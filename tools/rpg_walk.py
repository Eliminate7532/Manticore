# SPDX-License-Identifier: GPL-3.0-or-later
"""A developer's window onto the RPG's map engine (Round RPG5) - not the game's RPG screen, which isn't built yet.

    python tools/rpg_walk.py [--map ashfen_outskirts] [--size 1280x800]
    python tools/rpg_walk.py --walk "right 1.5, down 2" --screenshot out.png      (no window needed: SDL's dummy driver)

Arrow keys or WASD walk; Esc closes. Nothing is saved: the walk uses a save-shaped stand-in, so no RPG save is read or written.
Walking into a foe says who you would fight and counts it as won (it disappears), a town says which, an exit takes you through.
Round RPG6: one room at a time with side panels; four directions. Round RPG11: the camera follows the walker
(rpg.explore.Camera, whole map pixels); no rooms, no panels; DawnLike's people walk.
"""
import argparse
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

KEYS = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}


def stand_in_save(map_name=None, town="town_1"):
    return types.SimpleNamespace(world={"region": 1, "map": map_name or town, "x": None, "y": None, "last_town": town,
                                        "towns_visited": [town], "cleared": [], "beaten": []},
                                 gold=100, deck_name="Starting deck (stand-in)", cards=60)


def parse_walk(text):
    """"right 1.5, down 2" -> [((1, 0), 1.5), ((0, 1), 2.0)]; "right+down 1" walks diagonally."""
    out = []
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        words = part.split()
        if len(words) != 2:
            raise ValueError(f"{part!r}: say a direction and seconds, like 'right 1.5'")
        dx = dy = 0
        for d in words[0].lower().split("+"):
            if d not in KEYS:
                raise ValueError(f"{part!r}: {d!r} isn't left, right, up or down")
            dx += KEYS[d][0]
            dy += KEYS[d][1]
        out.append(((dx, dy), float(words[1])))
    return out


def describe(event):
    if event is None:
        return None
    kind, what = event
    if kind == "encounter":
        return f"{what.obj.name or what.enemy}: you would fight {what.enemy} ({what.encounter}) - counted as won here"
    if kind == "town":
        return f"You reach {what} (the town screen isn't built yet)"
    return f"You go through to {what}"


def run(argv=None):
    ap = argparse.ArgumentParser(description="Walk the RPG's maps (a developer's tool).")
    ap.add_argument("--map", default=None, help="start on this map (default: outside the start town)")
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--walk", default="", help='walk by itself first, e.g. "right 1.5, down 2"')
    ap.add_argument("--screenshot", default=None, help="save a picture after the walk and close")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--maps", default=None, help="the folder of maps to walk (default: rpg/maps; the tests pass their fixtures)")
    a = ap.parse_args(argv)
    if a.screenshot:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    import gfx
    import rpg_map_view
    from rpg import explore

    w, h = (int(v) for v in a.size.lower().split("x"))
    pygame.init()
    screen = pygame.display.set_mode((w, h))
    pygame.display.set_caption("Manticore - RPG map (developer's view)")
    save = stand_in_save(a.map)
    ex = explore.Explorer(save, start_town="town_1", folder=a.maps)
    views = {}
    cam = explore.Camera(w, h, explore.Camera.zoom_for(h, ex.area.th))
    font = gfx.get_font(max(14, h // 45))
    line, line_until = None, 0.0
    script = parse_walk(a.walk)
    clock = pygame.time.Clock()
    t0 = time.monotonic()
    simulated = 0.0

    def note(event):
        nonlocal line, line_until
        text = describe(event)
        if text:
            print(text)
            line, line_until = text, time.monotonic() + 4.0
        if event and event[0] == "encounter":
            save.world.setdefault("beaten", []).append(event[1].spot)

    def frame(now):
        view = views.get(ex.area.name)
        if view is None:
            view = views[ex.area.name] = rpg_map_view.MapView(ex.area)
        cam.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        view.draw(screen, cam, ex.walker, ex.encounters(), now)
        if line and time.monotonic() < line_until:
            gfx.draw_text(screen, line, 12, h - 12, font, gfx.GOLD, anchor="bottomleft", shadow=True)

    for (dx, dy), secs in script:                  # the scripted walk runs in fixed 1/60 s steps: the same every time
        steps = int(round(secs * 60))
        for _ in range(steps):
            note(ex.step(dx, dy, 1 / 60))
            simulated += 1 / 60
    if a.screenshot:
        frame(simulated)
        pygame.image.save(screen, a.screenshot)
        print("saved", a.screenshot, "at", ex.area.name, ex.walker.resting_place())
        pygame.quit()
        return 0
    running = True
    while running:
        dt = clock.tick(a.fps) / 1000.0
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                running = False
        k = pygame.key.get_pressed()
        dx = (k[pygame.K_RIGHT] or k[pygame.K_d]) - (k[pygame.K_LEFT] or k[pygame.K_a])
        dy = (k[pygame.K_DOWN] or k[pygame.K_s]) - (k[pygame.K_UP] or k[pygame.K_w])
        note(ex.step(dx, dy, min(dt, 0.1)))
        frame(time.monotonic() - t0)
        pygame.display.flip()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(run())
