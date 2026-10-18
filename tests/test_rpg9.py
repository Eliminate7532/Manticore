# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG9: an easier first map (Karl, 14 Oct 2026: "adventure mode is way too hard"; then "Jumpstart foes first", "Two colours
max", "About 65%").

  - A half-deck's colours (rpg.products.colours) and the new-game rule: the three picks use at most two colours (rpg.newgame).
  - Which deck list a fight uses (towns.json "enemy_decks", economy.World.deck_source, rpg.duel.start): on region 1 the map's
    regular foes play their Jumpstart decks, the cave's dungeon foes and its boss their archetype decks; the reward cards come from
    the list that was played.
  - Enemy Jumpstart decks (rpg.enemies): three half-decks, at most two colours with the kind's colour in two of them, every card in
    the region's band, nothing Forge's AI is told to leave out, and a handicap: the deck's strongest spells swapped for basic lands.
  - The shipped data: each first-map foe has four such decks, and the balance measurement (rpg/data/balance.json: Forge's AI against
    Forge's AI, tools/rpg_balance.py) is of exactly these decks and lands near the target.
  - tools/rpg_balance.py's numbers, and (live, with Java) one duel through java_rpg/rpg_tools.jar's Duels.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpg import cardpool, duel, economy, enemies, newgame, products, rules, tiers
from tests import live
from tests.test_rpg4 import POOL, FakeTiers, Temp, half, lists, numbers, world

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("FORGE_RUNTIME") or os.path.join(BASE, "forge_runtime")
HAS_RUNTIME = os.path.isfile(os.path.join(RUNTIME, rules.VINTAGE_FILE))
BALANCE_FILE = os.path.join(BASE, "rpg", "data", "balance.json")

sys.path.insert(0, os.path.join(BASE, "tools"))
import rpg_balance  # noqa: E402

W1 = half("TST Cats 1", [("Savannah Lions", 4), ("Pacifism", 4), ("Plains", 12)])
W2 = half("TST Lions 2", [("Savannah Lions", 2), ("Swords to Plowshares", 1), ("Plains", 12)])
G1 = half("TST Elves 1", [("Llanowar Elves", 4), ("Grizzly Bears", 4), ("Forest", 12)])
G2 = half("TST Trees 2", [("Llanowar Elves", 2), ("Tree Folk", 4), ("Wild Growth", 2), ("Forest", 12)])
U1 = half("TST Islands 1", [("Counterspell", 2), ("Lim-Dûl's Vault", 2), ("Island", 16)])
R1 = half("TST Bolts 1", [("Lightning Bolt", 4), ("Island", 16)])
C1 = half("TST Thopters 1", [("Ornithopter", 4), ("Forest", 16)])
HALVES = [W1, W2, G1, G2, U1, R1, C1]


def flow(**kw):
    f = newgame.NewGameFlow(seed=kw.pop("seed", 3), decks=kw.pop("decks", HALVES), pool=POOL, lists=lists(), numbers=numbers(),
                            world=world(), offer_size=kw.pop("offer_size", 7))
    f.set_name("Karl")
    return f


# ==================================================================================================================================
class ColourTests(unittest.TestCase):
    def test_a_half_decks_colours_are_its_spells_colours(self):
        self.assertEqual(products.colours(W1, POOL), "W")
        self.assertEqual(products.colours(G2, POOL), "G")
        self.assertEqual(products.colours(U1, POOL), "UB")         # Lim-Dul's Vault is blue and black in the made-up pool
        self.assertEqual(products.colours(C1, POOL), "")           # only an artifact and lands
        self.assertEqual(products.colours_of([W1, G1, C1], POOL), "WG")
        self.assertEqual(products.colours_of([], POOL), "")

    def test_colour_words(self):
        self.assertEqual(products.colour_words(""), "colourless")
        self.assertEqual(products.colour_words("G"), "Green")
        self.assertEqual(products.colour_words("UG"), "Blue and Green")
        self.assertEqual(products.colour_words("WUB"), "White, Blue and Black")


class NewGameTests(unittest.TestCase):
    def test_any_first_pick_and_any_one_colour_second(self):
        f = flow()
        self.assertEqual({h.code for h in f.offer()}, {h.code for h in HALVES if len(products.colours(h, POOL)) <= 2})
        f.pick("TST Cats 1")
        self.assertEqual({products.colours(h, POOL) for h in f.offer()}, {"W", "G", "", "R"})   # U1 is two colours: W+U+B = 3
        self.assertEqual(f.colours(), "W")

    def test_after_two_colours_only_those_are_offered_and_browsable(self):
        f = flow()
        f.pick("TST Cats 1")
        f.pick("TST Elves 1")
        for h in f.offer() + f.all_choices():
            self.assertTrue(set(products.colours(h, POOL)) <= {"W", "G"}, h.code)
        self.assertEqual(sorted(h.code for h in f.all_choices()), ["TST Lions 2", "TST Thopters 1", "TST Trees 2"])

    def test_a_third_colour_is_refused_in_words(self):
        f = flow()
        f.pick("TST Cats 1")
        f.pick("TST Elves 1")
        with self.assertRaises(ValueError) as cm:
            f.pick("TST Bolts 1")
        self.assertIn("3 colours (White, Red and Green)", str(cm.exception))
        self.assertEqual(len(f.picks), 2)
        f.pick("TST Thopters 1")                                    # colourless keeps it at two
        self.assertEqual(f.step, "done")
        self.assertEqual(f.colours(), "WG")

    def test_the_colour_note_says_where_the_rule_stands(self):
        f = flow()
        self.assertIn("At most 2 colours", f.colour_note())
        f.pick("TST Cats 1")
        self.assertIn("White", f.colour_note())
        self.assertIn("one more colour at most", f.colour_note())
        f.pick("TST Elves 1")
        self.assertIn("White and Green", f.colour_note())
        self.assertIn("Only half-decks of these colours", f.colour_note())

    def test_undo_frees_the_colours_again(self):
        f = flow()
        f.pick("TST Cats 1")
        f.pick("TST Elves 1")
        f.undo()
        self.assertIn("TST Bolts 1", [h.code for h in f.all_choices()])


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (the Jumpstart lists)")
class RealHalvesTests(unittest.TestCase):
    def test_forges_half_decks_are_one_colour_but_three(self):
        pool = cardpool.load()
        counts = {}
        for h in products.jumpstart():
            counts[len(products.colours(h, pool))] = counts.get(len(products.colours(h, pool)), 0) + 1
        self.assertEqual(counts, {1: 360, 5: 2, 0: 1})

    def test_random_new_games_always_have_two_colours_at_most_and_an_offer_to_pick_from(self):
        pool, lists_ = cardpool.load(), rules.vintage_lists()
        decks = rpg_balance.starter_decks(40, 7, "two", pool=pool, lists=lists_)
        for name, cards, codes in decks:
            halves = [h for h in products.jumpstart() if h.code in codes]
            self.assertLessEqual(len(products.colours_of(halves, pool)), 2, codes)
            self.assertGreaterEqual(sum(k for _n, k in cards), 60, name)

    def test_the_old_rule_made_three_colour_decks(self):
        pool, lists_ = cardpool.load(), rules.vintage_lists()
        decks = rpg_balance.starter_decks(20, 7, "any", pool=pool, lists=lists_)
        three = 0
        for _n, _c, codes in decks:
            halves = [h for h in products.jumpstart() if h.code in codes]
            three += len(products.colours_of(halves, pool)) >= 3
        self.assertGreater(three, 5)                                # Karl's 13 Oct deck: Angels, Beasts, Spirits


# ==================================================================================================================================
class WorldTests(unittest.TestCase):
    def test_a_region_without_enemy_decks_plays_archetype_decks_as_before(self):
        w = world()
        for e in economy.ENCOUNTERS:
            self.assertEqual(w.deck_source(1, e), "archetype")
        self.assertEqual(w.deck_source(9, "regular"), "archetype")

    def test_the_shipped_region_one(self):
        w = economy.read_world()
        self.assertEqual({e: w.deck_source(1, e) for e in economy.ENCOUNTERS},
                         {"regular": "jumpstart", "dungeon": "archetype", "boss": "archetype"})

    def test_bad_values_are_refused_by_name(self):
        for bad in ({"regular": "easy"}, {"picnic": "jumpstart"}, ["jumpstart"]):
            d = {"start_town": "green_town", "regions": {"1": {"name": "R1", "tiers": [1, 3], "enemy_decks": bad}},
                 "towns": [{"id": "green_town", "name": "G", "region": 1}]}
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                economy.World(d)
            self.assertIn("enemy_decks", str(cm.exception))


JS = {"name": "Elf Gang J", "archetype": "Jumpstart", "halves": ["TST Elves 1", "TST Trees 2", "TST Cats 1"],
      "main": [["Savannah Lions", 4], ["Pacifism", 4], ["Llanowar Elves", 4], ["Grizzly Bears", 4], ["Forest", 44]], "sideboard": []}
KIND = {"kind": "elf_gang", "name": "Elf Gang", "decks": [{"name": "Elf Gang 1", "archetype": "x",
                                                           "main": [["Tree Folk", 4], ["Forest", 56]], "sideboard": []}],
        "jumpstart": [JS], "rejected": [], "soak": None}
OLD_KIND = dict(KIND, kind="old_gang", jumpstart=None)
OLD_KIND.pop("jumpstart")


class DuelSourceTests(Temp):
    def setUp(self):
        super().setUp()
        for data in (KIND, OLD_KIND):
            with open(os.path.join(self.tmp, data["kind"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f)
        from rpg import shop
        shop._CANDIDATES.clear()

    def save(self):
        from tests.test_rpg4 import started_save
        return started_save()

    def start(self, s, kind="elf_gang", encounter="regular", **kw):
        return duel.start(s, kind, encounter, POOL, lists(), folder=self.tmp, now=5.0, **kw)

    def test_a_regular_fight_on_region_one_plays_a_jumpstart_deck(self):
        s = self.save()
        d = self.start(s)                                    # no source given: the region decides (the shipped towns.json)
        self.assertEqual((d.source, d.enemy_deck["name"]), ("jumpstart", "Elf Gang J"))
        self.assertEqual(s.pending_duel["source"], "jumpstart")

    def test_dungeon_and_boss_fights_play_archetype_decks(self):
        for enc in ("dungeon", "boss"):
            s = self.save()
            d = self.start(s, encounter=enc)
            with self.subTest(enc=enc):
                self.assertEqual((d.source, d.enemy_deck["name"]), ("archetype", "Elf Gang 1"))
                self.assertEqual(s.pending_duel["source"], "archetype")

    def test_a_kind_without_jumpstart_decks_plays_its_archetype_decks(self):
        s = self.save()
        d = self.start(s, kind="old_gang")
        self.assertEqual((d.source, d.enemy_deck["name"]), ("archetype", "Elf Gang 1"))

    def test_the_caller_can_say_which_list(self):
        s = self.save()
        self.assertEqual(self.start(s, source="archetype").source, "archetype")

    def test_an_unreadable_region_file_means_archetype(self):
        s = self.save()
        with mock.patch.object(economy, "read_world", side_effect=OSError("gone")):
            self.assertEqual(self.start(s).source, "archetype")

    def test_the_reward_comes_from_the_deck_that_was_played(self):
        s = self.save()
        self.start(s)
        out = duel.finish(s, "won", numbers(), world(), POOL, FakeTiers(), lists(), folder=self.tmp)
        js_cards = {n for n, _k in JS["main"]}
        for name, src in out["cards"]:
            if src == "deck":
                self.assertIn(name, js_cards)
        self.assertTrue(any(src == "deck" for _n, src in out["cards"]), out["cards"])

    def test_the_cards_a_handicap_swapped_out_can_be_won(self):
        data = dict(KIND, jumpstart=[dict(JS, handicap=4, swapped=[["Tree Folk", 4]])])
        with open(os.path.join(self.tmp, "elf_gang.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        s = self.save()
        self.start(s)
        seen = []
        real = duel.reward_cards
        with mock.patch.object(duel, "reward_cards", lambda save, deck, *a, **k: seen.append(deck) or real(save, deck, *a, **k)):
            duel.finish(s, "won", numbers(), world(), POOL, FakeTiers(), lists(), folder=self.tmp)
        names = [n for n, _k in seen[0]["main"]]
        self.assertIn("Tree Folk", names)
        self.assertIn("Savannah Lions", names)

    def test_a_duel_marked_before_rpg9_reads_the_archetype_list(self):
        s = self.save()
        s.pending_duel = {"kind": "elf_gang", "encounter": "regular", "deck": 0, "map": "green_town", "x": None, "y": None,
                          "started": 1.0}
        out = duel.finish(s, "won", numbers(), world(), POOL, FakeTiers(), lists(), folder=self.tmp)
        arch = {"Tree Folk"}
        self.assertTrue(all(name in arch for name, src in out["cards"] if src == "deck"), out["cards"])


# ==================================================================================================================================
class JumpstartRuleTests(unittest.TestCase):
    KIND_G = {"id": "elf_gang", "name": "Elf Gang", "colour": "G"}

    def problems(self, halves):
        return enemies.jumpstart_problems(self.KIND_G, halves, POOL, lists())

    def test_two_green_halves_and_one_more_colour_is_fine(self):
        self.assertEqual(self.problems([G1, G2, W1]), [])
        self.assertEqual(self.problems([G1, G2, C1]), [])

    def test_three_colours_one_green_half_or_no_green_are_refused(self):
        self.assertTrue(any("colours" in p for p in self.problems([G1, W1, R1])))
        self.assertTrue(any("fewer than two" in p for p in self.problems([G1, W1, W2])))
        self.assertTrue(any("missing" in p for p in self.problems([W1, W2, C1])))
        self.assertTrue(any("different" in p for p in self.problems([G1, G1, W1])))

    def test_a_half_with_a_card_the_ai_leaves_out_or_above_the_band_is_refused(self):
        flags = lambda n: ["All"] if n == "Wild Growth" else []          # noqa: E731
        self.assertTrue(any("leave these out" in p for p in enemies.half_problems(G2, (1, 3), POOL, FakeTiers(), flags)))
        self.assertTrue(any("above tier 3" in p for p in enemies.half_problems(G1, (1, 3), POOL, FakeTiers(), lambda n: [])))
        self.assertEqual(enemies.half_problems(W1, (1, 3), POOL, FakeTiers(), lambda n: []), [])

    def test_candidates_are_the_same_every_run_and_all_pass(self):
        halves = [W1, W2, G1, G2, C1, half("TST Elves 3", [("Grizzly Bears", 4), ("Tree Folk", 4), ("Forest", 12)])]
        kw = dict(band=(1, 5), decks=halves, pool=POOL, lists=lists(), tier_table=FakeTiers(), flags=lambda n: [])
        a = enemies.jumpstart_candidates(self.KIND_G, 5, 11, **kw)
        b = enemies.jumpstart_candidates(self.KIND_G, 5, 11, **kw)
        self.assertEqual(a, b)
        c = enemies.jumpstart_candidates(self.KIND_G, 5, 11, swap=3, **kw)        # the same half-decks at any handicap
        self.assertEqual([d["halves"] for d in c], [d["halves"] for d in a])
        self.assertTrue(all(d["handicap"] == 3 for d in c))
        self.assertGreater(len(a), 1)
        by_code = {h.code: h for h in halves}
        for d in a:
            self.assertEqual(self.problems([by_code[c] for c in d["halves"]]), [])
            self.assertEqual([tuple(x) for x in d["main"]], products.starter_deck([by_code[c] for c in d["halves"]], POOL,
                                                                                   lists()))
        self.assertEqual(len({tuple(sorted(d["halves"])) for d in a}), len(a))

    DECK = [("Llanowar Elves", 4), ("Grizzly Bears", 4), ("Tree Folk", 4), ("Savannah Lions", 2), ("Forest", 12), ("Plains", 6)]

    def test_the_handicap_swaps_the_strongest_spells_for_basics_in_proportion(self):
        out, gone = enemies.handicap(self.DECK, 6, POOL, FakeTiers())     # tier 4 Elves x4, then tier 2 Lions x2
        self.assertEqual(gone, [("Llanowar Elves", 4), ("Savannah Lions", 2)])
        self.assertEqual(out, [("Grizzly Bears", 4), ("Tree Folk", 4), ("Forest", 16), ("Plains", 8)])
        out, gone = enemies.handicap(self.DECK, 7, POOL, FakeTiers())     # then the dearer of the tier-1 cards: Tree Folk (3)
        self.assertEqual(dict(gone)["Tree Folk"], 1)
        self.assertEqual(dict(out)["Forest"] + dict(out)["Plains"], 18 + 7)
        self.assertEqual((dict(out)["Forest"], dict(out)["Plains"]), (17, 8))  # 4.67 and 2.33 -> the larger remainder gets the 7th
        for k in (0, 1, 7, 14, 40):
            out, _gone = enemies.handicap(self.DECK, k, POOL, FakeTiers())
            self.assertEqual(sum(c for _n, c in out), sum(c for _n, c in self.DECK), k)

    def test_the_handicap_never_swaps_lands_and_leaves_a_deck_without_basics_alone(self):
        out, gone = enemies.handicap(self.DECK, 40, POOL, FakeTiers())
        self.assertEqual(sum(c for _n, c in gone), 14)                   # every spell, no land
        self.assertEqual(out, [("Forest", 12 + 9), ("Plains", 6 + 5)])                 # 9.33 and 4.67: the larger remainder
        self.assertEqual(enemies.handicap(self.DECK, 0, POOL, FakeTiers()), (self.DECK, []))
        spells_only = [("Grizzly Bears", 4), ("Tree Folk", 4)]
        self.assertEqual(enemies.handicap(spells_only, 3, POOL, FakeTiers()), (spells_only, []))

    def test_a_jumpstart_deck_records_its_handicap(self):
        d = enemies.jumpstart_deck(self.KIND_G, [G1, G2, W1], POOL, lists(), swap=5, tier_table=FakeTiers())
        main, gone = enemies.handicap(products.starter_deck([G1, G2, W1], POOL, lists()), 5, POOL, FakeTiers())
        self.assertEqual((d["handicap"], d["main"], d["swapped"]), (5, [[n, c] for n, c in main], [[n, c] for n, c in gone]))
        self.assertEqual(enemies.jumpstart_deck(self.KIND_G, [G1, G2, W1], POOL, lists())["handicap"], 0)

    def test_write_jumpstart_keeps_the_archetype_decks(self):
        tmp = tempfile.mkdtemp(prefix="rpg9_")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "elf_gang.json"), "w", encoding="utf-8") as f:
            json.dump(KIND, f)
        enemies.write_jumpstart("elf_gang", [dict(JS)], {"target": 0.65}, tier_table=FakeTiers(), folder=tmp)
        data = enemies.read_kind("elf_gang", tmp)
        self.assertEqual(data["decks"], KIND["decks"])
        self.assertEqual(data["jumpstart"][0]["name"], "Elf Gang J")
        self.assertEqual(data["jumpstart_balance"], {"target": 0.65})
        self.assertIn("tiers", data["jumpstart"][0])


# ==================================================================================================================================
@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (the Jumpstart lists, Vintage's lists, Forge's card scripts)")
class ShippedTests(unittest.TestCase):
    def setUp(self):
        self.pool, self.lists = cardpool.load(), rules.vintage_lists()
        self.tiers = tiers.load(pool=self.pool)
        self.by_code = {h.code: h for h in products.jumpstart()}

    def test_the_first_maps_foes(self):
        self.assertEqual(enemies.regular_kinds(), ["ember_bandit", "grave_robber", "thornback", "zealot"])

    def test_every_first_map_foe_has_four_jumpstart_decks_that_pass_the_rules(self):
        band = economy.read_world().band(1)
        for kind in enemies.read_kinds():
            if kind["id"] not in enemies.regular_kinds():
                continue
            data = enemies.read_kind(kind["id"])
            with self.subTest(kind=kind["id"]):
                js = data.get("jumpstart") or []
                self.assertEqual(len(js), 4)
                self.assertEqual(len(data["decks"]), 4)                     # the archetype decks are still there (the cave uses them)
                for d in js:
                    halves = [self.by_code[c] for c in d["halves"]]
                    self.assertEqual(enemies.jumpstart_problems(kind, halves, self.pool, self.lists), [], d["name"])
                    for h in halves:
                        self.assertEqual(enemies.half_problems(h, band, self.pool, self.tiers), [], (d["name"], h.code))
                    main, gone = enemies.handicap(products.starter_deck(halves, self.pool, self.lists), d["handicap"], self.pool,
                                                  self.tiers)
                    self.assertEqual(([tuple(x) for x in d["main"]], [tuple(x) for x in d["swapped"]]), (main, gone), d["name"])
                    self.assertGreaterEqual(sum(k for _n, k in d["main"]), 60)
                    self.assertEqual(rules.check([tuple(x) for x in d["main"]], [], self.pool, self.lists), [], d["name"])
                    self.assertEqual(d["handicap"], data["jumpstart_balance"]["handicap"])

    def test_the_balance_file_measured_exactly_these_decks_and_landed_near_the_target(self):
        with open(BALANCE_FILE, encoding="utf-8") as f:
            b = json.load(f)
        self.assertEqual(b["decks_sha"], enemies.jumpstart_digest(enemies.regular_kinds()))
        self.assertEqual(b["target"], 0.65)
        self.assertEqual(b["handicap"], enemies.read_kind("thornback")["jumpstart"][0]["handicap"])
        self.assertEqual(b["measured"]["handicap"], [b["handicap"]])                 # the run itself says which decks it played
        m = b["measured"]
        self.assertEqual((m["rule"], m["source"]), ("two", "jumpstart"))
        self.assertGreaterEqual(m["all"]["games"], 500)
        self.assertLessEqual(abs(m["all"]["share"] - b["target"]), 0.07, m["all"])
        self.assertEqual(sorted(m["kinds"]), enemies.regular_kinds())
        self.assertGreater(m["all"]["low"], 0.5)                           # the interval itself is above an even game
        self.assertLess(b["before"]["all"]["share"], m["all"]["share"])    # easier than what Karl played on 13-14 Oct

    def test_the_cave_still_plays_its_archetype_decks(self):
        w = economy.read_world()
        for kind_id in ("plague_rat", "grave_priest"):
            data = enemies.read_kind(kind_id)
            self.assertFalse(data.get("jumpstart"), kind_id)
            self.assertEqual(enemies.deck_list(data, w.deck_source(1, "dungeon"))[0], "archetype")


# ==================================================================================================================================
from tests import test_rpg8 as r8  # noqa: E402  (pygame, the real table with a stand-in engine)


class ScreenTests(r8.Base):
    """The new-game screen says each offered half-deck's colours and where the rule stands, at every size, without its words
    landing on other words."""

    def to_pick(self, size=(1360, 840), scale=None):
        g = self.gui(size, scale)
        f = self.open(g)
        self.press(g, "new:2")
        for ch in "Karl":
            r8.key(g, getattr(r8.pygame, "K_" + ch.lower()), unicode=ch)
        r8.key(g, r8.pygame.K_RETURN)
        self.assertEqual(f.stage, "pick")
        return g, f

    def words(self, g):
        return r8.FitTests.words(self, g)

    def test_the_intro_names_the_rule(self):
        self.assertIn("at most two colours", r8.rs.PICK_INTRO)

    def test_each_offer_shows_its_colours_and_the_note_follows_the_picks(self):
        g, f = self.to_pick()
        shown = " ".join(s for s, _r in self.words(g))
        for h in f.flow.offer():
            self.assertIn(products.colour_words(products.colours(h, self.pool)), shown, h.code)
        self.assertIn("At most 2 colours in all.", shown)
        first = f.flow.offer()[0]
        r8.click(g, next(r for r, k, d in f.rows if k == "pick" and d == first.code).center)
        shown = " ".join(s for s, _r in self.words(g))
        self.assertIn(f"So far {products.colour_words(products.colours(first, self.pool))}", shown)
        second = next(h for h in f.flow.offer() if products.colours(h, self.pool) not in ("", products.colours(first, self.pool)))
        r8.click(g, next(r for r, k, d in f.rows if k == "pick" and d == second.code).center)
        shown = " ".join(s for s, _r in self.words(g))
        self.assertIn("Your colours: " + products.colour_words(f.flow.colours()), shown)
        for h in f.flow.offer():
            self.assertTrue(set(products.colours(h, self.pool)) <= set(f.flow.colours()), h.code)

    def test_the_note_fits_and_lands_on_no_other_words(self):
        for size, scale in r8.SIZES:
            with self.subTest(size=size, scale=scale):
                g, f = self.to_pick(size, scale)
                f.pick(g, f.flow.offer()[0].code)
                drawn = self.words(g)
                W, H = g.screen.get_size()
                note = [(s, r) for s, r in drawn if len(s) >= 8 and s.strip() in f.flow.colour_note()]
                self.assertEqual(" ".join(s for s, _r in note).split(), f.flow.colour_note().split())   # whole, not cut
                for s, r in note:
                    self.assertTrue(r8.pygame.Rect(0, 0, W, H).contains(r), (s, r))
                    for t, q in drawn:
                        if (t, q) != (s, r):
                            self.assertFalse(r.inflate(-2, -2).colliderect(q.inflate(-2, -2)), f"{s!r} at {r} on {t!r} at {q}")
                self.close(g)


# ==================================================================================================================================
class BalanceToolTests(unittest.TestCase):
    def test_wilson(self):
        lo, hi = rpg_balance.wilson(65, 100)
        self.assertAlmostEqual(lo, 0.5525, places=3)
        self.assertAlmostEqual(hi, 0.7364, places=3)
        self.assertEqual(rpg_balance.wilson(0, 0), (0.0, 1.0))

    def test_tally_leaves_draws_out_of_the_share(self):
        games = [{"winner": "A", "turns": 10}] * 6 + [{"winner": "B", "turns": 8}] * 4 + [{"winner": "draw", "turns": 0}] * 3
        t = rpg_balance.tally(games)
        self.assertEqual((t["games"], t["starter"], t["enemy"], t["draws"], t["share"]), (13, 6, 4, 3, 0.6))
        self.assertEqual(t["turns"], 9.2)

    def test_the_summary_keeps_every_number_beside_the_run_info(self):
        starters = [("Starter 1", [], ["A"]), ("Starter 2", [], ["B"])]
        foes = [("thornback", "Thornback 1", []), ("zealot", "Zealot 1", [])]
        labels = {"s0_e0": (0, 0), "s1_e1": (1, 1)}
        played = [{"id": "s0_e0", "winner": "A", "turns": 9}, {"id": "s1_e1", "winner": "B", "turns": 7}]
        s = rpg_balance.with_info(rpg_balance.summarize(played, labels, starters, foes),
                                  rpg_balance.run_info("jumpstart", "two", 2, 1, 5))
        self.assertEqual(s["starters"], 2)
        self.assertEqual(s["by_starter"]["Starter 1"]["starter"], 1)
        self.assertEqual(s["by_starter"]["Starter 2"]["halves"], ["B"])
        self.assertEqual(sorted(s["kinds"]), ["thornback", "zealot"])
        with self.assertRaises(ValueError):
            rpg_balance.with_info({"rule": 1}, {"rule": 2})

    def test_suggest_draws_lines_between_the_measured_handicaps(self):
        self.assertEqual(rpg_balance.suggest([(6, 0.55), (12, 0.75)], 0.65), 9)
        self.assertEqual(rpg_balance.suggest([(12, 0.75), (6, 0.55)], 0.65), 9)          # any order
        self.assertEqual(rpg_balance.suggest([(6, 0.70), (12, 0.80)], 0.65), 6)          # outside: the nearest end
        self.assertEqual(rpg_balance.suggest([(0, 0.47), (6, None), (12, 0.71)], 0.65), 9)
        self.assertIsNone(rpg_balance.suggest([(6, None)], 0.65))

    def test_record_writes_the_balance_file_from_the_runs(self):
        tmp = tempfile.mkdtemp(prefix="rpg9_rec_")
        self.addCleanup(shutil.rmtree, tmp, True)
        kinds = {"thornback": {"games": 10, "starter": 7, "enemy": 3, "draws": 0, "share": 0.7, "low": 0.4, "high": 0.9}}
        for name, info in (("m", {"source": "jumpstart", "rule": "two"}), ("b", {"source": "archetype", "rule": "any"})):
            os.makedirs(os.path.join(tmp, name))
            with open(os.path.join(tmp, name, "summary.json"), "w", encoding="utf-8") as f:
                json.dump(dict(info, all=kinds["thornback"], kinds=kinds, decks={"x": 1}, by_starter={"y": 2}), f)
        out = os.path.join(tmp, "balance.json")
        data = rpg_balance.record(os.path.join(tmp, "m"), os.path.join(tmp, "b"), path=out)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)
        self.assertEqual(data["decks_sha"], enemies.jumpstart_digest(["thornback"]))
        self.assertNotIn("decks", data["measured"])                                         # the per-deck lines stay in the run
        self.assertEqual(data["target"], 0.65)
        with self.assertRaises(SystemExit):                                                  # the wrong run for "measured"
            rpg_balance.record(os.path.join(tmp, "b"), os.path.join(tmp, "b"), path=out)
        with open(os.path.join(tmp, "m", "summary.json"), encoding="utf-8") as f:
            m = json.load(f)
        with open(os.path.join(tmp, "m", "summary.json"), "w", encoding="utf-8") as f:                  # decks other than the files'
            json.dump(dict(m, handicap=[99]), f)
        with self.assertRaises(SystemExit):
            rpg_balance.record(os.path.join(tmp, "m"), os.path.join(tmp, "b"), path=out)

    def test_tuning_steps_from_tune_and_measure_folders(self):
        tmp = tempfile.mkdtemp(prefix="rpg9_steps_")
        self.addCleanup(shutil.rmtree, tmp, True)
        t = {"seed": 3, "starters": 10, "games_per_pair": 1, "handicaps": {"12": {"all": {"share": 0.7}}, "6": {"all": {"share": 0.57}}}}
        os.makedirs(os.path.join(tmp, "t"))
        os.makedirs(os.path.join(tmp, "v"))
        with open(os.path.join(tmp, "t", "tune.json"), "w", encoding="utf-8") as f:
            json.dump(t, f)
        with open(os.path.join(tmp, "v", "summary.json"), "w", encoding="utf-8") as f:
            json.dump({"seed": 4, "starters": 16, "games_per_pair": 2, "all": {"share": 0.69}}, f)
        steps = rpg_balance.tuning_steps([os.path.join(tmp, "t"), os.path.join(tmp, "v") + "@10"])
        self.assertEqual([(x["handicap"], x["all"]["share"]) for x in steps], [(6, 0.57), (10, 0.69), (12, 0.7)])

    def test_the_java_command_runs_duels_from_rpg_tools(self):
        with mock.patch("forge_client.find_java", return_value="java"):
            cmd, cwd = rpg_balance.java_command("p.tsv", "o.jsonl", 90)
        self.assertEqual(cmd[cmd.index("-cp") + 2:], ["forge.rpgtools.Duels", "p.tsv", "o.jsonl", "90"])
        self.assertIn("rpg_tools.jar", cmd[cmd.index("-cp") + 1])

    def test_duels_is_in_the_tools_jar_and_the_jar_is_built_from_the_sources(self):
        import zipfile
        import rpg_build_data
        self.assertTrue(os.path.isfile(os.path.join(BASE, "java_rpg", "src", "forge", "rpgtools", "Duels.java")))
        with zipfile.ZipFile(rpg_balance.JAR) as z:
            self.assertIn("forge/rpgtools/Duels.class", z.namelist())
        self.assertEqual(rpg_build_data.jar_stamp(), rpg_build_data.source_hash())


@unittest.skipUnless(HAS_RUNTIME and live.live_enabled(), "needs Java and forge_runtime/ (live)")
class LiveTests(unittest.TestCase):
    def test_one_duel_between_a_starting_deck_and_a_first_map_foe(self):
        tmp = tempfile.mkdtemp(prefix="rpg9_live_")
        self.addCleanup(shutil.rmtree, tmp, True)
        name, cards, _codes = rpg_balance.starter_decks(1, 5, "two")[0]
        foe = rpg_balance.enemy_decks(["thornback"], "jumpstart")[0]
        a = rpg_balance.write_dck(tmp, name, cards)
        b = rpg_balance.write_dck(tmp, foe[1], foe[2])
        games = rpg_balance.run_pairs([("live", a, b, 1, 5)], tmp, workers=1, log=lambda *_: None)
        self.assertEqual(len(games), 1, open(os.path.join(tmp, "java_0.log"), encoding="utf-8").read()[-2000:])
        self.assertIn(games[0]["winner"], ("A", "B", "draw"))
        self.assertGreater(games[0]["turns"], 0)


if __name__ == "__main__":
    unittest.main()
