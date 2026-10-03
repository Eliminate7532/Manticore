# SPDX-License-Identifier: GPL-3.0-or-later
"""backup.py: back the project up to a git remote. Uses a local bare repository as the stand-in for GitHub (no network)."""
import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def sh(args, cwd, check=True):
    env = dict(os.environ, **GIT_ENV)
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode:
        raise AssertionError(p.stderr + p.stdout)
    return p.stdout.strip()


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


class BackupBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved_env = {k: os.environ.get(k) for k in GIT_ENV}
        os.environ.update(GIT_ENV)                                  # no personal git settings leak into the tests
        self.remote = os.path.join(self.tmp, "remote.git")
        sh(["init", "-q", "--bare", self.remote], self.tmp)
        sh(["symbolic-ref", "HEAD", "refs/heads/main"], self.remote)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        for f in (".gitignore", ".gitattributes"):
            shutil.copy(os.path.join(HERE, f), os.path.join(self.proj, f))
        write(os.path.join(self.proj, "game.py"), "print('hi')\n")
        write(os.path.join(self.proj, "README.txt"), "hello\r\nworld\r\n")
        write(os.path.join(self.proj, "forge_bundle", "forge_runtime.tar.xz.part01"), b"\x00\r\n\xff\r\n" * 500)
        write(os.path.join(self.proj, "java_bridge", "forge_bridge.jar"), b"PK\x03\x04\r\n\r\n")
        write(os.path.join(self.proj, "my_decks", "Tymna.txt"), "1 Tymna the Weaver\n")

    def tearDown(self):
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = backup.main(list(argv), self.proj)
        return code, buf.getvalue()

    def setup_repo(self):
        return self.run_cli("--setup", self.remote, "--name", "Test", "--email", "t@example.com")

    def remote_files(self):
        return set(sh(["ls-tree", "-r", "--name-only", "main"], self.remote).splitlines())

    def make_ignored(self):
        for rel in ("forge_runtime/forge.jar", "cache/images/sol_ring.jpg", "forge_engine.log", "forge_decks/player.dck", "settings.json",
                    "__pycache__/a.cpython-314.pyc", "tests/__pycache__/b.pyc", "backup.log", ".env", "bug_report_config.json",
                    "bug_reports/bugreport_1.zip", "soak_runs/soak_summary.txt"):
            write(os.path.join(self.proj, rel), "private")


class BackupTests(BackupBase):
    def test_setup_makes_the_first_backup_and_leaves_out_the_right_things(self):
        self.make_ignored()
        code, out = self.setup_repo()
        self.assertEqual(code, 0, out)
        self.assertIn("Backed up", out)
        files = self.remote_files()
        for want in ("game.py", "README.txt", ".gitignore", ".gitattributes", "my_decks/Tymna.txt", "java_bridge/forge_bridge.jar",
                     "forge_bundle/forge_runtime.tar.xz.part01"):
            self.assertIn(want, files)
        for skip in ("forge_runtime/forge.jar", "cache/images/sol_ring.jpg", "forge_engine.log", "forge_decks/player.dck",
                     "settings.json", "__pycache__/a.cpython-314.pyc", "tests/__pycache__/b.pyc", "backup.log", ".env",
                     "bug_report_config.json", "bug_reports/bugreport_1.zip"):
            self.assertNotIn(skip, files)
        self.assertEqual(sh(["symbolic-ref", "--short", "HEAD"], self.proj), "main")

    def test_the_packed_engine_and_jars_come_back_byte_for_byte(self):
        self.setup_repo()
        clone = os.path.join(self.tmp, "clone")
        sh(["clone", "-q", self.remote, clone], self.tmp)
        for rel in ("forge_bundle/forge_runtime.tar.xz.part01", "java_bridge/forge_bridge.jar"):
            with open(os.path.join(self.proj, rel), "rb") as a, open(os.path.join(clone, rel), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)

    def test_a_run_with_no_changes_makes_no_new_backup(self):
        self.setup_repo()
        before = sh(["rev-parse", "HEAD"], self.proj)
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("No new changes", out)
        self.assertEqual(sh(["rev-parse", "HEAD"], self.proj), before)

    def test_a_change_is_saved_with_my_message_and_uploaded(self):
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('changed')\n")
        write(os.path.join(self.proj, "new_file.py"), "1\n")
        os.remove(os.path.join(self.proj, "my_decks", "Tymna.txt"))
        code, out = self.run_cli("Added the deck screen")
        self.assertEqual(code, 0, out)
        self.assertEqual(sh(["log", "-1", "--format=%s"], self.remote), "Added the deck screen")
        self.assertEqual(sh(["rev-parse", "HEAD"], self.proj), sh(["rev-parse", "main"], self.remote))
        self.assertIn("new_file.py", self.remote_files())
        self.assertNotIn("my_decks/Tymna.txt", self.remote_files())

    def test_default_message_lists_the_files(self):
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('again')\n")
        self.run_cli()
        msg = sh(["log", "-1", "--format=%B"], self.remote)
        self.assertTrue(msg.startswith("Backup "), msg)
        self.assertIn("changed: game.py", msg)

    def test_old_versions_are_never_lost(self):
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('v2')\n")
        self.run_cli("second")
        first = sh(["rev-list", "--max-parents=0", "main"], self.remote)
        self.assertEqual(sh(["show", f"{first}:game.py"], self.remote), "print('hi')")
        self.assertEqual(int(sh(["rev-list", "--count", "main"], self.remote)), 2)

    def test_a_failed_upload_keeps_the_change_and_the_next_run_uploads_it(self):
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('offline edit')\n")
        moved = self.remote + ".away"
        os.rename(self.remote, moved)
        code, out = self.run_cli("edited while offline")
        self.assertEqual(code, 2)
        self.assertIn("PROBLEM", out)
        self.assertEqual(sh(["log", "-1", "--format=%s"], self.proj), "edited while offline")      # saved on this computer
        code, out = self.run_cli("--status")
        self.assertEqual(code, 1)
        self.assertIn("NOT uploaded", out)
        os.rename(moved, self.remote)
        code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(sh(["log", "-1", "--format=%s"], self.remote), "edited while offline")
        code, out = self.run_cli("--status")
        self.assertEqual(code, 0)
        self.assertIn("Everything is backed up", out)

    def test_status_says_what_is_not_saved(self):
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('dirty')\n")
        code, out = self.run_cli("--status")
        self.assertEqual(code, 1)
        self.assertIn("NOT saved yet", out)
        self.assertIn("game.py", out)

    def test_a_file_github_would_refuse_stops_the_backup_and_saves_nothing(self):
        self.setup_repo()
        head = sh(["rev-parse", "HEAD"], self.proj)
        old = backup.MAX_FILE_BYTES
        backup.MAX_FILE_BYTES = 1000
        try:
            write(os.path.join(self.proj, "big.bin"), b"\x01" * 5000)
            write(os.path.join(self.proj, "game.py"), "print('also changed')\n")
            code, out = self.run_cli()
        finally:
            backup.MAX_FILE_BYTES = old
        self.assertEqual(code, 2)
        self.assertIn("big.bin", out)
        self.assertEqual(sh(["rev-parse", "HEAD"], self.proj), head)
        self.assertEqual(sh(["diff", "--cached", "--name-only"], self.proj), "")          # nothing left half-staged

    def test_a_password_looking_file_stops_the_backup(self):
        self.setup_repo()
        write(os.path.join(self.proj, "github_token.txt"), "ghp_notreal")
        code, out = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("github_token.txt", out)
        self.assertNotIn("github_token.txt", self.remote_files())

    def test_dotenv_is_ignored_so_it_never_even_gets_that_far(self):
        self.setup_repo()
        write(os.path.join(self.proj, ".env"), "SECRET=1")
        code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertNotIn(".env", self.remote_files())

    def test_without_a_name_and_email_it_explains_what_to_type(self):
        sh(["init", "-q"], self.proj)
        sh(["symbolic-ref", "HEAD", "refs/heads/main"], self.proj)
        sh(["remote", "add", "origin", self.remote], self.proj)
        code, out = self.run_cli("--auto")
        self.assertEqual(code, 2)
        with open(os.path.join(self.proj, "backup.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("--name", log)
        self.assertIn("--email", log)

    def test_auto_mode_writes_a_log_and_uploads(self):
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('auto')\n")
        code, out = self.run_cli("--auto")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")                                                            # nothing on screen; it goes to the log
        with open(os.path.join(self.proj, "backup.log"), encoding="utf-8") as f:
            self.assertIn("Backed up", f.read())
        self.assertTrue(sh(["log", "-1", "--format=%s"], self.remote).startswith("Automatic backup"))

    def test_before_setup_it_says_to_run_setup(self):
        code, out = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("--setup", out)

    def test_an_online_copy_with_other_changes_is_never_overwritten(self):
        other = os.path.join(self.tmp, "other")
        sh(["clone", "-q", self.remote, other], self.tmp)
        sh(["config", "user.name", "O"], other)
        sh(["config", "user.email", "o@example.com"], other)
        sh(["checkout", "-q", "-b", "main"], other)
        write(os.path.join(other, "README.md"), "created on github\n")
        sh(["add", "-A"], other)
        sh(["commit", "-q", "-m", "created on github"], other)
        sh(["push", "-q", "origin", "main"], other)
        code, out = self.setup_repo()
        self.assertEqual(code, 2)
        self.assertIn("Nothing was overwritten", out)
        self.assertEqual(sh(["log", "-1", "--format=%s"], self.remote), "created on github")

    def test_setup_can_be_run_again_with_a_new_address(self):
        self.setup_repo()
        new_remote = os.path.join(self.tmp, "second.git")
        sh(["init", "-q", "--bare", new_remote], self.tmp)
        sh(["symbolic-ref", "HEAD", "refs/heads/main"], new_remote)
        code, out = self.run_cli("--setup", new_remote)
        self.assertEqual(code, 0, out)
        self.assertEqual(sh(["remote", "get-url", "origin"], self.proj), new_remote)
        self.assertEqual(sh(["rev-parse", "main"], new_remote), sh(["rev-parse", "HEAD"], self.proj))

    def test_a_folder_inside_someone_elses_repo_is_not_mistaken_for_ours(self):
        sub = os.path.join(self.tmp, "outer", "inner")
        os.makedirs(sub)
        sh(["init", "-q"], os.path.join(self.tmp, "outer"))
        self.assertFalse(backup.is_repo(sub))
        self.assertTrue(backup.is_repo(os.path.join(self.tmp, "outer")))

    def test_the_real_ignore_rules(self):
        """The project's own .gitignore: generated and private things out, everything the game needs in."""
        repo = os.path.join(self.tmp, "rules")
        os.makedirs(repo)
        shutil.copy(os.path.join(HERE, ".gitignore"), repo)
        sh(["init", "-q"], repo)
        ignored = ["forge_runtime/forge.jar", "forge_runtime/res/cardsfolder/a/x.txt", "cache/card_data.json", "cache/images/a.jpg",
                   "forge_engine.log", "forge_decks/player.dck", "settings.json", "__pycache__/x.pyc", "tests/__pycache__/y.pyc",
                   "game.jsonl", "backup.log", ".env", "backup_settings.json", "bug_report_config.json", "bug_reports/bugreport_1.zip",
                   "soak_runs/soak_summary.txt", "soak_runs/game_000/record.jsonl"]
        kept = ["forge_table.py", "backup.py", "backup.bat", "forge_bundle/forge_runtime.tar.xz.part01", "forge_bundle/forge_runtime.manifest.json",
                "java_bridge/forge_bridge.jar", "java_bridge/src/forge/bridge/Main.java", "my_decks/Tymna.txt",
                "my_decks/_removed/Old.txt", "sample_decks/kinnan_nbc_moxfield_export.txt", "tests/fixtures/forge_states/coin_toss.json",
                "requirements.txt", "README.txt", "CLAUDE.md", "bug_report_config.example.json", "reporting.py"]
        for path in ignored:
            self.assertEqual(sh(["check-ignore", path], repo, check=False), path, f"{path} should be ignored")
        for path in kept:
            self.assertEqual(sh(["check-ignore", path], repo, check=False), "", f"{path} should be backed up")

    def test_the_real_project_passes_the_safety_checks(self):
        """Dry run on the real project files (copied, without the big generated folders): nothing in it looks like a secret or is too big."""
        copy = os.path.join(self.tmp, "real")
        shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("forge_runtime", "cache", "__pycache__", ".git", "shots*", "docs_work",
                                                                   "forge_decks", "forge_engine.log", "settings.json",
                                                                   "soak_runs", "bug_reports", "saves"))   # round 28d fix 1: soak_runs was 42 GB and filled the disk
        sh(["init", "-q"], copy)
        sh(["add", "-A"], copy)
        staged = backup.staged_files(copy)
        self.assertGreater(len(staged), 20)
        big, secret, reserved = backup.problems_with(copy, staged)
        self.assertEqual((big, secret, reserved), ([], [], []))


class LocalBackupTests(BackupBase):
    """The local copy: dated zips outside the project, the packed engine kept once, old zips thinned."""

    def setUp(self):
        super().setUp()
        self.dest = os.path.join(self.tmp, "my_backups")

    def snaps(self, dest=None):
        d = os.path.join(dest or self.dest, "snapshots")
        return sorted(n for n in os.listdir(d)) if os.path.isdir(d) else []

    def local(self, *extra):
        return self.run_cli("--local-only", "--local-dir", self.dest, *extra)

    def names_in(self, zip_name):
        with zipfile.ZipFile(os.path.join(self.dest, "snapshots", zip_name)) as z:
            return set(z.namelist())

    def test_a_zip_holds_the_project_and_leaves_out_what_is_rebuilt_or_private(self):
        self.make_ignored()
        write(os.path.join(self.proj, "tests", "test_x.py"), "pass\n")
        code, out = self.local()
        self.assertEqual(code, 0, out)
        self.assertIn("Local copy saved", out)
        (name,) = self.snaps()
        names = self.names_in(name)
        for want in ("proj/game.py", "proj/README.txt", "proj/.gitignore", "proj/my_decks/Tymna.txt", "proj/java_bridge/forge_bridge.jar",
                     "proj/tests/test_x.py"):
            self.assertIn(want, names)
        for skip in ("proj/forge_runtime/forge.jar", "proj/cache/images/sol_ring.jpg", "proj/forge_engine.log", "proj/forge_decks/player.dck",
                     "proj/__pycache__/a.cpython-314.pyc", "proj/tests/__pycache__/b.pyc", "proj/backup.log", "proj/.env",
                     "proj/forge_bundle/forge_runtime.tar.xz.part01", "proj/bug_report_config.json", "proj/bug_reports/bugreport_1.zip",
                     "proj/soak_runs/soak_summary.txt"):
            self.assertNotIn(skip, names)
        self.assertIn("proj/settings.json", names)                     # your own settings ARE in the local copy (small, and yours)
        with zipfile.ZipFile(os.path.join(self.dest, "snapshots", name)) as z:
            self.assertEqual(z.read("proj/README.txt"), b"hello\r\nworld\r\n")           # bytes exactly as they were
            self.assertIsNone(z.testzip())
        self.assertIn(".env", out)                                     # and it says what it left out for being secret-looking

    def test_nothing_changed_means_no_new_zip(self):
        self.local()
        code, out = self.local()
        self.assertEqual(code, 0)
        self.assertIn("no changes", out)
        self.assertEqual(len(self.snaps()), 1)

    def test_touching_a_file_without_changing_it_makes_no_new_zip(self):
        self.local()
        path = os.path.join(self.proj, "game.py")
        with open(path, "rb") as f:
            data = f.read()
        os.utime(path, (1, 1))
        write(path, data)
        self.local()
        self.assertEqual(len(self.snaps()), 1)

    def test_a_change_makes_a_new_zip_and_keeps_the_old_one(self):
        self.local()
        write(os.path.join(self.proj, "game.py"), "print('v2')\n")
        code, out = self.local()
        self.assertEqual(code, 0, out)
        first, second = self.snaps()
        with zipfile.ZipFile(os.path.join(self.dest, "snapshots", first)) as z:
            self.assertEqual(z.read("proj/game.py"), b"print('hi')\n")
        with zipfile.ZipFile(os.path.join(self.dest, "snapshots", second)) as z:
            self.assertEqual(z.read("proj/game.py"), b"print('v2')\n")

    def test_a_deleted_file_is_gone_from_the_new_zip_but_still_in_the_old(self):
        self.local()
        os.remove(os.path.join(self.proj, "my_decks", "Tymna.txt"))
        self.local()
        first, second = self.snaps()
        self.assertIn("proj/my_decks/Tymna.txt", self.names_in(first))
        self.assertNotIn("proj/my_decks/Tymna.txt", self.names_in(second))

    def test_the_packed_engine_is_copied_once_exactly_and_kept_current(self):
        self.local()
        src = os.path.join(self.proj, "forge_bundle", "forge_runtime.tar.xz.part01")
        dst = os.path.join(self.dest, "forge_bundle", "forge_runtime.tar.xz.part01")
        with open(src, "rb") as a, open(dst, "rb") as b:
            self.assertEqual(a.read(), b.read())
        mtime = os.path.getmtime(dst)
        code, out = self.local()
        self.assertNotIn("refreshed", out)                             # unchanged: not copied again
        self.assertEqual(os.path.getmtime(dst), mtime)
        write(src, b"\x01\r\n" * 100)                                  # a new engine version, and an old part that no longer exists
        write(os.path.join(self.dest, "forge_bundle", "old_part99"), "stale")
        code, out = self.local()
        self.assertIn("refreshed", out)
        with open(src, "rb") as a, open(dst, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertFalse(os.path.exists(os.path.join(self.dest, "forge_bundle", "old_part99")))

    def test_the_default_place_is_next_to_the_project_never_inside_it(self):
        code, out = self.run_cli("--local-only")
        self.assertEqual(code, 0, out)
        sibling = self.proj + "_backups"
        self.assertTrue(os.path.isdir(os.path.join(sibling, "snapshots")))
        self.assertEqual(backup.local_dir(self.proj), sibling)

    def test_local_dir_is_remembered(self):
        code, out = self.run_cli("--local-dir", self.dest)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.snaps()), 1)                         # choosing a place also makes the first copy there
        with open(os.path.join(self.proj, "backup_settings.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["local_dir"], os.path.abspath(self.dest))
        write(os.path.join(self.proj, "game.py"), "print('later')\n")
        self.run_cli("--local-only")                                   # no --local-dir this time
        self.assertEqual(len(self.snaps()), 2)

    def test_a_folder_inside_the_project_is_refused(self):
        code, out = self.run_cli("--local-only", "--local-dir", os.path.join(self.proj, "copies"))
        self.assertEqual(code, 2)
        self.assertIn("OUTSIDE", out)
        self.assertFalse(os.path.exists(os.path.join(self.proj, "copies")))

    def test_the_local_copy_works_with_no_git_at_all(self):
        original = backup.find_git
        backup.find_git = lambda: (_ for _ in ()).throw(backup.BackupError(backup.GIT_HELP))
        try:
            code, out = self.local()
        finally:
            backup.find_git = original
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.snaps()), 1)
        self.assertFalse(os.path.isdir(os.path.join(self.proj, ".git")))

    def test_setup_with_no_git_still_saves_the_local_copy_and_says_what_to_install(self):
        original = backup.find_git
        backup.find_git = lambda: (_ for _ in ()).throw(backup.BackupError(backup.GIT_HELP))
        try:
            code, out = self.run_cli("--local-dir", self.dest)
            code, out = self.run_cli("--setup", self.remote, "--name", "T", "--email", "t@e.com")
        finally:
            backup.find_git = original
        self.assertEqual(code, 2)
        self.assertIn("winget install --id Git.Git", out)
        self.assertEqual(len(self.snaps()), 1)

    def test_a_normal_run_does_the_local_copy_and_github(self):
        self.run_cli("--local-dir", self.dest)
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('both')\n")
        code, out = self.run_cli("both places")
        self.assertEqual(code, 0, out)
        self.assertIn("Local copy saved", out)
        self.assertIn("Backed up", out)
        self.assertEqual(sh(["log", "-1", "--format=%s"], self.remote), "both places")
        self.assertEqual(len(self.snaps()), 2)

    def test_when_github_is_down_the_local_copy_is_still_made(self):
        self.run_cli("--local-dir", self.dest)
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('offline')\n")
        os.rename(self.remote, self.remote + ".away")
        code, out = self.run_cli("edit")
        self.assertEqual(code, 2)
        self.assertIn("GITHUB:", out)
        self.assertNotIn("LOCAL COPY:", out)
        self.assertEqual(len(self.snaps()), 2)                         # the first copy, and this edit (setup changed nothing in it)

    def test_when_the_local_place_is_missing_github_still_gets_the_backup(self):
        self.setup_repo()
        blocker = os.path.join(self.tmp, "a_file_not_a_folder")
        write(blocker, "x")
        save = {"local_dir": os.path.join(blocker, "inside")}          # a drive that is not plugged in behaves the same way
        with open(os.path.join(self.proj, "backup_settings.json"), "w", encoding="utf-8") as f:
            json.dump(save, f)
        write(os.path.join(self.proj, "game.py"), "print('remote only')\n")
        code, out = self.run_cli("still uploaded")
        self.assertEqual(code, 2)
        self.assertIn("LOCAL COPY:", out)
        self.assertIn("plug the drive in", out)
        self.assertEqual(sh(["log", "-1", "--format=%s"], self.remote), "still uploaded")

    def test_before_github_is_set_up_a_plain_run_still_makes_the_local_copy(self):
        code, out = self.run_cli("--local-dir", self.dest)
        code, out = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("--setup", out)
        self.assertNotIn("LOCAL COPY:", out)

    def test_a_big_file_is_left_out_of_the_zip_and_reported(self):
        old = backup.SNAPSHOT_MAX_FILE
        backup.SNAPSHOT_MAX_FILE = 1000
        try:
            write(os.path.join(self.proj, "huge.bin"), b"\x01" * 5000)
            code, out = self.local()
        finally:
            backup.SNAPSHOT_MAX_FILE = old
        self.assertEqual(code, 0)
        self.assertIn("huge.bin", out)
        self.assertNotIn("proj/huge.bin", self.names_in(self.snaps()[0]))

    def test_an_interrupted_run_leaves_no_half_written_zip_behind(self):
        os.makedirs(os.path.join(self.dest, "snapshots"))
        write(os.path.join(self.dest, "snapshots", "commander_sim_2026-01-01_00-00-00.zip.partial"), "half")
        self.local()
        self.assertEqual([n for n in self.snaps() if n.endswith(".partial")], [])
        self.assertEqual(len(self.snaps()), 1)

    def test_status_reports_the_local_copy(self):
        code, out = self.run_cli("--status")
        self.assertEqual(code, 1)
        self.assertIn("none yet", out)
        self.local()
        code, out = self.run_cli("--status")
        self.assertIn("matches the project", out)
        write(os.path.join(self.proj, "game.py"), "print('newer')\n")
        code, out = self.run_cli("--status")
        self.assertIn("has changed since the newest zip", out)

    def test_auto_mode_makes_the_local_copy_and_logs_it(self):
        self.run_cli("--local-dir", self.dest)
        self.setup_repo()
        write(os.path.join(self.proj, "game.py"), "print('auto again')\n")
        code, out = self.run_cli("--auto")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        with open(os.path.join(self.proj, "backup.log"), encoding="utf-8") as f:
            self.assertIn("Local copy saved", f.read())

    def test_the_zip_can_be_extracted_and_matches_the_project(self):
        self.local()
        out_dir = os.path.join(self.tmp, "restored")
        with zipfile.ZipFile(os.path.join(self.dest, "snapshots", self.snaps()[0])) as z:
            z.extractall(out_dir)
        for rel in ("game.py", "README.txt", "my_decks/Tymna.txt", "java_bridge/forge_bridge.jar"):
            with open(os.path.join(self.proj, rel), "rb") as a, open(os.path.join(out_dir, "proj", rel), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)


class CiSwitchTests(BackupBase):
    """Round 14: python backup.py --enable-tests-on-github puts ci/tests.yml where GitHub looks, and can never hold up the ordinary backups."""

    WORKFLOW = ".github/workflows/tests.yml"

    def setUp(self):
        super().setUp()
        write(os.path.join(self.proj, "ci", "tests.yml"), "name: Tests\non: push\n")
        self.assertEqual(self.setup_repo()[0], 0)

    def refuse_workflow_files(self):
        """Make the stand-in for GitHub answer the way GitHub does when the sign-in has no 'workflow' permission."""
        hook = os.path.join(self.remote, "hooks", "pre-receive")
        write(hook, "#!/bin/sh\n"
                    "while read old new ref; do\n"
                    "  zero=0000000000000000000000000000000000000000\n"
                    "  if [ \"$old\" = \"$zero\" ]; then files=$(git ls-tree -r --name-only \"$new\"); else files=$(git diff --name-only \"$old\" \"$new\"); fi\n"
                    "  if echo \"$files\" | grep -q '^.github/workflows/'; then\n"
                    "    echo \"remote: refusing to allow a Personal Access Token to create or update workflow \\`.github/workflows/tests.yml\\` without \\`workflow\\` scope\" >&2\n"
                    "    exit 1\n"
                    "  fi\n"
                    "done\n")
        os.chmod(hook, 0o755)

    def test_the_workflow_goes_up_as_its_own_commit_after_a_normal_backup(self):
        write(os.path.join(self.proj, "game.py"), "print('changed')\n")
        code, out = self.run_cli("--enable-tests-on-github")
        self.assertEqual(code, 0, out)
        self.assertIn("Switched on", out)
        self.assertIn(self.WORKFLOW, self.remote_files())
        self.assertEqual(sh(["show", "--name-only", "--format=", "main"], self.remote).splitlines(), [self.WORKFLOW])
        self.assertEqual(sh(["show", "main:game.py"], self.remote), "print('changed')")          # the change before it went up too
        self.assertEqual(sh(["status", "--porcelain"], self.proj), "")

    def test_running_it_again_changes_nothing_and_a_new_version_of_the_file_is_updated(self):
        self.run_cli("--enable-tests-on-github")
        before = sh(["rev-parse", "main"], self.remote)
        code, out = self.run_cli("--enable-tests-on-github")
        self.assertEqual(code, 0)
        self.assertIn("already switched on", out)
        self.assertEqual(sh(["rev-parse", "main"], self.remote), before)
        write(os.path.join(self.proj, "ci", "tests.yml"), "name: Tests\non: [push, pull_request]\n")
        self.assertEqual(self.run_cli("--enable-tests-on-github")[0], 0)
        self.assertIn("pull_request", sh(["show", "main:" + self.WORKFLOW], self.remote))

    def test_a_refused_workflow_is_taken_back_out_and_the_ordinary_backups_keep_working(self):
        self.refuse_workflow_files()
        write(os.path.join(self.proj, "game.py"), "print('before')\n")
        code, out = self.run_cli("--enable-tests-on-github")
        self.assertEqual(code, 2)
        self.assertIn("'workflow' files", out)
        self.assertIn("gh auth refresh -s workflow", out)
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".github")), "the file must not stay behind for `git add -A` to pick up")
        self.assertEqual(sh(["status", "--porcelain"], self.proj), "")
        self.assertEqual(sh(["show", "main:game.py"], self.remote), "print('before')")               # the first, normal backup did go up
        write(os.path.join(self.proj, "game.py"), "print('after')\n")                                # and the next scheduled backup is fine
        code, out = self.run_cli("--auto")
        self.assertEqual(code, 0, out)
        self.assertEqual(sh(["show", "main:game.py"], self.remote), "print('after')")
        self.assertNotIn(self.WORKFLOW, self.remote_files())

    def test_a_refused_update_of_an_existing_workflow_puts_the_old_file_back(self):
        self.assertEqual(self.run_cli("--enable-tests-on-github")[0], 0)
        old = open(os.path.join(self.proj, self.WORKFLOW), "rb").read()
        self.refuse_workflow_files()
        write(os.path.join(self.proj, "ci", "tests.yml"), "name: Tests\non: [push, pull_request]\n")
        code, out = self.run_cli("--enable-tests-on-github")
        self.assertEqual(code, 2)
        self.assertEqual(open(os.path.join(self.proj, self.WORKFLOW), "rb").read(), old)
        self.assertEqual(sh(["status", "--porcelain"], self.proj), "")
        self.assertIn("pull_request", sh(["show", "main:ci/tests.yml"], self.remote))          # the edit itself was part of the normal backup
        self.assertNotIn("pull_request", sh(["show", "main:" + self.WORKFLOW], self.remote))   # the live copy is still the old one

    def test_without_the_source_file_it_says_so(self):
        os.remove(os.path.join(self.proj, "ci", "tests.yml"))
        code, out = self.run_cli("--enable-tests-on-github")
        self.assertEqual(code, 2)
        self.assertIn("ci/tests.yml is missing", out.replace("\\", "/"))

    def test_the_workflow_scope_message_wins_over_the_generic_sign_in_message(self):
        text = "remote: refusing to allow an OAuth App to create or update workflow `.github/workflows/tests.yml` without `workflow` scope\n! [remote rejected] main -> main (refusing to allow an OAuth App ...)\nerror: failed to push some refs; permission denied 403"
        msg = backup.explain_push_failure(text, "https://github.com/x/y.git")
        self.assertIn("workflow", msg)
        self.assertIn("Do NOT create the file on the github.com website", msg)
        self.assertNotIn("Never paste a password", msg)

    def test_the_actions_page_address_is_worked_out_for_github_addresses_only(self):
        self.assertEqual(backup.actions_page("https://github.com/Eliminate7532/commander_sim.git"), "https://github.com/Eliminate7532/commander_sim/actions")
        self.assertEqual(backup.actions_page("git@github.com:Eliminate7532/commander_sim.git"), "https://github.com/Eliminate7532/commander_sim/actions")
        self.assertEqual(backup.actions_page("https://user@github.com/a/b"), "https://github.com/a/b/actions")
        self.assertIsNone(backup.actions_page("/tmp/remote.git"))
        self.assertIsNone(backup.actions_page(None))


class RealWorkflowFileTests(unittest.TestCase):
    """The workflow that ships in ci/tests.yml: it cannot be run here, so check what it promises."""

    def setUp(self):
        with open(os.path.join(HERE, "ci", "tests.yml"), encoding="utf-8") as f:
            self.text = f.read()

    def test_it_runs_the_offline_tests_on_windows_with_python_3_14_and_the_pinned_packages(self):
        for needle in ("runs-on: windows-latest", 'python-version: "3.14"', "pip install -r requirements.txt",
                       "python -m unittest discover -s tests -t .", "SDL_VIDEODRIVER: dummy", "workflow_dispatch"):
            self.assertIn(needle, self.text)

    def test_it_triggers_on_every_branch_not_only_main(self):
        """Round 27a: a round branch needs testing on Windows before it merges, so this no longer says
        'branches: [main]' - a plain push trigger runs on every branch."""
        self.assertNotIn("branches:", self.text)

    def test_it_is_valid_yaml_with_the_usual_shape(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed here")
        doc = yaml.safe_load(self.text)
        self.assertEqual(doc["name"], "Tests")
        steps = doc["jobs"]["offline-tests"]["steps"]
        self.assertTrue(any("checkout" in str(s.get("uses", "")) for s in steps))
        self.assertTrue(any("unittest" in str(s.get("run", "")) for s in steps))

    def test_once_switched_on_the_deployed_copy_matches_the_source(self):
        """Karl ran --enable-tests-on-github, so .github/workflows/tests.yml is now a permanent, intentional part of the repo
        (this used to assert the opposite - that .github did not exist yet - which broke the moment he actually used the
        feature). What still matters is that enable_tests() keeps the deployed copy byte-identical to ci/tests.yml; if a
        future round edits ci/tests.yml without re-running --enable-tests-on-github, this is what should catch the drift."""
        dst = os.path.join(HERE, ".github", "workflows", "tests.yml")
        if not os.path.exists(dst):
            self.skipTest("the automatic-tests workflow has not been switched on in this copy")
        with open(dst, encoding="utf-8") as f:
            self.assertEqual(f.read(), self.text)


class ThinningTests(unittest.TestCase):
    def test_old_zips_are_thinned_to_one_a_day_then_dropped(self):
        d = tempfile.mkdtemp()
        try:
            now = datetime.datetime(2026, 9, 20, 12, 0, 0)
            made = {}

            def add(days_ago, hour, tag):
                when = (now - datetime.timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0)
                name = f"commander_sim_{when:%Y-%m-%d_%H-%M-%S}.zip"
                write(os.path.join(d, name), "z")
                made[tag] = name

            add(0, 9, "today_am")
            add(0, 11, "today_pm")
            add(2, 8, "d2_am")                     # within the last 3 days: all kept
            add(2, 20, "d2_pm")
            add(10, 8, "d10_am")                   # older: only the newest of its day stays
            add(10, 20, "d10_pm")
            add(10, 22, "d10_late")
            add(89, 9, "d89")                      # still inside 90 days
            add(120, 9, "d120")                    # too old: removed
            write(os.path.join(d, "notes.txt"), "mine")
            write(os.path.join(d, "other.zip"), "not ours")
            removed = backup.prune_snapshots(d, now)
            left = set(os.listdir(d))
            for tag in ("today_am", "today_pm", "d2_am", "d2_pm", "d10_late", "d89"):
                self.assertIn(made[tag], left, tag)
            for tag in ("d10_am", "d10_pm", "d120"):
                self.assertNotIn(made[tag], left, tag)
            self.assertIn("notes.txt", left)
            self.assertIn("other.zip", left)
            self.assertEqual(removed, 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_newest_zip_is_never_removed_even_when_very_old(self):
        d = tempfile.mkdtemp()
        try:
            write(os.path.join(d, "commander_sim_2020-01-01_00-00-00.zip"), "z")
            self.assertEqual(backup.prune_snapshots(d, datetime.datetime(2026, 9, 20)), 0)
            self.assertEqual(len(os.listdir(d)), 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
