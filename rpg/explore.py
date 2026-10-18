# SPDX-License-Identifier: GPL-3.0-or-later
"""Walking the RPG's maps, without pygame (Round RPG5; the plan's phase 3, part 2): where you can walk, walking, what you walk
into, and the camera. rpg_map_view.py (beside the program, not in this package) draws it.

A map is a Tiled map in rpg/maps/<name>.tmx, read by rpg.tmx. What this module takes from it:
  - where you can't walk: a tile whose tileset gives it the property solid=true (or collides=true), on any layer; and any tile on a
    layer named "collision" (any case) or with the property collision=true. The map's edges are walls too.
  - objects, by their class (Tiled 1.9+; "type" in older files):
      spawn      a point or a rectangle (its centre); its name is what an exit or a town names. "start" is used when nothing
                 else says where to stand.
      encounter  a rectangle (a point is one tile): property kind = an enemy kind's id (rpg/data/enemy_kinds.json), encounter =
                 regular | dungeon | boss (default regular), once = true/false (default: false for regular, true otherwise).
      exit       a rectangle: map = another map's name, spawn = a spawn's name on that map.
      town       a rectangle: town = a town's id (rpg/data/towns.json). The map also needs a spawn named after the town: where you
                 stand when you leave it, after a lost duel, and in a new game.
  - the map's own properties: name (what the screen calls it), region (a number in towns.json).

What you walk into fires once, as you enter it (not every step while you stand in it, and not where you were put down). An
encounter that is won is gone: for good when it is "once" (save.world["cleared"]), until your next visit to a town when it isn't
(save.world["beaten"]; duel.visit_town empties it). Those two lists hold "<map>#<object id>".

My estimates (no source; the plan says the slice's look and feel is tuned while Karl plays it): WALK_TILES_PER_SECOND,
FEET (the walker's box against walls), ROWS_IN_VIEW (the camera's zoom).

Round RPG6 - the original Zelda's way of showing and walking a map (Karl, 12 Oct 2026: "I want RPG mode 'look and feel' to be
inspired by original Zelda"; then his choices: rooms + 4-way walking, the room centred with side panels, the Diablo 1 / Vermis
dark palette kept, the map side only):
  - a map is cut into ROOMS of ROOM_COLS x ROOM_ROWS tiles - the original's screen, "16 x 11 tiles" of "16 x 16 pixels"
    (inventwithpython.com/blog/2012/12/10/8-bit-nes-legend-of-zelda-map-data). One room is on screen (RoomCamera); walking off
    it slides the view to the next room over ROOM_SLIDE_SECONDS, and the walker waits meanwhile.
  - the Explorer walks in four directions (four_way): with two directions held, the one pressed later wins. Walking along one
    axis, the walker drifts onto the nearest half-tile line across its way (ALIGN). Stopped by a wall, it stops dead (Round RPG7,
    Karl 13 Oct; CORNER_HELP = False). With CORNER_HELP it would slide across to a way through within CORNER tiles
    (Walker._corner), so a one-tile gap lets you in from anywhere beside it - RPG6's first version.
  - a foe walks about its own room (Foe), only while you are in that room, and is back where it started when you come in again;
    a boss stands still (map property wander=false does the same for any foe). Touching it starts the duel, as before.
  - save.world["seen"]: the rooms you have been in, "<map>#<column>,<row>" (the minimap shows them).
  - a map property light = a radius in tiles makes the map dark but for a light around the walker (rpg_map_view draws it): the
    plan's "Diablo 1 and Vermis feel through palette and lighting". Round RPG7 (Karl, 13 Oct: "Dark only in dungeons"): only on a
    dungeon - a map whose property dungeon is true, or (without that property) one with a dungeon or boss encounter.
None of Nintendo's art, music or names is used: the tiles are this project's own placeholders (tools/rpg_make_maps.py).
More of my estimates: ALIGN, CORNER, FOE_TILES_PER_SECOND, FOE_TURN, FOE_BOX, PANEL_MIN_W. Karl's (Round RPG7):
ROOM_SLIDE_SECONDS 0.3, CORNER_HELP off, darkness only in dungeons.
"""
import math
import os
import random
import zlib

from rpg import tmx

HERE = os.path.dirname(os.path.abspath(__file__))
MAPS_DIR = os.path.join(HERE, "maps")
COLLISION_LAYER = "collision"
SOLID_PROPS = ("solid", "collides")
KINDS = ("spawn", "encounter", "exit", "town")
WALK_TILES_PER_SECOND = 4.5          # my estimate
FEET = (0.625, 0.5)                  # the walker's box, as a share of a tile (width, height) - my estimate
ROWS_IN_VIEW = 15                    # the camera's zoom aims at about this many rows of tiles on the screen - my estimate
EPS = 1e-6
ROOM_COLS, ROOM_ROWS = 16, 11         # Round RPG6: the original Zelda's screen, in tiles (see the module's notes)
ROOM_SLIDE_SECONDS = 0.3              # the view's slide to the next room - Karl, 13 Oct (Round RPG7; RPG6's estimate was 0.6)
ALIGN = 0.5                           # tiles: the half-tile lines the walker drifts onto across its way - my estimate
CORNER_HELP = False                   # Round RPG7 (Karl, 13 Oct): stopped by a wall, the walker stops dead - no corner help
CORNER = 0.5                          # tiles: with CORNER_HELP, how far across the walker slides to a way through - my estimate
FOE_TILES_PER_SECOND = 1.6            # a wandering foe's pace - my estimate
FOE_TURN = (0.5, 1.6)                 # seconds between a foe's turns - my estimate
FOE_BOX = 0.75                        # a foe's box, as a share of a tile - my estimate
PANEL_MIN_W = 120                     # px: a side panel narrower than this makes the room's zoom step down - my estimate
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class MapError(Exception):
    pass


class Trigger:
    """Something on a map you can walk into."""
    __slots__ = ("kind", "obj", "box", "spot", "enemy", "encounter", "once", "to_map", "to_spawn", "town", "wander")

    def __init__(self, kind, obj, box, spot):
        self.kind, self.obj, self.box, self.spot = kind, obj, box, spot
        p = obj.props
        self.enemy = p.get("kind") if kind == "encounter" else None
        self.encounter = p.get("encounter", "regular") if kind == "encounter" else None
        once = p.get("once")
        self.once = (self.encounter != "regular") if once is None else bool(once)
        self.to_map = p.get("map") if kind == "exit" else None
        self.to_spawn = p.get("spawn") if kind == "exit" else None
        self.town = p.get("town") if kind == "town" else None
        wander = p.get("wander")              # Round RPG6: a foe walks about its room; a boss stands still
        self.wander = kind == "encounter" and ((self.encounter != "boss") if wander is None else bool(wander))

    def __repr__(self):
        what = {"encounter": self.enemy, "exit": f"{self.to_map}:{self.to_spawn}", "town": self.town}.get(self.kind)
        return f"Trigger({self.kind}, {what!r}, {self.spot})"


def overlaps(a, b):
    """Two (x0, y0, x1, y1) boxes overlap (touching edges don't)."""
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


class Area:
    """One map, ready to walk on."""

    def __init__(self, name, tiled):
        self.name = name
        self.map = tiled
        self.tw, self.th = tiled.tilewidth, tiled.tileheight
        self.cols, self.rows = tiled.width, tiled.height
        self.width, self.height = self.cols * self.tw, self.rows * self.th
        self.title = str(tiled.props.get("name", name))
        self.region = tiled.props.get("region")
        light = tiled.props.get("light")                  # Round RPG6: dark but for a light this many tiles round the walker
        self.light = float(light) if isinstance(light, (int, float)) and not isinstance(light, bool) and light > 0 else None
        # (Round RPG7: only a dungeon is dark - see the end of __init__)
        self.room_w, self.room_h = ROOM_COLS * self.tw, ROOM_ROWS * self.th
        self.room_cols = max(1, -(-self.cols // ROOM_COLS))
        self.room_rows = max(1, -(-self.rows // ROOM_ROWS))
        self.solid = bytearray(self.cols * self.rows)
        for layer in tiled.layers:
            whole = layer.name.lower() == COLLISION_LAYER or layer.props.get("collision") is True
            for i, gid in enumerate(layer.data):
                if not gid:
                    continue
                if whole or any(tiled.tile_props(gid).get(k) is True for k in SOLID_PROPS):
                    self.solid[i] = 1
        self.spawns = {}
        self.triggers = []
        self.problems = []
        for ob in tiled.objects:
            if ob.kind not in KINDS:
                continue
            if ob.kind == "spawn":
                if ob.name in self.spawns:
                    self.problems.append(f"two spawns are called {ob.name!r}")
                self.spawns[ob.name] = (ob.x + ob.width / 2, ob.y + ob.height / 2)
                continue
            if ob.is_point:
                box = (ob.x - self.tw / 2, ob.y - self.th / 2, ob.x + self.tw / 2, ob.y + self.th / 2)
            else:
                box = (ob.x, ob.y, ob.x + ob.width, ob.y + ob.height)
            self.triggers.append(Trigger(ob.kind, ob, box, f"{name}#{ob.id}"))
        # Round RPG7 (Karl, 13 Oct: "Dark only in dungeons"): a map is a dungeon when its property dungeon says so (true / false),
        # else when it has a dungeon or boss encounter. Only a dungeon is dark; light on any other map is left out and said.
        dungeon = tiled.props.get("dungeon")
        self.dungeon = dungeon if isinstance(dungeon, bool) else any(
            t.kind == "encounter" and t.encounter in ("dungeon", "boss") for t in self.triggers)
        if self.light is not None and not self.dungeon:
            self.problems.append("the map has light (dark but round the walker), but only dungeons are dark: give it a dungeon or "
                                 "boss encounter, or the map property dungeon = true")
            self.light = None

    # ---- tiles ------------------------------------------------------------------------------------------------------------

    def blocked(self, col, row):
        if col < 0 or row < 0 or col >= self.cols or row >= self.rows:
            return True
        return bool(self.solid[row * self.cols + col])

    def box_blocked(self, box):
        """Any wall under the box (x0, y0, x1, y1), or the box past the map's edge."""
        x0, y0, x1, y1 = box
        if x0 < -EPS or y0 < -EPS or x1 > self.width + EPS or y1 > self.height + EPS:
            return True
        for row in range(int(math.floor((y0 + EPS) / self.th)), int(math.floor((y1 - EPS) / self.th)) + 1):
            for col in range(int(math.floor((x0 + EPS) / self.tw)), int(math.floor((x1 - EPS) / self.tw)) + 1):
                if self.blocked(col, row):
                    return True
        return False

    def tile_at(self, x, y):
        return int(x // self.tw), int(y // self.th)

    # ---- rooms (Round RPG6) ---------------------------------------------------------------------------------------------

    def room_of(self, x, y):
        """(column, row) of the room the map pixel (x, y) is in."""
        rx = min(max(int(x // self.room_w), 0), self.room_cols - 1)
        ry = min(max(int(y // self.room_h), 0), self.room_rows - 1)
        return rx, ry

    def room_rect(self, room):
        """(x0, y0, x1, y1) of a room in map pixels (a whole room, even where the map ends inside it)."""
        rx, ry = room
        return (rx * self.room_w, ry * self.room_h, (rx + 1) * self.room_w, (ry + 1) * self.room_h)

    def room_key(self, room):
        return f"{self.name}#{room[0]},{room[1]}"

    def spawn(self, name=None):
        """(x, y) of a spawn: the one named, else "start", else the first; None when the map has none."""
        if name is not None and name in self.spawns:
            return self.spawns[name]
        if name is None and "start" in self.spawns:
            return self.spawns["start"]
        if name is None and self.spawns:
            return next(iter(self.spawns.values()))
        return None

    def towns(self):
        return [t.town for t in self.triggers if t.kind == "town"]

    def reachable(self, start_xy):
        """Every tile you can walk to from (x, y), by flood fill over tiles that aren't walls (4 directions)."""
        c, r = self.tile_at(*start_xy)
        if self.blocked(c, r):
            return set()
        seen, todo = {(c, r)}, [(c, r)]
        while todo:
            c, r = todo.pop()
            for nc, nr in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if (nc, nr) not in seen and not self.blocked(nc, nr):
                    seen.add((nc, nr))
                    todo.append((nc, nr))
        return seen

    def check(self, kinds=None, towns=None, maps=None, spawns_of=None):
        """What is wrong with the map, as sentences (an empty list = nothing). kinds / towns / maps are the ids that exist;
        spawns_of(map) the spawn names of another map. Each check runs only when its list is given."""
        out = list(self.problems)
        if not self.spawns:
            out.append("the map has no spawn")
        if (self.cols > ROOM_COLS or self.rows > ROOM_ROWS) and (self.cols % ROOM_COLS or self.rows % ROOM_ROWS):
            out.append(f"the map is {self.cols} x {self.rows} tiles: make it whole rooms of {ROOM_COLS} x {ROOM_ROWS}")
        feet_w, feet_h = self.tw * FEET[0], self.th * FEET[1]
        for name, (x, y) in self.spawns.items():
            if self.box_blocked((x - feet_w / 2, y - feet_h / 2, x + feet_w / 2, y + feet_h / 2)):
                out.append(f"spawn {name!r} is in a wall")
        start = self.spawn()
        reach = self.reachable(start) if start else set()
        for t in self.triggers:
            where = f"{t.kind} {t.obj.id} ({t.obj.name or 'no name'})"
            if t.kind == "encounter":
                if not t.enemy:
                    out.append(f"{where} has no kind")
                elif kinds is not None and t.enemy not in kinds:
                    out.append(f"{where}: no enemy kind {t.enemy!r}")
                if t.encounter not in ("regular", "dungeon", "boss"):
                    out.append(f"{where}: encounter {t.encounter!r} isn't regular, dungeon or boss")
            elif t.kind == "exit":
                if not t.to_map or not t.to_spawn:
                    out.append(f"{where} needs map and spawn")
                elif maps is not None and t.to_map not in maps:
                    out.append(f"{where}: no map {t.to_map!r}")
                elif spawns_of is not None and t.to_spawn not in spawns_of(t.to_map):
                    out.append(f"{where}: map {t.to_map!r} has no spawn {t.to_spawn!r}")
            elif t.kind == "town":
                if not t.town:
                    out.append(f"{where} has no town")
                else:
                    if towns is not None and t.town not in towns:
                        out.append(f"{where}: no town {t.town!r}")
                    if t.town not in self.spawns:
                        out.append(f"{where}: the map has no spawn named {t.town!r} (where you stand outside it)")
            if start and not self._reachable_box(t.box, reach):
                out.append(f"{where} can't be reached from the start")
        for name, xy in self.spawns.items():
            if start and self.tile_at(*xy) not in reach and not self.blocked(*self.tile_at(*xy)):
                out.append(f"spawn {name!r} can't be reached from the start")
        return out

    def _reachable_box(self, box, reach):
        x0, y0, x1, y1 = box
        for row in range(int(y0 // self.th), int(math.ceil(y1 / self.th))):
            for col in range(int(x0 // self.tw), int(math.ceil(x1 / self.tw))):
                if (col, row) in reach:
                    return True
        return False


class Walker:
    """The player on a map: a point at the middle of the feet and a box around it that walls stop."""

    def __init__(self, area, x, y, speed=None):
        self.area = area
        self.x, self.y = float(x), float(y)
        self.hw = area.tw * FEET[0] / 2
        self.hh = area.th * FEET[1] / 2
        self.speed = WALK_TILES_PER_SECOND * area.tw if speed is None else speed
        self.facing = "down"
        self.inside = {t.spot for t in self._touching()}     # the spots it stands in now: they don't fire until left

    def box(self, x=None, y=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        return (x - self.hw, y - self.hh, x + self.hw, y + self.hh)

    def _touching(self, skip=(), boxes=None):
        """The triggers under the walker; `boxes` {spot: box} puts a moving foe where it is now (Round RPG6)."""
        b = self.box()
        boxes = boxes or {}
        return [t for t in self.area.triggers if t.spot not in skip and overlaps(b, boxes.get(t.spot, t.box))]

    def _slide(self, axis, step):
        """Move along one axis; a wall stops the box flush against it."""
        a = self.area
        if axis == 0:
            nx = self.x + step
            if not a.box_blocked(self.box(x=nx)):
                self.x = nx
                return
            if step > 0:
                edge = min(math.floor((nx + self.hw - EPS) / a.tw) * a.tw, a.width)
                self.x = max(self.x, edge - self.hw)
            else:
                edge = max((math.floor((nx - self.hw + EPS) / a.tw) + 1) * a.tw, 0)
                self.x = min(self.x, edge + self.hw)
        else:
            ny = self.y + step
            if not a.box_blocked(self.box(y=ny)):
                self.y = ny
                return
            if step > 0:
                edge = min(math.floor((ny + self.hh - EPS) / a.th) * a.th, a.height)
                self.y = max(self.y, edge - self.hh)
            else:
                edge = max((math.floor((ny - self.hh + EPS) / a.th) + 1) * a.th, 0)
                self.y = min(self.y, edge + self.hh)

    def move(self, dx, dy, dt, skip=(), boxes=None, align=False):
        """Walk for dt seconds in direction (dx, dy) (each -1, 0 or 1; a diagonal is as fast as a straight line). Returns the
        triggers walked into, in the order they were entered; spots in `skip` (cleared or beaten encounters) never fire.
        Round RPG6: `boxes` {spot: box} - moving foes where they are now; `align` - walking along one axis, drift onto the
        nearest ALIGN-tile line across it (never into a wall), as the original Zelda keeps its hero on a half-tile grid, and when a
        wall stops it, stop dead (Round RPG7) - or, with CORNER_HELP, slide across to a way through within CORNER tiles (_corner)."""
        if dx == 0 and dy == 0 or dt <= 0:
            return []
        self.facing = ("right" if dx > 0 else "left") if dx else ("down" if dy > 0 else "up")
        n = math.hypot(dx, dy)
        dist = self.speed * dt
        sx, sy = dx / n * dist, dy / n * dist
        steps = max(1, int(math.ceil(max(abs(sx) / (self.area.tw / 4), abs(sy) / (self.area.th / 4)))))
        fired = []
        for _ in range(steps):
            before = (self.x, self.y)
            if sx:
                self._slide(0, sx / steps)
            if sy:
                self._slide(1, sy / steps)
            if align and bool(sx) != bool(sy):
                main = 0 if sx else 1
                want = (sx if sx else sy) / steps
                stopped = abs((self.x, self.y)[main] - before[main] - want) > EPS
                if stopped and not CORNER_HELP:
                    pass                            # Round RPG7: a wall stops the walker dead (no slide across, no drift)
                elif not (stopped and self._corner(main, want, dist / steps)):
                    self._align(1 - main, dist / steps)
            now = self._touching(skip, boxes)
            spots = {t.spot for t in now}
            for t in now:
                if t.spot not in self.inside:
                    fired.append(t)
            self.inside = spots
            if fired:
                break                               # stop where it happened: the screen changes
        return fired

    def _align(self, axis, most):
        """Drift (at most `most` px) along `axis` onto the nearest ALIGN-tile line (Round RPG6)."""
        grid = (self.area.tw if axis == 0 else self.area.th) * ALIGN
        pos = self.x if axis == 0 else self.y
        off = round(pos / grid) * grid - pos
        if abs(off) > EPS:
            self._slide(axis, max(-most, min(most, off)))

    def _corner(self, main, want, most):
        """Stopped by a wall while walking along `main`: when a tile's middle line within CORNER tiles across has a way on, slide
        (at most `most` px) toward the nearest such line and say True - the corner help of 4-way Zelda-likes, so a one-tile gap
        lets you in from anywhere beside it (Round RPG6)."""
        a = self.area
        cross = 1 - main
        size = a.tw if cross == 0 else a.th
        pos = (self.x, self.y)[cross]
        nudge = (1 if want > 0 else -1) * min(a.tw, a.th) / 8
        best = None
        first = int(math.floor(pos / size))
        for k in (first - 1, first, first + 1):
            off = k * size + size / 2 - pos
            if abs(off) <= EPS or abs(off) > CORNER * size + EPS:
                continue
            spot = [self.x, self.y]
            spot[cross] += off
            spot[main] += nudge
            if not a.box_blocked(self.box(*spot)) and (best is None or abs(off) < abs(best)):
                best = off
        if best is None:
            return False
        self._slide(cross, max(-most, min(most, best)))
        return True

    def recheck(self, skip=(), boxes=None):
        """What the walker stands in now that it didn't before (a foe walked into it): the same "on the way in" rule."""
        now = self._touching(skip, boxes)
        fired = [t for t in now if t.spot not in self.inside]
        self.inside = {t.spot for t in now}
        return fired

    def place(self, x, y):
        self.x, self.y = float(x), float(y)
        self.inside = {t.spot for t in self._touching()}

    def resting_place(self):
        """(x, y) as whole pixels for the save, chosen so the box isn't in a wall (rounding can move it half a pixel)."""
        for fx in (round, math.floor, math.ceil):
            for fy in (round, math.floor, math.ceil):
                x, y = int(fx(self.x)), int(fy(self.y))
                if not self.area.box_blocked(self.box(x, y)):
                    return x, y
        return int(round(self.x)), int(round(self.y))


def four_way(dx, dy, held=(0, 0), axis=None):
    """Round RPG6: the original Zelda walks in four directions. (dx, dy) is what is pressed now, `held` what was pressed the frame
    before, `axis` the axis walked on then ("x", "y" or None). Returns (dx, dy, axis) with one of dx, dy 0: with two directions
    pressed, the one pressed later wins; pressed together, or both still held, the axis already walked on stays (across when
    there is none). Nothing pressed -> (0, 0, None)."""
    if dx and dy:
        hx, hy = held
        if hx and not hy:
            use = "y"                         # the up/down key came later
        elif hy and not hx:
            use = "x"                         # the left/right key came later
        else:
            use = axis or "x"
        return (dx, 0, "x") if use == "x" else (0, dy, "y")
    if dx:
        return dx, 0, "x"
    if dy:
        return 0, dy, "y"
    return 0, 0, None


class Foe:
    """Round RPG6: an encounter that walks about its own room (the original Zelda's enemies do) while you are in that room. It is
    back where it started whenever you come into the room. A boss - or any foe whose object says wander=false - stands still.
    Touching it starts its duel, as for a standing encounter. Its walk comes from its own random numbers (seeded by its spot and
    how many times its room was entered), so the same walk comes out every time."""

    def __init__(self, area, trigger):
        self.area, self.t, self.spot = area, trigger, trigger.spot
        x0, y0, x1, y1 = trigger.box
        self.home = ((x0 + x1) / 2, (y0 + y1) / 2)
        self.room = area.room_of(*self.home)
        self.hw, self.hh = area.tw * FOE_BOX / 2, area.th * FOE_BOX / 2
        self.speed = FOE_TILES_PER_SECOND * area.tw
        self.entered = 0
        self.reset()

    # what the view and the checks read (the same names as a Trigger's)
    kind = "encounter"
    enemy = property(lambda self: self.t.enemy)
    encounter = property(lambda self: self.t.encounter)
    once = property(lambda self: self.t.once)
    obj = property(lambda self: self.t.obj)
    wanders = property(lambda self: self.t.wander)

    @property
    def box(self):
        return (self.x - self.hw, self.y - self.hh, self.x + self.hw, self.y + self.hh)

    def reset(self):
        self.x, self.y = self.home
        self.dir = (0, 0)
        self.turn_in = 0.0
        self.rng = random.Random(zlib.crc32(f"{self.spot}|{self.entered}".encode("utf-8")))

    def enter(self):
        """You came into its room: it starts again from home."""
        self.entered += 1
        self.reset()

    def _free(self, x, y):
        b = (x - self.hw, y - self.hh, x + self.hw, y + self.hh)
        r = self.area.room_rect(self.room)
        inside = b[0] >= r[0] + self.area.tw - EPS and b[1] >= r[1] + self.area.th - EPS and \
            b[2] <= r[2] - self.area.tw + EPS and b[3] <= r[3] - self.area.th + EPS
        return inside and not self.area.box_blocked(b)

    def step(self, dt):
        """Walk for dt seconds: a straight line, a new direction (or a pause) every FOE_TURN seconds, turning at once at a wall or
        at its room's edge (it keeps a tile off the edge, where you come in)."""
        if not self.wanders or dt <= 0:
            return
        left = dt
        while left > EPS:
            if self.turn_in <= 0:
                self.dir = self.rng.choice(DIRECTIONS + ((0, 0),))
                self.turn_in = self.rng.uniform(*FOE_TURN)
            part = min(left, self.turn_in)
            self.turn_in -= part
            left -= part
            if self.dir == (0, 0):
                continue
            dist = self.speed * part
            steps = max(1, int(math.ceil(dist / (self.area.tw / 4))))
            for _ in range(steps):
                nx = self.x + self.dir[0] * dist / steps
                ny = self.y + self.dir[1] * dist / steps
                if not self._free(nx, ny):
                    self.turn_in = 0.0               # blocked: a new direction next
                    break
                self.x, self.y = nx, ny


class RoomCamera:
    """Round RPG6: the original Zelda's camera - one room on screen at a whole-number zoom (pixel art stays sharp), centred in the
    window, the width left over on each side for the side panels. The room's zoom is the largest that fits and still leaves
    each panel PANEL_MIN_W wide (a window too narrow for that gets zoom 1). Explorer.view_origin() says which part of the map
    the room shows (it slides between two rooms); look_at() takes it."""

    def __init__(self, view_w, view_h, tile_w=16, tile_h=16, zoom=None, panel_min=PANEL_MIN_W):
        self.view_w, self.view_h = int(view_w), int(view_h)
        self.tw, self.th = tile_w, tile_h
        self.zoom = int(zoom) if zoom else self.zoom_for(view_w, view_h, tile_w, tile_h, panel_min)
        self.room_px = (ROOM_COLS * tile_w * self.zoom, ROOM_ROWS * tile_h * self.zoom)
        self.ox = (self.view_w - self.room_px[0]) // 2
        self.oy = (self.view_h - self.room_px[1]) // 2
        self.x = self.y = 0.0                     # the map pixel at the room view's top left

    @staticmethod
    def zoom_for(view_w, view_h, tile_w=16, tile_h=16, panel_min=PANEL_MIN_W):
        rw, rh = ROOM_COLS * tile_w, ROOM_ROWS * tile_h
        z = max(1, min(int(view_w // rw), int(view_h // rh)))
        while z > 1 and (view_w - rw * z) / 2 < panel_min:
            z -= 1
        return z

    def viewport(self):
        """(x, y, w, h) of the room on the screen."""
        return (self.ox, self.oy, self.room_px[0], self.room_px[1])

    def panels(self):
        """((x, y, w, h) left, (x, y, w, h) right): the side panels."""
        right_x = self.ox + self.room_px[0]
        return ((0, 0, max(0, self.ox), self.view_h), (right_x, 0, max(0, self.view_w - right_x), self.view_h))

    def look_at(self, origin):
        x, y = origin
        self.x = round(x * self.zoom) / self.zoom               # whole screen pixels while it slides
        self.y = round(y * self.zoom) / self.zoom

    def to_screen(self, mx, my):
        return self.ox + int(round((mx - self.x) * self.zoom)), self.oy + int(round((my - self.y) * self.zoom))

    def to_map(self, sx, sy):
        return (sx - self.ox) / self.zoom + self.x, (sy - self.oy) / self.zoom + self.y

    def visible(self, tw, th, cols, rows):
        """(c0, r0, c1, r1): the map's tiles inside the room view, c1 and r1 not included."""
        vw, vh = ROOM_COLS * self.tw, ROOM_ROWS * self.th
        c0 = max(0, int(math.floor(self.x / tw)))
        r0 = max(0, int(math.floor(self.y / th)))
        c1 = min(cols, int(math.ceil((self.x + vw) / tw)))
        r1 = min(rows, int(math.ceil((self.y + vh) / th)))
        return c0, r0, c1, r1


class Camera:
    """What part of the map the screen shows: a whole-number zoom (pixel art stays sharp), following the walker, never showing
    past the map's edge (a map smaller than the screen is centred)."""

    def __init__(self, view_w, view_h, zoom=1):
        self.view_w, self.view_h = int(view_w), int(view_h)
        self.zoom = max(1, int(zoom))
        self.x = self.y = 0.0                     # the map pixel at the screen's top left

    @staticmethod
    def zoom_for(view_h, tile_h, rows=ROWS_IN_VIEW):
        return max(1, int(view_h // (tile_h * rows)))

    def follow(self, tx, ty, map_w, map_h):
        vw, vh = self.view_w / self.zoom, self.view_h / self.zoom
        self.x = (map_w - vw) / 2 if map_w <= vw else min(max(tx - vw / 2, 0.0), map_w - vw)
        self.y = (map_h - vh) / 2 if map_h <= vh else min(max(ty - vh / 2, 0.0), map_h - vh)
        self.x = round(self.x * self.zoom) / self.zoom        # whole screen pixels: no shimmer while walking
        self.y = round(self.y * self.zoom) / self.zoom

    def to_screen(self, mx, my):
        return int(round((mx - self.x) * self.zoom)), int(round((my - self.y) * self.zoom))

    def to_map(self, sx, sy):
        return sx / self.zoom + self.x, sy / self.zoom + self.y

    def visible(self, tw, th, cols, rows):
        """(c0, r0, c1, r1): the tiles to draw, c1 and r1 not included."""
        vw, vh = self.view_w / self.zoom, self.view_h / self.zoom
        c0 = max(0, int(math.floor(self.x / tw)))
        r0 = max(0, int(math.floor(self.y / th)))
        c1 = min(cols, int(math.ceil((self.x + vw) / tw)))
        r1 = min(rows, int(math.ceil((self.y + vh) / th)))
        return c0, r0, c1, r1


# ---- the maps on disk ---------------------------------------------------------------------------------------------------------

def map_names(folder=MAPS_DIR):
    try:
        return sorted(f[:-4] for f in os.listdir(folder) if f.endswith(".tmx"))
    except OSError:
        return []


_AREAS = {}


def load_area(name, folder=MAPS_DIR):
    key = (os.path.abspath(folder), name)
    if key not in _AREAS:
        path = os.path.join(folder, name + ".tmx")
        if not os.path.exists(path):
            raise MapError(f"No map called {name!r}.")
        _AREAS[key] = Area(name, tmx.read_map(path))
    return _AREAS[key]


def forget_areas():
    _AREAS.clear()


def town_map(town_id, folder=MAPS_DIR):
    """The name of the map a town is on, or None."""
    for name in map_names(folder):
        try:
            if town_id in load_area(name, folder).towns():
                return name
        except (tmx.TmxError, MapError):
            continue
    return None


def check_all(folder=MAPS_DIR, kinds=None, towns=None):
    """{map name: [problems]} for every map in the folder, the exits checked against the other maps."""
    names = map_names(folder)
    out = {}
    areas = {}
    for name in names:
        try:
            areas[name] = load_area(name, folder)
        except (tmx.TmxError, MapError) as e:
            out[name] = [str(e)]
    for name, area in areas.items():
        out[name] = area.check(kinds=kinds, towns=towns, maps=set(areas),
                               spawns_of=lambda m: set(areas[m].spawns) if m in areas else set())
    return out


class Explorer:
    """The walking part of an RPG game: the map you're on, where you stand, and what happens when you walk into something.
    It reads and writes save.world: "map" (a map's name - or a town's id, which means "outside that town"), "x", "y",
    "cleared", "beaten", and (Round RPG6) "seen" - the rooms you have been in.

    Round RPG6: one room at a time. step() walks in four directions (four_way), moves the room's foes, and when you walk off the
    room it slides the view to the next one (view_origin() for the camera; sliding() is True meanwhile and the walker waits)."""

    def __init__(self, save, start_town=None, folder=MAPS_DIR):
        self.save = save
        self.folder = folder
        self.start_town = start_town
        self.area = None
        self.walker = None
        self.room = None
        self.slide = None                 # {"from": room, "to": room, "t": seconds} while the view slides
        self.foes = {}
        self.held = (0, 0)                # what was pressed last frame (four_way)
        self.axis = None
        self.place()

    # where you are

    def place(self):
        """Put the walker where the save says: a map and a spot on it; outside a town; else the start town; else the first
        map's start."""
        w = self.save.world
        where = w.get("map")
        if where in map_names(self.folder):
            area = load_area(where, self.folder)
            x, y = w.get("x"), w.get("y")
            if x is not None and y is not None:
                walker = Walker(area, x, y)
                if not area.box_blocked(walker.box()):
                    self._use(area, walker)
                    return
            self._use(area, Walker(area, *self._spawn(area, None)))
            return
        town = where or self.start_town
        if town:
            name = town_map(town, self.folder)
            if name:
                area = load_area(name, self.folder)
                self._use(area, Walker(area, *self._spawn(area, town)))
                return
        names = map_names(self.folder)
        if not names:
            raise MapError("There are no maps.")
        area = load_area(names[0], self.folder)
        self._use(area, Walker(area, *self._spawn(area, None)))

    def _spawn(self, area, name):
        xy = area.spawn(name) or area.spawn()
        if xy is None:
            raise MapError(f"Map {area.name!r} has no spawn.")
        return xy

    def _use(self, area, walker):
        self.area, self.walker = area, walker
        self.slide = None
        self.foes = {t.spot: Foe(area, t) for t in area.triggers if t.kind == "encounter"}
        self._enter(area.room_of(walker.x, walker.y))
        self.store()

    def _enter(self, room):
        """You are in `room` now: its foes start again from home, and it counts as seen."""
        self.room = room
        for f in self.foes.values():
            if f.room == room:
                f.enter()
        seen = self.save.world.setdefault("seen", [])
        key = self.area.room_key(room)
        if key not in seen:
            seen.append(key)

    def store(self):
        """Write where the walker is into the save (whole pixels)."""
        x, y = self.walker.resting_place()
        self.save.world.update(map=self.area.name, x=x, y=y)

    # what's gone

    def gone(self):
        w = self.save.world
        return set(w.get("cleared") or ()) | set(w.get("beaten") or ())

    def encounters(self):
        """The foes still on this map, where they are now (for the screen to draw; it shows only the room in view)."""
        gone = self.gone()
        return [f for f in self.foes.values() if f.spot not in gone]

    def room_foes(self):
        gone = self.gone()
        return [f for f in self.foes.values() if f.room == self.room and f.spot not in gone]

    def _skip(self):
        """Spots that can't fire now: won fights, and foes of the rooms you aren't in."""
        return self.gone() | {f.spot for f in self.foes.values() if f.room != self.room}

    # rooms

    def sliding(self):
        return self.slide is not None

    def view_origin(self):
        """The map pixel at the room view's top left: the room you are in, or part way from the last one while it slides."""
        to = self.area.room_rect(self.room)[:2]
        if self.slide is None:
            return to
        fr = self.area.room_rect(self.slide["from"])[:2]
        p = min(1.0, self.slide["t"] / ROOM_SLIDE_SECONDS)
        return (fr[0] + (to[0] - fr[0]) * p, fr[1] + (to[1] - fr[1]) * p)

    def _slide_to(self, room):
        """Walked off the room: the walker steps wholly into the next one (never into a wall) and waits while the view slides."""
        old, w = self.room, self.walker
        r = self.area.room_rect(room)
        if room[0] != old[0]:
            target = max(w.x, r[0] + w.hw) if room[0] > old[0] else min(w.x, r[2] - w.hw)
            w._slide(0, target - w.x)
        if room[1] != old[1]:
            target = max(w.y, r[1] + w.hh) if room[1] > old[1] else min(w.y, r[3] - w.hh)
            w._slide(1, target - w.y)
        self.slide = {"from": old, "to": room, "t": 0.0}
        self._enter(room)
        w.inside = {t.spot for t in w._touching(self._skip(), {f.spot: f.box for f in self.room_foes()})}

    # walking

    def _fire(self, fired):
        for t in fired:
            if t.kind == "exit":
                area = load_area(t.to_map, self.folder)
                self._use(area, Walker(area, *self._spawn(area, t.to_spawn)))
                return ("map", area.name)
            if t.kind == "town":
                return ("town", t.town)
            if t.kind == "encounter":
                return ("encounter", t)
        return None

    def step(self, dx, dy, dt):
        """Walk; returns what happened, or None: ("encounter", trigger) - start that duel (duel.start(..., spot=trigger.spot,
        once=trigger.once)); ("town", town id) - open the town; ("map", name) - you went through an exit and are on that map
        now. The save's place is updated either way. (dx, dy) is what is pressed; Round RPG6 walks it in four directions, and
        while the view slides to the next room nothing moves."""
        if self.slide is not None:
            self.slide["t"] += dt
            if self.slide["t"] >= ROOM_SLIDE_SECONDS:
                self.slide = None
            self.held = (dx, dy)
            return None
        mdx, mdy, self.axis = four_way(dx, dy, self.held, self.axis)
        self.held = (dx, dy)
        fired = self.walker.move(mdx, mdy, dt, skip=self._skip(), boxes={f.spot: f.box for f in self.room_foes()}, align=True)
        result = self._fire(fired)
        if result is not None and result[0] == "map":
            return result
        if result is None:
            for f in self.room_foes():
                f.step(dt)
            result = self._fire(self.walker.recheck(self._skip(), {f.spot: f.box for f in self.room_foes()}))
        if result is None:
            room = self.area.room_of(self.walker.x, self.walker.y)
            if room != self.room:
                self._slide_to(room)
        self.store()
        return result

    def leave_town(self, town_id):
        """Back on the map, outside the town (its spawn)."""
        name = town_map(town_id, self.folder)
        if name is None:
            raise MapError(f"No map has the town {town_id!r}.")
        area = load_area(name, self.folder)
        self._use(area, Walker(area, *self._spawn(area, town_id)))
