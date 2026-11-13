# SPDX-License-Identifier: GPL-3.0-or-later
"""Task bug-1567718618249035777 (and bug-1567718966477062207): the right column's three panels.

A player (rage_237, 5 Nov 2026, posted by hand): "When the stack fills up with multiple triggers make the game log open up on click",
and "be able to scale down the focus card". Karl, 7 Nov: the focus card, the stack and the game log must be stable, each collapsible
and expandable, the borders between them draggable, the log scrollable, and the layout remembered.

Before this task none of the three could be folded or resized: the stack took up to STACK_MAX_SHARE of the column whatever the
player wanted, and the focus card always took its designed height."""
import copy
import json
import os
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import forge_table as ft
from tests import ui6_layout as u6
from tests.forge_fake import FakeSession, StubStore, load_log, load_state


def long_stack_state(n=8):
    """stack_two_late with its two stack items repeated to n (fresh ids), so the stack needs more than its designed share."""
    st = copy.deepcopy(load_state("stack_two_late"))
    base = st["stack"]
    items = []
    for i in range(n):
        it = copy.deepcopy(base[i % len(base)])
        if it.get("card"):
            it["card"]["id"] = 5000 + i
        items.append(it)
    st["stack"] = items
    return st


def table(state=None, size=(1360, 840), scale=1.0, settings_path=None, fold=None, sizes=None):
    state = state if state is not None else copy.deepcopy(load_state("stack_two_late"))
    gui = ft.ForgeTable(FakeSession(state, load_log()), StubStore(), settings_path=settings_path, window_size=size)
    gui.text_scale = scale
    gui.animations = False
    if fold:
        gui.panel_fold.update(fold)
    if sizes:
        gui.panel_size.update(sizes)
    u6.draw(gui)
    return gui


def click(gui, pos, button=1):
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0)))
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button))
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button))
    u6.draw(gui, 1)


def drag(gui, start, end):
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=start, rel=(0, 0), buttons=(0, 0, 0)))
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1))
    mid = (start[0], (start[1] + end[1]) // 2)
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=mid, rel=(0, 0), buttons=(1, 0, 0)))
    u6.draw(gui, 1)
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=end, rel=(0, 0), buttons=(1, 0, 0)))
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=end, button=1))
    u6.draw(gui, 1)


def hit_rect(gui, name):
    for rect, kind, data in gui.hits:
        if kind == "button" and data.get("name") == name and rect.w < 80:
            return rect
    for rect, kind, data in gui.hits:
        if kind == "button" and data.get("name") == name:
            return rect
    return None


def seam(gui, name):
    return dict(gui.L.seams).get(name)


class SettingsTests(unittest.TestCase):
    def test_missing_or_broken_values_are_as_designed(self):
        for bad in (None, 3, "x", [], {"fold": "yes"}, {"focus": 2.0, "stack": -1}, {"focus": True}, {"fold": {"log": 1}}):
            fold, size = ft.right_panels_from(bad)
            self.assertEqual(fold, {"focus": False, "stack": False, "log": False}, bad)
            self.assertEqual(size, {"focus": None, "stack": None}, bad)

    def test_good_values_are_kept(self):
        fold, size = ft.right_panels_from({"fold": {"stack": True, "log": False}, "focus": 0.3, "stack": 0.5})
        self.assertEqual(fold, {"focus": False, "stack": True, "log": False})
        self.assertEqual(size, {"focus": 0.3, "stack": 0.5})

    def test_saved_and_read_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            gui = table(settings_path=path, fold={"stack": True}, sizes={"focus": 0.3})
            gui.save_settings()
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)["right_panels"]
            self.assertEqual(saved, {"fold": {"focus": False, "stack": True, "log": False}, "focus": 0.3, "stack": None})
            again = table(settings_path=path)
            self.assertEqual(again.panel_fold["stack"], True)
            self.assertEqual(again.panel_size["focus"], 0.3)
            self.assertIn("stack", again.L.heads)


class LayoutTests(unittest.TestCase):
    def test_nothing_folded_or_dragged_changes_nothing(self):
        gui = table()
        self.assertEqual(gui.L.heads, {})
        self.assertEqual([n for n, _ in gui.L.seams], ["focus", "stack"])

    def test_folding_the_stack_gives_its_room_to_the_log(self):
        gui = table()
        L0 = gui.L
        log_h, stack = L0.log.h, L0.stack
        gui.panel_fold["stack"] = True
        u6.draw(gui, 1)
        L = gui.L
        self.assertEqual(L.stack.top, stack.top)
        self.assertEqual(L.stack.h, L.panel_head)
        self.assertEqual(L.log.top, L.stack.bottom + L.margin)
        self.assertEqual(L.log.h, log_h + stack.h - L.panel_head)
        self.assertEqual(L.log.bottom, L.right.bottom)
        self.assertNotIn("stack", dict(L.seams))
        self.assertEqual(gui.stack_row_rects, {})             # no row is drawn, nor clickable
        self.assertFalse(any(k == "stack" for _r, k, _d in gui.hits))

    def test_folding_the_log_lets_a_long_stack_take_what_it_needs(self):
        gui = table(long_stack_state())
        L0 = gui.L
        self.assertLess(L0.stack.h, L0.stack_need)              # as designed it scrolls
        gui.panel_fold["log"] = True
        u6.draw(gui, 1)
        L = gui.L
        self.assertGreater(L.stack.h, L0.stack.h)
        self.assertEqual(L.log.h, L.panel_head)
        self.assertEqual(L.log.bottom, L.right.bottom)          # the folded log stays at the foot of the column
        self.assertLessEqual(L.stack.bottom + L.margin, L.log.top)

    def test_a_folded_log_stays_at_the_foot_of_the_column_whatever_is_above_it(self):
        for state in (copy.deepcopy(load_state("stack_two_late")), copy.deepcopy(load_state("main1_lands"))):
            for fold in ({"log": True}, {"log": True, "stack": True}, {"log": True, "focus": True}):
                gui = table(state, fold=fold)
                L = gui.L
                self.assertEqual(L.log.bottom, L.right.bottom, fold)
                self.assertEqual(L.log.h, L.panel_head, fold)
                self.assertEqual(L.heads["log"], L.log)

    def test_folding_the_focus_card_moves_the_stack_up(self):
        gui = table()
        top = gui.L.right.y
        gui.panel_fold["focus"] = True
        u6.draw(gui, 1)
        L = gui.L
        self.assertEqual(L.heads["focus"].top, top)
        self.assertEqual(L.stack.top, top + L.panel_head + L.margin)
        self.assertFalse(L.panel_on)
        self.assertFalse(gui.focus_shown())

    def test_every_fold_fits_at_every_size(self):
        combos = [{}, {"focus": True}, {"stack": True}, {"log": True}, {"focus": True, "stack": True},
                  {"stack": True, "log": True}, {"focus": True, "stack": True, "log": True}, {"focus": True, "log": True}]
        drags = [{}, {"focus": 0.1, "stack": 0.9}, {"focus": 0.9, "stack": 0.05}]
        for size, scale in u6.SIZES:
            for state in (long_stack_state(), copy.deepcopy(load_state("main1_lands"))):
                for fold in combos:
                    for dr in drags:
                        gui = table(copy.deepcopy(state), size, scale, fold=fold, sizes=dr)
                        L = gui.L
                        parts = [r for r in (L.heads.get("focus"), L.stack, L.log) if r is not None and r.h > 0]
                        if gui.focus_shown():
                            parts.append(L.preview.union(L.caption))
                        for r in parts:
                            self.assertTrue(L.right.contains(r), (size, scale, fold, dr, r, L.right))
                        parts.sort(key=lambda r: r.top)
                        for a, b in zip(parts, parts[1:]):
                            self.assertLessEqual(a.bottom, b.top, (size, scale, fold, dr, a, b))


class ClickTests(unittest.TestCase):
    def test_the_arrows_fold_and_unfold_each_panel_and_send_nothing(self):
        gui = table()
        for name in ("stack", "log", "focus"):
            r = hit_rect(gui, "fold_" + name)
            self.assertIsNotNone(r, name)
            click(gui, r.center)
            self.assertTrue(gui.panel_fold[name], name)
            r = hit_rect(gui, "fold_" + name)
            click(gui, r.center)
            self.assertFalse(gui.panel_fold[name], name)
        self.assertEqual(gui.session.sent, [])

    def test_a_click_anywhere_on_a_folded_heading_opens_it(self):
        gui = table(fold={"log": True})
        r = gui.L.heads["log"]
        click(gui, (r.right - 40, r.centery))
        self.assertFalse(gui.panel_fold["log"])

    def test_no_room_beside_the_card_gives_a_hide_card_button(self):
        gui = table(size=(1000, 1600))
        L = gui.L
        self.assertIsNone(gui.focus_fold_rect())                # the card fills the column's width
        r = hit_rect(gui, "fold_focus")
        self.assertIsNotNone(r)
        self.assertTrue(L.stack.contains(r) or L.log.contains(r))
        self.assertFalse(L.preview.colliderect(r))
        click(gui, r.center)
        self.assertTrue(gui.panel_fold["focus"])

    def test_the_arrow_never_covers_the_card(self):
        for size, scale in u6.SIZES:
            gui = table(size=size, scale=scale)
            r = gui.focus_fold_rect()
            if r is not None:
                self.assertFalse(r.colliderect(gui.L.preview), (size, scale))
                self.assertTrue(gui.L.right.contains(r), (size, scale))


class DragTests(unittest.TestCase):
    def test_dragging_the_border_under_the_focus_card_shrinks_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            gui = table(settings_path=path)
            before = gui.L.preview.h
            s = seam(gui, "focus")
            drag(gui, s.center, (s.centerx, s.centery - 150))
            self.assertIsNotNone(gui.panel_size["focus"])
            self.assertLess(gui.L.preview.h, before - 100)
            self.assertEqual(gui.L.stack.top, gui.L.caption.bottom + gui.L.margin)
            self.assertIsNone(gui.seam_drag)
            with open(path, encoding="utf-8") as f:
                self.assertIsNotNone(json.load(f)["right_panels"]["focus"])
            self.assertEqual(gui.session.sent, [])

    def test_dragging_the_border_under_the_stack_expands_it(self):
        gui = table(long_stack_state())
        L0 = gui.L
        self.assertLessEqual(L0.stack.h, int(L0.right.h * ft.STACK_MAX_SHARE))
        s = seam(gui, "stack")
        drag(gui, s.center, (s.centerx, s.centery + 200))
        L = gui.L
        self.assertGreater(L.stack.h, L0.stack.h + 20)
        self.assertGreater(L.stack.h, int(L.right.h * ft.STACK_MAX_SHARE))
        self.assertEqual(L.stack.bottom, L.right.bottom - L.panel_head - L.margin)    # as far as the log's heading allows
        self.assertEqual(L.log.top, L.stack.bottom + L.margin)
        self.assertGreaterEqual(L.log.h, L.panel_head)          # the log keeps at least its heading

    def test_a_right_click_on_a_border_puts_it_back(self):
        gui = table(sizes={"focus": 0.2})
        s = seam(gui, "focus")
        click(gui, s.center, button=3)
        self.assertIsNone(gui.panel_size["focus"])
        self.assertEqual(gui.L.heads, {})

    def test_no_border_drags_under_a_dialog(self):
        gui = table()
        s = seam(gui, "focus")
        gui.modal = object()
        self.assertIsNone(gui.seam_at(s.center))


class LogScrollTests(unittest.TestCase):
    def test_the_log_shows_more_lines_with_the_stack_folded_and_still_scrolls(self):
        gui = table()
        h0 = gui.L.log.h
        gui.panel_fold["stack"] = True
        u6.draw(gui, 1)
        self.assertGreater(gui.L.log.h, h0)
        pos = gui.L.log.center
        gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0)))
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1))
        u6.draw(gui, 1)
        if gui.log_overflow:
            self.assertGreater(gui.log_scroll, 0)


if __name__ == "__main__":
    unittest.main()
