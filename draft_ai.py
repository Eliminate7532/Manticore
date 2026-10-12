# SPDX-License-Identifier: GPL-3.0-or-later
"""
draft_ai.py - AI drafters that pick the way people pick (Round CD1). No pygame, no I/O.

Karl's answer 7 (9 Oct): the AI drafters should be best at drafting like people - the packs you're passed should carry realistic signals
- with their deck strength second. So the card value is Cube Cobra's Elo (cube_source.py: a rating made from real people's picks on
Cube Cobra), and on top of it the two habits people show: they take the strongest cards early and settle into colours as the draft goes
on, and in Commander Draft they make sure of a commander whose colours match what they have.

The score of a card in a pack (all in Elo standard deviations of this cube, draft_deck.zscores):
  its z                                                       how people value it
  + ON_BONUS x commitment       if it fits the planned colours  (colourless cards fit any)
  - OFF_COST x commitment x n   for each of its n colours outside them
  + COMMANDER_BONUS x worry     a commander that fits, while the pool has none that fits
  + a little noise (NOISE)      people don't all pick alike; seeded, so a draft can be rebuilt
commitment: 0 for the first OPEN_CARDS cards, rising to 1 at COMMIT_CARDS. worry: 0 until WORRY_FROM cards, 1 at WORRY_FULL.
The planned colours: the colour identity of the best commander (or pair) in the pool, by the strength of the pool's cards it could play
- or, with no commander yet, the two colours the pool is strongest in.
Every number below is Claude's first setting (9 Oct), to be measured with tools/draft_sim.py and tuned, not a known value.
"""
import random

import draft_deck as dd

OPEN_CARDS = 4                           # the first two picks (four cards): strength only
COMMIT_CARDS = 24                        # fully settled from about the start of pack 2
ON_BONUS = 0.4
OFF_COST = 1.4
COMMANDER_BONUS = 1.5
WORRY_FROM = 12
WORRY_FULL = 36
NOISE = 0.3
PLAN_SUPPORT_FLOOR = 1.0                 # a card's weight in the plan: max(0, z + PLAN_SUPPORT_FLOOR)


def ramp(x, lo, hi):
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


class Drafter:
    """One AI seat's picking. choose(pack, pool, count) -> cards. Deterministic for a seed."""

    def __init__(self, cards, seed, z=None):
        self.cards = cards
        self.z = z if z is not None else dd.zscores(cards)
        self.rng = random.Random(seed)

    # ---- the plan ------------------------------------------------------------------------------------------------------------
    def colour_weights(self, pool):
        w = {c: 0.0 for c in "WUBRG"}
        for i in pool:
            card = self.cards[i]
            if not card.identity:
                continue
            share = max(0.0, self.z[i] + PLAN_SUPPORT_FLOOR) / len(card.identity)
            for col in card.identity:
                w[col] += share
        return w

    def plan(self, pool):
        """(planned colours as a frozenset, the commanders behind it or ()). Empty colours = no plan yet."""
        options = dd.commander_options(self.cards, pool)
        best, best_score = None, None
        for opt in options:
            ident = dd.identity_of(self.cards, opt)
            support = sum(max(0.0, self.z[i] + PLAN_SUPPORT_FLOOR) for i in pool
                          if i not in opt and dd.fits(self.cards[i], ident) and self.cards[i].identity)
            score = support + dd.COMMANDER_WEIGHT * sum(self.z[i] for i in opt)
            if best_score is None or score > best_score:
                best, best_score = opt, score
        if best is not None:
            return dd.identity_of(self.cards, best), best
        w = self.colour_weights(pool)
        top = [c for c in sorted(w, key=lambda c: -w[c]) if w[c] > 0][:2]
        return frozenset(top), ()

    # ---- the pick --------------------------------------------------------------------------------------------------------------
    def score(self, i, pool, colours, has_commander):
        card = self.cards[i]
        s = self.z[i]
        n = len(pool)
        commit = ramp(n, OPEN_CARDS, COMMIT_CARDS) if colours else 0.0
        if commit:
            off = len(set(card.identity) - set(colours))
            s += ON_BONUS * commit if off == 0 else -OFF_COST * commit * off
        if card.commander and not has_commander:
            worry = ramp(n, WORRY_FROM, WORRY_FULL)
            if worry and (not colours or dd.fits(card, colours) or len(set(card.identity) - set(colours)) <= 1):
                s += COMMANDER_BONUS * worry
        return s + self.rng.gauss(0.0, NOISE)

    def choose(self, pack, pool, count):
        colours, cmd = self.plan(pool)
        has_commander = bool(cmd)
        ranked = sorted(pack, key=lambda i: -self.score(i, pool, colours, has_commander))
        return ranked[:count]


def chooser(cards, seed, z=None):
    """For draft_core.run_ai: one Drafter per seat, made on first use from seed and seat."""
    z = z if z is not None else dd.zscores(cards)
    seats = {}

    def choose(seat, pack, pool, count):
        if seat not in seats:
            seats[seat] = Drafter(cards, seed * 1000003 + seat, z)
        return seats[seat].choose(pack, pool, count)
    return choose
