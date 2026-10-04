# SPDX-License-Identifier: GPL-3.0-or-later
"""
Round 30: an installed copy updates itself (updater.py, update_screens.py, tools/make_update_feed.py).

Everything here is offline: the feed and the installer come from a small HTTP server on 127.0.0.1 (MANTICORE_UPDATE_TEST=1
lets updater.py use plain http there, and stands in for "this is an installed copy"); the installer is never really run -
launch_installer gets a fake Popen. NOT tested here (needs Windows): the PowerShell helper itself, Inno Setup's /SILENT, and
the new copy starting by itself.
"""
import base64
import hashlib
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import boot_screens as fboot
import forge_client as fc
import forge_table as ft
import paths
import update_screens
import updater
import version
from tests.forge_fake import StubStore
from tests.test_deck_screen import FakeLauncher, TempDecks
from tests.test_forge_table import frame, key
from tools import check_dist
from tools import make_update_feed as muf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def testing_env(**extra):
    env = {"MANTICORE_UPDATE_TEST": "1"}
    env.update(extra)
    return mock.patch.dict(os.environ, env)


def good_feed(**over):
    feed = {"version": "9.0.0", "url": "https://github.com/o/r/releases/download/v9.0.0/Manticore-9.0.0-setup.exe",
            "sha256": "a" * 64, "size": 150_000_000, "notes": "Online play.", "published": "2026-10-02"}
    feed.update(over)
    return feed


# ---- a tiny web server ----------------------------------------------------------------------------------------------
class Server:
    """Serves self.routes: path -> (status, body bytes). Counts requests per path."""

    def __init__(self):
        self.routes = {}
        self.hits = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits[self.path] = outer.hits.get(self.path, 0) + 1
                status, body = outer.routes.get(self.path, (404, b"no"))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.httpd.server_address[1], path)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def put(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)


def get_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wait(job, seconds=10):
    end = time.monotonic() + seconds
    while not job.finished and time.monotonic() < end:
        time.sleep(0.01)
    return job.finished


# ---- versions, the feed, where it comes from --------------------------------------------------------------------------
class VersionTests(unittest.TestCase):
    def test_newer_compares_numbers_not_text(self):
        self.assertTrue(updater.newer("0.28.14", "0.28.13"))
        self.assertTrue(updater.newer("0.28.10", "0.28.9"))
        self.assertTrue(updater.newer("0.29", "0.28.99"))
        self.assertTrue(updater.newer("1.0.0.1", "1.0.0"))
        self.assertFalse(updater.newer("0.28.13", "0.28.13"))
        self.assertFalse(updater.newer("0.28.13.0", "0.28.13"))
        self.assertFalse(updater.newer("0.28.12", "0.28.13"))
        self.assertFalse(updater.newer("v0.30", "0.28"))          # a feed version must be bare numbers
        self.assertFalse(updater.newer("0.30-beta", "0.28"))
        self.assertFalse(updater.newer("", "0.28"))

    def test_this_copys_own_version_parses(self):
        self.assertIsNotNone(updater.parse_version(version.VERSION))


class FeedTests(unittest.TestCase):
    def test_a_good_feed_reads(self):
        info, why = updater.parse_feed(good_feed(sha256="AB" * 32))
        self.assertIsNone(why)
        self.assertEqual((info.version, info.size, info.sha256), ("9.0.0", 150_000_000, "ab" * 32))
        self.assertEqual(info.file_name(), "Manticore-9.0.0-setup.exe")

    def test_a_bad_feed_says_what_is_wrong(self):
        cases = {"not https": good_feed(url="http://github.com/x.exe"),
                 "no version": good_feed(version=""),
                 "odd version": good_feed(version="latest"),
                 "short checksum": good_feed(sha256="abc"),
                 "tiny": good_feed(size=1000),
                 "huge": good_feed(size=10 ** 12),
                 "no size": good_feed(size=None)}
        for name, feed in cases.items():
            with self.subTest(name):
                info, why = updater.parse_feed(feed)
                self.assertIsNone(info)
                self.assertTrue(why)
        self.assertEqual(updater.parse_feed([1, 2])[0], None)

    def test_plain_http_is_only_for_the_tests_on_this_computer(self):
        self.assertFalse(updater.url_ok("http://127.0.0.1:8000/latest.json"))
        with testing_env():
            self.assertTrue(updater.url_ok("http://127.0.0.1:8000/latest.json"))
            self.assertFalse(updater.url_ok("http://example.com/latest.json"))

    def test_notes_are_cut_short_and_an_odd_file_name_gets_a_safe_one(self):
        info, _ = updater.parse_feed(good_feed(notes="x" * 5000, url="https://h/x/..%5C..%5Cevil.bat"))
        self.assertEqual(len(info.notes), updater.NOTES_MAX)
        self.assertEqual(info.file_name(), "Manticore-9.0.0-setup.exe")

    def test_where_the_feed_comes_from(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, updater.CONFIG_NAME)
            self.assertIsNone(updater.feed_url(d))                                  # no file: off
            for cfg, want in (({"repo": "", "feed_url": ""}, None),
                              ({"repo": "karl/manticore"}, "https://github.com/karl/manticore/releases/latest/download/latest.json"),
                              ({"repo": "not a repo"}, None),
                              ({"repo": "a/b", "feed_url": "https://x.example/latest.json"}, "https://x.example/latest.json")):
                with self.subTest(cfg=cfg):
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(cfg, f)
                    self.assertEqual(updater.feed_url(d), want)
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertIsNone(updater.feed_url(d))
            with mock.patch.dict(os.environ, {"MANTICORE_UPDATE_FEED": "https://y.example/f.json"}):
                self.assertEqual(updater.feed_url(d), "https://y.example/f.json")

    def test_the_shipped_config_names_the_public_repository(self):
        """Patch 35 (3 Oct 2026): Karl made the public repository; until then this test checked that both were empty."""
        cfg = updater.read_config(BASE_DIR)
        self.assertEqual((cfg.get("repo"), cfg.get("feed_url")), ("Eliminate7532/Manticore", ""))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MANTICORE_UPDATE_FEED", None)
            self.assertEqual(updater.feed_url(BASE_DIR),
                             "https://github.com/Eliminate7532/Manticore/releases/latest/download/latest.json")
        with open(os.path.join(BASE_DIR, "commander_sim.spec"), encoding="utf-8") as f:
            spec = f.read()
        self.assertIn('_data("update_config.json", ".")', spec)


class EnabledTests(unittest.TestCase):
    def test_karls_git_copy_never_updates_itself(self):
        with mock.patch.dict(os.environ, {"MANTICORE_NO_UPDATE_CHECK": "", "MANTICORE_UPDATE_FEED": "https://x/f.json"}):
            self.assertFalse(updater.installed())                    # not frozen
            ok, why = updater.enabled(True)
            self.assertFalse(ok)
            self.assertIn("git", why)
            self.assertTrue(updater.describe(True).startswith("updates: off"))

    def test_an_installed_copy_is_frozen_and_not_portable(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.object(paths, "is_portable", return_value=False):
                self.assertTrue(updater.installed())
            with mock.patch.object(paths, "is_portable", return_value=True):
                self.assertFalse(updater.installed())

    def test_each_reason_to_stay_quiet(self):
        with mock.patch.object(updater, "installed", return_value=True):
            with mock.patch.dict(os.environ, {"MANTICORE_NO_UPDATE_CHECK": "", "MANTICORE_UPDATE_FEED": ""}):
                with mock.patch.object(updater, "read_config", return_value={}):
                    self.assertEqual(updater.enabled(True), (False, "no update source is set up in this build"))
                with mock.patch.object(updater, "read_config", return_value={"repo": "o/r"}):
                    self.assertEqual(updater.enabled(False), (False, "turned off in Settings"))
                    ok, url = updater.enabled(True)
                    self.assertTrue(ok)
                    self.assertEqual(updater.describe(True), "updates: on (github.com)")
            with mock.patch.dict(os.environ, {"MANTICORE_NO_UPDATE_CHECK": "1", "MANTICORE_UPDATE_TEST": ""}):
                self.assertEqual(updater.enabled(True), (False, "turned off for this run"))

    def test_the_test_suite_never_checks(self):
        self.assertEqual(os.environ.get("MANTICORE_NO_UPDATE_CHECK"), "1")


# ---- the check and the download ---------------------------------------------------------------------------------------
class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.env = testing_env()
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.env.stop()
        self.server.close()
        self.tmp.cleanup()

    def installer(self, size=300_000):
        data = os.urandom(size)
        self.server.routes["/setup.exe"] = (200, data)
        return data

    def info(self, data, **over):
        kw = dict(version="9.0.0", url=self.server.url("/setup.exe"), sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        kw.update(over)
        return updater.UpdateInfo(**kw)

    def test_check_reads_the_feed(self):
        self.server.routes["/latest.json"] = (200, json.dumps(good_feed()).encode())
        info, why = updater.check(self.server.url("/latest.json"))
        self.assertIsNone(why)
        self.assertEqual(info.version, "9.0.0")

    def test_check_never_raises(self):
        self.server.routes["/bad.json"] = (200, b"{oops")
        self.server.routes["/big.json"] = (200, b" " * (updater.FEED_MAX_BYTES + 10))
        self.assertIn("404", updater.check(self.server.url("/missing.json"))[1])
        self.assertIn("valid JSON", updater.check(self.server.url("/bad.json"))[1])
        self.assertIn("too big", updater.check(self.server.url("/big.json"))[1])
        self.assertIn("couldn't reach", updater.check("http://127.0.0.1:1/latest.json", timeout=2)[1])
        self.assertIn("https", updater.check("ftp://x/latest.json")[1])

    def test_check_job_offers_only_a_newer_version(self):
        self.server.routes["/latest.json"] = (200, json.dumps(good_feed(version="0.28.20")).encode())
        job = updater.CheckJob(self.server.url("/latest.json"), current="0.28.14").start()
        self.assertTrue(wait(job))
        self.assertEqual((job.info.version, job.error), ("0.28.20", None))
        job = updater.CheckJob(self.server.url("/latest.json"), current="0.28.20").start()
        self.assertTrue(wait(job))
        self.assertEqual((job.info, job.error), (None, None))          # up to date: nothing, and nothing logged

    def test_a_good_download_is_checked_and_kept_and_older_ones_go(self):
        data = self.installer()
        old = os.path.join(self.tmp.name, "Manticore-1.0.0-setup.exe")
        put(old, b"old")
        job = updater.DownloadJob(self.info(data), folder=self.tmp.name).start()
        self.assertTrue(wait(job))
        self.assertIsNone(job.error)
        with open(job.path, "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual(job.fraction(), 1.0)
        self.assertEqual(os.listdir(self.tmp.name), [os.path.basename(job.path)])     # no .part, no older installer
        again = updater.DownloadJob(self.info(data), folder=self.tmp.name).start()  # already there and verified: no request
        self.assertTrue(wait(again))
        self.assertEqual((again.path, self.server.hits["/setup.exe"]), (job.path, 1))

    def test_a_damaged_or_oversized_download_is_refused_and_removed(self):
        data = self.installer()
        for name, info in (("checksum", self.info(data, sha256="0" * 64)),
                           ("too big", self.info(data, size=len(data) - 1000)),
                           ("too small", self.info(data, size=len(data) + 1000))):
            with self.subTest(name):
                job = updater.DownloadJob(info, folder=self.tmp.name).start()
                self.assertTrue(wait(job))
                self.assertIsNone(job.path)
                self.assertTrue(job.error)
                self.assertEqual(os.listdir(self.tmp.name), [])

    def test_a_server_error_and_a_cancel(self):
        job = updater.DownloadJob(self.info(b"x", url=self.server.url("/gone.exe")), folder=self.tmp.name).start()
        self.assertTrue(wait(job))
        self.assertIn("404", job.error)
        data = self.installer(3_000_000)
        job = updater.DownloadJob(self.info(data), folder=self.tmp.name)
        job.cancel()
        job.start()
        self.assertTrue(wait(job))
        self.assertTrue(job.cancelled)
        self.assertIsNone(job.path)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_a_redirect_to_an_unsafe_address_is_stopped(self):
        class Resp:
            status_code = 200
            url = "http://evil.example/setup.exe"

            def iter_content(self, n):
                yield b"x"

        class Session:
            def get(self, *a, **k):
                return Resp()

        job = updater.DownloadJob(self.info(b"x"), folder=self.tmp.name, session=Session()).start()
        self.assertTrue(wait(job))
        self.assertIn("unsafe", job.error)


# ---- the installer helper ---------------------------------------------------------------------------------------------
class InstallerTests(unittest.TestCase):
    def test_the_helper_waits_installs_quietly_and_starts_the_new_copy(self):
        script = updater.helper_script(r"C:\Users\Kar'l\AppData\Local\Manticore\updates\Manticore-9.0.0-setup.exe",
                                       r"C:\Users\Kar'l\AppData\Local\Programs\Manticore\Manticore.exe", 4242)
        lines = script.splitlines()
        self.assertIn("Wait-Process -Id 4242 -Timeout 120", lines)
        install = next(ln for ln in lines if "-ArgumentList" in ln)
        self.assertIn("'/SILENT','/SUPPRESSMSGBOXES','/NORESTART'", install)
        self.assertIn("-Wait", install)
        self.assertIn(r"'C:\Users\Kar''l\AppData\Local\Manticore\updates\Manticore-9.0.0-setup.exe'", install)   # ' doubled
        self.assertEqual(lines[-1], r"Start-Process -FilePath 'C:\Users\Kar''l\AppData\Local\Programs\Manticore\Manticore.exe'")
        self.assertLess(lines.index("Wait-Process -Id 4242 -Timeout 120"), lines.index(install))
        self.assertEqual(base64.b64decode(updater.encoded(script)).decode("utf-16-le"), script)

    def test_launch_refuses_during_a_game_and_without_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            setup = os.path.join(d, "Manticore-9.0.0-setup.exe")
            self.assertEqual(updater.launch_installer(setup, game_running=True), (False, "Finish or leave the game first."))
            with testing_env():
                ok, why = updater.launch_installer(setup)
                self.assertFalse(ok)
                self.assertIn("missing", why)
            if os.name != "nt":
                put(setup, b"x")
                self.assertEqual(updater.launch_installer(setup, popen=mock.Mock())[0], False)   # Windows only for real

    def test_launch_starts_a_hidden_powershell_and_keeps_a_copy_of_what_it_said(self):
        with tempfile.TemporaryDirectory() as d, testing_env():
            setup = os.path.join(d, "Manticore-9.0.0-setup.exe")
            put(setup, b"x")
            popen = mock.Mock()
            self.assertEqual(updater.launch_installer(setup, exe=r"C:\P\Manticore.exe", pid=77, popen=popen), (True, None))
            cmd = popen.call_args[0][0]
            self.assertEqual(cmd[0], "powershell.exe")
            self.assertIn("Hidden", cmd)
            script = base64.b64decode(cmd[cmd.index("-EncodedCommand") + 1]).decode("utf-16-le")
            self.assertIn("Wait-Process -Id 77", script)
            with open(os.path.join(d, "last_update.ps1.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), script + "\n")
            popen.side_effect = OSError("no powershell")
            ok, why = updater.launch_installer(setup, exe=r"C:\P\Manticore.exe", pid=77, popen=popen)
            self.assertFalse(ok)
            self.assertIn("no powershell", why)

    def test_on_windows_the_helper_breaks_away_from_a_job_if_it_may(self):
        with tempfile.TemporaryDirectory() as d:
            setup = os.path.join(d, "Manticore-9.0.0-setup.exe")
            put(setup, b"x")
            popen = mock.Mock(side_effect=[OSError("Access is denied"), mock.Mock()])
            with mock.patch.object(updater.os, "name", "nt"):
                self.assertEqual(updater.launch_installer(setup, exe=r"C:\P\Manticore.exe", pid=77, popen=popen), (True, None))
            first, second = (c[1]["creationflags"] for c in popen.call_args_list)
            self.assertEqual(first, second | 0x01000000)                  # CREATE_BREAKAWAY_FROM_JOB, then without it
            self.assertEqual(second & 0x00000008, 0x00000008)             # DETACHED_PROCESS: no console window

    def test_the_installer_does_not_start_the_program_a_second_time(self):
        with open(os.path.join(BASE_DIR, "installer", "commander_sim.iss"), encoding="utf-8") as f:
            iss = f.read()
        run = iss.split("[Run]", 1)[1].split("[", 1)[0]
        self.assertIn("skipifsilent", run)          # /SILENT skips "Run Manticore": the helper starts it instead


# ---- the title screen's card ------------------------------------------------------------------------------------------
class FakeCheck:
    def __init__(self, info=None, error=None):
        self.info, self.error, self.finished = info, error, True


class FakeDownload:
    def __init__(self, info):
        self.info = info
        self.finished, self.cancelled = False, False
        self.path, self.error = None, None
        self.done_bytes = 0

    def start(self):
        return self

    def fraction(self):
        return 0.4

    def cancel(self):
        self.cancelled = True
        self.finished = True


class CardTests(TempDecks):
    def setUp(self):
        super().setUp()
        update_screens.reset_for_tests()
        self.info = updater.UpdateInfo("9.0.0", "https://github.com/o/r/releases/download/v9.0.0/M-9.0.0-setup.exe",
                                       "a" * 64, 150_000_000, "Online play with a friend.")
        self.check = FakeCheck(self.info)
        self.enabled = mock.patch.object(updater, "enabled", return_value=(True, "https://x/latest.json"))
        self.job = mock.patch.object(updater, "CheckJob", side_effect=lambda url: mock.Mock(start=lambda: self.check))
        self.enabled.start()
        self.job.start()
        self.work = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.enabled.stop()
        self.job.stop()
        update_screens.reset_for_tests()
        self.work.cleanup()
        super().tearDown()

    def menu_gui(self, size=(1360, 840), scale=1.0):
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=os.path.join(self.work.name, "settings.json"),
                            window_size=size, launcher=FakeLauncher(), deck_dirs=self.dirs)
        gui.text_scale = scale
        gui.open_boot(False)
        gui.boot.to_menu()
        frame(gui, 3)
        return gui

    def press(self, gui, name):
        rect = next(r for r, n in gui.boot.update.buttons if n == name)
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1))
        frame(gui, 1)

    def test_a_newer_version_shows_the_card_with_three_choices(self):
        gui = self.menu_gui()
        card = gui.boot.update
        self.assertEqual(card.state, "offer")
        self.assertEqual([n for _r, n in card.buttons], ["update", "later", "skip"])
        self.assertTrue(gui.screen.get_rect().contains(card.rect))
        self.assertFalse(any(card.rect.colliderect(r) for r, _n in gui.boot.buttons))       # clear of the menu row

    def test_later_hides_it_and_the_menu_still_works(self):
        gui = self.menu_gui()
        self.press(gui, "later")
        self.assertEqual(gui.boot.update.state, "hidden")
        self.assertEqual(gui.boot.update.buttons, [])
        gui = self.menu_gui()                      # one check per run of the program: a second title doesn't ask again
        self.assertEqual(gui.boot.update.state, "idle")

    def test_esc_is_later(self):
        gui = self.menu_gui()
        key(gui, pygame.K_ESCAPE)
        self.assertEqual(gui.boot.update.state, "hidden")
        self.assertIsNotNone(gui.boot)             # Esc only closed the card, not the title

    def test_skip_remembers_the_version_and_it_is_not_offered_again(self):
        gui = self.menu_gui()
        self.press(gui, "skip")
        self.assertEqual(gui.skip_update_version, "9.0.0")
        saved = get_json(gui.settings_path)
        self.assertEqual(saved["skip_update_version"], "9.0.0")
        update_screens.reset_for_tests()
        gui = self.menu_gui()
        self.assertEqual(gui.skip_update_version, "9.0.0")
        self.assertEqual(gui.boot.update.state, "hidden")
        self.check = FakeCheck(updater.UpdateInfo("9.0.1", self.info.url, "b" * 64, 150_000_000))
        update_screens.reset_for_tests()
        self.assertEqual(self.menu_gui().boot.update.state, "offer")          # a later version is offered again

    def test_update_now_downloads_then_installs_and_closes(self):
        gui = self.menu_gui()
        downloads = []
        with mock.patch.object(updater, "DownloadJob", side_effect=lambda info: downloads.append(FakeDownload(info)) or downloads[-1]):
            self.press(gui, "update")
        card = gui.boot.update
        self.assertEqual(card.state, "downloading")
        self.assertEqual([n for _r, n in card.buttons], ["cancel"])
        self.assertTrue(gui.boot.moving(time.monotonic()))                   # the bar keeps drawing
        play = next(r for r, n in gui.boot.buttons if n == "play")
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=play.center, button=1))
        self.assertIsNone(gui.menu)                                          # no Play while it downloads
        downloads[0].path, downloads[0].finished = os.path.join(self.work.name, "setup.exe"), True
        with mock.patch.object(updater, "launch_installer", return_value=(True, None)) as launch:
            frame(gui, 1)
        self.assertEqual(launch.call_args[0][0], os.path.join(self.work.name, "setup.exe"))
        self.assertEqual(launch.call_args[1], {"game_running": False})
        self.assertEqual(card.state, "installing")
        self.assertTrue(gui.running)
        card.closing_at = 0
        frame(gui, 1)
        self.assertFalse(gui.running)                                        # the helper waits for exactly this

    def test_cancel_goes_back_to_the_offer_and_a_failure_offers_try_again(self):
        gui = self.menu_gui()
        downloads = []
        with mock.patch.object(updater, "DownloadJob", side_effect=lambda info: downloads.append(FakeDownload(info)) or downloads[-1]):
            self.press(gui, "update")
            self.press(gui, "cancel")
            self.assertEqual(gui.boot.update.state, "offer")
            self.press(gui, "update")
            downloads[-1].error, downloads[-1].finished = "The download was damaged (its checksum didn't match).", True
            frame(gui, 1)
            card = gui.boot.update
            self.assertEqual(card.state, "failed")
            self.assertEqual([n for _r, n in card.buttons], ["update", "later"])
            self.press(gui, "update")
            self.assertEqual(card.state, "downloading")
        self.assertEqual(len(downloads), 3)

    def test_an_installer_that_cannot_start_is_said_and_the_program_stays(self):
        gui = self.menu_gui()
        card = gui.boot.update
        with mock.patch.object(updater, "launch_installer", return_value=(False, "Updating itself only works on Windows.")):
            card.install(gui, "x.exe")
        self.assertEqual((card.state, card.message), ("failed", "Updating itself only works on Windows."))
        self.assertTrue(gui.running)

    def test_never_during_a_game(self):
        gui = self.menu_gui()
        card = gui.boot.update
        with mock.patch.object(gui, "game_in_progress", return_value=True):
            with mock.patch.object(updater, "launch_installer", wraps=updater.launch_installer) as launch:
                card.install(gui, "x.exe")
        self.assertEqual(launch.call_args[1], {"game_running": True})
        self.assertEqual(card.state, "failed")

    def test_nothing_when_up_to_date_or_the_check_failed(self):
        for check in (FakeCheck(None), FakeCheck(None, "couldn't reach the update feed (ConnectionError)")):
            with self.subTest(error=check.error):
                self.check = check
                update_screens.reset_for_tests()
                with mock.patch.object(update_screens, "_note") as note:
                    gui = self.menu_gui()
                self.assertEqual(gui.boot.update.state, "hidden")
                self.assertEqual(note.called, bool(check.error))

    def test_off_means_no_check_at_all(self):
        self.enabled.stop()
        try:
            with mock.patch.object(updater, "enabled", return_value=(False, "turned off in Settings")):
                gui = self.menu_gui()
            self.assertEqual(gui.boot.update.state, "idle")
            self.assertIsNone(gui.boot.update.check_job)
        finally:
            self.enabled.start()

    def test_the_card_fits_small_windows_and_big_text(self):
        for size, scale in (((900, 600), 1.0), ((900, 600), 2.0), ((1360, 840), 1.5), ((1920, 1080), 2.0)):
            with self.subTest(size=size, scale=scale):
                update_screens.reset_for_tests()
                gui = self.menu_gui(size, scale)
                card = gui.boot.update
                screen = gui.screen.get_rect()
                self.assertTrue(screen.contains(card.rect), (card.rect, screen))
                for r, _n in card.buttons:
                    self.assertTrue(card.rect.contains(r), (r, card.rect))


class EndToEndTests(TempDecks):
    """The real check and the real download from a server on this computer, through the title screen's card, up to the point
    where the helper would start (launch_installer is replaced: it would run PowerShell)."""

    def test_title_card_to_installer(self):
        server = Server()
        work = tempfile.TemporaryDirectory()
        update_screens.reset_for_tests()
        try:
            data = os.urandom(updater.MIN_SIZE + 4321)
            server.routes["/Manticore-9.0.0-setup.exe"] = (200, data)
            feed = good_feed(url=server.url("/Manticore-9.0.0-setup.exe"), sha256=hashlib.sha256(data).hexdigest(),
                             size=len(data))
            server.routes["/latest.json"] = (200, json.dumps(feed).encode())
            with testing_env(MANTICORE_UPDATE_FEED=server.url("/latest.json")), \
                    mock.patch.object(updater, "updates_dir", return_value=work.name), \
                    mock.patch.object(updater, "launch_installer", return_value=(True, None)) as launch:
                gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=(1360, 840),
                                    launcher=FakeLauncher(), deck_dirs=self.dirs)
                gui.open_boot(False)
                gui.boot.to_menu()
                end = time.monotonic() + 10
                while time.monotonic() < end and (gui.boot.update is None or gui.boot.update.state in ("idle", "checking")):
                    frame(gui, 1)
                    time.sleep(0.02)
                card = gui.boot.update
                self.assertEqual(card.state, "offer")
                rect = next(r for r, n in card.buttons if n == "update")
                gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1))
                end = time.monotonic() + 20
                while time.monotonic() < end and card.state == "downloading":
                    frame(gui, 1)
                    time.sleep(0.02)
                self.assertEqual(card.state, "installing", card.message)
                path = launch.call_args[0][0]
                self.assertEqual(path, os.path.join(work.name, "Manticore-9.0.0-setup.exe"))
                self.assertEqual(updater.sha256_file(path), feed["sha256"])
        finally:
            update_screens.reset_for_tests()
            server.close()
            work.cleanup()


# ---- settings, the cog switch, --version -------------------------------------------------------------------------------
class SettingsTests(TempDecks):
    def gui(self, path):
        return ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=path, window_size=(1360, 840),
                             launcher=FakeLauncher(), deck_dirs=self.dirs)

    def test_the_cog_switch_turns_updates_off_and_on_and_is_remembered(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            gui = self.gui(path)
            self.assertTrue(gui.check_updates)
            gui.overlay = ft.fset.SettingsPopup()
            frame(gui, 1)
            pop = gui.overlay
            rect = next(r for r, n in pop.buttons if n == "updates")
            gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1))
            self.assertFalse(gui.check_updates)
            self.assertFalse(get_json(path)["check_updates"])
            self.assertFalse(self.gui(path).check_updates)
            with mock.patch.object(ft, "SETTINGS_FILE", path):
                self.assertFalse(ft._check_updates_setting())
            gui.toggle_check_updates()
            self.assertTrue(self.gui(path).check_updates)

    def test_a_bad_skip_version_is_ignored_and_clearing_it_removes_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            put(path, json.dumps({"skip_update_version": "next one"}))
            self.assertIsNone(self.gui(path).skip_update_version)
            put(path, json.dumps({"skip_update_version": "9.0.0"}))
            gui = self.gui(path)
            self.assertEqual(gui.skip_update_version, "9.0.0")
            gui.skip_update_version = None
            gui.save_settings()
            self.assertNotIn("skip_update_version", get_json(path))

    def test_version_says_whether_this_copy_updates(self):
        out = io.StringIO()
        with redirect_stdout(out):
            ft.main(["forge_table.py", "--version"])
        line = next(ln for ln in out.getvalue().splitlines() if ln.startswith("updates:"))
        self.assertTrue(line.startswith("updates: off"), line)


# ---- the release side ------------------------------------------------------------------------------------------------
class FeedToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.setup = os.path.join(self.tmp.name, "Manticore-%s-setup.exe" % version.VERSION)
        with open(self.setup, "wb") as f:
            f.write(b"M" * (updater.MIN_SIZE + 123))

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_writes_a_feed_an_installed_copy_accepts(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = muf.main([self.setup, "--repo", "karl/manticore-alpha", "--notes", 'Online "play".'])
        self.assertEqual(code, 0)
        feed = get_json(os.path.join(self.tmp.name, "latest.json"))
        info, why = updater.parse_feed(feed)
        self.assertIsNone(why)
        self.assertEqual(info.version, version.VERSION)
        self.assertEqual(info.size, updater.MIN_SIZE + 123)
        self.assertEqual(info.sha256, hashlib.sha256(b"M" * (updater.MIN_SIZE + 123)).hexdigest())
        self.assertEqual(feed["url"], "https://github.com/karl/manticore-alpha/releases/download/v%s/%s"
                         % (version.VERSION, os.path.basename(self.setup)))
        text = out.getvalue()
        self.assertIn("gh release create v%s" % version.VERSION, text)
        self.assertNotIn("--prerelease", text)
        self.assertIn("pre-release", text)                   # it warns not to

    def test_it_refuses_without_a_repository_or_for_another_version(self):
        with redirect_stdout(io.StringIO()):
            with mock.patch.object(muf, "repo_from_config", return_value=None):
                self.assertEqual(muf.main([self.setup]), 2)
            other = os.path.join(self.tmp.name, "Manticore-0.0.1-setup.exe")
            put(other, b"x" * (updater.MIN_SIZE + 1))
            self.assertEqual(muf.main([other, "--repo", "a/b"]), 2)
            self.assertEqual(muf.main([os.path.join(self.tmp.name, "nope.exe"), "--repo", "a/b"]), 2)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "latest.json")))


class BuildTests(unittest.TestCase):
    def test_check_dist_says_where_updates_come_from(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("no update_config.json", check_dist.update_source_note(d))
            path = os.path.join(d, "update_config.json")
            put(path, json.dumps({"repo": "", "feed_url": ""}))
            self.assertIn("off", check_dist.update_source_note(d))
            put(path, json.dumps({"repo": "karl/m"}))
            self.assertEqual(check_dist.update_source_note(d), "updates: on, from github.com/karl/m (Releases -> latest.json)")


if __name__ == "__main__":
    unittest.main()
