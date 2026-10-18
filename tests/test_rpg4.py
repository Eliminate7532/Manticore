# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG4 (phase 3 of the RPG plan, the game logic with no screen): the numbers and the towns, the collection, the save file,
the new-game flow, the shops and packs, duels and their rewards.

Most tests use a small made-up world (cards, tiers, lists, packs and enemy decks written to a temp folder), so they say exactly what
each rule does. The "Shipped" tests read this round's data files and the card pool; the "Runtime" ones also read forge_runtime/
(Vintage's lists, the Jumpstart half-decks) and skip without it.
"""
import copy
import json
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

import paths
from rpg import cardpool, collection, duel, economy, enemies, newgame, products, rules, shop
from rpg import save as sv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("FORGE_RUNTIME") or os.path.join(BASE, "forge_runtime")
HAS_RUNTIME = os.path.isfile(os.path.join(RUNTIME, rules.VINTAGE_FILE))


# ---- a small made-up world ---------------------------------------------------------------------------------------------------

ROWS = [
    ["Plains", "Basic Land - Plains", 0, "C", "L", 0],
    ["Forest", "Basic Land - Forest", 0, "C", "L", 0],
    ["Island", "Basic Land - Island", 0, "C", "L", 0],
    ["Savannah Lions", "Creature - Cat", 1, "W", "R", 0],
    ["Grizzly Bears", "Creature - Bear", 2, "G", "C", 0],
    ["Llanowar Elves", "Creature - Elf Druid", 1, "G", "C", 0],
    ["Pacifism", "Enchantment - Aura", 2, "W", "C", 0],
    ["Selesnya Guildmage", "Creature - Elf Wizard", 2, "GW", "U", 0],
    ["Ornithopter", "Artifact Creature - Thopter", 0, "C", "U", 0],
    ["Counterspell", "Instant", 2, "U", "C", 0],
    ["Lightning Bolt", "Instant", 1, "R", "C", 0],
    ["Swords to Plowshares", "Instant", 1, "W", "U", 0],
    ["Black Lotus", "Artifact", 0, "C", "R", 0],
    ["Chaos Orb", "Artifact", 2, "C", "R", 0],
    ["A-Grizzly Bears", "Creature - Bear", 2, "G", "C", 0],
    ["Lim-Dûl's Vault", "Instant", 2, "UB", "U", 0],
    ["Wild Growth", "Enchantment - Aura", 1, "G", "C", 0],
    ["Tree Folk", "Creature - Treefolk", 3, "G", "C", 0],
]
TIER = {"Plains": 0, "Forest": 0, "Island": 0, "Savannah Lions": 2, "Grizzly Bears": 1, "Llanowar Elves": 4, "Pacifism": 1,
        "Selesnya Guildmage": 3, "Ornithopter": 2, "Counterspell": 5, "Lightning Bolt": 5, "Swords to Plowshares": 5,
        "Black Lotus": 5, "Chaos Orb": None, "A-Grizzly Bears": 1, "Lim-Dûl's Vault": 2, "Wild Growth": 1, "Tree Folk": 1}


def pool():
    return cardpool.CardPool(ROWS, "Forge test")


def lists():
    return rules.Lists(banned=["Chaos Orb"], restricted=["Black Lotus"], source="made up")


class FakeTiers:
    def tier_of(self, name):
        card = POOL.get(name)
        return TIER.get(card.name) if card is not None else None


POOL = pool()

NUMBERS = {
    "about": "test", "save_slots": 3, "start_gold": 100, "loss_gold_share": 0.25, "basic_lands_free": True,
    "single_price": {"1": 10, "2": 25, "3": 60, "4": 150, "5": 400}, "sell_share": 0.25, "pack_price": 80,
    "restock_after_duels": 2, "stock": {"1": 3, "2": 2, "3": 1}, "stock_copies": [1, 3], "shop_skip_prefixes": ["A-"],
    "rewards": {"regular": {"gold": [20, 40], "cards": 2}, "dungeon": {"gold": [50, 80], "cards": 2},
                "boss": {"gold": [200, 200], "cards": 3}},
}
TOWNS = {
    "start_town": "green_town",
    "regions": {"1": {"name": "R1", "tiers": [1, 3]}},
    "towns": [{"id": "green_town", "name": "Green", "region": 1, "colours": "GW", "sets": ["TST", "NOPACKS"]},
              {"id": "blue_town", "name": "Blue", "region": 1, "colours": "U", "sets": []}],
}


def numbers(**over):
    d = copy.deepcopy(NUMBERS)
    d.update(over)
    return economy.Numbers(d)


def world(**over):
    d = copy.deepcopy(TOWNS)
    d.update(over)
    return economy.World(d)


def half(code, cards):
    return products.HalfDeck(code, cards)


HALVES = [
    half("TST Cats 1", [("Savannah Lions", 4), ("Pacifism", 4), ("Plains", 12)]),
    half("TST Elves 1", [("Llanowar Elves", 4), ("Grizzly Bears", 4), ("Forest", 12)]),
    half("TST Elves 2", [("Llanowar Elves", 2), ("Tree Folk", 4), ("Wild Growth", 2), ("Forest", 12)]),
    half("TST Guilds 1", [("Selesnya Guildmage", 4), ("Ornithopter", 4), ("Forest", 6), ("Plains", 6)]),
    half("TST Islands 1", [("Counterspell", 2), ("Lim-Dûl's Vault", 2), ("Island", 16)]),
]


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rpg4_")
        self.addCleanup(shutil.rmtree, self.tmp, True)


def new_save(slot=1, seed=7, gold=100):
    s = sv.SaveGame(slot, "Karl", seed, pool=POOL)
    s.created = s.updated = 1000.0
    s.gold = gold
    s.world = {"region": 1, "map": "green_town", "x": 3, "y": 4, "last_town": "green_town", "towns_visited": ["green_town"]}
    return s


def started_save(**kw):
    """A new game made by the flow over the made-up half-decks: Cats 1, Elves 1, Elves 2."""
    flow = newgame.NewGameFlow(seed=kw.pop("seed", 11), decks=HALVES, pool=POOL, lists=lists(), numbers=numbers(),
                               world=world(), offer_size=5)
    flow.set_name("Karl")
    for code in ("TST Cats 1", "TST Elves 1", "TST Elves 2"):
        flow.pick(code)
    return flow.finish(1, now=1000.0)


# ---- numbers and towns -------------------------------------------------------------------------------------------------------

class NumbersTests(unittest.TestCase):
    def test_a_good_file_reads_and_the_arithmetic(self):
        n = numbers()
        self.assertEqual(n.price(3), 60)
        self.assertIsNone(n.price(0))                       # basic lands: never sold
        self.assertIsNone(n.price(None))                    # banned
        self.assertEqual(n.sell_price(3), 15)               # 60 x 0.25
        self.assertEqual(n.sell_price(1), 2)                # 10 x 0.25 = 2.5, down
        self.assertEqual(numbers(single_price={"1": 1, "2": 2, "3": 3, "4": 4, "5": 5}).sell_price(1), 1)   # never 0
        self.assertEqual(n.sell_price(0), 0)
        self.assertEqual(n.loss_cost(101), 25)              # 101 x 0.25 = 25.25, down
        self.assertEqual(n.loss_cost(3), 0)
        self.assertEqual(n.loss_cost(-5), 0)
        golds = {n.reward_gold("regular", random.Random(s)) for s in range(200)}
        self.assertEqual((min(golds), max(golds)), (20, 40))
        self.assertEqual(n.reward_gold("boss", random.Random(1)), 200)
        self.assertEqual(n.reward_cards("boss"), 3)

    def test_every_bad_value_is_named(self):
        bad = [({"start_gold": -1}, "start_gold"), ({"start_gold": True}, "start_gold"), ({"loss_gold_share": 1.5}, "loss_gold_share"),
               ({"single_price": {"1": 10}}, "single_price"), ({"single_price": {"1": 0, "2": 1, "3": 1, "4": 1, "5": 1}}, "single_price"),
               ({"stock": {"7": 1}}, "stock"), ({"stock_copies": [3, 1]}, "stock_copies"), ({"stock_copies": [0, 1]}, "stock_copies"),
               ({"rewards": {"regular": {"gold": [1, 2], "cards": 1}}}, "rewards"), ({"basic_lands_free": "yes"}, "basic_lands_free"),
               ({"save_slots": 0}, "save_slots"), ({"shop_skip_prefixes": "A-"}, "shop_skip_prefixes")]
        for over, key in bad:
            with self.subTest(over=over), self.assertRaises(ValueError) as cm:
                numbers(**over)
            self.assertIn(key, str(cm.exception))
        d = copy.deepcopy(NUMBERS)
        del d["pack_price"]
        with self.assertRaises(ValueError) as cm:
            economy.Numbers(d)
        self.assertIn("pack_price", str(cm.exception))

    def test_the_world_file_is_checked(self):
        w = world()
        self.assertEqual(w.band(1), (1, 3))
        self.assertEqual(w.town("blue_town").colours, "U")
        with self.assertRaises(KeyError):
            w.town("nowhere")
        bad = [{"start_town": "nowhere"}, {"regions": {"1": {"tiers": [0, 3]}}}, {"regions": {"1": {"tiers": [4, 2]}}},
               {"towns": [{"id": "a", "name": "A", "region": 2}], "start_town": "a"},
               {"towns": [{"id": "green_town", "name": "A", "region": 1}, {"id": "green_town", "name": "B", "region": 1}]},
               {"towns": [{"id": "green_town", "name": "A", "region": 1, "colours": "GX"}]}]
        for over in bad:
            with self.subTest(over=over), self.assertRaises(ValueError):
                world(**over)


class ShippedNumbersTests(unittest.TestCase):
    def test_the_shipped_files_read_and_every_town_sells_packs(self):
        n = economy.read_numbers()
        w = economy.read_world()
        self.assertGreater(n.start_gold, 0)
        self.assertIn(w.start_town, w.towns)
        for t in w.towns.values():
            with self.subTest(town=t.id):
                self.assertTrue(t.sets and all(products.has_packs(c) for c in t.sets))
                lo, hi = w.band(t.region)
                self.assertTrue(1 <= lo <= hi <= 5)
        with open(economy.NUMBERS_FILE, encoding="utf-8") as f:
            self.assertIn("placeholder", json.load(f)["about"])          # the numbers are still Karl's to tune
        with open(economy.TOWNS_FILE, encoding="utf-8") as f:
            self.assertIn("PLACEHOLDERS", json.load(f)["about"])


# ---- the collection ----------------------------------------------------------------------------------------------------------

class CollectionTests(unittest.TestCase):
    def test_counts_by_the_pools_name_and_basics_are_free(self):
        c = collection.Collection(pool=POOL)
        c.add("lim-dul's vault", 2)
        c.add("Lim-Dûl's Vault", 1)
        c.add("Forest", 30)
        self.assertEqual(c.count("LIM-DUL'S VAULT"), 3)
        self.assertEqual(c.to_dict(), {"Lim-Dûl's Vault": 3})
        self.assertIsNone(c.count("Forest"))                 # unlimited
        self.assertEqual(c.total(), 3)
        c.remove("Lim-Dul's Vault", 3)
        self.assertEqual(c.count("Lim-Dûl's Vault"), 0)
        self.assertEqual(len(c), 0)
        with self.assertRaises(ValueError):
            c.remove("Grizzly Bears")
        with self.assertRaises(ValueError):
            c.add("Grizzly Bears", -1)
        paid = collection.Collection(pool=POOL, basics_free=False)
        paid.add("Forest", 2)
        self.assertEqual(paid.count("Forest"), 2)

    def test_a_deck_needs_every_copy_owned_main_and_sideboard_together(self):
        c = collection.Collection({"Grizzly Bears": 3, "Pacifism": 1}, pool=POOL)
        d = {"name": "d", "main": [["Grizzly Bears", 2], ["Forest", 57], ["Pacifism", 1]], "sideboard": [["Grizzly Bears", 2]]}
        probs = collection.owned_problems(d, c)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0].kind, "owned")
        self.assertIn("Grizzly Bears (the deck uses 4, you own 3)", probs[0].text)
        self.assertNotIn("Forest", probs[0].text)
        c.add("Grizzly Bears")
        self.assertEqual(collection.problems(d, c, POOL, lists()), [])
        small = {"name": "s", "main": [["Grizzly Bears", 4]], "sideboard": []}
        self.assertEqual([p.kind for p in collection.problems(small, c, POOL, lists())], ["size"])

    def test_selling_is_refused_when_a_deck_needs_the_copies(self):
        c = collection.Collection({"Grizzly Bears": 4}, pool=POOL)
        decks = [{"name": "Bears", "main": [["Grizzly Bears", 3]], "sideboard": []}]
        self.assertIsNone(collection.sell_problem("Grizzly Bears", 1, c, decks))
        self.assertIn("Bears", collection.sell_problem("Grizzly Bears", 2, c, decks))
        self.assertIn("own 4", collection.sell_problem("Grizzly Bears", 5, c, decks))
        self.assertIn("free basic land", collection.sell_problem("Forest", 1, c, decks))
        self.assertIn("can't sell 0", collection.sell_problem("Grizzly Bears", 0, c, decks))


# ---- the save file -----------------------------------------------------------------------------------------------------------

class SaveTests(Temp):
    def test_written_and_read_back_the_same_and_no_tmp_left(self):
        s = started_save()
        s.extra = {"future_key": [1, 2]}
        path = sv.write(s, self.tmp, now=2000.0)
        self.assertEqual(os.path.basename(path), "save_1.json")
        self.assertFalse(os.path.exists(path + ".tmp"))
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual((raw["format"], raw["version"], raw["future_key"]), ("manticore-rpg-save", sv.VERSION, [1, 2]))  # RPG7: 2
        back, note = sv.read(1, self.tmp, pool=POOL)
        self.assertIsNone(note)
        self.assertEqual(back.to_dict(), s.to_dict())

    def test_the_old_file_becomes_the_backup_and_a_damaged_file_falls_back_to_it(self):
        s = new_save(gold=100)
        sv.write(s, self.tmp, now=2000.0)
        s.gold = 250
        path = sv.write(s, self.tmp, now=3000.0)
        self.assertEqual(sv.read(1, self.tmp)[0].gold, 250)
        with open(path + ".bak", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["gold"], 100)
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"format": "manticore-rpg-save", "versi')            # cut off mid-write
        back, note = sv.read(1, self.tmp)
        self.assertEqual(back.gold, 100)
        self.assertIn("damaged", note)
        self.assertIn("backup", note)
        # writing again must not put the damaged file over the good backup
        s.gold = 300
        sv.write(s, self.tmp, now=4000.0)
        with open(path + ".bak", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["gold"], 100)
        self.assertEqual(sv.read(1, self.tmp)[0].gold, 300)

    def test_both_damaged_an_empty_slot_and_a_missing_file_with_a_backup(self):
        with self.assertRaises(FileNotFoundError):
            sv.read(2, self.tmp)
        s = new_save()
        path = sv.write(s, self.tmp)
        sv.write(s, self.tmp)
        os.remove(path)
        back, note = sv.read(1, self.tmp)                     # main gone, backup there
        self.assertEqual(back.name, "Karl")
        self.assertIn("missing", note)
        for p in (path, path + ".bak"):
            with open(p, "w", encoding="utf-8") as f:
                f.write("not json")
        with self.assertRaises(sv.SaveError) as cm:
            sv.read(1, self.tmp)
        self.assertIn("damaged", str(cm.exception))

    def test_a_newer_version_is_refused_in_plain_words(self):
        d = new_save().to_dict()
        d["version"] = sv.VERSION + 1
        os.makedirs(self.tmp, exist_ok=True)
        with open(sv.save_path(1, self.tmp), "w", encoding="utf-8") as f:
            json.dump(d, f)
        with self.assertRaises(sv.SaveError) as cm:
            sv.read(1, self.tmp)
        self.assertIn("newer Manticore", str(cm.exception))

    def test_every_damaged_field_is_refused(self):
        good = started_save().to_dict()
        cases = [("gold", -1), ("gold", "100"), ("collection", {"Grizzly Bears": 0}), ("collection", []), ("active_deck", 5),
                 ("decks", [{"name": "", "main": []}]), ("decks", [{"name": "x", "main": [["Bears", -1]]}]), ("seed", "x"),
                 ("rng_counter", -1), ("name", ""), ("world", {"region": 0, "towns_visited": []}),
                 ("shops", {"t": {"restock": 0, "bought": {"1": 0}}}), ("record", {"duels": 0}), ("pending_duel", []),
                 ("format", "something else"), ("slot", 0)]
        for key, value in cases:
            d = copy.deepcopy(good)
            d[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(sv.SaveError):
                sv.SaveGame.from_dict(d)
        self.assertEqual(sv.SaveGame.from_dict(copy.deepcopy(good), pool=POOL).to_dict(), good)

    def test_names(self):
        self.assertEqual(sv.check_name("  Karl   the   Bold "), "Karl the Bold")
        for bad in ("", "   ", None, "x" * 25, "Karl\x07"):
            with self.subTest(name=bad), self.assertRaises(sv.SaveError):
                sv.check_name(bad)
        self.assertEqual(sv.check_name("x" * 24), "x" * 24)

    def test_the_saves_randomness_is_its_own_and_survives_a_reload(self):
        a, b = new_save(seed=5), new_save(seed=5)
        self.assertEqual([a.rng("p").random() for _ in range(3)], [b.rng("p").random() for _ in range(3)])
        self.assertEqual(a.rng_counter, 3)
        self.assertNotEqual(a.rng("p").random(), new_save(seed=6).rng("p").random())
        x = a.rng("pack").random()
        sv.write(a, self.tmp)
        back = sv.read(1, self.tmp)[0]
        self.assertNotEqual(back.rng("pack").random(), x)       # the counter moved on: nothing is rolled twice
        c = new_save(seed=5)
        self.assertEqual(c.stable_rng("shop", "t", 0).random(), c.stable_rng("shop", "t", 0).random())
        self.assertEqual(c.rng_counter, 0)

    def test_slots_for_a_load_screen(self):
        sv.write(new_save(slot=1, gold=42), self.tmp)
        os.makedirs(self.tmp, exist_ok=True)
        with open(sv.save_path(3, self.tmp), "w", encoding="utf-8") as f:
            f.write("{")
        rows = sv.slots(3, self.tmp)
        self.assertEqual([r["used"] for r in rows], [True, False, True])
        self.assertEqual((rows[0]["name"], rows[0]["gold"], rows[0]["problem"]), ("Karl", 42, None))
        self.assertIn("damaged", rows[2]["problem"])

    def test_where_saves_live(self):
        with mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": self.tmp}):
            self.assertEqual(paths.rpg_saves_dir(), os.path.join(self.tmp, "rpg_saves"))
            self.assertEqual(os.path.dirname(sv.save_path(2)), os.path.join(self.tmp, "rpg_saves"))
        with mock.patch.dict(os.environ, {"MANTICORE_USER_DIR": self.tmp}):
            os.environ.pop("MANTICORE_DATA_DIR", None)
            self.assertEqual(paths.rpg_saves_dir(), os.path.join(self.tmp, "rpg_saves"))
        self.assertNotEqual(os.path.basename(paths.rpg_saves_dir()), "rpg")      # never inside the rpg/ package folder

    def test_saves_are_never_published_or_packaged(self):
        from tools import export_public, nightly_package
        self.assertIn("rpg_saves", export_public.TOP_LEVEL_SKIP)
        self.assertIn("rpg_saves/", nightly_package.NEVER)
        import backup
        self.assertNotIn("rpg_saves", backup.TOP_LEVEL_SKIP)                    # backed up like your decks


# ---- a new game --------------------------------------------------------------------------------------------------------------

class NewGameTests(unittest.TestCase):
    def flow(self, seed=3, size=3):
        return newgame.NewGameFlow(seed=seed, decks=HALVES, pool=POOL, lists=lists(), numbers=numbers(), world=world(),
                                   offer_size=size)

    def test_the_steps(self):
        f = self.flow()
        self.assertEqual(f.step, "name")
        self.assertEqual(f.offer(), [])
        self.assertIn("name is needed", f.set_name("   "))
        self.assertIsNone(f.set_name(" Karl "))
        self.assertEqual((f.step, f.name), ("pick", "Karl"))
        for _ in range(3):
            f.pick(f.offer()[0].code)
        self.assertEqual(f.step, "done")
        with self.assertRaises(ValueError):
            f.pick("TST Cats 1")
        f.undo()
        self.assertEqual((f.step, len(f.picks)), ("pick", 2))
        f.undo(), f.undo(), f.undo()
        self.assertEqual(f.step, "name")

    def test_offers_have_different_themes_never_a_picked_half_and_come_from_the_seed(self):
        f = self.flow(size=3)
        f.set_name("Karl")
        first = [h.code for h in f.offer()]
        self.assertEqual(len({h.theme for h in f.offer()}), 3)
        self.assertEqual(first, [h.code for h in self.flow_named().offer()])           # same seed, same offer
        again = [h.code for h in f.reroll()]
        self.assertEqual(again, [h.code for h in self.flow_named(rerolls=1).offer()])
        f.pick(again[0])
        self.assertNotIn(again[0], [h.code for h in f.offer()])
        self.assertNotIn(again[0], [h.code for h in f.all_choices()])
        with self.assertRaises(ValueError):
            f.pick(again[0])                                         # never the same half-deck twice
        with self.assertRaises(ValueError):
            f.pick("TST Not A Half Deck 9")
        browse = [h.code for h in f.all_choices()]
        self.assertEqual(sorted(browse), sorted(h.code for h in HALVES if h.code != again[0]          # Round RPG9: within
                                                and len(products.colours_of(f.picks + [h], POOL)) <= 2))  # two colours
        self.assertTrue(browse)
        f.pick(browse[-1])                                            # one from the browse list, not the offer
        self.assertEqual(f.picks[-1].code, browse[-1])

    def flow_named(self, rerolls=0):
        f = self.flow()
        f.set_name("Karl")
        for _ in range(rerolls):
            f.reroll()
        return f

    def test_finish_makes_a_legal_owned_deck_and_the_start(self):
        f = self.flow(size=5)
        f.set_name("Karl")
        with self.assertRaises(ValueError):
            f.finish(1)
        for code in ("TST Cats 1", "TST Elves 1", "TST Elves 2"):
            f.pick(code)
        s = f.finish(2, now=1000.0)
        deck = s.deck()
        self.assertEqual(deck["name"], "Starting deck")
        self.assertEqual(sum(k for _n, k in deck["main"]), 60)
        main = dict((n, k) for n, k in deck["main"])
        self.assertEqual(main["Llanowar Elves"], 4)                 # 4 + 2 held to four
        self.assertEqual(collection.problems(deck, s.collection, POOL, lists()), [])
        self.assertEqual(s.collection.to_dict(), {"Grizzly Bears": 4, "Llanowar Elves": 4, "Pacifism": 4, "Savannah Lions": 4,
                                                  "Tree Folk": 4, "Wild Growth": 2})
        self.assertEqual((s.gold, s.slot, s.name), (100, 2, "Karl"))
        self.assertEqual(s.world, {"region": 1, "map": "green_town", "x": None, "y": None, "last_town": "green_town",
                                   "towns_visited": ["green_town"]})
        self.assertEqual(s.start, {"half_decks": ["TST Cats 1", "TST Elves 1", "TST Elves 2"]})
        self.assertEqual(s.forge, "Forge test")


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (the Jumpstart lists, Vintage's lists)")
class RuntimeNewGameTests(unittest.TestCase):
    def test_every_half_deck_is_known_and_random_starts_are_legal(self):
        pool_, lists_ = cardpool.load(), rules.vintage_lists(RUNTIME)
        decks = products.jumpstart(RUNTIME)
        self.assertEqual(len(decks), 363)
        unknown = sorted({n for d in decks for n, _k in d.cards if pool_.get(n) is None})
        self.assertEqual(unknown, [])
        banned = sorted({n for d in decks for n, _k in d.cards if lists_.is_banned(n)})
        self.assertEqual(banned, [])
        for seed in range(40):
            f = newgame.NewGameFlow(seed=seed, decks=decks, pool=pool_, lists=lists_)
            f.set_name("Karl")
            rng = random.Random(seed)
            for _ in range(3):
                f.pick(rng.choice(f.offer()).code)
            s = f.finish(1)
            with self.subTest(seed=seed, picks=s.start["half_decks"]):
                self.assertGreaterEqual(sum(k for _n, k in s.deck()["main"]), 60)
                self.assertEqual(collection.problems(s.deck(), s.collection, pool_, lists_), [])


# ---- shops -------------------------------------------------------------------------------------------------------------------

class ShopTests(Temp):
    def setUp(self):
        super().setUp()
        self.packs = os.path.join(self.tmp, "packs")
        os.makedirs(self.packs)
        packs = [[["Grizzly Bears", "TST", "1", "C"], ["Forest", "TST", "2", "L"], ["Tree Folk", "TST", "3", "C", True]],
                 [["Pacifism", "TST", "4", "C"], ["Plains", "TST", "5", "L"], ["Savannah Lions", "TST", "6", "R"]]]
        with open(os.path.join(self.packs, "TST.json"), "w", encoding="utf-8") as f:
            json.dump({"set": "TST", "name": "Test Set", "template": "x", "seed": 1, "forge": "f", "packs": packs}, f)
        p = mock.patch.object(products, "PACKS_DIR", self.packs)
        p.start()
        self.addCleanup(p.stop)
        shop._CANDIDATES.clear()

    def shop(self, s, town="green_town", **over):
        return shop.Shop(s, town, world(), numbers(**over), POOL, FakeTiers(), lists())

    def test_the_stock_holds_only_what_the_town_may_sell(self):
        s = new_save()
        offers = self.shop(s).singles()
        names = [o.name for o in offers]
        # green_town (GW), band 1-3: tier 1 GW/colourless = Grizzly Bears, Pacifism, Wild Growth, Tree Folk (not A-Grizzly Bears);
        # tier 2 = Savannah Lions, Ornithopter; tier 3 = Selesnya Guildmage. Never basics, Chaos Orb (banned), Black Lotus,
        # Llanowar Elves (tier 4), Lim-Dûl's Vault (UB).
        self.assertEqual(len(offers), 6)
        self.assertTrue(set(names) <= {"Grizzly Bears", "Pacifism", "Wild Growth", "Tree Folk", "Savannah Lions", "Ornithopter",
                                       "Selesnya Guildmage"})
        self.assertEqual(sorted(o.tier for o in offers), [1, 1, 1, 2, 2, 3])
        self.assertIn("Selesnya Guildmage", names)
        for o in offers:
            self.assertEqual(o.price, numbers().price(o.tier))
            self.assertTrue(1 <= o.copies <= 3 and o.left == o.copies)
        self.assertEqual([o.price for o in offers], sorted(o.price for o in offers))
        # a tier the numbers stock but the region's band leaves out (Llanowar Elves, tier 4, GW) is never stocked
        wider = self.shop(s, stock={"1": 3, "2": 2, "3": 1, "4": 1}).singles()
        self.assertEqual(sorted(o.tier for o in wider), [1, 1, 1, 2, 2, 3])
        self.assertNotIn("Llanowar Elves", [o.name for o in wider])
        blue = [o.name for o in self.shop(s, "blue_town").singles()]
        self.assertEqual(blue, ["Ornithopter"])             # U town: Lim-Dûl's Vault (UB) doesn't fit, Counterspell is tier 5

    def test_the_stock_stays_until_the_restock_and_then_changes(self):
        s = new_save()
        sh = self.shop(s)
        first = [(o.slot, o.name, o.copies) for o in sh.singles()]
        self.assertEqual(first, [(o.slot, o.name, o.copies) for o in self.shop(new_save()).singles()])
        sh.buy_single(sh.singles()[0].slot)
        self.assertEqual(s.shops["green_town"]["bought"], {first[0][0]: 1})
        s.record["duels"] = 1
        self.assertEqual([(o.slot, o.name, o.copies) for o in sh.singles()], first)
        s.record["duels"] = 2                                # restock_after_duels = 2
        later = [(o.slot, o.name, o.copies) for o in sh.singles()]
        self.assertEqual(s.shops["green_town"], {"restock": 1, "bought": {}})
        self.assertEqual(s.rng_counter, 0)                   # looking at a shop rolls nothing
        found_change = later != first
        for d in range(4, 40, 2):
            s.record["duels"] = d
            found_change = found_change or [(o.slot, o.name, o.copies) for o in sh.singles()] != first
        self.assertTrue(found_change)

    def test_buying_singles(self):
        s = new_save(gold=30)
        sh = self.shop(s)
        o = next(x for x in sh.singles() if x.price == 25)
        after = sh.buy_single(o.slot)
        self.assertEqual((s.gold, after.left, s.collection.count(o.name)), (5, o.left - 1, 1))
        cheap = next(x for x in sh.singles() if x.price == 10)
        with self.assertRaises(shop.ShopError) as cm:
            sh.buy_single(cheap.slot)
        self.assertIn("you have 5", str(cm.exception))
        s.gold = 1000
        for _ in range(cheap.left):
            sh.buy_single(cheap.slot)
        with self.assertRaises(shop.ShopError) as cm:
            sh.buy_single(cheap.slot)
        self.assertIn("sold out", str(cm.exception))
        with self.assertRaises(shop.ShopError):
            sh.buy_single("99")

    def test_packs_are_the_towns_sets_with_a_pool_and_open_into_the_collection(self):
        s = new_save(gold=200)
        sh = self.shop(s)
        self.assertEqual(sh.packs(), [("TST", "Test Set", 80)])
        opened = sh.buy_pack("TST")
        self.assertEqual(s.gold, 120)
        self.assertEqual(len(opened), 3)
        twin = new_save(gold=200)
        self.assertEqual(self.shop(twin).buy_pack("TST"), opened)       # the save's own randomness
        for c in opened:
            if c["free"]:
                self.assertIsNone(s.collection.count(c["name"]))
            else:
                self.assertTrue(c["new"])
                self.assertEqual(s.collection.count(c["name"]), 1)
        self.assertEqual(s.rng_counter, 1)
        with self.assertRaises(shop.ShopError):
            sh.buy_pack("NOPACKS")
        s.gold = 79
        with self.assertRaises(shop.ShopError) as cm:
            sh.buy_pack("TST")
        self.assertIn("80 gold", str(cm.exception))

    def test_selling(self):
        s = new_save(gold=0)
        s.collection.add("Selesnya Guildmage", 3)
        s.decks = [{"name": "Main", "main": [["Selesnya Guildmage", 2]], "sideboard": []}]
        sh = self.shop(s)
        self.assertEqual(sh.sell("Selesnya Guildmage", 1), 15)              # tier 3: 60 x 0.25
        self.assertEqual((s.gold, s.collection.count("Selesnya Guildmage")), (15, 2))
        with self.assertRaises(shop.ShopError) as cm:
            sh.sell("Selesnya Guildmage", 1)
        self.assertIn("Main", str(cm.exception))
        s.collection.add("Chaos Orb", 1)
        with self.assertRaises(shop.ShopError) as cm:
            sh.sell("Chaos Orb", 1)
        self.assertIn("No shop buys", str(cm.exception))


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (Vintage's lists)")
class ShippedShopTests(unittest.TestCase):
    def test_every_town_stocks_its_band_and_sells_its_packs(self):
        from rpg import tiers
        pool_, lists_ = cardpool.load(), rules.vintage_lists(RUNTIME)
        t = tiers.load(pool=pool_, lists=lists_)
        n, w = economy.read_numbers(), economy.read_world()
        s = sv.SaveGame(1, "Karl", 99, pool=pool_)
        s.gold = 10_000
        for town in w.towns.values():
            sh = shop.Shop(s, town.id, w, n, pool_, t, lists_)
            offers = sh.singles()
            lo, hi = w.band(town.region)
            with self.subTest(town=town.id):
                self.assertEqual(len(offers), sum(k for tier, k in n.stock.items() if lo <= tier <= hi))
                for o in offers:
                    card = pool_.get(o.name)
                    self.assertTrue(lo <= o.tier <= hi and not card.is_basic and not lists_.is_banned(o.name))
                    self.assertTrue(shop.fits(card, town.colours))
                    self.assertFalse(o.name.startswith("A-"))
                opened = sh.buy_pack(town.sets[0])
                self.assertEqual(len(opened), 15)
                self.assertTrue(all(pool_.get(c["name"]) is not None for c in opened))


# ---- duels -------------------------------------------------------------------------------------------------------------------

ENEMY = {"kind": "elf_gang", "name": "Elf Gang", "forge": "f", "seed": 1, "source": {}, "max_tier": 4,
         "decks": [{"name": "Elf Gang 1", "archetype": "x", "main": [["Llanowar Elves", 4], ["Grizzly Bears", 4], ["Tree Folk", 2],
                                                                      ["Forest", 50]], "sideboard": [["Pacifism", 1]]},
                   {"name": "Elf Gang 2", "archetype": "y", "main": [["Grizzly Bears", 4], ["Forest", 56]], "sideboard": []}],
         "rejected": [], "soak": None}
NO_BAND = dict(ENEMY, kind="lotus_gang", decks=[{"name": "L", "archetype": "z", "main": [["Black Lotus", 1], ["Llanowar Elves", 4],
                                                                                      ["Island", 55]], "sideboard": []}])


class DuelTests(Temp):
    def setUp(self):
        super().setUp()
        for data in (ENEMY, NO_BAND):
            with open(os.path.join(self.tmp, data["kind"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f)
        shop._CANDIDATES.clear()

    def start(self, s, kind="elf_gang", encounter="regular"):
        return duel.start(s, kind, encounter, POOL, lists(), folder=self.tmp, now=5.0)

    def finish(self, s, result, **over):
        return duel.finish(s, result, numbers(**over), world(), POOL, FakeTiers(), lists(), folder=self.tmp)

    def test_start_checks_the_deck_and_marks_the_duel(self):
        s = started_save()
        d = self.start(s)
        self.assertEqual((d.enemy_name, d.format, d.life, d.kind), ("Elf Gang", "standard", 20, "elf_gang"))
        self.assertIn(d.deck_index, (0, 1))
        self.assertEqual(sum(k for _n, k in d.player_main()), 60)
        self.assertEqual(s.pending_duel, {"kind": "elf_gang", "encounter": "regular", "deck": d.deck_index, "map": "green_town",
                                          "x": None, "y": None, "started": 5.0, "source": "archetype"})   # Round RPG9: source
        with self.assertRaises(duel.DuelError) as cm:
            self.start(s)
        self.assertIn("already", str(cm.exception))
        twin = started_save()
        self.assertEqual(self.start(twin).deck_index, d.deck_index)            # the save's own randomness

    def test_start_refuses(self):
        s = started_save()
        with self.assertRaises(duel.DuelError):
            self.start(s, encounter="picnic")
        with self.assertRaises(duel.DuelError) as cm:
            self.start(s, kind="nobody")
        self.assertIn("No enemy decks", str(cm.exception))
        s.collection.remove("Pacifism", 1)
        with self.assertRaises(duel.DuelError) as cm:
            self.start(s)
        self.assertIn("don't own enough", str(cm.exception))
        s.active_deck = None
        with self.assertRaises(duel.DuelError) as cm:
            self.start(s)
        self.assertIn("no deck", str(cm.exception))
        self.assertIsNone(s.pending_duel)

    def test_a_win_pays_gold_and_cards_from_the_enemys_deck_inside_the_band(self):
        for seed in range(20):
            s = started_save(seed=seed)
            before = dict(s.collection.to_dict())
            d = self.start(s)
            out = self.finish(s, "won")
            with self.subTest(seed=seed):
                self.assertEqual(out["result"], "won")
                self.assertTrue(20 <= out["gold"] <= 40)
                self.assertEqual(s.gold, 100 + out["gold"])
                self.assertEqual(len(out["cards"]), 2)
                deck_names = {n for n, _k in d.enemy_deck["main"] + d.enemy_deck["sideboard"]}
                for name, src in out["cards"]:
                    self.assertNotIn(name, ("Forest", "Llanowar Elves"))            # basic / tier 4: outside the band
                    if src == "deck":
                        self.assertIn(name, deck_names)
                    self.assertEqual(s.collection.count(name), before.get(name, 0) + 1)
                self.assertEqual(len({n for n, _s in out["cards"]}), 2)
                self.assertEqual(s.record, {"duels": 1, "wins": 1, "losses": 0, "draws": 0, "defeated": {"elf_gang": 1}})
                self.assertIsNone(s.pending_duel)
                self.assertIsNone(out["moved_to"])

    def test_a_deck_with_nothing_in_the_band_pays_cards_from_the_region(self):
        s = started_save()
        self.start(s, kind="lotus_gang", encounter="boss")
        out = self.finish(s, "won")
        self.assertEqual(out["gold"], 200)
        self.assertEqual(len(out["cards"]), 3)
        for name, src in out["cards"]:
            self.assertEqual(src, "region")
            self.assertIn(FakeTiers().tier_of(name), (1, 2, 3))
            self.assertNotIn(name, ("Black Lotus", "Llanowar Elves", "A-Grizzly Bears"))

    def test_a_loss_costs_gold_and_goes_back_to_the_last_town(self):
        for result in ("lost", "conceded"):
            s = started_save()
            s.gold = 101
            s.world.update(map="dungeon_1", x=7, y=9)
            duel.visit_town(s, "blue_town", world())
            self.start(s)
            out = self.finish(s, result)
            with self.subTest(result=result):
                self.assertEqual(out, {"result": result, "gold": -25, "cards": [], "moved_to": "blue_town"})
                self.assertEqual((s.gold, s.world["map"], s.world["x"], s.world["y"]), (76, "blue_town", None, None))
                self.assertEqual((s.record["losses"], s.record["duels"]), (1, 1))

    def test_a_draw_and_an_unfinished_duel(self):
        s = started_save()
        self.start(s)
        self.assertEqual(self.finish(s, "draw"), {"result": "draw", "gold": 0, "cards": [], "moved_to": None})
        self.assertEqual((s.record["draws"], s.record["duels"], s.gold), (1, 1, 100))
        self.start(s)
        self.assertEqual(self.finish(s, "unfinished")["gold"], 0)
        self.assertEqual(s.record["duels"], 1)                                   # an unfinished duel isn't counted
        with self.assertRaises(duel.DuelError):
            self.finish(s, "won")                                                # nothing going on
        self.start(s)
        with self.assertRaises(duel.DuelError):
            self.finish(s, "flipped the table")

    def test_a_duel_still_going_on_at_load_doesnt_count(self):
        s = started_save()
        self.assertIsNone(duel.settle_pending(s))
        self.start(s)
        sv.write(s, self.tmp)
        back = sv.read(1, self.tmp, pool=POOL)[0]
        self.assertIsNotNone(back.pending_duel)
        self.assertIn("doesn't count", duel.settle_pending(back))
        self.assertIsNone(back.pending_duel)
        self.assertEqual((back.gold, back.record["duels"]), (100, 0))

    def test_visiting_a_town(self):
        s = started_save()
        duel.visit_town(s, "blue_town", world())
        duel.visit_town(s, "blue_town", world())
        self.assertEqual((s.world["last_town"], s.world["towns_visited"]), ("blue_town", ["green_town", "blue_town"]))
        with self.assertRaises(KeyError):
            duel.visit_town(s, "atlantis", world())


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/")
class ShippedDuelTests(Temp):
    def test_a_new_game_can_fight_every_kind_of_enemy_and_a_whole_session_survives_a_reload(self):
        from rpg import tiers
        pool_, lists_ = cardpool.load(), rules.vintage_lists(RUNTIME)
        t = tiers.load(pool=pool_, lists=lists_)
        n, w = economy.read_numbers(), economy.read_world()
        f = newgame.NewGameFlow(seed=2026, pool=pool_, lists=lists_, numbers=n, world=w)
        f.set_name("Karl")
        for _ in range(3):
            f.pick(f.offer()[0].code)
        s = f.finish(1)
        for k in enemies.read_kinds():
            with self.subTest(kind=k["id"]):
                d = duel.start(s, k["id"], pool=pool_, lists=lists_)
                self.assertEqual(sum(c for _n, c in d.enemy_main()) >= 60, True)
                out = duel.finish(s, "won", n, w, pool_, t, lists_)
                self.assertEqual(len(out["cards"]), n.reward_cards("regular"))
                lo, hi = w.band(1)
                for name, _src in out["cards"]:
                    self.assertTrue(lo <= t.tier_of(name) <= hi)
        sh = shop.Shop(s, w.start_town, w, n, pool_, t, lists_)
        s.gold += 500
        sh.buy_pack(w.town(w.start_town).sets[0])
        sh.buy_single(sh.singles()[0].slot)
        sv.write(s, self.tmp)
        back, note = sv.read(1, self.tmp, pool=pool_)
        self.assertIsNone(note)
        self.assertEqual(back.to_dict(), s.to_dict())
        self.assertEqual(collection.problems(back.deck(), back.collection, pool_, lists_), [])


if __name__ == "__main__":
    unittest.main()
