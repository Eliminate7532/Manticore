# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PACK2 - Karl, 3 Nov 2026: "Also add a visual image of what those packs look like." His choice: our own wrapper + a card -
a foil pack drawn by the game in the set's colours, with the set's name and a real card picture from that set on its front.

1. pack_art.pack_surface draws every kind (plain, premium with a gold edge, bundle of three fanned, theme), never outside its w x h,
   cached; without the card picture the same wrapper without the window.
2. The card on the front: a set's highest-tier rare or mythic in its pool (ties: most often, then the name), each set its own card,
   that set's printing; a theme pack's highest-tier non-basic card (ties: the name); overrides in rpg/data/pack_faces.json.
3. The packs tab: every kind as its wrapper with its price, a grid that scrolls; a tap shows the details and Buy and open; fits at
   1360x840, 1920x1080, 2736x1824 and 200% text; the pictures never hold the shelf up.
Nothing on the wrapper but our own drawing and the card picture (no art crop, no picture file of a real wrapper)."""
import ast
import os
import re
import time
import unittest
from unittest import mock

import pygame

import gfx
import pack_art
import rpg_screens as rs
from rpg import cardpool, pack_faces, products, tiers
from tests import test_ad1
from tests import test_rpg4 as r4
from tests import test_rpg8 as r8
from tests.test_forge_table import frame
from tests.test_rpg14b import StyleBase

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = (((1360, 840), None), ((1920, 1080), None), ((2736, 1824), None),
         ((1360, 840), 2.0), ((1920, 1080), 2.0), ((2736, 1824), 2.0))
MARK = (250, 0, 250)                     # a colour no wrapper has: the stand-in card picture's


def picture(w=244, h=340):
    s = pygame.Surface((w, h))
    s.fill(MARK)
    return s


def has_mark(surf):
    w, h = surf.get_size()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            c = surf.get_at((x, y))
            if c.a > 200 and abs(c.r - MARK[0]) < 6 and c.g < 6 and abs(c.b - MARK[2]) < 6:
                return True
    return False


class ArtSpy:
    """A stand-in art loader: `have` - card picture keys that are 'on disk'; every call is counted."""

    def __init__(self, have=(), inner=None):
        self.have, self.calls, self.inner = set(have), [], inner

    def __getattr__(self, name):                 # everything else (collect, ...) from the table's own loader
        if name == "inner" or self.__dict__.get("inner") is None:
            raise AttributeError(name)
        return getattr(self.inner, name)

    def get(self, key):
        self.calls.append(("get", key))
        return picture() if key in self.have else None

    def preview(self, key, w, h):
        self.calls.append(("preview", key))
        return picture(w, h) if key in self.have else None

    def art_crop(self, key, w, h):
        self.calls.append(("art_crop", key))
        return picture(w, h)

    def add_names(self, names):
        self.calls.append(("add_names", tuple(sorted(map(str, names)))))

    def pending(self):
        return 0


class WrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        if pygame.display.get_surface() is None:
            pygame.display.set_mode((64, 64))

    def test_every_kind_draws_inside_its_size_at_many_sizes(self):
        for kind in pack_art.KINDS:
            for w, h in ((40, 64), (90, 145), (186, 300), (400, 645), (61, 300), (300, 120)):
                with self.subTest(kind=kind, size=(w, h)):
                    surf = pack_art.pack_surface(kind, "Magic 2010", "Booster - 15 cards", "G", picture(), w, h)
                    self.assertEqual(surf.get_size(), (w, h))
                    box = surf.get_bounding_rect()
                    self.assertTrue(surf.get_rect().contains(box))
                    self.assertGreater(box.w * box.h, 0.25 * w * h)        # a wrapper, not an empty picture

    def test_a_wrapper_has_the_card_in_a_window_only_when_the_picture_is_here(self):
        for kind in pack_art.KINDS:
            with self.subTest(kind=kind):
                on = pack_art.pack_surface(kind, "Tenth Edition", "Booster - 15 cards", "R", picture(), 186, 300)
                off = pack_art.pack_surface(kind, "Tenth Edition", "Booster - 15 cards", "R", None, 186, 300)
                self.assertTrue(has_mark(on))
                self.assertFalse(has_mark(off))
                self.assertEqual(off.get_size(), on.get_size())

    def test_cached_by_what_changes_it(self):
        pic = picture()
        a = pack_art.pack_surface("plain", "Magic 2011", "Booster - 15 cards", "W", pic, 120, 194, card_key="x")
        self.assertIs(a, pack_art.pack_surface("plain", "Magic 2011", "Booster - 15 cards", "W", pic, 120, 194, card_key="x"))
        for other in (("premium", "Magic 2011", "Booster - 15 cards", "W", pic, 120, 194, "x"),
                      ("plain", "Magic 2012", "Booster - 15 cards", "W", pic, 120, 194, "x"),
                      ("plain", "Magic 2011", "Booster - 14 cards", "W", pic, 120, 194, "x"),
                      ("plain", "Magic 2011", "Booster - 15 cards", "U", pic, 120, 194, "x"),
                      ("plain", "Magic 2011", "Booster - 15 cards", "W", None, 120, 194, "x"),
                      ("plain", "Magic 2011", "Booster - 15 cards", "W", pic, 121, 194, "x"),
                      ("plain", "Magic 2011", "Booster - 15 cards", "W", pic, 120, 194, "y")):
            with self.subTest(other=other[:4] + other[5:]):
                self.assertIsNot(a, pack_art.pack_surface(*other[:7], card_key=other[7]))

    def test_the_colours(self):
        self.assertEqual(pack_art.body_colours("WU"), (gfx.PACK_W, gfx.PACK_U))
        self.assertEqual(pack_art.body_colours("BG"), (gfx.PACK_B, gfx.PACK_G))
        for one in "WUBRG":
            top, bottom = pack_art.body_colours(one)
            base = getattr(gfx, pack_art.COLOUR_TOKENS[one])
            self.assertGreater(sum(top), sum(base))                      # lighter at the top, darker at the bottom
            self.assertLess(sum(bottom), sum(base))
        silver, gold = pack_art.body_colours("C"), pack_art.body_colours("WUB")
        self.assertEqual(silver, pack_art.body_colours(""))
        self.assertGreater(sum(silver[0]), sum(gfx.PACK_SILVER))
        self.assertEqual(gold, pack_art.body_colours("WUBRG"))
        self.assertNotEqual(silver, gold)

    def test_premium_has_a_gold_edge_and_the_plain_pack_none(self):
        prem = pack_art.pack_surface("premium", "M", "", "U", None, 186, 300)
        plain = pack_art.pack_surface("plain", "M", "", "U", None, 186, 300)
        edge = tuple(gfx.PACK_EDGE)
        count = lambda s: sum(1 for y in range(0, 300, 3) for x in range(0, 186, 3) if tuple(s.get_at((x, y)))[:3] == edge)  # noqa
        self.assertGreater(count(prem), 40)
        self.assertEqual(count(plain), 0)


class NothingButOursTests(unittest.TestCase):
    def test_pack_art_loads_no_picture_and_names_no_real_wrapper(self):
        with open(os.path.join(BASE, "pack_art.py"), encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertFalse(calls & {"load", "art_crop", "open"}, calls & {"load", "art_crop", "open"})
        code = re.sub(r'"""(.|\n)*?"""', "", src)                    # the words drawn may only be what the caller gives
        self.assertNotRegex(code, r"Wizards|Magic: The Gathering|Booster|Jumpstart")

    def test_the_colour_guard_covers_pack_art(self):
        self.assertIn("pack_art.py", test_ad1.GUARDED)
        stray = [v for _l, v in test_ad1.NoColourLiteralsOutsideGfx().literals("pack_art.py") if v not in test_ad1.ALLOWED_LITERALS]
        self.assertEqual(stray, [])

    def test_the_shelf_asks_for_the_full_card_never_an_art_crop(self):
        art = ArtSpy(have={"Birds of Paradise"})
        gui = mock.Mock(art=art)
        flow = rs.AdventureFlow.__new__(rs.AdventureFlow)
        self.assertIsNotNone(flow.pack_picture(gui, "Birds of Paradise", 130, 182))
        self.assertIsNotNone(flow.pack_picture(gui, "Birds of Paradise", 400, 560))
        self.assertEqual([c[0] for c in art.calls], ["get", "preview"])
        self.assertIsNone(flow.pack_picture(mock.Mock(art=None), "Birds of Paradise", 130, 182))


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = cardpool.load()
        cls.t = tiers.load(pool=cls.pool)

    def fake_pool(self, packs):
        return mock.patch.object(products, "read_pool", lambda code, folder=None: {"packs": packs})

    def test_highest_tier_rare_then_most_often_then_the_name(self):
        tier = {"A": 3, "B": 4, "C": 4, "D": 4, "E": 5}
        t = mock.Mock(tier_of=lambda n: tier.get(n))
        pool = mock.Mock(get=lambda n: None)
        packs = [[["E", "X", "1", "U"], ["A", "X", "2", "R"], ["C", "X", "3", "R"], ["D", "X", "4", "M"]],
                 [["B", "X", "5", "R"], ["D", "X", "4", "M"]],
                 [["C", "X", "3", "R"], ["B", "X", "5", "R"]]]
        with self.fake_pool(packs), mock.patch.object(products, "has_packs", lambda c: True):
            ranking = pack_faces._set_ranking("X", pool, t)
        self.assertEqual([n for n, _p in ranking], ["B", "C", "D", "A"])     # E is uncommon; B, C, D tier 4 twice each: names
        self.assertEqual(ranking[0][1], ("X", "5"))

    def test_every_set_has_its_own_card_from_its_own_pool(self):
        faces = pack_faces.set_faces(self.pool, self.t, overrides={"sets": {}, "themes": {}})
        codes = pack_faces.set_order()
        self.assertEqual(sorted(faces), sorted(codes))
        self.assertEqual(len({v[0] for v in faces.values()}), len(faces))    # each set its own card
        taken = set()
        for code in codes:
            name, pset, num = faces[code]
            with self.subTest(set=code, card=name):
                rm = {c[0]: c for p in products.read_pool(code)["packs"] for c in p if c[3] in ("R", "M")}
                self.assertIn(name, rm)
                self.assertEqual(pset.upper(), code)                   # that set's printing
                best = max(self.t.tier_of(n) or -1 for n in rm if n not in taken)
                self.assertEqual(self.t.tier_of(name), best)
            taken.add(name)

    def test_an_override_wins_and_a_wrong_name_is_ignored(self):
        faces = pack_faces.set_faces(self.pool, self.t, overrides={"sets": {"M10": "Serra Angel", "M11": "No Such Card"},
                                                                 "themes": {}})
        self.assertEqual(faces["M10"], ("Serra Angel", None, None))
        self.assertNotEqual(faces["M11"][0], "No Such Card")
        self.assertIn(faces["M11"][0], {c[0] for p in products.read_pool("M11")["packs"] for c in p})

    def test_the_overrides_file_is_there_and_empty(self):
        self.assertEqual(pack_faces.read_overrides(), {"sets": {}, "themes": {}})
        self.assertEqual(pack_faces.read_overrides("/no/such/file.json"), {"sets": {}, "themes": {}})

    def test_a_theme_pack_shows_its_highest_tier_non_basic_card(self):
        for h in products.jumpstart()[:40]:
            name = pack_faces.theme_face(h, self.pool, self.t, overrides={"sets": {}, "themes": {}})
            with self.subTest(theme=h.code):
                others = [self.pool.get(n) for n, _k in h.cards if self.pool.get(n) is not None and not self.pool.get(n).is_basic]
                best = max(self.t.tier_of(c.name) or -1 for c in others)
                self.assertEqual(self.t.tier_of(name), best)
                self.assertEqual(name, min(c.name for c in others if (self.t.tier_of(c.name) or -1) == best))
                self.assertFalse(self.pool.get(name).is_basic)

    def test_the_colours_are_the_cards(self):
        self.assertEqual(pack_faces.face_colours("Birds of Paradise", self.pool), "G")
        self.assertEqual(pack_faces.face_colours(None, self.pool), "C")


@unittest.skipUnless(r4.HAS_RUNTIME, "needs forge_runtime/")
class ShelfTests(StyleBase):
    def town(self, size=(1360, 840), scale=None, town=None, art=None):
        g = self.gui(size, scale)
        art = art if art is not None else ArtSpy()
        art.inner = g.art
        g.art = art
        f = self.open(g, slot=1)
        f.enter_town(g, town or f.game.world.start_town)
        f.tab = "packs"
        f.game.save.gold = 10_000
        f.game.save.shops = {}
        f.shelf_pick = None
        frame(g)
        return g, f

    def tiles(self, f):
        return [(r, d) for r, k, d in f.rows if k == "shelf"]

    def tap(self, g, f, pos):
        f._last_tap = None
        g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1))
        g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1))
        frame(g)

    def test_each_kind_is_a_wrapper_with_its_price_and_a_tap_shows_its_details_then_buys(self):
        r8.new_game(None)
        g, f = self.town()
        drawn = []
        real = pack_art.pack_surface
        with mock.patch.object(pack_art, "pack_surface", lambda *a, **k: drawn.append(a[:4]) or real(*a, **k)):
            frame(g)
        shelf = f.shop.shelf()
        tiles = self.tiles(f)
        self.assertTrue(tiles)
        self.assertEqual([d for _r, d in tiles], [s[:2] for s in shelf[:len(tiles)]])
        self.assertEqual({a[0] for a in drawn}, {"theme", "plain", "premium", "bundle"} & {k for k, _c, _p in shelf[:len(tiles)]})
        self.assertFalse([n for _r, n in f.buttons if n.startswith(("pack:", "premium:", "bundle:", "theme:"))])
        rect, (kind, code) = tiles[3]
        self.tap(g, f, rect.center)
        self.assertEqual(f.shelf_pick, (kind, code))
        buy, = [(r, n) for r, n in f.buttons if n == f"{'pack' if kind == 'plain' else kind}:{code}"]
        gold = f.game.save.gold
        self.tap(g, f, buy[0].center)
        self.assertEqual(f.stage, "pack")
        self.assertLess(f.game.save.gold, gold)

    def test_the_grid_scrolls(self):
        r8.new_game(None)
        g, f = self.town((1360, 840), 2.0)
        first = self.tiles(f)
        _area, scrolls, step = f._pan_views["town:packs"]
        self.assertTrue(scrolls)
        f.wheel(g, -1)
        frame(g)
        self.assertNotEqual(self.tiles(f)[0][1], first[0][1])

    def test_everything_fits_and_no_wrapper_leaves_its_place(self):
        r8.new_game(None)
        for size, scale in SIZES:
            for town in ("town_1", "town_2", "town_3"):
                with self.subTest(size=size, scale=scale, town=town):
                    g, f = self.town(size, scale, town)
                    win = pygame.Rect((0, 0), g.screen.get_size())
                    f.shelf_pick = f.shop.shelf()[0][:2]
                    frame(g)
                    tiles = self.tiles(f)
                    self.assertTrue(tiles)
                    for i, (r, d) in enumerate(tiles):
                        self.assertTrue(f.rect.contains(r), (d, r))
                        for r2, d2 in tiles[i + 1:]:
                            self.assertFalse(r.colliderect(r2), (d, d2))
                    for r, n in f.buttons:
                        self.assertTrue(win.contains(r), (n, r))
                    self.assertTrue(any(n.endswith(":" + f.shelf_pick[1]) for _r, n in f.buttons))
                    self.close(g)

    def test_the_shelf_never_waits_for_a_picture(self):
        r8.new_game(None)
        art = ArtSpy()
        g, f = self.town(art=art)
        seen = []
        real = pack_art.pack_surface
        with mock.patch.object(pack_art, "pack_surface", lambda *a, **k: seen.append(a[4]) or real(*a, **k)):
            art.calls.clear()
            t0 = time.monotonic()
            frame(g)
            took = time.monotonic() - t0
        self.assertLess(took, 1.0)
        self.assertTrue(seen)
        self.assertTrue(all(p is None for p in seen))                    # no picture yet: the wrapper without its window
        gets = [c for c in art.calls if c[0] in ("get", "preview")]
        self.assertLessEqual(len(gets), len(seen))                       # asked once each, never again in a loop
        art.have = {k for _c, k in gets}                                 # the pictures arrive: the next frame has them
        seen.clear()
        with mock.patch.object(pack_art, "pack_surface", lambda *a, **k: seen.append(a[4]) or real(*a, **k)):
            frame(g)
        self.assertTrue(seen and all(p is not None for p in seen))

    def test_offline_no_art_loader_still_draws(self):
        r8.new_game(None)
        g, f = self.town()
        with mock.patch.object(g, "art", None):
            with mock.patch.object(rs.AdventureFlow, "prefetch", lambda *a: None):
                frame(g)
        self.assertTrue(self.tiles(f))

    def test_the_wrapper_faces_are_the_rules(self):
        r8.new_game(None)
        g, f = self.town()
        halves = {h.code: h for h in f.shop.theme_stock()}
        faces = f.pack_faces(f.shop.shelf(), halves)
        for (kind, code), key in faces.items():
            with self.subTest(kind=kind, code=code):
                if kind == "theme":
                    self.assertEqual(key, pack_faces.theme_face(halves[code], f.game.pool, f.shop.tiers))
                else:
                    self.assertEqual(key, pack_faces.set_face_key(code, f.game.pool, f.shop.tiers))


if __name__ == "__main__":
    unittest.main()
