# SPDX-License-Identifier: GPL-3.0-or-later
"""
anim.py - Round AD2c: animations that make the table easier to read. Each one says what just happened, to what and by how much.

  * the AI-action spotlight  an AI's spell / ability / trigger shown large for a moment, one at a time, "AI 2 casts" above it
  * floating numbers         "-3" off a creature that took damage, "+1/+1" off a card that got counters
  * life that counts         a life total runs from old to new instead of jumping
  * the turn banner          "Your turn" / "AI 2's turn" sweeps across the middle
  * combat                   attackers step toward who they attack; attack and block lines grow out from the attacker
  * tap / untap              the card turns instead of snapping

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
from collections import deque

import pygame

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


def ease_out(t):
    t = 0.0 if t <= 0 else 1.0 if t >= 1 else t
    return 1 - (1 - t) ** 3


def frac(now, t0, seconds):
    return 0.0 if seconds <= 0 else max(0.0, min(1.0, (now - t0) / seconds))


class Spot:
    __slots__ = ("card", "name", "verb", "who", "born", "t0", "end", "more")

    def __init__(self, card, name, verb, who, born):
        self.card, self.name, self.verb, self.who, self.born = card, name, verb, who, born
        self.t0 = self.end = None
        self.more = 0


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
            elif k == "damage_card":
                amount = d.get("amount") or 0
                if amount:
                    self.floats.append((f"-{amount * b.count if b.count > 1 else amount}", gfx.LIFE_LOSS_COLOUR, b.card, None, now))
            elif k == "counters":
                new, old = d.get("new"), b.data.get("old")
                if new is not None and old is not None and new != old:
                    label = COUNTER_SHORT.get(str(d.get("counter")), "")
                    sign = "+" if new > old else "-"
                    text = f"{sign}{abs(new - old)} {label}".strip() if label != "+1/+1" else f"{sign}{abs(new - old)} +1/+1"
                    self.floats.append((text, gfx.GOLD if new > old else gfx.DIM, b.card, None, now))
            elif k == "tap" and b.card is not None:
                tapped = bool(d.get("tapped"))
                self.taps[b.card] = (0.0 if tapped else 90.0, 90.0 if tapped else 0.0, now)
        del self.floats[:-24]

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

    def active(self, now):
        """True while anything here still moves (the frame pacer's "moving")."""
        return bool(self.spot or self.spots
                    or any(now - f[4] < FLOAT_SECONDS for f in self.floats)
                    or any(now - t0 < LIFE_SECONDS for _a, _b, t0 in self.lives.values())
                    or (self.banner and now - self.banner[2] < BANNER_SECONDS)
                    or any(now - t0 < TAP_SECONDS for _a, _b, t0 in self.taps.values())
                    or any(now - t0 < STEP_SECONDS for _a, _b, t0 in self.steps.values())
                    or any(now - t0 < GROW_SECONDS for t0 in self.lines.values()))

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
        font = gui.font("big", True)                              # numbers: the body face, large (K3)
        keep = []
        for f in self.floats:
            text, colour, cid, pid, t0 = f
            t = frac(now, t0, FLOAT_SECONDS)
            if t >= 1:
                continue
            keep.append(f)
            r = gui.card_rects.get(cid) if cid is not None else gui.panel_rects.get(pid)
            if r is None:
                continue
            img = gfx.pick_font(font, text).render(text, True, colour)
            img.set_alpha(int(255 * (1 - t ** 2)))
            shadow = gfx.pick_font(font, text).render(text, True, gfx.SHADOW)
            shadow.set_alpha(int(200 * (1 - t ** 2)))
            rr = img.get_rect(center=(r.centerx, r.centery - int(FLOAT_RISE * max(1.0, L.fs) * ease_out(t))))
            for ox, oy in ((2, 2), (-1, 1), (1, -1), (-1, -1)):          # a dark edge all round: it reads on any card art
                scr.blit(shadow, rr.move(ox, oy))
            scr.blit(img, rr)
        self.floats = keep

    def draw_spot(self, gui, now):
        """The AI action, large, in the focus panel (top right: the place a big card is always shown), unless the player is using
        that panel right now (hovering or pinning a card). It never covers the board, the action bar or the hand."""
        sp = self.spot
        if sp is None or sp.t0 is None or gui.focus_busy():
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
        x = int(band.centerx + (1 - slide) * band.w * 0.25)
        img = gfx.pick_font(font, text).render(text, True, gfx.lerp(gfx.BACKDROP, gfx.GOLD, fade))    # "AI 1's turn": body face (K3)
        scr.blit(img, img.get_rect(midtop=(x, band.top + int(6 * L.fs))))
        draw_text(scr, sub, x, band.top + int(6 * L.fs) + font.get_height(), small, gfx.lerp(gfx.BACKDROP, gfx.DIM, fade), "midtop")


COUNTER_SHORT = {"P1P1": "+1/+1", "M1M1": "-1/-1", "LOYALTY": "loyalty", "CHARGE": "charge", "TIME": "time", "STUN": "stun",
                 "SHIELD": "shield", "OIL": "oil", "LORE": "lore", "QUEST": "quest", "VERSE": "verse"}
