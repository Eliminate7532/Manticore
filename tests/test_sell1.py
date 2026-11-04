# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SELL1 - Karl, 2 Nov 2026 (Adventure, on his Surface): "Shops in Adventure mode need to let me scroll through all my cards
in the SELL menu. Also I should be able to sell all cards, not just duplicates, and from the starting deck".

1. The shop's lists (and the deck editor's and the Collection) scrolled with the mouse wheel only - a pen or a finger has none.
   Now a press on a list that scrolls waits (Round PEN1's way): a drag moves the list, a press that doesn't move still clicks.
2. A card a saved deck uses could not be sold ("a card your deck still needs can't be sold"), so a new game's cards - all in the
   starting deck - could not be sold at all. Now any card you own can be sold (not a free basic land; no shop buys basic lands);
   copies a deck would be left using that you no longer own come out of that deck, the sideboard first, and the shop says so."""
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import collection, shop
from tests import test_rpg4 as r4
from tests import test_rpg8 as r8
from tests.test_forge_table import frame
from tests.test_rpg14b import SIZES, Spy, StyleBase


class SellRuleTests(unittest.TestCase):
    def shop_for(self, s):
        return shop.Shop(s, "green_town", r4.world(), r4.numbers(), r4.POOL, r4.FakeTiers(), r4.lists())

    def test_a_card_the_deck_uses_can_be_sold_and_comes_out_of_the_deck(self):
        s = r4.new_save(gold=0)
        s.collection.add("Selesnya Guildmage", 2)
        s.decks = [{"name": "Main", "main": [["Selesnya Guildmage", 2], ["Grizzly Bears", 1]], "sideboard": []}]
        sh = self.shop_for(s)
        self.assertEqual(sh.sell("Selesnya Guildmage", 1), 15)
        self.assertEqual((s.gold, s.collection.count("Selesnya Guildmage")), (15, 1))
        self.assertEqual(s.decks[0]["main"], [["Selesnya Guildmage", 1], ["Grizzly Bears", 1]])
        self.assertEqual(sh.taken, [("Main", 1)])
        sh.sell("Selesnya Guildmage", 1)                         # the last copy: the row goes
        self.assertEqual(s.decks[0]["main"], [["Grizzly Bears", 1]])
        self.assertEqual(s.collection.count("Selesnya Guildmage"), 0)

    def test_a_spare_copy_leaves_the_decks_alone(self):
        s = r4.new_save(gold=0)
        s.collection.add("Selesnya Guildmage", 3)
        s.decks = [{"name": "Main", "main": [["Selesnya Guildmage", 2]], "sideboard": []}]
        sh = self.shop_for(s)
        sh.sell("Selesnya Guildmage", 1)
        self.assertEqual((s.decks[0]["main"], sh.taken), ([["Selesnya Guildmage", 2]], []))

    def test_the_sideboard_gives_up_its_copy_first_and_every_deck_is_kept_owned(self):
        s = r4.new_save(gold=0)
        s.collection.add("Selesnya Guildmage", 2)
        s.decks = [{"name": "A", "main": [["Selesnya Guildmage", 1]], "sideboard": [["Selesnya Guildmage", 1]]},
                   {"name": "B", "main": [["Selesnya Guildmage", 2]], "sideboard": []},
                   {"name": "C", "main": [["Grizzly Bears", 4]], "sideboard": []}]
        sh = self.shop_for(s)
        sh.sell("Selesnya Guildmage", 1)
        self.assertEqual((s.decks[0]["main"], s.decks[0]["sideboard"]), ([["Selesnya Guildmage", 1]], []))
        self.assertEqual(s.decks[1]["main"], [["Selesnya Guildmage", 1]])
        self.assertEqual(s.decks[2]["main"], [["Grizzly Bears", 4]])
        self.assertEqual(sh.taken, [("A", 1), ("B", 1)])
        for d in s.decks[:2]:                                           # (C's Bears were never owned in this save)
            self.assertEqual(collection.owned_problems(d, s.collection), [], d["name"])

    def test_what_still_cant_be_sold(self):
        c = collection.Collection({"Grizzly Bears": 4}, pool=r4.POOL)
        decks = [{"name": "Bears", "main": [["Grizzly Bears", 4]], "sideboard": []}]
        self.assertIsNone(collection.sell_problem("Grizzly Bears", 4, c, decks))     # all four, though the deck uses them
        self.assertIn("own 4", collection.sell_problem("Grizzly Bears", 5, c, decks))
        self.assertIn("free basic land", collection.sell_problem("Forest", 1, c, decks))
        s = r4.new_save(gold=0)
        s.collection.add("Chaos Orb", 1)
        with self.assertRaises(shop.ShopError):
            self.shop_for(s).sell("Chaos Orb", 1)                   # no shop buys it: nothing taken out of any deck either
        self.assertEqual(s.collection.count("Chaos Orb"), 1)


class SellScreenTests(StyleBase):
    def town(self, size=(1360, 840), scale=None, tab="sell"):
        g = self.gui(size, scale)
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        f.tab = tab
        frame(g)
        return g, f

    def sell_button(self, f, name=None):
        for rect, n in f.buttons:
            if n.startswith("sell:") and (name is None or n == "sell:" + name):
                return rect, n[5:]
        return None, None

    def test_a_new_games_starting_deck_cards_can_be_sold(self):
        r8.new_game(None)
        g, f = self.town()
        rows = f.sell_list()
        in_deck = [r for r in rows if r[3] >= r[1] and r[2] >= 1]          # every copy owned is in the deck
        self.assertTrue(in_deck)                                          # before SELL1: all of these were greyed
        name = in_deck[0][0]
        deck = f.game.deck()
        size = sum(k for _n, k in deck["main"])
        gold = f.game.save.gold
        rect, _n = self.sell_button(f, name)
        self.assertIsNotNone(rect)
        f.click(g, rect.center, 1)
        frame(g)
        self.assertEqual(sum(k for _n, k in f.game.deck()["main"]), size - 1)
        self.assertGreater(f.game.save.gold, gold)
        text, _colour = f.message
        self.assertIn(f"Sold one {name}", text)
        self.assertIn(f"Taken out of '{deck['name']}': {size - 1} cards now.", text)
        self.assertIn("Add cards before you duel.", text)
        self.assertTrue(f.game.deck_problems())

    def test_the_sell_list_scrolls_with_a_pen_and_a_still_press_still_sells(self):
        r8.new_game(None)
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                g, f = self.town(size, scale)
                rows = len(f.sell_list())
                area, scrolls, step = f._pan_views["town:sell"]
                self.assertTrue(scrolls, rows)                            # a starting deck has more cards than fit
                start = (area.centerx - area.w // 4, area.bottom - step // 2)
                self.assertIsNotNone(f.pan_view(g, start))
                g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1))
                for k in range(1, 9):
                    g.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(start[0], start[1] - k * step // 2),
                                                      rel=(0, 0), buttons=(1, 0, 0)))
                g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(start[0], start[1] - 4 * step), button=1))
                frame(g)
                self.assertEqual(f.scroll["town:sell"], 4)              # dragged up four rows: four rows further down the list
                self.assertIsNone(f.message)                              # nothing was sold by the drag
                rect, name = self.sell_button(f)
                have = f.game.save.collection.count(name)
                g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1))
                g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=rect.center, button=1))
                frame(g)
                self.assertEqual(f.game.save.collection.count(name), have - 1)     # a press that doesn't move: Sell 1
                spy = Spy()
                with mock.patch.object(rs.gfx, "draw_text", spy):
                    frame(g)
                shown = " ".join(t for t, _f, _c, _r in spy.calls)
                self.assertIn("before you duel.", shown)                  # the whole message fits its two lines
                self.close(g)

    def test_the_other_shop_lists_the_deck_editor_and_the_collection_drag_too(self):
        r8.new_game(None)
        g, f = self.town(tab="singles")
        f.game.save.gold = 10000
        self.assertIn("town:singles", f._pan_views)
        f.open_deck(g, "town")
        frame(g)
        self.assertIn("deckpool", f._pan_views)
        self.assertIn("decklist", f._pan_views)
        area, scrolls, _step = f._pan_views["deckpool"]
        self.assertTrue(scrolls)
        self.assertIsNotNone(f.pan_view(g, area.center))
        f.set_stage("collection")
        frame(g)
        self.assertIn("collection", f._pan_views)

    def test_a_list_that_fits_is_clicked_at_once_as_before(self):
        r8.new_game(None)
        g, f = self.town(tab="packs")
        area, scrolls, _step = f._pan_views["town:packs"]
        if not scrolls:
            self.assertIsNone(f.pan_view(g, area.center))


if __name__ == "__main__":
    unittest.main()
