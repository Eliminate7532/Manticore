# SPDX-License-Identifier: GPL-3.0-or-later
"""Round STARTER1C (Karl, 8 Nov 2026): every new game's starting deck is exactly 60 cards, and the starting-deck screen's list
never runs under the card preview (1360x840 @200%, 1920x1080 @100%, 2880x1920 @150%)."""
import os
import random
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from rpg import cardpool, economy, newgame, products, rules, starter  # noqa: E402
from tests import test_starter1 as s1  # noqa: E402

OVER = ("J25 Goblins 3", "J25 Modified")          # Forge 9a6f776's two half-decks over 20 cards (23 and 22)


def size(deck):
    return sum(c for _n, c in deck)


@unittest.skipUnless(s1.HAS_RUNTIME, "needs forge_runtime (python setup_forge.py)")
class SixtyTests(unittest.TestCase):
    def test_forges_oversized_halves_are_still_there(self):
        d = s1.data()
        sizes = {h.code: h.size for h in d["decks"] if h.code in OVER}
        self.assertEqual(sizes, {"J25 Goblins 3": 23, "J25 Modified": 22})

    def test_trim_half_cuts_to_twenty_keeping_the_land_share(self):
        d = s1.data()
        for h in d["decks"]:
            new, gone = starter.trim_half(h, d["pool"], d["tiers"])
            if h.size <= 20:
                self.assertIs(new, h)
                self.assertEqual(gone, [])
                continue
            self.assertEqual(new.size, 20, h.code)
            lands = lambda hh: sum(c for n, c in hh.cards if d["pool"].get(n).is_land)  # noqa: E731
            cut_lands = lands(h) - lands(new)
            self.assertEqual(cut_lands, int((h.size - 20) * lands(h) / h.size + 0.5), h.code)
            # only basics among the lands cut; the non-land cut are the weakest copies
            for n, c in gone:
                card = d["pool"].get(n)
                self.assertTrue(card.is_basic or not card.is_land, (h.code, n))
            spells_cut = [n for n, c in gone for _ in range(c) if not d["pool"].get(n).is_land]
            self.assertEqual(sorted(spells_cut),
                             sorted(starter.weakest(h, None, d["pool"], d["tiers"])[:len(spells_cut)]))

    def test_every_sampled_new_game_deck_is_sixty(self):
        bad = []
        for k in (0, 4):
            for picks, seed in s1.samples(n=1500, seed=31):
                deck, _w = s1.build(picks, seed, k)
                if size(deck) != rules.MIN_DECK:
                    bad.append((k, [h.code for h in picks], size(deck)))
        self.assertEqual(bad, [])

    def test_decks_with_the_oversized_halves_are_sixty_and_keep_their_twelve_swaps(self):
        d = s1.data()
        by = {h.code: h for h in d["decks"]}
        rng = random.Random(5)
        n = 0
        for first in OVER:
            for second in [h for h in d["decks"] if h.code != first]:
                picks = [by[first], second]
                if len(products.colours_of(picks, d["pool"])) > newgame.MAX_COLOURS:
                    continue
                free = [h for h in d["decks"] if h.code not in {p.code for p in picks}
                        and len(products.colours_of(picks + [h], d["pool"])) <= newgame.MAX_COLOURS]
                picks.append(rng.choice(free))
                deck, swaps = s1.build(picks, rng.getrandbits(40), 4)
                self.assertEqual(size(deck), 60, [h.code for h in picks])
                self.assertEqual(len(swaps), 12, [h.code for h in picks])
                n += 1
        self.assertGreater(n, 50)
        # both oversized halves together: 23 + 22 + a 20 used to make 65
        third = next(h for h in d["decks"] if h.code not in OVER
                     and len(products.colours_of([by[OVER[0]], by[OVER[1]], h], d["pool"])) <= newgame.MAX_COLOURS)
        deck, _w = s1.build([by[OVER[0]], by[OVER[1]], third], 9, 4)
        self.assertEqual(size(deck), 60)

    def test_the_new_game_flow_and_its_save_are_sixty(self):
        d = s1.data()
        for seed in range(12):
            f = newgame.NewGameFlow(seed=seed, pool=d["pool"], lists=d["lists"], decks=d["decks"])
            f.set_name("Karl")
            f.pick(OVER[seed % 2])
            rng = random.Random(seed)
            while f.step == "pick":
                f.pick(rng.choice(f.offer()).code)
            self.assertEqual(size(f.preview()), 60, [h.code for h in f.picks])
            save = f.finish(1, now=0)
            self.assertEqual(sum(int(c) for _n, c in save.decks[0]["main"]), 60)


# ==================================================================================================================================
SIZES = [((1360, 840), 2.0), ((1920, 1080), None), ((2880, 1920), 1.5)]


@unittest.skipUnless(s1.HAS_RUNTIME, "needs forge_runtime (python setup_forge.py)")
class ScreenTests(unittest.TestCase):
    def ready(self, size, scale, first=None):
        import pygame
        from tests import test_rpg8 as r8
        from tests.test_forge_table import frame
        base = r8.Base("setUp")
        base.setUp()
        self.addCleanup(base.tearDown)
        g = base.gui(size=size, scale=scale)
        f = base.open(g)
        frame(g)
        base.press(g, "new:1")
        for ch in "Karl":
            r8.key(g, getattr(pygame, "K_" + ch.lower()), unicode=ch)
        r8.key(g, pygame.K_RETURN)
        if first:
            f.flow.pick(first)
        while f.stage != "ready":
            r8.click(g, next(r for r, k, _d in f.rows if k == "pick").center)
        frame(g, 2)
        return g, f

    def check(self, size, scale, first=None):
        from tests.test_forge_table import frame, move
        g, f = self.ready(size, scale, first)
        big = f.big_fixed
        self.assertIsNotNone(big)
        lines = f.deck_line_rects
        self.assertTrue(lines)
        for name, r in lines:
            self.assertFalse(r.colliderect(big), (size, name, r, big))
        # every card of the deck has its line (nothing cut off at the area's end)
        self.assertEqual(sorted({n for n, _r in lines}), sorted({n for n, _c in f.flow.preview()}), size)
        # hover every line: the big card is drawn in its strip, and an upgraded line's hover sentence shows
        seen_up = False
        for name, r in lines:
            move(g, r.center)
            frame(g)
            self.assertIsNotNone(f.hover, name)
            self.assertEqual(f.big_drawn, big, name)
            seen_up = seen_up or bool(f.upgrade_hover)
        self.assertTrue(seen_up)

    def test_the_list_never_runs_under_the_preview(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                self.check(size, scale)

    def test_the_largest_decks_fit_too(self):
        for size, scale in SIZES:
            for first in OVER:
                with self.subTest(size=size, scale=scale, first=first):
                    self.check(size, scale, first)


if __name__ == "__main__":
    unittest.main()
