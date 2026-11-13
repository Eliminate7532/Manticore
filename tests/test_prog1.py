# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG1 (Karl, 2 Nov 2026: "My main idea is to add ways to make progression more tangible and 'fun' JRPG style"; the design:
the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.1). Through the Adventure window where it can be:

  1  ResultsTests     after a won / lost / drawn / conceded duel the results screen opens back on the Adventure screen: the
                      banner, the gold counting from before to after, each reward card turning with its tier's glow and NEW!; a
                      tap, Space or Enter finishes it, the next continues; the result's words are the old window's
  2  FoundLogTests    the Card Book's log in the save: written when a card first comes in; a save from before loads, its cards
                      "found before the Card Book" and never NEW
  3  BookTests        the Card Book from the pause window: every card ever owned, its tier, copies, where and when found;
                      colour and tier filters by tap; a tap shows a card big
  4  BeastTests       the bestiary: "???" for a kind never met; after a won and a lost duel its record and the cards it gave
  5  MeterTests       the deck-power formula (one function), on the map's corner window and in the deck editor, "+0.1" after a
                      change for a few seconds
  6  DataTests        rpg/data/progression.json is read and checked; tier colours and the NEW rule are one place each
  7  FitTests         every new screen inside the window at 1360x840, 1920x1080, 2736x1824 and at 200% text

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest tests.test_prog1
"""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import pygame

import gfx
import rpg_results
import rpg_screens as rs
from rpg import adventure as adv
from rpg import encounters, progress
from rpg import save as sv
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame, key
from tests.test_rpg14 import WindowBase

PROG_SIZES = (((1360, 840), None), ((1920, 1080), None), ((2736, 1824), None), ((1360, 840), 2.0), ((1920, 1080), 2.0),
              ((2736, 1824), 2.0))


class PlayedBase(WindowBase):
    """The results screen plays out here (other tests open it finished: tests/test_rpg8.Base)."""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(rpg_results, "AT_ONCE", False))

    def duel_to_result(self, g, won=True, foe=None):
        """A duel through the window (the encounter screen, Duel, the stand-in engine's end, Continue) - the window it comes
        back to."""
        f = g.modal
        self.meet(g, foe or encounters.RandomFoe("ember_bandit"))
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=won)
        g.end_continue()
        frame(g)
        return g.modal


class ResultsTests(PlayedBase):
    def test_a_won_duel_opens_the_results_screen_counting_the_gold_and_turning_the_cards(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        f.game.save.world["first_win_day"] = 10 ** 6        # Round PROG5 (expected change): the day's first win paid already, so the duel's own gold and XP are what is tested
        gold0 = f.game.save.gold
        f = self.duel_to_result(g, won=True)
        self.assertEqual(f.sub, "result")
        res = f.results
        self.assertIsNotNone(res, "no results screen after a won duel")
        info = res.info
        self.assertEqual(info["banner"], "VICTORY")
        self.assertEqual((info["gold_before"], info["gold_after"]), (gold0, f.game.save.gold))
        self.assertGreater(info["gold_change"], 0)
        self.assertGreaterEqual(len(info["offers"]), 1)          # Round PROG2: the reward cards are a choice (rpg.rewards)
        self.assertEqual(f.result[1], adv.outcome_lines(dict(result="won", enemy="Ember Bandit", gold=info["gold_change"],
                                                             cards=[], offers=[[c["name"] for c in grp]
                                                                               for grp in info["offers"]])))
        t0 = res.t0
        self.assertEqual(res.gold_shown(t0), gold0)                                       # it starts from what you carried
        mid = t0 + res.n.banner_seconds + res.n.gold_count_seconds / 2
        self.assertTrue(gold0 < res.gold_shown(mid) < f.game.save.gold)                 # ... and counts up
        self.assertEqual(res.gold_shown(t0 + res.length), f.game.save.gold)
        self.assertEqual(res.card_turn(0, t0), 0.0)
        self.assertEqual(res.card_turn(0, t0 + res.length), 1.0)
        self.assertEqual([n for _r, n in f.buttons], ["results_skip"])                   # a tap anywhere skips
        key(g, pygame.K_RETURN)                                                          # finishes the count at once
        self.assertEqual(f.sub, "result")
        self.assertTrue(res.done(time.monotonic()))
        frame(g)
        self.assertIn("ok", [n for _r, n in f.buttons])                                 # Continue shows
        key(g, pygame.K_RETURN)                                                          # ... and the next one continues
        self.assertIsNone(f.sub)
        self.assertIsNone(f.results)

    def test_a_lost_duel_counts_the_gold_down_and_says_where_you_wake(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        f.game.save.gold = 200
        f = self.duel_to_result(g, won=False)
        info = f.results.info
        self.assertEqual(info["banner"], "DEFEAT")
        self.assertEqual(info["gold_change"], -20)                      # GOLD1: a random foe is a dungeon fight, 10% (was 20%)
        self.assertEqual((info["gold_before"], info["gold_after"]), (200, 180))
        self.assertEqual(info["cards"], [])
        self.assertTrue(any(ln.startswith("You lost 20 gold") for ln in info["lines"]), info["lines"])
        self.assertEqual(f.result[0], "Defeat")

    def test_a_draw_and_a_concede_open_it_too(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = g.modal
        for result, banner in (("draw", "DRAW"), ("conceded", "DEFEAT")):
            with self.subTest(result=result):
                adv.start_duel(f.game, encounters.RandomFoe("ember_bandit"))
                out = adv.finish_loaded(f.game, result)
                f.open_results(g, out, adv.outcome_lines(out, f.game.world))
                f.result, f.sub = ("x", adv.outcome_lines(out, f.game.world), rs.GOLD), "result"
                self.assertEqual(f.results.info["banner"], banner)
                frame(g)
                click(g, (5, 5))                                          # a tap anywhere: finished
                frame(g)
                click(g, (5, 5))                                          # again: continues
                self.assertIsNone(f.sub)

    def test_a_tap_on_continue_closes_it_once_finished(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = self.duel_to_result(g, won=True)
        click(g, (10, 10))
        frame(g)
        self.press(g, "ok")
        self.assertIsNone(f.sub)

    def test_a_card_never_owned_is_new_and_one_owned_is_not(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        s = f.game.save
        owned = s.collection.names()[0]
        self.assertFalse(progress.is_new(s, owned))
        never = next(n for n in ("Lightning Bolt", "Shock", "Counterspell", "Giant Growth")
                     if (s.collection.count(n) or 0) == 0 and n not in [e["name"] for e in s.found.values()])
        self.assertTrue(progress.is_new(s, never))
        s.collection.add(never, 1)
        self.assertFalse(progress.is_new(s, never))
        s.collection.remove(never, 1)                                    # sold: still not new (it was owned)
        self.assertFalse(progress.is_new(s, never))
        before_found = set(s.found)
        before_owned = {progress.cardpool.normal_name(n) for n in s.collection.names()}
        f = self.duel_to_result(g, won=True)
        for c in f.results.info["cards"]:
            k = progress.cardpool.normal_name(c["name"])
            self.assertEqual(c["new"], k not in before_found and k not in before_owned, c)
            self.assertIn(k, f.game.save.found)                          # written down as it came in

    def test_the_cards_glow_in_their_tiers_colour_and_say_new(self):
        g, _l = self.setup_game(size=(1920, 1080))
        f = self.on_map(g, "ashfen_outskirts")
        info = progress.results({"result": "won", "gold": 30, "cards": [("Shock", "deck"), ("Counterspell", "deck")],
                                 "new": ["Shock"]}, ["You beat the X: +30 gold.", "New cards: Shock, Counterspell."],
                                f.game.save.gold, f.game.tiers())
        f.results = rpg_results.ResultsScreen(info, time.monotonic() - 60, pool=f.game.pool,
                                              picture=lambda n, c, w, h: rs.card_picture(g, n, c, w, h))
        f.result, f.sub = ("Victory", info["lines"], rs.GREEN), "result"
        frame(g)
        scr = g.screen
        for c, r in zip(info["cards"], f.results.card_rects):
            k = max(1, rpg_results.unit_for(g) // 8)
            edge = r.inflate(4 * k, 4 * k)
            self.assertEqual(tuple(scr.get_at((edge.x + 1, edge.centery)))[:3], rpg_results.tier_colour(c["tier"]))
        self.assertEqual([c["new"] for c in info["cards"]], [True, False])
        self.assertNotIn("New cards: Shock, Counterspell.", f.results.text_lines())    # the cards are shown, not listed

    def test_the_xp_slot_is_kept_and_now_holds_the_xp_bar(self):
        # Round PROG3 (expected change): PROG1 kept this slot empty for prog3; it now holds "Level n", the bar and "+20 XP".
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts").game.save.world["first_win_day"] = 10 ** 6     # Round PROG5 (expected change): the day's first win paid already, so the duel's own gold and XP are what is tested
        f = self.duel_to_result(g, won=True)
        key(g, pygame.K_SPACE)
        frame(g)
        r = f.results.xp_rect
        self.assertTrue(f.results.rect.contains(r))
        self.assertGreater(r.h, 0)
        self.assertTrue(r.contains(f.results.xp_bar_rect))
        self.assertEqual(f.results.xp_words[:2], ["Level 2", "+40 XP"])           # a random foe is a dungeon fight: 40 XP (rc16, expected change: XP x2 - was "Level 1", "+20 XP"; 40 reaches level 2 at 30)

    def test_with_animations_off_it_opens_finished(self):
        g, _l = self.setup_game()
        g.animations = False
        self.on_map(g, "ashfen_outskirts")
        f = self.duel_to_result(g, won=True)
        self.assertTrue(f.results.done(time.monotonic()))
        key(g, pygame.K_RETURN)
        self.assertIsNone(f.sub)

    def test_the_table_end_screen_still_says_the_same_words(self):
        out = {"result": "won", "enemy": "Ember Bandit", "gold": 31, "cards": [("Shock", "deck")], "new": ["Shock"]}
        self.assertEqual(adv.outcome_lines(out), ["You beat the Ember Bandit: +31 gold.", "New card: Shock."])

    def test_skipping_costs_two_presses_and_the_whole_show_a_few_seconds(self):
        n = progress.read()
        self.assertLessEqual(n.results_seconds(1), 3.0)                  # a regular win
        self.assertLessEqual(n.results_seconds(5), 4.0)                  # a boss's five cards
        info = progress.results({"result": "won", "gold": 10, "cards": [("Shock", "deck")]}, ["x"], 50)
        res = rpg_results.ResultsScreen(info, 100.0, numbers=n)
        self.assertEqual(res.tap(100.0), "skipped")
        self.assertEqual(res.tap(100.0), "continue")


class FoundLogTests(r8.Base):
    def test_a_new_card_is_written_down_with_the_day_and_the_place(self):
        game = r8.new_game(None)
        s = game.save
        self.assertTrue(s.found, "the starting deck isn't in the Card Book's log")
        s.world["clock"] = 3 * 24 * 60 + 600                             # Day 4
        s.world["map"] = "warrens_1"
        s.collection.add("Lightning Bolt", 1)
        e = s.found[progress.cardpool.normal_name("Lightning Bolt")]
        self.assertEqual((e["day"], e["map"]), (4, "warrens_1"))
        s.collection.add("Lightning Bolt", 1)                            # the first time only
        self.assertEqual(s.found[progress.cardpool.normal_name("Lightning Bolt")]["day"], 4)
        game.write()
        back = adv.Game.load_raw(1, None, pool=self.pool)[0].save
        self.assertIn(progress.cardpool.normal_name("Lightning Bolt"), back.found)

    def test_a_save_from_before_the_round_loads_its_cards_found_before_the_card_book(self):
        game = r8.new_game(None)
        d = game.save.to_dict()
        d.pop("found")
        d.pop("bestiary")
        s = sv.SaveGame.from_dict(d, pool=self.pool)
        self.assertEqual((s.found, s.bestiary), ({}, {}))
        owned = s.collection.names()
        rows = progress.book_rows(s, self.pool, game.tiers(), deck=s.deck())
        self.assertEqual({r.name for r in rows} >= set(owned), True)
        self.assertTrue(all(r.found == progress.BEFORE for r in rows))
        self.assertFalse(any(progress.is_new(s, n) for n in owned))      # owned before: never NEW
        s.collection.add("Lightning Bolt", 1)                            # a card after the round: written down
        self.assertIn(progress.cardpool.normal_name("Lightning Bolt"), s.found)

    def test_a_damaged_log_is_mended_not_refused(self):
        game = r8.new_game(None)
        d = game.save.to_dict()
        d["found"] = {"shock": {"name": 5}, "bolt": {"name": "Lightning Bolt", "day": 2, "map": "x", "town": None}}
        d["bestiary"] = ["nonsense"]
        s = sv.SaveGame.from_dict(d, pool=self.pool)
        self.assertEqual(list(s.found), ["bolt"])
        self.assertEqual(s.bestiary, {})

    def test_reading_a_save_writes_nothing_new_in_the_log(self):
        game = r8.new_game(None)
        d = game.save.to_dict()
        d["found"] = {}
        s = sv.SaveGame.from_dict(d, pool=self.pool)
        self.assertEqual(s.found, {})


class BookTests(WindowBase):
    def open_book(self, g):
        f = g.modal
        key(g, pygame.K_ESCAPE)
        self.assertEqual(f.sub, "pause")
        self.press(g, "book")                                            # a tap on the menu's line
        frame(g)
        self.assertEqual(f.stage, "book")
        return f

    def test_the_card_book_lists_every_card_ever_owned_with_where_it_was_found(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        s = f.game.save
        s.world["clock"] = 24 * 60 + 600                                 # Day 2
        s.collection.add("Lightning Bolt", 1)
        s.collection.remove("Lightning Bolt", 1)                         # sold since: still in the book
        f = self.open_book(g)
        rows = progress.book_rows(s, f.game.pool, f.game.tiers(), deck=s.deck(), place_name=f.place_name)
        bolt = next(r for r in rows if r.name == "Lightning Bolt")
        self.assertEqual((bolt.owned, bolt.in_deck), (0, 0))
        self.assertEqual(bolt.found, "Day 2, " + f.place_name("ashfen_outskirts"))
        self.assertNotEqual(f.place_name("ashfen_outskirts"), "ashfen_outskirts")
        d = s.deck()
        name, k = d["main"][0]
        row = next(r for r in rows if r.name == name)
        self.assertGreaterEqual(row.in_deck, k)
        tiers = [r.tier if r.tier is not None else -1 for r in rows]
        self.assertEqual(tiers, sorted(tiers, reverse=True))            # best tier first

    def test_every_filter_button_works_by_tap(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = self.open_book(g)
        names = [n for _r, n in f.buttons]
        for c in adv.COLOURS:
            self.assertIn(f"bookc:{c}", names)
        for t in range(6):
            self.assertIn(f"bookt:{t}", names)
        self.press(g, "bookc:G")
        frame(g)
        self.assertEqual(f.book_colours, {"G"})
        rows = progress.book_rows(f.game.save, f.game.pool, f.game.tiers(), colours={"G"})
        self.assertTrue(all("G" in r.colours for r in rows))
        self.press(g, "bookc:G")
        self.assertEqual(f.book_colours, set())
        frame(g)
        self.press(g, "bookt:1")
        self.assertEqual(f.book_tier, 1)
        frame(g)
        self.press(g, "bookt:1")
        self.assertIsNone(f.book_tier)
        frame(g)
        line = next((r, d) for r, kind, d in f.rows if kind == "bookcard")
        click(g, line[0].center)                                         # a tap shows it big
        self.assertEqual(f.book_big[0], line[1][0])
        frame(g)
        click(g, line[0].center)                                         # again: not
        self.assertIsNone(f.book_big)
        frame(g)
        self.press(g, "book_done")
        self.assertEqual(f.stage, "map")

    def test_esc_leaves_the_book(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = self.open_book(g)
        key(g, pygame.K_ESCAPE)
        self.assertEqual(f.stage, "map")


class BeastTests(PlayedBase):
    def open_beasts(self, g):
        f = g.modal
        key(g, pygame.K_ESCAPE)
        self.press(g, "beasts")
        frame(g)
        self.assertEqual(f.stage, "beasts")
        return f

    def test_never_met_is_unknown_then_a_win_and_a_loss_fill_its_page(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = self.open_beasts(g)
        kinds = rs.rpg_enemy_kinds()
        rows = progress.bestiary_rows(f.game.save, kinds, [], slot=1)
        self.assertTrue(all(r.name == progress.UNMET for r in rows))
        self.press(g, "beasts_done")
        self.on_map(g, "ashfen_outskirts")
        f = self.duel_to_result(g, won=True)
        won = f.results.picked()                       # Round PROG2: the cards picked (the first of each group, unchanged)
        key(g, pygame.K_RETURN)
        key(g, pygame.K_RETURN)
        self.on_map(g, "ashfen_outskirts")
        f = self.duel_to_result(g, won=False)
        key(g, pygame.K_RETURN)
        key(g, pygame.K_RETURN)
        self.on_map(g, "ashfen_outskirts")
        f = self.open_beasts(g)
        import stats as gstats
        games = gstats.load(adv.duel_stats_dir(f.game.folder))
        rows = progress.bestiary_rows(f.game.save, kinds, games, slot=1, home_of=lambda k: encounters.kind_text(k).get("home"))
        bandit = next(r for r in rows if r.kind == "ember_bandit")
        self.assertEqual((bandit.met, bandit.won, bandit.lost), (True, 1, 1))
        self.assertEqual([n for n, _k in bandit.cards], won)
        self.assertTrue(bandit.home)
        others = [r for r in rows if r.kind != "ember_bandit"]
        self.assertTrue(all(r.name == progress.UNMET for r in others))
        key(g, pygame.K_RETURN)
        self.assertEqual(f.stage, "map")

    def test_a_save_from_before_counts_its_wins_from_the_record(self):
        game = r8.new_game(None)
        game.save.record["defeated"] = {"grave_robber": 3}
        rows = progress.bestiary_rows(game.save, rs.rpg_enemy_kinds(), [], slot=1)
        robber = next(r for r in rows if r.kind == "grave_robber")
        self.assertEqual((robber.met, robber.won, robber.cards), (True, 3, []))


class MeterTests(WindowBase):
    def test_the_formula_is_the_mean_tier_of_the_non_land_cards(self):
        game = r8.new_game(None)

        class T:
            def tier_of(self, n):
                return {"Shock": 1, "Counterspell": 3, "Forest": 0, "Llanowar Elves": 2}.get(n)
        cards = [("Shock", 4), ("Counterspell", 2), ("Forest", 17), ("Llanowar Elves", 2), ("No Such Card", 3)]
        self.assertAlmostEqual(progress.deck_power(cards, game.pool, T()), (4 * 1 + 2 * 3 + 2 * 2) / 8)
        self.assertIsNone(progress.deck_power([("Forest", 20)], game.pool, T()))
        self.assertEqual(progress.power_text(1.75), "Card quality 1.8")        # Round PROG1b: renamed
        self.assertEqual(progress.change_text(1.32, 1.44), "+0.1")
        self.assertEqual(progress.change_text(1.44, 1.32), "-0.1")
        self.assertIsNone(progress.change_text(1.41, 1.44))

    def test_the_map_corner_and_the_deck_editor_show_it_and_a_change(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        want = progress.power_text(progress.deck_power(f.game.deck()["main"], f.game.pool, f.game.tiers()))
        words = [t for t, _r in f.hud_words]
        self.assertIn(want, words)
        key(g, pygame.K_ESCAPE)
        self.press(g, "deck")
        frame(g)
        self.assertEqual(f.stage, "deck")
        e = f.editor
        tier = f.game.tiers().tier_of
        low = min(tier(n) for n in e.main if tier(n) and "Land" not in (f.game.pool.get(n).type_line or ""))
        for n in [n for n in e.main if tier(n) == low and "Land" not in (f.game.pool.get(n).type_line or "")]:
            while e.remove(n):                                           # the weakest cards out: the mean goes up
                pass
        t = [time.monotonic()]
        with mock.patch.object(rs.time, "monotonic", lambda: t[0]):
            frame(g)
            m = f.meters["deck"]
            info = f.power_info("deck", e.as_deck()["main"])
            self.assertTrue(info.get("power_up") or info.get("power_down"), info)
            self.assertRegex(info["power"], r"^Card quality \d\.\d  [+-]\d\.\d$")
            t[0] += progress.read().change_seconds + 0.1
            info = f.power_info("deck", e.as_deck()["main"])
            self.assertRegex(info["power"], r"^Card quality \d\.\d$")              # gone after a few seconds
            self.assertIs(m, f.meters["deck"])


class DataTests(unittest.TestCase):
    def test_progression_json_is_read_and_checked(self):
        n = progress.read()
        self.assertGreater(n.change_seconds, 0)
        with open(progress.PROGRESSION_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertIn("about", d)
        d["results"]["gold_count_seconds"] = "slow"
        with self.assertRaisesRegex(ValueError, "results.gold_count_seconds"):
            progress.Progression(d)

    def test_the_tier_colours_are_one_table(self):
        self.assertEqual(len(gfx.TIER_GLOW), 6)
        for t in range(6):
            self.assertEqual(rpg_results.tier_colour(t), gfx.TIER_GLOW[t])
        self.assertEqual(rpg_results.tier_colour(None), gfx.DB_DIM)

    def test_a_pack_uses_the_same_new_rule(self):
        import inspect
        from rpg import shop
        self.assertIn("progress.is_new", inspect.getsource(shop.Shop._add))      # Round PACK1: every kind of pack adds through _add


class FitTests(PlayedBase):
    def screens(self, g):
        """Every new screen drawn once (results, Card Book, bestiary): [(what, f.buttons, f.rect)]."""
        f = g.modal
        out = []
        info = progress.results({"result": "won", "gold": 200, "cards": [(n, "deck") for n in
                                 ("Shock", "Counterspell", "Giant Growth", "Lightning Bolt", "Dark Ritual")],
                                 "new": ["Shock", "Dark Ritual"]},
                                ["You beat the Grave Priest: +200 gold.", "New cards: Shock, Counterspell.",
                                 "The duel hall is lit."], f.game.save.gold, f.game.tiers())
        f.results = rpg_results.ResultsScreen(info, time.monotonic() - 60, pool=f.game.pool,
                                              picture=lambda n, c, w, h: rs.card_picture(g, n, c, w, h))
        f.result, f.sub = ("Victory", info["lines"], rs.GREEN), "result"
        frame(g)
        out.append(("results", list(f.buttons), f.results.rect, list(f.results.card_rects)))
        key(g, pygame.K_RETURN)
        for st in ("book", "beasts"):
            key(g, pygame.K_ESCAPE)
            self.press(g, st)
            frame(g)
            out.append((st, list(f.buttons), f.rect, []))
            key(g, pygame.K_ESCAPE)
        return out

    def test_every_new_screen_fits(self):
        r8.new_game(None)
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                g = self.gui(size=size, scale=scale)
                self.open(g, slot=1)
                self.on_map(g, "ashfen_outskirts")
                whole = g.screen.get_rect()
                for what, buttons, rect, cards in self.screens(g):
                    self.assertTrue(whole.contains(rect), f"{what} {rect}")
                    for r, n in buttons:
                        if n != "ok" or r != whole:
                            self.assertTrue(whole.contains(r), f"{what}: {n} {r}")
                    for c in cards:
                        self.assertTrue(rect.contains(c), f"{what}: a card {c} outside {rect}")
                    small = [r for r, n in buttons if r != whole]
                    for i, a in enumerate(small):
                        for b in small[i + 1:]:
                            self.assertFalse(a.colliderect(b), f"{what}: buttons overlap {a} {b}")
                self.close(g)


if __name__ == "__main__":
    unittest.main()
