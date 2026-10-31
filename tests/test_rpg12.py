# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG12 (the Adventure remaster's third round, claude/SPEC_RPG_SLICE_2026-10-23.md): the slice's three maps - Lantern
Hollow, the Ashfen outskirts and the Warrens (two floors) - built from text layouts (rpg/maps/src/) by tools/rpg_make_maps.py with
DawnLike's pieces.

  ToolTests        the pieces the tool picks; a layout's mistakes are said; the maps in rpg/maps are the layouts' build
  PairTests        the water shimmers (DawnLike's Pit0/Pit1 pair)
  MapTests         every map loads and checks clean against the Adventure's data; exits, spawns, foes, NPCs, Platino
  WalkTests        a new game walks (one key at a time) from the card shop to the Grave Priest, through every exit
  TownTests        a counter opens the town on its tab and you leave it where you came in
  OldSaveTests     a save from before these maps goes on inside Lantern Hollow's inn (the spec's "old saves")
  DrawTests        the maps draw at the window's sizes
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from rpg import clock, conditions, explore, tmx
from rpg import rare as rare_mod
from tests import rpg_route as route

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "rpg", "maps", "src")
TOOL = os.path.join(BASE, "tools", "rpg_make_maps.py")
MAPS = ("lantern_hollow", "ashfen_outskirts", "warrens_1", "warrens_2")
DATA = os.path.join(BASE, "rpg", "data")


def make_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rpg_make_maps", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def data_ids():
    with open(os.path.join(DATA, "enemy_kinds.json"), encoding="utf-8") as f:
        kinds = {k["id"] for k in json.load(f)["kinds"]}
    with open(os.path.join(DATA, "towns.json"), encoding="utf-8") as f:
        towns = {t["id"] for t in json.load(f)["towns"]}
    return kinds, towns


def new_world():
    """A save-shaped stand-in where a new game starts: outside town_1 (rpg/data/towns.json's start town)."""
    return types.SimpleNamespace(world={"region": 1, "map": "town_1", "x": None, "y": None, "last_town": "town_1",
                                        "towns_visited": ["town_1"], "cleared": [], "beaten": []})


# ---- the tool -----------------------------------------------------------------------------------------------------------------

class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = make_tool()

    def test_floor_pieces(self):
        fp = self.t.floor_piece
        self.assertEqual(fp(1, 1, 1, 1), (1, 1))                      # the middle
        self.assertEqual(fp(0, 1, 1, 0), (0, 0))                      # top-left corner
        self.assertEqual(fp(0, 1, 0, 1), (2, 0))
        self.assertEqual(fp(1, 0, 1, 0), (0, 2))
        self.assertEqual(fp(1, 0, 0, 1), (2, 2))
        self.assertEqual(fp(0, 1, 1, 1), (1, 0))                      # top edge
        self.assertEqual(fp(1, 0, 1, 1), (1, 2))
        self.assertEqual(fp(1, 1, 0, 1), (2, 1))
        self.assertEqual(fp(1, 1, 1, 0), (0, 1))
        self.assertEqual(fp(0, 0, 0, 0), (5, 0))                      # a single tile
        self.assertEqual(fp(0, 0, 1, 0), (4, 1))                      # the horizontal strip: left end, middle, right end
        self.assertEqual(fp(0, 0, 1, 1), (5, 1))
        self.assertEqual(fp(0, 0, 0, 1), (6, 1))
        self.assertEqual(fp(0, 1, 0, 0), (3, 0))                      # the vertical strip
        self.assertEqual(fp(1, 1, 0, 0), (3, 1))
        self.assertEqual(fp(1, 0, 0, 0), (3, 2))

    def test_every_way_a_wall_can_join_has_a_piece_as_the_sheets_legend_draws_it(self):
        w = self.t.WALL_PIECES
        self.assertEqual(len(w), 16)
        self.assertEqual(w[(0, 1, 1, 0)], (0, 0))                     # "┌" at the legend's top-left
        self.assertEqual(w[(1, 1, 1, 1)], (4, 1))                     # "┼"
        self.assertEqual(w[(1, 0, 1, 1)], (4, 2))                     # "┴"
        self.assertEqual(w[(1, 1, 0, 0)], (0, 1))                     # "│"
        self.assertEqual(w[(0, 0, 1, 1)], (1, 0))                     # "─"

    def write(self, folder, name, text):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name + ".txt"), "w", encoding="utf-8") as f:
            f.write(text)

    def build(self, text):
        tmp = tempfile.mkdtemp(prefix="rpg12_")
        self.addCleanup(shutil.rmtree, tmp, True)
        src = os.path.join(tmp, "src")
        self.write(src, "m", text)
        out = os.path.join(tmp, "out")
        return self.t.make(out=out, src=src, relative_to=out)

    def test_a_layouts_mistakes_are_said(self):
        good = "name: M\n[markers]\n1 = spawn start on .\n[map]\n...\n.1.\n...\n"
        self.assertTrue(self.build(good))
        for text, words in (("name: M\n[map]\n...\n..\n", "row 1 is 2 wide"),
                            ("name: M\n[map]\n...\n.Q.\n", "'Q' is neither in the legend nor a marker"),
                            ("name: M\n[markers]\n1 = spawn start\n[map]\n.1.\n", "say what the marker stands on"),
                            ("name: M\n[markers]\n1 = gate start on .\n[map]\n.1.\n", "a marker line is"),
                            ("name: M\n[markers]\nA = npc name=Bo on .\n[map]\n.A.\n", "needs cell=column,row"),
                            ("name: M\n[legend]\nq = floor_nowhere\n[map]\n...\n", "a known terrain"),
                            ("no colon here\n[map]\n...\n", "expected 'key: value'")):
            with self.subTest(words=words):
                with self.assertRaises(self.t.LayoutError) as cm:
                    self.build(text)
                self.assertIn(words, str(cm.exception))

    def test_a_marker_group_is_one_object_and_the_rock_inside_a_wall_mass_is_dark(self):
        files = self.build("name: M\n[markers]\nX = exit map=m spawn=start on .\n1 = spawn start on .\n[map]\n"
                           "%%%%%\n%%%%%\n%%%%%\n.....\n.1XX.\n.....\n")
        path = next(p for p in files if p.endswith("m.tmx"))
        m = tmx.read_map(path)
        exits = [o for o in m.objects if o.kind == "exit"]
        self.assertEqual(len(exits), 1)
        self.assertEqual((exits[0].x, exits[0].y, exits[0].width, exits[0].height), (32, 64, 32, 16))
        t = self.t
        sheets = t.Sheets()
        walls = next(ly for ly in m.layers if ly.name == "walls").data
        void = sheets.gid("wall", *t.VOID)
        self.assertEqual(walls[1 * 5 + 2], void)                     # row 1 has walls all round: rock
        self.assertNotEqual(walls[2 * 5 + 2], void)                  # row 2 faces the floor: a wall piece
        self.assertTrue(walls[2 * 5 + 2])

    def test_the_maps_in_rpg_maps_are_the_layouts_build(self):
        r = subprocess.run([sys.executable, TOOL, "--check"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("up to date", r.stdout)

    def test_the_tilesets_point_at_dawnlikes_pictures_and_only_real_pairs_animate(self):
        t = self.t
        for name, (image, pair) in t.SHEETS.items():
            with self.subTest(name=name):
                root = __import__("xml.etree.ElementTree", fromlist=["ET"]).parse(
                    os.path.join(BASE, "rpg", "maps", f"dl_{name}.tsx")).getroot()
                src = os.path.normpath(os.path.join(BASE, "rpg", "maps", root.find("image").get("source")))
                self.assertEqual(src, os.path.normpath(os.path.join(BASE, "assets", "rpg", "dawnlike", image)))
                self.assertTrue(os.path.isfile(src))
                props = {p.get("name"): p.get("value") for p in root.iter("property")}
                self.assertEqual("frame2" in props, pair is not None)
        self.assertTrue(t.SHEETS["pit"][1].endswith("Pit1.png"))      # the water shimmers (Pit1 moves its edges)
        self.assertTrue(t.SHEETS["door"][0].endswith("Door1.png"))    # Door1 = the open doors, not a second frame


# ---- the maps -----------------------------------------------------------------------------------------------------------------

def tile_pixels(path, col, row):
    import pygame
    pic = pygame.image.load(path)
    return pygame.image.tobytes(pic.subsurface((col * 16, row * 16, 16, 16)).copy(), "RGBA")


class PairTests(unittest.TestCase):
    def test_the_water_on_the_maps_moves_and_the_floors_do_not(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        art = os.path.join(BASE, "assets", "rpg", "dawnlike", "Objects")
        t = make_tool()
        moving = 0
        for name in ("water_river", "water_bog"):
            c0, r0 = t.TERRAINS[name]["set"]
            for dc in range(3):
                if tile_pixels(os.path.join(art, "Pit0.png"), c0 + dc, r0 + 1) != tile_pixels(os.path.join(art, "Pit1.png"),
                                                                                                c0 + dc, r0 + 1):
                    moving += 1
        self.assertGreater(moving, 0)                                   # the cellar's and the river's water shimmer
        a = explore.load_area("warrens_1")
        water = next(ly for ly in a.map.layers if ly.name == "water")
        pit = next(ts for ts in a.map.tilesets if ts.name == "dl_pit")
        self.assertTrue(any(g and pit.firstgid <= g < pit.firstgid + pit.tilecount for g in water.data))
        self.assertIsNotNone(pit.image2)


class MapTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_the_four_maps_load_and_check_clean_against_the_data(self):
        self.assertEqual(sorted(explore.map_names()), sorted(MAPS))
        kinds, towns = data_ids()
        problems = explore.check_all(kinds=kinds, towns=towns)
        self.assertEqual(problems, {m: [] for m in MAPS})

    def test_the_sizes_and_properties(self):
        sizes = {"lantern_hollow": (40, 30), "ashfen_outskirts": (60, 40), "warrens_1": (40, 30), "warrens_2": (40, 30)}
        for name, size in sizes.items():
            with self.subTest(name=name):
                a = explore.load_area(name)
                self.assertEqual((a.cols, a.rows), size)
                self.assertEqual(a.region, 1)
                self.assertEqual(a.dungeon, name.startswith("warrens"))
                self.assertEqual(a.light, {"lantern_hollow": 3.0, "ashfen_outskirts": 3.5}.get(name, 4.5))   # RPG15: a lantern
                self.assertEqual(a.darkness, {"lantern_hollow": 0.45, "ashfen_outskirts": 0.5}.get(name, 0.85))  # everywhere
                self.assertIn(a.map.props.get("backdrop"), ("forest", "dungeon"))

    def test_the_foes(self):
        out = explore.load_area("ashfen_outskirts")
        allfoes = [t for t in out.triggers if t.kind == "encounter"]
        foes = [t for t in allfoes if not t.rare and not any(c.startswith("phase:") for c in t.when)]  # Round RPG16: by day
        self.assertEqual(sorted({t.enemy for t in foes}), ["ember_bandit", "grave_robber", "thornback", "zealot"])
        self.assertTrue(all(t.encounter == "regular" and not t.once for t in foes))
        # Round RPG16 (expected change): night foes and rares - the night's robbers are regular fights, the rats and the rares
        # dungeon fights that come back (never once)
        night = [t for t in allfoes if t not in foes]
        self.assertEqual(sorted((t.enemy, t.encounter, t.once, bool(t.rare)) for t in night),
                         [("ember_bandit", "dungeon", False, True), ("grave_robber", "regular", False, False),
                          ("grave_robber", "regular", False, False), ("plague_rat", "dungeon", False, False),
                          ("plague_rat", "dungeon", False, False), ("plague_rat", "dungeon", False, True),
                          ("thornback", "dungeon", False, True)])
        self.assertFalse(out.dungeon)                                  # night rats don't make the moor a dungeon
        self.assertGreaterEqual(sum(t.enemy == "ember_bandit" for t in foes), 3)      # the Lamplighter's Debt needs three
        deep = [t for n in ("warrens_1", "warrens_2") for t in explore.load_area(n).triggers if t.kind == "encounter"]
        self.assertEqual([t.enemy for t in deep if t.encounter == "boss"], ["grave_priest"])
        # Round RPG13: the Warrens' rats are random fights now (map properties encounter_rate / encounters), not visible foes
        self.assertEqual({t.enemy for t in deep if t.encounter != "boss"}, set())
        self.assertEqual({k for n in ("warrens_1", "warrens_2") for k, _w in explore.load_area(n).fight_table},
                         {"plague_rat", "grave_robber"})
        self.assertTrue(all(t.once for t in deep))
        boss = next(t for t in deep if t.encounter == "boss")
        self.assertFalse(boss.wander)
        for name in MAPS:
            a = explore.load_area(name)
            for t in a.triggers:
                if t.kind == "encounter":
                    f = explore.Foe(a, t)
                    self.assertTrue(f._free(*f.home), (name, t))             # nobody stands in a wall

    def test_every_exit_leads_to_a_spawn_and_back(self):
        links = {}
        for name in MAPS:
            for t in explore.load_area(name).triggers:
                if t.kind == "exit":
                    target = explore.load_area(t.to_map)
                    self.assertIn(t.to_spawn, target.spawns, (name, t))
                    links.setdefault(name, set()).add(t.to_map)
        one_way = {("warrens_2", "lantern_hollow")}      # Round RPG18: the stair up from the Grave Priest's hall, one way on purpose
        for a, bs in links.items():
            for b in bs:
                if (a, b) in one_way:
                    self.assertNotIn(a, links.get(b, set()), f"{b} -> {a} should not exist (the way up is one way)")
                    continue
                self.assertIn(a, links.get(b, set()), f"{a} -> {b} has no way back")
        self.assertEqual(links, {"lantern_hollow": {"ashfen_outskirts"}, "ashfen_outskirts": {"lantern_hollow", "warrens_1"},
                                 "warrens_1": {"ashfen_outskirts", "warrens_2"}, "warrens_2": {"warrens_1", "lantern_hollow"}})

    def test_the_towns_on_the_maps(self):
        _kinds, towns = data_ids()
        on_maps = {t.town for n in MAPS for t in explore.load_area(n).triggers if t.kind == "town"}
        self.assertEqual(on_maps, towns)                                 # every town of the data has a door somewhere
        self.assertEqual(explore.town_map("town_1"), "lantern_hollow")
        hollow = explore.load_area("lantern_hollow")
        counters = sorted((t.tab, t.back) for t in hollow.triggers if t.kind == "town")
        self.assertEqual(counters, [("packs", "town_1_packs"), ("singles", "town_1")])

    def test_lantern_hollows_people_and_platino(self):
        a = explore.load_area("lantern_hollow")
        npcs = [o for o in a.map.objects if o.kind == "npc"]
        always = [o for o in npcs if not o.props.get("when")]           # Round RPG16: people who come and go have `when`
        self.assertTrue(4 <= len(always) <= 8, len(always))
        self.assertEqual(len({(o.props.get("id"), o.props.get("when")) for o in npcs}), len(npcs))
        people = next(ly for ly in a.map.layers if ly.name == "people")
        hum = next(ts for ts in a.map.tilesets if ts.name == "dl_humanoid")
        for o in npcs:
            c, r = (int(v) for v in o.props["cell"].split(","))
            col, row = int(o.x // 16), int(o.y // 16)
            if o.props.get("when"):                                      # drawn by the view while there, not on the layer
                self.assertEqual(people.data[row * a.cols + col], 0, o.name)
                self.assertFalse(a.blocked(col, row), o.name)
                continue
            self.assertEqual(people.data[row * a.cols + col], hum.firstgid + r * hum.columns + c, o.name)
            self.assertTrue(a.blocked(col, row), o.name)                  # you can't walk through people
        rep = next(ts for ts in a.map.tilesets if ts.name == "dl_reptile")
        platino = rep.firstgid + 12 * rep.columns + 3                     # DawnLike's README: Reptile0/1.png, x 48, y 192
        found = [i for n in MAPS for ly in explore.load_area(n).map.layers for i, g in enumerate(ly.data) if g == platino]
        self.assertEqual(len(found), 1)
        self.assertTrue(people.data[found[0]] == platino)
        self.assertIsNotNone(rep.image2)                                  # he blinks too (Reptile1.png)

    def test_the_layouts_are_the_source(self):
        for name in MAPS:
            with self.subTest(name=name):
                self.assertTrue(os.path.isfile(os.path.join(SRC, name + ".txt")))
        fixture = os.path.join(BASE, "tests", "fixtures", "rpg_maps")
        self.assertEqual(sorted(f for f in os.listdir(fixture) if f.endswith(".tmx")), ["sample_dungeon.tmx", "sample_region.tmx"])
        self.assertFalse(os.path.exists(os.path.join(BASE, "rpg", "maps", "sample_region.tmx")))


# ---- walking the whole slice --------------------------------------------------------------------------------------------------

def tile_of(area, t):
    x0, y0, x1, y1 = t.box
    return area.tile_at((x0 + x1) / 2 - 0.01, (y0 + y1) / 2 - 0.01)


class WalkTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def goal(self, ex, kind, **match):
        for t in ex.area.triggers:
            if t.kind == kind and all(getattr(t, k) == v for k, v in match.items()):
                return t
        self.fail(f"no {kind} {match} on {ex.area.name}")

    def walk(self, ex, t):
        tile = tile_of(ex.area, t)
        events, frames = route.walk_to_tile(ex, tile, limit=20000, stop_at_event=True)
        return events

    def test_from_the_card_shop_to_the_grave_priest_through_every_exit(self):
        s = new_world()
        ex = explore.Explorer(s)
        self.assertEqual(ex.area.name, "lantern_hollow")
        self.assertEqual(ex.area.tile_at(ex.walker.x, ex.walker.y), (31, 8))       # in front of the card shop's counter
        s.world["beaten"] = [t.spot for n in MAPS for t in explore.load_area(n).triggers if t.kind == "encounter"
                             and t.encounter != "boss"]
        s.world["danger"] = 10 ** 6            # Round RPG13: no random fight on this walk (they are tested in test_rpg13)
        # the pack shop's counter, then out of the gate (Round RPG15b, expected change: talked across, its keeper opens it)
        packs = self.goal(ex, "town", tab="packs")
        c, r = tile_of(ex.area, packs)
        self.assertEqual(route.walk_to_tile(ex, (c, r - 1))[0], [])
        ex.walker.facing = "down"
        self.assertIs(ex.facing_shop(), packs)
        ex.came_from = packs
        ex.leave_town("town_1")
        self.assertEqual(ex.area.tile_at(ex.walker.x, ex.walker.y), (7, 20))       # the pack shop's own spawn
        legs = [("exit", {"to_map": "ashfen_outskirts"}, "ashfen_outskirts"), ("exit", {"to_map": "warrens_1"}, "warrens_1"),
                ("exit", {"to_map": "warrens_2"}, "warrens_2")]
        for kind, match, arrives in legs:
            events = self.walk(ex, self.goal(ex, kind, **match))
            self.assertEqual(events, [("map", arrives)])
            self.assertEqual(ex.area.name, arrives)
        boss = self.goal(ex, "encounter", encounter="boss")
        events = route.walk_to_tile(ex, lambda: tile_of(ex.area, boss), limit=20000)[0]
        self.assertEqual([(k, w.enemy) for k, w in events], [("encounter", "grave_priest")])
        # and all the way back up to Lantern Hollow
        s.world["cleared"] = [boss.spot]
        for match, arrives in (({"to_map": "warrens_1"}, "warrens_1"), ({"to_map": "ashfen_outskirts"}, "ashfen_outskirts"),
                               ({"to_map": "lantern_hollow"}, "lantern_hollow")):
            self.assertEqual(self.walk(ex, self.goal(ex, "exit", **match)), [("map", arrives)])

    def test_every_foe_and_town_on_the_outskirts_can_be_walked_to(self):
        s = new_world()
        s.world["map"] = "ashfen_outskirts"
        ex = explore.Explorer(s)
        targets = [t for t in ex.area.triggers if t.kind in ("encounter", "town")]
        for t in targets:
            with self.subTest(t=t):
                explore.forget_areas()
                s2 = new_world()
                s2.world["map"] = "ashfen_outskirts"
                ex2 = explore.Explorer(s2)
                others = [o.spot for o in ex2.area.triggers if o.kind == "encounter" and o.spot != t.spot]
                s2.world["beaten"] = others
                if t.when or t.rare:                       # Round RPG16: a night foe or a rare, walked to at an hour it is out
                    # Round RPG18: Pell's bandits are out only while his quest isn't ready or done
                    debt = "active" if t.enemy == "ember_bandit" and not t.rare else "done"   # the Red Sash: done
                    s2.world.update(flags=["warrens_entered"], quests={"lamplighters_debt": {"state": debt}})
                    s2.world["clock"] = next(clock.DAY + h * 60 for h in (22, 19, 6)
                                             if conditions.all_hold(t.when, s2.world, minutes=clock.DAY + h * 60))
                    s2.stable_rng = lambda *parts: None                      # a save's rolls (the patched roll answers)
                    self.enterContext(mock.patch.object(rare_mod, "roll", return_value=0.0))   # every rare comes out
                    ex2.refresh(force=True)
                foe = ex2.foes.get(t.spot)
                goal = (lambda: ex2.area.tile_at(foe.x, foe.y)) if foe else tile_of(ex2.area, t)
                events = route.walk_to_tile(ex2, goal, limit=20000)[0]
                self.assertEqual(len(events), 1, events)
                self.assertEqual(events[0][0], "encounter" if t.kind == "encounter" else "town")


# ---- the town's counters ------------------------------------------------------------------------------------------------------

class TownTests(unittest.TestCase):
    def test_a_counter_opens_its_tab_and_you_leave_where_you_came_in(self):
        from tests import test_rpg8 as r8
        base = r8.Base("runTest")
        base.setUp()
        self.addCleanup(base.doCleanups)
        r8.new_game(None)
        g = base.gui()
        f = base.open(g, slot=1)
        if f.stage != "map":
            base.press(g, "leave_town")
        self.assertEqual((f.stage, f.explorer.area.name), ("map", "lantern_hollow"))
        # Round RPG15b (expected change): a counter's shop opens when you talk to its keeper across it, not by stepping on it
        packs = next(t for t in f.explorer.area.triggers if t.kind == "town" and t.tab == "packs")
        self.talk_across(base, g, f, packs)
        self.assertEqual((f.stage, f.tab), ("town", "packs"))
        base.press(g, "leave_town")
        self.assertEqual(f.explorer.area.tile_at(f.explorer.walker.x, f.explorer.walker.y), (7, 20))
        singles = next(t for t in f.explorer.area.triggers if t.kind == "town" and t.tab == "singles")
        self.talk_across(base, g, f, singles)
        self.assertEqual((f.stage, f.tab), ("town", "singles"))
        base.close(g)

    def talk_across(self, base, g, f, shop):
        """Walk to the tile across the shop's counter from its keeper, face them and talk until the box closes."""
        import pygame
        from tests.test_forge_table import key
        a = f.explorer.area
        c, r = tile_of(a, shop)
        for dx, dy, facing in ((0, 1, "up"), (0, -1, "down"), (1, 0, "left"), (-1, 0, "right")):
            if a.npc_at(c - dx, r - dy) and not a.blocked(c + dx, r + dy):
                base.walk_to(g, (c + dx, r + dy))
                f.explorer.walker.facing = facing
                break
        else:
            self.fail(f"nobody behind the counter at {(c, r)}")
        self.assertIs(f.explorer.facing_shop(), shop)
        key(g, pygame.K_RETURN)
        for _ in range(20):
            if f.sub != "talk":
                break
            key(g, pygame.K_RETURN)


# ---- a save from before these maps ------------------------------------------------------------------------------------------

def old_world():
    """An RPG11 save's world: on the placeholder region, with foes beaten there."""
    return {"region": 1, "map": "sample_region", "x": 400, "y": 300, "last_town": "town_2", "towns_visited": ["town_1", "town_2"],
            "cleared": ["sample_region:3"], "beaten": ["sample_dungeon:1"], "facing": "left", "hero": "Mage",
            "in_town": "town_2"}


class OldSaveTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_a_save_whose_map_is_gone_goes_on_inside_lantern_hollows_inn(self):
        w = old_world()
        note = explore.move_old_save(w, "town_1")
        self.assertEqual(note, explore.OLD_SAVE_NOTE)
        inn = explore.load_area("lantern_hollow").spawn(explore.INN_SPAWN)
        self.assertEqual((w["map"], w["x"], w["y"]), ("lantern_hollow", inn[0], inn[1]))
        self.assertEqual((w["cleared"], w["beaten"]), ([], []))
        self.assertNotIn("in_town", w)
        for k in ("region", "last_town", "towns_visited", "facing", "hero"):
            self.assertEqual(w[k], old_world()[k], k)
        ex = explore.Explorer(types.SimpleNamespace(world=w), start_town="town_1")
        self.assertEqual((ex.area.name, ex.area.tile_at(ex.walker.x, ex.walker.y)), ("lantern_hollow", (8, 8)))
        self.assertIsNone(explore.move_old_save(w, "town_1"))          # once moved, it stays

    def test_a_save_on_these_maps_or_in_a_town_or_new_is_left_alone(self):
        for where in ("ashfen_outskirts", "warrens_2", "town_3", None):
            with self.subTest(where=where):
                w = old_world()
                w["map"] = where
                self.assertIsNone(explore.move_old_save(w, "town_1"))
                self.assertEqual(w, dict(old_world(), map=where))

    def test_opening_an_rpg11_save_says_so_writes_it_and_walks_from_the_inn(self):
        from rpg import save as sv
        from tests import test_rpg8 as r8
        base = r8.Base("runTest")
        base.setUp()
        self.addCleanup(base.doCleanups)
        game = r8.new_game(None)
        before = (json.dumps(game.save.collection.to_dict(), sort_keys=True), game.save.gold,
                  json.dumps(game.save.decks, sort_keys=True, default=str))
        game.save.world.update(old_world())
        game.write()
        g = base.gui()
        f = base.open(g, slot=1)
        self.assertEqual((f.stage, f.explorer.area.name), ("map", "lantern_hollow"))
        self.assertEqual(f.explorer.area.tile_at(f.explorer.walker.x, f.explorer.walker.y), (8, 8))
        self.assertEqual(f.result[0], "Welcome back")
        self.assertIn(explore.OLD_SAVE_NOTE, f.result[1])
        disk = r8.on_disk(None)
        self.assertEqual((disk["world"]["map"], disk["world"]["cleared"], disk["world"]["beaten"]), ("lantern_hollow", [], []))
        after = sv.read(1, None, pool=f.game.pool)[0]
        self.assertEqual((json.dumps(after.collection.to_dict(), sort_keys=True), after.gold,
                          json.dumps(after.decks, sort_keys=True, default=str)), before)
        base.close(g)


# ---- drawing ------------------------------------------------------------------------------------------------------------------

class DrawTests(unittest.TestCase):
    def test_every_map_draws_at_the_windows_sizes(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        import gfx
        import rpg_map_view
        pygame.init()
        if pygame.display.get_surface() is None:
            pygame.display.set_mode((64, 64))
        for name in MAPS:
            explore.forget_areas()
            a = explore.load_area(name)
            view = rpg_map_view.MapView(a)
            spawn = a.spawn()
            walker = explore.Walker(a, *spawn)
            for size in ((1920, 1080), (1366, 768), (4096, 2160)):
                with self.subTest(name=name, size=size):
                    screen = pygame.Surface(size)
                    cam = explore.Camera(*size, explore.Camera.zoom_for(size[1], a.th))
                    cam.follow(walker.x, walker.y, a.width, a.height)
                    view.draw(screen, cam, walker, [explore.Foe(a, t) for t in a.triggers if t.kind == "encounter"], 0.0)
                    near = cam.to_screen(walker.x + 8, walker.y)
                    self.assertNotEqual(tuple(screen.get_at(near))[:3], gfx.BACKDROP)


if __name__ == "__main__":
    unittest.main()
