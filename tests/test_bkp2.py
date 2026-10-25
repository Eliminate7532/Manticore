# SPDX-License-Identifier: GPL-3.0-or-later
"""Round BKP2: the backup also pushes the local round-* branches GitHub doesn't have yet (18 Oct: nine branches lived only on
Karl's PC until they were merged). A local bare repository stands in for GitHub (no network)."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def sh(args, cwd):
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, **GIT_ENV))
    if p.returncode:
        raise AssertionError(p.stderr + p.stdout)
    return p.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class RoundBranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bkp2_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = {k: os.environ.get(k) for k in GIT_ENV}
        os.environ.update(GIT_ENV)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        self.remote = os.path.join(self.tmp, "remote.git")
        os.makedirs(self.remote)
        sh(["init", "-q", "--bare"], self.remote)
        self.main = os.path.join(self.tmp, "commander_sim")
        os.makedirs(self.main)
        shutil.copy(os.path.join(HERE, ".gitignore"), os.path.join(self.main, ".gitignore"))
        write(os.path.join(self.main, "forge_table.py"), "# table\n")
        sh(["init", "-q", "-b", "main"], self.main)
        sh(["config", "user.email", "t@example.com"], self.main)
        sh(["config", "user.name", "t"], self.main)
        sh(["add", "-A"], self.main)
        sh(["commit", "-q", "-m", "init"], self.main)
        sh(["remote", "add", "origin", self.remote], self.main)
        sh(["push", "-q", "-u", "origin", "main"], self.main)

    # ---- helpers -------------------------------------------------------------------------------------------------------
    def branch(self, name, rel, text, base="main"):
        """A branch made the way a delivered round arrives: its own commit on top of `base`, main left checked out."""
        sh(["checkout", "-q", "-b", name, base], self.main)
        write(os.path.join(self.main, rel), text)
        sh(["add", rel], self.main)
        sh(["commit", "-q", "-m", name], self.main)
        sh(["checkout", "-q", "main"], self.main)
        return sh(["rev-parse", name], self.main)

    def on_github(self, name):
        p = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{name}"], cwd=self.remote, capture_output=True,
                           text=True, env=dict(os.environ, **GIT_ENV))
        return p.stdout.strip() or None

    def quiet(self, fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = fn(*a, **kw)
        return result, buf.getvalue()

    def backup_now(self):
        code, out = self.quiet(backup.backup, self.main, auto=True)
        self.assertEqual(code, 0, out)
        return out

    def counting_git(self):
        """backup.git, recording every push it is asked to run."""
        real, pushes = backup.git, []

        def fake(args, folder, check=True, timeout=120):
            if args and args[0] == "push":
                pushes.append(list(args))
            return real(args, folder, check=check, timeout=timeout)
        return fake, pushes

    # ---- the tests -----------------------------------------------------------------------------------------------------
    def test_an_unmerged_round_branch_reaches_github(self):
        sha = self.branch("round-ai2", "Threats.java", "class Threats {}\n")
        out = self.backup_now()
        self.assertEqual(self.on_github("round-ai2"), sha)
        self.assertIn("Round branch pushed too: round-ai2.", out)

    def test_merged_rounds_and_other_branch_names_stay_on_this_pc(self):
        self.branch("round-old", "old.py", "x = 1\n")
        sh(["merge", "-q", "--no-ff", "-m", "merge round-old", "round-old"], self.main)
        self.branch("experiment", "exp.py", "y = 1\n")
        self.branch("rounding-error", "r.py", "z = 1\n")          # not "round-*": the dash is part of the pattern
        new = self.branch("round-new", "new.py", "w = 1\n")
        fake, pushes = self.counting_git()
        with mock.patch.object(backup, "git", side_effect=fake):
            self.backup_now()
        self.assertIsNone(self.on_github("round-old"))
        self.assertIsNone(self.on_github("experiment"))
        self.assertIsNone(self.on_github("rounding-error"))
        self.assertEqual(self.on_github("round-new"), new)
        round_pushes = [p for p in pushes if "--porcelain" in p]
        self.assertEqual(len(round_pushes), 1, pushes)
        self.assertEqual([a for a in round_pushes[0] if a.startswith("refs/")], ["refs/heads/round-new:refs/heads/round-new"])

    def test_a_branch_github_already_has_is_not_pushed_again_and_a_new_commit_is(self):
        self.branch("round-sb2", "soak_bot.py", "a = 1\n")
        self.backup_now()
        fake, pushes = self.counting_git()
        with mock.patch.object(backup, "git", side_effect=fake):
            out = self.backup_now()
        self.assertEqual([p for p in pushes if "--porcelain" in p], [], pushes)        # no network for it at all
        self.assertNotIn("Round branch", out)
        sh(["checkout", "-q", "round-sb2"], self.main)
        write(os.path.join(self.main, "soak_bot.py"), "a = 2\n")
        sh(["commit", "-q", "-am", "more"], self.main)
        newer = sh(["rev-parse", "HEAD"], self.main)
        sh(["checkout", "-q", "main"], self.main)
        out = self.backup_now()
        self.assertEqual(self.on_github("round-sb2"), newer)
        self.assertIn("round-sb2", out)

    def test_a_branch_github_has_another_version_of_is_refused_not_forced_and_the_rest_still_go(self):
        first = self.branch("round-bkp2", "backup.py", "v = 1\n")
        sh(["push", "-q", "origin", "round-bkp2"], self.main)
        sh(["branch", "-q", "-f", "round-bkp2", "main"], self.main)            # rewritten here: no longer contains GitHub's tip
        sh(["checkout", "-q", "round-bkp2"], self.main)
        write(os.path.join(self.main, "backup.py"), "v = 2\n")
        sh(["add", "backup.py"], self.main)
        sh(["commit", "-q", "-m", "rewritten"], self.main)
        sh(["checkout", "-q", "main"], self.main)
        other = self.branch("round-rm2", "ROADMAP.txt", "r\n")
        out = self.backup_now()
        self.assertEqual(self.on_github("round-bkp2"), first)                 # GitHub's version is untouched
        self.assertEqual(self.on_github("round-rm2"), other)
        self.assertIn("could NOT be pushed", out)
        self.assertIn("round-bkp2", out.split("could NOT be pushed")[1])
        self.assertIn("rejected", out)

    def test_the_desks_branches_are_left_to_push_desk_branches(self):
        self.branch("round-desk", "d.py", "d = 1\n")
        mine = self.branch("round-mine", "m.py", "m = 1\n")
        fake, pushes = self.counting_git()
        with mock.patch.object(backup, "push_desk_branches", return_value={"round-desk": True}), \
                mock.patch.object(backup, "git", side_effect=fake):
            self.backup_now()
        args = [a for p in pushes if "--porcelain" in p for a in p]
        self.assertNotIn("refs/heads/round-desk:refs/heads/round-desk", args)
        self.assertIsNone(self.on_github("round-desk"))
        self.assertEqual(self.on_github("round-mine"), mine)

    def test_no_network_or_a_slow_git_never_fails_the_backup(self):
        self.branch("round-x", "x.py", "x = 1\n")
        real = backup.git

        def offline(args, folder, check=True, timeout=120):
            if args[:2] == ["push", "--porcelain"]:
                return 128, "fatal: unable to access 'https://github.com/x/y.git/': Could not resolve host: github.com"
            return real(args, folder, check=check, timeout=timeout)

        def slow(args, folder, check=True, timeout=120):
            if args[:2] == ["push", "--porcelain"]:
                raise backup.BackupError("git took too long and was stopped.")
            return real(args, folder, check=check, timeout=timeout)
        for fake in (offline, slow):
            with mock.patch.object(backup, "git", side_effect=fake):
                out = self.backup_now()                                       # still 0: asserted in backup_now
            self.assertIn("could NOT be pushed (the folder's own backup is fine)", out)
            self.assertIn("round-x", out)
        self.assertIsNone(self.on_github("round-x"))

    def test_nothing_unpushed_runs_no_push_at_all(self):
        fake, pushes = self.counting_git()
        with mock.patch.object(backup, "git", side_effect=fake):
            result, out = self.quiet(backup.push_round_branches, self.main)
        self.assertEqual(result, {})
        self.assertEqual(pushes, [])
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
