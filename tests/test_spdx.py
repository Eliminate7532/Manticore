# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SPDX: every Python file of the program, its tools and its tests says its licence in its first lines (GPL-3.0-or-later,
the project's licence since 2026-09-23). docs/ (drafts and reference copies) is not part of the program and is not checked.
The Java bridge's sources are deliberately not headed yet: any change to them changes the bridge's source stamp (Round 27c),
which would force a jar rebuild and make every saved game un-resumable (Round 28d's replay key) for a comment."""
import os
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "docs", "forge_runtime", "forge_bundle", "build", "dist", "installer_out", "cache", "soak_runs", "__pycache__",
             "my_decks", "saves", "bug_reports", "Claude outputs", "backups"}
TAG = "SPDX-License-Identifier: GPL-3.0-or-later"


def program_py_files():
    for dp, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dp, f)


class SpdxTests(unittest.TestCase):
    def test_every_program_file_says_its_licence_in_its_first_three_lines(self):
        missing = []
        for path in program_py_files():
            with open(path, encoding="utf-8") as f:
                head = [f.readline() for _ in range(3)]
            if not any(TAG in line for line in head):
                missing.append(os.path.relpath(path, BASE))
        self.assertEqual(missing, [], "add '# " + TAG + "' as the first line (after a #! or coding line)")


if __name__ == "__main__":
    unittest.main()
