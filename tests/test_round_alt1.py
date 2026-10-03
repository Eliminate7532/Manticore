# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ALT1: alternate card art - a printing per card per deck, and your own pictures in my_art/."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import art_loader
import art_picker
import card_data
import deck_importer
import deck_library as lib
import forge_menu as fmenu
import forge_table as ft
import journal as gjournal
from card_data import ArtKey
from tests.forge_fake import FakeSession, load_state

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pygame.init()
pygame.display.set_mode((10, 10))


class FakeResponse:
    def __init__(self, status_code=200, content=b"\xff\xd8\xfake", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json = json_data or {}

    def json(self):
        return self._json


def lines_of(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def near(test, got, want, tol=6):
    test.assertTrue(all(abs(a - b) <= tol for a, b in zip(got[:3], want)), f"{got[:3]} is not about {want}")


def png(path, colour, size=(60, 84)):
    s = pygame.Surface(size)
    s.fill(colour)
    pygame.image.save(s, str(path))
    return str(path)


class FileStore:
    """Pictures from a temp folder, one colour per picture key; never the network."""
    last_error = None

    def __init__(self, folder):
        self.folder = folder
        self.files = {}

    def add(self, key, colour, sizes=("normal", "large", "art_crop", "small")):
        for size in sizes:
            p = card_data.CardDataStore._image_file(key, size)
            self.files[(key, size)] = png(os.path.join(self.folder, f"{p.parent.name}_{p.name}.png"), colour)

    def prefetch_cards(self, names):
        return 0

    def peek_card(self, name):
        return None

    def peek_image_path(self, key, size="normal"):
        return self.files.get((key, size))

    def get_image_path(self, key, size="normal"):
        return self.peek_image_path(key, size)


# ---- 1. the parser ----------------------------------------------------------------------------------------------------------------
class ParserTests(unittest.TestCase):
    def test_the_edge_cases(self):
        cases = {
            "1 Kinnan, Bonder Prodigy (PLST) ARB-1": ("kinnan, bonder prodigy", ("plst", "ARB-1")),
            "1 Swamp (SLD) 1500★": ("swamp", ("sld", "1500★")),
            "1 Esika, God of the Tree / The Prismatic Bridge (KHM) 290": ("esika, god of the tree / the prismatic bridge", ("khm", "290")),
            "1 Sol Ring (40K) 228": ("sol ring", ("40k", "228")),
            "1 Ponder [Ramp] (M12) 73": ("ponder", ("m12", "73")),
            "1 Brainstorm (MH2) 267 [Draw]": ("brainstorm", ("mh2", "267")),
            "1 Opt (XLN) 65 *F*": ("opt", ("xln", "65")),
        }
        for line, (name, pr) in cases.items():
            with self.subTest(line=line):
                _c, deck, printings = deck_importer.import_from_text_with_printings("Commander\n1 Kinnan, Bonder Prodigy\n\n" + line)
                self.assertEqual(printings.get(name), pr)

    def test_import_from_text_is_unchanged(self):
        text = "Commander\n1 Kinnan, Bonder Prodigy (IKO) 192\n\nDeck\n1 Sol Ring (C21) 263 *F* [Ramp]\n30 Island\n"
        self.assertEqual(deck_importer.import_from_text(text), (["Kinnan, Bonder Prodigy"], ["Sol Ring"] + ["Island"] * 30))
        self.assertEqual(deck_importer.import_from_text_with_printings(text)[2],
                         {"kinnan, bonder prodigy": ("iko", "192"), "sol ring": ("c21", "263")})

    def test_the_first_printing_of_a_name_wins(self):
        _c, _d, p = deck_importer.import_from_text_with_printings("1 Kinnan, Bonder Prodigy\n\n10 Swamp (ONE) 271\n10 Swamp (DMU) 270\n")
        self.assertEqual(p["swamp"], ("one", "271"))

    def test_no_printing_without_a_collector_number(self):
        self.assertIsNone(deck_importer.printing_of("Sol Ring (C21)"))
        self.assertIsNone(deck_importer.printing_of("Sol Ring"))

    def test_every_sample_deck_reads_the_same_as_before(self):
        for fn in os.listdir(os.path.join(HERE, "sample_decks")):
            with open(os.path.join(HERE, "sample_decks", fn), encoding="utf-8-sig") as f:
                text = f.read()
            c, d, _p = deck_importer.import_from_text_with_printings(text)
            self.assertEqual((c, d), deck_importer.import_from_text(text))

    def test_the_kinnan_sample_names_a_printing_on_every_line(self):
        e = lib.DeckEntry(os.path.join(HERE, "sample_decks", "kinnan_nbc_moxfield_export.txt"), "k", True).load()
        self.assertEqual(e.printings["ancient tomb"], ("uma", "236"))
        self.assertGreater(len(e.printings), 60)


# ---- 2. the store -----------------------------------------------------------------------------------------------------------------
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        base = Path(self.tmp)
        for attr, sub in (("IMAGE_CACHE_DIR", "images"), ("IMAGE_CACHE_DIR_LARGE", "images_large"),
                          ("IMAGE_CACHE_DIR_ART", "images_art"), ("IMAGE_CACHE_DIR_SMALL", "images_small"),
                          ("DATA_CACHE_FILE", "card_data.json"), ("CACHE_DIR", "")):
            p = mock.patch.object(card_data, attr, base / sub if sub else base)
            p.start()
            self.addCleanup(p.stop)
        with mock.patch("time.sleep"):
            self.store = card_data.CardDataStore()
        self.store.cards["sol ring"] = {"name": "Sol Ring", "image_uris": {"normal": "https://img/default.jpg"}}
        sleep = mock.patch("card_data.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_the_default_printing_keeps_its_file_name(self):
        self.assertEqual(card_data.CardDataStore._image_file("Sol Ring").name, "sol_ring.jpg")
        self.assertEqual(card_data.CardDataStore._image_file(ArtKey("Sol Ring")).name, "sol_ring.jpg")

    def test_a_printing_has_its_own_file(self):
        f = card_data.CardDataStore._image_file(ArtKey("Sol Ring", "sld", "1500★"), "small")
        self.assertEqual(f.name, "sol_ring__sld_1500_.jpg")
        self.assertEqual(f.parent.name, "images_small")

    def test_art_key_is_a_plain_name_for_the_default(self):
        self.assertEqual(card_data.art_key("Sol Ring"), "Sol Ring")
        self.assertEqual(card_data.art_key("Sol Ring", "C21", "263"), ArtKey("Sol Ring", "c21", "263"))

    def test_get_printing_caches(self):
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.return_value = FakeResponse(json_data={"name": "Sol Ring", "image_uris": {"normal": "https://img/c21.jpg"}})
            a = self.store.get_printing("C21", "263")
            b = self.store.get_printing("c21", "263")
        self.assertEqual(a, b)
        self.assertEqual(sess.get.call_count, 1)
        self.assertIn("/cards/c21/263", sess.get.call_args[0][0])

    def test_a_printing_downloads_its_own_picture(self):
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.side_effect = [FakeResponse(json_data={"name": "Sol Ring", "image_uris": {"normal": "https://img/c21.jpg"}}),
                                    FakeResponse()]
            path = self.store.get_image_path(ArtKey("Sol Ring", "c21", "263"))
        self.assertTrue(path.endswith("sol_ring__c21_263.jpg"))
        self.assertEqual(sess.get.call_args[0][0], "https://img/c21.jpg")

    def test_a_404_is_permanent_and_falls_back_to_the_default_picture(self):
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.side_effect = [FakeResponse(404), FakeResponse()]
            path = self.store.get_image_path(ArtKey("Sol Ring", "zzz", "999"))
            self.assertTrue(path.endswith("sol_ring.jpg"))
            self.assertIsNone(self.store.get_printing("zzz", "999"))
            self.assertEqual(sess.get.call_count, 2)                       # the printing once, the default picture once
            self.assertEqual(self.store.get_image_path(ArtKey("Sol Ring", "zzz", "999")), path)
            self.assertEqual(sess.get.call_count, 2)

    def test_offline_still_gives_the_default_picture_when_it_is_cached(self):
        card_data.CardDataStore._image_file("Sol Ring").parent.mkdir(parents=True, exist_ok=True)
        card_data.CardDataStore._image_file("Sol Ring").write_bytes(b"x")
        import requests
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.side_effect = requests.ConnectionError("offline")
            path = self.store.get_image_path(ArtKey("Sol Ring", "c21", "263"))
        self.assertTrue(path.endswith("sol_ring.jpg"))

    def test_list_printings_follows_pages_and_keeps_paper_only(self):
        page1 = {"data": [{"set": "C21", "collector_number": "263", "set_name": "Commander 2021", "released_at": "2021-04-23",
                           "games": ["paper", "mtgo"], "image_uris": {"small": "s1"}},
                          {"set": "PRM", "collector_number": "1", "games": ["mtgo"], "image_uris": {"small": "s0"}}],
                 "has_more": True, "next_page": "https://api.scryfall.com/cards/search?page=2"}
        page2 = {"data": [{"set": "LEA", "collector_number": "270", "set_name": "Alpha", "released_at": "1993-08-05",
                           "games": ["paper"], "image_uris": {"small": "s2"}}], "has_more": False}
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.side_effect = [FakeResponse(json_data=page1), FakeResponse(json_data=page2)]
            got = self.store.list_printings("Sol Ring")
            again = self.store.list_printings("Sol Ring")
        self.assertEqual([(p["set"], p["cn"]) for p in got], [("c21", "263"), ("lea", "270")])
        self.assertIs(got, again)
        self.assertEqual(sess.get.call_count, 2)
        self.assertEqual(sess.get.call_args_list[0][1]["params"]["q"], '!"Sol Ring"')


# ---- 3. the loader ----------------------------------------------------------------------------------------------------------------
class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = FileStore(self.tmp)
        self.store.add("Sol Ring", (200, 0, 0))
        self.store.add(ArtKey("Sol Ring", "c21", "263"), (0, 0, 200))
        self.art_dir = os.path.join(self.tmp, "my_art")
        os.makedirs(self.art_dir)

    def loader(self):
        return art_loader.ArtLoader(self.store, custom_dir=self.art_dir)

    def wait(self, art, key):
        import time
        for _ in range(200):
            art.collect()
            img = art.get(key)
            if img is not None:
                return img
            time.sleep(0.01)
        self.fail(f"no picture for {key}")

    def test_two_printings_of_one_card_are_two_pictures(self):
        art = self.loader()
        a = self.wait(art, "Sol Ring")
        b = self.wait(art, ArtKey("Sol Ring", "c21", "263"))
        self.assertIsNot(a, b)
        near(self, a.get_at((5, 5)), (200, 0, 0))
        near(self, b.get_at((5, 5)), (0, 0, 200))
        near(self, art.preview(ArtKey("Sol Ring", "c21", "263"), 50, 70).get_at((5, 5)), (0, 0, 200))
        near(self, art.preview("Sol Ring", 50, 70).get_at((5, 5)), (200, 0, 0))

    def test_a_plain_name_and_a_default_art_key_are_the_same_picture(self):
        art = self.loader()
        self.assertIs(self.wait(art, "Sol Ring"), art.get(ArtKey("Sol Ring")))

    # ---- 6. my_art/ (Phase 2)
    def test_lookup_order_one_printing_then_the_card_then_scryfall(self):
        png(os.path.join(self.art_dir, "Sol Ring.png"), (0, 200, 0))
        png(os.path.join(self.art_dir, "sol ring__C21_263.jpg"), (200, 200, 0))
        art = self.loader()
        near(self, art.get(ArtKey("Sol Ring", "c21", "263")).get_at((5, 5)), (200, 200, 0))    # that printing's file
        near(self, art.get(ArtKey("Sol Ring", "lea", "270")).get_at((5, 5)), (0, 200, 0))      # the card's file
        near(self, art.get("Sol Ring").get_at((5, 5)), (0, 200, 0))
        self.assertTrue(art.is_custom("Sol Ring"))
        self.assertIsNone(art.art_crop("Sol Ring", 40, 30))                     # no art crop of your own picture
        self.assertFalse(art.is_custom("Arcane Signet"))

    def test_a_slash_in_a_card_name_is_written_as_an_underscore(self):
        png(os.path.join(self.art_dir, "Fire _ Ice.png"), (0, 200, 0))
        art = self.loader()
        self.assertTrue(art.custom_path("Fire // Ice"))

    def test_an_oversize_picture_is_refused(self):
        png(os.path.join(self.art_dir, "Sol Ring.png"), (0, 200, 0), size=(4097, 10))
        art = self.loader()
        self.assertFalse(art.is_custom("Sol Ring"))
        self.assertIn("4097x10", next(iter(art.custom_refused.values())))
        near(self, self.wait(art, "Sol Ring").get_at((5, 5)), (200, 0, 0))     # Scryfall's picture instead

    def test_reload_picks_up_new_files(self):
        art = self.loader()
        near(self, self.wait(art, "Sol Ring").get_at((5, 5)), (200, 0, 0))
        png(os.path.join(self.art_dir, "Sol Ring.png"), (0, 200, 0))
        gen = art.custom_gen
        art.reload_custom()
        self.assertGreater(art.custom_gen, gen)
        near(self, art.get("Sol Ring").get_at((5, 5)), (0, 200, 0))

    def test_my_art_is_backed_up_but_never_published(self):
        # Karl, 2 Oct: back it up (local copy and GitHub, like my_decks); the public source copy still leaves it out
        import backup
        from tools import export_public
        self.assertNotIn("my_art", backup.TOP_LEVEL_SKIP)
        self.assertIn("my_art", export_public.TOP_LEVEL_SKIP)
        with open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            self.assertNotIn("my_art/", f.read().split())
        import paths
        self.assertEqual(os.path.basename(paths.my_art_dir()), "my_art")


# ---- 4. the table -----------------------------------------------------------------------------------------------------------------
class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = FileStore(self.tmp)
        self.store.add("Sol Ring", (200, 0, 0))
        self.store.add(ArtKey("Sol Ring", "c21", "263"), (0, 0, 200))
        self.store.add(ArtKey("Sol Ring", "lea", "270"), (0, 200, 0))
        st = copy.deepcopy(load_state("main1_lands"))
        self.me = st["me"]
        self.opp = [p["id"] for p in st["players"] if p["id"] != self.me][0]
        self.gui = ft.ForgeTable(FakeSession(st, None), self.store, settings_path=None, window_size=(1360, 840))
        self.gui.art.custom = {}
        self.gui.set_seat_printings([{"sol ring": ("c21", "263")}, {"sol ring": ("lea", "270")}])
        for key, colour in (("Sol Ring", (200, 0, 0)), (ArtKey("Sol Ring", "c21", "263"), (0, 0, 200)),
                            (ArtKey("Sol Ring", "lea", "270"), (0, 200, 0))):
            s = pygame.Surface(art_loader.SOURCE_SIZE)
            s.fill(colour)
            self.gui.art.imgs[key] = s

    def card(self, controller, **kw):
        c = {"id": 900 + controller, "name": "Sol Ring", "controller": controller, "zone": "Battlefield", "type": "Artifact"}
        c.update(kw)
        return c

    def test_each_seat_gets_its_own_decks_printing(self):
        self.assertEqual(self.gui.art_key(self.card(self.me)), ArtKey("Sol Ring", "c21", "263"))
        self.assertEqual(self.gui.art_key(self.card(self.opp)), ArtKey("Sol Ring", "lea", "270"))

    def test_two_sol_rings_two_surfaces_everywhere(self):
        mine, theirs = self.card(self.me), self.card(self.opp)
        k1, s1 = self.gui.card_surface_keyed(mine, 120, 168)
        k2, s2 = self.gui.card_surface_keyed(theirs, 120, 168)
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(s1.get_at((60, 60))[:3], s2.get_at((60, 60))[:3])
        f1, fs1 = self.gui.board_frame_surface(mine, 90, 70)            # the board frame's key must name the printing (it didn't)
        f2, fs2 = self.gui.board_frame_surface(theirs, 90, 70)
        self.assertEqual((f1[0], f2[0]), ("frame", "frame"))
        self.assertNotEqual(f1, f2)
        self.assertNotEqual(fs1.get_at((45, 50))[:3], fs2.get_at((45, 50))[:3])
        self.assertIsNot(self.gui.art.preview(self.gui.art_key(mine), 200, 280),
                         self.gui.art.preview(self.gui.art_key(theirs), 200, 280))

    def test_the_rotated_copy_follows(self):
        import gfx
        k1, _s = self.gui.card_surface_keyed(self.card(self.me), 120, 168)
        k2, _s = self.gui.card_surface_keyed(self.card(self.opp), 120, 168)
        self.assertNotEqual(("rot", k1), ("rot", k2))
        self.assertIn(ArtKey("Sol Ring", "c21", "263"), k1[-1])

    def test_a_stolen_card_shows_its_owners_art_when_the_owner_is_known(self):
        stolen = self.card(self.opp, owner=self.me)
        self.assertEqual(self.gui.art_key(stolen), ArtKey("Sol Ring", "c21", "263"))
        self.assertEqual(self.gui.art_key(self.card(self.opp)), ArtKey("Sol Ring", "lea", "270"))   # no owner: the controller's

    def test_tokens_and_decks_without_printings_get_the_default(self):
        self.assertEqual(self.gui.art_key(self.card(self.me, token=True)), "Sol Ring")
        self.gui.set_seat_printings([{}, {}])
        self.assertEqual(self.gui.art_key(self.card(self.me)), "Sol Ring")
        self.assertEqual(self.gui.card_surface_keyed(self.card(self.me), 120, 168)[0][-1], ("img", "Sol Ring", 120, 168))

    def test_the_vs_screen_shows_each_seats_commander_printing(self):
        import flow_screens as flow
        self.gui.set_seat_printings([{"kinnan, bonder prodigy": ("iko", "192")}, {}])
        self.assertEqual(flow.seat_art(self.gui, 0, "Kinnan, Bonder Prodigy"), ArtKey("Kinnan, Bonder Prodigy", "iko", "192"))
        self.assertEqual(flow.seat_art(self.gui, 1, "Kinnan, Bonder Prodigy"), "Kinnan, Bonder Prodigy")

    def test_a_double_faced_card_is_found_by_its_front_face(self):
        self.gui.set_seat_printings([{"esika, god of the tree // the prismatic bridge": ("khm", "168")}])
        self.assertEqual(self.gui.art_key({"name": "Esika, God of the Tree", "controller": self.me}),
                         ArtKey("Esika, God of the Tree", "khm", "168"))

    def test_your_own_picture_is_marked_and_has_no_copyright_band(self):
        art_dir = os.path.join(self.tmp, "my_art")
        os.makedirs(art_dir)
        png(os.path.join(art_dir, "Sol Ring.png"), (9, 9, 9))
        self.gui.art.custom_dir = art_dir
        self.gui.art.reload_custom()
        with mock.patch.object(self.gui, "_badges", wraps=self.gui._badges) as badges:
            key, _s = self.gui.card_surface_keyed(self.card(self.me, power=2, toughness=2), 120, 168)
        self.assertIn("custom", key[-1])
        self.assertFalse(badges.call_args[1]["real"])


class GameTests(unittest.TestCase):
    """The printings travel with the game: Start, Restart and the journal (for Resume)."""

    def test_start_restart_and_the_journal_carry_the_printings(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        deck = os.path.join(tmp, "d.txt")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("Commander\n1 Kinnan, Bonder Prodigy (IKO) 192\n\nDeck\n1 Sol Ring (C21) 263\n" + "98 Island\n")
        mine = lib.DeckEntry(deck, "d", False).load()

        class Launcher:
            runtime = None
            calls = []

            def start(self, my, opps):
                self.calls.append((my, opps))
                return FakeSession(load_state("main1_lands"), None)

        gui = ft.ForgeTable(FakeSession(None, None), None, settings_path=None, window_size=(1360, 840), launcher=Launcher())
        gui.journal = None
        self.assertIsNone(gui.start_game(mine, [mine], 1))
        self.assertEqual(gui.current_printings[0]["sol ring"], ("c21", "263"))
        self.assertEqual(gui.seat_printings[1]["kinnan, bonder prodigy"], ("iko", "192"))
        gui.set_seat_printings([])
        gui.restart_game()
        self.assertEqual(gui.seat_printings[0]["sol ring"], ("c21", "263"))
        j = gjournal.GameJournal(os.path.join(tmp, "saves"))
        if j is not None:
            j.start(1, "Karl", {"player.dck": ""}, "x", 0.0, printings=[{"sol ring": ["c21", "263"]}])
            j.close()
            with open(j.path, encoding="utf-8") as f:
                head = json.loads(f.readline())
            self.assertEqual(head["printings"], [{"sol ring": ["c21", "263"]}])


# ---- 5. the picker ----------------------------------------------------------------------------------------------------------------
class PickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "samples")
        os.makedirs(self.lib)
        os.makedirs(self.samples)
        self.text = ("Commander\n1 Kinnan, Bonder Prodigy (IKO) 192\n\nDeck\n1 Sol Ring *F* [Ramp]\n1x Ponder (M12) 73 [Draw]\n"
                     "1 Esika, God of the Tree / The Prismatic Bridge (KHM) 168\n96 Island\n")
        with open(os.path.join(self.lib, "mine.txt"), "w", encoding="utf-8") as f:
            f.write(self.text)
        with open(os.path.join(self.samples, "sample.txt"), "w", encoding="utf-8") as f:
            f.write(self.text)

    def entries(self):
        return {e.name: e.load() for e in lib.list_decks(self.lib, self.samples, self.tmp)}

    def test_a_pick_rewrites_only_that_line_and_keeps_quantity_foil_and_tags(self):
        e = self.entries()["mine"]
        self.assertEqual(lib.set_printing(e, "Sol Ring", ("c21", "263")), 1)
        with open(e.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertIn("1 Sol Ring (C21) 263 *F* [Ramp]", lines)
        self.assertEqual([l for l in lines if "Sol Ring" not in l], [l for l in self.text.splitlines() if "Sol Ring" not in l])
        self.assertEqual(e.printings["sol ring"], ("c21", "263"))
        lib.set_printing(e, "Island", ("one", "271"))
        self.assertIn("96 Island (ONE) 271", lines_of(e.path))
        lib.set_printing(e, "Esika, God of the Tree / The Prismatic Bridge", ("khm", "290"))
        self.assertIn("1 Esika, God of the Tree / The Prismatic Bridge (KHM) 290", lines_of(e.path))

    def test_default_removes_the_printing(self):
        e = self.entries()["mine"]
        lib.set_printing(e, "Ponder", None)
        self.assertIn("1x Ponder [Draw]", lines_of(e.path))
        self.assertNotIn("ponder", e.printings)

    def test_a_sample_deck_is_never_changed(self):
        e = [x for x in self.entries().values() if x.builtin][0]
        with self.assertRaises(deck_importer.DeckImportError):
            lib.set_printing(e, "Sol Ring", ("c21", "263"))

    def gui(self, size=(1360, 840), scale=1.0):
        from tests.test_deck_screen import FakeLauncher
        g = ft.ForgeTable(FakeSession(None, None), None, settings_path=None, window_size=size, launcher=FakeLauncher(),
                          deck_dirs=(self.lib, self.samples, self.tmp))
        g.text_scale = scale
        g.open_menu()
        g.render()
        return g

    def test_a_sample_deck_asks_to_copy_first_then_applies_the_pick_to_the_copy(self):
        g = self.gui()
        sample = [e for e in g.menu.entries if e.builtin][0]
        g.menu.assign(sample)
        g.menu.press(g, "card_art")
        picker = g.modal
        self.assertIsInstance(picker, art_picker.ArtPicker)
        picker.open_card("Sol Ring")
        picker.pick(g, ("c21", "263"))
        self.assertIsInstance(g.modal, fmenu.dlg.QuestionDialog)
        g.modal.answer(True)
        self.assertIs(g.modal, picker)
        self.assertFalse(picker.entry.builtin)
        self.assertEqual(picker.entry.printings["sol ring"], ("c21", "263"))
        with open(sample.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), self.text)                 # the sample itself is untouched

    def test_cancel_keeps_the_picker_and_changes_nothing(self):
        g = self.gui()
        sample = [e for e in g.menu.entries if e.builtin][0]
        g.menu.assign(sample)
        g.menu.press(g, "card_art")
        picker = g.modal
        picker.open_card("Sol Ring")
        picker.pick(g, ("c21", "263"))
        g.modal.answer(False)
        self.assertIs(g.modal, picker)
        self.assertEqual(os.listdir(self.lib), ["mine.txt"])

    def test_the_grid_lists_each_card_once_commander_first(self):
        e = self.entries()["mine"]
        names = art_picker.deck_cards(e)
        self.assertEqual(names[0], "Kinnan, Bonder Prodigy")
        self.assertEqual(names.count("Island"), 1)

    def test_fit_at_five_sizes(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0),
                            ((4096, 1949), 1.75)):
            with self.subTest(size=size, scale=scale):
                g = self.gui(size, scale)
                mine = [e for e in g.menu.entries if not e.builtin][0]
                g.menu.assign(mine)
                g.render()
                self.assertIn("card_art", [n for _r, n in g.menu.btns])
                for r, n in g.menu.btns:
                    if n in ("import", "remove", "card_art"):
                        self.assertTrue(g.screen.get_rect().contains(r), n)
                g.modal = art_picker.ArtPicker(g.menu, mine)
                for card in (None, "Sol Ring"):
                    if card:
                        g.modal.open_card(card)
                    g.render()
                    p = g.modal
                    self.assertTrue(g.screen.get_rect().contains(p.rect))
                    for r, _n in p.buttons:
                        self.assertTrue(p.rect.contains(r))
                    for r, _v in p.tiles:
                        self.assertTrue(p.area.contains(r) or r.w == 0 or r.h == 0)
                    self.assertTrue(p.rect.contains(p.area))


# ---- 7. live ------------------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(os.environ.get("MANTICORE_LIVE_NETWORK"), "talks to Scryfall: set MANTICORE_LIVE_NETWORK=1")
class LiveTests(unittest.TestCase):
    def test_sol_ring_and_swamp_have_many_printings(self):
        store = card_data.CardDataStore()
        self.assertGreater(len(store.list_printings("Sol Ring")), 1)
        self.assertGreater(len(store.list_printings("Swamp")), 1)


if __name__ == "__main__":
    unittest.main()
