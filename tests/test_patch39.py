# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 39 (4 Oct 2026), from Karl's bug report of 09:06 (the installed 0.28.41 on his Surface Pro 11, Windows 11 on Arm):
1. The deck screen froze for about 50 s at the first start (the watchdog's "FROZEN" twice; Karl closed it once): the "not in this
   version of Forge" check read all ~35,000 card scripts on the window's thread (forge_client._card_name_index). The index is now
   shipped with an installed build (forge_card_names.json, made by tools/build_installer.py), saved in the card cache otherwise
   (keyed by the Forge build), and built on a background thread started with the program; the deck screen never waits for it.
2. The report's computer section described the logs folder, not the program ("code unknown | installed build unknown",
   "forge_runtime: missing") in an installed copy.
3. "Python 3.14.7 (ARM64)": Python's platform.machine() names the processor even for the x64 build, so patch 37's emulation line
   never showed; the build's own architecture now comes from sysconfig (tests/test_patch37.py).
4. "Update check failed: the update feed answered 404" at every start - no release is published yet; no longer a crash-log entry."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deck_library as lib
import paths

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake_runtime(folder, names=("Forest", "Island"), forge="Forge 2.0.16-SNAPSHOT | commit fb4d809"):
    rt = os.path.join(folder, "rt")
    os.makedirs(os.path.join(rt, "res", "cardsfolder", "f"), exist_ok=True)
    for i, n in enumerate(names):
        with open(os.path.join(rt, "res", "cardsfolder", "f", f"c{i}.txt"), "w", encoding="utf-8") as f:
            f.write(f"Name:{n}\nTypes:Land\n")
    with open(os.path.join(rt, "VERSION.txt"), "w", encoding="utf-8") as f:
        f.write(forge + "\n")
    return rt


class CardIndexTests(unittest.TestCase):
    def setUp(self):
        import forge_client as fc
        self.fc = fc
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rt = fake_runtime(self.tmp)
        self.addCleanup(lambda: (fc._NAME_INDEX.pop(self.rt, None), fc._INDEX_THREADS.pop(self.rt, None)))
        self.prog, self.cache = os.path.join(self.tmp, "prog"), os.path.join(self.tmp, "cache")
        for p in (mock.patch.object(paths, "program_dir", return_value=self.prog),
                  mock.patch.object(paths, "cache_dir", return_value=self.cache)):
            p.start()
            self.addCleanup(p.stop)

    def test_saved_and_read_back_for_the_same_forge_build_only(self):
        names = self.fc.build_card_name_index(self.rt)
        self.assertEqual(names, {"Forest", "Island"})
        path = self.fc.save_card_index(names, self.rt, os.path.join(self.cache, self.fc.CARD_INDEX_FILE))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.fc._load_saved_index(self.rt), names)
        other = fake_runtime(os.path.join(self.tmp, "other"), forge="Forge 2.0.17 | commit 0000000")
        self.assertIsNone(self.fc._load_saved_index(other))                   # another Forge build: not used

    def test_a_shipped_index_means_no_card_script_is_read(self):
        self.fc.save_card_index({"Forest", "Island", "Shipped Card"}, self.rt, os.path.join(self.prog, self.fc.CARD_INDEX_FILE))
        with mock.patch.object(self.fc, "build_card_name_index", side_effect=AssertionError("read the scripts")):
            self.assertTrue(self.fc.forge_knows("Shipped Card", self.rt, wait=False))
        self.assertTrue(self.fc.card_index_ready(self.rt))

    def test_the_deck_screen_never_waits_for_the_index(self):
        gate = threading.Event()

        def slow(rt):
            gate.wait(5)
            return {"Forest", "Island"}
        with mock.patch.object(self.fc, "build_card_name_index", side_effect=slow):
            t0 = time.time()
            self.assertIsNone(self.fc.forge_knows("Frest", self.rt, wait=False))
            self.assertEqual(self.fc.unknown_cards(["Frest"], self.rt, wait=False), [])
            problems = lib.describe_problems(["Forest"], ["Frest"], runtime=self.rt)
            self.assertFalse([p for p in problems if "Not in this version" in p[0]], problems)
            self.assertLess(time.time() - t0, 1.0)
            self.assertFalse(self.fc.card_index_ready(self.rt))
            gate.set()
            self.fc._INDEX_THREADS[self.rt].join(5)
        self.assertTrue(self.fc.card_index_ready(self.rt))
        self.assertEqual(self.fc.unknown_cards(["Forest", "Frest"], self.rt, wait=False), ["Frest"])
        self.assertTrue(os.path.exists(os.path.join(self.cache, self.fc.CARD_INDEX_FILE)) or
                        os.environ.get("MANTICORE_NO_CARD_INDEX_SAVE"))

    def test_a_deck_entry_works_its_problems_out_again_when_the_index_arrives(self):
        deck = os.path.join(self.tmp, "d.txt")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("Commander\n1 Forest\n\nDeck\n1 Frest\n")
        e = lib.DeckEntry(deck, "d", False).load()
        gate = threading.Event()
        with mock.patch.object(self.fc, "build_card_name_index", side_effect=lambda rt: gate.wait(5) and {"Forest"}):
            before = e.problems(self.rt)
            self.assertFalse(any("Not in this version" in p[0] for p in before))
            gate.set()
            self.fc._INDEX_THREADS[self.rt].join(5)
        after = e.problems(self.rt)
        self.assertTrue(any("Not in this version" in p[0] and "Frest" in p[0] for p in after), after)

    def test_the_test_suite_never_saves_into_the_project(self):
        self.assertTrue(os.environ.get("MANTICORE_NO_CARD_INDEX_SAVE"))
        self.assertIsNone(self.fc.save_card_index({"Forest"}, self.rt))

    def test_the_installer_build_ships_the_index(self):
        from tools import build_installer as bi
        from tools import check_dist as cd
        self.assertIn("forge_card_names.json", cd.REQUIRED_FILES)
        base = os.path.join(self.tmp, "base")
        shutil.copytree(self.rt, os.path.join(base, "forge_runtime"))
        b = bi.Builder(False, base=base, which=lambda n: None, run=lambda *a, **k: None, environ={}, say=lambda *_: None)
        b.dist_dir = os.path.join(self.tmp, "dist")
        b.jre_dir = os.path.join(self.tmp, "jre")
        os.makedirs(b.jre_dir)
        b.add_runtime()
        with open(os.path.join(b.dist_dir, "forge_card_names.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["names"], ["Forest", "Island"])
        self.assertIn("fb4d809", data["forge"])


class ReportTests(unittest.TestCase):
    def test_an_installed_copys_report_describes_the_program_folder(self):
        import crashlog
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(paths, "is_portable", return_value=False), \
                mock.patch.object(paths, "program_dir", return_value=HERE):
            lines = crashlog.environment(tmp)                   # tmp: the installed copy's logs folder
        self.assertTrue(any(l.startswith("forge_runtime: ") and "forge.jar" in l for l in lines), lines)

    def test_no_release_yet_is_not_a_crash_log_entry(self):
        import update_screens
        import updater

        class R:
            status_code = 404
        s = mock.Mock()
        s.get.return_value = R()
        info, err = updater.check("https://example.com/latest.json", session=s)
        self.assertIsNone(info)
        self.assertEqual(err, updater.NO_RELEASE)
        self.assertIn("404", err)
        with open(update_screens.__file__, encoding="utf-8") as f:
            self.assertIn("job.error != updater.NO_RELEASE", f.read())


if __name__ == "__main__":
    unittest.main()
