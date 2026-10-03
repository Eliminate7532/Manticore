# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_fx.py - visual cues for "what just happened" on the table.

  * Arrivals: a permanent that was not on the battlefield in the previous snapshot gets a bright ring (a flash and an expanding
    ring on top, unless animations are off) for a couple of seconds. Forge never says "this card entered", so this is worked out
    by comparing card ids from one snapshot to the next.
  * Flights: a permanent that arrived from somewhere the table can point at (my hand, the stack, an opponent's panel) glides from
    there to its place in a fraction of a second (animations only), so the eye follows it instead of finding a new card.
  * Stack marks: while a trigger or an activated ability is on the stack, its source permanent gets a coloured ring and a small
    number that matches the number on its row in the stack panel (purple = trigger, green = ability, 1 = resolves first).

The logic (Arrivals, stack_marks) has no drawing in it, so it can be tested without a window; the draw_* functions only need a
pygame surface. Timing is passed in (`now`), never read here, so a test or a preview render can use any clock it likes.
"""
import math

import pygame

import gfx

ARRIVE_SECONDS = 2.6              # how long a new permanent stays ringed
LAND_SECONDS = 1.5                # lands come and go every turn, so theirs is shorter and quieter
SHOCK_SECONDS = 0.7               # the expanding ring
FLASH_SECONDS = 0.35              # the white flash on the card
FLY_SECONDS = 0.45                # a permanent gliding to its place
FLY_LAND_SECONDS = 0.3
LINK_MAX = 8                      # only the first few stack items draw lines to their targets
STACK_MARK_MAX = 8                # only the first few stack items mark their source: a long stack must not light up the whole board
ARRIVE = gfx.FX_ARRIVE
TRIGGER = gfx.FX_TRIGGER
ABILITY = gfx.FX_ABILITY
SPELL = gfx.GOLD
KIND_COLOUR = {"trigger": TRIGGER, "ability": ABILITY, "spell": SPELL}


def kind_of(item):
    """'trigger', 'ability' or 'spell' for one stack item as Forge sends it."""
    return "trigger" if item.get("trigger") else "ability" if item.get("ability") else "spell"


class Arrivals:
    """Remembers which battlefield card ids there were, and when each new one showed up."""

    def __init__(self):
        self.seen = None              # ids on the battlefield at the last snapshot (None until the first: that one is history, not news)
        self.born = {}                # card id -> (time it arrived, is it a land)
        self.origin = {}              # card id -> the pygame.Rect it came from (only for arrivals the table could place)

    def update(self, cards, now, origins=None):
        """cards: {card id: is_land} for every permanent on the battlefield right now; origins: {card id: Rect it came from}.
        Returns the ids that just arrived."""
        ids = set(cards)
        if self.seen is None:
            self.seen = ids
            return []
        new = [cid for cid in cards if cid not in self.seen]
        for cid in new:
            self.born[cid] = (now, bool(cards[cid]))
            if origins and origins.get(cid) is not None:
                self.origin[cid] = pygame.Rect(origins[cid])
        self.seen = ids
        for cid in [c for c, (t, land) in self.born.items()
                    if c not in ids or now - t > (LAND_SECONDS if land else ARRIVE_SECONDS)]:
            del self.born[cid]
            self.origin.pop(cid, None)
        return new

    def flight(self, cid, now):
        """(progress 0..1, origin rect) while the permanent is still gliding to its place, else None."""
        hit, origin = self.born.get(cid), self.origin.get(cid)
        if not hit or origin is None:
            return None
        age = now - hit[0]
        dur = FLY_LAND_SECONDS if hit[1] else FLY_SECONDS
        return (age / dur, origin) if 0 <= age < dur else None

    def look(self, cid, now):
        """(age, duration, is_land) while the ring should still show, else None."""
        hit = self.born.get(cid)
        if not hit:
            return None
        age, land = now - hit[0], hit[1]
        dur = LAND_SECONDS if land else ARRIVE_SECONDS
        return (age, dur, land) if 0 <= age <= dur else None

    def recent(self, now):
        return [cid for cid in self.born if self.look(cid, now)]


def stack_marks(stack, limit=STACK_MARK_MAX):
    """{source card id: [(row number, kind)]} for the triggers and abilities on the stack (row 1 is the one that resolves first).
    Spells are not marked: the spell itself is on the stack, not on the battlefield."""
    marks = {}
    for i, item in enumerate(stack[:limit]):
        kind = kind_of(item)
        cid = (item.get("card") or {}).get("id")
        if kind != "spell" and cid is not None:
            marks.setdefault(cid, []).append((i + 1, kind))
    return marks


def stack_targets(stack, limit=LINK_MAX):
    """[(row number, kind, source card id or None, [targets as Forge names them: "c12" a card, "p3" a player])] for the first few stack
    items that target something (row 1 resolves first)."""
    out = []
    for i, item in enumerate(stack[:limit]):
        targets = [t for t in (item.get("targets") or []) if isinstance(t, str) and t[:1] in "cp" and t[1:].isdigit()]
        if targets:
            out.append((i + 1, kind_of(item), (item.get("card") or {}).get("id"), targets))
    return out


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def edge_point(rect, toward):
    """Where the line from the middle of `rect` towards the point `toward` leaves the rectangle."""
    cx, cy = rect.center
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return (cx, cy)
    sx = (rect.w / 2) / abs(dx) if dx else float("inf")
    sy = (rect.h / 2) / abs(dy) if dy else float("inf")
    k = min(sx, sy)
    return (cx + dx * k, cy + dy * k)


# ---- drawing --------------------------------------------------------------------------------------------------------------

def _outline(screen, rect, colour, alpha, grow, width, radius):
    alpha = int(max(0, min(255, alpha)))
    if alpha <= 0:
        return
    pad = grow + width + 2
    surf = pygame.Surface((rect.w + 2 * pad, rect.h + 2 * pad), pygame.SRCALPHA)
    r = pygame.Rect(pad - grow, pad - grow, rect.w + 2 * grow, rect.h + 2 * grow)
    pygame.draw.rect(surf, (*colour, alpha), r, width, border_radius=radius + grow)
    screen.blit(surf, (rect.x - pad, rect.y - pad))


def draw_arrival(screen, rect, age, dur, land, animate):
    """The ring around a permanent that just entered. With animate off the ring simply holds and fades: no flash, no expanding ring."""
    rect = pygame.Rect(rect)
    radius = max(3, int(min(rect.w, rect.h) * 0.07))
    t = min(1.0, age / dur)
    fade = 1.0 if t < 0.65 else max(0.0, (1 - t) / 0.35)
    k = 0.6 if land else 1.0
    _outline(screen, rect, ARRIVE, 235 * fade * k, 4, 3, radius)
    _outline(screen, rect, ARRIVE, 110 * fade * k, 8, 2, radius)
    if not animate:
        return
    if age < SHOCK_SECONDS:
        s = age / SHOCK_SECONDS
        _outline(screen, rect, ARRIVE, 220 * (1 - s) * k, 4 + int(30 * s), 3, radius)
    if age < FLASH_SECONDS:
        surf = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(surf, (255, 255, 255, int(150 * (1 - age / FLASH_SECONDS) * k)), surf.get_rect(), border_radius=radius)
        screen.blit(surf, rect)


def draw_number(screen, centre, radius, colour, n, dim=False):
    """A round badge with a number in it (wider when the number has two digits)."""
    text = str(n)
    font = gfx.get_font(max(9, int(radius * 1.35)), True)
    tw, th = font.size(text)
    w = max(radius * 2, tw + 6)
    box = pygame.Rect(0, 0, w, radius * 2)
    box.center = centre
    fill = tuple(int(c * (0.7 if dim else 1.0)) for c in colour)
    pygame.draw.rect(screen, fill, box, border_radius=radius)
    pygame.draw.rect(screen, gfx.FX_INK, box, 2, border_radius=radius)
    gfx.draw_text(screen, text, box.centerx, box.centery, font, gfx.FX_INK, "center")
    return box


def draw_marks(screen, rect, marks, now, animate):
    """The ring and the number badge(s) on the source of a trigger / ability that is on the stack."""
    rect = pygame.Rect(rect)
    radius = max(3, int(min(rect.w, rect.h) * 0.07))
    first_n, kind = min(marks)
    colour = KIND_COLOUR[kind]
    top = first_n == 1
    pulse = 0.5 + 0.5 * math.sin(now * 6) if animate else 1.0
    a = (150 + 90 * pulse) * (1.0 if top else 0.6)
    _outline(screen, rect, colour, a, 3, 3, radius)
    _outline(screen, rect, colour, a * 0.45, 7, 2, radius)
    r = max(9, int(rect.w * 0.11))
    x = rect.x + r + 2
    for n, kd in sorted(marks)[:3]:
        box = draw_number(screen, (x, rect.y + r + 2), r, KIND_COLOUR[kd], n, dim=n != 1)
        x = box.right + r + 1


def draw_attack_glow(screen, rect, now, animate, colour):
    """A pulsing ring on a creature that is attacking or blocking, so it reads at a glance instead of blending into every other
    static-glow state on the board (selectable, hovered). With animate off it just holds at full strength, like draw_marks."""
    rect = pygame.Rect(rect)
    radius = max(3, int(min(rect.w, rect.h) * 0.07))
    pulse = 0.5 + 0.5 * math.sin(now * 5) if animate else 1.0
    grow = 2 + int(3 * pulse) if animate else 3
    a = 150 + 90 * pulse if animate else 235
    _outline(screen, rect, colour, a, grow, 3, radius)
    _outline(screen, rect, colour, a * 0.45, grow + 5, 2, radius)


def draw_life_flash(screen, rect, colour, strength, now, animate):
    """A ring around a player's whole panel while their life total is still flashing (round 15: Karl asked for 'more flashing
    around your health total for damage/lifegain' - the old version only tinted the life number itself). `strength` is the
    same 0..1 fade the number and the floating +N/-N already use (1 = just happened, fading to 0); it pulses gently on top of
    that fade while animating, and holds steady at the fade level when animations are off."""
    if strength <= 0:
        return
    rect = pygame.Rect(rect)
    radius = max(10, int(min(rect.w, rect.h) * 0.045))
    pulse = 0.7 + 0.3 * math.sin(now * 10) if animate else 1.0
    a = 210 * strength * pulse
    _outline(screen, rect, colour, a, 3, 4, radius)
    _outline(screen, rect, colour, a * 0.5, 8, 3, radius)


def draw_flight(screen, surf, origin, dest, t):
    """The picture of a permanent, gliding from where it came from (`origin`) to `dest`, growing as it goes."""
    e = ease_out(t)
    scale = 0.55 + 0.45 * e
    w, h = max(2, int(surf.get_width() * scale)), max(2, int(surf.get_height() * scale))
    cx = origin.centerx + (dest.centerx - origin.centerx) * e
    cy = origin.centery + (dest.centery - origin.centery) * e
    img = pygame.transform.smoothscale(surf, (w, h)) if (w, h) != surf.get_size() else surf
    rect = img.get_rect(center=(round(cx), round(cy)))
    shadow = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, gfx.SHADOW_SOFT, shadow.get_rect(), border_radius=max(3, int(w * 0.06)))
    screen.blit(shadow, rect.move(4, 6))
    screen.blit(img, rect)
    return rect


SPRING_FLIGHT_SCALE_SECONDS = 0.23     # how long the 0.55 -> 1.0 grow takes (about a FLIGHT spring's settle time); the position
                                       # itself is driven by the springs directly, not by this


def draw_spring_flight(screen, surf, motion, now):
    """Round 23: like draw_flight, but the position comes from two FLIGHT springs (motion.x, motion.y) that forge_table retargets
    every frame to the card's current destination rect, instead of a fixed-length tween from a frozen origin to a frozen
    destination. This is what lets a flight still land in the right place if the window is resized mid-flight."""
    import motion as mot
    t = mot.clamp01((now - motion.flight_start) / SPRING_FLIGHT_SCALE_SECONDS) if motion.flight_start else 1.0
    scale = mot.lerp(0.55, 1.0, mot.ease_out_cubic(t))
    w, h = max(2, int(surf.get_width() * scale)), max(2, int(surf.get_height() * scale))
    img = pygame.transform.smoothscale(surf, (w, h)) if (w, h) != surf.get_size() else surf
    rect = img.get_rect(center=(round(motion.x.value), round(motion.y.value)))
    shadow = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, gfx.SHADOW_SOFT, shadow.get_rect(), border_radius=max(3, int(w * 0.06)))
    screen.blit(shadow, rect.move(4, 6))
    screen.blit(img, rect)
    return rect


def draw_landing_squash(screen, rect, surf, t):
    """90 ms of squash-and-recover right after a spring flight ends: x*1.04 / y*0.96, easing back out. `t` is 0 (just landed) to 1 (done).
    Skipped by the caller for lands, which come and go every turn."""
    import motion as mot
    e = 1 - mot.ease_out_quad(mot.clamp01(t))     # 1 at t=0, fading to 0
    sx, sy = 1 + 0.04 * e, 1 - 0.04 * e
    w, h = max(2, int(rect.w * sx)), max(2, int(rect.h * sy))
    img = pygame.transform.smoothscale(surf, (w, h)) if (w, h) != surf.get_size() else surf
    screen.blit(img, img.get_rect(center=rect.center))


def draw_link(screen, start, target, colour, now, animate, strong=True, n=None, badge_r=9):
    """A dashed line with an arrowhead from `start` (a Rect or a point) to the Rect `target`; the dashes march when animating.
    `n` puts the stack row number on the arrowhead's end so the line can be matched with its row."""
    a_rect = start if isinstance(start, pygame.Rect) else None
    a_pt = start.center if a_rect else start
    tgt = pygame.Rect(target)
    a = edge_point(a_rect, tgt.center) if a_rect else a_pt
    b = edge_point(tgt, a)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 6:
        return None
    ux, uy = dx / length, dy / length
    alpha = 230 if strong else 130
    width = 3 if strong else 2
    x0, y0 = int(min(a[0], b[0])) - 14, int(min(a[1], b[1])) - 14
    surf = pygame.Surface((int(abs(dx)) + 28, int(abs(dy)) + 28), pygame.SRCALPHA)
    ox, oy = x0, y0
    dash, gap = 11, 7
    shift = (now * 42) % (dash + gap) if animate else 0.0
    pos = -shift
    while pos < length - 10:
        s0, s1 = max(0.0, pos), min(length - 10, pos + dash)
        if s1 > s0:
            pygame.draw.line(surf, (*colour, alpha), (a[0] + ux * s0 - ox, a[1] + uy * s0 - oy), (a[0] + ux * s1 - ox, a[1] + uy * s1 - oy), width)
        pos += dash + gap
    head = 11 if strong else 9
    tip = (b[0] - ox, b[1] - oy)
    left = (tip[0] - ux * head - uy * head * 0.55, tip[1] - uy * head + ux * head * 0.55)
    right = (tip[0] - ux * head + uy * head * 0.55, tip[1] - uy * head - ux * head * 0.55)
    pygame.draw.polygon(surf, (*colour, alpha), [tip, left, right])
    screen.blit(surf, (x0, y0))
    if n is not None:
        draw_number(screen, (int(b[0] - ux * (head + badge_r + 1)), int(b[1] - uy * (head + badge_r + 1))), badge_r, colour, n, dim=not strong)
    return (a, b)
