# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 25 (licences on the chain): Forge's own modules inside forge.jar count as the "forge" component.

java_components lists the 74 third-party Maven artifacts in forge.jar; Forge's own five modules (forge:forge-core, -game, -ai,
-gui, -gui-desktop) are the "forge" component (GPL-3.0-or-later). The release check never knew that, so with a real forge.jar
present (Karl's PC, an installer build) `build_notices --release` and check_dist named all five. Sandboxes without forge.jar
skipped the check, which is why it went unnoticed.
"""
import os
import sys
import tempfile
import unittest
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))

import build_notices as bn
import check_dist as cd


def jar(path, artifacts):
    with zipfile.ZipFile(path, "w") as z:
        for group, artifact, version in artifacts:
            z.writestr(f"META-INF/maven/{group}/{artifact}/pom.properties",
                       f"groupId={group}\nartifactId={artifact}\nversion={version}\n")


MANIFEST = {"components": [{"id": "forge", "version": "2.0.15-SNAPSHOT (commit 3a74143aa2a4d1475e929e140865bd0c574adf1c)"}],
            "java_components": {"com.google.code.gson": {"artifacts": ["gson:2.13.2"]}}}


class ForgeModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.jar = os.path.join(self.tmp, "forge.jar")

    def problems(self, artifacts, manifest=MANIFEST):
        jar(self.jar, artifacts)
        old = bn.FORGE_JAR
        bn.FORGE_JAR = self.jar
        try:
            return bn.check_jar_coverage(manifest), cd.jar_coverage_problems(self.jar, manifest)
        finally:
            bn.FORGE_JAR = old

    def test_forges_own_modules_are_the_forge_component(self):
        mods = [("forge", m, "2.0.15-SNAPSHOT") for m in ("forge-core", "forge-game", "forge-ai", "forge-gui", "forge-gui-desktop")]
        self.assertEqual(self.problems(mods + [("com.google.code.gson", "gson", "2.13.2")]), ([], []))

    def test_another_forge_build_is_still_reported(self):
        a, b = self.problems([("forge", "forge-core", "2.0.16-SNAPSHOT")])
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertIn("forge:forge-core:2.0.16-SNAPSHOT", a[0])

    def test_a_third_party_library_is_still_reported(self):
        a, b = self.problems([("org.example", "thing", "1.0")])
        self.assertEqual((len(a), len(b)), (1, 1))

    def test_without_a_forge_component_nothing_is_excused(self):
        a, _ = self.problems([("forge", "forge-core", "2.0.15-SNAPSHOT")], manifest={"components": [], "java_components": {}})
        self.assertEqual(len(a), 1)

    def test_the_real_manifest_covers_the_real_jar_when_it_is_here(self):
        if not os.path.isfile(bn.FORGE_JAR):
            self.skipTest("no forge_runtime/forge.jar in this copy")
        self.assertEqual(bn.check_jar_coverage(bn.load_manifest()), [])


if __name__ == "__main__":
    unittest.main()
