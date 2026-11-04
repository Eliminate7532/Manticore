# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RLY2: the relay is the default way to host (Karl, 2 Nov 2026: "I want the relay to be the default multiplayer choice and
I want the ip/port already filled in automatically").

relay_config.json next to the program names the relay; Host online and the online draft's host window tick "Connect through
the relay" with that address filled in. A player who unticks it is remembered as hosting directly; a relay they type is kept,
the default one is not (so a moved relay reaches them through an update). No relay set up = hosting as before.
tests/__init__.py runs every other test with MANTICORE_RELAY="" (no default relay); these tests set it themselves.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import forge_client as fc
import forge_net as fn
import online_screens as onl
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import frame
from tests.test_mp1 import SIZES, FakeHostSession, press_button

RELAY = "relay.example.com:36901"


def with_relay(value):
    return mock.patch.dict(os.environ, {"MANTICORE_RELAY": value})


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text):
        with open(os.path.join(self.tmp, fn.RELAY_CONFIG), "w", encoding="utf-8") as f:
            f.write(text)

    def test_the_shipped_file_names_the_oracle_relay(self):
        self.assertEqual(fn.default_relay(ROOT), "163.192.120.37:36900")

    def test_no_file_broken_file_or_bad_address_means_no_default(self):
        self.assertEqual(fn.default_relay(self.tmp), "")
        self.write("{not json")
        self.assertEqual(fn.default_relay(self.tmp), "")
        self.write(json.dumps({"relay": "has space:36900"}))
        self.assertEqual(fn.default_relay(self.tmp), "")
        self.write(json.dumps({"relay": "1.2.3.4:80"}))           # port below 1024
        self.assertEqual(fn.default_relay(self.tmp), "")
        self.write(json.dumps(["1.2.3.4:36900"]))
        self.assertEqual(fn.default_relay(self.tmp), "")

    def test_a_relay_without_a_port_gets_36900(self):
        self.write(json.dumps({"relay": "relay.example.com"}))
        self.assertEqual(fn.default_relay(self.tmp), "relay.example.com:36900")

    def test_the_environment_wins_when_no_folder_is_given(self):
        with with_relay("10.1.2.3:37000"):
            self.assertEqual(fn.default_relay(), "10.1.2.3:37000")
        with with_relay(""):
            self.assertEqual(fn.default_relay(), "")
        with with_relay("10.1.2.3:37000"):                        # a folder asks for that folder's file, whatever the env
            self.assertEqual(fn.default_relay(ROOT), "163.192.120.37:36900")

    def test_parse_relay(self):
        self.assertEqual(fn.parse_relay("163.192.120.37:36900"), ("163.192.120.37", 36900))
        self.assertEqual(fn.parse_relay("163.192.120.37"), ("163.192.120.37", fn.DEFAULT_RELAY_PORT))
        self.assertEqual(fn.parse_relay("[::1]:37000"), ("::1", 37000))
        self.assertEqual(fn.parse_relay("::1"), ("::1", fn.DEFAULT_RELAY_PORT))
        self.assertEqual(fn.parse_relay(""), "Type the relay's address (address:port).")
        self.assertIsInstance(fn.parse_relay("a b:36900"), str)
        self.assertIsInstance(fn.parse_relay("1.2.3.4:99999"), str)


class HostDialogTests(TempDecks):
    def dialog(self, prefs=None):
        gui = self.idle_gui()
        if prefs:
            gui.online_prefs.update(prefs)
        gui.menu.press(gui, "host_online")
        d = gui.modal
        self.assertIsInstance(d, onl.HostDialog)
        d.fields["name"].set("Karl")
        frame(gui, 1)
        return gui, d

    def test_the_relay_is_ticked_and_filled_in(self):
        with with_relay(RELAY):
            gui, d = self.dialog()
            self.assertTrue(d.use_relay)
            self.assertEqual(d.fields["relay"].text, RELAY)
            names = [n for _r, n in d.buttons]
            self.assertIn("field_relay", names)
            self.assertNotIn("upnp", names)                          # the router doesn't matter through the relay
            self.assertIsNone(d.problem())
            with mock.patch.object(gui, "host_game", return_value=None) as hg:
                press_button(gui, d, "host")
            self.assertEqual(hg.call_args.kwargs["relay"], ("relay.example.com", 36901))

    def test_the_old_untick_from_before_this_round_doesnt_stop_the_default(self):
        with with_relay(RELAY):
            _gui, d = self.dialog({"use_relay": False})
            self.assertTrue(d.use_relay)

    def test_a_player_who_chose_direct_gets_direct_again(self):
        with with_relay(RELAY):
            gui, d = self.dialog({"direct": True})
            self.assertFalse(d.use_relay)
            self.assertIn("upnp", [n for _r, n in d.buttons])
            with mock.patch.object(gui, "host_game", return_value=None) as hg:
                press_button(gui, d, "host")
            self.assertNotIn("relay", hg.call_args.kwargs)
            press_button(gui, d, "use_relay")                        # ticking it again brings the default back
            self.assertEqual(d.relay_target(), ("relay.example.com", 36901))

    def test_a_relay_the_player_typed_is_shown(self):
        with with_relay(RELAY):
            _gui, d = self.dialog({"relay": "my.relay.net:37005"})
            self.assertTrue(d.use_relay)
            self.assertEqual(d.relay_target(), ("my.relay.net", 37005))

    def test_an_emptied_box_means_the_default(self):
        with with_relay(RELAY):
            _gui, d = self.dialog()
            d.fields["relay"].set("")
            self.assertEqual(d.relay_target(), ("relay.example.com", 36901))
            self.assertIsNone(d.problem())

    def test_without_a_relay_hosting_is_as_before(self):
        with with_relay(""):
            _gui, d = self.dialog()
            self.assertFalse(d.use_relay)
            self.assertEqual(d.fields["relay"].text, "")
            self.assertIn("upnp", [n for _r, n in d.buttons])

    def test_the_dialog_fits_at_every_size_with_the_relay_ticked(self):
        with with_relay(RELAY):
            for size, scale in SIZES:
                with self.subTest(size=size, scale=scale):
                    gui = self.idle_gui(size=size, scale=scale)
                    gui.menu.press(gui, "host_online")
                    d = gui.modal
                    self.assertTrue(d.use_relay)
                    frame(gui, 1)
                    r = d.rect
                    self.assertTrue(gui.screen.get_rect().contains(r), r)
                    rects = list(d.buttons)
                    for b, n in rects:
                        self.assertTrue(r.contains(b), (n, b, r))
                    for i, (b1, n1) in enumerate(rects):
                        for b2, n2 in rects[i + 1:]:
                            self.assertFalse(b1.colliderect(b2), (n1, b1, n2, b2))


class RememberTests(TempDecks):
    def host(self, gui, relay):
        entry = self.entry = getattr(self, "entry", None) or gui.menu.mine          # hosting closes the deck screen

        def fake_host(*a, **kw):
            s = FakeHostSession(*a, **kw)
            s.relay = kw.get("relay")
            return s
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), \
                mock.patch.object(fc, "HostSession", side_effect=fake_host):
            self.assertIsNone(gui.host_game(entry, "Karl", 36800, "pw-123", True, **({"relay": relay} if relay else {})))

    def test_the_default_relay_is_not_kept_a_typed_one_is_and_direct_is_remembered(self):
        with with_relay(RELAY):
            gui = self.idle_gui()
            self.host(gui, ("relay.example.com", 36901))
            self.assertEqual(gui.online_prefs.get("relay"), "")
            self.assertFalse(gui.online_prefs.get("direct"))
            self.host(gui, ("my.relay.net", 37005))
            self.assertEqual(gui.online_prefs.get("relay"), "my.relay.net:37005")
            self.host(gui, None)
            self.assertTrue(gui.online_prefs.get("direct"))
            self.assertEqual(gui.online_prefs.get("relay"), "my.relay.net:37005")     # hosting directly keeps the typed relay


class DraftHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def gui(self, size=(1360, 840), scale=1.0):
        import forge_table as ft
        from tests.forge_fake import StubStore
        lib_dir = os.path.join(self.tmp, "lib")
        g = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=size, launcher=ft.Launcher(None, "Karl"),
                          deck_dirs=(lib_dir, lib_dir, self.tmp))
        g.text_scale = scale
        frame(g, 1)
        return g

    def flow(self, g, prefs):
        import online_draft_screens as odscr
        import draft_pod as dp
        with mock.patch.object(dp, "unfinished", return_value=None):
            f = odscr.OnlineDraftFlow(g, "host", job=None, prefs=prefs)
        g.modal = f
        frame(g, 1)
        return f

    def test_the_draft_hosts_through_the_relay_by_default(self):
        g = self.gui()
        f = self.flow(g, {"name": "Karl", "relay_default": RELAY})
        self.assertTrue(f.use_relay)
        names = [n for _r, n in f.buttons]
        self.assertIn("use_relay", names)
        self.assertNotIn("upnp", names)
        with mock.patch.object(g, "host_draft", return_value=(None, "stop here")) as hd:
            f.open_hosting(g)
        self.assertEqual(hd.call_args.kwargs.get("relay"), ("relay.example.com", 36901))

    def test_unticked_or_no_relay_hosts_directly(self):
        g = self.gui()
        f = self.flow(g, {"name": "Karl", "relay_default": RELAY})
        f.press(g, "use_relay")
        self.assertFalse(f.use_relay)
        frame(g, 1)
        self.assertIn("upnp", [n for _r, n in f.buttons])
        with mock.patch.object(g, "host_draft", return_value=(None, "stop here")) as hd:
            f.open_hosting(g)
        self.assertNotIn("relay", hd.call_args.kwargs)
        f2 = self.flow(g, {"name": "Karl"})                        # no relay set up
        self.assertFalse(f2.use_relay)
        self.assertNotIn("use_relay", [n for _r, n in f2.buttons])

    def test_host_draft_passes_the_relay_to_the_engine(self):
        g = self.gui()
        made = {}

        class Stop:
            def start(self):
                raise RuntimeError("stop here")

        def fake(*a, **kw):
            made.update(kw)
            return Stop()
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", side_effect=fake):
            g.host_draft("Karl", 36800, "pw-123", True, relay=("relay.example.com", 36901))
        self.assertEqual(made.get("relay"), ("relay.example.com", 36901))
        self.assertTrue(made.get("draft"))

    def test_the_draft_host_window_fits_with_the_relay_ticked(self):
        from tests.test_cd5 import FIT_SIZES
        import pygame
        for size, scale in FIT_SIZES:
            with self.subTest(size=size, scale=scale):
                g = self.gui(size, scale)
                f = self.flow(g, {"name": "Karl", "relay_default": RELAY})
                screen = pygame.Rect(0, 0, g.L.W, g.L.H)
                self.assertTrue(screen.contains(f.rect))
                rects = [r for r, _n in f.buttons]
                for r in rects:
                    self.assertTrue(f.rect.contains(r), r)
                for i, a in enumerate(rects):
                    for b in rects[i + 1:]:
                        self.assertFalse(a.colliderect(b), (a, b, f.buttons))


class PackagingTests(unittest.TestCase):
    def test_the_installer_ships_the_relay_file(self):
        with open(os.path.join(ROOT, "commander_sim.spec"), encoding="utf-8") as f:
            self.assertIn('_data("relay_config.json", ".")', f.read())
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import check_dist
        self.assertIn("relay_config.json", check_dist.REQUIRED_FILES)
        self.assertIn("163.192.120.37:36900", check_dist.relay_note(ROOT))


if __name__ == "__main__":
    unittest.main()
