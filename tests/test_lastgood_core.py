# SPDX-License-Identifier: GPL-3.0-or-later
"""Round LAST1 (no pygame): the saves manager (last_good.py) and the client's new messages and commands."""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import last_good
from last_good import LastGood
from tests.forge_fake import FakeSession


def good_save(lg, now, sig, turn=5):
    """Take a save the way the table does, and answer it the way the bridge does (the file exists)."""
    n, path = lg.next_path()
    lg.sent(now, n, path, sig)
    with open(path, "w") as f:
        f.write("turn=%d\n" % turn)
    return lg.reply({"t": "state_saved", "n": n, "ok": True, "turn": turn}, now + 0.5), path


class LastGoodTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lg = LastGood(os.path.join(self.dir, "g"))

    def test_first_save_is_due_then_waits_for_the_gap_and_for_a_change(self):
        lg = self.lg
        self.assertTrue(lg.due(100.0, "A"))
        ok, _ = good_save(lg, 100.0, "A")
        self.assertTrue(ok)
        self.assertFalse(lg.due(130.0, "A"))                 # nothing changed
        self.assertFalse(lg.due(110.0, "B"))                 # changed, but only 10 s since the last try
        self.assertTrue(lg.due(121.0, "B"))

    def test_nothing_goes_out_while_one_is_in_flight_and_a_lost_one_is_forgotten(self):
        lg = self.lg
        n, path = lg.next_path()
        lg.sent(100.0, n, path, "A")
        self.assertFalse(lg.due(125.0, "B"))
        self.assertTrue(lg.due(131.0, "B"))                  # no answer in 30 s: given up on
        self.assertFalse(lg.reply({"n": n, "ok": True, "turn": 1}, 132.0))      # its late answer is ignored

    def test_busy_answer_is_not_a_save_and_retries_after_the_gap(self):
        lg = self.lg
        n, path = lg.next_path()
        lg.sent(100.0, n, path, "A")
        self.assertFalse(lg.reply({"n": n, "ok": False, "reason": "busy"}, 101.0))
        self.assertIsNone(lg.latest())
        self.assertFalse(lg.due(110.0, "A"))
        self.assertTrue(lg.due(121.0, "A"))

    def test_an_ok_answer_without_the_file_is_not_trusted(self):
        lg = self.lg
        n, path = lg.next_path()
        lg.sent(100.0, n, path, "A")
        self.assertFalse(lg.reply({"n": n, "ok": True, "turn": 3}, 101.0))
        self.assertIsNone(lg.latest())

    def test_only_the_newest_three_are_kept_on_disk(self):
        lg = self.lg
        paths = []
        for i in range(5):
            ok, path = good_save(lg, 100.0 + 100 * i, "S%d" % i, turn=i + 1)
            paths.append(path)
        self.assertEqual([os.path.isfile(p) for p in paths], [False, False, True, True, True])
        self.assertEqual(lg.latest()["turn"], 5)

    def test_latest_skips_a_file_that_has_gone(self):
        lg = self.lg
        good_save(lg, 100.0, "A", turn=2)
        ok, newest = good_save(lg, 200.0, "B", turn=3)
        os.remove(newest)
        self.assertEqual(lg.latest()["turn"], 2)

    def test_clean_old_removes_only_old_folders(self):
        root = os.path.join(self.dir, "root")
        old, new = os.path.join(root, "old"), os.path.join(root, "new")
        os.makedirs(old)
        os.makedirs(new)
        past = time.time() - 4 * 86400
        os.utime(old, (past, past))
        last_good.clean_old(root, days=3)
        self.assertFalse(os.path.isdir(old))
        self.assertTrue(os.path.isdir(new))
        last_good.clean_old(os.path.join(self.dir, "missing"))      # no folder: no error


class ClientTests(unittest.TestCase):
    def test_ready_line_tells_whether_the_bridge_can_save(self):
        s = FakeSession()
        self.assertFalse(s.last_state_ok)
        s.handle({"t": "ready", "loopCap": 10, "lastState": True})
        self.assertTrue(s.last_state_ok)

    def test_an_old_bridge_without_the_flag_stays_false(self):
        s = FakeSession()
        s.handle({"t": "ready", "loopCap": 10})
        self.assertFalse(s.last_state_ok)

    def test_save_and_load_send_the_commands_and_answers_are_kept(self):
        s = FakeSession()
        n = s.save_state("C:/x/state_0001.state")
        self.assertEqual(s.commands("savestate"), [{"c": "savestate", "path": "C:/x/state_0001.state", "n": n}])
        s.handle({"t": "state_saved", "n": n, "ok": True, "turn": 7})
        self.assertEqual(s.state_saves[-1]["turn"], 7)
        s.load_state("C:/x/state_0001.state")
        self.assertEqual(s.commands("loadstate"), [{"c": "loadstate", "path": "C:/x/state_0001.state"}])
        s.handle({"t": "state_load_failed", "reason": "boom"})
        self.assertEqual(s.state_load_failed, "boom")
        s.load_state("C:/x/state_0001.state")
        self.assertIsNone(s.state_load_failed)                # cleared by the next attempt


if __name__ == "__main__":
    unittest.main()
