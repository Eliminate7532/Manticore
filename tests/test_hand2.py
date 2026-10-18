# SPDX-License-Identifier: GPL-3.0-or-later
"""Round HAND2: the command zone follows Card size, like the hand (Karl, 13 Oct, HAND1's question "Should the command zone follow
Card size too?": "Yes, like the hand").

With Card size above 100%, my commander cards are drawn Card size times bigger, Arena-style as round HAND1's hand: each card's top
where it always was, the rest below the window's bottom edge, and the part past the frame's right side hidden (as a hand card's
right side is under its neighbour). The card under the mouse rises until the whole card shows, over the action bar and the hand,
but its click area never leaves the frame. The frame, the bar, the hand strip and the battlefield keep their places; at 100%
nothing changes (UI6's recorded layouts). Opponents' command zones are not changed (pods size them by their row, UI5).

  RestTests     size, top, hidden part, click area, layout unchanged; 100% exactly as before; the tax tag
  HoverTests    whole when hovered; the bar and the hand keep their clicks; a click on it picks it; it drops back; partners
  FitTests      every size and text size: nothing outside the window, nothing raises; a commander elsewhere
"""
import copy
import os
import sys
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame

import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import frame, move

SIZES = ((1360, 840), (1920, 1080), (1100, 700), (1280, 720), (2560, 1440), (4096, 1949))
KINNAN = 57


def table(state="main1_lands", size=(1360, 840), scale=1.0, card=1.0, partner=False, casts=0, elsewhere=False, opp_cmd=False):
    st = copy.deepcopy(load_state(state))
    me = next(p for p in st["players"] if p["id"] == st["me"])
    if opp_cmd:                                                    # the recorded opponent's commander is on the battlefield
        opp = next(p for p in st["players"] if p["id"] != st["me"])
        opp["zones"]["command"] = [dict(me["zones"]["command"][0], id=900, name="Lathril, Blade of the Elves")]
        opp["commanders"] = [900]
        for z in opp["zones"]:
            if z != "command":
                opp["zones"][z] = [c for c in opp["zones"][z] if c.get("id") != 900]
    if partner:
        second = dict(me["zones"]["command"][0], id=58, name="Tymna the Weaver")
        me["zones"]["command"].append(second)
        me["commanders"] = [KINNAN, 58]
    if casts:
        me["commanderCasts"] = {str(KINNAN): casts}
    if elsewhere:
        me["zones"]["command"] = []
    gui = ft.ForgeTable(FakeSession(st, load_log()), StubStore(), settings_path=None, window_size=size)
    gui.text_scale, gui.card_scale, gui.animations = scale, card, False
    frame(gui, 3)
    return gui


def hits(gui):
    return {d["card"]["id"]: r for r, k, d in gui.hits if k == "card" and "card" in d}


def hover(gui, cid, x=None):
    r = gui.card_rects[cid]
    f = gui.L.cmd_rect
    pos = (x if x is not None else min(r.left + 6, f.right - 3), f.bottom - 6)
    for _ in range(3):
        move(gui, pos)
        frame(gui)
        time.sleep(0.02)
    frame(gui)
    return pos


def away(gui):
    for _ in range(3):
        move(gui, gui.L.my_bf.center)
        frame(gui)
    frame(gui)


class RestTests(unittest.TestCase):
    def test_at_100_percent_nothing_changes(self):
        g = table(card=1.0)
        r = g.card_rects[KINNAN]
        self.assertEqual(r.h, g.L.cmd_ch)
        self.assertTrue(g.L.cmd_rect.contains(r), (r, g.L.cmd_rect))
        self.assertEqual(hits(g)[KINNAN], r)

    def test_the_commander_is_card_size_times_bigger_and_starts_where_it_did(self):
        base = table(card=1.0)
        for card in (1.25, 1.5, 2.0):
            with self.subTest(card=card):
                g = table(card=card)
                self.assertEqual((g.L.cmd_rect, g.L.bar, g.L.hand, g.L.my_bf, g.L.cmd_ch),
                                 (base.L.cmd_rect, base.L.bar, base.L.hand, base.L.my_bf, base.L.cmd_ch))
                r = g.card_rects[KINNAN]
                self.assertEqual(r.h, int(round(g.L.cmd_ch * card)))
                self.assertEqual(r.w, int(r.h * ft.CARD_ASPECT))
                self.assertEqual(r.top, base.card_rects[KINNAN].top)
                self.assertGreater(r.bottom, g.screen.get_height(), "the bottom of a resting commander card shows")

    def test_the_click_area_stays_in_the_frame_and_picks_the_card(self):
        for card in (1.5, 2.0):
            g = table(card=card)
            h = hits(g)[KINNAN]
            self.assertTrue(g.L.cmd_rect.contains(h), (card, h, g.L.cmd_rect))
            kind, data = g.hit_at((h.centerx, h.bottom - 3))
            self.assertEqual((kind, data["card"]["id"]), ("card", KINNAN))

    def test_nothing_is_drawn_past_the_frames_side_at_rest(self):
        """The gap between the frame and the hand looks exactly as it does with no commander card in the frame (the same Card
        size, so the hand's shadows are the same), from the card's top down."""
        a = table(card=2.0)
        f = a.L.cmd_rect
        H = a.screen.get_height()
        r = a.card_rects[KINNAN]
        self.assertLess(f.right + 2, a.L.hand.x)
        self.assertGreater(r.right, a.L.hand.x, "the big card doesn't reach past the frame - the test proves nothing")
        cols = range(f.right, a.L.hand.x)
        with_card = [[a.screen.get_at((x, y)) for y in range(r.top, H)] for x in cols]   # read now: every table shares one window
        b = table(card=2.0, elsewhere=True)
        without = [[b.screen.get_at((x, y)) for y in range(r.top, H)] for x in cols]
        self.assertEqual(with_card, without)

    def test_the_tax_tag_sits_on_what_shows(self):
        g = table(card=2.0, casts=1)
        seen = []
        real = g.draw_tax_tag
        g.draw_tax_tag = lambda n, cx, bottom, room: seen.append((n, cx, bottom, room)) or real(n, cx, bottom, room)
        frame(g)
        self.assertEqual(len(seen), 1)
        n, cx, bottom, room = seen[0]
        self.assertEqual(n, 1)
        self.assertLessEqual(bottom, g.L.cmd_rect.bottom)
        self.assertTrue(g.L.cmd_rect.left < cx < g.L.cmd_rect.right, (cx, g.L.cmd_rect))
        self.assertLessEqual(room, g.L.cmd_rect.w)

    def test_opponents_command_zones_are_not_changed(self):
        a, b = table(card=1.0, opp_cmd=True), table(card=2.0, opp_cmd=True)
        opp = [c["id"] for p in a.session.opponents() for c in p["zones"].get("command", [])]
        self.assertTrue(opp)
        for cid in opp:
            self.assertEqual(a.card_rects[cid], b.card_rects[cid])


class HoverTests(unittest.TestCase):
    def test_the_hovered_commander_shows_whole(self):
        for size in ((1360, 840), (1920, 1080), (1100, 700)):
            for scale in (1.0, 2.0):
                for card in (1.5, 2.0):
                    with self.subTest(size=size, scale=scale, card=card):
                        g = table(size=size, scale=scale, card=card)
                        rest = g.card_rects[KINNAN].top
                        hover(g, KINNAN)
                        r = g.card_rects[KINNAN]
                        W, H = g.screen.get_size()
                        self.assertLess(r.top, rest)
                        self.assertLessEqual(r.bottom, H - g.L.margin + 1, r)
                        self.assertGreaterEqual(r.top, 0, r)
                        self.assertLessEqual(r.right, W, r)
                        self.assertEqual(r.h, int(round(g.L.cmd_ch * card)))

    def test_it_is_drawn_over_the_bar(self):
        a = table(card=2.0)
        away(a)
        before = a.screen.copy()
        hover(a, KINNAN)
        r = a.card_rects[KINNAN].clip(a.L.bar)
        self.assertGreater(r.w * r.h, 0, "the risen card doesn't reach the bar in this scene - the test proves nothing")
        changed = sum(1 for x in range(r.left, r.right, 3) for y in range(r.top, r.bottom, 3)
                      if before.get_at((x, y)) != a.screen.get_at((x, y)))
        self.assertGreater(changed, 0)

    def test_it_is_drawn_after_the_bar_and_the_hand_so_nothing_covers_it(self):
        g = table(card=2.0)
        hover(g, KINNAN)
        order = []
        bar, hand, card = g.draw_action_bar, g.draw_hand, g.draw_card_at
        g.draw_action_bar = lambda *a, **k: order.append("bar") or bar(*a, **k)
        g.draw_hand = lambda *a, **k: order.append("hand") or hand(*a, **k)

        def spy(c, *a, **k):
            r = card(c, *a, **k)
            if c.get("id") == KINNAN:
                order.append(("commander", r.top))
            return r
        g.draw_card_at = spy
        frame(g)
        last = max(i for i, o in enumerate(order) if isinstance(o, tuple))
        self.assertGreater(last, order.index("bar"))
        self.assertGreater(last, order.index("hand"))
        self.assertLess(order[last][1], g.card_rects[KINNAN].bottom)           # the risen one is the one drawn last
        self.assertEqual(order[last][1], g.card_rects[KINNAN].top)

    def test_the_bar_and_the_hand_keep_their_clicks_under_it(self):
        g = table(size=(1920, 1080), scale=2.0, card=2.0)
        hover(g, KINNAN)
        risen = g.card_rects[KINNAN]
        checked = 0
        for area in (g.L.bar, g.L.hand):
            over = risen.clip(area)
            if over.w > 4 and over.h > 4:
                checked += 1
                for pt in (over.center, (over.left + 2, over.top + 2), (over.right - 3, over.bottom - 3)):
                    kind, data = g.hit_at(pt)
                    self.assertFalse(kind == "card" and data["card"]["id"] == KINNAN, (area, pt))
        self.assertGreater(checked, 0, "the risen card covers neither the bar nor the hand - the test proves nothing")

    def test_a_click_on_the_risen_card_picks_it(self):
        g = table(card=2.0)
        pos = hover(g, KINNAN)
        kind, data = g.hit_at(pos)
        self.assertEqual((kind, data["card"]["id"]), ("card", KINNAN))
        kind, data = g.hit_at((g.L.cmd_rect.centerx, g.L.cmd_rect.top + 4))     # its upper part is in the frame too
        self.assertEqual((kind, data["card"]["id"]), ("card", KINNAN))

    def test_leaving_the_frame_lets_it_fall_back(self):
        g = table(card=2.0)
        rest = g.card_rects[KINNAN].top
        hover(g, KINNAN)
        self.assertLess(g.card_rects[KINNAN].top, rest)
        away(g)
        self.assertEqual(g.card_rects[KINNAN].top, rest)
        h = hits(g)[KINNAN]
        self.assertTrue(g.L.cmd_rect.contains(h))

    def test_with_animations_it_rises_smoothly_and_settles(self):
        g = table(card=2.0)
        g.animations = True
        rest = g.card_rects[KINNAN].top
        tops = []
        pos = (g.L.cmd_rect.left + 6, g.L.cmd_rect.bottom - 6)
        move(g, pos)
        for _ in range(40):
            frame(g)
            tops.append(g.card_rects[KINNAN].top)
            time.sleep(0.01)
        self.assertLess(tops[-1], rest)
        self.assertGreater(len(set(tops)), 2, "it jumped instead of rising")

    def test_partners_overlap_and_each_rises_when_hovered(self):
        g = table(card=2.0, partner=True)
        a, b = g.card_rects[KINNAN], g.card_rects[58]
        f = g.L.cmd_rect
        self.assertLess(a.left, b.left)
        self.assertTrue(f.contains(hits(g)[KINNAN]) and f.contains(hits(g)[58]))
        hover(g, KINNAN, x=a.left + 3)
        self.assertLess(g.card_rects[KINNAN].top, a.top)
        self.assertEqual(g.card_rects[58].top, b.top)
        kind, data = g.hit_at((a.left + 3, f.bottom - 6))
        self.assertEqual(data["card"]["id"], KINNAN)
        away(g)
        hover(g, 58, x=min(b.left + 6, f.right - 3))
        self.assertLess(g.card_rects[58].top, b.top)
        self.assertEqual(g.card_rects[KINNAN].top, a.top)


class ReviewTests(unittest.TestCase):
    """What a separate review of the round found (13 Oct), each red before its fix."""

    def test_a_partner_somewhere_else_keeps_its_column(self):
        g = table(card=2.0, partner=True)
        st = copy.deepcopy(g.session.state)
        me = next(p for p in st["players"] if p["id"] == st["me"])
        me["zones"]["command"] = [c for c in me["zones"]["command"] if c["id"] != 58]       # Tymna is "elsewhere"
        g.session.handle(st)
        frame(g, 2)
        f = g.L.cmd_rect
        rest = g.card_rects[KINNAN].top
        mid = (f.left + f.right) // 2
        place = (min(mid + 8, f.right - 4), f.bottom - 8)                                     # the placeholder's column
        kind, data = g.hit_at(place)
        self.assertFalse(kind == "card" and data["card"]["id"] == KINNAN, "a click on Tymna's place picks Kinnan")
        for _ in range(3):
            move(g, place)
        frame(g)
        self.assertEqual(g.card_rects[KINNAN].top, rest, "hovering Tymna's place raised Kinnan")

    def test_a_partners_tax_tag_stays_in_its_own_column(self):
        g = table(card=2.0, partner=True, casts=1)
        seen = []
        real = g.draw_tax_tag
        g.draw_tax_tag = lambda n, cx, bottom, room: seen.append((cx, room)) or real(n, cx, bottom, room)
        frame(g)
        f = g.L.cmd_rect
        b = g.card_rects[58]
        self.assertEqual(len(seen), 1)
        cx, room = seen[0]
        self.assertLess(cx, b.left, "Kinnan's tag sits under Tymna")
        self.assertLessEqual(room, b.left - f.left)

    def test_a_cast_commander_does_not_come_back_risen(self):
        g = table(card=2.0)
        g.animations = True
        rest = g.card_rects[KINNAN].top
        pos = hover(g, KINNAN)
        for _ in range(30):
            move(g, pos)
            time.sleep(0.01)
        self.assertLess(g.card_rects[KINNAN].top, rest - 20)                 # risen
        st = copy.deepcopy(g.session.state)
        me = next(p for p in st["players"] if p["id"] == st["me"])
        card = me["zones"]["command"].pop()
        me["zones"]["battlefield"].append(card)                              # cast while risen
        g.session.handle(st)
        for _ in range(40):
            frame(g)
            time.sleep(0.01)
        m = g.motion.get(KINNAN)
        self.assertTrue(m is None or (abs(m.lift.target) < 0.01 and abs(m.lift.value) < 1), "it is still lifted")
        st2 = copy.deepcopy(st)
        me2 = next(p for p in st2["players"] if p["id"] == st2["me"])
        me2["zones"]["battlefield"] = [c for c in me2["zones"]["battlefield"] if c["id"] != KINNAN]
        me2["zones"]["command"].append(card)                                 # back to the command zone
        g.session.handle(st2)
        move(g, g.L.my_bf.center)
        frame(g)
        self.assertLessEqual(abs(g.card_rects[KINNAN].top - rest), 1)

    def test_a_commander_in_my_hand_still_rises_there(self):
        g = table(card=2.0)
        g.animations = True
        st = copy.deepcopy(g.session.state)
        me = next(p for p in st["players"] if p["id"] == st["me"])
        me["zones"]["hand"].append(me["zones"]["command"].pop())              # returned to my hand
        g.session.handle(st)
        frame(g, 2)
        rest = g.card_rects[KINNAN].top
        r = g.card_rects[KINNAN]
        for _ in range(40):
            move(g, (r.left + 4, g.L.hand.bottom - 4))
            time.sleep(0.01)
        self.assertLess(g.card_rects[KINNAN].top, rest - 20)

    def test_card_size_back_to_100_lets_a_risen_card_go(self):
        g = table(card=2.0)
        g.animations = True
        pos = hover(g, KINNAN)
        for _ in range(20):
            move(g, pos)
        g.card_scale = 1.0
        for _ in range(40):
            frame(g)
            time.sleep(0.01)
        m = g.motion.get(KINNAN)
        self.assertTrue(m is None or abs(m.lift.target) < 0.01)


class FitTests(unittest.TestCase):
    def test_every_size_and_text_size(self):
        for size in SIZES:
            for scale in (1.0, 2.0):
                for card in (1.25, 2.0):
                    for partner in (False, True):
                        with self.subTest(size=size, scale=scale, card=card, partner=partner):
                            g = table(size=size, scale=scale, card=card, partner=partner)
                            W, H = g.screen.get_size()
                            f = g.L.cmd_rect
                            for cid in ([KINNAN, 58] if partner else [KINNAN]):
                                r = g.card_rects[cid]
                                self.assertGreaterEqual(r.left, f.left, (cid, r, f))
                                self.assertGreaterEqual(r.top, f.top, (cid, r, f))
                                self.assertTrue(f.contains(hits(g)[cid]), (cid, hits(g)[cid], f))
                            hover(g, KINNAN, x=g.card_rects[KINNAN].left + 3)
                            r = g.card_rects[KINNAN]
                            self.assertTrue(pygame.Rect(0, 0, W, H).contains(r), (r, (W, H)))

    def test_a_commander_somewhere_else_shows_where(self):
        for card in (1.0, 2.0):
            g = table(card=card, elsewhere=True)
            self.assertNotIn(KINNAN, hits(g))
            frame(g)


if __name__ == "__main__":
    unittest.main()
