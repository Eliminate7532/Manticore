# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SND1 (Karl, 3 Nov 2026: "Add a wolf "howl" sound to the game that plays at dusk." and "Add a rooster sound at dawn."):
rpg.clock.crossed, rpg.worldsound (the one rule), the Adventure's window playing them, the ambience bus's one-shots, and the
sound files and their sources."""
import json
import os
import re
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

import audio  # noqa: E402
from rpg import clock, worldsound  # noqa: E402
from tests import test_rpg14 as r14  # noqa: E402
from tests.test_forge_table import key  # noqa: E402

SOUNDS = os.path.join(ROOT, "sounds")
MANIFEST = os.path.join(SOUNDS, "cues.json")
TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
DAY = clock.DAY


def at(day, hh, mm=0):
    return (day - 1) * DAY + hh * 60 + mm


def manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)["cues"]


def sources_file():
    with open(os.path.join(ROOT, "tools", "sound_sources.json"), encoding="utf-8") as f:
        return json.load(f)


# ---- the clock -------------------------------------------------------------------------------------------------------------------
class CrossedTests(unittest.TestCase):
    def test_walking_over_dusk_and_over_dawn(self):
        self.assertEqual(clock.crossed(at(1, 17, 59), at(1, 18, 0.5)), [("dusk", at(1, 18))])
        self.assertEqual(clock.crossed(at(2, 4, 59), at(2, 5, 1)), [("dawn", at(2, 5))])
        self.assertEqual(clock.crossed(at(1, 18, 1), at(1, 19)), [])            # already dusk: nothing reached
        self.assertEqual(clock.crossed(at(1, 12), at(1, 12)), [])               # the clock didn't move
        self.assertEqual(clock.crossed(at(1, 19), at(1, 12)), [])               # nor back

    def test_reaching_the_time_exactly_counts_and_starting_on_it_does_not(self):
        self.assertEqual(clock.crossed(at(1, 12), at(1, 18)), [("dusk", at(1, 18))])   # Rest until dusk: to 18:00 exactly
        self.assertEqual(clock.crossed(at(1, 18), at(1, 18, 30)), [])

    def test_a_rest_until_dusk_and_a_nights_sleep_past_dawn(self):
        w = {"clock": at(1, 10)}
        old = clock.now(w)
        clock.to_dusk(w)
        self.assertEqual(clock.crossed(old, clock.now(w)), [("dusk", at(1, 18))])
        w = {"clock": at(1, 22)}
        old = clock.now(w)
        clock.to_morning(w)                                                     # wakes at 07:00 next day: dawn at 05:00 passed
        self.assertEqual(clock.crossed(old, clock.now(w)), [("dawn", at(2, 5))])
        w = {"clock": at(2, 3)}                                                 # asleep at 03:00: the same night's dawn
        old = clock.now(w)
        clock.to_morning(w)
        self.assertEqual(clock.crossed(old, clock.now(w)), [("dawn", at(2, 5))])

    def test_a_jump_of_more_than_a_day_sounds_each_boundary_once(self):
        got = clock.crossed(at(1, 12), at(4, 12))                               # three days: three dusks and three dawns passed
        self.assertEqual([n for n, _m in got], ["dusk", "dawn"])
        self.assertEqual(got, [("dusk", at(3, 18)), ("dawn", at(4, 5))])        # each once, at its latest time

    def test_bad_input_is_nothing(self):
        for a, b in ((None, 5), ("x", 5), (float("nan"), 5), (0, float("inf"))):
            self.assertEqual(clock.crossed(a, b), [])


class TrackerTests(unittest.TestCase):
    K = ("folder", 1)

    def test_first_look_is_silent_then_each_crossing_plays_once(self):
        t = worldsound.Tracker()
        self.assertEqual(t.tick(self.K, at(1, 18, 30), True), [])               # a load at 18:30: silent
        self.assertEqual(t.tick(self.K, at(1, 19), True), [])
        self.assertEqual(t.tick(self.K, at(2, 5), True), ["rpg.rooster"])
        self.assertEqual(t.tick(self.K, at(2, 5, 1), True), [])                  # once
        self.assertEqual(t.tick(self.K, at(2, 18, 2), True), ["rpg.howl"])

    def test_reset_is_silent_and_another_game_starts_fresh(self):
        t = worldsound.Tracker()
        t.reset(self.K, at(1, 17))
        t.reset(self.K, at(1, 18, 30))                                           # a save loaded at 18:30
        self.assertEqual(t.tick(self.K, at(1, 18, 31), True), [])
        self.assertEqual(t.tick(("folder", 2), at(9, 5, 30), True), [])          # another slot: noted, not played
        self.assertEqual(t.tick(("folder", 2), at(9, 18), True), ["rpg.howl"])

    def test_a_crossing_out_of_earshot_waits_for_its_phase(self):
        t = worldsound.Tracker()
        t.reset(self.K, at(1, 17, 59))
        self.assertEqual(t.tick(self.K, at(1, 18, 1), False), [])                # the duel's way in is up
        self.assertEqual(t.tick(self.K, at(1, 18, 1), False), [])                # the duel, the results
        self.assertEqual(t.tick(self.K, at(1, 18, 1), True), ["rpg.howl"])       # back on the map, still dusk: now
        self.assertEqual(t.tick(self.K, at(1, 18, 2), True), [])

    def test_a_crossing_out_of_earshot_is_gone_when_its_phase_is(self):
        t = worldsound.Tracker()
        t.reset(self.K, at(1, 17, 59))
        t.tick(self.K, at(1, 18, 1), False)
        self.assertEqual(t.tick(self.K, at(1, 20, 30), True), [])                # night by then: never
        t.tick(self.K, at(2, 4, 59), True)
        t.tick(self.K, at(2, 5, 1), False)                                       # dawn reached in the Warrens
        self.assertEqual(t.tick(self.K, at(2, 7, 10), True), [])                 # out by day: the dawn is over

    def test_a_long_rest_plays_both_once_in_time_order(self):
        t = worldsound.Tracker()
        t.reset(self.K, at(1, 12))
        self.assertEqual(t.tick(self.K, at(3, 6), True), ["rpg.howl", "rpg.rooster"])


# ---- the Adventure's window ------------------------------------------------------------------------------------------------------
class WindowTests(r14.WindowBase):
    def heard(self, g, f):
        played = []
        g.play_cue = lambda cid, *a, **k: played.append(cid) or True
        f.walk_tick(g, 500.0, pressed=(0, 0))
        return played

    def test_walking_over_dusk_on_the_moors_howls(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        f.game.save.world["clock"] = at(1, 17, 59)
        self.assertEqual(self.heard(g, f), [])
        f.game.save.world["clock"] = at(1, 18, 0.5)
        self.assertEqual(self.heard(g, f), ["rpg.howl"])
        self.assertEqual(self.heard(g, f), [])                                  # once
        f.game.save.world["clock"] = at(2, 5)
        self.assertEqual(self.heard(g, f), ["rpg.rooster"])                      # SND1B: the rooster ships

    def test_the_town_map_follows_the_clock_and_the_warrens_do_not(self):
        g, _l = self.setup_game()
        f = self.on_map(g, TOWN, "start")
        f.game.save.world["clock"] = at(1, 17, 59)
        self.heard(g, f)
        f.game.save.world["clock"] = at(1, 18, 1)
        self.assertEqual(self.heard(g, f), ["rpg.howl"])
        f = self.on_map(g, "warrens_1")
        self.assertFalse(f.explorer.area.outdoor)
        f.game.save.world["clock"] = at(2, 5, 1)
        self.assertEqual(self.heard(g, f), [])                                  # underground: not heard
        f = self.on_map(g, OUT, "from_town")
        self.assertEqual(self.heard(g, f), ["rpg.rooster"])                      # up again while it is still dawn

    def test_loading_a_save_at_dusk_is_silent(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        self.heard(g, f)                                                        # 07:00 noted
        f.game.save.world["clock"] = at(1, 18, 30)                              # (as if another session had saved at 18:30)
        f.write()
        played = []
        g.play_cue = lambda cid, *a, **k: played.append(cid) or True
        f.open_slot(g, 1)                                                       # load (reads 18:30 back)
        f = self.on_map(g, OUT, "from_town")
        self.assertEqual(clock.phase(clock.now(f.game.save.world)), "dusk")
        self.assertEqual(self.heard(g, f) + played, [])
        f.game.save.world["clock"] = at(2, 5, 1)
        self.assertEqual(self.heard(g, f), ["rpg.rooster"])                      # and it listens from then on

    def test_a_new_game_is_silent(self):
        g, _l = self.setup_game()                                               # a new game, then opened
        f = self.on_map(g, OUT, "from_town")
        self.assertEqual(self.heard(g, f), [])

    def test_a_duel_across_dusk_plays_after_it_while_it_is_still_dusk(self):
        from rpg import encounters
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        f.game.save.world["clock"] = at(1, 17, 59)
        self.heard(g, f)
        f.game.save.world["clock"] = at(1, 18, 0.5)                              # the step into the foe passes 18:00
        f.fight, f.sub, f.encounter = encounters.RandomFoe("thornback"), "fight", None
        self.assertEqual(self.heard(g, f), [])                                  # the way into the duel: not now
        f2 = self.win(g, encounters.RandomFoe("thornback"))                      # the duel, the table, back: a new window
        self.assertIsNot(f2, f)
        self.assertEqual(f2.sub, "result")
        self.assertEqual(self.heard(g, f2), [])                                  # the results screen: not yet
        f2.sub, f2.result = None, None
        self.assertEqual(self.heard(g, f2), ["rpg.howl"])                        # back on the map, still dusk

    def test_a_duel_across_dusk_is_silent_when_dusk_is_over(self):
        from rpg import encounters
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        f.game.save.world["clock"] = at(1, 17, 59)
        self.heard(g, f)
        f.game.save.world["clock"] = at(1, 18, 0.5)
        f.fight, f.sub, f.encounter = encounters.RandomFoe("thornback"), "fight", None
        self.heard(g, f)
        f2 = self.win(g, encounters.RandomFoe("thornback"))
        f2.sub, f2.result = None, None
        f2.game.save.world["clock"] = at(1, 20, 5)                               # night by now
        self.assertEqual(self.heard(g, f2), [])

    def test_rest_until_dusk_howls_when_you_wake_and_a_night_crows(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 10)
        self.heard(g, f)
        f.sleep_at_inn("dusk")
        self.assertEqual(self.heard(g, f), ["rpg.howl"])
        f.game.save.world["clock"] = at(1, 21, 30)
        self.heard(g, f)
        f.sleep_at_inn()                                                        # to 07:00: the dawn passed in the night
        self.assertEqual(clock.now(f.game.save.world), at(2, 7))
        self.assertEqual(self.heard(g, f), ["rpg.rooster"])

    def test_the_innkeepers_rest_through_the_talk(self):
        from rpg import talk
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 21, 30)
        self.heard(g, f)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=True)                                             # Sleep until morning
        self.assertEqual(clock.now(f.game.save.world), at(2, 7))
        self.assertEqual(self.heard(g, g.modal), ["rpg.rooster"])

    def test_a_shop_window_waits(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 17, 59)
        self.heard(g, f)
        f.set_stage("town")                                                     # a shop's window
        f.game.save.world["clock"] = at(1, 18, 1)
        self.assertEqual(self.heard(g, f), [])
        f.set_stage("map")
        self.assertEqual(self.heard(g, f), ["rpg.howl"])


class _Spy:
    def __init__(self, ch, calls):
        self.ch, self.calls = ch, calls

    def get_busy(self):
        return True

    def set_volume(self, *v):
        self.calls.append(tuple(v))

    def stop(self):
        self.ch.stop()


# ---- the ambience bus ------------------------------------------------------------------------------------------------------------
class DirectorTests(unittest.TestCase):
    def director(self):
        return audio.AudioDirector(MANIFEST, SOUNDS, rnd=__import__("random").Random(3))

    def test_the_howl_plays_on_its_own_channels_at_the_ambience_level(self):
        d = self.director()
        self.assertTrue(d.ready, d.error)
        self.assertEqual(d.cues["rpg.howl"].bus, "ambience")
        self.assertTrue(d.play("rpg.howl", 0.0))
        ch = d.voices[-1][0]
        self.assertIn(ch, d.channels["ambience_fx"])
        self.assertNotIn(ch, d.channels["ambience"])                             # never the loops' cross-fade pair
        self.assertAlmostEqual(d.voice_gain("ambience", 0.0), d.stream_gain("ambience", 0.0))
        d.stop_all()

    def test_ambience_off_sound_off_and_m_silence_it(self):
        d = self.director()
        d.streams_on["ambience"] = False                                         # Cog > Ambience off
        self.assertFalse(d.play("rpg.howl", 0.0))
        d.streams_on["ambience"] = True
        d.muted = True                                                           # Sound off / M
        self.assertFalse(d.play("rpg.howl", 100.0))
        d.muted = False
        self.assertTrue(d.play("rpg.howl", 200.0))
        ch, _cid, bus, t0, left, right = d.voices[-1]
        self.assertGreater(left * d.voice_gain(bus, 200.0), 0)
        calls = []                                                               # pygame's get_volume doesn't report a pan:
        d.voices[-1] = (_Spy(ch, calls), _cid, bus, t0, left, right)             # watch what update() sets
        d.streams_on["ambience"] = False                                         # switched off while it howls
        d.update(200.5)
        self.assertEqual(calls[-1], (0.0, 0.0))
        d.stop_all()

    def test_the_rooster_plays_on_the_ambience_channels_and_ambience_off_silences_it(self):
        d = self.director()
        self.assertEqual(d.cues["rpg.rooster"].bus, "ambience")
        d.streams_on["ambience"] = False
        self.assertFalse(d.play("rpg.rooster", 0.0))
        d.streams_on["ambience"] = True
        self.assertTrue(d.play("rpg.rooster", 100.0))
        self.assertIn(d.voices[-1][0], d.channels["ambience_fx"])
        d.stop_all()

    def test_the_tables_sound_off_never_reaches_the_director(self):
        from tests.test_forge_table import make_gui
        g = make_gui("main1_start")
        g.sound["on"] = False
        self.assertFalse(g.play_cue("rpg.howl"))


# ---- the files and their sources -------------------------------------------------------------------------------------------------
class AssetTests(unittest.TestCase):
    def test_the_howl_is_a_rare_cue_on_the_ambience_bus_and_short_enough(self):
        spec = manifest()["rpg.howl"]
        self.assertEqual(spec["bus"], "ambience")
        self.assertGreaterEqual(spec["cooldown_ms"], 10000)
        pygame.mixer.init(44100, -16, 2, 512)
        for name in spec["files"]:
            p = os.path.join(SOUNDS, name)
            self.assertLess(os.path.getsize(p), 400 * 1024)
            self.assertTrue(3.0 <= pygame.mixer.Sound(p).get_length() <= 6.0)

    def test_the_howl_ships_as_candidate_b(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))                          # SND1C: Karl's pick, 3 Nov 2026 ("B")
        import build_sounds
        self.assertEqual(build_sounds.WORLD_PICK["rpg.howl"], "B")
        self.assertNotIn("rpg.howl", build_sounds.WORLD_PENDING)

    def test_the_shipped_howl_is_the_picked_candidate_not_a_forgotten_rebuild(self):
        """SND1C: the howl's three candidates differ in length by 0.57 s or more (A 3.30, B 3.88, C 4.46 s as pygame reads them),
        so a pick changed in WORLD_PICK without `build_sounds.py --only rpg.howl` leaves a file of another candidate's length.
        (Not done for the rooster: its A and C differ by 0.03 s.)"""
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build_sounds
        pygame.mixer.init(44100, -16, 2, 512)
        cand = os.path.join(ROOT, "docs", "rounds", "SND1", "candidates")
        lengths = {c: pygame.mixer.Sound(os.path.join(cand, f"rpg.howl_{c}.mp3")).get_length() for c in "ABC"}
        shipped = pygame.mixer.Sound(os.path.join(SOUNDS, manifest()["rpg.howl"]["files"][0])).get_length()
        pick = build_sounds.WORLD_PICK["rpg.howl"]
        self.assertLess(abs(shipped - lengths[pick]), 0.15, (shipped, lengths))
        nearest = min(lengths, key=lambda c: abs(shipped - lengths[c]))
        self.assertEqual(nearest, pick, (shipped, lengths))

    def test_the_rooster_ships_as_candidate_a_a_rare_cue_on_the_ambience_bus(self):
        spec = manifest()["rpg.rooster"]                                          # SND1B (Karl: "Allow alys's recordings")
        self.assertEqual(spec["bus"], "ambience")
        self.assertGreaterEqual(spec["cooldown_ms"], 10000)
        self.assertEqual(spec["files"], ["sfx/rpg.rooster.1.ogg"])
        pygame.mixer.init(44100, -16, 2, 512)
        p = os.path.join(SOUNDS, spec["files"][0])
        self.assertLess(os.path.getsize(p), 400 * 1024)
        self.assertTrue(2.0 <= pygame.mixer.Sound(p).get_length() <= 4.0)     # the brief: one crow, 2-4 s
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build_sounds
        self.assertEqual(build_sounds.WORLD_PICK["rpg.rooster"], "A")
        self.assertNotIn("rpg.rooster", build_sounds.WORLD_PENDING)

    def test_shipped_sources_are_cc0_or_an_allowed_dedication_and_credited(self):
        from tests.test_au1 import PD_WORDS, licence_problem
        data = sources_file()
        self.assertFalse({k for k in data.get("pending", {}) if not k.startswith("_")})   # nothing waits any more
        for k in ("snd1-creature-sfx", "snd1-avalanche", "snd1-wolf-monster"):
            s = data["sources"][k]
            self.assertEqual(s["licence"], "CC0-1.0", k)
            self.assertTrue(s["page"].startswith("https://opengameart.org/content/"), k)
            self.assertIn("CC0", s["note"], k)
        for k in ("snd1-pd-rooster-medium", "snd1-pd-rooster-small"):
            s = data["sources"][k]
            self.assertEqual(s["licence"], "public-domain-dedication", k)
            self.assertTrue(s["page"].startswith("https://commons.wikimedia.org/wiki/File:"), k)
            self.assertIn(PD_WORDS + ", alys", s["checked"], k)
            self.assertIsNone(licence_problem(s), k)
        with open(os.path.join(SOUNDS, "CREDITS.txt"), encoding="utf-8") as f:
            credits = f.read()
        for k in ("snd1-creature-sfx", "snd1-pd-rooster-medium", "snd1-pd-rooster-small"):
            self.assertIn(k + ":", credits)
        self.assertIn("public domain (the author's own dedication on Wikimedia Commons)", credits)
        with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.txt"), encoding="utf-8") as f:
            self.assertIn("snd1-pd-rooster-medium:", f.read())

    def test_the_licence_rule_refuses_a_dedication_off_commons_or_without_its_words(self):
        from tests.test_au1 import licence_problem
        good = sources_file()["sources"]["snd1-pd-rooster-medium"]
        self.assertIsNone(licence_problem(good))
        for page in ("https://pixabay.com/sound-effects/rooster/", "https://wikimedia.org.example.com/x",
                     "http://commons.wikimedia.org/wiki/File:Medium_rooster_crowing.ogg", "https://opengameart.org/content/x"):
            self.assertIsNotNone(licence_problem(dict(good, page=page)), page)
        self.assertIsNotNone(licence_problem(dict(good, checked="public domain")))
        self.assertIsNotNone(licence_problem({k: v for k, v in good.items() if k != "checked"}))
        self.assertIsNotNone(licence_problem(dict(good, licence="CC-BY-4.0")))
        self.assertIsNone(licence_problem({"licence": "CC0-1.0", "page": "https://opengameart.org/content/x"}))

    def test_each_cue_has_three_candidates_and_a_pick(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build_sounds
        cands = build_sounds.world_candidates(lambda ref, raw=False: None)
        for cid in ("rpg.howl", "rpg.rooster"):
            self.assertEqual(sorted(cands[cid]), ["A", "B", "C"])
            self.assertIn(build_sounds.WORLD_PICK[cid], cands[cid])
            for letter in "ABC":
                self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "rounds", "SND1", "candidates", f"{cid}_{letter}.mp3")))


if __name__ == "__main__":
    unittest.main()
