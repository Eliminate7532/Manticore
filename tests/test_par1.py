# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PAR1 (Karl, 11 Oct 2026: "I really need to be able to run simultaneous sessions and have them aware of each other").

Every session works at its own desk (a git worktree in .worktrees/<round>, made by tools/desk.py) and a board (.sessions/<round>.md)
says who is working where. These tests use real temporary git repositories (no network):
  - desk.py: a desk on its own branch with a RELATIVE link (so Karl's Windows git and the device bridge's Linux git both read it -
    a moved folder stands in for "the same folder at another path"), the board, overlaps, edits left in the main folder, names,
    notes, done, remove, merging, running from inside a desk;
  - the main folder never takes a desk in (git add -A), and the backup zip leaves desks out;
  - backup.py pushes every desk's branch, and one that fails doesn't fail the backup;
  - version.py reads a desk's commit and tag; paths.is_portable() counts a desk as a working copy;
  - the public export leaves desks and the board out; the working agreement and CLAUDE.md say how it works.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import backup  # noqa: E402
import desk  # noqa: E402
import export_public as ep  # noqa: E402
import paths  # noqa: E402
import version  # noqa: E402

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def sh(args, cwd, check=True):
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, **GIT_ENV))
    if check and p.returncode:
        raise AssertionError(p.stderr + p.stdout)
    return p.stdout.strip()


def write(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(shutil.which("git"), "needs git")
class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="par1_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = {k: os.environ.get(k) for k in GIT_ENV}
        os.environ.update(GIT_ENV)
        self.addCleanup(self._restore_env)
        self.main = os.path.join(self.tmp, "commander_sim")
        os.makedirs(self.main)
        shutil.copy(os.path.join(HERE, ".gitignore"), os.path.join(self.main, ".gitignore"))
        for rel in ("forge_table.py", "forge_menu.py", "forge_dialogs.py", "docs/WORKING_AGREEMENT.md"):
            write(os.path.join(self.main, rel), f"# {rel}\n")
        sh(["init", "-q", "-b", "main"], self.main)
        sh(["config", "user.email", "t@example.com"], self.main)
        sh(["config", "user.name", "t"], self.main)
        sh(["add", "-A"], self.main)
        sh(["commit", "-q", "-m", "init"], self.main)

    def _restore_env(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def desk(self, *argv, start=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = desk.main(list(argv), start=start or self.main)
        return code, out.getvalue() + err.getvalue()

    def new(self, name, files="", **kw):
        argv = ["new", name, "--who", kw.get("who", f"Claude {name}")]
        if files:
            argv += ["--files", files]
        if kw.get("elsewhere"):
            argv += ["--elsewhere", kw["elsewhere"]]
        code, out = self.desk(*argv)
        self.assertEqual(code, 0, out)
        return os.path.join(self.main, ".worktrees", name)

    def commit_in(self, folder, rel, text, msg="edit"):
        write(os.path.join(folder, rel), text)
        sh(["add", "-A"], folder)
        sh(["commit", "-q", "-m", msg], folder)


class DeskTests(RepoCase):
    def test_a_desk_is_its_own_branch_with_a_relative_link(self):
        d = self.new("cd2", files="forge_table.py")
        self.assertEqual(read(os.path.join(d, ".git")).strip(), "gitdir: ../../.git/worktrees/cd2")
        self.assertEqual(sh(["branch", "--show-current"], d), "round-cd2")
        self.assertEqual(sh(["config", "gc.worktreePruneExpire"], self.main), "never")
        board = read(os.path.join(self.main, ".sessions", "cd2.md"))
        for want in ("who: Claude cd2", "branch: round-cd2", "desk: .worktrees/cd2", "status: working", "files: forge_table.py"):
            self.assertIn(want, board)
        self.commit_in(d, "forge_table.py", "x = 2\n")
        self.assertEqual(sh(["rev-parse", "main"], self.main), sh(["rev-parse", "HEAD~1"], d))   # main didn't move

    def test_the_desk_still_works_when_the_folder_is_reached_by_another_path(self):
        # Karl's Windows git and the device bridge's Linux git see this folder at two different paths: a moved folder stands in
        d = self.new("cd2")
        self.commit_in(d, "forge_table.py", "x = 2\n")
        moved = os.path.join(self.tmp, "elsewhere", "commander_sim")
        os.makedirs(os.path.dirname(moved))
        shutil.move(self.main, moved)
        d2 = os.path.join(moved, ".worktrees", "cd2")
        self.assertEqual(sh(["status", "--porcelain"], d2), "")
        self.commit_in(d2, "forge_menu.py", "y = 1\n", "second")
        self.assertEqual(sh(["log", "-1", "--format=%s", "round-cd2"], moved), "second")
        code, out = self.desk("list", start=moved)
        self.assertEqual(code, 0, out)
        self.assertIn("changes 2 file(s): forge_menu.py, forge_table.py", out)

    def test_the_main_folder_never_takes_a_desk_or_the_board_in(self):
        d = self.new("cd2")
        write(os.path.join(d, "new_file.py"), "z = 1\n")
        sh(["add", "-A"], self.main)
        staged = sh(["diff", "--cached", "--name-only"], self.main).splitlines()
        self.assertFalse([s for s in staged if s.startswith((".worktrees", ".sessions"))], staged)

    def test_list_shows_what_each_desk_changes_and_where_two_meet(self):
        a = self.new("cd2", files="forge_menu.py")
        b = self.new("laststate", files="forge_dialogs.py")
        self.commit_in(a, "forge_table.py", "a = 1\n")
        write(os.path.join(b, "forge_table.py"), "b = 1\n")                   # not committed yet: still counts
        write(os.path.join(b, "last_good.py"), "new\n")                       # a new file counts too
        code, out = self.desk("list")
        self.assertEqual(code, 0, out)
        self.assertIn("2 session(s) working", out)
        self.assertIn("changes 1 file(s): forge_table.py", out)
        self.assertIn("changes 2 file(s): forge_table.py, last_good.py", out)
        self.assertIn("OVERLAPS", out)
        self.assertIn("forge_table.py: cd2, laststate", out)
        self.assertNotIn("forge_menu.py:", out)                              # only one session expects it
        self.assertEqual(self.desk("check")[0], 1)

    def test_no_overlap_no_main_edits_check_passes(self):
        a = self.new("cd2")
        self.commit_in(a, "forge_menu.py", "m = 1\n")
        self.new("laststate", files="forge_dialogs.py")
        code, out = self.desk("check")
        self.assertEqual(code, 0, out)
        self.assertNotIn("OVERLAPS", out)
        self.assertIn("no program edits waiting", out)

    def test_edits_in_the_main_folder_are_reported_but_patch_folders_are_not(self):
        self.new("cd2", files="forge_table.py")
        write(os.path.join(self.main, "docs", "incoming", "par1", "par1.patch"), "diff\n")
        code, out = self.desk("check")
        self.assertEqual(code, 0, out)
        write(os.path.join(self.main, "forge_table.py"), "edited in place\n")
        code, out = self.desk("check")
        self.assertEqual(code, 1, out)
        self.assertIn("1 file(s) changed and not committed: forge_table.py", out)
        self.assertIn("forge_table.py: cd2, main folder", out)

    def test_a_session_building_elsewhere_is_on_the_board_without_a_desk(self):
        a = self.new("laststate")
        self.commit_in(a, "forge_table.py", "a = 1\n")
        self.new("cd2", files="forge_table.py,forge_menu.py", elsewhere="cloud sandbox", who="Claude (Opus, cloud)")
        self.assertFalse(os.path.exists(os.path.join(self.main, ".worktrees", "cd2")))
        code, out = self.desk("list")
        self.assertIn("elsewhere: cloud sandbox", out)
        self.assertIn("forge_table.py: cd2, laststate", out)

    def test_bad_names_and_names_already_working_are_refused(self):
        for bad in ("nul", "con.txt", "COM1", "Has Space", "../up", "a" * 41, "merging", ""):
            code, out = self.desk("new", bad, "--who", "x")
            self.assertEqual(code, 2, (bad, out))
        self.assertFalse(os.path.exists(os.path.join(self.main, ".worktrees", "nul")))
        self.new("cd2")
        code, out = self.desk("new", "cd2", "--who", "someone else")
        self.assertEqual(code, 2)
        self.assertIn("already on the board", out)
        d = self.new("round-x9")
        self.assertEqual(sh(["branch", "--show-current"], d), "round-x9")      # a round- name is used as it is

    def test_notes_and_done(self):
        a = self.new("cd2")
        self.commit_in(a, "forge_table.py", "a = 1\n")
        self.assertEqual(self.desk("note", "cd2", "merging notes by hand", "--files", "forge_table.py,README.txt")[0], 0)
        entry = read(os.path.join(self.main, ".sessions", "cd2.md"))
        self.assertIn("merging notes by hand", entry)
        self.assertIn("files: README.txt, forge_table.py", entry)
        b = self.new("laststate")
        self.commit_in(b, "forge_table.py", "b = 1\n")
        self.assertIn("forge_table.py: cd2, laststate", self.desk("list")[1])
        self.assertEqual(self.desk("done", "cd2")[0], 0)
        code, out = self.desk("list")
        self.assertIn("1 session(s) working, 1 finished", out)
        self.assertNotIn("OVERLAPS", out)                                       # a finished desk no longer counts
        self.assertEqual(self.desk("note", "nobody", "x")[0], 2)

    def test_remove_keeps_uncommitted_work_and_the_branch(self):
        d = self.new("cd2")
        code, out = self.desk("remove", "cd2")
        self.assertEqual(code, 2)
        self.assertIn("isn't marked done", out)
        self.desk("done", "cd2")
        write(os.path.join(d, "forge_table.py"), "not committed\n")
        code, out = self.desk("remove", "cd2")
        self.assertEqual(code, 2, out)
        self.assertIn("not committed", out)
        self.assertTrue(os.path.isdir(d))
        self.commit_in(d, "forge_table.py", "committed\n")
        tip = sh(["rev-parse", "HEAD"], d)
        code, out = self.desk("remove", "cd2")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(d))
        self.assertFalse(os.path.exists(os.path.join(self.main, ".git", "worktrees", "cd2")))
        self.assertEqual(sh(["rev-parse", "round-cd2"], self.main), tip)
        listed = sh(["worktree", "list"], self.main)
        self.assertNotIn(".worktrees", listed)                                  # not "cd2": a commit id can hold those letters
        self.assertNotIn("[round-cd2]", listed)
        self.assertIn("status: removed", read(os.path.join(self.main, ".sessions", "cd2.md")))

    def test_one_merger_at_a_time(self):
        self.assertEqual(self.desk("merging", "start", "Karl", "cd2 then laststate")[0], 0)
        self.assertIn("MERGING NOW: Karl - cd2 then laststate", self.desk("list")[1])
        code, out = self.desk("merging", "start", "Claude", "par1")
        self.assertEqual(code, 2)
        self.assertIn("Karl is merging already", out)
        self.assertEqual(self.desk("merging", "stop")[0], 0)
        self.assertNotIn("MERGING NOW", self.desk("list")[1])
        self.assertEqual(self.desk("merging", "start", "Claude", "par1")[0], 0)

    def test_the_old_session_line_is_shown(self):
        write(os.path.join(self.main, "docs", "SESSION.md"), "2026-10-11 12:50 - Claude (Sonnet) - round-laststate - working\n")
        code, out = self.desk("list")
        self.assertIn("Old docs/SESSION.md line", out)
        self.assertIn("round-laststate - working", out)
        self.assertIn("no program edits waiting", out)                         # SESSION.md itself isn't a program edit

    def test_from_inside_a_desk_it_is_the_same_board(self):
        d = self.new("cd2")
        self.new("laststate")
        self.assertEqual(os.path.realpath(desk.main_folder(d)), os.path.realpath(self.main))
        code, out = self.desk("list", start=d)
        self.assertEqual(code, 0, out)
        self.assertIn("2 session(s) working", out)                          # both desks: the main folder's board

    def test_a_hidden_link_file_is_rewritten_too(self):
        # Git for Windows makes a new .git file hidden, and Windows won't open a hidden file with "w" (create-always): Karl's PC,
        # 11 Oct, 16 errors. desk.py now rewrites the link in place ("r+"). Here: the open() a "w" would use must never be called.
        d = self.new("cd2")
        with open(os.path.join(d, ".git"), "r+", encoding="utf-8") as f:      # "r+", not "w": on Windows this file is hidden too
            f.write("gitdir: " + os.path.join(self.main, ".git", "worktrees", "cd2").replace(os.sep, "/") + "\n")
            f.truncate()
        self.assertTrue(os.path.isabs(read(os.path.join(d, ".git"))[len("gitdir: "):].strip()))
        real_open = open

        def windows_like_open(file, mode="r", *a, **kw):
            if os.path.basename(str(file)) == ".git" and "w" in mode:
                raise PermissionError(13, "Permission denied (hidden file, create-always)", str(file))
            return real_open(file, mode, *a, **kw)
        with mock.patch("builtins.open", side_effect=windows_like_open):
            self.assertTrue(desk.make_link_relative(d))
        self.assertEqual(read(os.path.join(d, ".git")).strip(), "gitdir: ../../.git/worktrees/cd2")


class VersionAndPathsTests(RepoCase):
    def test_version_reads_a_desks_commit_and_tag(self):
        d = self.new("cd2")
        self.commit_in(d, "forge_table.py", "a = 1\n")
        tip = sh(["rev-parse", "HEAD"], d)
        self.assertEqual(version._head_sha(d), tip)
        self.assertEqual(version._head_sha(self.main), sh(["rev-parse", "HEAD"], self.main))
        self.assertIsNone(version.tag(d))
        sh(["tag", "-a", "rcd2", "-m", "Round CD2", "round-cd2"], self.main)
        self.assertEqual(version.tag(d), "rcd2")
        sh(["pack-refs", "--all"], self.main)                                   # packed refs, as after git gc
        self.assertEqual(version._head_sha(d), tip)
        self.assertEqual(version.tag(d), "rcd2")

    def test_a_desk_is_a_working_copy_not_an_installed_one(self):
        d = self.new("cd2")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MANTICORE_PORTABLE", None)
            with mock.patch.object(paths, "program_dir", return_value=d):
                self.assertTrue(paths.is_portable())
            with mock.patch.object(paths, "program_dir", return_value=self.main):
                self.assertTrue(paths.is_portable())
            empty = os.path.join(self.tmp, "installed")
            os.makedirs(empty)
            with mock.patch.object(paths, "program_dir", return_value=empty):
                self.assertFalse(paths.is_portable())


class BackupTests(RepoCase):
    def setUp(self):
        super().setUp()
        self.remote = os.path.join(self.tmp, "remote.git")
        sh(["init", "-q", "--bare", self.remote], self.tmp)
        sh(["remote", "add", "origin", self.remote], self.main)
        sh(["push", "-q", "-u", "origin", "main"], self.main)

    def quiet(self, fn, *a, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = fn(*a, **kw)
        return result, buf.getvalue()

    def test_the_backup_pushes_every_desks_branch_and_commits_nothing_from_them(self):
        a = self.new("cd2")
        b = self.new("laststate")
        self.commit_in(a, "forge_table.py", "a = 1\n")
        self.commit_in(b, "forge_menu.py", "b = 1\n")
        write(os.path.join(b, "uncommitted.py"), "not yet\n")
        write(os.path.join(self.main, "forge_dialogs.py"), "main edit\n")
        code, out = self.quiet(backup.backup, self.main, auto=True)
        self.assertEqual(code, 0, out)
        self.assertIn("Desk branch round-cd2 pushed too.", out)
        self.assertEqual(sh(["rev-parse", "round-cd2"], self.remote), sh(["rev-parse", "HEAD"], a))
        self.assertEqual(sh(["rev-parse", "round-laststate"], self.remote), sh(["rev-parse", "HEAD"], b))
        files = sh(["ls-tree", "-r", "--name-only", "main"], self.remote).splitlines()
        self.assertIn("forge_dialogs.py", files)
        self.assertFalse([f for f in files if f.startswith((".worktrees", ".sessions"))], files)
        self.assertNotIn("uncommitted.py", sh(["ls-tree", "-r", "--name-only", "round-laststate"], self.remote))

    def test_a_desk_branch_that_cannot_be_pushed_does_not_fail_the_backup(self):
        self.commit_in(self.new("cd2"), "forge_table.py", "a = 1\n")
        real = backup.git

        def fake(args, folder, check=True, timeout=120):
            if args[:2] == ["push", "-u"] and "refs/heads/round-cd2" in args[-1]:
                return 1, "! [rejected] round-cd2 (fetch first)"
            return real(args, folder, check=check, timeout=timeout)
        with mock.patch.object(backup, "git", side_effect=fake):
            code, out = self.quiet(backup.backup, self.main, auto=True)
        self.assertEqual(code, 0, out)
        self.assertIn("Desk branch round-cd2 could NOT be pushed", out)
        self.assertIn("rejected", out)

    def test_desk_branches_by_path_from_either_machine(self):
        porcelain = "\n".join([
            "worktree C:/Users/Some User/Documents/commander_sim", "HEAD 1111", "branch refs/heads/main", "",
            "worktree C:/Users/Some User/Documents/commander_sim/.worktrees/cd2", "HEAD 2222", "branch refs/heads/round-cd2", "",
            "worktree /sessions/x/mnt/commander_sim/.worktrees/laststate", "HEAD 3333", "branch refs/heads/round-laststate", "",
            "worktree C:\\Users\\Some User\\Documents\\commander_sim\\.worktrees\\think1", "HEAD 4444", "branch refs/heads/round-think1", "",
            "worktree /home/k/other_checkout", "HEAD 5555", "branch refs/heads/experiment", "",
            "worktree C:/x/commander_sim/.worktrees/detached", "HEAD 6666", "detached", ""])
        with mock.patch.object(backup, "git", return_value=(0, porcelain)):
            self.assertEqual(backup.desk_branches(self.main), ["round-cd2", "round-laststate", "round-think1"])

    def test_the_zip_leaves_desks_out_and_keeps_the_board(self):
        self.new("cd2")
        files, _left = backup.snapshot_files(self.main, os.path.join(self.tmp, "zips"))
        rels = [r for r, _f in files]
        self.assertFalse([r for r in rels if r.startswith(".worktrees")], rels)
        self.assertIn(".sessions/cd2.md", rels)
        self.assertIn(".worktrees", backup.TOP_LEVEL_SKIP)


class ExportAndDocsTests(unittest.TestCase):
    def test_the_public_export_leaves_desks_and_the_board_out(self):
        src = tempfile.mkdtemp(prefix="par1_export_")
        self.addCleanup(shutil.rmtree, src, True)
        for rel in ("forge_table.py", ".worktrees/cd2/forge_table.py", ".worktrees/cd2/CLAUDE.md", ".sessions/cd2.md", "README.txt"):
            write(os.path.join(src, rel))
        files = sorted(ep.iter_source_files(src))
        self.assertEqual(files, ["README.txt", "forge_table.py"])

    def test_git_ignores_desks_and_the_board(self):
        text = read(os.path.join(HERE, ".gitignore")).splitlines()
        self.assertIn(".worktrees/", text)
        self.assertIn(".sessions/", text)

    def test_the_working_agreement_says_how(self):
        text = read(os.path.join(HERE, "docs", "WORKING_AGREEMENT.md"))
        for want in ("desk.py list", "desk.py new", "--elsewhere", "desk.py done", "docs/rounds/", "merging start",
                     "Never `git worktree prune` or `git worktree repair`", "Never `git push`", "Never run `backup.py` yourself",
                     "`/dev/null`", "own engine",
                     "ask for delete permission before your first git write", "don't delete it yourself"):
            self.assertIn(want, text)
        self.assertNotIn("**One session at a time.**", text)
        self.assertLessEqual(len(text.splitlines()), 70)
        self.assertIn("desk.py list", read(os.path.join(HERE, "CLAUDE.md"))[:1500])
        self.assertTrue(os.path.isfile(os.path.join(HERE, "docs", "rounds", "README.md")))


if __name__ == "__main__":
    unittest.main()
