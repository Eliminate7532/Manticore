# SPDX-License-Identifier: GPL-3.0-or-later
"""Round LG2: the AI loop guard counts a repeat only when the AI's board isn't shrinking (Karl, 13 Oct).

Soak night 14 (claude/SOAK_NIGHT14_2026-10-07.md): six loop-guard stops, all Carrion Feeder eating real creatures and tokens for
an aristocrats drain - finite, since each use costs a creature - cut at 10. Now a use counts only when the AI's own board
(permanents it controls, creatures among them) is no smaller than at that ability's previous use; every use is also counted, and
SHRINK_CAP (100) uses in one turn stop it whatever the board did. The soak's ai_loop rule (bridge_rules) counts the same way.

  SourceTests        LoopGuard.java: the board test, the two counts, the cap, the message
  SoakRuleTests      bridge_rules.ai_loop on recorded-shape streams: a shrinking chain is fine, a steady one fails
  LiveTests (live)   the real engine and AI: night 14's pattern (Carrion Feeder unblocked, a lethal chain) runs to the end;
                     a shrinking chain that would run past SHRINK_CAP is stopped there. The Monolith loop is still stopped at 10
                     (tests/test_round28e.py LiveLoopTests, unchanged).
"""
import collections
import os
import re
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tests.live as live
import bridge_rules as br
import forge_client as fc

LOOP_GUARD = os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "LoopGuard.java")
FEEDER = "Carrion Feeder"


def source():
    with open(LOOP_GUARD, encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_the_board_is_the_ais_permanents_and_creatures(self):
        s = source()
        self.assertIn("getCardsIn(ZoneType.Battlefield)", s)
        self.assertIn("c.isCreature()", s)
        self.assertRegex(s, r"now\[0\] < before\[0\] \|\| now\[1\] < before\[1\]")

    def test_a_repeat_counts_only_when_the_board_did_not_shrink(self):
        s = source()
        self.assertRegex(s, r"if \(!shrank\(board\.get\(k\), here\)\) \{\s*uses\.merge\(k, 1, Integer::sum\);\s*\}\s*"
                            r"all\.merge\(k, 1, Integer::sum\);\s*board\.put\(k, here\);")

    def test_both_limits_stop_it(self):
        s = source()
        self.assertIn("static final int DEFAULT_CAP = 10;", s)
        self.assertIn("static final int SHRINK_CAP = 100;", s)
        self.assertIn("uses.getOrDefault(k, 0) >= cap || all.getOrDefault(k, 0) >= SHRINK_CAP", s)

    def test_a_new_turn_forgets_everything(self):
        block = source().split("if (now != turn) {", 1)[1].split("}", 1)[0]
        for m in ("uses.clear();", "all.clear();", "board.clear();", "told.clear();"):
            self.assertIn(m, block)

    def test_the_message_says_which_limit(self):
        s = source()
        self.assertIn('" (the board kept shrinking)"', s)
        self.assertIn('m.addProperty("shrinking", shrinking);', s)
        self.assertIn('m.addProperty("uses", all.getOrDefault(k, 0));', s)


def state(turn, creatures, lands=4, who="AI 1 (opp1)", pid=1):
    bf = [{"id": 100 + k, "name": "Egg Token", "isCreature": True, "controller": pid} for k in range(creatures)]
    bf += [{"id": 300 + k, "name": "Swamp", "isLand": True, "isCreature": False, "controller": pid} for k in range(lands)]
    return {"t": "state", "turn": turn, "phase": "COMBAT_DECLARE_BLOCKERS",
            "players": [{"id": 0, "name": "Soak", "zones": {"battlefield": []}},
                        {"id": pid, "name": who, "zones": {"battlefield": bf}}]}


def activated(card=FEEDER, who="AI 1 (opp1)"):
    return {"t": "log", "entries": [{"type": "STACK_ADD", "text": "%s activated %s" % (who, card)}]}


def ai_loops(msgs):
    return [f for f in br.check_stream(msgs) if f["rule"] == "ai_loop"]


class SoakRuleTests(unittest.TestCase):
    def test_a_chain_that_eats_a_creature_each_time_is_no_loop(self):
        msgs = []
        for k in range(60):
            msgs += [state(5, 60 - k), activated()]
        self.assertEqual(ai_loops(msgs), [])

    def test_the_same_chain_on_a_board_that_stays_the_same_fails(self):
        msgs = []
        for _k in range(40):
            msgs += [state(5, 20), activated("Grim Monolith")]
        found = ai_loops(msgs)
        self.assertEqual(len(found), 1)
        self.assertIn("Grim Monolith", found[0]["detail"])

    def test_a_board_that_grows_is_not_shrinking(self):
        msgs = []
        for k in range(40):
            msgs += [state(5, 10 + k), activated()]
        self.assertEqual(len(ai_loops(msgs)), 1)

    def test_creatures_count_apart_from_permanents(self):
        msgs = []                                   # a creature for a Treasure: the permanents stay, the creatures go
        for k in range(40):
            msgs += [state(5, 40 - k, lands=4 + k), activated()]
        self.assertEqual(ai_loops(msgs), [])

    def test_no_newer_snapshot_is_not_counted(self):
        msgs = [state(5, 20)] + [activated("Grim Monolith") for _k in range(40)]
        self.assertEqual(ai_loops(msgs), [])

    def test_without_player_boards_every_activation_counts_as_before(self):
        msgs = [{"t": "state", "turn": 3, "players": []}] + [activated("Grim Monolith") for _k in range(31)]
        self.assertEqual(len(ai_loops(msgs)), 1)

    def test_another_players_shrinking_board_does_not_hide_a_loop(self):
        msgs = []
        for k in range(40):
            st = state(5, 20)
            st["players"][0]["zones"]["battlefield"] = [{"id": 900 + j, "isCreature": True, "controller": 0}
                                                         for j in range(40 - k)]
            msgs += [st, activated("Grim Monolith")]
        self.assertEqual(len(ai_loops(msgs)), 1)


PROBLEM = live.live_problem()


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveTests(unittest.TestCase):
    """The AI attacks with Carrion Feeder into an empty, tapped-out board with Eggs (0/1 defenders) as fodder. Forge's
    AristocratCounters logic (SpecialAiLogic.doAristocratWithCountersLogic at fb4d809) eats a creature for a counter at
    declare blockers while that makes the hit lethal."""

    def play(self, eggs, life, seconds=120):
        import card_check as cc
        tmp = tempfile.mkdtemp()
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Swamp"] * 99, "Opp")
        fc.sync_bridge()
        s = fc.ForgeSession(mine, [opp], name="Me", seed=7, dev=True)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end, mem = time.time() + 150, {}
        while time.time() < end:
            s.poll()
            st = s.state
            if st and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                break
            mv = cc.next_move(st, [], mem) if st else None
            if mv and mv[0] == "ok":
                s.ok()
                time.sleep(0.4)
            time.sleep(0.05)
        before = s.setups_done
        s.setup(["activeplayer=p1", "activephase=MAIN1", "turn=5", "p0life=%d" % life, "p1life=40",
                 "p0battlefield=Island|Tapped", "removesummoningsickness=true", "p0library=" + ";".join(["Island"] * 8),
                 "p1battlefield=" + ";".join([FEEDER] + ["T:g_0_1_egg_defender"] * eggs + ["Swamp"] * 4),
                 "p1library=" + ";".join(["Swamp"] * 8)])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        self.assertGreater(s.setups_done, before, "the set-up never finished")
        uses, seen, start = collections.Counter(), 0, None
        end = time.time() + seconds
        while time.time() < end and not s.game_over and not s.exited:
            s.poll()
            st = s.state or {}
            if start is None and st.get("turn") is not None:
                start = st.get("turn")
            for e in s.log[max(0, len(s.log) - (s.log_total - seen)):] if s.log_total > seen else []:
                if e.get("type") == "STACK_ADD" and ("activated " + FEEDER) in e.get("text", ""):
                    uses[st.get("turn")] += 1
            seen = s.log_total
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            elif st.get("prompt") and st["prompt"].get("ok"):
                s.ok()
            if st.get("turn", 0) >= (start or 0) + 2:
                break
            time.sleep(0.02)
        me = [p for p in (s.state or {}).get("players", []) if p.get("name") == "Me"]
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
        return s, max(uses.values() or [0]), (me[0].get("life") if me else None), log

    def test_night_14s_drain_runs_to_the_end(self):
        s, used, life, log = self.play(eggs=24, life=20)
        self.assertEqual(s.loop_guards, [], "the guard cut a chain that shrinks the board")
        self.assertGreater(used, 10, "the chain was cut at the old limit")
        self.assertTrue(s.game_over and life is not None and life <= 0, (s.game_over, life, used))
        self.assertNotIn("bridge: loop guard", log)

    def test_a_chain_past_the_shrink_cap_is_stopped_there(self):
        s, used, life, log = self.play(eggs=120, life=110)
        self.assertTrue(s.loop_guards, "nothing stopped a 109-step chain")
        g = s.loop_guards[0]
        self.assertEqual((g.get("card"), g.get("count"), g.get("uses"), g.get("shrinking")), (FEEDER, 100, 100, True))
        self.assertEqual(used, 100)
        self.assertGreater(life, 0)
        self.assertRegex(log, re.escape("used Carrion Feeder (") + r".*\) 100 times this turn \(the board kept shrinking\)")


if __name__ == "__main__":
    unittest.main()
