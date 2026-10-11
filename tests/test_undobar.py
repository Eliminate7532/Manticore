# SPDX-License-Identifier: GPL-3.0-or-later
"""Lane A, the Undo-button width bug (found by patch UI6, 8 Oct): Undo came and went with the window's WIDTH.

UI6's notes: "On stack_two_late at 840 high, 100% text: window widths 1300, 1320, 1340 and 1440, 1460 draw only End Turn and
Pass priority; 1360-1420 and 1480-1540 draw Undo too." Three causes in forge_table.draw_action_bar, each fixed and each with a
test here:
  1. fit() took the first button font whose buttons used under half the bar even when the words then didn't fit, so the
     candidate with Undo failed and the one without it won (a wider bar could reach the bigger font);
  2. at 100-150% text the bar is too short for UI3's stacked Undo, and the headline - which grows with the window's width -
     left the hint line no room: Undo gave way to the hint. Now the hint may shorten ("...") to keep Undo, as UI3 already
     chose at 200%; the headline still never shortens to keep Undo (UI2: the words first);
  3. UI3's "is the bar tall enough to stack Undo" measured the body face's line height, but round 32 draws buttons in the
     display face, whose line height jumps at 16 px (where it starts) while its letters get smaller.
"""
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gfx
import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import frame


def table(state, size, scale):
    st = load_state(state) if isinstance(state, str) else state
    gui = ft.ForgeTable(FakeSession(st, load_log()), StubStore(), settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.animations = False
    lines = []
    draw_line = gui.draw_line

    def record(text, x, y, font, colour):
        if gui.L.bar.collidepoint(x, y):
            lines.append((text, colour))
        return draw_line(text, x, y, font, colour)
    gui.draw_line = record
    frame(gui, 2)
    lines.clear()                    # keep the last frame's lines only: the headline, then the hint, once a frame
    frame(gui, 1)
    gui.bar_lines = list(lines)
    return gui


def bar_buttons(gui):
    return {d.get("name"): r for r, k, d in gui.hits if k == "button" and gui.L.bar.colliderect(r)}


def words(gui):
    """(headline, hint) as prompt_view gives them."""
    me = gui.session.me()
    p = gui.prompt()
    return ft.prompt_view(p.get("message", ""), me["name"], gui.mulligans, bool(gui.pool_items(me)), gui.pay_trouble(),
                          gui.input_kind())[:2]


class UndoAtEveryWidthTests(unittest.TestCase):
    def test_at_100_percent_undo_is_on_the_bar_at_every_width(self):
        # UI6's own widths (1300-1340, 1440-1460 at 840 high) and a sweep around them; red before the fix at 1300 x 840 and others
        widths = sorted(set(range(1100, 1561, 20)) | {1300, 1320, 1340, 1440, 1460})
        for state in ("stack_two_late", "main1_lands"):
            for h in (840, 1080):
                for w in widths:
                    with self.subTest(state=state, size=(w, h)):
                        gui = table(state, (w, h), 1.0)
                        buttons = bar_buttons(gui)
                        self.assertIn("undo", buttons)
                        self.assertTrue(gui.L.bar.contains(buttons["undo"]), buttons["undo"])
                        others = [r for n, r in buttons.items() if n != "undo"]
                        self.assertFalse(any(buttons["undo"].colliderect(r) for r in others))

    def test_a_wider_window_never_loses_undo_that_a_narrower_one_shows(self):
        # each run is a stretch of widths where the old code showed Undo, then lost it at a wider width:
        #   (1080 high, 150%) 1360 shown, 1400 and 1440 not - cause 3 (the display face's line height at 16 px);
        #   (840 high, 200%) 1080 shown, 1120 not - cause 3;
        #   (840 high, 125%) 1080 shown, 1120 not - cause 1 (no font under half the bar, and the hint couldn't shorten)
        for state, h, scale, widths in (("main1_lands", 1080, 1.5, range(1360, 1521, 40)),
                                        ("main1_lands", 840, 2.0, range(1080, 1241, 40)),
                                        ("main1_lands", 840, 1.25, range(1080, 1201, 40))):
            seen = False
            for w in widths:
                with self.subTest(state=state, size=(w, h), scale=scale):
                    shown = "undo" in bar_buttons(table(state, (w, h), scale))
                    if seen:
                        self.assertTrue(shown, "Undo was on the bar in a narrower window")
                    seen = seen or shown
            self.assertTrue(seen)


class WordsFirstTests(unittest.TestCase):
    """UI2's rule stays: Undo never costs the headline. Only the hint line under it may shorten."""

    def test_the_headline_is_whole_whenever_undo_is_kept(self):
        for state, size, scale in (("stack_two_late", (1300, 840), 1.0), ("stack_two_late", (1440, 840), 1.0),
                                   ("main1_lands", (1120, 840), 1.25), ("paying_mana", (1360, 840), 1.0),
                                   ("main1_lands", (1920, 1080), 2.0)):
            with self.subTest(state=state, size=size, scale=scale):
                gui = table(state, size, scale)
                self.assertIn("undo", bar_buttons(gui))
                head = [t for t, c in gui.bar_lines if c != gfx.HINT_LINE_TEXT]
                self.assertTrue(head)
                self.assertFalse(head[-1].endswith("..."), head)
                self.assertEqual(" ".join(head).split(), words(gui)[0].split())

    def test_at_150_percent_undo_may_go_but_the_headline_is_always_whole(self):
        # Replaces test_undo_still_gives_way_to_the_headline_when_both_cannot_fit (10 Oct), which pinned 1200 x 840 at 150%:
        # Undo off in the sandbox, on with Windows' fonts (Karl's quick_tests; the sandbox gives the same three buttons, to the
        # pixel, with every text measured 1% narrower). At this size it is not the headline that sends Undo away but UI2's
        # other rule: the buttons may take at most 60% of the bar - 278 px of a 463 px bar here (60.04%), one pixel less on
        # Windows. That edge comes and goes every few window widths, so this checks every width from 1100 to 1320: the headline
        # whole each time, Undo on the bar or not, and Undo gone at some of them. UI2's words-first rule (Undo never kept at the
        # headline's cost) is pinned by test_a_headline_too_long_for_the_bar_keeps_undo_off below.
        gone = []
        for w in range(1100, 1321, 10):
            with self.subTest(width=w):
                gui = table("main1_lands", (w, 840), 1.5)
                head = [t for t, c in gui.bar_lines if c != gfx.HINT_LINE_TEXT]
                self.assertEqual(" ".join(head).split(), words(gui)[0].split())      # the headline whole, Undo or not
                buttons = bar_buttons(gui)
                for name, r in buttons.items():
                    self.assertTrue(gui.L.bar.contains(r), (name, r))
                if "undo" not in buttons:
                    gone.append(w)
        self.assertTrue(gone, "Undo was on the bar at every width from 1100 to 1320 x 840, 150% text")

    def test_a_headline_too_long_for_the_bar_keeps_undo_off(self):
        # Forge's own sentence, too long for the bar even at 1920 x 1080: Undo gives its room to the words (UI2), and the
        # hint's "may shorten" never lets the HEADLINE be cut to keep Undo
        st = load_state("main1_lands")
        st["prompt"]["message"] = ("Looking at cards in AI 1 (Kinnan, Bonder Prodigy)'s library: Sol Ring, Basalt Monolith, "
                                   "Thrasios, Triton Hero, Birds of Paradise, Llanowar Elves, Fyndhorn Elves")
        st["input"] = ""
        gui = table(st, (1920, 1080), 1.0)
        self.assertEqual(sorted(bar_buttons(gui)), ["cancel", "ok"])
        head = " ".join(t for t, c in gui.bar_lines if c != gfx.HINT_LINE_TEXT)
        self.assertIn("Llanowar Elves", head)                          # more of the words than with three buttons

    def test_a_short_bar_gives_up_the_hint_not_undo(self):
        # 1300 x 840 at 100%: the headline needs both of the bar's lines and the bar is too short to stack Undo (UI3), so the
        # hint line ("Respond (glowing cards) or Pass priority ...") gives way - before this fix Undo did, and the hint stayed
        gui = table("stack_two_late", (1300, 840), 1.0)
        self.assertEqual(sorted(bar_buttons(gui)), ["cancel", "ok", "undo"])
        self.assertEqual([t for t, c in gui.bar_lines if c == gfx.HINT_LINE_TEXT], [])
        self.assertTrue(words(gui)[1])                                 # there was a hint to give up
        # a wider bar has room for both again: the whole hint, and Undo
        wide = table("stack_two_late", (1440, 840), 1.0)
        self.assertIn("undo", bar_buttons(wide))
        self.assertEqual([t for t, c in wide.bar_lines if c == gfx.HINT_LINE_TEXT], [words(wide)[1]])


if __name__ == "__main__":
    unittest.main()
