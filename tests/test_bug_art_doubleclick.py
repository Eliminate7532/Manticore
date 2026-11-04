# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1567003179428618281 (3 Nov 2026): "game crashed while picking card art".

The crash report sent at the next start (bug-1567005833978445886, 0.28.53.13) shows AttributeError at art_picker.chosen
('tuple' object has no attribute 'lower') from open_card, then TypeError in clip_text from ArtPicker.draw on every frame until
the program closed. A click on the card grid opened a "card" that was a printing (set, number): the tiles list still held the
printings page's tiles, because picking a printing (or Back / Esc) goes back to the grid and a second click in the same frame
(a double click) hits the old tiles before the grid is drawn again.
"""
import os
import shutil
import tempfile
import unittest

import pygame

import art_picker
import deck_library as lib
import forge_table as ft
from tests.forge_fake import FakeSession


class StaleTileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "samples")
        os.makedirs(self.lib)
        os.makedirs(self.samples)
        with open(os.path.join(self.lib, "mine.txt"), "w", encoding="utf-8") as f:
            f.write("Commander\n1 Kinnan, Bonder Prodigy\n\nDeck\n1 Sol Ring\n98 Island\n")
        from tests.test_deck_screen import FakeLauncher
        self.g = ft.ForgeTable(FakeSession(None, None), None, settings_path=None, window_size=(1360, 840),
                               launcher=FakeLauncher(), deck_dirs=(self.lib, self.samples, self.tmp))
        self.g.open_menu()
        self.g.render()
        mine = [e for e in self.g.menu.entries if not e.builtin][0]
        self.picker = art_picker.ArtPicker(self.g.menu, mine)
        self.g.modal = self.picker
        self.g.render()

    def on_card_page_with_a_printing_tile(self):
        p = self.picker
        p.open_card("Sol Ring")
        self.g.render()
        r = pygame.Rect(p.area.x + 5, p.area.y + 5, 60, 80)
        p.tiles = [(r, ("c21", "263"))]               # what the printings page registers for a printing tile
        return r.center

    def second_click_then_draw(self, pos):
        self.picker.click(self.g, pos, 1)
        self.g.render()                               # crashed here before the fix (head = a tuple)
        self.assertIsNone(self.picker.card)

    def test_double_click_on_a_printing_does_not_open_a_tuple(self):
        pos = self.on_card_page_with_a_printing_tile()
        self.picker.pick(self.g, ("c21", "263"))      # the first click: picked, back to the grid
        self.assertIsNone(self.picker.card)
        self.second_click_then_draw(pos)

    def test_click_after_back_in_the_same_frame(self):
        pos = self.on_card_page_with_a_printing_tile()
        self.picker.back_to_grid()
        self.second_click_then_draw(pos)

    def test_the_grid_still_opens_a_card(self):
        self.g.render()
        tiles = [(r, v) for r, v in self.picker.tiles if isinstance(v, str)]
        self.assertTrue(tiles)
        r, v = tiles[0]
        self.picker.click(self.g, r.center, 1)
        self.assertEqual(self.picker.card, v)
        self.g.render()


if __name__ == "__main__":
    unittest.main()
