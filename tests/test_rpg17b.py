# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG17b: Karl's answers (24 Oct) to RPG16d's five questions (claude/DECISIONS_2026-10-24.md, "RPG16d's questions").

  Q0  RPG9's note after a colourless first pick stays "At most 2 colours in all." - the game as it was, the test changed:
      tests/test_rpg9.py NewGameTests.test_a_colourless_first_pick_keeps_the_note_at_most_two_colours and
      ScreenTests.test_a_colourless_first_pick_keeps_at_most_two_colours_on_the_screen (the screen tests now name their seed).
  Q1  gold below 0 in a save is mended to 0 - no change: tests/test_rpg16d.py SaveRepairTests.test_gold_below_zero_is_zero,
      tests/test_rpg4.py (gold -1 mended). Pointed at here by GuardTests.
  Q2  events only give gold - no change: tests/test_rpg16d.py test_a_toll_and_a_list_flag_change_nothing_in_the_save and
      test_a_reward_never_takes_gold. Pointed at here by GuardTests.
  Q3  an event whose `where` can't be read now happens ANYWHERE on its map (RPG16d: never), still said once in crash_log.txt.
  Q4  rare_spawns.json has a `level` per rare (placeholders 1 / 2 / 3); a cooldown that can't be read is `level` days; a missing or
      unreadable level counts as 1; check() checks the field; a cooldown that can be read is unchanged (RPG17's 2 days).
"""
import copy
import inspect
import json
import math
import unittest
from unittest import mock

from rpg import adventure as adv
from rpg import events
from rpg import rare as rpg_rare
from tests import test_rpg8 as r8
from tests.test_rpg16 import RareTrigger, at, explorer_at, place, press, save
from tests.test_rpg16d import FreshNotes, rare_table, said

OUT = "ashfen_outskirts"
TOWN = "lantern_hollow"
GOOD = {"title": "x", "map": OUT, "when": [], "cooldown_days": 1, "pages": ["x"], "effect": {"gold": 1}}
ODD_BOXES = {"empty_where": [], "one_tile": [21, 20], "words": "0,0,9,9", "inside_out": [30, 30, 10, 10], "a_number": 7,
             "nan_box": [0, 0, math.nan, 5]}


# ---- Q3 ------------------------------------------------------------------------------------------------------------------------
class AnywhereTests(FreshNotes, unittest.TestCase):
    def table(self):
        t = {eid: dict(GOOD, where=box) for eid, box in ODD_BOXES.items()}
        t["boxed"] = dict(GOOD, where=[0, 0, 2, 2])                  # a good box the tile is outside of: still left out
        t["elsewhere"] = dict(GOOD, map=TOWN, where=[])               # another map's odd box: never here
        return t

    def test_an_odd_where_is_anywhere_on_its_map(self):
        table = self.table()
        with mock.patch("crashlog.note") as note:
            for tile in ((21, 20), (5, 30), (0, 0)):
                with self.subTest(tile=tile):
                    ids = events.eligible(save().world, OUT, tile, minutes=at(1, 12), entries=table)
                    for eid in ODD_BOXES:
                        self.assertIn(eid, ids)
                    self.assertNotIn("elsewhere", ids)
                    self.assertEqual("boxed" in ids, tile == (0, 0))
        for eid in ODD_BOXES:
            self.assertEqual(len(said(note, eid, "where")), 1, f"{eid}: said once over three checks ({note.call_args_list})")
            self.assertTrue(said(note, eid, "anywhere on its map"), eid)
        self.assertFalse(said(note, "never happens"))
        self.assertFalse(said(note, "boxed"))

    def test_on_its_own_map_only(self):
        with mock.patch("crashlog.note"):
            self.assertEqual(events.eligible(save().world, TOWN, (3, 3), minutes=at(1, 12), entries=self.table()), ["elsewhere"])

    def test_the_rest_of_its_rules_still_hold(self):
        """Anywhere is only the box: its when and its cooldown still keep it away."""
        table = {"night_only": dict(GOOD, where=[], when=["phase:night"]), "cooling": dict(GOOD, where="here")}
        s = save()
        s.world["events"] = {"last": {"cooling": at(1, 11)}}
        with mock.patch("crashlog.note"):
            self.assertEqual(events.eligible(s.world, OUT, (21, 20), minutes=at(1, 12), entries=table), [])
            self.assertEqual(events.eligible(s.world, OUT, (21, 20), minutes=at(2, 12), entries=table), ["cooling"])

    def test_check_still_names_the_odd_where_for_karl(self):
        """The game reads it as anywhere; check() (run on the shipped file by the tests) still says it is wrong."""
        maps = {OUT: (60, 40), TOWN: (40, 30)}
        problems = events.check({"words": dict(GOOD, where="0,0,9,9")}, maps=maps)
        self.assertTrue(any("where" in p for p in problems), problems)

    def test_walking_meets_an_event_with_an_odd_where(self):
        """Through the walk (Explorer._event -> events.step): the first check on the map picks the odd-boxed event."""
        events.forget()
        self.addCleanup(events.forget)
        table = {"odd": dict(GOOD, where="by the bridge")}
        got = []
        real_step = events.step

        def step(*a, **kw):
            r = real_step(*a, **kw)
            got.append(r)
            return r
        with mock.patch.object(events, "events", return_value=table), mock.patch.object(events, "step", step), \
                mock.patch("crashlog.note"):
            ex = place(explorer_at(OUT, at(1, 12)), (21, 20))
            ex.save.world["events"] = {"gap": 0.5}
            press(ex, (1, 0), 30)
        self.assertIn("odd", got, got)


# ---- Q4 ------------------------------------------------------------------------------------------------------------------------
class LevelTests(FreshNotes, unittest.TestCase):
    def test_the_shipped_levels(self):
        with open(rpg_rare.RARE_FILE, encoding="utf-8") as f:
            table = json.load(f)["rare"]
        self.assertEqual({rid: e.get("level") for rid, e in table.items()},
                         {"mother_of_rats": 1, "red_sash": 2, "old_gorsehide": 3})
        self.assertEqual(rpg_rare.check(table), [])

    def test_a_cooldown_that_can_be_read_is_unchanged(self):
        with open(rpg_rare.RARE_FILE, encoding="utf-8") as f:
            table = json.load(f)["rare"]
        for rid, e in table.items():
            with self.subTest(rid):
                self.assertEqual(e["cooldown_days"], 2)                                   # RPG17's 2 days
                self.assertEqual(rpg_rare.cooldown_of(rid, e), 2.0)
        for v in (0, 0.5, 7, math.inf):
            self.assertEqual(rpg_rare.cooldown_of("x", {"level": 3, "cooldown_days": v}), float(v))

    def test_a_cooldown_that_cannot_be_read_is_the_levels_days(self):
        for lv in (1, 2, 3, 10):
            for v in ("three", None, math.nan, [2], True, {"days": 2}):
                with self.subTest(level=lv, cooldown=v):
                    e = {"level": lv} if v is None else {"level": lv, "cooldown_days": v}
                    self.assertEqual(rpg_rare.cooldown_of(f"r{lv}", e), float(lv))

    def test_a_negative_cooldown_is_still_0(self):
        """A number below 0 is read (and clamped to 0, as RPG16d had it) - not "can't be read", so the level doesn't come in."""
        self.assertEqual(rpg_rare.cooldown_of("x", {"level": 3, "cooldown_days": -1}), 0.0)

    def test_a_missing_or_unreadable_level_counts_as_1(self):
        with mock.patch("crashlog.note") as note:
            for lv in (None, 0, -2, 2.5, "two", True, [3], math.nan, math.inf):
                with self.subTest(level=lv):
                    e = {"cooldown_days": "three"} if lv is None else {"level": lv, "cooldown_days": "three"}
                    self.assertEqual(rpg_rare.level_of("x", e), 1)
                    self.assertEqual(rpg_rare.cooldown_of(f"x{lv!r}", e), 1.0)
        self.assertFalse(said(note, "x", "level None"), "a missing level is 1 and says nothing")
        self.assertEqual(len(said(note, "rare_spawns.json: x's level ")), 1, "rare x's unreadable level is said once, not per call")

    def test_each_thing_is_said_once(self):
        with mock.patch("crashlog.note") as note:
            for _ in range(3):
                rpg_rare.cooldown_of("old_gorsehide", {"level": "high", "cooldown_days": "three"})
        self.assertEqual(len(said(note, "old_gorsehide's level ")), 1)
        self.assertEqual(len(said(note, "old_gorsehide", "cooldown_days", "1 day is used")), 1, note.call_args_list)

    def test_check_accepts_and_checks_the_level(self):
        base = copy.deepcopy(rpg_rare.spawns())
        self.assertEqual(rpg_rare.check(base), [])
        del base["red_sash"]["level"]
        self.assertEqual(rpg_rare.check(base), [], "level may be left out (it counts as 1)")
        for bad in (0, -1, 2.5, "two", True, None, [1]):
            with self.subTest(level=bad):
                t = copy.deepcopy(rpg_rare.spawns())
                t["red_sash"]["level"] = bad
                problems = rpg_rare.check(t)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("red_sash: level", problems[0])

    def test_beaten_with_an_unreadable_cooldown_it_stays_away_for_its_level(self):
        s = save()
        with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(cooldown_days="three")), mock.patch("crashlog.note"):
            rpg_rare.on_beaten(s.world, "old_gorsehide", at(1, 6))             # level 3
            for day, out in ((1, False), (2, False), (3, False), (4, True)):
                with self.subTest(day=day):
                    self.assertEqual(rpg_rare.is_out(s, "old_gorsehide", at(day, 18, 30)), out)

    def test_the_fight_question_says_the_levels_days(self):
        for lv, words in ((1, "Beaten, it stays away for 1 day."), (2, "Beaten, it stays away for 2 days.")):
            with self.subTest(level=lv):
                with mock.patch.object(rpg_rare, "spawns", return_value=rare_table(cooldown_days="soon", level=lv)), \
                        mock.patch("crashlog.note"):
                    _title, lines = adv.fight_text(r8.new_game(None), RareTrigger("old_gorsehide"))
                self.assertIn(words, lines)


# ---- Q1, Q2: no change; the tests that say so are still there --------------------------------------------------------------------
class GuardTests(unittest.TestCase):
    def test_the_q1_and_q2_tests_are_still_there(self):
        from tests import test_rpg16d
        names = {n for cls in (test_rpg16d.SaveRepairTests,) for n in dir(cls)}
        self.assertIn("test_gold_below_zero_is_zero", names)
        src = inspect.getsource(test_rpg16d)
        self.assertIn("def test_a_toll_and_a_list_flag_change_nothing_in_the_save", src)
        self.assertIn("def test_a_reward_never_takes_gold", src)


if __name__ == "__main__":
    unittest.main()
