# SPDX-License-Identifier: GPL-3.0-or-later
"""
tour.py - the first-game tour (Round UX1): a one-minute show-and-tell of the table, not a lesson in how to play Magic.

Karl, 2 Oct: "a 'tour' of the UI elements and hotkeys and settings that starts when a player starts a game for the first time,
and can have the option to go through it again in settings."

The whole window is dimmed except one region of the table, which gets the AD2 stone frame; a caption beside it says what the
region is for, with Back / Next / Skip tour and a step counter. Nothing reaches Forge while the tour is open (ForgeTable gives
it every key and click first), and Forge simply waits: there is no time limit on the human player.

    STEPS          the wording, one table - Karl edits it here and nowhere else
    TOUR_VERSION   written to settings.json ("tour_done") when the tour is finished or skipped; raise it when the steps change
                   enough that players who saw the old tour should see it once more
    Tour           the state of one showing: which step, next / back / skip, and drawing it on a ForgeTable
    region_rect()  where a step's region is on this frame's layout (the layout moves with the window size and the text size)
"""
from collections import namedtuple
import time

import pygame

import gfx
from gfx import DIM, GOLD, WHITE, draw_text, round_rect, wrap_text

TOUR_VERSION = 1
MOVE_SECONDS = 0.2                  # the spotlight glides to the next region this fast (jumps with Animations off)
DIM_ALPHA = 178

Step = namedtuple("Step", "region title text keys")

# Each text: at most two sentences, plain words, every key named in it handled by ForgeTable.on_key and listed in HELP_LINES
# (tests/test_ux1.py checks both). Titles are drawn in the display face, so they carry no digits (K3).
STEPS = (
    Step(None, "Welcome to the table",
         "A one-minute look at what is where. Esc skips it, and Cog > Help > Tour of the table shows it again any time.",
         ()),
    Step("bar", "Turn and phases",
         "The lit pill is the phase the game is in. The dots under each pill choose where the game stops for you (blue on your "
         "turn, orange on the others'), and where you can't do anything the game passes for you.",
         ()),
    Step("me", "You",
         "Your life, and your library, graveyard and exile piles. Click the graveyard or exile pile to look inside.",
         ()),
    Step("opps", "Your opponents",
         "Their life, the cards in their hands and their piles. When the game asks you to choose a player - to target or to "
         "attack - click their panel.",
         ()),
    Step("hand", "Your hand",
         "Glowing cards can be played right now: click one to play it. Hover a card to lift it and read it.",
         ()),
    Step("cmd", "Your commander",
         "Cast your commander from here. The commander tax is added for you.",
         ()),
    Step("bf", "The battlefield",
         "Click a permanent to use its abilities. Rings mark what just arrived and what is waiting on the stack; a tapped card "
         "turns sideways.",
         ()),
    Step("actions", "What the game wants",
         "This bar always says what you are being asked, and its buttons answer it. The same answers are on the keyboard:",
         ("Space  OK / pass once", "Enter  pass until an opponent acts", "Esc  cancel", "A  attack with all", "S  skip ahead",
          "U  undo this turn")),
    Step("preview", "A closer look",
         "Hover any card to see it large here, and right-click to keep it. When an opponent casts something, it shows here too.",
         ()),
    Step("stacklog", "Stack and log",
         "The log is everything that has happened; the mouse wheel scrolls it. When spells and abilities are waiting to resolve, "
         "the stack opens above it - the top row resolves first.",
         ()),
    Step("cog", "Settings",
         "Text size, the table picture, full screen, animations, sound, a new game and conceding are all in here.",
         ("F11  full screen", "+ / -  text size", "M  sound on / off")),
    Step(None, "Help is one key away",
         "H lists every key and control. If anything looks wrong, F8 sends a bug report with a picture of the table.",
         ("H  controls", "F8  report a bug")),
)

REGIONS = ("bar", "me", "opps", "hand", "cmd", "bf", "actions", "preview", "stacklog", "cog")


def region_rect(gui, region):
    """This frame's rectangle for a step's region, or None (no such region on screen right now)."""
    L = getattr(gui, "L", None)
    if L is None or region is None:
        return None
    r = None
    if region == "bar":
        cog = getattr(gui, "cog_rect", None)
        right = cog.x - L.margin if cog else L.W
        r = pygame.Rect(0, 0, right, L.top_h)
    elif region == "me":
        r = L.my_info
    elif region == "opps":
        rects = [pygame.Rect(x) for x in (L.opp_rects or []) if x.w > 0 and x.h > 0]
        if rects:
            r = rects[0].unionall(rects[1:])
    elif region == "hand":
        r = L.hand
    elif region == "cmd":
        r = L.cmd_rect
    elif region == "bf":
        r = L.my_bf
    elif region == "actions":
        r = L.bar
    elif region == "preview":
        r = L.preview if getattr(L, "panel_on", True) else None            # patch UI6: Focus: Over card / Hidden has no panel to show
    elif region == "stacklog":
        r = L.log if L.log.h > 0 else None                                  # patch UI6: Log: Corner / Hidden has no log in the column
        if getattr(L, "stack", None) is not None and L.stack.h > 0:
            r = L.stack.union(L.log) if r is not None else pygame.Rect(L.stack)
    elif region == "cog":
        r = getattr(gui, "cog_rect", None)
    if r is None:
        return None
    r = pygame.Rect(r).clip(pygame.Rect(0, 0, L.W, L.H))
    return r if r.w >= 8 and r.h >= 8 else None


def available_steps(gui):
    """The steps whose region is on screen at the start (a centred step always is). Fixed for one showing, so the counter is
    honest: a 2-player game has opponents, a game whose layout has no command zone skips that step."""
    return [s for s in STEPS if s.region is None or region_rect(gui, s.region) is not None]


# Keys that keep working under the tour: they change only the table (help, sound, text size), never send anything to Forge.
# F3, F8, F11 and Shift+F1 are handled before the tour is asked at all.
PASS_KEYS = (pygame.K_h, pygame.K_m, pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_MINUS, pygame.K_KP_MINUS)


def passes_through(ev):
    return ev.key in PASS_KEYS and not (getattr(ev, "mod", 0) & (pygame.KMOD_CTRL | pygame.KMOD_ALT))


def _lerp_rect(a, b, t):
    return pygame.Rect(round(a.x + (b.x - a.x) * t), round(a.y + (b.y - a.y) * t),
                       round(a.w + (b.w - a.w) * t), round(a.h + (b.h - a.h) * t))


class Tour:
    """One showing of the tour on a ForgeTable. `manual`: started from the cog (Help > Tour of the table)."""

    def __init__(self, gui, manual=False, now=None):
        self.steps = available_steps(gui)
        self.index = 0
        self.manual = manual
        self.finished = None                 # None while open; "done" or "skipped" once closed
        self.buttons = []                    # (rect, name) from the last drawn frame
        self.panel = None                    # the caption panel's rect, last drawn frame
        self._from = None                    # the spotlight's rect when the step changed (for the glide)
        self._shown = None                   # the spotlight's rect as last drawn
        self._moved_at = now if now is not None else time.monotonic()

    # ---- state ----------------------------------------------------------------------------------------------------------
    @property
    def step(self):
        return self.steps[self.index]

    @property
    def total(self):
        return len(self.steps)

    def _go(self, index, now=None):
        self._from = self._shown
        self.index = index
        self._moved_at = now if now is not None else time.monotonic()

    def next(self, now=None):
        if self.index + 1 >= self.total:
            self.finished = "done"
        else:
            self._go(self.index + 1, now)

    def back(self, now=None):
        if self.index > 0:
            self._go(self.index - 1, now)

    def skip(self):
        self.finished = "skipped"

    def moving(self, now, animations=True):
        return animations and self._from is not None and now - self._moved_at < MOVE_SECONDS

    # ---- input: everything is the tour's while it is open ----------------------------------------------------------------
    def key(self, ev, now=None):
        k = ev.key
        if k in (pygame.K_RIGHT, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self.next(now)
        elif k in (pygame.K_LEFT, pygame.K_BACKSPACE):
            self.back(now)
        elif k == pygame.K_ESCAPE:
            self.skip()
        # anything else is swallowed: Space, E, A, S, U, M and the rest must not reach the game under the tour

    def click(self, pos, now=None):
        for rect, name in reversed(self.buttons):
            if rect.collidepoint(pos):
                {"next": self.next, "back": self.back}.get(name, lambda now=None: self.skip())(now)
                return name
        return None                          # a click anywhere else does nothing (and never reaches the table)

    # ---- drawing --------------------------------------------------------------------------------------------------------
    def spotlight(self, gui, now):
        """The spotlight's rect this frame (None for a centred step), gliding from the last one with Animations on."""
        target = region_rect(gui, self.step.region)
        if target is not None:
            target = target.inflate(8, 8).clip(gui.screen.get_rect())
        if target is None or self._from is None or not getattr(gui, "animations", True):
            return target
        t = min(1.0, (now - self._moved_at) / MOVE_SECONDS)
        if t >= 1.0:
            return target
        t = 1 - (1 - t) ** 3                                   # ease out
        return _lerp_rect(self._from, target, t)

    def draw(self, gui, now=None):
        now = now if now is not None else time.monotonic()
        scr, L = gui.screen, gui.L
        fs = max(1.0, L.fs)
        spot = self.spotlight(gui, now)
        self._shown = spot
        shade = pygame.Surface(scr.get_size(), pygame.SRCALPHA)
        shade.fill(gfx.BACKDROP + (DIM_ALPHA,))
        if spot is not None:
            shade.fill(gfx.CLEAR, spot)
        scr.blit(shade, (0, 0))
        if spot is not None:
            pygame.draw.rect(scr, GOLD, spot, max(2, int(3 * fs)))
            gfx.bevel(scr, spot, GOLD)
            gfx.studs(scr, spot)
        self.panel = self._draw_caption(gui, spot, fs)

    def _caption_layout(self, gui, fs):
        """(width, height, fonts, body lines) of the caption panel for this step."""
        L = gui.L
        pad, gap = int(14 * fs), int(8 * fs)
        title_f, body_f, key_f, foot_f = gui.font("title", True), gui.font("body"), gui.font("small", True), gui.font("small")
        width = min(int(400 * fs), L.W - 2 * L.margin - 8)
        inner = width - 2 * pad
        lines = wrap_text(self.step.text, body_f, inner)
        key_rows = list(self.step.keys)
        btn_h = int(max(30, 34 * fs))
        h = (pad + title_f.get_height() + gap + len(lines) * body_f.get_linesize()
             + (gap + len(key_rows) * (key_f.get_linesize() + 4) if key_rows else 0) + gap * 2 + btn_h + pad)
        return width, h, (title_f, body_f, key_f, foot_f), lines, key_rows, pad, gap, btn_h

    def place(self, gui, spot, width, height):
        """Where the caption panel goes: below, above, right of or left of the spotlight - the first side it fits on whole,
        never over the spotlight - or the middle of the window for a centred step (or when no side has room)."""
        L = gui.L
        win = pygame.Rect(0, 0, L.W, L.H).inflate(-2 * L.margin, -2 * L.margin)
        panel = pygame.Rect(0, 0, width, height)
        if spot is None:
            panel.center = win.center
            return panel
        gap = L.margin * 2
        tries = []
        for side in ("below", "above", "right", "left"):
            p = panel.copy()
            if side == "below":
                p.midtop = (spot.centerx, spot.bottom + gap)
            elif side == "above":
                p.midbottom = (spot.centerx, spot.top - gap)
            elif side == "right":
                p.midleft = (spot.right + gap, spot.centery)
            else:
                p.midright = (spot.left - gap, spot.centery)
            p.clamp_ip(win)
            tries.append(p)
            if not p.colliderect(spot) and win.contains(p):
                return p
        best = min(tries, key=lambda p: p.clip(spot).w * p.clip(spot).h)      # nowhere clean: the side that covers the least
        return best

    def _draw_caption(self, gui, spot, fs):
        scr = gui.screen
        width, height, fonts, lines, key_rows, pad, gap, btn_h = self._caption_layout(gui, fs)
        title_f, body_f, key_f, foot_f = fonts
        panel = self.place(gui, spot, width, height)
        gfx.shadowed(scr, panel, 14, 5, 130)
        round_rect(scr, panel, gfx.DIALOG_BG, 14, 2, gfx.DIALOG_EDGE)
        x, y = panel.x + pad, panel.y + pad
        draw_text(scr, self.step.title, x, y, title_f, GOLD)
        y += title_f.get_height() + gap
        for line in lines:
            draw_text(scr, line, x, y, body_f, WHITE)
            y += body_f.get_linesize()
        if key_rows:
            y += gap
            col = x + max(key_f.size(r.partition("  ")[0])[0] for r in key_rows) + int(20 * fs)    # the words line up in one column
            for row in key_rows:
                cap, _sp, what = row.partition("  ")
                cap_w = key_f.size(cap)[0] + int(12 * fs)
                chip = pygame.Rect(x, y, cap_w, key_f.get_linesize() + 2)
                round_rect(scr, chip, gfx.KEYCAP_BG, 4, 1, gfx.KEYCAP_EDGE)
                draw_text(scr, cap, chip.centerx, chip.centery, key_f, WHITE, "center")
                draw_text(scr, what, col, chip.centery, key_f, DIM, "midleft")
                y += key_f.get_linesize() + 4
        # footer: "3 / 12" on the left (body font: it has digits), Skip tour / Back / Next on the right
        by = panel.bottom - pad - btn_h
        draw_text(scr, "%d / %d" % (self.index + 1, self.total), x, by + btn_h // 2, foot_f, DIM, "midleft")
        self.buttons = []
        last = self.index + 1 >= self.total
        labels = [("skip", "Skip tour", False, not last), ("back", "Back", False, self.index > 0),
                  ("next", "Done" if last else "Next", True, True)]
        right = panel.right - pad
        for name, label, primary, shown in reversed(labels):
            if not shown:
                continue
            w = max(gui.button_width(label), int(84 * fs))
            r = pygame.Rect(right - w, by, w, btn_h)
            gui.draw_button(r, label, "tour_" + name, True, primary, primary, hit=False)
            self.buttons.append((r, name))
            right = r.x - int(8 * fs)
        return panel
