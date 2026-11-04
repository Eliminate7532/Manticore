# SPDX-License-Identifier: GPL-3.0-or-later
"""rpg_results.py - Round PROG1: the results screen after every Adventure duel, Final Fantasy style (Karl, 2 Nov 2026: "make
progression more tangible and 'fun' JRPG style"; the design: claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.1).

Back on the Adventure screen after a won, lost, drawn or conceded duel: a VICTORY / DEFEAT / DRAW banner, the duel's words (every
fact the old result window said - rpg.adventure.outcome_lines), the gold counting from what you carried to what you carry now, each
reward card flipping in one by one with a glow in its tier's colour (gfx.TIER_GLOW), its tier's name and "NEW!" when you never
owned it (rpg.progress.is_new). Under the cards, Round PROG3's XP slot (self.xp_rect): "Level 3" and the XP bar filling from what
you had to what you have (it fills after the cards, over XP_SECONDS; crossing a level shows the new one and plays the level-up
fanfare once, rpg.level_up), "+10 XP" at its right, and the level-up line(s) under it ("Level up! Level 4: ...", "Choose a perk.").

One tap anywhere, Space or Enter finishes it at once; the next one continues (so does the Continue button, once it shows). With
the table's Animations switched off it opens finished.

Round PROG2 ("pick 1 of 3"): after a win the reward cards are a choice (info["offers"]: groups of 3 from rpg.rewards). Each group
is drawn as three cards side by side, the groups in as many rows as gives the biggest cards; the first card of each group starts
picked (a gold frame and "PICKED"). Once the screen has played, a tap on a card picks it in its group; the arrow keys do the same
(Left / Right within a group, Up / Down to the next group). A tap beside the cards does nothing, so a stray tap never takes the
cards: Continue (or Enter / Space) takes the picked ones. A boss's signature card (info["cards"], "signature") is shown first, in a
row of its own, marked "GUARDED". Cards the deck can't cast are dimmed. Timings: rpg/data/progression.json "results" (rpg.progress).
Drawn in DawnLike's window and SDS font at the screen's own pixels, like the shop (rpg_screens.DawnGui's sizes); every colour is a
gfx.py token.
"""
import pygame

import gfx
import rpg_ui
from rpg import progress
from rpg.tiers import TIER_NAMES

CARD_ASPECT = 488 / 680
NEW_TAG = "NEW!"
PICKED_TAG = "PICKED"
GUARDED_TAG = "GUARDED"
OFF_COLOUR_HINT = "Dimmed cards are outside your deck's colours."
PICK_HINT = "Tap a card to pick it."
SKIP_HINT = "Tap to skip"
CONTINUE = "Continue"
BANNER_COLOUR = {"won": gfx.DB_GREEN, "lost": gfx.DB_RED, "conceded": gfx.DB_RED, "draw": gfx.DB_GOLD}
XP_SECONDS = 0.8                    # Round PROG3: how long the XP bar takes to fill
LEVEL_UP_CUE = "rpg.level_up"       # Round PROG3: the fanfare (sounds/cues.json)
MAX_TEXT = "MAX"
AT_ONCE = False                     # tests of other screens open it finished (tests/test_rpg8.Base); never set in the program


def tier_colour(tier):
    """THE tier colours (one place): gfx.TIER_GLOW by tier 0-5; an unknown tier is dim."""
    if isinstance(tier, int) and not isinstance(tier, bool) and 0 <= tier < len(gfx.TIER_GLOW):
        return gfx.TIER_GLOW[tier]
    return gfx.DB_DIM


def unit_for(gui):
    """The font's pixel size: about the table's "small" text, a whole multiple of 8 (rpg_screens.DawnGui's rule)."""
    try:
        return 8 * max(1, int(gui.L.px["small"] / 8 + 0.5))
    except (AttributeError, KeyError, TypeError):
        return 16


class ResultsScreen:
    """info: rpg.progress.results(...); now: time.monotonic() when it opened; picture(name, card, w, h) -> Surface (the
    Adventure's card_picture); pool: the card pool (for the stand-ins)."""

    def __init__(self, info, now, numbers=None, picture=None, pool=None, animations=True):
        self.info = info
        self.n = numbers or progress.read()
        self.picture, self.pool = picture, pool
        self.t0 = now
        self.offers = [list(g) for g in info.get("offers") or ()]          # Round PROG2: [[card dict, ...], ...]
        self.picks = [0 for _g in self.offers]                             # the picked card's place in each group
        self.focus = 0                                                     # the group the arrow keys move in
        self.xp = info.get("xp") if isinstance(info.get("xp"), dict) else None         # Round PROG3
        self.length = self.n.results_seconds(self.card_count()) + (XP_SECONDS if self.xp else 0.0)
        self.fanfare = False                # Round PROG3: the level-up fanfare played (once)
        if AT_ONCE or not animations:
            self.t0 = now - self.length - 1
        self.buttons = []
        self.xp_rect = None
        self.xp_bar_rect = None
        self.xp_words = []
        self.card_rects = []
        self.pick_rects = []                # Round PROG2: [(Rect, group, index)] of the cards to choose from
        self.rect = None

    # ---- Round PROG2: the choice ----
    def card_count(self):
        return len(self.info.get("cards") or ()) + sum(len(g) for g in self.offers)

    def choosing(self):
        return bool(self.offers)

    def picked(self):
        """The picked card's name in each group, in the groups' order."""
        return [g[i]["name"] for g, i in zip(self.offers, self.picks)]

    def pick(self, group, index):
        """Pick card `index` of `group` (a tap on it); True when it changed."""
        if 0 <= group < len(self.offers) and 0 <= index < len(self.offers[group]) and self.picks[group] != index:
            self.picks[group] = index
            self.focus = group
            return True
        if 0 <= group < len(self.offers):
            self.focus = group
        return False

    def arrow(self, dx, dy):
        """The arrow keys: Left / Right move the pick within the focused group, Up / Down move to another group."""
        if not self.offers:
            return False
        if dy:
            self.focus = (self.focus + dy) % len(self.offers)
            return True
        g = self.offers[self.focus]
        return self.pick(self.focus, (self.picks[self.focus] + dx) % len(g))

    # ---- time ----
    def elapsed(self, now):
        return max(0.0, now - self.t0)

    def done(self, now):
        return self.elapsed(now) >= self.length

    def skip(self, now):
        """Everything shown at once (the gold counted, every card turned)."""
        self.t0 = now - self.length - 1

    def tap(self, now):
        """A tap, Space or Enter: "skipped" while it plays, then "continue"."""
        if not self.done(now):
            self.skip(now)
            return "skipped"
        return "continue"

    def gold_shown(self, now):
        a, b = self.info["gold_before"], self.info["gold_after"]
        t = self.elapsed(now) - self.n.banner_seconds
        if t <= 0:
            return a
        if self.n.gold_count_seconds <= 0 or t >= self.n.gold_count_seconds:
            return b
        return int(round(a + (b - a) * t / self.n.gold_count_seconds))

    def card_turn(self, i, now):
        """0 (not yet) .. 1 (face up) for the i-th reward card."""
        start = self.n.banner_seconds + self.n.gold_count_seconds + i * self.n.card_gap_seconds
        t = self.elapsed(now) - start
        if t <= 0:
            return 0.0
        if self.n.card_flip_seconds <= 0:
            return 1.0
        return min(1.0, t / self.n.card_flip_seconds)

    def xp_shown(self, now):
        """Round PROG3: the XP shown now - from what you had to what you have, after the cards have turned."""
        if not self.xp:
            return 0
        a, b = self.xp.get("xp_before", 0), self.xp.get("xp_after", 0)
        t = self.elapsed(now) - (self.length - XP_SECONDS)
        if t <= 0:
            return a
        if t >= XP_SECONDS:
            return b
        return int(round(a + (b - a) * t / XP_SECONDS))

    def level_shown(self, now):
        from rpg import levels
        try:
            return levels.read().level_of(self.xp_shown(now))
        except (OSError, ValueError):
            return (self.xp or {}).get("level_after", 1)

    def draw_xp(self, screen, gui, rect, u, k, lh, now):
        """Round PROG3: the XP slot: "Level 3", the bar, "+10 XP"; under it the level-up lines once the bar has reached them."""
        from rpg import levels
        xp = self.xp
        try:
            lv = levels.read()
        except (OSError, ValueError):
            return
        shown = self.xp_shown(now)
        level, into, need = lv.bar(shown)
        label = f"Level {level}"
        lw = rpg_ui.font(u).size(label)[0]
        gain = f"+{xp.get('gained', 0)} XP"
        gw = rpg_ui.font(u).size(gain)[0]
        up = level > xp.get("level_before", 1)
        rpg_ui.text(screen, label, rect.x, rect.y, gfx.DB_GOLD if up else gfx.DB_TEXT, u)
        rpg_ui.text(screen, gain, rect.right, rect.y, gfx.DB_GREEN if xp.get("gained") else gfx.DB_DIM, u, "topright")
        bar = pygame.Rect(rect.x + lw + u, rect.y + u // 4, max(4 * k, rect.w - lw - gw - 2 * u), max(2 * k, u - u // 4))
        self.xp_bar_rect = bar
        pygame.draw.rect(screen, gfx.DB_BG, bar)
        frac = 1.0 if into is None else (into / need if need else 0.0)
        fill = bar.copy()
        fill.w = int(bar.w * max(0.0, min(1.0, frac)))
        if fill.w:
            pygame.draw.rect(screen, gfx.DB_GOLD if into is None else gfx.DB_GREEN, fill)
        pygame.draw.rect(screen, gfx.DB_DIM, bar, max(1, k))
        if into is None:
            rpg_ui.text(screen, MAX_TEXT, bar.centerx, bar.centery, gfx.DB_BG, u, "center")
        y = rect.y + lh
        self.xp_words = [label, gain]
        if up:
            for ln in self._xp_lines(rect.w, u):
                if y + lh > rect.bottom + 1:
                    break
                rpg_ui.text(screen, ln, rect.x, y, gfx.DB_GOLD, u)
                self.xp_words.append(ln)
                y += lh
            if not self.fanfare and gui is not None and hasattr(gui, "play_cue"):
                self.fanfare = True
                try:
                    gui.play_cue("rpg.level_up")       # LEVEL_UP_CUE (a literal: tests/test_au1 reads it)
                except Exception:
                    pass

    def _xp_lines(self, width, u):
        out = []
        for ln in (self.xp or {}).get("lines") or ():
            out += rpg_ui.wrap(ln, width, u) or [""]
        return out

    # ---- drawing ----
    def text_lines(self):
        """The duel's words without the "New card(s): ..." line (the cards are shown, each with its name)."""
        return [ln for ln in self.info.get("lines") or () if not str(ln).startswith(("New card: ", "New cards: "))]

    def _rows(self):
        """[(label or None, [(card dict, group or None, index)])]: the granted cards' row, then the offers' rows (filled in by
        _grid)."""
        cards = self.info.get("cards") or []
        return [[(c, None, i) for i, c in enumerate(cards)]] if cards else []

    def _grid(self, inner, room_h, gap, lh, k):
        """The card size and the rows of cards. Each row: [(card, group, index, x offset)]; groups keep their three cards side by
        side and a wider gap between groups. Tries every number of groups a row and keeps the one with the biggest cards."""
        granted = self.info.get("cards") or []
        n = len(self.offers)
        per = max([len(g) for g in self.offers] or [0])
        if n and granted:                    # Round PROG2: a boss's card leads the first row of the choice, a group of its own
            return self._grid_with_lead(inner, room_h, gap, lh, granted)
        ggap = 3 * gap
        best = None
        for across in (range(1, n + 1) if n else (0,)):
            rows_off = -(-n // across) if n else 0
            rows = rows_off + (1 if granted else 0)
            if not rows:
                return 0, 0, []
            width_cards = max(across * per + (across - 1) * 0, len(granted), 1)
            w_room = inner - (across * per - across) * gap - max(0, across - 1) * ggap if across else inner
            if granted:
                w_room = min(w_room, inner - (len(granted) - 1) * gap)
            cw_by_w = w_room // max(1, width_cards)
            ch_by_h = (room_h - rows * (lh + gap) - (rows - 1) * gap) // rows
            ch = max(0, min(int(cw_by_w / CARD_ASPECT), ch_by_h))
            if best is None or ch > best[0]:
                best = (ch, across)
        ch, across = best
        cw = int(ch * CARD_ASPECT)
        out = []
        if granted:
            total = len(granted) * cw + (len(granted) - 1) * gap
            out.append([(c, None, i, -total // 2 + i * (cw + gap)) for i, c in enumerate(granted)])
        for r0 in range(0, n, max(1, across)):
            groups = list(range(r0, min(n, r0 + across)))
            widths = [len(self.offers[g]) * cw + (len(self.offers[g]) - 1) * gap for g in groups]
            total = sum(widths) + (len(groups) - 1) * ggap
            x = -total // 2
            row = []
            for g, wg in zip(groups, widths):
                for i, c in enumerate(self.offers[g]):
                    row.append((c, g, i, x + i * (cw + gap)))
                x += wg + ggap
            out.append(row)
        return cw, ch, out

    def _grid_with_lead(self, inner, room_h, gap, lh, granted):
        """The choice's grid with the granted cards as the first group (no row of their own)."""
        groups = [[(c, None, i) for i, c in enumerate(granted)]] + [[(c, g, i) for i, c in enumerate(grp)]
                                                                   for g, grp in enumerate(self.offers)]
        ggap = 3 * gap
        best = None
        for across in range(1, len(groups) + 1):
            rows = [groups[r:r + across] for r in range(0, len(groups), across)]
            cw_by_w = min((inner - sum(len(x) - 1 for x in row) * gap - (len(row) - 1) * ggap) // max(1, sum(len(x) for x in row))
                          for row in rows)
            ch_by_h = (room_h - len(rows) * (lh + gap) - (len(rows) - 1) * gap) // len(rows)
            ch = max(0, min(int(cw_by_w / CARD_ASPECT), ch_by_h))
            if best is None or ch > best[0]:
                best = (ch, rows)
        ch, rows = best
        cw = int(ch * CARD_ASPECT)
        out = []
        for row in rows:
            widths = [len(x) * cw + (len(x) - 1) * gap for x in row]
            x = -(sum(widths) + (len(row) - 1) * ggap) // 2
            line = []
            for grp, wg in zip(row, widths):
                for j, (c, g, i) in enumerate(grp):
                    line.append((c, g, i, x + j * (cw + gap)))
                x += wg + ggap
            out.append(line)
        return cw, ch, out

    def draw(self, screen, gui, now):
        W, H = screen.get_size()
        u = unit_for(gui)
        k = max(1, u // 8)
        big = min(4 * u, 8 * max(1, (W // 14) // 8))            # the banner: four lines' worth, never wider than the window
        if self.choosing():                                      # Round PROG2: the cards need the room - a two-line banner
            big = min(big, 2 * u)
        pad, gap = 2 * u, u
        lh = u + u // 4
        m = max(8, 3 * k)
        choosing = self.choosing()
        win_w = min(W - 2 * m, max(60 * u, W * 2 // 3 if not choosing else W * 7 // 8))
        inner = win_w - 2 * pad
        body = []
        for ln in self.text_lines():
            body += rpg_ui.wrap(ln, inner, u) or [""]
        if choosing and any(not c.get("castable", True) for g in self.offers for c in g):
            body += rpg_ui.wrap(OFF_COLOUR_HINT, inner, u)
        n = self.card_count()
        xp_h = self.n.xp_slot_lines * lh
        if self.xp:                                              # Round PROG3: the bar's line and every level-up line
            xp_h = max(xp_h, (1 + len(self._xp_lines(inner, u))) * lh)
        btn_h = u + 4 * k * 2
        fixed = pad + int(big * 1.25) + gap + len(body) * lh + gap + lh + gap + xp_h + gap + btn_h + pad
        card_w = card_h = 0
        rows = []
        if n:
            room_h = H - 2 * m - fixed - gap - (lh if not choosing else gap)
            if not choosing:
                room_h = min(room_h, int(H * 0.34) + lh + gap)
            rl = 0 if choosing else lh                         # Round PROG2: no name line (the card shows it), tags inside
            card_w, card_h, rows = self._grid(inner, room_h, gap, rl, k)
        rl = 0 if choosing else lh
        cards_h = ((lh if not choosing else gap) + len(rows) * (card_h + rl + gap) + (lh if not choosing and rows else 0)) if rows else 0
        h = min(fixed + cards_h, H - 2 * m)
        rect = pygame.Rect((W - win_w) // 2, (H - h) // 2, win_w, h)
        self.rect = rect
        rpg_ui.window(screen, rect, k)
        x0, y = rect.x + pad, rect.y + pad
        r = self.info.get("result")
        t = self.elapsed(now)
        banner = self.info.get("banner") or ""
        drop = 0 if self.n.banner_seconds <= 0 else int(max(0.0, 1 - t / self.n.banner_seconds) * u)
        rpg_ui.text(screen, banner, rect.centerx, y - drop, BANNER_COLOUR.get(r, gfx.DB_GOLD), big, "midtop")
        y += int(big * 1.25) + gap
        for ln in body:
            rpg_ui.text(screen, ln, x0, y, gfx.DB_TEXT if ln != OFF_COLOUR_HINT else gfx.DB_DIM, u)
            y += lh
        y += gap
        shown = self.gold_shown(now)
        change = self.info.get("gold_change") or 0
        rpg_ui.text(screen, f"Gold {shown}", x0, y, gfx.DB_GOLD, u)
        if change:
            rpg_ui.text(screen, f"{'+' if change > 0 else '-'}{abs(change)}", rect.right - pad, y,
                        gfx.DB_GREEN if change > 0 else gfx.DB_RED, u, "topright")
        y += lh + gap
        self.card_rects = []
        self.pick_rects = []                                                # Round PROG2: [(Rect, group, index)]
        done = self.done(now)
        if rows:
            y += lh if not choosing else gap                             # room for the NEW! tags over the cards
            order = 0
            for row in rows:
                for c, g, i, dx in row:
                    cr = pygame.Rect(rect.centerx + dx, y, card_w, card_h)
                    self.card_rects.append(cr)
                    if g is not None:
                        self.pick_rects.append((cr, g, i))
                    self._card(screen, cr, c, self.card_turn(order, now), u, k, gap, lh,
                               picked=(g is not None and self.picks[g] == i), choosing=choosing)
                    order += 1
                y += card_h + rl + gap
            if not choosing:
                y += lh
        self.xp_rect = pygame.Rect(x0, y, inner, xp_h)            # Round PROG3: the XP bar and the level-up line(s)
        if self.xp:
            self.draw_xp(screen, gui, self.xp_rect, u, k, lh, now)
        y += xp_h + gap
        foot = rect.bottom - pad - btn_h
        if choosing:                                               # a stray tap never takes the cards: only Continue does
            self.buttons = [(screen.get_rect(), "results_skip" if not done else "results_wait")]
            if done:
                self.buttons += [(cr, f"rpick:{g}:{i}") for cr, g, i in self.pick_rects]
        else:
            self.buttons = [(screen.get_rect(), "results_skip" if not done else "ok")]
        if not done:
            rpg_ui.text(screen, SKIP_HINT, rect.right - pad, foot + btn_h // 2, gfx.DB_DIM, u, "midright")
        else:
            if choosing:
                rpg_ui.text(screen, PICK_HINT, x0, foot + btn_h // 2, gfx.DB_DIM, u, "midleft")
            bw = rpg_ui.font(u).size(CONTINUE)[0] + 8 * k
            br = pygame.Rect(rect.right - pad - bw, foot, bw, btn_h)
            rpg_ui.window(screen, br, k)
            rpg_ui.text(screen, CONTINUE, br.centerx, br.centery, gfx.DB_GOLD, u, "center")
            self.buttons.append((br, "ok"))
        return self.buttons

    def _card(self, screen, cr, c, turn, u, k, gap, lh, picked=False, choosing=False):
        if turn <= 0:
            rpg_ui.window(screen, cr, k)                 # the card still face down: an empty frame
            return
        colour = tier_colour(c.get("tier"))
        if picked and turn >= 1:
            pygame.draw.rect(screen, gfx.DB_GOLD, cr.inflate(8 * k, 8 * k), max(2, 2 * k))
        glow = cr.inflate(4 * k, 4 * k)
        pygame.draw.rect(screen, colour, glow, max(2, k))
        img = self._picture(c["name"], cr.w, cr.h)
        if img is not None:
            w = max(1, int(cr.w * turn))
            face = img if w == cr.w else pygame.transform.scale(img, (w, cr.h))
            screen.blit(face, (cr.centerx - w // 2, cr.y))
        if turn < 1:
            return
        if choosing and not c.get("castable", True):          # Round PROG2: a card the deck can't cast, dimmed
            veil = pygame.Surface(cr.size, pygame.SRCALPHA)
            veil.fill(gfx.DB_BG + (150,))
            screen.blit(veil, cr.topleft)
        tag = GUARDED_TAG if c.get("signature") else (NEW_TAG if c.get("new") else None)
        if tag and not choosing:                               # over the card's top edge, off its name line
            tw = rpg_ui.font(u).size(tag)[0] + 2 * k
            box = pygame.Rect(0, 0, tw + 2 * k, u + 2 * k)
            box.midbottom = (cr.centerx, cr.y + 2 * k)
            pygame.draw.rect(screen, gfx.DB_BG, box)
            pygame.draw.rect(screen, gfx.DB_GOLD, box, max(1, k))
            rpg_ui.text(screen, tag, box.centerx, box.centery, gfx.DB_GOLD, u, "center")
        elif tag:                                              # Round PROG2: on the card's top edge, as big as fits on it
            self._tag(screen, tag, cr, u, k, top=True)
        if picked:                                             # Round PROG2: the picked card of its group
            self._tag(screen, PICKED_TAG, cr, u, k, top=False)
        if choosing:                                           # no name line: the card's own picture names it
            return
        chars = rpg_ui.chars_in(cr.w + gap, u)
        name = c["name"] if len(c["name"]) <= chars else c["name"][:max(1, chars - 3)] + "..."
        rpg_ui.text(screen, name, cr.centerx, cr.bottom + gap // 2, gfx.DB_TEXT, u, "midtop")
        if not choosing:
            tname = TIER_NAMES.get(c.get("tier"), "") if c.get("tier") is not None else ""
            if tname:
                rpg_ui.text(screen, tname, cr.centerx, cr.bottom + gap // 2 + lh, colour, u, "midtop")

    @staticmethod
    def _tag(screen, tag, cr, u, k, top):
        """A tag on the card's top or bottom edge, in the biggest of the font's sizes (u, then smaller by 8) that fits on it."""
        px = u
        while px > 8 and rpg_ui.font(px).size(tag)[0] + 4 * k > cr.w:
            px -= 8
        tw = rpg_ui.font(px).size(tag)[0] + 2 * k
        box = pygame.Rect(0, 0, tw + 2 * k, px + 2 * k)
        if top:                                               # just under the card's name line, never over the name
            box.midtop = (cr.centerx, cr.y + max(k, int(cr.h * 0.2)))
        else:
            box.midbottom = (cr.centerx, cr.bottom - k)
        pygame.draw.rect(screen, gfx.DB_BG, box)
        pygame.draw.rect(screen, gfx.DB_GOLD, box, max(1, k))
        rpg_ui.text(screen, tag, box.centerx, box.centery, gfx.DB_GOLD, px, "center")

    def _picture(self, name, w, h):
        if self.picture is None or w <= 0 or h <= 0:
            return None
        try:
            card = self.pool.get(name) if self.pool is not None else None
            return self.picture(name, card, w, h)
        except Exception:                                         # a picture must never stop the Adventure
            return None
