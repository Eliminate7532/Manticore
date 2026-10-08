# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 46 (7 Oct 2026): Java's own crash reports (soak night 14, game 75; claude/SOAK_NIGHT14_2026-10-07.md).

Game 75's Java died with EXCEPTION_ACCESS_VIOLATION in JIT-compiled Forge code. Java wrote hs_err_pid24052.log into its working
folder, forge_runtime/ - where tools/build_installer.py would have copied it into the next installer (it holds the PC's user name,
PATH and temp folder), where no soak report or F8 report looked, and the table's message pointed at forge_engine.log instead.

  FlagTests        the bridge's Java writes its crash files beside forge_engine.log, never a core dump; the folder is made first
  StrayTests       crash files left in forge_runtime/ move to the log folder at the next start, nothing else moves
  CrashReportTests ForgeSession.crash_report(): only once Java is gone, only this engine's (a reused pid's old file doesn't count)
  ReadTests        summary() / excerpt() on Windows- and Linux-shaped reports; no environment variables in an excerpt
  ReportTests      a bug report carries the excerpt; the F8 window passes it; the "Forge stopped" message names the file
  SoakTests        the soak's fatal finding says what crashed and where
  ShipTests        the installer build skips crash files, check_dist fails on one, git ignores them, the public copy leaves them out
  HeaderTests      a crash-log entry or report written into a soak / test folder describes the program, not that folder
  LiveCrashTests   a real Java crash (a stand-in bridge that writes to address 0): the report lands in the log folder (needs a JDK)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import crashlog
import forge_client as fc
import java_crash
import paths
import reporting
import version

HOME = os.path.expanduser("~")

# A Windows crash report shaped like game 75's (the real one is not in the repository: it holds Karl's PC's details). The
# "Environment Variables" block is put early on purpose, so the excerpt has to cut it rather than lose it to the length limit.
WINDOWS_REPORT = textwrap.dedent(f"""\
    #
    # A fatal error has been detected by the Java Runtime Environment:
    #
    #  EXCEPTION_ACCESS_VIOLATION (0xc0000005) at pc=0x0000022d228dd8a5, pid=24052, tid=21656
    #
    # JRE version: OpenJDK Runtime Environment Temurin-21.0.12.1+1 (21.0.12.1+1) (build 21.0.12.1+1-LTS)
    # Problematic frame:
    # J 9459 c2 forge.util.collect.FCollection.<init>(Ljava/lang/Iterable;)V (47 bytes) @ 0x0000022d228dd8a5 [0x0000022d228dd3a0+0x505]
    #

    ---------------  S U M M A R Y ------------

    Command Line: -Xmx3072m forge.bridge.Main --deck {HOME}\\AppData\\Local\\Temp\\soak_decks_x\\mine.dck --name Soak

    Environment Variables:
    JAVA_HOME=C:\\Program Files\\Eclipse Adoptium\\jdk-21.0.12.101-hotspot
    PATH={HOME}\\AppData\\Local\\Programs\\Python\\Python314\\;C:\\Program Files\\Git\\cmd
    USERNAME=Jane Q Tester
    COMPUTERNAME=JANES-DESKTOP
    TEMP={HOME}\\AppData\\Local\\Temp

    ---------------  T H R E A D  ---------------

    Current thread (0x0000022d7711eef0):  JavaThread "Game-0"    daemon [_thread_in_Java, id=21656]
    USERDOMAIN=JANES-DESKTOP
    """) + "".join(f"register line {i}\n" for i in range(600)) + textwrap.dedent("""\
    ---------------  S Y S T E M  ---------------

    OS:
     Windows 11 , 64 bit Build 26100 (10.0.26100.9549)
    CPU: total 16 (initial active 16) (16 cores per cpu, 2 threads per core) family 23 model 1 stepping 1
    """)

LINUX_REPORT = textwrap.dedent("""\
    #
    # A fatal error has been detected by the Java Runtime Environment:
    #
    #  SIGSEGV (0xb) at pc=0x00007ff395fc91d4, pid=67, tid=68
    #
    # Problematic frame:
    # V  [libjvm.so+0xfc91d4]  Unsafe_PutLong+0xa4
    #
    Current thread (0x00007ff38c02a1f0):  JavaThread "main" [_thread_in_vm, id=68]
    """)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p46_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.logs = os.path.join(self.tmp, "logs")
        self.runtime = os.path.join(self.tmp, "forge_runtime")
        os.makedirs(self.runtime)

    def session(self):
        s = fc.ForgeSession(write(os.path.join(self.tmp, "mine.dck"), "[Commander]\n1 Kinnan\n"), [], runtime=self.runtime,
                            fmt="commander")
        s.stderr_path = os.path.join(self.logs, "forge_engine.log")
        self.addCleanup(self._close, s)
        return s

    @staticmethod
    def _close(s):
        for f in (getattr(s, "_err", None), getattr(getattr(s, "proc", None), "stdout", None),
                  getattr(getattr(s, "proc", None), "stdin", None)):
            try:
                f.close()
            except Exception:
                pass


class FlagTests(Base):
    def test_both_files_go_to_the_folder_and_no_core_dump(self):
        flags = java_crash.jvm_flags(self.logs)
        self.assertIn("-XX:ErrorFile=" + os.path.join(self.logs, "hs_err_pid%p.log"), flags)
        self.assertIn("-XX:ReplayDataFile=" + os.path.join(self.logs, "replay_pid%p.log"), flags)
        self.assertIn("-XX:-CreateCoredumpOnCrash", flags)

    def test_the_bridge_command_has_them_before_the_class_path(self):
        s = self.session()
        s.crash_dir = s.crash_folder()
        cmd = s.java_command("java", "CP")
        cp_at = cmd.index("-cp")
        error_at = next(i for i, a in enumerate(cmd) if a.startswith("-XX:ErrorFile="))
        self.assertLess(error_at, cp_at)
        self.assertEqual(cmd[cp_at + 2], fc.BRIDGE_MAIN)
        self.assertIn(os.path.abspath(self.logs), cmd[error_at])

    def test_the_folder_is_the_engine_logs_and_is_made(self):
        s = self.session()
        self.assertFalse(os.path.isdir(self.logs))
        self.assertEqual(s.crash_folder(), os.path.abspath(self.logs))
        self.assertTrue(os.path.isdir(self.logs))    # Java falls back to forge_runtime/ when the folder doesn't exist

    def test_start_moves_strays_and_passes_the_flags(self):
        stray = write(os.path.join(self.runtime, "hs_err_pid1.log"), "old crash\n")
        seen = {}

        class Proc:
            pid, stdout = 99, iter(())

            def poll(self):
                return 0

        def popen(cmd, **kw):
            seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
            return Proc()
        s = self.session()
        with mock.patch.object(fc, "runtime_problem", return_value=None), \
                mock.patch.object(fc, "find_java", return_value="java"), \
                mock.patch.object(fc.subprocess, "Popen", side_effect=popen):
            s.start()
        self.assertFalse(os.path.exists(stray))
        self.assertTrue(os.path.isfile(os.path.join(self.logs, "hs_err_pid1.log")))
        self.assertTrue(any(a.startswith("-XX:ErrorFile=") for a in seen["cmd"]))
        self.assertEqual(seen["cwd"], self.runtime)       # the working folder is unchanged


class StrayTests(Base):
    def test_only_crash_files_move(self):
        for name in ("hs_err_pid7.log", "replay_pid8.log", "java.mdmp"):
            write(os.path.join(self.runtime, name), name)
        write(os.path.join(self.runtime, "forge.jar"), "jar")
        write(os.path.join(self.runtime, "VERSION.txt"), "Forge")
        moved = java_crash.move_strays(self.runtime, self.logs)
        self.assertEqual(sorted(os.path.basename(p) for p in moved), ["hs_err_pid7.log", "java.mdmp", "replay_pid8.log"])
        self.assertEqual(sorted(os.listdir(self.runtime)), ["VERSION.txt", "forge.jar"])

    def test_a_taken_name_gets_a_number(self):
        write(os.path.join(self.logs, "hs_err_pid7.log"), "first")
        write(os.path.join(self.runtime, "hs_err_pid7.log"), "second")
        moved = java_crash.move_strays(self.runtime, self.logs)
        self.assertEqual([os.path.basename(p) for p in moved], ["hs_err_pid7_2.log"])
        with open(os.path.join(self.logs, "hs_err_pid7.log"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "first")

    def test_harmless_cases(self):
        self.assertEqual(java_crash.move_strays(self.runtime, self.runtime), [])
        self.assertEqual(java_crash.move_strays(os.path.join(self.tmp, "nope"), self.logs), [])
        self.assertEqual(java_crash.move_strays(None, self.logs), [])

    def test_names(self):
        for name in ("hs_err_pid24052.log", "HS_ERR_PID1.LOG", "replay_pid3.log", "x.mdmp", "forge_runtime/hs_err_pid9.log",
                     "forge_runtime\\hs_err_pid9.log"):
            self.assertTrue(java_crash.is_crash_file(name), name)
        for name in ("forge_engine.log", "hs_err.txt", "crash_log.txt", "pid24052.log"):
            self.assertFalse(java_crash.is_crash_file(name), name)


class FakeProc:
    def __init__(self, pid, code=1):
        self.pid, self.code = pid, code

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code


class CrashReportTests(Base):
    def started(self, pid=4242, code=1):
        s = self.session()
        s.crash_dir = s.crash_folder()
        s.proc, s._t0 = FakeProc(pid, code), time.time()
        return s

    def test_this_engines_report_once_it_is_gone(self):
        s = self.started()
        self.assertIsNone(s.crash_report())
        path = write(os.path.join(self.logs, "hs_err_pid4242.log"), WINDOWS_REPORT)
        self.assertEqual(s.crash_report(), path)
        write(os.path.join(self.logs, "hs_err_pid1111.log"), WINDOWS_REPORT)      # another engine's
        self.assertEqual(s.crash_report(), path)

    def test_none_while_it_runs(self):
        s = self.started(code=None)
        write(os.path.join(self.logs, "hs_err_pid4242.log"), WINDOWS_REPORT)
        self.assertIsNone(s.crash_report())

    def test_a_reused_pids_old_report_is_not_this_one(self):
        s = self.started()
        path = write(os.path.join(self.logs, "hs_err_pid4242.log"), WINDOWS_REPORT)
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertIsNone(s.crash_report())

    def test_the_fallback_folder_counts(self):
        s = self.started()
        path = write(os.path.join(self.runtime, "hs_err_pid4242.log"), WINDOWS_REPORT)
        self.assertEqual(s.crash_report(), path)

    def test_a_stand_in_bridge_has_none(self):
        s = self.started()
        s.command = ["python", "fake_bridge.py"]
        write(os.path.join(self.logs, "hs_err_pid4242.log"), WINDOWS_REPORT)
        self.assertIsNone(s.crash_report())


class ReadTests(Base):
    def test_summary_of_a_windows_report(self):
        path = write(os.path.join(self.logs, "hs_err_pid24052.log"), WINDOWS_REPORT)
        self.assertEqual(java_crash.summary(path), "EXCEPTION_ACCESS_VIOLATION in forge.util.collect.FCollection.<init> (thread Game-0)")

    def test_summary_of_a_native_frame(self):
        path = write(os.path.join(self.logs, "hs_err_pid67.log"), LINUX_REPORT)
        self.assertEqual(java_crash.summary(path), "SIGSEGV in libjvm.so+0xfc91d4 (thread main)")

    def test_summary_never_raises(self):
        self.assertEqual(java_crash.summary(os.path.join(self.tmp, "missing.log")), "Java crashed")
        path = write(os.path.join(self.logs, "hs_err_pid1.log"), "something else\n")
        self.assertEqual(java_crash.summary(path), "Java crashed (no details in its report)")

    def test_out_of_memory_report(self):
        path = write(os.path.join(self.logs, "hs_err_pid2.log"),
                     "#\n# There is insufficient memory for the Java Runtime Environment to continue.\n#\n")
        self.assertTrue(java_crash.summary(path).startswith("out of native memory"))

    def test_an_excerpt_has_no_environment_variables(self):
        path = write(os.path.join(self.logs, "hs_err_pid24052.log"), WINDOWS_REPORT)
        text = java_crash.excerpt(path)
        for gone in ("USERNAME=", "COMPUTERNAME=", "JANES-DESKTOP", "Jane Q Tester", "PATH=", "TEMP=", "JAVA_HOME=", "USERDOMAIN"):
            self.assertNotIn(gone, text)
        self.assertIn("Environment Variables: (left out of bug reports", text)
        self.assertIn("FCollection.<init>", text)
        self.assertIn('JavaThread "Game-0"', text)

    def test_a_long_report_is_shortened_but_keeps_the_system_section(self):
        path = write(os.path.join(self.logs, "hs_err_pid24052.log"), WINDOWS_REPORT)
        lines = java_crash.excerpt(path).splitlines()
        self.assertLessEqual(len(lines), java_crash.HEAD_LINES + java_crash.SYSTEM_LINES + 1)
        self.assertTrue(any("lines left out" in ln for ln in lines))
        self.assertTrue(any("S Y S T E M" in ln for ln in lines))
        self.assertTrue(any(ln.startswith("CPU:") for ln in lines))


class ReportTests(Base):
    def test_a_bug_report_carries_the_excerpt_scrubbed(self):
        path = write(os.path.join(self.logs, "hs_err_pid24052.log"), WINDOWS_REPORT)
        folder = os.path.join(self.tmp, "program")
        os.makedirs(folder)
        zpath = reporting.build_report({"name": "Karl", "happened": "Java crashed", "expected": "no crash", "seed": 1},
                                       folder=folder, extra_files=reporting.java_crash_files(path))
        with zipfile.ZipFile(zpath) as z:
            self.assertIn("java_crash_hs_err_pid24052.txt", z.namelist())
            text = z.read("java_crash_hs_err_pid24052.txt").decode("utf-8")
        self.assertIn("EXCEPTION_ACCESS_VIOLATION", text)
        self.assertNotIn("Jane Q Tester", text)
        if len(HOME) > 3:
            self.assertNotIn(HOME, text)          # the user folder becomes "~", as in every other file of a report

    def test_no_crash_no_file(self):
        self.assertEqual(reporting.java_crash_files(None), [])
        self.assertEqual(reporting.java_crash_files(os.path.join(self.tmp, "missing.log")), [])

    def test_the_message_names_the_crash_report(self):
        crash = os.path.join(self.logs, "hs_err_pid24052.log")
        text = java_crash.stopped_message(os.path.join(self.logs, "forge_engine.log"), crash)
        self.assertIn("Java itself crashed", text)
        self.assertIn("hs_err_pid24052.log", text)
        self.assertIn(self.logs, text)
        self.assertIn("F8", text)
        plain = java_crash.stopped_message(os.path.join(self.logs, "forge_engine.log"), None)
        self.assertIn("forge_engine.log in " + self.logs, plain)
        self.assertNotIn("next to the program", plain)


class TableTests(Base):
    def setUp(self):
        super().setUp()
        from tests.test_forge_table import frame, make_gui
        self.frame, self.gui = frame, make_gui()

    def test_forge_stopped_names_the_crash_report(self):
        path = write(os.path.join(self.logs, "hs_err_pid24052.log"), WINDOWS_REPORT)
        s = self.gui.session
        s.crash_report = lambda: path
        s.exited = True
        self.frame(self.gui, 2)
        self.assertEqual(self.gui.modal.title, "Forge stopped")
        self.assertIn("Java itself crashed", self.gui.modal.text)
        self.assertIn("hs_err_pid24052.log", self.gui.modal.text)

    def test_forge_stopped_without_a_crash_report(self):
        s = self.gui.session
        s.exited = True
        self.frame(self.gui, 2)
        self.assertEqual(self.gui.modal.title, "Forge stopped")
        self.assertIn("forge_engine.log", self.gui.modal.text)
        self.assertNotIn("Java itself crashed", self.gui.modal.text)

    def test_f8_report_takes_it(self):
        path = write(os.path.join(self.logs, "hs_err_pid24052.log"), WINDOWS_REPORT)
        self.gui.session.crash_report = lambda: path
        ctx = self.gui.report_context()
        self.assertEqual([n for n, _ in ctx["extra_files"]], ["java_crash_hs_err_pid24052.txt"])
        self.gui.open_report()
        dialog = self.gui.overlay
        dialog.fields[0].text, dialog.fields[1].text = "Karl", "Java crashed mid-game"
        with mock.patch.object(reporting, "build_report", return_value=os.path.join(self.tmp, "r.zip")) as build:
            self.assertTrue(dialog.make_zip(self.gui))
        self.assertEqual([n for n, _ in build.call_args.kwargs["extra_files"]], ["java_crash_hs_err_pid24052.txt"])

    def test_no_crash_report_no_extra_file(self):
        self.assertEqual(self.gui.report_context()["extra_files"], [])


class SoakTests(Base):
    def setUp(self):
        super().setUp()
        import soak
        self.soak = soak

    def result(self, findings):
        r = mock.Mock()
        r.findings = findings
        return r

    def test_the_fatal_finding_says_what_crashed(self):
        path = write(os.path.join(self.logs, "hs_err_pid24052.log"), WINDOWS_REPORT)
        session = mock.Mock()
        session.crash_report.return_value = path
        r = self.result([{"rule": "fatal", "severity": "fail", "at": 3,
                          "detail": "the stream ended (_exit) without game_over and without a quit command"}])
        self.assertEqual(self.soak._note_java_crash(r, session), path)
        detail = r.findings[0]["detail"]
        self.assertTrue(detail.startswith("the stream ended (_exit)"))
        self.assertIn("Java itself crashed: EXCEPTION_ACCESS_VIOLATION in forge.util.collect.FCollection.<init> (thread Game-0)", detail)
        self.assertIn("hs_err_pid24052.log", detail)

    def test_a_crash_without_a_fatal_finding_adds_one(self):
        path = write(os.path.join(self.logs, "hs_err_pid1.log"), LINUX_REPORT)
        session = mock.Mock()
        session.crash_report.return_value = path
        r = self.result([])
        self.soak._note_java_crash(r, session)
        self.assertEqual([(f["rule"], f["severity"]) for f in r.findings], [("fatal", "fail")])

    def test_no_crash_changes_nothing(self):
        session = mock.Mock()
        session.crash_report.return_value = None
        findings = [{"rule": "fatal", "severity": "fail", "at": 3, "detail": "x"}]
        r = self.result(list(findings))
        self.assertIsNone(self.soak._note_java_crash(r, session))
        self.assertEqual(r.findings, findings)
        self.assertIsNone(self.soak._note_java_crash(r, object()))      # a session from before patch 46

    def test_a_stand_in_session_is_not_a_crash(self):
        # the full suite found this: tests/test_patch44.py hands _finish_report a Mock session, whose crash_report() is a Mock
        r = self.result([{"rule": "fatal", "severity": "fail", "at": 3, "detail": "x"}])
        self.assertIsNone(self.soak._note_java_crash(r, mock.Mock()))
        self.assertEqual(r.findings[0]["detail"], "x")
        self.assertEqual(java_crash.summary(mock.Mock()), "Java crashed")
        self.assertEqual(reporting.java_crash_files(mock.Mock()), [])


class ShipTests(Base):
    def test_the_installer_build_never_copies_one(self):
        import build_installer
        ignore = shutil.ignore_patterns(*build_installer.RUNTIME_IGNORE)
        names = ["forge.jar", "forge_bridge.jar", "res", "VERSION.txt", "hs_err_pid24052.log", "replay_pid1.log", "java.mdmp",
                 "__pycache__", "x.pyc"]
        self.assertEqual(sorted(ignore(self.runtime, names)),
                         sorted(["hs_err_pid24052.log", "replay_pid1.log", "java.mdmp", "__pycache__", "x.pyc"]))

    def test_check_dist_fails_on_one(self):
        import check_dist
        tree = [("forge_runtime/forge.jar", 10), ("forge_runtime/hs_err_pid24052.log", 133106)]
        problems = check_dist.check_layout(self.tmp, tree, ["forge_runtime"], sample_decks=None)
        crash = [p for p in problems if "Java crash report" in p]
        self.assertEqual(len(crash), 1)
        self.assertIn("forge_runtime/hs_err_pid24052.log", crash[0])
        clean = check_dist.check_layout(self.tmp, tree[:1], ["forge_runtime"], sample_decks=None)
        self.assertFalse([p for p in clean if "Java crash report" in p])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_git_ignores_them_anywhere(self):
        for rel in ("hs_err_pid24052.log", "forge_runtime/hs_err_pid1.log", "soak_runs/x/hs_err_pid2.log", "replay_pid3.log",
                    "java.mdmp"):
            r = subprocess.run(["git", "check-ignore", "--no-index", "-q", rel], cwd=BASE, capture_output=True)
            self.assertEqual(r.returncode, 0, f"{rel} is not ignored by .gitignore")
        r = subprocess.run(["git", "check-ignore", "--no-index", "-q", "java_crash.py"], cwd=BASE, capture_output=True)
        self.assertEqual(r.returncode, 1)

    def test_the_public_copy_and_nightly_package_leave_them_out(self):
        import export_public as ep
        write(os.path.join(self.tmp, "version.py"), "VERSION = '1'\n")
        write(os.path.join(self.tmp, "hs_err_pid24052.log"), WINDOWS_REPORT)
        write(os.path.join(self.tmp, "replay_pid1.log"), "x")
        files = list(ep.iter_source_files(self.tmp))
        self.assertIn("version.py", files)
        self.assertFalse([f for f in files if java_crash.is_crash_file(f)])


class HeaderTests(Base):
    def test_a_log_folder_is_not_described_as_the_program(self):
        with mock.patch.object(paths, "is_portable", return_value=True):
            lines = crashlog.environment(self.logs)
        self.assertEqual(lines[0], version.describe(paths.program_dir()))
        self.assertNotIn("code unknown", lines[0])

    def test_a_program_folder_still_describes_itself(self):
        write(os.path.join(self.tmp, "version.py"), "x = 1\n")
        with mock.patch.object(paths, "is_portable", return_value=True):
            lines = crashlog.environment(self.tmp)
        self.assertEqual(lines[0], version.describe(self.tmp))
        self.assertIn("forge_runtime: ", "\n".join(lines))

    def test_the_version(self):
        parts = tuple(int(x) for x in version.VERSION.split("."))
        self.assertGreaterEqual(parts, (0, 28, 50))


CRASH_MAIN = textwrap.dedent("""\
    package forge.bridge;
    import java.lang.reflect.Field;
    // A stand-in for the bridge's main class that crashes Java itself, as game 75's engine did (patch 46's live test).
    public class Main {
        public static void main(String[] args) throws Exception {
            Field f = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
            f.setAccessible(true);
            ((sun.misc.Unsafe) f.get(null)).putAddress(0L, 42L);
        }
    }
    """)


def _javac():
    java = fc.find_java()
    if not java:
        return None
    for name in ("javac", "javac.exe"):
        cand = os.path.join(os.path.dirname(java), name)
        if os.path.isfile(cand):
            return cand
    return shutil.which("javac")


@unittest.skipIf(os.environ.get("MANTICORE_SKIP_LIVE"), "MANTICORE_SKIP_LIVE is set")
@unittest.skipUnless(_javac(), "needs a JDK (javac) to build the crashing stand-in bridge")
class LiveCrashTests(Base):
    def make_runtime(self):
        src = os.path.join(self.tmp, "src", "forge", "bridge")
        write(os.path.join(src, "Main.java"), CRASH_MAIN)
        classes = os.path.join(self.tmp, "classes")
        r = subprocess.run([_javac(), "-nowarn", "-d", classes, os.path.join(src, "Main.java")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with zipfile.ZipFile(os.path.join(self.runtime, "forge_bridge.jar"), "w") as z:
            z.write(os.path.join(classes, "forge", "bridge", "Main.class"), "forge/bridge/Main.class")
        with zipfile.ZipFile(os.path.join(self.runtime, "forge.jar"), "w") as z:
            z.writestr("placeholder.txt", "not Forge")

    def test_a_real_crash_lands_in_the_log_folder(self):
        self.make_runtime()
        stray = write(os.path.join(self.runtime, "hs_err_pid1.log"), LINUX_REPORT)
        s = self.session()
        s.java = fc.find_java()
        with mock.patch.object(fc, "runtime_problem", return_value=None):
            s.start()
        s.proc.wait(timeout=60)
        self.assertNotEqual(s.proc.returncode, 0)
        report = s.crash_report()
        self.assertIsNotNone(report, f"no crash report; log folder has {os.listdir(self.logs)}")
        self.assertEqual(os.path.dirname(report), os.path.abspath(self.logs))
        self.assertEqual(os.path.basename(report), f"hs_err_pid{s.proc.pid}.log")
        self.assertFalse(os.path.exists(stray))
        self.assertFalse([n for n in os.listdir(self.runtime) if java_crash.is_crash_file(n)])
        summary = java_crash.summary(report)
        self.assertTrue(summary.startswith(("SIGSEGV", "EXCEPTION_ACCESS_VIOLATION", "SIGBUS")), summary)
        name, text = reporting.java_crash_files(report)[0]
        self.assertNotIn("Environment Variables:\n", text)
        for key in ("PATH=", "USERNAME=", "HOME="):
            self.assertFalse([ln for ln in text.splitlines() if ln.startswith(key)], key)


if __name__ == "__main__":
    unittest.main()
