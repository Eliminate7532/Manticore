# SPDX-License-Identifier: GPL-3.0-or-later
"""Round OUT1 (23 Oct 2026, found by the cloud online soak of release-0.28.53.12, game 18): a friend who concedes in an online
game of 3-4 players while Forge is asking them something froze the game for everyone.

Forge's PlayerControllerHuman.concede() releases the questions waiting only when the concede ENDS the game (InputQueue.onGameOver;
the same at fb4d809 and at master 9a6f776). In a game that goes on, the conceding player's open question stays open, and patch 44
drops every click from a seat whose player has lost ("out") - so nobody could answer it: the host's table said "Waiting for Sam..."
until the soak's 120 s stall limit. The bridge now releases a question of a player who is out, the one open at the concede and any
asked later.

  SourceTests  the release is in BridgeGui's input observer and in Main.concedeWhenSafe, after the concede
  LiveTests    real Forge: host + friend + AI; the friend concedes at their own Main 1 priority (asked, so the concede runs at once)
               and then goes quiet, as a table that has left; the game must go on to another player's turn
"""
import os
import sys
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
import tests.live as live

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_a_question_of_a_player_who_is_out_is_released(self):
        gui = read("BridgeGui.java")
        self.assertIn("releaseIfOut(h);", gui)
        body = gui[gui.index("static void releaseIfOut("):]
        body = body[:body.index("\n    }\n")]
        self.assertIn("hasLost()", body)
        self.assertIn(".stop()", body)
        main = read("Main.java")
        concede = main[main.index("static void concedeWhenSafe("):]
        concede = concede[:concede.index("\n    }\n")]
        after = concede[concede.index("gc.concede();"):]
        self.assertIn("BridgeGui.releaseIfOut(", after)


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_mp2c import LiveTests as MP2c
        MP2c.setUpClass()
        cls.mp2c = MP2c
        cls.helper = MP2c("test_a_friend_and_an_ai_player")

    @classmethod
    def tearDownClass(cls):
        cls.mp2c.tearDownClass()

    def test_a_friend_who_concedes_while_asked_does_not_freeze_a_pod(self):
        hp = self.helper
        hp.addCleanup = self.addCleanup
        h, port = hp.start_host(guests=1, ai_paths=[hp.decks["Teysa"]])
        g = hp.guest(port, "Sam", "Veyran")
        self.assertTrue(hp.play_pregame([h, g], lambda: (g.state or {}).get("turn", 0) >= 1 and (h.state or {}).get("turn", 0) >= 1),
                        "the game never started")

        def answer(s):
            for r in list(s.requests):
                s.answer(r, True if r["kind"] == "confirm" else list(range(max(r.get("min", 1), 1))))
            st = s.state or {}
            pr = st.get("prompt") or {}
            ok = pr.get("ok", {})
            if st.get("asking") and ok.get("enabled"):
                s.ok()
            elif st.get("asking") and pr.get("selecting") and s.me():          # the cleanup discard: OK greyed until a pick
                picks = [c for c in s.me()["zones"].get("hand", []) if c.get("selectable")]
                if picks and getattr(s, "_picked", None) != s.state_version:
                    s._picked = s.state_version
                    s.click_card(picks[0]["id"])

        conceded_turn, end = None, time.time() + 240
        while time.time() < end and conceded_turn is None:
            for s in (h, g):
                s.poll()
            st = g.state or {}
            if st.get("activePlayer") == st.get("me") and st.get("asking") and st.get("input") == "InputPassPriority":
                conceded_turn = st.get("turn")
                g.concede()
                break
            answer(h)
            answer(g)
            time.sleep(0.15)
        self.assertIsNotNone(conceded_turn, "never reached a priority question of Sam's in Sam's turn")

        def moved_on():
            st = h.state or {}
            sam = next((p for p in st.get("players", []) if p.get("name") == "Sam"), {})
            return sam.get("lost") and (st.get("turn", 0) > conceded_turn or st.get("activePlayer") != sam.get("id"))

        end = time.time() + 60                                  # the guest now sends nothing (a table that has left)
        while time.time() < end and not moved_on():
            h.poll()
            g.poll()
            answer(h)
            time.sleep(0.15)
        st = h.state or {}
        self.assertTrue(moved_on(), "the game stayed on Sam's turn after Sam conceded: turn %s, prompt %r"
                        % (st.get("turn"), (st.get("prompt") or {}).get("message", "")[:80]))
        with open(h.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertIn("gamesync: released a question of Sam, who is out (InputPassPriority)", f.read())


if __name__ == "__main__":
    unittest.main()
