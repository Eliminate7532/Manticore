# SPDX-License-Identifier: GPL-3.0-or-later
"""Round THINK1: "<seat> is still thinking  -  N s" shows after 10 quiet seconds, restarts when the table changes, and never acts."""
import copy
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_table as ft
from tests.forge_fake import load_state
from tests.test_forge_table import frame, make_gui

WAITING = {"message": "Waiting for AI 1.", "ok": {"enabled": False}, "cancel": {"enabled": False}}


def waiting_gui(my_turn=False):
    st = copy.deepcopy(load_state("main1_start"))
    me = st["me"]
    other = next(p["id"] for p in st["players"] if p["id"] != me)
    st["activePlayer"] = me if my_turn else other
    st["prompt"] = copy.deepcopy(WAITING)
    gui = make_gui(st)
    # make_gui drew 3 frames, and drawing calls think_text() with the real time.monotonic(): that started the stall clock at the
    # PC's uptime (on Karl's PC about 48,000 s), so the fake times below came out negative. These tests keep their own clock.
    gui._think_sig = gui._think_since = None
    return gui, st, other


class ThinkMessageTests(unittest.TestCase):
    def test_nothing_before_ten_seconds_then_the_seat_and_the_seconds(self):
        gui, st, other = waiting_gui()
        self.assertEqual(gui.think_elapsed(100.0), 0.0)
        self.assertIsNone(gui.think_text(109.0))
        text = gui.think_text(112.0)
        name = next(p["name"] for p in st["players"] if p["id"] == other)
        self.assertEqual(text, "%s is still thinking  -  12 s" % name)

    def test_the_clock_restarts_when_the_table_changes(self):
        gui, st, other = waiting_gui()
        gui.think_text(100.0)
        self.assertIsNotNone(gui.think_text(115.0))
        st["players"][0]["life"] = (st["players"][0].get("life") or 40) - 1       # something happened
        self.assertIsNone(gui.think_text(116.0))
        self.assertIsNotNone(gui.think_text(127.0))

    def test_a_new_snapshot_with_the_same_table_does_not_restart_it(self):
        gui, st, other = waiting_gui()
        gui.think_text(100.0)
        gui.session.state_version += 1                                             # a flush ping's snapshot, nothing changed
        self.assertIsNotNone(gui.think_text(111.0))

    def test_not_while_forge_is_asking_me(self):
        gui, st, other = waiting_gui()
        st["prompt"] = {"message": "Your priority.", "ok": {"enabled": True}, "cancel": {"enabled": False}}
        gui.think_text(100.0)
        self.assertIsNone(gui.think_text(200.0))

    def test_my_own_turn_says_resolving_not_thinking(self):
        gui, st, other = waiting_gui(my_turn=True)
        gui.think_text(100.0)
        self.assertIn("Forge is still resolving your turn  -  15 s", gui.think_text(115.0))

    def test_a_big_board_is_named(self):
        gui, st, other = waiting_gui()
        card = {"id": 900000, "name": "Citizen", "isLand": False}
        zones = st["players"][0].setdefault("zones", {})
        zones["battlefield"] = [dict(card, id=900000 + i) for i in range(250)]
        gui.think_text(100.0)
        self.assertIn("permanents on the table", gui.think_text(130.0))

    def test_the_not_answering_banner_takes_over(self):
        gui, st, other = waiting_gui()
        gui.think_text(100.0)
        with mock.patch.object(gui, "not_responding", return_value=True):
            self.assertIsNone(gui.think_text(130.0))

    def test_drawing_it_does_not_raise(self):
        gui, st, other = waiting_gui()
        with mock.patch.object(gui, "think_text", return_value="X is still thinking  -  12 s"):
            frame(gui, 2)


if __name__ == "__main__":
    unittest.main()
