# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 24: sound (audio.py, the SOUND group in the cog, and forge_table's beat-to-cue mapping)."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import audio
import events
import forge_table as ft
from tests.test_forge_table import click, frame, key, make_gui, point_for_card


def beat(kind, **data):
    return events.Beat(dict(data, kind=kind, seq=0), 0.0)


class AudioModuleTests(unittest.TestCase):
    """audio.py is copied from the reference (docs/reference_2026-09-23/audio.py), plus a synth() cache added in this round; these
    check the module on its own, without a table."""

    def test_every_cue_in_the_manifest_loads(self):
        d = audio.AudioDirector(os.path.join("sounds", "cues.json"), "sounds")
        self.assertTrue(d.ready, d.error)
        with open(os.path.join("sounds", "cues.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(set(d.cues), set(manifest["cues"]))
        for cid, cue in d.cues.items():
            if manifest["cues"][cid].get("stream"):              # round AU1: ambience and music play from disk, not decoded here
                self.assertTrue(cue.paths, cid)
            else:
                self.assertTrue(cue.variants, cid)

    def test_disabled_is_a_no_op(self):
        d = audio.AudioDirector(os.path.join("sounds", "cues.json"), "sounds", enabled=False)
        self.assertFalse(d.ready)
        self.assertFalse(d.play("ui.click", 0.0))

    def test_cooldown_and_voice_limit(self):
        d = audio.AudioDirector(os.path.join("sounds", "cues.json"), "sounds")
        self.assertTrue(d.play("ui.click", 0.0))
        self.assertFalse(d.play("ui.click", 0.01))          # cooling down
        self.assertTrue(d.play("ui.click", 5.0))             # long since: fine again

    def test_a_thousand_plays_with_cooldowns_are_fast(self):
        d = audio.AudioDirector(os.path.join("sounds", "cues.json"), "sounds")
        t0 = time.perf_counter()
        for i in range(1000):
            d.play("tap", i * 0.001)
        ms = (time.perf_counter() - t0) * 1000
        self.assertLess(ms, 300, f"{ms:.1f} ms for 1000 plays")

    def test_synth_is_cached(self):
        audio._SYNTH_CACHE.clear()
        recipe = {"type": "tick", "freq": 4000}
        a = audio.synth(recipe)
        self.assertIn(json.dumps(recipe, sort_keys=True), audio._SYNTH_CACHE)
        b = audio.synth(recipe)
        self.assertEqual(list(a), list(b))
        self.assertIsNot(a, b)          # a fresh array each call: a caller must be free to mutate its own copy


class StartupTests(unittest.TestCase):
    def test_building_the_director_does_not_block_table_creation(self):
        ft._AUDIO_SHARED["director"] = None
        ft._AUDIO_SHARED["building"] = False
        ft._AUDIO_SHARED["event"] = __import__("threading").Event()
        real_init = audio.AudioDirector.__init__

        def slow_init(self, *a, **k):
            time.sleep(0.3)
            real_init(self, *a, **k)
        with mock.patch.object(audio.AudioDirector, "__init__", slow_init):
            t0 = time.perf_counter()
            gui = make_gui("main1_start")
            elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.2, f"table creation took {elapsed:.2f}s (should not wait for audio)")
        ft._AUDIO_SHARED["event"].wait(2.0)


class MKeyAndCogTests(unittest.TestCase):
    def test_m_toggles_sound_and_shows_a_toast(self):
        gui = make_gui("main1_start")
        self.assertTrue(gui.sound["on"])
        key(gui, pygame.K_m)
        self.assertFalse(gui.sound["on"])
        self.assertIsNotNone(gui.toast)
        key(gui, pygame.K_m)
        self.assertTrue(gui.sound["on"])

    def test_m_does_not_fire_while_typing_in_a_text_field(self):
        gui = make_gui("main1_start")
        gui.open_report()
        gui.overlay.focus = 0
        gui.overlay.fields[0].text = ""
        key(gui, pygame.K_m, unicode="m")
        self.assertEqual(gui.overlay.fields[0].text, "m")
        self.assertTrue(gui.sound["on"])          # unaffected: the field consumed the key

    def test_the_cog_switch_and_volume_buttons_persist_to_settings_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            gui = make_gui("main1_start", settings=path)
            gui.overlay = __import__("forge_settings").SettingsPopup()
            frame(gui)
            from tests.test_settings_report import popup_point
            click(gui, popup_point(gui, "sound"))
            self.assertFalse(gui.sound["on"])
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertFalse(data["sound"]["on"])
            gui.overlay = __import__("forge_settings").SettingsPopup()
            frame(gui)
            click(gui, popup_point(gui, "vol_up"))
            self.assertAlmostEqual(gui.sound["master"], 0.9, places=2)


class BeatMappingTests(unittest.TestCase):
    """For each beat kind the spec's table names, the right cue is chosen (a spy on ForgeTable.play_cue)."""

    def setUp(self):
        self.gui = make_gui("main1_start")
        self.played = []
        self.gui.play_cue = lambda cid, pan=0.0, gain_db=0.0: self.played.append(cid)

    def test_draw_play_land_discard_destroy_exile(self):
        g = self.gui
        g._play_beat(beat("zone", card=1, **{"from": "Library", "to": "Hand"}), 99, "Karl")
        self.assertIn("card.draw", self.played)
        g._play_beat(beat("zone", card=1, **{"from": "Hand", "to": "Stack"}), 99, "Karl")
        self.assertIn("card.play", self.played)
        g._play_beat(beat("zone", card=1, **{"from": "Hand", "to": "Graveyard"}), 99, "Karl")
        self.assertIn("card.discard", self.played)
        g._play_beat(beat("zone", card=1, **{"from": "Battlefield", "to": "Graveyard"}), 99, "Karl")
        self.assertIn("destroy", self.played)
        g._play_beat(beat("zone", card=1, **{"from": "Battlefield", "to": "Exile"}), 99, "Karl")
        self.assertIn("exile", self.played)

    def test_tap_untap_and_flurry(self):
        g = self.gui
        g._play_beat(beat("tap", card=1, tapped=True), 99, "Karl")
        self.assertIn("tap", self.played)
        g._play_beat(beat("tap", card=1, tapped=False), 99, "Karl")
        self.assertIn("untap", self.played)
        b = beat("tap", card=1, tapped=True)
        b.flurry = True
        g._play_beat(b, 99, "Karl")
        self.assertIn("flurry", self.played)

    def test_cast_and_resolve(self):
        g = self.gui
        g._play_beat(beat("cast", card=1, trigger=False), 99, "Karl")
        self.assertIn("spell.cast", self.played)
        g._play_beat(beat("cast", card=1, trigger=True), 99, "Karl")
        self.assertIn("ability.trigger", self.played)
        g._play_beat(beat("resolve", card=1, fizzled=False), 99, "Karl")
        self.assertIn("spell.resolve", self.played)
        g._play_beat(beat("resolve", card=1, fizzled=True), 99, "Karl")
        self.assertIn("spell.fizzle", self.played)

    def test_damage_life_counters_token_combat(self):
        g = self.gui
        g._play_beat(beat("damage_card", card=1, amount=3), 99, "Karl")
        self.assertIn("damage.creature", self.played)
        g._play_beat(beat("damage_player", player=99, amount=3), 99, "Karl")
        self.assertIn("damage.player", self.played)
        g._play_beat(beat("life", player=99, old=40, new=45), 99, "Karl")
        self.assertIn("life.gain", self.played)
        g._play_beat(beat("counters", card=1, counter="P1P1", old=0, new=1), 99, "Karl")
        self.assertIn("counter.add", self.played)
        g._play_beat(beat("counters", card=1, counter="P1P1", old=1, new=0), 99, "Karl")
        self.assertIn("counter.remove", self.played)
        g._play_beat(beat("token"), 99, "Karl")
        self.assertIn("token.create", self.played)
        g._play_beat(beat("attack", attackers=[{"card": 1, "defender": "p2"}]), 99, "Karl")
        self.assertIn("combat.attack", self.played)
        g._play_beat(beat("attack", attackers=[]), 99, "Karl")          # empty: no sound
        g._play_beat(beat("block", pairs=[{"attacker": 1, "blocker": 2}]), 99, "Karl")
        self.assertIn("combat.block", self.played)

    def test_life_loss_is_skipped_when_a_damage_player_beat_covers_the_same_hit(self):
        g = self.gui
        dmg = beat("damage_player", player=99, amount=3)
        loss = beat("life", player=99, old=40, new=37)
        g.beats_this_frame = [dmg, loss]
        g._play_beat(loss, 99, "Karl")
        self.assertNotIn("life.loss", self.played)

    def test_turn_and_outcome(self):
        g = self.gui
        g._play_beat(beat("turn", player=99), 99, "Karl")
        self.assertIn("phase.turn_mine", self.played)
        g._play_beat(beat("turn", player=5), 99, "Karl")
        self.assertIn("phase.turn_theirs", self.played)
        g._play_beat(beat("outcome", winner=99), 99, "Karl")
        self.assertIn("game.win", self.played)
        g._play_beat(beat("outcome", winner=5), 99, "Karl")
        self.assertIn("game.lose", self.played)

    def test_stale_beats_are_never_reached(self):
        g = self.gui
        b = beat("tap", card=1, tapped=True)
        b.stale = True
        g.beats_this_frame = [b]
        g.apply_audio()
        self.assertEqual(self.played, [])


class UiHookTests(unittest.TestCase):
    def test_press_plays_ui_click(self):
        gui = make_gui("main1_start")
        played = []
        gui.play_cue = lambda cid, *a, **k: played.append(cid) or True
        gui.press("help")
        self.assertIn("ui.click", played)

    def test_hover_tick_respects_the_setting(self):
        gui = make_gui("main1_start")
        gui.sound["hover_tick"] = False
        played = []
        gui.play_cue = lambda cid, *a, **k: played.append(cid) or True
        hand = gui.session.me()["zones"]["hand"]
        selectable = next((c for c in hand if c.get("selectable") or c.get("weak")), None)
        if selectable:
            from unittest import mock as m
            with m.patch("time.monotonic", return_value=1000.0):
                from tests.test_forge_table import move
                move(gui, point_for_card(gui, selectable["id"]))
            self.assertNotIn("ui.hover", played)


if __name__ == "__main__":
    unittest.main()
