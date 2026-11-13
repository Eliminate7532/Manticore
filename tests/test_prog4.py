# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG4 (Karl, 2 Nov 2026: gear from "Everywhere" - common pieces in town shops, better ones in chests, from bosses and quests;
the design: the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.4). Groups:

  1  DataTests     rpg/data/gear.json is read and checked; every card a piece puts in play is a real card or token script in our
                   Forge copy, every relic a Conspiracy card (its source named); the icons are what tools/rpg_gear_icons.py draws
  2  RuleTests     rpg.gear: wear / take off / the Pack slot (Pack Mule only); each worn piece counts ONCE in the duel's start
                   effects, the reward picks, a win's gold and the walk speed; trade-offs never take the start below 1 life
  3  SourceTests   where gear comes from: chests give their piece, the Grave Priest his, a rare foe its own (said on the encounter
                   screen), a quest its own; a piece is owned once; a lost duel keeps every piece
  4  ShopTests     the town's Gear tab sells only the region's common pieces not owned yet; a tap buys one and writes the save
  5  WindowTests   by tap only: Menu > Gear > Wear / Take off / In pack (written at once); the corner panel's icons; the results
                   screen says what was worn; the walk speed follows the boots; the encounter screen says what you start with
  6  SaveTests     a save from before PROG4 loads with nothing owned or worn; a damaged gear value is mended
  7  FitTests      the Gear screen, the shop's Gear tab, the encounter screen and the results screen at 1360x840, 1920x1080, 2736x1824
                   and at 200% text
  8  LiveTests     real Forge: the relics work from the command zone (Sentinel Dispatch's Construct, Hold the Perimeter's Soldier),
                   the gear's tokens are on the battlefield; Duels (rpg_tools.jar) applies a gear spec with a command-zone relic

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest tests.test_prog4
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import pygame

import forge_client as fc
import rpg_screens as rs
import start_effects as se
from rpg import adventure as adv
from rpg import chests, encounters, explore, gear, levels, progress, talk
from rpg import save as sv
from tests import live
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame, key
from tests.test_prog1 import PROG_SIZES, PlayedBase

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(BASE, "forge_runtime", "res")


class FakeSave:
    def __init__(self, perks=()):
        self.progress = {"xp": 0, "perks": list(perks), "offers": []}
        self.gear = {"owned": [], "worn": {}}
        self.gold = 1000
        self.world = {"region": 1}

    def rng(self, purpose):
        import random
        return random.Random(purpose)


class RareFoe(encounters.RandomFoe):
    """A rare foe met as on the map (rpg.explore's trigger carries the rare's id)."""
    __slots__ = ("rare",)

    def __init__(self, enemy, rare):
        super().__init__(enemy)
        self.rare = rare


def own(s, *pids):
    for p in pids:
        gear.give(s, p)


# ==================================================================================================================================
class DataTests(unittest.TestCase):
    def test_the_file_is_read_and_has_about_twelve_region_1_pieces(self):
        gr = gear.read()
        r1 = [p for p in gr.pieces.values() if p.region == 1]
        self.assertGreaterEqual(len(r1), 12)
        self.assertEqual([s.id for s in gr.slots.values()], ["weapon", "offhand", "amulet", "armour", "boots", "pack"])
        self.assertEqual(len({p.name for p in gr.pieces.values()}), len(gr.pieces))          # no two pieces share a name
        for sid in ("weapon", "offhand", "amulet", "armour", "boots"):
            self.assertTrue(any(p.slot == sid and p.rarity == "common" for p in r1), f"no common {sid} in the shops")
        self.assertTrue(gr.slots["pack"].any and gr.slots["pack"].perk == "pack_mule")

    def test_every_effect_kind_of_the_brief_is_used(self):
        kinds = {k for p in gear.read().pieces.values() for k in p.effect}
        self.assertEqual(kinds, set(gear.EFFECTS))
        self.assertTrue(any(p.effect.get("life", 0) < 0 or p.effect.get("walk_speed", 1) < 1 for p in gear.read().pieces.values()),
                        "no trade-off piece")

    def test_every_found_piece_has_a_source_and_every_source_a_piece(self):
        gr = gear.read()
        found = {p.id for p in gr.pieces.values() if p.rarity != "common"}
        named = {pid for grp in gr.sources.values() for pid in grp.values()}
        named |= {c["gear"] for c in progress.read().chests.values() if isinstance(c["gear"], str)}
        self.assertEqual(found, named)
        self.assertIn("grave_priest", gr.sources["bosses"])
        rares = json.load(open(os.path.join(BASE, "rpg", "data", "rare_spawns.json"), encoding="utf-8"))["rare"]
        self.assertTrue(set(gr.sources["rares"]) <= set(rares))
        quests = json.load(open(os.path.join(BASE, "rpg", "data", "quests.json"), encoding="utf-8"))["quests"]
        self.assertTrue(set(gr.sources["quests"]) <= set(quests))

    def test_bad_keys_are_named(self):
        good = json.load(open(gear.GEAR_FILE, encoding="utf-8"))
        for change, word in ((lambda d: d["pieces"]["copper_charm"].update(slot="belt"), "slot"),
                             (lambda d: d["pieces"]["copper_charm"]["effect"].update(mana=1), "mana"),
                             (lambda d: d["pieces"]["copper_charm"].update(rarity="epic"), "rarity"),
                             (lambda d: d["pieces"]["road_boots"]["effect"].update(walk_speed=9), "walk_speed"),
                             (lambda d: d["sources"]["bosses"].update(grave_priest="nothing"), "nothing")):
            d = json.loads(json.dumps(good))
            change(d)
            with self.subTest(word=word):
                with self.assertRaises(ValueError) as cm:
                    gear.Gear(d)
                self.assertIn(word, str(cm.exception))

    def card_script(self, name):
        if name.lower().startswith(se.TOKEN):
            path = os.path.join(RUNTIME, "tokenscripts", name[len(se.TOKEN):] + ".txt")
            return path if os.path.exists(path) else None
        import forge_scripts
        base = name.lower().replace("'", "").replace(",", "").replace(" ", "_")
        path = os.path.join(RUNTIME, "cardsfolder", base[0], base + ".txt")
        return path if os.path.exists(path) else None

    @unittest.skipUnless(os.path.isdir(os.path.join(RUNTIME, "cardsfolder")), "needs forge_runtime/ (python setup_forge.py)")
    def test_every_card_a_piece_puts_in_play_is_in_our_forge_copy_and_every_relic_a_conspiracy(self):
        for p in gear.read().pieces.values():
            for k in ("battlefield", "command"):
                for name in p.effect.get(k, ()):
                    with self.subTest(piece=p.id, card=name):
                        path = self.card_script(name)
                        self.assertIsNotNone(path, f"{name}: no script in forge_runtime")
                        text = open(path, encoding="utf-8").read()
                        if not name.lower().startswith(se.TOKEN):
                            self.assertIn(f"Name:{name}\n", text)
                        if k == "command":                       # Wizards' Conspiracy cards: their abilities work from there
                            self.assertIn("Types:Conspiracy", text)
                            self.assertIn("Command", text)

    @unittest.skipUnless(os.path.isdir(os.path.join(RUNTIME, "cardsfolder")), "needs forge_runtime/")
    def test_forges_own_adventure_item_cards_are_not_in_our_copy(self):
        # The brief asked us to check (UNVERIFIED before this round): Forge's Adventure keeps its item cards under
        # res/adventure/; our bundle ships res/ without it, so none of those scripts can be used.
        self.assertFalse(os.path.exists(os.path.join(RUNTIME, "adventure")))

    def test_the_icons_are_what_the_tool_draws_and_every_piece_has_one(self):
        from tools import rpg_gear_icons as icons
        pygame.init()
        self.assertEqual(icons.check(), [])
        for p in gear.read().pieces.values():
            self.assertIn(p.icon, icons.ICONS, p.id)
            self.assertTrue(os.path.exists(os.path.join(BASE, "assets", "gear", p.icon + ".png")))

    def test_the_icons_are_credited(self):
        notices = json.load(open(os.path.join(BASE, "licenses", "NOTICES.json"), encoding="utf-8"))
        entry = next((c for c in notices["components"] if c.get("id") == "rpg-gear-icons"), None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["license"], "CC0-1.0")
        self.assertIn("assets/gear", entry["used_for"])


# ==================================================================================================================================
class RuleTests(unittest.TestCase):
    def test_wear_take_off_and_one_piece_per_slot(self):
        s = FakeSave()
        own(s, "copper_charm", "merchants_token")
        gear.wear(s, "copper_charm")
        self.assertEqual(gear.worn_in(s, "copper_charm"), "amulet")
        gear.wear(s, "merchants_token")                                 # the same slot: the charm comes off
        self.assertIsNone(gear.worn_in(s, "copper_charm"))
        self.assertEqual(gear.take_off(s, "amulet"), "merchants_token")
        self.assertEqual(gear.worn(s), [])
        with self.assertRaises(gear.GearError):
            gear.wear(s, "road_boots")                                  # not owned
        with self.assertRaises(gear.GearError):
            gear.wear(s, "copper_charm", "boots")                      # the wrong slot

    def test_the_pack_slot_opens_only_with_pack_mule(self):
        s = FakeSave()
        own(s, "copper_charm", "ossuary_ward")
        self.assertNotIn("pack", [x.id for x in gear.open_slots(s)])
        with self.assertRaises(gear.GearError):
            gear.wear(s, "ossuary_ward", "pack")
        s.progress["perks"] = ["pack_mule"]
        gear.wear(s, "copper_charm")
        gear.wear(s, "ossuary_ward", "pack")                            # two amulets: one in the Pack
        self.assertEqual(levels.duel_effects(s), {"life": 22, "command": ["Sentinel Dispatch"]})
        s.progress["perks"] = []                                        # the Pack closed again: it no longer counts
        self.assertEqual(levels.duel_effects(s), {"life": 22})

    def test_each_worn_piece_counts_once_in_the_duels_start(self):
        s = FakeSave()
        own(s, "copper_charm", "padded_jerkin", "militia_spear", "tallow_lantern")
        for p in ("copper_charm", "padded_jerkin", "militia_spear"):
            gear.wear(s, p)
        self.assertEqual(levels.duel_effects(s), {"life": 25, "battlefield": ["token:w_1_1_soldier"]})
        gear.wear(s, "tallow_lantern")
        self.assertEqual(levels.duel_effects(s)["battlefield"], ["token:w_1_1_soldier", "token:c_a_clue_draw"])
        self.assertEqual(levels.duel_effects(s), levels.duel_effects(s))   # asking twice changes nothing

    def test_gear_adds_to_levels_and_perks(self):
        s = FakeSave(perks=["stout", "hoard", "quick_draw"])
        s.progress["xp"] = levels.read().xp_to_reach(4)
        own(s, "black_censer", "copper_charm")
        gear.wear(s, "black_censer")
        gear.wear(s, "copper_charm")
        # 20 + 3 levels + Stout 3 + charm 2 - censer 3 = 25; hand 7 + Quick Draw 1 + censer 1 = 9
        self.assertEqual(levels.duel_effects(s), {"life": 25, "hand": 9, "battlefield": [se.TREASURE]})

    def test_a_trade_off_never_takes_the_start_below_one_life(self):
        s = FakeSave()
        own(s, "black_censer")
        gear.wear(s, "black_censer")
        with mock.patch.object(gear.read().pieces["black_censer"], "effect", {"life": -20, "hand": -20}):
            e = levels.duel_effects(s)
        self.assertEqual((e["life"], e["hand"]), (1, 0))
        se.normal(e)                                                    # the bridge would take it

    def test_reward_picks_gold_and_walk_speed(self):
        s = FakeSave(perks=["keen_eye", "bounty"])
        own(s, "seekers_monocle", "road_boots", "gorsehide_cloak")
        self.assertEqual((levels.reward_choices(s, 3), levels.win_gold(s, 100)), (4, 110))   # nothing worn: perks only
        gear.wear(s, "seekers_monocle")
        self.assertEqual(levels.reward_choices(s, 3), 5)
        gear.take_off(s, "amulet")
        own(s, "merchants_token")
        gear.wear(s, "merchants_token")
        self.assertEqual(levels.win_gold(s, 100), round(100 * 1.1 * 1.15))
        self.assertEqual(gear.walk_speed(s), 1.0)
        gear.wear(s, "road_boots")
        gear.wear(s, "gorsehide_cloak")
        self.assertAlmostEqual(gear.walk_speed(s), 1.2 * 0.9)

    def test_a_piece_is_owned_once(self):
        s = FakeSave()
        self.assertIsNotNone(gear.give(s, "copper_charm"))
        self.assertIsNone(gear.give(s, "copper_charm"))
        self.assertEqual(s.gear["owned"], ["copper_charm"])
        self.assertIsNone(gear.give(s, "no_such_piece"))


# ==================================================================================================================================
class SourceTests(r8.Base):
    def game(self):
        r8.new_game(None)
        game, _n = adv.Game.load(1, pool=self.pool)
        return game

    def finish(self, game, kind, encounter, result="won", **extra):
        game.save.pending_duel = dict({"kind": kind, "encounter": encounter, "deck": 0, "source": "archetype", "map": None,
                                       "x": None, "y": None, "started": 0}, **extra)
        out = adv.finish_loaded(game, result)
        game.save.pending_pick = None
        return out

    def test_chests_give_their_piece_once(self):
        game = self.game()
        s = game.save
        for cid, pid in (("priest_hoard", "ossuary_ward"), ("moor_hollow", "wildsong_horn")):
            lines, _c = chests.open_chest(s, cid, progress.read().chests[cid], game.pool, game.tiers(), game.lists(),
                                          game.numbers, (1, 3), now=1000, refill_hour=7)
            self.assertIn(pid, s.gear["owned"])
            self.assertIn(gear.found_line(gear.read().piece(pid)), lines)
        self.assertEqual(len(s.gear["owned"]), 2)

    def test_a_hand_edited_number_is_still_gold(self):
        game = self.game()
        gold = game.save.gold
        lines, _c = chests.open_chest(game.save, "x", {"gold": 0, "gear": 80, "card": None, "pack": None, "daily": False},
                                      game.pool, game.tiers(), game.lists(), game.numbers, (1, 3), now=1000, refill_hour=7)
        self.assertEqual(game.save.gold, gold + 80)
        self.assertEqual(game.save.gear["owned"], [])

    def test_the_grave_priest_gives_his_censer_and_says_so(self):
        game = self.game()
        out = self.finish(game, "grave_priest", "boss")
        self.assertIn("black_censer", game.save.gear["owned"])
        lines = adv.outcome_lines(out, game.world)
        self.assertIn("New gear: Priest's Black Censer (Off-hand). Wear it from the Menu: Gear.", lines)
        out = self.finish(game, "grave_priest", "boss")                 # beaten again: nothing more
        self.assertFalse(any(ln.startswith("New gear") for ln in adv.outcome_lines(out, game.world)))
        self.assertEqual(game.save.gear["owned"].count("black_censer"), 1)

    def test_a_rare_foe_drops_its_piece_and_the_encounter_screen_says_which(self):
        game = self.game()
        trig = RareFoe("plague_rat", "mother_of_rats")
        self.assertIn("Carries: Seeker's Monocle (gear).", adv.fight_text(game, trig)[1])
        self.finish(game, "plague_rat", "dungeon", rare="mother_of_rats")
        self.assertIn("seekers_monocle", game.save.gear["owned"])
        self.assertNotIn("Carries: Seeker's Monocle (gear).", adv.fight_text(game, trig)[1])     # owned: no longer said

    def test_a_quest_gives_its_piece(self):
        game = self.game()
        w = game.save.world
        talk._quest(w, "lamplighters_debt")["state"] = "ready"
        conv = talk.Conversation("lamplighter", w)
        self.assertEqual(conv.reward_quest, "lamplighters_debt")
        lines = adv.quest_gear(game, conv.reward_quest)
        self.assertEqual(lines, [gear.found_line(gear.read().piece("bannerman_pike"))])
        self.assertIn("bannerman_pike", game.save.gear["owned"])
        self.assertEqual(adv.quest_gear(game, "lamplighters_debt"), [])

    def test_a_lost_duel_keeps_every_piece(self):
        game = self.game()
        own(game.save, "copper_charm", "road_boots")
        gear.wear(game.save, "copper_charm")
        game.save.gold = 300
        out = self.finish(game, "ember_bandit", "regular", result="lost")
        self.assertLess(game.save.gold, 300)
        self.assertEqual(game.save.gear, {"owned": ["copper_charm", "road_boots"], "worn": {"amulet": "copper_charm"}})
        self.assertEqual(r8.on_disk(self.folder)["gear"]["owned"], ["copper_charm", "road_boots"])
        self.assertEqual(adv.outcome_lines(out, game.world)[-1], "Worn: Copper Charm.")


# ==================================================================================================================================
class ShopTests(r8.Base):
    def town(self, size=(1360, 840), scale=None):
        g = self.gui(size, scale)
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        f.tab = "gear"
        frame(g)
        return g, f

    def test_only_common_pieces_not_owned_are_sold(self):
        s = FakeSave()
        offers = gear.shop_offers(s)
        self.assertEqual({p.rarity for p in offers}, {"common"})
        self.assertEqual(len(offers), sum(1 for p in gear.read().pieces.values() if p.rarity == "common"))
        own(s, "road_boots")
        self.assertNotIn("road_boots", [p.id for p in gear.shop_offers(s)])
        for pid in ("seekers_monocle", "road_boots"):                    # a found piece / one owned: never sold
            with self.assertRaises(gear.GearError):
                gear.buy(s, pid)

    def test_a_tap_on_buy_buys_and_writes(self):
        r8.new_game(None)
        g, f = self.town()
        f.game.save.gold = 500
        frame(g)
        names = [n for _r, n in f.buttons if n.startswith("gearbuy:")]
        self.assertEqual(sorted(n[8:] for n in names), sorted(p.id for p in gear.shop_offers(f.game.save)))
        rect = next(r for r, n in f.buttons if n == "gearbuy:copper_charm")
        click(g, rect.center)
        frame(g)
        self.assertEqual(f.game.save.gold, 460)
        disk = r8.on_disk(self.folder)
        self.assertEqual((disk["gold"], disk["gear"]["owned"]), (460, ["copper_charm"]))
        self.assertNotIn("gearbuy:copper_charm", [n for _r, n in f.buttons])
        self.assertIn("Bought Copper Charm for 40 gold.", f.message[0])

    def test_too_little_gold_greys_the_button(self):
        r8.new_game(None)
        g, f = self.town()
        f.game.save.gold = 10
        frame(g)
        self.assertNotIn("gearbuy:copper_charm", [n for _r, n in f.buttons])     # a greyed button takes no tap


# ==================================================================================================================================
class WindowTests(PlayedBase):
    def gear_screen(self, g):
        key(g, pygame.K_ESCAPE)
        frame(g)
        self.press(g, "gear")
        frame(g)
        self.assertEqual(g.modal.stage, "gear")
        return g.modal

    def test_menu_gear_wear_and_take_off_by_tap(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        own(f.game.save, "copper_charm", "road_boots")
        f.game.write()
        frame(g)
        f = self.gear_screen(g)
        self.assertIn("gear_wear:copper_charm", [n for _r, n in f.buttons])
        self.press(g, "gear_wear:copper_charm")
        self.assertEqual(r8.on_disk(self.folder)["gear"]["worn"], {"amulet": "copper_charm"})
        frame(g)
        self.assertNotIn("gear_wear:copper_charm", [n for _r, n in f.buttons])
        self.press(g, "gear_off:amulet")
        self.assertEqual(r8.on_disk(self.folder)["gear"]["worn"], {})
        frame(g)
        self.assertNotIn("gear_pack:copper_charm", [n for _r, n in f.buttons])    # no Pack without Pack Mule
        self.press(g, "gear_done")
        frame(g)
        self.assertEqual(f.stage, "map")

    def test_escape_and_the_wheel_on_the_gear_screen(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        own(f.game.save, *gear.read().pieces)
        f = self.gear_screen(g)
        f.wheel(g, -5)                                            # down the list
        self.assertGreater(f.scroll["gear"], 0)
        key(g, pygame.K_ESCAPE)
        self.assertEqual(f.stage, "map")

    def test_the_pack_button_with_pack_mule(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        f.game.save.progress["perks"] = ["pack_mule"]
        own(f.game.save, "copper_charm", "merchants_token")
        f.game.write()
        f = self.gear_screen(g)
        self.press(g, "gear_wear:copper_charm")
        frame(g)
        self.press(g, "gear_pack:merchants_token")
        self.assertEqual(r8.on_disk(self.folder)["gear"]["worn"], {"amulet": "copper_charm", "pack": "merchants_token"})

    def test_the_corner_panel_shows_the_worn_icons(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        frame(g)
        self.assertEqual(f.hud_icons, [])
        own(f.game.save, "copper_charm", "road_boots")
        gear.wear(f.game.save, "copper_charm")
        gear.wear(f.game.save, "road_boots")
        frame(g)
        self.assertEqual([p for p, _r in f.hud_icons], ["copper_charm", "road_boots"])
        W, H = g.screen.get_size()
        for _p, r in f.hud_icons:
            self.assertTrue(pygame.Rect(0, 0, W, H).contains(r))

    def test_the_walk_follows_the_boots(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        ex = f.explorer
        plain = explore.WALK_TILES_PER_SECOND * ex.area.tw
        self.assertAlmostEqual(ex.walker.speed, plain)
        own(f.game.save, "road_boots")
        f.game.write()
        f = self.gear_screen(g)
        self.press(g, "gear_wear:road_boots")
        self.assertAlmostEqual(f.explorer.walker.speed, plain * 1.2)
        ex._use(ex.area, explore.Walker(ex.area, ex.walker.x, ex.walker.y))         # a new map: still fast
        self.assertAlmostEqual(f.explorer.walker.speed, plain * 1.2)

    def test_the_results_screen_says_what_was_worn_and_the_duel_starts_with_it(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        own(f.game.save, "padded_jerkin", "ossuary_ward")
        gear.wear(f.game.save, "padded_jerkin")
        gear.wear(f.game.save, "ossuary_ward")
        f.game.write()
        enc = self.meet(g, encounters.RandomFoe("ember_bandit"))
        lines = adv.fight_text(f.game, encounters.RandomFoe("ember_bandit"))[1]
        self.assertIn("You start with 23 life and Sentinel Dispatch in your command zone.", lines)
        f = self.duel_to_result(g, won=True)
        self.assertEqual(launcher.calls[-1]["effects"], {"life": 23, "command": ["Sentinel Dispatch"]})
        self.assertIn("Worn: Ossuary Ward, Padded Jerkin.", f.results.info["lines"])


# ==================================================================================================================================
class SaveTests(r8.Base):
    def test_a_save_from_before_prog4_loads_with_nothing_owned_or_worn(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        d = json.load(open(path, encoding="utf-8"))
        d.pop("gear", None)
        json.dump(d, open(path, "w", encoding="utf-8"))
        game, _n = adv.Game.load(1, pool=self.pool)
        self.assertEqual(game.save.gear, {"owned": [], "worn": {}})
        self.assertEqual(gear.worn(game.save), [])
        self.assertEqual(levels.duel_effects(game.save), {})
        self.assertEqual(game.panel_info()["gear"], [])

    def test_a_damaged_gear_value_is_mended(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        d = json.load(open(path, encoding="utf-8"))
        d["gear"] = {"owned": ["copper_charm", 5, "copper_charm"], "worn": {"amulet": "copper_charm", "boots": "road_boots"}}
        json.dump(d, open(path, "w", encoding="utf-8"))
        game, _n = adv.Game.load(1, pool=self.pool)
        self.assertEqual(game.save.gear, {"owned": ["copper_charm"], "worn": {"amulet": "copper_charm"}})


# ==================================================================================================================================
class FitTests(PlayedBase):
    def inside(self, g, f, what):
        W, H = g.screen.get_size()
        whole = pygame.Rect(0, 0, W, H)
        for r, n in f.buttons:
            self.assertTrue(whole.contains(r), (what, n, r))
        others = [(r, n) for r, n in f.buttons if r.size != (W, H)]
        for i, (a, na) in enumerate(others):
            for b, nb in others[i + 1:]:
                self.assertFalse(a.colliderect(b), (what, na, nb, a, b))

    def test_every_new_screen_fits(self):
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                r8.new_game(None)
                g = self.gui(size=size, scale=scale)
                f = self.open(g, slot=1)
                self.on_map(g, "ashfen_outskirts")
                s = f.game.save
                s.progress["perks"] = ["pack_mule"]
                own(s, *[p for p in gear.read().pieces])
                for pid in ("black_censer", "ossuary_ward", "gorsehide_cloak", "wayfarer_boots", "bannerman_pike"):
                    gear.wear(s, pid)
                gear.wear(s, "seekers_monocle", "pack")
                f.game.write()
                frame(g)
                self.assertEqual(len(f.hud_icons), 6)
                W, H = g.screen.get_size()
                for _p, r in f.hud_icons:
                    self.assertTrue(pygame.Rect(0, 0, W, H).contains(r), r)
                key(g, pygame.K_ESCAPE)
                frame(g)
                self.press(g, "gear")
                frame(g)
                self.inside(g, f, "gear")
                for name in ("gear_off:weapon", "gear_done"):
                    self.assertIn(name, [n for _r, n in f.buttons])
                f.scroll["gear"] = 99
                frame(g)
                self.inside(g, f, "gear scrolled")
                self.assertTrue(any(n.startswith("gear_wear:") for _r, n in f.buttons))
                self.press(g, "gear_done")
                frame(g)
                trig = RareFoe("plague_rat", "mother_of_rats")
                s.gear["owned"].remove("seekers_monocle")
                s.gear["worn"].pop("pack")
                self.meet(g, trig)
                self.inside(g, f, "encounter")
                self.assertTrue(f.encounter is not None)
                f.sub = None
                f.enter_town(g, f.game.world.start_town)
                s.gear = {"owned": [], "worn": {}}
                f.tab = "gear"
                frame(g)
                self.inside(g, f, "shop")
                seen = {n for _r, n in f.buttons if n.startswith("gearbuy:")}
                self.assertTrue(seen)
                f.scroll["town:gear"] = 99                                 # the rest of the list, scrolled to (big text)
                frame(g)
                self.inside(g, f, "shop scrolled")
                seen |= {n for _r, n in f.buttons if n.startswith("gearbuy:")}
                self.assertEqual(seen, {f"gearbuy:{p.id}" for p in gear.shop_offers(s)})
                self.close(g)

    def test_the_results_screen_with_found_gear_and_a_worn_line_fits(self):
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                r8.new_game(None)
                g = self.gui(size=size, scale=scale)
                f = self.open(g, slot=1)
                self.on_map(g, "ashfen_outskirts")
                own(f.game.save, "copper_charm", "road_boots", "padded_jerkin")
                for p in ("copper_charm", "road_boots", "padded_jerkin"):
                    gear.wear(f.game.save, p)
                f.game.write()
                f = self.duel_to_result(g, won=True)
                key(g, pygame.K_RETURN)
                frame(g)
                W, H = g.screen.get_size()
                res = f.results
                self.assertIn("Worn: Copper Charm, Padded Jerkin, Road Boots.", res.info["lines"])
                self.assertTrue(pygame.Rect(0, 0, W, H).contains(res.rect))
                for r, _n in f.buttons:
                    self.assertTrue(res.rect.contains(r) or r.size == (W, H), r)
                self.close(g)


# ==================================================================================================================================
DECK = os.path.join(BASE, "sample_decks", "standard_red_aggro.txt")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/ (live Forge)")
class LiveTests(unittest.TestCase):
    def decks(self, effects):
        import deck_importer
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        c, cards = deck_importer.import_from_text(open(DECK, encoding="utf-8").read(), fmt="standard")
        mp = fc.write_deck_file(os.path.join(d, "player.dck"), c, cards, "P", fmt="standard", effects=effects)
        op = fc.write_deck_file(os.path.join(d, "opp.dck"), c, cards, "O", fmt="standard")
        return mp, op

    @staticmethod
    def seat(st, mine=True):
        return next(p for p in st["players"] if (p["id"] == st["me"]) == mine)

    @staticmethod
    def zone(p, z):
        return [c.get("name") for c in (p.get("zones") or {}).get(z) or ()]

    def play_until(self, s, done, limit=180):
        """Keep the hand, then pass priority (and every other question with OK / the first choice) until done(state)."""
        import card_check
        t0 = time.time()
        last = None
        mem = {}
        while time.time() - t0 < limit:
            s.poll()
            if s.fatal:
                self.fail(s.fatal)
            st = s.state
            if st and done(st):
                return st
            for req in list(s.requests):                                  # e.g. the order of two relics' triggers
                s.answer(req, card_check.request_answer(req, mem))
            seq = (st or {}).get("inputSeq")
            if st and (st.get("prompt") or {}).get("buttons", {}).get("ok", {}).get("enabled", True) and seq != last:
                last = seq
                s.ok()
            time.sleep(0.15)
        self.fail("the game never got there")

    def test_the_relics_work_from_the_command_zone(self):
        eff = {"command": ["Sentinel Dispatch", "Hold the Perimeter"]}
        mp, op = self.decks(eff)
        s = fc.ForgeSession(mp, [op], name="Me", seed=5).start()
        self.addCleanup(s.close)

        def tokens(st):
            names = self.zone(self.seat(st), "battlefield")
            return "Construct Token" in names and "Soldier Token" in names
        st = self.play_until(s, tokens)
        me = self.seat(st)
        self.assertEqual(sorted(self.zone(me, "command")), ["Hold the Perimeter", "Sentinel Dispatch"])
        self.assertEqual(self.zone(self.seat(st, False), "command"), [])

    def test_the_gears_tokens_and_life(self):
        mp, op = self.decks({"life": 23, "battlefield": ["token:w_1_1_soldier", "token:c_a_clue_draw"]})
        s = fc.ForgeSession(mp, [op], name="Me", seed=7).start()
        self.addCleanup(s.close)
        st = self.play_until(s, lambda st: bool(self.zone(self.seat(st), "battlefield")))
        me = self.seat(st)
        self.assertEqual(me["life"], 23)
        self.assertEqual(sorted(self.zone(me, "battlefield")), ["Clue Token", "Soldier Token"])

    def test_duels_applies_a_gear_spec_with_a_relic(self):
        from tools import rpg_balance
        mp, op = self.decks(None)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        games = rpg_balance.run_pairs([("x", mp, op, 1, 3, "life:25;command:Sentinel Dispatch")], d, workers=1,
                                      log=lambda *_a: None)
        self.assertEqual(len(games), 1, games)
        self.assertNotIn("error", games[0])
        log = open(os.path.join(d, "java_0.log"), encoding="utf-8", errors="replace").read()
        self.assertIn("starteffects: life=25", log)
        self.assertIn("Sentinel Dispatch", log)


if __name__ == "__main__":
    unittest.main()
