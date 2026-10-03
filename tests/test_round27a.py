# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 27a acceptance tests: backups that never fail silently, the deck-screen banner, branch-aware
pushes, quick/hygienic tests, and the restore drill. See claude/SONNET_SPEC_R27A_2026-09-25.md §9."""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup
import backup_banner
import reporting
import version
from tests.live import live_enabled, live_problem
from tests.test_backup import BackupBase, GIT_ENV, sh, write

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- 1. unexpected errors are logged, not swallowed --------------------------------------------------------------

class UnexpectedErrorTests(BackupBase):
    def test_an_unexpected_error_in_the_local_stage_is_logged_and_github_still_runs(self):
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('changed')\n")
        with mock.patch.object(backup, "local_backup", side_effect=ValueError("boom")):
            with self.assertRaises(backup.BackupError) as ctx:
                backup.run_backup(self.proj)
        self.assertIn("LOCAL COPY: unexpected error: ValueError: boom", str(ctx.exception))
        # GitHub stage still ran and uploaded the change
        self.assertIn("game.py", self.remote_files())

    def test_run_backup_and_report_status_writes_the_error_to_the_status_file(self):
        self.setup_repo()
        with mock.patch.object(backup, "local_backup", side_effect=ValueError("boom")):
            with self.assertRaises(backup.BackupError):
                backup.run_backup_and_report_status(self.proj)
        with open(os.path.join(self.proj, backup.STATUS_FILE)) as f:
            status = json.load(f)
        self.assertIsNotNone(status["last_error"])
        self.assertIn("boom", status["error"])

    def test_main_returns_3_only_for_a_truly_unexpected_error_outside_run_backup(self):
        self.setup_repo()
        with mock.patch.object(backup, "run_backup_and_report_status", side_effect=RuntimeError("nope")):
            code, _out = self.run_cli()
        self.assertEqual(code, 3)

    def test_main_returns_2_for_an_ordinary_backuperror(self):
        # no --setup yet: pushing with no remote configured is a plain, expected BackupError
        code, out = self.run_cli("--local-only")
        self.assertEqual(code, 0, out)                          # local-only never touches git, so this one just works
        code2, out2 = self.run_cli()                             # but the GitHub stage, with no remote, fails in an expected way
        self.assertEqual(code2, 2, out2)


# ---- 2. reserved names and old timestamps -------------------------------------------------------------------------

class ReservedNameTests(BackupBase):
    def test_is_reserved_name_covers_the_windows_device_names(self):
        for name in ("nul", "NUL", "nul.txt", "con", "prn", "aux", "com1", "COM9.log", "lpt1", "lpt9.bak"):
            self.assertTrue(backup.is_reserved_name(name), name)
        for name in ("null.txt", "console.py", "auxiliary.txt", "comedy.txt", "lpt10.txt"):
            self.assertFalse(backup.is_reserved_name(name), name)

    def test_snapshot_files_skips_a_reserved_name_and_reports_why(self):
        # Windows itself refuses to create a real file named "nul" (any open of that path component hits
        # the NUL device, not a file) - the very thing that broke the real backup. So this can't be tested
        # by writing an actual "nul" file on Windows; it has to fake os.walk() reporting one instead, the
        # same way it would look if git checked one out or a Linux session created one through this folder.
        real_walk = os.walk

        def fake_walk(top, *a, **kw):
            for root, dirs, names in real_walk(top, *a, **kw):
                if os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(self.proj)):
                    names = list(names) + ["nul"]
                yield root, dirs, names

        with mock.patch.object(backup.os, "walk", side_effect=fake_walk):
            files, left_out = backup.snapshot_files(self.proj, os.path.join(self.tmp, "dest"))
        self.assertNotIn("nul", [rel for rel, _full in files])
        reasons = dict(left_out)
        self.assertIn("nul", reasons)
        self.assertIn("Windows reserved name", reasons["nul"])

    def test_problems_with_flags_a_staged_reserved_name(self):
        # problems_with() only needs the STAGED entry - a (status, path) pair - to check the name; it does
        # not need the file to really exist for the reserved-name check, so no real "nul" file is needed.
        big, secret, reserved = backup.problems_with(self.proj, [("A", "nul")])
        self.assertEqual(reserved, ["nul"])

    def test_backup_refuses_a_staged_reserved_name_with_the_delete_command(self):
        self.setup_repo()
        with mock.patch.object(backup, "staged_files", return_value=[("A", "nul")]):
            with self.assertRaises(backup.BackupError) as ctx:
                backup.backup(self.proj)
        self.assertIn("cmd /c del", str(ctx.exception))

    def test_a_file_with_mtime_zero_zips_fine(self):
        target = os.path.join(self.proj, "old.txt")
        write(target, "ancient")
        os.utime(target, (0, 0))
        dest = os.path.join(self.tmp, "dest")
        result = backup.take_snapshot(self.proj, dest)
        self.assertTrue(result.get("made"))
        zpath = os.path.join(dest, "snapshots", result["made"])
        with zipfile.ZipFile(zpath) as z:
            self.assertIsNone(z.testzip())                       # the existing self-check: the zip reads back cleanly


# ---- 3. stale git lock ------------------------------------------------------------------------------------------

class StaleLockTests(BackupBase):
    def _make_lock(self, age_minutes):
        self.setup_repo()
        lock = os.path.join(self.proj, ".git", "index.lock")
        write(lock, "")
        old = datetime.datetime.now() - datetime.timedelta(minutes=age_minutes)
        ts = old.timestamp()
        os.utime(lock, (ts, ts))
        return lock

    def test_an_old_lock_with_no_git_running_is_removed(self):
        lock = self._make_lock(backup.LOCK_STALE_MINUTES + 5)
        with mock.patch.object(backup, "git_process_running", return_value=False):
            backup.clear_stale_lock(self.proj)
        self.assertFalse(os.path.exists(lock))

    def test_a_fresh_lock_is_left_alone_and_the_run_gives_up_with_a_clear_message(self):
        lock = self._make_lock(1)
        with mock.patch.object(backup, "git_process_running", return_value=False):
            with self.assertRaises(backup.BackupError) as ctx:
                backup.clear_stale_lock(self.proj)
        self.assertIn("minute", str(ctx.exception))
        self.assertTrue(os.path.exists(lock))

    def test_an_old_lock_is_left_alone_while_git_is_running(self):
        lock = self._make_lock(backup.LOCK_STALE_MINUTES + 5)
        with mock.patch.object(backup, "git_process_running", return_value=True):
            with self.assertRaises(backup.BackupError) as ctx:
                backup.clear_stale_lock(self.proj)
        self.assertIn("running", str(ctx.exception))
        self.assertTrue(os.path.exists(lock))


# ---- 4. push names HEAD, not a hardcoded branch ---------------------------------------------------------------

class PushDestinationTests(BackupBase):
    def test_the_push_command_names_head_and_follow_tags(self):
        self.setup_repo()
        sh(["switch", "-c", "round-99"], self.proj)
        write(os.path.join(self.proj, "game.py"), "print('on a branch')\n")
        seen = []
        real_git = backup.git

        def spy(args, folder, check=True, timeout=120):
            if args and args[0] == "push":
                seen.append(args)
            return real_git(args, folder, check, timeout)

        with mock.patch.object(backup, "git", side_effect=spy):
            backup.backup(self.proj)
        self.assertTrue(seen, "no push happened")
        self.assertEqual(seen[-1], ["push", "-u", "origin", "HEAD", "--follow-tags"])


# ---- 5. backup_status.json ----------------------------------------------------------------------------------------

class StatusFileTests(BackupBase):
    def test_written_atomically_on_ok_and_on_error(self):
        path = os.path.join(self.proj, backup.STATUS_FILE)
        backup.write_status(self.proj, True)
        with open(path) as f:
            ok_status = json.load(f)
        self.assertIsNotNone(ok_status["last_ok"])
        self.assertIsNone(ok_status["error"])
        backup.write_status(self.proj, False, "it broke")
        with open(path) as f:
            err_status = json.load(f)
        self.assertEqual(err_status["error"], "it broke")
        self.assertEqual(err_status["last_ok"], ok_status["last_ok"])   # the earlier success is kept, not wiped

    def test_ignored_by_git_and_by_the_snapshot(self):
        self.assertIn(backup.STATUS_FILE, backup.SKIP_FILES)
        with open(os.path.join(self.proj, ".gitignore")) as f:
            self.assertIn(backup.STATUS_FILE, f.read())
        write(os.path.join(self.proj, backup.STATUS_FILE), "{}")
        files, _left_out = backup.snapshot_files(self.proj, os.path.join(self.tmp, "dest"))
        self.assertNotIn(backup.STATUS_FILE, [rel for rel, _full in files])


# ---- 6. the deck-screen banner --------------------------------------------------------------------------------

class BannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, **fields):
        with open(os.path.join(self.tmp, backup_banner.STATUS_FILE), "w") as f:
            json.dump(fields, f)

    def test_no_file_means_no_banner(self):
        self.assertIsNone(backup_banner.banner_text(self.tmp))

    def test_fresh_ok_means_no_banner(self):
        now = datetime.datetime.now()
        self._write(last_ok=now.isoformat(timespec="seconds"), last_error=None, error=None)
        self.assertIsNone(backup_banner.banner_text(self.tmp, now=now))

    def test_an_error_after_the_last_ok_shows_the_banner(self):
        now = datetime.datetime.now()
        self._write(last_ok=(now - datetime.timedelta(minutes=10)).isoformat(timespec="seconds"),
                    last_error=(now - datetime.timedelta(minutes=1)).isoformat(timespec="seconds"),
                    error="GITHUB: unexpected error: OSError: disk full")
        text = backup_banner.banner_text(self.tmp, now=now)
        self.assertIsNotNone(text)
        self.assertIn("disk full", text)
        self.assertIn("backup.log", text)

    def test_a_stale_last_ok_shows_the_banner_even_with_no_error(self):
        now = datetime.datetime.now()
        self._write(last_ok=(now - datetime.timedelta(hours=3)).isoformat(timespec="seconds"), last_error=None, error=None)
        self.assertIsNotNone(backup_banner.banner_text(self.tmp, now=now))

    def test_an_ok_less_than_two_hours_old_shows_nothing(self):
        now = datetime.datetime.now()
        self._write(last_ok=(now - datetime.timedelta(hours=1)).isoformat(timespec="seconds"), last_error=None, error=None)
        self.assertIsNone(backup_banner.banner_text(self.tmp, now=now))

    def test_discord_alert_posts_once_per_streak_and_never_without_a_webhook(self):
        now = datetime.datetime.now()
        self._write(last_ok=None, last_error=now.isoformat(timespec="seconds"), error="boom")
        with mock.patch.object(reporting, "webhook_url", return_value=None):
            with mock.patch.object(reporting, "post_to_discord") as post:
                backup_banner.maybe_alert_discord(self.tmp, now=now)
        post.assert_not_called()                                # no webhook configured: skip silently

        with mock.patch.object(reporting, "webhook_url", return_value="https://discord.com/api/webhooks/1/x"):
            with mock.patch.object(reporting, "post_to_discord", return_value=(True, "ok", "")) as post:
                backup_banner.maybe_alert_discord(self.tmp, now=now)
                backup_banner.maybe_alert_discord(self.tmp, now=now)    # same streak: must not post twice
        self.assertEqual(post.call_count, 1)
        text = post.call_args[0][1]
        self.assertNotIn("https://discord.com", text)            # never leaks the webhook address itself
        self.assertNotIn("Traceback", text)


# ---- 8. MANTICORE_SKIP_LIVE ---------------------------------------------------------------------------------------

class SkipLiveTests(unittest.TestCase):
    def test_skip_live_wins_even_when_forge_is_ready(self):
        with mock.patch.dict(os.environ, {"MANTICORE_SKIP_LIVE": "1"}):
            with mock.patch("forge_client.runtime_problem", return_value=None):
                self.assertFalse(live_enabled())
                self.assertEqual(live_problem(), "MANTICORE_SKIP_LIVE is set")

    def test_without_the_flag_it_defers_to_forge_client(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MANTICORE_SKIP_LIVE", None)
            with mock.patch("forge_client.runtime_problem", return_value="Java is missing"):
                self.assertEqual(live_problem(), "Java is missing")
            with mock.patch("forge_client.runtime_problem", return_value=None):
                self.assertTrue(live_enabled())


# ---- 9. tools/quick_tests.py: the hygiene and reserved-name checkers ------------------------------------------

class QuickTestsHelperTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import quick_tests
        self.quick_tests = quick_tests
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _walk_with_extra_file(self, top, rel_dir, extra_name):
        """A fake os.walk() that reports one extra file name inside rel_dir, without needing to actually
        create it - Windows refuses to create a real file named "nul"/"con"/etc. (any open of that path
        component hits the reserved device, not a file), so these tests fake the directory listing
        instead, the same way ReservedNameTests.test_snapshot_files_... does for backup.py."""
        real_walk = os.walk
        target = os.path.normcase(os.path.abspath(os.path.join(top, rel_dir))) if rel_dir else \
            os.path.normcase(os.path.abspath(top))

        def fake_walk(path, *a, **kw):
            for root, dirs, names in real_walk(path, *a, **kw):
                if os.path.normcase(os.path.abspath(root)) == target:
                    names = list(names) + [extra_name]
                yield root, dirs, names
        return fake_walk

    def test_finds_a_reserved_name_anywhere_in_the_tree(self):
        os.makedirs(os.path.join(self.tmp, "sub"))
        with mock.patch("os.walk", side_effect=self._walk_with_extra_file(self.tmp, "sub", "NUL")):
            found = self.quick_tests.find_reserved_names(self.tmp)
        self.assertEqual(found, [os.path.join("sub", "NUL")])

    def test_ignores_reserved_names_inside_skipped_top_level_folders(self):
        os.makedirs(os.path.join(self.tmp, "forge_runtime"))
        with mock.patch("os.walk", side_effect=self._walk_with_extra_file(self.tmp, "forge_runtime", "con")):
            self.assertEqual(self.quick_tests.find_reserved_names(self.tmp), [])

    def test_snapshot_notices_a_changed_watched_file(self):
        os.makedirs(os.path.join(self.tmp, "saves"))
        target = os.path.join(self.tmp, "crash_log.txt")
        with open(target, "w") as f:
            f.write("before")
        before = self.quick_tests.snapshot(self.tmp)
        with open(target, "w") as f:
            f.write("after, longer than before")
        after = self.quick_tests.snapshot(self.tmp)
        self.assertNotEqual(before, after)


# ---- 5/10. restore drill --------------------------------------------------------------------------------------

class RestoreDrillTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import restore_drill
        self.rd = restore_drill
        self.tmp = tempfile.mkdtemp()
        self.saved_env = {k: os.environ.get(k) for k in GIT_ENV}
        os.environ.update(GIT_ENV)
        self.bare = os.path.join(self.tmp, "bare.git")
        sh(["init", "-q", "--bare", self.bare], self.tmp)
        sh(["symbolic-ref", "HEAD", "refs/heads/main"], self.bare)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        write(os.path.join(self.proj, "a.py"), "print(1)\n")
        sh(["init", "-q", "-b", "main"], self.proj)
        sh(["config", "user.email", "t@example.com"], self.proj)
        sh(["config", "user.name", "t"], self.proj)
        sh(["remote", "add", "origin", self.bare], self.proj)
        sh(["add", "-A"], self.proj)
        sh(["commit", "-q", "-m", "init"], self.proj)
        sh(["push", "-q", "-u", "origin", "main"], self.proj)
        os.makedirs(os.path.join(self.tmp, "backups", "snapshots"))
        with open(os.path.join(self.proj, "backup_settings.json"), "w") as f:
            json.dump({"local_dir": os.path.join(self.tmp, "backups")}, f)

    def tearDown(self):
        import shutil
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_zip(self, name="commander_sim_2026-09-25_09-00-00.zip", extra=None):
        zpath = os.path.join(self.tmp, "backups", "snapshots", name)
        with zipfile.ZipFile(zpath, "w") as z:
            z.write(os.path.join(self.proj, "a.py"), "a.py")
            if extra:
                z.writestr(extra, "extra")
        return zpath

    def test_matches_a_clean_temporary_repo_and_zip(self):
        self._make_zip()
        ok, msg = self.rd.clone_github(self.proj, os.path.join(self.tmp, "gh"))
        self.assertTrue(ok, msg)
        gh_files = self.rd.tracked_files(os.path.join(self.tmp, "gh"))
        self.assertTrue(self.rd.compare("t", self.proj, os.path.join(self.tmp, "gh"), gh_files, lambda rel: True))

    def test_reports_a_changed_file(self):
        zpath = self._make_zip()
        zdir = os.path.join(self.tmp, "from_zip")
        self.rd.unzip_snapshot(zpath, zdir)
        with open(os.path.join(zdir, "a.py"), "w") as f:
            f.write("print('tampered')\n")
        buf_ok = self.rd.compare("t", self.proj, zdir, self.rd.zipped_files(zpath), lambda rel: True)
        self.assertFalse(buf_ok)

    def test_reports_a_missing_file(self):
        write(os.path.join(self.proj, "b.py"), "print(2)\n")
        sh(["add", "b.py"], self.proj)
        sh(["commit", "-q", "-m", "add b.py"], self.proj)        # tracked_files() (git ls-files) only sees committed files
        zpath = self._make_zip()                                 # zip made before b.py existed
        zdir = os.path.join(self.tmp, "from_zip")
        self.rd.unzip_snapshot(zpath, zdir)
        ok = self.rd.compare("t", self.proj, zdir, self.rd.zipped_files(zpath), lambda rel: rel == "b.py")
        self.assertTrue(ok)                                      # b.py missing, but explicitly marked as expected
        not_ok = self.rd.compare("t", self.proj, zdir, self.rd.zipped_files(zpath), lambda rel: False)
        self.assertFalse(not_ok)                                 # same gap, not marked expected: a real problem

    def test_no_remote_configured_is_reported_plainly(self):
        sh(["remote", "remove", "origin"], self.proj)
        ok, msg = self.rd.clone_github(self.proj, os.path.join(self.tmp, "gh"))
        self.assertFalse(ok)
        self.assertIn("No GitHub remote", msg)


if __name__ == "__main__":
    unittest.main()
