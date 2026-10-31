# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG18b: Karl's answers of 25 Oct 2026 to RPG18's and RPG17's open questions (claude/DECISIONS_2026-10-25.md in the Project).

  1  RedSashTests     the Red Sash is the beaten leader's successor: his taunt (Karl's wording); "seen" unchanged
  2  LeaderTests      only the bandit by the bridge (the bandits' leader, spot ashfen_outskirts#6) counts for Pell's quest; the
                      other two Ember Bandits are ordinary duels; the leader is called "Ember Bandit Leader"; all three still leave
                      once the quest is ready or done
  3  HintTests        Pell's hints point at the leader by the bridge, the count dropped (Karl's words)
  4  NightMusicTests  the night tracks play from dusk (18:00-05:00); dawn keeps the day tracks (tests/test_rpgnm.py has the hour table)
  5  InnTests         by day Marta has three answers: Rest until dusk / Sleep until morning / Not now (the default); at night two
  6  WalkerTalkTests  a person walking stops when you walk up to them, can be talked to, and carries on when the talk ends
  7  SpeedTests       people walk 1.5 tiles a second

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest
tests.test_rpg18b
"""
import json
import os
import unittest

import pygame

import rpg_screens as rs
import rpg_ui
from rpg import adventure as adv
from rpg import clock, conditions, explore, hints, talk
from tests import test_rpg8 as r8
from tests.test_forge_table import frame, key
from tests.test_review_rpg16b import at_window
from tests.test_rpg14 import WindowBase
from tests.test_rpg16 import at, explorer_at, place
from tests.test_rpg17 import FRAME, let_them_walk, mover_of

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT, TOWN = "ashfen_outskirts", "lantern_hollow"
LEADER = "ashfen_outskirts#6"                       # the Ember Bandit by the bridge (object 6: pinned by tests/test_rpg15c.py)
LEADER_NAME = "Ember Bandit Leader"


def load(*parts):
    with open(os.path.join(BASE, "rpg", "data", *parts), encoding="utf-8") as fh:
        return json.load(fh)


def bandits(area):
    return [t for t in area.triggers if t.kind == "encounter" and t.enemy == "ember_bandit" and not t.rare]


def tile_of(area, t):
    return area.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)


# ---- 1 ----------------------------------------------------------------------------------------------------------------------------

class RedSashTests(unittest.TestCase):
    def test_the_red_sash_is_the_leaders_successor(self):
        r = load("rare_spawns.json")["rare"]["red_sash"]
        self.assertEqual(r["taunt"], "You beat our leader at the bridge. The sash is mine now. Pay up.")
        self.assertEqual(r["seen"], "A red sash moves by the road, in no hurry.")            # unchanged


# ---- 2 ----------------------------------------------------------------------------------------------------------------------------

class LeaderTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_the_quest_names_the_bridge_bandit(self):
        q = load("quests.json")["quests"]["lamplighters_debt"]
        self.assertEqual(q["goal"], {"beat": "ember_bandit", "spot": LEADER, "count": 1,
                                     "others_flag": "road_bandits_fled"})   # Round RPG18c (expected change)
        a = explore.load_area(OUT)
        t = next(t for t in a.triggers if t.spot == LEADER)
        self.assertEqual((t.kind, t.enemy, t.encounter, t.title), ("encounter", "ember_bandit", "regular", LEADER_NAME))
        self.assertEqual(tile_of(a, t), (24, 19))                        # on the road, four tiles west of the bridge (columns 28-29)

    def test_the_other_two_are_ordinary_ember_bandits(self):
        a = explore.load_area(OUT)
        others = [t for t in bandits(a) if t.spot != LEADER]
        self.assertEqual(len(others), 2)
        for t in others:
            self.assertIsNone(t.title)
            self.assertEqual(adv.foe_name(t), "Ember Bandit")
        self.assertEqual(adv.foe_name(next(t for t in bandits(a) if t.spot == LEADER)), LEADER_NAME)

    def test_only_the_leader_counts(self):
        a = explore.load_area(OUT)
        others = [t.spot for t in bandits(a) if t.spot != LEADER]
        w = {}
        talk.Conversation("lamplighter", w).answer(True)
        for spot in others + [None]:                                     # the other two, and a duel with no spot (a random one)
            talk.on_duel_won(w, "ember_bandit", "regular", spot=spot)
            self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "active", spot)
        self.assertEqual(talk.journal(w)[0][1][-8:], "(0 of 1)")
        talk.on_duel_won(w, "ember_bandit", "regular", spot=LEADER)
        self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "ready")

    def test_all_three_still_leave_once_the_quest_is_ready_or_done(self):
        a = explore.load_area(OUT)
        self.assertEqual(len(bandits(a)), 3)
        for state, out in (("none", True), ("active", True), ("ready", False), ("done", False)):
            w = {"quests": {"lamplighters_debt": {"state": state, "count": 0}}} if state != "none" else {}
            for t in bandits(a):
                with self.subTest(state=state, spot=t.spot):
                    self.assertEqual(conditions.all_hold(t.when, w, minutes=12 * 60), out)

    def test_the_map_source_and_the_built_map_agree(self):
        with open(os.path.join(BASE, "rpg", "maps", "src", "ashfen_outskirts.txt"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("L = encounter kind=ember_bandit title=Ember_Bandit_Leader when=!quest:lamplighters_debt=ready,"
                      "!quest:lamplighters_debt=done on =", src)
        rows = src.split("[map]\n", 1)[1].splitlines()
        self.assertEqual(rows[19][24], "L")
        self.assertEqual(sum(r.count("L") for r in rows), 1)


class LeaderWindowTests(WindowBase):
    """Through the Adventure window: the encounter screen and the fight question name him, and the result too; only his win
    finishes the quest."""

    def test_beating_another_bandit_does_not_finish_the_quest_the_leader_does(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        w = f.game.save.world
        talk.Conversation("lamplighter", w).answer(True)
        a = f.explorer.area
        other = next(t for t in bandits(a) if t.spot != LEADER)
        enc = self.meet(g, other)
        self.assertEqual(enc.name, "Ember Bandit")
        f = self.win(g, other)
        self.assertEqual(talk.quest_state(f.game.save.world, "lamplighters_debt"), "active")
        key(g, pygame.K_RETURN)
        f = self.on_map(g, OUT, "from_town")
        leader = next(t for t in bandits(f.explorer.area) if t.spot == LEADER)
        enc = self.meet(g, leader)
        self.assertEqual(enc.name, LEADER_NAME)
        self.assertTrue(adv.fight_text(f.game, leader)[0].startswith(LEADER_NAME))
        f = self.win(g, leader)
        self.assertEqual(talk.quest_state(f.game.save.world, "lamplighters_debt"), "ready")
        self.assertIn(LEADER_NAME, f.result[0] + " ".join(f.result[1]))


# ---- 3 ----------------------------------------------------------------------------------------------------------------------------

class HintTests(unittest.TestCase):
    WANT = {
        "bandits_moor": ["The bandits' leader waits by the bridge on the east road. Walk into him to duel."],
        "both_moor": ["The bandits' leader waits by the bridge on the east road. Walk into him to duel.",
                      "The old mill is up the path north-east of the road. Its stairs go down to the Warrens."],
        "bandits_town": ["Pell's bandits are on the moor's road; their leader is by the bridge. Leave by the east road."],
        "both_town": ["Both jobs are out on the moor. Leave Lantern Hollow by the east road.",
                      "The bandits' leader is by the bridge. The old mill is north-east."],
        "debt_report": ["The bandits' leader is beaten. Tell Pell in Lantern Hollow."],
    }

    def test_karls_words(self):
        got = {h["id"]: h["say"] for h in load("hints.json")["hints"]}
        for hid, say in self.WANT.items():
            with self.subTest(hint=hid):
                self.assertEqual(got[hid], say)

    def test_no_bandit_count_is_left_and_the_hints_check(self):
        for h in load("hints.json")["hints"]:
            for page in h["say"]:
                self.assertNotIn("{count:lamplighters_debt}", page)
                self.assertNotIn("{goal:lamplighters_debt}", page)
        self.assertEqual(hints.check(), [])


# ---- 4 ----------------------------------------------------------------------------------------------------------------------------

class NightMusicTests(unittest.TestCase):
    def test_from_dusk_to_dawn(self):
        self.assertEqual(rs.NIGHT_MUSIC_PHASES, ("dusk", "night"))
        for hhmm, night in (((17, 59), False), ((18, 0), True), ((19, 59), True), ((20, 0), True), ((4, 59), True),
                            ((5, 0), False), ((6, 59), False), ((12, 0), False)):
            with self.subTest(time=hhmm):
                self.assertEqual(clock.phase(hhmm[0] * 60 + hhmm[1]) in rs.NIGHT_MUSIC_PHASES, night)


# ---- 5 ----------------------------------------------------------------------------------------------------------------------------

class InnTests(unittest.TestCase):
    def test_by_day_three_answers_not_now_the_default(self):
        for hour in (7, 12, 17):
            with self.subTest(hour=hour):
                c = talk.Conversation(talk.INNKEEPER, {"clock": at(1, hour)})
                self.assertEqual(c.choice, ("Rest until dusk", "Sleep until morning", "Not now"))
                self.assertEqual(c.choice[c.choice_default], "Not now")

    def test_each_answer_by_day(self):
        for picked, rest, until in ((0, True, "dusk"), (1, True, "morning"), (2, False, None)):
            with self.subTest(picked=picked):
                c = talk.Conversation(talk.INNKEEPER, {"clock": at(1, 12)})
                pages = c.answer(picked == 0, picked)
                self.assertEqual(c.rest, rest)
                if rest:
                    self.assertEqual(c.rest_until, until)
                    self.assertEqual(pages, talk.inn()["rest"])
                else:
                    self.assertEqual(pages, [])

    def test_at_night_she_keeps_sleep_until_morning(self):
        for when in (at(1, 18), at(1, 22), at(2, 3), at(2, 6)):
            with self.subTest(clock=clock.stamp(when)):
                c = talk.Conversation(talk.INNKEEPER, {"clock": when})
                self.assertEqual(c.choice, ("Sleep until morning", "Not now"))
                self.assertEqual(c.choice_default, 0)
                c.answer(True)
                self.assertEqual((c.rest, c.rest_until), (True, "morning"))

    def test_the_box_with_three_answers(self):
        b = rpg_ui.DialogueBox("Marta", ["Hello."], 0.0, choice=("Rest until dusk", "Sleep until morning", "Not now"), default=2)
        b.advance(10.0)
        self.assertTrue(b.choosing)
        self.assertEqual(b.cursor, 2)
        b.key(pygame.K_DOWN, 10.0)
        self.assertEqual(b.cursor, 0)                                      # round
        b.key(pygame.K_UP, 10.0)
        b.key(pygame.K_UP, 10.0)
        self.assertEqual(b.cursor, 1)
        self.assertEqual(b.key(pygame.K_RETURN, 10.0), "answer")
        self.assertEqual((b.picked, b.answer), (1, False))
        b2 = rpg_ui.DialogueBox("Marta", ["Hello."], 0.0, choice=("A", "B", "C"), default=2)
        b2.advance(10.0)
        self.assertEqual(b2.key(pygame.K_ESCAPE, 10.0), "answer")         # Esc answers the last ("Not now")
        self.assertEqual(b2.picked, 2)
        pygame.init()
        screen = pygame.Surface((1360, 840))
        b3 = rpg_ui.DialogueBox("Marta", ["Hello."], 0.0, choice=("A", "B", "C"), default=2)
        b3.advance(10.0)
        names = [n for _r, n in b3.draw(screen, 10.0)]
        self.assertEqual(names, ["talk_next", "talk_yes", "talk_opt1", "talk_no"])
        self.assertEqual(b3.pick("talk_opt1", 10.0), "answer")
        self.assertEqual(b3.picked, 1)


class InnWindowTests(WindowBase):
    def test_sleep_until_morning_by_day_through_the_window(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        w = f.game.save.world
        w["clock"] = at(1, 12)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        self.assertEqual(f.sub, "talk")
        self.say_all(g, answer=1)                                        # "Sleep until morning"
        self.assertEqual(clock.stamp(clock.now(f.game.save.world))[:5], clock.stamp(at(2, 7))[:5])
        self.assertEqual(clock.now(f.game.save.world) % clock.DAY, 7 * 60)


# ---- 6 ----------------------------------------------------------------------------------------------------------------------------

class WalkerTalkTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def hesk_walking(self):
        ex = at_window(place(explorer_at(TOWN, at(1, 19, 59)), (37, 17)), 1920, 1080)
        ex.save.world["clock"] = at(1, 21)
        ex.refresh()
        m = mover_of(ex, "elder")
        self.assertIsNotNone(m)
        return ex, m

    def walk_up_to(self, ex, m):
        """The walker beside Hesk's tile - where he stands, or between two tiles the one he is walking to (not on his way before
        or after it), facing it."""
        a = ex.area
        target = m.tile() if m.at_tile() or m.done else m.route[m.leg]
        way = set(m.route[max(0, m.leg - 1):m.leg + 2])
        for (dx, dy), facing in (((0, 1), "up"), ((0, -1), "down"), ((1, 0), "left"), ((-1, 0), "right")):
            t = (target[0] + dx, target[1] + dy)
            if t in way or a.blocked(*t):
                continue
            ex.walker.x, ex.walker.y = (t[0] + 0.5) * a.tw, (t[1] + 0.5) * a.th
            ex.walker.facing = facing
            return target
        self.fail("no tile beside his next tile to stand on")

    def test_he_stops_when_you_walk_up_and_you_can_talk_to_him(self):
        ex, m = self.hesk_walking()
        for _ in range(20):
            ex.step_movers(FRAME)                                          # under way, between tiles
        self.walk_up_to(ex, m)
        for _ in range(60):                                                # one second: he reaches the tile and stops
            ex.step_movers(FRAME)
        self.assertTrue(m.at_tile(), "he stopped between two tiles")
        stopped = tuple(m.pos)
        for _ in range(60):                                                # Round RPG18c (expected change): under 3 s
            ex.step_movers(FRAME)                                          # stopped - he walks on after PERSON_WAIT_SECONDS
        self.assertEqual(tuple(m.pos), stopped, "he walked on while you stood by him")
        self.assertIsNotNone(mover_of(ex, "elder"))
        self.walk_up_to(ex, m)
        self.assertEqual(ex.facing_npc(), "elder")
        c = talk.Conversation(ex.facing_npc(), ex.save.world)
        self.assertTrue(c.pages)

    def test_when_the_talk_ends_he_carries_on_and_arrives(self):
        ex, m = self.hesk_walking()
        self.walk_up_to(ex, m)
        for _ in range(120):
            ex.step_movers(FRAME)
        stopped = tuple(m.pos)
        ex.talked_to("elder")
        for _ in range(30):
            ex.step_movers(FRAME)
        self.assertNotEqual(tuple(m.pos), stopped, "he didn't carry on after the talk")
        a = ex.area
        ex.walker.x, ex.walker.y = (37 + 0.5) * a.tw, (17 + 0.5) * a.th   # back where we were, out of his way
        let_them_walk(ex)
        self.assertTrue(ex.is_out("npc:elder@32,21"), "he never got home")

    def test_he_never_steps_onto_you(self):
        ex, m = self.hesk_walking()
        nxt = m.route[m.leg]
        a = ex.area
        ex.walker.x, ex.walker.y = (nxt[0] + 0.5) * a.tw, (nxt[1] + 0.5) * a.th
        ex.talked_to("elder")                                              # even after a talk
        for _ in range(120):
            ex.step_movers(FRAME)
            self.assertNotEqual(m.tile(), nxt)

    def test_through_the_window_talk_then_he_walks_on(self):
        """Enter in front of him opens the talk (rpg_screens.talk_to), and he is marked to carry on."""
        ex, m = self.hesk_walking()
        self.walk_up_to(ex, m)
        self.assertFalse(m.talked)

        class Flow:
            pass
        fl = Flow()
        fl.explorer = ex

        class Game:
            save = ex.save
        fl.game = Game()
        fl.say = lambda *a, **k: None
        fl.write = lambda: None
        played = []

        class Gui:
            def play_cue(self, c):
                played.append(c)
        self.assertTrue(rs.AdventureFlow.talk_to(fl, Gui()))
        self.assertTrue(m.talked)
        self.assertEqual(fl.sub, "talk")
        self.assertEqual(fl.talk_conv.npc_id, "elder")


# ---- 7 ----------------------------------------------------------------------------------------------------------------------------

class SpeedTests(unittest.TestCase):
    def test_one_and_a_half_tiles_a_second(self):
        self.assertEqual(explore.PERSON_TILES_PER_SECOND, 1.5)
        m = explore.Mover({"id": "x"}, "g", [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)])
        m.step(1.0)
        self.assertAlmostEqual(m.pos[0], 1.5)
        m.step(1.0)
        self.assertAlmostEqual(m.pos[0], 3.0)


if __name__ == "__main__":
    unittest.main()
