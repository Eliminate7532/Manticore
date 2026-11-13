# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ARM1: MANTICORE_JAVA, the developer's switch that points any copy (the installed one too) at another Java, so the
Surface can time its emulated x64 jre/ against a native Arm64 JDK; and java_arch, which the bench prints."""
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import forge_client as fc          # noqa: E402


def fake_java(folder, version="21.0.8", arch="aarch64", name=None):
    """A stand-in java that answers -version (and -XshowSettings:properties) on stderr, as a real one does."""
    name = name or ("java.exe" if os.name == "nt" else "java")
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n"
                "echo '    os.arch = %s' >&2\n"
                "echo 'openjdk version \"%s\" 2025-07-15 LTS' >&2\n" % (arch, version))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@unittest.skipIf(os.name == "nt", "the stand-in java is a shell script")
class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, "jdk", "bin")
        os.makedirs(self.bin)

    def env(self, value):
        return mock.patch.dict(os.environ, {fc.JAVA_OVERRIDE_ENV: value})

    def test_unset_changes_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(fc.JAVA_OVERRIDE_ENV, None)
            self.assertEqual(fc.java_override(), (None, None))
            with mock.patch.object(fc, "bundled_runtime_expected", return_value=True), \
                    mock.patch.object(fc, "bundled_java_path", return_value=os.path.join(self.tmp, "nope", "java")):
                self.assertIsNone(fc.find_java())       # the installed copy's own rule, unchanged

    def test_a_java_file_wins_even_for_an_installed_copy(self):
        java = fake_java(self.bin)
        with self.env(java), mock.patch.object(fc, "bundled_runtime_expected", return_value=True), \
                mock.patch.object(fc, "bundled_java_path", return_value=os.path.join(self.tmp, "nope", "java")):
            self.assertEqual(fc.find_java(), java)

    def test_a_jdk_folder_means_its_bin_java(self):
        java = fake_java(self.bin)
        with self.env('"%s"' % os.path.join(self.tmp, "jdk")):     # quotes as pasted from Explorer
            self.assertEqual(fc.find_java(), java)

    def test_a_wrong_path_is_said_not_silently_replaced(self):
        fake_java(self.bin)                       # a good java on JAVA_HOME must NOT be used instead
        with self.env(os.path.join(self.tmp, "missing")), \
                mock.patch.dict(os.environ, {"JAVA_HOME": os.path.join(self.tmp, "jdk")}), \
                mock.patch.object(fc, "bundled_runtime_expected", return_value=False):
            self.assertIsNone(fc.find_java())
            problem = fc.runtime_problem(self.tmp)
        self.assertIn("MANTICORE_JAVA", problem)
        self.assertIn("no java there", problem)

    def test_too_old_a_java_is_refused(self):
        java = fake_java(self.bin, version="11.0.2")
        with self.env(java):
            self.assertIsNone(fc.find_java())
            self.assertIn("needs 17", fc.java_override()[1])

    def test_java_arch_reads_os_arch(self):
        self.assertEqual(fc.java_arch(fake_java(self.bin, arch="aarch64")), "aarch64")
        self.assertEqual(fc.java_arch(fake_java(self.bin, arch="amd64", name="java2")), "amd64")
        self.assertEqual(fc.java_arch(os.path.join(self.tmp, "missing")), "")


class BenchSourceTests(unittest.TestCase):
    def test_the_bench_says_which_java_it_timed(self):
        with open(os.path.join(ROOT, "tools", "bench_board.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("fc.java_arch(java)", src)
        self.assertIn('results["java"]', src)
        self.assertIn("java_line", src.split('"bench_board.txt"')[1])   # the line is in the written report too


if __name__ == "__main__":
    unittest.main()
