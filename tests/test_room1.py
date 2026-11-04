# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ROOM1 - bug report from Karl, 31 Oct 2026: "Room cards are appearing sideways" (Funeral Room // Awakening Hall in the
focus panel: Scryfall's picture is upright and both doors read from bottom to top). Karl, 2 Nov: turn them in the focus panel only.

A split card printed sideways (layout "split" on Scryfall: the Rooms, Fire // Ice - checked on api.scryfall.com, 2 Nov) is turned
a quarter clockwise in the focus panel, as wide as the panel. An Aftermath card (Commit // Memory, also "split" but with the
Aftermath keyword) is not: its first half is printed upright. Hand, battlefield and every other picture are unchanged."""
import copy
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

from tests.forge_fake import load_state
from tests.test_forge_table import frame, make_gui

ROOM = {"id": 900, "name": "Funeral Room // Awakening Hall", "oracleName": "Funeral Room // Awakening Hall",
        "type": "Enchantment - Room", "cost": "{8}{B}{B}{B}", "owner": 0, "controller": 0, "zone": "Hand", "text": "doors"}
SCRYFALL = {"funeral room // awakening hall": {"layout": "split", "keywords": []},
            "fire // ice": {"layout": "split", "keywords": []},
            "commit // memory": {"layout": "split", "keywords": ["Aftermath"]},
            "bonecrusher giant // stomp": {"layout": "adventure", "keywords": []},
            "grizzly bears": {"layout": "normal", "keywords": []}}


def portrait(w, h):
    """A stand-in Scryfall picture: dark, with a red top-left corner (to see which way it turned)."""
    surf = pygame.Surface((w, h))
    surf.fill((20, 20, 60))
    surf.fill((255, 0, 0), pygame.Rect(0, 0, max(2, w // 5), max(2, h // 8)))
    return surf


def gui_with(card, size=(1600, 900), scale=None, data=True, picture=True):
    st = load_state("main1_start")
    me = next(p for p in st["players"] if p["id"] == st["me"])
    me["zones"]["hand"].append(copy.deepcopy(card))
    gui = make_gui(st, size=size, scale=scale)
    if data:
        gui.art.store.peek_card = lambda n: SCRYFALL.get((n or "").strip().lower())
    gui.art.preview = (lambda name, w, h: portrait(w, h)) if picture else (lambda name, w, h: None)
    gui.art.is_custom = lambda key: False
    gui.pinned = card["id"]
    frame(gui)
    return gui


class SidewaysTests(unittest.TestCase):
    def test_a_room_is_turned_clockwise_and_as_wide_as_the_panel(self):
        gui = gui_with(ROOM)
        r = gui.L.preview
        surf, dest = gui.focus_picture(ROOM, r)
        self.assertGreater(surf.get_width(), surf.get_height())                  # landscape now
        self.assertEqual(surf.get_width(), r.w)
        self.assertTrue(r.contains(dest))
        self.assertEqual(dest.center, r.center)
        # clockwise: the picture's top-left corner is now its top-right corner
        w, h = surf.get_size()
        self.assertEqual(surf.get_at((w - 4, 4))[:3], (255, 0, 0))
        self.assertNotEqual(surf.get_at((4, 4))[:3], (255, 0, 0))

    def test_the_panel_draws_the_turned_picture(self):
        gui = gui_with(ROOM)
        r = gui.L.preview
        seen = []
        real = gui.focus_picture
        gui.focus_picture = lambda card, rr: seen.append(real(card, rr)) or seen[-1]
        frame(gui)
        self.assertTrue(seen)
        surf, dest = seen[-1]
        self.assertGreater(surf.get_width(), surf.get_height())
        mid = (r.centerx, r.centery)
        self.assertNotEqual(gui.screen.get_at(mid)[:3], (0, 0, 0))

    def test_other_split_cards_follow_scryfall(self):
        for name, turned in (("Fire // Ice", True), ("Commit // Memory", False), ("Bonecrusher Giant // Stomp", False),
                             ("Grizzly Bears", False)):
            with self.subTest(name=name):
                card = dict(ROOM, id=901, name=name, oracleName=name, type="Instant" if "//" in name else "Creature - Bear")
                gui = gui_with(card)
                self.assertEqual(gui.sideways_card(card), turned)
                surf, dest = gui.focus_picture(card, gui.L.preview)
                self.assertEqual(surf.get_width() > surf.get_height(), turned)

    def test_without_card_data_a_room_is_known_by_its_type_line(self):
        gui = gui_with(ROOM, data=False)
        self.assertTrue(gui.sideways_card(ROOM))
        self.assertFalse(gui.sideways_card(dict(ROOM, name="Fire // Ice", oracleName="Fire // Ice", type="Instant")))

    def test_a_drawn_stand_in_or_your_own_picture_is_not_turned(self):
        gui = gui_with(ROOM, picture=False)                       # no picture yet: the drawn face reads upright already
        surf, dest = gui.focus_picture(ROOM, gui.L.preview)
        self.assertEqual(dest.size, gui.L.preview.size)
        gui = gui_with(ROOM)
        gui.art.is_custom = lambda key: True                     # a my_art picture: left as you made it
        surf, dest = gui.focus_picture(ROOM, gui.L.preview)
        self.assertEqual(dest.size, gui.L.preview.size)

    def test_a_hidden_or_face_down_room_is_a_card_back(self):
        gui = gui_with(ROOM)
        for extra in ({"hidden": True}, {"faceDown": True}):
            self.assertFalse(gui.sideways_card(dict(ROOM, **extra)))

    def test_only_the_focus_panel_turns_it(self):
        gui = gui_with(ROOM)
        frame(gui)
        hand = [r for r, kind, data in gui.hits if kind == "card" and data["card"]["id"] == ROOM["id"]]
        self.assertTrue(hand)
        self.assertGreater(hand[-1].h, hand[-1].w)                # the hand keeps the upright card
        self.assertEqual(gui.preview_surface(ROOM, 100, 140).get_size(), (100, 140))

    def test_it_fits_at_every_size(self):
        for size, scale in (((1100, 700), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0), ((4096, 1949), 1.75)):
            with self.subTest(size=size, scale=scale):
                gui = gui_with(ROOM, size=size, scale=scale)
                r = gui.L.preview
                surf, dest = gui.focus_picture(ROOM, r)
                self.assertTrue(r.contains(dest), (dest, r))
                self.assertGreater(surf.get_width(), surf.get_height())


if __name__ == "__main__":
    unittest.main()
