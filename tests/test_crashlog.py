# SPDX-License-Identifier: GPL-3.0-or-later
"""version.py (which copy is running) and crashlog.py (a file that says what went wrong), plus how the table's loop uses them."""
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crashlog
import version

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA = "0123456789abcdef0123456789abcdef01234567"


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8", "newline": ""})) as f:
        f.write(text)


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, head, refs=None, packed=None):
        write(os.path.join(self.tmp, ".git", "HEAD"), head)
        for name, sha in (refs or {}).items():
            write(os.path.join(self.tmp, ".git", *name.split("/")), sha + "\n")
        if packed:
            write(os.path.join(self.tmp, ".git", "packed-refs"), packed)

    def test_commit_from_a_branch_ref(self):
        self.git("ref: refs/heads/main\n", {"refs/heads/main": SHA})
        self.assertEqual(version.commit(self.tmp), SHA[:7])

    def test_commit_from_packed_refs(self):
        self.git("ref: refs/heads/main\n", packed=f"# pack-refs with: peeled\n{SHA} refs/heads/main\n")
        self.assertEqual(version.commit(self.tmp), SHA[:7])

    def test_commit_when_detached(self):
        self.git(SHA + "\n")
        self.assertEqual(version.commit(self.tmp), SHA[:7])

    def test_no_commit_without_a_backup_yet(self):
        self.assertIsNone(version.commit(self.tmp))
        self.git("ref: refs/heads/main\n")                          # a repo with no commit yet
        self.assertIsNone(version.commit(self.tmp))

    def test_garbage_in_git_files_is_not_a_commit(self):
        self.git("ref: refs/heads/main\n", {"refs/heads/main": "not a sha"})
        self.assertIsNone(version.commit(self.tmp))

    def test_fingerprint_changes_with_the_code_only(self):
        write(os.path.join(self.tmp, "a.py"), "x = 1\n")
        first = version.code_fingerprint(self.tmp)
        self.assertRegex(first, r"^[0-9a-f]{8}$")
        write(os.path.join(self.tmp, "notes.txt"), "not code")
        self.assertEqual(version.code_fingerprint(self.tmp), first)
        write(os.path.join(self.tmp, "a.py"), "x = 2\n")
        self.assertNotEqual(version.code_fingerprint(self.tmp), first)

    def test_fingerprint_ignores_windows_line_endings(self):
        write(os.path.join(self.tmp, "a.py"), "x = 1\ny = 2\n")
        unix = version.code_fingerprint(self.tmp)
        write(os.path.join(self.tmp, "a.py"), "x = 1\r\ny = 2\r\n")
        self.assertEqual(version.code_fingerprint(self.tmp), unix)

    def test_fingerprint_notices_a_new_file(self):
        write(os.path.join(self.tmp, "a.py"), "x = 1\n")
        first = version.code_fingerprint(self.tmp)
        write(os.path.join(self.tmp, "b.py"), "")
        self.assertNotEqual(version.code_fingerprint(self.tmp), first)

    def test_unreadable_folder_does_not_raise(self):
        self.assertEqual(version.code_fingerprint(os.path.join(self.tmp, "nope")), "unknown")

    def test_describe_and_short(self):
        write(os.path.join(self.tmp, "a.py"), "x = 1\n")
        text = version.describe(self.tmp)
        self.assertIn(version.VERSION, text)
        self.assertIn("commit none yet", text)
        self.assertIn(version.code_fingerprint(self.tmp), version.short(self.tmp))
        self.git("ref: refs/heads/main\n", {"refs/heads/main": SHA})
        self.assertIn(f"commit {SHA[:7]}", version.describe(self.tmp))

    def test_the_real_project_folder_can_be_described(self):
        self.assertRegex(version.describe(HERE), r"code [0-9a-f]{8}")


class LogBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_sys, self.orig_thread = sys.excepthook, threading.excepthook
        crashlog.install(self.tmp, dialog=False)

    def tearDown(self):
        crashlog.uninstall()
        sys.excepthook, threading.excepthook = self.orig_sys, self.orig_thread
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self):
        try:
            with open(os.path.join(self.tmp, crashlog.LOG_NAME), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def boom(self, msg="bad thing"):
        try:
            raise ValueError(msg)
        except ValueError as e:
            return e


class RecordTests(LogBase):
    def test_an_entry_has_everything_needed_to_reproduce_it(self):
        write(os.path.join(self.tmp, "forge_engine.log"), "line one\nline two\n")
        crashlog.set_context(lambda: ["Showing: the table", "Game: turn 4"])
        self.assertTrue(crashlog.record(self.boom("kaboom")))
        text = self.log()
        # patch 46: the log folder here is a temp folder, so the forge_runtime line describes the PROGRAM's forge_runtime (present
        # or "missing" depending on the machine) - it used to say "missing" because it looked in the log folder
        for want in ("ERROR: ValueError at test_crashlog.py:", "ValueError: kaboom", "Traceback", version.VERSION,
                     "code ", "Python " + sys.version.split()[0],
                     "Showing: the table", "Game: turn 4", "forge_runtime: ", "line two", "forge_engine.log"):
            self.assertIn(want, text)

    def test_the_same_error_is_written_once_and_counted_later(self):
        first = None
        for i in range(12):
            wrote = crashlog.record(self.boom())            # one raise site inside boom(): the same signature every time
            first = wrote if first is None else first
            if i > 0:
                self.assertFalse(wrote)
        text = self.log()
        self.assertEqual(text.count("Traceback"), 1)
        self.assertIn("10 times so far", text)

    def test_different_errors_are_each_written(self):
        crashlog.record(self.boom())
        try:
            {}["missing"]
        except KeyError as e:
            crashlog.record(e)
        self.assertEqual(self.log().count("Traceback"), 2)

    def test_note_writes_a_plain_entry(self):
        crashlog.note("SOMETHING", "it happened")
        self.assertIn("SOMETHING", self.log())
        self.assertIn("it happened", self.log())

    def test_a_broken_context_function_is_reported_not_raised(self):
        def bad():
            raise RuntimeError("state is broken")
        crashlog.set_context(bad)
        self.assertTrue(crashlog.record(self.boom()))
        self.assertIn("could not describe the game state", self.log())

    def test_an_unwritable_folder_never_raises(self):
        crashlog.install(os.path.join(self.tmp, "no", "such", "folder"), dialog=False)
        self.assertFalse(crashlog.record(self.boom()))
        self.assertFalse(crashlog.note("x"))

    def test_a_huge_log_is_moved_aside_not_grown_forever(self):
        write(os.path.join(self.tmp, crashlog.LOG_NAME), "x" * (crashlog.MAX_LOG_BYTES + 10))
        crashlog.record(self.boom())
        self.assertTrue(os.path.exists(os.path.join(self.tmp, crashlog.OLD_NAME)))
        self.assertLess(len(self.log()), 20000)
        self.assertIn("ValueError", self.log())

    def test_long_engine_lines_are_cut_and_only_the_end_of_the_file_is_read(self):
        write(os.path.join(self.tmp, "forge_engine.log"), "old line\n" * 5000 + "y" * 1000 + "\nlast line\n")
        tail = crashlog.engine_tail(self.tmp)
        self.assertEqual(tail[-1], "last line")
        self.assertLessEqual(len(tail), crashlog.ENGINE_TAIL_LINES)
        self.assertTrue(all(len(r) <= 300 for r in tail))


class HookTests(LogBase):
    def test_uncaught_error_is_logged_and_still_printed(self):
        printed = []
        crashlog._hooks["sys"] = lambda *a: printed.append(a)       # stands in for Python's own printing of the traceback
        e = self.boom("fatal one")
        sys.excepthook(type(e), e, e.__traceback__)
        self.assertIn("CRASH - the program closed", self.log())
        self.assertIn("fatal one", self.log())
        self.assertEqual(len(printed), 1)

    def test_a_crash_is_always_written_even_if_it_was_logged_before(self):
        e = self.boom("again")
        crashlog.record(e)
        sys.excepthook = crashlog._excepthook
        crashlog._hooks["sys"] = lambda *a: None
        sys.excepthook(type(e), e, e.__traceback__)
        self.assertEqual(self.log().count("Traceback"), 2)

    def test_ctrl_c_is_not_a_crash(self):
        crashlog._hooks["sys"] = lambda *a: None
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        self.assertEqual(self.log(), "")

    def test_a_helper_thread_that_dies_is_logged_and_the_program_carries_on(self):
        seen = []
        crashlog._hooks["thread"] = seen.append
        t = threading.Thread(target=lambda: 1 / 0, name="art-downloader")
        t.start()
        t.join()
        self.assertIn("THREAD 'art-downloader' stopped", self.log())
        self.assertIn("ZeroDivisionError", self.log())
        self.assertEqual(len(seen), 1)

    def test_no_dialog_when_it_is_switched_off(self):
        self.assertFalse(crashlog.show_dialog("t", "text"))

    def test_install_twice_is_harmless_and_uninstall_restores_the_hooks(self):
        crashlog.install(self.tmp, dialog=False)
        crashlog.uninstall()
        self.assertIs(sys.excepthook, self.orig_sys)
        self.assertIs(threading.excepthook, self.orig_thread)
        crashlog.install(self.tmp, dialog=False)                    # so tearDown has something to undo


class NativeCrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = sys.excepthook, threading.excepthook

    def tearDown(self):
        crashlog.uninstall()
        sys.excepthook, threading.excepthook = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_hard_crash_report_left_by_the_last_run_is_moved_into_the_log(self):
        native = os.path.join(self.tmp, crashlog.NATIVE_NAME)
        write(native, "Windows fatal exception: access violation\n\nCurrent thread 0x1 (most recent call first):\n  File x.py\n")
        crashlog.install(self.tmp, dialog=False)
        with open(os.path.join(self.tmp, crashlog.LOG_NAME), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("HARD CRASH in the previous run", text)
        self.assertIn("access violation", text)
        self.assertEqual(os.path.getsize(native), 0)                 # not reported a second time at the next start

    def test_a_clean_run_leaves_no_native_file_behind(self):
        crashlog.install(self.tmp, dialog=False)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, crashlog.NATIVE_NAME)))
        crashlog.uninstall()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, crashlog.NATIVE_NAME)))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, crashlog.LOG_NAME)))


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class WatchdogTests(LogBase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.dog = crashlog.Watchdog(limit=30, clock=self.clock)

    def test_quiet_while_the_window_keeps_responding(self):
        for _ in range(5):
            self.clock.t += 10
            self.dog.beat()
            self.assertFalse(self.dog.check())
        self.assertEqual(self.log(), "")

    def test_a_freeze_is_written_once_with_what_each_thread_was_doing(self):
        self.clock.t += 31
        self.assertTrue(self.dog.check())
        self.assertFalse(self.dog.check())                           # not again every second
        text = self.log()
        self.assertIn("FROZEN - the window has not responded for 31 seconds", text)
        self.assertIn("main thread (the window)", text)
        self.assertIn("test_a_freeze_is_written_once", text)         # the main thread's stack names this very test
        self.assertEqual(text.count("FROZEN"), 1)

    def test_it_says_when_the_window_came_back_and_can_report_a_second_freeze(self):
        self.clock.t += 45
        self.dog.check()
        self.clock.t += 5
        self.dog.beat()
        self.assertIn("responded again", self.log())
        self.assertIn("about 50 seconds", self.log())
        self.clock.t += 40
        self.assertTrue(self.dog.check())

    def test_the_background_thread_reports_a_real_freeze_and_stops_cleanly(self):
        dog = crashlog.Watchdog(limit=0.05, poll=0.02).start()
        deadline = time.time() + 5
        while "FROZEN" not in self.log() and time.time() < deadline:
            time.sleep(0.02)
        dog.stop()
        self.assertIn("FROZEN", self.log())
        self.assertFalse(dog._thread.is_alive())


class TableLoopTests(LogBase):
    """ForgeTable.run(): one bad frame is survived, a permanent problem ends the game with a report, and shutdown always runs."""

    def make(self):
        from tests.test_forge_table import make_gui
        gui = make_gui()
        self.shutdowns = []
        gui.shutdown = lambda: self.shutdowns.append(1)             # the real one calls pygame.quit(), which other tests need alive
        self.events = []
        return gui

    def test_an_error_in_one_frame_is_logged_and_the_game_goes_on(self):
        gui = self.make()
        real, calls = gui.render, []

        def flaky():
            calls.append(1)
            if len(calls) == 2:
                raise ValueError("one bad frame")
            if len(calls) >= 6:
                gui.running = False
            real()
        gui.render = flaky
        gui.run()
        self.assertGreaterEqual(len(calls), 6)                       # it kept going after the error
        self.assertIn("ERROR in the game loop: ValueError", self.log())
        self.assertIn("one bad frame", self.log())
        self.assertIn("Showing: the table", self.log())              # the table described itself
        self.assertIn("Game: turn", self.log())
        self.assertEqual(self.shutdowns, [1])
        self.assertIsNotNone(gui.toast)                              # the player is told something went wrong
        self.assertIn("crash_log.txt", gui.toast[0])

    def test_the_same_error_every_frame_ends_the_game_and_still_shuts_down(self):
        gui = self.make()
        gui.render = lambda: 1 / 0
        with self.assertRaises(ZeroDivisionError):
            gui.run()
        self.assertEqual(self.shutdowns, [1])
        self.assertEqual(self.log().count("Traceback"), 1)           # 60 repeats, one full entry

    def test_a_normal_quit_writes_nothing(self):
        gui = self.make()
        gui.running = False
        gui.run()
        self.assertEqual(self.shutdowns, [1])
        self.assertEqual(self.log(), "")

    def test_crash_context_copes_with_no_game_and_with_the_deck_screen(self):
        gui = self.make()
        gui.session.state = None
        text = "\n".join(gui.crash_context())
        self.assertIn("Game: none running", text)
        gui.menu = object()
        self.assertIn("the deck screen", "\n".join(gui.crash_context()))

    def test_the_window_title_names_the_version(self):
        self.make()
        import pygame
        self.assertIn(version.VERSION, pygame.display.get_caption()[0])


class CommandLineTests(unittest.TestCase):
    def test_version_flag_prints_and_stops_without_starting_anything(self):
        import contextlib
        import forge_table as ft
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ft.main(["forge_table.py", "--version"])
        self.assertIn(version.VERSION, out.getvalue())
        self.assertIn("code ", out.getvalue())


class ProjectFilesTests(unittest.TestCase):
    def test_requirements_are_pinned_exactly(self):
        with open(os.path.join(HERE, "requirements.txt"), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        self.assertTrue(lines)
        for ln in lines:
            self.assertRegex(ln, r"^[A-Za-z0-9_.\-]+==\d[\w.]*$", ln)
        names = {ln.split("==")[0].lower() for ln in lines}
        self.assertIn("pygame-ce", names)
        self.assertIn("requests", names)

    def test_pinned_versions_match_what_the_tests_run_on(self):
        import pygame
        with open(os.path.join(HERE, "requirements.txt"), encoding="utf-8") as f:
            pins = dict(ln.strip().split("==") for ln in f if "==" in ln and not ln.startswith("#"))
        self.assertEqual(pins["pygame-ce"], pygame.version.ver)

    def test_crash_files_are_not_backed_up_to_github_or_zipped(self):
        import backup
        with open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read().split()
        for name in (crashlog.LOG_NAME, crashlog.OLD_NAME, crashlog.NATIVE_NAME):
            self.assertIn(name, ignored)
            self.assertIn(name, backup.SKIP_FILES)


if __name__ == "__main__":
    unittest.main()
