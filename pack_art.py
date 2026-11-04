# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PACK2 (Karl, 3 Nov 2026: "Our own wrapper + a card"): the picture of a pack in the Adventure's shop, drawn in code.

A foil wrapper of our own - never a copy or look-alike of a real Magic booster: no Wizards of the Coast logo, no "Magic: The
Gathering" wordmark, no set symbol, no real wrapper art or layout. On it: a two-colour gradient from the colours (W U B R G;
colourless silver; more than two colours gold), a diagonal sheen and faint foil lines, crimped top and bottom edges, the title (the
set's name or the theme) in the display face, a line under it in the body face, and a window with one real card picture - the full
card from Scryfall as the rest of the game shows it (gfx.card_in_frame), never an art crop. A premium pack has a gold edge; a bundle is
three wrappers of the set, fanned. Until the card picture is on disk the same wrapper is drawn without the window.

Every colour is a gfx token (tests/test_ad1.py's colour guard covers this file). The words are drawn with font.render, not gfx.draw_text:
they are part of the picture (the shop's own words stay in the Adventure's pixel font).
"""
import pygame

import gfx

KINDS = ("plain", "premium", "bundle", "theme")
ASPECT = 0.62                    # a wrapper's width / height (our own proportion)
CARD_ASPECT = 0.716              # a card's width / height (63 x 88 mm)
BUNDLE_TILT = 11                 # degrees each side wrapper of a bundle is turned
COLOUR_TOKENS = {"W": "PACK_W", "U": "PACK_U", "B": "PACK_B", "R": "PACK_R", "G": "PACK_G"}


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _luma(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def body_colours(colours):
    """(top, bottom) of the wrapper's gradient: two colours their two tokens; one colour its token lightened to darkened; none
    (colourless) silver; more than two gold."""
    cs = [c for c in (colours or "") if c in COLOUR_TOKENS]
    seen = []
    for c in cs:
        if c not in seen:
            seen.append(c)
    if not seen:
        base = gfx.PACK_SILVER
    elif len(seen) > 2:
        base = gfx.PACK_GOLD
    elif len(seen) == 2:
        return getattr(gfx, COLOUR_TOKENS[seen[0]]), getattr(gfx, COLOUR_TOKENS[seen[1]])
    else:
        base = getattr(gfx, COLOUR_TOKENS[seen[0]])
    return _mix(base, gfx.PACK_SHEEN, 0.22), _mix(base, gfx.PACK_SHADE, 0.35)


def ink_for(top, bottom):
    """The words' colour (and their shadow's) on a body of these colours: dark ink on a light body, light on a dark one."""
    mid = _mix(top, bottom, 0.3)
    return (gfx.PACK_INK, gfx.PACK_LIGHT) if _luma(mid) > 150 else (gfx.PACK_LIGHT, gfx.PACK_INK)


def _fit_lines(text, role, px, width, lines):
    """(font, [line]) - the biggest font from px down that sets `text` in at most `lines` lines of `width`."""
    words = (text or "").split()
    while True:
        font = gfx.get_font(px, role=role)
        out, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.size(t)[0] <= width or not cur:
                cur = t
            else:
                out.append(cur)
                cur = w
        if cur:
            out.append(cur)
        if (len(out) <= lines and all(font.size(ln)[0] <= width for ln in out)) or px <= 7:
            if len(out) > lines:
                out = out[:lines]
            return font, out
        px -= 1


def _blit_words(surf, font, lines, cx, y, colour, shadow, width):
    """Lines centred on cx from y down, a shadow one pixel off; each line clipped to `width`. Returns the y under them."""
    off = max(1, font.get_height() // 18)
    for ln in lines:
        img = font.render(ln, True, colour)
        sh = font.render(ln, True, shadow)
        if img.get_width() > width:
            img = img.subsurface(pygame.Rect(0, 0, width, img.get_height()))
            sh = sh.subsurface(pygame.Rect(0, 0, width, sh.get_height()))
        x = cx - img.get_width() // 2
        sh.set_alpha(150)
        surf.blit(sh, (x + off, y + off))
        surf.blit(img, (x, y))
        y += font.get_linesize() if hasattr(font, "get_linesize") else font.get_height()
    return y


def _body(w, h, top, bottom, premium):
    """The wrapper without its words or window: gradient, foil lines, sheen, crimps, edge."""
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        pygame.draw.line(surf, _mix(top, bottom, y / max(1, h - 1)), (0, y), (w - 1, y))
    foil = pygame.Surface((w, h), pygame.SRCALPHA)
    step = max(3, w // 36)
    for x in range(0, w, step):
        pygame.draw.line(foil, (*gfx.PACK_SHEEN, 16), (x, 0), (x, h - 1))
    band = max(4, w // 4)                                   # the diagonal sheen: a wide soft band and a thin bright one
    for off, width, alpha in ((-w * 0.15, band, 46), (w * 0.35, max(2, band // 4), 60)):
        x0 = int(off)
        pts = [(x0, h), (x0 + width, h), (x0 + width + int(h * 0.7), 0), (x0 + int(h * 0.7), 0)]
        pygame.draw.polygon(foil, (*gfx.PACK_SHEEN, alpha), pts)
    surf.blit(foil, (0, 0))
    crimp = max(4, round(h * 0.05))
    for y0 in (0, h - crimp):                                # crimped strips, with ridges
        shade = pygame.Surface((w, crimp), pygame.SRCALPHA)
        shade.fill((*gfx.PACK_SHADE, 70))
        for y in range(0, crimp, 2):
            pygame.draw.line(shade, (*gfx.PACK_SHEEN, 34), (0, y), (w - 1, y))
        surf.blit(shade, (0, y0))
    tooth = max(4, w // 14)
    depth = max(2, crimp // 2)
    for x in range(0, w + tooth, tooth):                     # the sawtooth edges: cut out
        pygame.draw.polygon(surf, gfx.CLEAR, [(x, -1), (x + tooth // 2, depth), (x + tooth, -1)])
        pygame.draw.polygon(surf, gfx.CLEAR, [(x, h), (x + tooth // 2, h - 1 - depth), (x + tooth, h)])
    side = max(1, w // 90)
    pygame.draw.line(surf, (*gfx.PACK_SHADE, 120), (0, depth), (0, h - 1 - depth), side)
    pygame.draw.line(surf, (*gfx.PACK_SHADE, 120), (w - side, depth), (w - side, h - 1 - depth), side)
    if premium:                                              # Premium: a gold edge round the body, between the crimps
        e = max(2, w // 36)
        r = pygame.Rect(e // 2 + side, crimp, w - e - 2 * side, h - 2 * crimp)
        pygame.draw.rect(surf, gfx.PACK_EDGE, r, e)
        pygame.draw.line(surf, gfx.PACK_EDGE, (side, crimp - 1), (w - 1 - side, crimp - 1), max(1, e // 2))
        pygame.draw.line(surf, gfx.PACK_EDGE, (side, h - crimp), (w - 1 - side, h - crimp), max(1, e // 2))
    return surf, crimp


def _single(kind, title, subtitle, colours, picture, w, h):
    top, bottom = body_colours(colours)
    surf, crimp = _body(w, h, top, bottom, kind == "premium")
    ink, shadow = ink_for(top, bottom)
    room = int(w * 0.84)
    y = crimp + max(2, int(h * 0.035))
    tf, tlines = _fit_lines(title, "display", max(7, int(w * 0.14)), room, 2)
    y = _blit_words(surf, tf, tlines, w // 2, y, ink, shadow, room)
    sf, slines = _fit_lines(subtitle, "body", max(7, int(w * 0.075)), room, 2)
    y = _blit_words(surf, sf, slines, w // 2, y, ink, shadow, room) + max(2, int(h * 0.02))
    if picture is not None:                                  # the window: the full card, rounded like a real one, in a frame
        bottom_y = h - crimp - max(2, int(h * 0.03))
        pad_k = 1.06                                         # card_in_frame's frame is about 3% of the card wider each side
        cw = int(min(w * 0.7, (bottom_y - y) * CARD_ASPECT / pad_k))
        ch = int(cw / CARD_ASPECT)
        if cw >= 16:
            pad = max(4, int(cw * 0.03))
            rect = pygame.Rect(0, 0, cw, ch)
            rect.midtop = (w // 2, y + pad)
            if rect.bottom + pad > bottom_y:
                rect.bottom = bottom_y - pad
            gfx.card_in_frame(surf, picture, rect, edge=gfx.PACK_EDGE if kind == "premium" else None)
    return surf


def _bundle(title, subtitle, colours, picture, w, h):
    """Three wrappers of the set, fanned: the two at the back turned BUNDLE_TILT degrees, the front one upright."""
    sw, sh = int(w * 0.76), int(h * 0.9)
    back = _single("plain", title, subtitle, colours, None, sw, sh)
    front = _single("plain", title, subtitle, colours, picture, sw, sh)
    big = pygame.Surface((w * 2, h * 2), pygame.SRCALPHA)
    c = big.get_rect().center
    for angle, dx in ((BUNDLE_TILT, -int(w * 0.13)), (-BUNDLE_TILT, int(w * 0.13))):
        img = pygame.transform.rotozoom(back, angle, 1.0)
        big.blit(img, img.get_rect(center=(c[0] + dx, c[1] + int(h * 0.02))))
    big.blit(front, front.get_rect(center=(c[0], c[1] - int(h * 0.02))))
    box = big.get_bounding_rect()
    part = big.subsurface(box)
    k = min(w / box.w, h / box.h)
    img = pygame.transform.smoothscale(part, (max(1, int(box.w * k)), max(1, int(box.h * k))))
    out = pygame.Surface((w, h), pygame.SRCALPHA)
    out.blit(img, img.get_rect(center=(w // 2, h // 2)))
    return out


def pack_surface(kind, title, subtitle, colours, card_picture, w, h, card_key=None):
    """The wrapper of one kind of pack ("plain", "premium", "bundle", "theme") at w x h: a Surface with per-pixel alpha, never
    larger than w x h. card_picture: the card's Surface, or None until it is here (then no window). Cached by everything that changes
    it; card_key names the picture in the key (else the picture's identity)."""
    w, h = max(8, int(w)), max(8, int(h))
    if kind not in KINDS:
        raise ValueError(f"unknown kind of pack {kind!r}")
    pic = None if card_picture is None else (card_key if card_key is not None else id(card_picture))
    key = ("pack_art", kind, title, subtitle, colours or "", w, h, pic)
    hit = gfx.recall(key)
    if hit is not None:
        return hit
    if kind == "bundle":
        surf = _bundle(title, subtitle, colours, card_picture, w, h)
    else:
        surf = _single(kind, title, subtitle, colours, card_picture, w, h)
    return gfx.remember(key, surf)


def fit(w, h):
    """The biggest wrapper (width, height) of our proportion that fits w x h."""
    ph = min(h, int(w / ASPECT))
    return max(8, int(ph * ASPECT)), max(8, ph)
