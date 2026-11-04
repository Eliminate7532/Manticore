# SPDX-License-Identifier: GPL-3.0-or-later
"""Task bug-sell-scroll-2 - Karl, 3 Nov 2026 (0.28.53.14, Surface Pro 11, window 2880x1920): "It's referring to only the
(alphabetically) top visible cards in my adventure deck. I should be able to scroll down this list to see all." He tried a finger
drag, a pen drag and the arrow keys; none moved the Sell list, and nothing on the screen said the list went on.

What these tests found on main 06a368e (docs/rounds/BUG-SELL-SCROLL-2.md has the table):
  H1 (no sign of more): TRUE at 150% and 200% text - the "1-13 of 87" note was dropped because the Sell title left no room.
  H2 ("everything fits"): FALSE - the list knew it scrolled at every size (its pan view was registered).
  H3 (arrow keys): TRUE - Up/Down/PageUp/PageDown/Home/End did nothing on any Adventure list.
  H4 (touch and pen): a finger whose touches SDL does NOT also turn into mouse events did nothing (nothing read FINGER* events);
     what SDL really sends on the Surface is unknown here - tools/input_probe.py shows it. Mouse events from a pen, and from a
     finger with touch=True, did drag (as SELL1's test).
Now: every Adventure list has a scroll bar you can grab with a finger, the count is always shown, the keys move it, and FINGER*
events drag it when SDL doesn't make mouse events from them."""
import unittest

import pygame

import rpg_screens as rs
from tests import test_rpg8 as r8
from tests.test_forge_table import frame
from tests.test_rpg14b import StyleBase, Spy
from unittest import mock

SURFACE = (2880, 1920)
SCALES = (1.0, 1.5, 2.0)


def bar_of(f, key):
    """The scroll bar list `key` drew this frame (None when it has none)."""
    got = f._bars_drawn.get(key)
    return got[0] if got else None


def ev(kind, **kw):
    return pygame.event.Event(kind, **kw)


class Base(StyleBase):
    EXTRA = 55          # cards added to a new game's 37 sellable ones: about 90 rows, as Karl's 67-card deck plus a few packs

    def town(self, size=SURFACE, scale=1.0, tab="sell"):
        g = self.gui(size, scale)
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        coll = f.game.save.collection
        added = 0
        for name in sorted(f.game.pool.names()):
            if added >= self.EXTRA:
                break
            if coll.count(name) or coll.is_free(name):
                continue
            try:
                if f.shop.sell_price(name) < 1:
                    continue
            except Exception:
                continue
            coll.add(name, 1)
            added += 1
        f.tab = tab
        frame(g, 2)
        return g, f

    def view(self, f, key="town:sell"):
        return f._pan_views[key]

    def fit(self, f, key="town:sell"):
        area, _s, step = self.view(f, key)
        return area.h // step

    # ---- the ways Karl tried, as SDL sends them --------------------------------------------------------------------------------
    @staticmethod
    def finger(g, kind, pos, fid=0):
        w, h = g.screen.get_size()
        return ev(kind, x=pos[0] / w, y=pos[1] / h, dx=0.0, dy=0.0, touch_id=1, finger_id=fid, pressure=1.0)

    def finger_drag(self, g, start, dy, synth):
        """A finger drag of dy px. synth: SDL also sends the mouse events it makes from the touch (touch=True), BEFORE each
        finger event (SDL_touch.c sends the mouse ones first)."""
        steps = 8
        if synth:
            g.handle_event(ev(pygame.MOUSEMOTION, pos=start, rel=(0, 0), buttons=(0, 0, 0), touch=True))
            g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=start, button=1, touch=True))
        g.handle_event(self.finger(g, pygame.FINGERDOWN, start))
        for k in range(1, steps + 1):
            p = (start[0], start[1] + dy * k // steps)
            if synth:
                g.handle_event(ev(pygame.MOUSEMOTION, pos=p, rel=(0, dy // steps), buttons=(1, 0, 0), touch=True))
            g.handle_event(self.finger(g, pygame.FINGERMOTION, p))
        end = (start[0], start[1] + dy)
        if synth:
            g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=end, button=1, touch=True))
        g.handle_event(self.finger(g, pygame.FINGERUP, end))
        frame(g)

    def mouse_drag(self, g, start, dy):
        """A pen (or the mouse): plain mouse events, touch=False."""
        g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=start, button=1, touch=False))
        for k in range(1, 9):
            g.handle_event(ev(pygame.MOUSEMOTION, pos=(start[0], start[1] + dy * k // 8), rel=(0, 0), buttons=(1, 0, 0),
                              touch=False))
        g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=(start[0], start[1] + dy), button=1, touch=False))
        frame(g)

    def key(self, g, k):
        g.handle_event(ev(pygame.KEYDOWN, key=k, mod=0, unicode="", scancode=0))
        frame(g)

    def start_point(self, f, key="town:sell"):
        area, _s, step = self.view(f, key)
        return (area.x + area.w // 3, area.bottom - step // 2)


class RedTests(Base):
    """Each of these failed on main 06a368e (the hypothesis it shows is in its name)."""

    def test_h1_the_count_is_shown_at_every_text_size(self):
        r8.new_game(None)
        for scale in SCALES:
            with self.subTest(scale=scale):
                g, f = self.town(scale=scale)
                n = len(f.sell_list())
                self.assertGreater(n, 80)
                spy = Spy()
                with mock.patch.object(rs.gfx, "draw_text", spy):
                    frame(g)
                shown = [t for t, _f, _c, _r in spy.calls]
                self.assertTrue(any(f"of {n}" in t for t in shown), shown[:8])
                self.close(g)

    def test_h3_arrow_keys_page_keys_home_and_end_move_the_list(self):
        r8.new_game(None)
        for scale in SCALES:
            with self.subTest(scale=scale):
                g, f = self.town(scale=scale)
                n, fit = len(f.sell_list()), self.fit(f)
                self.key(g, pygame.K_DOWN)
                self.assertEqual(f.scroll["town:sell"], 1)
                self.key(g, pygame.K_UP)
                self.assertEqual(f.scroll["town:sell"], 0)
                self.key(g, pygame.K_PAGEDOWN)
                self.assertEqual(f.scroll["town:sell"], max(1, fit - 1))
                self.key(g, pygame.K_PAGEUP)
                self.assertEqual(f.scroll["town:sell"], 0)
                self.key(g, pygame.K_END)
                self.assertEqual(f.scroll["town:sell"], n - fit)
                self.key(g, pygame.K_HOME)
                self.assertEqual(f.scroll["town:sell"], 0)
                self.assertEqual(f.stage, "town")                 # no key left the town
                self.close(g)

    def test_h4_a_finger_that_sdl_does_not_turn_into_mouse_events_drags_the_list(self):
        r8.new_game(None)
        for scale in SCALES:
            with self.subTest(scale=scale):
                g, f = self.town(scale=scale)
                step = self.view(f)[2]
                self.finger_drag(g, self.start_point(f), -4 * step, synth=False)
                self.assertEqual(f.scroll["town:sell"], 4)
                self.assertIsNone(f.message)                      # nothing sold by the drag
                self.close(g)

    def test_h4_a_touch_motion_that_says_no_button_is_held_still_drags(self):
        """Not seen yet, only possible: SDL keeps a touch's button state apart from the mouse's, and a motion event built from the
        mouse's state would say no button is held - main took that as "released outside the window" and dropped the drag."""
        r8.new_game(None)
        g, f = self.town(scale=1.5)
        start = self.start_point(f)
        step = self.view(f)[2]
        g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=start, button=1, touch=True))
        for k in range(1, 9):
            g.handle_event(ev(pygame.MOUSEMOTION, pos=(start[0], start[1] - 4 * step * k // 8), rel=(0, 0),
                              buttons=(0, 0, 0), touch=True))
        g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=(start[0], start[1] - 4 * step), button=1, touch=True))
        frame(g)
        self.assertEqual(f.scroll["town:sell"], 4)

    def test_a_scroll_bar_a_finger_can_grab(self):
        r8.new_game(None)
        for scale in SCALES:
            with self.subTest(scale=scale):
                g, f = self.town(scale=scale)
                n, fit = len(f.sell_list()), self.fit(f)
                bar = bar_of(f, "town:sell")
                self.assertIsNotNone(bar)
                self.assertTrue(bar.needed)
                self.assertGreaterEqual(bar.track.w, 64)          # about 6 mm on the Surface's 267 dpi screen
                area = self.view(f)[0]
                self.assertFalse(area.colliderect(bar.track))    # the rows (and their Sell buttons) stay clear of it
                # drag the handle to the bottom with a finger, as SDL sends it (both kinds of event)
                h = bar.handle
                self.finger_drag(g, h.center, bar.track.bottom - h.centery + 10, synth=True)
                self.assertEqual(f.scroll["town:sell"], n - fit)
                # a tap on the groove above the handle: a page back
                bar = bar_of(f, "town:sell")
                g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=(bar.track.centerx, bar.track.y + 4), button=1))
                g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=(bar.track.centerx, bar.track.y + 4), button=1))
                frame(g)
                self.assertTrue(n - 2 * fit <= f.scroll["town:sell"] < n - fit, f.scroll["town:sell"])    # the bar's page (UI8)
                self.assertIsNone(f.message)                      # nothing sold
                self.close(g)


class StillWorkedTests(Base):
    """These passed on main too: the pen (mouse events), a finger SDL turns into mouse events, the wheel - kept so they stay so."""

    def test_a_pen_drag_and_the_wheel(self):
        r8.new_game(None)
        for scale in SCALES:
            with self.subTest(scale=scale):
                g, f = self.town(scale=scale)
                step = self.view(f)[2]
                self.mouse_drag(g, self.start_point(f), -4 * step)
                self.assertEqual(f.scroll["town:sell"], 4)
                g.mouse = self.view(f)[0].center
                g.handle_event(ev(pygame.MOUSEWHEEL, x=0, y=-1, flipped=False, precise_x=0.0, precise_y=-1.0))
                frame(g)
                self.assertEqual(f.scroll["town:sell"], 4 + rs.WHEEL_ROWS)
                self.assertIsNone(f.message)
                self.close(g)

    def test_a_finger_sdl_turns_into_mouse_events_scrolls_once_not_twice(self):
        r8.new_game(None)
        g, f = self.town(scale=1.5)
        step = self.view(f)[2]
        self.finger_drag(g, self.start_point(f), -4 * step, synth=True)
        self.assertEqual(f.scroll["town:sell"], 4)
        self.assertIsNone(f.message)


class TapTests(Base):
    """A press that doesn't move is still a click (SELL1's rule) - with a finger, a pen and the mouse."""

    def sell_button(self, f):
        return next(((r, n[5:]) for r, n in f.buttons if n.startswith("sell:")), (None, None))

    def test_a_tap_sells_one_copy_every_way(self):
        r8.new_game(None)
        g, f = self.town(scale=1.5)
        for how in ("finger only", "finger as Windows sends it", "pen"):
            with self.subTest(how=how):
                rect, name = self.sell_button(f)
                have = f.game.save.collection.count(name)
                p = rect.center
                if how == "pen":
                    g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=p, button=1))
                    g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=p, button=1))
                else:
                    synth = how != "finger only"
                    if synth:
                        g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=p, button=1, touch=True))
                    g.handle_event(self.finger(g, pygame.FINGERDOWN, p))
                    if synth:
                        g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=p, button=1, touch=True))
                    g.handle_event(self.finger(g, pygame.FINGERUP, p))
                frame(g)
                self.assertEqual(f.game.save.collection.count(name), have - 1, how)
                f.message = None
                g.modal._last_tap = None

    def test_a_finger_tap_as_windows_sends_it_acts_once(self):
        """SDL sends a touch twice - as mouse events (touch=True) and as finger events: only one of them may act. A tap on the
        scroll bar's groove (outside the list's rows, so it acts on the press) pages once, not twice."""
        r8.new_game(None)
        g, f = self.town(scale=1.0)
        bar = bar_of(f, "town:sell")
        p = (bar.track.centerx, bar.track.bottom - 4)
        g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=p, button=1, touch=True))
        g.handle_event(self.finger(g, pygame.FINGERDOWN, p))
        g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=p, button=1, touch=True))
        g.handle_event(self.finger(g, pygame.FINGERUP, p))
        frame(g)
        once = f.scroll["town:sell"]
        self.assertGreater(once, 0)
        f.scroll["town:sell"] = 0
        frame(g)
        g.modal._last_tap = None
        g.handle_event(ev(pygame.MOUSEBUTTONDOWN, pos=p, button=1))      # the same tap with the mouse: the page it should be
        g.handle_event(ev(pygame.MOUSEBUTTONUP, pos=p, button=1))
        frame(g)
        self.assertEqual(once, f.scroll["town:sell"])

    def test_a_tap_on_a_row_away_from_its_button_sells_nothing(self):
        r8.new_game(None)
        g, f = self.town(scale=1.0)
        gold = f.game.save.gold
        p = (self.view(f)[0].x + 20, self.view(f)[0].y + self.view(f)[2] // 2)
        g.handle_event(self.finger(g, pygame.FINGERDOWN, p))
        g.handle_event(self.finger(g, pygame.FINGERUP, p))
        frame(g)
        self.assertEqual((f.game.save.gold, f.scroll["town:sell"]), (gold, 0))


class EveryListTests(Base):
    """Item 2 and 3: Singles, Packs, Basic lands, Sell, the deck editor's pool and deck list, the Collection."""

    def lists(self, g, f):
        def town(tab):
            def go():
                if f.stage != "town":
                    f.enter_town(g, f.game.world.start_town)
                f.tab = tab
                return "town:" + tab
            return go

        def pool():
            f.enter_town(g, f.game.world.start_town)
            f.open_deck(g, "town")
            return "deckpool"

        def decklist():
            if f.stage != "deck":
                pool()
            return "decklist"

        def coll():
            f.set_stage("collection")
            return "collection"
        return [("singles", town("singles")), ("packs", town("packs")), ("lands", town("lands")), ("sell", town("sell")),
                ("pool", pool), ("decklist", decklist), ("collection", coll)]

    def test_every_list_that_scrolls_has_a_bar_and_moves_with_keys_finger_pen_and_wheel(self):
        r8.new_game(None)
        for size, scale in ((SURFACE, 1.0), (SURFACE, 2.0), ((1920, 1080), 1.0), ((1360, 840), 1.0)):
            g, f = self.town(size, scale)
            scrolled = set()
            for what, go in self.lists(g, f):
                with self.subTest(size=size, scale=scale, list=what):
                    key = go()
                    frame(g, 2)
                    area, scrolls, step = f._pan_views[key]
                    bar = bar_of(f, key)
                    if not scrolls:
                        self.assertTrue(bar is None or not bar.needed)
                        continue
                    scrolled.add(what)
                    self.assertIsNotNone(bar, "no scroll bar")
                    self.assertFalse(area.colliderect(bar.track))
                    f.scroll[key] = 0
                    fit, count = f._fits[key]
                    last = count - fit                            # the first row shown at the end
                    g.mouse = area.center                         # the deck list's keys follow the mouse, as its wheel does
                    self.key(g, pygame.K_DOWN)
                    self.assertEqual(f.scroll[key], 1, "Down")
                    self.key(g, pygame.K_HOME)
                    self.assertEqual(f.scroll[key], 0, "Home")
                    start = (area.x + area.w // 3, area.bottom - step // 2 - 2)
                    self.finger_drag(g, start, -2 * step, synth=False)
                    self.assertEqual(f.scroll[key], min(2, last), "finger")
                    self.mouse_drag(g, start, -2 * step)
                    self.assertEqual(f.scroll[key], min(4, last), "pen")
                    self.key(g, pygame.K_END)
                    end = f.scroll[key]
                    self.assertEqual(end, last)
                    self.key(g, pygame.K_DOWN)
                    self.assertEqual(f.scroll[key], end, "past the end")
            self.assertTrue({"sell", "pool"} <= scrolled, scrolled)
            if size == (1360, 840):
                self.assertIn("collection", scrolled)            # (at the Surface's size the Collection's 4 columns fit it all)
            self.close(g)


if __name__ == "__main__":
    unittest.main()
