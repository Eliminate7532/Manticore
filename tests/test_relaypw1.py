# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RELAYPW1 (7 Nov 2026): a relay game's password in tools/online_soak.py comes from the system's secure source, not from
the game's seed, and a relay game's line in the summary prints no seed (ORS1, 4 Nov: "every game line in the public log prints
seed="). No Forge needed: the host session is a stand-in that only records the password it was given."""
import argparse
import os
import random
import tempfile
import unittest
from unittest import mock

from tools import online_soak as osk


class _Host:
    """Stands in for forge_client.HostSession: notes the password and fails at once, so run_game returns without Forge."""
    seen = []

    def __init__(self, deck, name, port, password, *a, **k):
        _Host.seen.append(password)
        self.hosting, self.exited, self.host_failed = None, False, "stand-in host"
        self.stderr_path = None

    def start(self):
        return self

    def poll(self):
        pass

    def close(self):
        pass


class RelayPasswordTests(unittest.TestCase):
    def setUp(self):
        osk._load_modules()
        _Host.seen = []

    def run_once(self, relay, seed=1000):
        sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_decks")
        pool = sorted(os.path.join(sample, f) for f in os.listdir(sample)
                      if f.endswith(".txt") and osk.soak._file_format(os.path.join(sample, f)) == "commander")[:2]
        args = argparse.Namespace(seed=seed, relay_addr=("127.0.0.1", 9) if relay else None, pool=pool, format="commander",
                                  pods=0.0, concede=0.0, leave=0.0, drops=0.0, resume=0.0)
        with tempfile.TemporaryDirectory() as out, mock.patch.object(osk.fc, "HostSession", _Host):
            r = osk.run_game(0, random.Random(seed), out, cert=None, args=args)
        self.assertEqual(r.ended, "host_failed")
        return r

    def test_a_relay_games_password_differs_across_two_runs_with_the_same_seed(self):
        self.run_once(True)
        self.run_once(True)
        self.assertEqual(len(_Host.seen), 2)
        self.assertNotEqual(_Host.seen[0], _Host.seen[1])
        self.assertNotEqual(_Host.seen[0], osk.fn.make_password(random.Random(1000)))     # not the old seeded one either

    def test_a_relay_games_password_still_has_the_usual_shape(self):
        self.run_once(True)
        words = _Host.seen[0].split("-")
        self.assertEqual(len(words), 4)
        self.assertTrue(words[-1].isdigit())

    def test_a_direct_game_keeps_the_fixed_local_password(self):
        self.run_once(False)
        self.assertEqual(_Host.seen, [osk.PASSWORD])

    def test_no_relay_game_line_contains_seed(self):
        r = self.run_once(True)
        self.assertTrue(r.relay)
        self.assertNotIn("seed=", r.line())
        self.assertNotIn("1000", r.line())
        self.assertTrue(r.line().startswith("game 0: decks="))
        self.assertNotIn("seed=", osk.summary_text([r], "2026-11-07 12:00", None, False))

    def test_a_direct_game_line_still_prints_its_seed(self):
        r = self.run_once(False)
        self.assertIn("seed=1000 ", r.line())


class LineTests(unittest.TestCase):
    def test_a_game_that_joined_through_the_relay_prints_no_seed(self):
        r = osk.OnlineResult(3, 4242, ["a.txt", "b.txt"])
        r.via, r.ended, r.turns = "relay", "game_over", 9
        self.assertEqual(r.line().split(" turns=")[0], "game 3: decks=a+b")
        self.assertTrue(r.line().endswith(" via=relay"))

    def test_a_relay_game_that_failed_before_joining_prints_no_seed_either(self):
        r = osk.OnlineResult(3, 4242, ["a.txt", "b.txt"])
        r.relay, r.ended = True, "relay_failed"
        self.assertNotIn("4242", r.line())

    def test_the_source_draws_no_password_from_a_seeded_rng(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "tools", "online_soak.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("make_password(random.Random", src)
        self.assertIn("make_password(secrets.SystemRandom())", src)


if __name__ == "__main__":
    unittest.main()
