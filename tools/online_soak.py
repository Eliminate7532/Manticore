# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools\\online_soak.py - Round MP1: two bots play each other through the real online connection, game after game.

    python tools\\online_soak.py --hours 2 [--games N] [--seed N] [--concede 0.7] [--leave 0.2] [--drops 0.5] [--pods 0.3] [--resume 0.2]
                                  [--keep] [--out DIR]

Each game: NetHost starts with one sample deck (HostSession, as Host online does, on 127.0.0.1), a second table joins it over
TLS with another (NetSession, as Join online does), and soak_bot.SoakBot plays BOTH seats - the same bot, the same
"ask once per question" loop as tools\\soak.py. Nothing else is in the game: no Forge AI.

After each game, besides bridge_rules on both recordings (the host's with its engine log):
  online_hidden     FAIL  a guest snapshot shows a card in the host's hand or library that Forge says the guest may not see
                          (and the other way round) - Snapshot.card uses canBeShownTo, so this is the bridge's own promise
  online_refused    FAIL  the host refused a command the guest sent (the bot only sends play commands)
  online_left       FAIL  a side saw "peer_left" before the game was over
  online_mismatch   FAIL  the two sides disagree about the game being over
  online_name_seen  WARN  a host card's name appears in the guest's game log although the guest never saw that card (a
                          heuristic for a leak through the log; names in the guest's own deck and basic lands are ignored)
  online_leave      FAIL  the guest left (closed its connection) and the host's table did not reach game over within 30 s
                          (round MP2c, a game of 3-4: the host didn't hear within 30 s that it left; the others play on)
  online_resume     FAIL  round MP2b (--resume): a game closed at a quiet moment and continued from the host's journal did not
                          play back to the end, or some table's board (turn, phase, prompt, life, cards in each zone) differs
  online_reconnect  FAIL  round MP2 (--drops): the guest's connection was cut mid-game and it was not playing again within
                          30 s (its table reconnects by itself; the host keeps the seat for its grace period)
  (counted, not a finding: "late" questions the bridge released because Forge asked them after the game had ended -
  routine after a concede; before BridgeGui.releaseIfGameOver such a game hung for good)
  stall             FAIL  neither side got a new snapshot for 120 s

Output: soak_runs\\online_<date>_<time>\\ - online_summary.txt, and game_NNN\\ (both recordings, gzipped, and the host's
engine log) kept only for games with a finding.
"""
import argparse
import datetime
import json
import os
import random
import re
import shutil
import socket
import sys
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

TURN_CAP = 80                    # both players' turns together
GAME_TIMEOUT = 30 * 60
STALL_SECONDS = 120
HOST_READY_SECONDS = 120
POLL_SLEEP = 0.02
MOVE_WAIT_SECONDS = 3.0
RECONNECT_SECONDS = 30           # round MP2: a cut connection must be back within this
PASSWORD = "otter-lamp-river-42"
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Plains", "Snow-Covered Island",
          "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest"}

soak = fc = fn = bridge_rules = SoakBot = None


def _load_modules():
    global soak, fc, fn, bridge_rules, SoakBot
    if fc is not None:
        return
    from tools import soak as _soak
    _soak._load_modules()
    import forge_client as _fc
    import forge_net as _fn
    import bridge_rules as _br
    from soak_bot import SoakBot as _SB
    soak, fc, fn, bridge_rules, SoakBot = _soak, _fc, _fn, _br, _SB


# ---- checks that need no Forge (tests/test_online_soak.py) -----------------------------------------------------------
def hidden_leaks(state):
    """Cards in another player's Hand or Library that this snapshot shows face up although Forge marked them hidden, or that
    carry more than a face-down card should (a name other than "Face-down card", a type or text)."""
    out = []
    st = state or {}
    me = st.get("me")
    for p in st.get("players") or []:
        if p.get("id") == me:
            continue
        for zone in ("hand", "library"):
            for c in (p.get("zones") or {}).get(zone) or []:
                if not c.get("hidden"):
                    continue                              # Forge says this viewer may see it (a reveal): its call, not a leak
                extra = sorted(k for k in ("type", "text", "oracleName", "cost") if c.get(k))
                if c.get("name") not in (None, "Face-down card") or extra:
                    out.append("%s's %s card %s shows %s" % (p.get("name"), zone, c.get("id"), c.get("name") or extra))
    return out


def visible_names(state, into):
    """Every card name this snapshot shows face up, anywhere (zones, the stack, the prompt's card)."""
    def walk(x):
        if isinstance(x, dict):
            name = x.get("name")
            if isinstance(name, str) and not x.get("hidden") and name != "Face-down card":
                into.add(name)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(state or {})
    return into


def names_seen_in_log(log_lines, host_names, guest_names, seen):
    """Host card names in the guest's log that the guest never saw and doesn't play itself (online_name_seen)."""
    suspects = sorted(n for n in host_names if n not in seen and n not in guest_names and n not in BASICS and len(n) > 3)
    # Online night 1, game 1: the guest's own "The Mind Stone" matched the host's "Mind Stone". Names the guest may know (its
    # own cards, what it has seen) are blanked out of the line first, longest first, so a longer name can't hide a shorter one.
    known = sorted((n for n in set(guest_names) | set(seen) if n), key=len, reverse=True)
    out = []
    for line in log_lines:
        rest = line
        for k in known:
            rest = re.sub(r"(?<![\w'])" + re.escape(k) + r"(?![\w'])", " ", rest)
        for n in suspects:
            if re.search(r"(?<![\w'])" + re.escape(n) + r"(?![\w'])", rest):
                out.append((n, line[:160]))
    return out


def deck_names(dck_path):
    names = set()
    try:
        with open(dck_path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^\s*\d+\s+(.+?)(?:\|.*)?\s*$", line)
                if m:
                    names.add(m.group(1).strip())
    except OSError:
        pass
    return names


# ---- one seat ----------------------------------------------------------------------------------------------------------
class Seat:
    """One table: its session, its bot and the bot's memory, and the 'ask once per question' state from tools/soak.py."""

    def __init__(self, label, session, seed):
        self.label, self.session = label, session
        self.bot = SoakBot(seed=seed)
        self.mem = {}
        self.last_key, self.last_at = None, 0.0
        self.version = -1
        self.hidden = []
        self.seen = set()

    def step(self):
        s = self.session
        s.poll()
        progressed = s.state_version != self.version
        if progressed:
            self.version = s.state_version
            if s.state:
                for leak in hidden_leaks(s.state):
                    if len(self.hidden) < 5:
                        self.hidden.append(leak)
                visible_names(s.state, self.seen)
        while s.infos:
            info = dict(s.infos.popleft())
            info["_seq"] = (s.state or {}).get("inputSeq")
            self.mem.setdefault("infos", []).append(info)
            del self.mem["infos"][:-20]
        st = s.state or {}
        if st.get("asking") is False and not s.requests:
            return progressed
        key = soak._question_key(s.state, s.requests)
        if key == self.last_key and time.monotonic() - self.last_at < MOVE_WAIT_SECONDS:
            return progressed
        self.last_key, self.last_at = key, time.monotonic()
        move = self.bot.next_action(s.state, list(s.requests), self.mem)
        if move is not None:
            soak._do(s, move)
        return progressed


# ---- one game ----------------------------------------------------------------------------------------------------------
class OnlineResult:
    def __init__(self, index, seed, decks):
        self.index, self.seed, self.decks = index, seed, decks
        self.turns = 0
        self.ended = "?"
        self.findings = []
        self.seconds = 0.0
        self.kept = None
        self.conceded = None         # "host" / "guest" when that side conceded (the --concede share of games)
        self.late = 0                # questions Forge asked after the game ended, released by the bridge
        self.players, self.guests = 2, 1     # round MP2c (--pods): the table's size, and how many of them joined online
        self.drops = 0               # round MP2: times the soak cut the guest's connection (--drops)
        self.reconnected = 0         # ... and the guest's table came back by itself
        self.resumes = 0             # round MP2b (--resume): times the game was closed and continued from its journal
        self.resume_turn = None

    def fail_rules(self):
        return sorted({f["rule"] for f in self.findings if f["severity"] == "fail"})

    def warn_rules(self):
        return sorted({f["rule"] for f in self.findings if f["severity"] == "warn"})

    def line(self):
        return "game %d: seed=%s decks=%s%s turns=%d ended=%s%s %.0fs fails=%s warns=%s%s%s%s%s" % (
            self.index, self.seed, "+".join(os.path.splitext(d)[0] for d in self.decks),
            (" (%d players, %d online)" % (self.players, self.guests)) if self.players > 2 else "", self.turns, self.ended,
            (" (%s)" % (self.conceded if " " in self.conceded else self.conceded + " conceded")) if self.conceded else "", self.seconds,
            ",".join(self.fail_rules()) or "-", ",".join(self.warn_rules()) or "-",
            (" late=%d" % self.late) if self.late else "",
            (" drops=%d/%d" % (self.reconnected, self.drops)) if self.drops else "",
            (" resumed@%s" % self.resume_turn) if self.resumes else "", ("  -> " + self.kept) if self.kept else "")


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_game(index, rng, out_root, cert, args):
    seed = (args.seed + index) if args.seed is not None else fc.new_seed()
    pool = soak.deck_pool("sample")
    # --pods (round MP2c): in this share of games the table has 3 or 4 players - 1-3 guests over the network, AI players for
    # the rest. Otherwise 1v1, as before.
    players = rng.choice([3, 4]) if rng.random() < getattr(args, "pods", 0.0) else 2
    n_guests = rng.randint(1, players - 1) if players > 2 else 1
    n_ai = players - 1 - n_guests
    decks = rng.sample(pool, players) if len(pool) >= players else [rng.choice(pool) for _ in range(players)]
    result = OnlineResult(index, seed, [os.path.basename(d) for d in decks])
    result.players, result.guests = players, n_guests
    game_dir = os.path.join(out_root, "game_%03d" % index)
    os.makedirs(game_dir, exist_ok=True)
    host_rec = os.path.join(game_dir, "host.jsonl")
    guest_recs = [os.path.join(game_dir, "guest.jsonl" if k == 0 else "guest%d.jsonl" % (k + 1)) for k in range(n_guests)]
    engine_log = os.path.join(game_dir, "host_engine.log")
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="online_soak_") as tmp:
        host_dck = soak.build_dck(decks[0], tmp, "host")
        guest_dcks = [soak.build_dck(decks[1 + k], tmp, "guest%d" % k) for k in range(n_guests)]
        ai_dcks = [soak.build_dck(decks[1 + n_guests + k], tmp, "ai%d" % k) for k in range(n_ai)]
        port = _free_port()
        extra = {"guests": n_guests, "ai_paths": ai_dcks} if players > 2 else {}
        journal = os.path.join(game_dir, "cmds.jsonl")             # round MP2b: NetHost's journal, for --resume
        host = fc.HostSession(host_dck, "Host", port, PASSWORD, cert, os.path.join(tmp, "incoming.dck"), upnp=False,
                              bind="127.0.0.1", code="soakhost", seed=seed, record_path=host_rec, journal_path=journal, **extra)
        host.stderr_path = engine_log
        guests = []
        try:
            host.start()
            end = time.monotonic() + HOST_READY_SECONDS
            while host.hosting is None and not host.exited and not host.host_failed and time.monotonic() < end:
                host.poll()
                time.sleep(0.05)
            if host.hosting is None:
                result.ended = "host_failed"
                result.findings.append({"rule": "host_failed", "severity": "fail",
                                        "detail": str(host.host_failed or "NetHost never got ready")})
                return _finish(result, host, guests, host_rec, guest_recs, engine_log, game_dir, t0)
            for k in range(n_guests):
                with open(guest_dcks[k], encoding="utf-8") as f:
                    g = fc.NetSession("127.0.0.1", port, "Guest" if k == 0 else "Guest%d" % (k + 1), PASSWORD, "guest%d" % k,
                                      f.read(), fingerprint=cert.fingerprint, code="soakguest", record_path=guest_recs[k])
                guests.append(g)
                try:
                    g.start()
                except Exception as e:                     # NetRefused or a socket error: the join itself failed
                    result.ended = "join_failed"
                    result.findings.append({"rule": "join_failed", "severity": "fail",
                                            "detail": "%s: %s: %s" % (g.name, type(e).__name__, e)})
                    return _finish(result, host, guests, host_rec, guest_recs, engine_log, game_dir, t0)
            guest = guests[0]
            seats = [Seat("host", host, seed)] + [Seat("guest" if k == 0 else "guest%d" % (k + 1), g, seed + 7919 * (k + 1))
                                                   for k, g in enumerate(guests)]
            guest_seat = seats[1]
            # Two bots rarely win, so most games would only end at the turn cap. In most games one side concedes at a random
            # turn instead: that tests a concede from either table and both tables reaching game over. (A pod plays on after
            # one concede, so there every bot but one concedes, one after the other.)
            concede_turn = rng.randint(12, 60) if rng.random() < args.concede else None
            conceders = [rng.choice(seats)] if players == 2 else rng.sample(seats, len(seats) - 1)
            # --leave: in this share of games the guest simply goes (closes its connection, as closing the window does) instead;
            # NetHost then concedes for it and the host's table must still reach game over (in a pod: hear that it left).
            leave_turn = None
            if concede_turn is not None and rng.random() < args.leave:
                leave_turn, concede_turn = concede_turn, None
            # --drops (round MP2): in this share of games the guest's connection is cut once or twice mid-game, as a Wi-Fi
            # hiccup would; its table must reconnect by itself and the game go on.
            drop_turns = sorted(rng.sample(range(4, 40), rng.randint(1, 2))) if rng.random() < args.drops else []
            # --resume (round MP2b): in this share of games the host's engine is closed at a quiet moment and the game is
            # continued from its journal; both boards must be exactly as they were.
            resume_turn = rng.randint(6, 30) if rng.random() < getattr(args, "resume", 0.0) else None
            pending_drop = None
            last_progress = time.monotonic()

            def everyone_over():
                return host.game_over and all(g.game_over for g in guests if not g.exited or g.game_over)

            while True:
                progressed = False
                for seat in seats:
                    progressed = seat.step() or progressed
                now_turn = (host.state or {}).get("turn") or 0
                if drop_turns and now_turn >= drop_turns[0] and pending_drop is None and guest.connected() \
                        and not (host.game_over or guest.game_over):
                    drop_turns.pop(0)
                    pending_drop = (guest.reconnects, time.monotonic(), now_turn)
                    result.drops += 1
                    try:
                        guest.proc.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                if pending_drop is not None:
                    if guest.reconnects > pending_drop[0] and guest.connected():
                        result.reconnected += 1
                        pending_drop = None
                        last_progress = time.monotonic()
                    elif time.monotonic() - pending_drop[1] > RECONNECT_SECONDS and not (host.game_over or guest.game_over):
                        result.ended = "reconnect_failed"
                        result.findings.append({"rule": "online_reconnect", "severity": "fail",
                                                "detail": "the guest's connection was cut on turn %s and it was not back after "
                                                          "%d s (guest net_state=%s, net_lost=%s; host peer_dropped=%s)" % (
                                                              pending_drop[2], RECONNECT_SECONDS, guest.net_state,
                                                              guest.net_lost, host.peer_dropped)})
                        break
                if resume_turn is not None and now_turn >= resume_turn and pending_drop is None and _quiet(seats) \
                        and not (host.game_over or any(g.game_over for g in guests)):
                    resume_turn = None
                    got = _resume_game(result, seed, tmp, cert, host, guests, seats, journal, game_dir, extra)
                    if got is None:
                        break
                    host, guests, seats = got
                    guest, guest_seat = guests[0], seats[1]
                    last_progress = time.monotonic()
                    continue
                if concede_turn is not None and now_turn >= concede_turn:
                    labels = {c.label for c in conceders}             # by label: --resume replaces the sessions
                    conceders = [st for st in seats if st.label in labels]
                    for seat in conceders:
                        seat.session.concede()
                    result.conceded = conceders[0].label if len(conceders) == 1 else "+".join(c.label for c in conceders)
                    concede_turn = None
                if leave_turn is not None and now_turn >= leave_turn:
                    guest.close()
                    result.conceded = "guest left"
                    seats = [st for st in seats if st.session is not guest]
                    leave_turn = None
                    if pending_drop is not None:
                        pending_drop = None
                    end = time.monotonic() + 30
                    if players == 2:
                        while time.monotonic() < end and not host.game_over:
                            host.poll()
                            time.sleep(0.05)
                        if host.game_over and getattr(host, "peer_left", None):
                            result.ended = "game_over"
                        else:
                            result.ended = "stall"
                            result.findings.append({"rule": "online_leave", "severity": "fail",
                                                    "detail": "the guest left on turn %s; host game_over=%s peer_left=%s" % (
                                                        (host.state or {}).get("turn"), host.game_over,
                                                        bool(getattr(host, "peer_left", None)))})
                        break
                    # a pod: the others play on; the host must hear that the guest left (and the guest's player be out)
                    while time.monotonic() < end and not host.players_left:
                        for seat in seats:
                            seat.session.poll()
                        time.sleep(0.05)
                    if not host.players_left:
                        result.findings.append({"rule": "online_leave", "severity": "fail",
                                                "detail": "in a game of %d the guest left on turn %s and the host never heard"
                                                          % (players, now_turn)})
                        result.ended = "stall"
                        break
                    continue
                if progressed:
                    last_progress = time.monotonic()
                if everyone_over():
                    result.ended = "game_over"
                    break
                if host.game_over or any(g.game_over for g in guests):
                    # one side heard first; give the others a few seconds
                    end = time.monotonic() + 10
                    while time.monotonic() < end and not everyone_over():
                        for seat in seats:
                            seat.session.poll()
                        time.sleep(0.05)
                    if not everyone_over():
                        result.findings.append({"rule": "online_mismatch", "severity": "fail",
                                                "detail": "game over on %s only" % ", ".join(
                                                    st.label for st in seats if st.session.game_over)})
                    result.ended = "game_over"
                    break
                for seat in seats:
                    if seat.session.fatal or seat.session.exited:
                        result.ended = "crash_" + seat.label
                for seat in seats:
                    if getattr(seat.session, "peer_left", None) and not seat.session.game_over:
                        result.findings.append({"rule": "online_left", "severity": "fail",
                                                "detail": "%s saw peer_left %s mid-game" % (seat.label, seat.session.peer_left)})
                        result.ended = "peer_left"
                if result.ended != "?":
                    break
                turn = (host.state or {}).get("turn") or 0
                result.turns = max(result.turns, turn)
                if turn > args.turn_cap:
                    result.ended = "turn_cap"
                    break
                if time.time() - t0 > args.game_timeout:
                    result.ended = "time_cap"
                    break
                if time.monotonic() - last_progress > STALL_SECONDS:
                    result.ended = "stall"
                    prompts = "; ".join("%s: %r" % (s.label, ((s.session.state or {}).get("prompt") or {}).get("message", "")[:80])
                                        for s in seats)
                    result.findings.append({"rule": "stall", "severity": "fail",
                                            "detail": "no new snapshot on any side for %ds (%s)" % (STALL_SECONDS, prompts)})
                    break
                time.sleep(POLL_SLEEP)
            for seat in seats:
                for leak in seat.hidden:
                    result.findings.append({"rule": "online_hidden", "severity": "fail", "detail": "%s: %s" % (seat.label, leak)})
            for g in guests:
                for r in getattr(g, "refused_cmds", []) or []:
                    result.findings.append({"rule": "online_refused", "severity": "fail", "detail": json.dumps(r)[:200]})
            guest_log = [e.get("text") or "" for e in getattr(guest, "log", []) or []]
            for name, line in names_seen_in_log(guest_log, deck_names(host_dck), deck_names(guest_dcks[0]), guest_seat.seen)[:3]:
                result.findings.append({"rule": "online_name_seen", "severity": "warn",
                                        "detail": "%r in the guest's log: %s" % (name, line)})
        finally:
            for s in list(guests) + [host]:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
    return _finish(result, host, guests, host_rec, guest_recs, engine_log, game_dir, t0)


def _quiet(seats):
    """Every table idle, and somebody being asked for priority: a moment where the game waits for a person (MP2b --resume)."""
    for st in seats:
        if st.session.requests:
            return False
    return any(((st.session.state or {}).get("prompt") or {}).get("message", "").startswith("Priority")
               and (st.session.state or {}).get("asking") for st in seats)


def _resume_game(result, seed, tmp, cert, host, guests, seats, journal, game_dir, extra):
    """Round MP2b: close the host's engine and every guest, continue the game from NetHost's journal with fresh sessions, and
    compare every table's board with what it was. Returns (host, guests, seats) to play on with, or None (a finding was added)."""
    import replay
    wait_end = time.monotonic() + 1.5
    while time.monotonic() < wait_end:                            # nothing else under way
        for st in seats:
            st.session.poll()
        time.sleep(0.05)
    before = {st.label: replay.summary(st.session.state, any_waiting=True) for st in seats}
    seat_list = list(host.seat_list)
    turn = (host.state or {}).get("turn")
    for st in seats:
        try:
            st.session.close()
        except Exception:
            pass
    src = os.path.join(game_dir, "cmds_resumed_from.jsonl")
    shutil.copyfile(journal, src)
    port = _free_port()
    extra = {k: v for k, v in extra.items() if k != "guests"}
    h2 = fc.HostSession(host.deck_path, "Host", port, PASSWORD, cert, os.path.join(tmp, "incoming2.dck"), upnp=False,
                        bind="127.0.0.1", code="soakhost", seed=seed, record_path=os.path.join(game_dir, "host_resumed.jsonl"),
                        journal_path=journal, resume={"replay": src, "guests": [(g["seat"], g["name"], g["deck"]) for g in seat_list]},
                        **extra)
    h2.stderr_path = os.path.join(game_dir, "host_engine_resumed.log")
    result.resumes += 1
    result.resume_turn = turn
    h2.start()
    end = time.monotonic() + 600
    while (h2.resuming or h2.hosting is None) and not h2.exited and time.monotonic() < end:
        h2.poll()
        time.sleep(0.05)
    r = h2.replay_result or {}
    if h2.exited or h2.resuming or r.get("diverged"):
        result.findings.append({"rule": "online_resume", "severity": "fail",
                                "detail": "continuing on turn %s: the play-back %s (%s)" % (
                                    turn, "stopped early" if r.get("diverged") else "never finished",
                                    r.get("diverged") or ("engine exited" if h2.exited else "timeout"))})
        result.ended = "resume_failed"
        h2.close()
        return None
    new_guests = []
    for k, g in enumerate(seat_list):
        ng = fc.NetSession("127.0.0.1", port, g["name"], PASSWORD, "guest", "", fingerprint=cert.fingerprint, code="soakguest",
                           record_path=os.path.join(game_dir, "guest%d_resumed.jsonl" % (k + 1)))
        ng.start()
        new_guests.append(ng)
    end = time.monotonic() + 60
    while time.monotonic() < end and not (h2.everyone_back() and all(g.ready and g.state for g in new_guests) and h2.state):
        for s in [h2] + new_guests:
            s.poll()
        time.sleep(0.05)
    wait_end = time.monotonic() + 2.0
    while time.monotonic() < wait_end:
        for s in [h2] + new_guests:
            s.poll()
        time.sleep(0.05)
    new_seats = [Seat("host", h2, seed + 1)] + [Seat(seats[k + 1].label, g, seed + 7919 * (k + 1) + 1)
                                                 for k, g in enumerate(new_guests)]
    diffs = []
    for st in new_seats:
        for d in replay.differences(before.get(st.label) or {}, replay.summary(st.session.state, any_waiting=True)):
            diffs.append("%s: %s" % (st.label, d))
    if diffs:
        result.findings.append({"rule": "online_resume", "severity": "fail",
                                "detail": "continued on turn %s, but the board differs: %s" % (turn, "; ".join(diffs[:4]))})
    return h2, new_guests, new_seats


SOAK_CLOSED = ("turn_cap", "time_cap")       # the soak itself ended these games, guest first
GUEST_LEFT = re.compile(r"^nethost: (the guest|.+) left \(")       # MP1/MP2a: "the guest left (", MP2c: "<name> left ("


def before_soak_closed(lines, ended):
    """The engine log up to the moment the soak closed the game itself (turn or time cap). Closing the guest's connection
    first makes the host's game thread die on its next question ("client went away while waiting for a reply") - the
    harness's doing, not a finding (first online night, game 0). A guest leaving in any other game is still judged."""
    if ended not in SOAK_CLOSED:
        return lines
    for i, line in enumerate(lines):
        if GUEST_LEFT.search(line):
            return lines[:i]
    return lines


def _finish(result, host, guests, host_rec, guest_recs, engine_log, game_dir, t0):
    result.seconds = time.time() - t0
    try:
        with open(engine_log, encoding="utf-8", errors="replace") as f:
            log_lines = f.read().splitlines()
    except OSError:
        log_lines = []
    # BridgeGui.releaseIfGameOver: questions Forge asked after the game had ended. Routine after a concede (Forge asks the next
    # player before it notices the game is over); before that fix such a game hung with both tables saying "Waiting for ...".
    # Counted, not a finding.
    result.late = len([ln for ln in log_lines if ln.startswith("gamesync: released")])
    log_lines = before_soak_closed(log_lines, result.ended)
    if isinstance(guest_recs, str):
        guest_recs = [guest_recs]
    recs = [("host", host_rec, log_lines)] + [("guest" if k == 0 else "guest%d" % (k + 1), rec, ()) for k, rec in enumerate(guest_recs)]
    for label, rec, lines in recs:
        if not os.path.isfile(rec):
            continue
        try:
            msgs = bridge_rules.load_stream(rec)
            for f in bridge_rules.check_stream(msgs, lines):
                f = dict(f)
                f["detail"] = "%s: %s" % (label, f.get("detail", ""))
                result.findings.append(f)
        except Exception as e:
            result.findings.append({"rule": "record_unreadable", "severity": "warn", "detail": "%s: %s" % (label, e)})
    if getattr(_finish, "keep_all", False) or (result.findings and any(f["severity"] == "fail" for f in result.findings)):
        for rec in [host_rec] + list(guest_recs):
            soak._compress_record(rec)
        result.kept = game_dir
    else:
        shutil.rmtree(game_dir, ignore_errors=True)
    return result


def summary_text(results, started, args, stopped_early):
    games = len(results)
    ended = {}
    for r in results:
        ended[r.ended] = ended.get(r.ended, 0) + 1
    fails, warns = {}, {}
    for r in results:
        for f in r.findings:
            bucket = fails if f["severity"] == "fail" else warns
            bucket.setdefault(f["rule"], []).append((r.index, f.get("detail", "")))
    lines = ["ONLINE SOAK: %d game(s), %s" % (games, ", ".join("%d %s" % (n, k) for k, n in sorted(ended.items())) or "none"),
             "started %s, %s%s" % (started, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                   " (stopped early)" if stopped_early else ""),
             "version: %s" % _version_line(),
             "late questions released after game over: %d, in %d game(s)" % (sum(r.late for r in results),
                                                                             len([r for r in results if r.late])),
             "connections cut (--drops): %d, came back by themselves: %d" % (sum(r.drops for r in results),
                                                                         sum(r.reconnected for r in results)), ""]
    lines.append("FAILURES (%d kind(s))" % len(fails))
    for rule, items in sorted(fails.items()):
        lines.append("  %s: %d game(s)" % (rule, len({i for i, _ in items})))
        for i, d in items[:3]:
            lines.append("    game %d: %s" % (i, d[:220]))
    lines.append("WARNINGS (%d kind(s))" % len(warns))
    for rule, items in sorted(warns.items()):
        lines.append("  %s: %d game(s)" % (rule, len({i for i, _ in items})))
        for i, d in items[:3]:
            lines.append("    game %d: %s" % (i, d[:220]))
    lines.append("")
    lines.append("GAMES")
    lines += ["  " + r.line() for r in results]
    return "\n".join(lines) + "\n"


def _version_line():
    try:
        import version
        return version.describe()
    except Exception:
        return "unknown"


def run(args):
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out_root = args.out or os.path.join(BASE_DIR, "soak_runs", "online_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_root, exist_ok=True)
    os.environ["MANTICORE_DATA_DIR"] = out_root            # crash notes and engine logs stay in this run's folder
    _load_modules()
    fc.sync_bridge()                                         # the program's own bridge jar, as a game start does
    cert = fn.ensure_host_cert(out_root, fc.find_java())
    rng = random.Random(args.seed)
    results, stopped = [], False
    deadline = time.time() + args.hours * 3600 if args.hours else None
    summary = os.path.join(out_root, "online_summary.txt")
    try:
        i = 0
        while (args.games is None or i < args.games) and (deadline is None or time.time() < deadline):
            r = run_game(i, rng, out_root, cert, args)
            results.append(r)
            print(r.line(), flush=True)
            with open(summary, "w", encoding="utf-8") as f:
                f.write(summary_text(results, started, args, False))
            i += 1
    except KeyboardInterrupt:
        stopped = True
        print("stopping (Ctrl+C): writing what happened so far.")
    with open(summary, "w", encoding="utf-8") as f:
        f.write(summary_text(results, started, args, stopped))
    shutil.rmtree(os.path.join(out_root, "net"), ignore_errors=True)        # the run's own throwaway certificate
    print("summary: " + summary)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Two soak bots play each other through Host online / Join online.")
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--games", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--turn-cap", type=int, default=TURN_CAP)
    ap.add_argument("--game-timeout", type=int, default=GAME_TIMEOUT)
    ap.add_argument("--concede", type=float, default=0.7, help="share of games where one side concedes at a random turn")
    ap.add_argument("--leave", type=float, default=0.2, help="of those, the share where the guest leaves (closes) instead")
    ap.add_argument("--pods", type=float, default=0.3, help="share of games with 3 or 4 players: 1-3 guests online, AI players "
                                                             "for the rest (round MP2c)")
    ap.add_argument("--resume", type=float, default=0.2, help="share of games closed at a quiet moment and continued from the "
                                                                "host's journal (round MP2b: the board must be the same)")
    ap.add_argument("--drops", type=float, default=0.5, help="share of games where the guest's connection is cut mid-game "
                                                              "(round MP2: it must reconnect by itself)")
    ap.add_argument("--keep", action="store_true", help="keep every game's recordings, not only those with a failure")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    _finish.keep_all = args.keep
    if args.hours is None and args.games is None:
        args.games = 3
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
