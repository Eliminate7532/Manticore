# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UI4: the yes/no window's height and the VS screen's partner fan (alpha screenshots 22 and 09)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import flow_screens as flow
import forge_dialogs as dlg
from tests.test_forge_table import frame, make_gui

SIZES = (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1100, 700), 2.0))
SHORT = {"kind": "confirm", "id": 1, "title": "Return your commander to the command zone?", "options": ["Yes", "No"]}
LONG = {"kind": "confirm", "id": 1, "title": "Do you want to pay 1 life? " * 6, "options": ["Yes", "No"]}
PARTNERS = [("You", ["Tymna the Weaver", "Kraum, Ludevic's Opus"], "Blue Farm"), ("AI 1", ["Kinnan, Bonder Prodigy"], "Kinnan"),
            ("AI 2", ["Teysa Karlov"], "Aristocrats"), ("AI 3", ["Light-Paws, Emperor's Voice"], "Voltron")]


def inside(inner, outer):
    return outer.contains(inner)


class ConfirmTests(unittest.TestCase):
    def show(self, req, size, scale):
        gui = make_gui("main1_start", size, scale, log=False)
        d = dlg.ConfirmDialog(dict(req))
        gui.modal = d
        frame(gui, 2)
        return gui, d

    def test_a_short_question_gets_a_short_window(self):
        gui, d = self.show(SHORT, (1920, 1080), 2.0)
        self.assertLess(d.rect.h, 0.3 * 1080, d.rect)              # it was 680 px high (most of the screen) before
        self.assertLess(d.rect.w, 1920 - 40, d.rect)
        line = gui.font("title", True).get_height()
        yes = [r for r, n in d.buttons if n == "yes"][0]
        self.assertGreaterEqual(yes.top - d.rect.top, line + 20)   # the words sit above the buttons

    def test_every_size_keeps_words_and_buttons_inside(self):
        for size, scale in SIZES:
            for req in (SHORT, LONG):
                with self.subTest(size=size, scale=scale, long=req is LONG):
                    gui, d = self.show(req, size, scale)
                    self.assertTrue(inside(d.rect, pygame.Rect(0, 0, *size)), d.rect)
                    for r, _n in d.buttons:
                        self.assertTrue(inside(r, d.rect), (r, d.rect))

    def test_a_card_picture_still_fits(self):
        gui = make_gui("main1_start", (1920, 1080), 2.0, log=False)
        src = next(c for p in gui.state["players"] for z in (p.get("zones") or {}).values() for c in (z or [])
                   if isinstance(c, dict) and c.get("id") is not None)
        d = dlg.ConfirmDialog(dict(SHORT, source=src))
        gui.modal = d
        frame(gui, 2)
        pic = gui.card_rects[src["id"]]
        self.assertTrue(inside(pic, d.rect), (pic, d.rect))
        yes = [r for r, n in d.buttons if n == "yes"][0]
        self.assertFalse(pic.colliderect(yes))


class PartnerFanTests(unittest.TestCase):
    def test_the_first_partner_stays_mostly_visible_and_on_screen(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = make_gui(None, size, scale)
                gui.vs = flow.VsShow(PARTNERS)
                frame(gui)
                first, second = gui.vs.card_rects[0], gui.vs.card_rects[1]
                screen = pygame.Rect(0, 0, *size)
                self.assertTrue(inside(first, screen) and inside(second, screen), (first, second))
                shown = second.left - first.left
                self.assertGreaterEqual(shown, 0.55 * first.w, (first, second))        # 22% before

    def test_seats_without_partners_keep_full_size(self):
        gui = make_gui(None, (1920, 1080), 1.0)
        gui.vs = flow.VsShow(PARTNERS)
        frame(gui)
        partner, single = gui.vs.card_rects[0], gui.vs.card_rects[2]
        gui2 = make_gui(None, (1920, 1080), 1.0)
        gui2.vs = flow.VsShow([("You", ["Kinnan, Bonder Prodigy"], "")] + PARTNERS[1:])
        frame(gui2)
        self.assertEqual(single.size, gui2.vs.card_rects[1].size)                   # a partner elsewhere doesn't shrink it
        self.assertLess(partner.w, single.w)


if __name__ == "__main__":
    unittest.main()
