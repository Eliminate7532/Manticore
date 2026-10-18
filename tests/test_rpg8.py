# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG8: the Adventure - the RPG mode's first playable loop (Karl, 13 Oct 2026: "First playable loop": main menu > Adventure >
new game > walk the map > duel an enemy on the real table > gold and cards > the town's shop > the deck editor > save).

Offline: the logic (rpg/adventure.py - slots, a new game written at once, the fight's words, a duel marked before it starts and
settled from its result, the deck editor's rules), and the window (rpg_screens.py) driven headlessly through the real table: the
main menu's Adventure, a new game, the town and its shop, the deck editor, walking the map, a fight started on the table with a
stand-in engine, its result in the save the moment the game knows it, and back to the Adventure. Every screen fits from 1024x640
to 4096x2019 at 175% and at 200% text.
Live (real Forge): a duel from the Adventure plays on the real engine as Standard - 20 life each, 60-card decks, the AI named after
the enemy - and conceding it costs the gold a loss costs."""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_client as fc
import forge_table as ft
import rpg_screens as rs
from rpg import adventure as adv
from rpg import cardpool, duel, explore, newgame, rules
from rpg import save as sv
from tests import live, rpg_route
from tests.forge_fake import FakeSession, StubStore
from tests.test_forge_table import click, frame, key, load_log, load_state

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("FORGE_RUNTIME") or os.path.join(BASE, "forge_runtime")
HAS_RUNTIME = os.path.isfile(os.path.join(RUNTIME, rules.VINTAGE_FILE))
SIZES = (((1024, 640), 1.0), ((1100, 700), 2.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0),
         ((1920, 1080), 2.0), ((4096, 2019), 1.75))
FRAME = 1 / 60


def new_game(folder, slot=1, seed=7, name="Karl"):
    """A game made the way the new-game screen makes it (rpg.newgame), written to `folder`."""
    f = newgame.NewGameFlow(seed=seed)
    f.set_name(name)
    for _ in range(newgame.PICKS):
        offer = f.offer()                    # Round RPG9: the offers changed (two colours); a 20-card half keeps the deck at 60
        f.pick(next((h for h in offer if h.size == 20), offer[0]).code)
    return adv.Game.create(f, slot, folder)


def on_disk(folder, slot=1):
    with open(sv.save_path(slot, folder), encoding="utf-8") as fh:
        return json.load(fh)


class Launcher:
    """Stands in for ft.Launcher: no Java; hands back a session showing a saved snapshot, and keeps what it was asked."""
    runtime = None

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def start(self, mine, opponents, fmt=None, rules=(), names=None):
        self.calls.append({"mine": mine, "opponents": opponents, "fmt": fmt, "names": names})
        if self.fail:
            raise RuntimeError(self.fail)
        return FakeSession(copy.deepcopy(load_state("main1_start")), load_log())


def end_game(gui, won=True, conceded=False):
    """The engine says the game is over (Forge's game_over with its result, as the bridge sends it - patch 44)."""
    s = gui.session
    st = copy.deepcopy(gui.state)
    me = st["me"]
    other = next(p for p in st["players"] if p["id"] != me)
    st["gameOver"] = True
    st["winner"] = (st["players"][0] if st["players"][0]["id"] == me else other)["name"] if won else other["name"]
    s.handle(st)
    rows = [{"id": p["id"], "name": p["name"], "won": (p["id"] == me) == won,
             "loss": None if (p["id"] == me) == won else ("Conceded" if conceded else "LifeReachedZero")} for p in st["players"]]
    s.handle({"t": "game_over", "result": {"players": rows, "lastTurn": st.get("turn", 1)}})
    frame(gui, 3)


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (the Jumpstart lists, Vintage's lists)")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rpg8_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = os.path.join(self.tmp, "rpg_saves")
        folder = self.folder
        self.enterContext(mock.patch.object(sv, "saves_dir", lambda: folder))     # every save of the test goes here
        self.pool = cardpool.load()

    def gui(self, size=(1360, 840), scale=None, launcher=None):
        g = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=size,
                          launcher=launcher if launcher is not None else Launcher())
        if scale is not None:
            g.text_scale = scale
        self.addCleanup(self.close, g)
        g.open_boot(splash=False)
        frame(g, 2)
        return g

    @staticmethod
    def close(g):
        with mock.patch.object(ft.pygame, "quit"):              # pygame stays up for the next test
            g.shutdown()

    def open(self, g, slot=None):
        """The Adventure window from the main menu (its Adventure item)."""
        for rect, name in g.boot.buttons:
            if name == "adventure":
                click(g, rect.center)
                break
        else:
            self.fail(f"no Adventure on the main menu: {[n for _r, n in g.boot.buttons]}")
        self.assertIsInstance(g.modal, rs.AdventureFlow)
        if slot is not None:
            g.modal.open_slot(g, slot)
            frame(g)
        return g.modal

    def press(self, g, name):
        for rect, n in reversed(g.modal.buttons):
            if n == name:
                click(g, rect.center)
                return
        self.fail(f"no button {name!r} on {g.modal.stage}/{g.modal.sub}: {[n for _r, n in g.modal.buttons]}")

    def names(self, g):
        return [n for _r, n in g.modal.buttons]

    def walk_to(self, g, goal, limit=6000):
        """Walk the window's map (one key at a time, 1/60 s a frame) until something opens over it or the stage changes."""
        f = g.modal
        t = time.monotonic()
        for _ in range(limit):
            if f.sub is not None or f.stage != "map":
                frame(g)                                        # drawn, as the real loop draws after each tick
                return
            press = rpg_route.next_press(f.explorer, goal)
            t += FRAME
            f.walk_tick(g, t, pressed=press or (0, 0))
            if press is None:
                frame(g)
                return
        self.fail("didn't get there")

    def start_fight(self, launcher=None):
        new_game(None)
        launcher = launcher or Launcher()
        g = self.gui(launcher=launcher)
        f = self.open(g, slot=1)
        self.press(g, "leave_town")
        foe, goal = self.foe_goal(f)
        self.walk_to(g, goal)
        self.assertEqual(f.sub, "fight")
        return g, f, foe

    def foe_goal(self, f, kind=None):
        ex = f.explorer
        foes = [fo for fo in ex.room_foes() + ex.encounters() if kind is None or fo.t.enemy == kind]
        foe = sorted(foes, key=lambda fo: abs(fo.x - ex.walker.x) + abs(fo.y - ex.walker.y))[0]
        return foe, (lambda: f.explorer.area.tile_at(foe.x, foe.y))


# ==================================================================================================================================
class LogicTests(Base):
    def test_slots_list_every_slot_and_a_new_game_is_written_at_once(self):
        rows = adv.slot_rows()
        self.assertEqual([r["slot"] for r in rows], [1, 2, 3])                       # numbers.json save_slots
        self.assertFalse(any(r["used"] for r in rows))
        g = new_game(None, slot=2)
        d = on_disk(self.folder, 2)
        self.assertEqual((d["name"], d["gold"], d["active_deck"]), ("Karl", g.numbers.start_gold, 0))
        self.assertEqual(sum(k for _n, k in d["decks"][0]["main"]), 60)
        self.assertEqual([r["used"] for r in adv.slot_rows()], [False, True, False])

    def test_a_duel_left_going_on_does_not_count_and_the_load_says_so(self):
        g = new_game(None)
        gold = g.save.gold
        g.save.pending_duel = {"kind": "thornback", "encounter": "regular", "deck": 0}
        g.write()
        g2, notes = adv.Game.load(1)
        self.assertIsNone(g2.save.pending_duel)
        self.assertEqual(g2.save.gold, gold)
        self.assertTrue(any("didn't finish" in n for n in notes))
        self.assertIsNone(on_disk(self.folder)["pending_duel"])                    # written back at once

    def trigger(self, g, encounter="regular"):
        for area_name in explore.map_names():
            area = explore.load_area(area_name)
            for t in area.triggers:
                if t.kind == "encounter" and t.encounter == encounter:
                    return t
        self.fail("no such encounter")

    def test_the_fight_question_says_what_a_win_gives_and_a_loss_costs(self):
        g = new_game(None)
        t = self.trigger(g)
        title, lines = adv.fight_text(g, t)
        self.assertEqual(title, adv.enemy_name(t.enemy))
        lo, hi = g.numbers.rewards["regular"]["gold"]
        self.assertIn(f"Win: {lo}-{hi} gold and 1 card.", lines)
        self.assertIn(f"Lose: {g.numbers.loss_cost(g.save.gold)} of your {g.save.gold} gold, and back to Town 1.", lines)
        self.assertTrue(any("comes back" in ln for ln in lines))                    # a regular foe isn't "once"
        boss = self.trigger(g, "boss")
        title, lines = adv.fight_text(g, boss)
        self.assertIn("a boss", title)
        self.assertFalse(any("comes back" in ln for ln in lines))
        g.save.decks[0]["main"] = g.save.decks[0]["main"][:3]                      # a deck that can't be played is said
        self.assertTrue(any(ln.startswith("Your deck can't be played yet") for ln in adv.fight_text(g, t)[1]))

    def test_a_duel_is_marked_on_disk_before_it_starts_and_settled_from_its_result(self):
        g = new_game(None)
        t = self.trigger(g)
        d = adv.start_duel(g, t)
        self.assertEqual(on_disk(self.folder)["pending_duel"]["kind"], t.enemy)    # written before the game starts
        self.assertEqual((d.format, len(adv.deck_cards(d.player_main())), len(adv.deck_cards(d.enemy_main()))), ("standard", 60, 60))
        gold = g.save.gold
        out = adv.finish_duel(1, "won")
        disk = on_disk(self.folder)
        self.assertEqual((out["result"], disk["gold"]), ("won", gold + out["gold"]))
        self.assertGreater(out["gold"], 0)
        self.assertEqual(len(out["cards"]), 1)
        self.assertIn(out["cards"][0][0], disk["collection"])
        self.assertIsNone(disk["pending_duel"])
        self.assertIn(t.spot, disk["world"]["beaten"])
        self.assertEqual(adv.outcome_lines(out)[0], f"You beat the {out['enemy']}: +{out['gold']} gold.")

    def test_a_loss_or_a_concede_costs_gold_and_sends_you_to_the_last_town_and_a_draw_costs_nothing(self):
        for result in ("lost", "conceded", "draw"):
            g = new_game(None)
            gold = g.save.gold
            adv.start_duel(g, self.trigger(g))
            out = adv.finish_duel(1, result)
            disk = on_disk(self.folder)
            if result == "draw":
                self.assertEqual((disk["gold"], disk["record"]["draws"], out["moved_to"]), (gold, 1, None))
                self.assertIn("nothing won or lost", adv.outcome_lines(out)[0])
            else:
                cost = g.numbers.loss_cost(gold)
                self.assertEqual((disk["gold"], disk["record"]["losses"]), (gold - cost, 1))
                self.assertEqual((disk["world"]["map"], disk["world"]["x"]), (g.world.start_town, None))
                lines = adv.outcome_lines(out, g.world)
                self.assertEqual(lines[1], f"You lost {cost} gold and are back in Town 1.")
                self.assertTrue(lines[0].startswith("You conceded" if result == "conceded" else "The "))

    def test_an_unfinished_duel_is_not_settled_and_nothing_is_settled_twice(self):
        g = new_game(None)
        adv.start_duel(g, self.trigger(g))
        self.assertIsNone(adv.finish_duel(1, "unfinished"))
        self.assertIsNotNone(on_disk(self.folder)["pending_duel"])                 # still marked: the next load says so
        self.assertIsNotNone(adv.finish_duel(1, "lost"))
        gold = on_disk(self.folder)["gold"]
        self.assertIsNone(adv.finish_duel(1, "lost"))                              # nothing going on any more
        self.assertEqual(on_disk(self.folder)["gold"], gold)
        with self.assertRaises(duel.DuelError):
            adv.finish_duel(1, "maybe")

    def test_a_save_that_fails_as_the_duel_starts_takes_the_mark_off(self):
        g = new_game(None)
        t = self.trigger(g)
        with mock.patch.object(sv, "write", side_effect=PermissionError("the file is in use")):
            with self.assertRaises(OSError):
                adv.start_duel(g, t)
        self.assertIsNone(g.save.pending_duel)                                      # in memory too: a later Fight can start
        self.assertIsNotNone(adv.start_duel(g, t))
        self.assertEqual(on_disk(self.folder)["pending_duel"]["kind"], t.enemy)

    def test_cancel_takes_the_mark_off(self):
        g = new_game(None)
        adv.start_duel(g, self.trigger(g))
        adv.cancel_duel(g)
        self.assertIsNone(on_disk(self.folder)["pending_duel"])

    def test_the_duel_record_has_its_own_folder(self):
        self.assertEqual(adv.duel_stats_dir(), os.path.join(self.folder, "duel_stats"))
        self.assertEqual(adv.duel_stats_dir("/x"), os.path.join("/x", "duel_stats"))
        self.assertEqual(adv.deck_cards([("A", 2), ("B", 1)]), ["A", "A", "B"])


class EditorTests(Base):
    def editor(self):
        g = new_game(None)
        return g, adv.DeckEditor(g)

    def test_a_card_goes_in_only_while_you_own_another_copy_and_the_rules_allow_it(self):
        g, e = self.editor()
        name = e.collection_rows()[0].name
        self.assertEqual(e.in_deck(name), g.save.collection.count(name))           # a new game's deck uses every card
        self.assertIn("already uses", e.why_not(name))
        self.assertFalse(e.add(name))
        g.save.collection.add(name, 5)
        for _ in range(rules.FOUR - e.in_deck(name)):
            self.assertTrue(e.add(name))
        self.assertIn("may hold 4", e.why_not(name))                               # four of a card
        self.assertFalse(e.add(name))
        self.assertTrue(e.remove(name))
        self.assertTrue(e.changed)

    def test_restricted_banned_and_basic_lands(self):
        g, e = self.editor()
        lists = g.lists()
        restricted = sorted(lists.restricted.values())[0]
        g.save.collection.add(restricted, 2)
        self.assertTrue(e.add(restricted))
        self.assertIn("restricted", e.why_not(restricted))
        if lists.banned:
            banned = sorted(lists.banned.values())[0]
            if g.pool.get(banned) is not None:
                g.save.collection.add(banned, 1)
                self.assertIn("banned", e.why_not(banned))
        g.save.collection.add("Forest", 20)                                         # basics: any number, but owned
        have = g.save.collection.count("Forest")
        for _ in range(have + 50):                                                  # bounded: a missing limit fails, never hangs
            if not e.add("Forest"):
                break
        self.assertEqual(e.in_deck("Forest"), have)

    def test_done_writes_the_deck_and_it_becomes_the_one_you_duel_with(self):
        g, e = self.editor()
        name = e.collection_rows()[0].name
        e.remove(name)
        left = e.apply()
        self.assertEqual(left[0].kind, "size")                                     # 59 cards: kept, and said
        disk = on_disk(self.folder)
        self.assertEqual(sum(k for _n, k in disk["decks"][0]["main"]), 59)
        self.assertFalse(e.changed)

    def test_filters_and_search(self):
        g, e = self.editor()
        allrows = e.collection_rows()
        e.set_group("Land")
        self.assertTrue(e.collection_rows())
        self.assertTrue(all(r.group == "Land" for r in e.collection_rows()))
        e.set_group("Land")                                                          # again: off
        self.assertEqual(len(e.collection_rows()), len(allrows))
        letter = next(c for r in allrows for c in (r.card.colours if r.card else "") if c in "WUBRG")
        e.toggle_colour(letter)
        self.assertTrue(all(letter in r.card.colours for r in e.collection_rows()))
        e.toggle_colour(letter)
        e.search = allrows[0].name[:5]
        self.assertIn(allrows[0].name, [r.name for r in e.collection_rows()])
        e.search = "zzzz no card"
        self.assertEqual(e.collection_rows(), [])

    def test_deck_rows_by_type(self):
        g, e = self.editor()
        groups = [gname for gname, _items in e.deck_rows()]
        self.assertEqual(groups, [x for x in adv.TYPE_ORDER if x in groups])
        self.assertEqual(sum(k for _g, items in e.deck_rows() for _n, k, _c in items), 60)


# ==================================================================================================================================
class WindowTests(Base):
    def test_the_main_menu_has_adventure_in_continues_old_place(self):
        g = self.gui()
        names = [n for _r, n in g.boot.buttons]
        self.assertEqual(names[:3], ["play", "draft", "adventure"])
        f = self.open(g)
        self.assertEqual(f.stage, "slots")
        self.assertEqual([n for n in self.names(g) if n.startswith("new:")], ["new:1", "new:2", "new:3"])
        key(g, pygame.K_ESCAPE)                                                      # back to the main menu
        frame(g)
        self.assertIsNone(g.modal)
        self.assertEqual(g.boot.stage, "menu")

    def test_a_new_game_from_the_window(self):
        g = self.gui()
        f = self.open(g)
        self.press(g, "new:1")
        self.assertEqual(f.stage, "name")
        self.press(g, "next")                                                        # no name
        self.assertEqual((f.stage, f.message[1]), ("name", rs.ORANGE))
        for ch in "Karl":
            key(g, getattr(pygame, "K_" + ch.lower()), unicode=ch)
        key(g, pygame.K_RETURN)
        self.assertEqual(f.stage, "pick")
        first = f.flow.offer()[0].code
        click(g, next(r for r, k, d in f.rows if k == "pick" and d == first).center)
        self.assertEqual(len(f.flow.picks), 1)
        self.press(g, "back")                                                        # undo the pick
        self.assertEqual(len(f.flow.picks), 0)
        before = [h.code for h in f.flow.offer()]
        self.press(g, "reroll")
        self.assertNotEqual([h.code for h in f.flow.offer()], before)
        for _ in range(3):
            click(g, next(r for r, k, d in f.rows if k == "pick").center)
        self.assertEqual(f.stage, "ready")
        self.assertFalse(os.path.exists(sv.save_path(1, self.folder)))               # nothing written before Begin
        self.press(g, "begin")
        self.assertEqual((f.stage, f.sub), ("town", "result"))                       # the start town, with a welcome
        d = on_disk(self.folder)
        self.assertEqual((d["name"], d["world"]["in_town"]), ("Karl", "town_1"))
        self.press(g, "ok")
        self.assertIsNone(f.sub)

    def test_replacing_a_used_slot_asks_first(self):
        new_game(None, name="Old")
        g = self.gui()
        f = self.open(g)
        self.press(g, "new:1")
        self.assertEqual(f.sub, "confirm")
        self.press(g, "no")
        self.assertEqual((f.sub, f.stage), (None, "slots"))
        self.press(g, "new:1")
        self.press(g, "yes")
        self.assertEqual(f.stage, "name")
        self.assertEqual(on_disk(self.folder)["name"], "Old")                       # still there until Begin

    def test_continue_opens_the_game_where_it_was(self):
        new_game(None)
        g = self.gui()
        f = self.open(g)
        self.press(g, "slot:1")
        self.assertEqual(f.stage, "town")                                            # a new game starts in the town
        self.press(g, "leave_town")
        self.assertEqual(f.stage, "map")
        self.assertNotIn("in_town", on_disk(self.folder)["world"])
        key(g, pygame.K_ESCAPE)
        self.assertEqual(f.sub, "pause")
        self.press(g, "leave")
        frame(g)
        self.assertIsNone(g.modal)
        w = on_disk(self.folder)["world"]
        f2 = self.open(g, slot=1)
        self.assertEqual(f2.stage, "map")
        self.assertEqual(f2.explorer.walker.resting_place(), (w["x"], w["y"]))

    def test_the_shop_buys_and_sells_and_every_change_is_written_at_once(self):
        new_game(None)
        g = self.gui()
        f = self.open(g, slot=1)
        gold = f.game.save.gold
        offer = f.shop.singles()[0]
        self.press(g, f"buy:{offer.slot}")
        d = on_disk(self.folder)
        self.assertEqual(d["gold"], gold - offer.price)
        self.assertGreaterEqual(d["collection"].get(offer.name, 0), 1)
        self.press(g, "tab:lands")
        self.press(g, "basic:Forest:5")
        self.assertEqual(on_disk(self.folder)["gold"], gold - offer.price - 5 * f.game.numbers.basic_land_price)
        f.game.save.gold = 1
        offer2 = next(o for o in f.shop.singles() if o.price > 1 and o.left)
        f.tab = "singles"
        frame(g)
        names = self.names(g)
        self.assertNotIn(f"buy:{offer2.slot}", names)                                # greyed out: not a button
        f.shop_do(g, "buy", offer2.slot)
        self.assertIn("costs", f.message[0])
        f.game.save.gold = 500
        self.press(g, "tab:packs")
        code = f.shop.packs()[0][0]
        self.press(g, f"pack:{code}")
        self.assertEqual(f.stage, "pack")
        self.assertTrue(f.opened[1])
        self.assertEqual(on_disk(self.folder)["gold"], 500 - f.game.numbers.pack_price)
        self.press(g, "pack_done")
        self.press(g, "tab:sell")
        rows = f.sell_list()
        needed = next(r for r in rows if r[3] >= r[1])                                # the deck uses every copy: refused
        self.assertNotIn(f"sell:{needed[0]}", self.names(g))
        f.shop_do(g, "sell", needed[0])
        self.assertIn("take them out first", f.message[0])
        spare = next(r for r in rows if r[1] > r[3] and r[2] >= 1)
        before = on_disk(self.folder)["gold"]
        f.shop_do(g, "sell", spare[0])
        self.assertEqual(on_disk(self.folder)["gold"], before + spare[2])

    def test_the_deck_editor_through_the_window(self):
        new_game(None)
        g = self.gui()
        f = self.open(g, slot=1)
        f.game.save.collection.add("Forest", 3)
        self.press(g, "town_deck")
        self.assertEqual(f.stage, "deck")
        line = next((r, d) for r, k, d in f.rows if k == "deckline")
        click(g, line[0].center)                                                     # one less
        self.assertEqual(f.editor.total(), 59)
        key(g, pygame.K_f, unicode="F")
        key(g, pygame.K_o, unicode="o")
        key(g, pygame.K_r, unicode="r")
        self.assertEqual(f.editor.search, "For")
        tile = next(r for r, k, d in f.rows if k == "pool" and d == "Forest")
        click(g, tile.center)                                                        # one more Forest
        self.assertEqual(f.editor.total(), 60)
        click(g, tile.center, button=3)                                              # right-click: one less
        self.assertEqual(f.editor.total(), 59)
        key(g, pygame.K_ESCAPE)                                                      # clears the search first
        self.assertEqual((f.editor.search, f.stage), ("", "deck"))
        self.press(g, "deck_cancel")                                                 # changes thrown away
        self.assertEqual(sum(k for _n, k in on_disk(self.folder)["decks"][0]["main"]), 60)
        self.assertEqual(f.stage, "town")
        self.press(g, "town_deck")
        click(g, next(r for r, k, d in f.rows if k == "deckline").center)
        self.press(g, "deck_done")
        self.assertEqual(sum(k for _n, k in on_disk(self.folder)["decks"][0]["main"]), 59)
        self.assertIn("can't be played yet", f.message[0])

    def test_walking_into_the_town_gate_opens_the_town(self):
        new_game(None)
        g = self.gui()
        f = self.open(g, slot=1)
        self.press(g, "leave_town")
        ex = f.explorer
        gate = next(t for t in ex.area.triggers if t.kind == "town")
        x0, y0, x1, y1 = gate.box
        tile = ex.area.tile_at((x0 + x1) / 2, (y0 + y1) / 2)
        start = ex.walker.resting_place()
        rpg_route.walk_to_tile(ex, (tile[0] + 2, tile[1] + 3) if not ex.area.blocked(tile[0] + 2, tile[1] + 3) else
                               ex.area.tile_at(*start))
        self.walk_to(g, tile)
        self.assertEqual(f.stage, "town")

    def test_a_fight_starts_on_the_table_and_its_result_is_saved_before_continue(self):
        new_game(None)
        launcher = Launcher()
        g = self.gui(launcher=launcher)
        f = self.open(g, slot=1)
        self.press(g, "leave_town")
        foe, goal = self.foe_goal(f)
        self.walk_to(g, goal)
        self.assertEqual(f.sub, "fight")
        self.press(g, "no")                                                         # Not now
        self.assertIsNone(f.sub)
        self.walk_to(g, goal)
        if f.sub is None:                                                            # standing in it: step out and back in
            ex = f.explorer
            here = ex.area.tile_at(ex.walker.x, ex.walker.y)
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if not ex.area.blocked(here[0] + 2 * d[0], here[1] + 2 * d[1]):
                    rpg_route.walk_to_tile(ex, (here[0] + 2 * d[0], here[1] + 2 * d[1]))
                    break
            self.walk_to(g, goal)
        self.assertEqual(f.sub, "fight")
        gold = f.game.save.gold
        self.press(g, "fight")
        call = launcher.calls[-1]
        self.assertEqual((call["fmt"], call["names"], len(call["mine"][1]), len(call["opponents"][0][1])),
                         ("standard", [adv.enemy_name(foe.t.enemy)], 60, 60))
        self.assertEqual((call["mine"][0], call["opponents"][0][0]), ([], []))      # no commander
        self.assertIsNone(g.modal)                                                   # the table
        self.assertEqual(on_disk(self.folder)["pending_duel"]["kind"], foe.t.enemy)
        self.assertEqual(g.adventure_duel["slot"], 1)
        self.assertFalse(g.can_restart())                                            # no Play again for an Adventure duel
        info = g.session.stats_info
        self.assertEqual(info["folder"], adv.duel_stats_dir())
        g.vs = None
        frame(g, 2)
        end_game(g, won=True)
        disk = on_disk(self.folder)                                                  # settled before Continue is pressed
        self.assertIsNone(disk["pending_duel"])
        self.assertGreater(disk["gold"], gold)
        self.assertTrue(g.end_stats_lines()[0].startswith("You beat the"))
        self.assertTrue(os.path.isfile(os.path.join(adv.duel_stats_dir(), "games.ndjson")))
        g.end_continue()
        frame(g)
        f2 = g.modal
        self.assertIsInstance(f2, rs.AdventureFlow)
        self.assertEqual((f2.stage, f2.sub, f2.result[0]), ("map", "result", "Victory"))
        self.assertIsNone(g.adventure_duel)
        self.assertIn(foe.spot, f2.game.save.world["beaten"])
        self.assertNotIn(foe.spot, [x.spot for x in f2.explorer.encounters()])

    def test_a_lost_duel_sends_you_to_the_town_and_says_so(self):
        g, f, foe = self.start_fight()
        gold = f.game.save.gold
        self.press(g, "fight")
        g.vs = None
        end_game(g, won=False)
        cost = f.game.numbers.loss_cost(gold)
        self.assertEqual(on_disk(self.folder)["gold"], gold - cost)
        town = f.game.world.town(f.game.world.start_town).name                     # the end screen names the town,
        self.assertIn(f"You lost {cost} gold and are back in {town}.", g.end_stats_lines())   # never its id
        g.end_continue()
        frame(g)
        f2 = g.modal
        self.assertEqual((f2.stage, f2.sub, f2.result[0]), ("town", "result", "Defeat"))
        self.assertIn(f"You lost {cost} gold", " ".join(f2.result[1]))

    def test_conceding_to_the_main_menu_settles_a_loss(self):
        g, f, foe = self.start_fight()
        gold = f.game.save.gold
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        g.concede_to_title()
        frame(g, 2)
        disk = on_disk(self.folder)
        self.assertIsNone(disk["pending_duel"])
        self.assertEqual((disk["gold"], disk["record"]["losses"]), (gold - f.game.numbers.loss_cost(gold), 1))

    def test_conceding_and_closing_the_program_settles_a_loss(self):
        g, f, foe = self.start_fight()
        gold = f.game.save.gold
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        g.concede_and_close()                                                        # Cog > Concede... > Concede and close
        self.assertFalse(g.running)
        self.close(g)                                                                # run() ends and shutdown() follows
        disk = on_disk(self.folder)
        self.assertIsNone(disk["pending_duel"])
        self.assertEqual((disk["gold"], disk["record"]["losses"]), (gold - f.game.numbers.loss_cost(gold), 1))

    def test_closing_the_program_mid_duel_leaves_it_marked_and_it_does_not_count(self):
        g, f, foe = self.start_fight()
        gold = f.game.save.gold
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        self.close(g)                                                                # the window's close button, mid-game
        self.assertIsNotNone(on_disk(self.folder)["pending_duel"])                   # "unfinished": not settled
        g2, notes = adv.Game.load(1)
        self.assertTrue(any("didn't finish" in n for n in notes))
        self.assertEqual(g2.save.gold, gold)

    def test_a_duel_left_for_a_new_game_does_not_count(self):
        g, f, foe = self.start_fight()
        gold = f.game.save.gold
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        g.open_menu()                                                                # the deck screen, as Cog > New game would
        g.begin(([], ["Forest"] * 60), [([], ["Forest"] * 60)], fmt="standard")       # another game starts
        frame(g, 3)
        self.assertIsNotNone(on_disk(self.folder)["pending_duel"])                   # not settled ("unfinished")
        g2, notes = adv.Game.load(1)
        self.assertTrue(any("didn't finish" in n for n in notes))
        self.assertEqual(g2.save.gold, gold)

    def test_an_engine_that_cannot_start_takes_the_mark_off_and_says_why(self):
        g, f, foe = self.start_fight(Launcher(fail="Java is missing"))
        self.press(g, "fight")
        self.assertIs(g.modal, f)
        self.assertEqual((f.sub, f.result[0]), ("result", "No duel"))
        self.assertIn("Java is missing", " ".join(f.result[1]))
        self.assertIsNone(on_disk(self.folder)["pending_duel"])
        self.assertIsNone(g.adventure_duel)

    def test_a_deck_that_cannot_be_played_starts_no_duel(self):
        g, f, foe = self.start_fight()
        f.game.save.decks[0]["main"] = f.game.save.decks[0]["main"][:5]
        self.press(g, "fight")
        self.assertEqual((f.sub, f.result[0]), ("result", "No duel"))
        self.assertEqual(g.launcher.calls, [])
        self.assertIsNone(on_disk(self.folder)["pending_duel"])

    def test_closing_the_program_on_the_map_keeps_where_you_stand(self):
        new_game(None)
        g = self.gui()
        f = self.open(g, slot=1)
        self.press(g, "leave_town")
        t = time.monotonic()
        for _ in range(40):
            t += FRAME
            f.walk_tick(g, t, pressed=(0, 1))
        here = f.explorer.walker.resting_place()
        self.close(g)
        w = on_disk(self.folder)["world"]
        self.assertEqual((w["x"], w["y"]), here)

    def test_the_music_follows_the_screen_and_the_map_is_drawn_at_full_speed(self):
        new_game(None)
        g = self.gui()
        f = self.open(g)
        self.assertEqual(g.soundscape(), ("amb.title", "title"))
        f.open_slot(g, 1)
        frame(g)
        self.assertEqual(g.soundscape(), ("amb.cathedral", "deck"))
        self.press(g, "leave_town")
        self.assertEqual(g.soundscape(), ("amb.plain", "game"))
        self.assertTrue(g.activity(time.monotonic())[0])        # no game on the table: drawn at full speed (the map walks)


class ReviewFixTests(Base):
    """What the independent review of 13-14 Oct found (docs/rounds/RPG8.md, "A review by a separate agent"), each red on the
    round as first built."""

    def fight(self, launcher=None):
        g, f, foe = self.start_fight(launcher)
        gold = f.game.save.gold
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        return g, f, foe, gold

    def test_conceding_to_the_main_menu_forgets_the_duel(self):
        g, f, foe, gold = self.fight()
        g.concede_to_title()
        frame(g)
        self.assertIsNone(g.adventure_duel)                     # the next game (an online one, say) is an ordinary game
        self.assertEqual(g.end_stats_lines(), [])

    def test_continue_before_forges_result_still_counts(self):
        g, f, foe, gold = self.fight()
        st = copy.deepcopy(g.state)
        st["gameOver"] = True
        st["winner"] = next(p for p in st["players"] if p["id"] == st["me"])["name"]
        g.session.handle(st)                                     # the board says so; Forge's own result hasn't come yet
        frame(g)
        self.assertIsNotNone(on_disk(self.folder)["pending_duel"])
        g.end_continue()                                         # Enter on VICTORY at once
        frame(g)
        disk = on_disk(self.folder)
        self.assertIsNone(disk["pending_duel"])
        self.assertEqual(disk["record"]["wins"], 1)
        self.assertGreater(disk["gold"], gold)
        self.assertEqual(g.modal.result[0], "Victory")

    def test_a_result_that_cannot_be_written_is_tried_again_not_lost(self):
        g, f, foe, gold = self.fight()
        real = sv.write
        fails = {"n": 2}

        def flaky(save, folder=None, now=None):
            if fails["n"] > 0:
                fails["n"] -= 1
                raise PermissionError("the file is in use")
            return real(save, folder, now)

        with mock.patch.object(sv, "write", flaky):
            end_game(g, won=True)
            self.assertFalse(g.adventure_duel["settled"])
            self.assertIn("couldn't be saved yet", g.end_stats_lines()[0])
            self.assertIsNotNone(on_disk(self.folder)["pending_duel"])
            g.end_continue()                                     # second failure: the window takes the result with it
            frame(g)
            f2 = g.modal
            self.assertEqual(f2.retry, None)                     # third try (opening the slot) worked
        disk = on_disk(self.folder)
        self.assertIsNone(disk["pending_duel"])
        self.assertEqual(disk["record"]["wins"], 1)
        self.assertGreater(disk["gold"], gold)

    def test_a_result_still_unwritten_keeps_the_duel_marked_and_counts_at_the_next_save(self):
        g, f, foe, gold = self.fight()
        real = sv.write
        with mock.patch.object(sv, "write", side_effect=PermissionError("the file is in use")):
            end_game(g, won=True)
            g.end_continue()
            frame(g)
        f2 = g.modal
        self.assertEqual(f2.retry, "won")
        self.assertIsNotNone(f2.game.save.pending_duel)          # not voided as "didn't finish"
        self.assertIn("tried again", " ".join(f2.result[1]))
        self.assertIsNotNone(on_disk(self.folder)["pending_duel"])
        with mock.patch.object(sv, "write", real):
            f2.write()                                           # the next save takes it
        disk = on_disk(self.folder)
        self.assertIsNone(disk["pending_duel"])
        self.assertEqual(disk["record"]["wins"], 1)
        self.assertIsNone(f2.retry)

    def test_shift_f1_never_replaces_the_adventure(self):
        g = self.gui()
        f = self.open(g)
        key(g, pygame.K_F1, mod=pygame.KMOD_SHIFT)
        self.assertIs(g.modal, f)

    def test_nothing_walks_behind_the_bug_report(self):
        new_game(None)
        g = self.gui()
        f = self.open(g, slot=1)
        self.press(g, "leave_town")
        g.open_report()
        self.assertIsNotNone(g.overlay)
        here = f.explorer.walker.resting_place()
        t = time.monotonic()
        for _ in range(30):
            t += FRAME
            f.walk_tick(g, t, pressed=(0, 1))
        self.assertEqual(f.explorer.walker.resting_place(), here)

    def test_a_save_that_fails_at_fight_leaves_no_mark(self):
        g, f, foe = self.start_fight()
        with mock.patch.object(sv, "write", side_effect=PermissionError("the file is in use")):
            self.press(g, "fight")
        self.assertEqual((f.sub, f.result[0]), ("result", "No duel"))
        self.assertIsNone(f.game.save.pending_duel)
        self.press(g, "ok")
        f.fight, f.sub = foe.t, "fight"                          # walk into it again: this time it starts
        frame(g)
        self.press(g, "fight")
        self.assertIsNotNone(g.adventure_duel)

    def test_a_broken_map_is_said_and_the_town_still_has_a_way_out(self):
        new_game(None)
        g = self.gui()
        f = self.open(g, slot=1)
        from rpg import tmx
        with mock.patch.object(explore, "Explorer", side_effect=tmx.TmxError("sample_region.tmx: damaged")):
            self.press(g, "leave_town")
        self.assertEqual(f.stage, "town")
        self.assertIn("couldn't be opened", f.message[0])
        self.press(g, "menu")                                    # Main menu, top right
        frame(g)
        self.assertIsNone(g.modal)
        self.assertEqual(g.boot.stage, "menu")

    def test_an_adventure_duel_is_never_offered_to_resume(self):
        g, f, foe, gold = self.fight()
        self.assertEqual(g.session.stats_info.get("adventure"), 1)
        start = {"stats": dict(g.session.stats_info)}
        import types
        g.journal = types.SimpleNamespace(folder=self.tmp, close=lambda: None)   # this table has none; unfinished() stands in
        with mock.patch("forge_table.gjournal.unfinished", return_value=(start, [{"c": "ok"}])):
            self.assertIsNone(g.unfinished_game())
            start2 = {"stats": {"id": "x", "format": "commander"}}
            with mock.patch("forge_table.gjournal.unfinished", return_value=(start2, [{"c": "ok"}])):
                self.assertIsNotNone(g.unfinished_game())       # an ordinary game still is

    def test_every_type_button_is_there_at_200_percent(self):
        new_game(None)
        for size, scale in (((1100, 700), 2.0), ((1360, 840), 2.0), ((1920, 1080), 2.0)):
            with self.subTest(size=size):
                g = self.gui(size, scale)
                f = self.open(g, slot=1)
                f.open_deck(g, "town")
                frame(g)
                names = self.names(g)
                for gname in rs.GROUP_BUTTONS:
                    self.assertIn(f"group:{gname}", names)
                self.close(g)


class FitTests(Base):
    """Every stage and every small window at seven sizes: drawn without an error, every button inside the window and none on
    another."""

    def check(self, g, what):
        W, H = g.screen.get_size()
        rects = [pygame.Rect(r) for r, _n in g.modal.buttons]
        for r, n in g.modal.buttons:
            self.assertTrue(pygame.Rect(0, 0, W, H).contains(r), f"{what}: {n} at {r} outside {W}x{H}")
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(a.colliderect(b), f"{what}: buttons {a} and {b} overlap")

    def test_every_stage_fits(self):
        new_game(None)
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                g = self.gui(size, scale)
                f = self.open(g)
                frame(g)
                self.check(g, "slots")
                self.press(g, "new:2")
                for ch in "Karl":
                    key(g, getattr(pygame, "K_" + ch.lower()), unicode=ch)
                frame(g)
                self.check(g, "name")
                key(g, pygame.K_RETURN)
                self.check(g, "pick")
                for _ in range(3):
                    f.pick(g, f.flow.offer()[0].code)
                frame(g)
                self.check(g, "ready")
                f.flow = None
                f.open_slot(g, 1)
                f.enter_town(g, f.game.world.start_town)
                for tab in ("singles", "packs", "lands", "sell"):
                    f.tab = tab
                    frame(g)
                    self.check(g, "town " + tab)
                f.game.save.gold = 500
                f.shop_do(g, "pack", f.shop.packs()[0][0])
                frame(g)
                self.check(g, "pack")
                f.stage = "town"
                f.open_deck(g, "town")
                frame(g)
                self.check(g, "deck")
                f.deck_done(g)
                f.leave_town(g)
                frame(g)
                self.check(g, "map")
                for sub in ("pause", "result"):
                    f.sub, f.result = sub, ("Victory", ["You beat the Thornback: +30 gold.", "New card: Rancor."], rs.GREEN)
                    frame(g)
                    self.check(g, sub)
                f.sub = "fight"
                f.fight = next(t for t in f.explorer.area.triggers if t.kind == "encounter")
                frame(g)
                self.check(g, "fight")
                self.close(g)

    def words(self, g):
        """Every line of words rpg_screens draws in one frame, with where it went."""
        drawn, real = [], rs.draw_text

        def spy(screen, s, x, y, font, *a, **k):
            r = real(screen, s, x, y, font, *a, **k)
            drawn.append((s, pygame.Rect(r)))
            return r
        with mock.patch.object(rs, "draw_text", spy):
            frame(g)
        return drawn

    def test_no_words_overlap_in_the_deck_editor_and_the_map_keys_are_whole(self):
        new_game(None)
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                g = self.gui(size, scale)
                f = self.open(g, slot=1)
                f.enter_town(g, f.game.world.start_town)
                f.open_deck(g, "town")
                drawn = self.words(g)
                for i, (a, ra) in enumerate(drawn):                                  # a pixel's touch between lines is not
                    for b, rb in drawn[i + 1:]:                                      # an overlap (a glyph may stand 1 px taller)
                        self.assertFalse(ra.inflate(-2, -2).colliderect(rb.inflate(-2, -2)),
                                         f"deck editor: {a!r} at {ra} overlaps {b!r} at {rb}")
                f.deck_done(g)
                f.leave_town(g)
                shown = " ".join(s for s, _r in self.words(g))
                for part in ("Arrows or WASD:", "walk", "Esc: menu"):                # wrapped in a narrow panel, never cut
                    self.assertIn(part, shown)
                self.close(g)


class GuardTests(unittest.TestCase):
    def test_no_colour_literals_and_no_pygame_in_the_logic(self):
        from tests import test_ad1
        self.assertIn("rpg_screens.py", test_ad1.GUARDED)
        import ast
        with open(os.path.join(BASE, "rpg", "adventure.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                    for a in getattr(n, "names", [])} | {n.module.split(".")[0] for n in ast.walk(tree)
                                                          if isinstance(n, ast.ImportFrom) and n.module}
        self.assertNotIn("pygame", imported)


# ==================================================================================================================================
@unittest.skipUnless(live.live_enabled() and HAS_RUNTIME, live.live_problem() or "needs forge_runtime/")
class LiveTests(Base):
    """The real engine: a duel from the Adventure is a Standard game - 20 life each, 60-card decks, the AI named after the enemy -
    and conceding it costs what a loss costs."""

    def test_a_duel_from_the_adventure_on_the_real_engine(self):
        launcher = ft.Launcher(None, "Karl", seed=8008)
        g, f, foe = self.start_fight(launcher)
        gold = f.game.save.gold
        self.press(g, "fight")
        self.assertIsNone(g.modal, getattr(g.modal, "result", None))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            frame(g)
            st = g.state
            if (st and len(st.get("players") or []) == 2 and g.session.ready
                    and all(int(p.get("handCount") or 0) + int(p.get("libraryCount") or 0) > 0 for p in st["players"])):
                break                                          # the cards are dealt
            time.sleep(0.05)
        st = g.state
        self.assertIsNotNone(st, g.session.fatal)
        self.assertEqual(g.session.fmt, "standard")
        players = st["players"]
        self.assertEqual(sorted(p["life"] for p in players), [20, 20])
        enemy = next(p for p in players if p["id"] != st["me"])
        self.assertIn(f"({adv.enemy_name(foe.t.enemy)})", enemy["name"])
        for p in players:
            z = p.get("zones", {})
            in_game = (int(p.get("handCount") or 0) + int(p.get("libraryCount") or 0)
                       + sum(len(z.get(k, [])) for k in ("battlefield", "graveyard", "exile")))
            self.assertEqual(in_game, 60, p["name"])
            self.assertFalse(z.get("command"), "no command zone in a Standard game")
        g.concede()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and on_disk(self.folder)["pending_duel"] is not None:
            frame(g)
            time.sleep(0.05)
        disk = on_disk(self.folder)
        self.assertIsNone(disk["pending_duel"])
        self.assertEqual((disk["gold"], disk["record"]["losses"]), (gold - f.game.numbers.loss_cost(gold), 1))
        g.end_screen = g.end_screen or True
        g.end_continue()
        frame(g)
        self.assertIsInstance(g.modal, rs.AdventureFlow)
        self.assertEqual(g.modal.stage, "town")


if __name__ == "__main__":
    unittest.main()
