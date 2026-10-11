# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CON1: 60-card Standard beside Commander and Brawl (formats.py, the deck files, legality, the engine, the deck screen, the
table without a command zone, the soak). The first step of the RPG plan (claude/RPG_PLAN_2026-10-09.md): the RPG's duels are
Constructed games, and the main game gets the 60-card format Karl asked for on 2 Oct.

The rules (Comprehensive Rules, effective 25 Sep 2026, read from media.wizards.com on 9 Oct 2026):
  100.2a  a constructed deck has at least 60 cards; at most four cards with the same English name, basic lands excepted
  100.4a  a sideboard of at most fifteen cards; the four-card limit counts the deck and the sideboard together
  103.4   each player starts with 20 life
  103.5c  in a multiplayer game (and in any Brawl game) the first mulligan is free
"""
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deck_importer as di
import deck_library as lib
import forge_client as fc
import formats
import journal as gjournal
import legality as lg
import reporting

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(BASE, "sample_decks")
STANDARD_SAMPLES = ("standard_red_aggro", "standard_green_dinos")
JAVA = os.path.join(BASE, "java_bridge", "src")


def card(name, type_line="Creature", oracle="", standard="legal", **extra):
    c = {"name": name, "type_line": type_line, "color_identity": [], "oracle_text": oracle,
         "legalities": {"commander": "legal", "brawl": "legal", "standard": standard}}
    c.update(extra)
    return c


class Store:
    def __init__(self, cards):
        self.cards = {c["name"]: c for c in cards}

    def peek_card(self, name):
        return self.cards.get(name)


CARDS = [card("Lightning Strike", "Instant"), card("Shock", "Instant"), card("Grizzly Bears"),
         card("Mountain", "Basic Land — Mountain"), card("Forest", "Basic Land — Forest"),
         card("Relentless Rats", oracle="A deck can have any number of cards named Relentless Rats."),
         card("Seven Dwarves", oracle="A deck can have up to seven cards named Seven Dwarves."),
         card("Black Lotus", "Artifact", standard="not_legal"), card("Old Thing", standard="banned")]


def store():
    return Store(CARDS)


def sixty(*extra):
    """A legal 60-card Standard main deck: 4 Lightning Strike, 4 Shock, 4 Grizzly Bears, 48 Mountain - then `extra`."""
    return ["Lightning Strike"] * 4 + ["Shock"] * 4 + ["Grizzly Bears"] * 4 + ["Mountain"] * 48 + list(extra)


def no_ban():
    return mock.patch.dict(lg._BANNED, {"standard": lg.BanList(frozenset({"old thing"}), "2026-10-09T00:00:00+00:00", "snapshot")})


# ---- formats.py ----------------------------------------------------------------------------------------------------------------
class FormatTableTests(unittest.TestCase):
    def test_standard_is_60_card_constructed(self):
        s = formats.get("standard")
        self.assertEqual((s.key, s.name, s.deck_size), ("standard", "Standard", 60))
        self.assertEqual((formats.starting_life("standard", 2), formats.starting_life("standard", 4)), (20, 20))   # 103.4
        self.assertFalse(s.commander)
        self.assertFalse(s.commander_damage)
        self.assertFalse(s.exact)                              # 100.2a: at least 60
        self.assertEqual((s.copies, s.sideboard), (4, 15))     # 100.2a, 100.4a
        self.assertFalse(s.online)                             # NetHost isn't touched: online stays Commander
        self.assertEqual(formats.engine("standard"), "constructed")

    def test_commander_and_brawl_are_what_they_were(self):
        for key, size in (("commander", 100), ("brawl", 100)):
            f = formats.get(key)
            self.assertTrue(f.commander, key)
            self.assertTrue(f.exact, key)
            self.assertEqual((f.deck_size, f.copies, f.sideboard), (size, 1, 0), key)
            self.assertEqual(formats.engine(key), key)
        self.assertTrue(formats.has_commander(None))
        self.assertFalse(formats.has_commander("standard"))

    def test_the_order_and_the_default(self):
        self.assertEqual(formats.ORDER, ("commander", "brawl", "standard"))
        self.assertEqual(formats.normal("STANDARD"), "standard")
        self.assertEqual(formats.normal("vintage"), "commander")            # unknown is still the default
        self.assertEqual(formats.life_note("standard", 2), "20 life each.")
        self.assertIn("60", formats.BLURB["standard"])

    def test_the_lines_that_name_it(self):
        self.assertEqual(formats.text_line("standard"), "# format: standard")
        self.assertEqual(formats.from_text("# format: standard\n4 Shock"), "standard")
        self.assertEqual(formats.dck_lines("standard"), ["Deck Type=Constructed"])     # Forge's own DeckFileHeader value
        self.assertEqual(formats.from_dck("[metadata]\nName=P\nDeck Type=Constructed\n[Main]\n4 Shock\n"), "standard")
        self.assertEqual(formats.dck_lines("commander"), [])                          # a Commander .dck is unchanged


# ---- reading a deck ------------------------------------------------------------------------------------------------------------
class ImportTests(unittest.TestCase):
    def test_a_standard_deck_never_has_a_commander(self):
        # A last line set apart by a blank line, and a first line of quantity 1 in a 100-card list, both read as a commander in
        # Commander. In a Standard deck they are just cards.
        text = "# format: standard\n" + "\n".join(["1 Shock"] * 59) + "\n\n1 Lightning Strike\n"
        cmd, deck = di.import_from_text(text)
        self.assertEqual((cmd, len(deck)), ([], 60))
        self.assertIn("Lightning Strike", deck)
        cmd, deck = di.import_from_text("Commander\n1 Grizzly Bears\n\nDeck\n4 Shock\n", fmt="standard")
        self.assertEqual((cmd, deck), ([], ["Grizzly Bears", "Shock", "Shock", "Shock", "Shock"]))
        # A line tagged [Commander] (Archidekt's categories) keeps its quantity in Standard: 4 Shock, not 1
        cmd, deck = di.import_from_text("4 Shock [Commander]\n56 Mountain\n", fmt="standard")
        self.assertEqual((cmd, deck.count("Shock"), len(deck)), ([], 4, 60))

    def test_the_sideboard_is_read_when_asked(self):
        text = "Deck\n4 Shock\n56 Mountain\n\nSideboard\n2 Lightning Strike\n1 Grizzly Bears\n"
        side = []
        cmd, deck = di.import_from_text(text, sideboard=side, fmt="standard")
        self.assertEqual(len(deck), 60)
        self.assertEqual(side, ["Lightning Strike", "Lightning Strike", "Grizzly Bears"])
        moxfield = "4 Shock\n56 Mountain\n\nSIDEBOARD:\n3 Grizzly Bears\n"
        side = []
        di.import_from_text(moxfield, sideboard=side, fmt="standard")
        self.assertEqual(side, ["Grizzly Bears"] * 3)

    def test_a_commander_deck_reads_exactly_as_before(self):
        with open(os.path.join(SAMPLES, "stompy_goreclaw.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(di.import_from_text(text), di.import_from_text(text, fmt="commander"))
        cmd, _deck = di.import_from_text(text)
        self.assertEqual(cmd, ["Goreclaw, Terror of Qal Sisma"])


# ---- legality --------------------------------------------------------------------------------------------------------------------
class LegalityTests(unittest.TestCase):
    def check(self, deck, side=None):
        with no_ban():
            return lg.check([], deck, store(), fmt="standard", sideboard=side)

    def kinds(self, v):
        return {k: cards for k, _t, cards in v.issues}

    def test_a_legal_deck(self):
        v = self.check(sixty())
        self.assertEqual((v.status, v.issues), ("legal", []))
        self.assertEqual(self.check(sixty("Shock") [:-1] + ["Grizzly Bears"]).status, "not_legal")   # a fifth Bears

    def test_at_least_sixty(self):
        v = self.check(sixty()[:-1])
        self.assertIn("size", self.kinds(v))
        self.assertIn("This deck has 59 cards; a Standard deck has at least 60.", [t for _k, t, _c in v.issues])
        self.assertEqual(self.check(sixty("Mountain", "Mountain")).status, "legal")          # 62 is fine

    def test_four_copies_basics_and_the_cards_that_say_otherwise(self):
        v = self.check(sixty()[:-1] + ["Shock"])
        self.assertEqual(self.kinds(v).get("copies"), ["Shock ×5"])
        self.assertEqual(self.check(sixty("Relentless Rats", *(["Relentless Rats"] * 9))).status, "legal")
        self.assertEqual(self.check(sixty(*(["Seven Dwarves"] * 7))).status, "legal")
        self.assertIn("copies", self.kinds(self.check(sixty(*(["Seven Dwarves"] * 8)))))

    def test_the_sideboard_counts_toward_four_and_holds_fifteen(self):
        v = self.check(sixty(), ["Shock"])                       # 4 in the deck + 1 in the sideboard
        self.assertEqual(self.kinds(v).get("copies"), ["Shock ×5"])
        v = self.check(sixty(), ["Mountain"] * 16)
        self.assertIn("sideboard", self.kinds(v))
        self.assertEqual(self.check(sixty(), ["Mountain"] * 15).status, "legal")

    def test_banned_and_not_in_standard(self):
        v = self.check(sixty("Black Lotus", "Old Thing"))
        k = self.kinds(v)
        self.assertEqual(k.get("not_legal"), ["Black Lotus"])
        self.assertEqual(k.get("banned"), ["Old Thing"])
        lines = [t for t, _b in lg.problem_lines(v, "standard")]
        self.assertIn("Not legal in Standard: Black Lotus.", lines)
        self.assertTrue(any(t.startswith("Banned in Standard (list as of 9 Oct 2026): Old Thing") for t in lines), lines)

    def test_nothing_blocks_a_standard_deck(self):
        v = self.check(sixty()[:-10] + ["Shock"] * 3)
        self.assertEqual(v.status, "not_legal")
        self.assertFalse(lg.blocks_start(v))
        self.assertTrue(all(not b for _t, b in lg.problem_lines(v, "standard")))
        lines = [t for t, _b in lg.problem_lines(v, "standard")]
        self.assertIn("More than four copies: Shock ×7.", lines)
        self.assertNotIn("commander", " ".join(lines).lower())

    def test_unchecked_without_card_data(self):
        with no_ban():
            v = lg.check([], sixty(), Store([]), fmt="standard")
        self.assertEqual(v.status, "unchecked")

    def test_the_bundled_standard_list(self):
        self.assertIn("standard", lg.FORMATS)
        with open(os.path.join(BASE, "banned_standard.snapshot.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["query"], "banned:standard")
        self.assertTrue(data["fetched_at"].startswith("2026-10"))
        self.assertIsInstance(data["cards"], list)


# ---- the deck library ------------------------------------------------------------------------------------------------------------
class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def entry(self, text, name="t"):
        path = os.path.join(self.tmp, name + ".txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return lib.DeckEntry(path, name, False, base_dir=self.tmp).load()

    def test_a_standard_deck_file(self):
        e = self.entry("# format: standard\nDeck\n3 Shock\n4 Grizzly Bears\n53 Mountain\n\nSideboard\n2 Lightning Strike\n")
        self.assertEqual(e.format, "standard")
        self.assertEqual((e.commanders, len(e.deck), e.sideboard), ([], 60, ["Lightning Strike"] * 2))
        self.assertTrue(e.ok)
        self.assertEqual(e.total, 60)
        self.assertEqual(e.face(), "Grizzly Bears")                 # the most copies of a card that isn't a basic land
        texts = [t for t, _b in e.problems()]
        self.assertFalse(any("commander" in t.lower() for t in texts), texts)
        self.assertNotIn("no commander", e.summary().lower())

    def test_a_commander_deck_still_needs_its_commander(self):
        e = self.entry("4 Shock\n56 Mountain\n")
        self.assertEqual(e.format, "commander")
        self.assertFalse(e.ok)
        self.assertTrue(any(t.startswith("No commander found") and b for t, b in e.problems()))
        e = self.entry("Commander\n1 Grizzly Bears\n\nDeck\n4 Shock\n")
        self.assertEqual(e.face(), "Grizzly Bears")                 # a commander deck's face is its commander

    def test_pasting_a_standard_list(self):
        cmd, deck, err = lib.analyse("4 Shock\n56 Mountain\n", fmt="standard")
        self.assertEqual((cmd, len(deck), err), ([], 60, None))
        self.assertIsNotNone(lib.analyse("4 Shock\n56 Mountain\n")[2])          # a Commander paste still wants its commander
        lib.save_text("Burn", "4 Shock\n56 Mountain\n", library_dir=self.tmp, base_dir=self.tmp, fmt="standard")
        with open(os.path.join(self.tmp, "Burn.txt"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("# format: standard\n"))

    def test_a_name_from_an_arena_export(self):
        arena = "About\nName Boros Burn\n\nDeck\n4 Shock\n56 Mountain\n"
        self.assertEqual(lib.suggest_name([], text=arena), "Boros Burn")
        self.assertEqual(lib.suggest_name(["Krenko, Tin Street Kingpin"]), "Krenko, Tin Street Kingpin")
        self.assertEqual(lib.suggest_name([], text="4 Shock\n"), "")

    def test_the_problem_lines_without_card_data(self):
        lines = lib.describe_problems([], ["Shock"] * 30, None, None, "standard")
        self.assertIn(("This deck has 30 cards; a Standard deck has at least 60.", False), lines)


# ---- the engine ------------------------------------------------------------------------------------------------------------------
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_standard_dck_has_no_commander_section(self):
        p = fc.write_deck_file(os.path.join(self.tmp, "s.dck"), [], ["Shock"] * 4 + ["Mountain"] * 56, "P", fmt="standard")
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "[metadata]\nName=P\nDeck Type=Constructed\n[Main]\n4 Shock\n56 Mountain\n")
        self.assertEqual(formats.from_dck_file(p), "standard")
        c = fc.write_deck_file(os.path.join(self.tmp, "c.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 2, "P")
        with open(c, encoding="utf-8") as f:
            self.assertEqual(f.read(), "[metadata]\nName=P\n[Commander]\n1 Kinnan, Bonder Prodigy\n[Main]\n2 Forest\n")

    def test_the_session_starts_a_constructed_engine(self):
        p = fc.write_deck_file(os.path.join(self.tmp, "s.dck"), [], ["Mountain"] * 60, "P", fmt="standard")
        s = fc.ForgeSession(p, [p], seed=1)
        self.assertEqual(s.fmt, "standard")
        args = s._bridge_args()
        self.assertEqual(args[args.index("--format") + 1], "constructed")
        s.handle({"t": "ready", "protocol": 2, "format": "constructed", "startingLife": 20})
        self.assertIsNone(s.fatal)
        s = fc.ForgeSession(p, [p], seed=1)
        s.handle({"t": "ready", "protocol": 2, "format": "commander", "startingLife": 40})   # a bridge that ignored --format
        self.assertIn("Commander game instead of Standard", s.fatal or "")

    def test_the_bridge_source(self):
        with open(os.path.join(JAVA, "forge", "bridge", "Main.java"), encoding="utf-8") as f:
            main = f.read()
        self.assertIn('f.equals("constructed")', main)
        self.assertIn("GameType.Constructed", main)
        self.assertIn("new RegisteredPlayer(deck)", main)                     # Forge's own Constructed lobby: 20 life
        self.assertNotRegex(main, r"static\s+(final\s+)?GameType\s+\w+\s*=")  # FMT1's start-up trap
        with open(os.path.join(JAVA, "forge", "bridge", "BridgeGui.java"), encoding="utf-8") as f:
            gui = f.read()
        self.assertIn("Main.constructed()", gui)                               # the start check knows a game has no variant
        with open(os.path.join(JAVA, "forge", "game", "mulligan", "MulliganService.java"), encoding="utf-8") as f:
            mull = f.read()
        self.assertIn("game.getRules().hasCommander() || game.getPlayers().size() > 2", mull)   # 103.5c, and Commander's house rule

    def test_the_journal_and_the_report_name_a_standard_game(self):
        j = gjournal.GameJournal(self.tmp)
        j.start(1, "Karl", {"player.dck": "x"}, "code", 100.0, fmt="standard")
        j.close()
        self.assertEqual(gjournal.read(j.path)[0]["format"], "standard")
        text = reporting.report_text({"name": "K", "seed": 1, "format": "standard"}, None, [], "now")
        self.assertIn("Format:   Standard\n", text)


# ---- the deck screen and the table ------------------------------------------------------------------------------------------
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame  # noqa: E402
import flow_screens as flow  # noqa: E402
import forge_table as ft  # noqa: E402
from tests import test_deck_screen as tds  # noqa: E402
from tests.test_forge_table import click, frame  # noqa: E402
from tests.test_fmt1 import FormatLauncher, ft_online_problem  # noqa: E402


class DeckScreenTests(tds.TempDecks):
    def setUp(self):
        super().setUp()
        for stem in STANDARD_SAMPLES + ("brawl_krenko",):
            shutil.copy(os.path.join(SAMPLES, stem + ".txt"), self.samples)

    def gui(self, size=(1360, 840), scale=None, settings=None):
        gui = self.idle_gui(size, scale, settings)
        self.launcher = gui.launcher = FormatLauncher()
        return gui

    def names(self, gui):
        return sorted(e.name for e in gui.menu.visible())

    def test_the_standard_button_shows_standard_decks(self):
        gui = self.gui()
        click(gui, self.button_point(gui, "fmt_standard"))
        self.assertEqual(gui.menu.fmt, "standard")
        self.assertEqual(self.names(gui), sorted(lib.SAMPLE_NAMES[s] for s in STANDARD_SAMPLES))
        self.assertIn("Standard", gui.menu.message[0])
        frame(gui, 2)

    def test_start_plays_standard_and_random_picks_standard(self):
        gui = self.gui()
        click(gui, self.button_point(gui, "fmt_standard"))
        click(gui, self.button_point(gui, "plus"))
        click(gui, self.button_point(gui, "random2"))
        gui.menu.rng.seed(3)
        click(gui, self.button_point(gui, "start"))
        self.assertIsNone(gui.menu)
        self.assertEqual(self.launcher.formats, ["standard"])
        mine, opps = self.launcher.calls[0]
        self.assertEqual(mine[0], [])                                   # no commander
        self.assertEqual(len(mine[1]), 60)
        self.assertTrue(all(o[0] == [] and len(o[1]) == 60 for o in opps), opps)
        gui.restart_game()
        self.assertEqual(self.launcher.formats, ["standard", "standard"])

    def test_online_play_stays_commander(self):
        gui = self.gui()
        gui.open_host = mock.Mock(return_value=None)
        click(gui, self.button_point(gui, "fmt_standard"))
        gui.menu.press(gui, "host_online")
        gui.open_host.assert_not_called()
        self.assertIn("Online games are Commander only for now", gui.menu.message[0])
        entries = {e.name: e for e in lib.list_decks(*self.dirs)}
        self.assertIn("Commander only", ft_online_problem(gui, entries[lib.SAMPLE_NAMES["standard_red_aggro"]]))

    def test_the_import_window_saves_a_standard_deck(self):
        gui = self.gui()
        gui.menu.open_import(gui, "About\nName Mono Red\n\nDeck\n4 Shock\n4 Lightning Strike\n52 Mountain\n")
        d = gui.modal
        frame(gui, 2)
        click(gui, self.dialog_button(gui, "fmt_standard"))
        self.assertEqual(d.fmt, "standard")
        self.assertFalse(d.error)
        self.assertEqual(d.name, "Mono Red")
        self.assertTrue(d.can_save)
        click(gui, self.dialog_button(gui, "save"))
        frame(gui, 2)
        with open(os.path.join(self.lib, "Mono Red.txt"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("# format: standard\n"))
        self.assertEqual(gui.menu.fmt, "standard")
        self.assertEqual(gui.menu.mine.name, "Mono Red")

    def test_the_format_buttons_fit(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1100, 700), 2.0), ((1920, 1080), 2.0), ((900, 600), 1.0)):
            with self.subTest(size=size, scale=scale):
                gui = self.gui(size, scale)
                click(gui, self.button_point(gui, "fmt_standard"))
                frame(gui, 2)
                window = pygame.Rect(0, 0, *size)
                rects = gui.menu.format_rects
                self.assertEqual(set(rects), {"fmt_commander", "fmt_brawl", "fmt_standard"})
                for r in rects.values():
                    self.assertTrue(window.contains(r), (r, size, scale))
                rs = sorted(rects.values(), key=lambda r: r.x)
                self.assertFalse(any(a.colliderect(b) for a, b in zip(rs, rs[1:])), rs)
                for name in ("main_menu", "back"):
                    other = getattr(gui.menu, name + "_rect", None)
                    if other:
                        self.assertFalse(any(other.colliderect(r) for r in rs), (name, size, scale))
                gui.menu.open_import(gui, "")
                frame(gui, 2)
                d = gui.modal
                taken = [r for r, n in d.buttons if not n.startswith("fmt_")]
                self.assertEqual(set(d.format_rects), {"fmt_commander", "fmt_brawl", "fmt_standard"})
                for r in d.format_rects.values():
                    self.assertTrue(window.contains(r), (r, size, scale))
                    self.assertFalse(any(r.colliderect(t) for t in taken), (r, size, scale))
                d.close() if hasattr(d, "close") else None
                gui.modal = None

    def test_the_command_line_deck_plays_its_own_format(self):
        """python forge_table.py <deck file>: the file's "# format:" line decides the game's format (a Standard file used to
        start a Commander game there)."""
        started = []

        class Launcher:
            def __init__(self, *a, **k):
                self.runtime = None

            def start(self, mine, opps, fmt=None):
                started.append((fmt, mine, opps))
                return SimpleNamespace()
        table = mock.Mock()
        deck = os.path.join(SAMPLES, "standard_red_aggro.txt")
        with mock.patch.object(ft, "Launcher", Launcher), mock.patch.object(ft, "ForgeTable", return_value=table), \
                mock.patch.object(ft.paths, "ensure_dirs"), mock.patch.object(ft.paths, "migrate_from_program_dir", return_value=[]), \
                mock.patch.object(ft.crashlog, "install"), mock.patch.object(ft.last_session, "begin", return_value=None), \
                mock.patch.object(ft.last_session, "end"), mock.patch.object(fc, "sync_bridge"), \
                mock.patch.object(fc, "runtime_problem", return_value=None), mock.patch.object(fc, "warm_card_index"), \
                mock.patch("card_data.CardDataStore"), mock.patch("builtins.print"):
            ft.main(["forge_table.py", deck])
            ft.main(["forge_table.py", os.path.join(SAMPLES, "stompy_goreclaw.txt")])
        self.assertEqual([f for f, _m, _o in started], ["standard", "commander"])
        self.assertEqual((started[0][1][0], len(started[0][1][1])), ([], 60))
        self.assertEqual(table.run.call_count, 2)

    def test_the_loading_screen_names_the_format(self):
        gui = self.gui()
        gui.session = SimpleNamespace(fmt="standard", opponent_paths=["a"])
        self.assertEqual(gui.format_line(), "Standard - 20 life each.")


class TableTests(unittest.TestCase):
    """A Standard game has no command zone: no frame for mine or the opponents', and its room goes to the board."""

    def setUp(self):
        from tests.forge_fake import FakeSession, StubStore, load_state
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        state = load_state("main1_start")
        for p in state["players"]:
            p["commanders"] = []
            p["zones"]["command"] = []
        self.session = FakeSession(state)
        self.session.fmt = "standard"
        self.gui = ft.ForgeTable(self.session, StubStore(), window_size=(1360, 840), saves_dir=os.path.join(self.tmp, "s"))

    def test_no_command_zone(self):
        gui = self.gui
        frame(gui, 2)
        self.assertEqual(gui.L.cmd_rect.w, 0)
        self.assertFalse(gui.has_command_zone())
        self.session.fmt = "commander"
        frame(gui, 2)
        self.assertTrue(gui.has_command_zone())
        self.assertGreater(gui.L.cmd_rect.w, 0)                  # a Commander game keeps its (empty) zone as before

    def test_the_tour_leaves_the_command_zone_out(self):
        import tour
        frame(self.gui, 2)
        self.assertNotIn("cmd", [st.region for st in tour.available_steps(self.gui)])
        self.session.fmt = "commander"
        frame(self.gui, 2)
        self.assertIn("cmd", [st.region for st in tour.available_steps(self.gui)])

    def test_the_vs_screen_shows_each_decks_face(self):
        seats = self.gui.vs_seats(([], ["Shock"] * 4 + ["Grizzly Bears"] * 4 + ["Mountain"] * 52), [([], ["Forest"] * 56 + ["Bite Down"] * 4)])
        self.assertEqual([s[1] for s in seats], [["Shock"], ["Bite Down"]])
        text = "[metadata]\nName=P\nDeck Type=Constructed\n[Main]\n4 Shock\n4 Grizzly Bears\n52 Mountain\n"
        self.assertEqual(flow.dck_faces(text), ["Shock"])
        self.assertEqual(flow.dck_faces("[metadata]\nName=P\n[Commander]\n1 Kinnan, Bonder Prodigy\n[Main]\n2 Forest\n"),
                         ["Kinnan, Bonder Prodigy"])


# ---- the soak tool --------------------------------------------------------------------------------------------------------------
class SoakTests(unittest.TestCase):
    def test_the_deck_pool_and_the_option(self):
        from tools import soak
        standard = soak.deck_pool("sample", "standard")
        self.assertEqual(sorted(os.path.basename(p)[:-4] for p in standard), sorted(STANDARD_SAMPLES))
        with mock.patch.object(soak, "run", side_effect=lambda a: a):
            self.assertEqual(soak.main(["--format", "standard"]).format, "standard")

    def test_standard_boards(self):
        import soak_boards as sb
        lines, _p = sb.board_lines([os.path.join(SAMPLES, "standard_red_aggro.txt")] * 2, random.Random(1), fmt="standard")
        life = int([ln for ln in lines if ln.startswith("p0life=")][0].split("=")[1])
        self.assertTrue(8 <= life <= 20, life)
        self.assertFalse(any("command=" in ln for ln in lines), lines)


# ---- the sample decks ------------------------------------------------------------------------------------------------------------
class SampleDeckTests(unittest.TestCase):
    def read(self, stem):
        with open(os.path.join(SAMPLES, stem + ".txt"), encoding="utf-8") as f:
            text = f.read()
        side = []
        cmd, deck = di.import_from_text(text, sideboard=side)
        return text, cmd, deck, side

    def test_two_standard_samples_that_follow_the_deck_rules(self):
        for stem in STANDARD_SAMPLES:
            with self.subTest(stem=stem):
                text, cmd, deck, _side = self.read(stem)
                self.assertTrue(text.startswith("# format: standard\n"))
                self.assertEqual(cmd, [])
                self.assertGreaterEqual(len(deck), 60)
                counts = {}
                for n in deck:
                    counts[n] = counts.get(n, 0) + 1
                over = {n: k for n, k in counts.items() if k > 4 and n not in ("Mountain", "Forest", "Plains", "Island", "Swamp")}
                self.assertEqual(over, {})
                self.assertIn(stem, lib.SAMPLE_NAMES)

    @unittest.skipUnless(os.path.isdir(os.path.join(BASE, "forge_runtime", "res", "cardsfolder")), "needs forge_runtime/")
    def test_forge_knows_every_card_and_the_ai_may_play_it(self):
        import card_check
        root = os.path.join(BASE, "forge_runtime", "res", "cardsfolder")
        flagged_control = card_check.find_script(root, "Beast Within")           # patch 38: a card Forge marks AI:RemoveDeck:All
        self.assertIn("AI:RemoveDeck", flagged_control)                          # so the check below can see a flag (the script's text)
        for stem in STANDARD_SAMPLES:
            _t, _c, deck, _s = self.read(stem)
            for name in sorted(set(deck)):
                with self.subTest(stem=stem, card=name):
                    script = card_check.find_script(root, name)
                    self.assertIsNotNone(script, name)
                    self.assertNotIn("AI:RemoveDeck", script, name)

    def test_the_build_ships_the_standard_banned_list(self):
        import tools.check_dist as cd
        self.assertIn("banned_standard.snapshot.json", cd.REQUIRED_FILES)
        with open(os.path.join(BASE, "commander_sim.spec"), encoding="utf-8") as f:
            self.assertIn('_data("banned_standard.snapshot.json", ".")', f.read())

    def test_the_build_expects_twelve_sample_decks(self):
        import tools.check_dist as cd
        self.assertEqual(cd.EXPECTED_SAMPLE_DECKS, 12)                          # 6 Commander + 4 Brawl + 2 Standard
        self.assertEqual(len([n for n in os.listdir(SAMPLES) if n.endswith(".txt")]), 12)


# ---- live: the real engine -----------------------------------------------------------------------------------------------------
import tests.live as live  # noqa: E402
from deck_loader import load_deck  # noqa: E402

LIVE_PROBLEM = live.live_problem()


@unittest.skipIf(LIVE_PROBLEM, f"Forge is not ready here: {LIVE_PROBLEM}")
class LiveStandardTests(unittest.TestCase):
    """A Standard game in the real engine: 20 life, no commander, Forge's Constructed game type, and 103.5c's mulligans."""

    def setUp(self):
        fc.sync_bridge()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def session(self, stems, seed=11):
        paths = []
        for i, stem in enumerate(stems):
            cmd, deck = load_deck(os.path.join(SAMPLES, stem + ".txt"))
            self.assertEqual(cmd, [])
            paths.append(fc.write_deck_file(os.path.join(self.tmp, f"d{i}.dck"), cmd, deck, f"D{i}", fmt="standard"))
        s = fc.ForgeSession(paths[0], paths[1:], name="Tester", seed=seed)
        s.stderr_path = os.path.join(self.tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        return s

    def started(self, s):
        end = time.time() + 150
        while time.time() < end:
            s.poll()
            self.assertFalse(s.exited or s.fatal, s.fatal or "Forge stopped while starting")
            if s.state and s.me() and s.state["prompt"].get("message"):        # Forge's first question: the decks are dealt
                return s
            time.sleep(0.05)
        self.fail("the game never started")

    def test_a_standard_1v1_starts_at_20_without_a_commander(self):
        s = self.started(self.session(["standard_red_aggro", "standard_green_dinos"]))
        self.assertEqual((s.fmt, s.format_used), ("standard", "constructed"))
        self.assertEqual([p["life"] for p in s.state["players"]], [20, 20])
        self.assertTrue(all(not p["zones"].get("command") for p in s.state["players"]))
        self.assertEqual(s.me()["handCount"] + s.me()["libraryCount"], 60)
        time.sleep(1.0)
        s.poll()
        self.assertEqual(s.checks, [])                       # the start check knows a Constructed game has no variant

    def test_a_standard_pod_starts_at_20(self):
        s = self.started(self.session(["standard_red_aggro", "standard_green_dinos", "standard_red_aggro"]))
        self.assertEqual([p["life"] for p in s.state["players"]], [20, 20, 20])

    def mulligan_twice(self, s):
        """Hand sizes at each keep-or-mulligan question and the cards asked back after each mulligan (test_forge_live's way)."""
        sizes, prompts = [], []
        taken, last, since, t0 = 0, None, time.time(), time.time()
        while time.time() - t0 < 120 and not (s.exited or s.fatal):
            s.poll()
            st = s.state
            if not st or not s.me():
                time.sleep(0.05)
                continue
            p, msg = st["prompt"], st["prompt"]["message"]
            key = (msg, len(s.me()["zones"]["hand"]))
            if key != last:
                last, since = key, time.time()
            if time.time() - since > 0.6:
                since = time.time() + 5
                if "who would you like to start" in msg.lower():                  # 3-4 players: a click on a player answers it
                    s.click_player(st["me"])
                elif "coin toss" in msg:
                    s.ok()
                elif s.is_mulligan_prompt():
                    sizes.append(s.me()["handCount"])
                    if taken < 2:
                        taken += 1
                        s.cancel()
                    else:
                        break
                elif msg.startswith("Return "):
                    n = int(msg.split()[1])
                    prompts.append(n)
                    for c in s.me()["zones"]["hand"][:n]:
                        s.click_card(c["id"])
                        time.sleep(0.3)
                    s.ok()
            time.sleep(0.05)
        return sizes, prompts

    def test_103_5c_the_first_mulligan_is_free_only_with_more_than_two_players(self):
        sizes, prompts = self.mulligan_twice(self.session(["standard_red_aggro", "standard_green_dinos"]))
        self.assertEqual((sizes, prompts), ([7, 6, 5], [1, 2]), "1v1 Standard: every mulligan costs a card")
        sizes, prompts = self.mulligan_twice(self.session(["standard_red_aggro", "standard_green_dinos", "standard_red_aggro"]))
        self.assertEqual((sizes, prompts), ([7, 7, 6], [0, 1]), "a 3-player Standard game: the first mulligan is free")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTableTests(unittest.TestCase):
    """The whole path through the real table: two Standard sample decks start a game with no command zone, and it plays on."""

    def setUp(self):
        from tests.forge_fake import StubStore
        from tests.forge_bot import Bot
        self.tmp = tempfile.mkdtemp(prefix="con1_table_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(mock.patch.object(ft, "DECK_DIR", os.path.join(self.tmp, "decks")))
        settings = os.path.join(self.tmp, "settings.json")
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"passing_version": 43, "tour_done": 99}, f)
        mine = load_deck(os.path.join(SAMPLES, "standard_red_aggro.txt"))
        opp = load_deck(os.path.join(SAMPLES, "standard_green_dinos.txt"))
        self.gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), settings_path=settings,
                                 launcher=ft.Launcher(None, "Karl", seed=4321), saves_dir=os.path.join(self.tmp, "saves"))
        self.addCleanup(self.close)
        self.assertIsNone(self.gui.begin(mine, [opp], fmt="standard"))
        self.bot = Bot(self.gui.session)

    def close(self):
        with mock.patch.object(ft.pygame, "quit"):
            self.gui.shutdown()

    def test_a_standard_game_through_the_table(self):
        gui, t0 = self.gui, time.time()
        turns, aimed = set(), None
        while time.time() - t0 < 240:
            gui.tick(time.monotonic())
            gui.vs = None
            st = gui.state or {}
            if st.get("turn"):
                turns.add(st["turn"])
            if gui.session.exited or gui.session.fatal:
                break
            if len(turns) >= 4:
                break
            if gui.modal is not None and gui.session.requests:
                self.bot.answer_requests()                       # a question from Forge (Burst Lightning's kicker): the bot answers
                gui.modal = None
            elif gui.modal is not None:
                gui.modal = None                                 # a list Forge showed: looked at, closed
            if gui.modal is None and st.get("input") == "InputSelectTargets" and st.get("inputSeq") != aimed:
                aimed = st.get("inputSeq")                       # the test bot can't aim: a burn spell goes at the opponent
                gui.session.click_player(next(p["id"] for p in st["players"] if p["id"] != st["me"]))
            elif gui.modal is None and st:
                self.bot.step()
            time.sleep(0.02)
        self.assertGreaterEqual(len(turns), 4, f"the game didn't get to turn 4 ({sorted(turns)}; {gui.session.fatal})")
        self.assertEqual(gui.game_format(), "standard")
        self.assertEqual(gui.L.cmd_rect.w, 0)
        self.assertEqual(gui.session.checks, [])
        self.assertEqual(gjournal.read_full(gui.journal.path)["start"]["format"], "standard")


if __name__ == "__main__":
    unittest.main()
