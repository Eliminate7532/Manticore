# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UI2: the 200% text gaps from the alpha screenshots (docs/incoming/alpha_screens/FINDINGS.md)."""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_log
from tests.test_forge_table import frame
from tools.alpha_screens import pod

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = (((1920, 1080), 2.0), ((1920, 1080), 1.0), ((1360, 840), 2.0), ((4096, 1949), 1.75))


def table(state, size, scale, players=None):
    st = pod(state, players) if players else state
    from tests.forge_fake import load_state
    gui = ft.ForgeTable(FakeSession(st if players else load_state(state), load_log()), StubStore(), settings_path=None,
                        window_size=size)
    gui.text_scale = scale
    frame(gui, 3)
    return gui


class PodPanelTests(unittest.TestCase):
    def test_every_opponents_piles_are_on_their_own_panel(self):
        # at 200% the Library / Graveyard / Exile row was drawn below each AI panel, behind the next one
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = table("main1_lands", size, scale, players=4)
                panels = {pid: r for pid, r in gui.panel_rects.items()}
                libs = [(r, d) for r, k, d in gui.hits if k == "library"]
                self.assertEqual(len(libs), 4)
                # on the panel: wholly across, and at least three quarters of its height (the smallest pod panel at 200% clips
                # a chip's last few pixels - drawing is clipped to the panel, never onto the next one)
                def on(r, p):
                    return p.left <= r.left and r.right <= p.right and r.clip(p).h >= 0.75 * r.h
                for r, d in libs:
                    self.assertTrue(on(r, panels[d["player"]]), (size, scale, r, panels[d["player"]]))
                for r, k, d in gui.hits:
                    if k == "zone" and d.get("zone") in ("graveyard", "exile") and d.get("player") in panels:
                        self.assertTrue(on(r, panels[d["player"]]), (size, scale, d, r))


class ActionBarTests(unittest.TestCase):
    def lines(self, gui):
        out = []
        real = gui.draw_line
        gui.draw_line = lambda text, x, y, font, colour: (out.append(text), real(text, x, y, font, colour))
        gui.render()
        gui.draw_line = real
        return out

    def test_the_hint_line_shows_at_200_percent_when_it_fits(self):
        gui = table("paying_mana", (1920, 1080), 2.0)
        self.assertTrue(any("Tap lands" in t for t in self.lines(gui)))

    def test_undo_stays_on_the_bar_at_200_percent(self):
        # Round UI3: Undo shares the left button's column (two half-height buttons) instead of disappearing
        for state in ("main1_lands", "declare_attackers", "stack_two_late", "paying_mana"):
            with self.subTest(state=state):
                gui = table(state, (1920, 1080), 2.0)
                buttons = {d.get("name"): r for r, k, d in gui.hits if k == "button"}
                self.assertIn("undo", buttons)
                for name, r in buttons.items():
                    if name in ("ok", "cancel", "undo", "skip"):
                        self.assertTrue(gui.L.bar.contains(r), (name, r))
                others = [r for n, r in buttons.items() if n in ("ok", "cancel", "skip")]
                self.assertFalse(any(buttons["undo"].colliderect(r) for r in others))

    def test_100_percent_still_shows_undo(self):
        gui = table("main1_lands", (1920, 1080), 1.0)
        self.assertIn("undo", [d.get("name") for _r, k, d in gui.hits if k == "button"])

    def test_my_panel_gives_the_bar_room_only_on_a_big_window(self):
        big = table("main1_lands", (1920, 1080), 2.0)
        self.assertLess(big.L.my_info.w, big.L.opp_info_w)
        small = table("main1_lands", (1100, 700), 2.0)
        self.assertEqual(small.L.my_info.w, small.L.opp_info_w)


class DeckListTests(unittest.TestCase):
    def test_at_200_percent_the_list_shows_most_of_the_decks(self):
        from tests.test_deck_screen import FakeLauncher
        lib = tempfile.mkdtemp()
        gui = ft.ForgeTable(FakeSession(None, None), None, settings_path=None, window_size=(1920, 1080), launcher=FakeLauncher(),
                            deck_dirs=(lib, os.path.join(HERE, "sample_decks"), HERE))
        gui.text_scale = 2.0
        gui.open_menu()
        gui.render()
        visible = [r for r, _e in gui.menu.rows if gui.menu.list_rect.contains(r)]
        self.assertGreaterEqual(len(visible), 4)          # it was one, plus half of the next
        self.assertTrue(gui.menu.one_line)
        for r, n in gui.menu.btns:
            self.assertTrue(gui.screen.get_rect().contains(r), n)

    def test_at_100_percent_rows_keep_two_lines(self):
        from tests.test_deck_screen import FakeLauncher
        lib = tempfile.mkdtemp()
        gui = ft.ForgeTable(FakeSession(None, None), None, settings_path=None, window_size=(1920, 1080), launcher=FakeLauncher(),
                            deck_dirs=(lib, os.path.join(HERE, "sample_decks"), HERE))
        gui.text_scale = 1.0
        gui.open_menu()
        gui.render()
        self.assertFalse(gui.menu.one_line)


if __name__ == "__main__":
    unittest.main()
