# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG14b: the town's shop, the pack opening, the deck editor and the Collection in DawnLike's window and SDS 8x8 font (the
part of RPG14's spec - "the existing DeckEditor, shop and pack-opening screens open inside it, restyled" - split off).

Checks: every word on those screens is drawn in the pixel font at a whole multiple of 8 px and in a DawnBringer colour; every
button is a DawnLike window; at 5 window sizes and at 200% text nothing runs out of the window and no two lines overlap; a
character the font lacks is drawn plainly; the style is off again outside these screens (and after an error); the result window
over the shop is DawnLike's; the frame drawn at 2x is the 1x frame's pixels doubled.
"""
import os
import re
import sys
import unittest
from unittest import mock

import pygame

import gfx
import rpg_screens as rs
import rpg_ui
from tests import test_rpg8 as r8
from tests.test_forge_table import frame, key, move

SIZES = (((1024, 640), None), ((1366, 768), None), ((1920, 1080), None), ((2560, 1440), None), ((4096, 2019), None),
         ((1360, 840), 2.0))
DB = {tuple(c) for c in (gfx.DB_TEXT, gfx.DB_GOLD, gfx.DB_DIM, gfx.DB_RED, gfx.DB_ORANGE, gfx.DB_GREEN, gfx.DB_BG)}


class Spy:
    """Every call rpg_screens makes to gfx.draw_text in one frame: (text, font, colour, rect)."""

    def __init__(self):
        self.calls = []
        self.real = gfx.draw_text

    def __call__(self, screen, s, x, y, font, colour=gfx.TEXT, anchor="topleft", shadow=False):
        r = self.real(screen, s, x, y, font, colour, anchor, shadow)
        if sys._getframe(1).f_globals.get("__name__") == "rpg_screens":       # not the words printed on a stand-in card
            self.calls.append((s, font, tuple(colour)[:3], pygame.Rect(r)))
        return r


class StyleBase(r8.Base):
    def stages(self, g):
        """(name, how to get there) for every DawnLike stage of the game in slot 1."""
        f = g.modal

        def town(tab):
            def go():
                f.enter_town(g, f.game.world.start_town)
                f.tab = tab
            return go

        def pack():
            f.enter_town(g, f.game.world.start_town)
            f.game.save.gold = 500
            f.shop_do(g, "pack", f.shop.packs()[0][0])

        def deck():
            f.enter_town(g, f.game.world.start_town)
            f.open_deck(g, "town")

        def collection():
            f.set_stage("collection")
        return [("town " + t, town(t)) for t in ("singles", "packs", "lands", "sell")] + \
            [("pack", pack), ("deck", deck), ("collection", collection)]

    def drawn(self, g):
        spy = Spy()
        with mock.patch.object(rs.gfx, "draw_text", spy):
            frame(g)
        return spy.calls


class StyleTests(StyleBase):
    def test_every_word_is_the_pixel_font_in_dawnbringers_colours_and_nothing_runs_out(self):
        r8.new_game(None)
        for size, scale in SIZES:
            g = self.gui(size, scale)
            f = self.open(g, slot=1)
            for what, go in self.stages(g):
                with self.subTest(size=size, scale=scale, stage=what):
                    go()
                    calls = self.drawn(g)
                    self.assertIn(f.stage, rs.DAWN_STAGES)
                    self.assertTrue(calls)
                    whole = f.rect
                    for s, font, colour, r in calls:
                        self.assertIsInstance(font, rpg_ui._Spaced, (what, s))
                        self.assertEqual(font.size("M")[0] % 8, 0, (what, s))
                        self.assertIn(colour, DB, (what, s, colour))
                        self.assertTrue(s.isascii() or all(rpg_ui._drawn(ch) for ch in s if not ch.isascii()), (what, s))
                        self.assertTrue(whole.contains(r), f"{what}: {s!r} at {r} outside {whole}")
                    for i, (a, _fa, _ca, ra) in enumerate(calls):
                        for b, _fb, _cb, rb in calls[i + 1:]:
                            self.assertFalse(ra.inflate(-2, -2).colliderect(rb.inflate(-2, -2)),
                                             f"{what}: {a!r} at {ra} overlaps {b!r} at {rb}")
                    f.sub = None
            self.close(g)

    def test_every_button_is_a_dawnlike_window(self):
        r8.new_game(None)
        g = self.gui((1920, 1080))
        f = self.open(g, slot=1)
        for what, go in self.stages(g):
            with self.subTest(stage=what):
                go()
                windows = []
                real = rpg_ui.window

                def spy(surface, rect, scale=1):
                    windows.append(pygame.Rect(rect))
                    return real(surface, rect, scale)
                with mock.patch.object(rpg_ui, "window", spy):
                    frame(g)
                self.assertTrue(f.buttons)
                for r, n in f.buttons:
                    self.assertIn(r, windows, (what, n))
                self.assertIn(f.rect, windows)                  # the stage's own window
        self.close(g)

    def test_every_button_shows_its_whole_label(self):
        """A button too narrow for its words shows them cut ("W..." for a colour) - every label is drawn whole, at every size."""
        r8.new_game(None)
        for size, scale in SIZES:
            g = self.gui(size, scale)
            f = self.open(g, slot=1)
            for what, go in self.stages(g):
                with self.subTest(size=size, scale=scale, stage=what):
                    go()
                    labels, real = [], rs.DawnGui.draw_button

                    def spy(dg, rect, label, name, *a, **kw):
                        labels.append((label, name))
                        return real(dg, rect, label, name, *a, **kw)
                    with mock.patch.object(rs.DawnGui, "draw_button", spy):
                        shown = {s for s, _f, _c, _r in self.drawn(g)}
                    self.assertTrue(labels)
                    for label, name in labels:
                        self.assertIn(rpg_ui.plain(label), shown, (what, name))
                    f.sub = None
            self.close(g)

    def test_a_shop_line_keeps_its_price_and_the_town_its_buttons_at_every_size(self):
        """DawnLike's letters are wide: at 200% a line cut its price ("tier 1 ...") and the town lost Your deck (the screenshots)."""
        r8.new_game(None)
        for size, scale in SIZES:
            g = self.gui(size, scale)
            f = self.open(g, slot=1)
            col = f.game.save.collection
            for name in list(col.names()):
                if not col.is_free(name):
                    col.add(name, 1)                             # a spare copy, so there is something to sell
            for tab, label in (("singles", "Buy"), ("packs", "Buy and open"), ("lands", "+1"), ("sell", "Sell 1")):
                with self.subTest(size=size, scale=scale, tab=tab):
                    f.enter_town(g, f.game.world.start_town)
                    f.tab = tab
                    if tab == "packs":                                  # Round PACK2: a tap on a wrapper shows its Buy
                        f.shelf_pick = f.shop.shelf()[0][:2]
                    move(g, (2, 2))
                    shown = [s for s, _f, _c, _r in self.drawn(g)]
                    names = [n for _r, n in f.buttons]
                    self.assertIn("leave_town", names)
                    self.assertIn("town_deck", names)
                    rows = shown.count(label)                                # one button with this label on each line
                    self.assertTrue(rows)
                    priced = [s for s in shown if re.search(r"\d+ gold", s) and "Your deck" not in s
                              and s != f"{f.game.save.gold} gold"]                   # Round GOLD1: your gold, a line of its own
                    if tab in ("packs", "singles"):                     # Round PACK2 / the singles gallery (task
                        self.assertEqual(rows, 1, shown)                # bug-1567699607696441414): one Buy (the picked one's);
                                                                        # every tile's price and the details' price whole
                        self.assertTrue(len(priced) >= 2 and all(re.fullmatch(r"\d+ gold", p) for p in priced), shown)
                    else:
                        self.assertEqual(len(priced), rows, shown)           # every line's price whole, not cut to "..."
                    f.sub = None
            self.close(g)

    def test_a_row_under_the_mouse_says_so_in_gold(self):
        """(The singles are a gallery since task bug-1567699607696441414: the Basic lands list is the shop list now.)"""
        r8.new_game(None)
        g = self.gui((1920, 1080))
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        f.tab = "lands"
        frame(g)
        buy = [r for r, n in f.buttons if n.startswith("basic:") and n.endswith(":1")]
        basics = [n for n, _p in f.shop.basics()]
        move(g, (buy[1].x - 300, buy[1].centery))
        calls = self.drawn(g)
        colour = {s: c for s, _f, c, _r in calls}
        self.assertEqual(colour[basics[1]], tuple(gfx.DB_GOLD))
        self.assertEqual(colour[basics[0]], tuple(gfx.DB_TEXT))
        self.close(g)

    def test_the_style_is_off_outside_these_screens_and_after_an_error(self):
        r8.new_game(None)
        g = self.gui((1360, 840))
        f = self.open(g)                                       # the slots screen: Manticore's own look, as before
        calls = self.drawn(g)
        self.assertTrue(calls)
        self.assertFalse(any(isinstance(font, rpg_ui._Spaced) for _s, font, _c, _r in calls))
        self.assertEqual(rs._DAWN["k"], 0)
        f.open_slot(g, 1)
        f.enter_town(g, f.game.world.start_town)
        with mock.patch.object(rs.AdventureFlow, "draw_town", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                f.draw(g)
        self.assertEqual(rs._DAWN["k"], 0)                   # an error mid-stage leaves the old look working
        self.close(g)

    def test_the_result_window_over_the_shop_is_dawnlikes(self):
        r8.new_game(None)
        g = self.gui((1920, 1080))
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        f.result, f.sub = ("Bought", ["Bought Llanowar Elves for 12 gold."], rs.GREEN), "result"
        frame(g)
        self.assertIsInstance(f.message_box, rpg_ui.MessageWindow)
        self.assertEqual([n for _r, n in f.buttons], ["ok"])
        key(g, pygame.K_RETURN)
        self.assertEqual((f.stage, f.sub), ("town", None))
        self.close(g)


class PieceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_a_character_the_font_lacks_is_drawn_plainly(self):
        self.assertEqual(rpg_ui.plain("Lim-Dûl's Vault · 3 left — x…"), "Lim-Dul's Vault - 3 left - x...")
        self.assertEqual(rpg_ui.plain("Jötun Grunt"), "Jotun Grunt")
        self.assertEqual(rpg_ui.plain("☃"), "?")
        s = "Plain ASCII stays (as it is)."
        self.assertIs(rpg_ui.plain(s), s)

    def test_the_spaced_font_has_room_between_lines_but_draws_the_same_letters(self):
        f, plain = rpg_ui.spaced_font(16), rpg_ui.font(16)
        self.assertEqual(f.get_height(), 20)
        self.assertEqual(f.size("Llanowar Elves"), plain.size("Llanowar Elves"))
        a = pygame.image.tobytes(f.render("Elves", False, gfx.DB_TEXT), "RGBA")
        b = pygame.image.tobytes(plain.render("Elves", False, gfx.DB_TEXT), "RGBA")
        self.assertEqual(a, b)

    def test_the_frame_at_2x_is_the_1x_frame_doubled(self):
        one = pygame.Surface((60, 40), pygame.SRCALPHA)
        rpg_ui.window(one, one.get_rect())
        two = pygame.Surface((120, 80), pygame.SRCALPHA)
        rpg_ui.window(two, two.get_rect(), 2)
        for x, y in ((0, 0), (3, 1), (59, 39), (30, 0), (0, 20), (5, 5)):
            self.assertEqual(tuple(two.get_at((2 * x, 2 * y))), tuple(one.get_at((x, y))), (x, y))
            self.assertEqual(tuple(two.get_at((2 * x + 1, 2 * y + 1))), tuple(one.get_at((x, y))), (x, y))
        rpg_ui._WINDOWS.clear()
        with mock.patch.object(pygame.transform, "scale", wraps=pygame.transform.scale) as sc:
            rpg_ui.window(two, two.get_rect(), 2)
            first = sc.call_count
            rpg_ui.window(two, two.get_rect(), 2)
        self.assertGreater(first, 0)
        self.assertEqual(sc.call_count, first)                # drawn once, then kept

    def test_dawngui_sizes_follow_the_tables_small_text(self):
        for small, unit in ((10, 8), (12, 16), (17, 16), (22, 24), (33, 32)):
            gui = mock.Mock()
            gui.L.px = {"small": small}
            d = rs.DawnGui(gui)
            self.assertEqual((d.unit, d.k), (unit, unit // 8))
            self.assertEqual(d.font("title").size("M")[0], 2 * unit)
            self.assertEqual(d.font("tiny").size("M")[0], unit)


if __name__ == "__main__":
    unittest.main()
