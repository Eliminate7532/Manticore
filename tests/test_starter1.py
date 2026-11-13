# SPDX-License-Identifier: GPL-3.0-or-later
"""Round STARTER1 (Karl, 4 Nov 2026: "Adventure starting decks are so bad they're not even fun to play"; he picked "Upgrade picked
halves"): each picked Jumpstart half-deck of a new game gives up its K weakest spells for stronger cards (rpg/starter.py).

  - The rule, on Forge's real half-decks and the real Adventure pool: same card type, mana value within 1, the half's colours, a
    higher tier than the card replaced and at most the cap, the theme's other half-decks first, copy limits, two colours, lands
    unchanged; deterministic from the seed; K = 0 is the deck made before this round.
  - The game's numbers (numbers.json "starter_upgrade"), the new-game flow (the preview, the save's record, the collection) and the
    tier cap never above the start region's band.
  - The foes are NOT upgraded (rpg/enemies.py doesn't use the upgrade), and rpg/data/balance.json is of the upgraded starting decks.
"""
import json
import os
import random
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from rpg import cardpool, economy, enemies, newgame, products, rules, starter, tiers  # noqa: E402

RUNTIME = os.environ.get("FORGE_RUNTIME") or os.path.join(BASE, "forge_runtime")
HAS_RUNTIME = os.path.isfile(os.path.join(RUNTIME, rules.VINTAGE_FILE))
NUMBERS_FILE = os.path.join(BASE, "rpg", "data", "numbers.json")
BALANCE_FILE = os.path.join(BASE, "rpg", "data", "balance.json")

_DATA = {}


def data():
    if not _DATA:
        pool = cardpool.load()
        _DATA.update(pool=pool, lists=rules.vintage_lists(), tiers=tiers.load(pool=pool), decks=products.jumpstart())
    return _DATA


def sample_picks(rng, decks, pool):
    picks = []
    while len(picks) < newgame.PICKS:
        free = [h for h in decks if h.code not in {p.code for p in picks}
                and len(products.colours_of(picks + [h], pool)) <= newgame.MAX_COLOURS]
        picks.append(rng.choice(free))
    return picks


def samples(n=40, seed=7):
    d = data()
    rng = random.Random(seed)
    return [(sample_picks(rng, d["decks"], d["pool"]), rng.getrandbits(40)) for _ in range(n)]


def build(picks, seed, k, max_tier=3):
    d = data()
    return starter.starting_deck(picks, seed, k, d["pool"], d["lists"], d["tiers"], d["decks"], max_tier)


def counts(deck):
    out = {}
    for n, c in deck:
        key = cardpool.normal_name(n)
        out[key] = out.get(key, 0) + c
    return out


# ==================================================================================================================================
@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime (python setup_forge.py)")
class RuleTests(unittest.TestCase):
    K = 4

    @classmethod
    def setUpClass(cls):
        cls.games = samples()
        cls.built = [(p, s, build(p, s, cls.K)) for p, s in cls.games]

    def test_k_zero_is_the_deck_made_before_this_round(self):
        d = data()
        for picks, seed in self.games:
            deck, swaps = build(picks, seed, 0)
            self.assertEqual(swaps, [])
            trimmed = [starter.trim_half(h, d["pool"], d["tiers"])[0] for h in picks]     # Round STARTER1C: 20 a half
            self.assertEqual(deck, products.starter_deck(trimmed, d["pool"], d["lists"]))

    def test_every_swap_keeps_the_type_and_the_mana_value_within_one(self):
        pool = data()["pool"]
        n = 0
        for _p, _s, (_deck, swaps) in self.built:
            for w in swaps:
                a, b = pool.get(w.out), pool.get(w.into)
                self.assertEqual(starter.card_kind(a), starter.card_kind(b), w)
                self.assertLessEqual(abs(a.mana_value - b.mana_value), starter.MV_SLACK, w)
                self.assertFalse(b.is_land, w)
                n += 1
        self.assertGreater(n, len(self.built) * 3 * (self.K - 1))       # nearly every half gets its K swaps

    def test_every_swap_is_in_the_halfs_colours(self):
        pool = data()["pool"]
        for picks, _s, (_deck, swaps) in self.built:
            by_code = {h.code: products.colours(h, pool) for h in picks}
            for w in swaps:
                self.assertTrue(starter.on_colour(pool.get(w.into), by_code[w.half]), (w, by_code[w.half]))

    def test_the_deck_keeps_its_colours_and_its_lands(self):
        pool = data()["pool"]
        for picks, seed, (deck, _w) in self.built:
            before = build(picks, seed, 0)[0]
            lands = lambda dk: sorted((n, c) for n, c in dk if pool.get(n) and pool.get(n).is_land)  # noqa: E731
            self.assertEqual(lands(deck), lands(before))
            cols = {x for n, _c in deck if pool.get(n) and not pool.get(n).is_land for x in pool.get(n).colours if x in "WUBRG"}
            self.assertLessEqual(len(cols), newgame.MAX_COLOURS, deck)
            self.assertEqual(sum(c for _n, c in deck), sum(c for _n, c in before))

    def test_every_swap_is_stronger_and_never_above_the_cap(self):
        t = data()["tiers"]
        for _p, _s, (_deck, swaps) in self.built:
            for w in swaps:
                self.assertGreater(w.in_tier, w.out_tier, w)
                self.assertLessEqual(w.in_tier, 3, w)
                self.assertEqual(t.tier_of(w.into), w.in_tier)

    def test_a_lower_cap_is_kept(self):
        for picks, seed in self.games[:15]:
            _deck, swaps = build(picks, seed, self.K, max_tier=2)
            self.assertTrue(all(w.in_tier <= 2 for w in swaps), swaps)

    def test_copy_limits_hold(self):
        d = data()
        for _p, _s, (deck, _w) in self.built:
            for n, c in deck:
                card = d["pool"].get(n)
                if card is None or card.is_basic:
                    continue
                limit = 1 if d["lists"].is_restricted(card.name) else (10 ** 6 if card.copy_limit < 0 else card.copy_limit or 4)
                self.assertLessEqual(c, limit, (n, c))
                self.assertFalse(d["lists"].is_banned(card.name), n)

    def test_the_theme_comes_first(self):
        d = data()
        pool, t = d["pool"], d["tiers"]
        theme = pool_n = 0
        for picks, _s, (deck, swaps) in self.built:
            have = counts(deck)
            gone = {cardpool.normal_name(w.out) for w in swaps}
            by_code = {h.code: h for h in picks}
            for w in swaps:
                half = by_code[w.half]
                mates = {n for h in starter.theme_mates(half, d["decks"]) for n, _c in h.cards}
                if w.source == "theme":
                    theme += 1
                    self.assertIn(w.into, mates, w)
                    continue
                pool_n += 1
                out = pool.get(w.out)
                cols = products.colours(half, pool)
                for m in mates:
                    c = pool.get(m)
                    if c is None or c.is_land or not starter.on_colour(c, cols):
                        continue
                    if starter.card_kind(c) != starter.card_kind(out) or abs(c.mana_value - out.mana_value) > 1:
                        continue
                    tm = t.tier_of(m) or 0
                    if not (w.out_tier < tm <= 3) or m.startswith("A-") or d["lists"].is_banned(m) or d["lists"].is_restricted(m):
                        continue
                    key = cardpool.normal_name(m)
                    if key in gone or have.get(key, 0) >= 4:
                        continue                         # the copy limit may have stopped it at the time
                    self.fail(f"{w}: the theme's {m} would have fitted")
        self.assertGreater(theme, 0)
        self.assertGreater(pool_n, 0)

    def test_the_same_seed_makes_the_same_deck_and_another_seed_may_not(self):
        differ = 0
        for picks, seed in self.games[:20]:
            a, sa = build(picks, seed, self.K)
            b, sb = build(picks, seed, self.K)
            self.assertEqual(a, b)
            self.assertEqual([w.as_list() for w in sa], [w.as_list() for w in sb])
            differ += build(picks, seed + 1, self.K)[0] != a
        self.assertGreater(differ, 0)

    def test_the_weakest_go_first(self):
        d = data()
        for picks, seed in self.games[:10]:
            _deck, swaps = build(picks, seed, 1)
            for w in swaps:
                half = next(h for h in picks if h.code == w.half)
                order = starter.weakest(half, None, d["pool"], d["tiers"])
                self.assertEqual(d["tiers"].tier_of(order[0]) or 0, w.out_tier)


# ==================================================================================================================================
class NumbersTests(unittest.TestCase):
    def test_numbers_json_upgrades_the_starting_decks(self):
        with open(NUMBERS_FILE, encoding="utf-8") as f:
            up = json.load(f)["starter_upgrade"]
        nums = economy.read_numbers()
        self.assertEqual((nums.starter_per_half, nums.starter_max_tier), (up["per_half"], up["max_tier"]))
        self.assertGreater(nums.starter_per_half, 0)          # the measured K, not today's deck
        self.assertEqual(nums.starter_max_tier, 3)

    def test_round_starter1b_karls_numbers(self):
        """Karl, 7 Nov 2026: "Stronger: 4 swaps" and "Only Moat"."""
        with open(NUMBERS_FILE, encoding="utf-8") as f:
            up = json.load(f)["starter_upgrade"]
        self.assertEqual(up["per_half"], 4)
        self.assertEqual(up["never"], ["Moat"])
        nums = economy.read_numbers()
        self.assertEqual(nums.starter_per_half, 4)
        self.assertEqual(nums.starter_never, ("Moat",))

    def test_never_missing_is_none_and_a_bad_value_or_an_unknown_name_is_refused_by_name(self):
        with open(NUMBERS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        up = dict(raw["starter_upgrade"])
        up.pop("never")
        self.assertEqual(economy.Numbers(dict(raw, starter_upgrade=up)).starter_never, ())
        for bad in ("Moat", [3], ["Moat", ""], {"Moat": 1}):
            with self.assertRaises(ValueError) as cm:
                economy.Numbers(dict(raw, starter_upgrade=dict(up, never=bad)))
            self.assertIn("starter_upgrade.never", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            economy.Numbers(dict(raw, starter_upgrade=dict(up, never=["Moat", "Moaat"])))
        self.assertIn("'Moaat'", str(cm.exception))
        with self.assertRaises(ValueError):                   # a key nobody reads is still refused
            economy.Numbers(dict(raw, starter_upgrade=dict(up, nevr=["Moat"])))
        self.assertEqual(economy.Numbers(dict(raw, starter_upgrade=dict(up, never=["moat"]))).starter_never, ("Moat",))

    def test_a_file_without_the_key_makes_todays_deck_and_a_bad_value_is_refused(self):
        with open(NUMBERS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        raw.pop("starter_upgrade")
        self.assertEqual(economy.Numbers(raw).starter_per_half, 0)
        for bad in ({"per_half": 2}, {"per_half": -1, "max_tier": 3}, {"per_half": 2, "max_tier": 9}, [2, 3]):
            with self.assertRaises(ValueError):
                economy.Numbers(dict(raw, starter_upgrade=bad))


# ==================================================================================================================================
@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime (python setup_forge.py)")
class NeverTests(unittest.TestCase):
    """Round STARTER1B: a card on the never list is not chosen even where it is the best fit; the next fitting card is."""
    def moat_cases(self, n=400):
        """(picks, seed) whose K 4 deck gets Moat without the list."""
        out = []
        for picks, seed in samples(n, seed=21):
            deck, swaps = build(picks, seed, 4)
            if any(w.into == "Moat" for w in swaps):
                out.append((picks, seed, swaps))
        return out

    def test_moat_is_never_chosen_and_the_next_fitting_card_is(self):
        d = data()
        cases = self.moat_cases()
        self.assertTrue(cases, "no sampled deck got Moat - the fixture needs more samples")
        for picks, seed, swaps in cases[:5]:
            deck, swaps2 = starter.starting_deck(picks, seed, 4, d["pool"], d["lists"], d["tiers"], d["decks"], 3,
                                                 never=("Moat",))
            self.assertNotIn("moat", counts(deck))
            self.assertFalse(any(w.into == "Moat" for w in swaps2))
            self.assertEqual(len(swaps2), len(swaps))         # still K a half: another card took Moat's place
            moat = next(w for w in swaps if w.into == "Moat")
            # the copy Moat replaced is still upgraded, to another enchantment of mana value 3-5 (the next fit), or - when none
            # fits - a later copy is; either way the swap count holds and Moat is gone
            others = [w for w in swaps2 if w.half == moat.half and w.out == moat.out]
            for w in others:
                c = d["pool"].get(w.into)
                self.assertEqual(starter.card_kind(c), "Enchantment")
                self.assertLessEqual(abs(c.mana_value - 4), starter.MV_SLACK)

    def test_the_game_flow_honours_numbers_json_never(self):
        d = data()
        picks, seed, _swaps = self.moat_cases()[0]
        f = newgame.NewGameFlow(seed=seed, pool=d["pool"], lists=d["lists"], decks=d["decks"])
        f.picks = list(picks)
        self.assertNotIn("moat", counts(f.preview()))
        nums = economy.read_numbers()
        f2 = newgame.NewGameFlow(seed=seed, pool=d["pool"], lists=d["lists"], decks=d["decks"],
                                 numbers=economy.Numbers(dict(json.load(open(NUMBERS_FILE, encoding="utf-8")),
                                                              starter_upgrade={"per_half": 4, "max_tier": 3})))
        f2.picks = list(picks)
        self.assertEqual(nums.starter_per_half, 4)
        self.assertIn("moat", counts(f2.preview()))           # the same picks without the list do get Moat


# ==================================================================================================================================
@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime (python setup_forge.py)")
class FlowTests(unittest.TestCase):
    def flow(self, seed=11, numbers=None, world=None):
        d = data()
        f = newgame.NewGameFlow(seed=seed, pool=d["pool"], lists=d["lists"], decks=d["decks"], numbers=numbers, world=world)
        f.set_name("Karl")
        rng = random.Random(seed)
        while f.step == "pick":
            f.pick(rng.choice(f.offer()).code)
        return f

    def test_the_preview_and_the_save_are_the_upgraded_deck(self):
        f = self.flow()
        nums = economy.read_numbers()
        deck, swaps = build(f.picks, f.seed, nums.starter_per_half)
        self.assertEqual(f.preview(), deck)
        self.assertEqual([w.as_list() for w in f.upgrades()], [w.as_list() for w in swaps])
        self.assertTrue(f.upgraded())
        save = f.finish(1, now=0)
        self.assertEqual(save.start["upgrades"], [w.as_list() for w in swaps])
        main = {cardpool.normal_name(n): k for n, k in save.decks[0]["main"]}
        for w in swaps:
            self.assertGreaterEqual(main.get(cardpool.normal_name(w.into), 0), 1)
            self.assertGreaterEqual(save.collection.count(w.into), 1)
        self.assertNotEqual(f.preview(), products.starter_deck(f.picks, data()["pool"], data()["lists"]))

    def test_the_cap_is_never_above_the_start_regions_band(self):
        world = economy.read_world()
        f = self.flow()
        self.assertEqual(f.upgrade_max_tier(), min(3, world.band(world.town(world.start_town).region)[1]))
        with mock.patch.object(economy.World, "band", lambda self, r: (1, 2)):
            g = self.flow(world=economy.read_world())
            self.assertEqual(g.upgrade_max_tier(), 2)
            self.assertTrue(all(w.in_tier <= 2 for w in g.upgrades()))


# ==================================================================================================================================
class FoeTests(unittest.TestCase):
    def test_the_foes_are_not_upgraded(self):
        with open(os.path.join(BASE, "rpg", "enemies.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("starter", src.replace("starter_deck", ""))

    @unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime")
    def test_foe_decks_dont_change_with_the_upgrade(self):
        d = data()
        kind = enemies.read_kinds()[0]
        a = enemies.jumpstart_candidates(kind, 2, 303, pool=d["pool"], lists=d["lists"], tier_table=d["tiers"], swap=9)
        with mock.patch.object(starter, "upgrade_half", side_effect=AssertionError("a foe was upgraded")):
            b = enemies.jumpstart_candidates(kind, 2, 303, pool=d["pool"], lists=d["lists"], tier_table=d["tiers"], swap=9)
        self.assertEqual([x["main"] for x in a], [x["main"] for x in b])

    def test_balance_json_is_of_the_upgraded_starting_decks_and_the_retuned_foes(self):
        with open(BALANCE_FILE, encoding="utf-8") as f:
            bal = json.load(f)
        with open(NUMBERS_FILE, encoding="utf-8") as f:
            up = json.load(f)["starter_upgrade"]
        self.assertEqual(bal.get("starter_upgrade"), up)
        files = sorted({d.get("handicap", 0) for k in enemies.regular_kinds()
                        for d in enemies.read_kind(k).get(enemies.JUMPSTART) or []})
        self.assertEqual([bal["handicap"]], files)
        self.assertEqual(bal["decks_sha"], enemies.jumpstart_digest(enemies.regular_kinds()))
        self.assertIn("starter1", bal)                 # how the handicap was re-checked with the upgraded decks
        self.assertIn("starter1b", bal)                # Round STARTER1B: re-measured with K 4 and the never list
        self.assertEqual(bal["starter1b"]["starter_upgrade"], up)
        self.assertEqual(bal["measured"]["handicap"], files)


if __name__ == "__main__":
    unittest.main()
