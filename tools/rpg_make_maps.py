# SPDX-License-Identifier: GPL-3.0-or-later
"""Writes the RPG's placeholder map files (Round RPG5; rooms and the dark palette, Round RPG6): rpg/maps/placeholder_tiles.png and
.tsx (16 tiles of 16 px, drawn here in code), sample_region.tmx (one town, a river, five foes and a cave) and sample_dungeon.tmx
(two foes and a boss, dark but for a light round the walker).

Round RPG6 (Karl, 12 Oct 2026: the look and feel inspired by the original Zelda, in the Diablo 1 / Vermis dark palette): the maps
are whole rooms of 16 x 11 tiles (rpg.explore.ROOM_COLS x ROOM_ROWS) - the region 3 x 2 rooms, the dungeon 2 x 2 - with gaps in
the trees and walls between rooms; the colours are dark and muted. This project's own drawings: nothing of Nintendo's.

They are PLACEHOLDERS for the map engine to run on until the slice's real maps are made in the Tiled editor (mapeditor.org): the
files are ordinary Tiled files, so Tiled opens, edits and saves them. Run again to remake them (the same files every time):

    python tools/rpg_make_maps.py [--out FOLDER]

Colours are this tool's own (a picture it writes), not gfx.py tokens.
"""
import argparse
import os
import sys
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAPS = os.path.join(ROOT, "rpg", "maps")
TILE = 16
COLUMNS = 8

# id: (name, solid, base colour, pattern) - Round RPG6: dark, muted colours (Diablo 1 / Vermis), my choice of values
TILES = [
    ("grass", False, (46, 56, 38), "speckle"),
    ("path", False, (84, 72, 56), "speckle"),
    ("water", True, (26, 38, 52), "waves"),
    ("tree", True, (46, 56, 38), "tree"),
    ("wall", True, (70, 66, 64), "bricks"),
    ("roof", True, (76, 36, 32), "roof"),
    ("gate", False, (84, 72, 56), "gate"),
    ("cave", False, (22, 20, 18), "cave"),
    ("floor", False, (62, 56, 50), "flags"),
    ("dark wall", True, (22, 20, 20), "bricks"),
    ("stairs", False, (62, 56, 50), "stairs"),
    ("bridge", False, (78, 58, 40), "planks"),
    ("flowers", False, (46, 56, 38), "flowers"),
    ("rock", True, (60, 58, 56), "rock"),
    ("rug", False, (70, 24, 26), "rug"),
    ("sand", False, (104, 92, 66), "speckle"),
]
ID = {t[0]: i for i, t in enumerate(TILES)}


def shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def draw_tile(pygame, surf, ox, oy, name, base, pattern):
    """One tile, by pattern; no randomness, so the picture is the same every time."""
    r = pygame.Rect(ox, oy, TILE, TILE)
    surf.fill(base, r)
    dark, light = shade(base, 0.72), shade(base, 1.25)
    if pattern in ("speckle", "flowers", "tree"):
        for y in range(TILE):
            for x in range(TILE):
                if (x * 7 + y * 13) % 11 == 0:
                    surf.set_at((ox + x, oy + y), dark)
                elif (x * 5 + y * 3) % 17 == 0:
                    surf.set_at((ox + x, oy + y), light)
    if pattern == "flowers":
        for (x, y, c) in ((3, 4, (150, 136, 70)), (11, 3, (140, 84, 104)), (7, 10, (160, 156, 150)), (13, 12, (150, 136, 70))):
            pygame.draw.rect(surf, c, (ox + x, oy + y, 2, 2))
    elif pattern == "tree":
        pygame.draw.rect(surf, (52, 38, 28), (ox + 7, oy + 10, 2, 5))
        pygame.draw.circle(surf, (24, 36, 26), (ox + 8, oy + 7), 6)
        pygame.draw.circle(surf, (34, 48, 32), (ox + 7, oy + 6), 3)
    elif pattern == "waves":
        for y in (3, 8, 13):
            for x in range(0, TILE, 4):
                pygame.draw.line(surf, light, (ox + x, oy + y), (ox + x + 2, oy + y - 1))
    elif pattern == "bricks":
        for y in range(0, TILE, 4):
            pygame.draw.line(surf, dark, (ox, oy + y), (ox + TILE - 1, oy + y))
            off = 0 if (y // 4) % 2 == 0 else 4
            for x in range(off, TILE, 8):
                pygame.draw.line(surf, dark, (ox + x, oy + y), (ox + x, oy + y + 3))
    elif pattern == "roof":
        for y in range(0, TILE, 3):
            pygame.draw.line(surf, dark, (ox, oy + y), (ox + TILE - 1, oy + y))
    elif pattern == "gate":
        pygame.draw.rect(surf, (62, 46, 30), (ox + 3, oy + 2, 10, 13))
        pygame.draw.rect(surf, (40, 30, 20), (ox + 3, oy + 2, 10, 13), 1)
        pygame.draw.line(surf, (40, 30, 20), (ox + 8, oy + 2), (ox + 8, oy + 14))
    elif pattern == "cave":
        pygame.draw.rect(surf, (60, 58, 56), r)
        pygame.draw.ellipse(surf, (10, 9, 8), (ox + 2, oy + 3, 12, 14))
    elif pattern == "flags":
        pygame.draw.line(surf, dark, (ox, oy + 7), (ox + TILE - 1, oy + 7))
        pygame.draw.line(surf, dark, (ox + 7, oy), (ox + 7, oy + 7))
        pygame.draw.line(surf, dark, (ox + 12, oy + 8), (ox + 12, oy + TILE - 1))
    elif pattern == "stairs":
        for y in range(2, TILE, 4):
            pygame.draw.rect(surf, light, (ox + 2, oy + y, 12, 2))
    elif pattern == "planks":
        for x in range(0, TILE, 4):
            pygame.draw.line(surf, dark, (ox + x, oy), (ox + x, oy + TILE - 1))
    elif pattern == "rock":
        pygame.draw.polygon(surf, light, [(ox + 2, oy + 13), (ox + 6, oy + 3), (ox + 11, oy + 5), (ox + 14, oy + 13)])
        pygame.draw.polygon(surf, dark, [(ox + 2, oy + 13), (ox + 6, oy + 3), (ox + 11, oy + 5), (ox + 14, oy + 13)], 1)
    elif pattern == "rug":
        pygame.draw.rect(surf, (120, 92, 46), r, 2)


def write_tileset_png(path):
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    pygame.init()
    rows = (len(TILES) + COLUMNS - 1) // COLUMNS
    surf = pygame.Surface((COLUMNS * TILE, rows * TILE))
    for i, (name, _solid, base, pattern) in enumerate(TILES):
        draw_tile(pygame, surf, (i % COLUMNS) * TILE, (i // COLUMNS) * TILE, name, base, pattern)
    pygame.image.save(surf, path)
    return surf.get_size()


def tileset_xml(size):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<tileset version="1.10" tiledversion="1.10.2" name="placeholder_tiles" tilewidth="{TILE}" tileheight="{TILE}" '
             f'tilecount="{len(TILES)}" columns="{COLUMNS}">',
             f' <image source="placeholder_tiles.png" width="{size[0]}" height="{size[1]}"/>']
    for i, (name, solid, _b, _p) in enumerate(TILES):
        lines.append(f' <tile id="{i}">')
        lines.append('  <properties>')
        lines.append(f'   <property name="name" value={quoteattr(name)}/>')
        if solid:
            lines.append('   <property name="solid" type="bool" value="true"/>')
        lines.append('  </properties>')
        lines.append(' </tile>')
    lines.append('</tileset>')
    return "\n".join(lines) + "\n"


class Grid:
    def __init__(self, w, h, fill):
        self.w, self.h = w, h
        self.t = [[ID[fill]] * w for _ in range(h)]

    def put(self, x, y, name):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.t[y][x] = ID[name]

    def rect(self, x0, y0, x1, y1, name):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.put(x, y, name)

    def border(self, name):
        for x in range(self.w):
            self.put(x, 0, name)
            self.put(x, self.h - 1, name)
        for y in range(self.h):
            self.put(0, y, name)
            self.put(self.w - 1, y, name)

    def csv(self):
        return ",\n".join(",".join(str(v + 1) for v in row) for row in self.t)


def map_xml(grid, props, objects):
    """objects: (class, name, tile x, tile y, tile w or None for a point, tile h, {prop: (type, value)})"""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<map version="1.10" tiledversion="1.10.2" orientation="orthogonal" renderorder="right-down" width="{grid.w}" '
             f'height="{grid.h}" tilewidth="{TILE}" tileheight="{TILE}" infinite="0" nextlayerid="3" '
             f'nextobjectid="{len(objects) + 1}">',
             ' <properties>']
    for k, (typ, v) in props.items():
        t = "" if typ == "string" else f' type="{typ}"'
        lines.append(f'  <property name="{k}"{t} value={quoteattr(str(v))}/>')
    lines += [' </properties>',
              ' <tileset firstgid="1" source="placeholder_tiles.tsx"/>',
              f' <layer id="1" name="ground" width="{grid.w}" height="{grid.h}">',
              '  <data encoding="csv">', grid.csv(), '</data>', ' </layer>',
              ' <objectgroup id="2" name="objects">']
    for i, (cls, name, x, y, w, h, oprops) in enumerate(objects, 1):
        if w is None:
            px, py = x * TILE + TILE // 2, y * TILE + TILE // 2
            head = f'  <object id="{i}" name={quoteattr(name)} class="{cls}" x="{px}" y="{py}"'
        else:
            head = (f'  <object id="{i}" name={quoteattr(name)} class="{cls}" x="{x * TILE}" y="{y * TILE}" '
                    f'width="{w * TILE}" height="{h * TILE}"')
        inner = []
        if oprops:
            inner.append('   <properties>')
            for k, (typ, v) in oprops.items():
                t = "" if typ == "string" else f' type="{typ}"'
                val = str(v).lower() if typ == "bool" else str(v)
                inner.append(f'    <property name="{k}"{t} value={quoteattr(val)}/>')
            inner.append('   </properties>')
        if w is None:
            inner.append('   <point/>')
        lines.append(head + ">")
        lines += inner
        lines.append('  </object>')
    lines += [' </objectgroup>', '</map>']
    return "\n".join(lines) + "\n"


ROOM_W, ROOM_H = 16, 11          # rpg.explore.ROOM_COLS, ROOM_ROWS (this tool doesn't import the package)


def room_walls(g, rooms_x, rooms_y, name, gaps_x, gaps_y):
    """Walls (trees, dark walls) round the map and between its rooms, with gaps: gaps_x[(column, row)] = the rows (inside the room
    row) of the gap in the wall on that room's right; gaps_y[(column, row)] = the columns of the gap in the wall below it."""
    g.border(name)
    for rx in range(rooms_x):
        for ry in range(rooms_y):
            x0, y0 = rx * ROOM_W, ry * ROOM_H
            if rx < rooms_x - 1:
                for y in range(y0, y0 + ROOM_H):
                    if y - y0 not in gaps_x.get((rx, ry), ()):
                        g.put(x0 + ROOM_W - 1, y, name)
            if ry < rooms_y - 1:
                for x in range(x0, x0 + ROOM_W):
                    if x - x0 not in gaps_y.get((rx, ry), ()):
                        g.put(x, y0 + ROOM_H - 1, name)


def region():
    """3 x 2 rooms: the town (top left), the river's two rooms with a bridge each, a wood, a meadow and the rocks with the cave."""
    g = Grid(3 * ROOM_W, 2 * ROOM_H, "grass")
    room_walls(g, 3, 2, "tree", {(0, 0): (4, 5, 6), (1, 0): (4, 5, 6), (0, 1): (4, 5, 6), (1, 1): (4, 5, 6)},
               {(0, 0): (6, 7, 8), (1, 0): (2, 3, 4), (2, 0): (6, 7, 8)})
    # room (0, 0): the town - walls, roofs, a gate in the south wall
    g.rect(2, 1, 9, 5, "wall")
    g.rect(3, 2, 8, 4, "roof")
    g.put(5, 5, "gate")
    g.rect(5, 6, 5, 8, "path")
    g.rect(5, 8, 11, 8, "path")
    g.rect(11, 5, 11, 8, "path")
    g.rect(11, 5, 15, 5, "path")
    g.rect(6, 9, 8, 10, "path")
    # the river, two rooms tall, and its bridges
    g.rect(23, 1, 24, 20, "water")
    g.rect(16, 5, 31, 5, "path")
    g.rect(23, 5, 24, 5, "bridge")
    g.rect(16, 16, 31, 16, "path")
    g.rect(23, 16, 24, 16, "bridge")
    # room (2, 0): a wood
    for x, y in ((34, 2), (36, 8), (42, 3), (44, 7), (45, 2), (41, 8), (35, 3), (43, 8)):
        g.put(x, y, "tree")
    g.rect(32, 5, 39, 5, "path")
    g.rect(39, 5, 39, 10, "path")
    # room (0, 1): a meadow; room (1, 1): sand by the river
    for x, y in ((3, 14), (9, 19), (12, 13), (4, 18)):
        g.put(x, y, "flowers")
    g.rect(26, 18, 30, 20, "sand")
    g.put(28, 13, "rock")
    # room (2, 1): rocks round the cave's mouth, which faces south
    g.rect(39, 11, 39, 18, "path")
    g.rect(32, 16, 39, 16, "path")
    g.rect(39, 18, 43, 18, "path")
    g.rect(40, 13, 46, 16, "rock")
    g.put(43, 16, "cave")
    g.put(43, 17, "path")
    objects = [
        ("spawn", "start", 5, 7, None, None, {}),
        ("spawn", "town_1", 5, 6, None, None, {}),
        ("spawn", "dungeon_door", 43, 18, None, None, {}),
        ("town", "Town 1 gate", 5, 5, 1, 1, {"town": ("string", "town_1")}),
        ("exit", "Into the cave", 43, 16, 1, 1, {"map": ("string", "sample_dungeon"), "spawn": ("string", "entrance")}),
        ("encounter", "Grave robber", 19, 8, 1, 1, {"kind": ("string", "grave_robber")}),
        ("encounter", "Ember bandit", 28, 2, 1, 1, {"kind": ("string", "ember_bandit")}),
        ("encounter", "Thornback", 38, 8, 1, 1, {"kind": ("string", "thornback")}),
        ("encounter", "Zealot", 19, 14, 1, 1, {"kind": ("string", "zealot")}),
        ("encounter", "Thornback", 6, 16, 1, 1, {"kind": ("string", "thornback")}),
    ]
    props = {"name": ("string", "The Outskirts (placeholder)"), "region": ("int", 1)}
    return g, props, objects


def dungeon():
    """2 x 2 rooms of dark wall: the entrance (bottom left) with the stairs up, a side room above it, a room to its right and the
    boss's hall above that. Dark but for a light round the walker (map property light)."""
    g = Grid(2 * ROOM_W, 2 * ROOM_H, "dark wall")
    g.rect(2, 13, 13, 19, "floor")         # room (0, 1): the entrance
    g.rect(2, 2, 13, 8, "floor")           # room (0, 0): a side room
    g.rect(18, 13, 29, 19, "floor")        # room (1, 1)
    g.rect(18, 2, 29, 8, "floor")          # room (1, 0): the boss's hall
    g.rect(22, 3, 25, 5, "rug")
    g.rect(14, 15, 17, 16, "floor")        # doors: entrance -> right
    g.rect(7, 9, 8, 12, "floor")           # entrance -> up
    g.rect(23, 9, 24, 12, "floor")         # right room -> the hall
    g.put(2, 19, "stairs")
    objects = [
        ("spawn", "entrance", 4, 17, None, None, {}),
        ("exit", "Back up", 2, 19, 1, 1, {"map": ("string", "sample_region"), "spawn": ("string", "dungeon_door")}),
        ("encounter", "Plague rat", 24, 16, 1, 1, {"kind": ("string", "plague_rat"), "encounter": ("string", "dungeon")}),
        ("encounter", "Plague rat", 8, 5, 1, 1, {"kind": ("string", "plague_rat"), "encounter": ("string", "dungeon")}),
        ("encounter", "The Grave Priest", 23, 4, 1, 1, {"kind": ("string", "grave_priest"), "encounter": ("string", "boss")}),
    ]
    props = {"name": ("string", "The Cave (placeholder)"), "region": ("int", 1), "light": ("float", 4.5)}
    return g, props, objects


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=MAPS)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    size = write_tileset_png(os.path.join(a.out, "placeholder_tiles.png"))
    with open(os.path.join(a.out, "placeholder_tiles.tsx"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tileset_xml(size))
    for name, build in (("sample_region", region), ("sample_dungeon", dungeon)):
        g, props, objects = build()
        with open(os.path.join(a.out, name + ".tmx"), "w", encoding="utf-8", newline="\n") as f:
            f.write(map_xml(g, props, objects))
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
