# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AWAY1 (12 Oct 2026, Karl: "Go. Run all and apply." - the work for while he was away).

1. The soak bot never clicks an attacker that isn't there (soak_bot._attackers_move).
   Soak run_20261011_200529, game 271: at turn 27 (question 109) the bot declared three Elf Warrior tokens and pressed OK.
   A trigger order came up; after it the soak loop asked the bot again from the snapshot it already had, still question
   109. The bot's plan was done, so it made a new one from that old snapshot and clicked its first card - the bridge
   dropped the click (Forge had moved on). The rest of that plan stayed in the bot's memory. Two turns later, at
   question 155, the bot carried on with it and clicked token 307, which had died in between: Forge logged "findByView:
   ... not found in any zone" and the game was an engine_error. Now the plan lives for one question (reset with the
   question number, like every other per-question memory since round 28bb), and only a creature on the board that can
   attack now is clicked. tests/fixtures/soak/away1_g271_attacks.json.gz holds the two real snapshots.
2. Ctrl+0 puts the text size back to 100% (the old table's key, table_gui.py; ForgeTable.change_text_scale(0) already did
   it, nothing called it). Card size is left alone.
"""
import copy
import gzip
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_table as ft
import soak_bot
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import frame, key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G271 = os.path.join(ROOT, "tests", "fixtures", "soak", "away1_g271_attacks.json.gz")


def load_g271():
    with gzip.open(G271, "rt", encoding="utf-8") as f:
        return json.load(f)


def my_battlefield(state):
    me = next(p for p in state["players"] if p["id"] == state["me"])
    return me["zones"]["battlefield"]


def can_attack(state):
    """The creatures Forge marks as able to attack in this snapshot (soak_bot's own test: weak or selectable, untapped)."""
    return {c["id"] for c in my_battlefield(state)
            if c.get("isCreature") and not c.get("tapped") and (c.get("weak") or c.get("selectable"))}


class AttackTable:
    """Forge's attack question, as far as the bot can see it: a click on a creature that can attack declares it (Forge
    takes its highlight off and marks it attacking); a click on anything else is what game 271 did wrong; OK ends it."""

    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.clicks, self.bad, self.done = [], [], False

    def apply(self, move):
        if move is None:
            return
        if move[0] == "ok":
            self.done = True
            return
        self.assert_click(move)
        cid = move[1]
        self.clicks.append(cid)
        card = next((c for c in my_battlefield(self.state) if c["id"] == cid), None)
        if card is None or cid not in can_attack(self.state):
            self.bad.append(cid)
            return
        card["weak"] = None
        card["attacking"] = True

    @staticmethod
    def assert_click(move):
        assert move[0] == "click", move


def play_question(bot, table, mem, limit=40):
    for _ in range(limit):
        if table.done:
            return
        table.apply(bot.next_action(table.state, [], mem))
    raise AssertionError("the bot never pressed OK")


class AttackPlanTests(unittest.TestCase):
    def setUp(self):
        self.g = load_g271()

    def test_the_recorded_snapshots_are_game_271s(self):
        a, b = self.g["turn27_q109"], self.g["turn29_q155"]
        self.assertEqual((a["turn"], a["inputSeq"], a["input"]), (27, 109, "InputAttack"))
        self.assertEqual((b["turn"], b["inputSeq"], b["input"]), (29, 155, "InputAttack"))
        ids = {c["id"] for c in my_battlefield(b)}
        self.assertIn(307, can_attack(a))
        self.assertNotIn(307, ids, "token 307 died between the two questions")

    def test_game_271_never_clicks_a_card_that_is_gone(self):
        """The real sequence, for 200 seeds: declare at question 109, OK, be asked again from that same old snapshot (the
        click that makes is dropped by the bridge), then the attack question two turns later."""
        a, b = self.g["turn27_q109"], self.g["turn29_q155"]
        for seed in range(200):
            bot, mem = soak_bot.SoakBot(seed), {}
            first = AttackTable(a)
            play_question(bot, first, mem)
            stale = bot.next_action(first.state, [], mem)  # the re-ask from the out-of-date snapshot
            later = AttackTable(b)
            play_question(bot, later, mem)
            with self.subTest(seed=seed, stale=stale, clicks=later.clicks):
                self.assertEqual(later.bad, [], "clicked a card that can't attack at question 155")
                self.assertEqual(len(later.clicks), len(set(later.clicks)), "a creature clicked twice (that takes it back)")

    def test_an_unfinished_plan_is_not_carried_into_the_next_question(self):
        """A plan cut short in one question (here: one click, then a new question number) is dropped: in the later question
        the creature it started with can be declared again. With the plan kept, it never was (0 of 60 seeds)."""
        a = self.g["turn27_q109"]
        redeclared = 0
        for seed in range(60):
            bot, mem = soak_bot.SoakBot(seed), {}
            move = bot.next_action(a, [], mem)
            if not move or move[0] != "click":
                continue
            again = copy.deepcopy(a)
            again["inputSeq"] = 555
            table = AttackTable(again)
            play_question(bot, table, mem)
            self.assertEqual(table.bad, [])
            if move[1] in table.clicks:
                redeclared += 1
        self.assertGreater(redeclared, 0)

    def test_a_planned_attacker_that_leaves_the_board_mid_question_is_skipped(self):
        """Within one question: the bot plans, clicks one, and a planned creature is gone from the next snapshot (same
        question number). It is never clicked; the rest of the plan and OK still come."""
        a = self.g["turn27_q109"]
        tried = 0
        for seed in range(60):
            bot, mem = soak_bot.SoakBot(seed), {}
            move = bot.next_action(a, [], mem)
            if not move or move[0] != "click":
                continue
            plan = set(mem["soak_attackers_chosen"]) - {move[1]}
            if not plan:
                continue
            tried += 1
            gone = min(plan)
            table = AttackTable(a)
            table.apply(move)
            me = next(p for p in table.state["players"] if p["id"] == table.state["me"])
            me["zones"]["battlefield"] = [c for c in me["zones"]["battlefield"] if c["id"] != gone]
            play_question(bot, table, mem)
            with self.subTest(seed=seed, gone=gone, clicks=table.clicks):
                self.assertNotIn(gone, table.clicks)
                self.assertEqual(table.bad, [])
                self.assertTrue(table.done)
        self.assertGreater(tried, 5)


class TextSizeResetTests(TempDecks):
    def table(self, settings=None):
        gui = ft.ForgeTable(FakeSession(load_state("main1_lands"), load_log()), StubStore(), settings_path=settings,
                            window_size=(1360, 840))
        gui.animations = False
        frame(gui, 2)
        return gui

    def test_ctrl_0_puts_the_text_back_to_100_and_leaves_the_card_size(self):
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"text_scale": 1.75, "card_scale": 1.5}, f)
        gui = self.table(settings=path)
        self.assertEqual((gui.text_scale, gui.card_scale), (1.75, 1.5))
        key(gui, pygame.K_0, mod=pygame.KMOD_CTRL)
        self.assertEqual((gui.text_scale, gui.card_scale), (1.0, 1.5))
        self.assertIn("Text size 100%", gui.toast[0])
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["text_scale"], saved["card_scale"]), (1.0, 1.5))
        self.assertEqual(gui.session.sent, [])                                   # nothing goes to Forge

    def test_the_keypad_0_with_ctrl_too_and_plain_0_does_nothing(self):
        gui = self.table()
        gui.text_scale = 1.5
        key(gui, pygame.K_0)
        self.assertEqual(gui.text_scale, 1.5)
        key(gui, pygame.K_KP0, mod=pygame.KMOD_LCTRL)
        self.assertEqual(gui.text_scale, 1.0)
        key(gui, pygame.K_0, mod=pygame.KMOD_CTRL)                              # already 100%: stays, says so
        self.assertEqual(gui.text_scale, 1.0)
        self.assertIn("100%", gui.toast[0])

    def test_on_the_deck_screen_it_does_not_type_into_the_search(self):
        gui = self.make_gui(scale=1.25)
        key(gui, pygame.K_l, unicode="l")
        self.assertEqual(gui.menu.search, "l")
        key(gui, pygame.K_0, mod=pygame.KMOD_CTRL, unicode="0")
        self.assertEqual(gui.menu.search, "l")
        self.assertEqual(gui.text_scale, 1.0)

    def test_help_readme_and_start_here_name_it(self):
        self.assertTrue(any("Ctrl+0 puts the text back to 100%" in t for _k, t in ft.HELP_LINES))
        for name in ("README.txt", "START_HERE.txt"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                self.assertIn("Ctrl+0", f.read(), name)


if __name__ == "__main__":
    unittest.main()
