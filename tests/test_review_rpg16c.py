# SPDX-License-Identifier: GPL-3.0-or-later
"""A cold review of Round RPG16c (the fixes to the RPG16b review), 24 Oct 2026: one failing test per bug found, and guard tests
that pass and prove the harness can see the behaviour. Nothing here changes the program.

  T1  a rare's chance or cooldown that isn't a number in rare_spawns.json ("25%") raises out of every refresh once its hour
      comes: the walk errors every frame and the table closes after MAX_BAD_FRAMES (rpg.rare.is_out's float(); nothing catches it)
  T2  an event whose flag isn't a word (a list) puts an unhashable value in save.world["flags"]: every refresh raises from then
      on, the save is written that way, and the map can't be opened again - even after events.json is mended
  T3  an event whose "gold" is negative (a toll) takes the save below 0 gold; the save is written, and can't be loaded again
      ("The save file is damaged (gold)")
  T4  S3's guard (Explorer._event) still lets IndexError out: an event whose "where" is [] or [col, row] errors the walk at
      every check
  T5  an odd event answer (a "yes" that is a sentence, an "effect" that is a number) raises out of the event's box:
      _event_effects catches only OSError/ValueError/KeyError, and pages_of(e, answer) is outside it
  T6  N5's foes keep off a person only while they walk: a person who comes out (out of view) on the tile a foe stands on -
      a foe still or woken within ACTIVE_TILES but off screen - leaves the foe on top of them, and every step it tries is then
      refused, so it never moves again while they are there
  T7  N3's joined line is replaced on the very next frame when the event's own "minutes" took the clock into a new phase: the
      phase note ("Night falls.") overwrites the map's one message line, so "+5 gold." is never seen
  T8  S2's feet set has no EPS on its low edges, unlike Walker._blocked: a walker left within 1e-6 px of a person's right
      (or lower) edge has that person's tile in its feet, the tile is dropped from the blockers, and it walks into them
  T9  clock.pass_minutes: a finite n whose sum overflows still sends the clock back (S4's fix checks n, not the sum), and a
      whole number too big for a float raises OverflowError (not caught by its own TypeError/ValueError guard)
  T10 save.world["danger"] = Infinity (a save Python's json reads) ends random fights for good: _random_fight takes any
      number that isn't negative, and inf - steps is inf

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest
tests.test_review_rpg16c
"""
import copy
import math
import types
import unittest
from unittest import mock

from rpg import clock, events, explore, talk
from rpg import rare as rpg_rare
from rpg import save as sv
from tests.test_rpg16 import _window_base, at, explorer_at, place, press

TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
GORSEHIDE = "ashfen_outskirts#rare:old_gorsehide"
BANDIT = "ashfen_outskirts#10"                   # an ember bandit that wanders the west road, home (13, 22)
EPS = explore.EPS


def rare_table(**changes):
    """rare_spawns.json's table with old_gorsehide's fields changed (a hand edit), and its chance 1 so it always comes out."""
    t = copy.deepcopy(rpg_rare.spawns())
    t["old_gorsehide"].update(dict(dict(chance=1.0), **changes))
    return t


def events_table(**entries):
    t = copy.deepcopy(events.events())
    t.update(entries)
    return t


def fake_game(save):
    return types.SimpleNamespace(save=save)


def with_person(ex, pid, tile, when=("phase:night",)):
    """The map the explorer is on, with one more person who comes and goes (a copy: the cached Area is left alone)."""
    a = copy.copy(ex.area)
    a.people = list(a.people) + [{"id": pid, "name": pid, "tile": tile, "when": list(when), "cell": None,
                                  "key": f"npc:{pid}@{tile[0]},{tile[1]}"}]
    return a


def box_tiles(a, box):
    """The tiles a box overlaps by more than EPS (the walls' own rule, Walker._blocked)."""
    x0, y0, x1, y1 = box
    return {(c, r) for r in range(int(math.floor((y0 + EPS) / a.th)), int(math.floor((y1 - EPS) / a.th)) + 1)
            for c in range(int(math.floor((x0 + EPS) / a.tw)), int(math.floor((x1 - EPS) / a.tw)) + 1)}


class GuardTests(unittest.TestCase):
    """These pass: the harness sees the rare come out, an event's flag land, a foe move, and a person wall the walker."""

    def test_the_rare_comes_out_at_dusk_with_a_good_table(self):
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table()):
            ex = place(explorer_at(OUT, at(1, 17)), (21, 20))
            self.assertFalse(ex.is_out(GORSEHIDE))
            ex.save.world["clock"] = at(1, 18, 30)
            press(ex, (1, 0), 30)
            press(ex, (-1, 0), 30)
            self.assertTrue(ex.is_out(GORSEHIDE), "with a chance of 1 it comes out at dusk (its rocks are out of view)")

    def test_a_word_flag_from_an_event_is_set_and_the_walk_goes_on(self):
        ex = place(explorer_at(OUT, at(1, 22)), (21, 20))                # night: the rats' `when` reads the flags (T2's hour)
        table = events_table(odd={"title": "x", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["x"],
                                  "effect": {"flag": "a_word"}})
        events.apply(fake_game(ex.save), "odd", entries=table)
        self.assertIn("a_word", talk.flags(ex.save.world))
        press(ex, (1, 0), 10)

    def test_a_wandering_foe_moves_when_nobody_stands_where_it_is(self):
        ex = place(explorer_at(OUT, at(1, 12)), (13, 11))            # 11 rows north of the bandit's home: awake, off screen
        f = ex.foes[BANDIT]
        self.assertTrue(f.active, "guard: within ACTIVE_TILES of the walker")
        start = (f.x, f.y)
        for _ in range(240):
            ex.step(0, 0, 1 / 60)
        self.assertNotEqual((f.x, f.y), start, "the foe walks about")

    def test_a_present_person_beside_the_walker_is_a_wall(self):
        ex = place(explorer_at(OUT, at(1, 22)), (21, 20))
        a = with_person(ex, "watcher", (20, 20))
        ex._use(a, explore.Walker(a, 21 * a.tw + ex.walker.hw, 20.5 * a.th))   # flush against the person's right edge
        self.assertTrue(ex.is_out("npc:watcher@20,20"))
        self.assertIn((20, 20), ex.walker.blockers)
        for _ in range(30):
            press(ex, (-1, 0), 1)
            self.assertNotIn((20, 20), box_tiles(a, ex.walker.box()), "a person is a wall")
        self.assertEqual(ex.walker.box()[0], 21 * a.tw, "the walker stays flush against them")


class EventGuardTests(unittest.TestCase):
    """Guard (passes now): an odd event whose "where" is a sentence raises TypeError, which S3's guard catches - so T4's IndexError
    is a gap in the guard, not a harness that can't see the walk survive."""

    def test_an_event_whose_where_is_a_sentence_does_not_crash_the_walk(self):
        events.forget()
        self.addCleanup(events.forget)
        table = {"odd": {"title": "x", "map": OUT, "where": "0,0,59,39", "when": [], "cooldown_days": 1, "pages": ["x"],
                         "effect": {"gold": 1}}}
        with mock.patch.object(events, "events", return_value=table):
            ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
            ex.save.world["events"] = {"gap": 0.5}
            for d in ((1, 0), (-1, 0)):
                press(ex, d, 30)
            self.assertGreater(ex.save.world["events"]["gap"], 50, "the check came up (a new gap was drawn)")


class RareDataTests(unittest.TestCase):
    def test_t1_a_rare_chance_that_is_not_a_number_never_stops_the_walk(self):
        """T1. rare.is_out ends with float(e.get("chance") or 0); "25%" raises ValueError. It runs in daynight.wanted() (which
        catches only ConditionError) at every refresh, so once old_gorsehide's dusk comes every frame raises."""
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(chance="25%")):
            ex = place(explorer_at(OUT, at(1, 17)), (21, 20))       # day: its `when` doesn't hold, so its chance isn't read yet
            ex.save.world["clock"] = at(1, 18, 30)                   # dusk
            try:
                press(ex, (1, 0), 30)
            except Exception as e:                                   # noqa: BLE001 - any exception out of the walk is the bug
                self.fail(f"walking at dusk crashed on a hand-edited chance: {type(e).__name__}: {e}")

    def test_t1_a_rare_cooldown_that_is_not_a_number_never_stops_the_walk(self):
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(cooldown_days="three")):
            ex = place(explorer_at(OUT, at(1, 17)), (21, 20))
            rpg_rare.on_beaten(ex.save.world, "old_gorsehide", at(1, 6))
            ex.save.world["clock"] = at(1, 18, 30)
            try:
                press(ex, (1, 0), 30)
            except Exception as e:                                   # noqa: BLE001
                self.fail(f"walking at dusk crashed on a hand-edited cooldown: {type(e).__name__}: {e}")


class EventSaveTests(unittest.TestCase):
    def test_t2_an_event_flag_that_is_a_list_never_poisons_the_save(self):
        """T2. events.apply passes eff["flag"] to talk.set_flag unchecked; conditions.flags() then does set(world["flags"]),
        which raises TypeError on the list at every refresh at night (the rats' `when` reads a flag once phase:night holds) -
        and the save keeps it, so loading it at night raises too. (rpg_screens.draw_map's talk.flags() does the same set() on
        every drawn frame, by day too.)"""
        ex = place(explorer_at(OUT, at(1, 22)), (21, 20))
        table = events_table(odd={"title": "x", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["x"],
                                  "effect": {"flag": ["heard", "the_bell"]}})
        try:
            events.apply(fake_game(ex.save), "odd", entries=table)
        except Exception:                                            # noqa: BLE001 - refusing the odd flag is a fix too
            pass
        loaded_world = sv.SaveGame.from_dict(ex.save.to_dict()).world
        try:
            press(ex, (1, 0), 10)
            ex.store()
            explore.Explorer(sv.SaveGame.from_dict(ex.save.to_dict()))
        except Exception as e:                                       # noqa: BLE001
            self.fail(f"an event's odd flag broke the walk and the save: {type(e).__name__}: {e} "
                      f"(flags saved: {loaded_world.get('flags')})")

    def test_t3_an_event_that_takes_gold_never_leaves_a_save_that_cannot_load(self):
        """T3. adventure.give_reward adds int(gold) whatever its sign; a toll of 50 on 10 gold leaves -40, the save writes it,
        and SaveGame.from_dict refuses gold below 0."""
        ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
        ex.save.gold = 10
        table = events_table(toll={"title": "A toll", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["Pay."],
                                   "effect": {"gold": -50}})
        try:
            events.apply(fake_game(ex.save), "toll", entries=table)
        except Exception:                                            # noqa: BLE001 - refusing the toll is a fix too
            pass
        try:
            sv.SaveGame.from_dict(ex.save.to_dict())
        except sv.SaveError as e:
            self.fail(f"the event left {ex.save.gold} gold and the save can't be loaded: {e}")


class EventWalkTests(unittest.TestCase):
    def walk_with(self, table):
        events.forget()
        self.addCleanup(events.forget)
        with mock.patch.object(events, "events", return_value=table):
            ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
            ex.save.world["events"] = {"gap": 0.5}
            try:
                for d in ((1, 0), (-1, 0)):
                    press(ex, d, 30)
            except Exception as e:                                   # noqa: BLE001 - any exception out of the walk is the bug
                self.fail(f"walking crashed on an odd event: {type(e).__name__}: {e}")

    def test_t4_an_event_whose_where_is_empty_does_not_crash_the_walk(self):
        """T4. events.in_box reads box[0..3]; [] (meant as "anywhere") raises IndexError, which Explorer._event doesn't catch."""
        self.walk_with({"odd": {"title": "x", "map": OUT, "where": [], "when": [], "cooldown_days": 1, "pages": ["x"],
                                "effect": {"gold": 1}}})

    def test_t4_an_event_whose_where_is_one_tile_does_not_crash_the_walk(self):
        self.walk_with({"odd": {"title": "x", "map": OUT, "where": [21, 20], "when": [], "cooldown_days": 1, "pages": ["x"],
                                "effect": {"gold": 1}}})


class EventBoxTests(_window_base()):
    def answer(self, g, f, eid, yes=True):
        f.start_event(g, eid)
        try:
            self.say_all(g, answer=yes)
        except AssertionError:                                       # say_all's own "the box never closed"
            raise
        except Exception as e:                                       # noqa: BLE001 - any exception out of the box is the bug
            self.fail(f"the event's box crashed on {eid}: {type(e).__name__}: {e}")

    def test_t5_an_answer_that_is_a_sentence_does_not_crash_the_box(self):
        """T5. effect_of(e, "yes") on a string: "gold" in "Thanks for the gold" is True, then part["gold"] raises TypeError."""
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        table = events_table(odd={"title": "On the road", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["A carter."],
                                  "choice": ["Help", "Walk on"], "yes": "Thanks for the gold", "no": {"pages": ["Fine."]}})
        with mock.patch.object(events, "events", return_value=table):
            self.answer(g, f, "odd")

    def test_t5_an_effect_that_is_a_number_does_not_crash_the_box(self):
        """dict(5) raises TypeError inside _event_effects, which catches OSError, ValueError and KeyError only."""
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        table = events_table(odd={"title": "A bell", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["Dong."],
                                  "effect": 5})
        with mock.patch.object(events, "events", return_value=table):
            self.answer(g, f, "odd")


class FoeOnPersonTests(unittest.TestCase):
    def evening(self, person_tile=None):
        """The walker 11 rows north of the ember bandit's home (13, 22): the bandit is awake (ACTIVE_TILES 12) but out of view
        (the view box holds rows 2-20 here). Three seconds of 19:50 pass; the bandit wanders (its walk is seeded, so it is the
        same walk every time). person_tile: a night person stands there on this copy of the map."""
        ex = place(explorer_at(OUT, at(1, 19, 50)), (13, 11))
        if person_tile is not None:
            a = with_person(ex, "watcher", person_tile)
            ex._use(a, explore.Walker(a, ex.walker.x, ex.walker.y))
        for _ in range(180):
            ex.step(0, 0, 1 / 60)
        return ex

    def test_t6_a_person_who_comes_out_where_a_foe_stands_does_not_freeze_it_on_top_of_them(self):
        """T6. Daynight.Presence keeps a person off the walker's feet, not off a foe: at 20:00 a night person comes out (out of
        view) on the tile the bandit wandered onto. From then on Foe._free (RPG16c, N5) refuses every step the bandit tries,
        because each one still overlaps the person's tile (a frame's step is 0.43 px) - it stands on them while they are there."""
        f0 = self.evening().foes[BANDIT]
        a = explore.load_area(OUT)
        here = a.tile_at(f0.x, f0.y)
        ex = self.evening(here)                                       # the same evening, with someone who comes out there
        f = ex.foes[BANDIT]
        self.assertEqual((f.x, f.y), (f0.x, f0.y), "the setup: the same walk")
        self.assertTrue(f.active and not ex.is_out(f"npc:watcher@{here[0]},{here[1]}"))
        ex.save.world["clock"] = at(1, 20, 1)
        ex.step(0, 0, 1 / 60)
        self.assertTrue(ex.is_out(f"npc:watcher@{here[0]},{here[1]}"), "the setup: the person came out (out of view)")
        seen = set()
        for _ in range(600):                                          # ten seconds
            ex.step(0, 0, 1 / 60)
            seen.add((round(f.x, 3), round(f.y, 3)))
        self.assertFalse(here in box_tiles(ex.area, f.box) and len(seen) == 1,
                         f"the bandit stands frozen on the person at {here} for ten seconds (box "
                         f"{tuple(round(v, 1) for v in f.box)}, every step refused)")


class EventLineTests(_window_base()):
    def test_t7_an_events_lines_are_not_replaced_by_its_own_phase_change(self):
        """T7. A no-choice event that gives gold and passes 90 minutes at 19:00: event_do says "+5 gold." on the message line,
        then the next walk_tick's refresh notes the phase change and say() replaces the line before anyone reads it."""
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        f.game.save.world["clock"] = at(1, 19)
        f.explorer.refresh(force=True)
        table = events_table(found={"title": "Something shines", "map": OUT, "when": [], "cooldown_days": 1,
                                    "pages": ["A coin in the mud."], "effect": {"gold": 5, "minutes": 90}})
        with mock.patch.object(events, "events", return_value=table):
            f.start_event(g, "found")
            self.say_all(g)
        self.assertIn("+5 gold.", (f.message or ("",))[0], "the setup: the effect is said")
        f.walk_tick(g, 100.0, pressed=(0, 0))
        f.walk_tick(g, 100.0 + 1 / 60, pressed=(0, 0))
        self.assertIn("+5 gold.", (f.message or ("",))[0], f"the gold line was replaced at once by {f.message!r}")


class FeetEdgeTests(unittest.TestCase):
    def test_t8_a_walker_a_hair_inside_a_persons_edge_still_cannot_walk_into_them(self):
        """T8. Walker._blocked allows an overlap of up to EPS (floor((x0 + EPS) / tw)), so one step of 1.0000005 px from x0 = 337
        toward a person on (20, 20) ends with x0 = 335.9999995 - allowed. refresh's feet (S2) use int(x0 // tw), so that box counts
        (20, 20) as under the feet, drops it from the blockers, and the next steps left walk into the person."""
        ex = place(explorer_at(OUT, at(1, 22)), (21, 20))
        a = with_person(ex, "watcher", (20, 20))
        ex._use(a, explore.Walker(a, 337 + ex.walker.hw, 20.5 * a.th))    # x0 = 337: one pixel east of the person's tile
        self.assertTrue(ex.is_out("npc:watcher@20,20"), "the setup: the person is out")
        self.assertIn((20, 20), ex.walker.blockers)
        ex.step(-1, 0, (1 + 5e-7) / ex.walker.speed)                       # one step of 1.0000005 px (the walker's own rules)
        self.assertAlmostEqual(ex.walker.box()[0], 336 - 5e-7, places=9, msg="the setup: a hair over the edge")
        inside = []
        for _ in range(30):                                                 # half a second, a frame at a time
            press(ex, (-1, 0), 1)
            if (20, 20) in box_tiles(a, ex.walker.box()):
                inside.append(round(ex.walker.box()[0], 2))
        self.assertEqual(inside, [], f"walked into the person: the box's left edge at {inside[:3]}... (blockers "
                                     f"{sorted(ex.walker.blockers)})")


class DangerTests(unittest.TestCase):
    def walk_warrens(self, danger):
        ex = explorer_at("warrens_1", at(1, 12))
        a = ex.area
        ex._use(a, explore.Walker(a, *a.spawn("entrance")))
        if danger is not None:
            ex.save.world["danger"] = danger
        start = ex.walker.walked
        for _ in range(40):                     # Round RPG17 (expected change): the rate is 100 now, a gap at most 188 tiles
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if any(e[0] == "random" for e in press(ex, d, 40)):
                    return (ex.walker.walked - start) / a.tw, ex
        return None, ex

    def test_a_fight_comes_within_the_gap_on_a_fresh_save(self):
        """Guard (passes now): the same walk on a save with no danger meets a random fight."""
        tiles, _ex = self.walk_warrens(None)
        self.assertIsNotNone(tiles)

    def test_t10_a_saved_danger_of_infinity_never_ends_random_fights(self):
        """T10. Explorer._random_fight accepts any number that isn't negative: Infinity (Python's json reads it, as R9 found for
        the clock) minus the tiles walked stays Infinity, is written back, and no random fight ever comes again (the most a
        gap can be on warrens_1 is 2 x 50 - MIN_GAP tiles; this walk is over 150)."""
        tiles, ex = self.walk_warrens(float("inf"))
        self.assertIsNotNone(tiles, f"no random fight in {(ex.walker.walked) / ex.area.tw:.0f} tiles walked (danger "
                                    f"{ex.save.world.get('danger')})")


class ClockTests(unittest.TestCase):
    def test_t9_a_sum_that_overflows_never_sends_the_clock_back(self):
        """T9. S4 checks that n is finite; 1.5e308 is, but 1.5e308 + 1.5e308 is inf, which clock.now reads as 17:00 day 1."""
        w = {"clock": at(5, 12)}
        clock.pass_minutes(w, 1.5e308)
        before = clock.now(w)
        clock.pass_minutes(w, 1.5e308)
        self.assertGreaterEqual(clock.now(w), before, f"the clock went back to {clock.now(w)}")

    def test_t9_a_whole_number_too_big_for_a_float_is_refused_not_raised(self):
        """json reads 1 followed by 400 zeros as a Python int; float() of it raises OverflowError, not ValueError."""
        w = {"clock": at(5, 12)}
        try:
            clock.pass_minutes(w, 10 ** 400)
        except OverflowError as e:
            self.fail(f"pass_minutes raised {type(e).__name__}: {e}")


if __name__ == "__main__":
    unittest.main()
