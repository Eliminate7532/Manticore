# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG2 (Karl, 2 Nov 2026: visible rewards - "pick 1 of 3" from "the beaten foe's deck, your colours first", treasure chests
"mostly one-time ... a few small ones refill each new in-game day", and the boss's signature card; the design: the Project doc
claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.2). Through the Adventure window where it can be:

  1  OfferTests     rpg.rewards: n groups of 3 from the beaten foe's deck and band, castable first, different names; a
                    Jumpstart foe's swapped cards can still be offered (RPG9); the choice goes into the collection
  2  PickTests      after a won duel the results screen shows 3n cards in groups; a tap picks one in its group, a tap beside
                    them does nothing, Continue takes the picked ones; the arrow keys pick too; a game closed mid-choice offers
                    the same cards again
  3  ChestTests     the maps' chests (ids in progression.json, none blocking a path); walking into one or tapping it opens it
                    once; a daily one fills again at 07:00; gold, a band card in your colours, a pack, gear as gold; the
                    sprites
  4  BossTests      the boss's encounter screen says "Guards: <card>"; beating him gives that card on top of his reward cards
  5  SaveTests      a save from before PROG2 loads (no pending_pick, no chests); a damaged pending_pick is dropped
  6  DataTests      progression.json's new keys are read and checked
  7  FitTests       the choosing screen (a boss's 5 groups and his card) and the chest window inside the window at 1360x840,
                    1920x1080, 2736x1824 and at 200% text

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest tests.test_prog2
"""
import json
import os
import random
import tempfile
import time
import unittest
from unittest import mock

import pygame

import rpg_map_view
import rpg_results
import rpg_screens as rs
from rpg import adventure as adv
from rpg import cardpool, chests, clock, duel, encounters, enemies, explore, progress, rewards, tapwalk
from rpg import save as sv
from tests import rpg_route
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame, key
from tests.test_prog1 import PROG_SIZES, PlayedBase

POOL = cardpool.load()
CHESTS = {"lantern_hollow": ["yard_crate"], "ashfen_outskirts": ["moor_hollow", "moor_cairn"],
          "warrens_1": ["crypt_coffer"], "warrens_2": ["priest_hoard", "cave_cache"]}


def area_chests(name):
    explore.forget_areas()
    return [t for t in explore.load_area(name).triggers if t.kind == "chest"]


class FakeSave:
    """Enough of a save for rpg.rewards (rng, deck, collection)."""

    def __init__(self, deck, seed=1):
        self._deck = deck
        self.n = 0
        self.seed = seed

    def rng(self, purpose):
        self.n += 1
        return random.Random(f"{self.seed}:{self.n}:{purpose}")

    def deck(self):
        return self._deck


# ==================================================================================================================================
class OfferTests(r8.Base):
    def game(self):
        r8.new_game(None)
        g, _n = adv.Game.load(1, pool=self.pool)
        return g

    def test_n_groups_of_three_castable_first_different_names(self):
        g = self.game()
        data = enemies.read_kind("grave_priest")
        _src, decks = enemies.deck_list(data, enemies.ARCHETYPE)
        colours = rewards.deck_colours(g.save.deck(), g.pool)
        self.assertTrue(colours)
        for seed in range(6):
            with self.subTest(seed=seed):
                s = FakeSave(g.save.deck(), seed)
                groups = rewards.offer_cards(s, decks[0], 5, (1, 3), g.pool, g.tiers(), g.lists(), g.numbers, colours)
                self.assertEqual(len(groups), 5)
                self.assertTrue(all(len(x) == 3 for x in groups), groups)
                flat = [n for x in groups for n in x]
                self.assertEqual(len(flat), len(set(flat)), "a name offered twice")
                ok = [rewards.castable(g.pool.get(n), colours) for n in flat]
                # castable first: once a card that can't be cast is dealt, every later card in the order can't be cast either
                order = [x[i] for i in range(3) for x in groups]
                cast = [rewards.castable(g.pool.get(n), colours) for n in order]
                self.assertEqual(cast, sorted(cast, reverse=True), order)
                self.assertGreater(sum(ok), 0)

    def test_the_castable_rule(self):
        self.assertTrue(rewards.castable(POOL.get("Lightning Bolt"), "R"))
        self.assertFalse(rewards.castable(POOL.get("Lightning Bolt"), "WG"))
        self.assertTrue(rewards.castable(POOL.get("Sol Ring") or POOL.get("Ornithopter"), "G"))      # colourless always
        self.assertEqual(rewards.deck_colours({"main": [["Forest", 9], ["Lightning Bolt", 4]]}, POOL), "RG")

    def test_a_jumpstart_foes_swapped_cards_can_still_be_offered(self):
        g = self.game()
        data = enemies.read_kind("ember_bandit")
        _src, decks = enemies.deck_list(data, enemies.JUMPSTART)
        deck = next(d for d in decks if d.get("swapped"))
        swapped = {POOL.get(n).name for n, _k in deck["swapped"] if POOL.get(n) is not None}
        full = dict(deck, main=[list(x) for x in deck["main"]] + [list(x) for x in deck["swapped"]])
        seen = set()
        for seed in range(40):
            s = FakeSave({"main": deck["main"]}, seed)
            for grp in rewards.offer_cards(s, full, 1, (1, 3), g.pool, g.tiers(), g.lists(), g.numbers, "WUBRG"):
                seen |= set(grp)
        self.assertTrue(seen & swapped, "no swapped card was ever offered")

    def test_choose_puts_the_picked_cards_in_and_clears_the_choice(self):
        g = self.game()
        s = g.save
        s.pending_pick = {"groups": [["Shock", "Counterspell", "Giant Growth"], ["Dark Ritual", "Opt", "Duress"]],
                          "kind": "ember_bandit"}
        before = {n: s.collection.count(n) for n in ("Counterspell", "Opt", "Shock")}
        with self.assertRaises(ValueError):
            rewards.choose(s, ["Counterspell"])                       # one card for two groups
        with self.assertRaises(ValueError):
            rewards.choose(s, ["Opt", "Counterspell"])                # each from its own group
        given = rewards.choose(s, ["Counterspell", "Opt"])
        self.assertEqual(given, [("Counterspell", "deck"), ("Opt", "deck")])
        self.assertEqual(s.collection.count("Counterspell"), before["Counterspell"] + 1)
        self.assertEqual(s.collection.count("Shock"), before["Shock"])
        self.assertIsNone(s.pending_pick)
        self.assertEqual(s.bestiary["ember_bandit"]["cards"].get("Counterspell"), 1)

    def test_without_offer_the_tools_still_get_the_cards_at_once(self):
        g = self.game()
        adv.start_duel(g, encounters.RandomFoe("ember_bandit"))
        out = duel.finish(g.save, "won", g.numbers, g.world, g.pool, g.tiers(), g.lists())
        self.assertGreaterEqual(len(out["cards"]), 1)
        self.assertIsNone(g.save.pending_pick)
        self.assertNotIn("offers", out)


# ==================================================================================================================================
class PickTests(PlayedBase):
    def won(self, g):
        self.on_map(g, "ashfen_outskirts")
        return self.duel_to_result(g, won=True)

    def test_a_win_offers_three_cards_for_each_reward_card_and_continue_takes_the_picked(self):
        g, _l = self.setup_game()
        f = self.won(g)
        res = f.results
        self.assertTrue(res.choosing(), "no choice after a win")
        n = f.game.numbers.reward_cards(encounters.RANDOM_ENCOUNTER)          # the window's duel is a random foe's
        self.assertEqual(len(res.offers), n)
        self.assertTrue(all(len(x) == 3 for x in res.offers))
        self.assertEqual(r8.on_disk(self.folder)["pending_pick"]["groups"],
                         [[c["name"] for c in grp] for grp in res.offers])            # written before you choose
        key(g, pygame.K_RETURN)                                                           # finish the count
        frame(g)
        picks = [n_ for _r, n_ in f.buttons if n_.startswith("rpick:")]
        self.assertEqual(len(picks), 3 * n)
        target = res.offers[0][2]["name"]
        before = f.game.save.collection.count(target)
        rect = next(r for r, n_ in f.buttons if n_ == "rpick:0:2")
        click(g, rect.center)                                                             # a tap picks the third card
        self.assertEqual(res.picked()[0], target)
        click(g, (3, 3))                                                                  # a tap beside the cards: nothing
        self.assertEqual(f.sub, "result")
        frame(g)
        self.press(g, "ok")                                                               # Continue takes it
        self.assertIsNone(f.sub)
        self.assertEqual(f.game.save.collection.count(target), before + 1)
        disk = r8.on_disk(self.folder)
        self.assertIsNone(disk["pending_pick"])
        self.assertGreaterEqual(disk["collection"].get(target, 0) if isinstance(disk["collection"], dict) else 1, 1)

    def test_the_arrow_keys_pick_and_enter_takes(self):
        g, _l = self.setup_game()
        f = self.won(g)
        res = f.results
        key(g, pygame.K_RETURN)
        frame(g)
        key(g, pygame.K_RIGHT)
        self.assertEqual(res.picks[0], 1)
        key(g, pygame.K_LEFT)
        key(g, pygame.K_LEFT)
        self.assertEqual(res.picks[0], len(res.offers[0]) - 1)
        name = res.picked()[0]
        before = f.game.save.collection.count(name)
        key(g, pygame.K_RETURN)
        self.assertIsNone(f.sub)
        self.assertEqual(f.game.save.collection.count(name), before + 1)

    def test_a_game_closed_on_the_choice_offers_the_same_cards_again(self):
        g, _l = self.setup_game()
        f = self.won(g)
        offered = [[c["name"] for c in grp] for grp in f.results.offers]
        self.close(g)                                                                     # the program closes here
        g2 = self.gui()
        f2 = self.open(g2, slot=1)
        frame(g2)
        self.assertEqual(f2.sub, "result")
        self.assertIsNotNone(f2.results)
        self.assertEqual([[c["name"] for c in grp] for grp in f2.results.offers], offered)
        self.assertEqual(f2.results.info["gold_change"], 0)                               # the gold was given already
        key(g2, pygame.K_RETURN)
        frame(g2)
        key(g2, pygame.K_RETURN)
        self.assertIsNone(f2.sub)
        self.assertIsNone(r8.on_disk(self.folder)["pending_pick"])

    def test_a_loss_offers_nothing(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = self.duel_to_result(g, won=False)
        self.assertFalse(f.results.choosing())
        self.assertIsNone(r8.on_disk(self.folder).get("pending_pick"))


# ==================================================================================================================================
class ChestTests(PlayedBase):
    def test_the_maps_chests_are_listed_and_none_blocks_a_path(self):
        n = progress.read()
        for name, ids in CHESTS.items():
            with self.subTest(map=name):
                ts = area_chests(name)
                self.assertEqual(sorted(t.chest for t in ts), sorted(ids))
                a = explore.load_area(name)
                self.assertEqual(a.check(), [])
                start = a.spawn()
                people = {p["tile"] for p in a.scheduled()}
                tiles = {a.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2) for t in ts}
                free = a.reachable(start, people)
                walled = a.reachable(start, people | tiles)
                self.assertEqual(free - tiles, walled, f"a chest on {name} cuts off {sorted(free - tiles - walled)}")
                for t in ts:
                    self.assertIn(t.chest, n.chests)
        self.assertEqual(sorted(i for ids in CHESTS.values() for i in ids), sorted(n.chests))
        self.assertEqual([c for c, e in n.chests.items() if e["daily"]], ["yard_crate"])
        behind = area_chests("warrens_2")
        boss = next(t for t in explore.load_area("warrens_2").triggers if t.encounter == "boss")
        hoard = next(t for t in behind if t.chest == "priest_hoard")
        self.assertLess(hoard.box[1], boss.box[1], "the hoard isn't behind the Grave Priest (further from the hall's way in)")

    def walk_into(self, g, chest_id, by_tap=False):
        """Walk (or tap) into a chest from the free tile next to it (the moor's foes are left out of the way: beaten)."""
        f = g.modal
        ex = f.explorer
        f.game.save.world["beaten"] = sorted(ex.foes)
        t = next(t for t in ex.area.triggers if t.kind == "chest" and t.chest == chest_id)
        tile = ex.area.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
        a = ex.area
        for dc, dr in ((0, 1), (1, 0), (-1, 0), (0, -1), (0, 2), (2, 0)):
            near = (tile[0] + dc, tile[1] + dr)
            if not a.blocked(*near):
                ex._use(a, explore.Walker(a, *rpg_route.middle(a, near)))
                f.game.save.world["beaten"] = sorted(ex.foes)
                break
        if by_tap:
            mx, my = rpg_route.middle(ex.area, tile)
            goal, why = tapwalk.goal_at(ex, mx, my)
            self.assertIsNotNone(goal, why)
            f.tap = tapwalk.TapWalk(goal)
            for _ in range(4000):
                if f.tap is None or f.sub is not None:
                    break
                f.tap_tick(g, 1 / 60)
        else:
            events, _n = rpg_route.walk_to_tile(ex, tile)
            for ev in events:
                f.walk_event(g, ev)
        frame(g)
        return f, t

    def test_walking_into_a_chest_opens_it_once_and_the_save_says_so(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        gold = f.game.save.gold
        f, t = self.walk_into(g, "moor_hollow")
        self.assertEqual(f.sub, "result")
        self.assertEqual(f.result[0], chests.TITLE)
        self.assertEqual(f.game.save.gold, gold + 60)
        self.assertIn("moor_hollow", r8.on_disk(self.folder)["world"]["chests"])
        key(g, pygame.K_RETURN)
        self.assertIsNone(f.sub)
        self.assertIn(t.spot, f.explorer.skipped())
        ex = f.explorer                                   # walk off and back on: nothing more
        a = ex.area
        c, r = a.tile_at(ex.walker.x, ex.walker.y)
        for tile in ((c, r + 1), (c, r - 1), (c + 1, r), (c - 1, r)):
            if not a.blocked(*tile):
                break
        rpg_route.walk_to_tile(ex, tile)
        events, _n = rpg_route.walk_to_tile(ex, (c, r))
        self.assertEqual([e for e in events if e[0] == "chest"], [])
        self.assertEqual(f.game.save.gold, gold + 60)

    def test_a_tap_on_a_chest_walks_up_and_opens_it(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f, _t = self.walk_into(g, "moor_cairn", by_tap=True)
        self.assertEqual(f.sub, "result")
        self.assertTrue(any(ln.startswith("New card: ") for ln in f.result[1]), f.result)
        card = next(ln for ln in f.result[1] if ln.startswith("New card: "))[len("New card: "):-1]
        colours = rewards.deck_colours(f.game.save.deck(), f.game.pool)
        self.assertTrue(rewards.castable(f.game.pool.get(card), colours), (card, colours))
        lo, hi = f.game.world.band(1)
        self.assertTrue(lo <= f.game.tiers().tier_of(card) <= hi)

    def test_a_walk_elsewhere_never_opens_a_chest(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "lantern_hollow", "start")
        ex = f.explorer
        t = next(t for t in ex.area.triggers if t.kind == "chest")
        goal = tapwalk.Goal("tile", (20, 3), ex.area.name)
        walls = tapwalk.walls(ex, goal)
        self.assertTrue(tapwalk.box_tiles(ex.area, t.box) <= walls)

    def test_a_daily_chest_fills_again_at_seven_and_a_one_time_one_never(self):
        w = {}
        daily = {"gold": 15, "daily": True}
        once = {"gold": 60, "daily": False}
        night = 3 * clock.DAY + 22 * 60                                   # day 4, 22:00
        w["chests"] = {"d": night, "o": night}
        self.assertTrue(chests.is_open(w, "d", daily, 7, night + 60))
        self.assertTrue(chests.is_open(w, "d", daily, 7, 4 * clock.DAY + 7 * 60 - 1))
        self.assertFalse(chests.is_open(w, "d", daily, 7, 4 * clock.DAY + 7 * 60))     # 07:00 the next morning
        self.assertTrue(chests.is_open(w, "o", once, 7, 40 * clock.DAY))
        early = 5 * clock.DAY + 6 * 60                                     # opened at 06:00: full again at 07:00 that morning
        w["chests"]["d"] = early
        self.assertFalse(chests.is_open(w, "d", daily, 7, early + 61))
        self.assertFalse(chests.is_open({}, "d", daily, 7, early))        # a save from before: closed

    def test_the_daily_crate_through_the_window(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "lantern_hollow", "start")
        gold = f.game.save.gold
        f, _t = self.walk_into(g, "yard_crate")
        self.assertEqual(f.game.save.gold, gold + 15)
        key(g, pygame.K_RETURN)
        clock.to_morning(f.game.save.world)                                 # a night at the inn
        f.explorer.refresh(force=True)
        self.assertFalse(dict((t.chest, o) for t, o in f.explorer.chests())["yard_crate"])

    def test_gear_gives_gold_for_now_and_a_pack_opens(self):
        r8.new_game(None)
        g, _n = adv.Game.load(1, pool=self.pool)
        s = g.save
        gold = s.gold
        lines, cards = chests.open_chest(s, "priest_hoard", progress.read().chests["priest_hoard"], g.pool, g.tiers(),
                                         g.lists(), g.numbers, (1, 3), now=1000, refill_hour=7)
        self.assertEqual(s.gold, gold + 180)
        self.assertIn(chests.GEAR_LATER, lines)
        self.assertEqual(len(cards), 1)
        lines, cards = chests.open_chest(s, "cave_cache", progress.read().chests["cave_cache"], g.pool, g.tiers(),
                                         g.lists(), g.numbers, (1, 3), now=1000, refill_hour=7)
        self.assertTrue(any("pack" in ln for ln in lines), lines)
        self.assertGreaterEqual(len(cards), 10)
        self.assertEqual(chests.open_chest(s, "cave_cache", progress.read().chests["cave_cache"], g.pool, g.tiers(),
                                           g.lists(), g.numbers, (1, 3), now=2000, refill_hour=7), ([], []))

    def test_the_sprites_closed_and_open_differ(self):
        closed, opened = rpg_map_view.chest_sprite(False, False, 1), rpg_map_view.chest_sprite(True, False, 1)
        self.assertIsNotNone(closed)
        self.assertIsNotNone(opened)
        self.assertNotEqual(pygame.image.tobytes(closed, "RGB"), pygame.image.tobytes(opened, "RGB"))
        self.assertEqual(closed.get_colorkey()[:3], rpg_map_view.CHEST_KEY)
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        self.assertEqual(len(f.chest_list()), 2)


# ==================================================================================================================================
class BossTests(PlayedBase):
    def test_the_boss_says_what_he_guards_and_gives_it(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "warrens_2", "stairs_top")
        boss = self.boss(f)
        enc = self.meet(g, boss)
        self.assertTrue(enc.guards, "no signature card for the Grave Priest")
        want = rewards.signature_card(enc.deck, f.game.pool, f.game.tiers(), f.game.lists())
        self.assertEqual(enc.guards, want)
        self.assertIn(f"Guards: {want}. Beat him and it is yours.", enc.lines)
        before = f.game.save.collection.count(want)
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=True)
        g.end_continue()
        frame(g)
        f = g.modal
        self.assertEqual(f.game.save.collection.count(want), before + 1)
        info = f.results.info
        self.assertEqual([c["name"] for c in info["cards"] if c["signature"]], [want])
        self.assertEqual(len(f.results.offers), f.game.numbers.reward_cards("boss"))
        self.assertTrue(any(ln.startswith(f"He guarded {want}") for ln in f.result[1]), f.result)

    def test_the_signature_is_the_decks_highest_tier(self):
        r8.new_game(None)
        g, _n = adv.Game.load(1, pool=self.pool)
        _src, decks = enemies.deck_list(enemies.read_kind("grave_priest"), enemies.ARCHETYPE)
        for d in decks:
            sig = rewards.signature_card(d, g.pool, g.tiers(), g.lists())
            tiers = [g.tiers().tier_of(n) for n, _k in d["main"] if not g.pool.get(n).is_land]     # lands left out
            self.assertFalse(g.pool.get(sig).is_land)
            self.assertEqual(g.tiers().tier_of(sig), max(t for t in tiers if t is not None))
        self.assertIsNone(duel.guarded_card(decks[0], "regular", g.pool, g.lists(), g.tiers()))


# ==================================================================================================================================
class SaveTests(r8.Base):
    def test_a_save_from_before_prog2_loads(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d.pop("pending_pick", None)
        d["world"].pop("chests", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
        g, _n = adv.Game.load(1, pool=self.pool)
        self.assertIsNone(g.save.pending_pick)
        self.assertIsNone(adv.pending_pick_outcome(g))
        self.assertIn("pending_pick", g.save.to_dict())

    def test_a_damaged_choice_is_dropped_not_refused(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d["pending_pick"] = {"groups": "nonsense"}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
        g, _n = adv.Game.load(1, pool=self.pool)
        self.assertIsNone(g.save.pending_pick)


# ==================================================================================================================================
class DataTests(unittest.TestCase):
    def test_the_new_keys_are_read_and_checked(self):
        n = progress.read()
        self.assertEqual(n.choices_per_card, 3)
        self.assertTrue(n.signature_card)
        self.assertEqual(n.refill_hour, 7)
        with open(progress.PROGRESSION_FILE, encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn("PROG2", d["about"])
        for bad, word in (({"rewards": {"choices_per_card": 0}}, "choices_per_card"),
                          ({"chests": {"list": {"x": {"gold": 5, "sword": 1}}}}, "sword"),
                          ({"chests": {"list": {"x": {}}}}, "empty chest"),
                          ({"chests": {"list": {"x": {"card": "any"}}}}, "card")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    progress.Progression(dict(d, **bad))
                self.assertIn(word, str(cm.exception))
        old = {k: v for k, v in d.items() if k in ("about", "results")}                # PROG1's file: defaults
        old["meter"] = {k: v for k, v in d["card_quality"].items() if k != "explain"}  # (Round PROG1b renamed the key)
        self.assertEqual(progress.Progression(old).chests, {})


# ==================================================================================================================================
class FitTests(PlayedBase):
    def choosing(self, g):
        f = g.modal
        groups = [["Shock", "Counterspell", "Giant Growth"], ["Dark Ritual", "Opt", "Duress"],
                  ["Lightning Bolt", "Llanowar Elves", "Swords to Plowshares"], ["Brainstorm", "Ponder", "Preordain"],
                  ["Doom Blade", "Naturalize", "Disenchant"]]
        out = {"result": "won", "gold": 200, "cards": [("Sengir Vampire", "signature")], "offers": groups,
               "new": ["Sengir Vampire"], "enemy": "Grave Priest"}
        f.game.save.pending_pick = {"groups": groups, "kind": "grave_priest"}
        f.open_results(g, out, adv.outcome_lines(out, f.game.world))
        f.result, f.sub = ("Victory", adv.outcome_lines(out, f.game.world), rs.GREEN), "result"
        f.results.skip(time.monotonic())
        frame(g)
        return f

    def test_the_choosing_screen_and_the_chest_window_fit(self):
        r8.new_game(None)
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                g = self.gui(size=size, scale=scale)
                self.open(g, slot=1)
                self.on_map(g, "ashfen_outskirts")
                f = self.choosing(g)
                whole = g.screen.get_rect()
                rect = f.results.rect
                self.assertTrue(whole.contains(rect), rect)
                self.assertEqual(len(f.results.card_rects), 16)
                for c in f.results.card_rects:
                    self.assertTrue(rect.contains(c), f"a card {c} outside {rect}")
                    self.assertGreater(c.h, 24, f"cards too small to see: {c}")
                small = [r for r, n in f.buttons if r != whole]
                for i, a in enumerate(small):
                    for b in small[i + 1:]:
                        self.assertFalse(a.colliderect(b), f"buttons overlap {a} {b}")
                key(g, pygame.K_RETURN)
                self.assertIsNone(f.sub)
                f.result, f.sub, f.message_box = (chests.TITLE, ["+100 gold.", "+80 gold.", chests.GEAR_LATER,
                                                                  "New card: Swords to Plowshares."], rs.GOLD), "result", None
                frame(g)
                for r, n in f.buttons:
                    self.assertTrue(whole.contains(r) or r == whole, f"chest window: {n} {r}")
                self.close(g)


if __name__ == "__main__":
    unittest.main()
