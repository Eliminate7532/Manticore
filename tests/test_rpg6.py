# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG6 (Karl, 12 Oct 2026: the RPG's look and feel inspired by the original Zelda): one 16 x 11-tile room on screen at a
time with side panels, the view sliding to the next room, walking in four directions on a half-tile grid with help round
corners, foes that wander about their room, the rooms you have seen in the save, the Diablo / Vermis dark palette and a light
round the walker in the dungeon. Map side only: the duels keep the normal table.

Small maps are written to a temp folder (tests.test_rpg5's helpers); the "Shipped" tests read rpg/maps/.
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from rpg import explore
from rpg import save as sv
from tests import rpg_route as route
from tests import test_rpg4 as r4
from tests.test_rpg5 import BASE, Temp, area_of, layer_xml, obj, room, stand_in, write_map

TILE = 16


def mid(c, r):
    return c * TILE + TILE / 2, r * TILE + TILE / 2


def frames(n, fn):
    out = []
    for _ in range(n):
        out.append(fn())
    return out


def slide_out(ex, most=600):
    """Step (pressing nothing) until the view has slid to the room; a slide that never ends is a failure, not a hang."""
    for _ in range(most):
        if not ex.sliding():
            return
        ex.step(0, 0, 1 / 60)
    raise AssertionError("the slide never ended")


def walk_until_slide(ex, dx, dy, most=120):
    for _ in range(most):
        if ex.sliding():
            return
        ex.step(dx, dy, 1 / 60)
    raise AssertionError("no slide to the next room")


# ---- four directions ----------------------------------------------------------------------------------------------------------

class FourWayTests(unittest.TestCase):
    def test_one_key_and_none(self):
        self.assertEqual(explore.four_way(1, 0), (1, 0, "x"))
        self.assertEqual(explore.four_way(0, -1), (0, -1, "y"))
        self.assertEqual(explore.four_way(0, 0, (1, 1), "y"), (0, 0, None))

    def test_two_keys_the_later_one_wins(self):
        self.assertEqual(explore.four_way(1, 1, held=(1, 0), axis="x"), (0, 1, "y"))      # down pressed while walking right
        self.assertEqual(explore.four_way(-1, -1, held=(0, -1), axis="y"), (-1, 0, "x"))  # left pressed while walking up

    def test_two_keys_together_or_still_held_keep_the_axis(self):
        self.assertEqual(explore.four_way(1, 1, held=(0, 0), axis=None), (1, 0, "x"))     # together, from standing: across
        self.assertEqual(explore.four_way(1, 1, held=(0, 0), axis="y"), (0, 1, "y"))
        self.assertEqual(explore.four_way(1, 1, held=(1, 1), axis="y"), (0, 1, "y"))      # both held: no change
        self.assertEqual(explore.four_way(1, 1, held=(1, 1), axis="x"), (1, 0, "x"))

    def test_the_explorer_never_walks_diagonally(self):
        explore.forget_areas()
        s = stand_in()
        ex = explore.Explorer(s, start_town="town_1")
        ex.leave_town("town_1")
        x0, y0 = ex.walker.x, ex.walker.y
        for _ in range(20):
            ex.step(1, 1, 1 / 60)
        self.assertGreater(ex.walker.x, x0 + 10)
        self.assertEqual(ex.walker.y, y0)


# ---- the half-tile grid and the help round corners ------------------------------------------------------------------------------

def gap_map(folder, gap=4, solid=False):
    """9 x 7 tiles: a border, and a wall across row 3 with a one-tile gap at column `gap` (none when solid)."""
    walls = {(c, 3) for c in range(9) if solid or c != gap}
    return area_of(folder, room(9, 7, walls), 9, 7)


class CornerTests(Temp):
    def walk_up(self, a, x, align=True, seconds=1.5):
        w = explore.Walker(a, x, mid(4, 5)[1])
        for _ in range(int(seconds * 60)):
            w.move(0, -1, 1 / 60, align=align)
        return w

    def test_a_one_tile_gap_lets_you_in_from_anywhere_beside_it(self):
        # Round RPG7: the help is off (Karl, 13 Oct - stop dead at walls); the code is kept, and this is what it does when on
        self.enterContext(mock.patch.object(explore, "CORNER_HELP", True))
        a = gap_map(self.tmp)
        gx = mid(4, 3)[0]
        for off in (-7.5, -6, -4, -2, 0, 2, 4, 6, 7.5):
            with self.subTest(off=off):
                w = self.walk_up(a, gx + off)
                self.assertLess(w.y, 3 * TILE, "through the gap")
                self.assertAlmostEqual(w.x, gx, delta=1e-6)

    def test_without_the_help_the_walker_stays_stuck_against_the_wall(self):
        a = gap_map(self.tmp)
        gx = mid(4, 3)[0]
        w = self.walk_up(a, gx + 6, align=False)
        self.assertEqual(w.y, 4 * TILE + w.hh)              # flush under the wall
        self.assertEqual(w.x, gx + 6)

    def test_the_help_reaches_half_a_tile_and_no_further(self):
        self.enterContext(mock.patch.object(explore, "CORNER_HELP", True))          # Round RPG7: off by default
        a = gap_map(self.tmp)
        gx = mid(4, 3)[0]
        w = self.walk_up(a, gx + TILE)                      # a whole tile beside the gap
        self.assertEqual(w.y, 4 * TILE + w.hh)
        self.assertFalse(a.box_blocked(w.box()))

    def test_a_solid_wall_only_drifts_onto_the_grid(self):
        a = gap_map(self.tmp, solid=True)
        x = mid(4, 3)[0] + 3
        w = self.walk_up(a, x)
        self.assertEqual(w.y, 4 * TILE + w.hh)
        self.assertEqual(w.x % (TILE * explore.ALIGN), 0)  # on the half-tile grid
        self.assertLessEqual(abs(w.x - x), TILE * explore.ALIGN / 2)

    def test_walking_along_one_axis_drifts_onto_the_half_tile_grid(self):
        a = area_of(self.tmp, room(12, 8), 12, 8)
        w = explore.Walker(a, mid(3, 3)[0], mid(3, 3)[1] + 3)
        for _ in range(30):
            w.move(1, 0, 1 / 60, align=True)
        self.assertEqual(w.y % (TILE * explore.ALIGN), 0)
        self.assertEqual(w.y, mid(3, 3)[1])               # +3 px: the nearer line is the tile's middle


# ---- rooms on a map --------------------------------------------------------------------------------------------------------------

class RoomTests(Temp):
    def test_rooms_of_a_map(self):
        a = area_of(self.tmp, room(32, 22), 32, 22)
        self.assertEqual((a.room_w, a.room_h, a.room_cols, a.room_rows), (256, 176, 2, 2))
        self.assertEqual(a.room_of(0, 0), (0, 0))
        self.assertEqual(a.room_of(255.9, 175.9), (0, 0))
        self.assertEqual(a.room_of(256, 176), (1, 1))
        self.assertEqual(a.room_of(-5, 9999), (0, 1))                     # off the map: the nearest room
        self.assertEqual(a.room_rect((1, 0)), (256, 0, 512, 176))
        self.assertEqual(a.room_key((1, 0)), "m#1,0")

    def test_a_map_bigger_than_a_room_must_be_whole_rooms(self):
        spawn = obj(1, "spawn", 40, 40, name="start")
        for (w, h), bad in (((20, 11), True), ((32, 12), True), ((32, 22), False), ((10, 8), False), ((16, 11), False)):
            with self.subTest(size=(w, h)):
                explore.forget_areas()
                a = area_of(self.tmp, room(w, h), w, h, objects=spawn, name=f"m{w}x{h}")
                said = [p for p in a.check() if "whole rooms" in p]
                self.assertEqual(bool(said), bad, a.check())
                if bad:
                    self.assertEqual(said, [f"the map is {w} x {h} tiles: make it whole rooms of 16 x 11"])

    def test_the_light_property(self):
        # Round RPG7: only a dungeon is dark - these maps say so with the map property dungeon (tests/test_rpg7.py has the rest)
        dungeon = '<property name="dungeon" type="bool" value="true"/>'
        for props, light in (("", None), (f'<properties>{dungeon}<property name="light" type="float" value="4.5"/></properties>', 4.5),
                             (f'<properties>{dungeon}<property name="light" type="float" value="0"/></properties>', None),
                             (f'<properties>{dungeon}<property name="light" value="bright"/></properties>', None)):
            with self.subTest(props=props):
                explore.forget_areas()
                write_map(self.tmp, "lit", 8, 6, [layer_xml("g", 8, 6, room())], map_props=props)
                self.assertEqual(explore.load_area("lit", self.tmp).light, light)


# ---- the room's camera ----------------------------------------------------------------------------------------------------------

class RoomCameraTests(unittest.TestCase):
    def test_the_zoom_and_the_panels_at_every_size(self):
        for size, zoom in (((1920, 1080), 6), ((4096, 2160), 12), ((1366, 768), 4), ((1280, 800), 4), ((1024, 768), 3),
                           ((2560, 1440), 8), ((800, 600), 2)):
            with self.subTest(size=size):
                cam = explore.RoomCamera(*size)
                self.assertEqual(cam.zoom, zoom)
                x, y, w, h = cam.viewport()
                self.assertEqual((w, h), (256 * zoom, 176 * zoom))
                self.assertTrue(0 <= x and 0 <= y and x + w <= size[0] and y + h <= size[1])
                self.assertLessEqual(abs(x - (size[0] - x - w)), 1)                      # centred
                left, right = cam.panels()
                self.assertGreaterEqual(min(left[2], right[2]), explore.PANEL_MIN_W)
                self.assertEqual(left[2] + w + right[2], size[0])
                bigger = explore.RoomCamera(*size, zoom=zoom + 1)
                bx, _by, bw, bh = bigger.viewport()
                self.assertTrue(bh > size[1] or bw > size[0] or bx < explore.PANEL_MIN_W, "the next zoom up wouldn't fit")

    def test_a_tiny_window_still_gets_zoom_one(self):
        cam = explore.RoomCamera(300, 200)
        self.assertEqual(cam.zoom, 1)

    def test_to_screen_and_back_and_whole_pixels_while_sliding(self):
        cam = explore.RoomCamera(1920, 1080)
        cam.look_at((256, 0))
        self.assertEqual(cam.to_screen(256, 0), cam.viewport()[:2])
        self.assertEqual(cam.to_map(*cam.to_screen(300, 40)), (300, 40))
        cam.look_at((100.07, 0))
        self.assertEqual((cam.x * cam.zoom) % 1, 0)
        self.assertEqual(cam.visible(16, 16, 48, 22), (6, 0, 23, 11))


# ---- sliding to the next room ---------------------------------------------------------------------------------------------------

class SlideTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()
        self.s = stand_in()
        self.ex = explore.Explorer(self.s, start_town="town_1")
        self.s.world["beaten"] = [t.spot for t in self.ex.area.triggers if t.kind == "encounter"]

    def test_walking_off_a_room_slides_the_view_and_the_walker_waits(self):
        ex = self.ex
        self.assertEqual(ex.room, (0, 0))
        self.assertEqual(self.s.world["seen"], ["sample_region#0,0"])
        route.walk_to_tile(ex, (15, 5))                         # the gap in the trees on the room's right
        for _ in range(60):
            ex.step(1, 0, 1 / 60)
            if ex.sliding():
                break
        self.assertTrue(ex.sliding())
        self.assertEqual(ex.room, (1, 0))
        self.assertGreaterEqual(ex.walker.box()[0], 256)                         # wholly in the new room
        self.assertIn("sample_region#1,0", self.s.world["seen"])
        where = (ex.walker.x, ex.walker.y)
        origins = []
        while ex.sliding():
            origins.append(ex.view_origin())
            ex.step(1, 0, 1 / 60)
            self.assertEqual((ex.walker.x, ex.walker.y), where)                 # it waits
            self.assertLess(len(origins), 600, "the slide never ended")
        self.assertEqual(len(origins), round(explore.ROOM_SLIDE_SECONDS * 60))
        xs = [o[0] for o in origins]
        self.assertEqual(xs, sorted(xs))
        self.assertTrue(0 <= xs[0] < 20 and 236 < xs[-1] <= 256)
        self.assertEqual(ex.view_origin(), (256, 0))
        ex.step(1, 0, 1 / 60)
        self.assertGreater(ex.walker.x, where[0])                               # and walks on

    def test_down_into_the_next_room_and_back_up(self):
        ex = self.ex
        route.walk_to_tile(ex, (6, 10))                         # the gap below the town's room
        for _ in range(60):
            ex.step(0, 1, 1 / 60)
        slide_out(ex)
        self.assertEqual(ex.room, (0, 1))
        self.assertGreaterEqual(ex.walker.box()[1], 176)
        route.walk_to_tile(ex, (6, 10))
        slide_out(ex)
        self.assertEqual(ex.room, (0, 0))
        self.assertEqual(self.s.world["seen"], ["sample_region#0,0", "sample_region#0,1"])


# ---- foes walk about their room -------------------------------------------------------------------------------------------------

class FoeTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def explorer_in(self, where, x, y):
        s = stand_in(where, x, y)
        return s, explore.Explorer(s)

    def test_a_foe_wanders_inside_its_room_and_never_into_a_wall(self):
        s, ex = self.explorer_in("sample_region", *mid(28, 8))            # room (1, 0), away from the ember bandit
        robber = next(f for f in ex.foes.values() if f.enemy == "grave_robber")
        self.assertEqual(robber.room, ex.room)
        moved = False
        r = ex.area.room_rect(robber.room)
        for _ in range(60 * 20):
            ex.step(0, 0, 1 / 60)
            b = robber.box
            self.assertFalse(ex.area.box_blocked(b))
            self.assertTrue(r[0] + TILE <= b[0] + 1e-6 and b[2] <= r[2] - TILE + 1e-6, b)
            self.assertTrue(r[1] + TILE <= b[1] + 1e-6 and b[3] <= r[3] - TILE + 1e-6, b)
            moved = moved or (robber.x, robber.y) != robber.home
        self.assertTrue(moved)

    def test_a_foe_in_a_pen_never_walks_into_its_walls(self):
        tmp = tempfile.mkdtemp(prefix="rpg6_")
        self.addCleanup(shutil.rmtree, tmp, True)
        pen = {(c, r) for c in range(5, 10) for r in range(3, 8) if c in (5, 9) or r in (3, 7)}   # 3 x 3 tiles inside
        objects = obj(1, "spawn", 40, 40, name="start") + \
            obj(2, "encounter", 112, 80, 16, 16, props={"kind": ("string", "elf_gang")})          # tile (7, 5): the middle
        area_of(tmp, room(16, 11, pen), 16, 11, objects=objects)
        explore.forget_areas()
        s = types.SimpleNamespace(world={"region": 1, "map": "m", "x": None, "y": None})
        ex = explore.Explorer(s, folder=tmp)
        foe = next(iter(ex.foes.values()))
        moved = False
        for _ in range(60 * 10):
            ex.step(0, 0, 1 / 60)
            self.assertFalse(ex.area.box_blocked(foe.box), foe.box)
            moved = moved or (foe.x, foe.y) != foe.home
        self.assertTrue(moved)

    def test_the_same_walk_every_time(self):
        def run():
            explore.forget_areas()
            _s, ex = self.explorer_in("sample_region", *mid(28, 8))
            for _ in range(60 * 5):
                ex.step(0, 0, 1 / 60)
            return sorted((f.spot, round(f.x, 6), round(f.y, 6)) for f in ex.room_foes())
        self.assertEqual(run(), run())

    def test_foes_of_other_rooms_stand_still_and_start_from_home_when_you_come_in(self):
        s, ex = self.explorer_in("sample_region", *mid(5, 7))
        s.world["beaten"] = [f.spot for f in ex.foes.values() if f.enemy != "grave_robber"]
        robber = next(f for f in ex.foes.values() if f.enemy == "grave_robber")
        for _ in range(120):
            ex.step(0, 0, 1 / 60)
        self.assertEqual((robber.x, robber.y), robber.home)                 # you aren't in its room
        route.walk_to_tile(ex, (16, 5))
        slide_out(ex)
        for _ in range(90):
            ex.step(0, 0, 1 / 60)
        self.assertNotEqual((robber.x, robber.y), robber.home)
        entered = robber.entered
        route.walk_to_tile(ex, (14, 5))                                     # back out ...
        slide_out(ex)
        route.walk_to_tile(ex, (15, 5))                                     # ... and in again
        walk_until_slide(ex, 1, 0)
        self.assertEqual(robber.entered, entered + 1)
        self.assertEqual((robber.x, robber.y), robber.home)                 # home again the moment you come in

    def test_the_boss_stands_still(self):
        s, ex = self.explorer_in("sample_dungeon", *mid(26, 7))
        boss = next(f for f in ex.foes.values() if f.encounter == "boss")
        self.assertEqual(boss.room, ex.room)
        self.assertFalse(boss.wanders)
        for _ in range(60 * 5):
            ex.step(0, 0, 1 / 60)
        self.assertEqual((boss.x, boss.y), boss.home)

    def test_walking_into_a_foe_and_a_foe_walking_into_you_both_start_its_duel(self):
        s, ex = self.explorer_in("sample_region", *mid(5, 7))
        thorn = next(f for f in ex.foes.values() if f.room == (0, 1))
        s.world["beaten"] = [f.spot for f in ex.foes.values() if f is not thorn]
        events, _ = route.walk_to_tile(ex, lambda: ex.area.tile_at(thorn.x, thorn.y))
        self.assertEqual([(k, t.spot) for k, t in events], [("encounter", thorn.spot)])
        # it comes to you: stand still beside it, facing it
        s.world["beaten"] = [f.spot for f in ex.foes.values() if f is not thorn]
        ex.walker.place(thorn.x - 14, thorn.y)
        thorn.dir, thorn.turn_in = (-1, 0), 5.0
        got = None
        for _ in range(60):
            got = ex.step(0, 0, 1 / 60)
            if got:
                break
        self.assertEqual(got and (got[0], got[1].spot), ("encounter", thorn.spot))
        s.world["beaten"].append(thorn.spot)
        self.assertNotIn(thorn, ex.room_foes())
        thorn.x, thorn.y = ex.walker.x, ex.walker.y
        self.assertIsNone(ex.step(0, 0, 1 / 60))                            # a won fight never fires again

    def test_a_foe_marked_wander_false_stands_still(self):
        tmp = tempfile.mkdtemp(prefix="rpg6_")
        self.addCleanup(shutil.rmtree, tmp, True)
        objects = (obj(1, "spawn", 40, 40, name="start") +
                   obj(2, "encounter", 96, 80, 16, 16, props={"kind": ("string", "elf_gang"),
                                                               "wander": ("bool", "false")}) +
                   obj(3, "encounter", 160, 80, 16, 16, props={"kind": ("string", "elf_gang")}))
        area_of(tmp, room(16, 11), 16, 11, objects=objects)
        explore.forget_areas()
        s = types.SimpleNamespace(world={"region": 1, "map": "m", "x": None, "y": None})
        ex = explore.Explorer(s, folder=tmp)
        still, walks = sorted(ex.foes.values(), key=lambda f: f.home)
        for _ in range(60 * 5):
            ex.step(0, 0, 1 / 60)
        self.assertEqual((still.x, still.y), still.home)
        self.assertNotEqual((walks.x, walks.y), walks.home)


# ---- the save -------------------------------------------------------------------------------------------------------------------

class SeenSaveTests(unittest.TestCase):
    def test_the_rooms_seen_are_saved_and_read_back(self):
        tmp = tempfile.mkdtemp(prefix="rpg6_save_")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = r4.started_save()
        s.world.update(map="sample_region", x=None, y=None)
        explore.forget_areas()
        explore.Explorer(s, start_town="town_1")
        self.assertEqual(s.world["seen"], ["sample_region#0,0"])
        sv.write(s, tmp)
        self.assertEqual(sv.read(1, tmp, pool=r4.POOL)[0].world["seen"], ["sample_region#0,0"])
        bad = tempfile.mkdtemp(prefix="rpg6_save_")                    # a fresh slot: no backup to fall back to
        self.addCleanup(shutil.rmtree, bad, True)
        s.world["seen"] = ["sample_region#0,0", 3]
        sv.write(s, bad)
        with self.assertRaises(sv.SaveError):
            sv.read(1, bad, pool=r4.POOL)

    def test_an_rpg5_save_without_seen_still_reads(self):
        tmp = tempfile.mkdtemp(prefix="rpg6_save_")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = r4.started_save()
        s.world.pop("seen", None)
        sv.write(s, tmp)
        back = sv.read(1, tmp, pool=r4.POOL)[0]
        self.assertNotIn("seen", back.world)
        explore.forget_areas()
        explore.Explorer(back, start_town="town_1")
        self.assertEqual(len(back.world["seen"]), 1)


# ---- the shipped maps -----------------------------------------------------------------------------------------------------------

class ShippedRoomTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_whole_rooms_every_room_reachable_and_every_foe_free_at_home(self):
        for name, rooms in (("sample_region", (3, 2)), ("sample_dungeon", (2, 2))):
            with self.subTest(name=name):
                a = explore.load_area(name)
                self.assertEqual((a.cols % 16, a.rows % 11), (0, 0))
                self.assertEqual((a.room_cols, a.room_rows), rooms)
                reach = a.reachable(a.spawn())
                for rx in range(a.room_cols):
                    for ry in range(a.room_rows):
                        self.assertTrue(any(a.room_of(*mid(c, r)) == (rx, ry) for c, r in reach), (rx, ry))
                for t in a.triggers:
                    if t.kind == "encounter":
                        f = explore.Foe(a, t)
                        self.assertTrue(f._free(*f.home), t)

    def test_every_room_change_on_the_shipped_maps_can_be_walked(self):
        """Walk (one key at a time) from the start into every room of both maps and back to the start."""
        for name in ("sample_region", "sample_dungeon"):
            with self.subTest(name=name):
                explore.forget_areas()
                s = stand_in(name, None, None)
                ex = explore.Explorer(s)
                s.world["beaten"] = [t.spot for t in ex.area.triggers if t.kind == "encounter"]
                home = ex.area.tile_at(*ex.area.spawn())
                reach = ex.area.reachable(ex.area.spawn())
                for rx in range(ex.area.room_cols):
                    for ry in range(ex.area.room_rows):
                        goal = min((t for t in reach if ex.area.room_of(*mid(*t)) == (rx, ry)
                                    and t not in route.trigger_tiles(ex.area)),
                                   key=lambda t: (abs(t[0] - home[0]) + abs(t[1] - home[1]), t))
                        events, _ = route.walk_to_tile(ex, goal)
                        self.assertEqual(events, [])
                        slide_out(ex)
                        self.assertEqual(ex.room, (rx, ry))
                route.walk_to_tile(ex, home)
                self.assertEqual(len(s.world["seen"]), ex.area.room_cols * ex.area.room_rows)

    def test_the_dungeon_is_dark_and_the_region_is_not(self):
        self.assertEqual(explore.load_area("sample_dungeon").light, 4.5)
        self.assertIsNone(explore.load_area("sample_region").light)


# ---- drawing --------------------------------------------------------------------------------------------------------------------

class RoomDrawTests(Temp):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        pygame.init()
        cls.pg = pygame
        pygame.display.set_mode((64, 64))

    def explorer(self, where="sample_region", x=None, y=None):
        explore.forget_areas()
        s = stand_in(where, x, y)
        if x is None:
            s = stand_in()
        ex = explore.Explorer(s, start_town="town_1")
        return s, ex

    def test_only_the_room_is_drawn_and_the_panels_beside_it(self):
        import gfx
        import rpg_map_view
        s, ex = self.explorer()
        view = rpg_map_view.MapView(ex.area)
        for size in ((1920, 1080), (1366, 768), (4096, 2160), (1024, 768), (800, 600)):
            with self.subTest(size=size):
                screen = self.pg.Surface(size)
                cam = explore.RoomCamera(*size, ex.area.tw, ex.area.th)
                cam.look_at(ex.view_origin())
                view.draw(screen, cam, ex.walker, ex.encounters(), 0.0)
                x, y, w, h = cam.viewport()
                for px in ((x - 1, y + h // 2), (x + w, y + h // 2), (x + w // 2, max(0, y - 1)), (2, 2)):
                    if 0 <= px[0] < size[0] and 0 <= px[1] < size[1] and not (x <= px[0] < x + w and y <= px[1] < y + h):
                        self.assertEqual(tuple(screen.get_at(px))[:3], gfx.BACKDROP, px)
                self.assertNotEqual(tuple(screen.get_at((x + w // 2, y + h // 2)))[:3], gfx.BACKDROP)
                out = rpg_map_view.draw_panels(screen, cam, ex.area, ex.room, s.world["seen"],
                                               {"gold": 100, "deck": "A deck with a rather long name indeed", "cards": 60})
                for side in ("left", "right"):
                    r = out[side]
                    self.assertIsNotNone(r, side)
                    self.assertTrue(self.pg.Rect(0, 0, *size).contains(r))
                    self.assertFalse(r.colliderect(self.pg.Rect(cam.viewport())), side)
                self.assertTrue(out["right"].contains(out["minimap"]))
                self.assertEqual(len(out["cells"]), 6)
                self.assertEqual(tuple(screen.get_at(out["cells"][(0, 0)].center))[:3], gfx.GOLD)
                self.assertEqual(tuple(screen.get_at(out["cells"][(2, 1)].center))[:3], gfx.PANEL_DEEP)

    def test_half_way_through_a_slide_nothing_is_drawn_outside_the_room(self):
        import gfx
        import rpg_map_view
        s, ex = self.explorer()
        view = rpg_map_view.MapView(ex.area)
        screen = self.pg.Surface((1920, 1080))
        cam = explore.RoomCamera(1920, 1080)
        cam.look_at((100, 40))                       # not on a tile's edge: the tiles at the sides are cut by the room's edge
        view.draw(screen, cam, ex.walker, ex.encounters(), 0.0)
        x, y, w, h = cam.viewport()
        for px in ((x - 1, y + h // 2), (x + w, y + h // 2), (x + w // 2, y - 1), (x + w // 2, y + h)):
            self.assertEqual(tuple(screen.get_at(px))[:3], gfx.BACKDROP, px)
        self.assertNotEqual(tuple(screen.get_at((x, y + h // 2)))[:3], gfx.BACKDROP)

    def test_a_seen_room_on_the_minimap(self):
        import gfx
        import rpg_map_view
        s, ex = self.explorer()
        s.world["seen"].append("sample_region#1,0")
        screen = self.pg.Surface((1920, 1080))
        cam = explore.RoomCamera(1920, 1080)
        out = rpg_map_view.draw_panels(screen, cam, ex.area, ex.room, s.world["seen"])
        self.assertEqual(tuple(screen.get_at(out["cells"][(1, 0)].center))[:3], gfx.PANEL_EDGE)
        self.assertEqual(tuple(screen.get_at(out["cells"][(1, 1)].center))[:3], gfx.PANEL_DEEP)

    def test_the_dungeon_is_dark_but_round_the_walker(self):
        import rpg_map_view
        s, ex = self.explorer("sample_dungeon", *mid(4, 17))
        view = rpg_map_view.MapView(ex.area)
        cam = explore.RoomCamera(1920, 1080)
        cam.look_at(ex.view_origin())
        lit = self.pg.Surface((1920, 1080))
        view.draw(lit, cam, ex.walker, [], 0.0)
        light, ex.area.light = ex.area.light, None
        try:
            plain = self.pg.Surface((1920, 1080))
            view.draw(plain, cam, ex.walker, [], 0.0)
        finally:
            ex.area.light = light
        near = cam.to_screen(ex.walker.x + 8, ex.walker.y - 12)
        far = cam.to_screen(*mid(12, 14))
        self.assertEqual(lit.get_at(near), plain.get_at(near))               # in the light: as it is
        bright = sum(tuple(plain.get_at(far))[:3])
        self.assertLess(sum(tuple(lit.get_at(far))[:3]), bright / 4)          # away from it: dark

    def test_long_titles_wrap_and_a_long_word_is_cut(self):
        import gfx
        import rpg_map_view
        self.pg.font.init()
        font = gfx.get_font(20)
        lines = rpg_map_view.wrap("The Outskirts of a Town With a Very Long Name", font, 120)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(font.size(t)[0] <= 120 for t in lines))
        self.assertLessEqual(len(rpg_map_view.wrap("word " * 40, font, 60, most=3)), 3)
        cut = rpg_map_view.wrap("Supercalifragilisticexpialidocious", font, 50)
        self.assertTrue(cut and font.size(cut[0])[0] <= 50)

    def test_the_view_has_no_colour_literals_and_the_engine_no_pygame(self):
        with open(os.path.join(BASE, "rpg_map_view.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        tuples = [n for n in ast.walk(tree) if isinstance(n, ast.Tuple) and len(n.elts) in (3, 4)
                  and all(isinstance(e, ast.Constant) and isinstance(e.value, int) and 0 <= e.value <= 255 for e in n.elts)]
        self.assertEqual([ast.unparse(t) for t in tuples], [])
        with open(os.path.join(BASE, "rpg", "explore.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse({m for m in mods if m.split(".")[0] == "pygame"})


class WalkToolTests(unittest.TestCase):
    def run_tool(self, walk, size="1920x1080"):
        tmp = tempfile.mkdtemp(prefix="rpg6_walk_")
        self.addCleanup(shutil.rmtree, tmp, True)
        out = os.path.join(tmp, "walk.png")
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "rpg_walk.py"), "--walk", walk, "--screenshot", out,
                            "--size", size], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy"))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, out

    def test_the_window_walks_through_a_room_and_draws_the_panels(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        import gfx
        stdout, out = self.run_tool("right 0.25, down 2.2")
        last = stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("saved "), stdout)
        x, y = (int(v) for v in last.split("(")[1].rstrip(")").split(","))
        self.assertGreaterEqual(y, 176, stdout)                                   # in the room below the town's
        pic = pygame.image.load(out)
        self.assertEqual(pic.get_size(), (1920, 1080))
        cam = explore.RoomCamera(1920, 1080)
        left, _right = cam.panels()
        self.assertNotEqual(tuple(pic.get_at((left[2] // 2, 1080 // 2)))[:3], gfx.BACKDROP)   # a panel is drawn there


if __name__ == "__main__":
    unittest.main()
