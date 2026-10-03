# SPDX-License-Identifier: GPL-3.0-or-later
"""Round FB1 (2 Oct 2026): Forge updated from 3a74143 to fb4d809, before the alpha (Karl: "We absolutely have to update
Forge before alpha release").

What the round has to keep true:
  * forge_bundle/, licenses/NOTICES.json and the bundle's README all name the same Forge build;
  * the bridge compiles against the new Forge: three zone methods Forge removed from IGuiGame are gone from BridgeGui
    (upstream #12023 "Move zone display decisions from host to client", #12056 "IGuiGame cleanup");
  * a forge_runtime/ unpacked from an OLDER forge_bundle/ is noticed: the game refuses to start and says to run
    setup_forge.py, and setup_forge.py reinstalls on its own (before FB1 it said "already installed" and kept the old Forge,
    which would then run the new bridge);
  * live, where Forge is set up: Reality Fracture's 16 cards the old build lacked are known.
"""
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import forge_client as fc
import setup_forge
import tests.live as live

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW = "fb4d8091126051b0c579db5f3bfdcb7e03aae63d"
OLD = "3a74143aa2a4d1475e929e140865bd0c574adf1c"

# Reality Fracture cards with no script in Forge 3a74143 (checked 2 Oct against the set's edition file); all have one at fb4d809.
FRA_NEW_IN_FB4D809 = ["Graft Surgeon", "Guiding Hydra", "Sanctum Lurker", "Command the Stage", "Omnipresence",
                      "Ferocity of the Hunt", "Primal Witchstalker", "Rescue Girl, First Responder", "Tomik, Orzhov Lawmage",
                      "Way of the Mind Sculptor", "Loot, the Anomaly", "Tomik, Izzet Sparkmage", "Loot, the Nexus",
                      "Hapatra, the Desert Fang", "Void Extrapolator", "Theorix Metamage"]


def read(*parts):
    with open(os.path.join(BASE, *parts), "r", encoding="utf-8") as f:
        return f.read()


class BuildIsNamedTheSameEverywhereTests(unittest.TestCase):
    def manifest(self):
        return json.loads(read("forge_bundle", "forge_runtime.manifest.json"))

    def test_the_bundle_is_forge_fb4d809(self):
        m = self.manifest()
        self.assertEqual(m["forge_commit"], NEW)
        self.assertTrue(m["forge_version"].startswith("2.0.16-SNAPSHOT"))
        self.assertEqual(sum(p["size"] for p in m["parts"]), m["tar_size"])

    def test_the_licence_manifest_names_the_same_build(self):
        forge = next(c for c in json.loads(read("licenses", "NOTICES.json"))["components"] if c["id"] == "forge")
        m = self.manifest()
        self.assertEqual(forge["version"], f"{m['forge_version'].split()[0]} (commit {m['forge_commit']})")

    def test_the_bundle_readme_and_third_party_notices_name_it(self):
        self.assertIn(NEW, read("forge_bundle", "README.txt"))
        self.assertIn("2.0.16-SNAPSHOT-jar-with-dependencies.jar", read("forge_bundle", "README.txt"))
        self.assertIn(NEW, read("THIRD_PARTY_NOTICES.txt"))
        self.assertNotIn(OLD, read("THIRD_PARTY_NOTICES.txt"))

    def test_the_parts_match_their_checksums_where_they_are_present(self):
        m = self.manifest()
        if not all(os.path.isfile(os.path.join(BASE, "forge_bundle", p["name"])) for p in m["parts"]):
            self.skipTest("forge_bundle parts are not in this copy")
        setup_forge.verify_parts(m)                      # raises SetupError on any size or checksum mismatch


class BridgeSourceTests(unittest.TestCase):
    def test_bridge_gui_no_longer_overrides_the_removed_zone_methods(self):
        src = read("java_bridge", "src", "forge", "bridge", "BridgeGui.java")
        for name in ("tempShowZones", "hideZones", "restoreOldZones"):
            self.assertIsNone(re.search(r"public\s+\S+\s+" + name + r"\s*\(", src), name)
        # openZones is now a void default in IGuiGame; the old override returned PlayerZoneUpdates
        self.assertIsNone(re.search(r"PlayerZoneUpdates\s+openZones\s*\(", src))


def runtime_with(folder, commit=None, first_line=None):
    os.makedirs(os.path.join(folder, "res", "cardsfolder"), exist_ok=True)
    for name in ("forge.jar", "forge_bridge.jar"):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"x")
    if commit or first_line:
        with open(os.path.join(folder, "VERSION.txt"), "w", encoding="utf-8") as f:
            f.write((first_line or f"Forge 2.0.15-SNAPSHOT (built from the master branch) (commit {commit})") + "\n"
                    "Free software under the GNU General Public License v3.\n")
    return folder


def manifest_with(path, commit):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"format": 1, "forge_version": "2.0.16-SNAPSHOT", "forge_commit": commit,
                   "parts": [{"name": "forge_runtime.tar.xz.part01", "size": 1, "sha256": "0" * 64}]}, f)
    return path


class OutdatedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rt = os.path.join(self.tmp, "forge_runtime")
        self.man = os.path.join(self.tmp, "forge_runtime.manifest.json")

    def test_commits_are_read_from_version_txt_and_the_manifest(self):
        runtime_with(self.rt, OLD.upper())
        manifest_with(self.man, NEW)
        self.assertEqual(fc.runtime_commit(self.rt), OLD)
        self.assertEqual(fc.bundle_commit(self.man), NEW)

    def test_nothing_known_means_not_outdated(self):
        runtime_with(self.rt)                                            # no VERSION.txt (a hand-made runtime)
        manifest_with(self.man, NEW)
        self.assertIsNone(fc.runtime_outdated(self.rt, self.man))
        runtime_with(self.rt, OLD)
        self.assertIsNone(fc.runtime_outdated(self.rt, os.path.join(self.tmp, "missing.json")))   # an installed copy
        with open(self.man, "w") as f:
            f.write("{not json")
        self.assertIsNone(fc.runtime_outdated(self.rt, self.man))

    def test_the_same_build_is_not_outdated_even_with_a_short_commit(self):
        runtime_with(self.rt, first_line="Forge 2.0.16-SNAPSHOT | commit fb4d809")
        manifest_with(self.man, NEW)
        self.assertIsNone(fc.runtime_outdated(self.rt, self.man))

    def test_an_older_runtime_is_outdated(self):
        runtime_with(self.rt, OLD)
        manifest_with(self.man, NEW)
        self.assertEqual(fc.runtime_outdated(self.rt, self.man), ("3a74143", "fb4d809"))

    def test_the_game_refuses_an_outdated_runtime_in_karls_copy(self):
        runtime_with(self.rt, OLD)
        manifest_with(self.man, NEW)
        with mock.patch.object(fc, "find_java", return_value="java"), \
                mock.patch.object(fc, "bundled_runtime_expected", return_value=False), \
                mock.patch.object(fc, "BUNDLE_MANIFEST", self.man):
            problem = fc.runtime_problem(self.rt)
        self.assertIn("3a74143", problem)
        self.assertIn("fb4d809", problem)
        self.assertIn("python setup_forge.py", problem)

    def test_an_installed_copy_is_never_judged_by_a_bundle(self):
        runtime_with(self.rt, OLD)
        manifest_with(self.man, NEW)
        with mock.patch.object(fc, "find_java", return_value="java"), \
                mock.patch.object(fc, "bundled_runtime_expected", return_value=True), \
                mock.patch.object(fc, "BUNDLE_MANIFEST", self.man):
            self.assertIsNone(fc.runtime_problem(self.rt))

    def run_setup(self, installed_commit):
        runtime_with(self.rt, installed_commit)
        manifest_with(self.man, NEW)
        calls = []
        out = io.StringIO()
        with mock.patch.object(setup_forge, "RUNTIME", self.rt), mock.patch.object(setup_forge, "MANIFEST", self.man), \
                mock.patch.object(setup_forge.fc, "find_java", return_value="java"), \
                mock.patch.object(setup_forge.fc, "runtime_problem", return_value=None), \
                mock.patch.object(setup_forge.fc, "sync_bridge", return_value=False), \
                mock.patch.object(setup_forge, "install", lambda m, force=False: calls.append(("install", m["forge_commit"]))), \
                mock.patch.object(setup_forge, "install_bridge", lambda rebuild=False: calls.append(("bridge", rebuild))), \
                mock.patch.object(setup_forge, "write_notes", lambda m: calls.append(("notes", m["forge_commit"]))), \
                redirect_stdout(out):
            code = setup_forge.main([])
        return code, calls, out.getvalue()

    def test_setup_reinstalls_an_outdated_runtime_by_itself(self):
        code, calls, text = self.run_setup(OLD)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("install", NEW), ("bridge", False), ("notes", NEW)])
        self.assertIn("forge_runtime/ holds Forge 3a74143; forge_bundle/ has Forge fb4d809", text)

    def test_setup_leaves_a_current_runtime_alone(self):
        code, calls, text = self.run_setup(NEW)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("already installed", text)


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveRuntimeTests(unittest.TestCase):
    def test_the_installed_runtime_is_the_bundled_build(self):
        self.assertEqual(fc.runtime_commit(), fc.bundle_commit())

    def test_forge_knows_reality_fractures_new_cards(self):
        unknown = [n for n in FRA_NEW_IN_FB4D809 if not fc.forge_knows(n)]
        self.assertEqual(unknown, [])


if __name__ == "__main__":
    unittest.main()
