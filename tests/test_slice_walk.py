# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's vertical slice walked end to end, headlessly, the way a player would go through it (round SLICEWALK,
23 Oct 2026; the slice is claude/SPEC_RPG_SLICE_2026-10-23.md, rounds RPG10-RPG14).

One long run from a new game, every step asserted before the next one starts:
   1  a new game from the Adventure window (name, three Jumpstart halves, Begin), out of the card shop's window;
   2  all eight people of Lantern Hollow talked to (walking up to each one), both quests taken;
   3  both shop counters opened and closed;
   4  a rest at the inn (Marta);
   5  out of the east gate to the Ashfen outskirts;
   6  an Ember Bandit chased down and beaten (a stand-in engine's win, as in RPG14's tests) - Round RPG18: one, their leader by
      the bridge, and the others leave the road once Pell's quest is ready (was three);
   7  back through the gate to Pell: the Lamplighter's Debt handed in, its rare in the collection;
   8  to the old mill, down into the Warrens, floor 1 to its stairs, floor 2 (random fights on the way: the first won, the others
      turned back);
   9  the Grave Priest: his words first, no fleeing him, his duel won, his last words;
  10  Round RPG18: up the stair from the Grave Priest's hall into Lantern Hollow (was: back up both floors and through the
      outskirts): the duel hall is lit;
  11  Elder Hesk hands in The Dark Hall (+100 gold since RPG18, has_orrins_map);
  12  Keeper Brann's rematch, lost: you wake at Marta's inn with a fifth of your gold gone, and she says so;
  13  saved from the pause window, the program's window closed, a new one opens the slot: everything is where it was.
The new game's seed is 7 (SLICE_WALK_SEED=<n> for another: the random fights, the foes' wandering and the rewards change with it).
Walking is by route (tests/rpg_route's breadth-first search), one key at a time at 60 frames a second through
rpg_screens.AdventureFlow.walk_tick, never by moving the walker, so an exit to a missing spawn, a wall in the way, a person
nobody can reach or a quest that can't be finished stops the run at its step with the step's name in the message. A step the
code allows only in a way no player would think of (a person talked to through a wall) is done that way and noted, and
PeopleTests fails on it on its own.

A second run, in another order a player may well take: the three Ember Bandits beaten on the road first, then back to town, a
rest at the inn and Pell's quest taken - there must be three bandits to beat for it when you go out again. And a third: a duel
lost before any rest at the inn (back to Lantern Hollow's shop window, as before RPG14) still leaves a way out of the gate.

Plain map facts for the people (PeopleTests): every person in Lantern Hollow can be talked to from a tile a player would stand
on - next to them, or across a counter - without stepping on a trigger (a shop's counter opens the shop window first), and
nobody is talked to through a wall.

Findings when this file was written (23 Oct, round-rpg14b at 4ac6ec2): the long run passes (40 seeds of 40), and the early loss;
PeopleTests' two tests and the bandits' run fail - see claude/SLICE_WALK_2026-10-23.md in the Project.
Round RPG15b fixed them: you talk across a counter only (a counter tile), the shops open from their keepers' words (their
triggers stand on the counters now), and walking onto a town's map or resting at its inn brings the beaten foes back. Steps 2
and 3 open the shops that way (expected changes).
"""
import contextlib
import math
import os
import sys
import time
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import clock
from rpg import encounters, explore, newgame, talk
from tests import rpg_route
from tests import test_rpg8 as r8
from tests import test_rpg14 as r14
from tests.test_forge_table import click, frame, key

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOWN, OUT, W1, W2 = "lantern_hollow", "ashfen_outskirts", "warrens_1", "warrens_2"
FRAME = 1 / 60
FACE = {"down": (0, 1), "up": (0, -1), "right": (1, 0), "left": (-1, 0)}
DIRS = {(0, 1): "down", (0, -1): "up", (1, 0): "right", (-1, 0): "left"}
PEOPLE = ("card_seller", "gate_guard", "lamplighter", "elder", "innkeeper", "pack_seller", "hall_keeper", "platino")
QUEST_OF = {"elder": "the_dark_hall", "lamplighter": "lamplighters_debt"}
SHOPS = {"card_seller": ("singles", "town_1"), "pack_seller": ("packs", "town_1_packs")}   # Round RPG15b: their words open the shop
COUNTER = "K"                                                  # the counter's character in the map layouts (tools/rpg_make_maps.py)
WALK_LIMIT = 30000                                             # frames for one walk (8 minutes of walking)
RANDOM_KINDS = {W1: {"plague_rat", "grave_robber"}, W2: {"plague_rat", "grave_robber"}}


def layout(name):
    """The map's text layout (rpg/maps/src/<name>.txt): its [map] rows, each marker written as the terrain it stands on (Round
    RPG15b: a shop's trigger stands on its counter)."""
    with open(os.path.join(BASE, "rpg", "maps", "src", name + ".txt"), encoding="utf-8") as f:
        text = f.read().splitlines()
    under = {}
    for ln in text[text.index("[markers]") + 1:text.index("[map]")] if "[markers]" in text else ():
        if " = " in ln and " on " in ln:
            under[ln.split(" = ", 1)[0].strip()] = ln.rsplit(" on ", 1)[1].strip()
    return ["".join(under.get(ch, ch) for ch in ln) for ln in text[text.index("[map]") + 1:] if ln.strip()]


def is_counter(name, tile):
    rows = layout(name)
    c, r = tile
    return 0 <= r < len(rows) and 0 <= c < len(rows[r]) and rows[r][c] == COUNTER


def talk_spots(area, nid, at=None):
    """[(tile, facing, how)] for every tile the person `nid` can be talked to from (Explorer.facing_npc's rule): next to them
    ("next"), or across one solid tile with nobody on it - a counter ("counter") or anything else, a wall ("wall"). Round RPG16:
    at - the person's tile (a person may have a day and a night place; default their first marker)."""
    col, row = at or area.npcs[nid]["tile"]
    out = []
    for (dx, dy), facing in DIRS.items():
        nxt = (col - dx, row - dy)
        if not area.blocked(*nxt):
            out.append((nxt, facing, "next"))
        elif area.npc_at(*nxt) is None:
            far = (col - 2 * dx, row - 2 * dy)
            if not area.blocked(*far):
                out.append((far, facing, "counter" if is_counter(area.name, nxt) else "wall"))
    return out


class SliceWalkTests(r14.WindowBase):
    """The slice from a new game to its end, in one run (see the module's words)."""

    # ---- the run's bookkeeping -------------------------------------------------------------------------------------------------
    def setUp(self):
        super().setUp()
        self.log = []                  # [(step, what, seconds)]
        self.notes = []                # what a player couldn't do the plain way but the code let the run do (said at the end)
        self.random_met = []           # [(map, kind, what was done)]
        self.events_met = []           # Round RPG16: [(map, event id)] - random events read through on the way
        self.routes = {}
        self.avoid = {}
        self.t = time.monotonic()
        self.frames, self.walked = 0, 0.0      # frames walked (1/60 s each) and tiles walked, over the whole run
        self.walked_on = {}                    # Round RPG17: tiles walked on each map
        self.launcher = r8.Launcher()

    @contextlib.contextmanager
    def step(self, n, what):
        t0 = time.perf_counter()
        try:
            yield
        except AssertionError as e:
            raise AssertionError(f"[step {n}: {what}] {e}") from None
        except Exception as e:
            raise AssertionError(f"[step {n}: {what}] {type(e).__name__}: {e}") from e
        self.log.append((n, what, time.perf_counter() - t0))

    def here(self, g):
        ex = g.modal.explorer
        return ex.area.name, ex.area.tile_at(ex.walker.x, ex.walker.y)

    # ---- walking -----------------------------------------------------------------------------------------------------------------
    def press_for(self, ex, goal):
        """The key a player would hold now to walk to tile `goal` by the shortest way round the walls (rpg_route.next_press,
        with the route kept while the walker stays on one tile). None once on the goal tile's middle."""
        area = ex.area
        target = goal() if callable(goal) else goal
        here = area.tile_at(ex.walker.x, ex.walker.y)
        if area.name not in self.avoid:
            self.avoid[area.name] = rpg_route.trigger_tiles(area)
        k = (area.name, here, target)
        if k not in self.routes:
            self.routes[k] = rpg_route.tile_route(area, here, target, avoid=self.avoid[area.name] - {target})
        route = self.routes[k]
        if route is None:
            self.fail(f"no way on {area.name} from {here} to {target}")
        mx, my = rpg_route.middle(area, here)
        ddx, ddy = mx - ex.walker.x, my - ex.walker.y
        toward_x = (1 if ddx > 0 else -1, 0)
        toward_y = (0, 1 if ddy > 0 else -1)
        if len(route) == 1:
            if abs(ddx) <= rpg_route.NEAR and abs(ddy) <= rpg_route.NEAR:
                return None
            return toward_x if abs(ddx) > rpg_route.NEAR else toward_y
        sx, sy = route[1][0] - here[0], route[1][1] - here[1]
        if sx:
            return toward_y if abs(ddy) > rpg_route.NEAR else (sx, 0)
        return toward_x if abs(ddx) > rpg_route.NEAR else (0, sy)

    def tick(self, g, pressed):
        self.t += FRAME
        self.frames += 1
        f = g.modal
        before = f.explorer.walker.walked
        f.walk_tick(g, self.t, pressed=pressed)
        if g.modal is f and f.explorer is not None and f.explorer.walker is not None:
            d = max(0.0, f.explorer.walker.walked - before) / f.explorer.area.tw
            self.walked += d
            self.walked_on[f.explorer.area.name] = self.walked_on.get(f.explorer.area.name, 0.0) + d

    def walk(self, g, goal, until=None, limit=WALK_LIMIT):
        """Walk the map to tile `goal` ((col, row) or a function giving one), one key at a time. Stops on the goal's middle, or
        once `until(flow)` holds. A random fight on the way: the first is won, the rest turned back (random_fight). A visible
        foe walked into on the way (one `until` doesn't ask for): turned back. Anything else that opens over the map, a town
        window, or a change of map stops the run (unless `until` asked for it)."""
        start_map = g.modal.explorer.area.name
        for _ in range(limit):
            f = g.modal
            if until is not None and until(f):
                return f
            if f.sub == "fight":
                if isinstance(f.fight, encounters.RandomFoe):
                    self.random_fight(g)
                else:
                    frame(g)
                    self.press(g, "no")                               # Turn back from a foe met on the way
                    self.assertIsNone(g.modal.sub, "turning back from a foe left a window open")
                continue
            if f.sub == "talk" and getattr(f, "talk_event", None) is not None:   # Round RPG16: a random event on the way
                eid = f.talk_event[0]
                self.say_all(g, answer=False)                     # read it; a choice is answered No (nothing taken)
                self.assertIsNone(g.modal.sub, f"the event {eid} left a window open")
                self.events_met.append((start_map, eid))
                continue
            if f.sub is not None:
                self.fail(f"while walking on {start_map}, a window opened: {f.sub} {f.result or ''} "
                          f"{getattr(f.talk_box, 'name', '') or ''}")
            if f.stage != "map":
                self.fail(f"while walking on {start_map}, the screen changed to {f.stage!r} at {self.here(g)}")
            if f.explorer.area.name != start_map:
                self.fail(f"walking to {goal if not callable(goal) else 'a foe'} on {start_map}, the walker went onto "
                          f"{f.explorer.area.name}")
            press = self.press_for(f.explorer, goal)
            if press is None:
                if until is None:
                    return f
                self.fail(f"reached {goal} on {start_map} but nothing happened there")
            self.tick(g, press)
        self.fail(f"didn't get to {goal} on {start_map} in {limit} frames (at {self.here(g)})")

    def through(self, g, to_map, to_spawn):
        """Walk to the exit of this map that leads to `to_map`, through it, and check where it put the walker."""
        f = g.modal
        a = f.explorer.area
        exits = [t for t in a.triggers if t.kind == "exit" and t.to_map == to_map]
        self.assertTrue(exits, f"{a.name} has no exit to {to_map}")
        t = exits[0]
        tile = a.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
        f = self.walk(g, tile, until=lambda fl: fl.stage == "map" and fl.explorer.area.name == to_map)
        b = f.explorer.area
        self.assertIn(t.to_spawn, b.spawns, f"{a.name}'s exit to {to_map} names the spawn {t.to_spawn!r}, which {to_map} doesn't "
                                            f"have: the walker landed at {self.here(g)[1]} (the map's fallback spawn)")
        self.assertEqual(t.to_spawn, to_spawn, f"{a.name}'s exit to {to_map} leads to {t.to_spawn!r}, not {to_spawn!r}")
        self.assertEqual(self.here(g), (to_map, b.tile_at(*b.spawn(to_spawn))))
        return f

    def face(self, g, facing):
        """Press toward the person for one frame, as a player turns to face someone (the walker can't step onto them)."""
        self.tick(g, FACE[facing])
        self.assertEqual(g.modal.explorer.walker.facing, facing)

    # ---- people ------------------------------------------------------------------------------------------------------------------
    def leave_shop(self, g, tab, back):
        """Round RPG15b: the shop a keeper's words opened is the right one; leaving it puts you at the counter's spawn."""
        f = g.modal
        a = f.explorer.area
        self.assertEqual((f.stage, f.tab), ("town", tab), "the shopkeeper's words didn't open their shop")
        self.assertIsNotNone(f.shop)
        frame(g)
        self.press(g, "leave_town")
        self.assertEqual(f.stage, "map")
        self.assertEqual(self.here(g), (TOWN, a.tile_at(*a.spawn(back))))

    def talk_to(self, g, nid, answer=None, then=None):
        """Walk up to `nid` and talk: from a tile a player would stand on (next to them, or across a counter, not on a trigger);
        when there is none, the way the code still allows (across a wall), noted. `answer`: the choice's answer, if one is
        offered; `then`: the window open once the talk is over (None: the map). Returns (pages said, the Conversation)."""
        f = g.modal
        a = f.explorer.area
        self.assertIn(nid, a.npcs, f"{nid} isn't on {a.name}")
        at = next((p["tile"] for p in f.explorer.present_people() if p["id"] == nid), None)   # Round RPG16: where they are now
        self.assertIsNotNone(at, f"{nid} isn't out at {clock.clock_text(clock.now(f.game.save.world))}")
        _m, here = self.here(g)
        avoid = rpg_route.trigger_tiles(a) | set(f.explorer.walker.blockers)
        spots = [(tile, facing, how) for tile, facing, how in talk_spots(a, nid, at)
                 if tile not in avoid and rpg_route.tile_route(a, here, tile, avoid=avoid - {tile}) is not None]
        plain = [s for s in spots if s[2] != "wall"]
        if not plain:
            self.assertTrue(spots, f"{nid} ({a.npcs[nid]['name']}) can't be talked to from anywhere a player can walk")
            note = (f"{a.npcs[nid]['name']} could only be talked to across a wall, from {spots[0][0]} facing {spots[0][1]} "
                    "(see PeopleTests)")
            if note not in self.notes:
                self.notes.append(note)
        tile, facing, _how = sorted(plain or spots, key=lambda s: len(rpg_route.tile_route(a, here, s[0], avoid=avoid)))[0]
        self.walk(g, tile)
        self.face(g, facing)
        f = g.modal
        self.assertEqual(f.explorer.facing_npc(), nid)
        key(g, pygame.K_RETURN)
        self.assertEqual(f.sub, "talk", f"Enter in front of {nid} opened nothing")
        self.assertEqual(f.talk_box.name, talk.npcs()[nid]["name"])
        conv = f.talk_conv
        said = self.say_all(g, answer=answer)
        self.assertEqual(g.modal.sub, then, f"after the talk with {nid}: {g.modal.sub!r} open")
        return said, conv

    # ---- fights ------------------------------------------------------------------------------------------------------------------
    def duel(self, g, won):
        """The fight's window is open: Duel, the table (a stand-in engine), its end, back to the Adventure. The result window
        says Victory or Defeat. Returns the Adventure window opened after the duel."""
        f = g.modal
        self.assertEqual(f.sub, "fight")
        frame(g)                                                 # the encounter screen is made when first drawn
        self.assertIsNotNone(f.encounter, "the fight's screen never opened")
        calls = len(self.launcher.calls)
        self.press(g, "fight")
        if len(self.launcher.calls) != calls + 1:
            self.fail(f"Duel started no game: {getattr(g.modal, 'result', None)}")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=won)
        g.end_continue()
        frame(g)
        f = g.modal
        self.assertIsInstance(f, rs.AdventureFlow, "the table didn't come back to the Adventure")
        self.assertEqual((f.stage, f.sub), ("map", "result"))
        self.assertEqual(f.result[0], "Victory" if won else "Defeat", f.result)
        return f

    def ok(self, g):
        key(g, pygame.K_RETURN)                                  # the result window's OK

    def random_fight(self, g):
        f = g.modal
        foe = f.fight
        name, tile = self.here(g)
        self.assertIn(foe.enemy, RANDOM_KINDS.get(name, set()), f"a random {foe.enemy} on {name}")
        if not any(m == "won" for _n, _k, m in self.random_met):
            f = self.duel(g, won=True)
            self.assertEqual(self.here(g), (name, tile), "after a random fight you're not where you stood")
            self.ok(g)
            self.assertIsNone(g.modal.sub)
            self.random_met.append((name, foe.enemy, "won"))
        else:
            frame(g)
            self.press(g, "no")                                   # Turn back
            self.assertIsNone(g.modal.sub)
            self.assertEqual(self.here(g), (name, tile))
            self.random_met.append((name, foe.enemy, "turned back"))

    def foe_tile(self, g, spot):
        ex = g.modal.explorer
        foe = next((fo for fo in ex.encounters() if fo.spot == spot), None)
        self.assertIsNotNone(foe, f"the foe {spot} is gone from {ex.area.name}")
        return ex.area.tile_at(foe.x, foe.y)

    # ---- the run -----------------------------------------------------------------------------------------------------------------
    def test_the_slice_from_a_new_game_to_its_end(self):
        t_run = time.perf_counter()
        g = self.gui(launcher=self.launcher)

        seed = int(os.environ.get("SLICE_WALK_SEED", "7"))      # the new game's seed (the window draws one at random)

        class Seeded(newgame.NewGameFlow):
            def __init__(self, *a, **k):
                k.setdefault("seed", seed)
                super().__init__(*a, **k)

        with self.step(1, "a new game from the Adventure window"):
            f = self.open(g)
            with mock.patch.object(newgame, "NewGameFlow", Seeded):
                self.press(g, "new:1")
            self.assertEqual(f.flow.seed, seed)
            self.assertEqual(f.stage, "name")
            for ch in "Karl":
                key(g, getattr(pygame, "K_" + ch.lower()), unicode=ch)
            key(g, pygame.K_RETURN)
            self.assertEqual(f.stage, "pick")
            for _ in range(newgame.PICKS):
                offer = f.flow.offer()
                code = next((h for h in offer if h.size == 20), offer[0]).code
                click(g, next(r for r, k, d in f.rows if k == "pick" and d == code).center)
            self.assertEqual(f.stage, "ready")
            self.press(g, "begin")
            self.assertEqual((f.stage, f.sub), ("town", "result"))           # the card shop's window, with a welcome
            self.press(g, "ok")
            self.assertEqual(sum(n for _c, n in f.game.deck()["main"]), 60)
            self.press(g, "leave_town")
            self.assertEqual(f.stage, "map")
            a = f.explorer.area
            self.assertEqual(self.here(g), (TOWN, a.tile_at(*a.spawn("town_1"))))  # in front of the card counter
            frame(g)
            self.assertTrue(f.views[TOWN].dark_tiles, "the duel hall isn't dark at the start")
            gold0 = f.game.save.gold

        with self.step(2, "all eight people of Lantern Hollow, both quests taken"):
            out = {p["id"] for p in g.modal.explorer.present_people()}          # Round RPG16: the people out at this hour
            self.assertTrue(set(PEOPLE) <= out, sorted(set(PEOPLE) - out))
            for nid in PEOPLE:
                q = QUEST_OF.get(nid)
                said, conv = self.talk_to(g, nid, answer=True if q or nid == talk.INNKEEPER else None)   # RPG15c: Marta asks
                self.assertTrue([p for p in said if p != "..."], f"{nid} said nothing")
                self.assertEqual(said[0], conv.pages[0])
                w = g.modal.game.save.world
                self.assertIn(f"met_{nid}", talk.flags(w))
                if q:
                    self.assertEqual(conv.choice, (talk.quests()[q]["yes"], talk.quests()[q]["no"]), f"{nid} offered nothing")
                    self.assertEqual(talk.quest_state(w, q), "active")
                if nid == talk.HALL_KEEPER:
                    self.assertIsNone(conv.choice, "the keeper offers a rematch before the Grave Priest is beaten")
                if nid == talk.INNKEEPER:
                    self.assertEqual(r8.on_disk(self.folder)["world"]["inn"], {"map": TOWN, "spawn": explore.INN_SPAWN})
                if nid in SHOPS:                                   # Round RPG15b (expected change): the shop opens after the words
                    self.leave_shop(g, *SHOPS[nid])
            disk = r8.on_disk(self.folder)["world"]
            self.assertTrue({f"met_{n}" for n in PEOPLE} <= set(disk["flags"]))
            self.assertEqual({k: v["state"] for k, v in disk["quests"].items()},
                             {"the_dark_hall": "active", "lamplighters_debt": "active"})

        with self.step(3, "both shops opened by talking to their keepers across the counter, and closed"):
            for nid, (tab, back) in SHOPS.items():                 # Round RPG15b (expected change): a counter is talked across
                self.talk_to(g, nid)
                self.leave_shop(g, tab, back)

        with self.step(4, "a rest at the inn"):
            said, conv = self.talk_to(g, talk.INNKEEPER, answer=True)  # Round RPG15c (expected change): she asks; Sleep until morning
            self.assertTrue(conv.rest)
            self.assertEqual(said[-len(talk.inn()["rest"]):], talk.inn()["rest"])
            self.assertEqual(r8.on_disk(self.folder)["world"]["inn"], {"map": TOWN, "spawn": explore.INN_SPAWN})

        with self.step(5, "out of the east gate to the outskirts"):
            self.through(g, OUT, "from_town")

        with self.step(6, "the bandits' leader by the bridge beaten, and the rest leave the road"):   # RPG18: was three;
            # Round RPG18b (expected change, Karl 25 Oct): only the leader by the bridge counts; the hint says where he is
            a = g.modal.explorer.area
            bandits = [t.spot for t in a.triggers if t.kind == "encounter" and t.enemy == "ember_bandit"]
            self.assertGreaterEqual(len(bandits), 1, "no Ember Bandit on the outskirts")
            goal = talk.quests()["lamplighters_debt"]["goal"]
            self.assertEqual(goal["count"], 1)
            leader = goal["spot"]
            self.assertIn(leader, bandits)
            for _round in range(4):                               # the leader, and any other bandit that walks into us first
                ex = g.modal.explorer
                self.assertIn(leader, [fo.spot for fo in ex.encounters()], "the bandits' leader isn't on the road")
                f = self.walk(g, lambda: self.foe_tile(g, leader),
                              until=lambda fl: fl.sub == "fight" and getattr(fl.fight, "enemy", None) == "ember_bandit")
                spot = f.fight.spot
                f = self.duel(g, won=True)
                self.assertEqual(self.here(g)[0], OUT)
                self.ok(g)
                w = f.game.save.world
                self.assertIn(spot, w.get("beaten") or [], "a beaten bandit isn't marked beaten")
                self.assertNotIn(spot, [fo.spot for fo in f.explorer.encounters()], "a beaten bandit is still standing")
                state = talk.quest_state(w, "lamplighters_debt")
                if spot != leader:                                # an ordinary duel: the quest is still open
                    self.assertEqual(state, "active")
                    continue
                self.assertEqual(state, "ready")
                break
            else:
                self.fail("never reached the bandits' leader")
            self.assertEqual([fo for fo in g.modal.explorer.encounters() if fo.enemy == "ember_bandit"], [],
                             "Ember Bandits still on the road once Pell's quest is ready")

        with self.step(7, "back to Pell: the Lamplighter's Debt handed in"):
            self.through(g, TOWN, "from_outskirts")
            cards = g.modal.game.save.collection.total()
            said, _conv = self.talk_to(g, "lamplighter")
            new = [p for p in said if p.startswith("New card: ")]
            self.assertEqual(len(new), 1, said)
            self.assertEqual(said[-len(talk.quests()["lamplighters_debt"]["done"]):], talk.quests()["lamplighters_debt"]["done"])
            self.assertEqual(g.modal.game.save.collection.total(), cards + 1)
            card = new[0][len("New card: "):-1]
            self.assertGreaterEqual(g.modal.game.save.collection.count(card), 1)
            self.assertEqual(r8.on_disk(self.folder)["world"]["quests"]["lamplighters_debt"]["state"], "done")

        with self.step(8, "to the mill and down both floors of the Warrens"):
            self.through(g, OUT, "from_town")
            self.through(g, W1, "entrance")
            self.assertIn(talk.WARRENS_FLAG, talk.flags(g.modal.game.save.world))
            self.through(g, W2, "stairs_top")

        with self.step(9, "the Grave Priest"):
            a = g.modal.explorer.area
            boss = next((t for t in a.triggers if t.kind == "encounter" and t.encounter == "boss"), None)
            self.assertIsNotNone(boss, "no boss on the Warrens' second floor")
            self.assertEqual(boss.enemy, talk.BOSS_KIND)
            tile = a.tile_at((boss.box[0] + boss.box[2]) / 2, (boss.box[1] + boss.box[3]) / 2)
            f = self.walk(g, tile, until=lambda fl: fl.sub in ("talk", "fight") and getattr(fl.fight, "spot", None) == boss.spot)
            self.assertEqual(f.sub, "talk", "the Grave Priest said nothing before his duel")
            self.assertEqual(f.talk_box.name, "Grave Priest")
            self.assertEqual(self.say_all(g), encounters.kind_text("grave_priest")["intro"])
            self.assertEqual(f.sub, "fight")
            frame(g)
            self.press(g, "no")                                   # Turn back: refused
            self.assertEqual(f.sub, "fight", "the Grave Priest let you leave")
            f = self.duel(g, won=True)
            self.ok(g)
            self.assertEqual((f.sub, f.talk_box.name), ("talk", "Grave Priest"))
            self.assertEqual(self.say_all(g), encounters.kind_text("grave_priest")["defeated"])
            w = f.game.save.world
            self.assertIn(talk.BOSS_FLAG, talk.flags(w))
            self.assertEqual(talk.quest_state(w, "the_dark_hall"), "ready")
            self.assertIn(boss.spot, w.get("cleared") or [])
            disk = r8.on_disk(self.folder)["world"]
            self.assertIn(talk.BOSS_FLAG, disk["flags"])

        with self.step(10, "up the stair from the Grave Priest's hall to Lantern Hollow: the duel hall is lit"):
            self.through(g, TOWN, "from_outskirts")      # Round RPG18 (expected change): was W1, the outskirts, then the town
            frame(g)
            self.assertEqual(g.modal.views[TOWN].dark_tiles, [], "the duel hall is still dark after the Grave Priest")

        with self.step(11, "Elder Hesk hands in The Dark Hall"):
            gold = g.modal.game.save.gold
            said, _conv = self.talk_to(g, "elder")
            q = talk.quests()["the_dark_hall"]
            self.assertEqual(said, q["ready"] + ["+100 gold."] + q["done"])   # Round RPG18: 50 -> 100
            w = g.modal.game.save.world
            self.assertEqual(g.modal.game.save.gold, gold + 100)   # Round RPG18: 50 -> 100
            self.assertIn("has_orrins_map", talk.flags(w))
            self.assertEqual(talk.quest_state(w, "the_dark_hall"), "done")

        with self.step(12, "the keeper's rematch, lost: you wake at the inn"):
            said, conv = self.talk_to(g, talk.HALL_KEEPER, answer=True, then="fight")
            self.assertEqual(conv.choice, (talk.REMATCH_YES, talk.REMATCH_NO), "no rematch offered after the Grave Priest")
            self.assertEqual(said[-1], talk.REMATCH_PAGE)
            f = g.modal
            self.assertEqual(f.sub, "fight", "Yes to the rematch started nothing")
            self.assertIsInstance(f.fight, encounters.RematchFoe)
            self.assertIn(f.fight.enemy, f.game.save.record["defeated"])
            rival = f.fight.enemy
            gold = f.game.save.gold
            f = self.duel(g, won=False)
            lost = int(gold * f.game.numbers.loss_gold_share)
            self.assertEqual(f.game.numbers.loss_gold_share, 0.2)
            self.assertEqual(f.game.save.gold, gold - lost)
            rematch = (rival, gold, lost)
            a = f.explorer.area
            self.assertEqual(self.here(g), (TOWN, a.tile_at(*a.spawn(explore.INN_SPAWN))), "a lost duel didn't wake you at the inn")
            self.assertEqual(f.result[1][-1], f"You lost {lost} gold and woke at the inn.")
            self.ok(g)
            self.assertEqual((f.sub, f.talk_box.name), ("talk", talk.npcs()[talk.INNKEEPER]["name"]))
            self.assertEqual(self.say_all(g), talk.inn()["woke"])

        with self.step(13, "saved, the window closed, the slot opened again"):
            name, inn = self.here(g)
            a = g.modal.explorer.area
            spot = next(t for t in ((inn[0] + dx, inn[1] + dy) for dx, dy in ((1, 0), (-1, 0), (0, -1), (0, 1)))
                        if not a.blocked(*t))
            self.walk(g, spot)
            self.face(g, "left")
            key(g, pygame.K_ESCAPE)
            self.assertEqual(g.modal.sub, "pause")
            info = [t for t, _c in g.modal.menu.info]
            self.assertIn("The Dark Hall - Finished:", info)
            self.assertIn("The Lamplighter's Debt - Finished:", info)
            self.press(g, "save")
            self.assertEqual(g.modal.message[0], "Saved.")
            saved = r8.on_disk(self.folder)
            before = (g.modal.game.save.gold, g.modal.game.save.collection.total(), self.here(g),
                      g.modal.explorer.walker.facing)
            self.assertEqual((saved["world"]["map"], saved["gold"]), (TOWN, before[0]))
            key(g, pygame.K_ESCAPE)
            self.press(g, "leave")                                # Main menu: saved and left
            self.assertNotIsInstance(g.modal, rs.AdventureFlow)
            self.close(g)
            explore.forget_areas()
            g2 = self.gui(launcher=self.launcher)
            f2 = self.open(g2)
            self.press(g2, "slot:1")
            self.assertEqual((f2.stage, f2.sub), ("map", None), f2.result)
            self.assertEqual(r8.on_disk(self.folder)["world"], saved["world"])
            w2 = f2.game.save.world
            self.assertEqual({k: v["state"] for k, v in w2["quests"].items()},
                             {"the_dark_hall": "done", "lamplighters_debt": "done"})
            self.assertTrue({talk.BOSS_FLAG, "has_orrins_map", talk.WARRENS_FLAG} | {f"met_{n}" for n in PEOPLE}
                            <= talk.flags(w2))
            self.assertEqual(w2["inn"], {"map": TOWN, "spawn": explore.INN_SPAWN})
            self.assertEqual((f2.game.save.gold, f2.game.save.collection.total(), self.here(g2), f2.explorer.walker.facing),
                             before)
            frame(g2)
            self.assertEqual(f2.views[TOWN].dark_tiles, [])
            said, _conv = self.talk_to(g2, "elder")
            done = [e for e in talk.npcs()["elder"]["lines"] if e["when"] == ["quest:the_dark_hall=done"]][0]["pages"]
            self.assertEqual(said, done)

        total = time.perf_counter() - t_run
        lines = [f"  step {n:2}  {s:6.1f} s  {what}" for n, what, s in self.log]
        lines.append(f"  seed {seed}; the whole run: {total:.1f} s; {self.frames} frames walked ({self.frames / 60 / 60:.1f} minutes of a "
                     f"player's walking at 60 fps), {self.walked:.0f} tiles; {len(self.launcher.calls)} duels; random fights "
                     f"met: {self.random_met}; random events: {self.events_met}; gold at the start {gold0}, at the end "
                     f"{f2.game.save.gold}")
        lines.append(f"  the rematch: {rematch[0]}; gold {rematch[1]} before it, {rematch[2]} lost (a fifth, rounded down)")
        lines += [f"  NOTE: {n}" for n in self.notes]
        sys.stderr.write("\nThe slice walk:\n" + "\n".join(lines) + "\n")
        # Round RPG17 (expected change, Karl 24 Oct: the Warrens' encounter_rate 50 -> 100): a walk down and back may now meet no
        # random fight - but only if it walked fewer tiles there than the longest gap the rate allows (2 x rate - MIN_GAP)
        if not self.random_met:
            most = 2 * explore.load_area(W1).fight_rate - encounters.MIN_GAP          # the danger counts on both floors
            down = self.walked_on.get(W1, 0.0) + self.walked_on.get(W2, 0.0)
            self.assertLess(down, most, f"no random fight in {down:.0f} tiles in the Warrens (a gap is at most {most})")
            sys.stderr.write(f"  no random fight met (tiles in the Warrens: {self.walked_on.get(W1, 0):.0f} + "
                             f"{self.walked_on.get(W2, 0):.0f}; the rate is 100)\n")


    def hud_clock(self, g):
        frame(g)
        return g.modal.hud_words[-1][0]

    def test_rpg17_a_new_game_at_seven_a_rest_until_dusk_day_two_and_a_loss_that_keeps_the_clock(self):
        """Round RPG17 (Karl's decisions of 24 Oct): a new game begins at 07:00 (Q2); by day Marta offers "Rest until dusk" (Q5)
        and it wakes you at 18:00; a night's sleep makes it day 2, and the corner says so (Q10); a duel lost at dusk wakes you at
        the inn at the same hour (losses keep the clock), not at 07:00."""
        r8.new_game(None)
        g = self.gui(launcher=self.launcher)
        f = self.open(g, slot=1)
        with self.step(1, "a new game: 07:00 on day 1"):
            w = f.game.save.world
            self.assertEqual(clock.now(w), 7 * 60)
            self.press(g, "leave_town")
            self.assertEqual(g.modal.stage, "map")
            self.assertEqual(self.hud_clock(g), "Day 1, Day 07:00")
        with self.step(2, "Marta by day: Rest until dusk"):
            t0 = clock.now(w)
            said, conv = self.talk_to(g, talk.INNKEEPER, answer=True)
            self.assertEqual((conv.choice, conv.rest_until, conv.rest),
                             ((talk.REST_DUSK, talk.REST_YES, talk.REST_NO), "dusk", True))   # RPG18b: three answers by day
            self.assertLess(t0, 18 * 60)
            self.assertEqual(clock.now(w), 18 * 60)
            self.assertEqual(self.hud_clock(g), "Day 1, Dusk 18:00")
            self.assertEqual(r8.on_disk(self.folder)["world"]["inn"], {"map": TOWN, "spawn": explore.INN_SPAWN})
        with self.step(3, "Marta at dusk: Sleep until morning - day 2"):
            said, conv = self.talk_to(g, talk.INNKEEPER, answer=True)
            self.assertEqual((conv.choice[0], conv.rest_until), (talk.REST_YES, "morning"))
            self.assertEqual(clock.now(w), clock.DAY + 7 * 60)
            self.assertEqual(self.hud_clock(g), "Day 2, Day 07:00")
        with self.step(4, "a rest until dusk on day 2, then out of the gate"):
            self.talk_to(g, talk.INNKEEPER, answer=True)
            self.assertEqual(clock.now(w), clock.DAY + 18 * 60)
            self.through(g, OUT, "from_town")
            self.assertTrue(self.hud_clock(g).startswith("Day 2, Dusk 18:"), self.hud_clock(g))
        with self.step(5, "a duel lost at dusk: you wake at the inn at the same hour"):
            ex = g.modal.explorer
            foe = min(ex.encounters(), key=lambda fo: math.hypot(fo.x - ex.walker.x, fo.y - ex.walker.y))
            spot = foe.spot
            self.walk(g, lambda: self.foe_tile(g, spot), until=lambda fl: fl.sub == "fight" and getattr(fl.fight, "spot", None) == spot)
            before = clock.now(g.modal.game.save.world)
            f = self.duel(g, won=False)
            self.ok(g)
            self.say_all(g)                                       # Marta's words when you wake
            f = g.modal
            self.assertEqual(f.explorer.area.name, TOWN)
            self.assertEqual(clock.now(f.game.save.world), before, "the loss moved the clock")
            self.assertEqual(clock.phase(before), "dusk")
            self.assertTrue(self.hud_clock(g).startswith("Day 2, Dusk 18:"), self.hud_clock(g))

    def test_bandits_beaten_before_the_lamplighters_debt_can_still_be_fought_for_it(self):
        """A player who meets the Ember Bandits on the road first (they stand on it) and only then talks to Pell: back in town, a
        rest at the inn, the quest taken, out of the gate again - there must be three bandits to beat for it."""
        r8.new_game(None)
        g = self.gui(launcher=self.launcher)
        f = self.open(g, slot=1)
        self.press(g, "leave_town")
        with self.step(1, "out of the gate, three Ember Bandits beaten before the quest"):
            self.through(g, OUT, "from_town")
            for _ in range(3):
                ex = g.modal.explorer
                foe = min((fo for fo in ex.encounters() if fo.enemy == "ember_bandit"),
                          key=lambda fo: math.hypot(fo.x - ex.walker.x, fo.y - ex.walker.y))
                spot = foe.spot
                self.walk(g, lambda: self.foe_tile(g, spot),
                          until=lambda fl: fl.sub == "fight" and getattr(fl.fight, "enemy", None) == "ember_bandit")
                self.duel(g, won=True)
                self.ok(g)
            self.assertEqual([fo for fo in g.modal.explorer.encounters() if fo.enemy == "ember_bandit"], [])
        with self.step(2, "back to Lantern Hollow: a rest at the inn, Pell's quest taken"):
            self.through(g, TOWN, "from_outskirts")
            self.talk_to(g, talk.INNKEEPER, answer=True)  # Round RPG15c (expected change): she asks; Sleep until morning
            self.talk_to(g, "lamplighter", answer=True)
            self.assertEqual(talk.quest_state(g.modal.game.save.world, "lamplighters_debt"), "active")
        with self.step(3, "out of the gate again: the bandits for the quest"):
            self.through(g, OUT, "from_town")
            standing = [fo.spot for fo in g.modal.explorer.encounters() if fo.enemy == "ember_bandit"]
            self.assertGreaterEqual(
                len(standing), 3,
                f"only {len(standing)} Ember Bandits stand on the outskirts after walking into Lantern Hollow, resting at the inn "
                f"and taking the Lamplighter's Debt, which needs three wins from now on. Beaten foes come back only when a shop "
                f"counter opens the town window (rpg.duel.visit_town, called from AdventureFlow.enter_town), though the encounter "
                f"screen says \"{'Beaten, it comes back after your next visit to a town.'}\"")


    def test_a_loss_before_any_rest_at_the_inn_leaves_a_way_on(self):
        """No rest at Marta's yet: a lost duel sends you to the last town as before RPG14 (rpg.duel.finish) - Lantern Hollow's
        shop window. From there you must be able to walk out of the gate again."""
        r8.new_game(None)
        g = self.gui(launcher=self.launcher)
        self.open(g, slot=1)
        self.press(g, "leave_town")
        with self.step(1, "out of the gate, a duel lost before resting anywhere"):
            self.assertNotIn("inn", g.modal.game.save.world)
            self.through(g, OUT, "from_town")
            ex = g.modal.explorer
            foe = min(ex.encounters(), key=lambda fo: math.hypot(fo.x - ex.walker.x, fo.y - ex.walker.y))
            spot = foe.spot
            self.walk(g, lambda: self.foe_tile(g, spot), until=lambda fl: fl.sub == "fight" and fl.fight.spot == spot)
            gold = g.modal.game.save.gold
            frame(g)
            self.press(g, "fight")
            g.vs = None
            frame(g, 2)
            r8.end_game(g, won=False)
            g.end_continue()
            frame(g)
            f = g.modal
            self.assertEqual((f.stage, f.sub, f.result[0]), ("town", "result", "Defeat"))
            lost = int(gold * f.game.numbers.loss_gold_share)
            self.assertEqual(f.game.save.gold, gold - lost)
            self.assertEqual(f.result[1][-1], f"You lost {lost} gold and are back in Lantern Hollow.")
        with self.step(2, "out of the shop window and back through the gate"):
            self.ok(g)
            frame(g)
            self.press(g, "leave_town")
            self.assertEqual(g.modal.stage, "map")
            self.assertEqual(self.here(g)[0], TOWN)
            self.through(g, OUT, "from_town")


class PeopleTests(unittest.TestCase):
    """Where each person of Lantern Hollow can be talked to from (the map and Explorer.facing_npc's rule, no window)."""

    def setUp(self):
        explore.forget_areas()
        self.area = explore.load_area(TOWN)
        self.start = self.area.tile_at(*self.area.spawn("start"))
        self.avoid = rpg_route.trigger_tiles(self.area)

    def reachable(self, tile):
        return tile not in self.avoid and rpg_route.tile_route(self.area, self.start, tile, avoid=self.avoid - {tile}) is not None

    def check_spot(self, tile, facing, nid):
        a = self.area
        w = explore.Walker(a, *rpg_route.middle(a, tile))
        w.facing = facing

        class Ex:                                                  # just what facing_npc reads
            walker, area = w, a
        return explore.Explorer.facing_npc(Ex())

    def test_everyone_can_be_talked_to_from_where_a_player_stands(self):
        """Next to them or across a counter, on a tile you can walk to without stepping on a trigger (a shop's counter opens the
        shop's window as you step on it, so you can never stand there facing the shopkeeper)."""
        bad = []
        for person in self.area.people:                     # Round RPG16: every place of every person
            nid, at = person["id"], person["tile"]
            spots = [(t, fc, how) for t, fc, how in talk_spots(self.area, nid, at) if how != "wall" and self.reachable(t)]
            for t, fc, _how in spots:
                self.assertEqual(self.check_spot(t, fc, nid), nid)
            if not spots:
                on_trigger = [(t, fc) for t, fc, how in talk_spots(self.area, nid, at) if how != "wall" and t in self.avoid]
                bad.append(f"{self.area.npcs[nid]['name']} ({nid}): no tile next to them or across a counter that a player can "
                           f"stand on" + (f"; the one across the counter, {on_trigger[0][0]}, is the shop's own trigger"
                                          if on_trigger else ""))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_nobody_is_talked_to_through_a_wall(self):
        bad = []
        for person in self.area.people:                     # Round RPG16: every place of every person
            nid = person["id"]
            for t, fc, how in talk_spots(self.area, nid, person["tile"]):
                if how == "wall" and self.reachable(t) and self.check_spot(t, fc, nid) == nid:
                    bad.append(f"{self.area.npcs[nid]['name']} ({nid}) is talked to through a wall from {t} facing {fc}")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
