# SPDX-License-Identifier: GPL-3.0-or-later
"""Round MP2d: playing through a relay (relay/manticore_relay.py + relay_agent.py) when the host can't take incoming connections.

Fast tests need no Java: the relay server on 127.0.0.1 (in a thread), the host's agent bringing a guest through to a local
stand-in engine byte for byte, the refusals, the agent coming back after the relay restarts, invite codes with a room, the
Host dialog's relay option, the Host screen's line and invite code, and the Join dialog passing the room on. Live tests (skipped
without Java and forge_runtime/) run the real NetHost behind the relay: TLS through it with the fingerprint checked, the game
starting, and a dropped connection coming back through the relay.
"""
import asyncio
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import forge_client as fc
import forge_net as fn
import online_screens as onl
import relay_agent as ra
import tests.live as live
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import frame
from tests.test_mp1 import FakeHostSession, deck_file, free_port, press_button, wait_for

_spec = importlib.util.spec_from_file_location("manticore_relay", os.path.join(ROOT, "relay", "manticore_relay.py"))
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


class RelayThread:
    """The relay server running on its own event loop in a thread, on 127.0.0.1."""

    def __init__(self, port=0, max_rooms=200):
        self.loop = asyncio.new_event_loop()
        self.relay = mr.Relay(max_rooms)
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.port = asyncio.run_coroutine_threadsafe(self.relay.start("127.0.0.1", port), self.loop).result(5)

    def stop(self):
        if self.loop.is_closed():
            return

        async def shut():
            self.relay.server.close()
            for room in list(self.relay.rooms.values()):
                room.control.close()
            await asyncio.sleep(0.05)
        try:
            asyncio.run_coroutine_threadsafe(shut(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        if not self.loop.is_running():
            self.loop.close()


class EchoEngine:
    """A stand-in for NetHost on 127.0.0.1: echoes every byte back, upper-cased."""

    def __init__(self):
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self.connections = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _a = self.server.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._echo, args=(c,), daemon=True).start()

    @staticmethod
    def _echo(c):
        try:
            while True:
                d = c.recv(4096)
                if not d:
                    break
                c.sendall(d.upper())
        except OSError:
            pass
        finally:
            c.close()

    def stop(self):
        self.server.close()


def guest_through(port, room, timeout=5):
    raw = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        fn.relay_join(raw, room, timeout)
    except fn.NetRefused:
        raw.close()
        raise
    return raw


def recv_exactly(s, n, timeout=5):
    s.settimeout(timeout)
    out = b""
    while len(out) < n:
        d = s.recv(n - len(out))
        if not d:
            break
        out += d
    return out


# ---------------------------------------------------------------------------------------------------------------------
# 1. the relay and the agent
# ---------------------------------------------------------------------------------------------------------------------
class RelayTests(unittest.TestCase):
    def setUp(self):
        self.relay = RelayThread()
        self.addCleanup(self.relay.stop)
        self.engine = EchoEngine()
        self.addCleanup(self.engine.stop)

    def agent(self, **kw):
        a = ra.RelayAgent("127.0.0.1", self.relay.port, self.engine.port, **kw).start()
        self.addCleanup(a.close)
        self.assertTrue(wait_for(lambda: a.status == "ready", 5), a.status)
        return a

    def test_a_guest_reaches_the_engine_through_the_relay_byte_for_byte(self):
        a = self.agent()
        self.assertRegex(a.room, r"^[a-z0-9]{10}$")
        payload = bytes(range(256)) * 40                          # binary, like TLS records
        g = guest_through(self.relay.port, a.room)
        g.sendall(payload)
        self.assertEqual(recv_exactly(g, len(payload)), payload.upper())
        g2 = guest_through(self.relay.port, a.room)               # a second guest, at the same time
        g2.sendall(b"second")
        self.assertEqual(recv_exactly(g2, 6), b"SECOND")
        self.assertEqual(self.engine.connections, 2)
        self.assertTrue(wait_for(lambda: a.forwarded == 2, 3))
        g.close()
        g2.close()
        self.assertTrue(wait_for(lambda: a.active == 0, 5))

    def test_refusals(self):
        with self.assertRaises(fn.NetRefused) as cm:
            guest_through(self.relay.port, "noroom0000")
        self.assertEqual(cm.exception.reason, "relay_no_room")
        a = self.agent()
        thief = ra.RelayAgent("127.0.0.1", self.relay.port, self.engine.port, room=a.room).start()   # same room, other key
        self.addCleanup(thief.close)
        self.assertTrue(wait_for(lambda: thief.status == "failed", 5))
        self.assertIn("taken", thief.error)
        raw = socket.create_connection(("127.0.0.1", self.relay.port), timeout=5)
        ra.send_line(raw, {"c": "join", "v": 99, "room": a.room})
        self.assertEqual(ra.read_line(raw), {"t": "refused", "reason": "version"})
        raw.close()

    def test_nobody_answering_inside_the_room(self):
        a = self.agent()
        with mock.patch.object(a, "_forward", lambda gid: None):  # the host's game never takes the guest
            with mock.patch.object(mr, "PAIR_SECONDS", 1):
                with self.assertRaises(fn.NetRefused) as cm:
                    guest_through(self.relay.port, a.room)
        self.assertEqual(cm.exception.reason, "relay_no_answer")

    def test_the_agent_comes_back_after_the_relay_restarts(self):
        a = self.agent()
        room, port = a.room, self.relay.port
        self.relay.stop()
        self.assertTrue(wait_for(lambda: a.status in ("lost", "connecting"), 5), a.status)
        self.relay = RelayThread(port)                            # the same address again
        self.addCleanup(self.relay.stop)
        self.assertTrue(wait_for(lambda: a.status == "ready", 15), a.status)
        self.assertEqual(a.room, room)                            # the invite code stays good
        g = guest_through(port, room)
        g.sendall(b"hi")
        self.assertEqual(recv_exactly(g, 2), b"HI")
        g.close()

    def test_the_key_is_never_shown(self):
        a = self.agent()
        self.assertNotIn(a._key, repr(a))


class InviteTests(unittest.TestCase):
    def test_a_room_goes_through_the_invite_code(self):
        code = fn.make_invite("relay.example.com", 36900, "ab" * 32, "pw-1", room="k3x9q2m7pa")
        inv = fn.read_invite(code)
        self.assertEqual((inv["address"], inv["port"], inv["room"]), ("relay.example.com", 36900, "k3x9q2m7pa"))
        self.assertNotIn("room", fn.read_invite(fn.make_invite("203.0.113.7", 36800, "ab" * 32, "pw-1")))
        import base64
        body = json.dumps({"a": "relay.example.com", "p": 36900, "f": "ab" * 32, "w": "pw", "m": "x y"})
        bad = fn.INVITE_PREFIX + base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        with self.assertRaises(fn.InviteError):
            fn.read_invite(bad)

    def test_relay_messages(self):
        for reason in ("relay_no_room", "relay_no_answer", "relay_busy"):
            self.assertNotIn("{", fn.join_message(reason))
        self.assertIn("(9)", fn.join_message("relay_version", "9"))


# ---------------------------------------------------------------------------------------------------------------------
# 2. the table
# ---------------------------------------------------------------------------------------------------------------------
class TableTests(TempDecks):
    def test_host_dialog_relay_option(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "host_online")
        d = gui.modal
        d.fields["name"].set("Karl")
        frame(gui, 1)
        press_button(gui, d, "use_relay")
        self.assertTrue(d.use_relay)
        self.assertEqual(d.problem(), "Type the relay's address (address:port).")
        d.fields["relay"].set("relay.example.com:36901")
        self.assertIsNone(d.problem())
        with mock.patch.object(gui, "host_game", return_value=None) as hg:
            d.host(gui)
        self.assertEqual(hg.call_args.kwargs, {"relay": ("relay.example.com", 36901)})
        d.fields["relay"].set("relay.example.com")                # the default port
        self.assertEqual(d.relay_target(), ("relay.example.com", fn.DEFAULT_RELAY_PORT))

    def test_host_game_through_a_relay(self):
        gui = self.idle_gui()
        made = {}

        def fake_host(*a, **kw):
            made.update(kw)
            s = FakeHostSession(*a, **kw)
            s.relay = kw.get("relay")
            return s
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", side_effect=fake_host):
            self.assertIsNone(gui.host_game(gui.menu.mine, "Karl", 36800, "pw-123", True, relay=("relay.example.com", 36900)))
        self.assertEqual(made["relay"], ("relay.example.com", 36900))
        self.assertEqual(gui.online_prefs.get("relay"), "relay.example.com:36900")
        self.assertTrue(gui.online_prefs.get("use_relay"))
        # the Host screen: no invite until the room is open, then the relay's address and the room
        s = gui.session
        s.relay_status = lambda: ("connecting", None, None)
        s.handle({"t": "hosting", "port": 36800, "bind": "127.0.0.1"})
        frame(gui, 1)
        self.assertIsNone(gui.hosting_wait.invite())
        s.relay_status = lambda: ("ready", "k3x9q2m7pa", None)
        inv = fn.read_invite(gui.hosting_wait.invite())
        self.assertEqual((inv["address"], inv["port"], inv["room"], inv["password"]), ("relay.example.com", 36900, "k3x9q2m7pa",
                                                                                         "pw-123"))
        self.assertIn("Connected to the relay", onl.relay_line(("ready", "k3x9q2m7pa", None), s.relay)[0])
        self.assertIn("refused", onl.relay_line(("failed", None, "the relay refused this game (taken)"), s.relay)[0])

    def test_join_passes_the_room_on(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "join_online")
        d = gui.modal
        d.fields["code"].set(fn.make_invite("relay.example.com", 36900, "ab" * 32, "pw-12", room="k3x9q2m7pa"))
        d.fields["name"].set("Sam")
        got = {}

        def fake_make(entry, name, address, port, password, fp, by_hand=False, watch=False, room=None):
            got.update(address=address, port=port, room=room)
            return None, "stop"
        with mock.patch.object(gui, "make_guest_session", side_effect=fake_make):
            press_button(gui, d, "join")
        self.assertEqual(got, {"address": "relay.example.com", "port": 36900, "room": "k3x9q2m7pa"})

    def test_a_relayed_session_never_remembers_the_relay_as_the_host(self):
        gui = self.idle_gui()
        s = fc.NetSession("relay.example.com", 36900, "Sam", "pw", "Deck", "x", room="k3x9q2m7pa")
        before = dict(gui.online_prefs.get("known_hosts") or {})
        gui._note_known_host(s)
        self.assertEqual(dict(gui.online_prefs.get("known_hosts") or {}), before)
        self.assertIn("via relay", repr(s))


# ---------------------------------------------------------------------------------------------------------------------
# 3. live: the real NetHost behind the relay
# ---------------------------------------------------------------------------------------------------------------------
LIVE = live.live_enabled()


@unittest.skipUnless(LIVE, "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="mp2d_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        cls.host_deck = deck_file(cls.tmp, "typal_lathril.txt", "Lathril")
        cls.guest_deck = deck_file(cls.tmp, "spellslinger_veyran.txt", "Veyran")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_game_through_the_relay_and_a_drop_coming_back_through_it(self):
        relay = RelayThread()
        self.addCleanup(relay.stop)
        port = free_port()
        h = fc.HostSession(self.host_deck, "Karl", port, "pw-live-4", self.cert, os.path.join(self.tmp, "guest.dck"),
                           upnp=True, code="hostcode", seed=9, relay=("127.0.0.1", relay.port))
        self.assertEqual((h.bind, h.upnp_wanted), ("127.0.0.1", False))      # nothing listens on the internet side
        h.stderr_path = os.path.join(self.tmp, "host.log")
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None, 120, pump=[h]), "NetHost never got ready")
        self.assertTrue(wait_for(lambda: (h.relay_status() or ("",))[0] == "ready", 10, pump=[h]), h.relay_status())
        room = h.relay_status()[1]
        with self.assertRaises(fn.NetRefused) as cm:                          # the wrong PC behind the room: refused by TLS
            fc.NetSession("127.0.0.1", relay.port, "Eve", "pw-live-4", "x", "x", fingerprint="0" * 64, room=room).start()
        self.assertEqual(cm.exception.reason, "fingerprint")
        with open(self.guest_deck, encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", relay.port, "Sam", "pw-live-4", "Veyran", f.read(), fingerprint=self.cert.fingerprint,
                              code="guestcode", room=room)
        g.start()
        self.addCleanup(g.close)
        self.assertEqual(g.proc.sock.version(), "TLSv1.3")
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.state and g.state, 60, pump=[h, g]), "no game")
        self.assertEqual(sorted(p["name"] for p in g.state["players"]), ["Karl", "Sam"])
        g.proc.sock.shutdown(socket.SHUT_RDWR)                                # a drop: it comes back through the relay
        self.assertTrue(wait_for(lambda: g.reconnects == 1 and h.peer_backs == 1, 30, pump=[h, g]), "no reconnect")
        self.assertGreaterEqual(h.relay_agent.forwarded, 3)                   # Eve, Sam, Sam again
        self.assertFalse(h.game_over)


class FitTests(TempDecks):
    def test_the_host_dialog_with_every_option_fits_at_every_size(self):
        from tests.test_mp1 import SIZES
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.idle_gui(size=size, scale=scale)
                gui.menu.press(gui, "host_online")
                d = gui.modal
                d.guests, d.ai, d.use_relay = 2, 1, True
                d.message = ("Port 36800 is already in use on this PC. Close the other program, or choose another port.",
                             onl.RED)
                frame(gui, 1)
                r = d.rect
                self.assertTrue(gui.screen.get_rect().contains(r), r)
                rects = [(b, n) for b, n in d.buttons]
                for b, n in rects:
                    self.assertTrue(r.contains(b), (n, b, r))
                for i, (b1, n1) in enumerate(rects):
                    for b2, n2 in rects[i + 1:]:
                        self.assertFalse(b1.colliderect(b2), (n1, b1, n2, b2))
