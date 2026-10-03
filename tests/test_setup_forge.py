# SPDX-License-Identifier: GPL-3.0-or-later
"""setup_forge.py: bundle checking and unpacking, with tiny fake bundles (no Java, no real Forge)."""
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import setup_forge as sf


def make_tar_xz(files, extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info, data in (extra or []):
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def write_bundle(folder, blob, parts=3):
    os.makedirs(folder, exist_ok=True)
    size = max(1, -(-len(blob) // parts))
    entries = []
    for i in range(0, len(blob), size):
        chunk = blob[i:i + size]
        name = f"forge_runtime.tar.xz.part{i // size + 1:02d}"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(chunk)
        entries.append({"name": name, "size": len(chunk), "sha256": hashlib.sha256(chunk).hexdigest()})
    manifest = {"format": 1, "forge_version": "test", "forge_commit": "abc", "tar_sha256": hashlib.sha256(blob).hexdigest(),
                "parts": entries}
    with open(os.path.join(folder, "forge_runtime.manifest.json"), "w") as f:
        json.dump(manifest, f)
    return manifest


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        patches = {"BASE": self.base, "BUNDLE": os.path.join(self.base, "forge_bundle"),
                   "MANIFEST": os.path.join(self.base, "forge_bundle", "forge_runtime.manifest.json"),
                   "RUNTIME": os.path.join(self.base, "forge_runtime"),
                   "BRIDGE_JAR": os.path.join(self.base, "java_bridge", "forge_bridge.jar"),
                   "BRIDGE_SRC": os.path.join(self.base, "java_bridge", "src")}
        for name, value in patches.items():
            p = mock.patch.object(sf, name, value)
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(os.path.join(self.base, "java_bridge"))
        with open(sf.BRIDGE_JAR, "wb") as f:
            f.write(b"bridge")
        self.files = {"forge.jar": b"J" * 5000, "res/cardsfolder/a/abandon.txt": b"Name:Abandon\n" * 50,
                      "res/editions/x.txt": b"x"}
        self.blob = make_tar_xz(self.files)
        self.manifest = write_bundle(sf.BUNDLE, self.blob)

    def run_main(self, *args, java="java"):
        out = io.StringIO()
        with mock.patch.object(fc, "find_java", return_value=java), mock.patch.object(sf.fc, "find_java", return_value=java), \
                redirect_stdout(out):
            code = sf.main(list(args))
        return code, out.getvalue()


class BundleCheckTests(Base):
    def test_good_bundle_verifies(self):
        self.assertEqual(len(sf.verify_parts(self.manifest, sf.BUNDLE)), 3)

    def test_missing_part_is_named(self):
        os.remove(os.path.join(sf.BUNDLE, "forge_runtime.tar.xz.part02"))
        with self.assertRaises(sf.SetupError) as cm:
            sf.verify_parts(self.manifest, sf.BUNDLE)
        self.assertIn("part02", str(cm.exception))
        self.assertIn("missing", str(cm.exception))

    def test_truncated_part_is_named(self):
        path = os.path.join(sf.BUNDLE, "forge_runtime.tar.xz.part01")
        with open(path, "r+b") as f:
            f.truncate(10)
        with self.assertRaises(sf.SetupError) as cm:
            sf.verify_parts(self.manifest, sf.BUNDLE)
        self.assertIn("part01", str(cm.exception))
        self.assertIn("cut off", str(cm.exception))

    def test_flipped_byte_is_caught_by_the_checksum(self):
        path = os.path.join(sf.BUNDLE, "forge_runtime.tar.xz.part03")
        data = bytearray(open(path, "rb").read())
        data[3] ^= 0xFF
        open(path, "wb").write(bytes(data))
        with self.assertRaises(sf.SetupError) as cm:
            sf.verify_parts(self.manifest, sf.BUNDLE)
        self.assertIn("checksum", str(cm.exception))

    def test_missing_manifest_says_what_to_do(self):
        os.remove(sf.MANIFEST)
        with self.assertRaises(sf.SetupError) as cm:
            sf.read_manifest()
        self.assertIn("forge_bundle", str(cm.exception))


class InstallTests(Base):
    def test_install_unpacks_everything_and_leaves_no_temp_folders(self):
        sf.install(self.manifest)
        for name, data in self.files.items():
            with open(os.path.join(sf.RUNTIME, *name.split("/")), "rb") as f:
                self.assertEqual(f.read(), data)
        self.assertFalse(os.path.exists(sf.RUNTIME + ".new"))
        self.assertFalse(os.path.exists(sf.RUNTIME + ".old"))

    def test_reinstall_replaces_the_old_runtime(self):
        os.makedirs(sf.RUNTIME)
        with open(os.path.join(sf.RUNTIME, "stale.txt"), "w") as f:
            f.write("old")
        sf.install(self.manifest)
        self.assertFalse(os.path.exists(os.path.join(sf.RUNTIME, "stale.txt")))
        self.assertTrue(os.path.isfile(os.path.join(sf.RUNTIME, "forge.jar")))

    def test_wrong_overall_checksum_installs_nothing(self):
        self.manifest["tar_sha256"] = "0" * 64
        with self.assertRaises(sf.SetupError):
            sf.install(self.manifest)
        self.assertFalse(os.path.exists(sf.RUNTIME))
        self.assertFalse(os.path.exists(sf.RUNTIME + ".new"))

    def test_paths_that_escape_the_folder_are_refused(self):
        evil = tarfile.TarInfo("../evil.txt")
        evil.size = 4
        blob = make_tar_xz({"forge.jar": b"J"}, extra=[(evil, b"evil")])
        manifest = write_bundle(sf.BUNDLE, blob)
        with self.assertRaises(sf.SetupError) as cm:
            sf.install(manifest)
        self.assertIn("unsafe", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.base, "evil.txt")))

    def test_links_in_a_bundle_are_skipped(self):
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        blob = make_tar_xz({"forge.jar": b"J"}, extra=[(link, None)])
        manifest = write_bundle(sf.BUNDLE, blob)
        sf.install(manifest)
        self.assertFalse(os.path.lexists(os.path.join(sf.RUNTIME, "link")))


class MainTests(Base):
    def test_main_installs_and_reports_java_problem_kindly(self):
        code, out = self.run_main(java=None)
        self.assertEqual(code, 1)
        self.assertIn("installed in forge_runtime", out)
        self.assertIn("winget install EclipseAdoptium.Temurin", out)
        self.assertTrue(os.path.isfile(os.path.join(sf.RUNTIME, "forge_bridge.jar")))
        self.assertTrue(os.path.isfile(os.path.join(sf.RUNTIME, "VERSION.txt")))

    def test_main_success_path_points_at_the_game(self):
        with mock.patch.object(fc, "runtime_problem", return_value=None):
            code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("python forge_table.py", out)

    def test_second_run_does_not_unpack_again(self):
        with mock.patch.object(fc, "runtime_problem", return_value=None):
            self.run_main()
            with mock.patch.object(sf, "install") as install:
                code, out = self.run_main()
        install.assert_not_called()
        self.assertIn("already installed", out)

    def test_force_unpacks_again(self):
        with mock.patch.object(fc, "runtime_problem", return_value=None):
            self.run_main()
            with mock.patch.object(sf, "install") as install:
                self.run_main("--force")
        install.assert_called_once()

    def test_damaged_bundle_gives_a_setup_problem_message(self):
        os.remove(os.path.join(sf.BUNDLE, "forge_runtime.tar.xz.part01"))
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("Setup problem", out)
        self.assertIn("part01", out)


if __name__ == "__main__":
    unittest.main()
