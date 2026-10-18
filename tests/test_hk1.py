# SPDX-License-Identifier: GPL-3.0-or-later
"""Round HK1 (14 Oct 2026, Karl: housekeeping, "run 3-5"): what git keeps and what stays on this computer only.

  - Deliveries (docs/incoming/<round>/) are not committed any more - only docs/incoming/README.md is; applied ones move to
    ..\\manticore_archive\\incoming\\. They were 506 of the repository's 594 MiB.
  - last_good/ (Round LAST1's game states), the captured test output and the card checker's report are not committed.
  - Everything that is Karl's or the game's stays in: the round notes, the working agreement, the Adventure saves, stats, decks.
  - No tracked file matches an ignore rule (a rule added without `git rm --cached` would leave it tracked and half-ignored).
  - The parked nightly-build workflow is gone (Karl, 13 Oct: "delete the parked nightly-build workflow"); the tests workflow stays.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IGNORED = ["docs/incoming/rpg9/rpg9.mbox", "docs/incoming/rpg9/runs/5_check/summary.json", "docs/incoming/notes.md",
           "docs/incoming/ad1_rebase/ad1b_src.tar", "last_good/20261014_093333/state_0001.state", "last_good/restoring.state",
           "quick.txt", "test_results.txt", "r28d_test.txt", "card_check_report.txt"]
KEPT = ["docs/incoming/README.md", "docs/rounds/HK1.md", "docs/rounds/README.md", "docs/WORKING_AGREEMENT.md",
        "docs/CLAUDE_ARCHIVE.md", "rpg_saves/save_1.json", "rpg_saves/duel_stats/games.ndjson", "stats/games.ndjson",
        "my_decks/Tymna.txt", "sample_decks/stompy_goreclaw.txt", "last_good.py", "tests/test_hk1.py", "tools/rpg_balance.py",
        "rpg/data/balance.json", "README.txt", "CLAUDE.md"]


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


@unittest.skipUnless(shutil.which("git"), "needs git")
class IgnoreRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hk1_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        shutil.copy(os.path.join(HERE, ".gitignore"), self.tmp)
        git(["init", "-q"], self.tmp)

    def test_deliveries_game_states_and_test_output_stay_on_this_computer(self):
        for path in IGNORED:
            self.assertEqual(git(["check-ignore", path], self.tmp).stdout.strip(), path, f"{path} should not be committed")

    def test_the_notes_the_saves_and_the_decks_are_still_committed(self):
        for path in KEPT:
            self.assertEqual(git(["check-ignore", path], self.tmp).stdout.strip(), "", f"{path} should be committed")


@unittest.skipUnless(shutil.which("git") and os.path.isdir(os.path.join(HERE, ".git")), "needs git and this folder's repository")
class RepositoryTests(unittest.TestCase):
    def test_no_tracked_file_matches_an_ignore_rule(self):
        out = git(["ls-files", "-ci", "--exclude-standard"], HERE)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split("\n")[:-1] if out.stdout else [], [])


class WorkflowTests(unittest.TestCase):
    def test_the_nightly_build_workflow_is_gone_and_the_tests_workflow_stays(self):
        self.assertFalse(os.path.exists(os.path.join(HERE, ".github", "workflows", "nightly.yml")))
        self.assertTrue(os.path.isfile(os.path.join(HERE, "ci", "tests.yml")))


if __name__ == "__main__":
    unittest.main()
