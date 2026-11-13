# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1568353208475066370 (Karl Shuh, 6 Nov 2026): The Call of the Ring's "you may pay 2 life. If you do, draw a card" was paid.

Forge asked (InputConfirm "Do you want to pay 2 life?", Yes focused), but the question named no card and Space/Enter answered Yes,
so a pass-priority keypress paid. Karl, 7 Nov: "Card + no Space/Enter" - every optional-cost question names the card it comes from,
and only a click on Yes or No answers it.
"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame

pygame.init()

import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_state

RING = {"key": "The Call of the Ring (77): trigger", "text": "The Call of the Ring trigger", "trigger": True, "ability": True,
        "card": {"id": 77, "name": "The Call of the Ring", "controller": 0, "zone": "Battlefield"}}


def cost_state(message="Do you want to pay 2 life?", stack=(RING,), source=None, input_class="InputConfirm"):
    st = load_state("stack_one")
    st.update({"input": input_class, "asking": True, "inputSeq": 9, "stack": list(stack)})
    st["prompt"] = {"message": message, "ok": {"label": "Yes", "enabled": True, "focus": True},
                    "cancel": {"label": "No", "enabled": True}}
    if source is not None:
        st["prompt"]["source"] = source
    return st


class TextTests(unittest.TestCase):
    def test_forge_cost_questions_are_recognised(self):
        cases = {"Do you want to pay 2 life?": "Pay 2 life",
                 "Do you want to pay {1}?": "Pay {1}",
                 "Do you want to sacrifice Llanowar Elves (12)?": "Sacrifice Llanowar Elves",
                 "Do you want to discard your hand?": "Discard your hand",
                 "Do you want to draw 1 card(s)?": "Draw 1 card(s)",
                 "Pay 3 life?": "Pay 3 life",
                 "Sacrifice Treasure (5)?": "Sacrifice Treasure",
                 "Do you want Sulfuric Vortex to deal 2 damage to you?": "Take 2 damage from Sulfuric Vortex",
                 "The Call of the Ring (77) - Draw a card\n\nDo you want to pay 2 life?": "Pay 2 life"}
        for msg, want in cases.items():
            self.assertEqual(ft.optional_cost_text(msg, "confirm"), want, msg)

    def test_other_questions_are_not_costs(self):
        for msg in ("Do you want to keep your hand?", "Do you want to scry?", "Do you want to play Island?",
                    "Do you want to copy Lightning Bolt?", "Priority: Main 1 - Karl - 1 to Resolve", ""):
            self.assertIsNone(ft.optional_cost_text(msg, "confirm"), msg)
        self.assertIsNone(ft.optional_cost_text("Do you want to pay 2 life?", "priority"))   # only a yes/no question

    def test_the_bar_names_the_card(self):
        head, hint, _ok = ft.prompt_view("Do you want to pay 2 life?", "Karl", kind="confirm", about="The Call of the Ring")
        self.assertEqual(head, "Pay 2 life?  -  The Call of the Ring")
        self.assertIn("Space and Enter don't answer", hint)


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, state):
        session = FakeSession(state)
        session.handle({"t": "ready", "protocol": 2, "passing": "smart"})
        gui = ft.ForgeTable(session, StubStore(), settings_path=os.path.join(self.tmp.name, "s.json"), window_size=(1360, 840))
        gui.sync()
        gui.render()
        return gui, session

    def key(self, gui, k):
        gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode=""))

    def sent(self, s):
        return [c["c"] for c in s.sent if c["c"] in ("ok", "cancel", "yield", "passturn")]

    def test_space_and_enter_do_not_pay(self):
        gui, s = self.make(cost_state())
        for k in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.key(gui, k)
        self.assertEqual(self.sent(s), [])

    def test_clicking_yes_still_pays(self):
        gui, s = self.make(cost_state())
        gui.press("ok")                                # the Yes button's click
        self.assertIn("ok", self.sent(s))

    def test_the_card_is_the_resolving_trigger_and_shows_in_the_focus_panel(self):
        gui, _s = self.make(cost_state())
        gui.mouse = (-50, -50)                          # nothing hovered
        self.assertEqual(gui.cost_card_name(), "The Call of the Ring")
        self.assertEqual((gui.preview_card() or {}).get("name"), "The Call of the Ring")

    def test_a_card_forge_sends_with_the_question_wins(self):
        src = {"id": 5, "name": "Tithing Blade", "zone": "Battlefield"}
        gui, _s = self.make(cost_state(source=src))
        self.assertEqual(gui.cost_card_name(), "Tithing Blade")

    def test_other_confirm_questions_keep_their_keys(self):
        gui, s = self.make(cost_state(message="Do you want to copy Lightning Bolt?"))
        self.key(gui, pygame.K_SPACE)
        self.assertEqual(self.sent(s), ["ok"])
        self.assertEqual(gui.cost_card_name(), "")


if __name__ == "__main__":
    unittest.main()
