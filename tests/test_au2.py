# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 49 (AU2): the sounds Karl picked on the candidates page (7 Oct 2026) - typed cues (arrivals by creature family / artifact /
enchantment, lands by type, destruction by type), the deck-screen and settings music, Lament of the War on big attacks, Cold Silence
in the playlist, the four rooms, and the 17 Keeps left exactly as they were (cue_types.py, audio.py, forge_table.py, sounds/,
tools/build_sounds.py, tools/sound_sources.json)."""
import hashlib
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
import cue_types as ct
import forge_settings as fset
import forge_table as ft
import licenses_view
import version
from tests.test_forge_table import make_gui
from tests.test_round24 import beat

SOUNDS = os.path.join(ROOT, "sounds")
MANIFEST = os.path.join(SOUNDS, "cues.json")


def manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def director():
    return audio.AudioDirector(MANIFEST, SOUNDS, rnd=__import__("random").Random(5))


def card(type_line, **kw):
    c = {"type": type_line}
    c.update(kw)
    return c


# Karl's picks of 7 Oct (the candidates page, collection "picks"): the cue each one became in the patch
PICKED_CUES = ["spell.cast", "spell.resolve", "spell.fizzle", "ability.trigger", "token.create", "card.land", "card.land.artifact",
               "card.land.enchantment", "land.play.forest", "land.play.swamp", "destroy", "destroy.artifact", "destroy.enchantment",
               "damage.creature", "damage.player", "life.loss", "life.gain", "counter.add", "counter.remove", "combat.block",
               "phase.turn_mine", "phase.turn_theirs", "game.win", "game.lose", "rewind", "tap", "untap", "flurry", "card.draw",
               "deck.shuffle", "ui.error", "ui.menu_hover", "amb.citadel", "amb.title", "amb.ruins", "amb.plain", "music.defeat"]

# The 17 cues Karl marked Keep (7 Oct review): their files, first 16 hex of SHA-256, as AU1 built them. A rebuild must not change one.
KEEP_FILES = {
    "sfx/ui.click.1.ogg": "1f5cdaab53c2d542", "sfx/ui.click.2.ogg": "b9f76ce6dec25911", "sfx/ui.click.3.ogg": "59c7fcdd5af87cfa",
    "sfx/ui.hover.1.ogg": "357eb24bed3a5028", "sfx/ui.hover.2.ogg": "6a1057f6a4000ca3", "sfx/ui.hover.3.ogg": "65525e62f86c8ed0",
    "sfx/ui.dialog_close.1.ogg": "120a8f1a2ce924a3", "sfx/ui.menu_select.1.ogg": "8e45535d46c579d4",
    "sfx/boot.splash.1.ogg": "028a9153b6199db5", "sfx/vs.reveal.1.ogg": "6cadd4b08f9ba02c",
    "sfx/card.pickup.1.ogg": "672844b739288a43", "sfx/card.pickup.2.ogg": "bba26184a2a00755", "sfx/card.pickup.3.ogg": "8d95701e67d50fe8",
    "sfx/card.pickup.4.ogg": "438a7e5abe6e3148", "sfx/combat.attack.1.ogg": "9877812525235ff7", "sfx/combat.attack.2.ogg": "d15a049068985988",
    "sfx/exile.1.ogg": "ee1de2839341d643", "sfx/exile.2.ogg": "1b1e7afb7bcf1a68", "sfx/exile.3.ogg": "3cfd5f7adef53806",
    "ambience/graveyard.ogg": "516b9be3f09ec33c", "ambience/cathedral.ogg": "6c365a1f44328a4b",
    "music/title.ogg": "694a31620f8b5655", "music/game1.ogg": "7013dbf16f899a76", "music/game2.ogg": "2c7e08c79f537c55",
    "music/game3.ogg": "d4267bb9b03b7074", "music/game4.ogg": "493a9b85bcad4393", "music/game5.ogg": "723341d436f7f1a5"}


# ---- cue_types ------------------------------------------------------------------------------------------------------------------
class CueTypeTests(unittest.TestCase):
    def test_subtypes_after_any_dash(self):
        self.assertEqual(ct.subtypes(card("Legendary Creature - Human Wizard")), ["human", "wizard"])
        self.assertEqual(ct.subtypes(card("Creature — Zombie Dog")), ["zombie", "dog"])
        self.assertEqual(ct.subtypes(card("Artifact")), [])
        self.assertEqual(ct.subtypes(None), [])

    def test_land_types(self):
        self.assertEqual(ct.land_type(card("Basic Land - Island", isLand=True)), "island")
        self.assertEqual(ct.land_type(card("Basic Snow Land - Forest", isLand=True)), "forest")
        self.assertEqual(ct.land_type(card("Land - Island Swamp", isLand=True)), "island")       # a dual: the first type
        self.assertEqual(ct.land_type(card("Land", isLand=True)), "other")                         # Command Tower, a fetch land
        self.assertEqual(ct.land_type(card("Land - Urza's Tower", isLand=True)), "other")

    def test_creature_families_and_their_order(self):
        fam = lambda t: ct.creature_family(card("Creature - " + t, isCreature=True))
        self.assertEqual(fam("Human Wizard"), None)                     # humanoids are card.land itself
        self.assertEqual(fam("Elf Druid"), None)
        self.assertEqual(fam("Zombie Wizard"), "undead")
        self.assertEqual(fam("Vampire Bat"), "undead")                  # undead before vermin
        self.assertEqual(fam("Spirit Dragon"), "dragon")                # dragon first
        self.assertEqual(fam("Demon"), "demon")
        self.assertEqual(fam("Elemental"), "elemental")
        self.assertEqual(fam("Rat"), "vermin")
        self.assertEqual(fam("Human Werewolf"), "beast")
        self.assertEqual(fam("Angel"), "spirit")
        for fam_name, types in ct.FAMILY_TYPES.items():                 # every family names real words, lower case
            self.assertIn(fam_name, ct.FAMILIES)
            self.assertTrue(all(t == t.lower() and " " not in t for t in types), fam_name)

    def test_arrival_and_destroy_suffixes(self):
        self.assertEqual(ct.arrival_suffix(card("Artifact - Equipment")), "artifact")
        self.assertEqual(ct.arrival_suffix(card("Enchantment - Aura")), "enchantment")
        self.assertEqual(ct.arrival_suffix(card("Legendary Planeswalker - Nissa", isPlaneswalker=True)), "enchantment")
        self.assertEqual(ct.arrival_suffix(card("Artifact Creature - Golem", isCreature=True)), "elemental")   # a creature first
        self.assertEqual(ct.arrival_suffix(card("Artifact Creature - Construct", isCreature=True)), None)
        self.assertEqual(ct.arrival_suffix(card("Creature - Zombie", isCreature=True, token=True)), "undead")  # a token by its type
        self.assertEqual(ct.arrival_suffix(None), None)
        self.assertEqual(ct.destroy_suffix(card("Artifact")), "artifact")
        self.assertEqual(ct.destroy_suffix(card("Enchantment Creature - God", isCreature=True)), None)   # a creature breaks as one
        self.assertEqual(ct.destroy_suffix(card("Land", isLand=True)), "land")
        self.assertEqual(ct.destroy_suffix(card("Legendary Planeswalker - Jace", isPlaneswalker=True)), "enchantment")
        self.assertEqual(ct.destroy_suffix(None), None)

    def test_choose_falls_back_to_the_parent(self):
        cues = {"card.land": 1, "card.land.artifact": 1, "land.play": 1, "land.play.forest": 1, "destroy": 1}
        self.assertEqual(ct.for_arrival(card("Artifact"), cues), "card.land.artifact")
        self.assertEqual(ct.for_arrival(card("Creature - Zombie", isCreature=True), cues), "card.land")      # no undead cue yet
        self.assertEqual(ct.for_arrival(card("Basic Land - Forest", isLand=True), cues), "land.play.forest")
        self.assertEqual(ct.for_arrival(card("Basic Land - Island", isLand=True), cues), "land.play")        # not picked yet
        self.assertEqual(ct.for_arrival(None, cues), "card.land")
        self.assertEqual(ct.for_destroy(card("Land", isLand=True), cues), "destroy")
        self.assertEqual(ct.choose("x", None, cues), "x")

    def test_every_typed_cue_in_the_manifest_is_one_the_table_can_ask_for(self):
        cues = manifest()["cues"]
        typed = {f"{b}.{s}" for b, suffixes in ct.TYPED.items() for s in suffixes}
        for cid in cues:
            if any(cid.startswith(b + ".") for b in ct.TYPED):
                self.assertIn(cid, typed, cid)


# ---- the table ------------------------------------------------------------------------------------------------------------------
class TableTests(unittest.TestCase):
    def setUp(self):
        self.gui = make_gui("main1_start")
        self.played = []
        self.gui.play_cue = lambda cid, *a, **k: self.played.append(cid) or True
        self.gui.audio = mock.Mock(ready=True, cues=manifest()["cues"])
        self.cards = {}
        self.gui.session.card = lambda cid: self.cards.get(cid)

    def arrive(self, c):
        self.cards[7] = c
        self.played.clear()
        self.gui._play_beat(beat("zone", card=7, **{"from": "Stack", "to": "Battlefield"}), 99, "Karl")
        return self.played[-1]

    def destroyed(self, c, kind="zone"):
        self.cards[7] = c
        self.played.clear()
        if kind == "zone":
            self.gui._play_beat(beat("zone", card=7, **{"from": "Battlefield", "to": "Graveyard"}), 99, "Karl")
        else:
            self.gui._play_beat(beat("sacrifice", card=7), 99, "Karl")
        return self.played[-1]

    def test_arrivals_by_type(self):
        self.assertEqual(self.arrive(card("Artifact - Equipment")), "card.land.artifact")
        self.assertEqual(self.arrive(card("Enchantment")), "card.land.enchantment")
        self.assertEqual(self.arrive(card("Creature - Human Monk", isCreature=True)), "card.land")
        self.assertEqual(self.arrive(card("Basic Land - Forest", isLand=True)), "land.play.forest")
        self.assertEqual(self.arrive(card("Basic Land - Swamp", isLand=True)), "land.play.swamp")
        self.assertEqual(self.arrive(card("Basic Land - Island", isLand=True)), "land.play")       # round 2 will give it its own
        self.assertEqual(self.arrive(None), "card.land")                                           # data not loaded

    def test_destruction_by_type_and_sacrifice_too(self):
        self.assertEqual(self.destroyed(card("Artifact")), "destroy.artifact")
        self.assertEqual(self.destroyed(card("Enchantment - Aura")), "destroy.enchantment")
        self.assertEqual(self.destroyed(card("Creature - Elf", isCreature=True)), "destroy")
        self.assertEqual(self.destroyed(card("Land", isLand=True)), "destroy")                     # destroy.land: round 2
        self.assertEqual(self.destroyed(card("Artifact"), "sacrifice"), "destroy.artifact")

    def test_no_audio_still_plays_the_parent(self):
        self.gui.audio = None
        self.assertEqual(self.arrive(card("Artifact")), "card.land")


class BigAttackTests(unittest.TestCase):
    def setUp(self):
        self.gui = make_gui("main1_start")
        self.gui.play_cue = lambda *a, **k: True
        self.interludes = []
        self.gui.audio = mock.Mock(ready=True, cues=manifest()["cues"])
        self.gui.audio.music_interlude = lambda cid, now: self.interludes.append(cid) or True
        self.powers = {}
        self.gui.session.card = lambda cid: {"power": self.powers[cid]} if cid in self.powers else None
        self.lives = {}
        players = [{"id": pid, "life": life} for pid, life in self.lives.items()]
        self.gui.session.state = dict(self.gui.session.state or {}, players=players)

    def set_players(self, **lives):
        self.gui.session.state = dict(self.gui.session.state or {}, players=[{"id": int(k[1:]), "life": v} for k, v in lives.items()])

    def attack(self, *pairs):
        atk = []
        for i, (power, defender) in enumerate(pairs):
            self.powers[100 + i] = power
            atk.append({"card": 100 + i, "defender": defender})
        self.gui._play_beat(beat("attack", attackers=atk), 99, "Karl")

    def test_ten_power_is_big(self):
        self.set_players(p1=40, p2=40)
        self.attack((4, "p2"), (3, "p2"), (2, "p2"))
        self.assertEqual(self.interludes, [])                     # 9 power at 40 life: not big
        self.attack((5, "p2"), (5, "p1"))
        self.assertEqual(self.interludes, ["music.attack"])

    def test_lethal_to_one_player_is_big_and_the_cooldown_holds(self):
        self.set_players(p1=40, p2=4)
        with mock.patch.object(ft.time, "monotonic", return_value=1000.0):
            self.attack((4, "p2"))
        self.assertEqual(self.interludes, ["music.attack"])
        with mock.patch.object(ft.time, "monotonic", return_value=1000.0 + ft.ATTACK_MUSIC_COOLDOWN - 1):
            self.attack((12, "p1"))
        self.assertEqual(len(self.interludes), 1)                 # within two minutes: no second one
        with mock.patch.object(ft.time, "monotonic", return_value=1000.0 + ft.ATTACK_MUSIC_COOLDOWN + 1):
            self.attack((12, "p1"))
        self.assertEqual(len(self.interludes), 2)

    def test_unknown_cards_and_planeswalkers_and_sound_off(self):
        self.set_players(p1=40, p2=3)
        self.gui._play_beat(beat("attack", attackers=[{"card": 999, "defender": "p2"}]), 99, "Karl")   # unknown power: 0
        self.attack((6, "c55"))                                   # a planeswalker: counts toward the total only
        self.assertEqual(self.interludes, [])
        self.gui.sound["on"] = False
        self.attack((20, "p1"))
        self.assertEqual(self.interludes, [])


class ScreenMusicTests(unittest.TestCase):
    def test_deck_screen_settings_and_credits_have_their_own_music(self):
        gui = make_gui("main1_start")
        gui.session.state = None                                  # no game on the table
        gui.boot = fboot.BootFlow(False)
        self.assertEqual(gui.soundscape(), ("amb.title", "title"))
        gui.overlay = fset.SettingsPopup()
        self.assertEqual(gui.soundscape(), ("amb.title", "settings"))
        gui.overlay = None
        gui.modal = licenses_view.LicensesDialog()
        self.assertEqual(gui.soundscape(), ("amb.title", "settings"))
        gui.modal = None
        gui.boot = None
        gui.menu = mock.Mock()                                    # the deck screen (make_gui has no launcher to open the real one)
        self.assertEqual(gui.soundscape(), ("amb.title", "deck"))
        gui.overlay = fset.SettingsPopup()
        self.assertEqual(gui.soundscape(), ("amb.title", "settings"))

    def test_the_cog_in_a_game_leaves_the_game_music(self):
        gui = make_gui("main1_start")
        gui.overlay = fset.SettingsPopup()
        self.assertEqual(gui.soundscape()[1], "game")

    def test_loop_tracks_start_and_fall_back_to_the_title(self):
        d = director()
        with mock.patch.object(d, "_start_track", return_value=True) as start, \
                mock.patch.object(pygame.mixer.music, "get_busy", return_value=False):
            d.set_music("deck", 0.0)
            d.update(5.0)
            start.assert_called_once_with("music.deck", 5.0, loops=-1)
            start.reset_mock()
            d.set_music("settings", 6.0)
            d.update(10.0)
            start.assert_called_once_with("music.settings", 10.0, loops=-1)
            start.reset_mock()
            d.cues["music.deck"].paths = []                       # a copy without the file: the title's track
            d.set_music("deck", 11.0)
            d.update(15.0)
            start.assert_called_once_with("music.title", 15.0, loops=-1)

    def test_an_interlude_plays_once_then_the_playlist_rests_and_goes_on(self):
        d = director()
        started = []
        busy = {"v": True}

        def start(cid, now, loops=0):
            started.append(cid)
            busy["v"] = True
            return True
        with mock.patch.object(d, "_start_track", side_effect=start), \
                mock.patch.object(pygame.mixer.music, "get_busy", side_effect=lambda: busy["v"]), \
                mock.patch.object(pygame.mixer.music, "fadeout"):
            d.set_music("game", 0.0)
            d.music_cue = "music.game2"
            self.assertTrue(d.music_interlude("music.attack", 100.0))
            self.assertFalse(d.music_interlude("music.attack", 100.2))        # one at a time
            busy["v"] = False
            d.update(100.5)
            self.assertEqual(started, [])                                     # the game track fades first
            d.update(100.0 + audio.INTERLUDE_FADE + 0.01)
            self.assertEqual(started, ["music.attack"])
            busy["v"] = False                                                 # the interlude ends
            t = 150.0
            d.update(t)
            self.assertGreaterEqual(d.music_next_at - t, audio.MUSIC_GAP[0])  # the usual gap before the next game track
            d.update(t + audio.MUSIC_GAP[1] + 1)
            self.assertEqual(len(started), 2)
            self.assertTrue(started[1].startswith("music.game"))

    def test_no_interlude_outside_a_game_or_during_the_end_sting(self):
        d = director()
        with mock.patch.object(pygame.mixer.music, "get_busy", return_value=False):
            d.set_music("title", 0.0)
            self.assertFalse(d.music_interlude("music.attack", 1.0))
            d.set_music("game", 2.0)
            d.music_sting_after("music.victory", 3.0, 2.5)
            self.assertFalse(d.music_interlude("music.attack", 3.5))

    def test_cold_silence_is_in_the_playlist(self):
        self.assertIn("music.game6", director()._game_tracks())


# ---- the files ------------------------------------------------------------------------------------------------------------------
class FileTests(unittest.TestCase):
    def test_every_pick_has_its_files(self):
        cues = manifest()["cues"]
        for cid in PICKED_CUES:
            self.assertIn(cid, cues, cid)
            for f in cues[cid]["files"]:
                self.assertTrue(os.path.isfile(os.path.join(SOUNDS, f)), f)

    def test_the_keeps_are_byte_for_byte_as_au1_built_them(self):
        for f, digest in KEEP_FILES.items():
            with open(os.path.join(SOUNDS, f), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest()[:16], digest, f)

    def test_typed_cues_play_by_their_parents_rules(self):
        cues = manifest()["cues"]
        for cid, spec in cues.items():
            for base in ct.TYPED:
                if cid.startswith(base + "."):
                    parent = cues[base]
                    for key in ("bus", "cooldown_ms", "max_voices"):
                        self.assertEqual(spec[key], parent[key], (cid, key))

    def test_the_manifest_version_and_the_new_music(self):
        m = manifest()
        self.assertEqual(m["version"], 3)
        self.assertIn("SOUND_BIBLE", m["_comment"])
        for cid in ("music.deck", "music.settings", "music.game6", "music.attack"):
            self.assertTrue(m["cues"][cid]["stream"], cid)
            self.assertEqual(m["cues"][cid]["bus"], "music", cid)

    def test_the_bible_ships_beside_the_sounds(self):
        with open(os.path.join(SOUNDS, "SOUND_BIBLE.md"), encoding="utf-8") as f:
            text = f.read()
        for word in ("CC0", "magic", "horn", "crow", "potion", "Keep"):
            self.assertIn(word, text, word)


class BuildToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("the build tool needs numpy and scipy")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build_sounds
        cls.b = build_sounds
        with open(os.path.join(ROOT, "tools", "sound_sources.json"), encoding="utf-8") as f:
            cls.sources = json.load(f)["sources"]

    def test_the_credits_are_exactly_the_sources_a_recipe_names(self):
        with open(os.path.join(SOUNDS, "CREDITS.txt"), encoding="utf-8") as f:
            credited = set(re.findall(r"^([a-z0-9-]+):", f.read(), re.M))
        self.assertEqual(credited, self.b.referenced_sources(self.sources))
        self.assertNotIn("oga-healing", credited)                 # AU1's life.gain - replaced, no longer credited
        self.assertIn("oga2-crow-caw", credited)                  # AU2's game.lose
        self.assertIn("fs-toll", credited)                        # still in vs.reveal, a Keep

    def test_au2_sources_are_cc0_pages_with_their_files(self):
        au2 = {k: s for k, s in self.sources.items() if k.startswith(("oga2-", "mus2-"))}
        self.assertTrue(au2)
        for k, s in au2.items():
            self.assertEqual(s["licence"], "CC0-1.0", k)
            self.assertTrue(s["page"].startswith("https://opengameart.org/content/"), k)
            self.assertTrue(s["urls"] and all(u.startswith("https://") for u in s["urls"]), k)
            self.assertTrue(s.get("prefer_lossless"), k)
        self.assertIn("used under CC0", self.sources["oga2-footsteps-leather-cloth-armor"]["note"])

    def test_typed_settings_and_the_au2_cap(self):
        self.assertEqual(self.b.settings_for("card.land.artifact"), self.b.CUE_SETTINGS["card.land"])
        self.assertEqual(self.b.settings_for("destroy.enchantment"), self.b.CUE_SETTINGS["destroy"])
        self.assertEqual(self.b.AU2_CAP_SECONDS, 1.15)
        au2 = self.b.recipes_au2(lambda ref, raw=False: None)
        self.assertEqual(sorted(set(au2) | set(self.b.ambiences_au2(lambda ref, raw=False: None)) | {"music.defeat"}), sorted(PICKED_CUES))

    def test_au1_sources_keep_their_old_file_order(self):
        """An AU1 source never says prefer_lossless, so a rebuild of a Keep reads the file it always read."""
        for k, s in self.sources.items():
            if not k.startswith(("oga2-", "mus2-")):
                self.assertNotIn("prefer_lossless", s, k)


class VersionTests(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in version.VERSION.split(".")), (0, 28, 53))


if __name__ == "__main__":
    unittest.main()
