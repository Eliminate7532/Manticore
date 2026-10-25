# SPDX-License-Identifier: GPL-3.0-or-later
"""A cold review of Round RPG16 (day and night), 24 Oct 2026: one failing test per bug found, and guard tests that pass and prove
the harness can see the behaviour at all. Nothing here changes the program.

  R1  things come and go in plain view near a map's edge: the "out of view" box is centred on the walker, the camera is not
  R2  a wandering foe comes and goes by its HOME tile, not where it stands: it vanishes (and reappears) in view
  R3  a person with two places (Elder Hesk, Pell) is in both at once, or in neither, while the hour changes
  R4  loading a save that stands on a night person's tile walls the walker in for good (and stops the clock)
  R5  a night foe's home= line (the town's grave robber, the outskirts' rats) never reaches the encounter screen
  R6  a missing or broken rpg/data/events.json crashes the walk on any map with event_rate
  R7  a once-ever event (the well's offering) is marked done before its effect is given: quitting with the box open loses it
  R8  the hints still send you to "the square" for Elder Hesk at night, when he is at home
  R9  a save whose clock is Infinity (Python's json reads it) can't open the outskirts: the rare's roll is int(nan)

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest
tests.test_review_rpg16
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from rpg import clock, events, explore, hints
from rpg import adventure as adv
from tests import test_rpg8 as r8
from tests.test_rpg16 import at, explorer_at, place, press, walker_tile, _window_base

TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
GRAVES = "ashfen_outskirts#night_graves"


def screen_view_half(ex, cam):
    """The view half rpg_screens.draw_map gives the Explorer (rpg_screens.py l.1603)."""
    return (int(cam.view_w / cam.zoom / ex.area.tw / 2) + 1, int(cam.view_h / cam.zoom / ex.area.th / 2) + 1)


def on_screen_tiles(ex, cam):
    """Every tile the camera shows, as rpg_screens draws it (Camera.follow, then Camera.visible)."""
    a = ex.area
    cam.follow(ex.walker.x, ex.walker.y, a.width, a.height)
    c0, r0, c1, r1 = cam.visible(a.tw, a.th, a.width // a.tw, a.height // a.th)
    return {(c, r) for c in range(c0, c1) for r in range(r0, r1)}


def tile_in_box(tile, box):
    return box[0] <= tile[0] <= box[2] and box[1] <= tile[1] <= box[3]


def item_tile(ex, key):
    return next(it.tile for it in ex.presence.items if it.key == key)


class GuardTests(unittest.TestCase):
    """These pass: the harness sees things come and go, people as walls, and the clock."""

    def test_a_night_foe_in_view_does_not_appear_and_one_out_of_view_does(self):
        ex = place(explorer_at(OUT, at(1, 19, 58)), (21, 27))          # the graves (21, 30) three rows below: in view
        self.assertFalse(ex.is_out(GRAVES))
        ex.save.world["clock"] = at(1, 20, 1)
        ex.refresh()
        self.assertFalse(ex.is_out(GRAVES))
        ex.walker.x += 40 * ex.area.tw                                  # far off: now it comes
        ex.refresh()
        self.assertTrue(ex.is_out(GRAVES))

    def test_the_walker_walks_off_the_well_tile_by_day(self):
        ex = place(explorer_at(TOWN, at(1, 12)), (20, 15))
        press(ex, (-1, 0), 30)
        self.assertNotEqual(walker_tile(ex), (20, 15))
        self.assertGreater(clock.now(ex.save.world), at(1, 12))

    def test_a_rare_foe_shows_its_own_home_line(self):
        import pygame
        import rpg_encounter as renc
        from tests.test_rpg16 import RareTrigger
        from rpg import rare
        pygame.init()
        pygame.display.set_mode((64, 64))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(r8.sv, "saves_dir", lambda: os.path.join(tmp, "rpg_saves")):
                g = r8.new_game(None)
                scr = renc.EncounterScreen(g, RareTrigger("mother_of_rats"), 0.0, animations=False)
        self.assertEqual(scr.text["home"], rare.spawns()["mother_of_rats"]["home"])


class EdgeOfMapTests(unittest.TestCase):
    def test_r1_nobody_comes_or_goes_on_a_tile_the_camera_shows(self):
        """R1: at 1920x1080 near Lantern Hollow's east gate the camera stops at the map's edge and shows columns 10-39, but the
        Explorer's view box is the walker's tile +- 17 columns (20-54). At 20:00 Elder Hesk leaves the square (17, 16) - a tile
        on the screen - at once."""
        ex = place(explorer_at(TOWN, at(1, 19, 59)), (37, 17))
        cam = explore.Camera(1920, 1080, explore.Camera.zoom_for(1080, ex.area.th))
        ex.view_half = screen_view_half(ex, cam)
        shown = on_screen_tiles(ex, cam)
        ex.save.world["clock"] = at(1, 20, 1)
        changed = ex.refresh()
        in_sight = [(k, item_tile(ex, k)) for k in changed if item_tile(ex, k) in shown]
        # Round RPG17 (expected change, Karl 24 Oct S1 "(b) people walk off and on, animated"): a person may now leave a tile the
        # camera shows - walking (a Mover from that tile), never at once
        walked = {m.route[0] for m in ex.movers.values()}
        self.assertEqual([(k, t) for k, t in in_sight if t not in walked], [],
                         "these came or went on a tile the camera shows without walking")


class WanderTests(unittest.TestCase):
    """R2: a night foe walks up to LEASH_TILES (4) from home, but Presence judges it by its home tile."""

    def foe_near(self, minutes, walker=(18, 20)):
        ex = place(explorer_at(OUT, minutes), walker)
        f = ex.foes[GRAVES]
        a = ex.area
        hx, hy = f.home
        for up in (4, 3, 2):                                            # as far toward the walker as its leash allows
            if f._free(hx, hy - up * a.th):
                f.x, f.y = hx, hy - up * a.th
                break
        else:
            self.fail("no free spot within the leash")
        box = ex.view_box()
        self.assertFalse(tile_in_box(a.tile_at(*f.home), box), "set-up: its home should be out of view")
        self.assertTrue(tile_in_box(a.tile_at(f.x, f.y), box), "set-up: where it stands should be in view")
        return ex, f

    def test_r2_a_foe_standing_in_view_does_not_vanish_at_dawn(self):
        ex, f = self.foe_near(at(1, 4, 58))
        self.assertIn(f, ex.encounters())
        ex.save.world["clock"] = at(1, 5, 1)
        ex.refresh()
        self.assertIn(f, ex.encounters(), f"vanished while standing at {ex.area.tile_at(f.x, f.y)}, in view")

    def test_r2_a_foe_does_not_reappear_in_view_where_it_last_stood(self):
        ex, f = self.foe_near(at(1, 12))                                # by day it is away; it last stood 4 tiles up
        self.assertNotIn(f, ex.encounters())
        ex.save.world["clock"] = at(1, 20, 1)
        ex.refresh()
        self.assertNotIn(f, ex.encounters(), f"appeared at {ex.area.tile_at(f.x, f.y)}, in view")


class TwoPlacesTests(unittest.TestCase):
    """R3: each of a person's markers comes and goes on its own, so while the hour changes a person can be in two places."""

    def elders(self, ex):
        return sorted(p["tile"] for p in ex.present_people() if p["id"] == "elder")

    def test_r3_elder_hesk_is_never_in_two_places_at_once(self):
        ex = place(explorer_at(TOWN, at(1, 19, 59)), (10, 17))         # the square (17, 16) in view, his house (32, 21) not
        ex.save.world["clock"] = at(1, 20, 1)
        ex.refresh()
        ex.walker.x, ex.walker.y = 24.5 * ex.area.tw, 18.5 * ex.area.th     # walk east: both places in view now
        ex.refresh()
        box = ex.view_box()
        self.assertTrue(all(tile_in_box(t, box) for t in [(17, 16), (32, 21)]))
        self.assertLessEqual(len(self.elders(ex)), 1, f"Elder Hesk stands at {self.elders(ex)} at once, both in view")

    def test_r3_elder_hesk_is_somewhere_at_night(self):
        """daynight.gaps says he is always somewhere; at the gate end of the town at 20:00 he is nowhere."""
        ex = place(explorer_at(TOWN, at(1, 19, 59)), (37, 21))         # his house in view, the square not
        ex.save.world["clock"] = at(1, 20, 1)
        ex.refresh()
        walking = [m for m in ex.movers.values() if m.person["id"] == "elder"]   # Round RPG17: or walking home, in sight
        self.assertEqual(len(self.elders(ex)) + len(walking), 1, "Elder Hesk is nowhere")


class LoadOnTheirTileTests(unittest.TestCase):
    def test_r4_a_save_standing_where_wenna_sits_can_still_walk(self):
        """R4: stand on the well tile (20, 15) as night falls (she can't come in view, so she doesn't), save, load: the load sets
        everything at once, Wenna appears on the walker, her tile walls it in, and nothing moves - not even the clock."""
        ex = place(explorer_at(TOWN, at(1, 19, 59)), (20, 15))
        ex.save.world["clock"] = at(1, 20, 1)
        ex.refresh()
        ex.store()
        loaded = explore.Explorer(ex.save)                              # what opening the slot does (rpg_screens.open_slot)
        self.assertEqual(walker_tile(loaded), (20, 15))
        t0 = clock.now(loaded.save.world)
        been = set()
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):        # RPG16b: right then left can end on the start tile again, so
            press(loaded, d, 40)                             # look at every tile it reached, not only the last
            been.add(walker_tile(loaded))
        self.assertNotEqual(been, {(20, 15)}, "walled in by Wenna on the walker's own tile")
        self.assertGreater(clock.now(loaded.save.world), t0)


class HomeLineTests(r8.Base):
    def test_r5_the_night_yards_robber_says_where_it_lives(self):
        """R5: tools/rpg_make_maps.py writes home= (SPACED) and Trigger.home keeps it, but EncounterScreen only takes a rare's
        home (rpg_encounter.py l.171-173), so the town's night robber says the chapel's graveyard."""
        import pygame
        import rpg_encounter as renc
        pygame.init()
        pygame.display.set_mode((64, 64))
        g = r8.new_game(None)
        for name, spot in ((TOWN, "lantern_hollow#night_yard"), (OUT, "ashfen_outskirts#night_rocks")):
            t = next(t for t in explore.load_area(name).triggers if t.spot == spot)
            self.assertTrue(t.home)
            with self.subTest(spot=spot):
                scr = renc.EncounterScreen(g, t, 0.0, animations=False)
                self.assertEqual(scr.text["home"], t.home)


class MissingDataTests(unittest.TestCase):
    def test_r6_a_broken_events_file_does_not_crash_the_walk(self):
        """R6: rpg_screens.start_event catches OSError/ValueError and explore.Area catches them for rare_spawns.json, but
        Explorer._event calls rpg.events.step with no guard: the first check on the outskirts raises."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "events.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write('{"version": 1, "events": {')                     # a half-saved edit
            events.forget()
            self.addCleanup(events.forget)
            with mock.patch.object(events.events, "__defaults__", (bad,)):
                ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
                ex.save.world["events"] = {"gap": 0.5}                    # the next check half a tile on
                try:
                    for d in ((1, 0), (-1, 0)):
                        press(ex, d, 30)
                except (OSError, ValueError) as e:
                    self.fail(f"walking crashed on a broken events.json: {type(e).__name__}: {e}")


class OnceEventTests(_window_base()):
    def test_r7_the_wells_offering_is_not_used_up_before_it_is_answered(self):
        """R7: start_event writes the event into world["events"]["done"] and saves; its effect comes only when the box is
        answered. A game closed with the box open is left with the once-ever event spent and neither flag set."""
        g, _l = self.setup_game()
        f = self.on_map(g, TOWN, "start")
        f.game.save.world["clock"] = at(1, 22)
        f.explorer.refresh(force=True)
        f.start_event(g, "well_offering")
        disk = r8.on_disk(self.folder)["world"]
        done = "well_offering" in ((disk.get("events") or {}).get("done") or [])
        given = {"took_the_offering", "left_the_offering"} & set(disk.get("flags") or [])
        self.assertFalse(done and not given, "the save on disk has the once-ever offering spent with nothing given")


class HintTests(unittest.TestCase):
    def test_r8_the_hint_does_not_send_you_to_the_square_when_hesk_is_at_home(self):
        world = {"flags": ["met_elder", "met_lamplighter"], "clock": at(1, 21)}
        hid, pages = hints.hint_for(world, TOWN, clock.now(world))
        ex = explorer_at(TOWN, at(1, 21), ["met_elder", "met_lamplighter"])
        where = sorted(p["tile"] for p in ex.present_people() if p["id"] == "elder")
        self.assertIn(hid, ("hesk_offer", "hesk_offer_night"))          # RPG16b: the fix is a night entry of its own
        if any("square" in p for p in pages):
            self.assertIn((17, 16), where, f"the hint says {pages!r}; at 21:00 Hesk stands at {where} (his house)")


class BrokenClockTests(unittest.TestCase):
    def test_r9_a_clock_of_infinity_still_opens_the_outskirts(self):
        """R9: save.py doesn't check world["clock"]; json reads "Infinity"; clock.now keeps it (v >= 0); phase_key(inf) is nan
        and rare.roll's int(nan) raises - in Explorer.__init__ (place -> _use -> refresh(force=True)) - once a rare's `when`
        holds (phase(inf) is "night"; the Mother of Rats needs warrens_entered)."""
        w = json.loads('{"clock": Infinity}')
        self.assertEqual(clock.now(w), float(clock.START))              # RPG16b: a clock that isn't finite reads as a new game's
        explorer_at(OUT, float("inf"))                                   # guard: with no rare's `when` holding, it opens
        try:
            explorer_at(OUT, float("inf"), ["warrens_entered"])
        except (ValueError, OverflowError) as e:
            self.fail(f"the outskirts can't be opened: {type(e).__name__}: {e}")


if __name__ == "__main__":
    unittest.main()
