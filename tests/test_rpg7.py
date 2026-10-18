# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG7: Karl's answers of 13 Oct to the RPG's open questions (RPG4's and RPG6's "For Karl").

  - basic lands are bought, 1 gold each (numbers.json basic_lands_free false, basic_land_price 1): every shop sells the six plain
    basics, packs and the starting deck give them, shops don't buy them back, a save keeps the rule it was made under;
  - the walker stops dead at a wall (explore.CORNER_HELP off - no slide across to a gap, no drift onto the grid while stopped);
  - the slide to the next room takes 0.3 s;
  - only a dungeon is dark (map property dungeon, or a dungeon / boss encounter); light on another map is left out and said;
  - PyTMX reads the Tiled maps (rpg/tmx.py turns what it read into the RPG's own objects), checked against the files themselves.

  EconomyTests, ShopTests, SaveTests, NewGameTests   the basic lands
  WallTests, SlideTests, DarkTests                     the map
  PyTmxTests                                           the reader, the licence, requirements.txt
"""
import copy
import csv
import io
import json
import os
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from rpg import collection, economy, explore, newgame, shop, tmx
from rpg import save as sv
from tests import test_rpg4 as r4
from tests.test_rpg5 import BASE, Temp, area_of, layer_xml, obj, room, write_map

TILE = 16
PAID = {"basic_lands_free": False, "basic_land_price": 1}


def mid(c, r):
    return c * TILE + TILE / 2, r * TILE + TILE / 2


def paid_save(gold=100, slot=1):
    s = sv.SaveGame(slot, "Karl", 7, pool=r4.POOL, basics_free=False)
    s.created = s.updated = 1000.0
    s.gold = gold
    s.world = {"region": 1, "map": "green_town", "x": 3, "y": 4, "last_town": "green_town", "towns_visited": ["green_town"]}
    return s


def paid_shop(s):
    return shop.Shop(s, "green_town", r4.world(), r4.numbers(**PAID), r4.POOL, r4.FakeTiers(), r4.lists())


# ---- the basic lands ------------------------------------------------------------------------------------------------------------

class EconomyTests(unittest.TestCase):
    def test_the_shipped_numbers_sell_basic_lands_for_one_gold(self):
        n = economy.read_numbers()
        self.assertFalse(n.basic_lands_free)
        self.assertEqual(n.basic_land_price, 1)

    def test_a_price_is_needed_when_basics_are_not_free_and_never_zero(self):
        with self.assertRaises(ValueError) as cm:
            r4.numbers(basic_lands_free=False)
        self.assertIn("basic_land_price", str(cm.exception))
        for bad in (0, -1, "1", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                r4.numbers(basic_lands_free=False, basic_land_price=bad)
            self.assertIn("basic_land_price", str(cm.exception))
        self.assertEqual(r4.numbers().basic_land_price, 0)                 # free (RPG4's tests): no price, none needed
        self.assertEqual(r4.numbers(basic_land_price=3).basic_land_price, 0)


class ShopTests(r4.Temp):
    def setUp(self):
        super().setUp()
        shop._CANDIDATES.clear()

    def test_every_shop_sells_the_basic_lands_it_has_at_one_gold(self):
        sh = paid_shop(paid_save())
        self.assertEqual(sh.basics(), [("Plains", 1), ("Island", 1), ("Forest", 1)])   # the made-up pool has three of the six
        free = shop.Shop(r4.new_save(), "green_town", r4.world(), r4.numbers(), r4.POOL, r4.FakeTiers(), r4.lists())
        self.assertEqual(free.basics(), [])

    def test_buying_basic_lands(self):
        s = paid_save(gold=10)
        sh = paid_shop(s)
        self.assertEqual(sh.buy_basic("forest", 7), 7)
        self.assertEqual((s.gold, s.collection.count("Forest")), (3, 7))
        self.assertEqual(sh.buy_basic("Island"), 1)
        self.assertEqual((s.gold, s.collection.count("Island")), (2, 1))
        with self.assertRaises(shop.ShopError) as cm:
            sh.buy_basic("Plains", 3)
        self.assertIn("3 gold; you have 2", str(cm.exception))
        self.assertEqual((s.gold, s.collection.count("Plains")), (2, 0))       # nothing taken
        for bad in ("Grizzly Bears", "Swamp", "Snow-Covered Forest"):
            with self.subTest(bad=bad), self.assertRaises(shop.ShopError):
                sh.buy_basic(bad)
        for bad in (0, -2, 2.9, "two", True):                               # a review of the round: these went through int()
            with self.subTest(k=bad), self.assertRaises(shop.ShopError):
                sh.buy_basic("Forest", bad)
        self.assertEqual((s.gold, s.collection.count("Forest")), (2, 7))

    def test_the_saves_rule_decides_not_numbers_json(self):
        s = paid_save(gold=5)
        free_numbers = shop.Shop(s, "green_town", r4.world(), r4.numbers(), r4.POOL, r4.FakeTiers(), r4.lists())
        self.assertEqual(free_numbers.basics(), [("Plains", 0), ("Island", 0), ("Forest", 0)])   # counted, at numbers' price
        free_numbers.buy_basic("Forest", 3)
        self.assertEqual((s.gold, s.collection.count("Forest")), (5, 3))
        free_save = shop.Shop(r4.new_save(), "green_town", r4.world(), r4.numbers(**PAID), r4.POOL, r4.FakeTiers(), r4.lists())
        self.assertEqual(free_save.basics(), [])

    def test_basic_lands_are_never_singles_and_no_shop_buys_them_back(self):
        s = paid_save(gold=0)
        s.collection.add("Forest", 5)
        sh = paid_shop(s)
        self.assertFalse(any(collection.cardpool.normal_name(o.name) == "forest" for o in sh.singles()))
        with self.assertRaises(shop.ShopError) as cm:
            sh.sell("Forest", 1)
        self.assertIn("No shop buys", str(cm.exception))
        self.assertEqual((s.gold, s.collection.count("Forest")), (0, 5))

    def test_a_deck_needs_its_basic_lands_owned_now(self):
        c = collection.Collection({"Grizzly Bears": 4}, pool=r4.POOL, basics_free=False)
        d = {"name": "d", "main": [["Grizzly Bears", 4], ["Forest", 56]], "sideboard": []}
        probs = collection.owned_problems(d, c)
        self.assertIn("Forest (the deck uses 56, you own 0)", probs[0].text)
        c.add("Forest", 56)
        self.assertEqual(collection.owned_problems(d, c), [])

    def test_pack_basics_are_counted(self):
        s = paid_save()
        self.assertEqual(s.collection.count("Forest"), 0)
        s.collection.add("Forest", 2)                                      # what buy_pack does with each card
        self.assertEqual(s.collection.count("Forest"), 2)
        self.assertFalse(s.collection.is_free("Forest"))


class NewGameTests(unittest.TestCase):
    def test_the_starting_deck_comes_with_its_basic_lands_owned(self):
        flow = newgame.NewGameFlow(seed=11, decks=r4.HALVES, pool=r4.POOL, lists=r4.lists(), numbers=r4.numbers(**PAID),
                                   world=r4.world(), offer_size=5)
        flow.set_name("Karl")
        for code in ("TST Cats 1", "TST Elves 1", "TST Elves 2"):
            flow.pick(code)
        s = flow.finish(1, now=1000.0)
        deck = dict(s.decks[0]["main"])
        self.assertGreater(deck.get("Forest", 0), 0)
        self.assertEqual(s.collection.count("Forest"), deck["Forest"])
        self.assertEqual(s.collection.count("Plains"), deck.get("Plains", 0))
        self.assertEqual(collection.owned_problems(s.decks[0], s.collection), [])
        self.assertFalse(s.collection.basics_free)


class SaveTests(r4.Temp):
    def test_a_save_keeps_its_rule_whatever_the_reader_is_told(self):
        s = paid_save()
        s.collection.add("Forest", 9)
        sv.write(s, self.tmp)
        back, note = sv.read(1, self.tmp, pool=r4.POOL)                   # the reader's default: free
        self.assertIsNone(note)
        self.assertFalse(back.collection.basics_free)
        self.assertEqual(back.collection.count("Forest"), 9)
        self.assertEqual(back.to_dict(), s.to_dict())
        with open(sv.save_path(1, self.tmp), encoding="utf-8") as f:
            self.assertIs(json.load(f)["basics_free"], False)

    def test_a_version_1_save_keeps_free_basics_whatever_the_reader_is_told(self):
        """Version 1 saves were made while basic lands were free: the migration says so (a review of the round: reading them under
        the caller's rule would have dropped every basic land the moment a screen passed numbers.json's "counted")."""
        self.assertEqual(sv.VERSION, 2)
        s = r4.new_save()
        d = s.to_dict()
        d.pop("basics_free")
        d["version"] = 1
        for told in (True, False):
            old = sv.SaveGame.from_dict(copy.deepcopy(d), pool=r4.POOL, basics_free=told)
            self.assertTrue(old.collection.basics_free, told)
            self.assertEqual(old.to_dict()["version"], 2)
        d2 = dict(d, version=2)                                            # version 2 must say it
        with self.assertRaises(sv.SaveError):
            sv.SaveGame.from_dict(copy.deepcopy(d2), pool=r4.POOL)
        with self.assertRaises(sv.SaveError):
            sv.SaveGame.from_dict(dict(d2, basics_free="no"), pool=r4.POOL)

    def test_an_older_copy_of_the_game_refuses_a_version_2_save(self):
        d = paid_save().to_dict()
        with mock.patch.object(sv, "VERSION", 1), self.assertRaises(sv.SaveError) as cm:
            sv.SaveGame.from_dict(d, pool=r4.POOL)
        self.assertIn("newer Manticore", str(cm.exception))


# ---- the map --------------------------------------------------------------------------------------------------------------------

def gap_map(folder, gap=4, solid=False):
    """9 x 7 tiles: a border, and a wall across row 3 with a one-tile gap at column `gap` (none when solid)."""
    walls = {(c, 3) for c in range(9) if solid or c != gap}
    return area_of(folder, room(9, 7, walls), 9, 7)


class WallTests(Temp):
    def walk_up(self, a, x, y=None, seconds=1.5):
        w = explore.Walker(a, x, mid(4, 5)[1] if y is None else y)
        for _ in range(int(seconds * 60)):
            w.move(0, -1, 1 / 60, align=True)
        return w

    def test_corner_help_is_off(self):
        self.assertFalse(explore.CORNER_HELP)

    def test_beside_the_gap_the_walker_stops_dead(self):
        a = gap_map(self.tmp)
        gx = mid(4, 3)[0]
        for off in (-7.5, -6, -4, 4, 6, 7.5):
            with self.subTest(off=off):
                w = self.walk_up(a, gx + off, y=4 * TILE + 5)         # already against the wall: the grid never moves it
                self.assertEqual(w.y, 4 * TILE + w.hh)
                self.assertEqual(w.x, gx + off)

    def test_in_line_with_the_gap_it_goes_through(self):
        a = gap_map(self.tmp)
        gx = mid(4, 3)[0]
        for off in (-2, 0, 2):
            with self.subTest(off=off):
                w = self.walk_up(a, gx + off)
                self.assertLess(w.y, 3 * TILE)

    def test_walking_freely_still_drifts_onto_the_half_tile_grid(self):
        a = area_of(self.tmp, room(12, 8), 12, 8)
        w = explore.Walker(a, mid(3, 3)[0], mid(3, 3)[1] + 3)
        for _ in range(30):
            w.move(1, 0, 1 / 60, align=True)
        self.assertEqual(w.y, mid(3, 3)[1])

    def test_with_the_help_switched_back_on_it_slides_in(self):
        a = gap_map(self.tmp)
        gx = mid(4, 3)[0]
        with mock.patch.object(explore, "CORNER_HELP", True):
            w = self.walk_up(a, gx + 6, y=4 * TILE + 5)
        self.assertLess(w.y, 3 * TILE)

    def test_the_shipped_maps_can_still_be_walked_through(self):
        """tests/rpg_route walks the region and the cave with the grid (as a player lines up with a gap): RPG5/RPG6's walks."""
        from tests import rpg_route as route
        from tests.test_rpg5 import stand_in
        explore.forget_areas()
        s = stand_in()
        ex = explore.Explorer(s, start_town="town_1")
        s.world["beaten"] = [t.spot for t in ex.area.triggers if t.kind == "encounter"]
        route.walk_to_tile(ex, (15, 5))
        for _ in range(60):
            ex.step(1, 0, 1 / 60)
            if ex.sliding():
                break
        self.assertEqual(ex.room, (1, 0))


class SlideTests(unittest.TestCase):
    def test_the_slide_takes_three_tenths_of_a_second(self):
        self.assertEqual(explore.ROOM_SLIDE_SECONDS, 0.3)


class DarkTests(Temp):
    LIGHT = '<property name="light" type="float" value="4.5"/>'

    def area(self, props="", objects=""):
        explore.forget_areas()
        write_map(self.tmp, "m", 8, 6, [layer_xml("g", 8, 6, room())], objects,
                  map_props=f"<properties>{props}</properties>" if props else "")
        return explore.load_area("m", self.tmp)

    def test_light_on_a_map_that_is_not_a_dungeon_is_left_out_and_said(self):
        a = self.area(self.LIGHT, obj(1, "encounter", 32, 32, 16, 16, "Rat", {"kind": ("string", "rat")}))
        self.assertIsNone(a.light)
        self.assertFalse(a.dungeon)
        self.assertTrue(any("only dungeons are dark" in p for p in a.problems), a.problems)

    def test_a_dungeon_or_boss_encounter_makes_it_a_dungeon(self):
        for kind in ("dungeon", "boss"):
            with self.subTest(kind=kind):
                a = self.area(self.LIGHT, obj(1, "encounter", 32, 32, 16, 16, "Rat",
                                              {"kind": ("string", "rat"), "encounter": ("string", kind)}))
                self.assertEqual((a.dungeon, a.light), (True, 4.5))
                self.assertFalse(any("dungeons" in p for p in a.problems))

    def test_the_map_property_says_so_either_way(self):
        a = self.area('<property name="dungeon" type="bool" value="true"/>' + self.LIGHT)
        self.assertEqual((a.dungeon, a.light), (True, 4.5))
        a = self.area('<property name="dungeon" type="bool" value="false"/>' + self.LIGHT,
                      obj(1, "encounter", 32, 32, 16, 16, "Priest", {"kind": ("string", "p"), "encounter": ("string", "boss")}))
        self.assertEqual((a.dungeon, a.light), (False, None))

    def test_the_shipped_cave_is_dark_and_the_region_is_not(self):
        explore.forget_areas()
        cave, region = explore.load_area("sample_dungeon"), explore.load_area("sample_region")
        self.assertEqual((cave.dungeon, cave.light), (True, 4.5))
        self.assertEqual((region.dungeon, region.light), (False, None))
        self.assertEqual(explore.check_all(), {"sample_dungeon": [], "sample_region": []})


# ---- PyTMX ----------------------------------------------------------------------------------------------------------------------

def csv_layers(path):
    """{layer name: [Tiled gids, flags included]} straight from the file's CSV - an oracle that shares no code with PyTMX."""
    root = ET.parse(path).getroot()
    out = {}
    for layer in root.iter("layer"):
        data = layer.find("data")
        out[layer.get("name")] = [int(v) for v in next(csv.reader(io.StringIO(data.text.replace("\n", "")))) if v.strip()]
    return out


class PyTmxTests(Temp):
    def test_pytmx_is_installed_and_reads_the_maps(self):
        import pytmx
        self.assertEqual(pytmx.__version__, (3, 32))
        seen = []
        real = pytmx.TiledMap

        def spy(*a, **k):
            seen.append(a[0])
            return real(*a, **k)
        with mock.patch.object(pytmx, "TiledMap", spy):
            tmx.read_map(os.path.join(BASE, "rpg", "maps", "sample_region.tmx"))
        self.assertEqual(len(seen), 1)

    def test_the_shipped_maps_read_as_the_files_say(self):
        for name in ("sample_region", "sample_dungeon"):
            with self.subTest(name=name):
                path = os.path.join(BASE, "rpg", "maps", name + ".tmx")
                m = tmx.read_map(path)
                raw = csv_layers(path)
                self.assertEqual([l.name for l in m.layers], list(raw))
                for layer in m.layers:
                    self.assertEqual(layer.data, [g & tmx.GID_MASK for g in raw[layer.name]])
                    self.assertEqual(layer.flips, {i: g & ~tmx.GID_MASK for i, g in enumerate(raw[layer.name]) if g & ~tmx.GID_MASK})
                root = ET.parse(path).getroot()
                objs = [(int(o.get("id")), o.get("class") or o.get("type"), o.get("name", "")) for o in root.iter("object")]
                self.assertEqual([(o.id, o.kind, o.name) for o in m.objects], objs)
                tsx = ET.parse(os.path.join(BASE, "rpg", "maps", "placeholder_tiles.tsx")).getroot()
                solid = {int(t.get("id")) for t in tsx.iter("tile") for p in t.iter("property")
                         if p.get("name") == "solid" and p.get("value") == "true"}
                ts = m.tilesets[0]
                self.assertEqual({k for k, p in ts.props.items() if p.get("solid") is True}, solid)
                self.assertEqual((ts.tilewidth, ts.columns, ts.tilecount), (16, 8, 16))
                self.assertTrue(ts.source.endswith("placeholder_tiles.tsx"))

    def test_a_layer_inside_a_group_is_read(self):
        inner = layer_xml("collision", 4, 3, [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0])
        explore.forget_areas()
        write_map(self.tmp, "g", 4, 3, [layer_xml("ground", 4, 3, [1] * 12), f'<group name="walls">{inner}</group>'],
                  obj(1, "spawn", 8, 8, name="start"))
        a = explore.load_area("g", self.tmp)
        self.assertTrue(a.blocked(1, 1))                                  # RPG5's reader skipped groups
        self.assertFalse(a.blocked(2, 1))

    def test_a_tiles_collision_shapes_are_not_the_maps_objects(self):
        inside = ('<tileset firstgid="1" name="in" tilewidth="16" tileheight="16" tilecount="4" columns="2">'
                  '<image source="t.png" width="32" height="32"/>'
                  '<tile id="0"><objectgroup draworder="index" id="2"><object id="1" x="0" y="0" width="16" height="8"/>'
                  '</objectgroup></tile></tileset>')
        p = write_map(self.tmp, "c", 2, 1, [layer_xml("g", 2, 1, [1, 1])], obj(5, "spawn", 8, 8, name="start"), tileset=inside)
        m = tmx.read_map(p)
        self.assertEqual([(o.id, o.kind, o.name) for o in m.objects], [(5, "spawn", "start")])

    def test_a_polygon_is_its_bounding_box(self):
        poly = '<object id="7" name="area" class="town" x="32" y="16"><polygon points="0,0 16,0 16,24 -8,24"/></object>'
        p = write_map(self.tmp, "p", 4, 4, [layer_xml("g", 4, 4, [1] * 16)], poly)
        o = tmx.read_map(p).objects[0]
        self.assertEqual((o.x, o.y, o.width, o.height), (24.0, 16.0, 24.0, 24.0))

    # ---- what a review of the round found PyTMX doing differently from RPG5's reader (13 Oct), each red before its fix ----

    def test_an_image_layer_with_no_picture_is_left_out(self):
        p = write_map(self.tmp, "i", 2, 1, [layer_xml("g", 2, 1, [1, 1]), '<imagelayer id="9" name="sky"/>'])
        m = tmx.read_map(p)
        self.assertEqual([l.name for l in m.layers], ["g"])

    def test_an_empty_string_property_is_empty_not_none(self):
        props = '<properties><property name="name" value=""/></properties>'
        p = write_map(self.tmp, "e", 2, 1, [layer_xml("g", 2, 1, [1, 1])], obj(1, "exit", 0, 0, 16, 16, props={"map": ("string", "")}),
                      map_props=props)
        m = tmx.read_map(p)
        self.assertEqual((m.props["name"], m.objects[0].props["map"]), ("", ""))

    def test_a_tile_object_keeps_its_y_and_only_its_own_properties(self):
        tile_obj = ('<object id="3" name="statue" class="encounter" gid="2" x="16" y="32" width="16" height="16">'
                    '<properties><property name="kind" value="rat"/></properties></object>')
        p = write_map(self.tmp, "t", 4, 4, [layer_xml("g", 4, 4, [1] * 16)], tile_obj)
        o = tmx.read_map(p).objects[0]
        self.assertEqual((o.x, o.y, o.width, o.height), (16.0, 32.0, 16.0, 16.0))
        self.assertEqual(o.props, {"kind": "rat"})                        # not gid 2's "solid", nor PyTMX's id / width / frames

    def test_a_property_type_pytmx_doesnt_know_is_a_string(self):
        props = '<properties><property name="tags" type="list" value="a,b"/></properties>'
        m = tmx.read_map(write_map(self.tmp, "l", 2, 1, [layer_xml("g", 2, 1, [1, 1])], map_props=props))
        self.assertEqual(m.props["tags"], "a,b")

    def test_a_trailing_comma_in_csv_reads_as_rpg5_did(self):
        layer = '<layer name="g" width="2" height="1"><data encoding="csv">1,1,</data></layer>'
        m = tmx.read_map(write_map(self.tmp, "c", 2, 1, [layer]))
        self.assertEqual(m.layers[0].data, [1, 1])

    def test_an_error_is_not_blamed_on_a_property_that_did_not_cause_it(self):
        layer = '<layer name="g" width="2" height="1"><data encoding="csv">1,x</data></layer>'
        bad = obj(1, "encounter", 0, 0, 16, 16, "Rat", {"once": ("bool", "maybe")})
        with self.assertRaises(tmx.TmxError) as cm:
            tmx.read_map(write_map(self.tmp, "b", 2, 1, [layer], bad))
        self.assertNotIn("once", str(cm.exception))
        self.assertIn("'x'", str(cm.exception))

    def test_a_bad_property_in_the_tileset_file_is_named(self):
        p = write_map(self.tmp, "s", 2, 1, [layer_xml("g", 2, 1, [1, 1])])
        with open(os.path.join(self.tmp, "t.tsx"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tileset name="t" tilewidth="16" tileheight="16" tilecount="4" '
                    'columns="2"><image source="t.png" width="32" height="32"/><tile id="1"><properties>'
                    '<property name="solid" type="bool" value="maybe"/></properties></tile></tileset>')
        with self.assertRaises(tmx.TmxError) as cm:
            tmx.read_map(p)
        self.assertIn("t.tsx: property 'solid' of type bool has the value 'maybe'", str(cm.exception))

    def test_without_pytmx_it_says_what_to_run(self):
        p = write_map(self.tmp, "x", 2, 1, [layer_xml("g", 2, 1, [1, 1])])
        with mock.patch.object(tmx, "pytmx", None), self.assertRaises(tmx.TmxError) as cm:
            tmx.read_map(p)
        self.assertIn("pip install -r requirements.txt", str(cm.exception))

    def test_requirements_and_the_licence(self):
        with open(os.path.join(BASE, "requirements.txt"), encoding="utf-8") as f:
            self.assertIn("PyTMX==3.32", f.read().split())
        with open(os.path.join(BASE, "licenses", "NOTICES.json"), encoding="utf-8") as f:
            entry = next(c for c in json.load(f)["components"] if c["id"] == "pytmx")
        self.assertEqual((entry["version"], entry["license"], entry["status"]), ("3.32", "LGPL-3.0-or-later", "verified"))
        with open(os.path.join(BASE, "licenses", "texts", "LGPL-3.0-or-later.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 29 June 2007", text)
        with open(os.path.join(BASE, "THIRD_PARTY_NOTICES.txt"), encoding="utf-8") as f:
            self.assertIn("PyTMX", f.read())


if __name__ == "__main__":
    unittest.main()
