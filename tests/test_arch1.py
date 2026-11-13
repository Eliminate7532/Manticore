# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ARCH1 (12 Nov 2026): a new tester's Archidekt text export didn't import.

Archidekt can put a label after a line's category tag - "1x Crypt Ghast (rvr) 322 [Ramp] ^Don't Have,#f47373^" - and in that
export the commander's line was "1x Tergrid, God of Fright // Tergrid's Lantern (khm) 112 [Commander{top}] ^Getting,#2ccce4^".
On 0.28.53.15 the "[Commander{top}]" tag was looked for only at the very end of the line and every decoration stripper wanted its
piece at the end too, so: the commander tag was missed, the list's 100 cards made the first line (a non-legendary creature) the
commander by the Moxfield guess - which blocks Start - and the labelled lines kept "(rvr) 322 [Ramp] ^...^" in their names.

The lines here have the same shapes as the tester's export; the list itself is not copied (tests are published).
"""
import unittest

import deck_importer as di

TERGRID = "Tergrid, God of Fright // Tergrid's Lantern"


def hundred_card_list(commander_line, labelled_every=7):
    """A 100-card Archidekt-style export with the commander in the middle and a label on some lines, first line quantity 1."""
    lines = ["1x Accursed Marauder (mh3) 405 [Removal]"]
    for i in range(2, 70):
        label = " ^Don't Have,#f47373^" if i % labelled_every == 0 else ""
        lines.append(f"1x Test Card {i:02d} (tst) {i} [Removal]{label}")
    lines.append(commander_line)
    for i in range(70, 76):
        lines.append(f"1x Test Card {i:02d} (tst) {i} [Draw]")
    lines.append("24x Swamp (trk) 321 [Land]")
    return "\n".join(lines) + "\n"


class LabelTests(unittest.TestCase):
    def test_a_label_after_the_category_tag_is_stripped(self):
        _, deck, pr = di.import_from_text_with_printings(
            "1x Crypt Ghast (rvr) 322 [Ramp] ^Don't Have,#f47373^\n1x Sol Ring (msc) 211 [Ramp]\n")
        self.assertIn("Crypt Ghast", deck)
        self.assertNotIn("^", "".join(deck))
        self.assertEqual(pr.get("crypt ghast"), ("rvr", "322"))

    def test_a_label_with_a_list_number_printing_is_stripped(self):
        _, deck, pr = di.import_from_text_with_printings(
            "1x Sadistic Hypnotist (plst) ODY-159 [Sac Outlet] ^Don't Have,#f47373^\n1x Sol Ring (msc) 211 [Ramp]\n")
        self.assertIn("Sadistic Hypnotist", deck)
        self.assertEqual(pr.get("sadistic hypnotist"), ("plst", "ODY-159"))

    def test_a_label_on_a_line_without_a_category_is_stripped(self):
        _, deck = di.import_from_text("1x Crypt Ghast (rvr) 322 ^Don't Have,#f47373^\n1x Sol Ring\n")
        self.assertIn("Crypt Ghast", deck)


class CommanderTagTests(unittest.TestCase):
    def test_the_commander_tag_before_a_label_is_found(self):
        cmd, deck = di.import_from_text(hundred_card_list(
            f"1x {TERGRID} (khm) 112 [Commander{{top}}] ^Getting,#2ccce4^"))
        self.assertEqual(cmd, [TERGRID])
        self.assertEqual(len(deck), 99)
        self.assertIn("Accursed Marauder", deck)          # the first line is NOT taken for the commander
        self.assertNotIn("^", "".join(deck))

    def test_the_commander_tag_after_a_label_is_found(self):
        cmd, deck = di.import_from_text(hundred_card_list(
            f"1x {TERGRID} (khm) 112 ^Getting,#2ccce4^ [Commander{{top}}]"))
        self.assertEqual(cmd, [TERGRID])
        self.assertEqual(len(deck), 99)

    def test_the_commander_tag_with_a_foil_marker_and_a_label(self):
        cmd, _ = di.import_from_text(hundred_card_list(
            f"1x {TERGRID} (khm) 112 *F* [Commander{{top}}] ^Getting,#2ccce4^"))
        self.assertEqual(cmd, [TERGRID])

    def test_the_commander_printing_is_kept(self):
        _, _, pr = di.import_from_text_with_printings(hundred_card_list(
            f"1x {TERGRID} (khm) 112 [Commander{{top}}] ^Getting,#2ccce4^"))
        self.assertEqual(pr.get(TERGRID.lower()), ("khm", "112"))


class UnchangedTests(unittest.TestCase):
    def test_a_plain_commander_tag_still_works(self):
        cmd, deck = di.import_from_text(hundred_card_list(f"1x {TERGRID} (khm) 112 [Commander{{top}}]"))
        self.assertEqual(cmd, [TERGRID])
        self.assertEqual(len(deck), 99)

    def test_a_card_named_commander_something_is_not_a_tag(self):
        cmd, deck = di.import_from_text("Commander\n1 Tergrid, God of Fright\n\n1 Commander's Sphere\n1 Sol Ring\n")
        self.assertEqual(cmd, ["Tergrid, God of Fright"])
        self.assertIn("Commander's Sphere", deck)

    def test_a_label_naming_commander_is_not_a_commander_tag(self):
        cmd, deck = di.import_from_text("Commander\n1 Tergrid, God of Fright\n\n1 Sol Ring [Ramp] ^Commander staple,#ffffff^\n")
        self.assertEqual(cmd, ["Tergrid, God of Fright"])
        self.assertIn("Sol Ring", deck)

    def test_a_moxfield_list_without_labels_reads_as_before(self):
        text = "\n".join(["1 Tergrid, God of Fright"] + [f"1 Test Card {i:02d}" for i in range(75)] + ["24 Swamp"]) + "\n"
        cmd, deck = di.import_from_text(text)
        self.assertEqual(cmd, ["Tergrid, God of Fright"])
        self.assertEqual(len(deck), 99)


if __name__ == "__main__":
    unittest.main()
