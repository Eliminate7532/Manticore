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
    view.draw(...) with an rpg.explore.RoomCamera draws only the room in view (clipped to it), and a map with the property
    light is dark but for a light round the walker (LIGHT_BANDS steps, drawn at the map's own pixel size and scaled up, so the
    light has pixel-art edges).
    draw_panels(screen, camera, area, room, seen, info)   the side panels: the map's name, gold, the deck (left); a minimap of
                                        the map's rooms - the one you are in, the ones you have seen (right)
"""
import math

import pygame

import gfx
from rpg import enemies, tmx


def _kind_colours():
    try:
        return {k["id"]: k.get("colour", "") for k in enemies.read_kinds()}
    except (OSError, ValueError, KeyError):
        return {}


_COLOURS = None
DARK_ALPHA = 228                  # Round RPG6: how dark a lit map is away from the light (0-255) - my estimate
LIGHT_BANDS = 4                   # the light's steps from bright to dark - my estimate


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
        for ts in area.map.tilesets:
            img = pygame.image.load(ts.image)
            if pygame.display.get_surface() is not None:
                img = img.convert_alpha()
            self.sheets[ts.firstgid] = (ts, img)
        self.cache = {}

    def tile(self, gid, bits, zoom):
        key = (gid, bits, zoom)
        surf = self.cache.get(key)
        if surf is None:
            ts = self.area.map.tileset_for(gid)
            _ts, img = self.sheets[ts.firstgid]
            x, y, w, h = ts.rect(ts.local(gid))
            surf = img.subsurface(pygame.Rect(x, y, w, h)).copy()
            surf = _flip(surf, bits)
            if zoom != 1:
                surf = pygame.transform.scale(surf, (w * zoom, h * zoom))
            self.cache[key] = surf
        return surf

    def draw(self, screen, camera, walker=None, encounters=(), now=0.0):
        a, z = self.area, camera.zoom
        screen.fill(gfx.BACKDROP)
        port = getattr(camera, "viewport", None)
        old_clip = screen.get_clip()
        if port is not None:                                     # Round RPG6: only the room in view
            screen.set_clip(pygame.Rect(port()).clip(old_clip))
        try:
            self._draw_map(screen, camera, walker, encounters, now)
        finally:
            screen.set_clip(old_clip)

    def _draw_map(self, screen, camera, walker, encounters, now):
        a, z = self.area, camera.zoom
        c0, r0, c1, r1 = camera.visible(a.tw, a.th, a.cols, a.rows)
        for layer in a.map.layers:
            if not layer.visible or layer.name.lower() == "collision":
                continue
            for row in range(r0, r1):
                base = row * a.cols
                for col in range(c0, c1):
                    gid = layer.data[base + col]
                    if not gid:
                        continue
                    img = self.tile(gid, layer.flips.get(base + col, 0), z)
                    screen.blit(img, camera.to_screen(col * a.tw, row * a.th))
        for t in encounters:
            self.draw_foe(screen, camera, t, now)
        if walker is not None:
            self.draw_walker(screen, camera, walker, now)
        if a.light and walker is not None and getattr(camera, "viewport", None) is not None:
            self.draw_light(screen, camera, walker)

    # ---- Round RPG6: the light in a dark map ----------------------------------------------------------------------------------

    def light_mask(self, radius):
        """A (2r x 2r) picture whose alpha is 0 at the middle and DARK_ALPHA at the radius, in LIGHT_BANDS steps."""
        key = ("mask", radius)
        mask = self.cache.get(key)
        if mask is None:
            size = 2 * radius
            mask = pygame.Surface((size, size), pygame.SRCALPHA)
            mask.fill((*gfx.BACKDROP, DARK_ALPHA))
            for band in range(LIGHT_BANDS, 0, -1):
                r = radius * band / LIGHT_BANDS
                alpha = int(DARK_ALPHA * (band - 1) / LIGHT_BANDS)
                pygame.draw.circle(mask, (*gfx.BACKDROP, alpha), (radius, radius), max(1, int(round(r))))
            self.cache[key] = mask
        return mask

    def draw_light(self, screen, camera, walker):
        a, z = self.area, camera.zoom
        port = pygame.Rect(camera.viewport())
        rw, rh = port.w // z, port.h // z
        dark = pygame.Surface((rw, rh), pygame.SRCALPHA)
        dark.fill((*gfx.BACKDROP, DARK_ALPHA))
        radius = max(1, int(round(a.light * a.tw)))
        cx, cy = int(round(walker.x - camera.x)), int(round(walker.y - camera.y))
        dark.blit(self.light_mask(radius), (cx - radius, cy - radius), special_flags=pygame.BLEND_RGBA_MIN)
        screen.blit(pygame.transform.scale(dark, port.size), port.topleft)

    def draw_foe(self, screen, camera, t, now):
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


# ---- Round RPG6: the side panels ------------------------------------------------------------------------------------------------

def _panel(screen, rect):
    if rect.w <= 8 or rect.h <= 8:
        return None
    inner = rect.inflate(-max(8, rect.w // 14), -max(8, rect.w // 14))
    gfx.round_rect(screen, inner, gfx.PANEL, radius=gfx.FRAME_RADIUS, border=max(1, rect.w // 120),
                   border_colour=gfx.PANEL_EDGE)
    return inner


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


def draw_panels(screen, camera, area, room, seen=(), info=None):
    """The two side panels of the room view. info: {"gold": n, "deck": name, "cards": n} - each shown when given. Returns
    {"left": rect, "right": rect, "minimap": rect, "cells": {room: rect}} (what was drawn where, for tests and clicks)."""
    info = info or {}
    left_r, right_r = (pygame.Rect(r) for r in camera.panels())
    out = {"left": None, "right": None, "minimap": None, "cells": {}}
    px = max(14, min(camera.view_h // 34, left_r.w // 9 if left_r.w else 14))
    head, body = gfx.get_font(px, role="display"), gfx.get_font(max(12, int(px * 0.8)))
    inner = _panel(screen, left_r)
    if inner is not None:
        out["left"] = inner
        x, y = inner.x + px // 2, inner.y + px // 2
        width = inner.w - px
        for line in wrap(area.title, head, width):
            gfx.draw_text(screen, line, x, y, head, gfx.GOLD)
            y += head.get_height() + px // 4
        for line, font, colour in (("", body, gfx.TEXT),
                                   (f"Gold  {info['gold']}" if info.get("gold") is not None else "", body, gfx.TEXT),
                                   (f"Deck  {info['deck']}" if info.get("deck") else "", body, gfx.TEXT),
                                   (f"{info['cards']} cards" if info.get("cards") is not None else "", body, gfx.DIM)):
            if line:
                text = line
                while text and font.size(text)[0] > width:
                    text = text[:-1]
                if text != line and len(text) > 1:
                    text = text[:-1] + "…"
                gfx.draw_text(screen, text, x, y, font, colour)
            y += font.get_height() + px // 4
    inner = _panel(screen, right_r)
    if inner is not None:
        out["right"] = inner
        gfx.draw_text(screen, "Map", inner.x + px // 2, inner.y + px // 2, head, gfx.GOLD)
        top = inner.y + px // 2 + head.get_height() + px // 2
        cols, rows = area.room_cols, area.room_rows
        cell = max(4, min((inner.w - px) // cols, (inner.h - (top - inner.y) - px) // max(1, rows), px * 2))
        mw, mh = cell * cols, int(cell * rows * 0.7)                 # a room is wider than it is tall (16 x 11)
        ch = max(3, mh // max(1, rows))
        mx = inner.x + (inner.w - mw) // 2
        out["minimap"] = pygame.Rect(mx, top, mw, ch * rows)
        seen = set(seen)
        for ry in range(rows):
            for rx in range(cols):
                r = pygame.Rect(mx + rx * cell + 1, top + ry * ch + 1, cell - 2, ch - 2)
                if (rx, ry) == tuple(room):
                    colour = gfx.GOLD
                elif area.room_key((rx, ry)) in seen:
                    colour = gfx.PANEL_EDGE
                else:
                    colour = gfx.PANEL_DEEP
                pygame.draw.rect(screen, colour, r)
                out["cells"][(rx, ry)] = r
    return out
