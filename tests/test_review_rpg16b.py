# SPDX-License-Identifier: GPL-3.0-or-later
"""A cold review of Round RPG16b (the fixes to the RPG16 review), 24 Oct 2026: one failing test per bug found, and guard tests
that pass and prove the harness can see the behaviour. Nothing here changes the program.

  S1  in Lantern Hollow the square, the well and Pell's day spot are on screen from every tile of the map (at every window size
      tried), so with RPG16b's view box (R1) and one-group-per-person rule (R3) Elder Hesk never goes home, Pell never goes to the
      lamps and Wenna never comes to the well while you walk the town - only a forced refresh (a shop, the inn, a new map, a load)
      moves them; the new night hints (R8) then send you to Hesk's house while he stands in the square
  S2  R4 is fixed only for the walker's centre tile: a save whose feet box overlaps Wenna's tile (centre one tile east) still walls
      the walker in after a load, and the clock stops
  S3  R6 is caught only for OSError/ValueError/KeyError/TypeError: an event in events.json that isn't a table (a list) raises
      AttributeError out of the walk
  S4  clock.pass_minutes accepts infinity and writes it; since R9 clock.now reads that as 17:00 on day 1, so an event's
      "minutes": Infinity sends the clock back days ("never back" in its own docstring)
  S5  R4's forced rule drops a person's old place even when the new one is under the walker: a load on Hesk's house tile at
      night leaves Elder Hesk (one who must always be somewhere) nowhere

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest
tests.test_review_rpg16b
"""
import os
import tempfile
import unittest
from unittest import mock

from rpg import clock, events, explore, hints
from tests.test_rpg16 import at, explorer_at, place, press, walker_tile
from tests.test_review_rpg16 import screen_view_half

TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
HESK_SQUARE, HESK_HOME = "npc:elder@17,16", "npc:elder@32,21"
PELL_DAY, PELL_LAMPS = "npc:lamplighter@23,12", "npc:lamplighter@22,18"
WENNA = "npc:mourner@20,15"
GATE_GUARD = "npc:gate_guard@35,15"
WINDOWS = ((1920, 1080), (1280, 720))


def at_window(ex, w=1920, h=1080):
    """The view half rpg_screens.draw_map gives the Explorer for a window of w x h (rpg_screens.py l.1603)."""
    cam = explore.Camera(w, h, explore.Camera.zoom_for(h, ex.area.th))
    ex.view_half = screen_view_half(ex, cam)
    return ex


def free_tiles(a):
    return [(c, r) for c in range(a.cols) for r in range(a.rows) if not a.blocked(c, r)]


WALK_DT = 0.1      # Round RPG17: the seconds that pass at each tile, so the people walking between their places get there


def walk_everywhere(ex):
    """Stand on every free tile of the map in turn (row by row), with a refresh at each, as walking there would. Round RPG17
    (Karl's S1 "(b) people walk off and on, animated"): WALK_DT seconds pass at each tile, for the people walking."""
    a = ex.area
    for c, r in free_tiles(a):
        ex.walker.x, ex.walker.y = (c + 0.5) * a.tw, (r + 0.5) * a.th
        ex.step_movers(WALK_DT)
        ex.refresh()
        yield (c, r)


class GuardTests(unittest.TestCase):
    """These pass: on the same map and windows the harness sees a person come and go, and a bad events table is survived."""

    def test_the_gate_guard_goes_off_duty_once_his_post_is_out_of_sight(self):
        for w, h in WINDOWS:
            with self.subTest(window=(w, h)):
                ex = at_window(place(explorer_at(TOWN, at(1, 19, 59)), (37, 17)), w, h)
                self.assertTrue(ex.is_out(GATE_GUARD))
                ex.save.world["clock"] = at(1, 21)
                ex.refresh()
                # Round RPG17 (expected change, Karl's S1 "(b)"): his post is in view, so he walks off (before, he stayed)
                self.assertFalse(ex.is_out(GATE_GUARD), "his post is in view: he walks off")
                self.assertIn(("group", "gate_guard"), ex.movers, "he leaves walking, not at once")
                for _t in walk_everywhere(ex):
                    pass
                self.assertFalse(ex.is_out(GATE_GUARD), "he went")
                self.assertNotIn(("group", "gate_guard"), ex.movers, "he walked off the screen and is gone")

    def test_an_events_table_that_is_a_list_does_not_crash_the_walk(self):
        """events.events() raises EventError (a ValueError) for this, which RPG16b's guard catches."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "events.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"version": 1, "events": []}')
            events.forget()
            self.addCleanup(events.forget)
            with mock.patch.object(events.events, "__defaults__", (bad,)):
                ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
                ex.save.world["events"] = {"gap": 0.5}
                for d in ((1, 0), (-1, 0)):
                    press(ex, d, 30)


class StuckInViewTests(unittest.TestCase):
    """S1. Fact (probe over every free tile): at 1920x1080, 1360x840, 1280x720, 2560x1440, 3840x2160 and 1024x768 the view box
    of every walker tile in Lantern Hollow (40 x 30 tiles) holds (17, 16), (20, 15) and (23, 12); at most sizes (22, 18) too. The
    camera itself shows them from everywhere (it stops at the map's edge), so R1's box is right - but then R3's rule (a person
    moves only when every place that changes is out of view) can never be met for Hesk and Pell on this map."""

    def test_s1_elder_hesk_goes_home_at_night_somewhere_on_the_town_map(self):
        for w, h in WINDOWS:
            with self.subTest(window=(w, h)):
                ex = at_window(place(explorer_at(TOWN, at(1, 19, 59)), (37, 17)), w, h)
                self.assertTrue(ex.is_out(HESK_SQUARE))
                ex.save.world["clock"] = at(1, 21)
                home = [t for t in walk_everywhere(ex) if ex.is_out(HESK_HOME)]
                self.assertTrue(home, "walked every tile of the town at 21:00: Elder Hesk never went home "
                                      f"(still in the square: {ex.is_out(HESK_SQUARE)})")

    def test_s1_wenna_comes_to_the_well_at_night_somewhere_on_the_town_map(self):
        for w, h in WINDOWS:
            with self.subTest(window=(w, h)):
                ex = at_window(place(explorer_at(TOWN, at(1, 19, 59)), (37, 17)), w, h)
                self.assertFalse(ex.is_out(WENNA))
                ex.save.world["clock"] = at(1, 21)
                came = [t for t in walk_everywhere(ex) if ex.is_out(WENNA)]
                self.assertTrue(came, "walked every tile of the town at 21:00: Wenna never came to the well")

    def test_s1_pell_goes_to_the_lamps_at_dusk_somewhere_on_the_town_map(self):
        ex = at_window(place(explorer_at(TOWN, at(1, 17, 59)), (37, 17)))
        self.assertTrue(ex.is_out(PELL_DAY))
        ex.save.world["clock"] = at(1, 18, 30)
        lamps = [t for t in walk_everywhere(ex) if ex.is_out(PELL_LAMPS)]
        self.assertTrue(lamps, "walked every tile of the town at 18:30: Pell never went to the lamps")

    def test_s1_the_night_hint_sends_you_where_hesk_is(self):
        """R8's night hint follows the clock; where Hesk stands follows Presence. After walking the whole town at 21:00 (no shop,
        no inn) the hint says "at home" while Hesk stands in the square."""
        ex = at_window(place(explorer_at(TOWN, at(1, 19, 59), flags=("met_elder", "met_lamplighter")), (37, 17)))
        ex.save.world["clock"] = at(1, 21)
        for _t in walk_everywhere(ex):
            pass
        hid, pages = hints.hint_for(ex.save.world, TOWN, clock.now(ex.save.world))
        says_home = "home" in " ".join(pages)
        self.assertTrue(says_home, f"the setup expects a night hint about Hesk's home, got {hid}: {pages}")
        self.assertFalse(says_home and ex.is_out(HESK_SQUARE) and not ex.is_out(HESK_HOME),
                         f"hint {hid} says Hesk is at home; he is in the square")


class LoadBesideTheirTileTests(unittest.TestCase):
    def test_s2_a_save_whose_feet_overlap_wennas_tile_can_still_walk(self):
        """S2. By day walk left along row 15 from (22, 15) and stop with the walker's centre in (21, 15) and its feet box (10 px
        wide) more than 1.2 px over the east edge of the well tile (20, 15). At night save and load: R4's check compares only the
        centre tile, so Wenna appears on (20, 15), the blockers are her tile minus the centre tile, and the box already overlaps a
        blocker - every sub-step of 1.2 px (60 frames a second) is refused (an overlap of 1 px gets out on the first step). The
        walker can't move, so the clock stops, and she can't leave (her tile is always in view: S1)."""
        ex = place(explorer_at(TOWN, at(1, 12)), (22, 15))
        a = ex.area
        for _ in range(40):                                    # a frame at a time, until the feet reach over the well tile by
            press(ex, (-1, 0), 1)                              # more than one 1.2 px sub-step (the save rounds to whole pixels)
            if ex.walker.box()[0] < 21 * a.tw - 2.5:
                break
        press(ex, (0, 0), 1)                                   # let go
        self.assertEqual(walker_tile(ex), (21, 15), "the setup: the centre is still one tile east of the well")
        ex.save.world["clock"] = at(1, 22)
        ex.store()
        loaded = explore.Explorer(ex.save)                     # what opening the slot does
        self.assertEqual(walker_tile(loaded), (21, 15))
        self.assertLess(loaded.walker.box()[0], 21 * a.tw - 1.2, "the setup: the saved feet box reaches over the well tile")
        start, t0 = (loaded.walker.x, loaded.walker.y), clock.now(loaded.save.world)
        moved = False
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            press(loaded, d, 40)
            moved = moved or (loaded.walker.x, loaded.walker.y) != start
        self.assertTrue(moved, f"walled in beside Wenna (blockers {sorted(loaded.walker.blockers)})")
        self.assertGreater(clock.now(loaded.save.world), t0)


class EventEntryTests(unittest.TestCase):
    def test_s3_an_event_that_is_not_a_table_does_not_crash_the_walk(self):
        """S3. events.eligible calls e.get(...) on every entry; an entry that is a list raises AttributeError, which
        Explorer._event (RPG16b) doesn't catch. events.check would name it ("not a table"), but the game doesn't run the check."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "events.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"version": 1, "events": {"odd": ["map", "ashfen_outskirts"]}}')
            events.forget()
            self.addCleanup(events.forget)
            with mock.patch.object(events.events, "__defaults__", (bad,)):
                ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
                ex.save.world["events"] = {"gap": 0.5}
                try:
                    for d in ((1, 0), (-1, 0)):
                        press(ex, d, 30)
                except Exception as e:                         # noqa: BLE001 - any exception out of the walk is the bug
                    self.fail(f"walking crashed on an odd event entry: {type(e).__name__}: {e}")


class InfiniteMinutesTests(unittest.TestCase):
    def test_s4_passing_infinite_minutes_never_sends_the_clock_back(self):
        """S4. pass_minutes' float('inf') > 0 passes its check and world["clock"] becomes inf; R9 makes clock.now read inf as
        START (17:00, day 1). Before R9 the clock read inf (and crashed elsewhere); now it silently goes back."""
        w = {"clock": at(5, 12)}
        clock.pass_minutes(w, float("inf"))
        self.assertGreaterEqual(clock.now(w), at(5, 12), f"the clock went back to {clock.now(w)} (day 5 noon is {at(5, 12)})")


class ForcedNowhereTests(unittest.TestCase):
    def test_s5_a_load_on_hesks_house_tile_at_night_leaves_him_somewhere(self):
        """S5 (a judgement call: RPG16b's own test expects "nowhere until the walker goes"). By day stand on (32, 21) (free: he is
        not home), save at night, load: the forced update drops his square place and refuses his house place (under the walker),
        so Elder Hesk - on daynight.ALWAYS_SOMEWHERE - is nowhere until (32, 21) is out of view."""
        ex = place(explorer_at(TOWN, at(1, 12)), (32, 21))
        ex.save.world["clock"] = at(1, 22)
        ex.store()
        loaded = explore.Explorer(ex.save)
        self.assertEqual(walker_tile(loaded), (32, 21))
        self.assertFalse(loaded.is_out(HESK_HOME), "the setup: never on the walker (R4)")
        self.assertTrue(loaded.is_out(HESK_SQUARE) or loaded.is_out(HESK_HOME), "Elder Hesk is nowhere after the load")


if __name__ == "__main__":
    unittest.main()
