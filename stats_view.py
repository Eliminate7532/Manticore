# SPDX-License-Identifier: GPL-3.0-or-later
"""
stats_view.py - patch 44: the deck screen's Stats page (Karl's decision 8).

"This deck" (the deck in focus): the record (only games played to the end; conceded and unfinished games are counted apart,
decision 7), by number of players, by seat in turn order, against each opponent's deck, on which of your turns you win and
lose, how you won and lost and what beat you - then the same deck's online games, a separate record (decision 9).
"All decks": one line per deck. Sections flow into as many columns as the window has room for, and scroll.
The numbers come from stats.py (stats/games.ndjson); nothing here writes anything.
"""
import pygame

import forge_dialogs as dlg
import gfx
import stats
from gfx import DIM, GOLD, WHITE, clip_text, draw_text, wrap_text

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
TOP_OPPONENTS = 12
TOP_CARDS = 5


def wld(row):
    """'3-1 (75%)' for [won, lost, drawn]."""
    w, l, d = row
    played = w + l + d
    text = f"{w}-{l}" + (f"-{d}" if d else "")
    return f"{text} ({stats.pct(w, played)})" if played else text


def sections_for(s, online=False):
    """[(title, rows)] for one summary. A row is ("text", left, right, colour) or ("bar", left, value, most, right)."""
    out = []
    where = ""
    played = s["won"] + s["lost"] + s["draw"]
    rec = [("big", stats.record_words(s) if played else "No games played to the end yet", "", WHITE)]
    apart = []
    if s["conceded"]:
        apart.append(f"{s['conceded']} conceded")
    if s["unfinished"]:
        apart.append(f"{s['unfinished']} unfinished")
    if apart:
        rec.append(("text", "Not counted: " + ", ".join(apart), "", DIM))
    out.append((where + "RECORD", rec))
    if not played:
        return out
    if s["pods"]:
        out.append((where + "BY NUMBER OF PLAYERS",
                    [("text", f"{n} players", wld(r), gfx.BODY_TEXT) for n, r in sorted(s["pods"].items()) if n]))
    if s["seats"]:
        out.append((where + "BY SEAT (turn order)",
                    [("text", f"{ORDINAL.get(k, str(k))} to play", wld(r), gfx.BODY_TEXT) for k, r in sorted(s["seats"].items())]))
    if s["opponents"]:
        rows = sorted(s["opponents"].items(), key=lambda kv: (-sum(kv[1]), kv[0]))
        lines = [("text", name, wld(r), gfx.BODY_TEXT) for name, r in rows[:TOP_OPPONENTS]]
        if len(rows) > TOP_OPPONENTS:
            lines.append(("text", f"... and {len(rows) - TOP_OPPONENTS} more", "", DIM))
        out.append(("AGAINST (their commanders)" if online else "AGAINST", lines))
    for title, turns, causes, cards, mine in (("WHEN YOU WIN", s["win_turns"], s["won_by"], s["win_cards"], True),
                                              ("WHEN YOU LOSE", s["loss_turns"], s["lost_to"], s["killers"], False)):
        if not turns:
            continue
        rows = [("text", "On your turn: " + stats.turn_words(turns), "", gfx.BODY_TEXT)]
        counts = {}
        for t in turns:
            counts[t] = counts.get(t, 0) + 1
        most = max(counts.values())
        for t in sorted(counts):
            rows.append(("bar", f"turn {t}", counts[t], most, str(counts[t])))
        out.append((where + title, rows))
        how = [("text", stats.CAUSE_WORDS.get(c, c).capitalize(), str(n), gfx.BODY_TEXT) for c, n in causes.most_common()]
        if cards:
            how.append(("text", "Cards that won it" if mine else "What beat you", "", GOLD))
            for key, n in cards.most_common(TOP_CARDS):
                label = key if mine else (f"{key[0]} ({key[1]})" if key[1] else key[0])
                how.append(("text", label, str(n), gfx.BODY_TEXT))
        out.append((where + ("HOW YOU WON" if mine else "HOW YOU LOST"), how))
    return out


def all_decks_sections(games, current_id=None):
    rows = []
    for _key, name, loc, onl in stats.decks(games, current_id):
        right = wld([loc["won"], loc["lost"], loc["draw"]]) if loc["won"] + loc["lost"] + loc["draw"] else "-"
        if onl["games"]:
            right += "   online " + (wld([onl["won"], onl["lost"], onl["draw"]]) if onl["won"] + onl["lost"] + onl["draw"] else "-")
        rows.append(("text", name, right, gfx.BODY_TEXT))
    if not rows:
        return [("YOUR DECKS", [("text", "No games recorded yet. Games you finish from now on are counted here.", "", DIM)])]
    return [("YOUR DECKS (won-lost, games played to the end)", rows)]


class StatsPage(dlg.Dialog):
    def __init__(self, entry=None, current_id=None, where=None):
        super().__init__()
        self.entry = entry
        self.current_id = current_id
        self.where = where
        self.mode = "deck" if entry is not None else "all"
        self.games = stats.load(where)
        self.scroll = 0
        self.content_h = 0
        self.area = pygame.Rect(0, 0, 0, 0)
        self.blocks = []                     # (rect, title) of each section drawn this frame (tests look at them)
        self.headings = []                   # (rect, text) of each full-width heading (ONLINE GAMES)

    def deck_groups(self):
        """[(heading or None, sections)]: this deck's games against the AI, then its online games (a separate record)."""
        e = self.entry
        key = getattr(e, "id", None)
        loc = stats.summarize(self.games, key, False, self.current_id)
        onl = stats.summarize(self.games, key, True, self.current_id)
        if not loc["games"] and not onl["games"]:
            return [(None, [("RECORD", [("text", "No games recorded with this deck yet. Games you finish with it from now on are "
                                                 "counted here.", "", DIM)])])]
        out = [(None, sections_for(loc))] if loc["games"] else []
        if onl["games"]:
            out.append(("ONLINE GAMES - a separate record", sections_for(onl, online=True)))
        return out

    def groups(self):
        return self.deck_groups() if self.mode == "deck" else [(None, all_decks_sections(self.games, self.current_id))]

    # ---- input
    def click(self, gui, pos, button):
        if button != 1:
            return
        name = self.button_at(pos)
        if name == "close":
            self.done = True
        elif name in ("tab_deck", "tab_all"):
            self.mode, self.scroll = name[4:], 0

    def key(self, gui, ev):
        if ev.key == pygame.K_ESCAPE:
            self.done = True
        elif ev.key in (pygame.K_DOWN, pygame.K_PAGEDOWN):
            self.wheel(gui, -3 if ev.key == pygame.K_PAGEDOWN else -1)
        elif ev.key in (pygame.K_UP, pygame.K_PAGEUP):
            self.wheel(gui, 3 if ev.key == pygame.K_PAGEUP else 1)

    def wheel(self, gui, dy):
        step = int(60 * max(1.0, gui.L.fs))
        self.scroll = max(0, min(self.scroll - dy * step, max(0, self.content_h - self.area.h)))

    # ---- drawing
    def draw(self, gui):
        L, scr = gui.L, gui.screen
        fs = L.fs
        rect = self.panel(gui, L.W - 40, L.H - 40, real=True)
        title, small = gui.font("title", True), gui.font("small")
        pad = int(max(14, 18 * fs))
        x, y, tw = rect.x + pad, rect.y + pad, rect.w - 2 * pad
        bh = int(max(34, 40 * fs))
        cw = max(gui.button_width("Close"), int(120 * fs))
        self.button(gui, pygame.Rect(rect.right - pad - cw, y, cw, bh), "Close", "close", True)
        right = rect.right - pad - cw - 10
        tabs = [("all", "All decks")]
        if self.entry is not None:
            tabs.insert(0, ("deck", "This deck"))
        for mode, label in reversed(tabs):
            w = max(gui.button_width(label), int(130 * fs))
            self.button(gui, pygame.Rect(right - w, y, w, bh), label, "tab_" + mode, True, mode == self.mode, False)
            right -= w + 8
        head = "Stats  -  " + (getattr(self.entry, "name", "") if self.mode == "deck" else "all decks")
        draw_text(scr, clip_text(head, title, right - x - 10), x, y + bh // 2, title, WHITE, "midleft")
        y += bh + 6
        hint = ("Only games played to the end count (conceded and unfinished ones are listed apart). Your seat is your place "
                "in turn order; your turn is how many turns you had taken.")
        for ln in wrap_text(hint, small, tw)[:2]:
            draw_text(scr, ln, x, y, small, gfx.BODY_TEXT)
            y += small.get_height()
        y += 8
        self.area = pygame.Rect(x, y, tw, rect.bottom - pad - y)
        self.draw_groups(gui, self.groups())

    def draw_groups(self, gui, groups):
        scr, fs = gui.screen, gui.L.fs
        area = self.area
        head, body, big, group_font = gui.font("small", True), gui.font("small"), gui.font("title"), gui.font("body", True)
        gap = int(24 * fs)
        bar = int(14 * max(1.0, fs))                   # room for the scroll bar, so no number runs under it
        width = area.w - bar
        col_min = int(340 * max(1.0, fs))
        ncols = max(1, min(3, (width + gap) // (col_min + gap)))
        col_w = (width - (ncols - 1) * gap) // ncols
        row_h = body.get_height() + int(4 * fs)

        def height(rows):
            h = head.get_height() + int(8 * fs)
            for row in rows:
                h += (big.get_height() + int(4 * fs)) if row[0] == "big" else row_h * self._row_lines(row, body, col_w)
            return h + int(18 * fs)

        top_of_group = 0
        place, heads = [], []
        for heading, sections in groups:
            if heading:
                heads.append((top_of_group, heading))
                top_of_group += group_font.get_height() + int(14 * fs)
            cols = [top_of_group] * ncols
            for title, rows in sections:
                c = cols.index(min(cols))
                place.append((c, cols[c], title, rows))
                cols[c] += height(rows)
            top_of_group = max(cols) + int(10 * fs)
        self.content_h = top_of_group
        self.scroll = max(0, min(self.scroll, max(0, self.content_h - area.h)))
        scr.set_clip(area)
        self.blocks, self.headings = [], []
        for top, heading in heads:
            hy = area.y + top - self.scroll
            r = pygame.Rect(area.x, hy, width, group_font.get_height() + int(8 * fs))
            self.headings.append((r, heading))
            draw_text(scr, clip_text(heading, group_font, width), area.x, hy, group_font, WHITE)
            pygame.draw.line(scr, GOLD, (area.x, r.bottom - 2), (area.x + width, r.bottom - 2), 2)
        for c, top, title, rows in place:
            bx = area.x + c * (col_w + gap)
            by = area.y + top - self.scroll
            self.blocks.append((pygame.Rect(bx, by, col_w, height(rows)), title))
            draw_text(scr, clip_text(title, head, col_w), bx, by, head, GOLD)
            yy = by + head.get_height() + 2
            pygame.draw.line(scr, gfx.GOLD_DARK, (bx, yy), (bx + col_w, yy), 1)
            yy += int(6 * fs)
            for row in rows:
                yy = self._draw_row(gui, row, bx, yy, col_w, body, big, row_h)
        scr.set_clip(None)
        if self.content_h > area.h:
            frac = area.h / self.content_h
            bar_h = max(30, int(area.h * frac))
            bar_y = area.y + int((area.h - bar_h) * (self.scroll / max(1, self.content_h - area.h)))
            pygame.draw.rect(scr, gfx.SCROLLBAR, pygame.Rect(area.right - 5, bar_y, 5, bar_h), border_radius=3)

    @staticmethod
    def _row_lines(row, font, w):
        if row[0] == "text" and not row[2]:
            return max(1, min(3, len(wrap_text(row[1], font, w))))
        return 1

    def _draw_row(self, gui, row, x, y, w, body, big, row_h):
        scr, fs = gui.screen, gui.L.fs
        if row[0] == "big":
            draw_text(scr, clip_text(row[1], big, w), x, y, big, row[3])
            return y + big.get_height() + int(4 * fs)
        if row[0] == "bar":
            _kind, label, value, most, right = row
            lw = int(90 * max(1.0, fs))
            draw_text(scr, clip_text(label, body, lw - 6), x, y, body, gfx.BODY_TEXT)
            rw = body.size("999")[0]
            room = max(10, w - lw - rw - 10)
            bar = pygame.Rect(x + lw, y + row_h // 4, max(3, int(room * value / max(1, most))), max(4, row_h // 2))
            pygame.draw.rect(scr, gfx.GOLD_DARK, bar, border_radius=2)
            draw_text(scr, right, x + w, y, body, gfx.BODY_TEXT, "topright")
            return y + row_h
        _kind, left, right, colour = row
        if not right:
            lines = wrap_text(left, body, w)[:3]
            for ln in lines:
                draw_text(scr, ln, x, y, body, colour)
                y += row_h
            return y
        rw = body.size(right)[0]
        draw_text(scr, clip_text(left, body, max(20, w - rw - 12)), x, y, body, colour)
        draw_text(scr, right, x + w, y, body, WHITE, "topright")
        return y + row_h
