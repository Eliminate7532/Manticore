# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Round 28: paths.py, the one place that knows where a copy of Commander Sim keeps its own
data, and the small first-start migration that goes with it (no pygame needed for any of this)."""
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import paths


def _clean_env(**overrides):
    """A dict of the env vars this module cares about, all cleared unless overridden - so a test never
    inherits MANTICORE_DATA_DIR from tests/__init__.py (which sets it for the whole suite) by accident."""
    base = {"MANTICORE_PORTABLE": None, "MANTICORE_USER_DIR": None, "MANTICORE_DATA_DIR": None,
            "APPDATA": None, "LOCALAPPDATA": None, "XDG_DATA_HOME": None, "XDG_CACHE_HOME": None,
            "FORGE_RUNTIME": None}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}, [k for k, v in base.items() if v is None]


class EnvCase(unittest.TestCase):
    """Base class: runs each test with a controlled environment (nothing left over from another test or
    from tests/__init__.py), restored afterwards."""

    def patch_env(self, **overrides):
        to_set, to_clear = _clean_env(**overrides)
        patcher = mock.patch.dict(os.environ, to_set)
        patcher.start()
        self.addCleanup(patcher.stop)
        removed = {}
        for k in to_clear:
            if k in os.environ:
                removed[k] = os.environ.pop(k)
        def restore():
            os.environ.update(removed)
        self.addCleanup(restore)


class ModeDetectionTests(EnvCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="manticore_round28_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def program_dir_is(self, folder):
        return mock.patch.object(paths, "program_dir", return_value=folder)

    def test_a_git_folder_means_portable(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        self.patch_env()
        with self.program_dir_is(self.tmp):
            self.assertTrue(paths.is_portable())

    def test_a_portable_txt_file_means_portable(self):
        with open(os.path.join(self.tmp, "portable.txt"), "w") as f:
            f.write("")
        self.patch_env()
        with self.program_dir_is(self.tmp):
            self.assertTrue(paths.is_portable())

    def test_neither_means_installed(self):
        self.patch_env()
        with self.program_dir_is(self.tmp):
            self.assertFalse(paths.is_portable())

    def test_manticore_portable_overrides_both_ways(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        self.patch_env(MANTICORE_PORTABLE="0")
        with self.program_dir_is(self.tmp):
            self.assertFalse(paths.is_portable())
        self.patch_env(MANTICORE_PORTABLE="1")
        with self.program_dir_is(self.tmp):
            self.assertTrue(paths.is_portable())        # true even with no .git and no portable.txt


class PortableModeUnchangedTests(EnvCase):
    """In portable mode every paths.* function must return EXACTLY today's path in the program folder -
    Karl's own working copy must behave byte-for-byte as it did before this round."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="manticore_round28_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.patch_env(MANTICORE_PORTABLE="1")
        self.patcher = mock.patch.object(paths, "program_dir", return_value=self.tmp)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def j(self, *parts):
        return os.path.join(self.tmp, *parts)

    def test_settings_and_config_files_stay_next_to_the_program(self):
        self.assertEqual(paths.settings_file(), self.j("settings.json"))
        self.assertEqual(paths.bug_config_file(), self.j("bug_report_config.json"))

    def test_decks_stay_next_to_the_program(self):
        self.assertEqual(paths.library_dir(), self.j("my_decks"))
        self.assertEqual(paths.sample_dir(), self.j("sample_decks"))

    def test_cache_saves_and_forge_decks_stay_next_to_the_program(self):
        self.assertEqual(paths.cache_dir(), self.j("cache"))
        self.assertEqual(paths.saves_dir(), self.j("saves"))
        self.assertEqual(paths.forge_decks_dir(), self.j("forge_decks"))
        self.assertEqual(paths.bug_reports_dir(), self.j("bug_reports"))

    def test_logs_are_flat_in_the_program_folder_not_nested_under_logs(self):
        self.assertEqual(paths.log_dir(), self.tmp)
        self.assertEqual(paths.log_file("crash_log.txt"), self.j("crash_log.txt"))

    def test_the_programs_own_files_are_always_here_too(self):
        self.assertEqual(paths.sounds_dir(), self.j("sounds"))
        self.assertEqual(paths.licenses_dir(), self.j("licenses"))
        self.assertEqual(paths.runtime_dir(), self.j("forge_runtime"))

    def test_describe_says_portable(self):
        self.assertEqual(paths.describe(), "data: portable (program folder)")


class InstalledModeTests(EnvCase):
    """With APPDATA/LOCALAPPDATA/HOME (or XDG vars) pointed at temp folders, every user-data path is
    inside user_dir() or local_dir(); nothing is inside program_dir(); the program's own read-only
    folders (sample decks, sounds, licenses, the Forge runtime) still are."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="manticore_round28_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.program = os.path.join(self.tmp, "program")
        self.appdata = os.path.join(self.tmp, "roaming")
        self.localappdata = os.path.join(self.tmp, "local")
        os.makedirs(self.program)
        self.patch_env(MANTICORE_PORTABLE="0", APPDATA=self.appdata, LOCALAPPDATA=self.localappdata)
        self.patcher = mock.patch.object(paths, "program_dir", return_value=self.program)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self._win = mock.patch.object(sys, "platform", "win32")
        self._win.start()
        self.addCleanup(self._win.stop)

    def test_every_user_data_path_is_under_user_or_local_dir(self):
        user, local = paths.user_dir(), paths.local_dir()
        self.assertTrue(paths.settings_file().startswith(user + os.sep))
        self.assertTrue(paths.bug_config_file().startswith(user + os.sep))
        self.assertTrue(paths.library_dir().startswith(user + os.sep))
        self.assertTrue(paths.saves_dir().startswith(user + os.sep))
        self.assertTrue(paths.cache_dir().startswith(local + os.sep))
        self.assertTrue(paths.forge_decks_dir().startswith(local + os.sep))
        self.assertTrue(paths.bug_reports_dir().startswith(local + os.sep))
        self.assertTrue(paths.log_dir().startswith(local + os.sep))

    def test_no_user_data_path_is_inside_the_program_folder(self):
        prog = self.program + os.sep
        for p in (paths.settings_file(), paths.bug_config_file(), paths.library_dir(), paths.saves_dir(),
                  paths.cache_dir(), paths.forge_decks_dir(), paths.bug_reports_dir(), paths.log_dir()):
            self.assertFalse(p.startswith(prog), f"{p} should not be under the program folder")

    def test_the_programs_own_files_are_still_in_the_program_folder(self):
        self.assertEqual(paths.sample_dir(), os.path.join(self.program, "sample_decks"))
        self.assertEqual(paths.sounds_dir(), os.path.join(self.program, "sounds"))
        self.assertEqual(paths.licenses_dir(), os.path.join(self.program, "licenses"))
        self.assertEqual(paths.runtime_dir(), os.path.join(self.program, "forge_runtime"))

    def test_logs_land_in_a_logs_subfolder(self):
        self.assertEqual(paths.log_dir(), os.path.join(self.localappdata, "Manticore", "logs"))

    def test_describe_names_the_folder(self):
        self.assertIn(paths.user_dir(), paths.describe())

    def test_windows_uses_appdata_and_localappdata(self):
        self.assertEqual(paths.user_dir(), os.path.join(self.appdata, "Manticore"))
        self.assertEqual(paths.local_dir(), os.path.join(self.localappdata, "Manticore"))


class OverrideTests(EnvCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="manticore_round28_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.override = os.path.join(self.tmp, "scratch")

    def test_manticore_user_dir_moves_settings_decks_and_cache(self):
        self.patch_env(MANTICORE_PORTABLE="0", MANTICORE_USER_DIR=self.override)
        self.assertEqual(paths.user_dir(), self.override)
        self.assertEqual(paths.local_dir(), self.override)
        self.assertTrue(paths.settings_file().startswith(self.override + os.sep))
        self.assertTrue(paths.cache_dir().startswith(self.override + os.sep))

    def test_manticore_data_dir_only_moves_logs_and_saves(self):
        data = os.path.join(self.tmp, "data_only")
        self.patch_env(MANTICORE_PORTABLE="0", MANTICORE_DATA_DIR=data)
        self.assertEqual(paths.log_dir(), data)
        self.assertEqual(paths.saves_dir(), os.path.join(data, "saves"))
        self.assertFalse(paths.settings_file().startswith(data + os.sep))     # settings.json is NOT moved by this one
        self.assertFalse(paths.cache_dir().startswith(data + os.sep))

    def test_manticore_data_dir_wins_over_manticore_user_dir_for_logs(self):
        user = os.path.join(self.tmp, "user_dir")
        data = os.path.join(self.tmp, "data_dir")
        self.patch_env(MANTICORE_PORTABLE="0", MANTICORE_USER_DIR=user, MANTICORE_DATA_DIR=data)
        self.assertEqual(paths.log_dir(), data)
        self.assertTrue(paths.settings_file().startswith(user + os.sep))       # everything else still follows USER_DIR


class NoImportTimeFoldersTests(EnvCase):
    """Re-importing paths and card_data with the environment pointed at an empty temp folder must not
    create anything there - only ensure_dirs() (and, for card_data, the first CardDataStore()) may."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="manticore_round28_empty_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_reimporting_paths_creates_nothing(self):
        self.patch_env(MANTICORE_PORTABLE="0", MANTICORE_USER_DIR=self.tmp)
        importlib.reload(paths)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_reimporting_card_data_creates_nothing(self):
        self.patch_env(MANTICORE_PORTABLE="0", MANTICORE_USER_DIR=self.tmp)
        importlib.reload(paths)
        import card_data
        importlib.reload(card_data)
        try:
            self.assertEqual(os.listdir(self.tmp), [])
        finally:
            # leave both modules as later tests expect them
            for k in ("MANTICORE_PORTABLE", "MANTICORE_USER_DIR"):
                os.environ.pop(k, None)
            importlib.reload(paths)
            importlib.reload(card_data)

    def test_constructing_a_card_data_store_makes_its_own_cache_folders_on_first_use(self):
        self.patch_env(MANTICORE_PORTABLE="0", MANTICORE_USER_DIR=self.tmp)
        importlib.reload(paths)
        import card_data
        importlib.reload(card_data)
        try:
            self.assertFalse(card_data.CACHE_DIR.exists())
            with mock.patch.object(card_data.requests, "Session"):
                card_data.CardDataStore()
            self.assertTrue(card_data.CACHE_DIR.is_dir())
            self.assertTrue(card_data.IMAGE_CACHE_DIR.is_dir())
        finally:
            for k in ("MANTICORE_PORTABLE", "MANTICORE_USER_DIR"):
                os.environ.pop(k, None)
            importlib.reload(paths)
            importlib.reload(card_data)


class MigrationTests(EnvCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="manticore_round28_migrate_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.program = os.path.join(self.tmp, "program")
        self.user = os.path.join(self.tmp, "user")
        os.makedirs(self.program)
        self.patch_env(MANTICORE_PORTABLE="0", MANTICORE_USER_DIR=self.user)
        self.patcher = mock.patch.object(paths, "program_dir", return_value=self.program)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write_old(self, name, content="x"):
        path = os.path.join(self.program, name)
        if name.endswith("/") or "." not in os.path.basename(name):
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "a.txt"), "w") as f:
                f.write(content)
        else:
            with open(path, "w") as f:
                f.write(content)
        return path

    def test_nothing_to_copy_does_nothing(self):
        self.assertEqual(paths.migrate_from_program_dir(), [])
        self.assertFalse(os.path.exists(os.path.join(self.user, "settings.json")))

    def test_copies_settings_config_decks_and_saves(self):
        self.write_old("settings.json", '{"a": 1}')
        self.write_old("bug_report_config.json", "{}")
        self.write_old("my_decks")
        self.write_old("saves")
        copied = paths.migrate_from_program_dir()
        self.assertEqual(set(copied), {"settings.json", "bug_report_config.json", "my_decks", "saves"})
        self.assertTrue(os.path.isfile(os.path.join(self.user, "settings.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.user, "my_decks", "a.txt")))
        # the originals are untouched (copy, never move)
        self.assertTrue(os.path.isfile(os.path.join(self.program, "settings.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.program, "my_decks", "a.txt")))

    def test_never_overwrites_an_existing_file(self):
        self.write_old("settings.json", "old content")
        os.makedirs(self.user, exist_ok=True)
        with open(os.path.join(self.user, "settings.json"), "w") as f:
            f.write("already here, real settings")
        # remove the marker so migration would otherwise run (simulating a hand-placed settings.json)
        copied = paths.migrate_from_program_dir()
        self.assertNotIn("settings.json", copied)
        with open(os.path.join(self.user, "settings.json")) as f:
            self.assertEqual(f.read(), "already here, real settings")

    def test_running_it_again_does_nothing_because_of_the_marker(self):
        self.write_old("settings.json")
        first = paths.migrate_from_program_dir()
        self.assertEqual(first, ["settings.json"])
        self.write_old("bug_report_config.json")           # something NEW appears in the program folder afterwards
        second = paths.migrate_from_program_dir()
        self.assertEqual(second, [])                        # the marker stops a second look entirely
        self.assertFalse(os.path.exists(os.path.join(self.user, "bug_report_config.json")))

    def test_portable_copies_never_migrate(self):
        self.patch_env(MANTICORE_PORTABLE="1", MANTICORE_USER_DIR=self.user)
        self.write_old("settings.json")
        self.assertEqual(paths.migrate_from_program_dir(), [])


class SyncBridgeInstalledModeTests(EnvCase):
    """forge_client.sync_bridge() must not touch forge_runtime/ in an installed copy - the installer ships
    the right jar - and must still work exactly as before in portable mode."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="manticore_round28_bridge_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, "forge_bridge.jar")
        with open(self.source, "wb") as f:
            f.write(b"new jar bytes")
        self.runtime = os.path.join(self.tmp, "forge_runtime")
        os.makedirs(self.runtime)

    def test_installed_mode_writes_nothing(self):
        self.patch_env(MANTICORE_PORTABLE="0")
        import forge_client as fc
        self.assertFalse(fc.sync_bridge(runtime=self.runtime, source=self.source))
        self.assertEqual(os.listdir(self.runtime), [])

    def test_portable_mode_still_copies(self):
        self.patch_env(MANTICORE_PORTABLE="1")
        import forge_client as fc
        self.assertTrue(fc.sync_bridge(runtime=self.runtime, source=self.source))
        with open(os.path.join(self.runtime, "forge_bridge.jar"), "rb") as f:
            self.assertEqual(f.read(), b"new jar bytes")


if __name__ == "__main__":
    unittest.main()
