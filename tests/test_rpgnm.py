# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG-NM: Karl's night music in the Adventure (25 Oct 2026 00:11-00:13 UTC, page "Adventure Night Music";
claude/DECISIONS_2026-10-24.md, section "Night music"): Lantern Hollow at night plays "Lonelyhood" (yd), the Ashfen moors at night
"When the Shadows Gather" (Tsorthan Grove). Nothing here is judged by ear.

  AreaTests      the map property night_music; the shipped maps name the two night cues, the Warrens none
  NightTests     the cue each map asks for by day, at dusk, at night and at dawn, and on the minute either side of 18:00 and 05:00 (RPG18b: from dusk);
                 walking across 18:00 changes the music; a map that isn't outdoor keeps its day music; the boss keeps his
  FadeTests      the director fades the day track out and starts the night track, looping, when the mode changes
  CueTests       both night cues exist, stream, have their files, and are built from the picked sources (sha256 recorded)
  CreditTests    sounds/CREDITS.txt names exactly the sources tools/build_sounds.py uses, and both picks
"""
import json
import os
import re
import sys
import unittest
from unittest import mock

import pygame

import audio
import rpg_screens as rs
from rpg import clock, explore
from tests import test_au1 as au1
from tests import test_rpg14 as r14
from tests.test_forge_table import frame
from tests.test_rpg5 import Temp, layer_xml, room, write_map
from tests.test_rpg15 import at, prop

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOUNDS = os.path.join(BASE, "sounds")
TOOLS = os.path.join(BASE, "tools")
TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
TOWN_DAY, TOWN_NIGHT = "music.rpg_town", "music.rpg_town_night"
OUT_DAY, OUT_NIGHT = "music.rpg_outskirts", "music.rpg_outskirts_night"
PICKS = {TOWN_NIGHT: ("mus4-lonelyhood", "e342f0659e4ac33da72abc3a2fb72303523fe3d333228e328b7a76a5072c9067"),
         OUT_NIGHT: ("mus4-when-the-shadows-gather", "f275350e329c28dcc76e9d3a01688d6dc6c0b2f49341da9a3ff701deb14a3430")}


def manifest():
    with open(os.path.join(SOUNDS, "cues.json"), encoding="utf-8") as f:
        return json.load(f)["cues"]


def sources():
    with open(os.path.join(TOOLS, "sound_sources.json"), encoding="utf-8") as f:
        return json.load(f)["sources"]


def music_of(g):
    return g.soundscape()[1]


class AreaTests(Temp):
    def area(self, props=""):
        explore.forget_areas()
        write_map(self.tmp, "m", 8, 6, [layer_xml("g", 8, 6, room())], "",
                  map_props=f"<properties>{props}</properties>" if props else "")
        return explore.load_area("m", self.tmp)

    def test_the_shipped_maps_name_their_night_music(self):
        want = {TOWN: (TOWN_DAY, TOWN_NIGHT), OUT: (OUT_DAY, OUT_NIGHT),
                "warrens_1": ("music.rpg_warrens", None), "warrens_2": ("music.rpg_warrens", None)}
        for name, pair in want.items():
            with self.subTest(map=name):
                a = explore.load_area(name)
                self.assertEqual((a.music, a.night_music), pair)

    def test_the_property_is_read_and_its_absence_is_none(self):
        self.assertIsNone(self.area(prop("music", "string", TOWN_DAY)).night_music)
        a = self.area(prop("music", "string", TOWN_DAY) + prop("night_music", "string", TOWN_NIGHT))
        self.assertEqual((a.music, a.night_music), (TOWN_DAY, TOWN_NIGHT))


class NightTests(r14.WindowBase):
    def at_time(self, g, f, minutes):
        f.game.save.world["clock"] = minutes
        return music_of(g)

    def test_each_map_by_day_dusk_night_and_dawn(self):
        """From dusk (18:00-05:00, Round RPG18b - Karl 25 Oct; RPG-NM had 20:00): dawn (05:00-07:00) keeps the day track."""
        g, _l = self.setup_game()
        for name, spawn, day, night in ((TOWN, "start", TOWN_DAY, TOWN_NIGHT), (OUT, "from_town", OUT_DAY, OUT_NIGHT)):
            f = self.on_map(g, name, spawn)
            for when, cue in ((at(1, 12), day), (at(1, 17, 59), day), (at(1, 18), night), (at(1, 18, 30), night),
                              (at(1, 19, 59), night), (at(1, 20), night),
                              (at(1, 23), night), (at(2, 0), night), (at(2, 3), night), (at(2, 4, 59), night),
                              (at(2, 5), day), (at(2, 6), day), (at(2, 7), day), (at(5, 22, 10), night)):
                with self.subTest(map=name, time=clock.stamp(when)):
                    self.assertEqual(self.at_time(g, f, when), "loop:" + cue)

    def test_the_town_shop_at_night_plays_the_towns_night_music(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 22)
        for stage in ("town", "deck", "pack", "collection"):
            with self.subTest(stage=stage):
                f.stage = stage
                self.assertEqual(music_of(g), "loop:" + TOWN_NIGHT)

    def test_walking_across_six_pm_changes_the_music(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        world = f.game.save.world
        world["clock"] = at(1, 17, 59) + 0.9                             # six seconds of walking before 18:00 (RPG18b)
        seen = [music_of(g)]
        step = None
        for s in ((1, 0), (-1, 0), (0, 1), (0, -1)):                       # a direction the walker can go
            x, y = f.explorer.walker.x, f.explorer.walker.y
            f.last = None
            f.walk_tick(g, 100.0, pressed=s)
            f.walk_tick(g, 100.05, pressed=s)
            if (f.explorer.walker.x, f.explorer.walker.y) != (x, y):
                step = s
                break
        self.assertIsNotNone(step)
        t = 100.05
        while clock.now(world) < at(1, 18) + 0.5:
            t += 0.05
            f.walk_tick(g, t, pressed=step)
            seen.append(music_of(g))
            step = step if (t * 20) % 40 else (-step[0], -step[1])          # back and forth, so the walker never hits a wall for long
            self.assertLess(t, 200.0, "the clock didn't reach 18:00")
        self.assertEqual(seen[0], "loop:" + OUT_DAY)
        self.assertEqual(seen[-1], "loop:" + OUT_NIGHT)
        changes = [i for i in range(1, len(seen)) if seen[i] != seen[i - 1]]
        self.assertEqual(len(changes), 1)                                  # one change, at 18:00, no flicker

    def test_a_map_that_isnt_outdoors_keeps_its_day_music_at_night(self):
        """Only an outdoor map follows the clock (RPG15): a dungeon with night_music would not change."""
        g, _l = self.setup_game()
        f = self.on_map(g, TOWN, "start")
        f.game.save.world["clock"] = at(1, 23)
        with mock.patch.object(f.explorer.area, "outdoor", False):
            self.assertEqual(music_of(g), "loop:" + TOWN_DAY)

    def test_the_warrens_and_the_boss_are_the_same_at_night(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "warrens_1")
        f.game.save.world["clock"] = at(1, 23)
        self.assertEqual(music_of(g), "loop:music.rpg_warrens")
        f = self.on_map(g, "warrens_2")
        f.game.save.world["clock"] = at(1, 23)
        f.fight, f.sub = self.boss(f), "fight"
        self.assertEqual(music_of(g), "loop:" + rs.BOSS_MUSIC)

    def test_the_night_phases_are_the_clocks_dusk_and_night(self):
        self.assertEqual(rs.NIGHT_MUSIC_PHASES, ("dusk", "night"))         # Round RPG18b (Karl 25 Oct): from dusk
        for ph in rs.NIGHT_MUSIC_PHASES:
            self.assertIn(ph, clock.PHASE_NAMES)


class FadeTests(unittest.TestCase):
    def test_the_day_track_fades_out_and_the_night_track_loops_in(self):
        """pygame plays one music stream: the day track fades out (MUSIC_FADE), then the night track starts with its own fade-in
        (audio._start_track's fade_ms) - a fade out and in, not an overlap."""
        d = au1.director()
        busy = [False]
        with mock.patch.object(d, "_start_track", return_value=True) as start, \
                mock.patch.object(pygame.mixer.music, "get_busy", side_effect=lambda: busy[0]), \
                mock.patch.object(pygame.mixer.music, "set_volume"), \
                mock.patch.object(pygame.mixer.music, "fadeout") as fadeout:
            d.set_music("loop:" + TOWN_DAY, 0.0)
            d.update(5.0)
            start.assert_called_once_with(TOWN_DAY, 5.0, loops=-1)
            busy[0] = True                                                # the day track plays
            start.reset_mock()
            d.set_music("loop:" + TOWN_NIGHT, 60.0)                      # 20:00
            fadeout.assert_called_once_with(int(audio.MUSIC_FADE * 1000))  # the day track fades out...
            d.update(61.5)
            start.assert_not_called()                                     # ... and nothing starts while it is still fading
            busy[0] = False                                               # faded out
            d.update(62.1)
            start.assert_called_once_with(TOWN_NIGHT, 62.1, loops=-1)     # the night track, looping
            busy[0] = True
            d.set_music("loop:" + TOWN_NIGHT, 63.0)                       # asked again every tick: nothing restarts
            d.update(70.0)
            self.assertEqual(start.call_count, 1)
            self.assertEqual(fadeout.call_count, 1)


class MoorsTrimTests(unittest.TestCase):
    """Karl's mark on the Night Music Check page (25 Oct 2026 21:18 UTC): the moors' night track 2 dB quieter."""

    def test_the_moors_night_cue_is_2_db_quieter_and_the_build_tool_keeps_it(self):
        cues = manifest()
        self.assertEqual(cues["music.rpg_outskirts_night"]["gain_db"], -2.0)
        self.assertEqual(cues["music.rpg_town_night"]["gain_db"], 0)            # Lonelyhood: no change asked
        self.assertEqual(cues["music.rpg_outskirts"]["gain_db"], 0)             # the day track it is compared with
        with open(os.path.join(TOOLS, "build_sounds.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertRegex(text, r'RPG_MUSIC_GAIN = \{"music\.rpg_outskirts_night": -2\.0\}')
        self.assertIn('"gain_db": RPG_MUSIC_GAIN.get(cid, 0)', text)

    def test_the_game_plays_the_moors_night_track_at_the_lower_volume(self):
        d = au1.director()
        volumes = {}
        def start(cid):
            with mock.patch.object(pygame.mixer.music, "load"), \
                    mock.patch.object(pygame.mixer.music, "play"), \
                    mock.patch.object(pygame.mixer.music, "set_volume") as sv:
                self.assertTrue(d._start_track(cid, 0.0, loops=-1))
                volumes[cid] = sv.call_args[0][0]
        start("music.rpg_outskirts")
        start("music.rpg_outskirts_night")
        self.assertGreater(volumes["music.rpg_outskirts"], 0)
        self.assertAlmostEqual(20 * __import__("math").log10(volumes["music.rpg_outskirts_night"] / volumes["music.rpg_outskirts"]),
                               -2.0, places=3)


class CueTests(unittest.TestCase):
    def test_both_night_cues_exist_stream_and_have_their_files(self):
        cues = manifest()
        for cid in PICKS:
            with self.subTest(cue=cid):
                self.assertTrue(cues[cid].get("stream"))
                self.assertEqual(cues[cid].get("bus"), "music")
                for fn in cues[cid]["files"]:
                    p = os.path.join(SOUNDS, fn)
                    self.assertTrue(os.path.isfile(p), fn)
                    self.assertGreater(os.path.getsize(p), 500_000, fn)   # a whole track (about 1-1.6 MB at Vorbis q3)

    def test_the_picked_sources_with_page_author_cc0_and_sha256(self):
        s = sources()
        for cid, (key, sha) in PICKS.items():
            with self.subTest(source=key):
                rec = s[key]
                self.assertEqual(rec["licence"], "CC0-1.0")
                self.assertIn("License(s): CC0", rec["licence_quote"])
                self.assertTrue(rec["page"].startswith("https://opengameart.org/content/"))
                self.assertTrue(rec["author"])
                self.assertEqual(rec["sha256"], sha)
        with open(os.path.join(TOOLS, "build_sounds.py"), encoding="utf-8") as f:
            text = f.read()
        for cid, (key, _sha) in PICKS.items():
            self.assertRegex(text, r'"%s":\s*\("%s"' % (re.escape(cid), re.escape(key)))


class BuildToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("the build tool needs numpy and scipy")
        sys.path.insert(0, TOOLS)
        import build_sounds
        cls.b = build_sounds

    def test_a_download_with_the_wrong_checksum_is_refused(self):
        self.b.check_sha256("k", "AB" * 32, "ab" * 32)                     # case doesn't matter
        with self.assertRaises(SystemExit):
            self.b.check_sha256("k", "ab" * 32, "cd" * 32)

    def test_a_cached_source_with_the_wrong_checksum_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "k"))
            with open(os.path.join(d, "k", ".done"), "w") as f:
                f.write("ab" * 32)
            self.assertEqual(self.b.fetch("k", {"url": "https://x/y.ogg", "kind": "file", "sha256": "ab" * 32}, d),
                             os.path.join(d, "k"))
            with self.assertRaises(SystemExit):
                self.b.fetch("k", {"url": "https://x/y.ogg", "kind": "file", "sha256": "cd" * 32}, d)

    def test_only_lonelyhood_has_its_seam_blended(self):
        self.assertEqual(self.b.RPG_LOOP_XFADE, {TOWN_NIGHT: 3.0})
        for cid in PICKS:
            self.assertTrue(self.b.RPG_MUSIC[cid][4], cid)                  # both are loops, written whole but for the blend


class CreditTests(unittest.TestCase):
    def credited(self):
        with open(os.path.join(SOUNDS, "CREDITS.txt"), encoding="utf-8") as f:
            return {m.group(1) for m in re.finditer(r"^([A-Za-z0-9_.-]+):", f.read(), re.M)}

    def used(self):
        """build_sounds.referenced_sources without importing it (no numpy needed): every source key the tool's text names."""
        with open(os.path.join(TOOLS, "build_sounds.py"), encoding="utf-8") as f:
            text = f.read()
        return {k for k in sources() if re.search(r"[\"']" + re.escape(k) + r"[:\"']", text)}

    def test_the_credits_name_exactly_the_sources_used(self):
        self.assertEqual(self.credited(), self.used())

    def test_both_picks_are_credited(self):
        credited = self.credited()
        for key, _sha in PICKS.values():
            self.assertIn(key, credited)


if __name__ == "__main__":
    unittest.main()
