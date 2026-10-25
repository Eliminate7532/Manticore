# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PEN1 (24 Oct 2026): Karl on his Surface Pro with a stylus and no keyboard.

  20:29 "On screens like this we need to be able to increase the size of the cards" (the library search) - built in UI8 (on main,
        not yet in an installer); CardSizeTests there.
  20:29 "Scroll bars like this are almost unusable with a stylus (on surface), please fix." (the deck list)
  20:30 "It was impossible for me to scroll down or type an answer with a stylus." ("Choose a creature type", 300 lines; his
        screenshot ended with "Selected: Incarnation" - a pen trying to drag the list picks the row under it)

  PannerTests     the press-or-drag decision on its own
  ChooseDragTests a long list (Karl's creature types) and a card list: drag scrolls and picks nothing; a tap still picks; a list
                  that fits still acts on the press
  FindTests       the Find box of a long plain list
  KeyboardTests   the on-screen keyboard: opens from a window's Keyboard button, types into that window only (never the table's
                  one-letter shortcuts), Shift, Back, Enter, Move, Hide, closes with its window, fits at every size
  OtherListTests  the deck list, the game log, Help and Licenses can be dragged too
"""
import copy
import glob
import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_dialogs as dlg
import forge_table as ft
import osk
import panning
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_deck_screen import FakeLauncher
from tests.test_forge_table import frame, make_gui, my_hand

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the start of Karl's "Choose a creature type" list (his screenshot), then more of Forge's creature types
TYPES = ["Human", "Construct", "Wizard", "Thopter", "Crab", "Merfolk", "Toy", "Scarecrow", "Servo", "Hero", "Artificer", "Elemental",
         "Beast", "Frog", "Warrior", "Incarnation", "Advisor", "Aetherborn", "Ally", "Angel", "Antelope", "Ape", "Archer", "Archon",
         "Armadillo", "Army", "Artificer Bird", "Assassin", "Assembly-Worker", "Atog", "Aurochs", "Avatar", "Azra", "Badger",
         "Barbarian", "Bard", "Basilisk", "Bat", "Bear", "Beeble", "Beholder", "Berserker", "Bird", "Blinkmoth", "Boar", "Bringer",
         "Brushwagg", "Camarid", "Camel", "Caribou", "Carrier", "Cat", "Centaur", "Cephalid", "Chimera", "Citizen", "Cleric",
         "Cockatrice", "Coward", "Crocodile"]


def down(gui, pos):
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1))


def up(gui, pos):
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1))


def motion(gui, pos, held=True):
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1 if held else 0, 0, 0)))


def drag(gui, start, end, steps=6):
    down(gui, start)
    for k in range(1, steps + 1):
        motion(gui, (start[0] + (end[0] - start[0]) * k // steps, start[1] + (end[1] - start[1]) * k // steps))
    up(gui, end)
    frame(gui)


def tap(gui, pos):
    down(gui, pos)
    up(gui, pos)
    frame(gui)


def press_key(gui, name):
    """Tap one key of the on-screen keyboard."""
    frame(gui)
    rect = next(r for r, n in gui.keyboard.keys if n == name)
    tap(gui, rect.center)


def button_rect(d, name):
    return next(r for r, n in d.buttons if n == name)


class PannerTests(unittest.TestCase):
    def view(self):
        box = {"v": 100}
        return box, panning.PanView(get=lambda: box["v"], put=lambda v: box.__setitem__("v", v))

    def test_a_press_that_does_not_move_is_a_click_where_it_went_down(self):
        box, v = self.view()
        p = panning.Panner()
        p.begin(v, (50, 50), 1.0)
        p.motion((53, 52))                                 # a pen tap wobbles a little
        self.assertEqual(p.release(), (50, 50))
        self.assertEqual(box["v"], 100)

    def test_a_press_that_moves_is_a_drag_and_no_click(self):
        box, v = self.view()
        p = panning.Panner()
        p.begin(v, (50, 200), 1.0)
        p.motion((50, 150))
        self.assertTrue(p.dragging)
        self.assertEqual(box["v"], 150)                    # dragged up 50: the list moves 50 down its length
        p.motion((50, 260))
        self.assertEqual(box["v"], 40)
        self.assertIsNone(p.release())

    def test_the_threshold_grows_with_the_text_size(self):
        self.assertEqual(panning.threshold(1.0), panning.PAN_START)
        self.assertEqual(panning.threshold(2.0), 2 * panning.PAN_START)

    def test_wheel_only_views_get_one_notch_per_step(self):
        got = []
        p = panning.Panner()
        p.begin(panning.PanView(notch=got.append, step=30), (0, 0), 1.0)
        for y in (20, 40, 65, 95):
            p.motion((0, y))
        self.assertEqual(sum(got), 3)                      # 95 px down = three notches up (towards the top)
        p.motion((0, -35))
        self.assertEqual(sum(got), -1)

    def test_a_press_cancelled_outside_the_window_is_nothing(self):
        box, v = self.view()
        p = panning.Panner()
        p.begin(v, (5, 5), 1.0)
        p.cancel()
        self.assertIsNone(p.release())
        self.assertFalse(p.holding)


class ChooseDragTests(unittest.TestCase):
    def types_window(self, size=(1920, 1080), scale=1.0, n=len(TYPES)):
        g = make_gui("main1_start", size, scale)
        items = [{"kind": "text", "label": t} for t in TYPES[:n]]
        g.session.requests.append({"id": 41, "kind": "choose", "title": "Choose a creature type", "min": 1, "max": 1, "items": items})
        frame(g, 2)
        self.assertIsInstance(g.modal, dlg.ChooseDialog)
        return g, g.modal

    def row(self, d, label):
        return next(r for r, i in d.tiles if d.items[i]["label"] == label)

    def test_dragging_karls_creature_type_list_scrolls_it_and_picks_nothing(self):
        for size, scale in (((1920, 1080), 1.0), ((2880, 1920), 2.0), ((1360, 840), 1.0)):
            with self.subTest(size=size, scale=scale):
                g, d = self.types_window(size, scale)
                self.assertTrue(d.bar.needed, "the test needs a list that scrolls")
                start = self.row(d, "Crab").center
                drag(g, start, (start[0], start[1] - 300))
                self.assertEqual(d.sel, [])                     # before: the press picked the row under the pen
                self.assertGreaterEqual(d.scroll, 250)
                self.assertEqual(g.session.commands("reply"), [])

    def test_a_tap_still_picks_a_row_and_a_double_tap_takes_it(self):
        g, d = self.types_window()
        tap(g, self.row(d, "Merfolk").center)
        self.assertEqual([d.items[i]["label"] for i in d.sel], ["Merfolk"])
        tap(g, self.row(d, "Wizard").center)
        tap(g, self.row(d, "Wizard").center)
        self.assertEqual([c["value"] for c in g.session.commands("reply")], [[TYPES.index("Wizard")]])

    def test_after_a_drag_the_row_under_the_pen_can_be_tapped(self):
        g, d = self.types_window()
        start = self.row(d, "Frog").center
        drag(g, start, (start[0], start[1] - 400))
        target = next((r, i) for r, i in d.tiles if d.body.contains(r) and d.items[i]["label"] not in TYPES[:8])
        tap(g, target[0].center)
        self.assertEqual(d.sel, [target[1]])

    def test_a_list_that_fits_still_picks_on_the_press(self):
        g, d = self.types_window(n=4)
        self.assertFalse(d.bar.needed)
        down(g, self.row(d, "Wizard").center)              # no release yet: nothing waits when the list can't be dragged
        self.assertEqual([d.items[i]["label"] for i in d.sel], ["Wizard"])
        up(g, self.row(d, "Wizard").center)

    def test_the_scroll_bar_still_drags_its_handle(self):
        g, d = self.types_window()
        grab = d.bar.handle.center
        down(g, grab)
        motion(g, (grab[0], d.body.bottom + 600))
        up(g, (grab[0], d.body.bottom + 600))
        frame(g)
        self.assertEqual(d.scroll, d.content_h - d.body.h)
        self.assertEqual(d.sel, [])

    def test_the_library_search_with_big_cards_drags_too(self):
        g = make_gui("main1_start", (1920, 1080), 1.75)
        g.choose_card_zoom = 2.0
        base = my_hand(g)[0]
        items = []
        for k in range(24):
            c = copy.deepcopy(base)
            c["id"], c["name"] = 9000 + k, f"Sliver {k}"
            items.append({"kind": "card", "card": c})
        g.session.requests.append({"id": 77, "kind": "choose_optional", "title": "Select a card from your library", "min": 0,
                                   "max": 1, "items": items})
        frame(g, 2)
        d = g.modal
        self.assertTrue(d.bar.needed)
        r = next(r for r, i in d.tiles if d.body.contains(r))
        drag(g, r.center, (r.centerx, r.centery - 250))
        self.assertEqual(d.sel, [])
        self.assertGreaterEqual(d.scroll, 200)
        r = next((r, i) for r, i in d.tiles if d.body.contains(r))
        tap(g, r[0].center)
        self.assertEqual(d.sel, [r[1]])


class FindTests(unittest.TestCase):
    def window(self, n=len(TYPES)):
        return ChooseDragTests.types_window(self, n=n)

    def test_typing_narrows_karls_list_and_the_choice_is_still_its_own_index(self):
        g, d = self.window()
        self.assertTrue(d.has_find())
        for ch in "merf":
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord(ch), unicode=ch, mod=0))
        frame(g)
        shown = [d.items[i]["label"] for _r, i in d.tiles]
        self.assertEqual(shown, ["Merfolk"])
        tap(g, d.tiles[0][0].center)
        tap(g, button_rect(d, "done").center)
        self.assertEqual([c["value"] for c in g.session.commands("reply")], [[TYPES.index("Merfolk")]])

    def test_a_short_list_has_no_find_box_and_letters_do_nothing_there(self):
        g, d = self.window(n=4)
        self.assertFalse(d.has_find())
        self.assertNotIn("keyboard", [n for _r, n in d.buttons])

    def test_backspace_escape_and_the_x(self):
        g, d = self.window()
        for ch in "bea":
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord(ch), unicode=ch, mod=0))
        frame(g)
        self.assertEqual(sorted(d.items[i]["label"] for _r, i in d.tiles), ["Bear", "Beast"])
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, unicode="\b", mod=0))
        self.assertEqual(d.find, "be")
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="\x1b", mod=0))
        self.assertEqual(d.find, "")
        self.assertFalse(d.done)                            # the first Esc only clears what was typed
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord("z"), unicode="z", mod=0))
        frame(g)
        tap(g, button_rect(d, "clear_find").center)
        self.assertEqual(d.find, "")

    def test_nothing_matches_says_so(self):
        g, d = self.window()
        for ch in "qqq":
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord(ch), unicode=ch, mod=0))
        frame(g)
        self.assertEqual(d.tiles, [])

    def test_the_picked_one_stays_in_view_while_you_type(self):
        g, d = self.window()
        tap(g, ChooseDragTests.row(self, d, "Crab").center)
        for ch in "wiz":
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord(ch), unicode=ch, mod=0))
        frame(g)
        self.assertEqual(sorted(d.items[i]["label"] for _r, i in d.tiles), ["Crab", "Wizard"])

    def test_the_window_does_not_change_height_as_you_type(self):
        g, d = self.window()
        h = d.rect.h
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord("b"), unicode="b", mod=0))
        frame(g)
        self.assertEqual(d.rect.h, h)


class KeyboardTests(unittest.TestCase):
    def types(self, size=(1920, 1080), scale=1.0):
        return ChooseDragTests.types_window(self, size, scale)

    def test_the_keyboard_types_into_the_find_box(self):
        g, d = self.types()
        tap(g, button_rect(d, "keyboard").center)
        self.assertIs(g.keyboard.owner, d)
        for k in "merf":
            press_key(g, k)
        self.assertEqual(d.find, "merf")
        press_key(g, "back")
        self.assertEqual(d.find, "mer")
        press_key(g, "shift")
        press_key(g, "w")
        self.assertEqual(d.find, "merW")
        press_key(g, "w")
        self.assertEqual(d.find, "merWw")                    # Shift is for one letter
        press_key(g, "hide")
        self.assertFalse(g.keyboard.open)

    def test_keys_go_to_the_window_never_to_the_tables_shortcuts(self):
        g = make_gui("main1_start", (1920, 1080), 1.0)
        g.session.requests.append({"id": 5, "kind": "input", "title": "Name a card", "initial": ""})
        frame(g, 2)
        d = g.modal
        self.assertIsInstance(d, dlg.InputDialog)
        tap(g, button_rect(d, "keyboard").center)
        sound = dict(g.sound)
        for k in "mhj":                                     # M = sound, H = help, J = Judge?! on the table
            press_key(g, k)
        self.assertEqual(d.text, "mhj")
        self.assertEqual(g.sound, sound)
        self.assertIs(g.modal, d)
        press_key(g, "enter")
        self.assertEqual([c["value"] for c in g.session.commands("reply")], ["mhj"])
        frame(g)
        self.assertFalse(g.keyboard.open)                   # it closes with the window that opened it

    def test_a_tap_on_the_keyboard_never_reaches_what_is_under_it(self):
        g, d = self.types()
        tap(g, button_rect(d, "keyboard").center)
        frame(g)
        r = next(r for r, n in g.keyboard.keys if n == "z")
        self.assertTrue(any(t.collidepoint(r.center) for t, _i in d.tiles), "the test needs a row under the key")
        tap(g, r.center)
        self.assertEqual(d.sel, [])                         # the row under the key is not picked
        self.assertEqual(d.find, "z")
        pad = (g.keyboard.rect.x + 2, g.keyboard.rect.y + 2)
        tap(g, pad)
        self.assertEqual(d.sel, [])
        self.assertTrue(g.keyboard.open)

    def test_move_puts_it_at_the_top_and_back(self):
        g, d = self.types()
        tap(g, button_rect(d, "keyboard").center)
        frame(g)
        self.assertGreater(g.keyboard.rect.y, g.L.H // 2)
        press_key(g, "move")
        frame(g)
        self.assertLess(g.keyboard.rect.bottom, g.L.H // 2 + 1)
        press_key(g, "move")
        frame(g)
        self.assertGreater(g.keyboard.rect.y, g.L.H // 2)

    def test_it_fits_and_no_two_keys_overlap_at_every_size(self):
        for size, scale in (((1100, 700), 2.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((2880, 1920), 2.0),
                            ((4096, 2160), 1.75), ((900, 600), 1.0)):
            with self.subTest(size=size, scale=scale):
                kb = osk.Keyboard()
                kb.show(object())
                r = kb.layout(size[0], size[1], scale)
                self.assertTrue(pygame.Rect(0, 0, *size).contains(r), r)
                self.assertLessEqual(r.h, size[1] * osk.MAX_SHARE + 1)
                rects = [k for k, _n in kb.keys]
                for a in range(len(rects)):
                    self.assertTrue(r.contains(rects[a]))
                    self.assertGreaterEqual(rects[a].h, 20)
                    for b in range(a + 1, len(rects)):
                        self.assertFalse(rects[a].colliderect(rects[b]), (kb.keys[a][1], kb.keys[b][1]))

    def test_the_deck_search_box_has_a_keyboard_and_types(self):
        g = DeckScreen.gui(self)
        try:
            m = g.menu
            r = next(r for r, n in m.btns if n == "keyboard")
            tap(g, r.center)
            self.assertIs(g.keyboard.owner, m)
            text_size = g.text_scale
            for k in ("-", "m", "a"):                        # "-" first: with nothing typed yet it was "smaller text" there
                press_key(g, k)
            self.assertEqual(m.search, "-ma")
            self.assertEqual(g.text_scale, text_size)
        finally:
            DeckScreen.done(self)

    def test_the_bug_report_form_has_a_keyboard(self):
        g = make_gui("main1_start", (1920, 1080), 1.0)
        g.open_report()
        frame(g)
        rep = g.overlay
        tap(g, button_rect(rep, "keyboard").center)
        self.assertIs(g.keyboard.owner, rep)
        before = rep.fields[rep.focus].text if hasattr(rep.fields[rep.focus], "text") else None
        for k in "hi":
            press_key(g, k)
        after = rep.fields[rep.focus].text if hasattr(rep.fields[rep.focus], "text") else None
        self.assertEqual(after, (before or "") + "hi")


class DeckScreen:
    @staticmethod
    def gui(case, size=(1920, 1080), scale=1.75):
        case.tmp = tempfile.mkdtemp()
        lib, samples = os.path.join(case.tmp, "my_decks"), os.path.join(case.tmp, "sample_decks")
        os.makedirs(lib)
        os.makedirs(samples)
        files = sorted(glob.glob(os.path.join(BASE, "sample_decks", "*.txt")))
        for f in files:
            shutil.copy(f, samples)
        for k, f in enumerate(files[:9]):
            shutil.copy(f, os.path.join(lib, f"copy_{k}_" + os.path.basename(f)))
        g = ft.ForgeTable(FakeSession(load_state("main1_start"), load_log()), StubStore(), settings_path=None, window_size=size,
                          launcher=FakeLauncher(), deck_dirs=(lib, samples, case.tmp))
        g.text_scale = scale
        frame(g, 2)
        g.open_menu()
        frame(g, 2)
        return g

    @staticmethod
    def done(case):
        shutil.rmtree(case.tmp, ignore_errors=True)


class OtherListTests(unittest.TestCase):
    def test_the_deck_list_drags_and_a_tap_still_picks(self):
        g = DeckScreen.gui(self)
        try:
            m = g.menu
            self.assertTrue(m.bar.needed)
            mine = m.mine_id
            r, entry = m.rows[1]
            drag(g, r.center, (r.centerx, r.centery - 250))
            self.assertEqual(m.mine_id, mine)                # nothing picked by the drag
            self.assertGreaterEqual(m.scroll, 200)
            r, entry = next((r, e) for r, e in m.rows if m.list_rect.contains(r) and e.id != mine)
            tap(g, r.center)
            self.assertEqual(m.mine_id, entry.id)
        finally:
            DeckScreen.done(self)

    def test_the_game_log_drags(self):
        g = make_gui("main1_start", (1100, 700), 1.0)
        g.session.handle({"t": "log", "entries": [{"type": "INFO", "message": f"line {k}"} for k in range(200)]})
        frame(g, 2)
        self.assertTrue(g.log_overflow, "the test needs a log that scrolls")
        r = g.log_rect
        start = (r.centerx, r.y + r.h // 3)
        drag(g, start, (start[0], min(r.bottom - 4, start[1] + 150)))
        self.assertGreater(g.log_scroll, 0)                  # dragged down: older lines

    def test_help_and_licenses_drag(self):
        g = make_gui("main1_start", (1100, 700), 2.0)
        g.open_help()
        frame(g)
        h = g.modal
        self.assertTrue(h.max_scroll)
        start = (h.viewport.centerx, h.viewport.centery)
        drag(g, start, (start[0], start[1] - 200))
        self.assertGreater(h.scroll, 150)
        h.done = True
        frame(g)
        g.open_licenses()
        frame(g)
        lic = g.modal
        if getattr(lic, "pane_max_scroll", 0):
            start = lic.pane_rect.center
            drag(g, start, (start[0], start[1] - 120))
            self.assertGreater(lic.pane_scroll, 60)


if __name__ == "__main__":
    unittest.main()
