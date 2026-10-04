# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ALT2: MPC Autofill pictures in the Card art picker - one card's renders as thumbnails, one picked, only that one
downloaded (bleed cut off), kept per deck as a "# art: <card> = mpc:<id>" line. Offline: every request goes to a fake
session; the live class talks to mpcfill.com only with MANTICORE_LIVE_NETWORK=1."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
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
import mpc_art
from card_data import ArtKey
from tests.forge_fake import FakeSession, load_state

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pygame.init()
pygame.display.set_mode((10, 10))

ID1 = "1EFuP_3jzbIYkgtKNO7rJ-56OjHU-Hzc5"
ID2 = "1wyuhzmJyZkkTbId2Z_4QAt2EKVVt9zG7"
ID3 = "1ASidbRvyhKRMt42QSz_4AWsZqy5CAB3j"


def picture_bytes(size, colour=(10, 120, 40), fmt="png", edge=None):
    """An encoded picture; with `edge`, a 1/8-inch-bleed-sized border of that colour round the card."""
    s = pygame.Surface(size)
    s.fill(edge or colour)
    if edge:
        box = mpc_art.bleed_box(*size)
        s.fill(colour, pygame.Rect(box))
    buf = io.BytesIO()
    pygame.image.save(s, buf, f"x.{fmt}")
    return buf.getvalue()


def card_json(drive_id, name="Bitterblossom (MOR 58)", source="Chilli_Axe", dpi=800, q="bitterblossom", **kw):
    c = {"identifier": drive_id, "name": name, "sourceName": source, "dpi": dpi, "size": 3000000, "tags": ["Frame"],
         "extension": "jpg", "language": "EN", "searchq": q, "sourceType": "Google Drive"}
    c.update(kw)
    return c


class Response:
    def __init__(self, status=200, data=None, content=b"", content_type="application/json"):
        self.status_code = status
        self._data = data
        self.content = content
        self.headers = {"Content-Type": content_type}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class MpcSession:
    """Answers like mpcfill.com and Google Drive; records every request."""

    def __init__(self, cards=None, ids=None, v2=True, picture=None, fail=False):
        self.calls = []
        self.headers = {}
        self.cards = cards if cards is not None else {ID1: card_json(ID1), ID2: card_json(ID2)}
        self.ids = ids if ids is not None else list(self.cards)
        self.v2, self.picture, self.fail = v2, picture, fail

    def get(self, url, timeout=None, headers=None):
        self.calls.append(("GET", url, None))
        if self.fail:
            raise ConnectionError("offline")
        if url.endswith("/2/sources/"):
            return Response(200, {"results": {"1": {"pk": 1, "name": "Chilli_Axe", "sourceType": "Google Drive"},
                                              "2": {"pk": 2, "name": "JohnPrime", "sourceType": "Google Drive"}}})
        if url.startswith("https://drive.google.com/thumbnail"):
            if self.picture is None:
                return Response(404, None, b"", "text/html")
            return Response(200, None, self.picture, "image/png")
        return Response(404)

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        if self.fail:
            raise ConnectionError("offline")
        if url.endswith("/2/editorSearch/"):
            if not self.v2:
                return Response(404, {"name": "Not found", "message": "gone"})
            q = json["queries"][0]["query"]
            return Response(200, {"results": {q: {"CARD": list(self.ids)}}})
        if url.endswith("/3/editorSearch/"):
            return Response(200, {"results": {"q": list(self.ids)}})
        if url.endswith("/2/cards/"):
            return Response(200, {"results": {i: self.cards[i] for i in json["cardIdentifiers"] if i in self.cards}})
        return Response(404)

    def posts(self, path):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(path)]


class Clock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def client(tmp, session, clock=None):
    clock = clock or Clock()
    return mpc_art.MpcClient(tmp, session=session, backend="https://mpc.test", clock=clock, sleep=clock.sleep)


# ---- 1. the pieces --------------------------------------------------------------------------------------------------------------
class PiecesTests(unittest.TestCase):
    def test_the_bleed_is_found_by_shape_and_cut_off(self):
        self.assertEqual(mpc_art.bleed_box(816, 1110), (36, 36, 744, 1038))         # 300 dpi: 1/8 inch is 36 px... rounded
        x, y, w, h = mpc_art.bleed_box(1029, 1400)                                    # Google's w1400 thumbnail
        self.assertAlmostEqual(w / h, 2.48 / 3.46, places=2)
        self.assertIsNone(mpc_art.bleed_box(488, 680))                               # already card-shaped (Scryfall's)
        self.assertIsNone(mpc_art.bleed_box(500, 500))
        self.assertIsNone(mpc_art.bleed_box(0, 10))

    def test_names_are_compared_as_mpc_autofill_does(self):
        self.assertEqual(mpc_art.searchable("Lathril, Blade of the Elves"), "lathril blade of the elves")
        self.assertEqual(mpc_art.searchable("Bitterblossom (MOR 58) [Frame]"), "bitterblossom")
        self.assertTrue(mpc_art.same_card("lathril blade of elves", "Lathril, Blade of the Elves"))
        self.assertFalse(mpc_art.same_card("island sanctuary", "Island"))
        self.assertEqual(mpc_art.query_for("Delver of Secrets // Insectile Aberration"), "Delver of Secrets")
        self.assertEqual(mpc_art.query_for("Esika, God of the Tree / The Prismatic Bridge"), "Esika, God of the Tree")

    def test_ids_differing_only_by_case_get_different_files(self):
        a, b = "1AbCdEfGhIjKlMnOp", "1abcdefghijklmnop"
        self.assertNotEqual(mpc_art.file_part(a), mpc_art.file_part(b))
        ka, kb = card_data.art_key("Sol Ring", mpc_art.MPC_SET, a), card_data.art_key("Sol Ring", mpc_art.MPC_SET, b)
        self.assertEqual(ka, ArtKey("Sol Ring", "_mpc", a))                          # the id keeps its case in the key
        self.assertNotEqual(card_data.CardDataStore._image_file(ka).name.lower(),
                            card_data.CardDataStore._image_file(kb).name.lower())
        self.assertNotEqual(card_data.CardDataStore._image_file(ka), card_data.CardDataStore._image_file(ArtKey("Sol Ring", "c21", "263")))

    def test_what_counts_as_an_mpc_picture(self):
        self.assertTrue(mpc_art.is_mpc(ArtKey("Sol Ring", "_mpc", ID1)))
        self.assertFalse(mpc_art.is_mpc(ArtKey("Sol Ring", "c21", "263")))
        self.assertFalse(mpc_art.is_mpc("Sol Ring"))
        self.assertTrue(mpc_art.is_mpc_printing(("_mpc", ID1)))
        self.assertFalse(mpc_art.is_mpc_printing(("mpc", ID1)))
        self.assertFalse(mpc_art.valid_id("../../etc"))
        self.assertFalse(mpc_art.valid_id("short"))
        self.assertTrue(mpc_art.valid_id(ID1))

    def test_requests_name_the_program(self):
        ua = mpc_art.user_agent()
        self.assertTrue(ua.startswith("Manticore/"), ua)
        self.assertIn("github.com/Eliminate7532/Manticore", ua)


# ---- 2. the client --------------------------------------------------------------------------------------------------------------
class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_nothing_is_contacted_until_a_search(self):
        c = mpc_art.MpcClient(self.tmp)
        self.assertIsNone(c._session)
        self.assertIsNone(c.peek("Bitterblossom"))
        self.assertIsNone(c._session)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "mpc")))

    def test_a_search_asks_every_source_then_the_details_and_keeps_the_order(self):
        s = MpcSession(ids=[ID2, ID1])
        got = client(self.tmp, s).search("Bitterblossom")
        self.assertEqual([c["id"] for c in got], [ID2, ID1])
        self.assertEqual(got[0]["source"], "Chilli_Axe")
        self.assertEqual(got[0]["dpi"], 800)
        body = s.posts("/2/editorSearch/")[0][2]
        self.assertEqual(body["queries"], [{"query": "Bitterblossom", "cardType": "CARD"}])
        self.assertEqual(body["searchSettings"]["sourceSettings"]["sources"], [[1, True], [2, True]])
        self.assertEqual(s.posts("/2/cards/")[0][2], {"cardIdentifiers": [ID2, ID1]})

    def test_a_double_faced_card_is_searched_by_its_front_face(self):
        s = MpcSession(cards={ID1: card_json(ID1, "Delver of Secrets", q="delver of secrets")})
        client(self.tmp, s).search("Delver of Secrets // Insectile Aberration")
        self.assertEqual(s.posts("/2/editorSearch/")[0][2]["queries"][0]["query"], "Delver of Secrets")

    def test_other_cards_with_the_same_words_are_left_out(self):
        s = MpcSession(cards={ID1: card_json(ID1, "Island", q="island"), ID2: card_json(ID2, "Island Sanctuary", q="island sanctuary")})
        self.assertEqual([c["id"] for c in client(self.tmp, s).search("Island")], [ID1])

    def test_results_are_kept_a_week_on_disk(self):
        s = MpcSession()
        c = client(self.tmp, s)
        first = c.search("Bitterblossom")
        n = len(s.calls)
        self.assertEqual(c.search("bitterblossom"), first)                              # the same client: no request
        self.assertEqual(len(s.calls), n)
        s2 = MpcSession()
        again = client(self.tmp, s2)                                                    # a new run of the program
        self.assertEqual(again.peek("Bitterblossom"), first)
        self.assertEqual(again.card(ID1)["source"], "Chilli_Axe")
        self.assertEqual(s2.calls, [])
        with mock.patch("mpc_art.time.time", return_value=time.time() + mpc_art.SEARCH_TTL + 60):
            self.assertIsNone(again.peek("Bitterblossom"))
            again.search("Bitterblossom")
        self.assertTrue(s2.posts("/2/editorSearch/"))

    def test_requests_to_mpc_autofill_are_spaced_out(self):
        clock = Clock()
        client(self.tmp, MpcSession(), clock).search("Bitterblossom")                 # sources, search, details: 3 requests
        self.assertEqual(len(clock.slept), 2)
        self.assertTrue(all(abs(x - mpc_art.MIN_INTERVAL) < 1e-9 for x in clock.slept), clock.slept)

    def test_the_newer_search_is_used_when_the_old_one_is_gone(self):
        s = MpcSession(v2=False)
        got = client(self.tmp, s).search("Bitterblossom")
        self.assertEqual(len(got), 2)
        body = s.posts("/3/editorSearch/")[0][2]
        self.assertEqual(body["queries"], {"q": {"query": "Bitterblossom", "cardType": "CARD"}})

    def test_offline_is_none_with_a_reason_and_nothing_kept(self):
        c = client(self.tmp, MpcSession(fail=True))
        self.assertIsNone(c.search("Bitterblossom"))
        self.assertIn("can't be reached", c.last_error)
        self.assertIsNone(c.peek("Bitterblossom"))

    def test_no_pictures_is_an_empty_list(self):
        s = MpcSession(cards={}, ids=[])
        self.assertEqual(client(self.tmp, s).search("Nonexistent Card"), [])
        self.assertEqual(s.posts("/2/cards/"), [])

    def test_a_picture_is_saved_without_its_bleed(self):
        s = MpcSession(picture=picture_bytes((816, 1110), (10, 120, 40), edge=(250, 250, 250)))
        c = client(self.tmp, s)
        dest = os.path.join(self.tmp, "x", "pic.jpg")
        self.assertTrue(c.fetch_image(ID1, dest, "normal"))
        img = pygame.image.load(dest)
        self.assertEqual(img.get_size(), (744, 1038))
        for corner in ((2, 2), (741, 2), (2, 1035), (741, 1035)):                     # no white border left
            self.assertLess(max(img.get_at(corner)[:3]), 200, corner)
        self.assertIn("sz=w1000-h1000", s.calls[-1][1])
        self.assertIn(f"id={ID1}", s.calls[-1][1])
        self.assertFalse([f for f in os.listdir(os.path.join(self.tmp, "x")) if "tmp" in f])

    def test_a_card_shaped_picture_is_kept_whole(self):
        s = MpcSession(picture=picture_bytes((488, 680)))
        dest = os.path.join(self.tmp, "pic.jpg")
        self.assertTrue(client(self.tmp, s).fetch_image(ID1, dest, "small"))
        self.assertEqual(pygame.image.load(dest).get_size(), (488, 680))
        self.assertIn("sz=w400-h400", s.calls[-1][1])

    def test_a_missing_or_unreadable_picture_is_false(self):
        c = client(self.tmp, MpcSession(picture=None))
        self.assertFalse(c.fetch_image(ID1, os.path.join(self.tmp, "a.jpg")))
        self.assertIn("HTTP 404", c.last_error)
        s = MpcSession(picture=b"not a picture")
        self.assertFalse(client(self.tmp, s).fetch_image(ID1, os.path.join(self.tmp, "b.jpg")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "b.jpg")))
        self.assertFalse(client(self.tmp, s).fetch_image("../evil", os.path.join(self.tmp, "c.jpg")))

    def test_a_page_instead_of_a_picture_is_false(self):
        s = MpcSession(picture=b"<html></html>")
        s.get = lambda url, timeout=None, headers=None: Response(200, None, b"<html></html>", "text/html; charset=utf-8")
        c = client(self.tmp, s)
        self.assertFalse(c.fetch_image(ID1, os.path.join(self.tmp, "a.jpg")))
        self.assertIn("sent a page", c.last_error)


# ---- 3. the card store -------------------------------------------------------------------------------------------------------------
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        base = Path(self.tmp)
        for attr, sub in (("IMAGE_CACHE_DIR", "images"), ("IMAGE_CACHE_DIR_LARGE", "images_large"),
                          ("IMAGE_CACHE_DIR_ART", "images_art"), ("IMAGE_CACHE_DIR_SMALL", "images_small"),
                          ("DATA_CACHE_FILE", "card_data.json"), ("CACHE_DIR", ""), ("MPC_KEPT_DIR", "mpc_art")):
            p = mock.patch.object(card_data, attr, base / sub if sub else base)
            p.start()
            self.addCleanup(p.stop)
        with mock.patch("time.sleep"):
            self.store = card_data.CardDataStore()
        self.session = MpcSession(picture=picture_bytes((816, 1110)))
        self.store._mpc = client(self.tmp, self.session)
        self.key = ArtKey("Bitterblossom", "_mpc", ID1)

    def test_the_client_lives_in_the_cache_folder(self):
        self.store._mpc = None
        self.assertEqual(self.store.mpc.dir, Path(self.tmp) / "mpc")

    def test_an_mpc_key_downloads_its_own_picture_at_each_size(self):
        """Patch 38: the table's picture ("normal" and "large") is ONE 1400 px picture, kept in mpc_art/; the picker's tiles
        ("small" 400 px, "medium" 800 px) are cache files."""
        kept = self.store.get_image_path(self.key, "normal")
        self.assertEqual(Path(kept).parent, Path(self.tmp) / "mpc_art")
        self.assertIn("sz=w1400-h1400", self.session.calls[-1][1])
        n = len(self.session.calls)
        self.assertEqual(self.store.get_image_path(self.key, "large"), kept)            # the same picture: no second download
        self.assertEqual(self.store.peek_image_path(self.key, "normal"), kept)
        self.assertEqual(self.store.kept_mpc_picture(self.key), kept)
        self.assertEqual(len(self.session.calls), n)
        for size, px in (("small", 400), ("medium", 800)):
            path = self.store.get_image_path(self.key, size)
            self.assertEqual(Path(path).parent, Path(self.tmp) / "images_small")
            self.assertIn(f"sz=w{px}-h{px}", self.session.calls[-1][1])
            self.assertEqual(self.store.peek_image_path(self.key, size), path)
        self.assertEqual(len({self.store.get_image_path(self.key, s) for s in ("small", "medium", "large")}), 3)

    def test_the_kept_picture_survives_the_cache_being_cleared(self):
        kept = self.store.get_image_path(self.key, "normal")
        for sub in ("images", "images_large", "images_small", "mpc"):
            shutil.rmtree(os.path.join(self.tmp, sub), True)
        self.session.fail = True                                                          # and offline
        self.assertEqual(self.store.get_image_path(self.key, "normal"), kept)
        self.assertEqual(self.store.get_image_path(self.key, "large"), kept)

    def test_a_picture_round_alt2_cached_is_moved_without_downloading(self):
        name = card_data.safe_part("Bitterblossom") + "__" + mpc_art.file_part(ID1) + ".jpg"
        old = Path(self.tmp) / "images_large" / name
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(picture_bytes((939, 1310), fmt="jpg"))
        got = self.store.get_image_path(self.key, "normal")
        self.assertEqual(Path(got), Path(self.tmp) / "mpc_art" / name)
        self.assertEqual(Path(got).read_bytes(), old.read_bytes())
        self.assertEqual(self.session.calls, [])

    def test_there_is_no_art_crop_of_an_mpc_picture(self):
        self.assertIsNone(self.store.get_image_path(self.key, "art_crop"))
        self.assertEqual(self.session.calls, [])

    def test_a_missing_picture_falls_back_to_the_default_at_the_table_but_not_in_the_picker(self):
        self.session.picture = None
        with mock.patch.object(self.store, "get_card", return_value={"name": "Bitterblossom",
                                                                    "image_uris": {"normal": "https://img/default.jpg"}}), \
                mock.patch.object(self.store, "_download", return_value=True) as dl:
            got = self.store.get_image_path(self.key, "normal")
        self.assertEqual(got, str(card_data.CardDataStore._image_file("Bitterblossom", "normal")))
        self.assertEqual(dl.call_args[0][0], "https://img/default.jpg")
        self.assertIsNone(self.store.mpc_image_path(self.key, "small"))
        self.assertIn("No MPC Autofill picture", self.store.last_error)

    def test_search_and_peek_go_through_the_store(self):
        self.assertIsNone(self.store.peek_mpc("Bitterblossom"))
        self.assertEqual(len(self.store.mpc_search("Bitterblossom")), 2)
        self.assertEqual(len(self.store.peek_mpc("Bitterblossom")), 2)
        self.assertEqual(self.store.mpc_card(ID2)["id"], ID2)
        self.store._mpc = client(self.tmp + "x", MpcSession(fail=True))
        self.assertIsNone(self.store.mpc_search("Sol Ring"))
        self.assertIn("can't be reached", self.store.last_error)


# ---- 4. the deck file ---------------------------------------------------------------------------------------------------------------
class DeckFileTests(unittest.TestCase):
    TEXT = ("# format: brawl\nCommander\n1 Kinnan, Bonder Prodigy (IKO) 192\n\nDeck\n1 Sol Ring (C21) 263 *F* [Ramp]\n"
            "1x Ponder (M12) 73 [Draw]\n1 Bitterblossom\n57 Island\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "samples")
        os.makedirs(self.lib)
        os.makedirs(self.samples)
        for folder in (self.lib, self.samples):
            with open(os.path.join(folder, "mine.txt" if folder == self.lib else "sample.txt"), "w", encoding="utf-8") as f:
                f.write(self.TEXT)

    def entries(self):
        return {e.name: e.load() for e in lib.list_decks(self.lib, self.samples, self.tmp)}

    def lines(self, e):
        with open(e.path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_an_mpc_picture_is_one_comment_line_and_the_card_lines_stay_as_moxfield_wrote_them(self):
        e = self.entries()["mine"]
        self.assertTrue(lib.set_mpc_art(e, "Sol Ring", ID1))
        lines = self.lines(e)
        self.assertEqual(lines[:2], ["# format: brawl", f"# art: Sol Ring = mpc:{ID1}"])
        self.assertEqual([ln for ln in lines if not ln.startswith("# art:")], self.TEXT.splitlines())
        self.assertEqual(e.printings["sol ring"], ("_mpc", ID1))
        self.assertEqual(e.printings["ponder"], ("m12", "73"))
        self.assertEqual(e.format, "brawl")
        with open(e.path, encoding="utf-8") as f:
            self.assertEqual(deck_importer.import_from_text(f.read()), deck_importer.import_from_text(self.TEXT))

    def test_a_second_pick_replaces_the_first(self):
        e = self.entries()["mine"]
        lib.set_mpc_art(e, "Sol Ring", ID1)
        lib.set_printing(e, "Sol Ring", ("_mpc", ID2))                                  # the picker's route
        self.assertEqual([ln for ln in self.lines(e) if ln.startswith("# art:")], [f"# art: Sol Ring = mpc:{ID2}"])
        self.assertEqual(e.printings["sol ring"], ("_mpc", ID2))
        self.assertFalse(lib.set_mpc_art(e, "Sol Ring", ID2))                          # the same again: nothing to write

    def test_a_printing_or_the_default_takes_the_mpc_picture_out(self):
        e = self.entries()["mine"]
        lib.set_mpc_art(e, "Sol Ring", ID1)
        lib.set_printing(e, "Sol Ring", ("c21", "263"))                                # the same printing the line names
        self.assertFalse([ln for ln in self.lines(e) if ln.startswith("# art:")])
        self.assertEqual(e.printings["sol ring"], ("c21", "263"))
        lib.set_mpc_art(e, "Bitterblossom", ID3)
        lib.set_printing(e, "Bitterblossom", None)
        self.assertNotIn("bitterblossom", e.printings)
        self.assertEqual(self.lines(e), self.TEXT.splitlines())
        lib.set_mpc_art(e, "Ponder", ID1)
        lib.set_mpc_art(e, "Ponder", None)
        self.assertEqual(e.printings["ponder"], ("m12", "73"))

    def test_a_sample_deck_and_a_bad_id_are_refused(self):
        sample = [x for x in self.entries().values() if x.builtin][0]
        with self.assertRaises(deck_importer.DeckImportError):
            lib.set_mpc_art(sample, "Sol Ring", ID1)
        with self.assertRaises(deck_importer.DeckImportError):
            lib.set_mpc_art(self.entries()["mine"], "Sol Ring", "bad id with spaces")

    def test_lines_for_other_cards_or_with_bad_ids_are_ignored(self):
        e = self.entries()["mine"]
        with open(e.path, "w", encoding="utf-8") as f:
            f.write(f"# art: Black Lotus = mpc:{ID1}\n# ART : sol ring = MPC: {ID2}\n# art: Ponder = mpc:../x\n" + self.TEXT)
        e._mtime = None
        e.load()
        self.assertNotIn("black lotus", e.printings)
        self.assertEqual(e.printings["sol ring"], ("_mpc", ID2))
        self.assertEqual(e.printings["ponder"], ("m12", "73"))
        self.assertTrue(e.ok)

    def test_a_copy_of_a_deck_keeps_its_pictures(self):
        e = self.entries()["mine"]
        lib.set_mpc_art(e, "Sol Ring", ID1)
        new = lib.copy_to_library(e, self.lib, self.tmp).load()
        self.assertEqual(new.printings["sol ring"], ("_mpc", ID1))


# ---- 5. the art loader -------------------------------------------------------------------------------------------------------------
class MpcStore:
    """A stand-in for CardDataStore: MPC searches from a dict, pictures as coloured files, never the network."""
    last_error = None

    def __init__(self, folder, results=None, fail=False, delay=0.0):
        self.folder = folder
        self.results = results if results is not None else {
            "sol ring": [{"id": ID1, "name": "Sol Ring", "source": "Chilli_Axe", "dpi": 800, "tags": ["Frame"]},
                         {"id": ID2, "name": "Sol Ring (Borderless)", "source": "JohnPrime", "dpi": 1200, "tags": []}]}
        self.fail, self.delay = fail, delay
        self.kept = {}
        self.searches, self.image_calls, self.get_calls = [], [], []
        self.files = {}
        self.missing = set()
        self.threads = set()

    def prefetch_cards(self, names):
        return 0

    def peek_card(self, name):
        return None

    def peek_printings(self, name):
        return []

    def peek_mpc(self, name):
        return self.kept.get(mpc_art.query_for(name).lower())

    def mpc_search(self, name):
        self.searches.append(name)
        time.sleep(self.delay)
        if self.fail:
            self.last_error = "MPC Autofill can't be reached (ConnectionError)"
            return None
        got = self.results.get(name.lower(), [])
        self.kept[name.lower()] = got
        return got

    def mpc_card(self, drive_id):
        for cards in self.kept.values():
            for c in cards:
                if c["id"] == drive_id:
                    return c
        return None

    def _make(self, key, size):
        p = os.path.join(self.folder, f"{size}_{abs(hash(key))}.png")
        s = pygame.Surface((60, 84))
        s.fill((0, 0, 200) if mpc_art.is_mpc(key) else (200, 0, 0))
        pygame.image.save(s, p)
        self.files[(key, size)] = p
        return p

    def mpc_image_path(self, key, size="normal"):
        self.image_calls.append((key, size))
        self.threads.add(threading.current_thread().name)
        if key.collector_no in self.missing:
            return None
        return self.files.get((key, size)) or self._make(key, size)

    def peek_image_path(self, key, size="normal"):
        return self.files.get((key, size))

    def get_image_path(self, key, size="normal"):
        self.get_calls.append((key, size))
        if mpc_art.is_mpc(key):
            return self.mpc_image_path(key, size)
        return self.files.get((key, size)) or self._make(key, size)


def wait_for(test, fn, art=None, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if art is not None:
            art.collect(50)
        got = fn()
        if got:
            return got
        time.sleep(0.01)
    test.fail("timed out")


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_search_runs_in_the_background_once(self):
        store = MpcStore(self.tmp, delay=0.05)
        art = art_loader.ArtLoader(store)
        self.assertIsNone(art.mpc_results("Sol Ring"))
        self.assertIsNone(art.mpc_results("Sol Ring"))
        got = wait_for(self, lambda: art.mpc_results("Sol Ring"), art)
        self.assertEqual([c["id"] for c in got], [ID1, ID2])
        self.assertEqual(store.searches, ["Sol Ring"])

    def test_a_failed_search_says_why_and_can_be_tried_again(self):
        store = MpcStore(self.tmp, fail=True)
        art = art_loader.ArtLoader(store)
        art.mpc_results("Sol Ring")
        wait_for(self, lambda: art.mpc_error("Sol Ring"), art)
        self.assertEqual(art.mpc_results("Sol Ring"), [])
        self.assertIn("can't be reached", art.mpc_error("Sol Ring"))
        self.assertEqual(len(store.searches), 1)
        store.fail = False
        art.retry_mpc("Sol Ring")
        self.assertIsNone(art.mpc_error("Sol Ring"))
        self.assertIsNone(art.mpc_results("Sol Ring"))
        self.assertEqual(len(wait_for(self, lambda: art.mpc_results("Sol Ring"), art)), 2)

    def test_thumbnails_come_from_their_own_threads_newest_first(self):
        store = MpcStore(self.tmp)
        art = art_loader.ArtLoader(store)
        key = ArtKey("Sol Ring", "_mpc", ID1)
        self.assertIsNone(art.small(key, 50, 70))
        surf = wait_for(self, lambda: art.small(key, 50, 70), art)
        self.assertEqual(surf.get_size(), (50, 70))
        self.assertEqual(store.image_calls, [(key, "small")])
        self.assertEqual(store.get_calls, [])                                           # not through the locked store
        self.assertTrue(all(n.startswith("mpc-art-") for n in store.threads), store.threads)
        with art._mpc_cv:                                                               # the stack is last in, first out
            art._mpc_stack[:] = []
        self.assertLessEqual(len(art._mpc_threads), art_loader.MPC_THREADS)

    def test_a_tile_whose_picture_cant_be_had_isnt_asked_for_every_frame(self):
        store = MpcStore(self.tmp)
        store.missing.add(ID2)
        art = art_loader.ArtLoader(store)
        key = ArtKey("Sol Ring", "_mpc", ID2)
        art.small(key, 50, 70)
        wait_for(self, lambda: art.small_unavailable(key), art)
        for _ in range(5):
            self.assertIsNone(art.small(key, 50, 70))
        time.sleep(0.05)
        self.assertEqual(store.image_calls, [(key, "small")])

    def test_an_mpc_picture_has_no_art_crop_and_the_preview_uses_it(self):
        store = MpcStore(self.tmp)
        art = art_loader.ArtLoader(store)
        key = ArtKey("Sol Ring", "_mpc", ID1)
        self.assertIsNone(art.art_crop(key, 90, 70))
        self.assertIsNone(art.art_crop(key, 90, 70))
        img = wait_for(self, lambda: art.get(key), art)
        self.assertEqual(img.get_at((5, 5))[:3], (0, 0, 200))
        self.assertNotIn((key, "art_crop"), store.image_calls)

    def test_without_mpc_support_the_tab_is_just_empty(self):
        class Old:
            last_error = None

            def prefetch_cards(self, names):
                return 0
        art = art_loader.ArtLoader(Old())
        self.assertEqual(art.mpc_results("Sol Ring"), [])


# ---- 6. the picker ----------------------------------------------------------------------------------------------------------------
class PickerTests(unittest.TestCase):
    TEXT = ("Commander\n1 Kinnan, Bonder Prodigy (IKO) 192\n\nDeck\n1 Sol Ring (C21) 263 *F* [Ramp]\n1x Ponder (M12) 73 [Draw]\n"
            "97 Island\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "samples")
        os.makedirs(self.lib)
        os.makedirs(self.samples)
        for folder, name in ((self.lib, "mine.txt"), (self.samples, "sample.txt")):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                f.write(self.TEXT)

    def gui(self, size=(1360, 840), scale=1.0, store=None):
        from tests.test_deck_screen import FakeLauncher
        g = ft.ForgeTable(FakeSession(None, None), None, settings_path=None, window_size=size, launcher=FakeLauncher(),
                          deck_dirs=(self.lib, self.samples, self.tmp))
        g.text_scale = scale
        if store is not None:
            g.art = art_loader.ArtLoader(store)
            g.art.custom = {}
        g.open_menu()
        g.render()
        return g

    def picker(self, g, builtin=False):
        e = [x for x in g.menu.entries if x.builtin == builtin][0]
        g.menu.assign(e)
        g.menu.press(g, "card_art")
        self.assertIsInstance(g.modal, art_picker.ArtPicker)
        return g.modal

    def settle(self, g, p):
        for _ in range(100):
            g.art.collect(50)
            g.render()
            if p.card and p.source == "mpc" and g.art.mpc_results(p.card) is not None and \
                    all(g.art.small(card_data.art_key(p.card, *v), 1, 1) is not None for _r, v in p.tiles):
                return
            time.sleep(0.01)

    def test_the_printings_tab_never_searches_mpc_autofill(self):
        store = MpcStore(self.tmp)
        g = self.gui(store=store)
        p = self.picker(g)
        p.open_card("Sol Ring")
        self.assertEqual(p.source, "scryfall")
        for _ in range(5):
            g.render()
        time.sleep(0.05)
        self.assertEqual(store.searches, [])
        self.assertIn("src_mpc", [n for _r, n in p.buttons])

    def test_pick_an_mpc_picture(self):
        store = MpcStore(self.tmp)
        g = self.gui(store=store)
        p = self.picker(g)
        p.open_card("Sol Ring")
        g.render()
        r = [r for r, n in p.buttons if n == "src_mpc"][0]
        p.click(g, r.center, 1)
        self.assertEqual(p.source, "mpc")
        self.settle(g, p)
        self.assertEqual([v for _r, v in p.tiles], [("_mpc", ID1), ("_mpc", ID2)])
        tile = p.tiles[1][0]
        g.mouse = tile.center
        g.render()
        self.assertIn("JohnPrime", p.hover_info)
        self.assertIn("1200 DPI", p.hover_info)
        p.click(g, tile.center, 1)
        self.assertIsNone(p.card)                                                        # back to the grid
        self.assertIn("MPC Autofill (by JohnPrime)", p.message[0])
        self.assertEqual(p.entry.printings["sol ring"], ("_mpc", ID2))
        with open(p.entry.path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(f"# art: Sol Ring = mpc:{ID2}", text)
        self.assertIn("1 Sol Ring (C21) 263 *F* [Ramp]", text)
        self.assertIn(ArtKey("Sol Ring", "_mpc", ID2), g.art._requested)               # the table's picture is fetched
        p.open_card("Sol Ring")
        self.assertEqual(p.source, "mpc")                                                # opens on the tab of the choice
        g.mouse = (0, 0)
        g.render()
        p.click(g, [r for r, n in p.buttons if n == "src_scryfall"][0].center, 1)
        g.render()
        default = [r for r, v in p.tiles if v is None][0]
        p.click(g, default.center, 1)
        self.assertNotIn("sol ring", p.entry.printings)
        with open(p.entry.path, encoding="utf-8") as f:
            self.assertNotIn("# art:", f.read())

    def test_the_grid_describes_an_mpc_choice(self):
        store = MpcStore(self.tmp)
        g = self.gui(store=store)
        p = self.picker(g)
        store.mpc_search("Sol Ring")
        lib.set_mpc_art(p.entry, "Sol Ring", ID1)
        g.render()
        tile = [r for r, v in p.tiles if v == "Sol Ring"][0]
        g.mouse = tile.center
        g.render()
        self.assertEqual(p.hover_info, "Sol Ring  -  MPC Autofill (by Chilli_Axe)")

    def test_a_sample_deck_asks_to_copy_first(self):
        store = MpcStore(self.tmp)
        g = self.gui(store=store)
        p = self.picker(g, builtin=True)
        p.open_card("Sol Ring")
        p.pick(g, ("_mpc", ID1))
        self.assertIsInstance(g.modal, fmenu.dlg.QuestionDialog)
        g.modal.answer(True)
        self.assertIs(g.modal, p)
        self.assertFalse(p.entry.builtin)
        self.assertEqual(p.entry.printings["sol ring"], ("_mpc", ID1))
        with open(os.path.join(self.samples, "sample.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), self.TEXT)

    def test_offline_says_so_and_try_again_searches_again(self):
        store = MpcStore(self.tmp, fail=True)
        g = self.gui(store=store)
        p = self.picker(g)
        p.open_card("Sol Ring")
        p.source = "mpc"
        for _ in range(100):
            g.art.collect(50)
            g.render()
            if "retry_mpc" in [n for _r, n in p.buttons]:
                break
            time.sleep(0.01)
        r = [r for r, n in p.buttons if n == "retry_mpc"][0]
        store.fail = False
        p.click(g, r.center, 1)
        self.settle(g, p)
        self.assertEqual(len(store.searches), 2)
        self.assertEqual(len(p.tiles), 2)

    def test_fit_at_five_sizes(self):
        many = {"sol ring": [{"id": f"1{'x' * 20}{i:04d}", "name": "Sol Ring", "source": f"Maker number {i}", "dpi": 800,
                              "tags": []} for i in range(60)]}
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0),
                            ((4096, 1949), 1.75)):
            for fail in (False, True):
                with self.subTest(size=size, scale=scale, fail=fail):
                    store = MpcStore(self.tmp, results=many, fail=fail)
                    g = self.gui(size, scale, store=store)
                    p = self.picker(g)
                    p.open_card("Sol Ring")
                    p.source = "mpc"
                    for _ in range(100):
                        g.art.collect(50)
                        g.render()
                        if (p.tiles and not fail) or (fail and "retry_mpc" in [n for _r, n in p.buttons]):
                            break
                        time.sleep(0.01)
                    self.assertTrue(g.screen.get_rect().contains(p.rect))
                    for r, n in p.buttons:
                        self.assertTrue(p.rect.contains(r), n)
                    for r, _v in p.tiles:
                        self.assertTrue(p.area.contains(r) or r.w == 0 or r.h == 0)
                    self.assertTrue(p.rect.contains(p.area))
                    tabs = sorted((r for r, n in p.buttons if n.startswith("src_")), key=lambda r: r.x)
                    self.assertEqual(len(tabs), 2)
                    self.assertLessEqual(tabs[0].right, tabs[1].x)
                    if fail:
                        retry = [r for r, n in p.buttons if n == "retry_mpc"][0]
                        self.assertTrue(p.area.contains(retry))
                    else:
                        p.wheel(g, -1000)
                        g.render()
                        self.assertGreater(p.scroll, 0)


# ---- 7. at the table ---------------------------------------------------------------------------------------------------------------
class TableTests(unittest.TestCase):
    def test_a_seats_mpc_picture_is_its_picture_key_and_travels_in_the_journal(self):
        st = load_state("main1_lands")
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        gui = ft.ForgeTable(FakeSession(st, None), MpcStore(tmp), settings_path=None, window_size=(1360, 840))
        gui.set_seat_printings([{"sol ring": ("_mpc", ID1)}, {"sol ring": ("c21", "263")}])
        me = st["me"]
        opp = [p["id"] for p in st["players"] if p["id"] != me][0]
        self.assertEqual(gui.art_key({"name": "Sol Ring", "controller": me}), ArtKey("Sol Ring", "_mpc", ID1))
        self.assertEqual(gui.art_key({"name": "Sol Ring", "controller": opp}), ArtKey("Sol Ring", "c21", "263"))
        self.assertIn(ArtKey("sol ring", "_mpc", ID1), gui.printing_keys())         # fetched with the rest (ALT1 keys them
                                                                                        # by the lower-case name; same file)
        j = gjournal.GameJournal(os.path.join(tmp, "saves"))
        j.start(1, "Karl", {"player.dck": ""}, "x", 0.0, printings=[{"sol ring": ["_mpc", ID1]}])
        j.close()
        with open(j.path, encoding="utf-8") as f:
            head = json.loads(f.readline())
        gui.set_seat_printings(head["printings"])
        self.assertEqual(gui.art_key({"name": "Sol Ring", "controller": me}), ArtKey("Sol Ring", "_mpc", ID1))


# ---- 8. credits --------------------------------------------------------------------------------------------------------------------
class CreditTests(unittest.TestCase):
    def test_mpc_autofill_is_credited_and_the_notices_file_is_current(self):
        with open(os.path.join(HERE, "licenses", "NOTICES.json"), encoding="utf-8") as f:
            credits = {c["id"]: c for c in json.load(f)["credits"]}
        text = credits["mpc-autofill"]["text"]
        self.assertIn("mpcfill.com", text)
        self.assertIn("belongs to the person who made it", text)
        with open(os.path.join(HERE, "THIRD_PARTY_NOTICES.txt"), encoding="utf-8") as f:
            self.assertIn("mpcfill.com", f.read())


# ---- 9. live ------------------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(os.environ.get("MANTICORE_LIVE_NETWORK"), "talks to mpcfill.com and Google Drive: set MANTICORE_LIVE_NETWORK=1")
class LiveTests(unittest.TestCase):
    def test_bitterblossom_has_renders_and_one_downloads_without_its_bleed(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        c = mpc_art.MpcClient(tmp)
        got = c.search("Bitterblossom")
        self.assertIsNotNone(got, c.last_error)
        self.assertGreater(len(got), 10)
        dest = os.path.join(tmp, "b.jpg")
        self.assertTrue(c.fetch_image(got[0]["id"], dest, "small"), c.last_error)
        w, h = pygame.image.load(dest).get_size()
        self.assertAlmostEqual(w / h, 2.48 / 3.46, delta=0.01)


if __name__ == "__main__":
    unittest.main()
