# SPDX-License-Identifier: GPL-3.0-or-later
"""
rewind.py - Undo back to the start of your turn (Round UNDO1).

Karl, 8 Oct 2026: "make undo backtrack as far back as the beginning of your turn". His choices (the same evening): an EXACT
rebuild rather than Forge's experimental snapshot, and a list of this turn's moments to pick from.

Why a rebuild: Forge cannot put a game state back exactly. Its GameSnapshot (Game.EXPERIMENTAL_RESTORE_SNAPSHOT, off by default,
used only to cancel a spell during payment) keeps the live card objects and resets only their zone, tapped, face-down and
summoning sickness - counters and damage on cards that stayed are not put back - copies only spells on the stack, and nothing
copies delayed triggers (a Pact's upkeep payment) or until-end-of-turn effects. A game IS repeatable, though: the same seed and the
same commands give the same game (that is how Resume works: journal.py + replay.Replayer). So a rewind starts a second engine with
the game's seed and decks, plays the journal's commands back up to the chosen moment, checks that the board there is the board that
was recorded at that moment, and only then swaps it in for the running engine. If anything doesn't match - or it is cancelled -
the running game is kept exactly as it was.

The moments ("points"): the first time Forge asks me anything in one of my turns (the start of the turn - an upkeep trigger's
question, or Main 1), then every time it gives me priority in that turn. Each is written into the journal ({"t": "point"}, see
journal.py) with how many commands came before it, which question it is (Forge's question number, or a request id), how long
the game log was (for its label) and a fingerprint of the board (board_hash). Undo offers the points of my latest turn.

No pygame in here.
"""
import threading
import time

PRIORITY_INPUT = "InputPassPriority"
MAX_CHOICES = 9                  # the window's buttons answer to the keys 1-9: the start of the turn + the newest 8 moments
WAIT_LIMIT = 90.0                # seconds a rebuild waits for the engine at one step (an AI turn of a big pod can take a while)

PHASES = {"UNTAP": "Untap", "UPKEEP": "Upkeep", "DRAW": "Draw step", "MAIN1": "Main 1", "COMBAT_BEGIN": "Combat",
          "COMBAT_DECLARE_ATTACKERS": "Attackers", "COMBAT_DECLARE_BLOCKERS": "Blockers", "COMBAT_FIRST_STRIKE_DAMAGE": "Combat damage",
          "COMBAT_DAMAGE": "Combat damage", "COMBAT_END": "End of combat", "MAIN2": "Main 2", "END_OF_TURN": "End step",
          "CLEANUP": "Cleanup"}


# What the check after a rebuild compares (board_fingerprint). Only what Forge decides: nothing that depends on timing - the
# highlights ("weak", "selectable": patch 43's AvailableActions is time-limited), the prompt text, the stops and yields (settings),
# the event and question counters. The library ORDER is not in a snapshot, so it can't be compared; its size is.
CARD_FIELDS = ("id", "name", "tapped", "damage", "counters", "power", "toughness", "faceDown", "sick", "controller", "owner",
               "token", "keywords", "attacking", "blocking", "commander")
PLAYER_FIELDS = ("name", "life", "lost", "landsPlayed", "maxLandPlay", "handCount", "libraryCount", "manaPool", "commanderDamage",
                 "commanderCasts", "commanders", "priority")


def board_fingerprint(state):
    """Everything a rebuilt board must have exactly as the recorded one: turn, phase, whose turn, the stack (each entry's card,
    text and targets), combat, and for every player their life, counts and every card in every zone with its tapped state,
    damage, counters, power / toughness, keywords... Hidden cards count by id. A dict, ready for json."""
    st = state or {}

    def card(c):
        return {k: c.get(k) for k in CARD_FIELDS if k in c}
    players = []
    for p in st.get("players", []) or []:
        zones = {z: [card(c) for c in cards or []] for z, cards in sorted((p.get("zones") or {}).items())}
        players.append(dict({k: p.get(k) for k in PLAYER_FIELDS if k in p}, id=p.get("id"), zones=zones))
    stack = [{"card": card(e.get("card") or {}), "text": e.get("text"), "activator": e.get("activator"), "targets": e.get("targets")}
             for e in st.get("stack", []) or []]
    return {"turn": st.get("turn"), "phase": st.get("phase"), "active": st.get("activePlayer"), "stack": stack,
            "combat": st.get("combat"), "players": players}


def board_hash(state):
    """A short fingerprint of board_fingerprint (sha1, 16 hex digits)."""
    import hashlib
    import json
    return hashlib.sha1(json.dumps(board_fingerprint(state), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def board_differences(want, got, limit=3):
    """A few readable differences between two fingerprints (for the crash log when a rebuild doesn't match)."""
    out = []
    for k in ("turn", "phase", "active"):
        if want.get(k) != got.get(k):
            out.append(f"{k}: was {want.get(k)!r}, rebuilt {got.get(k)!r}")
    if want.get("stack") != got.get("stack"):
        out.append(f"the stack differs ({len(want.get('stack') or [])} entries before, {len(got.get('stack') or [])} rebuilt)")
    gp = {p.get("id"): p for p in got.get("players", [])}
    for w in want.get("players", []):
        g = gp.get(w.get("id"))
        if g is None:
            out.append(f"{w.get('name')}: missing")
            continue
        for k in PLAYER_FIELDS:
            if w.get(k) != g.get(k):
                out.append(f"{w.get('name')}: {k} was {w.get(k)!r}, rebuilt {g.get(k)!r}")
        for z, cards in (w.get("zones") or {}).items():
            if cards != (g.get("zones") or {}).get(z):
                out.append(f"{w.get('name')}: {z} differs")
    return out[:limit]


def phase_name(code):
    return PHASES.get(code or "", (code or "").replace("_", " ").title() or "?")


class Tracker:
    """Watches the snapshots of one game and says when a new rewind point has come. The table calls see() once per new snapshot
    (before it sends anything of its own for that snapshot), with how many commands the journal holds at that moment."""

    def __init__(self):
        self.key = None              # the newest question seen (("seq", n) / ("req", id)), point or not
        self.turn_started = None     # the turn whose start point is already recorded

    def reset(self, turn_started=None, key=None):
        self.key, self.turn_started = key, turn_started

    def see(self, state, requests, count, log_total, board_hash):
        """A new point (a dict for journal.point) or None. board_hash(state) makes the fingerprint (rewind.board_hash)."""
        st = state or {}
        turn = st.get("turn") or 0
        if turn < 1 or st.get("gameOver") or st.get("spectator"):
            return None
        req = next((r.get("id") for r in (requests or []) if r.get("id") is not None), None)
        seq, asking = st.get("inputSeq"), st.get("asking")
        asking = True if asking is None else bool(asking)
        if req is not None:
            key = ("req", req)
        elif asking and seq is not None:
            key = ("seq", seq)
        else:
            return None                                   # Forge is asking me nothing right now
        if key == self.key:
            return None
        self.key = key
        if st.get("activePlayer") is None or st.get("activePlayer") != st.get("me"):
            return None                                   # only moments of my own turn
        start = self.turn_started != turn
        if not start and (req is not None or st.get("input") != PRIORITY_INPUT or not asking):
            return None                                   # mid-action questions (a target, a payment) are not points
        self.turn_started = turn
        return {"i": int(count), "turn": turn, "seq": seq if req is None else None, "req": req, "log": int(log_total or 0),
                "phase": st.get("phase") or "", "start": bool(start), "sum": board_hash(st)}


def latest_turn_points(points, count, my_turn_now=None):
    """The points Undo can offer now: those of my latest turn with at least one command after them (the moment I'm at now
    has nothing to take back), in time order. my_turn_now: the turn number when it is my turn right now - then only that
    turn's points count (an earlier turn of mine is not "this turn"); during an opponent's turn, my last turn's."""
    if not points:
        return []
    turn = my_turn_now if my_turn_now else max(p.get("turn") or 0 for p in points)
    return [p for p in points if (p.get("turn") or 0) == turn and isinstance(p.get("i"), int) and p["i"] < count]


def _clean(text):
    import re
    text = re.sub(r"\s*\(\d+\)", "", text or "").strip()
    return text.rstrip(".")


def action_words(entries, me):
    """What I did first among these game-log entries ({"type", "text"}), as words for a label: "you cast Sol Ring",
    "you played Forest", "you used Grim Monolith", "you attacked". None when I did none of those (I passed)."""
    if not me:
        return None
    lead = me + " "
    for e in entries or []:
        text = (e or {}).get("text") or ""
        if not text.startswith(lead):
            continue
        rest = text[len(lead):].split("\n")[0]
        kind = (e or {}).get("type")
        if kind == "LAND" and rest.startswith("played "):
            return "you played " + _clean(rest[len("played "):])
        if kind == "STACK_ADD" and rest.startswith("cast "):
            return "you cast " + _clean(rest[len("cast "):])
        if kind == "STACK_ADD" and rest.startswith("activated "):
            return "you used " + _clean(rest[len("activated "):])
        if kind == "COMBAT" and rest.startswith("assigned ") and " to attack" in rest:
            return "you attacked"
    return None


def command_words(cmd, card_name=None):
    """What a command was, as words, when the game log shows nothing for it (a cast cancelled during payment, say)."""
    c = (cmd or {}).get("c")
    if c == "ok":
        return "you passed priority"
    if c == "card":
        name = card_name(cmd.get("id")) if card_name else None
        return f"you clicked {name}" if name else "you clicked a card"
    if c == "cancel":
        return "you cancelled"
    return "your next action"


def choices(points, count, log, log_total, me, commands=None, card_name=None, my_turn_now=None):
    """[(point, label)] for the Undo window, newest first and the start of the turn last, at most MAX_CHOICES.
    log: the session's game log (only the newest entries are kept); log_total: how many entries were ever received;
    commands: the journal's commands (for a moment after which the log shows nothing of mine); card_name(id): a card's name;
    my_turn_now: see latest_turn_points."""
    pts = latest_turn_points(points, count, my_turn_now)
    if not pts:
        return []
    base = (log_total or 0) - len(log or [])

    def entries(a, b):
        a, b = max(a, base), max(b, base)
        return list(log or [])[a - base:b - base] if b > a else []

    out = []
    for k, p in enumerate(pts):
        end = pts[k + 1]["log"] if k + 1 < len(pts) else (log_total or 0)
        did = action_words(entries(p.get("log") or 0, end), me)
        if not did and commands is not None and 0 <= p["i"] < len(commands):
            did = command_words(commands[p["i"]], card_name)
        where = phase_name(p.get("phase"))
        if p.get("start"):
            label = f"Start of your turn {p.get('turn')}" + (f" ({where}): before {did}" if did else f" ({where})")
        else:
            label = f"{where}: before {did or 'your next action'}"
        out.append((p, label))
    start = [c for c in out if c[0].get("start")]
    rest = [c for c in out if not c[0].get("start")]
    rest.reverse()                                        # newest first: 1 = take back the last thing
    keep = MAX_CHOICES - (1 if start else 0)
    return rest[:keep] + start[:1]


class RewindJob:
    """Rebuilds the game up to one point in a second engine, on a worker thread. The table keeps its own engine until this is
    done and says ok; while it runs only this job talks to the new session. Same shape as forge_table.ResumeJob (done_count,
    total, finished, cancelled, error, diverged), plus `mismatch` (the board at the point differs from the recorded one) and
    `seconds`."""

    kind = "rewind"

    def __init__(self, session, commands, point, label="", drops=None, fast_from=None, expected=None, wait_limit=WAIT_LIMIT):
        self.session, self.commands, self.point, self.label = session, list(commands), dict(point), label
        self.drops, self.fast_from, self.expected = set(drops or ()), fast_from, expected
        self.wait_limit = wait_limit
        self.done_count, self.total = 0, len(self.commands)
        self.finished, self.ok, self.diverged, self.error, self.mismatch = False, False, None, None, None
        self.cancelled = False
        self.seconds = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="rewind")

    def start(self):
        self.thread.start()
        return self

    def _progress(self, i, n):
        self.done_count = i

    def _run(self):
        import replay
        t0 = time.time()
        try:
            r = replay.Replayer(self.session, self.commands, progress=self._progress, drops=self.drops, fast_from=self.fast_from,
                                cancel=lambda: self.cancelled, wait_limit=self.wait_limit)
            ok = r.run()
            if ok:
                ok = r.reach(seq=self.point.get("seq"), req=self.point.get("req"))
            self.diverged = r.diverged
            if ok:
                got = board_hash(self.session.state)
                want = self.point.get("sum")
                if want and got != want:
                    self.mismatch = "the rebuilt board is not the one you had at that moment"
                    if self.expected is not None:
                        diffs = board_differences(self.expected, board_fingerprint(self.session.state))
                        if diffs:
                            self.mismatch += ": " + "; ".join(diffs)
                    ok = False
            self.ok = ok
        except KeyboardInterrupt:
            self.error = "cancelled"
        except Exception as e:                          # the table must survive whatever happens here
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.seconds = time.time() - t0
            self.finished = True
