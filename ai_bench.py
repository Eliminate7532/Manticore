# SPDX-License-Identifier: GPL-3.0-or-later
"""
ai_bench.py - does the AI answer the right threat? Ten fixed boards, each a yes / no, played through the real Forge engine with the
card checker's machinery (card_check.Checker: Forge's "Setup Game State", a scripted player who takes the first legal answer).

    python tools/ai_bench.py                      10 boards x 5 trials, about 4 minutes
    python tools/ai_bench.py --trials 10 --seed 11 --boards C1,C2,R2

Your side holds only cards from the Kinnan list (tests/fixtures/decks/kinnan_nbc_moxfield_export.txt); the AI's side holds plain
interaction (Counterspell, Force of Will, Swords to Plowshares, Shatter) and the lands to cast it. Each trial: the board is set up
(Forge's Setup Game State, always as turn 5), a scripted Karl makes the same moves every time, the turn is played out to the next
one, the AI's own stack entries are kept as evidence ("Swords to Plowshares (279): Exile target creature. [targets: Kinnan, Bonder
Prodigy (415)]"), and the verdict is a plain yes / no against what a good player would do.

When removal counts: on boards played on the AI's turn (R1-R3), removal counts only if your card is already gone when your turn
begins - from your upkeep on you can start the Basalt Monolith loop in response - so the board is read at the first question of
your turn (a Swords cast in your upkeep is still on the stack then, and counts as too late). On your own turn (C1-C4, F1, F2, I1)
the AI gets every priority of the turn - your main phase, combat, your end step - and the board is read when its turn begins.

Rebuilt 7 Oct 2026 (Claude, Fable) from claude/ROUND_AI1_2026-10-02.md: the 3 Oct build was only ever posted in a chat and never saved.
Same boards, same scripted moves, same rules for the verdicts; the code is new. Never imported by the game.
"""
import json
import os
import re
import time

import card_check as cc

KINNAN_DECK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")
MY_LANDS = ["Forest", "Forest", "Island", "Island", "Island", "Island"]        # six: Basalt Monolith (3) with Kinnan's tax covered
AI_BLUE = ["Island"] * 5                                                     # Force of Will's {3}{U}{U} hard cast
AI_WHITE = ["Plains"] * 2
AI_RED = ["Mountain"] * 2
COUNTERS = ("Counterspell", "Force of Will")
REMOVAL = ("Swords to Plowshares", "Shatter")
SETUP_TURN = 5              # every board is set up as turn 5; the trial ends when Forge's prompt says turn 6 has begun
TURN_WAIT = 90.0            # seconds to let a turn play out before the trial is an error
TRIAL_BUDGET = 40.0         # seconds for one scripted play
TURN_LINE = re.compile(r"Turn:\s*(\d+)\s*\((.*)\)")


class Board:
    __slots__ = ("key", "title", "right", "mine_bf", "mine_hand", "ai_bf", "ai_hand", "active", "plays", "judge", "why")

    def __init__(self, key, title, right, mine_bf, mine_hand, ai_bf, ai_hand, active, plays, judge, why=""):
        self.key, self.title, self.right = key, title, right
        self.mine_bf, self.mine_hand, self.ai_bf, self.ai_hand = list(mine_bf), list(mine_hand), list(ai_bf), list(ai_hand)
        self.active, self.plays, self.judge, self.why = active, list(plays), judge, why

    def setup_lines(self):
        """Forge's Setup Game State lines, the card checker's way. Every zone is written, the empty ones too: a zone left out keeps
        what the last trial left in it (seen live: the exiled Kinnan of one trial still in exile at the next). The deck's real
        commander stays in the command zone untouched; the Kinnan these boards put out is a fresh card, not the commander."""
        return ["humanlife=40", "ailife=40", "activeplayer=" + self.active, "activephase=MAIN1", "turn=%d" % SETUP_TURN,
                "humanlandsplayed=0", "humanhand=" + ";".join(self.mine_hand), "humanbattlefield=" + ";".join(self.mine_bf + MY_LANDS),
                "humanlibrary=" + ";".join(cc.LIBRARY), "humangraveyard=", "humanexile=",
                "aihand=" + ";".join(self.ai_hand), "ailibrary=" + ";".join(cc.LIBRARY), "aibattlefield=" + ";".join(self.ai_bf),
                "aigraveyard=", "aiexile=", "removesummoningsickness=true"]

    def my_cards(self):
        return set(self.mine_bf) | set(self.mine_hand)


class Trial:
    """One run of one board: what the AI did (its stack entries as Forge words them), where my cards ended, the verdict."""

    def __init__(self, board, index):
        self.board, self.index = board.key, index
        self.ai_entries = []            # "Counterspell (230): ... [targets: Basalt Monolith (203)]" in the order first seen
        self.my_entries = []
        self.ai_cast = []               # names the AI cast (from the "cast" events; triggers and abilities are not casts)
        self.zones = {}                 # my card name -> where it ended ("my battlefield", "my graveyard", "my exile", ...)
        self.at_my_turn = None          # R1-R3: my battlefield at the first question of my turn
        self.stack_end = []             # what was still on the stack when the board was read (a Swords cast in my upkeep, say)
        self.verdict = None             # True / False / None (the trial could not be judged)
        self.error = ""
        self.seconds = 0.0

    def as_dict(self):
        return {"board": self.board, "index": self.index, "verdict": self.verdict, "ai_entries": self.ai_entries,
                "ai_cast": self.ai_cast, "zones": self.zones, "at_my_turn": self.at_my_turn, "stack_end": self.stack_end,
                "error": self.error, "seconds": round(self.seconds, 1)}


# ---- the verdicts (pure functions of a Trial, so they are tested without Forge) ----------------------------------------------

def countered(name):
    """The AI countered my spell: it cast a counter while the spell was on the stack, and the spell ended in my graveyard."""
    def judge(t):
        return any(c in COUNTERS for c in t.ai_cast) and t.zones.get(name) == "my graveyard"
    return judge


def resolved(name):
    """My spell resolved: it is on my battlefield at the end (nothing the AI did stopped it)."""
    def judge(t):
        return t.zones.get(name) == "my battlefield"
    return judge


def both(*judges):
    def judge(t):
        return all(j(t) for j in judges)
    return judge


def gone_by_my_turn(name, keep=None):
    """The AI's turn: `name` is no longer on my battlefield when my turn begins (and `keep`, if given, still is)."""
    def judge(t):
        if t.at_my_turn is None:
            return None
        if name in t.at_my_turn:
            return False
        return keep is None or keep in t.at_my_turn
    return judge


def exiled_this_turn(name):
    """My turn: the AI answered the creature I cast with its removal before its own turn began (the card is off my battlefield)."""
    def judge(t):
        return any(c in REMOVAL for c in t.ai_cast) and t.zones.get(name) != "my battlefield"
    return judge


KINNAN = "Kinnan, Bonder Prodigy"
BASALT = "Basalt Monolith"
SIGNET = "Arcane Signet"
PETAL = "Lotus Petal"
SPHINX = "Consecrated Sphinx"
SOL_RING = "Sol Ring"

BOARDS = [
    Board("C1", "Kinnan out, you cast Basalt Monolith; AI holds Counterspell", "counter Basalt",
          [KINNAN], [BASALT], AI_BLUE, ["Counterspell"], "human", [BASALT], countered(BASALT)),
    Board("C2", "Basalt out, you cast Kinnan", "counter Kinnan",
          [BASALT], [KINNAN], AI_BLUE, ["Counterspell"], "human", [KINNAN], countered(KINNAN)),
    Board("C3", "Nothing going on, you cast Arcane Signet", "let it resolve",
          [], [SIGNET], AI_BLUE, ["Counterspell"], "human", [SIGNET], resolved(SIGNET)),
    Board("C4", "Kinnan out: Arcane Signet, then Basalt", "let Signet resolve, counter Basalt",
          [KINNAN], [SIGNET, BASALT], AI_BLUE, ["Counterspell"], "human", [SIGNET, BASALT], both(resolved(SIGNET), countered(BASALT))),
    Board("F1", "Kinnan out, Basalt; AI has only Force of Will", "Force of Will on Basalt",
          [KINNAN], [BASALT], AI_BLUE, ["Force of Will"], "human", [BASALT], countered(BASALT)),
    Board("F2", "Kinnan out: Lotus Petal, then Basalt; only Force of Will", "save it for Basalt",
          [KINNAN], [PETAL, BASALT], AI_BLUE, ["Force of Will"], "human", [PETAL, BASALT], both(resolved(PETAL), countered(BASALT))),
    Board("R1", "AI's turn; you have Kinnan + Basalt; AI has Swords", "exile Kinnan before your turn",
          [KINNAN, BASALT], [], AI_WHITE, ["Swords to Plowshares"], "ai", [], gone_by_my_turn(KINNAN)),
    Board("R2", "AI's turn; you have Kinnan + Basalt + Consecrated Sphinx", "exile Kinnan, not the Sphinx",
          [KINNAN, BASALT, SPHINX], [], AI_WHITE, ["Swords to Plowshares"], "ai", [], gone_by_my_turn(KINNAN, keep=SPHINX)),
    Board("R3", "AI's turn; you have Kinnan + Basalt + Sol Ring; AI has Shatter", "Shatter Basalt",
          [KINNAN, BASALT, SOL_RING], [], AI_RED, ["Shatter"], "ai", [], gone_by_my_turn(BASALT)),
    Board("I1", "Basalt out, you cast Kinnan; AI holds Swords", "exile Kinnan on your turn",
          [BASALT], [KINNAN], AI_WHITE, ["Swords to Plowshares"], "human", [KINNAN], exiled_this_turn(KINNAN)),
]
BOARD_BY_KEY = {b.key: b for b in BOARDS}


def _entry_text(item, names=None, players=None):
    """A stack entry as evidence: "Counterspell (230): Counter target spell. [targets: Basalt Monolith (203)]"."""
    c = item.get("card") or {}
    text = item.get("key") or item.get("text") or ""
    head = "%s (%s)" % (c.get("name", "?"), c.get("id", "?"))
    if text and not text.startswith(head):
        text = head + ": " + text
    text = text or head
    targets = []
    for tg in item.get("targets") or []:
        tg = str(tg)
        if tg.startswith("c") and tg[1:].isdigit():
            cid = int(tg[1:])
            targets.append("%s (%d)" % ((names or {}).get(cid, "card"), cid))
        elif tg.startswith("p") and tg[1:].isdigit():
            targets.append((players or {}).get(int(tg[1:]), "player " + tg[1:]))
        else:
            targets.append(tg)
    if targets:
        text += " [targets: " + ", ".join(targets) + "]"
    return text


def prompt_turn(state):
    """(turn number, whose turn) from Forge's own prompt text ("Priority: Checker / Turn: 5 (AI 1 (Filler)) / Phase: ..."), or None.
    Only a question Forge is asking now counts ("asking" not false): right after a set-up the snapshot can still carry the question
    from before it. The snapshot's own "turn" and "activePlayer" fields are not used: after Setup Game State they kept the old turn
    number until the next turn began (seen live, 7 Oct), so a trial could not tell the set-up's turn 5 from the turn before."""
    if not state or state.get("asking") is False:
        return None
    m = TURN_LINE.search(((state.get("prompt") or {}).get("message")) or "")
    return (int(m.group(1)), m.group(2)) if m else None


class Watcher:
    """Wraps the session's poll() for one trial: notes every stack entry as it appears, the AI's casts, the turn as Forge's prompts
    report it, and my board at the first question of my next turn (R1-R3). The AI's hand is hidden, so a cast event names only a
    card id; the name is looked up from the stack entry (or any zone that later shows it) when the trial ends (`finish`)."""

    def __init__(self, session, trial, my_id, ai_id, my_name):
        self.s, self.t, self.me, self.ai, self.my_name = session, trial, my_id, ai_id, my_name
        self.seen = set()
        self.names = {}                 # card id -> name, from every stack entry and zone seen during the trial
        self.cast_ids = []              # the AI's casts, as the events name them, in order
        self.settled = False            # a question asked on the set-up's own turn has been seen (what came before it is stale)
        self.next_turn = None           # (turn, whose) once a question on a later turn is asked
        self.last_seq = max([ev.get("seq") or 0 for ev, _rx in list(session.events)] or [0])   # events from before this trial are not its
        self.orig = session.poll

    def __enter__(self):
        self.s.poll = self.poll
        return self

    def __exit__(self, *exc):
        self.s.poll = self.orig
        self.finish()
        return False

    def poll(self, *a, **k):
        r = self.orig(*a, **k)
        st = self.s.state or {}
        for c, _zone, _pid in cc.all_cards(st):
            if c.get("id") is not None and c.get("name"):
                self.names[c["id"]] = c["name"]
        players = {p["id"]: ("me" if p["id"] == self.me else "the AI") for p in st.get("players", [])}
        for item in st.get("stack") or []:
            c = item.get("card") or {}
            key = (c.get("id"), c.get("name"), item.get("activator"))
            if key in self.seen:
                continue
            self.seen.add(key)
            (self.t.ai_entries if item.get("activator") == self.ai or c.get("controller") == self.ai else self.t.my_entries).append(
                _entry_text(item, self.names, players))
        turn = prompt_turn(st)
        if turn and turn[0] == SETUP_TURN:
            self.settled = True
        elif turn and self.settled and turn[0] > SETUP_TURN and self.next_turn is None:
            self.next_turn = turn
            if turn[1] == self.my_name and self.t.at_my_turn is None:
                me = next((p for p in st.get("players", []) if p["id"] == self.me), None)
                self.t.at_my_turn = [c.get("name") for c in (me or {}).get("zones", {}).get("battlefield", [])]
        for ev, _rx in list(self.s.events):
            seq = ev.get("seq") or 0
            if seq <= self.last_seq:
                continue
            self.last_seq = seq
            if ev.get("kind") == "cast" and ev.get("player") == self.ai and not ev.get("trigger") and not ev.get("ability"):
                self.cast_ids.append(ev.get("card"))
        return r

    def finish(self):
        self.poll()
        self.t.ai_cast = [self.names.get(cid) or ((self.s.card(cid) or {}).get("name")) or ("card %s" % cid) for cid in self.cast_ids]


def where(chk, name):
    """The one zone my card ended in. Forge's command zone carries a card named after each commander for as long as the game lasts
    (seen live: Kinnan on the battlefield shows as "my battlefield, my command, opponent's command" - the filler deck's commander is
    Kinnan too), so the command zones are left out unless nothing else holds the card (the commander went back there)."""
    zones = sorted(set(chk.where_is(name)))
    real = [z for z in zones if not z.endswith(" command")]
    if not real:
        real = [z for z in zones if z == "my command"]
    return real[0] if len(real) == 1 else (", ".join(real) if real else "")


def pass_the_turn(chk, watcher, seconds=TURN_WAIT):
    """Pass priority (and answer whatever is asked on the way, as the scripted player does) until the next turn begins: the
    AI's after mine, mine after the AI's. Returns an error text, or "" when Forge's prompt says the next turn has begun."""
    s = chk.s
    end = time.time() + seconds
    mem = {}
    while time.time() < end:
        s.poll()
        if s.exited or s.fatal:
            return "the engine stopped: " + (s.fatal or "exited")
        st = s.state or {}
        if watcher.next_turn is not None:
            chk.pump(0.5)
            return ""
        move = cc.next_move(st, list(s.requests), mem)
        prompt = st.get("prompt") or {}
        if move is None and prompt.get("message", "").startswith("Priority:") and (prompt.get("ok") or {}).get("enabled"):
            move = ("ok",)                                   # a plain priority with nothing on the stack: next_move leaves it alone
        if move is not None:
            version = s.state_version
            chk.do(move)
            chk.wait_change(version, 1.5)
        else:
            time.sleep(0.05)
    return "the next turn did not begin within %d seconds" % seconds


def run_trial(chk, board, index, say=lambda *_a: None):
    """One trial of one board on a running Checker. Returns a Trial."""
    t = Trial(board, index)
    t0 = time.time()
    s = chk.s
    try:
        if not chk.clean():
            t.error = "could not get back to a clean board"
            return t
        hand = list(board.mine_hand)
        if not chk.apply(board.setup_lines(), hand, board.mine_bf[0] if board.mine_bf and not hand else None):
            t.error = "the board could not be set up"
            return t
        me = s.me()
        ai = s.opponents()[0]
        with Watcher(s, t, me["id"], ai["id"], me.get("name") or "Checker") as watcher:
            for name in board.plays:                                  # my turn's boards: the scripted plays, one after the other
                card = chk.find_in(name, "hand")
                if not card:
                    t.error = "%s is not in my hand" % name
                    return t
                result = cc.Result(name, "cast")
                chk.drive(result, lambda cid=card["id"]: s.click_card(cid), budget=TRIAL_BUDGET)
                if any(n.startswith(("stalled", "the engine stopped")) for n in result.notes):
                    t.error = "; ".join(result.notes)
                    return t
            # then the turn is played out: on my turn the AI gets every priority of it (an instant "on your turn", I1, can come in
            # my main phase, combat or end step); on the AI's turn (R1-R3) this passes until my turn begins, where the board is read
            t.error = pass_the_turn(chk, watcher)
            if t.error:
                return t
            for name in board.my_cards():
                t.zones[name] = where(chk, name)
            t.stack_end = [_entry_text(item, watcher.names) for item in (s.state or {}).get("stack") or []]
        t.verdict = board.judge(t)
    except Exception as exc:                      # a trial never stops the run: it is reported as an error
        t.error = "%s: %s" % (type(exc).__name__, exc)
    finally:
        t.seconds = time.time() - t0
        say("  %s trial %d: %s  %s" % (board.key, index + 1, verdict_word(t), "; ".join(t.ai_entries[:2]) or t.error))
    return t


def verdict_word(t):
    return "error" if t.error else ("yes" if t.verdict else "NO" if t.verdict is False else "?")


def run(trials=5, seed=7, boards=None, say=print, deck=None, runtime=None):
    """Every board, `trials` times each, on one engine. Returns [Trial]."""
    keys = [k.strip().upper() for k in boards] if boards else [b.key for b in BOARDS]
    unknown = [k for k in keys if k not in BOARD_BY_KEY]
    if unknown:
        raise ValueError("unknown board(s): %s (have %s)" % (", ".join(unknown), ", ".join(BOARD_BY_KEY)))
    chk = cc.Checker(deck or KINNAN_DECK, runtime=runtime, seed=seed, say=say, per_check=TRIAL_BUDGET, classic_stops=True)
    say("Starting Forge (seed %d) ..." % seed)
    chk.start()
    out = []
    try:
        for key in keys:
            board = BOARD_BY_KEY[key]
            say("%s  %s  ->  %s" % (board.key, board.title, board.right))
            for i in range(trials):
                out.append(run_trial(chk, board, i, say))
    finally:
        chk.stop()
    return out


def score(results):
    """{board key: (yes, judged)} and the total."""
    per = {}
    for t in results:
        yes, n = per.get(t.board, (0, 0))
        if t.verdict is None:
            continue
        per[t.board] = (yes + (1 if t.verdict else 0), n + 1)
    total = (sum(y for y, _n in per.values()), sum(n for _y, n in per.values()))
    return per, total


def summary_text(results, seed, version=""):
    per, (yes, n) = score(results)
    errors = [t for t in results if t.error]
    lines = ["AI BENCH - does the AI answer the right threat?", "seed %d%s" % (seed, ("  |  " + version) if version else ""),
             "score %d/%d" % (yes, n), ""]
    lines.append("%-4s %-62s %-36s %s" % ("", "board", "the right answer", "yes/judged"))
    for b in BOARDS:
        if b.key not in per and not any(t.board == b.key for t in results):
            continue
        y, k = per.get(b.key, (0, 0))
        lines.append("%-4s %-62s %-36s %d/%d" % (b.key, b.title[:62], b.right[:36], y, k))
    lines.append("")
    for b in BOARDS:
        misses = [t for t in results if t.board == b.key and t.verdict is False]
        if misses:
            lines.append("%s misses (%d): what the AI did:" % (b.key, len(misses)))
            for t in misses[:5]:
                lines.append("    trial %d: AI cast %s; stack: %s; ended: %s%s%s" % (
                    t.index + 1, ", ".join(t.ai_cast) or "nothing", " | ".join(t.ai_entries) or "-",
                    ", ".join("%s in %s" % (k, v or "?") for k, v in sorted(t.zones.items())),
                    ("; my turn began with " + ", ".join(t.at_my_turn)) if t.at_my_turn is not None else "",
                    ("; still on the stack: " + " | ".join(t.stack_end)) if t.stack_end else ""))
    if errors:
        lines.append("")
        lines.append("errors (%d, not judged):" % len(errors))
        for t in errors[:10]:
            lines.append("    %s trial %d: %s" % (t.board, t.index + 1, t.error))
    return "\n".join(lines) + "\n"


def write_results(folder, results, seed, version=""):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "ai_bench_results.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": seed, "version": version, "trials": [t.as_dict() for t in results]}, f, indent=1)
    text = summary_text(results, seed, version)
    with open(os.path.join(folder, "ai_bench_summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    return text
