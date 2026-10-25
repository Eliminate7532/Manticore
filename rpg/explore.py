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
More of my estimates: ALIGN, CORNER, FOE_TILES_PER_SECOND, FOE_TURN, FOE_BOX. Karl's (Round RPG7): CORNER_HELP off, darkness
only in dungeons.

Round RPG11 (the Adventure remaster, Karl 23 Oct 2026: "smooth free-scrolling movement instead of room-by-room screens", with
Final Fantasy Pixel Remaster as the reference; claude/SPEC_RPG_SLICE_2026-10-23.md): RPG6's rooms are gone.
  - The camera follows the walker (Camera.follow), whole map pixels so the tiles never shimmer; no rooms, no slide, no side panels
    and no minimap (save.world["seen"] is no longer written; an old save's list is left alone).
  - A foe wanders within LEASH_TILES of where it stands on the map, and only while it is within ACTIVE_TILES of the walker; each
    time it wakes it starts again from home with a walk seeded by its spot and how many times it woke (still the same walk
    every time). A boss still stands still.
  - The walker keeps which way it faces in the save (world["facing"]), and counts how far it has walked (Walker.walked) for the
    view's walk frames; Walker.moving says whether it moved this step.
  - Area.check no longer asks for whole rooms.
My estimates: ACTIVE_TILES, LEASH_TILES.

Round RPG14: people (map objects of class npc with an id, and the hidden platino) - Area.npcs, Explorer.facing_npc (the one in front
of you, or across a counter); the map property dark_zone ("col0,row0,col1,row1 flag", several with ";") - a part of the map that is
dark until the flag is set (Lantern Hollow's duel hall: grave_priest_beaten); save.world["spawn"] - where the next place() puts you
on the map the save names (a lost duel: the inn you last rested at).

Round RPG13 (random fights in dungeons, Karl's decision 6): a map with the properties encounter_rate and encounters has random
fights (rpg.encounters: the steps to the next one are drawn from the save's rng and kept in save.world["danger"]); step() then
returns ("random", rpg.encounters.RandomFoe) when one starts. Only tiles walked count. The map property backdrop (cave / dungeon /
forest) is the encounter screen's (rpg_encounter); without one a dungeon gets the dungeon and any other map the forest.
"""
import math
import os
import random
import zlib

from rpg import clock, conditions, daynight, encounters, tmx
from rpg import events as rpg_events
from rpg import notes as rpg_notes
from rpg import rare as rpg_rare

HERE = os.path.dirname(os.path.abspath(__file__))
MAPS_DIR = os.path.join(HERE, "maps")
COLLISION_LAYER = "collision"
SOLID_PROPS = ("solid", "collides")
KINDS = ("spawn", "encounter", "exit", "town", "rare")    # Round RPG16: "rare" (a rare foe: rpg/data/rare_spawns.json)
WALK_TILES_PER_SECOND = 4.5          # my estimate
FEET = (0.625, 0.5)                  # the walker's box, as a share of a tile (width, height) - my estimate
ROWS_IN_VIEW = 15                    # the camera's zoom aims at about this many rows of tiles on the screen - my estimate
VIEW_HALF = (14, 8)                  # Round RPG16: half the view in tiles, until rpg_screens says (a 16:9 window) - my estimate
VIEW_MARGIN = 1                      # ... and a tile more, so nothing comes or goes at the edge of the screen
EPS = 1e-6
ALIGN = 0.5                           # tiles: the half-tile lines the walker drifts onto across its way - my estimate
CORNER_HELP = False                   # Round RPG7 (Karl, 13 Oct): stopped by a wall, the walker stops dead - no corner help
CORNER = 0.5                          # tiles: with CORNER_HELP, how far across the walker slides to a way through - my estimate
FOE_TILES_PER_SECOND = 1.6            # a wandering foe's pace - my estimate
FOE_TURN = (0.5, 1.6)                 # seconds between a foe's turns - my estimate
FOE_BOX = 0.75                        # a foe's box, as a share of a tile - my estimate
ACTIVE_TILES = 12                     # Round RPG11: a foe moves only within this many tiles of the walker - my estimate
LEASH_TILES = 4                       # Round RPG11: how far from its spot a foe wanders - my estimate
INN_SPAWN = "inn"                     # Round RPG12: the spawn inside the start town's inn (an old save starts there)
PERSON_TILES_PER_SECOND = 2.0         # Round RPG17: a person walking between their places - my estimate
WALK_ROUTE_MOST = 400                 # ... the longest way a person walks (tiles); longer, they wait out of sight as before
FACINGS = ("down", "left", "right", "up")
BACKDROPS = ("cave", "dungeon", "forest")    # Round RPG13: Simple Battles' scenes (assets/rpg/simple_battles/scene_<name>.png)
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
OLD_DARKNESS = 228 / 255             # Round RPG15: a map with only RPG6's "light" is as dark as RPG6 drew it (DARK_ALPHA 228)
LIGHT_RADIUS, LIGHT_STRENGTH = 3.0, 0.8    # Round RPG15: a light object's defaults (tiles; 1 = no darkness at its middle) - my estimate
NIGHT_MORE, NIGHT_MAX = 0.25, 0.85         # Round RPG15: an outdoor map's night is this much darker than its dusk, at most - my estimate


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Light:
    """Round RPG15: a light source on a map - (x, y) its middle in map pixels, radius in tiles, strength 0-1 (how much of the
    darkness it takes away at its middle), flicker, and flag (lit only once that story flag is set; None = always)."""
    __slots__ = ("id", "x", "y", "radius", "strength", "flicker", "flag")

    def __init__(self, lid, x, y, radius, strength, flicker=True, flag=None):
        self.id, self.x, self.y, self.radius, self.strength, self.flicker, self.flag = lid, x, y, radius, strength, flicker, flag

    def __repr__(self):
        return f"Light({self.id}, ({self.x:.0f}, {self.y:.0f}), r={self.radius}, s={self.strength}" + \
            (f", flag={self.flag}" if self.flag else "") + ")"


class MapError(Exception):
    pass


class Trigger:
    """Something on a map you can walk into."""
    __slots__ = ("kind", "obj", "box", "spot", "enemy", "encounter", "once", "to_map", "to_spawn", "town", "wander", "tab",
                 "back", "when", "home", "rare")

    def __init__(self, kind, obj, box, spot, rare=None):
        self.kind, self.obj, self.box, self.spot = kind, obj, box, spot
        p = obj.props
        # Round RPG16: a rare foe (an object of class rare) is an encounter whose kind, fight and `when` come from its table entry
        self.rare = rare.get("id") if rare else None
        self.enemy = (rare or p).get("kind") if kind == "encounter" else None
        self.encounter = (rare or p).get("encounter", "regular") if kind == "encounter" else None
        once = False if rare else p.get("once")
        self.once = (self.encounter != "regular") if once is None else bool(once)
        self.when = conditions.parse_when(p.get("when")) + conditions.parse_when((rare or {}).get("when"))   # RPG16: there only
        # then (RPG16d: the rare's `when` read as the map's are, so a hand-edited 5 can't stop the map from loading)
        self.home = p.get("home") or None                     # Round RPG16: the encounter screen's "where it lives" line
        self.to_map = p.get("map") if kind == "exit" else None
        self.to_spawn = p.get("spawn") if kind == "exit" else None
        self.town = p.get("town") if kind == "town" else None
        self.tab = p.get("tab") if kind == "town" else None          # Round RPG12: the shop's tab a counter opens on
        self.back = p.get("spawn") if kind == "town" else None       # ... and the spawn you leave it at (default: the town's)
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
        light = tiled.props.get("lantern", tiled.props.get("light"))   # Round RPG6: a light this many tiles round the walker
        self.light = float(light) if isinstance(light, (int, float)) and not isinstance(light, bool) and light > 0 else None
        # Round RPG15: how dark the map is (0 none - 1 black); RPG6-RPG14's maps had only "light", which meant RPG6's dark
        darkness = tiled.props.get("darkness")
        good = isinstance(darkness, (int, float)) and not isinstance(darkness, bool) and 0 <= darkness <= 1
        self.darkness = float(darkness) if good else (OLD_DARKNESS if self.light is not None else 0.0)
        # ... and an outdoor map follows the clock (rpg.clock): its darkness is the dusk's, night_darkness the night's
        outdoor = tiled.props.get("outdoor")
        if isinstance(outdoor, str) and outdoor.strip().lower() in ("true", "false"):
            outdoor = outdoor.strip().lower() == "true"            # Round RPG15c (review N5): a string property made in Tiled
        self.outdoor = outdoor is True
        night = tiled.props.get("night_darkness")
        self.night_darkness = float(night) if _number(night) and 0 <= night <= 1 else min(NIGHT_MAX, self.darkness + NIGHT_MORE)
        self.solid = bytearray(self.cols * self.rows)
        self.counters = set()
        town = tiled.props.get("town")                          # Round RPG15b: this map is that town (rduel.visit_town on arriving)
        self.town = town if isinstance(town, str) and town else None
        for layer in tiled.layers:
            whole = layer.name.lower() == COLLISION_LAYER or layer.props.get("collision") is True
            for i, gid in enumerate(layer.data):
                if not gid:
                    continue
                if whole or any(tiled.tile_props(gid).get(k) is True for k in SOLID_PROPS):
                    self.solid[i] = 1
                if tiled.tile_props(gid).get("counter") is True:     # Round RPG15b: you talk across a counter, never a wall
                    self.counters.add((i % self.cols, i // self.cols))
        self.spawns = {}
        self.triggers = []
        self.problems = []
        spots = set()
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
            kind, entry, spot = ob.kind, None, f"{name}#{ob.id}"
            if kind == "rare":                                   # Round RPG16: a rare foe, its spot named by its id
                rid = ob.props.get("id")
                try:
                    entry = rpg_rare.spawns().get(rid) if isinstance(rid, str) else None
                except (OSError, ValueError):
                    entry = None
                if not isinstance(entry, dict):
                    self.problems.append(f"rare {ob.id}: no rare {rid!r} in rare_spawns.json")
                    continue
                kind, entry, spot = "encounter", dict(entry, id=rid), rpg_rare.spot(name, rid)
            elif isinstance(ob.props.get("spot"), str) and ob.props["spot"]:
                spot = f"{name}#{ob.props['spot']}"          # Round RPG16: a spot of its own, whatever its id
            if spot in spots:
                self.problems.append(f"two objects have the spot {spot!r}")
            spots.add(spot)
            t = Trigger(kind, ob, box, spot, rare=entry)
            for c in t.when:
                why = conditions.problem(c) or ("a map word means nothing on a map" if c.lstrip("!").startswith("map:") else None)
                if why:
                    self.problems.append(f"{kind} {ob.id}: {why}")
            self.triggers.append(t)
        # Round RPG7 (Karl, 13 Oct: "Dark only in dungeons"): a map is a dungeon when its property dungeon says so (true / false),
        # else when it has a dungeon or boss encounter. RPG7 left the light out of any other map; Round RPG15 (the slice's spec:
        # "the darkness layer for every map", town dusk 0.45) keeps it - the open question to Karl is in docs/rounds/RPG15.md.
        dungeon = tiled.props.get("dungeon")
        self.dungeon = dungeon if isinstance(dungeon, bool) else any(
            t.kind == "encounter" and t.encounter in ("dungeon", "boss") for t in self.triggers)
        if darkness is not None and not good:
            self.problems.append(f"darkness {darkness!r} isn't a number from 0 to 1")
        if outdoor is not None and not isinstance(outdoor, bool):
            self.problems.append(f"outdoor {outdoor!r} isn't true or false")     # Round RPG15c (review N5): said, not ignored
        night = tiled.props.get("night_darkness")
        if night is not None and not (_number(night) and 0 <= night <= 1):
            self.problems.append(f"night_darkness {night!r} isn't a number from 0 to 1")
        # Round RPG15: light sources - objects of class light (radius in tiles, strength 0-1, flicker, and a flag that must be set)
        self.lights = []
        for ob in tiled.objects:
            if ob.kind != "light":
                continue
            p = ob.props
            radius, strength = p.get("radius", LIGHT_RADIUS), p.get("strength", LIGHT_STRENGTH)
            if not (_number(radius) and radius > 0 and _number(strength) and 0 < strength <= 1):
                self.problems.append(f"light {ob.id} needs radius > 0 and strength from 0 to 1 (has {radius!r}, {strength!r})")
                continue
            x, y = (ob.x, ob.y) if ob.is_point else (ob.x + ob.width / 2, ob.y + ob.height / 2)
            self.lights.append(Light(f"{name}#{ob.id}", x, y, float(radius), float(strength),
                                     p.get("flicker", True) is not False, p.get("flag") or None))
        # Round RPG15: the map's music and ambience (cue ids in sounds/cues.json; rpg_screens.AdventureFlow.soundscape)
        self.music = tiled.props.get("music") or None
        self.ambience = tiled.props.get("ambience") or None
        # Round RPG16: random events (rpg.events: event_rate = tiles walked between checks, on average)
        rate = tiled.props.get("event_rate")
        self.event_rate = rate if isinstance(rate, int) and not isinstance(rate, bool) and rate > rpg_events.EVENT_MIN_GAP else None
        if rate is not None and self.event_rate is None:
            self.problems.append(f"event_rate {rate!r} isn't a whole number above {rpg_events.EVENT_MIN_GAP}")
        # Round RPG13: random fights (rpg.encounters: encounter_rate + encounters) and the encounter screen's backdrop
        self.fight_rate, self.fight_table = None, []
        rate, table = tiled.props.get("encounter_rate"), tiled.props.get("encounters")
        if rate is not None or table is not None:
            try:
                if rate is None or table is None:
                    raise encounters.EncounterError("random fights need both encounter_rate and encounters")
                self.fight_rate, self.fight_table = encounters.check_rate(rate), encounters.parse_table(table)
            except encounters.EncounterError as e:
                self.problems.append(str(e))
                self.fight_rate, self.fight_table = None, []
        # Round RPG14: the people you can talk to (objects of class npc, and the hidden platino) and the dark zones
        self.npcs = {}
        self.people = []        # Round RPG16: every person's marker - {"id", "name", "tile", "when", "cell", "key"} (a person may
        for ob in tiled.objects:                                # have one place by day and another at night)
            if ob.kind in ("npc", "platino"):
                nid = ob.props.get("id") or ("platino" if ob.kind == "platino" else None)
                if not nid:
                    self.problems.append(f"npc {ob.id} ({ob.name or 'no name'}) has no id")
                    continue
                tile = (int(ob.x // self.tw), int(ob.y // self.th))
                when = conditions.parse_when(ob.props.get("when"))
                for c in when:
                    why = conditions.problem(c)
                    if why:
                        self.problems.append(f"npc {nid}: {why}")
                cell = None
                try:
                    cell = tuple(int(v) for v in str(ob.props.get("cell") or "").split(","))
                except ValueError:
                    pass
                person = {"id": nid, "name": ob.name or nid, "tile": tile, "when": when,
                          "cell": cell if cell and len(cell) == 2 else None, "key": f"npc:{nid}@{tile[0]},{tile[1]}"}
                self.people.append(person)
                self.npcs.setdefault(nid, person)
        for nid in {p["id"] for p in self.people}:
            places = [p for p in self.people if p["id"] == nid]
            whens = [p["when"] for p in places]
            if len(places) > 1 and not all(whens):
                self.problems.append(f"two people are called {nid!r}")
            elif len(places) > 1 and daynight.clashes(whens):
                self.problems.append(f"{nid} can be in two places at once ({daynight.clashes(whens)[0]})")
            if nid in daynight.ALWAYS_SOMEWHERE and any(whens) and daynight.gaps(whens):
                self.problems.append(f"{nid} is nowhere at times ({daynight.gaps(whens)[0]}) - they must always be somewhere")
        self.dark_zones = []
        for part in str(tiled.props.get("dark_zone") or "").split(";"):
            if not part.strip():
                continue
            try:
                box, flag = part.split()
                c0, r0, c1, r1 = (int(v) for v in box.split(","))
                self.dark_zones.append(((c0, r0, c1, r1), flag))
            except ValueError:
                self.problems.append(f"dark_zone {part.strip()!r} isn't 'col0,row0,col1,row1 flag'")
        backdrop = tiled.props.get("backdrop")
        self.backdrop = backdrop if backdrop in BACKDROPS else ("dungeon" if self.dungeon else "forest")
        if backdrop is not None and backdrop not in BACKDROPS:
            self.problems.append(f"backdrop {backdrop!r} isn't one of {', '.join(BACKDROPS)}")

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

    def spawn(self, name=None):
        """(x, y) of a spawn: the one named, else "start", else the first; None when the map has none."""
        if name is not None and name in self.spawns:
            return self.spawns[name]
        if name is None and "start" in self.spawns:
            return self.spawns["start"]
        if name is None and self.spawns:
            return next(iter(self.spawns.values()))
        return None

    def npc_at(self, col, row, present=None):
        """Round RPG14: the id of the person standing on that tile, or None. Round RPG16: present - the keys of the people who come
        and go that are there now (rpg.explore.Explorer.present); None: every marker counts."""
        for p in self.people:
            if p["tile"] == (col, row) and (present is None or not p["when"] or p["key"] in present):
                return p["id"]
        return None

    def scheduled(self):
        """Round RPG16: the markers of people who come and go (they carry `when`)."""
        return [p for p in self.people if p["when"]]

    def dark(self, flags):
        """Round RPG14: the dark zones still dark - [(col0, row0, col1, row1)] whose flag isn't set."""
        return [box for box, flag in self.dark_zones if flag not in flags]

    def darkness_at(self, minutes):
        """Round RPG15: how dark the map is at that time of the clock - an outdoor map by the clock (none by day), any other its own
        darkness at every hour."""
        if not self.outdoor:
            return self.darkness
        return clock.level(minutes, self.darkness, self.night_darkness)

    def lit(self, flags):
        """Round RPG15: the light sources burning - those with no flag, or whose flag is set."""
        return [li for li in self.lights if li.flag is None or li.flag in flags]

    def towns(self):
        return [t.town for t in self.triggers if t.kind == "town"]

    def reachable(self, start_xy, walls=()):
        """Every tile you can walk to from (x, y), by flood fill over tiles that aren't walls (4 directions). walls: more tiles to
        count as walls (Round RPG16: the people who come and go, at every hour)."""
        walls = set(walls)
        c, r = self.tile_at(*start_xy)
        if self.blocked(c, r) or (c, r) in walls:
            return set()
        seen, todo = {(c, r)}, [(c, r)]
        while todo:
            c, r = todo.pop()
            for nc, nr in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if (nc, nr) not in seen and not self.blocked(nc, nr) and (nc, nr) not in walls:
                    seen.add((nc, nr))
                    todo.append((nc, nr))
        return seen

    def check(self, kinds=None, towns=None, maps=None, spawns_of=None):
        """What is wrong with the map, as sentences (an empty list = nothing). kinds / towns / maps are the ids that exist;
        spawns_of(map) the spawn names of another map. Each check runs only when its list is given."""
        out = list(self.problems)
        if not self.spawns:
            out.append("the map has no spawn")
        feet_w, feet_h = self.tw * FEET[0], self.th * FEET[1]
        for name, (x, y) in self.spawns.items():
            if self.box_blocked((x - feet_w / 2, y - feet_h / 2, x + feet_w / 2, y + feet_h / 2)):
                out.append(f"spawn {name!r} is in a wall")
        start = self.spawn()
        people = {p["tile"] for p in self.scheduled()}            # Round RPG16: nothing reachable only past someone who stands
        reach = self.reachable(start, people) if start else set()  # there at some hour
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
                    if t.back and t.back not in self.spawns:
                        out.append(f"{where}: the map has no spawn named {t.back!r} (its spawn property)")
            if start and not self._reachable_box(t.box, reach) and not (t.kind == "town" and self._beside_counter(t.box, reach)):
                out.append(f"{where} can't be reached from the start")
        if kinds is not None:                                 # Round RPG13: the random fights' foes exist
            for k, _w in self.fight_table:
                if k not in kinds:
                    out.append(f"encounters: no enemy kind {k!r}")
        for name, xy in self.spawns.items():
            if start and self.tile_at(*xy) not in reach and not self.blocked(*self.tile_at(*xy)):
                out.append(f"spawn {name!r} can't be reached from the start")
        for p in self.people:                                    # Round RPG16: everyone can be talked to, at every hour
            c, r = p["tile"]
            near = [(c + dx, r + dy) for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))]
            across = [(c + 2 * dx, r + 2 * dy) for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)) if self.is_counter(c + dx, r + dy)]
            if start and p["when"] and not any(t in reach for t in near + across):
                out.append(f"npc {p['id']} at {p['tile']} can't be talked to (no free tile beside them)")
        return out

    def _beside_counter(self, box, reach):
        """Round RPG15b: a shop on a counter tile, opened by talking to its keeper across it - reached when a tile beside it is."""
        c, r = self.tile_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        return (c, r) in self.counters and any((c + dx, r + dy) in reach for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)))

    def is_counter(self, col, row):
        return (col, row) in self.counters

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
        self.walked = 0.0                  # Round RPG11: map pixels walked so far (the view's walk frames)
        self.moving = False                # Round RPG11: did it move in the last step
        self.inside = {t.spot for t in self._touching()}     # the spots it stands in now: they don't fire until left
        self.blockers = set()              # Round RPG16: tiles of the people who come and go, there now (they wall you off)

    def box(self, x=None, y=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        return (x - self.hw, y - self.hh, x + self.hw, y + self.hh)

    def _blocked(self, box):
        if self.area.box_blocked(box):
            return True
        if not self.blockers:
            return False
        a = self.area
        x0, y0, x1, y1 = box
        for row in range(int(math.floor((y0 + EPS) / a.th)), int(math.floor((y1 - EPS) / a.th)) + 1):
            for col in range(int(math.floor((x0 + EPS) / a.tw)), int(math.floor((x1 - EPS) / a.tw)) + 1):
                if (col, row) in self.blockers:
                    return True
        return False

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
            if not self._blocked(self.box(x=nx)):
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
            if not self._blocked(self.box(y=ny)):
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
        start = (self.x, self.y)
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
        moved = math.hypot(self.x - start[0], self.y - start[1])
        self.walked += moved
        self.moving = moved > EPS
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
            if not self._blocked(self.box(*spot)) and (best is None or abs(off) < abs(best)):
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
                if not self._blocked(self.box(x, y)):
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
    """An encounter that walks about where it stands (Round RPG6; Round RPG11: within LEASH_TILES of its spot, and only while the
    walker is within ACTIVE_TILES - Explorer wakes and stills it). Each time it wakes it walks on from where it stands (Round
    RPG15c, review R7: it used to start again from home, a hop of up to 4 tiles in plain view - the line where foes wake is on
    screen), its walk seeded by its spot and how many times it woke, so the same walk comes out every time. A boss - or any foe whose object says
    wander=false - stands still. Touching it starts its duel, as for a standing encounter."""

    def __init__(self, area, trigger):
        self.area, self.t, self.spot = area, trigger, trigger.spot
        x0, y0, x1, y1 = trigger.box
        self.home = ((x0 + x1) / 2, (y0 + y1) / 2)
        self.hw, self.hh = area.tw * FOE_BOX / 2, area.th * FOE_BOX / 2
        self.speed = FOE_TILES_PER_SECOND * area.tw
        self.leash = LEASH_TILES * min(area.tw, area.th)
        self.woken = 0
        self.active = False
        self.avoid = frozenset()            # Round RPG16c (review N5): tiles it may not walk onto - the people out at this hour
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
        self.walked = 0.0
        self.rng = random.Random(zlib.crc32(f"{self.spot}|{self.woken}".encode("utf-8")))

    def wake(self):
        """The walker came near: it walks on from where it stands (Round RPG15c: never a hop home in view), a new walk seeded by
        its spot and how many times it woke."""
        self.woken += 1
        self.dir = (0, 0)
        self.turn_in = 0.0
        self.rng = random.Random(zlib.crc32(f"{self.spot}|{self.woken}".encode("utf-8")))
        self.active = True

    def still(self):
        """The walker went far away: it stops where it is (and walks on from there when it wakes again)."""
        self.active = False

    def _free(self, x, y):
        if math.hypot(x - self.home[0], y - self.home[1]) > self.leash + EPS:
            return False
        box = (x - self.hw, y - self.hh, x + self.hw, y + self.hh)
        if self.avoid:                                   # Round RPG16c (review N5): never through a person who is out
            on = self._avoided(box)
            # Round RPG16d (review T6): a person can come out (out of view) where the foe already stands; every step would still
            # be over them and be refused, freezing the foe on top of them. Now a step may stay over the people it is already
            # over (so it walks off them), never onto anyone new.
            if on and not on <= self._avoided(self.box):
                return False
        return not self.area.box_blocked(box)

    def _avoided(self, box):
        """The avoided tiles (people who are out) a box is over."""
        a = self.area
        c0, r0 = int(box[0] // a.tw), int(box[1] // a.th)
        c1, r1 = int((box[2] - EPS) // a.tw), int((box[3] - EPS) // a.th)
        return {(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1) if (c, r) in self.avoid}

    def step(self, dt):
        """Walk for dt seconds: a straight line, a new direction (or a pause) every FOE_TURN seconds, turning at once at a wall or
        at the end of its leash."""
        if not self.wanders or not self.active or dt <= 0:
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
                self.walked += dist / steps


def _finite(v):
    """Round RPG16d: a number a float can hold - math.isfinite raises OverflowError for a whole number past 1.8e308."""
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


_NOTED = rpg_notes.NOTED                                 # Round RPG16d: one set for every module's notes (rpg.notes)


def _note_once(what, text):
    """Round RPG16b: say a data problem once a run in crash_log.txt (rpg/ has no pygame; crashlog is the program's own).
    Round RPG16d: rpg.notes.once, which events, rare spawns and the save use too."""
    rpg_notes.once(what, text)


class Mover:
    """Round RPG17 (Karl 24 Oct, S1 "(b) people walk off and on, animated"): a person walking from one of their places to another
    (or in from a tile out of sight, or off to one) along `route`, a list of tiles, at PERSON_TILES_PER_SECOND. They wait while the
    next tile is under the walker's feet. `pos` is where they are drawn, in tiles (the top left of the tile they would stand on)."""

    def __init__(self, person, group, route):
        self.person, self.group, self.route = person, group, [tuple(t) for t in route]
        self.pos = [float(self.route[0][0]), float(self.route[0][1])]
        self.leg = 1                           # the index of the tile walked to next

    @property
    def done(self):
        return self.leg >= len(self.route)

    def tile(self):
        return (int(round(self.pos[0])), int(round(self.pos[1])))

    def step(self, dt, feet=()):
        left = PERSON_TILES_PER_SECOND * max(0.0, float(dt))
        while left > 1e-9 and not self.done:
            goal = self.route[self.leg]
            if goal in feet:
                break                          # never onto the walker: wait for it to go
            dx, dy = goal[0] - self.pos[0], goal[1] - self.pos[1]
            d = abs(dx) + abs(dy)
            if d <= left:
                self.pos = [float(goal[0]), float(goal[1])]
                left -= d
                self.leg += 1
            else:
                self.pos[0] += left * (dx / d)
                self.pos[1] += left * (dy / d)
                left = 0.0
        return self.done

    def drawn(self):
        """What rpg_map_view.draw_person needs: the marker's cell, and where to draw it (pos, in tiles)."""
        return dict(self.person, pos=tuple(self.pos), walking=True)


def route(area, start, goal=None, out_of=None, most=WALK_ROUTE_MOST):
    """Round RPG17: the shortest way over free tiles (four directions) from `start` to `goal`, or - goal None - to the nearest
    free tile outside the box `out_of` (col0, row0, col1, row1): where a person comes in from or walks off to, out of sight.
    A list of tiles from start to the end, or None when there is no such way (within `most` tiles)."""
    start = tuple(start)
    if goal is not None and tuple(goal) == start:
        return [start]
    prev = {start: None}
    todo = [start]
    i = 0
    while i < len(todo):
        here = todo[i]
        i += 1
        done = (here == tuple(goal)) if goal is not None else not daynight.in_view(here, out_of)
        if done:
            way = []
            while here is not None:
                way.append(here)
                here = prev[here]
            return way[::-1] if len(way) <= most + 1 else None
        for dc, dr in DIRECTIONS:
            nxt = (here[0] + dc, here[1] + dr)
            if nxt in prev or area.blocked(*nxt):
                continue
            prev[nxt] = here
            todo.append(nxt)
    return None


class Camera:
    """What part of the map the screen shows: a whole-number zoom (pixel art stays sharp), following the walker, never showing
    past the map's edge (a map smaller than the screen is centred). Round RPG11: this is the map's camera again (RPG6's
    RoomCamera is gone), and it stops on whole map pixels, so every tile and sprite stays on the same pixel grid while you walk."""

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
        self.x = float(round(self.x))                         # whole map pixels: no shimmer while walking (Round RPG11)
        self.y = float(round(self.y))

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

def map_names(folder=None):
    folder = folder or MAPS_DIR
    try:
        return sorted(f[:-4] for f in os.listdir(folder) if f.endswith(".tmx"))
    except OSError:
        return []


_AREAS = {}


def load_area(name, folder=None):
    folder = folder or MAPS_DIR
    key = (os.path.abspath(folder), name)
    if key not in _AREAS:
        path = os.path.join(folder, name + ".tmx")
        if not os.path.exists(path):
            raise MapError(f"No map called {name!r}.")
        _AREAS[key] = Area(name, tmx.read_map(path))
    return _AREAS[key]


def forget_areas():
    _AREAS.clear()


def town_map(town_id, folder=None):
    """The name of the map a town is on, or None."""
    folder = folder or MAPS_DIR
    for name in map_names(folder):
        try:
            if town_id in load_area(name, folder).towns():
                return name
        except (tmx.TmxError, MapError):
            continue
    return None


def phase_note(phase):
    """Round RPG16: the map's line when the phase changes while you walk ("Night falls.")."""
    try:
        return rpg_events.phase_notes().get(phase)
    except (OSError, ValueError):
        return None


def facing_of(w, a, present=None):
    """Round RPG15b: (person id or None, the counter tile talked across or None). Round RPG16: present - see Area.npc_at."""
    dx, dy = {"down": (0, 1), "up": (0, -1), "left": (-1, 0), "right": (1, 0)}[w.facing]
    reach_x, reach_y = w.hw + a.tw / 2, w.hh + a.th / 2
    col, row = a.tile_at(w.x + dx * reach_x, w.y + dy * reach_y)
    nid = a.npc_at(col, row, present)
    if nid is not None:
        return nid, None
    if a.is_counter(col, row):
        nid = a.npc_at(col + dx, row + dy, present)
        if nid is not None:
            return nid, (col, row)
    return None, None


OLD_SAVE_NOTE = ("The Adventure's maps were remade, so this game goes on from Lantern Hollow's inn. Your cards, gold and decks "
                 "are kept; the foes you had beaten are back.")


def move_old_save(world, start_town, folder=None):
    """Round RPG12 (the spec's "old saves"): a save whose map is gone - RPG5-RPG11's sample_region and sample_dungeon - goes on
    inside the start town's inn (INN_SPAWN, else the town's own spawn). "cleared" and "beaten" are emptied: they name spots on
    the old maps. Everything else in the save is kept. Changes `world` and returns OLD_SAVE_NOTE, or None when nothing moved
    (the map is there, or it is a town's id, or there is no map yet)."""
    folder = folder or MAPS_DIR
    where = world.get("map")
    if not where or where in map_names(folder) or town_map(where, folder):
        return None
    name = town_map(start_town, folder) if start_town else None
    if name is None:
        return None                                   # no start town on these maps: Explorer.place falls back as before
    area = load_area(name, folder)
    xy = area.spawn(INN_SPAWN) if INN_SPAWN in area.spawns else area.spawn(start_town)
    world.update(map=name, x=xy[0] if xy else None, y=xy[1] if xy else None, cleared=[], beaten=[])
    world.pop("in_town", None)
    return OLD_SAVE_NOTE


def check_all(folder=None, kinds=None, towns=None):
    """{map name: [problems]} for every map in the folder, the exits checked against the other maps."""
    folder = folder or MAPS_DIR
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
    "cleared", "beaten", and (Round RPG11) "facing".

    step() walks in four directions (four_way), wakes the foes near you and moves them (Round RPG11: no rooms; the camera
    follows the walker - rpg.explore.Camera)."""

    def __init__(self, save, start_town=None, folder=None):
        self.save = save
        self.folder = folder or MAPS_DIR        # Round RPG12: read when made, so tests can point MAPS_DIR at their fixtures
        self.start_town = start_town
        self.area = None
        self.walker = None
        self.foes = {}
        self.held = (0, 0)                # what was pressed last frame (four_way)
        self.came_from = None             # Round RPG12: the town trigger walked into last (a counter: its tab and spawn)
        self.axis = None
        self.presence = None              # Round RPG16: who and what is out at this hour (rpg.daynight.Presence)
        self.view_half = VIEW_HALF        # ... half the view in tiles (rpg_screens sets it from the camera each frame)
        self.notes = []                   # ... lines for the map's message line: a phase note, a rare's "seen" line
        self.movers = {}                  # Round RPG17: {group: Mover} - people walking between their places, in sight
        self._phase = None
        self.place()

    # where you are

    def place(self):
        """Put the walker where the save says: a map and a spot on it; outside a town; else the start town; else the first
        map's start."""
        w = self.save.world
        where = w.get("map")
        wake = w.pop("spawn", None)                         # Round RPG14: a lost duel wakes you at this spawn (the inn)
        if where in map_names(self.folder):
            area = load_area(where, self.folder)
            if wake and wake in area.spawns:
                self._use(area, Walker(area, *area.spawn(wake)))
                return
            x, y = w.get("x"), w.get("y")
            if x is not None and y is not None:
                walker = Walker(area, x, y)
                if not area.box_blocked(walker.box()):
                    # Round RPG15c (review N2): a boss won't let you leave, so a game closed on his encounter screen opens in his
                    # box with him still there: he fires again at once (any other box you stand in waits until you leave it).
                    walker.inside -= {t.spot for t in area.triggers if t.kind == "encounter" and t.encounter == "boss"}
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

    def facing_npc(self):
        """Round RPG14: the person the walker faces - on the tile in front of it, or across a counter (Round RPG15b: a counter tile,
        never a wall - the slice walk found Marta, Wendel and Ysolde talked to through walls). None when nobody is there.
        Round RPG16: only the people there at this hour."""
        present = self.present_keys() if hasattr(self, "present_keys") else None    # a stand-in (tests): every marker
        return facing_of(self.walker, self.area, present)[0]

    def facing_shop(self):
        """Round RPG15b: the shop (a town trigger) on the counter between the walker and the person it faces, or None. Talking to a
        shopkeeper opens their shop (rpg_screens.talk_do)."""
        nid, counter = facing_of(self.walker, self.area, self.present_keys())
        if nid is None or counter is None:
            return None
        return next((t for t in self.area.triggers if t.kind == "town" and self.area.tile_at((t.box[0] + t.box[2]) / 2,
                                                                                        (t.box[1] + t.box[3]) / 2) == counter), None)


    def _spawn(self, area, name):
        xy = area.spawn(name) or area.spawn()
        if xy is None:
            raise MapError(f"Map {area.name!r} has no spawn.")
        return xy

    def _use(self, area, walker):
        if self.walker is not None:
            walker.facing = self.walker.facing                 # through an exit you face the way you were going
        elif self.save.world.get("facing") in FACINGS:
            walker.facing = self.save.world["facing"]
        self.area, self.walker = area, walker
        self.foes = {t.spot: Foe(area, t) for t in area.triggers if t.kind == "encounter"}
        self.movers = {}                                          # Round RPG17: nobody is walking on a new map
        self.presence = daynight.Presence(self._items(area))     # Round RPG16: a new map sets everything at once
        self._phase = None
        self.refresh(force=True)
        self._wake_near()
        self.store()

    # Round RPG16: day and night

    def _items(self, area):
        items = [daynight.Item(p["key"], p["when"], p["tile"], group=p.get("id")) for p in area.scheduled()]   # RPG16b: R3
        for t in area.triggers:
            if t.kind != "encounter" or not (t.when or t.rare):
                continue
            tile = area.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
            if t.rare:
                rid = t.rare
                items.append(daynight.Item(t.spot, t.when, tile, test=lambda world, minutes, rid=rid: (
                    hasattr(self.save, "stable_rng") and rpg_rare.is_out(self.save, rid, minutes))))
            else:
                items.append(daynight.Item(t.spot, t.when, tile))
        return items

    def view_box(self):
        """The tiles in view, plus a margin: (col0, row0, col1, row1). Round RPG16b (review R1): the camera stops at the map's edge
        (Camera.follow), so the box does too - near an edge it is shifted inside the map, and a map narrower (or shorter) than the
        view is in view whole."""
        a = self.area
        c, r = a.tile_at(self.walker.x, self.walker.y)
        hx, hy = self.view_half
        cols, rows = max(1, a.width // a.tw), max(1, a.height // a.th)

        def span(at, half, n):
            if 2 * half + 1 >= n:
                return 0, n - 1
            lo = min(max(at - half, 0), n - 1 - 2 * half)
            return lo, lo + 2 * half

        c0, c1 = span(c, hx, cols)
        r0, r1 = span(r, hy, rows)
        return (c0 - VIEW_MARGIN, r0 - VIEW_MARGIN, c1 + VIEW_MARGIN, r1 + VIEW_MARGIN)

    def refresh(self, force=False):
        """Bring who and what is out up to date with the clock and the flags: changes happen out of sight (force: all at once -
        a new map, a load, waking at the inn). Says a phase change on an outdoor map, and a rare that came out, in self.notes."""
        if self.presence is None or self.walker is None:
            return []
        world = self.save.world
        now = clock.now(world)
        a = self.area
        here = a.tile_at(self.walker.x, self.walker.y)
        x0, y0, x1, y1 = self.walker.box()                # RPG16c (review S2): every tile under the walker's feet, not only its middle
        feet = {(c, r) for c in range(int(math.floor((x0 + EPS) / a.tw)), int(math.floor((x1 - EPS) / a.tw)) + 1)   # RPG16d
                for r in range(int(math.floor((y0 + EPS) / a.th)), int(math.floor((y1 - EPS) / a.th)) + 1)} | {here}
        # ... (review T8): by Walker._blocked's own rule - a box within EPS of a tile's edge isn't over it, so that tile stays a wall
        stands = {spot: a.tile_at(f.x, f.y) for spot, f in self.foes.items()}   # RPG16b (review R2): where each foe stands now
        if force:
            self.movers = {}                              # RPG17: a forced update puts everyone in place at once
        view = self.view_box()
        changed = self.presence.update(world, now, view, feet, force=force, tiles=stands,
                                       walk=lambda g, leave, arrive: self._start_walk(g, leave, arrive, view, feet))
        for key in changed:
            f = self.foes.get(key)
            if f is not None and not self.presence.is_present(key):
                f.reset()                                 # ... a foe that goes goes home, and comes back there (out of view)
        self.walker.blockers = ({p["tile"] for p in a.scheduled() if p["key"] in self.presence.present}
                                | {m.tile() for m in self.movers.values()}) - feet   # never the walker's own tile (R4);
        # ... RPG17: a person walking is in the way too, where they are now
        phase = clock.phase(now)
        if self._phase is not None and phase != self._phase and a.outdoor and not force:
            note = phase_note(phase)
            if note:
                self.notes.append(note)
        self._phase = phase
        for key in changed:
            f = self.foes.get(key)
            if f is not None and f.t.rare and self.presence.is_present(key):
                seen = (rpg_rare.spawns().get(f.t.rare) or {}).get("seen")
                if isinstance(seen, str) and seen and not force and key not in self.gone():   # RPG16b (N2): only one that is
                    # drawn; RPG16d: only words (the map's message line draws this)
                    self.notes.append(seen)
        return changed

    def _feet(self):
        a, w = self.area, self.walker
        x0, y0, x1, y1 = w.box()
        return {(c, r) for c in range(int(math.floor((x0 + EPS) / a.tw)), int(math.floor((x1 - EPS) / a.tw)) + 1)
                for r in range(int(math.floor((y0 + EPS) / a.th)), int(math.floor((y1 - EPS) / a.th)) + 1)} | {a.tile_at(w.x, w.y)}

    def _start_walk(self, group, leave, arrive, view, feet):
        """Round RPG17 (S1 (b)): Presence asks whether a person's change in sight can be walked: from the place they leave (or a
        tile out of sight) to the place they go to (or a tile out of sight). False (no way there, or nobody to draw): they wait,
        out of sight, as before RPG17."""
        a = self.area
        people = {p["key"]: p for p in a.people}
        src = leave[0].tile if leave else None
        dst = arrive[0].tile if arrive else None
        person = people.get((leave or arrive)[0].key)
        if person is None or (src is None and dst is None):
            return False
        if src is not None and dst is not None:
            way = route(a, src, dst)
        elif dst is not None:                             # coming on: from the nearest tile out of sight, to their place
            way = route(a, dst, out_of=view)
            way = way[::-1] if way else None
        else:                                             # walking off: to the nearest tile out of sight, then gone
            way = route(a, src, out_of=view)
        if not way:
            return False
        if dst is not None:
            person = people.get(arrive[0].key, person)    # drawn as the place they walk to has them (its cell)
        self.movers[group] = Mover(person, group, way)
        return True

    def step_movers(self, dt):
        """Round RPG17: the people walking between their places move on by dt seconds; one who arrives is in place (and can be
        talked to), one who walked off is gone. Returns the groups that arrived."""
        if not self.movers or self.presence is None or self.walker is None:
            return []
        feet = self._feet()
        done = []
        for g, m in list(self.movers.items()):
            if m.step(dt, feet):
                del self.movers[g]
                self.presence.arrived(g)
                done.append(g)
        return done

    def walking_people(self):
        """Round RPG17: the people walking now, as rpg_map_view.draw_person draws them (their marker with "pos")."""
        return [m.drawn() for m in self.movers.values()]

    def present_keys(self):
        """The keys of the people and foes who come and go that are out now (None before a map is set: every one counts)."""
        return None if self.presence is None else set(self.presence.present)

    def is_out(self, key):
        p = self.presence
        return p is None or key in p.present or all(it.key != key for it in p.items)

    def present_people(self):
        """The person markers there now: every one without `when`, and those with it whose time it is."""
        return [p for p in self.area.people if not p["when"] or self.is_out(p["key"])]

    def take_notes(self):
        out, self.notes = self.notes, []
        return out

    def store(self):
        """Write where the walker is, and which way it faces, into the save (whole pixels)."""
        x, y = self.walker.resting_place()
        self.save.world.update(map=self.area.name, x=x, y=y, facing=self.walker.facing)

    # what's gone

    def gone(self):
        w = self.save.world
        return set(w.get("cleared") or ()) | set(w.get("beaten") or ())

    def skipped(self):
        """Round RPG16: the spots that can't fire now - beaten or cleared, or a foe whose hour it isn't."""
        return self.gone() | {spot for spot in self.foes if not self.is_out(spot)}

    def encounters(self):
        """The foes still on this map and out at this hour, where they are now (for the screen to draw)."""
        skip = self.skipped()
        return [f for f in self.foes.values() if f.spot not in skip]

    def active_foes(self):
        """Round RPG11: the foes near the walker (they move)."""
        return [f for f in self.encounters() if f.active]

    def _wake_near(self):
        """Round RPG11: a foe within ACTIVE_TILES of the walker moves; one further away stands where it is."""
        reach = ACTIVE_TILES * max(self.area.tw, self.area.th)
        w = self.walker
        for f in self.encounters():
            near = math.hypot(f.home[0] - w.x, f.home[1] - w.y) <= reach
            if near and not f.active:
                f.wake()
            elif not near and f.active:
                f.still()

    def _boxes(self):
        return {f.spot: f.box for f in self.encounters()}

    # walking

    def _fire(self, fired):
        for t in fired:
            if t.kind == "exit":
                area = load_area(t.to_map, self.folder)
                self._use(area, Walker(area, *self._spawn(area, t.to_spawn)))
                return ("map", area.name)
            if t.kind == "town":
                self.came_from = t
                return ("town", t.town)
            if t.kind == "encounter":
                return ("encounter", t)
        return None

    def step(self, dx, dy, dt):
        """Walk; returns what happened, or None: ("encounter", trigger) - start that duel (duel.start(..., spot=trigger.spot,
        once=trigger.once)); ("town", town id) - open the town; ("map", name) - you went through an exit and are on that map
        now; Round RPG13: ("random", rpg.encounters.RandomFoe) - a random fight on a map that has them. The save's place is
        updated either way. (dx, dy) is what is pressed; it is walked in four directions (four_way)."""
        mdx, mdy, self.axis = four_way(dx, dy, self.held, self.axis)
        self.held = (dx, dy)
        if not mdx and not mdy:
            self.walker.moving = False
        before = self.walker.walked
        fired = self.walker.move(mdx, mdy, dt, skip=self.skipped(), boxes=self._boxes(), align=True)
        result = self._fire(fired)
        if result is not None and result[0] == "map":
            return result
        if result is None:
            self._wake_near()
            for f in self.active_foes():
                f.avoid = self.walker.blockers               # Round RPG16c (review N5): the people out now are walls for foes too
                f.step(dt)
            result = self._fire(self.walker.recheck(self.skipped(), self._boxes()))
        if result is None:                                   # Round RPG13: ("random", foe) on a map with random fights
            result = self._random_fight((self.walker.walked - before) / self.area.tw)
        if result is None:                                   # Round RPG16: ("event", id) on a map with random events
            result = self._event((self.walker.walked - before) / self.area.tw)
        self.step_movers(dt)                                 # Round RPG17: people walking between their places
        self.refresh()
        self.store()
        return result

    def _event(self, steps):
        a = self.area
        if not a.event_rate or steps <= 0 or not hasattr(self.save, "rng"):     # a stand-in save (tests) rolls nothing
            return None
        try:
            eid = rpg_events.step(self.save, a.name, a.tile_at(self.walker.x, self.walker.y), a.event_rate, steps=steps)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:     # RPG16b (R6), RPG16c (S3): a broken events.json never stops a walk
            _note_once("events", f"rpg/data/events.json can't be read: {type(e).__name__}: {e}")
            return None
        return ("event", eid) if eid else None

    def _random_fight(self, steps):
        """Round RPG13: count `steps` (tiles walked) off the save's world["danger"]; at 0 a random fight starts and the next one is
        drawn (rpg.encounters.next_gap). None on a map without random fights, or when nothing was walked."""
        a = self.area
        if not a.fight_rate or steps <= 0:
            return None
        w = self.save.world
        danger = w.get("danger")
        bad = isinstance(danger, bool) or not isinstance(danger, (int, float)) or danger < 0
        if bad or not _finite(danger):              # RPG16d (review T10): no Infinity / NaN, nor a whole number past a float
            danger = encounters.next_gap(self.save.rng("encounter:gap"), a.fight_rate)
        danger -= steps
        if danger > 1e-9:
            w["danger"] = round(danger, 6)
            return None
        kind = encounters.pick(self.save.rng("encounter:kind"), a.fight_table)
        w["danger"] = encounters.next_gap(self.save.rng("encounter:gap"), a.fight_rate)
        return ("random", encounters.RandomFoe(kind))

    def leave_town(self, town_id):
        """Back on the map, outside the town (its spawn) - Round RPG12: or at the spawn the counter you came in by names."""
        name = town_map(town_id, self.folder)
        if name is None:
            raise MapError(f"No map has the town {town_id!r}.")
        area = load_area(name, self.folder)
        came = self.came_from
        spot = came.back if came is not None and came.town == town_id and came.back in area.spawns else town_id
        self.came_from = None
        self._use(area, Walker(area, *self._spawn(area, spot)))
