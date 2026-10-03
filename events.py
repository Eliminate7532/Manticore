# SPDX-License-Identifier: GPL-3.0-or-later
"""
events.py - turns Forge's game events (bridge protocol 2: {"t": "event", "seq": N, "kind": ...}) into "beats" the table can animate and
play sounds for.

Two jobs:
  * Wait for the board. An event arrives about 30-60 ms BEFORE the snapshot that shows its result (the bridge sends events at once and
    snapshots after a 40 ms quiet period). Each snapshot carries "eventSeq", the newest event it already includes; ready() only hands
    out events with seq <= eventSeq, so an animation always has the card's new place on screen to go to.
  * Keep combo loops readable. A Kinnan + Basalt Monolith loop can send hundreds of tap / untap / mana events a second. Events of the same
    kind on the same card within COALESCE seconds become ONE beat with a count; more than FLURRY_COUNT beats of one kind within
    FLURRY_WINDOW mark the rest as `flurry` (one sound, no per-card animation); events older than MAX_BACKLOG are too late to animate and
    come out with `stale=True` (sounds skip them too, except the few kinds in ALWAYS).

No pygame, no clock: `now` is passed in.
"""
from collections import deque

COALESCE = 0.25
FLURRY_COUNT = 12
FLURRY_WINDOW = 0.5
MAX_BACKLOG = 1.5
ALWAYS = {"life", "damage_player", "outcome", "turn"}          # never dropped as stale (they change what the player must know)


class Beat:
    """One thing to show / play. kind: the event kind; card: card id or None; count: how many events it stands for; data: the first event's
    fields; last: the newest event's fields (e.g. the final life total); seq: the newest seq it covers."""
    __slots__ = ("kind", "card", "count", "data", "last", "seq", "rx", "flurry", "stale")

    def __init__(self, ev, rx):
        self.kind, self.card, self.count = ev.get("kind"), ev.get("card"), 1
        self.data = self.last = ev
        self.seq, self.rx, self.flurry, self.stale = ev.get("seq", 0), rx, False, False

    def key(self):
        d = self.data
        return (self.kind, self.card, d.get("tapped"), d.get("from"), d.get("to"), d.get("player"), d.get("counter"))

    def __repr__(self):
        return f"Beat({self.kind}, card={self.card}, x{self.count}{', flurry' if self.flurry else ''}{', stale' if self.stale else ''})"


class EventRouter:
    def __init__(self):
        self.pending = deque()            # (event dict, time received), oldest first
        self.last_seq = 0                 # newest seq handed out
        self.gaps = 0                     # how many times a seq number was skipped (desync / lost line: reported by the perf HUD)
        self.recent = {}                  # kind -> deque of beat times (for the flurry rule)
        self.open = {}                    # key -> Beat still collecting repeats (COALESCE window)

    def feed(self, event, now):
        seq = event.get("seq", 0)
        if self.pending:
            prev = self.pending[-1][0].get("seq", 0)
        else:
            prev = self.last_seq
        if prev and seq != prev + 1:
            self.gaps += 1
        self.pending.append((event, now))

    def synthetic(self, kind, now, **data):
        """For an old bridge (protocol 1) the table makes its own events from snapshot changes (life went down, a card arrived ...).
        They are ready at once (they came from a snapshot)."""
        ev = dict(data, kind=kind, seq=0, synthetic=True)
        return self._beats([(ev, now)], now)

    def ready(self, event_seq, now):
        """Beats for every pending event the latest snapshot already shows (seq <= event_seq). event_seq None (old bridge): nothing."""
        if event_seq is None:
            return []
        out = []
        while self.pending and self.pending[0][0].get("seq", 0) <= event_seq:
            out.append(self.pending.popleft())
        if out:
            self.last_seq = out[-1][0].get("seq", self.last_seq)
        return self._beats(out, now)

    def _beats(self, items, now):
        beats = []
        for ev, rx in items:
            b = Beat(ev, rx)
            if now - rx > MAX_BACKLOG and b.kind not in ALWAYS:
                b.stale = True
            key = b.key()
            prev = self.open.get(key)
            if prev is not None and rx - prev.rx <= COALESCE:
                prev.count += 1                          # same thing again on the same card: fold into the beat already handed out
                prev.last, prev.seq, prev.rx = ev, b.seq, rx
                continue
            times = self.recent.setdefault(b.kind, deque())
            while times and now - times[0] > FLURRY_WINDOW:
                times.popleft()
            times.append(now)
            if len(times) > FLURRY_COUNT:
                b.flurry = True
            self.open[key] = b
            beats.append(b)
        for key in [k for k, b in self.open.items() if now - b.rx > COALESCE]:
            del self.open[key]
        return beats
