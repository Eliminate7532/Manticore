# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds the Adventure's maps from text layouts (Round RPG12, 23 Oct 2026; claude/SPEC_RPG_SLICE_2026-10-23.md):

    rpg/maps/src/<map>.txt   ->   rpg/maps/<map>.tmx   (plus the tilesets rpg/maps/dl_*.tsx, written every run)

    python tools/rpg_make_maps.py [--out FOLDER] [--check]

--check builds into a temporary folder and fails when a built file differs from the one in rpg/maps (so a hand edit in Tiled, or a
layout changed without a rebuild, is noticed). Every run writes the same bytes for the same layouts.

The pictures are DawnLike v1.81's (assets/rpg/dawnlike/, CC-BY 4.0, DragonDePlatino; the palette by DawnBringer), unchanged: the
.tsx files point at them. Edit the .txt layouts, not the .tmx files - a run of this tool overwrites the .tmx files. (Karl can open
any .tmx in the Tiled editor to look; to keep a change made there, copy it back into the layout.)

A layout file:

    name: Lantern Hollow          the map's properties (name, region, dungeon, light, backdrop - any key: value line)
    region: 1
    [legend]                      optional: this map's own meaning for a character (otherwise LEGEND below)
    a = floor_town
    [markers]                     one line per marker character: what it is, and the ground it stands on
    1 = spawn start on _
    X = exit map=ashfen_outskirts spawn=from_town on =
    A = npc name=Marta cell=0,9 on w
    [map]
    ...rows of characters, all the same length...

A marker becomes a Tiled object - spawn, exit, town, encounter or npc - whose box covers each connected group of that character
(so a two-tile exit is two marker characters side by side), with the key=value words as its properties; "npc" and "platino" also
put the character's DawnLike sprite (Characters/Humanoid0/1, Reptile0/1) on the "people" layer, which walls them off. "on X" says
which character's terrain lies under it.

Round RPG15 (light): a "light" marker (radius=tiles strength=0-1, flicker=false to hold it still) is a light you don't see the source
of - a lamp in a room - and every brazier (i) and candles (!) tile is a light object too (TERRAINS' "light": radius, strength), one
inside a dark_zone burning only once that zone's flag is set. Light objects come after every other object, so adding lights never
moves the other objects' ids (a save names its beaten foes "<map>#<id>"). The map properties darkness (0-1) and lantern (the
walker's light, in tiles) say how dark the map is.

Round RPG16 (day and night): a marker may say when=<conditions> (rpg.conditions' words, a comma between them: when=phase:night,
!flag:warrens_entered) - it is there only while they hold. A person with `when` is not put on the people layer (rpg_map_view draws
them while they are there); their object keeps the sprite and its cell. An encounter may say spot=<name> (its saved spot is then
"<map>#<name>", whatever its id) and home=<words> (the encounter screen's line; "_" is a space, as in name=). "rare" markers
(G = rare id=old_gorsehide on :) place a rare foe from rpg/data/rare_spawns.json. A [more markers] section (after [markers]) holds
markers whose objects come after every [markers] object and before the lights, so adding them never moves an older object's id.
The map property event_rate (an int: tiles between random events, rpg.events) is written typed.

The layers, in drawing order: ground, water, detail, walls, doors, props, people, above. water, walls, props and people have the
property collision = true (rpg.explore: nothing on them can be walked through); ground, detail, doors and above can.

How the pieces are picked (DawnLike's own sheet layouts, read off the sheets; the example maps place them the same way by hand):
  - floors (Floor.png): each set is 7 x 3 tiles - a 3 x 3 box (corners, edges, middle), a 1-wide vertical strip, a 1-high
    horizontal strip and a single tile; a floor tile's piece follows which of its four neighbours are the same floor (past the
    map's edge counts as the same, so a field runs off the map);
  - walls and fences (Wall.png, Fence.png): each set is 6 x 3 tiles, one piece for each way a wall joins its neighbours
    (the legend drawn in the sheets' top-left corner); a door joins the walls beside it;
  - forests (Tree0.png): a 3 x 3 box like a floor's, and a lone tree where a tree has no tree on two opposite sides;
  - water (Pit0.png): a 2-row set - the top row has the bank's lip, the row below is open water - with a 1-wide column;
  - counters (Decor0.png row 10): a left end, middle and right end;
  - stairs (Tile.png row 3): a picture two tiles wide - a left half and a right half.
"""
import argparse
import os
import re
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAPS = os.path.join(ROOT, "rpg", "maps")
SRC = os.path.join(MAPS, "src")
ART = os.path.join(ROOT, "assets", "rpg", "dawnlike")
TILE = 16
LAYERS = ("ground", "water", "detail", "walls", "doors", "props", "people", "above")
SOLID_LAYERS = ("water", "walls", "props", "people")

# tileset name: (picture under assets/rpg/dawnlike, its frame2 partner or None). Door1 is DawnLike's set of open doors (Door0 the
# shut ones), not a second animation frame. Pit1 moves the water's edges by a few pixels (the shimmer); Tree1 differs from Tree0
# only in two sets this tool doesn't use - both are paired, as the pack means them.
SHEETS = {
    "floor": ("Objects/Floor.png", None),
    "wall": ("Objects/Wall.png", None),
    "tree": ("Objects/Tree0.png", "Objects/Tree1.png"),
    "pit": ("Objects/Pit0.png", "Objects/Pit1.png"),
    "decor": ("Objects/Decor0.png", "Objects/Decor1.png"),
    "door": ("Objects/Door1.png", None),
    "tile": ("Objects/Tile.png", None),
    "ground": ("Objects/Ground0.png", "Objects/Ground1.png"),
    "fence": ("Objects/Fence.png", None),
    "effect": ("Objects/Effect0.png", "Objects/Effect1.png"),
    "humanoid": ("Characters/Humanoid0.png", "Characters/Humanoid1.png"),
    "reptile": ("Characters/Reptile0.png", "Characters/Reptile1.png"),
}
PLATINO = (3, 12)
VOID = (9, 0)                     # Wall.png: the darkest colour of the palette swatches (row 0) - solid rock seen from above                 # Reptile0/1.png: DawnLike's README asks every game that uses it to hide this one

# What a character means. kind: floor (sheet floor, set = the 3 x 3 box's top-left), wall / fence (set = the 6 x 3 block's top-left),
# trees (Tree0's 3 x 3 box top-left; single = the lone tree), water (Pit0's 2-row set: its lip row), tile (one piece of a sheet on
# a layer), counter (Decor0 row 10). base = the floor under it (none: the most common floor around it).
TERRAINS = {
    "floor_town": {"kind": "floor", "set": (7, 6)},          # green grass on brown earth
    "floor_moor": {"kind": "floor", "set": (7, 9)},          # dark grass on brown earth: the Ashfen moor
    "floor_rocks": {"kind": "floor", "set": (14, 9)},        # red-brown stones on earth: the moor's rocky ground
    "floor_road": {"kind": "floor", "set": (0, 21)},         # brown dirt
    "floor_paving": {"kind": "floor", "set": (7, 21)},       # grey flagstones
    "floor_wood": {"kind": "floor", "set": (0, 30)},         # dark planks
    "floor_bridge": {"kind": "floor", "set": (0, 27)},       # light planks
    "floor_crypt": {"kind": "floor", "set": (7, 24)},        # dark flagstones
    "floor_dungeon": {"kind": "floor", "set": (0, 12)},      # dark brick floor
    "floor_cave": {"kind": "floor", "set": (0, 24)},         # dark earth
    "wall_wood": {"kind": "wall", "sheet": "wall", "set": (7, 12), "base": "floor_wood"},
    "wall_stone": {"kind": "wall", "sheet": "wall", "set": (0, 6), "base": "floor_crypt"},
    "wall_dungeon": {"kind": "wall", "sheet": "wall", "set": (0, 12), "base": "floor_dungeon"},
    "fence_wood": {"kind": "wall", "sheet": "fence", "set": (0, 3), "base": "floor_town"},
    "trees_dark": {"kind": "trees", "set": (0, 6), "single": (3, 6)},
    "tree_dead": {"kind": "tile", "sheet": "tree", "cell": (9, 15), "layer": "walls"},
    "tree_gallows": {"kind": "tile", "sheet": "tree", "cell": (9, 3), "layer": "walls"},
    "water_river": {"kind": "water", "set": (0, 14)},        # blue water, stone bank
    "water_bog": {"kind": "water", "set": (0, 16)},          # fen water, a cobbled bank
    "door_wood": {"kind": "door", "cell": (0, 0), "base": "floor_wood"},
    "stairs_wood": {"kind": "pair", "sheet": "tile", "cells": ((4, 3), (5, 3)), "layer": "detail"},   # two tiles wide
    "stairs_stone": {"kind": "pair", "sheet": "tile", "cells": ((6, 3), (7, 3)), "layer": "detail"},
    "counter": {"kind": "counter"},
    "chair": {"kind": "tile", "sheet": "decor", "cell": (0, 7), "layer": "props"},
    "table": {"kind": "tile", "sheet": "decor", "cell": (1, 7), "layer": "props"},
    "shelf": {"kind": "tile", "sheet": "decor", "cell": (1, 4), "layer": "props"},
    "bookcase": {"kind": "tile", "sheet": "decor", "cell": (5, 4), "layer": "props"},
    "bed": {"kind": "tile", "sheet": "decor", "cell": (0, 9), "layer": "props"},
    "crate": {"kind": "tile", "sheet": "decor", "cell": (3, 9), "layer": "props"},
    "barrel": {"kind": "tile", "sheet": "decor", "cell": (4, 9), "layer": "props"},
    "coffin": {"kind": "tile", "sheet": "decor", "cell": (6, 10), "layer": "props"},
    "grave": {"kind": "tile", "sheet": "decor", "cell": (0, 17), "layer": "props"},
    "grave2": {"kind": "tile", "sheet": "decor", "cell": (2, 17), "layer": "props"},
    "candles": {"kind": "tile", "sheet": "decor", "cell": (3, 8), "layer": "props", "light": (2.5, 0.7)},     # Round RPG15:
    "brazier": {"kind": "tile", "sheet": "effect", "cell": (1, 21), "layer": "props", "light": (4.0, 0.9)},   # (radius, strength)
    "bones": {"kind": "tile", "sheet": "decor", "cell": (0, 12), "layer": "detail"},
    "flowers": {"kind": "tile", "sheet": "ground", "cell": (0, 2), "layer": "detail"},
    "tufts": {"kind": "tile", "sheet": "ground", "cell": (0, 1), "layer": "detail"},
    "signpost": {"kind": "tile", "sheet": "decor", "cell": (0, 21), "layer": "props"},
    "cobweb": {"kind": "tile", "sheet": "decor", "cell": (0, 19), "layer": "above"},
}

LEGEND = {
    ",": "floor_town", ".": "floor_moor", ":": "floor_rocks", "=": "floor_road", "_": "floor_paving", "w": "floor_wood",
    "v": "floor_bridge", "+": "floor_crypt", "-": "floor_dungeon", ";": "floor_cave",
    "H": "wall_wood", "#": "wall_stone", "%": "wall_dungeon", "|": "fence_wood",
    "T": "trees_dark", "t": "tree_dead", "y": "tree_gallows", "~": "water_river", "b": "water_bog",
    "D": "door_wood", ">": "stairs_wood", "<": "stairs_stone", "K": "counter",
    "c": "chair", "n": "table", "s": "shelf", "k": "bookcase", "e": "bed", "x": "crate", "o": "barrel", "z": "coffin",
    "R": "grave", "r": "grave2", "!": "candles", "i": "brazier", "j": "bones", "*": "flowers", '"': "tufts", "p": "signpost",
    "^": "cobweb",
}
SPACED = ("name", "home")             # Round RPG16: "_" in these words is a space (home= is the encounter screen's line)
MARKER_KINDS = ("spawn", "exit", "town", "encounter", "npc", "platino", "light", "rare")   # Round RPG16: "rare"

# the wall / fence joins: (north, south, east, west) -> piece in the 6 x 3 block (DawnLike's legend, Wall.png and Fence.png rows 0-2)
WALL_PIECES = {
    (0, 1, 1, 0): (0, 0), (0, 0, 1, 1): (1, 0), (0, 1, 0, 1): (2, 0), (0, 1, 1, 1): (4, 0),
    (1, 1, 0, 0): (0, 1), (1, 1, 1, 0): (3, 1), (1, 1, 1, 1): (4, 1), (1, 1, 0, 1): (5, 1),
    (1, 0, 1, 0): (0, 2), (1, 0, 0, 1): (2, 2), (1, 0, 1, 1): (4, 2),
    (0, 0, 0, 0): (1, 1),                                    # a post on its own
    (1, 0, 0, 0): (0, 1), (0, 1, 0, 0): (0, 1),              # an end: the straight piece
    (0, 0, 1, 0): (1, 0), (0, 0, 0, 1): (1, 0),
}


class LayoutError(Exception):
    pass


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise LayoutError(f"{path} isn't a PNG")
    return struct.unpack(">II", head[16:24])


# ---- reading a layout ---------------------------------------------------------------------------------------------------------

class Layout:
    def __init__(self, name, path):
        self.name, self.path = name, path
        self.props = {}
        self.legend = dict(LEGEND)
        self.markers = {}                 # char: (kind, {props}, base char)
        self.more = set()                 # Round RPG16: the characters of the [more markers] section
        self.rows = []

    def where(self, line_no):
        return f"{os.path.basename(self.path)} line {line_no}"


def read_layout(path):
    name = os.path.splitext(os.path.basename(path))[0]
    lay = Layout(name, path)
    section = "head"
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    for no, raw in enumerate(lines, 1):
        line = raw.rstrip("\r")
        if section != "map" and (not line.strip() or line.lstrip().startswith("#!")):
            continue
        if line.strip() in ("[legend]", "[markers]", "[more markers]", "[map]"):
            section = line.strip()[1:-1]
            continue
        if section == "head":
            key, sep, val = line.partition(":")
            if not sep:
                raise LayoutError(f"{lay.where(no)}: expected 'key: value', got {line!r}")
            lay.props[key.strip()] = val.strip()
        elif section == "legend":
            ch, sep, terr = line.partition("=")
            ch, terr = ch.strip(), terr.strip()
            if not sep or len(ch) != 1 or terr not in TERRAINS:
                raise LayoutError(f"{lay.where(no)}: a legend line is 'c = terrain' with a known terrain, got {line!r}")
            lay.legend[ch] = terr
        elif section in ("markers", "more markers"):
            ch, sep, rest = line.partition("=")
            ch = ch.strip()
            words = rest.split()
            if not sep or len(ch) != 1 or not words or words[0] not in MARKER_KINDS:
                raise LayoutError(f"{lay.where(no)}: a marker line is 'c = kind key=value ... on x', got {line!r}")
            kind, props, base = words[0], {}, None
            i = 1
            if kind == "spawn" and len(words) > 1 and "=" not in words[1] and words[1] != "on":
                props["name"] = words[1]                  # "spawn start on ," - a spawn's name
                i = 2
            while i < len(words):
                w = words[i]
                if w == "on" and i + 1 < len(words):
                    base = words[i + 1]
                    i += 2
                    continue
                if "=" not in w:
                    raise LayoutError(f"{lay.where(no)}: {w!r} isn't key=value")
                k, _s, v = w.partition("=")
                props[k] = v.replace("_", " ") if k in SPACED else v
                i += 1
            if kind == "spawn" and not props.get("name"):
                raise LayoutError(f"{lay.where(no)}: a spawn needs a name ('1 = spawn start on ,')")
            if base is None or len(base) != 1:
                raise LayoutError(f"{lay.where(no)}: say what the marker stands on ('on ,')")
            if ch in lay.markers:
                raise LayoutError(f"{lay.where(no)}: marker {ch!r} is already given a meaning")
            if kind == "rare" and not props.get("id"):
                raise LayoutError(f"{lay.where(no)}: a rare needs its id in rpg/data/rare_spawns.json ('G = rare id=old_gorsehide on :')")
            lay.markers[ch] = (kind, props, base)
            if section == "more markers":
                lay.more.add(ch)
        else:
            if line == "" and no == len(lines):
                continue
            lay.rows.append(line)
    if not lay.rows:
        raise LayoutError(f"{os.path.basename(path)}: no [map] rows")
    width = len(lay.rows[0])
    for i, r in enumerate(lay.rows):
        if len(r) != width:
            raise LayoutError(f"{os.path.basename(path)}: map row {i} is {len(r)} wide, row 0 is {width}")
    for y, r in enumerate(lay.rows):
        for x, ch in enumerate(r):
            if ch not in lay.legend and ch not in lay.markers:
                raise LayoutError(f"{os.path.basename(path)}: map row {y} column {x}: {ch!r} is neither in the legend nor a marker")
    for ch, (_k, _p, base) in lay.markers.items():
        if base not in lay.legend:
            raise LayoutError(f"{os.path.basename(path)}: marker {ch!r} stands on {base!r}, which isn't in the legend")
    return lay


# ---- building the layers ------------------------------------------------------------------------------------------------------

class Sheets:
    """The tilesets in the order the maps list them, with each one's first gid and column count."""

    def __init__(self, art=ART):
        self.order = list(SHEETS)
        self.first, self.cols, self.size = {}, {}, {}
        gid = 1
        for name in self.order:
            w, h = png_size(os.path.join(art, *SHEETS[name][0].split("/")))
            cols, rows = w // TILE, h // TILE
            self.first[name], self.cols[name], self.size[name] = gid, cols, (w, h)
            gid += cols * rows

    def gid(self, sheet, col, row):
        cols = self.cols[sheet]
        rows = self.size[sheet][1] // TILE
        if not (0 <= col < cols and 0 <= row < rows):
            raise LayoutError(f"{sheet} has no tile at column {col}, row {row}")
        return self.first[sheet] + row * cols + col


def floor_piece(n, s, e, w):
    """(dx, dy) in a floor set (7 x 3): the box, the strips, or the single tile."""
    if not n and not s:
        if not e and not w:
            return (5, 0)
        return (4, 1) if not w else ((6, 1) if not e else (5, 1))
    if not e and not w:
        return (3, 0) if not n else ((3, 2) if not s else (3, 1))
    return (0 if not w else (2 if not e else 1), 0 if not n else (2 if not s else 1))


def build(lay, sheets):
    """The map's layers ({layer: [gid] row by row}) and objects."""
    H, W = len(lay.rows), len(lay.rows[0])

    def terrain_at(x, y):
        ch = lay.rows[y][x]
        if ch in lay.markers:
            ch = lay.markers[ch][2]
        return lay.legend[ch]

    terr = [[terrain_at(x, y) for x in range(W)] for y in range(H)]

    def ground_of(t):
        spec = TERRAINS[t]
        if spec["kind"] == "floor":
            return t
        if spec["kind"] == "water":
            return None
        return spec.get("base")

    ground = [[ground_of(terr[y][x]) for x in range(W)] for y in range(H)]
    # the floor under a tree, a prop or a lone tile that names none: the most common floor of its eight neighbours, worked in from
    # the edges of a group (a forest's middle takes its neighbours' floor); anything left takes the map's most common floor
    todo = {(x, y) for y in range(H) for x in range(W) if ground[y][x] is None and TERRAINS[terr[y][x]]["kind"] != "water"}
    while todo:
        done = {}
        for x, y in sorted(todo, key=lambda p: (p[1], p[0])):
            near = [ground[ny][nx] for nx in (x - 1, x, x + 1) for ny in (y - 1, y, y + 1)
                    if (nx, ny) != (x, y) and 0 <= nx < W and 0 <= ny < H and ground[ny][nx] is not None]
            if near:
                done[(x, y)] = max(sorted(set(near)), key=near.count)
        if not done:
            counts = [g for row in ground for g in row if g is not None]
            common = max(sorted(set(counts)), key=counts.count) if counts else "floor_moor"
            done = {p: common for p in todo}
        for (x, y), g in done.items():
            ground[y][x] = g
        todo -= set(done)
    layers = {name: [0] * (W * H) for name in LAYERS}

    def put(layer, x, y, gid):
        layers[layer][y * W + x] = gid

    def same(grid, x, y, value, edge=True):
        if not (0 <= x < W and 0 <= y < H):
            return edge
        return grid[y][x] == value

    for y in range(H):
        for x in range(W):
            g = ground[y][x]
            if g is None:
                continue
            sx, sy = TERRAINS[g]["set"]
            dx, dy = floor_piece(same(ground, x, y - 1, g), same(ground, x, y + 1, g), same(ground, x + 1, y, g),
                                 same(ground, x - 1, y, g))
            put("ground", x, y, sheets.gid("floor", sx + dx, sy + dy))

    def is_wall(x, y):
        return not (0 <= x < W and 0 <= y < H) or TERRAINS[terr[y][x]]["kind"] == "wall"

    # a wall with only walls round it (the rock inside a dungeon's mass) is drawn as the dark of DawnLike's palette, not as wall
    # pieces: only the walls that face a floor are pieces (as the sheet's own dungeon example does)
    hidden = {(x, y) for y in range(H) for x in range(W) if TERRAINS[terr[y][x]]["kind"] == "wall"
              and all(is_wall(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))}

    def joins(x, y, t):
        if not (0 <= x < W and 0 <= y < H) or (x, y) in hidden:
            return False
        o = terr[y][x]
        if o == t:
            return True
        return TERRAINS[o]["kind"] == "door" and TERRAINS[t]["sheet"] == "wall"

    for y in range(H):
        for x in range(W):
            t = terr[y][x]
            spec = TERRAINS[t]
            kind = spec["kind"]
            if kind == "wall" and (x, y) in hidden:
                put("walls", x, y, sheets.gid("wall", *VOID))
            elif kind == "wall":
                key = (int(joins(x, y - 1, t)), int(joins(x, y + 1, t)), int(joins(x + 1, y, t)), int(joins(x - 1, y, t)))
                dx, dy = WALL_PIECES[key]
                put("walls", x, y, sheets.gid(spec["sheet"], spec["set"][0] + dx, spec["set"][1] + dy))
            elif kind == "door":
                put("doors", x, y, sheets.gid("door", *spec["cell"]))
            elif kind == "trees":
                n, s = same(terr, x, y - 1, t), same(terr, x, y + 1, t)
                e, w = same(terr, x + 1, y, t), same(terr, x - 1, y, t)
                if (not n and not s) or (not e and not w):
                    put("walls", x, y, sheets.gid("tree", *spec["single"]))
                else:
                    sx, sy = spec["set"]
                    dx, dy = floor_piece(n, s, e, w)
                    put("walls", x, y, sheets.gid("tree", sx + dx, sy + dy))
            elif kind == "water":
                sx, sy = spec["set"]
                n = same(terr, x, y - 1, t, edge=True)
                e, w = same(terr, x + 1, y, t), same(terr, x - 1, y, t)
                row = sy + (1 if n else 0)
                col = 4 if (not e and not w) else (0 if not w else (2 if not e else 1))
                put("water", x, y, sheets.gid("pit", sx + col, row))
            elif kind == "counter":
                e, w = same(terr, x + 1, y, t, edge=False), same(terr, x - 1, y, t, edge=False)
                col = 1 if (e and w) or (not e and not w) else (0 if e else 2)
                put("props", x, y, sheets.gid("decor", col, 10))
            elif kind == "pair":
                left = not same(terr, x - 1, y, t, edge=False) or same(terr, x + 1, y, t, edge=False)
                put(spec["layer"], x, y, sheets.gid(spec["sheet"], *spec["cells"][0 if left else 1]))
            elif kind == "tile":
                put(spec["layer"], x, y, sheets.gid(spec["sheet"], *spec["cell"]))
            elif kind != "floor":
                raise LayoutError(f"terrain {t!r}: unknown kind {kind!r}")

    # markers -> objects (each connected group of a marker character is one object)
    objects = []
    seen = set()
    for y in range(H):
        for x in range(W):
            ch = lay.rows[y][x]
            if ch not in lay.markers or (x, y) in seen:
                continue
            group, todo = [], [(x, y)]
            seen.add((x, y))
            while todo:
                cx, cy = todo.pop()
                group.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen and lay.rows[ny][nx] == ch:
                        seen.add((nx, ny))
                        todo.append((nx, ny))
            kind, props, _base = lay.markers[ch]
            x0, x1 = min(c for c, _r in group), max(c for c, _r in group)
            y0, y1 = min(r for _c, r in group), max(r for _c, r in group)
            if kind in ("npc", "platino"):
                if len(group) != 1:
                    raise LayoutError(f"{lay.name}: {kind} marker {ch!r} must be one tile, not {len(group)}")
                sheet, cell = ("reptile", PLATINO) if kind == "platino" else ("humanoid", _cell(props, lay, ch))
                if kind == "npc" and props.get("when"):
                    props = dict(props, cell=f"{cell[0]},{cell[1]}")   # Round RPG16: a person who comes and goes is drawn by the
                else:                                                   # map view while there, not on the people layer
                    put("people", x0, y0, sheets.gid(sheet, *cell))
                if kind == "platino":                 # Round RPG14: he can be talked to, so he is an object too
                    props = dict(props, name="Platino", id="platino")
            objects.append((kind, props, (x0 * TILE, y0 * TILE, (x1 - x0 + 1) * TILE, (y1 - y0 + 1) * TILE),
                            (1 if ch in lay.more else 0, y0, x0, ch)))
    objects.sort(key=lambda o: o[3])           # Round RPG16: the [more markers] after the markers, so no older object's id moves
    # Round RPG15: every tile that gives light (braziers, candles) is a light object too; one inside a dark zone burns only once the
    # zone's flag is set. They come after the markers, so the markers' object ids (the save's "<map>#<id>" spots) don't move.
    zones = dark_zones(lay)
    glow = []
    for y in range(H):
        for x in range(W):
            spec = TERRAINS[terr[y][x]]
            if spec.get("light") and lay.rows[y][x] not in lay.markers:
                radius, strength = spec["light"]
                props = {"radius": radius, "strength": strength, "flicker": "true"}
                flag = next((f for (c0, r0, c1, r1), f in zones if c0 <= x <= c1 and r0 <= y <= r1), None)
                if flag:
                    props["flag"] = flag
                glow.append(("light", props, (x * TILE, y * TILE, TILE, TILE), (2, y, x, lay.rows[y][x])))
    return W, H, layers, [o for o in objects if o[0] != "light"] + [o for o in objects if o[0] == "light"] + glow


def dark_zones(lay):
    """Round RPG15: the layout's dark_zone property, as rpg.explore reads it: [((col0, row0, col1, row1), flag)]."""
    out = []
    for part in str(lay.props.get("dark_zone") or "").split(";"):
        if part.strip():
            box, flag = part.split()
            out.append((tuple(int(v) for v in box.split(",")), flag))
    return out


def _cell(props, lay, ch):
    m = re.fullmatch(r"(\d+),(\d+)", props.get("cell", ""))
    if not m:
        raise LayoutError(f"{lay.name}: npc marker {ch!r} needs cell=column,row (Characters/Humanoid0.png)")
    return int(m.group(1)), int(m.group(2))


# ---- writing ------------------------------------------------------------------------------------------------------------------

TYPED = {"region": "int", "light": "float", "dungeon": "bool", "once": "bool", "wander": "bool",
         "encounter_rate": "int",      # Round RPG13: random fights (rpg.encounters)
         "darkness": "float", "lantern": "float", "radius": "float", "strength": "float", "flicker": "bool",     # Round RPG15
         "outdoor": "bool", "night_darkness": "float",
         "counter": "bool",                                                  # Round RPG15b: a tile's
         "event_rate": "int"}                                                # Round RPG16: random events (rpg.events)


def _value(k, v):
    """A property's value as the map file writes it."""
    t = TYPED.get(k)
    if t == "bool":
        v = "true" if str(v).lower() in ("1", "true", "yes") else "false"
    if t == "int":
        int(v)
    if t == "float":
        float(v)
    return str(v)


def _prop_xml(k, v):
    t = TYPED.get(k)
    v = _value(k, v)
    typ = f" type={quoteattr(t)}" if t else ""
    return f'<property name={quoteattr(k)}{typ} value={quoteattr(v)}/>'


# Round RPG15b: properties of single tiles, written into their tileset. A counter tile says so: rpg.explore lets you talk across a
# counter (the innkeeper's, a shopkeeper's) but never across a wall.
TILE_PROPS = {"decor": {(c, 10): {"counter": True} for c in range(3)}}


def tsx_text(name, sheets, art_rel):
    w, h = sheets.size[name]
    cols, rows = w // TILE, h // TILE
    image, pair = SHEETS[name]
    props = ""
    if pair:
        props = f' <properties>\n  <property name="frame2" value={quoteattr(art_rel + "/" + pair)}/>\n </properties>\n'
    tiles = ""
    for (col, row), tp in sorted(TILE_PROPS.get(name, {}).items(), key=lambda kv: (kv[0][1], kv[0][0])):
        tiles += (f' <tile id="{row * cols + col}">\n  <properties>\n'
                  + "".join(f"   {_prop_xml(k, v)}\n" for k, v in sorted(tp.items())) + "  </properties>\n </tile>\n")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<tileset version="1.10" tiledversion="1.10.2" name={quoteattr("dl_" + name)} tilewidth="{TILE}" '
            f'tileheight="{TILE}" tilecount="{cols * rows}" columns="{cols}">\n'
            f'{props} <image source={quoteattr(art_rel + "/" + image)} width="{w}" height="{h}"/>\n{tiles}</tileset>\n')


def _written_props(kind, props):
    """(name, {property: value as written}) of one object, as tmx_text writes it."""
    rest = {k: v for k, v in props.items() if k != "name" and not (kind == "npc" and k == "cell")}
    if kind == "npc":
        rest["sprite"] = "Characters/Humanoid"
        rest["cell"] = props.get("cell")
    return props.get("name", ""), rest


def _signature(kind, name, rest):
    return (kind, name, tuple(sorted((k, _value(k, v)) for k, v in rest.items())))


def read_ids(path):
    """Round RPG15c (review R5): the objects of a map file already built - [(id, signature, (x, y))] - or [] when there is none."""
    if not path or not os.path.exists(path):
        return []
    out = []
    for ob in ET.parse(path).getroot().iter("object"):
        props = {p.get("name"): p.get("value") for p in ob.iter("property")}
        sig = (ob.get("class") or ob.get("type") or "", ob.get("name") or "", tuple(sorted(props.items())))
        out.append((int(ob.get("id")), sig, (float(ob.get("x")), float(ob.get("y")))))
    return out


def assign_ids(objects, old=()):
    """Round RPG15c (review R5): an id for each object. A saved spot is "<map>#<object id>" (the beaten and cleared foes), and ids
    used to be the place in reading order, so a marker added above an old one renumbered every object after it (RPG16's night
    foes would have marked the wrong foe beaten). Now an object keeps the id of the old object with the same kind, name and
    properties - the nearest one when there are several (a moved marker keeps its id) - and new objects are numbered after the
    highest old id, in reading order. Without an old file: 1, 2, 3 ... in reading order, as before."""
    ids = [None] * len(objects)
    pairs = []
    for i, (kind, props, (x, y, _w, _h), _key) in enumerate(objects):
        sig = _signature(kind, *_written_props(kind, props))
        for oid, osig, (ox, oy) in old:
            if osig == sig:
                pairs.append(((x - ox) ** 2 + (y - oy) ** 2, oid, i))
    taken = set()
    for _d, oid, i in sorted(pairs):
        if ids[i] is None and oid not in taken:
            ids[i] = oid
            taken.add(oid)
    # Round RPG16: an object that only GAINED properties (a `when`, a `home`) where it stood keeps its id too - the same kind and
    # name on the same spot, every old property unchanged (the zealot by day, Pell and Hesk by day, the gate guard off at night).
    for i, (kind, props, (x, y, _w, _h), _key) in enumerate(objects):
        if ids[i] is not None:
            continue
        name, rest = _written_props(kind, props)
        now = dict(_signature(kind, name, rest)[2])
        for oid, (okind, oname, oprops), (ox, oy) in old:
            if oid in taken or (okind, oname, ox, oy) != (kind, name, x, y):
                continue
            if all(now.get(k) == v for k, v in oprops):
                ids[i] = oid
                taken.add(oid)
                break
    nxt = max([oid for oid, _s, _p in old] + [0]) + 1
    for i in range(len(objects)):
        if ids[i] is None:
            ids[i] = nxt
            nxt += 1
    return ids


def tmx_text(lay, sheets, old=()):
    """The map file. old: read_ids() of the file this one replaces (Round RPG15c: its objects keep their ids)."""
    W, H, layers, objects = build(lay, sheets)
    ids = assign_ids(objects, old)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<map version="1.10" tiledversion="1.10.2" orientation="orthogonal" renderorder="right-down" width="{W}" '
           f'height="{H}" tilewidth="{TILE}" tileheight="{TILE}" infinite="0" nextlayerid="{len(LAYERS) + 2}" '
           f'nextobjectid="{max(ids + [0]) + 1}">']
    if lay.props:
        out.append(" <properties>")
        for k in sorted(lay.props):
            out.append("  " + _prop_xml(k, lay.props[k]))
        out.append(" </properties>")
    for name in sheets.order:
        out.append(f' <tileset firstgid="{sheets.first[name]}" source="dl_{name}.tsx"/>')
    for i, name in enumerate(LAYERS, 1):
        data = layers[name]
        out.append(f' <layer id="{i}" name="{name}" width="{W}" height="{H}">')
        if name in SOLID_LAYERS:
            out.append('  <properties>\n   <property name="collision" type="bool" value="true"/>\n  </properties>')
        rows = [",".join(str(g) for g in data[r * W:(r + 1) * W]) for r in range(H)]
        out.append('  <data encoding="csv">\n' + ",\n".join(rows) + "\n</data>")
        out.append(" </layer>")
    out.append(f' <objectgroup id="{len(LAYERS) + 1}" name="objects">')
    for i, (kind, props, (x, y, w, h), _key) in zip(ids, objects):
        name, rest = _written_props(kind, props)
        out.append(f'  <object id="{i}" name={quoteattr(name)} class={quoteattr(kind)} x="{x}" y="{y}" width="{w}" '
                   f'height="{h}">' + ("" if not rest else
                                         "\n   <properties>\n" + "".join(f"    {_prop_xml(k, rest[k])}\n" for k in sorted(rest))
                                         + "   </properties>\n  "))
        out[-1] += "</object>"
    out.append(" </objectgroup>")
    out.append("</map>")
    return "\n".join(out) + "\n"


def make(out=MAPS, src=SRC, art=ART, relative_to=MAPS, previous=None):
    """Writes the tilesets and every layout's map into `out`; returns the paths. The pictures are named relative to
    `relative_to` - rpg/maps, where the files live, even for a build elsewhere that is only compared with them (--check); a test
    that loads what it built passes its own folder. Round RPG15c: each map's objects keep the ids of the map file in `previous`
    (default `out`: the file being replaced; --check passes rpg/maps)."""
    previous = out if previous is None else previous
    sheets = Sheets(art)
    os.makedirs(out, exist_ok=True)
    art_rel = os.path.relpath(art, relative_to).replace(os.sep, "/")
    written = []
    for name in sheets.order:
        p = os.path.join(out, f"dl_{name}.tsx")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(tsx_text(name, sheets, art_rel))
        written.append(p)
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".txt"):
            continue
        lay = read_layout(os.path.join(src, fn))
        p = os.path.join(out, lay.name + ".tmx")
        text = tmx_text(lay, sheets, read_ids(os.path.join(previous, lay.name + ".tmx")))
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        written.append(p)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=MAPS)
    ap.add_argument("--check", action="store_true", help="build into a temporary folder and compare with rpg/maps")
    a = ap.parse_args(argv)
    try:
        if a.check:
            tmp = tempfile.mkdtemp(prefix="rpg_maps_")
            try:
                files = make(out=tmp, previous=MAPS)
                bad = [os.path.basename(p) for p in files
                       if not os.path.exists(os.path.join(MAPS, os.path.basename(p)))
                       or open(p, "rb").read() != open(os.path.join(MAPS, os.path.basename(p)), "rb").read()]
            finally:
                shutil.rmtree(tmp, True)
            if bad:
                print("Out of date (run tools/rpg_make_maps.py): " + ", ".join(bad))
                return 1
            print(f"{len(files)} map files up to date")
            return 0
        files = make(out=a.out)
    except LayoutError as e:
        print(f"Layout problem: {e}", file=sys.stderr)
        return 2
    print(f"{len(files)} files written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
