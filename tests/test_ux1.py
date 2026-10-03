# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UX1: the first-game tour of the table (tour.py) - a show-and-tell of the table, not a lesson in Magic.

  StartTests     it starts by itself at the first priority question of a new game, once (settings.json "tour_done"), and never in
                 a test, a resumed game, under the mulligan / VS screen / a dialog, or while Forge asks nothing
  InputTests     while it shows, keys and clicks are the tour's: nothing reaches Forge; H, M, + and - still work
  FinishTests    Done and Skip both write "tour_done"; other settings are kept; Cog > Help > Tour of the table shows it again
  LayoutTests    every step's region exists and the caption fits the window beside it at every tested size and text scale
  WordingTests   titles carry no digits (display face, K3); every key a step names is in the help (H)
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import flow_screens as flow
import forge_dialogs as dlg
import forge_table as ft
import tour
from tests.forge_fake import load_state
from tests.test_forge_table import click, frame, key, make_gui

SIZES = (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1100, 700), 2.0), ((4096, 1949), 1.75))


class TourCase(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        os.environ.pop("MANTICORE_NO_TOUR", None)
        self.tmp = tempfile.mkdtemp(prefix="ux1_")
        self.settings = os.path.join(self.tmp, "settings.json")

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, True)

    def gui(self, state="main1_start", size=(1360, 840), scale=None, settings=None, launcher=True):
        if settings is not None:
            with open(self.settings, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        gui = make_gui(state, size, scale, settings=self.settings)
        if launcher:
            gui.launcher = object()              # a real program (the deck screen can start games); tests' tables have none
        return gui

    def tick(self, gui):
        gui.maybe_start_tour()
        frame(gui)

    def saved(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)


class StartTests(TourCase):
    def test_it_starts_at_the_first_priority_question_of_a_new_game(self):
        gui = self.gui()
        self.tick(gui)
        self.assertIsNotNone(gui.tour)
        self.assertTrue(gui.tour_visible())
        self.assertFalse(gui.tour.manual)
        self.assertEqual(gui.tour.index, 0)

    def test_not_in_a_test_run(self):
        os.environ["MANTICORE_NO_TOUR"] = "1"
        gui = self.gui()
        self.tick(gui)
        self.assertIsNone(gui.tour)

    def test_not_once_seen(self):
        gui = self.gui(settings={"tour_done": tour.TOUR_VERSION})
        self.tick(gui)
        self.assertIsNone(gui.tour)

    def test_again_when_the_tour_version_is_newer_than_the_one_seen(self):
        gui = self.gui(settings={"tour_done": tour.TOUR_VERSION - 1})
        self.tick(gui)
        self.assertIsNotNone(gui.tour)

    def test_a_broken_value_counts_as_not_seen(self):
        for bad in ("yes", True, None, [1]):
            with self.subTest(bad=bad):
                gui = self.gui(settings={"tour_done": bad})
                self.assertEqual(gui.tour_done, 0)

    def test_not_without_a_launcher(self):
        gui = self.gui(launcher=False)
        self.tick(gui)
        self.assertIsNone(gui.tour)

    def test_not_in_a_resumed_game(self):
        gui = self.gui()
        gui.resumed_game = True
        self.tick(gui)
        self.assertIsNone(gui.tour)
        gui.reset_game()                                  # the next new game may show it
        self.assertFalse(gui.resumed_game)

    def test_not_under_the_mulligan(self):
        gui = self.gui("mulligan")
        self.tick(gui)
        self.assertIsNone(gui.tour)

    def test_not_while_the_vs_screen_holds(self):
        gui = self.gui()
        gui.vs = flow.VsShow([("You", ["Kinnan, Bonder Prodigy"], ""), ("AI 1", ["Tymna the Weaver"], "")])
        with mock.patch.object(gui.vs, "holding", return_value=True):
            self.tick(gui)
        self.assertIsNone(gui.tour)

    def test_not_under_a_dialog_or_the_cog(self):
        gui = self.gui()
        gui.modal = dlg.HelpDialog()
        gui.maybe_start_tour()
        self.assertIsNone(gui.tour)
        gui.modal = None
        gui.overlay = object()
        gui.maybe_start_tour()
        self.assertIsNone(gui.tour)

    def test_not_while_forge_asks_nothing(self):
        st = copy.deepcopy(load_state("main1_start"))
        st["asking"] = False
        gui = self.gui(st)
        self.tick(gui)
        self.assertIsNone(gui.tour)

    def test_not_on_a_question_that_is_not_priority(self):
        gui = self.gui("declare_attackers")
        self.tick(gui)
        self.assertIsNone(gui.tour)


class InputTests(TourCase):
    def open(self, **kw):
        gui = self.gui(**kw)
        self.tick(gui)
        self.assertIsNotNone(gui.tour)
        gui.session.sent.clear()
        return gui

    def test_keys_move_through_the_steps(self):
        gui = self.open()
        key(gui, pygame.K_RIGHT)
        key(gui, pygame.K_SPACE)
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.tour.index, 3)
        key(gui, pygame.K_LEFT)
        key(gui, pygame.K_BACKSPACE)
        self.assertEqual(gui.tour.index, 1)
        key(gui, pygame.K_LEFT)
        key(gui, pygame.K_LEFT)
        self.assertEqual(gui.tour.index, 0)                # never below the first step

    def test_nothing_reaches_forge_while_it_shows(self):
        gui = self.open()
        for k in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_e, pygame.K_a, pygame.K_s, pygame.K_u, pygame.K_z):
            key(gui, k)
        for x in range(10, gui.L.W, 97):                    # click all over the table, cards and buttons included
            for y in range(10, gui.L.H, 89):
                if gui.tour is None:
                    break
                if not any(r.collidepoint((x, y)) for r, _n in gui.tour.buttons):
                    click(gui, (x, y))
                    click(gui, (x, y), 3)
        self.assertEqual(gui.session.sent, [])
        self.assertIsNotNone(gui.tour)

    def test_the_wheel_does_not_scroll_the_log_under_it(self):
        gui = self.open()
        gui.mouse = gui.log_rect.center
        before = gui.log_scroll
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=3))
        self.assertEqual(gui.log_scroll, before)

    def test_buttons(self):
        gui = self.open()
        names = [n for _r, n in gui.tour.buttons]
        self.assertEqual(sorted(names), ["next", "skip"])  # no Back on the first step
        nxt = dict((n, r) for r, n in gui.tour.buttons)["next"]
        click(gui, nxt.center)
        self.assertEqual(gui.tour.index, 1)
        back = dict((n, r) for r, n in gui.tour.buttons)["back"]
        click(gui, back.center)
        self.assertEqual(gui.tour.index, 0)
        skip = dict((n, r) for r, n in gui.tour.buttons)["skip"]
        click(gui, skip.center)
        self.assertIsNone(gui.tour)
        self.assertEqual(gui.session.sent, [])

    def test_help_sound_and_text_size_keys_still_work(self):
        gui = self.open()
        was = gui.text_scale
        key(gui, pygame.K_EQUALS)
        self.assertGreater(gui.text_scale, was)
        self.assertIsNotNone(gui.tour)
        key(gui, pygame.K_h)
        self.assertIsNotNone(gui.modal)                     # the help window, over the table
        self.assertFalse(gui.tour_visible())                # the tour waits under it
        key(gui, pygame.K_ESCAPE)                           # closes the help, not the tour
        self.assertIsNone(gui.modal)
        self.assertTrue(gui.tour_visible())
        self.assertEqual(gui.session.sent, [])

    def test_f8_opens_the_report_form_over_it(self):
        gui = self.open()
        with mock.patch.object(gui, "open_report") as rep:
            key(gui, pygame.K_F8)
        rep.assert_called_once()

    def test_the_spotlight_glides_with_animations_and_jumps_without(self):
        gui = self.open()
        key(gui, pygame.K_RIGHT)                            # centred step -> the turn bar: nothing to glide from
        now = time.monotonic()
        key(gui, pygame.K_RIGHT)
        self.assertTrue(gui.tour.moving(now + 0.01, True))
        self.assertFalse(gui.tour.moving(now + tour.MOVE_SECONDS + 0.05, True))
        self.assertFalse(gui.tour.moving(now + 0.01, False))
        gui.animations = False
        target = tour.region_rect(gui, gui.tour.step.region).inflate(8, 8).clip(gui.screen.get_rect())
        self.assertEqual(gui.tour.spotlight(gui, gui.tour._moved_at), target)


class FinishTests(TourCase):
    def test_done_writes_tour_done_and_keeps_other_settings(self):
        gui = self.gui(settings={"reporter_name": "Karl", "decks": {"mine": "x"}, "something_else": 7})
        self.tick(gui)
        for _ in range(gui.tour.total):
            key(gui, pygame.K_RIGHT)
        self.assertIsNone(gui.tour)
        data = self.saved()
        self.assertEqual(data["tour_done"], tour.TOUR_VERSION)
        self.assertEqual((data["reporter_name"], data["something_else"]), ("Karl", 7))
        self.tick(gui)
        self.assertIsNone(gui.tour)                         # not again in this game ...
        gui2 = self.gui()
        self.tick(gui2)
        self.assertIsNone(gui2.tour)                        # ... nor the next time the program starts

    def test_skip_writes_it_too_and_says_where_to_find_it(self):
        gui = self.gui()
        self.tick(gui)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.tour)
        self.assertEqual(self.saved()["tour_done"], tour.TOUR_VERSION)
        self.assertIn("Tour of the table", gui.toast[0])

    def test_the_cog_shows_it_again(self):
        gui = self.gui(settings={"tour_done": tour.TOUR_VERSION})
        frame(gui)
        gui.overlay = None
        click(gui, gui.cog_rect.center)
        rects = dict((n, r) for r, n in gui.overlay.buttons)
        self.assertIn("tour", rects)
        click(gui, rects["tour"].center)
        self.assertIsNone(gui.overlay)
        self.assertIsNotNone(gui.tour)
        self.assertTrue(gui.tour.manual)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.toast and "Tour skipped" in gui.toast[0] or None)   # no "skipped" note when you asked for it

    def test_the_cog_button_is_grey_without_a_game(self):
        gui = self.gui()
        gui.menu = mock.MagicMock()
        self.assertFalse(gui.tour_available())
        self.assertFalse(gui.start_tour(manual=True))
        self.assertIsNone(gui.tour)


class LayoutTests(TourCase):
    def test_every_step_fits_at_every_size(self):
        for size, scale in SIZES:
            gui = self.gui(size=size, scale=scale)
            frame(gui)
            gui.animations = False
            gui.start_tour(manual=True)                     # (the first size's Done already wrote tour_done)
            frame(gui)
            t = gui.tour
            self.assertEqual(t.total, len(tour.STEPS), f"{size} @{scale}: a step lost its region")
            win = gui.screen.get_rect()
            for i in range(t.total):
                with self.subTest(size=size, scale=scale, step=t.step.title):
                    self.assertEqual(t.index, i)
                    self.assertTrue(win.contains(t.panel), (t.panel, win))
                    for r, _n in t.buttons:
                        self.assertTrue(t.panel.contains(r))
                    spot = t._shown
                    if spot is not None and t.step.region not in ("bf",):
                        overlap = t.panel.clip(spot)
                        self.assertLess(overlap.w * overlap.h, 0.25 * spot.w * spot.h,
                                        "the caption hides most of what it is about")
                key(gui, pygame.K_RIGHT)
            self.assertIsNone(gui.tour)

    def test_every_region_is_on_the_table(self):
        gui = self.gui()
        frame(gui)
        for region in tour.REGIONS:
            with self.subTest(region=region):
                r = tour.region_rect(gui, region)
                self.assertIsNotNone(r)
                self.assertTrue(gui.screen.get_rect().contains(r))

    def test_text_lines_stay_inside_the_caption(self):
        gui = self.gui(size=(900, 600), scale=2.0)
        frame(gui)
        self.tick(gui)
        for _ in range(gui.tour.total):
            width, height, fonts, lines, keys, pad, gap, btn_h = gui.tour._caption_layout(gui, max(1.0, gui.L.fs))
            body = fonts[1]
            for line in lines:
                self.assertLessEqual(body.size(line)[0], width - 2 * pad, line)
            key(gui, pygame.K_RIGHT)


class WordingTests(unittest.TestCase):
    def test_titles_have_no_digits(self):
        for s in tour.STEPS:
            self.assertIsNone(re.search(r"\d", s.title), s.title)

    def test_every_named_key_is_in_the_help(self):
        help_text = " ".join(k for k, _v in ft.HELP_LINES)
        for s in tour.STEPS:
            for row in s.keys:
                cap = row.partition("  ")[0]
                for part in cap.split(" / "):
                    with self.subTest(step=s.title, key=part):
                        self.assertIn(part.strip(), help_text)

    def test_every_region_is_known(self):
        for s in tour.STEPS:
            self.assertTrue(s.region is None or s.region in tour.REGIONS, s.region)

    def test_a_dozen_steps_at_most_two_sentences_each(self):
        self.assertLessEqual(len(tour.STEPS), 12)
        for s in tour.STEPS:
            sentences = [x for x in re.split(r"(?<=[.!?])\s+", s.text.strip()) if x]
            self.assertLessEqual(len(sentences), 2, s.title)


if __name__ == "__main__":
    unittest.main()
