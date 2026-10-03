# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SH1: START_HERE.txt ships beside the program and opens from the installer's last page."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from tools import check_dist as cd
from tests.test_round29 import make_tree


def read(rel):
    with open(os.path.join(HERE, rel), "r", encoding="utf-8") as f:
        return f.read()


class PackagingTests(unittest.TestCase):
    def test_the_page_exists_and_is_for_testers(self):
        text = read("START_HERE.txt")
        for needle in ("MANTICORE ALPHA - START HERE", "F8", "Import a deck", "KNOWN ISSUES", "GNU GPL v3"):
            self.assertIn(needle, text)
        self.assertNotIn("Commander Sim", text)

    def test_the_build_ships_it(self):
        self.assertIn('_data("START_HERE.txt", ".")', read("commander_sim.spec"))
        self.assertIn("START_HERE.txt", cd.REQUIRED_FILES)

    def test_the_installer_offers_to_open_it(self):
        lines = [l for l in read(os.path.join("installer", "commander_sim.iss")).splitlines() if "START_HERE.txt" in l and l.startswith("Filename:")]
        self.assertEqual(len(lines), 1, lines)
        for flag in ("shellexec", "postinstall", "skipifsilent"):
            self.assertIn(flag, lines[0])
        self.assertNotIn("unchecked", lines[0])                 # ticked by default

    def test_it_keeps_windows_line_endings(self):
        self.assertIn("START_HERE.txt text eol=crlf", read(".gitattributes"))


class CheckDistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Manticore")
        make_tree(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(os.path.join(self.root, "START_HERE.txt"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_a_missing_page_is_a_problem(self):
        os.remove(os.path.join(self.root, "START_HERE.txt"))
        self.assertTrue(any("START_HERE.txt" in p for p in cd.check_dist(self.root, run=False).problems))

    def test_blanks_block_only_a_release(self):
        self.write("About [500 MB] of disk.\nRun Manticore-[x.y.z]-setup.exe\n")
        res = cd.check_dist(self.root, run=False)
        self.assertEqual(res.problems, [])
        self.assertEqual(res.blocking(release=False), [])
        blocking = res.blocking(release=True)
        self.assertTrue(any("[500 MB]" in b and "[x.y.z]" in b for b in blocking), blocking)

    def test_a_filled_in_page_is_green(self):
        self.write("About 480 MB of disk.\nRun Manticore-0.28.22-setup.exe\n")
        self.assertEqual(cd.check_dist(self.root, run=False).blocking(release=True), [])


if __name__ == "__main__":
    unittest.main()
