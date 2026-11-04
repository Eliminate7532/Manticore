# SPDX-License-Identifier: GPL-3.0-or-later
"""rpg_encounter.py - Round RPG13: the encounter screen, and the way into it (claude/SPEC_RPG_SLICE_2026-10-23.md, section
RPG13; Karl's decisions of 23 Oct 2026: Final Fantasy Pixel Remaster's encounter presentation - "a transition, the foe's battle
sprite on a backdrop, then the duel on the Manticore table" - and, from the art survey, look A with B's "Higher resolution, A
bigger foe").

Every fight starts here: a foe you walked into on the map (rpg.explore.Trigger) or one that found you at random in a dungeon
(rpg.encounters.RandomFoe). Nothing is sent to the rules engine until Duel is chosen: the screen only reads the save.

The way in (EncounterScreen.draw, from the moment the fight starts):
  - the map as it was (a picture taken at that moment) is covered by dark squares in a spiral from the edges in, over
    SPIRAL_SECONDS, then the encounter screen fades in over FADE_SECONDS (an encounter sting plays as it starts: rpg_screens).
  - with Animations off (Cog > Animations) the map just fades into the screen over PLAIN_SECONDS.
  - keys and clicks do nothing until the screen is in (Final Fantasy's way: a key held from walking can't pick for you).
The screen: one 640 x 480 picture (CANVAS), scaled by a whole number to the window (canvas_scale), with dark bars round it.
  - left half: Simple Battles' backdrop (cave / dungeon / forest - the map's "backdrop" property) drawn 2x, and the foe's battle
    picture (enemy_kinds.json "battler"), cut to its outline and drawn battler_scale times (3 a regular foe, 4 the boss; one less
    at a time while it wouldn't fit), its feet where the pack's picture puts them on the backdrop's ground.
  - right half, DawnLike windows (rpg_ui) in the SDS pixel font: the foe's name and where it lives (BOSS / AMBUSH for a boss /
    a random foe); its colours, what its deck does and its line (rpg/data/kinds_text.json), what a win gives and a loss costs
    (rpg.adventure.fight_text); then Duel / See its deck / Turn back.
  - Up/Down (or W/S) move the cursor, Enter or Space picks, Esc is Turn back; the mouse picks too. A boss won't let you go: Turn
    back (or Esc) says REFUSED instead (the spec).
  - See its deck lists the deck it will play - the very one: rpg.duel.peek reads the save's rng without using it up, and Duel
    passes that deck's index on (rpg.duel.start(index=)).

My estimates, from the spec (to tune while Karl plays): SPIRAL_SECONDS, FADE_SECONDS, PLAIN_SECONDS, SPIRAL_ROWS. Every colour is
a gfx.py token.
"""
import os

import pygame

import gfx
import paths
import rpg_ui
from rpg import adventure as adv
from rpg import duel as rduel
from rpg import encounters

CANVAS = (640, 480)
LEFT_W = 320                              # the backdrop's half
SB_DIR = os.path.join(paths.assets_dir(), "rpg", "simple_battles")
BACKDROP_SCALE = 2
FOE_BOX = pygame.Rect(6, 8, 308, 464)     # the foe stays inside this part of the backdrop
DEFAULT_SCALE, BOSS_SCALE = 3, 4
SPIRAL_SECONDS = 0.7
FADE_SECONDS = 0.25
PLAIN_SECONDS = 0.15
SPIRAL_ROWS = 9
FOE_LIGHT, FOE_LIGHT_MIN = 0.8, 70        # Round RPG15: the light round the foe - its size x this, at least this many px (estimate)
LIGHT_BANDS = 4
STING = "rpg.encounter"                   # Round RPG15: the encounter sting (sounds/cues.json; vs.reveal until then)
OPTIONS = (("fight", "Duel"), ("see_deck", "See its deck"), ("no", "Turn back"))
REFUSED = "It won't let you leave."
NOTE_SECONDS = 2.5
BODY, BIG = 8, 16                         # the pixel font's two sizes (px)
PITCH = 10                                # px between lines of body text
GUARDS = "Guards: {card}. Beat him and it is yours."      # Round PROG2: a boss's signature card (rpg.rewards.signature_card)

W_NAME = pygame.Rect(328, 8, 304, 60)
W_ABOUT = pygame.Rect(328, 76, 304, 236)
W_MENU = pygame.Rect(328, 320, 304, 152)
W_DECK = pygame.Rect(328, 8, 304, 464)
MENU_TOP, MENU_STEP = 14, 30
INSET = 12


def canvas_scale(w, h):
    """The whole number the 640 x 480 picture is scaled by in a w x h window (1 at least)."""
    return max(1, min(w // CANVAS[0], h // CANVAS[1]))


def canvas_rect(w, h):
    s = canvas_scale(w, h)
    cw, ch = CANVAS[0] * s, CANVAS[1] * s
    return pygame.Rect((w - cw) // 2, (h - ch) // 2, cw, ch)


def spiral(cols, rows):
    """[(col, row)] of a cols x rows grid, from the top-left corner round the edges and inwards (clockwise)."""
    out = []
    top, left, bottom, right = 0, 0, rows - 1, cols - 1
    while top <= bottom and left <= right:
        out += [(c, top) for c in range(left, right + 1)]
        top += 1
        out += [(right, r) for r in range(top, bottom + 1)]
        right -= 1
        if top <= bottom:
            out += [(c, bottom) for c in range(right, left - 1, -1)]
            bottom -= 1
        if left <= right:
            out += [(left, r) for r in range(bottom, top - 1, -1)]
            left += 1
    return out


def spiral_cells(w, h, rows=SPIRAL_ROWS):
    """The squares that cover a w x h screen, in the spiral's order: [Rect]."""
    cell = max(1, -(-h // rows))
    cols, n_rows = -(-w // cell), -(-h // cell)
    return [pygame.Rect(c * cell, r * cell, cell, cell) for c, r in spiral(cols, n_rows)]


_PICTURES = {}


def picture(name):
    """A Simple Battles picture (Surface), or None when it can't be read."""
    if name not in _PICTURES:
        try:
            img = pygame.image.load(os.path.join(SB_DIR, name + ".png"))
            _PICTURES[name] = img.convert_alpha() if pygame.display.get_surface() is not None else img
        except (OSError, pygame.error, FileNotFoundError):
            rpg_ui._note_missing(name + ".png")
            _PICTURES[name] = None
    return _PICTURES[name]


def night_layer(size, darkness, centre, radius, bands=LIGHT_BANDS):
    """Round RPG15: a size picture as dark as `darkness` (0-1) but for a round light at `centre`, clear in the middle and darker in
    `bands` steps out to `radius` - the map's light, drawn the same way."""
    alpha = max(0, min(255, int(round(255 * darkness))))
    layer = pygame.Surface(size, pygame.SRCALPHA)
    layer.fill((*gfx.BACKDROP, alpha))
    for band in range(bands, 0, -1):
        pygame.draw.circle(layer, (*gfx.BACKDROP, int(alpha * (band - 1) / bands)), centre, max(1, int(round(radius * band / bands))))
    return layer


def foe_layout(kind_data, boss, bigger=0):
    """(cropped picture, scale, Rect on the canvas) of the kind's battle picture; (None, 0, None) without one. bigger: whole steps
    over the kind's own scale (Round RPG16: a rare foe is drawn one step bigger, as a boss is)."""
    img = picture(kind_data.get("battler") or "")
    if img is None:
        return None, 0, None
    box = img.get_bounding_rect()
    if box.w == 0 or box.h == 0:
        return None, 0, None
    want = kind_data.get("battler_scale")
    s = want if isinstance(want, int) and not isinstance(want, bool) and want >= 1 else (BOSS_SCALE if boss else DEFAULT_SCALE)
    s += max(0, int(bigger))
    while s > 1 and (box.w * s > FOE_BOX.w or box.h * s > FOE_BOX.h):
        s -= 1
    w, h = box.w * s, box.h * s
    feet = max(box.bottom * BACKDROP_SCALE, FOE_BOX.top + h)          # where the pack stands it, unless that cuts its head off
    feet = min(feet, FOE_BOX.bottom)
    cx = (box.x + box.w / 2) * BACKDROP_SCALE
    x = int(min(max(cx - w / 2, FOE_BOX.left), FOE_BOX.right - w))
    return img.subsurface(box).copy(), s, pygame.Rect(x, feet - h, w, h)


class EncounterScreen:
    """One fight's screen. trigger: an rpg.explore.Trigger or an rpg.encounters.RandomFoe; now: time.monotonic() when it
    started; snapshot: the map as it was (a Surface) for the way in; backdrop: cave / dungeon / forest."""

    def __init__(self, game, trigger, now, animations=True, snapshot=None, backdrop="dungeon", darkness=0.0):
        self.game, self.trigger = game, trigger
        self.darkness = max(0.0, min(1.0, float(darkness or 0.0)))   # Round RPG15: the place's darkness, the foe in the light
        self.kind = trigger.enemy
        self.boss = trigger.encounter == "boss"
        self.random = bool(getattr(trigger, "random", False))
        self.rare = adv.rare_entry(trigger)     # Round RPG16: a rare foe - its own name, home and taunt, "RARE", drawn big
        self.t0, self.animations, self.snapshot = now, bool(animations), snapshot
        self.backdrop = backdrop if backdrop in ("cave", "dungeon", "forest") else "dungeon"
        self.view = "main"
        self.cursor = 0
        self.note = None                        # (text, until)
        self.name = adv.foe_name(trigger) or self.kind          # Round RPG18b: a rare's name, the map's title, else the kind's
        self.lines = adv.fight_text(game, trigger)[1]
        self.text = dict(encounters.kind_text(self.kind))
        home = getattr(getattr(trigger, "t", trigger), "home", None)      # Round RPG16b (review R5): the map's home= line (a Foe's
        if isinstance(home, str) and home.strip():                          # own .home is its spot's coordinates, so its trigger's)
            self.text["home"] = home
        for k in ("home", "taunt"):
            if self.rare and self.rare.get(k):
                self.text[k] = self.rare[k]
        self.kd = encounters.kind_data(self.kind)
        try:
            self.deck_index, self.deck = rduel.peek(game.save, self.kind, trigger.encounter,
                                                    source=adv.deck_source(game, trigger))   # Round RPG17: a rare's own decks
        except (OSError, ValueError, KeyError, IndexError):
            self.deck_index, self.deck = None, None
        self.guards = None                      # Round PROG2: a boss's signature card - "Guards: <card>", his on top of the reward
        if self.boss and self.deck:
            try:
                self.guards = rduel.guarded_card(self.deck, "boss", game.pool, game.lists(), game.tiers())
            except Exception:                   # never stops the encounter screen
                self.guards = None
            if self.guards:
                self.lines = [self.lines[0], GUARDS.format(card=self.guards)] + list(self.lines[1:])
        self.foe, self.foe_scale, self.foe_rect = foe_layout(self.kd, self.boss, 1 if self.rare else 0)
        self.buttons = []                       # [(screen Rect, name)] drawn this frame
        self._rows = []                         # [(canvas Rect, name)]
        self._stage = None
        self._frames = {}

    # ---- time -----------------------------------------------------------------------------------------------------------------
    def length(self):
        return SPIRAL_SECONDS + FADE_SECONDS if self.animations else PLAIN_SECONDS

    def ready(self, now):
        return now - self.t0 >= self.length()

    def skip_transition(self):
        self.t0 -= self.length() + 1.0

    # ---- choosing -------------------------------------------------------------------------------------------------------------
    def options(self):
        if self.view == "deck":
            return [("deck_back", "Back", True)]
        return [(name, label, not (name == "no" and self.boss)) for name, label in OPTIONS]

    def choose(self, name, now):
        """What picking `name` does: "fight" (start the duel), "no" (back to the map), "refused" (a boss: the note is shown) or None
        (handled here: the deck view)."""
        if name == "see_deck":
            self.view, self.cursor = "deck", 0
            return None
        if name == "deck_back":
            self.view, self.cursor = "main", 1
            return None
        if name == "no" and self.boss:
            self.note = (REFUSED, now + NOTE_SECONDS)
            return "refused"
        if name in ("fight", "no"):
            return name
        return None

    def key(self, k, now):
        """A key while the screen is up: the action (as choose), or None. Nothing happens until the screen is in."""
        if not self.ready(now):
            return None
        opts = self.options()
        if k in (pygame.K_UP, pygame.K_w):
            self.cursor = (self.cursor - 1) % len(opts)
        elif k in (pygame.K_DOWN, pygame.K_s):
            self.cursor = (self.cursor + 1) % len(opts)
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self.cursor = min(self.cursor, len(opts) - 1)
            return self.choose(opts[self.cursor][0], now)
        elif k in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
            return self.choose("deck_back" if self.view == "deck" else "no", now)
        return None

    def to_canvas(self, pos, size):
        r = canvas_rect(*size)
        s = canvas_scale(*size)
        return ((pos[0] - r.x) // s, (pos[1] - r.y) // s)

    def hover(self, pos, size, now):
        """The mouse over an option moves the cursor there."""
        if not self.ready(now):
            return
        cx, cy = self.to_canvas(pos, size)
        for i, (rect, _name) in enumerate(self._rows):
            if rect.collidepoint(cx, cy):
                self.cursor = i
                return

    # ---- drawing --------------------------------------------------------------------------------------------------------------
    def stage(self):
        """The left half: the backdrop and the foe (drawn once)."""
        if self._stage is None:
            surf = pygame.Surface((LEFT_W, CANVAS[1]))
            surf.fill(gfx.DB_BG)
            back = picture("scene_" + self.backdrop)
            if back is not None:
                surf.blit(pygame.transform.scale(back, (back.get_width() * BACKDROP_SCALE, back.get_height() * BACKDROP_SCALE)),
                          (0, 0))
            if self.foe is not None:
                surf.blit(pygame.transform.scale(self.foe, self.foe_rect.size), self.foe_rect.topleft)
            if self.darkness > 0:                    # Round RPG15: as dark as the place, but for a light on the foe
                r = self.foe_rect or pygame.Rect(LEFT_W // 2 - 60, CANVAS[1] // 2, 120, 120)
                surf.blit(night_layer(surf.get_size(), self.darkness, r.center, max(FOE_LIGHT_MIN, int(max(r.w, r.h) * FOE_LIGHT))),
                          (0, 0))
            self._stage = surf
        return self._stage

    def canvas(self, now):
        c = pygame.Surface(CANVAS)
        c.fill(gfx.DB_BG)
        c.blit(self.stage(), (0, 0))
        self._rows = []
        if self.view == "deck":
            self._draw_deck(c)
        else:
            self._draw_main(c, now)
        return c

    def _draw_main(self, c, now):
        t = rpg_ui
        r = t.window(c, W_NAME)
        x, w = r.x + INSET, r.w - 2 * INSET
        name = self.name[:t.chars_in(w, BIG)]
        t.text(c, name, x, r.y + 10, gfx.DB_GOLD, BIG)
        tag = "BOSS" if self.boss else ("RARE" if self.rare else
                                         (getattr(self.trigger, "tag", None) or ("AMBUSH" if self.random else "")))
        colour = {"BOSS": gfx.DB_RED, "AMBUSH": gfx.DB_ORANGE, "REMATCH": gfx.DB_GREEN, "RARE": gfx.DB_GOLD}.get(tag, gfx.DB_DIM)
        y = r.y + 36
        if tag:
            tr = t.text(c, tag, x, y, colour)
            x2 = tr.right + 8
        else:
            x2 = x
        home = self.text.get("home") or ""
        t.text(c, home[:t.chars_in(r.right - INSET - x2)], x2, y, gfx.DB_DIM)

        r = t.window(c, W_ABOUT)
        x, w, y = r.x + INSET, r.w - 2 * INSET, r.y + 10
        bottom = r.bottom - 8
        cols = [ch for ch in str(self.kd.get("colour", "")) if ch in "WUBRG"]
        px = x
        for ch in cols:
            gfx.mana_symbol(c, ch, px + 5, y + 4, 5)
            px += 13
        t.text(c, encounters.colour_names("".join(cols)), px + (4 if cols else 0), y, gfx.DB_TEXT)
        y += 16
        blocks = [(self.text.get("plan") or "", gfx.DB_TEXT, 4)]
        if self.text.get("taunt"):
            blocks.append((f"\"{self.text['taunt']}\"", gfx.DB_GOLD, 8))
        held = adv.duel.held_by(self.game.save, getattr(self.trigger, "spot", None))
        held_line = adv.HOLDS_LINE.format(n=held) if held else None
        for i, line in enumerate(self.lines):
            colour = gfx.DB_ORANGE if line.startswith("Your deck can't") else (
                gfx.DB_GOLD if line == held_line else gfx.DB_DIM)               # Round GOLD1: the gold it holds, in gold
            blocks.append((line, colour, 2 if i < len(self.lines) - 1 else 0))
        for text, colour, gap in blocks:
            for ln in t.wrap(text, w):
                if y + BODY > bottom:
                    break
                t.text(c, ln, x, y, colour)
                y += PITCH
            y += gap

        r = t.window(c, W_MENU)
        self._menu(c, r, self.options())
        if self.note and now < self.note[1]:
            t.text(c, self.note[0], r.x + INSET, r.bottom - 18, gfx.DB_RED)

    def _menu(self, c, r, opts, top=None):
        t = rpg_ui
        y = r.y + MENU_TOP if top is None else top
        self.cursor = min(self.cursor, len(opts) - 1)
        for i, (name, label, on) in enumerate(opts):
            here = i == self.cursor
            colour = gfx.DB_DIM if not on else (gfx.DB_GOLD if here else gfx.DB_TEXT)
            if here:
                t.text(c, ">", r.x + 10, y, gfx.DB_GOLD, BIG)
            t.text(c, label, r.x + 32, y, colour, BIG)
            self._rows.append((pygame.Rect(r.x + 4, y - 6, r.w - 8, MENU_STEP - 2), name))
            y += MENU_STEP

    def deck_lines(self):
        """The deck as lines: its spells and other cards (most copies first), then its lands."""
        if not self.deck:
            return ["Its deck can't be read."]
        pool = getattr(self.game, "pool", None)
        spells, lands = [], []
        for n, k in self.deck.get("main", []):
            card = pool.get(n) if pool is not None else None
            (lands if card is not None and card.is_land else spells).append((int(k), n))
        spells.sort(key=lambda p: (-p[0], p[1]))
        lands.sort(key=lambda p: (-p[0], p[1]))
        out = [f"{k} {n}" for k, n in spells]
        if lands:
            out.append("")
            out.append(f"Lands ({sum(k for k, _n in lands)}):")
            out += [f"{k} {n}" for k, n in lands]
        return out

    def _draw_deck(self, c):
        t = rpg_ui
        r = t.window(c, W_DECK)
        x, w = r.x + INSET, r.w - 2 * INSET
        t.text(c, "Its deck", x, r.y + 10, gfx.DB_GOLD, BIG)
        y = r.y + 32
        sub = ""
        if self.deck:
            total = sum(int(k) for _n, k in self.deck.get("main", []))
            sub = f"{total} cards" + (f" - {self.deck.get('archetype')}" if self.deck.get("archetype") else "")
        for ln in t.wrap(sub, w)[:2]:
            t.text(c, ln, x, y, gfx.DB_DIM)
            y += PITCH
        y += 6
        menu_top = r.bottom - 12 - MENU_STEP
        lines = self.deck_lines()
        room = max(1, (menu_top - 6 - y) // PITCH)
        if len(lines) > room:
            lines = lines[:room - 1] + [f"... and {len(lines) - room + 1} more lines"]
        n = t.chars_in(w)
        for ln in lines:
            t.text(c, ln[:n], x, y, gfx.DB_TEXT if not ln.startswith("Lands") else gfx.DB_DIM)
            y += PITCH
        self._menu(c, r, self.options(), top=menu_top + 6)

    def frame(self, now, size):
        """The screen-sized picture of the canvas (cached while nothing on it changes)."""
        s = canvas_scale(*size)
        note = bool(self.note and now < self.note[1])
        key = (s, self.view, self.cursor, note)
        img = self._frames.get(key)
        if img is None:
            canvas = self.canvas(now)
            img = canvas if s == 1 else pygame.transform.scale(canvas, (CANVAS[0] * s, CANVAS[1] * s))
            self._frames = {key: img}            # one picture kept: the option rows (self._rows) are this picture's
        return img

    def draw(self, screen, now):
        """Draw the way in or the screen; returns [(screen Rect, option name)] (empty while it is coming in)."""
        size = screen.get_size()
        dest = canvas_rect(*size)
        img = self.frame(now, size)
        t = now - self.t0
        self.buttons = []
        if self.animations and t < SPIRAL_SECONDS:
            self._under(screen, size)
            cells = spiral_cells(*size)
            n = min(len(cells), int(len(cells) * max(0.0, t) / SPIRAL_SECONDS))
            for cell in cells[:n]:
                screen.fill(gfx.DB_BG, cell)
            return self.buttons
        if t < self.length():
            if self.animations:
                screen.fill(gfx.DB_BG)
                alpha = (t - SPIRAL_SECONDS) / FADE_SECONDS
            else:
                self._under(screen, size)
                alpha = max(0.0, t) / PLAIN_SECONDS
            img.set_alpha(max(0, min(255, int(255 * alpha))))
            screen.blit(img, dest)
            img.set_alpha(None)
            return self.buttons
        screen.fill(gfx.DB_BG)
        screen.blit(img, dest)
        s = canvas_scale(*size)
        self.buttons = [(pygame.Rect(dest.x + r.x * s, dest.y + r.y * s, r.w * s, r.h * s), name) for r, name in self._rows]
        return self.buttons

    def _under(self, screen, size):
        if self.snapshot is None:
            screen.fill(gfx.DB_BG)
        elif self.snapshot.get_size() == size:
            screen.blit(self.snapshot, (0, 0))
        else:
            screen.blit(pygame.transform.scale(self.snapshot, size), (0, 0))
