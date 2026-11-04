# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ENG-FELIDAR: the AI loop guard also counts the AI's "yes" to an optional trigger.

Online draft soak 1, draft 13 (claude/CD5_2026-10-27.md): Felidar Guardian and Restoration Angel each have "When this enters,
you may exile [the other], then return it". Forge's AI always says yes, so the two flickered each other 4,614 times until Java
ran out of memory. The loop guard (Round 28e) counted only activated abilities. Forge asks the AI about a "may" trigger in
PlayerControllerAi.confirmTrigger(WrappedAbility), at the trigger's resolution; the guard's controller now counts those yeses
per (source card, trigger, triggering card) per turn and answers no at the same cap as activations (10).

  SourceTests          the guard overrides confirmTrigger, refuses at the cap, says so with kind "trigger", and never
                       counts a mandatory trigger; the jar is built from these sources
  RuleTests            the bridge's "loop guard" line for a trigger is not an engine error; the guard's message for a
                       trigger is the ai_loop_guard warning
  LiveFelidarTests     (live) the AI, with Restoration Angel on the battlefield, casts Felidar Guardian: without the trigger
                       count it loops past bridge_rules' AI_LOOP_LIMIT (30) in one turn and the turn never ends (fails on
                       the 0.28.53.14 jar); with it the guard says no at 10 each and the game goes on to game over
"""
import collections
import os
import re
import sys
import tempfile
import time
import unittest
import zipfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import tests.live as live
import bridge_rules as br
import forge_client as fc

JAVA = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
TRIGGERED = re.compile(r"triggered (Felidar Guardian|Restoration Angel)\b")


def read(name):
    with open(os.path.join(JAVA, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.src = read("LoopGuard.java")
        start = self.src.index("public boolean confirmTrigger(WrappedAbility wa)")
        self.body = self.src[start:self.src.index("int triggerYes", start)]

    def test_the_guard_counts_the_ai_s_yes_to_an_optional_trigger(self):
        self.assertIn("@Override\n        public boolean confirmTrigger(WrappedAbility wa)", self.src)
        self.assertIn("super.confirmTrigger(wa)", self.body)
        self.assertIn("yes.merge(k, 1, Integer::sum)", self.body)
        self.assertIn(">= cap", self.body)
        self.assertIn("return false;", self.body)

    def test_a_mandatory_trigger_is_never_counted_or_refused(self):
        self.assertIn("wa.isMandatory()", self.body.split("newTurn();")[0])

    def test_the_count_starts_again_every_turn(self):
        nt = self.src[self.src.index("private void newTurn()"):]
        nt = nt[:nt.index("\n        }\n")]
        nt = "\n".join(l.split("//")[0] for l in nt.splitlines())          # code only, never a comment
        for m in ("uses.clear()", "all.clear()", "yes.clear()", "yesAll.clear()"):
            self.assertIn(m, nt)

    def test_the_message_says_trigger(self):
        self.assertIn('m.addProperty("kind", "trigger")', self.src)
        self.assertIn('m.addProperty("kind", "ability")', self.src)
        self.assertIn('"bridge: loop guard: "', self.src)

    def test_the_jar_has_the_trigger_count(self):
        jar = os.path.join(BASE, "java_bridge", "forge_bridge.jar")
        with zipfile.ZipFile(jar) as z:
            data = z.read("forge/bridge/LoopGuard$Controller.class")
        self.assertIn(b"confirmTrigger", data)
        self.assertIn(b"triggerKey", data)


class RuleTests(unittest.TestCase):
    def test_the_trigger_line_is_not_an_engine_error(self):
        line = ("bridge: loop guard: AI 1 (Opp) said yes to Felidar Guardian's trigger (When Felidar Guardian enters, you may "
                "exile another target permanent you control, then return that card to the battlefield under your control.) "
                "10 times this turn; answering no instead")
        found = br.check_stream([{"t": "game_over"}], engine_log_lines=[line])
        self.assertEqual([f for f in found if f["severity"] == br.FAIL], [])

    def test_the_guard_message_for_a_trigger_is_a_warning(self):
        msg = {"t": "ai_loop_guard", "kind": "trigger", "player": "AI 1 (Opp)", "card": "Felidar Guardian", "cardId": 205,
               "ability": "When Felidar Guardian enters, ...", "turn": 5, "count": 10, "uses": 10, "shrinking": False}
        found = br.check_stream([msg, {"t": "game_over"}])
        self.assertEqual([(f["rule"], f["severity"]) for f in found], [("ai_loop_guard", br.WARN)])


PROBLEM = live.live_problem()


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveFelidarTests(unittest.TestCase):
    """The real engine, a real AI: Restoration Angel and four Plains on the AI's battlefield, Felidar Guardian in its hand, its
    first main phase. The human is at 7 life with nothing to block; the AI's fliers win in two of its turns once it stops."""

    def play(self, loop_cap, seconds, stop_after_turns=None):
        import card_check as cc
        tmp = tempfile.mkdtemp()
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Elspeth, Sun's Champion"], ["Plains"] * 99, "Opp")
        fc.sync_bridge()
        s = fc.ForgeSession(mine, [opp], name="Me", seed=7, dev=True, loop_cap=loop_cap)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end, mem = time.time() + 150, {}
        while time.time() < end:
            s.poll()
            st = s.state
            if st and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                break
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            mv = cc.next_move(st, [], mem) if st else None
            if mv and mv[0] == "ok":
                s.ok()
                time.sleep(0.4)
            time.sleep(0.05)
        before = s.setups_done
        s.setup(["activeplayer=p1", "activephase=MAIN1", "turn=5", "p0life=7", "p1life=40",
                 "p0battlefield=Island", "p0hand=Island", "p0library=Island;Island;Island;Island;Island;Island",
                 "p1battlefield=Restoration Angel;Plains;Plains;Plains;Plains",
                 "p1hand=Felidar Guardian", "p1library=Plains;Plains;Plains;Plains;Plains;Plains"])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        self.assertGreater(s.setups_done, before, "the set-up never finished")
        per_turn = collections.Counter()
        seen = 0
        start_turn = None
        end = time.time() + seconds
        while time.time() < end and not s.game_over and not s.exited:
            s.poll()
            st = s.state or {}
            if start_turn is None and st.get("turn") is not None:
                start_turn = st.get("turn")
            if s.log_total > seen:
                for e in s.log[max(0, len(s.log) - (s.log_total - seen)):]:
                    if TRIGGERED.search(e.get("text", "")):
                        per_turn[st.get("turn")] += 1
                seen = s.log_total
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            elif st.get("prompt") and st["prompt"].get("ok"):
                s.ok()
            if stop_after_turns is not None and start_turn is not None and st.get("turn", 0) >= start_turn + stop_after_turns:
                break
            time.sleep(0.02)
        return s, per_turn, start_turn

    def test_without_the_trigger_count_the_ai_loops(self):
        """loop_cap=0 turns the whole guard off: the 0.28.53.14 behaviour for triggers (its guard never counted them)."""
        s, per_turn, start = self.play(0, 45)
        self.assertGreater(max(per_turn.values() or [0]), br.AI_LOOP_LIMIT, per_turn)
        self.assertEqual((s.state or {}).get("turn"), start, "the turn should never have ended")
        self.assertFalse(s.game_over)

    def test_with_the_guard_the_ai_says_no_and_the_game_reaches_game_over(self):
        s, per_turn, start = self.play(None, 240)
        self.assertEqual(s.loop_cap_used, 10)
        guards = [g for g in s.loop_guards if g.get("kind") == "trigger"]
        self.assertTrue(guards, "the bridge never said it stopped a trigger loop: %r / %r" % (s.loop_guards, per_turn))
        self.assertIn(guards[0]["card"], ("Felidar Guardian", "Restoration Angel"))
        self.assertEqual(guards[0]["count"], 10)
        self.assertLessEqual(max(per_turn.values() or [0]), br.AI_LOOP_LIMIT, per_turn)
        self.assertTrue(s.game_over, "the game did not reach game over (turn %r, started %r)" % ((s.state or {}).get("turn"),
                                                                                                 start))
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
        self.assertIn("bridge: loop guard: AI 1 (Opp) said yes to", log)
        self.assertNotIn("OutOfMemoryError", log)


if __name__ == "__main__":
    unittest.main()
