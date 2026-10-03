# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 9: rings for what just entered the battlefield, and for the source of a trigger / ability on the stack (forge_fx.py)."""
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_fx as ffx
import forge_table as ft
import gfx
from tests.forge_fake import load_state
from tests.test_forge_table import click, frame, make_gui, point_for


class ArrivalsTests(unittest.TestCase):
    def test_the_first_snapshot_is_history_not_news(self):
        a = ffx.Arrivals()
        self.assertEqual(a.update({1: False, 2: True}, 100.0), [])
        self.assertEqual(a.recent(100.0), [])

    def test_a_new_id_gets_a_ring_that_expires(self):
        a = ffx.Arrivals()
        a.update({1: False}, 100.0)
        self.assertEqual(a.update({1: False, 2: False}, 101.0), [2])
        age, dur, land = a.look(2, 101.5)
        self.assertAlmostEqual(age, 0.5)
        self.assertEqual((dur, land), (ffx.ARRIVE_SECONDS, False))
        self.assertIsNone(a.look(1, 101.5))                                  # it was already there
        self.assertIsNone(a.look(2, 101.0 + ffx.ARRIVE_SECONDS + 0.1))

    def test_lands_are_quicker(self):
        a = ffx.Arrivals()
        a.update({}, 0.0)
        a.update({5: True}, 10.0)
        self.assertIsNotNone(a.look(5, 10.0 + ffx.LAND_SECONDS - 0.1))
        self.assertIsNone(a.look(5, 10.0 + ffx.LAND_SECONDS + 0.1))
        self.assertLess(ffx.LAND_SECONDS, ffx.ARRIVE_SECONDS)

    def test_a_card_that_leaves_and_comes_back_arrives_again(self):
        a = ffx.Arrivals()
        a.update({1: False}, 0.0)
        a.update({1: False, 2: False}, 1.0)
        a.update({1: False}, 1.2)                                            # 2 died
        self.assertEqual(a.recent(1.3), [])
        self.assertEqual(a.update({1: False, 2: False}, 1.4), [2])           # blinked back
        self.assertEqual(a.recent(1.5), [2])

    def test_several_at_once(self):
        a = ffx.Arrivals()
        a.update({1: False}, 0.0)
        self.assertEqual(sorted(a.update({1: False, 7: False, 8: False, 9: True}, 1.0)), [7, 8, 9])
        self.assertEqual(sorted(a.recent(1.1)), [7, 8, 9])


def item(kind, cid):
    return {"trigger": kind == "trigger", "ability": kind == "ability", "card": {"id": cid}}


class StackMarksTests(unittest.TestCase):
    def test_triggers_and_abilities_mark_their_source_with_their_row_number(self):
        marks = ffx.stack_marks([item("trigger", 10), item("spell", 11), item("ability", 12)])
        self.assertEqual(marks, {10: [(1, "trigger")], 12: [(3, "ability")]})     # the spell is on the stack, not on the board

    def test_one_source_with_two_items_lists_both_numbers(self):
        self.assertEqual(ffx.stack_marks([item("trigger", 10), item("trigger", 20), item("ability", 10)])[10],
                         [(1, "trigger"), (3, "ability")])

    def test_a_forge_item_flagged_both_ways_counts_as_a_trigger(self):
        self.assertEqual(ffx.kind_of({"trigger": True, "ability": True}), "trigger")     # what Forge sends for a triggered ability

    def test_a_long_stack_does_not_light_up_the_whole_board(self):
        marks = ffx.stack_marks([item("trigger", 100 + i) for i in range(20)])
        self.assertEqual(len(marks), ffx.STACK_MARK_MAX)

    def test_items_without_a_source_are_skipped(self):
        self.assertEqual(ffx.stack_marks([{"trigger": True, "card": None}, {"trigger": True}]), {})


def region_diff(a, b, rect):
    """How many pixels differ between two screenshots inside rect."""
    ra = pygame.Rect(rect).clip(a.get_rect())
    sa, sb = a.subsurface(ra).copy(), b.subsurface(ra).copy()
    diff = 0
    for x in range(ra.w):
        for y in range(ra.h):
            if sa.get_at((x, y)) != sb.get_at((x, y)):
                diff += 1
    return diff


def shot(gui):
    frame(gui, 1)
    return gui.screen.copy()


class OnTheTableTests(unittest.TestCase):
    def setUp(self):
        self.state = load_state("stack_two_late")

    def test_the_source_of_a_trigger_on_the_stack_is_ringed_and_numbered(self):
        gui = make_gui(self.state)
        frame(gui, 2)
        cid = ffx.stack_marks(gui.state["stack"]).popitem()[0]
        rect = gui.card_rects[cid]
        with_ring = shot(gui)
        no_stack = copy.deepcopy(self.state)
        no_stack["stack"] = []
        gui.session.handle(no_stack)
        without = shot(gui)
        self.assertGreater(region_diff(with_ring, without, rect.inflate(30, 30)), 60)

    def test_the_stack_panel_shows_the_row_numbers(self):
        gui = make_gui(self.state)
        frame(gui, 2)
        panel = gui.screen.subsurface(gui.L.stack)
        purple = pygame.mask.from_threshold(panel, ffx.TRIGGER, (2, 2, 2, 255)).count()
        gold_dim = pygame.mask.from_threshold(panel, tuple(int(c * 0.7) for c in ffx.SPELL), (2, 2, 2, 255)).count()
        self.assertGreater(purple, 20)                                                # row 1 (the trigger) has its number badge
        self.assertGreater(gold_dim, 20)                                              # row 2 (the spell) has a quieter one

    def test_first_look_at_a_board_rings_nothing(self):
        gui = make_gui("declare_attackers")
        frame(gui, 3)
        self.assertEqual(gui.arrivals.recent(__import__("time").time()), [])

    def board_with_new_card(self, base="main1_lands", name=None):
        st = copy.deepcopy(load_state(base))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        bf = me["zones"]["battlefield"]
        card = copy.deepcopy(bf[0])
        card["id"] = 9001
        if name:
            card["name"] = card["oracleName"] = name
        card["tapped"] = False
        card["isLand"] = False
        bf.append(card)
        return st

    def test_a_new_permanent_is_ringed_then_the_ring_goes(self):
        # Round 23: the picture no longer glides in on a fixed wall-clock timer; it flies on FLIGHT springs stepped from real
        # time.monotonic() dt, so both clocks are mocked together (and monotonic is pushed well past the flight's 0.6 s cutoff
        # before we look for the ring, same as tests/test_round10.py's equivalent fix).
        gui = make_gui("main1_lands")
        frame(gui, 2)
        gui.session.handle(self.board_with_new_card())
        with mock.patch("time.time", return_value=1000.0), mock.patch("time.monotonic", return_value=0.0):
            gui.sync()
        with mock.patch("time.time", return_value=1000.0), mock.patch("time.monotonic", return_value=0.75):
            gui.render()                                  # the flight has now settled: the ring can start showing
            now_rect = gui.card_rects[9001]
            ringed = gui.screen.copy()
        self.assertIsNotNone(gui.arrivals.look(9001, 1000.5))
        with mock.patch("time.time", return_value=1000.0 + ffx.ARRIVE_SECONDS + 1), mock.patch("time.monotonic", return_value=0.85):
            gui.render()
            later = gui.screen.copy()
        self.assertGreater(region_diff(ringed, later, now_rect.inflate(60, 60)), 100)
        self.assertIsNone(gui.arrivals.look(9001, 1000.0 + ffx.ARRIVE_SECONDS + 1))

    def test_animations_off_means_the_ring_stands_still(self):
        def two_frames(animations):
            gui = make_gui("main1_lands")
            gui.animations = animations
            frame(gui, 2)
            gui.session.handle(self.board_with_new_card())
            with mock.patch("time.time", return_value=2000.0), mock.patch("time.monotonic", return_value=0.0):
                gui.sync()
            # round 23: with animations on, wait past the spring flight's cutoff (monotonic) before the ring can show at all
            with mock.patch("time.time", return_value=2000.0 + ffx.FLY_SECONDS + 0.02), mock.patch("time.monotonic", return_value=0.75):
                gui.render()
                rect = gui.card_rects[9001].inflate(80, 80)
                a = gui.screen.copy()
            with mock.patch("time.time", return_value=2000.0 + ffx.FLY_SECONDS + 0.12), mock.patch("time.monotonic", return_value=0.85):
                gui.render()
                b = gui.screen.copy()
            return region_diff(a, b, rect)
        self.assertGreater(two_frames(True), 0)          # the expanding ring and the flash move
        self.assertEqual(two_frames(False), 0)           # the same picture 0.1 s later

    def test_the_orange_glow_only_marks_the_copy_that_just_arrived(self):
        gui = make_gui("main1_lands")
        frame(gui, 2)
        st = self.board_with_new_card(name=None)
        old = [c for c in [p for p in st["players"] if p["id"] == st["me"]][0]["zones"]["battlefield"] if c["id"] != 9001][0]
        gui.session.handle(st)
        gui.sync()
        gui.spot = (old["name"], __import__("time").time() + 5)
        with mock.patch.object(gfx, "glow") as glow:
            gui.render()
        orange = [c for c in glow.call_args_list if c.args[2] == ft.ORANGE]
        self.assertEqual(len(orange), 1)
        self.assertEqual(orange[0].args[1], gui.card_rects[9001])

    def test_a_new_game_starts_with_no_rings(self):
        gui = make_gui("main1_lands")
        frame(gui, 2)
        gui.session.handle(self.board_with_new_card())
        gui.sync()
        self.assertTrue(gui.arrivals.recent(__import__("time").time()))
        gui.reset_game()
        self.assertIsNone(gui.arrivals.seen)


class NoBreakageTests(unittest.TestCase):
    def test_the_effects_draw_at_small_and_large_windows_and_text_sizes(self):
        for size, scale in (((900, 600), 2.0), ((900, 600), 1.0), ((1360, 840), 1.75), ((1920, 1080), 1.0)):
            for animations in (True, False):
                gui = make_gui("stack_two_late", size=size, scale=scale)
                gui.animations = animations
                frame(gui, 2)
                st = copy.deepcopy(load_state("stack_two_late"))
                me = [p for p in st["players"] if p["id"] == st["me"]][0]
                card = copy.deepcopy(me["zones"]["battlefield"][0])
                card["id"] = 9002
                me["zones"]["battlefield"].append(card)
                gui.session.handle(st)
                frame(gui, 3)
                self.assertIn(9002, gui.card_rects, (size, scale))
                self.assertTrue(gui.arrivals.recent(__import__("time").time()), (size, scale))


class AnimationSettingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "settings.json")

    def test_the_cog_toggles_and_remembers_it(self):
        gui = make_gui("main1_lands", settings=self.path)
        self.assertTrue(gui.animations)
        click(gui, point_for(gui, "button", name="settings"))
        frame(gui, 1)
        click(gui, [r.center for r, n in gui.overlay.buttons if n == "motion"][0])
        self.assertFalse(gui.animations)
        with open(self.path, encoding="utf-8") as f:
            self.assertIs(json.load(f)["animations"], False)
        again = make_gui("main1_lands", settings=self.path)
        self.assertFalse(again.animations)                # kept for next time

    def test_the_cog_row_says_which_way_it_is(self):
        gui = make_gui("main1_lands", settings=self.path)
        click(gui, point_for(gui, "button", name="settings"))
        frame(gui, 1)
        self.assertIn("motion", [n for _r, n in gui.overlay.buttons])

    def test_help_explains_the_rings(self):
        text = " ".join(f"{k} {v}" for k, v in ft.HELP_LINES)
        self.assertIn("ring", text.lower())
        self.assertIn("Animations", text)


if __name__ == "__main__":
    unittest.main()
