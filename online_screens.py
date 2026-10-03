# SPDX-License-Identifier: GPL-3.0-or-later
"""
online_screens.py - Round MP1: the screens for playing a friend online (1v1, the host's PC runs the game).

    the deck screen's "Host online" -> HostDialog (your name, the game password, the port, open the router port by UPnP)
        -> ForgeTable.host_game -> HostWait, drawn by the table instead of the loading screen until the friend arrives:
           the router's answer, your internet address, the invite code with Copy, Cancel
    the deck screen's "Join online" -> JoinDialog (paste the invite code, or type the address / port / password by hand)
        -> a JoinJob connects on a worker thread (up to ~25 s) -> ForgeTable.begin_online(session)
    either side, when the other table goes: ForgeTable shows "Your friend left" / "The host left" (connection_lost_dialog)

The network parts are in forge_net.py and forge_client.NetSession / HostSession; nothing here sends anything to Forge.
"""
import threading
import time

import pygame

import forge_dialogs as dlg
import forge_net
import gfx
import online_save
from gfx import DIM, GOLD, GREEN, ORANGE, RED, WHITE, clip_text, draw_text, round_rect, wrap_text

COLOURS = {"dim": DIM, "green": GREEN, "orange": ORANGE, "red": RED}


# ---- a one-line text box ------------------------------------------------------------------------------------------
class TextField:
    """One line of text the player types into. Ctrl+V pastes (the table's clipboard), Backspace / Ctrl+Backspace delete.
    A long value shows its end, so the caret is always visible."""

    def __init__(self, text="", max_len=60, placeholder="", allowed=None):
        self.text = text or ""
        self.max_len = max_len
        self.placeholder = placeholder
        self.allowed = allowed                    # None, or a string of the characters that may be typed
        self.rect = pygame.Rect(0, 0, 0, 0)

    def set(self, text):
        self.text = self._filter(text)[:self.max_len]

    def _filter(self, text):
        text = (text or "").replace("\r", " ").replace("\n", " ")
        if self.allowed is not None:
            text = "".join(ch for ch in text if ch in self.allowed)
        return "".join(ch for ch in text if ch.isprintable())

    def key(self, gui, ev):
        """True when the key was for this box."""
        k, mod = ev.key, getattr(ev, "mod", 0)
        ch = getattr(ev, "unicode", "") or ""
        if k == pygame.K_BACKSPACE:
            self.text = "" if mod & pygame.KMOD_CTRL else self.text[:-1]
            return True
        if k == pygame.K_v and mod & pygame.KMOD_CTRL:
            pasted = gui.read_clipboard() if hasattr(gui, "read_clipboard") else ""
            self.set(self.text + (pasted or "").strip())
            return True
        if ch and ch.isprintable() and not mod & (pygame.KMOD_CTRL | pygame.KMOD_ALT):
            if self.allowed is None or ch in self.allowed:
                if len(self.text) < self.max_len:
                    self.text += ch
            return True
        return False

    def draw(self, gui, rect, focused, font=None):
        scr = gui.screen
        font = font or gui.font("body")
        self.rect = pygame.Rect(rect)
        round_rect(scr, rect, gfx.FIELD_BG, 6, 2 if focused else 1, GOLD if focused else gfx.FIELD_EDGE)
        pad = max(6, rect.h // 5)
        room = rect.w - 2 * pad
        shown = self.text
        if shown and font.size(shown)[0] > room:              # show the end of a long value
            while shown and font.size("..." + shown)[0] > room:
                shown = shown[1:]
            shown = "..." + shown
        blink = "|" if focused and int(time.time() * 2) % 2 == 0 else ""
        if shown or focused:
            draw_text(scr, shown + blink, rect.x + pad, rect.centery, font, WHITE, "midleft")
        elif self.placeholder:
            draw_text(scr, clip_text(self.placeholder, font, room), rect.x + pad, rect.centery, font, DIM, "midleft")


def _row_h(gui):
    return int(max(34, 40 * gui.L.fs))


def _label_w(gui, labels, font):
    return max(font.size(t)[0] for t in labels) + int(14 * gui.L.fs)


def tick_box(gui, owner, rect, on, label, name):
    """A square tick box with its label; registers `name` as a button of `owner`."""
    scr, fs = gui.screen, gui.L.fs
    side = rect.h
    box = pygame.Rect(rect.x, rect.y, side, side)
    round_rect(scr, box, gfx.FIELD_BG, 4, 2, GOLD if on else gfx.FIELD_EDGE)
    if on:
        pygame.draw.lines(scr, GOLD, False, [(box.x + side * 0.22, box.y + side * 0.52), (box.x + side * 0.42, box.y + side * 0.74),
                                             (box.x + side * 0.80, box.y + side * 0.26)], max(2, int(3 * fs)))
    font = gui.font("small")
    draw_text(scr, clip_text(label, font, rect.w - side - int(10 * fs)), box.right + int(10 * fs), box.centery, font, WHITE, "midleft")
    owner.buttons.append((pygame.Rect(rect), name))


# ---- Host a game --------------------------------------------------------------------------------------------------
class HostDialog(dlg.Dialog):
    """Before hosting: your name, the password, the port, and whether to ask the router to open the port (UPnP)."""

    FIELDS = ("name", "password", "port")

    def __init__(self, gui, deck_entry, prefs=None, deck_problem=None):
        super().__init__()
        prefs = prefs or {}
        self.entry = deck_entry
        self.deck_problem = deck_problem            # Round MP2b: only a saved game can be continued with this deck
        self.fields = {
            "name": TextField(prefs.get("name") or "", forge_net.NAME_MAX, "How your friend sees you"),
            "password": TextField(forge_net.make_password(), 40),
            "port": TextField(str(prefs.get("port") or forge_net.DEFAULT_PORT), 5, allowed="0123456789"),
        }
        self.upnp = bool(prefs.get("upnp", True))
        self.focus = "name" if not self.fields["name"].text else "password"
        self.message = None                         # (text, colour)
        # Round MP2c: up to three friends, and AI players for the empty seats (four at most in all)
        self.guests = min(3, max(1, int(prefs.get("guests") or 1)))
        self.ai = min(3 - self.guests, max(0, int(prefs.get("ai") or 0)))
        # Round MP2d: through a relay (for internet providers that block incoming connections)
        self.use_relay = bool(prefs.get("use_relay")) and bool(prefs.get("relay"))
        # Round MP2b: an unfinished hosted game that can be continued
        self.saved = gui.online_saved() if hasattr(gui, "online_saved") else None
        self.resume = False
        self.fields["relay"] = TextField(prefs.get("relay") or "", 120, "relay address:port")

    def step(self, what, d):
        if what == "guests":
            self.guests = min(3, max(1, self.guests + d))
            self.ai = min(self.ai, 3 - self.guests)
        else:
            self.ai = min(3 - self.guests, max(0, self.ai + d))

    def relay_target(self):
        """(address, port) of the relay typed, or a str saying what's wrong."""
        text = self.fields["relay"].text.strip()
        if not text:
            return "Type the relay's address (address:port)."
        addr, _sep, port_text = text.rpartition(":")
        if not addr:
            addr, port_text = text, str(forge_net.DEFAULT_RELAY_PORT)
        why = forge_net.validate_address(addr)
        if why:
            return why
        port, why = forge_net.validate_port(port_text)
        return why if why else (addr, port)

    def problem(self):
        if not forge_net.clean_name(self.fields["name"].text):
            return "Type your name."
        if self.use_relay:
            target = self.relay_target()
            if isinstance(target, str):
                return target
        if len(self.fields["password"].text.strip()) < 4:
            return "The password needs at least 4 characters."
        _port, why = forge_net.validate_port(self.fields["port"].text)
        return why

    def host(self, gui):
        why = self.problem() or (None if self.resume else self.deck_problem)
        if why:
            self.message = (why, RED)
            return
        port, _ = forge_net.validate_port(self.fields["port"].text)
        extra = {"guests": self.guests, "ai": self.ai} if (self.guests, self.ai) != (1, 0) and not self.resume else {}
        if self.resume and self.saved:
            extra["resume"] = (self.saved[0], self.saved[1])
        if self.use_relay:
            extra["relay"] = self.relay_target()
        error = gui.host_game(self.entry, forge_net.clean_name(self.fields["name"].text), port,
                              self.fields["password"].text.strip(), self.upnp, **extra)
        if error:
            self.message = (error, RED)
        else:
            self.done = True

    def draw(self, gui):
        L, scr = gui.L, gui.screen
        k = max(1.0, L.fs)
        title, body, small = gui.font("title", True), gui.font("body"), gui.font("small")
        rh = _row_h(gui)
        gap = int(10 * L.fs)
        pw = min(int(760 * k), L.W - 40)
        tw = pw - 52
        if self.resume:
            intro = wrap_text("You continue the saved game. Your friends join with the new invite code; each gets their own "
                              "seat back by typing the same name as before.", small, tw)
        else:
            intro = wrap_text("Your friends join your game over the internet, each with their own deck. You play: %s."
                              % (getattr(self.entry, "name", "") or "your chosen deck"), small, tw)
        saved_line = []
        if self.saved:
            folder, meta, playable = self.saved
            saved_line = wrap_text(("Saved game: " if playable else "Saved game (can't be continued with this version): ")
                                   + online_save.describe(meta), small, tw - 2 * max(gui.button_width("Continue it", "small"),
                                                                                     gui.button_width("Discard", "small")) - 30)
        ai_note = wrap_text("The AI players use the decks chosen for AI 1%s on the deck screen." % (
            "" if self.ai == 1 else " and AI 2"), small, tw) if self.ai and not self.resume else []
        note = wrap_text("You'll get an invite code to send your friend. The first time, Windows may ask whether Java may "
                         "use the network: allow it, or your friend can't connect.", small, tw)
        msg = wrap_text(self.message[0], small, tw)[:3] if self.message else []
        bh = int(max(42, 46 * L.fs))
        need = (20 + title.get_height() + 8 + len(intro) * small.get_height() + gap + 4 * (rh + gap) + 2 * (rh + gap)
                + ((max(rh, len(saved_line) * small.get_height()) + gap) if saved_line else 0)
                + len(ai_note) * small.get_height() + len(note) * small.get_height() + gap + len(msg) * small.get_height()
                + gap + bh + 20)
        rect = self.panel(gui, pw, min(need, L.H - 40), real=True)
        x, y = rect.x + 26, rect.y + 20
        draw_text(scr, "Continue a saved game online" if self.resume else "Host a game online", x, y, title, WHITE)
        y += title.get_height() + 8
        for ln in intro:
            draw_text(scr, ln, x, y, small, gfx.BODY_TEXT)
            y += small.get_height()
        y += gap
        if saved_line:                                 # Round MP2b: the saved game, with Continue it / Discard
            row_h = max(rh, len(saved_line) * small.get_height())
            yy = y + (row_h - len(saved_line) * small.get_height()) // 2
            for ln in saved_line:
                draw_text(scr, ln, x, yy, small, GOLD if self.saved[2] else DIM)
                yy += small.get_height()
            dw = max(gui.button_width("Discard", "small"), int(90 * L.fs))
            cw_ = max(gui.button_width("Continue it", "small"), gui.button_width("New game", "small"), int(110 * L.fs))
            bx = rect.right - 26 - dw
            self.button(gui, pygame.Rect(bx, y, dw, rh), "Discard", "discard_saved", not self.resume, fkey="small")
            if self.saved[2]:
                self.button(gui, pygame.Rect(bx - 8 - cw_, y, cw_, rh), "New game" if self.resume else "Continue it",
                            "toggle_resume", fkey="small")
            y += row_h + gap
        labels = {"name": "Your name", "password": "Password", "port": "Port"}
        lw = _label_w(gui, list(labels.values()) + ["Friends", "Relay"], body)
        for name in self.FIELDS:
            draw_text(scr, labels[name], x, y + rh // 2, body, WHITE, "midleft")
            fx = x + lw
            fw = rect.right - 26 - fx
            if name == "password":
                nw = max(gui.button_width("New"), int(90 * L.fs))
                self.button(gui, pygame.Rect(rect.right - 26 - nw, y, nw, rh), "New", "new_password", fkey="small")
                fw -= nw + 8
            elif name == "port":
                fw = min(fw, int(140 * L.fs))
            self.fields[name].draw(gui, pygame.Rect(fx, y, fw, rh), self.focus == name)
            self.buttons.append((pygame.Rect(fx, y, fw, rh), "field_" + name))
            y += rh + gap
        # Round MP2c: how many friends, how many AI players (a saved game has its own)
        if self.resume:
            draw_text(scr, clip_text("Playing with: " + online_save.who(self.saved[1]), body, tw), x, y + rh // 2, body,
                      WHITE, "midleft")
        else:
            draw_text(scr, "Friends", x, y + rh // 2, body, WHITE, "midleft")
            sw = max(rh, gui.button_width("+", "small"))
            bx = x + lw
            for what, label, value in (("guests", None, self.guests), ("ai", "AI players", self.ai)):
                if label:
                    bx += gap * 2
                    draw_text(scr, label, bx, y + rh // 2, body, WHITE, "midleft")
                    bx += body.size(label)[0] + gap
                lo, hi = (1, 3) if what == "guests" else (0, 3 - self.guests)
                self.button(gui, pygame.Rect(bx, y, sw, rh), "-", what + "_minus", value > lo, fkey="small")
                bx += sw + 6
                vw = body.size("8")[0] + 2 * gap
                draw_text(scr, str(value), bx + vw // 2, y + rh // 2, body, GOLD, "center")
                bx += vw + 6
                self.button(gui, pygame.Rect(bx, y, sw, rh), "+", what + "_plus", value < hi, fkey="small")
                bx += sw
        y += rh + gap
        for ln in ai_note:
            draw_text(scr, ln, x, y, small, DIM)
            y += small.get_height()
        if not self.use_relay:
            tick_box(gui, self, pygame.Rect(x, y + (rh - int(26 * L.fs)) // 2, rect.w - 52, int(26 * L.fs)), self.upnp,
                     "Try to open my router port automatically (UPnP)", "upnp")
            y += rh + gap
        # Round MP2d: through a relay
        tick_box(gui, self, pygame.Rect(x, y + (rh - int(26 * L.fs)) // 2, rect.w - 52, int(26 * L.fs)), self.use_relay,
                 "Connect through a relay (when your internet provider blocks incoming connections)", "use_relay")
        y += rh + gap
        if self.use_relay:
            draw_text(scr, "Relay", x, y + rh // 2, body, WHITE, "midleft")
            fx = x + lw
            self.fields["relay"].draw(gui, pygame.Rect(fx, y, rect.right - 26 - fx, rh), self.focus == "relay")
            self.buttons.append((pygame.Rect(fx, y, rect.right - 26 - fx, rh), "field_relay"))
            y += rh + gap
        for ln in note:
            draw_text(scr, ln, x, y, small, DIM)
            y += small.get_height()
        y += gap
        if msg:
            for ln in msg:
                draw_text(scr, ln, x, y, small, self.message[1])
                y += small.get_height()
        by = rect.bottom - 18 - bh
        cw = max(gui.button_width("Cancel"), int(130 * L.fs))
        self.button(gui, pygame.Rect(x, by, cw, bh), "Cancel", "cancel")
        hw = max(gui.button_width("Host"), int(200 * L.fs))
        ok = self.problem() is None
        label = "Continue" if self.resume else "Host"
        hw = max(hw, gui.button_width(label))
        self.button(gui, pygame.Rect(rect.right - 26 - hw, by, hw, bh), label, "host", ok, True, ok)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name is None:
            return
        if name.startswith("field_"):
            self.focus = name[len("field_"):]
        elif name == "new_password":
            self.fields["password"].set(forge_net.make_password())
        elif name == "upnp":
            self.upnp = not self.upnp
        elif name == "toggle_resume" and self.saved and self.saved[2]:     # Round MP2b
            self.resume = not self.resume
            self.message = None
            if self.resume and self.saved[1].get("host"):
                self.fields["name"].set(self.saved[1]["host"])          # the saved game knows you by this name
        elif name == "discard_saved":
            gui.discard_online_save()
            self.saved = gui.online_saved()
            self.resume = False
        elif name == "use_relay":
            self.use_relay = not self.use_relay
            if self.use_relay and not self.fields["relay"].text:
                self.focus = "relay"
            elif not self.use_relay and self.focus == "relay":
                self.focus = "name"
        elif name in ("guests_minus", "guests_plus", "ai_minus", "ai_plus"):
            what, d = name.split("_")
            self.step(what, 1 if d == "plus" else -1)
        elif name == "cancel":
            self.done = True
        elif name == "host":
            self.host(gui)

    def key(self, gui, ev):
        k, mod = ev.key, getattr(ev, "mod", 0)
        if k == pygame.K_ESCAPE:
            self.done = True
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.host(gui)
        elif k == pygame.K_TAB:
            order = self.FIELDS + (("relay",) if self.use_relay else ())
            i = order.index(self.focus) if self.focus in order else 0
            self.focus = order[(i + (-1 if mod & pygame.KMOD_SHIFT else 1)) % len(order)]
        else:
            self.fields[self.focus].key(gui, ev)
            self.message = None


# ---- the host's waiting screen ------------------------------------------------------------------------------------
class HostWait:
    """Drawn by the table while it hosts and no friend has joined yet (ForgeTable.hosting_wait). The invite code needs this
    PC's internet address: the router's (UPnP), the PC's Tailscale address when the router can't help (CGNAT), or typed."""

    def __init__(self, session, password, port, upnp_wanted):
        self.session = session
        self.password = password
        self.port = port
        self.upnp_wanted = upnp_wanted
        self.address = TextField("", 120, "Your internet address (see the line above)")
        self.address_from = None                  # "router" / "tailscale" / None (typed)
        self.copied = None                        # (what, time)
        self.buttons = []
        self.t0 = time.monotonic()
        self.tailscale_checked = False

    # what to show
    def upnp_result(self):
        s = self.session
        if s.upnp is not None:
            return s.upnp
        return "waiting" if self.upnp_wanted else "off"

    def update(self):
        """Fill in the address from what the router said, once (the player can still change it)."""
        u = self.session.upnp
        if u is not None and self.address_from is None and not self.address.text:
            ip = u.get("external_ip")
            if u.get("ok") and ip:
                self.address.set(ip)
                self.address_from = "router"
            elif u.get("reason") == "cgnat" and not self.tailscale_checked:
                self.tailscale_checked = True
                ts = forge_net.tailscale_address()
                if ts:
                    self.address.set(ts)
                    self.address_from = "tailscale"

    def relay(self):
        """Round MP2d: (status, room, error) when hosting through a relay, else None."""
        rs = getattr(self.session, "relay_status", None)
        return rs() if callable(rs) else None

    def invite(self):
        """The invite code, or None while there's no usable address yet (through a relay: until the room is open)."""
        rel = self.relay()
        if rel is not None:
            status, room, _err = rel
            if status != "ready" or not room:
                return None
            address, port = self.session.relay
            return forge_net.make_invite(address, port, self.session.cert.fingerprint, self.password, room=room)
        addr = self.address.text.strip()
        if not addr or forge_net.validate_address(addr):
            return None
        return forge_net.make_invite(addr, self.port, self.session.cert.fingerprint, self.password)

    def failed(self):
        s = self.session
        if s.host_failed:
            return s.host_failed.get("text") or "Hosting failed."
        if s.exited and not s.hosting_cancelled:
            return "The rules engine stopped before your friend joined (details in forge_engine.log)."
        return None

    # input
    def click(self, gui, pos, button):
        name = next((n for r, n in reversed(self.buttons) if r.collidepoint(pos)), None)
        if name == "copy_invite" and self.invite():
            gui.write_clipboard(self.invite())
            self.copied = ("invite", time.monotonic())
        elif name == "copy_password":
            gui.write_clipboard(self.password)
            self.copied = ("password", time.monotonic())
        elif name == "cancel":
            gui.cancel_hosting()
        elif name == "back":
            gui.cancel_hosting()

    def key(self, gui, ev):
        if ev.key == pygame.K_ESCAPE:
            gui.cancel_hosting()
        elif self.relay() is None and self.address.key(gui, ev):
            self.address_from = None

    # drawing
    def button(self, gui, rect, label, name, enabled=True, primary=False):
        gui.draw_button(rect, label, name, enabled, primary, primary, hit=False, fkey="small")
        if enabled:
            self.buttons.append((pygame.Rect(rect), name))

    def draw(self, gui):
        self.update()
        scr, L = gui.screen, gui.L
        k = max(1.0, L.fs)
        self.buttons = []
        big, body, bodyb, small = gui.font("big", True), gui.font("body"), gui.font("body", True), gui.font("small")
        pw = min(int(900 * k), L.W - 40)
        tw = pw - 60
        rh = _row_h(gui)
        gap = int(10 * L.fs)
        bh = int(max(36, 40 * L.fs))
        failed = self.failed()
        s = self.session
        lines = []                                 # (text, font, colour) under the title
        if failed:
            lines += [(ln, body, RED) for ln in wrap_text(failed, body, tw)[:4]]
        elif s.hosting is None:
            dots = "." * (1 + int(time.time() * 2) % 3)
            lines.append(("Starting the rules engine" + dots + "  (10-20 seconds)", body, WHITE))
        elif getattr(s, "resume", None):           # Round MP2b: continuing a saved game
            dots = "." * (1 + int(time.time() * 2) % 3)
            if s.resuming:
                done, total = s.replay_progress
                lines.append(("Restoring the saved game%s  (%d of %d actions)" % (dots, done, total) if total else
                              "Restoring the saved game" + dots, body, WHITE))
            names = [g.get("name") for g in (s.seat_list or [])] or [n for _seat, n, _d in s.resume.get("guests") or []]
            missing = [n for n in names if n not in (s.guests_joined or [])]
            if missing:
                lines.append(("Waiting for %s to join again%s" % (" and ".join(missing), dots if not s.resuming else ""),
                              body if not s.resuming else small, WHITE))
            if s.guests_joined:
                lines += [(ln, small, GREEN) for ln in wrap_text("Back: " + ", ".join(s.guests_joined), small, tw)[:2]]
        else:
            dots = "." * (1 + int(time.time() * 2) % 3)
            wanted = getattr(s, "guests_wanted", 1)
            joined = list(getattr(s, "guests_joined", []) or [])
            if wanted <= 1:
                lines.append(("Waiting for your friend to join" + dots, body, WHITE))
            else:                                  # Round MP2c: several friends
                lines.append(("Waiting for your friends to join (%d of %d here)%s" % (len(joined), wanted, dots), body, WHITE))
                if joined:
                    lines += [(ln, small, GREEN) for ln in wrap_text("Joined: " + ", ".join(joined), small, tw)[:2]]
        rel = self.relay()
        if rel is not None:                        # Round MP2d: through a relay, the router doesn't matter
            text, colour = relay_line(rel, self.session.relay)
        else:
            text, colour = forge_net.upnp_line(self.upnp_result(), self.port)
        status = [(ln, small, COLOURS.get(colour, DIM)) for ln in wrap_text(text, small, tw)[:3]]
        if self.address_from == "tailscale":
            status += [(ln, small, DIM) for ln in wrap_text("Your Tailscale address is filled in below: your friend needs "
                                                            "Tailscale too (README part C).", small, tw)]
        refusals = len(s.join_refusals)
        if refusals:
            why = {"password": "a wrong password", "full": "the game already full", "deck": "a deck that couldn't be used",
                   "version": "a different version"}.get(s.join_refusals[-1].get("reason"), "a problem")
            status.append(("Someone tried to join and was turned away (%s)%s." % (why, " - %d tries" % refusals if refusals > 1
                                                                                   else ""), small, ORANGE))
        code = self.invite()
        code_lines = wrap_text_hard(code, small, tw - 20) if code else []
        hint = ("Type your internet address above. Without automatic port opening, find it by searching \"what is my IP\" "
                "in a web browser, and open the port by hand (README part B).")
        hint_lines = wrap_text(hint, small, tw)[:3] if not code and not failed and rel is None else []
        h = (24 + big.get_height() + 6 + len(lines) * (body.get_height() + 2) + gap + len(status) * small.get_height() + gap
             + rh + gap + max(1, len(code_lines)) * small.get_height() + 16 + gap + len(hint_lines) * small.get_height()
             + small.get_height() * 2 + gap + bh + 24)
        rect = pygame.Rect(0, 0, pw, min(h, L.H - 40))
        rect.center = (L.W // 2, L.H // 2)
        gfx.shadowed(scr, rect, 16, 6, 120)
        round_rect(scr, rect, gfx.DIALOG_BG, 16, 2, gfx.DIALOG_EDGE)
        x, y = rect.x + 30, rect.y + 24
        draw_text(scr, "Hosting a game", x, y, big, GOLD)
        y += big.get_height() + 6
        for ln, f, c in lines:
            draw_text(scr, ln, x, y, f, c)
            y += f.get_height() + 2
        y += gap
        for ln, f, c in status:
            draw_text(scr, ln, x, y, f, c)
            y += f.get_height()
        y += gap
        if not failed:
            if rel is not None:                    # Round MP2d: no address to type - the relay's is in the code
                draw_text(scr, clip_text("Through the relay at %s:%d" % self.session.relay, bodyb, tw), x, y + rh // 2, bodyb,
                          WHITE, "midleft")
            else:
                lab = "Your internet address"
                lw = bodyb.size(lab)[0] + int(14 * L.fs)
                draw_text(scr, lab, x, y + rh // 2, bodyb, WHITE, "midleft")
                fr = pygame.Rect(x + lw, y, rect.right - 30 - x - lw, rh)
                self.address.draw(gui, fr, True)
            y += rh + gap
            box = pygame.Rect(x, y, tw, max(1, len(code_lines)) * small.get_height() + 16)
            round_rect(scr, box, gfx.FIELD_BG, 6, 1, gfx.FIELD_EDGE)
            if code_lines:
                yy = box.y + 8
                for ln in code_lines:
                    draw_text(scr, ln, box.x + 10, yy, small, WHITE)
                    yy += small.get_height()
            else:
                draw_text(scr, "The invite code appears here once the relay has opened your room." if rel is not None else
                          "The invite code appears here once your address is filled in.", box.x + 10, box.centery, small,
                          DIM, "midleft")
            y = box.bottom + gap
            for ln in hint_lines:
                draw_text(scr, ln, x, y, small, DIM)
                y += small.get_height()
            draw_text(scr, clip_text("Send the invite code only to your friend: it contains the password.  Port %d  -  password %s"
                                     % (self.port, self.password), small, tw), x, y, small, ORANGE if code else DIM)
            y += small.get_height()
            if self.copied and time.monotonic() - self.copied[1] < 3:
                draw_text(scr, "Copied the %s." % ("invite code" if self.copied[0] == "invite" else "password"), x, y, small, GREEN)
        by = rect.bottom - 20 - bh
        if failed:
            bw = max(gui.button_width("Back to the deck screen", "small"), int(240 * L.fs))
            self.button(gui, pygame.Rect(rect.right - 30 - bw, by, bw, bh), "Back to the deck screen", "back", True, True)
            return
        cw = max(gui.button_width("Cancel", "small"), int(120 * L.fs))
        self.button(gui, pygame.Rect(x, by, cw, bh), "Cancel", "cancel")
        iw = max(gui.button_width("Copy invite code", "small"), int(200 * L.fs))
        self.button(gui, pygame.Rect(rect.right - 30 - iw, by, iw, bh), "Copy invite code", "copy_invite", bool(code), True)
        pw2 = max(gui.button_width("Copy password", "small"), int(160 * L.fs))
        self.button(gui, pygame.Rect(rect.right - 30 - iw - 10 - pw2, by, pw2, bh), "Copy password", "copy_password")


def wrap_text_hard(text, font, width):
    """Wraps a string with no spaces (an invite code) at any character."""
    out, line = [], ""
    for ch in text or "":
        if font.size(line + ch)[0] > width and line:
            out.append(line)
            line = ""
        line += ch
    if line:
        out.append(line)
    return out


# ---- Join a game --------------------------------------------------------------------------------------------------
class JoinJob:
    """Connects to the host on a worker thread: NetSession.start() can take up to ~25 s (the connection, then the host's
    answer), and the window must keep drawing meanwhile."""

    def __init__(self, session):
        self.session = session
        self.finished = False
        self.error = None                        # a NetRefused, or another exception's plain text
        self.cancelled = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="join")

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        try:
            self.session.start()
        except forge_net.NetRefused as e:
            self.error = e
        except Exception as e:                   # the window must survive whatever happens here
            self.error = forge_net.NetRefused("unknown", "%s: %s" % (type(e).__name__, e))
        finally:
            if self.cancelled and self.error is None:
                self.session.close()
            self.finished = True


class JoinDialog(dlg.Dialog):
    """Join a friend's game: paste their invite code (or type the address, port and password), and your name.
    Round MP2: or Watch it - the same code, no deck, every hidden card face down."""

    def __init__(self, gui, deck_entry, prefs=None, deck_problem=None):
        super().__init__()
        prefs = prefs or {}
        self.entry = deck_entry
        self.deck_problem = deck_problem          # Round MP2: Join can't be used with this deck, but Watch can
        self.by_hand = False
        self.fields = {
            "code": TextField("", 2000, "Paste the invite code here (Ctrl+V)"),
            "address": TextField(prefs.get("address") or "", 120, "e.g. 203.0.113.7 or a Tailscale address"),
            "port": TextField(str(prefs.get("port") or forge_net.DEFAULT_PORT), 5, allowed="0123456789"),
            "password": TextField("", 40, "The game password"),
            "name": TextField(prefs.get("name") or "", forge_net.NAME_MAX, "How your friend sees you"),
        }
        self.focus = "code"
        self.known = dict(prefs.get("known_hosts") or {})          # "address:port" -> fingerprint, from earlier games
        self.message = None
        self.job = None

    def order(self):
        return ("address", "port", "password", "name") if self.by_hand else ("code", "name")

    def target(self):
        """(address, port, password, fingerprint or None) from what's typed, or raises InviteError / returns a str problem."""
        if self.by_hand:
            addr = self.fields["address"].text.strip()
            why = forge_net.validate_address(addr)
            if why:
                return why
            port, why = forge_net.validate_port(self.fields["port"].text)
            if why:
                return why
            if not self.fields["password"].text.strip():
                return "Type the game password."
            return addr, port, self.fields["password"].text.strip(), self.known.get("%s:%d" % (addr, port))
        inv = forge_net.read_invite(self.fields["code"].text)
        self.room = inv.get("room")                # Round MP2d: through a relay
        return inv["address"], inv["port"], inv["password"], inv["fingerprint"]

    def problem(self):
        try:
            t = self.target()
        except forge_net.InviteError as e:
            return str(e)
        if isinstance(t, str):
            return t
        if not forge_net.clean_name(self.fields["name"].text):
            return "Type your name."
        return None

    def join(self, gui, watch=False):
        if self.job is not None:
            return
        why = self.problem() or (None if watch else self.deck_problem)
        if why:
            self.message = (why, RED)
            return
        self.room = None
        address, port, password, fp = self.target()
        extra = {"watch": True} if watch else {}
        if self.room and not self.by_hand:
            extra["room"] = self.room
        session, error = gui.make_guest_session(self.entry, forge_net.clean_name(self.fields["name"].text), address, port,
                                                password, fp, by_hand=self.by_hand, **extra)
        if error:
            self.message = (error, RED)
            return
        self.connecting_to = "%s:%d" % (address, port)
        self.watching = watch
        self.message = ("Connecting to %s..." % self.connecting_to, DIM)
        self.job = JoinJob(session).start()

    def poll(self, gui):
        """Called every frame by the table while this dialog is open."""
        job = self.job
        if job is None or not job.finished:
            return
        self.job = None
        if job.cancelled:
            return
        if job.error is not None:
            self.message = (str(job.error), RED)
            return
        gui.begin_online(job.session)
        self.done = True

    def draw(self, gui):
        self.poll(gui)
        L, scr = gui.L, gui.screen
        k = max(1.0, L.fs)
        title, body, small = gui.font("title", True), gui.font("body"), gui.font("small")
        rh = _row_h(gui)
        gap = int(10 * L.fs)
        pw = min(int(780 * k), L.W - 40)
        tw = pw - 52
        if self.deck_problem:
            intro = wrap_text("Join a friend who is hosting a game. %s - you can still watch." % self.deck_problem.rstrip("."),
                              small, tw)
        else:
            intro = wrap_text("Join a friend who is hosting a game, or watch one. You play: %s." % (
                getattr(self.entry, "name", "") or "your chosen deck"), small, tw)
        note = wrap_text("Without an invite code, this program can't check that the address is really your friend's PC "
                         "the first time." if self.by_hand else "The invite code holds your friend's address, the port, the "
                         "password and their PC's fingerprint.", small, tw)
        msg = wrap_text(self.message[0], small, tw)[:3] if self.message else []
        bh = int(max(42, 46 * L.fs))
        rows = len(self.order())
        need = (20 + title.get_height() + 8 + len(intro) * small.get_height() + gap + rows * (rh + gap) + rh + gap
                + len(note) * small.get_height() + gap + 3 * small.get_height() + gap + bh + 20)
        rect = self.panel(gui, pw, min(need, L.H - 40), real=True)
        x, y = rect.x + 26, rect.y + 20
        draw_text(scr, "Join a friend's game", x, y, title, WHITE)
        y += title.get_height() + 8
        for ln in intro:
            draw_text(scr, ln, x, y, small, gfx.BODY_TEXT)
            y += small.get_height()
        y += gap
        labels = {"code": "Invite code", "address": "Address", "port": "Port", "password": "Password", "name": "Your name"}
        lw = _label_w(gui, [labels[n] for n in self.order()], body)
        busy = self.job is not None
        for name in self.order():
            draw_text(scr, labels[name], x, y + rh // 2, body, WHITE, "midleft")
            fx = x + lw
            fw = rect.right - 26 - fx
            if name == "code":
                pw_ = max(gui.button_width("Paste"), int(90 * L.fs))
                self.button(gui, pygame.Rect(rect.right - 26 - pw_, y, pw_, rh), "Paste", "paste", not busy, fkey="small")
                fw -= pw_ + 8
            elif name == "port":
                fw = min(fw, int(140 * L.fs))
            self.fields[name].draw(gui, pygame.Rect(fx, y, fw, rh), self.focus == name and not busy)
            self.buttons.append((pygame.Rect(fx, y, fw, rh), "field_" + name))
            y += rh + gap
        toggle = "Use an invite code instead" if self.by_hand else "Enter the address by hand"
        tw_ = max(gui.button_width(toggle, "small"), int(220 * L.fs))
        self.button(gui, pygame.Rect(x, y, tw_, rh), toggle, "toggle", not busy, fkey="small")
        y += rh + gap
        for ln in note:
            draw_text(scr, ln, x, y, small, DIM)
            y += small.get_height()
        y += gap
        for ln in msg:
            draw_text(scr, ln, x, y, small, self.message[1])
            y += small.get_height()
        by = rect.bottom - 18 - bh
        cw = max(gui.button_width("Cancel"), int(130 * L.fs))
        self.button(gui, pygame.Rect(x, by, cw, bh), "Cancel", "cancel")
        jw = max(gui.button_width("Join"), gui.button_width("Connecting..."), int(200 * L.fs))
        can = not busy and self.problem() is None
        ok = can and not self.deck_problem
        watching = busy and getattr(self, "watching", False)
        self.button(gui, pygame.Rect(rect.right - 26 - jw, by, jw, bh), "Connecting..." if busy and not watching else "Join",
                    "join", ok, True, ok)
        ww = max(gui.button_width("Watch"), gui.button_width("Connecting..."), int(150 * L.fs))
        self.button(gui, pygame.Rect(rect.right - 26 - jw - 12 - ww, by, ww, bh), "Connecting..." if watching else "Watch",
                    "watch", can)

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name is None:
            return
        if name == "cancel":
            self.cancel()
        elif self.job is not None:
            return
        elif name.startswith("field_"):
            self.focus = name[len("field_"):]
        elif name == "paste":
            self.fields["code"].set(gui.read_clipboard() or "")
            self.focus = "code"
            self.message = None
        elif name == "toggle":
            self.by_hand = not self.by_hand
            self.focus = self.order()[0]
            self.message = None
        elif name == "join":
            self.join(gui)
        elif name == "watch":
            self.join(gui, watch=True)

    def cancel(self):
        if self.job is not None:
            self.job.cancelled = True              # the worker closes the session if it connects after all
            self.job = None
        self.done = True

    def key(self, gui, ev):
        k, mod = ev.key, getattr(ev, "mod", 0)
        if k == pygame.K_ESCAPE:
            self.cancel()
        elif self.job is not None:
            return
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.join(gui)
        elif k == pygame.K_TAB:
            order = self.order()
            i = order.index(self.focus) if self.focus in order else 0
            self.focus = order[(i + (-1 if mod & pygame.KMOD_SHIFT else 1)) % len(order)]
        else:
            self.fields[self.focus].key(gui, ev)
            self.message = None


def relay_line(rel, relay):
    """Round MP2d: (text, colour name) for the Host screen's status line when hosting through a relay."""
    status, room, err = rel
    where = "%s:%d" % relay
    if status == "ready":
        return "Connected to the relay at %s - your friends join through it (room %s)." % (where, room), "green"
    if status == "failed":
        return "The relay at %s refused this game: %s." % (where, err or "no reason given"), "red"
    if status == "lost":
        return "Lost the connection to the relay at %s - trying again..." % where, "orange"
    return "Connecting to the relay at %s..." % where, "dim"


def lobby_line(lobby, my_name=""):
    """Round MP2c: the guest's line while it waits for the other players, e.g. "Karl, Sam and you are here - waiting for 1 more
    player." (lobby = the host's {"t":"lobby"} message)."""
    if lobby.get("resuming"):                       # Round MP2b
        return "The host is restoring your saved game. It goes on when everyone is back."
    names = [n for n in (lobby.get("players") or []) if n]
    if my_name in names:
        names = [n for n in names if n != my_name] + ["you"]
    here = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else "you"
    more = max(0, int(lobby.get("wanted") or 0) - int(lobby.get("joined") or 0))
    ai = int(lobby.get("ai") or 0)
    tail = (" (and %d AI player%s)" % (ai, "s" if ai != 1 else "")) if ai else ""
    if more <= 0:
        return "%s are here%s - starting the game." % (here[0].upper() + here[1:], tail)
    return "%s %s here%s - waiting for %d more player%s." % (here[0].upper() + here[1:], "is" if len(names) == 1 else "are",
                                                            tail, more, "s" if more != 1 else "")


# ---- the other table left -----------------------------------------------------------------------------------------
def connection_lost_dialog(who, on_back, peer_left=None, net_lost=None, spectating=False):
    """Never "Forge stopped" for a network drop: say who left, in plain words. who = "host" / "guest" / "player" (a spectator
    hearing that the guest left). Round MP2: a drop is reconnected by itself first; this shows only once that failed (net_lost,
    the guest's own table), the guest's grace period ran out (peer_left "expired"), or someone left on purpose."""
    peer_left = peer_left or {}
    if net_lost:
        title = "Connection lost"
        text = net_lost.get("text") or forge_net.join_message("lost")
        if not spectating:
            text += " The game has ended."
    elif who == "host":
        title = "The host left"
        text = ("%s closed their game." % peer_left.get("name") if peer_left.get("name") else "The host closed their game.") \
            + (" The game has ended." if not spectating else "")
    elif peer_left.get("expired"):
        title = "Your friend didn't come back" if who == "guest" else "A player didn't come back"
        text = ("Their connection dropped and they didn't reconnect within %d seconds, so they lost the game."
                % int(peer_left.get("grace") or 0))
    elif peer_left.get("left"):
        title = "Your friend left" if who == "guest" else "A player left"
        text = "They left the game, so they lost it."
    else:
        title = "Your friend left" if who == "guest" else "A player left"
        text = "Their connection closed, so they lost the game."
    d = dlg.OptionsDialog(title, text, [("Back to the deck screen", on_back, "normal")], "Look at the board")
    return d
