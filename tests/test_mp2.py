# SPDX-License-Identifier: GPL-3.0-or-later
"""Round MP2a: online games survive a dropped connection, and people can watch.

Fast tests need no Java: the protocol-2 lines, the guest's NetSession against tests/net_fake.py's SeatHost (heartbeats answered and
kept out of the game, reconnecting with the rejoin token, a question asked again after a reconnect shown once and answered once,
"leave" on close, the token never written anywhere, giving up), and the table (the banner, toasts, the dialogs, watching).
Live tests (skipped without Java and forge_runtime/) run the real NetHost and NetSession on 127.0.0.1: a drop in the middle of a
question and the automatic reconnect, the grace period running out, leaving on purpose, a silent connection dropped by the
heartbeat, and a spectator who sees no hidden card and can't act.
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_dialogs as dlg
import forge_net as fn
import online_screens as onl
import tests.live as live
from tests.net_fake import SeatHost, plain_connect
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import frame
from tests.test_mp1 import FakeHostSession, deck_file, free_port, press_button, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA_SRC = os.path.join(ROOT, "java_bridge", "src", "forge", "bridge")
TOKEN = "f00dfeed" * 4                     # a rejoin token that must never be written anywhere


# ---------------------------------------------------------------------------------------------------------------------
# 1. the lines
# ---------------------------------------------------------------------------------------------------------------------
class ProtocolTests(unittest.TestCase):
    def test_protocol_two_and_its_lines(self):
        self.assertEqual(fn.PROTOCOL_V, 2)
        self.assertEqual(json.loads(fn.hello_line("Sam", "pw", "c", "d", "x"))["v"], 2)
        self.assertEqual(json.loads(fn.rejoin_line(TOKEN)), {"c": "rejoin", "v": 2, "token": TOKEN})
        w = json.loads(fn.watch_line("Ann", "pw", "code"))
        self.assertEqual((w["c"], w["v"], w["name"], w["password"]), ("watch", 2, "Ann", "pw"))
        self.assertNotIn("deck", w)
        self.assertEqual([fn.reconnect_wait(n) for n in (1, 2, 3, 4, 5, 9)], [1, 2, 3, 5, 5, 5])

    def test_every_new_refusal_has_a_message(self):
        for reason in ("rejoin", "gone", "lost"):
            self.assertNotIn("{", fn.join_message(reason))
        self.assertEqual(fn.join_message("not_started", "The game hasn't started yet."), "The game hasn't started yet.")


# ---------------------------------------------------------------------------------------------------------------------
# 2. the guest's session against a stand-in host
# ---------------------------------------------------------------------------------------------------------------------
class SessionTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("_connect", staticmethod(plain_connect)), ("_sleep", staticmethod(lambda s: time.sleep(min(s, 0.02))))):
            p = mock.patch.object(fc.NetSession, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tempfile.mkdtemp(prefix="mp2_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.hosts = []

    def tearDown(self):
        for h in self.hosts:
            h.stop()

    def host(self, **kw):
        kw.setdefault("token", TOKEN)
        h = SeatHost(**kw).start()
        self.hosts.append(h)
        return h

    def session(self, h, watch=False, **kw):
        s = fc.NetSession("127.0.0.1", h.port, "Sam", "pw-12", "Veyran", "[metadata]\nName=Veyran\n", code="guestcode",
                          record_path=os.path.join(self.tmp, "rec.jsonl"), watch=watch, **kw)
        s.start()
        self.addCleanup(s.close)
        return s

    def test_the_welcome_gives_the_token_and_grace_but_never_shows_them(self):
        h = self.host(grace=45)
        s = self.session(h)
        self.assertEqual((s.grace, s._token, s.net_state), (45, TOKEN, "connected"))
        self.assertNotIn(TOKEN, repr(s))
        self.assertTrue(s.connected())

    def test_heartbeats_are_answered_and_kept_out_of_the_game(self):
        h = self.host(after=[{"t": "ready", "protocol": 2}])
        s = self.session(h)
        self.assertTrue(wait_for(lambda: s.ready, 3, pump=[s]))
        n0 = s.state_version
        for _ in range(3):
            h.send({"t": "hb"})
        self.assertTrue(wait_for(lambda: sum(1 for m in h.lines if m.get("c") == "hb") >= 3, 3))
        before = s.last_rx
        wait_for(lambda: False, 0.3, pump=[s])
        self.assertEqual(s.last_rx, before)                    # a heartbeat isn't the engine answering
        self.assertEqual(s.state_version, n0)
        s.close()
        with open(os.path.join(self.tmp, "rec.jsonl"), encoding="utf-8") as f:
            rec = f.read()
        self.assertNotIn('"hb"', rec)

    def test_a_drop_reconnects_with_the_token_and_a_repeated_question_shows_once(self):
        q = {"t": "request", "id": 7, "kind": "confirm", "title": "Keep going?"}
        h = self.host(after=[{"t": "ready", "protocol": 2}, q])
        s = self.session(h)
        self.assertTrue(wait_for(lambda: len(s.requests) == 1, 3, pump=[s]))
        h.hang_up()                                             # the connection drops; the question is still open
        self.assertTrue(wait_for(lambda: s.net_state == "reconnecting" or s.reconnects, 3, pump=[s]))
        self.assertTrue(wait_for(lambda: s.reconnects == 1 and s.net_state == "connected", 5, pump=[s]))
        self.assertEqual([f["c"] for f in h.firsts], ["hello", "rejoin"])
        self.assertEqual(h.firsts[1]["token"], TOKEN)
        wait_for(lambda: False, 0.3, pump=[s])                  # the host asks again (SeatHost's "after" repeats it)
        self.assertEqual([r["id"] for r in s.requests], [7])
        self.assertFalse(s.exited)
        s.answer(s.requests[0], True)
        self.assertTrue(wait_for(lambda: any(m.get("c") == "reply" for m in h.lines), 3))
        h.send(q)                                               # asked once more (the answer was lost): the same answer again
        self.assertTrue(wait_for(lambda: sum(1 for m in h.lines if m.get("c") == "reply") == 2, 3, pump=[s]))
        self.assertEqual([m["value"] for m in h.lines if m.get("c") == "reply"], [True, True])
        self.assertEqual(list(s.requests), [])
        s.close()
        with open(os.path.join(self.tmp, "rec.jsonl"), encoding="utf-8") as f:
            self.assertNotIn(TOKEN, f.read())

    def test_close_says_leave_first(self):
        h = self.host()
        s = self.session(h)
        s.close()
        self.assertTrue(wait_for(lambda: any(m.get("c") == "leave" for m in h.lines), 3))
        self.assertFalse(s.alive())

    def test_a_rejoin_refused_gives_up_at_once(self):
        h = self.host(after=[{"t": "ready", "protocol": 2}])
        s = self.session(h)
        h.refuse_rejoins = "gone"
        h.hang_up()
        self.assertTrue(wait_for(lambda: s.exited, 5, pump=[s]))
        self.assertEqual(s.net_state, "lost")
        self.assertEqual(s.net_lost["reason"], "gone")

    def test_nobody_answering_gives_up_after_the_grace_period(self):
        h = self.host(grace=1, after=[{"t": "ready", "protocol": 2}])
        s = self.session(h)
        h.accepting = False
        h.server.close()
        t0 = time.time()
        with mock.patch.object(fn, "RECONNECT_EXTRA", 0):
            h.hang_up()
            self.assertTrue(wait_for(lambda: s.exited, 10, pump=[s]))
        self.assertLess(time.time() - t0, 6)
        self.assertEqual(s.net_lost["reason"], "lost")

    def test_after_game_over_or_without_a_token_a_drop_just_ends(self):
        h = self.host(after=[{"t": "game_over"}])
        s = self.session(h)
        self.assertTrue(wait_for(lambda: s.game_over, 3, pump=[s]))
        h.hang_up()
        self.assertTrue(wait_for(lambda: s.exited, 3, pump=[s]))
        self.assertEqual(len(h.firsts), 1)
        self.assertIsNone(s.net_lost)

    def test_silence_counts_as_a_drop(self):
        h = self.host(after=[{"t": "ready", "protocol": 2}])
        s = self.session(h, silence_seconds=1)
        self.assertTrue(wait_for(lambda: s.reconnects >= 1, 6, pump=[s]))   # nothing for 1 s: dropped, then back
        self.assertGreaterEqual(s.net_drops, 1)

    def test_a_spectator_says_watch_and_can_only_ask_for_a_snapshot(self):
        h = self.host()
        s = self.session(h, watch=True)
        self.assertEqual(h.firsts[0]["c"], "watch")
        self.assertNotIn("deck", h.firsts[0])
        self.assertTrue(s.spectator)
        self.assertEqual(s.watched_guest, "Sam")
        self.assertFalse(s.ok())
        self.assertFalse(s.click_card(5))
        self.assertTrue(s.send(c="flush"))
        self.assertTrue(wait_for(lambda: [m["c"] for m in h.lines] == ["flush"], 3))

    def test_a_spectator_does_not_reconnect(self):
        h = self.host()
        s = self.session(h, watch=True)
        h.hang_up()
        self.assertTrue(wait_for(lambda: s.exited, 3, pump=[s]))
        self.assertEqual(len(h.firsts), 1)


class SoakToolTests(unittest.TestCase):
    def test_drops_show_in_the_game_line_and_the_summary(self):
        from tools import online_soak as osk
        r = osk.OnlineResult(3, 9, ["a.txt", "b.txt"])
        r.ended, r.drops, r.reconnected = "game_over", 2, 1
        self.assertIn("drops=1/2", r.line())
        self.assertIn("connections cut (--drops): 2, came back by themselves: 1", osk.summary_text([r], "now", None, False))
        r.drops = 0
        self.assertNotIn("drops=", r.line())


class SessionNoteTests(unittest.TestCase):
    """ForgeSession's own handling of the new messages (no network)."""

    def test_drops_backs_and_spectators(self):
        s = fc.ForgeSession("", [])
        s.handle({"t": "peer_dropped", "who": "guest", "name": "Sam", "grace": 60})
        self.assertEqual(s.peer_dropped["name"], "Sam")
        s.handle({"t": "peer_back", "who": "guest"})
        self.assertIsNone(s.peer_dropped)
        self.assertEqual((s.peer_drops, s.peer_backs), (1, 1))
        s.handle({"t": "spectator_joined", "name": "Ann"})
        s.handle({"t": "spectator_joined", "name": "Bo"})
        s.handle({"t": "spectator_left", "name": "Ann"})
        self.assertEqual(s.spectators, ["Bo"])

    def test_a_request_seen_twice_is_kept_once(self):
        s = fc.ForgeSession("", [])
        s.handle({"t": "request", "id": 3, "kind": "confirm"})
        s.handle({"t": "request", "id": 3, "kind": "confirm"})
        s.handle({"t": "request", "id": 4, "kind": "confirm"})
        self.assertEqual([r["id"] for r in s.requests], [3, 4])

    def test_only_the_newest_answers_are_kept(self):
        s = fc.ForgeSession("", [])
        for i in range(fc.REPLIES_KEPT + 10):
            s.answer({"id": i}, i)
        self.assertEqual(len(s._replied), fc.REPLIES_KEPT)
        self.assertNotIn(0, s._replied)
        self.assertIn(fc.REPLIES_KEPT + 9, s._replied)


# ---------------------------------------------------------------------------------------------------------------------
# 3. the table
# ---------------------------------------------------------------------------------------------------------------------
class TableTests(TempDecks):
    def online_gui(self, online="guest", **attrs):
        from tests.forge_fake import FakeSession, load_state
        gui = self.idle_gui()
        s = FakeSession(load_state("main1_start"))
        s.online, s.peer_name = online, "Karl" if online == "guest" else "Sam"
        for k, v in attrs.items():
            setattr(s, k, v)
        gui.menu = None
        gui.session = s
        gui._hook_session(journal=False)
        gui.reset_game()
        frame(gui, 2)
        return gui, s

    def test_the_guest_reconnecting_shows_a_countdown_then_a_note(self):
        gui, s = self.online_gui("guest", grace=60, reconnects=0)
        s.handle({"t": "net_dropped", "why": "x", "grace": 60})
        frame(gui, 1)
        text = gui.online_banner_text()
        self.assertIn("Connection to Karl lost", text)
        self.assertIn("60 s left", text)
        self.assertFalse(gui.not_responding(time.monotonic() + 30))
        self.assertIsNone(gui.modal)                            # a drop is not the end
        gui.drop_noticed = (gui.drop_noticed[0] - 20, 60)
        self.assertIn("40 s left", gui.online_banner_text())
        s.reconnects = 1
        s.handle({"t": "net_back", "attempts": 2})
        frame(gui, 1)
        self.assertIsNone(gui.online_banner_text())
        self.assertIn("Reconnected", gui.toast[0])

    def test_the_host_sees_the_guest_drop_and_come_back(self):
        gui, s = self.online_gui("host")
        s.handle({"t": "peer_dropped", "who": "guest", "name": "Sam", "why": "nothing heard for 20 s", "grace": 60})
        frame(gui, 1)
        self.assertIn("Sam's connection dropped", gui.online_banner_text())
        self.assertIsNone(gui.modal)
        s.handle({"t": "peer_back", "who": "guest", "name": "Sam"})
        frame(gui, 1)
        self.assertIsNone(gui.online_banner_text())
        self.assertIn("Sam is back", gui.toast[0])

    def test_the_dialogs_say_what_happened(self):
        for left, title, words in (({"who": "guest", "expired": True, "grace": 60}, "Your friend didn't come back", "60 seconds"),
                                   ({"who": "guest", "left": True}, "Your friend left", "left the game"),
                                   ({"who": "guest"}, "Your friend left", "connection closed")):
            with self.subTest(left=left):
                gui, s = self.online_gui("host")
                s.handle(dict(left, t="peer_left"))
                frame(gui, 1)
                self.assertEqual(gui.modal.title, title)
                self.assertIn(words, gui.modal.text if hasattr(gui.modal, "text") else str(vars(gui.modal)))
        gui, s = self.online_gui("guest")
        s.handle({"t": "net_lost", "reason": "lost", "text": fn.join_message("lost")})
        s.handle({"t": "_exit"})
        frame(gui, 2)
        self.assertEqual(gui.modal.title, "Connection lost")

    def test_spectators_coming_and_going(self):
        gui, s = self.online_gui("host")
        s.handle({"t": "spectator_joined", "name": "Ann"})
        frame(gui, 1)
        self.assertIn("Ann is watching", gui.toast[0])
        s.handle({"t": "spectator_left", "name": "Ann"})
        frame(gui, 1)
        self.assertIn("Ann stopped watching", gui.toast[0])

    def test_watching_labels_and_the_end(self):
        import flow_screens as flow
        gui, s = self.online_gui("guest", spectator=True, watched_guest="Sam")
        me = s.me()
        self.assertNotEqual(gui.player_label(me["id"]), "You")
        self.assertEqual([seat[0] for seat in gui.online_vs_seats([])], ["Karl", "Sam"])
        s.state = dict(s.state, gameOver=True, winner=me["name"])
        s.game_over = True
        end = flow.end_screen_for(gui)
        self.assertEqual(end.kind, "over")
        self.assertEqual(flow.WORDS[end.kind], "GAME OVER")
        gui.end_continue()
        self.assertEqual(gui.modal.heading, "Game over")

    def test_join_dialog_watch_needs_no_deck(self):
        gui = self.idle_gui()
        made = {}

        def fake_make(entry, name, address, port, password, fp, by_hand=False, watch=False):
            made.update(watch=watch, name=name)
            return None, "stop here"
        with mock.patch.object(gui, "_online_deck_problem", return_value="Your deck (X): 3 cards are banned"):
            gui.menu.press(gui, "join_online")
        d = gui.modal
        self.assertIsInstance(d, onl.JoinDialog)                # a deck problem no longer keeps the dialog shut
        d.fields["code"].set(fn.make_invite("127.0.0.1", 36800, "ab" * 32, "pw-12"))
        d.fields["name"].set("Ann")
        with mock.patch.object(gui, "make_guest_session", side_effect=fake_make):
            frame(gui, 1)
            self.assertNotIn("join", [n for _r, n in d.buttons])   # greyed out
            self.assertIn("watch", [n for _r, n in d.buttons])
            d.join(gui)
            self.assertIn("banned", d.message[0])
            press_button(gui, d, "watch")
        self.assertEqual(made, {"watch": True, "name": "Ann"})

    def test_the_host_can_copy_the_invite_again_for_someone_to_watch(self):
        import pygame
        from tests.test_forge_table import key
        from tests.test_mp1 import TableTests as MP1Tables
        gui = MP1Tables.hosting_gui(self)
        s = gui.session
        s.handle({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})
        s.handle({"t": "upnp", "ok": True, "external_ip": "203.0.113.7"})
        frame(gui, 1)
        code = gui.hosting_wait.invite()
        s.handle({"t": "guest_joined", "name": "Sam", "code": "x"})
        s.handle({"t": "ready", "protocol": 2, "seats": 2, "online": True})
        frame(gui, 2)
        self.assertEqual(gui.invite_code, code)
        with mock.patch.object(gui, "write_clipboard") as wc:
            key(gui, pygame.K_i, pygame.KMOD_CTRL)
        wc.assert_called_once_with(code)
        gui.leave_online_game()
        self.assertIsNone(gui.invite_code)

    def test_a_watch_session_has_no_deck(self):
        gui = self.idle_gui()
        s, err = gui.make_guest_session(None, "Ann", "127.0.0.1", 36800, "pw", "ab" * 32, watch=True)
        self.assertIsNone(err)
        self.assertTrue(s.spectator)
        self.assertEqual(s.dck_text, "")


# ---------------------------------------------------------------------------------------------------------------------
# 4. the bridge's source
# ---------------------------------------------------------------------------------------------------------------------
class SourceTests(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(JAVA_SRC, name), encoding="utf-8") as f:
            return f.read()

    def test_the_token_is_never_logged(self):
        for name in ("NetHost.java", "SeatWire.java", "RemoteWire.java"):
            for line in self.read(name).splitlines():
                if "System.err.println" in line:
                    self.assertNotIn("token", line.lower().replace("tokens", ""), "%s: %s" % (name, line.strip()))

    def test_spectators_see_no_hidden_zone(self):
        src = self.read("Snapshot.java")
        self.assertIn("shownToSpectator", src)
        self.assertIn("isHidden()", src)
        self.assertIn("isFaceDown()", src)


# ---------------------------------------------------------------------------------------------------------------------
# 5. live: the real NetHost
# ---------------------------------------------------------------------------------------------------------------------
LIVE = live.live_enabled()


@unittest.skipUnless(LIVE, "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="mp2_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        cls.host_deck = deck_file(cls.tmp, "typal_lathril.txt", "Lathril")
        cls.guest_deck = deck_file(cls.tmp, "spellslinger_veyran.txt", "Veyran")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def start_host(self, dev=False, seed=5, **kw):
        port = free_port()
        h = fc.HostSession(self.host_deck, "Karl", port, "pw-live-2", self.cert, os.path.join(self.tmp, "guest_%d.dck" % port),
                           upnp=False, bind="127.0.0.1", code="hostcode", dev=dev, seed=seed, **kw)
        h.stderr_path = os.path.join(self.tmp, "host_%d.log" % port)
        h.record_path = os.path.join(self.tmp, "host_%d.jsonl" % port)
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        return h, port

    def guest(self, port, watch=False, name="Sam", password="pw-live-2", **kw):
        with open(self.guest_deck, encoding="utf-8") as f:
            dck = f.read()
        g = fc.NetSession("127.0.0.1", port, name, password, "Veyran", "" if watch else dck, fingerprint=self.cert.fingerprint,
                          code="guestcode", watch=watch, record_path=os.path.join(self.tmp, "%s_%d.jsonl" % (name, port)), **kw)
        g.start()
        self.addCleanup(g.close)
        return g

    @staticmethod
    def msg(s):
        return ((s.state or {}).get("prompt") or {}).get("message", "")

    def play_pregame(self, sessions, until, seconds=90):
        end = time.time() + seconds
        while time.time() < end:
            for s in sessions:
                s.poll()
                for r in list(s.requests):
                    s.answer(r, True if r["kind"] == "confirm" else list(range(max(r.get("min", 1), 1))))
                p = (s.state or {}).get("prompt") or {}
                if p.get("ok", {}).get("enabled") and (s.is_mulligan_prompt() or p.get("ok", {}).get("label") == "Play"
                                                       or "Return 0" in p.get("message", "")):
                    s.ok()
            if until():
                return True
            time.sleep(0.1)
        return False

    def started(self, h, g):
        self.assertTrue(self.play_pregame([h, g], lambda: self.msg(h).startswith("Priority") or self.msg(g).startswith("Priority")),
                        "the game never started")

    def fact_or_fiction(self, h, g):
        """The host casts Fact or Fiction at the guest, who is then asked to split the piles (a request)."""
        h.setup(["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Fact or Fiction", "humanbattlefield=Island;Island;Island;Island",
                 "humanlibrary=Island;Forest;Mountain;Plains;Swamp;Island;Island",
                 "aihand=Forest;Island;Mountain", "ailibrary=Forest;Forest;Forest", "aibattlefield=", "removesummoningsickness=true"])
        self.assertTrue(wait_for(lambda: h.setups_done >= 1, 30, pump=[h, g]))
        end, cast = time.time() + 60, False
        while time.time() < end:
            h.poll()
            g.poll()
            if g.requests:
                return g.requests[0]
            mh = self.msg(h)
            hand = [c for c in h.me()["zones"]["hand"] if c["name"] == "Fact or Fiction"]
            if not cast and mh.startswith("Priority") and "Main phase" in mh and hand and not h.state.get("stack"):
                h.click_card(hand[0]["id"])
                cast = True
            elif cast and ("Pay Mana" in mh or ((h.state or {}).get("prompt") or {}).get("selecting")):
                lands = [c for c in h.me()["zones"]["battlefield"] if c["name"] == "Island" and not c.get("tapped")]
                if lands:
                    h.click_card(lands[0]["id"])
            elif mh.startswith("Priority") and (h.state.get("stack") or not cast):
                h.ok()
            elif self.msg(g).startswith("Priority"):
                g.ok()
            time.sleep(0.3)
        self.fail("the guest was never asked to split Fact or Fiction's piles")

    def test_a_drop_mid_question_reconnects_and_the_game_goes_on(self):
        h, port = self.start_host(dev=True)
        g = self.guest(port)
        self.started(h, g)
        req = self.fact_or_fiction(h, g)
        forge_net_sock = g.proc.sock
        forge_net_sock.shutdown(socket.SHUT_RDWR)                     # the guest's Wi-Fi goes for a moment
        # (the host may see the new connection before the old one's end: then there is a peer_back without a peer_dropped)
        self.assertTrue(wait_for(lambda: g.reconnects == 1 and h.peer_backs == 1, 20, pump=[h, g]), "no reconnect")
        self.assertIsNone(h.peer_dropped)
        self.assertTrue(wait_for(lambda: g.state_version > 0, 5, pump=[h, g]))
        wait_for(lambda: False, 1.0, pump=[h, g])
        self.assertEqual([r["id"] for r in g.requests], [req["id"]])  # asked again, shown once
        r = g.requests[0]
        g.answer(r, list(range(max(r.get("min", 1), 1))))
        end = time.time() + 60
        while time.time() < end and not any(c["name"] in ("Forest", "Mountain", "Plains", "Swamp")
                                            for c in h.me()["zones"]["hand"]):
            h.poll()
            g.poll()
            for rq in list(h.requests):
                h.answer(rq, True if rq["kind"] == "confirm" else [0])
            time.sleep(0.1)
        self.assertTrue(any(c["name"] in ("Forest", "Mountain", "Plains", "Swamp", "Island") for c in h.me()["zones"]["hand"]),
                        "Fact or Fiction never resolved after the reconnect")
        self.assertFalse(h.game_over)
        self.assertIsNone(h.peer_left)
        with open(h.stderr_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
        self.assertIn("reconnected", log)
        self.assertNotIn(g._token, log)
        for path in (h.record_path, g.record_path):
            g.poll()
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(g._token, f.read())

    def test_no_reconnect_within_the_grace_period_ends_the_game(self):
        h, port = self.start_host(grace_seconds=3)
        g = self.guest(port)
        self.started(h, g)
        g._token = None                                               # this table can't come back
        g.proc.sock.shutdown(socket.SHUT_RDWR)
        t0 = time.time()
        self.assertTrue(wait_for(lambda: h.peer_dropped is not None, 10, pump=[h]))
        self.assertTrue(wait_for(lambda: h.peer_left is not None, 15, pump=[h]), "the seat was never given up")
        self.assertGreater(time.time() - t0, 2.0)
        self.assertTrue(h.peer_left.get("expired"))
        self.assertTrue(wait_for(lambda: h.game_over or (h.state or {}).get("gameOver"), 20, pump=[h]))
        self.assertEqual((h.state or {}).get("winner"), "Karl")

    def test_leaving_ends_the_game_at_once(self):
        h, port = self.start_host()                                   # the default 60 s grace period
        g = self.guest(port)
        self.started(h, g)
        t0 = time.time()
        g.close()
        self.assertTrue(wait_for(lambda: h.peer_left is not None, 5, pump=[h]), "no peer_left within 5 s")
        self.assertLess(time.time() - t0, 5.5)
        self.assertTrue(h.peer_left.get("left"))
        self.assertIsNone(h.peer_dropped)
        self.assertTrue(wait_for(lambda: h.game_over or (h.state or {}).get("gameOver"), 20, pump=[h]))

    def test_a_silent_guest_is_dropped_by_the_heartbeat(self):
        h, port = self.start_host(heartbeat_seconds=1, silence_seconds=3)
        g = self.guest(port)
        self.started(h, g)
        g._write_line = lambda *a, **k: False                         # the guest's table freezes: no heartbeat answers
        g._token = None
        self.assertTrue(wait_for(lambda: h.peer_dropped is not None, 15, pump=[h, g]), "a silent guest was never dropped")
        self.assertIn("nothing heard", h.peer_dropped.get("why", ""))

    def test_a_spectator_sees_no_hidden_card_and_cant_act(self):
        h, port = self.start_host()
        with self.assertRaises(fn.NetRefused) as cm:                  # nothing to watch yet
            self.guest(port, watch=True, name="Ann")
        self.assertEqual(cm.exception.reason, "not_started")
        g = self.guest(port)
        self.started(h, g)
        with self.assertRaises(fn.NetRefused) as cm:
            self.guest(port, watch=True, name="Eve", password="nope")
        self.assertEqual(cm.exception.reason, "password")
        w = self.guest(port, watch=True, name="Ann")
        self.assertTrue(wait_for(lambda: w.state is not None and w.ready, 30, pump=[h, g, w]), "the spectator got no snapshot")
        self.assertTrue(wait_for(lambda: "Ann" in h.spectators and "Ann" in g.spectators, 10, pump=[h, g, w]))
        self.assertTrue(w.state.get("spectator"))
        self.assertEqual(w.watched_guest, "Sam")
        names = sorted(p["name"] for p in w.state["players"])
        self.assertEqual(names, ["Karl", "Sam"])
        for p in w.state["players"]:
            self.assertNotIn("hand", p["zones"])
            self.assertGreater(p["handCount"], 0)
            for zone, cards in p["zones"].items():
                for c in cards:
                    self.assertNotIn(c.get("zone"), ("Hand", "Library"), c)
        hands = {c["name"] for p in (h.me(), g.me()) for c in p["zones"].get("hand", [])}
        seen = json.dumps(w.state) + json.dumps([e for e, _t in w.events]) + json.dumps(w.log)
        for name in hands:
            on_board = any(c.get("name") == name for p in w.state["players"] for z in p["zones"].values() for c in z)
            if not on_board:
                self.assertNotIn('"%s"' % name, seen)
        self.assertFalse(w.ok())
        w.proc.stdin.write('{"c":"ok"}\n')                             # past the table's own guard: the host refuses it too
        w.proc.stdin.flush()
        self.assertTrue(wait_for(lambda: w.refused_cmds, 5, pump=[h, g, w]))
        w.close()
        self.assertTrue(wait_for(lambda: "Ann" not in h.spectators, 10, pump=[h, g]))
        self.assertTrue(h.alive() and not h.game_over)
