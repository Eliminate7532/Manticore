# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 42: the deck screen's Main menu button (Karl, 4 Oct: "The Choose your deck screen needs a button to return to menu").

Esc already went back to the title and main menu when no game was running; the button shows the same way on the screen."""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import boot_screens as fboot
import forge_client as fc
import forge_menu as fmenu
import forge_table as ft
from tests.forge_fake import StubStore
from tests.test_deck_screen import FakeLauncher, TempDecks
from tests.test_forge_table import click, frame, key

SIZES = (((1024, 640), 1.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1280, 720), 1.25), ((1100, 700), 2.0),
         ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((4096, 2019), 1.75))


class MainMenuButtonTests(TempDecks):
    def deck_screen(self, size=(1360, 840), scale=None, title_ok=True):
        """The deck screen as main() reaches it: the title's Play (title_ok), no game running."""
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=size,
                            launcher=FakeLauncher(), deck_dirs=self.dirs)
        if scale is not None:
            gui.text_scale = scale
        gui.title_ok = title_ok
        gui.open_menu()
        frame(gui, 2)
        return gui

    def names(self, gui):
        return [n for _r, n in gui.menu.btns]

    def point(self, gui, name):
        return next(r for r, n in gui.menu.btns if n == name).center

    def test_the_button_goes_back_to_the_title_and_main_menu(self):
        gui = self.deck_screen()
        self.assertIn("main_menu", self.names(gui))
        click(gui, self.point(gui, "main_menu"))
        self.assertIsNone(gui.menu)
        self.assertIsInstance(gui.boot, fboot.BootFlow)
        frame(gui, 2)                                            # and the title draws

    def test_it_does_exactly_what_esc_does(self):
        gui = self.deck_screen()
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.menu)
        self.assertIsInstance(gui.boot, fboot.BootFlow)

    def test_no_button_when_a_game_is_running(self):
        """Over a running game the screen keeps "Back to the game"; the cog's Concede is the way to the main menu."""
        gui = self.deck_screen()
        gui.menu.has_game = True
        frame(gui, 2)
        self.assertNotIn("main_menu", self.names(gui))
        self.assertIn("back", self.names(gui))
        self.assertIsNone(gui.menu.main_menu_rect)

    def test_no_button_when_started_without_the_title(self):
        """The --deck command line and the tests open the deck screen directly: there is no title to go back to."""
        gui = self.deck_screen(title_ok=False)
        self.assertNotIn("main_menu", self.names(gui))
        key(gui, pygame.K_ESCAPE)
        self.assertIsNotNone(gui.menu)                           # as before

    def test_a_click_that_arrives_after_a_game_began_does_nothing(self):
        gui = self.deck_screen()
        gui.menu.has_game = True
        gui.menu.press(gui, "main_menu")
        self.assertIsNotNone(gui.menu)

    def test_it_fits_beside_the_title_and_the_format_switch_at_every_size(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.deck_screen(size, scale)
                m = gui.menu
                r = m.main_menu_rect
                window = pygame.Rect(0, 0, *size)
                self.assertTrue(window.contains(r), r)
                self.assertGreater(m.title_rect.x, r.right)                       # the title starts after the button
                self.assertLess(m.title_rect.right, m.formats_left)               # and ends before "Format"
                for fr in m.format_rects.values():
                    self.assertFalse(r.colliderect(fr))
                    self.assertLess(r.right, fr.x)
                self.assertLess(r.bottom, m.list_rect.y)                          # above the deck list

    def test_the_label_and_title_are_never_cut_short(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                drawn, real = [], fmenu.clip_text

                def spy(text, font, width):
                    out = real(text, font, width)
                    drawn.append((text, out))
                    return out
                with mock.patch.object(fmenu, "clip_text", spy):
                    self.deck_screen(size, scale)
                self.assertIn(("Choose your decks", "Choose your decks"), drawn)
                gui = self.deck_screen(size, scale)
                small = gui.font("small", True)
                self.assertLessEqual(small.size(fmenu.MAIN_MENU_LABEL)[0], gui.menu.main_menu_rect.w - 8)


class VersionTests(unittest.TestCase):
    def test_version(self):
        import version
        self.assertGreaterEqual(tuple(int(p) for p in version.VERSION.split(".")), (0, 28, 46))


if __name__ == "__main__":
    unittest.main()
