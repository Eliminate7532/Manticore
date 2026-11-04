# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PACK1 - Karl, 3 Nov 2026: "Add more options for purchasable packs." He picked all four kinds: Jumpstart theme packs,
more sets, a premium pack, a bundle of 3.

1. Six more booster sets, two per town (8ED, 10E; 9ED, 6ED; FDN, 7ED), each with Forge's own 200-pack pool, none with a bigger share
   of tier 4-5 cards than the most of today's ten core sets (M10).
2. Each shop stocks 3 Jumpstart theme packs in its colours, drawn at each restock like the singles; one of each, gone once bought.
3. A premium pack of each set: Forge's BoosterGenerator with the set's own template, 3 commons fewer, 2 uncommons and 1 rare more.
4. A bundle of 3 plain packs of one set at once, 210 gold.
5. Every kind's price beside what its cards sell back for; theme and premium packs must cost more (shop.price_problems).
6. The packs tab lists every kind (it scrolls); a bundle's cards show a pack at a time; all of it by tap.
Each kind draws with its own generator, so the packs an older save opens are the ones it opened before."""
import json
import os
import random
import statistics
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import cardpool, economy, products, rules, shop, tiers
from rpg import save as sv
from tests import test_rpg4 as r4
from tests import test_rpg8 as r8
from tests.test_forge_table import frame
from tests.test_rpg14b import StyleBase

TEN = ("M10", "M11", "M12", "M13", "M14", "M15", "ORI", "M19", "M20", "M21")
SIX = ("8ED", "10E", "9ED", "6ED", "FDN", "7ED")
SIZES = (((1360, 840), None), ((1920, 1080), None), ((2736, 1824), None),
         ((1360, 840), 2.0), ((1920, 1080), 2.0), ((2736, 1824), 2.0))


def high_share(code, pool, t):
    """The share of a pool's non-basic cards, as they come out of its packs, with tier 4 or 5."""
    n = h = 0
    for p in products.read_pool(code)["packs"]:
        for c in p:
            card = pool.get(c[0])
            if card.is_basic:
                continue
            n += 1
            tt = t.tier_of(card.name)
            h += 1 if tt is not None and tt >= 4 else 0
    return h / n


def rares(p):
    return sum(1 for c in p if c[3] in ("R", "M"))


class SetTests(unittest.TestCase):
    def test_each_town_sells_five_or_six_sets_with_a_pool_and_a_premium_pool(self):
        w = economy.read_world()
        sold = []
        for town in w.towns.values():
            with self.subTest(town=town.id):
                self.assertIn(len(town.sets), (5, 6))
                for code in town.sets:
                    self.assertTrue(products.has_packs(code), code)
                    self.assertTrue(products.has_premium(code), code)
                    self.assertEqual(len(products.read_pool(code)["packs"]), 200)
                    self.assertEqual(len(products.read_premium(code)["packs"]), 200)
                sold += town.sets
        self.assertEqual(sorted(sold), sorted(TEN + SIX))
        self.assertEqual(len(set(sold)), len(sold))

    def test_the_new_sets_are_built_like_the_old_and_none_is_stronger_than_the_strongest_old_one(self):
        pool = cardpool.load()
        t = tiers.load(pool=pool)
        worst = max(high_share(c, pool, t) for c in TEN)
        for code in SIX:
            with self.subTest(set=code):
                d = products.read_pool(code)
                self.assertEqual(d["seed"], 20261012)                 # tools/rpg_build_data.py PACK_SEED
                self.assertIn("9a6f776", d["forge"])
                self.assertTrue(all(pool.get(c[0]) is not None for p in d["packs"] for c in p))
                self.assertLessEqual(high_share(code, pool, t), worst)


class PremiumTests(unittest.TestCase):
    def test_every_premium_pack_has_an_extra_rare_and_two_more_uncommons(self):
        for code in TEN + SIX:
            with self.subTest(set=code):
                plain, prem = products.read_pool(code)["packs"], products.read_premium(code)["packs"]
                self.assertEqual(products.read_premium(code)["seed"], 20261013)
                self.assertTrue(all(rares(p) >= 2 for p in prem))
                self.assertGreaterEqual(statistics.mean(map(rares, prem)), statistics.mean(map(rares, plain)) + 0.9)
                u = lambda ps: statistics.mean(sum(1 for c in p if c[3] == "U") for p in ps)      # noqa: E731
                self.assertGreaterEqual(u(prem), u(plain) + 1.9)
                self.assertEqual({len(p) for p in prem}, {len(p) for p in plain})               # the same size
        tpl = products.read_premium("M10")["template"]
        self.assertIn("7 Common, 5 Uncommon, 2 RareMythic, and 1 BasicLand", tpl)

    def test_the_build_tool_knows_premium(self):
        import tools.rpg_build_data as b
        self.assertEqual(b.PACK1_SETS, SIX)
        self.assertEqual((b.PACK_SEED, b.PACKS_PER_SET, b.PREMIUM_SEED), (20261012, 200, 20261013))
        self.assertEqual(b.jar_stamp(), b.source_hash())               # rpg_tools.jar holds the premium command
        with open(os.path.join(b.SRC, "forge", "rpgtools", "RpgData.java"), encoding="utf-8") as f:
            self.assertIn('case "premium"', f.read())


@unittest.skipUnless(r4.HAS_RUNTIME, "needs forge_runtime/ (the Jumpstart lists, Vintage's lists)")
class ShopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = cardpool.load()
        cls.lists = rules.vintage_lists(r4.RUNTIME)
        cls.tiers = tiers.load(pool=cls.pool, lists=cls.lists)
        cls.numbers, cls.world = economy.read_numbers(), economy.read_world()

    def save(self, gold=10_000, seed=99):
        s = sv.SaveGame(1, "Karl", seed, pool=self.pool)
        s.gold = gold
        return s

    def shop(self, s, town="town_1"):
        return shop.Shop(s, town, self.world, self.numbers, self.pool, self.tiers, self.lists)

    def test_three_theme_packs_in_the_towns_colours_one_each_gone_until_restock(self):
        s = self.save()
        sh = self.shop(s)
        stock = sh.themes()
        self.assertEqual(len(stock), 3)
        self.assertEqual(len({h.theme for h, _p in stock}), 3)
        self.assertEqual([h.code for h, _p in stock], [h.code for h, _p in self.shop(self.save()).themes()])   # stable
        self.assertEqual(s.rng_counter, 0)                       # looking rolls nothing
        h, price = stock[0]
        self.assertEqual(price, self.numbers.jumpstart_price)
        gold = s.gold
        got = sh.buy_theme(h.code)
        self.assertEqual(s.gold, gold - price)
        self.assertEqual(len(got), h.size)
        self.assertEqual(sorted(c["name"] for c in got), sorted(n for n, k in h.cards for _ in range(k)))
        self.assertEqual([x.code for x, _p in sh.themes()], [x.code for x, _p in stock[1:]])
        with self.assertRaises(shop.ShopError) as cm:
            sh.buy_theme(h.code)
        self.assertIn("sold out", str(cm.exception))
        self.assertEqual(s.shops["town_1"]["themes"], [h.code])
        s.record["duels"] = self.numbers.restock_after_duels           # a restock: a new draw, nothing bought
        self.assertEqual(s.shops.get("town_1", {}).get("restock"), 0)
        later = [x.code for x, _p in sh.themes()]
        self.assertEqual(len(later), 3)
        self.assertEqual(s.shops["town_1"].get("themes", []), [])
        changed = later != [x.code for x, _p in stock]
        for d in range(2, 12):
            s.record["duels"] = d * self.numbers.restock_after_duels
            changed = changed or [x.code for x, _p in sh.themes()] != [x.code for x, _p in stock]
        self.assertTrue(changed)

    def test_a_town_never_stocks_a_theme_outside_its_colours(self):
        s = self.save()
        for town in self.world.towns.values():
            sh = self.shop(s, town.id)
            with self.subTest(town=town.id):
                for d in range(0, 40 * self.numbers.restock_after_duels, self.numbers.restock_after_duels):
                    s.record["duels"] = d
                    for h, _p in sh.themes():
                        cols = products.colours(h, self.pool)
                        self.assertTrue(cols and all(c in town.colours for c in cols), (town.id, h.code, cols))
                self.assertNotIn("J22 Urza's", [h.code for h in sh.theme_candidates()])     # colourless: never

    def test_premium_packs_and_their_own_draws(self):
        s = self.save()
        sh = self.shop(s)
        codes = [c for c, _n, _p in sh.premium_packs()]
        self.assertEqual(codes, self.world.town("town_1").sets)
        want = products.open_premium("M10", s.peek_rng("pack:premium:M10"))
        got = sh.buy_premium("M10")
        self.assertEqual([c["name"] for c in got], [n for n, _r, _f in want])
        self.assertGreaterEqual(sum(1 for c in got if c["rarity"] in ("R", "M")), 2)
        self.assertEqual(s.gold, 10_000 - 160)
        s.gold = 159
        with self.assertRaises(shop.ShopError) as cm:
            sh.buy_premium("M10")
        self.assertIn("160 gold", str(cm.exception))
        with self.assertRaises(shop.ShopError):
            sh.buy_premium("M13")                                    # Brackwater's

    def test_a_bundle_is_three_packs_45_cards_for_210(self):
        s = self.save()
        sh = self.shop(s)
        self.assertEqual(sh.bundles()[0], ("M10", "Magic 2010", 210))
        want = products.open_bundle("M10", s.peek_rng("pack:bundle:M10"))
        got = sh.buy_bundle("M10")
        self.assertEqual(len(got), 45)
        self.assertEqual(s.gold, 10_000 - 210)
        self.assertEqual([c["pack"] for c in got], [0] * 15 + [1] * 15 + [2] * 15)
        self.assertEqual([c["name"] for c in got], [n for p in want for n, _r, _f in p])
        self.assertEqual(sum(1 for c in got if c["rarity"] in ("R", "M")) >= 3, True)
        s.gold = 209
        with self.assertRaises(shop.ShopError):
            sh.buy_bundle("M10")

    def test_the_plain_pack_draws_as_before(self):
        s = self.save()
        sh = self.shop(s)
        want = products.open_pack("M10", s.peek_rng("pack:M10"))            # the stream it used before PACK1
        sh.buy_premium("M11")
        sh.buy_bundle("M12")
        theme = sh.themes()[0][0]
        sh.buy_theme(theme.code)
        twin = self.save()
        self.assertEqual(self.shop(twin).buy_pack("M10"), [dict(c) for c in self.shop(self.save()).buy_pack("M10")])
        self.assertEqual([n for n, _r, _f in want], [c["name"] for c in self.shop(self.save()).buy_pack("M10")])

    def test_the_shelf_lists_every_kind(self):
        sh = self.shop(self.save())
        shelf = sh.shelf()
        kinds = [k for k, _c, _p in shelf]
        self.assertEqual(kinds[:3], ["theme"] * 3)
        self.assertEqual(kinds[3:], ["plain", "premium", "bundle"] * 5)
        self.assertEqual([c for k, c, _p in shelf if k == "plain"], self.world.town("town_1").sets)

    def test_an_old_save_loads_and_buys_every_kind(self):
        s = self.save(seed=7)
        s.shops = {"town_1": {"restock": 0, "bought": {"0": 1}}}          # a shop as a save from before PACK1 holds it
        d = json.loads(json.dumps(s.to_dict()))
        self.assertNotIn("themes", json.dumps(d["shops"]))
        with _TempDir() as folder:
            with open(sv.save_path(1, folder), "w", encoding="utf-8") as f:
                json.dump(d, f)
            back = sv.read(1, folder, pool=self.pool)[0]
        sh = self.shop(back)
        sh.buy_pack("M10")
        sh.buy_premium("M10")
        sh.buy_bundle("8ED")
        sh.buy_theme(sh.themes()[0][0].code)
        self.assertEqual(back.shops["town_1"]["bought"], {"0": 1})        # what it had bought stays
        self.assertEqual(len(back.shops["town_1"]["themes"]), 1)


class _TempDir:
    def __enter__(self):
        import tempfile
        self.path = tempfile.mkdtemp(prefix="pack1_")
        return self.path

    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.path, True)


class SaveMendTests(unittest.TestCase):
    def test_a_damaged_themes_list_is_mended_not_refused(self):
        s = r4.new_save()
        s.shops = {"green_town": {"restock": 0, "bought": {}, "themes": ["JMP Above the Clouds 1", 7, ""]}}
        with _TempDir() as folder:
            sv.write(s, folder)
            back = sv.read(1, folder, pool=r4.POOL)[0]
        self.assertEqual(back.shops["green_town"]["themes"], ["JMP Above the Clouds 1"])


class PriceTests(unittest.TestCase):
    def test_every_price_against_what_its_cards_sell_back_for(self):
        n, w = economy.read_numbers(), economy.read_world()
        vals = shop.pack_values(n, towns=w.towns)
        self.assertEqual(shop.price_problems(n, vals), [])
        self.assertEqual((n.pack_price, n.jumpstart_price, n.premium_pack_price, n.bundle_price), (80, 90, 160, 210))
        self.assertEqual(n.bundle_price, int(0.875 * 3 * n.pack_price))
        kinds = {k for k, _w in vals}
        self.assertEqual(kinds, {"plain", "premium", "bundle", "theme"})
        self.assertEqual(len([1 for k, _w in vals if k == "theme"]), 3)

    def test_a_price_below_its_sell_back_is_refused(self):
        n, w = economy.read_numbers(), economy.read_world()
        vals = shop.pack_values(n, towns=w.towns)
        n.jumpstart_price = 60                                          # the brief's placeholder: Brackwater's sell back 68.5
        probs = shop.price_problems(n, vals)
        self.assertTrue(any("jumpstart_price 60" in p and "town_2" in p for p in probs), probs)
        n.jumpstart_price, n.premium_pack_price = 90, 70
        probs = shop.price_problems(n, vals)
        self.assertTrue(any("premium_pack_price 70" in p and "M10" in p for p in probs), probs)

    def test_older_numbers_files_still_read(self):
        with open(economy.NUMBERS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        for k in ("jumpstart_price", "premium_pack_price", "bundle_price", "jumpstart_stock"):
            d.pop(k)
        n = economy.Numbers(d)
        self.assertEqual((n.jumpstart_price, n.premium_pack_price, n.bundle_price, n.jumpstart_stock), (90, 160, 210, 3))


@unittest.skipUnless(r4.HAS_RUNTIME, "needs forge_runtime/")
class ScreenTests(StyleBase):
    def town(self, size=(1360, 840), scale=None, town=None):
        g = self.gui(size, scale)
        f = self.open(g, slot=1)
        f.enter_town(g, town or f.game.world.start_town)
        f.tab = "packs"
        f.game.save.gold = 10_000
        f.game.save.shops = {}                                       # nothing bought yet (the slot is shared by every subtest)
        frame(g)
        return g, f

    def find(self, f, prefix):
        return [(r, n) for r, n in f.buttons if n.startswith(prefix)]

    def tap(self, g, f, pos):
        f._last_tap = None
        g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1))
        g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1))
        frame(g)

    def test_the_tab_lists_every_kind_and_a_tap_buys_a_theme_pack(self):
        r8.new_game(None)
        g, f = self.town()
        shelf = f.shop.shelf()
        self.assertEqual(len(shelf), 18)
        _area, scrolls, _step = f._pan_views["town:packs"]
        self.assertTrue(scrolls)                                      # 18 lines: the list scrolls (SELL1's drag)
        tile = next(r for r, kind, data in f.rows if kind == "shelf" and data == shelf[0][:2])     # Round PACK2: tap the
        self.tap(g, f, tile.center)                                   # wrapper, then its Buy and open
        (rect, name), = self.find(f, "theme:" + shelf[0][1])
        self.tap(g, f, rect.center)
        self.assertEqual(f.stage, "pack")
        self.assertEqual(len(f.opened[1]), f.shop.theme_stock()[0].size)
        self.assertTrue(f.opened[0].endswith("Jumpstart") or " - Jumpstart" in f.opened[0])
        f.press(g, "pack_done")
        frame(g)
        self.assertEqual(len(f.shop.shelf()), 17)
        self.assertFalse(self.find(f, "theme:" + shelf[0][1]))
        self.assertFalse([d for _r, k, d in f.rows if k == "shelf" and d == shelf[0][:2]])

    def test_a_bundle_shows_one_pack_a_page_with_new_marks(self):
        r8.new_game(None)
        g, f = self.town()
        f.press(g, "bundle:M10")
        frame(g)
        self.assertEqual((f.stage, len(f.opened[1]), f.pack_pages()), ("pack", 45, 3))
        self.assertTrue(self.find(f, "pack_next"))
        self.assertFalse(self.find(f, "pack_prev"))                   # greyed on the first pack
        seen = [f.pack_page]
        for _ in range(2):
            self.tap(g, f, self.find(f, "pack_next")[0][0].center)
            seen.append(f.pack_page)
        self.assertEqual(seen, [0, 1, 2])
        self.assertFalse(self.find(f, "pack_next"))
        self.tap(g, f, self.find(f, "pack_prev")[0][0].center)
        self.assertEqual(f.pack_page, 1)
        self.assertTrue(any(c["new"] for c in f.opened[1]))

    def test_everything_fits(self):
        r8.new_game(None)
        for size, scale in SIZES:
            for town in ("town_1", "town_2", "town_3"):
                with self.subTest(size=size, scale=scale, town=town):
                    g, f = self.town(size, scale, town)
                    win = pygame.Rect((0, 0), g.screen.get_size())
                    for r, n in f.buttons:
                        self.assertTrue(win.contains(r), (n, r))
                    for kind in ("theme", "bundle"):
                        f.stage = "town"
                        name = (f"theme:{f.shop.themes()[0][0].code}" if kind == "theme" else "bundle:" + f.shop.packs()[0][0])
                        f.press(g, name)
                        frame(g)
                        self.assertEqual(f.stage, "pack")
                        for r, n in f.buttons:
                            self.assertTrue(win.contains(r), (kind, n, r))
                        f.press(g, "pack_done")
                        frame(g)
                    self.close(g)


if __name__ == "__main__":
    unittest.main()
