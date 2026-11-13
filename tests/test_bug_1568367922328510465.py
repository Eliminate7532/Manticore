# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1568367922328510465 (6 Nov 2026): the AI cast Starving Revenant at 2 life, kept both surveilled cards on top and lost
6 life ("for each card you put on top of your library, you draw a card and you lose 3 life"). The bridge's SurveilGuard moves
cards to the graveyard until that loss no longer kills the AI.

  SourceTests      the guard is in the AI controller and in the jar.
  LiveTests (live) the real engine and AI: the AI at 2 life casts Starving Revenant with two castable spells on top of its
                   library - it must survive; at 20 life the guard leaves Forge's choice alone.
"""

import os
import tempfile
import time
import unittest
import zipfile

import forge_client as fc
import tests.live as live

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "java_bridge", "src", "forge", "bridge")


class SourceTests(unittest.TestCase):
    def test_the_ai_controller_passes_its_surveil_through_the_guard(self):
        with open(os.path.join(SRC, "LoopGuard.java"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("SurveilGuard.limit(getPlayer(), super.arrangeForSurveil(topN))", text)

    def test_the_guard_only_ever_moves_cards_to_the_graveyard(self):
        with open(os.path.join(SRC, "SurveilGuard.java"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("loss(life, top) >= p.getLife()", text)
        self.assertIn("grave.add(c)", text)
        self.assertNotIn("top.add(", text)

    def test_the_jar_has_the_guard(self):
        with zipfile.ZipFile(os.path.join(HERE, "java_bridge", "forge_bridge.jar")) as z:
            self.assertIn("forge/bridge/SurveilGuard.class", z.namelist())


PROBLEM = live.live_problem()


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveTests(unittest.TestCase):
    def play(self, ai_life, seconds=120):
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
        s.setup(["activeplayer=p1", "activephase=MAIN1", "turn=6", "p0life=40", "p1life=%d" % ai_life,
                 "p0battlefield=Island", "p0library=Island;Island;Island;Island;Island",
                 "p1hand=Starving Revenant",
                 "p1battlefield=Swamp;Swamp;Swamp;Swamp;Swamp;Swamp",
                 "p1library=Duress;Duress;Duress;Duress;Duress;Duress"])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        self.assertGreater(s.setups_done, before, "the set-up never finished")
        seen, surveilled = 0, False
        end = time.time() + seconds
        while time.time() < end and not s.game_over and not s.exited:
            s.poll()
            st = s.state or {}
            if s.log_total > seen:
                for e in s.log[max(0, len(s.log) - (s.log_total - seen)):]:
                    if "surveiled" in e.get("text", "") or "When Starving Revenant enters" in e.get("text", ""):
                        surveilled = True
                seen = s.log_total
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            elif st.get("prompt") and st["prompt"].get("ok"):
                s.ok()
            if surveilled and st.get("turn", 0) >= 7:
                break
            time.sleep(0.05)
        self.assertTrue(surveilled, "the AI never cast Starving Revenant")
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
        ai = [p for p in (s.state or {}).get("players", []) if p.get("name", "").startswith("AI")]
        return s, log, ai

    def test_at_two_life_the_ai_does_not_kill_itself(self):
        s, log, ai = self.play(2)
        self.assertTrue(ai, "no AI player in the last snapshot")
        self.assertGreater(ai[0].get("life", 0), 0, "the AI lost all its life to Starving Revenant")
        self.assertFalse(ai[0].get("lost"), "the AI lost the game")
        self.assertIn("bridge: surveil guard:", log)

    def test_at_twenty_life_forge_decides_alone(self):
        s, log, ai = self.play(20)
        self.assertNotIn("bridge: surveil guard:", log)


if __name__ == "__main__":
    unittest.main()
