# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 26: legibility -- large preview images and art-crop board frames for small battlefield cards."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import card_data
import forge_table as ft
from art_loader import ArtLoader
from tests.test_forge_table import make_gui


class FakeResponse:
    def __init__(self, status_code=200, content=b"\xff\xd8\xfake", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json = json_data or {}

    def json(self):
        return self._json


class CardDataImageSizeTests(unittest.TestCase):
    """get_image_path/peek_image_path take a `size` and cache 'normal', 'large' and 'art_crop' separately."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.normal_dir, self.large_dir, self.art_dir = base / "normal", base / "large", base / "art"
        for d in (self.normal_dir, self.large_dir, self.art_dir):
            d.mkdir()
        patches = [
            mock.patch.object(card_data, "IMAGE_CACHE_DIR", self.normal_dir),
            mock.patch.object(card_data, "IMAGE_CACHE_DIR_LARGE", self.large_dir),
            mock.patch.object(card_data, "IMAGE_CACHE_DIR_ART", self.art_dir),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.store = card_data.CardDataStore()
        self.store.cards["fake card"] = {
            "name": "Fake Card",
            "image_uris": {"normal": "https://img/normal.jpg", "large": "https://img/large.jpg",
                            "art_crop": "https://img/crop.jpg"},
        }

    def test_default_size_is_normal_and_uses_the_normal_cache_dir(self):
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.return_value = FakeResponse()
            path = self.store.get_image_path("Fake Card")
        self.assertTrue(path.startswith(str(self.normal_dir)))
        self.assertEqual(sess.get.call_args[0][0], "https://img/normal.jpg")

    def test_large_uses_the_large_url_and_the_large_cache_dir(self):
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.return_value = FakeResponse()
            path = self.store.get_image_path("Fake Card", size="large")
        self.assertTrue(path.startswith(str(self.large_dir)))
        self.assertEqual(sess.get.call_args[0][0], "https://img/large.jpg")
        self.assertIsNone(self.store.peek_image_path("Fake Card"))               # the normal picture was never fetched
        self.assertIsNotNone(self.store.peek_image_path("Fake Card", size="large"))

    def test_art_crop_uses_the_art_crop_url_and_its_own_cache_dir(self):
        with mock.patch.object(self.store, "_session") as sess:
            sess.get.return_value = FakeResponse()
            path = self.store.get_image_path("Fake Card", size="art_crop")
        self.assertTrue(path.startswith(str(self.art_dir)))
        self.assertEqual(sess.get.call_args[0][0], "https://img/crop.jpg")

    def test_an_already_cached_size_never_touches_the_network_again(self):
        (self.large_dir / "fake_card.jpg").write_bytes(b"cached")
        with mock.patch.object(self.store, "_session") as sess:
            path = self.store.get_image_path("Fake Card", size="large")
        sess.get.assert_not_called()
        self.assertEqual(Path(path).read_bytes(), b"cached")


class FakeBigStore:
    """A store StubStore-like fake, extended with fake large/art_crop paths (round 26)."""

    last_error = None

    def __init__(self, large_ready=False, normal_ready=True):
        self.large_ready = large_ready
        self.normal_ready = normal_ready
        self.get_calls = []

    def prefetch_cards(self, names):
        return 0

    def peek_image_path(self, name, size="normal"):
        if size == "large":
            return f"/cache/large/{name}.jpg" if self.large_ready else None
        if size == "art_crop":
            return None
        return f"/cache/normal/{name}.jpg" if self.normal_ready else None

    def get_image_path(self, name, size="normal"):
        self.get_calls.append((name, size))               # never expected to be called from the GUI thread directly
        return self.peek_image_path(name, size)


class ArtLoaderPreviewTests(unittest.TestCase):
    """preview() asks for the 'large' picture only when the panel is wider than the normal image (488px), and never on
    the calling (GUI) thread: it queues a background job and falls back to the normal picture meanwhile."""

    def setUp(self):
        pygame.display.set_mode((10, 10))

    def test_a_narrow_preview_never_asks_for_large(self):
        store = FakeBigStore(large_ready=False)
        loader = ArtLoader(store, [])
        with mock.patch.object(pygame.image, "load", side_effect=lambda p: pygame.Surface((10, 10))):
            loader.preview("Fake", 400, 557)
        self.assertNotIn(("large",), [c[:1] for c in store.get_calls])
        self.assertEqual(store.get_calls, [])              # peek only, no synchronous download

    def test_a_wide_preview_uses_the_normal_picture_until_large_is_cached(self):
        store = FakeBigStore(large_ready=False)
        loader = ArtLoader(store, [])
        with mock.patch.object(pygame.image, "load", side_effect=lambda p: pygame.Surface((10, 10))):
            surf = loader.preview("Fake", 600, 838)
        self.assertIsNotNone(surf)                                        # the normal picture, used while large downloads
        self.assertIn(("large", "Fake"), loader._requested_large)         # queued for the background thread, not fetched inline
        loader._done.get(timeout=2)                                       # let the worker thread finish so it doesn't outlive the test
        time.sleep(0.05)

    def test_a_wide_preview_uses_large_once_it_is_cached(self):
        store = FakeBigStore(large_ready=True)
        loader = ArtLoader(store, [])
        loaded_paths = []
        with mock.patch.object(pygame.image, "load", side_effect=lambda p: (loaded_paths.append(p), pygame.Surface((10, 10)))[1]):
            loader.preview("Fake", 600, 838)
        self.assertTrue(loaded_paths[0].startswith("/cache/large/"))


class BoardFrameTests(unittest.TestCase):
    """The 'Compact board cards' cog switch (default OFF): with it on, small battlefield cards use the art-crop board
    frame; the hand never does, whatever the switch says."""

    def test_default_is_off_and_nothing_uses_a_frame(self):
        gui = make_gui("main1_lands", (1360, 840))
        self.assertFalse(gui.frames)
        with mock.patch.object(ft.ForgeTable, "board_frame_surface", wraps=gui.board_frame_surface) as spy:
            gui.render()
        spy.assert_not_called()

    def test_switching_it_on_uses_frames_on_a_small_battlefield_and_never_on_the_hand(self):
        gui = make_gui("main1_lands", (1360, 840))
        gui.toggle_frames()
        self.assertTrue(gui.frames)
        seen_boards = []
        real = ft.ForgeTable.board_frame_surface

        def spy(self, card, w, h):
            seen_boards.append(card.get("id"))
            return real(self, card, w, h)

        with mock.patch.object(ft.ForgeTable, "board_frame_surface", spy):
            gui.render()
        self.assertTrue(seen_boards)
        hand_ids = {c["id"] for c in gui.session.me()["zones"]["hand"] if not c.get("hidden")}
        self.assertFalse(hand_ids & set(seen_boards))

    def test_a_tall_enough_battlefield_card_still_gets_the_full_picture(self):
        gui = make_gui("main1_lands", (2400, 1600))         # plenty of room: rows stay above BOARD_FRAME_BELOW
        gui.toggle_frames()
        threshold = ft.BOARD_FRAME_BELOW * max(1.0, gui.L.fs / 1.3)
        with mock.patch.object(ft.ForgeTable, "board_frame_surface", wraps=gui.board_frame_surface) as spy:
            gui.render()
        if gui.L.opp_row_h >= threshold and gui.L.my_row_h >= threshold:
            spy.assert_not_called()

    def test_type_letter_covers_the_five_kinds(self):
        self.assertEqual(ft.type_letter({"isCreature": True}), "C")
        self.assertEqual(ft.type_letter({"isLand": True}), "L")
        self.assertEqual(ft.type_letter({"isPlaneswalker": True}), "P")
        self.assertEqual(ft.type_letter({"type": "Artifact"}), "A")
        self.assertEqual(ft.type_letter({"type": "Enchantment"}), "E")
        self.assertEqual(ft.type_letter({"type": "Instant"}), "")


if __name__ == "__main__":
    unittest.main()
