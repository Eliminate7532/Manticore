# SPDX-License-Identifier: GPL-3.0-or-later
"""
anim.py - Round AD2c: animations that make the table easier to read. Each one says what just happened, to what and by how much.

  * the AI-action spotlight  an AI's spell / ability / trigger shown large for a moment, one at a time, "AI 2 casts" above it
  * floating numbers         "-3" off a creature that took damage, "+1/+1" off a card that got counters
  * life that counts         a life total runs from old to new instead of jumping
  * the turn banner          "Your turn" / "AI 2's turn" sweeps across the middle
  * combat                   attackers step toward who they attack; attack and block lines grow out from the attacker
  * tap / untap              the card turns instead of snapping

Patch 48 (Scope B, Karl 7 Oct: "a real overhaul" - explain first, then flourish), all drawn AROUND the cards:
  * a trigger / ability      its source pulses in the stack's colour and a line runs from it to its entry on the stack
  * a spell                  a stream of motes flies from the caster to the stack panel
  * a resolved entry         a glowing dot carries it from its row to its first target (a burst there); countered / fizzled: a
                             red cross beside where its row was
  * arrivals                 a glow coloured by what arrived (creature, artifact, enchantment, planeswalker, land, token) and the
                             dust it throws off (bone, sparks, motes, a gold bloom, ash, a puff)
  * a hit                    an impact flash, embers, the creature is knocked sideways (nudge), the number is bigger for a bigger hit;
                             a player's panel flashes when hit (the panel shake, round 25, is scaled by the hit already)
  * counters                 the badge's corner rings
  * the turn banner          embers off its edges; VICTORY / DEFEAT: embers rising / ash falling, for a few seconds

Rules (claude/ART_DIRECTION_2026-09-27.md, AD2c):
  * nothing here ever delays an answer to Forge, and nothing here takes a click (these are drawn over the table, never hit-tested);
  * when Forge asks ME to decide something (a dialog, a target, attackers, blockers, a mulligan ... - anything but a plain "pass
    priority"), the AI-action queue is dropped and what is on screen ends at once, so a question is never sitting behind a show;
    the queue is also never more than MAX_LAG seconds behind the game. A plain priority pass does not cut it: the spotlight is
    usually the very spell I am being given priority over, and it is click-through, so Pass is never blocked by it;
  * with the Animations switch off nothing here runs: ForgeTable does not call it, and the table draws as before;
  * card pictures may move, turn and scale while they move, but are never tinted, blurred or stretched (Scryfall's rules).

Like motion.py and events.py, nothing here reads a clock: `now` (time.monotonic()) is passed in.
"""
import math
import random
from collections import deque

import pygame

import forge_fx as ffx
import gfx
from gfx import draw_text, get_font

SPOT_SECONDS = 0.9          # one AI action on screen
SPOT_FAST = 0.18            # what is left of it when Forge asks me something
SPOT_MAX_QUEUE = 4          # more than this waiting and the rest become "+N more"
MAX_LAG = 2.0               # an AI action waiting longer than this is dropped (the game has moved on)
FLOAT_SECONDS = 0.9
FLOAT_RISE = 34             # px at text scale 1.0
LIFE_SECONDS = 0.45
BANNER_SECONDS = 1.1
TAP_SECONDS = 0.16
GROW_SECONDS = 0.25         # attack / block lines
STEP = 0.10                 # an attacker steps this fraction of its height toward the defender
STEP_SECONDS = 0.18
CARD_ASPECT = 488 / 680

# ---- Patch 48 (Scope B, Karl 7 Oct: "a real overhaul" - explain first, then flourish). Every effect here is drawn AROUND a card
# (an outline, particles beside it), never on the picture; none takes a click; none waits on anything.
PULSE_SECONDS = 0.6         # a trigger's source card pulses gold when its trigger goes on the stack
LINK_SECONDS = 0.5          # ... and a line runs from it to the stack entry
AURA_SECONDS = {"creature": 0.55, "artifact": 0.45, "enchantment": 0.7, "planeswalker": 0.7, "land": 0.35, "token": 0.6, "other": 0.4}
HIT_SECONDS = 0.28          # the impact flash on a creature or a player panel that was hit
NUDGE_PX = 6                # how far a hit creature is knocked (text scale 1.0)
POP_SECONDS = 0.35          # the badge pop when counters / P/T change
RESOLVE_SECONDS = 0.45      # a resolved stack entry slides to its target
FIZZLE_SECONDS = 0.7        # the cross beside a countered / fizzled entry
STREAM_SPEED = 900.0        # px/s: a spell's energy on its way to the stack
STREAM_COUNT = 22           # motes in that stream
FLOAT_SIZES = ((10, 1.65), (5, 1.3), (0, 1.0))      # |amount| at least N -> the floating number is that much bigger
END_SPAWN = 2               # embers the first frame VICTORY / DEFEAT shows, then END_RATE a second (by time, whatever the frame rate) ...
END_RATE = 50
END_SECONDS = 6.0           # ... for this long; then they settle and the end screen idles as before (the frame pacer)
BANNER_SPAWN = 2            # embers a frame along the turn banner
ROW_MEMORY = 4.0            # a stack entry's last place is remembered this long after it left the stack
RESOLVE_COMET_R = 6         # px at text scale 1.0: the glowing dot that carries a resolved entry to its target
ARRIVAL_BURSTS = {          # what each kind of arrival throws off: (colour, count, speed, spread, angle (-pi/2 = up), life, gravity)
    "creature": ("bone", 10, 130.0, math.pi * 0.9, -math.pi / 2, 0.55, 260.0),       # bone dust kicked up, falling back
    "artifact": ("white", 8, 170.0, math.pi * 2, -math.pi / 2, 0.4, 0.0),            # sparks all round
    "enchantment": ("magic", 10, 60.0, math.pi * 0.8, -math.pi / 2, 0.9, -70.0),     # motes drifting up
    "planeswalker": ("gold", 12, 110.0, math.pi * 2, -math.pi / 2, 0.8, -40.0),      # a gold bloom
    "land": ("ash", 6, 70.0, math.pi * 0.7, math.pi / 2, 0.5, 120.0),                # dust settling
    "token": ("white", 6, 140.0, math.pi * 2, -math.pi / 2, 0.45, 0.0),              # a small puff (the round-25 one, kept)
    "other": ("white", 6, 120.0, math.pi * 2, -math.pi / 2, 0.4, 0.0),
}

AURA_COLOUR = {"creature": gfx.AURA_CREATURE, "artifact": gfx.AURA_ARTIFACT, "enchantment": gfx.AURA_ENCHANTMENT,
               "planeswalker": gfx.AURA_PLANESWALKER, "land": gfx.AURA_LAND, "token": gfx.AURA_TOKEN, "other": gfx.AURA_TOKEN}


def arrival_kind(card):
    """What kind of permanent just arrived, from the snapshot's type line ("Legendary Creature - Human Wizard", "Artifact - Equipment",
    "Basic Land - Island"...): decides the aura and the dust. A token is "token" whatever its type."""
    if not card:
        return "other"
    if card.get("token"):
        return "token"
    t = (card.get("type") or "").lower()
    if card.get("isCreature") or "creature" in t:
        return "creature"
    if card.get("isPlaneswalker") or "planeswalker" in t:
        return "planeswalker"
    if card.get("isLand") or "land" in t:
        return "land"
    if "artifact" in t:
        return "artifact"
    if "enchantment" in t or "battle" in t:
        return "enchantment"
    return "other"


def float_scale(amount):
    a = abs(amount or 0)
    for least, scale in FLOAT_SIZES:
        if a >= least:
            return scale
    return 1.0


def outline(screen, rect, colour, alpha, grow, width, radius):
    """A rounded outline `grow` px outside `rect`, like forge_fx._outline, but the picture is kept (gfx.remember, by size, colour, grow,
    width and radius - grow in steps of GROW_STEP px, so a swelling glow makes a handful of pictures, not one a frame) and only its
    alpha is set per blit. At 4K an outline round a card is a 300 x 400 px layer: making one per outline per frame cost most of
    a busy turn's extra time (measured, patch 48)."""
    alpha = int(max(0, min(255, alpha)))
    if alpha <= 0:
        return
    grow = int(grow) - int(grow) % GROW_STEP
    pad = grow + width + 2
    key = ("outline", rect.w, rect.h, colour, grow, width, radius)
    surf = gfx.recall(key)
    if surf is None:
        surf = pygame.Surface((rect.w + 2 * pad, rect.h + 2 * pad), pygame.SRCALPHA)
        r = pygame.Rect(pad - grow, pad - grow, rect.w + 2 * grow, rect.h + 2 * grow)
        pygame.draw.rect(surf, (*colour, 255), r, width, border_radius=radius + grow)
        gfx.remember(key, surf)
    surf.set_alpha(alpha)
    screen.blit(surf, (rect.x - pad, rect.y - pad))


GROW_STEP = 2


def ease_out(t):
    t = 0.0 if t <= 0 else 1.0 if t >= 1 else t
    return 1 - (1 - t) ** 3


def ease_in(t):
    t = 0.0 if t <= 0 else 1.0 if t >= 1 else t
    return t * t


def frac(now, t0, seconds):
    return 0.0 if seconds <= 0 else max(0.0, min(1.0, (now - t0) / seconds))


class Spot:
    __slots__ = ("card", "name", "verb", "who", "born", "t0", "end", "more")

    def __init__(self, card, name, verb, who, born):
        self.card, self.name, self.verb, self.who, self.born = card, name, verb, who, born
        self.t0 = self.end = None
        self.more = 0


class Resolve:
    """Patch 48: a stack entry that just resolved (a glowing dot carries it from where its row was to its first target, with a
    trail; nothing to go to -> it fades where it was) or fizzled (a cross beside where its row was)."""
    __slots__ = ("card", "start", "target", "kind", "fizzled", "t0", "arrived")

    def __init__(self, card, start, target, kind, fizzled, t0):
        self.card, self.start, self.target, self.kind, self.fizzled, self.t0 = card, pygame.Rect(start), target, kind, fizzled, t0
        self.arrived = False

    def seconds(self):
        return FIZZLE_SECONDS if self.fizzled else RESOLVE_SECONDS


class Animator:
    def __init__(self):
        self.spots = deque()              # waiting AI actions, oldest first
        self.spot = None                  # the one on screen
        self.floats = []                  # (text, colour, card id or None, player id or None, t0)
        self.lives = {}                   # player id -> [from, to, t0]
        self.taps = {}                    # card id -> (from_deg, to_deg, t0)
        self.steps = {}                   # card id -> (from, to, t0): attacker step, as a fraction of the card's height
        self.lines = {}                   # (attacker, target) -> first seen
        self.banner = None                # (text, sub, t0)
        self.last_turn = None             # (turn, active player id) last seen in a snapshot
        self.asked_seq = None             # the question number the flush last saw
        # ---- Patch 48 (Scope B) ----
        self.pulses = {}                  # card id -> (t0, kind): the source of a trigger / ability pulses when it goes on the stack
        self.links = []                   # (card id, t0, kind): a line from that source to its stack entry
        self.auras = {}                   # card id -> [kind, t0, burst done]: the glow around a permanent that just arrived
        self.hits = {}                    # card id -> [t0, amount, burst done]: the impact flash on a creature that took damage
        self.panel_hits = {}              # player id -> (t0, amount): the same on a player's panel
        self.pops = {}                    # card id -> (t0, up): the counters badge pops
        self.resolves = []                # Resolve: a stack entry on its way to its target, or crossed out
        self.rows = {}                    # card id -> (row rect, targets, kind, last seen): where each stack entry was drawn last
        self.banner_spawned = None        # the frame time the banner last spawned embers (one batch a frame)
        self.end_t0 = None                # when the VICTORY / DEFEAT embers started ...
        self.end_key = None               # ... for which end screen (its t0)
        self.end_spawned = 0              # ... and how many so far (END_RATE a second)

    # ---- input -------------------------------------------------------------------------------------------------------------
    def feed(self, gui, beats, now):
        """Turn this tick's beats into animations. Called by ForgeTable.apply_effects every tick (only with animations on)."""
        me = gui.my_id()
        for b in beats:
            if b.stale:
                continue
            d, k = b.last, b.kind
            if k == "cast" and not b.flurry:
                card = gui.session.card(b.card) if b.card is not None else None
                owner = (card or {}).get("controller")
                if card and owner is not None and owner != me:
                    verb = "trigger" if d.get("trigger") else ("activates" if d.get("ability") else "casts")
                    self.queue_spot(Spot(b.card, card.get("name", ""), verb, gui.short_player(gui.player_label(owner)), now))
                if b.card is not None and (d.get("trigger") or d.get("ability")):
                    kind = "trigger" if d.get("trigger") else "ability"
                    self.pulses[b.card] = (now, kind)                       # the source lights up ...
                    self.links.append((b.card, now, kind))                  # ... and a line runs from it to the stack
                elif card is not None:
                    self.cast_energy(gui, card, owner)                      # a spell: its energy flies from the caster to the stack
            elif k == "zone" and b.card is not None:
                if d.get("to") == "Battlefield" and d.get("from") != "Battlefield":
                    card = gui.session.card(b.card)
                    self.auras[b.card] = [arrival_kind(card), now, False]
            elif k == "token" and b.card is not None:
                self.auras[b.card] = ["token", now, False]
            elif k == "damage_card":
                amount = d.get("amount") or 0
                if amount:
                    total = amount * b.count if b.count > 1 else amount
                    self.floats.append((f"-{total}", gfx.LIFE_LOSS_COLOUR, b.card, None, now, float_scale(total)))
                    if b.card is not None:
                        self.hits[b.card] = [now, total, False]
            elif k == "damage_player":
                pid, amount = d.get("player"), d.get("amount") or 1
                if pid is not None:
                    self.panel_hits[pid] = (now, amount)
            elif k == "counters":
                new, old = d.get("new"), b.data.get("old")
                if new is not None and old is not None and new != old:
                    label = COUNTER_SHORT.get(str(d.get("counter")), "")
                    sign = "+" if new > old else "-"
                    text = f"{sign}{abs(new - old)} {label}".strip() if label != "+1/+1" else f"{sign}{abs(new - old)} +1/+1"
                    self.floats.append((text, gfx.GOLD if new > old else gfx.DIM, b.card, None, now, float_scale(new - old)))
                    if b.card is not None:
                        self.pops[b.card] = (now, new > old)
            elif k == "resolve" and b.card is not None:
                row = self.rows.get(b.card)
                if row is not None:
                    rect, targets, kind, _seen = row
                    self.resolves.append(Resolve(b.card, rect, targets[0] if targets else None, kind, bool(d.get("fizzled")), now))
            elif k == "tap" and b.card is not None:
                tapped = bool(d.get("tapped"))
                self.taps[b.card] = (0.0 if tapped else 90.0, 90.0 if tapped else 0.0, now)
        del self.floats[:-24]
        del self.links[:-12]
        del self.resolves[:-12]

    def cast_energy(self, gui, card, owner):
        """A spell's energy: a stream of motes from the caster (my hand, or that player's panel) to the top of the stack panel.
        Nothing when the table has no stack panel on screen (the stack rect is where the spell is about to appear)."""
        start = gui.L.hand if owner == gui.my_id() else gui.panel_rects.get(owner)
        top = gui.stack_row_rects.get(0)
        if top is None:                                       # the stack panel is not drawn yet (this is the first spell on it):
            r = gui.L.stack                                   # the layout knows where it will open (its height is 0 until then)
            if r is None or r.w < 20:
                return
            top = pygame.Rect(r.x, r.y, r.w, int(70 * max(1.0, gui.L.fs)))
        if start is None:
            return
        gui.particles.stream(STREAM_COUNT, start.centerx, start.centery, top.centerx, top.centery, "magic", speed=STREAM_SPEED * max(1.0, gui.L.fs))

    def queue_spot(self, spot):
        if len(self.spots) >= SPOT_MAX_QUEUE:
            self.spots[-1].more += 1
            return
        self.spots.append(spot)

    def watch(self, gui, now):
        """Every tick: the turn banner from the snapshot, the flush when Forge asks me something, the queue's lag, line births."""
        st = gui.state or {}
        turn, active = st.get("turn"), st.get("activePlayer")
        if turn is not None and (turn, active) != self.last_turn:
            if self.last_turn is not None and turn and not st.get("gameOver"):
                mine = active is not None and active == gui.my_id()
                who = "Your turn" if mine else (f"{gui.short_player(gui.player_label(active))}'s turn" if active is not None else "Next turn")
                self.banner = (who, f"Turn {gui.round_of(turn)}", now)
            self.last_turn = (turn, active)
        asking = gui.asks_me()
        if asking:
            self.spots.clear()                                   # never a show between me and a question
            if self.spot is not None and self.spot.end is not None:
                self.spot.end = min(self.spot.end, now + SPOT_FAST)
        while self.spots and now - self.spots[0].born > MAX_LAG:
            self.spots.popleft()
        if self.spot is not None and now >= self.spot.end:
            self.spot = None
        if self.spot is None and self.spots:
            self.spot = self.spots.popleft()
            self.spot.t0, self.spot.end = now, now + SPOT_SECONDS
        seen = set()
        for a in st.get("combat", []) or []:
            for tgt in [("b", x) for x in a.get("blockers", [])] + [("d", a.get("defender", ""))]:
                key = (a.get("card"), tgt)
                seen.add(key)
                self.lines.setdefault(key, now)
        for key in [k for k in self.lines if k not in seen]:
            del self.lines[key]
        self.note_rows(gui, st.get("stack") or [], now)

    def note_rows(self, gui, stack, now):
        """Patch 48: where each stack entry was drawn last (the last frame's stack_row_rects, by row), kept ROW_MEMORY seconds after
        it leaves the stack - a "resolve" beat arrives once the snapshot no longer shows the entry, so this is the only way to know
        where it was. The first target is kept with it (Forge's "c12" / "p3" names), and the entry's kind."""
        on_stack = set()
        for i, item in enumerate(stack):
            cid = (item.get("card") or {}).get("id")
            rect = gui.stack_row_rects.get(i)
            if cid is None:
                continue
            on_stack.add(cid)
            if rect is not None:
                targets = [t for t in (item.get("targets") or []) if isinstance(t, str) and t[:1] in "cp" and t[1:].isdigit()]
                self.rows[cid] = (pygame.Rect(rect), targets, ffx.kind_of(item), now)
        for cid in [c for c, (_r, _t, _k, seen) in self.rows.items() if c not in on_stack and now - seen > ROW_MEMORY]:
            del self.rows[cid]

    def active(self, now):
        """True while anything here still moves (the frame pacer's "moving")."""
        return bool(self.spot or self.spots
                    or any(now - f[4] < FLOAT_SECONDS for f in self.floats)
                    or any(now - t0 < LIFE_SECONDS for _a, _b, t0 in self.lives.values())
                    or (self.banner and now - self.banner[2] < BANNER_SECONDS)
                    or any(now - t0 < TAP_SECONDS for _a, _b, t0 in self.taps.values())
                    or any(now - t0 < STEP_SECONDS for _a, _b, t0 in self.steps.values())
                    or any(now - t0 < GROW_SECONDS for t0 in self.lines.values())
                    or any(now - t0 < PULSE_SECONDS for t0, _k in self.pulses.values())
                    or any(now - t0 < LINK_SECONDS for _c, t0, _k in self.links)
                    or any(now - t0 < AURA_SECONDS.get(kind, 0.5) for kind, t0, _b in self.auras.values())
                    or any(now - t0 < HIT_SECONDS for t0, _a, _b in self.hits.values())
                    or any(now - t0 < HIT_SECONDS for t0, _a in self.panel_hits.values())
                    or any(now - t0 < POP_SECONDS for t0, _u in self.pops.values())
                    or any(now - r.t0 < r.seconds() for r in self.resolves)
                    or (self.end_t0 is not None and now - self.end_t0 < END_SECONDS))

    def nudge(self, cid, now):
        """Patch 48: (dx, dy) in px (text scale 1.0) a creature that was just hit is knocked: a jolt sideways that settles."""
        hit = self.hits.get(cid)
        if hit is None:
            return (0, 0)
        t = frac(now, hit[0], HIT_SECONDS)
        if t >= 1:
            return (0, 0)
        amp = NUDGE_PX * min(2.0, 0.6 + 0.2 * hit[1]) * (1 - t)
        return (int(round(amp * math.sin(t * math.pi * 5))), int(round(-amp * 0.35 * math.sin(t * math.pi * 2.5))))

    # ---- values the table asks for while it draws ----------------------------------------------------------------------------
    def life(self, pid, life, now):
        """The life total to print: it runs from the old value to the new one over LIFE_SECONDS."""
        cur = self.lives.get(pid)
        if cur is None:
            self.lives[pid] = [life, life, now - LIFE_SECONDS]
            return life
        a, b, t0 = cur
        if life != b:
            shown = round(a + (b - a) * ease_out(frac(now, t0, LIFE_SECONDS)))
            self.lives[pid] = cur = [shown, life, now]
            a, b, t0 = cur
        return round(a + (b - a) * ease_out(frac(now, t0, LIFE_SECONDS)))

    def tap_angle(self, cid, tapped, now):
        """Degrees the card is turned right now (0 untapped, 90 tapped), or None when it is at rest."""
        t = self.taps.get(cid)
        if t is None:
            return None
        a, b, t0 = t
        if (b == 90.0) != bool(tapped) or now - t0 >= TAP_SECONDS:
            del self.taps[cid]
            return None
        return a + (b - a) * ease_out(frac(now, t0, TAP_SECONDS))

    def step(self, cid, attacking, now):
        """How far (fraction of the card's height) an attacker has stepped toward the defender."""
        target = STEP if attacking else 0.0
        cur = self.steps.get(cid)
        if cur is None:
            if not attacking:
                return 0.0
            cur = self.steps[cid] = (0.0, target, now)
        a, b, t0 = cur
        if b != target:
            shown = a + (b - a) * ease_out(frac(now, t0, STEP_SECONDS))
            cur = self.steps[cid] = (shown, target, now)
            a, b, t0 = cur
        value = a + (b - a) * ease_out(frac(now, t0, STEP_SECONDS))
        if not attacking and value <= 0.0005:
            del self.steps[cid]
        return value

    def grow(self, attacker, target, now):
        """0..1: how much of an attack / block line is drawn (it grows out from the attacker)."""
        t0 = self.lines.get((attacker, target))
        return 1.0 if t0 is None else ease_out(frac(now, t0, GROW_SECONDS))

    # ---- drawing (over the table; never hit-tested) ------------------------------------------------------------------------
    def draw_floats(self, gui, now):
        scr, L = gui.screen, gui.L
        base = gui.font("big", True)                              # numbers: the body face, large (K3)
        keep = []
        for f in self.floats:
            text, colour, cid, pid, t0 = f[:5]
            scale = f[5] if len(f) > 5 else 1.0                   # patch 48: a bigger hit is a bigger number
            t = frac(now, t0, FLOAT_SECONDS)
            if t >= 1:
                continue
            keep.append(f)
            r = gui.card_rects.get(cid) if cid is not None else gui.panel_rects.get(pid)
            if r is None:
                continue
            font = base if scale == 1.0 else get_font(int(L.px["big"] * scale), True)
            img = gfx.pick_font(font, text).render(text, True, colour)
            img.set_alpha(int(255 * (1 - t ** 2)))
            shadow = gfx.pick_font(font, text).render(text, True, gfx.SHADOW)
            shadow.set_alpha(int(200 * (1 - t ** 2)))
            rr = img.get_rect(center=(r.centerx, r.centery - int(FLOAT_RISE * max(1.0, L.fs) * ease_out(t))))
            for ox, oy in ((2, 2), (-1, 1), (1, -1), (-1, -1)):          # a dark edge all round: it reads on any card art
                scr.blit(shadow, rr.move(ox, oy))
            scr.blit(img, rr)
        self.floats = keep

    # ---- Patch 48: around the cards (drawn right after the particles, before the stack panel) ----------------------------
    def draw_auras(self, gui, now):
        """The glow around a permanent that just arrived, coloured by what it is, and the dust it throws off (once, the first frame
        the table knows where the card is - the beat comes before the frame that draws it). A card still gliding in (round 23's
        flight) keeps its aura for when it lands."""
        scr, L = gui.screen, gui.L
        for cid in list(self.auras):
            kind, t0, burst = self.auras[cid]
            secs = AURA_SECONDS.get(kind, 0.5)
            rect = gui.card_rects.get(cid)
            cm = gui.motion.get(cid) if hasattr(gui, "motion") else None
            if cm is not None and cm.flying():
                self.auras[cid][1] = now                          # wait for the landing
                continue
            if rect is None:
                if now - t0 > secs + 1.0:
                    del self.auras[cid]
                continue
            t = frac(now, t0, secs)
            if t >= 1:
                del self.auras[cid]
                continue
            if not burst:
                self.auras[cid][2] = True
                colour, n, speed, spread, angle, life, gravity = ARRIVAL_BURSTS.get(kind, ARRIVAL_BURSTS["other"])
                y = rect.bottom - 2 if kind in ("creature", "land") else rect.centery
                gui.particles.spawn(n, rect.centerx, y, colour, speed=speed * max(1.0, L.fs), spread=spread, angle=angle,
                                    life=life, gravity=gravity * max(1.0, L.fs))
            colour = AURA_COLOUR.get(kind, gfx.AURA_TOKEN)
            radius = max(3, int(min(rect.w, rect.h) * 0.07))
            fade = 1 - t * t
            outline(scr, rect, colour, 220 * fade, 2, 2, radius)
            outline(scr, rect, colour, 120 * fade, 3 + int(14 * ease_out(t) * max(1.0, L.fs)), 2, radius)

    def draw_hits(self, gui, now):
        """The impact flash around a creature that took damage (the number floats off it; the card itself is knocked by nudge())
        and around a player's panel that was hit."""
        scr, L = gui.screen, gui.L
        for cid in list(self.hits):
            t0, amount, burst = self.hits[cid]
            t = frac(now, t0, HIT_SECONDS)
            rect = gui.card_rects.get(cid)
            if t >= 1:
                del self.hits[cid]
                continue
            if rect is None:
                continue
            if not burst:
                self.hits[cid][2] = True
                gui.particles.spawn(min(14, 3 + amount), rect.centerx, rect.centery, "ember", speed=150 * max(1.0, L.fs),
                                    spread=math.pi * 2, life=0.4, gravity=200 * max(1.0, L.fs))
            radius = max(3, int(min(rect.w, rect.h) * 0.07))
            outline(scr, rect, gfx.HIT_FLASH, 235 * (1 - t), 2 + int(6 * t), 3, radius)
        for pid in list(self.panel_hits):
            t0, amount = self.panel_hits[pid]
            t = frac(now, t0, HIT_SECONDS)
            rect = gui.panel_rects.get(pid)
            if t >= 1:
                del self.panel_hits[pid]
                continue
            if rect is not None:
                outline(scr, rect, gfx.HIT_FLASH, 200 * (1 - t), 1 + int(5 * t), 3, 4)

    def draw_pops(self, gui, now):
        """A ring that swells out of the counters badge's corner (bottom left of the card) when counters change."""
        scr, L = gui.screen, gui.L
        for cid in list(self.pops):
            t0, up = self.pops[cid]
            t = frac(now, t0, POP_SECONDS)
            rect = gui.card_rects.get(cid)
            if t >= 1:
                del self.pops[cid]
                continue
            if rect is None:
                continue
            k = 1 + 0.25 * math.sin(math.pi * min(1.0, t * 1.4))                 # 1 -> 1.25 -> 1
            r = max(4, int(rect.w * 0.16 * k))
            cx, cy = rect.x + int(rect.w * 0.16), rect.bottom - int(rect.h * 0.1)
            surf = pygame.Surface((2 * r + 6, 2 * r + 6), pygame.SRCALPHA)
            pygame.draw.circle(surf, (*(gfx.GOLD if up else gfx.DIM), int(230 * (1 - t))), (r + 3, r + 3), r, 2)
            scr.blit(surf, (cx - r - 3, cy - r - 3))

    def draw_links(self, gui, now):
        """Drawn after the stack panel: the trigger / ability pulse on its source and the line from it to its entry on the stack,
        then each resolved entry on its way to its target (or crossed out where it was)."""
        scr, L = gui.screen, gui.L
        fs = max(1.0, L.fs)
        for cid in list(self.pulses):
            t0, kind = self.pulses[cid]
            t = frac(now, t0, PULSE_SECONDS)
            if t >= 1:
                del self.pulses[cid]
                continue
            rect = gui.card_rects.get(cid)
            if rect is not None:
                radius = max(3, int(min(rect.w, rect.h) * 0.07))
                pulse = 0.5 + 0.5 * math.cos(t * math.pi * 4)                   # two beats
                outline(scr, rect, ffx.KIND_COLOUR[kind], 230 * (1 - t) * pulse, 3 + int(8 * t), 3, radius)
        keep = []
        for link in self.links:
            cid, t0, kind = link
            t = frac(now, t0, LINK_SECONDS)
            if t >= 1:
                continue
            keep.append(link)
            start = gui.card_rects.get(cid)
            end = gui.stack_rects.get(cid) or gui.stack_row_rects.get(0)
            if start is None or end is None:
                continue
            a = ffx.edge_point(start, end.center)
            b = ffx.edge_point(end, start.center)
            grow = ease_out(min(1.0, t / 0.5))
            tip = (a[0] + (b[0] - a[0]) * grow, a[1] + (b[1] - a[1]) * grow)
            fade = 1.0 if t < 0.6 else (1 - t) / 0.4
            colour = ffx.KIND_COLOUR[kind]
            pygame.draw.line(scr, gfx.lerp(gfx.BACKDROP, colour, fade * 0.55), a, tip, max(3, int(5 * fs)))
            pygame.draw.line(scr, gfx.lerp(gfx.BACKDROP, colour, fade), a, tip, max(1, int(2 * fs)))
            if grow >= 1:
                pygame.draw.circle(scr, gfx.lerp(gfx.BACKDROP, colour, fade), (int(b[0]), int(b[1])), max(3, int(4 * fs)))
        self.links = keep
        keep = []
        for r in self.resolves:
            t = frac(now, r.t0, r.seconds())
            if t >= 1:
                continue
            keep.append(r)
            colour = ffx.KIND_COLOUR.get(r.kind, gfx.GOLD)
            if r.fizzled:
                self._draw_fizzle(gui, r, t)
                continue
            dest = None
            if r.target is not None:
                tid = int(r.target[1:])
                dest = gui.card_rects.get(tid) if r.target[0] == "c" else gui.panel_rects.get(tid)
            if dest is None:                                                    # nothing to go to: it fades where it was
                radius = max(3, int(r.start.h * 0.1))
                outline(scr, r.start, colour, 200 * (1 - t), 2 + int(10 * t), 2, radius)
                continue
            e = ease_in(t)
            x = r.start.centerx + (dest.centerx - r.start.centerx) * e
            y = r.start.centery + (dest.centery - r.start.centery) * e
            rad = max(3, int(RESOLVE_COMET_R * fs))
            trail = "magic" if r.kind == "spell" else "gold"
            gui.particles.spawn(2, x, y, trail, speed=40 * fs, spread=math.pi * 2, life=0.35, gravity=0)
            surf = pygame.Surface((rad * 4 + 2, rad * 4 + 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (*colour, 90), (rad * 2 + 1, rad * 2 + 1), rad * 2)
            pygame.draw.circle(surf, (*gfx.WHITE, 230), (rad * 2 + 1, rad * 2 + 1), rad)
            scr.blit(surf, (int(x) - rad * 2 - 1, int(y) - rad * 2 - 1))
            if t > 0.92 and not r.arrived:                                      # it lands: a burst at the target
                r.arrived = True
                gui.particles.spawn(10, dest.centerx, dest.centery, trail, speed=160 * fs, spread=math.pi * 2, life=0.45, gravity=0)
                radius = max(3, int(min(dest.w, dest.h) * 0.07))
                outline(scr, dest, colour, 220, 4, 3, radius)
        self.resolves = keep

    def _draw_fizzle(self, gui, r, t):
        """A red cross beside where the countered / fizzled entry was, with a puff of ash the first frame."""
        scr, L = gui.screen, gui.L
        fs = max(1.0, L.fs)
        size = max(6, int(10 * fs))
        cx, cy = r.start.x + size + 4, r.start.centery
        if not r.arrived:
            r.arrived = True
            gui.particles.spawn(8, cx, cy, "ash", speed=80 * fs, spread=math.pi * 2, life=0.5, gravity=60 * fs)
        grow = ease_out(min(1.0, t / 0.25))
        fade = 1.0 if t < 0.6 else (1 - t) / 0.4
        colour = gfx.lerp(gfx.BACKDROP, gfx.FIZZLE_MARK, fade)
        w = max(2, int(3 * fs))
        s = int(size * grow)
        pygame.draw.line(scr, colour, (cx - s, cy - s), (cx + s, cy + s), w)
        pygame.draw.line(scr, colour, (cx - s, cy + s), (cx + s, cy - s), w)

    def end_particles(self, gui, end, now):
        """Patch 48: called by flow_screens.draw_end every frame it draws. VICTORY: gold embers rising from the bottom of the window;
        DEFEAT: ash falling from the top; a draw: bone dust. Only for END_SECONDS after the screen opened, then they settle, so the
        end screen goes back to idling (the frame pacer) as before. Into gui.front_particles, drawn over the dark layer."""
        kind = end.kind
        if self.end_t0 is None or self.end_key != end.t0:
            self.end_t0, self.end_key, self.end_spawned = now, end.t0, 0
        if now - self.end_t0 >= END_SECONDS:
            return
        L = gui.L
        fs = max(1.0, L.fs)
        rnd = random
        due = int((now - self.end_t0) * END_RATE) + END_SPAWN - self.end_spawned      # by time, not by frame: the same at 30 or 60 fps
        due = max(0, min(due, END_SPAWN * 4))
        self.end_spawned += due
        for _ in range(due):
            x = rnd.uniform(0, L.W)
            if kind == "won":
                gui.front_particles.spawn(1, x, L.H + 4, rnd.choice(("gold", "ember")), speed=90 * fs, spread=math.pi / 3,
                                          angle=-math.pi / 2, life=2.6, gravity=-25 * fs)
            elif kind == "draw":
                gui.front_particles.spawn(1, x, rnd.uniform(0, L.H), "bone", speed=20 * fs, spread=math.pi * 2, life=2.0, gravity=0)
            else:
                gui.front_particles.spawn(1, x, -4, "ash", speed=40 * fs, spread=math.pi / 2, angle=math.pi / 2, life=3.0,
                                          gravity=18 * fs)

    def draw_spot(self, gui, now):
        """The AI action, large, in the focus panel (top right: the place a big card is always shown), unless the player is using
        that panel right now (hovering or pinning a card). It never covers the board, the action bar or the hand."""
        sp = self.spot
        if sp is None or sp.t0 is None or gui.focus_busy():
            return
        room = getattr(gui, "spot_room", None)
        if room is not None and not room():                      # patch UI6: no focus panel and no room under the stack for it
            return
        scr, L = gui.screen, gui.L
        area = L.preview
        t_in = frac(now, sp.t0, 0.15)
        t_out = frac(now, sp.end - 0.2, 0.2)
        alpha = ease_out(t_in) * (1 - t_out)
        if alpha <= 0.02 or area.w < 40 or area.h < 60:
            return
        cap_font = gui.font("title", True)
        caption = {"casts": f"{sp.who} casts", "activates": f"{sp.who} activates", "trigger": f"{sp.who}'s trigger"}[sp.verb]
        cap_font = gfx.pick_font(cap_font, caption)            # "AI 1": a digit, so the body face (K3)
        cap_h = cap_font.get_height() + 10
        more_h = gui.font("small").get_height() + 4 if sp.more else 0
        ch = int(min(area.h - cap_h - more_h - 8, (area.w - 8) / CARD_ASPECT) * 0.92)     # room for the card's frame
        cw = int(ch * CARD_ASPECT)
        if ch < 40:
            return
        layer = pygame.Surface(area.size, pygame.SRCALPHA)
        layer.fill(gfx.BACKDROP + (int(235 * alpha),))
        scr.blit(layer, area)
        drop = int((1 - ease_out(t_in)) * 24 * max(1.0, L.fs))
        rect = pygame.Rect(0, 0, cw, ch)
        rect.midtop = (area.centerx, area.y + cap_h - drop + 2)
        card = gui.session.card(sp.card) or {"name": sp.name}
        akey = (gui.art_key(card) if hasattr(gui, "art_key") else None) or card.get("name") or sp.name     # round ALT1
        real = gui.art.preview(akey, cw, ch) if gui.art else None
        surf = real if real is not None else gfx.card_face(card, cw, ch, max(4, int(cw * 0.045)))
        pad = max(4, int(cw * 0.03))
        rect.y += pad                                            # the frame sits under the caption, not on it
        gfx.card_in_frame(scr, surf, rect, key=("spot", akey) if real is not None else None,
                          alpha=int(255 * alpha))               # fading as a whole; the picture's colours are untouched
        cap = cap_font.render(caption, True, gfx.GOLD)
        cap.set_alpha(int(255 * alpha))
        scr.blit(cap, cap.get_rect(midtop=(area.centerx, area.y + 4)))
        if sp.more:
            more = gui.font("small").render(f"+{sp.more} more", True, gfx.DIM)
            more.set_alpha(int(255 * alpha))
            scr.blit(more, more.get_rect(midtop=(area.centerx, rect.bottom + 4)))

    def draw_banner(self, gui, now):
        if not self.banner:
            return
        text, sub, t0 = self.banner
        t = frac(now, t0, BANNER_SECONDS)
        if t >= 1:
            self.banner = None
            return
        scr, L = gui.screen, gui.L
        slide = ease_out(min(1.0, t / 0.18))
        fade = 1.0 if t < 0.7 else 1 - (t - 0.7) / 0.3
        font = get_font(int(max(30, 46 * L.fs)), True, "display")
        small = gui.font("small")
        bh = font.get_height() + small.get_height() + int(18 * L.fs)
        area = L.main                                            # over the board only: the focus panel and the log stay clear
        band = pygame.Rect(area.x, 0, area.w, bh)
        band.centery = area.centery
        layer = pygame.Surface(band.size, pygame.SRCALPHA)
        layer.fill(gfx.BACKDROP + (int(185 * fade),))
        scr.blit(layer, band)
        for y in (band.top, band.bottom - 1):
            pygame.draw.line(scr, gfx.lerp(gfx.BACKDROP, gfx.GOLD_DARK, fade), (band.left, y), (band.right, y), 1)
        if t < 0.7 and self.banner_spawned != now and hasattr(gui, "front_particles"):       # patch 48: embers off the band's edges
            self.banner_spawned = now
            fs = max(1.0, L.fs)
            for _ in range(BANNER_SPAWN):
                ex = random.uniform(band.left + band.w * 0.15, band.right - band.w * 0.15)
                gui.front_particles.spawn(1, ex, random.choice((band.top, band.bottom)), "ember", speed=30 * fs,
                                          spread=math.pi / 2, angle=-math.pi / 2, life=0.9, gravity=-40 * fs)
        x = int(band.centerx + (1 - slide) * band.w * 0.25)
        img = gfx.pick_font(font, text).render(text, True, gfx.lerp(gfx.BACKDROP, gfx.GOLD, fade))    # "AI 1's turn": body face (K3)
        scr.blit(img, img.get_rect(midtop=(x, band.top + int(6 * L.fs))))
        draw_text(scr, sub, x, band.top + int(6 * L.fs) + font.get_height(), small, gfx.lerp(gfx.BACKDROP, gfx.DIM, fade), "midtop")


COUNTER_SHORT = {"P1P1": "+1/+1", "M1M1": "-1/-1", "LOYALTY": "loyalty", "CHARGE": "charge", "TIME": "time", "STUN": "stun",
                 "SHIELD": "shield", "OIL": "oil", "LORE": "lore", "QUEST": "quest", "VERSE": "verse"}
