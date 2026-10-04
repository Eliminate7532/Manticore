# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28c: the nightly run (tools/nightly.py) and games from a mid-game board (soak_boards.py). SOAK_PLAN Phase C.

  ManaValueTests / BoardTests   the board built from each alpha deck is legal: its own cards, 5-6 lands, 1-2 creatures or
                                artifacts it could have cast, 5-7 in hand, the rest as its library, its commander back
  SetupDoneTests (live)         the bridge says "setup_done" when Forge has finished a set-up, including one that asks
                                a question on the way (a shock land); before 28c a click in that window killed the game
  RotationTests / SweepTests    which deck the card-check sweep takes next, resuming mid-deck, the night's state file
  SummaryTests                  the night's verdict: a bridge self-check failure makes it INVALID, other card-check FAILs don't
"""
import argparse
import datetime
import os
import random
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import tests.live as live

ALPHA = ["typal_lathril", "go_wide_adeline", "aristocrats_teysa", "voltron_light_paws", "spellslinger_veyran", "stompy_goreclaw"]


def deck(stem):
    return os.path.join(BASE, "sample_decks", stem + ".txt")


def have_card_scripts():
    import forge_client as fc
    return os.path.isdir(os.path.join(fc.DEFAULT_RUNTIME, "res", "cardsfolder", "a"))


class ManaValueTests(unittest.TestCase):
    def test_forge_cost_strings(self):
        import soak_boards as sb
        for cost, mv in (("2 G G", 4), ("X B B", 2), ("W/U W/U", 2), ("no cost", 0), ("", 0), ("2/W 2/W", 4), ("7", 7)):
            self.assertEqual(sb.mana_value(cost), mv, cost)


@unittest.skipUnless(have_card_scripts(), "needs Forge's card scripts (forge_runtime/)")
class BoardTests(unittest.TestCase):
    def zones(self, lines, seat):
        key = "p%d" % seat
        out = {}
        for line in lines:
            k, _, v = line.partition("=")
            if k.startswith(key) and not k[len(key):][:1].isdigit():
                out[k[len(key):]] = [x for x in v.split(";") if x]
        return out

    def test_every_alpha_deck_gives_a_legal_board(self):
        import card_check as cc
        import soak_boards as sb
        for stem in ALPHA:
            profiles = {p.name: p for p in cc.load_profiles(deck(stem))}
            cards = sb.DeckCards(deck(stem))
            for seed in range(6):
                lines, placed = sb.board_lines([deck(stem)], random.Random(seed))
                z = self.zones(lines, 0)
                bf, hand, lib = z["battlefield"], z["hand"], z["library"]
                self.assertEqual(placed[0], bf)
                lands = [n for n in bf if profiles[n].is_land]
                others = [n for n in bf if not profiles[n].is_land]
                self.assertIn(len(lands), (5, 6), (stem, seed))
                self.assertIn(len(others), (1, 2), (stem, seed))
                for n in others:
                    p = profiles[n]
                    self.assertTrue(sb.DeckCards.is_starter_permanent(p), (stem, n))
                    self.assertLessEqual(sb.mana_value(p.cost), len(lands), (stem, n, p.cost))
                self.assertTrue(5 <= len(hand) <= 7, (stem, seed, len(hand)))
                everything = sorted(bf + hand + lib)
                self.assertEqual(everything, sorted(p.name for p in cards.cards), "the whole deck, each card once")
                self.assertTrue(any(l.startswith("p0command=") and l.endswith("|IsCommander") for l in lines), stem)
                life = int(next(l for l in lines if l.startswith("p0life=")).split("=")[1])
                self.assertTrue(30 <= life <= 40)

    def test_every_seat_gets_its_own_deck(self):
        import soak_boards as sb
        decks = [deck(s) for s in ALPHA[:4]]
        lines, placed = sb.board_lines(decks, random.Random(3))
        self.assertEqual(sorted(placed), [0, 1, 2, 3])
        for seat, path in enumerate(decks):
            names = {p.name for p in sb.DeckCards(path).cards}
            self.assertTrue(set(self.zones(lines, seat)["battlefield"]) <= names, seat)
        self.assertIn("activeplayer=p0", lines)
        self.assertIn("activephase=MAIN1", lines)


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class SetupDoneTests(unittest.TestCase):
    def test_a_set_up_that_asks_a_question_still_says_done(self):
        """Breeding Pool in the new board asks "pay 2 life?" while the set-up is running. setup_done must come after it
        is answered, and only then may priority be passed (28c's first board game died passing it earlier)."""
        import card_check as cc
        chk = cc.Checker(deck("typal_lathril"))
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        before = s.setups_done
        s.setup(["activeplayer=p0", "activephase=MAIN1", "turn=5", "p0life=35", "p1life=35",
                 "p0battlefield=Forest;Forest;Swamp;Swamp;Breeding Pool;Llanowar Elves", "p0hand=Swamp;Forest",
                 "p0library=Forest;Forest;Forest;Forest;Forest", "p1battlefield=Forest;Forest",
                 "p1library=Forest;Forest;Forest;Forest;Forest"])
        end = time.time() + 60
        answered = False
        while time.time() < end and s.setups_done == before:
            s.poll()
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
                answered = True
            elif "pay 2 life" in ((s.state or {}).get("prompt") or {}).get("message", "").lower():
                s.ok()
                answered = True
                time.sleep(0.5)
            time.sleep(0.1)
        self.assertEqual(s.setups_done, before + 1, "no setup_done from the bridge")
        s.poll()
        names = [c["name"] for c in s.me()["zones"]["battlefield"]]
        self.assertIn("Llanowar Elves", names)
        self.assertIn("Breeding Pool", names)
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertNotIn("ConcurrentModificationException", f.read())


class FakeProfile:
    def __init__(self, i):
        self.deck_name = self.name = "Card %d" % i


class FakeResult:
    def __init__(self, card, status="OK", notes=()):
        self.card, self.level, self.status, self.notes = card, "cast", status, list(notes)
        self.prompts, self.trace, self.zone = [], [], ""

    def as_dict(self):
        return {"card": self.card, "status": self.status}


class FakeChecker:
    def __init__(self, path):
        self.path = path

    def start(self):
        pass

    def stop(self):
        pass

    def check_card(self, profile):
        return [FakeResult(profile.name)]


class RotationTests(unittest.TestCase):
    def test_alpha_decks_first(self):
        import nightly
        stems = [s for s, _p in nightly.deck_rotation("sample")]
        self.assertEqual(stems[:6], ALPHA)
        self.assertNotIn("kinnan_nbc_moxfield_export", stems)          # patch 38: out of the alpha (a test deck now)
        self.assertFalse(any(s.startswith("mine/") for s in stems))

    def test_next_deck_resumes_then_takes_the_first_unfinished_then_the_oldest(self):
        import nightly
        rot = [("a", "A"), ("b", "B"), ("c", "C")]
        self.assertEqual(nightly.next_deck(rot, {}), ("a", "A", 0))
        state = {"decks": {"a": {"complete": "2026-09-20"}, "b": {"next": 40}}}
        self.assertEqual(nightly.next_deck(rot, state), ("b", "B", 40))
        state = {"decks": {"a": {"complete": "2026-09-20"}, "b": {"complete": "2026-09-21"}}}
        self.assertEqual(nightly.next_deck(rot, state), ("c", "C", 0))
        state["decks"]["c"] = {"complete": "2026-09-19"}
        self.assertEqual(nightly.next_deck(rot, state), ("c", "C", 0))

    def test_until_is_the_next_one(self):
        import nightly
        now = datetime.datetime(2026, 9, 29, 23, 30)
        self.assertEqual(nightly.parse_until("07:00", now), datetime.datetime(2026, 9, 30, 7, 0))
        self.assertEqual(nightly.parse_until("23:45", now), datetime.datetime(2026, 9, 29, 23, 45))


class SweepTests(unittest.TestCase):
    def setUp(self):
        import card_check as cc
        self.cc = cc
        self.orig = cc.load_profiles
        cc.load_profiles = lambda path, runtime=None, only=None: [FakeProfile(i) for i in range(5)]
        self.addCleanup(setattr, cc, "load_profiles", self.orig)
        self.tmp = tempfile.mkdtemp()

    def test_a_deck_cut_short_resumes_next_night_and_then_completes(self):
        import nightly
        rot = [("a", "A"), ("b", "B")]
        state, path = {}, os.path.join(self.tmp, "state.json")
        calls = {"n": 0}

        class Slow(FakeChecker):
            def check_card(self_, profile):
                calls["n"] += 1
                return [FakeResult(profile.name)]
        # night 1: time for 3 cards only
        t0 = time.time()
        clock = {"t": t0}
        real_time = nightly.time.time
        nightly.time.time = lambda: clock["t"]
        try:
            def say(_s):
                clock["t"] += 1                     # each printed card line "takes" a second
            night = nightly.run_card_checks(rot, state, path, t0 + 3.5, self.tmp, say=say, checker_factory=Slow)
        finally:
            nightly.time.time = real_time
        self.assertEqual(night[0][0], "a")
        self.assertFalse(night[0][2])
        self.assertEqual(nightly.load_state(path)["decks"]["a"]["next"], 3)
        # night 2: plenty of time - "a" goes on from card 4, finishes, then "b" is checked
        night = nightly.run_card_checks(rot, nightly.load_state(path), path, time.time() + 60, self.tmp,
                                        say=lambda s: None, checker_factory=FakeChecker)
        self.assertEqual([(s, len(r), f) for s, r, f in night], [("a", 2, True), ("b", 5, True)])
        st = nightly.load_state(path)
        self.assertTrue(st["decks"]["a"]["complete"] and st["decks"]["b"]["complete"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "card_check_a.txt")))


class SummaryTests(unittest.TestCase):
    VALID_SOAK = "SOAK RUN: VALID\nCanary: not run\n"

    def test_a_clean_night_is_valid(self):
        import nightly
        text = nightly.night_summary((True, "ok"), [("a", [FakeResult("X")], True)], self.VALID_SOAK, ["Alpha decks swept: 1 of 5"], 0)
        self.assertTrue(text.startswith("NIGHT: VALID"), text)

    def test_a_card_the_checker_could_not_set_up_does_not_make_it_invalid(self):
        import nightly
        r = FakeResult("Fling", "FAIL", ["stalled: could not find a creature to sacrifice"])
        text = nightly.night_summary((True, "ok"), [("a", [r], True)], self.VALID_SOAK, [], 0)
        self.assertTrue(text.startswith("NIGHT: VALID"), text)
        self.assertIn("Fling", text)

    def test_a_bridge_self_check_failure_makes_it_invalid(self):
        import nightly
        r = FakeResult("Spiteful Visions", "FAIL", ["bridge self-check: order / wrong_count"])
        text = nightly.night_summary((True, "ok"), [("a", [r], True)], self.VALID_SOAK, [], 0)
        self.assertTrue(text.startswith("NIGHT: INVALID"), text)
        self.assertIn("BRIDGE SELF-CHECK FAILURES", text)

    def test_a_missed_canary_or_an_invalid_soak_makes_it_invalid(self):
        import nightly
        self.assertTrue(nightly.night_summary((False, "no check"), [], self.VALID_SOAK, [], 0).startswith("NIGHT: INVALID"))
        self.assertTrue(nightly.night_summary((True, "ok"), [], "SOAK RUN: INVALID - x\n", [], 0).startswith("NIGHT: INVALID"))


class VerdictTests(unittest.TestCase):
    def test_land_drops_count_only_in_games_from_turn_1(self):
        import soak_report
        board = {"seats": 2, "lands": 0, "spells": 8, "answered": 3, "kinds": {"a", "b", "c"}, "dropped": 0, "board": True}
        turn1 = {"seats": 2, "lands": 5, "spells": 6, "answered": 2, "kinds": {"a"}, "dropped": 0}
        self.assertTrue(soak_report.run_verdict([board, board, turn1])[0])
        self.assertTrue(soak_report.run_verdict([board])[0], "all from boards: no land-drop test at all")
        low = dict(turn1, lands=1)
        self.assertFalse(soak_report.run_verdict([board, low])[0])


if __name__ == "__main__":
    unittest.main()
