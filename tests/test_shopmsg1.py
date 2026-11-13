# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SHOPMSG1 - Karl, 7 Nov 2026 ("Queue it", claude/DECISIONS_2026-11-07.md), found by STARTER1: the Adventure shop's message
was two lines whatever it said, so a long sale ("Sold one Elvish Visionary for 37 gold. Taken out of '...': 59 cards now. Add cards
before you duel.") lost its end at 1360x840 @200% text. Now the message is wrapped to the window's width and every line is drawn:
two lines in the strip kept for it under the lists (as before), and (Round SHOPMSG2, Karl 8 Nov 2026: "Grow the strip + fix
gallery") a longer message makes that strip taller and the lists' area shorter (up to rpg_screens.AdventureFlow.MESSAGE_MAX_LINES;
past that the last line ends in "..."). SHOPMSG1's panel over the lists, and its tap-to-put-away, are gone (tests/test_shopmsg2.py).

The card name used is the longest name in the Adventure's card pool (rpg.cardpool.load()), measured in pixels in the message's
own font, DawnLike's (longest_name)."""
import unittest
from unittest import mock

import rpg_screens as rs
from rpg import cardpool
from tests import test_rpg8 as r8
from tests.test_forge_table import frame
from tests.test_rpg14b import Spy, StyleBase

SIZES = (((1360, 840), 2.0), ((1920, 1080), 1.0), ((2880, 1920), 1.5))
TABS = ("singles", "sell", "lands", "packs")
_LONGEST = {}


def longest_name(dg):
    """The card-pool name widest in the message's font (cached per font size)."""
    small = dg.font("small", True)
    key = small.size("M")
    if key not in _LONGEST:
        _LONGEST[key] = max((c.name for c in cardpool.load().cards.values()), key=lambda n: small.size(n)[0])
    return _LONGEST[key]


class ShopMessageTests(StyleBase):
    def town(self, size, scale):
        g = self.gui(size, scale)
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        frame(g)
        return g, f, rs.DawnGui(g)

    def messages(self, f, name):
        """(what, text) for every kind of shop message, with `name` in it and the longest numbers the shop could show."""
        deck = f.game.deck()["name"]
        f.game.save.decks.append({"name": "A Second Deck With A Long Name", "main": [], "sideboard": []})
        f.shop.taken = [(deck, 1), ("A Second Deck With A Long Name", 1)]
        with mock.patch.object(f.game, "deck_problems", return_value=["too few cards"]):
            sold_two = f.sold_words(name, 9999)
        f.shop.taken = [(deck, 1)]
        with mock.patch.object(f.game, "deck_problems", return_value=["too few cards"]):
            sold = f.sold_words(name, 9999)
        self.assertIn("Add cards before you duel.", sold)
        return (("buy", f"Bought {name} for 9999 gold."),
                ("sell", sold),
                ("sell_two_decks", sold_two),
                ("cant_afford", f"{name} costs 9999 gold; you have 9998."),
                ("sold_out", f"{name} is sold out."),
                ("cant_sell", f"You own 0 {name}; you can't sell 1."),
                ("pack", "A pack costs 9999 gold; you have 9998."))

    def drawn_message(self, g, f, text):
        spy = Spy()
        with mock.patch.object(rs.gfx, "draw_text", spy):
            frame(g)
        x = f.rect.x + f.pad
        mine = [i for i, c in enumerate(spy.calls) if c[0] and c[0] in text and c[3].x == x
                and set(c[0].split()) <= set(text.split())]
        return spy.calls, mine

    def test_every_shop_message_is_drawn_whole_inside_the_window(self):
        r8.new_game(None)
        for size, scale in SIZES:
            g, f, dg = self.town(size, scale)
            name = longest_name(dg)
            for what, text in self.messages(f, name):
                for tab in TABS:
                    with self.subTest(size=size, scale=scale, what=what, tab=tab):
                        f.tab = tab
                        f.say(text, rs.ORANGE)
                        calls, mine = self.drawn_message(g, f, text)
                        lines = sorted((calls[i] for i in mine), key=lambda c: c[3].y)
                        self.assertEqual(" ".join(c[0] for c in lines), text, "the message isn't drawn whole")
                        if what != "pack":
                            self.assertIn(name, text)
                        box_right = f.rect.right - f.pad
                        for s, font, _c, r in lines:
                            self.assertLessEqual(font.size(s)[0], box_right - r.x, s)   # measured against the box
                            self.assertLessEqual(r.right, box_right, s)
                            self.assertGreaterEqual(r.y, f.rect.y, s)
                            self.assertLessEqual(r.bottom, f.rect.bottom - f.pad, s)
                        for a, b in zip(lines, lines[1:]):
                            self.assertLessEqual(a[3].bottom, b[3].y)                  # one under the other
                        self.assertEqual(mine, list(range(len(calls) - len(mine), len(calls))),
                                         "the message is drawn last, over the lists")
                        for _s, _f, _c, r in lines:                                 # Round SHOPMSG2: in the strip
                            self.assertTrue(f.msg_strip.contains(r), (r, f.msg_strip))

    def test_the_long_sale_at_200_percent_needs_more_than_two_lines(self):
        """STARTER1's case: without the change its end was cut - so this size exercises the grown strip."""
        r8.new_game(None)
        g, f, dg = self.town((1360, 840), 2.0)
        text = dict(self.messages(f, longest_name(dg)))["sell"]
        f.say(text, rs.GREEN)
        self.assertGreater(len(f.message_lines(dg, f.rect.w - 2 * f.pad, f.MESSAGE_MAX_LINES)), 2)

    def test_a_short_message_keeps_the_two_line_strip(self):
        """Round SHOPMSG2: replaces test_a_short_message_stays_in_its_strip_with_no_panel (no panel any more)."""
        r8.new_game(None)
        g, f, dg = self.town((1360, 840), 2.0)
        f.say("Bought Opt for 5 gold.", rs.GREEN)
        frame(g)
        small = dg.font("small")
        self.assertEqual(f.msg_strip.h, max(2 * small.get_height(), 2 * dg.font("small", True).get_height()))

    def test_a_message_too_long_even_for_the_strip_ends_in_dots_inside_it(self):
        r8.new_game(None)
        g, f, dg = self.town((1360, 840), 2.0)
        w = f.rect.w - 2 * f.pad
        f.say(" ".join(["word"] * 400), rs.GREEN)
        lines = f.message_lines(dg, w, f.MESSAGE_MAX_LINES)
        self.assertEqual(len(lines), f.MESSAGE_MAX_LINES)
        self.assertTrue(lines[-1].endswith("..."))
        small = dg.font("small", True)
        self.assertTrue(all(small.size(ln)[0] <= w for ln in lines))
        frame(g)
        self.assertGreaterEqual(f.msg_strip.y, f.rect.y)


if __name__ == "__main__":
    unittest.main()
