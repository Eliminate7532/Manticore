# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1566939794272161812 (Karl, 2 Nov 2026: "The settings menu should include the ability to quit then choose quit to desktop or
main menu"; 3 Nov: "Ask each time" and "One Quit... button"). The cog's Concede... became Quit...: the main menu or the desktop,
and with a game going on, "keep for later" or "concede" - plus "Keep playing". What "keep for later" means per kind of game is in
ForgeTable.ask_quit and docs/bugs/bug-1566939794272161812.md."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_dialogs as dlg
import forge_table as ft
import journal
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import click, frame, key, point_for


def labels(gui):
    return [l for l, _r, _s in gui.modal.options]


def press(gui, label):
    i = labels(gui).index(label)
    frame(gui, 1)                                                     # the window's buttons are placed when it is drawn
    click(gui, next(r.center for r, n in gui.modal.buttons if n == f"opt{i}"))


class QuitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, journal_on=False):
        gui = ft.ForgeTable(FakeSession(load_state("main1_start"), load_log()), StubStore(), window_size=(1360, 840),
                            saves_dir=os.path.join(self.tmp, "saves") if journal_on else None)
        frame(gui, 2)
        return gui

    def saves(self):
        return os.path.join(self.tmp, "saves")

    def start_journal(self, gui):
        a, b = os.path.join(self.tmp, "p.dck"), os.path.join(self.tmp, "o.dck")
        for path, text in ((a, "[metadata]\nName=Me\n"), (b, "[metadata]\nName=AI\n")):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        gui.session.deck_path, gui.session.opponent_paths, gui.session.seed = a, [b], 5
        gui.start_journal()
        gui.session.on_send({"c": "ok"})

    # --- the cog -----------------------------------------------------------------------------------------------------------------
    def test_the_cog_has_quit_and_no_concede_button(self):
        gui = self.make()
        click(gui, point_for(gui, "button", name="settings"))
        names = [n for _r, n in gui.overlay.buttons]
        self.assertIn("quit", names)
        self.assertNotIn("concede", names)
        click(gui, next(r.center for r, n in gui.overlay.buttons if n == "quit"))
        self.assertIsNone(gui.overlay)
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertEqual(gui.modal.title, "Quit")
        self.assertEqual(labels(gui), ["Main menu  -  keep for later", "Main menu  -  concede",
                                       "Desktop  -  keep for later", "Desktop  -  concede"])
        self.assertEqual(gui.modal.cancel, "Keep playing")

    def test_keep_playing_changes_nothing(self):
        gui = self.make(journal_on=True)
        self.start_journal(gui)
        gui.ask_quit()
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.modal)
        self.assertTrue(gui.running)
        self.assertTrue(gui.game_in_progress())
        self.assertEqual(gui.session.commands("concede"), [])

    # --- a game against the AI ---------------------------------------------------------------------------------------------------
    def test_main_menu_keep_for_later_leaves_the_game_resumable(self):
        gui = self.make(journal_on=True)
        self.start_journal(gui)
        session = gui.session
        closed = []
        session.close = lambda: closed.append(True)
        gui.ask_quit()
        press(gui, "Main menu  -  keep for later")
        self.assertEqual(session.commands("concede"), [])            # nothing given up
        self.assertEqual(closed, [True])                              # the engine is ended
        self.assertTrue(gui.running)
        self.assertEqual(gui.boot.stage, "menu")                      # the title's main menu
        self.assertFalse(gui.game_showing())
        info = journal.unfinished(self.saves())                       # still unfinished: "Resume last game" offers it
        self.assertIsNotNone(info)
        self.assertEqual(info[1], [{"c": "ok"}])
        self.assertFalse(os.path.isdir(os.path.join(self.saves(), "finished")))

    def test_main_menu_concede_finishes_the_journal(self):
        gui = self.make(journal_on=True)
        self.start_journal(gui)
        session = gui.session
        gui.ask_quit()
        press(gui, "Main menu  -  concede")
        self.assertEqual(session.commands("concede"), [{"c": "concede"}])
        self.assertEqual(gui.boot.stage, "menu")
        self.assertIsNone(journal.unfinished(self.saves()))
        self.assertTrue(any(n.endswith("_conceded.jsonl") for n in os.listdir(os.path.join(self.saves(), "finished"))))

    def test_desktop_keep_for_later_closes_without_conceding_and_shutdown_keeps_it(self):
        gui = self.make(journal_on=True)
        self.start_journal(gui)
        session = gui.session
        gui.ask_quit()
        press(gui, "Desktop  -  keep for later")
        self.assertFalse(gui.running)
        self.assertEqual(session.commands("concede"), [])
        with mock.patch.object(ft.pygame, "quit"):                   # the other tests still need the display
            gui.shutdown()
        self.assertIsNotNone(journal.unfinished(self.saves()))

    def test_desktop_concede(self):
        gui = self.make()
        gui.ask_quit()
        press(gui, "Desktop  -  concede")
        self.assertFalse(gui.running)
        self.assertEqual(gui.session.commands("concede"), [{"c": "concede"}])

    # --- no game ------------------------------------------------------------------------------------------------------------------
    def test_no_game_running_offers_the_main_menu_and_the_desktop(self):
        gui = self.make()
        session = gui.session
        session.game_over = True
        gui.ask_quit()
        self.assertEqual(labels(gui), ["Main menu", "Desktop  (close the program)"])
        self.assertEqual(gui.modal.cancel, "Cancel")
        press(gui, "Main menu")
        self.assertEqual(gui.boot.stage, "menu")
        self.assertEqual(session.commands("concede"), [])

    def test_on_the_title_only_the_desktop_is_offered(self):
        gui = self.make()
        gui.leave_game()                                              # to the title's main menu
        frame(gui, 2)
        gui.ask_quit()
        self.assertEqual(labels(gui), ["Desktop  (close the program)"])
        press(gui, "Desktop  (close the program)")
        self.assertFalse(gui.running)

    # --- online -------------------------------------------------------------------------------------------------------------------
    def test_a_guest_can_only_leave_by_conceding(self):
        gui = self.make()
        gui.session.online = "guest"
        gui.ask_quit()
        self.assertEqual(labels(gui), ["Main menu  -  leave and concede", "Desktop  -  leave and concede"])
        self.assertEqual([s for _l, _r, s in gui.modal.options], ["danger", "danger"])
        self.assertIn("Only the host", gui.modal.text)

    def test_a_spectator_just_stops_watching(self):
        gui = self.make()
        session = gui.session
        session.online = "guest"
        session.spectator = True
        gui.ask_quit()
        self.assertEqual(labels(gui), ["Main menu  -  stop watching", "Desktop  -  stop watching"])
        press(gui, "Main menu  -  stop watching")
        self.assertEqual(session.commands("concede"), [])
        self.assertEqual(gui.boot.stage, "menu")

    def test_a_host_keeping_for_later_leaves_the_online_save_unfinished(self):
        gui = self.make()
        session = gui.session
        session.online = "host"
        gui.online_save_folder = os.path.join(self.tmp, "online", "x")
        gui.ask_quit()
        self.assertIn("Host online", gui.modal.text)
        with mock.patch.object(ft.online_save, "finish") as finish:
            press(gui, "Main menu  -  keep for later")
        finish.assert_not_called()                                    # Host online > "Continue it" still finds it
        self.assertEqual(session.commands("concede"), [])
        self.assertEqual(gui.boot.stage, "menu")

    # --- an Adventure duel --------------------------------------------------------------------------------------------------------
    def test_an_adventure_duel_left_unfinished_is_not_settled(self):
        """The Adventure's own rule (rpg/duel.py settle_pending): a duel that didn't finish doesn't count - nothing is won or lost.
        Leaving it must not settle it as a loss (only "concede" does)."""
        gui = self.make()
        gui.adventure_duel = {"slot": 1, "folder": self.tmp, "kind": "rat", "enemy": "Rat", "stats_id": "x", "outcome": None,
                              "error": None, "settled": False, "result": None, "tried": 0.0, "boss": False}
        session = gui.session
        gui.ask_quit()
        self.assertEqual(labels(gui)[0], "Main menu  -  leave the duel")
        self.assertIn("doesn't count", gui.modal.text)
        from rpg import adventure as adv
        with mock.patch.object(adv, "finish_duel") as finish:
            press(gui, "Main menu  -  leave the duel")
        finish.assert_not_called()
        self.assertIsNone(gui.adventure_duel)
        self.assertEqual(session.commands("concede"), [])
        self.assertEqual(gui.boot.stage, "menu")

    def test_the_window_fits_at_small_sizes_and_big_text(self):
        for size, scale in (((900, 600), 1.0), ((900, 600), 2.0), ((1100, 700), 2.0), ((1920, 1080), 1.0)):
            with self.subTest(size=size, scale=scale):
                gui = self.make()
                gui.text_scale = scale
                gui.screen = pygame.display.set_mode(size)
                frame(gui, 2)
                gui.ask_quit()
                frame(gui, 2)
                self.assertTrue(pygame.Rect(0, 0, *size).contains(gui.modal.rect))
                for r, _n in gui.modal.buttons:
                    self.assertTrue(gui.modal.rect.contains(r))


if __name__ == "__main__":
    unittest.main()
