# SPDX-License-Identifier: GPL-3.0-or-later
"""forge_fx.draw_attack_glow: the pulsing ring on an attacking/blocking creature (round 15).

Karl asked twice for attacking creatures to have "an animation that makes it clearer" - the old code drew a plain, static
red glow (gfx.glow), the same weight every frame, easy to miss among the other static glows on the board (selectable,
hovered). draw_attack_glow pulses like the existing stack-item ring (forge_fx.draw_marks) already does, and - like every
other effect in this module - holds steady instead of pulsing when the player has turned animations off.
"""
import math
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import unittest

import forge_fx as ffx

pygame.init()
pygame.display.set_mode((1, 1))


def render(rect, now, animate, colour=(230, 90, 84)):
    surf = pygame.Surface((rect.w + 40, rect.h + 40))
    surf.fill((0, 0, 0))
    ffx.draw_attack_glow(surf, rect, now, animate, colour)
    return pygame.image.tostring(surf, "RGB")


class AttackGlowTests(unittest.TestCase):
    def setUp(self):
        self.rect = pygame.Rect(20, 20, 60, 84)

    def test_the_ring_pulses_over_time_when_animations_are_on(self):
        a = render(self.rect, 0.0, True)                        # sin(0) -> pulse 0.5
        b = render(self.rect, math.pi / 10, True)                # sin(pi/2) -> pulse 1.0
        self.assertNotEqual(a, b, "the glow should look different from one moment to the next while animating")

    def test_the_ring_holds_steady_when_animations_are_off(self):
        a = render(self.rect, 0.0, False)
        b = render(self.rect, 999.0, False)
        self.assertEqual(a, b, "animations off should mean a still ring, not a pulsing one")

    def test_something_is_actually_drawn(self):
        blank = pygame.Surface((self.rect.w + 40, self.rect.h + 40))
        blank.fill((0, 0, 0))
        drawn = render(self.rect, 0.0, True)
        self.assertNotEqual(pygame.image.tostring(blank, "RGB"), drawn)


def render_life_flash(rect, colour, strength, now, animate):
    surf = pygame.Surface((rect.w + 60, rect.h + 60))
    surf.fill((0, 0, 0))
    ffx.draw_life_flash(surf, rect, colour, strength, now, animate)
    return pygame.image.tostring(surf, "RGB")


class LifeFlashTests(unittest.TestCase):
    """Karl: 'More flashing around your health total for damage/lifegain.'"""

    def setUp(self):
        self.rect = pygame.Rect(30, 30, 160, 120)

    def test_nothing_is_drawn_once_the_flash_has_faded(self):
        blank = pygame.Surface((self.rect.w + 60, self.rect.h + 60))
        blank.fill((0, 0, 0))
        self.assertEqual(pygame.image.tostring(blank, "RGB"), render_life_flash(self.rect, (120, 235, 140), 0.0, 0.0, True))

    def test_something_is_drawn_while_the_flash_is_live(self):
        blank = pygame.image.tostring(pygame.Surface((self.rect.w + 60, self.rect.h + 60)), "RGB")
        self.assertNotEqual(blank, render_life_flash(self.rect, (255, 110, 110), 1.0, 0.0, True))

    def test_it_fades_out_as_strength_drops(self):
        strong = render_life_flash(self.rect, (255, 110, 110), 1.0, 0.0, False)
        weak = render_life_flash(self.rect, (255, 110, 110), 0.1, 0.0, False)
        self.assertNotEqual(strong, weak)

    def test_it_pulses_over_time_when_animations_are_on(self):
        a = render_life_flash(self.rect, (120, 235, 140), 0.8, 0.0, True)
        b = render_life_flash(self.rect, (120, 235, 140), 0.8, math.pi / 20, True)
        self.assertNotEqual(a, b)

    def test_it_holds_steady_when_animations_are_off(self):
        a = render_life_flash(self.rect, (120, 235, 140), 0.8, 0.0, False)
        b = render_life_flash(self.rect, (120, 235, 140), 0.8, 999.0, False)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
