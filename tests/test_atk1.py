# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ATK1 - bug report from Karl, 31 Oct 2026: "Only letting me attack AI 1 during a 4 player pod".

Forge's attack question has a current defender (the first opponent when the question opens). A click on a creature declares it
against that defender; a click on a player only changes the defender for the creatures clicked AFTER it. Karl clicked his
creature first and then the player he meant (his commands in the report: card 59, then players 2, 3, 1, ... all in question 57),
so every attack went to AI 1.

Now, with two or more opponents, a creature clicked before a player has been chosen in this question waits (a "WHO?" tag) and
attacks the player clicked next; once a player is chosen, creatures go straight to them, as before.
"""
import copy
import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import pygame

import forge_table as ft
import tests.live as live
from tests.forge_fake import StubStore, load_state
from tests.test_forge_table import click, frame, key, make_gui, point_for_card

ELVES, ELVES2, TAPPED_ELVES = 25, 23, 22          # my Llanowar Elves in declare_attackers.json (22 is tapped)
QUESTION = 57


def attack_state(opponents=3, lost=(), planeswalker=False):
    """declare_attackers.json made into a pod: the same opponent copied as AI 2 and AI 3 (card ids moved by 1000 each)."""
    st = load_state("declare_attackers")
    st["input"], st["inputSeq"], st["asking"] = "InputAttack", QUESTION, True
    base = st["players"][1]
    players = [st["players"][0]]
    for n in range(1, opponents + 1):
        p = copy.deepcopy(base)
        p["id"] = n
        p["name"] = f"AI {n} (Kinnan)"
        p["lost"] = n in lost
        for zone in p["zones"].values():
            for c in zone if isinstance(zone, list) else []:
                if isinstance(c, dict) and "id" in c:
                    c["id"] += 1000 * (n - 1)
                    c["controller"] = c["owner"] = n
        players.append(p)
    if planeswalker:
        pw = copy.deepcopy(players[2]["zones"]["battlefield"][0])
        pw.update(id=7777, name="Jace Beleren", isPlaneswalker=True, isCreature=False, type="Legendary Planeswalker - Jace",
                  tapped=False)
        players[2]["zones"]["battlefield"].append(pw)
    st["players"] = players
    first = next(p for p in players[1:] if not p["lost"])
    st["prompt"]["message"] = f"Select creatures to attack {first['name']} or select player/card you wish to attack."
    return st


def forge_moved_to(gui, pid):
    """What Forge does after a player click in the attack question: its words name the new defender."""
    st = copy.deepcopy(gui.session.state)
    name = next(p["name"] for p in st["players"] if p["id"] == pid)
    st["prompt"]["message"] = f"Select creatures to attack {name} or select player/card you wish to attack."
    gui.session.handle(st)
    frame(gui)


def panel_point(gui, pid):
    r = gui.panel_rects[pid]
    for dx in range(4, r.w, 6):
        for dy in range(4, r.h, 6):
            p = (r.x + dx, r.y + dy)
            kind, data = gui.hit_at(p)
            if kind == "player" and data["id"] == pid:
                return p
    raise AssertionError(f"player {pid}'s panel can't be clicked")


def sent(gui):
    return [(c["c"], c.get("id")) for c in gui.session.sent]


class PodAttackTests(unittest.TestCase):
    def gui(self, **kw):
        return make_gui(attack_state(**kw), size=kw.pop("size", (1600, 900)) if "size" in kw else (1600, 900))

    def test_a_creature_then_a_player_attacks_that_player(self):
        """Karl's order: the creature, then the player he meant."""
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        self.assertEqual(sent(gui), [])                                  # it waits for a player
        self.assertEqual(gui.attack_pending(), [ELVES])
        click(gui, panel_point(gui, 2))
        self.assertEqual(sent(gui), [("player", 2), ("card", ELVES)])   # Forge's defender first, then the creature
        self.assertEqual(gui.attack_pending(), [])

    def test_two_waiting_creatures_go_to_the_same_player(self):
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        click(gui, point_for_card(gui, ELVES2))
        click(gui, panel_point(gui, 3))
        self.assertEqual(sent(gui), [("player", 3), ("card", ELVES), ("card", ELVES2)])

    def test_player_first_works_as_before_and_a_later_player_moves_nothing(self):
        gui = self.gui()
        click(gui, panel_point(gui, 2))
        forge_moved_to(gui, 2)
        click(gui, point_for_card(gui, ELVES))
        self.assertEqual(sent(gui), [("player", 2), ("card", ELVES)])   # a player is chosen: the creature goes at once
        click(gui, panel_point(gui, 3))
        self.assertEqual(sent(gui), [("player", 2), ("card", ELVES), ("player", 3)])

    def test_ok_with_a_waiting_creature_attacks_forges_current_player(self):
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        gui.press("ok")
        self.assertEqual(sent(gui), [("card", ELVES), ("ok", None)])
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        key(gui, pygame.K_SPACE)
        self.assertEqual(sent(gui), [("card", ELVES), ("ok", None)])

    def test_a_second_click_or_esc_lets_it_go(self):
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        click(gui, point_for_card(gui, ELVES))
        self.assertEqual((gui.attack_pending(), sent(gui)), ([], []))
        click(gui, point_for_card(gui, ELVES))
        key(gui, pygame.K_ESCAPE)
        self.assertEqual((gui.attack_pending(), sent(gui)), ([], []))   # Esc only lets the creature go: no Full Send, no cancel

    def test_full_send_lets_waiting_creatures_go(self):
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        gui.press("cancel")
        self.assertEqual(sent(gui), [("cancel", None)])
        self.assertEqual(gui.attack_pending(), [])

    def test_a_tapped_creature_or_an_opponents_creature_is_not_held(self):
        gui = self.gui()
        click(gui, point_for_card(gui, TAPPED_ELVES))
        click(gui, point_for_card(gui, 155 + 1000))                    # AI 2's (tapped) Kinnan
        self.assertEqual(gui.attack_pending(), [])
        self.assertEqual(sent(gui), [("card", TAPPED_ELVES), ("card", 1155)])

    def test_my_own_panel_does_not_take_the_waiting_creature(self):
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        gui.aim_attack(0)                                               # Forge says no to attacking yourself
        self.assertEqual(sent(gui), [("player", 0)])
        self.assertEqual(gui.attack_pending(), [ELVES])

    def test_an_opponents_planeswalker_takes_the_waiting_creature(self):
        gui = self.gui(planeswalker=True)
        click(gui, point_for_card(gui, ELVES))
        click(gui, point_for_card(gui, 7777))
        self.assertEqual(sent(gui), [("card", 7777), ("card", ELVES)])

    def test_a_new_question_forgets_the_waiting_creature(self):
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        st = copy.deepcopy(gui.session.state)
        st["inputSeq"] = QUESTION + 1
        gui.session.handle(st)
        frame(gui)
        self.assertEqual(gui.attack_pending(), [])
        gui.press("ok")
        self.assertEqual(sent(gui), [("ok", None)])

    def test_one_opponent_left_is_a_plain_attack(self):
        for st in (attack_state(opponents=1), attack_state(opponents=3, lost=(1, 3))):
            gui = make_gui(st, size=(1600, 900))
            click(gui, point_for_card(gui, ELVES))
            self.assertEqual(sent(gui), [("card", ELVES)])
            self.assertFalse(gui.attack_pod())

    def test_the_bar_and_the_card_say_what_is_going_on(self):
        gui = self.gui()
        head, hint = gui.attack_words("x", "y")
        self.assertEqual(head, "Declare attackers  -  choose who to attack")
        self.assertIn("Click a player's panel, then the creatures", hint)
        click(gui, point_for_card(gui, ELVES))
        head, hint = gui.attack_words("x", "y")
        self.assertEqual(head, "Who does Llanowar Elves attack?")
        self.assertIn("Click that player's panel", hint)
        self.assertIn("AI 1", hint)
        drawn = []
        orig = gui.draw_source_badge
        gui.draw_source_badge = lambda rect, radius, text="SOURCE": drawn.append(text) or orig(rect, radius, text)
        frame(gui)
        self.assertIn("WHO?", drawn)
        click(gui, panel_point(gui, 2))
        forge_moved_to(gui, 2)
        self.assertEqual(gui.attack_defender(), 2)
        head, hint = gui.attack_words("Declare attackers  -  attacking AI 2 (Kinnan)", "y")
        self.assertEqual(head, "Declare attackers  -  attacking AI 2 (Kinnan)")
        self.assertIn("attack AI 2", hint)

    def test_it_fits_at_every_size(self):
        for size, scale in (((1100, 700), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0), ((4096, 1949), 1.75)):
            with self.subTest(size=size, scale=scale):
                gui = make_gui(attack_state(), size=size, scale=scale)
                click(gui, point_for_card(gui, ELVES))
                frame(gui)
                self.assertEqual(gui.attack_pending(), [ELVES])
                click(gui, panel_point(gui, 3))
                self.assertEqual(sent(gui), [("player", 3), ("card", ELVES)])

    def test_karls_clicks_from_the_report(self):
        """His clicks in question 57, through the new table: the first creature now attacks the first player he clicked."""
        gui = self.gui()
        click(gui, point_for_card(gui, ELVES))
        for pid in (2, 3, 1):
            click(gui, panel_point(gui, pid))
            forge_moved_to(gui, pid)
        self.assertEqual(sent(gui), [("player", 2), ("card", ELVES), ("player", 3), ("player", 1)])


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    def test_through_the_real_table_a_creature_then_a_player_attacks_that_player(self):
        """A real 4-player game: two Grizzly Bears, the first clicked before any player (it waits), then the second opponent's
        panel; the second Bears after that goes straight to the same opponent; a click on the third opponent moves nothing."""
        import soak
        import soak_bot
        from tests.test_round28d import _four_player_session
        tmp = tempfile.mkdtemp(prefix="atk1_")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = _four_player_session(tmp)
        self.addCleanup(s.close)
        bot, mem = soak_bot.SoakBot(seed=1), {}

        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                s.poll()
                time.sleep(0.05)

        end = time.time() + 120
        while time.time() < end:                                       # to my first priority (mulligans, who starts)
            s.poll()
            st = s.state or {}
            if st.get("asking") and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                break
            if s.requests or st.get("asking"):
                mv = bot.next_action(st, list(s.requests), mem)
                if mv:
                    soak._do(s, mv)
            pump(0.5)
        lines = ["activeplayer=p0", "activephase=MAIN1", "turn=5"]
        for k in range(4):
            lines += ["p%dlife=20" % k, "p%dlibrary=Forest;Forest;Forest" % k, "p%dhand=" % k]
        lines += ["p0battlefield=Grizzly Bears;Grizzly Bears;Forest", "p1battlefield=Island", "p2battlefield=Island",
                  "p3battlefield=Island"]
        n = s.setups_done
        s.setup(lines)
        while s.setups_done == n and time.time() < end:
            pump(0.3)
        pump(1)
        s.ok()                                                         # pass priority in Main 1: the attack question
        while time.time() < end and s.state.get("input") != "InputAttack":
            pump(0.3)
        self.assertEqual(s.state.get("input"), "InputAttack")
        gui = ft.ForgeTable(s, StubStore(), window_size=(1600, 900))
        frame(gui, 3)
        self.assertTrue(gui.attack_pod())
        opps = [p["id"] for p in s.state["players"] if p["id"] != s.state["me"]]
        bears = [c["id"] for c in s.me()["zones"]["battlefield"] if c["name"] == "Grizzly Bears"]

        def defenders():
            pump(1.5)
            frame(gui)
            return {a["card"]: a.get("defender") for a in s.state.get("combat") or []}

        click(gui, point_for_card(gui, bears[0]))
        self.assertEqual(defenders(), {})                              # waiting for its player: Forge has nothing yet
        click(gui, panel_point(gui, opps[1]))
        self.assertEqual(defenders(), {bears[0]: f"p{opps[1]}"})       # before ATK1 it went to the first opponent
        click(gui, point_for_card(gui, bears[1]))
        self.assertEqual(defenders(), {bears[0]: f"p{opps[1]}", bears[1]: f"p{opps[1]}"})
        click(gui, panel_point(gui, opps[2]))
        self.assertEqual(defenders(), {bears[0]: f"p{opps[1]}", bears[1]: f"p{opps[1]}"})
        gui.press("ok")
        while time.time() < end and s.player(opps[1])["life"] == 20:
            pump(0.3)
            if s.state.get("asking") and s.state.get("input") != "InputAttack":
                s.ok()
        self.assertEqual([s.player(p)["life"] for p in opps], [20, 16, 20])


if __name__ == "__main__":
    unittest.main()
