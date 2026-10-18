# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PARTNER-PASTE (13 Oct 2026, Karl's Kraum + Tymna Moxfield export): a pasted 100-card list whose first TWO lines are a
partner pair imports with both commanders. Moxfield lists the commanders first and the rest alphabetically, so the second commander
is the one card that breaks the alphabetical run."""
import unittest

import deck_importer as di

# the lines visible in Karl's screenshot (everything after Command Tower is filler, kept alphabetical like Moxfield's)
KARL_TOP = ["1 Kraum, Ludevic's Opus (PZ2) 46", "1 Tymna the Weaver (C16) 48 *F*"]
KARL_MAIN = [
    "1 Ad Nauseam (ALA) 63", "1 An Offer You Can't Refuse (FDN) 160", "1 Ancient Tomb (ZNE) 21", "1 Arcane Signet (TDC) 105",
    "1 Arid Mesa (SPG) 109", "1 Badlands (OLGC) 2016NA", "1 Beseech the Mirror (PWOE) 82p",
    "1 Birgi, God of Storytelling / Harnfel, Horn of Bounty (KHM) 311", "1 Bloodstained Mire (MH3) 216",
    "1 Borne Upon a Wind (LTR) 495", "1 Brain Freeze (MB2) 24", "1 Cabal Ritual (V16) 2 *F*", "1 Cavern of Souls (MM3) 232",
    "1 Chain of Vapor (C16) 84", "1 Chrome Mox (MPS) 9 *F*", "1 City of Brass (2X2) 321", "1 City of Traitors (OLGC) 2019NA",
    "1 Command Tower (EOC) 59"]


def filler(n):
    return [f"1 Zzz Filler {i:03d}" for i in range(n)]


def paste(top, main, total=100):
    return "\n".join(top + main + filler(total - len(top) - len(main)))


class PartnerPasteTests(unittest.TestCase):
    def test_karls_export_has_both_commanders(self):
        cmd, deck = di.import_from_text(paste(KARL_TOP, KARL_MAIN))
        self.assertEqual(cmd, ["Kraum, Ludevic's Opus", "Tymna the Weaver"])
        self.assertEqual(len(deck), 98)
        self.assertNotIn("Tymna the Weaver", deck)
        self.assertIn("Ad Nauseam", deck)

    def test_one_commander_is_still_one(self):
        cmd, deck = di.import_from_text(paste(KARL_TOP[:1], KARL_MAIN))
        self.assertEqual(cmd, ["Kraum, Ludevic's Opus"])
        self.assertEqual(len(deck), 99)

    def test_a_partner_that_sorts_with_the_deck_is_not_guessed(self):
        # "Abdel Adrian" sorts above "Ad Nauseam": the text shows no break, so it stays a one-commander guess (as before)
        top = ["1 Kraum, Ludevic's Opus (PZ2) 46", "1 Abdel Adrian, Gorion's Ward (CLB) 1"]
        cmd, deck = di.import_from_text(paste(top, KARL_MAIN))
        self.assertEqual(cmd, ["Kraum, Ludevic's Opus"])
        self.assertEqual(len(deck), 99)

    def test_a_list_that_is_not_alphabetical_keeps_the_old_guess(self):
        names = [f"1 Card {99 - i:03d}" for i in range(100)]                  # reverse order: the second line DOES sort after
                                                                              # the third, but there is no alphabetical run to read
        cmd, deck = di.import_from_text("\n".join(names))
        self.assertEqual(len(cmd), 1)
        self.assertEqual(len(deck), 99)

    def test_an_explicit_commander_header_is_untouched(self):
        text = "Commander\n1 Kraum, Ludevic's Opus\n\nDeck\n" + "\n".join(KARL_MAIN + filler(81))
        cmd, deck = di.import_from_text(text)
        self.assertEqual(cmd, ["Kraum, Ludevic's Opus"])

    def test_a_second_line_with_a_quantity_is_not_a_commander(self):
        top = ["1 Kraum, Ludevic's Opus (PZ2) 46", "4 Zzz Plentiful Card (XXX) 1"]
        cmd, deck = di.import_from_text(paste(top, KARL_MAIN, total=102))       # the expanded list is 105 cards: no 100-card rule
        self.assertLessEqual(len(cmd), 1)

    def test_a_short_list_is_not_guessed_at_all(self):
        cmd, deck = di.import_from_text(paste(KARL_TOP, KARL_MAIN, total=60))
        self.assertEqual(cmd, [])


if __name__ == "__main__":
    unittest.main()
