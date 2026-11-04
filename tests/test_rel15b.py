# SPDX-License-Identifier: GPL-3.0-or-later
"""Release candidate 0.28.53.15, second merge (task rc15b, 4 Nov 2026): where PROG3 (already in the release) and SND1-SND1C met in
tools/build_sounds.py and the files it writes. The sound tool needs the recordings to run, so no other test runs these parts; a
planted error in any of them went unnoticed (rc15b's plants) until this file.

  1  CueSettingsTests  every one-shot cue the tool writes from CUE_SETTINGS (the Adventure's encounter sting, PROG3's level-up
                       fanfare, SND1's howl and rooster) has its settings there, and sounds/cues.json says the same - so a rebuild
                       can't drop or change one of them.
  2  CreditsTests      write_credits() from tools/sound_sources.json writes exactly the shipped sounds/CREDITS.txt: SND1's sources
                       with their licence words and PROG3's "Synthesized for Manticore" line; THIRD_PARTY_NOTICES.txt (which
                       copies it) is up to date (tools/build_notices.py --check).
  3  BridgeArgsTests   forge_client.ForgeSession's signature, merged by hand (PROG3's effects=, PERF1's timing= and skip_scan=):
                       all three reach the bridge's command line. PERF1's own tests start the bridge directly, so nothing else
                       noticed a session that dropped --skip-scan. And Main.java, merged by hand (PROG3's startSpec next to PERF1's
                       sampleFile): each option is read into its own variable and used.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))
CUES = os.path.join(BASE, "sounds", "cues.json")
CREDITS = os.path.join(BASE, "sounds", "CREDITS.txt")

try:
    import build_sounds
    HAVE = True
except Exception:                                    # numpy / scipy missing: the tool can't be imported
    HAVE = False


def shipped_cues():
    with open(CUES, encoding="utf-8") as f:
        d = json.load(f)
    return d["cues"] if "cues" in d else d


@unittest.skipUnless(HAVE, "tools/build_sounds.py can't be imported here (numpy/scipy)")
class CueSettingsTests(unittest.TestCase):
    def tool_written(self):
        """The one-shot cues the tool writes from CUE_SETTINGS by name (not from a recipe list): the world's sounds, the encounter
        sting, the synthesized stingers."""
        out = [c for c in build_sounds.WORLD_PICK if c not in build_sounds.WORLD_PENDING]
        out.append(build_sounds.RPG_STING[0])
        out += list(build_sounds.SYNTH_STINGS)
        return out

    def test_the_merged_cues_are_all_written_by_the_tool(self):
        self.assertTrue({"rpg.howl", "rpg.rooster", "rpg.level_up", "rpg.encounter"} <= set(self.tool_written()))

    def test_each_has_its_own_settings(self):
        for cid in self.tool_written():
            with self.subTest(cue=cid):
                self.assertIn(cid, build_sounds.CUE_SETTINGS, "a rebuild would look it up under its parent, or fail")

    def test_cues_json_matches_the_settings(self):
        cues = shipped_cues()
        for cid in self.tool_written():
            with self.subTest(cue=cid):
                s = build_sounds.settings_for(cid)
                c = cues[cid]
                self.assertEqual((c["bus"], c["gain_db"], c["cooldown_ms"], c["max_voices"]), tuple(s[:4]))
                self.assertEqual(c.get("duck"), s[4] if len(s) > 4 else None)
                self.assertEqual(c["files"], [f"sfx/{cid}.1.ogg"])
                self.assertTrue(os.path.isfile(os.path.join(BASE, "sounds", "sfx", f"{cid}.1.ogg")))


@unittest.skipUnless(HAVE, "tools/build_sounds.py can't be imported here (numpy/scipy)")
class CreditsTests(unittest.TestCase):
    def test_write_credits_writes_the_shipped_file(self):
        with open(build_sounds.SOURCES, encoding="utf-8") as f:
            sources = json.load(f)["sources"]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(build_sounds, "SOUNDS", tmp):
            build_sounds.write_credits(sources, build_sounds.referenced_sources(sources))
            with open(os.path.join(tmp, "CREDITS.txt"), encoding="utf-8") as f:
                made = f.read()
        with open(CREDITS, encoding="utf-8") as f:
            shipped = f.read()
        self.assertEqual(made.splitlines(), shipped.splitlines())

    def test_the_shipped_file_has_both_rounds(self):
        with open(CREDITS, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Synthesized for Manticore by tools/build_sounds.py, no recording used (CC0 1.0): rpg.level_up", text)
        for key in ("snd1-wolf-monster", "snd1-pd-rooster-small"):
            self.assertIn(key + ":", text)


    def test_third_party_notices_are_up_to_date(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "build_notices.py"), "--check"], cwd=BASE,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class BridgeArgsTests(unittest.TestCase):
    def args(self, **kw):
        import forge_client as fc
        s = fc.ForgeSession("player.dck", ["opp.dck"], fmt="commander", seed=1, **kw)
        return s._bridge_args()

    def test_skip_scan_timing_and_effects_reach_the_bridge(self):
        a = self.args(skip_scan="compare", timing=True, effects={"life": 25})
        i = a.index("--skip-scan")
        self.assertEqual(a[i + 1], "compare")
        self.assertIn("--timing", a)
        self.assertIn("--start-effects", a)

    def test_none_of_them_by_default(self):
        a = self.args()
        for flag in ("--skip-scan", "--timing", "--start-effects"):
            self.assertNotIn(flag, a)



class MainJavaTests(unittest.TestCase):
    def test_start_effects_and_sample_are_each_read_and_used(self):
        with open(os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "Main.java"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('case "--start-effects": startSpec = args[++i];', src)
        self.assertIn('case "--sample": sampleFile = args[++i];', src)
        self.assertIn("StartEffects.parse(startSpec)", src)
        self.assertIn("Timing.startSampler(sampleFile,", src)


if __name__ == "__main__":
    unittest.main()
