# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG3 (Karl, 2 Nov 2026: "make progression more tangible and 'fun' JRPG style"; answer 1 "Duel too, freely", answer 3 "a small
fixed bonus every level plus a perk choice every few levels"; the design: the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md,
section 3.3). Groups:

  1  EffectsTests    start_effects.py: the SPEC, the .dck line, the bridge's arguments; write_deck_file and the Launcher put them in
                     the PLAYER's .dck only; ForgeSession reads them from the .dck (Resume) and refuses a bridge that doesn't say it
                     applied them
  2  SourceTests     the bridge's Main / StartEffects and java_rpg's Duels / StartEffects (the same parse())
  3  LevelTests      rpg.levels: XP by kind, the curve, the cap, life by level, perks dealt every 3rd level, never one offered twice,
                     stackable ones again, unavailable ones never; the perks' gold, loss and reward-choice rules
  4  AdventureTests  through the Adventure window: a won duel gives its XP (written), a loss 2; the results screen's XP bar and
                     level-up line and fanfare; a duel at level 10 starts with 29 life (the launcher gets the effects, the foe none);
                     the encounter screen says so; the perk waits in the Menu, a tap takes it; a quest gives XP
  5  SaveTests       a save from before PROG3 loads at level 1 with 0 XP; a damaged progress is mended
  6  DataTests       progression.json's xp / levels / perks are read and checked
  7  FitTests        the results screen with level-up lines and the perk window fit 1360x840, 1920x1080, 2736x1824 at 100%/200%
  8  LiveTests       real Forge: each effect seen in a game; the foe untouched; a game from a saved .dck keeps them (Resume);
                     a bad card is a fatal error; Duels (rpg_tools.jar) applies seat A's effects

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest tests.test_prog3
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import pygame

import forge_client as fc
import forge_table as ft
import rpg_results
import rpg_screens as rs
import start_effects as se
from rpg import adventure as adv
from rpg import duel, encounters, levels, progress
from rpg import save as sv
from tests import live
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame, key
from tests.test_prog1 import PROG_SIZES, PlayedBase

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_SE = os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "StartEffects.java")
RPG_SE = os.path.join(BASE, "java_rpg", "src", "forge", "rpgtools", "StartEffects.java")
MAIN = os.path.join(BASE, "java_bridge", "src", "forge", "bridge", "Main.java")
DUELS = os.path.join(BASE, "java_rpg", "src", "forge", "rpgtools", "Duels.java")


def xp_at(level):
    return levels.read().xp_to_reach(level)


# ==================================================================================================================================
class EffectsTests(unittest.TestCase):
    E = {"life": 27, "hand": 9, "battlefield": ["Sol Ring", "token:c_a_treasure_sac"], "command": ["Circle of Protection: Red"]}

    def test_spec_parse_and_the_dck_line_round_trip(self):
        s = se.spec(self.E)
        self.assertEqual(s, "life:27;hand:9;battlefield:Sol Ring;battlefield:token:c_a_treasure_sac;"
                            "command:Circle of Protection: Red")
        self.assertEqual(se.parse(s), self.E)                         # a ':' inside a card name survives
        self.assertEqual(se.from_dck("[metadata]\n" + se.dck_lines(self.E)[0] + "\n[Main]\n"), self.E)
        self.assertEqual(se.engine_args(self.E), ["--start-effects", s])

    def test_none_is_exactly_as_before(self):
        for none in (None, {}):
            self.assertEqual(se.spec(none), "")
            self.assertEqual(se.dck_lines(none), [])
            self.assertEqual(se.engine_args(none), [])
        self.assertEqual(se.from_dck("[metadata]\nName=x\n[Main]\n4 Shock\n"), {})

    def test_bad_values_are_refused(self):
        for bad in ({"life": 0}, {"life": "x"}, {"hand": 21}, {"mana": 3}, {"battlefield": ["a;b"]}, {"life": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(se.StartEffectsError):
                    se.normal(bad)
        with self.assertRaises(se.StartEffectsError):
            se.parse("life")

    def test_the_bridge_must_say_it_applied_them(self):
        self.assertTrue(se.same({"life": 23}, {"life": 23}))
        self.assertFalse(se.same({"life": 23}, None))                 # an older bridge: no "startEffects"
        self.assertFalse(se.same({"life": 23}, {}))
        self.assertTrue(se.same({}, None))
        self.assertEqual(se.describe({"life": 23, "battlefield": [se.TREASURE]}), "You start with 23 life and a Treasure.")
        self.assertEqual(se.describe({}), "")

    def test_write_deck_file_and_the_session_read_them_from_the_players_dck(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = fc.write_deck_file(os.path.join(d, "player.dck"), [], ["Shock"] * 60, "P", fmt="standard", effects={"life": 25})
        self.assertIn("Manticore Start=life:25\n", open(p, encoding="utf-8").read())
        q = fc.write_deck_file(os.path.join(d, "o.dck"), [], ["Shock"] * 60, "O", fmt="standard")
        self.assertNotIn("Manticore Start", open(q, encoding="utf-8").read())
        s = fc.ForgeSession(p, [q], seed=1)                            # Resume / a replay: no effects passed, the .dck says them
        self.assertEqual(s.effects, {"life": 25})
        args = s._bridge_args()
        self.assertEqual(args[args.index("--start-effects") + 1], "life:25")

    def test_an_older_bridge_is_a_fatal_error(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = fc.write_deck_file(os.path.join(d, "player.dck"), [], ["Shock"] * 60, "P", fmt="standard", effects={"life": 25})
        s = fc.ForgeSession(p, [p], seed=1)
        s.handle({"t": "ready", "format": "constructed", "houseRules": []})               # no "startEffects": older
        self.assertIn("older than this program", s.fatal or "")
        s2 = fc.ForgeSession(p, [p], seed=1)
        s2.handle({"t": "ready", "format": "constructed", "houseRules": [], "startEffects": {"life": 25}})
        self.assertFalse(s2.fatal)

    def test_the_launcher_writes_them_into_the_players_dck_only(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        made = []

        class FakeSession:
            def __init__(self, path, opps, **kw):
                made.append((path, opps))

            def start(self):
                return self

        with mock.patch.object(ft, "DECK_DIR", d), mock.patch.object(fc, "ForgeSession", FakeSession):
            ft.Launcher().start(([], ["Shock"] * 60), [([], ["Shock"] * 60)], fmt="standard", names=["Foe"],
                                effects={"life": 29, "hand": 8})
        mine, opps = made[0]
        self.assertEqual(se.from_dck_file(mine), {"life": 29, "hand": 8})
        self.assertEqual(se.from_dck_file(opps[0]), {}, "the foe's deck got the player's effects")


# ==================================================================================================================================
class SourceTests(unittest.TestCase):
    def body(self, path):
        src = open(path, encoding="utf-8").read()
        i = src.index("public static StartEffects parse(")
        j = src.index("public boolean isEmpty()")
        return src[i:j]

    def test_the_two_parsers_are_the_same_text(self):
        self.assertEqual(self.body(BRIDGE_SE), self.body(RPG_SE))

    def test_main_takes_the_flag_applies_it_to_the_human_seat_and_names_it_in_ready(self):
        m = open(MAIN, encoding="utf-8").read()
        self.assertIn('case "--start-effects"', m)
        self.assertIn('ready.add("startEffects"', m)
        a = m.index("StartEffects.active.applyTo(human)")
        self.assertLess(m.index("RegisteredPlayer human = register(mine, seats);"), a)
        self.assertLess(a, m.index("for (String path : opponents)"))           # before (and never inside) the AI seats' loop

    def test_duels_reads_seat_as_start_from_the_sixth_column(self):
        d = open(DUELS, encoding="utf-8").read()
        self.assertIn("p.length > 5 ? p[5] : null", d)
        self.assertIn("StartEffects.parse(startA).applyTo(ra)", d)
        self.assertNotIn("applyTo(rb)", d)


# ==================================================================================================================================
class FakeSave:
    def __init__(self, xp=0):
        self.progress = {"xp": xp, "perks": [], "offers": []}
        self.n = 0
        self.gold = 100

    def rng(self, purpose):
        import random
        self.n += 1
        return random.Random(f"{self.n}:{purpose}")


class LevelTests(unittest.TestCase):
    def test_xp_by_kind(self):
        lv = levels.read()
        got = {k: levels.xp_for("won", k, lv) for k in ("regular", "dungeon", "rare", "boss")}
        self.assertEqual(got, {"regular": 20, "dungeon": 40, "rare": 60, "boss": 120})   # rc16 (expected change): Karl's XP x2 (was 10/20/30/60)
        self.assertEqual(levels.xp_for("quest", None, lv), 50)                            # rc16: was 25
        self.assertEqual(levels.xp_for("lost", "boss", lv), 4)        # a loss is a loss's XP, whoever beat you (rc16: was 2)
        self.assertEqual(levels.xp_for("conceded", "regular", lv), 4)
        self.assertEqual(levels.xp_for("draw", "regular", lv), 0)
        self.assertEqual(levels.xp_for("unfinished", "regular", lv), 0)

    def test_the_curve_and_the_cap(self):
        lv = levels.read()
        self.assertEqual([lv.xp_to_reach(n) for n in range(2, 8)], [30, 75, 135, 210, 300, 405])   # the handover's numbers
        self.assertEqual(lv.level_of(29), 1)
        self.assertEqual(lv.level_of(30), 2)
        self.assertEqual(lv.level_of(129), 3)                                                     # the slice's ~129 XP
        self.assertEqual(lv.level_of(10 ** 7), 20)
        self.assertEqual(lv.bar(10 ** 7), (20, None, None))
        self.assertEqual(levels.bar_text(40), "Level 2  XP 10/45")
        self.assertEqual(levels.bar_text(10 ** 7), "Level 20  XP MAX")

    def test_life_by_level(self):
        for lvl, life in ((1, None), (2, 21), (10, 29), (20, 39)):
            s = FakeSave(xp_at(lvl))
            self.assertEqual(levels.duel_effects(s).get("life"), life, lvl)

    def test_a_perk_choice_every_third_level_and_never_one_offered_twice(self):
        s = FakeSave()
        g = levels.gain(s, xp_at(9))                           # 1 -> 9 at once: levels 3, 6 and 9 each deal a choice
        self.assertEqual((g["level_before"], g["level_after"]), (1, 9))
        self.assertEqual(len(g["offers"]), 3)
        flat = [x for grp in s.progress["offers"] for x in grp]
        lv = levels.read()
        for n, grp in enumerate(s.progress["offers"]):
            self.assertEqual(len(grp), 3 if n < 2 else len(grp))     # the third may hold fewer: none twice, and few are left
            self.assertGreaterEqual(len(grp), 1)
            self.assertEqual(len(set(grp)), len(grp))
        non_stack = [x for x in flat if not lv.perks[x].stackable]
        self.assertEqual(len(non_stack), len(set(non_stack)), f"a perk offered twice: {s.progress['offers']}")
        self.assertFalse({x for x in flat} & {p.id for p in lv.perks.values() if not p.available})

    def test_a_taken_perk_is_not_offered_again_unless_stackable(self):
        lv = levels.read()
        for seed in range(12):
            s = FakeSave()
            s.n = seed * 7
            for lvl in (3, 6, 9, 12, 15, 18):
                levels.gain(s, xp_at(lvl) - s.progress["xp"])
                while levels.offers(s):
                    levels.choose(s, levels.offers(s)[0][0])
            taken = s.progress["perks"]
            once = [x for x in taken if not lv.perks[x].stackable]
            self.assertEqual(len(once), len(set(once)), taken)
            self.assertEqual(len(taken), 6)                    # the stackable Stout keeps the list from running dry

    def test_choose_takes_only_an_offered_perk(self):
        s = FakeSave()
        levels.gain(s, xp_at(3))
        grp = levels.offers(s)[0]
        other = next(p for p in levels.read().perks if p not in grp)
        with self.assertRaises(ValueError):
            levels.choose(s, other)
        levels.choose(s, grp[1])
        self.assertEqual(s.progress["perks"], [grp[1]])
        self.assertEqual(levels.offers(s), [])

    def test_the_perks_effects(self):
        s = FakeSave(xp_at(4))
        s.progress["perks"] = ["quick_draw", "hoard", "stout", "stout", "keen_eye", "bounty", "thick_skin"]
        self.assertEqual(levels.duel_effects(s), {"life": 20 + 3 + 6, "hand": 8, "battlefield": [se.TREASURE]})
        self.assertEqual(levels.reward_choices(s, 3), 4)
        self.assertEqual(levels.win_gold(s, 30), 33)

        class N:                                     # Round GOLD1: Thick Skin is a quarter off the kind's share
            loss_gold_share = 0.2

            def loss_share(self, kind="regular"):
                return 0.2
        self.assertEqual(levels.loss_cost(s, N()), 15)
        s.progress["perks"] = []
        self.assertEqual(levels.loss_cost(s, N()), 20)

    def test_level_up_lines(self):
        g = {"level_before": 2, "level_after": 3, "offers": [["a", "b", "c"]]}
        self.assertEqual(levels.level_up_lines(g), ["Level up! Level 3: your duels start at 22 life (before perks).",
                                                    "A perk to choose: Menu."])
        self.assertEqual(levels.level_up_lines({"level_before": 3, "level_after": 3, "offers": []}), [])


# ==================================================================================================================================
class AdventureTests(PlayedBase):
    def at_xp(self, g, xp, perks=()):
        f = g.modal
        f.game.save.progress = {"xp": xp, "perks": list(perks), "offers": []}
        f.game.write()
        return f

    def test_a_won_duel_gives_its_xp_and_writes_it(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts").game.save.world["first_win_day"] = 10 ** 6     # Round PROG5 (expected change): the day's first win paid already, so the duel's own gold and XP are what is tested
        f = self.duel_to_result(g, won=True)
        want = levels.xp_for("won", encounters.RANDOM_ENCOUNTER)          # the window's duel is a random foe's (a dungeon's: 20)
        self.assertEqual(r8.on_disk(self.folder)["progress"]["xp"], want)
        self.assertEqual(f.game.save.progress["xp"], want)
        self.assertEqual(f.results.info["xp"]["gained"], want)

    def test_a_loss_gives_a_losss_xp_not_a_wins(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        self.duel_to_result(g, won=False)
        self.assertEqual(r8.on_disk(self.folder)["progress"]["xp"], 4)      # rc16 (expected change): XP x2, was 2

    def test_dungeon_boss_and_rare_wins(self):
        r8.new_game(None)
        for enc, kind, extra, want in (("dungeon", "plague_rat", {}, 40), ("boss", "grave_priest", {}, 120),   # rc16: XP x2 (was 20 / 60 / 30)
                                       ("regular", "ember_bandit", {"rare": "red_sash"}, 60)):
            with self.subTest(enc=enc, rare=extra):
                game, _n = adv.Game.load(1, pool=self.pool)
                game.save.progress = {"xp": 0, "perks": [], "offers": []}
                game.save.world["first_win_day"] = 10 ** 6          # Round PROG5 (expected change): the day's first win paid already, so the duel's own gold and XP are what is tested
                game.save.pending_duel = dict({"kind": kind, "encounter": enc, "deck": 0, "source": "archetype", "map": None,
                                               "x": None, "y": None, "started": 0}, **extra)
                out = adv.finish_loaded(game, "won")
                self.assertEqual(out["xp"]["gained"], want)
                game.save.pending_pick = None
                game.write()

    def test_the_results_screen_shows_the_bar_the_level_up_and_plays_the_fanfare(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        f.game.save.world["first_win_day"] = 10 ** 6            # Round PROG5 (expected change): the day's first win paid already, so the duel's own gold and XP are what is tested
        self.at_xp(g, 70)                                       # +20 -> 90: level 3, a perk
        played = []
        orig = g.play_cue
        g.play_cue = lambda cue, *a, **k: (played.append(cue), orig(cue, *a, **k))[1]
        f = self.duel_to_result(g, won=True)
        res = f.results
        self.assertEqual(res.xp_shown(res.t0), 70)
        self.assertEqual(res.xp_shown(res.t0 + res.length), 70 + levels.xp_for("won", encounters.RANDOM_ENCOUNTER))
        self.assertNotIn("rpg.level_up", played)
        key(g, pygame.K_RETURN)
        frame(g)
        self.assertEqual(res.xp_words[0], "Level 3")
        self.assertIn("Level up! Level 3: your duels start at 22 life (before perks).", res.xp_words)
        self.assertIn("A perk to choose: Menu.", res.xp_words)
        self.assertEqual(played.count("rpg.level_up"), 1)
        frame(g)
        self.assertEqual(played.count("rpg.level_up"), 1)      # once
        self.assertEqual(len(r8.on_disk(self.folder)["progress"]["offers"]), 1)

    def test_a_duel_at_level_10_starts_with_29_life_and_the_foe_gets_nothing(self):
        g, launcher = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        self.at_xp(g, xp_at(10), perks=["hoard"])
        enc = self.meet(g, encounters.RandomFoe("ember_bandit"))
        self.assertIn("You start with 29 life and a Treasure.", enc.lines if hasattr(enc, "lines") else
                      adv.fight_text(g.modal.game, encounters.RandomFoe("ember_bandit"))[1])
        self.press(g, "fight")
        call = launcher.calls[-1]
        self.assertEqual(call["effects"], {"life": 29, "battlefield": [se.TREASURE]})

    def test_a_level_1_duel_asks_the_bridge_for_nothing(self):
        g, launcher = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        self.meet(g, encounters.RandomFoe("ember_bandit"))
        self.press(g, "fight")
        self.assertIsNone(launcher.calls[-1]["effects"])

    def test_the_perk_waits_in_the_menu_and_a_tap_takes_it(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        f.game.save.progress = {"xp": 80, "perks": [], "offers": [["stout", "keen_eye", "hoard"]]}
        f.game.write()
        frame(g)
        self.assertIsNone(f.sub, "the perk must never stop the walk")
        self.assertIn(rs.PERK_HUD, [t for t, _r in f.hud_words])
        key(g, pygame.K_ESCAPE)                                  # the Menu
        frame(g)
        self.assertEqual(f.sub, "pause")
        self.assertEqual(f.menu.items[0][:2], ("perk", rs.PERK_ITEM))
        self.press(g, "perk")
        frame(g)
        self.assertEqual(f.sub, "perk")
        self.assertEqual([n for _r, n in f.buttons if n.startswith("perk:")], ["perk:stout", "perk:keen_eye", "perk:hoard"])
        rect = next(r for r, n in f.buttons if n == "perk:keen_eye")
        click(g, rect.center)
        self.assertIsNone(f.sub)
        disk = r8.on_disk(self.folder)["progress"]
        self.assertEqual((disk["perks"], disk["offers"]), (["keen_eye"], []))
        frame(g)
        self.assertNotIn(rs.PERK_HUD, [t for t, _r in f.hud_words])

    def test_later_and_escape_keep_the_perk_waiting(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        f.game.save.progress = {"xp": 80, "perks": [], "offers": [["stout", "keen_eye", "hoard"]]}
        f.sub, f.perk_focus = "perk", 0
        frame(g)
        self.press(g, "perk_later")
        self.assertIsNone(f.sub)
        f.sub = "perk"
        frame(g)
        key(g, pygame.K_ESCAPE)
        self.assertIsNone(f.sub)
        self.assertEqual(levels.offers(f.game.save), [["stout", "keen_eye", "hoard"]])
        f.sub = "perk"
        frame(g)
        key(g, pygame.K_3)                                       # a number takes that perk
        self.assertEqual(f.game.save.progress["perks"], ["hoard"])

    def test_a_quest_handed_in_gives_xp(self):
        r8.new_game(None)
        game, _n = adv.Game.load(1, pool=self.pool)
        game.save.progress = {"xp": 60, "perks": [], "offers": []}
        lines = adv.quest_xp(game)
        self.assertEqual(game.save.progress["xp"], 110)                  # rc16 (expected change): XP x2 - a quest is 50 (was 85 / +25)
        self.assertEqual(lines[0], "+50 XP.")
        self.assertIn("Level up! Level 3", lines[1])

    def test_keen_eye_offers_four_and_the_corner_panel_shows_the_level(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        self.at_xp(g, 40, perks=["keen_eye"])
        frame(g)
        self.assertIn("Level 2  XP 10/45", [t for t, _r in f.hud_words])
        f = self.duel_to_result(g, won=True)
        self.assertTrue(all(len(grp) == 4 for grp in f.results.offers), f.results.offers)


# ==================================================================================================================================
class SaveTests(r8.Base):
    def test_a_save_from_before_prog3_loads_at_level_1_with_0_xp(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        d = json.load(open(path, encoding="utf-8"))
        d.pop("progress", None)
        json.dump(d, open(path, "w", encoding="utf-8"))
        game, _n = adv.Game.load(1, pool=self.pool)
        self.assertEqual(game.save.progress, {"xp": 0, "perks": [], "offers": []})
        self.assertEqual(levels.level(game.save), 1)
        self.assertEqual(levels.duel_effects(game.save), {})
        self.assertEqual(game.panel_info()["level"], "Level 1  XP 0/30")

    def test_a_damaged_progress_is_mended(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        d = json.load(open(path, encoding="utf-8"))
        d["progress"] = {"xp": "lots", "perks": ["stout"], "offers": "x"}
        json.dump(d, open(path, "w", encoding="utf-8"))
        game, _n = adv.Game.load(1, pool=self.pool)
        self.assertEqual(game.save.progress, {"xp": 0, "perks": ["stout"], "offers": []})


# ==================================================================================================================================
class DataTests(unittest.TestCase):
    def data(self):
        return json.load(open(levels.PROGRESSION_FILE, encoding="utf-8"))

    def test_the_numbers_are_read(self):
        lv = levels.read()
        self.assertEqual((lv.first, lv.step, lv.cap, lv.life_per_level, lv.perk_every, lv.perk_choices), (30, 15, 20, 1, 3, 3))
        self.assertTrue(levels.read().perks["stout"].stackable)
        self.assertEqual({p.id for p in lv.perks.values() if not p.available}, {"haggler", "fleet_foot"})   # PROG4: Pack Mule is offered
        progress.read()                                          # PROG1's reader still takes the file

    def test_a_bad_key_is_named(self):
        for path, value, word in ((("xp", "boss"), -1, "xp.boss"), (("levels", "cap"), 0, "levels.cap"),
                                  (("perks", "stout", "effect"), {"mana": 1}, "mana"), (("xp", "ogre"), 5, "ogre")):
            d = self.data()
            t = d
            for k in path[:-1]:
                t = t[k]
            t[path[-1]] = value
            with self.subTest(path=path):
                with self.assertRaises(ValueError) as cm:
                    levels.Levels(d)
                self.assertIn(word, str(cm.exception))


# ==================================================================================================================================
class FitTests(PlayedBase):
    def test_the_results_screen_with_a_level_up_and_the_perk_window_fit(self):
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                r8.new_game(None)
                g = self.gui(size=size, scale=scale)
                f = self.open(g, slot=1)
                self.on_map(g, "ashfen_outskirts")
                f.game.save.progress = {"xp": 70, "perks": [], "offers": []}
                f.game.write()
                f = self.duel_to_result(g, won=True)
                key(g, pygame.K_RETURN)
                frame(g)
                W, H = g.screen.get_size()
                res = f.results
                self.assertTrue(pygame.Rect(0, 0, W, H).contains(res.rect))
                self.assertTrue(res.rect.contains(res.xp_rect))
                self.assertIn("A perk to choose: Menu.", res.xp_words)
                for r, _n in f.buttons:
                    self.assertTrue(res.rect.contains(r) or r.size == (W, H), r)
                key(g, pygame.K_RETURN)                                  # take the cards
                f.sub = "perk"
                frame(g)
                box = f.rect
                self.assertTrue(pygame.Rect(0, 0, W, H).contains(box), (box, size, scale))
                for r, n in f.buttons:
                    self.assertTrue(box.contains(r), (n, r, box))
                self.assertEqual(len([n for _r, n in f.buttons if n.startswith("perk:")]), 3)
                self.close(g)


# ==================================================================================================================================
DECK = os.path.join(BASE, "sample_decks", "standard_red_aggro.txt")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/ (live Forge)")
class LiveTests(unittest.TestCase):
    E = {"life": 27, "hand": 9, "battlefield": ["Sol Ring", se.TREASURE], "command": ["Lightning Bolt"]}

    def decks(self, effects):
        import deck_importer
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        c, cards = deck_importer.import_from_text(open(DECK, encoding="utf-8").read(), fmt="standard")
        mp = fc.write_deck_file(os.path.join(d, "player.dck"), c, cards, "P", fmt="standard", effects=effects)
        op = fc.write_deck_file(os.path.join(d, "opp.dck"), c, cards, "O", fmt="standard")
        return mp, op

    def to_keep(self, s, limit=150):
        t0 = time.time()
        while time.time() - t0 < limit:
            s.poll()
            if s.fatal:
                return None
            st = s.state
            msg = ((st or {}).get("prompt") or {}).get("message") or ""
            if "keep your hand" in msg.lower():
                return st
            if "play or draw" in msg.lower():
                s.ok()
                time.sleep(0.5)
            time.sleep(0.1)
        self.fail("never asked to keep the hand")

    @staticmethod
    def seat(st, mine=True):
        return next(p for p in st["players"] if (p["id"] == st["me"]) == mine)

    @staticmethod
    def zone(p, z):
        return [c.get("name") for c in (p.get("zones") or {}).get(z) or ()]

    def test_each_effect_in_a_real_game_and_the_foe_untouched(self):
        mp, op = self.decks(self.E)
        s = fc.ForgeSession(mp, [op], name="Me", seed=5).start()
        self.addCleanup(s.close)
        st = self.to_keep(s)
        self.assertIsNotNone(st, s.fatal)
        self.assertEqual(se.normal(s.effects_used), self.E)
        me, foe = self.seat(st), self.seat(st, False)
        self.assertEqual(me["life"], 27)
        self.assertEqual(me["handCount"], 9)
        self.assertEqual(sorted(self.zone(me, "battlefield")), ["Sol Ring", "Treasure Token"])
        self.assertEqual(self.zone(me, "command"), ["Lightning Bolt"])
        self.assertEqual(foe["life"], 20)
        self.assertEqual(foe["handCount"], 7)
        self.assertEqual(self.zone(foe, "battlefield"), [])
        self.assertEqual(self.zone(foe, "command"), [])

    def test_a_game_started_from_the_saved_dck_keeps_them(self):
        # Resume, Undo's rebuild and a bug report's replay all start from the saved .dck files: no effects passed here
        mp, op = self.decks({"life": 31, "hand": 6})
        s = fc.ForgeSession(mp, [op], name="Me", seed=9).start()
        self.addCleanup(s.close)
        st = self.to_keep(s)
        self.assertIsNotNone(st, s.fatal)
        self.assertEqual((self.seat(st)["life"], self.seat(st)["handCount"]), (31, 6))

    def test_an_unknown_card_is_a_fatal_error(self):
        mp, op = self.decks({"battlefield": ["No Such Card At All"]})
        s = fc.ForgeSession(mp, [op], name="Me", seed=5).start()
        self.addCleanup(s.close)
        t0 = time.time()
        while time.time() - t0 < 120 and not s.fatal:
            s.poll()
            time.sleep(0.1)
        self.assertIn("No Such Card At All", s.fatal or "")

    def test_duels_applies_seat_as_effects(self):
        from tools import rpg_balance
        mp, op = self.decks(None)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        games = rpg_balance.run_pairs([("x", mp, op, 1, 3, "life:55;battlefield:Sol Ring")], d, workers=1, log=lambda *_a: None)
        self.assertEqual(len(games), 1, games)
        self.assertNotIn("error", games[0])
        log = open(os.path.join(d, "java_0.log"), encoding="utf-8", errors="replace").read()
        self.assertIn('starteffects: life=55', log)


if __name__ == "__main__":
    unittest.main()
