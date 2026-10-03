# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SB1: the soak bot's payment loop, found by the alpha chain's sandbox soak (2 Oct, game 23).

An AI's Rhystic Study asked the bot to pay {1}. Its only mana source was Transmogrant Altar ({B}, tap, sacrifice a
creature). Clicking the Altar opened the Altar's own "Pay Mana Cost: {B}", which the bot couldn't pay and cancelled, and
that brought Rhystic Study's question back under a new question number - for 30 minutes, until the time cap.
The two states here are the real ones from that game's record (tests/fixtures/soak/sb1_rhystic_altar.json.gz).
"""
import copy
import gzip
import json
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import soak_bot

FIX = os.path.join(BASE, "tests", "fixtures", "soak", "sb1_rhystic_altar.json.gz")


def states():
    with gzip.open(FIX, "rt", encoding="utf-8") as f:
        return json.load(f)


class StandIn:
    """Forge as it behaved in game 23: on Rhystic Study's question a click on the Altar opens the Altar's {B}; Cancel on
    the Altar's question goes back to Rhystic Study under a new question number; Cancel on Rhystic Study declines to
    pay (the question is over)."""

    def __init__(self):
        s = states()
        self.rhystic, self.altar = s["rhystic"], s["altar"]
        self.seq = self.rhystic["inputSeq"]
        self.now = self.with_seq(self.rhystic)
        self.declined = False
        self.moves = []

    def with_seq(self, st):
        st = copy.deepcopy(st)
        st["inputSeq"] = self.seq
        return st

    def send(self, move):
        self.moves.append(move)
        at_rhystic = self.now["prompt"]["message"].startswith("Rhystic Study")
        if at_rhystic and move == ("cancel",):
            self.declined = True
            return
        self.seq += 1
        if at_rhystic and move == ("click", 1):
            self.now = self.with_seq(self.altar)
        elif not at_rhystic and move == ("cancel",):
            self.now = self.with_seq(self.rhystic)
        else:
            raise AssertionError("a move Forge didn't see in game 23: %r on %s" % (move, self.now["prompt"]["message"][:40]))


class PaymentLoopTests(unittest.TestCase):
    def test_the_bot_declines_rhystic_study_instead_of_going_round_for_ever(self):
        forge, bot, mem = StandIn(), soak_bot.SoakBot(seed=1), {}
        for _ in range(60):
            if forge.declined:
                break
            forge.send(bot.next_action(forge.now, [], mem))
        self.assertTrue(forge.declined, "still going round after 60 moves: %r" % forge.moves[-4:])
        rounds = forge.moves.count(("click", 1))
        self.assertEqual(rounds, soak_bot.PAY_REPEAT_CAP, forge.moves)

    def test_the_same_question_polled_again_is_not_counted_twice(self):
        """tools/soak.py asks the bot again when nothing changed for 3 s: same question number, so not a repeat."""
        st, bot, mem = states()["rhystic"], soak_bot.SoakBot(seed=1), {}
        for _ in range(10):
            self.assertIsNone(bot._payment_loop_move(st, st["prompt"], st["prompt"]["message"], mem))

    def test_the_count_starts_again_in_another_step(self):
        st, bot, mem = states()["rhystic"], soak_bot.SoakBot(seed=1), {}
        msg = st["prompt"]["message"]
        for i in range(soak_bot.PAY_REPEAT_CAP):
            st = dict(st, inputSeq=st["inputSeq"] + 1)
            self.assertIsNone(bot._payment_loop_move(st, st["prompt"], msg, mem))
        later = dict(st, inputSeq=st["inputSeq"] + 1, phase="MAIN2")
        self.assertIsNone(bot._payment_loop_move(later, later["prompt"], msg, mem))
        again = dict(st, inputSeq=st["inputSeq"] + 2)
        self.assertIsNone(bot._payment_loop_move(again, again["prompt"], msg, mem), "MAIN1 again is a new step too")

    def test_a_payment_with_cancel_greyed_is_left_to_card_check(self):
        st, bot, mem = states()["rhystic"], soak_bot.SoakBot(seed=1), {}
        st = copy.deepcopy(st)
        st["prompt"]["cancel"]["enabled"] = False
        for i in range(soak_bot.PAY_REPEAT_CAP + 3):
            st = dict(st, inputSeq=st["inputSeq"] + 1)
            self.assertIsNone(bot._payment_loop_move(st, st["prompt"], st["prompt"]["message"], mem))

    def test_different_costs_are_different_questions(self):
        """Paying a spell's cost changes the text as mana goes in ({2}{G} -> {1}{G}); each text counts on its own."""
        st, bot, mem = states()["rhystic"], soak_bot.SoakBot(seed=1), {}
        for i, cost in enumerate(["{4}", "{3}", "{2}", "{1}", "{G}", "{4}", "{3}"]):
            msg = "Some Spell (5)\n\nPay Mana Cost: " + cost
            st = dict(st, inputSeq=100 + i)
            self.assertIsNone(bot._payment_loop_move(st, st["prompt"], msg, mem), cost)


class TimeCapTests(unittest.TestCase):
    """A game that hits the time cap is now a warning naming the last question (game 23 was "0 failing findings")."""

    def test_a_game_at_the_time_cap_is_a_warning_with_its_last_question(self):
        from tests.test_round28bc import AiStallTests
        runner = AiStallTests("test_an_ai_gets_longer_than_my_seat")
        try:
            text = runner.run_game(0.5, 30.0, 3.0)
        finally:
            runner.doCleanups()
        warnings = text.split("WARNINGS", 1)[1].split("GAMES", 1)[0]
        self.assertIn("time_cap: still going after", warnings)
        self.assertIn("last prompt: 'Waiting for AI # (opp1)...'", warnings)      # the summary groups by text without numbers
        self.assertIn("ended=time_cap", text)
        self.assertIn("SOAK RUN: ", text)
        self.assertIn("0 game(s) with a failing finding", text)


if __name__ == "__main__":
    unittest.main()
