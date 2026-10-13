# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CD3: the stock cubes and the classic draft (Karl, 12 Oct 2026: "a small selection of 'stock' cubes that come built in";
then: classic draft for the classic cubes, 1v1 against the AI you pick, the lists bundled in the program with their owners
credited, Turbo Cube with its emblem, Commander Cube bundled too).

Offline: the Draft format and its legality; house_rules.py and its three homes (the deck's .txt, Forge's .dck, the bridge's
command line); cube_source's stock table, bundled lists and basics; the classic draft's shape, AI and 40-card builder; pods that
keep their style; every stock cube drafted to four legal decks; the draft window's stock buttons, style buttons and the AI you
play, driven headlessly through the real table; the build's checks and the credits.
Live (real Forge): Turbo Cube's emblem in both command zones lowers a spell and an activated ability by {2} (and an ordinary game
has none); a classic Turbo draft played through the table at 20 life with the emblem."""
import json
import os
import re
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

import cube_source as cs
import deck_library as lib
import draft_ai as da
import draft_core as dc
import draft_deck as dd
import draft_pod as dp
import draft_screens as ds
import forge_client as fc
import forge_table as ft
import formats
import house_rules as hr
import legality as lg
from tests import live
from tests.forge_fake import StubStore
from tests.test_cd2 import SIZES, every_card_known
from tests.test_deck_screen import FakeLauncher
from tests.test_forge_table import click, frame

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(HERE, "java_bridge", "src", "forge", "bridge")
TURBO = "turbo"
ENTRY = "2edc8e87-440c-4e63-8c93-1d2842de4909"
# Measured 12 Oct 2026 on the bundled lists (from the document: each cube's Cube Cobra data).
CARD_COUNTS = {"cm": 814, ENTRY: 180, "modovintage": 540, "cthulhu": 540, "turbo": 416,
               "e1a51d49-9a07-48b1-b2ba-c78b6e65d03b": 360, "devoid": 360, "spooty": 450}
# What this Forge (fb4d809) has no script for, measured 12 Oct with check_forge.
FORGE_MISSING = {"cm": {"Gisa's Favorite Shovel", "Fallaji Archaeologist"}, "modovintage": {"Makdee and Itla, Skysnarers", "Kavaero, Mind-Bitten", "Desecrex, Gift of Servitude"},
                 "cthulhu": {"Havengul Laboratory"}, "turbo": {"Voldaren Thrillseeker"},
                 "devoid": {"Park Map", "Weaponized Scrap"}}


def _read(path, binary=False):
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8", "errors": "replace"})) as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def stock_cube(key):
    cube, note = cs.load(key)
    return cube, cs.check_forge(cube.cards, every_card_known)


def play_out(pod, seed=77):
    """Finish the draft for you, picking the way an AI of this pod's style would."""
    colours = "".join(c for c in pod.station if c) if pod.classic else "WUBRG"
    pick = da.chooser(pod.cards, seed, pod.z, pod.style, colours)
    while not pod.done:
        pod.pick(pick(0, pod.pack(), pod.pool(), pod.needed()))


class RulesLauncher(FakeLauncher):
    """FakeLauncher that also records the format and the house rules a game starts with."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fmts, self.rules = [], []

    def start(self, mine, opponents, fmt=None, rules=()):
        self.fmts.append(fmt)
        self.rules.append(tuple(rules))
        return super().start(mine, opponents)


class Store:
    """peek_card for legality.check: every card known, basics typed as basics."""

    def peek_card(self, name):
        basic = name in ("Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes") or name.startswith("Snow-Covered ")
        return {"name": name, "type_line": "Basic Land — Island" if basic else "Creature — Test", "legalities": {},
                "oracle_text": "", "color_identity": []}


# ==================================================================================================================================
class FormatTests(unittest.TestCase):
    def test_draft_is_a_40_card_limited_format_played_as_constructed(self):
        f = formats.get("draft")
        self.assertEqual((f.key, f.name, f.deck_size, f.life_two, f.life_pod), ("draft", "Draft", 40, 20, 20))
        self.assertEqual((f.commander, f.copies, f.sideboard, f.exact, f.engine, f.dck_type),
                         (False, None, 0, False, "constructed", "Limited"))
        self.assertEqual(formats.screen("draft"), "standard")
        self.assertTrue(formats.drafted("draft") and formats.any_copies("draft"))
        self.assertNotIn("draft", formats.ORDER)                   # no button of its own: listed under Standard
        self.assertEqual(formats.from_text("# format: draft\nDeck\n1 Opt"), "draft")

    def test_the_dck_names_draft_and_standard_apart(self):
        self.assertEqual(formats.from_dck("[metadata]\nName=X\nDeck Type=Limited\n[Main]\n"), "draft")
        self.assertEqual(formats.from_dck("[metadata]\nName=X\nDeck Type=Constructed\n[Main]\n"), "standard")
        self.assertEqual(formats.from_dck("[metadata]\nName=X\n[Main]\n"), "commander")

    def test_a_draft_dck_has_no_commander_section(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = fc.write_deck_file(os.path.join(tmp, "d.dck"), ["Some Legend"], ["Island"] * 39, "D", fmt="draft")
        text = _read(p)
        self.assertIn("Deck Type=Limited", text)
        self.assertNotIn("[Commander]", text)
        self.assertIn("1 Some Legend", text)


class LegalityTests(unittest.TestCase):
    def test_40_cards_with_five_of_a_card_is_legal(self):
        deck = ["Grizzly Bears"] * 5 + ["Opt"] * 18 + ["Island"] * 17
        v = lg.check([], deck, Store(), fmt="draft")
        self.assertEqual(v.issues, [])

    def test_39_cards_is_too_few(self):
        v = lg.check([], ["Opt"] * 22 + ["Island"] * 17, Store(), fmt="draft")
        self.assertEqual([k for k, _t, _c in v.issues], ["size"])
        self.assertIn("at least 40", v.issues[0][1])

    def test_no_banned_list_for_a_draft(self):
        with mock.patch.dict(lg._BANNED, {"standard": lg.BanList(frozenset({"opt"}), 1, "snapshot")}):
            v = lg.check([], ["Opt"] * 23 + ["Island"] * 17, Store(), fmt="draft")
        self.assertEqual(v.issues, [])


class HouseRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_deck_line_and_its_reading(self):
        self.assertEqual(hr.text_line(["Turbo"]), "# rules: turbo")
        self.assertEqual(hr.text_line([]), "")
        self.assertEqual(hr.from_text("# format: draft\n# rules: turbo\n\nDeck\n1 Opt\n"), ("turbo",))
        self.assertEqual(hr.from_text("Deck\n1 Opt\n"), ())
        self.assertEqual(hr.normal("turbo, nonsense"), ("turbo",))
        self.assertEqual(hr.unknown("turbo, nonsense"), ("nonsense",))

    def test_the_dck_line_and_the_engine_arguments(self):
        self.assertEqual(hr.dck_lines(["turbo"]), ["Manticore Rules=turbo"])
        self.assertEqual(hr.dck_lines(()), [])
        self.assertEqual(hr.from_dck("[metadata]\nName=X\nManticore Rules=turbo\n[Main]\n"), ("turbo",))
        self.assertEqual(hr.engine_args(["turbo"]), ["--house-rule", "turbo"])
        self.assertEqual(hr.engine_args(None), [])
        self.assertEqual(hr.describe(["turbo"]), ["Turbo Cube emblem: all spells and activated abilities cost {2} less."])

    def test_a_dck_without_rules_is_exactly_what_it_was(self):
        a = fc.write_deck_file(os.path.join(self.tmp, "a.dck"), [], ["Island"] * 40, "A", fmt="draft")
        b = fc.write_deck_file(os.path.join(self.tmp, "b.dck"), [], ["Island"] * 40, "A", fmt="draft", rules=())
        self.assertEqual(_read(a, binary=True), _read(b, binary=True))
        c = fc.write_deck_file(os.path.join(self.tmp, "c.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "C")
        self.assertNotIn("Manticore Rules", _read(c))

    def test_the_session_reads_its_rules_from_the_players_dck(self):
        p = fc.write_deck_file(os.path.join(self.tmp, "p.dck"), [], ["Island"] * 40, "P", fmt="draft", rules=["turbo"])
        self.assertIn("Manticore Rules=turbo", _read(p))
        s = fc.ForgeSession(p, [p])
        self.assertEqual((s.fmt, s.rules), ("draft", ("turbo",)))      # a resumed game, Undo's rebuild, a replay
        args = s._bridge_args()
        i = args.index("--house-rule")
        self.assertEqual(args[i + 1], "turbo")
        self.assertEqual(args[args.index("--format") + 1], "constructed")
        plain = fc.ForgeSession(p, [p], rules=())
        self.assertNotIn("--house-rule", plain._bridge_args())

    def test_an_engine_that_ignores_the_rule_is_fatal(self):
        p = fc.write_deck_file(os.path.join(self.tmp, "p.dck"), [], ["Island"] * 40, "P", fmt="draft", rules=["turbo"])
        s = fc.ForgeSession(p, [p])
        s.handle({"t": "ready", "format": "constructed"})          # a bridge from before CD3 says nothing about house rules
        self.assertIn("house rule", s.fatal or "")
        ok = fc.ForgeSession(p, [p])
        ok.handle({"t": "ready", "format": "constructed", "houseRules": ["turbo"]})
        self.assertIsNone(ok.fatal)
        self.assertEqual(ok.rules_used, ("turbo",))
        plain = fc.ForgeSession(p, [p], rules=())
        plain.handle({"t": "ready", "format": "constructed"})
        self.assertIsNone(plain.fatal)

    def test_the_rules_of_a_game_come_from_its_decks(self):
        a, b = os.path.join(self.tmp, "a.txt"), os.path.join(self.tmp, "b.txt")
        _write(a, "# format: draft\n# rules: turbo\nDeck\n40 Island\n")
        _write(b, "# format: draft\nDeck\n40 Island\n")

        class E:
            def __init__(self, path):
                self.path = path
        self.assertEqual(hr.of_entries([E(a), E(b)]), ("turbo",))
        self.assertEqual(hr.of_entries([E(b), None]), ())


class StockTests(unittest.TestCase):
    def test_every_stock_cube_is_bundled_and_names_its_owner(self):
        self.assertEqual(len(cs.STOCK_CUBES), 8)
        self.assertEqual([st.key for st in cs.STOCK_CUBES][0], cs.DEFAULT_CUBE)
        for st in cs.STOCK_CUBES:
            data, fetched = cs.read_stock(st.key)
            self.assertIsNotNone(data, st.key)
            cube = cs.from_cubecobra(data, st.key)
            self.assertEqual((cube.name, cube.owner), (st.name, st.owner), st.key)
            self.assertEqual(len(cube.cards), CARD_COUNTS[st.key], st.key)
            self.assertGreater(fetched or 0, 1.79e9, st.key)            # downloaded on or after 12 Oct 2026
            self.assertIn(st.style, cs.STYLES)
        self.assertEqual({st.key: st.style for st in cs.STOCK_CUBES if st.style == "commander"}, {"cm": "commander"})
        self.assertEqual({st.key: st.rules for st in cs.STOCK_CUBES if st.rules}, {"turbo": ("turbo",)})

    def test_a_stock_cube_loads_with_no_network(self):
        def refuse(*a, **k):
            raise AssertionError("a stock cube must not be fetched")
        with mock.patch.object(cs, "fetch", refuse):
            for st in cs.STOCK_CUBES:
                cube, note = cs.load(st.key, refresh=True)
                self.assertTrue(cube.stock, st.key)
                self.assertEqual((cube.style, cube.rules, note), (st.style, tuple(st.rules), ""), st.key)
            cube, _note = cs.load("https://cubecobra.com/cube/list/turbo?view=table")
            self.assertTrue(cube.stock)
            cube, _note = cs.load("TURBO")
            self.assertEqual(cube.id, "turbo")

    def test_a_missing_bundled_list_falls_back_and_says_so(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        data, _f = cs.read_stock("devoid")
        cs.save_cache("devoid", data, os.path.join(tmp, "cubes"))
        with mock.patch.object(cs, "stock_dir", lambda: os.path.join(tmp, "nothing_here")):
            cube, note = cs.load("devoid", folder=os.path.join(tmp, "cubes"))
        self.assertFalse(cube.stock)
        self.assertEqual(cube.style, "classic")
        self.assertIn("built-in list is missing", note)

    def test_each_cubes_own_basic_lands(self):
        station = {st.key: cs.basic_station(cs.load(st.key)[0]) for st in cs.STOCK_CUBES}
        self.assertEqual(station["devoid"], {"": "Wastes"})
        self.assertEqual(station["spooty"]["U"], "Snow-Covered Island")
        self.assertEqual(sorted(station["cthulhu"].values()), ["Forest", "Island", "Mountain", "Plains", "Swamp"])
        self.assertEqual(cs.basic_station([]), cs.BASIC_NAME)
        self.assertEqual(cs.basic_station(["Bruna, the Fading Light"]), cs.BASIC_NAME)

    def test_the_style_of_a_cube_that_isnt_stock(self):
        self.assertEqual(cs.guess_style("Anything", ["edh cube"]), "commander")
        self.assertEqual(cs.guess_style("My Commander Cube", []), "commander")
        self.assertEqual(cs.guess_style("Vintage", [], "Vintage", ["Powered"], 0.3), "classic")
        self.assertEqual(cs.guess_style("Pauper", [], None, ["Commander"], 0.0), "commander")
        self.assertEqual(cs.guess_style("Mystery", [], None, [], 0.2), "commander")
        self.assertEqual(cs.guess_style("Mystery", [], None, [], 0.05), "classic")
        # every stock list, read as if pasted (without its stock entry), is guessed its own style
        with mock.patch.object(cs, "STOCK_CUBES", ()):
            for key in CARD_COUNTS:
                data, _f = cs.read_stock(key)
                want = "commander" if key == "cm" else "classic"
                self.assertEqual(cs.from_cubecobra(data, key).style, want, key)

    @unittest.skipUnless(os.path.isdir(os.path.join(fc.DEFAULT_RUNTIME, "res", "cardsfolder")), "needs forge_runtime/res")
    def test_what_this_forge_can_play_of_each_stock_cube(self):
        look = cs.forge_script_lookup()
        for st in cs.STOCK_CUBES:
            cube, _note = cs.load(st.key)
            chk = cs.check_forge(cube.cards, look)
            self.assertEqual(set(chk.missing), FORGE_MISSING.get(st.key, set()), st.key)
            self.assertGreaterEqual(len(chk.playable), dc.needed_for(st.style), st.key)


class ClassicDraftTests(unittest.TestCase):
    def test_the_two_shapes(self):
        self.assertEqual(dc.shape("commander"), (3, 20, 2))
        self.assertEqual(dc.shape("classic"), (3, 15, 1))
        self.assertEqual((dc.needed_for("commander"), dc.needed_for("classic")), (240, 180))

    def test_a_classic_draft_is_one_card_a_pick(self):
        seats = [dc.Seat(n, "human" if i == 0 else "ai") for i, n in enumerate(dp.SEAT_NAMES)]
        d = dc.Draft(180, seats, 3, *dc.shape("classic"))
        self.assertEqual((d.needed(0), d.label(0)), (1, "Pack 1, pick 1 of 15"))
        with self.assertRaises(dc.DraftError):
            d.pick(0, d.pack_for(0)[:2])
        dc.run_ai(d, lambda s, pack, pool, n: pack[:n], seats=range(4))
        self.assertTrue(d.done)
        self.assertEqual([len(p) for p in d.pools], [45] * 4)
        with self.assertRaises(dc.DraftError):
            dc.Draft(179, seats, 3, *dc.shape("classic"))

    def test_the_classic_drafter_wants_no_commander_and_only_colours_with_basics(self):
        cube, check = stock_cube("devoid")
        cards = list(check.playable)
        d = da.Drafter(cards, 5, style="classic", colours="")
        pool = [i for i, c in enumerate(cards) if c.identity][:6] + [i for i, c in enumerate(cards) if not c.identity][:20]
        colours, cmds = d.plan(pool)
        self.assertEqual((colours, cmds), (frozenset(), ()))
        coloured = next(i for i, c in enumerate(cards) if c.identity and i not in pool)
        plain = da.Drafter(cards, 5, style="classic", colours="")
        plain.rng.gauss = lambda *_a: 0.0
        self.assertLess(plain.score(coloured, pool, colours, False), plain.z[coloured])   # off-plan once settled
        legend = next(i for i, c in enumerate(cards) if c.commander)
        plain_cmd = da.Drafter(cards, 5, style="classic")
        plain_cmd.rng.gauss = lambda *_a: 0.0
        self.assertAlmostEqual(plain_cmd.score(legend, list(range(40)), frozenset(), False), plain_cmd.z[legend])

    def test_a_40_card_deck_with_the_cubes_own_basics(self):
        for key, basic in (("devoid", "Wastes"), ("spooty", "Snow-Covered"), (ENTRY, "")):
            cube, check = stock_cube(key)
            cards = list(check.playable)
            station = cs.basic_station(cube)
            z = dd.zscores(cards)
            pool = list(range(0, len(cards), 4))[:45]
            deck = dd.build_classic(cards, pool, z, station=station)
            self.assertEqual(dd.deck_size(deck), 40, key)
            self.assertLessEqual(len(deck.identity), 2, key)
            self.assertEqual(deck.commanders, ())
            for name in deck.basics:
                self.assertIn(name, station.values(), key)
                self.assertTrue(name.startswith(basic), (key, name))
            main = list(deck.spells + deck.lands) + [n for n, k in deck.basics.items() for _ in range(k)]
            self.assertEqual(dd.check_classic(cards, pool, main, station), [], key)

    def test_the_classic_deck_check(self):
        cube, check = stock_cube(ENTRY)
        cards = list(check.playable)
        pool = list(range(45))
        main = pool[:23] + ["Island"] * 17
        self.assertEqual(dd.check_classic(cards, pool, main), [])
        self.assertIn("needs at least 40", " ".join(dd.check_classic(cards, pool, main[:-1])))
        self.assertIn("2 in the deck, 1 drafted", " ".join(dd.check_classic(cards, pool, main[:-1] + [pool[0]])))
        self.assertIn("isn't one of this cube's basic lands",
                      " ".join(dd.check_classic(cards, pool, main, {"": "Wastes"})))
        self.assertEqual(dd.colour_name(frozenset("UR")), "Blue-Red")
        self.assertEqual(dd.colour_name(frozenset()), "Colourless")


class PodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cd3_pods_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_every_stock_cube_drafts_to_four_legal_decks(self):
        for st in cs.STOCK_CUBES:
            cube, check = stock_cube(st.key)
            pod = dp.Pod.new(cube, check, seed=7, root=self.tmp)
            self.assertEqual(pod.style, st.style)
            play_out(pod)
            size = 40 if pod.classic else 60
            decks = pod.build_all()
            self.assertEqual(pod.counts(), [45 if pod.classic else 60] * 4, st.key)
            for s, deck in enumerate(decks):
                self.assertIsNotNone(deck, (st.key, s))
                self.assertEqual(dd.deck_size(deck), size, (st.key, s))
                self.assertEqual(pod.problems(s, deck), [], (st.key, s))

    def test_a_classic_pod_keeps_its_style_basics_and_rules(self):
        cube, check = stock_cube(TURBO)
        pod = dp.Pod.new(cube, check, seed=9, root=self.tmp)
        self.assertEqual((pod.style, pod.rules, pod.format), ("classic", ("turbo",), "draft"))
        pod.pick([pod.pack()[0]])
        back = dp.Pod.load(pod.folder)
        self.assertEqual((back.style, back.rules, back.basics, back.counts()), (pod.style, pod.rules, pod.basics, pod.counts()))
        self.assertEqual(back.draft.pack_size, 15)

    def test_a_pod_from_before_cd3_is_a_commander_draft(self):
        cube, check = stock_cube("cm")
        pod = dp.Pod.new(cube, check, seed=9, root=self.tmp)
        path = os.path.join(pod.folder, dp.STATE_FILE)
        state = json.loads(_read(path))
        for k in ("style", "basics", "rules"):
            state.pop(k)
        _write(path, json.dumps(state))
        back = dp.Pod.load(pod.folder)
        self.assertEqual((back.style, back.rules, back.format), ("commander", (), "commander_draft"))

    def test_the_saved_decks_are_draft_decks_with_the_rule(self):
        cube, check = stock_cube(TURBO)
        pod = dp.Pod.new(cube, check, seed=11, root=self.tmp)
        play_out(pod)
        lib_dir = os.path.join(self.tmp, "my_decks")
        entries = pod.save_decks(pod.build_all(), lib_dir, self.tmp)
        for s, e in enumerate(entries):
            text = _read(e.path)
            self.assertTrue(text.startswith("# format: draft\n# draft: "), text[:80])
            self.assertIn("\n# rules: turbo\n", text)
            self.assertNotIn("\nCommander\n", text)
            self.assertEqual((e.format, e.ok, e.total), ("draft", True, 40), (s, e.error))
            self.assertEqual(hr.from_file(e.path), ("turbo",))
            self.assertRegex(e.name, r"^\d{4}-\d\d-\d\d draft - (You|AI \d) - [A-Za-z-]+$")
            self.assertFalse(lg.blocks_start(e.legality()))


class ScreenTests(unittest.TestCase):
    """The draft window through the real table (headless): the stock buttons, the style, a classic draft and the AI you play."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cd3_screens_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cubes = os.path.join(self.tmp, "cubes")
        self.pods = os.path.join(self.tmp, "pods")
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "sample_decks")
        os.makedirs(self.samples)
        shutil.copy(os.path.join(HERE, "sample_decks", "standard_red_aggro.txt"), self.samples)
        self.dirs = (self.lib, self.samples, self.tmp)
        cubes = self.cubes
        self.enterContext(mock.patch.object(cs, "cache_path", lambda cid, folder=None: os.path.join(folder or cubes, f"{cid}.json")))
        self.enterContext(mock.patch.object(cs, "forge_script_lookup", lambda runtime=None: every_card_known))
        self.enterContext(mock.patch.object(dp, "pods_dir", lambda: self.pods))

        def refuse(*a, **k):
            raise cs.CubeError("no network in this test")
        self.enterContext(mock.patch.object(cs, "fetch", refuse))

    def gui(self, size=(1360, 840), scale=None):
        self.launcher = RulesLauncher()
        g = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=size, launcher=self.launcher,
                          deck_dirs=self.dirs)
        if scale is not None:
            g.text_scale = scale
        self.addCleanup(self.close, g)
        g.open_boot(splash=False)
        frame(g, 2)
        return g

    @staticmethod
    def close(g):
        with mock.patch.object(ft.pygame, "quit"):
            g.shutdown()

    def open_draft(self, g):
        click(g, next(r for r, n in g.boot.buttons if n == "draft").center)
        flow = g.modal
        self.assertIsInstance(flow, ds.DraftFlow)
        self.wait(g, flow)
        return flow

    @staticmethod
    def wait(g, flow):
        if flow.job is not None:
            flow.job.thread.join(20)
        frame(g, 2)

    @staticmethod
    def btn(flow, name):
        return next(r for r, n in flow.buttons if n == name).center

    def choose(self, g, flow, key):
        i = next(k for k, st in enumerate(cs.STOCK_CUBES) if st.key == key)
        click(g, self.btn(flow, f"stock_{i}"))
        self.wait(g, flow)

    def test_the_default_is_commander_cube_from_its_bundled_list(self):
        g = self.gui()
        flow = self.open_draft(g)
        text = " ".join(t for t, _c in flow.status_lines())
        self.assertIn("Commander Cube by tritoch: 814 cards", text)
        self.assertIn("Built into Manticore: tritoch's list from Cube Cobra", text)
        self.assertEqual((flow.style(), flow.ready()), ("commander", True))
        self.assertEqual([n for _r, n in flow.buttons if n.startswith("stock_")], [f"stock_{i}" for i in range(8)])
        self.assertFalse([n for _r, n in flow.buttons if n.startswith("style_")])     # a stock cube's style is its own

    def test_turbo_cube_says_its_house_rule_and_drafts_one_card_a_pick(self):
        g = self.gui()
        flow = self.open_draft(g)
        self.choose(g, flow, TURBO)
        text = " ".join(t for t, _c in flow.status_lines())
        self.assertIn("Turbo Cube by anthonymattox: 416 cards", text)
        self.assertIn("House rule - Turbo Cube emblem: all spells and activated abilities cost {2} less.", text)
        self.assertEqual(flow.style(), "classic")
        click(g, self.btn(flow, "start"))
        self.assertEqual((flow.stage, flow.pod.needed(), flow.pod.style), ("pick", 1, "classic"))
        frame(g)
        click(g, flow.tiles[2][0].center)
        click(g, flow.tiles[4][0].center)                     # a second click swaps the first out: one card a pick
        self.assertEqual(len(flow.selected), 1)
        self.assertTrue(flow.confirm(g))
        self.assertEqual(flow.pod.counts()[0], 1)

    def test_a_pasted_cube_gets_the_style_buttons(self):
        data, _f = cs.read_stock(ENTRY)
        data = dict(data, cards={"mainboard": data["cards"]["mainboard"][:200]}, name="Someone's Cube", shortId="someone")
        cs.save_cache("someone", data)
        g = self.gui()
        flow = self.open_draft(g)
        flow.field.text = "someone"
        flow.load_cube()
        self.wait(g, flow)
        self.assertEqual((flow.style(), flow.ready()), ("classic", True))             # 200 cards: enough for 180
        click(g, self.btn(flow, "style_commander"))
        frame(g)
        self.assertEqual((flow.style(), flow.ready()), ("commander", False))          # ... not for 240
        bad = [t for t, c in flow.status_lines() if c == ds.RED]
        self.assertTrue(bad and "3 packs x 20 cards needs 240" in bad[0], bad)
        click(g, self.btn(flow, "style_classic"))
        frame(g)
        self.assertTrue(flow.ready())
        click(g, self.btn(flow, "start"))
        self.assertEqual(flow.pod.style, "classic")

    def test_a_classic_draft_ends_with_the_ai_you_choose(self):
        g = self.gui()
        flow = self.open_draft(g)
        self.choose(g, flow, TURBO)
        click(g, self.btn(flow, "start"))
        play_out(flow.pod)
        flow.to_review()
        frame(g)
        flow.to_decks(g)
        frame(g)
        self.assertEqual(flow.opponent, 1)
        click(g, self.btn(flow, "versus_3"))
        frame(g)
        self.assertEqual(flow.opponent, 3)
        self.assertIn("Save the decks and play AI 3", [b for b in self.labels(g, flow)])
        self.assertTrue(flow.save_decks(g))
        frame(g)
        menu = g.menu
        self.assertEqual(menu.fmt, "standard")
        ids = flow.pod.deck_ids
        self.assertEqual((menu.mine_id, menu.count, menu.opps[0]), (ids[0], 1, ids[3]))
        self.assertEqual(menu.message, (ds.CHOSEN_SAID_CLASSIC.format(ai="AI 3"), ds.GREEN))
        menu.start(g)
        self.assertEqual(self.launcher.fmts[-1], "draft")
        self.assertEqual(self.launcher.rules[-1], ("turbo",))
        self.assertEqual(g.current_rules, ("turbo",))

    def labels(self, g, flow):
        """The words on the window's buttons this frame (gui.draw_button records them)."""
        out = []
        real = g.draw_button

        def spy(rect, label, *a, **k):
            out.append(label)
            return real(rect, label, *a, **k)
        with mock.patch.object(g, "draw_button", spy):
            g.render()
        return out

    def test_the_setup_fits_at_every_size(self):
        for size, scale in SIZES + (((900, 600), 1.0),):
            g = self.gui(size, scale)
            flow = self.open_draft(g)
            self.choose(g, flow, TURBO)
            drawn = []
            real = ds.draw_text

            def spy(surf, text, *a, **k):
                drawn.append(text)
                return real(surf, text, *a, **k)
            with mock.patch.object(ds, "draw_text", spy):
                g.render()
            self.assertTrue(any(t.startswith("House rule - Turbo Cube emblem") for t in drawn), (size, scale))   # never cut
            panel = flow.rect
            foot_top = min(r.top for r, n in flow.buttons if n in ("back", "start"))
            stock = [r for r, n in flow.buttons if n.startswith("stock_")]
            self.assertEqual(len(stock), 8, (size, scale))
            for r in stock:
                self.assertTrue(panel.contains(r), (size, scale, r))
                self.assertLess(r.bottom, foot_top, (size, scale, r))
            for a in stock:
                for b in stock:
                    if a is not b:
                        self.assertFalse(a.colliderect(b), (size, scale, a, b))


class TableTests(unittest.TestCase):
    def test_the_loading_line_names_the_rule_and_restart_keeps_it(self):
        g = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=(1360, 840),
                          launcher=RulesLauncher())
        self.addCleanup(ScreenTests.close, g)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = fc.write_deck_file(os.path.join(tmp, "p.dck"), [], ["Island"] * 40, "P", fmt="draft", rules=["turbo"])
        g.session = fc.ForgeSession(p, [p])
        self.assertEqual(g.format_line(), "Draft - 20 life each. Turbo Cube emblem: all spells and activated abilities cost {2} "
                                          "less.")
        deck = ([], ["Island"] * 40)
        self.assertIsNone(g.begin(deck, [deck], fmt="draft", rules=("turbo",)))
        g.restart_game()
        self.assertEqual(g.launcher.rules, [("turbo",), ("turbo",)])
        self.assertEqual(g.launcher.fmts, ["draft", "draft"])


    def test_a_drafted_deck_from_the_command_line_keeps_its_rule(self):
        """python forge_table.py <a Turbo Cube draft deck>: the file's "# rules: turbo" line reaches the engine."""
        from types import SimpleNamespace
        started = []

        class Launcher:
            def __init__(self, *a, **k):
                self.runtime = None

            def start(self, mine, opps, fmt=None, rules=()):
                started.append((fmt, tuple(rules)))
                return SimpleNamespace()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        deck = os.path.join(tmp, "turbo.txt")
        _write(deck, "# format: draft\n# rules: turbo\n\nDeck\n23 Opt\n17 Island\n")
        table = mock.Mock()
        with mock.patch.object(ft, "Launcher", Launcher), mock.patch.object(ft, "ForgeTable", return_value=table), \
                mock.patch.object(ft.paths, "ensure_dirs"), mock.patch.object(ft.paths, "migrate_from_program_dir", return_value=[]), \
                mock.patch.object(ft.crashlog, "install"), mock.patch.object(ft.last_session, "begin", return_value=None), \
                mock.patch.object(ft.last_session, "end"), mock.patch.object(fc, "sync_bridge"), \
                mock.patch.object(fc, "runtime_problem", return_value=None), mock.patch.object(fc, "warm_card_index"), \
                mock.patch("card_data.CardDataStore"), mock.patch("builtins.print"):
            ft.main(["forge_table.py", deck])
        self.assertEqual(started, [("draft", ("turbo",))])


class BuildTests(unittest.TestCase):
    def test_the_build_ships_every_stock_list(self):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import check_dist as cd
        self.assertEqual(cd.stock_cube_files(), [f"stock_cubes/{st.key}.json" for st in cs.STOCK_CUBES])
        spec = _read(os.path.join(HERE, "commander_sim.spec"))
        self.assertIn('_data("stock_cubes")', spec)
        problems = cd.check_layout("x", [("stock_cubes/cm.json", 1)], [])
        self.assertIn("missing file: stock_cubes/turbo.json", problems)
        self.assertNotIn("missing file: stock_cubes/cm.json", problems)

    def test_the_owners_are_credited(self):
        notices = json.loads(_read(os.path.join(HERE, "licenses", "NOTICES.json")))
        text = next(c["text"] for c in notices["credits"] if c["id"] == "stock-cubes")
        for st in cs.STOCK_CUBES:
            self.assertIn(f"{st.name} by {st.owner} - cubecobra.com/cube/list/{st.key}", text)
        third = _read(os.path.join(HERE, "THIRD_PARTY_NOTICES.txt"))
        self.assertIn("Turbo Cube by anthonymattox", third)

    def test_the_tool_checks_the_bundled_lists(self):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import build_stock_cubes as bsc
        self.assertEqual(bsc.check(), [])
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copy(cs.stock_path("devoid"), cs.stock_path("turbo", tmp))
        problems = bsc.check(tmp)
        self.assertTrue(any("turbo: the file names 'The Devoid Cube' by 'Isticle'" in p for p in problems), problems)
        self.assertTrue(any(p.startswith("cm: ") and "missing" in p for p in problems), problems)


class SourceTests(unittest.TestCase):
    def test_the_bridge_side(self):
        src = _read(os.path.join(BRIDGE, "HouseRules.java"))
        static = re.search(r'TURBO_STATIC = "([^;]*);', src, re.S).group(1)
        for part in ("Mode$ ReduceCost", "EffectZone$ Command", "ValidCard$ Card", "Activator$ You", "Amount$ 2"):
            self.assertIn(part, static)
        self.assertNotIn("Type$", static)                     # spells AND activated abilities
        self.assertIn("@Subscribe", src)
        self.assertIn("GameEventGameStarted", src)
        self.assertNotRegex(src, r'println\("bridge:')         # card_check.PROBLEM_LINE would count its log line as a problem
        main = _read(os.path.join(BRIDGE, "Main.java"))
        self.assertIn('case "--house-rule"', main)
        self.assertIn('ready.add("houseRules", rules)', main)
        self.assertIn("HouseRules.reapply(game)", _read(os.path.join(BRIDGE, "SetupState.java")))
        self.assertIn("new HouseRules(g)", _read(os.path.join(BRIDGE, "BridgeGui.java")))

    def test_the_jar_holds_it(self):
        import zipfile
        with zipfile.ZipFile(os.path.join(HERE, "java_bridge", "forge_bridge.jar")) as z:
            self.assertIn("forge/bridge/HouseRules.class", z.namelist())


# ---- live: the real engine -----------------------------------------------------------------------------------------------------
LIVE_PROBLEM = live.live_problem()
DECK = ["Island"] * 17 + ["Divination"] * 23


@unittest.skipIf(LIVE_PROBLEM, f"Forge is not ready here: {LIVE_PROBLEM}")
class LiveEmblemTests(unittest.TestCase):
    """Turbo Cube's emblem in the real engine (fb4d809)."""

    def setUp(self):
        fc.sync_bridge()
        self.tmp = tempfile.mkdtemp(prefix="cd3_live_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def game(self, rules):
        p0 = fc.write_deck_file(os.path.join(self.tmp, "p.dck"), [], DECK, "P", fmt="draft", rules=rules)
        p1 = fc.write_deck_file(os.path.join(self.tmp, "o.dck"), [], DECK, "O", fmt="draft", rules=rules)
        s = fc.ForgeSession(p0, [p1], name="Tester", seed=5, dev=True)
        s.stderr_path = os.path.join(self.tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end = time.time() + 150
        while time.time() < end:
            s.poll()
            self.assertFalse(s.fatal or s.exited, s.fatal or "Forge stopped while starting")
            if s.state and s.me() and s.state["prompt"].get("message"):
                break
            time.sleep(0.05)
        first = [[c["name"] for c in p["zones"].get("command") or []] for p in s.state["players"]]
        end = time.time() + 90
        while time.time() < end:                              # to my first priority question
            s.poll()
            st = s.state
            msg = st["prompt"].get("message", "") if st else ""
            if st and s.is_mulligan_prompt():
                s.ok()
                time.sleep(0.5)
            elif st and msg.startswith("Priority") and st.get("asking", True):
                break
            time.sleep(0.1)
        return s, first

    def cost_after(self, s, lines, card_name):
        n0 = s.setups_done
        s.setup(lines + ["ailibrary=Island;Island;Island;Island;Island", "aibattlefield=Island",   # both players named: Forge's
                         "activeplayer=human", "activephase=MAIN1"])                                 # set-up wants every one
        end = time.time() + 60
        while s.setups_done == n0 and time.time() < end:
            s.poll()
            time.sleep(0.05)
        time.sleep(1.0)
        s.poll()
        log = _read(s.stderr_path)
        self.assertNotIn("setup failed", log)
        me = s.me()
        cards = [c for z in ("hand", "battlefield") for c in me["zones"][z] if c.get("name") == card_name]
        self.assertTrue(cards, card_name)
        s.click_card(cards[0]["id"])
        end = time.time() + 20
        while time.time() < end:
            s.poll()
            msg = s.state["prompt"].get("message", "")
            m = re.search(r"Pay Mana Cost: (\S+)", msg)
            if m:
                s.cancel()
                return m.group(1), [[c["name"] for c in p["zones"].get("command") or []] for p in s.state["players"]]
            time.sleep(0.1)
        self.fail(f"no payment question for {card_name}: {s.state['prompt'].get('message')!r}")

    def test_the_emblem_lowers_a_spell_by_2(self):
        s, first = self.game(["turbo"])
        self.assertEqual((s.rules, s.rules_used), (("turbo",), ("turbo",)))
        self.assertEqual(first, [["Turbo Cube Emblem"], ["Turbo Cube Emblem"]])
        cost, command = self.cost_after(s, ["humanhand=Divination", "humanbattlefield=Island",
                                            "humanlibrary=Island;Island;Island;Island;Island"], "Divination")
        self.assertEqual(cost, "{U}")                          # {2}{U} - {2}
        self.assertEqual(command, [["Turbo Cube Emblem"], ["Turbo Cube Emblem"]])     # back after Forge's set-up
        log = _read(s.stderr_path)
        self.assertEqual(log.count("has the Turbo Cube emblem"), 4)                    # the start, then the set-up
        self.assertEqual([ln for ln in log.splitlines() if "Exception" in ln], [])
        self.assertEqual(s.checks, [])

    def test_the_emblem_lowers_an_activated_ability_by_2(self):
        # One set-up per game: a second one sent while the game thread runs on after the Cancel races it (round 28bb's note).
        s, _first = self.game(["turbo"])
        cost, _c = self.cost_after(s, ["humanhand=Island", "humanbattlefield=Jayemdae Tome;Island;Island",
                                       "humanlibrary=Island;Island;Island;Island;Island"], "Jayemdae Tome")
        self.assertEqual(cost, "{2}")                          # {4}, {T}: Draw a card - {2}

    def test_a_game_without_the_rule_has_no_emblem(self):
        s, first = self.game([])
        self.assertEqual((s.rules, s.rules_used), ((), ()))
        self.assertEqual(first, [[], []])
        cost, _c = self.cost_after(s, ["humanhand=Divination", "humanbattlefield=Island",
                                       "humanlibrary=Island;Island;Island;Island;Island"], "Divination")
        self.assertEqual(cost, "{2}{U}")


@unittest.skipUnless(live.live_enabled(), "needs Forge and Java (tests/live.py)")
class LiveTableTests(unittest.TestCase):
    """A classic Turbo Cube draft's decks, played through the real table: 1v1, 20 life, the emblem in both command zones."""

    def setUp(self):
        fc.sync_bridge()
        self.tmp = tempfile.mkdtemp(prefix="cd3_table_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(mock.patch.object(ft, "DECK_DIR", os.path.join(self.tmp, "decks")))

    def test_a_classic_turbo_draft_plays_1v1_with_the_emblem(self):
        from tests.forge_bot import Bot
        cube, _note = cs.load(TURBO)
        check = cs.check_forge(cube.cards, cs.forge_script_lookup())
        pod = dp.Pod.new(cube, check, seed=13, root=os.path.join(self.tmp, "pods"))
        play_out(pod)
        lib_dir, samples = os.path.join(self.tmp, "my_decks"), os.path.join(self.tmp, "samples")
        os.makedirs(samples)
        entries = pod.save_decks(pod.build_all(), lib_dir, self.tmp)
        settings = os.path.join(self.tmp, "settings.json")
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"passing_version": 43, "tour_done": 99}, f)
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), settings_path=settings,
                            launcher=ft.Launcher(None, "Karl", seed=4243), saves_dir=os.path.join(self.tmp, "saves"),
                            deck_dirs=(lib_dir, samples, self.tmp))
        self.addCleanup(lambda: ScreenTests.close(gui))
        gui.open_menu()
        self.assertTrue(gui.menu.choose_draft(entries[0].id, [entries[2].id], fmt="draft"))
        gui.menu.start(gui)
        self.assertIsNone(gui.menu, gui.toast)
        self.assertEqual((gui.current_format, gui.current_rules), ("draft", ("turbo",)))
        bot, t0, seen, turns = Bot(gui.session), time.time(), None, set()
        while time.time() - t0 < 240:
            gui.tick(time.monotonic())
            gui.vs = None
            st = gui.state or {}
            if gui.session.exited or gui.session.fatal:
                break
            if st.get("players") and seen is None and st.get("prompt", {}).get("message"):
                seen = [(p["life"], [c["name"] for c in p["zones"].get("command", [])], p.get("handCount", 0)
                         + p.get("libraryCount", 0)) for p in st["players"]]
            if st.get("turn"):
                turns.add(st["turn"])
            if len(turns) >= 4:
                break
            if gui.modal is not None and gui.session.requests:
                bot.answer_requests()
                gui.modal = None
            elif gui.modal is not None:
                gui.modal = None
            elif st:
                bot.step()
            time.sleep(0.02)
        self.assertFalse(gui.session.fatal, gui.session.fatal)
        self.assertEqual((gui.session.format_used, gui.session.rules_used), ("constructed", ("turbo",)))
        self.assertEqual(seen, [(20, ["Turbo Cube Emblem"], 40)] * 2)
        self.assertGreaterEqual(len(turns), 4, sorted(turns))
        self.assertEqual(gui.session.checks, [])
        self.assertIn("Turbo Cube emblem", gui.format_line())
        import journal as gjournal
        start = gjournal.read_full(gui.journal.path)["start"]
        self.assertEqual(start["format"], "draft")
        self.assertIn("Manticore Rules=turbo", start["decks"]["player.dck"])    # Resume and Undo's rebuild read it there


if __name__ == "__main__":
    unittest.main()
