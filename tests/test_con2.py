# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CON2 (gate 1 of the RPG plan): a third Standard sample deck that makes Forge ask more kinds of question.

CON1's Standard soak was INVALID by the soak's own rule (only 2 kinds of question: its two decks are creatures and burn). This deck is
blue-black surveil, scry and "choose one" spells, a hand-look and a planeswalker; the same soak (seed 700, 0.5 h) with it came out
VALID with 7 kinds (docs/incoming/rpg2/con2/SOAK_STANDARD.txt). These tests keep the deck what it is: legal, playable by Forge's AI,
and still full of the cards that ask questions.
"""
import json
import os
import re
import unittest

import deck_importer as di
import deck_library as lib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(BASE, "sample_decks")
STEM = "standard_dimir_surveil"
RUNTIME = os.path.join(BASE, "forge_runtime")
BASICS = ("Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes")


def read_deck():
    with open(os.path.join(SAMPLES, STEM + ".txt"), encoding="utf-8") as f:
        text = f.read()
    side = []
    cmd, deck = di.import_from_text(text, sideboard=side)
    return text, cmd, deck, side


class DeckTests(unittest.TestCase):
    def test_a_standard_deck_of_60_with_no_commander(self):
        text, cmd, deck, side = read_deck()
        self.assertTrue(text.startswith("# format: standard\n"))
        self.assertEqual(cmd, [])
        self.assertEqual(len(deck), 60)
        self.assertEqual(side, [])

    def test_four_of_a_card_at_most(self):
        _t, _c, deck, _s = read_deck()
        counts = {}
        for n in deck:
            counts[n] = counts.get(n, 0) + 1
        self.assertEqual({n: k for n, k in counts.items() if k > 4 and n not in BASICS}, {})

    def test_it_has_a_name_on_the_deck_screen(self):
        self.assertEqual(lib.SAMPLE_NAMES[STEM], "Look Before You Leap (Dimir Surveil)")
        self.assertEqual(lib.display_name(STEM, True), "Look Before You Leap (Dimir Surveil)")

    def test_nothing_on_the_standard_banned_list(self):
        with open(os.path.join(BASE, "banned_standard.snapshot.json"), encoding="utf-8") as f:
            data = json.load(f)
        banned = {n.lower() for n in (data.get("cards") or data.get("names") or [])}
        self.assertTrue(banned, "the snapshot holds the banned names")
        _t, _c, deck, _s = read_deck()
        self.assertEqual(sorted({n for n in deck if n.lower() in banned}), [])

    def test_the_build_counts_it(self):
        import tools.check_dist as cd
        self.assertEqual(cd.EXPECTED_SAMPLE_DECKS, 13)
        self.assertEqual(len([n for n in os.listdir(SAMPLES) if n.endswith(".txt")]), 13)


@unittest.skipUnless(os.path.isdir(os.path.join(RUNTIME, "res", "cardsfolder")), "needs forge_runtime/")
class ForgeScriptTests(unittest.TestCase):
    """Read Forge's own card scripts: every card is there, none is marked for the AI to leave out, and enough of them make Forge
    ask a question (surveil / scry put cards in order or in the graveyard; a Charm is "choose one")."""

    def scripts(self):
        import card_check
        root = os.path.join(RUNTIME, "res", "cardsfolder")
        _t, _c, deck, _s = read_deck()
        return {n: card_check.find_script(root, n) for n in sorted(set(deck))}

    def test_forge_knows_every_card_and_the_ai_may_play_it(self):
        for name, script in self.scripts().items():
            with self.subTest(card=name):
                self.assertIsNotNone(script)
                self.assertNotIn("AI:RemoveDeck", script)

    def test_the_cards_that_ask_questions_are_still_there(self):
        _t, _c, deck, _s = read_deck()
        asks = {"Surveil": 0, "Scry": 0, "Charm": 0}
        for name, script in self.scripts().items():
            for kind in asks:
                if re.search(r"\$ %s\b" % kind, script):
                    asks[kind] += deck.count(name)
        self.assertGreaterEqual(asks["Surveil"], 16, asks)       # 4 Dreamthief, 3 Diviner, 2 Revenant, 2 Kaito, 2 Spellgyre, 3 Research, 4 Sewers
        self.assertGreaterEqual(asks["Scry"], 6, asks)           # 4 Opt, 2 Freeze in Place
        self.assertGreaterEqual(asks["Charm"], 8, asks)          # 4 Heartless Act, 2 Get Out, 2 Spellgyre


if __name__ == "__main__":
    unittest.main()
