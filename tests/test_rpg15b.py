# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG15b: the Adventure's Hint button (Karl 23 Oct 2026 19:07: "make a hint button that players can press that gives them
hints about where what to do to progress in the game"), and the bugs the slice walk found (claude/SLICE_WALK_2026-10-23.md):
F1 the two shopkeepers couldn't be talked to, F2 people talked to through walls, F3 beaten foes not coming back until a shop's
window, F4 the fight screen naming the wrong place a loss sends you, F5 Elder Hesk offering a job already done.

  HintDataTests      rpg/data/hints.json: its check, two planted long lines caught, the condition words, the numbers filled in
  HintStoryTests     the hint at each step of the slice names that step; every quest state on every map has a real hint
  HintWindowTests    the Hint button beside Menu, the H key, the Menu's Hint; the box fits at five sizes
  CounterTests       talk across counters only (F2); the shops open from their keepers' words (F1)
  FixTests           foes back on arriving in town and after a rest (F3); the loss's words (F4); Hesk after the boss (F5)
"""
import copy
import os
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import adventure as adv
from rpg import clock, encounters, explore, hints, talk
from tests import rpg_route
from tests import test_rpg8 as r8
from tests import test_rpg14 as r14
from tests.test_forge_table import frame, key

TOWN, OUT, W1, W2 = "lantern_hollow", "ashfen_outskirts", "warrens_1", "warrens_2"
MAPS = (TOWN, OUT, W1, W2)
DARK, DEBT = "the_dark_hall", "lamplighters_debt"
SIZES = ((1024, 640), (1366, 768), (1920, 1080), (2560, 1440), (4096, 2019))


def world(**kw):
    """A save's world: flags (list), quests {id: (state, count)}, clock (minutes)."""
    w = {"flags": list(kw.get("flags", ())), "quests": {}, "clock": kw.get("clock", 12 * 60)}
    for qid, (state, count) in (kw.get("quests") or {}).items():
        w["quests"][qid] = {"state": state, "count": count}
    return w


def say(w, place):
    hid, pages = hints.hint_for(w, place)
    return hid, " ".join(pages)


class HintDataTests(unittest.TestCase):
    def known(self):
        return set(talk.GAME_FLAGS) | {f"met_{n}" for n in talk.npcs()}

    def test_the_hints_pass_their_check(self):
        self.assertEqual(hints.check(maps=set(explore.map_names()), known_flags=self.known()), [])

    def test_the_check_catches_two_planted_long_lines(self):
        entries = copy.deepcopy(hints.hints())
        entries[0]["say"][0] += " And then a great deal more is said than any dialogue box page could ever hold on three lines."
        entries[5]["say"].append("x" * 20 + " " + "y" * 40 + " " + "z" * 40 + " " + "w" * 40)
        problems = hints.check(entries, maps=set(explore.map_names()), known_flags=self.known())
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all("lines" in p for p in problems), problems)
        self.assertIn(entries[0]["id"], problems[0])
        self.assertIn(entries[5]["id"], problems[1])

    def test_a_page_one_line_too_long_is_caught_and_a_full_page_is_not(self):
        """The box's limit exactly: a page of talk.PAGE_LINES lines passes, one more line doesn't (plant 1 let 4 lines through)."""
        def page_of(n):
            words, page = ["word"] * 400, ""
            for wd in words:
                trial = (page + " " + wd).strip()
                if len(talk.wrap(trial)) > n:
                    break
                page = trial
            self.assertEqual(len(talk.wrap(page)), n)
            return page
        for n, problems in ((talk.PAGE_LINES, 0), (talk.PAGE_LINES + 1, 1)):
            entries = copy.deepcopy(hints.hints())
            entries[0]["say"] = [page_of(n)]
            with self.subTest(lines=n):
                self.assertEqual(len(hints.check(entries, maps=set(explore.map_names()), known_flags=self.known())),
                                 problems)

    def test_the_check_knows_its_words(self):
        bad = [{"id": "a", "if": ["map:nowhere", "phase:noon", "quest:the_dark_hall=lost", "flag:never_set", "weather:rain"],
                "say": ["{count:no_quest} of 3"]},
               {"id": "a", "if": ["flag:met_elder"], "say": []}]
        problems = " | ".join(hints.check(bad, maps=set(explore.map_names()), known_flags=self.known()))
        for word in ("no map 'nowhere'", "'phase:noon'", "the_dark_hall=lost", "never_set", "'weather:rain'", "no_quest",
                     "two hints have this id", "says nothing", "last hint must have no conditions"):
            self.assertIn(word, problems)

    def test_numbers_are_filled_in_and_never_pass_the_goal(self):
        # Round RPG18 (expected change): Pell's goal is one bandit (was three). Round RPG18b (expected change, Karl 25 Oct): the
        # shipped hints no longer show the count, so the fields are checked on a page of their own
        page = "{count:lamplighters_debt} of {goal:lamplighters_debt}"
        w = world(quests={DEBT: ("active", 0)}, flags=["met_elder", "met_lamplighter"])
        self.assertEqual(hints.fill(page, w), "0 of 1")
        w = world(quests={DEBT: ("ready", 5)}, flags=["met_lamplighter"])
        self.assertEqual(hints.fill(page, w), "1 of 1")

    def test_map_phase_and_negated_conditions(self):
        w = world(clock=23 * 60)
        self.assertTrue(hints.holds("phase:night", w, TOWN))
        self.assertTrue(hints.holds("!phase:day", w, TOWN))
        self.assertTrue(hints.holds("map:lantern_hollow", w, TOWN))
        self.assertFalse(hints.holds("!map:lantern_hollow", w, TOWN))
        self.assertTrue(hints.holds("!quest:the_dark_hall=active", w, TOWN))
        self.assertTrue(hints.holds("phase:day", w, TOWN, minutes=12 * 60))


class HintStoryTests(unittest.TestCase):
    def test_each_step_of_the_slice_has_a_hint_that_names_it(self):
        steps = [
            ("a new game", world(), TOWN, "meet_people", "Talk to the people"),
            ("Hesk met, not taken", world(flags=["met_elder"]), TOWN, "hesk_offer", "Hesk"),
            ("Pell met, not taken", world(flags=["met_elder", "met_lamplighter"], quests={DARK: ("active", 0)}), TOWN,
             "mill_town", "old mill"),
            ("both taken, in town", world(flags=["met_elder", "met_lamplighter"], quests={DARK: ("active", 0), DEBT: ("active", 0)}),
             TOWN, "both_town", "east road"),
            ("both taken, on the moor", world(flags=["met_elder", "met_lamplighter"],
                                               quests={DARK: ("active", 0), DEBT: ("active", 0)}), OUT, "both_moor",
             "leader waits by the bridge"),                                                   # RPG18b: was "0 of 1"
            ("their leader beaten", world(flags=["met_elder", "met_lamplighter"], quests={DARK: ("active", 0), DEBT: ("active", 1)}),
             OUT, "debt_report", "Tell Pell"),
            ("the Warrens, floor 1", world(flags=["met_elder", "met_lamplighter", "warrens_entered"],
                                            quests={DARK: ("active", 0), DEBT: ("done", 3)}), W1, "warrens_stairs", "stairs down"),
            ("the Warrens, floor 2", world(flags=["met_elder", "met_lamplighter", "warrens_entered"],
                                            quests={DARK: ("active", 0), DEBT: ("done", 3)}), W2, "warrens_boss", "Grave Priest"),
            ("the Priest beaten", world(flags=["met_elder", "met_lamplighter", "warrens_entered", talk.BOSS_FLAG],
                                         quests={DARK: ("active", 0), DEBT: ("done", 3)}), W2, "dark_hall_report", "Elder Hesk"),
            ("both handed in", world(flags=["met_elder", "met_lamplighter", "warrens_entered", talk.BOSS_FLAG, "has_orrins_map"],
                                      quests={DARK: ("done", 0), DEBT: ("done", 3)}), TOWN, "rematches", "Keeper Brann"),
        ]
        for what, w, place, hid, words in steps:
            with self.subTest(step=what):
                got_id, text = say(w, place)
                self.assertEqual(got_id, hid)
                self.assertIn(words, text)

    def test_every_quest_state_on_every_map_gets_a_real_hint(self):
        """Never the fallback (the shops) while a quest is open, and a quest that is ready always names who to tell."""
        giver = {DARK: "Hesk", DEBT: "Pell"}
        for place in MAPS:
            for dark in talk.STATES:
                for debt in talk.STATES:
                    flags = ["met_elder", "met_lamplighter"] + ([talk.BOSS_FLAG] if dark in ("ready", "done") else [])
                    w = world(flags=flags, quests={q: (s, 3 if s in ("ready", "done") else 0)
                                                   for q, s in ((DARK, dark), (DEBT, debt)) if s != "none"})
                    with self.subTest(place=place, dark=dark, debt=debt):
                        hid, text = say(w, place)
                        self.assertTrue(text)
                        if "active" in (dark, debt) or "none" in (dark, debt):
                            self.assertNotEqual(hid, "shops")
                        for q, s in ((DARK, dark), (DEBT, debt)):
                            if s == "ready" and not (place in (W1, W2) and q == DEBT and dark != "ready"):
                                if hid in ("dark_hall_report", "debt_report"):
                                    self.assertIn(giver[{"dark_hall_report": DARK, "debt_report": DEBT}[hid]], text)

    def test_a_quest_done_before_it_was_asked_for_is_reported(self):
        w = world(flags=["met_elder", talk.BOSS_FLAG])
        hid, text = say(w, TOWN)
        self.assertEqual(hid, "dark_hall_news")                         # Hesk hands it in at once (F5): the hint says go to him
        self.assertIn("Elder Hesk", text)

    def test_hint_for_never_changes_the_save(self):
        w = world(flags=["met_elder", "met_lamplighter"], quests={DEBT: ("active", 3)})
        before = copy.deepcopy(w)
        hints.hint_for(w, OUT)
        self.assertEqual(w, before)


class HintWindowTests(r14.WindowBase):
    def hint_said(self, g):
        f = g.modal
        self.assertEqual((f.sub, f.talk_box.name), ("talk", hints.NAME))
        return f.talk_box.pages

    def test_the_button_beside_menu_shows_the_hint(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        names = [n for _r, n in f.buttons]
        self.assertIn("hint", names)
        self.press(g, "hint")
        _hid, pages = hints.hint_for(f.game.save.world, TOWN)
        self.assertEqual(self.hint_said(g), pages)
        for _ in range(10):
            if f.sub != "talk":
                break
            key(g, pygame.K_RETURN)
        self.assertIsNone(f.sub)

    def test_the_h_key_and_the_menu_show_it_too(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        key(g, pygame.K_h)
        self.assertTrue(self.hint_said(g))
        key(g, pygame.K_ESCAPE)
        for _ in range(10):
            if f.sub != "talk":
                break
            key(g, pygame.K_RETURN)
        key(g, pygame.K_ESCAPE)
        self.assertEqual(f.sub, "pause")
        names = [n for n, _l, _o in f.menu.items]
        for _ in range(names.index("hint")):
            key(g, pygame.K_DOWN)
        key(g, pygame.K_RETURN)
        self.assertTrue(self.hint_said(g))

    def test_the_hint_follows_the_story(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        self.press(g, "hint")
        self.assertIn("Talk to the people", " ".join(self.hint_said(g)))
        f.close_talk()
        w = f.game.save.world
        talk.set_flag(w, "met_elder")
        talk.set_flag(w, "met_lamplighter")
        w.setdefault("quests", {})[DEBT] = {"state": "active", "count": 0}      # Round RPG18: one bandit (was 2 of 3)
        self.on_map(g, OUT, "from_town")
        key(g, pygame.K_h)
        self.assertIn("leader waits by the bridge", " ".join(self.hint_said(g)))          # Round RPG18b: was "0 of 1"

    def test_the_buttons_and_the_box_fit_at_five_sizes(self):
        r8.new_game(None)
        for size in SIZES:
            with self.subTest(size=size):
                g = self.gui(size=size)
                self.open(g, slot=1)
                f = self.in_town(g)
                whole = g.screen.get_rect()
                rects = {n: r for r, n in f.buttons}
                self.assertTrue(whole.contains(rects["hint"]) and whole.contains(rects["pause"]))
                self.assertFalse(rects["hint"].colliderect(rects["pause"]))
                self.assertLess(rects["hint"].right, rects["pause"].left)
                key(g, pygame.K_h)
                frame(g)
                box = f.buttons[0][0]
                self.assertTrue(whole.contains(box), (box, whole))
                self.close(g)


class CounterTests(r14.WindowBase):
    def test_nobody_is_talked_to_through_a_wall(self):
        explore.forget_areas()
        a = explore.load_area(TOWN)
        for nid in a.npcs:
            c, r = a.npcs[nid]["tile"]
            for (dx, dy), facing in r14.STEPS.items():
                between, far = (c - dx, r - dy), (c - 2 * dx, r - 2 * dy)
                if a.blocked(*between) and not a.is_counter(*between) and not a.blocked(*far) and a.npc_at(*between) is None:
                    w = explore.Walker(a, *rpg_route.middle(a, far))
                    w.facing = facing
                    with self.subTest(nid=nid, tile=far):
                        self.assertEqual(explore.facing_of(w, a), (None, None))

    def test_the_counters_and_the_shops_on_them(self):
        explore.forget_areas()
        a = explore.load_area(TOWN)
        self.assertEqual(len(a.counters), 15)                 # the inn's 3, the card shop's 6, the pack shop's 6
        shops = {t.tab: a.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2) for t in a.triggers if t.kind == "town"}
        self.assertEqual(shops, {"singles": (31, 5), "packs": (7, 23)})
        self.assertTrue(all(a.is_counter(*tile) for tile in shops.values()))
        self.assertEqual(a.town, "town_1")
        self.assertEqual(explore.check_all()[TOWN], [])

    def test_the_tool_builds_the_shipped_tilesets_and_maps(self):
        """The counter property comes from tools/rpg_make_maps.py (TILE_PROPS), not from a hand edit of dl_decor.tsx: the tool's
        own --check builds every map file again and compares it with rpg/maps (plant 15 emptied TILE_PROPS unseen)."""
        from tests.test_rpg12 import make_tool
        self.assertEqual(make_tool().main(["--check"]), 0)

    def test_each_shopkeeper_opens_their_shop_and_you_leave_by_its_door(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        for nid, tab, back in (("card_seller", "singles", "town_1"), ("pack_seller", "packs", "town_1_packs")):
            with self.subTest(nid=nid):
                self.stand(g, nid)
                self.assertIsNotNone(f.explorer.facing_shop())
                key(g, pygame.K_RETURN)
                self.assertEqual(f.talk_box.name, talk.npcs()[nid]["name"])
                self.say_all(g)
                self.assertEqual((f.stage, f.tab), ("town", tab))
                self.press(g, "leave_town")
                self.assertEqual(f.stage, "map")
                self.assertEqual(a.tile_at(f.explorer.walker.x, f.explorer.walker.y), a.tile_at(*a.spawn(back)))

    def test_talking_to_marta_opens_no_shop(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        self.stand(g, talk.INNKEEPER)
        self.assertIsNone(f.explorer.facing_shop())
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=False)                        # Round RPG15c: her talk asks; Not now
        self.assertEqual((f.stage, f.sub), ("map", None))


class FixTests(r14.WindowBase):
    def arrive(self, g, f, name, spawn):
        """Through an exit onto `name` (the explorer moved there, then walk_tick told of the new map, as Explorer.step does)."""
        a = explore.load_area(name)
        f.explorer._use(a, explore.Walker(a, *a.spawn(spawn)))
        with mock.patch.object(f.explorer, "step", return_value=("map", name)):
            f.last = None
            f.walk_tick(g, 2.0, pressed=(1, 0))
            f.walk_tick(g, 2.05, pressed=(1, 0))

    def test_walking_into_town_brings_the_beaten_foes_back(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        w = f.game.save.world
        w["beaten"], w["last_town"] = ["ashfen_outskirts#6", "ashfen_outskirts#9"], "town_2"
        self.arrive(g, f, W1, "entrance")                     # a map that isn't a town: nothing comes back
        self.assertEqual(w["beaten"], ["ashfen_outskirts#6", "ashfen_outskirts#9"])
        self.arrive(g, f, TOWN, "from_outskirts")
        self.assertEqual(w["beaten"], [])
        self.assertEqual(w["last_town"], "town_1")

    def test_a_rest_at_the_inn_brings_them_back_too(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        w = f.game.save.world
        w["beaten"] = ["ashfen_outskirts#6"]
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=True)  # Round RPG15c (expected change): she asks; Sleep until morning
        self.assertEqual(w["beaten"], [])

    def test_the_fight_screen_says_where_a_loss_wakes_you(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        foe = encounters.RandomFoe("thornback")
        w = f.game.save.world
        w.pop("inn", None)
        w["last_town"] = "town_1"
        _t, lines = adv.fight_text(f.game, foe)
        self.assertTrue(lines[1].endswith("and back to Lantern Hollow."), lines[1])
        w["inn"] = {"map": TOWN, "spawn": explore.INN_SPAWN}
        w["last_town"] = "town_2"                             # Brackwater's window last: the old words named Brackwater (F4)
        _t, lines = adv.fight_text(f.game, foe)
        self.assertTrue(lines[1].endswith("and you wake at Lantern Hollow's inn."), lines[1])

    def test_hesk_hands_in_a_job_done_before_he_asked(self):
        w = {"flags": ["met_elder", talk.BOSS_FLAG]}
        conv = talk.Conversation("elder", w)
        q = talk.quests()[DARK]
        self.assertIsNone(conv.choice)
        self.assertEqual(conv.pages, list(q["ready"]))
        self.assertEqual(conv.reward, dict(q["reward"]))
        self.assertEqual(conv.after, list(q["done"]))
        self.assertEqual(talk.quest_state(w, DARK), "done")

    def test_hesk_still_offers_the_job_before_the_boss(self):
        w = {"flags": ["met_elder"]}
        conv = talk.Conversation("elder", w)
        self.assertIsNotNone(conv.choice)
        self.assertEqual(talk.quest_state(w, DARK), "none")


if __name__ == "__main__":
    unittest.main()
