# SPDX-License-Identifier: GPL-3.0-or-later
"""Round SPAR1 (Karl, 3 Nov 2026: "B as its own task after prog-tune"; the handover's section 8, option B): the sparring yard in
Lantern Hollow - a measured win chance for your own deck, with its range. Groups:

  1  DataTests     progression.json's "sparring" is read and checked (a bad key is named); Yardmaster Corin stands in Lantern
                   Hollow with words that fit the box; the maps are built from their layouts; the yard's fence blocks no path
                   (every floor tile of the town is still reachable from the start, and Corin can be talked to)
  2  PlanTests     the duels are spread evenly over this region's regular foe kinds, with the decks (and handicaps) the region's
                   regular fights play; another region's foes never come in; the same seed gives the same plan
  3  RunTests      the duel tool gets YOUR deck and YOUR level and gear (start effects); Stop keeps every finished duel; the same
                   deck and seed give the same result; the words and the Wilson range; the default engine is Forge's duel tool
  4  WindowTests   by tap only: a tap on Corin -> his words -> "Spar: 40 duels" -> the progress window (Stop) -> the result ->
                   kept in the save (written) -> the deck editor shows "Last sparred: ..."; Stop by tap; no gold, cards, XP or
                   time come of it; a deck that can't be played, or no duel tool, is said
  5  SaveTests     a save from before SPAR1 loads (never sparred); damaged records are dropped, the rest kept
  6  FitTests      the progress window, the result and the deck editor's lines inside the window at 1360x840, 1920x1080 and
                   2736x1824, each at 100% and 200% text (SPAR1_SHOTS=<folder> writes the screenshots)
  7  LiveTests     (live Forge) real practice duels: Forge's winner and turns for each, the start effects reach Forge

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest tests.test_spar1
"""
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import clock, enemies, explore, levels, spar, talk
from rpg import save as sv
from tests import live
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame
from tests.test_prog1 import PROG_SIZES, PlayedBase
from tests.test_rpg14 import TOWN
from tests.test_rpgtap import TapBase

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGULAR = ["ember_bandit", "grave_robber", "thornback", "zealot"]       # region 1's regular foes on the shipped maps


class StandIn:
    """The duel tool's stand-in: decides from the Forge seed (so the same seed gives the same result), keeps what it was given.
    gate: a threading.Event each duel waits for (Stop tests); kill() lets it go and ends the run as Java's kill does."""
    name = "stand-in"
    made = []

    def __init__(self, folder, timeout=None, gate=None, per_game=None):
        self.calls, self.gate, self.killed, self.closed = [], gate, False, False
        self.per_game = per_game
        StandIn.made.append(self)

    def play(self, mine, theirs, seed, spec=""):
        self.calls.append({"mine": list(mine), "theirs": list(theirs), "seed": seed, "spec": spec})
        if self.per_game is not None:
            self.per_game(self)
        if self.gate is not None:
            while not self.gate.wait(0.01):
                if self.killed:
                    return None
        if self.killed:
            return None
        return {"winner": "A" if seed % 5 < 3 else ("B" if seed % 5 < 4 else "draw"), "turns": 9, "ms": 5}

    def kill(self):
        self.killed = True

    def close(self):
        self.closed = True


def factory(**kw):
    return lambda folder, timeout=None: StandIn(folder, timeout, **kw)


def new_save(seed=7):
    game = r8.new_game(tempfile.mkdtemp(), seed=seed)
    return game


# ==================================================================================================================================
class DataTests(unittest.TestCase):
    def test_the_sparring_table_is_read(self):
        st = spar.settings()
        self.assertEqual((st.duels, st.max_duels, st.timeout), (40, 200, 120))
        self.assertEqual(st.against_words(1), "the moor's foes")
        self.assertEqual(st.against_words(7), spar.AGAINST)

    def test_bad_keys_are_named(self):
        good = json.load(open(spar.PROGRESSION_FILE, encoding="utf-8"))
        cases = ((lambda d: d["sparring"].update(duels=0), "sparring.duels"),
                 (lambda d: d["sparring"].update(duels=500), "sparring.duels"),
                 (lambda d: d["sparring"].update(timeout="slow"), "sparring.timeout"),
                 (lambda d: d["sparring"].update(against=["moor"]), "sparring.against"),
                 (lambda d: d["sparring"].update(rewards=1), "'rewards'"),
                 (lambda d: d.update(sparring=[]), "sparring"))
        for change, word in cases:
            d = json.loads(json.dumps(good))
            change(d)
            with self.subTest(word=word):
                with self.assertRaises(ValueError) as cm:
                    spar.Settings(d)
                self.assertIn(word, str(cm.exception))

    def test_corin_stands_in_lantern_hollow_with_words(self):
        import xml.etree.ElementTree as ET
        root = ET.parse(os.path.join(BASE, "rpg", "maps", TOWN + ".tmx")).getroot()
        ids = {p.get("value") for o in root.iter("object") if o.get("class") == "npc" for p in o.iter("property")
               if p.get("name") == "id"}
        self.assertIn(talk.YARD_MASTER, ids)
        self.assertEqual(talk.npcs()[talk.YARD_MASTER]["name"], "Yardmaster Corin")
        self.assertEqual(talk.check(), [])
        c = talk.Conversation(talk.YARD_MASTER, {"region": 1, "flags": []})
        self.assertEqual(c.choice, ("Spar: 40 duels", "Not now"))
        self.assertIn("I'll run 40 practice duels: your deck, your level and gear, against the moor's foes.", c.pages)
        for p in c.pages:
            self.assertLessEqual(len(talk.wrap(p)), talk.PAGE_LINES, p)
        self.assertEqual(c.answer(True), [])
        self.assertTrue(c.spar)
        c = talk.Conversation(talk.YARD_MASTER, {"region": 1, "flags": []})
        c.answer(False)
        self.assertFalse(c.spar)

    def test_the_maps_are_built_from_their_layouts(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "rpg_make_maps.py"), "--check"], capture_output=True,
                           text=True, cwd=BASE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_yard_blocks_no_path_and_corin_can_be_reached(self):
        area = explore.Area(TOWN, __import__("rpg.tmx", fromlist=["x"]).read_map(os.path.join(explore.MAPS_DIR, TOWN + ".tmx")))
        free = {(c, r) for r in range(area.rows) for c in range(area.cols) if not area.solid[r * area.cols + c]}
        start = next(t for t in free if (t[0] * area.tw, t[1] * area.th) == (128, 160))         # the "start" spawn
        seen, todo = {start}, [start]
        while todo:
            c, r = todo.pop()
            for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if n in free and n not in seen:
                    seen.add(n)
                    todo.append(n)
        corin = (352 // 16, 80 // 16)
        self.assertNotIn(corin, free)                                  # a person is a wall
        self.assertTrue(any(n in seen for n in ((corin[0], corin[1] + 1), (corin[0] - 1, corin[1]), (corin[0] + 1, corin[1]))))
        pockets = free - seen
        self.assertFalse({t for t in pockets if 17 <= t[0] <= 27 and 2 <= t[1] <= 9}, "the yard shut in a floor tile")
        self.assertEqual(sorted(pockets), sorted(self.base_pockets()), "the yard made a floor tile unreachable")

    def base_pockets(self):
        """The unreachable floor tiles of the town before SPAR1 (the yard's tiles as floor, no Corin): the yard may add none."""
        from rpg import tmx
        a = explore.Area(TOWN, tmx.read_map(os.path.join(explore.MAPS_DIR, TOWN + ".tmx")))
        solid = bytearray(a.solid)
        for r in range(3, 8):
            for c in range(19, 26):
                solid[r * a.cols + c] = 0
        free = {(c, r) for r in range(a.rows) for c in range(a.cols) if not solid[r * a.cols + c]}
        start = (8, 10)
        seen, todo = {start}, [start]
        while todo:
            c, r = todo.pop()
            for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if n in free and n not in seen:
                    seen.add(n)
                    todo.append(n)
        return free - seen


# ==================================================================================================================================
class PlanTests(unittest.TestCase):
    def setUp(self):
        self.game = new_save()

    def test_the_foes_are_the_regions_regular_kinds_with_their_regular_decks(self):
        foes = spar.region_foes(self.game.save)
        self.assertEqual([f[0] for f in foes], REGULAR)
        for kid, name, src, decks in foes:
            with self.subTest(kind=kid):
                self.assertEqual(src, enemies.JUMPSTART)                       # towns.json: region 1's regular fights
                self.assertEqual(decks, enemies.deck_list(enemies.read_kind(kid), "jumpstart")[1])
                self.assertTrue(any(d.get("handicap") for d in decks) or all("handicap" in d for d in decks))

    def test_the_duels_are_spread_evenly_and_each_kinds_decks_dealt_in_turn(self):
        p = spar.plan(self.game.save, 40, seed=5)
        self.assertEqual(len(p), 40)
        counts = {k: sum(1 for d in p if d["kind"] == k) for k in REGULAR}
        self.assertEqual(counts, {k: 10 for k in REGULAR})
        foes = {f[0]: f[3] for f in spar.region_foes(self.game.save)}
        for k in REGULAR:
            idx = [d["index"] for d in p if d["kind"] == k]
            n = len(foes[k])
            self.assertEqual(sorted(idx[:n]), list(range(n)), f"{k}: every deck before any repeats")
        for d in p:
            self.assertEqual(d["cards"], spar.deck_cards(foes[d["kind"]][d["index"]]))

    def test_the_same_seed_gives_the_same_plan(self):
        a, b, c = (spar.plan(self.game.save, 12, seed=s) for s in (3, 3, 4))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_another_regions_foes_never_come_in(self):
        real = explore.load_area

        def fake(name, folder=None):
            area = real(name, folder)
            if name == "warrens_1":                                    # pretend the Warrens are region 2, with a regular foe
                class A:
                    pass
                fa = A()
                fa.region = 2
                fa.triggers = list(area.triggers) + [type("T", (), {"kind": "encounter", "encounter": "regular",
                                                                     "enemy": "plague_rat"})()]
                return fa
            return area
        with mock.patch.object(explore, "load_area", fake):
            kinds = [f[0] for f in spar.region_foes(self.game.save)]
            self.assertNotIn("plague_rat", kinds)
            self.game.save.world["region"] = 2
            self.assertEqual([f[0] for f in spar.region_foes(self.game.save)], ["plague_rat"])

    def test_no_foes_says_so(self):
        with self.assertRaises(spar.SparError):
            spar.plan(self.game.save, 4, 1, foes=[])


# ==================================================================================================================================
class RunTests(unittest.TestCase):
    def setUp(self):
        self.game = new_save()
        StandIn.made = []

    def run_spar(self, save, n=8, seed=9, **kw):
        sp = spar.Sparring(save, n=n, seed=seed, engine_factory=factory(**kw))
        return sp.run_here()

    def test_the_tool_gets_your_deck(self):
        sp = self.run_spar(self.game.save)
        eng = StandIn.made[-1]
        self.assertEqual(len(eng.calls), 8)
        mine = spar.deck_cards(self.game.save.deck())
        self.assertEqual(len(mine), 60)
        for c in eng.calls:
            self.assertEqual(c["mine"], mine)
        self.assertTrue(eng.closed)
        self.assertEqual(sp.result()["duels"], 8)

    def test_your_level_and_gear_reach_the_tool(self):
        s = self.game.save
        self.run_spar(s, n=2)
        self.assertEqual(StandIn.made[-1].calls[0]["spec"], "")                # level 1, nothing worn: the plain duel
        s.progress["xp"] = 140                                                   # level 4: +3 life
        self.assertEqual(levels.level(s), 4)
        s.gear = {"owned": ["padded_jerkin", "black_censer"], "worn": {"armour": "padded_jerkin", "offhand": "black_censer"}}
        self.run_spar(s, n=2)
        spec = StandIn.made[-1].calls[0]["spec"]
        import start_effects
        self.assertEqual(start_effects.parse(spec), levels.duel_effects(s))
        self.assertEqual(start_effects.parse(spec)["life"], 20 + 3 + 3 - 3)
        self.assertEqual(start_effects.parse(spec)["hand"], 8)

    def test_stop_keeps_every_finished_duel(self):
        gate = threading.Event()
        s = self.game.save
        done = []

        def let_three(eng):
            if len(eng.calls) <= 3:
                gate.set()
            else:
                gate.clear()
                done.append(1)
        sp = spar.Sparring(s, n=10, seed=2, engine_factory=factory(gate=gate, per_game=let_three)).start()
        for _ in range(400):
            if done:
                break
            time.sleep(0.01)
        self.assertEqual(sp.progress()[0], 3)
        sp.stop()
        self.assertTrue(sp.wait(5))
        r = sp.result()
        self.assertEqual((r["duels"] + r["errors"], r["total"], r["stopped"]), (3, 10, True))
        rec = spar.record(s, r)
        self.assertEqual(rec["duels"], r["duels"])
        self.assertIn(spar.STOPPED_LINE.format(n=3, total=10), spar.result_lines(r))

    def test_the_same_deck_and_seed_give_the_same_result(self):
        a = self.run_spar(self.game.save, n=12, seed=77).result()
        b = self.run_spar(self.game.save, n=12, seed=77).result()
        c = self.run_spar(self.game.save, n=12, seed=78).result()
        for k in ("duels", "wins", "draws", "kinds"):
            self.assertEqual(a[k], b[k])
        self.assertNotEqual([g["seed"] for g in StandIn.made[0].calls], [g["seed"] for g in StandIn.made[2].calls])
        self.assertTrue(c["duels"])

    def test_the_words_and_the_range(self):
        self.assertEqual(spar.pct_range(26, 40), (65, 50, 78))
        lo, hi = spar.wilson(0, 0)
        self.assertEqual((lo, hi), (0.0, 1.0))
        res = {"duels": 40, "wins": 26, "draws": 2, "errors": 1, "kinds": {"zealot": [6, 10, "Zealot"], "x": [0, 0, "X"]},
               "total": 41, "stopped": False, "against": "the moor's foes", "effects_words": "You start with 23 life."}
        lines = spar.result_lines(res)
        self.assertEqual(lines[0], "Won 26 of 40 against the moor's foes (65%; likely 50-78%).")
        self.assertIn("Zealot: won 6 of 10.", lines)
        self.assertNotIn("X: won 0 of 0.", lines)
        self.assertIn("2 ended in a draw (Forge's time limit) and count as not won.", lines)
        self.assertIn("1 couldn't be played and don't count.", lines)
        self.assertIn("Your deck played with your level and gear: You start with 23 life.", lines)
        self.assertEqual(lines[-1], spar.FREE_LINE)

    def test_a_tool_that_cant_start_is_said(self):
        def broken(folder, timeout=None):
            raise OSError("no java here")
        sp = spar.Sparring(self.game.save, n=3, seed=1, engine_factory=broken).run_here()
        r = sp.result()
        self.assertIn("no java here", r["error"])
        self.assertEqual(spar.result_lines(r), [r["error"]])
        self.assertIsNone(spar.record(self.game.save, r))

    def test_the_default_engine_is_forges_duel_tool(self):
        self.assertIs(spar.Sparring(self.game.save, n=1, seed=1).engine_factory, spar.make_engine)
        src = open(os.path.join(BASE, "rpg", "spar.py"), encoding="utf-8").read()
        self.assertIn('"forge.rpgtools.Duels", "-"', src)
        self.assertIn("return ForgeEngine(folder, timeout=timeout)", src)

    def test_history_lines(self):
        s = self.game.save
        deck = s.deck()
        self.assertEqual(spar.history_lines(s, deck), [])
        base = {"deck": deck["name"], "cards": spar.fingerprint(deck), "duels": 40}
        s.sparring = [dict(base, day=1, wins=19), dict(base, day=2, wins=22), dict(base, day=4, wins=26),
                      dict(base, deck="Another", day=5, wins=1)]
        self.assertEqual(spar.history_lines(s, deck), ["Last sparred: Day 4, 65% (50-78%)", "Before: Day 2, 55%; Day 1, 48%"])
        changed = dict(deck, main=[list(x) for x in deck["main"]][1:])
        self.assertEqual(spar.history_lines(s, changed)[0], "Last sparred: Day 4, 65% (50-78%) - before your last changes")

    def test_the_record_keeps_the_newest(self):
        s = self.game.save
        r = {"deck": "D", "deck_key": "k", "duels": 4, "wins": 1, "draws": 0, "kinds": {}, "effects": ""}
        for _ in range(spar.HISTORY_KEEP + 5):
            spar.record(s, r, now=clock.DAY * 3)
        self.assertEqual(len(s.sparring), spar.HISTORY_KEEP)
        self.assertEqual(s.sparring[-1]["day"], 4)


# ==================================================================================================================================
class WindowTests(TapBase):
    def setUp(self):
        super().setUp()
        StandIn.made = []
        p = mock.patch.object(spar, "make_engine", factory())
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(spar, "tool_problem", lambda: None)
        q.start()
        self.addCleanup(q.stop)

    def tap_button(self, g, name):
        f = g.modal
        frame(g)
        rect = next((r for r, n in reversed(f.buttons) if n == name), None)
        self.assertIsNotNone(rect, f"no {name!r}: {[n for _r, n in f.buttons]}")
        click(g, rect.center)
        frame(g)

    def tap_through(self, g, answer=None):
        f = g.modal
        seen = []
        for _ in range(80):
            if f.sub != "talk":
                return seen
            box = f.talk_box
            if box.choosing:
                if answer is None:
                    return seen
                self.tap_button(g, answer)
                answer = None
                continue
            if box.pages[box.page] not in seen:
                seen.append(box.pages[box.page])
            box.t0 -= 100
            self.tap_button(g, "talk_next")
        self.fail("the box never closed")

    def talk_by_tap(self, g, nid):
        f = g.modal
        at = __import__("rpg.tapwalk", fromlist=["x"]).person_tile(f.explorer, nid)
        self.assertIsNotNone(at, f"{nid} isn't out")
        self.tap_tile(g, at)
        self.walk(g)
        self.assertEqual(f.sub, "talk")
        self.assertEqual(f.explorer.facing_npc(), nid)
        return f

    def wait_done(self, g, f, seconds=10):
        for _ in range(int(seconds / 0.02)):
            frame(g)
            if f.sub != "spar":
                return
            time.sleep(0.02)
        self.fail("the spar never ended")

    def test_a_tap_on_corin_spars_and_the_result_is_kept(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        s = f.game.save
        self.talk_by_tap(g, talk.YARD_MASTER)                                  # (walking there moves the clock; talking doesn't)
        gold, coll, xp, clk, cnt = s.gold, dict(s.collection.to_dict()), s.progress["xp"], clock.now(s.world), s.rng_counter
        seen = self.tap_through(g, answer="talk_yes")
        self.assertTrue(any("practice duels" in p for p in seen), seen)
        self.assertIn(f.sub, ("spar", "result"))
        if f.sub == "spar":
            frame(g)
            self.assertIn("ok", [n for _r, n in f.buttons])                  # Stop
        self.wait_done(g, f)
        self.assertEqual(f.sub, "result")
        self.assertEqual(f.result[0], rs.SPAR_TITLE)
        self.assertTrue(f.result[1][0].startswith("Won "), f.result[1])
        self.assertIn("of 40 against the moor's foes", f.result[1][0])
        self.assertEqual(len(StandIn.made[-1].calls), 40)
        disk = r8.on_disk(self.folder)
        self.assertEqual(len(disk["sparring"]), 1)
        self.assertEqual(disk["sparring"][0]["duels"], 40)
        self.assertEqual((s.gold, s.collection.to_dict(), s.progress["xp"], clock.now(s.world), s.rng_counter),
                         (gold, coll, xp, clk, cnt))                                    # nothing won, no time, no dice used
        self.tap_button(g, "ok")
        self.assertIsNone(f.sub)
        f.open_deck(g, "map")
        frame(g)
        drawn = [ln for kind, ln, _r in f.shape_drawn["lines"] if kind == "spar"]
        self.assertTrue(drawn and drawn[0].startswith("Last sparred: Day 1, "), f.shape_drawn["lines"])

    def test_stop_by_tap_keeps_the_duels_played(self):
        gate = threading.Event()
        counted = []

        def two(eng):
            if len(eng.calls) <= 2:
                gate.set()
            else:
                gate.clear()
                counted.append(1)
        with mock.patch.object(spar, "make_engine", factory(gate=gate, per_game=two)):
            g, _l = self.setup_game()
            f = self.in_town(g)
            self.talk_by_tap(g, talk.YARD_MASTER)
            self.tap_through(g, answer="talk_yes")
            for _ in range(300):
                if counted:
                    break
                time.sleep(0.01)
            frame(g)
            self.assertEqual(f.sub, "spar")
            self.tap_button(g, "ok")                                           # Stop
            self.wait_done(g, f)
        self.assertEqual(f.sub, "result")
        self.assertIn("Stopped after 2 of 40 duels.", f.result[1])
        self.assertEqual(r8.on_disk(self.folder)["sparring"][0]["duels"], 2)

    def test_not_now_spars_nothing(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        self.talk_by_tap(g, talk.YARD_MASTER)
        self.tap_through(g, answer="talk_no")
        self.assertIsNone(f.sub)
        self.assertFalse(StandIn.made)

    def test_a_deck_that_cant_be_played_is_said(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        deck = f.game.save.deck()
        deck["main"] = deck["main"][:3]
        f.spar_start(g)
        self.assertEqual(f.sub, "result")
        self.assertTrue(f.result[1][0].startswith("Your deck can't be played yet:"), f.result[1])
        self.assertFalse(StandIn.made)

    def test_no_duel_tool_is_said(self):
        with mock.patch.object(spar, "tool_problem", lambda: "no java_rpg/rpg_tools.jar"):
            g, _l = self.setup_game()
            f = self.in_town(g)
            f.spar_start(g)
        self.assertEqual(f.result[1], [spar.NO_TOOL.format(what="no java_rpg/rpg_tools.jar")])

    def test_nothing_walks_while_sparring_and_esc_stops(self):
        gate = threading.Event()
        with mock.patch.object(spar, "make_engine", factory(gate=gate)):
            g, _l = self.setup_game()
            f = self.in_town(g)
            f.spar_start(g)
            self.assertEqual(f.sub, "spar")
            before = (f.explorer.walker.x, f.explorer.walker.y)
            f.walk_tick(g, 2000.0, pressed=(1, 0))
            f.walk_tick(g, 2001.0, pressed=(1, 0))
            self.assertEqual((f.explorer.walker.x, f.explorer.walker.y), before)
            from tests.test_forge_table import key
            key(g, pygame.K_RETURN)                                            # a stray Enter doesn't stop it
            self.assertFalse(f.sparring.stopped)
            key(g, pygame.K_ESCAPE)
            self.wait_done(g, f)
        self.assertEqual(f.sub, "result")
        self.assertIn(spar.NONE_LINE, f.result[1])


# ==================================================================================================================================
class SaveTests(r8.Base):
    def test_a_save_from_before_spar1_loads_never_sparred(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        d = json.load(open(path, encoding="utf-8"))
        d.pop("sparring", None)
        json.dump(d, open(path, "w", encoding="utf-8"))
        s, _n = sv.read(1, self.folder, pool=self.pool)
        self.assertEqual(s.sparring, [])
        self.assertEqual(spar.history_lines(s, s.deck()), [])

    def test_damaged_records_are_dropped_the_rest_kept(self):
        r8.new_game(None)
        path = sv.save_path(1, self.folder)
        d = json.load(open(path, encoding="utf-8"))
        good = {"deck": "Starting deck", "cards": "x", "day": 2, "duels": 10, "wins": 6}
        d["sparring"] = [good, {"deck": "x", "day": 1, "duels": 3, "wins": 5}, "junk", {"deck": 1}]
        json.dump(d, open(path, "w", encoding="utf-8"))
        s, _n = sv.read(1, self.folder, pool=self.pool)
        self.assertEqual(s.sparring, [good])
        d["sparring"] = "junk"
        json.dump(d, open(path, "w", encoding="utf-8"))
        self.assertEqual(sv.read(1, self.folder, pool=self.pool)[0].sparring, [])


# ==================================================================================================================================
class FitTests(PlayedBase):
    def inside(self, g, f, what):
        whole = g.screen.get_rect()
        self.assertTrue(f.buttons, what)
        for r, n in f.buttons:
            self.assertTrue(whole.contains(r), f"{what}: {n} {r} outside {whole}")

    def shot(self, g, name):
        out = os.environ.get("SPAR1_SHOTS")
        if out:
            os.makedirs(out, exist_ok=True)
            pygame.image.save(g.screen, os.path.join(out, name + ".png"))

    def test_the_windows_fit(self):
        r8.new_game(None)
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                tag = f"{size[0]}x{size[1]}" + ("_200" if scale else "")
                g, _l = self.setup_game(size=size)
                if scale:
                    g.text_scale = scale
                f = self.on_map(g, TOWN, "start")
                from tests.test_rpgtap import place
                place(f, (22, 7))                                              # at the yard's gate, below Corin
                frame(g, 2)
                self.shot(g, f"yard_{tag}")
                gate = threading.Event()
                eng = factory(gate=gate)
                with mock.patch.object(spar, "make_engine", eng), mock.patch.object(spar, "tool_problem", lambda: None):
                    f.talk_with(g, talk.YARD_MASTER)
                    frame(g)
                    self.inside(g, f, "Corin's box")
                    f.close_talk()
                    f.spar_start(g)
                    for _ in range(200):
                        if StandIn.made and StandIn.made[-1].calls:
                            break
                        time.sleep(0.01)
                    frame(g)
                    self.inside(g, f, "starting")
                    sp = f.sparring
                    with sp._lock:
                        sp.games += [{"kind": "zealot", "name": "Zealot", "index": 0, "winner": "A", "turns": 9, "ms": 1}] * 11
                    frame(g)
                    self.assertEqual(f.spar_box.lines, ["Practice duel 12 of 40. Won 11 so far."])
                    self.inside(g, f, "progress")
                    self.assertTrue(f.spar_box.rect.width <= g.screen.get_width())
                    self.shot(g, f"spar_progress_{tag}")
                    f.spar_stop(g)
                    gate.set()
                    for _ in range(300):
                        frame(g)
                        if f.sub == "result":
                            break
                        time.sleep(0.01)
                self.assertEqual(f.sub, "result")
                frame(g)
                self.inside(g, f, "result")
                self.assertTrue(g.screen.get_rect().contains(f.message_box.rect))
                self.shot(g, f"spar_result_{tag}")
                f.press_sub(g, "ok")
                s = f.game.save
                base = {"deck": s.deck()["name"], "cards": spar.fingerprint(s.deck()), "duels": 40}
                s.sparring = [dict(base, day=1, wins=19), dict(base, day=2, wins=22), dict(base, day=4, wins=26)]
                f.open_deck(g, "map")
                frame(g)
                drawn = [(ln, r) for kind, ln, r in f.shape_drawn["lines"] if kind == "spar"]
                self.assertTrue(drawn, "no sparring line in the deck editor")
                said = " ".join(ln for ln, _r in drawn)
                self.assertTrue(said.startswith("Last sparred: Day 4, 65% (50-78%)") or said == "Sparred: 65% (50-78%)", drawn)
                items = [r for _k, _ln, r in f.shape_drawn["lines"]]
                for a in items:                                                 # nothing drawn over anything else
                    for b in items:
                        if a is not b:
                            self.assertFalse(a.colliderect(b), f"{a} and {b} overlap")
                for ln, r in drawn:
                    self.assertTrue(f.decklist_rect.contains(r), f"{ln!r} {r} outside the deck panel {f.decklist_rect}")
                self.shot(g, f"deck_editor_{tag}")
                f.deck_done(g, keep=False)


# ==================================================================================================================================
@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveTests(unittest.TestCase):
    def test_real_practice_duels(self):
        if spar.tool_problem():
            self.skipTest(spar.tool_problem())
        game = new_save()
        s = game.save
        s.progress["xp"] = 140                                               # level 4: 23 life
        keep = tempfile.mkdtemp()
        sp = spar.Sparring(s, n=4, seed=11, engine_factory=lambda folder, timeout=None: spar.ForgeEngine(keep, timeout=timeout))
        sp.run_here()
        r = sp.result()
        self.assertIsNone(r["error"])
        self.assertEqual(r["duels"] + r["errors"], 4)
        self.assertEqual(r["errors"], 0)
        out = [json.loads(ln) for ln in open(os.path.join(keep, "duels_out.jsonl"), encoding="utf-8")]
        self.assertEqual(len(out), 4)
        for d in out:
            self.assertIn(d["winner"], ("A", "B", "draw"))
            self.assertGreater(d["turns"], 0)                                 # a real game, not a coin
        log = open(os.path.join(keep, "java.log"), encoding="utf-8").read()
        self.assertIn("life=23", log.replace(" ", ""), "the start effects didn't reach Forge")
        self.assertEqual({k for k, v in r["kinds"].items() if v[1]}, set(REGULAR))


if __name__ == "__main__":
    unittest.main()
