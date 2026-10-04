# SPDX-License-Identifier: GPL-3.0-or-later
"""
art_picker.py - Round ALT1: choose which printing of each card a deck shows (deck screen > Card art).

Screen 1, the grid: the deck's cards, one tile each (sorted by type, then name; a card that appears many times - basics - is
shown once). A gold dot marks a card with a chosen printing. Screen 2, the printings: click a tile to see every printing of that
card (Scryfall's small pictures, fetched on the art loader's thread; "Loading printings..." meanwhile); "Default" is always first.
Hover shows the set and year, a click picks it. The pick is written into the deck file as Moxfield writes it ("1 Sol Ring (C21)
263"), so the deck still round-trips with Moxfield. A sample deck is never changed: the first pick offers to save a copy.
Pictures from my_art/ (your own) replace Scryfall's; "Reload my art" scans the folder again.

Round ALT2: screen 2 has two tabs, Printings (Scryfall, as above) and MPC Autofill: that card's community renders from
mpcfill.com (mpc_art.py), searched when the tab is first opened, shown as Google Drive's small thumbnails (hover: who made it,
its DPI and tags). A click picks one: only that picture is downloaded, and the deck file gets a "# art: <card> = mpc:<id>" line
(deck_library.set_mpc_art) while its card line stays as Moxfield wrote it. Default, or any printing, takes the MPC picture out.
"""
import pygame

import art_loader
import card_data
import deck_library as lib
import forge_dialogs as dlg
import gfx
import mpc_art
from deck_importer import DeckImportError
from gfx import DIM, GOLD, GREEN, ORANGE, RED, WHITE, clip_text, draw_text, round_rect, wrap_text

CARD_ASPECT = 488 / 680
ZOOM_STEPS = (0.6, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)      # patch 38: the card size (- / +, Ctrl+wheel); 1.0 = as before
TYPE_ORDER = ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment", "Land")


def type_rank(store, name):
    """Where a card goes in the grid: by its main type (TYPE_ORDER), unknown cards last."""
    peek = getattr(store, "peek_card", None) if store is not None else None
    card = peek(name) if peek else None
    if not card:
        return len(TYPE_ORDER) + 1
    t = card.get("type_line") or ((card.get("card_faces") or [{}])[0].get("type_line") or "")
    t = t.split("//")[0]
    for i, word in enumerate(TYPE_ORDER):
        if word in t:
            return i
    return len(TYPE_ORDER)


def deck_cards(entry, store=None):
    """The deck's card names, each once: the commanders first, then by type and name."""
    seen, out = set(), []
    for n in list(entry.commanders or []):
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    rest = sorted({n for n in entry.deck or [] if n.lower() not in seen}, key=lambda n: (type_rank(store, n), n.lower()))
    return out + rest


class ArtPicker(dlg.Dialog):
    def __init__(self, menu, entry):
        super().__init__()
        self.menu = menu
        self.entry = entry.load()
        self.card = None                     # None: the grid; a name: that card's printings
        self.scroll = 0
        self.content_h = 0
        self.tiles = []                      # (rect, value) this frame: a card name (grid) or (set, cn) / None (printings)
        self.area = pygame.Rect(0, 0, 0, 0)
        self.hover_info = ""
        self.message = None                  # (text, colour) under the title
        self._cards = None
        self.source = "scryfall"             # round ALT2: the card screen's tab, "scryfall" (printings) or "mpc"

    # ---- state
    def cards(self, gui):
        if self._cards is None:
            self._cards = deck_cards(self.entry, getattr(getattr(gui, "art", None), "store", None))
        return self._cards

    def chosen(self, name):
        return (self.entry.printings or {}).get(name.lower())

    def key_for(self, name, printing):
        return card_data.art_key(name, *printing) if printing else name

    # ---- patch 38: the card size
    def zoom(self, gui):
        z = getattr(gui, "art_picker_zoom", 1.0)
        return min(ZOOM_STEPS, key=lambda step: abs(step - (z if isinstance(z, (int, float)) else 1.0)))

    def change_zoom(self, gui, step):
        """One step bigger (+1) or smaller (-1); remembered in settings.json (art_picker_zoom). The scroll keeps the same place."""
        old = self.zoom(gui)
        i = max(0, min(len(ZOOM_STEPS) - 1, ZOOM_STEPS.index(old) + step))
        new = ZOOM_STEPS[i]
        if new == old:
            return
        self.scroll = int(self.scroll * new / old)
        gui.art_picker_zoom = new
        save = getattr(gui, "save_settings", None)
        if save:
            save()

    def tile_width(self, gui, label_h):
        """A tile's width: the base size (110 px at 100% text) times the card size, but never wider than the area or taller than
        it (one whole card always fits)."""
        w = int(max(84, 110 * gui.L.fs) * self.zoom(gui))
        w = min(w, max(40, self.area.w - 10))
        room = self.area.h - label_h - 4
        if room > 0:
            w = min(w, int(room * CARD_ASPECT))
        return max(40, w)

    def open_card(self, name):
        self.card, self.scroll = name, 0
        self.source = "mpc" if mpc_art.is_mpc_printing(self.chosen(name)) else "scryfall"

    def mpc_info(self, gui, drive_id):
        """The kept details of an MPC Autofill picture ({source, dpi, tags, name}), or None."""
        store = getattr(getattr(gui, "art", None), "store", None)
        get = getattr(store, "mpc_card", None)
        return get(drive_id) if get else None

    def describe(self, gui, printing):
        """A chosen printing in words: "C21 263", or "MPC Autofill (by Chilli_Axe)"."""
        if mpc_art.is_mpc_printing(printing):
            c = self.mpc_info(gui, printing[1])
            return "MPC Autofill" + (f" (by {c['source']})" if c and c.get("source") else "")
        return f"{printing[0].upper()} {printing[1]}"

    def back_to_grid(self):
        self.card, self.scroll = None, 0

    def pick(self, gui, printing):
        """Write the pick into the deck file (a sample deck: offer to copy it first)."""
        name = self.card
        if self.entry.builtin:
            def copy_then_pick():
                try:
                    new = lib.copy_to_library(self.entry, self.menu.library_dir, self.menu.base_dir)
                except (DeckImportError, OSError) as e:
                    self.menu.say(f"Could not copy the deck: {e}", RED)
                    gui.modal = self
                    return
                self.menu.imported(new)
                self.entry = new
                self._cards = None
                gui.modal = self
                self.done = False
                self.pick(gui, printing)
            q = dlg.QuestionDialog("Save a copy to My decks?",
                                   f"'{self.entry.name}' came with the program and isn't changed. A copy in My decks keeps your "
                                   "choice of card art (and it's the copy you then play).", "Save a copy", "Cancel", copy_then_pick)
            back = self

            def answer(value, _orig=q.answer):
                _orig(value)
                if not value:
                    gui.modal = back
                    back.done = False
            q.answer = answer
            gui.modal = q
            return
        try:
            lib.set_printing(self.entry, name, printing)
        except (DeckImportError, OSError) as e:
            self.message = (str(e), RED)
            return
        if printing:
            self.message = (f"{name}: {self.describe(gui, printing)} saved in '{self.entry.name}'.", GREEN)
        else:
            self.message = (f"{name}: back to the default picture.", GREEN)
        if gui.art is not None:
            gui.art.add_names([self.key_for(name, printing)])
        self.back_to_grid()

    # ---- input
    def click(self, gui, pos, button):
        if button != 1:
            return
        name = self.button_at(pos)
        if name == "close":
            self.done = True
            return
        if name == "back":
            self.back_to_grid()
            return
        if name in ("src_scryfall", "src_mpc"):                   # round ALT2: the card screen's two tabs
            self.source, self.scroll = name[4:], 0
            return
        if name == "retry_mpc":
            if gui.art is not None and self.card:
                gui.art.retry_mpc(self.card)
            return
        if name in ("zoom_in", "zoom_out"):                       # patch 38
            self.change_zoom(gui, 1 if name == "zoom_in" else -1)
            return
        if name == "reload":
            if gui.art is not None:
                gui.art.reload_custom()
                n = len(gui.art.custom)
                self.message = (f"my_art: {n} picture{'s' if n != 1 else ''} found.", GREEN if n else DIM)
            return
        if not self.area.collidepoint(pos):
            return
        for rect, value in self.tiles:
            if rect.collidepoint(pos):
                if self.card is None:
                    self.open_card(value)
                else:
                    self.pick(gui, value)
                return

    def key(self, gui, ev):
        if ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):          # patch 38: the card size
            self.change_zoom(gui, 1)
            return
        if ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.change_zoom(gui, -1)
            return
        if ev.key == pygame.K_ESCAPE:
            if self.card is not None:
                self.back_to_grid()
            else:
                self.done = True

    def wheel(self, gui, dy):
        if pygame.key.get_mods() & pygame.KMOD_CTRL:              # patch 38: Ctrl+wheel changes the card size
            if dy:
                self.change_zoom(gui, 1 if dy > 0 else -1)
            return
        step = int(60 * max(1.0, gui.L.fs))
        self.scroll = max(0, min(self.scroll - dy * step, max(0, self.content_h - self.area.h)))

    # ---- drawing
    def draw(self, gui):
        L, scr = gui.L, gui.screen
        fs = L.fs
        rect = self.panel(gui, L.W - 40, L.H - 40, real=True)
        title, small, tiny = gui.font("title", True), gui.font("small"), gui.font("tiny", True)
        pad = int(max(14, 18 * fs))
        x, y, tw = rect.x + pad, rect.y + pad, rect.w - 2 * pad
        bh = int(max(34, 40 * fs))
        cw = max(gui.button_width("Close"), int(120 * fs))
        self.button(gui, pygame.Rect(rect.right - pad - cw, y, cw, bh), "Close", "close", True)
        right = rect.right - pad - cw - 10
        # patch 38: the card size, - and + (also the - / + keys and Ctrl+wheel)
        z = self.zoom(gui)
        self.button(gui, pygame.Rect(right - bh, y, bh, bh), "+", "zoom_in", z < ZOOM_STEPS[-1])
        self.button(gui, pygame.Rect(right - 2 * bh - 6, y, bh, bh), "\u2212", "zoom_out", z > ZOOM_STEPS[0])
        right -= 2 * bh + 6
        size_label = "Card size"
        if title.size(size_label)[0] < (right - x) // 3:
            lw = small.size(size_label)[0]
            draw_text(scr, size_label, right - 8, y + bh // 2, small, gfx.BODY_TEXT, "midright")
            right -= lw + 16
        else:
            right -= 10
        if self.card is None:
            if gui.art is not None:
                rw = max(gui.button_width("Reload my art"), int(160 * fs))
                self.button(gui, pygame.Rect(right - rw, y, rw, bh), "Reload my art", "reload", True)
                right -= rw + 10
            head = f"Card art  -  {self.entry.name}"
        else:
            bw = max(gui.button_width("Back"), int(110 * fs))
            self.button(gui, pygame.Rect(right - bw, y, bw, bh), "Back", "back", True)
            right -= bw + 10
            head = self.card
        draw_text(scr, clip_text(head, title, right - x - 10), x, y + bh // 2, title, WHITE, "midleft")
        y += bh + 6
        if self.card is not None:                                # round ALT2: Printings | MPC Autofill
            th = int(max(28, 32 * fs))
            tx = x
            for src, label in (("scryfall", "Printings"), ("mpc", "MPC Autofill")):
                tw_ = max(gui.button_width(label), int(150 * fs))
                self.button(gui, pygame.Rect(tx, y, tw_, th), label, "src_" + src, True, src == self.source, False)
                tx += tw_ + int(8 * fs)
            pygame.draw.line(scr, GOLD, (x, y + th + 3), (x + tw, y + th + 3), 1)
            y += th + 8
        if self.card is None:
            hint = ("Click a card to choose its printing or an MPC Autofill picture. A gold dot marks a card with a chosen "
                    "picture. Card size: \u2212 and + (or Ctrl+wheel). Your own pictures go in the my_art folder (\"Sol Ring.png\", or "
                    "\"Sol Ring__C21_263.png\" for one printing).")
        elif self.source == "mpc":
            hint = ("Community renders from MPC Autofill (mpcfill.com); each belongs to the person who made it. Only the "
                    "picture you click is downloaded. Hover for who made it and its DPI. Esc goes back.")
        else:
            hint = "Click a printing to use it in this deck. Default is Scryfall's usual picture. Esc goes back."
        for ln in wrap_text(hint, small, tw)[:2]:
            draw_text(scr, ln, x, y, small, gfx.BODY_TEXT)
            y += small.get_height()
        if self.message:
            draw_text(scr, clip_text(self.message[0], small, tw), x, y, small, self.message[1])
        y += small.get_height() + 6
        foot = tiny.get_height() + 8
        self.area = pygame.Rect(x, y, tw, rect.bottom - pad - foot - y)
        self.tiles = []
        self.hover_info = ""
        if self.card is None:
            self.draw_grid(gui)
        elif self.source == "mpc":
            self.draw_mpc(gui)
        else:
            self.draw_printings(gui)
        if self.hover_info:
            draw_text(scr, clip_text(self.hover_info, tiny, tw), x, rect.bottom - pad - tiny.get_height(), tiny, GOLD)
        if self.content_h > self.area.h:
            frac = self.area.h / self.content_h
            bar_h = max(30, int(self.area.h * frac))
            bar_y = self.area.y + int((self.area.h - bar_h) * (self.scroll / max(1, self.content_h - self.area.h)))
            pygame.draw.rect(scr, gfx.SCROLLBAR, pygame.Rect(self.area.right - 5, bar_y, 5, bar_h), border_radius=3)

    def _layout(self, n, tile_w, label_h):
        gap = max(6, tile_w // 12)
        cols = max(1, (self.area.w - 10 + gap) // (tile_w + gap))
        tile_h = int(tile_w / CARD_ASPECT)
        rows = (n + cols - 1) // cols
        self.content_h = rows * (tile_h + label_h + gap)
        self.scroll = max(0, min(self.scroll, max(0, self.content_h - self.area.h)))
        return gap, cols, tile_h

    def _picture(self, gui, key, w, h, small=False):
        art = gui.art
        if art is None:
            return None
        if small and card_data.is_art_key(key) and not art.custom_path(key):
            return art.small(key, w, h)
        k = ("picker", key, w, h, art.custom_gen if art.is_custom(key) else 0)
        hit = gfx.recall(k)
        if hit is not None:
            return hit
        radius = max(3, int(w * 0.05))
        if h > art_loader.SOURCE_SIZE[1] + 20:          # patch 38: a big card size - the full picture, not the 244x340 copy
            big = art.preview(key, w, h)
            if big is not None:
                return gfx.remember(k, gfx.rounded_image(big, w, h, radius))
        img = art.get(key)
        if img is None:
            return None
        surf = gfx.rounded_image(img, w, h, radius)
        if h > art_loader.SOURCE_SIZE[1] + 20:          # the small copy only until the full one is here: not remembered
            return surf
        return gfx.remember(k, surf)

    def draw_grid(self, gui):
        scr, fs = gui.screen, gui.L.fs
        tiny = gui.font("tiny", True)
        names = self.cards(gui)
        label_h = tiny.get_height() + 4
        tile_w = self.tile_width(gui, label_h)
        gap, cols, tile_h = self._layout(len(names), tile_w, label_h)
        scr.set_clip(self.area)
        for i, name in enumerate(names):
            r = pygame.Rect(self.area.x + (i % cols) * (tile_w + gap),
                            self.area.y + (i // cols) * (tile_h + label_h + gap) - self.scroll, tile_w, tile_h)
            if r.bottom < self.area.y or r.y > self.area.bottom:
                continue
            pr = self.chosen(name)
            pic = self._picture(gui, self.key_for(name, pr), tile_w, tile_h)
            if pic is None:
                pic = gfx.card_face({"name": name}, tile_w, tile_h, max(3, int(tile_w * 0.05)))
            scr.blit(pic, r)
            hot = r.collidepoint(gui.mouse) and self.area.collidepoint(gui.mouse)
            if hot:
                pygame.draw.rect(scr, GOLD, r.inflate(4, 4), 2, border_radius=4)
                self.hover_info = name + (f"  -  {self.describe(gui, pr)}" if pr else "  -  default picture")
            if pr:
                rad = max(4, int(5 * fs))
                pygame.draw.circle(scr, gfx.BADGE_DARK, (r.right - rad - 3, r.y + rad + 3), rad + 2)
                pygame.draw.circle(scr, GOLD, (r.right - rad - 3, r.y + rad + 3), rad)
            draw_text(scr, clip_text(name, tiny, tile_w), r.centerx, r.bottom + 2, tiny, WHITE if hot else DIM, "midtop")
            self.tiles.append((r.clip(self.area), name))
        scr.set_clip(None)

    def draw_printings(self, gui):
        scr, fs = gui.screen, gui.L.fs
        tiny, small = gui.font("tiny", True), gui.font("small")
        name = self.card
        prints = gui.art.printings(name) if gui.art is not None else []
        options = [None] + [(p["set"], p["cn"]) for p in (prints or []) if p.get("set") and p.get("cn")]
        info = {(p["set"], p["cn"]): p for p in (prints or []) if p.get("set")}
        label_h = tiny.get_height() + 4
        tile_w = self.tile_width(gui, label_h)
        gap, cols, tile_h = self._layout(len(options) + (1 if prints is None else 0), tile_w, label_h)
        current = self.chosen(name)
        if mpc_art.is_mpc_printing(current):                     # round ALT2: an MPC picture is chosen; no printing is
            current = None                                       # marked, and neither is Default
            mpc_chosen = True
        else:
            mpc_chosen = False
        scr.set_clip(self.area)
        for i, pr in enumerate(options):
            r = pygame.Rect(self.area.x + (i % cols) * (tile_w + gap),
                            self.area.y + (i // cols) * (tile_h + label_h + gap) - self.scroll, tile_w, tile_h)
            if r.bottom < self.area.y or r.y > self.area.bottom:
                continue
            pic = self._picture(gui, self.key_for(name, pr), tile_w, tile_h, small=pr is not None)
            if pic is None:
                round_rect(scr, r, gfx.LOG_BG, 6, 1, gfx.LOG_EDGE)
                draw_text(scr, pr[0].upper() if pr else "Default", r.centerx, r.centery, small, DIM, "center")
            else:
                scr.blit(pic, r)
            hot = r.collidepoint(gui.mouse) and self.area.collidepoint(gui.mouse)
            sel = (pr is None and not current and not mpc_chosen) or (pr is not None and current and tuple(current) == tuple(pr))
            if sel or hot:
                pygame.draw.rect(scr, GOLD, r.inflate(4, 4), 3 if sel else 2, border_radius=4)
            if pr is None:
                label = "Default"
            else:
                label = f"{pr[0].upper()} {pr[1]}"
            if hot:
                p = info.get(pr) if pr else None
                self.hover_info = ("Scryfall's usual picture" if pr is None else
                                   f"{p.get('set_name') or pr[0].upper()} ({(p.get('released') or '')[:4]})  -  {pr[0].upper()} {pr[1]}"
                                   if p else label)
            draw_text(scr, clip_text(label, tiny, tile_w), r.centerx, r.bottom + 2, tiny, GOLD if sel else (WHITE if hot else DIM),
                      "midtop")
            self.tiles.append((r.clip(self.area), pr))
        if prints is None:
            i = len(options)
            r = pygame.Rect(self.area.x + (i % cols) * (tile_w + gap), self.area.y + (i // cols) * (tile_h + label_h + gap) - self.scroll,
                            self.area.w - (i % cols) * (tile_w + gap), tile_h)
            draw_text(scr, "Loading printings...", r.x + 10, r.centery, small, DIM, "midleft")
        elif not prints:
            draw_text(scr, "No other printings found (or Scryfall can't be reached right now).",
                      self.area.x + tile_w + gap + 10, self.area.y + tile_h // 2, small, ORANGE, "midleft")
        scr.set_clip(None)

    # ---- round ALT2: the MPC Autofill tab
    def draw_mpc(self, gui):
        scr, fs = gui.screen, gui.L.fs
        tiny, small = gui.font("tiny", True), gui.font("small")
        name, art = self.card, gui.art
        found = art.mpc_results(name) if art is not None else []
        label_h = tiny.get_height() + 4
        tile_w = self.tile_width(gui, label_h)
        gap, cols, tile_h = self._layout(len(found or []), tile_w, label_h)
        if found is None:
            draw_text(scr, "Searching MPC Autofill...", self.area.x + 10, self.area.y + small.get_height(), small, DIM, "midleft")
            return
        if not found:
            err = art.mpc_error(name) if art is not None else None
            ty = self.area.y + small.get_height()
            if err:
                why = f"The search didn't work: {err}. Your internet connection, or mpcfill.com, may be down."
                for ln in wrap_text(why, small, self.area.w - 20)[:2]:
                    draw_text(scr, ln, self.area.x + 10, ty, small, ORANGE, "midleft")
                    ty += small.get_height()
                bw = max(gui.button_width("Try again"), int(130 * fs))
                bh = int(max(30, 34 * fs))
                self.button(gui, pygame.Rect(self.area.x + 10, ty, bw, bh), "Try again", "retry_mpc", True)
            elif art is None:
                draw_text(scr, "Card pictures aren't available here.", self.area.x + 10, ty, small, DIM, "midleft")
            else:
                draw_text(scr, "MPC Autofill has no pictures of this card.", self.area.x + 10, ty, small, DIM, "midleft")
            return
        current = self.chosen(name)
        scr.set_clip(self.area)
        for i, c in enumerate(found):
            r = pygame.Rect(self.area.x + (i % cols) * (tile_w + gap),
                            self.area.y + (i // cols) * (tile_h + label_h + gap) - self.scroll, tile_w, tile_h)
            if r.bottom < self.area.y or r.y > self.area.bottom:
                continue
            pr = (mpc_art.MPC_SET, c["id"])
            key = card_data.art_key(name, *pr)
            pic = self._picture(gui, key, tile_w, tile_h, small=True)
            if pic is None:
                round_rect(scr, r, gfx.LOG_BG, 6, 1, gfx.LOG_EDGE)
                gone = art is not None and art.small_unavailable(key)
                draw_text(scr, "No picture" if gone else "...", r.centerx, r.centery, tiny if gone else small, DIM, "center")
            else:
                scr.blit(pic, r)
            hot = r.collidepoint(gui.mouse) and self.area.collidepoint(gui.mouse)
            sel = bool(current) and tuple(current) == pr
            if sel or hot:
                pygame.draw.rect(scr, GOLD, r.inflate(4, 4), 3 if sel else 2, border_radius=4)
            if hot:
                bits = [c.get("source") or "unknown maker"]
                if c.get("dpi"):
                    bits.append(f"{c['dpi']} DPI")
                if c.get("tags"):
                    bits.append(", ".join(c["tags"]))
                bits.append(c.get("name") or name)
                self.hover_info = "  -  ".join(bits)
            draw_text(scr, clip_text(c.get("source") or "?", tiny, tile_w), r.centerx, r.bottom + 2, tiny,
                      GOLD if sel else (WHITE if hot else DIM), "midtop")
            self.tiles.append((r.clip(self.area), pr))
        scr.set_clip(None)
