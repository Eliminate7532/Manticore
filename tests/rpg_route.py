# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG6, for the tests: walk an Explorer to a tile the way a player would - pressing one direction at a time, 1/60 s a
frame - along the shortest way round the walls (a breadth-first search over the map's tiles). Rooms, slides and wandering foes
are left to the engine; this only chooses which key to hold."""
from collections import deque

FRAME = 1 / 60
NEAR = 0.6                     # px: close enough to a tile's middle to turn


def tile_route(area, start, goal, avoid=()):
    """The tiles from `start` to `goal` (both included), 4 directions, never through a wall or a tile in `avoid` (the goal
    excepted); None when there is no way."""
    prev = {start: None}
    todo = deque([start])
    while todo:
        c = todo.popleft()
        if c == goal:
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dx, c[1] + dy)
            if n in prev or area.blocked(*n) or (n in avoid and n != goal):
                continue
            prev[n] = c
            todo.append(n)
    if goal not in prev:
        return None
    out, c = [], goal
    while c is not None:
        out.append(c)
        c = prev[c]
    return out[::-1]


def trigger_tiles(area, but=()):
    """Every tile a trigger's box covers (towns, exits, standing foes), except the spots in `but`."""
    out = set()
    for t in area.triggers:
        if t.spot in but:
            continue
        x0, y0, x1, y1 = t.box
        for r in range(int(y0 // area.th), int((y1 - 1e-6) // area.th) + 1):
            for c in range(int(x0 // area.tw), int((x1 - 1e-6) // area.tw) + 1):
                out.add((c, r))
    return out


def middle(area, tile):
    return (tile[0] * area.tw + area.tw / 2, tile[1] * area.th + area.th / 2)


def walk_to_tile(ex, goal, limit=6000, stop_at_event=True):
    """Walk to the middle of tile `goal` (a (col, row), or a function returning one - re-read at every tile, to chase something
    that moves). Other triggers' tiles are walked round. Returns (events, frames): the events on the way; it stops at the first
    one (or only at a map change when stop_at_event is False), at the goal, or after `limit` frames."""
    events = []
    frames = 0
    area = ex.area
    while frames < limit:
        target = goal() if callable(goal) else goal
        here = area.tile_at(ex.walker.x, ex.walker.y)
        if ex.sliding():
            e = ex.step(0, 0, FRAME)
            frames += 1
            if e:
                events.append(e)
                if stop_at_event or e[0] == "map":
                    return events, frames
            continue
        route = tile_route(area, here, target, avoid=trigger_tiles(area) - {target})
        if route is None:
            raise AssertionError(f"no way from {here} to {target} on {area.name}")
        mx, my = middle(area, here)
        ddx, ddy = mx - ex.walker.x, my - ex.walker.y
        toward_x = (1 if ddx > 0 else -1, 0)
        toward_y = (0, 1 if ddy > 0 else -1)
        if len(route) == 1:                                   # on the goal's tile: to its middle
            if abs(ddx) <= NEAR and abs(ddy) <= NEAR:
                return events, frames
            press = toward_x if abs(ddx) > NEAR else toward_y
        else:
            sx, sy = route[1][0] - here[0], route[1][1] - here[1]
            if sx:                                            # across first onto this tile's middle line, then along
                press = toward_y if abs(ddy) > NEAR else (sx, 0)
            else:
                press = toward_x if abs(ddx) > NEAR else (0, sy)
        e = ex.step(press[0], press[1], FRAME)
        frames += 1
        if e:
            events.append(e)
            if stop_at_event or e[0] == "map":
                return events, frames
            if ex.area is not area:
                return events, frames
    raise AssertionError(f"didn't reach {goal} in {limit} frames (at {ex.area.tile_at(ex.walker.x, ex.walker.y)})")


def next_press(ex, goal):
    """Round RPG8: the key walk_to_tile would hold now on the way to tile `goal` ((col, row), or a function returning one), as
    (dx, dy) - for driving the Adventure's screen (rpg_screens.AdventureFlow.walk_tick), which does the stepping itself. None
    once the walker stands in the goal tile's middle; (0, 0) while the view slides to the next room."""
    if ex.sliding():
        return (0, 0)
    area = ex.area
    target = goal() if callable(goal) else goal
    here = area.tile_at(ex.walker.x, ex.walker.y)
    route = tile_route(area, here, target, avoid=trigger_tiles(area) - {target})
    if route is None:
        raise AssertionError(f"no way from {here} to {target} on {area.name}")
    mx, my = middle(area, here)
    ddx, ddy = mx - ex.walker.x, my - ex.walker.y
    toward_x = (1 if ddx > 0 else -1, 0)
    toward_y = (0, 1 if ddy > 0 else -1)
    if len(route) == 1:
        if abs(ddx) <= NEAR and abs(ddy) <= NEAR:
            return None
        return toward_x if abs(ddx) > NEAR else toward_y
    sx, sy = route[1][0] - here[0], route[1][1] - here[1]
    if sx:
        return toward_y if abs(ddy) > NEAR else (sx, 0)
    return toward_x if abs(ddx) > NEAR else (0, sy)
