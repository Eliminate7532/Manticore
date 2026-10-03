# SPDX-License-Identifier: GPL-3.0-or-later
"""Round MANA1: mana symbols drawn with the Mana font (gfx.mana_symbol), with the old letter circle as the fallback."""
import hashlib
import json
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame

import gfx

FONT = os.path.join(BASE, "assets", "fonts", "mana.ttf")
SHA = "a23809f7c0af7f9866734216bdd73bce2cfedd67333f5cde86a9ee066fa69819"


def setUpModule():
    pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((10, 10))


def draw(sym, r=16):
    s = pygame.Surface((2 * r + 6, 2 * r + 6), pygame.SRCALPHA)
    gfx.mana_symbol(s, sym, r + 2, r + 2, r)
    return s


def colours(surf):
    w, h = surf.get_size()
    return {tuple(surf.get_at((x, y)))[:3] for x in range(w) for y in range(h) if surf.get_at((x, y))[3] == 255}


class FontTests(unittest.TestCase):
    def test_the_bundled_font_is_the_checked_one(self):
        with open(FONT, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), SHA)

    def test_every_glyph_in_the_table_is_in_the_font(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest("fontTools is not installed (pip install fonttools)")
        cmap = TTFont(FONT).getBestCmap()
        for sym, ch in gfx.MANA_GLYPHS.items():
            with self.subTest(sym=sym):
                self.assertIn(ord(ch), cmap)

    def test_the_licence_is_recorded(self):
        with open(os.path.join(BASE, "licenses", "NOTICES.json"), encoding="utf-8") as f:
            ids = {c["id"]: c for c in json.load(f)["components"]}
        self.assertEqual((ids["font-mana"]["license"], ids["font-mana"]["status"]), ("OFL-1.1", "verified"))
        self.assertEqual(ids["mana-codepoints"]["license"], "MIT")
        self.assertTrue(os.path.isfile(os.path.join(BASE, "licenses", ids["font-mana"]["notice_file"])))


class SymbolTests(unittest.TestCase):
    def setUp(self):
        gfx._MANA_FONTS.clear()

    def test_parts(self):
        self.assertEqual(gfx.mana_parts("w"), ("W",))
        self.assertEqual(gfx.mana_parts("W/U"), ("W", "U"))
        self.assertEqual(gfx.mana_parts("G/U/P"), ("G", "U", "P"))
        self.assertEqual(gfx.mana_parts("1/2"), ("1/2",))

    def test_the_core_symbols_use_the_font(self):
        for sym in ("W", "U", "B", "R", "G", "C", "S", "X", "T", "Q", "E", "0", "7", "20", "W/U", "2/W", "W/P", "G/U/P", "1/2"):
            with self.subTest(sym=sym):
                self.assertIsNotNone(gfx._mana_surface(sym, 14))

    def test_a_coloured_symbol_is_its_colour_with_dark_ink(self):
        found = colours(draw("G"))
        self.assertIn(gfx.MANA_FILL["G"], found)
        self.assertIn(gfx.MANA_INK, found)

    def test_a_hybrid_shows_both_colours(self):
        found = colours(draw("W/U", 20))
        self.assertIn(gfx.MANA_FILL["W"], found)
        self.assertIn(gfx.MANA_FILL["U"], found)

    def test_a_phyrexian_symbol_is_not_the_plain_one(self):
        self.assertNotEqual(pygame.image.tobytes(draw("W/P"), "RGBA"), pygame.image.tobytes(draw("W"), "RGBA"))
        self.assertIn(gfx.MANA_FILL["W"], colours(draw("W/P")))

    def test_numbers_above_twenty_and_unknown_symbols_fall_back_to_letters(self):
        self.assertIsNone(gfx._mana_surface("25", 14))
        self.assertIsNone(gfx._mana_surface("HW", 14))
        self.assertTrue(colours(draw("25")))                    # still drawn, the old way

    def test_no_font_file_falls_back_and_notes_it_once(self):
        gfx._FONT_NOTED.discard("mana")
        with mock.patch.object(gfx, "MANA_FONT_FILE", "not_there.ttf"), mock.patch("crashlog.note") as note:
            gfx._MANA_FONTS.clear()
            self.assertIsNone(gfx._mana_surface("W", 14))
            draw("W")
            draw("U")
        self.assertEqual(note.call_count, 1)
        gfx._MANA_FONTS.clear()

    def test_drawn_symbols_are_cached(self):
        draw("R", 11)
        self.assertIsNotNone(gfx.recall(("mana", "R", 11)))


if __name__ == "__main__":
    unittest.main()
