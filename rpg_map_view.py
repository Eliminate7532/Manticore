# SPDX-License-Identifier: GPL-3.0-or-later
"""Draws an RPG map with pygame (Round RPG5): the tiles the camera sees, the foes still on the map, and the walker. The walking
itself (walls, what you walk into, the camera) is rpg.explore, which has no pygame; this module only draws.

    MapView(area)                       one per map: cuts the tileset pictures into tiles (Tiled's flip bits applied) and keeps
                                        each tile scaled to the camera's zoom
    view.draw(screen, camera, walker, encounters, now)
    marker_colour(kind)                 a foe's marker: its enemy kind's first colour (gfx.MANA_FILL)

Placeholders until the slice's art exists: a foe is a diamond in its colour (a boss is bigger, a dungeon foe has a dark ring),
the walker a gold figure with a shadow. Every colour is a gfx.py token.

Round RPG6 (Karl, 12 Oct 2026: inspired by the original Zelda; the Diablo 1 / Vermis dark palette; rooms with side panels):
    view.draw(...) with an rpg.explore.RoomCamera drew only the room in view (gone in RPG11), and a map with the property
    light is dark but for a light round the walker (LIGHT_BANDS steps, drawn at the map's own pixel size and scaled up, so the
    light has pixel-art edges).
    draw_panels(...) drew the side panels and a minimap of the rooms (gone in RPG11)

Round RPG10: tiles move. Each tile is drawn as tmx.TiledMap.frame_at(gid, now) says: a Tiled animation's frame, and for a
tileset with a "frame2" sheet (DawnLike's pairs) the second sheet every other frame_ms. Only tilesets that animate pay for it.

Round RPG11 (the Adventure remaster: free scrolling, people who walk; claude/SPEC_RPG_SLICE_2026-10-23.md):
  - the walker is a DawnLike class sheet (rpg/data/art.json: 4 directions x 4 walking frames, a frame every WALK_FRAME_TILES
    walked, the standing frame when it stops); each foe kind a DawnLike character (its 2-frame idle, the pair of sheets swapping
    every tmx.FRAME_MS). The drawn shapes are kept for a kind or a file that isn't there.
  - sprites are drawn after the map's layers in order of their feet (the lower on screen, the later), then the layers that stand
    above them: a layer named "above", or with the property above = true.
  - every sprite stands on a whole map pixel, as the camera does, so nothing shimmers while you walk.
  - the darkness of a dark map covers the whole view (RPG6's needed the room camera's viewport).
  - the side panels and the minimap are gone (rpg_screens draws a small corner window instead).
"""
import collections
import json
import math
import os
import zlib

import pygame

import crashlog
import gfx
import paths
from rpg import enemies, explore, tmx

ART_DIR = os.path.join(paths.assets_dir(), "rpg", "dawnlike")
ART_FILE = os.path.join(os.path.dirname(os.path.abspath(enemies.__file__)), "data", "art.json")
WALK_FRAME_TILES = 0.5            # Round RPG11: a walking frame every half tile walked - my estimate
_NOTED = set()                    # Round RPG15c: tile pictures already noted as missing in the crash log (once a run each)
WALK_FRAMES = 2                   # Round RPG17: a person walking between places changes frame this many times as fast as standing


def load_sheet(path, size):
    """Round RPG15c (review R8): a tileset's picture, or - when it is missing or unreadable - a plain sheet of its size, noted
    once in the crash log. Returns (surface, missing?). Before, one missing sheet raised inside every frame, and the game loop
    closed the program after MAX_BAD_FRAMES (about a second)."""
    try:
        img = pygame.image.load(path)
        if pygame.display.get_surface() is not None:
            img = img.convert_alpha()
        return img, False
    except (OSError, pygame.error) as e:
        if path not in _NOTED:
            _NOTED.add(path)
            crashlog.note(f"Adventure: a map picture is missing or unreadable, drawn plain: {path}: {type(e).__name__}: {e}")
        w, h = size
        img = pygame.Surface((max(1, int(w)), max(1, int(h))))
        img.fill(gfx.DB_BG)
        return img, True


def _kind_colours():
    try:
        return {k["id"]: k.get("colour", "") for k in enemies.read_kinds()}
    except (OSError, ValueError, KeyError):
        return {}


_COLOURS = None
DARK_ALPHA = 228                  # Round RPG6: how dark a lit map is away from the light (0-255) - my estimate
LIGHT_BANDS = 4                   # the light's steps from bright to dark - my estimate
DARK_ZONE_ALPHA = 215             # Round RPG14: how dark a dark zone is (the duel hall before the boss) - my estimate
NIGHT = gfx.BACKDROP              # the darkness's colour: RPG6's near-black (Diablo 1 / Vermis dark)
FLICKER_HZ = 6.0                  # Round RPG15: how often a flame may dip - my estimate
FLICKER_ODDS = 3                  # ... one moment in about this many it is at its smaller band - my estimate
MASK_ALPHA_STEP = 4               # Round RPG15c (review R4): a light's mask is made for the darkness rounded UP to this many alpha steps
MASK_KEEP = 48                    # ... and at most this many masks are kept (the least recently used goes) - my estimate


def marker_colour(kind):
    global _COLOURS
    if _COLOURS is None:
        _COLOURS = _kind_colours()
    letters = _COLOURS.get(kind) or "C"
    return gfx.MANA_FILL.get(letters[0], gfx.MANA_FILL["C"])


def _flip(surf, bits):
    """Tiled's flip bits: the diagonal flip (swap x and y) first, then horizontal, then vertical."""
    if bits & tmx.FLIP_D:
        surf = pygame.transform.flip(pygame.transform.rotate(surf, -90), True, False)
    if bits & (tmx.FLIP_H | tmx.FLIP_V):
        surf = pygame.transform.flip(surf, bool(bits & tmx.FLIP_H), bool(bits & tmx.FLIP_V))
    return surf


class MapView:
    def __init__(self, area):
        self.area = area
        self.sheets = {}
        self.moving = set()                        # Round RPG10: firstgids of the tilesets that animate
        self.missing = []                          # Round RPG15c: the tile pictures that couldn't be read (file names)
        for ts in area.map.tilesets:
            imgs = []
            for path in (ts.image, ts.image2):
                if path is None:
                    continue
                img, missing = load_sheet(path, (ts.imagewidth, ts.imageheight))
                if missing:
                    self.missing.append(os.path.basename(path))
                imgs.append(img)
            self.sheets[ts.firstgid] = (ts, imgs)
            if ts.anims or ts.image2:
                self.moving.add(ts.firstgid)
        self.cache = {}
        self.mask_order = collections.OrderedDict()   # Round RPG15c (review R4): the light masks in self.cache, oldest use first
        self.sprites = Sprites()
        self.hero = None                           # Round RPG11: the save's hero (rpg_screens sets it); None = the Warrior
        self.dark_tiles = []                       # Round RPG14: the map's dark zones still dark [(c0, r0, c1, r1)] (rpg_screens)
        self.lit = True                            # Round RPG15: the Adventure's Light setting; False = the plain art
        self.flags = ()                            # ... the story flags set (a light with a flag burns once it is set)
        self.flicker = True                        # ... flames flicker (Animations on)
        self.darkness = None                       # ... how dark it is now (rpg_screens: the clock's); None = the map's own

    def tile(self, gid, bits, zoom, sheet=0):
        key = (gid, bits, zoom, sheet)
        surf = self.cache.get(key)
        if surf is None:
            ts = self.area.map.tileset_for(gid)
            _ts, imgs = self.sheets[ts.firstgid]
            img = imgs[sheet if sheet < len(imgs) else 0]
            x, y, w, h = ts.rect(ts.local(gid))
            surf = img.subsurface(pygame.Rect(x, y, w, h)).copy()
            surf = _flip(surf, bits)
            if zoom != 1:
                surf = pygame.transform.scale(surf, (w * zoom, h * zoom))
            self.cache[key] = surf
        return surf

    def draw(self, screen, camera, walker=None, encounters=(), now=0.0, people=()):
        """Round RPG11: the view is drawn at the map's own pixel size on a canvas (the camera stops on whole map pixels, so
        nothing is lost) and the canvas is scaled up by the zoom once - as pixel-art games do. Drawing every tile at the
        screen's size took about 15 ms a frame at 4096 x 2160, a dark map's light 30 more; this way both are a few ms."""
        z = camera.zoom
        cw, ch = -(-camera.view_w // z), -(-camera.view_h // z)
        flat = explore.Camera(cw, ch, 1)
        flat.x, flat.y = camera.x, camera.y
        canvas = self._surface("canvas", (cw, ch))
        canvas.fill(gfx.BACKDROP)
        self._draw_map(canvas, flat, walker, encounters, now, people)
        if screen.get_size() != (camera.view_w, camera.view_h):
            screen.fill(gfx.BACKDROP)
        if z == 1:
            screen.blit(canvas, (0, 0))
            return
        big = self._surface("big", (cw * z, ch * z))
        pygame.transform.scale(canvas, big.get_size(), big)
        screen.blit(big, (0, 0))

    def _surface(self, name, size):
        """A drawing surface kept between frames (made again when the window's size changes)."""
        key = ("surface", name)
        surf = self.cache.get(key)
        if surf is None or surf.get_size() != size:
            surf = pygame.Surface(size)
            if pygame.display.get_surface() is not None:
                surf = surf.convert()
            self.cache[key] = surf
        return surf

    def _draw_map(self, screen, camera, walker, encounters, now, people=()):
        a = self.area
        layers = [ly for ly in a.map.layers if ly.visible and ly.name.lower() != "collision"]
        above = [ly for ly in layers if is_above(ly)]
        for layer in layers:
            if layer not in above:
                self._draw_layer(screen, camera, layer, now)
        stand = [(f.box[3], 0, f) for f in encounters]
        for p in people:                       # Round RPG16: a person who comes and goes, drawn while there (not on a layer)
            row = p["pos"][1] if p.get("pos") is not None else p["tile"][1]     # Round RPG17: a person walking, where they are
            stand.append(((row + 1) * a.th, 0, ("person", p)))
        if walker is not None:
            stand.append((walker.y + walker.hh, 1, walker))
        for _y, _o, who in sorted(stand, key=lambda t: (t[0], t[1])):     # Round RPG11: the lower feet are drawn later
            if who is walker:
                self.draw_walker(screen, camera, walker, now)
            elif isinstance(who, tuple):
                self.draw_person(screen, camera, who[1], now)
            else:
                self.draw_foe(screen, camera, who, now)
        for layer in above:
            self._draw_layer(screen, camera, layer, now)
        for c0, r0, c1, r1 in self.dark_tiles:          # Round RPG14: a dark zone (the duel hall until the boss is beaten)
            x, y = camera.to_screen(c0 * a.tw, r0 * a.th)
            w, h = (c1 - c0 + 1) * a.tw * camera.zoom, (r1 - r0 + 1) * a.th * camera.zoom
            shade = self.cache.get(("dark_zone", w, h))
            if shade is None:
                shade = self.cache[("dark_zone", w, h)] = pygame.Surface((w, h), pygame.SRCALPHA)
                shade.fill(gfx.DB_BG + (DARK_ZONE_ALPHA,))
            screen.blit(shade, (x, y))
        if self.lit and self.dark_level() > 0:         # Round RPG15: every map has its darkness (the Light setting turns it off)
            self.draw_light(screen, camera, walker, now)
        for f in encounters:                            # Round RPG16: a rare foe's gold ring, over the night so it shows in it
            if getattr(getattr(f, "t", f), "rare", None):
                self.draw_rare_ring(screen, camera, f)

    def draw_rare_ring(self, screen, camera, t):
        """Round RPG16: the gold ring round a rare foe's feet (gfx.RARE_RING), drawn after the darkness."""
        x0, y0, x1, y1 = t.box
        fx, fy = camera.to_screen(round((x0 + x1) / 2), round(y1))
        w = self.area.tw * camera.zoom
        pygame.draw.ellipse(screen, gfx.RARE_RING, (fx - w * 0.5, fy - w * 0.2, w, w * 0.4), max(1, camera.zoom))

    def _draw_layer(self, screen, camera, layer, now):
        a, z = self.area, camera.zoom
        c0, r0, c1, r1 = camera.visible(a.tw, a.th, a.cols, a.rows)
        ms, tiled, moving = int(now * 1000), a.map, self.moving
        for row in range(r0, r1):
            base = row * a.cols
            for col in range(c0, c1):
                gid = layer.data[base + col]
                if not gid:
                    continue
                sheet = 0
                if moving:
                    ts = tiled.tileset_for(gid)
                    if ts is not None and ts.firstgid in moving:
                        gid, sheet = tiled.frame_at(gid, ms)
                img = self.tile(gid, layer.flips.get(base + col, 0), z, sheet)
                screen.blit(img, camera.to_screen(col * a.tw, row * a.th))

    # ---- Round RPG6: the light in a dark map (Round RPG15: every map, with light sources) ----------------------------------------

    def light_mask(self, radius, alpha=DARK_ALPHA, strength=1.0):
        """A (2r x 2r) picture whose alpha is the darkness's at the radius and alpha x (1 - strength) at the middle, in LIGHT_BANDS
        steps (RPG6's mask is alpha DARK_ALPHA, strength 1: clear at the middle).

        Round RPG15c (review R4): on an outdoor map the alpha follows the clock, so one mask per alpha piled up (503 in a day of the
        town). The alpha is rounded UP to MASK_ALPHA_STEP (above the darkness's own fill, so the mask's square edge never shows
        under BLEND_RGBA_MIN) and only MASK_KEEP masks are kept."""
        alpha = min(255, -(-int(alpha) // MASK_ALPHA_STEP) * MASK_ALPHA_STEP)
        key = ("mask", radius, alpha, strength)
        mask = self.cache.get(key)
        if mask is not None:
            self.mask_order.move_to_end(key)
        if mask is None:
            size = 2 * radius
            mask = pygame.Surface((size, size), pygame.SRCALPHA)
            mask.fill((*NIGHT, alpha))
            for band in range(LIGHT_BANDS, 0, -1):
                r = radius * band / LIGHT_BANDS
                a = int(alpha * (1 - strength * (1 - (band - 1) / LIGHT_BANDS)))
                pygame.draw.circle(mask, (*NIGHT, a), (radius, radius), max(1, int(round(r))))
            self.cache[key] = mask
            self.mask_order[key] = True
            while len(self.mask_order) > MASK_KEEP:
                old, _ = self.mask_order.popitem(last=False)
                self.cache.pop(old, None)
        return mask

    def dark_level(self):
        return self.area.darkness if self.darkness is None else self.darkness

    def flickering(self, light, now):
        """Round RPG15: whether a flame is at its smaller band at this moment - FLICKER_HZ chances a second, about one in
        FLICKER_ODDS of them, the same every run (the light's id and the moment, not a random number)."""
        return zlib.crc32(f"{light.id}:{int(now * FLICKER_HZ)}".encode()) % FLICKER_ODDS == 0

    def light_sources(self, walker, now):
        """Round RPG15: [(x, y, radius px, strength)] - the walker's lantern and every light burning (rpg.explore.Area.lit)."""
        a = self.area
        out = []
        if walker is not None and a.light:
            out.append((walker.x, walker.y, max(1, int(round(a.light * a.tw))), 1.0))
        for li in a.lit(self.flags):
            r = max(1, int(round(li.radius * a.tw)))
            if li.flicker and self.flicker and self.flickering(li, now):
                r = max(1, r - max(1, int(round(r / LIGHT_BANDS / 2))))       # half a band in: the flame dips
            out.append((li.x, li.y, r, li.strength))
        return out

    def draw_light(self, screen, camera, walker, now=0.0):
        """The map's darkness over the whole view, but for the walker's lantern and the map's lights (Round RPG11: drawn on the
        map-sized canvas, so it is a small picture, and its bands keep their pixel-art edges when the canvas is scaled up)."""
        a, z = self.area, camera.zoom
        rw, rh = camera.view_w // z, camera.view_h // z
        dark = self._dark(rw, rh)
        alpha = max(0, min(255, int(round(255 * self.dark_level()))))
        dark.fill((*NIGHT, alpha))
        for x, y, r, strength in self.light_sources(walker, now):
            cx, cy = int(round(x - camera.x)), int(round(y - camera.y))
            if cx + r < 0 or cy + r < 0 or cx - r > rw or cy - r > rh:
                continue                                         # out of view
            dark.blit(self.light_mask(r, alpha, strength), (cx - r, cy - r), special_flags=pygame.BLEND_RGBA_MIN)
        if z == 1:
            screen.blit(dark, (0, 0))
        else:
            screen.blit(pygame.transform.scale(dark, (rw * z, rh * z)), (0, 0))

    def _dark(self, w, h):
        key = ("dark", w, h)
        surf = self.cache.get(key)
        if surf is None:
            surf = self.cache[key] = pygame.Surface((w, h), pygame.SRCALPHA)
        return surf

    def draw_person(self, screen, camera, p, now):
        """Round RPG16: a person who comes and goes (their marker's Humanoid cell, its 2-frame idle), on their tile. Round RPG17:
        one walking between their places (p["pos"], in tiles: rpg.explore.Mover) is drawn where they are, the 2 frames twice as
        fast (a walk, not a stand)."""
        a = self.area
        cell = p.get("cell") or (0, 0)
        img = self.sprites.person(cell, now * (WALK_FRAMES if p.get("walking") else 1), camera.zoom)
        cx, cy = p["pos"] if p.get("pos") is not None else p["tile"]
        if img is not None:
            screen.blit(img, camera.to_screen(round(cx * a.tw), round(cy * a.th)))

    def draw_foe(self, screen, camera, t, now):
        img = self.sprites.foe(t.enemy, now, camera.zoom)
        if img is not None:                                       # Round RPG11: the kind's DawnLike character
            x0, y0, x1, y1 = t.box
            fx, fy = camera.to_screen(round((x0 + x1) / 2), round(y1))
            w = self.area.tw * camera.zoom
            pygame.draw.ellipse(screen, gfx.SHADOW, (fx - w * 0.3, fy - w * 0.12, w * 0.6, w * 0.22))
            screen.blit(img, img.get_rect(midbottom=(fx, fy + int(w * 0.05))))
            return
        x0, y0, x1, y1 = t.box
        cx, cy = camera.to_screen((x0 + x1) / 2, (y0 + y1) / 2)
        z = camera.zoom
        r = (0.42 if t.encounter == "boss" else 0.3) * self.area.tw * z
        bob = math.sin(now * 3.0 + sum(map(ord, t.spot)) % 7) * 0.06 * self.area.th * z
        cy = int(cy + bob)
        pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
        pygame.draw.ellipse(screen, gfx.SHADOW, (cx - r, cy + r * 0.7, 2 * r, r * 0.6))
        pygame.draw.polygon(screen, marker_colour(t.enemy), pts)
        pygame.draw.polygon(screen, gfx.MANA_EDGE, pts, max(1, z // 2))
        if t.encounter == "dungeon":
            pygame.draw.circle(screen, gfx.MANA_EDGE, (cx, cy), int(r * 0.35))
        elif t.encounter == "boss":
            pygame.draw.circle(screen, gfx.DEFEAT, (cx, cy), int(r * 0.35))

    def draw_walker(self, screen, camera, walker, now):
        img = self.sprites.walker(walker.facing, walker.walked, walker.moving, self.area.tw, camera.zoom, self.hero)
        if img is not None:                                       # Round RPG11: a DawnLike class sheet, walking
            fx, fy = camera.to_screen(round(walker.x), round(walker.y + walker.hh))
            w = self.area.tw * camera.zoom
            pygame.draw.ellipse(screen, gfx.SHADOW, (fx - w * 0.3, fy - w * 0.12, w * 0.6, w * 0.22))
            screen.blit(img, img.get_rect(midbottom=(fx, fy + int(w * 0.05))))
            return
        z = camera.zoom
        fx, fy = camera.to_screen(walker.x, walker.y + walker.hh)      # the feet's bottom edge
        w = self.area.tw * z
        pygame.draw.ellipse(screen, gfx.SHADOW, (fx - w * 0.3, fy - w * 0.1, w * 0.6, w * 0.2))
        body = pygame.Rect(0, 0, int(w * 0.44), int(w * 0.55))
        body.midbottom = (fx, fy)
        pygame.draw.rect(screen, gfx.GOLD_DARK, body, border_radius=max(1, z))
        head = (fx, body.top - int(w * 0.16))
        pygame.draw.circle(screen, gfx.GOLD, head, int(w * 0.2))
        dx = {"left": -1, "right": 1}.get(walker.facing, 0)
        dy = {"up": -1, "down": 1}.get(walker.facing, 0)
        if dy >= 0:
            eye = (head[0] + dx * int(w * 0.08), head[1] + int(w * 0.02))
            pygame.draw.circle(screen, gfx.SHADOW, eye, max(1, z // 2))


def is_above(layer):
    """Round RPG11: a tile layer drawn over the people (roofs, tree tops): named "above", or with the property above = true."""
    return layer.name.lower() == "above" or layer.props.get("above") is True


class Sprites:
    """Round RPG11: the map's people, from rpg/data/art.json and assets/rpg/dawnlike/ (CC-BY 4.0, DragonDePlatino; the
    DawnBringer palette). Pictures are cut and scaled once per zoom. A file or a kind that isn't there gives None, and the view
    draws its old shape instead."""

    def __init__(self, art_file=ART_FILE, art_dir=ART_DIR):
        self.art_dir = art_dir
        try:
            with open(art_file, encoding="utf-8") as f:
                self.art = json.load(f)
        except (OSError, ValueError):
            self.art = {}
        self.sheets = {}
        self.cache = {}

    def _sheet(self, rel):
        if rel not in self.sheets:
            try:
                img = pygame.image.load(os.path.join(self.art_dir, *rel.split("/")))
                if pygame.display.get_surface() is not None:
                    img = img.convert_alpha()
            except (pygame.error, FileNotFoundError, OSError):
                img = None
            self.sheets[rel] = img
        return self.sheets[rel]

    def _cut(self, key, rel, col, row, zoom, size=16):
        surf = self.cache.get(key)
        if surf is None:
            sheet = self._sheet(rel)
            if sheet is None or (col + 1) * size > sheet.get_width() or (row + 1) * size > sheet.get_height():
                return None
            surf = sheet.subsurface(pygame.Rect(col * size, row * size, size, size)).copy()
            if zoom != 1:
                surf = pygame.transform.scale(surf, (size * zoom, size * zoom))
            self.cache[key] = surf
        return surf

    def walker_frame(self, facing, walked, moving, tile):
        """(row, frame) of the walker's sheet: the row for its facing; while it moves a frame every WALK_FRAME_TILES walked."""
        w = self.art.get("walker") or {}
        row = (w.get("rows") or {}).get(facing, 0)
        frames = int(w.get("frames") or 1)
        frame = int(walked // max(1e-6, tile * WALK_FRAME_TILES)) % frames if moving else 0
        return row, frame

    def walker_sheet(self, hero=None):
        """The class sheet for `hero` (rpg.save.HEROES, listed in art.json's "classes"); the walker's own sheet otherwise."""
        w = self.art.get("walker") or {}
        if hero and hero in (self.art.get("classes") or ()):
            return f"Commissions/{hero}.png"
        return w.get("sheet")

    def walker(self, facing, walked, moving, tile, zoom, hero=None):
        w = self.art.get("walker")
        if not w:
            return None
        sheet = self.walker_sheet(hero)
        row, frame = self.walker_frame(facing, walked, moving, tile)
        return self._cut(("walker", sheet, row, frame, zoom), sheet, frame, row, zoom)

    def person(self, cell, now, zoom):
        """Round RPG16: a person's DawnLike picture (Characters/Humanoid0/1.png at `cell`), its 2-frame idle."""
        half = (int(now * 1000) // tmx.FRAME_MS) % 2
        col, row = cell
        return self._cut(("person", col, row, half, zoom), f"Characters/Humanoid{half}.png", col, row, zoom)

    def foe(self, kind, now, zoom):
        k = (self.art.get("kinds") or {}).get(kind)
        if not k:
            return None
        half = (int(now * 1000) // tmx.FRAME_MS) % 2               # DawnLike's 2-frame idle: the pair of sheets
        col, row = k["cell"]
        return self._cut(("foe", kind, half, zoom), f"{k['sheet']}{half}.png", col, row, zoom)


def wrap(text, font, width, most=3):
    """Lines of `text` that fit `width` (by words; a word too long for a line is cut), at most `most` lines."""
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if font.size(trial)[0] <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = word
        while line and font.size(line)[0] > width:
            line = line[:-1]
    if line:
        lines.append(line)
    return lines[:most]
