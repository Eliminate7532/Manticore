# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG5 (phase 3 of the RPG plan, part 2: the map engine, no game screen yet): the Tiled map reader (rpg.tmx), walking
(rpg.explore: walls, sliding, the map's edges, what you walk into, the camera, the save's place), the placeholder maps, the pygame
drawing (rpg_map_view) and the developer's window (tools/rpg_walk.py).

Small maps are written to a temp folder by the tests, so each says exactly what a rule does; the "Shipped" tests read rpg/maps/.
"""
import ast
import base64
import gzip
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zlib

from rpg import duel, explore, tmx
from rpg import save as sv
from tests import rpg_route as route
from tests import test_rpg4 as r4

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(BASE, "rpg", "maps")


# ---- writing small maps -------------------------------------------------------------------------------------------------------

TSX = """<?xml version="1.0" encoding="UTF-8"?>
<tileset name="t" tilewidth="16" tileheight="16" tilecount="4" columns="2">
 <image source="t.png" width="32" height="32"/>
 <tile id="1"><properties><property name="solid" type="bool" value="true"/></properties></tile>
 <tile id="3"><properties><property name="collides" type="bool" value="true"/><property name="name" value="rock"/></properties></tile>
</tileset>
"""


def pack(gids, comp=None):
    raw = b"".join(struct.pack("<I", g) for g in gids)
    if comp == "zlib":
        raw = zlib.compress(raw)
    elif comp == "gzip":
        raw = gzip.compress(raw, mtime=0)
    return base64.b64encode(raw).decode("ascii")


def layer_xml(name, w, h, gids, encoding="csv", comp=None, props=""):
    if encoding == "csv":
        data = f'<data encoding="csv">{",".join(str(g) for g in gids)}</data>'
    else:
        c = f' compression="{comp}"' if comp else ""
        data = f'<data encoding="base64"{c}>{pack(gids, comp)}</data>'
    return f'<layer name="{name}" width="{w}" height="{h}">{props}{data}</layer>'


def write_map(folder, name, w, h, layers, objects="", tileset='<tileset firstgid="1" source="t.tsx"/>', head="",
              map_props=""):
    with open(os.path.join(folder, "t.tsx"), "w", encoding="utf-8") as f:
        f.write(TSX)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<map version="1.10" orientation="orthogonal" width="{w}" height="{h}" '
           f'tilewidth="16" tileheight="16" infinite="0"{head}>{map_props}{tileset}{"".join(layers)}'
           f'<objectgroup name="o">{objects}</objectgroup></map>')
    path = os.path.join(folder, name + ".tmx")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    return path


def obj(i, cls, x, y, w=None, h=None, name="", props=None, attr="class"):
    p = ""
    if props:
        p = "<properties>" + "".join(
            f'<property name="{k}"{"" if t == "string" else f" type={chr(34)}{t}{chr(34)}"} value="{v}"/>'
            for k, (t, v) in props.items()) + "</properties>"
    size = "" if w is None else f' width="{w}" height="{h}"'
    point = "<point/>" if w is None else ""
    return f'<object id="{i}" name="{name}" {attr}="{cls}" x="{x}" y="{y}"{size}>{p}{point}</object>'


def room(w=8, h=6, walls=()):
    """An open floor (gid 1) with a border of walls (gid 2, solid) and more walls at `walls` (col, row)."""
    g = []
    for r in range(h):
        for c in range(w):
            edge = r in (0, h - 1) or c in (0, w - 1)
            g.append(2 if edge or (c, r) in walls else 1)
    return g


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rpg5_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        explore.forget_areas()
        self.addCleanup(explore.forget_areas)


# ---- the reader -------------------------------------------------------------------------------------------------------------

class ReaderTests(Temp):
    GIDS = [1, 2, 3, 4, 0, 1 | tmx.FLIP_H, 2 | tmx.FLIP_V | tmx.FLIP_D, 4 | tmx.ROTATE_HEX]

    def test_every_encoding_reads_the_same_tiles_and_keeps_the_flips_apart(self):
        plain = [g & tmx.GID_MASK for g in self.GIDS]
        for enc, comp in (("csv", None), ("base64", None), ("base64", "zlib"), ("base64", "gzip")):
            with self.subTest(enc=enc, comp=comp):
                p = write_map(self.tmp, "m", 4, 2, [layer_xml("ground", 4, 2, self.GIDS, enc, comp)])
                m = tmx.read_map(p)
                self.assertEqual(m.layers[0].data, plain)
                self.assertEqual(m.layers[0].flips, {5: tmx.FLIP_H, 6: tmx.FLIP_V | tmx.FLIP_D, 7: tmx.ROTATE_HEX})
                self.assertEqual(m.layers[0].tile(1, 1), 1)

    def test_the_tileset_beside_the_map_and_one_inside_it(self):
        p = write_map(self.tmp, "m", 2, 1, [layer_xml("g", 2, 1, [1, 4])])
        m = tmx.read_map(p)
        ts = m.tilesets[0]
        self.assertEqual((ts.tilewidth, ts.columns, ts.tilecount, ts.source.endswith("t.tsx")), (16, 2, 4, True))
        self.assertEqual(os.path.normpath(ts.image), os.path.join(self.tmp, "t.png"))
        self.assertEqual(m.tile_props(2), {"solid": True})
        self.assertEqual(m.tile_props(4), {"collides": True, "name": "rock"})
        self.assertEqual(ts.rect(3), (16, 16, 16, 16))
        inside = ('<tileset firstgid="1" name="in" tilewidth="16" tileheight="16" tilecount="4" columns="2" margin="1" '
                  'spacing="2"><image source="sub/t.png" width="36" height="36"/>'
                  '<tile id="0"><properties><property name="solid" type="bool" value="true"/></properties></tile></tileset>')
        p = write_map(self.tmp, "m2", 2, 1, [layer_xml("g", 2, 1, [1, 4])], tileset=inside)
        m = tmx.read_map(p)
        self.assertIsNone(m.tilesets[0].source)
        self.assertEqual(m.tilesets[0].rect(3), (19, 19, 16, 16))               # margin 1 + spacing 2
        self.assertEqual(m.tile_props(1), {"solid": True})
        self.assertTrue(m.tilesets[0].image.endswith(os.path.join("sub", "t.png")))

    def test_objects_their_kinds_and_typed_properties(self):
        objs = (obj(1, "spawn", 24, 24, name="start")
                + obj(2, "encounter", 32, 16, 16, 16, "Rat", {"kind": ("string", "rat"), "once": ("bool", "true")})
                + obj(3, "exit", 0, 0, 16, 32, attr="type", props={"map": ("string", "b"), "n": ("int", "3"),
                                                                    "f": ("float", "0.5"), "c": ("color", "#ff112233"),
                                                                    "file": ("file", "x.png"), "o": ("object", "2")}))
        p = write_map(self.tmp, "m", 4, 3, [layer_xml("g", 4, 3, [1] * 12)], objs,
                      map_props='<properties><property name="region" type="int" value="2"/></properties>')
        m = tmx.read_map(p)
        self.assertEqual(m.props, {"region": 2})
        a, b, c = m.objects
        self.assertTrue(a.is_point)
        self.assertEqual((a.kind, a.name, a.x, a.y), ("spawn", "start", 24.0, 24.0))
        self.assertEqual((b.kind, b.props), ("encounter", {"kind": "rat", "once": True}))
        self.assertEqual(c.kind, "exit")                                          # "type" from older Tiled files
        self.assertEqual(c.props, {"map": "b", "n": 3, "f": 0.5, "c": "#ff112233", "file": "x.png", "o": 2})
        self.assertEqual(m.pixel_size, (64, 48))

    def test_what_isnt_read_is_refused_in_plain_words(self):
        good = layer_xml("g", 2, 1, [1, 1])
        cases = [
            ("zstd", dict(layers=[layer_xml("g", 2, 1, [1, 1], "base64").replace('encoding="base64"',
                                                                                 'encoding="base64" compression="zstd"')]),
             "zstd"),
            ("infinite", dict(layers=[good], head="").copy(), "infinite"),
            ("isometric", dict(layers=[good]), "isometric"),
            ("chunks", dict(layers=['<layer name="g" width="2" height="1"><data encoding="csv"><chunk x="0" y="0" '
                                    'width="2" height="1">1,1</chunk></data></layer>']), "chunks"),
            ("count", dict(layers=[layer_xml("g", 2, 1, [1, 1, 1])]), "has 3 tiles"),
            ("gid", dict(layers=[layer_xml("g", 2, 1, [1, 9])]), "uses tile 9"),
            ("damaged", dict(layers=[layer_xml("g", 2, 1, [1, 1], "base64").replace("<data encoding=\"base64\">",
                                                                                    "<data encoding=\"base64\" compression=\"zlib\">")]),
             "damaged"),
            ("bool", dict(layers=[good], map_props='<properties><property name="x" type="bool" value="maybe"/></properties>'),
             "maybe"),                       # Round RPG7: PyTMX reads true/false, 1/0 and yes/no, and refuses the rest
            ("overlap", dict(layers=[good], tileset='<tileset firstgid="1" source="t.tsx"/><tileset firstgid="3" source="t.tsx"/>'),
             "overlaps"),
            ("image", dict(layers=[good], tileset='<tileset firstgid="1" name="n" tilewidth="16" tileheight="16"></tileset>'),
             "no <image>"),
        ]
        for name, kw, words in cases:
            with self.subTest(name):
                p = write_map(self.tmp, "bad", 2, 1, **kw)
                if name in ("infinite", "isometric"):
                    with open(p, encoding="utf-8") as f:
                        s = f.read()
                    s = s.replace('infinite="0"', 'infinite="1"') if name == "infinite" else s.replace('"orthogonal"',
                                                                                                     '"isometric"')
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(s)
                with self.assertRaises(tmx.TmxError) as cm:
                    tmx.read_map(p)
                self.assertIn(words, str(cm.exception))
                self.assertIn("bad.tmx", str(cm.exception))
        with self.assertRaises(tmx.TmxError):
            tmx.read_map(os.path.join(self.tmp, "nowhere.tmx"))
        notmap = os.path.join(self.tmp, "x.tmx")
        with open(notmap, "w") as f:
            f.write("<tileset/>")
        with self.assertRaises(tmx.TmxError) as cm:
            tmx.read_map(notmap)
        self.assertIn("not a Tiled map", str(cm.exception))


# ---- walls, walking and what you walk into ------------------------------------------------------------------------------------

def area_of(folder, gids, w, h, objects="", layers=None, name="m"):
    write_map(folder, name, w, h, layers or [layer_xml("ground", w, h, gids)], objects)
    return explore.load_area(name, folder)


class AreaTests(Temp):
    def test_walls_come_from_tile_properties_a_collision_layer_and_the_edges(self):
        g = [1, 2, 3, 4, 1, 1]                           # gid 2 solid, gid 4 collides
        a = area_of(self.tmp, g, 3, 2)
        self.assertEqual([a.blocked(c, r) for r in range(2) for c in range(3)], [False, True, False, True, False, False])
        self.assertTrue(a.blocked(-1, 0) and a.blocked(3, 0) and a.blocked(0, 2))
        explore.forget_areas()
        layers = [layer_xml("ground", 3, 2, [1] * 6), layer_xml("Collision", 3, 2, [0, 0, 1, 0, 0, 0])]
        a = area_of(self.tmp, None, 3, 2, layers=layers)
        self.assertEqual([c for c in range(3) if a.blocked(c, 0)], [2])
        explore.forget_areas()
        layers = [layer_xml("walls", 3, 2, [0, 1, 0, 0, 0, 0],
                            props='<properties><property name="collision" type="bool" value="true"/></properties>')]
        a = area_of(self.tmp, None, 3, 2, layers=layers)
        self.assertEqual([c for c in range(3) if a.blocked(c, 0)], [1])

    def test_spawns_triggers_and_problems(self):
        objs = (obj(1, "spawn", 40, 40, name="start") + obj(2, "spawn", 56, 40, name="start")
                + obj(3, "encounter", 72, 40, name="rat", props={"kind": ("string", "rat")})
                + obj(4, "town", 48, 64, 16, 16, props={"town": ("string", "t1")}))
        a = area_of(self.tmp, room(), 8, 6, objs)
        self.assertEqual(a.spawn(), (56.0, 40.0))
        t = a.triggers[0]
        self.assertEqual((t.kind, t.enemy, t.encounter, t.once, t.spot, t.box), ("encounter", "rat", "regular", False, "m#3",
                                                                                (64.0, 32.0, 80.0, 48.0)))
        problems = a.check(kinds={"rat"}, towns={"t1"})
        self.assertIn("two spawns are called 'start'", problems)
        self.assertIn("town 4 (no name): the map has no spawn named 't1' (where you stand outside it)", problems)

    def test_the_checks_catch_bad_maps(self):
        objs = (obj(1, "spawn", 24, 24, name="start") + obj(2, "spawn", 8, 8, name="inwall")
                + obj(3, "encounter", 96, 48, 16, 16, "far", {"kind": ("string", "ghost"), "encounter": ("string", "picnic")})
                + obj(4, "exit", 32, 32, 16, 16, "door", {"map": ("string", "nowhere"), "spawn": ("string", "x")})
                + obj(5, "town", 48, 16, 16, 16, "gate", {"town": ("string", "atlantis")})
                + obj(6, "spawn", 104, 24, name="atlantis"))
        walls = {(5, 1), (5, 2), (5, 3), (5, 4)}                     # a wall from top to bottom: the right side is cut off
        a = area_of(self.tmp, room(8, 6, walls), 8, 6, objs)
        p = a.check(kinds={"rat"}, towns={"t1"}, maps={"m"}, spawns_of=lambda m: set())
        joined = "\n".join(p)
        for words in ("spawn 'inwall' is in a wall", "no enemy kind 'ghost'", "encounter 'picnic'", "no map 'nowhere'",
                      "no town 'atlantis'", "encounter 3 (far) can't be reached", "spawn 'atlantis' can't be reached"):
            self.assertIn(words, joined)
        self.assertNotIn("exit 4 (door) can't be reached", joined)


class WalkerTests(Temp):
    def setUp(self):
        super().setUp()
        self.a = area_of(self.tmp, room(8, 6, {(4, 2)}), 8, 6)       # walls round the edge and one at (4, 2)

    def test_a_wall_stops_the_walker_flush_against_it(self):
        w = explore.Walker(self.a, 24, 40)                            # row 2, left of the wall at x 64
        w.move(1, 0, 2.0)
        self.assertAlmostEqual(w.box()[2], 64.0)
        self.assertFalse(self.a.box_blocked(w.box()))
        w.move(-1, 0, 5.0)
        self.assertAlmostEqual(w.box()[0], 16.0)                      # the border wall
        w.move(0, -1, 5.0)
        self.assertAlmostEqual(w.box()[1], 16.0)
        w.move(0, 1, 5.0)
        self.assertAlmostEqual(w.box()[3], 80.0)

    def test_the_maps_edges_stop_the_walker_on_a_map_with_no_walls(self):
        a = area_of(self.tmp, [1] * 60, 10, 6, name="open")
        w = explore.Walker(a, 80, 48)
        for (dx, dy), edge in (((-1, 0), (0, 0.0)), ((0, -1), (1, 0.0)), ((1, 0), (2, 160.0)), ((0, 1), (3, 96.0))):
            w.move(dx, dy, 10.0)
            self.assertAlmostEqual(w.box()[edge[0]], edge[1])

    def test_walking_diagonally_into_a_wall_slides_along_it(self):
        w = explore.Walker(self.a, 56, 24)                            # row 1, just left of x 64, the wall is at row 2
        x0 = w.x
        w.move(1, 1, 0.5)                                             # right and down: the wall stops x only while beside it
        self.assertGreater(w.y, 24)
        self.assertFalse(self.a.box_blocked(w.box()))
        w2 = explore.Walker(self.a, 40, 24)
        w2.move(0, -1, 3.0)
        w2.move(1, -1, 0.2)                                           # up into the top wall and right: slides right
        self.assertAlmostEqual(w2.box()[1], 16.0)
        self.assertGreater(w2.x, 40)
        self.assertGreaterEqual(w.x, x0)

    def test_a_diagonal_is_as_fast_as_a_straight_line_and_a_long_frame_doesnt_go_through_walls(self):
        big = area_of(self.tmp, [1] * 400, 20, 20, name="big")
        w = explore.Walker(big, 160, 160)
        w.move(1, 1, 0.5)
        moved = ((w.x - 160) ** 2 + (w.y - 160) ** 2) ** 0.5
        self.assertAlmostEqual(moved, w.speed * 0.5, places=3)
        self.assertEqual(w.facing, "right")
        w = explore.Walker(self.a, 24, 40)
        w.move(1, 0, 10.0)                                            # ten seconds in one frame: still stops at the wall
        self.assertLessEqual(w.box()[2], 64.0 + 1e-6)

    def test_triggers_fire_once_on_the_way_in_never_where_you_were_put_and_skip_whats_gone(self):
        objs = (obj(1, "encounter", 64, 64, 16, 16, "rat", {"kind": ("string", "rat")})
                + obj(2, "town", 32, 64, 16, 16, "gate", {"town": ("string", "t1")}))
        a = area_of(self.tmp, room(8, 6), 8, 6, objs, name="trig")
        w = explore.Walker(a, 40, 72)                                 # put down inside the town gate: nothing fires
        self.assertEqual({t.spot for t in a.triggers if explore.overlaps(w.box(), t.box)}, {"trig#2"})
        self.assertEqual(w.move(0, 0, 1.0), [])
        fired = w.move(1, 0, 1.0)
        self.assertEqual([t.spot for t in fired], ["trig#1"])       # stops where it happened
        self.assertLess(w.box()[0], 64)
        here = (w.x, w.y)
        self.assertEqual(w.move(1, 0, 0.01), [])                      # still inside: not again
        self.assertNotEqual((w.x, w.y), here)
        fired = w.move(-1, 0, 0.6)
        self.assertEqual([t.kind for t in fired], ["town"])           # back into the gate after leaving it
        self.assertEqual(w.move(-1, 0, 0.1), [])
        self.assertEqual([t.spot for t in w.move(1, 0, 1.5, skip={"trig#1"})], [])   # a beaten foe doesn't fire
        self.assertGreater(w.box()[0], 80)                           # walked right through it

    def test_the_resting_place_is_whole_pixels_and_never_in_a_wall(self):
        w = explore.Walker(self.a, 24, 40)
        w.hw = 4.5                                                    # flush against the wall at x 64 is x 59.5 ...
        w.move(1, 0, 2.0)
        self.assertEqual(w.x, 59.5)
        self.assertTrue(self.a.box_blocked(w.box(round(w.x), w.y)))  # ... which round() would put 0.5 px into it
        x, y = w.resting_place()
        self.assertEqual((x, y), (59, 40))
        self.assertIsInstance(x, int)
        self.assertFalse(self.a.box_blocked(w.box(x, y)))


class CameraTests(unittest.TestCase):
    def test_follows_clamps_and_centres_a_small_map(self):
        c = explore.Camera(320, 240, zoom=2)                         # shows 160 x 120 map pixels
        c.follow(400, 300, 800, 600)
        self.assertEqual((c.x, c.y), (320.0, 240.0))
        self.assertEqual(c.to_screen(400, 300), (160, 120))
        c.follow(10, 10, 800, 600)
        self.assertEqual((c.x, c.y), (0.0, 0.0))
        c.follow(799, 599, 800, 600)
        self.assertEqual((c.x, c.y), (640.0, 480.0))
        c.follow(50, 50, 100, 80)                                     # smaller than the screen: centred
        self.assertEqual((c.x, c.y), (-30.0, -20.0))
        self.assertEqual(c.to_map(*c.to_screen(37, 41)), (37.0, 41.0))

    def test_whole_screen_pixels_and_the_tiles_it_sees(self):
        c = explore.Camera(1280, 800, zoom=3)
        c.follow(333.3, 211.1, 640, 480)
        self.assertEqual(round(c.x * 3), c.x * 3)
        c0, r0, c1, r1 = c.visible(16, 16, 40, 30)
        self.assertLessEqual(c0 * 16, c.x)
        self.assertGreaterEqual(c1 * 16, min(640, c.x + 1280 / 3))
        self.assertGreaterEqual(r1 * 16, min(480, c.y + 800 / 3))
        self.assertEqual(explore.Camera.zoom_for(800, 16), 3)
        self.assertEqual(explore.Camera.zoom_for(1080, 16), 4)
        self.assertEqual(explore.Camera.zoom_for(2160, 16), 9)
        self.assertEqual(explore.Camera.zoom_for(200, 16), 1)


# ---- the explorer and the save ------------------------------------------------------------------------------------------------

def stand_in(where="town_1", x=None, y=None):
    return types.SimpleNamespace(world={"region": 1, "map": where, "x": x, "y": y, "last_town": "town_1",
                                        "towns_visited": ["town_1"]})


class ExplorerTests(unittest.TestCase):
    """On the shipped placeholder maps."""

    def setUp(self):
        explore.forget_areas()

    def test_a_new_game_starts_outside_the_start_town(self):
        s = stand_in()
        ex = explore.Explorer(s, start_town="town_1")
        self.assertEqual(ex.area.name, "sample_region")
        self.assertEqual((ex.walker.x, ex.walker.y), ex.area.spawns["town_1"])
        self.assertEqual((s.world["map"], s.world["x"], s.world["y"]), ("sample_region", 88, 104))    # Round RPG6's map
        none = stand_in(None)
        self.assertEqual(explore.Explorer(none, start_town="town_1").area.name, "sample_region")

    def test_a_saved_place_comes_back_and_a_bad_one_falls_back_to_the_start(self):
        s = stand_in("sample_region", 200, 248)
        ex = explore.Explorer(s)
        self.assertEqual((ex.walker.x, ex.walker.y), (200.0, 248.0))
        s = stand_in("sample_region", 4, 4)                           # in the border trees
        ex = explore.Explorer(s)
        self.assertEqual((ex.walker.x, ex.walker.y), ex.area.spawns["start"])
        with self.assertRaises(explore.MapError):
            explore.Explorer(stand_in("atlantis"), folder=tempfile.gettempdir() + "/rpg5_no_maps_here")

    def test_walking_into_a_town_a_foe_and_through_the_cave_and_back(self):
        """Round RPG6: the walk goes the shortest way round the walls (tests/rpg_route.py), one key at a time, through rooms."""
        s = stand_in()
        ex = explore.Explorer(s, start_town="town_1")
        ev = ex.step(0, -1, 0.3)
        self.assertEqual(ev, ("town", "town_1"))
        ex.leave_town("town_1")
        self.assertEqual((ex.walker.x, ex.walker.y), ex.area.spawns["town_1"])
        robber = next(t for t in ex.area.triggers if t.kind == "encounter" and t.enemy == "grave_robber")
        s.world["beaten"] = [t.spot for t in ex.area.triggers if t.kind == "encounter" and t is not robber]
        foe = ex.foes[robber.spot]
        events, _ = route.walk_to_tile(ex, lambda: ex.area.tile_at(foe.x, foe.y))     # it wanders: chase where it is
        self.assertEqual([(k, t.enemy) for k, t in events], [("encounter", "grave_robber")])
        spot = events[0][1].spot
        self.assertIn(spot, {t.spot for t in ex.encounters()})
        s.world["beaten"].append(spot)
        self.assertNotIn(spot, {t.spot for t in ex.encounters()})
        cave = next(t for t in ex.area.triggers if t.kind == "exit")
        events, _ = route.walk_to_tile(ex, ex.area.tile_at(*cave.box[:2]))            # over a bridge, into the cave's mouth
        self.assertEqual(events[-1], ("map", "sample_dungeon"))
        self.assertEqual((s.world["map"], ex.walker.x, ex.walker.y), ("sample_dungeon",) + ex.area.spawns["entrance"])
        s.world["beaten"] += [t.spot for t in ex.area.triggers if t.kind == "encounter"]
        stairs = next(t for t in ex.area.triggers if t.kind == "exit")
        events, _ = route.walk_to_tile(ex, ex.area.tile_at(*stairs.box[:2]))          # back onto the stairs
        self.assertEqual(events[-1], ("map", "sample_region"))
        self.assertEqual((ex.walker.x, ex.walker.y), ex.area.spawns["dungeon_door"])

    def test_a_won_fight_clears_its_spot_and_a_town_brings_the_regular_foes_back(self):
        tmp = tempfile.mkdtemp(prefix="rpg5_duel_")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "elf_gang.json"), "w", encoding="utf-8") as f:
            json.dump(r4.ENEMY, f)
        for once, key in ((False, "beaten"), (True, "cleared")):
            s = r4.started_save()
            duel.start(s, "elf_gang", "regular", r4.POOL, r4.lists(), folder=tmp, now=5.0, spot="sample_region#6", once=once)
            self.assertEqual((s.pending_duel["spot"], s.pending_duel["once"]), ("sample_region#6", once))
            duel.finish(s, "won", r4.numbers(), r4.world(), r4.POOL, r4.FakeTiers(), r4.lists(), folder=tmp)
            self.assertEqual(s.world.get(key), ["sample_region#6"])
            sv.write(s, tmp)
            back = sv.read(1, tmp, pool=r4.POOL)[0]
            self.assertEqual(back.world[key], ["sample_region#6"])
            duel.visit_town(back, "green_town", r4.world())
            self.assertEqual(back.world.get("beaten"), [])
            self.assertEqual(back.world.get("cleared", []), ["sample_region#6"] if once else [])
        s = r4.started_save()
        duel.start(s, "elf_gang", "regular", r4.POOL, r4.lists(), folder=tmp, now=5.0, spot="sample_region#6")
        duel.finish(s, "lost", r4.numbers(), r4.world(), r4.POOL, r4.FakeTiers(), r4.lists(), folder=tmp)
        self.assertEqual(s.world.get("beaten", []), [])                # a loss clears nothing
        s = r4.started_save()
        duel.start(s, "elf_gang", "regular", r4.POOL, r4.lists(), folder=tmp, now=5.0)
        self.assertNotIn("spot", s.pending_duel)                       # a duel from elsewhere is as before

    def test_a_save_with_a_bad_cleared_list_is_refused(self):
        tmp = tempfile.mkdtemp(prefix="rpg5_save_")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = r4.started_save()
        s.world["cleared"] = ["a#1", 7]
        sv.write(s, tmp)
        with self.assertRaises(sv.SaveError):
            sv.read(1, tmp, pool=r4.POOL)

    def test_after_a_lost_duel_the_walker_is_outside_the_last_town(self):
        s = stand_in("sample_dungeon", 300, 60)
        s.world.update(map="town_1", x=None, y=None)                   # what duel.finish writes for a loss
        ex = explore.Explorer(s, start_town="town_1")
        self.assertEqual((ex.area.name, (ex.walker.x, ex.walker.y)), ("sample_region", ex.area.spawns["town_1"]))


# ---- the shipped maps --------------------------------------------------------------------------------------------------------

class ShippedMapTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_every_map_reads_and_has_nothing_wrong(self):
        with open(os.path.join(BASE, "rpg", "data", "enemy_kinds.json"), encoding="utf-8") as f:
            kinds = {k["id"] for k in json.load(f)["kinds"]}
        with open(os.path.join(BASE, "rpg", "data", "towns.json"), encoding="utf-8") as f:
            towns_data = json.load(f)
        towns = {t["id"] for t in towns_data["towns"]}
        found = explore.check_all(kinds=kinds, towns=towns)
        self.assertEqual(sorted(found), ["sample_dungeon", "sample_region"])
        self.assertEqual(found, {"sample_dungeon": [], "sample_region": []})
        region = explore.load_area("sample_region")
        self.assertIn(towns_data["start_town"], region.towns())
        for name in found:
            a = explore.load_area(name)
            self.assertIn(a.region, [int(r) for r in towns_data["regions"]])
            for t in a.triggers:
                if t.kind == "encounter":
                    self.assertTrue(os.path.isfile(os.path.join(BASE, "rpg", "data", "enemies", t.enemy + ".json")), t.enemy)
        dungeon = explore.load_area("sample_dungeon")
        self.assertEqual([t.encounter for t in dungeon.triggers if t.kind == "encounter"].count("boss"), 1)
        self.assertTrue(all(t.once for t in dungeon.triggers if t.kind == "encounter"))
        self.assertEqual(len({t.enemy for t in region.triggers if t.kind == "encounter"}), 4)

    def test_the_tool_makes_the_same_files_again(self):
        tmp = tempfile.mkdtemp(prefix="rpg5_maps_")
        self.addCleanup(shutil.rmtree, tmp, True)
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "rpg_make_maps.py"), "--out", tmp],
                           capture_output=True, text=True, timeout=120, env=dict(os.environ, SDL_VIDEODRIVER="dummy",
                                                                                  SDL_AUDIODRIVER="dummy"))
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("placeholder_tiles.tsx", "sample_region.tmx", "sample_dungeon.tmx"):
            with open(os.path.join(tmp, name), "rb") as a, open(os.path.join(MAPS, name), "rb") as b:
                self.assertEqual(a.read(), b.read(), name)
        import pygame
        x = pygame.image.load(os.path.join(tmp, "placeholder_tiles.png"))
        y = pygame.image.load(os.path.join(MAPS, "placeholder_tiles.png"))
        self.assertEqual(pygame.image.tobytes(x, "RGB"), pygame.image.tobytes(y, "RGB"))


# ---- drawing ---------------------------------------------------------------------------------------------------------------

class DrawTests(Temp):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        pygame.init()
        cls.pg = pygame
        pygame.display.set_mode((64, 64))

    def test_the_map_the_foes_and_the_walker_are_drawn_at_every_size(self):
        import rpg_map_view
        pygame = self.pg
        explore.forget_areas()
        s = stand_in()
        ex = explore.Explorer(s, start_town="town_1")
        view = rpg_map_view.MapView(ex.area)
        for size in ((1280, 800), (1920, 1080), (4096, 2160), (800, 600)):
            with self.subTest(size=size):
                screen = pygame.Surface(size)
                cam = explore.Camera(*size, explore.Camera.zoom_for(size[1], 16))
                cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
                view.draw(screen, cam, None, [], 0.0)
                bare = screen.copy()
                view.draw(screen, cam, ex.walker, [], 0.0)
                wx, wy = cam.to_screen(ex.walker.x, ex.walker.y)
                self.assertNotEqual(screen.get_at((wx, wy - 2)), bare.get_at((wx, wy - 2)))    # the walker is there
                self.assertEqual(screen.get_at((2, 2)), bare.get_at((2, 2)))
        foe = [t for t in ex.area.triggers if t.kind == "encounter"][0]
        screen = pygame.Surface((1280, 800))
        cam = explore.Camera(1280, 800, 3)
        cam.follow(*((foe.box[0] + foe.box[2]) / 2, (foe.box[1] + foe.box[3]) / 2), ex.area.width, ex.area.height)
        fx, fy = cam.to_screen((foe.box[0] + foe.box[2]) / 2, (foe.box[1] + foe.box[3]) / 2)
        view.draw(screen, cam, None, [foe], 0.0)
        with_foe = tuple(screen.get_at((fx, fy)))
        view.draw(screen, cam, None, [], 0.0)
        self.assertNotEqual(with_foe, tuple(screen.get_at((fx, fy))))   # a beaten foe isn't drawn

    def test_flipped_tiles_are_drawn_flipped(self):
        import rpg_map_view
        pygame = self.pg
        img = pygame.Surface((32, 32))
        img.fill((0, 0, 0))
        img.fill((200, 10, 10), (0, 0, 4, 16))                         # tile 0: a red stripe on its left edge
        pygame.image.save(img, os.path.join(self.tmp, "t.png"))
        gids = [1, 1 | tmx.FLIP_H, 1 | tmx.FLIP_D]
        a = area_of(self.tmp, None, 3, 1, layers=[layer_xml("g", 3, 1, gids)])
        view = rpg_map_view.MapView(a)
        screen = pygame.Surface((48, 16))
        cam = explore.Camera(48, 16, 1)
        cam.follow(24, 8, 48, 16)
        view.draw(screen, cam)
        self.assertEqual(tuple(screen.get_at((1, 8)))[:3], (200, 10, 10))       # plain: red on the left
        self.assertEqual(tuple(screen.get_at((16 + 14, 8)))[:3], (200, 10, 10))  # flipped: red on the right
        self.assertEqual(tuple(screen.get_at((16 + 1, 8)))[:3], (0, 0, 0))
        self.assertEqual(tuple(screen.get_at((32 + 8, 1)))[:3], (200, 10, 10))   # diagonal: red along the top
        self.assertEqual(tuple(screen.get_at((32 + 8, 14)))[:3], (0, 0, 0))

    def test_the_view_has_no_colour_literals(self):
        with open(os.path.join(BASE, "rpg_map_view.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        found = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Tuple) and 3 <= len(n.elts) <= 4 and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, int) for e in n.elts):
                found.append((n.lineno, [e.value for e in n.elts]))
        self.assertEqual(found, [])

    def test_the_package_has_no_pygame(self):
        for name in os.listdir(os.path.join(BASE, "rpg")):
            if name.endswith(".py"):
                with open(os.path.join(BASE, "rpg", name), encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
                self.assertNotIn("pygame", mods, name)

    def test_the_developers_window_walks_and_takes_a_picture(self):
        out = os.path.join(self.tmp, "walk.png")
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "rpg_walk.py"), "--walk", "up 0.3",
                            "--screenshot", out, "--size", "640x400"], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("You reach town_1", r.stdout)                    # Round RPG6's map: the gate is just above
        self.assertEqual(self.pg.image.load(out).get_size(), (640, 400))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rpg_walk", os.path.join(BASE, "tools", "rpg_walk.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.parse_walk("right 1.5, up+left 2"), [((1, 0), 1.5), ((-1, -1), 2.0)])
        with self.assertRaises(ValueError):
            mod.parse_walk("sideways 2")


if __name__ == "__main__":
    unittest.main()
