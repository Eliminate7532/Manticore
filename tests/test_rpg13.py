# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG13: random fights in the Warrens, the encounter screen, the way into it (claude/SPEC_RPG_SLICE_2026-10-23.md, RPG13).

The spec's checks: the rate over 10,000 seeded steps lies within its window; never two fights within 12 steps; the table's
weights; the screen fits from 1024 x 640 to 4096 x 2019; nothing is sent to Forge before Duel; a boss can't be fled; the record
and stats carry the fight as before. Plus: the way in and its timing, input waiting for it, See its deck showing the deck the duel
plays, the data (battle pictures, the foes' words), the maps.
"""
import json
import os
import random
import time
import unittest
from unittest import mock

import pygame

import gfx
import rpg_encounter as renc
import rpg_screens as rs
from rpg import adventure as adv
from rpg import encounters, enemies, explore
from rpg import save as sv
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame, move

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WARRENS = ("warrens_1", "warrens_2")
SIZES = ((1024, 640), (1366, 768), (1920, 1080), (2560, 1440), (4096, 2019), (4096, 2160))


def save_on(map_name, seed=7):
    s = sv.SaveGame(1, "Karl", seed)
    s.world["map"] = map_name
    return s


def pace(ex, steps, dt=0.1):
    """Walk the explorer back and forth along its row for `steps` tiles walked; returns [(tiles walked so far, foe)] for every
    random fight on the way (the screen would open: here the walk just goes on)."""
    out, walked, d = [], 0.0, 1
    tw = ex.area.tw
    guard = 0
    while walked < steps:
        before = ex.walker.walked
        ev = ex.step(d, 0, dt)
        moved = (ex.walker.walked - before) / tw
        walked += moved
        if ev is not None:
            if ev[0] == "random":
                out.append((walked, ev[1]))
            elif ev[0] != "random":
                raise AssertionError(f"walked into {ev}")
        if moved <= 1e-9:
            d = -d
            guard += 1
            if guard > 1000:
                raise AssertionError("the walker can't move")
        else:
            guard = 0
    return out


WARRENS_RATE = 200      # Round RPG17 (expected change, Karl 24 Oct: "Warrens encounter_rate 50 -> 100"): the maps' rate;
                        # Round RPG18 (expected change, Karl 24 Oct, Plan 2 of the 45-minute slice): 100 -> 200


class RateTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_the_average_over_10000_seeded_steps_lies_in_its_window(self):
        for seed in (1, 7, 2026):
            with self.subTest(seed=seed):
                ex = explore.Explorer(save_on("warrens_1", seed))
                self.assertEqual(ex.area.fight_rate, WARRENS_RATE)
                fights = pace(ex, 10_000)
                self.assertGreater(len(fights), 0)
                self.assertTrue(0.8 * WARRENS_RATE <= 10_000 / len(fights) <= 1.2 * WARRENS_RATE, f"{len(fights)} fights in 10,000 steps")

    def test_never_two_fights_within_12_steps(self):
        ex = explore.Explorer(save_on("warrens_1", 11))
        fights = pace(ex, 24_000)                                                # Round RPG17/18: the steps grow with the rate
        gaps = [b - a for (a, _f), (b, _g) in zip(fights, fights[1:])]
        self.assertGreater(len(gaps), 50)
        self.assertGreaterEqual(min(gaps), encounters.MIN_GAP - 1e-6)
        self.assertLessEqual(max(gaps), 2 * WARRENS_RATE - encounters.MIN_GAP + 0.5)   # the longest wait is bounded (+ one step)

    def test_the_gap_is_drawn_between_12_and_twice_the_rate_less_12(self):
        rng = random.Random(5)
        gaps = [encounters.next_gap(rng, 50) for _ in range(20_000)]
        self.assertEqual((min(gaps), max(gaps)), (12, 88))
        self.assertAlmostEqual(sum(gaps) / len(gaps), 50, delta=0.6)

    def test_the_tables_weights(self):
        rng = random.Random(3)
        table = encounters.parse_table("plague_rat 3, grave_robber 1")
        picks = [encounters.pick(rng, table) for _ in range(8000)]
        self.assertAlmostEqual(picks.count("plague_rat") / 8000, 0.75, delta=0.02)
        ex = explore.Explorer(save_on("warrens_1", 23))
        kinds = [f.enemy for _w, f in pace(ex, 8_000)]
        self.assertEqual(set(kinds), {"plague_rat", "grave_robber"})
        self.assertAlmostEqual(kinds.count("plague_rat") / len(kinds), 0.75, delta=0.1)

    def test_standing_still_or_pushing_a_wall_is_not_a_step(self):
        ex = explore.Explorer(save_on("warrens_1"))
        pace(ex, 3)
        danger = ex.save.world["danger"]
        for _ in range(500):
            self.assertIsNone(ex.step(0, 0, 0.1))
        self.assertEqual(ex.save.world["danger"], danger)
        for _ in range(500):                                  # into the wall to the left of the entrance hall
            self.assertIsNone(ex.step(-1, 0, 0.1))
        moved = ex.walker.walked
        for _ in range(200):
            ex.step(-1, 0, 0.1)
        self.assertEqual(ex.walker.walked, moved)
        self.assertLessEqual(ex.save.world["danger"], danger)
        d2 = ex.save.world["danger"]
        for _ in range(200):
            ex.step(-1, 0, 0.1)
        self.assertEqual(ex.save.world["danger"], d2)

    def test_a_map_without_random_fights_never_has_one(self):
        for name in ("lantern_hollow", "ashfen_outskirts"):
            with self.subTest(name=name):
                s = save_on(name)
                s.world["beaten"] = [t.spot for t in explore.load_area(name).triggers if t.kind == "encounter"]
                ex = explore.Explorer(s)
                self.assertIsNone(ex.area.fight_rate)
                self.assertEqual(pace(ex, 400), [])
                self.assertNotIn("danger", s.world)

    def test_the_steps_to_go_are_in_the_save_and_survive_a_reload(self):
        s = save_on("warrens_1", 9)
        ex = explore.Explorer(s)
        pace(ex, 30)
        d = s.world["danger"]
        self.assertTrue(0 < d <= 2 * WARRENS_RATE - encounters.MIN_GAP)       # Round RPG18: was 88 (rate 50)
        back = sv.SaveGame.from_dict(json.loads(json.dumps(s.to_dict())))
        self.assertEqual(back.world["danger"], d)
        for bad in (-1, True, "5"):
            dd = s.to_dict()
            dd["world"]["danger"] = bad
            with self.subTest(bad=bad), self.assertRaises(sv.SaveError):
                sv.SaveGame.from_dict(dd)

    def test_the_same_seed_gives_the_same_fights(self):
        runs = []
        for _ in range(2):
            explore.forget_areas()
            ex = explore.Explorer(save_on("warrens_1", 77))
            runs.append([(round(w, 6), f.enemy) for w, f in pace(ex, 1_500)])
        self.assertEqual(runs[0], runs[1])
        explore.forget_areas()
        other = [(round(w, 6), f.enemy) for w, f in pace(explore.Explorer(save_on("warrens_1", 78)), 1_500)]
        self.assertNotEqual(runs[0], other)

    def test_the_rate_and_the_table_are_checked(self):
        for bad in ("", "plague_rat 0", "plague_rat 2, plague_rat 1", "Plague Rat", "rat 2 3"):
            with self.subTest(bad=bad), self.assertRaises(encounters.EncounterError):
                encounters.parse_table(bad)
        for bad in (12, 5, 40.5, True, "50"):
            with self.subTest(rate=bad), self.assertRaises(encounters.EncounterError):
                encounters.check_rate(bad)
        a = explore.load_area("warrens_1")
        a.fight_table = [("no_such_kind", 1)]
        self.assertIn("encounters: no enemy kind 'no_such_kind'", a.check(kinds={k["id"] for k in enemies.read_kinds()}))
        explore.forget_areas()


class MapTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_the_warrens_have_random_fights_and_no_visible_rats(self):
        for name, table in (("warrens_1", [("plague_rat", 3), ("grave_robber", 1)]),
                            ("warrens_2", [("plague_rat", 2), ("grave_robber", 1)])):
            with self.subTest(name=name):
                a = explore.load_area(name)
                self.assertEqual((a.fight_rate, a.fight_table), (WARRENS_RATE, table))
                self.assertEqual([t.enemy for t in a.triggers if t.kind == "encounter" and t.encounter != "boss"], [])
                self.assertTrue(a.dungeon)

    def test_every_map_checks_clean_and_has_its_backdrop(self):
        kinds = {k["id"] for k in enemies.read_kinds()}
        with open(os.path.join(BASE, "rpg", "data", "towns.json"), encoding="utf-8") as f:
            towns = {t["id"] for t in json.load(f)["towns"]}
        self.assertEqual(explore.check_all(kinds=kinds, towns=towns), {n: [] for n in explore.map_names()})
        want = {"lantern_hollow": "forest", "ashfen_outskirts": "forest", "warrens_1": "dungeon", "warrens_2": "dungeon"}
        self.assertEqual({n: explore.load_area(n).backdrop for n in want}, want)


class DataTests(unittest.TestCase):
    def test_every_kind_has_a_battle_picture_and_a_scale(self):
        with open(os.path.join(BASE, "assets", "rpg", "MANIFEST.sha256"), encoding="utf-8") as f:
            shipped = f.read()
        for k in enemies.read_kinds():
            with self.subTest(kind=k["id"]):
                self.assertTrue(os.path.isfile(os.path.join(renc.SB_DIR, k["battler"] + ".png")))
                self.assertIn("simple_battles/" + k["battler"] + ".png", shipped)          # credited since RPG10
                self.assertEqual(k["battler_scale"], 4 if k["id"] == "grave_priest" else 3)

    def test_every_kind_has_its_words_within_the_limits(self):
        kinds = {k["id"] for k in enemies.read_kinds()}
        with open(encounters.KINDS_TEXT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], 1)
        self.assertEqual(set(data["kinds"]), kinds)
        for kid, t in data["kinds"].items():
            with self.subTest(kind=kid):
                self.assertTrue(0 < len(t["home"]) <= encounters.HOME_MAX)
                self.assertTrue(0 < len(t["plan"]) <= encounters.PLAN_MAX)
                self.assertTrue(0 < len(t["taunt"]) <= encounters.TAUNT_MAX)
        for key in ("intro", "defeated"):
            for page in data["kinds"]["grave_priest"][key]:
                lines = __import__("textwrap").wrap(page, 34)
                self.assertLessEqual(len(lines), 3, page)

    def test_a_kind_without_words_falls_back_to_its_colours(self):
        t = encounters.kind_text("plague_rat", path=os.path.join(BASE, "no_such_file.json"))
        self.assertEqual((t["plan"], t["home"], t["taunt"]), ("Plays Black and Red.", "", ""))

    def test_a_random_foe_is_a_dungeon_fight_with_no_spot(self):
        f = encounters.RandomFoe("plague_rat")
        self.assertEqual((f.kind, f.encounter, f.once, f.spot, f.random), ("encounter", "dungeon", False, None, True))


class SpiralTests(unittest.TestCase):
    def test_the_spiral_covers_every_cell_once_from_the_edge_in(self):
        for cols, rows in ((16, 9), (5, 5), (1, 4), (7, 2)):
            with self.subTest(cols=cols, rows=rows):
                order = renc.spiral(cols, rows)
                self.assertEqual(sorted(order), sorted((c, r) for c in range(cols) for r in range(rows)))
                self.assertEqual(order[0], (0, 0))
                ring = lambda p: min(p[0], p[1], cols - 1 - p[0], rows - 1 - p[1])
                rings = [ring(p) for p in order]
                self.assertEqual(rings, sorted(rings))             # outer rings first
        for w, h in SIZES:
            cells = renc.spiral_cells(w, h)
            cover = pygame.Rect(cells[0]).unionall(cells)
            self.assertTrue(cover.contains(pygame.Rect(0, 0, w, h)))
            self.assertEqual(cells[0].topleft, (0, 0))                       # the drawn order starts in the corner...
            mid = (w / 2, h / 2)
            dist = lambda r: abs(r.centerx - mid[0]) + abs(r.centery - mid[1])
            self.assertLess(dist(cells[-1]), dist(cells[0]))                 # ...and ends in the middle

    def test_the_canvas_scale_is_a_whole_number_that_fits(self):
        for (w, h), s in zip(SIZES, (1, 1, 2, 3, 4, 4)):
            with self.subTest(size=(w, h)):
                self.assertEqual(renc.canvas_scale(w, h), s)
                r = renc.canvas_rect(w, h)
                self.assertTrue(pygame.Rect(0, 0, w, h).contains(r))
                self.assertEqual(r.size, (640 * s, 480 * s))


class ScreenBase(r8.Base):
    def on_map(self, g, name, spawn=None):
        f = g.modal
        f.sub, f.result = None, None
        f.game.save.world.pop("in_town", None)
        f.game.save.world.update(map=name, x=None, y=None)
        explore.forget_areas()
        f.explorer = explore.Explorer(f.game.save)
        if spawn:
            a = f.explorer.area
            f.explorer._use(a, explore.Walker(a, *a.spawn(spawn)))
        f.set_stage("map")
        f.town = None
        frame(g)
        return f

    def meet(self, g, foe, ready=True):
        f = g.modal
        f.fight, f.sub, f.encounter = foe, "fight", None
        frame(g)
        if ready:
            f.encounter.skip_transition()
            frame(g)
        return f.encounter

    def boss(self, f):
        return next(t for t in f.explorer.area.triggers if t.kind == "encounter" and t.encounter == "boss")

    def setup_game(self, size=(1360, 840), launcher=None):
        r8.new_game(None)
        launcher = launcher or r8.Launcher()
        g = self.gui(size=size, launcher=launcher)
        self.open(g, slot=1)
        return g, launcher


class ScreenTests(ScreenBase):
    def test_it_fits_every_window(self):
        r8.new_game(None)
        for size in SIZES:
            with self.subTest(size=size):
                g = self.gui(size=size)
                self.open(g, slot=1)
                f = self.on_map(g, "warrens_1")
                for foe, view in ((encounters.RandomFoe("plague_rat"), "main"), (encounters.RandomFoe("grave_robber"), "deck")):
                    enc = self.meet(g, foe)
                    if view == "deck":
                        self.press(g, "see_deck")
                    W, H = g.screen.get_size()
                    dest = renc.canvas_rect(W, H)
                    s = renc.canvas_scale(W, H)
                    self.assertEqual(s, max(1, min(W // 640, H // 480)))
                    names = [n for _r, n in f.buttons]
                    self.assertEqual(names, ["fight", "see_deck", "no"] if view == "main" else ["deck_back"])
                    rects = [r for r, _n in f.buttons]
                    for r in rects:
                        self.assertTrue(dest.contains(r), f"{r} outside {dest}")
                    for i, a in enumerate(rects):
                        for b in rects[i + 1:]:
                            self.assertFalse(a.colliderect(b))
                    if dest.x > 0:
                        self.assertEqual(tuple(g.screen.get_at((dest.x // 2, H // 2)))[:3], gfx.DB_BG)    # the bars
                    self.assertEqual(enc.view, view)
                    f.sub, f.encounter, f.fight = None, None, None
                self.close(g)

    def test_the_foe_is_drawn_three_times_over_and_the_boss_four(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "warrens_2")
        for foe, scale in ((encounters.RandomFoe("plague_rat"), 3), (self.boss(f), 4)):
            with self.subTest(foe=foe.enemy):
                enc = self.meet(g, foe)
                self.assertEqual(enc.foe_scale, scale)
                kd = encounters.kind_data(foe.enemy)
                box = renc.picture(kd["battler"]).get_bounding_rect()
                self.assertEqual(enc.foe_rect.size, (box.w * scale, box.h * scale))
                self.assertTrue(renc.FOE_BOX.contains(enc.foe_rect))
                # the drawn picture: the foe's pixels are on the stage where it says, at that size
                stage = enc.stage()
                src = renc.picture(kd["battler"]).subsurface(box)
                for (px, py) in ((box.w // 2, box.h // 2), (box.w // 3, box.h // 2)):
                    c = src.get_at((px, py))
                    if c.a == 255:
                        self.assertEqual(tuple(stage.get_at((enc.foe_rect.x + px * scale + 1, enc.foe_rect.y + py * scale + 1)))[:3],
                                         tuple(c)[:3])
                f.sub, f.encounter, f.fight = None, None, None

    def test_a_foe_too_big_for_its_scale_is_drawn_smaller(self):
        kd = {"battler": "monster_firebat", "battler_scale": 3}           # 138 px wide: 3x would not fit 308
        img, s, rect = renc.foe_layout(kd, False)
        self.assertEqual(s, 2)
        self.assertTrue(renc.FOE_BOX.contains(rect))

    def test_the_backdrop_is_the_maps(self):
        g, _l = self.setup_game()
        g.rpg_light = False                       # Round RPG15: the plain art (the screen's darkness is test_rpg15's)
        for name, spawn, scene in (("ashfen_outskirts", "from_town", "scene_forest"), ("warrens_1", None, "scene_dungeon")):
            with self.subTest(map=name):
                f = self.on_map(g, name, spawn)
                enc = self.meet(g, encounters.RandomFoe("plague_rat"))
                pic = renc.picture(scene)
                self.assertEqual(tuple(enc.stage().get_at((3, 3)))[:3], tuple(pic.get_at((1, 1)))[:3])
                f.sub, f.encounter, f.fight = None, None, None

    def test_nothing_goes_to_forge_before_duel(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "warrens_1")
        enc = self.meet(g, encounters.RandomFoe("plague_rat"), ready=False)
        for k in (pygame.K_RETURN, pygame.K_DOWN, pygame.K_SPACE):           # the way in: nothing happens
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode=""))
            frame(g)
        self.assertEqual((f.sub, launcher.calls, enc.cursor), ("fight", [], 0))
        self.assertEqual(f.buttons, [])
        enc.skip_transition()
        frame(g)
        self.press(g, "see_deck")
        self.press(g, "deck_back")
        for k in (pygame.K_DOWN, pygame.K_UP, pygame.K_DOWN):
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode=""))
        frame(g)
        self.assertEqual(launcher.calls, [])
        self.assertIsNone(f.game.save.pending_duel)
        self.press(g, "fight")
        self.assertEqual(len(launcher.calls), 1)
        self.assertEqual(launcher.calls[0]["names"], ["Plague Rat"])

    def test_a_boss_cannot_be_fled(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "warrens_2")
        enc = self.meet(g, self.boss(f))
        cues = []
        g.play_cue = lambda cid, *a, **k: cues.append(cid)
        self.press(g, "no")
        self.assertEqual((f.sub, enc.note[0]), ("fight", renc.REFUSED))
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode=""))
        frame(g)
        self.assertEqual(f.sub, "fight")
        self.assertIn("ui.error", cues)
        self.assertEqual([o[2] for o in enc.options()], [True, True, False])
        self.assertEqual(launcher.calls, [])
        self.press(g, "fight")
        self.assertEqual(len(launcher.calls), 1)

    def test_turning_back_from_a_foe_goes_back_to_the_map(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "warrens_1")
        where = (f.explorer.walker.x, f.explorer.walker.y)
        self.meet(g, encounters.RandomFoe("grave_robber"))
        for k in (pygame.K_DOWN, pygame.K_DOWN, pygame.K_RETURN):              # the keyboard: Turn back is third
            g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode=""))
        frame(g)
        self.assertEqual((f.stage, f.sub, f.encounter), ("map", None, None))
        self.assertEqual((f.explorer.walker.x, f.explorer.walker.y), where)
        self.assertEqual((launcher.calls, f.game.save.pending_duel), ([], None))
        self.meet(g, encounters.RandomFoe("grave_robber"))
        g.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode=""))
        frame(g)
        self.assertIsNone(f.sub)

    def test_the_mouse_moves_the_cursor_and_picks(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "warrens_1")
        enc = self.meet(g, encounters.RandomFoe("plague_rat"))
        rect = dict((n, r) for r, n in f.buttons)["see_deck"]
        move(g, rect.center)
        self.assertEqual(enc.cursor, 1)
        click(g, rect.center)
        self.assertEqual(enc.view, "deck")
        self.assertEqual(launcher.calls, [])

    def test_see_its_deck_shows_the_deck_the_duel_plays(self):
        for seed in (7, 8, 9):
            with self.subTest(seed=seed):
                g, launcher = self.setup_game()
                f = self.on_map(g, "warrens_1")
                f.game.save.seed = seed
                enc = self.meet(g, encounters.RandomFoe("plague_rat"))
                self.press(g, "see_deck")
                shown = enc.deck
                lines = enc.deck_lines()
                self.assertEqual(sum(int(ln.split(" ", 1)[0]) for ln in lines if ln[:1].isdigit()), 60)
                self.press(g, "deck_back")
                f.game.save.rng("something else")             # the save's rng moves on before Duel: the shown deck still plays
                self.press(g, "fight")
                pd = r8.on_disk(self.folder)["pending_duel"]
                self.assertEqual(pd["deck"], enc.deck_index)
                played = sorted(launcher.calls[0]["opponents"][0][1])
                self.assertEqual(played, sorted(adv.deck_cards(shown["main"])))
                self.close(g)

    def test_the_way_in_takes_its_time(self):
        g, _l = self.setup_game(size=(1920, 1080))
        f = self.on_map(g, "warrens_1")
        before = g.screen.copy()
        enc = self.meet(g, encounters.RandomFoe("plague_rat"), ready=False)
        self.assertTrue(enc.animations)
        self.assertEqual(enc.length(), renc.SPIRAL_SECONDS + renc.FADE_SECONDS)
        cells = renc.spiral_cells(1920, 1080)

        def covered():
            return sum(tuple(g.screen.get_at(c.center))[:3] == gfx.DB_BG for c in cells)

        lit = sum(tuple(before.get_at(c.center))[:3] != gfx.DB_BG for c in cells)
        counts = []
        for t in (0.1, 0.35, 0.6):
            enc.t0 = time.monotonic() - t
            frame(g)
            counts.append(covered())
            self.assertEqual(f.buttons, [])
        self.assertTrue(counts[0] < counts[1] < counts[2], counts)
        self.assertGreater(lit, 0)
        self.assertEqual(tuple(g.screen.get_at((4, 4)))[:3], gfx.DB_BG)                # the top-left corner goes first
        enc.t0 = time.monotonic() - 0.1
        frame(g)
        self.assertEqual(tuple(g.screen.get_at((4, 4)))[:3], gfx.DB_BG)
        self.assertEqual(g.screen.get_at((960, 540)), before.get_at((960, 540)))       # while the middle is still the map
        enc.t0 = time.monotonic() - renc.SPIRAL_SECONDS - renc.FADE_SECONDS - 0.01
        frame(g)
        self.assertEqual(len(f.buttons), 3)

    def test_with_animations_off_it_is_a_short_fade(self):
        g, _l = self.setup_game()
        g.animations = False
        f = self.on_map(g, "warrens_1")
        enc = self.meet(g, encounters.RandomFoe("plague_rat"), ready=False)
        self.assertFalse(enc.animations)
        self.assertEqual(enc.length(), renc.PLAIN_SECONDS)
        enc.t0 = time.monotonic() - 0.05
        frame(g)
        self.assertEqual(f.buttons, [])
        enc.t0 = time.monotonic() - renc.PLAIN_SECONDS - 0.01
        frame(g)
        self.assertEqual(len(f.buttons), 3)


class FlowTests(ScreenBase):
    def test_walking_the_warrens_opens_the_screen_with_its_sting(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "warrens_1")
        cues = []
        g.play_cue = lambda cid, *a, **k: cues.append(cid)
        t, d = time.monotonic(), 1
        for _ in range(20000):
            before = f.explorer.walker.walked
            t += 1 / 30
            f.walk_tick(g, t, pressed=(d, 0))
            if f.sub is not None:
                break
            if f.explorer.walker.walked == before:
                d = -d
        self.assertEqual(f.sub, "fight")
        self.assertIsInstance(f.fight, encounters.RandomFoe)
        self.assertIn(renc.STING, cues)
        frame(g)
        self.assertIsInstance(f.encounter, renc.EncounterScreen)
        self.assertIsNotNone(f.encounter.snapshot)
        on_disk = r8.on_disk(self.folder)
        self.assertEqual(on_disk["world"]["map"], "warrens_1")                    # saved where you stood
        self.assertGreaterEqual(on_disk["world"]["danger"], encounters.MIN_GAP)  # the next fight is drawn already

    def test_a_random_duel_counts_like_any_other(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "warrens_1")
        self.meet(g, encounters.RandomFoe("plague_rat"))
        gold = f.game.save.gold
        self.press(g, "fight")
        call = launcher.calls[-1]
        self.assertEqual((call["fmt"], call["names"], len(call["opponents"][0][1])), ("standard", ["Plague Rat"], 60))
        pd = r8.on_disk(self.folder)["pending_duel"]
        self.assertEqual((pd["kind"], pd["encounter"], pd.get("spot")), ("plague_rat", "dungeon", None))
        self.assertEqual(g.session.stats_info["folder"], adv.duel_stats_dir())
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=True)
        disk = r8.on_disk(self.folder)
        self.assertIsNone(disk["pending_duel"])
        self.assertGreater(disk["gold"], gold)
        self.assertEqual((disk["record"]["duels"], disk["record"]["wins"], disk["record"]["defeated"]),
                         (1, 1, {"plague_rat": 1}))
        self.assertEqual((disk["world"].get("cleared") or [], disk["world"].get("beaten") or []), ([], []))
        self.assertTrue(os.path.isfile(os.path.join(adv.duel_stats_dir(), "games.ndjson")))
        g.end_continue()
        frame(g)
        f2 = g.modal
        self.assertEqual((f2.stage, f2.sub, f2.result[0]), ("map", "result", "Victory"))
        self.assertEqual(f2.explorer.area.name, "warrens_1")

    def test_a_visible_foe_still_opens_the_screen_and_says_it_comes_back(self):
        g, launcher = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts", "from_town")
        foe = next(t for t in f.explorer.area.triggers if t.kind == "encounter" and t.enemy == "ember_bandit")
        enc = self.meet(g, foe)
        self.assertFalse(enc.random)
        self.assertIn("Beaten, it comes back after your next visit to a town.", enc.lines)
        rnd = self.meet(g, encounters.RandomFoe("plague_rat"))
        self.assertNotIn("Beaten, it comes back after your next visit to a town.", rnd.lines)


if __name__ == "__main__":
    unittest.main()
