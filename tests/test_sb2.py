# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SB2: when clicking a land asks "Choose an ability" (Play land, or Channel / Cycling / a Town's ability / an MDFC's
spell face), the soak bot plays the land 90% of the time. The 15-16 Oct soak played such lands 6 of 44 times
(claude/SOAK_RUN_2026-10-16.md). The requests are real: tests/fixtures/soak/sb2_choose_an_ability.json."""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soak_bot
from tests import live

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(BASE, "tests", "fixtures", "soak", "sb2_choose_an_ability.json")
KINNAN = os.path.join(BASE, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")
SEEDS = range(2000)


def requests():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def tally(req, seeds=SEEDS):
    """{answer as a tuple: how many seeds gave it}, each answer checked against the question's own rules first."""
    out = {}
    for seed in seeds:
        ans = soak_bot.SoakBot(seed).answer_request(req)
        assert isinstance(ans, list) and len(ans) <= (req.get("max") or 1) and len(set(ans)) == len(ans), ans
        assert all(0 <= i < len(req["items"]) for i in ans), ans
        out[tuple(ans)] = out.get(tuple(ans), 0) + 1
    return out


class ChooseAnAbilityTests(unittest.TestCase):
    def test_the_land_is_played_about_nine_times_in_ten(self):
        for key, land in (("eiganjo", 0), ("steppe", 0), ("silundi_mdfc", 1)):
            t = tally(requests()[key])
            share = t.get((land,), 0) / len(SEEDS)
            self.assertGreater(share, 0.87, (key, t))
            self.assertLess(share, 0.93, (key, t))

    def test_the_other_ability_and_nothing_still_come_up_sometimes(self):
        """Channel, Cycling and an MDFC's spell face are questions of their own worth reaching: the other share keeps them."""
        for key, other in (("eiganjo", 1), ("steppe", 1), ("silundi_mdfc", 0)):
            t = tally(requests()[key])
            self.assertGreater(t.get((other,), 0), 20, (key, t))         # 3.75% of 2000 = 75 expected
            self.assertGreater(t.get((), 0), 40, (key, t))               # 6.25% of 2000 = 125 expected

    def test_a_question_without_play_land_is_answered_as_before(self):
        """Cyclonic Rift's "Choose an ability" (target / overload): the old rule, 25% skip then 0 or 1 of the two items:
        nothing 62.5%, each item 18.75% (arithmetic)."""
        t = tally(requests()["cyclonic_rift"])
        self.assertEqual(set(t), {(), (0,), (1,)})
        self.assertAlmostEqual(t[()] / len(SEEDS), 0.625, delta=0.04)
        self.assertAlmostEqual(t[(0,)] / len(SEEDS), 0.1875, delta=0.04)

    def test_play_land_in_another_question_is_not_special(self):
        req = dict(requests()["steppe"], title="Choose cards")
        t = tally(req)
        self.assertLess(t.get((0,), 0) / len(SEEDS), 0.3, t)

    def test_the_same_seed_gives_the_same_answers(self):
        reqs = list(requests().values())[1:]
        a = [soak_bot.SoakBot(7).answer_request(r) for r in reqs * 20]
        b = [soak_bot.SoakBot(7).answer_request(r) for r in reqs * 20]
        self.assertEqual(a, b)

    def test_next_action_answers_the_pending_question_the_same_way(self):
        req = requests()["eiganjo"]
        move = soak_bot.SoakBot(3).next_action({"players": []}, [req], {})
        self.assertEqual(move[0], "answer")
        self.assertIs(move[1], req)


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveLandTests(unittest.TestCase):
    def test_the_bot_plays_secluded_steppe_as_a_land(self):
        """Real Forge: a Secluded Steppe in hand with Plains to pay its cycling, no land played this turn. Clicking it asks
        Play land / Cycling; the bot (seed 1, whose first draw is in the 90%) answers Play land and the Steppe lands."""
        import card_check as cc
        chk = cc.Checker(KINNAN)
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        before = s.setups_done
        s.setup(["activeplayer=human", "activephase=MAIN1", "turn=4", "humanlife=40", "ailife=40", "humanlandsplayed=0",
                 "humanhand=Secluded Steppe", "humanbattlefield=Plains;Plains", "humangraveyard=", "humanexile=",
                 "humanlibrary=Plains;Plains;Plains;Plains", "aibattlefield=Forest", "aihand=", "ailibrary=Forest;Forest;Forest"])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        self.assertGreater(s.setups_done, before, "the set-up never finished")
        # Right after a set-up Forge hasn't marked the hand's playable cards yet (no "weak" flag until its next question), so the
        # test makes the click the bot makes in a real game (28ba's tests cover choosing it); everything after is the bot's.
        steppe = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Secluded Steppe")
        s.click_card(steppe["id"])
        bot, mem, asked, answers = soak_bot.SoakBot(seed=1), {}, [], []
        end = time.time() + 60
        while time.time() < end:
            s.poll()
            me = s.me()
            if any(c["name"] == "Secluded Steppe" for c in me["zones"]["battlefield"]):
                break
            if not any(c["name"] == "Secluded Steppe" for c in me["zones"]["hand"]):
                break                                          # cycled away: the test fails below
            reqs = list(s.requests)
            if reqs and reqs[0].get("title") == "Choose an ability":
                asked.append([it.get("label") for it in reqs[0]["items"]])
            mv = bot.next_action(s.state, reqs, mem)
            if mv is None:
                time.sleep(0.1)
                continue
            if mv[0] == "answer":
                answers.append(mv[2])
                s.answer(mv[1], mv[2])
            elif mv[0] == "click":
                s.click_card(mv[1])
            elif mv[0] == "ok" and answers:
                break                                          # answered, and the bot wants to pass: nothing left to play
            time.sleep(0.5)
        self.assertTrue(asked, "Forge never asked Choose an ability")
        self.assertEqual(asked[0][0], "Play land")
        self.assertTrue(asked[0][1].startswith("Cycling"), asked[0])
        self.assertEqual(answers[:1], [[0]])
        self.assertTrue(any(c["name"] == "Secluded Steppe" for c in s.me()["zones"]["battlefield"]),
                        "the Steppe is not on the battlefield")


if __name__ == "__main__":
    unittest.main()
