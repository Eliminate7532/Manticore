# SPDX-License-Identifier: GPL-3.0-or-later
"""Round STARTER1: pictures of the new-game screen's starting-deck preview with the upgraded cards marked (developer tool).

    python tools/starter1_screens.py [--out checkpoint/screens]

Makes a new game through the real Adventure window (the main menu's Adventure, slot 1, name "Karl", the first offer three times),
then saves the "ready" screen at 1920x1080 (100%) and 1360x840 (200% text), plain and with the mouse on an upgraded card.
"""
import argparse
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame  # noqa: E402

from tests import test_rpg8 as r8  # noqa: E402
from tests.test_forge_table import frame, move  # noqa: E402

OUT = os.path.join(BASE, "checkpoint", "screens")


class Shots(r8.Base):
    def shoot(self, size, scale, tag):
        g = self.gui(size=size, scale=scale)
        f = self.open(g)
        frame(g)
        self.press(g, "new:1")
        for ch in "Karl":
            r8.key(g, getattr(pygame, "K_" + ch.lower()), unicode=ch)
        r8.key(g, pygame.K_RETURN)
        for _ in range(3):
            r8.click(g, next(r for r, k, d in f.rows if k == "pick").center)
        self.assertEqual(f.stage, "ready")
        frame(g, 2)
        os.makedirs(OUT, exist_ok=True)
        pygame.image.save(g.screen, os.path.join(OUT, f"preview_{tag}.png"))
        marks = f.flow.upgraded()
        self.assertTrue(marks)
        # find an upgraded line: draw_deck_text sets upgrade_hover when the mouse is on one - walk the screen in rows
        w, h = g.screen.get_size()
        found = None
        for x in range(20, w, w // 12):
            for y in range(h // 6, h, 8):
                move(g, (x, y))
                if f.upgrade_hover:
                    found = (x, y)
                    break
            if found:
                break
        self.assertIsNotNone(found)
        pygame.image.save(g.screen, os.path.join(OUT, f"preview_{tag}_hover.png"))
        print(tag, "upgrades:", len(f.flow.upgrades()), "hover at", found, f.upgrade_hover)

    def test_1920x1080_100(self):
        self.shoot((1920, 1080), None, "1920x1080_100")

    def test_1360x840_200(self):
        self.shoot((1360, 840), 2.0, "1360x840_200")

    def test_2880x1920_150(self):                      # Round STARTER1C: the third supported size
        self.shoot((2880, 1920), 1.5, "2880x1920_150")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    OUT = a.out
    unittest.main(argv=[sys.argv[0], "-v"])
