# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CD4: building your drafted deck by hand (draft_builder.py; the screen in draft_screens.py; Play again / Edit deck at the end
of a draft game). Spec: docs/incoming/cd1/SPEC_CUBE_DRAFT_2026-10-09.md, section 7.

  ModelTests     add / remove, the commander and a pair, the greying, the steppers, Suggest lands, Auto-build gives a legal 60,
                 the counter, filters, a saved deck read back by name, only a commander problem blocks
  ClassicTests   a classic pod: no commander, any colours, 40 cards, the cube's own basics
  ScreenTests    the build stage on the draft window: open it from the decks stage, click to add and take out, the commander list,
                 steppers, Auto-build, Done keeps the deck; fit at 5 sizes and 200% text
  EndTests       a draft game's end dialog has Play again and Edit deck; Edit deck opens the builder on the saved deck and saves it
                 in place; other games' end dialogs are as before
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import cube_source as cs
import draft_builder as db
import draft_deck as dd
import draft_pod as dp
import tests.test_cd2 as cd2
import tests.test_cd3 as cd3


def drafted_pod(root, seed=7):
    cube, check = cd2.made_up_check()
    pod = dp.Pod.new(cube, check, seed=seed, root=root)
    cd2.play_out(pod)
    return pod


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cd4_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pod = drafted_pod(self.tmp)
        self.auto = self.pod.build(0)
        self.b = db.Builder(self.pod, self.auto)

    def test_it_starts_as_the_auto_built_deck_and_counts_60(self):
        self.assertEqual(self.b.total(), 60)
        self.assertEqual(self.b.counter(), "60 / 60")
        self.assertEqual(self.b.problems(), [])
        self.assertFalse(self.b.changed)

    def test_take_out_and_put_back(self):
        i = self.b.main[0]
        self.assertTrue(self.b.remove(i))
        self.assertEqual(self.b.counter(), "59 / 60")
        self.assertIn(i, self.b.available())
        self.assertTrue(self.b.add(i))
        self.assertNotIn(i, self.b.available())
        self.assertFalse(self.b.add(i), "the same card twice")
        self.assertTrue(self.b.changed)

    def test_cards_outside_the_commanders_colours_are_greyed_and_refused(self):
        off = [i for i in self.b.pool if not self.b.in_deck(i) and not self.b.fits(i)]
        self.assertTrue(off, "this pool has no off-colour card - the test proves nothing")
        self.assertIn("outside your commander's colours", self.b.why_not(off[0]))
        self.assertFalse(self.b.add(off[0]))

    def test_no_commander_nothing_goes_in(self):
        b = db.Builder(self.pod, None)
        some = b.pool[0]
        self.assertIn("Choose a commander first", b.why_not(some))
        self.assertFalse(b.add(some))
        self.assertIn("Choose a commander", " ".join(b.problems()))
        self.assertTrue(b.blocking())

    def test_choosing_another_commander_drops_what_no_longer_fits(self):
        opts = self.b.commander_options()
        other = next(o for o in opts if dd.identity_of(self.pod.cards, o) != self.b.identity())
        out = self.b.set_commanders(other)
        ident = self.b.identity()
        self.assertTrue(all(self.b.fits(i) for i in self.b.main))
        self.assertTrue(set(self.b.basics) <= set(self.b.basic_names()))
        self.assertTrue(all(isinstance(n, str) for n in out))
        self.assertEqual(ident, dd.identity_of(self.pod.cards, other))

    def test_a_partner_pair(self):
        from tests.test_cd1 import details
        raw = [details("Tymna the Weaver", "Legendary Creature — Human Cleric", "WB", 1500, ["1", "w", "b"], "Partner", "2"),
               details("Kraum, Ludevic's Opus", "Legendary Creature — Zombie Horror", "UR", 1450, ["3", "u", "r"],
                       "Flying, haste\nPartner", "4"),
               details("W Spell", "Instant", "W", 1300, ["w"]), details("G Spell", "Instant", "G", 1300, ["g"])]
        cards = list(cs.from_cubecobra({"name": "p", "cards": {"mainboard": [{"details": d} for d in raw]}}, "p").cards)

        class Pod:
            classic, station = False, {}
            def __init__(self):
                self.cards, self.z = cards, dd.zscores(cards)
            def pool(self, seat=0):
                return [0, 1, 2, 3]
            def problems(self, seat, deck):
                main = list(deck.spells + deck.lands) + [n for n, k in deck.basics.items() for _ in range(k)]
                return dd.check(cards, self.pool(), list(deck.commanders), main)
        b = db.Builder(Pod(), None)
        self.assertIn((0, 1), b.commander_options())
        b.set_commanders((0, 1))
        self.assertEqual(b.basic_names(), ["Plains", "Island", "Swamp", "Mountain"])
        self.assertTrue(b.add(2))
        self.assertFalse(b.add(3))                                   # green is outside Tymna + Kraum
        self.assertEqual(b.blocking(), [])

    def test_basic_steppers_follow_the_commanders_colours(self):
        names = self.b.basic_names()
        cols = [c for c in "WUBRG" if c in self.b.identity()]
        self.assertEqual(names, [cs.BASIC_NAME[c] for c in cols] or ["Wastes"])
        before = self.b.basics.get(names[0], 0)
        self.assertTrue(self.b.step_basic(names[0], +1))
        self.assertEqual(self.b.basics.get(names[0], 0), before + 1)
        self.assertEqual(self.b.counter(), "61 / 60")
        self.assertFalse(self.b.step_basic("Wastes" if names != ["Wastes"] else "Forest", +1), "a basic outside the colours")
        for _ in range(before + 3):
            self.b.step_basic(names[0], -1)
        self.assertNotIn(names[0], self.b.basics)

    def test_suggest_lands_reaches_the_deck_size(self):
        for _ in range(10):
            self.b.remove(self.b.main[-1])
        self.b.basics = {}
        self.b.suggest_lands()
        self.assertEqual(self.b.total(), 60)
        self.assertEqual(self.b.problems(), [])

    def test_auto_build_keeps_your_commander_and_is_legal(self):
        other = next(o for o in self.b.commander_options() if o != tuple(self.auto.commanders))
        self.b.set_commanders(other)
        self.assertTrue(self.b.auto_build())
        self.assertEqual(tuple(self.b.commanders), tuple(other))
        self.assertEqual(self.b.total(), 60)
        self.assertEqual(self.b.problems(), [])

    def test_columns_and_filters(self):
        cols = self.b.columns()
        self.assertEqual(list(cols), list(db.MV_COLUMNS) + ["Lands"])
        self.assertEqual(sum(len(v) for v in cols.values()), len(self.b.main))
        for i in cols["2"]:
            self.assertEqual(int(self.pod.cards[i].cmc), 2)
        for i in self.b.main[:6]:
            self.b.remove(i)
        everything = self.b.available()
        self.b.toggle_filter("type", "land")
        self.assertTrue(all(cs.is_land(self.pod.cards[i]) for i in self.b.available()))
        self.b.clear_filters()
        self.b.toggle_filter("mv", "2")
        self.assertTrue(all(int(self.pod.cards[i].cmc) == 2 for i in self.b.available()))
        self.b.toggle_filter("mv", "2")
        self.assertEqual(self.b.available(), everything)

    def test_a_saved_deck_reads_back_by_name(self):
        commanders, main = dd.deck_names(self.pod.cards, self.auto)
        b = db.Builder(self.pod, None)
        lost = b.load_names(commanders, main + ["Black Lotus"])
        self.assertEqual(lost, ["Black Lotus"])
        self.assertEqual(sorted(b.main), sorted(list(self.auto.spells) + list(self.auto.lands)))
        self.assertEqual(b.basics, {n: k for n, k in self.auto.basics.items() if k})

    def test_only_a_commander_problem_blocks(self):
        self.b.remove(self.b.main[0])
        self.assertTrue(self.b.problems())
        self.assertEqual(self.b.blocking(), [])


class ClassicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cd4c_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cube, check = cd3.stock_cube(cd3.TURBO)
        self.pod = dp.Pod.new(cube, check, seed=9, root=self.tmp)
        cd2.play_out(self.pod)
        self.b = db.Builder(self.pod, self.pod.build(0))

    def test_forty_cards_any_colour_the_cubes_basics(self):
        self.assertEqual((self.b.size, self.b.counter()), (40, "40 / 40"))
        self.assertEqual(self.b.commander_options(), [])
        self.assertTrue(all(self.b.fits(i) for i in self.b.pool))
        self.assertEqual(set(self.b.basic_names()), set(self.pod.station.values()))
        self.assertEqual(self.b.problems(), [])
        self.assertEqual(self.b.blocking(), [])

    def test_a_splash_by_hand(self):
        cols = self.b.colours_played()
        other = next((i for i in self.b.available() if set(self.pod.cards[i].identity or ()) - cols
                      and not cs.is_land(self.pod.cards[i])), None)
        if other is None:
            self.skipTest("no off-colour spell in this pool")
        self.assertTrue(self.b.add(other))
        self.assertTrue(set(self.pod.cards[other].identity) <= self.b.colours_played())

    def test_auto_build_is_the_classic_builder(self):
        self.b.main = self.b.main[:5]
        self.assertTrue(self.b.auto_build())
        self.assertEqual(self.b.total(), 40)
        self.assertEqual(self.b.problems(), [])


class ScreenTests(unittest.TestCase):
    """tests/test_cd2.py's offline draft window set-up (borrowed, not its tests)."""
    setUp = cd2.ScreenTests.setUp
    gui = cd2.ScreenTests.gui
    close = staticmethod(cd2.ScreenTests.close)

    def flow_at_decks(self, g):
        import draft_screens as ds
        f = ds.DraftFlow(g, root=self.pods, lookup=cd2.every_card_known, cube_folder=self.cubes)
        f.job.thread.join(10)
        f.start_draft(g)
        cd2.play_out(f.pod)
        f.to_decks(g)
        g.modal = f
        cd2.frame(g, 2)
        return f

    def test_edit_my_deck_then_done_keeps_what_you_built(self):
        g = self.gui(title=False)
        f = self.flow_at_decks(g)
        self.assertIn("edit", [n for _r, n in f.buttons])
        f.press(g, "edit")
        cd2.frame(g, 2)
        self.assertEqual(f.stage, "build")
        rect, _w, card = next(t for t in f.btiles if t[1] == "deck")
        cd2.click(g, rect.center)
        self.assertNotIn(card, f.builder.main)
        self.assertEqual(f.builder.counter(), "59 / 60")
        f.press(g, "build_done")
        self.assertEqual(f.stage, "decks")
        self.assertEqual(dd.deck_size(f.decks[0]), 59)
        self.assertIn("at least 60", " ".join(f.problems[0]))
        self.assertTrue(f.save_decks(g))
        with open(next(e for e in __import__("deck_library").list_decks(*self.dirs) if e.id == f.pod.deck_ids[0]).path,
                  encoding="utf-8") as fh:
            self.assertIn("built by hand", fh.read())

    def test_pool_clicks_add_and_off_colour_says_why(self):
        g = self.gui(title=False)
        f = self.flow_at_decks(g)
        f.to_build(g)
        cd2.frame(g, 2)
        b = f.builder
        if not any(b.fits(c) for _r, w, c in f.btiles if w == "pool"):
            make_room_in_pool(self, g, f)
        rect, _w, card = next(t for t in f.btiles if t[1] == "pool" and b.fits(t[2]))
        cd2.click(g, rect.center)
        self.assertIn(card, b.main)
        b.clear_filters()
        cd2.frame(g, 2)
        off = next((t for t in f.btiles if t[1] == "pool" and not b.fits(t[2])), None)
        if off is not None:
            cd2.frame(g, 2)
            off = next(t for t in f.btiles if t[1] == "pool" and not b.fits(t[2]))
            cd2.click(g, off[0].center)
            self.assertIn("outside your commander's colours", f.message[0])

    def test_the_commander_list_steppers_auto_build_and_suggest_lands(self):
        g = self.gui(title=False)
        f = self.flow_at_decks(g)
        f.to_build(g)
        cd2.frame(g, 2)
        f.press(g, "choose_cmd")
        cd2.frame(g, 2)
        names = [n for _r, n in f.buttons]
        self.assertTrue(all(n.startswith("cmd_") for n in names), names)
        opts = f.builder.commander_options()
        k = next(k for k, o in enumerate(opts) if tuple(o) != tuple(f.builder.commanders))
        r = next(r for r, n in f.buttons if n == f"cmd_{k}")
        cd2.click(g, r.center)
        self.assertEqual(tuple(f.builder.commanders), tuple(opts[k]))
        self.assertFalse(f.choosing)
        f.press(g, "auto")
        self.assertEqual((f.builder.total(), f.builder.problems()), (60, []))
        name = f.builder.basic_names()[0]
        before = f.builder.basics.get(name, 0)
        cd2.frame(g, 2)
        r = next(r for r, n in f.buttons if n == f"basic+_{name}")
        cd2.click(g, r.center)
        self.assertEqual(f.builder.basics.get(name, 0), before + 1)
        f.press(g, "suggest")
        self.assertEqual(f.builder.total(), 60)

    def test_escape_goes_back_to_the_decks(self):
        g = self.gui(title=False)
        f = self.flow_at_decks(g)
        f.to_build(g)
        cd2.key(g, __import__("pygame").K_ESCAPE)
        self.assertEqual(f.stage, "decks")

    def test_it_fits(self):
        for size in ((1360, 840), (1920, 1080), (1100, 700), (1280, 720), (4096, 1949)):
            for scale in (None, 2.0):
                with self.subTest(size=size, scale=scale):
                    g = self.gui(size=size, scale=scale, title=False)
                    f = self.flow_at_decks(g)
                    f.to_build(g)
                    cd2.frame(g, 2)
                    W, H = g.screen.get_size()
                    rects = [r for r, _n in f.buttons]
                    for r in rects:
                        self.assertTrue(pygame_rect(0, 0, W, H).contains(r), (size, scale, r))
                    named = [(r, n) for r, n in f.buttons if not n.startswith("f_")]
                    for a in range(len(named)):
                        for c in range(a + 1, len(named)):
                            self.assertFalse(named[a][0].colliderect(named[c][0]), (named[a][1], named[c][1], size, scale))
                    self.assertTrue(any(w == "pool" for _r, w, _c in f.btiles) or not f.builder.available())
                    f.pod.discard()


def make_room_in_pool(test, g, f):
    """Auto-build took every pool card that fits: put one back where it shows. Round ANS1: it used to put back the deck's first card,
    which can sort below the pool's visible part (the draft's seed is random - seed 4 failed every time), and the test then found no
    card to click. Now: the deck's cheapest spell, the pool filtered to its colour and scrolled to the top."""
    b = f.builder
    spells = [i for i in b.main if not cs.is_land(b.cards[i])] or list(b.main)
    low = min(spells, key=lambda i: (b.cards[i].cmc, b.cards[i].name))
    b.remove(low)
    b.toggle_filter("colour", db.colour_key(b.cards[low]))
    f.build_scroll = 0
    cd2.frame(g, 2)
    test.assertTrue(any(t[1] == "pool" and t[2] == low for t in f.btiles), "the card put back is not shown")


def pygame_rect(*a):
    import pygame
    return pygame.Rect(*a)


class EndTests(unittest.TestCase):
    setUp = cd2.ScreenTests.setUp
    gui = cd2.ScreenTests.gui
    close = staticmethod(cd2.ScreenTests.close)

    def saved_pod(self, g):
        f = ScreenTests.flow_at_decks(self, g)
        f.save_decks(g)
        import deck_library as lib
        entries = {e.id: e for e in lib.list_decks(*self.dirs)}
        return f.pod, [entries[i] for i in f.pod.deck_ids]

    def test_draft_of_names_the_pod_only_for_a_drafted_deck(self):
        import forge_table as ft
        import deck_library as lib
        g = self.gui(title=False)
        pod, entries = self.saved_pod(g)
        self.assertEqual(ft.draft_of(entries[0]), {"pod": os.path.basename(pod.folder), "path": entries[0].path})
        normal = next(e for e in lib.list_decks(*self.dirs) if e.format == "commander" and not e.id in pod.deck_ids)
        self.assertIsNone(ft.draft_of(normal))

    def test_the_end_dialog_offers_play_again_and_edit_deck_after_a_draft_game(self):
        import forge_dialogs as fdlg
        g = self.gui(title=False)
        pod, entries = self.saved_pod(g)
        g.modal = None
        import forge_table as ft
        g.current_draft = ft.draft_of(entries[0])
        g.current_decks = ((entries[0].commanders, entries[0].deck), [(e.commanders, e.deck) for e in entries[1:]])
        g.end_continue()
        self.assertIsInstance(g.modal, fdlg.GameOverDialog)
        cd2.frame(g, 2)
        names = [n for _r, n in g.modal.buttons]
        self.assertEqual(names[:2], ["extra_0", "extra_1"])
        self.assertEqual([lbl for lbl, _cb in g.modal.extra], ["Play again", "Edit deck"])
        g.current_draft = None                        # Round ANS1 (Karl, 13 Oct): an ordinary game gets Play again, not Edit deck
        g.end_continue()
        cd2.frame(g, 2)
        self.assertEqual([n for _r, n in g.modal.buttons], ["extra_0", "new", "close"])
        self.assertEqual([lbl for lbl, _cb in g.modal.extra], ["Play again"])

    def test_edit_deck_saves_in_place_and_plays_again(self):
        import forge_table as ft
        import draft_screens as ds
        g = self.gui(title=False)
        pod, entries = self.saved_pod(g)
        g.modal = None
        g.current_draft = ft.draft_of(entries[0])
        g.current_decks = ((entries[0].commanders, entries[0].deck), [(e.commanders, e.deck) for e in entries[1:]])
        with mock.patch.object(ds.dp, "pods_dir", lambda: self.pods):
            g.edit_draft_deck()
        f = g.modal
        self.assertIsInstance(f, ds.DraftFlow)
        self.assertEqual((f.stage, f.builder.total()), ("build", 60))
        cd2.frame(g, 2)
        f.builder.remove(f.builder.main[0])
        f.builder.suggest_lands()
        with mock.patch.object(g, "restart_game") as again:
            self.assertTrue(f.save_edit(g, play=True))
        again.assert_called_once()
        with open(entries[0].path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("built by hand", text)
        self.assertEqual(sum(int(ln.split()[0]) for ln in text.splitlines() if ln[:1].isdigit()), 60)
        self.assertEqual(g.current_decks[0][1], __import__("deck_library").DeckEntry(entries[0].path, "x", False, self.tmp)
                         .load().deck)


if __name__ == "__main__":
    unittest.main()
