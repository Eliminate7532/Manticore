# SPDX-License-Identifier: GPL-3.0-or-later
"""Round QT1 (13 Oct 2026): tools/quick_tests.py doesn't blame a test for what the scheduled backup wrote.

Karl's quick_tests run of 13 Oct evening took 1010 s and failed its hygiene check on backup.log and backup_status.json: the
18:36 backup (backup.py --auto, which writes both) had run during it. backup_status.json's last_ok said 18:37:07.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
import quick_tests  # noqa: E402


def stamp(t):
    """backup.write_status's format: local time, to the second."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


class QT1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_status(self, **fields):
        data = {"last_ok": None, "last_error": None, "error": None, "commit": "abc1234"}
        data.update(fields)
        with open(os.path.join(self.tmp, "backup_status.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # ---- the window ------------------------------------------------------------------------------------------------------
    def test_a_backup_that_finished_during_the_run_is_found(self):
        t0 = time.time() - 1000
        self.write_status(last_ok=stamp(t0 + 500))
        self.assertEqual(quick_tests.backup_finished_between(t0, t0 + 1010, self.tmp), stamp(t0 + 500))

    def test_a_failed_backup_during_the_run_counts_too(self):
        t0 = time.time() - 1000
        self.write_status(last_ok=stamp(t0 - 3600), last_error=stamp(t0 + 20))
        self.assertEqual(quick_tests.backup_finished_between(t0, t0 + 1010, self.tmp), stamp(t0 + 20))

    def test_a_backup_before_or_after_the_run_is_not(self):
        t0 = time.time() - 1000
        self.write_status(last_ok=stamp(t0 - 120), last_error=stamp(t0 + 1200))
        self.assertIsNone(quick_tests.backup_finished_between(t0, t0 + 1010, self.tmp))

    def test_no_file_a_broken_file_or_odd_values_never_raise(self):
        self.assertIsNone(quick_tests.backup_finished_between(0, time.time(), self.tmp))
        with open(os.path.join(self.tmp, "backup_status.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(quick_tests.backup_finished_between(0, time.time(), self.tmp))
        with open(os.path.join(self.tmp, "backup_status.json"), "w", encoding="utf-8") as f:
            f.write("[1, 2]")
        self.assertIsNone(quick_tests.backup_finished_between(0, time.time(), self.tmp))
        self.write_status(last_ok=12345, last_error="yesterday")
        self.assertIsNone(quick_tests.backup_finished_between(0, time.time(), self.tmp))

    # ---- what is still blamed ----------------------------------------------------------------------------------------------
    def test_only_the_backups_own_two_files_are_left_out(self):
        t0 = time.time() - 1000
        self.write_status(last_ok=stamp(t0 + 500))
        changed = ["backup.log", "backup_status.json", "crash_log.txt", os.path.join("saves", "current_game.jsonl")]
        blamed, when = quick_tests.split_backup_changes(changed, t0, t0 + 1010, self.tmp)
        self.assertEqual(blamed, ["crash_log.txt", os.path.join("saves", "current_game.jsonl")])
        self.assertEqual(when, stamp(t0 + 500))

    def test_without_a_backup_in_the_window_everything_is_blamed(self):
        t0 = time.time() - 1000
        self.write_status(last_ok=stamp(t0 - 120))
        blamed, when = quick_tests.split_backup_changes(["backup.log", "backup_status.json"], t0, t0 + 1010, self.tmp)
        self.assertEqual(blamed, ["backup.log", "backup_status.json"])
        self.assertIsNone(when)

    # ---- 18 Oct 2026: a backup still running when the suite ended ------------------------------------------------------------
    def log_header(self, t):
        with open(os.path.join(self.tmp, "backup.log"), "a", encoding="utf-8") as f:
            f.write("--- " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + "\nLocal copy: no changes since x.zip.\n")

    def test_a_backup_still_running_at_the_end_is_found_from_backup_log(self):
        # Karl's run of 18 Oct ended inside the 10:06 backup: backup.log changed, backup_status.json not yet.
        t0 = time.time() - 1000
        self.write_status(last_ok=stamp(t0 - 1800))
        self.log_header(t0 - 1800)
        self.log_header(t0 + 990)
        blamed, when = quick_tests.split_backup_changes(["backup.log"], t0, t0 + 1000, self.tmp)
        self.assertEqual(blamed, [])
        self.assertEqual(when, stamp(t0 + 990))

    def test_a_backup_log_header_from_before_the_run_is_not_counted(self):
        t0 = time.time() - 1000
        self.write_status(last_ok=stamp(t0 - 1800))
        self.log_header(t0 - 1800)
        with open(os.path.join(self.tmp, "backup.log"), "a", encoding="utf-8") as f:
            f.write("a test wrote this\n")
        self.assertIsNone(quick_tests.backup_started_between(t0, t0 + 1000, self.tmp))
        blamed, when = quick_tests.split_backup_changes(["backup.log"], t0, t0 + 1000, self.tmp)
        self.assertEqual(blamed, ["backup.log"])
        self.assertIsNone(when)

    def test_no_backup_log_or_a_broken_header_never_raises(self):
        self.assertIsNone(quick_tests.backup_started_between(0, time.time(), self.tmp))
        with open(os.path.join(self.tmp, "backup.log"), "w", encoding="utf-8") as f:
            f.write("--- 2026-99-99 99:99:99\n")
        self.assertIsNone(quick_tests.backup_started_between(0, time.time(), self.tmp))

    def test_main_passes_when_the_suite_ends_inside_a_backup(self):
        self.bump("backup.log")
        self.write_status(last_ok=stamp(time.time() - 7200))
        code, out = self.run_main(lambda: (self.log_header(time.time()), self.bump("backup.log", "")))
        self.assertEqual(code, 0, out)
        self.assertIn("the scheduled backup ran during the tests", out)

    # ---- main(), with the suite and the folder stood in -----------------------------------------------------------------------
    def run_main(self, during):
        """main() over self.tmp; `during` stands in for the test suite (it may write files)."""
        orig_snapshot, orig_split = quick_tests.snapshot, quick_tests.split_backup_changes

        def fake_run(*_a, **_k):
            during()
            return mock.Mock(returncode=0)

        out = io.StringIO()
        with mock.patch.object(quick_tests, "snapshot", lambda: orig_snapshot(self.tmp)), \
                mock.patch.object(quick_tests, "split_backup_changes", lambda c, a, b: orig_split(c, a, b, self.tmp)), \
                mock.patch.object(quick_tests, "find_reserved_names", lambda: []), \
                mock.patch.object(quick_tests.subprocess, "run", fake_run), contextlib.redirect_stdout(out):
            code = quick_tests.main()
        return code, out.getvalue()

    def bump(self, name, text="x\n"):
        path = os.path.join(self.tmp, name)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 5))

    def test_the_scheduled_backup_during_the_run_passes_and_says_so(self):
        self.bump("backup.log")
        self.write_status(last_ok=stamp(time.time() - 7200))

        def backup_runs():
            self.bump("backup.log", "Backed up.\n")
            self.write_status(last_ok=stamp(time.time()))
            st = os.stat(os.path.join(self.tmp, "backup_status.json"))
            os.utime(os.path.join(self.tmp, "backup_status.json"), (st.st_atime, st.st_mtime + 5))

        code, out = self.run_main(backup_runs)
        self.assertEqual(code, 0, out)
        self.assertIn("the scheduled backup ran during the tests", out)
        self.assertIn("all clear", out)

    def test_a_test_writing_backup_log_without_a_backup_still_fails(self):
        self.bump("backup.log")
        self.write_status(last_ok=stamp(time.time() - 7200))
        code, out = self.run_main(lambda: self.bump("backup.log", "a test wrote this\n"))
        self.assertEqual(code, 1, out)
        self.assertIn("backup.log", out.split("changed during the run")[1])
        self.assertNotIn("the scheduled backup ran", out)

    def test_a_test_writing_the_crash_log_fails_even_while_a_backup_ran(self):
        self.bump("crash_log.txt")
        self.write_status(last_ok=stamp(time.time() - 7200))

        def both():
            self.bump("crash_log.txt", "a test wrote this\n")
            self.write_status(last_ok=stamp(time.time()))

        code, out = self.run_main(both)
        self.assertEqual(code, 1, out)
        blamed = out.split("changed during the run")[1]
        self.assertIn("crash_log.txt", blamed)
        self.assertNotIn("backup_status.json", blamed)


if __name__ == "__main__":
    unittest.main()
