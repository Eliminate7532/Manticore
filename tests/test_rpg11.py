# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG11 (the Adventure remaster, Karl 23 Oct 2026; claude/SPEC_RPG_SLICE_2026-10-23.md): the map scrolls freely and its
people walk.

  CameraTests        the camera follows the walker on whole map pixels, never past the map's edge, at a zoom for ~15 rows
  FoeTests           a foe moves only near the walker and stays within its leash
  WalkFrameTests     the walker's DawnLike frames: its facing's row, a frame every half tile, the standing frame when it stops
  DrawTests          the people in order of their feet, the layer above them, no shimmer, the old shapes without the art
  ArtTests           rpg/data/art.json: every foe kind has a sprite, and every cell it names is in its sheets and not empty
  HeroTests          the hero you walk as: picked on the new game's name screen, kept in the save, its sheet on the map
  ScreenTests        the Adventure's map screen: the corner window, the keys and Menu at every size, nothing overlapping
"""
import json
import os
import random
import shutil
import tempfile
import types
import unittest
from unittest import mock

from rpg import enemies, explore
from tests.test_rpg5 import BASE, Temp, area_of, layer_xml, obj, room, stand_in, write_map
from tests.rpg_fixture_maps import FIXTURE_MAPS, setUpModule, tearDownModule  # noqa: F401  (Round RPG12: the placeholder maps)

TILE = 16
ART = os.path.join(BASE, "rpg", "data", "art.json")
DAWNLIKE = os.path.join(BASE, "assets", "rpg", "dawnlike")


def mid(c, r):
    return c * TILE + TILE / 2, r * TILE + TILE / 2


def pg():
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((64, 64))
    return pygame


# ---- the camera ---------------------------------------------------------------------------------------------------------------

class CameraTests(unittest.TestCase):
    def test_it_stops_on_whole_map_pixels(self):
        rng = random.Random(11)
        cam = explore.Camera(1920, 1080, 4)
        for _ in range(500):
            x, y = rng.uniform(0, 768), rng.uniform(0, 352)
            cam.follow(x, y, 768, 352)
            self.assertEqual((cam.x, cam.y), (int(cam.x), int(cam.y)), (x, y))

    def test_the_walker_stays_in_the_middle_until_the_edge(self):
        cam = explore.Camera(1920, 1080, 4)                       # 480 x 270 map pixels in view
        cam.follow(384.3, 176.7, 768, 352)
        sx, sy = cam.to_screen(384.3, 176.7)
        self.assertLessEqual(abs(sx - 960), 4)
        self.assertLessEqual(abs(sy - 540), 4)
        for x, y in ((0, 0), (768, 352), (5, 340), (760, 3)):
            cam.follow(x, y, 768, 352)
            self.assertTrue(0 <= cam.x <= 768 - 480 and 0 <= cam.y <= 352 - 270, (x, y, cam.x, cam.y))

    def test_a_map_smaller_than_the_view_is_centred(self):
        cam = explore.Camera(4096, 2160, 9)                       # 455 x 240 map pixels in view
        cam.follow(100, 50, 256, 176)
        self.assertEqual((cam.x, cam.y), (float(round((256 - 4096 / 9) / 2)), float(round((176 - 240) / 2))))

    def test_the_zoom_shows_about_fifteen_rows_at_every_size(self):
        for (w, h), zoom in (((4096, 2160), 9), ((2560, 1440), 6), ((1920, 1080), 4), ((1366, 768), 3), ((1024, 640), 2),
                             ((800, 600), 2), ((320, 200), 1)):
            with self.subTest(size=(w, h)):
                self.assertEqual(explore.Camera.zoom_for(h, TILE), zoom)
                if h >= TILE * explore.ROWS_IN_VIEW:
                    rows = h / zoom / TILE
                    self.assertTrue(explore.ROWS_IN_VIEW <= rows < 2 * explore.ROWS_IN_VIEW, rows)


# ---- foes ---------------------------------------------------------------------------------------------------------------------

class FoeTests(Temp):
    def corridor(self, wander=True):
        """A 60 x 5 corridor: the start at its left end, one foe at column 40."""
        props = {"kind": ("string", "elf_gang")}
        if not wander:
            props["wander"] = ("bool", "false")
        objects = obj(1, "spawn", 24, 40, name="start") + obj(2, "encounter", 40 * TILE, 2 * TILE, 16, 16, props=props)
        area_of(self.tmp, room(60, 5), 60, 5, objects=objects)
        s = types.SimpleNamespace(world={"region": 1, "map": "m", "x": None, "y": None})
        ex = explore.Explorer(s, folder=self.tmp)
        return ex, next(iter(ex.foes.values()))

    def test_a_foe_moves_only_within_active_tiles_of_the_walker(self):
        ex, foe = self.corridor()
        reach = explore.ACTIVE_TILES * TILE
        hx, hy = foe.home
        ex.walker.place(hx - reach - 1, hy)
        ex.step(0, 0, 1 / 60)
        self.assertFalse(foe.active)
        ex.walker.place(hx - reach, hy)
        ex.step(0, 0, 1 / 60)
        self.assertTrue(foe.active)
        self.assertEqual(foe.woken, 1)
        for _ in range(60 * 3):
            ex.step(0, 0, 1 / 60)
        self.assertNotEqual((foe.x, foe.y), foe.home)
        ex.walker.place(hx - reach - 1, hy)
        ex.step(0, 0, 1 / 60)
        self.assertFalse(foe.active)
        where = (foe.x, foe.y)
        for _ in range(60):
            ex.step(0, 0, 1 / 60)
        self.assertEqual((foe.x, foe.y), where)                       # stood still where it was

    def test_a_foe_never_wanders_past_its_leash(self):
        ex, foe = self.corridor()
        ex.walker.place(foe.home[0] - 6 * TILE, foe.home[1])
        furthest = 0.0
        for _ in range(60 * 60):
            ex.step(0, 0, 1 / 60)
            furthest = max(furthest, abs(foe.x - foe.home[0]) + abs(foe.y - foe.home[1]))
            self.assertLessEqual(((foe.x - foe.home[0]) ** 2 + (foe.y - foe.home[1]) ** 2) ** 0.5,
                                 explore.LEASH_TILES * TILE + 1e-6)
        self.assertGreater(furthest, 2 * TILE)                        # it does use its room to roam

    def test_waking_again_walks_on_from_where_it_stood_the_same_way_every_run(self):
        """Round RPG15c (review R7, expected change): a foe that wakes again walks on from where it stood - it used to start
        again from home, a hop in plain view. The walk is still seeded by how many times it woke."""
        ex, foe = self.corridor()
        reach = explore.ACTIVE_TILES * TILE
        starts = []

        def walk():
            ex.walker.place(foe.home[0] - reach, foe.home[1])
            starts.append((round(foe.x, 6), round(foe.y, 6)))
            out = []
            for _ in range(120):
                ex.step(0, 0, 1 / 60)
                out.append((round(foe.x, 6), round(foe.y, 6)))
            ex.walker.place(foe.home[0] - reach - TILE, foe.home[1])
            ex.step(0, 0, 1 / 60)
            return out
        first, second = walk(), walk()
        self.assertEqual(foe.woken, 2)
        self.assertEqual(starts[0], foe.home)
        self.assertEqual(starts[1], first[-1])                        # the second walk begins where the first stopped
        self.assertNotEqual(first, second)                            # seeded by how many times it woke ...
        explore.forget_areas()
        ex2, foe2 = self.corridor()
        ex, foe = ex2, foe2
        self.assertEqual(walk(), first)                               # ... so the same walk comes out every time

    def test_a_foe_that_stands_still_never_moves(self):
        ex, foe = self.corridor(wander=False)
        ex.walker.place(foe.home[0] - 4 * TILE, foe.home[1])
        for _ in range(60 * 5):
            ex.step(0, 0, 1 / 60)
        self.assertTrue(foe.active)
        self.assertEqual((foe.x, foe.y), foe.home)


# ---- the walker's frames ------------------------------------------------------------------------------------------------------

class WalkFrameTests(unittest.TestCase):
    def setUp(self):
        pg()
        import rpg_map_view
        self.mv = rpg_map_view
        self.sprites = rpg_map_view.Sprites()

    def test_a_frame_every_half_tile_and_the_standing_frame_when_it_stops(self):
        half = TILE * self.mv.WALK_FRAME_TILES
        for walked, frame in ((0, 0), (half - 0.01, 0), (half, 1), (2 * half, 2), (3 * half + 1, 3), (4 * half, 0)):
            self.assertEqual(self.sprites.walker_frame("down", walked, True, TILE), (0, frame), walked)
            self.assertEqual(self.sprites.walker_frame("down", walked, False, TILE), (0, 0), walked)

    def test_each_facing_has_its_row(self):
        rows = {f: self.sprites.walker_frame(f, 0, False, TILE)[0] for f in explore.FACINGS}
        self.assertEqual(rows, {"down": 0, "left": 1, "right": 2, "up": 3})

    def test_the_explorer_counts_how_far_it_walked_and_whether_it_moves(self):
        explore.forget_areas()
        ex = explore.Explorer(stand_in("sample_region", *mid(10, 8)))
        ex.save.world["beaten"] = [f.spot for f in ex.foes.values()]
        for _ in range(30):
            ex.step(1, 0, 1 / 60)
        self.assertTrue(ex.walker.moving)
        self.assertEqual(ex.walker.facing, "right")
        self.assertAlmostEqual(ex.walker.walked, 30 * explore.WALK_TILES_PER_SECOND * TILE / 60, places=6)
        self.assertEqual(self.sprites.walker_frame("right", ex.walker.walked, True, TILE)[0], 2)
        ex.step(0, 0, 1 / 60)
        self.assertFalse(ex.walker.moving)
        walked = ex.walker.walked
        ex.step(0, -1, 0.0)
        self.assertEqual(ex.walker.walked, walked)

    def test_the_picture_is_the_sheets_cell(self):
        pygame = pg()
        sheet = pygame.image.load(os.path.join(DAWNLIKE, "Commissions", "Warrior.png")).convert_alpha()
        for facing, walked, moving in (("down", 0, False), ("left", 8, True), ("up", 24, True)):
            row, frame = self.sprites.walker_frame(facing, walked, moving, TILE)
            img = self.sprites.walker(facing, walked, moving, TILE, 3)
            self.assertEqual(img.get_size(), (48, 48))
            for i in range(16):
                for j in range(16):
                    self.assertEqual(tuple(img.get_at((i * 3 + 1, j * 3 + 1))), tuple(sheet.get_at((frame * 16 + i, row * 16 + j))))

    def test_a_foe_swaps_its_two_sheets(self):
        pygame = pg()
        art = json.load(open(ART, encoding="utf-8"))
        k = art["kinds"]["grave_robber"]
        col, row = k["cell"]
        for now, half in ((0.0, 0), (0.49, 0), (0.5, 1), (0.99, 1), (1.0, 0)):
            img = self.sprites.foe("grave_robber", now, 1)
            sheet = pygame.image.load(os.path.join(DAWNLIKE, *f"{k['sheet']}{half}.png".split("/"))).convert_alpha()
            cell = sheet.subsurface((col * 16, row * 16, 16, 16))
            self.assertEqual([tuple(img.get_at((i, j))) for i in range(16) for j in range(16)],
                             [tuple(cell.get_at((i, j))) for i in range(16) for j in range(16)], now)
        self.assertIsNone(self.sprites.foe("nobody", 0.0, 1))


# ---- drawing ------------------------------------------------------------------------------------------------------------------

class DrawTests(Temp):
    def setUp(self):
        super().setUp()
        self.pg = pg()
        import rpg_map_view
        self.mv = rpg_map_view

    def tileset_picture(self):
        img = self.pg.Surface((32, 32))
        img.fill((20, 120, 20), (0, 0, 16, 16))          # gid 1: floor, green
        img.fill((60, 60, 60), (16, 0, 16, 16))          # gid 2: wall
        img.fill((10, 10, 200), (0, 16, 16, 16))         # gid 3: a roof, blue
        img.fill((90, 90, 90), (16, 16, 16, 16))
        self.pg.image.save(img, os.path.join(self.tmp, "t.png"))

    def roof_map(self, name="roof", props=""):
        self.tileset_picture()
        ground = room(9, 7)
        roof = [0] * (9 * 7)
        roof[3 * 9 + 4] = 3                             # over tile (4, 3)
        objects = obj(1, "spawn", 4 * TILE + 8, 3 * TILE + 8, name="start")
        write_map(self.tmp, "m", 9, 7, [layer_xml("ground", 9, 7, ground), layer_xml(name, 9, 7, roof, props=props)], objects)
        explore.forget_areas()
        s = types.SimpleNamespace(world={"region": 1, "map": "m", "x": None, "y": None})
        return explore.Explorer(s, folder=self.tmp)

    def tile_pixels(self, layer_name, props=""):
        ex = self.roof_map(layer_name, props)
        view = self.mv.MapView(ex.area)
        screen = self.pg.Surface((288, 224))
        cam = explore.Camera(288, 224, 2)
        cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        view.draw(screen, cam, ex.walker, [], 0.0)
        x0, y0 = cam.to_screen(4 * TILE, 3 * TILE)
        return {tuple(screen.get_at((x0 + i, y0 + j)))[:3] for i in range(32) for j in range(32)}

    def test_a_layer_named_above_or_marked_above_is_drawn_over_the_walker(self):
        self.assertEqual(self.tile_pixels("above"), {(10, 10, 200)})
        self.assertEqual(self.tile_pixels("Roofs", '<properties><property name="above" type="bool" value="true"/>'
                                                   '</properties>'), {(10, 10, 200)})
        self.assertGreater(len(self.tile_pixels("roofs")), 1)          # an ordinary layer: the walker stands over it

    def test_people_are_drawn_in_order_of_their_feet(self):
        explore.forget_areas()
        ex = explore.Explorer(stand_in("sample_region", *mid(28, 8)))
        foe = next(f for f in ex.foes.values() if f.enemy == "grave_robber")
        view = self.mv.MapView(ex.area)
        screen = self.pg.Surface((640, 480))
        cam = explore.Camera(640, 480, 2)
        cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        for up, first in ((6, "foe"), (-6, "walker")):
            with self.subTest(up=up):
                foe.x, foe.y = ex.walker.x + 4, ex.walker.y + (ex.walker.hh - foe.hh) - up   # its feet `up` px higher
                order = []
                with mock.patch.object(view, "draw_walker", lambda *a: order.append("walker")), \
                        mock.patch.object(view, "draw_foe", lambda *a: order.append("foe")):
                    view.draw(screen, cam, ex.walker, [foe], 0.0)
                self.assertEqual(order[0], first)
                self.assertEqual(sorted(order), ["foe", "walker"])

    def test_a_walker_between_whole_pixels_is_drawn_the_same(self):
        explore.forget_areas()
        ex = explore.Explorer(stand_in("sample_region", *mid(20, 10)))
        view = self.mv.MapView(ex.area)
        cam = explore.Camera(1920, 1080, 4)
        shots = []
        for x in (ex.walker.x + 0.2, ex.walker.x + 0.4):
            ex.walker.x = x
            cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
            screen = self.pg.Surface((1920, 1080))
            view.draw(screen, cam, ex.walker, ex.encounters(), 0.0)
            shots.append(self.pg.image.tobytes(screen, "RGB"))
        self.assertEqual(shots[0], shots[1])

    def test_the_whole_view_of_a_dark_map_is_dark_but_round_the_walker(self):
        explore.forget_areas()
        ex = explore.Explorer(stand_in("sample_dungeon", *mid(16, 11)))
        view = self.mv.MapView(ex.area)
        cam = explore.Camera(4096, 2160, 9)
        cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        lit = self.pg.Surface((4096, 2160))
        view.draw(lit, cam, ex.walker, [], 0.0)
        view.lit = False                                # Round RPG15: the Light setting off = the plain art (expected change)
        try:
            plain = self.pg.Surface((4096, 2160))
            view.draw(plain, cam, ex.walker, [], 0.0)
        finally:
            view.lit = True
        self.assertTrue(ex.area.width * 9 >= 4096 and ex.area.height * 9 >= 2160)     # the map fills the whole view
        reach = (ex.area.light + 1) * TILE
        checked = set()
        for sx in range(10, 4096, 120):
            for sy in range(10, 2160, 120):
                mx, my = cam.to_map(sx, sy)
                bright = sum(tuple(plain.get_at((sx, sy)))[:3])
                if bright > 150 and ((mx - ex.walker.x) ** 2 + (my - ex.walker.y) ** 2) ** 0.5 > reach:
                    self.assertLess(sum(tuple(lit.get_at((sx, sy)))[:3]), bright / 3, (sx, sy))
                    checked.add((sx < 2048, sy < 1080))
        self.assertEqual(len(checked), 4)                                # dark in every quarter of the view
        near = cam.to_screen(ex.walker.x + 8, ex.walker.y - 12)
        self.assertEqual(lit.get_at(near), plain.get_at(near))

    def test_without_the_art_the_old_shapes_are_drawn(self):
        explore.forget_areas()
        ex = explore.Explorer(stand_in("sample_region", *mid(28, 8)))
        view = self.mv.MapView(ex.area)
        view.sprites = self.mv.Sprites(art_file=os.path.join(self.tmp, "none.json"))
        self.assertIsNone(view.sprites.walker("down", 0, False, TILE, 2))
        screen = self.pg.Surface((640, 480))
        cam = explore.Camera(640, 480, 2)
        cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        view.draw(screen, cam, None, [], 0.0)
        bare = screen.copy()
        view.draw(screen, cam, ex.walker, ex.encounters(), 0.0)
        wx, wy = cam.to_screen(ex.walker.x, ex.walker.y)
        self.assertNotEqual(screen.get_at((wx, wy - 2)), bare.get_at((wx, wy - 2)))
        gone = self.mv.Sprites(art_dir=os.path.join(self.tmp, "nowhere"))
        self.assertIsNone(gone.walker("down", 0, False, TILE, 2))
        self.assertIsNone(gone.foe("grave_robber", 0.0, 2))

    def test_through_an_exit_you_face_the_way_you_were_going(self):
        explore.forget_areas()
        ex = explore.Explorer(stand_in("sample_region", None, None))
        door = next(t for t in ex.area.triggers if t.kind == "exit")
        ex.walker.facing = "up"
        self.assertEqual(ex._fire([door])[0], "map")
        self.assertNotEqual(ex.area.name, "sample_region")
        self.assertEqual((ex.walker.facing, ex.save.world["facing"]), ("up", "up"))


# ---- rpg/data/art.json --------------------------------------------------------------------------------------------------------

class ArtTests(unittest.TestCase):
    def setUp(self):
        self.pg = pg()
        with open(ART, encoding="utf-8") as f:
            self.art = json.load(f)

    def filled(self, sheet, col, row):
        """Any pixel of the cell drawn (the sheets are read with convert_alpha: their transparent colour is alpha 0)."""
        if (col + 1) * 16 > sheet.get_width() or (row + 1) * 16 > sheet.get_height():
            return False
        return any(sheet.get_at((col * 16 + i, row * 16 + j))[3] > 0 for i in range(16) for j in range(16))

    def test_every_enemy_kind_has_a_sprite_in_both_of_its_sheets(self):
        kinds = {k["id"] for k in enemies.read_kinds()}
        self.assertEqual(set(self.art["kinds"]), kinds)
        for kind, k in self.art["kinds"].items():
            for half in (0, 1):
                with self.subTest(kind=kind, half=half):
                    sheet = self.pg.image.load(os.path.join(DAWNLIKE, *f"{k['sheet']}{half}.png".split("/"))).convert_alpha()
                    self.assertTrue(self.filled(sheet, *k["cell"]), (kind, k))

    def test_the_walker_sheet_has_four_rows_of_four_frames(self):
        w = self.art["walker"]
        self.assertEqual(sorted(w["rows"]), sorted(explore.FACINGS))
        self.assertEqual(sorted(w["rows"].values()), [0, 1, 2, 3])
        self.assertEqual(w["frames"], 4)
        sheet = self.pg.image.load(os.path.join(DAWNLIKE, *w["sheet"].split("/"))).convert_alpha()
        self.assertEqual(sheet.get_size(), (64, 64))
        for row in range(4):
            for frame in range(4):
                self.assertTrue(self.filled(sheet, frame, row), (row, frame))
        for cls in self.art["classes"]:
            self.assertTrue(os.path.exists(os.path.join(DAWNLIKE, "Commissions", cls + ".png")), cls)

    def test_the_pictures_are_the_packs_own_and_credited(self):
        with open(os.path.join(BASE, "assets", "rpg", "MANIFEST.sha256"), encoding="utf-8") as f:
            listed = {ln.split("  ", 1)[1].strip() for ln in f if ln.strip() and not ln.startswith("#")}
        used = {"dawnlike/" + self.art["walker"]["sheet"]}
        used |= {f"dawnlike/{k['sheet']}{h}.png" for k in self.art["kinds"].values() for h in (0, 1)}
        self.assertEqual(used - listed, set())


# ---- the hero ----------------------------------------------------------------------------------------------------------------

class HeroTests(unittest.TestCase):
    def test_the_heroes_are_the_art_files_classes(self):
        from rpg import save as sv
        with open(ART, encoding="utf-8") as f:
            self.assertEqual(tuple(json.load(f)["classes"]), sv.HEROES)

    def test_a_new_game_keeps_the_hero_and_a_bad_one_is_refused(self):
        from rpg import newgame
        from rpg import save as sv
        from tests import test_rpg4 as r4
        flow = newgame.NewGameFlow(seed=3, pool=r4.POOL)
        self.assertEqual(flow.hero, "Warrior")
        self.assertIn("isn't one of the heroes", flow.set_hero("Bard"))
        self.assertEqual(flow.hero, "Warrior")
        self.assertIsNone(flow.set_hero("Mage"))
        flow.set_name("Karl")
        for _ in range(newgame.PICKS):
            flow.pick(flow.offer()[0].code)
        flow.undo()                                                    # back a step keeps the hero
        flow.pick(flow.offer()[0].code)
        s = flow.finish(1)
        self.assertEqual(s.world["hero"], "Mage")
        tmp = tempfile.mkdtemp(prefix="rpg11_save_")
        self.addCleanup(shutil.rmtree, tmp, True)
        sv.write(s, tmp)
        self.assertEqual(sv.read(1, tmp, pool=r4.POOL)[0].world["hero"], "Mage")
        bad = tempfile.mkdtemp(prefix="rpg11_save_")
        self.addCleanup(shutil.rmtree, bad, True)
        s.world["hero"] = "Bard"
        sv.write(s, bad)
        with self.assertRaises(sv.SaveError):
            sv.read(1, bad, pool=r4.POOL)

    def test_each_hero_walks_with_its_own_sheet_and_an_old_save_is_the_warrior(self):
        pygame = pg()
        import rpg_map_view
        sprites = rpg_map_view.Sprites()
        for hero in ("Warrior", "Paladin", "Rogue", "Mage", "Engineer"):
            sheet = pygame.image.load(os.path.join(DAWNLIKE, "Commissions", hero + ".png")).convert_alpha()
            img = sprites.walker("left", 8, True, TILE, 1, hero)
            row, frame = sprites.walker_frame("left", 8, True, TILE)
            self.assertEqual([tuple(img.get_at((i, j))) for i in range(16) for j in range(16)],
                             [tuple(sheet.get_at((frame * 16 + i, row * 16 + j))) for i in range(16) for j in range(16)], hero)
        self.assertEqual(sprites.walker_sheet(None), "Commissions/Warrior.png")
        self.assertEqual(sprites.walker_sheet("Bard"), "Commissions/Warrior.png")


# ---- the Adventure's map screen -----------------------------------------------------------------------------------------------

class ScreenTests(unittest.TestCase):
    """The map in the Adventure window: the map fills it, a corner window says where you are, the keys and Menu at the bottom."""

    def test_the_corner_window_the_keys_and_menu_fit_and_never_overlap(self):
        from tests import test_rpg8 as r8
        import gfx
        import rpg_screens as rs
        pygame = pg()
        base = r8.Base("runTest")
        real = rs.draw_text

        def words(g):
            drawn = []

            def spy(screen, s, x, y, font, *a, **k):
                r = real(screen, s, x, y, font, *a, **k)
                drawn.append((s, pygame.Rect(r)))
                return r
            with mock.patch.object(rs, "draw_text", spy):
                r8.frame(g)
            return drawn
        base.setUp()
        self.addCleanup(base.doCleanups)
        r8.new_game(None)
        for size, scale in r8.SIZES:
            with self.subTest(size=size, scale=scale):
                g = base.gui(size, scale)
                f = base.open(g, slot=1)
                if f.stage != "map":
                    base.press(g, "leave_town")
                self.assertEqual(f.stage, "map")
                drawn = words(g)
                W, H = g.screen.get_size()
                whole = pygame.Rect(0, 0, W, H)
                hud = f.hud
                self.assertIsNotNone(hud)
                self.assertTrue(whole.contains(hud), hud)
                self.assertLessEqual(hud.w, W // 2)
                menu = next(r for r, n in f.buttons if n == "pause")
                self.assertTrue(whole.contains(menu))
                self.assertFalse(hud.colliderect(menu))
                keys = [r for s, r in drawn if s == "   ".join(rs.MAP_KEYS)]
                self.assertEqual(len(keys), 1, [s for s, _r in drawn])          # the keys, whole, on one line
                self.assertTrue(whole.contains(keys[0]))
                self.assertFalse(keys[0].colliderect(menu), (keys[0], menu))
                self.assertFalse(keys[0].colliderect(hud))
                # Round RPG14: the corner window draws its lines in the pixel font (rpg_ui.text), not draw_text, and lists them
                inside = [s for s, r in drawn if hud.contains(r)] + [s for s, r in f.hud_words if hud.contains(r)]
                self.assertIn(f.explorer.area.title, inside)
                self.assertTrue(any(s.startswith("Gold") for s in inside), inside)
                for i, (a, ra) in enumerate(drawn):
                    for b, rb in drawn[i + 1:]:
                        self.assertFalse(ra.inflate(-2, -2).colliderect(rb.inflate(-2, -2)), f"{a!r} {ra} / {b!r} {rb}")
                ws = f.camera.to_screen(f.explorer.walker.x, f.explorer.walker.y)
                self.assertTrue(whole.collidepoint(ws))
                self.assertNotEqual(tuple(g.screen.get_at((W // 2, H // 2)))[:3], gfx.BACKDROP)
                base.close(g)

    def test_the_hero_is_picked_on_the_name_screen_and_walks_the_map(self):
        from tests import test_rpg8 as r8
        pygame = pg()
        base = r8.Base("runTest")
        base.setUp()
        self.addCleanup(base.doCleanups)
        for size, scale in r8.SIZES:
            with self.subTest(size=size, scale=scale):
                g = base.gui(size, scale)
                f = base.open(g)
                base.press(g, "new:1")
                if f.sub == "confirm":                                  # the last size's game is in slot 1
                    base.press(g, "yes")
                self.assertEqual(f.stage, "name")
                heroes = [(r, n) for r, n in f.buttons if n.startswith("hero:")]
                self.assertEqual([n for _r, n in heroes], ["hero:" + h for h in ("Warrior", "Paladin", "Rogue", "Mage", "Engineer")])
                others = [r for r, n in f.buttons if not n.startswith("hero:")]
                whole = pygame.Rect(0, 0, *g.screen.get_size())
                for i, (r, n) in enumerate(heroes):
                    self.assertTrue(whole.contains(r), (n, r))
                    self.assertFalse(any(r.colliderect(o) for o in others), (n, r))
                    self.assertFalse(any(r.colliderect(o) for o, _m in heroes[i + 1:]), (n, r))
                    self.assertGreaterEqual(r.w, 24)
                base.press(g, "hero:Rogue")
                self.assertEqual(f.flow.hero, "Rogue")
                r8.frame(g)
                for ch in "Karl":
                    r8.key(g, getattr(pygame, "K_" + ch.lower()), unicode=ch)
                r8.key(g, pygame.K_RETURN)
                for _ in range(3):
                    r8.click(g, next(r for r, k, d in f.rows if k == "pick").center)
                base.press(g, "begin")
                self.assertEqual(f.game.save.world["hero"], "Rogue")
                base.press(g, "ok")
                base.press(g, "leave_town")
                self.assertEqual(f.stage, "map")
                r8.frame(g)
                self.assertEqual(f.views[f.explorer.area.name].hero, "Rogue")
                base.close(g)


if __name__ == "__main__":
    unittest.main()
