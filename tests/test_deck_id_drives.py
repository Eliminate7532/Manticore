# SPDX-License-Identifier: GPL-3.0-or-later
"""Deck ids when decks are not under the program folder (found by GitHub Actions, 2026-10-02).

GitHub's Windows runner checks the project out on D: and makes temporary folders on C:. os.path.relpath raises ValueError
for paths on different drives, so DeckEntry() - and with it the whole deck screen - crashed
(tests.test_round28d LastSessionTests / ReplayKeyTests, red on every push since Round 28d). The same crash would hit an
installed copy whose program folder is on another drive than %APPDATA% (Round 28 keeps my_decks/ there).
"""
import ntpath
import os
import tempfile
import unittest
from unittest import mock

import deck_library as lib


class DeckIdTests(unittest.TestCase):
    def test_the_cause_relpath_refuses_paths_on_two_windows_drives(self):
        with self.assertRaises(ValueError):
            ntpath.relpath(r"C:\Users\x\AppData\Roaming\Manticore\my_decks\A.txt", r"D:\Games\Manticore")

    def test_a_deck_under_the_program_folder_keeps_its_old_id(self):
        with tempfile.TemporaryDirectory() as base:
            path = os.path.join(base, "my_decks", "Kinnan.txt")
            self.assertEqual(lib.deck_id(path, base), "my_decks/Kinnan.txt")
            path = os.path.join(base, "sample_decks", "typal_lathril.txt")
            self.assertEqual(lib.deck_id(path, base), "sample_decks/typal_lathril.txt")

    def test_a_deck_on_another_drive_gets_folder_and_file(self):
        with mock.patch("os.path.relpath", side_effect=ValueError("path is on mount 'D:', start on mount 'C:'")):
            self.assertEqual(lib.deck_id(os.path.join("somewhere", "my_decks", "Kinnan.txt"), "elsewhere"),
                             "my_decks/Kinnan.txt")

    def test_a_deck_outside_the_program_folder_gets_folder_and_file_not_dot_dot(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as user:
            path = os.path.join(user, "my_decks", "Kinnan.txt")
            self.assertEqual(lib.deck_id(path, base), "my_decks/Kinnan.txt")

    def test_the_deck_list_opens_when_decks_are_on_another_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            mine, samples = os.path.join(tmp, "my_decks"), os.path.join(tmp, "sample_decks")
            for folder in (mine, samples):
                os.makedirs(folder)
                with open(os.path.join(folder, "Deck.txt"), "w", encoding="utf-8") as f:
                    f.write("1 Kinnan, Bonder Prodigy\n")
            with mock.patch("os.path.relpath", side_effect=ValueError("different drives")):
                entries = lib.list_decks(mine, samples, base_dir=os.path.join(tmp, "program"))
            self.assertEqual([e.id for e in entries], ["my_decks/Deck.txt", "sample_decks/Deck.txt"])
            self.assertIs(lib.find(entries, "sample_decks/Deck.txt"), entries[1])


if __name__ == "__main__":
    unittest.main()
