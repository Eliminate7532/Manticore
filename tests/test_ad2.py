# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AD2: stone frames, and the studio splash -> title and main menu (boot_screens.py)."""
import inspect
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import boot_screens as fboot
import forge_client as fc
import forge_table as ft
import gfx
import licenses_view
from tests.forge_fake import StubStore
from tests.test_deck_screen import FakeLauncher, TempDecks
from tests.test_forge_table import frame, key


class FrameTests(unittest.TestCase):
    def test_panels_are_framed_and_pills_keep_their_shape(self):
        self.assertTrue(gfx.framed(pygame.Rect(0, 0, 300, 200), 12))
        self.assertFalse(gfx.framed(pygame.Rect(0, 0, 80, 30), 15))           # a pill: radius about half its height
        self.assertFalse(gfx.framed(pygame.Rect(0, 0, 80, 30), 2))

    def test_a_framed_panel_has_square_corners_and_studs(self):
        surf = pygame.Surface((300, 200))
        surf.fill(gfx.SHADOW)
        gfx.round_rect(surf, pygame.Rect(0, 0, 300, 200), gfx.PANEL, 14, 2, gfx.PANEL_EDGE)
        self.assertNotEqual(tuple(surf.get_at((1, 1)))[:3], gfx.SHADOW)          # the corner is filled (square), not cut round
        self.assertEqual(tuple(surf.get_at((3, 4)))[:3], gfx.GOLD_DARK)          # a corner stud
        pill = pygame.Surface((80, 30))
        pill.fill(gfx.SHADOW)
        gfx.round_rect(pill, pygame.Rect(0, 0, 80, 30), gfx.PANEL, 15, 1, gfx.PANEL_EDGE)
        self.assertEqual(tuple(pill.get_at((0, 0)))[:3], gfx.SHADOW)            # still round

    def test_the_old_look_comes_back_with_the_switch_off(self):
        with mock.patch.object(gfx, "FRAME_STYLE", False):
            self.assertFalse(gfx.framed(pygame.Rect(0, 0, 300, 200), 12))
            surf = pygame.Surface((300, 200))
            surf.fill(gfx.SHADOW)
            gfx.round_rect(surf, pygame.Rect(0, 0, 300, 200), gfx.PANEL, 14, 2, gfx.PANEL_EDGE)
            self.assertEqual(tuple(surf.get_at((1, 1)))[:3], gfx.SHADOW)


class BootTests(TempDecks):
    def boot_gui(self, size=(1360, 840), scale=1.0, splash=True):
        self.launcher = FakeLauncher()
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=size, launcher=self.launcher,
                            deck_dirs=self.dirs)
        gui.text_scale = scale
        gui.open_boot(splash)
        frame(gui, 2)
        return gui

    def item(self, gui, name):
        return next(r for r, n in gui.boot.buttons if n == name).center

    def test_the_splash_moves_on_by_itself_or_with_a_click(self):
        gui = self.boot_gui()
        self.assertEqual(gui.boot.stage, "splash")
        gui.boot.tick(gui.boot.t0 + fboot.SPLASH_SECONDS + 0.01)
        self.assertEqual(gui.boot.stage, "title")
        gui = self.boot_gui()
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(10, 10), button=1))
        self.assertEqual(gui.boot.stage, "title")

    def test_the_title_is_the_picture_alone_until_a_click_or_a_key(self):
        for how in ("click", "key"):
            with self.subTest(how=how):
                gui = self.boot_gui()
                gui.boot.to_title(gui.boot.t0)
                frame(gui, 2)
                self.assertEqual(gui.boot.stage, "title")
                self.assertEqual(gui.boot.buttons, [])           # no menu yet
                if how == "click":
                    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(10, 10), button=1))
                else:
                    key(gui, pygame.K_SPACE)
                self.assertEqual(gui.boot.stage, "menu")
                self.assertIsNone(gui.menu)                      # the key that left the title did not also press Play
                frame(gui, 2)
                self.assertEqual(len(gui.boot.buttons), 5)

    def test_the_title_keeps_the_lettering_and_the_menu_dims_the_picture(self):
        fboot._PICS.clear()
        for size in ((1360, 840), (1920, 1080), (900, 600)):
            with self.subTest(size=size):
                pic = fboot.picture("title", size)
                dim = fboot.dimmed("title", size)
                self.assertEqual(pic.get_size(), size)
                lettering = pygame.Rect(0, int(size[1] * 0.78), size[0], int(size[1] * 0.12))      # the red MANTICORE band
                reds = sum(1 for x in range(0, size[0], 7) for y in range(lettering.top, lettering.bottom, 5)
                           if pic.get_at((x, y))[0] > 120 > pic.get_at((x, y))[1])
                self.assertGreater(reds, 40, "the red lettering is cropped out of the title")
                mean = lambda s: sum(sum(s.get_at((x, y))[:3]) for x in range(0, size[0], 40) for y in range(0, size[1], 40))
                self.assertLess(mean(dim), mean(pic) * 0.6)

    def test_the_title_prompt_fits_and_is_still_with_animations_off(self):
        for size, scale in (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = self.boot_gui(size, scale)
                gui.animations = False
                gui.boot.to_title(gui.boot.t0)
                frame(gui, 2)
                self.assertEqual(gui.boot.stage, "title")
                self.assertTrue(gui.boot.moving(time.monotonic()))

    def test_the_menu_has_the_five_items(self):
        gui = self.boot_gui(splash=False)
        self.assertEqual([n for _r, n in gui.boot.buttons], ["play", "continue", "settings", "credits", "quit"])

    def test_play_opens_the_deck_screen_and_esc_comes_back(self):
        gui = self.boot_gui(splash=False)
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=self.item(gui, "play"), button=1))
        frame(gui)
        self.assertIsNone(gui.boot)
        self.assertIsNotNone(gui.menu)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.menu)
        self.assertEqual(gui.boot.stage, "menu")             # the menu, not the picture again

    def test_continue_is_grey_without_a_saved_game(self):
        gui = self.boot_gui(splash=False)
        self.assertFalse(gui.can_continue())
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=self.item(gui, "continue"), button=1))
        self.assertIsNotNone(gui.boot)                       # nothing happened

    def test_continue_resumes_when_there_is_a_game(self):
        gui = self.boot_gui(splash=False)
        with mock.patch.object(gui, "can_continue", return_value=True), mock.patch.object(gui, "resume_last_game") as resume:
            frame(gui)
            gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=self.item(gui, "continue"), button=1))
        resume.assert_called_once_with()
        self.assertIsNone(gui.boot)

    def test_settings_credits_and_quit(self):
        gui = self.boot_gui(splash=False)
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=self.item(gui, "settings"), button=1))
        self.assertIsNotNone(gui.overlay)
        gui.overlay = None
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=self.item(gui, "credits"), button=1))
        self.assertIsInstance(gui.modal, licenses_view.LicensesDialog)
        gui.modal = None
        frame(gui)
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=self.item(gui, "quit"), button=1))
        self.assertFalse(gui.running)

    def test_the_keyboard_walks_the_menu_and_skips_grey_items(self):
        gui = self.boot_gui(splash=False)
        self.assertEqual(gui.boot.focus, 0)
        key(gui, pygame.K_DOWN)
        self.assertEqual(gui.boot.focus, 2)                  # Continue is grey: straight to Settings
        key(gui, pygame.K_UP)
        self.assertEqual(gui.boot.focus, 0)
        key(gui, pygame.K_RETURN)
        self.assertIsNotNone(gui.menu)

    def test_it_draws_at_every_size_and_without_its_pictures(self):
        for size, scale in (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = self.boot_gui(size, scale, splash=False)
                screen = pygame.Rect((0, 0), size)
                for r, _n in gui.boot.buttons:
                    self.assertTrue(screen.contains(r), r)
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(fboot.paths, "assets_dir", return_value=empty):
            fboot._PICS.clear()
            gui = self.boot_gui()
            frame(gui)
            gui.boot.to_title()
            frame(gui)
        fboot._PICS.clear()

    def test_the_window_icon_and_main_start_here(self):
        self.assertIsNotNone(fboot.window_icon())
        self.assertIn("table.open_boot()", inspect.getsource(ft.main))

    def test_tests_and_the_command_line_never_see_it(self):
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=(1360, 840))
        self.assertIsNone(gui.boot)
        self.assertFalse(gui.title_ok)


if __name__ == "__main__":
    unittest.main()
