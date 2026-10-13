# SPDX-License-Identifier: GPL-3.0-or-later
"""Round LAST1 (needs pygame): the table takes saves at safe moments, offers the last good state after 180 s of an opponent's stall
(never on my own turn), and puts a saved game back in a new game."""
import copy
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_dialogs as dlg
import forge_table as ft
from tests.forge_fake import load_state
from tests.test_forge_table import make_gui


def table(my_turn=False, asking=False, can_save=True):
    st = copy.deepcopy(load_state("main1_start"))
    me = st["me"]
    other = next(p["id"] for p in st["players"] if p["id"] != me)
    st["activePlayer"] = me if my_turn else other
    if asking:
        st["asking"] = True
        st["prompt"] = {"message": "Priority: Your turn", "ok": {"enabled": True}, "cancel": {"enabled": False}}
    else:
        st["asking"] = False
        st["prompt"] = {"message": "Waiting for AI 1.", "ok": {"enabled": False}, "cancel": {"enabled": False}}
    gui = make_gui(st)
    # make_gui drew 3 frames, and drawing calls think_text() with the real time.monotonic(): that started the stall clock at the
    # PC's uptime (on Karl's PC about 48,000 s), so the fake times below came out negative. These tests keep their own clock.
    gui._think_sig = gui._think_since = None
    gui.session.last_state_ok = can_save
    gui.launcher, gui.current_decks = object(), ("a", ["b"])
    return gui, st


class LastGoodTableTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = mock.patch.object(fc, "DATA_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)

    def answer(self, gui, ok=True, turn=4, now=None):
        """The bridge's answer to the newest savestate; then one tick, which is when the table takes the answer in."""
        cmd = gui.session.commands("savestate")[-1]
        if ok:
            with open(cmd["path"], "w") as f:
                f.write("turn=%d\n" % turn)
        gui.session.handle({"t": "state_saved", "n": cmd["n"], "ok": ok, "turn": turn})
        gui.last_good_tick(gui.last_good.in_flight[3] + 0.5 if now is None else now)

    def test_a_save_is_asked_for_when_forge_asks_me_and_not_more_than_every_20_seconds(self):
        gui, st = table(asking=True)
        gui.last_good_tick(100.0)
        self.assertEqual(len(gui.session.commands("savestate")), 1)
        self.answer(gui)
        gui.last_good_tick(110.0)
        self.assertEqual(len(gui.session.commands("savestate")), 1)         # nothing changed, and too soon
        st["players"][0]["life"] = (st["players"][0].get("life") or 40) - 3
        gui.last_good_tick(115.0)
        self.assertEqual(len(gui.session.commands("savestate")), 1)         # changed, but 15 s since the last try
        gui.last_good_tick(125.0)
        self.assertEqual(len(gui.session.commands("savestate")), 2)

    def test_no_save_while_an_ai_is_thinking(self):
        gui, st = table(asking=False)
        gui.last_good_tick(100.0)
        self.assertEqual(gui.session.commands("savestate"), [])

    def test_no_save_with_a_bridge_that_cannot(self):
        gui, st = table(asking=True, can_save=False)
        gui.last_good_tick(100.0)
        self.assertEqual(gui.session.commands("savestate"), [])

    def test_the_offer_comes_after_180_s_on_an_opponents_turn_with_the_restore_option(self):
        gui, st = table(asking=True)
        gui.last_good_tick(10.0)
        self.answer(gui, turn=4)
        st["asking"] = False
        st["prompt"] = {"message": "Waiting for AI 1.", "ok": {"enabled": False}, "cancel": {"enabled": False}}
        gui.watchdog_tick(100.0)
        gui.watchdog_tick(279.0)
        self.assertIsNone(gui.modal)
        gui.watchdog_tick(281.0)
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        labels = [o[0] for o in gui.modal.options]
        self.assertEqual(labels, ["Restore the last good state (turn 4)", "End this game"])
        self.assertEqual(gui.modal.cancel, "Keep waiting")

    def test_keep_waiting_asks_again_180_s_later(self):
        gui, st = table(asking=False)
        gui.watchdog_tick(100.0)
        gui.watchdog_tick(281.0)
        self.assertIsNotNone(gui.modal)
        gui.modal = None                                                      # "Keep waiting"
        gui.watchdog_tick(300.0)
        self.assertIsNone(gui.modal)
        gui.watchdog_tick(459.0)                                              # 359 s into the stall: not yet
        self.assertIsNone(gui.modal)
        gui.watchdog_tick(461.0)                                              # 361 s: the second offer
        self.assertIsNotNone(gui.modal)

    def test_without_a_save_the_offer_only_ends_the_game(self):
        gui, st = table(asking=False)
        gui.watchdog_tick(100.0)
        gui.watchdog_tick(281.0)
        self.assertEqual([o[0] for o in gui.modal.options], ["End this game"])

    def test_never_on_my_own_turn(self):
        gui, st = table(my_turn=True, asking=False)
        gui.watchdog_tick(100.0)
        gui.watchdog_tick(1000.0)
        self.assertIsNone(gui.modal)

    def test_restore_starts_a_new_game_then_loads_at_the_first_priority_stop(self):
        gui, st = table(asking=True)
        gui.last_good_tick(10.0)
        self.answer(gui, turn=4)
        with mock.patch.object(gui, "restart_game") as restart:
            gui.restore_last_good()
        restart.assert_called_once()
        self.assertEqual(gui.pending_restore["stage"], "wait")
        st["asking"] = False                                                  # mulligans still going: nothing is sent
        gui.restore_tick(20.0)
        self.assertEqual(gui.session.commands("loadstate"), [])
        st["asking"] = True                                                   # a mulligan question: asked, but not a priority stop
        st["prompt"] = {"message": "Keep this hand or mulligan?", "ok": {"enabled": True}, "cancel": {"enabled": True}}
        gui.restore_tick(25.0)
        self.assertEqual(gui.session.commands("loadstate"), [])
        st["prompt"] = {"message": "Priority: Your turn", "ok": {"enabled": True}, "cancel": {"enabled": False}}
        gui.restore_tick(30.0)
        self.assertEqual(len(gui.session.commands("loadstate")), 1)
        self.assertIsInstance(gui.modal, dlg.WorkingDialog)
        gui.restore_tick(31.0)
        self.assertEqual(len(gui.session.commands("loadstate")), 1)           # only once
        gui.session.handle({"t": "setup_done"})
        st["turn"] = 4                                                        # round LAST1c: done AND the table shows the saved turn
        gui.restore_tick(35.0)
        self.assertIsNone(gui.pending_restore)
        self.assertTrue(gui.modal.done)

    def test_a_failed_load_ends_the_wait_and_says_so(self):
        gui, st = table(asking=True)
        gui.pending_restore = {"path": "x", "turn": 4, "stage": "apply", "t0": 0.0, "t1": 10.0, "expect": 1}
        gui.modal = dlg.WorkingDialog("t", "t")
        gui.session.handle({"t": "state_load_failed", "reason": "boom"})
        with mock.patch.object(gui, "say") as say:
            gui.restore_tick(12.0)
        self.assertIsNone(gui.pending_restore)
        self.assertIn("boom", say.call_args[0][0])

    def test_a_restore_that_never_answers_gives_up_after_60_s(self):
        gui, st = table(asking=True)
        gui.pending_restore = {"path": "x", "turn": 4, "stage": "apply", "t0": 0.0, "t1": 10.0, "expect": 1}
        gui.restore_tick(71.0)
        self.assertIsNone(gui.pending_restore)


if __name__ == "__main__":
    unittest.main()
