# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG16c: two of the RPG16 review's reading-only notes (claude/REVIEW_RPG16_2026-10-24.md), fixed though nothing shipped
hits them yet:

  N5  foes walked through the people who come and go (Foe._free looked only at the map's walls)
  N3  an event with no choice that gives several lines said only the last one on the map's message line
"""
import unittest
from unittest import mock

from rpg import events, explore
from tests.test_rpg16 import _window_base, at, explorer_at, place

OUT = "ashfen_outskirts"
GRAVES = "ashfen_outskirts#night_graves"
DT = 0.05


def tiles_under(f, a):
    x0, y0, x1, y1 = f.box
    return {(c, r) for c in range(int(x0 // a.tw), int((x1 - 1e-6) // a.tw) + 1)
            for r in range(int(y0 // a.th), int((y1 - 1e-6) // a.th) + 1)}


def walk(f, a, frames=4000):
    seen = set()
    f.wake()
    for _ in range(frames):
        f.step(DT)
        seen |= tiles_under(f, a)
    return seen


class FoeAvoidTests(unittest.TestCase):
    def fresh_foe(self):
        ex = place(explorer_at(OUT, at(1, 22)), (21, 26))
        return ex, explore.Foe(ex.area, next(t for t in ex.area.triggers if t.spot == GRAVES))

    def test_a_foe_never_walks_onto_a_tile_it_must_avoid(self):
        ex, f = self.fresh_foe()
        a = ex.area
        home = a.tile_at(*f.home)
        visited = walk(f, a)
        others = sorted(t for t in visited if t != home and abs(t[0] - home[0]) + abs(t[1] - home[1]) >= 2)
        self.assertTrue(others, "guard: without a tile to avoid the foe walks about")
        keep_off = others[len(others) // 2]
        ex, f = self.fresh_foe()                                         # the same walk again (same spot, same seed)
        f.avoid = {keep_off}
        self.assertNotIn(keep_off, walk(f, a), f"walked onto {keep_off}")

    def test_the_explorer_gives_its_foes_the_people_who_are_out(self):
        ex = place(explorer_at(OUT, at(1, 22)), (21, 26))
        ex.walker.blockers = {(1, 1)}                                    # what the people out at this hour make (the step's
        ex.step(1, 0, DT)                                                # own refresh makes a new set afterwards)
        self.assertTrue(ex.active_foes(), "guard: a foe near enough to move")
        for f in ex.active_foes():
            self.assertEqual(f.avoid, {(1, 1)})


class EventLinesTests(_window_base()):
    def test_every_line_of_an_effect_is_said(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "lantern_hollow", "start")
        eid = next(k for k, e in events.events().items() if "choice" not in e)
        f.start_event(g, eid)
        with mock.patch.object(events, "apply", return_value=["You found 5 gold.", "A card: Lightning Bolt."]):
            f.event_do(g, "close")
        text = (f.message or ("",))[0]
        self.assertIn("You found 5 gold.", text)
        self.assertIn("A card: Lightning Bolt.", text)


if __name__ == "__main__":
    unittest.main()
