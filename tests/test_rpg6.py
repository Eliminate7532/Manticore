# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG6 (Karl, 12 Oct 2026: the RPG's look and feel inspired by the original Zelda): one 16 x 11-tile room on screen at a
time with side panels, the view sliding to the next room, walking in four directions on a half-tile grid with help round
corners, foes that wander about their room, the rooms you have seen in the save, the Diablo / Vermis dark palette and a light
round the walker in the dungeon. Map side only: the duels keep the normal table.

Small maps are written to a temp folder (tests.test_rpg5's helpers); the "Shipped" tests read rpg/maps/.

Round RPG11 (the Adventure remaster, Karl 23 Oct 2026: free scrolling instead of rooms) took the rooms out: the room, room-camera,
slide, minimap, side-panel and "seen" tests are gone; the foe tests check the leash and waking near the walker; the drawing tests
use the following camera. What RPG11 adds is tested in tests/test_rpg11.py.
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
from tests.rpg_fixture_maps import FIXTURE_MAPS, setUpModule, tearDownModule  # noqa: F401  (Round RPG12: the placeholder maps)

TILE = 16


def mid(c, r):
    return c * TILE + TILE / 2, r * TILE + TILE / 2


def frames(n, fn):
    out = []
    for _ in range(n):
        out.append(fn())
    return out




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


# ---- the light property --------------------------------------------------------------------------------------------------------

class LightPropertyTests(Temp):
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



# ---- foes walk about their spot (Round RPG11: within their leash, while you are near) ---------------------------------------

class FoeTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def explorer_in(self, where, x, y):
        s = stand_in(where, x, y)
        return s, explore.Explorer(s)

    def test_a_foe_wanders_within_its_leash_and_never_into_a_wall(self):
        s, ex = self.explorer_in("sample_region", *mid(28, 8))
        robber = next(f for f in ex.foes.values() if f.enemy == "grave_robber")
        self.assertTrue(robber.active)
        moved = False
        for _ in range(60 * 20):
            ex.step(0, 0, 1 / 60)
            self.assertFalse(ex.area.box_blocked(robber.box))
            self.assertLessEqual(((robber.x - robber.home[0]) ** 2 + (robber.y - robber.home[1]) ** 2) ** 0.5,
                                 explore.LEASH_TILES * TILE + 1e-6)
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
            return sorted((f.spot, round(f.x, 6), round(f.y, 6)) for f in ex.active_foes())
        self.assertEqual(run(), run())
    def test_far_foes_stand_still_and_start_from_home_when_you_come_near(self):
        s, ex = self.explorer_in("sample_region", *mid(5, 7))
        s.world["beaten"] = [f.spot for f in ex.foes.values() if f.enemy != "grave_robber"]
        robber = next(f for f in ex.foes.values() if f.enemy == "grave_robber")
        self.assertFalse(robber.active)
        for _ in range(120):
            ex.step(0, 0, 1 / 60)
        self.assertEqual((robber.x, robber.y), robber.home)                 # too far from you to move
        reach = ex.area.reachable(ex.area.spawn()) - route.trigger_tiles(ex.area)
        near = min(reach, key=lambda t: (abs(mid(*t)[0] - (robber.home[0] - 9 * TILE)) + abs(mid(*t)[1] - robber.home[1]), t))
        route.walk_to_tile(ex, near)                                        # about 9 tiles to its left
        self.assertTrue(robber.active)
        for _ in range(90):
            ex.step(0, 0, 1 / 60)
        self.assertNotEqual((robber.x, robber.y), robber.home)
        woken = robber.woken
        route.walk_to_tile(ex, (5, 7))                                      # away ...
        self.assertFalse(robber.active)
        self.assertNotEqual((robber.x, robber.y), robber.home)              # it stops where it is
        route.walk_to_tile(ex, near)                                        # ... and near again
        self.assertEqual(robber.woken, woken + 1)
        self.assertTrue(robber.active)


    def test_the_boss_stands_still(self):
        s, ex = self.explorer_in("sample_dungeon", *mid(26, 7))
        boss = next(f for f in ex.foes.values() if f.encounter == "boss")
        self.assertTrue(boss.active)
        self.assertFalse(boss.wanders)
        for _ in range(60 * 5):
            ex.step(0, 0, 1 / 60)
        self.assertEqual((boss.x, boss.y), boss.home)

    def test_walking_into_a_foe_and_a_foe_walking_into_you_both_start_its_duel(self):
        s, ex = self.explorer_in("sample_region", *mid(5, 7))
        thorn = next(f for f in ex.foes.values() if f.home[0] < 256 and f.home[1] >= 176)     # RPG6's room (0, 1)
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
        self.assertNotIn(thorn, ex.active_foes())
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

class FacingSaveTests(unittest.TestCase):
    """Round RPG11: the save keeps which way you face; RPG6's "seen" rooms are no longer written, and an old list still reads."""

    def test_the_facing_is_saved_and_read_back(self):
        tmp = tempfile.mkdtemp(prefix="rpg6_save_")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = r4.started_save()
        s.world.update(map="sample_region", x=None, y=None)
        explore.forget_areas()
        ex = explore.Explorer(s, start_town="town_1")
        self.assertEqual(s.world["facing"], "down")
        for _ in range(10):
            ex.step(-1, 0, 1 / 60)
        self.assertEqual(s.world["facing"], "left")
        self.assertNotIn("seen", s.world)
        sv.write(s, tmp)
        back = sv.read(1, tmp, pool=r4.POOL)[0]
        self.assertEqual(back.world["facing"], "left")
        explore.forget_areas()
        self.assertEqual(explore.Explorer(back, start_town="town_1").walker.facing, "left")
        bad = tempfile.mkdtemp(prefix="rpg6_save_")                    # a fresh slot: no backup to fall back to
        self.addCleanup(shutil.rmtree, bad, True)
        s.world["facing"] = "sideways"
        sv.write(s, bad)
        with self.assertRaises(sv.SaveError):
            sv.read(1, bad, pool=r4.POOL)

    def test_an_rpg6_save_with_seen_rooms_still_reads_and_keeps_them(self):
        tmp = tempfile.mkdtemp(prefix="rpg6_save_")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = r4.started_save()
        s.world["seen"] = ["sample_region#0,0", "sample_region#1,0"]
        s.world.pop("facing", None)
        sv.write(s, tmp)
        back = sv.read(1, tmp, pool=r4.POOL)[0]
        explore.forget_areas()
        ex = explore.Explorer(back, start_town="town_1")
        self.assertEqual(back.world["seen"], ["sample_region#0,0", "sample_region#1,0"])
        self.assertEqual(ex.walker.facing, "down")


# ---- the shipped maps -----------------------------------------------------------------------------------------------------------

class ShippedMapTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_every_trigger_reachable_and_every_foe_free_at_home(self):
        for name in ("sample_region", "sample_dungeon"):
            with self.subTest(name=name):
                a = explore.load_area(name)
                self.assertEqual([p for p in a.check() if "reached" in p], [])
                for t in a.triggers:
                    if t.kind == "encounter":
                        f = explore.Foe(a, t)
                        self.assertTrue(f._free(*f.home), t)

    def test_the_far_corner_of_each_shipped_map_can_be_walked_to_and_back(self):
        """Walk (one key at a time) from the start to the reachable tile furthest from it, and back (RPG6 walked into every room)."""
        for name in ("sample_region", "sample_dungeon"):
            with self.subTest(name=name):
                explore.forget_areas()
                s = stand_in(name, None, None)
                ex = explore.Explorer(s)
                s.world["beaten"] = [t.spot for t in ex.area.triggers if t.kind == "encounter"]
                home = ex.area.tile_at(*ex.area.spawn())
                reach = ex.area.reachable(ex.area.spawn()) - route.trigger_tiles(ex.area)
                far = max(reach, key=lambda t: (abs(t[0] - home[0]) + abs(t[1] - home[1]), t))
                self.assertGreater(abs(far[0] - home[0]) + abs(far[1] - home[1]), 20)
                events, _ = route.walk_to_tile(ex, far)
                self.assertEqual(events, [])
                self.assertEqual(ex.area.tile_at(ex.walker.x, ex.walker.y), far)
                events, _ = route.walk_to_tile(ex, home)
                self.assertEqual(events, [])
                self.assertEqual(ex.area.tile_at(ex.walker.x, ex.walker.y), home)

    def test_the_dungeon_is_dark_and_the_region_is_not(self):
        self.assertEqual(explore.load_area("sample_dungeon").light, 4.5)
        self.assertIsNone(explore.load_area("sample_region").light)



# ---- drawing --------------------------------------------------------------------------------------------------------------------

class MapDrawTests(Temp):
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

    def test_the_map_fills_the_window_at_every_size(self):
        import gfx
        import rpg_map_view
        s, ex = self.explorer()
        view = rpg_map_view.MapView(ex.area)
        for size in ((1920, 1080), (1366, 768), (4096, 2160), (1024, 768), (800, 600)):
            with self.subTest(size=size):
                screen = self.pg.Surface(size)
                cam = explore.Camera(*size, explore.Camera.zoom_for(size[1], ex.area.th))
                cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
                view.draw(screen, cam, ex.walker, ex.encounters(), 0.0)
                for px in ((size[0] // 2, size[1] // 2), (2, 2), (size[0] - 3, size[1] - 3), (2, size[1] - 3)):
                    mx, my = cam.to_map(*px)
                    if 0 <= mx < ex.area.width and 0 <= my < ex.area.height:
                        self.assertNotEqual(tuple(screen.get_at(px))[:3], gfx.BACKDROP, px)
                    else:
                        self.assertEqual(tuple(screen.get_at(px))[:3], gfx.BACKDROP, px)


    def test_the_dungeon_is_dark_but_round_the_walker(self):
        import rpg_map_view
        s, ex = self.explorer("sample_dungeon", *mid(4, 17))
        view = rpg_map_view.MapView(ex.area)
        cam = explore.Camera(1920, 1080, explore.Camera.zoom_for(1080, ex.area.th))
        cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        lit = self.pg.Surface((1920, 1080))
        view.draw(lit, cam, ex.walker, [], 0.0)
        view.lit = False                                # Round RPG15: the Light setting off = the plain art (expected change)
        try:
            plain = self.pg.Surface((1920, 1080))
            view.draw(plain, cam, ex.walker, [], 0.0)
        finally:
            view.lit = True
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
                            "--size", size, "--maps", FIXTURE_MAPS], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy"))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, out

    def test_the_window_walks_and_draws_the_map(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        import gfx
        stdout, out = self.run_tool("right 0.25, down 2.2")
        last = stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("saved "), stdout)
        x, y = (int(v) for v in last.split("(")[1].rstrip(")").split(","))
        self.assertGreaterEqual(y, 176, stdout)                                   # below the town (RPG6's next room)
        pic = pygame.image.load(out)
        self.assertEqual(pic.get_size(), (1920, 1080))
        self.assertNotEqual(tuple(pic.get_at((1920 // 2, 1080 // 2)))[:3], gfx.BACKDROP)     # the map, round the walker
        self.assertNotEqual(tuple(pic.get_at((4, 1080 // 2)))[:3], gfx.BACKDROP)            # no side panels now: map there too


if __name__ == "__main__":
    unittest.main()
