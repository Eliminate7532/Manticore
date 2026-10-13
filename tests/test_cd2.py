# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CD2: the cube draft on the main menu - "Choose cube -> pick 2 at a time vs 3 AIs -> review -> your deck Auto-built -> all 4
decks saved to My decks and chosen on the deck screen -> Start" (Karl, 9 Oct 2026).

Offline: the Commander Draft format and its legality, the pod kept on disk (draft_pod.py), the window's four stages driven
headlessly through the real table (draft_screens.py), the main menu, the deck screen choosing the four decks, and Start. A made-up
cube (tests/test_cd1.py) stands in for a Cube Cobra cube, and a stand-in for Forge's card scripts says every card is playable.
Live (real Forge): a cube made from Forge's own card scripts, drafted, and its four decks played as a 4-player game through the
table - 40 life, the commanders in the command zones, 60-card decks with doubles."""
import json
import os
import random
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

import boot_screens as fboot
import cube_source as cs
import deck_library as lib
import draft_ai as da
import draft_core as dc
import draft_deck as dd
import draft_pod as dp
import draft_screens as ds
import forge_client as fc
import forge_menu as fmenu
import forge_table as ft
import formats
import legality as lg
import version
from tests import live
from tests.forge_fake import StubStore
from tests.test_cd1 import made_up_cube
from tests.test_deck_screen import FakeLauncher
from tests.test_forge_table import click, frame, key

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = (((1024, 640), 1.0), ((1100, 700), 2.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0),
         ((1920, 1080), 2.0), ((4096, 2019), 1.75))


def every_card_known(name):
    return "Name:" + name


def made_up_check(data=None):
    cube = cs.from_cubecobra(data or made_up_cube(), "madeup")
    return cube, cs.check_forge(cube.cards, every_card_known)


def play_out(pod, seed=77):
    """Finish the draft for you, picking the way an AI would (a different seed from the AIs')."""
    pick = da.chooser(pod.cards, seed, pod.z)
    while not pod.done:
        pod.pick(pick(0, pod.pack(), pod.pool(), pod.needed()))


class DraftLauncher(FakeLauncher):
    """FakeLauncher that also takes the format a game starts in (Round FMT1's Launcher.start(..., fmt=))."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fmts = []

    def start(self, mine, opponents, fmt=None):
        self.fmts.append(fmt)
        return super().start(mine, opponents)


# ==================================================================================================================================
class FormatTests(unittest.TestCase):
    def test_commander_draft_is_a_commander_game_with_at_least_60_cards(self):
        f = formats.get("commander_draft")
        self.assertEqual((f.name, f.deck_size, f.exact, f.life_two, f.life_pod), ("Commander Draft", 60, False, 40, 40))
        self.assertTrue(f.commander and f.commander_damage)
        self.assertEqual(formats.engine("commander_draft"), "commander")         # 903.13g: Forge plays it as Commander
        self.assertEqual(formats.dck_lines("commander_draft"), [])                 # a .dck exactly like a Commander one
        self.assertFalse(f.online)                                                 # online drafting is CD5
        self.assertTrue(formats.any_copies("commander_draft"))
        self.assertFalse(formats.any_copies("commander"))
        self.assertTrue(formats.drafted("commander_draft"))
        self.assertFalse(formats.drafted("commander"))

    def test_it_is_listed_under_commander_and_has_no_button_of_its_own(self):
        self.assertEqual(formats.ORDER, ("commander", "brawl", "standard"))
        self.assertEqual(formats.screen("commander_draft"), "commander")
        for k in formats.ORDER:
            self.assertEqual(formats.screen(k), k)
        self.assertIn("commander_draft", formats.BLURB)

    def test_a_deck_file_says_so_in_its_format_line(self):
        text = formats.with_format("Commander\n1 X\n", "commander_draft")
        self.assertTrue(text.startswith("# format: commander_draft\n"))
        self.assertEqual(formats.from_text(text), "commander_draft")
        self.assertEqual(formats.declared("# format: Commander_Draft\n1 X"), "commander_draft")

    def test_the_dck_file_is_a_commander_one(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        a = fc.write_deck_file(os.path.join(tmp, "a.dck"), ["Kinnan, Bonder Prodigy"], ["Sol Ring", "Sol Ring", "Forest"], "D",
                               fmt="commander_draft")
        b = fc.write_deck_file(os.path.join(tmp, "b.dck"), ["Kinnan, Bonder Prodigy"], ["Sol Ring", "Sol Ring", "Forest"], "D")
        with open(a, encoding="utf-8") as fa, open(b, encoding="utf-8") as fb:
            self.assertEqual(fa.read(), fb.read())


# ==================================================================================================================================
class LegalityTests(unittest.TestCase):
    """legality.check for a Commander Draft deck (the fixture's real Scryfall card data): Commander's commander, pair and colour
    checks; at least 60 cards; any number of copies; no banned list and no card pool (903.13f; the spec's section 1)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "tests", "fixtures", "legality", "cards.json"), encoding="utf-8") as f:
            cls.cards = json.load(f)

    def setUp(self):
        cards = self.cards

        class Store:
            def peek_card(self, name):
                return cards.get(name.strip().lower())
        self.store = Store()
        lg.load("commander")

    def deck(self, extra=()):
        """Kinnan (G/U) and 59 cards: doubles of Delver, Llanowar Elves and Sol Ring, Commander's banned Mana Crypt and Chaos Orb,
        the silver-bordered Mox Lotus, and basics."""
        main = ["Delver of Secrets"] * 2 + ["Llanowar Elves"] * 3 + ["Sol Ring"] * 2 + ["Arcane Signet", "Mana Crypt", "Chaos Orb",
                                                                                      "Mox Lotus", "Thrasios, Triton Hero"]
        main += list(extra)
        main += ["Forest"] * 24 + ["Snow-Covered Island"] * (59 - len(main) - 24)
        return ["Kinnan, Bonder Prodigy"], main

    def test_a_drafted_deck_with_doubles_and_any_cube_card_is_legal(self):
        cmd, main = self.deck()
        self.assertEqual(len(cmd) + len(main), 60)
        v = lg.check(cmd, main, self.store, "commander_draft")
        self.assertEqual((v.status, v.issues, v.unchecked), ("legal", [], []))

    def test_the_same_deck_as_a_commander_deck_is_not(self):
        cmd, main = self.deck()
        kinds = {k for k, _t, _c in lg.check(cmd, main, self.store, "commander").issues}
        self.assertTrue({"size", "singleton", "banned", "not_legal"} <= kinds, kinds)

    def test_59_cards_are_too_few_and_more_than_60_are_fine(self):
        cmd, main = self.deck()
        v = lg.check(cmd, main[:-1], self.store, "commander_draft")
        self.assertEqual([k for k, _t, _c in v.issues], ["size"])
        self.assertIn("at least 60", v.issues[0][1])
        self.assertEqual(lg.check(cmd, main + ["Forest"] * 15, self.store, "commander_draft").status, "legal")

    def test_colour_identity_and_the_commander_are_still_checked(self):
        cmd, main = self.deck(["Lightning Bolt"])
        v = lg.check(cmd, main, self.store, "commander_draft")
        self.assertEqual([k for k, _t, _c in v.issues], ["identity"])
        v = lg.check(["Llanowar Elves"], main, self.store, "commander_draft")
        self.assertIn("commander", [k for k, _t, _c in v.issues])
        self.assertTrue(lg.blocks_start(v))                       # an ineligible commander still blocks Start (Round BAN1)


# ==================================================================================================================================
class PodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cd2_pod_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "pods")
        self.cube, self.check = made_up_check()

    def new(self, seed=7):
        return dp.Pod.new(self.cube, self.check, seed=seed, root=self.root)

    def test_a_new_draft_has_the_ais_first_picks_made_and_is_kept_on_disk(self):
        pod = self.new()
        self.assertEqual(pod.label(), "Pack 1, pick 1 of 10")
        self.assertEqual(pod.counts()[0], 0)
        self.assertGreater(sum(pod.counts()[1:]), 0)
        self.assertEqual(len(pod.pack()), 20)
        self.assertEqual(pod.needed(), 2)
        for f in (dp.STATE_FILE, dp.CARDS_FILE):
            self.assertTrue(os.path.isfile(os.path.join(pod.folder, f)))

    def test_you_take_two_cards_and_the_next_pack_comes(self):
        pod = self.new()
        first = pod.pack()
        pod.pick(first[:2])
        self.assertEqual(pod.pool(), first[:2])
        self.assertEqual(pod.label(), "Pack 1, pick 2 of 10")
        self.assertNotEqual(set(pod.pack()), set(first[2:]))     # not your own pack back: AI 3's, passed to you
        self.assertEqual(len(pod.pack()), 18)

    def test_one_card_or_a_card_from_another_pack_is_refused(self):
        pod = self.new()
        log = list(pod.draft.log)
        with self.assertRaises(dc.DraftError):
            pod.pick(pod.pack()[:1])
        other = next(i for i in range(len(pod.cards)) if i not in pod.pack())
        with self.assertRaises(dc.DraftError):
            pod.pick([pod.pack()[0], other])
        self.assertEqual(pod.draft.log, log)

    def test_packs_one_and_three_pass_left_and_two_right(self):
        pod = self.new()
        seen = {}
        pick = da.chooser(pod.cards, 1, pod.z)
        while not pod.done:
            seen[pod.draft.round] = pod.passing()
            pod.pick(pick(0, pod.pack(), pod.pool(), pod.needed()))
        self.assertEqual(seen, {0: ("left", "AI 1"), 1: ("right", "AI 3"), 2: ("left", "AI 1")})

    def test_kept_after_every_pick_and_rebuilt_from_disk(self):
        pod = self.new()
        pod.pick(pod.pack()[:2])
        pod.pick(pod.pack()[-2:])
        again = dp.Pod.load(pod.folder)
        self.assertEqual(again.draft.log, pod.draft.log)
        self.assertEqual(again.pack(), pod.pack())
        self.assertEqual(again.pool(), pod.pool())
        self.assertEqual(again.cards, pod.cards)
        self.assertEqual(again.cube["name"], "Made-up Cube")

    def test_the_pod_keeps_its_own_cards(self):
        """A later change to the cube can't scramble an old draft: the cards are read from the pod, not the cube."""
        pod = self.new()
        pod.pick(pod.pack()[:2])
        changed = made_up_cube(seed=99)
        changed["cards"]["mainboard"] = changed["cards"]["mainboard"][::-1]
        again = dp.Pod.load(pod.folder)
        self.assertEqual([c.name for c in again.cards], [c.name for c in pod.cards])

    def test_the_same_seed_makes_the_same_draft(self):
        a, b = self.new(seed=11), self.new(seed=11)
        play_out(a)
        play_out(b)
        self.assertEqual(a.draft.log, b.draft.log)
        self.assertNotEqual(a.folder, b.folder)

    def test_unfinished_offers_the_newest_draft_not_saved_or_discarded(self):
        self.assertIsNone(dp.unfinished(self.root))
        a = self.new()
        self.assertEqual(dp.unfinished(self.root).folder, a.folder)
        b = self.new()
        self.assertEqual(dp.unfinished(self.root).folder, b.folder)
        b.discard()
        self.assertEqual(dp.unfinished(self.root).folder, a.folder)
        self.assertTrue(os.path.isdir(b.folder))                 # discarded, never deleted
        a.stage = "saved"
        a.save()
        self.assertIsNone(dp.unfinished(self.root))

    def test_a_broken_pod_is_skipped(self):
        a = self.new()
        b = self.new()
        with open(os.path.join(b.folder, dp.CARDS_FILE), "wb") as f:
            f.write(b"not gzip")
        self.assertEqual(dp.unfinished(self.root).folder, a.folder)
        with self.assertRaises(dp.PodError):
            dp.Pod.load(b.folder)

    def test_the_decks_are_saved_to_my_decks_as_commander_draft_decks(self):
        pod = self.new()
        play_out(pod)
        decks = pod.build_all()
        self.assertTrue(all(d is not None for d in decks))
        for s, d in enumerate(decks):
            self.assertEqual(pod.problems(s, d), [], s)
        library = os.path.join(self.tmp, "my_decks")
        entries = pod.save_decks(decks, library, self.tmp)
        self.assertEqual(len(entries), 4)
        for s, e in enumerate(entries):
            self.assertTrue(e.ok, e.error)
            self.assertEqual(e.format, "commander_draft")
            self.assertEqual(e.total, 60)
            self.assertIn(dp.SEAT_NAMES[s], e.name)
            with open(e.path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(dp.draft_id_of(text), pod.id)
        self.assertEqual(entries[0].name.split(" - ")[1], "You")
        listed = {e.id for e in lib.list_decks(library, os.path.join(self.tmp, "none"), self.tmp)}
        self.assertEqual(listed, {e.id for e in entries})
        self.assertEqual(pod.deck_ids, [e.id for e in entries])
        self.assertIsNone(dp.unfinished(self.root))
        self.assertEqual(dp.Pod.load(pod.folder).stage, "saved")

    def test_your_deck_may_use_forges_ai_flagged_cards_and_the_ais_avoid_them(self):
        flagged = frozenset({self.cube.cards[0].name})
        pod = dp.Pod.new(self.cube, self.check._replace(ai_flagged=flagged), seed=3, root=self.root)
        play_out(pod)
        with mock.patch.object(dp.dd, "build", wraps=dd.build) as build:
            pod.build_all()
        avoids = [c.kwargs.get("avoid") for c in build.call_args_list]
        self.assertEqual(avoids, [frozenset(), flagged, flagged, flagged])

    def test_the_deck_file_reads_back_as_the_same_deck(self):
        pod = self.new()
        play_out(pod)
        deck = pod.build(2)
        cmd, main = dd.deck_names(pod.cards, deck)
        import deck_importer
        c2, m2 = deck_importer.import_from_text(pod.deck_text(2, deck))
        self.assertEqual(c2, cmd)
        self.assertEqual(sorted(m2), sorted(main))


# ==================================================================================================================================
class ScreenTests(unittest.TestCase):
    """The window through the real table, headless."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cd2_screen_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cubes = os.path.join(self.tmp, "cubes")
        self.pods = os.path.join(self.tmp, "pods")
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "sample_decks")
        os.makedirs(self.samples)
        shutil.copy(os.path.join(HERE, "sample_decks", "stompy_goreclaw.txt"), self.samples)
        shutil.copy(os.path.join(HERE, "sample_decks", "brawl_krenko.txt"), self.samples)
        self.dirs = (self.lib, self.samples, self.tmp)
        cubes = self.cubes
        self.enterContext(mock.patch.object(cs, "cache_path", lambda cid, folder=None: os.path.join(folder or cubes, f"{cid}.json")))
        self.enterContext(mock.patch.object(cs, "forge_script_lookup", lambda runtime=None: every_card_known))
        self.enterContext(mock.patch.object(dp, "pods_dir", lambda: self.pods))
        self.enterContext(mock.patch.object(cs, "STOCK_CUBES", ()))   # round CD3: "cm" is a stock cube now (its bundled list);
        cs.save_cache(cs.DEFAULT_CUBE, made_up_cube())                  # these tests draft the made-up cube kept under its name

    def gui(self, size=(1360, 840), scale=None, title=True):
        g = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=size, launcher=DraftLauncher(),
                          deck_dirs=self.dirs)
        if scale is not None:
            g.text_scale = scale
        self.addCleanup(self.close, g)
        if title:
            g.open_boot(splash=False)
        frame(g, 2)
        return g

    @staticmethod
    def close(g):
        with mock.patch.object(ft.pygame, "quit"):
            g.shutdown()

    def open_draft(self, g):
        click(g, self.item(g, "draft"))
        self.assertIsInstance(g.modal, ds.DraftFlow)
        flow = g.modal
        if flow.job is not None:
            flow.job.thread.join(10)
        frame(g, 2)
        return flow

    @staticmethod
    def item(g, name):
        return next(r for r, n in g.boot.buttons if n == name).center

    @staticmethod
    def btn(flow, name):
        return next(r for r, n in flow.buttons if n == name).center

    @staticmethod
    def names(flow):
        return [n for _r, n in flow.buttons]

    def start(self, g):
        flow = self.open_draft(g)
        click(g, self.btn(flow, "start"))
        self.assertEqual(flow.stage, "pick")
        frame(g)
        return flow

    def tile(self, flow, k):
        return flow.tiles[k][0].center, flow.tiles[k][1]

    # ---- the main menu ---------------------------------------------------------------------------------------------------------
    def test_draft_is_on_the_main_menu_right_after_play(self):
        g = self.gui()
        self.assertEqual([n for _r, n in g.boot.buttons], ["play", "draft", "settings", "credits", "quit"])
        self.assertEqual(dict(fboot.ITEMS)["draft"], "Draft")

    def test_the_main_menu_row_still_fits_at_every_size(self):
        for size, scale in SIZES:
            g = self.gui(size, scale)
            rects = [r for r, _n in g.boot.buttons]
            screen = pygame.Rect(0, 0, *size)
            for r in rects:
                self.assertTrue(screen.contains(r), (size, scale, r))
            for a, b in zip(rects, rects[1:]):
                self.assertLessEqual(a.right, b.x + 1, (size, scale))

    def test_choosing_draft_opens_the_window_over_the_deck_screen(self):
        g = self.gui()
        flow = self.open_draft(g)
        self.assertIsNone(g.boot)
        self.assertIsNotNone(g.menu)
        self.assertTrue(flow.from_title)
        self.assertEqual(flow.stage, "setup")

    def test_keys_go_to_the_window_not_the_deck_search(self):
        g = self.gui()
        flow = self.open_draft(g)
        for ch in "abc":
            key(g, getattr(pygame, "K_" + ch), unicode=ch)
        self.assertEqual(flow.field.text, "abc")
        self.assertEqual(g.menu.search, "")

    # ---- setup -----------------------------------------------------------------------------------------------------------------
    def test_the_default_cube_loads_and_says_what_it_has(self):
        g = self.gui()
        flow = self.open_draft(g)
        self.assertTrue(flow.ready())
        text = " ".join(t for t, _c in flow.status_lines())
        self.assertIn("Made-up Cube by nobody: 310 cards; 310 of them this Forge can play", text)
        self.assertIn("start", self.names(flow))

    def test_cards_forge_cant_play_are_left_out_and_named(self):
        def lookup(name):
            return None if name in ("W Spell 0", "Artifact 3") else "Name:" + name
        with mock.patch.object(cs, "forge_script_lookup", lambda runtime=None: lookup):
            g = self.gui()
            flow = self.open_draft(g)
        text = " ".join(t for t, _c in flow.status_lines())
        self.assertIn("308 of them this Forge can play", text)
        self.assertIn("Left out (this Forge has no script for them): W Spell 0, Artifact 3.", text)

    def test_a_cube_too_small_is_refused_with_the_numbers(self):
        small = made_up_cube(per_colour=20, legends_per_colour=4)
        cs.save_cache("tiny", small)
        g = self.gui()
        flow = self.open_draft(g)
        for ch in "tiny":
            key(g, getattr(pygame, "K_" + ch), unicode=ch)
        key(g, pygame.K_RETURN)
        flow.job.thread.join(10)
        frame(g, 2)
        self.assertFalse(flow.ready())
        self.assertNotIn("start", self.names(flow))
        bad = [t for t, c in flow.status_lines() if c == fmenu.RED]
        self.assertTrue(bad and "needs 240 cards" in bad[0], bad)

    def test_a_link_to_another_site_is_refused_in_words(self):
        g = self.gui()
        flow = self.open_draft(g)
        flow.field.text = "https://example.com/cube/x"
        key(g, pygame.K_RETURN)
        self.assertEqual(flow.message[1], fmenu.RED)
        self.assertIn("Cube Cobra", flow.message[0])

    def test_esc_on_the_setup_goes_back_to_the_main_menu(self):
        g = self.gui()
        self.open_draft(g)
        key(g, pygame.K_ESCAPE)
        frame(g)
        self.assertIsNone(g.modal)
        self.assertIsInstance(g.boot, fboot.BootFlow)
        self.assertIsNone(g.menu)

    # ---- picking ---------------------------------------------------------------------------------------------------------------
    def test_two_picks_then_confirm_and_one_is_refused(self):
        g = self.gui()
        flow = self.start(g)
        pos, first = self.tile(flow, 0)
        click(g, pos)
        self.assertEqual(flow.selected, [first])
        self.assertNotIn("confirm", self.names(flow))            # greyed: one card is not a pick
        key(g, pygame.K_RETURN)
        self.assertEqual(flow.pod.counts()[0], 0)
        self.assertEqual(flow.message[1], fmenu.ORANGE)
        pos, second = self.tile(flow, 1)
        click(g, pos)
        self.assertIn("confirm", self.names(flow))
        click(g, self.btn(flow, "confirm"))
        self.assertEqual(flow.pod.pool(), [first, second])
        self.assertEqual(flow.selected, [])
        self.assertEqual(flow.pod.label(), "Pack 1, pick 2 of 10")

    def test_a_third_click_swaps_out_the_first_and_a_second_click_unpicks(self):
        g = self.gui()
        flow = self.start(g)
        cards = [self.tile(flow, k) for k in range(3)]
        for pos, _c in cards:
            click(g, pos)
        self.assertEqual(flow.selected, [cards[1][1], cards[2][1]])
        click(g, cards[2][0])
        self.assertEqual(flow.selected, [cards[1][1]])

    def test_enter_confirms(self):
        g = self.gui()
        flow = self.start(g)
        a, b = self.tile(flow, 4), self.tile(flow, 7)
        click(g, a[0])
        click(g, b[0])
        key(g, pygame.K_RETURN)
        self.assertEqual(flow.pod.pool(), [a[1], b[1]])

    def test_every_card_of_the_pack_is_shown_once(self):
        g = self.gui()
        flow = self.start(g)
        self.assertEqual(sorted(c for _r, c in flow.tiles), sorted(flow.pod.pack()))

    def test_the_whole_draft_through_the_window(self):
        g = self.gui()
        flow = self.start(g)
        pick = da.chooser(flow.pod.cards, 5, flow.pod.z)
        n = 0
        while flow.stage == "pick":
            want = pick(0, flow.pod.pack(), flow.pod.pool(), flow.pod.needed())
            for card in want:
                click(g, next(r.center for r, c in flow.tiles if c == card))
            key(g, pygame.K_RETURN)
            n += 1
        self.assertEqual(n, 30)
        self.assertEqual(flow.stage, "review")
        self.assertEqual(flow.pod.counts(), [60, 60, 60, 60])

    def test_nothing_is_sent_to_forge_while_drafting(self):
        g = self.gui()
        with mock.patch.object(g.session, "send") as send:
            flow = self.start(g)
            play_out(flow.pod)
            flow.to_review()
            frame(g)
            flow.to_decks(g)
            frame(g)
        send.assert_not_called()

    # ---- leaving and coming back -----------------------------------------------------------------------------------------------
    def test_save_and_leave_keeps_the_draft_and_draft_continues_it(self):
        g = self.gui()
        flow = self.start(g)
        flow.pod.pick(flow.pod.pack()[:2])
        frame(g)
        pack, pool = flow.pod.pack(), flow.pod.pool()
        key(g, pygame.K_ESCAPE)
        frame(g)
        self.assertIsNone(g.modal)
        self.assertIsInstance(g.boot, fboot.BootFlow)
        self.assertIn("Draft on the main menu continues it", g.toast[0])
        flow = self.open_draft(g)
        self.assertIsNone(flow.job)                              # an unfinished draft: no cube is loaded meanwhile
        self.assertIn("continue", self.names(flow))
        self.assertNotIn("start", self.names(flow))
        click(g, self.btn(flow, "continue"))
        self.assertEqual(flow.stage, "pick")
        self.assertEqual((flow.pod.pack(), flow.pod.pool()), (pack, pool))

    def test_a_restart_mid_pack_continues_where_it_was(self):
        g = self.gui()
        flow = self.start(g)
        flow.pod.pick(flow.pod.pack()[:2])
        log = list(flow.pod.draft.log)
        self.close(g)                                             # Manticore closes mid-pack
        g2 = self.gui()
        flow2 = self.open_draft(g2)
        key(g2, pygame.K_RETURN)                                 # Enter = Continue it
        self.assertEqual(flow2.stage, "pick")
        self.assertEqual(flow2.pod.draft.log, log)

    def test_discard_is_not_offered_again(self):
        g = self.gui()
        flow = self.start(g)
        folder = flow.pod.folder
        key(g, pygame.K_ESCAPE)
        frame(g)
        flow = self.open_draft(g)
        click(g, self.btn(flow, "discard"))
        self.assertIsNone(flow.resume)
        self.assertIsNotNone(flow.job)                           # the cube loads now
        self.assertTrue(os.path.isdir(folder))
        key(g, pygame.K_ESCAPE)
        frame(g)
        flow = self.open_draft(g)
        self.assertIsNone(flow.resume)

    # ---- review and decks --------------------------------------------------------------------------------------------------------
    def finished(self, g):
        flow = self.start(g)
        play_out(flow.pod)
        flow.to_review()
        frame(g, 2)
        return flow

    def test_the_review_shows_each_pick_with_its_pack(self):
        g = self.gui(size=(1920, 1080))
        flow = self.finished(g)
        picks = flow.pod.draft.review(0)
        self.assertEqual(len(picks), 30)
        first = [c for _r, c in flow.tiles[:20]]
        self.assertEqual(first[:2], list(picks[0].cards))
        self.assertEqual(sorted(first), sorted(picks[0].pack_before))
        click(g, self.btn(flow, "seat_2"))
        frame(g)
        self.assertEqual(flow.review_seat, 2)
        self.assertEqual([c for _r, c in flow.tiles[:2]], list(flow.pod.draft.review(2)[0].cards))

    def test_the_review_scrolls(self):
        g = self.gui()
        flow = self.finished(g)
        self.assertGreater(flow.max_scroll, 0)
        g.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3))
        frame(g)
        self.assertGreater(flow.scroll, 0)

    def test_build_then_save_chooses_the_four_decks_on_the_deck_screen(self):
        g = self.gui()
        flow = self.finished(g)
        click(g, self.btn(flow, "build"))
        self.assertEqual(flow.stage, "decks")
        self.assertTrue(all(d is not None for d in flow.decks))
        click(g, self.btn(flow, "save"))
        frame(g)
        self.assertIsNone(g.modal)
        menu = g.menu
        ids = flow.pod.deck_ids
        self.assertEqual(menu.fmt, "commander")
        self.assertEqual((menu.mine_id, menu.opps, menu.count), (ids[0], ids[1:], 3))
        self.assertEqual(menu.message, (ds.CHOSEN_SAID, fmenu.GREEN))
        self.assertEqual(g.deck_choice["mine"], ids[0])
        tags = [t for t, _c in menu.row_tags(menu.mine)]
        self.assertIn(fmenu.DRAFT_TAG_TEXT, tags)
        self.assertIn("YOU", tags)
        self.assertEqual({e.id for e in menu.visible() if fmenu.DRAFT_TAG_TEXT in [t for t, _c in menu.row_tags(e)]}, set(ids))

    def test_start_plays_them_as_a_commander_draft_game(self):
        g = self.gui()
        flow = self.finished(g)
        flow.to_decks(g)
        flow.save_decks(g)
        frame(g)
        g.menu.start(g)
        self.assertEqual(g.launcher.fmts, ["commander_draft"])
        self.assertEqual(g.current_format, "commander_draft")
        mine, opps = g.launcher.calls[0]
        self.assertEqual(len(opps), 3)
        self.assertEqual(len(mine[0]) + len(mine[1]), 60)

    def test_without_a_commander_there_is_no_deck_for_you(self):
        g = self.gui()
        flow = self.finished(g)
        real = flow.pod.build_all()
        with mock.patch.object(flow.pod, "build_all", lambda: [None] + real[1:]):
            click(g, self.btn(flow, "build"))
        frame(g)
        self.assertNotIn("save", self.names(flow))
        key(g, pygame.K_RETURN)
        self.assertEqual(flow.stage, "decks")
        self.assertEqual(os.listdir(self.lib) if os.path.isdir(self.lib) else [], [])

    def test_esc_on_the_decks_goes_back_to_the_review(self):
        g = self.gui()
        flow = self.finished(g)
        flow.to_decks(g)
        frame(g)
        key(g, pygame.K_ESCAPE)
        self.assertEqual(flow.stage, "review")
        self.assertIsNotNone(g.modal)

    # ---- the deck screen and Start -----------------------------------------------------------------------------------------------
    def test_draft_decks_are_listed_under_commander_only(self):
        g = self.gui()
        flow = self.finished(g)
        flow.to_decks(g)
        flow.save_decks(g)
        frame(g)
        menu = g.menu
        shown = {e.id for e in menu.visible()}
        self.assertTrue(set(flow.pod.deck_ids) <= shown)
        menu.set_format("brawl")
        self.assertFalse(set(flow.pod.deck_ids) & {e.id for e in menu.visible()})

    def test_a_draft_deck_may_meet_a_commander_deck_but_not_a_brawl_one(self):
        g = self.gui(title=False)
        flow = ds.DraftFlow(g, root=self.pods, lookup=every_card_known, cube_folder=self.cubes)
        flow.job.thread.join(10)
        flow.start_draft(g)
        play_out(flow.pod)
        entries = flow.pod.save_decks(flow.pod.build_all(), self.lib, self.tmp)
        decks = lib.list_decks(self.lib, self.samples, self.tmp)
        goreclaw = next(e for e in decks if "Goreclaw" in " ".join(e.commanders or []))
        krenko = next(e for e in decks if e.format == "brawl")
        self.assertIsNone(g.start_game(entries[0], [goreclaw], 1))
        self.assertIn("Brawl", g.start_game(entries[0], [krenko], 1))

    # ---- fit -------------------------------------------------------------------------------------------------------------------
    def test_every_stage_fits_at_every_size(self):
        for size, scale in SIZES:
            g = self.gui(size, scale)
            flow = self.open_draft(g)
            stages = []
            stages.append(("setup", self.check_fit(g, flow)))
            flow.start_draft(g)
            pos = None
            frame(g)
            if flow.tiles:
                pos = flow.tiles[0][0].center
                click(g, pos)
            stages.append(("pick", self.check_fit(g, flow, tiles=True)))
            play_out(flow.pod)
            flow.to_review()
            frame(g)
            stages.append(("review", self.check_fit(g, flow)))
            flow.to_decks(g)
            frame(g)
            stages.append(("decks", self.check_fit(g, flow)))
            flow.pod.discard()                                   # the next size starts a draft of its own
            self.close(g)

    def check_fit(self, g, flow, tiles=False):
        frame(g)
        screen = pygame.Rect(0, 0, g.L.W, g.L.H)
        where = (g.L.W, g.L.H, g.text_scale, flow.stage)
        self.assertTrue(screen.contains(flow.rect), where)
        rects = [r for r, _n in flow.buttons]
        for r in rects:
            self.assertTrue(flow.rect.contains(r), (where, r))
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(a.colliderect(b), (where, a, b))
        if tiles:
            ts = [r for r, _c in flow.tiles]
            self.assertTrue(ts, where)
            for r in ts:
                self.assertTrue(flow.rect.contains(r), (where, r))
                self.assertGreaterEqual(r.w, 40, where)                   # big enough to see
            for i, a in enumerate(ts):
                for b in ts[i + 1:]:
                    self.assertFalse(a.colliderect(b), (where, a, b))
            for r in ts:
                for b in rects:
                    self.assertFalse(r.colliderect(b), (where, r, b))
        return True


class ToolTests(unittest.TestCase):
    def test_a_commander_soak_may_play_draft_decks(self):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import soak
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for name, fmt in (("a", "commander_draft"), ("b", None), ("c", "brawl"), ("d", "standard")):
            with open(os.path.join(tmp, name + ".txt"), "w", encoding="utf-8") as f:
                f.write(formats.with_format("Commander\n1 X\n\nDeck\n59 Forest\n", fmt or "commander"))
        with mock.patch("paths.sample_dir", lambda: tmp):
            self.assertEqual([os.path.basename(p) for p in soak.deck_pool("sample", "commander")], ["a.txt", "b.txt"])
            self.assertEqual([os.path.basename(p) for p in soak.deck_pool("sample", "brawl")], ["c.txt"])

    def test_the_console_warns_only_under_60(self):
        import contextlib
        import io
        import deck_loader
        for n, warned in ((59, False), (75, False), (58, True)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                deck_loader.describe_deck("You", ["A"], ["x"] * n, "commander_draft")
            self.assertEqual("at least 60" in out.getvalue(), warned, n)


class GridTests(unittest.TestCase):
    def test_the_cards_are_as_big_as_fit_and_never_overlap(self):
        area = pygame.Rect(10, 20, 900, 600)
        for n in (20, 18, 11, 2, 1):
            rs = ds.grid(n, area, 8, 20, 300)
            self.assertEqual(len(rs), n)
            for r in rs:
                self.assertTrue(area.contains(r.inflate(0, 0).union(r.move(0, 20))), (n, r))
                self.assertLessEqual(r.w, 300)
            for i, a in enumerate(rs):
                for b in rs[i + 1:]:
                    self.assertFalse(a.colliderect(b))
        self.assertEqual(ds.grid(0, area, 8, 20, 300), [])


class SourceTests(unittest.TestCase):
    def test_new_files_have_no_colour_literals_and_the_licence_line(self):
        from tests.test_ad1 import GUARDED
        self.assertIn("draft_screens.py", GUARDED)
        for name in ("draft_screens.py", "draft_pod.py", "tools/cd2_screens.py", "tests/test_cd2.py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                self.assertEqual(f.readline().strip(), "# SPDX-License-Identifier: GPL-3.0-or-later", name)

    def test_the_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in version.VERSION.split(".")), (0, 28, 53, 5))


# ==================================================================================================================================
TYPE_WORDS = {"Legendary", "Basic", "Snow", "World", "Artifact", "Creature", "Enchantment", "Instant", "Sorcery", "Land",
              "Planeswalker", "Battle", "Kindred", "Tribal"}


def forge_cube(runtime, seed=21):
    """A Cube Cobra-shaped cube of REAL cards, read from this Forge's own scripts: 12 mono-coloured legendary creatures and 36
    other spells per colour, 20 two-colour legends, 20 colourless artifacts - 280 cards, every one a card Forge can play."""
    rng = random.Random(seed)
    root = os.path.join(runtime, "res", "cardsfolder")
    want = {("legend", c): 12 for c in "WUBRG"}
    want.update({("spell", c): 36 for c in "WUBRG"})
    want[("gold", "")] = 20
    want[("artifact", "")] = 20
    out = []
    for letter in sorted(os.listdir(root)):
        folder = os.path.join(root, letter)
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            if all(v <= 0 for v in want.values()):
                break
            try:
                with open(os.path.join(folder, fn), encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            if "AlternateMode" in text or "\nALTERNATE" in text or "AI:RemoveDeck" in text:
                continue
            fields = {}
            for ln in text.splitlines():
                k, _s, v = ln.partition(":")
                fields.setdefault(k.strip(), v.strip())
            name, cost, types = fields.get("Name"), fields.get("ManaCost", ""), fields.get("Types", "").split()
            if not name or not types or cost in ("", "no cost") or "Land" in types or "/" in cost:
                continue
            syms = cost.split()
            colours = "".join(c for c in "WUBRG" if c in syms)
            cmc = sum(int(s) if s.isdigit() else 1 for s in syms)
            if cmc > 7:
                continue
            legend = "Legendary" in types and "Creature" in types
            if legend and len(colours) == 1:
                slot = ("legend", colours)
            elif legend and len(colours) == 2:
                slot = ("gold", "")
            elif not legend and len(colours) == 1 and ("Creature" in types or "Instant" in types or "Sorcery" in types):
                slot = ("spell", colours)
            elif not legend and not colours and "Artifact" in types and "Creature" not in types:
                slot = ("artifact", "")
            else:
                continue
            if want.get(slot, 0) <= 0:
                continue
            want[slot] -= 1
            k = next((i for i, t in enumerate(types) if t not in TYPE_WORDS), len(types))
            type_line = " ".join(types[:k]) + (" — " + " ".join(types[k:]) if types[k:] else "")
            pt = fields.get("PT", "")
            out.append({"name": name, "type": type_line, "oracle_text": fields.get("Oracle", ""),
                        "parsed_cost": [s.lower() for s in syms], "cmc": cmc, "colors": list(colours),
                        "color_identity": list(colours), "elo": rng.uniform(1100, 1650),
                        "power": pt.split("/")[0] if "/" in pt else None, "oracle_id": name})
    return {"name": "Forge Cube", "shortId": "forgecube", "owner": {"username": "test"}, "dateLastUpdated": 1,
            "cards": {"mainboard": [{"details": d} for d in out]}}


@unittest.skipUnless(live.live_enabled(), "needs Forge and Java (tests/live.py)")
class LiveDraftGameTests(unittest.TestCase):
    """CD1's spec, section 5, first job: Forge starts a 4-player Commander game with drafted 60-card decks (doubles included)."""

    def setUp(self):
        fc.sync_bridge()
        self.tmp = tempfile.mkdtemp(prefix="cd2_live_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(mock.patch.object(ft, "DECK_DIR", os.path.join(self.tmp, "decks")))

    def test_a_drafted_pod_plays_a_4_player_commander_game_through_the_table(self):
        from tests.forge_bot import Bot
        cube = cs.from_cubecobra(forge_cube(fc.DEFAULT_RUNTIME), "forgecube")
        check = cs.check_forge(cube.cards, cs.forge_script_lookup())
        self.assertEqual(len(check.missing), 0, check.missing[:5])
        self.assertGreaterEqual(len(check.playable), 240)
        pod = dp.Pod.new(cube, check, seed=13, root=os.path.join(self.tmp, "pods"))
        play_out(pod)
        decks = pod.build_all()
        lib_dir, samples = os.path.join(self.tmp, "my_decks"), os.path.join(self.tmp, "samples")
        os.makedirs(samples)
        entries = pod.save_decks(decks, lib_dir, self.tmp)
        spell = pod.cards[decks[0].spells[0]].name               # a second copy of a spell in place of a basic: 903.13f(2)
        basic = next(n for n, k in decks[0].basics.items() if k)
        with open(entries[0].path, encoding="utf-8") as f:
            text = f.read()
        lines = text.split("\n")
        lines[lines.index(f"1 {spell}")] = f"2 {spell}"
        b = next(i for i, ln in enumerate(lines) if ln.endswith(" " + basic))
        lines[b] = f"{int(lines[b].split()[0]) - 1} {basic}"
        with open(entries[0].path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        entries[0]._mtime = None
        entries[0].load()
        self.assertEqual((entries[0].deck.count(spell), entries[0].total), (2, 60))
        settings = os.path.join(self.tmp, "settings.json")
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"passing_version": 43, "tour_done": 99}, f)
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), settings_path=settings,
                            launcher=ft.Launcher(None, "Karl", seed=4242), saves_dir=os.path.join(self.tmp, "saves"),
                            deck_dirs=(lib_dir, samples, self.tmp))
        self.addCleanup(lambda: ScreenTests.close(gui))
        gui.open_menu()
        self.assertTrue(gui.menu.choose_draft(entries[0].id, [e.id for e in entries[1:]]))
        gui.menu.start(gui)
        self.assertIsNone(gui.menu, gui.toast)
        self.assertEqual(gui.current_format, "commander_draft")
        bot, t0, seen = Bot(gui.session), time.time(), None
        turns = set()
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
        self.assertEqual(gui.session.format_used, "commander")
        self.assertEqual([life for life, _c, _n in seen], [40] * 4)
        by_name = {p["name"]: p for p in gui.state["players"]}
        self.assertEqual(len(by_name), 4)
        for life, cmd, cards in seen:
            self.assertEqual(len(cmd), 1)
            self.assertEqual(cards, 59)                          # 60 cards: the commander in the command zone, 59 in hand + library
        mine = [c for c in entries[0].commanders]
        self.assertEqual(seen[0][1], mine)
        self.assertGreaterEqual(len(turns), 4, sorted(turns))
        self.assertEqual(gui.session.checks, [])
        import journal as gjournal
        self.assertEqual(gjournal.read_full(gui.journal.path)["start"]["format"], "commander_draft")


if __name__ == "__main__":
    unittest.main()
