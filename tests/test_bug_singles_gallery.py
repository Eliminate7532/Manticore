# SPDX-License-Identifier: GPL-3.0-or-later
"""Task bug-1567699607696441414 - kr.bot.01, 5 Nov 2026 (posted by hand, a screenshot of Lantern Hollow > Singles): "the shop page
for singles should show each card like a gallery". Karl, 7 Nov: "Gallery replaces list" - the Singles tab is a grid of card
pictures like the Packs shelf, the price and the copies left under each card; a tap on a card shows it at the right with Buy.
No list view is kept."""
import unittest
from unittest import mock

import rpg_screens as rs
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame
from tests.test_rpg14b import SIZES, Spy, StyleBase


class SinglesGalleryTests(StyleBase):
    def town(self, size=(1360, 840), scale=None):
        g = self.gui(size, scale)
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        f.tab = "singles"
        f.game.save.gold = 1000
        frame(g)
        return g, f

    def tiles(self, f):
        return [(r, slot) for r, kind, slot in f.rows if kind == "single"]

    def test_each_single_is_a_card_tile_with_its_price_and_copies_left_under_it(self):
        r8.new_game(None)
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                g, f = self.town(size, scale)
                offers = {o.slot: o for o in f.shop.singles()}
                spy = Spy()
                with mock.patch.object(rs.gfx, "draw_text", spy):
                    frame(g)
                tiles = self.tiles(f)
                self.assertTrue(tiles, "no card tiles: the singles are still a list")
                drawn = [(s, r) for s, _f, _c, r in spy.calls]
                for rect, slot in tiles:
                    o = offers[slot]
                    under = [s for s, r in drawn if rect.colliderect(r)]
                    self.assertIn(f"{o.price} gold", under, (slot, under))
                    self.assertIn(f"{o.left} left" if o.left else "sold out", under, (slot, under))
                buys = [n for _r, n in f.buttons if n.startswith("buy:")]
                self.assertEqual(len(buys), 1, buys)          # one Buy: the picked card's, in the details
                self.assertNotIn("Buy", [s for s, r in drawn if any(t.colliderect(r) for t, _s in tiles)])
                self.close(g)

    def test_a_tap_on_a_card_shows_it_with_its_buy_and_buy_buys_that_card(self):
        r8.new_game(None)
        g, f = self.town()
        tiles = self.tiles(f)
        offers = {o.slot: o for o in f.shop.singles()}
        rect, slot = next((r, s) for r, s in tiles if s != tiles[0][1] and offers[s].left > 0)
        click(g, rect.center)
        frame(g)
        self.assertEqual(f.singles_pick, slot)
        spy = Spy()
        with mock.patch.object(rs.gfx, "draw_text", spy):
            frame(g)
        self.assertIn(offers[slot].name, [s for s, *_ in spy.calls])
        self.assertEqual([n for _r, n in f.buttons if n.startswith("buy:")], [f"buy:{slot}"])
        have = f.game.save.collection.count(offers[slot].name) or 0
        gold = f.game.save.gold
        self.press(g, f"buy:{slot}")
        self.assertEqual(f.game.save.gold, gold - offers[slot].price)
        self.assertEqual(f.game.save.collection.count(offers[slot].name), have + 1)
        after = next(o for o in f.shop.singles() if o.slot == slot)
        self.assertEqual(after.left, offers[slot].left - 1)
        spy = Spy()
        with mock.patch.object(rs.gfx, "draw_text", spy):
            frame(g)
        tile = next(r for r, s in self.tiles(f) if s == slot)
        under = [s for s, _f, _c, r in spy.calls if tile.colliderect(r)]
        self.assertIn(f"{after.left} left" if after.left else "sold out", under)    # the copies LEFT, not the copies stocked
        self.close(g)

    def test_a_card_you_cannot_afford_has_no_buy_button(self):
        r8.new_game(None)
        g, f = self.town()
        f.game.save.gold = 0
        frame(g)
        self.assertEqual([n for _r, n in f.buttons if n.startswith("buy:")], [])
        self.close(g)


if __name__ == "__main__":
    unittest.main()
