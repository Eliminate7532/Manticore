# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG17: Karl's decisions of 24 Oct 2026 for the Adventure slice (claude/DECISIONS_2026-10-24.md in the Project).

  1  S1 (b): the people of Lantern Hollow walk between their places, in sight (not at once, and not only out of sight)
  2  a new game starts at 07:00 (was 17:00)
  3  the inn offers "Rest until dusk" (to 18:00) by day; the inn's night hint names it
  4  rare foes: chances doubled (0.25 / 0.3 / 0.2 -> 0.5 / 0.6 / 0.4), cooldown 3 -> 2 days; rewards unchanged
  5  the clock reads "Day N, <period> HH:MM"
  6  the Warrens' encounter_rate 50 -> 100
  7  a lost duel keeps the clock (waking at the inn no longer jumps to 07:00)
  8  Old Gorsehide plays the regular (Jumpstart) decks (claude/SLICE_NIGHT_DUELS_2026-10-24.md: 23.6% on his archetype decks)

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest
tests.test_rpg17
"""
import json
import os
import unittest
from unittest import mock

import rpg_screens as rs
from rpg import adventure as adv
from rpg import clock, daynight, duel, explore, hints, rare, talk
from tests import test_rpg8 as r8
from tests.test_review_rpg16b import WINDOWS, at_window
from tests.test_rpg16 import RareTrigger, at, explorer_at, place, save

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
HESK_SQUARE, HESK_HOME = "npc:elder@17,16", "npc:elder@32,21"
PELL_DAY, PELL_LAMPS = "npc:lamplighter@23,12", "npc:lamplighter@22,18"
WENNA = "npc:mourner@20,15"
GATE_GUARD = "npc:gate_guard@35,15"
FRAME = 1 / 60
OLD_RARES = {"old_gorsehide": (0.25, 3, {"gold": 40}), "red_sash": (0.3, 3, {"card": "rare_from_band"}),
             "mother_of_rats": (0.2, 3, {"gold": 60})}       # rpg/data/rare_spawns.json before RPG17 (merge pack b5e4d94)


def mover_of(ex, nid):
    return next((m for m in ex.movers.values() if m.person["id"] == nid), None)


def let_them_walk(ex, seconds=60.0, dt=FRAME):
    """Frames pass with the walker standing still; returns the walking people's positions, frame by frame."""
    trail = []
    for _ in range(int(seconds / dt)):
        if not ex.movers:
            break
        trail.append({g: tuple(m.pos) for g, m in ex.movers.items()})
        ex.step(0, 0, dt)
    return trail


# ---- 1: S1 (b) --------------------------------------------------------------------------------------------------------------------

class WalkBetweenPlacesTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def night_falls_in_town(self, walker=(37, 17), w=1920, h=1080, hour=21):
        ex = at_window(place(explorer_at(TOWN, at(1, 19, 59)), walker), w, h)
        ex.save.world["clock"] = at(1, hour)
        ex.refresh()
        return ex

    def test_elder_hesk_walks_home_at_night_in_sight_and_arrives(self):
        for w, h in WINDOWS:
            with self.subTest(window=(w, h)):
                ex = self.night_falls_in_town(w=w, h=h)
                m = mover_of(ex, "elder")
                self.assertIsNotNone(m, "21:00 with the square in sight: Hesk doesn't walk (before RPG17 he waited for ever)")
                self.assertEqual((m.route[0], m.route[-1]), ((17, 16), (32, 21)), "he walks from the square to his house")
                self.assertFalse(ex.is_out(HESK_SQUARE) or ex.is_out(HESK_HOME), "while he walks he is at neither place")
                trail = let_them_walk(ex)
                self.assertTrue(ex.is_out(HESK_HOME), "he never got home")
                self.assertFalse(ex.is_out(HESK_SQUARE))
                g = ("group", "elder")
                path = [t[g] for t in trail if g in t]
                self.assertGreater(len(path), 30, "not a walk: he got there in a few frames")
                step = explore.PERSON_TILES_PER_SECOND * FRAME + 1e-9
                for a, b in zip(path, path[1:]):
                    self.assertLessEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), step, f"a jump from {a} to {b} (a teleport)")

    def test_wenna_walks_on_from_out_of_sight_to_the_well(self):
        ex = self.night_falls_in_town()
        m = mover_of(ex, "mourner")
        self.assertIsNotNone(m, "Wenna doesn't come to the well while the well is in sight")
        self.assertFalse(daynight.in_view(m.route[0], ex.view_box()), "she appears in sight instead of walking on")
        self.assertEqual(m.route[-1], (20, 15))
        let_them_walk(ex)
        self.assertTrue(ex.is_out(WENNA))

    def test_the_gate_guard_walks_off_out_of_sight_and_is_gone(self):
        ex = self.night_falls_in_town()
        m = mover_of(ex, "gate_guard")
        self.assertIsNotNone(m)
        self.assertEqual(m.route[0], (35, 15))
        self.assertFalse(daynight.in_view(m.route[-1], ex.view_box()), "he vanishes in sight instead of walking off")
        let_them_walk(ex)
        self.assertFalse(ex.is_out(GATE_GUARD))
        self.assertIsNone(mover_of(ex, "gate_guard"))

    def test_pell_walks_to_the_lamps_at_dusk(self):
        ex = at_window(place(explorer_at(TOWN, at(1, 17, 59)), (37, 17)))
        ex.save.world["clock"] = at(1, 18, 30)
        ex.refresh()
        self.assertIsNotNone(mover_of(ex, "lamplighter"))
        let_them_walk(ex)
        self.assertTrue(ex.is_out(PELL_LAMPS))
        self.assertFalse(ex.is_out(PELL_DAY))

    def test_a_walking_person_waits_for_the_walker_and_never_walls_it_in(self):
        ex = self.night_falls_in_town()
        m = mover_of(ex, "elder")
        nxt = m.route[m.leg]
        a = ex.area
        ex.walker.x, ex.walker.y = (nxt[0] + 0.5) * a.tw, (nxt[1] + 0.5) * a.th      # the walker on the next tile of his way
        before = tuple(m.pos)
        for _ in range(30):
            ex.step_movers(FRAME)
        self.assertEqual(tuple(m.pos), before, "he walked onto the walker")
        ex.refresh()
        self.assertNotIn(nxt, ex.walker.blockers, "the walker's own tile is a wall")
        self.assertIn(m.tile(), ex.walker.blockers, "a walking person is in the way where they stand")

    def test_the_people_walking_are_drawn_where_they_are(self):
        ex = self.night_falls_in_town()
        drawn = ex.walking_people()
        self.assertTrue(drawn)
        for p in drawn:
            self.assertTrue(p["walking"])
            self.assertEqual(len(p["pos"]), 2)
        import pygame
        import rpg_map_view
        pygame.init()
        view = rpg_map_view.MapView.__new__(rpg_map_view.MapView)
        view.area = ex.area
        blits = []

        class Sprites:
            def person(self, cell, now, zoom):
                return "img"

        class Screen:
            def blit(self, img, xy):
                blits.append(xy)
        view.sprites = Sprites()
        cam = explore.Camera(1920, 1080, 1)
        p = dict(drawn[0], pos=(3.5, 4.25))
        view.draw_person(Screen(), cam, p, 0.0)
        self.assertEqual(blits[-1], cam.to_screen(round(3.5 * ex.area.tw), round(4.25 * ex.area.th)))

    def test_a_forced_update_puts_everyone_in_place_at_once(self):
        ex = self.night_falls_in_town()
        self.assertTrue(ex.movers)
        ex.refresh(force=True)
        self.assertEqual(ex.movers, {})
        self.assertTrue(ex.is_out(HESK_HOME))

    def test_a_load_on_hesks_doorstep_keeps_him_in_the_square_until_the_walker_steps_off(self):
        ex = place(explorer_at(TOWN, at(1, 12)), (32, 21))
        ex.save.world["clock"] = at(1, 22)
        ex.store()
        loaded = explore.Explorer(ex.save)
        self.assertTrue(loaded.is_out(HESK_SQUARE), "nowhere after the load (review S5)")
        a = loaded.area
        loaded.walker.x, loaded.walker.y = (37 + 0.5) * a.tw, (17 + 0.5) * a.th     # off his doorstep
        loaded.refresh()
        let_them_walk(loaded)
        self.assertTrue(loaded.is_out(HESK_HOME))

    def test_foes_still_come_and_go_out_of_sight_only(self):
        """Only people walk (a group with an id); a night foe's spot in sight waits, as before."""
        ex = place(explorer_at(OUT, at(1, 19, 59)), (20, 20))
        ex.save.world["clock"] = at(1, 20, 1)
        ex.refresh()
        self.assertEqual(ex.movers, {})


# ---- 2, 5: the clock ------------------------------------------------------------------------------------------------------------

class ClockTests(unittest.TestCase):
    def test_a_new_game_starts_at_seven(self):
        self.assertEqual(clock.START, 7 * 60)
        s = save()
        self.assertEqual(clock.now(s.world), 7 * 60)
        self.assertEqual(clock.phase(clock.now(s.world)), "day")

    def test_the_clock_names_the_day(self):
        self.assertEqual(clock.stamp(at(2, 21)), "Day 2, Night 21:00")
        self.assertEqual(clock.stamp(at(1, 7)), "Day 1, Day 07:00")
        self.assertEqual(clock.stamp(at(3, 18, 5)), "Day 3, Dusk 18:05")
        self.assertEqual(rs.time_text({"clock": at(2, 21)}), "Day 2, Night 21:00")
        self.assertEqual(rs.time_text({}), "Day 1, Day 07:00")

    def test_rest_until_dusk_goes_on_to_the_next_six_in_the_evening(self):
        for now, to in ((at(1, 10), at(1, 18)), (at(1, 7), at(1, 18)), (at(1, 18), at(2, 18)), (at(2, 23), at(3, 18))):
            with self.subTest(now=clock.stamp(now)):
                w = {"clock": now}
                self.assertEqual(clock.to_dusk(w), to)
                self.assertEqual(w["clock"], to)


# ---- 3: the inn -----------------------------------------------------------------------------------------------------------------

class InnTests(unittest.TestCase):
    def test_by_day_the_innkeeper_offers_a_rest_until_dusk(self):
        c = talk.Conversation(talk.INNKEEPER, {"clock": at(1, 12)})
        self.assertEqual(c.choice, (talk.REST_DUSK, talk.REST_NO))
        self.assertEqual((c.rest_until, c.choice_default), ("dusk", 1))
        self.assertEqual(c.answer(True), talk.inn()["rest"])
        self.assertTrue(c.rest)

    def test_at_dusk_night_and_dawn_she_offers_sleep_until_morning(self):
        for hour in (18, 21, 2, 5):
            with self.subTest(hour=hour):
                c = talk.Conversation(talk.INNKEEPER, {"clock": at(1, hour)})
                self.assertEqual(c.choice, (talk.REST_YES, talk.REST_NO))
                self.assertEqual((c.rest_until, c.choice_default), ("morning", 0))

    def test_the_inns_night_hint_names_both_rests_and_the_hints_check(self):
        w = {"clock": at(1, 22), "flags": ["met_elder", "met_lamplighter"], "quests": {"the_dark_hall": {"state": "done"},
                                                         "lamplighters_debt": {"state": "done"}}}
        hid, pages = hints.hint_for(w, TOWN, at(1, 22))
        self.assertEqual(hid, "night_inn")
        text = " ".join(pages)
        self.assertIn("until morning", text)
        self.assertIn("rest until dusk", text)
        self.assertEqual(hints.check(), [])

    def test_hesk_and_pells_night_hints_are_kept(self):
        """RPG16b R8's words, approved as drafted (Karl 24 Oct)."""
        with open(os.path.join(BASE, "rpg", "data", "hints.json"), encoding="utf-8") as fh:
            ids = {h["id"]: h for h in json.load(fh)["hints"]}
        self.assertIn("At night he is at home, the house east of the duel hall.", ids["hesk_offer_night"]["say"][0])
        self.assertIn("From dusk he lights the lamps by the duel hall.", ids["pell_offer_dusk"]["say"][0])


# ---- 4, 8: the rares ------------------------------------------------------------------------------------------------------------

class RareTests(r8.Base):
    def test_chances_about_doubled_cooldown_two_days_rewards_kept(self):
        rare.forget()
        e = rare.spawns()
        for rid, (chance, _cd, reward) in OLD_RARES.items():
            with self.subTest(rare=rid):
                self.assertTrue(0.4 <= e[rid]["chance"] <= 0.6, e[rid]["chance"])
                self.assertAlmostEqual(e[rid]["chance"], 2 * chance)
                self.assertEqual(e[rid]["cooldown_days"], 2)
                self.assertEqual(e[rid]["reward"], reward)
                self.assertEqual(e[rid]["encounter"], "dungeon", "the pay is a dungeon win's, as before")
        self.assertEqual(rare.check(), [])

    def test_a_rare_is_out_about_half_its_phases(self):
        s = save("rpg17-rare")
        s.world.update(clock=at(1, 12))
        out = 0
        n = 400
        for day in range(n):
            if rare.is_out(s, "old_gorsehide", at(day + 1, 18, 30)):
                out += 1
        self.assertTrue(0.42 <= out / n <= 0.58, f"out {out} of {n} dusks")

    def test_beaten_it_comes_back_after_two_days(self):
        s = save("rpg17-cool")
        rare.on_beaten(s.world, "old_gorsehide", at(1, 18, 30))
        with mock.patch.object(rare, "roll", lambda *a: 0.0):
            self.assertFalse(rare.is_out(s, "old_gorsehide", at(2, 18, 30)))
            self.assertFalse(rare.is_out(s, "old_gorsehide", at(3, 18, 29)))
            self.assertTrue(rare.is_out(s, "old_gorsehide", at(3, 18, 30)))

    def test_old_gorsehide_plays_the_regular_jumpstart_decks_and_the_others_their_own(self):
        g = r8.new_game(None)
        self.assertEqual(rare.deck_encounter("old_gorsehide", rare.spawns()["old_gorsehide"]), "regular")
        self.assertEqual(adv.deck_source(g, RareTrigger("old_gorsehide")), "jumpstart")
        self.assertIsNone(adv.deck_source(g, RareTrigger("red_sash")))
        self.assertIsNone(adv.deck_source(g, RareTrigger("mother_of_rats")))
        d = adv.start_duel(g, RareTrigger("old_gorsehide"))
        self.assertEqual(g.save.pending_duel["source"], "jumpstart")
        self.assertEqual(g.save.pending_duel["encounter"], "dungeon", "his pay stays a dungeon win's")
        self.assertEqual(d.enemy_name, "Old Gorsehide")

    def test_the_encounter_screen_shows_the_deck_he_will_play(self):
        import pygame
        import rpg_encounter as renc
        pygame.init()
        pygame.display.set_mode((64, 64))
        g = r8.new_game(None)
        scr = renc.EncounterScreen(g, RareTrigger("old_gorsehide"), 0.0, animations=False)
        i, deck = duel.peek(g.save, "thornback", "dungeon", source="jumpstart")
        self.assertEqual((scr.deck_index, scr.deck), (i, deck))
        d = adv.start_duel(g, RareTrigger("old_gorsehide"), index=scr.deck_index)
        self.assertEqual(g.save.pending_duel["deck"], i)
        self.assertEqual(d.enemy_deck, deck)

    def test_a_decks_word_that_isnt_an_encounter_is_refused_and_said(self):
        bad = {"kind": "thornback", "name": "X", "encounter": "dungeon", "decks": "boss", "when": [], "chance": 0.5,
               "cooldown_days": 2, "home": "h", "taunt": "t", "seen": "s", "reward": {"gold": 1}}
        self.assertTrue(any("decks" in p for p in rare.check({"x": bad})))
        with mock.patch.object(rare.notes, "once") as once:
            self.assertEqual(rare.deck_encounter("x", bad), "dungeon")
        once.assert_called_once()


# ---- 6: the Warrens ------------------------------------------------------------------------------------------------------------

class WarrensTests(unittest.TestCase):
    def test_the_warrens_rate_is_100_in_the_source_and_the_built_map(self):
        explore.forget_areas()
        for name in ("warrens_1", "warrens_2"):
            with self.subTest(map=name):
                self.assertEqual(explore.load_area(name).fight_rate, 100)
                with open(os.path.join(BASE, "rpg", "maps", "src", name + ".txt"), encoding="utf-8") as fh:
                    self.assertIn("encounter_rate: 100\n", fh.read())
        self.assertIsNone(explore.load_area(OUT).fight_rate)


# ---- 7: a loss keeps the clock ----------------------------------------------------------------------------------------------------

class LossTests(r8.Base):
    def test_a_lost_duel_wakes_you_at_the_inn_at_the_same_hour(self):
        g = r8.new_game(None)
        w = g.save.world
        w["inn"] = {"map": TOWN, "spawn": explore.INN_SPAWN}
        w["clock"] = at(2, 21, 30)
        g.save.pending_duel = {"kind": "thornback", "encounter": "regular", "deck": 0, "source": "jumpstart"}
        out = duel.finish(g.save, "lost", g.numbers, g.world)
        self.assertTrue(out["inn"])
        self.assertEqual(w["map"], TOWN)
        self.assertEqual(clock.now(w), at(2, 21, 30), "the loss moved the clock (RPG15 went on to 07:00)")
        self.assertEqual(clock.phase(clock.now(w)), "night")


if __name__ == "__main__":
    unittest.main()
