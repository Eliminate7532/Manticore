# SPDX-License-Identifier: GPL-3.0-or-later
"""The deck library, the paste-a-deck window and the deck screen (start screen / New game), driven headlessly."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import requests

import legality
import deck_library as lib
import forge_client as fc
import forge_dialogs as dlg
import forge_menu as fmenu
import forge_table as ft
from deck_importer import DeckImportError
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import click, frame, key, move

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(HERE, "sample_decks", "kinnan_nbc_moxfield_export.txt")


def sample_text():
    with open(SAMPLE, "r", encoding="utf-8") as f:
        return f.read()


def other_deck_text():
    """The sample list with another commander on the first line (still exactly 100 cards)."""
    lines = sample_text().strip().split("\n")
    lines[0] = "1 Tymna the Weaver (CMR) 500"
    return "\n".join(lines) + "\n"


class FakeLauncher:
    """Stands in for ft.Launcher: no Java; hands back a session that shows a saved snapshot."""
    runtime = None

    def __init__(self, state="main1_start", fail=None):
        self.state, self.fail, self.calls = state, fail, []

    def start(self, mine, opponents):
        self.calls.append((mine, opponents))
        if self.fail:
            raise RuntimeError(self.fail)
        st = copy.deepcopy(load_state(self.state))
        base = [p for p in st["players"] if p["id"] != st["me"]][0]
        st["players"] = [p for p in st["players"] if p["id"] == st["me"]]
        for k in range(len(opponents)):
            p = copy.deepcopy(base)
            p["id"], p["name"] = 20 + k, f"AI {k + 1}"
            for z in p["zones"].values():
                for c in z:
                    c["id"] += 1000 * (k + 1)
            st["players"].append(p)
        return FakeSession(st, load_log())


class TempDecks(unittest.TestCase):
    """A scratch folder with my_decks/ and sample_decks/, so nothing touches the real ones."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "sample_decks")
        os.makedirs(self.samples)
        shutil.copy(SAMPLE, self.samples)
        self.dirs = (self.lib, self.samples, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_gui(self, size=(1360, 840), scale=None, launcher=None, state="main1_start", settings=None, menu=True):
        st = load_state(state) if isinstance(state, str) else state
        session = FakeSession(st, load_log())
        self.launcher = launcher or FakeLauncher()
        gui = ft.ForgeTable(session, StubStore(), settings_path=settings, window_size=size, launcher=self.launcher,
                            deck_dirs=self.dirs)
        if scale is not None:
            gui.text_scale = scale
        frame(gui, 2)
        if menu:
            gui.open_menu()
            frame(gui, 2)
        return gui

    def idle_gui(self, size=(1360, 840), scale=None, settings=None):
        """The way the program opens with no deck on the command line: no game yet, the deck screen showing."""
        self.launcher = FakeLauncher()
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=settings, window_size=size,
                            launcher=self.launcher, deck_dirs=self.dirs)
        if scale is not None:
            gui.text_scale = scale
        gui.open_menu()
        frame(gui, 2)
        return gui

    def row_point(self, gui, name):
        for rect, e in gui.menu.rows:
            if e.name == name:
                return rect.center
        raise AssertionError(f"no row {name!r}: {[e.name for _, e in gui.menu.rows]}")

    def button_point(self, gui, name):
        for rect, n in gui.menu.btns:
            if n == name:
                return rect.center
        raise AssertionError(f"no button {name!r}")

    def dialog_button(self, gui, name):
        for rect, n in gui.modal.buttons:
            if n == name:
                return rect.center
        raise AssertionError(f"no dialog button {name!r}")


class LibraryTests(TempDecks):
    def test_lists_the_bundled_sample(self):
        entries = lib.list_decks(*self.dirs)
        self.assertEqual([e.name for e in entries], ["Kinnan NBC (sample)"])
        e = entries[0]
        self.assertTrue(e.ok and e.builtin)
        self.assertEqual(e.commanders, ["Kinnan, Bonder Prodigy"])
        self.assertEqual(e.total, 100)
        self.assertEqual([p for p in e.problems(os.path.join(self.tmp, "no_forge_here"))
                          if not legality.is_unchecked_line(p[0])], [])       # no card folder: nothing to check (BAN1: nor card data)

    def test_save_then_list(self):
        e = lib.save_text("Tymna", other_deck_text(), self.lib, self.tmp)
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "Tymna.txt")))
        self.assertEqual(e.commanders, ["Tymna the Weaver"])
        names = [x.name for x in lib.list_decks(*self.dirs)]
        self.assertEqual(names, ["Tymna", "Kinnan NBC (sample)"])          # mine first, samples after

    def test_names_never_collide_or_escape_the_folder(self):
        a = lib.save_text("Deck", sample_text(), self.lib, self.tmp)
        b = lib.save_text("Deck", sample_text(), self.lib, self.tmp)
        c = lib.save_text("..\\..\\evil/name?", sample_text(), self.lib, self.tmp)
        self.assertNotEqual(a.path, b.path)
        self.assertEqual(b.name, "Deck (2)")
        for e in (a, b, c):
            self.assertEqual(os.path.dirname(e.path), self.lib)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["my_decks", "sample_decks"])

    def test_remove_moves_the_file_and_never_deletes(self):
        e = lib.save_text("Tymna", other_deck_text(), self.lib, self.tmp)
        target = lib.remove(e, self.lib)
        self.assertFalse(os.path.exists(e.path))
        self.assertTrue(os.path.isfile(target))
        self.assertIn(lib.REMOVED, target)
        self.assertEqual([x.name for x in lib.list_decks(*self.dirs)], ["Kinnan NBC (sample)"])
        e2 = lib.save_text("Tymna", other_deck_text(), self.lib, self.tmp)      # same name again, then removed again
        self.assertNotEqual(lib.remove(e2, self.lib), target)

    def test_samples_cannot_be_removed(self):
        with self.assertRaises(DeckImportError):
            lib.remove(lib.list_decks(*self.dirs)[0], self.lib)

    def test_bad_text_is_explained(self):
        for text, part in (("", "Nothing pasted"), ("https://moxfield.com/decks/abc123", "TEXT list"),
                           ("this is not a deck at all", "which one is the commander")):
            commanders, deck, error = lib.analyse(text)
            self.assertTrue(error, text)
            self.assertIn(part, error)
            with self.assertRaises(DeckImportError):
                lib.save_text("x", text, self.lib, self.tmp)
        self.assertFalse(os.path.isdir(self.lib))                             # nothing was written

    def test_unreadable_file_is_listed_with_its_error(self):
        os.makedirs(self.lib)
        with open(os.path.join(self.lib, "broken.txt"), "wb") as f:
            f.write(b"\xff\xfe\x00garbage\x80")
        e = lib.find(lib.list_decks(*self.dirs), "my_decks/broken.txt")
        self.assertIsNotNone(e)
        self.assertFalse(e.ok)
        self.assertTrue(e.error)
        self.assertTrue(e.problems(None)[0][1])                               # blocking

    def test_a_byte_order_mark_is_tolerated(self):
        os.makedirs(self.lib)
        with open(os.path.join(self.lib, "notepad.txt"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + sample_text().encode("utf-8"))
        e = lib.find(lib.list_decks(*self.dirs), "my_decks/notepad.txt")
        self.assertTrue(e.ok)
        self.assertEqual(e.commanders, ["Kinnan, Bonder Prodigy"])

    def test_card_script_names(self):
        self.assertEqual(fc._slug("Nature's Rhythm"), "natures_rhythm")
        self.assertEqual(fc._slug("Nature\u2019s Rhythm"), "natures_rhythm")
        self.assertEqual(fc._slug("Kinnan, Bonder Prodigy"), "kinnan_bonder_prodigy")
        self.assertEqual(fc._slug("Fire // Ice"), "fire_ice")

    def test_unknown_cards_against_a_fake_card_folder(self):
        rt = os.path.join(self.tmp, "rt")
        for slug in ("forest", "island"):
            os.makedirs(os.path.join(rt, "res", "cardsfolder", slug[0]), exist_ok=True)
            open(os.path.join(rt, "res", "cardsfolder", slug[0], slug + ".txt"), "w").close()
        os.makedirs(os.path.join(rt, "res", "cardsfolder", "upcoming"))
        open(os.path.join(rt, "res", "cardsfolder", "upcoming", "new_card.txt"), "w").close()      # cards Forge loads from 'upcoming'
        self.assertEqual(fc.unknown_cards(["Forest", "Island", "New Card", "Frest", "Frest"], rt), ["Frest"])
        self.assertIsNone(fc.forge_knows("Forest", os.path.join(self.tmp, "nowhere")))       # no card folder: can't say
        self.assertEqual(fc.unknown_cards(["Forest"], os.path.join(self.tmp, "nowhere")), [])
        problems = lib.describe_problems(["Forest"], ["Island", "Frest"], None, rt)
        self.assertTrue(any("Frest" in text and not blocking for text, blocking in problems))


class FlavorNameTests(TempDecks):
    """Some Secret Lair / Universes Beyond printings are exported under the joke/crossover name printed on the
    card ('Chaos Theory') instead of its real rules name ('Chaos Warp'), which Forge's database has never heard
    of. Round 15, from Karl's PC: 'Forge does not know: Chaos Theory, Storm's Will, The Dead Marshes'."""

    def setUp(self):
        super().setUp()
        # a real disk cache the tests must never touch (or be affected by an earlier test run's leftovers)
        patcher = mock.patch("deck_importer._FLAVOR_CACHE_FILE", os.path.join(self.tmp, "flavor_names_cache.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_runtime(self):
        rt = os.path.join(self.tmp, "flavor_rt")
        folder = os.path.join(rt, "res", "cardsfolder", "c")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "chaos_warp.txt"), "w", encoding="utf-8") as f:
            f.write("Name:Chaos Warp\nManaCost:2 R\nTypes:Instant\n")
        return rt

    def fake_scryfall(self, mapping):
        """mapping: {queried name (any case): (real_name, flavor_name) or None for 'no card by that name'}."""
        def fake_get(url, headers=None, timeout=None):
            from urllib.parse import parse_qs, urlparse
            queried = parse_qs(urlparse(url).query)["exact"][0]
            hit = mapping.get(queried.lower())
            resp = mock.Mock()
            if hit is None:
                resp.status_code = 404
                return resp
            real, flavor = hit
            resp.status_code = 200
            resp.json.return_value = {"name": real, "flavor_name": flavor}
            return resp
        return fake_get

    def test_resolve_flavor_names_translates_a_secret_lair_flavor_name(self):
        rt = self.make_runtime()
        with mock.patch("deck_importer.requests.get", self.fake_scryfall({"chaos theory": ("Chaos Warp", "Chaos Theory")})):
            commanders, deck, renames = lib.resolve_flavor_names(["Kinnan, Bonder Prodigy"], ["Chaos Theory", "Forest"], rt)
        self.assertEqual(deck, ["Chaos Warp", "Forest"])
        self.assertEqual(renames, {"Chaos Theory": "Chaos Warp"})
        self.assertEqual(commanders, ["Kinnan, Bonder Prodigy"])          # untouched: Forge already knows it

    def test_a_name_scryfall_has_never_heard_of_is_left_alone(self):
        rt = self.make_runtime()
        with mock.patch("deck_importer.requests.get", self.fake_scryfall({})):        # 404 for anything asked
            commanders, deck, renames = lib.resolve_flavor_names([], ["Totally Fake Card"], rt)
        self.assertEqual(deck, ["Totally Fake Card"])
        self.assertEqual(renames, {})

    def test_offline_leaves_everything_as_it_was(self):
        rt = self.make_runtime()
        with mock.patch("deck_importer.requests.get", side_effect=requests.RequestException("no network in this test")):
            commanders, deck, renames = lib.resolve_flavor_names([], ["Chaos Theory"], rt)
        self.assertEqual(deck, ["Chaos Theory"])
        self.assertEqual(renames, {})

    def test_import_dialog_renames_on_paste_and_saves_the_real_name(self):
        rt = self.make_runtime()
        menu = mock.Mock(runtime=rt)
        deck_text = "Commander\n1 Kinnan, Bonder Prodigy\n1 Chaos Theory\n" + "\n".join(["1 Forest"] * 98)
        with mock.patch("deck_importer.requests.get", self.fake_scryfall({"chaos theory": ("Chaos Warp", "Chaos Theory")})):
            d = fmenu.ImportDialog(menu, deck_text)
        self.assertIn("Chaos Warp", d.deck)
        self.assertNotIn("Chaos Theory", d.deck)
        self.assertEqual(d.renames, {"Chaos Theory": "Chaos Warp"})
        self.assertTrue(any("Chaos Theory -> Chaos Warp" in text for text, _blocking in d.problems))
        self.assertFalse(any(b for _t, b in d.problems))                  # a rename is informational, never blocking


class MenuTests(TempDecks):
    def setUp(self):
        super().setUp()
        lib.save_text("Tymna", other_deck_text(), self.lib, self.tmp)

    def test_renders_at_several_sizes(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((4096, 2019), 1.75), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = self.idle_gui(size, scale)
                self.assertEqual(gui.screen.get_size(), size)
                self.assertTrue(gui.menu.rows)
                window = pygame.Rect(0, 0, *size)
                for rect, _ in gui.menu.btns:
                    self.assertTrue(window.contains(rect), (rect, size, scale))
                for rect, _ in gui.menu.rows:
                    self.assertTrue(rect.right <= size[0])

    def test_opens_with_no_game_and_no_back_button(self):
        gui = self.idle_gui()
        self.assertIsNotNone(gui.menu)
        self.assertFalse(gui.menu.has_game)
        names = [n for _, n in gui.menu.btns]
        self.assertIn("start", names)
        self.assertNotIn("back", names)
        key(gui, pygame.K_ESCAPE)
        self.assertIsNotNone(gui.menu)                       # nothing to go back to

    def test_the_first_click_picks_mine_then_the_ai_deck(self):
        gui = self.idle_gui()
        self.assertEqual(gui.menu.active, "mine")
        click(gui, self.row_point(gui, "Tymna"))
        self.assertEqual(gui.menu.mine.name, "Tymna")
        self.assertEqual(gui.menu.active, "opp")             # it moves on to the next question by itself
        self.assertEqual(gui.menu.opp.name, "Tymna")         # ... and the AI plays the same deck until told otherwise
        click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
        self.assertEqual(gui.menu.mine.name, "Tymna")
        self.assertEqual(gui.menu.opp.name, "Kinnan NBC (sample)")
        click(gui, self.button_point(gui, "same"))
        self.assertIsNone(gui.menu.opp_id)
        self.assertEqual(gui.menu.opp.name, "Tymna")

    def test_the_slots_can_be_clicked_to_switch_what_the_list_sets(self):
        gui = self.idle_gui()
        click(gui, gui.menu.slots["opp"].center)
        self.assertEqual(gui.menu.active, "opp")
        click(gui, self.row_point(gui, "Tymna"))
        self.assertEqual(gui.menu.opp.name, "Tymna")
        self.assertEqual(gui.menu.mine.name, "Tymna")        # first deck in the list (A-Z): mine is untouched
        click(gui, gui.menu.slots["mine"].center)
        self.assertEqual(gui.menu.active, "mine")

    def test_opponent_count_stepper(self):
        gui = self.idle_gui()
        self.assertEqual(gui.menu.count, 1)
        click(gui, self.button_point(gui, "plus"))
        click(gui, self.button_point(gui, "plus"))
        self.assertEqual(gui.menu.count, 3)
        with self.assertRaises(AssertionError):
            self.button_point(gui, "plus")                   # greyed out at the maximum
        for _ in range(5):
            key(gui, pygame.K_LEFT)
        self.assertEqual(gui.menu.count, 1)

    def test_start_swaps_the_session_and_closes_the_screen(self):
        gui = self.idle_gui()
        click(gui, self.button_point(gui, "plus"))
        click(gui, self.row_point(gui, "Tymna"))
        click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
        click(gui, self.button_point(gui, "start"))
        self.assertIsNone(gui.menu)
        self.assertEqual(len(self.launcher.calls), 1)
        mine, opps = self.launcher.calls[0]
        self.assertEqual(mine[0], ["Tymna the Weaver"])
        self.assertEqual(len(opps), 2)
        self.assertEqual(opps[0][0], ["Kinnan, Bonder Prodigy"])
        self.assertIn("Tymna the Weaver", gui.deck_names)
        self.assertEqual([lab for lab, _c, _d in gui.vs.seats], ["You", "AI 1", "AI 2"])     # Round AD2b: the VS screen
        self.assertEqual(gui.vs.seats[1][1], ["Kinnan, Bonder Prodigy"])
        gui.vs.skipped = True                                                     # (a click or Space skips it)
        frame(gui, 3)
        self.assertEqual(len(gui.session.opponents()), 2)
        self.assertTrue(gui.hits)

    def test_start_remembers_the_choice_in_settings(self):
        path = os.path.join(self.tmp, "settings.json")
        with open(path, "w") as f:
            json.dump({"text_scale": 1.5, "other": "kept"}, f)
        gui = self.idle_gui(settings=path)
        click(gui, self.row_point(gui, "Tymna"))
        click(gui, self.button_point(gui, "plus"))
        click(gui, self.button_point(gui, "start"))
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["decks"]["mine"], "my_decks/Tymna.txt")
        self.assertEqual(data["decks"]["count"], 2)
        self.assertEqual(data["other"], "kept")
        gui2 = self.idle_gui(settings=path)                    # next time the screen opens with those picked
        self.assertEqual(gui2.menu.mine.name, "Tymna")
        self.assertEqual(gui2.menu.count, 2)

    def test_start_failure_is_shown_and_the_screen_stays(self):
        gui = self.idle_gui()
        self.launcher.fail = "Java is missing"
        click(gui, self.button_point(gui, "start"))
        self.assertIsNotNone(gui.menu)
        self.assertIn("Java is missing", gui.menu.message[0])
        frame(gui, 2)
        self.launcher.fail = None                              # and it can be tried again
        click(gui, self.button_point(gui, "start"))
        self.assertIsNone(gui.menu)

    def test_a_deck_that_cannot_be_read_blocks_start(self):
        os.makedirs(self.lib, exist_ok=True)
        with open(os.path.join(self.lib, "AAA broken.txt"), "wb") as f:
            f.write(b"\xff\xfe\x00\x80")
        gui = self.idle_gui()
        self.assertEqual(gui.menu.entries[0].name, "AAA broken")
        click(gui, self.row_point(gui, "AAA broken"))
        click(gui, self.button_point(gui, "start"))
        self.assertIsNotNone(gui.menu)
        self.assertEqual(self.launcher.calls, [])
        self.assertIn("AAA broken", gui.menu.message[0])

    def test_new_game_button_opens_the_screen_over_a_running_game(self):
        gui = self.make_gui(menu=False)
        self.assertIsNone(gui.menu)
        click(gui, point_for_button(gui, "settings"))              # the cog's pop-up holds the New game button
        self.assertIsNotNone(gui.overlay)
        click(gui, next(r.center for r, n in gui.overlay.buttons if n == "newgame"))
        self.assertIsNotNone(gui.menu)
        self.assertIsNone(gui.overlay)
        self.assertTrue(gui.menu.has_game)
        self.assertIn("back", [n for _, n in gui.menu.btns])
        click(gui, self.button_point(gui, "back"))
        self.assertIsNone(gui.menu)
        key(gui, pygame.K_n, pygame.KMOD_CTRL)                 # Ctrl+N does the same
        self.assertIsNotNone(gui.menu)
        key(gui, pygame.K_ESCAPE)                              # Esc goes back to the game
        self.assertIsNone(gui.menu)

    def test_starting_over_a_running_game_asks_first(self):
        gui = self.make_gui()
        old = gui.session
        click(gui, self.button_point(gui, "start"))
        self.assertIsInstance(gui.modal, dlg.QuestionDialog)
        self.assertEqual(self.launcher.calls, [])
        self.assertIs(gui.session, old)
        click(gui, self.dialog_button(gui, "no"))               # Keep playing
        self.assertIsNone(gui.modal)
        self.assertIsNotNone(gui.menu)
        click(gui, self.button_point(gui, "start"))
        click(gui, self.dialog_button(gui, "yes"))              # End it and start
        self.assertIsNone(gui.menu)
        self.assertEqual(len(self.launcher.calls), 1)
        self.assertIsNot(gui.session, old)

    def test_a_new_game_starts_from_a_clean_table(self):
        gui = self.make_gui(menu=False)
        gui.toast, gui.shown_over, gui.log_scroll, gui.mulligans = ("old", (255, 0, 0), 1e12), True, 9, 2
        gui.pinned = 123
        gui.open_menu()
        frame(gui, 2)
        click(gui, self.button_point(gui, "start"))
        click(gui, self.dialog_button(gui, "yes"))
        self.assertIsNone(gui.toast)
        self.assertFalse(gui.shown_over)
        self.assertEqual((gui.log_scroll, gui.mulligans, gui.pinned), (0, 0, None))
        frame(gui, 2)

    def test_game_over_dialog_leads_to_the_deck_screen(self):
        st = copy.deepcopy(load_state("combat_damage"))
        st["gameOver"], st["winner"] = True, "Karl"
        gui = self.make_gui(state=st, menu=False)
        self.assertEqual(gui.end_screen.kind, "won")            # Round AD2b: VICTORY first
        gui.end_continue()
        frame(gui)
        self.assertIsInstance(gui.modal, dlg.GameOverDialog)
        click(gui, self.dialog_button(gui, "new"))
        self.assertIsNotNone(gui.menu)
        self.assertIsNone(gui.modal)
        self.assertTrue(gui.menu.has_game)                     # a finished game can still be looked at
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.menu)

    def test_requests_from_the_old_game_do_not_pop_up_over_the_screen(self):
        gui = self.make_gui()
        gui.session.handle({"t": "request", "id": 9, "kind": "confirm", "title": "Sure?", "message": "?"})
        frame(gui, 3)
        self.assertIsNone(gui.modal)
        self.assertIsNotNone(gui.menu)

    def test_keyboard_moves_through_the_list(self):
        gui = self.idle_gui()
        key(gui, pygame.K_DOWN)
        self.assertEqual(gui.menu.mine.name, "Kinnan NBC (sample)")
        self.assertEqual(gui.menu.active, "mine")               # the arrows change the side you are on
        key(gui, pygame.K_UP)
        self.assertEqual(gui.menu.mine.name, "Tymna")
        key(gui, pygame.K_TAB)
        key(gui, pygame.K_DOWN)
        self.assertEqual(gui.menu.opp.name, "Kinnan NBC (sample)")
        self.assertEqual(gui.menu.mine.name, "Tymna")
        key(gui, pygame.K_RETURN)
        self.assertIsNone(gui.menu)

    def test_remove_asks_then_moves_the_file(self):
        gui = self.idle_gui()
        click(gui, self.row_point(gui, "Tymna"))
        click(gui, self.button_point(gui, "remove"))
        self.assertIsInstance(gui.modal, dlg.QuestionDialog)
        click(gui, self.dialog_button(gui, "no"))
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "Tymna.txt")))
        click(gui, self.button_point(gui, "remove"))
        click(gui, self.dialog_button(gui, "yes"))
        self.assertFalse(os.path.exists(os.path.join(self.lib, "Tymna.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.lib, lib.REMOVED, "Tymna.txt")))
        self.assertEqual([e.name for e in gui.menu.entries], ["Kinnan NBC (sample)"])
        self.assertIsNotNone(gui.menu.mine)                    # falls back to a deck that still exists
        frame(gui, 2)

    def test_samples_have_no_active_remove_button(self):
        gui = self.idle_gui()
        click(gui, self.row_point(gui, "Kinnan NBC (sample)"))
        with self.assertRaises(AssertionError):
            self.button_point(gui, "remove")

    def test_many_decks_scroll(self):
        for i in range(25):
            lib.save_text(f"Deck {i:02d}", other_deck_text(), self.lib, self.tmp)
        gui = self.idle_gui((1024, 640))
        self.assertGreater(gui.menu.content_h, gui.menu.list_rect.h)
        move(gui, gui.menu.list_rect.center)
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-5))
        frame(gui)
        self.assertGreater(gui.menu.scroll, 0)
        for _ in range(60):
            gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-5))
        frame(gui)
        self.assertLessEqual(gui.menu.scroll, gui.menu.content_h)
        last = gui.menu.rows[-1][1]
        click(gui, gui.menu.rows[-1][0].center)                # the bottom row can be reached and picked
        self.assertEqual(gui.menu.mine_id, last.id)


def point_for_button(gui, name):
    for rect, kind, data in reversed(gui.hits):
        if kind == "button" and data.get("name") == name:
            return rect.center
    raise AssertionError(f"no {name} button; hits: {sorted({k for _, k, _ in gui.hits})}")


class ImportTests(TempDecks):
    def open_import(self, gui, text=""):
        gui.menu.open_import(gui, text)
        frame(gui, 2)
        return gui.modal

    def test_paste_button_reads_the_clipboard(self):
        gui = self.idle_gui()
        gui.read_clipboard = lambda: sample_text()
        d = self.open_import(gui)
        self.assertIsInstance(d, fmenu.ImportDialog)
        self.assertFalse(d.can_save)
        click(gui, self.dialog_button(gui, "paste"))
        self.assertTrue(d.can_save)
        self.assertEqual(d.name, "Kinnan, Bonder Prodigy")

    def test_ctrl_v_on_the_deck_screen_opens_the_window_with_the_text(self):
        gui = self.idle_gui()
        gui.read_clipboard = lambda: other_deck_text()
        key(gui, pygame.K_v, pygame.KMOD_CTRL)
        self.assertIsInstance(gui.modal, fmenu.ImportDialog)
        self.assertEqual(gui.modal.commanders, ["Tymna the Weaver"])
        self.assertIsNone(gui.modal.error)

    def test_save_adds_and_selects_the_deck(self):
        gui = self.idle_gui()
        d = self.open_import(gui, other_deck_text())
        click(gui, self.dialog_button(gui, "save"))
        self.assertTrue(d.done)
        frame(gui, 2)
        self.assertIsNone(gui.modal)
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "Tymna the Weaver.txt")))
        self.assertEqual(gui.menu.mine.name, "Tymna the Weaver")
        self.assertEqual(gui.menu.active, "opp")
        self.assertIn("Saved", gui.menu.message[0])
        self.assertEqual(len(gui.menu.entries), 2)

    def test_the_name_can_be_typed(self):
        gui = self.idle_gui()
        d = self.open_import(gui, other_deck_text())
        for _ in range(len(d.name)):
            key(gui, pygame.K_BACKSPACE)
        for ch in "My Tymna":
            key(gui, ord(ch.lower()), 0, ch)
        self.assertEqual(d.name, "My Tymna")
        key(gui, pygame.K_RETURN)                              # Enter saves
        self.assertTrue(os.path.isfile(os.path.join(self.lib, "My Tymna.txt")))
        self.assertEqual(gui.menu.mine.name, "My Tymna")

    def test_an_empty_name_cannot_be_saved(self):
        gui = self.idle_gui()
        d = self.open_import(gui, other_deck_text())
        for _ in range(len(d.name)):
            key(gui, pygame.K_BACKSPACE)
        self.assertFalse(d.can_save)
        with self.assertRaises(AssertionError):
            self.dialog_button(gui, "save")
        key(gui, pygame.K_RETURN)
        self.assertFalse(os.path.isdir(self.lib))

    def test_bad_text_is_explained_and_cannot_be_saved(self):
        gui = self.idle_gui()
        for text, part in (("https://www.moxfield.com/decks/abc", "TEXT list"), ("hello there", "commander")):
            d = self.open_import(gui, text)
            self.assertFalse(d.can_save)
            self.assertTrue(d.error)
            self.assertIn(part, d.error)
            key(gui, pygame.K_RETURN)
            frame(gui)
            self.assertFalse(d.done)
            key(gui, pygame.K_ESCAPE)
            frame(gui)
            self.assertIsNone(gui.modal)
        self.assertFalse(os.path.isdir(self.lib))

    def test_an_unreadable_clipboard_says_so(self):
        gui = self.idle_gui()
        gui.read_clipboard = lambda: ""
        d = self.open_import(gui)
        click(gui, self.dialog_button(gui, "paste"))
        self.assertIn("clipboard", d.flash)
        self.assertIn("drag", d.flash)
        frame(gui)

    def test_dropping_a_deck_file_on_the_window(self):
        path = os.path.join(self.tmp, "dropped.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(other_deck_text())
        gui = self.idle_gui()
        gui.handle_event(pygame.event.Event(pygame.DROPFILE, file=path))         # on the deck screen: opens the window
        frame(gui)
        self.assertIsInstance(gui.modal, fmenu.ImportDialog)
        self.assertEqual(gui.modal.commanders, ["Tymna the Weaver"])
        gui.modal.set_text("")
        gui.handle_event(pygame.event.Event(pygame.DROPFILE, file=path))         # and inside the window: fills it
        self.assertTrue(gui.modal.can_save)
        gui.handle_event(pygame.event.Event(pygame.DROPFILE, file=os.path.join(self.tmp, "nope.txt")))
        frame(gui)
        self.assertIn("Could not read", gui.modal.flash)

    def test_cancel_saves_nothing(self):
        gui = self.idle_gui()
        d = self.open_import(gui, other_deck_text())
        click(gui, self.dialog_button(gui, "cancel"))
        self.assertTrue(d.done)
        self.assertFalse(os.path.isdir(self.lib))

    def test_a_long_list_and_big_text_render(self):
        for size, scale in (((1024, 640), 1.0), ((4096, 2019), 1.75), ((1100, 700), 2.0)):
            gui = self.idle_gui(size, scale)
            d = self.open_import(gui, sample_text())
            frame(gui, 2)
            for _ in range(3):
                gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3))
            frame(gui)
            window = pygame.Rect(0, 0, *size)
            for rect, _ in d.buttons:
                self.assertTrue(window.contains(rect), (size, scale, rect))


class LauncherTests(TempDecks):
    def test_launcher_writes_deck_files_and_starts_a_session(self):
        started = []
        real = ft.fc.ForgeSession
        ft.DECK_DIR, old_dir = self.tmp, ft.DECK_DIR

        class Spy:
            def __init__(self, mine, opps, **kw):
                started.append((mine, opps, kw))

            def start(self):
                return self

        ft.fc.ForgeSession = Spy
        try:
            launcher = ft.Launcher(None, "Karl", 7, None)
            s = launcher.start((["Tymna the Weaver"], ["Forest", "Forest"]), [(["Kinnan, Bonder Prodigy"], ["Island"])] * 2)
        finally:
            ft.fc.ForgeSession = real
            ft.DECK_DIR = old_dir
        self.assertIsInstance(s, Spy)
        mine, opps, kw = started[0]
        self.assertTrue(os.path.isfile(mine))
        self.assertEqual(len(opps), 2)
        self.assertEqual(kw["seed"], 7)
        with open(mine) as f:
            text = f.read()
        self.assertIn("[Commander]", text)
        self.assertIn("2 Forest", text)



class BundledDeckTests(unittest.TestCase):
    """The decks that ship in sample_decks/ (the alpha's starter decks): every one loads, is a legal-size Commander deck
    and has a proper name with its archetype - a new file without a SAMPLE_NAMES entry would show a made-up title."""

    def test_every_bundled_deck_loads_with_a_name(self):
        entries = lib.list_decks(os.path.join(HERE, "no_such_folder"), lib.SAMPLE_DIR, HERE)
        self.assertEqual(sorted(e.name for e in entries), sorted(lib.SAMPLE_NAMES.values()))
        for e in entries:
            with self.subTest(deck=e.name):
                self.assertTrue(e.ok and e.builtin, e.error)
                self.assertEqual(e.total, 100)
                self.assertEqual([p for p in e.problems(os.path.join(HERE, "no_forge_here"))
                                  if not legality.is_unchecked_line(p[0])], [])      # Round BAN1: no card data in a test run

    def test_the_five_archetypes_are_named(self):
        names = " ".join(lib.SAMPLE_NAMES.values())
        for archetype in ("Typal", "Tokens / Go Wide", "Aristocrats", "Voltron", "Spellslinger"):
            self.assertIn(f"({archetype})", names)

    def test_no_bundled_card_is_one_forge_says_its_ai_cannot_play(self):
        """Forge's card scripts carry 'AI:RemoveDeck:All' when Forge's developers found that the AI misplays the card.
        The bundled decks are there for the AI to pilot, so none of their cards may carry it (checked 2026-09-25)."""
        folder = os.path.join(fc.DEFAULT_RUNTIME, "res", "cardsfolder")
        if not os.path.isdir(folder):
            self.skipTest("Forge's card scripts are not installed (python setup_forge.py)")
        flagged = []
        for e in lib.list_decks(os.path.join(HERE, "no_such_folder"), lib.SAMPLE_DIR, HERE):
            if "kinnan" in e.id:
                continue                                    # Karl's own cEDH list, not an AI starter deck
            for name in set(e.commanders + e.deck):
                slug = fc._slug(" ".join(f.strip() for f in name.split("/")))
                for sub in (slug[:1], "upcoming", "rebalanced"):
                    path = os.path.join(folder, sub, slug + ".txt")
                    if os.path.isfile(path):
                        with open(path, encoding="utf-8") as f:
                            if any(line.strip() == "AI:RemoveDeck:All" for line in f):
                                flagged.append(f"{e.name}: {name}")
                        break
        self.assertEqual(flagged, [])

if __name__ == "__main__":
    unittest.main()
