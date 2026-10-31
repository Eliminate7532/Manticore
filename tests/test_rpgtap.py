# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG-TAP (Karl, 27 Oct 2026, from his Surface Pro 11 with a pen only: "rpg mode needs click to move and click to interact";
"Walk to the tapped spot", "Walk up and act"): a left press (a pen tap) on the Adventure's map walks there, or up to a person,
door or foe and acts. Through the Adventure window (as RPG18c's window tests):

  1  TapTileTests      a tap on a tile walks to its middle through Explorer.step (the marker shows the goal); a wall or a place
                       with no way says so and nothing moves; random fights still happen on the way
  2  TapPersonTests    a tap on a person walks up, faces them and the talk opens; a shopkeeper across a counter too
  3  TapFoeTests       a tap on a foe walks into it: the duel's screen opens; a tap beyond a foe walks round it - no duel
  4  CancelTests       an arrow key (held or pressed) ends the walk
  5  PenTests          one pen tap acts once: SDL's touch events are ignored, and a second press told for the same tap is too
  6  HelpLineTests     the help line says you can tap

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest
tests.test_rpgtap
"""
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import explore, tapwalk
from tests import rpg_route
from tests.test_forge_table import click, frame, key
from tests.test_rpg14 import TOWN, WindowBase

FRAME = 1 / 60
OUT = "ashfen_outskirts"


def place(f, tile):
    ex = f.explorer
    a = ex.area
    ex._use(a, explore.Walker(a, *rpg_route.middle(a, tile)))


def here(f):
    ex = f.explorer
    return ex.area.tile_at(ex.walker.x, ex.walker.y)


def screen_of(f, mx, my):
    return f.camera.to_screen(mx, my)


class TapBase(WindowBase):
    def setUp(self):
        super().setUp()
        self.t = 1000.0

    def tap(self, g, mx, my):
        """A whole left click (press and release) at map point (mx, my), as a pen tap gives it."""
        f = g.modal
        frame(g)                                                    # the camera where it is now
        click(g, screen_of(f, mx, my))

    def tap_tile(self, g, tile):
        a = g.modal.explorer.area
        self.tap(g, *rpg_route.middle(a, tile))

    def walk(self, g, seconds=60, watch=None):
        """Tick the window's walk with no key held until the tap's walk ends or a window opens; watch(f) after every tick."""
        f = g.modal
        for i in range(max(1, int(round(seconds / FRAME)))):
            if f.tap is None or f.sub is not None or f.stage != "map":
                break
            self.t += FRAME
            f.walk_tick(g, self.t, pressed=(0, 0))
            if watch is not None:
                watch(f)
            if i % 20 == 0:
                frame(g)
        frame(g)
        return f



class TapTileTests(TapBase):
    def test_a_tap_on_a_tile_walks_to_its_middle_with_the_marker_on_it(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        start = here(f)
        reach = a.reachable((f.explorer.walker.x, f.explorer.walker.y), walls=f.explorer.walker.blockers)
        goal = next(c for c in sorted(reach, key=lambda c: (abs(c[0] - start[0]) + abs(c[1] - start[1]), c))
                    if abs(c[0] - start[0]) + abs(c[1] - start[1]) >= 4 and not a.is_counter(*c)
                    and c not in rpg_route.trigger_tiles(a) and c not in tapwalk.people_now(f.explorer))
        with mock.patch.object(f.explorer, "step", wraps=f.explorer.step) as step:
            self.tap_tile(g, goal)
            self.assertIsNotNone(f.tap)
            self.assertEqual(f.tap.goal.kind, "tile")
            frame(g)
            self.assertEqual(f.tap.marker(f.explorer), goal)
            marker = f.tap_marker_rect
            self.assertTrue(marker.collidepoint(screen_of(f, *rpg_route.middle(a, goal))))
            self.walk(g)
            self.assertGreater(step.call_count, 3)                  # walked by Explorer.step, the keys' code
        self.assertIsNone(f.tap)
        self.assertEqual(here(f), goal)
        mx, my = rpg_route.middle(a, goal)
        self.assertAlmostEqual(f.explorer.walker.x, mx, delta=0.1)
        self.assertAlmostEqual(f.explorer.walker.y, my, delta=0.1)
        self.assertEqual(f.sub, None)

    def test_a_wall_or_no_way_says_so_and_nothing_moves(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        start = (f.explorer.walker.x, f.explorer.walker.y)
        wall = next((c, r) for r in range(a.rows) for c in range(a.cols)
                    if a.blocked(c, r) and not a.is_counter(c, r) and (c, r) not in tapwalk.people_now(f.explorer)
                    and abs(c - here(f)[0]) + abs(r - here(f)[1]) <= 6)
        self.tap_tile(g, wall)
        self.assertIsNone(f.tap)
        self.assertEqual(f.message[0], tapwalk.WALL)
        reach = a.reachable(start, walls=f.explorer.walker.blockers)
        shut = [(c, r) for r in range(a.rows) for c in range(a.cols) if not a.blocked(c, r) and (c, r) not in reach
                and (c, r) not in rpg_route.trigger_tiles(a) and (c, r) not in tapwalk.people_now(f.explorer)]
        if shut:                                                     # a floor tile walled off from here: no way there
            target = min(shut, key=lambda c: abs(c[0] - here(f)[0]) + abs(c[1] - here(f)[1]))
            place(f, here(f))
            with mock.patch.object(f.camera, "to_map", return_value=rpg_route.middle(a, target)):
                f.click(g, (5, 5 + g.screen.get_height() // 2), 1)
            self.assertIsNone(f.tap)
            self.assertEqual(f.message[0], tapwalk.NO_WAY)
        self.walk(g, seconds=1)
        self.assertEqual((f.explorer.walker.x, f.explorer.walker.y), start)

    def test_a_walk_to_a_tile_goes_round_a_door_it_does_not_aim_at(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "warrens_1", "entrance")
        f.game.save.world["danger"] = 10 ** 6                      # no random fight on the way
        a = f.explorer.area
        door = next(t for t in a.triggers if t.kind == "exit" and t.to_map == "warrens_2")
        self.assertIn((33, 24), tapwalk.box_tiles(a, door.box))
        self.assertEqual(explore.route(a, (31, 24), (35, 24)), [(31, 24), (32, 24), (33, 24), (34, 24), (35, 24)])
        place(f, (31, 24))
        self.tap_tile(g, (35, 24))
        self.assertEqual(f.tap.goal.kind, "tile")
        self.walk(g)
        self.assertEqual(f.explorer.area.name, "warrens_1")         # never through the stairs down
        self.assertEqual(here(f), (35, 24))
        self.assertIsNone(f.sub)

    def test_a_tap_on_a_door_walks_through_it(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "warrens_1", "entrance")
        f.game.save.world["danger"] = 10 ** 6
        place(f, (31, 24))
        self.tap_tile(g, (33, 24))
        self.assertEqual(f.tap.goal.kind, "door")
        self.walk(g)
        self.assertEqual(f.explorer.area.name, "warrens_2")
        self.assertIsNone(f.tap)

    def test_a_random_fight_still_starts_on_the_way(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "warrens_1", "entrance")
        a = f.explorer.area
        f.game.save.world["danger"] = 1.5                           # the next random fight 1.5 tiles away
        start = here(f)
        reach = a.reachable((f.explorer.walker.x, f.explorer.walker.y))
        goal = max((c for c in reach if c not in rpg_route.trigger_tiles(a)
                    and abs(c[0] - start[0]) + abs(c[1] - start[1]) <= 6),
                   key=lambda c: abs(c[0] - start[0]) + abs(c[1] - start[1]))
        self.tap_tile(g, goal)
        self.walk(g)
        self.assertEqual(f.sub, "fight")
        self.assertIsInstance(f.fight, rs.encounters.RandomFoe)
        self.assertIsNone(f.tap)                                   # the walk ends where the fight began


class TapPersonTests(TapBase):
    def talk_to_by_tap(self, g, nid, tap_at=None):
        f = g.modal
        a = f.explorer.area
        at = tapwalk.person_tile(f.explorer, nid)
        self.assertIsNotNone(at, f"{nid} isn't out")
        self.tap_tile(g, tap_at or at)
        self.assertIsNotNone(f.tap)
        self.assertEqual((f.tap.goal.kind, f.tap.goal.nid), ("person", nid))
        talked = []
        real = f.talk_to

        def spy(gui):
            talked.append(f.explorer.facing_npc())
            return real(gui)
        with mock.patch.object(f, "talk_to", spy):
            self.walk(g)
        self.assertEqual(talked, [nid])
        self.assertEqual(f.sub, "talk")
        self.assertEqual(f.talk_conv.nid if hasattr(f.talk_conv, "nid") else nid, nid)
        self.assertEqual(f.explorer.facing_npc(), nid)
        return f, a

    def test_a_tap_on_a_person_walks_up_faces_them_and_talks(self):
        g, _l = self.setup_game()
        self.in_town(g)
        self.talk_to_by_tap(g, "elder")

    def test_a_tap_on_a_shop_counter_talks_to_its_keeper_and_the_shop_follows(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        keeper = tapwalk.person_tile(f.explorer, "card_seller")
        counter = next((keeper[0] - dx, keeper[1] - dy) for dx, dy in tapwalk.STEPS if a.is_counter(keeper[0] - dx, keeper[1] - dy))
        place(f, (counter[0], counter[1] + 3) if not a.blocked(counter[0], counter[1] + 3) else here(f))
        self.talk_to_by_tap(g, "card_seller", tap_at=counter)
        self.assertIsNotNone(f.talk_shop)                            # her words, then her shop (RPG15b)


class TapFoeTests(TapBase):
    def standing_foe(self, f):
        """The nearest foe out now, standing still for the test (its trigger's wander off; put back after)."""
        ex = f.explorer
        foe = min(ex.encounters(), key=lambda fo: abs(fo.x - ex.walker.x) + abs(fo.y - ex.walker.y))
        old = foe.t.wander
        foe.t.wander = False
        self.addCleanup(setattr, foe.t, "wander", old)
        return foe

    def test_a_tap_on_a_foe_walks_into_it_and_the_duel_screen_opens(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        foe = self.standing_foe(f)
        a = f.explorer.area
        ft = a.tile_at(foe.x, foe.y)
        near = next(c for c in ((ft[0] - 4, ft[1]), (ft[0] + 4, ft[1]), (ft[0], ft[1] - 4), (ft[0], ft[1] + 4))
                    if not a.blocked(*c) and explore.route(a, c, ft) is not None)
        place(f, near)
        self.tap(g, foe.x, foe.y)
        self.assertEqual((f.tap.goal.kind, f.tap.goal.spot), ("foe", foe.spot))
        self.walk(g)
        self.assertEqual(f.sub, "fight")
        self.assertEqual(f.fight.spot, foe.spot)
        self.assertIsNone(f.tap)

    def test_a_tap_beyond_a_foe_walks_round_it_and_no_duel_starts(self):
        g, _l = self.setup_game()
        f = self.on_map(g, OUT, "from_town")
        ex = f.explorer
        a = ex.area
        found = None
        for foe in ex.encounters():                                  # a foe on a straight line of free tiles, with a way round
            ft = a.tile_at(foe.x, foe.y)
            for dx, dy in ((1, 0), (0, 1)):
                w, gl = (ft[0] - 3 * dx, ft[1] - 3 * dy), (ft[0] + 3 * dx, ft[1] + 3 * dy)
                line = [(ft[0] + k * dx, ft[1] + k * dy) for k in range(-3, 4)]
                if any(a.blocked(*c) for c in line) or set(line) & rpg_route.trigger_tiles(a, but={foe.spot}):
                    continue
                if explore.route(a, w, gl) != line:
                    continue
                if tapwalk.route(a, w, {gl}, tapwalk.box_tiles(a, foe.box) | {ft}) is None:
                    continue
                found = (foe, w, gl)
                break
            if found:
                break
        self.assertIsNotNone(found, "no foe on a straight free line")
        foe, w, gl = found
        foe.t.wander = False
        self.addCleanup(setattr, foe.t, "wander", True)
        for other in ex.encounters():                                # only this foe near the way
            if other is not foe:
                other.still()
        place(f, w)
        self.tap_tile(g, gl)
        self.assertEqual(f.tap.goal.kind, "tile")
        touched = []

        def watch(fl):
            if explore.overlaps(fl.explorer.walker.box(), foe.box):
                touched.append(here(fl))
        self.walk(g, watch=watch)
        self.assertEqual(touched, [])
        self.assertIsNone(f.sub)
        self.assertEqual(here(f), gl)
        self.assertIn(foe.spot, [fo.spot for fo in ex.encounters()])    # still there: never duelled


class CancelTests(TapBase):
    def start_long_walk(self, g):
        f = self.in_town(g)
        a = f.explorer.area
        start = here(f)
        reach = a.reachable((f.explorer.walker.x, f.explorer.walker.y), walls=f.explorer.walker.blockers)
        goal = max((c for c in reach if c not in rpg_route.trigger_tiles(a) and abs(c[0] - start[0]) + abs(c[1] - start[1]) <= 8
                    and c not in tapwalk.people_now(f.explorer)),
                   key=lambda c: abs(c[0] - start[0]) + abs(c[1] - start[1]))
        self.tap_tile(g, goal)
        self.walk(g, seconds=0.2)
        self.assertIsNotNone(f.tap)
        return f, goal

    def test_a_held_walking_key_ends_the_walk(self):
        g, _l = self.setup_game()
        f, goal = self.start_long_walk(g)
        self.t += FRAME
        f.walk_tick(g, self.t, pressed=(0, 1))
        self.assertIsNone(f.tap)
        self.t += FRAME
        f.walk_tick(g, self.t, pressed=(0, 0))
        at = (f.explorer.walker.x, f.explorer.walker.y)
        self.walk(g, seconds=1)
        self.assertEqual((f.explorer.walker.x, f.explorer.walker.y), at)       # nothing walks on by itself

    def test_a_pressed_walking_key_ends_the_walk(self):
        g, _l = self.setup_game()
        f, goal = self.start_long_walk(g)
        key(g, pygame.K_a)
        self.assertIsNone(f.tap)


class PenTests(TapBase):
    def test_one_pen_tap_acts_once(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        start = here(f)
        goal = (start[0], start[1] + 2) if not a.blocked(start[0], start[1] + 2) else (start[0] + 2, start[1])
        frame(g)
        pos = screen_of(f, *rpg_route.middle(a, goal))
        W, H = g.screen.get_size()
        taps = []
        real = f.map_tap
        with mock.patch.object(f, "map_tap", lambda gui, p: (taps.append(p), real(gui, p))):
            # what Windows / SDL may send for one pen tap: the touch, and the mouse press made from it - twice
            for ev in (pygame.event.Event(pygame.FINGERDOWN, touch_id=1, finger_id=0, x=pos[0] / W, y=pos[1] / H,
                                          dx=0, dy=0, pressure=1),
                       pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1, touch=True),
                       pygame.event.Event(pygame.FINGERUP, touch_id=1, finger_id=0, x=pos[0] / W, y=pos[1] / H,
                                          dx=0, dy=0, pressure=0),
                       pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1, touch=True)):
                g.handle_event(ev)
            walk = f.tap
            self.walk(g, seconds=FRAME * 3)
            g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(pos[0] + 2, pos[1] - 1), button=1, touch=False))
            g.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(pos[0] + 2, pos[1] - 1), button=1, touch=False))
        self.assertEqual(len(taps), 1)
        self.assertIs(f.tap, walk)                                  # the same walk, not started again
        self.assertGreater(walk.steps, 0)

    def test_a_press_somewhere_else_is_a_new_tap(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        start = here(f)
        frame(g)
        one = screen_of(f, *rpg_route.middle(a, start))
        far = next(c for c in ((start[0] + 3, start[1]), (start[0] - 3, start[1]), (start[0], start[1] + 3))
                   if not a.blocked(*c))
        two = screen_of(f, *rpg_route.middle(a, far))
        click(g, one)
        first = f.tap
        click(g, two)
        self.assertIsNot(f.tap, first)
        self.assertEqual(f.tap.goal.tile, far)

    def test_a_right_click_on_the_map_does_nothing(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        a = f.explorer.area
        frame(g)
        click(g, screen_of(f, *rpg_route.middle(a, (here(f)[0], here(f)[1] + 1))), button=3)
        self.assertIsNone(f.tap)


class HelpLineTests(TapBase):
    def test_the_help_line_says_you_can_tap(self):
        self.assertIn("Tap: walk or act", rs.MAP_KEYS)
        self.assertIn("Arrows or WASD: walk", rs.MAP_KEYS)


if __name__ == "__main__":
    unittest.main()
