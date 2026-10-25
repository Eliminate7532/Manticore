# SPDX-License-Identifier: GPL-3.0-or-later
"""Rounds GY1 and PEN1 together (24 Oct 2026): a graveyard too big for its window, on Karl's Surface with the stylus.

GY1 made a glowing card in a graveyard window playable with a tap; PEN1 made a list that scrolls wait for the pen to move or
lift. The graveyard window is a ChooseDialog, so both apply: drag it down to the card, then tap the card.
"""
import copy
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

from tests import test_gy1 as g
from tests.test_forge_table import frame


def big_graveyard(cards=150, frog_at=140):
    """Karl's recorded board with his graveyard filled with copies of Mishra's Bauble (nothing to play) and the castable Frogmite
    near the bottom, where it is out of sight until the list is dragged."""
    st = g.recorded()
    me = st["players"][0]
    gy = me["zones"]["graveyard"]
    frog = next(c for c in gy if c["id"] == g.FROGMITE)
    filler = next(c for c in gy if c["id"] == g.BAUBLE)
    pile = []
    for i in range(cards):
        c = copy.deepcopy(filler)
        c["id"] = 5000 + i
        c.pop("weak", None)
        pile.append(c)
    pile.insert(frog_at, frog)
    me["zones"]["graveyard"] = pile
    return st


class BigGraveyardTests(unittest.TestCase):
    def send(self, gui, kind, **fields):
        gui.handle_event(pygame.event.Event(kind, **fields))
        frame(gui)

    def test_drag_the_graveyard_down_with_the_pen_then_tap_the_glowing_card(self):
        gui = g.make(big_graveyard(), (1100, 700), 2.0)
        m = g.open_pile(gui, 0)
        self.assertIsNotNone(gui.pan_view_at(m.body.center), "the window must scroll for this test to mean anything")
        self.assertFalse(m.body.contains(g.tiles(m)[g.FROGMITE]), "Frogmite starts out of sight")
        gui.session.sent.clear()
        x, y = m.body.center
        self.send(gui, pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
        for dy in range(0, 1500, 30):                                       # the pen moves up: the list follows it
            self.send(gui, pygame.MOUSEMOTION, pos=(x, y - dy), rel=(0, -30), buttons=(1, 0, 0))
        self.send(gui, pygame.MOUSEBUTTONUP, pos=(x, y - 1470), button=1)
        self.assertEqual(gui.session.sent, [], "a drag over the cards plays nothing")
        self.assertGreater(m.scroll, 0)
        r = g.tiles(m)[g.FROGMITE]
        self.assertTrue(m.body.contains(r), "the drag brought Frogmite into view")
        self.send(gui, pygame.MOUSEBUTTONDOWN, pos=r.center, button=1)
        self.assertEqual(gui.session.commands("card"), [], "on a list that scrolls, the press alone waits")
        self.send(gui, pygame.MOUSEBUTTONUP, pos=r.center, button=1)
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": g.FROGMITE}], "lifting the pen casts it")


if __name__ == "__main__":
    unittest.main()
