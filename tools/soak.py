# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools\\soak.py - whole games through the real bridge, with soak_bot.py in my seat, so Karl can find
bugs overnight without playing (Round 28b).

    python tools\\soak.py --hours 8 [--games N] [--players 2..4] [--decks sample|mine] [--seed N] [--fault NAME] [--out DIR]
                           [--format commander|brawl]

Each game: soak_bot.SoakBot plays me, against 1 to 3 Forge AIs (the seat count rotates between 2, 3
and 4 unless --players fixes it); decks come from sample_decks/ by default (--decks mine also uses
my_decks/, read-only); a turn cap (60, all players' turns together) and a per-game time cap (30 minutes, --turn-cap / --game-timeout)
stop a game that never ends; every game writes a record file and its own engine log.

After each game, bridge_rules.check_stream looks at what was recorded. Any `fail` writes a bug-report
zip (reporting.build_report, plus the record file) named soak_<date>_<game>_<rule>.zip in the output
folder - never posted to Discord.

Output (the output folder; Karl reads it, there's no GUI):
  soak_summary.txt   games, turns, how each game ended, findings by rule, the path of each zip
  soak_shapes.json    the summed shapes, for tools\\coverage_report.py

Stops cleanly on Ctrl+C: the game in progress is closed properly (ForgeSession.close(), never
`pkill`) and what happened so far is still written.
"""
import argparse
import datetime
import glob
import json
import os
import random
import sys
import tempfile
import threading
import re
import time
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:            # `python tools\soak.py` puts tools\ on sys.path[0], not the project root
    sys.path.insert(0, BASE_DIR)        # where bridge_rules.py, card_check.py, forge_client.py etc. actually live
TURN_CAP = 60                    # round 28ba: 25 counted every seat's turns, so the bot got about 7 of its own and games rarely ended
GAME_TIMEOUT = 30 * 60           # seconds
STALL_SECONDS = 120
BOARD_WAIT_SECONDS = 60        # round 28c: longest wait for Forge to finish a mid-game board set-up (setup_done)
AI_STALL_SECONDS = 300          # round 28bc: while Forge asks me nothing (an AI is thinking); see run_game()
POLL_SLEEP = 0.02
MOVE_RESEND_SECONDS = 0.5        # unused since round 28bb (the bot is asked once per state; see run_game()); kept for old imports
MOVE_WAIT_SECONDS = 3.0          # round 28ba: after a click/ok, wait this long for Forge's new state before any other click
WATCHDOG_GRACE_SECONDS = 90      # last resort for a game that never even reaches its own timeout check - see run()

# forge_client (and crashlog, which reporting and card_check pull in) read MANTICORE_DATA_DIR exactly once, at import
# time, to decide where crash_log.txt, forge_engine.log, perf_log.txt and saves\ go. If this file imported them at the
# top like an ordinary module, they'd already be pinned to the real project folder before run() ever got a chance to
# point MANTICORE_DATA_DIR at this run's own output folder - the same leak Round 27a's tests/__init__.py fixed for the
# test suite. So they're imported lazily, by _load_modules(), only after run() has set the variable.
bridge_rules = card_check = deck_loader = fc = reporting = SoakBot = soak_report = soak_boards = None
CANARY_FAULT = "NO_COMMANDER_VARIANT"     # round 28ba: fires its check as the game starts, every time
CANARY_SECONDS = 150


def _load_modules():
    """Import forge_client and everything that pulls it in (card_check, reporting -> crashlog), after MANTICORE_DATA_DIR
    is already set to this run's output folder (see run()). A no-op once done: importing an already-loaded module doesn't
    re-run its top level, so calling this more than once in one process is harmless (and expected under the tests, where
    tests/__init__.py has usually already imported forge_client for its own reasons before any test calls run())."""
    global bridge_rules, card_check, deck_loader, fc, reporting, SoakBot, soak_report, soak_boards
    if fc is not None:
        return
    import soak_report as _soak_report
    soak_report = _soak_report
    import soak_boards as _soak_boards
    soak_boards = _soak_boards
    import bridge_rules as _bridge_rules
    import card_check as _card_check
    import deck_loader as _deck_loader
    import forge_client as _fc
    import reporting as _reporting
    from soak_bot import SoakBot as _SoakBot
    bridge_rules, card_check, deck_loader, fc, reporting, SoakBot = _bridge_rules, _card_check, _deck_loader, _fc, _reporting, _SoakBot


def default_out_dir():
    """<program folder>/soak_runs for a portable copy (Karl's git checkout), paths.local_dir()/soak for an installed one.

    Round 28bb: 28b's plan was paths.local_dir()/soak once Round 28 existed - but in a portable copy local_dir() IS the
    program folder, so runs would have moved to a new soak/ folder there that neither .gitignore nor backup.py skips
    (only soak_runs/ is), and the nightly backup would have committed and pushed megabytes of recordings."""
    try:
        import paths                       # Round 28
    except ImportError:
        return os.path.join(BASE_DIR, "soak_runs")
    if paths.is_portable():
        return os.path.join(BASE_DIR, "soak_runs")
    return os.path.join(paths.local_dir(), "soak")


def deck_pool(which, fmt="commander"):
    """.txt deck files to build games from: sample_decks/ always; --decks mine adds my_decks/ too
    (read-only: nothing here ever writes into it). Round FMT1: only decks of `fmt` (a file's "# format:" line, formats.py)."""
    import formats
    import paths                          # Round 29: through paths, so a frozen build finds its own sample_decks and the player's my_decks
    pool = sorted(glob.glob(os.path.join(paths.sample_dir(), "*.txt")))
    if which == "mine":
        pool += sorted(p for p in glob.glob(os.path.join(paths.library_dir(), "*.txt")) if os.path.isfile(p))
    return [p for p in pool if _file_format(p) == formats.normal(fmt)]


def _file_format(path):
    import formats
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return formats.from_text(f.read())
    except (OSError, UnicodeDecodeError):
        return formats.DEFAULT


def pick_decks(pool, n, rng):
    if not pool:
        raise RuntimeError("no deck files found to build a soak game from")
    if len(pool) >= n:
        return rng.sample(pool, n)
    return [rng.choice(pool) for _ in range(n)]           # a small pool: repeats are fine, it's only a soak test


def build_dck(path, tmp_dir, name, fmt=None):
    commanders, deck = deck_loader.load_deck(path)
    out = os.path.join(tmp_dir, name + ".dck")
    return fc.write_deck_file(out, commanders, deck, name, fmt=fmt)


class GameResult:
    def __init__(self, index, seed, players):
        self.index, self.seed, self.players = index, seed, players
        self.turns = 0
        self.ended = "?"                 # game_over | turn_cap | time_cap | stall | crash
        self.findings = []
        self.zip_path = None
        self.shapes = {}                 # (q, shape) -> count, this game's session.shapes
        self.decks = []                  # round 28ba: the deck files played, mine first
        self.record_path = None
        self.start = "turn 1"            # round 28c: "turn 1" or "board" (a mid-game board, soak_boards.py)

    def fail_rules(self):
        return sorted({f["rule"] for f in self.findings if f["severity"] == "fail"})

    def summary_line(self):
        rules = ", ".join(self.fail_rules()) or "-"
        return "game %d: seed=%s players=%d turns=%d ended=%s findings=%s%s" % (
            self.index, self.seed, self.players, self.turns, self.ended, rules,
            ("  -> " + self.zip_path) if self.zip_path else "")


# ---------------------------------------------------------------------------------------------
# one game
# ---------------------------------------------------------------------------------------------


def run_game(index, args, rng, out_root, watchdog_holder=None):
    players = args.players or rng.choice([2, 3, 4])
    seed = args.seed + index if args.seed is not None else fc.new_seed()
    fmt = getattr(args, "format", None) or "commander"          # round FMT1
    pool = deck_pool(args.decks, fmt)
    decks = pick_decks(pool, players, rng)
    game_dir = os.path.join(out_root, "game_%03d" % index)
    os.makedirs(game_dir, exist_ok=True)
    record_path = os.path.join(game_dir, "record.jsonl")
    result = GameResult(index, seed, players)
    result.decks = [os.path.basename(d) for d in decks]
    result.record_path = record_path
    bot = SoakBot(seed=seed)
    mem = {}
    # Round 28c: this share of games starts from a mid-game board (soak_boards.py). Decided here, before the session
    # starts, because it needs the bridge's developer mode for Forge's "Setup Game State".
    board = rng.random() < (getattr(args, "boards", 0.0) or 0.0)
    result.start = "board" if board else "turn 1"
    board_pending, board_wait_until, board_expect = board, None, None

    with tempfile.TemporaryDirectory(prefix="soak_decks_") as tmp:
        mine = build_dck(decks[0], tmp, "mine", fmt)
        opponents = [build_dck(d, tmp, "opp%d" % i, fmt) for i, d in enumerate(decks[1:], 1)]
        fc.sync_bridge()
        test_command = getattr(args, "session_command", None)     # tests only: a stand-in bridge instead of real Java
        session = fc.ForgeSession(mine, opponents, name="Soak", seed=seed, record_path=record_path,
                                  dev=bool(args.fault) or board, faults=[args.fault] if args.fault else (),
                                  command=test_command, fmt=fmt)
        session.stderr_path = os.path.join(game_dir, "forge_engine.log")
        if watchdog_holder is not None:
            watchdog_holder["session"] = session          # run()'s watchdog timer can now find this game's process
        try:
            session.start()
        except fc.ForgeUnavailable as e:
            result.ended = "crash"
            result.findings = [{"rule": "fatal", "severity": "fail", "at": -1, "detail": str(e)}]
            return result
        t0 = time.time()
        last_ask_key, last_ask_at = None, 0.0     # round 28bb: see "ask the bot at most ONCE per state" below
        last_progress_at = time.monotonic()
        last_state_version = session.state_version
        try:
            while True:
                session.poll()
                # Real progress is a new state snapshot, not merely bytes from Forge: a "dropped" ack of
                # our own resend arrives just as reliably whether the game is fine or the engine has died.
                # Found live: Forge threw an uncaught NullPointerException on a background thread during
                # game setup (a bad card in a deck reached GameAction.startGame with a null player) - the
                # bridge process stayed up and kept acking clicks with "dropped" forever, but the game
                # never advanced again. The old check measured "nothing from Forge for 120s" using
                # session.last_rx, which those dropped acks refresh every resend - so it never fired, and
                # the bot spun sending the same click for the rest of the game_timeout. It also only ran
                # when bot.next_action returned None, which it never did here (there was always another
                # "ok" to send) - so it needs to run every iteration, not just when the bot is idle.
                if session.state_version != last_state_version:
                    last_state_version = session.state_version
                    last_progress_at = time.monotonic()
                if session.game_over:
                    result.ended = "game_over"
                    break
                if session.fatal or session.exited:
                    result.ended = "crash"
                    break
                if time.time() - t0 > (args.game_timeout or GAME_TIMEOUT):
                    result.ended = "time_cap"
                    # Round SB1: a game that hits the time cap is often going round in a loop that keeps the state
                    # changing, so no stall rule fires (night 9's Grim Monolith loop, found by hand; the 2 Oct chain
                    # soak's Rhystic Study payment loop). A warning with the last question makes it visible in the summary.
                    st = session.state or {}
                    result.findings.append({"rule": "time_cap", "severity": "warn", "at": -1,
                                            "detail": "still going after %ds, turn %s, question %s; last prompt: %r" % (
                                                int(args.game_timeout or GAME_TIMEOUT), st.get("turn"), st.get("inputSeq"),
                                                card_check.prompt_key((st.get("prompt") or {}).get("message", ""))[:80])})
                    break
                turn = (session.state or {}).get("turn") or 0
                result.turns = max(result.turns, turn)
                if turn > (args.turn_cap or TURN_CAP):
                    result.ended = "turn_cap"
                    break
                ai_thinking = (session.state or {}).get("asking") is False
                limit = AI_STALL_SECONDS if ai_thinking else STALL_SECONDS
                if time.monotonic() - last_progress_at > limit:
                    msg = ((session.state or {}).get("prompt") or {}).get("message", "")
                    result.ended = "stall"
                    died = _game_thread_error(session) if ai_thinking else None
                    if died:
                        # Round 28bd: Forge's game thread threw and stopped - nobody is thinking, the game is dead. Night 3's
                        # two "ai_stall"s were this (a Cancel during a mana ability, see Main.java), not a slow AI.
                        rule = "ai_timeout_race" if _ai_timeout_before_death(session) else "game_thread_died"
                        result.findings.append({"rule": rule, "severity": "fail", "at": -1,
                                                "detail": "Forge's game thread stopped after %s; last prompt %r, turn %s" % (
                                                    died, msg[:80], (session.state or {}).get("turn"))})
                    elif ai_thinking:
                        # Round 28bc: Forge was not asking me anything - an AI was thinking (soak night 2, game 247: a
                        # 4-player game on turn 35, a huge elf-token board, "Waiting for AI 1 (opp1)..." for over 120 s).
                        # Its own rule, so it's told apart from my seat being stuck, and it gets longer before it counts.
                        result.findings.append({"rule": "ai_stall", "severity": "fail", "at": -1,
                                                "detail": "an AI thought for over %ds (Forge asked me nothing); %r, turn %s" % (
                                                    AI_STALL_SECONDS, msg[:80], (session.state or {}).get("turn"))})
                    else:
                        result.findings.append({"rule": "stall", "severity": "fail", "at": -1,
                                                "detail": "no new state from Forge for %ds (dropped acks don't count as progress); last prompt: %r" % (STALL_SECONDS, msg[:200])})
                    break
                while session.infos:                  # round 28bb: Forge's messages ("X must block ...") - the bot reads the latest
                    info = dict(session.infos.popleft())
                    info["_seq"] = (session.state or {}).get("inputSeq")     # which question it was said during
                    mem.setdefault("infos", []).append(info)
                    del mem["infos"][:-20]
                # Round 28bb: ask the bot at most ONCE per question as it stands (_question_key: the question number, its
                # text and buttons, the cards marked on it, the pending requests), and again only after MOVE_WAIT_SECONDS if
                # none of that has changed. NOT once per snapshot: while the AIs act Forge sends many snapshots for one
                # question, and answering each (the same OK again) got 170-430 clicks per game dropped in 28bb's first
                # sandbox soak. Asking has side effects: card_check.next_move
                # marks a card as clicked when it proposes it, and counts how often it has seen a prompt. Round 28ba asked on
                # every poll (every 20 ms) and only then decided whether to wait, so:
                #  - while a first click was on its way the bot "used up" the other cards of a pick-two or discard-six
                #    question (soak night 1: Sylvan Library x4, Butcher of Malakir x2, Ruthless Winnower x2, the cleanup
                #    discard x2 - 10 of the 16 stalls);
                #  - during the half-second resend cooldown a plain "OK" prompt was counted as seen 25 times, past the
                #    3-times limit, and the bot went quiet ("Return 0 card(s) to the bottom of your library", night 0's
                #    game 140, and again in 28bb's first sandbox soak).
                # This also keeps round 28ba's rule of one click per question: after a click the state is unchanged until
                # Forge has handled it, so the bot isn't asked again until then (or until MOVE_WAIT_SECONDS have passed).
                if board_pending and (session.state or {}).get("asking") and not session.requests and \
                        ((session.state or {}).get("prompt") or {}).get("message", "").startswith("Priority:"):
                    # Round 28c: the first time I'm asked to act (mulligans and "who starts" are over), set up the board.
                    board_pending = False
                    try:
                        lines, _placed = soak_boards.board_lines(decks, random.Random(seed), fmt=fmt)
                        board_expect = getattr(session, "setups_done", 0) + 1
                        session.setup(lines)
                        board_wait_until = time.monotonic() + BOARD_WAIT_SECONDS
                    except Exception as e:            # a deck the builder can't read: play the game from turn 1 instead
                        result.start = "turn 1 (board failed: %s)" % str(e)[:60]
                if board_wait_until is not None:
                    # Forge applies a set-up on its own thread; passing priority before it finishes lets the game loop run
                    # beside it and kills it (ConcurrentModificationException: 28c's first board game, after a shock land
                    # in the new board asked "pay 2 life?"). Until the bridge says "setup_done", only questions the set-up
                    # itself asks are answered - never the old priority prompt. BOARD_WAIT_SECONDS at most.
                    msg_now = ((session.state or {}).get("prompt") or {}).get("message", "")
                    done = getattr(session, "setups_done", 0) >= board_expect
                    if not done and time.monotonic() < board_wait_until:
                        if not session.requests and msg_now.startswith("Priority:"):
                            time.sleep(POLL_SLEEP)
                            continue
                    else:
                        board_wait_until = None
                if (session.state or {}).get("asking") is False and not session.requests:
                    # Round 28bb: Forge is asking nothing right now (between two questions, or an AI is thinking). The
                    # snapshot can still show the last question's text and an enabled OK, so the bot would answer it again
                    # and the bridge would drop the click: about 100 dropped clicks a game in 28bb's second sandbox soak.
                    # Not "input" == "": that is also what an anonymous Input class (the cleanup discard) reports - the
                    # third sandbox soak stalled on exactly that. The bridge's own "asking" flag (new in 28bb) is exact.
                    time.sleep(POLL_SLEEP)
                    continue
                ask_key = _question_key(session.state, session.requests)
                if ask_key == last_ask_key and time.monotonic() - last_ask_at < MOVE_WAIT_SECONDS:
                    time.sleep(POLL_SLEEP)
                    continue
                last_ask_key, last_ask_at = ask_key, time.monotonic()
                move = bot.next_action(session.state, list(session.requests), mem)
                if move is None:
                    time.sleep(POLL_SLEEP)
                    continue
                _do(session, move)
        finally:
            result.shapes = dict(session.shapes)
            session.close()

        _finish_report(result, session, record_path, game_dir, out_root)
        result.record_path = _compress_record(record_path)
    return result


def _compress_record(path):
    """Round 28d fix 1: a game's record.jsonl is 10-40 MB (every snapshot); a night of them was about 6 GB, and Karl's
    soak_runs reached 42 GB with 33 GB left on the disk. Once the game is judged (and any bug-report zip holds its own copy)
    the record is kept gzipped - about a tenth of the size - and bridge_rules.load_stream reads .jsonl.gz as before.
    Returns the path to use from now on; on any error the plain file stays."""
    import gzip
    import shutil
    if not path or not os.path.isfile(path):
        return path
    gz = path + ".gz"
    try:
        with open(path, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.remove(path)
        return gz
    except OSError:
        try:
            if os.path.isfile(gz) and os.path.isfile(path):
                os.remove(gz)
        except OSError:
            pass
        return path


_GAME_THREAD_ERROR = re.compile(r"^Game-\d+ > (\S+)")


def _game_thread_error(session):
    """The first exception Forge's game thread threw in this game's engine log ("Game-0 > java.util.Concurrent..."), or
    None. Such a line means the game loop itself stopped."""
    path = getattr(session, "stderr_path", None)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _GAME_THREAD_ERROR.match(line)
                if m:
                    return m.group(1).split(".")[-1].rstrip(":")
    except (OSError, TypeError):
        pass
    return None


def _ai_timeout_before_death(session):
    """Round 28d: True when this game's engine log shows an abandoned AI think ("AI eval thread at timeout:") BEFORE the game
    thread's exception. Soak night 4 game 3: on Java 20+ Forge can't stop a timed-out AI eval thread, which then races the
    game thread over the same cards (see Main.AI_TIMEOUT_SECONDS). Reported as its own kind, ai_timeout_race, so it's known
    at a glance and counted apart from game-thread deaths with other causes."""
    path = getattr(session, "stderr_path", None)
    seen_timeout = False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "AI eval thread at timeout" in line:
                    seen_timeout = True
                elif _GAME_THREAD_ERROR.match(line):
                    return seen_timeout
    except (OSError, TypeError):
        pass
    return False


def _question_key(state, requests):
    """What the bot's answer depends on: which question (inputSeq and Input class), its text and buttons, the cards marked
    selectable / highlighted (picked so far) / playable ("weak") in my zones, the stack's size, and the pending requests.
    Life totals, other players' boards and the log are left out, so a snapshot that only reports what an AI did doesn't
    make the bot answer the same question again."""
    st = state or {}
    p = st.get("prompt") or {}
    ok, cancel = p.get("ok") or {}, p.get("cancel") or {}
    marked = []
    for pl in st.get("players") or []:
        if pl.get("selectable") or pl.get("highlight"):              # round 28d: "choose a player" questions
            marked.append(("player", pl.get("id"), bool(pl.get("selectable")), bool(pl.get("highlight"))))
        for zone, cards in (pl.get("zones") or {}).items():
            for c in cards or []:
                if c.get("selectable") or c.get("highlight") or c.get("weak"):
                    marked.append((c.get("id"), bool(c.get("selectable")), bool(c.get("highlight")), c.get("weak") or 0, zone))
    return (st.get("inputSeq"), st.get("input"), p.get("message"), bool(ok.get("enabled")), bool(cancel.get("enabled")),
            tuple(sorted(marked, key=repr)), len(st.get("stack") or []), st.get("phase"),
            tuple(r.get("id") for r in requests or []))


def _do(session, move):
    kind = move[0]
    if kind == "answer":
        session.answer(move[1], move[2])
    elif kind == "click":
        session.click_card(move[1])
    elif kind == "player":
        session.click_player(move[1])
    elif kind == "ok":
        session.ok()
    elif kind == "cancel":
        session.cancel()


def _watchdog_kill(holder):
    """Last resort for a game that stops making forward progress for any reason at all - even one none of
    run_game()'s own checks catch. Fires from a background timer well past the per-game time cap (see run()),
    so it should never trigger on an ordinary, if slow, game. Force-kills that game's Forge process; the
    reader thread then sees EOF, run_game()'s loop sees session.exited on its next poll, and the game ends as
    a crash instead of the whole run (and, on 2026-09-26, the whole machine) needing to be restarted by hand.
    This can only reach a game that got as far as starting its ForgeSession - a hang earlier than that (deck
    loading, sync_bridge, ...) isn't something a kill of the Forge process can unstick."""
    session = holder.get("session")
    if session is None:
        return
    try:
        if session.alive():
            session.proc.kill()
    except Exception:
        pass


RECORD_JUDGE_LIMIT = 400 * 1024 * 1024   # patch 44: bytes of record (uncompressed) the rules and the summary still read


def _too_big(path):
    """Patch 44: a record so big that reading it back would take gigabytes of memory - a game that went round in a loop (6 Oct:
    a 1.9 GB record, 28,000 refused attacks; reading it killed the soak). A .gz counts about ten times its size."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    size = size * 10 if path.endswith(".gz") else size
    return size if size > RECORD_JUDGE_LIMIT else 0


def _load_messages(path):
    """The record's messages, or None when it is too big to read back."""
    if not path or _too_big(path):
        return None
    try:
        return bridge_rules.load_stream(path)
    except OSError:
        return []


def _note_java_crash(result, session):
    """Patch 46 (soak night 14, game 75): when Java itself crashed, the game's "fatal" finding says so - what and where, and
    the crash report's name (it is in the game's folder: forge_client writes it beside forge_engine.log). Returns its path or
    None. Night 14's only clue was the bare "the stream ended (_exit) ..." and a file nobody looked at in forge_runtime/."""
    try:
        crash = session.crash_report() if hasattr(session, "crash_report") else None
    except Exception:
        crash = None
    if not isinstance(crash, str) or not os.path.isfile(crash):      # a path, or nothing (a stand-in session gives neither)
        return None
    import java_crash
    note = "Java itself crashed: %s (%s)" % (java_crash.summary(crash), os.path.basename(crash))
    fatal = [f for f in result.findings if f.get("rule") == "fatal"]
    if fatal:
        fatal[0]["detail"] = "%s - %s" % (fatal[0]["detail"], note)
    else:
        result.findings.append({"rule": "fatal", "severity": "fail", "at": -1, "detail": note})
    return crash


def _finish_report(result, session, record_path, game_dir, out_root):
    """bridge_rules over what was recorded, and a bug-report zip when a rule failed."""
    messages = _load_messages(record_path)
    if messages is None:
        result.findings.append({"rule": "huge_record", "severity": "warn", "at": -1,
                                "detail": "record of %d MB not read back (a loop?)" % (_too_big(record_path) // (1024 * 1024))})
        messages = []
    engine_lines = bridge_rules.load_engine_log(session.stderr_path)
    result.findings = result.findings + bridge_rules.check_stream(messages, engine_lines)
    crash = _note_java_crash(result, session)            # patch 46
    if not bridge_rules.has_fail(result.findings):
        return
    info = {"name": "soak", "happened": "; ".join("%s: %s" % (f["rule"], f["detail"]) for f in result.findings if f["severity"] == "fail")[:1400],
           "expected": "no bridge_check / commander_stranded / unanswered_request / fatal / engine_error / stall findings", "seed": result.seed}
    path = reporting.build_report(info, state=session.state, log_lines=[e.get("text", "") for e in session.log],
                                  commands=session.sent_all, screenshot=None, folder=game_dir, dest_dir=out_root,
                                  extra_files=reporting.java_crash_files(crash))
    rule_tag = "+".join(result.fail_rules()) or "fail"
    date_tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    wanted = os.path.join(out_root, "soak_%s_%d_%s.zip" % (date_tag, result.index, rule_tag))
    n = 1
    while os.path.exists(wanted):
        n += 1
        wanted = os.path.join(out_root, "soak_%s_%d_%s_%d.zip" % (date_tag, result.index, rule_tag, n))
    os.replace(path, wanted)
    try:
        with zipfile.ZipFile(wanted, "a", zipfile.ZIP_DEFLATED) as z:
            if os.path.isfile(record_path):
                z.write(record_path, "record.jsonl")
    except OSError:
        pass
    result.zip_path = wanted


# ---------------------------------------------------------------------------------------------
# the whole run
# ---------------------------------------------------------------------------------------------


def run_canary(args, out_root):
    """Round 28ba: one short game with a deliberate, known bridge fault that fires at the start of every game. If the
    run does not report it, the checks (or the plumbing that reports them) are broken, and "0 failures" means nothing.
    Returns (ok, detail)."""
    canary_dir = os.path.join(out_root, "canary")
    os.makedirs(canary_dir, exist_ok=True)
    pool = deck_pool("sample")
    rng = random.Random(0)
    with tempfile.TemporaryDirectory(prefix="soak_canary_") as tmp:
        try:
            decks = pick_decks(pool, 2, rng)
            mine = build_dck(decks[0], tmp, "mine")
            opp = build_dck(decks[1], tmp, "opp1")
        except Exception as e:                      # a deck that won't load is not the canary's question
            return False, "could not build the canary's decks: %s" % e
        fc.sync_bridge()
        session = fc.ForgeSession(mine, [opp], name="Soak", seed=1, record_path=os.path.join(canary_dir, "record.jsonl"),
                                  dev=True, faults=[CANARY_FAULT], command=getattr(args, "canary_command", None))
        session.stderr_path = os.path.join(canary_dir, "forge_engine.log")
        try:
            session.start()
        except fc.ForgeUnavailable as e:
            return False, "Forge did not start: %s" % e
        try:
            end = time.time() + CANARY_SECONDS
            while time.time() < end:
                session.poll()
                if any(c.get("rule") == "commander_variant_missing" for c in session.checks):
                    return True, "start/commander_variant_missing reported"
                if session.fatal or session.exited:
                    return False, "Forge stopped before the check arrived"
                time.sleep(0.1)
            return False, "no check within %d s" % CANARY_SECONDS
        finally:
            session.close()


def _header(args, started, results):
    try:
        import version
        program = version.describe()
        forge = version.forge_build() or "unknown"               # round 28d (F1)
    except Exception as e:
        program = forge = "unknown (%s)" % e
    decks = sorted({d for r in results for d in r.decks})
    return ["Program:  " + program,
            "Forge:    " + forge,
            "Started:  " + started, "Finished: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Options:  games=%s hours=%s players=%s decks=%s seed=%s fault=%s turn-cap=%s game-timeout=%ss boards=%s%s" % (
                args.games, args.hours, args.players or "2-4", args.decks, args.seed, args.fault,
                getattr(args, "turn_cap", None), getattr(args, "game_timeout", None), getattr(args, "boards", 0.0) or 0,
                "" if (getattr(args, "format", None) or "commander") == "commander" else " format=" + args.format),
            "Decks:    " + (", ".join(decks) or "-")]


def run(args):
    history_dir = getattr(args, "history", None) or args.out or default_out_dir()   # round 28c: nightly.py keeps the history folder
    if args.out:
        out_root = args.out
    else:                                   # round 28ba: each run in its own folder, so runs no longer overwrite each other
        out_root = os.path.join(history_dir, "run_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_root, exist_ok=True)
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    os.environ["MANTICORE_DATA_DIR"] = out_root      # before _load_modules(): see the comment above it
    _load_modules()
    canary = run_canary(args, out_root) if getattr(args, "canary", False) else None
    if canary is not None:
        print("canary: %s (%s)" % ("OK" if canary[0] else "FAILED", canary[1]), flush=True)
    rng = random.Random(args.seed)
    deadline = time.time() + args.hours * 3600 if args.hours else None
    results = []
    shapes = {}
    index = 0
    try:
        while (args.games is None or index < args.games) and (deadline is None or time.time() < deadline):
            watchdog_holder = {}
            watchdog_seconds = (args.game_timeout or GAME_TIMEOUT) + WATCHDOG_GRACE_SECONDS
            watchdog = threading.Timer(watchdog_seconds, _watchdog_kill, args=(watchdog_holder,))
            watchdog.daemon = True
            watchdog.start()
            try:
                result = run_game(index, args, rng, out_root, watchdog_holder=watchdog_holder)
            finally:
                watchdog.cancel()
            results.append(result)
            for (q, shape), n in result.shapes.items():
                key = "%s|%s" % (q, shape)
                shapes[key] = shapes.get(key, 0) + n
            print(result.summary_line(), flush=True)
            index += 1
    except KeyboardInterrupt:
        print("\nstopping (Ctrl+C): closing the game in progress and writing what happened so far.")
    write_outputs(out_root, results, shapes, header=_header(args, started, results), canary=canary, history_dir=history_dir)
    return 1 if any(r.fail_rules() for r in results) else 0


def write_outputs(out_root, results, shapes, header=None, canary=None, history_dir=None):
    """soak_summary.txt (round 28ba: VALID/INVALID, the canary, problems grouped by signature with what's new since the
    last run, warnings, and what the bot did in each game) and soak_shapes.json. With history_dir (the folder that holds
    every run), a copy of the summary goes there too as soak_summary.txt, and known_signatures.json is kept there."""
    _load_modules()
    healths = []
    for r in results:
        messages = (_load_messages(r.record_path) or []) if r.record_path else []        # patch 44: never a huge one
        health = soak_report.game_health(messages)
        health["board"] = getattr(r, "start", "turn 1") == "board"      # round 28c: see soak_report.run_verdict
        healths.append(health)
    verdict = soak_report.run_verdict(healths, canary)
    groups = soak_report.group_findings(results)
    known_path = os.path.join(history_dir or out_root, "known_signatures.json")
    known = soak_report.load_known(known_path)
    new = {s for s in groups if s not in known}
    text = soak_report.summary_text(header or [], results, healths, verdict, canary, groups, new)
    with open(os.path.join(out_root, "soak_summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    if history_dir and os.path.abspath(history_dir) != os.path.abspath(out_root):
        with open(os.path.join(history_dir, "soak_summary.txt"), "w", encoding="utf-8") as f:
            f.write("(the latest run: %s)\n\n" % os.path.basename(out_root) + text)
    soak_report.save_known(known_path, known | set(groups))
    shapes_path = os.path.join(out_root, "soak_shapes.json")
    with open(shapes_path, "w", encoding="utf-8") as f:
        json.dump(shapes, f, indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Play whole games through the bridge overnight, with soak_bot.SoakBot in my seat.")
    ap.add_argument("--games", type=int, default=None,
                    help="stop after this many games (default: 1, or no limit when --hours is given)")
    ap.add_argument("--hours", type=float, default=None, help="stop after this many hours, whichever limit is hit first")
    ap.add_argument("--players", type=int, choices=(2, 3, 4), default=None, help="fix the seat count (default: rotates 2, 3, 4)")
    ap.add_argument("--decks", choices=("sample", "mine"), default="sample")
    ap.add_argument("--seed", type=int, default=None, help="base seed; game N uses seed+N (default: a fresh seed each game)")
    ap.add_argument("--fault", default=None, help="a Checks.java Fault name (--dev), for every game this run")
    ap.add_argument("--out", default=None, help="output folder (default: paths.local_dir()/soak, or soak_runs/ next to the program)")
    ap.add_argument("--turn-cap", type=int, default=TURN_CAP, dest="turn_cap")
    ap.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT, dest="game_timeout", help="per-game time cap, in seconds")
    ap.add_argument("--boards", type=float, default=0.0,
                    help="share of games that start from a mid-game board, 0..1 (round 28c; tools\\nightly.py uses 0.5)")
    ap.add_argument("--format", choices=("commander", "brawl"), default="commander",
                    help="round FMT1: the games' format; decks are the sample (or my_decks) files of that format")
    ap.add_argument("--no-canary", action="store_false", dest="canary",
                    help="skip the canary game (a deliberate fault that must be reported; round 28ba)")
    args = ap.parse_args(argv)
    if args.games is None and not args.hours:
        args.games = 1           # round 28bb: "--hours 8" alone used to mean 8 hours OR 1 game, whichever came first
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
