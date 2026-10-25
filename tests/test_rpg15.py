# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG15: the Adventure's light, its clock and its music (claude/SPEC_RPG_SLICE_2026-10-23.md, RPG15; Karl 23 Oct 2026:
"Town and overworld should have day night cycles", and clock "A" - time passes while you walk, a day is about 24 minutes, the inn
skips to morning).

  ClockTests          rpg.clock on its own: the phases, the text, the morning, the darkness eased between its keys
  AreaTests           the map properties (darkness, lantern, outdoor, night_darkness, music, ambience) and light objects
  ShippedMapTests     the four maps: their levels, every brazier and candle a light, the hall's only once the Priest is beaten
  LightPixelTests     the darkness drawn: dark away from a light, lit near one, the hall's candles after the flag, the flicker
  ScreenTests         walking moves the clock and standing doesn't, the HUD's time, the inn's morning, Light on / off and saved,
                      the encounter screen dark round the foe, the music each screen asks for
  CueTests            every cue a map or screen names exists in sounds/cues.json with its files; the "loop:<cue>" music mode
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import pygame

import audio
import forge_table as ft
import rpg_encounter as renc
import rpg_map_view
import rpg_screens as rs
from rpg import clock, encounters, explore, talk
from tests import test_au1 as au1
from tests import test_rpg8 as r8
from tests import test_rpg14 as r14
from tests.test_forge_table import frame, key, make_gui
from tests.test_rpg5 import Temp, layer_xml, obj, room, write_map
from tests.test_rpg12 import make_tool
from tests.test_round24 import beat

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "rpg", "maps", "src")
SOUNDS = os.path.join(BASE, "sounds")
TOWN, OUT = "lantern_hollow", "ashfen_outskirts"
MAPS = (TOWN, OUT, "warrens_1", "warrens_2")
H = 60


def at(day, hh, mm=0):
    """Game minutes at day `day` (1 = the first), hh:mm."""
    return (day - 1) * clock.DAY + hh * H + mm


def manifest():
    with open(os.path.join(SOUNDS, "cues.json"), encoding="utf-8") as f:
        return json.load(f)["cues"]


# ---- the clock ------------------------------------------------------------------------------------------------------------------

class ClockTests(unittest.TestCase):
    def test_a_game_from_before_rpg15_starts_at_seven_in_the_morning(self):
        """Round RPG17 (expected change, Karl 24 Oct Q2 "Start at 07:00"): was 17:00."""
        for bad in ({}, {"clock": None}, {"clock": "noon"}, {"clock": True}, {"clock": -5}):
            with self.subTest(world=bad):
                self.assertEqual(clock.now(bad), at(1, 7))
        self.assertEqual(clock.now({"clock": 100}), 100.0)

    def test_a_second_walked_is_a_game_minute_and_a_day_is_24_minutes(self):
        w = {}
        self.assertEqual(clock.advance(w, 60), at(1, 8))              # Round RPG17 (expected change): a new game is at 07:00
        self.assertEqual(clock.advance(w, -10), at(1, 8))             # time never runs back
        self.assertEqual(clock.advance(w, 24 * 60), at(2, 8))         # 24 minutes of walking: the same hour the next day

    def test_the_phases_change_on_the_hour(self):
        want = {(0, 0): "night", (4, 59): "night", (5, 0): "dawn", (6, 59): "dawn", (7, 0): "day", (17, 59): "day",
                (18, 0): "dusk", (19, 59): "dusk", (20, 0): "night", (23, 59): "night"}
        for (hh, mm), name in want.items():
            with self.subTest(time=f"{hh}:{mm}"):
                self.assertEqual(clock.phase(at(1, hh, mm)), name)
                self.assertEqual(clock.phase(at(3, hh, mm)), name)

    def test_the_clock_text_and_the_day(self):
        self.assertEqual(clock.clock_text(at(1, 18, 20)), "18:20")
        self.assertEqual(clock.clock_text(at(2, 0, 5)), "00:05")
        self.assertEqual((clock.day_number(at(1, 23, 59)), clock.day_number(at(2, 0))), (1, 2))
        self.assertEqual(rs.time_text({"clock": at(1, 18, 20)}), "Day 1, Dusk 18:20")   # Round RPG17 (expected change): the day
        self.assertEqual(rs.time_text({}), "Day 1, Day 07:00")                         # ... and a new game at 07:00

    def test_sleeping_wakes_you_at_seven_the_next_morning_or_this_one(self):
        for now, wake in ((at(1, 17), at(2, 7)), (at(2, 3), at(2, 7)), (at(2, 7), at(3, 7)), (at(2, 6, 59), at(2, 7))):
            with self.subTest(now=clock.clock_text(now)):
                w = {"clock": now}
                self.assertEqual(clock.to_morning(w), wake)
                self.assertEqual(w["clock"], wake)

    def test_the_darkness_by_the_clock(self):
        dusk, night = 0.45, 0.7
        self.assertEqual(clock.level(at(1, 12), dusk, night), 0)
        self.assertEqual(clock.level(at(1, 18, 30), dusk, night), dusk)
        self.assertEqual(clock.level(at(1, 23), dusk, night), night)
        self.assertEqual(clock.level(at(2, 3), dusk, night), night)
        self.assertEqual(clock.level(at(1, 6), dusk, night), dusk * clock.DAWN_SHARE)
        self.assertEqual(clock.level(at(1, 18), dusk, night), round(dusk / 2, 4))        # half way, eased: still half

    def test_the_darkness_changes_gently_and_never_jumps(self):
        dusk, night = 0.45, 0.7
        prev = clock.level(0, dusk, night)
        for m in range(1, clock.DAY + 1):
            v = clock.level(m, dusk, night)
            self.assertLessEqual(abs(v - prev), 0.012, f"a jump at {clock.clock_text(m)}")
            prev = v
        # eased: a quarter of the way into the evening's change is darker by less than a quarter of it
        quarter = clock.level(at(1, 17, 45), dusk, night)
        self.assertGreater(quarter, 0)
        self.assertLess(quarter, dusk * 0.25)
        evening = [clock.level(at(1, 17, 30) + m, dusk, night) for m in range(0, 151)]
        self.assertEqual(evening, sorted(evening))                                      # only ever darker towards night
        morning = [clock.level(at(1, 5) + m, dusk, night) for m in range(0, 151)]
        self.assertEqual(morning, sorted(morning, reverse=True))


# ---- the map properties ---------------------------------------------------------------------------------------------------------

def prop(name, kind, value):
    return f'<property name="{name}" type="{kind}" value="{value}"/>'


class AreaTests(Temp):
    def area(self, props="", objects=""):
        explore.forget_areas()
        write_map(self.tmp, "m", 8, 6, [layer_xml("g", 8, 6, room())], objects,
                  map_props=f"<properties>{props}</properties>" if props else "")
        return explore.load_area("m", self.tmp)

    def test_no_property_no_darkness_and_an_old_light_is_rpg6s_dark(self):
        a = self.area()
        self.assertEqual((a.darkness, a.light, a.outdoor, a.lights, a.music, a.ambience), (0.0, None, False, [], None, None))
        self.assertEqual(a.darkness_at(at(1, 23)), 0.0)
        a = self.area(prop("light", "float", 4.5))
        self.assertEqual(a.light, 4.5)
        self.assertAlmostEqual(a.darkness, explore.OLD_DARKNESS)

    def test_darkness_lantern_music_and_ambience(self):
        a = self.area(prop("darkness", "float", 0.6) + prop("lantern", "float", 3) + prop("light", "float", 9)
                      + prop("music", "string", "music.rpg_town") + prop("ambience", "string", "amb.ruins"))
        self.assertEqual((a.darkness, a.light, a.music, a.ambience), (0.6, 3.0, "music.rpg_town", "amb.ruins"))
        self.assertFalse(a.outdoor)
        for m in (at(1, 12), at(1, 23)):                                 # indoors: the same darkness at every hour
            self.assertEqual(a.darkness_at(m), 0.6)

    def test_an_outdoor_map_follows_the_clock(self):
        a = self.area(prop("darkness", "float", 0.5) + prop("outdoor", "bool", "true"))
        self.assertTrue(a.outdoor)
        self.assertEqual(a.night_darkness, 0.75)                        # dusk + NIGHT_MORE
        self.assertEqual((a.darkness_at(at(1, 12)), a.darkness_at(at(1, 18, 30)), a.darkness_at(at(1, 23))), (0, 0.5, 0.75))
        a = self.area(prop("darkness", "float", 0.8) + prop("outdoor", "bool", "true"))
        self.assertEqual(a.night_darkness, explore.NIGHT_MAX)          # never darker than NIGHT_MAX by itself
        a = self.area(prop("darkness", "float", 0.3) + prop("outdoor", "bool", "true") + prop("night_darkness", "float", 0.9))
        self.assertEqual(a.darkness_at(at(1, 23)), 0.9)                 # unless the map says so

    def test_a_bad_value_is_said(self):
        a = self.area(prop("darkness", "float", 1.5))
        self.assertEqual(a.darkness, 0.0)
        self.assertTrue(any("darkness" in p for p in a.problems), a.problems)
        a = self.area(prop("darkness", "float", 0.4) + prop("night_darkness", "float", 2))
        self.assertEqual(a.night_darkness, 0.65)
        self.assertTrue(any("night_darkness" in p for p in a.problems), a.problems)

    def test_light_objects_their_defaults_and_their_flag(self):
        a = self.area(prop("darkness", "float", 0.5),
                      obj(1, "light", 16, 16, 16, 16, "", {})
                      + obj(2, "light", 48, 32, 16, 16, "", {"radius": ("float", 5), "strength": ("float", 0.5),
                                                             "flicker": ("bool", "false"), "flag": ("string", "door_open")})
                      + obj(3, "light", 64, 32, 16, 16, "", {"radius": ("float", 0)}))
        self.assertEqual(len(a.lights), 2)
        one, two = a.lights
        self.assertEqual((one.id, one.x, one.y, one.radius, one.strength, one.flicker, one.flag),
                         ("m#1", 24, 24, explore.LIGHT_RADIUS, explore.LIGHT_STRENGTH, True, None))
        self.assertEqual((two.radius, two.strength, two.flicker, two.flag), (5.0, 0.5, False, "door_open"))
        self.assertTrue(any("light 3" in p for p in a.problems), a.problems)
        self.assertEqual([li.id for li in a.lit(())], ["m#1"])
        self.assertEqual([li.id for li in a.lit({"door_open"})], ["m#1", "m#2"])


# ---- the shipped maps -----------------------------------------------------------------------------------------------------------

class ShippedMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        explore.forget_areas()
        cls.tool = make_tool()
        cls.areas = {n: explore.load_area(n) for n in MAPS}

    def test_each_maps_light_level_lantern_music_and_ambience(self):
        want = {TOWN: (0.45, 0.7, True, 3.0, "music.rpg_town", "amb.ruins"),
                OUT: (0.5, 0.75, True, 3.5, "music.rpg_outskirts", "amb.graveyard"),
                "warrens_1": (0.85, 0.85, False, 4.5, "music.rpg_warrens", "amb.plain"),
                "warrens_2": (0.85, 0.85, False, 4.5, "music.rpg_warrens", "amb.plain")}
        for name, (dusk, night, outdoor, lantern, music, amb) in want.items():
            with self.subTest(name=name):
                a = self.areas[name]
                self.assertEqual((a.darkness, a.night_darkness, a.outdoor, a.light, a.music, a.ambience),
                                 (dusk, night, outdoor, lantern, music, amb))
                self.assertEqual(a.problems, [])
        self.assertEqual(self.areas[TOWN].darkness_at(at(1, 12)), 0)                    # the town by day: no darkness
        self.assertEqual(self.areas["warrens_1"].darkness_at(at(1, 12)), 0.85)          # the Warrens: dark at every hour

    def tiles_that_glow(self, name):
        """{(col, row): (radius, strength)} of every brazier and candle tile in the map's text layout."""
        t = self.tool
        lay = t.read_layout(os.path.join(SRC, name + ".txt"))
        out = {}
        for y, row in enumerate(lay.rows):
            for x, ch in enumerate(row):
                if ch in lay.markers:
                    continue
                spec = t.TERRAINS[lay.legend[ch]]
                if spec.get("light"):
                    out[(x, y)] = spec["light"]
        return lay, out

    def test_every_brazier_and_candle_tile_gives_light(self):
        counts = {}
        for name in MAPS:
            with self.subTest(name=name):
                a = self.areas[name]
                lay, glow = self.tiles_that_glow(name)
                by_tile = {(int(li.x // a.tw), int(li.y // a.th)): li for li in a.lights}
                for tile, (radius, strength) in glow.items():
                    li = by_tile.get(tile)
                    self.assertIsNotNone(li, f"{name}: the light tile at {tile} has no light")
                    self.assertEqual((li.radius, li.strength, li.flicker), (radius, strength, True))
                    self.assertEqual((li.x, li.y), (tile[0] * a.tw + a.tw / 2, tile[1] * a.th + a.th / 2))
                counts[name] = (len(a.lights), sum(li.flag is not None for li in a.lights))
        self.assertEqual(counts, {TOWN: (13, 4), OUT: (2, 0), "warrens_1": (2, 0), "warrens_2": (8, 0)})

    def test_the_halls_lights_burn_only_once_the_priest_is_beaten(self):
        a = self.areas[TOWN]
        (c0, r0, c1, r1), flag = a.dark_zones[0]
        self.assertEqual(flag, talk.BOSS_FLAG)
        inside = [li for li in a.lights if c0 <= li.x // a.tw <= c1 and r0 <= li.y // a.th <= r1]
        self.assertEqual(len(inside), 4)
        self.assertTrue(all(li.flag == talk.BOSS_FLAG for li in inside))
        self.assertEqual(len(a.lit(())), 9)
        self.assertEqual(len(a.lit({talk.BOSS_FLAG})), 13)

    def test_the_rooms_of_the_town_have_their_lamps(self):
        a = self.areas[TOWN]
        lamps = sorted((int(li.x // a.tw), int(li.y // a.th)) for li in a.lights if li.radius == 4 and li.strength == 0.75)
        self.assertEqual(lamps, [(6, 13), (8, 6), (8, 21), (31, 7), (31, 21)])

    def test_light_objects_come_after_every_other_object_so_saved_spots_keep_their_ids(self):
        for name in MAPS:
            with self.subTest(name=name):
                obs = self.areas[name].map.objects
                lights = [ob.id for ob in obs if ob.kind == "light"]
                # Round RPG16: markers added since (RPG15c's tool numbers new objects after the highest old id) may come after
                # the lights; every object the maps had when RPG15b shipped them still comes before them
                with open(os.path.join(BASE, "tests", "fixtures", "rpg", "object_ids_rpg15b.json"), encoding="utf-8") as fh:
                    shipped = {int(i) for i, ob in json.load(fh)["maps"][name].items() if ob["class"] != "light"}
                others = [ob.id for ob in obs if ob.kind != "light" and ob.id in shipped]
                self.assertTrue(lights)
                self.assertGreater(min(lights), max(others))

    def test_the_maps_are_what_the_tool_makes_from_the_layouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tool.make(out=tmp, previous=os.path.join(BASE, "rpg", "maps"))   # Round RPG16: the ids the maps keep (RPG15c)
            for name in MAPS:
                with self.subTest(name=name):
                    with open(os.path.join(tmp, name + ".tmx"), encoding="utf-8") as f:
                        made = f.read()
                    with open(os.path.join(BASE, "rpg", "maps", name + ".tmx"), encoding="utf-8") as f:
                        shipped = f.read()
                    self.assertEqual(made, shipped, f"{name}.tmx is out of date: python tools/rpg_make_maps.py")


# ---- the darkness drawn ---------------------------------------------------------------------------------------------------------

def brightness(surf, x, y):
    return sum(surf.get_at((int(x), int(y)))[:3]) / 3


def through(darkness, keep=0.0):
    """White seen through the darkness (rpg_map_view.NIGHT at that alpha); keep: the share of the darkness a light takes away."""
    a = round(255 * darkness) * (1 - keep) / 255
    return 255 * (1 - a) + sum(rpg_map_view.NIGHT) / 3 * a


class LightPixelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((64, 64))
        explore.forget_areas()
        cls.town = explore.load_area(TOWN)

    def view(self, area, darkness, flags=(), flicker=False):
        v = rpg_map_view.MapView(area)
        v.lit, v.flags, v.darkness, v.flicker = True, flags, darkness, flicker
        return v

    def drawn(self, view, x, y, now=0.0):
        """The darkness drawn on white over a 480x320 view at zoom 1 centred on map pixel (x, y): (screen, camera)."""
        a = view.area
        cam = explore.Camera(480, 320, 1)
        cam.follow(x, y, a.width, a.height)
        scr = pygame.Surface((480, 320))
        scr.fill((255, 255, 255))
        view.draw_light(scr, cam, None, now)
        return scr, cam

    def far_spot(self, area, cam, flags=()):
        """A screen pixel further than every burning light's radius (and in view)."""
        for sy in range(10, 310, 10):
            for sx in range(10, 470, 10):
                mx, my = sx + cam.x, sy + cam.y
                if all((li.x - mx) ** 2 + (li.y - my) ** 2 > (li.radius * area.tw + 4) ** 2 for li in area.lit(flags)):
                    return sx, sy
        self.fail("no spot away from the lights")

    def test_dark_away_from_a_light_and_lit_near_one(self):
        lamp = next(li for li in self.town.lights if li.flag is None and li.radius == 4)
        scr, cam = self.drawn(self.view(self.town, 0.7), lamp.x, lamp.y)
        near = brightness(scr, lamp.x - cam.x, lamp.y - cam.y)
        far = brightness(scr, *self.far_spot(self.town, cam))
        self.assertAlmostEqual(far, through(0.7), delta=2)              # 0.7 dark: 30 % of the white shows
        self.assertAlmostEqual(near, through(0.7, lamp.strength), delta=3)   # the lamp's middle: 0.75 of the dark gone
        self.assertGreater(near, far + 100)

    def test_no_darkness_by_day_and_none_with_the_light_off(self):
        lamp = next(li for li in self.town.lights if li.flag is None)
        scr, cam = self.drawn(self.view(self.town, 0.0), lamp.x, lamp.y)
        self.assertEqual(brightness(scr, *self.far_spot(self.town, cam)), 255)

    def test_the_halls_candles_light_only_after_the_flag(self):
        candle = next(li for li in self.town.lights if li.flag == talk.BOSS_FLAG)
        before, cam = self.drawn(self.view(self.town, 0.7), candle.x, candle.y)
        after, _cam = self.drawn(self.view(self.town, 0.7, flags={talk.BOSS_FLAG}), candle.x, candle.y)
        sx, sy = candle.x - cam.x, candle.y - cam.y
        self.assertAlmostEqual(brightness(before, sx, sy), through(0.7), delta=2)
        self.assertGreater(brightness(after, sx, sy), brightness(before, sx, sy) + 100)

    def test_the_mask_bands_grow_darker_outwards(self):
        v = self.view(self.town, 0.7)
        m = v.light_mask(40, 179, 0.8)
        alphas = [m.get_at((40 + d, 40))[3] for d in (0, 12, 22, 32, 39)]
        self.assertEqual(alphas, sorted(alphas))
        up = -(-179 // rpg_map_view.MASK_ALPHA_STEP) * rpg_map_view.MASK_ALPHA_STEP   # Round RPG15c (review R4, expected change):
        self.assertEqual(m.get_at((0, 0))[3], up)            # a mask's alpha is rounded up to MASK_ALPHA_STEP (180 here)
        self.assertGreaterEqual(up, 179)                     # ... never below the darkness's own fill, so its edge can't show
        self.assertAlmostEqual(alphas[0], up * 0.2, delta=1.5)

    def test_a_flame_flickers_between_two_bands_the_same_every_run(self):
        a = self.town
        flame = next(li for li in a.lights if li.flag is None and li.flicker)
        v = self.view(a, 0.7, flicker=True)
        moments = [i / rpg_map_view.FLICKER_HZ for i in range(360)]

        def radii(view):
            return [next(r for x, y, r, _s in view.light_sources(None, t) if (x, y) == (flame.x, flame.y)) for t in moments]
        first = radii(v)
        self.assertEqual(first, radii(self.view(a, 0.7, flicker=True)))
        self.assertEqual(len(set(first)), 2)
        dips = first.count(min(first)) / len(first)
        self.assertTrue(0.2 < dips < 0.47, dips)
        self.assertEqual(len(set(radii(self.view(a, 0.7, flicker=False)))), 1)      # Animations off: a steady flame

    def test_the_lantern_goes_with_the_walker(self):
        a = self.town
        v = self.view(a, 0.7)
        walker = explore.Walker(a, *a.spawn("start"))
        here = v.light_sources(walker, 0.0)[0]
        self.assertEqual(here, (walker.x, walker.y, int(round(a.light * a.tw)), 1.0))


# ---- the window -------------------------------------------------------------------------------------------------------------------

class ScreenTests(r14.WindowBase):
    def walk_somewhere(self, g, f):
        """A direction the walker can go from where it stands, found by trying."""
        ex = f.explorer
        for step in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            x, y = ex.walker.x, ex.walker.y
            f.last = None
            f.walk_tick(g, 100.0, pressed=step)
            f.walk_tick(g, 100.05, pressed=step)
            if (ex.walker.x, ex.walker.y) != (x, y):
                return step
        self.fail("the walker can't move from here")

    def test_walking_moves_the_clock_and_standing_still_does_not(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        world = f.game.save.world
        step = self.walk_somewhere(g, f)
        t0 = clock.now(world)
        for i in range(1, 6):                                             # five ticks of 0.05 s (MAX_DT caps a tick)
            f.walk_tick(g, 100.05 + i * 0.05, pressed=step)
        self.assertAlmostEqual(clock.now(world) - t0, 0.25 * clock.GAME_MINUTES_PER_SECOND, places=2)
        f.walk_tick(g, 100.31, pressed=(0, 0))                            # let go: the walker stops
        t1 = clock.now(world)
        for i in range(1, 6):
            f.walk_tick(g, 100.31 + i * 0.2, pressed=(0, 0))
        self.assertEqual(clock.now(world), t1)

    def test_the_hud_shows_the_time_of_day(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 18, 20)
        frame(g)
        self.assertIn("Day 1, Dusk 18:20", [t for t, _r in f.hud_words])   # Round RPG17 (expected change): with the day

    def test_the_inn_wakes_you_at_seven(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 21, 30)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=True)  # Round RPG15c (expected change): she asks; Sleep until morning
        self.assertEqual(clock.now(f.game.save.world), at(2, 7))
        self.assertEqual(r8.on_disk(self.folder)["world"]["clock"], at(2, 7))

    def test_a_lost_duel_wakes_you_at_the_inn_and_keeps_the_clock(self):
        """Round RPG17 (expected change, Karl 24 Oct "Losses keep the clock"): before, the clock went on to 07:00 (at(3, 7))."""
        g, _l = self.setup_game()
        f = self.in_town(g)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=True)  # Round RPG15c (expected change): she asks; Sleep until morning
        f = self.on_map(g, OUT, "from_town")
        f.game.save.world["clock"] = at(2, 22)
        self.meet(g, encounters.RandomFoe("thornback"))
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=False)
        g.end_continue()
        frame(g)
        self.assertEqual(g.modal.explorer.area.name, TOWN)
        self.assertEqual(clock.now(g.modal.game.save.world), at(2, 22))

    def test_a_won_duel_leaves_the_clock_alone(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        f.game.save.world["clock"] = at(1, 22)
        self.win(g, encounters.RandomFoe("thornback"))
        self.assertEqual(clock.now(g.modal.game.save.world), at(1, 22))

    def town_spot(self, g, f):
        """The screen's mean brightness."""
        frame(g)
        return sum(pygame.transform.average_color(g.screen)[:3]) / 3

    def test_the_menus_light_item_turns_the_darkness_off_and_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            g, _l = self.setup_game()
            g.settings_path = path
            f = self.in_town(g)
            f.game.save.world["clock"] = at(1, 23)
            frame(g)
            view = f.views[TOWN]
            self.assertTrue(view.lit)
            self.assertEqual(view.darkness, 0.7)
            dark = self.town_spot(g, f)
            self.press(g, "pause")
            self.assertIn("Light: on", [label for _n, label, _on in f.menu.items])
            self.choose(g, f, "light")
            self.assertFalse(g.rpg_light)
            self.assertIn("Light: off", [label for _n, label, _on in f.menu.items])
            self.assertEqual(f.sub, "pause")                                    # the menu stays open
            with open(path, encoding="utf-8") as fh:
                self.assertIs(json.load(fh)["rpg_light"], False)
            key(g, pygame.K_ESCAPE)
            frame(g)
            self.assertFalse(view.lit)
            self.assertGreater(self.town_spot(g, f), dark + 25)
            again = ft.ForgeTable(g.session, r8.StubStore(), settings_path=path, window_size=(800, 600))
            self.addCleanup(self.close, again)
            self.assertFalse(again.rpg_light)

    def choose(self, g, f, name):
        """Pick a Menu item by keys (Down until it is the cursor's, then Enter)."""
        names = [n for n, _l, _on in f.menu.items]
        for _ in range(len(names) + 1):
            if names[f.menu.cursor] == name:
                key(g, pygame.K_RETURN)
                return
            key(g, pygame.K_DOWN)
        self.fail(f"no {name} in the menu")

    def test_the_encounter_screen_is_as_dark_as_the_place_but_round_the_foe(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        with mock.patch.object(renc, "night_layer", wraps=renc.night_layer) as layer:
            f.game.save.world["clock"] = at(1, 23)
            enc = self.meet(g, encounters.RandomFoe("thornback"))
            self.assertEqual(enc.darkness, 0.75)
            self.assertTrue(layer.called)
            self.assertEqual(layer.call_args[0][1], 0.75)
            self.press(g, "no")
            layer.reset_mock()
            f.game.save.world["clock"] = at(2, 12)
            enc = self.meet(g, encounters.RandomFoe("thornback"))
            self.assertEqual(enc.darkness, 0)
            self.assertFalse(layer.called)                                     # by day: no darkness layer at all
            self.press(g, "no")
            f.game.save.world["clock"] = at(2, 23)
            g.rpg_light = False
            enc = self.meet(g, encounters.RandomFoe("thornback"))
            self.assertEqual(enc.darkness, 0)                                  # Light off: the plain picture

    def test_the_night_layer_is_clear_on_the_foe_and_dark_round_it(self):
        layer = renc.night_layer((200, 200), 0.8, (100, 100), 60)
        alphas = [layer.get_at((100 + d, 100))[3] for d in (0, 20, 40, 55, 75, 99)]
        self.assertEqual(alphas[0], 0)
        self.assertEqual(alphas, sorted(alphas))
        self.assertEqual(alphas[-1], round(255 * 0.8))
        self.assertEqual(alphas[-2], round(255 * 0.8))

    def test_the_music_each_screen_asks_for(self):
        g, _l = self.setup_game()
        self.on_map(g, TOWN, "start")
        self.assertEqual(g.soundscape(), ("amb.ruins", "loop:music.rpg_town"))
        self.on_map(g, OUT, "from_town")
        self.assertEqual(g.soundscape(), ("amb.graveyard", "loop:music.rpg_outskirts"))
        self.on_map(g, "warrens_1")
        self.assertEqual(g.soundscape(), ("amb.plain", "loop:music.rpg_warrens"))
        f = self.on_map(g, "warrens_2")
        f.fight, f.sub = self.boss(f), "fight"
        self.assertEqual(g.soundscape(), ("amb.plain", "loop:music.rpg_boss"))

    def test_walking_into_a_foe_sounds_the_encounter_sting(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        played = []
        g.play_cue = lambda cid, *a, **k: played.append(cid) or True
        foe = next(t for t in f.explorer.area.triggers if t.kind == "encounter")
        with mock.patch.object(f.explorer, "step", return_value=("encounter", foe)):
            f.last = None
            f.walk_tick(g, 10.0, pressed=(1, 0))
            f.walk_tick(g, 10.1, pressed=(1, 0))
        self.assertEqual(f.sub, "fight")
        self.assertIn("rpg.encounter", played)


class TableMusicTests(unittest.TestCase):
    def test_a_boss_duel_plays_the_bosss_music_and_others_the_game_playlist(self):
        gui = make_gui("main1_start")
        gui.adventure_duel = {"boss": True}
        self.assertEqual(gui.soundscape()[1], "loop:music.rpg_boss")
        gui.adventure_duel = {"boss": False}
        self.assertEqual(gui.soundscape()[1], "game")

    def test_an_adventure_duel_ends_with_the_adventures_stings(self):
        gui = make_gui("main1_start")
        gui.audio = mock.Mock(ready=True)
        gui.play_cue = lambda cid, *a, **k: True
        gui.adventure_duel = {"boss": False}
        gui._play_beat(beat("outcome", winner=99), 99, "Karl")
        self.assertEqual(gui.audio.music_sting_after.call_args[0][0], "music.rpg_victory")
        gui._play_beat(beat("outcome", winner=5), 99, "Karl")
        self.assertEqual(gui.audio.music_sting_after.call_args[0][0], "music.rpg_defeat")
        gui.adventure_duel = None
        gui._play_beat(beat("outcome", winner=99), 99, "Karl")
        self.assertEqual(gui.audio.music_sting_after.call_args[0][0], "music.victory")   # a table game: as before


# ---- the cues -------------------------------------------------------------------------------------------------------------------

class CueTests(unittest.TestCase):
    def test_every_cue_a_map_or_screen_names_exists_with_its_files(self):
        explore.forget_areas()
        cues = manifest()
        named = {rs.BOSS_MUSIC, rs.VICTORY_STING, rs.DEFEAT_STING, renc.STING}
        for name in MAPS:
            a = explore.load_area(name)
            named |= {a.music, a.ambience}
        for cid in sorted(named):
            with self.subTest(cue=cid):
                self.assertIn(cid, cues)
                files = cues[cid]["files"]
                self.assertTrue(files)
                for fn in files:
                    self.assertTrue(os.path.isfile(os.path.join(SOUNDS, fn)), fn)
        for cid in (rs.BOSS_MUSIC, "music.rpg_town", "music.rpg_outskirts", "music.rpg_warrens"):
            self.assertTrue(cues[cid].get("stream"), cid)                        # long tracks are streamed, never decoded
        self.assertEqual(cues[renc.STING].get("bus"), "stinger")

    def test_every_new_track_is_credited(self):
        with open(os.path.join(SOUNDS, "CREDITS.txt"), encoding="utf-8") as f:
            credits = f.read()
        for src in ("mus3-moonlit-lullaby-for-the-lost", "mus3-kokopellis-graveyard-theme", "mus3-dark-cavern-ambient",
                    "mus3-boss-battle-music", "mus3-victory-fanfare", "mus3-game-over-theme", "mus3-string-and-piano-horror-stings"):
            self.assertIn(src + ":", credits)                             # the credits name sources, one line each

    def test_the_loop_music_mode(self):
        self.assertEqual(audio.loop_cue("loop:music.rpg_town"), "music.rpg_town")
        self.assertEqual(audio.loop_cue("title"), "music.title")
        for mode in ("loop:", "game", None, "keep"):
            self.assertIsNone(audio.loop_cue(mode))

    def test_a_loop_mode_loops_its_track_and_falls_back_to_the_titles(self):
        d = au1.director()
        with mock.patch.object(d, "_start_track", return_value=True) as start, \
                mock.patch.object(pygame.mixer.music, "get_busy", return_value=False):
            d.set_music("loop:music.rpg_town", 0.0)
            d.update(5.0)
            start.assert_called_once_with("music.rpg_town", 5.0, loops=-1)
            start.reset_mock()
            d.set_music("loop:music.no_such_track", 6.0)
            d.update(12.0)
            start.assert_called_once_with("music.title", 12.0, loops=-1)


if __name__ == "__main__":
    unittest.main()
