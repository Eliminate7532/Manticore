# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AI2c: rule 4 of Threats.java - an AI no longer counters another player's counterspell that is aimed at a threat (round
AI2b's 3-player bench: two AIs holding Counterspell fought each other over my Basalt Monolith and it resolved in 12 of 15 trials,
with AI2 on as with it off). Offline: the source, the guard board P7 and its judge. Live (real Forge): P2 with the rule, and the
guard - an AI still protects its own spell."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_bench
import card_check
from tests import live

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THREATS = os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "Threats.java")


def source():
    with open(THREATS, encoding="utf-8") as f:
        return f.read()


def method(src, name):
    """The body of a Java method, from its signature to the closing brace at its own indent."""
    m = re.search(r"\n(    (?:static |private |public )[^\n=;]*\b%s\([^\n]*\{\n.*?\n    \}\n)" % re.escape(name), src, re.S)
    assert m, name
    return m.group(1)


class SourceTests(unittest.TestCase):
    def test_rule_4_holds_only_when_forge_chose_to_counter(self):
        body = method(source(), "decide")
        i = body.index("// 4.")
        block = body[i:body.index("// 1.", i)]
        self.assertIn("if (countering)", block)
        self.assertIn("answeredThreat(ai, top)", block)
        self.assertIn("return null;", block)                       # pass and keep the card, as rule 2
        self.assertIn('note(ai, "held"', block)
        self.assertLess(body.index("// 2."), i)                    # after the cheap-ramp hold, before rule 1

    def test_answered_threat_is_another_players_threat_spell_only(self):
        body = method(source(), "answeredThreat")
        self.assertIn("isCounter(top)", body)                     # only a counter on top is looked at
        self.assertIn("getFirstTargetedSpell()", body)
        self.assertIn("target.isSpell()", body)                    # an ability (a Stifle's target) is not this case
        self.assertIn("caster.equals(ai)", body)                   # the AI's own spell: Forge's call, as before
        self.assertIn("spellThreat(target.getHostCard(), caster) > NONE", body)

    def test_the_class_comment_names_rule_4(self):
        self.assertIn(" *  4. (round AI2c", source())


def trial(key, **kw):
    t = ai_bench.Trial(ai_bench.BOARD_BY_KEY[key], 0)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class BenchTests(unittest.TestCase):
    def test_p7_is_ai_2s_turn_with_both_counters(self):
        b = ai_bench.BOARD_BY_KEY["P7"]
        self.assertEqual((b.players, b.active, b.until_mine), (3, 2, True))
        self.assertEqual(b.seats[1][1], ["Counterspell"])
        self.assertEqual(b.seats[2][0], [ai_bench.KINNAN])
        self.assertEqual(b.seats[2][1], [ai_bench.BASALT, "Counterspell"])
        self.assertIn(ai_bench.BASALT, b.watch)
        self.assertIn("P7", ai_bench.board_keys(["pod"]))

    def test_p7_judge(self):
        judge, b = ai_bench.BOARD_BY_KEY["P7"].judge, ai_bench.BASALT
        self.assertIsNone(judge(trial("P7", casts=[], zones={b: "opponent's hand"})))                       # never cast
        self.assertIsNone(judge(trial("P7", casts=["AI 2: Basalt Monolith"], zones={b: "opponent's battlefield"})))  # no attack
        self.assertTrue(judge(trial("P7", casts=["AI 2: Basalt Monolith", "AI 1: Counterspell", "AI 2: Counterspell"],
                                    zones={b: "opponent's battlefield"})))
        self.assertFalse(judge(trial("P7", casts=["AI 2: Basalt Monolith", "AI 1: Counterspell"],
                                     zones={b: "opponent's graveyard"})))
        self.assertIsNone(judge(trial("P7", casts=["AI 2: Basalt Monolith", "AI 3: Counterspell"],
                                      zones={b: "opponent's graveyard"})))                                # not AI 1's counter


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    """Real Forge, the bench's own boards, seed 7, one engine for a table of three."""

    @classmethod
    def setUpClass(cls):
        cls.chk = card_check.Checker(ai_bench.KINNAN_DECK, seed=7, say=lambda *_a: None, per_check=ai_bench.TRIAL_BUDGET,
                                     classic_stops=True, opponents=2)
        cls.chk.start()

    @classmethod
    def tearDownClass(cls):
        cls.chk.stop()

    def test_p2_one_counter_and_my_basalt_is_countered(self):
        """AI2b measured 3 of 15 right here with AI2 on: AI 2 countered AI 1's Counterspell. With rule 4, 3 trials of 3."""
        for i in range(3):
            t = ai_bench.run_trial(self.chk, ai_bench.BOARD_BY_KEY["P2"], i)
            self.assertEqual(t.error, "")
            self.assertTrue(t.verdict, t.as_dict())
            self.assertEqual(ai_bench.counters_cast(t), 1, t.as_dict())

    def test_p7_rule_4_never_stops_an_ai_protecting_its_own_spell(self):
        for i in range(3):
            t = ai_bench.run_trial(self.chk, ai_bench.BOARD_BY_KEY["P7"], i)
            self.assertEqual(t.error, "")
            self.assertFalse([m for m in t.ai2 if "would let" in m], t.as_dict())


if __name__ == "__main__":
    unittest.main()
