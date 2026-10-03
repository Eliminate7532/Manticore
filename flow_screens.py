# SPDX-License-Identifier: GPL-3.0-or-later
"""
flow_screens.py - Round AD2b: the full-screen moments around a game.

  * VsShow / draw_vs           commander-versus-commander while the Forge engine starts (and while a game is resumed)
  * pregame_kind / draw_pregame the opening hand: keep or mulligan, then which cards go on the bottom
  * EndScreen / draw_end       VICTORY / DEFEAT (and "You are out" in a pod) before the end-of-game dialog

Nothing here talks to Forge. The buttons on these screens register the same hits the action bar and the hand register
("button" with name "ok" / "cancel", "card" with the card), so a click here goes through ForgeTable.on_click exactly as
a click on the bar or the hand does: the same command, with the same question number (Round 22). Journals, replays and
the soak cannot tell the difference.

Card pictures follow Scryfall's rules: the full card image (it carries the artist and copyright), never tinted, blurred
or drawn over. Labels and "VS" sit beside and under the cards, not on them.
"""
import re
import time

import pygame

import gfx
from gfx import draw_text, get_font, round_rect, wrap_text, clip_text

CARD_ASPECT = 488 / 680
VS_TAIL = 1.0                 # seconds the VS screen stays after the first snapshot arrives (a click, Space or Esc skips it)
END_ANIM = 0.4                # the Victory / Defeat word scales and fades in over this long (not at all when animations are off)
FADE_ANIM = 0.25


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def layer(gui, alpha=200):
    """Dim everything drawn so far, and swallow clicks that would land on the table underneath."""
    L = gui.L
    full = pygame.Rect(0, 0, L.W, L.H)
    round_rect(gui.screen, full, gfx.BACKDROP, 0, alpha=alpha)
    gui.add_hit(full, "layer")


def ornament(scr, cx, y, half, colour):
    """A thin rule with a diamond in the middle: the title art's divider, drawn instead of an emblem."""
    pygame.draw.line(scr, colour, (cx - half, y), (cx - 10, y), 1)
    pygame.draw.line(scr, colour, (cx + 10, y), (cx + half, y), 1)
    pygame.draw.polygon(scr, colour, [(cx, y - 6), (cx + 6, y), (cx, y + 6), (cx - 6, y)], 1)
    for sx in (-1, 1):
        pygame.draw.circle(scr, colour, (cx + sx * half, y), 2)


def seat_art(gui, seat, name):
    """Round ALT1: the picture key for seat `seat`'s commander `name` (its deck's printing, or the name itself)."""
    maps = getattr(gui, "seat_printings", None) or []
    pr = maps[seat].get(name.lower()) if seat < len(maps) else None
    if not pr:
        return name
    import card_data
    return card_data.art_key(name, *pr)


def card_picture(gui, name, w, h):
    """The full card image at w x h (never stretched: the caller keeps the card's aspect), or a drawn stand-in meanwhile."""
    surf = gui.art.preview(name, w, h) if getattr(gui, "art", None) else None
    if surf is None:
        import card_data
        surf = gfx.card_face({"name": card_data.key_name(name), "type": "Legendary Creature"}, w, h, max(4, int(w * 0.045)))
    return surf


# ---- VS ----------------------------------------------------------------------------------------------------------------
class VsShow:
    """Who plays whom, for the screen shown while the engine starts. seats: [(label, [commander names], deck name or "")]."""

    def __init__(self, seats):
        self.seats = [(lab, [c for c in (cmd or []) if c], deck or "") for lab, cmd, deck in seats]
        self.t0 = time.monotonic()
        self.state_at = None          # when the first snapshot arrived
        self.skipped = False
        self.card_rects = []          # Round UI4: where draw_vs put each commander card (last drawn frame)

    def holding(self, gui, now=None):
        """True while the VS screen should still be drawn instead of the table."""
        if self.skipped or not self.seats:
            return False
        if gui.modal is not None or gui.menu is not None:
            return False
        now = time.monotonic() if now is None else now
        if not gui.state:
            return True
        if self.state_at is None:
            self.state_at = now
        return now - self.state_at < VS_TAIL


def dck_commanders(text):
    """The [Commander] names in a .dck file's text (Resume rebuilds a game from the journal's deck files)."""
    names, inside = [], False
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("["):
            inside = line.lower() == "[commander]"
            continue
        if inside and line:
            m = re.match(r"^\d+\s+(.+?)(\|.*)?$", line)
            if m:
                names.append(m.group(1).strip())
    return names


PARTNER_FAN = 0.6          # Round UI4: the second commander sits this fraction of a card to the right of the first
PARTNER_DROP = 0.08        # ... and this fraction of a card lower
FRAME_PAD = 0.03           # Round 32: gfx.card_in_frame's frame reaches this fraction of the card's width past each edge ...
FRAME_SLACK = 10           # ... (at least 4 px); the layout keeps this many px more between a frame and its neighbours


def draw_vs(gui, vs, area):
    """The seats in a row inside `area`, a "VS" between each pair. Partners (two commanders) are fanned."""
    scr, L = gui.screen, gui.L
    seats = vs.seats
    vs.card_rects = []                                            # Round UI4: drawn card rects, in order (for tests)
    n = len(seats)
    if not n:
        return
    vs_font = get_font(int(max(48, 84 * L.fs) if n == 2 else max(28, 40 * L.fs)), True, "display")   # pods: room goes to the cards
    lab_font = gui.font("title", True)
    deck_font = gui.font("small")
    vs_w = vs_font.size("VS")[0] + int((36 if n == 2 else 16) * L.fs)
    seat_w = max(60, (area.w - (n - 1) * vs_w) // n)
    text_h = lab_font.get_height() + deck_font.get_height() + int(14 * L.fs)
    # Round UI4: partners spread wide enough that the first card's name and most of its art show (at 22% the second card hid
    # nearly all of the first - alpha screenshot 09), and only a seat with partners gets smaller cards; the others keep full size.
    fan, drop = PARTNER_FAN, PARTNER_DROP
    # Round 32 (Karl, 3 Oct, a 4-player loading screen at 4096x2160 / 175%: "cards out of frame"): gfx.card_in_frame draws its
    # stone frame OUTSIDE the card, max(4 px, FRAME_PAD of the card's width) on every side. The cards used to be sized to the
    # whole seat, so in a pod the frames ran past the window's left and right edges and over the "VS" between the seats. The
    # card now leaves room for its frame (and a few pixels more) inside its seat, across and down.
    room_w = max(40, int((seat_w - 2 * FRAME_SLACK) / (1 + 2 * FRAME_PAD)))
    room_h = max(40, int((area.h - text_h - 2 * FRAME_SLACK) / (1 + 2 * FRAME_PAD * CARD_ASPECT)))
    ch = max(60, int(min(room_h, room_w / CARD_ASPECT)))
    cw = int(ch * CARD_ASPECT)
    radius = max(4, int(cw * 0.045))
    top = area.y + max(0, (area.h - text_h - ch) // 2)
    x = area.x + (area.w - (n * seat_w + (n - 1) * vs_w)) // 2
    for i, (label, cmds, deck) in enumerate(seats):
        k = max(1, len(cmds))
        kcw = min(cw, int(room_w / (1 + fan * (k - 1))), int(ch / (1 + drop * (k - 1)) * CARD_ASPECT))
        kch = int(kcw / CARD_ASPECT)
        group_w = int(kcw * (1 + fan * (k - 1)))
        group_h = int(kch * (1 + drop * (k - 1)))
        gx = x + (seat_w - group_w) // 2
        gy = top + (ch - group_h) // 2
        for j, name in enumerate(cmds or ["?"]):
            r = pygame.Rect(gx + int(kcw * fan * j), gy + int(kch * drop * j), kcw, kch)
            if name != "?":
                name = seat_art(gui, i, name)                     # round ALT1: the printing that seat's deck names
                pic = card_picture(gui, name, kcw, kch)
                real = getattr(gui, "art", None) is not None and gui.art.preview(name, kcw, kch) is pic
                gfx.card_in_frame(scr, pic, r, key=("vs", name) if real else None,      # cache only the real picture
                                  edge=gfx.VS_TEXT, width=max(2, int(3 * L.fs)))        # the frame matches the "VS" (Karl)
            else:
                gfx.card_in_frame(scr, gfx.card_back(kcw, kch, radius), r, edge=gfx.VS_TEXT, width=max(2, int(3 * L.fs)))
            vs.card_rects.append(pygame.Rect(r))
        ly = top + ch + int(10 * L.fs)
        draw_text(scr, clip_text(label, lab_font, seat_w), x + seat_w // 2, ly, lab_font, gfx.GOLD if i == 0 else gfx.WHITE, "midtop")
        if deck:
            draw_text(scr, clip_text(deck, deck_font, seat_w), x + seat_w // 2, ly + lab_font.get_height() + 2, deck_font,
                      gfx.DIM, "midtop")
        x += seat_w
        if i < n - 1:
            cy = top + ch // 2
            draw_text(scr, "VS", x + vs_w // 2 + 3, cy + 3, vs_font, gfx.VS_SHADOW, "center")
            draw_text(scr, "VS", x + vs_w // 2, cy, vs_font, gfx.VS_TEXT, "center")
            x += vs_w


# ---- the opening hand --------------------------------------------------------------------------------------------------
_RETURN = re.compile(r"Return (\d+) card\(s\) to the bottom of your library", re.I)
_GOING = re.compile(r"you are going (\w+)", re.I)


def pregame_kind(gui):
    """"mulligan" while Forge asks keep-or-mulligan, "bottom" while it asks which cards go on the bottom, else None.
    The input kind (protocol 2) decides when there is one; Forge's wording otherwise (older bridges, recorded states)."""
    st = gui.state
    if not st or not st.get("prompt"):
        return None
    kind = gui.input_kind()
    if kind in ("mulligan", "bottom"):
        return kind
    if kind is not None:
        return None
    flat = " ".join(str(gui.prompt().get("message", "")).split())
    if "Do you want to keep your hand?" in flat:
        return "mulligan"
    if _RETURN.search(flat):
        return "bottom"
    return None


CANCEL_WORDS = {"Auto": "Choose for me"}       # Forge's word for "pick the bottom cards for me"


def bottom_count(gui):
    m = re.search(r"(\d+)", " ".join(str(gui.prompt().get("message", "")).split()))
    return int(m.group(1)) if m else 0


def who_goes_first(gui):
    """"You go first" / "AI 1 goes first" from the mulligan prompt's own words, or "" when Forge did not say."""
    flat = " ".join(str(gui.prompt().get("message", "")).split())
    m = _GOING.search(flat)
    if not m:
        return ""
    pos = m.group(1).lower()
    if pos == "first":
        return "You go first"
    order = gui.state.get("players", [])
    if pos == "second" and len(order) == 2:
        opp = gui.session.opponents()
        return f"{gui.short_player(opp[0]['name'])} goes first" if opp else "You go second"
    return f"You go {pos}"


def mulligan_status(gui):
    """One short line per AI from Forge's MULLIGAN log lines: kept N / mulliganed to N / deciding."""
    out = []
    log = [e.get("text", "") for e in getattr(gui.session, "log", []) if e.get("type") == "MULLIGAN"]
    for opp in gui.session.opponents():
        name = opp.get("name", "")
        short = gui.short_player(name)
        last = next((t for t in reversed(log) if t.startswith(name + " ")), "")
        m = re.search(r"kept a hand of (\d+)", last)
        if m:
            out.append(f"{short} kept {m.group(1)}")
            continue
        m = re.search(r"mulliganed down to (\d+)", last)
        out.append(f"{short} took a mulligan, deciding on {m.group(1)}" if m else f"{short} is deciding...")
    return out


def draw_pregame(gui, me):
    """The keep-or-mulligan screen, and the put-on-the-bottom screen that follows a mulligan."""
    scr, L = gui.screen, gui.L
    kind = pregame_kind(gui)
    layer(gui, 228)
    p = gui.prompt()
    ok, cancel = p.get("ok", {}), p.get("cancel", {})
    hand = (me or {}).get("zones", {}).get("hand", [])
    big = get_font(int(max(30, 48 * L.fs)), True, "display")
    body, small = gui.font("body"), gui.font("small")
    y = L.margin + int(24 * L.fs)
    if kind == "bottom":
        n = bottom_count(gui)
        head = f"Put {n} card{'s' if n != 1 else ''} on the bottom"
        sub = [f"Click {'a card' if n == 1 else f'{n} cards'} to choose {'it' if n == 1 else 'them'}, then press the button."]
    else:
        head = who_goes_first(gui) or "Your opening hand"
        sub = mulligan_status(gui)
    draw_text(scr, head, L.W // 2, y, big, gfx.WHITE, "midtop")
    y += big.get_height() + int(6 * L.fs)
    for ln in sub[:4]:
        draw_text(scr, ln, L.W // 2, y, body, gfx.TEXT, "midtop")
        y += body.get_height() + 2
    # buttons and the line under them, measured first so the cards get the rest of the height
    bh = int(max(40, 52 * L.fs))
    note_h = small.get_height() * 2 + 6
    bottom_y = L.H - L.margin - note_h - bh - int(10 * L.fs)
    top = y + int(18 * L.fs)
    k = max(1, len(hand))
    overlap = 0.86
    ch = int(min((bottom_y - top) - int(28 * L.fs), (L.W * 0.92) / (CARD_ASPECT * (1 + overlap * (k - 1)))))
    ch = max(80, ch)
    cw = int(ch * CARD_ASPECT)
    step = int(cw * overlap)
    total = cw + step * (k - 1)
    x0 = (L.W - total) // 2
    lift = int(ch * 0.10)
    cards_y = top + lift
    hovered = None
    for i in range(k - 1, -1, -1):
        r = pygame.Rect(x0 + i * step, cards_y - lift, cw, ch + lift)
        if r.collidepoint(gui.mouse):
            hovered = i
            break
    picked = 0
    for i, c in enumerate(hand):
        chosen = kind == "bottom" and bool(c.get("highlight") or c.get("selected"))    # Forge highlights the picked cards
        picked += chosen
        up = lift if (chosen or i == hovered) else 0
        r = gui.draw_card_at(c, x0 + i * step, cards_y - up, cw, ch, plate=False, flags=False)
        if chosen:
            pygame.draw.rect(scr, gfx.YELLOW, r.inflate(8, 8), 3, border_radius=max(4, int(cw * 0.05)))
        gui.card_rects[c["id"]] = r
        hit = pygame.Rect(r.x, r.y, step if i < k - 1 else r.w, r.h + up)
        gui.add_hit(hit, "card", card=c)
    # the two decisions, side by side: Mulligan (cancel) on the left, Keep (ok) on the right - Forge's own buttons
    specs = []
    # the labels stay free of digits, so both buttons are set in the display face (K3 would put a number in the body font);
    # the counts go in the line under each button instead
    notes = {}
    if cancel.get("label"):
        label = CANCEL_WORDS.get(cancel["label"], cancel["label"])
        if kind == "mulligan":
            notes["cancel"] = ("Free: you draw a new seven." if gui.mulligans == 0 else
                               f"You draw a new seven, then put {gui.mulligans} on the bottom.")
        elif kind == "bottom":
            notes["cancel"] = "Forge chooses them for you."
        specs.append(("cancel", label, bool(cancel.get("enabled")), False))
    if ok.get("label"):
        label = ok["label"]
        if kind == "mulligan":
            notes["ok"] = f"Play these {len(hand)} cards."
        elif kind == "bottom":
            label = "Put on bottom"
            notes["ok"] = f"{picked} of {bottom_count(gui)} chosen."
        specs.append(("ok", label, bool(ok.get("enabled")), True))
    bw = max([int(220 * L.fs)] + [gui.button_width(lab) + int(30 * L.fs) for _n, lab, _e, _p in specs])
    gap = int(28 * L.fs)
    bx = L.W // 2 - (len(specs) * bw + (len(specs) - 1) * gap) // 2
    by = bottom_y
    for name, label, enabled, primary in specs:
        r = pygame.Rect(bx, by, bw, bh)
        gui.draw_button(r, label, name, enabled, primary, primary and enabled)
        for j, ln in enumerate(wrap_text(notes.get(name, ""), small, bw + gap)[:2]):
            draw_text(scr, ln, r.centerx, r.bottom + 6 + j * small.get_height(), small, gfx.DIM, "midtop")
        bx += bw + gap
    view_table_button(gui)


def view_table_button(gui, label="View table"):
    L = gui.L
    w = gui.button_width(label, "small")
    h = int(max(30, 34 * L.fs))
    r = pygame.Rect(L.W - L.margin - w - (gui.cog_rect.w + 12 if getattr(gui, "cog_rect", None) else 0), L.margin, w, h)
    gui.draw_button(r, label, "view_table", True, fkey="small")
    gui.draw_cog_button()


def back_button(gui, label):
    """While a full-screen moment is hidden ("View table"): one button, top centre, brings it back."""
    L = gui.L
    w = gui.button_width(label)
    h = int(max(34, 40 * L.fs))
    r = pygame.Rect(L.W // 2 - w // 2, L.top_h + int(6 * L.fs), w, h)
    gui.draw_button(r, label, "show_flow", True, True, True)


# ---- Victory / Defeat --------------------------------------------------------------------------------------------------
class EndScreen:
    """kind: "won" | "lost" | "draw" | "out" (I lost in a pod, the others play on)."""

    def __init__(self, kind, line):
        self.kind, self.line = kind, line
        self.t0 = time.monotonic()


def end_screen_for(gui):
    """The EndScreen for the game that just ended (or for me being knocked out of a pod)."""
    st = gui.state or {}
    me = gui.session.me()
    my_name = me["name"] if me else ""
    if not (gui.session.game_over or st.get("gameOver")):
        return EndScreen("out", "You are out. The other players are still playing it out.")
    winner = st.get("winner")
    turn = st.get("turn")
    when = f" on turn {turn}" if turn else ""
    if not winner:
        return EndScreen("draw", "The game ended in a draw.")
    if getattr(gui.session, "spectator", False):            # Round MP2: someone watching neither wins nor loses
        return EndScreen("over", f"{gui.short_player(winner)} won{when}.")
    if winner == my_name:
        return EndScreen("won", f"You won{when}.")
    return EndScreen("lost", f"{gui.short_player(winner)} won{when}.")


WORDS = {"won": "VICTORY", "lost": "DEFEAT", "draw": "DRAW", "out": "DEFEAT", "over": "GAME OVER"}


def draw_end(gui, end):
    scr, L = gui.screen, gui.L
    layer(gui, 222)
    t = ease_out((time.monotonic() - end.t0) / END_ANIM) if gui.animations else 1.0
    colour = {"won": gfx.VICTORY, "draw": gfx.GOLD, "over": gfx.GOLD}.get(end.kind, gfx.DEFEAT)
    px = int(max(54, 120 * L.fs) * (0.82 + 0.18 * t))
    word = WORDS[end.kind]
    font = get_font(px, True, "display")
    while px > 30 and font.size(word)[0] > L.W - 2 * L.margin:
        px -= 6
        font = get_font(px, True, "display")
    cy = int(L.H * 0.42)
    half = min(int(L.W * 0.36), int(font.size(word)[0] * 0.95))
    rule_col = gfx.lerp(gfx.BACKDROP, gfx.GOLD, t)
    ornament(scr, L.W // 2, cy - font.get_height() // 2 - int(18 * L.fs), half, rule_col)
    ornament(scr, L.W // 2, cy + font.get_height() // 2 + int(14 * L.fs), half, rule_col)
    img = font.render(word, True, gfx.lerp(gfx.BACKDROP, colour, t))
    shadow = font.render(word, True, gfx.SHADOW)
    r = img.get_rect(center=(L.W // 2, cy))
    scr.blit(shadow, r.move(4, 4))
    scr.blit(img, r)
    body, small = gui.font("title"), gui.font("small")
    y = cy + font.get_height() // 2 + int(34 * L.fs)
    draw_text(scr, end.line, L.W // 2, y, body, gfx.WHITE, "midtop")
    y += body.get_height() + int(26 * L.fs)
    bh = int(max(40, 48 * L.fs))
    if end.kind == "out":
        labels = [("Keep watching", "end_watch", False), ("Leave the game", "end_leave", True)]
    else:
        labels = [("Continue", "end_continue", True)]
    bw = max([int(200 * L.fs)] + [gui.button_width(lab) + int(30 * L.fs) for lab, _n, _p in labels])
    gap = int(24 * L.fs)
    bx = L.W // 2 - (len(labels) * bw + (len(labels) - 1) * gap) // 2
    for label, name, primary in labels:
        gui.draw_button(pygame.Rect(bx, y, bw, bh), label, name, True, primary, primary)
        bx += bw + gap
    if end.kind != "out":
        draw_text(scr, "Click anywhere, Space or Enter to continue", L.W // 2, y + bh + int(12 * L.fs), small, gfx.DIM, "midtop")
        view_table_button(gui, "View battlefield")
