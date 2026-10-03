# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AU1: the dark-fantasy sound overhaul - real CC0 recordings, ambience per table, music, and the sounds of the title and the
end of a game (audio.py, sounds/, tools/build_sounds.py, tools/sound_sources.json)."""
import ast
import glob
import json
import os
import re
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame

import audio
import boot_screens as fboot
import flow_screens as flow
import forge_settings as fset
import forge_table as ft
from tests.test_ad2b import game_over
from tests.test_forge_table import frame, make_gui
from tests.test_round24 import beat

SOUNDS = os.path.join(ROOT, "sounds")
MANIFEST = os.path.join(SOUNDS, "cues.json")


def manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)["cues"]


def director():
    return audio.AudioDirector(MANIFEST, SOUNDS, rnd=__import__("random").Random(3))


# ---- the files ---------------------------------------------------------------------------------------------------------------
class AssetTests(unittest.TestCase):
    def test_every_cue_has_its_files_and_every_file_has_a_cue(self):
        cues = manifest()
        named = set()
        for cid, spec in cues.items():
            self.assertTrue(spec.get("files"), cid)
            for name in spec["files"]:
                self.assertTrue(os.path.isfile(os.path.join(SOUNDS, name)), f"{cid}: {name}")
                named.add(name.replace("\\", "/"))
        on_disk = {os.path.relpath(p, SOUNDS).replace("\\", "/") for p in glob.glob(os.path.join(SOUNDS, "*", "*.ogg"))}
        self.assertEqual(on_disk - named, set(), "sound files no cue plays")

    def test_every_cue_the_code_plays_is_in_the_manifest(self):
        played = set()
        for path in glob.glob(os.path.join(ROOT, "*.py")):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            played |= set(re.findall(r'play_cue\(\s*"([a-z_.]+)"', src))
            played |= set(re.findall(r'"(music\.[a-z0-9_]+|amb\.[a-z_]+)"', src))
        played.discard("music.game")                                      # the prefix the playlist is built from, not a cue
        cues = manifest()
        for cid in played:
            self.assertTrue(cid in cues, f"the code plays {cid!r}, which sounds/cues.json doesn't have")
        for name in ("graveyard", "cathedral", "citadel", "ruins", "plain"):             # every table picture has its ambience
            self.assertIn(f"amb.{name}", manifest())
        for bg in ft.BG_ORDER:
            self.assertIn(f"amb.{bg}", manifest())

    def test_every_source_is_cc0_and_credited(self):
        with open(os.path.join(ROOT, "tools", "sound_sources.json"), encoding="utf-8") as f:
            sources = json.load(f)["sources"]
        with open(os.path.join(SOUNDS, "CREDITS.txt"), encoding="utf-8") as f:
            credits = f.read()
        self.assertIn("CC0", credits)
        credited = re.findall(r"^([a-z0-9-]+):", credits, re.M)
        self.assertTrue(credited)
        for key in credited:
            self.assertIn(key, sources)
        for key, s in sources.items():
            self.assertEqual(s["licence"], "CC0-1.0", key)
            self.assertTrue(s["page"].startswith("https://"), key)
            self.assertTrue(s["author"], key)

    def test_the_recipes_name_only_listed_sources(self):
        with open(os.path.join(ROOT, "tools", "build_sounds.py"), encoding="utf-8") as f:
            src = f.read()
        with open(os.path.join(ROOT, "tools", "sound_sources.json"), encoding="utf-8") as f:
            sources = json.load(f)["sources"]
        refs = set(re.findall(r'L\(f?"([a-z0-9-]+)(?::[^"]*)?"\)', src)) | {m[0] for m in re.findall(r'"(mus-[a-z-]+)", (\d+)', src)}
        self.assertTrue(refs)
        for key in refs:
            self.assertIn(key, sources, key)

    def test_the_sounds_stay_small(self):
        total = sum(os.path.getsize(p) for p in glob.glob(os.path.join(SOUNDS, "**", "*.ogg"), recursive=True))
        self.assertLess(total, 32 * 1024 * 1024, "sounds/ should stay under 32 MB (the installer carries it)")
        for p in glob.glob(os.path.join(SOUNDS, "sfx", "*.ogg")):
            self.assertLess(os.path.getsize(p), 400 * 1024, p)

    def test_the_licence_manifest_names_the_sounds(self):
        with open(os.path.join(ROOT, "licenses", "NOTICES.json"), encoding="utf-8") as f:
            notices = json.load(f)
        entry = next(c for c in notices["components"] if c.get("id") == "sounds")
        self.assertEqual(entry["license"], "CC0-1.0")
        self.assertEqual(entry["status"], "verified")


# ---- the director ---------------------------------------------------------------------------------------------------------------
class DirectorTests(unittest.TestCase):
    def test_one_shots_play_the_recordings_not_the_placeholders(self):
        d = director()
        self.assertTrue(d.ready, d.error)
        for cid, spec in manifest().items():
            if spec.get("stream"):
                self.assertEqual(d.cues[cid].variants, [], cid)                # never decoded up front
                self.assertTrue(d.cues[cid].paths, cid)
            else:
                self.assertEqual(len(d.cues[cid].variants), len(spec["files"]), cid)

    def test_a_missing_file_still_falls_back_to_the_synth(self):
        cues = {"version": 2, "cues": {"tap": {"bus": "sfx", "files": ["sfx/nope.ogg"], "synth": {"type": "tick"}}}}
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cues.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cues, f)
            d = audio.AudioDirector(path, tmp)
        self.assertEqual(len(d.cues["tap"].variants), 1)

    def wait_loaded(self, d, cid):
        for _ in range(200):
            with d._amb_lock:
                if cid in d.amb_cache:
                    return
            pygame.time.wait(10)
        self.fail(f"{cid} never loaded")

    def test_ambience_loads_off_the_main_thread_then_fades_in(self):
        d = director()
        d.set_ambience("amb.graveyard")
        self.wait_loaded(d, "amb.graveyard")
        d.update(100.0)
        cid, ch, t0 = d.amb_now
        self.assertEqual(cid, "amb.graveyard")
        self.assertIn(ch, d.channels["ambience"])
        self.assertTrue(ch.get_busy())
        d.update(100.0 + audio.AMBIENCE_FADE / 2)
        half = ch.get_volume()
        d.update(100.0 + audio.AMBIENCE_FADE * 2)
        self.assertGreater(ch.get_volume(), half)

    def test_a_new_table_cross_fades_and_the_old_loop_stops(self):
        d = director()
        d.set_ambience("amb.graveyard")
        self.wait_loaded(d, "amb.graveyard")
        d.update(0.0)
        old = d.amb_now[1]
        d.set_ambience("amb.ruins")
        self.wait_loaded(d, "amb.ruins")
        d.update(10.0)
        self.assertEqual(d.amb_now[0], "amb.ruins")
        self.assertIsNot(d.amb_now[1], old)
        self.assertEqual([c for c, _t, _id in d.amb_out], [old])
        d.update(10.0 + audio.AMBIENCE_FADE + 0.1)
        self.assertEqual(d.amb_out, [])
        self.assertLessEqual(len(d.amb_cache), audio.AMBIENCE_KEEP)

    def test_the_switches_and_mute_silence_the_long_sounds(self):
        d = director()
        d.set_ambience("amb.cathedral")
        self.wait_loaded(d, "amb.cathedral")
        d.update(0.0)
        d.update(50.0)
        ch = d.amb_now[1]
        self.assertGreater(ch.get_volume(), 0)
        d.streams_on["ambience"] = False
        d.update(51.0)
        self.assertEqual(ch.get_volume(), 0)
        d.streams_on["ambience"] = True
        d.muted = True
        d.update(52.0)
        self.assertEqual(ch.get_volume(), 0)
        self.assertEqual(d.stream_gain("music", 52.0), 0.0)

    def test_title_music_starts_after_a_moment_and_loops(self):
        d = director()
        with mock.patch.object(d, "_start_track", return_value=True) as start, \
                mock.patch.object(pygame.mixer.music, "get_busy", return_value=False):
            d.set_music("title", 0.0)
            d.update(0.1)
            start.assert_not_called()
            d.update(5.0)
            start.assert_called_once_with("music.title", 5.0, loops=-1)

    def test_game_music_rests_between_tracks_and_plays_each_before_repeating(self):
        d = director()
        started = []
        busy = {"v": False}

        def start(cid, now, loops=0):
            started.append(cid)
            busy["v"] = True
            return True
        with mock.patch.object(d, "_start_track", side_effect=start), \
                mock.patch.object(pygame.mixer.music, "get_busy", side_effect=lambda: busy["v"]):
            d.set_music("game", 0.0)
            d.update(1.0)
            self.assertEqual(started, [])                         # the table settles first
            t = 20.0
            for _ in range(len(d._game_tracks())):
                d.update(t)
                self.assertEqual(len(started), _ + 1)
                busy["v"] = False                                # the track ends
                t += 1.0
                d.update(t)                                     # ... and a gap is chosen
                self.assertIsNotNone(d.music_next_at)
                self.assertGreaterEqual(d.music_next_at - t, audio.MUSIC_GAP[0])
                t += audio.MUSIC_GAP[1] + 1
        self.assertEqual(sorted(started), d._game_tracks())        # every track once before any repeats

    def test_the_end_of_a_game_plays_its_sting_after_the_bell(self):
        d = director()
        with mock.patch.object(d, "_start_track", return_value=True) as start, \
                mock.patch.object(pygame.mixer.music, "get_busy", return_value=False):
            d.set_music("game", 0.0)
            d.music_sting_after("music.victory", 10.0, 2.5)
            d.update(11.0)
            start.assert_not_called()
            d.update(12.6)
            start.assert_called_once_with("music.victory", 12.6)
            d.update(200.0)                                      # a sting plays once: nothing follows it
            self.assertEqual(start.call_count, 1)

    def test_stop_bus_spares_the_other_buses(self):
        d = director()
        self.assertTrue(d.play("card.play", 0.0))
        self.assertTrue(d.play("game.win", 0.0))
        d.stop_bus("sfx")
        self.assertEqual([v[1] for v in d.voices], ["game.win"])


# ---- the table ------------------------------------------------------------------------------------------------------------------
class TableTests(unittest.TestCase):
    def test_what_each_screen_sounds_like(self):
        gui = make_gui("main1_start")
        gui.current_bg = "cathedral"
        self.assertEqual(gui.soundscape(), ("amb.cathedral", "game"))
        gui.current_bg = "plain"
        self.assertEqual(gui.soundscape()[0], "amb.plain")
        gui.vs = flow.VsShow([("You", ["Kinnan, Bonder Prodigy"], ""), ("AI", ["Tymna the Weaver"], "")])
        with mock.patch.object(gui.vs, "holding", return_value=True):
            self.assertEqual(gui.soundscape(), ("amb.plain", None))         # the VS screen: no music under the bell
        over = make_gui(game_over())
        self.assertEqual(over.soundscape()[1], "keep")                       # the VICTORY / DEFEAT sting plays out
        gui.boot = fboot.BootFlow(True)
        self.assertEqual(gui.soundscape(), (None, None))
        gui.boot.to_title()
        self.assertEqual(gui.soundscape(), ("amb.title", "title"))

    def test_apply_audio_sets_the_soundscape_every_tick(self):
        gui = make_gui("main1_start")
        d = mock.Mock(ready=True, cues={"amb.graveyard": 1, "amb.plain": 1})
        gui.audio = d
        gui.current_bg = "graveyard"
        gui.apply_audio()
        d.set_ambience.assert_called_with("amb.graveyard")
        self.assertEqual(d.set_music.call_args[0][0], "game")
        gui.sound["on"] = False
        gui.apply_audio()
        self.assertTrue(d.muted)

    def test_the_end_of_a_game_keeps_the_bell_and_starts_the_sting(self):
        gui = make_gui("main1_start")
        gui.audio = mock.Mock(ready=True)
        played = []
        gui.play_cue = lambda cid, *a, **k: played.append(cid) or True
        gui._play_beat(beat("outcome", winner=99), 99, "Karl")
        gui.audio.stop_bus.assert_called_once_with("sfx")
        gui.audio.stop_all.assert_not_called()
        self.assertEqual(played, ["game.win"])
        self.assertEqual(gui.audio.music_sting_after.call_args[0][0], "music.victory")
        gui._play_beat(beat("outcome", winner=5), 99, "Karl")
        self.assertEqual(gui.audio.music_sting_after.call_args[0][0], "music.defeat")

    def test_the_music_and_ambience_switches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            gui = make_gui("main1_start", settings=path)
            gui.audio = director()
            gui.toggle_stream("music_on")
            self.assertFalse(gui.sound["music_on"])
            self.assertFalse(gui.audio.streams_on["music"])
            gui.toggle_stream("ambience_on")
            self.assertFalse(gui.audio.streams_on["ambience"])
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)["sound"]
            self.assertFalse(saved["music_on"])
            self.assertFalse(saved["ambience_on"])
            again = make_gui("main1_start", settings=path)
            self.assertFalse(again.sound["music_on"])

    def test_the_cog_has_both_switches_and_new_game_shares_a_row_with_concede(self):
        gui = make_gui("main1_start")
        gui.overlay = fset.SettingsPopup()
        frame(gui)
        b = {n: r for r, n in gui.overlay.buttons}
        self.assertEqual(b["music_on"].y, b["ambience_on"].y)
        self.assertEqual(b["newgame"].y, b["concede"].y)
        with mock.patch.object(gui, "toggle_stream") as t:
            gui.overlay.click(gui, b["music_on"].center, 1)
            t.assert_called_once_with("music_on")

    def test_vs_reveal_rings_when_a_game_starts(self):
        with open(os.path.join(ROOT, "forge_table.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count('self.play_cue("vs.reveal")'), 2)          # start / restart, and resume

    def test_the_title_has_its_own_sounds(self):
        gui = make_gui("main1_start")
        played = []
        gui.play_cue = lambda cid, *a, **k: played.append(cid) or True
        gui.boot = fboot.BootFlow(True)
        gui.audio = mock.Mock(ready=True, cues={})
        gui.apply_audio()
        gui.apply_audio()
        self.assertEqual(played.count("boot.splash"), 1)                     # once, not every tick
        gui.boot.to_menu()                                                      # splash -> title -> menu (the title-page patch)
        frame(gui)
        gui.boot.focus = 2
        frame(gui)
        self.assertIn("ui.menu_hover", played)
        gui.boot.choose(gui, "settings")
        self.assertIn("ui.menu_select", played)


class BuildToolTests(unittest.TestCase):
    """tools/build_sounds.py is a developer tool (numpy, scipy, ffmpeg); these only check its pieces when scipy is there."""

    def setUp(self):
        try:
            import scipy  # noqa: F401
        except ImportError:
            self.skipTest("scipy is not installed (only needed to rebuild the sounds)")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build_sounds
        self.b = build_sounds

    def test_a_loop_seam_has_no_click(self):
        import numpy as np
        b = self.b
        t = np.arange(b.SR * 6) / b.SR
        x = np.stack([np.sin(2 * np.pi * 110 * t)] * 2, axis=1).astype(np.float32) * np.linspace(0.2, 0.8, len(t))[:, None]
        y = b.loop_seam(x, 2.0)
        jump = np.abs(y[0] - y[-1]).max()
        self.assertLess(jump, 0.05)

    def test_level_hits_its_loudness_under_its_ceiling(self):
        import numpy as np
        b = self.b
        rng = np.random.default_rng(1)
        x = (rng.standard_normal((b.SR, 2)) * np.exp(-np.arange(b.SR) / 3000)[:, None]).astype(np.float32)
        y = b.level(x, -24, -6)
        self.assertLessEqual(np.abs(y).max(), b.db(-6) + 1e-6)
        self.assertLess(abs(b.loudness(y) - -24), 4.5)                       # at most 6 dB of limiting, then it backs off

    def test_onsets_find_each_strike(self):
        import numpy as np
        b = self.b
        x = np.zeros((b.SR * 3, 2), np.float32)
        for at in (0.2, 1.1, 2.3):
            i = int(at * b.SR)
            x[i:i + 4000] += (np.exp(-np.arange(4000) / 600) * 0.8)[:, None]
        hits = b.onsets(x, -30)
        self.assertEqual(len(hits), 3)
        for h, at in zip(hits, (0.2, 1.1, 2.3)):
            self.assertLess(abs(h - at), 0.02)

    def test_every_cue_has_settings(self):
        b = self.b
        recipes = b.recipes(lambda ref: None)
        self.assertEqual(set(recipes) - set(b.CUE_SETTINGS), set())
        self.assertTrue(set(b.TIGHT) <= set(recipes))


if __name__ == "__main__":
    unittest.main()
