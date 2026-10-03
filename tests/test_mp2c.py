# SPDX-License-Identifier: GPL-3.0-or-later
"""Round MP2c: online games of three or four - up to three friends, and AI players for the empty seats.

Fast tests need no Java: the host's arguments, the session's notes about the lobby and about one of several players leaving or
dropping, the guest's waiting line, the Host dialog's Friends / AI players, the host's waiting screen, the table's banner and
toasts, and the VS seats built from a snapshot. Live tests (skipped without Java and forge_runtime/) run the real NetHost: two
friends and the host (each sees only their own hand; one leaving doesn't end the game for the others), a friend and an AI
player, a friend leaving the lobby before the start (the seat is free again), and the host cancelling with a friend waiting.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_net as fn
import online_screens as onl
import tests.live as live
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import frame
from tests.test_mp1 import FakeHostSession, deck_file, free_port, press_button, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------------------------------------------------
# 1. the sessions
# ---------------------------------------------------------------------------------------------------------------------
class SessionTests(unittest.TestCase):
    def host(self, **kw):
        cert = fn.HostCert("x.p12", "sp", "ab" * 32)
        return fc.HostSession("me.dck", "Karl", 36800, "pw", cert, "guest.dck", upnp=False, **kw)

    def test_the_host_asks_for_guests_and_ai_seats(self):
        args = self.host(guests=2, ai_paths=["ai1.dck"])._bridge_args()
        self.assertEqual(args[args.index("--guests") + 1], "2")
        self.assertEqual(args[args.index("--opponent") + 1], "ai1.dck")
        plain = self.host()._bridge_args()
        self.assertNotIn("--guests", plain)                           # MP1's command line, unchanged
        self.assertNotIn("--opponent", plain)
        with self.assertRaises(ValueError):
            self.host(guests=3, ai_paths=["a.dck"])                    # five players

    def test_the_lobby_and_guests_joining_and_leaving_it(self):
        h = self.host(guests=2)
        h.handle({"t": "guest_joined", "name": "Sam", "code": "c1", "seat": 2, "joined": 1, "wanted": 2})
        h.handle({"t": "guest_joined", "name": "Ann", "code": "c2", "seat": 3, "joined": 2, "wanted": 2})
        self.assertEqual((h.peer_name, h.peer_code, h.guests_joined), ("Sam", "c1", ["Sam", "Ann"]))
        h.handle({"t": "guest_left_lobby", "name": "Sam", "seat": 2})
        self.assertEqual((h.peer_name, h.guests_joined), ("Ann", ["Ann"]))
        self.assertTrue(h.waiting())
        h.handle({"t": "lobby", "players": ["Karl", "Ann"], "joined": 1, "wanted": 2, "ai": 0})
        self.assertEqual(h.lobby["joined"], 1)

    def test_one_of_several_leaving_is_not_the_end(self):
        s = fc.ForgeSession("", [])
        s.handle({"t": "peer_dropped", "who": "guest", "name": "Sam", "seat": 2, "grace": 60})
        s.handle({"t": "peer_dropped", "who": "guest", "name": "Ann", "seat": 3, "grace": 60})
        s.handle({"t": "peer_back", "who": "guest", "name": "Sam", "seat": 2})
        self.assertEqual(s.peer_dropped["name"], "Ann")
        self.assertEqual(s.peer_back_name, "Sam")
        s.handle({"t": "peer_left", "who": "guest", "name": "Ann", "seat": 3, "expired": True, "players": 4})
        self.assertIsNone(s.peer_left)                                 # the others play on
        self.assertEqual([m["name"] for m in s.players_left], ["Ann"])
        self.assertIsNone(s.peer_dropped)
        s.handle({"t": "peer_left", "who": "guest", "name": "Sam", "seat": 2, "players": 2})
        self.assertEqual(s.peer_left["name"], "Sam")                   # a 1v1: the end, as in MP1
        s.handle({"t": "peer_left", "who": "host", "players": 4})
        self.assertEqual(s.peer_left["who"], "host")                   # the host leaving always ends it


class LobbyLineTests(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(onl.lobby_line({"players": ["Karl", "Sam"], "joined": 1, "wanted": 2, "ai": 1}, "Sam"),
                         "Karl and you are here (and 1 AI player) - waiting for 1 more player.")
        self.assertEqual(onl.lobby_line({"players": ["Karl"], "joined": 0, "wanted": 2}, "Ann"),
                         "Karl is here - waiting for 2 more players.")
        self.assertIn("starting the game", onl.lobby_line({"players": ["Karl", "Sam", "Ann"], "joined": 2, "wanted": 2}, "Ann"))


# ---------------------------------------------------------------------------------------------------------------------
# 2. the table
# ---------------------------------------------------------------------------------------------------------------------
def pod_state(n=3):
    """main1_start with more players: copies of the opponent with new ids and names."""
    from tests.forge_fake import load_state
    st = load_state("main1_start")
    opp = next(p for p in st["players"] if p["id"] != st["me"])
    for k in range(n - len(st["players"])):
        q = copy.deepcopy(opp)
        q["id"] = 900 + k
        q["name"] = "Ann" if k == 0 else "Bo"
        for z in q.get("zones", {}).values():
            for c in z:
                c["id"] = c["id"] + 10000 * (k + 1)
        q["commanders"] = [c + 10000 * (k + 1) for c in q.get("commanders", [])]
        st["players"].append(q)
    return st


class TableTests(TempDecks):
    def online_gui(self, online="host", state=None, **attrs):
        from tests.forge_fake import FakeSession
        gui = self.idle_gui()
        s = FakeSession(state or pod_state(3))
        s.online, s.peer_name = online, "Karl" if online == "guest" else "Sam"
        for k, v in attrs.items():
            setattr(s, k, v)
        gui.menu = None
        gui.session = s
        gui._hook_session(journal=False)
        gui.reset_game()
        frame(gui, 2)
        return gui, s

    def test_a_player_leaving_a_pod_is_a_toast_not_the_end(self):
        gui, s = self.online_gui("host")
        s.handle({"t": "peer_left", "who": "guest", "name": "Ann", "seat": 3, "left": True, "players": 3})
        frame(gui, 1)
        self.assertIsNone(gui.modal)
        self.assertIn("Ann left and is out of the game", gui.toast[0])
        gui2, s2 = self.online_gui("guest")
        s2.handle({"t": "peer_left", "who": "host", "name": "Karl", "players": 3})
        frame(gui2, 1)
        self.assertEqual(gui2.modal.title, "The host left")

    def test_two_dropped_at_once(self):
        gui, s = self.online_gui("host")
        s.handle({"t": "peer_dropped", "who": "guest", "name": "Sam", "seat": 2, "grace": 60})
        s.handle({"t": "peer_dropped", "who": "guest", "name": "Ann", "seat": 3, "grace": 60})
        frame(gui, 1)
        self.assertIn("Sam and Ann's connections dropped", gui.online_banner_text())
        s.handle({"t": "peer_back", "who": "guest", "name": "Ann", "seat": 3})
        frame(gui, 1)
        self.assertIn("Ann is back", gui.toast[0])
        self.assertIn("Sam's connection dropped", gui.online_banner_text())

    def test_vs_seats_come_from_the_snapshot(self):
        import flow_screens as flow
        gui, s = self.online_gui("guest")
        gui.current_deck_labels = ("My deck", [])
        gui.vs = flow.VsShow(gui.online_vs_seats([]))
        gui.track_online()
        labels = [seat[0] for seat in gui.vs.seats]
        self.assertEqual(len(labels), 3)
        self.assertEqual(labels[0], "You")
        self.assertIn("Ann", labels)
        self.assertEqual(gui.vs.seats[0][2], "My deck")

    def test_the_guest_waits_for_the_others(self):
        from tests.forge_fake import FakeSession
        gui = self.idle_gui()
        s = FakeSession(None)
        s.ready = False
        s.online, s.peer_name, s.name = "guest", "Karl", "Sam"
        s.lobby = {"t": "lobby", "players": ["Karl", "Sam"], "joined": 1, "wanted": 2, "ai": 0}
        gui.menu = None
        gui.session = s
        gui._hook_session(journal=False)
        gui.reset_game()
        drawn = []
        real = onl.lobby_line
        with mock.patch.object(onl, "lobby_line", side_effect=lambda *a: drawn.append(real(*a)) or drawn[-1]):
            frame(gui, 1)
        self.assertTrue(drawn and "waiting for 1 more player" in drawn[-1], drawn)

    def test_host_dialog_friends_and_ai_players(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "host_online")
        d = gui.modal
        self.assertIsInstance(d, onl.HostDialog)
        self.assertEqual((d.guests, d.ai), (1, 0))
        frame(gui, 1)
        self.assertNotIn("guests_minus", [n for _r, n in d.buttons])   # at least one friend
        press_button(gui, d, "guests_plus")
        press_button(gui, d, "ai_plus")
        self.assertEqual((d.guests, d.ai), (2, 1))
        frame(gui, 1)
        self.assertNotIn("ai_plus", [n for _r, n in d.buttons])        # four players at most
        press_button(gui, d, "guests_plus")
        self.assertEqual((d.guests, d.ai), (3, 0))                     # a third friend takes the AI's seat
        d.fields["name"].set("Karl")
        with mock.patch.object(gui, "host_game", return_value=None) as hg:
            d.host(gui)
        self.assertEqual(hg.call_args.kwargs, {"guests": 3, "ai": 0})

    def test_host_game_writes_the_ai_decks(self):
        gui = self.idle_gui()
        made = {}

        def fake_host(*a, **kw):
            made.update(kw)
            return FakeHostSession(*a, **kw)
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", side_effect=fake_host):
            err = gui.host_game(gui.menu.mine, "Karl", 36800, "pw-123", False, guests=1, ai=2)
        self.assertIsNone(err)
        self.assertEqual(made["guests"], 1)
        self.assertEqual(len(made["ai_paths"]), 2)
        for p in made["ai_paths"]:
            self.assertTrue(os.path.isfile(p), p)
        self.assertEqual(gui.online_prefs.get("ai"), 2)

    def test_host_wait_counts_the_friends(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "host_online")
        d = gui.modal
        d.fields["name"].set("Karl")
        d.guests = 2
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)

        def fake_host(*a, **kw):
            s = FakeHostSession(*a, **kw)
            s.guests_wanted = kw.get("guests", 1)
            return s
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", side_effect=fake_host):
            d.host(gui)
        s = gui.session
        s.handle({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})
        s.handle({"t": "guest_joined", "name": "Sam", "code": "x", "seat": 2, "joined": 1, "wanted": 2})
        texts = []
        real = onl.draw_text
        with mock.patch.object(onl, "draw_text", side_effect=lambda scr, t, *a, **k: texts.append(t) or real(scr, t, *a, **k)):
            frame(gui, 1)
        self.assertTrue(any("1 of 2 here" in t for t in texts), texts)
        self.assertTrue(any("Joined: Sam" in t for t in texts), texts)
        self.assertIsNotNone(gui.hosting_wait)                           # still waiting for the second friend


# ---------------------------------------------------------------------------------------------------------------------
# 3. live: the real NetHost
# ---------------------------------------------------------------------------------------------------------------------
LIVE = live.live_enabled()


@unittest.skipUnless(LIVE, "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="mp2c_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        cls.decks = {name: deck_file(cls.tmp, sample, name) for name, sample in (
            ("Lathril", "typal_lathril.txt"), ("Veyran", "spellslinger_veyran.txt"), ("Adeline", "go_wide_adeline.txt"),
            ("Teysa", "aristocrats_teysa.txt"))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def start_host(self, **kw):
        port = free_port()
        h = fc.HostSession(self.decks["Lathril"], "Karl", port, "pw-live-3", self.cert,
                           os.path.join(self.tmp, "guest_%d.dck" % port), upnp=False, bind="127.0.0.1", code="hostcode", seed=7,
                           **kw)
        h.stderr_path = os.path.join(self.tmp, "host_%d.log" % port)
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        return h, port

    def guest(self, port, name, deck):
        with open(self.decks[deck], encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, name, "pw-live-3", deck, f.read(), fingerprint=self.cert.fingerprint,
                              code="guestcode")
        g.start()
        self.addCleanup(g.close)
        return g

    @staticmethod
    def msg(s):
        return ((s.state or {}).get("prompt") or {}).get("message", "")

    def play_pregame(self, sessions, until, seconds=120):
        end = time.time() + seconds
        while time.time() < end:
            for s in sessions:
                s.poll()
                for r in list(s.requests):
                    s.answer(r, True if r["kind"] == "confirm" else list(range(max(r.get("min", 1), 1))))
                st = s.state or {}
                p = st.get("prompt") or {}
                ok = p.get("ok", {})
                if ok.get("enabled") and (s.is_mulligan_prompt() or ok.get("label") == "Play" or "Return 0" in p.get("message", "")):
                    s.ok()
                elif "who" in p.get("message", "").lower() and "start" in p.get("message", "").lower():
                    me = s.me()                                    # 3-4 players: click a player's panel, then OK
                    if me and not any(pl.get("highlight") for pl in st.get("players", [])):
                        s.click_player(me["id"])
                    elif ok.get("enabled"):
                        s.ok()
            if until():
                return True
            time.sleep(0.1)
        return False

    def test_two_friends_and_the_host_each_see_only_their_own_hand(self):
        h, port = self.start_host(guests=2)
        sam = self.guest(port, "Sam", "Veyran")
        self.assertTrue(wait_for(lambda: sam.lobby is not None and h.guests_joined == ["Sam"], 10, pump=[h, sam]))
        self.assertEqual(sam.lobby["wanted"], 2)
        self.assertFalse(h.ready)
        ann = self.guest(port, "Ann", "Adeline")
        self.assertEqual(ann.seat, 3)
        self.assertEqual(ann.players, 3)
        self.assertTrue(wait_for(lambda: h.ready and sam.ready and ann.ready and all(s.state for s in (h, sam, ann)), 60,
                                 pump=[h, sam, ann]), "the game didn't start for all three")
        with self.assertRaises(fn.NetRefused) as cm:                   # a fourth person: full
            self.guest(port, "Eve", "Teysa")
        self.assertEqual(cm.exception.reason, "full")
        everyone = [h, sam, ann]
        self.assertTrue(self.play_pregame(everyone, lambda: any(self.msg(s).startswith("Priority") for s in everyone)),
                        "the game never got past the opening hands")
        for s in everyone:
            st = s.state
            self.assertEqual(sorted(p["name"] for p in st["players"]), ["Ann", "Karl", "Sam"])
            for p in st["players"]:
                if p["id"] == st["me"]:
                    self.assertIn("hand", p["zones"])
                else:
                    for c in p["zones"].get("hand", []) or []:
                        self.assertTrue(c.get("hidden"), c)
        sam.close()                                                       # one leaves; the other two play on
        self.assertTrue(wait_for(lambda: h.players_left and ann.players_left, 10, pump=[h, ann]))
        self.assertIsNone(h.peer_left)
        self.assertIsNone(ann.peer_left)
        wait_for(lambda: False, 3.0, pump=[h, ann])
        self.assertFalse(h.game_over or ann.game_over)
        sam_player = next(p for p in h.state["players"] if p["name"] == "Sam")
        self.assertTrue(wait_for(lambda: next(p for p in h.state["players"] if p["name"] == "Sam").get("lost"), 20, pump=[h, ann]),
                        "Sam's player wasn't out of the game: %r" % sam_player)

    def test_a_friend_and_an_ai_player(self):
        h, port = self.start_host(guests=1, ai_paths=[self.decks["Teysa"]])
        g = self.guest(port, "Sam", "Veyran")
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.state and g.state, 60, pump=[h, g]))
        names = sorted(p["name"] for p in g.state["players"])
        self.assertEqual(len(names), 3)
        ai = next(p for p in g.state["players"] if p["name"] not in ("Karl", "Sam"))
        self.assertTrue(ai["ai"])
        self.assertTrue(ai["name"].startswith("AI 1"))

    def test_leaving_the_lobby_frees_the_seat(self):
        h, port = self.start_host(guests=2)
        sam = self.guest(port, "Sam", "Veyran")
        self.assertTrue(wait_for(lambda: h.guests_joined == ["Sam"], 10, pump=[h, sam]))
        sam.close()
        self.assertTrue(wait_for(lambda: h.guests_joined == [], 10, pump=[h]), "Sam's seat wasn't freed")
        ann = self.guest(port, "Ann", "Adeline")
        bo = self.guest(port, "Bo", "Teysa")
        self.assertEqual(sorted((ann.seat, bo.seat)), [2, 3])
        self.assertTrue(wait_for(lambda: h.ready and ann.ready and bo.ready, 60, pump=[h, ann, bo]))

    def test_cancelling_tells_the_friends_waiting(self):
        h, port = self.start_host(guests=2)
        sam = self.guest(port, "Sam", "Veyran")
        self.assertTrue(wait_for(lambda: h.guests_joined == ["Sam"], 10, pump=[h, sam]))
        h.cancel_host()
        self.assertTrue(wait_for(lambda: sam.peer_left is not None, 10, pump=[h, sam]))
        self.assertEqual(sam.peer_left.get("who"), "host")
        self.assertTrue(sam.peer_left.get("cancelled"))
        self.assertTrue(wait_for(lambda: h.hosting_cancelled, 10, pump=[h]))
