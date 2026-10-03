# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28e: the AI loop guard, and what soak night 9 found.

  LoopRuleTests        the soak fails a game where one ability is activated more than 30 times in a turn (ai_loop): nights 7
                       and 9 had the Kinnan AI untap two Grim Monoliths with each other thousands of times, reported as "no
                       finding"; the bridge's guard stepping in is a warning (ai_loop_guard)
  InterruptedTests     Forge interrupting a timed-out AI thread inside Swing's start-up ("AWT blocker activation interrupted")
                       is a warning, ai_interrupted, not an engine_error (night 9, game 144)
  ClientTests          forge_client keeps the bridge's ai_loop_guard messages, notes the first per card in crash_log.txt,
                       reads loopCap from "ready", and passes --loop-cap only in dev mode
  SourceTests          the bridge wraps every AI opponent in LoopGuard, default limit 10; the jar has the classes
  LiveLoopTests (live) Kinnan + two Grim Monoliths (one tapped) in the other player's end step: with the guard off the AI
                       loops; with it on, the bridge stops it at the limit and the game moves on
"""
import collections
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import tests.live as live
import bridge_rules as br
import forge_client as fc

JAVA = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


def state(turn):
    return {"t": "state", "turn": turn, "phase": "END_OF_TURN", "players": []}


def activations(n, who="AI 1 (opp1)", card="Grim Monolith"):
    out = []
    for k in range(n):
        out.append({"t": "log", "entries": [{"type": "MANA", "text": "%s (145) - {T}: Add {C}{C}{C}." % card},
                                            {"type": "STACK_ADD", "text": "%s activated %s" % (who, card)}]})
        out.append({"t": "_sent", "cmd": {"c": "ok", "at": k}})
        out.append({"t": "log", "entries": [{"type": "STACK_RESOLVE", "text": "%s (151) - Untap %s (151)." % (card, card)}]})
    return out


def rules(findings):
    return [(f["rule"], f["severity"]) for f in findings]


class LoopRuleTests(unittest.TestCase):
    def test_more_than_thirty_in_one_turn_fails(self):
        msgs = [state(9)] + activations(br.AI_LOOP_LIMIT + 1) + [{"t": "game_over"}]
        found = [f for f in br.check_stream(msgs) if f["rule"] == "ai_loop"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], br.FAIL)
        self.assertIn("Grim Monolith", found[0]["detail"])
        self.assertIn("turn 9", found[0]["detail"])
        self.assertTrue(br.has_fail(found))

    def test_thousands_in_one_turn_is_still_one_finding(self):
        msgs = [state(9)] + activations(500)
        self.assertEqual(rules(br.check_stream(msgs)).count(("ai_loop", br.FAIL)), 1)

    def test_thirty_or_spread_over_turns_is_fine(self):
        self.assertNotIn("ai_loop", [f["rule"] for f in br.check_stream([state(3)] + activations(br.AI_LOOP_LIMIT))])
        spread = [state(3)] + activations(20) + [state(4)] + activations(20) + [state(5)] + activations(20)
        self.assertNotIn("ai_loop", [f["rule"] for f in br.check_stream(spread)])

    def test_different_cards_and_players_are_counted_apart(self):
        msgs = [state(3)] + activations(20) + activations(20, card="Basalt Monolith") + activations(20, who="Soak")
        self.assertNotIn("ai_loop", [f["rule"] for f in br.check_stream(msgs)])

    def test_a_spell_cast_is_not_an_activation(self):
        msgs = [state(3)] + [{"t": "log", "entries": [{"type": "STACK_ADD", "text": "AI 1 cast Lightning Bolt"}]}] * 40
        self.assertNotIn("ai_loop", [f["rule"] for f in br.check_stream(msgs)])

    def test_the_guard_stepping_in_is_a_warning(self):
        g = {"t": "ai_loop_guard", "player": "AI 1 (opp1)", "card": "Grim Monolith", "ability": "{4}: Untap this artifact.",
             "turn": 9, "count": 10}
        msgs = [state(9)] + activations(10) + [g, state(10), dict(g, turn=10), {"t": "game_over"}]
        found = [f for f in br.check_stream(msgs) if f["rule"] == "ai_loop_guard"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], br.WARN)
        self.assertIn("2 repeat(s): Grim Monolith", found[0]["detail"])
        self.assertFalse(br.has_fail(br.check_stream(msgs)))


AWT_BLOCK = """AWT blocker activation interrupted:
java.lang.InterruptedException
	at java.base/java.lang.Object.wait0(Native Method)
	at java.base/java.lang.Object.wait(Object.java:366)
	at java.desktop/sun.awt.AWTAutoShutdown.activateBlockerThread(AWTAutoShutdown.java:349)
	at java.desktop/sun.awt.AWTAutoShutdown.notifyThreadBusy(AWTAutoShutdown.java:175)
	at java.desktop/java.awt.EventQueue$6.run(EventQueue.java:1126)
	at forge.GuiDesktop.invokeInEdtLater(GuiDesktop.java:106)
	at forge.gui.control.FControlGameEventHandler.processEvent(FControlGameEventHandler.java:163)""".splitlines()

OTHER_INTERRUPT = """java.lang.InterruptedException
	at java.base/java.lang.Object.wait0(Native Method)
	at forge.bridge.BridgeGui.waitForAnswer(BridgeGui.java:120)""".splitlines()


class InterruptedTests(unittest.TestCase):
    def test_awt_interrupt_is_a_warning_even_in_an_unfinished_game(self):
        found = br.check_stream([state(30)], AWT_BLOCK)                   # night 9 game 144 ended at the time cap: no game_over
        self.assertEqual(rules(found), [("ai_interrupted", br.WARN)])
        self.assertFalse(br.has_fail(found))

    def test_three_of_them_are_three_warnings(self):
        found = br.check_stream([state(30)], AWT_BLOCK * 3)
        self.assertEqual(rules(found), [("ai_interrupted", br.WARN)] * 3)

    def test_any_other_interrupt_still_fails(self):
        found = br.check_stream([state(30)], OTHER_INTERRUPT)
        self.assertEqual(rules(found), [("engine_error", br.FAIL)])


class ClientTests(unittest.TestCase):
    def session(self):
        s = fc.ForgeSession("me.dck", ["opp.dck"])
        return s

    def test_ready_says_the_limit(self):
        s = self.session()
        s.handle({"t": "ready", "protocol": 2, "aiTimeout": 20, "loopCap": 10})
        self.assertEqual((s.ai_timeout, s.loop_cap_used), (20, 10))

    def test_guard_messages_are_kept_and_noted_once_per_card(self):
        s = self.session()
        g = {"t": "ai_loop_guard", "player": "AI 1", "card": "Grim Monolith", "ability": "{4}: Untap this artifact.", "count": 10}
        with mock.patch("crashlog.note") as note:
            s.handle(dict(g, turn=5))
            s.handle(dict(g, turn=6))
            s.handle(dict(g, card="Basalt Monolith", turn=6))
        self.assertEqual(len(s.loop_guards), 3)
        self.assertEqual(note.call_count, 2)
        self.assertEqual(note.call_args_list[0][0][0], "AI loop stopped")
        self.assertIn("Grim Monolith", note.call_args_list[0][0][1])

    def command_line(self, **kw):
        s = fc.ForgeSession("me.dck", ["opp.dck"], seed=1, **kw)
        s.stderr_path = os.path.join(tempfile.mkdtemp(), "engine.log")
        seen = {}

        class FakeProc:
            stdout = iter(())

        def popen(cmd, **_kw):
            seen["cmd"] = cmd
            return FakeProc()

        with mock.patch.object(fc, "runtime_problem", lambda *_a: None), \
                mock.patch.object(fc, "find_java", lambda: "java"), \
                mock.patch.object(fc, "class_path", lambda *_a: "cp"), \
                mock.patch.object(fc.subprocess, "Popen", popen):
            s.start()
        if s._err:
            s._err.close()
        return seen["cmd"]

    def test_loop_cap_is_passed_only_in_dev_mode(self):
        self.assertNotIn("--loop-cap", self.command_line())
        self.assertNotIn("--loop-cap", self.command_line(loop_cap=5))                 # the game itself never changes it
        cmd = self.command_line(dev=True, loop_cap=0)
        self.assertEqual(cmd[cmd.index("--loop-cap") + 1], "0")
        self.assertNotIn("--loop-cap", self.command_line(dev=True))


class SourceTests(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(JAVA, name), encoding="utf-8") as f:
            return f.read()

    def test_every_ai_opponent_is_guarded(self):
        main = self.read("Main.java")
        self.assertIn("LoopGuard.guarded(GamePlayerUtil.createAiPlayer(", main)
        self.assertEqual(main.count("GamePlayerUtil.createAiPlayer("), 1)
        self.assertIn('ready.addProperty("loopCap", LoopGuard.cap)', main)
        self.assertIn('case "--loop-cap"', main)

    def test_the_default_limit_is_ten_and_a_null_choice_passes(self):
        src = self.read("LoopGuard.java")
        self.assertIn("static final int DEFAULT_CAP = 10;", src)
        self.assertIn("return null;                       // pass priority instead", src)
        self.assertIn("isLandAbility()", src)

    def test_the_jar_has_the_guard(self):
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            names = set(z.namelist())
        for cls in ("LoopGuard", "LoopGuard$Lobby", "LoopGuard$Controller"):
            self.assertIn("forge/bridge/%s.class" % cls, names)


PROBLEM = live.live_problem()


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveLoopTests(unittest.TestCase):
    """The real engine, a real AI. Kinnan + Grim Monolith (tapped) + Grim Monolith: each taps for 3 + 1 from Kinnan = 4, the
    other's untap cost. The AI keeps priority in the human's end step and loops (night 9 game 28's pattern)."""

    def play(self, loop_cap, seconds):
        import card_check as cc
        tmp = tempfile.mkdtemp()
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
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
            mv = cc.next_move(st, [], mem) if st else None
            if mv and mv[0] == "ok":
                s.ok()
                time.sleep(0.4)
            time.sleep(0.05)
        before = s.setups_done
        s.setup(["activeplayer=p0", "activephase=END_OF_TURN", "turn=5", "p0life=40", "p1life=40", "p0battlefield=Island",
                 "p0library=Island;Island;Island;Island;Island",
                 "p1battlefield=Kinnan, Bonder Prodigy;Grim Monolith|Tapped;Grim Monolith;Forest",
                 "p1library=Forest;Forest;Forest;Forest;Forest;Forest"])
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
            for e in s.log[max(0, len(s.log) - (s.log_total - seen)):] if s.log_total > seen else []:
                if e.get("type") == "STACK_ADD" and "activated Grim Monolith" in e.get("text", ""):
                    per_turn[st.get("turn")] += 1
            seen = s.log_total
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            elif st.get("prompt") and st["prompt"].get("ok"):
                s.ok()
            if loop_cap != 0 and st.get("turn", 0) >= (start_turn or 0) + 3:
                break
            time.sleep(0.02)
        return s, per_turn, start_turn

    def test_without_the_guard_the_ai_loops(self):
        s, per_turn, start = self.play(0, 45)
        self.assertEqual(s.loop_cap_used, 0)
        self.assertGreater(max(per_turn.values() or [0]), br.AI_LOOP_LIMIT, per_turn)
        self.assertEqual((s.state or {}).get("turn"), start, "the turn should never have ended")
        self.assertEqual(s.loop_guards, [])

    def test_with_the_guard_the_ai_stops_and_the_game_moves_on(self):
        s, per_turn, start = self.play(None, 150)
        self.assertEqual(s.loop_cap_used, 10)
        self.assertTrue(s.loop_guards, "the bridge never said it stopped a loop")
        self.assertEqual(s.loop_guards[0]["card"], "Grim Monolith")
        self.assertEqual(s.loop_guards[0]["count"], 10)
        self.assertGreaterEqual((s.state or {}).get("turn", 0), start + 3, "the game did not move on")
        self.assertLessEqual(max(per_turn.values() or [0]), 2 * 10, per_turn)     # two Monoliths, ten each at most
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertIn("bridge: loop guard: AI 1 (Opp) used Grim Monolith", f.read())


if __name__ == "__main__":
    unittest.main()
