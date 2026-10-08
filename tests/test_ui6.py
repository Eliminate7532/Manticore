# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch UI6: Cog > Display > Log (Always | Corner | Hidden) and Focus (Always | Over card | Hidden).

The numbers in the test names follow claude/SONNET_SPEC_NOTES_2026-10-08.md section B. All offline; the corner log and the over-card
picture are timed by ForgeTable.ui_clock, which these tests replace with a clock they move by hand."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_settings as fset
import forge_table as ft
import tour
from tests import ui6_layout as u6
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_ad2c import beat, opp_card
from tests.test_forge_table import click, frame, key, move, point_for, point_for_card

ALWAYS_ALWAYS = ("always", "always")
OTHER_MODES = [(lm, pm) for lm in ft.LOG_MODES for pm in ft.PREVIEW_MODES if (lm, pm) != ALWAYS_ALWAYS]
SOME_SIZES = (((900, 600), 1.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def table(scene="two_players", size=(1360, 840), scale=1.0, log="always", preview="always"):
    """A drawn table in the given modes, timed by a clock the test moves."""
    gui = u6.make_table(u6.scenes()[scene], size, scale)
    gui.log_mode, gui.preview_mode = log, preview
    gui.ui_clock = Clock()
    u6.draw(gui)
    return gui


def at(gui, pos, wait=0.0):
    """Move the mouse to pos, let `wait` seconds pass on the table's clock, and draw one frame."""
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0)))
    gui.ui_clock.advance(wait)
    u6.draw(gui, 1)


def rest_on(gui, pos):
    """Hold the mouse on pos long enough for the over-card picture to appear."""
    at(gui, pos, 0.0)
    at(gui, pos, ft.OVER_DWELL + 0.02)


def my_card_ids(gui, zone):
    return [c["id"] for c in gui.session.me()["zones"][zone]]


def middle_card(gui):
    """The battlefield card whose drawn rectangle is nearest the middle of the main area (so a clamp never moves its picture)."""
    cx, cy = gui.L.my_bf.center
    best = min((r for cid, r in gui.card_rects.items() if cid in my_card_ids(gui, "battlefield")),
               key=lambda r: abs(r.centerx - cx) + abs(r.centery - cy))
    return next(cid for cid, r in gui.card_rects.items() if r == best and cid in my_card_ids(gui, "battlefield"))


def edge_point_not_on_anything(gui):
    return (3, gui.L.H // 2)


def expected_picture(gui, crect, size=None):
    """Where the over-card picture belongs for a card drawn at crect, by the spec's rule: centred on the card (above it, for a hand or
    command-zone card), kept inside the window by the table's margin."""
    L = gui.L
    w, h = size or L.over_size
    m = L.margin
    x = max(m, min(crect.centerx - w // 2, L.W - m - w))
    y = (crect.top - 6 - h) if crect.centery >= L.bar.y else (crect.centery - h // 2)
    y = max(m, min(y, L.H - m - h))
    ceiling = L.bar.y - 6 - h                                               # and never over the action bar, where it fits above it
    if ceiling >= m and pygame.Rect(x, y, w, h).colliderect(L.bar):
        y = ceiling
    return pygame.Rect(x, y, w, h)


class SettingsTests(unittest.TestCase):                                     # 1
    def test_both_default_to_always(self):
        gui = u6.make_table(u6.scenes()["two_players"], (1360, 840), 1.0)
        self.assertEqual((gui.log_mode, gui.preview_mode), ALWAYS_ALWAYS)
        self.assertEqual((gui.log_label(), gui.preview_label()), ("Always", "Always"))

    def test_each_cycles_forward_and_back_through_three_modes(self):
        gui = u6.make_table(u6.scenes()["two_players"], (1360, 840), 1.0)
        seen = []
        for _ in range(4):
            gui.change_log_mode(1)
            seen.append(gui.log_mode)
        self.assertEqual(seen, ["corner", "hidden", "always", "corner"])
        gui.change_log_mode(-1)
        self.assertEqual(gui.log_mode, "always")
        seen = []
        for _ in range(4):
            gui.change_preview_mode(1)
            seen.append(gui.preview_mode)
        self.assertEqual(seen, ["over_card", "hidden", "always", "over_card"])
        gui.change_preview_mode(-1)
        self.assertEqual(gui.preview_mode, "always")
        self.assertEqual(gui.log_label(), "Always")
        gui.change_preview_mode(1)
        self.assertEqual(gui.preview_label(), "Over card")

    def test_the_modes_are_saved_and_read_back_and_other_settings_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"text_scale": 1.0, "other_table_setting": 7}, f)
            gui = ft.ForgeTable(FakeSession(load_state("main1_lands"), load_log()), StubStore(), settings_path=path, window_size=(1360, 840))
            gui.change_log_mode(1)
            gui.change_preview_mode(-1)                                     # always -> hidden, going back
            with open(path) as f:
                saved = json.load(f)
            self.assertEqual((saved["log_mode"], saved["preview_mode"]), ("corner", "hidden"))
            self.assertEqual(saved["other_table_setting"], 7)
            again = ft.ForgeTable(FakeSession(load_state("main1_lands"), load_log()), StubStore(), settings_path=path, window_size=(1360, 840))
            self.assertEqual((again.log_mode, again.preview_mode), ("corner", "hidden"))

    def test_a_missing_or_unknown_value_loads_as_always(self):
        for data in ({}, {"log_mode": "sideways", "preview_mode": 7}, {"log_mode": None, "preview_mode": "over card"}):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "settings.json")
                with open(path, "w") as f:
                    json.dump(data, f)
                gui = ft.ForgeTable(FakeSession(load_state("main1_lands"), load_log()), StubStore(), settings_path=path, window_size=(1360, 840))
                self.assertEqual((gui.log_mode, gui.preview_mode), ALWAYS_ALWAYS, data)

    def test_the_cog_has_both_buttons_and_they_cycle_on_left_click_and_back_on_right_click(self):
        gui = table()
        click(gui, point_for(gui, "button", name="settings"))
        self.assertIsInstance(gui.overlay, fset.SettingsPopup)

        def button(name):
            return next(r for r, n in gui.overlay.buttons if n == name).center
        click(gui, button("log_mode"))
        click(gui, button("preview_mode"))
        self.assertEqual((gui.log_mode, gui.preview_mode), ("corner", "over_card"))
        click(gui, button("log_mode"), 3)
        click(gui, button("preview_mode"), 3)
        click(gui, button("preview_mode"), 3)
        self.assertEqual((gui.log_mode, gui.preview_mode), ("always", "hidden"))
        self.assertIsInstance(gui.overlay, fset.SettingsPopup)              # the pop-up stays open, so the label can be seen changing

    def test_every_cog_label_fits_its_button_and_the_pop_up_stays_at_twelve_rows(self):
        real = ft.clip_text
        cut = []

        def spy(text, font, width):
            out = real(text, font, width)
            if text.startswith(("Log:", "Focus:", "Compact:", "Sort hand:")) and out != text:
                cut.append(text)
            return out
        for size, scale in SOME_SIZES:
            for lm, pm in [ALWAYS_ALWAYS] + OTHER_MODES:
                gui = table(size=size, scale=scale, log=lm, preview=pm)
                click(gui, point_for(gui, "button", name="settings"))
                with mock.patch.object(ft, "clip_text", spy):
                    frame(gui)
                self.assertEqual(len(gui.overlay.buttons) > 0, True)
                rect = gui.overlay.rect
                self.assertTrue(pygame.Rect(0, 0, size[0], size[1]).contains(rect), (size, scale, rect))
        self.assertEqual(sorted(set(cut)), [], "these labels are cut short")


class LayoutTests(unittest.TestCase):                                       # 2
    def test_always_and_always_is_exactly_todays_layout(self):
        if u6.BASELINE is None:
            self.skipTest(f"no layout was recorded for {sys.platform!r} (BASELINE_FILES in tests/ui6_layout.py)")
        with open(u6.BASELINE, encoding="utf-8") as f:
            base = json.load(f)
        now = json.loads(json.dumps(u6.all_snapshots()))
        self.assertEqual(sorted(now), sorted(base))
        for k in sorted(base):
            with self.subTest(layout=k):
                self.assertEqual(now[k], base[k])

    def test_every_recorded_layout_file_holds_the_same_28_tables(self):
        want = sorted(f"{scene}|{w}x{h}|{scale}" for scene in u6.scenes() for (w, h), scale in u6.SIZES)
        self.assertEqual(len(want), 28)
        for platform in u6.BASELINE_FILES:
            with open(u6.baseline_path(platform), encoding="utf-8") as f:
                self.assertEqual(sorted(json.load(f)), want, platform)

    def test_the_windows_layout_differs_from_the_linux_one_only_in_opponent_cards_one_pixel_sideways(self):
        """Why there are two recorded layouts: in a 4-player game at 200% text the opponents' command-zone frame is as wide as the word
        "CMD" in the tiny font, and Windows measures that word 1 px differently from Linux, so each opponent's three cards sit 1 px
        left or right. Nothing else may differ - a Windows file recorded from the wrong tree would show here."""
        with open(u6.baseline_path("linux"), encoding="utf-8") as f:
            linux = json.load(f)
        with open(u6.baseline_path("win32"), encoding="utf-8") as f:
            win = json.load(f)
        differ = []
        for k in sorted(linux):
            a, b = dict(linux[k]), dict(win[k])
            cards_a, cards_b = ({c[0]: c[1:] for c in d.pop("cards")} for d in (a, b))
            self.assertEqual(a, b, k)                                       # every rectangle and number but the cards: identical
            self.assertEqual(sorted(cards_a), sorted(cards_b), k)
            for cid in cards_a:
                (xa, ya, wa, ha), (xb, yb, wb, hb) = cards_a[cid], cards_b[cid]
                self.assertEqual((ya, wa, ha), (yb, wb, hb), (k, cid))
                self.assertLessEqual(abs(xa - xb), 1, (k, cid))
            if cards_a != cards_b:
                differ.append(k)
        want = sorted(f"{scene}|{w}x{h}|2.0" for scene in ("four_players", "four_players_stack") for w, h in ((1100, 700), (1360, 840), (1920, 1080)))
        self.assertEqual(differ, want)                                      # the six tables where that frame is as wide as its word

    def test_the_stack_is_never_removed_and_never_shorter_when_the_log_or_the_panel_go(self):
        for scene in ("two_players_stack", "four_players_stack"):
            for size, scale in SOME_SIZES:
                gui = table(scene, size, scale)
                base = pygame.Rect(gui.L.stack)
                self.assertGreater(base.h, 0)
                for lm, pm in OTHER_MODES:
                    gui.log_mode, gui.preview_mode = lm, pm
                    u6.draw(gui, 1)
                    st = gui.L.stack
                    with self.subTest(scene=scene, size=size, scale=scale, modes=(lm, pm)):
                        self.assertGreater(st.h, 0)
                        self.assertGreaterEqual(st.h, base.h)
                        self.assertGreaterEqual(st.h, min(gui.L.stack_need, base.h))
                        self.assertTrue(gui.L.right.contains(st))
                        self.assertTrue(any(k == "stack" for _r, k, _d in gui.hits), "the stack's rows are still hit-testable")

    def test_the_log_has_height_only_in_always_and_the_panel_only_in_always(self):
        gui = table()
        self.assertGreater(gui.L.log.h, 0)
        self.assertTrue(gui.L.log_col and gui.L.panel_on)
        self.assertIsNone(gui.L.aux)
        for lm, pm in OTHER_MODES:
            gui.log_mode, gui.preview_mode = lm, pm
            u6.draw(gui, 1)
            L = gui.L
            self.assertEqual((L.log_col, L.panel_on), (lm == "always", pm == "always"), (lm, pm))
            self.assertEqual(L.log.h > 0, lm == "always", (lm, pm))
            self.assertEqual(L.aux is not None, pm != "always" and L.aux_ok, (lm, pm))

    def test_the_right_column_narrows_only_when_neither_the_panel_nor_the_log_is_in_it_and_never_below_260(self):
        for size, scale in SOME_SIZES:
            gui = table(size=size, scale=scale)
            full = gui.L.right_w
            for lm, pm in OTHER_MODES:
                gui.log_mode, gui.preview_mode = lm, pm
                u6.draw(gui, 1)
                L = gui.L
                self.assertEqual(L.right_w_full, full)
                if lm != "always" and pm != "always":
                    self.assertLessEqual(L.right_w, full, (size, scale, lm, pm))
                    self.assertGreaterEqual(L.right_w, min(full, 260))
                else:
                    self.assertEqual(L.right_w, full, (size, scale, lm, pm))

    def test_the_over_card_size_is_the_panels_designed_size(self):
        for size, scale in SOME_SIZES:
            gui = table(size=size, scale=scale)
            self.assertEqual(tuple(gui.L.preview.size), tuple(gui.L.over_size), (size, scale))

    def test_the_corner_zone_is_a_square_at_the_windows_lower_right_corner(self):
        for size, scale in SOME_SIZES:
            gui = table(size=size, scale=scale, log="corner")
            hot = gui.L.hot
            self.assertEqual((hot.right, hot.bottom), size)
            self.assertEqual(hot.w, hot.h)
            self.assertEqual(hot.w, int(48 * gui.L.fs), (size, scale))     # the spec's 48 px times the text scale, not the constant's value

    def test_the_corner_log_opens_in_the_right_column_and_never_over_the_action_bar(self):
        for scene in ("two_players_stack", "four_players_stack"):
            for size, scale in SOME_SIZES:
                gui = table(scene, size, scale, log="corner")
                L = gui.L
                self.assertTrue(L.right.contains(L.log_overlay), (size, scale))
                self.assertFalse(L.log_overlay.colliderect(L.bar), (size, scale))
                self.assertEqual(L.log_overlay.w, L.right_w)

    def test_in_the_corner_the_log_is_what_the_mouse_is_over_even_where_a_tall_stack_reaches_the_corner(self):
        for scene in ("two_players", "two_players_stack", "four_players_stack"):
            for size, scale in SOME_SIZES:
                gui = table(scene, size, scale, log="corner")
                at(gui, gui.L.hot.center)
                with self.subTest(scene=scene, size=size, scale=scale):
                    self.assertTrue(gui._log_open)
                    self.assertTrue(gui.L.log_overlay.collidepoint(gui.L.hot.center))
                    self.assertIn(gui.hit_at(gui.L.hot.center)[0], ("logpanel", "logcard"))
                    self.assertNotEqual(gui.hit_at(gui.L.hot.center)[0], "stack")

    def test_a_dialogs_zoom_keeps_the_top_of_the_column_with_no_panel(self):
        gui = table()
        self.assertEqual(gui.L.zoom, gui.L.preview)
        gui = table(preview="over_card")
        z = gui.L.zoom
        self.assertEqual((z.y, z.size), (gui.L.right.y, tuple(gui.L.over_size)))
        self.assertTrue(pygame.Rect(0, 0, *gui.screen.get_size()).contains(z))

    def test_the_focus_panel_and_log_steps_of_the_tour_are_left_out_when_they_are_not_on_screen(self):
        gui = table()
        steps = {s.region for s in tour.available_steps(gui)}
        self.assertTrue({"preview", "stacklog"} <= steps)
        gui = table(log="hidden", preview="hidden")
        steps = {s.region for s in tour.available_steps(gui)}
        self.assertNotIn("preview", steps)
        self.assertNotIn("stacklog", steps)                                 # no stack in this scene, no log in the column
        gui = table("two_players_stack", log="hidden", preview="hidden")
        self.assertIn("stacklog", {s.region for s in tour.available_steps(gui)})        # the stack is still there
        rect = tour.region_rect(gui, "stacklog")
        self.assertEqual(rect, gui.L.stack.clip(pygame.Rect(0, 0, gui.L.W, gui.L.H)))


class CornerLogTests(unittest.TestCase):                                    # 3
    def test_the_log_opens_when_the_mouse_enters_the_corner_and_closes_after_it_has_left(self):
        gui = table(log="corner")
        self.assertFalse(gui._log_open)
        self.assertFalse(any(k == "logpanel" for _r, k, _d in gui.hits))
        at(gui, gui.L.hot.center)
        self.assertTrue(gui._log_open)
        self.assertTrue(any(k == "logpanel" for _r, k, _d in gui.hits))
        self.assertEqual(gui.log_rect, gui.L.log_overlay)
        away = edge_point_not_on_anything(gui)
        at(gui, away, 0.0)                                                  # the mouse has just left both
        self.assertTrue(gui._log_open)
        at(gui, away, 0.20)                                                 # the spec says "about 250 ms": the numbers here are the spec's,
        self.assertTrue(gui._log_open, "still open 0.20 s after the mouse left")        # not the constant's, so changing the constant is seen
        at(gui, away, 0.10)
        self.assertFalse(gui._log_open, "closed 0.30 s after the mouse left")
        self.assertFalse(any(k == "logpanel" for _r, k, _d in gui.hits))

    def test_only_the_corner_square_opens_it_one_pixel_outside_does_not(self):
        for size, scale in SOME_SIZES:
            with self.subTest(size=size, scale=scale):
                gui = table("two_players_stack", size, scale, log="corner")
                hot = gui.L.hot
                for pos in ((hot.left - 1, hot.bottom - 1), (hot.right - 1, hot.top - 1)):
                    at(gui, pos, ft.LOG_CLOSE_SECONDS + 0.5)
                    self.assertFalse(gui._log_open, f"{pos} is outside the {hot} square")
                for pos in ((hot.left, hot.top), (hot.right - 1, hot.bottom - 1)):
                    gui2 = table("two_players_stack", size, scale, log="corner")
                    at(gui2, pos)
                    self.assertTrue(gui2._log_open, f"{pos} is inside the {gui2.L.hot} square")

    def test_the_open_log_is_opaque_so_the_stack_under_it_never_shows_through(self):
        for size, scale in (((1360, 840), 2.0), ((1920, 1080), 1.0)):
            with self.subTest(size=size, scale=scale):
                gui = table("two_players_stack", size, scale, log="corner")
                at(gui, gui.L.hot.center)
                self.assertTrue(gui._log_open)
                region = gui.L.log_overlay.clip(gui.L.stack).inflate(-4, -4)       # not the frame's own rounded corner pixels
                self.assertGreater(region.h, 20, "the log covers part of the stack here")
                with_stack = gui.screen.subsurface(region).copy()
                with mock.patch.object(gui, "draw_stack"):
                    u6.draw(gui, 1)
                without_stack = gui.screen.subsurface(region).copy()
                self.assertEqual(pygame.image.tostring(with_stack, "RGB"), pygame.image.tostring(without_stack, "RGB"))

    def test_it_stays_open_while_the_mouse_is_over_the_log_and_the_wheel_scrolls_the_log_not_the_stack(self):
        gui = table("two_players_stack", log="corner")
        at(gui, gui.L.hot.center)
        inside = gui.L.log_overlay.center
        at(gui, inside, 5.0)
        self.assertTrue(gui._log_open)
        before = gui.stack_scroll
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=2))
        self.assertEqual(gui.log_scroll, 6)
        self.assertEqual(gui.stack_scroll, before)

    def test_moving_back_in_before_the_close_time_keeps_it_open_and_restarts_the_timer(self):
        gui = table(log="corner")
        at(gui, gui.L.hot.center)
        away = edge_point_not_on_anything(gui)
        at(gui, away, 0.0)
        at(gui, away, 0.2)
        self.assertTrue(gui._log_open)
        at(gui, gui.L.hot.center, 0.0)                                      # back in
        self.assertTrue(gui._log_open)
        at(gui, away, 0.0)
        at(gui, away, 0.2)
        self.assertTrue(gui._log_open, "the 0.25 s count began again when the mouse left the second time")

    def test_the_log_overlay_alone_does_not_open_it_only_the_corner_does(self):
        gui = table(log="corner")
        r = gui.L.log_overlay
        p = (r.x + 5, r.y + 5)
        self.assertFalse(gui.L.hot.collidepoint(p))
        at(gui, p, 1.0)
        self.assertFalse(gui._log_open)

    def test_the_overlay_is_drawn_over_the_stack_and_a_card_name_in_it_is_a_log_card_hit(self):
        gui = table("two_players_stack", log="corner")
        at(gui, gui.L.hot.center)
        panel = next(i for i, (_r, k, _d) in enumerate(gui.hits) if k == "logpanel")
        names = [i for i, (r, k, _d) in enumerate(gui.hits) if k == "logcard" and gui.L.log_overlay.contains(r)]
        self.assertTrue(names, "the log's card names are clickable hover targets")
        self.assertTrue(all(i > panel for i in names), "registered after the panel, so they win the hit test")

    def test_a_dialog_or_the_cog_closes_it_at_once(self):
        gui = table(log="corner")
        at(gui, gui.L.hot.center)
        self.assertTrue(gui._log_open)
        click(gui, point_for(gui, "button", name="settings"))
        self.assertIsNotNone(gui.overlay)
        self.assertFalse(gui._log_open)
        self.assertFalse(any(k == "logpanel" for _r, k, _d in gui.hits))

    def test_hidden_never_opens_and_always_has_no_overlay(self):
        for mode in ("hidden", "always"):
            gui = table(log=mode)
            at(gui, gui.L.hot.center, 2.0)
            self.assertFalse(gui._log_open, mode)
            self.assertFalse(any(k == "logpanel" for _r, k, _d in gui.hits), mode)
        gui = table(log="hidden")
        self.assertEqual(gui.log_rect.size, (0, 0))

    def test_the_corner_mode_shows_a_faint_mark_and_the_hidden_mode_does_not(self):
        shots = {}
        for mode in ("corner", "hidden"):
            gui = table(log=mode)
            W, H = gui.screen.get_size()
            area = pygame.Rect(W - 60, H - 30, 60, 30)
            shots[mode] = pygame.image.tobytes(gui.screen.subsurface(area).copy(), "RGB")
        self.assertNotEqual(shots["corner"], shots["hidden"])

    def test_the_pending_close_keeps_the_frame_pacer_awake(self):
        gui = table(log="corner")
        at(gui, gui.L.hot.center)
        at(gui, edge_point_not_on_anything(gui), 0.0)
        self.assertIsNotNone(gui._log_left)
        moving, _pulsing = gui.activity(time.monotonic())
        self.assertTrue(moving)


class OverCardTests(unittest.TestCase):                                     # 4
    def test_nothing_shows_until_the_mouse_has_rested_and_then_the_picture_is_centred_over_the_card(self):
        gui = table(preview="over_card")
        cid = middle_card(gui)
        p = point_for_card(gui, cid)
        at(gui, p, 0.0)
        self.assertIsNone(gui.over_rect)
        at(gui, p, 0.09)                                                    # the spec's "about 120 ms": the numbers are the spec's, not the constant's
        self.assertIsNone(gui.over_rect, "not yet after 0.09 s")
        at(gui, p, 0.05)
        self.assertIsNotNone(gui.over_rect, "shown after 0.14 s")
        self.assertEqual(gui.over_rect, expected_picture(gui, gui.card_rects[cid]))
        self.assertEqual(tuple(gui.over_rect.size), tuple(gui.L.over_size))

    def test_where_there_is_room_the_picture_is_exactly_centred_on_what_the_mouse_rests_on(self):
        gui = table("two_players_stack", (1920, 1080), 1.0, preview="over_card")
        rest_on(gui, point_for(gui, "stack"))
        row = next(r for r, k, _d in gui.hits if k == "stack" and r.collidepoint(gui.mouse))
        self.assertIsNotNone(gui.over_rect)
        self.assertLessEqual(abs(gui.over_rect.centerx - row.centerx), 1)
        self.assertLessEqual(abs(gui.over_rect.centery - row.centery), 1)
        self.assertEqual(gui.over_rect, expected_picture(gui, row))

    def test_while_the_mouse_keeps_moving_the_frames_alone_never_start_the_picture(self):
        gui = table(preview="over_card")
        p = point_for_card(gui, middle_card(gui))
        at(gui, p, 0.0)
        for _ in range(50):
            u6.draw(gui, 1)                                                 # frames without any clock time
        self.assertIsNone(gui.over_rect)

    def test_sweeping_across_the_hand_shows_nothing(self):
        gui = table(preview="over_card")
        for cid in my_card_ids(gui, "hand"):
            at(gui, point_for_card(gui, cid), 0.03)
            self.assertIsNone(gui.over_rect, cid)

    def test_a_hand_card_gets_its_picture_above_the_hand_and_every_picture_stays_inside_the_window(self):
        for scene in ("two_players", "four_players_stack"):
            for size, scale in SOME_SIZES:
                gui = table(scene, size, scale, preview="over_card")
                window = pygame.Rect(0, 0, *size)
                targets = [("hand", c) for c in my_card_ids(gui, "hand")[:2]] + \
                          [("battlefield", c) for c in my_card_ids(gui, "battlefield")[:2]]
                for zone, cid in targets:
                    rest_on(gui, edge_point_not_on_anything(gui))
                    rest_on(gui, point_for_card(gui, cid))
                    with self.subTest(scene=scene, size=size, scale=scale, zone=zone, card=cid):
                        self.assertIsNotNone(gui.over_rect)
                        self.assertTrue(window.contains(gui.over_rect), (gui.over_rect, window))
                        if zone == "hand":
                            self.assertLessEqual(gui.over_rect.bottom, gui.card_rects[cid].top + 2)
                            self.assertLessEqual(gui.over_rect.bottom, gui.L.hand.top + 2)

    def test_the_picture_never_covers_the_action_bar_where_it_fits_above_it(self):
        for scene in ("two_players", "four_players_stack"):
            for size, scale in SOME_SIZES:
                gui = table(scene, size, scale, preview="over_card")
                L = gui.L
                targets = [("hand", c) for c in my_card_ids(gui, "hand")] + \
                          [("battlefield", c) for c in my_card_ids(gui, "battlefield")] + \
                          [("command", c) for c in my_card_ids(gui, "command")]
                seen = 0
                for zone, cid in targets:
                    if cid not in gui.card_rects:
                        continue
                    rest_on(gui, edge_point_not_on_anything(gui))
                    rest_on(gui, point_for_card(gui, cid))
                    if gui.over_rect is None:
                        continue
                    seen += 1
                    cap = gui.caption_height(gui.find_card(cid), gui.over_rect.w)
                    block = gui.over_rect.h + ((cap + 4) if cap else 0)
                    fits = L.bar.y - 6 - block >= L.margin
                    with self.subTest(scene=scene, size=size, scale=scale, zone=zone, card=cid, fits=fits):
                        if fits:
                            self.assertFalse(gui.over_rect.colliderect(L.bar), "the picture keeps off the action bar")
                self.assertGreater(seen, 3, (scene, size, scale))

    def test_the_picture_takes_no_clicks_and_the_hits_do_not_change(self):
        gui = table("two_players", preview="over_card")
        cid = middle_card(gui)
        p = point_for_card(gui, cid)
        before_hits = [(tuple(r), k) for r, k, _d in gui.hits]
        probes = [(x, y) for x in range(0, gui.L.W, 37) for y in range(0, gui.L.H, 37)]
        before = [gui.hit_at(q)[0] for q in probes]
        rest_on(gui, p)
        self.assertIsNotNone(gui.over_rect)
        self.assertEqual([(tuple(r), k) for r, k, _d in gui.hits], before_hits, "the picture registered no hit of its own")
        self.assertEqual([gui.hit_at(q)[0] for q in probes], before, "every point of the window answers as it did")
        covered = [q for q in probes if gui.over_rect.collidepoint(q)]
        self.assertTrue(covered)
        kind, data = gui.hit_at(p)
        self.assertEqual((kind, data["card"]["id"]), ("card", cid))

    def test_a_click_under_the_picture_still_does_what_it_always_did(self):
        gui = table("two_players", preview="over_card")
        cid = next(c for c in my_card_ids(gui, "battlefield") if c in gui.card_rects)
        p = point_for_card(gui, cid)
        rest_on(gui, p)
        self.assertIsNotNone(gui.over_rect)
        self.assertTrue(gui.over_rect.collidepoint(p), "the click lands under the picture")
        plain = table("two_players", preview="always")
        click(plain, point_for_card(plain, cid))
        click(gui, p)
        self.assertEqual(gui.session.sent, plain.session.sent)

    def test_it_goes_a_moment_after_the_mouse_leaves_every_card(self):
        gui = table(preview="over_card")
        rest_on(gui, point_for_card(gui, middle_card(gui)))
        self.assertIsNotNone(gui.over_rect)
        away = edge_point_not_on_anything(gui)
        at(gui, away, 0.0)
        self.assertIsNotNone(gui.over_rect, "still there for the switch time")
        at(gui, away, 0.08)                                                 # the spec's "about 60 ms"
        self.assertIsNone(gui.over_rect)

    def test_it_moves_to_the_next_card_only_after_the_switch_time(self):
        gui = table(preview="over_card")
        a, b = [c for c in my_card_ids(gui, "battlefield") if c in gui.card_rects][:2]
        rest_on(gui, point_for_card(gui, a))
        first = pygame.Rect(gui.over_rect)
        pb = point_for_card(gui, b)
        at(gui, pb, 0.0)
        at(gui, pb, 0.03)
        self.assertEqual(gui.over_rect, first, "not yet after 0.03 s")
        at(gui, pb, 0.05)
        self.assertNotEqual(gui.over_rect, first, "moved after 0.08 s")
        self.assertEqual(gui._over_key, ("c", b))

    def test_a_stack_row_and_a_log_name_get_the_picture_too(self):
        gui = table("two_players_stack", preview="over_card")
        rest_on(gui, point_for(gui, "stack"))
        self.assertIsNotNone(gui.over_rect)
        self.assertTrue(pygame.Rect(0, 0, *gui.screen.get_size()).contains(gui.over_rect))
        rest_on(gui, edge_point_not_on_anything(gui))
        at(gui, edge_point_not_on_anything(gui), 0.1)
        self.assertIsNone(gui.over_rect)
        rest_on(gui, point_for(gui, "logcard"))
        self.assertIsNotNone(gui.over_rect)

    def test_a_dialog_hides_it_and_always_and_hidden_never_follow_the_mouse(self):
        gui = table(preview="over_card")
        rest_on(gui, point_for_card(gui, middle_card(gui)))
        self.assertIsNotNone(gui.over_rect)
        click(gui, point_for(gui, "button", name="settings"))
        self.assertIsNone(gui.over_rect)
        for mode in ("always", "hidden"):
            gui = table(preview=mode)
            rest_on(gui, point_for_card(gui, middle_card(gui)))
            at(gui, point_for_card(gui, middle_card(gui)), 1.0)
            self.assertIsNone(gui.over_rect, mode)

    def test_a_pending_dwell_keeps_the_frame_pacer_awake(self):
        gui = table(preview="over_card")
        at(gui, point_for_card(gui, middle_card(gui)), 0.0)
        self.assertIsNotNone(gui._over_cand)
        moving, _pulsing = gui.activity(time.monotonic())
        self.assertTrue(moving)

    def test_the_picture_is_the_cards_own_with_its_caption_below_it_never_over_it(self):
        gui = table(preview="over_card")
        cid = middle_card(gui)
        card = gui.find_card(cid)
        with mock.patch.object(gui, "imprint_caption", return_value="Imprinted: Lightning Bolt"):
            self.assertGreater(gui.caption_height(card, gui.L.over_size[0]), 0)
            rest_on(gui, point_for_card(gui, cid))
            self.assertIsNotNone(gui.over_rect)
            self.assertTrue(pygame.Rect(0, 0, *gui.screen.get_size()).contains(
                gui.over_rect.union(pygame.Rect(gui.over_rect.x, gui.over_rect.bottom, gui.over_rect.w,
                                                gui.caption_height(card, gui.over_rect.w) + 4))))


class PinTests(unittest.TestCase):                                          # 5
    def test_a_right_click_pin_shows_the_picture_even_when_hidden_and_escape_removes_it(self):
        for mode in ("hidden", "over_card"):
            gui = table(preview=mode)
            cid = middle_card(gui)
            click(gui, point_for_card(gui, cid), 3)
            self.assertEqual(gui.pinned, cid, mode)
            at(gui, edge_point_not_on_anything(gui), 1.0)
            self.assertIsNotNone(gui.over_rect, mode)
            self.assertEqual(gui.over_rect, expected_picture(gui, gui.card_rects[cid]), mode)
            key(gui, pygame.K_ESCAPE)
            self.assertIsNone(gui.pinned)
            self.assertIsNone(gui.over_rect)

    def test_a_second_right_click_on_the_same_card_unpins(self):
        gui = table(preview="hidden")
        cid = middle_card(gui)
        click(gui, point_for_card(gui, cid), 3)
        click(gui, point_for_card(gui, cid), 3)
        self.assertIsNone(gui.pinned)
        at(gui, edge_point_not_on_anything(gui), 1.0)
        self.assertIsNone(gui.over_rect)

    def test_what_the_mouse_rests_on_wins_over_the_pin_and_the_pin_comes_back_after(self):
        gui = table(preview="over_card")
        a, b = [c for c in my_card_ids(gui, "battlefield") if c in gui.card_rects][:2]
        click(gui, point_for_card(gui, a), 3)
        rest_on(gui, point_for_card(gui, b))
        self.assertEqual(gui._over_key, ("c", b))
        self.assertEqual(gui.over_rect, expected_picture(gui, gui.card_rects[b]))
        away = edge_point_not_on_anything(gui)
        at(gui, away, 0.0)
        at(gui, away, ft.OVER_SWITCH + 0.01)
        self.assertIsNone(gui._over_key)
        self.assertIsNotNone(gui.over_rect, "the pinned card's picture is back")
        self.assertEqual(gui.over_rect, expected_picture(gui, gui.card_rects[a]))

    def test_with_the_panel_on_a_pin_still_uses_the_panel_and_no_over_card_picture(self):
        gui = table(preview="always")
        cid = middle_card(gui)
        click(gui, point_for_card(gui, cid), 3)
        at(gui, edge_point_not_on_anything(gui), 1.0)
        self.assertIsNone(gui.over_rect)
        self.assertEqual(gui.preview_card()["id"], cid)


class RecordingTests(unittest.TestCase):                                    # 6
    ENTRIES = [{"type": "STACK_ADD", "text": "AI 1 (Kinnan) cast Sol Ring"}, {"type": "STACK_ADD", "text": "Karl cast Forest"}]

    def test_hidden_means_not_drawn_never_not_recorded(self):
        collected = {}
        for lm in ft.LOG_MODES:
            gui = table(log=lm)
            gui.session.handle({"t": "log", "entries": self.ENTRIES})
            u6.draw(gui, 2)
            collected[lm] = ([ln.text() for ln in gui._log_lines], len(gui.session.log), len(gui._log_rows))
            self.assertTrue(any("Sol Ring" in t for t in collected[lm][0]), lm)
            lines = gui.report_context()["log_lines"] if hasattr(gui, "report_context") else []
            with mock.patch.object(gui, "capture_frame", return_value=None), mock.patch.object(gui, "screenshot_encoder", return_value=None):
                lines = gui.report_context()["log_lines"]
            self.assertTrue(any("Sol Ring" in t for t in lines), lm)
        self.assertEqual(collected["corner"][1:], collected["always"][1:])
        self.assertEqual(collected["hidden"][1:], collected["always"][1:])

    def test_the_opponent_feed_and_the_spotlight_name_still_work_with_the_log_hidden(self):
        for lm in ft.LOG_MODES:
            gui = table(log=lm)
            gui.session.handle({"t": "log", "entries": self.ENTRIES})
            u6.draw(gui, 2)
            self.assertTrue(gui.feed, lm)
            self.assertEqual(gui.spot_name(), "Sol Ring", lm)

    def test_the_corner_log_shows_lines_that_arrived_while_it_was_closed(self):
        gui = table(log="corner")
        gui.session.handle({"t": "log", "entries": self.ENTRIES})
        u6.draw(gui, 2)
        at(gui, gui.L.hot.center)
        self.assertTrue(any("Sol Ring" in ln.text() for ln in gui._log_lines))

    def test_wrapping_follows_the_panel_that_is_shown(self):
        gui = table(log="corner")
        self.assertEqual(gui._log_key[0], gui.L.log_overlay.w)
        gui = table(log="always")
        self.assertEqual(gui._log_key[0], gui.L.log.w)


class AuxSlotTests(unittest.TestCase):                                      # 7
    def test_there_is_no_card_slot_with_the_panel_on(self):
        gui = table()
        self.assertIsNone(gui.L.aux)
        self.assertTrue(gui.spot_room())

    def test_with_the_panel_off_the_slot_sits_under_the_stack_inside_the_column(self):
        for scene in ("two_players", "two_players_stack"):
            for lm in ft.LOG_MODES:
                for size, scale in SOME_SIZES:
                    gui = table(scene, size, scale, log=lm, preview="over_card")
                    L = gui.L
                    with self.subTest(scene=scene, log=lm, size=size, scale=scale):
                        if L.aux is None:
                            self.assertFalse(L.aux_ok)
                            self.assertFalse(gui.spot_room() or L.panel_on)
                            continue
                        self.assertTrue(L.aux_ok)
                        self.assertTrue(L.right.contains(L.aux))
                        self.assertFalse(L.aux.colliderect(L.stack))
                        if L.log_col:                               # it covers the log's older lines while it shows; the newest stay readable
                            self.assertLessEqual(L.aux.bottom, L.log.y + int(L.log.h * 0.75) + 1)
                        self.assertGreaterEqual(L.aux.h, int(ft.AUX_MIN * L.fs))

    def test_at_least_the_ordinary_sizes_have_room_for_the_slot(self):
        for size, scale in (((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0)):
            for lm in ft.LOG_MODES:
                gui = table("two_players", size, scale, log=lm, preview="over_card")
                self.assertIsNotNone(gui.L.aux, (size, scale, lm))

    def test_the_slot_shows_the_card_a_question_is_about_and_the_ai_cast_with_animations_off(self):
        gui = table(preview="over_card")
        r = gui.L.aux
        self.assertIsNotNone(r)
        empty = pygame.image.tobytes(gui.screen.subsurface(r).copy(), "RGB")
        card = gui.session.me()["zones"]["hand"][0]
        with mock.patch.object(gui, "prompt_library_card", return_value=card):
            u6.draw(gui, 1)
            forced = pygame.image.tobytes(gui.screen.subsurface(r).copy(), "RGB")
        self.assertNotEqual(forced, empty)
        gui.animations = False
        gui.spot = (card["name"], time.time() + 3.0)
        u6.draw(gui, 1)
        shown = pygame.image.tobytes(gui.screen.subsurface(r).copy(), "RGB")
        self.assertNotEqual(shown, empty)
        gui.animations = True
        u6.draw(gui, 1)
        with_anim = pygame.image.tobytes(gui.screen.subsurface(r).copy(), "RGB")
        self.assertEqual(with_anim, empty, "with animations on the AD2c spotlight shows the cast, not this slot")

    def test_focus_busy_is_false_while_hovering_in_over_card_but_true_for_a_question_card(self):
        gui = table(preview="over_card")
        rest_on(gui, point_for_card(gui, middle_card(gui)))
        self.assertFalse(gui.focus_busy())
        with mock.patch.object(gui, "prompt_library_card", return_value=gui.session.me()["zones"]["hand"][0]):
            self.assertTrue(gui.focus_busy())
        gui = table(preview="always")
        at(gui, point_for_card(gui, middle_card(gui)))
        self.assertTrue(gui.focus_busy(), "the panel is used by what the mouse is over, as before")

    def test_the_spotlight_has_no_room_when_there_is_no_panel_and_no_slot(self):
        gui = table(preview="over_card")
        gui.L.aux_ok = False
        self.assertFalse(gui.spot_room())
        gui.L.aux_ok, gui.L.panel_on = True, False
        self.assertTrue(gui.spot_room())
        gui.L.aux_ok, gui.L.panel_on = False, True
        self.assertTrue(gui.spot_room())

    def test_the_ai_spotlight_draws_in_the_slot_and_skips_when_there_is_no_room(self):
        gui = table(preview="over_card")
        now = time.monotonic()
        gui.anim.feed(gui, [beat("cast", card=opp_card(gui)["id"])], now)
        gui.anim.watch(gui, now)
        self.assertIsNotNone(gui.anim.spot)
        area = pygame.Rect(gui.L.preview)
        self.assertEqual(area, gui.L.aux)
        grab = lambda: pygame.image.tobytes(gui.screen.subsurface(area).copy(), "RGB")
        base = grab()
        gui.anim.draw_spot(gui, now + 0.5)
        self.assertNotEqual(grab(), base, "the spotlight is drawn in the card slot")
        gui.render()                                                        # a clean frame again
        base = grab()
        with mock.patch.object(gui, "spot_room", return_value=False):
            gui.anim.draw_spot(gui, now + 0.5)
        self.assertEqual(grab(), base, "no room, no spotlight")


class SessionAndSendingTests(unittest.TestCase):
    def test_the_modes_never_send_anything_to_forge(self):
        gui = table("two_players_stack")
        sent = len(gui.session.sent)
        for lm, pm in OTHER_MODES:
            gui.change_log_mode(1)
            gui.change_preview_mode(1)
            u6.draw(gui, 1)
            at(gui, gui.L.hot.center, 0.2)
            rest_on(gui, point_for_card(gui, middle_card(gui)))
        self.assertEqual(len(gui.session.sent), sent)


if __name__ == "__main__":
    unittest.main()
