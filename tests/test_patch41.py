# SPDX-License-Identifier: GPL-3.0-or-later
"""
Patch 41: the self-update starts the installer itself (no PowerShell helper), and the next start says what became of it.

On Karl's Surface Pro 11 (Windows 11 on Arm) the 0.28.41 copy offered 0.28.43 and then 0.28.44, downloaded them (size and
checksum right), closed itself - and nothing else happened: no installer window, no new version, Manticore never reopened.
Round 30's hidden PowerShell helper set $ErrorActionPreference = 'SilentlyContinue' and kept no log, so there was no trace of
why. Its last line starts Manticore.exe whatever happens before it, and even that never ran: it died, or never got going.
Now:
  * updater.launch_installer runs the installer (Inno Setup) directly: /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
    /UPDATE /LOG="<updates>\\install.log"; the .iss closes what still holds a file (CloseApplications=force) and starts the new
    Manticore after a silent /UPDATE (its SilentSelfUpdate [Run] line);
  * if that start fails, the installer's own window is opened instead (os.startfile) for the person to click through;
  * updates\\pending_update.json + last_attempt(): the next start knows whether the update finished; a crash-log entry either
    way (install.log's last lines when it didn't), and the next Update now opens the installer's own window.
NOT tested here (needs Windows): Inno's /CLOSEAPPLICATIONS and /LOG, the [Run] line, the [Code] function (compiled by iscc).
"""
import json
import os
import re
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import forge_client as fc
import forge_table as ft
import update_screens
import updater
import version
from tests.forge_fake import StubStore
from tests.test_deck_screen import FakeLauncher, TempDecks
from tests.test_forge_table import frame

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def testing_env():
    return mock.patch.dict(os.environ, {"MANTICORE_UPDATE_TEST": "1"})


def read(*parts):
    with open(os.path.join(BASE_DIR, *parts), encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def pending(folder):
    with open(os.path.join(folder, updater.PENDING_NAME), encoding="utf-8") as f:
        return json.load(f)


class CommandTests(unittest.TestCase):
    # A made-up user with a space in the name (the point of the test). Never a real one: tools/export_public.py refuses to export a file that
    # holds the home folder path of the computer it runs on, so a real name here fails tests/test_pub1.py on that one computer.
    SETUP = r"C:\Users\Some User\AppData\Local\Manticore\updates\Manticore-0.28.46-setup.exe"
    LOG = r"C:\Users\Some User\AppData\Local\Manticore\updates\install.log"

    def test_a_silent_update_quotes_the_paths_with_spaces_and_asks_inno_to_close_and_reopen(self):
        cmd = updater.setup_command(self.SETUP, self.LOG)
        self.assertEqual(cmd, '"%s" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATE /LOG="%s"'
                         % (self.SETUP, self.LOG))

    def test_a_visible_one_keeps_only_update_and_the_log(self):
        cmd = updater.setup_command(self.SETUP, self.LOG, visible=True)
        self.assertEqual(cmd, '"%s" /UPDATE /LOG="%s"' % (self.SETUP, self.LOG))
        self.assertNotIn("/SILENT", cmd)

    def test_no_powershell_anywhere_in_the_updater(self):
        src = read("updater.py").lower()
        code = src.split('"""', 2)[2]                                      # past the module docstring (which tells the history)
        for word in ("powershell", "encodedcommand", "wait-process", "start-process"):
            self.assertNotIn(word, code)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.setup = os.path.join(self.tmp.name, "Manticore-9.0.0-setup.exe")
        write(self.setup, "x")
        self.env = testing_env()
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_a_start_writes_what_is_pending_and_clears_the_last_log(self):
        write(os.path.join(self.tmp.name, updater.INSTALL_LOG), "an older attempt")
        popen = mock.Mock()
        self.assertEqual(updater.launch_installer(self.setup, to_version="9.0.0", popen=popen), (True, None))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, updater.INSTALL_LOG)))
        rec = pending(self.tmp.name)
        self.assertEqual((rec["from"], rec["to"], rec["how"]), (version.VERSION, "9.0.0", "silent"))
        self.assertIn("in_job", rec)
        self.assertIn('/LOG="%s"' % os.path.join(self.tmp.name, updater.INSTALL_LOG), popen.call_args[0][0])

    def test_visible_mode_runs_the_installer_without_silent(self):
        popen = mock.Mock()
        self.assertEqual(updater.launch_installer(self.setup, to_version="9.0.0", visible=True, popen=popen), (True, None))
        self.assertNotIn("/SILENT", popen.call_args[0][0])
        self.assertEqual(pending(self.tmp.name)["how"], "visible")

    def test_when_it_cannot_be_started_the_installers_own_window_is_opened(self):
        popen = mock.Mock(side_effect=OSError("blocked"))
        startfile = mock.Mock()
        with mock.patch.object(updater.os, "name", "nt"), mock.patch.object(updater, "in_job", return_value=None):
            ok, why = updater.launch_installer(self.setup, to_version="9.0.0", popen=popen, startfile=startfile)
        self.assertEqual((ok, why), (True, updater.MANUAL))
        self.assertEqual(popen.call_count, 2)                              # with and without breaking away from a job
        startfile.assert_called_once_with(self.setup)
        self.assertEqual(pending(self.tmp.name)["how"], "manual")
        with open(os.path.join(self.tmp.name, updater.COMMAND_NAME), encoding="utf-8") as f:
            self.assertIn("started: manual", f.read())

    def test_nothing_started_leaves_nothing_pending_but_says_why_in_the_command_file(self):
        popen = mock.Mock(side_effect=OSError("blocked"))
        ok, why = updater.launch_installer(self.setup, popen=popen, startfile=mock.Mock(side_effect=OSError("no")))
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, updater.PENDING_NAME)))
        with open(os.path.join(self.tmp.name, updater.COMMAND_NAME), encoding="utf-8") as f:
            self.assertIn("NOT started", f.read())


class LastAttemptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, to="0.28.46", **more):
        rec = {"from": "0.28.45", "to": to, "how": "silent", "at": "2026-10-05 10:00:00", "in_job": False}
        rec.update(more)
        write(os.path.join(self.d, updater.PENDING_NAME), json.dumps(rec))

    def test_nothing_pending(self):
        self.assertIsNone(updater.last_attempt(self.d, current="0.28.45"))

    def test_a_finished_update_is_ok_and_forgotten_with_its_installer(self):
        self.record()
        setup = os.path.join(self.d, "Manticore-0.28.46-setup.exe")
        write(setup, "x")
        write(os.path.join(self.d, updater.INSTALL_LOG), "Installation process succeeded.\n")
        last = updater.last_attempt(self.d, current="0.28.46")
        self.assertTrue(last["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.d, updater.PENDING_NAME)))
        self.assertFalse(os.path.exists(setup))                                   # ~130 MB not kept once it's installed
        self.assertTrue(os.path.exists(os.path.join(self.d, updater.INSTALL_LOG)))   # the log stays until the next update
        self.assertIsNone(updater.last_attempt(self.d, current="0.28.46"))

    def test_one_that_did_not_finish_brings_the_log_and_is_kept_marked(self):
        self.record()
        write(os.path.join(self.d, updater.INSTALL_LOG), "\n".join("line %d" % i for i in range(40)) + "\n")
        last = updater.last_attempt(self.d, current="0.28.45")
        self.assertFalse(last["ok"])
        self.assertFalse(last.get("noted"))
        self.assertEqual(last["log"], ["line %d" % i for i in range(40 - updater.LOG_TAIL_LINES, 40)])
        setup = os.path.join(self.d, "Manticore-0.28.46-setup.exe")
        write(setup, "x")
        again = updater.last_attempt(self.d, current="0.28.45")
        self.assertTrue(os.path.exists(setup))                                    # kept: Update now reuses it
        self.assertTrue(again["noted"])                                    # its crash-log entry is written once
        self.assertTrue(pending(self.d)["failed"])

    def test_the_log_tail_reads_only_the_end_in_utf8_or_utf16(self):
        path = os.path.join(self.d, updater.INSTALL_LOG)
        lines = ["Dest filename: C:\\P\\forge_runtime\\res\\cardsfolder\\%05d.txt" % i for i in range(5000)]
        lines.append("Installation process succeeded.")
        for enc, bom in (("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe")):
            with self.subTest(enc=enc, bom=bom):
                with open(path, "wb") as f:
                    f.write(bom + "\r\n".join(lines).encode(enc))
                tail = updater.log_tail(path, 3)
                self.assertEqual(tail[-1], "Installation process succeeded.")
                self.assertEqual(tail[0], lines[-3])
        self.assertEqual(updater.log_tail(os.path.join(self.d, "none.log")), [])

    def test_a_broken_record_is_dropped(self):
        write(os.path.join(self.d, updater.PENDING_NAME), "{not json")
        self.assertIsNone(updater.last_attempt(self.d))
        self.record(to="")
        self.assertIsNone(updater.last_attempt(self.d))
        self.assertFalse(os.path.exists(os.path.join(self.d, updater.PENDING_NAME)))


class CrashLogTests(unittest.TestCase):
    def tearDown(self):
        update_screens.reset_for_tests()

    def settle(self, last):
        with mock.patch.object(updater, "last_attempt", return_value=last), \
                mock.patch.object(update_screens, "_note") as note:
            got = update_screens._settle_last_attempt()
        return got, note

    def test_finished_is_one_line(self):
        _got, note = self.settle({"from": "0.28.45", "to": "0.28.46", "how": "silent", "ok": True, "log": []})
        self.assertEqual(note.call_args[0][0], "Update finished")
        self.assertIn("0.28.45 -> 0.28.46", note.call_args[0][1])

    def test_not_finished_carries_the_install_log(self):
        _got, note = self.settle({"to": "0.28.46", "how": "silent", "at": "x", "in_job": True, "ok": False,
                                  "log": ["Line A", "Fatal exception during installation"]})
        title, text = note.call_args[0]
        self.assertEqual(title, "Update didn't finish")
        self.assertIn("Fatal exception during installation", text)
        self.assertIn("in a job: True", text)

    def test_once_only(self):
        _got, note = self.settle({"to": "0.28.46", "ok": False, "noted": True, "log": []})
        note.assert_not_called()


class CardTests(TempDecks):
    def setUp(self):
        super().setUp()
        update_screens.reset_for_tests()
        self.info = updater.UpdateInfo("9.0.0", "https://github.com/o/r/releases/download/v9.0.0/M-9.0.0-setup.exe",
                                       "a" * 64, 150_000_000, "Online play with a friend.")
        check = mock.Mock(info=self.info, error=None, finished=True)
        self.patches = [mock.patch.object(updater, "enabled", return_value=(True, "https://x/latest.json")),
                        mock.patch.object(updater, "CheckJob", side_effect=lambda url: mock.Mock(start=lambda: check))]
        for p in self.patches:
            p.start()
        self.work = tempfile.TemporaryDirectory()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        update_screens.reset_for_tests()
        self.work.cleanup()
        super().tearDown()

    def gui(self, last, size=(1360, 840), scale=1.0):
        with mock.patch.object(updater, "last_attempt", return_value=last), mock.patch.object(update_screens, "_note"):
            gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(),
                                settings_path=os.path.join(self.work.name, "settings.json"), window_size=size,
                                launcher=FakeLauncher(), deck_dirs=self.dirs)
            gui.text_scale = scale
            gui.open_boot(False)
            gui.boot.to_menu()
            frame(gui, 3)
        return gui

    FAILED = {"to": "9.0.0", "ok": False, "log": []}

    def test_after_one_that_did_not_finish_the_offer_says_so_and_opens_the_installers_window(self):
        gui = self.gui(self.FAILED)
        card = gui.boot.update
        self.assertEqual(card.state, "offer")
        self.assertTrue(card.retry_visible())
        with mock.patch.object(updater, "launch_installer", return_value=(True, None)) as launch:
            card.install(gui, "x.exe")
        self.assertTrue(launch.call_args[1]["visible"])
        self.assertEqual(card.state, "installing")

    def test_a_finished_or_no_earlier_update_stays_silent(self):
        for last in (None, {"to": "8.0.0", "ok": True, "log": []}):
            with self.subTest(last=last):
                update_screens.reset_for_tests()
                card = self.gui(last).boot.update
                self.assertFalse(card.retry_visible())

    def test_the_manual_fallback_says_follow_the_installer_and_closes_a_little_later(self):
        gui = self.gui(None)
        card = gui.boot.update
        with mock.patch.object(updater, "launch_installer", return_value=(True, updater.MANUAL)), \
                mock.patch.object(update_screens, "_note"):
            card.install(gui, "x.exe")
        self.assertEqual((card.state, card.message), ("installing", updater.MANUAL))
        self.assertGreater(card.closing_at - time.monotonic(), 2.0)
        frame(gui, 1)
        self.assertTrue(gui.running)

    def test_the_card_with_the_extra_line_fits_small_windows_and_big_text(self):
        for size, scale in (((900, 600), 1.0), ((900, 600), 2.0), ((1360, 840), 1.5), ((1920, 1080), 2.0)):
            with self.subTest(size=size, scale=scale):
                update_screens.reset_for_tests()
                gui = self.gui(self.FAILED, size, scale)
                card = gui.boot.update
                self.assertTrue(gui.screen.get_rect().contains(card.rect), (card.rect, size, scale))
                for r, _n in card.buttons:
                    self.assertTrue(card.rect.contains(r), (r, card.rect))


class InstallerScriptTests(unittest.TestCase):
    def setUp(self):
        self.iss = read("installer", "commander_sim.iss")

    def setting(self, key):
        m = re.search(r"^%s=(.*)$" % key, self.iss, re.M)
        return m.group(1).strip() if m else None

    def test_it_closes_what_holds_a_file_and_logs(self):
        self.assertEqual(self.setting("CloseApplications"), "force")
        self.assertEqual(self.setting("CloseApplicationsFilter").split(","), ["*.exe", "*.dll", "*.pyd", "*.jar"])
        self.assertEqual(self.setting("SetupLogging"), "yes")

    def test_a_silent_self_update_starts_the_new_copy_and_nothing_else_does_twice(self):
        run = self.iss.split("[Run]", 1)[1].split("[UninstallDelete]", 1)[0]
        lines = [ln for ln in run.splitlines() if ln.startswith("Filename:")]
        relaunch = [ln for ln in lines if "Check: SilentSelfUpdate" in ln]
        self.assertEqual(len(relaunch), 1)
        self.assertIn(r'Filename: "{app}\Manticore.exe"', relaunch[0])
        self.assertIn("nowait", relaunch[0])
        self.assertNotIn("postinstall", relaunch[0])                      # runs in silent mode too
        for ln in lines:
            if ln not in relaunch:
                self.assertIn("skipifsilent", ln)                          # so a silent update starts exactly one copy

    def test_the_check_function_reads_the_switches_the_updater_passes(self):
        code = self.iss.split("[Code]", 1)[1]
        self.assertLess(code.index("function HasSwitch"), code.index("function SilentSelfUpdate"))   # Pascal: declared first
        body = code.split("function SilentSelfUpdate", 1)[1].split("end;", 1)[0]
        self.assertIn("HasSwitch('/UPDATE')", body)
        self.assertIn("HasSwitch('/SILENT')", body)
        self.assertIn("/UPDATE", updater.SETUP_SWITCHES)
        self.assertIn("/SILENT", updater.SETUP_SWITCHES)
        self.assertIn("/UPDATE", updater.VISIBLE_SWITCHES)
        self.assertNotIn("/SILENT", updater.VISIBLE_SWITCHES)


class VersionTests(unittest.TestCase):
    def test_version(self):
        """At least 0.28.45 (patch 42 made it "at least": an exact version failed as soon as the next patch raised it)."""
        self.assertGreaterEqual(tuple(int(p) for p in version.VERSION.split(".")), (0, 28, 45))


if __name__ == "__main__":
    unittest.main()
