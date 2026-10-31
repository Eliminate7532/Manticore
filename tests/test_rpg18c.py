# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG18c: Karl's answers of 26 Oct 2026 to RPG18b's follow-ups (claude/DECISIONS_2026-10-25.md, "RPG18b's follow-ups").

  1  FleeTests        while Pell's quest is active, beating either ordinary Ember Bandit makes the other leave the road
                      (quests.json goal "others_flag": "road_bandits_fled", set by talk.on_duel_won; marker E's `when` has
                      !flag:road_bandits_fled); the leader by the bridge stays until he is beaten; before the quest nothing flees
  2  WalkOnTests      a walking person stopped beside you walks on after PERSON_WAIT_SECONDS (3) if you don't talk to them,
                      round you (never onto your tile); talking still stops them until the talk ends

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest
tests.test_rpg18c
"""
import json
import os
import unittest

import pygame

import rpg_screens as rs
from rpg import conditions, explore, talk
from tests.test_forge_table import key
from tests.test_rpg14 import WindowBase
from tests.test_rpg16 import at, explorer_at
from tests.test_rpg17 import FRAME, let_them_walk, mover_of
from tests import test_rpg18b as r18b
from tests.test_rpg18b import LEADER, OUT, bandits

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLED = "road_bandits_fled"


def load(*parts):
    with open(os.path.join(BASE, "rpg", "data", *parts), encoding="utf-8") as fh:
        return json.load(fh)


def holds(t, w):
    return conditions.all_hold(t.when, w, minutes=12 * 60)


# ---- 1 ----------------------------------------------------------------------------------------------------------------------------

class FleeTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def others(self):
        return [t for t in bandits(explore.load_area(OUT)) if t.spot != LEADER]

    def test_beating_either_ordinary_bandit_makes_the_other_leave_and_the_leader_stays(self):
        a = explore.load_area(OUT)
        leader = next(t for t in bandits(a) if t.spot == LEADER)
        others = self.others()
        self.assertEqual(len(others), 2)
        for won, other in ((others[0], others[1]), (others[1], others[0])):
            with self.subTest(won=won.spot):
                w = {}
                talk.Conversation("lamplighter", w).answer(True)
                self.assertTrue(holds(other, w) and holds(leader, w))
                talk.on_duel_won(w, "ember_bandit", "regular", spot=won.spot)
                self.assertIn(FLED, w["flags"])
                self.assertFalse(holds(other, w), "the other ordinary bandit is still on the road")
                self.assertTrue(holds(leader, w), "the leader left too")
                self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "active")
                talk.on_duel_won(w, "ember_bandit", "regular", spot=LEADER)
                self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "ready")
                self.assertFalse(holds(leader, w))                     # RPG18's rule kept: all leave once ready

    def test_beating_the_leader_first_sets_no_flag(self):
        w = {}
        talk.Conversation("lamplighter", w).answer(True)
        talk.on_duel_won(w, "ember_bandit", "regular", spot=LEADER)
        self.assertNotIn(FLED, w.get("flags") or [])

    def test_only_while_the_quest_is_active(self):
        for state in ("none", "ready", "done"):
            with self.subTest(state=state):
                w = {"quests": {"lamplighters_debt": {"state": state, "count": 0}}} if state != "none" else {}
                talk.on_duel_won(w, "ember_bandit", "regular", spot=self.others()[0].spot)
                self.assertNotIn(FLED, w.get("flags") or [])

    def test_another_kind_or_a_random_duel_sets_nothing(self):
        w = {}
        talk.Conversation("lamplighter", w).answer(True)
        talk.on_duel_won(w, "grave_robber", "regular", spot=self.others()[0].spot)
        talk.on_duel_won(w, "ember_bandit", "regular", spot=None)        # a random Ember Bandit (no map spot) - my reading
        self.assertNotIn(FLED, w.get("flags") or [])

    def test_the_data_and_the_built_map(self):
        q = load("quests.json")["quests"]["lamplighters_debt"]
        self.assertEqual(q["goal"]["others_flag"], FLED)
        with open(os.path.join(BASE, "rpg", "maps", "src", "ashfen_outskirts.txt"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("E = encounter kind=ember_bandit when=!quest:lamplighters_debt=ready,!quest:lamplighters_debt=done,"
                      "!flag:road_bandits_fled on =", src)
        for t in self.others():
            self.assertIn("!flag:" + FLED, t.when)
            self.assertIn(t.spot, ("ashfen_outskirts#9", "ashfen_outskirts#10"))    # their ids kept (saved spots)
        self.assertEqual(talk.check(kinds=None), [])                    # the flag counts as one the game sets

    def test_the_explorer_takes_the_other_off_the_road(self):
        w_spots = [t.spot for t in self.others()]
        ex = explorer_at(OUT, at(1, 12), quests={"lamplighters_debt": "active"})
        ex.refresh()
        self.assertTrue(all(ex.is_out(s) for s in w_spots + [LEADER]))
        talk.on_duel_won(ex.save.world, "ember_bandit", "regular", spot=w_spots[0])
        ex.save.world.setdefault("cleared", []).append(w_spots[0])
        ex.refresh()
        self.assertFalse(ex.is_out(w_spots[1]), "the other bandit is still out")
        self.assertTrue(ex.is_out(LEADER))
        self.assertNotIn(w_spots[1], [t.spot for t in ex.encounters()])


class FleeWindowTests(WindowBase):
    """Through the Adventure window: an ordinary bandit beaten, back on the map the other is gone and the leader is there."""

    def test_after_one_win_through_the_window_the_other_is_gone(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        talk.Conversation("lamplighter", f.game.save.world).answer(True)
        a = f.explorer.area
        won, other = [t for t in bandits(a) if t.spot != LEADER][:2]
        f = self.win(g, won)
        self.assertIn(FLED, f.game.save.world["flags"])
        key(g, pygame.K_RETURN)
        f = self.on_map(g, OUT, "from_town")
        f.explorer.refresh()
        self.assertFalse(f.explorer.is_out(other.spot))
        self.assertTrue(f.explorer.is_out(LEADER))
        self.assertNotIn(other.spot, [t.spot for t in f.explorer.encounters()])


# ---- 2 ----------------------------------------------------------------------------------------------------------------------------

class WalkOnTests(unittest.TestCase):
    hesk_walking = r18b.WalkerTalkTests.hesk_walking
    walk_up_to = r18b.WalkerTalkTests.walk_up_to

    def setUp(self):
        explore.forget_areas()

    def test_the_constant(self):
        self.assertEqual(explore.PERSON_WAIT_SECONDS, 3.0)

    def test_he_waits_about_three_seconds_then_walks_on(self):
        ex, m = self.hesk_walking()
        for _ in range(20):
            ex.step_movers(FRAME)
        self.walk_up_to(ex, m)
        feet = ex._feet()
        for _ in range(60):                                       # reaches the tile and stops
            ex.step_movers(FRAME)
        self.assertTrue(m.at_tile())
        stopped, t0 = tuple(m.pos), 0
        while tuple(m.pos) == stopped and t0 < 600:
            ex.step_movers(FRAME)
            t0 += 1
            self.assertNotIn(m.tile(), feet)
        waited = m.waited
        self.assertGreaterEqual(waited, explore.PERSON_WAIT_SECONDS - 1e-6)
        self.assertLess(waited, explore.PERSON_WAIT_SECONDS + 1.0)      # he went once he had waited, not later
        self.assertNotEqual(tuple(m.pos), stopped, "he never walked on")
        for _ in range(60 * 40):
            ex.step_movers(FRAME)
            if mover_of(ex, "elder") is None:
                break
            self.assertNotIn(m.tile(), feet, "he stepped onto your tile")
        self.assertTrue(ex.is_out("npc:elder@32,21"), "he never got home")

    def test_standing_on_his_way_he_goes_round_you(self):
        ex, m = self.hesk_walking()
        nxt = m.route[m.leg]
        a = ex.area
        ex.walker.x, ex.walker.y = (nxt[0] + 0.5) * a.tw, (nxt[1] + 0.5) * a.th
        feet = ex._feet()
        for _ in range(int(60 * 30)):                              # half a minute at most, standing still on his way
            ex.step_movers(FRAME)
            if mover_of(ex, "elder") is None:
                break
            self.assertNotIn(m.tile(), feet, "he stepped onto your tile")
        self.assertIsNone(mover_of(ex, "elder"), "he waited on you for ever")
        self.assertTrue(ex.is_out("npc:elder@32,21"))

    def test_talking_still_stops_him_until_the_talk_ends(self):
        """The talk window: no walking while it is open (AdventureFlow.walk_tick), however long; after it he carries on."""
        ex, m = self.hesk_walking()
        self.walk_up_to(ex, m)
        for _ in range(60):
            ex.step_movers(FRAME)
        stopped = tuple(m.pos)
        ex.talked_to("elder")

        class Flow:
            stage, sub, last = "map", "talk", None
        fl = Flow()
        fl.explorer = ex

        class Game:
            save = ex.save
        fl.game = Game()
        fl.say = lambda *a, **k: None

        class Gui:
            overlay = None
        now = 100.0
        for _ in range(600):                                       # ten seconds of talk
            now += FRAME
            rs.AdventureFlow.walk_tick(fl, Gui(), now, pressed=(0, 0))
        self.assertEqual(tuple(m.pos), stopped, "he walked off in the middle of the talk")
        fl.sub = None
        for _ in range(30):
            now += FRAME
            rs.AdventureFlow.walk_tick(fl, Gui(), now, pressed=(0, 0))
        self.assertNotEqual(tuple(m.pos), stopped, "he didn't carry on after the talk")

    def test_walking_away_and_back_stops_him_again(self):
        ex, m = self.hesk_walking()
        self.walk_up_to(ex, m)
        for _ in range(90):
            ex.step_movers(FRAME)
        self.assertGreater(m.waited, 0.0)
        a = ex.area
        ex.walker.x, ex.walker.y = 2.5 * a.tw, 2.5 * a.th                 # far away
        ex.step_movers(FRAME)
        self.assertFalse(m.walk_on)
        self.assertEqual(m.waited, 0.0)

    def test_a_mover_alone_waits_three_seconds(self):
        m = explore.Mover({"id": "x"}, "g", [(0, 0), (1, 0), (2, 0), (3, 0)])
        feet = {(1, 1)}                                            # beside (1, 0), not on the way
        m.step(1.0 / 1.5, feet)                                   # one tile: at (1, 0), next to the walker
        self.assertEqual(m.tile(), (1, 0))
        m.step(2.9, feet)
        self.assertEqual(m.tile(), (1, 0))
        m.step(0.2, feet)                                         # 3.1 s: walks on
        self.assertGreater(m.pos[0], 1.0)

    def test_without_a_way_round_he_still_waits(self):
        m = explore.Mover({"id": "x"}, "g", [(0, 0), (1, 0), (2, 0)])
        feet = {(2, 0)}                                           # standing on his last tile
        for _ in range(10):
            m.step(1.0, feet, detour=lambda s, e, avoid: None)
        self.assertNotIn(m.tile(), feet)
        self.assertFalse(m.done)


if __name__ == "__main__":
    unittest.main()
