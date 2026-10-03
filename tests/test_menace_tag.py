# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 32 (Karl, 3 Oct 2026, with a picture of Lathril, Blade of the Elves on the battlefield: "creatures with menace need to have a
'tag' on them like haste, flying, lifelink, etc").

The keyword tags on a permanent only showed keywords it had GAINED ("+Flying" from an Equipment, a spell, an ability): a printed
keyword is in the card's text, so it was never tagged. But a board-sized card's text can't be read, and menace changes how the
creature can be blocked (two or more blockers). forge_table.PRINTED_TAGS names the printed keywords that get a tag anyway: menace.
Forge reports Lathril's keywords as ["Menace"] (checked live, Forge fb4d809).
"""
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_table as ft
from tests.test_forge_table import make_gui
from tests.test_round11 import state_with
from tests.test_round13 import creature

LATHRIL_TEXT = ("Menace (This creature can't be blocked except by two or more creatures.)\nWhenever Lathril deals combat damage to a "
                "player, create that many 1/1 green Elf Warrior creature tokens.\n{T}, Tap ten untapped Elves you control: Each "
                "opponent loses 10 life and you gain 10 life.")


def lathril(keywords=("Menace",)):
    return creature(list(keywords), LATHRIL_TEXT, cid=71, name="Lathril, Blade of the Elves", oracleName="Lathril, Blade of the Elves",
                    type="Legendary Creature - Elf Noble", cost="{2}{B}{G}", colors=["B", "G"], power=2, toughness=3)


class KeywordTagTests(unittest.TestCase):
    def test_printed_menace_is_tagged(self):
        self.assertEqual(ft.keyword_tags(lathril()), ["Menace"])
        self.assertEqual(ft.keyword_tags(creature(["Menace"], "Menace")), ["Menace"])

    def test_gained_menace_is_tagged_as_gained(self):
        self.assertEqual(ft.keyword_tags(creature(["Menace"])), ["+Menace"])

    def test_other_printed_keywords_are_still_not_tagged(self):
        self.assertEqual(ft.keyword_tags(creature(["Flying", "Lifelink"], "Flying, lifelink")), [])

    def test_printed_menace_comes_first_then_the_gained_ones(self):
        self.assertEqual(ft.keyword_tags(lathril(["Menace", "Flying", "Haste"])), ["Menace", "+Flying", "+Haste"])

    def test_menace_it_no_longer_has_is_not_tagged(self):
        """Only Forge's current list counts: a Humility or a Turn to Frog takes printed menace away."""
        self.assertEqual(ft.keyword_tags(lathril([])), [])
        self.assertEqual(ft.keyword_tags(dict(lathril(), keywords=None)), [])

    def test_listed_once(self):
        self.assertEqual(ft.keyword_tags(lathril(["Menace", "Menace"])), ["Menace"])


class KeywordTagDrawingTests(unittest.TestCase):
    def pixels(self, card, w=120, h=168):
        gui = make_gui(state_with(card))
        return pygame.image.tostring(gui.card_surface(card, w, h, True), "RGB")

    def test_a_menace_creature_gets_a_chip(self):
        self.assertNotEqual(self.pixels(lathril()), self.pixels(lathril([])))

    def test_the_chip_is_the_same_violet_as_the_gained_ones(self):
        card = lathril()
        gui = make_gui(state_with(card))
        surf = gui.card_surface(card, 200, 280, True)
        found = any(surf.get_at((x, y))[:3] == (74, 52, 130) for x in range(0, 100) for y in range(150, 280))
        self.assertTrue(found, "no violet chip in the lower left of the card")

    def test_losing_menace_redraws_the_card(self):
        """The cached picture is keyed on the tags, so a creature that loses menace doesn't keep showing the old chip."""
        gui = make_gui(state_with(lathril()))
        with_tag = pygame.image.tostring(gui.card_surface(lathril(), 120, 168, True), "RGB")
        without = pygame.image.tostring(gui.card_surface(lathril([]), 120, 168, True), "RGB")
        self.assertNotEqual(with_tag, without)


if __name__ == "__main__":
    unittest.main()
