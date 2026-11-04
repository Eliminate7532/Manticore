# SPDX-License-Identifier: GPL-3.0-or-later
"""
online_draft_screens.py - Round CD5: the draft window for drafting together online (Karl, 27 Oct 2026: "Draft together, Commander
Cube first"; "No timer"; "Everyone waits").

The same window as offline (draft_screens.DraftFlow: the pick screen with two cards a pick and Confirm, "Pack 2, pick 3 of 10" and
where the pack goes, the review, CD4's deck builder), with the online stages around it:

  Host:   Draft (main menu) > a Commander cube > "Draft with friends"
            host     your name, the port, open the router port (UPnP); the password is made for you ("New password")
            lobby    the invite code (Copy invite code) and who has joined - up to 3 friends; "Start the draft" when you want:
                     the AI drafters fill the empty seats
  Friend: Join online > paste the invite code > the window opens on
            joining  "Waiting for Karl to start the draft" and who is here

  Both:   pick       your pack only, your picks only (903.13d); of the others how many cards each has, and who everyone is
                     waiting for - there is no timer. A friend who drops pauses the draft for everyone: "Waiting for Sam to come
                     back"; the host can press "Let the AI pick for Sam" (Sam takes the seat back from the next pick).
          review     your picks, and the AI drafters' (not the other people's)
          decks      your deck, Auto-built from your picks; Edit my deck (CD4's builder); Ready
          ready      who is still building ("Waiting for Sam to finish their deck"); Not ready goes back
          starting   the 4-player game is starting (the host's engine builds it) - then the window closes onto the table.

The host's table runs the draft (online_draft.HostDraft); a friend's window works on online_draft.GuestPod. Nothing here talks to
the network itself: the table's session carries every message (online_draft.pump_host / pump_guest).
"""
import time

import pygame

import draft_core as dc
import draft_pod as dp
import draft_screens as ds
import forge_dialogs as dlg
import forge_net
import gfx
import online_draft as od
import online_screens as onl
from gfx import DIM, GOLD, GREEN, ORANGE, RED, WHITE, clip_text, draw_text, round_rect, wrap_text

TITLE = "Draft with friends"
HOST_INTRO = ("You host the draft on this PC: up to three friends join with your invite code, and AI drafters fill the empty "
              "seats. Everyone takes two cards a pick, with no timer - the others wait for the slowest picker. Then each of you "
              "builds a deck on your own PC, and all four play one Commander game: 40 life, 21 commander damage.")
LOBBY_HELP = ("Send the invite code to your friends (it holds the password). Start when everyone you want is here: the AI "
              "drafters take the empty seats. Nobody can join once the draft has started, but a friend who drops can come back.")
GUEST_WAIT = "Waiting for {host} to start the draft."
DROP_HINT = "Their seat waits for them. Let the AI pick for {name} if they can't come back (they take it back from the next pick)."
READY_SAID = "Your deck is ready. Waiting for {names} to finish their deck{s}."
ALL_READY = "Everyone is ready: starting the game..."
STARTING = "Starting the game - the host's PC is setting up the four seats (10-20 seconds)..."
LEFT_SAID = "You left the draft: the AI picks for your seat."


class OnlineDraftFlow(ds.DraftFlow):
    """role "host": `job` is the draft window's loaded cube (draft_screens.CubeJob). role "guest": `session` is the NetSession that
    joined the host's draft (its welcome said "draft")."""

    def __init__(self, gui, role, job=None, session=None, from_title=False, root=None, prefs=None):
        dlg.Dialog.__init__(self)
        import forge_settings as fset
        self.role = role
        self.from_title, self.root, self.lookup, self.cube_folder = from_title, root, None, None
        self.runtime = getattr(getattr(gui, "launcher", None), "runtime", None)
        self.field = fset.TextField("Cube", "", multiline=False, limit=300, lines=1, hint="")
        self.job = job
        self.loaded_ref = None
        self.message = None
        self.pod = None
        self.builder = None
        self.by_hand = False
        self.edit = None
        self.build_scroll = self.build_max_scroll = 0
        self.choosing = False
        self.btiles = []
        self.resume = None
        self.stock = None
        self.style_choice = None
        self.opponent = 1
        self.selected = []
        self.tiles = []
        self.hover = None
        self.review_seat = 0
        self.scroll = self.max_scroll = 0
        self.decks = None
        self.problems = None
        self.session = session
        self.hd = None                          # the host's online_draft.HostDraft, from Start on
        self.wait = None                        # the host's online_screens.HostWait (address, invite code) in the lobby
        self.ended = None                       # (title, text): the draft can't go on (the host left, the engine stopped)
        self.my_deck = None
        self.started_game = False
        self.copied_at = 0.0
        prefs = prefs or {}
        if role == "host":
            self.stage = "host"
            self.fields = {"name": onl.TextField(prefs.get("name") or "", forge_net.NAME_MAX, "How your friends see you"),
                           "port": onl.TextField(str(prefs.get("port") or forge_net.DEFAULT_PORT), 5, allowed="0123456789")}
            self.password = forge_net.make_password()
            self.upnp = bool(prefs.get("upnp", True))
            # round RLY2: through the relay by default, as Host online (the address is the one Host online uses)
            self.relay_text = prefs.get("relay") or prefs.get("relay_default") or ""
            self.use_relay = bool(self.relay_text) and not prefs.get("direct")
            self.focus = "name"
            # the host's program closed in the middle of an online draft: Continue it (the friends join again by name)
            try:
                self.resume_pod = dp.unfinished(root, online=True)
            except Exception:
                self.resume_pod = None
            self.continuing = self.resume_pod is not None
        else:
            self.stage = "joining"
            self.pod = od.GuestPod(od.guest_sender(session))
            self.fields = {}
            self.focus = None
            session.send(c="draft", msg={"k": "view"})          # send me everything (also after a reconnect by name)

    # ---- the messages ------------------------------------------------------------------------------------------------------
    def tick(self, gui):
        """Every frame: what the host's engine / the host's table sent. Moves the stages on."""
        s = self.session
        if s is None:
            return
        if self.role == "host":
            if self.hd is not None:
                od.pump_host(s, self.hd)
                if self.stage == "pick" and self.hd.stage != "pick":
                    self.to_review()
                if self.stage == "ready" and self.hd.all_ready() and not self.started_game:
                    self.start_game(gui)
            if s.host_failed and self.ended is None:
                self.ended = ("Hosting stopped", s.host_failed.get("text") or "Hosting failed.")
            elif s.exited and not s.ready and self.ended is None:
                self.ended = ("The rules engine stopped", "The draft can't go on (details in forge_engine.log). Your picks are "
                              "saved.")
            if s.draft_game_failed and self.ended is None:
                self.ended = ("The game couldn't start", s.draft_game_failed)
            return
        gp = self.pod
        changed = od.pump_guest(s, gp)
        if changed and not gp.started and gp.refused and self.ended is None:
            self.ended = ("Not in this draft", gp.refused)
        if changed and gp.started:
            if self.stage == "joining" and gp.ready_to_show:          # (the host started, or sent everything again)
                if gp.done:
                    self.to_review()
                else:
                    self.to_pick(gui)
            elif self.stage == "pick" and gp.done:
                self.to_review()
            if self.stage == "pick":
                self.prefetch(gui)
        if self.ended is None:
            if s.peer_left and s.peer_left.get("who") == "host":
                self.ended = ("The host left", "%s closed the draft." % (s.peer_left.get("name") or "The host"))
            elif s.net_lost:
                self.ended = ("Connection lost", s.net_lost.get("text") or forge_net.join_message("lost"))

    # ---- the host: open the draft, the lobby -------------------------------------------------------------------------------
    def host_problem(self):
        if not forge_net.clean_name(self.fields["name"].text):
            return "Type your name."
        _port, why = forge_net.validate_port(self.fields["port"].text)
        return why

    def open_hosting(self, gui):
        why = self.host_problem()
        if why:
            self.message = (why, RED)
            gui.play_cue("ui.error")
            return
        port, _ = forge_net.validate_port(self.fields["port"].text)
        name = forge_net.clean_name(self.fields["name"].text)
        relay = forge_net.parse_relay(self.relay_text) if self.use_relay else None       # round RLY2
        if isinstance(relay, str):
            self.message = ("The relay: " + relay, RED)
            gui.play_cue("ui.error")
            return
        session, error = gui.host_draft(name, port, self.password, self.upnp, **({"relay": relay} if relay else {}))
        if error:
            self.message = (error, RED)
            gui.play_cue("ui.error")
            return
        self.session = session
        self.wait = onl.HostWait(session, self.password, port, self.upnp)
        self.host_name = name
        self.stage, self.message = "lobby", None
        gui.play_cue("ui.menu_select")

    def invite(self):
        if self.wait is None:
            return None
        code = self.wait.invite()
        if not code:
            return None
        inv = forge_net.read_invite(code)
        return forge_net.make_invite(inv["address"], inv["port"], inv["fingerprint"], inv["password"], room=inv.get("room"),
                                     fmt="commander_draft", draft=True)

    def friends_here(self):
        s = self.session
        return list(getattr(s, "guests_joined", []) or []) if s is not None else []

    def start_ok(self):
        s = self.session
        if self.continuing:                                   # a saved draft: its cards are in the pod; absent friends wait
            return bool(s is not None and s.hosting is not None and self.resume_pod is not None)
        return bool(s is not None and s.hosting is not None and self.friends_here() and self.job and self.job.done
                    and not self.job.error)

    def start_draft(self, gui):
        """Start: the lobby closes, the seats are fixed, the pod is made, the AI drafters pick, the friends get their packs."""
        if not self.start_ok():
            self.message = ("Wait for a friend to join first." if not self.friends_here() else "Not ready yet.", ORANGE)
            gui.play_cue("ui.error")
            return
        s = self.session
        s.draft_locked = None
        pod_friends = self.resume_friends() if self.continuing else []
        here = {n.lower() for n in self.friends_here()}
        s.draft_lock(reserve=[n for n in pod_friends if n.lower() not in here])
        end = time.monotonic() + 5
        while s.draft_locked is None and time.monotonic() < end and s.alive():
            s.poll()
            time.sleep(0.02)
        locked = (s.draft_locked or {}).get("seats") or []
        seats = [(int(o["seat"]), o["name"]) for o in locked]
        if not seats:
            self.message = ("The draft couldn't start: the host's engine didn't answer.", RED)
            return
        if self.continuing:
            return self.continue_draft_online(gui, locked, pod_friends)
        try:
            self.pod = dp.Pod.new(self.job.cube, self.job.check, root=self.root, style="commander",
                                  seats=od.HostDraft.seats_for(self.host_name, [n for _s, n in seats]),
                                  online={"friends": [n for _s, n in seats], "host": self.host_name})
        except (dc.DraftError, OSError) as e:
            self.message = (f"The draft couldn't start: {e}", RED)
            gui.play_cue("ui.error")
            return
        self.hd = od.HostDraft(self.pod, seats, s.draft_send)
        gui.online_draft_pod = self.pod
        self.hd.start()
        gui.play_cue("deck.shuffle")
        self.to_pick(gui)

    # ---- the host: Continue it (the host's program had closed in the middle of the draft) ---------------------------------
    def resume_friends(self):
        return od.pod_friends(self.resume_pod) if self.resume_pod else []

    def continue_draft_online(self, gui, locked, pod_friends):
        """Start with the saved pod: each friend's seat by name (connected or waiting for them), anyone else told no."""
        friends, strangers = od.continue_seats(self.resume_pod, locked)
        self.pod = self.resume_pod
        self.resume_pod = None
        self.hd = od.HostDraft(self.pod, friends, self.session.draft_send)
        gui.online_draft_pod = self.pod
        for ns in strangers:                                  # joined, but not one of this draft's friends
            self.session.draft_send(ns, {"k": "refused", "text": od.NOT_IN_DRAFT})
        self.hd.start()
        gui.play_cue("deck.shuffle")
        if self.hd.stage != "pick":
            self.to_review()
        else:
            self.to_pick(gui)

    def discard_resume(self, gui):
        if self.resume_pod is not None:
            try:
                self.resume_pod.discard()
            except OSError as e:
                self.message = (f"The saved draft couldn't be changed: {e}", RED)
                return
        self.resume_pod, self.continuing = None, False
        gui.play_cue("ui.click")

    def stop(self, gui):
        """The host's Stop / Back: friends are told, the engine closes. The draft so far is kept on disk."""
        if self.pod is not None:
            try:
                self.pod.save()
            except OSError:
                pass
        self.done = True
        gui.end_online_draft(stopped=True)

    # ---- picking -------------------------------------------------------------------------------------------------------
    def confirm(self, gui):
        if self.pod is None or len(self.selected) != self.pod.needed() or not self.pod.needed():
            gui.play_cue("ui.error")
            need = self.pod.needed() if self.pod else 0
            self.message = (f"Choose {need} card{'s' if need != 1 else ''} first." if need else "No pack in front of you yet.",
                            ORANGE)
            return False
        paused = self.paused_names()
        if paused:
            self.message = (f"Waiting for {od.names_text(paused)} to come back.", ORANGE)
            gui.play_cue("ui.error")
            return False
        try:
            if self.role == "host":
                self.hd.host_pick(self.selected)
            else:
                self.pod.pick(self.selected)
        except (dc.DraftError, OSError) as e:
            self.message = (str(e), RED)
            gui.play_cue("ui.error")
            return False
        gui.play_cue("card.pickup")
        self.selected, self.hover, self.message = [], None, None
        if self.role == "host" and self.hd.stage != "pick":
            self.to_review()
        else:
            self.prefetch(gui)
        return True

    def paused_names(self):
        if self.role == "host":
            return [self.hd.name(s) for s in self.hd.paused_on()] if self.hd else []
        return self.pod.paused() if self.pod else []

    def waiting_names(self):
        if self.role == "host":
            return [self.hd.name(s) for s in self.hd.waiting_for() if s != 0] if self.hd else []
        return self.pod.waiting() if self.pod else []

    def dropped_friends(self):
        """The host: [(seat, name)] of friends who are away and whose seat waits for them (a "Let the AI pick" button each)."""
        if self.role != "host" or self.hd is None:
            return []
        return [(s, self.hd.name(s)) for s in self.hd.paused_on()]

    def review_seats(self):
        """Yours and the AI drafters' (Claude's default in the brief: not the other people's)."""
        if self.pod is None:
            return []
        if self.role == "host":
            return [0] + [s for s in range(self.pod.draft.n) if self.pod.draft.seats[s].kind == "ai"]
        return [0] + sorted(s for s in range(self.pod.draft.n) if s not in self.pod.people_local)

    # ---- the decks --------------------------------------------------------------------------------------------------------
    def to_decks(self, gui):
        deck = self.my_deck if self.my_deck is not None else self.pod.build(0)
        self.decks = [deck]
        self.problems = [self.pod.problems(0, deck) if deck is not None else None]
        self.stage, self.hover, self.message = "decks", None, None
        gui.play_cue("deck.shuffle")

    def ready(self, gui):
        """Ready with my deck (it goes to the host, who checks it against my picks)."""
        deck = (self.decks or [None])[0]
        if deck is None:
            self.message = ("No card in your picks can be a commander, so you have no deck yet: Edit my deck.", RED)
            gui.play_cue("ui.error")
            return
        self.my_deck = deck
        if self.role == "host":
            try:
                self.hd.host_deck(deck, by_hand=self.by_hand)
            except dc.DraftError as e:
                self.message = (str(e), RED)
                return
        else:
            self.pod.send_deck(deck, by_hand=self.by_hand)
        self.stage, self.message = "ready", None
        gui.play_cue("ui.menu_select")

    def unready(self, gui):
        if self.role == "host":
            self.hd.host_unready()
        else:
            self.pod.unready()
        self.stage = "decks"
        gui.play_cue("ui.click")

    def ready_names(self):
        """[(name, ready)] of every person."""
        if self.role == "host":
            if self.hd is None:
                return []
            return [(("You" if s == 0 else self.hd.name(s)), r) for s, r in self.hd.ready().items()]
        me = self.pod.names[self.pod.real_seat] if self.pod.names and self.pod.real_seat is not None else None
        return [("You" if name == me else name, r) for name, r in self.pod.ready_list()]

    def start_game(self, gui):
        """The host, once every deck is in: the decks are written and the game starts in the host's engine."""
        self.started_game = True
        self.stage = "starting"
        error = gui.start_online_draft_game(self)
        if error:
            self.started_game = False
            self.stage = "ready"
            self.message = (error, RED)

    def give_to_ai(self, gui, seat):
        if self.hd is not None and self.hd.give_to_ai(seat):
            gui.say(f"The AI picks for {self.hd.name(seat)} until they are back.", DIM, 5.0)
            gui.play_cue("ui.click")
            if self.stage == "pick" and self.hd.stage != "pick":
                self.to_review()

    def leave(self, gui):
        """Leaving the draft: the host stops it for everyone (the draft so far stays saved); a friend's seat goes to the AI."""
        if self.role == "host":
            self.stop(gui)
            return
        if self.session is not None and self.ended is None:
            self.session.send(c="draft", msg={"k": "leave"})
        self.done = True
        gui.end_online_draft(stopped=False)
        gui.say(LEFT_SAID, DIM, 6.0)

    # ---- input ----------------------------------------------------------------------------------------------------------
    def press(self, gui, name):
        if name == "open_host":
            self.open_hosting(gui)
        elif name == "upnp":
            self.upnp = not self.upnp
            gui.play_cue("ui.click")
        elif name == "use_relay" and getattr(self, "relay_text", ""):         # round RLY2
            self.use_relay = not self.use_relay
            gui.play_cue("ui.click")
        elif name == "new_password":
            self.password = forge_net.make_password()
            gui.play_cue("ui.click")
        elif name == "copy_invite":
            code = self.invite()
            if code:
                gui.write_clipboard(code)
                self.copied_at = time.monotonic()
                gui.play_cue("ui.click")
        elif name == "start_online":
            self.start_draft(gui)
        elif name == "continue_saved":
            self.continuing = not self.continuing and self.resume_pod is not None
            gui.play_cue("ui.click")
        elif name == "discard_saved":
            self.discard_resume(gui)
        elif name == "close_host":                            # Back, before anything is hosted
            self.done = True
            gui.end_online_draft(stopped=False)
        elif name in ("stop", "leave", "back"):
            self.leave(gui)
        elif name == "end_ok":
            self.done = True
            gui.end_online_draft(stopped=self.role == "host")
        elif name == "build":
            self.to_decks(gui)
        elif name == "ready":
            self.ready(gui)
        elif name == "unready":
            self.unready(gui)
        elif name.startswith("ai_for_"):
            self.give_to_ai(gui, int(name[7:]))
        elif name == "build_done":
            super().press(gui, name)
            self.stage = "decks"
        elif name == "save":
            self.ready(gui)
        else:
            super().press(gui, name)

    def click(self, gui, pos, button):
        if self.stage == "host":
            for key, field in self.fields.items():
                if field.rect.collidepoint(pos):
                    self.focus = key
                    return
        super().click(gui, pos, button)

    def key(self, gui, ev):
        k = ev.key
        enter = k in (pygame.K_RETURN, pygame.K_KP_ENTER)
        if self.ended is not None:
            if enter or k == pygame.K_ESCAPE:
                self.press(gui, "end_ok")
            return
        if self.stage == "host":
            if k == pygame.K_ESCAPE:
                self.done = True
                gui.end_online_draft(stopped=False)
            elif enter:
                self.open_hosting(gui)
            elif k == pygame.K_TAB:
                order = list(self.fields)
                self.focus = order[(order.index(self.focus) + 1) % len(order)] if self.focus in order else order[0]
            elif self.focus in self.fields:
                self.fields[self.focus].key(gui, ev)
            return
        if self.stage == "lobby":
            if k == pygame.K_ESCAPE:
                self.stop(gui)
            elif enter:
                self.start_draft(gui)
            elif self.wait is not None and self.wait.relay() is None:
                self.wait.address.key(gui, ev)
            return
        if self.stage in ("joining", "starting"):
            if k == pygame.K_ESCAPE:
                self.leave(gui)
            return
        if self.stage == "decks":
            if k == pygame.K_ESCAPE:
                self.to_review()
            elif enter:
                self.ready(gui)
            return
        if self.stage == "ready":
            if k == pygame.K_ESCAPE:
                self.unready(gui)
            return
        if self.stage in ("pick", "review") and k == pygame.K_ESCAPE:
            return                                            # online, leaving is a button (it ends the draft for others)
        if self.stage == "build" and k == pygame.K_ESCAPE and not self.choosing:
            self.build_done(gui)
            self.stage = "decks"
            return
        super().key(gui, ev)

    # ---- drawing ------------------------------------------------------------------------------------------------------
    def draw(self, gui):
        self.tick(gui)
        L = gui.L
        rect = self.panel(gui, L.W - 40, L.H - 40, real=True)
        self.tiles = []
        fs = L.fs
        self.pad = int(16 * fs)
        self.bh = int(max(36, 42 * fs))
        if self.ended is not None:
            self.draw_ended(gui, rect)
            return
        if self.stage == "host":
            self.draw_host(gui, rect)
        elif self.stage == "lobby":
            self.draw_lobby(gui, rect)
        elif self.stage in ("joining", "starting"):
            self.draw_waiting(gui, rect)
        elif self.stage == "pick":
            self.draw_pick(gui, rect)
        elif self.stage == "review":
            self.draw_review(gui, rect)
        elif self.stage == "build":
            self.draw_build(gui, rect)
        elif self.stage in ("decks", "ready"):
            self.draw_online_decks(gui, rect)

    def leave_label(self):
        return "Stop the draft" if self.role == "host" else "Leave the draft"

    def review_next_label(self):
        return "Build my deck"

    def draw_ended(self, gui, rect):
        scr, pad = gui.screen, self.pad
        title, text = self.ended
        y = self.header(gui, rect, title)
        body = gui.font("body")
        for ln in wrap_text(text, body, rect.w - 2 * pad)[:8]:
            draw_text(scr, ln, rect.x + pad, y, body, WHITE)
            y += body.get_height() + 2
        self.footer(gui, rect, [], [("Back", "end_ok", True, True)])

    def draw_host(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        body, bodyb, small = gui.font("body"), gui.font("body", True), gui.font("small")
        y = self.header(gui, rect, TITLE, self.cube_line())
        wrap_w = min(rect.w - 2 * pad, int(1000 * fs))
        for ln in wrap_text(HOST_INTRO, small, wrap_w)[:5]:
            draw_text(scr, ln, rect.x + pad, y, small, WHITE)
            y += small.get_height() + 1
        y += int(12 * fs)
        rh = onl._row_h(gui)
        labels = {"name": "Your name", "port": "Port"}
        lw = max(bodyb.size(t)[0] for t in labels.values()) + int(16 * fs)
        fw = min(int(420 * fs), wrap_w - lw)
        for key in ("name", "port"):
            draw_text(scr, labels[key], rect.x + pad, y + rh // 2, bodyb, WHITE, "midleft")
            self.fields[key].draw(gui, pygame.Rect(rect.x + pad + lw, y, fw if key == "name" else int(130 * fs), rh),
                                  self.focus == key)
            y += rh + int(8 * fs)
        draw_text(scr, "Password", rect.x + pad, y + rh // 2, bodyb, WHITE, "midleft")
        draw_text(scr, self.password, rect.x + pad + lw, y + rh // 2, body, GOLD, "midleft")
        nw = gui.button_width("New password", "small")
        self.button(gui, pygame.Rect(rect.x + pad + lw + body.size(self.password)[0] + int(14 * fs), y + (rh - self.bh) // 2,
                                     nw, self.bh), "New password", "new_password", True, fkey="small")
        y += rh + int(8 * fs)
        if self.relay_text:                                    # round RLY2: the relay first, ticked by default
            onl.tick_box(gui, self, pygame.Rect(rect.x + pad, y, wrap_w, rh), self.use_relay, onl.RELAY_TICK, "use_relay")
            y += rh + int(8 * fs)
        if not self.use_relay:
            onl.tick_box(gui, self, pygame.Rect(rect.x + pad, y, wrap_w, rh), self.upnp,
                         "Open the port on my router automatically (UPnP)", "upnp")
            y += rh + int(8 * fs)
        if self.resume_pod is not None:
            y = self.draw_saved_box(gui, rect, y, wrap_w)
        top = self.footer(gui, rect, [("Back", "close_host", True, False)], [("Open the draft", "open_host", True, True)])
        self.draw_message(gui, rect.x + pad + gui.button_width("Back") + int(16 * fs), top + (self.bh - small.get_height()) // 2,
                          rect.w - 2 * pad - gui.button_width("Back") - gui.button_width("Open the draft") - int(40 * fs))

    def draw_saved_box(self, gui, rect, y, wrap_w):
        """The online draft whose host's program closed: Continue it (on by default) or Discard it."""
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        small, smallb = gui.font("small"), gui.font("small", True)
        pod = self.resume_pod
        friends = od.names_text(self.resume_friends())
        when = time.strftime("%d %b %H:%M", time.localtime(pod.created or 0)).lstrip("0")
        text = f"{pod.progress_text()}, with {friends} (started {when})."
        box_h = smallb.get_height() + 2 * small.get_height() + self.bh + int(30 * fs)
        foot = rect.bottom - pad - self.bh - int(10 * fs)
        box = pygame.Rect(rect.x + pad, min(y, foot - box_h), wrap_w, box_h)
        round_rect(scr, box, gfx.ROW_BG, 10, 2, GOLD)
        draw_text(scr, "Your online draft isn't finished", box.x + int(12 * fs), box.y + int(8 * fs), smallb, GOLD)
        yy = box.y + int(10 * fs) + smallb.get_height()
        for ln in wrap_text(text, small, box.w - int(24 * fs))[:2]:
            draw_text(scr, ln, box.x + int(12 * fs), yy, small, WHITE)
            yy += small.get_height()
        by = box.bottom - self.bh - int(8 * fs)
        cw = gui.button_width("Continue it")
        self.button(gui, pygame.Rect(box.x + int(12 * fs), by, cw, self.bh), "Continue it", "continue_saved", True,
                    self.continuing)
        dw = gui.button_width("Discard it")
        self.button(gui, pygame.Rect(box.x + int(22 * fs) + cw, by, dw, self.bh), "Discard it", "discard_saved", True)
        note = ("Your friends join with the new invite code and the same names; each gets their seat back." if self.continuing
                else "A new draft of the cube you chose.")
        draw_text(scr, clip_text(note, small, box.right - (box.x + int(34 * fs) + cw + dw) - int(12 * fs)),
                  box.x + int(34 * fs) + cw + dw, by + self.bh // 2, small, DIM, "midleft")
        return box.bottom + int(8 * fs)

    def cube_line(self):
        if getattr(self, "continuing", False) and self.resume_pod is not None:
            return f"Continuing: {self.resume_pod.progress_text()}"
        j = self.job
        if j is None or not j.done or j.error:
            return "Loading the cube..." if j is not None and not j.done else None
        cube = j.cube
        return f"{cube.name or cube.id}{' by ' + cube.owner if cube.owner else ''}: {j.playable} cards this Forge can play."

    def draw_lobby(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        body, bodyb, small, smallb = gui.font("body"), gui.font("body", True), gui.font("small"), gui.font("small", True)
        w = self.wait
        w.update()
        s = self.session
        y = self.header(gui, rect, TITLE + " - waiting for your friends", self.cube_line())
        wrap_w = min(rect.w - 2 * pad, int(1100 * fs))
        x = rect.x + pad
        failed = w.failed()
        if failed:
            for ln in wrap_text(failed, body, wrap_w)[:4]:
                draw_text(scr, ln, x, y, body, RED)
                y += body.get_height() + 2
            self.footer(gui, rect, [("Back", "stop", True, True)], [])
            return
        if s.hosting is None:
            dots = "." * (1 + int(time.time() * 2) % 3)
            draw_text(scr, "Starting the rules engine" + dots + "  (10-20 seconds; friends can join once it is up)", x, y, body,
                      WHITE)
            y += body.get_height() + int(8 * fs)
        rel = w.relay()
        text, colour = onl.relay_line(rel, s.relay) if rel is not None else forge_net.upnp_line(w.upnp_result(), w.port)
        for ln in wrap_text(text, small, wrap_w)[:3]:
            draw_text(scr, ln, x, y, small, onl.COLOURS.get(colour, DIM))
            y += small.get_height()
        y += int(8 * fs)
        rh = onl._row_h(gui)
        if rel is None:
            lab = "Your internet address"
            lw = bodyb.size(lab)[0] + int(14 * fs)
            draw_text(scr, lab, x, y + rh // 2, bodyb, WHITE, "midleft")
            w.address.draw(gui, pygame.Rect(x + lw, y, min(int(520 * fs), wrap_w - lw), rh), True)
            y += rh + int(8 * fs)
        code = self.invite()
        lines = onl.wrap_text_hard(code, small, wrap_w - 20) if code else []
        box = pygame.Rect(x, y, wrap_w, max(1, min(4, len(lines))) * small.get_height() + 16)
        round_rect(scr, box, gfx.FIELD_BG, 6, 1, gfx.FIELD_EDGE)
        if lines:
            yy = box.y + 8
            for ln in lines[:4]:
                draw_text(scr, ln, box.x + 10, yy, small, WHITE)
                yy += small.get_height()
        else:
            draw_text(scr, "The invite code appears here once the relay has opened your room." if rel is not None else
                      "The invite code appears here once your address is filled in.", box.x + 10, box.centery, small,
                      DIM, "midleft")
        y = box.bottom + int(6 * fs)
        note = "Send the invite code only to your friends: it holds the password (%s)." % self.password
        if time.monotonic() - self.copied_at < 3:
            note = "Copied the invite code."
        draw_text(scr, clip_text(note, small, wrap_w), x, y, small, GREEN if "Copied" in note else ORANGE)
        y += small.get_height() + int(12 * fs)
        here = self.friends_here()
        draw_text(scr, "At the table", x, y, smallb, GOLD)
        y += smallb.get_height() + 2
        rows = [f"You ({self.host_name}) - the host"]
        if self.continuing and self.resume_pod is not None:          # the saved draft's friends, by name
            back = {n.lower() for n in here}
            mine = self.resume_friends()
            rows += [f"{n} - {'back' if n.lower() in back else 'not back yet (their seat waits)'}" for n in mine]
            rows += [f"{n} - not in this draft" for n in here if n.lower() not in {m.lower() for m in mine}]
        else:
            rows += [f"{n} - joined" for n in here]
            rows += [f"AI drafter (an empty seat)" for _ in range(dc.SEATS - 1 - len(here))]
        for row in rows:
            draw_text(scr, clip_text(row, body, wrap_w), x, y, body, WHITE if "AI drafter" not in row else DIM)
            y += body.get_height() + 1
        y += int(8 * fs)
        for ln in wrap_text(LOBBY_HELP, small, wrap_w)[:3]:
            if y + small.get_height() > rect.bottom - pad - self.bh - int(10 * fs):
                break
            draw_text(scr, ln, x, y, small, DIM)
            y += small.get_height()
        label = ("Continue the draft" if self.continuing else
                 "Start the draft" + (f" ({len(here)} friend{'s' if len(here) != 1 else ''})" if here else ""))
        top = self.footer(gui, rect, [("Stop", "stop", True, False)],
                          [(label, "start_online", self.start_ok(), True), ("Copy invite code", "copy_invite", bool(code), False)])
        self.draw_message(gui, rect.x + pad + gui.button_width("Stop") + int(16 * fs),
                          top + (self.bh - small.get_height()) // 2, int(rect.w * 0.3))

    def draw_waiting(self, gui, rect):
        scr, pad = gui.screen, self.pad
        body, small = gui.font("body"), gui.font("small")
        s = self.session
        if self.stage == "starting":
            y = self.header(gui, rect, "The draft is done")
            text = STARTING if self.role == "host" else "Starting the game on %s's PC..." % (s.peer_name or "the host")
        else:
            y = self.header(gui, rect, TITLE)
            text = GUEST_WAIT.format(host=s.peer_name or "the host")
        dots = "." * (1 + int(time.time() * 2) % 3)
        draw_text(scr, text + dots, rect.x + pad, y, body, WHITE)
        y += body.get_height() + 6
        lobby = getattr(s, "lobby", None)
        if lobby and self.stage == "joining":
            names = [n for n in lobby.get("players") or [] if n]
            draw_text(scr, clip_text("Here so far: " + ", ".join(names), small, rect.w - 2 * pad), rect.x + pad, y, small, DIM)
            y += small.get_height()
        self.footer(gui, rect, [(self.leave_label(), "leave", True, False)], [])

    # the pick screen: the offline one, plus who the others wait for (no timer)
    def draw_pick(self, gui, rect):
        super().draw_pick(gui, rect)
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        pack = self.pod.pack()
        body = gui.font("body", True)
        if not pack:
            if self.role == "guest" and self.pod.pending is not None:
                text = "Your pick is on its way to the host..."
            else:
                waiting = self.waiting_names()
                text = (f"Waiting for {od.names_text(waiting)} to pick - your next pack comes from them." if waiting else
                        "Waiting for the next pack...")
            if self.role == "guest" and self.pod.ai_for_me():
                text = "The AI is picking for you right now (the host chose it): your seat is yours again from the next pick."
            w = int(rect.w * 0.6)
            yy = rect.centery - body.get_height()
            for ln in wrap_text(text, body, w)[:3]:
                draw_text(scr, ln, rect.x + pad + w // 2, yy, body, GOLD, "midtop")
                yy += body.get_height() + 2

    def header(self, gui, rect, title, sub=None):
        """The offline header, then - while a friend is away - the pause bar under it (the stage's content starts below both)."""
        y = super().header(gui, rect, title, sub)
        if self.stage in ("pick", "review", "decks", "ready", "build") and self.paused_names():
            y = self.draw_pause_bar(gui, rect, y)
        return y

    def draw_pause_bar(self, gui, rect, y):
        """Under the header while a friend is away: who the draft waits for; the host gets a button each. Returns the y below."""
        paused = self.paused_names()
        scr, fs = gui.screen, gui.L.fs
        smallb = gui.font("small", True)
        h = self.bh + int(12 * fs)
        bar = pygame.Rect(rect.x + self.pad, y, rect.w - 2 * self.pad, h)
        round_rect(scr, bar, gfx.DIALOG_BG, 8, 2, ORANGE)
        text = (f"Waiting for {od.names_text(paused)} to come back - the draft is paused." if self.stage == "pick" else
                f"Waiting for {od.names_text(paused)} to come back - the game can't start without them.")
        x = bar.x + int(12 * fs)
        bx = bar.right - int(10 * fs)
        building = self.hd is not None and self.hd.stage != "pick"
        for seat, name in reversed(self.dropped_friends()):
            label = f"Let the AI {'build' if building else 'pick'} for {name}"
            w = gui.button_width(label, "small")
            bx -= w
            self.button(gui, pygame.Rect(bx, bar.y + (h - self.bh) // 2, w, self.bh), label, f"ai_for_{seat}", True, fkey="small")
            bx -= int(8 * fs)
        draw_text(scr, clip_text(text, smallb, bx - x - int(8 * fs)), x, bar.centery, smallb, ORANGE, "midleft")
        return bar.bottom + int(8 * fs)

    def draw_online_decks(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        small, smallb, body = gui.font("small"), gui.font("small", True), gui.font("body", True)
        ready = self.stage == "ready"
        y = self.header(gui, rect, "Your deck" if not ready else "Your deck is ready",
                        "Auto-built from your picks: a commander, the best cards of its colours, your lands, then basic lands to "
                        "60. Edit my deck builds it by hand. Each of you builds your own; the game starts when all are ready.")
        foot = rect.bottom - pad - self.bh - int(10 * fs)
        col_w = min(int(rect.w * 0.5), int(560 * fs))
        col = pygame.Rect(rect.x + pad, y, col_w, foot - y)
        side = pygame.Rect(col.right + int(20 * fs), y, rect.right - pad - col.right - int(20 * fs), foot - y)
        deck = (self.decks or [None])[0]
        round_rect(scr, col, gfx.ROW_BG, 10, 2, GOLD)
        inner = col.inflate(-2 * int(10 * fs), -2 * int(10 * fs))
        prev = scr.get_clip()
        scr.set_clip(col)
        cy = inner.y
        self.hover = None
        if deck is None:
            for ln in wrap_text("No card in your picks can be a commander, so there is no deck yet: a Commander Draft deck "
                                "needs one (903.13). Edit my deck shows what you have.", small, inner.w)[:6]:
                draw_text(scr, ln, inner.x, cy, small, RED)
                cy += small.get_height()
        else:
            pic_h = max(0, min(int(inner.w / ds.CARD_ASPECT * 0.6), int((inner.bottom - cy) * 0.45)))
            pic_w = int(pic_h * ds.CARD_ASPECT)
            if pic_h >= 40:
                for k, i in enumerate(deck.commanders):
                    pr = pygame.Rect(inner.x + k * (pic_w + int(8 * fs)), cy, pic_w, pic_h)
                    scr.blit(ds.card_picture(gui, self.pod.cards[i], pr.w, pr.h), pr)
                    if pr.collidepoint(gui.mouse):
                        self.hover = i
                cy += pic_h + 6
            for text, colour, font in self.deck_lines(0, deck, {"small": small, "smallb": smallb}):
                if text.startswith("Colours: {"):
                    gfx.draw_rich(scr, text, inner.x, cy + font.get_height() // 2, font, colour)
                    cy += font.get_height() + 1
                    continue
                for ln in wrap_text(text, font, inner.w)[:3]:
                    draw_text(scr, ln, inner.x, cy, font, colour)
                    cy += font.get_height() + 1
            tiny = gui.font("tiny")
            for ln in self.card_list(deck):
                if cy + tiny.get_height() > inner.bottom:
                    break
                draw_text(scr, clip_text(ln, tiny, inner.w), inner.x, cy, tiny, DIM)
                cy += tiny.get_height()
        scr.set_clip(prev)
        # who is ready
        sy = side.y
        draw_text(scr, "Everyone's decks", side.x, sy, body, GOLD)
        sy += body.get_height() + 4
        for name, r in self.ready_names():
            draw_text(scr, clip_text(f"{name}: {'ready' if r else 'still building'}", small, side.w), side.x, sy, small,
                      GREEN if r else WHITE)
            sy += small.get_height() + 2
        if self.role == "host" and self.hd is not None:
            ai = [self.hd.name(s) for s in self.hd.ai_seats() if s != 0]
            if ai:
                draw_text(scr, clip_text("Auto-built by the AI: " + ", ".join(ai), small, side.w), side.x, sy, small, DIM)
                sy += small.get_height() + 2
        if ready:
            waiting = [n for n, r in self.ready_names() if not r and n != "You"]
            text = (READY_SAID.format(names=od.names_text(waiting), s="s" if len(waiting) != 1 else "") if waiting else ALL_READY)
            sy += int(10 * fs)
            for ln in wrap_text(text, small, side.w)[:4]:
                draw_text(scr, ln, side.x, sy, small, GOLD)
                sy += small.get_height()
        if self.role == "guest" and self.pod.deck_state not in (None, "sent", "ok"):
            self.message = (f"The host refused your deck: {self.pod.deck_state}", RED)
            self.pod.deck_state = None
            self.stage = "decks"
        if ready:
            top = self.footer(gui, rect, [(self.leave_label(), "leave", True, False)],
                              [("Not ready - change my deck", "unready", True, False)])
        else:
            top = self.footer(gui, rect, [("Back to the review", "to_review", True, False), (self.leave_label(), "leave", True, False)],
                              [("Ready", "ready", deck is not None, True), ("Edit my deck", "edit", True, False)])
        if self.message:
            self.draw_message(gui, rect.x + pad, top - smallb.get_height() - 6, rect.w - 2 * pad)
        if self.hover is not None:
            self.draw_big(gui, rect, self.hover)
