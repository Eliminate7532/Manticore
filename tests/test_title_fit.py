# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 31 (title fix, Karl 3 Oct: "fix the aspect ratio of the background image").

The title and studio pictures used to be scaled to cover the window and cropped. On Karl's 4096x2160 screen (1.9:1) that cut
off the top fifth of the 1.49:1 title art (the wings and the head) and put the MANTICORE lettering under the menu. Now the whole
picture is shown at its own proportions, as large as fits, centred; a blurred, darkened copy fills the rest.
"""
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import boot_screens as bs

pygame.init()

TITLE = (1168, 784)                          # assets/splash/title.png


class FitRectTests(unittest.TestCase):
    def test_a_wide_window_shows_the_whole_picture_at_its_own_shape(self):
        for size in ((4096, 2160), (1920, 1080), (1366, 768), (2560, 1080)):
            with self.subTest(size=size):
                r = bs.fit_rect(TITLE, size)
                self.assertEqual(r.h, size[1])                               # full height
                self.assertLessEqual(r.w, size[0])                           # the whole width of the picture fits
                self.assertAlmostEqual(r.w / r.h, TITLE[0] / TITLE[1], delta=0.01)
                self.assertAlmostEqual(r.centerx, size[0] // 2, delta=1)

    def test_a_tall_window_leaves_room_above_and_below(self):
        for size in ((1280, 1024), (1024, 768), (800, 1200)):
            with self.subTest(size=size):
                r = bs.fit_rect(TITLE, size)
                self.assertEqual(r.w, size[0])
                self.assertLessEqual(r.h, size[1])
                self.assertAlmostEqual(r.w / r.h, TITLE[0] / TITLE[1], delta=0.01)
                self.assertAlmostEqual(r.centery, size[1] // 2, delta=1)

    def test_a_window_of_the_pictures_own_shape_is_filled(self):
        self.assertEqual(bs.fit_rect(TITLE, (1168, 784)), pygame.Rect(0, 0, 1168, 784))
        self.assertEqual(bs.fit_rect(TITLE, (2336, 1568)), pygame.Rect(0, 0, 2336, 1568))


class PictureTests(unittest.TestCase):
    def setUp(self):
        bs._PICS.clear()
        self.raw = pygame.image.load(bs.splash_path("title"))

    def test_nothing_of_the_picture_is_cropped_on_karls_screen(self):
        """The top of the picture (the wings and head) is on the screen: the window's top rows inside the picture match the
        picture's own top rows. With the old cover-and-crop the window's top showed the picture from about 20% down."""
        size = (4096, 2160)
        pic = bs.picture("title", size)
        self.assertEqual(pic.get_size(), size)
        r = bs.fit_rect(self.raw.get_size(), size)
        fitted = pygame.transform.smoothscale(self.raw, r.size)
        diffs = []
        for x in range(r.w // 4, r.w * 3 // 4, 97):                            # the middle half: no edge fade there
            for y in (2, r.h // 10, r.h // 2, r.h - 3):
                a, b = pic.get_at((r.x + x, r.y + y)), fitted.get_at((x, y))
                diffs.append(sum(abs(a[i] - b[i]) for i in range(3)))
        self.assertLess(max(diffs), 12, "the picture on screen is not the whole picture at its own shape")

    def test_the_sides_are_a_dark_fill_not_black_and_not_the_picture(self):
        size = (4096, 2160)
        pic = bs.picture("title", size)
        r = bs.fit_rect(self.raw.get_size(), size)
        self.assertGreater(r.x, 100)
        side = [pic.get_at((r.x // 2, y))[:3] for y in range(100, size[1] - 100, 150)]
        self.assertTrue(any(sum(c) > 0 for c in side), "the side is plain black")
        mean_side = sum(sum(c) for c in side) / len(side)
        mid = [pic.get_at((size[0] // 2, y))[:3] for y in range(100, size[1] - 100, 150)]
        self.assertLess(mean_side, sum(sum(c) for c in mid) / len(mid), "the fill should be darker than the picture")

    def test_the_menu_still_dims_it_and_a_missing_file_is_still_plain(self):
        size = (1920, 1080)
        pic, dim = bs.picture("title", size), bs.dimmed("title", size)
        mean = lambda s: sum(sum(s.get_at((x, y))[:3]) for x in range(0, size[0], 40) for y in range(0, size[1], 40))
        self.assertLess(mean(dim), mean(pic) * 0.6)
        old = bs.FILES["title"]
        bs.FILES["title"] = "no_such_file.png"
        try:
            bs._PICS.clear()
            self.assertIsNone(bs.picture("title", size))
        finally:
            bs.FILES["title"] = old
            bs._PICS.clear()

    def test_the_studio_splash_is_shown_whole_too(self):
        raw = pygame.image.load(bs.splash_path("studio"))
        size = (4096, 2160)
        pic = bs.picture("studio", size)
        r = bs.fit_rect(raw.get_size(), size)
        fitted = pygame.transform.smoothscale(raw, r.size)
        a, b = pic.get_at((r.centerx, r.y + 2)), fitted.get_at((r.w // 2, 2))
        self.assertLess(sum(abs(a[i] - b[i]) for i in range(3)), 12)


if __name__ == "__main__":
    unittest.main()
