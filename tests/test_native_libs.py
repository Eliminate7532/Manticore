# SPDX-License-Identifier: GPL-3.0-or-later
"""licenses/NOTICES.json native_libraries: the 16 DLLs in pygame-ce 2.5.8's Windows wheel (filled 2026-10-02).

tools/build_notices.py --release checks the DLLs actually installed, but only on Windows; these checks run everywhere."""
import os
import unittest

import licenses_view as lv
from tools import build_notices as bn

WHEEL_DLLS = {
    "SDL2.dll", "SDL2_image.dll", "SDL2_mixer.dll", "SDL2_ttf.dll", "freetype.dll", "libjpeg-62.dll", "libogg-0.dll",
    "libopus-0.dll", "libopusfile-0.dll", "libpng16-16.dll", "libtiff-5.dll", "libwavpack-1.dll", "libwebp-7.dll",
    "libwebpdemux-2.dll", "libxmp.dll", "portmidi.dll",
}


class NativeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.natives = lv.load_manifest()["native_libraries"]
        self.libs = self.natives["libraries"]

    def test_every_dll_in_the_pinned_wheel_is_listed_once(self):
        files = [lib["file"] for lib in self.libs]
        self.assertEqual(sorted(files), sorted(WHEEL_DLLS))

    def test_it_is_no_longer_marked_not_collected(self):
        self.assertNotIn("_status", self.natives)
        self.assertNotIn(("native_libraries", "not_collected"), bn.collect_statuses(lv.load_manifest()))

    def test_every_licence_id_has_a_text_file(self):
        missing = [(lib["id"], spdx) for lib in self.libs for spdx in lv.license_ids(lib["license"])
                   if lv.license_text(spdx)[1]]
        self.assertEqual(missing, [])

    def test_every_notice_file_exists_and_is_not_empty(self):
        for lib in self.libs:
            files = lib["notice_file"] if isinstance(lib["notice_file"], list) else [lib["notice_file"]]
            for rel in files:
                path = os.path.join(bn.LICENSES_DIR, rel)
                with self.subTest(lib=lib["id"], file=rel):
                    self.assertTrue(os.path.isfile(path))
                    self.assertGreater(os.path.getsize(path), 200)

    def test_each_entry_says_where_the_dll_came_from_and_is_verified(self):
        for lib in self.libs:
            with self.subTest(lib=lib["id"]):
                self.assertIn("byte-identical", lib["source"])
                self.assertEqual(lib["status"], "verified")
                self.assertTrue(lib["copyright"])

    def test_the_manifest_validates_and_the_notices_file_is_current(self):
        m = lv.load_manifest()
        self.assertEqual(bn.validate_manifest(m), [])
        with open(bn.OUTPUT_PATH, encoding="utf-8") as f:
            self.assertEqual(f.read(), bn.generate_notices_text(m))
        self.assertIn("--- NOTICE (notices/native/harfbuzz.txt) ---", bn.generate_notices_text(m))


class GrokCreditTests(unittest.TestCase):
    def test_the_credits_say_created_with_grok_once(self):
        m = lv.load_manifest()
        lines = [c for c in m["credits"] if "Created with Grok" in c.get("text", "")]
        self.assertEqual([c["id"] for c in lines], ["grok-art"])
        by_id = {c["id"]: c for c in m["components"]}
        self.assertIn("Created with Grok", by_id["art-backgrounds"]["copyright"])

    def test_it_shows_in_the_licences_window_and_the_notices_file(self):
        m = lv.load_manifest()
        self.assertTrue(any("Created with Grok" in text for text, _c, _b in lv._credits_lines(m)))
        self.assertIn("Created with Grok", bn.generate_notices_text(m))

    def test_the_art_passes_the_release_check_because_the_attribution_is_given(self):
        m = lv.load_manifest()
        by_id = {c["id"]: c for c in m["components"]}
        self.assertEqual((by_id["art-backgrounds"]["status"], by_id["art-backgrounds"]["license"]), ("attribution", "NOASSERTION"))
        self.assertIn("Created with Grok", by_id["art-backgrounds"]["attribution"])
        self.assertFalse([e for e in bn.unverified_entries(m) if "[art-backgrounds]" in e])
        self.assertEqual(bn.validate_manifest(m), [])

    def test_an_attribution_entry_without_its_attribution_text_is_refused(self):
        m = lv.load_manifest()
        for c in m["components"]:
            if c["id"] == "art-backgrounds":
                c["attribution"] = ""
        self.assertTrue(any("art-backgrounds" in p for p in bn.validate_manifest(m)))

    def test_running_the_script_twice_changes_nothing(self):
        from tools import native_libs_2026_10_02 as nl
        m = lv.load_manifest()
        before = [dict(c) for c in m["credits"]]
        nl.grok_attribution(m)
        self.assertEqual(m["credits"], before)


class JavaAndPygameTests(unittest.TestCase):
    def test_the_checked_entries_are_verified_with_their_texts(self):
        m = lv.load_manifest()
        java = m["java_components"]
        for key in ("com.googlecode.soundlibs", "javazoom", "com.miglayout", "io.github.x-stream", "javax.servlet"):
            with self.subTest(key=key):
                self.assertEqual(java[key]["status"], "verified")
                self.assertEqual([s for s in lv.license_ids(java[key]["license"]) if lv.license_text(s)[1]], [])
        self.assertIn("BSD-2-Clause", java["com.googlecode.soundlibs"]["license"])
        self.assertIn("Apache-2.0", java["javax.servlet"]["license"])
        pg = {c["id"]: c for c in m["components"]}["pygame-ce"]
        self.assertEqual((pg["license"], pg["status"]), ("LGPL-2.0-or-later", "verified"))

    def test_only_the_runtimes_the_installer_will_pin_are_left(self):
        left = sorted(e.split("[")[1].split("]")[0] for e in bn.unverified_entries(lv.load_manifest()))
        self.assertEqual(left, ["python", "temurin"])


if __name__ == "__main__":
    unittest.main()
