# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 40 (4 Oct 2026): Karl exported a deck's card images from Proxxied as a ZIP Archive - "I don't want to apply to every deck
just the one I exported." Pictures are imported into ONE deck (deck_art.py): matched to its cards by file name, kept in deck_art/,
and written into that deck file only as "# art: <card> = image:<id>" lines.

The file names below copy the shape of Karl's real export (100 pictures, "001 - Light Up the Stage.jpg" ... "182 - Volcanic
Fissure.png", "101 - Default.png" the card back, "Scheming Silvertongue __ Sign in Blood", MDFC backs numbered above 100), but
the deck here is made up."""
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import art_loader
import art_picker
import card_data
import deck_art
import deck_library as lib
import file_chooser
import forge_table as ft
import mpc_art
import paths
from card_data import ArtKey
from tests.forge_fake import FakeSession, StubStore

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pygame.init()
pygame.display.set_mode((10, 10))

DFC = {"blightstep pathway": ["Blightstep Pathway", "Searstep Pathway"],
       "fell the profane": ["Fell the Profane", "Fell Mire"]}

DECK = ("# format: commander\n"
        "1 Agate Instigator\n1 Big Apple, 3 a.m.\n1 Blightstep Pathway\n1 Chandra's Incinerator\n1 Fell the Profane\n"
        "1 Scheming Silvertongue\n1 Spiked Corridor/Torture Pit\n1 Sol Ring (C21) 263\n1 Zidane Tribal\n6 Mountain\n\n"
        "1 Ingris Stingerquill\n")

MEMBERS = ["002 - Agate Instigator.png", "006 - Big Apple, 3 a.m..png", "012 - Blightstep Pathway.png",
           "018 - Chandra's Incinerator.jpg", "031 - Fell the Profane.jpg", "055 - Mountain.png",
           "070 - Scheming Silvertongue __ Sign in Blood.jpg", "078 - Sol Ring.png", "080 - Spiked Corridor __ Torture Pit.png",
           "099 - Ragavan, Nimble Pilferer.png", "100 - Ingris Stingerquill.png", "101 - Default.png",
           "109 - Searstep Pathway.png", "130 - Fell Mire.png"]

NAMES = ["Ingris Stingerquill", "Agate Instigator", "Big Apple, 3 a.m.", "Blightstep Pathway", "Chandra's Incinerator",
         "Fell the Profane", "Scheming Silvertongue", "Spiked Corridor/Torture Pit", "Sol Ring", "Zidane Tribal", "Mountain"]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def faces_of(name):
    return DFC.get(name.lower())


def png_bytes(w, h, colour=(200, 30, 30), border=None, palette=False, fmt=".png"):
    """A picture of w x h; `border` (a colour) paints the outer 0.12/2.72 of it, like a print file's bleed."""
    s = pygame.Surface((w, h))
    s.fill(border or colour)
    if border:
        dx, dy = round(w * mpc_art.CUT_X), round(h * mpc_art.CUT_Y)
        s.fill(colour, pygame.Rect(dx, dy, w - 2 * dx, h - 2 * dy))
    if palette:
        s = s.convert(8)
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "x" + fmt)
        pygame.image.save(s, p)
        with open(p, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(d, True)


def make_zip(path, members, size=(272, 370)):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for i, m in enumerate(members):
            fmt = ".jpg" if m.lower().endswith(".jpg") else ".png"
            z.writestr(m, png_bytes(*size, colour=(10 + i * 15 % 240, 40, 90), fmt=fmt))
        z.writestr("__MACOSX/._002 - Agate Instigator.png", b"junk")
        z.writestr("readme.txt", b"not a picture")


class FaceStore(StubStore):
    """StubStore with Scryfall-like card data for the double-faced cards."""

    def __init__(self):
        self.prefetched = []

    def peek_card(self, name):
        faces = DFC.get(name.strip().lower())
        if not faces:
            return {"name": name}
        return {"name": " // ".join(faces), "card_faces": [{"name": f, "image_uris": {"normal": "x"}} for f in faces]}

    def prefetch_cards(self, names):
        self.prefetched.append(list(names))
        return 0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.art_dir = os.path.join(self.tmp, "deck_art")
        p = mock.patch.object(card_data, "DECK_ART_DIR", card_data.Path(self.art_dir))
        p.start()
        self.addCleanup(p.stop)
        self.lib = os.path.join(self.tmp, "my_decks")
        os.makedirs(self.lib)
        os.makedirs(os.path.join(self.tmp, "samples"))
        self.deck_path = os.path.join(self.lib, "ingris.txt")
        with open(self.deck_path, "w", encoding="utf-8") as f:
            f.write(DECK)

    def entry(self):
        return lib.DeckEntry(self.deck_path, "Ingris", False, base_dir=self.tmp).load()


# ---- matching file names to cards -----------------------------------------------------------------------------------------------
class MatchTests(unittest.TestCase):
    def test_karls_export_shape_matches_by_name(self):
        plan = deck_art.plan_import(MEMBERS, NAMES, faces_of)
        self.assertEqual(plan.cards["Big Apple, 3 a.m."], "006 - Big Apple, 3 a.m..png")
        self.assertEqual(plan.cards["Chandra's Incinerator"], "018 - Chandra's Incinerator.jpg")
        self.assertEqual(plan.cards["Scheming Silvertongue"], "070 - Scheming Silvertongue __ Sign in Blood.jpg")
        self.assertEqual(plan.cards["Spiked Corridor/Torture Pit"], "080 - Spiked Corridor __ Torture Pit.png")
        self.assertEqual(plan.cards["Ingris Stingerquill"], "100 - Ingris Stingerquill.png")
        self.assertEqual(plan.cards["Mountain"], "055 - Mountain.png")
        self.assertEqual(plan.backs, {"Searstep Pathway": "109 - Searstep Pathway.png", "Fell Mire": "130 - Fell Mire.png"})
        self.assertEqual(plan.unmatched, ["099 - Ragavan, Nimble Pilferer.png"])
        self.assertEqual(plan.missing, ["Zidane Tribal"])
        self.assertEqual(plan.card_backs, ["101 - Default.png"])
        self.assertEqual(plan.count, 12)

    def test_without_card_data_a_back_face_is_only_listed(self):
        plan = deck_art.plan_import(MEMBERS, NAMES, None)
        self.assertEqual(plan.backs, {})
        self.assertIn("109 - Searstep Pathway.png", plan.unmatched)

    def test_card_data_is_asked_only_when_a_file_is_left_over(self):
        asked = []
        deck_art.plan_import(["Sol Ring.png"], ["Sol Ring"], lambda n: asked.append(n))
        self.assertEqual(asked, [])

    def test_other_name_shapes(self):
        cases = {"Sol Ring.jpg": "Sol Ring", "sol ring (1).png": "Sol Ring", "Sol Ring (C21) 263.png": "Sol Ring",
                 "Sol_Ring_front.png": "Sol Ring", "1. Sol Ring.png": "Sol Ring", "Lórien Revealed.png": "Lorien Revealed",
                 "Delver of Secrets __ Insectile Aberration.png": "Delver of Secrets // Insectile Aberration",
                 "Fire _ Ice.png": "Fire // Ice", "Chandra’s Incinerator.png": "Chandra's Incinerator",
                 "art/Sol Ring.png": "Sol Ring"}
        for member, card in cases.items():
            plan = deck_art.plan_import([member], [card, "Island"])
            self.assertEqual(plan.cards, {card: member}, member)

    def test_a_name_ending_in_back_is_still_a_card(self):
        plan = deck_art.plan_import(["Strike Back.png", "Sol Ring_back.png"], ["Strike Back", "Island"])
        self.assertEqual(plan.cards, {"Strike Back": "Strike Back.png"})
        self.assertEqual(plan.card_backs, ["Sol Ring_back.png"])

    def test_a_second_picture_of_the_same_card_is_counted_not_used(self):
        plan = deck_art.plan_import(["055 - Mountain.png", "056 - Mountain.png"], ["Mountain"])
        self.assertEqual(plan.cards, {"Mountain": "055 - Mountain.png"})
        self.assertEqual(plan.extra, [("056 - Mountain.png", "Mountain")])

    def test_a_number_at_the_start_of_a_real_name_still_matches(self):
        plan = deck_art.plan_import(["001 - 1996 World Champion.png", "1996 World Champion.png"], ["1996 World Champion"])
        self.assertEqual(plan.cards, {"1996 World Champion": "001 - 1996 World Champion.png"})

    def test_scryfall_faces_only_for_cards_with_two_pictures(self):
        self.assertEqual(deck_art.scryfall_faces({"card_faces": [{"name": "A", "image_uris": {}}, {"name": "B"}]}), [])
        self.assertEqual(deck_art.scryfall_faces({"card_faces": [{"name": "A", "image_uris": {"n": 1}},
                                                                 {"name": "B", "image_uris": {"n": 2}}]}), ["A", "B"])
        self.assertEqual(deck_art.scryfall_faces(None), [])


# ---- storing one picture ---------------------------------------------------------------------------------------------------------
class StoreTests(Base):
    def test_bleed_is_cut_off_and_big_pictures_scaled_to_1400(self):
        data = png_bytes(3264, 4440, colour=(250, 0, 0), border=(0, 0, 255))
        pid = deck_art.store_picture(data, "x.png", self.art_dir)
        img = pygame.image.load(deck_art.picture_path(pid, self.art_dir))
        self.assertEqual(img.get_height(), 1400)
        self.assertAlmostEqual(img.get_width() / img.get_height(), mpc_art.CARD_ASPECT, delta=0.005)
        for corner in ((2, 2), (img.get_width() - 3, img.get_height() - 3)):
            r, g, b = img.get_at(corner)[:3]
            self.assertGreater(r, 200, corner)                 # the card's own colour reaches the edge: no blue bleed left
            self.assertLess(b, 60, corner)

    def test_a_card_shaped_picture_is_kept_whole_and_not_enlarged(self):
        pid = deck_art.store_picture(png_bytes(745, 1040), "Swamp.png", self.art_dir)
        self.assertEqual(pygame.image.load(deck_art.picture_path(pid, self.art_dir)).get_size(), (745, 1040))

    def test_same_file_twice_is_one_picture_and_is_not_decoded_again(self):
        data = png_bytes(300, 418)
        pid = deck_art.store_picture(data, "a.png", self.art_dir)
        with mock.patch("pygame.image.load", side_effect=AssertionError("decoded twice")):
            self.assertEqual(deck_art.store_picture(data, "b.png", self.art_dir), pid)
        self.assertEqual(os.listdir(self.art_dir), [f"{pid}.jpg"])
        self.assertTrue(deck_art.valid_id(pid))

    def test_a_palette_png_and_a_jpeg_are_fine(self):
        deck_art.store_picture(png_bytes(272, 370, palette=True), "p.png", self.art_dir)
        deck_art.store_picture(png_bytes(272, 370, fmt=".jpg"), "j.jpg", self.art_dir)
        self.assertEqual(len(os.listdir(self.art_dir)), 2)

    def test_refused_before_decoding(self):
        import struct
        huge = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 20000, 30000) + b"\x08\x02\x00\x00\x00"
        with mock.patch("pygame.image.load", side_effect=AssertionError("decoded")):
            with self.assertRaisesRegex(ValueError, "too big"):
                deck_art.store_picture(huge, "huge.png", self.art_dir)
            with self.assertRaisesRegex(ValueError, "not a PNG or JPEG"):
                deck_art.store_picture(b"GIF89a....", "x.png", self.art_dir)

    def test_image_size_reads_png_and_jpeg_headers(self):
        self.assertEqual(deck_art.image_size(png_bytes(123, 456)), (123, 456))
        self.assertEqual(deck_art.image_size(png_bytes(123, 456, fmt=".jpg")), (123, 456))
        self.assertIsNone(deck_art.image_size(b"nothing"))


# ---- sources and the whole job ---------------------------------------------------------------------------------------------------
class JobTests(Base):
    def test_a_zip_is_imported_on_a_thread(self):
        z = os.path.join(self.tmp, "export.zip")
        make_zip(z, MEMBERS)
        job = deck_art.ImportJob(z, NAMES, faces_of, folder=self.art_dir)
        for _ in range(500):
            if job.finished:
                break
            time.sleep(0.01)
        self.assertTrue(job.finished)
        self.assertIsNone(job.error)
        self.assertEqual(set(job.pictures), set(job.plan.cards) | set(job.plan.backs))
        self.assertEqual(job.progress, (12, 12))
        for pid in job.pictures.values():
            self.assertTrue(os.path.isfile(deck_art.picture_path(pid, self.art_dir)))
        text = "\n".join(deck_art.summary(job, "Ingris"))
        self.assertIn("10 cards in 'Ingris' now use your pictures, and 2 back faces", text)
        self.assertIn("Not a card of this deck (1): 099 - Ragavan, Nimble Pilferer", text)
        self.assertIn("No picture for (1): Zidane Tribal", text)
        self.assertIn("Card back left out: 101 - Default", text)

    def test_a_folder_one_level_down_and_a_single_picture(self):
        d = os.path.join(self.tmp, "export")
        os.makedirs(os.path.join(d, "backs", "deeper"))
        for rel in ("Sol Ring.png", "backs/Searstep Pathway.png", "backs/deeper/Mountain.png", "notes.txt"):
            with open(os.path.join(d, rel), "wb") as f:
                f.write(png_bytes(60, 84) if rel.endswith(".png") else b"x")
        src = deck_art.open_source(d)
        self.assertEqual(sorted(src.members()), ["Sol Ring.png", "backs/Searstep Pathway.png"])
        one = deck_art.ImportJob(os.path.join(d, "Sol Ring.png"), NAMES, folder=self.art_dir, only_card="Agate Instigator",
                                 start=False).run_now()
        self.assertEqual(list(one.pictures), ["Agate Instigator"])
        self.assertIn("Agate Instigator now uses your picture in 'Ingris'", deck_art.summary(one, "Ingris")[0])

    def test_cancel_and_errors(self):
        z = os.path.join(self.tmp, "export.zip")
        make_zip(z, MEMBERS)
        job = deck_art.ImportJob(z, NAMES, faces_of, folder=self.art_dir, start=False)
        job.cancel()
        job.run_now()
        self.assertTrue(job.finished)
        self.assertEqual(job.pictures, {})
        bad = os.path.join(self.tmp, "bad.zip")
        with open(bad, "wb") as f:
            f.write(b"not a zip")
        self.assertIn("can't be opened as a .zip", deck_art.ImportJob(bad, NAMES, start=False).run_now().error)
        txt = os.path.join(self.tmp, "deck.txt")
        with open(txt, "w"):
            pass
        self.assertIn("isn't a .zip, a folder or a picture", deck_art.ImportJob(txt, NAMES, start=False).run_now().error)
        empty = os.path.join(self.tmp, "empty.zip")
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("readme.txt", b"x")
        self.assertIn("no pictures", deck_art.ImportJob(empty, NAMES, start=False).run_now().error)

    def test_a_picture_that_cant_be_read_is_listed(self):
        z = os.path.join(self.tmp, "export.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("Sol Ring.png", b"\x89PNG\r\n\x1a\nbroken")
            zf.writestr("Mountain.png", png_bytes(60, 84))
        job = deck_art.ImportJob(z, NAMES, folder=self.art_dir, start=False).run_now()
        self.assertEqual(list(job.pictures), ["Mountain"])
        self.assertEqual(job.failed[0][0], "Sol Ring.png")


# ---- the deck file ----------------------------------------------------------------------------------------------------------------
class DeckFileTests(Base):
    A, B, C = "0123456789abcdef", "fedcba9876543210", "00112233445566ff"

    def test_lines_go_into_this_deck_only_after_its_format_line(self):
        other = os.path.join(self.lib, "other.txt")
        with open(other, "w", encoding="utf-8") as f:
            f.write(DECK)
        e = self.entry()
        n = lib.set_imported_art(e, {"Sol Ring": self.A, "Searstep Pathway": self.B, "Blightstep Pathway": self.C})
        self.assertEqual(n, 3)
        lines = read(self.deck_path).splitlines()
        self.assertEqual(lines[:4], ["# format: commander", f"# art: Blightstep Pathway = image:{self.C}",
                                     f"# art: Searstep Pathway = image:{self.B}", f"# art: Sol Ring = image:{self.A}"])
        self.assertIn("1 Sol Ring (C21) 263", lines)                   # the card line stays as Moxfield wrote it
        self.assertEqual(read(other), DECK)
        self.assertEqual(e.printings["sol ring"], ("_img", self.A))
        self.assertEqual(e.printings["searstep pathway"], ("_img", self.B))     # a back face, not a card line of the deck
        self.assertEqual(lib.imported_art_count(e), 3)

    def test_a_new_import_replaces_mpc_and_older_lines_and_mpc_lines_of_other_cards_stay(self):
        e = self.entry()
        lib.set_mpc_art(e, "Sol Ring", "1" * 25)
        lib.set_mpc_art(e, "Mountain", "2" * 25)
        lib.set_imported_art(e, {"Sol Ring": self.A})
        lib.set_imported_art(e, {"Sol Ring": self.B})
        text = read(self.deck_path)
        self.assertEqual(text.count("# art: Sol Ring"), 1)
        self.assertIn(f"# art: Sol Ring = image:{self.B}", text)
        self.assertEqual(lib.mpc_art_lines(text), {"mountain": "2" * 25})
        self.assertEqual(lib.remove_imported_art(e), 1)
        self.assertEqual(lib.mpc_art_lines(read(self.deck_path)), {"mountain": "2" * 25})
        self.assertEqual(e.printings["sol ring"], ("c21", "263"))

    def test_a_printing_or_an_mpc_pick_replaces_an_imported_picture(self):
        e = self.entry()
        lib.set_imported_art(e, {"Sol Ring": self.A, "Mountain": self.B})
        lib.set_printing(e, "Sol Ring", ("cmr", "472"))
        lib.set_mpc_art(e, "Mountain", "3" * 25)
        text = read(self.deck_path)
        self.assertNotIn("image:", text)
        self.assertIn("1 Sol Ring (CMR) 472", text)
        lib.set_printing(e, "Mountain", ("_img", self.C))
        self.assertEqual(e.printings["mountain"], ("_img", self.C))
        self.assertNotIn("mpc:", read(self.deck_path))

    def test_undo_puts_the_file_back_and_samples_are_refused(self):
        e = self.entry()
        before = lib.read_text(e)
        lib.set_imported_art(e, {"Sol Ring": self.A})
        lib.restore_text(e, before)
        self.assertEqual(read(self.deck_path), DECK)
        self.assertEqual(e.printings["sol ring"], ("c21", "263"))
        sample = lib.DeckEntry(self.deck_path, "Sample", True, base_dir=self.tmp)
        for call in (lambda: lib.set_imported_art(sample, {"Sol Ring": self.A}), lambda: lib.remove_imported_art(sample),
                     lambda: lib.restore_text(sample, DECK)):
            with self.assertRaises(lib.DeckImportError):
                call()
        with self.assertRaises(lib.DeckImportError):
            lib.set_imported_art(e, {"Sol Ring": "../../etc"})

    def test_a_bad_id_in_the_file_is_ignored(self):
        with open(self.deck_path, "w", encoding="utf-8") as f:
            f.write("# art: Sol Ring = image:../secret\n" + DECK)
        self.assertEqual(self.entry().printings["sol ring"], ("c21", "263"))


# ---- pictures at the table ---------------------------------------------------------------------------------------------------------
class PictureTests(Base):
    def test_the_store_finds_an_imported_picture_and_falls_back_when_it_is_gone(self):
        pid = deck_art.store_picture(png_bytes(300, 418), "x.png", self.art_dir)
        key = card_data.art_key("Sol Ring", deck_art.IMG_SET, pid)
        self.assertTrue(deck_art.is_img(key))
        self.assertFalse(mpc_art.is_mpc(key))
        for size in ("normal", "large", "small", "medium"):
            self.assertEqual(str(card_data.CardDataStore._image_file(key, size)), deck_art.picture_path(pid, self.art_dir))
        store = card_data.CardDataStore.__new__(card_data.CardDataStore)
        import threading
        store._lock = threading.RLock()
        self.assertEqual(card_data.CardDataStore.get_image_path(store, key), deck_art.picture_path(pid, self.art_dir))
        self.assertIsNone(card_data.CardDataStore.get_image_path(store, key, "art_crop"))
        gone = card_data.art_key("Sol Ring", deck_art.IMG_SET, "ffffffffffffffff")
        with mock.patch.object(card_data.CardDataStore, "get_card", return_value=None) as get_card:
            self.assertIsNone(card_data.CardDataStore.get_image_path(store, gone))
        get_card.assert_called_with("Sol Ring")                       # the default printing, never a download of the id

    def test_no_art_crop_for_an_imported_picture(self):
        loader = art_loader.ArtLoader(StubStore())
        loader.custom = {}
        key = ArtKey("Sol Ring", "_img", "0123456789abcdef")
        with mock.patch.object(loader, "_request_bigger") as req:
            self.assertIsNone(loader.art_crop(key, 100, 80))
        req.assert_not_called()

    def test_the_table_maps_a_single_slash_split_card_both_ways(self):
        g = ft.ForgeTable(FakeSession(None, None), None, window_size=(800, 600))
        g.set_seat_printings([{"spiked corridor/torture pit": ("_img", "0123456789abcdef")}])
        m = g.seat_printings[0]
        for name in ("spiked corridor/torture pit", "spiked corridor // torture pit", "spiked corridor"):
            self.assertEqual(m[name], ("_img", "0123456789abcdef"), name)


# ---- the Card art window ----------------------------------------------------------------------------------------------------------
class WindowTests(Base):
    def gui(self, size=(1360, 840)):
        from tests.test_deck_screen import FakeLauncher
        g = ft.ForgeTable(FakeSession(None, None), None, window_size=size, launcher=FakeLauncher(),
                          deck_dirs=(self.lib, os.path.join(self.tmp, "samples"), self.tmp))
        g.art = art_loader.ArtLoader(FaceStore())
        g.art.custom = {}
        g.open_menu()
        g.render()
        e = [x for x in g.menu.entries if not x.builtin][0]
        g.menu.assign(e)
        g.menu.press(g, "card_art")
        g.render()
        return g, g.modal

    def finish(self, g, p):
        for _ in range(500):
            g.render()
            if p.job is None:
                return
            time.sleep(0.01)
        self.fail("the import never finished")

    def test_drop_a_zip_then_ok(self):
        z = os.path.join(self.tmp, "export.zip")
        make_zip(z, MEMBERS)
        g, p = self.gui()
        self.assertIn("import", [n for _r, n in p.buttons])
        self.assertNotIn("remove_imported", [n for _r, n in p.buttons])
        p.drop_file(g, z)
        self.assertIsNotNone(p.job)
        self.finish(g, p)
        report = g.modal
        self.assertIsInstance(report, art_picker.ImportReport)
        self.assertTrue(report.applied)
        self.assertIn("10 cards in 'ingris' now use your pictures", report.lines[0])
        g.render()
        self.assertIn("undo", [n for _r, n in report.buttons])
        report.key(g, pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r"))
        self.assertIs(g.modal, p)
        self.assertFalse(p.done)
        text = read(self.deck_path)
        self.assertEqual(text.count("= image:"), 12)
        self.assertEqual(p.entry.printings["searstep pathway"][0], "_img")
        g.render()
        self.assertIn("remove_imported", [n for _r, n in p.buttons])

    def test_undo_and_remove(self):
        z = os.path.join(self.tmp, "export.zip")
        make_zip(z, MEMBERS)
        g, p = self.gui()
        p.drop_file(g, z)
        self.finish(g, p)
        g.render()
        g.modal.click(g, [r for r, n in g.modal.buttons if n == "undo"][0].center, 1)
        self.assertIs(g.modal, p)
        self.assertEqual(read(self.deck_path), DECK)
        p.drop_file(g, z)
        self.finish(g, p)
        g.modal.back(g)
        g.render()
        p.click(g, [r for r, n in p.buttons if n == "remove_imported"][0].center, 1)
        q = g.modal
        q.answer(True)
        self.assertIs(g.modal, p)
        self.assertNotIn("image:", read(self.deck_path))

    def test_a_picture_dropped_on_a_cards_page_is_that_card(self):
        pic = os.path.join(self.tmp, "whatever I called it.png")
        with open(pic, "wb") as f:
            f.write(png_bytes(272, 370))
        g, p = self.gui()
        p.open_card("Zidane Tribal")
        p.drop_file(g, pic)
        self.finish(g, p)
        self.assertEqual(g.modal.lines[0], "Zidane Tribal now uses your picture in 'ingris'. Other decks are not changed.")
        self.assertEqual(p.entry.printings["zidane tribal"][0], "_img")

    def test_esc_stops_an_import_first_and_close_too(self):
        g, p = self.gui()
        p.job = deck_art.ImportJob("x.zip", [], start=False)
        p.key(g, pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode="\x1b"))
        self.assertTrue(p.job.cancelled)
        self.assertFalse(p.done)
        p.click(g, (0, 0), 1)
        g.render()
        p.click(g, [r for r, n in p.buttons if n == "close"][0].center, 1)
        self.assertFalse(p.done)
        p.job.finished = True
        g.render()
        self.assertIsNone(p.job)
        self.assertIn("Import stopped", p.message[0])
        self.assertEqual(read(self.deck_path), DECK)

    def test_the_open_window_runs_on_its_own_thread(self):
        z = os.path.join(self.tmp, "export.zip")
        make_zip(z, ["Sol Ring.png"])
        g, p = self.gui()
        seen = {}

        def opener(title, patterns, start_dir):
            seen["thread"] = __import__("threading").current_thread().name
            seen["patterns"] = patterns
            return z
        with mock.patch.object(file_chooser, "tk_open", opener):
            p.click(g, [r for r, n in p.buttons if n == "import"][0].center, 1)
            self.assertIn("Open window", p.message[0])
            p.chooser.wait(2)
        self.assertEqual(seen["thread"], "file-chooser")
        self.assertIn("*.zip", seen["patterns"][0][1])
        self.finish(g, p)
        self.assertIsInstance(g.modal, art_picker.ImportReport)

    def test_no_open_window_says_drag_instead_and_nothing_chosen_is_quiet(self):
        g, p = self.gui()
        p.chooser = file_chooser.FileChooser("t", [], opener=lambda *a: (_ for _ in ()).throw(ImportError("no tkinter")))
        p.chooser.wait(2)
        g.render()
        self.assertIn("Drag the .zip onto this window", p.message[0])
        p.chooser = file_chooser.FileChooser("t", [], opener=lambda *a: "")
        p.chooser.wait(2)
        g.render()
        self.assertEqual(p.message[0], "No file chosen.")
        self.assertIsNone(p.job)

    def test_a_sample_deck_is_copied_first(self):
        samples = os.path.join(self.tmp, "samples")
        shutil.move(self.deck_path, os.path.join(samples, "ingris.txt"))
        from tests.test_deck_screen import FakeLauncher
        g = ft.ForgeTable(FakeSession(None, None), None, window_size=(1360, 840), launcher=FakeLauncher(),
                          deck_dirs=(self.lib, samples, self.tmp))
        g.art = art_loader.ArtLoader(FaceStore())
        g.art.custom = {}
        g.open_menu()
        g.render()
        e = [x for x in g.menu.entries if x.builtin and x.path.endswith("ingris.txt")][0]
        g.menu.assign(e)
        g.menu.press(g, "card_art")
        p = g.modal
        z = os.path.join(self.tmp, "export.zip")
        make_zip(z, ["Sol Ring.png"])
        p.drop_file(g, z)
        q = g.modal
        self.assertIsNot(q, p)
        q.answer(True)
        self.assertIs(g.modal, p)
        self.assertFalse(p.entry.builtin)
        self.finish(g, p)
        self.assertIn("image:", read(p.entry.path))
        self.assertNotIn("image:", read(os.path.join(samples, "ingris.txt")))

    def test_the_deck_screen_points_pictures_to_card_art(self):
        g, p = self.gui()
        g.modal = None
        with mock.patch("builtins.open", side_effect=AssertionError("read")):
            g.menu.drop_file(g, os.path.join(self.tmp, "export.zip"))
        self.assertIn("press Card art", g.menu.message[0])


# ---- where the pictures live -----------------------------------------------------------------------------------------------------
class PlacesTests(unittest.TestCase):
    def test_deck_art_is_user_data_kept_out_of_git_and_the_public_copy_but_in_the_local_zips(self):
        self.assertEqual(os.path.dirname(paths.deck_art_dir()), os.path.dirname(paths.my_art_dir()))
        self.assertEqual(os.path.basename(paths.deck_art_dir()), "deck_art")
        with open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("deck_art/", f.read().splitlines())
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import export_public
        import backup
        self.assertIn("deck_art", export_public.TOP_LEVEL_SKIP)
        self.assertNotIn("deck_art", backup.TOP_LEVEL_SKIP)

    def test_ensure_dirs_makes_it(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"MANTICORE_USER_DIR": d}):
            paths.ensure_dirs()
            self.assertTrue(os.path.isdir(paths.deck_art_dir()))


if __name__ == "__main__":
    unittest.main()
