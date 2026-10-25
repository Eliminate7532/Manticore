# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG16d: the RPG16c review's T1-T10 (claude/REVIEW_RPG16c_2026-10-24.md; its own tests are tests/test_review_rpg16c.py).
These are the parts its tests don't reach:

  - a value the game can't use in rare_spawns.json or events.json is read by the check's rules, said ONCE in crash_log.txt, and
    the other entries go on (one odd event used to stop every event on the map);
  - a save an older copy wrote with a flag that isn't a word, a danger of Infinity / NaN or gold below 0 still loads, mended, and
    says so in crash_log.txt;
  - a foe a person came out on walks off them, but never onto anyone new (T6);
  - an event whose choice isn't two words is said without a choice (the box measures each answer with len());
  - a schedule's test that raises keeps the thing away instead of raising out of every refresh.
"""
import copy
import json
import math
import os
import shutil
import tempfile
import unittest
from unittest import mock

from rpg import adventure as adv
from rpg import clock, daynight, events, explore, notes, talk
from rpg import rare as rpg_rare
from rpg import save as sv
from tests import test_rpg8 as r8
from tests.test_rpg16 import RareTrigger, _window_base, at, explorer_at, place, press, save

OUT = "ashfen_outskirts"
GORSEHIDE = "ashfen_outskirts#rare:old_gorsehide"


class FreshNotes:
    """Each test starts with nothing said yet this run (rpg.notes says a thing once a run) and leaves the set as it was."""

    def setUp(self):
        super().setUp()
        kept = set(notes.NOTED)
        notes.NOTED.clear()
        self.addCleanup(lambda: (notes.NOTED.clear(), notes.NOTED.update(kept)))


def rare_table(**changes):
    t = copy.deepcopy(rpg_rare.spawns())
    t["old_gorsehide"].update(dict(dict(chance=1.0), **changes))
    return t


def said(note, *words):
    """The crash-log lines (crashlog.note's first argument) that hold every one of `words`."""
    return [c.args[0] for c in note.call_args_list if all(w in c.args[0] for w in words)]


class RareFieldTests(FreshNotes, unittest.TestCase):
    def test_chance_and_cooldown_by_the_checks_rules(self):
        for v, want in ((0.25, 0.25), (1, 1.0), (2, 1.0), (math.inf, 1.0), (0, 0.0), (-1, 0.0), ("25%", 0.0), ([0.25], 0.0),
                        (True, 0.0), (math.nan, 0.0), (None, 0.0)):
            with self.subTest(chance=v):
                self.assertEqual(rpg_rare.chance_of("x", {"chance": v}), want)
        # Round RPG17b (Karl 24 Oct, RPG16d's question 4): one that can't be read is the level's days - no level here, so 1 day
        for v, want in ((3, 3.0), (0, 0.0), (math.inf, math.inf), (-1, 0.0), ("three", 1.0), (math.nan, 1.0), (None, 1.0)):
            with self.subTest(cooldown=v):
                self.assertEqual(rpg_rare.cooldown_of("x", {"cooldown_days": v}), want)

    def test_a_hand_edited_chance_is_said_once_however_many_frames(self):
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(chance="25%")), mock.patch("crashlog.note") as note:
            ex = place(explorer_at(OUT, at(1, 18, 30)), (21, 20))
            for d in ((1, 0), (-1, 0), (1, 0)):
                press(ex, d, 30)
            self.assertFalse(ex.is_out(GORSEHIDE), "a chance that isn't a number: it never comes out")
        self.assertEqual(len(said(note, "rare_spawns.json", "old_gorsehide", "chance")), 1, note.call_args_list)

    def test_a_cooldown_that_is_not_a_number_is_its_level_in_days(self):
        """Round RPG17b (Karl 24 Oct, RPG16d's question 4): RPG16d read it as 0 (back the same day's dusk); now Old Gorsehide's
        level 3 is 3 days."""
        s = save()
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(cooldown_days="three")):
            rpg_rare.on_beaten(s.world, "old_gorsehide", at(1, 6))
            self.assertFalse(rpg_rare.is_out(s, "old_gorsehide", at(1, 18, 30)), "level 3: not back the same day's dusk")
            self.assertTrue(rpg_rare.is_out(s, "old_gorsehide", at(4, 18, 30)), "level 3: back after 3 days")
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(cooldown_days=3)):
            self.assertFalse(rpg_rare.is_out(s, "old_gorsehide", at(1, 18, 30)), "guard: with 3 days it stays away")

    def test_the_fight_question_with_a_hand_edited_cooldown(self):
        """The question before the fight formatted the cooldown with :g - "three" raised there too (the same field as T1)."""
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(cooldown_days="three")):
            _title, lines = adv.fight_text(r8.new_game(None), RareTrigger("old_gorsehide"))
        self.assertIn("Beaten, it stays away for 3 days.", lines)          # Round RPG17b: Old Gorsehide's level 3

    def test_a_rare_when_that_is_not_a_list_never_stops_the_map(self):
        explore.forget_areas()
        self.addCleanup(explore.forget_areas)
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(when=5)):
            ex = place(explorer_at(OUT, at(1, 18, 30)), (21, 20))
            press(ex, (1, 0), 30)
            self.assertFalse(ex.is_out(GORSEHIDE))
        explore.forget_areas()
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(when="phase:dusk|dawn")):
            ex = place(explorer_at(OUT, at(1, 18, 30)), (21, 20))
            press(ex, (1, 0), 30)
            press(ex, (-1, 0), 30)
            self.assertTrue(ex.is_out(GORSEHIDE), "one word in place of a list reads as the map's words do")


class EventFieldTests(FreshNotes, unittest.TestCase):
    def test_an_effect_is_given_by_the_checks_rules(self):
        cases = [({"flag": ["heard", "the_bell"]}, {}), ({"flag": ""}, {}), ({"flag": "a_word"}, {"flag": "a_word"}),
                 ({"gold": -50}, {}), ({"gold": 0}, {}), ({"gold": 5.5}, {}), ({"gold": True}, {}), ({"gold": "5"}, {}),
                 ({"gold": 5}, {"gold": 5}), ({"card": "a_mythic"}, {}), ({"card": "rare_from_band"}, {"card": "rare_from_band"}),
                 ({"minutes": "90"}, {}), ({"minutes": math.nan}, {}), ({"minutes": math.inf}, {}), ({"minutes": -5}, {}),
                 ({"minutes": 90}, {"minutes": 90}), ({"minutes": 3 * clock.DAY}, {"minutes": clock.DAY})]
        with mock.patch("crashlog.note") as note:
            for i, (eff, want) in enumerate(cases):
                with self.subTest(effect=eff):
                    self.assertEqual(events.usable_effect(f"e{i}", eff), want)
        refused = [eff for eff, want in cases if eff != want]
        self.assertEqual(note.call_count, len(refused), "every value the check would refuse is said, and only those")

    def test_a_toll_and_a_list_flag_change_nothing_in_the_save(self):
        g = type("G", (), {})()
        g.save = save()
        g.save.gold = 10
        g.save.world.update(clock=at(1, 12), flags=["met_elder"])
        table = {"toll": {"title": "A toll", "map": OUT, "effect": {"gold": -50, "flag": ["x", "y"], "minutes": 3000}}}
        with mock.patch("crashlog.note"):
            self.assertEqual(events.apply(g, "toll", entries=table), [])
        self.assertEqual((g.save.gold, g.save.world["flags"]), (10, ["met_elder"]))
        self.assertEqual(clock.now(g.save.world), at(1, 12) + clock.DAY, "minutes over a day: a day")
        sv.SaveGame.from_dict(json.loads(json.dumps(g.save.to_dict())))

    def test_set_flag_takes_only_words(self):
        w = {"flags": ["a"]}
        for bad in (["b"], 5, None, "", {"c": 1}):
            talk.set_flag(w, bad)
        talk.set_flag(w, "d")
        self.assertEqual(w["flags"], ["a", "d"])
        w = {"flags": "not a list"}
        talk.set_flag(w, "e")
        self.assertEqual(w["flags"], ["e"])

    def test_the_flags_readers_skip_what_isnt_a_word(self):
        w = {"flags": ["a", ["b", "c"], 5, {"d": 1}]}
        self.assertEqual(talk.flags(w), {"a"})
        self.assertTrue(talk.holds("flag:a", w))

    def test_an_odd_entry_never_hides_a_good_one(self):
        good = {"title": "x", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["x"], "effect": {"gold": 1}}
        table = {"empty_where": dict(good, where=[]), "one_tile": dict(good, where=[21, 20]), "words": dict(good, where="0,0,9,9"),
                 "when_number": dict(good, when=5), "when_typo": dict(good, when=["phase:nite"]), "a_list": ["not", "a", "table"],
                 "cooldown_word": dict(good, cooldown_days="three"), "weight_word": dict(good, weight="heavy"),
                 "inside_out": dict(good, where=[30, 30, 10, 10]), "good": good}
        s = save()
        s.world["events"] = {"last": {"cooldown_word": at(1, 11)}}           # met an hour ago: its cooldown is read
        with mock.patch("crashlog.note") as note:
            ids = events.eligible(s.world, OUT, (21, 20), minutes=at(1, 12), entries=table)
            # Round RPG17b (Karl 24 Oct, RPG16d's question 3): a `where` that can't be read is anywhere on the map, so the four
            # odd boxes are in too (RPG16d had them never happen)
            self.assertEqual(ids, ["empty_where", "one_tile", "words", "cooldown_word", "weight_word", "inside_out", "good"])
            self.assertIn(events.pick(s, ids, entries=table), ids)
            events.eligible(s.world, OUT, (21, 20), minutes=at(1, 12), entries=table)
        for eid, field in (("empty_where", "where"), ("one_tile", "where"), ("words", "where"), ("when_number", "when"),
                           ("when_typo", "nite"), ("a_list", "not a table"), ("cooldown_word", "cooldown_days"),
                           ("weight_word", "weight"), ("inside_out", "where")):
            self.assertEqual(len(said(note, f"{eid}")), 1, f"{eid}: said once over two checks ({note.call_args_list})")
            self.assertTrue(said(note, f"{eid}", field), f"{eid}: the note names what is wrong ({field})")
        self.assertFalse(said(note, "good"))
        self.assertFalse(said(note, "can't be read"), "every one of these has its own rule (the catch-all is for the unforeseen)")


    def test_the_catch_all_holds_what_no_rule_foresaw(self):
        """Found by planting P20: box_ok stops every IndexError it knows of, so the per-entry catch-all was never reached. Here a
        rule that lets [] through stands in for whatever no rule foresees - T4's own IndexError must still never reach the walk."""
        good = {"title": "x", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["x"], "effect": {"gold": 1}}
        table = {"empty_where": dict(good, where=[]), "good": good}
        with mock.patch.object(events, "box_ok", return_value=True), mock.patch("crashlog.note") as note:
            self.assertEqual(events.eligible(save().world, OUT, (21, 20), minutes=at(1, 12), entries=table), ["good"])
        self.assertEqual(len(said(note, "empty_where", "can't be read", "IndexError")), 1)


class SaveRepairTests(FreshNotes, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def written(self, **world):
        """A save on the outskirts at night, written as RPG16c would have written it (with `world` changed), read back."""
        s = save()
        s.world.update(map=OUT, clock=at(1, 22), **world)
        sv.write(s, self.tmp)
        return sv.read(1, self.tmp)

    def test_a_flag_that_isnt_a_word_is_dropped_and_the_walk_goes_on_at_night(self):
        with mock.patch("crashlog.note") as note:
            back, problem = self.written(flags=["met_elder", ["heard", "the_bell"], 5, ""])
        self.assertIsNone(problem, "the file itself loads (not its backup)")
        self.assertEqual(back.world["flags"], ["met_elder"])
        self.assertEqual(len(said(note, "save slot 1", "flags")), 1)
        ex = place(explore.Explorer(back), (21, 20))
        press(ex, (1, 0), 10)
        ex.store()
        self.assertEqual(sv.SaveGame.from_dict(json.loads(json.dumps(back.to_dict()))).world["flags"], ["met_elder"])

    def test_a_danger_of_infinity_or_nan_is_drawn_again(self):
        for bad in (math.inf, -math.inf, math.nan):
            notes.NOTED.clear()
            with self.subTest(danger=bad), mock.patch("crashlog.note") as note:
                back, problem = self.written(danger=bad)            # json writes Infinity / NaN and reads them back
                self.assertIsNone(problem)
                self.assertNotIn("danger", back.world)
                self.assertEqual(len(said(note, "save slot 1", "random fight")), 1)
        for still_damaged in (-1, True, "5"):                       # RPG13's rule stays (test_rpg13 pins it)
            d = save().to_dict()
            d["world"]["danger"] = still_damaged
            with self.subTest(danger=still_damaged), self.assertRaises(sv.SaveError):
                sv.SaveGame.from_dict(d)

    def test_gold_below_zero_is_zero(self):
        s = save()
        s.gold = 10
        d = s.to_dict()
        d["gold"] = -40                                             # what RPG16c's toll left
        with mock.patch("crashlog.note") as note:
            self.assertEqual(sv.SaveGame.from_dict(d).gold, 0)
        self.assertEqual(len(said(note, "save slot 1", "gold was -40")), 1)
        d["gold"] = "100"
        with self.assertRaises(sv.SaveError):
            sv.SaveGame.from_dict(d)

    def test_a_good_save_is_read_unchanged_and_nothing_is_said(self):
        s = save()
        s.world.update(flags=["met_elder"], danger=42.5)
        d = json.loads(json.dumps(s.to_dict()))
        with mock.patch("crashlog.note") as note:
            self.assertEqual(sv.SaveGame.from_dict(copy.deepcopy(d)).to_dict(), d)
        self.assertEqual(note.call_count, 0)

    def test_a_danger_no_float_can_hold_is_drawn_again(self):
        """Found writing these tests: 10 ** 400 (json reads 1 and 400 zeros as a whole number) minus the tiles walked - a float -
        raised OverflowError on every step on a map with random fights."""
        ex = explorer_at("warrens_1", at(1, 12))
        a = ex.area
        ex._use(a, explore.Walker(a, *a.spawn("entrance")))
        ex.save.world["danger"] = 10 ** 400
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            press(ex, d, 20)
        self.assertLess(ex.save.world["danger"], 1000, "a new gap was drawn")
        d = save().to_dict()
        d["world"]["danger"] = 10 ** 400
        with mock.patch("crashlog.note") as note:
            self.assertNotIn("danger", sv.SaveGame.from_dict(d).world)
        self.assertEqual(len(said(note, "random fight")), 1)

    def test_a_reward_never_takes_gold(self):
        g = r8.new_game(None)
        g.save.gold = 10
        with mock.patch("crashlog.note"):
            for bad in (-50, 0, "-5", "lots", None, [5], True):
                self.assertEqual(adv.give_reward(g, {"gold": bad}), [])
            self.assertEqual(adv.give_reward(g, ["gold", 5]), [])
        self.assertEqual(g.save.gold, 10)
        self.assertEqual(adv.give_reward(g, {"gold": 5}), ["+5 gold."])
        self.assertEqual(g.save.gold, 15)


class FoeWalkOffTests(unittest.TestCase):
    def test_a_foe_over_a_person_walks_off_but_never_onto_another(self):
        ex = place(explorer_at(OUT, at(1, 22)), (21, 26))
        a = ex.area
        f = explore.Foe(a, next(t for t in a.triggers if t.spot == "ashfen_outskirts#night_graves"))
        c, r = a.tile_at(f.x, f.y)
        self.assertEqual(f.box[0] // a.tw, (f.box[2] - 1e-6) // a.tw, "the setup: the foe's box is inside one tile")
        f.avoid = {(c, r), (c + 1, r)}                       # a person came out where it stands, and someone stands east
        self.assertTrue(f._free(f.x - 1, f.y), "a step that stays over the person it is on (walking off them)")
        right = (c + 1) * a.tw + f.hw + 1                    # a box over the next tile east too
        self.assertFalse(f._free(right, f.y), "never onto someone new")
        f.avoid = {(c + 1, r)}
        self.assertTrue(f._free(f.x - 1, f.y))
        self.assertFalse(f._free(right, f.y), "guard: N5's rule as before")


class WantedTests(FreshNotes, unittest.TestCase):
    def test_a_test_that_raises_keeps_the_item_away_and_is_said_once(self):
        def boom(_world, _minutes):
            raise TypeError("unhashable type: 'list'")
        p = daynight.Presence([daynight.Item("odd#1", [], (3, 3), test=boom)])
        with mock.patch("crashlog.note") as note:
            for _ in range(5):
                self.assertEqual(p.update({}, at(1, 12), view=(0, 0, 1, 1), walker_tile=(9, 9), force=True), [])
        self.assertFalse(p.is_present("odd#1"))
        self.assertEqual(len(said(note, "odd#1")), 1)


class EventChoiceTests(FreshNotes, _window_base()):
    def test_a_choice_that_isnt_two_words_is_said_without_a_choice(self):
        from tests.test_forge_table import frame
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        table = {"odd": {"title": "On the road", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["A carter."],
                         "choice": [1, 2], "yes": {"pages": ["Yes."]}, "no": {"pages": ["No."]}}}
        with mock.patch.object(events, "events", return_value=table), mock.patch("crashlog.note") as note:
            f.start_event(g, "odd")
            self.assertIsNone(f.talk_box.choice)
            frame(g)                                          # the box draws
            self.say_all(g)
        self.assertIsNone(f.sub)
        self.assertEqual(len(said(note, "odd", "choice")), 1)


if __name__ == "__main__":
    unittest.main()
