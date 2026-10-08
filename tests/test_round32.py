# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 32: Karl's notes from a game on 3 Oct 2026 (the Menace tag: tests/test_menace_tag.py; Concede and return to the main
menu: tests/test_round8.py).

- "Menus and UI elements should be in the Avqest font": gui.ui_font() (the display face at a font key's size) for every button,
  the settings pop-up's words, the phase bar, the prompt's headline and the log heading; gfx.DisplayFont draws each run of a
  string in the face that can draw it, so the digits stay in the body face (K3) without sending the whole label there.

- The 4-player loading screen: "fix 4 player loading screen, cards out of frame". gfx.card_in_frame draws its frame outside the
  card (3% of the card's width, at least 4 px, each side) and the cards were as wide as their seats, so at 4096x2160 / 175% the
  outer frames ran past the window's edges and every frame ran into the "VS" between the seats.
"""
import os
import sys
import unittest
import unittest.mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import flow_screens as flow
import gfx
from tests.test_ad2b import make_gui
from tests.test_forge_table import click, frame, make_gui as table_gui, point_for

SEATS = [("You", ["Valgavoth, Harrower of Souls"], "Valgavoth, Harrower of Souls"),
         ("AI 1", ["Kinnan, Bonder Prodigy"], "Kinnan NBC (sample)"),
         ("AI 2", ["Lathril, Blade of the Elves"], "Tap for Mana, Tap for Violence (Typal)"),
         ("AI 3", ["Teysa Karlov"], "Organ Harvesting for Fun & Profit (Aristocrats)")]
SIZES = (((4096, 2160), 1.75), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1366, 768), 1.0), ((1100, 700), 2.0), ((2560, 1440), 1.5))


def frames(rects):
    """The stone frames gfx.card_in_frame draws around these card rects."""
    return [r.inflate(2 * max(4, int(r.w * 0.03)), 2 * max(4, int(r.w * 0.03))) for r in rects]


class VsFitTests(unittest.TestCase):
    def draw(self, seats, size, scale):
        gui = make_gui(None, size, scale)
        gui.vs = flow.VsShow(seats)
        frame(gui, 2)
        return gui

    def test_every_frame_is_inside_the_window_and_none_touch(self):
        for n in (2, 3, 4):
            for size, scale in SIZES:
                with self.subTest(players=n, size=size, scale=scale):
                    gui = self.draw(SEATS[:n], size, scale)
                    fr = frames(gui.vs.card_rects)
                    self.assertEqual(len(fr), n)
                    for f in fr:
                        self.assertTrue(pygame.Rect(0, 0, *size).contains(f), (f, size))
                    for a in range(n):
                        for b in range(a + 1, n):
                            self.assertFalse(fr[a].colliderect(fr[b]), (fr[a], fr[b]))

    def test_partners_stay_inside_too(self):
        seats = [("You", ["Tymna the Weaver", "Thrasios, Triton Hero"], "")] + SEATS[1:]
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.draw(seats, size, scale)
                for f in frames(gui.vs.card_rects):
                    self.assertTrue(pygame.Rect(0, 0, *size).contains(f), (f, size))

    def test_the_cards_are_still_big(self):
        """The fix takes only the frame's room: at Karl's size the 4-player cards stay over 1000 px tall."""
        gui = self.draw(SEATS, (4096, 2160), 1.75)
        self.assertGreater(min(r.h for r in gui.vs.card_rects), 1000)


class DisplayFontTests(unittest.TestCase):
    def setUp(self):
        self.d = gfx.get_font(40, True, "display")
        self.body = gfx.get_font(40, True)

    def test_it_is_a_display_font_with_its_body_twin(self):
        self.assertIsInstance(self.d, gfx.DisplayFont)
        self.assertIs(self.d.twin, self.body)
        self.assertIs(gfx.get_font(15, role="display"), gfx.get_font(15))        # still: under 16 px it is the body font

    def test_digits_and_plus_minus_are_runs_in_the_body_face(self):
        self.assertEqual(self.d.runs("Turn 7"), [("Turn ", self.d.face), ("7", self.body)])
        self.assertEqual(self.d.runs("Main 1"), [("Main ", self.d.face), ("1", self.body)])
        self.assertEqual(self.d.runs("A+"), [("A", self.d.face), ("+", self.body)])
        self.assertEqual(self.d.runs("Report a bug   (F8)"),
                         [("Report a bug   (F", self.d.face), ("8", self.body), (")", self.d.face)])
        self.assertEqual(self.d.runs("175%"), [("175%", self.body)])          # no letter: all body
        self.assertEqual(self.d.runs("Settings"), [("Settings", self.d.face)])

    def test_size_is_what_render_draws(self):
        for text in ("Turn 7", "Settings", "A+", "Report a bug   (F8)", "175%"):
            with self.subTest(text=text):
                self.assertEqual(self.d.render(text, True, (255, 255, 255)).get_width(), self.d.size(text)[0])

    def test_mixed_runs_fit_in_the_drawn_height(self):
        """A caller that renders straight from the font (not through draw_text) gets a picture as tall as get_height(), and a
        string with no letter is exactly what the body face draws."""
        self.assertEqual(self.d.render("Turn 7", True, (255, 255, 255)).get_height(), self.d.get_height())
        self.assertGreaterEqual(self.d.get_height(), max(self.d.face.get_height(), self.body.get_height()))
        self.assertEqual(self.d.render("40", True, (255, 255, 255)).get_size(), self.body.render("40", True, (255, 255, 255)).get_size())

    def test_pick_font_keeps_it_for_any_text_with_a_letter(self):
        self.assertIs(gfx.pick_font(self.d, "Turn 7"), self.d)
        self.assertIs(gfx.pick_font(self.d, "40"), self.body)


class UiFontTests(unittest.TestCase):
    def test_buttons_settings_phase_bar_and_headline_use_the_display_face(self):
        gui = table_gui("main1_start", (1920, 1080), 1.0)
        seen = []
        real = gfx.draw_text

        def spy(screen, s, x, y, font, *a, **kw):
            seen.append((s, font))
            return real(screen, s, x, y, font, *a, **kw)
        import forge_table as ft
        import forge_settings as fset
        with unittest.mock.patch.object(ft, "draw_text", side_effect=spy), unittest.mock.patch.object(fset, "draw_text", side_effect=spy):
            click(gui, point_for(gui, "button", name="settings"))
            frame(gui, 1)
        fonts = {s: f for s, f in seen}
        for text in ("Settings", "DISPLAY", "Text size", "Volume", "Compact: Off", "Log: Always", "Pass priority", "Upkeep", "YOUR TURN",
                     "Game log"):
            with self.subTest(text=text):
                self.assertIn(text, fonts)
                self.assertIsInstance(fonts[text], gfx.DisplayFont, text)
        headline = next(s for s in fonts if s.startswith("Your priority"))
        self.assertIsInstance(fonts[headline], gfx.DisplayFont)
        hint = next(s for s in fonts if s.startswith("Play a land"))
        self.assertNotIsInstance(fonts[hint], gfx.DisplayFont)                  # the hint sentence stays body text


if __name__ == "__main__":
    unittest.main()
