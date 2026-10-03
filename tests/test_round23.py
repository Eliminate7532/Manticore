# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 23: motion core - springs (motion.py, ported reference tests), hand hover lift with hysteresis, press dip, and
spring-based card flights that retarget every frame to the card's current layout position."""
import os
import random
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import gfx
import motion as mot
from tests.test_forge_table import click, frame, make_gui, move, point_for_card


def settle(spring, dt, limit=2.0):
    """(overshoot %, time to stay within 2%) for a 100-px step. Ported from docs/reference_2026-09-23/test_reference_modules.py."""
    spring.snap(0)
    spring.target = 100
    t, peak, last_out = 0.0, 0.0, 0.0
    while t < limit:
        spring.step(dt)
        t += dt
        peak = max(peak, spring.value)
        if abs(spring.value - 100) >= 2:
            last_out = t
    return peak - 100, last_out


class MotionModuleTests(unittest.TestCase):
    """motion.py is copied unchanged from the reference; these are its own tests, ported so `discover` picks them up."""

    def test_presets_match_the_spec_at_60_and_30_fps(self):
        for (om, ze), max_over, max_settle in ((mot.HOVER, 0.5, 0.19), (mot.PRESS, 0.5, 0.14), (mot.FLIGHT, 5.0, 0.26),
                                                (mot.SNAPBACK, 1.0, 0.17)):
            for dt in (1 / 60, 1 / 30):
                over, ts = settle(mot.Spring(0, om, ze), dt)
                self.assertLessEqual(over, max_over, (om, ze, dt))
                self.assertLessEqual(ts, max_settle, (om, ze, dt, ts))

    def test_long_pause_does_not_explode(self):
        s = mot.Spring(0, *mot.FLIGHT)
        s.target = 100
        s.step(5.0)
        self.assertTrue(-50 < s.value < 150)

    def test_tween_and_easing(self):
        tw = mot.Tween(0, 10, 1.0, 0.5)
        self.assertEqual(tw.value(0.5), 0)
        self.assertAlmostEqual(tw.value(1.5), 10)
        self.assertTrue(tw.done(1.5))
        self.assertAlmostEqual(max(mot.ease_out_back(i / 1000) for i in range(1001)), 1.057, places=2)

    def test_shaker_decays_to_rest(self):
        sh = mot.Shaker(max_px=10)
        sh.add(1.0, 0.0)
        self.assertNotEqual(sh.offset(0.01), (0, 0))
        self.assertEqual(sh.offset(1.0), (0, 0))
        self.assertFalse(sh.active(1.0))

    def test_particle_pool_never_grows_and_draws_without_allocating(self):
        pool = mot.ParticlePool({"red": (255, 90, 80), "green": (120, 235, 140)}, capacity=160)
        for _ in range(40):
            pool.spawn(10, 100, 100, "red", rnd=random.Random(1))
        self.assertEqual(pool.count(), 160)
        self.assertEqual(len(pool.x), 160)
        screen = pygame.display.get_surface() or pygame.Surface((200, 200))
        made = []
        real = pygame.Surface

        class Spy(real):
            def __init__(self, *a, **k):
                made.append(a)
                super().__init__(*a, **k)

        pygame.Surface = Spy
        try:
            for _ in range(10):
                pool.update(1 / 60)
                pool.draw(screen)
        finally:
            pygame.Surface = real
        self.assertEqual(made, [])
        for _ in range(12):
            pool.update(0.1)                # one update moves at most 0.1 s (a long pause is capped)
        self.assertEqual(pool.count(), 0)


class HoverLiftTests(unittest.TestCase):
    def test_hover_lift_animates_towards_L_lift(self):
        gui = make_gui("main1_start")
        cid = gui.session.me()["zones"]["hand"][0]["id"]
        p = point_for_card(gui, cid)
        base = 1000.0
        with mock.patch("time.monotonic", return_value=base):
            move(gui, p)
            frame(gui)
        v0 = gui.motion[cid].lift.value
        with mock.patch("time.monotonic", return_value=base + 0.016):
            frame(gui)
        v1 = gui.motion[cid].lift.value
        with mock.patch("time.monotonic", return_value=base + 0.05):
            frame(gui)
        v2 = gui.motion[cid].lift.value
        with mock.patch("time.monotonic", return_value=base + 0.4):
            frame(gui)
        v3 = gui.motion[cid].lift.value
        self.assertLessEqual(v0, v1 + 1e-6)
        self.assertLessEqual(v1, v2 + 1e-6)
        self.assertAlmostEqual(v3, gui.L.lift, delta=1.5)

    def test_with_animations_off_the_lift_is_there_on_the_first_frame(self):
        gui = make_gui("main1_start")
        gui.animations = False
        cid = gui.session.me()["zones"]["hand"][0]["id"]
        move(gui, point_for_card(gui, cid))
        frame(gui)
        self.assertAlmostEqual(gui.motion[cid].lift.value, gui.L.lift, delta=1.0)


class HoverHysteresisTests(unittest.TestCase):
    def test_switching_hand_cards_needs_a_60ms_dwell(self):
        gui = make_gui("main1_start")
        self.assertEqual(gui.hover_hand_target(0, 1000.0), 0)      # nothing to debounce: this is the first hover
        self.assertEqual(gui.hover_hand_target(1, 1000.02), 0)     # only 20 ms on the new card: stays put
        self.assertEqual(gui.hover_hand_target(1, 1000.04), 0)     # 40 ms: still not enough
        self.assertEqual(gui.hover_hand_target(1, 1000.09), 1)     # 70 ms since the mouse moved onto card 1: switches

    def test_leaving_the_hand_clears_the_hover_at_once(self):
        gui = make_gui("main1_start")
        gui.hover_hand_target(2, 1000.0)
        self.assertIsNone(gui.hover_hand_target(None, 1000.001))


class PressDipTests(unittest.TestCase):
    def test_the_click_is_sent_on_the_same_frame_as_the_press_dip(self):
        gui = make_gui("main1_start")
        hand = gui.session.me()["zones"]["hand"]
        playable = next((c for c in hand if c.get("selectable") or c.get("weak")), hand[0])
        click(gui, point_for_card(gui, playable["id"]))
        self.assertEqual(gui.session.sent[-1], {"c": "card", "id": playable["id"]})

    def test_a_playable_card_dips_and_recovers(self):
        gui = make_gui("main1_start")
        hand = gui.session.me()["zones"]["hand"]
        playable = next((c for c in hand if c.get("selectable") or c.get("weak")), hand[0])
        gui.press_dip(playable["id"])
        cm = gui.motion[playable["id"]]
        self.assertAlmostEqual(cm.scale.value, 0.96, places=2)
        self.assertEqual(cm.scale.target, 1.0)


class FlightTests(unittest.TestCase):
    def test_a_flight_settles_within_the_timeout_once_retargeted(self):
        gui = make_gui("main1_start")
        cid = 900001
        origin = pygame.Rect(10, 10, 40, 56)
        t = 1000.0
        with mock.patch("time.monotonic", return_value=t):
            gui.start_flight(cid, origin)
        self.assertTrue(gui.motion[cid].flying())
        gui.card_rects[cid] = pygame.Rect(500, 300, 40, 56)
        for _ in range(90):
            t += 1 / 60
            with mock.patch("time.monotonic", return_value=t):
                gui.step_motion(t)
                gui.step_flights(t)
            cm = gui.motion.get(cid)
            if cm is None or not cm.flying():
                break
        self.assertLessEqual(t - 1000.0, 0.61)
        cm = gui.motion.get(cid)
        self.assertTrue(cm is None or not cm.flying())

    def test_a_resize_mid_flight_still_lands_within_the_timeout(self):
        gui = make_gui("main1_start")
        cid = 900002
        origin = pygame.Rect(10, 10, 40, 56)
        t = 1000.0
        with mock.patch("time.monotonic", return_value=t):
            gui.start_flight(cid, origin)
        gui.card_rects[cid] = pygame.Rect(400, 200, 40, 56)
        t += 0.05
        with mock.patch("time.monotonic", return_value=t):
            gui.step_motion(t)
            gui.step_flights(t)
        gui.card_rects[cid] = pygame.Rect(900, 700, 40, 56)     # "the window was resized": a new destination mid-flight
        for _ in range(90):
            t += 1 / 60
            with mock.patch("time.monotonic", return_value=t):
                gui.step_motion(t)
                gui.step_flights(t)
            cm = gui.motion.get(cid)
            if cm is None or not cm.flying():
                break
        self.assertLessEqual(t - 1000.0, 0.65)

    def test_animations_off_nothing_flies(self):
        gui = make_gui("main1_start")
        gui.animations = False
        cid = 900003
        gui.start_flight(cid, pygame.Rect(10, 10, 40, 56))
        self.assertNotIn(cid, gui.motion)          # start_flight itself is a no-op with animations off


class ScaledSurfaceCacheTests(unittest.TestCase):
    def test_hovering_does_not_smoothscale_every_frame(self):
        gui = make_gui("main1_start")
        cid = gui.session.me()["zones"]["hand"][0]["id"]
        p = point_for_card(gui, cid)
        before = {k for k in gfx._ROUNDED if isinstance(k, tuple) and k and k[0] == "scaled"}
        t = 1000.0
        for _ in range(30):
            t += 1 / 60
            with mock.patch("time.monotonic", return_value=t):
                move(gui, p)
                frame(gui)
        after = {k for k in gfx._ROUNDED if isinstance(k, tuple) and k and k[0] == "scaled"}
        self.assertLessEqual(len(after - before), 12)      # quantised to 0.01 steps: only a handful of distinct scales are ever hit


if __name__ == "__main__":
    unittest.main()
