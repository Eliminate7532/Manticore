# SPDX-License-Identifier: GPL-3.0-or-later
"""Round MP1: playing a friend online (1v1, the host's PC runs Forge). See claude/SONNET_SPEC_MP1_2026-09-25.md, section 7.

Fast tests need no Java: passwords, addresses, invite codes, the handshake against tests/net_fake.py, the panels and the table's
online states. Live tests (skipped without Java and forge_runtime/) run the real NetHost and NetSession on 127.0.0.1: TLS and the
fingerprint, hidden information (the game log included), refused commands, wrong passwords, a guest who leaves mid-question,
three turns played by both tables, and the whole flow through two real ForgeTables.
"""
import io
import json
import os
import re
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import crashlog
import forge_client as fc
import forge_dialogs as dlg
import forge_net as fn
import online_screens as onl
import reporting
import tests.live as live
from tests.net_fake import FakeHost, plain_connect
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import click, frame, key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA_SRC = os.path.join(ROOT, "java_bridge", "src", "forge", "bridge")
SIZES = (((900, 600), 1.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.5), ((4096, 1949), 1.75))
MARKER = "zebra-quill-tundra-77"            # a password that must never be written anywhere


def wait_for(fn_, seconds, step=0.05, pump=()):
    end = time.time() + seconds
    while time.time() < end:
        for s in pump:
            s.poll()
        if fn_():
            return True
        time.sleep(step)
    return bool(fn_())


def press_button(gui, owner, name):
    """Draw a frame (a dialog registers its buttons while drawing), then click the button called `name`."""
    frame(gui, 1)
    target = owner() if callable(owner) else owner
    click(gui, next(r.center for r, n in target.buttons if n == name))


# ---------------------------------------------------------------------------------------------------------------------
# 1. passwords, names, addresses, ports
# ---------------------------------------------------------------------------------------------------------------------
class BasicsTests(unittest.TestCase):
    def test_passwords_are_three_words_and_two_digits_and_never_repeat(self):
        seen = set()
        for _ in range(1000):
            p = fn.make_password()
            self.assertRegex(p, r"^[a-z]+-[a-z]+-[a-z]+-\d\d$")
            self.assertTrue(all(w in fn.WORDS for w in p.split("-")[:3]))
            seen.add(p)
        self.assertEqual(len(seen), 1000)

    def test_addresses(self):
        for good in ("100.64.1.2", "my-pc.tailnet.ts.net", "192.168.1.5", "203.0.113.7", "localhost", "::1", "[2001:db8::1]"):
            self.assertIsNone(fn.validate_address(good), good)
        for bad in ("", "   ", "my pc", "http://1.2.3.4", "1.2.3", "a/b", "-bad-.com"):
            self.assertIsNotNone(fn.validate_address(bad), bad)

    def test_ports(self):
        self.assertEqual(fn.validate_port("36800"), (36800, None))
        for bad in ("", "abc", "80", "70000"):
            self.assertIsNone(fn.validate_port(bad)[0], bad)

    def test_names_are_cleaned(self):
        self.assertEqual(fn.clean_name("  Sam\t the\nBold  "), "Sam the Bold")
        self.assertEqual(fn.clean_name("<b>Sam</b>"), "bSam/b")
        self.assertEqual(len(fn.clean_name("x" * 100)), fn.NAME_MAX)

    def test_carrier_grade_and_private_addresses_are_behind_another_nat(self):
        for ip in ("100.64.0.1", "100.127.255.254", "10.0.0.8", "172.16.4.4", "192.168.0.1", "169.254.1.1"):
            self.assertTrue(fn.behind_another_nat(ip), ip)
        for ip in ("203.0.113.7", "100.63.0.1", "100.128.0.1", "8.8.8.8", "not an ip"):
            self.assertFalse(fn.behind_another_nat(ip), ip)

    def test_the_join_screen_messages_are_plain_words(self):
        """The section 5.2 table, word for word where the spec gives the words."""
        self.assertEqual(fn.join_message("refused_connection"),
                         "Nobody is hosting at that address and port. Check both, and that your friend pressed Host.")
        self.assertIn("No answer from that address", fn.join_message("no_answer"))
        self.assertEqual(fn.join_message("fingerprint"),
                         "This isn't your friend's PC, or their game was reinstalled. Ask for a new invite code.")
        self.assertEqual(fn.join_message("password"), "Wrong password.")
        self.assertEqual(fn.join_message("full"), "That game already has two players.")
        self.assertEqual(fn.join_message("deck", "no commander"), "The host could not use your deck: no commander")
        for reason in fn.JOIN_MESSAGES:
            self.assertNotIn("Traceback", fn.join_message(reason, "x"))

    def test_upnp_status_lines(self):
        self.assertEqual(fn.upnp_line({"t": "upnp", "ok": True, "external_ip": "203.0.113.7"}, 36800)[0],
                         "Router port opened automatically.")
        self.assertIn("Open port 36800 on your router", fn.upnp_line({"ok": False, "reason": "no_router"}, 36800)[0])
        self.assertIn("CGNAT", fn.upnp_line({"ok": False, "reason": "cgnat", "external_ip": "100.70.0.1"}, 36800)[0])
        self.assertEqual(fn.upnp_line({"ok": False, "reason": "cgnat"}, 1)[1], "red")
        self.assertIn("36801", fn.upnp_line({"ok": False, "reason": "refused"}, 36801)[0])
        self.assertIn("Asking your router", fn.upnp_line("waiting", 36800)[0])
        self.assertIn("off", fn.upnp_line("off", 36800)[0])
        self.assertIn("by hand", fn.upnp_line({"ok": False, "reason": "something new"}, 36800)[0])


# ---------------------------------------------------------------------------------------------------------------------
# 2. invite codes
# ---------------------------------------------------------------------------------------------------------------------
class InviteTests(unittest.TestCase):
    FP = "ab" * 32

    def test_round_trip(self):
        code = fn.make_invite("203.0.113.7", 36800, self.FP, "otter-lamp-river-42")
        self.assertTrue(code.startswith("MANT1-"))
        self.assertNotIn("=", code)
        self.assertEqual(fn.read_invite(code), {"address": "203.0.113.7", "port": 36800, "fingerprint": self.FP,
                                                "password": "otter-lamp-river-42"})

    def test_chat_line_breaks_and_spaces_dont_matter(self):
        code = fn.make_invite("my-pc.tailnet.ts.net", 36800, self.FP, "pw-11")
        broken = "Here you go: " + code[:20] + "\n" + code[20:50] + " " + code[50:]
        self.assertEqual(fn.read_invite(broken)["address"], "my-pc.tailnet.ts.net")

    def test_damaged_codes_give_a_plain_error(self):
        code = fn.make_invite("203.0.113.7", 36800, self.FP, "pw")
        for bad, words in (("", "Paste"), ("hello", "isn't an invite code"), (code[:-12], "damaged"),
                           ("MANT1-%%%%", "damaged"),
                           (fn.make_invite("203.0.113.7", 36800, "xyz", "pw"), "fingerprint"),
                           (fn.make_invite("203.0.113.7", 80, self.FP, "pw"), "port"),
                           (fn.make_invite("", 36800, self.FP, "pw"), "address"),
                           (fn.make_invite("203.0.113.7", 36800, self.FP, ""), "password")):
            with self.subTest(bad=bad[:30]):
                with self.assertRaises(fn.InviteError) as cm:
                    fn.read_invite(bad)
                self.assertIn(words, str(cm.exception))


# ---------------------------------------------------------------------------------------------------------------------
# 3. the handshake, against a stand-in host
# ---------------------------------------------------------------------------------------------------------------------
class HandshakeTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(fc.NetSession, "_connect", staticmethod(plain_connect))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hosts = []

    def tearDown(self):
        for h in self.hosts:
            h.stop()

    def host(self, mode, **kw):
        h = FakeHost(mode, **kw).start()
        self.hosts.append(h)
        return h

    def session(self, port, password="pw-12", **kw):
        return fc.NetSession("127.0.0.1", port, "Sam", password, "Veyran", "[metadata]\nName=Veyran\n", code="guestcode", **kw)

    def test_a_welcome_makes_a_ready_session(self):
        h = self.host("welcome", after=[{"t": "ready", "protocol": 2, "seats": 2, "online": True}])
        s = self.session(h.port).start()
        try:
            self.assertEqual((s.peer_name, s.peer_code, s.seat), ("Karl", "hostcode", 2))
            self.assertTrue(wait_for(lambda: s.ready, 3, pump=[s]))
            self.assertEqual(s.protocol, 2)
            hello = h.hellos[0]
            self.assertEqual((hello["c"], hello["v"], hello["name"], hello["password"], hello["code"]),
                             ("hello", 2, "Sam", "pw-12", "guestcode"))         # round MP2: protocol 2
            self.assertEqual(hello["deck"]["name"], "Veyran")
            self.assertIsNone(s._password)                             # forgotten once sent
            s.ok()                                                     # commands reach the host after the hello
            self.assertTrue(wait_for(lambda: h.received, 3))
            self.assertEqual(h.received[0]["c"], "ok")
        finally:
            s.close()

    def test_each_refusal_gives_its_reason_and_message(self):
        for reason, text in (("password", ""), ("full", ""), ("deck", "Forge could not read the deck."), ("version", "host speaks v1")):
            with self.subTest(reason=reason):
                h = self.host("refused:%s:%s" % (reason, text) if text else "refused:" + reason)
                with self.assertRaises(fn.NetRefused) as cm:
                    self.session(h.port).start()
                self.assertEqual(cm.exception.reason, reason)
                self.assertEqual(str(cm.exception), fn.join_message(reason, text))

    def test_a_silent_host_says_no_answer(self):
        h = self.host("silent")
        t0 = time.time()
        with self.assertRaises(fn.NetRefused) as cm:
            self.session(h.port, welcome_timeout=1).start()
        self.assertEqual(cm.exception.reason, "no_answer")
        self.assertLess(time.time() - t0, 5)

    def test_nobody_hosting_and_a_hang_up(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(fn.NetRefused) as cm:
            self.session(port).start()
        self.assertEqual(cm.exception.reason, "refused_connection")
        h = self.host("close")
        with self.assertRaises(fn.NetRefused) as cm:
            self.session(h.port).start()
        self.assertEqual(cm.exception.reason, "closed")

    def test_the_host_hanging_up_mid_game_ends_the_session(self):
        h = self.host("welcome", after=[{"t": "ready", "protocol": 2}])
        s = self.session(h.port).start()
        self.assertTrue(wait_for(lambda: s.ready, 3, pump=[s]))
        h.hang_up()
        self.assertTrue(wait_for(lambda: s.exited, 5, pump=[s]))
        self.assertFalse(s.alive())
        s.close()


# ---------------------------------------------------------------------------------------------------------------------
# 4. the password goes nowhere it could be read later
# ---------------------------------------------------------------------------------------------------------------------
class SecretTests(TempDecks):
    def test_the_password_is_not_on_the_command_line_but_in_the_childs_environment(self):
        cert = fn.HostCert(os.path.join(self.tmp, "host.p12"), "storepass-xyz", "cd" * 32)
        s = fc.HostSession("me.dck", "Karl", 36800, MARKER, cert, "guest.dck", upnp=True, code="c0de", seed=1)
        args = s._bridge_args()
        self.assertEqual(args[0], "forge.bridge.NetHost")
        self.assertNotIn(MARKER, " ".join(args))
        self.assertNotIn("storepass-xyz", " ".join(args))
        for flag in ("--upnp", "--parent-pid", "--keystore", "--guest-deck", "--port"):
            self.assertIn(flag, args)
        env = s._child_env()
        self.assertEqual(env["MANTICORE_NET_PASSWORD"], MARKER)
        self.assertEqual(env["MANTICORE_KEYSTORE_PASS"], "storepass-xyz")
        self.assertNotIn(MARKER, repr(s))
        self.assertNotIn("storepass-xyz", repr(cert))
        self.assertNotIn("MANTICORE_NET_PASSWORD", fc._clean_env())        # never leaks into an ordinary game's Java

    def test_not_in_settings_reports_or_the_crash_log(self):
        """A run that goes wrong on purpose, with the password typed in: it must not appear in settings.json, crash_log.txt,
        a bug report zip or anything printed."""
        settings = os.path.join(self.tmp, "settings.json")
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            gui = self.idle_gui(settings=settings)
            gui.menu.press(gui, "host_online")
            d = gui.modal
            d.fields["name"].set("Karl")
            d.fields["password"].set(MARKER)
            with mock.patch.object(fn, "ensure_host_cert", side_effect=fn.CertError("keytool was not found")):
                d.host(gui)                                        # fails on purpose
            self.assertIn("keytool", d.message[0])
            gui.save_settings()
            crashlog.record(RuntimeError("boom"), "test")          # a crash entry with the table's context
            with mock.patch.object(fc.NetSession, "_connect", side_effect=fn.NetRefused("no_answer")):
                session, err = gui.make_guest_session(gui.menu.mine, "Sam", "127.0.0.1", 36800, MARKER, None)
                with self.assertRaises(fn.NetRefused):
                    session.start()
            zip_path = reporting.build_report({"name": "t", "text": "test"}, folder=self.tmp)
        with open(settings, encoding="utf-8") as f:
            texts = [out.getvalue(), f.read()]
        with zipfile.ZipFile(zip_path) as z:
            texts += [z.read(n).decode("utf-8", "replace") for n in z.namelist()]
        log = crashlog.log_path()
        self.assertTrue(os.path.isfile(log), log)                  # the crash entry above really was written
        with open(log, encoding="utf-8", errors="replace") as f:
            texts.append(f.read())
        for t in texts:
            self.assertNotIn(MARKER, t)
        self.assertNotIn("password", json.loads(texts[1]).get("online", {}))


    def test_what_is_remembered_comes_back_next_time_without_the_password(self):
        """The name, port, address and known host fingerprints survive a restart (settings.json "online")."""
        settings = os.path.join(self.tmp, "settings.json")
        gui = self.idle_gui(settings=settings)
        known = {"203.0.113.7:36801": "ab" * 32}
        gui._remember_online(name="Karl", port=36801, upnp=False, address="203.0.113.7", known_hosts=known, password=MARKER)
        again = self.idle_gui(settings=settings)
        self.assertEqual(again.online_prefs, {"name": "Karl", "port": 36801, "upnp": False, "address": "203.0.113.7",
                                              "known_hosts": known})
        again.menu.press(again, "join_online")
        self.assertEqual(again.modal.fields["name"].text, "Karl")
        with open(settings, encoding="utf-8") as f:
            self.assertNotIn(MARKER, f.read())


# ---------------------------------------------------------------------------------------------------------------------
# 5. the deck screen's buttons and the two dialogs
# ---------------------------------------------------------------------------------------------------------------------
def inside(outer, inner):
    return outer.contains(inner)


class PanelTests(TempDecks):
    def check_dialog_fits(self, gui):
        r = gui.modal.rect
        self.assertTrue(gui.screen.get_rect().contains(r), r)
        rects = [b for b, _n in gui.modal.buttons]
        for b in rects:
            self.assertTrue(r.contains(b), (b, r))

    def test_the_deck_screen_has_host_and_join_that_dont_overlap(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.idle_gui(size=size, scale=scale)
                btns = {n: r for r, n in gui.menu.btns}
                self.assertIn("host_online", btns)
                self.assertIn("join_online", btns)
                self.assertFalse(btns["host_online"].colliderect(btns["join_online"]))
                self.assertFalse(btns["join_online"].colliderect(btns["start"]))
                self.assertTrue(gui.screen.get_rect().contains(btns["host_online"]))

    def test_host_and_join_dialogs_fit_at_every_size(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.idle_gui(size=size, scale=scale)
                gui.menu.press(gui, "host_online")
                self.assertIsInstance(gui.modal, onl.HostDialog)
                frame(gui, 1)
                self.check_dialog_fits(gui)
                gui.modal.message = ("Port 36800 is already in use on this PC. Close the other program, or choose another port.",
                                     onl.RED)
                frame(gui, 1)
                self.check_dialog_fits(gui)
                gui.modal = None
                gui.menu.press(gui, "join_online")
                self.assertIsInstance(gui.modal, onl.JoinDialog)
                frame(gui, 1)
                self.check_dialog_fits(gui)
                gui.modal.by_hand = True
                gui.modal.message = (fn.join_message("no_answer"), onl.RED)
                frame(gui, 1)
                self.check_dialog_fits(gui)
                gui.modal = None

    def test_host_waiting_screen_fits_in_every_state(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = self.idle_gui(size=size, scale=scale)
                s = fc.ForgeSession("", [])
                s.online, s.cert = "host", fn.HostCert("x", "y", "cd" * 32)
                gui.session, gui.menu = s, None
                gui.hosting_wait = onl.HostWait(s, "otter-lamp-river-42", 36800, True)
                for step in ("start", "hosting", "ok", "cgnat", "failed"):
                    if step == "hosting":
                        s.hosting = {"t": "hosting"}
                    elif step == "ok":
                        s.upnp = {"t": "upnp", "ok": True, "external_ip": "203.0.113.7"}
                    elif step == "cgnat":
                        gui.hosting_wait = onl.HostWait(s, "otter-lamp-river-42", 36800, True)
                        s.upnp = {"t": "upnp", "ok": False, "reason": "cgnat", "external_ip": "100.64.9.9"}
                        s.join_refusals = [{"reason": "password"}] * 3
                    elif step == "failed":
                        s.host_failed = {"text": "Port 36800 is already in use on this PC (Address already in use). Close the "
                                                 "other program, or choose another port."}
                    frame(gui, 1)
                    for b, _n in gui.hosting_wait.buttons:
                        self.assertTrue(gui.screen.get_rect().contains(b), (step, b))

    def test_host_dialog_checks_what_is_typed(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "host_online")
        d = gui.modal
        d.fields["name"].set("")
        self.assertEqual(d.problem(), "Type your name.")
        d.fields["name"].set("Karl")
        d.fields["port"].set("80")
        self.assertIn("1024", d.problem())
        d.fields["port"].set("36800")
        d.fields["password"].set("ab")
        self.assertIn("4 characters", d.problem())
        old = d.fields["password"].text
        press_button(gui, d, "new_password")
        self.assertNotEqual(d.fields["password"].text, old)
        self.assertIsNone(d.problem())
        press_button(gui, d, "upnp")
        self.assertFalse(d.upnp)
        key(gui, pygame.K_TAB)
        self.assertEqual(d.focus, "password")
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.modal)

    def test_join_dialog_reads_the_invite_code(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "join_online")
        d = gui.modal
        self.assertIn("Paste", d.problem())
        code = fn.make_invite("203.0.113.7", 36800, "ab" * 32, "pw-12")
        with mock.patch.object(gui, "read_clipboard", return_value="here: " + code):
            press_button(gui, d, "paste")
        d.fields["name"].set("Sam")
        self.assertIsNone(d.problem())
        self.assertEqual(d.target(), ("203.0.113.7", 36800, "pw-12", "ab" * 32))
        press_button(gui, d, "toggle")
        self.assertTrue(d.by_hand)
        self.assertEqual(d.focus, "address")
        d.fields["address"].set("my pc")
        self.assertEqual(d.problem(), "An address has no spaces in it.")

    def test_no_deck_chosen_says_so(self):
        gui = self.idle_gui()
        gui.menu.mine_id = None
        gui.menu.press(gui, "host_online")
        self.assertIsNone(gui.modal)
        self.assertIn("Choose your deck", gui.menu.message[0])

    def test_typing_in_a_field_never_reaches_the_table(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "join_online")
        d = gui.modal
        d.focus = "name"
        before = gui.text_scale
        for ch in "Sam-+":
            key(gui, 0, unicode=ch)
        self.assertEqual(d.fields["name"].text, "Sam-+")
        self.assertEqual(gui.text_scale, before)


# ---------------------------------------------------------------------------------------------------------------------
# 6. the table in an online game
# ---------------------------------------------------------------------------------------------------------------------
class FakeHostSession(fc.ForgeSession):
    """A HostSession without Java: the tests feed it NetHost's messages."""

    def __init__(self, deck_path, name, port, password, cert, guest_deck_path, upnp=True, code="", runtime=None, **kw):
        super().__init__(deck_path, [], name=name)
        self.online, self.cert, self.port = "host", cert, port
        self.sent = []
        self.started = False

    def start(self):
        self.started = True
        return self

    def alive(self):
        return self.started and not self.exited

    def send(self, **cmd):
        self.sent.append(cmd)
        return True

    def cancel_host(self):
        return self.send(c="cancel_host")

    def close(self):
        self.exited = True


class TableTests(TempDecks):
    def hosting_gui(self, settings=None):
        gui = self.idle_gui(settings=settings)
        gui.menu.press(gui, "host_online")
        d = gui.modal
        d.fields["name"].set("Karl")
        d.fields["password"].set(MARKER)
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", FakeHostSession):
            d.host(gui)
        frame(gui, 1)
        return gui

    def test_hosting_shows_the_waiting_screen_then_the_game(self):
        from tests.forge_fake import load_state
        gui = self.hosting_gui(settings=os.path.join(self.tmp, "settings.json"))
        self.assertIsNone(gui.modal)
        self.assertIsNone(gui.menu)
        self.assertIsInstance(gui.hosting_wait, onl.HostWait)
        s = gui.session
        self.assertEqual(s.online, "host")
        s.handle({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})
        s.handle({"t": "upnp", "ok": True, "external_ip": "203.0.113.7"})
        frame(gui, 1)
        code = gui.hosting_wait.invite()
        self.assertEqual(fn.read_invite(code)["address"], "203.0.113.7")
        self.assertEqual(fn.read_invite(code)["password"], MARKER)
        with mock.patch.object(gui, "write_clipboard") as wc:
            press_button(gui, gui.hosting_wait, "copy_invite")
        wc.assert_called_once_with(code)
        s.handle({"t": "guest_joined", "name": "Sam", "code": "othercode"})
        s.handle({"t": "ready", "protocol": 2, "seats": 2, "online": True})
        st = load_state("main1_start")
        s.handle(st)
        frame(gui, 2)
        self.assertIsNone(gui.hosting_wait)
        self.assertIn("different version", gui.toast[0])                 # othercode != this copy's code
        self.assertFalse(gui.can_restart())
        with open(os.path.join(self.tmp, "settings.json"), encoding="utf-8") as f:
            saved = f.read()
        self.assertEqual(json.loads(saved)["online"]["name"], "Karl")
        self.assertNotIn(MARKER, saved)

    def test_cancel_goes_back_to_the_deck_screen(self):
        gui = self.hosting_gui()
        s = gui.session
        key(gui, pygame.K_ESCAPE)
        self.assertEqual(s.sent[-1], {"c": "cancel_host"})
        self.assertIsNone(gui.hosting_wait)
        self.assertIsNotNone(gui.menu)

    def test_a_failed_host_shows_why_and_goes_back(self):
        gui = self.hosting_gui()
        gui.session.handle({"t": "host_failed", "reason": "port", "text": "Port 36800 is already in use on this PC."})
        frame(gui, 1)
        self.assertIn("already in use", gui.hosting_wait.failed())
        press_button(gui, gui.hosting_wait, "back")
        self.assertIsNotNone(gui.menu)

    def online_gui(self, online="guest"):
        from tests.forge_fake import FakeSession, load_state
        gui = self.idle_gui()
        s = FakeSession(load_state("main1_start"))
        s.online, s.peer_name = online, "Karl" if online == "guest" else "Sam"
        gui.menu = None
        gui.session = s
        gui._hook_session(journal=False)
        gui.reset_game()
        frame(gui, 2)
        return gui, s

    def test_peer_left_shows_your_friend_left_never_forge_stopped(self):
        gui, s = self.online_gui("host")
        s.handle({"t": "peer_left", "who": "guest", "why": "the connection closed"})
        s.exited = True
        frame(gui, 1)
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertEqual(gui.modal.title, "Your friend left")
        self.assertNotIn("Forge stopped", gui.modal.title)
        press_button(gui, gui.modal, "opt0")
        self.assertIsNotNone(gui.menu)

    def test_the_host_vanishing_says_the_host_left(self):
        gui, s = self.online_gui("guest")
        s.handle({"t": "_exit"})
        frame(gui, 2)
        self.assertEqual(gui.modal.title, "The host left")

    def test_after_game_over_a_leaving_friend_is_only_a_note(self):
        gui, s = self.online_gui("host")
        s.game_over = True
        s.handle({"t": "peer_left", "who": "guest", "gameOver": True})
        frame(gui, 1)
        self.assertNotIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertIn("left the table", gui.toast[0])

    def test_a_refused_command_is_a_short_note(self):
        gui, s = self.online_gui("guest")
        s.handle({"t": "refused_cmd", "c": "setup"})
        frame(gui, 1)
        self.assertIn("does not allow that from a guest", gui.toast[0])

    def test_online_games_are_not_journalled_and_quiet_banner_names_the_host(self):
        gui, s = self.online_gui("guest")
        gui.journal = mock.Mock()
        gui.start_journal()
        gui.journal.start.assert_not_called()
        gui._last_cmd_at = time.monotonic() - 9
        with mock.patch.object(gui, "not_responding", return_value=True), mock.patch.object(onl, "draw_text"):
            gui.draw_quiet_banner()                               # (no exception; the wording is checked below)
        import inspect
        self.assertIn("No answer from the host", inspect.getsource(type(gui).draw_quiet_banner))

    def test_join_job_success_starts_the_online_table(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "join_online")
        d = gui.modal
        d.fields["code"].set(fn.make_invite("127.0.0.1", 36800, "ab" * 32, "pw-12"))
        d.fields["name"].set("Sam")
        from tests.forge_fake import FakeSession, load_state

        def fake_make(entry, name, address, port, password, fp, by_hand=False):
            s = FakeSession(load_state("main1_start"))
            s.online, s.peer_name, s.address, s.port = "guest", "Karl", address, port
            s.start = lambda: s
            return s, None
        with mock.patch.object(gui, "make_guest_session", side_effect=fake_make):
            press_button(gui, d, "join")
            self.assertTrue(wait_for(lambda: (frame(gui, 1) or gui.modal is None), 5))
        self.assertEqual(gui.session.online, "guest")
        self.assertIsNone(gui.menu)

    def test_join_job_failure_shows_the_message(self):
        gui = self.idle_gui()
        gui.menu.press(gui, "join_online")
        d = gui.modal
        d.fields["code"].set(fn.make_invite("127.0.0.1", 36800, "ab" * 32, "pw-12"))
        d.fields["name"].set("Sam")
        with mock.patch.object(fc.NetSession, "_connect", side_effect=fn.NetRefused("fingerprint")):
            press_button(gui, d, "join")
            self.assertTrue(wait_for(lambda: (frame(gui, 1) or (d.job is None and d.message and d.message[1] == onl.RED)), 5))
        self.assertEqual(d.message[0], fn.join_message("fingerprint"))
        self.assertIs(gui.modal, d)


# ---------------------------------------------------------------------------------------------------------------------
# 7. the bridge's source (the parts a fast test can check)
# ---------------------------------------------------------------------------------------------------------------------
class SourceTests(unittest.TestCase):
    def test_the_installers_java_can_make_ec_keys(self):
        from tools import build_installer
        self.assertIn("jdk.crypto.ec", build_installer.JLINK_MODULES.split(","))

    def src(self, name):
        with open(os.path.join(JAVA_SRC, name), encoding="utf-8") as f:
            return f.read()

    def test_the_guest_may_not_quit_or_set_up_the_host_game(self):
        allowed = re.search(r"REMOTE_ALLOWED = Set\.of\(([^;]*)\);", self.src("NetHost.java"), re.S).group(1)
        names = set(re.findall(r'"(\w+)"', allowed))
        self.assertNotIn("quit", names)
        self.assertNotIn("setup", names)
        self.assertNotIn("cancel_host", names)
        self.assertIn("concede", names)

    def test_the_match_gets_the_commander_variant_and_tls_13_only(self):
        s = self.src("NetHost.java")
        self.assertIn("EnumSet.of(GameType.Commander)", s)              # round 27c: without it nobody is asked about the command zone
        self.assertIn('setEnabledProtocols(new String[]{"TLSv1.3"})', s)
        self.assertIn("MessageDigest.isEqual", s)                         # the password is compared in constant time
        self.assertIn('getenv("MANTICORE_NET_PASSWORD")', s)

    def test_input_limits(self):
        s = self.src("NetHost.java")
        self.assertIn("REMOTE_LINE_MAX = 64 * 1024", s)
        self.assertIn("REMOTE_RATE = 200", s)
        self.assertIn("HELLO_MAX = 256 * 1024", s)

    def test_the_jar_has_the_network_host(self):
        with zipfile.ZipFile(os.path.join(ROOT, "java_bridge", "forge_bridge.jar")) as z:
            names = set(z.namelist())
        for cls in ("NetHost", "RemoteWire", "LineReader", "Upnp"):
            self.assertIn("forge/bridge/%s.class" % cls, names)

    def test_backups_and_the_public_export_never_take_the_hosts_key(self):
        import backup
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import export_public
        self.assertIn("net", backup.TOP_LEVEL_SKIP)
        self.assertIn("*.p12", backup.SECRET_NAMES)
        self.assertIn("net", export_public.TOP_LEVEL_SKIP)
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            ignore = f.read().split()
        self.assertIn("net/", ignore)
        self.assertIn("*.p12", ignore)


# ---------------------------------------------------------------------------------------------------------------------
# 8. live: the real NetHost
# ---------------------------------------------------------------------------------------------------------------------
LIVE = live.live_enabled()
_CERT = {}


def deck_file(folder, sample, name):
    from deck_loader import load_deck
    c, d = load_deck(os.path.join(ROOT, "sample_decks", sample))
    return fc.write_deck_file(os.path.join(folder, name + ".dck"), c, d, name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(LIVE, "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="mp1_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        cls.host_deck = deck_file(cls.tmp, "typal_lathril.txt", "Lathril")
        cls.guest_deck = deck_file(cls.tmp, "spellslinger_veyran.txt", "Veyran")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def start_host(self, password="pw-live-1", dev=False, block_seconds=None, seed=5):
        port = free_port()
        h = fc.HostSession(self.host_deck, "Karl", port, password, self.cert, os.path.join(self.tmp, "guest.dck"), upnp=False,
                           bind="127.0.0.1", code="hostcode", dev=dev, block_seconds=block_seconds, seed=seed)
        h.stderr_path = os.path.join(self.tmp, "host_%d.log" % port)
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        self.assertIsNotNone(h.hosting, h.host_failed)
        return h, port

    def guest(self, port, password="pw-live-1", fingerprint="default", name="Sam"):
        fp = self.cert.fingerprint if fingerprint == "default" else fingerprint
        with open(self.guest_deck, encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, name, password, "Veyran", f.read(), fingerprint=fp, code="guestcode")
        g.start()
        self.addCleanup(g.close)
        return g

    def engine_log(self, h):
        with open(h.stderr_path, encoding="utf-8", errors="replace") as f:
            return f.read()

    # 11. TLS
    def test_tls_plain_text_and_the_fingerprint(self):
        h, port = self.start_host()
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)       # a plain-text client: no hello gets through
        raw.sendall((fn.hello_line("Mallory", "pw-live-1", "", "x", "") ).encode())
        raw.settimeout(5)
        try:
            data = raw.recv(4096)
        except (socket.timeout, OSError):
            data = b""
        raw.close()
        self.assertNotIn(b"welcome", data)
        with self.assertRaises(fn.NetRefused) as cm:                        # the wrong PC: closed before the password is sent
            self.guest(port, fingerprint="0" * 64)
        self.assertEqual(cm.exception.reason, "fingerprint")
        time.sleep(1.0)
        h.poll()
        self.assertEqual(h.join_refusals, [])                                # no hello ever reached the host
        self.assertIsNone(h.peer_name)
        g = self.guest(port)
        self.assertEqual(g.proc.sock.version(), "TLSv1.3")
        self.assertTrue(wait_for(lambda: h.peer_name == "Sam" and g.ready and h.ready, 30, pump=[h, g]))

    # 8. passwords
    def test_wrong_passwords_then_the_right_one_and_the_block(self):
        h, port = self.start_host(block_seconds=2)
        with self.assertRaises(fn.NetRefused) as cm:
            self.guest(port, password="nope")
        self.assertEqual(cm.exception.reason, "password")
        for _ in range(4):                                                    # 5 wrong in all: the address is now ignored
            with self.assertRaises(fn.NetRefused):
                self.guest(port, password="nope")
        with self.assertRaises(fn.NetRefused) as cm:                          # even the right password, for 2 s
            self.guest(port)
        self.assertIn(cm.exception.reason, ("closed", "tls", "no_answer"))
        time.sleep(2.5)
        g = self.guest(port)
        self.assertTrue(wait_for(lambda: g.ready, 30, pump=[h, g]))
        h.poll()
        self.assertEqual([r["reason"] for r in h.join_refusals], ["password"] * 5)
        with self.assertRaises(fn.NetRefused) as cm:                          # a third person: the game is full
            self.guest(port, name="Eve")
        self.assertEqual(cm.exception.reason, "full")

    def play_pregame(self, sessions, until, seconds=90):
        """Keep both opening hands; play or draw: play. Until `until()`."""
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

    @staticmethod
    def msg(s):
        return ((s.state or {}).get("prompt") or {}).get("message", "")

    # 6. hidden information, the game log included; 7. the guest's quit and setup are refused, the host's setup works
    def test_nothing_hidden_reaches_the_guest_and_the_guest_cant_rig_the_game(self):
        h, port = self.start_host(dev=True)
        g = self.guest(port)
        self.assertTrue(self.play_pregame([h, g], lambda: self.msg(h).startswith("Priority") or self.msg(g).startswith("Priority")))
        g.send(c="quit")
        g.send(c="setup", lines=["humanlife=1"])
        self.assertTrue(wait_for(lambda: len(g.refused_cmds) >= 2, 10, pump=[h, g]))
        self.assertEqual([r["c"] for r in g.refused_cmds[:2]], ["quit", "setup"])
        self.assertTrue(h.alive())
        h.setup(["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Demonic Tutor;Vampiric Tutor", "humanbattlefield=Swamp;Swamp;Swamp;Swamp",
                 "humanlibrary=Island;Mana Crypt;Island;Sol Ring;Island;Island", "aihand=Forest;Lightning Bolt",
                 "ailibrary=Forest;Forest;Forest;Forest", "aibattlefield=Forest", "removesummoningsickness=true"])
        self.assertTrue(wait_for(lambda: h.setups_done >= 1, 30, pump=[h, g]), "the host's dev setup didn't apply")
        log_start = len(g.log)
        self.assertTrue(self.cast(h, g, "Vampiric Tutor", "Mana Crypt", sorcery=False))
        self.assertTrue(self.cast(h, g, "Demonic Tutor", "Sol Ring", sorcery=True))
        wait_for(lambda: False, 2.0, pump=[h, g])
        self.assertIn("Sol Ring", [c["name"] for c in h.me()["zones"]["hand"]])
        guest_lines = [e.get("text") or "" for e in g.log]
        self.assertTrue(any("Vampiric Tutor" in t for t in guest_lines[log_start:]), "the guest's log didn't show the cast")
        for secret in ("Mana Crypt", "Sol Ring"):
            self.assertFalse(any(secret in t for t in guest_lines), secret)
            self.assertFalse(any(secret in json.dumps(e) for e, _t in g.events), secret)
        opp = g.opponents()[0]
        for zone in ("hand", "library"):
            for c in opp["zones"].get(zone, []) or []:
                self.assertTrue(c.get("hidden") or c.get("name") in (None, "Face-down card"), c)
        mine_in_host_view = h.opponents()[0]                                    # and the other way round
        for zone in ("hand", "library"):
            for c in mine_in_host_view["zones"].get(zone, []) or []:
                self.assertTrue(c.get("hidden") or c.get("name") in (None, "Face-down card"), c)
        self.assertNotIn("Lightning Bolt", json.dumps(h.state))

    def cast(self, h, g, name, want, sorcery):
        end, state = time.time() + 60, "wait"
        while time.time() < end:
            h.poll()
            g.poll()
            for r in list(g.requests):
                g.answer(r, True if r["kind"] == "confirm" else list(range(max(r.get("min", 1), 1))))
            for r in list(h.requests):
                names = [(it.get("card") or {}).get("name") or it.get("text") for it in r.get("items") or []]
                if r["kind"] == "confirm":
                    h.answer(r, True)
                else:
                    h.answer(r, [next((i for i, n in enumerate(names) if n == want), 0)])
                    state = "picked"
            if state == "picked" and not h.state.get("stack"):
                return True
            mh, ph = self.msg(h), (h.state or {}).get("prompt") or {}
            hand = [c for c in h.me()["zones"]["hand"] if c["name"] == name]
            if state == "wait" and mh.startswith("Priority") and not h.state.get("stack") and hand and (not sorcery or "Main phase" in mh):
                h.click_card(hand[0]["id"])
                state = "cast"
                wait_for(lambda: False, 0.6, pump=[h, g])
                continue
            if "Pay Mana" in mh or (ph.get("selecting") and state == "cast"):
                sw = [c for c in h.me()["zones"]["battlefield"] if c["name"] == "Swamp" and not c.get("tapped")]
                if sw:
                    h.click_card(sw[0]["id"])
                    wait_for(lambda: False, 0.4, pump=[h, g])
                    continue
            if mh.startswith("Priority") and (h.state.get("stack") or state == "wait"):
                h.ok()
                wait_for(lambda: False, 0.4, pump=[h, g])
                continue
            if self.msg(g).startswith("Priority"):
                g.ok()
                wait_for(lambda: False, 0.4, pump=[h, g])
                continue
            time.sleep(0.05)
        return False

    # 10. a concede between two questions (the online soak's game 10, 2 Oct): Forge ended the game, then asked the next priority
    #     question anyway, nobody answered it and both tables waited for ever. BridgeGui.releaseIfGameOver releases it.
    def test_a_concede_at_any_moment_ends_the_game_on_both_tables(self):
        from tools import online_soak as osk
        osk._load_modules()
        for attempt, conceder in enumerate(("guest", "host", "guest")):
            with self.subTest(attempt=attempt, conceder=conceder):
                h, port = self.start_host(seed=40 + attempt)
                g = self.guest(port)
                seats = [osk.Seat("host", h, 40 + attempt), osk.Seat("guest", g, 50 + attempt)]
                quitter = g if conceder == "guest" else h
                end = time.time() + 300
                conceded = False
                while time.time() < end and not (h.game_over and g.game_over):
                    for seat in seats:
                        if conceded:
                            seat.session.poll()
                        else:
                            seat.step()
                    turn = (h.state or {}).get("turn") or 0
                    # between two questions: Forge is asking the quitter nothing right now (the race the fix is for)
                    if not conceded and turn >= 3 and (quitter.state or {}).get("asking") is False:
                        quitter.concede()
                        conceded = True
                    time.sleep(0.02)
                self.assertTrue(conceded, "the game never reached turn 3")
                self.assertTrue(wait_for(lambda: h.game_over and g.game_over, 30, pump=[h, g]),
                                "after the %s conceded: host game_over=%s, guest game_over=%s; host asks %r" % (
                                    conceder, h.game_over, g.game_over, self.msg(h)[:60]))
                g.close()
                h.close()

    # 9. the guest leaves while Forge waits for the guest's answer: an input (Mind Rot's discard) and a request (Fact or
    #    Fiction's piles, which the opponent makes)
    def test_the_guest_leaving_mid_question_ends_the_game_cleanly(self):
        self.leave_mid_question("Mind Rot", "Swamp", lambda g: "discard" in self.msg(g).lower())

    def test_the_guest_leaving_mid_request_ends_the_game_cleanly(self):
        self.leave_mid_question("Fact or Fiction", "Island", lambda g: bool(g.requests))

    def leave_mid_question(self, spell, land, asked_now):
        h, port = self.start_host(dev=True)
        g = self.guest(port)
        self.assertTrue(self.play_pregame([h, g], lambda: self.msg(h).startswith("Priority") or self.msg(g).startswith("Priority")))
        h.setup(["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=" + spell, "humanbattlefield=" + ";".join([land] * 4),
                 "humanlibrary=Island;Forest;Mountain;Plains;Swamp;Island;Island",
                 "aihand=Forest;Island;Mountain", "ailibrary=Forest;Forest;Forest", "aibattlefield=", "removesummoningsickness=true"])
        self.assertTrue(wait_for(lambda: h.setups_done >= 1, 30, pump=[h, g]))
        end, asked, cast = time.time() + 60, False, False
        while time.time() < end:
            h.poll()
            g.poll()
            if asked_now(g):
                asked = True
                break
            mh = self.msg(h)
            hand = [c for c in h.me()["zones"]["hand"] if c["name"] == spell]
            if not cast and mh.startswith("Priority") and "Main phase" in mh and hand and not h.state.get("stack"):
                h.click_card(hand[0]["id"])
                cast = True
            elif cast and "target" in mh.lower():
                h.click_player(h.opponents()[0]["id"])
            elif cast and ("Pay Mana" in mh or ((h.state or {}).get("prompt") or {}).get("selecting")):
                lands = [c for c in h.me()["zones"]["battlefield"] if c["name"] == land and not c.get("tapped")]
                if lands:
                    h.click_card(lands[0]["id"])
            elif mh.startswith("Priority") and (h.state.get("stack") or not cast):
                h.ok()
            elif self.msg(g).startswith("Priority"):
                g.ok()
            time.sleep(0.3)
        self.assertTrue(asked, "the guest was never asked anything by " + spell)
        t0 = time.time()
        g.close()                                                               # gone, with Forge waiting for the answer
        self.assertTrue(wait_for(lambda: h.peer_left is not None, 5, pump=[h]), "no peer_left within 5 s")
        self.assertLess(time.time() - t0, 5.5)
        self.assertTrue(wait_for(lambda: h.game_over or (h.state or {}).get("gameOver"), 20, pump=[h]),
                        "the game didn't end after the guest left")
        self.assertEqual((h.state or {}).get("winner"), "Karl")
        h.close()
        self.assertTrue(wait_for(lambda: not h.alive(), 5), "the host's engine didn't exit")

    # 10. both tables play three turns
    def test_both_tables_play_three_turns(self):
        from tests.forge_bot import Bot
        h, port = self.start_host()
        g = self.guest(port)
        bots = [Bot(h), Bot(g)]
        end = time.time() + 240
        turns = set()
        while time.time() < end and not (h.game_over or g.game_over):
            for s, bot in zip((h, g), bots):
                s.poll()
                bot.step()
                if s.state:
                    turns.add(s.state.get("turn"))
            if max([t for t in turns if t] or [0]) >= 4:
                break
            time.sleep(0.03)
        self.assertGreaterEqual(max([t for t in turns if t] or [0]), 4, "three turns weren't played")
        self.assertEqual(h.me()["name"], "Karl")
        self.assertEqual(g.me()["name"], "Sam")
        for d in h.dropped + g.dropped:                                         # stale clicks are dropped, never applied elsewhere
            self.assertIn(d.get("c"), fc.NUMBERED)
        log = self.engine_log(h)
        self.assertNotRegex(log, r"Exception(?!.*AiController)")


# ---------------------------------------------------------------------------------------------------------------------
# 9. live, through two real tables: host, invite code, join, play, leave
# ---------------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(LIVE, "needs Java and forge_runtime/")
class LiveTableTests(TempDecks):
    def test_two_tables_host_join_and_leave(self):
        import paths
        import forge_table as ft
        from tests.forge_fake import StubStore
        user = os.path.join(self.tmp, "user")
        os.makedirs(user)
        with mock.patch.object(paths, "user_dir", return_value=user):
            launcher = ft.Launcher(None, "Karl")
            host = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), launcher=launcher,
                                 deck_dirs=self.dirs)
            host.open_menu()
            frame(host, 1)
            port = free_port()
            host.menu.press(host, "host_online")
            d = host.modal
            d.fields["name"].set("Karl")
            d.fields["port"].set(str(port))
            d.upnp = False
            d.host(host)
            self.addCleanup(lambda: host.session.close())
            self.assertIsInstance(host.hosting_wait, onl.HostWait)
            self.assertTrue(wait_for(lambda: (frame(host, 1) or host.session.hosting is not None), 120), "never hosting")
            host.hosting_wait.address.set("127.0.0.1")
            code = host.hosting_wait.invite()
            self.assertIsNotNone(code)

            guest = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), launcher=ft.Launcher(None, "Sam"),
                                  deck_dirs=self.dirs)
            guest.open_menu()
            frame(guest, 1)
            guest.menu.press(guest, "join_online")
            j = guest.modal
            j.fields["code"].set(code)
            j.fields["name"].set("Sam")
            j.join(guest)
            self.addCleanup(lambda: guest.session.close())
            self.assertTrue(wait_for(lambda: (frame(guest, 1) or frame(host, 1) or guest.modal is None), 60), j.message)
            self.assertEqual(guest.session.online, "guest")
            self.assertTrue(wait_for(lambda: (frame(guest, 1) or frame(host, 1) or (host.hosting_wait is None and
                                                                                     guest.state and host.state)), 60))
            self.assertEqual(host.session.peer_name, "Sam")
            self.assertEqual(guest.session.peer_name, "Karl")
            self.assertEqual(guest.session.me()["name"], "Sam")
            guest.session.close()                                                  # the friend closes their game
            self.assertTrue(wait_for(lambda: (frame(host, 1) or isinstance(host.modal, dlg.OptionsDialog)), 15))
            self.assertEqual(host.modal.title, "Your friend left")


if __name__ == "__main__":
    unittest.main()
