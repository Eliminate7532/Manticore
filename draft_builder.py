# SPDX-License-Identifier: GPL-3.0-or-later
"""
draft_builder.py - Round CD4: building your drafted deck by hand (the model; draft_screens.py draws it). No pygame, no I/O.

Karl's answer 9 (spec docs/incoming/cd1/SPEC_CUBE_DRAFT_2026-10-09.md): "By hand, Arena-style, plus an Auto-build button". The spec's
CD4 section: your pool with filters (colour, type, mana value), the deck as mana-value columns, a commander slot chosen from the
pool's commanders (two for a legal pair or a Background), cards outside the commander's colours greyed, basic land steppers for the
commander's colours (Wastes for a colourless one), Suggest lands (draft_deck.split_basics to reach 60), a "54 / 60" counter,
Auto-build (draft_deck.build for your pool, keeping your commander if you chose one). Only a commander problem stops Start (as
everywhere in Manticore, BAN1/D7); any other problem is said and the deck still plays.

A classic draft's deck (round CD3) has no commander: any colours, 40 cards, the cube's own basic lands (draft_pod.Pod.station).
Cards are card ints (the pod's numbering); basics are names with a count, free (the rules' glossary, "Draft"; CD1's notes).
"""
import cube_source as cs
import draft_deck as dd

MV_COLUMNS = ("0-1", "2", "3", "4", "5", "6", "7+")            # the deck's columns of spells, then "Lands"
COLOUR_FILTERS = ("W", "U", "B", "R", "G", "C", "M")           # C = colourless, M = more than one colour
TYPE_FILTERS = ("creature", "noncreature", "land")
MV_FILTERS = MV_COLUMNS


def mv_column(card):
    if cs.is_land(card):
        return "Lands"
    mv = int(card.cmc or 0)
    if mv <= 1:
        return "0-1"
    return "7+" if mv >= 7 else str(mv)


def type_of(card):
    if cs.is_land(card):
        return "land"
    return "creature" if "creature" in (card.type_line or "").lower().split("//")[0] else "noncreature"


def colour_key(card):
    ident = tuple(card.identity or ())
    if not ident:
        return "C"
    return ident[0] if len(ident) == 1 else "M"


class Builder:
    """Your deck while you build it. `deck` (a draft_deck.Deck, or None) is where it starts."""

    def __init__(self, pod, deck=None, seat=0):
        self.pod = pod
        self.cards = pod.cards
        self.seat = seat
        self.pool = list(pod.pool(seat))
        self.classic = bool(getattr(pod, "classic", False))
        self.size = dd.CLASSIC_SIZE if self.classic else dd.DECK_MIN
        self.station = dict(pod.station) if self.classic else dict(cs.BASIC_NAME)
        self.commanders, self.main, self.basics = [], [], {}
        self.filters = {"colour": set(), "type": set(), "mv": set()}
        self.changed = False
        if deck is not None:
            self.load(deck)

    # ---- state -----------------------------------------------------------------------------------------------------------
    def load(self, deck):
        self.commanders = [i for i in deck.commanders if i in self.pool]
        self.main = [i for i in list(deck.spells) + list(deck.lands) if i in self.pool]
        self.basics = {n: k for n, k in (deck.basics or {}).items() if k > 0}
        self.changed = False

    def load_names(self, commanders, names):
        """From a saved deck file (DeckEntry.commanders, DeckEntry.deck): names matched to pool cards, basics counted. Returns
        the names that are neither in the pool (any more copies) nor a basic land."""
        left = list(self.pool)
        by_name = {}
        for i in left:
            by_name.setdefault(self.cards[i].name.lower(), []).append(i)
        self.commanders, self.main, self.basics, lost = [], [], {}, []
        basic_names = {n.lower(): n for n in self.basic_names(every=True)}
        for n in commanders:
            got = by_name.get(n.lower()) or []
            (self.commanders.append(got.pop(0)) if got else lost.append(n))
        for n in names:
            got = by_name.get(n.lower()) or []
            if got:
                self.main.append(got.pop(0))
            elif n.lower() in basic_names:
                key = basic_names[n.lower()]
                self.basics[key] = self.basics.get(key, 0) + 1
            else:
                lost.append(n)
        self.changed = False
        return lost

    def identity(self):
        """The colours a card may have: the commanders' identity, or (classic) None - any colour."""
        if self.classic:
            return None
        return dd.identity_of(self.cards, self.commanders) if self.commanders else frozenset()

    def colours_played(self):
        """The colours of the deck's cards (a classic deck's colours; a commander deck's identity)."""
        ident = self.identity()
        if ident is not None:
            return frozenset(ident)
        out = set()
        for i in self.main:
            out |= set(self.cards[i].identity or ())
        return frozenset(out)

    def fits(self, i):
        ident = self.identity()
        if ident is None:
            return True
        if not self.commanders:
            return False
        return dd.fits(self.cards[i], ident)

    def in_deck(self, i):
        return i in self.main or i in self.commanders

    def total(self):
        return len(self.commanders) + len(self.main) + sum(self.basics.values())

    def counter(self):
        return f"{self.total()} / {self.size}"

    # ---- the pool and its filters -----------------------------------------------------------------------------------------
    def available(self):
        """Pool cards not in the deck, filtered, by mana value then name."""
        out = []
        taken = list(self.main) + list(self.commanders)
        for i in self.pool:
            if i in taken:
                taken.remove(i)                              # one copy of a name in the deck uses one copy from the pool
                continue
            if self.shown(i):
                out.append(i)
        return sorted(out, key=lambda i: (cs.is_land(self.cards[i]), self.cards[i].cmc, self.cards[i].name))

    def shown(self, i):
        c = self.cards[i]
        f = self.filters
        if f["colour"] and colour_key(c) not in f["colour"]:
            return False
        if f["type"] and type_of(c) not in f["type"]:
            return False
        if f["mv"] and (cs.is_land(c) or mv_column(c) not in f["mv"]):
            return False
        return True

    def toggle_filter(self, kind, value):
        s = self.filters[kind]
        s.symmetric_difference_update({value})

    def clear_filters(self):
        for s in self.filters.values():
            s.clear()

    # ---- the deck -------------------------------------------------------------------------------------------------------------
    def columns(self):
        """{"0-1": [ints], ..., "7+": [...], "Lands": [...]} - the deck's cards (commanders apart), each column by name."""
        out = {k: [] for k in MV_COLUMNS + ("Lands",)}
        for i in self.main:
            out[mv_column(self.cards[i])].append(i)
        for k in out:
            out[k].sort(key=lambda i: (self.cards[i].cmc, self.cards[i].name))
        return out

    def why_not(self, i):
        """Why a pool card can't go in, in words, or None."""
        if not self.classic and not self.commanders:
            return "Choose a commander first: the deck's colours come from it."
        if not self.fits(i):
            return f"{self.cards[i].name} is outside your commander's colours."
        return None

    def add(self, i):
        if i not in self.pool or self.why_not(i):
            return False
        used = self.main.count(i) + self.commanders.count(i)
        if used >= self.pool.count(i):
            return False
        self.main.append(i)
        self.changed = True
        return True

    def remove(self, i):
        if i in self.main:
            self.main.remove(i)
            self.changed = True
            return True
        return False

    def basic_names(self, every=False):
        """The basic lands this deck may add, in WUBRG order: the commander's colours' (Wastes when it is colourless), or the
        classic cube's own (its station). every=True: every basic name a saved deck of this pod could hold."""
        order = "WUBRG"
        if every:
            names = list(cs.BASIC_NAME.values()) + [n for n in self.station.values() if n not in cs.BASIC_NAME.values()]
            return names
        if self.classic:
            cols = sorted(self.station, key=lambda c: order.find(c) if c else 9)
            return [self.station[c] for c in cols]
        if not self.commanders:
            return []
        ident = self.identity()
        names = [cs.BASIC_NAME[c] for c in order if c in ident]
        return names or [cs.BASIC_NAME[""]]

    def step_basic(self, name, delta):
        if name not in self.basic_names():
            return False
        n = max(0, self.basics.get(name, 0) + delta)
        if n:
            self.basics[name] = n
        else:
            self.basics.pop(name, None)
        self.changed = True
        return True

    def suggest_lands(self):
        """Basics to reach the deck size: draft_deck.split_basics over the spells' mana symbols (the spec: "to reach 60")."""
        need = self.size - len(self.commanders) - len(self.main)
        spells = [i for i in self.main if not cs.is_land(self.cards[i])]
        if self.classic:
            ident = self.colours_played() & frozenset(c for c in self.station if c)
            names = self.station
        else:
            ident = self.identity()
            names = None
        self.basics = dd.split_basics(self.cards, spells, ident, need, names) if need > 0 else {}
        self.changed = True

    def commander_options(self):
        return [] if self.classic else dd.commander_options(self.cards, self.pool)

    def set_commanders(self, opt):
        """Choose the commander (or pair). Cards that no longer fit leave the deck; basics of colours it lacks too. Returns the
        names of the cards taken out."""
        if self.classic:
            return []
        self.commanders = list(opt)
        for i in opt:
            if i in self.main:
                self.main.remove(i)
        out = [self.cards[i].name for i in self.main if not self.fits(i)]
        self.main = [i for i in self.main if self.fits(i)]
        allowed = set(self.basic_names())
        self.basics = {n: k for n, k in self.basics.items() if n in allowed}
        self.changed = True
        return out

    def auto_build(self):
        """draft_deck.build (or the classic builder) for your pool - keeping the commander you chose, if any."""
        pod = self.pod
        if self.classic:
            deck = dd.build_classic(self.cards, self.pool, pod.z, station=pod.station)
        else:
            deck = dd.build(self.cards, self.pool, pod.z, commanders=tuple(self.commanders) or None)
        if deck is None:
            return False
        self.load(deck)
        self.changed = True
        return True

    # ---- out -------------------------------------------------------------------------------------------------------------------
    def deck(self):
        """A draft_deck.Deck for saving (draft_pod.Pod.save_decks / deck_text) and for its checks."""
        spells = tuple(i for i in self.main if not cs.is_land(self.cards[i]))
        lands = tuple(i for i in self.main if cs.is_land(self.cards[i]))
        ident = self.colours_played()
        return dd.Deck(tuple(self.commanders), spells, lands, dict(self.basics), ident, 0.0, 0)

    def problems(self):
        return self.pod.problems(self.seat, self.deck())

    def blocking(self):
        """Only a commander problem stops the deck (BAN1/D7): the sentences that name the commander."""
        if self.classic:
            return []
        return dd.check(self.cards, self.pool, list(self.commanders), [], size=0)    # the commander checks alone
