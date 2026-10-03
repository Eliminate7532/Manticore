# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28bc: what soak night 2 found (28 Sept, run_20260927_220536: 261 games, VALID, 5 stalls, 2 Forge-internal errors).

  PickCardsTests        Frantic Search "Discard 2": the bot picked a card, then clicked it again (un-picking it)
  ForgeInternalTests    an exception inside Forge's own code in a game that still finished is a warning, not a failure
  AiStallTests          an AI thinking a long time is its own finding, with a longer limit than my seat being stuck
"""
import argparse
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

FIX = os.path.join(BASE, "tests", "fixtures", "soak")


class PickCardsTests(unittest.TestCase):
    """The real question from night 2's game 31, played against a small stand-in for Forge: a click picks a card
    (highlight on, the text says one fewer), a second click on it un-picks it; OK only once 2 are picked."""

    def question(self):
        with open(os.path.join(FIX, "night2_game031_frantic_search.json"), encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def forge_answers(state, move):
        cards = [c for p in state["players"] for cs in p["zones"].values() for c in cs if c.get("selectable")]
        if move[0] == "click":
            c = next(c for c in cards if c["id"] == move[1])
            c["highlight"] = not c.get("highlight")
        picked = sum(1 for c in cards if c.get("highlight"))
        state["prompt"]["message"] = "Frantic Search (32)\n -  \nDiscard %d card(s)" % (2 - picked)
        state["prompt"]["ok"]["enabled"] = picked == 2
        return picked

    def play(self, seed):
        import soak_bot
        bot, mem, state = soak_bot.SoakBot(seed), {}, self.question()
        for _ in range(12):
            move = bot.next_action(state, [], mem)
            self.assertIsNotNone(move, "the bot had nothing to do")
            if move == ("ok",):
                return self.forge_answers(state, move)
            self.forge_answers(state, move)
        self.fail("no OK after 12 moves - the bot went round the cards")

    def test_two_cards_picked_then_ok(self):
        for seed in range(20):
            self.assertEqual(self.play(seed), 2, "seed %d" % seed)

    def test_an_already_picked_card_is_never_clicked(self):
        import soak_bot
        state = self.question()
        first = next(c for p in state["players"] for c in p["zones"]["hand"] if c.get("selectable"))
        first["highlight"] = True
        move = soak_bot.SoakBot(1).next_action(state, [], {})
        self.assertEqual(move[0], "click")
        self.assertNotEqual(move[1], first["id"])

    def test_any_number_is_not_always_all(self):
        """"Choose any number": a random count, not every card (all lands sacrificed would end games oddly)."""
        import soak_bot
        counts = set()
        for seed in range(30):
            state = self.question()
            state["prompt"]["selMin"], state["prompt"]["selMax"] = 0, 5
            state["prompt"]["ok"]["enabled"] = True
            bot, mem, n = soak_bot.SoakBot(seed), {}, 0
            while True:
                move = bot.next_action(state, [], mem)
                if move == ("ok",):
                    break
                self.forge_answers(state, move)
                state["prompt"]["ok"]["enabled"] = True
                n += 1
            counts.add(n)
        self.assertGreater(len(counts), 2, counts)


class ForgeInternalTests(unittest.TestCase):
    def lines(self, game):
        with open(os.path.join(FIX, "night2_game%s_engine.log" % game), encoding="utf-8") as f:
            return f.read().splitlines()

    def rules(self, messages, lines):
        import bridge_rules as br
        return [(f["rule"], f["severity"]) for f in br.check_stream(messages, lines)]

    def test_a_forge_exception_in_a_finished_game_is_a_warning(self):
        for game in ("057", "115"):          # ConcurrentModificationException in Tracker.unfreeze; StackOverflowError in the AI
            found = self.rules([{"t": "game_over"}], self.lines(game))
            self.assertTrue(found, game)
            self.assertEqual({r for r in found}, {("forge_internal_error", "warn")}, game)

    def test_the_same_exception_in_a_game_that_did_not_finish_is_a_failure(self):
        found = self.rules([], self.lines("057"))
        self.assertIn(("engine_error", "fail"), found)

    def test_a_caused_by_line_is_part_of_the_exception_above_it(self):
        self.assertEqual(len(self.rules([{"t": "game_over"}], self.lines("115"))), 1)

    def test_a_bridge_frame_keeps_it_a_failure(self):
        lines = ["java.lang.ArrayIndexOutOfBoundsException: Index 3 out of bounds for length 3",
                 "\tat forge.gamemodes.match.AbstractGuiGame.getWeakSelectableStrength(AbstractGuiGame.java:405)",
                 "\tat forge.bridge.Snapshot.card(Snapshot.java:127)"]
        self.assertEqual(self.rules([{"t": "game_over"}], lines), [("engine_error", "fail")])

    def test_the_trophy_crash_stays_a_failure(self):
        """Round 28bb's win freeze: no bridge frame, but the game never reached game over."""
        lines = ["EDT > java.lang.NullPointerException: Can't find an image for FSkinProp IMG_COMMON_TROPHY",
                 "\tat forge.toolbox.FSkin.getImage(FSkin.java:503)",
                 "\tat forge.GuiDesktop.createLayeredImage(GuiDesktop.java:150)"]
        self.assertEqual(self.rules([], lines), [("engine_error", "fail")])


def _write_ai_thinking_bridge():
    """Forge asks me nothing ("Waiting for AI 1 (opp1)...", asking false) and then goes quiet."""
    fd, path = tempfile.mkstemp(suffix="_bridge_ai_thinking.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "out(t='ready', protocol=2)\n"
            "out(t='state', turn=35, phase='COMBAT_DECLARE_ATTACKERS', me=0, activePlayer=1, input='', asking=False, inputSeq=716,\n"
            "    players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "             {'id': 1, 'name': 'AI 1 (opp1)', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Waiting for AI 1 (opp1)...', 'ok': {'label': '', 'enabled': False},\n"
            "            'cancel': {'label': '', 'enabled': False}})\n"
            "for line in sys.stdin:\n"
            "    if json.loads(line)['c'] == 'quit':\n"
            "        break\n"
        )
    return path


class AiStallTests(unittest.TestCase):
    def run_game(self, stall, ai_stall, timeout):
        import soak
        bridge = _write_ai_thinking_bridge()
        self.addCleanup(os.remove, bridge)
        old = soak.STALL_SECONDS, soak.AI_STALL_SECONDS
        soak.STALL_SECONDS, soak.AI_STALL_SECONDS = stall, ai_stall
        try:
            with tempfile.TemporaryDirectory() as out:
                args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                          turn_cap=60, game_timeout=timeout, session_command=[sys.executable, bridge],
                                          canary=False)
                soak.run(args)
                with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                    return f.read()
        finally:
            soak.STALL_SECONDS, soak.AI_STALL_SECONDS = old

    def test_an_ai_thinking_is_its_own_finding(self):
        text = self.run_game(0.5, 1.5, 10.0)
        self.assertIn("ai_stall", text)
        self.assertIn("Waiting for AI", text)

    def test_an_ai_gets_longer_than_my_seat(self):
        """Past STALL_SECONDS but not AI_STALL_SECONDS: not a stall yet (here the game's time cap ends it)."""
        text = self.run_game(0.5, 30.0, 3.0)
        self.assertNotIn("stall", text.split("GAMES", 1)[1])
        self.assertIn("ended=time_cap", text)


if __name__ == "__main__":
    unittest.main()
