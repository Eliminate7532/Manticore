# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 29a: the bridge exits as soon as the program that started it is gone (alpha must-have 1, gap 4).

  SourceTests          Main reads --parent-pid and starts ParentWatch before Forge is initialised; the jar has the class
  CommandLineTests     forge_client always passes its own process id
  LiveKillTests (live) a parent process is killed 3 s into Forge's start-up, and 40 s in: java.exe is gone within 5 s.
                       Before 29a the bridge only noticed once Forge reached its command loop (10-20 s on Karl's PC)
  Soak night 10 (the first night on 0.28.8):
  LoopGuardLogTests    the loop guard's own engine-log line is not an engine_error (games 160 and 240 were failed for it)
  BlockCountTests      "Agent Venom (401) cannot be blocked with 1 creatures you've assigned" (menace): the bot adds a free
                       creature to that attacker, or takes its blockers off and moves on (game 83 stalled 2 minutes on OK)
  LiveMenaceTests (live) the same refusal from real Forge, answered by the bot, and the game gets past blockers
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tests.live as live
import forge_client as fc

JAVA = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


def pid_alive(pid):
    """True while the process exists (Windows: OpenProcess + GetExitCodeProcess; elsewhere: signal 0)."""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259                           # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                                       # a zombie (killed, not yet reaped) counts as gone
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(") ")[-1][:1] != "Z"
    except OSError:
        return True


class SourceTests(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(JAVA, name), encoding="utf-8") as f:
            return f.read()

    def test_main_starts_the_watch_before_forge(self):
        main = self.read("Main.java")
        self.assertIn('case "--parent-pid": parentPid = Long.parseLong(args[++i]);', main)
        self.assertLess(main.index("ParentWatch.start(parentPid)"), main.index("FModel.initialize"))

    def test_the_watch_checks_every_second_and_halts(self):
        src = self.read("ParentWatch.java")
        self.assertIn("ProcessHandle.of(pid)", src)
        self.assertIn("INTERVAL_MS = 1000", src)
        self.assertIn("Runtime.getRuntime().halt(0)", src)
        self.assertIn("t.setDaemon(true)", src)

    def test_the_jar_has_it(self):
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            self.assertIn("forge/bridge/ParentWatch.class", z.namelist())


class CommandLineTests(unittest.TestCase):
    def test_forge_client_passes_its_own_pid(self):
        s = fc.ForgeSession("me.dck", ["opp.dck"], seed=1)
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
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--parent-pid") + 1], str(os.getpid()))


PROBLEM = live.live_problem()

CHILD = r'''
import json, os, sys, time
sys.path.insert(0, {base!r})
import forge_client as fc
fc.sync_bridge()
s = fc.ForgeSession({mine!r}, [{opp!r}], name="Me", seed=3)
s.stderr_path = {log!r}
s.start()
print(json.dumps({{"java": s.proc.pid}}), flush=True)
time.sleep(600)
'''


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveKillTests(unittest.TestCase):
    def kill_after(self, seconds):
        tmp = tempfile.mkdtemp()
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
        log = os.path.join(tmp, "engine.log")
        code = CHILD.format(base=BASE, mine=mine, opp=opp, log=log)
        parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        java = json.loads(parent.stdout.readline())["java"]
        try:
            time.sleep(seconds)
            parent.kill()                                     # TerminateProcess on Windows, SIGKILL elsewhere: no clean-up runs
            parent.wait()
            parent.stdout.close()
            t0 = time.time()
            while time.time() - t0 < 5 and pid_alive(java):
                time.sleep(0.2)
            gone_after = time.time() - t0
            self.assertFalse(pid_alive(java), "java.exe was still running 5 s after its parent was killed")
            return gone_after
        finally:
            if pid_alive(java):
                try:
                    os.kill(java, 9)
                except OSError:
                    pass

    def test_killed_during_forges_start_up(self):
        self.kill_after(3)

    def test_killed_mid_game(self):
        self.kill_after(40)


import bridge_rules as br
from soak_bot import SoakBot

GUARD_LINE = ("bridge: loop guard: AI 3 (opp3) used Carrion Feeder (Sacrifice a creature: Put a +1/+1 counter on CARDNAME.) "
              "10 times this turn; passing instead")


class LoopGuardLogTests(unittest.TestCase):
    def test_the_guard_line_is_not_an_engine_error(self):
        self.assertEqual(br.check_stream([{"t": "game_over"}], [GUARD_LINE]), [])
        self.assertEqual(br.check_stream([{"t": "state", "turn": 60}], [GUARD_LINE]), [])     # unfinished game too

    def test_the_guard_still_shows_as_a_warning_from_its_message(self):
        msgs = [{"t": "ai_loop_guard", "player": "AI 3 (opp3)", "card": "Carrion Feeder", "count": 10}, {"t": "game_over"}]
        found = br.check_stream(msgs, [GUARD_LINE])
        self.assertEqual([(f["rule"], f["severity"]) for f in found], [("ai_loop_guard", br.WARN)])

    def test_other_bridge_lines_still_fail(self):
        found = br.check_stream([{"t": "game_over"}], ["bridge: unknown command frobnicate"])
        self.assertEqual([f["rule"] for f in found], ["engine_error"])


def block_state(esper_blocking=True, spirit_free=True):
    """Night 10 game 83, cut down: AI 1 attacks with Agent Venom (menace, 401), Teysa (402) and Priest (308); my Esper
    Sentinel was put on Agent Venom alone and OK was refused."""
    mine = [{"id": 208, "name": "Esper Sentinel", "isCreature": True, "tapped": False, "blocking": esper_blocking},
            {"id": 207, "name": "Selfless Spirit", "isCreature": True, "tapped": False, "blocking": not spirit_free},
            {"id": 294, "name": "Nykthos, Shrine to Nyx", "isCreature": False, "tapped": False}]
    theirs = [{"id": 401, "name": "Agent Venom", "isCreature": True, "attacking": True, "tapped": True, "keywords": ["Menace"]},
              {"id": 402, "name": "Teysa Karlov", "isCreature": True, "attacking": True, "tapped": True},
              {"id": 308, "name": "Priest of the Crossing", "isCreature": True, "attacking": True, "tapped": True}]
    return {"t": "state", "turn": 11, "phase": "COMBAT_DECLARE_BLOCKERS", "activePlayer": 1, "me": 0, "input": "InputBlock",
            "inputSeq": 143, "asking": True,
            "combat": [{"card": 401, "defender": "p0", "blockers": []}, {"card": 402, "defender": "p0", "blockers": []},
                       {"card": 308, "defender": "p0", "blockers": []}],
            "prompt": {"message": "Select creatures to block Agent Venom (401) or select another attacker to declare blockers for.",
                       "ok": {"label": "OK", "enabled": True}, "cancel": {"label": "Cancel", "enabled": False}, "selecting": False},
            "players": [{"id": 0, "name": "Soak", "zones": {"battlefield": mine, "hand": []}},
                        {"id": 1, "name": "AI 1 (opp1)", "zones": {"battlefield": theirs, "hand": []}}]}


REFUSAL = {"t": "message", "title": "Forge", "text": "Agent Venom (401) cannot be blocked with 1 creatures you've assigned", "_seq": 143}


class BlockCountTests(unittest.TestCase):
    def moves(self, state, mem, n):
        bot = SoakBot(seed=3)
        return [bot._required_block_move(state, state["players"][0], state["prompt"], mem) for _ in range(n)]

    def test_a_free_creature_joins_the_menace_attacker_then_ok_is_left_to_the_usual_path(self):
        mem = {"infos": [REFUSAL]}
        self.assertEqual(self.moves(block_state(), mem, 3), [("click", 401), ("click", 207), None])

    def test_refused_again_the_blockers_come_off_and_another_attacker_is_chosen(self):
        mem = {"infos": [REFUSAL]}
        self.moves(block_state(), mem, 3)
        mem["infos"].append(dict(REFUSAL, text="Agent Venom (401) cannot be blocked with 2 creatures you've assigned"))
        st = block_state(spirit_free=False)
        got = self.moves(st, mem, 4)
        self.assertEqual(got[:3], [("click", 401), ("click", 208), ("click", 207)])
        self.assertIn(got[3], [("click", 402), ("click", 308)])

    def test_nothing_free_goes_straight_to_taking_them_off(self):
        mem = {"infos": [REFUSAL]}
        got = self.moves(block_state(spirit_free=False), mem, 2)
        self.assertEqual(got[:2], [("click", 401), ("click", 208)])

    def test_an_old_refusal_from_another_question_is_ignored(self):
        mem = {"infos": [dict(REFUSAL, _seq=99)]}
        self.assertEqual(self.moves(block_state(), mem, 1), [None])

    def test_next_action_reaches_ok_after_the_fix(self):
        bot = SoakBot(seed=3)
        mem = {"infos": [REFUSAL]}
        st = block_state()
        acts = [bot.next_action(st, [], mem) for _ in range(3)]
        self.assertEqual(acts[:2], [("click", 401), ("click", 207)])
        self.assertEqual(acts[2][0], "ok")


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveMenaceTests(unittest.TestCase):
    def test_the_bot_fixes_a_refused_menace_block(self):
        import card_check as cc
        tmp = tempfile.mkdtemp()
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Teysa Karlov"], ["Plains"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
        fc.sync_bridge()
        s = fc.ForgeSession(mine, [opp], name="Soak", seed=5, dev=True)
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
        s.setup(["activeplayer=p1", "activephase=MAIN1", "turn=6", "p0life=40", "p1life=40",
                 "p0battlefield=Esper Sentinel;Selfless Spirit;Alseid of Life's Bounty;Plains",
                 "p0library=Plains;Plains;Plains;Plains;Plains", "p1battlefield=Agent Venom;Forest",
                 "p1library=Forest;Forest;Forest;Forest;Forest", "removesummoningsickness=true"])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        bot, bmem, seen, forced, refused, past = SoakBot(seed=1), {"infos": []}, 0, False, False, False
        end = time.time() + 120
        while time.time() < end and not s.game_over and not past:
            s.poll()
            st = s.state or {}
            while len(s.infos) > seen:
                info = dict(s.infos[seen])
                info["_seq"] = st.get("inputSeq")
                bmem["infos"].append(info)
                refused = refused or "cannot be blocked with 1 creatures" in (info.get("text") or "")
                seen += 1
            if st.get("input") == "InputBlock" and not forced:
                venom = next(c for p in st["players"] for c in p["zones"]["battlefield"] if c["name"] == "Agent Venom")
                esper = next(c for c in s.me()["zones"]["battlefield"] if c["name"] == "Esper Sentinel")
                s.click_card(venom["id"])
                time.sleep(0.6)
                s.poll()
                s.click_card(esper["id"])
                time.sleep(0.6)
                s.poll()
                s.ok()                                          # one blocker on a menace attacker: Forge refuses
                time.sleep(1.5)
                forced = True
                continue
            if forced:
                if st.get("input") != "InputBlock" and st.get("phase") != "COMBAT_DECLARE_BLOCKERS":
                    past = True
                    break
                mv = bot.next_action(st, list(s.requests), bmem)
                if mv and mv[0] == "click":
                    s.click_card(mv[1])
                elif mv and mv[0] == "ok":
                    s.ok()
                elif mv and mv[0] == "answer":
                    s.answer(mv[1], mv[2])
                time.sleep(1.0)
            elif (st.get("prompt") or {}).get("ok", {}).get("enabled"):
                s.ok()
                time.sleep(0.5)
            time.sleep(0.1)
        self.assertTrue(forced, "the AI never attacked")
        self.assertTrue(refused, "Forge did not refuse the one-blocker block")
        self.assertTrue(past, "the bot did not get past the refused block")


if __name__ == "__main__":
    unittest.main()
