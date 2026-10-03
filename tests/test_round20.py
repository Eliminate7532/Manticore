# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 20: the table draws only when something changed, freezes the picture behind a dialog, keeps its picture cache inside a memory
budget, scales the stop dots, and measures itself (F3, perf_log.txt, bug reports)."""
import copy
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_dialogs as dlg
import forge_table as ft
import gfx
from tests.test_forge_table import make_gui


def settle_frames(gui, n=3):
    for _ in range(n):
        gui.sync()
        gui.render()


class DialogFreezeTests(unittest.TestCase):
    def test_the_table_behind_a_dialog_is_drawn_once_until_the_state_changes(self):
        gui = make_gui("combat_damage", (1360, 840))
        gui.end_continue()                # Round AD2b: this snapshot ends the game; past VICTORY/DEFEAT to the dialog
        settle_frames(gui, 1)
        self.assertIsNotNone(gui.modal, "the combat_damage snapshot opens the damage window")
        settle_frames(gui)
        with mock.patch.object(ft.ForgeTable, "draw_opponent", autospec=True, side_effect=ft.ForgeTable.draw_opponent) as spy:
            settle_frames(gui, 10)
            self.assertEqual(spy.call_count, 0, "a still table must not be redrawn under the dialog")
            st = copy.deepcopy(gui.state)
            st["players"][0]["life"] -= 1
            gui.session.inbox.put(st)                         # a new snapshot arrives (the reader thread queues it)
            settle_frames(gui, 1)
            self.assertEqual(spy.call_count, 1)
            settle_frames(gui, 5)
            self.assertEqual(spy.call_count, 1)

    def test_the_table_is_dimmed_once_not_twice(self):
        gui = make_gui("combat_damage", (1360, 840))
        gui.end_continue()                # Round AD2b: past VICTORY/DEFEAT to the dialog
        settle_frames(gui)
        calls = []
        real = dlg.dim_screen

        def spy(g, alpha=170):
            calls.append(getattr(g, "dim_done", False))
            real(g, alpha)
        with mock.patch.object(dlg, "dim_screen", side_effect=spy), mock.patch.object(ft.dlg, "dim_screen", side_effect=spy):
            settle_frames(gui, 4)
        # the dialog's own panel() calls dim_screen every frame, but it only dims when the table has not already done it
        self.assertTrue(calls)
        self.assertTrue(all(calls), "every call came after the table was already dimmed (so it returned early)")

    def test_closing_the_dialog_redraws_the_live_table(self):
        gui = make_gui("combat_damage", (1360, 840))
        settle_frames(gui)
        gui.modal = None
        gui.session.requests.clear()
        with mock.patch.object(ft.ForgeTable, "draw_opponent", autospec=True, side_effect=ft.ForgeTable.draw_opponent) as spy:
            settle_frames(gui, 2)
        self.assertEqual(spy.call_count, 2)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.saved = gfx._CACHE_BYTES
        gfx.clear_caches()

    def tearDown(self):
        gfx._CACHE_BYTES = self.saved
        gfx.clear_caches()

    def test_translucent_panels_are_made_once(self):
        gui = make_gui("main1_start", (1360, 840))
        settle_frames(gui, 3)
        made = []
        real = pygame.Surface

        class Spy(real):
            def __init__(self, *a, **k):
                made.append(a)
                super().__init__(*a, **k)
        with mock.patch.object(gfx.pygame, "Surface", Spy):
            before = len(gfx._LAYERS)
            settle_frames(gui, 5)
        self.assertEqual(len(gfx._LAYERS), before, "no new panel layers once the screen is steady")

    def test_the_picture_cache_stays_inside_its_memory_budget(self):
        gfx._CACHE_BYTES = 2 * 1024 * 1024
        gui = make_gui("stack_two_late", (1920, 1080))
        settle_frames(gui, 3)
        self.assertLessEqual(gfx.cache_megabytes(), 2.0 + 1e-6)

    def test_cache_keys_never_hold_object_ids(self):
        gui = make_gui("stack_two_late", (1920, 1080))
        settle_frames(gui, 3)

        def ints(x):
            if isinstance(x, int) and not isinstance(x, bool):
                yield x
            elif isinstance(x, tuple):
                for y in x:
                    yield from ints(y)
        biggest = max((i for k in gfx._ROUNDED for i in ints(k)), default=0)
        self.assertLess(biggest, 10 ** 7, "an id() in a cache key can point at a new surface once the old one is dropped")

    def test_dropping_half_the_cache_changes_nothing_on_screen(self):
        gui = make_gui("stack_two_late", (1360, 840))
        settle_frames(gui, 3)
        cid, rect = next(iter(gui.card_rects.items()))
        before = gui.screen.get_at(rect.center)
        for _ in range(len(gfx._ROUNDED) // 2):
            gfx._ROUNDED.popitem(last=False)
        gui._force_draw = True
        settle_frames(gui, 2)
        self.assertEqual(gui.screen.get_at(rect.center), before)


class StopDotTests(unittest.TestCase):
    def test_stop_dots_grow_with_the_text_size(self):
        gui = make_gui("main1_start", (1920, 1080), scale=2.0)
        settle_frames(gui, 2)
        stops = [r for r, kind, _d in gui.hits if kind == "stop"]
        self.assertTrue(stops)
        need = 2 * max(8, int(8 * gui.L.fs))
        self.assertTrue(all(r.w >= need for r in stops), (need, stops[0]))


class PacingTests(unittest.TestCase):
    def make(self, state="main1_start", animations=False):
        gui = make_gui(state, (1360, 840))
        gui.animations = animations
        gui.feed.clear()
        gui.spot = None
        gui.life_flash.clear()
        settle_frames(gui, 2)
        return gui

    def run_for(self, gui, seconds, fps=60, now=1000.0):
        drawn = 0
        for i in range(int(seconds * fps)):
            if gui.tick(now + i / fps):
                drawn += 1
        return drawn

    def test_an_idle_table_draws_only_a_few_frames(self):
        gui = self.make(animations=False)
        drawn = self.run_for(gui, 2.0)
        self.assertLessEqual(drawn, 10)

    def test_a_key_or_a_snapshot_is_drawn_at_once(self):
        gui = self.make(animations=False)
        self.run_for(gui, 1.0)
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F3, mod=0, unicode=""))
        self.assertTrue(gui.tick(1002.0))
        self.run_for(gui, 0.5, now=1002.02)
        st = copy.deepcopy(gui.state)
        st["turn"] = (st.get("turn") or 1) + 1
        self.run_for(gui, 0.3, now=1002.5)
        gui.session.inbox.put(st)
        self.assertTrue(gui.tick(1002.81))

    def test_a_fading_feed_line_keeps_it_drawing(self):
        gui = self.make(animations=True)
        import time as _t
        row = next(r for r in gui.formatter.format({"type": "STACK_ADD", "text": "AI 1 (Kinnan) cast Sol Ring"}) if r.segs) \
            if hasattr(gui, "formatter") else None
        if row is None:
            self.skipTest("no feed row available")
        gui.feed = [(row, _t.time() - (ft.FEED_SECONDS - 0.8))]
        drawn = self.run_for(gui, 0.5)
        self.assertGreaterEqual(drawn, 25)

    def test_the_toast_is_drawn_immediately(self):
        gui = self.make()
        self.run_for(gui, 0.5)
        gui.say("hello")
        self.assertTrue(gui.tick(1001.0))


class MeasurementTests(unittest.TestCase):
    def test_f3_overlay_draws_at_every_size(self):
        for size, scale in (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((1100, 700), 2.0)):
            gui = make_gui("stack_two_late", size, scale)
            gui.show_perf = True
            gui.perf.frame(5.0)
            settle_frames(gui, 1)
            self.assertIn("Table", gui.perf_lines()[0])

    def test_f3_toggles(self):
        gui = make_gui("main1_start")
        gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F3, mod=0, unicode=""))
        self.assertTrue(gui.show_perf)
        gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F3, mod=0, unicode=""))
        self.assertFalse(gui.show_perf)

    def test_shutdown_writes_one_perf_line(self):
        gui = make_gui("main1_start")
        with tempfile.TemporaryDirectory() as tmp:
            gui.perf_log_path = os.path.join(tmp, "perf_log.txt")
            gui.perf.frame(4.0)
            gui.write_perf_log()
            with open(gui.perf_log_path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("table: p50", lines[0])

    def test_bug_reports_carry_the_frame_times(self):
        gui = make_gui("main1_start")
        gui.perf.frame(4.0)
        self.assertIn("Frame times", gui.report_context()["perf"])

    def test_engine_reply_time_is_measured(self):
        gui = make_gui("main1_start")
        gui.session.on_send = None
        gui._hook_session()
        gui.session.on_send({"c": "ok"})
        gui.session.inbox.put(copy.deepcopy(gui.state))
        gui.sync()
        self.assertEqual(gui.perf.engine_summary()["n"], 1)


class SessionHookTests(unittest.TestCase):
    def test_a_failing_hook_never_stops_a_command(self):
        from forge_client import ForgeSession
        s = ForgeSession("", [])

        class Pipe:
            def __init__(self):
                self.data = []

            def write(self, t):
                self.data.append(t)

            def flush(self):
                pass

        class Proc:
            stdin = Pipe()

            def poll(self):
                return None
        s.proc = Proc()
        s.on_send = lambda cmd: 1 / 0
        self.assertTrue(s.send(c="ok"))
        self.assertEqual(len(Proc.stdin.data), 1)


if __name__ == "__main__":
    unittest.main()
