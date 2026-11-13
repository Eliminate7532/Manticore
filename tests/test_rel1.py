# SPDX-License-Identifier: GPL-3.0-or-later
"""Round REL1: tools/release.py - one command releases Manticore to testers. No network, no Windows: a fake runner records each
command and returns scripted results; the feed fetch and the question are fakes too."""
import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import release  # noqa: E402

SHA = "a" * 40
VERSION_SRC = 'VERSION = "0.28.53.16"\n'


class Fake:
    """Scripted commands: rules is a list of (predicate on the joined command, (code, output) or callable)."""

    def __init__(self, env):
        self.env = env
        self.calls = []
        self.rules = []

    def on(self, pred, result):
        self.rules.insert(0, (pred, result))

    def __call__(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        line = " ".join(str(c) for c in cmd)
        for pred, result in self.rules:
            if pred(line):
                return result(cmd) if callable(result) else result
        return 0, ""


class Env:
    def __init__(self, minute=20, published="0.28.53.15", windows=True, report_intake=False):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, "commander_sim")
        self.pub = os.path.join(self.tmp, "manticore_public")
        for d in (os.path.join(self.base, ".git"), os.path.join(self.base, "tools"), os.path.join(self.pub, ".git"),
                  os.path.join(self.base, "installer_out")):
            os.makedirs(d)
        if report_intake:
            open(os.path.join(self.base, "tools", "report_intake.py"), "w").close()
        self.fake = Fake(self)
        self.answers = []
        self.out = []
        self.published = published
        self.feed_after = None      # what latest.json says after publishing
        self.minute = minute
        f = self.fake
        f.on(lambda l: "rev-parse --verify" in l and "refs/heads/" in l, (0, SHA + "\n"))
        f.on(lambda l: "git show" in l and "version.py" in l, (0, VERSION_SRC))
        f.on(lambda l: "remote get-url origin" in l, (0, "https://github.com/Eliminate7532/Manticore.git\n"))
        f.on(lambda l: "git status --porcelain" in l and self.pub not in l, (0, ""))
        f.on(lambda l: l.endswith("rev-parse HEAD"), (0, SHA + "\n"))
        f.on(lambda l: "build_installer.py --test" in l, self._build)
        f.on(lambda l: "make_update_feed.py" in l, self._feed)
        f.on(lambda l: "export_public.py --check" in l, (0, "950 files would be exported.\n"))
        f.on(lambda l: l.startswith("robocopy"), (1, ""))
        f.on(lambda l: l.startswith("gh release view"), self._view)
        f.on(lambda l: "git -C " + self.pub + " status --porcelain" in l, (0, "M x.py\n"))

    def published_now(self):
        return any(c[:3] in (["gh", "release", "create"], ["gh", "release", "upload"]) for c in self.fake.calls)

    def _view(self, cmd):
        return (0, "v0.28.53.16") if self.published_now() else (1, "release not found")

    def _build(self, cmd):
        inst = os.path.join(self.base, "installer_out", "Manticore-0.28.53.16-setup.exe")
        with open(inst, "wb") as fh:
            fh.write(b"installer")
        self.sha256 = hashlib.sha256(b"installer").hexdigest()
        with open(inst + ".sha256", "w") as fh:
            fh.write(self.sha256 + "  Manticore-0.28.53.16-setup.exe\n")
        return 0, "built"

    def _feed(self, cmd):
        with open(os.path.join(self.base, "installer_out", "latest.json"), "w") as fh:
            json.dump({"version": "0.28.53.16", "sha256": getattr(self, "sha256", "")}, fh)
        return 0, "Wrote latest.json"

    def fetch(self, url):
        self.fake.calls.append(["FETCH", url])      # recorded, so the step-order tests can place the version check
        if self.published_now():
            return 200, self.feed_after or {"version": "0.28.53.16", "sha256": getattr(self, "sha256", "")}
        if self.published is None:
            return 404, None
        return 200, {"version": self.published}

    def ask(self, prompt):
        self.out.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def args(self, *extra):
        return release.parse_args(["--branch", "release-0.28.53.16", "--notes", "Fixes.", "--public-dir", self.pub]
                                  + list(extra))

    def make(self, *extra, windows=True):
        clock = lambda: datetime.datetime(2026, 11, 4, 20, self.minute, 0)
        return release.Release(self.args(*extra), run=self.fake, fetch=self.fetch, ask=self.ask, now=clock,
                               sleep=self._sleep, base=self.base, is_windows=windows, say=self.out.append,
                               python="python", getcwd=lambda: self.base)

    def _sleep(self, s):
        self.minute = (self.minute + 1) % 60

    def joined(self):
        return [" ".join(c) for c in self.fake.calls]

    def has(self, text):
        return any(text in l for l in self.joined())

    def log_text(self):
        folder = os.path.join(self.base, "release_logs")
        return "\n".join(open(os.path.join(folder, n), encoding="utf-8").read() for n in os.listdir(folder)
                         if n.endswith(".txt"))

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def run_it(self, *extra, answers=("y",), windows=True):
        self.e.answers = list(answers)
        return self.e.make(*extra, windows=windows).main()

    def index(self, text):
        for i, l in enumerate(self.e.joined()):
            if text in l:
                return i
        self.fail(f"never ran: {text}\n" + "\n".join(self.e.joined()))

    def test_the_steps_run_in_order_and_publish(self):
        self.assertEqual(self.run_it(), 0, "\n".join(self.e.out))
        order = ["gh auth status", "git fetch origin", "setup_forge.py", "quick_tests.py", "FETCH ", "git checkout main",
                 "git merge --ff-only " + SHA, "git push origin main", "build_installer.py --test", "make_update_feed.py",
                 "export_public.py --check", "robocopy", "commit -m Manticore 0.28.53.16", "gh release create v0.28.53.16"]
        idx = [self.index(t) for t in order]
        self.assertEqual(idx, sorted(idx))
        create = next(c for c in self.e.fake.calls if c[:3] == ["gh", "release", "create"])
        self.assertIn("--latest", create)
        self.assertNotIn("--prerelease", create)
        self.assertNotIn("--draft", create)
        self.assertEqual(create[create.index("--repo") + 1], "Eliminate7532/Manticore")

    def test_a_dirty_tree_stops_at_checks(self):
        self.e.fake.on(lambda l: "git status --porcelain" in l and self.e.pub not in l, (0, " M forge_table.py\n"))
        self.assertEqual(self.run_it(), 1)
        self.assertFalse(self.e.has("git fetch"))
        self.assertTrue(any("isn't clean" in o for o in self.e.out))

    def test_a_failure_stops_and_from_resumes(self):
        self.e.fake.on(lambda l: "build_installer.py" in l, (1, "iscc not found"))
        self.assertEqual(self.run_it(), 1)
        self.assertFalse(self.e.has("make_update_feed"))
        self.assertTrue(any("--from build" in o for o in self.e.out))
        # carry on: the build works now; test and merge are not run again
        self.e.fake.calls.clear()
        self.e.fake.on(lambda l: "build_installer.py --test" in l, self.e._build)
        self.assertEqual(self.run_it("--from", "build"), 0, "\n".join(self.e.out))
        self.assertFalse(self.e.has("quick_tests.py"))
        self.assertFalse(self.e.has("git merge"))
        self.assertTrue(self.e.has("gh auth status"))          # checks always run
        self.assertTrue(self.e.has("gh release create"))

    def test_not_a_fast_forward_is_refused(self):
        self.e.fake.on(lambda l: "merge-base --is-ancestor" in l, (1, ""))
        self.assertEqual(self.run_it(), 1)
        self.assertFalse(self.e.has("git merge --ff-only"))
        self.assertFalse(self.e.has("git push origin main"))

    def test_a_version_not_higher_than_published_is_refused(self):
        for pub in ("0.28.53.16", "0.28.53.17", "0.29"):
            self.e.close(); self.e = Env(published=pub)
            self.assertEqual(self.run_it(), 1, pub)
            self.assertFalse(self.e.has("build_installer"), pub)
            self.assertTrue(any("not higher" in o for o in self.e.out), pub)

    def test_a_version_not_higher_stops_before_main_is_touched(self):
        # Round REL1B (Karl, 4 Nov 2026: "Move it before the merge"): the refusal leaves main as it was.
        self.e.close(); self.e = Env(published="0.28.53.16")
        self.assertEqual(self.run_it(), 1)
        self.assertTrue(any("STOPPED at version" in o for o in self.e.out), "\n".join(self.e.out))
        for bad in ("git checkout main", "git pull", "git merge", "git push"):
            self.assertFalse(self.e.has(bad), bad)
        self.index("FETCH ")                              # the published feed was read
        self.assertTrue(any("--from version" in o for o in self.e.out))

    def test_the_version_is_read_from_the_commit_not_the_working_tree(self):
        # The working tree says a higher version than the commit does: only the commit's counts.
        self.e.close(); self.e = Env(published="0.28.53.16")
        with open(os.path.join(self.e.base, "version.py"), "w") as fh:
            fh.write('VERSION = "0.29.0"\n')
        self.e.fake.on(lambda l: "git show HEAD:version.py" in l, (0, 'VERSION = "0.29.0"\n'))   # nor HEAD's
        self.assertEqual(self.run_it(), 1)
        self.assertFalse(self.e.has("git merge"))
        self.assertTrue(any("0.28.53.16 is not higher" in o for o in self.e.out), "\n".join(self.e.out))
        show = [c for c in self.e.fake.calls if c[:2] == ["git", "show"]]
        self.assertTrue(any(c[2] == SHA + ":version.py" for c in show), show)

    def test_from_version_and_from_merge_both_work(self):
        self.assertEqual(release.STEPS.index("version") + 1, release.STEPS.index("merge"))
        self.assertEqual(self.run_it("--from", "version"), 1)  # not tested here: the version passes, the merge refuses
        self.assertTrue(any("STOPPED at merge" in o for o in self.e.out), "\n".join(self.e.out))
        self.assertFalse(self.e.has("quick_tests"))
        self.e.close(); self.e = Env()
        self.e.make().remember_tested(SHA)
        self.assertEqual(self.run_it("--from", "version"), 0, "\n".join(self.e.out))
        self.assertTrue(self.e.has("git merge --ff-only " + SHA))
        self.assertLess(self.index("FETCH "), self.index("git merge --ff-only"))
        self.e.close(); self.e = Env()
        self.e.make().remember_tested(SHA)
        self.assertEqual(self.run_it("--from", "merge"), 0, "\n".join(self.e.out))
        self.assertTrue(self.e.has("git merge --ff-only " + SHA))

    def test_no_release_yet_is_allowed(self):
        self.e.close(); self.e = Env(published=None)
        self.assertEqual(self.run_it(), 0, "\n".join(self.e.out))

    def test_an_export_refusal_stops(self):
        self.e.fake.on(lambda l: "export_public.py --check" in l, (1, "950 files\nWould refuse: README.txt home path\n"))
        self.assertEqual(self.run_it(), 1)
        self.assertFalse(self.e.has("robocopy"))
        self.assertFalse(self.e.has("gh release create"))

    def test_robocopy_8_stops_and_1_to_7_pass(self):
        for code in range(0, 8):
            self.e.close(); self.e = Env()
            self.e.fake.on(lambda l: l.startswith("robocopy"), (code, ""))
            self.assertEqual(self.run_it(), 0, code)
        for code in (8, 16):
            self.e.close(); self.e = Env()
            self.e.fake.on(lambda l: l.startswith("robocopy"), (code, ""))
            self.assertEqual(self.run_it(), 1, code)
            self.assertFalse(self.e.has("commit -m Manticore"), code)
            self.assertFalse(self.e.has("gh release create"), code)

    def test_xf_report_intake_only_when_the_release_lacks_it(self):
        self.assertEqual(self.run_it(), 0)
        robo = next(c for c in self.e.fake.calls if c[0] == "robocopy")
        self.assertIn("/XF", robo); self.assertIn("report_intake.py", robo)
        self.assertEqual(robo[robo.index("/XD") + 1: robo.index("/XD") + 3], [".git", ".github"])
        self.e.close(); self.e = Env(report_intake=True)
        self.assertEqual(self.run_it(), 0)
        robo = next(c for c in self.e.fake.calls if c[0] == "robocopy")
        self.assertNotIn("/XF", robo)
        self.assertIn(".github", robo)

    def test_y_publishes_and_anything_else_does_not(self):
        for ans in ("y", "Y", "yes"):
            self.e.close(); self.e = Env()
            self.assertEqual(self.run_it(answers=(ans,)), 0, ans)
            self.assertTrue(self.e.has("gh release create"), ans)
            self.assertTrue(any("Publish Manticore 0.28.53.16 to testers? [y/N]" in o for o in self.e.out))
        for ans in ("", "n", "no", "maybe"):
            self.e.close(); self.e = Env()
            self.assertEqual(self.run_it(answers=(ans,)), 1, ans)
            self.assertFalse(self.e.has("gh release create"), ans)
            self.assertFalse(self.e.has("gh release upload"), ans)

    def test_yes_skips_the_question(self):
        self.assertEqual(self.run_it("--yes", answers=()), 0)
        self.assertFalse(any("[y/N]" in o for o in self.e.out))
        self.assertTrue(self.e.has("gh release create"))

    def test_an_existing_release_gets_an_upload(self):
        self.e.fake.on(lambda l: l.startswith("gh release view"), (0, "v0.28.53.16"))
        self.assertEqual(self.run_it(), 0, "\n".join(self.e.out))
        self.assertFalse(self.e.has("gh release create"))
        up = next(c for c in self.e.fake.calls if c[:3] == ["gh", "release", "upload"])
        self.assertTrue(any(x.endswith("Manticore-0.28.53.16-setup.exe") for x in up))
        self.assertTrue(any(x.endswith("latest.json") for x in up))

    def test_check_refuses_a_feed_that_does_not_match(self):
        self.e.feed_after = {"version": "0.28.53.15", "sha256": "0" * 64}
        self.assertEqual(self.run_it(), 1)
        self.assertTrue(any("STOPPED at check" in o for o in self.e.out))
        self.e.close(); self.e = Env()
        self.e.feed_after = {"version": "0.28.53.16", "sha256": "0" * 64}
        self.assertEqual(self.run_it(), 1)

    MUTATING = ("git fetch", "git checkout", "git pull", "git merge --ff", "git push", "git add", "git commit",
                "setup_forge", "quick_tests", "build_installer", "make_update_feed", "robocopy", "gh release create",
                "gh release upload", "export_public.py " )

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_it("--dry-run", answers=()), 0, "\n".join(self.e.out))
        for l in self.e.joined():
            for bad in self.MUTATING:
                if bad == "export_public.py " and "--check" in l:
                    continue
                self.assertNotIn(bad, l, l)
        self.assertTrue(any("would run: git merge --ff-only" in o for o in self.e.out))
        self.assertTrue(any("would run: gh release create" in o for o in self.e.out))
        self.assertTrue(any("would ask: Publish" in o for o in self.e.out))
        self.assertFalse(os.path.exists(os.path.join(self.e.base, "release_logs", "tested.json")))

    def test_dry_run_is_allowed_off_windows_but_a_real_run_is_not(self):
        self.assertEqual(self.run_it("--dry-run", windows=False, answers=()), 0)
        self.e.close(); self.e = Env()
        self.assertEqual(self.run_it(windows=False), 1)
        self.assertFalse(self.e.has("git fetch"))

    def test_the_stamp_skips_a_passed_test_only_for_the_same_commit(self):
        self.e.fake.on(lambda l: "build_installer.py" in l, (1, "no"))
        self.assertEqual(self.run_it(), 1)
        self.assertEqual(sum("quick_tests" in l for l in self.e.joined()), 1)
        self.e.fake.calls.clear()
        self.assertEqual(self.run_it(), 1)                    # same commit: quick_tests not run again
        self.assertFalse(self.e.has("quick_tests"))
        self.e.fake.calls.clear()
        other = "b" * 40
        self.e.fake.on(lambda l: "rev-parse --verify" in l and "refs/heads/" in l, (0, other + "\n"))
        self.e.fake.on(lambda l: l.endswith("rev-parse HEAD"), (0, other + "\n"))
        self.assertEqual(self.run_it(), 1)                    # a later commit: tested again
        self.assertTrue(self.e.has("quick_tests"))

    def test_a_failed_quick_tests_is_not_remembered_and_blocks_the_merge(self):
        self.e.fake.on(lambda l: "quick_tests" in l, (1, "FAILED (failures=2)"))
        self.assertEqual(self.run_it(), 1)
        self.assertFalse(self.e.has("git merge"))
        self.e.fake.calls.clear()
        self.assertEqual(self.run_it("--from", "merge"), 1)  # the merge refuses an untested commit
        self.assertFalse(self.e.has("git merge --ff-only"))

    def test_the_backup_minutes_are_waited_out_or_refused(self):
        self.e.close(); self.e = Env(minute=6)
        self.assertEqual(self.run_it(), 0)
        self.assertTrue(any("backup task commits" in o for o in self.e.out))
        self.e.close(); self.e = Env(minute=36)
        self.assertEqual(self.run_it("--no-wait"), 1)
        self.assertFalse(self.e.has("git fetch"))

    def test_the_wrong_public_remote_stops(self):
        self.e.fake.on(lambda l: "remote get-url origin" in l, (0, "https://github.com/someone/else.git\n"))
        self.assertEqual(self.run_it(), 1)
        self.assertFalse(self.e.has("git fetch"))

    def test_long_notes_warn(self):
        self.assertEqual(self.run_it("--notes", "x" * 212), 0)
        self.assertTrue(any("WARNING" in o and "212" in o for o in self.e.out))

    def test_nothing_secret_reaches_the_log(self):
        hook = "https://discord.com/api/webhooks/123456789012345678/" + "Q" * 68
        tok = "gho_" + "Z" * 36
        self.e.fake.on(lambda l: "gh auth status" in l, (0, f"Logged in. Token: {tok}\nhook {hook}\n"))
        self.assertEqual(self.run_it(), 0)
        text = self.e.log_text() + "\n".join(self.e.out)
        self.assertNotIn(tok, text)
        self.assertNotIn("Q" * 68, text)
        self.assertIn("[hidden]", text)
        self.assertFalse(self.e.has("bug_report_config"))

    def test_it_never_reads_the_bug_report_config(self):
        src = open(os.path.join(BASE, "tools", "release.py"), encoding="utf-8").read()
        self.assertEqual(src.count("bug_report_config"), 1, "only the docstring may name it")


class HelperTests(unittest.TestCase):
    def test_higher(self):
        self.assertTrue(release.higher("0.28.53.16", "0.28.53.15"))
        self.assertTrue(release.higher("0.28.53.10", "0.28.53.9"))
        self.assertTrue(release.higher("0.29", "0.28.53.15"))
        self.assertFalse(release.higher("0.28.53.15", "0.28.53.15"))
        self.assertFalse(release.higher("0.28.53", "0.28.53.0"))
        self.assertFalse(release.higher("x", "0.1"))

    def test_version_from_source(self):
        self.assertEqual(release.version_from_source(open(os.path.join(BASE, "version.py"), encoding="utf-8").read()),
                         __import__("version").VERSION)

    def test_branch_needed_up_to_the_merge(self):
        with self.assertRaises(SystemExit):
            release.parse_args(["--notes", "x"])
        self.assertEqual(release.parse_args(["--from", "build"]).from_step, "build")

    def test_release_logs_are_ignored_not_backed_up_not_exported(self):
        sys.path.insert(0, BASE)
        import backup
        sys.path.insert(0, os.path.join(BASE, "tools"))
        import export_public
        self.assertIn("release_logs", backup.TOP_LEVEL_SKIP)
        self.assertIn("release_logs", export_public.TOP_LEVEL_SKIP)
        self.assertIn("release_logs/", open(os.path.join(BASE, ".gitignore"), encoding="utf-8").read().splitlines())


if __name__ == "__main__":
    unittest.main()
