# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG5 (Karl, 2 Nov 2026, answer 10: "All four" extra sources; the design: the Project doc
claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.5). Groups:

  1  DataTests      progression.json's first_win, bounties, trade_up and gauntlet are read and checked (a bad key is named); every
                    job's foe kind and map exist; the gauntlet's foes are regular region-1 kinds; the clerk and the warden stand in
                    Lantern Hollow with their words
  2  FirstWinTests  the first won duel of each in-game day adds 50 gold and 10 XP (once a day; a loss or a draw never), said on
                    the results screen, counted in its gold and its XP bar
  3  BountyTests    the board: 3 jobs at a time, a new one each in-game day (the oldest not done goes when it is full); a won duel
                    counts for every job it meets (a kind, a win at night, a win in the Warrens); the clerk pays a job done (gold,
                    XP or a card your deck can cast) and it leaves; the clerk's pages fit the dialogue box; the Menu lists the jobs
  4  TradeTests     Trade up: 4 copies of a tier-1 or tier-2 card -> 1 random card a tier higher your deck can cast; never 3 -> 4
                    in region 1; copies a deck uses come out of it (the sideboard first), said as a sale says it
  5  GauntletTests  the entry is paid; its duels pay no foe gold or cards; a loss there costs no gold and moves you nowhere; prizes
                    by wins (0, 1, 2, 3); "Stop here" pays for the wins so far; a game closed half-way resumes (never a refund)
  6  WindowTests    by tap only: a tap on the clerk walks up and talks (the board's pages, a pay-out); the Trade up tab's button;
                    the warden's Enter -> the encounter -> the duel -> the results -> the warden asks for the next duel
  7  SaveTests      a save from before PROG5 loads (no first win, no board, no gauntlet); damaged values are mended
  8  FitTests       the Trade up tab (scrolled too), the results screen with the first-win and gauntlet lines, the clerk's and the
                    warden's boxes inside the window at 1360x840, 1920x1080 and 2736x1824, each at 100% and 200% text

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest tests.test_prog5
"""
import json
import os
import time
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import adventure as adv
from rpg import clock, encounters, explore, extras, levels, rewards, talk
from rpg import save as sv
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame
from tests.test_prog1 import PROG_SIZES, PlayedBase
from tests.test_rpgtap import TapBase
from tests.test_rpg14 import TOWN

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = clock.DAY


def at_day(game, day, hour=10):
    """The save's clock set to `hour`:00 on in-game day `day` (1 = the first)."""
    game.save.world["clock"] = (day - 1) * DAY + hour * 60


class SaveBase(r8.Base):
    def game(self, seed=7):
        r8.new_game(None, seed=seed)
        game, _n = adv.Game.load(1, pool=self.pool)
        at_day(game, 1)
        return game

    def finish(self, game, kind="ember_bandit", encounter="regular", result="won", **extra):
        game.save.pending_duel = dict({"kind": kind, "encounter": encounter, "deck": 0, "source": "jumpstart", "map": None,
                                       "x": None, "y": None, "started": 0}, **extra)
        out = adv.finish_loaded(game, result)
        game.save.pending_pick = None
        return out


# ==================================================================================================================================
class DataTests(unittest.TestCase):
    def test_the_four_tables_are_read(self):
        ex = extras.read()
        self.assertEqual((ex.first_gold, ex.first_xp), (50, 10))
        self.assertEqual(ex.slots, 3)
        self.assertGreaterEqual(len(ex.jobs), ex.slots + 2)            # a new job can always differ from the ones posted
        self.assertEqual((ex.trade_copies, ex.trade_past_band), (4, False))
        self.assertEqual((ex.fee, ex.duels), (50, 3))
        self.assertEqual(ex.prize(0), {})
        self.assertEqual(ex.prize(1), {"gold": 25})
        self.assertEqual(ex.prize(2), {"gold": 50, "pack": True})
        self.assertEqual(ex.prize(3), {"gold": 150, "card": "rare_from_band", "xp": 40})

    def test_bad_keys_are_named(self):
        good = json.load(open(extras.PROGRESSION_FILE, encoding="utf-8"))
        cases = ((lambda d: d["first_win"].update(gold=-5), "first_win.gold"),
                 (lambda d: d["bounties"]["jobs"]["night"]["goal"].update(phase="noon"), "phase"),
                 (lambda d: d["bounties"]["jobs"]["night"].update(pays=1), "pays"),
                 (lambda d: d["bounties"]["jobs"]["night"]["reward"].update(card="any"), "card"),
                 (lambda d: d["bounties"]["jobs"]["night"].update(reward={}), "pays something"),
                 (lambda d: d["trade_up"].update(copies=1), "trade_up.copies"),
                 (lambda d: d["gauntlet"]["prizes"].update({"7": {}}), "'7'"),
                 (lambda d: d["gauntlet"].update(foes=[]), "gauntlet.foes"),
                 (lambda d: d["gauntlet"]["prizes"]["2"].update(pack="M10"), "pack"))
        for change, word in cases:
            d = json.loads(json.dumps(good))
            change(d)
            with self.subTest(word=word):
                with self.assertRaises(ValueError) as cm:
                    extras.Extras(d)
                self.assertIn(word, str(cm.exception))

    def test_every_job_names_a_foe_kind_and_map_that_exist(self):
        kinds = {k["id"] for k in json.load(open(os.path.join(BASE, "rpg", "data", "enemy_kinds.json"), encoding="utf-8"))["kinds"]}
        maps = {f[:-4] for f in os.listdir(os.path.join(BASE, "rpg", "maps")) if f.endswith(".tmx")}
        for jid, j in extras.read().jobs.items():
            g = j["goal"]
            with self.subTest(job=jid):
                if "beat" in g:
                    self.assertIn(g["beat"], kinds)
                for m in (g.get("map") or "").split("|") if g.get("map") else ():
                    self.assertIn(m, maps)
        kinds_used = {j["goal"].get("beat") for j in extras.read().jobs.values()} - {None}
        self.assertTrue({"phase", "map"} <= {k for j in extras.read().jobs.values() for k in j["goal"]})
        self.assertTrue(kinds_used)
        self.assertEqual({k for j in extras.read().jobs.values() for k in j["reward"]}, {"gold", "xp", "card"})

    def test_the_gauntlets_foes_are_the_regions_regular_foes_with_jumpstart_decks(self):
        from rpg import enemies
        for kind in extras.read().foes:
            with self.subTest(kind=kind):
                _src, decks = enemies.deck_list(enemies.read_kind(kind), "jumpstart")
                self.assertTrue(decks, f"{kind} has no Jumpstart decks (region 1's regular foes play them)")

    def test_the_clerk_and_the_warden_stand_in_lantern_hollow_with_words(self):
        import xml.etree.ElementTree as ET
        root = ET.parse(os.path.join(BASE, "rpg", "maps", TOWN + ".tmx")).getroot()
        ids = {p.get("value") for o in root.iter("object") if o.get("class") == "npc" for p in o.iter("property")
               if p.get("name") == "id"}
        self.assertIn(talk.BOUNTY_CLERK, ids)
        self.assertIn(talk.GAUNTLET_WARDEN, ids)
        people = talk.npcs()
        self.assertEqual((people[talk.BOUNTY_CLERK]["name"], people[talk.GAUNTLET_WARDEN]["name"]), ("Clerk Ada", "Warden Holt"))
        self.assertEqual(talk.check(), [])

    def test_the_maps_are_built_from_their_layouts(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "rpg_make_maps.py"), "--check"], capture_output=True,
                           text=True, cwd=BASE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# ==================================================================================================================================
class FirstWinTests(SaveBase):
    def test_the_first_win_of_a_day_adds_gold_and_xp_once(self):
        game = self.game()
        s = game.save
        g0, xp0 = s.gold, s.progress["xp"]
        out = self.finish(game)
        own = out["gold"] - 50
        self.assertEqual(out["first_win"], {"gold": 50, "xp": 10, "day": 1})
        self.assertEqual(s.gold, g0 + out["gold"])
        self.assertTrue(40 <= own <= 80)                                   # numbers.json's regular win gold, untouched (rc16, expected change: Karl's option A gold, was 20-40)
        self.assertEqual(out["xp"]["gained"], 20 + 10)                     # a regular win's XP and the bonus (rc16: XP x2 - the win 20, was 10; the bonus stays 10)
        self.assertEqual(s.progress["xp"], xp0 + 30)
        lines = adv.outcome_lines(out, game.world)
        self.assertEqual(lines[0], f"You beat the Ember Bandit: +{own} gold.")
        self.assertEqual(lines[1], "First win of the day: +50 gold and +10 XP.")
        out2 = self.finish(game)                                           # the same day: no bonus
        self.assertNotIn("first_win", out2)
        self.assertEqual(out2["xp"]["gained"], 20)                         # rc16: XP x2, was 10
        self.assertFalse(any("First win" in ln for ln in adv.outcome_lines(out2, game.world)))

    def test_a_new_day_pays_again_and_a_loss_or_draw_never(self):
        game = self.game()
        self.finish(game)
        at_day(game, 2, hour=3)                                            # past midnight: a new day (before the inn's 07:00)
        for result in ("lost", "conceded", "draw"):
            self.assertNotIn("first_win", self.finish(game, result=result))
        out = self.finish(game)
        self.assertEqual(out["first_win"]["day"], 2)
        self.assertEqual(game.save.world["first_win_day"], 2)
        self.assertEqual(r8.on_disk(self.folder)["world"]["first_win_day"], 2)

    def test_the_results_screen_counts_the_bonus_in_the_gold_and_the_xp_bar(self):
        from rpg import progress
        game = self.game()
        g0 = game.save.gold
        out = self.finish(game)
        info = progress.results(out, adv.outcome_lines(out, game.world), game.save.gold, game.tiers())
        self.assertEqual((info["gold_before"], info["gold_after"]), (g0, game.save.gold))
        self.assertEqual(info["xp"]["gained"], 30)                         # rc16 (expected change): 20 for the win (XP x2) + the 10 bonus, was 20
        self.assertIn("First win of the day: +50 gold and +10 XP.", info["lines"])


# ==================================================================================================================================
class BountyTests(SaveBase):
    def test_a_first_visit_fills_the_board_and_each_new_day_posts_one(self):
        game = self.game()
        s = game.save
        posted = extras.post_bounties(s)
        self.assertEqual(len(posted), 3)
        self.assertEqual(len({j["job"] for j in s.world["bounties"]["jobs"]}), 3)
        self.assertEqual(extras.post_bounties(s), [])                      # the same day: nothing more
        at_day(game, 2)
        new = extras.post_bounties(s)                                      # full: the oldest job not done goes
        self.assertEqual(len(new), 1)
        jobs = s.world["bounties"]["jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[-1], new[0])
        self.assertNotIn(posted[0]["id"], [j["id"] for j in jobs])
        at_day(game, 9)                                                    # many days away: at most a board's worth
        self.assertEqual(len(extras.post_bounties(s)), 3)

    def test_a_job_done_stays_until_it_is_paid(self):
        game = self.game()
        s = game.save
        extras.post_bounties(s)
        b = s.world["bounties"]
        ex = extras.read()
        for j in b["jobs"]:
            j["count"] = extras.job_need(ex, j)                           # all three done, waiting to be paid
        at_day(game, 2)
        self.assertEqual(extras.post_bounties(s), [])                      # nothing done is pushed off the board
        self.assertEqual(len(b["jobs"]), 3)

    def board(self, game, *jobs):
        game.save.world["bounties"] = {"day": 1, "next": len(jobs),
                                       "jobs": [{"id": f"b{i}", "job": j, "count": 0, "posted": 1} for i, j in enumerate(jobs)]}
        return game.save.world["bounties"]["jobs"]

    def test_a_won_duel_counts_for_the_jobs_it_meets(self):
        game = self.game()
        jobs = self.board(game, "bandits", "night", "warrens")
        at_day(game, 1, hour=12)
        out = self.finish(game, "ember_bandit", map="ashfen_outskirts")
        self.assertEqual([j["count"] for j in jobs], [1, 0, 0])
        self.assertNotIn("bounty_lines", out)
        at_day(game, 1, hour=22)                                           # night
        self.finish(game, "grave_robber", map="ashfen_outskirts")
        self.assertEqual([j["count"] for j in jobs], [1, 1, 0])
        out = self.finish(game, "ember_bandit", "dungeon", map="warrens_1")
        self.assertEqual([j["count"] for j in jobs], [2, 1, 1])
        self.assertIn("Bounty done: Beat 2 Ember Bandits. The clerk in Lantern Hollow pays.", adv.outcome_lines(out, game.world))
        self.finish(game, "ember_bandit", "dungeon", map="warrens_1")      # done jobs count no further
        self.assertEqual([j["count"] for j in jobs], [2, 1, 1])
        self.finish(game, "ember_bandit", result="lost")                   # a loss counts for nothing
        self.assertEqual([j["count"] for j in jobs], [2, 1, 1])

    def test_the_clerk_pays_gold_xp_and_a_card_your_deck_can_cast(self):
        game = self.game()
        s = game.save
        jobs = self.board(game, "night", "thornback", "rats")
        for j in jobs:
            j["count"] = extras.job_need(extras.read(), j)
        g0, xp0, owned0 = s.gold, s.progress["xp"], sum(s.collection.count(n) or 0 for n in s.collection.names())
        paid = extras.collect_bounties(game)
        self.assertEqual([t for t, _l in paid], ["Win a duel at night", "Beat a Thornback", "Beat 2 Plague Rats"])
        self.assertEqual(s.gold, g0 + 50)
        self.assertEqual(s.progress["xp"], xp0 + 25)
        card_line = paid[2][1][0]
        self.assertTrue(card_line.startswith("New card: "))
        name = card_line[len("New card: "):-1]
        self.assertTrue(rewards.castable(game.pool.get(name), rewards.deck_colours(s.deck(), game.pool)))
        lo, hi = game.world.band(1)
        self.assertTrue(lo <= game.tiers().tier_of(name) <= hi)
        self.assertEqual(sum(s.collection.count(n) or 0 for n in s.collection.names()), owned0 + 1)
        self.assertEqual(s.world["bounties"]["jobs"], [])                  # paid jobs leave the board
        self.assertEqual(extras.collect_bounties(game), [])                # ... and pay once

    def test_the_clerks_pages_fit_the_dialogue_box(self):
        game = self.game()
        posted = extras.post_bounties(game.save)
        paid = [("Win 3 duels", ["+60 gold.", "+15 XP.", "Level up! Level 4: your duels start at 23 life (before perks)."])]
        for p in extras.split_pages(extras.board_pages(game.save, posted, paid)):
            self.assertLessEqual(len(talk.wrap(p)), talk.PAGE_LINES, p)
        self.assertEqual(extras.split_pages(["x " * 60]), extras.split_pages(["x " * 60]))
        self.assertEqual(len(extras.split_pages(["word " * 40])), 2)       # 40 words of 5: 2 pages of at most 3 lines

    def test_the_menu_lists_the_jobs(self):
        game = self.game()
        jobs = self.board(game, "bandits", "night")
        jobs[1]["count"] = 1
        self.assertEqual(extras.journal_lines(game.save), [("Bounty: Beat 2 Ember Bandits", "0 of 2"),
                                                           ("Bounty: Win a duel at night", "Done! Go back to the board.")])


# ==================================================================================================================================
class TradeTests(SaveBase):
    def pick(self, game, tier, k):
        """A card of `tier` given k times (its name)."""
        from rpg.shop import candidates
        name = candidates(tier, "", game.pool, game.tiers(), game.lists(), game.numbers.shop_skip_prefixes)[0]
        game.save.collection.add(name, k)
        return name

    def test_only_four_copies_of_a_tier_one_or_two_card_can_be_traded(self):
        game = self.game()
        s = game.save
        band = game.world.band(1)
        self.assertEqual(extras.trade_tiers(band), {1: 2, 2: 3})
        t1, t2, t3 = self.pick(game, 1, 4), self.pick(game, 2, 5), self.pick(game, 3, 6)
        rows = {r[0]: r for r in extras.trade_rows(s, game.tiers(), band)}
        self.assertIn(t1, rows)
        self.assertIn(t2, rows)
        self.assertNotIn(t3, rows)                                         # 3 -> 4 is past region 1's band
        self.assertFalse(any(s.collection.is_free(n) for n in rows))
        self.assertTrue(all(r[2] >= 4 for r in rows.values()))
        with self.assertRaises(extras.TradeError):
            extras.trade_up(s, t3, game.pool, game.tiers(), game.lists(), game.numbers, band)

    def test_a_trade_takes_four_and_gives_one_card_a_tier_higher_your_deck_can_cast(self):
        game = self.game()
        s = game.save
        band = game.world.band(1)
        for tier in (1, 2):
            name = self.pick(game, tier, 4)
            have = s.collection.count(name)
            got, to, taken = extras.trade_up(s, name, game.pool, game.tiers(), game.lists(), game.numbers, band)
            with self.subTest(tier=tier):
                self.assertEqual(to, tier + 1)
                self.assertEqual(game.tiers().tier_of(got), tier + 1)
                self.assertEqual(s.collection.count(name) or 0, have - 4)
                self.assertGreaterEqual(s.collection.count(got), 1)
                self.assertTrue(rewards.castable(game.pool.get(got), rewards.deck_colours(s.deck(), game.pool)))
                self.assertEqual(taken, [])

    def test_too_few_copies_are_refused(self):
        game = self.game()
        name = self.pick(game, 1, 3)
        with self.assertRaises(extras.TradeError) as cm:
            extras.trade_up(game.save, name, game.pool, game.tiers(), game.lists(), game.numbers, game.world.band(1))
        self.assertIn("a trade takes 4", str(cm.exception))

    def test_copies_a_deck_uses_come_out_of_it_sideboard_first(self):
        game = self.game()
        s = game.save
        name = self.pick(game, 1, 4)
        d = s.deck()
        d["main"].append([name, 2])
        d.setdefault("sideboard", []).append([name, 1])
        _got, _to, taken = extras.trade_up(s, name, game.pool, game.tiers(), game.lists(), game.numbers, game.world.band(1))
        self.assertEqual(taken, [(d["name"], 3)])
        self.assertFalse(any(r[0] == name for r in d["main"] + d["sideboard"]))


# ==================================================================================================================================
class GauntletTests(SaveBase):
    def duel(self, game, result):
        foe = extras.next_foe(game.save.world)
        return self.finish(game, foe, "regular", result=result, gauntlet=True)

    def test_the_entry_is_paid_and_too_little_gold_is_refused(self):
        game = self.game()
        s = game.save
        s.gold = 40
        with self.assertRaises(extras.TradeError) as cm:
            extras.enter_gauntlet(s)
        self.assertEqual(str(cm.exception), "The entry is 50 gold; you have 40.")
        s.gold = 120
        g = extras.enter_gauntlet(s)
        self.assertEqual(s.gold, 70)
        self.assertEqual((g["played"], g["wins"], len(g["foes"])), (0, 0, 3))
        self.assertTrue(set(g["foes"]) <= set(extras.read().foes))
        with self.assertRaises(extras.TradeError):
            extras.enter_gauntlet(s)                                       # one at a time

    def test_its_duels_pay_no_foe_gold_or_cards_and_a_loss_costs_nothing(self):
        game = self.game()
        s = game.save
        s.gold = 300
        s.world["first_win_day"] = 1                                       # the day's first win already paid
        extras.enter_gauntlet(s)
        where = dict(map=s.world.get("map"), x=s.world.get("x"), y=s.world.get("y"))
        out = self.duel(game, "won")
        self.assertEqual((out["gold"], out["cards"], out.get("offers")), (0, [], None))
        self.assertIsNone(s.pending_pick)
        self.assertEqual(s.gold, 250)
        self.assertEqual(out["xp"]["gained"], 20)                          # XP as for any regular win (rc16, expected change: XP x2, was 10)
        out = self.duel(game, "lost")
        self.assertEqual(s.gold, 250)                                      # no loss share
        self.assertEqual((s.world.get("map"), s.world.get("x"), s.world.get("y")), tuple(where.values()))
        self.assertNotIn("inn", out)
        lines = adv.outcome_lines(out, game.world)
        self.assertIn("A gauntlet loss costs no gold.", lines)
        self.assertIn("Gauntlet duel 2 of 3: lost. Wins so far: 1.", lines)
        self.assertFalse(any("You lost" in ln for ln in lines))

    def test_prizes_by_wins(self):
        for wins, gold, xp_more, pack, rare in ((0, 0, 0, False, False), (1, 25, 0, False, False), (2, 50, 0, True, False),
                                                (3, 150, 40, False, True)):
            with self.subTest(wins=wins):
                game = self.game(seed=11 + wins)
                s = game.save
                s.gold = 100
                s.world["first_win_day"] = 99                              # no first-win bonus in the count
                extras.enter_gauntlet(s)
                cards0 = sum(s.collection.count(n) or 0 for n in s.collection.names())
                results = ["won"] * wins + ["lost"] * (3 - wins)
                for r in results[:-1]:
                    self.duel(game, r)
                xp0 = s.progress["xp"]
                out = self.duel(game, results[-1])
                self.assertTrue(out["gauntlet"]["done"])
                self.assertIsNone(extras.gauntlet_state(s.world))
                self.assertEqual(s.gold, 50 + gold)
                self.assertEqual(s.progress["xp"] - xp0, (20 if results[-1] == "won" else 4) + xp_more)   # rc16 (expected change): XP x2, was 10 / 2
                lines = adv.outcome_lines(out, game.world)
                self.assertIn(f"The gauntlet is over: {wins} of 3 won.", lines)
                if wins == 0:
                    self.assertIn("No prize this time.", lines)
                cards = sum(s.collection.count(n) or 0 for n in s.collection.names()) - cards0
                self.assertEqual(cards >= 10, pack)                        # a pack opened at once
                self.assertEqual(any(ln.startswith("New card: ") for ln in lines), rare)
                self.assertEqual(any(ln.startswith("A ") and " pack: " in ln for ln in lines), pack)

    def test_stop_here_pays_for_the_wins_so_far(self):
        game = self.game()
        s = game.save
        s.gold = 100
        extras.enter_gauntlet(s)
        self.duel(game, "won")
        gold = s.gold
        lines = extras.stop_gauntlet(game)
        self.assertEqual(lines[0], "The gauntlet is over: 1 of 3 won.")
        self.assertEqual(s.gold, gold + 25)
        self.assertIsNone(extras.gauntlet_state(s.world))

    def test_a_game_closed_half_way_resumes_and_an_unfinished_duel_is_fought_again(self):
        game = self.game()
        s = game.save
        s.gold = 100
        s.world["first_win_day"] = 1                                       # the day's first win already paid
        extras.enter_gauntlet(s)
        self.duel(game, "won")
        game.write()
        again, _n = adv.Game.load(1, pool=self.pool)                       # the program closed between two duels
        self.assertEqual(extras.gauntlet_state(again.save.world)["played"], 1)
        self.assertEqual(again.save.gold, 50)                              # no refund
        pages, choice = extras.gauntlet_pages(again.save.world)
        self.assertEqual(pages, ["Duel 2 of 3. You've won 1. Ready?"])
        self.assertEqual(choice, ("Next duel", "Stop here"))
        foe = extras.next_foe(again.save.world)
        again.save.pending_duel = {"kind": foe, "encounter": "regular", "deck": 0, "source": "jumpstart", "map": None, "x": None,
                                   "y": None, "started": 0, "gauntlet": True}
        again.write()                                                      # ... and closed during the duel
        third, notes = adv.Game.load(1, pool=self.pool)
        self.assertTrue(any("doesn't count" in n for n in notes))
        self.assertEqual(extras.gauntlet_state(third.save.world)["played"], 1)
        self.assertEqual(extras.next_foe(third.save.world), foe)          # the same duel again

    def test_the_encounter_screen_says_what_a_gauntlet_duel_is(self):
        game = self.game()
        game.save.gold = 100
        extras.enter_gauntlet(game.save)
        title, lines = adv.fight_text(game, encounters.GauntletFoe(extras.next_foe(game.save.world)))
        self.assertEqual(lines[:3], ["Duel 1 of 3. You've won 0.", extras.GAUNTLET_WIN, extras.GAUNTLET_LOSE])
        self.assertFalse(any(ln.startswith("Lose: 5") for ln in lines))

    def test_the_wardens_choice(self):
        game = self.game()
        w = game.save.world
        conv = talk.Conversation(talk.GAUNTLET_WARDEN, w)
        self.assertEqual(conv.choice, ("Enter: 50 gold", "Not now"))
        self.assertIn("The gauntlet: 3 duels in a row against the road's foes. Entry 50 gold.", conv.pages)
        conv.answer(True)
        self.assertEqual(conv.gauntlet, "enter")
        conv = talk.Conversation(talk.GAUNTLET_WARDEN, w)
        conv.answer(False)
        self.assertIsNone(conv.gauntlet)
        game.save.gold = 100
        extras.enter_gauntlet(game.save)
        conv = talk.Conversation(talk.GAUNTLET_WARDEN, w)
        self.assertEqual(conv.choice, ("Next duel", "Stop here"))
        conv.answer(False)
        self.assertEqual(conv.gauntlet, "stop")
        for p in conv.pages:
            self.assertLessEqual(len(talk.wrap(p)), talk.PAGE_LINES, p)


# ==================================================================================================================================
class WindowTests(TapBase):
    def tap_button(self, g, name):
        f = g.modal
        frame(g)
        rect = next((r for r, n in reversed(f.buttons) if n == name), None)
        self.assertIsNotNone(rect, f"no {name!r}: {[n for _r, n in f.buttons]}")
        click(g, rect.center)
        frame(g)

    def tap_through(self, g, answer=None):
        """Tap the dialogue box on until it closes or asks; then tap `answer` ("talk_yes" / "talk_no") if given. The pages seen."""
        f = g.modal
        seen = []
        for _ in range(80):
            if f.sub != "talk":
                return seen
            box = f.talk_box
            if box.choosing:
                if answer is None:
                    return seen
                self.tap_button(g, answer)
                answer = None
                continue
            if box.pages[box.page] not in seen:
                seen.append(box.pages[box.page])
            box.t0 -= 100                                              # the page typed out
            self.tap_button(g, "talk_next")
        self.fail("the box never closed")

    def talk_by_tap(self, g, nid):
        f = g.modal
        at = __import__("rpg.tapwalk", fromlist=["x"]).person_tile(f.explorer, nid)
        self.assertIsNotNone(at, f"{nid} isn't out")
        self.tap_tile(g, at)
        self.walk(g)
        self.assertEqual(f.sub, "talk")
        self.assertEqual(f.explorer.facing_npc(), nid)
        return f

    def test_a_tap_on_the_clerk_shows_the_board_and_pays_a_job_done(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        at_day(f.game, 1)
        self.talk_by_tap(g, talk.BOUNTY_CLERK)
        seen = self.tap_through(g)
        jobs = f.game.save.world["bounties"]["jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertTrue(any(p.startswith("1. ") for p in seen), seen)
        self.assertEqual(r8.on_disk(self.folder)["world"]["bounties"]["jobs"], jobs)     # written
        jobs[0]["count"] = extras.job_need(extras.read(), jobs[0])
        text = extras.read().jobs[jobs[0]["job"]]["text"]
        self.talk_by_tap(g, talk.BOUNTY_CLERK)
        seen = self.tap_through(g)
        self.assertTrue(any(p.startswith(f"Paid for \"{text}\"") for p in seen), seen)
        self.assertEqual(len(f.game.save.world["bounties"]["jobs"]), 2)

    def test_the_trade_up_tab_trades_by_a_tap(self):
        from rpg.shop import candidates
        g, _l = self.setup_game()
        f = g.modal
        f.enter_town(g, f.game.world.start_town)
        game = f.game
        name = candidates(1, "", game.pool, game.tiers(), game.lists(), game.numbers.shop_skip_prefixes)[0]
        game.save.collection.add(name, 4)
        self.tap_button(g, "tab:trade")
        self.assertEqual(f.tab, "trade")
        self.tap_button(g, f"trade:{name}")
        self.assertEqual(game.save.collection.count(name) or 0, 0)
        self.assertTrue(f.message[0].startswith(f"Traded 4 {name} for "), f.message)
        disk = r8.on_disk(self.folder)
        self.assertNotIn(name.lower(), {k.lower() for k in disk["collection"]} if isinstance(disk["collection"], dict) else set())
        self.assertNotIn(f"trade:{name}", [n for _r, n in f.buttons])

    def test_the_warden_by_tap_enter_duel_results_then_the_next_duel(self):
        g, launcher = self.setup_game()
        f = self.in_town(g)
        f.game.save.gold = 200
        self.talk_by_tap(g, talk.GAUNTLET_WARDEN)
        self.tap_through(g, answer="talk_yes")
        self.assertEqual(f.sub, "fight")
        self.assertIsInstance(f.fight, encounters.GauntletFoe)
        self.assertEqual(f.game.save.gold, 150)
        self.assertEqual(r8.on_disk(self.folder)["world"]["gauntlet"]["played"], 0)
        f.encounter.skip_transition()
        frame(g)
        self.tap_button(g, "fight")
        self.assertTrue(launcher.calls)
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=True)
        g.end_continue()
        frame(g)
        f = g.modal
        self.assertEqual(f.sub, "result")
        self.assertIn("Gauntlet duel 1 of 3: won. Wins so far: 1.", f.result[1])
        self.tap_button(g, "ok")
        self.assertEqual(f.sub, "talk")                                    # the warden asks for the next duel
        self.assertEqual(f.talk_conv.npc_id, talk.GAUNTLET_WARDEN)
        seen = self.tap_through(g)
        self.assertIn("Duel 2 of 3. You've won 1. Ready?", seen)
        self.assertEqual(f.talk_box.choice, ("Next duel", "Stop here"))
        self.tap_button(g, "talk_no")                                      # Stop here: the prize for one win
        self.assertEqual(f.sub, "result")
        self.assertEqual(f.result[0], rs.GAUNTLET_TITLE)
        self.assertIn("+25 gold.", f.result[1])
        self.assertIsNone(extras.gauntlet_state(f.game.save.world))

    def test_too_little_gold_for_the_entry_says_so(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.gold = 10
        self.talk_by_tap(g, talk.GAUNTLET_WARDEN)
        self.tap_through(g, answer="talk_yes")
        self.assertEqual(f.sub, "result")
        self.assertEqual(f.result[1], ["The entry is 50 gold; you have 10."])
        self.assertIsNone(extras.gauntlet_state(f.game.save.world))

    def test_the_menu_lists_the_bounties(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        extras.post_bounties(f.game.save)
        self.tap_button(g, "pause")
        words = [t for t, _c in f.menu.info]
        self.assertTrue(any(t.startswith("Bounty: ") for t in words), words)


# ==================================================================================================================================
class SaveTests(r8.Base):
    def test_a_save_from_before_prog5_loads_with_none_of_it(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        d = json.load(open(path, encoding="utf-8"))
        for k in ("first_win_day", "bounties", "gauntlet"):
            d["world"].pop(k, None)
        json.dump(d, open(path, "w", encoding="utf-8"))
        game, _n = adv.Game.load(1, pool=self.pool)
        w = game.save.world
        self.assertIsNone(extras.gauntlet_state(w))
        self.assertEqual(extras.journal_lines(game.save), [])
        self.assertEqual(len(extras.post_bounties(game.save)), 3)
        self.assertIsNotNone(extras.first_win(game.save, "won"))

    def test_damaged_values_are_mended(self):
        r8.new_game(None)
        game, _n = adv.Game.load(1, pool=self.pool)
        w = game.save.world
        w.update(first_win_day="x", bounties=[1, 2], gauntlet="half")
        self.assertIsNotNone(extras.first_win(game.save, "won"))
        self.assertIsNone(extras.gauntlet_state(w))
        self.assertIsNone(extras.next_foe(w))
        self.assertEqual(len(extras.post_bounties(game.save)), 3)
        w["bounties"]["jobs"].append({"job": "no_such_job"})
        self.assertEqual(extras.collect_bounties(game), [])
        self.assertEqual(len(extras.board_lines(game.save)), 3)


# ==================================================================================================================================
class FitTests(PlayedBase):
    def inside(self, g, f, what):
        whole = g.screen.get_rect()
        for r, n in f.buttons:
            self.assertTrue(whole.contains(r), f"{what}: {n} {r} outside {whole}")

    def test_the_trade_tab_fits(self):
        from rpg.shop import candidates
        r8.new_game(None)
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                g = self.gui(size, scale)
                f = self.open(g, slot=1)
                f.enter_town(g, f.game.world.start_town)
                game = f.game
                for n in candidates(1, "", game.pool, game.tiers(), game.lists(), game.numbers.shop_skip_prefixes)[:30]:
                    game.save.collection.add(n, 4)
                f.tab = "trade"
                frame(g)
                self.inside(g, f, "trade tab")
                tabs = [r for r, n in f.buttons if n.startswith("tab:")]
                self.assertEqual(len(tabs), len(rs.TABS))
                for a in tabs:
                    for b in tabs:
                        if a is not b:
                            self.assertFalse(a.colliderect(b), "two tabs overlap")
                f.wheel(g, -5)
                frame(g)
                self.inside(g, f, "trade tab scrolled")

    def test_the_results_and_the_boxes_fit(self):
        r8.new_game(None)
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                g, _l = self.setup_game(size=size)
                if scale:
                    g.text_scale = scale
                f = self.on_map(g, "ashfen_outskirts")
                f = self.duel_to_result(g, won=True)
                f.results.skip(time.monotonic())
                frame(g)
                self.inside(g, f, "results")
                self.assertTrue(any("First win of the day" in ln for ln in f.result[1]))
                f.sub, f.results = None, None
                f = self.on_map(g, TOWN, "start")
                for nid in (talk.BOUNTY_CLERK, talk.GAUNTLET_WARDEN):
                    f.talk_with(g, nid)
                    frame(g)
                    self.inside(g, f, nid)
                    f.close_talk()


if __name__ == "__main__":
    unittest.main()
