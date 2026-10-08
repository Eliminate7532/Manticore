# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 48 (AD3a, Scope B): the stack, combat, arrival and end-of-game effects in anim.py, and their hooks in forge_table.py,
flow_screens.py and motion.py. The three rules every test here holds to: nothing is sent to Forge, nothing runs with Animations
off, and every effect ends by itself."""
import copy
import math
import os
import random
import sys
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import anim
import events
import flow_screens as flow
import forge_table as ft
import gfx
import motion as mot
from tests.forge_fake import load_state
from tests.test_forge_table import frame, make_gui


def beat(kind, **data):
    return events.Beat(dict(data, kind=kind, seq=0), time.monotonic())


def opp_card(gui, zone="battlefield", index=0):
    return gui.session.opponents()[0]["zones"][zone][index]


def my_card(gui, index=0):
    return gui.session.me()["zones"]["battlefield"][index]


def creature_of(player):
    return next(c for c in player["zones"]["battlefield"] if c.get("isCreature"))


class ArrivalKindTests(unittest.TestCase):
    def test_the_type_line_decides_and_a_token_is_a_token(self):
        self.assertEqual(anim.arrival_kind({"type": "Legendary Creature - Human Wizard"}), "creature")
        self.assertEqual(anim.arrival_kind({"type": "Artifact Creature - Golem"}), "creature")
        self.assertEqual(anim.arrival_kind({"type": "Artifact - Equipment"}), "artifact")
        self.assertEqual(anim.arrival_kind({"type": "Enchantment - Aura"}), "enchantment")
        self.assertEqual(anim.arrival_kind({"type": "Legendary Planeswalker - Jace"}), "planeswalker")
        self.assertEqual(anim.arrival_kind({"type": "Basic Land - Island"}), "land")
        self.assertEqual(anim.arrival_kind({"type": "Battle - Siege"}), "enchantment")
        self.assertEqual(anim.arrival_kind({"type": "Creature - Elf", "token": True}), "token")
        self.assertEqual(anim.arrival_kind({"isCreature": True}), "creature")
        self.assertEqual(anim.arrival_kind({"isLand": True}), "land")
        self.assertEqual(anim.arrival_kind({"isPlaneswalker": True}), "planeswalker")
        self.assertEqual(anim.arrival_kind({}), "other")
        self.assertEqual(anim.arrival_kind(None), "other")

    def test_every_kind_has_a_duration_a_colour_and_a_burst(self):
        for kind in ("creature", "artifact", "enchantment", "planeswalker", "land", "token", "other"):
            self.assertIn(kind, anim.AURA_SECONDS)
            self.assertIn(kind, anim.AURA_COLOUR)
            self.assertIn(kind, anim.ARRIVAL_BURSTS)
            self.assertIn(anim.ARRIVAL_BURSTS[kind][0], ft.PARTICLE_PALETTE)

    def test_float_scale_grows_with_the_hit(self):
        self.assertEqual(anim.float_scale(1), 1.0)
        self.assertEqual(anim.float_scale(4), 1.0)
        self.assertEqual(anim.float_scale(5), 1.3)
        self.assertEqual(anim.float_scale(-7), 1.3)
        self.assertEqual(anim.float_scale(10), 1.65)
        self.assertEqual(anim.float_scale(None), 1.0)


class StreamTests(unittest.TestCase):
    def test_a_stream_flies_from_a_to_b_and_dies_on_arrival(self):
        pool = mot.ParticlePool({"magic": gfx.PARTICLE_MAGIC}, capacity=32)
        rnd = random.Random(3)
        pool.stream(10, 0, 0, 300, 0, "magic", speed=600.0, jitter=0.0, rnd=rnd)
        self.assertEqual(pool.count(), 10)
        for i in range(pool.capacity):
            if pool.alive[i]:
                self.assertGreater(pool.vx[i], 0)                      # all heading right
                self.assertEqual(pool.grav[i], 0.0)                    # no gravity: they arrive
                self.assertAlmostEqual(pool.life[i] * pool.vx[i], 300 - 0, delta=12)   # they die where they arrive (give or take the start jitter)
        for _ in range(20):
            pool.update(0.05)                                           # a second: 600 px/s * 0.7 at the slowest covers 300 px
        self.assertEqual(pool.count(), 0)

    def test_a_stream_of_no_distance_spawns_nothing(self):
        pool = mot.ParticlePool({"magic": gfx.PARTICLE_MAGIC}, capacity=8)
        pool.stream(5, 10, 10, 10, 10, "magic")
        self.assertEqual(pool.count(), 0)


class StackEffectTests(unittest.TestCase):
    def test_a_trigger_pulses_its_source_and_links_it_to_the_stack(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        c = opp_card(gui)
        gui.anim.feed(gui, [beat("cast", card=c["id"], trigger=True)], now)
        self.assertEqual(gui.anim.pulses[c["id"]], (now, "trigger"))
        self.assertEqual(gui.anim.links, [(c["id"], now, "trigger")])
        gui.anim.feed(gui, [beat("cast", card=my_card(gui)["id"], ability=True)], now)
        self.assertEqual(gui.anim.pulses[my_card(gui)["id"]][1], "ability")
        frame(gui)                                                      # draws without a stack panel on screen: no crash
        gui.anim.draw_links(gui, now + anim.PULSE_SECONDS + 0.01)
        self.assertEqual(gui.anim.pulses, {})
        self.assertEqual(gui.anim.links, [])

    def test_a_spell_streams_energy_from_the_caster_to_the_stack(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        before = gui.particles.count()
        gui.anim.feed(gui, [beat("cast", card=opp_card(gui)["id"])], now)
        self.assertGreaterEqual(gui.particles.count() - before, anim.STREAM_COUNT)
        self.assertEqual(gui.anim.pulses, {})                           # a spell is not a trigger: no pulse, no link
        gui2 = make_gui("main1_lands")
        gui2.anim.feed(gui2, [beat("cast", card=my_card(gui2)["id"])], now)     # mine too: from my hand
        self.assertGreaterEqual(gui2.particles.count(), anim.STREAM_COUNT)

    def test_the_link_is_drawn_to_the_entry_of_the_card_that_triggered(self):
        gui = make_gui("stack_two_late")
        frame(gui)
        now = time.monotonic()
        city = [it for it in gui.state["stack"] if it["card"]["name"] == "City of Brass"][0]["card"]
        self.assertIn(city["id"], gui.stack_rects)
        gui.anim.feed(gui, [beat("cast", card=city["id"], trigger=True)], now)
        frame(gui)                                                      # the line goes from the City to its row: no crash, still live
        self.assertEqual(len(gui.anim.links), 1)

    def test_the_rows_remember_where_each_entry_was_and_its_first_target(self):
        st = copy.deepcopy(load_state("stack_one"))
        opp = [p for p in st["players"] if p["id"] != st["me"]][0]
        target = creature_of(opp)
        st["stack"][0]["targets"] = [f"c{target['id']}", f"p{opp['id']}"]
        gui = make_gui(st)
        frame(gui)
        now = time.monotonic()
        gui.anim.watch(gui, now)
        sol = st["stack"][0]["card"]["id"]
        rect, targets, kind, seen = gui.anim.rows[sol]
        self.assertEqual((targets, kind, seen), ([f"c{target['id']}", f"p{opp['id']}"], "spell", now))
        self.assertEqual(rect, gui.stack_row_rects[0])
        # after it leaves the stack the row is kept for ROW_MEMORY seconds, then forgotten
        st2 = copy.deepcopy(st)
        st2["stack"] = []
        gui.session.handle(st2)
        frame(gui)
        gui.anim.watch(gui, now + 1)
        self.assertIn(sol, gui.anim.rows)
        gui.anim.watch(gui, now + anim.ROW_MEMORY + 1.1)
        self.assertNotIn(sol, gui.anim.rows)

    def test_a_resolved_entry_flies_to_its_target_and_bursts_there(self):
        st = copy.deepcopy(load_state("stack_one"))
        opp = [p for p in st["players"] if p["id"] != st["me"]][0]
        target = creature_of(opp)
        st["stack"][0]["targets"] = [f"c{target['id']}"]
        gui = make_gui(st)
        frame(gui)
        now = time.monotonic()
        gui.anim.watch(gui, now)
        sol = st["stack"][0]["card"]["id"]
        start = pygame.Rect(gui.stack_row_rects[0])
        gui.anim.feed(gui, [beat("resolve", card=sol, fizzled=False)], now)
        self.assertEqual(len(gui.anim.resolves), 1)
        r = gui.anim.resolves[0]
        self.assertEqual((r.card, r.start, r.target, r.kind, r.fizzled, r.t0), (sol, start, f"c{target['id']}", "spell", False, now))
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.anim.draw_links(gui, now + anim.RESOLVE_SECONDS * 0.5)
        self.assertGreater(gui.particles.count(), 0)                    # the trail
        self.assertFalse(r.arrived)
        gui.anim.draw_links(gui, now + anim.RESOLVE_SECONDS * 0.95)
        self.assertTrue(r.arrived)                                      # the burst at the target
        gui.anim.draw_links(gui, now + anim.RESOLVE_SECONDS + 0.01)
        self.assertEqual(gui.anim.resolves, [])

    def test_a_fizzled_entry_is_crossed_out_where_its_row_was(self):
        gui = make_gui("stack_one")
        frame(gui)
        now = time.monotonic()
        gui.anim.watch(gui, now)
        sol = gui.state["stack"][0]["card"]["id"]
        gui.anim.feed(gui, [beat("resolve", card=sol, fizzled=True)], now)
        r = gui.anim.resolves[0]
        self.assertTrue(r.fizzled)
        self.assertEqual(r.seconds(), anim.FIZZLE_SECONDS)
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.anim.draw_links(gui, now + 0.1)
        self.assertGreater(gui.particles.count(), 0)                    # a puff of ash, once
        n = gui.particles.count()
        gui.anim.draw_links(gui, now + 0.2)
        self.assertEqual(gui.particles.count(), n)
        gui.anim.draw_links(gui, now + anim.FIZZLE_SECONDS + 0.01)
        self.assertEqual(gui.anim.resolves, [])

    def test_a_resolve_of_an_entry_never_seen_on_the_stack_does_nothing(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        gui.anim.watch(gui, now)
        gui.anim.feed(gui, [beat("resolve", card=opp_card(gui)["id"], fizzled=False)], now)
        self.assertEqual(gui.anim.resolves, [])

    def test_a_resolve_with_no_target_fades_where_it_was(self):
        gui = make_gui("stack_one")
        frame(gui)
        now = time.monotonic()
        gui.anim.watch(gui, now)
        sol = gui.state["stack"][0]["card"]["id"]
        gui.anim.feed(gui, [beat("resolve", card=sol, fizzled=False)], now)
        self.assertIsNone(gui.anim.resolves[0].target)
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.anim.draw_links(gui, now + 0.2)
        self.assertEqual(gui.particles.count(), 0)                      # no comet, no trail: an outline that fades


class ArrivalTests(unittest.TestCase):
    def test_a_permanent_arriving_gets_an_aura_of_its_kind_and_one_burst_of_dust(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        creature = creature_of(gui.session.opponents()[0])
        land = next(c for c in gui.session.opponents()[0]["zones"]["battlefield"] if c.get("isLand"))
        gui.anim.feed(gui, [beat("zone", card=creature["id"], **{"from": "Stack", "to": "Battlefield"}),
                            beat("zone", card=land["id"], **{"from": "Hand", "to": "Battlefield"}),
                            beat("zone", card=creature["id"] + 100000, **{"from": "Battlefield", "to": "Graveyard"})], now)
        self.assertEqual(gui.anim.auras[creature["id"]], ["creature", now, False])
        self.assertEqual(gui.anim.auras[land["id"]], ["land", now, False])
        self.assertNotIn(creature["id"] + 100000, gui.anim.auras)          # leaving is not arriving
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.anim.draw_auras(gui, now + 0.05)
        n = gui.particles.count()
        self.assertGreater(n, 0)
        self.assertTrue(gui.anim.auras[creature["id"]][2] and gui.anim.auras[land["id"]][2])
        gui.anim.draw_auras(gui, now + 0.1)
        self.assertEqual(gui.particles.count(), n)                         # the dust is thrown once
        gui.anim.draw_auras(gui, now + anim.AURA_SECONDS["land"] + 0.01)
        self.assertNotIn(land["id"], gui.anim.auras)                       # a land's is the shortest
        self.assertIn(creature["id"], gui.anim.auras)
        gui.anim.draw_auras(gui, now + anim.AURA_SECONDS["creature"] + 0.01)
        self.assertEqual(gui.anim.auras, {})

    def test_a_token_gets_the_token_aura_and_the_old_puff_is_gone(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        cid = opp_card(gui)["id"]
        gui.beats_this_frame = [beat("token", card=cid)]
        before = gui.particles.count()
        gui.apply_effects(now)
        self.assertEqual(gui.anim.auras[cid][0], "token")
        self.assertEqual(gui.particles.count(), before)                    # _play_effect no longer spawns its own puff ...
        frame(gui)
        self.assertGreater(gui.particles.count(), before)                  # ... the aura does, once the card is drawn

    def test_an_aura_on_a_card_nobody_can_see_is_dropped_quietly(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        gui.anim.feed(gui, [beat("zone", card=424242, **{"from": "Hand", "to": "Battlefield"})], now)
        gui.anim.draw_auras(gui, now + 0.2)
        self.assertIn(424242, gui.anim.auras)
        gui.anim.draw_auras(gui, now + 2.0)
        self.assertEqual(gui.anim.auras, {})

    def test_an_aura_waits_for_a_card_still_gliding_in(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        cid = opp_card(gui)["id"]
        gui.start_flight(cid, pygame.Rect(0, 0, 40, 60))
        gui.anim.feed(gui, [beat("zone", card=cid, **{"from": "Library", "to": "Battlefield"})], now)
        gui.anim.draw_auras(gui, now + 0.1)
        self.assertEqual(gui.anim.auras[cid][1], now + 0.1)                # the clock is held while it flies
        self.assertFalse(gui.anim.auras[cid][2])


class CombatTests(unittest.TestCase):
    def test_a_hit_flashes_knocks_and_the_number_grows_with_the_damage(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        cid = opp_card(gui)["id"]
        gui.anim.feed(gui, [beat("damage_card", card=cid, amount=7)], now)
        self.assertEqual(gui.anim.hits[cid], [now, 7, False])
        self.assertEqual(gui.anim.floats[-1][0], "-7")
        self.assertEqual(gui.anim.floats[-1][5], 1.3)
        gui.anim.feed(gui, [beat("damage_card", card=cid, amount=1)], now + 1)
        self.assertEqual(gui.anim.floats[-1][5], 1.0)
        self.assertNotEqual(gui.anim.nudge(cid, now + 1 + anim.HIT_SECONDS * 0.1), (0, 0))
        self.assertEqual(gui.anim.nudge(cid, now + 1 + anim.HIT_SECONDS + 0.01), (0, 0))
        self.assertEqual(gui.anim.nudge(999999, now), (0, 0))

    def test_the_knocked_creature_is_drawn_off_its_place_then_back(self):
        st = copy.deepcopy(load_state("main1_lands"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        cid = creature_of(me)["id"]
        still = make_gui(copy.deepcopy(st))
        hit = make_gui(copy.deepcopy(st))
        a, b = still.card_rects[cid], hit.card_rects[cid]
        self.assertEqual(a, b)
        hit.anim.feed(hit, [beat("damage_card", card=cid, amount=5)], time.monotonic())
        moved = False
        for _ in range(6):
            frame(hit)
            if hit.card_rects[cid] != a:
                moved = True
                break
            time.sleep(anim.HIT_SECONDS / 8)
        self.assertTrue(moved)
        time.sleep(anim.HIT_SECONDS)
        frame(hit)
        self.assertEqual(hit.card_rects[cid], a)

    def test_a_hit_flash_throws_embers_once_and_a_panel_hit_flashes(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        cid = opp_card(gui)["id"]
        pid = gui.session.opponents()[0]["id"]
        gui.anim.feed(gui, [beat("damage_card", card=cid, amount=3), beat("damage_player", player=pid, amount=4)], now)
        self.assertEqual(gui.anim.panel_hits[pid], (now, 4))
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.anim.draw_hits(gui, now + 0.05)
        n = gui.particles.count()
        self.assertEqual(n, 6)                                              # 3 + the damage
        gui.anim.draw_hits(gui, now + 0.1)
        self.assertEqual(gui.particles.count(), n)
        gui.anim.draw_hits(gui, now + anim.HIT_SECONDS + 0.01)
        self.assertEqual((gui.anim.hits, gui.anim.panel_hits), ({}, {}))

    def test_counters_pop_the_badge_corner(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        cid = opp_card(gui)["id"]
        gui.anim.feed(gui, [beat("counters", card=cid, counter="P1P1", old=0, new=2)], now)
        self.assertEqual(gui.anim.pops[cid], (now, True))
        self.assertEqual(gui.anim.floats[-1][5], 1.0)
        gui.anim.feed(gui, [beat("counters", card=cid, counter="P1P1", old=12, new=0)], now)
        self.assertEqual(gui.anim.pops[cid], (now, False))
        self.assertEqual(gui.anim.floats[-1][5], 1.65)
        gui.anim.draw_pops(gui, now + 0.1)
        self.assertIn(cid, gui.anim.pops)
        gui.anim.draw_pops(gui, now + anim.POP_SECONDS + 0.01)
        self.assertEqual(gui.anim.pops, {})


class GhostTests(unittest.TestCase):
    def test_a_destroyed_permanent_throws_embers_and_an_exiled_one_drains_into_the_void(self):
        gui = make_gui("main1_lands")
        cid = opp_card(gui)["id"]
        start, end = pygame.Rect(100, 100, 50, 70), pygame.Rect(400, 300, 40, 40)
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.spawn_ghost(cid, start, end, ft.GHOST_SECONDS, "red")
        self.assertEqual(gui.particles.count(), ft.DESTROY_EMBERS)
        self.assertEqual({gui.particles.colour[i] for i in range(64) if gui.particles.alive[i]}, {"ember"})
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.spawn_ghost(cid, start, end, ft.GHOST_SECONDS, "white")
        self.assertEqual(gui.particles.count(), ft.EXILE_MOTES)
        self.assertEqual({gui.particles.colour[i] for i in range(64) if gui.particles.alive[i]}, {"void"})
        gui.particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
        gui.spawn_ghost(cid, start, end, ft.GHOST_SECONDS, None)                 # to hand / library: nothing extra
        self.assertEqual(gui.particles.count(), 0)


class BannerAndEndTests(unittest.TestCase):
    def test_the_turn_banner_spawns_embers_in_front_once_a_frame(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        gui.anim.banner = ("AI 1's turn", "Turn 3", now)
        gui.anim.draw_banner(gui, now + 0.1)
        self.assertEqual(gui.front_particles.count(), anim.BANNER_SPAWN)
        gui.anim.draw_banner(gui, now + 0.1)                                 # the same frame time: no second batch
        self.assertEqual(gui.front_particles.count(), anim.BANNER_SPAWN)
        gui.anim.draw_banner(gui, now + 0.2)
        self.assertEqual(gui.front_particles.count(), 2 * anim.BANNER_SPAWN)
        gui.anim.draw_banner(gui, now + anim.BANNER_SECONDS * 0.8)           # fading out: no more
        self.assertEqual(gui.front_particles.count(), 2 * anim.BANNER_SPAWN)

    def test_victory_embers_rise_defeat_ash_falls_and_both_settle(self):
        for kind, colours, up in (("won", {"gold", "ember"}, True), ("lost", {"ash"}, False), ("out", {"ash"}, False), ("draw", {"bone"}, None)):
            with self.subTest(kind=kind):
                gui = make_gui("combat_damage")
                gui.front_particles = mot.ParticlePool(ft.PARTICLE_PALETTE, capacity=64)
                end = flow.EndScreen(kind, "x")
                now = time.monotonic()
                gui.anim.end_particles(gui, end, now)
                self.assertEqual(gui.front_particles.count(), anim.END_SPAWN)
                p = gui.front_particles
                alive = [i for i in range(64) if p.alive[i]]
                self.assertTrue({p.colour[i] for i in alive} <= colours)
                if up is True:
                    self.assertTrue(all(p.vy[i] < 0 for i in alive))
                elif up is False:
                    self.assertTrue(all(p.vy[i] > 0 for i in alive))
                gui.anim.end_particles(gui, end, now + anim.END_SECONDS + 0.01)
                self.assertEqual(gui.front_particles.count(), anim.END_SPAWN)   # settled: no new ones after END_SECONDS
                end2 = flow.EndScreen(kind, "y")
                gui.anim.end_particles(gui, end2, now + anim.END_SECONDS + 1)   # a new end screen starts again
                self.assertEqual(gui.front_particles.count(), 2 * anim.END_SPAWN)

    def test_the_end_screen_draws_them_and_the_table_reports_moving(self):
        gui = make_gui("combat_damage")
        gui.end_screen = flow.EndScreen("won", "You won.")
        frame(gui)
        self.assertGreater(gui.front_particles.count(), 0)
        self.assertTrue(gui.activity(time.monotonic())[0])


class SwitchAndSafetyTests(unittest.TestCase):
    def all_beats(self, gui):
        c = opp_card(gui)["id"]
        pid = gui.session.opponents()[0]["id"]
        return [beat("cast", card=c, trigger=True), beat("cast", card=my_card(gui)["id"]),
                beat("zone", card=c, **{"from": "Hand", "to": "Battlefield"}), beat("token", card=c),
                beat("damage_card", card=c, amount=6), beat("damage_player", player=pid, amount=3),
                beat("counters", card=c, counter="P1P1", old=0, new=1), beat("resolve", card=c, fizzled=True)]

    def test_animations_off_means_none_of_it(self):
        gui = make_gui("main1_lands")
        gui.animations = False
        gui.beats_this_frame = self.all_beats(gui)
        gui.apply_effects(time.monotonic())
        frame(gui, 2)
        a = gui.anim
        self.assertEqual((a.pulses, a.links, a.auras, a.hits, a.panel_hits, a.pops, a.resolves, a.floats), ({}, [], {}, {}, {}, {}, [], []))
        self.assertEqual((gui.particles.count(), gui.front_particles.count()), (0, 0))
        gui.end_screen = flow.EndScreen("won", "You won.")
        frame(gui, 2)
        self.assertEqual(gui.front_particles.count(), 0)
        gui.spawn_ghost(opp_card(gui)["id"], pygame.Rect(0, 0, 40, 60), pygame.Rect(90, 90, 40, 40), 0.3, "red")
        self.assertEqual(gui.particles.count(), 0)

    def test_nothing_here_ever_sends_a_command(self):
        sent = []
        for on in (True, False):
            gui = make_gui("main1_lands")
            gui.animations = on
            gui.beats_this_frame = self.all_beats(gui)
            gui.apply_effects(time.monotonic())
            frame(gui, 3)
            gui.end_screen = flow.EndScreen("won", "You won.")
            frame(gui, 2)
            sent.append(list(gui.session.sent))
        self.assertEqual(sent[0], sent[1])

    def test_every_effect_ends_by_itself(self):
        gui = make_gui("main1_lands")
        now = time.monotonic()
        gui.anim.feed(gui, self.all_beats(gui), now)
        gui.anim.spots.clear()                                           # the AI-action spotlight is AD2c's (its own clock and tests)
        self.assertTrue(gui.anim.active(now + 0.1))
        later = now + max(anim.PULSE_SECONDS, anim.LINK_SECONDS, max(anim.AURA_SECONDS.values()), anim.HIT_SECONDS, anim.POP_SECONDS,
                          anim.FIZZLE_SECONDS, anim.RESOLVE_SECONDS, anim.FLOAT_SECONDS) + 0.05
        gui.anim.draw_auras(gui, later)
        gui.anim.draw_hits(gui, later)
        gui.anim.draw_pops(gui, later)
        gui.anim.draw_links(gui, later)
        gui.anim.draw_floats(gui, later)
        self.assertFalse(gui.anim.active(later))

    def test_everything_draws_at_every_size(self):
        for state in ("main1_lands", "stack_two_late", "combat_damage"):
            for size, scale in (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 2.0), ((4096, 1949), 1.75)):
                with self.subTest(state=state, size=size, scale=scale):
                    gui = make_gui(state, size, scale)
                    now = time.monotonic()
                    gui.anim.watch(gui, now)
                    gui.beats_this_frame = self.all_beats(gui)
                    for it in gui.state.get("stack") or []:
                        cid = (it.get("card") or {}).get("id")
                        if cid is not None:
                            gui.beats_this_frame.append(beat("resolve", card=cid, fizzled=False))
                    gui.apply_effects(now)
                    gui.anim.banner = ("AI 1's turn", "Turn 3", now)
                    frame(gui, 2)
                    self.assertTrue(gui.activity(time.monotonic())[0])
                    if state == "combat_damage":
                        gui.end_screen = flow.EndScreen("lost", "AI 1 won.")
                        frame(gui, 2)

    def test_the_palette_and_the_pools(self):
        for key in ("ember", "ash", "bone", "void", "magic", "red", "green", "gold", "white", "purple"):
            self.assertIn(key, ft.PARTICLE_PALETTE)
        gui = make_gui("main1_lands")
        self.assertEqual(gui.particles.capacity, ft.PARTICLE_CAPACITY)
        self.assertEqual(gui.front_particles.capacity, ft.FRONT_PARTICLE_CAPACITY)
        self.assertGreaterEqual(ft.PARTICLE_CAPACITY, 320)

    def test_the_version(self):
        import version
        self.assertGreaterEqual(tuple(int(x) for x in version.VERSION.split(".")), (0, 28, 52))


if __name__ == "__main__":
    unittest.main()
