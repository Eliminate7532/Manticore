# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 38 (4 Oct 2026), from Karl's first look at the MPC Autofill tab on his Surface:
1. "add a way to scale these card images up and down on this page": a card size (- / + in the Card art window, the - and + keys,
   Ctrl+wheel), remembered in settings.json; bigger tiles get bigger pictures, not a stretched thumbnail.
2. "make sure card arts are saved": a picked MPC Autofill picture is kept in mpc_art/ (not the card cache) at 1400 px, downloaded
   when it is picked; mpc_art/ is in the local backup zips, not in git, never in the public source copy.
3. "remove the kinnan deck from the alpha and add the Goreclaw deck": sample_decks/ has Karl's Goreclaw list (We Just Need to Punch
   Them) and no longer the Kinnan list, which is a test deck now (tests/fixtures/decks/)."""
import json
import os
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

import art_loader
import art_picker
import card_data
import deck_library as lib
import forge_table as ft
import paths
from card_data import ArtKey
from tests.forge_fake import FakeSession
from tests.test_round_alt2 import ID1, ID2, MpcStore, wait_for

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pygame.init()
pygame.display.set_mode((10, 10))

MANY = {"sol ring": [{"id": f"1{'x' * 20}{i:04d}", "name": "Sol Ring", "source": f"Maker {i}", "dpi": 800, "tags": []}
                     for i in range(40)]}


class Base(unittest.TestCase):
    TEXT = "Commander\n1 Kinnan, Bonder Prodigy (IKO) 192\n\nDeck\n1 Sol Ring (C21) 263\n1 Ponder (M12) 73\n97 Island\n"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = os.path.join(self.tmp, "my_decks")
        os.makedirs(self.lib)
        os.makedirs(os.path.join(self.tmp, "samples"))
        with open(os.path.join(self.lib, "mine.txt"), "w", encoding="utf-8") as f:
            f.write(self.TEXT)

    def gui(self, size=(1360, 840), scale=1.0, store=None, settings=None):
        from tests.test_deck_screen import FakeLauncher
        g = ft.ForgeTable(FakeSession(None, None), None, settings_path=settings, window_size=size, launcher=FakeLauncher(),
                          deck_dirs=(self.lib, os.path.join(self.tmp, "samples"), self.tmp))
        g.text_scale = scale
        if store is not None:
            g.art = art_loader.ArtLoader(store)
            g.art.custom = {}
        g.open_menu()
        g.render()
        e = [x for x in g.menu.entries if not x.builtin][0]
        g.menu.assign(e)
        g.menu.press(g, "card_art")
        return g, g.modal

    def settle(self, g, p, until):
        for _ in range(150):
            if g.art is not None:
                g.art.collect(60)
            g.render()
            if until():
                return
            time.sleep(0.01)


# ---- 1. the card size ---------------------------------------------------------------------------------------------------------------
class CardSizeTests(Base):
    def tile_w(self, g, p):
        g.render()
        return p.tiles[0][0].w if p.tiles else None

    def test_plus_and_minus_buttons_change_the_tiles(self):
        g, p = self.gui()
        g.render()
        w1 = self.tile_w(g, p)
        names = [n for _r, n in p.buttons]
        self.assertIn("zoom_in", names)
        self.assertIn("zoom_out", names)
        p.click(g, [r for r, n in p.buttons if n == "zoom_in"][0].center, 1)
        self.assertEqual(g.art_picker_zoom, 1.25)
        self.assertGreater(self.tile_w(g, p), w1)
        for _ in range(3):
            p.click(g, [r for r, n in p.buttons if n == "zoom_out"][0].center, 1)
        self.assertEqual(g.art_picker_zoom, 0.6)
        self.assertLess(self.tile_w(g, p), w1)

    def test_the_ends_disable_their_button(self):
        g, p = self.gui()
        g.art_picker_zoom = art_picker.ZOOM_STEPS[-1]
        g.render()
        self.assertNotIn("zoom_in", [n for _r, n in p.buttons])
        g.art_picker_zoom = art_picker.ZOOM_STEPS[0]
        g.render()
        self.assertNotIn("zoom_out", [n for _r, n in p.buttons])
        p.change_zoom(g, -1)
        self.assertEqual(g.art_picker_zoom, art_picker.ZOOM_STEPS[0])

    def test_keys_and_ctrl_wheel(self):
        g, p = self.gui()
        p.key(g, pygame.event.Event(pygame.KEYDOWN, key=pygame.K_EQUALS, mod=0, unicode="="))
        self.assertEqual(g.art_picker_zoom, 1.25)
        p.key(g, pygame.event.Event(pygame.KEYDOWN, key=pygame.K_KP_MINUS, mod=0, unicode="-"))
        p.key(g, pygame.event.Event(pygame.KEYDOWN, key=pygame.K_MINUS, mod=0, unicode="-"))
        self.assertEqual(g.art_picker_zoom, 0.75)
        with mock.patch("pygame.key.get_mods", return_value=pygame.KMOD_LCTRL):
            p.wheel(g, 1)
        self.assertEqual(g.art_picker_zoom, 1.0)
        with mock.patch("pygame.key.get_mods", return_value=0):
            p.wheel(g, -1)                                         # a plain wheel still scrolls
        self.assertEqual(g.art_picker_zoom, 1.0)
        self.assertFalse(p.done)

    def test_the_size_is_remembered(self):
        path = os.path.join(self.tmp, "settings.json")
        g, p = self.gui(settings=path)
        p.change_zoom(g, 2)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["art_picker_zoom"], 1.5)
        g2, p2 = self.gui(settings=path)
        self.assertEqual(g2.art_picker_zoom, 1.5)
        self.assertEqual(p2.zoom(g2), 1.5)

    def test_a_broken_setting_is_ignored(self):
        path = os.path.join(self.tmp, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"art_picker_zoom": "huge"}, f)
        g, p = self.gui(settings=path)
        self.assertEqual(p.zoom(g), 1.0)

    def test_every_size_fits_at_five_window_sizes(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0),
                            ((4096, 1949), 1.75)):
            for zoom in (art_picker.ZOOM_STEPS[0], 1.0, art_picker.ZOOM_STEPS[-1]):
                for mpc in (False, True):
                    with self.subTest(size=size, scale=scale, zoom=zoom, mpc=mpc):
                        g, p = self.gui(size, scale, store=MpcStore(self.tmp, results=MANY))
                        g.art_picker_zoom = zoom
                        if mpc:
                            p.open_card("Sol Ring")
                            p.source = "mpc"
                        self.settle(g, p, lambda: bool(p.tiles))
                        self.assertTrue(p.tiles)
                        self.assertTrue(g.screen.get_rect().contains(p.rect))
                        for r, n in p.buttons:
                            self.assertTrue(p.rect.contains(r), n)
                        heads = [r for r, n in p.buttons if n in ("zoom_in", "zoom_out", "close", "back", "reload")]
                        for i, a in enumerate(heads):                        # the header's buttons never overlap
                            for b in heads[i + 1:]:
                                self.assertFalse(a.colliderect(b), (a, b))
                        tile = p.tiles[0][0]
                        self.assertLessEqual(tile.w, p.area.w)
                        full_h = int(tile.w / art_picker.CARD_ASPECT)
                        self.assertLessEqual(full_h, p.area.h)             # one whole card always fits
                        for r, _v in p.tiles:
                            self.assertTrue(p.area.contains(r) or r.w == 0 or r.h == 0)


class SharpTilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_tall_mpc_tile_gets_the_800_px_thumbnail(self):
        store = MpcStore(self.tmp)
        art = art_loader.ArtLoader(store)
        key = ArtKey("Sol Ring", "_mpc", ID1)
        wait_for(self, lambda: art.small(key, 300, 418), art)
        self.assertEqual(store.image_calls, [(key, "medium")])
        wait_for(self, lambda: art.small(key, 100, 140), art)
        self.assertEqual(store.image_calls, [(key, "medium"), (key, "small")])

    def test_a_tall_scryfall_tile_uses_the_normal_picture(self):
        store = MpcStore(self.tmp)
        art = art_loader.ArtLoader(store)
        key = ArtKey("Sol Ring", "c21", "263")
        surf = wait_for(self, lambda: art.small(key, 200, 279), art)
        self.assertEqual(surf.get_size(), (200, 279))
        self.assertIn((key, "normal"), store.get_calls)
        self.assertNotIn((key, "small"), store.get_calls)


# ---- 2. picked pictures are kept ----------------------------------------------------------------------------------------------------
class KeptTests(Base):
    def test_the_kept_folder_is_user_data_beside_my_art(self):
        self.assertEqual(os.path.dirname(paths.mpc_art_dir()), os.path.dirname(paths.my_art_dir()))
        self.assertEqual(os.path.basename(paths.mpc_art_dir()), "mpc_art")
        self.assertEqual(card_data.MPC_KEPT_DIR, card_data.Path(paths.mpc_art_dir()))

    def test_a_pick_fetches_the_kept_picture_at_once(self):
        store = MpcStore(self.tmp)
        g, p = self.gui(store=store)
        p.open_card("Sol Ring")
        p.source = "mpc"
        self.settle(g, p, lambda: len(p.tiles) == 2)
        p.pick(g, ("_mpc", ID2))
        key = ArtKey("Sol Ring", "_mpc", ID2)
        self.settle(g, p, lambda: key in g.art.imgs)
        self.assertIn(key, g.art.imgs)
        self.assertIn((key, "normal"), store.image_calls)

    def test_mpc_art_is_kept_out_of_git_and_the_public_copy_but_in_the_local_zips(self):
        with open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("mpc_art/", f.read().splitlines())
        from tools import export_public
        self.assertIn("mpc_art", export_public.TOP_LEVEL_SKIP)
        import backup
        self.assertNotIn("mpc_art", backup.TOP_LEVEL_SKIP)
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(proj, "mpc_art"))
        with open(os.path.join(proj, "mpc_art", "sol_ring__mpc_x.jpg"), "wb") as f:
            f.write(b"\xff\xd8x")
        files, _left = backup.snapshot_files(proj, os.path.join(self.tmp, "zips"))
        self.assertIn("mpc_art/sol_ring__mpc_x.jpg", [rel for rel, _full in files])


# ---- 3. Goreclaw in, Kinnan out ----------------------------------------------------------------------------------------------------
class AlphaDecksTests(unittest.TestCase):
    def test_goreclaw_is_a_sample_and_kinnan_is_not(self):
        entries = {e.name: e.load() for e in lib.list_decks(os.path.join(HERE, "no_such_folder"), lib.SAMPLE_DIR, HERE)}
        g = entries["We Just Need to Punch Them (Stompy)"]
        self.assertEqual(g.commanders, ["Goreclaw, Terror of Qal Sisma"])
        self.assertEqual(g.total, 100)
        self.assertNotIn("Sasaya, Orochi Ascendant", g.deck)                 # Karl's sideboard line isn't part of the deck
        self.assertFalse(any("Kinnan" in name for name in entries))
        self.assertFalse(os.path.exists(os.path.join(lib.SAMPLE_DIR, "kinnan_nbc_moxfield_export.txt")))
        self.assertTrue(os.path.exists(os.path.join(HERE, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")))
        self.assertNotIn("kinnan_nbc_moxfield_export", lib.SAMPLE_NAMES)

    def test_the_nightly_sweeps_six_alpha_decks(self):
        sys.path.insert(0, os.path.join(HERE, "tools"))
        import nightly
        self.assertEqual(nightly.ALPHA_DECKS[-1], "stompy_goreclaw")
        self.assertEqual(len(nightly.ALPHA_DECKS), 6)
        for stem in nightly.ALPHA_DECKS:
            self.assertTrue(os.path.exists(os.path.join(lib.SAMPLE_DIR, stem + ".txt")), stem)

    def test_the_defaults_point_at_a_deck_that_exists(self):
        import rules_report
        import setup_forge
        for path in (ft.DEFAULT_DECK, rules_report.DEFAULT_DECK):
            self.assertTrue(os.path.exists(path), path)
        with open(os.path.join(HERE, "setup_forge.py"), encoding="utf-8") as f:
            self.assertIn('"stompy_goreclaw.txt"', f.read())
        self.assertTrue(setup_forge)


if __name__ == "__main__":
    unittest.main()
