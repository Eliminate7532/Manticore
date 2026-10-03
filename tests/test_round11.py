# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 11: what is imprinted on a card (Chrome Mox). Bug report 2026-09-20 16:14: Karl imprinted a blue card on Chrome Mox, then could not
pay Kinnan's {G}. The engine was right (Chrome Mox only makes the colours of the exiled card), but nothing on the table said so."""
import copy
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_table as ft
from tests.forge_fake import load_state
from tests.test_forge_table import frame, make_gui, move, point_for_card

MOX_TEXT = ("Imprint — When Chrome Mox enters, you may exile a nonartifact, nonland card from your hand.\r\n\r\n"
            "{T}: Add one mana of any of the exiled card's colors.")
SAMUT = {"name": "Samut, Tyrant of Naktamun", "colors": ["U"]}


def mox(imprinted=None, cid=61):
    card = {"id": cid, "hidden": False, "owner": 0, "controller": 0, "zone": "Battlefield", "name": "Chrome Mox", "oracleName": "Chrome Mox",
            "type": "Artifact", "cost": "{0}", "colors": [], "text": MOX_TEXT, "isCreature": False, "isLand": False, "isPlaneswalker": False,
            "set": "SLZ", "tapped": False, "sick": False, "attacking": False, "blocking": False, "faceDown": False, "token": False,
            "commander": False, "damage": 0, "selectable": False}
    if imprinted is not None:
        card["imprinted"] = imprinted
    return card


def state_with(card):
    st = copy.deepcopy(load_state("main1_start"))
    me = next(p for p in st["players"] if p["id"] == st["me"])
    me["zones"]["battlefield"].append(card)
    return st


def pixels(surface):
    return pygame.image.tostring(surface, "RGB")


class ImprintHelperTests(unittest.TestCase):
    def test_colours_are_merged_and_put_in_wubrg_order(self):
        card = mox([{"name": "A", "colors": ["G", "U"]}, {"name": "B", "colors": ["U", "W"]}])
        self.assertEqual(ft.imprint_colours(card), ["W", "U", "G"])
        self.assertEqual(ft.imprint_colours(mox()), [])
        self.assertEqual(ft.imprint_colours(mox([{"name": "Face-down card"}])), [])

    def test_only_a_chrome_mox_style_card_is_treated_as_making_the_imprinted_colours(self):
        self.assertTrue(ft.makes_imprinted_colours(mox()))
        scepter = dict(mox(), name="Isochron Scepter", text="Imprint — When Isochron Scepter enters, you may exile an instant card with mana value 2 or less from your hand.")
        self.assertFalse(ft.makes_imprinted_colours(scepter))
        self.assertFalse(ft.makes_imprinted_colours({"text": None}))


class ImprintCaptionTests(unittest.TestCase):
    def caption(self, card):
        gui = make_gui(state_with(card))
        return gui.imprint_caption(card)

    def test_the_caption_names_the_card_and_its_colour(self):
        self.assertEqual(self.caption(mox([SAMUT])), "Imprinted: Samut, Tyrant of Naktamun (blue)")

    def test_two_colours_and_two_cards(self):
        self.assertEqual(self.caption(mox([{"name": "Bloodbraid Elf", "colors": ["R", "G"]}])), "Imprinted: Bloodbraid Elf (red/green)")
        self.assertEqual(self.caption(mox([SAMUT, {"name": "Wrath of God", "colors": ["W"]}])),
                         "Imprinted: Samut, Tyrant of Naktamun (blue), Wrath of God (white)")

    def test_an_empty_chrome_mox_says_it_makes_no_mana(self):
        self.assertEqual(self.caption(mox()), "Nothing imprinted: makes no mana")
        self.assertEqual(self.caption(mox([])), "Nothing imprinted: makes no mana")

    def test_a_colourless_or_face_down_imprint_is_named_honestly(self):
        self.assertIn("(colourless)", self.caption(mox([{"name": "Thing", "colors": []}])))
        self.assertIn("a face-down card", self.caption(mox([{"name": "a face-down card"}])))

    def test_other_cards_get_no_caption(self):
        st = load_state("main1_start")
        gui = make_gui(st)
        for zone in ("hand", "battlefield"):
            for c in gui.session.me()["zones"][zone]:
                self.assertEqual(gui.imprint_caption(c), "", c["name"])
        self.assertEqual(gui.imprint_caption(dict(mox(), hidden=True)), "")

    def test_a_card_that_is_not_a_mox_but_has_something_imprinted_still_names_it(self):
        scepter = dict(mox([{"name": "Lightning Bolt", "colors": ["R"]}]), name="Isochron Scepter", text="Imprint - exile an instant.")
        self.assertEqual(self.caption(scepter), "Imprinted: Lightning Bolt (red)")


class ImprintDrawingTests(unittest.TestCase):
    def surface(self, card, w=120, h=168):
        gui = make_gui(state_with(card))
        return pixels(gui.card_surface(card, w, h, True))

    def test_the_card_wears_a_different_badge_for_each_colour_state(self):
        blue, green, none = self.surface(mox([SAMUT])), self.surface(mox([{"name": "E", "colors": ["G"]}])), self.surface(mox())
        self.assertEqual(len({blue, green, none}), 3)

    def test_the_badge_is_not_drawn_on_other_artifacts(self):
        gui = make_gui(state_with(mox([SAMUT])))
        sol = next(c for c in gui.session.me()["zones"]["hand"] if c["name"] == "Sol Ring")
        plain = pixels(gui.card_surface(sol, 120, 168, True))
        imprinted = pixels(gui.card_surface(dict(sol, imprinted=[SAMUT]), 120, 168, True))
        self.assertEqual(plain, imprinted)

    def test_the_badge_shows_the_blue_mana_colour(self):
        gui = make_gui(state_with(mox([SAMUT])))
        surf = gui.card_surface(mox([SAMUT]), 200, 280, True)
        blue = ft.gfx.MANA_FILL["U"]
        found = any(surf.get_at((x, y))[:3] == blue for x in range(0, 60) for y in range(190, 280))
        self.assertTrue(found, "no blue mana symbol in the bottom-left corner of the card")

    def test_hovering_the_mox_shows_the_caption_under_the_big_picture(self):
        # Round 27 (Scryfall image rules) moved the caption from ON the picture to a strip BELOW it (L.caption), so the
        # caption strip is what must change, and the picture itself must NOT (the card's own artist/copyright line stays visible).
        gui = make_gui(state_with(mox([SAMUT])))
        move(gui, point_for_card(gui, 61))
        strip, picture = gui.L.caption, gui.L.preview
        self.assertGreater(strip.h, 0, "hovering an imprinted card must reserve the caption strip")
        with_caption = pixels(gui.screen.subsurface(strip).copy())
        picture_with = pixels(gui.screen.subsurface(picture).copy())
        with unittest_patch(gui):
            frame(gui)
            without = pixels(gui.screen.subsurface(strip).copy())
            picture_without = pixels(gui.screen.subsurface(picture).copy())
        self.assertNotEqual(with_caption, without, "the caption strip shows the imprinted card")
        self.assertEqual(picture_with, picture_without, "the caption never covers the card picture")

    def test_the_caption_stays_inside_the_picture_at_big_text_and_small_windows(self):
        for size, scale in (((1360, 840), 1.0), ((900, 600), 1.0), ((1100, 700), 2.0), ((1920, 1080), 1.5)):
            gui = make_gui(state_with(mox([{"name": "A very long card name indeed, Tyrant of the Long Names", "colors": ["W", "U", "B"]}])),
                           size=size, scale=scale)
            move(gui, point_for_card(gui, 61))
            strip = gui.draw_imprint_caption(mox([{"name": "A very long card name indeed, Tyrant of the Long Names", "colors": ["W", "U", "B"]}]), gui.L.preview)
            self.assertIsNotNone(strip)
            self.assertTrue(gui.L.preview.contains(strip), (size, scale, strip, gui.L.preview))


class unittest_patch:
    """Draw the preview without its imprint caption (to see that the caption is what changed)."""
    def __init__(self, gui):
        self.gui = gui

    def __enter__(self):
        self.old = self.gui.draw_imprint_caption
        self.gui.draw_imprint_caption = lambda card, r: None

    def __exit__(self, *exc):
        self.gui.draw_imprint_caption = self.old


if __name__ == "__main__":
    unittest.main()
