# SPDX-License-Identifier: GPL-3.0-or-later
"""deck_importer.import_from_text: how a pasted decklist's commander gets found.

Round 15, from Karl's PC: pasting a deck under a bare "Commander" header, with the mainboard running
straight on afterward (no blank line, no second header), made EVERY remaining card read as another
commander - the deck showed as 98 commanders and an empty 99-card mainboard, which is unplayable.
Fixed by capping what an explicit "Commander" header can claim, and by teaching the parser Karl's other
requested format: a commander with no header at all, set apart only by a blank line at the end of the list.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deck_importer import DeckImportError, import_from_text


def numbered(n, start=0):
    return [f"1 Card{i}" for i in range(start, start + n)]


class CommanderHeaderTests(unittest.TestCase):
    def test_a_single_commander_header_with_no_blank_line_before_the_mainboard(self):
        """The exact bug reported from Karl's PC (Valgavoth deck)."""
        text = "Commander\n1 Valgavoth, Harrower of Souls\n" + "\n".join(numbered(97))
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, ["Valgavoth, Harrower of Souls"])
        self.assertEqual(len(deck), 97)
        self.assertNotIn("Valgavoth, Harrower of Souls", deck)

    def test_partner_commanders_with_a_blank_line_before_the_mainboard(self):
        text = "Commander\n1 Tymna the Weaver\n1 Kraum, Ludevic's Opus\n\n" + "\n".join(numbered(98))
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, ["Tymna the Weaver", "Kraum, Ludevic's Opus"])
        self.assertEqual(len(deck), 98)

    def test_partner_commanders_followed_directly_by_a_deck_header(self):
        text = "Commander\n1 Tymna the Weaver\n1 Kraum, Ludevic's Opus\nDeck\n" + "\n".join(numbered(98))
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, ["Tymna the Weaver", "Kraum, Ludevic's Opus"])
        self.assertEqual(len(deck), 98)

    def test_a_commander_header_running_straight_into_a_long_mainboard_only_claims_the_first_line(self):
        text = "Commander\n1 Kinnan, Bonder Prodigy\n1 Sol Ring\n" + "\n".join(numbered(97))
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, ["Kinnan, Bonder Prodigy"])
        self.assertIn("Sol Ring", deck)
        self.assertEqual(len(deck), 98)

    def test_sideboard_and_maybeboard_are_still_excluded(self):
        text = ("1 Kinnan, Bonder Prodigy\n" + "\n".join(numbered(99)) +
                "\nSideboard\n1 Not Included\nMaybeboard\n1 Also Not")
        commanders, deck = import_from_text(text)
        self.assertNotIn("Not Included", deck)
        self.assertNotIn("Also Not", deck)

    def test_archidekt_inline_commander_tag_still_works(self):
        text = "1 Tymna the Weaver [Commander{top}]\n" + "\n".join(numbered(99))
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, ["Tymna the Weaver"])
        self.assertEqual(len(deck), 99)


class BottomCommanderTests(unittest.TestCase):
    """Karl: 'Pasting in a decklist should detect the commander if its at the bottom like this' - a plain,
    header-less list, a blank line, then one more card by itself at the very end."""

    def test_a_commander_set_off_by_a_blank_line_at_the_end(self):
        text = "\n".join(numbered(99)) + "\n\n1 Valgavoth, Harrower of Souls\n"
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, ["Valgavoth, Harrower of Souls"])
        self.assertEqual(len(deck), 99)
        self.assertNotIn("Valgavoth, Harrower of Souls", deck)

    def test_takes_priority_over_the_first_line_guess_for_a_100_card_list(self):
        """Both heuristics could fire (100 cards total either way) - the explicit blank-line gap must win,
        not the weaker 'first line of a header-less 100-card list' guess."""
        text = "\n".join(numbered(99)) + "\n\n1 Valgavoth, Harrower of Souls\n"
        commanders, _deck = import_from_text(text)
        self.assertEqual(commanders, ["Valgavoth, Harrower of Souls"])

    def test_an_ordinary_last_line_with_no_blank_line_gap_is_not_mistaken_for_this(self):
        text = "1 Kinnan, Bonder Prodigy\n" + "\n".join(numbered(50))
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, [])                      # 51 cards, no header, no gap: no commander found
        self.assertEqual(len(deck), 51)

    def test_the_normal_headerless_moxfield_format_is_unaffected(self):
        text = "1 Kinnan, Bonder Prodigy\n" + "\n".join(numbered(99))
        commanders, deck = import_from_text(text)
        self.assertEqual(commanders, ["Kinnan, Bonder Prodigy"])
        self.assertEqual(len(deck), 99)


if __name__ == "__main__":
    unittest.main()
