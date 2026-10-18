# SPDX-License-Identifier: GPL-3.0-or-later
"""Round HAND1: Arena-style hand cards (Karl, 13 Oct, the UI7 question: "(c) Arena-style hand").

With Card size above 100% (UI7's setting, Ctrl and + / -), the hand's cards are drawn Card size times taller, their tops where they
always were and the rest below the window's bottom edge; the card under the mouse rises until the whole card shows. The hand strip,
the action bar, the command zone and the battlefield keep their places; at 100% nothing changes (UI6's recorded layouts). A risen
card is drawn over the action bar, but its click area never leaves the hand strip, so the bar's buttons still take their clicks.

  RestTests     sizes, tops, what is hidden, click areas, the strip unchanged; 100% exactly as before
  HoverTests    the whole card shows; the bar keeps its clicks; a click on the card still picks it; leaving the strip drops it
  FitTests      every size and text size, 2-10 cards, nothing above the table or outside the window's sides
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

SIZES = ((1360, 840), (1920, 1080), (1100, 700), (2560, 1440), (4096, 1949))


def table(state="main1_lands", size=(1360, 840), scale=1.0, card=1.0, hand=None):
    st = copy.deepcopy(load_state(state))
    if hand is not None:
        me = next(p for p in st["players"] if p["id"] == st["me"])
        base = me["zones"]["hand"][0]
        me["zones"]["hand"] = [dict(base, id=5000 + k) for k in range(hand)]
    gui = ft.ForgeTable(FakeSession(st, load_log()), StubStore(), settings_path=None, window_size=size)
    gui.text_scale, gui.card_scale, gui.animations = scale, card, False
    frame(gui, 3)
    return gui


def hand_ids(gui):
    return [c["id"] for c in gui.session.me()["zones"]["hand"]]


def hits(gui, kind="card"):
    return {d["card"]["id"]: r for r, k, d in gui.hits if k == kind and "card" in d}


def hover(gui, cid):
    r = gui.card_rects[cid]
    pos = (r.left + 4, gui.L.hand.bottom - 4)               # the left edge: a full row overlaps each card's right side
    for _ in range(3):                                      # past round 23's 60 ms dwell before the hover switches
        move(gui, pos)
        frame(gui)
        time.sleep(0.07)
    frame(gui)
    return pos


class RestTests(unittest.TestCase):
    def test_at_100_percent_nothing_changes(self):
        a = table(card=1.0)
        for cid in hand_ids(a):
            self.assertEqual(a.card_rects[cid].h, a.L.hand_ch)
            self.assertTrue(a.L.hand.contains(a.card_rects[cid]), cid)

    def test_cards_are_card_size_times_taller_and_start_where_they_did(self):
        base = table(card=1.0)
        for card in (1.25, 1.5, 2.0):
            with self.subTest(card=card):
                g = table(card=card)
                self.assertEqual((g.L.hand, g.L.hand_ch, g.L.bar, g.L.cmd_rect), (base.L.hand, base.L.hand_ch, base.L.bar,
                                                                                  base.L.cmd_rect))
                for cid in hand_ids(g):
                    r = g.card_rects[cid]
                    self.assertEqual(r.h, int(round(g.L.hand_ch * card)), cid)
                    self.assertEqual(r.top, base.card_rects[cid].top, cid)
                    self.assertGreater(r.bottom, g.screen.get_height(), "the bottom of a resting card shows")

    def test_click_areas_stay_in_the_hand_strip(self):
        for card in (1.5, 2.0):
            g = table(card=card)
            got = hits(g)
            for cid in hand_ids(g):
                self.assertTrue(g.L.hand.contains(got[cid]), (card, cid, got[cid], g.L.hand))
                kind, data = g.hit_at((got[cid].left + 3, got[cid].centery))
                self.assertEqual((kind, data["card"]["id"]), ("card", cid))

    def test_at_100_percent_the_hover_still_pops_and_at_200_it_rises_instead(self):
        for card, pops in ((1.0, True), (2.0, False)):
            g = table(card=card)
            g.animations = True
            seen = []
            real = g.draw_hand_card_scaled

            def spy(c, x, y, w, h, lift, scale, real=real):
                seen.append(scale)
                return real(c, x, y, w, h, lift, scale)
            g.draw_hand_card_scaled = spy
            hover(g, hand_ids(g)[0])
            for _ in range(20):                                 # let the lift spring settle
                frame(g)
                time.sleep(0.02)
            self.assertEqual(any(sc > 1.01 for sc in seen), pops, (card, seen[-3:]))

    def test_the_command_zone_and_the_battlefield_do_not_move(self):
        a, b = table(card=1.0), table(card=2.0)
        self.assertEqual((a.L.cmd_rect, a.L.my_bf, a.L.cmd_ch), (b.L.cmd_rect, b.L.my_bf, b.L.cmd_ch))


class HoverTests(unittest.TestCase):
    def test_the_hovered_card_shows_whole(self):
        for size in ((1360, 840), (1920, 1080)):
            for card in (1.5, 2.0):
                with self.subTest(size=size, card=card):
                    g = table(size=size, card=card)
                    cid = hand_ids(g)[len(hand_ids(g)) // 2]
                    hover(g, cid)
                    r = g.card_rects[cid]
                    self.assertLessEqual(r.bottom, g.screen.get_height() - g.L.margin + 1, r)
                    self.assertGreaterEqual(r.top, g.L.main.top, r)

    def test_the_bar_keeps_its_clicks_under_a_risen_card(self):
        g = table(size=(1920, 1080), scale=2.0, card=2.0)
        buttons = [(r, d) for r, k, d in g.hits if k == "button"]
        self.assertTrue(buttons)
        found = False
        for cid in hand_ids(g):
            hover(g, cid)
            risen = g.card_rects[cid]
            for r, _d in [(r, d) for r, k, d in g.hits if k == "button" and r.colliderect(g.L.bar)]:
                over = r.clip(risen)
                if over.w > 2 and over.h > 2:
                    found = True
                    kind, _ = g.hit_at(over.center)
                    self.assertEqual(kind, "button", (cid, over))
        self.assertTrue(found, "no risen card covered a bar button in this scene - the test proves nothing")

    def test_a_click_on_the_risen_card_still_picks_it(self):
        g = table(card=2.0)
        cid = hand_ids(g)[0]
        pos = hover(g, cid)
        kind, data = g.hit_at(pos)
        self.assertEqual((kind, data["card"]["id"]), ("card", cid))

    def test_leaving_the_strip_lets_it_fall_back(self):
        g = table(card=2.0)
        cid = hand_ids(g)[0]
        top_rest = g.card_rects[cid].top
        hover(g, cid)
        self.assertLess(g.card_rects[cid].top, top_rest)
        for _ in range(3):
            move(g, g.L.bar.center)
            frame(g)
            time.sleep(0.07)
        frame(g, 2)
        self.assertEqual(g.card_rects[cid].top, top_rest)


class FitTests(unittest.TestCase):
    def test_every_size_every_hand(self):
        for size in SIZES:
            for scale in (1.0, 2.0):
                for n in (2, 7, 10):
                    with self.subTest(size=size, scale=scale, n=n):
                        g = table(size=size, scale=scale, card=2.0, hand=n)
                        W = g.screen.get_width()
                        for cid in hand_ids(g):
                            r = g.card_rects[cid]
                            self.assertGreaterEqual(r.left, g.L.hand.left - 1)
                            self.assertLessEqual(r.right, min(W, g.L.hand.right + 1))
                            self.assertGreaterEqual(r.top, g.L.hand.top)


if __name__ == "__main__":
    unittest.main()
