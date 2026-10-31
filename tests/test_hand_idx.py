# SPDX-License-Identifier: GPL-3.0-or-later
"""Round hand-idx (27 Oct 2026): the hand's hovered index kept through the 60 ms hover dwell (round 23) could point past the end
of a hand that shrank meanwhile (a card played or discarded), and draw_hand raised IndexError at `positions[pos]` - Karl's
Surface, installed 0.28.53.11, 23 Oct 21:23, "10 times so far this run". A kept index outside the current hand is now dropped."""
import copy
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_table as ft
from tests.forge_fake import load_state
from tests.test_forge_table import frame, make_gui, move


def hand_hits(gui):
    """{card id: hit rect} for the hand cards drawn last frame."""
    hand_ids = {c["id"] for c in gui.session.me()["zones"]["hand"]}
    return {d["card"]["id"]: r for r, k, d in gui.hits if k == "card" and d["card"]["id"] in hand_ids}


def shrink_hand(gui, keep_ids, base=None):
    """A new snapshot with the same table but only the hand cards in `keep_ids` (as after playing one), from `base` (default:
    the snapshot shown now)."""
    st = copy.deepcopy(base or gui.session.state)
    for p in st["players"]:
        if p.get("id") == gui.session.me().get("id"):
            p["zones"]["hand"] = [c for c in p["zones"]["hand"] if c["id"] in keep_ids]
    gui.session.handle(st)


class StaleHoverTests(unittest.TestCase):
    def _hover_last_then_shrink(self, card_scale=1.0, sort=False):
        gui = make_gui("main1_start")
        gui.card_scale = card_scale
        gui.hand_sort_by_type = sort
        frame(gui)
        hits = hand_hits(gui)
        self.assertGreaterEqual(len(hits), 3, "the fixture's hand is too small for this test")
        by_x = sorted(hits, key=lambda cid: hits[cid].x)
        last, first = by_x[-1], by_x[0]
        move(gui, (hits[last].right - 3, hits[last].centery))          # the right-most card: the last screen position
        self.assertEqual(gui.hover_hand_index, len(hits) - 1)
        p0 = (hits[first].x + 3, hits[first].centery)
        # A card leaves the hand (here the hovered one, played) and the mouse is already on another card, inside the dwell.
        shrink_hand(gui, set(hits) - {last})
        move(gui, p0)                                                  # raised IndexError in draw_hand before the fix
        frame(gui, 2)
        self.assertTrue(gui.hover_hand_index is None or gui.hover_hand_index < len(hits) - 1)
        return gui

    def test_a_hand_that_shrinks_inside_the_hover_dwell_still_draws(self):
        with mock.patch.object(ft, "HOVER_DWELL", 30.0):              # the dwell never ends during the test: deterministic
            self._hover_last_then_shrink()

    def test_the_same_with_the_hand_sorted_by_type(self):
        with mock.patch.object(ft, "HOVER_DWELL", 30.0):
            self._hover_last_then_shrink(sort=True)

    def test_the_same_with_arena_style_hand_cards(self):               # round HAND1: Card size above 100%
        with mock.patch.object(ft, "HOVER_DWELL", 30.0):
            self._hover_last_then_shrink(card_scale=1.5)

    def test_an_empty_hand_after_a_hover_draws(self):
        with mock.patch.object(ft, "HOVER_DWELL", 30.0):
            gui = make_gui("main1_start")
            start = copy.deepcopy(gui.session.state)
            hits = hand_hits(gui)
            last = max(hits, key=lambda cid: hits[cid].x)
            move(gui, (hits[last].right - 3, hits[last].centery))
            shrink_hand(gui, set())
            frame(gui, 2)
            shrink_hand(gui, {last}, start)                                 # one card back (drawn): the old index 6 must not return
            frame(gui, 2)
            self.assertEqual(len(gui.session.me()["zones"]["hand"]), 1)
            self.assertIn(gui.hover_hand_index, (None, 0))


class HoverTargetTests(unittest.TestCase):
    def test_a_kept_index_past_the_hand_is_dropped(self):
        gui = make_gui("main1_start")
        self.assertEqual(gui.hover_hand_target(5, 1000.0, 6), 5)
        self.assertEqual(gui.hover_hand_target(0, 1000.01, 6), 5)      # inside the dwell: kept, the hand still has 6
        self.assertEqual(gui.hover_hand_target(0, 1000.02, 5), 0)      # the hand shrank to 5: 5 is gone, the card under the mouse
        self.assertIsNone(gui.hover_hand_target(None, 1000.03, 5))

    def test_inside_the_hand_the_dwell_is_unchanged(self):
        gui = make_gui("main1_start")
        self.assertEqual(gui.hover_hand_target(2, 1000.0, 6), 2)
        self.assertEqual(gui.hover_hand_target(1, 1000.02, 5), 2)      # still in a 5-card hand: the round-23 dwell holds
        self.assertEqual(gui.hover_hand_target(1, 1000.09, 5), 1)

    def test_without_a_count_it_behaves_as_before(self):              # round 23's own tests call it with two arguments
        gui = make_gui("main1_start")
        self.assertEqual(gui.hover_hand_target(5, 1000.0), 5)
        self.assertEqual(gui.hover_hand_target(0, 1000.01), 5)


if __name__ == "__main__":
    unittest.main()
