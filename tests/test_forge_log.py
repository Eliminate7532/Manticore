# SPDX-License-Identifier: GPL-3.0-or-later
"""forge_log: Forge's log lines -> readable rows (no window needed)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_log as fl
from tests.forge_fake import load_log

CARDS = ["Forest", "Llanowar Elves", "Sol Ring", "Lotus Petal", "Chrome Mox", "Kinnan, Bonder Prodigy", "Basalt Monolith"]


class Font:
    """1 px per 6 characters is enough to test wrapping."""
    def size(self, text):
        return (len(text) * 6, 10)


def fmt(entries, me="Karl", opps=("AI 1 (Kinnan)",), cards=CARDS):
    f = fl.LogFormatter()
    f.context(me, list(opps), cards)
    rows = []
    for e in entries:
        rows.extend(f.format(e))
    return rows


def plain(row):
    return "".join(t for t, _s in row.segs)


class RewriteTests(unittest.TestCase):
    def test_you_and_opponents_and_card_ids(self):
        rows = fmt([{"type": "LAND", "text": "Karl played Forest (44)"},
                    {"type": "STACK_ADD", "text": "AI 1 (Kinnan) cast Lotus Petal"}])
        self.assertEqual([plain(r) for r in rows], ["You played Forest", "AI 1 (Kinnan) cast Lotus Petal"])
        self.assertEqual(rows[0].segs, [("You", "me"), (" played ", "text"), ("Forest", "card")])
        self.assertEqual(rows[1].segs[0], ("AI 1 (Kinnan)", "opp"))
        self.assertEqual(rows[1].card, "Lotus Petal")
        self.assertTrue(rows[1].feed and not rows[0].feed)          # only the opponent's actions flash on the board

    def test_spell_resolution_is_short_and_ability_text_is_kept_but_trimmed(self):
        rows = fmt([{"type": "STACK_RESOLVE", "text": "Llanowar Elves - Creature 1 / 1"},
                    {"type": "STACK_RESOLVE", "text": "Lotus Petal"},
                    {"type": "STACK_RESOLVE", "text": "Imprint - When Chrome Mox enters, you may exile a card. [Zone Changer: Chrome Mox (117)]"},
                    {"type": "STACK_RESOLVE", "text": "Some ability. " * 30}])
        self.assertEqual(plain(rows[0]), "Llanowar Elves resolves")
        self.assertEqual(plain(rows[1]), "Lotus Petal resolves")
        self.assertNotIn("Zone Changer", plain(rows[2]))
        self.assertNotIn("117", plain(rows[2]))
        self.assertTrue(plain(rows[3]).endswith("..."))
        self.assertLess(len(plain(rows[3])), 160)

    def test_life_damage_death_and_combat(self):
        rows = fmt([{"type": "LIFE", "text": "Life: Karl 40 > 37"},
                    {"type": "LIFE", "text": "Life: AI 1 (Kinnan) 40 > 42"},
                    {"type": "DAMAGE", "text": "Llanowar Elves (12) deals 1 combat damage to Karl."},
                    {"type": "ZONE_CHANGE", "text": "Lotus Petal (70) was put into Graveyard from Battlefield."},
                    {"type": "ZONE_CHANGE", "text": "Sol Ring (3) was put into Exile from Battlefield."},
                    {"type": "COMBAT", "text": "AI 1 (Kinnan) assigned Kinnan, Bonder Prodigy (9) to attack Karl.\n"
                                               "Karl assigned Llanowar Elves (12) to block Kinnan, Bonder Prodigy (9)."},
                    {"type": "COMBAT", "text": "Karl didn't block Kinnan, Bonder Prodigy (9)."}])
        self.assertEqual([plain(r) for r in rows], [
            "Your life: 40 to 37 (-3)", "AI 1 (Kinnan)'s life: 40 to 42 (+2)",
            "Llanowar Elves deals 1 combat damage to you", "Lotus Petal died", "Sol Ring was exiled from the battlefield",
            "AI 1 (Kinnan) attacks you with Kinnan, Bonder Prodigy", "You block Kinnan, Bonder Prodigy with Llanowar Elves",
            "You don't block Kinnan, Bonder Prodigy"])
        self.assertEqual(rows[0].segs[-1], ("(-3)", "bad"))
        self.assertEqual(rows[1].segs[-1], ("(+2)", "good"))

    def test_noise_is_dropped(self):
        rows = fmt([{"type": "MANA", "text": "Forest (44) - {T}: Add {G}."}, {"type": "LAND", "text": "Lotus Petal (70) picked {G}"},
                    {"type": "MATCH_RESULTS", "text": "x"}, {"type": "PHASE", "text": "Karl's Untap step"}])
        self.assertEqual(rows, [])

    def test_mana_from_a_card_leaving_the_hand_stays_in_the_log(self):
        """Bug report 2026-09-20: the AI cast Kinnan (GU) with only Otawara tapped. The other G was Elvish Spirit Guide, exiled from
        the hand, and the log hid it, so it looked like one mana. Hand-exile mana now shows; ordinary land/rock mana stays hidden."""
        rows = fmt([{"type": "PHASE", "text": "AI 1 (Kinnan)'s Main phase, postcombat"},
                    {"type": "MANA", "text": "Otawara, Soaring City (107) - {T}: Add {U}."},
                    {"type": "MANA", "text": "Elvish Spirit Guide (190) - Exile Elvish Spirit Guide from your hand: Add {G}."},
                    {"type": "STACK_ADD", "text": "AI 1 (Kinnan) cast Kinnan, Bonder Prodigy"}],
                   cards=CARDS + ["Elvish Spirit Guide"])
        self.assertEqual([r.kind for r in rows], ["phase", "line", "line"])       # the phase label comes first, and only one mana row
        self.assertEqual(plain(rows[1]), "Elvish Spirit Guide exiled from hand for {G}")
        self.assertEqual(rows[1].segs[0], ("Elvish Spirit Guide", "card"))
        self.assertEqual(rows[1].bar, "MANA")
        self.assertTrue(rows[1].feed)          # round 2026-09-22: the log line alone was still missed; it now pops into the feed too
        self.assertIn("cast", plain(rows[2]))

    def test_hand_mana_pops_into_the_feed_like_the_second_report(self):
        """Bug report 2026-09-22 (bugreport_20260922_214652_Karl.zip): same root cause as the round-11 report above, reproduced
        again with a different pair of cards (Tropical Island + Elvish Spirit Guide paying for Kinnan). state.json confirmed the
        engine paid correctly (Tropical Island tapped, Elvish Spirit Guide in exile) and the log line was already present and
        correct - Karl simply never saw it, because it only appeared in the scrolling log column and not in the on-screen feed
        the way an opponent's cast or an exile/death already does. Feed=True (this round's fix) is what closes that gap; a
        regression here would silently reopen the exact same complaint a second time."""
        rows = fmt([{"type": "MANA", "text": "Tropical Island (153) - {T}: Add {U}."},
                    {"type": "MANA", "text": "Elvish Spirit Guide (190) - Exile Elvish Spirit Guide from your hand: Add {G}."},
                    {"type": "STACK_ADD", "text": "AI 1 (Kinnan) cast Kinnan, Bonder Prodigy"}],
                   cards=CARDS + ["Elvish Spirit Guide", "Tropical Island"])
        self.assertEqual([r.kind for r in rows], ["line", "line"])
        mana_row = rows[0]
        self.assertTrue(mana_row.feed)
        self.assertEqual(mana_row.card, "Elvish Spirit Guide")

    def test_verbs_agree_with_you(self):
        rows = fmt([{"type": "MULLIGAN", "text": "Karl has kept a hand of 7 cards"}])
        self.assertEqual(plain(rows[0]), "You have kept a hand of 7 cards")


class StructureTests(unittest.TestCase):
    def test_turn_headers_and_phase_labels_appear_once_and_only_when_something_happens(self):
        rows = fmt([{"type": "TURN", "text": "Turn 1 (Karl)"}, {"type": "PHASE", "text": "Karl's Untap step"},
                    {"type": "PHASE", "text": "Karl's Main phase, precombat"}, {"type": "LAND", "text": "Karl played Forest (1)"},
                    {"type": "STACK_ADD", "text": "Karl cast Sol Ring"}, {"type": "PHASE", "text": "Karl's Beginning of Combat Step"},
                    {"type": "PHASE", "text": "Karl's Main phase, postcombat"}, {"type": "STACK_ADD", "text": "Karl cast Lotus Petal"},
                    {"type": "TURN", "text": "Turn 2 (AI 1 (Kinnan))"}, {"type": "PHASE", "text": "AI 1 (Kinnan)'s Main phase, precombat"},
                    {"type": "LAND", "text": "AI 1 (Kinnan) played Forest (2)"}])
        kinds = [(r.kind, r.label or plain(r)) for r in rows]
        self.assertEqual(kinds, [("spacer", ""), ("turn", "Karl"), ("phase", "Main 1"), ("line", "You played Forest"),
                                 ("line", "You cast Sol Ring"), ("phase", "Main 2"), ("line", "You cast Lotus Petal"),
                                 ("spacer", ""), ("turn", "AI 1 (Kinnan)"), ("phase", "Main 1"),
                                 ("line", "AI 1 (Kinnan) played Forest")])
        self.assertTrue(rows[1].mine and not rows[8].mine)
        self.assertEqual(rows[1].turn, 1)

    def test_phase_label_table(self):
        for text, label in (("Karl's Upkeep step", "Upkeep"), ("Karl's Draw step", "Draw"), ("Karl's Declare Attackers Step", "Attackers"),
                            ("AI 1 (Kinnan)'s First Strike Damage Step", "Damage"), ("Karl's End step", "End step")):
            self.assertEqual(fl.phase_label(text), (label, True), text)
        self.assertEqual(fl.phase_label("Karl's Untap step"), (None, True))
        self.assertEqual(fl.phase_label("Karl cast Sol Ring"), (None, False))

    def test_the_recorded_sample_log_reads_cleanly(self):
        rows = fmt(load_log(), cards=CARDS + ["Otawara, Soaring City"])
        text = "\n".join(plain(r) for r in rows if r.kind == "line")
        for junk in ("(44)", "[Zone Changer", "picked {", "Add {G}", "Untap step"):
            self.assertNotIn(junk, text)
        self.assertIn("You cast Llanowar Elves", text)
        self.assertIn("AI 1 (Kinnan) cast Chrome Mox", text)
        self.assertEqual(sum(1 for r in rows if r.kind == "turn"), sum(1 for e in load_log() if e["type"] == "TURN"))


class WrapTests(unittest.TestCase):
    def test_wrapping_keeps_colours_and_card_names_whole(self):
        rows = fmt([{"type": "STACK_ADD", "text": "AI 1 (Kinnan) cast Kinnan, Bonder Prodigy targeting Llanowar Elves"}])
        lines = fl.wrap_segments(rows[0].segs, 12 * 6, Font())
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(sum(len(t) * 6 for t, _s, _r in line), 12 * 6 + 6 * 12)     # a single long word may overhang, nothing else
        joined = " ".join(t for line in lines for t, _s, _r in line)
        self.assertIn("Kinnan, Bonder Prodigy", " ".join(joined.split()))
        prodigy = [(s, r) for line in lines for t, s, r in line if t.strip() == "Prodigy"]
        self.assertEqual(prodigy, [("card", "Kinnan, Bonder Prodigy")])       # every piece of a card name points at the whole name

    def test_empty_row_is_one_empty_line(self):
        self.assertEqual(fl.wrap_segments([], 100, Font()), [[]])


if __name__ == "__main__":
    unittest.main()
