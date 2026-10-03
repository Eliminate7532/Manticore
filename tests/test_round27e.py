# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 27e: the window fits the screen it opens on, and each screen size keeps its own window and text size. Also: the card a
surveil / scry-1 question is about shows in the focus panel.

Karl, 2026-09-26: playing over Remote Desktop, the table opened "way too big" - settings.json remembered 4096x2019 from his own
monitor, and the table reopened at that size on a much smaller Remote Desktop screen.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_state

MONITOR = (4096, 2160)
RDP = (1920, 1080)


class FitTests(unittest.TestCase):
    def test_a_window_bigger_than_the_screen_is_made_to_fit(self):
        w, h = ft.fit_window((4096, 2019), RDP)
        self.assertLessEqual(w, RDP[0])
        self.assertLessEqual(h, RDP[1] - 60)                    # room for the title bar and the taskbar
        self.assertEqual((w, h), (int(RDP[0] * ft.SCREEN_ROOM[0]), int(RDP[1] * ft.SCREEN_ROOM[1])))

    def test_a_window_that_fits_is_left_alone(self):
        self.assertEqual(ft.fit_window((1360, 840), RDP), (1360, 840))

    def test_unknown_screen_and_minimum_size(self):
        self.assertEqual(ft.fit_window((4096, 2019), None), (4096, 2019))
        self.assertEqual(ft.fit_window((10, 10), RDP), ft.MIN_WINDOW)
        self.assertEqual(ft.fit_window((4096, 2019), (800, 500)), ft.MIN_WINDOW)     # a tiny screen: the smallest usable window

    def test_screen_key(self):
        self.assertEqual(ft.screen_key(RDP), "1920x1080")
        self.assertIsNone(ft.screen_key(None))


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "settings.json")
        self.desktop = MONITOR
        patcher = mock.patch.object(ft, "desktop_size", side_effect=lambda: self.desktop)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def table(self, window_size=None):
        return ft.ForgeTable(FakeSession(load_state("main1_start")), StubStore(), settings_path=self.path, window_size=window_size)

    def test_karls_settings_over_remote_desktop_open_a_window_that_fits(self):
        self.write({"window_size": [4096, 2019], "text_scale": 1.25, "fullscreen": False})
        self.desktop = RDP
        gui = self.table()
        w, h = gui.screen.get_size()
        self.assertLessEqual(w, RDP[0] * ft.SCREEN_ROOM[0])
        self.assertLessEqual(h, RDP[1] * ft.SCREEN_ROOM[1])

    def test_the_same_settings_on_the_big_monitor_still_open_big(self):
        self.write({"window_size": [3900, 1900], "text_scale": 1.25})
        gui = self.table()
        self.assertEqual(gui.screen.get_size(), (3900, 1900))

    def test_each_screen_size_keeps_its_own_window_and_text(self):
        self.write({"window_size": [3900, 1900], "text_scale": 1.25})
        gui = self.table()                                      # the monitor
        gui.save_settings()
        self.desktop = RDP
        gui = self.table()                                      # Remote Desktop: fitted, then made smaller text and a smaller window
        gui.text_scale = 1.0
        gui.resize_window((1500, 900))
        self.assertEqual(self.read()["screens"]["1920x1080"]["window_size"], [1500, 900])
        self.desktop = MONITOR
        gui = self.table()                                      # back at the monitor: its own sizes again
        self.assertEqual(gui.screen.get_size(), (3900, 1900))
        self.assertEqual(gui.text_scale, 1.25)
        self.desktop = RDP
        gui = self.table()
        self.assertEqual(gui.screen.get_size(), (1500, 900))
        self.assertEqual(gui.text_scale, 1.0)

    def test_a_reconnect_with_a_smaller_screen_resizes_the_running_table(self):
        self.write({"window_size": [3900, 1900], "text_scale": 1.25})
        gui = self.table()
        self.assertFalse(gui.check_display(force=True))         # nothing changed
        self.desktop = RDP
        self.assertTrue(gui.check_display(force=True))
        w, h = gui.screen.get_size()
        self.assertLessEqual((w, h), (int(RDP[0] * ft.SCREEN_ROOM[0]), int(RDP[1] * ft.SCREEN_ROOM[1])))
        self.assertEqual(self.read()["screens"]["4096x2160"]["window_size"], [3900, 1900])     # the monitor's size was kept
        self.desktop = MONITOR
        self.assertTrue(gui.check_display(force=True))
        self.assertEqual(gui.screen.get_size(), (3900, 1900))

    def test_the_display_is_only_checked_every_few_seconds(self):
        gui = self.table()
        with mock.patch.object(ft, "desktop_size", return_value=RDP) as ds:
            gui.check_display(now=gui._display_checked + 0.5)
            ds.assert_not_called()
            gui.check_display(now=gui._display_checked + ft.DISPLAY_CHECK_SECONDS + 0.1)
            ds.assert_called_once()

    def test_a_size_given_by_the_caller_is_used_as_given(self):
        self.desktop = RDP
        gui = self.table(window_size=(3000, 2000))              # the layout tests draw big windows on purpose
        self.assertEqual(gui.screen.get_size(), (3000, 2000))

    def test_an_old_settings_file_without_screens_still_loads(self):
        self.write({"window_size": [1200, 800], "text_scale": 1.5})
        gui = self.table()
        self.assertEqual(gui.screen.get_size(), (1200, 800))
        self.assertEqual(gui.text_scale, 1.5)

    def test_a_broken_screens_entry_is_ignored(self):
        self.write({"window_size": [1200, 800], "screens": {"4096x2160": {"window_size": "big"}, "x": 3}})
        gui = self.table()
        self.assertEqual(gui.screen.get_size(), (1200, 800))


def fixture_state(name, predicate):
    """The first state in a recorded bridge game (tests/fixtures/bridge, round 27d) that matches predicate."""
    import gzip
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bridge", name + ".jsonl.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            if m.get("t") == "state" and predicate(m):
                return m
    raise AssertionError("no such state in " + name)


class FocusPanelTests(unittest.TestCase):
    """Karl, 2026-09-26 (screenshot: "Put Decanter of Endless Water on the top of library or graveyard?"): what you surveil
    or scry should show in the focus panel, not only as the action bar's small thumbnail."""

    def gui_for(self, state):
        gui = ft.ForgeTable(FakeSession(state), StubStore(), window_size=(1360, 840))
        gui.sync()
        gui.mouse = (-1, -1)
        return gui

    def test_the_surveil_card_is_the_focus_card(self):
        st = fixture_state("surveil_ok", lambda m: "graveyard?" in ((m.get("prompt") or {}).get("message") or ""))
        gui = self.gui_for(st)
        self.assertEqual(gui.preview_card()["name"], "Forest")
        gui.render()
        self.assertEqual(gui.last_preview["name"], "Forest")

    def test_hovering_a_card_still_wins(self):
        st = fixture_state("surveil_ok", lambda m: "graveyard?" in ((m.get("prompt") or {}).get("message") or ""))
        gui = self.gui_for(st)
        swamp = {"id": 1, "name": "Swamp"}
        with mock.patch.object(gui, "hit_at", return_value=("card", {"card": swamp})):
            self.assertEqual(gui.preview_card()["name"], "Swamp")

    def test_a_prompt_about_a_card_on_the_table_does_not_take_the_panel(self):
        st = fixture_state("spiteful_ok", lambda m: "Pay Mana Cost" in ((m.get("prompt") or {}).get("message") or ""))
        self.assertNotEqual((st["prompt"].get("source") or {}).get("zone"), "Library")
        self.assertIsNone(self.gui_for(st).prompt_library_card())


class CommanderQuestionTests(unittest.TestCase):
    """Karl, 2026-09-26 (screenshot: Valgavoth's question ran off the bar): "Should be short. 'Return your commander to the
    command zone?'" """
    FORGE = ("Valgavoth, Harrower of Souls: If a commander is in a graveyard or in exile and that card was put into that zone "
             "since the last time state-based actions were checked, its owner may put it into the command zone.")

    def test_the_headline_is_short(self):
        headline, hint, _ok = ft.prompt_view(self.FORGE, "Karl", kind="confirm")
        self.assertEqual(headline, "Return your commander to the command zone?")
        self.assertIn("Valgavoth, Harrower of Souls", hint)
        self.assertLess(len(hint), 70)
        self.assertEqual(ft.prompt_view(self.FORGE, "Karl")[0], headline)             # with or without the input kind

    def test_the_recorded_question_and_the_commander_in_the_focus_panel(self):
        st = fixture_state("cmdzone_yes", lambda m: "command zone" in ((m.get("prompt") or {}).get("message") or "").lower())
        self.assertEqual(ft.commander_zone_name(st["prompt"]["message"]), "Kinnan, Bonder Prodigy")
        gui = ft.ForgeTable(FakeSession(st), StubStore(), window_size=(1360, 840))
        gui.sync()
        gui.mouse = (-1, -1)
        self.assertEqual(gui.preview_card()["name"], "Kinnan, Bonder Prodigy")

    def test_other_prompts_are_unchanged(self):
        self.assertIsNone(ft.commander_zone_name("Forest (205)\n\nPut Forest on the top of library or graveyard?"))
        self.assertIsNone(ft.commander_zone_name(""))


class SourceTests(unittest.TestCase):
    def test_the_main_loop_checks_the_display(self):
        import inspect
        self.assertIn("self.check_display(now)", inspect.getsource(ft.ForgeTable.tick))
