# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SHOPMSG2 - Karl, 8 Nov 2026 (claude/DECISIONS_2026-11-08.md: "Grow the strip + fix gallery", not SHOPMSG1's panel over
the lists): a shop message longer than two lines makes the strip under the lists taller (up to MESSAGE_MAX_LINES), the lists' area
shrinks by as much, and the Singles and Packs galleries draw only the rows that fit whole in what is left - never under the strip,
never past the window. The message is the longest the shop can say: a sale that took a card out of two decks, with the widest
name in the Adventure's card pool (test_shopmsg1.longest_name)."""
import unittest

import rpg_screens as rs
from tests import test_rpg8 as r8
from tests.test_forge_table import frame
from tests.test_rpg14b import StyleBase
from tests import test_shopmsg1 as m1
from tests.test_shopmsg1 import SIZES, longest_name

GALLERIES = ("singles", "packs")


class GrownStripTests(StyleBase):
    town = m1.ShopMessageTests.town
    messages = m1.ShopMessageTests.messages

    def longest(self, f, dg):
        return dict(self.messages(f, longest_name(dg)))["sell_two_decks"]

    def test_galleries_and_lists_stay_above_the_grown_strip(self):
        r8.new_game(None)
        for size, scale in SIZES:
            g, f, dg = self.town(size, scale)
            text = self.longest(f, dg)
            win = g.screen.get_rect()
            for tab in ("singles", "packs", "sell", "lands"):
                with self.subTest(size=size, scale=scale, tab=tab):
                    f.tab = tab
                    f.say(text, rs.GREEN)
                    frame(g)
                    strip = f.msg_strip
                    self.assertTrue(win.contains(strip), strip)
                    drawn = [r for r, kind, _d in f.rows if kind in ("single", "shelf")] if tab in GALLERIES else \
                        [r for r, _k, _d in f.rows]
                    if tab in GALLERIES:
                        self.assertTrue(drawn, "the gallery drew no card")
                    for r in drawn:
                        self.assertFalse(r.colliderect(strip), f"{tab}: {r} runs under the message strip {strip}")
                        self.assertTrue(win.contains(r), f"{tab}: {r} past the window")
                    for r, name in f.buttons:
                        self.assertFalse(r.colliderect(strip), f"{name} {r} under the strip {strip}")
                        self.assertTrue(win.contains(r), f"{name} {r} past the window")
                    if tab == "singles":
                        self.assertTrue(any(n.startswith("buy:") for _r, n in f.buttons), "Buy not reachable")

    def test_the_strip_grows_by_the_message_and_holds_every_line(self):
        r8.new_game(None)
        g, f, dg = self.town((1360, 840), 2.0)
        f.tab = "singles"
        f.say("Bought Opt for 5 gold.", rs.GREEN)
        frame(g)
        short = f.msg_strip
        lh = dg.font("small", True).get_height()
        f.say(self.longest(f, dg), rs.GREEN)
        frame(g)
        lines = f.message_lines(dg, f.rect.w - 2 * f.pad, f.MESSAGE_MAX_LINES)
        self.assertGreater(len(lines), 2)
        self.assertEqual(f.msg_strip.bottom, short.bottom)
        self.assertEqual(f.msg_strip.h - short.h, (len(lines) - 2) * lh)
        self.assertGreaterEqual(f.msg_strip.h, len(lines) * lh)

    def test_a_message_past_the_maximum_stops_growing_the_strip(self):
        r8.new_game(None)
        g, f, dg = self.town((1360, 840), 2.0)
        f.say(" ".join(["word"] * 400), rs.GREEN)
        frame(g)
        lh = dg.font("small", True).get_height()
        self.assertLessEqual(f.msg_strip.h, f.MESSAGE_MAX_LINES * lh + 1)
        self.assertGreaterEqual(f.msg_strip.y, f.rect.y)

    def test_the_gallery_draws_fewer_rows_then_scrolls_when_the_strip_grows(self):
        """At 1360x840 @200% the singles show at least one whole row either way, and all of them can be reached by scrolling."""
        r8.new_game(None)
        g, f, dg = self.town((1360, 840), 2.0)
        f.tab = "singles"
        f.say(self.longest(f, dg), rs.GREEN)
        frame(g)
        fit, count = f._fits["town:singles"]
        self.assertGreaterEqual(fit, 1)
        seen = set()
        for _ in range(count + 2):
            seen |= {d for _r, k, d in f.rows if k == "single"}
            f.scroll["town:singles"] = f.scroll.get("town:singles", 0) + 1
            frame(g)
        self.assertEqual(seen, {o.slot for o in f.shop.singles()})


class GalleryShapeTests(unittest.TestCase):
    def test_only_whole_rows_are_counted_and_the_words_get_their_column(self):
        for fs in (1.0, 1.5, 2.0):
            for aspect in (rs.CARD_ASPECT, rs.pack_art.ASPECT):
                for width in (60, 300, 900, 2000):
                    for height in range(0, 900, 7):
                        label_h, label_w, gap = int(40 * fs), int(80 * fs), int(8 * fs)
                        cw, ch, pitch, cols, row_h, rows, fit = rs.gallery_shape(height, width, label_h, label_w, gap, fs,
                                                                                 aspect, int(300 * fs), 12)
                        with self.subTest(fs=fs, aspect=aspect, width=width, height=height):
                            self.assertLessEqual(fit * row_h, height)           # no row past the area's bottom
                            self.assertEqual(row_h, ch + label_h + gap)
                            self.assertLessEqual(cw, pitch)
                            self.assertTrue(pitch >= label_w or pitch == max(8, width))
                            self.assertTrue(cols == 1 or cols * pitch + (cols - 1) * gap <= width)
                            self.assertEqual(rows, (12 + cols - 1) // cols)

    def test_too_short_for_one_row_counts_none(self):
        self.assertEqual(rs.gallery_shape(50, 900, 80, 160, 16, 2.0, rs.CARD_ASPECT, 600, 12)[-1], 0)


if __name__ == "__main__":
    unittest.main()
