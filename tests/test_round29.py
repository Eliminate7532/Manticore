# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 29 tests: the Windows installer round (SONNET_SPEC_R29 section 8).

Nothing here needs PyInstaller, Inno Setup or Windows. The frozen paths are probed in a child Python with sys.frozen and
sys._MEIPASS faked; the build script runs with injected runners; the .iss and the Sandbox files are checked as text.
No test reads Karl's bug_report_config.json: webhook tests use fake files in temp folders (explicit folder, or a fake program
folder in a child process)."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import reporting
import version
from tools import build_installer as bi
from tools import check_dist as cd

FAKE_HOOK = "https://discord.com/api/webhooks/123456789/FAKE_TOKEN_for_tests_only_abcdef"
OTHER_HOOK = "https://discord.com/api/webhooks/987654321/OTHER_FAKE_TOKEN_abcdef123"


def write(path, text="x", mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        f.write(text)


def read(rel):
    with open(os.path.join(BASE, rel), "r", encoding="utf-8") as f:
        return f.read()


def child(code, env_extra=None, cwd=None):
    env = dict(os.environ)
    env.update({"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "PYTHONDONTWRITEBYTECODE": "1"})
    env.pop("MANTICORE_PORTABLE", None)
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=cwd or BASE, timeout=120)
    return proc


FROZEN_PREFIX = """
import sys
sys.path.insert(0, {base!r})
sys.frozen = True
sys._MEIPASS = {meipass!r}
sys.executable = {exe!r}
"""


class FrozenPathTests(unittest.TestCase):
    """With sys.frozen set, everything the program reads from its own folder resolves inside sys._MEIPASS."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mei = os.path.join(self.tmp.name, "Manticore")
        os.makedirs(self.mei)
        self.env = {"MANTICORE_PORTABLE": "0", "MANTICORE_USER_DIR": os.path.join(self.tmp.name, "user"),
                    "MANTICORE_DATA_DIR": os.path.join(self.tmp.name, "data")}
        self.prefix = FROZEN_PREFIX.format(base=BASE, meipass=self.mei, exe=os.path.join(self.mei, "Manticore.exe"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_code(self, code):
        proc = child(self.prefix + code, self.env, cwd=self.tmp.name)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        return proc.stdout.strip().splitlines()

    def test_every_program_folder_reader_resolves_inside_meipass(self):
        out = self.run_code("""
import paths, forge_client, forge_table, licenses_view, version
from tools import soak
print(paths.program_dir()); print(forge_client.BASE_DIR); print(forge_client.DEFAULT_RUNTIME)
print(forge_table.SOUNDS_DIR); print(licenses_view.BASE_DIR); print(licenses_view.LICENSES_DIR); print(version.BASE_DIR)
""")
        out = [ln for ln in out if not ln.startswith("pygame") and not ln.startswith("Hello from")]   # pygame's banner
        self.assertEqual(len(out), 7)
        for line in out:
            self.assertTrue(os.path.normcase(line).startswith(os.path.normcase(self.mei)), line)

    def test_meipass_wins_over_the_exe_folder(self):
        other = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(other)
        code = "import paths; print(paths.program_dir())"
        proc = child(FROZEN_PREFIX.format(base=BASE, meipass=other, exe=os.path.join(self.mei, "Manticore.exe")) + code, self.env)
        self.assertEqual(proc.stdout.strip(), other)

    def test_a_frozen_copy_is_not_portable_and_uses_the_per_user_folders(self):
        out = self.run_code("import paths; print(paths.is_portable()); print(paths.user_dir()); print(paths.APP_NAME)")
        self.assertEqual(out[0], "False")
        self.assertEqual(out[1], self.env["MANTICORE_USER_DIR"])
        self.assertEqual(out[2], "Manticore")

    def test_soak_deck_pool_comes_from_the_installed_sample_folder(self):
        write(os.path.join(self.mei, "sample_decks", "a.txt"), "1 Island\n")
        out = self.run_code("""
import paths
print(paths.sample_dir())
""")
        self.assertTrue(os.path.normcase(out[0]).startswith(os.path.normcase(self.mei)))


class BuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def info(self, **kw):
        data = {"version": "0.28.9", "commit": "abcdef0123456789abcdef0123456789abcdef01", "tag": "r28d", "code": "9f8e7d6c",
                "built_at": "2026-10-02T10:11:12", "forge": "1.6.x", "bridge_stamp": "123456abcdef"}
        data.update(kw)
        write(os.path.join(self.dir, "build_info.json"), json.dumps(data))

    def test_a_folder_with_build_info_and_no_py_reports_the_recorded_identity(self):
        self.info()
        self.assertTrue(version.uses_build_info(self.dir))
        self.assertEqual(version.commit(self.dir), "abcdef0")
        self.assertEqual(version.code_fingerprint(self.dir), "9f8e7d6c")
        text = version.describe(self.dir)
        self.assertIn("code 9f8e7d6c", text)
        self.assertIn("installed build 2026-10-02", text)
        self.assertIn(version.APP_NAME, text)

    def test_missing_code_says_unknown_never_the_empty_hash(self):
        self.info(code="")
        self.assertEqual(version.code_fingerprint(self.dir), "unknown")
        self.assertNotIn("da39a3ee", version.describe(self.dir))

    def test_an_empty_folder_never_reports_the_empty_hash(self):
        self.assertNotEqual(version.code_fingerprint(self.dir), "da39a3ee")

    def test_a_broken_build_info_still_describes_without_crashing(self):
        write(os.path.join(self.dir, "build_info.json"), "{ not json")
        text = version.describe(self.dir)
        self.assertNotIn("da39a3ee", text)

    def test_a_source_folder_still_fingerprints_its_py_files(self):
        write(os.path.join(self.dir, "a.py"), "print(1)\n")
        self.info(code="shouldnotbeused")
        self.assertFalse(version.uses_build_info(self.dir))
        a = version.code_fingerprint(self.dir)
        self.assertNotEqual(a, "shouldnotbeused")
        write(os.path.join(self.dir, "a.py"), "print(2)\n")
        self.assertNotEqual(a, version.code_fingerprint(self.dir))

    def test_the_app_name_is_manticore(self):
        self.assertEqual(version.APP_NAME, "Manticore")
        import paths
        self.assertEqual(paths.APP_NAME, "Manticore")


class FindJavaTests(unittest.TestCase):
    def test_an_installed_copy_uses_only_its_own_jre(self):
        import forge_client as fc
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "jre"))
            with mock.patch.object(fc.paths, "program_dir", return_value=tmp), \
                 mock.patch.object(fc.paths, "is_portable", return_value=False), \
                 mock.patch.dict(os.environ, {"JAVA_HOME": tmp}), \
                 mock.patch.object(fc.shutil, "which", return_value="/usr/bin/java"):
                self.assertTrue(fc.bundled_runtime_expected())
                self.assertIsNone(fc.find_java())            # jre/ has no java: no fallback to JAVA_HOME or PATH
                java = fc.bundled_java_path()
                write(java, "")
                with mock.patch.object(fc, "java_major", return_value=21):
                    self.assertEqual(fc.find_java(), java)
                with mock.patch.object(fc, "java_major", return_value=0):
                    self.assertIsNone(fc.find_java())        # present but won't start

    def test_a_portable_copy_keeps_the_old_order(self):
        import forge_client as fc
        with mock.patch.object(fc.paths, "is_portable", return_value=True):
            self.assertFalse(fc.bundled_runtime_expected())

    def test_the_problem_text_says_reinstall_not_python(self):
        import forge_client as fc
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "jre"))
            with mock.patch.object(fc.paths, "program_dir", return_value=tmp), \
                 mock.patch.object(fc.paths, "is_portable", return_value=False):
                text = fc.runtime_problem() or ""
        self.assertIn("Reinstall Manticore", text)
        self.assertNotIn("setup_forge", text)


class SoakFlagTests(unittest.TestCase):
    def test_soak_argv_keeps_the_canary_on(self):
        import forge_table as ft
        self.assertEqual(ft.soak_argv(3), ["--games", "3", "--decks", "sample", "--boards", "0"])
        self.assertNotIn("--no-canary", ft.soak_argv(3))

    def test_soak_flag_hands_over_to_tools_soak_and_passes_the_exit_code(self):
        import forge_table as ft
        from tools import soak
        with mock.patch.object(soak, "main", return_value=3) as m, mock.patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                ft.main(["forge_table.py", "--soak", "2"])
        self.assertEqual(cm.exception.code, 3)
        m.assert_called_once_with(["--games", "2", "--decks", "sample", "--boards", "0"])

    def test_ensure_std_streams_replaces_none(self):
        import forge_table as ft
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            ft.ensure_std_streams()
            sys.stdout.write("x")
            sys.stdout.flush()
            sys.stderr.write("y")
            out, err = sys.stdout, sys.stderr
            self.assertIsNotNone(out)
            self.assertIsNotNone(err)
            out.close()
            err.close()


class WebhookTests(unittest.TestCase):
    def test_an_explicit_folder_reads_only_that_folders_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "bug_report_config.json"), json.dumps({"discord_webhook": FAKE_HOOK}))
            self.assertEqual(reporting.webhook_url(tmp), FAKE_HOOK)
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, reporting.BUNDLED_CONFIG_NAME), json.dumps({"discord_webhook": FAKE_HOOK}))
            self.assertIsNone(reporting.webhook_url(tmp))          # the bundled name is never read from an explicit folder

    def probe(self, own, bundled):
        """Child process with a fake frozen program folder and a fake user folder; prints the webhook the program would use."""
        with tempfile.TemporaryDirectory() as tmp:
            prog, user = os.path.join(tmp, "prog"), os.path.join(tmp, "user")
            os.makedirs(prog)
            os.makedirs(user)
            if bundled is not None:
                write(os.path.join(prog, reporting.BUNDLED_CONFIG_NAME), json.dumps(bundled))
            if own is not None:
                write(os.path.join(user, "bug_report_config.json"), json.dumps(own))
            code = FROZEN_PREFIX.format(base=BASE, meipass=prog, exe=os.path.join(prog, "Manticore.exe")) + (
                "import reporting\nprint(reporting.webhook_url())\nprint(reporting.owner_name())\n")
            proc = child(code, {"MANTICORE_PORTABLE": "0", "MANTICORE_USER_DIR": user, "MANTICORE_DATA_DIR": os.path.join(tmp, "d")},
                         cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
            return proc.stdout.strip().splitlines()

    def test_the_bundled_webhook_is_used_when_the_tester_has_none(self):
        self.assertEqual(self.probe(None, {"discord_webhook": FAKE_HOOK, "owner_name": "Karl"})[0], FAKE_HOOK)

    def test_the_testers_own_valid_webhook_wins(self):
        out = self.probe({"discord_webhook": OTHER_HOOK}, {"discord_webhook": FAKE_HOOK})
        self.assertEqual(out[0], OTHER_HOOK)

    def test_a_broken_own_webhook_falls_back_to_the_bundled_one_and_keeps_own_owner_name(self):
        out = self.probe({"discord_webhook": "not a hook", "owner_name": "Sam"}, {"discord_webhook": FAKE_HOOK})
        self.assertEqual(out[0], FAKE_HOOK)
        self.assertEqual(out[1], "Sam")

    def test_neither_means_not_set_up(self):
        self.assertEqual(self.probe(None, None)[0], "None")

    def test_the_bundled_file_is_never_migrated_into_the_user_folder(self):
        import paths
        import inspect
        src = inspect.getsource(paths.migrate_from_program_dir)
        self.assertNotIn("bundled", src)
        self.assertNotEqual(reporting.BUNDLED_CONFIG_NAME, "bug_report_config.json")

    def test_the_bundled_file_is_skipped_by_every_packer_and_git(self):
        name = "bug_report_config.bundled.json"
        self.assertIn(name, read(".gitignore"))
        self.assertIn(name, read("backup.py"))
        self.assertIn(name, read(os.path.join("tools", "export_public.py")))
        self.assertIn(name, read(os.path.join("tools", "nightly_package.py")))
        for folder in ("build", "dist", "installer_out"):
            self.assertIn(folder, read(".gitignore"))
            self.assertIn(f'"{folder}"', read("backup.py"))


# ---- check_dist on a fake tree --------------------------------------------------------------------------------------

def make_tree(root, natives=True):
    files = ["Manticore.exe", "Manticore-cli.exe", "jre/bin/java.exe", "forge_runtime/forge.jar", "forge_runtime/forge_bridge.jar",
             "forge_runtime/VERSION.txt", "forge_runtime/res/cardsfolder/a.txt", "sounds/cues.json", "LICENSE",
             "THIRD_PARTY_NOTICES.txt", "README_FIRST.txt", "assets/fonts/f.ttf", "jre/legal/x.txt",
             "banned_commander.snapshot.json", "banned_brawl.snapshot.json", "START_HERE.txt"]
    for i in range(cd.EXPECTED_SAMPLE_DECKS):
        files.append(f"sample_decks/d{i}.txt")
    for rel in files:
        if rel == "forge_runtime/forge.jar":
            os.makedirs(os.path.join(root, "forge_runtime"), exist_ok=True)
            import zipfile
            with zipfile.ZipFile(os.path.join(root, rel), "w") as zf:
                zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
            continue
        write(os.path.join(root, rel), "data")
    write(os.path.join(root, "build_info.json"), json.dumps({"version": "0.28.9", "code": "9f8e7d6c", "commit": "a" * 40}))
    manifest = {"project": {"name": "Manticore"}, "native_libraries": {"libraries": [
        {"name": "all", "files": ["*.dll", "*.pyd"]}]}}
    write(os.path.join(root, "licenses", "NOTICES.json"), json.dumps(manifest if natives else {"project": {}}))
    write(os.path.join(root, "python313.dll"), "d")


class CheckDistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Manticore")
        make_tree(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, **kw):
        return cd.check_dist(self.root, run=False, **kw)

    def test_a_good_tree_is_green(self):
        res = self.check()
        self.assertEqual(res.problems, [], res.problems)
        self.assertEqual(res.blocking(release=True), [])

    def test_a_missing_jre_names_the_missing_file(self):
        os.remove(os.path.join(self.root, "jre", "bin", "java.exe"))
        self.assertTrue(any("jre/bin/java.exe" in p for p in self.check().problems))

    def test_forbidden_items_are_named(self):
        for rel in (".git/HEAD", "settings.json", "portable.txt", "bug_report_config.json"):
            write(os.path.join(self.root, rel), "{}")
            problems = self.check().problems
            self.assertTrue(any(rel.split("/")[0] in p for p in problems), (rel, problems))
            top = rel.split("/")[0]
            target = os.path.join(self.root, top)
            if os.path.isdir(target):
                import shutil
                shutil.rmtree(target)
            else:
                os.remove(target)

    def test_a_loose_py_file_is_a_problem(self):
        write(os.path.join(self.root, "stray.py"), "")
        self.assertTrue(any("stray.py" in p for p in self.check().problems))

    def test_a_webhook_address_in_any_other_file_is_a_problem_and_only_names_are_printed(self):
        write(os.path.join(self.root, "sounds", "oops.txt"), "see " + FAKE_HOOK)
        res = self.check()
        text = "\n".join(res.problems)
        self.assertIn("oops.txt", text)
        self.assertNotIn("FAKE_TOKEN", text)

    def test_the_bundled_file_may_hold_the_webhook(self):
        write(os.path.join(self.root, cd.BUNDLED_CONFIG), json.dumps({"discord_webhook": FAKE_HOOK}))
        res = self.check()
        self.assertEqual(res.problems, [], res.problems)

    def test_a_dll_without_a_licence_entry_is_a_todo_for_test_and_blocking_for_release(self):
        write(os.path.join(self.root, "licenses", "NOTICES.json"), json.dumps(
            {"native_libraries": {"libraries": [{"name": "x", "file": "other.dll"}]}}))
        res = self.check()
        self.assertEqual(res.problems, [], res.problems)
        self.assertTrue(any("python313.dll" in t or "no native_libraries entry" in t for t in res.todo))
        self.assertEqual(res.blocking(release=False), [])
        self.assertTrue(res.blocking(release=True))

    def test_the_version_and_one_game_checks_use_the_injected_runner(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        res = cd.check_dist(self.root, run=True, runner=runner)
        self.assertTrue(res.problems)
        self.assertTrue(calls)

    def test_run_is_skipped_when_the_layout_is_already_broken(self):
        os.remove(os.path.join(self.root, "LICENSE"))
        calls = []
        res = cd.check_dist(self.root, run=True, runner=lambda *a, **k: calls.append(a))
        self.assertEqual(calls, [])
        self.assertTrue(any("LICENSE" in p for p in res.problems))


# ---- the installer script and the Sandbox files, as text ---------------------------------------------------------------

class InnoScriptTests(unittest.TestCase):
    def setUp(self):
        self.iss = read(os.path.join("installer", "commander_sim.iss"))

    def test_per_user_install_with_a_fixed_app_id(self):
        self.assertIn("PrivilegesRequired=lowest", self.iss)
        self.assertIn("{userpf}", self.iss)
        self.assertIn("{{1FE78D6B-8FAA-4EFE-8CE1-FD920DC97828}", self.iss)
        self.assertNotIn("PrivilegesRequired=admin", self.iss)

    def test_an_update_empties_the_program_folder_first(self):
        self.assertIn("[InstallDelete]", self.iss)
        self.assertIn(r"{app}\*", self.iss)

    def test_uninstall_asks_about_data_and_supports_purge(self):
        self.assertIn("/PURGE", self.iss)
        self.assertIn("SuppressibleMsgBox", self.iss)
        self.assertIn("{userappdata}", self.iss)
        self.assertIn("{localappdata}", self.iss)

    def test_forge_data_is_never_deleted(self):
        for line in self.iss.splitlines():
            if "DelTree" in line or "UninstallDelete" in line:
                self.assertNotIn("forge", line.lower(), line)

    def test_no_user_data_is_shipped_by_the_script(self):
        self.assertNotIn("settings.json", self.iss)
        self.assertNotIn("bug_report_config.json", self.iss)


class SandboxFileTests(unittest.TestCase):
    def test_the_template_has_all_its_placeholders(self):
        text = read(os.path.join("installer", "sandbox", "scripts", "sandbox_test.wsb.template"))
        for key in ("@@MEMORY@@", "@@INSTALLER_OUT@@", "@@SCRIPTS@@", "@@RESULTS@@"):
            self.assertIn(key, text)

    def test_write_sandbox_files_renders_absolute_paths_and_both_memory_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "installer", "sandbox", "scripts"))
            for name in ("sandbox_test.wsb.template", "run_tests.cmd", "run_tests.ps1"):
                src = os.path.join(BASE, "installer", "sandbox", "scripts", name)
                with open(src, "rb") as f:
                    write(os.path.join(tmp, "installer", "sandbox", "scripts", name), f.read(), "wb")
            b = bi.Builder(False, base=tmp, say=lambda *_: None)
            b.installer_path = None
            os.makedirs(b.dist_dir)
            b.write_sandbox_files()
            sb = os.path.join(tmp, "installer", "sandbox")
            t8 = open(os.path.join(sb, "sandbox_8gb.wsb"), encoding="utf-8").read()
            t4 = open(os.path.join(sb, "sandbox_4gb.wsb"), encoding="utf-8").read()
            self.assertIn("8192", t8)
            self.assertIn("4096", t4)
            for t in (t8, t4):
                for key in ("@@MEMORY@@", "@@INSTALLER_OUT@@", "@@SCRIPTS@@", "@@RESULTS@@"):
                    self.assertNotIn(key, t)
                self.assertIn(os.path.join(tmp, "installer_out").replace("&", "&amp;"), t)


# ---- the build script ---------------------------------------------------------------------------------------------------

class BuilderTests(unittest.TestCase):
    def builder(self, release=False, which=None, run=None, base=BASE, environ=None):
        return bi.Builder(release, base=base, which=which or (lambda n: None), run=run or self.no_run,
                          environ=environ if environ is not None else {}, say=lambda *_: None)

    @staticmethod
    def no_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    def test_missing_inno_setup_prints_install_help(self):
        with mock.patch.object(bi, "find_iscc", return_value=None):
            with self.assertRaises(bi.BuildError) as cm:
                self.builder().check_tools()
        self.assertIn("Inno Setup", str(cm.exception))

    def test_wrong_pyinstaller_prints_install_help(self):
        run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="5.13.0\n", stderr="")
        with mock.patch.object(bi, "find_iscc", return_value="iscc"):
            with self.assertRaises(bi.BuildError) as cm:
                self.builder(run=run).check_tools()
        self.assertIn("PyInstaller", str(cm.exception))

    def test_release_refuses_without_a_source_url(self):
        import forge_client as fc
        import setup_forge
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "licenses", "NOTICES.json"), json.dumps({"project": {"source_url": ""}}))
            ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            b = self.builder(release=True, run=ok, base=tmp)
            with mock.patch.object(fc, "runtime_problem", return_value=None), \
                 mock.patch.object(fc, "bridge_stamp", return_value="aaaaaaaaaaaa"), \
                 mock.patch.object(setup_forge, "bridge_source_hash", return_value="aaaaaaaaaaaa" + "0" * 28):
                with self.assertRaises(bi.BuildError) as cm:
                    b.check_sources()
        self.assertIn("SOURCE_URL", str(cm.exception))

    def test_test_build_warns_about_a_dirty_tree_but_release_refuses(self):
        import forge_client as fc
        import setup_forge
        dirty = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=" M a.py\n", stderr="")
        patches = (mock.patch.object(fc, "runtime_problem", return_value=None),
                   mock.patch.object(fc, "bridge_stamp", return_value="aaaaaaaaaaaa"),
                   mock.patch.object(setup_forge, "bridge_source_hash", return_value="aaaaaaaaaaaa" + "0" * 28))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        t = self.builder(release=False, run=dirty)
        t.check_sources()
        self.assertTrue(any("not clean" in n for n in t.notes))
        with self.assertRaises(bi.BuildError):
            self.builder(release=True, run=dirty).check_sources()

    def test_a_missing_runtime_stops_the_build_with_its_message(self):
        import forge_client as fc
        with mock.patch.object(fc, "runtime_problem", return_value="Forge is missing"):
            with self.assertRaises(bi.BuildError) as cm:
                self.builder().check_sources()
        self.assertIn("Forge is missing", str(cm.exception))

    def test_version_resource_is_a_valid_python_expression_with_four_numbers(self):
        text = bi.version_resource_text("0.28.9")
        self.assertEqual(bi.version_parts("0.28.9"), (0, 28, 9, 0))
        self.assertIn("Manticore", text)
        self.assertIn("filevers=(0, 28, 9, 0)", text)
        compile(text, "version_resource", "exec")

    def test_make_ico_writes_a_png_in_ico_file(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame not installed")
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        with tempfile.TemporaryDirectory() as tmp:
            png, ico = os.path.join(tmp, "a.png"), os.path.join(tmp, "a.ico")
            surf = pygame.Surface((300, 300))
            surf.fill((200, 30, 30))
            pygame.image.save(surf, png)
            bi.make_ico(png, ico)
            with open(ico, "rb") as f:
                data = f.read()
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")
        self.assertGreater(int.from_bytes(data[4:6], "little"), 0)
        self.assertIn(b"\x89PNG", data)

    def test_the_build_never_prints_webhook_contents(self):
        src = read(os.path.join("tools", "build_installer.py"))
        self.assertIn("bundled webhook", src)
        lines = [ln for ln in src.splitlines() if "bundled" in ln.lower() and ("say(" in ln or "print(" in ln)]
        for ln in lines:
            self.assertNotIn("discord_webhook", ln)


class SpecAndNamingTests(unittest.TestCase):
    def test_spec_file_is_onedir_unsigned_without_upx(self):
        spec = read("commander_sim.spec")
        self.assertIn("upx=False", spec)
        self.assertIn('contents_directory="."', spec)
        self.assertIn("Manticore-cli", spec)
        self.assertNotIn("onefile", spec.lower().replace("not onefile", ""))

    def test_requirements_pin_pyinstaller(self):
        self.assertIn("pyinstaller==6.22.3", read("requirements-build.txt"))

    def test_the_log_and_report_names_say_manticore(self):
        self.assertEqual(reporting.BOT_NAME, "Manticore bug report")
        self.assertIn("Manticore", read("crashlog.py"))



class QueueIntegrationTests(unittest.TestCase):
    """Brought onto the Patch Queue (2 Oct, after ALT1): what the Round 29 review (queue_2026-10-02\\ROUND29_REVIEW.md) asked for."""

    def test_the_banned_list_snapshot_ships(self):
        with open(os.path.join(cd.BASE_DIR, "commander_sim.spec"), encoding="utf-8") as f:
            self.assertIn('_data("banned_commander.snapshot.json", ".")', f.read())
        self.assertIn("banned_commander.snapshot.json", cd.REQUIRED_FILES)

    def test_one_game_need_not_be_valid_but_must_be_clean(self):
        ok = ("SOAK RUN: INVALID - its results do not show the game is fine\n  - only 1 kind(s) of question came up\n"
              "PROBLEMS (0 kind(s); 0 new since the last run)\n  game 0: seed=1 seats=2 turns=25 ended=game_over | bot: ...\n")
        self.assertEqual(cd.one_game_problems(ok), [])
        self.assertEqual(cd.one_game_problems("SOAK RUN: VALID\n"), [])
        bad = ok.replace("PROBLEMS (0 kind(s)", "PROBLEMS (1 kind(s)")
        self.assertTrue(cd.one_game_problems(bad))
        stalled = ok.replace("ended=game_over", "ended=stall")
        self.assertTrue(any("game over" in p for p in cd.one_game_problems(stalled)))
        self.assertTrue(cd.one_game_problems("SOAK RUN: INVALID\n"))


if __name__ == "__main__":
    unittest.main()
