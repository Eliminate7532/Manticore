# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UI8 (22-23 Oct 2026): Karl's two notes of 22 Oct evening.

  22:58 "I would like to have the ability to increase the size of cards on boxes like this." (the library search, "Select a
        card from your library"): the card-picking windows (forge_dialogs.ChooseDialog: choose, order, look at a zone) get a card
        size, as the Card art window has (patch 38) - Card size - / + at the bottom left, the - / + keys, Ctrl+wheel; 1.0 = as
        before; remembered in settings.json (choose_card_zoom).
  22:59 "This scroll bar on the side of the deck list is almost unusable. It needs to be 'clickable' by mouse, and maybe even
        mouse-wheel scroll up/down": scrollbar.ScrollBar - drag the handle, click the groove for a page - for the deck list and
        the card-picking windows; the deck list's wheel moves a whole row per notch (it moved about a third of one).

  BarTests        the bar on its own: no bar when everything shows, the handle's size and place, drag, page clicks, release
  DeckListTests   the deck screen: drag to the end shows the last deck, a groove click pages, a click on the bar picks no deck,
                  rows stay clear of the bar, one row per wheel notch, a drag ended outside the window
  CardSizeTests   the library search: 1.0 is the old size, + is bigger and scrolls, the keys and Ctrl+wheel, the setting is saved
                  and read back (a bad value is 1.0), the controls never overlap the buttons, text-only choices have none, the
                  bar in the window can be dragged and doesn't pick a card
"""
import copy
import glob
import json
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_dialogs as dlg
import forge_table as ft
import scrollbar
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_deck_screen import FakeLauncher
from tests.test_forge_table import frame, make_gui, my_hand

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def motion(gui, pos, held=True):
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1 if held else 0, 0, 0)))


def down(gui, pos, button=1):
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button))


def up(gui, pos, button=1):
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button))


def wheel(gui, y):
    gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=y, flipped=False))


def key(gui, k, mod=0):
    gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod, unicode="", scancode=0))


class BarTests(unittest.TestCase):
    def bar(self, content=1000, scroll=0, view=(0, 0, 300, 400), fs=1.0):
        b = scrollbar.ScrollBar()
        b.layout(pygame.Rect(view), content, scroll, fs)
        return b

    def test_no_bar_when_everything_shows(self):
        b = self.bar(content=400)
        self.assertFalse(b.needed)
        self.assertIsNone(b.press(b.track.center, 0))

    def test_the_handle_shows_how_much_of_the_list_is_in_view_and_where(self):
        b = self.bar(content=1000)
        self.assertEqual(b.track, pygame.Rect(300 - scrollbar.WIDTH, 0, scrollbar.WIDTH, 400))
        self.assertEqual(b.handle.h, 160)                                  # 400 of 1000 shown: 40% of the groove
        self.assertEqual(b.handle.y, 0)
        b = self.bar(content=1000, scroll=600)
        self.assertEqual(b.handle.bottom, 400)                             # at the end of the list: at the end of the groove
        b = self.bar(content=100000)
        self.assertEqual(b.handle.h, scrollbar.MIN_HANDLE)                 # a long list: never too small to grab
        b = self.bar(content=1000, fs=2.0)
        self.assertEqual(b.track.w, 2 * scrollbar.WIDTH)                   # wider with bigger text

    def test_drag_the_handle(self):
        b = self.bar(content=1000)
        grab = (b.handle.centerx, b.handle.y + 10)
        self.assertEqual(b.press(grab, 0), 0)                              # on the handle: a drag starts, nothing moves yet
        self.assertTrue(b.dragging)
        self.assertEqual(b.drag((grab[0], grab[1] + 120)), 300)            # 120 px of 240 px of room = half of 600 to scroll
        self.assertEqual(b.drag((grab[0], 10000)), 600)                    # past the end: the end
        self.assertEqual(b.drag((grab[0], -500)), 0)
        self.assertTrue(b.release())
        self.assertFalse(b.dragging)
        self.assertIsNone(b.drag((grab[0], 300)))                          # no drag after the button went up
        self.assertFalse(b.release())

    def test_a_click_on_the_groove_moves_a_page(self):
        b = self.bar(content=1000, scroll=300)
        page = int(400 * (1 - scrollbar.PAGE_KEEP))
        below = (b.track.centerx, b.handle.bottom + 5)
        above = (b.track.centerx, b.handle.y - 5)
        self.assertEqual(b.press(below, 300), min(600, 300 + page))
        self.assertEqual(b.press(above, 300), max(0, 300 - page))
        self.assertFalse(b.dragging)
        self.assertIsNone(b.press((10, 10), 300))                          # not on the bar: the owner handles it


class DeckListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        lib, samples = os.path.join(self.tmp, "my_decks"), os.path.join(self.tmp, "sample_decks")
        os.makedirs(lib)
        os.makedirs(samples)
        files = sorted(glob.glob(os.path.join(BASE, "sample_decks", "*.txt")))
        for f in files:
            shutil.copy(f, samples)
        for k, f in enumerate(files[:9]):                                  # Karl has 13 decks: enough that the list scrolls
            shutil.copy(f, os.path.join(lib, f"copy_{k}_" + os.path.basename(f)))
        self.dirs = (lib, samples, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gui(self, size=(1920, 1080), scale=1.75):
        g = ft.ForgeTable(FakeSession(load_state("main1_start"), load_log()), StubStore(), settings_path=None, window_size=size,
                          launcher=FakeLauncher(), deck_dirs=self.dirs)
        g.text_scale = scale
        frame(g, 2)
        g.open_menu()
        frame(g, 2)
        self.assertTrue(g.menu.bar.needed, "the test needs a list that scrolls")
        return g

    def test_dragging_the_handle_to_the_end_shows_the_last_deck(self):
        g = self.gui()
        m = g.menu
        last = m.visible()[-1]
        grab = m.bar.handle.center
        down(g, grab)
        motion(g, (grab[0], m.list_rect.bottom + 400))
        up(g, (grab[0], m.list_rect.bottom + 400))
        frame(g)
        self.assertEqual(m.scroll, m.content_h - m.list_rect.h)
        self.assertIn(last.id, [e.id for r, e in m.rows if m.list_rect.contains(r)])
        self.assertFalse(m.bar.dragging)

    def test_a_click_on_the_groove_moves_a_page_and_picks_no_deck(self):
        # The bar's left edge too: the old rows ended 8 px x text scale short of the list's edge, which is inside the new bar.
        for x in ("left", "centre"):
            with self.subTest(x=x):
                g = self.gui()
                m = g.menu
                before = (m.focus_id, m.mine_id, list(m.opps), m.active)
                down(g, (m.bar.track.x + 1 if x == "left" else m.bar.track.centerx, m.bar.handle.bottom + 4))
                frame(g)
                self.assertGreater(m.scroll, 0)
                self.assertEqual((m.focus_id, m.mine_id, list(m.opps), m.active), before)
                self.assertFalse(m.bar.dragging)

    def test_rows_stay_clear_of_the_bar(self):
        for size, scale in (((1920, 1080), 1.75), ((1360, 840), 1.0), ((4096, 2160), 1.75), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                g = self.gui(size, scale)
                m = g.menu
                for r, _e in m.rows:
                    self.assertLess(r.right, m.bar.track.x)

    def test_one_wheel_notch_moves_one_row(self):
        g = self.gui()
        m = g.menu
        g.mouse = m.list_rect.center
        wheel(g, -1)
        self.assertEqual(m.scroll, m.row_h_cache)
        wheel(g, 1)
        self.assertEqual(m.scroll, 0)

    def test_a_drag_that_ends_outside_the_window_still_ends(self):
        g = self.gui()
        m = g.menu
        down(g, m.bar.handle.center)
        self.assertTrue(m.bar.dragging)
        motion(g, (m.bar.handle.centerx, m.bar.handle.centery + 30), held=False)   # the button went up out of the window
        self.assertFalse(m.bar.dragging)


NAMES = ["Basal Sliver", "Birds of Paradise", "Cloudshredder Sliver", "Crystalline Sliver", "Dormant Sliver", "Esper Sentinel",
         "Galerider Sliver", "Gemhide Sliver", "Harmonic Sliver", "Heart Sliver", "Hermit Druid", "Hibernation Sliver",
         "Homing Sliver", "Lavabelly Sliver", "Manaweft Sliver", "Necrotic Sliver", "Quick Sliver", "Sedge Sliver",
         "Sentinel Sliver", "Sliver Hivelord", "Sliver Legion", "Sliver Queen", "The First Sliver", "Venom Sliver"]


class CardSizeTests(unittest.TestCase):
    """Karl's screenshot: a sliver deck's library search, 24 cards."""

    def search(self, size=(1920, 1080), scale=1.75, zoom=1.0, n=24, settings=None, text_only=False):
        g = make_gui("main1_start", size, settings=settings)
        g.text_scale = scale
        g.choose_card_zoom = zoom
        base = my_hand(g)[0]
        items = []
        for k in range(n):
            if text_only:
                items.append({"kind": "text", "label": f"Mode {k + 1}"})
                continue
            c = copy.deepcopy(base)
            c["id"], c["name"] = 9000 + k, NAMES[k % len(NAMES)] + ("" if k < len(NAMES) else f" {k}")
            items.append({"kind": "card", "card": c})
        g.session.requests.append({"id": 77, "kind": "choose_optional", "title": "Select a card from your library", "min": 0,
                                   "max": 1, "items": items})
        frame(g, 2)
        self.assertIsInstance(g.modal, dlg.ChooseDialog)
        return g, g.modal

    def tile_h(self, d):
        hs = {r.h for r, i in d.tiles if d.items[i].get("kind") == "card"}
        self.assertEqual(len(hs), 1)
        return hs.pop()

    def test_100_percent_is_the_old_size(self):
        # the tile heights release-0.28.53.12 draws (before this round), measured on it with the same kind of request
        for size, scale, n, h in (((1360, 840), 1.0, 24, 160), ((1920, 1080), 1.75, 24, 200), ((4096, 2160), 1.75, 24, 200),
                                  ((4096, 2160), 1.75, 12, 300), ((1920, 1080), 1.0, 5, 240), ((1100, 700), 2.0, 24, 120),
                                  ((1100, 700), 2.0, 60, 110)):
            with self.subTest(size=size, scale=scale, n=n):
                _g, d = self.search(size, scale, n=n)
                self.assertEqual(self.tile_h(d), h)

    def test_bigger_cards_scroll_and_never_get_taller_than_the_list(self):
        sizes = []
        for z in dlg.CHOOSE_ZOOM_STEPS:
            _g, d = self.search(zoom=z)
            h = self.tile_h(d)
            sizes.append(h)
            self.assertLessEqual(h, d.body.h)
        self.assertEqual(sizes, sorted(sizes))
        self.assertGreater(sizes[-1], 2 * sizes[dlg.CHOOSE_ZOOM_STEPS.index(1.0)])
        _g, d = self.search(zoom=2.0)
        self.assertTrue(d.bar.needed)
        for r, _i in d.tiles:
            self.assertLess(r.right, d.bar.track.x)                        # the cards stay clear of the bar

    def test_the_cards_stay_clear_of_the_bar_at_every_size(self):
        # One size isn't enough: the cards are centred, so whether a grid as wide as the list would reach the bar depends on the
        # leftover width (a planted grid under the bar passed the 1920x1080 case and reached it in 14 of 108 of these).
        seen = 0
        for size, scale in (((1360, 840), 1.0), ((1920, 1080), 1.75), ((4096, 2160), 1.75), ((1100, 700), 2.0), ((2560, 1440), 1.25)):
            for z in (1.25, 1.5, 2.0, 2.5):
                for n in (12, 24, 60):
                    _g, d = self.search(size, scale, zoom=z, n=n)
                    if not d.bar.needed:
                        continue
                    seen += 1
                    with self.subTest(size=size, scale=scale, zoom=z, n=n):
                        for r, _i in d.tiles:
                            self.assertLess(r.right, d.bar.track.x)
        self.assertGreater(seen, 30)

    def test_the_buttons_keys_and_ctrl_wheel_change_it_and_it_is_saved(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "settings.json")
        g, d = self.search(settings=path)
        before = self.tile_h(d)
        zin = next(r for r, n in d.buttons if n == "zoom_in")
        down(g, zin.center)
        frame(g)
        self.assertEqual(g.choose_card_zoom, 1.25)
        self.assertGreater(self.tile_h(g.modal), before)
        key(g, pygame.K_EQUALS)
        self.assertEqual(g.choose_card_zoom, 1.5)
        key(g, pygame.K_MINUS)
        key(g, pygame.K_KP_MINUS)
        self.assertEqual(g.choose_card_zoom, 1.0)
        pygame.key.set_mods(pygame.KMOD_LCTRL)
        try:
            wheel(g, 1)
        finally:
            pygame.key.set_mods(0)
        self.assertEqual(g.choose_card_zoom, 1.25)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["choose_card_zoom"], 1.25)
        g2 = make_gui("main1_start", settings=path)
        self.assertEqual(g2.choose_card_zoom, 1.25)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"choose_card_zoom": "huge"}, f)
        self.assertEqual(make_gui("main1_start", settings=path).choose_card_zoom, 1.0)
        self.assertEqual(g.session.commands("reply"), [])                  # none of it answered Forge

    def test_the_ends_of_the_scale(self):
        g, d = self.search(zoom=3.0)
        self.assertNotIn("zoom_in", [n for _r, n in d.buttons])            # greyed: nothing bigger
        key(g, pygame.K_PLUS)
        self.assertEqual(g.choose_card_zoom, 3.0)
        g, d = self.search(zoom=0.6)
        self.assertNotIn("zoom_out", [n for _r, n in d.buttons])

    def test_the_controls_never_overlap_the_buttons(self):
        for size, scale in (((900, 600), 1.0), ((1100, 700), 2.0), ((1360, 840), 1.0), ((1920, 1080), 1.75),
                            ((4096, 2160), 1.75)):
            with self.subTest(size=size, scale=scale):
                g, d = self.search(size, scale)
                if d.size_rect is None:
                    continue                                               # no room at all: no controls, the keys still work
                for r, n in d.buttons:
                    if n in ("done", "skip", "close"):
                        self.assertFalse(d.size_rect.colliderect(r), (n, r, d.size_rect))
                self.assertTrue(d.rect.contains(d.size_rect))
        g, d = self.search((1920, 1080), 1.75)
        self.assertIsNotNone(d.size_rect)                                  # Karl's kind of screen: the controls are there

    def test_text_only_choices_have_no_card_size(self):
        g, d = self.search(text_only=True, n=4)
        self.assertNotIn("zoom_in", [n for _r, n in d.buttons])
        key(g, pygame.K_PLUS)
        self.assertEqual(g.choose_card_zoom, 1.0)

    def test_the_bar_in_the_window_drags_and_picks_no_card(self):
        g, d = self.search(zoom=2.0)
        grab = d.bar.handle.center
        down(g, grab)
        motion(g, (grab[0], d.body.bottom + 500))
        up(g, (grab[0], d.body.bottom + 500))
        frame(g)
        self.assertEqual(d.scroll, d.content_h - d.body.h)
        self.assertEqual(d.sel, [])
        down(g, (d.bar.track.x + 1, d.bar.handle.y - 4))                  # the groove above, at its left edge: a page up
        frame(g)
        self.assertLess(d.scroll, d.content_h - d.body.h)
        self.assertEqual(d.sel, [])

    def test_ordered_lists_put_the_controls_after_reset_and_select_all(self):
        g = make_gui("main1_start", (1920, 1080))
        base = my_hand(g)[0]
        items = []
        for k in range(6):
            c = copy.deepcopy(base)
            c["id"], c["name"] = 9100 + k, NAMES[k]
            items.append({"kind": "card", "card": c})
        g.session.requests.append({"id": 78, "kind": "order", "title": "Order the triggers", "min": 6, "max": 6, "items": items})
        frame(g, 2)
        d = g.modal
        self.assertTrue(d.ordered)
        sel_all = next(r for r, n in d.buttons if n == "select_all")
        self.assertIsNotNone(d.size_rect)
        self.assertGreater(d.size_rect.x, sel_all.right)


if __name__ == "__main__":
    unittest.main()
