# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CF1 (27 Oct 2026): the 32-second freeze drawing a choose list on Karl's Surface (crash log, 23 Oct 21:36, 0.28.53.11:
main thread in forge_dialogs draw -> draw_items -> gfx.draw_rich, a ChooseDialog open, "unresponsive for about 32 seconds").

Forge asks "Choose a card name" (Pithing Needle, Meddling Mage, Cabal Therapy...) with every card name it knows: 35,291 plain
choices on Forge 9a6f776. ChooseDialog wrapped, measured and drew every one of them every frame, and row_labels compared each
label with all the others (list.count): about 55-60 s a frame at 2880x1920, text size 1.5, in the sandbox. Now the rows are laid
out once (text_layout), only the rows that show are drawn, and a short label is checked against the width without font.size.

  LongListTests   draw_rich only for the rows that show; nothing wrapped again on the next frame; the last row can be reached
                  and picked; the Find box still narrows the list; a frame of the whole list well under a second
  LabelTests      the same labels as before (card numbers kept only where two choices would look the same)
  FitTests        fits_one_line never claims a line fits that font.size says is too wide
"""
import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_dialogs as dlg
from tests.test_forge_table import frame, make_gui

N = 35291                                                       # Forge 9a6f776's "Choose a card name" list (Pithing Needle)
WORDS = ["Goblin", "Ancestral", "Recall", "Sol", "Ring", "Dragon", "of", "the", "Lotus", "Wurm", "Elder", "Shivan", "Ach! Hans,",
         "Run!", "Æther", "Vial", "Jötun", "Grunt", "Lim-Dûl's", "Vault"]


def names(n=N):
    """n distinct card-name-like labels, a few long enough to wrap."""
    out = []
    for k in range(n):
        w = [WORDS[(k * 7 + j * 3) % len(WORDS)] for j in range(1 + k % 4)]
        if k % 997 == 0:
            w = w * 12                                          # a very long one: wraps onto more lines
        out.append(" ".join(w) + f" {k}")
    return out


def name_window(size=(2880, 1920), scale=1.5, labels=None):
    g = make_gui("main1_start", size, scale)
    items = [{"kind": "text", "label": t} for t in (labels or names())]
    g.session.requests.append({"id": 77, "kind": "choose", "title": "Choose a card name", "min": 1, "max": 1, "items": items})
    frame(g)
    assert isinstance(g.modal, dlg.ChooseDialog)
    return g, g.modal


class Counting:
    """Counts calls of a forge_dialogs function while still calling it."""

    def __init__(self, name, only=None):
        self.name, self.calls, self.only = name, 0, only
        self.real = getattr(dlg, name)

    def __call__(self, *a, **k):
        if self.only is None or (a and a[0] in self.only):
            self.calls += 1
        return self.real(*a, **k)

    def __enter__(self):
        self.patch = mock.patch.object(dlg, self.name, self)
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class LongListTests(unittest.TestCase):
    def test_only_the_rows_that_show_are_drawn(self):
        for size, scale in (((2880, 1920), 1.5), ((1920, 1080), 1.0)):
            with self.subTest(size=size, scale=scale):
                g, d = name_window(size, scale)
                most = d.body.h // int(38 * g.L.fs) + 2         # rows are at least 38 * fs high
                with Counting("draw_rich") as dr:
                    frame(g)
                self.assertGreater(dr.calls, 3)
                self.assertLessEqual(dr.calls, most * 2)     # before: one call per name, 35,291 a frame
                self.assertLessEqual(len(d.tiles), most)
                self.assertTrue(all(r.bottom >= d.body.y and r.top <= d.body.bottom for r, _i in d.tiles))

    def test_the_next_frame_wraps_nothing_again(self):
        g, d = name_window()
        labels = set(names())
        with Counting("wrap_text", labels) as wr, Counting("fits_one_line", labels) as fit:
            frame(g, 2)
        self.assertEqual((wr.calls, fit.calls), (0, 0))         # before: every label wrapped twice a frame

    def test_typing_in_find_wraps_no_name_again(self):
        g, d = name_window()
        labels = set(names())
        with Counting("wrap_text", labels) as wr, Counting("fits_one_line", labels) as fit:
            for ch in "lot":
                g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord(ch), unicode=ch, mod=0))
                frame(g)
        self.assertEqual((wr.calls, fit.calls), (0, 0))         # each name is laid out once, whatever is typed

    def test_the_last_name_can_be_reached_and_picked(self):
        g, d = name_window()
        d.scroll = 10 ** 9
        frame(g)
        last = len(d.items) - 1
        rect = next(r for r, i in d.tiles if i == last)
        self.assertTrue(d.body.contains(rect) or d.body.colliderect(rect))
        self.assertAlmostEqual(d.scroll, d.content_h - d.body.h, delta=1)
        d.pick(g, last)
        self.assertEqual(d.sel, [last])
        first_shown = min(i for _r, i in d.tiles)
        self.assertGreater(first_shown, len(d.items) - 200)       # the rows at the top are not drawn down there

    def test_the_rows_follow_each_other_without_gaps_or_overlaps(self):
        g, d = name_window(labels=names(400))
        d.scroll = 700
        frame(g)
        rows = sorted(d.tiles, key=lambda t: t[0].y)
        self.assertEqual([i for _r, i in rows], sorted(i for _r, i in rows))
        for (a, _i), (b, _j) in zip(rows, rows[1:]):
            self.assertEqual(b.y, a.bottom + 6)
        rows_t, tops, text_h = d.text_layout(g, d.body.w)
        self.assertEqual(text_h, sum(h + 6 for _i, _l, h in rows_t))
        self.assertEqual(tops[5], sum(h + 6 for _i, _l, h in rows_t[:5]))

    def test_find_still_narrows_the_long_list(self):
        g, d = name_window()
        self.assertTrue(d.has_find())
        for ch in "lotus 12":
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=ord(ch), unicode=ch, mod=0))
        frame(g)
        rows_t = d.text_rows(g, d.body.w)
        want = [i for i, t in enumerate(names()) if "lotus" in t.lower() and "12" in t]
        self.assertGreater(len(want), 100)
        self.assertEqual([i for i, _l, _h in rows_t], want)
        shown = [i for _r, i in d.tiles]
        self.assertEqual(shown, want[:len(shown)])
        d.pick(g, shown[0])
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, unicode="", mod=pygame.KMOD_CTRL))
        frame(g)
        self.assertEqual(len(d.text_rows(g, d.body.w)), N)

    def test_a_frame_of_the_whole_list_takes_well_under_a_second(self):
        g, d = name_window()
        t = time.perf_counter()
        frame(g)
        self.assertLess(time.perf_counter() - t, 1.0)           # before: about 55-60 s in the sandbox


class LabelTests(unittest.TestCase):
    def test_card_numbers_are_kept_only_where_two_choices_would_look_the_same(self):
        d = dlg.ChooseDialog("x", [])
        items = [{"kind": "text", "label": "Goblin (12)"}, {"kind": "text", "label": "Goblin (13)"},
                 {"kind": "text", "label": "Elf (14)"}, {"kind": "player", "label": "Karl"}]
        self.assertEqual(d.row_labels(items), ["Goblin (12)", "Goblin (13)", "Elf", "Player: Karl"])


class FitTests(unittest.TestCase):
    def test_a_label_said_to_fit_does_fit(self):
        pygame.font.init()
        g = make_gui("main1_start", (1360, 840), 1.0)
        f = g.font("body")
        labels = names(3000) + ["WWWWWWWWWWWWWWWWWWWW", "iiiiiiiiiiiiiiiiiiiiiiiiiiiiii", "AVAVAVAVAVAVAVAV To", "Æther Vial"]
        for width in (80, 150, 300, 600):
            adv = {}
            for t in labels:
                if dlg.fits_one_line(t, f, width, adv):
                    self.assertLessEqual(f.size(t)[0], width, (t, width))

    def test_mana_symbols_and_line_breaks_are_left_to_wrap_text(self):
        pygame.font.init()
        f = pygame.font.Font(None, 20)
        self.assertIsNone(dlg.fits_one_line("Pay {2}{U}", f, 2000))
        self.assertIsNone(dlg.fits_one_line("a\nb", f, 2000))
        self.assertEqual(dlg.fits_one_line("Sol Ring", f, 2000), ["Sol Ring"])


if __name__ == "__main__":
    unittest.main()
