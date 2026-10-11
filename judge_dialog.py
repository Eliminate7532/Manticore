# SPDX-License-Identifier: GPL-3.0-or-later
"""Round JDG1: the Judge?! window - a card's Oracle text and its official rulings (judge_text.py has the words, rulings.py the
fetching). A modal like Help: while it is open the table waits, and a question Forge asks meanwhile opens when it closes."""
import pygame

import gfx
import judge_text as jt
import rulings as rul
from forge_dialogs import Dialog
from gfx import DIM, GOLD, WHITE, clip_text, draw_text, wrap_text

WIDTH = 820                            # at 100% text; grows with the text scale, never wider than the window
SCROLL_STEP = 40


class JudgeDialog(Dialog):
    def __init__(self, card, stack=None, who="", targets=()):
        """card: the snapshot's card; stack: (index, count, item) when it is there as a stack item."""
        super().__init__()
        self.card = dict(card or {})
        self.stack = stack
        self.who = who
        self.targets = list(targets or ())
        self.scroll = 0
        self.max_scroll = 0
        self.viewport = pygame.Rect(0, 0, 0, 0)
        self.asked = False             # card data asked for once (legality's background fetch)

    # ---- what to show --------------------------------------------------------------------------------------------------
    def scryfall_card(self, gui):
        """Scryfall's card object from the table's card cache (never the network on this thread), or None."""
        store = gui.art.store if getattr(gui, "art", None) else None
        peek = getattr(store, "peek_card", None)
        if peek is None or self.card.get("hidden"):
            return None
        names = [n for n in (self.card.get("name"), self.card.get("oracleName")) if n]
        for n in names:
            try:
                found = peek(n)
            except Exception:
                found = None
            if found:
                return found
        if names and not self.asked and not self.card.get("token"):
            self.asked = True
            import legality                                    # one background fetch, as the deck screen's legality check does
            legality.ensure_card_data(names[:1], store)
        return None

    def blocks(self, gui):
        scry = self.scryfall_card(gui)
        status, found = rul.get(scry) if scry else (rul.NO_CARD, [])
        return jt.page(self.card, scry, status, found, self.stack, self.who, self.targets, offline=rul.offline())

    # ---- drawing -------------------------------------------------------------------------------------------------------
    def rows(self, gui, blocks, width):
        """[(kind, text, font, colour, x offset, height)] for everything under the title."""
        bf, bb, sf, sb = gui.font("body"), gui.font("body", True), gui.font("small"), gui.font("small", True)
        hf = gui.ui_font("small", True)
        out = []

        def para(text, font, colour, indent=0, gap=0):
            for i, ln in enumerate(wrap_text(text, font, width - indent) or [""]):
                out.append(("line", ln, font, colour, indent, font.get_height() + (gap if i == 0 else 0)))
        for b in blocks:
            kind = b[0]
            if kind in ("title", "sub"):
                continue
            if kind == "head":
                out.append(("head", b[1], hf, GOLD, 0, hf.get_height() + 14))
            elif kind == "face":
                para(b[1], bb, WHITE, 0, 4)
            elif kind == "type":
                para(b[1], bf, gfx.SOFT_TEXT)
            elif kind == "text":
                para(b[1], bf, WHITE, 0, 2)
            elif kind == "pt":
                para(b[1], bb, WHITE)
            elif kind == "note":
                para(b[1], bf, DIM)
            elif kind == "ruling":
                _k, date, source, comment = b
                para(f"{date}  -  {source}" if source else date, sb, GOLD, 0, 6)
                para(comment, bf, WHITE, int(14 * max(1.0, gui.L.fs)))
            elif kind == "credit":
                para(b[1], gui.font("tiny"), DIM, 0, 12)
        return out

    def draw(self, gui):
        L = gui.L
        blocks = self.blocks(gui)
        tf, bf = gui.font("title", True), gui.font("body")
        width = min(int(WIDTH * max(1.0, L.fs)), L.W - 40)
        inner = width - 44
        title = next((b[1] for b in blocks if b[0] == "title"), "")
        subs = [ln for b in blocks if b[0] == "sub" for ln in wrap_text(b[1], bf, inner)]
        rows = self.rows(gui, blocks, inner)
        bh = int(max(36, 40 * L.fs))
        head = 16 + tf.get_height() + len(subs) * bf.get_height() + 10
        foot = bh + 24
        total = sum(r[5] for r in rows) + 8
        rect = self.panel(gui, width, head + total + foot, real=True)
        x0 = rect.x + 22
        draw_text(gui.screen, clip_text(title, tf, inner), x0, rect.y + 14, tf, GOLD)
        y = rect.y + 14 + tf.get_height()
        for ln in subs:
            gui.draw_line(ln, x0, y, bf, gfx.SOFT_TEXT)
            y += bf.get_height()
        self.viewport = pygame.Rect(rect.x + 2, rect.y + head, rect.w - 4, max(20, rect.h - head - foot))
        self.max_scroll = max(0, total - self.viewport.h)
        self.scroll = max(0, min(self.scroll, self.max_scroll))
        saved = gui.screen.get_clip()
        gui.screen.set_clip(self.viewport)
        y = self.viewport.y - self.scroll
        for kind, text, font, colour, dx, h in rows:
            if y + h > self.viewport.y and y < self.viewport.bottom:
                if kind == "head":
                    ty = y + 8
                    draw_text(gui.screen, text, x0, ty, font, colour)
                    pygame.draw.line(gui.screen, gfx.GOLD_DARK, (x0, ty + font.get_height() + 2),
                                     (rect.right - 22, ty + font.get_height() + 2), 1)
                else:
                    gui.draw_line(text, x0 + dx, y + (h - font.get_height()), font, colour)
            y += h
        gui.screen.set_clip(saved)
        if self.max_scroll:
            draw_text(gui.screen, "Mouse wheel: more", x0, rect.bottom - bh - 12 + bh // 2, gui.font("small"), DIM, "midleft")
        bw = max(gui.button_width("Close"), int(110 * L.fs))
        self.button(gui, pygame.Rect(rect.right - bw - 18, rect.bottom - bh - 12, bw, bh), "Close", "close", True, True)

    # ---- input ---------------------------------------------------------------------------------------------------------
    def wheel(self, gui, dy):
        self.scroll = max(0, min(self.max_scroll, self.scroll - dy * SCROLL_STEP))

    def click(self, gui, pos, button):
        if self.button_at(pos) == "close":
            self.done = True

    def key(self, gui, ev):
        k = ev.key
        page = max(SCROLL_STEP, self.viewport.h - SCROLL_STEP)
        if k in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE, pygame.K_j):
            self.done = True
        elif k == pygame.K_DOWN:
            self.scroll = min(self.max_scroll, self.scroll + SCROLL_STEP)
        elif k == pygame.K_UP:
            self.scroll = max(0, self.scroll - SCROLL_STEP)
        elif k == pygame.K_PAGEDOWN:
            self.scroll = min(self.max_scroll, self.scroll + page)
        elif k == pygame.K_PAGEUP:
            self.scroll = max(0, self.scroll - page)
        elif k == pygame.K_HOME:
            self.scroll = 0
        elif k == pygame.K_END:
            self.scroll = self.max_scroll
