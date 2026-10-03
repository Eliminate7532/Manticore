# SPDX-License-Identifier: GPL-3.0-or-later
"""Every game saved with  python replay.py report.zip --save-as NAME  (folders in tests/reports/) is replayed with the real engine
and must end on the same board with the same game log as when it was saved. With no saved games, or without Forge, this does nothing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import replay
import tests.live as live

PROBLEM = live.live_problem()
NAMES = replay.saved_names()


@unittest.skipIf(PROBLEM, f"Forge is not ready here: {PROBLEM}")
@unittest.skipUnless(NAMES, "no games saved in tests/reports yet")
class SavedReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()

    def test_every_saved_game_still_plays_out_the_same(self):
        for name in NAMES:
            with self.subTest(game=name):
                problems, meta = replay.check_saved(name)
                self.assertEqual(problems, [], f"{name} ({meta.get('note') or 'no note'}) no longer plays out as saved. If that is deliberate, "
                                              f"delete tests/reports/{name} and save it again with --save-as.")


if __name__ == "__main__":
    unittest.main()
