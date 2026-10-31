# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG15c: the independent review's findings (claude/REVIEW_RPG10_RPG15_2026-10-24.md), fixed. The review's own failing
tests are tests/test_review_rpg10_15.py (R1-R8, now passing); these check the fixes further and the "found by reading" ones.

  ClockTests         R2: the clock keeps time at 60 and 240 ticks a second
  MaskTests          R4: the light masks stay few, one frame never needs more than are kept, a mask is never lighter at its edge
  InnTests           R3: Marta asks; Enter sleeps at night and doesn't by day; Sleep rests you, Not now doesn't
  IdTests            R5: every object id RPG15b shipped keeps its meaning; a moved marker keeps its id; new ones come after
  CollectionTests    R1: the wheel reaches every card; "N more" is said; names aren't cut short when a column can hold them
  MissingArtTests    R8: a missing tile picture is said on the map and noted once
  ReadingTests       N2 a boss can't be escaped by closing the game in his box; N3 waking at the inn brings the beaten foes back;
                     N5 outdoor written as a string; N6 the duel hall isn't dark with the town's night
"""
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import pygame

import rpg_map_view
import rpg_screens as rs
from rpg import clock, encounters, explore, talk, tmx
from tests import test_rpg8 as r8
from tests import test_rpg14 as r14
from tests.test_forge_table import frame, key
from tests.test_rpg12 import make_tool

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS = os.path.join(BASE, "rpg", "maps")
SRC = os.path.join(MAPS, "src")
FIXTURE = os.path.join(BASE, "tests", "fixtures", "rpg", "object_ids_rpg15b.json")
TOWN, OUT, W2 = "lantern_hollow", "ashfen_outskirts", "warrens_2"


def at(day, hh, mm=0):
    return (day - 1) * clock.DAY + hh * 60 + mm


def objects_of(path):
    """{id: (class, name, {property: value})} of a built map file."""
    out = {}
    for ob in ET.parse(path).getroot().iter("object"):
        out[int(ob.get("id"))] = (ob.get("class"), ob.get("name"), {p.get("name"): p.get("value") for p in ob.iter("property")})
    return out


class ClockTests(unittest.TestCase):
    def test_the_clock_keeps_time_at_any_tick_rate(self):
        for hz in (30, 60, 144, 240):
            w = {}
            for _ in range(hz * 10):
                clock.advance(w, 1 / hz)
            with self.subTest(hz=hz):
                self.assertAlmostEqual(clock.now(w) - clock.START, 10.0, delta=0.01)

    def test_a_day_walked_at_60_ticks_is_a_day(self):
        w = {"clock": 0.0}
        for _ in range(60 * 60 * 24):                               # 24 minutes of walking at 60 ticks a second
            clock.advance(w, 1 / 60)
        self.assertAlmostEqual(clock.now(w), clock.DAY, delta=0.5)


class MaskTests(unittest.TestCase):
    def view(self, name):
        pygame.init()
        pygame.display.set_mode((64, 64))
        explore.forget_areas()
        return rpg_map_view.MapView(explore.load_area(name))

    def test_one_frame_never_needs_more_masks_than_are_kept(self):
        """The cap must not throw away masks the same frame needs again (every frame would make them anew)."""
        for name in (TOWN, OUT):
            v = self.view(name)
            a = v.area
            cam = explore.Camera(4096, 2160, explore.Camera.zoom_for(2160, a.th))
            most = 0
            for c in range(0, a.cols, 8):
                for r in range(0, a.rows, 6):
                    w = explore.Walker(a, (c + 0.5) * a.tw, (r + 0.5) * a.th)
                    cam.follow(w.x, w.y, a.width, a.height)
                    for now in (0.0, 0.17, 0.33):                    # the flames dip at some of these
                        v.darkness = 0.7
                        v.cache = {k: s for k, s in v.cache.items() if k[0] != "mask"}
                        v.mask_order.clear()
                        rw, rh = cam.view_w // cam.zoom, cam.view_h // cam.zoom
                        v.draw_light(pygame.Surface((rw, rh)), _flat(cam), w, now)
                        most = max(most, len(v.mask_order))
            with self.subTest(map=name):
                self.assertLess(most, rpg_map_view.MASK_KEEP, f"one frame of {name} needs {most} masks")

    def test_the_cache_keeps_at_most_mask_keep(self):
        v = self.view(TOWN)
        for alpha in range(0, 256):
            v.light_mask(30, alpha, 1.0)
        masks = [k for k in v.cache if k[0] == "mask"]
        self.assertLessEqual(len(masks), rpg_map_view.MASK_KEEP)
        self.assertEqual(set(masks), set(v.mask_order))

    def test_a_mask_is_never_lighter_than_the_darkness_at_its_edge(self):
        v = self.view(TOWN)
        for alpha in (1, 77, 178, 179, 254, 255):
            with self.subTest(alpha=alpha):
                self.assertGreaterEqual(v.light_mask(20, alpha, 0.5).get_at((0, 0))[3], alpha)


def _flat(cam):
    flat = explore.Camera(cam.view_w // cam.zoom, cam.view_h // cam.zoom, 1)
    flat.x, flat.y = cam.x, cam.y
    return flat


class InnTests(r14.WindowBase):
    def open_talk(self, g, when, choice=(talk.REST_YES, talk.REST_NO)):
        f = self.in_town(g)
        f.game.save.world["clock"] = when
        f.game.save.world.pop("inn", None)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        box = f.talk_box
        for _ in range(20):
            if box.choosing:
                break
            key(g, pygame.K_RETURN)
        self.assertTrue(box.choosing)
        self.assertEqual(box.choice, choice)
        return f, box

    def test_at_night_enter_sleeps_until_morning(self):
        g, _l = self.setup_game()
        f, box = self.open_talk(g, at(1, 21, 30))
        self.assertEqual(box.choice[box.cursor], talk.REST_YES)
        key(g, pygame.K_RETURN)
        self.say_all(g)
        w = f.game.save.world
        self.assertEqual(clock.now(w), at(2, 7))
        self.assertEqual(r8.on_disk(self.folder)["world"]["inn"], {"map": TOWN, "spawn": explore.INN_SPAWN})

    def test_by_day_enter_says_not_now_and_nothing_changes(self):
        g, _l = self.setup_game()
        f, box = self.open_talk(g, at(2, 12), (talk.REST_DUSK, talk.REST_YES, talk.REST_NO))   # RPG17: a rest until dusk by day;
        # ... Round RPG18b (expected change, Karl 25 Oct): and Sleep until morning - three answers
        self.assertEqual(box.choice[box.cursor], talk.REST_NO)
        key(g, pygame.K_RETURN)
        self.assertEqual(f.sub, None)
        w = f.game.save.world
        self.assertEqual(clock.now(w), at(2, 12))
        self.assertNotIn("inn", w)

    def test_a_rest_by_day_is_still_yours_to_choose(self):
        """Round RPG17 (expected change, Karl 24 Oct Q5): by day the first answer is "Rest until dusk" (to 18:00); before it was
        "Sleep until morning" (to 07:00 the next day)."""
        g, _l = self.setup_game()
        f, box = self.open_talk(g, at(2, 12), (talk.REST_DUSK, talk.REST_YES, talk.REST_NO))   # RPG18b: three answers
        key(g, pygame.K_DOWN)                                         # round to Rest until dusk
        self.assertEqual(box.choice[box.cursor], talk.REST_DUSK)
        key(g, pygame.K_RETURN)
        self.say_all(g)
        self.assertEqual(clock.now(f.game.save.world), at(2, 18))


class IdTests(unittest.TestCase):
    ADDED_LATER = {"when", "title"}      # Round RPG16: an object may gain a `when` (the zealot by day); nothing it had may change;
    # ... Round RPG18b (expected change): or a `title` (the bandits' leader by the bridge, object 6 of the outskirts)

    def test_every_id_rpg15b_shipped_keeps_its_meaning(self):
        with open(FIXTURE, encoding="utf-8") as fh:
            old = json.load(fh)["maps"]
        for name, obs in old.items():
            now = objects_of(os.path.join(MAPS, name + ".tmx"))
            for oid, ob in obs.items():
                with self.subTest(map=name, id=oid):
                    cur = now.get(int(oid))
                    self.assertIsNotNone(cur)
                    self.assertEqual(cur[:2], (ob["class"], ob["name"]))
                    self.assertLessEqual(set(cur[2]) - set(ob["props"]), self.ADDED_LATER)
                    self.assertEqual({k: cur[2].get(k) for k in ob["props"]}, ob["props"])

    def build(self, tmp, layout_text, name="m"):
        tool = make_tool()
        src = os.path.join(tmp, name + ".txt")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(layout_text)
        built = os.path.join(tmp, name + ".tmx")
        text = tool.tmx_text(tool.read_layout(src), tool.Sheets(), tool.read_ids(built))
        with open(built, "w", encoding="utf-8") as fh:
            fh.write(text)
        return objects_of(built), int(ET.parse(built).getroot().get("nextobjectid"))

    def test_a_moved_marker_keeps_its_id_and_new_ones_come_after_the_last(self):
        with open(os.path.join(SRC, "warrens_2.txt"), encoding="utf-8") as fh:
            text = fh.read()
        head, grid = text.split("[map]\n", 1)
        rows = grid.split("\n")
        with tempfile.TemporaryDirectory() as tmp:
            before, nxt = self.build(tmp, text, "warrens_2")
            self.assertEqual(nxt, max(before) + 1)
            boss = next(i for i, (_c, _n, p) in before.items() if p.get("encounter") == "boss")
            r = next(i for i, row in enumerate(rows) if "B" in row)
            c = rows[r].index("B")
            moved = list(rows)
            moved[r] = moved[r][:c] + "+" + moved[r][c + 1:]               # the Grave Priest two rows up, three tiles left
            moved[r - 2] = moved[r - 2][:c - 3] + "B" + moved[r - 2][c - 2:]
            after, _n = self.build(tmp, head + "[map]\n" + "\n".join(moved), "warrens_2")
            self.assertEqual(after, before, "a moved boss changed an id")
            # two new foes, one above every old marker: both numbered after the highest id, the old ones untouched
            r0 = 2
            c0 = moved[r0].index("+")
            moved[r0] = moved[r0][:c0] + "R" + moved[r0][c0 + 1:]
            moved[r0 + 9] = moved[r0 + 9][:c0 + 6] + "R" + moved[r0 + 9][c0 + 7:]
            more_head = head.replace("[markers]\n", "[markers]\nR = encounter kind=plague_rat on +\n", 1)
            more, nxt2 = self.build(tmp, more_head + "[map]\n" + "\n".join(moved), "warrens_2")
        self.assertEqual({i: more[i] for i in before}, before)
        self.assertEqual(sorted(i for i in more if i not in before), [max(before) + 1, max(before) + 2])
        self.assertEqual(nxt2, max(before) + 3)
        self.assertEqual(more[boss], before[boss])

    def test_without_an_old_file_ids_are_reading_order(self):
        tool = make_tool()
        objs = [("spawn", {"name": "a"}, (0, 0, 16, 16), (0, 0, "a")), ("spawn", {"name": "b"}, (16, 0, 16, 16), (0, 1, "b"))]
        self.assertEqual(tool.assign_ids(objs), [1, 2])

    def test_identical_objects_keep_the_nearest_old_ids(self):
        tool = make_tool()
        foe = lambda x: ("encounter", {"kind": "plague_rat"}, (x, 0, 16, 16), (0, x // 16, "r"))
        sig = tool._signature("encounter", *tool._written_props("encounter", {"kind": "plague_rat"}))
        old = [(4, sig, (0.0, 0.0)), (9, sig, (160.0, 0.0))]
        self.assertEqual(tool.assign_ids([foe(16), foe(144)], old), [4, 9])          # each to the one it stood nearer
        self.assertEqual(tool.assign_ids([foe(0), foe(80), foe(160)], old), [4, 10, 9])   # the new one after the highest


class CollectionTests(r14.WindowBase):
    def own(self, f, n):
        coll = f.game.save.collection
        names = sorted(nm for nm in f.game.pool.names() if not f.game.pool.get(nm).is_basic)[:n]
        for nm in names:
            coll.add(nm, 1)
        return [nm for nm in coll.names() if isinstance(coll.count(nm), int) and coll.count(nm) > 0]

    def drawn(self, g):
        out = []
        real = rs.draw_text

        def spy(screen, s, x, y, font, *a, **k):
            out.append(s)
            return real(screen, s, x, y, font, *a, **k)
        with mock.patch.object(rs, "draw_text", spy):
            frame(g)
        return out

    def test_the_wheel_reaches_every_card(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        owned = self.own(f, 400)
        key(g, pygame.K_ESCAPE)
        f.press_sub(g, "collection")
        seen, said_more = set(), False
        for _ in range(400):
            lines = self.drawn(g)
            said_more = said_more or any("more - scroll" in s for s in lines)
            seen |= {s.split("  ", 1)[1] for s in lines if "  " in s and s.split("  ", 1)[0].isdigit()}
            top = f.scroll.get("collection", 0)
            g.modal.wheel(g, -1)
            frame(g)
            if f.scroll.get("collection", 0) == top:
                break
        self.assertTrue(said_more, "the cards out of view were never said")
        self.assertEqual(sorted(set(owned) - seen), [])
        lines = self.drawn(g)                                         # at the end: nothing more below
        self.assertFalse(any(s.startswith("0 more") for s in lines))

    def test_a_starting_collection_fits_and_says_nothing_more(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        key(g, pygame.K_ESCAPE)
        f.press_sub(g, "collection")
        self.assertFalse(any("more - scroll" in s for s in self.drawn(g)))

    def test_names_are_not_cut_short_when_a_column_can_hold_them(self):
        g, _l = self.setup_game(size=(1360, 840))
        f = self.in_town(g)
        self.own(f, 120)
        key(g, pygame.K_ESCAPE)
        f.press_sub(g, "collection")
        cut = [s for s in self.drawn(g) if "  " in s and s.split("  ", 1)[0].isdigit() and s.endswith(("...", "…"))]
        self.assertEqual(cut, [])


class MissingArtTests(r14.WindowBase):
    def test_a_missing_sheet_is_said_and_noted_once(self):
        g, _l = self.setup_game()
        f = self.on_map(g, TOWN, "start")
        f.views.clear()
        real = rpg_map_view.pygame.image.load
        rpg_map_view._NOTED.clear()

        def load(path, *a, **k):
            if os.path.basename(str(path)).startswith("Floor"):
                raise FileNotFoundError(path)
            return real(path, *a, **k)
        with mock.patch.object(rpg_map_view.pygame.image, "load", load), \
                mock.patch.object(rpg_map_view.crashlog, "note") as note:
            frame(g, 2)
            text, colour = f.message
            self.assertIn("Floor", text)
            self.assertIn("missing", text)
            f.views.clear()
            frame(g, 2)                                               # a second view of the same map: no second note
        floor_notes = [c for c in note.call_args_list if "Floor" in str(c)]
        self.assertTrue(floor_notes, "the missing picture wasn't noted in the crash log")
        self.assertEqual(len(floor_notes), len({str(c) for c in floor_notes}), "a missing picture was noted twice")


class ReadingTests(r14.WindowBase):
    def test_n2_a_game_closed_in_the_bosss_box_meets_him_again(self):
        explore.forget_areas()
        a = explore.load_area(W2)
        boss = next(t for t in a.triggers if t.kind == "encounter" and t.encounter == "boss")
        x0, y0, x1, y1 = boss.box
        w = {"map": W2, "x": int((x0 + x1) / 2), "y": int((y0 + y1) / 2), "cleared": [], "beaten": []}
        save = type("S", (), {"world": w, "rng": lambda self, p: __import__("random").Random(1)})()
        ex = explore.Explorer(save)
        got = ex.step(0, 0, 1 / 60)
        self.assertIsNotNone(got)
        self.assertEqual((got[0], got[1].encounter), ("encounter", "boss"))

    def test_n2_a_beaten_boss_stays_beaten(self):
        explore.forget_areas()
        a = explore.load_area(W2)
        boss = next(t for t in a.triggers if t.kind == "encounter" and t.encounter == "boss")
        x0, y0, x1, y1 = boss.box
        w = {"map": W2, "x": int((x0 + x1) / 2), "y": int((y0 + y1) / 2), "cleared": [boss.spot], "beaten": []}
        save = type("S", (), {"world": w, "rng": lambda self, p: __import__("random").Random(1)})()
        self.assertIsNone(explore.Explorer(save).step(0, 0, 1 / 60))

    def test_n3_waking_at_the_inn_brings_the_beaten_foes_back(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        self.say_all(g, answer=True)
        f = self.on_map(g, OUT, "from_town")
        f.game.save.world["beaten"] = [f"{OUT}#6"]
        self.meet(g, encounters.RandomFoe("thornback"))
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=False)
        g.end_continue()
        frame(g)
        self.assertEqual(g.modal.explorer.area.name, TOWN)
        self.assertEqual(g.modal.game.save.world["beaten"], [])

    def test_n5_outdoor_as_a_string(self):
        tiled = tmx.read_map(os.path.join(MAPS, TOWN + ".tmx"))
        for value, outdoor, problem in (("true", True, False), ("False", False, False), ("maybe", False, True)):
            tiled.props["outdoor"] = value
            area = explore.Area("x", tiled)
            with self.subTest(value=value):
                self.assertIs(area.outdoor, outdoor)
                self.assertEqual(any("outdoor" in p for p in area.problems), problem)

    def test_n6_a_rematch_in_the_hall_is_not_dark_with_the_towns_night(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        f.game.save.world["clock"] = at(1, 23)
        self.assertGreater(f.explorer.area.darkness_at(at(1, 23)), 0.3)
        enc = self.meet(g, encounters.RematchFoe("thornback"))
        self.assertEqual(enc.darkness, 0.0)
        enc2 = self.meet(g, encounters.RandomFoe("thornback"))           # a fight outdoors at night still is
        self.assertGreater(enc2.darkness, 0.3)


if __name__ == "__main__":
    unittest.main()
