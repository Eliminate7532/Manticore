# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1567732410072956951 (Karl Shuh, 0.28.53.15): joining a friend's game "didn't give notice about which deck you would be
using - currently just takes the one you've already had selected". The Join dialog now has a "Your deck" row (the deck's name
and its commander(s)) with a "Change deck" button back to the deck screen, and the "Connecting..." line names the deck."""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import online_screens as onl
from tests.test_forge_table import frame
from tests.test_mp1 import SIZES, PanelTests


class _Entry:
    def __init__(self, name, commanders, fmt="commander"):
        self.name, self.commanders, self.format = name, commanders, fmt


class DeckLineTests(unittest.TestCase):
    def test_names_the_deck_and_its_commanders(self):
        self.assertEqual(onl.deck_line(_Entry("Slivers", ["The First Sliver"])), "Slivers  -  The First Sliver")
        self.assertEqual(onl.deck_line(_Entry("Pair", ["Tymna the Weaver", "Thrasios, Triton Hero"])),
                         "Pair  -  Tymna the Weaver + Thrasios, Triton Hero")

    def test_a_format_without_a_commander_shows_the_name_only(self):
        self.assertEqual(onl.deck_line(_Entry("Red Aggro", ["Monastery Swiftspear"], "standard")), "Red Aggro")

    def test_no_deck(self):
        self.assertIn("no deck chosen", onl.deck_line(None))


class JoinDialogDeckTests(PanelTests):
    def open_join(self, size=(1360, 840), scale=1.0):
        gui = self.idle_gui(size=size, scale=scale)
        gui.menu.press(gui, "join_online")
        self.assertIsInstance(gui.modal, onl.JoinDialog)
        frame(gui, 1)
        return gui

    def test_the_dialog_shows_the_chosen_deck_and_a_change_deck_button(self):
        gui = self.open_join()
        d = gui.modal
        names = [n for _r, n in d.buttons]
        self.assertIn("change_deck", names)
        self.assertIn(d.entry.name, d.deck_line)
        self.assertEqual(d.deck_line, onl.deck_line(d.entry))

    def test_change_deck_goes_back_to_the_deck_screen(self):
        gui = self.open_join()
        d = gui.modal
        r = next(r for r, n in d.buttons if n == "change_deck")
        d.click(gui, r.center, 1)
        self.assertTrue(d.done)

    def test_the_connecting_line_names_the_deck(self):
        gui = self.open_join()
        d = gui.modal
        fake = mock.Mock()
        with mock.patch.object(d, "problem", return_value=None), \
                mock.patch.object(d, "target", return_value=("203.0.113.7", 36800, "pw-11", None)), \
                mock.patch.object(d, "format_problem", return_value=None), \
                mock.patch.object(gui, "make_guest_session", return_value=(fake, None)), \
                mock.patch.object(onl, "JoinJob") as job:
            d.deck_problem = None
            d.join(gui)
        self.assertIn(d.entry.name, d.message[0])
        self.assertTrue(job.called)

    def test_it_still_fits_at_every_size(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.open_join(size, scale)
                self.check_dialog_fits(gui)
                gui.modal.by_hand = True
                frame(gui, 1)
                self.check_dialog_fits(gui)
                rects = [r for r, _n in gui.modal.buttons]
                for i, a in enumerate(rects):
                    for b in rects[i + 1:]:
                        self.assertFalse(a.colliderect(b), (a, b))


if __name__ == "__main__":
    unittest.main()
