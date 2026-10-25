# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG16b: the cold review of RPG16 (claude/REVIEW_RPG16_2026-10-24.md) fixed. The review's own failing tests are tests/test_review_rpg16.py; these add what its tests don't reach:

  - daynight.Presence keeps a person's places together at the level of the module (R3), and never puts anyone on the walker, even
    at once (R4)
  - the view box on a map smaller than the view is the whole map (R1, the ultrawide case the review named)
  - a foe that goes is sent home, and comes back there (R2)
  - a beaten rare past its cooldown says nothing (review N2: it said its "seen" line and wasn't drawn)
  - a broken events.json is noted in the crash log once, however often it is read (R6)
"""
import unittest
from unittest import mock

from rpg import daynight, explore
from tests import test_rpg8 as r8
from tests.test_rpg16 import _window_base
from rpg import rare as rpg_rare
from tests.test_rpg16 import at, explorer_at, place, press, walker_tile

TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
GRAVES = "ashfen_outskirts#night_graves"
RATS_RARE = "ashfen_outskirts#rare:mother_of_rats"


def hesk():
    """A Presence with Elder Hesk's two places, set at noon (he is in the square)."""
    items = [daynight.Item("npc:elder@17,16", ["!phase:night"], (17, 16), group="elder"),
             daynight.Item("npc:elder@32,21", ["phase:night"], (32, 21), group="elder")]
    p = daynight.Presence(items)
    p.update({}, at(1, 12), force=True)
    return p


VIEWS = {(True, False): (15, 14, 19, 18), (False, True): (30, 19, 34, 23), (True, True): (15, 14, 34, 23),
         (False, False): (0, 0, 2, 2)}


class PresenceGroupTests(unittest.TestCase):
    def test_a_person_moves_only_when_both_places_are_out_of_view(self):
        for sq, house, moves in ((True, False, False), (False, True, False), (True, True, False), (False, False, True)):
            with self.subTest(square_in_view=sq, house_in_view=house):
                p, view = hesk(), VIEWS[(sq, house)]
                self.assertEqual(p.present, {"npc:elder@17,16"})
                p.update({}, at(1, 21), view=view, walker_tile=(1, 1))
                self.assertEqual(len(p.present), 1, "always exactly one Hesk")
                self.assertEqual(p.present, {"npc:elder@32,21"} if moves else {"npc:elder@17,16"})

    def test_never_on_the_walker_even_at_once(self):
        p = daynight.Presence([daynight.Item("npc:wenna@20,15", ["phase:night"], (20, 15), group="wenna")])
        p.update({}, at(1, 21), view=None, walker_tile=(20, 15), force=True)
        self.assertFalse(p.is_present("npc:wenna@20,15"))
        p.update({}, at(1, 21), view=(0, 0, 1, 1), walker_tile=(5, 5))            # the walker walked off, out of view of it
        self.assertTrue(p.is_present("npc:wenna@20,15"))

    def test_an_item_is_judged_where_it_stands(self):
        p = daynight.Presence([daynight.Item("foe", ["phase:night"], (10, 10))])
        p.update({}, at(1, 12), force=True)
        p.update({}, at(1, 21), view=(0, 0, 5, 5), walker_tile=(0, 0), tiles={"foe": (3, 3)})   # home out of view, body in view
        self.assertFalse(p.is_present("foe"))
        p.update({}, at(1, 21), view=(0, 0, 5, 5), walker_tile=(0, 0), tiles={"foe": (10, 10)})
        self.assertTrue(p.is_present("foe"))


class ViewBoxTests(unittest.TestCase):
    def test_every_tile_the_camera_shows_is_in_the_box_near_every_edge(self):
        """R1 at the level of the box: the camera stops at the map's edge, so the box must too (the review's own R1 test now also
        passes because of R3's groups, so this one checks the box itself)."""
        from tests.test_review_rpg16 import on_screen_tiles, screen_view_half
        for size in ((1920, 1080), (1360, 840)):
            for name, spots in ((TOWN, ((37, 17), (2, 15), (20, 2), (20, 27), (37, 27))), (OUT, ((57, 20), (2, 20), (30, 2), (30, 38)))):
                for spot in spots:
                    ex = place(explorer_at(name, at(1, 12)), spot)
                    cam = explore.Camera(*size, explore.Camera.zoom_for(size[1], ex.area.th))
                    ex.view_half = screen_view_half(ex, cam)
                    c0, r0, c1, r1 = ex.view_box()
                    off = sorted(t for t in on_screen_tiles(ex, cam) if not (c0 <= t[0] <= c1 and r0 <= t[1] <= r1))
                    with self.subTest(map=name, spot=spot, size=size):
                        self.assertEqual(off, [], "tiles on screen but outside the out-of-view box")

    def test_a_map_smaller_than_the_view_is_in_view_whole(self):
        ex = place(explorer_at(TOWN, at(1, 12)), (20, 15))
        a = ex.area
        cols, rows = a.width // a.tw, a.height // a.th
        ex.view_half = (cols, rows)                                     # an ultrawide: wider and taller than the map
        c0, r0, c1, r1 = ex.view_box()
        self.assertLessEqual(c0, 0)
        self.assertLessEqual(r0, 0)
        self.assertGreaterEqual(c1, cols - 1)
        self.assertGreaterEqual(r1, rows - 1)

    def test_in_the_middle_the_box_is_centred_on_the_walker(self):
        ex = place(explorer_at(TOWN, at(1, 12)), (20, 15))
        ex.view_half = (5, 4)
        self.assertEqual(ex.view_box(), (20 - 5 - explore.VIEW_MARGIN, 15 - 4 - explore.VIEW_MARGIN,
                                         20 + 5 + explore.VIEW_MARGIN, 15 + 4 + explore.VIEW_MARGIN))


class FoeHomeTests(unittest.TestCase):
    def test_a_foe_that_goes_goes_home(self):
        ex = place(explorer_at(OUT, at(1, 4, 58)), (5, 5))             # far from the graves (21, 30)
        f = ex.foes[GRAVES]
        self.assertTrue(ex.is_out(GRAVES))
        f.x, f.y = f.home[0], f.home[1] - 2 * ex.area.th                # it wandered two tiles up, still out of view
        ex.save.world["clock"] = at(1, 5, 1)
        ex.refresh()
        self.assertFalse(ex.is_out(GRAVES))
        self.assertEqual((f.x, f.y), f.home)


class RareSeenTests(unittest.TestCase):
    def rats(self, beaten):
        with mock.patch.object(rpg_rare, "roll", lambda save, rid, minutes: 0.0):
            ex = place(explorer_at(OUT, at(10, 19, 59), ["warrens_entered"]), (5, 5))
            if beaten:
                rpg_rare.on_beaten(ex.save.world, "mother_of_rats", at(1, 21))       # long past its cooldown
                ex.save.world["beaten"] = [RATS_RARE]                                 # but no town visit since
            ex.take_notes()
            ex.save.world["clock"] = at(10, 20, 1)
            ex.refresh()
            return ex, ex.take_notes()

    def test_a_rare_that_comes_out_says_so(self):
        ex, notes = self.rats(beaten=False)
        self.assertTrue(ex.is_out(RATS_RARE))
        self.assertIn(rpg_rare.spawns()["mother_of_rats"]["seen"], notes)

    def test_a_beaten_rare_still_on_the_beaten_list_says_nothing(self):
        ex, notes = self.rats(beaten=True)
        self.assertNotIn(rpg_rare.spawns()["mother_of_rats"]["seen"], notes)
        self.assertNotIn(RATS_RARE, {f.spot for f in ex.encounters()})


class BrokenEventsTests(unittest.TestCase):
    def test_a_broken_events_file_is_noted_once(self):
        explore._NOTED.discard("events")
        self.addCleanup(explore._NOTED.discard, "events")
        ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
        with mock.patch.object(explore.rpg_events, "step", side_effect=ValueError("bad json")), \
                mock.patch("crashlog.note") as note:
            for _ in range(3):
                self.assertIsNone(ex._event(5.0))
        self.assertEqual(note.call_count, 1)
        self.assertIn("events.json", note.call_args[0][0])


class WalkerTileTests(unittest.TestCase):
    def test_the_walkers_own_tile_is_never_a_wall(self):
        """Even if someone were set down on the walker (an old save, a hand edit), the walker can step off."""
        ex = place(explorer_at(TOWN, at(1, 21)), (20, 15))
        ex.presence.present.add(next(p["key"] for p in ex.area.scheduled() if tuple(p["tile"]) == (20, 15)))
        ex.refresh()
        self.assertNotIn((20, 15), ex.walker.blockers)
        press(ex, (1, 0), 30)
        self.assertNotEqual(walker_tile(ex), (20, 15))


class EventMetTests(_window_base()):
    WELL = "well_offering"

    def open_well(self):
        g, _l = self.setup_game()
        f = self.on_map(g, TOWN, "start")
        f.game.save.world["clock"] = at(1, 22)
        f.explorer.refresh(force=True)
        f.start_event(g, self.WELL)
        return g, f

    def done_on_disk(self):
        disk = r8.on_disk(self.folder)["world"]
        return self.WELL in ((disk.get("events") or {}).get("done") or []), set(disk.get("flags") or [])

    def test_answered_it_is_met_and_given(self):
        g, f = self.open_well()
        f.talk_box.answer = True
        f.event_do(g, "answer")
        done, flags = self.done_on_disk()
        self.assertTrue(done)
        self.assertTrue({"took_the_offering", "left_the_offering"} & flags)

    def test_closed_unanswered_it_is_met_and_written(self):
        g, f = self.open_well()
        f.event_do(g, "close")
        done, flags = self.done_on_disk()
        self.assertTrue(done, "a question closed unanswered counts as met, on disk")
        self.assertFalse({"took_the_offering", "left_the_offering"} & flags)


if __name__ == "__main__":
    unittest.main()
