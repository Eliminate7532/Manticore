# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_dialogs.py - the pop-up windows of the Forge table: choose-from-a-list, yes/no, number entry,
"look at these cards", zone viewer, help, game over, errors.

Every dialog is a small object with draw(gui) / click(gui, pos, button) / key(gui, event) / wheel(gui, dy).
When it is finished it sets `done = True`; requests from the engine are answered through the session
(gui.session.answer), so the Java side is released the moment the player decides.
"""
import math
import time

import pygame

import forge_log as flog
import gfx
from allocation import Allocation
from gfx import CYAN, DIM, GOLD, GREEN, RED, WHITE, YELLOW, clip_text, draw_rich, draw_text, round_rect, wrap_text

CARD_ASPECT = 488 / 680
ORANGE_TEXT = gfx.NOTE_ORANGE
_DIM_LAYERS = {}


def dim_screen(gui, alpha=170):
    if getattr(gui, "dim_done", False):                  # the table already drew itself dimmed this frame (frozen behind a dialog)
        return
    size = gui.screen.get_size()
    layer = _DIM_LAYERS.get((size, alpha))
    if layer is None:
        layer = pygame.Surface(size, pygame.SRCALPHA)
        layer.fill(gfx.BACKDROP + (alpha,))
        _DIM_LAYERS.clear()
        _DIM_LAYERS[(size, alpha)] = layer
    gui.screen.blit(layer, (0, 0))


class Dialog:
    done = False
    closes_game = False

    def __init__(self):
        self.buttons = []                   # (rect, name) filled while drawing
        self.rect = None                    # the window's rectangle, set when drawn

    def panel(self, gui, w, h, real=False):
        """A window in the middle of the screen. w and h are sizes at 100% text and grow with the text scale, so the
        words keep fitting; with real=True they are already real pixels. Never bigger than the window."""
        L = gui.L
        k = 1.0 if real else max(1.0, L.fs)
        w, h = min(int(w * k), L.W - 40), min(int(h * k), L.H - 40)
        rect = pygame.Rect(0, 0, w, h)
        rect.center = (L.W // 2, L.H // 2)
        dim_screen(gui)
        gfx.shadowed(gui.screen, rect, 16, 6, 120)
        round_rect(gui.screen, rect, gfx.DIALOG_BG, 16, 2, gfx.DIALOG_EDGE)
        self.buttons = []
        self.rect = rect
        return rect

    def button(self, gui, rect, label, name, enabled=True, primary=False, focus=False, fkey="btn"):
        gui.draw_button(rect, label, name, enabled, primary, focus, hit=False, fkey=fkey)
        if enabled:
            self.buttons.append((pygame.Rect(rect), name))

    def button_at(self, pos):
        for rect, name in reversed(self.buttons):
            if rect.collidepoint(pos):
                return name
        return None

    def draw(self, gui):
        pass

    def click(self, gui, pos, button):
        pass

    def key(self, gui, ev):
        pass

    def wheel(self, gui, dy):
        pass


# ---------------------------------------------------------------------------------------
# text helpers shared by several dialogs
# ---------------------------------------------------------------------------------------

def draw_wrapped(gui, text, x, y, w, font, colour=WHITE, max_lines=99, gap=2):
    lines = wrap_text(text, font, w)[:max_lines]
    for ln in lines:
        draw_text(gui.screen, ln, x, y, font, colour)
        y += font.get_height() + gap
    return y


def source_thumb(gui, card, x, y, h):
    """A small picture of the card that caused a question. Returns its width."""
    w = int(h * CARD_ASPECT)
    r = gui.draw_card_at(card, x, y, w, h, plate=False, flags=False)
    gui.card_rects[card["id"]] = r
    return w


def draw_focus_zoom(gui, card):
    """A big, readable copy of a card the mouse is over, drawn in the table's own focus panel (top right, where
    hovering a card on the board shows it) - on top of the dimmed table so it stays bright and legible even while a
    dialog's own thumbnails are too small to read (Karl's request, 2026-09-22: 'I should be able to read the cards
    in the card focus area' - the assign-combat-damage window's little card portraits had no way to blow one up)."""
    r = getattr(gui.L, "zoom", None)                         # patch UI6: the panel's place, even when the panel is off
    if r is None:
        r = gui.L.preview
    x, y, w, h = r.x, r.y, r.w, r.h
    surf = None
    aname = None
    if gui.art and not card.get("hidden"):
        aname = gui.art_key(card) if hasattr(gui, "art_key") else gui.art_name(card)     # round ALT1: the deck's printing
    if aname:
        big = gui.art.preview(aname, w, h)
        if big is not None:
            key = ("prev", aname, w, h)
            surf = gfx.recall(key) or gfx.remember(key, gfx.rounded_image(big, w, h, max(6, w // 20)))
    if surf is None:
        surf = gfx.card_face(card, w, h, max(6, w // 20), show_pt=True) if not card.get("hidden") else \
            gfx.card_back(w, h, max(6, w // 20))
    gfx.shadowed(gui.screen, pygame.Rect(x, y, w, h), 10, 5, 130)
    gui.screen.blit(surf, (x, y))


# ---------------------------------------------------------------------------------------
# choose from a list of cards / players / plain choices (also the read-only viewers)
# ---------------------------------------------------------------------------------------

class ChooseDialog(Dialog):
    """mode 'choose': pick between min and max items and press Done (order: click in the order wanted).
    mode 'view': look only (info lists, graveyards); `on_pick(card)` makes cards clickable."""

    def __init__(self, title, items, request=None, mode="choose", minimum=1, maximum=1, ordered=False, source=None,
                 on_pick=None, optional=False):
        super().__init__()
        self.title = title
        self.items = items
        self.request = request
        self.mode = mode
        self.min = max(0, minimum)
        self.max = len(items) if maximum < 0 else maximum
        self.ordered = ordered
        self.source = source
        self.on_pick = on_pick
        self.optional = optional
        self.sel = []
        self.scroll = 0
        self.tiles = []
        self.content_h = 0
        self.body = pygame.Rect(0, 0, 0, 0)
        self.last_click = (None, 0.0)
        low = str(title).lower()
        self.zone = next((z for z in ("library", "graveyard", "exile") if z in low), None)
        # a search through a big pile of identical cards (forty Forests): show each name once with a count
        self.stacks = {}                     # first index of a name -> all indices with that name
        self.skip = {i for i, it in enumerate(items) if flog.is_heading(it)}      # Forge's "--CARDS ON BATTLEFIELD:--" lines: not choices
        if mode == "choose" and self.max == 1 and not ordered:
            by_name = {}
            for i, it in enumerate(items):
                card = it.get("card") if it.get("kind") == "card" else None
                if card and not card.get("hidden") and not card.get("faceDown"):
                    by_name.setdefault(card.get("name"), []).append(i)
            if any(len(v) > 1 for v in by_name.values()):
                self.stacks = {v[0]: v for v in by_name.values()}
                self.skip |= {i for v in by_name.values() for i in v[1:]}

    def done_label(self):
        return {"library": "Take card"}.get(self.zone, "Done")

    def skip_label(self):
        return "Find nothing" if self.zone == "library" else "Skip"

    def short_label(self, it):
        """A name that is quick to read: the card's name, or the part of a long choice before ' - '."""
        name = (it.get("card") or {}).get("name") or it.get("label") or it.get("name") or "?"
        name = flog.strip_ids(name)
        if len(name) > 38 and " - " in name:
            name = name.split(" - ")[0]
        return name

    def selected_names(self):
        return [self.short_label(self.items[i]) for i in self.sel]

    need_lines = 4

    def need_text(self):
        """The line of instructions under the title."""
        done = self.done_label()
        if self.ordered:
            if self.min == self.max == len(self.items):
                need = "Click them all, in the order you want (first click = first)"
            elif self.min == 0:
                need = f"Choose up to {self.max}, in the order you want (first click = first), or none and press {done}"
            else:
                need = f"Choose {self.min} to {self.max}, in the order you want (first click = first)"
        elif self.max == 1 and self.min == 1:
            need = f"Click the card you want, then press {done}  (or double-click it)"
        elif self.max == 1:
            need = f"Click the card you want, then press {done}  (or double-click it).  {self.skip_label()} = take none"
        elif self.min == self.max:
            need = f"Choose {self.min}, then press {done}"
        else:
            need = f"Choose {self.min} to {self.max}, then press {done}"
        if self.optional and self.min == 0 and self.max != 1:
            need = f"Choose any you want, then press {done}, or press {self.skip_label()}"
        return need

    # ---- state
    def has_skip(self):
        """True when the dialog shows the Skip / Find nothing button (taking nothing is allowed)."""
        return bool(self.optional and self.min == 0 or (self.request and self.request.get("kind") == "choose_optional"))

    def needs_explicit_skip(self):
        """Round 27b (the fetch-land bug): a pick-one list where taking nothing is allowed. Taking nothing must be the Skip / Find nothing
        button, never Done, Enter or Space with nothing picked: Space is also 'pass priority', so a player resolving a fetch land with
        Space could land on the library search the moment it opened and silently find nothing (Arid Mesa gone, life paid, no land)."""
        return self.mode == "choose" and self.max == 1 and not self.ordered and self.has_skip()

    def valid(self):
        if self.mode == "view":
            return True
        if not self.sel and self.needs_explicit_skip():
            return False
        return self.min <= len(self.sel) <= self.max

    def toggle(self, i):
        if i in self.sel:
            self.sel.remove(i)
        elif self.max == 1 and not self.ordered:
            self.sel = [i]
        elif len(self.sel) < self.max or self.ordered:
            self.sel.append(i)

    def can_select_all(self):
        """Anything left to add, in item order, that 'Select all' would pick up."""
        if not self.ordered or len(self.sel) >= self.max:
            return False
        return any(i not in self.sel for i in range(len(self.items)) if i not in self.skip)

    def select_all(self):
        """Ordered mode only: append every not-yet-picked item, in Forge's own order, up to the max allowed.
        Karl's request (2026-09-22): the 'select order for simultaneous abilities' screen forced a click per item
        even when the order rarely matters - this adds the rest in list order in one click; Reset still undoes it."""
        for i in range(len(self.items)):
            if i in self.skip or i in self.sel:
                continue
            if len(self.sel) >= self.max:
                break
            self.sel.append(i)

    def finish(self, gui, value):
        if self.request is not None:
            gui.session.answer(self.request, value)
        self.done = True

    # ---- drawing
    def scale(self, gui):
        return max(1.0, gui.L.fs)

    def panel_width(self, gui):
        return min(int(1060 * self.scale(gui)), gui.L.W - 40)

    def thumb_h(self, gui):
        return int(min(170 * self.scale(gui), gui.L.H * 0.2))

    def head_width(self, gui, panel_w):
        """How wide the title / instructions may be: not under the card picture or the library pile in the corner."""
        tw = panel_w - 36
        if self.source is not None:
            tw -= int(self.thumb_h(gui) * CARD_ASPECT) + 16
        if self.zone == "library":
            tw -= 90
        return tw

    def head_lines(self, gui, tw):
        """The header text, wrapped: (title lines, instruction lines, 'Selected:' lines)."""
        title = wrap_text(self.title, gui.font("title", True), tw)[:3]
        if self.mode != "choose":
            return title, [], []
        need = wrap_text(self.need_text(), gui.font("small"), tw)[:self.need_lines]
        picked = self.selected_names()
        sb = gui.font("small", True)
        room = 1 if self.max == 1 else 2                         # a fixed number of lines, so the list below does not jump
        sel = wrap_text("Selected: " + (", ".join(picked) if picked else "nothing yet"), sb, tw)
        if len(sel) > room:
            sel = sel[:room]
            sel[-1] = clip_text(sel[-1] + " ...", sb, tw)
        while len(sel) < room:
            sel.append("")
        return title, need, sel

    def head_height(self, gui, tw):
        title, need, sel = self.head_lines(gui, tw)
        h = len(title) * (gui.font("title", True).get_height() + 2)
        if self.mode == "choose":
            h += 2 + len(need) * (gui.font("small").get_height() + 2) + 4 + len(sel) * gui.font("small", True).get_height() + 6
        return h

    def row_labels(self, it_list):
        """Text of the plain choices without Forge's card numbers - unless that would make two choices look the same."""
        raw = []
        for it in it_list:
            label = it.get("label") or it.get("name") or "?"
            if it.get("kind") == "player":
                label = f"Player: {label}"
            raw.append(label)
        clean = [flog.strip_ids(t) for t in raw]
        return [c if clean.count(c) == 1 else r for c, r in zip(clean, raw)]

    def text_rows(self, gui, width):
        """[(item index, wrapped lines, row height)] for the plain-text choices. Long choices wrap instead of being cut off."""
        f = gui.font("body")
        lh = f.get_height() + 2
        pre = f.size("00.  ")[0] if self.ordered else 0
        idx = [i for i, it in enumerate(self.items) if it.get("kind") != "card" and i not in self.skip]
        labels = self.row_labels([self.items[i] for i in idx])
        rows = []
        for i, label in zip(idx, labels):
            lines = wrap_text(label, f, width - 24 - pre) or [""]
            rows.append((i, lines, max(int(38 * gui.L.fs), len(lines) * lh + 14)))
        return rows

    def wanted_height(self, gui):
        """Tall enough for the header, every text choice and the card grid; never taller than the window."""
        L = gui.L
        k = self.scale(gui)
        pw = self.panel_width(gui)
        width = pw - 28
        head = self.head_height(gui, self.head_width(gui, pw))
        if self.source is not None:
            head = max(head, self.thumb_h(gui))
        text_h = sum(h + 6 for _i, _l, h in self.text_rows(gui, width))
        cards = sum(1 for i, it in enumerate(self.items) if it.get("kind") == "card" and i not in self.skip)
        tile_h = 300 if cards <= 3 else 240 if cards <= 5 else 200
        cols = max(1, (width + 10) // (int(tile_h * CARD_ASPECT) + 10))
        rows = math.ceil(cards / cols) if cards else 0
        need = 14 + head + 8 + text_h + rows * (tile_h + 10) + int(52 * L.fs) + 8
        return int(min(L.H - 40, int(760 * k), max(int(260 * k), need)))

    def draw(self, gui):
        L, scr = gui.L, gui.screen
        rect = self.panel(gui, self.panel_width(gui), self.wanted_height(gui), real=True)
        x, y = rect.x + 18, rect.y + 14
        tw = self.head_width(gui, rect.w)
        if self.source is not None:
            sw = source_thumb(gui, self.source, x, y, self.thumb_h(gui))
            x += sw + 16
        title, need, sel = self.head_lines(gui, tw)
        tf, sf, sb = gui.font("title", True), gui.font("small"), gui.font("small", True)
        for ln in title:
            draw_text(scr, ln, x, y, tf, WHITE)
            y += tf.get_height() + 2
        if self.mode == "choose":
            y += 2
            for ln in need:
                draw_text(scr, ln, x, y, sf, GOLD)
                y += sf.get_height() + 2
            y += 4
            picked = self.selected_names()
            for ln in sel:
                draw_text(scr, ln, x, y, sb, WHITE if picked else DIM)
                y += sb.get_height()
            y += 6
        if self.zone == "library" and gui.session.me():
            self.draw_library_badge(gui, rect)
        top = max(y, rect.y + 14 + (self.thumb_h(gui) if self.source is not None else 0)) + 8
        footer = int(52 * L.fs)
        self.body = pygame.Rect(rect.x + 14, top, rect.w - 28, rect.bottom - footer - top - 8)
        self.draw_items(gui)
        # footer buttons
        bw = max(gui.button_width(self.done_label()), int(110 * L.fs))
        bh = int(40 * L.fs)
        bx = rect.right - 18 - bw
        by = rect.bottom - bh - 12
        if self.mode == "view":
            self.button(gui, pygame.Rect(bx, by, bw, bh), "Close", "close", True, True)
        else:
            self.button(gui, pygame.Rect(bx, by, bw, bh), self.done_label(), "done", self.valid(), True, self.valid())
            if self.has_skip():
                sw = max(gui.button_width(self.skip_label()), int(90 * L.fs))
                self.button(gui, pygame.Rect(bx - sw - 10, by, sw, bh), self.skip_label(), "skip", True)
            if self.ordered:
                cw = gui.button_width("Reset")
                self.button(gui, pygame.Rect(rect.x + 18, by, cw, bh), "Reset", "reset", bool(self.sel))
                aw = gui.button_width("Select all")
                self.button(gui, pygame.Rect(rect.x + 18 + cw + 10, by, aw, bh), "Select all", "select_all",
                            self.can_select_all())

    def draw_library_badge(self, gui, rect):
        """A little face-down pile in the corner: 'this is your library'."""
        me = gui.session.me()
        n = me.get("libraryCount") if me else None
        if n is None:
            return
        h = int(min(86, rect.h * 0.16))
        w = int(h * CARD_ASPECT)
        x = rect.right - 18 - w
        y = rect.y + 14
        back = gfx.recall(("libback", w, h)) or gfx.remember(("libback", w, h), gfx.card_back(w, h, max(3, w // 12)))
        for off in (4, 2):
            gui.screen.blit(back, (x + off, y - off))
        gui.screen.blit(back, (x, y))
        f = gui.font("small", True)
        cx = min(x + w // 2, rect.right - 10 - f.size("Your library")[0] // 2)       # big text: keep the words inside the window
        draw_text(gui.screen, "Your library", cx, y + h + 3, f, WHITE, "midtop")
        draw_text(gui.screen, f"{n} cards", cx, y + h + 3 + f.get_height(), gui.font("tiny"), DIM, "midtop")

    def draw_items(self, gui):
        scr = gui.screen
        body = self.body
        cards = [(i, it) for i, it in enumerate(self.items) if it.get("kind") == "card" and i not in self.skip]
        if self.stacks:
            cards.sort(key=lambda t: str((t[1].get("card") or {}).get("name", "")))
        rows_t = self.text_rows(gui, body.w)
        pad = 10
        text_h = sum(h + 6 for _i, _l, h in rows_t)
        # pick the biggest card size that shows everything without scrolling (never below a readable minimum)
        avail = body.h - text_h
        tile_h = 110
        for h in range(340, 109, -20):
            w = int(h * CARD_ASPECT)
            cols = max(1, (body.w + pad) // (w + pad))
            rows = math.ceil(len(cards) / cols) if cards else 0
            if rows * (h + pad) <= avail:
                tile_h = h
                break
        tile_w = int(tile_h * CARD_ASPECT)
        cols = max(1, (body.w + pad) // (tile_w + pad))
        rows = math.ceil(len(cards) / cols) if cards else 0
        self.content_h = text_h + rows * (tile_h + pad)
        self.scroll = max(0, min(self.scroll, max(0, self.content_h - body.h)))
        self.tiles = []
        scr.set_clip(body)
        y = body.y - self.scroll
        f = gui.font("body")
        lh = f.get_height() + 2
        pre = f.size("00.  ")[0] if self.ordered else 0
        for i, lines, row_h in rows_t:
            r = pygame.Rect(body.x, y, body.w, row_h)
            on = i in self.sel
            hot = r.collidepoint(gui.mouse) and self.mode != "view"
            round_rect(scr, r, gfx.CHOICE_ON_BG if on else (gfx.CHOICE_HOT_BG if hot else gfx.CHOICE_BG), 10, 2 if on else 1,
                       GOLD if on else gfx.CHOICE_EDGE)
            ty = r.y + (row_h - len(lines) * lh) // 2 + lh // 2
            if self.ordered and on:
                draw_text(scr, f"{self.sel.index(i) + 1}.", r.x + 12, ty - lh // 2, gui.font("body", True), GOLD)
            for ln in lines:
                draw_rich(scr, ln, r.x + 12 + pre, ty, f, WHITE)
                ty += lh
            self.tiles.append((r, i))
            y += row_h + 6
        total_w = cols * tile_w + (cols - 1) * pad
        x0 = body.x + max(0, (body.w - total_w) // 2)
        for n, (i, it) in enumerate(cards):
            cx = x0 + (n % cols) * (tile_w + pad)
            cy = y + (n // cols) * (tile_h + pad)
            card = it["card"]
            r = pygame.Rect(cx, cy, tile_w, tile_h)
            if r.bottom >= body.y and r.top <= body.bottom:
                gui.draw_card_at(card, cx, cy, tile_w, tile_h, plate=False, flags=False)
                on = i in self.sel
                actionable = self.mode == "view" and self.on_pick and (card.get("weak") or card.get("selectable"))
                hot = r.collidepoint(gui.mouse) and self.mode != "view"
                if on:
                    gfx.glow(scr, r, YELLOW, 8, 4, 4)
                    pygame.draw.rect(scr, YELLOW, r, 5, border_radius=8)
                    if not self.ordered:                           # a tick tells "picked" from "just under the mouse"
                        tick = pygame.Rect(0, 0, 30, 30)
                        tick.topleft = (r.x + 6, r.y + 6)
                        pygame.draw.circle(scr, YELLOW, tick.center, 15)
                        pygame.draw.lines(scr, gfx.TICK_INK, False, [(tick.x + 7, tick.y + 16), (tick.x + 13, tick.y + 22),
                                                                     (tick.x + 23, tick.y + 9)], 4)
                elif actionable or hot:
                    gfx.glow(scr, r, YELLOW, 8, 4, 4)
                    pygame.draw.rect(scr, YELLOW, r, 4, border_radius=8)
                if len(self.stacks.get(i, ())) > 1:
                    cf = gui.font("body", True)
                    label = f"x{len(self.stacks[i])}"
                    cb = pygame.Rect(0, 0, cf.size(label)[0] + 16, cf.get_height() + 4)
                    cb.topright = (r.right - 4, r.y + 4)
                    round_rect(scr, cb, gfx.CLOSE_BG, cb.h // 2, 2, GOLD, alpha=235)
                    draw_text(scr, label, cb.centerx, cb.centery, cf, WHITE, "center")
                if self.ordered and on:
                    b = pygame.Rect(0, 0, 28, 28)
                    b.topleft = (r.x + 4, r.y + 4)
                    pygame.draw.circle(scr, gfx.KNOB_DARK, b.center, 14)
                    pygame.draw.circle(scr, GOLD, b.center, 14, 2)
                    draw_text(scr, str(self.sel.index(i) + 1), b.centerx, b.centery, gui.font("small", True), WHITE, "center")
                if r.collidepoint(gui.mouse) and body.collidepoint(gui.mouse):
                    gui.hover_card = card
            self.tiles.append((r, i))
        scr.set_clip(None)
        if self.content_h > body.h:
            frac = body.h / self.content_h
            bar_h = max(30, int(body.h * frac))
            bar_y = body.y + int((body.h - bar_h) * (self.scroll / max(1, self.content_h - body.h)))
            pygame.draw.rect(scr, gfx.SCROLLBAR, pygame.Rect(body.right - 6, bar_y, 5, bar_h), border_radius=3)
        if not self.items:
            draw_text(scr, "Nothing here.", body.centerx, body.centery, gui.font("body"), DIM, "center")
        # a big look at the hovered card (the preview panel is hidden behind the dialog)
        card = getattr(gui, "hover_card", None)
        gui.hover_card = None
        if card is not None and self.items:
            self.draw_zoom(gui, card)

    def draw_zoom(self, gui, card):
        draw_focus_zoom(gui, card)

    # ---- input
    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name == "close":
            self.done = True
        elif name == "done" and self.valid():
            self.finish(gui, list(self.sel))
        elif name == "skip":
            self.finish(gui, [])
        elif name == "reset":
            self.sel = []
        elif name == "select_all":
            self.select_all()
        elif button == 1 and self.body.collidepoint(pos):
            for rect, i in reversed(self.tiles):
                if rect.collidepoint(pos):
                    now = time.time()
                    twice = self.last_click[0] == i and now - self.last_click[1] < 0.5
                    self.last_click = (i, now)
                    if twice and self.mode == "choose" and self.max == 1 and not self.ordered and not self.done:
                        self.sel = [i]                       # a double-click means "this one, now"
                        if self.valid():
                            self.finish(gui, list(self.sel))
                    else:
                        self.pick(gui, i)
                    break

    def pick(self, gui, i):
        if self.mode == "view":
            card = self.items[i].get("card")
            if self.on_pick and card is not None and (card.get("weak") or card.get("selectable")):
                self.on_pick(card)
                self.done = True
            return
        self.toggle(i)

    def key(self, gui, ev):
        if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            if self.mode == "view":
                self.done = True
            elif self.valid():
                self.finish(gui, list(self.sel))
        elif ev.key == pygame.K_ESCAPE:
            if self.mode == "view":
                self.done = True
            elif self.zone == "library" and self.needs_explicit_skip():
                pass                                          # round 27b: finding nothing in a search is the Find nothing button only
            elif self.min == 0 or (self.request and self.request.get("kind") == "choose_optional"):
                self.finish(gui, [])

    def wheel(self, gui, dy):
        self.scroll -= dy * 70


class OpeningHandDialog(ChooseDialog):
    """'Choose cards to activate from opening hand and their order' - Forge's wording for Gemstone Caverns, Leylines, Chancellors.
    Shown as a plain question instead: every card is already switched on, one button begins the game with them on the
    battlefield, another keeps them in hand. (Forge's own list starts with nothing chosen, and pressing Done then quietly
    skipped the card.)"""
    need_lines = 8

    def __init__(self, request):
        items = request.get("items", [])
        super().__init__("Before turn 1: begin the game with this on the battlefield?", items, request=request, mode="choose",
                         minimum=0, maximum=len(items), ordered=False, source=None, optional=True)
        self.sel = list(range(len(items)))
        first = next((it.get("card") for it in items if it.get("kind") == "card"), None) or {}
        self.explain = (first.get("text") or "").replace("\r", "").split("\n")[0].strip()

    def done_label(self):
        return "Begin with it" if len(self.items) == 1 else "Begin with selected"

    def skip_label(self):
        return "No, keep in hand"

    def need_text(self):
        lead = f"{self.explain}  " if self.explain else ""
        return (f"{lead}Highlighted cards will begin the game on the battlefield. Click a card to switch it off or on, "
                f"then press {self.done_label()}, or {self.skip_label()} to leave everything in your hand.")


class PlayDrawDialog(Dialog):
    """The coin toss: play first or draw first. A real dialog with both words spelled out (Forge shows OK / Cancel)."""

    def __init__(self, message, deck_names=()):
        super().__init__()
        low = message.lower()
        self.title = "You won the coin toss" if "coin toss" in low else "You lost the last game"
        self.caverns = "Gemstone Caverns" in set(deck_names or ())

    def answer(self, gui, play):
        gui.answered_prompt = " ".join(gui.prompt().get("message", "").split())
        (gui.session.ok if play else gui.session.cancel)()
        self.done = True

    def draw(self, gui):
        L = gui.L
        k = max(1.0, L.fs)
        pw = min(int(760 * k), L.W - 40)
        tw = pw - 52
        tf, sf = gui.font("big", True), gui.font("title", True)
        bf, bb, small = gui.font("body"), gui.font("body", True), gui.font("small")
        grey = gfx.BODY_TEXT
        blocks = [(wrap_text(self.title, tf, tw), tf, GOLD, 6),
                  (wrap_text("Do you want to go first or second?", sf, tw), sf, WHITE, 8),
                  (wrap_text("Play first: you take the first turn.", bf, tw), bf, grey, 2),
                  (wrap_text("Draw first: the opponent takes the first turn and you go second.", bf, tw), bf, grey, 12)]
        if self.caverns:
            blocks.append((wrap_text("Your deck has Gemstone Caverns. It can only begin the game on the battlefield when you go "
                                     "SECOND - so pick Draw first if you want it to be usable.", bb, tw), bb, ORANGE_TEXT, 12))
        bh = int(48 * L.fs)
        need = 22 + sum(len(lines) * (f.get_height() + 2) + gap for lines, f, _c, gap in blocks) + small.get_height() + 12 + bh + 22
        rect = self.panel(gui, pw, min(need, L.H - 40), real=True)
        x, y = rect.x + 26, rect.y + 22
        for lines, f, colour, gap in blocks:
            for ln in lines:
                draw_text(gui.screen, ln, x, y, f, colour)
                y += f.get_height() + 2
            y += gap
        by = rect.bottom - bh - 22
        draw_text(gui.screen, "Keys:  P = play first     D = draw first", x, by - small.get_height() - 10, small, DIM)
        bw = max(gui.button_width("Draw first (go second)"), int(210 * L.fs))
        self.button(gui, pygame.Rect(rect.right - 26 - bw, by, bw, bh), "Draw first (go second)", "draw", True, False)
        pbw = max(gui.button_width("Play first"), int(190 * L.fs))
        self.button(gui, pygame.Rect(rect.right - 26 - bw - 14 - pbw, by, pbw, bh), "Play first", "play", True, True, True)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name in ("play", "draw"):
            self.answer(gui, name == "play")

    def key(self, gui, ev):
        if ev.key == pygame.K_p:
            self.answer(gui, True)
        elif ev.key == pygame.K_d:
            self.answer(gui, False)


# ---------------------------------------------------------------------------------------
# yes / no
# ---------------------------------------------------------------------------------------

class ConfirmDialog(Dialog):
    def __init__(self, request):
        super().__init__()
        self.request = request
        opts = request.get("options") or []
        self.yes = opts[0] if len(opts) > 0 else "Yes"
        self.no = opts[1] if len(opts) > 1 else "No"
        self.default = bool(request.get("default", True))

    def answer(self, gui, value):
        gui.session.answer(self.request, bool(value))
        self.done = True

    def draw(self, gui):
        L = gui.L
        k = max(1.0, L.fs)
        # Round UI4: the window is as tall as its words (and the card picture) need - at 200% text a fixed 300 x scale left
        # two lines of words in a box most of the screen high (alpha screenshot 22_q_confirm).
        w = min(int(720 * k), L.W - 40)
        src = self.request.get("source")
        th = int(200 * k) if src else 0
        sw = int(th * CARD_ASPECT) + 18 if src else 0
        f = gui.font("title", True)
        title = self.request.get("title", "")
        buttons_w = max(gui.button_width(self.yes), int(110 * L.fs)) + max(gui.button_width(self.no), int(110 * L.fs)) + 12
        w = min(w, max(int(480 * k), f.size(" ".join(title.split()))[0] + 44 + sw + 4, buttons_w + 44))   # a short question
        lines = wrap_text(title, f, w - 44 - sw)[:8]
        text_h = len(lines) * (f.get_height() + 2)
        bh = int(42 * L.fs)
        h = 20 + max(text_h, th) + int(18 * k) + bh + 16
        rect = self.panel(gui, w, max(int(110 * k), min(h, L.H - 40)), real=True)
        x, y = rect.x + 22, rect.y + 20
        tw = rect.w - 44
        if src:
            th = max(40, min(th, rect.h - bh - 20 - int(18 * k) - 16))
            sw = source_thumb(gui, src, x, y, th)
            x += sw + 18
            tw -= sw + 18
        draw_wrapped(gui, self.request.get("title", ""), x, y, tw, f, WHITE, 8)
        bh = int(42 * L.fs)
        bw_yes = max(gui.button_width(self.yes), int(110 * L.fs))
        bw_no = max(gui.button_width(self.no), int(110 * L.fs))
        by = rect.bottom - bh - 16
        bx = rect.right - 22 - bw_no
        self.button(gui, pygame.Rect(bx, by, bw_no, bh), self.no, "no", True, not self.default, not self.default)
        bx -= bw_yes + 12
        self.button(gui, pygame.Rect(bx, by, bw_yes, bh), self.yes, "yes", True, self.default, self.default)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name == "yes":
            self.answer(gui, True)
        elif name == "no":
            self.answer(gui, False)

    def key(self, gui, ev):
        if ev.key in (pygame.K_y,):
            self.answer(gui, True)
        elif ev.key in (pygame.K_n, pygame.K_ESCAPE):
            self.answer(gui, False)
        elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self.answer(gui, self.default)


# ---------------------------------------------------------------------------------------
# type a number / pick a text option
# ---------------------------------------------------------------------------------------

class InputDialog(Dialog):
    def __init__(self, request):
        super().__init__()
        self.request = request
        self.text = str(request.get("initial", ""))
        self.numeric = bool(request.get("numeric"))
        self.options = list(request.get("options") or [])

    def submit(self, gui, value=None):
        gui.session.answer(self.request, self.text if value is None else value)
        self.done = True

    def step(self, delta):
        try:
            n = int(self.text or 0)
        except ValueError:
            n = 0
        self.text = str(max(0, n + delta))

    def draw(self, gui):
        L = gui.L
        rows = math.ceil(len(self.options) / 3) if self.options else 0
        rect = self.panel(gui, 640, 250 + rows * 48)
        x, y = rect.x + 22, rect.y + 20
        y = draw_wrapped(gui, self.request.get("title", ""), x, y, rect.w - 44, gui.font("title", True), WHITE, 5)
        y += 8
        box = pygame.Rect(x, y, int(180 * L.fs), int(44 * L.fs))
        round_rect(gui.screen, box, gfx.FIELD_BG, 8, 2, GOLD)
        blink = "|" if int(pygame.time.get_ticks() / 500) % 2 == 0 else ""
        draw_text(gui.screen, self.text + blink, box.x + 12, box.centery, gui.font("title", True), WHITE, "midleft")
        if self.numeric:
            sb = box.h
            self.button(gui, pygame.Rect(box.right + 10, box.y, sb, sb), "-", "minus")
            self.button(gui, pygame.Rect(box.right + 10 + sb + 6, box.y, sb, sb), "+", "plus")
        y = box.bottom + 14
        bw = int((rect.w - 44 - 20) / 3)
        for n, opt in enumerate(self.options):
            r = pygame.Rect(x + (n % 3) * (bw + 10), y + (n // 3) * int(48 * L.fs), bw, int(40 * L.fs))
            self.button(gui, r, str(opt), f"opt{n}")
        bh = int(42 * L.fs)
        bw2 = max(gui.button_width("OK"), int(110 * L.fs))
        self.button(gui, pygame.Rect(rect.right - 22 - bw2, rect.bottom - bh - 16, bw2, bh), "OK", "ok", True, True, True)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name == "ok":
            self.submit(gui)
        elif name == "minus":
            self.step(-1)
        elif name == "plus":
            self.step(1)
        elif name and name.startswith("opt"):
            self.submit(gui, str(self.options[int(name[3:])]))

    def key(self, gui, ev):
        if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.submit(gui)
        elif ev.key == pygame.K_BACKSPACE:
            self.text = self.text[:-1]
        elif ev.key == pygame.K_UP:
            self.step(1)
        elif ev.key == pygame.K_DOWN:
            self.step(-1)
        elif ev.unicode and ev.unicode.isprintable() and len(self.text) < 12:
            if not self.numeric or ev.unicode.isdigit():
                self.text += ev.unicode


# ---------------------------------------------------------------------------------------
# hand out combat damage / divide an amount between targets
# ---------------------------------------------------------------------------------------

class AssignDialog(Dialog):
    """Combat damage among blockers (and the defender for trample), or "divide N" among targets and mana colours.
    The rules live in allocation.Allocation; this window shows one row per target with - / + buttons, a quick "Lethal"
    (or "Max") and "Rest" button, and only lets OK through when the split is legal. It starts from the usual split for
    damage (lethal to each in order, the rest to the last) and from nothing for a divide."""

    def __init__(self, request):
        super().__init__()
        self.request = request
        self.alloc = Allocation.from_request(request)
        self.cur = 0
        self.scroll = 0
        self.body = pygame.Rect(0, 0, 0, 0)
        self.row_h = 0
        self.max_scroll = 0

    @property
    def damage(self):
        return self.alloc.mode == "damage"

    # ---- answering ------------------------------------------------------------------------

    def send(self, gui, value):
        gui.session.answer(self.request, value)
        self.done = True

    def ok(self, gui):
        if self.alloc.valid:
            self.send(gui, self.alloc.answer())

    def skip(self, gui):
        if self.alloc.may_skip:
            self.send(gui, False)

    # ---- drawing --------------------------------------------------------------------------

    def draw(self, gui):
        L = gui.L
        k = max(1.0, L.fs)
        a = self.alloc
        n = len(a.rows)
        self.row_h = int(88 * k)
        want_rows = min(n, 5)
        rect = self.panel(gui, 820, 236 + want_rows * 88)
        x, y = rect.x + 22, rect.y + 18
        tw = rect.w - 44
        title = self.request.get("title", "Assign")
        fh = gui.font("title", True)
        y = draw_wrapped(gui, title, x, y, tw, fh, WHITE, 2)
        y += 4
        fb = gui.font("body")
        f_small = gui.font("small")
        what = "damage" if self.damage else (self.request.get("label") or "amount")
        line = f"{what.capitalize()} to assign: {a.remaining} left (of {a.total})"
        draw_text(gui.screen, line, x, y, fb, GOLD if a.remaining else GREEN)
        y += fb.get_height() + 2
        problem = a.problem()
        has_defender = any(a.is_defender(i) for i in range(n))
        if self.damage and not a.free:
            if a.order:
                hint = "Lethal damage to each creature in order before the next one gets any" + (" - the player only after every blocker." if has_defender else ".")
            else:
                hint = "Split the damage between the blockers as you like" + (", but the player only gets damage once every blocker has lethal." if has_defender else ".")
        elif self.damage:
            hint = "This creature may divide its damage as you choose."
        elif a.at_least_one:
            hint = "Every target gets at least 1."
        else:
            hint = "Use - and + (or the arrow keys) to hand out the amount."
        draw_text(gui.screen, clip_text(hint, f_small, tw), x, y, f_small, DIM)
        y += f_small.get_height() + 2
        if problem and a.remaining == 0:
            draw_text(gui.screen, clip_text(problem, f_small, tw), x, y, f_small, ORANGE_TEXT)
        y += f_small.get_height() + 8

        bh = int(42 * L.fs)
        by = rect.bottom - bh - 16
        self.body = pygame.Rect(x, y, tw, max(self.row_h, by - 10 - y))
        visible = max(1, self.body.h // self.row_h)
        self.max_scroll = max(0, n - visible)
        self.scroll = max(0, min(self.scroll, self.max_scroll))
        if self.cur < self.scroll:
            self.scroll = self.cur
        elif self.cur >= self.scroll + visible:
            self.scroll = self.cur - visible + 1
        prev_clip = gui.screen.get_clip()
        gui.screen.set_clip(self.body)
        for slot, i in enumerate(range(self.scroll, min(n, self.scroll + visible))):
            self.draw_row(gui, i, pygame.Rect(self.body.x, self.body.y + slot * self.row_h, self.body.w, self.row_h - 6))
        gui.screen.set_clip(prev_clip)
        if self.max_scroll:
            draw_text(gui.screen, f"{self.scroll + 1}-{min(n, self.scroll + visible)} of {n}  (mouse wheel scrolls)", self.body.right, self.body.bottom + 2,
                      f_small, DIM, "topright")

        # bottom buttons
        bx = rect.right - 22
        bw_ok = max(gui.button_width("OK"), int(110 * L.fs))
        bx -= bw_ok
        self.button(gui, pygame.Rect(bx, by, bw_ok, bh), "OK", "ok", a.valid, True, a.valid)
        if a.may_skip:
            w = max(gui.button_width("Decide later"), int(130 * L.fs))
            bx -= w + 12
            self.button(gui, pygame.Rect(bx, by, w, bh), "Decide later", "skip", True)
        x2 = rect.x + 22
        w = max(gui.button_width("Auto"), int(90 * L.fs))
        self.button(gui, pygame.Rect(x2, by, w, bh), "Auto", "auto", True)
        x2 += w + 10
        w = max(gui.button_width("Reset"), int(90 * L.fs))
        self.button(gui, pygame.Rect(x2, by, w, bh), "Reset", "reset", True)
        # a big, readable look at whichever row's card the mouse is over (the little thumbnails are too small to read)
        card = getattr(gui, "hover_card", None)
        gui.hover_card = None
        if card is not None:
            draw_focus_zoom(gui, card)

    def draw_row(self, gui, i, r):
        L = gui.L
        k = max(1.0, L.fs)
        a = self.alloc
        row = a.rows[i]
        selected = i == self.cur
        round_rect(gui.screen, r, gfx.LIST_ROW_SEL_BG if selected else gfx.LIST_ROW_BG, 10, 2 if selected else 1, GOLD if selected else gfx.LIST_ROW_SEL_EDGE)
        self.buttons.append((pygame.Rect(r), f"row{i}"))
        x = r.x + 10
        th = r.h - 12
        card = row.get("card")
        if card:
            x += source_thumb(gui, card, x, r.y + 6, th) + 12
            thumb_r = gui.card_rects.get(card["id"])
            if thumb_r and thumb_r.collidepoint(gui.mouse) and self.body.collidepoint(gui.mouse):
                gui.hover_card = card
        elif row.get("kind") == "mana":
            rad = th // 2 - 4
            gfx.mana_symbol(gui.screen, str(row.get("symbol") or "C"), x + rad + 4, r.centery, rad)
            x += rad * 2 + 20
        else:                                                # a player: a round badge with the initial
            rad = th // 2 - 4
            pygame.draw.circle(gui.screen, gfx.RADIO_BG, (x + rad + 4, r.centery), rad)
            pygame.draw.circle(gui.screen, gfx.RADIO_EDGE, (x + rad + 4, r.centery), rad, 2)
            draw_text(gui.screen, (a.name(i) or "?")[:1].upper(), x + rad + 4, r.centery, gui.font("title", True), WHITE, "center")
            x += rad * 2 + 20
        # controls on the right, computed first so the name knows how much room it has
        bs = int(44 * k)
        ctl_w = bs * 2 + int(96 * k) + 12
        second = "Max" if not self.damage else "Lethal"
        bw2 = max(gui.button_width(second), int(76 * k))
        bw3 = max(gui.button_width("Rest"), int(64 * k))
        total_w = ctl_w + bw2 + bw3 + 20
        cx = r.right - 10 - total_w
        name = a.name(i)
        if row.get("kind") == "player":
            name = f"{name}" + (" (defending player)" if row.get("defender") and not str(name).lower().startswith("planes") else "")
        fb = gui.font("body", True)
        nm_w = max(40, cx - x - 8)
        draw_text(gui.screen, clip_text(name, fb, nm_w), x, r.y + 10, fb, WHITE)
        f_small = gui.font("small")
        amount = a.amounts[i]
        if self.damage:
            lethal = a.lethal(i)
            note = f"Lethal: {lethal}" if lethal else "No damage needed"
            if lethal and amount >= lethal:
                note = "Lethal" + (f" +{amount - lethal}" if amount > lethal else "")
            col = GREEN if lethal and amount >= lethal else DIM
        else:
            note = f"Up to {a.maximum(i)}" + (" (at least 1)" if a.at_least_one else "")
            col = GREEN if amount >= a.maximum(i) and amount > 0 else DIM
        draw_text(gui.screen, clip_text(note, f_small, nm_w), x, r.y + 10 + fb.get_height() + 4, f_small, col)
        cy = r.centery - bs // 2
        self.button(gui, pygame.Rect(cx, cy, bs, bs), "-", f"minus{i}", amount > a.minimum(i))
        box = pygame.Rect(cx + bs + 6, cy, int(96 * k), bs)
        round_rect(gui.screen, box, gfx.FIELD_BG, 8, 2, GOLD if amount else gfx.FIELD_EDGE_OFF)
        draw_text(gui.screen, str(amount), box.centerx, box.centery, gui.font("title", True), WHITE, "center")
        self.button(gui, pygame.Rect(box.right + 6, cy, bs, bs), "+", f"plus{i}", a.remaining > 0 and amount < a.maximum(i))
        bx = box.right + 6 + bs + 10
        self.button(gui, pygame.Rect(bx, cy, bw2, bs), second, f"lethal{i}", True)
        self.button(gui, pygame.Rect(bx + bw2 + 10, cy, bw3, bs), "Rest", f"rest{i}", a.remaining > 0)

    # ---- input ----------------------------------------------------------------------------

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if not name:
            return
        a = self.alloc
        if name == "ok":
            self.ok(gui)
        elif name == "skip":
            self.skip(gui)
        elif name == "auto":
            a.auto()
        elif name == "reset":
            a.reset()
        elif name.startswith("row"):
            self.cur = int(name[3:])
        else:
            for prefix, fn in (("minus", lambda i: a.change(i, -1)), ("plus", lambda i: a.change(i, 1)),
                               ("lethal", a.to_lethal), ("rest", a.to_rest)):
                if name.startswith(prefix):
                    i = int(name[len(prefix):])
                    self.cur = i
                    fn(i)
                    break

    def wheel(self, gui, dy):
        self.scroll = max(0, min(self.max_scroll, self.scroll - (1 if dy > 0 else -1)))

    def key(self, gui, ev):
        a = self.alloc
        n = len(a.rows)
        if not n:
            return
        if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.ok(gui)
        elif ev.key == pygame.K_ESCAPE:
            self.skip(gui)
        elif ev.key == pygame.K_UP:
            self.cur = (self.cur - 1) % n
        elif ev.key == pygame.K_DOWN:
            self.cur = (self.cur + 1) % n
        elif ev.key in (pygame.K_LEFT, pygame.K_MINUS, pygame.K_KP_MINUS):
            a.change(self.cur, -1)
        elif ev.key in (pygame.K_RIGHT, pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            a.change(self.cur, 1)
        elif ev.key == pygame.K_l or ev.key == pygame.K_m:
            a.to_lethal(self.cur)
        elif ev.key in (pygame.K_r, pygame.K_SPACE):
            a.to_rest(self.cur)
        elif ev.key == pygame.K_a:
            a.auto()
        elif ev.key == pygame.K_0:
            a.clear(self.cur)


# ---------------------------------------------------------------------------------------
# help, plain messages, game over, errors
# ---------------------------------------------------------------------------------------

class HelpDialog(Dialog):
    """The controls list. It grows to fit its text and, when the window is too small for all of it, scrolls with the mouse wheel
    (the old fixed-size panel let the last lines run under the Close button once the list got longer)."""

    def __init__(self):
        super().__init__()
        self.scroll = 0
        self.max_scroll = 0
        self.viewport = pygame.Rect(0, 0, 0, 0)

    def rows(self, gui, width):
        """[(key, wrapped text lines, row height)] for a panel `width` wide."""
        from forge_table import HELP_LINES
        kf, bf = gui.font("body", True), gui.font("body")
        tw = width - 44 - int(width * 0.26)
        out = []
        for key, text in HELP_LINES:
            lines = wrap_text(text, bf, tw)[:6]
            out.append((key, lines, max(len(lines) * bf.get_height(), kf.get_height()) + 8))      # Round AD1: Alegreya's line box is already roomy, so no extra 2 px
        return out

    def draw(self, gui):
        L = gui.L
        kf, bf, tf = gui.font("body", True), gui.font("body"), gui.font("title", True)
        width = min(int(900 * max(1.0, L.fs)), L.W - 40)
        rows = self.rows(gui, width)
        bh = int(40 * L.fs)
        head = 24 + tf.get_height() + 10
        foot = bh + 24
        total = sum(h for _k, _l, h in rows)
        rect = self.panel(gui, width, head + total + foot, real=True)
        draw_text(gui.screen, "Controls", rect.x + 22, rect.y + 16, tf, GOLD)
        self.viewport = pygame.Rect(rect.x, rect.y + head, rect.w, max(20, rect.h - head - foot))
        self.max_scroll = max(0, total - self.viewport.h)
        self.scroll = max(0, min(self.scroll, self.max_scroll))
        kw = int(rect.w * 0.26)
        y = self.viewport.y - self.scroll
        saved_clip = gui.screen.get_clip()
        gui.screen.set_clip(self.viewport)
        for key, lines, h in rows:
            if y + h > self.viewport.y and y < self.viewport.bottom:
                draw_text(gui.screen, key, rect.x + 22, y, kf, WHITE)
                for n, ln in enumerate(lines):
                    draw_text(gui.screen, ln, rect.x + 22 + kw, y + n * bf.get_height(), bf, gfx.SOFT_TEXT)
            y += h
        gui.screen.set_clip(saved_clip)
        if self.max_scroll:
            draw_text(gui.screen, "Mouse wheel: more", rect.x + 22, rect.bottom - bh - 12 + bh // 2, gui.font("small"), DIM, "midleft")
        bw = max(gui.button_width("Close"), int(110 * L.fs))
        self.button(gui, pygame.Rect(rect.right - bw - 18, rect.bottom - bh - 12, bw, bh), "Close", "close", True, True)

    def wheel(self, gui, dy):
        self.scroll = max(0, min(self.max_scroll, self.scroll - dy * 40))

    def click(self, gui, pos, button):
        if self.button_at(pos) == "close":
            self.done = True

    def key(self, gui, ev):
        if ev.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_h, pygame.K_SPACE):
            self.done = True


class MessageDialog(Dialog):
    def __init__(self, title, text, button="OK", closes_game=False, colour=WHITE, heading=None):
        super().__init__()
        self.title, self.text, self.label = title, text, button
        self.closes_game = closes_game
        self.colour = colour
        self.heading = heading

    def draw(self, gui):
        L = gui.L
        f, tf = gui.font("body"), gui.font("title", True)
        pw = min(int(700 * max(1.0, L.fs)), L.W - 40)
        head_h = ((gui.font("big", True) if self.heading else tf).get_height() + 12) if (self.heading or self.title) else 0
        need = 18 + head_h + len(wrap_text(self.text, f, pw - 60)) * (f.get_height() + 2) + 30 + int(42 * L.fs) + 16
        rect = self.panel(gui, pw, max(int(200 * max(1.0, L.fs)), need), real=True)
        y = rect.y + 18
        if self.heading or self.title:
            draw_text(gui.screen, self.heading or self.title, rect.centerx, y, gui.font("big", True) if self.heading else tf,
                      self.colour, "midtop")
            y += (gui.font("big", True) if self.heading else tf).get_height() + 12
        draw_wrapped(gui, self.text, rect.x + 30, y, rect.w - 60, f, WHITE, 14)
        bh = int(42 * L.fs)
        bw = max(gui.button_width(self.label), int(120 * L.fs))
        self.button(gui, pygame.Rect(rect.centerx - bw // 2, rect.bottom - bh - 16, bw, bh), self.label, "ok", True, True, True)

    def click(self, gui, pos, button):
        if self.button_at(pos) == "ok":
            self.done = True

    def key(self, gui, ev):
        if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE, pygame.K_ESCAPE):
            self.done = True


class QuestionDialog(Dialog):
    """A plain yes / no question that runs `on_yes()` when confirmed."""

    def __init__(self, title, text, yes, no, on_yes, enter_yes=True):
        super().__init__()
        self.title, self.text, self.yes, self.no, self.on_yes, self.enter_yes = title, text, yes, no, on_yes, enter_yes

    def answer(self, value):
        self.done = True
        if value:
            self.on_yes()

    def draw(self, gui):
        L = gui.L
        k = max(1.0, L.fs)
        pw = min(int(720 * k), L.W - 40)
        tf, bf = gui.font("title", True), gui.font("body")
        tlines, blines = wrap_text(self.title, tf, pw - 52), wrap_text(self.text, bf, pw - 52)
        bh = int(max(42, 46 * L.fs))
        need = 24 + len(tlines) * (tf.get_height() + 2) + 10 + len(blines) * (bf.get_height() + 2) + 24 + bh + 22
        rect = self.panel(gui, pw, min(need, L.H - 40), real=True)
        x, y = rect.x + 26, rect.y + 24
        for ln in tlines:
            draw_text(gui.screen, ln, x, y, tf, WHITE)
            y += tf.get_height() + 2
        y += 10
        for ln in blines:
            draw_text(gui.screen, ln, x, y, bf, gfx.BODY_TEXT)
            y += bf.get_height() + 2
        by = rect.bottom - bh - 22
        yw = max(gui.button_width(self.yes), int(180 * L.fs))
        nw = max(gui.button_width(self.no), int(150 * L.fs))
        self.button(gui, pygame.Rect(rect.right - 26 - yw, by, yw, bh), self.yes, "yes", True, self.enter_yes, self.enter_yes)
        self.button(gui, pygame.Rect(rect.right - 26 - yw - 12 - nw, by, nw, bh), self.no, "no", True, not self.enter_yes,
                    not self.enter_yes)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name in ("yes", "no"):
            self.answer(name == "yes")

    def key(self, gui, ev):
        if ev.key == pygame.K_y or (self.enter_yes and ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER)):
            self.answer(True)
        elif ev.key in (pygame.K_n, pygame.K_ESCAPE):
            self.answer(False)


class OptionsDialog(Dialog):
    """A question with several answers, one wide button each, and a 'never mind' button last. options: [(label, run, style)] where run() is
    called when that button is clicked and style is "normal" or "danger" (red outline). Enter does nothing (so a slip can't choose for you);
    Esc is 'never mind'; the keys 1, 2, ... press the options."""

    def __init__(self, title, text, options, cancel="Cancel"):
        super().__init__()
        self.title, self.text, self.options, self.cancel = title, text, list(options), cancel

    def choose(self, index):
        self.done = True
        if index is not None:
            self.options[index][1]()

    def draw(self, gui):
        L = gui.L
        k = max(1.0, L.fs)
        pw = min(int(620 * k), L.W - 40)
        tf, bf = gui.font("title", True), gui.font("body")
        tlines, blines = wrap_text(self.title, tf, pw - 52), wrap_text(self.text, bf, pw - 52)
        bh = int(max(42, 46 * L.fs))
        gap = 10
        n = len(self.options) + 1
        fixed = 24 + len(tlines) * (tf.get_height() + 2) + 10 + len(blines) * (bf.get_height() + 2) + 22 + 22
        while n * bh + (n - 1) * gap + fixed > L.H - 40 and bh > bf.get_height() + 8:      # many options in a small window: slimmer buttons
            bh -= 2
            gap = max(4, gap - 1)
        need = fixed + n * bh + (n - 1) * gap
        rect = self.panel(gui, pw, min(need, L.H - 40), real=True)
        x, y = rect.x + 26, rect.y + 24
        for ln in tlines:
            draw_text(gui.screen, ln, x, y, tf, WHITE)
            y += tf.get_height() + 2
        y += 10
        for ln in blines:
            draw_text(gui.screen, ln, x, y, bf, gfx.BODY_TEXT)
            y += bf.get_height() + 2
        by = rect.bottom - 22 - n * bh - (n - 1) * gap
        for i, (label, _run, style) in enumerate(self.options):
            r = pygame.Rect(x, by, rect.w - 52, bh)
            self.button(gui, r, label, f"opt{i}")
            if style == "danger":
                pygame.draw.rect(gui.screen, RED, r, 2, border_radius=10)
            by += bh + gap
        risky = any(style == "danger" for _l, _r, style in self.options)             # then 'never mind' is the highlighted, safe choice
        self.button(gui, pygame.Rect(x, by, rect.w - 52, bh), self.cancel, "cancel", True, risky, risky)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name == "cancel":
            self.choose(None)
        elif name and name.startswith("opt"):
            self.choose(int(name[3:]))

    def key(self, gui, ev):
        if ev.key == pygame.K_ESCAPE:
            self.choose(None)
        elif pygame.K_1 <= ev.key <= pygame.K_9 and ev.key - pygame.K_1 < len(self.options):
            self.choose(ev.key - pygame.K_1)


class RewindDialog(OptionsDialog):
    """Round UNDO1: the Undo window. One button per moment of this turn (rewind.choices: newest first, the start of the turn
    last; keys 1-9), and 'Keep playing'. A choice calls pick(point, label); closing it without one calls cancelled()."""

    def __init__(self, choices, pick, cancelled=None):
        self.choices, self.pick, self.cancelled = list(choices), pick, cancelled
        super().__init__("Go back to which moment of your turn?",
                         "The game is rebuilt from the first turn with the same shuffle and your same clicks, up to the moment you "
                         "pick. A long game takes a while. If the rebuilt game doesn't match, your game stays as it is.",
                         [(label, None, "normal") for _p, label in self.choices], "Keep playing")

    def choose(self, index):
        self.done = True
        if index is None:
            if self.cancelled is not None:
                self.cancelled()
            return
        point, label = self.choices[index]
        self.pick(point, label)


class GameOverDialog(Dialog):
    """Victory / Defeat with the two things you want next: a new game (deck screen) or a look at the final board."""

    def __init__(self, heading, text, colour, on_new):
        super().__init__()
        self.heading, self.text, self.colour, self.on_new = heading, text, colour, on_new

    def draw(self, gui):
        L = gui.L
        k = max(1.0, L.fs)
        pw = min(int(700 * k), L.W - 40)
        hf, bf = gui.font("big", True), gui.font("body")
        blines = wrap_text(self.text, bf, pw - 60)
        bh = int(max(42, 46 * L.fs))
        need = 18 + hf.get_height() + 12 + len(blines) * (bf.get_height() + 2) + 30 + bh + 22
        rect = self.panel(gui, pw, min(need, L.H - 40), real=True)
        y = rect.y + 18
        draw_text(gui.screen, self.heading, rect.centerx, y, hf, self.colour, "midtop")
        y += hf.get_height() + 12
        for ln in blines:
            draw_text(gui.screen, ln, rect.centerx, y, bf, WHITE, "midtop")
            y += bf.get_height() + 2
        by = rect.bottom - bh - 22
        nw = max(gui.button_width("New game"), int(200 * L.fs))
        cw = max(gui.button_width("Look at the board"), int(230 * L.fs))
        x = rect.centerx - (nw + 14 + cw) // 2
        self.button(gui, pygame.Rect(x, by, nw, bh), "New game", "new", True, True, True)
        self.button(gui, pygame.Rect(x + nw + 14, by, cw, bh), "Look at the board", "close")

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name == "new":
            self.done = True
            self.on_new()
        elif name == "close":
            self.done = True

    def key(self, gui, ev):
        if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_n):
            self.done = True
            self.on_new()
        elif ev.key in (pygame.K_ESCAPE, pygame.K_SPACE):
            self.done = True


def game_over_dialog(state, my_name, on_new=None, spectator=False):
    on_new = on_new or (lambda: None)
    winner = state.get("winner") or ""
    if not winner:
        return GameOverDialog("Game over", "The game has ended.", GOLD, on_new)
    if spectator:                                    # Round MP2: someone watching an online game
        return GameOverDialog("Game over", f"{winner} won the game.", GOLD, on_new)
    won = winner == my_name
    return GameOverDialog("Victory!" if won else "Defeat", f"{winner} won the game." if not won else "You won the game.",
                          GREEN if won else RED, on_new)


def make_request_dialog(request, gui):
    """The dialog that answers one request from Forge."""
    kind = request.get("kind")
    if kind == "confirm":
        return ConfirmDialog(request)
    if kind == "input":
        return InputDialog(request)
    if kind == "assign":
        return AssignDialog(request)
    items = request.get("items", [])
    if "opening hand" in str(request.get("title", "")).lower() and items and all(it.get("kind") == "card" for it in items):
        return OpeningHandDialog(request)
    return ChooseDialog(request.get("title", "Choose"), items, request=request, mode="choose",
                        minimum=request.get("min", 1), maximum=request.get("max", 1), ordered=(kind == "order"),
                        source=request.get("source"), optional=(kind == "choose_optional"))


def info_dialog(msg):
    items = msg.get("items", [])
    return ChooseDialog(msg.get("title", ""), items, mode="view")


def zone_dialog(player, zone, on_pick):
    cards = player["zones"].get(zone, [])
    title = f"{player['name']} - {zone}"
    return ChooseDialog(title, [{"kind": "card", "card": c} for c in cards], mode="view", on_pick=on_pick)
