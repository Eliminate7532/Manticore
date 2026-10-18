# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CD4a: fewer short AI decks in Commander Draft (the first tuning job in CD1's notes: "107 of 800 decks short").

Measured with tools/draft_sim.py --forge on Commander Cube (cm), 200 drafts x 4 AI seats, Forge fb4d809:
  seed 7     short 107 -> 57;  like people 88% -> 88%;  on colour 80% -> 80%;  legal 800 / 800
  seed 1001  short  99 -> 54   (89% / 80% -> 89% / 81%)
  seed 2001  short 104 -> 78   (89% / 81% -> 89% / 81%)
Three changes: (1) a deck short of spells plays up to 26 lands, the top of the "24 to 26" its source recommends, before a basic
counts as standing in for a spell; (2) choosing the commander weighs a short deck twice as much (SHORT_COST 1.5 -> 3.0); (3) an AI
drafter short of playables of its planned colours takes them more as each pack empties (people take power early in a pack and
playables late) - never at a pack's first pick.

  DeckTests      the land count, the commander choice weight, the classic builder untouched
  DrafterTests   the pull: only late in a pack, only for the plan's non-land cards, only when short, never in a classic draft
  MeasureTests   draft_sim on the made-up cube (always here) and on Commander Cube (the bundled list) against the spec's bars
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import cube_source as cs
import draft_ai as da
import draft_deck as dd
from tests.test_cd1 import cards_of

STOCK_CM = os.path.join(BASE, "stock_cubes", "cm.json")


def spells_of(cards, colour):
    return [i for i, c in enumerate(cards) if tuple(c.identity) == (colour,) and not cs.is_land(c) and not c.commander]


class DeckTests(unittest.TestCase):
    def setUp(self):
        self.cards = cards_of()
        self.z = dd.zscores(self.cards)
        self.legend = next(i for i, c in enumerate(self.cards) if c.name == "W Legend 0")
        self.white = spells_of(self.cards, "W")

    def deck(self, n_spells):
        pool = [self.legend] + self.white[:n_spells]
        return dd.build_for(self.cards, pool, (self.legend,), self.z)

    def lands_in(self, d):
        return len(d.lands) + sum(d.basics.values())

    def test_enough_spells_25_lands(self):
        d = self.deck(40)
        self.assertEqual((d.short, self.lands_in(d)), (0, 25))

    def test_one_spell_short_plays_26_lands_and_is_not_short(self):
        d = self.deck(33)
        self.assertEqual((d.short, self.lands_in(d), len(d.spells)), (0, 26, 33))

    def test_two_short_plays_26_and_one_still_counts(self):
        d = self.deck(32)
        self.assertEqual((d.short, self.lands_in(d), len(d.spells)), (1, 27, 32))

    def test_always_sixty(self):
        for n in (10, 30, 33, 34, 40):
            d = self.deck(n)
            self.assertEqual(1 + len(d.spells) + self.lands_in(d), 60, n)

    def test_an_explicit_land_count_is_kept(self):
        d = dd.build_for(self.cards, [self.legend] + self.white[:20], (self.legend,), self.z, lands=17, size=40)
        self.assertEqual(self.lands_in(d), 17 + d.short)

    def test_choosing_the_commander_weighs_a_short_deck_more(self):
        self.assertEqual(dd.SHORT_COST, 3.0)
        self.assertEqual(dd.LANDS_MAX, 26)

    def test_the_classic_builder_is_not_changed(self):
        pool = list(range(len(self.cards)))[:45]
        d = dd.build_classic(self.cards, pool, self.z)
        self.assertEqual(len(d.lands) + sum(d.basics.values()), dd.CLASSIC_LANDS + d.short)


class DrafterTests(unittest.TestCase):
    def setUp(self):
        self.cards = cards_of()
        self.a = da.Drafter(self.cards, 1)
        self.a.rng.gauss = lambda mu, sigma: 0.0                     # no noise: compare scores exactly
        self.white = spells_of(self.cards, "W")
        self.red = spells_of(self.cards, "R")
        self.pool = self.white[:10] + self.red[:20]                  # 30 cards, only 10 white playables

    def gain(self, i, late, need, pool=None, colours=frozenset("W")):
        pool = self.pool if pool is None else pool
        return self.a.score(i, pool, colours, True, late, need) - self.a.score(i, pool, colours, True, 0.0, 0)

    def test_no_pull_at_a_packs_first_pick(self):
        self.assertEqual(self.gain(self.white[20], 0.0, 24), 0.0)

    def test_the_pull_grows_as_the_pack_empties(self):
        early, late = self.gain(self.white[20], 0.3, 24), self.gain(self.white[20], 0.9, 24)
        self.assertGreater(early, 0.0)
        self.assertGreater(late, early)

    def test_only_the_plans_non_land_cards(self):
        self.assertEqual(self.gain(self.red[0], 0.9, 24), 0.0)
        land = next(i for i, c in enumerate(self.cards) if cs.is_land(c) and "W" in c.identity)
        plan = frozenset(self.cards[land].identity)                  # a plan the land fits: still no pull for a land
        self.assertEqual(self.gain(land, 0.9, 24, colours=plan), 0.0)
        self.assertGreater(self.gain(self.white[20], 0.9, 24, colours=plan), 0.0)

    def test_none_when_not_short_or_too_early(self):
        self.assertEqual(self.gain(self.white[20], 0.9, 0), 0.0)
        self.assertEqual(self.gain(self.white[20], 0.9, 24, pool=self.white[:5]), 0.0)     # 5 cards < NEED_FROM

    def test_choose_works_out_how_short_and_how_late(self):
        seen = []
        real = self.a.score
        self.a.score = lambda i, pool, colours, has, late=0.0, need=0: seen.append((late, need)) or real(i, pool, colours, has,
                                                                                                         late, need)
        self.a.plan = lambda pool: (frozenset("W"), (0,))
        self.a.choose(self.white[20:24], self.pool, 2)
        self.assertEqual({round(l, 2) for l, _n in seen}, {0.8})
        self.assertEqual({n for _l, n in seen}, {da.NEED_SPELLS - 10})

    def test_a_classic_drafter_feels_no_pull(self):
        b = da.Drafter(self.cards, 1, style="classic")
        seen = []
        real = b.score
        b.score = lambda i, pool, colours, has, late=0.0, need=0: seen.append((late, need)) or real(i, pool, colours, has, late, need)
        b.choose(self.white[20:24], self.pool, 1)
        self.assertEqual({(l, n) for l, n in seen}, {(0.0, 0)})


class MeasureTests(unittest.TestCase):
    def test_the_made_up_cube_still_drafts_legal_decks(self):
        import draft_sim as sim
        cards = cards_of()
        m = sim.measure(cards, drafts=10, seed=3)
        self.assertEqual(m["legal"], m["seats"])
        self.assertEqual(m["no_commander"], 0)

    @unittest.skipUnless(os.path.isfile(STOCK_CM), "no bundled Commander Cube list")
    def test_commander_cube_meets_the_specs_bars(self):
        import draft_sim as sim
        cards = list(sim.load_cards(STOCK_CM)[1])
        m = sim.measure(cards, drafts=100, seed=7)
        seats = m["seats"]
        self.assertEqual(m["legal"], seats)
        self.assertLessEqual(m["short_decks"], 0.10 * seats, m["short_decks"])         # the spec's bar: 10% or fewer
        self.assertGreaterEqual(m["early_top"], 0.85)
        self.assertGreaterEqual(m["late_on_colour"], 0.75)


if __name__ == "__main__":
    unittest.main()
