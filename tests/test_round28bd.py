# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28bd: what soak night 3 found (28 Sept, run_20260928_081136: 230 games, VALID, 2 failing games).

Both failures were one bug: a Cancel sent while Forge was still tapping a mana source for the payment. Forge activates the
mana ability on a second thread; the Cancel restarted the game loop beside it, both walked Forge's static effects, and a
ConcurrentModificationException stopped the game (games 132 and 151). The soak then reported "an AI thought for over 300s",
because Forge was asking nothing - but nobody was thinking; the game thread had died.

  BusyTests         the bridge drops a click that answers the question while a mana ability is being activated (live)
  GameThreadTests   a stall after a game-thread exception is reported as game_thread_died, not ai_stall
"""
import argparse
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import tests.live as live

KINNAN = os.path.join(BASE, "sample_decks", "kinnan_nbc_moxfield_export.txt")


class BusySourceTests(unittest.TestCase):
    def test_main_checks_the_mana_ability_flag_before_answering(self):
        with open(os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "Main.java"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("gui.activatingManaAbility()", src)
        self.assertIn('"busy"', src)
        with open(os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "BridgeGui.java"), encoding="utf-8") as f:
            self.assertIn("isActivatingManaAbility()", f.read())


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class BusyLiveTests(unittest.TestCase):
    def test_cancel_while_a_mana_ability_resolves_does_not_break_the_game(self):
        """Treasure Cruise, paid with Shivan Reef (two mana abilities, so Forge asks which): answer, then Cancel at once -
        exactly what the soak bot did in night 3. Before 28bd: a ConcurrentModificationException in about 1 run in 3
        here (the soak's two games died of it). Now the Cancel is dropped as "busy" while the land is being tapped."""
        import card_check as cc
        chk = cc.Checker(KINNAN)
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=40",
                 "humanbattlefield=Shivan Reef;Shivan Reef;Shivan Reef;Shivan Reef;Shivan Reef",
                 "humanhand=Treasure Cruise", "humangraveyard=Island;Island;Island"])

        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                s.poll()
                time.sleep(0.05)

        pump(3)
        cruise = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Treasure Cruise")
        s.click_card(cruise["id"])
        end = time.time() + 20
        while time.time() < end and s.state.get("input") != "InputPayManaOfCostPayment":
            pump(0.5)
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))          # delve: any answer
        self.assertEqual(s.state.get("input"), "InputPayManaOfCostPayment")
        reef = next(c for c in s.me()["zones"]["battlefield"] if c["name"] == "Shivan Reef" and not c.get("tapped"))
        s.click_card(reef["id"])
        end = time.time() + 10
        while time.time() < end and not s.requests:
            s.poll()
            time.sleep(0.005)
        self.assertTrue(s.requests, "Shivan Reef should ask which mana ability")
        s.answer(s.requests[0], [1])
        s.cancel()                                                  # at once, as the bot did
        pump(6)
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertNotIn("ConcurrentModificationException", f.read())
        self.assertIs(s.state.get("asking"), True, "Forge should still be asking something - the game is alive")


def _write_dead_game_bridge():
    """Forge asks nothing and never will: its game thread printed an uncaught exception to the engine log."""
    fd, path = tempfile.mkstemp(suffix="_bridge_dead.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "out(t='ready', protocol=2)\n"
            "out(t='state', turn=31, phase='MAIN1', me=0, activePlayer=0, input='', asking=False, inputSeq=358,\n"
            "    players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "             {'id': 1, 'name': 'AI 1 (opp1)', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Treasure Cruise (75) - Soak draws three cards.\\n\\nPay Mana Cost: {4}',\n"
            "            'ok': {'label': '', 'enabled': False}, 'cancel': {'label': '', 'enabled': False}})\n"
            "sys.stderr.write('Game-0 > java.util.ConcurrentModificationException\\n')\n"
            "sys.stderr.write('\\tat forge.game.StaticEffects.clearStaticEffects(StaticEffects.java:46)\\n')\n"
            "sys.stderr.flush()\n"
            "for line in sys.stdin:\n"
            "    if json.loads(line)['c'] == 'quit':\n"
            "        break\n"
        )
    return path


class GameThreadTests(unittest.TestCase):
    def test_a_dead_game_thread_is_not_called_a_slow_ai(self):
        import soak
        bridge = _write_dead_game_bridge()
        self.addCleanup(os.remove, bridge)
        old = soak.STALL_SECONDS, soak.AI_STALL_SECONDS
        soak.STALL_SECONDS, soak.AI_STALL_SECONDS = 0.5, 1.0
        try:
            with tempfile.TemporaryDirectory() as out:
                args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                          turn_cap=60, game_timeout=10.0, session_command=[sys.executable, bridge],
                                          canary=False)
                soak.run(args)
                with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                    text = f.read()
        finally:
            soak.STALL_SECONDS, soak.AI_STALL_SECONDS = old
        self.assertIn("game_thread_died", text)
        self.assertIn("ConcurrentModificationException", text)
        self.assertNotIn("ai_stall", text)


if __name__ == "__main__":
    unittest.main()
