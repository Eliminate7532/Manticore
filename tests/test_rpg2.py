# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG2 (phase 2 of the RPG plan): the data underneath the RPG mode - the card pool, the deck rules, power tiers, Jumpstart
starting decks, booster pools and enemy decks - and the developer tools that build it from Forge's own data.

Most tests use small made-up pools and lists, so they say exactly what each rule does. The "Shipped" tests read the files this
round ships (rpg/data/...), and the "Runtime" tests read forge_runtime/ (Vintage's lists, the Jumpstart half-decks) - they skip
without it. The two live tests start Java (tools/rpg_build_data.py's tool) and skip without it or with MANTICORE_SKIP_LIVE.
"""
import json
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

from rpg import cardpool, enemies, products, rules, tiers

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("FORGE_RUNTIME") or os.path.join(BASE, "forge_runtime")
HAS_RUNTIME = os.path.isfile(os.path.join(RUNTIME, rules.VINTAGE_FILE))


# ---- a small made-up world ---------------------------------------------------------------------------------------------------

def tiny_pool():
    rows = [
        ["Island", "Basic Land - Island", 0, "C", "L", 0],
        ["Swamp", "Basic Land - Swamp", 0, "C", "L", 0],
        ["Mountain", "Basic Land - Mountain", 0, "C", "L", 0],
        ["Lightning Bolt", "Instant", 1, "R", "C", 0],
        ["Counterspell", "Instant", 2, "U", "C", 0],
        ["Shivan Dragon", "Creature - Dragon", 6, "R", "R", 0],
        ["Grizzly Bears", "Creature - Bear", 2, "G", "C", 0],
        ["Black Lotus", "Artifact", 0, "C", "R", 0],
        ["Chaos Orb", "Artifact", 2, "C", "R", 0],
        ["Relentless Rats", "Creature - Rat", 3, "B", "C", -1],
        ["Seven Dwarves", "Creature - Dwarf", 3, "R", "C", 7],
        ["Fire // Ice", "Instant // Instant", 2, "UR", "U", 0],
        ["Nazgûl", "Creature - Wraith Knight", 3, "B", "U", 9],
        ["Archaeomender", "Creature - Human Artificer", 3, "U", "C", 0],
    ]
    return cardpool.CardPool(rows, "Forge test")


def tiny_lists():
    return rules.Lists(banned=["Chaos Orb"], restricted=["Black Lotus"], source="made up")


def deck(*pairs):
    return [(n, k) for n, k in pairs]


# ---- the card pool -----------------------------------------------------------------------------------------------------------

class CardPoolTests(unittest.TestCase):
    def test_a_name_is_found_whatever_its_case_spacing_slash_or_accents(self):
        self.assertEqual(cardpool.normal_name("Fire/Ice"), "fire // ice")
        self.assertEqual(cardpool.normal_name("  fire  //  ICE "), "fire // ice")
        self.assertEqual(cardpool.normal_name("Nazgûl"), cardpool.normal_name("Nazgul"))
        pool = tiny_pool()
        for name in ("Fire/Ice", "fire // ice", "FIRE // ICE"):
            self.assertEqual(pool.get(name).name, "Fire // Ice")
        self.assertEqual(pool.get("Nazgul").name, "Nazgûl")
        self.assertIsNone(pool.get("Not A Card"))
        self.assertIn("lightning bolt", pool)

    def test_ligatures_curly_apostrophes_and_flip_cards(self):
        # Forge's archetype data writes some names differently from its card database (12 Oct: 19 of 23 such names)
        pool = cardpool.CardPool([["Aether Vial", "Artifact", 1, "C", "U", 0], ["Sphinx's Decree", "Sorcery", 2, "W", "R", 0],
                                  ["Bushi Tenderfoot", "Creature - Human Monk Soldier", 1, "W", "U", 0],
                                  ["Fire // Ice", "Instant // Instant", 2, "UR", "U", 0]])
        self.assertEqual(pool.get("Æther Vial").name, "Aether Vial")
        self.assertEqual(pool.get("Sphinx\u2019s Decree").name, "Sphinx's Decree")
        self.assertEqual(pool.get("Bushi Tenderfoot // Kenzo the Hardhearted").name, "Bushi Tenderfoot")
        self.assertEqual(pool.get("Fire // Ice").name, "Fire // Ice")            # a split card keeps its whole name
        self.assertIsNone(pool.get("Fire"))                                      # and its face alone is not a card

    def test_basic_lands_and_lands(self):
        pool = tiny_pool()
        self.assertTrue(pool.get("Island").is_basic)
        self.assertTrue(pool.get("Island").is_land)
        self.assertFalse(pool.get("Black Lotus").is_basic)
        self.assertFalse(pool.get("Shivan Dragon").is_land)

    def test_write_then_load_keeps_every_row(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cat = {"cards": [["Lightning Bolt", "Instant", 1, "R", "C", 0, [["LEA", "C"]]],
                         ["Relentless Rats", "Creature - Rat", 3, "B", "C", -1, [["M10", "U"]]]]}
        path = os.path.join(tmp, "cards.json")
        self.assertEqual(cardpool.write(path, cat, "Forge X"), 2)
        pool = cardpool.load(path)
        self.assertEqual(pool.forge, "Forge X")
        self.assertEqual(pool.get("Relentless Rats").copy_limit, -1)
        self.assertEqual(len(pool), 2)


class ShippedPoolTests(unittest.TestCase):
    def test_the_shipped_pool_is_forges_whole_card_list(self):
        pool = cardpool.load()
        self.assertGreater(len(pool), 30000)
        self.assertIn("fb4d809", pool.forge)
        self.assertTrue(pool.get("Island").is_basic)
        self.assertEqual(pool.get("Relentless Rats").copy_limit, -1)
        self.assertEqual(pool.get("Seven Dwarves").copy_limit, 7)
        self.assertEqual(pool.get("Nazgul").copy_limit, 9)
        self.assertEqual(pool.get("Fire/Ice").name, "Fire // Ice")

    @unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/")
    def test_it_names_the_forge_build_in_forge_runtime(self):
        with open(os.path.join(RUNTIME, "VERSION.txt"), encoding="utf-8") as f:
            self.assertEqual(cardpool.load().forge, f.readline().strip())


# ---- the deck rules ----------------------------------------------------------------------------------------------------------

class RulesTests(unittest.TestCase):
    def check(self, main, side=()):
        return rules.check(main, side, tiny_pool(), tiny_lists())

    def kinds(self, main, side=()):
        return [p.kind for p in self.check(main, side)]

    def test_a_legal_deck_has_no_problems(self):
        main = deck(("Lightning Bolt", 4), ("Counterspell", 4), ("Black Lotus", 1), ("Island", 26), ("Mountain", 25))
        self.assertEqual(self.check(main, deck(("Shivan Dragon", 4))), [])
        self.assertTrue(rules.is_legal(main, (), tiny_pool(), tiny_lists()))

    def test_fewer_than_60(self):
        self.assertEqual(self.kinds(deck(("Lightning Bolt", 4), ("Mountain", 55))), ["size"])

    def test_a_sideboard_over_15(self):
        main = deck(("Mountain", 60))
        self.assertEqual(self.kinds(main, deck(("Shivan Dragon", 4), ("Grizzly Bears", 4), ("Counterspell", 4),
                                               ("Lightning Bolt", 4))), ["sideboard"])

    def test_four_copies_counted_over_deck_and_sideboard(self):
        p = self.check(deck(("Lightning Bolt", 3), ("Mountain", 57)), deck(("Lightning Bolt", 2)))
        self.assertEqual([x.kind for x in p], ["copies"])
        self.assertIn("Lightning Bolt x5", p[0].text)

    def test_basics_and_any_number_cards_have_no_limit_and_up_to_n_is_its_own_limit(self):
        self.assertEqual(self.check(deck(("Relentless Rats", 30), ("Swamp", 30))), [])
        self.assertEqual(self.check(deck(("Seven Dwarves", 7), ("Mountain", 53))), [])
        p = self.check(deck(("Seven Dwarves", 8), ("Mountain", 52)))
        self.assertEqual([x.kind for x in p], ["copies"])
        self.assertIn("Seven Dwarves x8 (up to 7)", p[0].text)

    def test_restricted_is_one_copy_banned_is_none(self):
        self.assertEqual(self.kinds(deck(("Black Lotus", 2), ("Island", 58))), ["restricted"])
        self.assertEqual(self.kinds(deck(("Black Lotus", 1), ("Island", 59))), [])
        self.assertEqual(self.kinds(deck(("Chaos Orb", 1), ("Island", 59))), ["banned"])
        self.assertEqual(self.kinds(deck(("Island", 60)), deck(("Chaos Orb", 1))), ["banned"])

    def test_a_card_forge_cannot_play(self):
        p = self.check(deck(("Island", 59), ("Totally Made Up", 1)))
        self.assertEqual([x.kind for x in p], ["unknown"])
        self.assertEqual(p[0].cards, ["Totally Made Up"])

    def test_names_counts_and_dicts_all_count_the_same(self):
        self.assertEqual(rules.counts(["Island", "Island", "Swamp"]), {"Island": 2, "Swamp": 1})
        self.assertEqual(rules.counts([("Island", 2), ("Island", 1)]), {"Island": 3})
        self.assertEqual(rules.counts({"Island": 2, "Swamp": 0}), {"Island": 2})

    def test_a_split_name_and_its_slash_form_are_one_card(self):
        p = self.check(deck(("Fire // Ice", 3), ("Fire/Ice", 2), ("Island", 55)))
        self.assertEqual([x.kind for x in p], ["copies"])

    def test_the_format_file_reader(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "Vintage.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("[format]\nName:Vintage\nBanned:Chaos Orb; Shahrazad\nRestricted:Black Lotus;Time Walk; \n")
        self.assertEqual(rules.read_format_file(path), (["Chaos Orb", "Shahrazad"], ["Black Lotus", "Time Walk"]))


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/")
class RuntimeListsTests(unittest.TestCase):
    def test_vintages_lists_from_forge(self):
        lists = rules.vintage_lists(RUNTIME)
        self.assertGreater(len(lists.restricted), 40)
        self.assertGreater(len(lists.banned), 50)
        self.assertTrue(lists.is_restricted("Black Lotus"))
        self.assertTrue(lists.is_restricted("Brainstorm"))
        self.assertTrue(lists.is_banned("Chaos Orb"))
        self.assertFalse(lists.is_banned("Lightning Bolt") or lists.is_restricted("Lightning Bolt"))


# ---- tiers -------------------------------------------------------------------------------------------------------------------

def tiny_archetypes():
    # Vintage: two archetypes, 3 and 1 decks; Pauper: one archetype.
    return {
        "Vintage": [{"name": "A", "decks": 3, "cards": [["Black Lotus", 1 / 60], ["Counterspell", 4 / 60], ["Island", 0.3]]},
                    {"name": "B", "decks": 1, "cards": [["Lightning Bolt", 4 / 60], ["Not In Pool", 0.1]]}],
        "Pauper": [{"name": "C", "decks": 5, "cards": [["Lightning Bolt", 4 / 60], ["Grizzly Bears", 0.00001]]}],
    }


class TierBuildTests(unittest.TestCase):
    def built(self):
        return tiers.build(tiny_archetypes(), tiny_pool(), weights={"Vintage": 6, "Pauper": 1})

    def test_expected_copies_per_deck_is_share_times_probability_times_60(self):
        copies, missing = tiers.expected_copies(tiny_archetypes(), tiny_pool())
        self.assertAlmostEqual(copies["Counterspell"]["Vintage"], 0.75 * 4)       # 3 of 4 decks play 4
        self.assertAlmostEqual(copies["Lightning Bolt"]["Vintage"], 0.25 * 4)
        self.assertAlmostEqual(copies["Lightning Bolt"]["Pauper"], 4)
        self.assertNotIn("Island", copies)                                         # basics are left out
        self.assertEqual(missing, {"Not In Pool"})

    def test_score_is_the_weighted_sum_and_the_cuts_are_absolute(self):
        cards = self.built()["cards"]
        self.assertAlmostEqual(cards["Counterspell"][1], 6 * 3.0)                 # 18 -> Power
        self.assertEqual(cards["Counterspell"][0], 5)
        self.assertAlmostEqual(cards["Lightning Bolt"][1], 6 * 1.0 + 1 * 4.0)      # 10 -> Power
        self.assertAlmostEqual(cards["Black Lotus"][1], 6 * 0.75)                  # 4.5 -> Power by score too
        self.assertNotIn("Grizzly Bears", cards)                                   # 0.0003: under the floor, not kept

    def test_the_method_says_what_was_left_out(self):
        m = self.built()["method"]
        self.assertEqual(m["missing_from_pool"], ["Not In Pool"])
        self.assertEqual(m["below_floor"], 1)
        self.assertEqual(m["formats"]["Vintage"], {"archetypes": 2, "decks": 4})

    def test_the_same_inputs_give_the_same_file(self):
        a = json.dumps(self.built(), sort_keys=True)
        b = json.dumps(self.built(), sort_keys=True)
        self.assertEqual(a, b)


class TierInfoTests(unittest.TestCase):
    def tiers(self, overrides=None):
        data = {"cards": {"Counterspell": [5, 18.0, {"Vintage": 3.0}], "Shivan Dragon": [3, 0.2, {"Legacy": 0.04}],
                          "Chaos Orb": [4, 1.0, {}]}}
        return tiers.Tiers(data, overrides or {}, tiny_pool(), tiny_lists())

    def test_where_each_tier_comes_from(self):
        t = self.tiers()
        self.assertEqual(t.info("Island")["tier"], 0)
        self.assertEqual(t.info("Black Lotus"), {"tier": 5, "source": "restricted", "score": 0.0, "formats": {}})
        self.assertEqual(t.info("Counterspell")["source"], "data")
        self.assertEqual(t.tier_of("Shivan Dragon"), 3)
        self.assertEqual(t.info("Grizzly Bears")["source"], "rarity")
        self.assertEqual(t.tier_of("Grizzly Bears"), 1)                            # common, not played
        self.assertEqual(t.tier_of("Relentless Rats"), 1)
        self.assertIsNone(t.tier_of("Chaos Orb"))                                  # banned beats the data
        self.assertIsNone(t.tier_of("Not A Card"))

    def test_an_override_beats_the_data_and_the_restricted_list_but_not_banned(self):
        t = self.tiers({"Counterspell": 3, "Black Lotus": 4, "Chaos Orb": 2, "Island": 1})
        self.assertEqual(t.info("Counterspell")["source"], "override")
        self.assertEqual(t.tier_of("Counterspell"), 3)
        self.assertEqual(t.tier_of("Black Lotus"), 4)
        self.assertIsNone(t.tier_of("Chaos Orb"))
        self.assertEqual(t.tier_of("Island"), 1)

    def test_the_override_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "o.json")
        self.assertEqual(tiers.read_overrides(path), {})                          # no file: no overrides
        for bad in (6, -1, "3", 2.5):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"cards": {"Opt": bad}}, f)
            with self.subTest(tier=bad), self.assertRaises(ValueError):
                tiers.read_overrides(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cards": {"Opt": 3}}, f)
        self.assertEqual(tiers.read_overrides(path), {"Opt": 3})

    def test_top_is_by_score(self):
        self.assertEqual([n for n, _i in self.tiers().top(2)], ["Counterspell", "Chaos Orb"])


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (the restricted list)")
class ShippedTierTests(unittest.TestCase):
    def test_the_shipped_tiers(self):
        t = tiers.load()
        self.assertIn("fb4d809", t.forge)
        self.assertEqual(t.tier_of("Island"), 0)
        self.assertEqual(t.tier_of("Black Lotus"), 5)
        self.assertEqual(t.tier_of("Force of Will"), 5)
        self.assertIsNone(t.tier_of("Chaos Orb"))
        self.assertLessEqual(t.tier_of("Doom Blade"), 3)        # the rank-cut mistake made it Elite
        self.assertLessEqual(t.tier_of("Shivan Dragon"), 2)
        self.assertEqual(t.info("Juzam Djinn")["source"], "data")             # found since accents are folded
        self.assertLessEqual(len(t.method["missing_from_pool"]), 4, t.method["missing_from_pool"])
        counts = t.counts()
        self.assertEqual(sum(counts.values()), len(cardpool.load()))
        for tier in range(6):
            self.assertGreater(counts.get(tier, 0), 0, tier)

    def test_the_override_file_ships_empty_and_valid(self):
        self.assertEqual(tiers.read_overrides(), {})


# ---- Jumpstart and boosters --------------------------------------------------------------------------------------------------

def half(code, *cards):
    return products.HalfDeck(code, list(cards))


class JumpstartTests(unittest.TestCase):
    def test_a_half_decks_code(self):
        h = half("J22 Constellation 1", ("Island", 7))
        self.assertEqual((h.set, h.theme, h.number), ("J22", "Constellation", 1))
        h = half("JMP Above the Clouds", ("Island", 7))
        self.assertEqual((h.set, h.theme, h.number), ("JMP", "Above the Clouds", 0))

    def test_sheet_names_are_stripped(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "printsheets.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("[J25 Giddyap ]\n1 Horse|J25\n# a comment\n7 Plains|J25|2\n")
        self.assertEqual(products.read_sheets(path), {"J25 Giddyap": ["1 Horse|J25", "7 Plains|J25|2"]})

    def test_a_starting_deck_adds_up_caps_copies_and_fills_with_its_main_basic(self):
        pool, lists = tiny_pool(), tiny_lists()
        a = half("JMP A 1", ("Archaeomender", 2), ("Lightning Bolt", 3), ("Island", 8))
        b = half("JMP B 1", ("Archaeomender", 2), ("Lightning Bolt", 2), ("Mountain", 6))
        c = half("JMP C 1", ("Archaeomender", 2), ("Black Lotus", 1), ("Island", 7))
        d = dict(products.starter_deck([a, b, c], pool, lists))
        self.assertEqual(d["Archaeomender"], 4)               # 6 across three halves, capped at four
        self.assertEqual(d["Lightning Bolt"], 4)              # 5 capped
        self.assertEqual(d["Black Lotus"], 1)
        self.assertEqual(sum(d.values()), 60)
        self.assertEqual(d["Mountain"], 6)                    # the fill goes to Island, the most common basic
        self.assertEqual(d["Island"], 60 - 4 - 4 - 1 - 6)
        self.assertEqual(rules.check(list(d.items()), (), pool, lists), [])

    def test_a_restricted_card_in_two_halves_is_one(self):
        pool, lists = tiny_pool(), tiny_lists()
        a = half("JMP A 1", ("Black Lotus", 1), ("Island", 30))
        b = half("JMP B 1", ("Black Lotus", 1), ("Island", 30))
        self.assertEqual(dict(products.starter_deck([a, b], pool, lists))["Black Lotus"], 1)

    def test_offers_are_different_themes_and_leave_out_what_was_taken(self):
        decks = [half(f"JMP T{i % 5} {i}", ("Island", 20)) for i in range(40)]
        taken = [decks[0].code, decks[1].code]
        for seed in range(30):
            got = products.offer(random.Random(seed), decks, 3, exclude=taken)
            self.assertEqual(len(got), 3)
            self.assertEqual(len({h.theme for h in got}), 3)
            self.assertFalse(set(taken) & {h.code for h in got})
        self.assertEqual([h.code for h in products.offer(random.Random(7), decks)],
                         [h.code for h in products.offer(random.Random(7), decks)])


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/")
class RuntimeJumpstartTests(unittest.TestCase):
    def test_every_half_deck_is_read_and_playable(self):
        decks, problems = products.read_jumpstart(RUNTIME)
        self.assertEqual(problems, [])
        self.assertEqual(len(decks), 363)
        by_set = {}
        for h in decks:
            by_set[h.set] = by_set.get(h.set, 0) + 1
        self.assertEqual(by_set, {"JMP": 121, "J22": 121, "J25": 121})
        pool, lists = cardpool.load(), rules.vintage_lists(RUNTIME)
        for h in decks:
            with self.subTest(deck=h.code):
                self.assertGreaterEqual(h.size, 19)
                self.assertEqual([n for n, _k in h.cards if pool.get(n) is None], [])
                self.assertEqual([n for n, _k in h.cards if lists.is_banned(n) or lists.is_restricted(n)], [])

    def test_any_three_make_a_legal_60(self):
        decks = products.jumpstart(RUNTIME)
        pool, lists = cardpool.load(), rules.vintage_lists(RUNTIME)
        rng = random.Random(20261012)
        for _ in range(200):
            three = rng.sample(decks, 3)
            d = products.starter_deck(three, pool, lists)
            with self.subTest(decks=[h.code for h in three]):
                self.assertGreaterEqual(sum(k for _n, k in d), 60)
                self.assertEqual(rules.check(d, (), pool, lists), [])


class ShippedPackTests(unittest.TestCase):
    def test_every_shipped_pool(self):
        pool = cardpool.load()
        names = sorted(n[:-5] for n in os.listdir(products.PACKS_DIR) if n.endswith(".json"))
        self.assertEqual(names, sorted(("M10", "M11", "M12", "M13", "M14", "M15", "ORI", "M19", "M20", "M21")))
        for code in names:
            data = products.read_pool(code)
            with self.subTest(set=code):
                self.assertEqual(data["set"], code)
                self.assertIn("fb4d809", data["forge"])
                self.assertEqual(len(data["packs"]), 200)
                for pack in data["packs"]:
                    self.assertEqual(len(pack), 15)
                    self.assertEqual([c[0] for c in pack if pool.get(c[0]) is None], [])
                    # one rare or mythic slot; a foil (which takes a common's place) may be a second one
                    self.assertEqual(len([c for c in pack if c[3] in ("R", "M") and not (len(c) > 4 and c[4])]), 1, pack)

    def test_opening_a_pack_is_repeatable_from_a_seed(self):
        a = products.open_pack("M10", random.Random(5))
        b = products.open_pack("M10", random.Random(5))
        self.assertEqual(a, b)
        self.assertTrue(all(len(c) == 3 for c in a))


# ---- enemies -----------------------------------------------------------------------------------------------------------------

def built_deck(archetype="Burn", deck_name=None, main=None, side=(), index=0, error=None):
    d = {"archetype": archetype, "index": index, "deckName": deck_name or archetype + enemies.FALLBACK_SUFFIX,
         "main": [list(x) for x in (main or [("Lightning Bolt", 4), ("Mountain", 56)])], "sideboard": [list(x) for x in side]}
    if error:
        d["error"] = error
    return d


KIND = {"id": "test", "name": "Test Enemy", "format": "Pauper", "archetypes": ["Burn"], "decks": 2, "max_tier": 4}


class EnemyTests(unittest.TestCase):
    def judge(self, d, kind=KIND, flags=lambda n: []):
        t = tiers.Tiers({"cards": {"Lightning Bolt": [3, 0.3, {}], "Counterspell": [5, 9.0, {}]}}, {}, tiny_pool(), tiny_lists())
        return enemies.judge(d, kind, tiny_pool(), tiny_lists(), t, flags)

    def test_a_good_deck_passes(self):
        self.assertEqual(self.judge(built_deck()), [])

    def test_forge_building_another_archetype_is_caught(self):
        why = self.judge(built_deck(deck_name="Goblins Generated Deck"))
        self.assertEqual(len(why), 1)
        self.assertIn("another archetype", why[0])

    def test_the_deck_rules_the_ai_flags_and_the_tier_cap(self):
        self.assertIn("at least 60", self.judge(built_deck(main=[("Lightning Bolt", 4), ("Mountain", 50)]))[0])
        why = self.judge(built_deck(), flags=lambda n: ["All"] if n == "Lightning Bolt" else [])
        self.assertEqual(why, ["Forge's AI is told to leave these out: Lightning Bolt (All)"])
        why = self.judge(built_deck(main=[("Counterspell", 4), ("Island", 56)]))
        self.assertEqual(why, ["above tier 4: Counterspell (tier 5)"])
        self.assertEqual(self.judge(built_deck(main=[("Counterspell", 4), ("Island", 56)]), dict(KIND, max_tier=5)), [])
        self.assertIn("could not build", self.judge(built_deck(error="boom"))[0])

    def test_select_keeps_the_first_good_ones_and_never_the_same_list_twice(self):
        cands = [built_deck(index=0), built_deck(index=1), built_deck(index=2, deck_name="X Generated Deck"),
                 built_deck(index=3, main=[("Lightning Bolt", 3), ("Mountain", 57)]),
                 built_deck(index=4, main=[("Lightning Bolt", 2), ("Mountain", 58)])]
        t = tiers.Tiers({"cards": {"Lightning Bolt": [3, 0.3, {}]}}, {}, tiny_pool(), tiny_lists())
        accepted, rejected = enemies.select(cands, KIND, pool=tiny_pool(), lists=tiny_lists(), tier_table=t, flags=lambda n: [])
        self.assertEqual([d["index"] for d in accepted], [0, 3])
        self.assertEqual([d["name"] for d in accepted], ["Test Enemy 1", "Test Enemy 2"])
        self.assertEqual({r["index"]: r["reasons"][0][:20] for r in rejected},
                         {1: "the same list as a d", 2: "Forge built another "})

    def test_deck_text_reads_back_as_the_same_deck(self):
        import deck_importer as di
        d = {"name": "Test Enemy 1", "archetype": "Burn", "main": [["Lightning Bolt", 4], ["Mountain", 56]],
             "sideboard": [["Shivan Dragon", 2]]}
        side = []
        cmd, main = di.import_from_text(enemies.deck_text(d), sideboard=side, fmt="standard")
        self.assertEqual((cmd, len(main), main.count("Lightning Bolt"), side), ([], 60, 4, ["Shivan Dragon"] * 2))


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/")
class ShippedEnemyTests(unittest.TestCase):
    def test_every_kind_ships_its_decks_and_every_deck_still_passes(self):
        kinds = enemies.read_kinds()
        self.assertEqual(len(kinds), 10)                                   # round RPG3: five more
        pool, lists, t = cardpool.load(), rules.vintage_lists(RUNTIME), tiers.load()
        for k in kinds:
            data = enemies.read_kind(k["id"])
            self.assertEqual(len(data["decks"]), k["decks"], k["id"])
            self.assertIn("fb4d809", data["forge"])
            for d in data["decks"]:
                with self.subTest(deck=d["name"]):
                    entry = dict(d, deckName=d["archetype"] + enemies.FALLBACK_SUFFIX)
                    self.assertEqual(enemies.judge(entry, k, pool, lists, t,
                                                   lambda n: enemies.ai_flags(n, RUNTIME)), [])

    def test_no_shipped_deck_failed_the_soak(self):
        for k in enemies.read_kinds():
            data = enemies.read_kind(k["id"])
            with self.subTest(kind=k["id"]):
                self.assertIsNotNone(data["soak"], "tools/rpg_deck_test.py has not been run on this kind")
                self.assertEqual(data["soak"]["soak_failed"], [])
                self.assertEqual(sorted(data["soak"]["decks"]), sorted(d["name"] for d in data["decks"]))
                for name, e in data["soak"]["decks"].items():
                    self.assertGreater(e["games"], 0, name)


class DeckTestToolTests(unittest.TestCase):
    def test_deck_files_and_the_soak_entry(self):
        from tools import rpg_deck_test
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = os.path.join(tmp, "enemies")
        os.makedirs(folder)
        kind = dict(KIND)
        enemies.write_kind(kind, [{"name": "Test Enemy 1", "archetype": "Burn", "index": 0,
                                   "main": [["Lightning Bolt", 4], ["Mountain", 56]], "sideboard": []}], [], "Forge X", 1,
                           tier_table=tiers.Tiers({"cards": {}}, {}, tiny_pool(), tiny_lists()), folder=folder)
        out = os.path.join(tmp, "decks")
        os.makedirs(out)
        with mock.patch.object(enemies, "ENEMIES_DIR", folder):
            files = rpg_deck_test.deck_files(out)
            self.assertEqual(list(files.values()), [("test", "Test Enemy 1")])
            path = list(files)[0]

            class R:
                def __init__(self, i, ended, rules_, decks):
                    self.index, self.ended, self._r, self.decks = i, ended, rules_, decks

                def fail_rules(self):
                    return self._r

            # tools/soak.py's GameResult.decks holds file names only (os.path.basename), as in a real run
            name, other = os.path.basename(path), "x.txt"
            results = [R(0, "game_over", [], [name, other]), R(1, "game_over", [], [other, name]),
                       R(2, "stall", ["stall"], [name, other])]
            by_kind = rpg_deck_test.record(results, files, "run", "2026-10-12 12:00")
            e = by_kind["test"]["Test Enemy 1"]
            self.assertEqual((e["games"], e["game_over"], e["as_ai"]), (3, 2, 1))
            self.assertEqual(e["failed"], [{"game": 2, "ended": "stall", "rules": ["stall"]}])
            data = enemies.read_kind("test", folder)
            self.assertEqual(data["soak"]["soak_failed"], ["Test Enemy 1"])


    def test_the_tool_round_trip_through_soaks_own_game_results(self):
        # main() hands soak its decks and records soak's results; soak.run_game names decks the way the real one does
        # (os.path.basename of a file from deck_pool). A first version matched full paths and recorded nothing (12 Oct).
        from tools import rpg_deck_test, soak
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = os.path.join(tmp, "enemies")
        os.makedirs(folder)
        two = [{"name": "Test Enemy %d" % i, "archetype": "Burn", "index": i, "sideboard": [],
                "main": [["Lightning Bolt", 4], ["Mountain", 56 - i]]} for i in (1, 2)]
        enemies.write_kind(dict(KIND), two, [], "Forge X", 1,
                           tier_table=tiers.Tiers({"cards": {}}, {}, tiny_pool(), tiny_lists()), folder=folder)

        def fake_game(index, args, rng, out_root, watchdog_holder=None):
            r = soak.GameResult(index, 1, 2)
            r.decks = [os.path.basename(d) for d in soak.deck_pool(args.decks, args.format)]
            r.ended = "game_over"
            return r

        def fake_run(args):
            for i in range(3):
                soak.run_game(i, args, random.Random(i), args.out)
            return 0

        with mock.patch.object(enemies, "ENEMIES_DIR", folder), mock.patch.object(soak, "run_game", fake_game), \
                mock.patch.object(soak, "run", fake_run), mock.patch("builtins.print"):
            self.assertEqual(rpg_deck_test.main(["--games", "3", "--out", os.path.join(tmp, "out")]), 0)
        data = enemies.read_kind("test", folder)
        self.assertEqual({n: (e["games"], e["as_ai"]) for n, e in data["soak"]["decks"].items()},
                         {"Test Enemy 1": (3, 0), "Test Enemy 2": (3, 3)})
        self.assertEqual(data["soak"]["soak_failed"], [])

    @unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (the Jumpstart lists)")
    def test_starting_decks_for_the_soak_are_legal_and_never_recorded(self):
        from tools import rpg_deck_test
        import deck_importer as di
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        files = rpg_deck_test.starter_files(tmp, 4, 9)
        self.assertEqual(files, rpg_deck_test.starter_files(tmp, 4, 9))           # the same seed, the same decks
        pool, lists = cardpool.load(), rules.vintage_lists(RUNTIME)
        for path, (kind, name) in files.items():
            self.assertEqual(kind, "starter")
            with open(path, encoding="utf-8") as f:
                _c, main = di.import_from_text(f.read(), fmt="standard")
            self.assertGreaterEqual(len(main), 60, name)
            self.assertEqual(rules.check(main, (), pool, lists), [], name)
        import types
        paths = sorted(files)
        game = types.SimpleNamespace(index=0, ended="game_over", decks=[os.path.basename(p) for p in paths[:2]],
                                     fail_rules=lambda: [])
        empty = os.path.join(tmp, "enemies")
        os.makedirs(empty)
        with mock.patch.object(enemies, "ENEMIES_DIR", empty):
            by_kind = rpg_deck_test.record([game], files, "run", "now")      # a starter.json would be a FileNotFoundError
        self.assertEqual(sorted(by_kind), ["starter"])
        self.assertEqual(os.listdir(empty), [])


# ---- the build tool ----------------------------------------------------------------------------------------------------------

class BuildToolTests(unittest.TestCase):
    def test_the_shipped_jar_was_built_from_these_sources(self):
        from tools import rpg_build_data as b
        self.assertTrue(os.path.isfile(b.JAR))
        self.assertEqual(b.jar_stamp(), b.source_hash())

    def test_the_stamp_follows_the_sources(self):
        from tools import rpg_build_data as b
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "src")
        shutil.copytree(b.SRC, src)
        h = b.source_hash(src)
        java = [os.path.join(dp, f) for dp, _d, fs in os.walk(src) for f in fs if f.endswith(".java")][0]
        with open(java, "rb") as f:
            text = f.read()
        with open(java, "wb") as f:
            f.write(text.replace(b"\n", b"\r\n"))                  # line endings don't count
        self.assertEqual(b.source_hash(src), h)
        with open(java, "ab") as f:
            f.write(b"// changed\n")
        self.assertNotEqual(b.source_hash(src), h)

    def test_the_tool_refuses_a_jar_that_does_not_match(self):
        from tools import rpg_build_data as b
        with mock.patch.object(b, "jar_stamp", lambda jar=None: "0" * 64), self.assertRaises(SystemExit) as cm:
            b.main(["tiers"])
        self.assertIn("rpg_build_data.py jar", str(cm.exception))

    def test_the_tool_is_not_part_of_the_bridge(self):
        # java_rpg/ is outside java_bridge/src, so the bridge's source stamp (and every saved game's replay key) is unchanged
        import zipfile
        import setup_forge
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            stamp = z.read(setup_forge.BRIDGE_STAMP).decode("ascii").strip()
        self.assertEqual(stamp, setup_forge.bridge_source_hash())
        with zipfile.ZipFile(os.path.join(BASE, "java_rpg", "rpg_tools.jar")) as z:
            self.assertFalse([n for n in z.namelist() if n.startswith("forge/bridge/")])


def live_ok():
    try:
        import tests.live as live
        return live.live_enabled(RUNTIME)
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(live_ok(), "needs Java and forge_runtime/ (and no MANTICORE_SKIP_LIVE)")
class LiveToolTests(unittest.TestCase):
    def test_forges_archetype_data_reads(self):
        from tools import rpg_build_data as b
        out = b.java_json("archetypes", "Pauper", rt=RUNTIME)["formats"]["Pauper"]
        self.assertGreater(len(out), 20)
        self.assertTrue(all(a["decks"] > 0 and a["cards"] for a in out))
        names = {a["name"] for a in out}
        self.assertTrue({"Stompy Green Mono", "Kuldotha Red Mono"} <= names, sorted(names)[:20])

    def test_forges_booster_generator_is_repeatable_from_a_seed(self):
        from tools import rpg_build_data as b
        a = b.java_json("boosters", 77, 3, "M10", rt=RUNTIME)["sets"]["M10"]["packs"]
        c = b.java_json("boosters", 77, 3, "M10", rt=RUNTIME)["sets"]["M10"]["packs"]
        self.assertEqual(len(a), 3)
        self.assertEqual(a, c)


if __name__ == "__main__":
    unittest.main()
