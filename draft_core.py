# SPDX-License-Identifier: GPL-3.0-or-later
"""
draft_core.py - the packs and the passing of a Commander cube draft (Round CD1). No pygame, no I/O, no network.

The rules (Comprehensive Rules effective 25 Sep 2026, 903.13, Commander Draft - Karl's answer 3, 9 Oct):
  903.13b  three draft rounds; each player opens a booster, drafts TWO cards, passes the rest; and so on until every card of that
           round has been drafted
  903.13c  round 1 and round 3 pass to the left, round 2 to the right
  903.13d  a player sees only the pack in front of them and their own picks
Manticore's standard (Karl's answers 5 and 6): 4 seats, 3 packs of 20 cards drawn at random from the cube - every card of the 240 is
drafted, so each seat ends with 60. The cube must hold at least seats x packs x pack size cards.
Round CD3, the classic draft (Karl, 12 Oct 2026: "Add classic draft" for the classic stock cubes): 4 seats, 3 packs of 15, ONE card
a pick, the same left-right-left passing - the glossary's Draft: "players choose cards one at a time from booster packs" (CR 25 Sep
2026). 180 cards; each seat ends with 45. Entry-level Cubing has exactly 180 (4 x 3 x 15). SHAPES holds both.

A card here is an int: its index in the list of draftable cards the caller gives (cube_source's playable cards). Packs are made from
`seed` alone, and a draft is rebuilt by playing its log of picks back on the same seed (save / load / resume), like journal.py's games.

Seats can be at different points: each seat has a queue of packs waiting for it (a pack passed to a seat that hasn't finished its
current one waits behind it), as in Forge's network draft host - so a seat may later be a person on another PC (Karl's answer 8: online
later, every seat able to be a person). A round ends when every pack of it is empty; the next round's packs open only then.
"""
from collections import deque, namedtuple
import random

SEATS = 4
PACKS = 3
PACK_SIZE = 20
PICKS = 2                                # 903.13b
DIRECTIONS = (+1, -1, +1)                # 903.13c: left, right, left. "Left" = the next seat number.
SAVE_VERSION = 1
# Round CD3: (packs, pack size, cards a pick) for each draft style (cube_source.STYLES).
SHAPES = {"commander": (PACKS, PACK_SIZE, PICKS), "classic": (3, 15, 1)}

Seat = namedtuple("Seat", "name kind")   # kind: "human" | "ai" (later: "remote")
Pick = namedtuple("Pick", "seat round turn cards pack_before")   # turn: 1-based, within the round, for this seat


class DraftError(Exception):
    """A wrong pick, or a cube too small for the draft."""


class Pack:
    __slots__ = ("id", "round", "opener", "cards")

    def __init__(self, pid, rnd, opener, cards):
        self.id, self.round, self.opener, self.cards = pid, rnd, opener, list(cards)


def cards_needed(seats=SEATS, packs=PACKS, pack_size=PACK_SIZE):
    return seats * packs * pack_size


def shape(style):
    """Round CD3: (packs, pack size, cards a pick) of a draft style; Commander Draft's for anything unknown."""
    return SHAPES.get(style or "commander", SHAPES["commander"])


def needed_for(style, seats=SEATS):
    """Round CD3: the cards a draft of this style needs: 240 for Commander Draft, 180 for the classic draft (4 seats)."""
    packs, size, _picks = shape(style)
    return cards_needed(seats, packs, size)


class Draft:
    """The packs, the queues, the pools and the log. pick() is the only way anything changes."""

    def __init__(self, n_cards, seats, seed, packs=PACKS, pack_size=PACK_SIZE, picks=PICKS):
        if not seats or len(seats) < 2:
            raise DraftError("A draft needs at least two seats.")
        need = cards_needed(len(seats), packs, pack_size)
        if n_cards < need:
            raise DraftError(f"This cube has {n_cards} cards Manticore can use; {len(seats)} seats x {packs} packs x {pack_size} "
                             f"cards needs {need}.")
        if picks < 1:
            raise DraftError("A pick takes at least one card.")
        self.seats = [s if isinstance(s, Seat) else Seat(*s) for s in seats]
        self.seed, self.n_cards, self.packs, self.pack_size, self.picks = seed, n_cards, packs, pack_size, picks
        order = list(range(n_cards))
        random.Random(seed).shuffle(order)
        self._all = []                                           # [round][seat] -> Pack
        k = 0
        for r in range(packs):
            row = []
            for s in range(len(self.seats)):
                row.append(Pack(r * len(self.seats) + s, r, s, order[k:k + pack_size]))
                k += pack_size
            self._all.append(row)
        self.round = 0
        self.queues = [deque() for _ in self.seats]
        self.pools = [[] for _ in self.seats]
        self.turns = [0 for _ in self.seats]                     # picks made this round, per seat
        self.log = []
        self._open_round(0)

    # ---- state ----------------------------------------------------------------------------------------------------------------
    @property
    def n(self):
        return len(self.seats)

    @property
    def done(self):
        return self.round >= self.packs

    def pack_for(self, seat):
        """The cards of the pack in front of this seat (a copy), or None when it is waiting for one (or the draft is over)."""
        q = self.queues[seat]
        return list(q[0].cards) if q else None

    def needed(self, seat):
        """How many cards this seat takes now: two, or what is left of the pack (903.13b)."""
        q = self.queues[seat]
        return min(self.picks, len(q[0].cards)) if q else 0

    def waiting_packs(self, seat):
        return len(self.queues[seat])

    def direction(self, rnd=None):
        return DIRECTIONS[(self.round if rnd is None else rnd) % len(DIRECTIONS)]

    def next_seat(self, seat, rnd=None):
        return (seat + self.direction(rnd)) % self.n

    def label(self, seat):
        """"Pack 2, pick 3 of 10" - pick = this seat's turn in the round (two cards each; round CD3: one each, "of 15")."""
        if self.done:
            return "Draft complete"
        turns = -(-self.pack_size // self.picks)
        return f"Pack {self.round + 1}, pick {self.turns[seat] + 1} of {turns}"

    def seats_to_act(self):
        return [s for s in range(self.n) if self.queues[s]]

    # ---- the one change ---------------------------------------------------------------------------------------------------------
    def pick(self, seat, cards):
        """This seat takes `cards` (ints) from the pack in front of it. Raises DraftError for anything not allowed."""
        if self.done:
            raise DraftError("The draft is over.")
        if not 0 <= seat < self.n:
            raise DraftError(f"There is no seat {seat}.")
        q = self.queues[seat]
        if not q:
            raise DraftError(f"{self.seats[seat].name} has no pack yet.")
        pack = q[0]
        cards = list(cards)
        need = min(self.picks, len(pack.cards))
        if len(cards) != need:
            raise DraftError(f"Take {need} card{'s' if need != 1 else ''} from this pack.")
        if len(set(cards)) != len(cards):
            raise DraftError("The same card twice.")
        missing = [c for c in cards if c not in pack.cards]
        if missing:
            raise DraftError("That card isn't in this pack.")
        before = tuple(pack.cards)
        for c in cards:
            pack.cards.remove(c)
        self.pools[seat].extend(cards)
        self.turns[seat] += 1
        self.log.append(Pick(seat, self.round, self.turns[seat], tuple(cards), before))
        q.popleft()
        if pack.cards:
            self.queues[self.next_seat(seat)].append(pack)
        if self._round_over():
            self._open_round(self.round + 1)
        return self.log[-1]

    def _round_over(self):
        return all(not p.cards for p in self._all[self.round])

    def _open_round(self, r):
        self.round = r
        if r >= self.packs:
            return
        self.turns = [0 for _ in self.seats]
        for s in range(self.n):
            self.queues[s] = deque([self._all[r][s]])

    # ---- saving ----------------------------------------------------------------------------------------------------------------
    def to_dict(self, extra=None):
        d = {"v": SAVE_VERSION, "seed": self.seed, "n_cards": self.n_cards, "packs": self.packs, "pack_size": self.pack_size,
             "picks": self.picks, "seats": [list(s) for s in self.seats], "log": [[p.seat, list(p.cards)] for p in self.log]}
        if extra:
            d["extra"] = extra
        return d

    @classmethod
    def from_dict(cls, d):
        """The same draft, rebuilt from the seed and its picks played back in order."""
        if not isinstance(d, dict) or d.get("v") != SAVE_VERSION:
            raise DraftError("This saved draft is from another version.")
        draft = cls(d["n_cards"], [Seat(*s) for s in d["seats"]], d["seed"], d["packs"], d["pack_size"], d["picks"])
        for seat, cards in d.get("log", ()):
            draft.pick(seat, cards)
        return draft

    # ---- the review after the draft ------------------------------------------------------------------------------------------
    def review(self, seat=None):
        """Every pick in order (or one seat's): Pick(seat, round, turn, cards, pack_before)."""
        return [p for p in self.log if seat is None or p.seat == seat]


def run_ai(draft, chooser, seats=None, limit=10000):
    """Let `chooser(seat, pack, pool, count) -> cards` pick for these seats (default: every "ai" seat) while they have a pack in
    front of them. Returns the picks made. Stops when only other seats can act (a person's turn) or the draft is over."""
    who = set(seats) if seats is not None else {s for s, st in enumerate(draft.seats) if st.kind == "ai"}
    made = []
    for _ in range(limit):
        ready = [s for s in draft.seats_to_act() if s in who]
        if not ready or draft.done:
            break
        for s in ready:
            if draft.done or not draft.queues[s]:
                continue
            made.append(draft.pick(s, chooser(s, draft.pack_for(s), list(draft.pools[s]), draft.needed(s))))
    return made
