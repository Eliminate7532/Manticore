# SPDX-License-Identifier: GPL-3.0-or-later
"""Round FMT1: formats - MTG Arena's 100-card Brawl beside Commander (formats.py, legality, the deck screen, the engine)."""
import json
import os
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
BRAWL_SAMPLES = ("brawl_nissa", "brawl_krenko", "brawl_elas", "brawl_tetsuko")


def card(name, type_line, identity=(), oracle="", brawl="legal", **extra):
    c = {"name": name, "type_line": type_line, "color_identity": list(identity), "oracle_text": oracle,
         "legalities": {"commander": "legal", "brawl": brawl}}
    c.update(extra)
    return c


class Store:
    def __init__(self, cards):
        self.cards = {c["name"]: c for c in cards}

    def peek_card(self, name):
        return self.cards.get(name)


# ---- formats.py ----------------------------------------------------------------------------------------------------------------
class FormatTableTests(unittest.TestCase):
    def test_known_formats_and_the_default(self):
        self.assertEqual(formats.normal(None), "commander")
        self.assertEqual(formats.normal("BRAWL"), "brawl")
        self.assertEqual(formats.normal("standard"), "commander")          # not built yet: anything unknown is Commander
        self.assertEqual(formats.ORDER, ("commander", "brawl"))

    def test_brawl_is_arenas_100_card_brawl(self):
        b = formats.get("brawl")
        self.assertEqual(b.deck_size, 100)
        self.assertEqual((formats.starting_life("brawl", 2), formats.starting_life("brawl", 3), formats.starting_life("brawl", 4)),
                         (25, 30, 30))                                    # CR 903.12f
        self.assertFalse(b.commander_damage)                              # 903.12h
        self.assertTrue(b.planeswalker_commanders)                        # 903.12c
        self.assertFalse(b.online)
        self.assertEqual(formats.starting_life("commander", 4), 40)
        self.assertEqual(formats.life_note("commander", 2), "")
        self.assertEqual(formats.life_note("brawl", 2), "25 life each.")

    def test_the_deck_file_line(self):
        self.assertEqual(formats.from_text("1 Sol Ring\n"), "commander")
        self.assertIsNone(formats.declared("1 Sol Ring\n"))
        self.assertEqual(formats.from_text("# format: brawl\n1 Forest\n"), "brawl")
        self.assertEqual(formats.declared("#Format=Brawl\n"), "brawl")
        self.assertEqual(formats.with_format("1 Forest", "brawl"), "# format: brawl\n1 Forest")
        self.assertEqual(formats.with_format("# format: brawl\n1 Forest", "commander"), "1 Forest")
        self.assertEqual(formats.with_format("# format: commander\n\nCommander\n1 X", "brawl"), "# format: brawl\nCommander\n1 X")

    def test_the_dck_metadata_line(self):
        self.assertEqual(formats.dck_lines("commander"), [])
        self.assertEqual(formats.dck_lines("brawl"), ["Deck Type=Brawl"])
        self.assertEqual(formats.from_dck("[metadata]\nName=P\nDeck Type=Brawl\n[Commander]\n1 X\n"), "brawl")
        self.assertEqual(formats.from_dck("[metadata]\nName=P\n[Commander]\n1 X\n"), "commander")
        self.assertEqual(formats.from_dck_file(os.path.join(BASE, "no such file.dck")), "commander")


# ---- reading a deck --------------------------------------------------------------------------------------------------------------
class ImportTests(unittest.TestCase):
    def test_a_comment_line_is_not_a_card(self):
        cmd, deck = di.import_from_text("# format: brawl\nCommander\n1 Nissa, Who Shakes the World\n\nDeck\n1 Forest\n1 Llanowar Elves\n")
        self.assertEqual(cmd, ["Nissa, Who Shakes the World"])
        self.assertEqual(deck, ["Forest", "Llanowar Elves"])

    def test_an_mtg_arena_export_with_its_about_section(self):
        text = ("About\nName Every Forest\n\nCommander\n1 Nissa, Who Shakes the World (WAR) 169\n\nDeck\n"
                "30 Forest (ANB) 113\n1 Llanowar Elves (M19) 314\n")
        cmd, deck = di.import_from_text(text)
        self.assertEqual(cmd, ["Nissa, Who Shakes the World"])
        self.assertEqual(len(deck), 31)
        self.assertNotIn("Name Every Forest", deck)

    def test_a_bottom_commander_after_a_comment(self):
        cmd, deck = di.import_from_text("\n".join(["# format: brawl"] + ["1 Forest"] * 99 + ["", "1 Nissa, Who Shakes the World"]))
        self.assertEqual(cmd, ["Nissa, Who Shakes the World"])
        self.assertEqual(len(deck), 99)


# ---- legality ------------------------------------------------------------------------------------------------------------------
class BrawlLegalityTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(lg._BANNED)
        self.addCleanup(lambda: lg._BANNED.update(self.saved))
        for f in lg.FORMATS:
            lg._BANNED[f] = lg.BanList(frozenset({"oko, thief of crowns"} if f == "brawl" else {"sol ring"}), "2026-10-02T00:00:00Z",
                                       "snapshot")
        self.nissa = card("Nissa, Who Shakes the World", "Legendary Planeswalker — Nissa", "G")
        self.karn = card("Karn, Living Legacy", "Legendary Planeswalker — Karn", ())
        self.forest = card("Forest", "Basic Land — Forest", "G")
        self.island = card("Island", "Basic Land — Island", "U")
        self.wastes = card("Wastes", "Basic Land", ())
        self.elves = card("Llanowar Elves", "Creature — Elf Druid", "G")
        self.oko = card("Oko, Thief of Crowns", "Legendary Planeswalker — Oko", "GU")
        self.sol = card("Sol Ring", "Artifact", (), brawl="not_legal")

    def check(self, cmd, deck, fmt, *cards):
        return lg.check(cmd, deck, Store([self.nissa, self.karn, self.forest, self.island, self.wastes, self.elves, self.oko,
                                          self.sol] + list(cards)), fmt=fmt)

    def test_a_planeswalker_commander_is_legal_in_brawl_only(self):
        deck = ["Forest"] * 99
        self.assertEqual(self.check(["Nissa, Who Shakes the World"], deck, "brawl").status, "legal")
        v = self.check(["Nissa, Who Shakes the World"], deck, "commander")
        self.assertIn("commander", [k for k, _t, _c in v.issues])
        self.assertTrue(lg.blocks_start(v))
        self.assertEqual(lg.can_be_commander(self.nissa, "brawl"), (True, ""))
        self.assertEqual(lg.can_be_commander(self.elves, "brawl"), (False, "not legendary"))

    def test_brawl_uses_its_own_banned_list_and_arenas_card_pool(self):
        v = self.check(["Nissa, Who Shakes the World"], ["Oko, Thief of Crowns", "Sol Ring"] + ["Forest"] * 97, "brawl")
        kinds = {k: c for k, _t, c in v.issues}
        self.assertEqual(kinds.get("banned"), ["Oko, Thief of Crowns"])
        self.assertEqual(kinds.get("not_legal"), ["Sol Ring"])
        lines = [t for t, _b in lg.problem_lines(v, "brawl")]
        self.assertTrue(any(t.startswith("Banned in Brawl (list as of 2 Oct 2026): Oko") for t in lines), lines)
        self.assertTrue(any(t.startswith("Not legal in Brawl (not on MTG Arena): Sol Ring") for t in lines), lines)
        # the same deck in Commander: Sol Ring is the banned card there, Oko is fine
        v = self.check(["Nissa, Who Shakes the World"], ["Oko, Thief of Crowns", "Sol Ring"] + ["Forest"] * 97, "commander")
        self.assertEqual({k: c for k, _t, c in v.issues}.get("banned"), ["Sol Ring"])

    def test_the_size_line_names_the_format(self):
        v = self.check(["Nissa, Who Shakes the World"], ["Forest"] * 59, "brawl")
        self.assertIn("This deck has 60 cards; a Brawl deck has 100 (commander included).", [t for _k, t, _c in v.issues])

    def test_a_colourless_commander_may_have_basics_of_one_type(self):
        # 903.12e: Karn (no colours) with Islands is legal; Forests on top of that are outside its colours
        v = self.check(["Karn, Living Legacy"], ["Island"] * 60 + ["Wastes"] * 39, "brawl")
        self.assertEqual(v.status, "legal", v.issues)
        v = self.check(["Karn, Living Legacy"], ["Island"] * 60 + ["Forest"] * 39, "brawl")
        ident = [c for k, _t, c in v.issues if k == "identity"]
        self.assertEqual(ident, [["Forest"]])
        # Commander has no such rule
        v = self.check(["Karn, Living Legacy"], ["Island"] * 99, "commander")
        self.assertIn("identity", [k for k, _t, _c in v.issues])

    def test_the_blocking_commander_line_says_planeswalker_in_brawl(self):
        v = self.check(["Llanowar Elves"], ["Forest"] * 99, "brawl")
        line = [t for t, b in lg.problem_lines(v, "brawl") if b][0]
        self.assertIn("A Brawl commander must be a legendary creature or planeswalker", line)
        v = self.check(["Llanowar Elves"], ["Forest"] * 99, "commander")
        line = [t for t, b in lg.problem_lines(v, "commander") if b][0]
        self.assertIn("A commander must be a legendary creature (or say", line)      # Commander's words are unchanged

    def test_the_bundled_brawl_snapshot(self):
        with open(os.path.join(BASE, "banned_brawl.snapshot.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["query"], "banned:brawl")
        self.assertIn("Oko, Thief of Crowns", data["cards"])
        self.assertNotIn("Sol Ring", data["cards"])           # not banned on Arena: it isn't on Arena at all (not_legal)
        self.assertGreater(len(data["cards"]), 20)
        self.assertEqual(lg.snapshot_file("brawl"), os.path.join(BASE, "banned_brawl.snapshot.json"))

    def test_start_loads_every_formats_list(self):
        with mock.patch.object(lg, "load") as load, mock.patch.object(lg, "refresh_in_background") as refresh:
            lg.start([])
        self.assertEqual([c.args[0] for c in load.call_args_list], ["commander", "brawl"])
        self.assertEqual([c.args[0] for c in refresh.call_args_list], ["commander", "brawl"])


# ---- the deck library --------------------------------------------------------------------------------------------------------
class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib_dir = os.path.join(self.tmp, "my_decks")

    def test_a_deck_file_knows_its_format(self):
        e = lib.DeckEntry(os.path.join(SAMPLES, "brawl_nissa.txt"), "n", True).load()
        self.assertEqual(e.format, "brawl")
        self.assertEqual(e.commanders, ["Nissa, Who Shakes the World"])
        k = lib.DeckEntry(os.path.join(SAMPLES, "kinnan_nbc_moxfield_export.txt"), "k", True).load()
        self.assertEqual(k.format, "commander")

    def test_saving_a_pasted_deck_writes_its_format(self):
        text = "Commander\n1 Nissa, Who Shakes the World\n\nDeck\n" + "\n".join(["1 Forest"] * 99)
        e = lib.save_text("Nissa", text, self.lib_dir, self.tmp, fmt="brawl")
        self.assertEqual(e.format, "brawl")
        with open(e.path, encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("# format: brawl\nCommander\n"))
        c = lib.save_text("Nissa C", text, self.lib_dir, self.tmp)              # no format: the text's own (none = Commander)
        self.assertEqual(c.format, "commander")
        copy = lib.copy_to_library(lib.DeckEntry(os.path.join(SAMPLES, "brawl_krenko.txt"), "Goblins", True).load(),
                                   self.lib_dir, self.tmp)
        self.assertEqual(copy.format, "brawl")                                  # a copied sample keeps its format

    def test_problems_follow_the_format(self):
        e = lib.DeckEntry(os.path.join(SAMPLES, "brawl_nissa.txt"), "n", True).load()
        with mock.patch.object(lg, "check", return_value=lg.Verdict("legal", [], [], None)) as check:
            e.problems()
        self.assertEqual(check.call_args.kwargs.get("fmt"), "brawl")
        lines = lib.legality_lines(["X"], ["Forest"] * 10, "brawl")              # (the real check, no card data here)
        self.assertTrue(any("a Brawl deck has 100" in t for t, _b in lines), lines)


class SampleDeckTests(unittest.TestCase):
    def test_four_brawl_samples_of_100_cards(self):
        for stem in BRAWL_SAMPLES:
            e = lib.DeckEntry(os.path.join(SAMPLES, stem + ".txt"), stem, True).load()
            self.assertIsNone(e.error, stem)
            self.assertEqual(e.format, "brawl", stem)
            self.assertEqual(e.total, 100, stem)
            self.assertEqual(len(e.commanders), 1, stem)
            counts = {}
            for n in e.deck:
                counts[n] = counts.get(n, 0) + 1
            extra = [n for n, k in counts.items() if k > 1 and n not in ("Forest", "Mountain", "Plains", "Swamp", "Island")]
            self.assertEqual(extra, [], stem)
            self.assertNotEqual(lib.display_name(stem, True), stem.replace("_", " ").title() + " (sample)", stem)

    @unittest.skipUnless(os.path.isdir(os.path.join(BASE, "forge_runtime", "res", "cardsfolder")), "no Forge runtime here")
    def test_forge_knows_every_card_of_the_brawl_samples(self):
        runtime = os.path.join(BASE, "forge_runtime")
        for stem in BRAWL_SAMPLES:
            e = lib.DeckEntry(os.path.join(SAMPLES, stem + ".txt"), stem, True).load()
            self.assertEqual(fc.unknown_cards(e.commanders + e.deck, runtime), [], stem)

    def test_the_build_expects_ten_sample_decks(self):
        from tools import check_dist as cd
        self.assertEqual(cd.EXPECTED_SAMPLE_DECKS, len([f for f in os.listdir(SAMPLES) if f.endswith(".txt")]))
        self.assertIn("banned_brawl.snapshot.json", cd.REQUIRED_FILES)
        with open(os.path.join(BASE, "commander_sim.spec"), encoding="utf-8") as f:
            self.assertIn('_data("banned_brawl.snapshot.json", ".")', f.read())


# ---- the engine's side -----------------------------------------------------------------------------------------------------------
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_brawl_dck_says_so_and_a_commander_dck_is_unchanged(self):
        b = fc.write_deck_file(os.path.join(self.tmp, "b.dck"), ["Nissa, Who Shakes the World"], ["Forest"] * 2, "P", fmt="brawl")
        c = fc.write_deck_file(os.path.join(self.tmp, "c.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 2, "P")
        with open(b, encoding="utf-8") as f:
            self.assertEqual(f.read().split("\n")[:4], ["[metadata]", "Name=P", "Deck Type=Brawl", "[Commander]"])
        with open(c, encoding="utf-8") as f:
            self.assertEqual(f.read(), "[metadata]\nName=P\n[Commander]\n1 Kinnan, Bonder Prodigy\n[Main]\n2 Forest\n")

    def test_the_session_reads_its_format_and_tells_the_bridge(self):
        b = fc.write_deck_file(os.path.join(self.tmp, "b.dck"), ["Nissa, Who Shakes the World"], ["Forest"], "P", fmt="brawl")
        c = fc.write_deck_file(os.path.join(self.tmp, "c.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"], "P")
        s = fc.ForgeSession(b, [b], seed=1)
        self.assertEqual(s.fmt, "brawl")                         # from player.dck: Resume and replay.py get it this way
        args = s._bridge_args()
        self.assertEqual(args[args.index("--format") + 1], "brawl")
        s = fc.ForgeSession(c, [c], seed=1)
        self.assertEqual(s.fmt, "commander")
        self.assertNotIn("--format", s._bridge_args())           # a Commander game's command line is what it was
        self.assertEqual(fc.ForgeSession(c, [c], seed=1, fmt="brawl").fmt, "brawl")
        self.assertEqual(fc.ForgeSession("", []).fmt, "commander")

    def test_an_old_bridge_that_ignores_the_format_is_a_fatal_error(self):
        b = fc.write_deck_file(os.path.join(self.tmp, "b.dck"), ["Nissa, Who Shakes the World"], ["Forest"], "P", fmt="brawl")
        s = fc.ForgeSession(b, [b], seed=1)
        s.handle({"t": "ready", "protocol": 2})                   # no "format": a bridge from before this round
        self.assertIn("older than this program", s.fatal or "")
        s = fc.ForgeSession(b, [b], seed=1)
        s.handle({"t": "ready", "protocol": 2, "format": "brawl", "startingLife": 25})
        self.assertIsNone(s.fatal)
        self.assertEqual(s.format_used, "brawl")

    def test_the_bridge_source(self):
        src = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
        with open(os.path.join(src, "Main.java"), encoding="utf-8") as f:
            main = f.read()
        self.assertIn('case "--format"', main)
        self.assertIn("RegisteredPlayer.forVariants(seats, java.util.EnumSet.of(GameType.Brawl)", main)
        self.assertIn("match.startMatch(variant, variants, players, human, gui);", main)
        self.assertIn('ready.addProperty("format"', main)
        # a GameType in a static field's initialiser loads GameType before Forge's Localizer exists: the bridge died at start-up
        self.assertNotRegex(main, r"static\s+(final\s+)?GameType\s+\w+\s*=")
        with open(os.path.join(src, "BridgeGui.java"), encoding="utf-8") as f:
            self.assertIn("hasAppliedVariant(Main.variant())", f.read())
        with open(os.path.join(src, "NetHost.java"), encoding="utf-8") as f:
            self.assertIn("GameType.Commander", f.read())          # online games stay Commander (MP2's file is not touched)

    def test_the_journal_and_the_bug_report_name_a_brawl_game(self):
        j = gjournal.GameJournal(self.tmp)
        j.start(1, "Karl", {"player.dck": "x"}, "code", 100.0, fmt="brawl")
        j.close()
        start, _c, _e = gjournal.read(j.path)
        self.assertEqual(start["format"], "brawl")
        j.start(2, "Karl", {"player.dck": "x"}, "code", 101.0, fmt="commander")
        j.close()
        self.assertNotIn("format", gjournal.read(j.path)[0])
        text = reporting.report_text({"name": "K", "seed": 1, "format": "brawl"}, None, [], "now")
        self.assertIn("Format:   Brawl\nGame:     ", text)
        self.assertNotIn("Format:", reporting.report_text({"name": "K", "seed": 1, "format": "commander"}, None, [], "now"))


# ---- the deck screen ------------------------------------------------------------------------------------------------------------
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame  # noqa: E402
import forge_menu as fmenu  # noqa: E402
from tests import test_deck_screen as tds  # noqa: E402
from tests.test_forge_table import click, frame  # noqa: E402


class FormatLauncher(tds.FakeLauncher):
    """FakeLauncher that also records the format a game was started in."""

    def start(self, mine, opponents, fmt=None):
        self.formats = getattr(self, "formats", []) + [fmt]
        return super().start(mine, opponents)


class DeckScreenTests(tds.TempDecks):
    def setUp(self):
        super().setUp()
        for stem in ("brawl_nissa", "brawl_krenko"):
            shutil.copy(os.path.join(SAMPLES, stem + ".txt"), self.samples)

    def gui(self, size=(1360, 840), scale=None, settings=None):
        gui = self.idle_gui(size, scale, settings)
        self.launcher = gui.launcher = FormatLauncher()
        return gui

    def names(self, gui):
        return [e.name for e in gui.menu.visible()]

    def test_the_screen_shows_one_formats_decks(self):
        gui = self.gui()
        self.assertEqual(gui.menu.fmt, "commander")
        self.assertEqual(self.names(gui), ["Kinnan NBC (sample)"])
        click(gui, self.button_point(gui, "fmt_brawl"))
        self.assertEqual(gui.menu.fmt, "brawl")
        self.assertEqual(sorted(self.names(gui)), ["Every Forest Is a Weapon (Lands)", "Goblin Union Meeting (Goblins)"])
        self.assertIn("Brawl (MTG Arena)", gui.menu.message[0])
        self.assertIn(gui.menu.mine.name, self.names(gui))
        frame(gui, 2)
        self.assertEqual({e.name for _r, e in gui.menu.rows}, set(self.names(gui)))

    def test_each_format_remembers_its_own_decks(self):
        path = os.path.join(self.tmp, "settings.json")
        gui = self.gui(settings=path)
        click(gui, self.button_point(gui, "fmt_brawl"))
        click(gui, self.row_point(gui, "Goblin Union Meeting (Goblins)"))
        click(gui, self.button_point(gui, "plus"))
        click(gui, self.button_point(gui, "fmt_commander"))
        self.assertEqual(gui.menu.mine.name, "Kinnan NBC (sample)")
        self.assertEqual(gui.menu.count, 1)
        click(gui, self.button_point(gui, "fmt_brawl"))
        self.assertEqual(gui.menu.mine.name, "Goblin Union Meeting (Goblins)")
        self.assertEqual(gui.menu.count, 2)
        c = gui.menu.choice()
        self.assertEqual(c["format"], "brawl")
        self.assertEqual(c["formats"]["commander"]["mine"], "sample_decks/kinnan_nbc_moxfield_export.txt")
        click(gui, self.button_point(gui, "start"))
        self.assertIsNone(gui.menu)
        gui2 = self.gui(settings=path)                         # the next time the screen opens on Brawl, with those decks
        self.assertEqual(gui2.menu.fmt, "brawl")
        self.assertEqual(gui2.menu.mine.name, "Goblin Union Meeting (Goblins)")

    def test_start_plays_the_format_and_restart_keeps_it(self):
        gui = self.gui()
        click(gui, self.button_point(gui, "fmt_brawl"))
        click(gui, self.button_point(gui, "plus"))
        click(gui, self.button_point(gui, "random2"))
        gui.menu.rng.seed(1)
        click(gui, self.button_point(gui, "start"))
        self.assertIsNone(gui.menu)
        self.assertEqual(self.launcher.formats, ["brawl"])
        self.assertEqual(gui.current_format, "brawl")
        mine, opps = self.launcher.calls[0]
        brawl_commanders = {"Nissa, Who Shakes the World", "Krenko, Tin Street Kingpin"}
        self.assertTrue(all(o[0][0] in brawl_commanders for o in opps), opps)     # Random picked Brawl decks only
        gui.restart_game()
        self.assertEqual(self.launcher.formats, ["brawl", "brawl"])

    def test_a_commander_game_starts_as_before(self):
        gui = self.gui()
        click(gui, self.button_point(gui, "start"))
        self.assertEqual(self.launcher.formats, [None])             # the launcher isn't even told: nothing changes for Commander
        self.assertEqual(gui.current_format, "commander")

    def test_the_table_refuses_decks_of_two_formats(self):
        gui = self.gui()
        entries = {e.name: e for e in lib.list_decks(*self.dirs)}
        msg = gui.start_game(entries["Every Forest Is a Weapon (Lands)"], [entries["Kinnan NBC (sample)"]], 1)
        self.assertEqual(msg, "The opponent's deck is a Commander deck, and this is a Brawl game.")
        self.assertEqual(self.launcher.calls, [])

    def test_online_play_stays_commander(self):
        gui = self.gui()
        gui.open_host = mock.Mock(return_value=None)
        click(gui, self.button_point(gui, "fmt_brawl"))
        gui.menu.press(gui, "host_online")
        gui.open_host.assert_not_called()
        self.assertIn("Online games are Commander only for now", gui.menu.message[0])
        entries = {e.name: e for e in lib.list_decks(*self.dirs)}
        self.assertIn("Commander only", ft_online_problem(gui, entries["Goblin Union Meeting (Goblins)"]))

    def test_the_import_window_saves_the_chosen_format(self):
        gui = self.gui()
        d = gui.menu.open_import(gui, "Commander\n1 Nissa, Who Shakes the World\n\nDeck\n" + "\n".join(["1 Forest"] * 99))
        d = gui.modal
        frame(gui, 2)
        self.assertEqual(d.fmt, "commander")
        click(gui, self.dialog_button(gui, "fmt_brawl"))
        self.assertEqual(d.fmt, "brawl")
        self.assertFalse(any("Can't be a commander" in t for t, _b in d.problems))
        click(gui, self.dialog_button(gui, "save"))
        frame(gui, 2)
        with open(os.path.join(self.lib, "Nissa, Who Shakes the World.txt"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("# format: brawl\n"))
        self.assertEqual(gui.menu.fmt, "brawl")                   # the screen moved to where the new deck is
        self.assertEqual(gui.menu.mine.name, "Nissa, Who Shakes the World")

    def test_a_pasted_format_line_picks_the_format(self):
        gui = self.gui()
        gui.menu.open_import(gui, "# format: brawl\nCommander\n1 Krenko, Tin Street Kingpin\n\nDeck\n1 Mountain")
        self.assertEqual(gui.modal.fmt, "brawl")

    def test_renders_with_the_format_buttons(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1100, 700), 2.0), ((1920, 1080), 1.5)):
            with self.subTest(size=size, scale=scale):
                gui = self.gui(size, scale)
                click(gui, self.button_point(gui, "fmt_brawl"))
                frame(gui, 2)
                window = pygame.Rect(0, 0, *size)
                rects = gui.menu.format_rects
                self.assertEqual(set(rects), {"fmt_commander", "fmt_brawl"})
                for r in rects.values():
                    self.assertTrue(window.contains(r), (r, size, scale))
                self.assertFalse(rects["fmt_commander"].colliderect(rects["fmt_brawl"]))
                title_w = gui.font("big", True).size("Choose your decks")[0]
                self.assertGreater(min(r.x for r in rects.values()), title_w, (size, scale))   # never over the title
                gui.menu.open_import(gui, "")
                frame(gui, 2)
                d = gui.modal
                taken = [r for r, n in d.buttons if not n.startswith("fmt_")]
                for r in d.format_rects.values():                  # never on top of Paste / Clear, never off the window
                    self.assertTrue(window.contains(r), (r, size, scale))
                    self.assertFalse(any(r.colliderect(t) for t in taken), (r, size, scale))

    def test_the_loading_screen_names_the_format(self):
        gui = self.gui()
        gui.session = SimpleNamespace(fmt="brawl", opponent_paths=["a", "b"])
        self.assertEqual(gui.format_line(), "Brawl - 30 life each, no commander damage.")
        gui.session = SimpleNamespace(fmt="commander", opponent_paths=["a"])
        self.assertEqual(gui.format_line(), "")


def ft_online_problem(gui, entry):
    return gui._online_deck_problem(entry) or ""


# ---- the soak tool ------------------------------------------------------------------------------------------------------------
class SoakTests(unittest.TestCase):
    def test_the_deck_pool_keeps_to_one_format(self):
        from tools import soak
        commander = soak.deck_pool("sample")
        brawl = soak.deck_pool("sample", "brawl")
        self.assertEqual(len(commander), 6)
        self.assertEqual(sorted(os.path.basename(p)[:-4] for p in brawl), sorted(BRAWL_SAMPLES))
        with mock.patch.object(soak, "run", side_effect=lambda a: a):
            self.assertEqual(soak.main(["--format", "brawl"]).format, "brawl")
            self.assertEqual(soak.main([]).format, "commander")

    def test_brawl_boards_have_brawl_life(self):
        import random
        import soak_boards as sb
        lines, _p = sb.board_lines([os.path.join(SAMPLES, "brawl_krenko.txt")], random.Random(1), fmt="brawl")
        life = int([ln for ln in lines if ln.startswith("p0life=")][0].split("=")[1])
        self.assertTrue(15 <= life <= 25, life)


# ---- live: the real engine -----------------------------------------------------------------------------------------------------
import tests.live as live  # noqa: E402
from deck_loader import load_deck  # noqa: E402

LIVE_PROBLEM = live.live_problem()


@unittest.skipIf(LIVE_PROBLEM, f"Forge is not ready here: {LIVE_PROBLEM}")
class LiveBrawlTests(unittest.TestCase):
    """A Brawl game in the real engine: 25 life in a 1v1, 30 in a pod, the commander (a planeswalker) in the command zone."""

    def setUp(self):
        fc.sync_bridge()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def start(self, stems, fmt):
        paths = []
        for i, stem in enumerate(stems):
            cmd, deck = load_deck(os.path.join(SAMPLES, stem + ".txt"))
            paths.append(fc.write_deck_file(os.path.join(self.tmp, f"d{i}.dck"), cmd, deck, f"D{i}", fmt=fmt))
        s = fc.ForgeSession(paths[0], paths[1:], name="Tester", seed=11)
        s.stderr_path = os.path.join(self.tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end = time.time() + 150
        while time.time() < end:
            s.poll()
            self.assertFalse(s.exited or s.fatal, s.fatal or "Forge stopped while starting")
            if s.me() and s.me()["zones"].get("command"):
                return s
            time.sleep(0.05)
        self.fail("the game never started")

    def test_a_brawl_1v1_starts_at_25_with_a_planeswalker_commander(self):
        s = self.start(["brawl_nissa", "brawl_krenko"], "brawl")
        self.assertEqual(s.fmt, "brawl")
        self.assertEqual(s.format_used, "brawl")
        self.assertEqual([p["life"] for p in s.state["players"]], [25, 25])
        self.assertEqual([c["name"] for c in s.me()["zones"]["command"]], ["Nissa, Who Shakes the World"])
        self.assertEqual(s.checks, [])                  # the start check found the Brawl variant applied

    def test_a_brawl_pod_starts_at_30(self):
        s = self.start(["brawl_elas", "brawl_tetsuko", "brawl_krenko"], "brawl")
        self.assertEqual([p["life"] for p in s.state["players"]], [30, 30, 30])


if __name__ == "__main__":
    unittest.main()
