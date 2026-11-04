# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ENG-SOAKATTACK (3 Nov 2026) - the soak bot repeated a refused attack declaration for minutes.

CD5's online draft soak 3 (28 Oct, draft 3): in a 4-person game Lee's soak bot sent OK / Alpha Strike / Call Back for ~10 minutes
and Forge answered "Attack declaration invalid" each time; snapshots kept coming, so no stall rule fired. No recording was kept.
Reproduced live in this round (checkpoint/eng-soakattack/probe.py): a 3-player game, my two Grizzly Bears, AI 2 controls Trove of
Temptation ("each opponent must attack you ... with at least one creature each combat if able"). Forge's attack question starts
with AI 1 as the current defender and Alpha Strike declares every creature against the CURRENT defender only, so neither the empty
declaration nor Alpha Strike is ever valid: the old bot was refused 33 times in 60 s and never left the step
(tests/fixtures/soak/eng_soakattack_trove_main_record.jsonl.gz, its real record). A goaded creature (Disrupt Decorum, Vengeful
Ancestor, Grenzo... - all in the Commander Cube) is the same shape: it must attack a player other than its goader.

1. soak_bot: after Alpha Strike is refused too, the bot works through the declarations that can be valid (none; everything at
   each defender, the other defenders first; each creature alone at each defender), one per refusal, never the same one twice
   in a row. Tested here against a stand-in for Forge's attack question built from the real snapshot.
2. bridge_rules: more than ATTACK_REFUSED_LIMIT refusals in one turn is a WARNING attack_refused_loop with the refusal and the
   turn, so a loop shows in the soak summary instead of only as a time cap.
"""
import copy
import gzip
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge_rules
import soak_bot

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "soak")
TROVE = os.path.join(FIX, "eng_soakattack_trove.json")
MAIN_RECORD = os.path.join(FIX, "eng_soakattack_trove_main_record.jsonl.gz")
REFUSAL = {"t": "message", "title": "", "text": "Attack declaration invalid"}


class FakeAttackQuestion:
    """Forge's attack question (InputAttack) as far as the bot can see it, started from the real snapshot: a click on a creature
    that can attack declares it against the current defender (a second click takes it back), a click on a player makes that
    player the current defender for creatures declared after it, Cancel reads Alpha Strike (declare every creature that can
    attack, against the current defender) or Call Back (take every attacker back), and OK either ends the question or is refused
    with Forge's message, the declaration kept and the question asked again under a new number (as seen live: 15, 17, 19...)."""

    def __init__(self, state, valid):
        self.state = copy.deepcopy(state)
        self.valid = valid
        self.me = next(p for p in self.state["players"] if p["id"] == self.state["me"])
        self.names = {p["id"]: p["name"] for p in self.state["players"]}
        self.defender = next(p["id"] for p in self.state["players"] if p["id"] != self.state["me"] and not p.get("lost"))
        self.declared = {}
        self.done = False
        self.refused = []          # the declaration each refused OK was for
        self.infos = []
        self._sync()

    def creatures(self):
        return [c for c in self.me["zones"]["battlefield"] if c.get("isCreature")]

    def _sync(self):
        for c in self.creatures():
            c["attacking"] = c["id"] in self.declared
            if c["attacking"]:
                c.pop("weak", None)
            elif not c.get("tapped"):
                c["weak"] = 1
        self.state["prompt"]["message"] = ("Select creatures to attack %s or select player/card you wish to attack."
                                           % self.names[self.defender])
        self.state["prompt"]["cancel"] = {"label": "Call Back" if self.declared else "Alpha Strike", "enabled": True}
        self.state["prompt"]["ok"] = {"label": "OK", "enabled": True, "focus": True}

    def do(self, move):
        kind = move[0]
        if kind == "click":
            cid = move[1]
            card = next((c for c in self.creatures() if c["id"] == cid), None)
            if card is not None:
                if cid in self.declared:
                    del self.declared[cid]
                elif not card.get("tapped"):
                    self.declared[cid] = self.defender
        elif kind == "player":
            self.defender = move[1]
        elif kind == "cancel":
            if self.declared:
                self.declared = {}
            else:
                self.declared = {c["id"]: self.defender for c in self.creatures() if not c.get("tapped")}
        elif kind == "ok":
            if self.valid(dict(self.declared), self):
                self.done = True
            else:
                self.refused.append(frozenset(self.declared.items()))
                self.infos.append(dict(REFUSAL))
                self.state["inputSeq"] += 2
        self._sync()


def trove(declared, q):
    """Trove of Temptation under AI 2 (player 2): at least one creature must attack AI 2."""
    return 2 in declared.values()


def goaded_by_ai1(declared, q):
    """Both my creatures goaded by AI 1: each must attack, and not AI 1, since another opponent is there."""
    ids = {c["id"] for c in q.creatures() if not c.get("tapped")}
    return set(declared) == ids and 1 not in declared.values()


def never(declared, q):
    return False


def play(state, valid, seed, limit=200):
    q = FakeAttackQuestion(state, valid)
    bot = soak_bot.SoakBot(seed=seed)
    mem = {"infos": q.infos}
    moves = []
    for _ in range(limit):
        if q.done:
            break
        move = bot.next_action(q.state, [], mem)
        moves.append(move)
        if move is None:
            break
        q.do(move)
    return q, moves


class FixtureTests(unittest.TestCase):
    def test_the_fixture_is_the_real_question(self):
        with open(TROVE, encoding="utf-8") as f:
            fx = json.load(f)
        q = fx["attack_question"]
        self.assertEqual(q["input"], "InputAttack")
        self.assertEqual(len([p for p in q["players"] if p["id"] != q["me"]]), 2)
        self.assertIn("attack AI 1", q["prompt"]["message"])
        self.assertEqual(fx["message"], REFUSAL)
        self.assertEqual(fx["asked_again"]["inputSeq"], q["inputSeq"] + 2)


class BotTests(unittest.TestCase):
    def setUp(self):
        with open(TROVE, encoding="utf-8") as f:
            self.q = json.load(f)["attack_question"]

    def check_gets_past(self, valid, most):
        for seed in range(25):
            q, moves = play(self.q, valid, seed)
            self.assertTrue(q.done, "seed %d: still refused after %d moves (%d refusals)" % (seed, len(moves), len(q.refused)))
            self.assertLessEqual(len(q.refused), most, "seed %d: %d refusals" % (seed, len(q.refused)))

    def test_trove_of_temptation_gets_past(self):
        # The old bot (main 06a368e) never does: empty / Alpha Strike at AI 1 for ever (33 refusals in 60 s live).
        self.check_gets_past(trove, 4)

    def test_goaded_creatures_get_past(self):
        self.check_gets_past(goaded_by_ai1, 4)

    def test_a_creature_alone_at_another_player_is_tried(self):
        # Only one Bear may attack, and only AI 2: needs the "each creature alone" plans.
        bears = [c["id"] for c in FakeAttackQuestion(self.q, never).creatures()]
        one = lambda d, q: list(d.items()) == [(bears[1], 2)]
        q, _ = play(self.q, one, 3)
        self.assertTrue(q.done, "%d refusals" % len(q.refused))

    def test_never_the_same_refused_declaration_twice_in_a_row(self):
        for seed in range(10):
            q, moves = play(self.q, never, seed, limit=400)
            self.assertGreater(len(q.refused), 20)
            for a, b in zip(q.refused[1:], q.refused[2:]):
                self.assertNotEqual(a, b, "seed %d: the same declaration refused twice in a row: %s" % (seed, sorted(a)))
            self.assertNotIn(None, moves, "seed %d: the bot went quiet" % seed)

    def test_only_one_refused_and_alpha_strike_work_as_before(self):
        # Bident of Thassa / Howlsquad Heavy: everything must attack; Alpha Strike at the first defender does it.
        everyone = lambda d, q: set(d) == {c["id"] for c in q.creatures()}
        for seed in range(10):
            q, _ = play(self.q, everyone, seed)
            self.assertTrue(q.done)
            self.assertLessEqual(len(q.refused), 1)

    def test_plans_go_to_the_other_defender_first(self):
        bot = soak_bot.SoakBot(seed=1)
        me = next(p for p in self.q["players"] if p["id"] == self.q["me"])
        plans = [p[0] for p in bot._refused_attack_plans(self.q, me, self.q["prompt"])]
        self.assertEqual(plans[:3], ["none", ("all", 2), ("all", 1)])
        self.assertLessEqual(len(plans), soak_bot.ATTACK_PLAN_CAP)


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.msgs = bridge_rules.load_stream(MAIN_RECORD)

    def test_the_real_loop_is_a_warning_with_its_turn_and_text(self):
        found = [f for f in bridge_rules.check_stream(self.msgs) if f["rule"] == "attack_refused_loop"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "warn")
        self.assertIn("33 times in turn 1", found[0]["detail"])
        self.assertIn("Attack declaration invalid", found[0]["detail"])
        self.assertFalse(bridge_rules.has_fail(found))

    def cut(self, keep):
        out, n = [], 0
        for m in self.msgs:
            if m.get("t") == "message" and "Attack declaration invalid" in (m.get("text") or ""):
                n += 1
                if n > keep:
                    continue
            out.append(m)
        return out

    def test_up_to_the_limit_is_not_a_loop(self):
        msgs = self.cut(bridge_rules.ATTACK_REFUSED_LIMIT)
        self.assertEqual([f for f in bridge_rules.check_stream(msgs) if f["rule"] == "attack_refused_loop"], [])
        msgs = self.cut(bridge_rules.ATTACK_REFUSED_LIMIT + 1)
        self.assertEqual(len([f for f in bridge_rules.check_stream(msgs) if f["rule"] == "attack_refused_loop"]), 1)

    def test_turns_are_counted_apart(self):
        msgs = []
        for turn in (3, 4):
            msgs.append({"t": "state", "turn": turn})
            msgs.extend(dict(REFUSAL) for _ in range(bridge_rules.ATTACK_REFUSED_LIMIT))
        self.assertEqual([f for f in bridge_rules.check_stream(msgs) if f["rule"] == "attack_refused_loop"], [])
        msgs.extend(dict(REFUSAL) for _ in range(2))
        found = [f for f in bridge_rules.check_stream(msgs) if f["rule"] == "attack_refused_loop"]
        self.assertEqual(len(found), 1)
        self.assertIn("turn 4", found[0]["detail"])

    def test_a_reduced_record_still_shows_it(self):
        # Round HUGE1: a record too big to read whole is judged cut down; the messages are kept whole.
        msgs, _info = bridge_rules.load_stream_reduced(MAIN_RECORD)
        self.assertEqual(len([f for f in bridge_rules.check_stream(msgs) if f["rule"] == "attack_refused_loop"]), 1)


if __name__ == "__main__":
    unittest.main()
