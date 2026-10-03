# SPDX-License-Identifier: GPL-3.0-or-later
"""
soak_boards.py - a mid-game board for a soak game (Round 28c, SOAK_PLAN item 7).

Half of the nightly soak games start from turn 4-6 instead of turn 1: every seat gets 5-6 lands and 1-2 creatures or
artifacts in play, 5-7 cards in hand and 30-40 life (15-25 in Brawl), all taken from its OWN deck (so the colours are right and every card
is one that deck really plays), the rest of the deck as its library, and its commander back in the command zone. The first
turns of a normal soak game are mostly land drops; a board like this reaches real questions sooner.

It is applied with Forge's own developer "Setup Game State" (the bridge's "setup" command, dev mode only), which clears
EVERY zone of every player first - so each seat's library, hand, battlefield and command zone are all written out here.

No Forge and no pygame needed to BUILD the lines (tests check them offline); only applying them needs the engine.
"""
import random
import re

import card_check
from deck_loader import load_deck

LANDS = (5, 6)
PERMANENTS = (1, 2)
HAND = (5, 7)
LIFE = (30, 40)
LIFE_BY_FORMAT = {"brawl": (15, 25)}     # round FMT1: Brawl starts at 25 (30 in a pod), so a mid-game board has less
TURN = (4, 6)


def mana_value(cost):
    """Forge script ManaCost ("2 G G", "X B B", "W/U W/U", "no cost") -> mana value. X counts 0; a hybrid symbol counts 1
    ("2/W" counts 2)."""
    total = 0
    for sym in (cost or "").split():
        if sym.isdigit():
            total += int(sym)
        elif sym in ("X", "Y", "Z", "no", "cost"):
            continue
        elif re.match(r"^\d+/", sym):
            total += int(sym.split("/")[0])
        else:
            total += 1
    return total


class DeckCards:
    """One deck's cards as Forge knows them: its commanders, and every copy of every other card (so 30 Forests are 30)."""

    def __init__(self, deck_file, runtime=None):
        commanders, deck = load_deck(deck_file)
        profiles = {p.deck_name: p for p in card_check.load_profiles(deck_file, runtime)}
        self.commanders = [profiles[c].name for c in commanders if c in profiles and profiles[c].found]
        self.cards = []                                   # [Profile], one entry per copy
        for name in deck:
            p = profiles.get(name)
            if p is not None and p.found and not p.commander:
                self.cards.append(p)

    @staticmethod
    def is_starter_permanent(p):
        types = p.types.split()
        return ("Creature" in types or "Artifact" in types) and "Land" not in types


def seat_board(cards, rng):
    """(battlefield, hand, library) card names for one seat, from a DeckCards. Works on positions, not Profile objects:
    every copy of a basic land shares one Profile."""
    order = list(range(len(cards.cards)))
    rng.shuffle(order)
    prof = cards.cards
    lands = [i for i in order if prof[i].is_land]
    n_lands = min(rng.randint(*LANDS), len(lands))
    on_field = lands[:n_lands]
    used = set(on_field)
    options = [i for i in order if i not in used and DeckCards.is_starter_permanent(prof[i])
               and mana_value(prof[i].cost) <= n_lands]
    n_perm = min(rng.randint(*PERMANENTS), len(options))
    on_field += options[:n_perm]
    used.update(options[:n_perm])
    rest = [i for i in order if i not in used]
    n_hand = min(rng.randint(*HAND), len(rest))
    hand, library = rest[:n_hand], rest[n_hand:]
    return [prof[i].name for i in on_field], [prof[i].name for i in hand], [prof[i].name for i in library]


def _zone(names):
    return ";".join(n.replace(";", ",") for n in names)


def board_lines(deck_files, rng=None, runtime=None, fmt=None):
    """Setup Game State lines for a whole table, seat 0 (my seat) first, and what they put on each battlefield:
    (lines, {seat: [battlefield names]}). The active player is seat 0, in its first main phase, with no land played yet."""
    rng = rng or random.Random()
    turn = rng.randint(*TURN)
    lines = ["activeplayer=p0", "activephase=MAIN1", "turn=%d" % turn, "removesummoningsickness=true"]
    placed = {}
    for seat, path in enumerate(deck_files):
        cards = DeckCards(path, runtime)
        battlefield, hand, library = seat_board(cards, rng)
        key = "p%d" % seat
        lines += ["%slife=%d" % (key, rng.randint(*LIFE_BY_FORMAT.get(fmt or "commander", LIFE))),
                  "%slandsplayed=0" % key,
                  "%sbattlefield=%s" % (key, _zone(battlefield)),
                  "%shand=%s" % (key, _zone(hand)),
                  "%slibrary=%s" % (key, _zone(library)),
                  "%sgraveyard=" % key]
        if cards.commanders:
            lines.append("%scommand=%s" % (key, ";".join(c + "|IsCommander" for c in cards.commanders)))
        placed[seat] = battlefield
    return lines, placed
