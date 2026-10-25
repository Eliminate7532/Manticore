# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SB4: the soak bot's yes/no loop, found by the cloud soak of release-0.28.53.12 (23 Oct 2026, classic draft decks, game 2).
Chain of Smog went back and forth between the soak seat and the AI: Forge asked "Do you want to copy the spell Chain of Smog?"
(InputConfirm, Yes/No, in the prompt bar), the bot said Yes, the copy targeted the AI, the AI copied back - 575 times, until the
30-minute time cap. The two states here are the real ones from that game's record (tests/fixtures/soak/sb4_chain_of_smog.json.gz).
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

FIX = os.path.join(BASE, "tests", "fixtures", "soak", "sb4_chain_of_smog.json.gz")


def states():
    with gzip.open(FIX, "rt", encoding="utf-8") as f:
        return json.load(f)


class StandIn:
    """Forge as it behaved in game 2: Yes on the copy question puts a copy on the stack and gives the bot priority; passing
    priority lets the copy resolve, the AI copies back, and the same question comes back under a new question number. No ends
    the chain (the question is over)."""

    def __init__(self, turn=None, phase=None, text=None):
        s = states()
        self.copy, self.prio = s["copy"], s["priority"]
        for st in (self.copy, self.prio):
            if turn is not None:
                st["turn"] = turn
            if phase is not None:
                st["phase"] = phase
        if text:
            self.copy["prompt"]["message"] = text
        self.seq = self.copy["inputSeq"]
        self.state = self.question(self.copy)
        self.yes = 0
        self.done = False

    def question(self, base):
        self.seq += 1
        st = copy.deepcopy(base)
        st["inputSeq"] = self.seq
        return st

    def send(self, move):
        msg = self.state["prompt"]["message"]
        if "copy the spell" in msg:
            if move == ("ok",):
                self.yes += 1
                self.state = self.question(self.prio)
            elif move == ("cancel",):
                self.done = True
        elif msg.startswith("Priority:") and move == ("ok",):
            self.state = self.question(self.copy)


def play(bot, forge, moves=60):
    mem = {}
    for _ in range(moves):
        if forge.done:
            break
        move = bot.next_action(forge.state, [], mem)
        forge.send(move)
    return forge


class LoopTests(unittest.TestCase):
    def test_the_bot_says_no_after_the_cap_in_one_step(self):
        forge = play(soak_bot.SoakBot(seed=1), StandIn())
        self.assertTrue(forge.done, "the bot never said No: the chain went on")
        self.assertEqual(forge.yes, soak_bot.CONFIRM_REPEAT_CAP)

    def test_the_real_question_is_an_input_confirm_with_yes_and_no(self):
        s = states()["copy"]
        self.assertEqual(s["input"], "InputConfirm")
        self.assertEqual(s["prompt"]["ok"]["label"], "Yes")
        self.assertEqual(s["prompt"]["cancel"]["label"], "No")

    def test_a_new_step_counts_again(self):
        bot, mem = soak_bot.SoakBot(seed=1), {}
        first = StandIn()
        for _ in range(soak_bot.CONFIRM_REPEAT_CAP):              # four Yes in turn 13
            first.send(bot.next_action(first.state, [], mem))
            first.send(bot.next_action(first.state, [], mem))
        later = StandIn(turn=first.copy["turn"] + 2)
        later.seq = first.seq
        later.state = later.question(later.copy)
        self.assertEqual(bot.next_action(later.state, [], mem), ("ok",), "a later turn's question is new")

    def test_another_question_is_counted_apart(self):
        bot, mem = soak_bot.SoakBot(seed=1), {}
        a, b = StandIn(), StandIn(text="Do you want to copy the spell Fork?")
        for _ in range(soak_bot.CONFIRM_REPEAT_CAP):
            a.send(bot.next_action(a.state, [], mem))
            a.send(bot.next_action(a.state, [], mem))
        b.seq = a.seq
        b.state = b.question(b.copy)
        self.assertEqual(bot.next_action(b.state, [], mem), ("ok",))

    def test_polling_the_same_question_is_not_a_repeat(self):
        # tools/soak.py asks the bot once per question as it stands (round DROP1); asked again about the SAME question number,
        # the cap still counts it once (card_check.next_move has its own rules for a prompt seen many times)
        mem, forge = {}, StandIn()
        for _ in range(10):
            self.assertIsNone(soak_bot.SoakBot._confirm_loop_move(forge.state, forge.state["prompt"],
                                                                 forge.state["prompt"]["message"], mem))

    def test_the_cap(self):
        self.assertEqual(soak_bot.CONFIRM_REPEAT_CAP, 4)


if __name__ == "__main__":
    unittest.main()
