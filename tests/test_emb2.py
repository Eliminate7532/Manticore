# SPDX-License-Identifier: GPL-3.0-or-later
"""Round EMB2 (16 Oct 2026): two leftovers EMB1's review saw in pod panels (docs/rounds/EMB1.md, "Seen there, older than EMB1").

  - "a full-length compact name's text box reaches about 2 px into the priority dot (main does the same)": the name's room
    (w - life width - 14) ended 3 px inside the dot. With priority the name now stops DOT_GAP px before the dot.
  - "a name that starts with " (" can shorten to a bare "..."": name_floor kept the part before " (" - nothing, for such a name.
    It now keeps the name's own text when that part is empty.

  DotTests    every opponent with priority, names as the bridge makes them, 3 and 4 players, big text and 100%: no name box
              reaches the dot; without priority the name is drawn as before (only a name next to the dot shortens)
  FloorTests  name_floor and a drawn " (...)" name next to moved chips
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import forge_table as ft
from tests.test_emb1 import BIG_TEXT, MONARCH, badge_hits, commander_table, named_pod
from tests.test_forge_table import frame

SIZES = BIG_TEXT + (((1360, 840), 1.0), ((1920, 1080), 1.0))
GAP = 4                  # px between a name and the dot - a number of its own, so a smaller ft.DOT_GAP can't pass the test
DECKS = ("", "Kinnan", "Point It at Their Face", "We Just Need to Punch Them (Stompy)")


def pod(players, deck, command=(), damage=None, priority=True, names=None):
    names = names or [(f"AI {k} ({deck})" if deck else f"AI {k}") for k in (1, 2, 3)]
    st = named_pod(names, list(command), damage=damage)
    if players == 3:
        st["players"] = st["players"][:3]
    for p in st["players"]:
        p["priority"] = priority and p["id"] != st["me"]
    return st


def drawn(st, size, scale):
    """Draw `st`; return (gui, [(name text, its rect)], [priority dot rects])."""
    names, dots = [], []
    real_t, real_c = ft.draw_text, pygame.draw.circle
    wanted = {p["name"] for p in st["players"] if p["id"] != st["me"]}

    def spy_t(surf, text, x, y, font, *a, **k):
        r = real_t(surf, text, x, y, font, *a, **k)
        if isinstance(text, str) and isinstance(r, pygame.Rect) and any(
                text == n or (text.endswith("...") and n.startswith(text[:-3])) for n in wanted):
            names.append((text, r))
        return r

    def spy_c(surf, colour, center, radius, *a, **k):
        if tuple(colour)[:3] == tuple(ft.GREEN)[:3] and radius == 5:
            dots.append(pygame.Rect(center[0] - radius, center[1] - radius, 2 * radius, 2 * radius))
        return real_c(surf, colour, center, radius, *a, **k)
    with mock.patch.object(ft, "draw_text", spy_t), mock.patch.object(ft.pygame.draw, "circle", spy_c):
        gui, _ = commander_table(st, size, scale)
        names.clear()
        dots.clear()
        frame(gui, 1)
    return gui, names, dots


def same_panel(gui, a, b):
    return any(p.contains(a) and p.contains(b) for p in gui.panel_rects.values())


class DotTests(unittest.TestCase):
    def test_a_name_never_reaches_the_priority_dot(self):
        """EMB1's code: 207 of 2352 name boxes in this kind of sweep reached 1-3 px into the dot."""
        checked = 0
        for players in (3, 4):
            for size, scale in SIZES:
                for deck in DECKS:
                    for command, dmg in (((), None), (MONARCH, 18)):
                        gui, names, dots = drawn(pod(players, deck, command, dmg), size, scale)
                        with self.subTest(players=players, size=size, scale=scale, deck=deck, badges=bool(command)):
                            self.assertEqual(len(dots), players - 1, "one dot per opponent with priority")
                            for text, nr in names:
                                for dot in dots:
                                    if not same_panel(gui, nr, dot):
                                        continue
                                    checked += 1
                                    self.assertFalse(nr.colliderect(dot), f"the name {text!r} {nr} reaches the dot {dot}")
                                    if nr.top < dot.bottom and dot.top < nr.bottom:
                                        self.assertLessEqual(nr.right + GAP, dot.left,
                                                             f"the name {text!r} {nr} is closer than {GAP} px to the dot {dot}")
        self.assertGreater(checked, 100, "too few name/dot pairs: the test proves nothing")

    def test_only_a_name_next_to_the_dot_shortens(self):
        """Without priority nothing changes. With it, a short name is drawn whole; only a name long enough to reach the dot is
        shortened - and in this sweep some are (else the test would not exercise the fix)."""
        shortened = 0
        for players in (3, 4):
            for size, scale in SIZES:
                for deck in DECKS:
                    _g, off, _d = drawn(pod(players, deck, priority=False), size, scale)
                    _g, on, _d = drawn(pod(players, deck), size, scale)
                    with self.subTest(players=players, size=size, scale=scale, deck=deck):
                        self.assertEqual(len(on), len(off))
                        for (t_on, r_on), (t_off, r_off) in zip(sorted(on, key=lambda t: t[1].y), sorted(off, key=lambda t: t[1].y)):
                            self.assertEqual(r_on.topleft, r_off.topleft, "the name moved")
                            if t_on != t_off:
                                shortened += 1
                                self.assertTrue(t_on.endswith("..."), (t_on, t_off))
                                self.assertLess(r_on.w, r_off.w)
                            if deck == "":
                                self.assertEqual(t_on, t_off, "a short name was shortened")
        self.assertGreater(shortened, 0, "no name was ever next to the dot: the sweep doesn't reach the case")

    def test_a_1v1_panel_keeps_its_name_off_the_dot_too(self):
        """A 1v1 panel at 100% is not compact (its dot sits at the right end and the name has w - 26), but at big text it is - and
        there EMB1's code put the name 1-3 px into the dot too (red on EMB1)."""
        st = pod(4, "We Just Need to Punch Them (Stompy)")
        st["players"] = st["players"][:2]
        for size, scale in SIZES:
            gui, names, dots = drawn(st, size, scale)
            with self.subTest(size=size, scale=scale):
                self.assertEqual(len(dots), 1)
                for _t, nr in names:
                    self.assertFalse(nr.colliderect(dots[0]))


class FloorTests(unittest.TestCase):
    def test_name_floor_keeps_a_real_part(self):
        pygame.init()
        f = ft.get_font(20, True)
        self.assertEqual(ft.ForgeTable.name_floor("AI 1 (Kinnan)", f), f.size("AI 1...")[0])
        self.assertEqual(ft.ForgeTable.name_floor("AI 1", f), f.size("AI 1")[0])
        for name in (" (Kinnan, Bonder Prodigy)", "  (x)", " (We Just Need to Punch Them (Stompy))"):
            with self.subTest(name=name):
                self.assertGreater(ft.ForgeTable.name_floor(name, f), f.size("...")[0] + 2, "only room for a bare '...'")
        self.assertEqual(ft.ForgeTable.name_floor("Bartholomew", f), f.size("Bartholomew")[0])

    def test_a_paren_name_next_to_moved_chips_keeps_a_part_and_leaves_them_room(self):
        """At big text the Monarch moves onto the name row and the name gives way down to its floor (EMB1). For a name starting
        with " (" the floor is its first word and "..." now (name_floor's own test shows the bare "..." it was). Drawn: never a bare
        "...", and the chips still move off the piles - a floor as wide as the whole name would keep them under the piles."""
        names = [" (We Just Need to Punch Them (Stompy))", " (Point It at Their Face (Red Aggro))", " (Kinnan, Bonder Prodigy)"]
        moved = 0
        for size, scale in BIG_TEXT:
            for prio in (False, True):
                st = pod(4, "", MONARCH, priority=prio, names=names)
                gui, drawn_names, _dots = drawn(st, size, scale)
                piles = [r for r, k, _d in gui.hits if k in ("zone", "library")]
                with self.subTest(size=size, scale=scale, priority=prio):
                    self.assertEqual(len(drawn_names), 3, drawn_names)
                    for text, _r in drawn_names:
                        self.assertNotEqual(text.strip(), "...", "a bare '...' for a name")
                    for r, d in badge_hits(gui):
                        if d["player"] == gui.state["me"]:
                            continue
                        if not any(r.colliderect(p) for p in piles):
                            moved += 1
                            for _t, nr in drawn_names:
                                self.assertFalse(nr.colliderect(r), f"the name {nr} under the chip {r}")
        self.assertGreater(moved, 0, "no chip moved to the name row: the test doesn't reach the floor")


if __name__ == "__main__":
    unittest.main()
