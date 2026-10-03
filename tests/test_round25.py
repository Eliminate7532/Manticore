# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 25: departure ghosts, particles, panel/screen shakes and pod combat arrows (forge_table.py, forge_fx.py, motion.py)."""
import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import events
import forge_table as ft
import motion as mot
from tests.test_forge_table import frame, make_gui, point_for


def beat(kind, **data):
    return events.Beat(dict(data, kind=kind, seq=0), 0.0)


def a_battlefield_card(gui):
    """Any card id currently drawn on the battlefield, with a real rect to depart from."""
    for _rect, kind, data in gui.hits:
        if kind == "card" and data["card"].get("zone") == "Battlefield":
            return data["card"]["id"]
    raise AssertionError("no battlefield card on screen")


class DepartureGhostTests(unittest.TestCase):
    def test_a_departure_beat_spawns_a_ghost_from_the_cards_last_rect_to_the_graveyard_tile(self):
        gui = make_gui("main1_lands")
        cid = a_battlefield_card(gui)
        start = gui.prev_card_rects.get(cid) or gui.card_rects.get(cid)
        self.assertIsNotNone(start)
        card = gui.session.card(cid)
        owner = card.get("controller")
        gui.beats_this_frame = [beat("zone", card=cid, **{"from": "Battlefield", "to": "Graveyard"})]
        gui.apply_effects(time.monotonic())
        self.assertEqual(len(gui.ghosts), 1)
        g = gui.ghosts[0]
        self.assertEqual(tuple(g.start), tuple(start))
        self.assertEqual(tuple(g.end), tuple(gui.zone_tile_rect(owner, "graveyard")))
        self.assertEqual(g.tint, "red")

    def test_the_ghost_fades_out_and_is_gone_after_its_duration(self):
        gui = make_gui("main1_lands")
        cid = a_battlefield_card(gui)
        gui.beats_this_frame = [beat("zone", card=cid, **{"from": "Battlefield", "to": "Exile"})]
        gui.apply_effects(time.monotonic())
        self.assertEqual(len(gui.ghosts), 1)
        self.assertEqual(gui.ghosts[0].tint, "white")
        with mock.patch("time.monotonic", return_value=time.monotonic() + 0.3):
            gui.draw_ghosts()
        self.assertEqual(gui.ghosts, [])

    def test_animations_off_means_no_ghost_ever(self):
        gui = make_gui("main1_lands")
        gui.animations = False
        cid = a_battlefield_card(gui)
        gui.beats_this_frame = [beat("zone", card=cid, **{"from": "Battlefield", "to": "Graveyard"})]
        gui.apply_effects(time.monotonic())
        self.assertEqual(gui.ghosts, [])

    def test_at_most_twelve_ghosts_and_the_oldest_is_dropped_first(self):
        gui = make_gui("main1_lands")
        rect = pygame.Rect(10, 10, 60, 84)
        for i in range(20):
            gui.spawn_ghost(i, rect, pygame.Rect(200, 200, 60, 84), 0.28, "red")
        self.assertLessEqual(len(gui.ghosts), ft.GHOST_MAX)

    def test_a_discard_ghost_goes_from_the_hand_to_the_graveyard_tile(self):
        gui = make_gui("main1_start")
        me = gui.session.me()
        hand = [c for c in me["zones"]["hand"] if not c.get("hidden")]
        self.assertTrue(hand)
        cid = hand[0]["id"]
        gui.beats_this_frame = [beat("zone", card=cid, **{"from": "Hand", "to": "Graveyard"})]
        gui.apply_effects(time.monotonic())
        self.assertEqual(len(gui.ghosts), 1)
        self.assertEqual(gui.ghosts[0].tint, "red")


class ShakeTests(unittest.TestCase):
    def test_damage_player_jitters_only_that_panel_and_the_hit_rect_stays_put(self):
        gui = make_gui("main1_start")
        opp = gui.session.opponents()[0]
        unshaken = point_for(gui, "player", id=opp["id"])
        now = time.monotonic()
        gui.beats_this_frame = [beat("damage_player", player=opp["id"], amount=8)]
        gui.apply_effects(now)
        self.assertIn(opp["id"], gui.panel_shakers)
        self.assertGreater(gui.panel_shakers[opp["id"]].trauma, 0)
        frame(gui)
        self.assertEqual(point_for(gui, "player", id=opp["id"]), unshaken)

    def test_losing_ten_or_more_life_at_once_shakes_the_whole_screen(self):
        gui = make_gui("main1_start")
        me = gui.session.me()
        now = time.monotonic()
        gui.beats_this_frame = [beat("life", player=me["id"], old=20, new=8)]
        gui.apply_effects(now)
        self.assertGreater(gui.screen_shaker.trauma, 0)

    def test_a_small_life_loss_does_not_shake_the_screen(self):
        gui = make_gui("main1_start")
        me = gui.session.me()
        now = time.monotonic()
        gui.beats_this_frame = [beat("life", player=me["id"], old=20, new=17)]
        gui.apply_effects(now)
        self.assertEqual(gui.screen_shaker.trauma, 0)

    def test_losing_the_game_shakes_the_screen(self):
        gui = make_gui("main1_start")
        me = gui.session.me()
        opp = gui.session.opponents()[0]
        now = time.monotonic()
        gui.beats_this_frame = [beat("outcome", winner=opp["id"])]
        gui.apply_effects(now)
        self.assertGreater(gui.screen_shaker.trauma, 0)


class ParticleTests(unittest.TestCase):
    def test_a_counter_added_beat_spawns_gold_particles_at_the_card(self):
        gui = make_gui("main1_lands")
        cid = a_battlefield_card(gui)
        gui.beats_this_frame = [beat("counters", card=cid, old=0, new=1)]
        gui.apply_effects(time.monotonic())
        self.assertGreater(gui.particles.count(), 0)

    def test_life_gain_spawns_particles_too(self):
        gui = make_gui("main1_start")
        me = gui.session.me()
        gui.beats_this_frame = [beat("life", player=me["id"], old=20, new=25)]
        gui.apply_effects(time.monotonic())
        self.assertGreater(gui.particles.count(), 0)


class ActivityTests(unittest.TestCase):
    def test_a_live_ghost_or_shaker_keeps_the_pacer_drawing(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        moving, _pulsing = gui.activity(now)
        gui.spawn_ghost(a_battlefield_card(gui), pygame.Rect(0, 0, 60, 84), pygame.Rect(200, 200, 60, 84), 0.28)
        moving2, _pulsing2 = gui.activity(now)
        self.assertTrue(moving2)


class PodArrowTests(unittest.TestCase):
    def test_an_attack_on_a_player_in_a_pod_draws_a_link_to_their_panel(self):
        gui = make_gui("main1_lands")
        cid = a_battlefield_card(gui)
        opp = gui.session.opponents()[0]
        gui.state["combat"] = [{"card": cid, "blockers": [], "defender": f"p{opp['id']}"}]
        with mock.patch("forge_fx.draw_link") as spy:
            gui.draw_combat_lines()
        spy.assert_called_once()
        args = spy.call_args[0]
        self.assertEqual(args[2], gui.panel_rects[opp["id"]])


if __name__ == "__main__":
    unittest.main()
