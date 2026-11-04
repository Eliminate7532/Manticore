# SPDX-License-Identifier: GPL-3.0-or-later
"""Bug 1566241279598264410 (Karl, 31 Oct 2026, "is this supposed to be a shop?"; his answer of 3 Nov: "Visible stall"). The two shops
on the Ashfen outskirts - Brackwater's and Cinder Gate's - were hidden trigger tiles drawn as plain road at the map's east and south
edges: walking along the road opened a shop out of nowhere. Now each is a stall beside the road: a counter with its keeper behind it,
crates, a signpost and a sign with the town's name, opened by talking to the keeper (as Lantern Hollow's counters, RPG15b), or by a
tap (RPG-TAP); the road itself is never blocked and never opens a shop.

  StallMapTests     each shop is on a counter tile (never a walkable one), its keeper behind it, its spawn on the road in front;
                    no road tile opens a shop; the map passes its checks
  StallWindowTests  talking to each keeper opens that town's shop and leaving puts you back in front of the stall; a tap on the
                    counter does the same; the sign with the town's name is drawn over the stall (and none in Lantern Hollow)

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -m unittest tests.test_bug_shopstall
"""
import unittest

import pygame

from rpg import explore, talk, tapwalk
from tests import rpg_route
from tests.test_forge_table import frame, key
from tests.test_rpg14 import WindowBase
from tests.test_rpgtap import TapBase, place

OUT, TOWN = "ashfen_outskirts", "lantern_hollow"
STALLS = (("brackwater_trader", "town_2", "Brackwater"), ("cinder_trader", "town_3", "Cinder Gate"))


def middle_tile(a, box):
    return a.tile_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def shop_tiles(a, t):
    x0, y0, x1, y1 = t.box
    return {(c, r) for r in range(int(y0 // a.th), int(-(-y1 // a.th))) for c in range(int(x0 // a.tw), int(-(-x1 // a.tw)))}


class StallMapTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()
        self.a = explore.load_area(OUT)

    def test_each_shop_is_a_counter_with_its_keeper_behind_it_and_its_spawn_in_front(self):
        a = self.a
        shops = {t.town: t for t in a.triggers if t.kind == "town"}
        self.assertEqual(set(shops), {"town_2", "town_3"})
        for nid, town, _name in STALLS:
            with self.subTest(town=town):
                t = shops[town]
                counter = middle_tile(a, t.box)
                self.assertEqual(shop_tiles(a, t), {counter})            # one tile, and that a counter
                self.assertTrue(a.is_counter(*counter))
                keeper = a.npcs[nid]["tile"]
                dx, dy = keeper[0] - counter[0], keeper[1] - counter[1]
                self.assertEqual(abs(dx) + abs(dy), 1, "the keeper stands right behind the counter")
                front = (counter[0] - dx, counter[1] - dy)
                self.assertFalse(a.blocked(*front))
                self.assertEqual(a.tile_at(*a.spawn(town)), front)         # you leave the shop standing at the stall

    def test_each_keeper_is_talked_to_only_across_the_counter(self):
        """Talking to a keeper from behind the stall would give their words but no shop: the stall's goods wall them in."""
        a = self.a
        for nid, _town, _name in STALLS:
            c, r = a.npcs[nid]["tile"]
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                with self.subTest(nid=nid, side=(dx, dy)):
                    self.assertTrue(a.blocked(c + dx, r + dy))
                    if a.is_counter(c + dx, r + dy):
                        self.assertFalse(a.blocked(c + 2 * dx, r + 2 * dy))

    def test_no_tile_you_can_walk_on_opens_a_shop(self):
        a = self.a
        for t in a.triggers:
            if t.kind == "town":
                for tile in shop_tiles(a, t):
                    with self.subTest(town=t.town, tile=tile):
                        self.assertTrue(a.blocked(*tile), "a shop on a tile you can walk on opens by surprise")

    def test_the_roads_old_shop_tiles_are_road_again_and_the_map_passes_its_checks(self):
        a = self.a
        for tile in ((59, 19), (59, 20), (51, 39), (52, 39)):            # where the hidden shops were
            with self.subTest(tile=tile):
                self.assertFalse(a.blocked(*tile))
                self.assertFalse(any(tile in shop_tiles(a, t) for t in a.triggers if t.kind == "town"))
        self.assertEqual(explore.check_all()[OUT], [])
        self.assertEqual(talk.check(map_npcs={n for name in explore.map_names() for n in explore.load_area(name).npcs}), [])


class StallWindowTests(TapBase):
    def on_outskirts(self, g, town):
        f = self.on_map(g, OUT, town)                              # the spawn in front of the stall
        f.game.save.world.setdefault("flags", [])
        return f

    def test_talking_to_each_keeper_opens_their_towns_shop_and_you_leave_at_the_stall(self):
        for nid, town, name in STALLS:
            with self.subTest(town=town):
                g, _l = self.setup_game()
                f = self.on_outskirts(g, town)
                a = f.explorer.area
                self.stand(g, nid)
                self.assertIsNotNone(f.explorer.facing_shop())
                key(g, pygame.K_RETURN)
                self.assertEqual(f.talk_box.name, talk.npcs()[nid]["name"])
                self.assertIn(name, " ".join(f.talk_box.pages))       # the keeper says whose stall it is
                self.say_all(g)
                self.assertEqual((f.stage, f.town), ("town", town))
                self.press(g, "leave_town")
                self.assertEqual(f.stage, "map")
                self.assertEqual(a.tile_at(f.explorer.walker.x, f.explorer.walker.y), a.tile_at(*a.spawn(town)))

    def test_a_tap_on_the_counter_talks_to_the_keeper_and_the_shop_follows(self):
        for nid, town, _name in STALLS:
            with self.subTest(town=town):
                g, _l = self.setup_game()
                f = self.on_map(g, OUT, "from_town")
                a = f.explorer.area
                counter = next(middle_tile(a, t.box) for t in a.triggers if t.kind == "town" and t.town == town)
                place(f, a.tile_at(*a.spawn(town)))
                key(g, pygame.K_UP)                                    # turn away, so the tap has to face them again
                self.tap_tile(g, counter)
                self.assertIsNotNone(f.tap)
                self.assertEqual((f.tap.goal.kind, f.tap.goal.nid), ("person", nid))
                self.walk(g)
                self.assertEqual(f.sub, "talk")
                self.assertIsNotNone(f.talk_shop)
                self.assertEqual(f.talk_shop.town, town)

    def test_the_sign_over_each_stall_names_its_town(self):
        for nid, town, name in STALLS:
            with self.subTest(town=town):
                g, _l = self.setup_game()
                f = self.on_outskirts(g, town)
                frame(g)
                words = dict(f.sign_words)
                self.assertIn(name, words)
                rect = words[name]
                self.assertTrue(g.screen.get_rect().contains(rect))
                a = f.explorer.area
                kx, ky = f.camera.to_screen(*rpg_route.middle(a, a.npcs[nid]["tile"]))
                self.assertLess(rect.bottom, ky, "the sign is above the keeper, not over the road")

    def test_lantern_hollows_own_counters_have_no_sign(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        frame(g)
        self.assertEqual(f.sign_words, [])


if __name__ == "__main__":
    unittest.main()
