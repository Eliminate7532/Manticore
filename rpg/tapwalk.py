# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG-TAP (Karl, 27 Oct 2026, from an F8 report sent on his Surface Pro 11: "rpg mode needs click to move and click to
interact so i can play on surface with only stylus"; his answers: "Walk to the tapped spot" and "Walk up and act"): the Adventure's
map played with a pen or a mouse alone. No pygame here; rpg_screens.AdventureFlow turns a tap into a map point and drives a TapWalk
every tick.

What a tap on the map means (goal_at), in this order:
  foe      a foe out now, tapped on itself (its box, or the tile-sized square round it): walk up to it and into it - the duel
           starts the way it does with the keys (Walker.move touching its box).
  person   a person out now - standing (Area.people) or walking (Explorer.movers) - on the tapped tile, or the person behind a
           tapped counter tile: walk to a free tile beside them (or across their counter), face them, and talk (as Enter does).
  door     an exit or a town (a shop's door) whose box holds the tapped point: walk into it - the map changes or the town opens
           as with the keys.
  tile     anything else you could stand on: walk to its middle.
  chest    Round PROG2: a closed treasure chest whose box holds the tapped point: walk into it - it opens as with the keys (a
           "door" goal: the walk ends on its tile).
A wall, or a place with no way to it, says so (NO_WAY / WALL) and nothing moves. The Adventure has no signs.

How it walks: a way of whole tiles (route, breadth first, four directions) from the tile under the walker to the goal, found again
at every step, so a person or a foe that moves is followed. The walker is moved by Explorer.step - the code the arrow keys use, with
the same (dx, dy) a held key gives - for exactly as long as it takes to reach the next tile's middle line, so random fights,
random events, triggers, doors, map changes and the clock behave as when walking with the keys. Walls of a way: the map's walls,
the people out now (Walker.blockers), every tile under a foe's box (only the tapped foe is ever walked into - "a walk anywhere else
must never step into a foe"), and every exit or town that isn't the goal (so a walk never leaves the map or opens a shop by
accident). A step whose end would touch a foe that isn't the goal is not taken: the walk waits and looks for another way (a foe
that walks into a walker standing still still starts its duel, as with the keys). With no way for WAIT_SECONDS (a person or foe in
a narrow place), the walk gives up and says so.

My estimates: WAIT_SECONDS, TAP_SLACK."""
from collections import deque

from rpg import explore

NO_WAY = "There's no way there."
WALL = "You can't walk there."
BLOCKED = "The way is blocked."
WAIT_SECONDS = 2.0          # a walk with no way (something moved into it) waits this long before giving up - my estimate
TAP_SLACK = 0.5             # a foe counts as tapped within this many tiles of its middle (a pen is less exact) - my estimate
AT = 0.05                   # map px: on a tile's middle line
STEPS = {(0, 1): "down", (0, -1): "up", (1, 0): "right", (-1, 0): "left"}     # the way a walker faces to look along (dx, dy)


class Goal:
    """What a tap asked for. kind: "tile" | "person" | "foe" | "door"; tile: the tapped tile (a person's or foe's is followed);
    nid: a person's id; spot: a foe's or a door's trigger spot; area: the map's name (another map ends the walk)."""

    __slots__ = ("kind", "tile", "nid", "spot", "area")

    def __init__(self, kind, tile, area, nid=None, spot=None):
        self.kind, self.tile, self.area, self.nid, self.spot = kind, tuple(tile), area, nid, spot

    def __repr__(self):
        return f"Goal({self.kind}, {self.tile}, {self.nid or self.spot or ''})"


def box_tiles(area, box):
    """Every tile a (x0, y0, x1, y1) box is over."""
    x0, y0, x1, y1 = box
    e = explore.EPS
    return {(c, r) for r in range(int((y0 + e) // area.th), int((y1 - e) // area.th) + 1)
            for c in range(int((x0 + e) // area.tw), int((x1 - e) // area.tw) + 1)}


def middle(area, tile):
    return (tile[0] * area.tw + area.tw / 2, tile[1] * area.th + area.th / 2)


def people_now(ex):
    """{tile: person id} of every person out now: the standing ones (Explorer.present_people) and the walking ones (Mover)."""
    out = {p["tile"]: p["id"] for p in ex.present_people()}
    for m in (getattr(ex, "movers", None) or {}).values():
        if m.person.get("id"):
            out[m.tile()] = m.person["id"]
    return out


def person_tile(ex, nid):
    """Where person `nid` is now (a walking one: the tile they are drawn nearest), or None when they aren't out."""
    for m in (getattr(ex, "movers", None) or {}).values():
        if m.person.get("id") == nid:
            return m.tile()
    for p in ex.present_people():
        if p["id"] == nid:
            return p["tile"]
    return None


def foe_of(ex, spot):
    return next((f for f in ex.encounters() if f.spot == spot), None)


def goal_at(ex, mx, my):
    """(Goal, None) for a tap at map point (mx, my), or (None, why) when there is nothing to walk to there."""
    a = ex.area
    tile = a.tile_at(mx, my)
    for f in ex.encounters():                                        # a foe, tapped on itself
        cx, cy = (f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2
        if abs(mx - cx) <= TAP_SLACK * a.tw and abs(my - cy) <= TAP_SLACK * a.th:
            return Goal("foe", a.tile_at(cx, cy), a.name, spot=f.spot), None
    people = people_now(ex)
    if tile in people:
        return Goal("person", tile, a.name, nid=people[tile]), None
    if a.is_counter(*tile):                                          # a counter: the person behind it
        for dc, dr in STEPS:
            beyond = (tile[0] + dc, tile[1] + dr)
            if beyond in people:
                return Goal("person", beyond, a.name, nid=people[beyond]), None
    shut = closed_chests(ex)
    for t in a.triggers:
        if (t.kind in ("exit", "town") or t.spot in shut) and t.box[0] <= mx < t.box[2] and t.box[1] <= my < t.box[3]:
            if any(not a.blocked(*c) for c in box_tiles(a, t.box)):
                return Goal("door", tile, a.name, spot=t.spot), None
    if a.blocked(*tile) or tile in (ex.walker.blockers or ()):
        return None, WALL
    return Goal("tile", tile, a.name), None


def walls(ex, goal):
    """The tiles a way for `goal` may not cross (besides the map's walls): people out now, the tiles under every foe but the goal,
    and every exit or town but the goal. The tiles the walker stands on are never walls (it can always walk off them)."""
    a, w = ex.area, ex.walker
    out = set(w.blockers or ()) | {m.tile() for m in (getattr(ex, "movers", None) or {}).values()}
    for f in ex.encounters():
        if goal.kind == "foe" and f.spot == goal.spot:
            continue
        out |= box_tiles(a, f.box)
    shut = closed_chests(ex)                  # Round PROG2: a walk never opens a chest it isn't going to
    for t in a.triggers:
        if ((t.kind in ("exit", "town") or t.spot in shut) and not (goal.kind == "door" and t.spot == goal.spot)
                and t.spot not in w.inside):
            out |= box_tiles(a, t.box)
    return out - feet(ex)


def closed_chests(ex):
    """Round PROG2: the spots of the closed treasure chests on the map (rpg.explore.Explorer.chests)."""
    fn = getattr(ex, "chests", None)
    return {t.spot for t, opened in (fn() if fn else ()) if not opened}


def feet(ex):
    a, w = ex.area, ex.walker
    return box_tiles(a, w.box()) | {a.tile_at(w.x, w.y)}


def ends(ex, goal):
    """{tile: facing or None}: where the walk ends. A person: a free tile beside them (facing them) or across their counter; a foe:
    its tiles (walked into); a door: its free tiles; a tile: itself. Empty when the goal is gone (a foe beaten, a person not out)."""
    a = ex.area
    if goal.kind == "tile":
        return {goal.tile: None}
    if goal.kind == "foe":
        f = foe_of(ex, goal.spot)          # the tile under its middle: a walker in that tile's middle always touches its box
        return {} if f is None else {a.tile_at((f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2): None}
    if goal.kind == "door":
        t = next((t for t in a.triggers if t.spot == goal.spot), None)
        return {} if t is None else {c: None for c in box_tiles(a, t.box) if not a.blocked(*c)}
    at = person_tile(ex, goal.nid)
    if at is None:
        return {}
    out = {}
    for (dc, dr), facing in STEPS.items():
        for reach in (1, 2):                              # 2: across a counter (Round RPG15b: a counter tile, never a wall)
            spot = (at[0] - dc * reach, at[1] - dr * reach)
            if reach == 2 and not a.is_counter(at[0] - dc, at[1] - dr):
                continue
            if not a.blocked(*spot):
                out.setdefault(spot, facing)
    return out


def route(area, start, goals, avoid):
    """The shortest way of tiles (four directions) from `start` to any tile in `goals`, never through a wall or a tile in `avoid`
    (a goal tile excepted); None when there is none."""
    start = tuple(start)
    prev = {start: None}
    todo = deque([start])
    while todo:
        here = todo.popleft()
        if here in goals:
            way = []
            while here is not None:
                way.append(here)
                here = prev[here]
            return way[::-1]
        for dc, dr in explore.DIRECTIONS:
            nxt = (here[0] + dc, here[1] + dr)
            if nxt in prev or area.blocked(*nxt) or (nxt in avoid and nxt not in goals):
                continue
            prev[nxt] = here
            todo.append(nxt)
    return None


def plan(ex, goal):
    """(way, ends) now: the way of tiles to the goal (or None), and where it may end."""
    stop = ends(ex, goal)
    if not stop:
        return None, stop
    a, w = ex.area, ex.walker
    return route(a, a.tile_at(w.x, w.y), set(stop), walls(ex, goal)), stop


def next_move(ex, way):
    """(dx, dy, px): the key to hold and how far to walk along `way` before looking again - first onto the middle line of the
    tile it stands on across its way, then along to the next tile's middle. None when it stands in the middle of the last tile."""
    a, w = ex.area, ex.walker
    here = way[0]
    mx, my = middle(a, here)
    ddx, ddy = mx - w.x, my - w.y
    if len(way) == 1:
        if abs(ddx) > AT:
            return (1 if ddx > 0 else -1), 0, abs(ddx)
        if abs(ddy) > AT:
            return 0, (1 if ddy > 0 else -1), abs(ddy)
        return None
    sx, sy = way[1][0] - here[0], way[1][1] - here[1]
    if sx:
        if abs(ddy) > AT:
            return 0, (1 if ddy > 0 else -1), abs(ddy)
        nx, _ny = middle(a, way[1])
        return sx, 0, abs(nx - w.x)
    if abs(ddx) > AT:
        return (1 if ddx > 0 else -1), 0, abs(ddx)
    _nx, ny = middle(a, way[1])
    return 0, sy, abs(ny - w.y)


def touches_foe(ex, goal, dx, dy, px):
    """Would walking px along (dx, dy) touch a foe that isn't the goal (where the foes stand now)?"""
    w = ex.walker
    box = w.box(w.x + dx * px, w.y + dy * px)
    for f in ex.encounters():
        if goal.kind == "foe" and f.spot == goal.spot:
            continue
        if explore.overlaps(box, f.box):
            return True
    return False


class TapWalk:
    """One tap's walk. tick(ex, dt, after) walks it on by dt seconds through ex.step; after(event, part) is called after every
    ex.step with what it returned and the seconds it walked (the clock, the events) and returns True when something happened that
    ends the walk (a duel, a town, a map, an event). tick returns:
        "walking"   still on the way
        "talk"      beside the person, facing them: the caller talks (as Enter does)
        "arrived"   in the middle of the tapped tile (or a door / foe reached without anything happening)
        "done"      after() said something happened
        "gone"      the goal went (another map, a foe beaten or gone, a person not out)
        "blocked"   no way for WAIT_SECONDS
    """

    def __init__(self, goal):
        self.goal = goal
        self.waited = 0.0
        self.steps = 0                      # ex.step calls made for this walk (tests)

    def marker(self, ex):
        """The tile to mark on the map: where the goal is now."""
        g = self.goal
        if g.kind == "person":
            return person_tile(ex, g.nid) or g.tile
        if g.kind == "foe":
            f = foe_of(ex, g.spot)
            return ex.area.tile_at((f.box[0] + f.box[2]) / 2, (f.box[1] + f.box[3]) / 2) if f is not None else g.tile
        return g.tile

    def tick(self, ex, dt, after):
        g = self.goal
        if ex.area is None or ex.area.name != g.area:
            return "gone"
        left = float(dt)
        for _ in range(6):                  # a few legs a tick: a tile's middle reached part way through the tick walks on
            if left <= 1e-9:
                return "walking"
            way, stop = plan(ex, g)
            if not stop:
                return "gone"
            if way is None:
                return self._wait(ex, left, after)
            mv = next_move(ex, way)
            if mv is None:                  # in the middle of the last tile
                facing = stop.get(way[-1])
                if g.kind == "person" and facing is not None:
                    ex.walker.facing = facing
                    if ex.facing_npc() == g.nid:
                        return "talk"
                    return self._wait(ex, left, after)    # they moved on this instant: look again
                if g.kind == "foe":
                    return self._wait(ex, left, after)    # the foe stepped off as we came: follow it
                return "arrived"
            dx, dy, px = mv
            if touches_foe(ex, g, dx, dy, min(px, ex.walker.speed * left)):
                return self._wait(ex, left, after)
            part = min(left, max(px, 0.0) / ex.walker.speed)
            if part <= 1e-9:
                part = min(left, 1e-3)
            self.steps += 1
            event = ex.step(dx, dy, part)
            left -= part
            self.waited = 0.0
            if after(event, part):
                return "done"
            if not ex.walker.moving and px > AT:
                return self._wait(ex, left, after)         # stopped by something the way didn't know of: look again
        return "walking"

    def _wait(self, ex, left, after):
        """Stand still for the rest of the tick (the world goes on, as with no key held); give up after WAIT_SECONDS."""
        if left > 1e-9:
            self.steps += 1
            event = ex.step(0, 0, left)
            if after(event, left):
                return "done"
        self.waited += left
        return "blocked" if self.waited >= WAIT_SECONDS else "walking"
