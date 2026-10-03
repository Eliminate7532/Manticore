# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 27b: a deck for each AI seat (or Random), deck search, remembered choices and the known-freeze warning, driven headlessly."""
import json
import os
import random
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import deck_library as lib
import forge_dialogs as dlg
import forge_menu as fmenu
import tests.live as live
from tests.test_deck_screen import TempDecks, sample_text
from tests.test_forge_table import click, frame, key, make_gui, point_for_card


def deck_text(commander):
    """The sample list with another commander on the first line (still exactly 100 cards)."""
    lines = sample_text().strip().split("\n")
    lines[0] = f"1 {commander}"
    return "\n".join(lines) + "\n"


class SeatTests(TempDecks):
    def setUp(self):
        super().setUp()
        for name, commander in (("Elves", "Lathril, Blade of the Elves"), ("Tokens", "Adeline, Resplendent Cathar"),
                                ("Sac", "Teysa Karlov")):
            lib.save_text(name, deck_text(commander), self.lib, self.tmp)

    def gui3(self, **kw):
        gui = self.idle_gui(**kw)
        for _ in range(2):
            click(gui, self.button_point(gui, "plus"))
        self.assertEqual(gui.menu.count, 3)
        return gui

    def test_one_slot_per_ai_seat(self):
        gui = self.idle_gui()
        self.assertEqual(sorted(gui.menu.slots), ["mine", "opp"])
        click(gui, self.button_point(gui, "plus"))
        self.assertEqual(sorted(gui.menu.slots), ["mine", "opp", "opp2"])
        click(gui, self.button_point(gui, "plus"))
        self.assertEqual(sorted(gui.menu.slots), ["mine", "opp", "opp2", "opp3"])
        click(gui, self.button_point(gui, "minus"))
        self.assertEqual(sorted(gui.menu.slots), ["mine", "opp", "opp2"])

    def test_clicks_fill_my_deck_then_each_ai_in_turn(self):
        gui = self.gui3()
        click(gui, self.row_point(gui, "Elves"))
        self.assertEqual(gui.menu.active, "opp")
        click(gui, self.row_point(gui, "Tokens"))
        self.assertEqual(gui.menu.active, "opp2")
        click(gui, self.row_point(gui, "Sac"))
        self.assertEqual(gui.menu.active, "opp3")
        click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
        self.assertEqual(gui.menu.active, "opp3")             # the last seat stays selected
        click(gui, self.button_point(gui, "start"))
        self.assertIsNone(gui.menu)
        mine, opps = self.launcher.calls[0]
        self.assertEqual(mine[0], ["Lathril, Blade of the Elves"])
        self.assertEqual([o[0][0] for o in opps], ["Adeline, Resplendent Cathar", "Teysa Karlov", "Kinnan, Bonder Prodigy"])
        self.assertEqual(len({o[0][0] for o in gui.current_decks[1]}), 3)          # Restart plays the same three decks

    def test_an_unset_seat_plays_my_deck_and_same_as_mine_resets_one_seat(self):
        gui = self.gui3()
        click(gui, gui.menu.slots["opp2"].center)
        click(gui, self.row_point(gui, "Sac"))
        self.assertEqual(gui.menu.seat_entry(1).name, "Sac")
        self.assertEqual(gui.menu.seat_entry(0), gui.menu.mine)
        click(gui, self.button_point(gui, "same2"))
        self.assertIsNone(gui.menu.opps[1])
        self.assertEqual(gui.menu.seat_entry(1), gui.menu.mine)

    def test_row_tags_name_the_seats(self):
        gui = self.gui3()
        click(gui, self.row_point(gui, "Elves"))
        click(gui, self.row_point(gui, "Tokens"))
        click(gui, self.row_point(gui, "Sac"))
        click(gui, self.row_point(gui, "Tokens"))
        tags = {e.name: [t for t, _c in gui.menu.row_tags(e) if t not in ("NOT LEGAL", "UNCHECKED")] for e in gui.menu.entries}   # BAN1
        self.assertEqual(tags["Elves"], ["YOU"])
        self.assertEqual(tags["Tokens"], ["AI 1,3"])
        self.assertEqual(tags["Sac"], ["AI 2"])

    def test_random_picks_a_deck_nobody_else_plays(self):
        gui = self.gui3()
        gui.menu.rng = random.Random(7)
        click(gui, self.row_point(gui, "Elves"))
        click(gui, self.row_point(gui, "Tokens"))
        click(gui, self.button_point(gui, "random2"))
        self.assertEqual(gui.menu.opps[1], fmenu.RANDOM)
        click(gui, gui.menu.slots["opp3"].center)
        click(gui, self.row_point(gui, "Sac"))
        click(gui, self.button_point(gui, "start"))
        mine, opps = self.launcher.calls[0]
        self.assertEqual(opps[1][0], ["Kinnan, Bonder Prodigy"])            # the only deck nobody else at the table plays
        self.assertIn("AI 2 plays Kinnan NBC (sample)", gui.toast[0])
        self.assertEqual(gui.deck_choice["opps"][1], fmenu.RANDOM)          # the saved choice keeps the seat as Random

    def test_random_reuses_a_deck_when_every_deck_is_taken(self):
        gui = self.gui3()
        for name in ("Elves", "Tokens", "Sac", "Kinnan NBC (sample)"):
            click(gui, self.row_point(gui, name))
        click(gui, self.button_point(gui, "random"))
        click(gui, self.button_point(gui, "start"))
        _mine, opps = self.launcher.calls[0]
        self.assertTrue(opps[0][0])                                            # still a real deck

    def test_seats_and_random_are_remembered(self):
        path = os.path.join(self.tmp, "settings.json")
        gui = self.gui3(settings=path)
        click(gui, self.row_point(gui, "Elves"))
        click(gui, self.row_point(gui, "Tokens"))
        click(gui, self.button_point(gui, "random2"))
        click(gui, gui.menu.slots["opp3"].center)
        click(gui, self.row_point(gui, "Sac"))
        click(gui, self.button_point(gui, "start"))
        with open(path) as f:
            saved = json.load(f)["decks"]
        self.assertEqual(saved["count"], 3)
        self.assertEqual(saved["opps"], ["my_decks/Tokens.txt", fmenu.RANDOM, "my_decks/Sac.txt"])
        self.assertEqual(saved["opp"], "my_decks/Tokens.txt")               # what a pre-27b copy reads
        gui2 = self.idle_gui(settings=path)
        self.assertEqual(gui2.menu.count, 3)
        self.assertEqual(gui2.menu.opps, ["my_decks/Tokens.txt", fmenu.RANDOM, "my_decks/Sac.txt"])

    def test_a_choice_saved_before_27b_gives_every_ai_that_deck(self):
        m = fmenu.DeckMenu(lib.list_decks(*self.dirs), {"mine": "my_decks/Elves.txt", "opp": "my_decks/Sac.txt", "count": 2},
                           dirs=self.dirs)
        self.assertEqual(m.opps, ["my_decks/Sac.txt"] * 3)
        m = fmenu.DeckMenu(lib.list_decks(*self.dirs), {"mine": "my_decks/Elves.txt", "opp": None, "count": "bad"}, dirs=self.dirs)
        self.assertEqual(m.opps, [None] * 3)
        self.assertEqual(m.count, 1)

    def test_a_deck_that_disappears_frees_its_seat(self):
        m = fmenu.DeckMenu(lib.list_decks(*self.dirs), {"opps": ["my_decks/Gone.txt", fmenu.RANDOM, "my_decks/Sac.txt"], "count": 3},
                           dirs=self.dirs)
        self.assertEqual(m.opps, [None, fmenu.RANDOM, "my_decks/Sac.txt"])

    def test_tab_walks_through_the_seats(self):
        gui = self.gui3()
        seen = [gui.menu.active]
        for _ in range(4):
            key(gui, pygame.K_TAB)
            seen.append(gui.menu.active)
        self.assertEqual(seen, ["mine", "opp", "opp2", "opp3", "mine"])
        key(gui, pygame.K_TAB, pygame.KMOD_SHIFT)
        self.assertEqual(gui.menu.active, "opp3")
        click(gui, self.button_point(gui, "minus"))
        self.assertEqual(gui.menu.active, "opp2")                          # a removed seat can't stay selected

    def test_three_seats_fit_the_window_at_every_size(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((1100, 700), 2.0), ((4096, 1949), 1.75)):
            with self.subTest(size=size, scale=scale):
                gui = self.gui3(size=size, scale=scale)
                gui.menu.opps[1] = fmenu.RANDOM
                frame(gui, 1)
                window = pygame.Rect(0, 0, *size)
                slots = [gui.menu.slots[k] for k in ("mine", "opp", "opp2", "opp3")]
                for i, a in enumerate(slots):
                    self.assertTrue(window.contains(a), (a, size))
                    for b in slots[i + 1:]:
                        self.assertFalse(a.colliderect(b), (a, b, size))
                for rect, name in gui.menu.btns:
                    self.assertTrue(window.contains(rect), (name, rect, size))
                    for k in ("opp", "opp2", "opp3"):
                        if name in ("same" + k[3:], "random" + k[3:]) or (k == "opp" and name in ("same", "random")):
                            self.assertTrue(gui.menu.slots[k].contains(rect), (name, rect, gui.menu.slots[k], size))

    def test_start_game_accepts_one_deck_per_seat(self):
        gui = self.idle_gui()
        entries = {e.name: e for e in lib.list_decks(*self.dirs)}
        self.assertIsNone(gui.start_game(entries["Elves"], [entries["Tokens"], entries["Sac"]], 2, {"count": 2}))
        self.assertEqual([o[0][0] for o in gui.current_decks[1]], ["Adeline, Resplendent Cathar", "Teysa Karlov"])
        self.assertIsNone(gui.start_game(entries["Elves"], entries["Sac"], 3, {"count": 3}))       # the old way: one deck for all
        self.assertEqual([o[0][0] for o in gui.current_decks[1]], ["Teysa Karlov"] * 3)


class SearchTests(TempDecks):
    def setUp(self):
        super().setUp()
        for name, commander in (("Tap for Mana (Typal)", "Lathril, Blade of the Elves"),
                                ("What Does The Fox Say (Voltron)", "Light-Paws, Emperor's Voice"),
                                ("Organ Harvesting (Aristocrats)", "Teysa Karlov")):
            lib.save_text(name, deck_text(commander), self.lib, self.tmp)

    def type(self, gui, text):
        for ch in text:
            key(gui, getattr(pygame, "K_" + ch.lower(), pygame.K_SPACE) if ch.isalnum() else pygame.K_MINUS
                if ch == "-" else pygame.K_SPACE, unicode=ch)

    def names(self, gui):
        return [e.name for _r, e in gui.menu.rows]

    def test_typing_filters_by_name_commander_or_style(self):
        gui = self.idle_gui()
        self.assertEqual(len(self.names(gui)), 4)
        self.type(gui, "voltron")
        self.assertEqual(self.names(gui), ["What Does The Fox Say (Voltron)"])
        key(gui, pygame.K_ESCAPE)
        self.assertEqual(gui.menu.search, "")
        self.assertEqual(len(self.names(gui)), 4)
        self.type(gui, "teysa")
        self.assertEqual(self.names(gui), ["Organ Harvesting (Aristocrats)"])
        key(gui, pygame.K_BACKSPACE, pygame.KMOD_CTRL)
        self.assertEqual(gui.menu.search, "")
        self.type(gui, "bonder prodigy")                      # every word must match, in any field
        self.assertEqual(self.names(gui), ["Kinnan NBC (sample)"])

    def test_a_minus_sign_types_into_the_search_instead_of_shrinking_text(self):
        gui = self.idle_gui()
        before = gui.text_scale
        self.type(gui, "light-paws")
        self.assertEqual(gui.menu.search, "light-paws")
        self.assertEqual(gui.text_scale, before)
        self.assertEqual(self.names(gui), ["What Does The Fox Say (Voltron)"])

    def test_no_match_says_so_and_escape_twice_does_not_leave(self):
        gui = self.idle_gui()
        self.type(gui, "zzz")
        self.assertEqual(self.names(gui), [])
        key(gui, pygame.K_ESCAPE)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNotNone(gui.menu)                        # no game behind it: nothing to go back to
        self.assertEqual(len(self.names(gui)), 4)

    def test_arrows_and_clicks_use_the_filtered_list(self):
        gui = self.idle_gui()
        self.type(gui, "aristocrats")
        key(gui, pygame.K_DOWN)
        self.assertEqual(gui.menu.mine.name, "Organ Harvesting (Aristocrats)")
        click(gui, self.button_point(gui, "clear_search"))
        self.assertEqual(gui.menu.search, "")

    def test_importing_clears_the_search_so_the_new_deck_shows(self):
        gui = self.idle_gui()
        self.type(gui, "zzz")
        e = lib.save_text("Brand New", deck_text("Tymna the Weaver"), self.lib, self.tmp)
        gui.menu.imported(e)
        frame(gui, 1)
        self.assertEqual(gui.menu.search, "")
        self.assertIn("Brand New", self.names(gui))


class KnownHangTests(TempDecks):
    POD = ("Valgavoth, Harrower of Souls", "Light-Paws, Emperor's Voice", "Kinnan, Bonder Prodigy", "Ojer Axonil, Deepest Might")

    def setUp(self):
        super().setUp()
        for i, commander in enumerate(self.POD[:2] + self.POD[3:]):
            lib.save_text(f"D{i}", deck_text(commander), self.lib, self.tmp)

    def test_the_known_freezing_pod_asks_first(self):
        self.assertTrue(fmenu.known_hang(set(self.POD) | {"Someone Else"}))
        self.assertFalse(fmenu.known_hang(self.POD[:3]))
        gui = self.idle_gui()
        click(gui, self.button_point(gui, "plus"))
        click(gui, self.button_point(gui, "plus"))
        for name in ("D0", "D1", "Kinnan NBC (sample)", "D2"):
            click(gui, self.row_point(gui, name))
        click(gui, self.button_point(gui, "start"))
        self.assertIsInstance(gui.modal, dlg.QuestionDialog)
        self.assertIn("freeze", gui.modal.title.lower())
        self.assertEqual(self.launcher.calls, [])             # nothing started yet
        gui.modal.answer(True)
        self.assertEqual(len(self.launcher.calls), 1)


def search_request(rid=5):
    """What the bridge sends when a fetch land resolves: an optional pick-one from the library (BridgeGui.chooseSingleEntityForEffect)."""
    card = lambda cid, name: {"kind": "card", "card": {"id": cid, "name": name, "type": "Basic Land", "zone": "Library"}}
    return {"t": "request", "id": rid, "kind": "choose_optional", "title": "Select a card from your library", "min": 0, "max": 1,
            "items": [card(901, "Mountain"), card(902, "Plains")]}


class FetchSearchTests(unittest.TestCase):
    """Round 27b: the fetch-land bug. Space / Enter / Take card with nothing picked used to answer 'find nothing'."""

    def open_search(self):
        gui = make_gui("main1_start", (1360, 840), log=False)
        gui.session.handle(search_request())
        frame(gui, 3)
        self.assertIsInstance(gui.modal, dlg.ChooseDialog)
        self.assertEqual(gui.modal.zone, "library")
        return gui

    def replies(self, gui):
        return [c["value"] for c in gui.session.commands("reply")]

    def test_space_enter_and_esc_with_nothing_picked_do_not_find_nothing(self):
        gui = self.open_search()
        for k in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            key(gui, k)
        self.assertEqual(self.replies(gui), [])
        self.assertIsInstance(gui.modal, dlg.ChooseDialog)             # still open, still asking
        self.assertFalse(gui.modal.valid())                           # and Take card is greyed out
        names = [n for _r, n in gui.modal.buttons] if hasattr(gui.modal, "buttons") else []
        self.assertIn("skip", names)

    def test_pick_then_space_takes_that_card(self):
        gui = self.open_search()
        gui.modal.toggle(1)
        key(gui, pygame.K_SPACE)
        self.assertEqual(self.replies(gui), [[1]])

    def test_find_nothing_is_still_possible_with_its_own_button(self):
        gui = self.open_search()
        rect = next(r for r, n in gui.modal.buttons if n == "skip")
        click(gui, rect.center)
        self.assertEqual(self.replies(gui), [[]])

    def test_other_optional_lists_keep_esc_as_skip(self):
        gui = make_gui("main1_start", (1360, 840), log=False)
        req = search_request()
        req["title"] = "Choose a creature to sacrifice"
        gui.session.handle(req)
        frame(gui, 3)
        key(gui, pygame.K_SPACE)
        self.assertEqual(self.replies(gui), [])                         # Space still needs a pick ...
        key(gui, pygame.K_ESCAPE)
        self.assertEqual(self.replies(gui), [[]])                       # ... but Esc outside a library search means "none", as before


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveFetchTests(unittest.TestCase):
    """Arid Mesa through the real engine and the real table: resolving it with Space, then pressing Space again at the search."""

    def test_space_at_the_search_no_longer_loses_the_land(self):
        import time
        import card_check as cc
        from tests.forge_fake import StubStore
        import forge_table as ft
        chk = cc.Checker(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_decks", "spellslinger_veyran.txt"))
        chk.start()
        try:
            lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                     "humanhand=Brainstorm", "humanbattlefield=Island;Island;Arid Mesa", "humanlibrary=Mountain;Plains;Island;Island",
                     "aihand=", "ailibrary=Forest;Forest;Forest;Forest", "aibattlefield=Grizzly Bears", "removesummoningsickness=true"]
            self.assertTrue(chk.apply(lines, ["Brainstorm"], target="Arid Mesa"))
            s = chk.s
            gui = ft.ForgeTable(s, StubStore(), window_size=(1360, 840))
            frame(gui, 3)
            names = lambda z: sorted(c["name"] for c in s.me()["zones"][z])
            mesa = next(c for c in s.me()["zones"]["battlefield"] if c["name"] == "Arid Mesa")
            click(gui, point_for_card(gui, mesa["id"]))
            end, paid, passed, search = time.time() + 30, False, False, None
            while time.time() < end and search is None:
                s.poll()
                frame(gui, 1)
                msg = (s.state.get("prompt") or {}).get("message") or ""
                if isinstance(gui.modal, dlg.ChooseDialog) and gui.modal.zone == "library":
                    search = gui.modal
                elif gui.modal is None and msg.startswith("Do you want") and not paid:
                    paid = True
                    key(gui, pygame.K_RETURN)                           # pay 1 life
                elif gui.modal is None and msg.startswith("Priority") and s.state.get("stack") and not passed:
                    passed = True
                    key(gui, pygame.K_SPACE)                            # resolve the fetch with Space, as a player would
                time.sleep(0.05)
            self.assertIsNotNone(search, "the library search never opened")
            key(gui, pygame.K_SPACE)                                    # the second Space: used to find nothing
            chk.pump(1.5)
            frame(gui, 1)
            self.assertIs(gui.modal, search)
            self.assertEqual(s.me().get("libraryCount"), 4)
            i = next(i for i, it in enumerate(search.items) if (it.get("card") or {}).get("name") == "Mountain")
            search.toggle(i)
            key(gui, pygame.K_SPACE)
            chk.pump(2.0)
            frame(gui, 1)
            self.assertIn("Mountain", names("battlefield"))
            self.assertIn("Arid Mesa", names("graveyard"))
        finally:
            chk.stop()


class BundledFileNameTests(unittest.TestCase):
    def test_no_bundled_deck_file_looks_like_a_secret_to_the_backup(self):
        """Round 27b: the alpha deck 'tokens_adeline.txt' matched backup.py's '*token*' secret pattern, so every local backup zip left it
        out (and the public export would have too). A bundled deck's file name must pass the same checks as every other file."""
        import fnmatch
        import backup
        for name in os.listdir(lib.SAMPLE_DIR):
            with self.subTest(name=name):
                self.assertFalse(any(fnmatch.fnmatch(name.lower(), pat) for pat in backup.SECRET_NAMES), name)
                self.assertFalse(backup.is_reserved_name(name), name)


if __name__ == "__main__":
    unittest.main()
