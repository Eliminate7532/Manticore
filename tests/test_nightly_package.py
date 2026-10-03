# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for tools/nightly_package.py: the nightly test package never carries private files, and it always has what a tester needs."""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import nightly_package as npk  # noqa: E402


def _write(base, rel, text="x"):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class NightlyPackageTests(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()
        for rel in ("LICENSE", "THIRD_PARTY_NOTICES.txt", "licenses/NOTICES.json", "forge_table.py", "requirements.txt",
                    "sample_decks/typal_lathril.txt", "forge_bundle/forge_runtime.manifest.json",
                    "forge_bundle/forge_runtime.tar.xz.part01", "tests/test_x.py"):
            _write(self.src, rel)
        for rel in ("my_decks/karl.txt", "settings.json", "bug_report_config.json", "docs/incoming/notes.md",
                    "CLAUDE.md", "backup_status.json", "ci/tests.yml", ".github/workflows/tests.yml",
                    "forge_runtime/forge.jar", "bug_reports/r1.json"):
            _write(self.src, rel, "private")

    def tearDown(self):
        shutil.rmtree(self.src, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def names(self, path):
        with zipfile.ZipFile(path) as z:
            return [n.split("/", 1)[1] for n in z.namelist()]

    def test_has_what_a_tester_needs_and_nothing_private(self):
        path, count = npk.build(self.out, "2026-09-26", "abcdef1234567890", base_dir=self.src)
        self.assertTrue(path.endswith("Manticore-nightly-2026-09-26.zip"))
        names = self.names(path)
        self.assertEqual(count, len(names))
        for needed in ("LICENSE", "THIRD_PARTY_NOTICES.txt", "HOW_TO_RUN.txt", "BUILD_INFO.txt", "forge_table.py",
                       "sample_decks/typal_lathril.txt", "forge_bundle/forge_runtime.manifest.json"):
            self.assertIn(needed, names)
        for name in names:
            for private in ("my_decks/", "settings.json", "bug_report_config.json", "docs/", "CLAUDE.md",
                            "backup_status.json", "ci/", ".github/", "forge_runtime/", "bug_reports/", "tests/"):
                self.assertFalse(name.startswith(private), name)
        with zipfile.ZipFile(path) as z:
            info = z.read("Manticore-nightly-2026-09-26/BUILD_INFO.txt").decode()
        self.assertIn("abcdef123456", info)
        self.assertTrue(os.path.isfile(path + ".sha256"))
        self.assertEqual(sorted(os.listdir(self.out)), sorted([os.path.basename(path), os.path.basename(path) + ".sha256"]))

    def test_a_planted_webhook_refuses_and_leaves_no_zip(self):
        real_shaped = "https://discord.com/api/webhooks/" + "1" * 18 + "/" + "AbC-dEf_" * 9        # Round PUB1: the scan wants a real-shaped one
        _write(self.src, "leaky.py", "W = '%s'\n" % real_shaped)
        with self.assertRaises(npk.PackageError) as ctx:
            npk.build(self.out, "2026-09-26", base_dir=self.src)
        self.assertIn("leaky.py", str(ctx.exception))
        self.assertEqual(os.listdir(self.out), [])

    def test_fake_webhooks_in_tests_do_not_block_the_build(self):
        _write(self.src, "tests/test_y.py", "W = 'https://discord.com/api/webhooks/123456789012345678/AbCdEf-GhIj'\n")
        path, _ = npk.build(self.out, "2026-09-26", base_dir=self.src)
        self.assertNotIn("tests/test_y.py", self.names(path))

    def test_no_licence_refuses(self):
        os.remove(os.path.join(self.src, "THIRD_PARTY_NOTICES.txt"))
        with self.assertRaises(npk.PackageError):
            npk.build(self.out, "2026-09-26", base_dir=self.src)
        self.assertEqual(os.listdir(self.out), [])

    def test_no_forge_bundle_refuses(self):
        shutil.rmtree(os.path.join(self.src, "forge_bundle"))
        with self.assertRaises(npk.PackageError):
            npk.build(self.out, "2026-09-26", base_dir=self.src)
        self.assertEqual(os.listdir(self.out), [])

    def test_never_overwrites(self):
        npk.build(self.out, "2026-09-26", base_dir=self.src)
        with self.assertRaises(npk.PackageError):
            npk.build(self.out, "2026-09-26", base_dir=self.src)

    def test_the_final_check_catches_a_private_name(self):
        with self.assertRaises(npk.PackageError):
            npk.check_names(["Top/my_decks/karl.txt"], "Top")
        npk.check_names(["Top/sample_decks/a.txt", "Top/my_decks_readme_is_not_a_folder.txt"], "Top")


if __name__ == "__main__":
    unittest.main()
