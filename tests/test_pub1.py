# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PUB1: tools/export_public.py can make the public source copy.

Before this round the export refused on the project itself: several tests hold short fake webhook addresses on purpose
("webhooks/123/SECRETTOKEN"), and its pattern matched any number and any token. It would also have published Karl's working
notes (docs/: 98 MB of round notes, specs and patches, many with his home folder's path) and the scripts that cd into his
folder. The fake webhooks in this file are built at run time, so the file itself never holds a real-shaped one.
"""
import os
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))

import export_public as ep

REAL_SHAPED = "https://discord.com/api/webhooks/" + "4" * 18 + "/" + "Xy-9_aBc" * 8 + "Zz12"      # 68-character token


def write(base, rel, text="x"):
    path = os.path.join(base, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.out = os.path.join(tempfile.mkdtemp(), "public")
        self.addCleanup(shutil.rmtree, self.src, True)
        self.addCleanup(shutil.rmtree, os.path.dirname(self.out), True)
        for rel in ("LICENSE", "THIRD_PARTY_NOTICES.txt", "licenses/NOTICES.json", "README.txt", "forge_table.py",
                    "ci/tests.yml", "tests/test_x.py", "sample_decks/a.txt"):
            write(self.src, rel)

    def exported(self):
        ep.export(self.out, base_dir=self.src)
        found = set()
        for root, _d, files in os.walk(self.out):
            for name in files:
                found.add(os.path.relpath(os.path.join(root, name), self.out).replace(os.sep, "/"))
        return found

    def test_short_fake_webhooks_in_tests_do_not_stop_it(self):
        write(self.src, "tests/test_x.py", "A = 'https://discord.com/api/webhooks/123/SECRETTOKEN'\n"
                                           "B = 'https://discord.com/api/webhooks/123456789012345678/AbC-dEf_123'\n")
        self.assertIn("tests/test_x.py", self.exported())

    def test_a_real_shaped_webhook_anywhere_stops_it(self):
        for variant in (REAL_SHAPED, REAL_SHAPED.replace("https://", "https://ptb."),
                        REAL_SHAPED.replace("discord.com", "discordapp.com")):
            write(self.src, "tests/test_x.py", "W = %r\n" % variant)
            with self.assertRaises(ep.ExportError) as ctx:
                ep.export(self.out, base_dir=self.src)
            self.assertIn("test_x.py", str(ctx.exception))
            self.assertFalse(os.path.exists(self.out))
            self.assertEqual([rel for rel, _why in ep.check(self.src)], ["tests/test_x.py"])

    def test_karls_working_notes_stay_out(self):
        for rel in ("docs/incoming/r1/r1.patch", "docs/WORKING_AGREEMENT.md", "CLAUDE.md", "EVENING_CHECKLIST.txt",
                    "backup.bat", "backup_task.bat", ".github/workflows/nightly.yml", "soak_runs/run_1/soak_summary.txt",
                    "session.json", "backup_status.json"):
            write(self.src, rel)
        found = self.exported()
        for gone in ("docs/", "CLAUDE.md", "EVENING_CHECKLIST.txt", "backup.bat", "backup_task.bat", ".github/",
                     "soak_runs/", "session.json", "backup_status.json"):
            self.assertFalse([f for f in found if f == gone or f.startswith(gone)], gone)
        for kept in ("README.txt", "forge_table.py", "ci/tests.yml", "tests/test_x.py", "sample_decks/a.txt", "LICENSE"):
            self.assertIn(kept, found)

    def test_only_top_level_notes_are_left_out(self):
        write(self.src, "tools/CLAUDE.md", "a tool's own notes")
        self.assertIn("tools/CLAUDE.md", self.exported())


class HomePathTests(unittest.TestCase):
    """A file with this computer's home folder in it (a captured test run, an instruction with a real path) stays private."""

    HOME = "C:\\Users\\Some Person"

    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.out = os.path.join(tempfile.mkdtemp(), "public")
        self.addCleanup(shutil.rmtree, self.src, True)
        self.addCleanup(shutil.rmtree, os.path.dirname(self.out), True)
        for rel in ("LICENSE", "THIRD_PARTY_NOTICES.txt", "licenses/NOTICES.json", "README.txt"):
            write(self.src, rel)
        self.real = real = ep.home_forms
        ep.home_forms = lambda home=None: real(self.HOME)
        self.addCleanup(setattr, ep, "home_forms", real)

    def test_the_forms_of_a_home_folder(self):
        self.assertEqual(self.real(self.HOME), ["c:/users/some person", "c:\\\\users\\\\some person", "c:\\users\\some person"])
        self.assertEqual(self.real("/home/alice"), ["/home/alice", "\\\\home\\\\alice", "\\home\\alice"])
        self.assertEqual(self.real("/root"), [])

    def test_a_home_path_in_any_form_stops_it(self):
        for text in ('cd /d "C:\\Users\\Some Person\\Documents"', "p = 'c:/users/some person/x'",
                     'P = "C:\\\\Users\\\\Some Person\\\\x"'):
            write(self.src, "tools/run.bat", text)
            with self.assertRaises(ep.ExportError) as ctx:
                ep.export(self.out, base_dir=self.src)
            self.assertIn("tools/run.bat".replace("/", os.sep), str(ctx.exception))
            self.assertIn("home folder", str(ctx.exception))

    def test_powershell_utf16_output_is_read_too(self):
        os.makedirs(os.path.join(self.src, "tools"), exist_ok=True)
        with open(os.path.join(self.src, "tools", "out.txt"), "wb") as f:
            f.write("python : C:\\Users\\Some Person\\Documents\\x.py:5\r\n".encode("utf-16"))
        self.assertEqual(ep.check(self.src), [("tools/out.txt", "contains this computer's home folder path (c:\\users\\some person)")])

    def test_other_text_files_at_the_top_and_pytest_caches_stay_out(self):
        write(self.src, "quick.txt", "a captured test run")
        write(self.src, "START_HERE.txt")
        write(self.src, "requirements.txt")
        write(self.src, ".pytest_cache/v/cache/lastfailed", "{}")
        write(self.src, "tests/notes.txt", "kept: only the top level is filtered")
        ep.export(self.out, base_dir=self.src)
        found = set()
        for root, _d, files in os.walk(self.out):
            for name in files:
                found.add(os.path.relpath(os.path.join(root, name), self.out).replace(os.sep, "/"))
        self.assertNotIn("quick.txt", found)
        self.assertFalse([f for f in found if f.startswith(".pytest_cache")])
        for kept in ("README.txt", "START_HERE.txt", "requirements.txt", "THIRD_PARTY_NOTICES.txt", "tests/notes.txt"):
            self.assertIn(kept, found)


class ProjectTests(unittest.TestCase):
    def test_the_project_as_it_stands_would_export(self):
        self.assertEqual(ep.check(BASE), [])

    def test_the_project_export_has_no_working_notes(self):
        files = list(ep.iter_source_files(BASE))
        self.assertIn("forge_table.py", files)
        self.assertIn("LICENSE", files)
        self.assertFalse([f for f in files if f.startswith(("docs/", ".github/", "soak_runs/")) or f in ("CLAUDE.md", "EVENING_CHECKLIST.txt")])

    def test_this_file_holds_no_real_shaped_webhook(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            self.assertIsNone(ep.WEBHOOK_RE.search(f.read()))


if __name__ == "__main__":
    unittest.main()
