# SPDX-License-Identifier: GPL-3.0-or-later
"""Round NAME1 - bug report from Erik, 2 Nov 2026 ("I was Karl"). Karl's note on it: "The user is not named "Karl" but noticed
that his playername defaulted to "Karl" -- in fact his name is Erik but the point is the game should ask for your player name".

Every copy called its player Karl: forge_table's --name defaulted to "Karl" and the Launcher passed it to Forge. Now there is no
default name: the first time the main menu shows, the game asks; the answer is kept in settings.json (player_name) and used at
the table, as the online name and on bug reports; "Playing as ... (change)" on the main menu asks again.
"""
import json
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame

import boot_screens as fboot
import forge_client as fc
import forge_table as ft
from tests.forge_fake import StubStore
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import click, frame, key
from tests.test_mp1 import press_button

SIZES = (((900, 600), 1.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.5), ((4096, 1949), 1.75))


class NameTests(TempDecks):
    def table(self, settings=None, name=None, size=(1360, 840), scale=None):
        settings = settings or os.path.join(self.tmp, "settings.json")
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=settings, window_size=size,
                            launcher=ft.Launcher(None, name), deck_dirs=self.dirs)
        if scale is not None:
            gui.text_scale = scale
        gui.open_title()
        frame(gui, 2)
        return gui

    def saved(self):
        with open(os.path.join(self.tmp, "settings.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_there_is_no_default_name_any_more(self):
        self.assertIsNone(ft.Launcher().name)
        self.assertIsNone(ft.parse_args([]).name)
        with mock.patch.object(fc, "ForgeSession") as fs:
            fs.return_value.start.return_value = "started"
            ft.Launcher().start(([], []), [([], [])])
        self.assertEqual(fs.call_args.kwargs["name"], "Player")          # never "Karl"

    def test_the_first_main_menu_asks_and_the_answer_is_used_everywhere(self):
        gui = self.table()
        d = gui.modal
        self.assertIsInstance(d, fboot.NameDialog)
        self.assertEqual(d.field.text, "")
        frame(gui, 1)
        self.assertNotIn("ok", [n for _r, n in d.buttons])                  # nothing typed: OK is greyed
        for ch in "Erik":
            key(gui, 0, unicode=ch)
        key(gui, pygame.K_RETURN)
        self.assertIsNone(gui.modal)
        self.assertEqual((gui.player_name, gui.launcher.name, gui.reporter_name, gui.online_prefs.get("name")),
                         ("Erik", "Erik", "Erik", "Erik"))
        s = self.saved()
        self.assertEqual(s["player_name"], "Erik")
        again = self.table()                                                 # next start: not asked again
        self.assertIsNone(again.modal)
        self.assertEqual(again.launcher.name, "Erik")
        self.assertEqual(again.online_defaults()["name"], "Erik")

    def test_not_now_plays_as_player_and_asks_again_next_time(self):
        gui = self.table()
        press_button(gui, gui.modal, "cancel")
        self.assertIsNone(gui.modal)
        self.assertIsNone(gui.launcher.name)
        self.assertEqual(gui.player_name, "")
        self.assertIsInstance(self.table().modal, fboot.NameDialog)

    def test_a_name_on_the_command_line_is_never_asked_about(self):
        gui = self.table(name="Sam")
        self.assertIsNone(gui.modal)
        self.assertEqual(gui.launcher.name, "Sam")

    def test_the_main_menu_says_who_you_are_and_changes_it(self):
        gui = self.table()
        gui.modal.field.set("Erik")
        gui.modal.ok(gui)
        gui.modal = None
        frame(gui, 1)
        self.assertNotIn("name", [n for _r, n in gui.boot.buttons])        # not one of the menu's six items
        self.assertIsNotNone(gui.boot.name_rect)
        click(gui, gui.boot.name_rect.center)
        d = gui.modal
        self.assertIsInstance(d, fboot.NameDialog)
        self.assertEqual(d.field.text, "Erik")
        d.field.set("Erik B")
        press_button(gui, d, "ok")
        self.assertEqual((gui.player_name, gui.launcher.name, gui.reporter_name), ("Erik B", "Erik B", "Erik B"))

    def test_a_report_or_online_name_given_before_is_offered(self):
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"reporter_name": "Erik"}, f)
        gui = self.table()
        self.assertEqual(gui.modal.field.text, "Erik")

    def test_a_name_typed_to_play_online_becomes_my_name(self):
        gui = self.table()
        gui.modal = None
        gui._remember_online(name="Ann", port=36800)
        self.assertEqual((gui.player_name, gui.launcher.name), ("Ann", "Ann"))
        gui._remember_online(name="Somebody else")                          # a name already given stays
        self.assertEqual(gui.player_name, "Ann")

    def test_a_resumed_game_without_a_name_is_not_karl(self):
        import inspect
        src = inspect.getsource(ft)
        self.assertNotIn('or "Karl"', src)
        self.assertNotIn('default="Karl"', src)

    def test_the_question_fits_at_every_size(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.table(settings=os.path.join(self.tmp, "s_%d_%s.json" % (size[0], scale)), size=size, scale=scale)
                gui.modal.field.set("x" * 24)
                frame(gui, 1)
                r = gui.modal.rect
                self.assertTrue(gui.screen.get_rect().contains(r))
                for b, _n in gui.modal.buttons:
                    self.assertTrue(r.contains(b), (b, r))
                gui.modal.ok(gui)
                gui.modal = None
                frame(gui, 1)
                nr = gui.boot.name_rect
                self.assertTrue(gui.screen.get_rect().contains(nr))
                for r_, n in gui.boot.buttons:
                    self.assertFalse(nr.colliderect(r_), n)


if __name__ == "__main__":
    unittest.main()
