# SPDX-License-Identifier: GPL-3.0-or-later
"""Forge's refusal messages explained (Karl, 2 Oct 2026: "When prompts like this come up I need them to explain why").

Every message CombatUtil.validateBlocks (Forge 3a74143) can send while you declare blockers gets a plain title, the reason
(read from the card's keywords and rules text) and what to do; the card it's about glows. Unknown messages still show, without
Forge's card numbers.
"""
import copy
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_why as why

LATHRIL = {"id": 201, "name": "Lathril, Blade of the Elves", "keywords": ["Menace"],
           "text": "Menace (This creature can't be blocked except by two or more creatures.)\nWhenever Lathril deals combat damage "
                   "to a player, create that many 1/1 green Elf Warrior creature tokens."}
CARDS = {201: LATHRIL,
         5: {"id": 5, "name": "Siege Behemoth", "text": "Hexproof"},
         7: {"id": 7, "name": "Charging Rhino", "text": "Trample\nCharging Rhino can't be blocked by more than one creature."},
         8: {"id": 8, "name": "Tarox Bladewing", "text": "Grandeur\nTarox Bladewing can't be blocked except by three or more creatures."},
         9: {"id": 9, "name": "Granted Menace", "keywords": ["Menace"], "text": ""}}


def find(cid):
    return CARDS.get(cid)


class BlockerCountTests(unittest.TestCase):
    def test_karls_lathril_message_says_menace_and_what_to_do(self):
        e = why.explain("Lathril, Blade of the Elves (201) cannot be blocked with 1 creatures you've assigned", find)
        self.assertEqual(e.title, "Can't block like that")
        self.assertEqual(e.card_id, 201)
        self.assertIn("Lathril, Blade of the Elves can't be blocked by only 1 creature: it has menace", e.body)
        self.assertIn("two or more creatures", e.body)
        self.assertIn("Add one more blocker to Lathril, Blade of the Elves, or take that blocker off it.", e.body)
        self.assertIn('"Menace (This creature can\'t be blocked except by two or more creatures.)"', e.body)
        self.assertNotIn("(201)", e.body)

    def test_menace_granted_by_another_card_is_read_from_the_keywords(self):
        e = why.explain("Granted Menace (9) cannot be blocked with 1 creatures you've assigned", find)
        self.assertIn("it has menace", e.body)

    def test_three_or_more(self):
        e = why.explain("Tarox Bladewing (8) cannot be blocked with 2 creatures you've assigned", find)
        self.assertIn("can't be blocked except by three or more creatures", e.body)
        self.assertIn("Add one more blocker", e.body)
        e = why.explain("Tarox Bladewing (8) cannot be blocked with 1 creatures you've assigned", find)
        self.assertIn("Add two more blockers", e.body)

    def test_only_one_blocker(self):
        e = why.explain("Charging Rhino (7) cannot be blocked with 2 creatures you've assigned", find)
        self.assertIn("can't be blocked by more than one creature", e.body)
        self.assertIn("Take blockers off Charging Rhino until only one blocks it.", e.body)

    def test_a_reason_we_cant_see_still_explains_in_general(self):
        e = why.explain("Siege Behemoth (5) cannot be blocked with 3 creatures you've assigned", find)
        self.assertIn("You assigned 3 creatures to block Siege Behemoth", e.body)
        self.assertIn("menace", e.body)
        e = why.explain("Mystery (99) cannot be blocked with 1 creatures you've assigned")          # no card data at all
        self.assertEqual(e.card_id, 99)


class OtherBlockRefusalTests(unittest.TestCase):
    def check(self, text, *parts, card_id):
        e = why.explain(text, find)
        self.assertIsNotNone(e, text)
        self.assertEqual(e.title, "Can't block like that")
        self.assertEqual(e.card_id, card_id)
        for p in parts:
            self.assertIn(p, e.body)
        self.assertNotRegex(e.body, r"\(\d+\)")

    def test_every_validate_blocks_message(self):
        self.check("Kor Spiritdancer (62) must still block The Masamune (40).", "Kor Spiritdancer must block The Masamune if it can",
                   card_id=62)
        self.check("Kor Spiritdancer (62) must block an attacker, but has not been assigned to block any.",
                   "is forced to block", "Assign Kor Spiritdancer to block an attacker", card_id=62)
        self.check("Kor Spiritdancer (62) must block an attacker, but has not been assigned to block the right ones.",
                   "the attacker that requires it", card_id=62)
        self.check("Goblin (3) must block each combat but was not assigned to block any attacker now.", "blocks each combat if able",
                   card_id=3)
        self.check("Mogg Flunkies (4) can't block alone.", "can't block alone", card_id=4)
        self.check("Mogg Flunkies (4) can't block unless at least two other creatures block.", "two other creatures", card_id=4)
        self.check("Coward (6) can't block unless a creature with greater power also blocks.", "greater power", card_id=6)

    def test_an_unknown_message_is_left_alone(self):
        self.assertIsNone(why.explain("The AI conceded.", find))
        self.assertIsNone(why.explain("", find))


class TableTests(unittest.TestCase):
    def test_the_table_shows_the_explanation_and_lights_up_the_card(self):
        import forge_dialogs as dlg
        from tests.test_forge_table import frame, make_gui, load_state
        st = copy.deepcopy(load_state("main1_start"))
        opp = [p for p in st["players"] if p["id"] != st["me"]][0]
        card = copy.deepcopy(LATHRIL)
        card.update(zone="Battlefield", isCreature=True, power=2, toughness=3, controller=opp["id"])
        opp["zones"]["battlefield"].append(card)
        gui = make_gui(st)
        gui.session.handle({"t": "message", "title": "Forge",
                            "text": "Lathril, Blade of the Elves (201) cannot be blocked with 1 creatures you've assigned"})
        frame(gui)
        self.assertIsInstance(gui.modal, dlg.MessageDialog)
        self.assertEqual(gui.modal.title, "Can't block like that")
        self.assertIn("menace", gui.modal.text)
        self.assertEqual(gui.spot_name(), "Lathril, Blade of the Elves")

    def test_an_unknown_message_still_shows_without_card_numbers(self):
        import forge_dialogs as dlg
        from tests.test_forge_table import frame, make_gui
        gui = make_gui("main1_start")
        gui.session.handle({"t": "message", "title": "Forge", "text": "Something odd about Sol Ring (12)."})
        frame(gui)
        self.assertIsInstance(gui.modal, dlg.MessageDialog)
        self.assertEqual((gui.modal.title, gui.modal.text), ("Forge", "Something odd about Sol Ring."))


if __name__ == "__main__":
    unittest.main()
