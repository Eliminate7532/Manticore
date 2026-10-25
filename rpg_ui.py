# SPDX-License-Identifier: GPL-3.0-or-later
"""rpg_ui.py - Round RPG13: DawnLike's window and its pixel font for the Adventure's pixel screens (the encounter screen now;
RPG14's one window style next - claude/SPEC_RPG_SLICE_2026-10-23.md).

    window(surface, rect)               DawnLike's dark window with the grey frame (assets/rpg/dawnlike/GUI/GUI0.png, tile
                                        columns 9-11, rows 10-12), drawn as a 9-slice: its 6-pixel corners kept, the edges
                                        stretched, the middle filled with the window's own colour
    font(px)                            the SDS 8x8 font (DawnLike's "Super Dragonsin Saga" monospaced font, GUI/SDS_8x8.ttf),
                                        at a whole multiple of 8 px, so every glyph stays on the pixel grid
    text(surface, s, x, y, colour, px)  one line, drawn without smoothing
    wrap(s, width, px)                  word-wrapped lines that fit `width` pixels (the font is monospaced: px per character)

Everything here is drawn at the screen's own pixel size (the encounter screen draws a 640 x 480 picture and scales it by a
whole number). Both files are part of DawnLike v1.81 (CC-BY 4.0, DragonDePlatino; palette DawnBringer), credited since RPG10.
A missing picture or font never stops a screen: the window falls back to a plain rectangle in the same colours, the font to
pygame's own. Every colour is a gfx.py token.
"""
import os
import textwrap
import unicodedata

import pygame

import gfx
import paths

GUI_DIR = os.path.join(paths.assets_dir(), "rpg", "dawnlike", "GUI")
WINDOW_FILE = "GUI0.png"
WINDOW_SRC = [144, 160, 48, 48]        # the dark window with the grey frame: x, y, w, h in GUI0.png (a list: test_ad1 reads a tuple as a colour)
CORNER = 6                             # px of each corner kept as drawn (the frame's rounded corner fits in 6 x 6)
FONT_FILE = "SDS_8x8.ttf"
FONT_PX = 8

_FONTS = {}
_SLICES = {}
_MISSING = set()


def _note_missing(what):
    if what in _MISSING:
        return
    _MISSING.add(what)
    try:
        import crashlog
        crashlog.note(f"Adventure: {what} is missing; drawn plainly instead")
    except Exception:
        pass


def font(px=FONT_PX):
    """The SDS 8x8 font at px (rounded down to a multiple of 8, at least 8)."""
    px = max(FONT_PX, int(px) // FONT_PX * FONT_PX)
    f = _FONTS.get(px)
    if f is None:
        path = os.path.join(GUI_DIR, FONT_FILE)
        try:
            f = pygame.font.Font(path, px)
        except (OSError, pygame.error, FileNotFoundError):
            _note_missing(FONT_FILE)
            f = pygame.font.Font(None, px + px // 2)
        _FONTS[px] = f
    return f


class _Spaced(pygame.font.Font):
    """The pixel font with a quarter more room between lines (Round RPG14b: the shop's and the deck editor's lists, laid out by
    get_height, would otherwise touch line to line)."""

    def get_height(self):
        return super().get_height() * 5 // 4

    def get_linesize(self):
        return self.get_height()


_SPACED = {}


def spaced_font(px=FONT_PX):
    """font(px), but get_height() is a quarter taller (the drawn letters are the same)."""
    px = max(FONT_PX, int(px) // FONT_PX * FONT_PX)
    f = _SPACED.get(px)
    if f is None:
        try:
            f = _Spaced(os.path.join(GUI_DIR, FONT_FILE), px)
        except (OSError, pygame.error, FileNotFoundError):
            _note_missing(FONT_FILE)
            f = _Spaced(None, px + px // 2)
        _SPACED[px] = f
    return f


PLAIN = {"\u00b7": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2026": "...", "\u00d7": "x", "\u2605": "*",
         "\u2022": "-", "\u25bc": "v"}
_HAS = {}


def _drawn(ch):
    """Does the pixel font have ch (or is its picture the font's empty box)?"""
    if ch not in _HAS:
        f = font()
        box = pygame.image.tobytes(f.render("\uffff", False, gfx.DB_TEXT), "RGBA")
        _HAS[ch] = pygame.image.tobytes(f.render(ch, False, gfx.DB_TEXT), "RGBA") != box
    return _HAS[ch]


def plain(s):
    """s with every character the pixel font can't draw put in a form it can (Round RPG14b): the middle dot and dashes as "-",
    an accented letter without its accent ("Lim-Dul"), anything else "?". ASCII comes back unchanged."""
    s = str(s)
    if s.isascii():
        return s
    out = []
    for ch in s:
        if ch.isascii() or _drawn(ch):
            out.append(ch)
        elif ch in PLAIN:
            out.append(PLAIN[ch])
        else:
            base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            out.append(base if base and base.isascii() else "?")
    return "".join(out)


def text(surface, s, x, y, colour, px=FONT_PX, anchor="topleft"):
    """One line of the pixel font; returns its rect."""
    img = font(px).render(plain(s), False, colour)
    r = img.get_rect(**{anchor: (int(x), int(y))})
    surface.blit(img, r)
    return r


def chars_in(width, px=FONT_PX):
    """How many characters fit in `width` pixels."""
    return max(1, int(width) // max(1, font(px).size("M")[0]))


def wrap(s, width, px=FONT_PX):
    """Lines of s that fit width pixels, split at spaces (a word longer than a line is cut)."""
    n = chars_in(width, px)
    out, line = [], ""
    for word in str(s).split():
        while len(word) > n:
            if line:
                out.append(line)
                line = ""
            out.append(word[:n])
            word = word[n:]
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= n:
            line += " " + word
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out


def _slices():
    """The window's nine pieces, cut once: {name: Surface} (None when the picture can't be read)."""
    if "pieces" in _SLICES:
        return _SLICES["pieces"]
    pieces = None
    try:
        sheet = pygame.image.load(os.path.join(GUI_DIR, WINDOW_FILE))
        sheet = sheet.convert_alpha() if pygame.display.get_surface() is not None else sheet
        x, y, w, h = WINDOW_SRC
        c = CORNER
        src = sheet.subsurface(pygame.Rect(x, y, w, h)).copy()
        m = w - 2 * c
        pieces = {"tl": src.subsurface((0, 0, c, c)).copy(), "t": src.subsurface((c, 0, m, c)).copy(),
                  "tr": src.subsurface((w - c, 0, c, c)).copy(), "l": src.subsurface((0, c, c, m)).copy(),
                  "r": src.subsurface((w - c, c, c, m)).copy(), "bl": src.subsurface((0, h - c, c, c)).copy(),
                  "b": src.subsurface((c, h - c, m, c)).copy(), "br": src.subsurface((w - c, h - c, c, c)).copy()}
    except (OSError, pygame.error, FileNotFoundError, ValueError):
        _note_missing(WINDOW_FILE)
    _SLICES["pieces"] = pieces
    return pieces


_WINDOWS = {}                          # (w, h, scale) -> the window drawn once (Round RPG14b: rows and buttons are windows too)
_WINDOWS_MAX = 400


def window(surface, rect, scale=1):
    """DawnLike's window filling rect (at least 2 x CORNER on a side). Round RPG14b: scale draws the frame's pixels that many
    screen pixels big (on a screen drawn at 1:1, as the shop and the deck editor are); a rect too small for it gets a plain box."""
    rect = pygame.Rect(rect)
    if scale > 1:
        c = CORNER * scale
        if rect.w < 2 * c or rect.h < 2 * c:
            pygame.draw.rect(surface, gfx.DB_BG, rect)
            pygame.draw.rect(surface, gfx.DB_DIM, rect, scale)
            return rect
        key = (rect.w, rect.h, scale)
        img = _WINDOWS.get(key)
        if img is None:                                   # drawn at 1:1, then scaled up (nearest pixel: the frame stays crisp;
            small = pygame.Surface((rect.w // scale, rect.h // scale), pygame.SRCALPHA)    # a size that isn't a multiple of
            window(small, small.get_rect())                                                 # the scale repeats a pixel row)
            img = pygame.transform.scale(small, rect.size)
            if len(_WINDOWS) >= _WINDOWS_MAX:
                _WINDOWS.clear()
            _WINDOWS[key] = img
        surface.blit(img, rect)
        return rect
    c = CORNER
    p = _slices()
    if p is None or rect.w < 2 * c or rect.h < 2 * c:
        pygame.draw.rect(surface, gfx.DB_BG, rect)
        pygame.draw.rect(surface, gfx.DB_DIM, rect, 1)
        return rect
    surface.fill(gfx.DB_BG, rect.inflate(-2 * c, -2 * c))
    mw, mh = rect.w - 2 * c, rect.h - 2 * c
    surface.blit(p["tl"], (rect.x, rect.y))
    surface.blit(p["tr"], (rect.right - c, rect.y))
    surface.blit(p["bl"], (rect.x, rect.bottom - c))
    surface.blit(p["br"], (rect.right - c, rect.bottom - c))
    if mw > 0:
        surface.blit(pygame.transform.scale(p["t"], (mw, c)), (rect.x + c, rect.y))
        surface.blit(pygame.transform.scale(p["b"], (mw, c)), (rect.x + c, rect.bottom - c))
    if mh > 0:
        surface.blit(pygame.transform.scale(p["l"], (c, mh)), (rect.x, rect.y + c))
        surface.blit(pygame.transform.scale(p["r"], (c, mh)), (rect.right - c, rect.y + c))
    return rect


# ---- Round RPG14: the dialogue box and a cursor menu, drawn at the UI's pixel size ------------------------------------------------
UI_H, UI_W = 270, 480                  # the UI's own pixels on screen: a scale that fits at least this many (ui_scale)
TYPE_CPS = 40                          # characters typed out a second (the spec)
LINE_CHARS = 34                        # a page's line: the words' own rule (rpg.talk.WRAP)
LINES = 3
PITCH_UI = 10                          # px between lines of the 8 px font
PAD = 10


def ui_scale(w, h):
    """How many screen pixels one of the UI's pixels is: a whole number, so the 8 px font stays on its grid."""
    return max(1, min(h // UI_H, w // UI_W))


def _blit_scaled(screen, surf, s, topleft):
    img = surf if s == 1 else pygame.transform.scale(surf, (surf.get_width() * s, surf.get_height() * s))
    return screen.blit(img, topleft)


class DialogueBox:
    """A person's words at the bottom of the screen: the name in gold, the page typed out at TYPE_CPS (Enter shows the rest at
    once), a blinking down-mark when more follows, then - if there is a choice - two answers in a small window above it.

        advance(now) -> None | "answer" | "close"     Enter / Space / a click on the box
        key(k, now)  -> the same, for a key (Up / Down move between the answers; Esc closes, or answers the second choice)
        answer        True (the first answer) / False, once given
    """

    def __init__(self, name, pages, now, choice=None, default=0):
        self.name = name
        self.pages = [p for p in pages if str(p).strip()] or ["..."]
        self.page, self.t0 = 0, now
        self.choice, self.choosing, self.cursor = choice, False, 0
        self.default = 1 if default == 1 else 0             # Round RPG15c: the answer the cursor starts on (Enter picks it)
        self.answer = None
        self.buttons = []                    # [(screen Rect, name)]: "talk_next", "talk_yes", "talk_no"

    def text(self):
        return " ".join(wrap_lines(self.pages[self.page]))

    def typed(self, now):
        return max(0, int((now - self.t0) * TYPE_CPS))

    def complete(self, now):
        return self.typed(now) >= len(self.text())

    def more(self):
        return self.page < len(self.pages) - 1 or (self.choice is not None and not self.choosing)

    def advance(self, now):
        if self.choosing:
            self.answer = self.cursor == 0
            return "answer"
        if not self.complete(now):
            self.t0 = now - (len(self.text()) + 1) / TYPE_CPS
            return None
        if self.page < len(self.pages) - 1:
            self.page, self.t0 = self.page + 1, now
            return None
        if self.choice is not None:
            self.choosing, self.cursor = True, self.default
            return None
        return "close"

    def key(self, k, now):
        if self.choosing and k in (pygame.K_UP, pygame.K_w, pygame.K_DOWN, pygame.K_s):
            self.cursor = 1 - self.cursor
            return None
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            return self.advance(now)
        if k == pygame.K_ESCAPE:
            if self.choice is not None:
                self.choosing, self.cursor = True, 1
                return self.advance(now)
            return "close"
        return None

    def pick(self, name, now):
        """A click: on the box, or on an answer."""
        if name in ("talk_yes", "talk_no") and self.choosing:
            self.cursor = 0 if name == "talk_yes" else 1
            return self.advance(now)
        if name == "talk_next":
            return self.advance(now)
        return None

    def draw(self, screen, now):
        W, H = screen.get_size()
        s = ui_scale(W, H)
        cw = font().size("M")[0]
        bw, bh = LINE_CHARS * cw + 2 * PAD + 8, PAD + 8 + 6 + LINES * PITCH_UI + PAD
        box = pygame.Surface((bw, bh), pygame.SRCALPHA)
        window(box, box.get_rect())
        text(box, self.name, PAD, PAD, gfx.DB_GOLD)
        shown = self.typed(now)
        y, used = PAD + 8 + 6, 0
        for ln in wrap_lines(self.pages[self.page]):
            part = ln[:max(0, shown - used)]
            used += len(ln) + 1
            if part:
                text(box, part, PAD, y, gfx.DB_TEXT)
            y += PITCH_UI
        if self.complete(now) and self.more() and int(now * 2) % 2 == 0:
            tri = [(bw - PAD - 6, bh - PAD - 4), (bw - PAD, bh - PAD - 4), (bw - PAD - 3, bh - PAD)]
            pygame.draw.polygon(box, gfx.DB_GOLD, tri)
        x0, y0 = (W - bw * s) // 2, H - (bh + 8) * s
        r = _blit_scaled(screen, box, s, (x0, y0))
        self.buttons = [(r, "talk_next")]
        if self.choosing and self.choice:
            labels = list(self.choice)
            cwid = max(len(lb) for lb in labels) * cw + 2 * PAD + 12
            ch = PAD * 2 + 2 * 14 - 4
            win = pygame.Surface((cwid, ch), pygame.SRCALPHA)
            window(win, win.get_rect())
            rows = []
            for i, lb in enumerate(labels):
                yy = PAD + i * 14
                here = i == self.cursor
                if here:
                    text(win, ">", PAD - 2, yy, gfx.DB_GOLD)
                text(win, lb, PAD + 10, yy, gfx.DB_GOLD if here else gfx.DB_TEXT)
                rows.append((pygame.Rect(4, yy - 3, cwid - 8, 14), "talk_yes" if i == 0 else "talk_no"))
            cx, cy = x0 + bw * s - cwid * s, y0 - (ch + 4) * s
            _blit_scaled(screen, win, s, (cx, cy))
            self.buttons += [(pygame.Rect(cx + rr.x * s, cy + rr.y * s, rr.w * s, rr.h * s), n) for rr, n in rows]
        return self.buttons


def wrap_lines(page):
    """A page's lines, as the box shows them (LINE_CHARS a line). Python's textwrap, as the dialogue lane's checker wraps: at
    spaces and after a hyphen ("thorn-" / "beasts"), a word longer than a line cut."""
    return textwrap.wrap(str(page), LINE_CHARS) or []


class MessageWindow:
    """A title and a few lines in a DawnLike window, with one answer ("> OK"): the Adventure's result window on the map
    (Round RPG14: "the existing result window, restyled"). Enter, Space, Esc or a click on OK close it (key -> "ok")."""

    WIDTH = 300                            # the window's width in the UI's pixels (title and lines wrap to it)

    def __init__(self, title, lines, colour, ok="OK"):
        self.title, self.lines, self.colour, self.ok = title, [str(x) for x in lines], colour, ok
        self.buttons = []

    def key(self, k):
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE, pygame.K_ESCAPE):
            return "ok"
        return None

    def draw(self, screen):
        W, H = screen.get_size()
        s = ui_scale(W, H)
        w = min(self.WIDTH, W // s - 16)
        body = []
        for ln in self.lines:
            body += wrap(ln, w - 2 * PAD) or [""]
        heads = wrap(self.title, w - 2 * PAD, 16) or [""]
        most = max(1, (H // s - 16 - 2 * PAD - len(heads) * 18 - 8 - 22) // PITCH_UI)
        if len(body) > most:                                # too many lines for the screen: the last shown ends in "..."
            body = body[:most - 1] + [body[most - 1][:max(0, chars_in(w - 2 * PAD) - 3)] + "..."]
        h = 2 * PAD + len(heads) * 18 + 6 + len(body) * PITCH_UI + 8 + 14
        win = pygame.Surface((w, h), pygame.SRCALPHA)
        window(win, win.get_rect())
        y = PAD
        for ln in heads:
            text(win, ln, PAD, y, self.colour, 16)
            y += 18
        y += 6
        for ln in body:
            text(win, ln, PAD, y, gfx.DB_TEXT)
            y += PITCH_UI
        y += 8
        text(win, ">", w - PAD - 10 - len(self.ok) * font().size("M")[0] - 2, y, gfx.DB_GOLD)
        okr = text(win, self.ok, w - PAD - len(self.ok) * font().size("M")[0], y, gfx.DB_GOLD)
        x0, y0 = (W - w * s) // 2, (H - h * s) // 2
        _blit_scaled(screen, win, s, (x0, y0))
        hit = okr.inflate(14, 8)
        hit.x -= 6
        self.buttons = [(pygame.Rect(x0 + hit.x * s, y0 + hit.y * s, hit.w * s, hit.h * s), "ok")]
        self.rect = pygame.Rect(x0, y0, w * s, h * s)
        return self.buttons


class ListMenu:
    """A cursor menu in a DawnLike window, with an information window beside it (the Adventure's pause window).
    items: [(name, label, enabled)]; info: [(text, colour token)]. Up / Down move, Enter picks, the mouse picks too."""

    def __init__(self, title, items, info=()):
        self.title, self.items, self.info = title, list(items), list(info)
        self.cursor = 0
        self.buttons = []

    def key(self, k):
        n = len(self.items)
        if k in (pygame.K_UP, pygame.K_w):
            self.cursor = (self.cursor - 1) % n
        elif k in (pygame.K_DOWN, pygame.K_s):
            self.cursor = (self.cursor + 1) % n
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            name, _label, on = self.items[self.cursor]
            return name if on else None
        return None

    def hover(self, pos):
        for i, (r, _n) in enumerate(self.buttons):
            if r.collidepoint(pos):
                self.cursor = i

    def draw(self, screen):
        W, H = screen.get_size()
        s = ui_scale(W, H)
        cw = font().size("M")[0]
        lw = max(len(lb) for _n, lb, _o in self.items) * cw + 2 * PAD + 14
        lw = max(lw, len(self.title) * 16 + 2 * PAD)
        lh = PAD + 16 + 8 + len(self.items) * 14 + PAD
        iw = min(UI_W - lw - 24, 34 * cw + 2 * PAD)
        info_lines = []
        for t, colour in self.info:
            for ln in wrap(t, iw - 2 * PAD) or [""]:       # an empty line is a gap between groups, not nothing
                info_lines.append((ln, colour))
        ih = max(lh, PAD * 2 + len(info_lines) * PITCH_UI)
        ih = min(ih, max(lh, H // s - 8))                   # never taller than the screen: the lines past it are left out
        total_w = lw + 8 + iw
        x0 = (W - total_w * s) // 2
        y0 = max(0, (H - max(lh, ih) * s) // 2)
        lst = pygame.Surface((lw, lh), pygame.SRCALPHA)
        window(lst, lst.get_rect())
        text(lst, self.title, PAD, PAD, gfx.DB_GOLD, 16)
        rows = []
        for i, (name, label, on) in enumerate(self.items):
            yy = PAD + 16 + 8 + i * 14
            here = i == self.cursor
            colour = gfx.DB_DIM if not on else (gfx.DB_GOLD if here else gfx.DB_TEXT)
            if here:
                text(lst, ">", PAD - 2, yy, gfx.DB_GOLD)
            text(lst, label, PAD + 10, yy, colour)
            rows.append(pygame.Rect(4, yy - 3, lw - 8, 14))
        _blit_scaled(screen, lst, s, (x0, y0))
        self.buttons = [(pygame.Rect(x0 + r.x * s, y0 + r.y * s, r.w * s, r.h * s), self.items[i][0]) for i, r in enumerate(rows)]
        inf = pygame.Surface((iw, ih), pygame.SRCALPHA)
        window(inf, inf.get_rect())
        y = PAD
        for ln, colour in info_lines:
            if y + 8 > ih - PAD // 2:
                break
            text(inf, ln, PAD, y, colour)
            y += PITCH_UI
        _blit_scaled(screen, inf, s, (x0 + (lw + 8) * s, y0))
        return self.buttons
