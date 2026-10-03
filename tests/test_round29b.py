# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 29b: a flaky live test and the card-check sweep's false alarms.

  StaleQuestionTests   bridge_rules.commander_stranded ignores a "Priority" prompt in a snapshot where Forge is asking nothing
                       ("asking": false - the bridge keeps the last question's text between questions). Scenario 7 failed about
                       1 run in 4 on exactly that; the recorded failing run is the fixture
  SetupLinesTests      the card check's 'activate' set-up gives a planeswalker its loyalty and attaches an Aura to a creature
                       (10 of every night's card-check FAILs: "the card is not where the setup put it"), and puts the deck's
                       commander back in the command zone (War Room's NullPointerException on the commanders' colours)
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import bridge_rules as br
import card_check as cc

FIX = os.path.join(BASE, "tests", "fixtures", "soak")


def stranded(messages):
    return [f for f in br.check_stream(messages) if f["rule"] == "commander_stranded"]


def commander_state(seq, zone, prompt, asking=True, inp="InputPassPriority"):
    me = {"id": 0, "name": "Checker", "zones": {"battlefield": [], "exile": [], "graveyard": [], "command": [], "hand": []}}
    me["zones"][zone].append({"id": 221, "name": "Kinnan, Bonder Prodigy", "owner": 0, "commander": True})
    st = {"t": "state", "me": 0, "inputSeq": seq, "input": inp, "prompt": {"message": prompt}, "players": [me]}
    if asking is not None:
        st["asking"] = asking
    return st


class StaleQuestionTests(unittest.TestCase):
    def test_the_recorded_flaky_run_is_clean(self):
        msgs = br.load_stream(os.path.join(FIX, "scenario7_stale_priority.jsonl.gz"))
        self.assertEqual(stranded(msgs), [])

    def test_asking_false_with_the_old_priority_text_is_not_a_question(self):
        msgs = [commander_state(17, "battlefield", "Priority: Checker ... Stack: 1 to Resolve"),
                commander_state(18, "exile", "Priority: Checker ... Stack: 1 to Resolve", asking=False, inp=""),
                commander_state(19, "exile", "Kinnan: If a commander is in a graveyard or in exile ... move it to the command zone?",
                                inp="InputConfirm"),
                commander_state(21, "command", "Priority: Checker Turn: 3")]
        self.assertEqual(stranded(msgs), [])

    def test_a_real_priority_question_without_the_offer_still_fails(self):
        msgs = [commander_state(17, "battlefield", "Priority: Checker ... Stack: 1 to Resolve"),
                commander_state(18, "exile", "Priority: Checker ... Stack: 1 to Resolve", asking=False, inp=""),
                commander_state(19, "exile", "Priority: Checker Turn: 3")]
        found = stranded(msgs)
        self.assertEqual([f["severity"] for f in found], [br.FAIL])

    def test_old_recordings_without_asking_are_judged_as_before(self):
        msgs = [commander_state(17, "battlefield", "Priority: a", asking=None),
                commander_state(18, "exile", "Priority: b", asking=None)]
        self.assertEqual(len(stranded(msgs)), 1)

    def test_the_faulted_fixture_still_fails(self):
        msgs = br.load_stream(os.path.join(BASE, "tests", "fixtures", "bridge", "cmdzone_fault.jsonl.gz"))
        self.assertTrue(stranded(msgs))


PLANESWALKER = """Name:Tyvar, Jubilant Brawler
ManaCost:1 B G
Types:Legendary Planeswalker Tyvar
Loyalty:3
A:AB$ Untap | Cost$ AddCounter<1/LOYALTY> | Planeswalker$ True | SpellDescription$ Untap up to one target creature.
"""
AURA = """Name:Conviction
ManaCost:1 W
Types:Enchantment Aura
K:Enchant creature
A:AB$ ChangeZone | Cost$ W | Defined$ Self | Origin$ Battlefield | Destination$ Hand | SpellDescription$ Return CARDNAME to its owner's hand.
"""
ROCK = """Name:War Room
Types:Land
A:AB$ Mana | Cost$ T | Produced$ C | SpellDescription$ Add {C}.
"""


def lines_of(profile, level, commanders=()):
    return dict(l.split("=", 1) for l in cc.setup_lines(profile, level, commanders))


class SetupLinesTests(unittest.TestCase):
    def test_a_planeswalker_gets_its_loyalty(self):
        p = cc.Profile("Tyvar, Jubilant Brawler", PLANESWALKER)
        self.assertEqual(p.loyalty, "3")
        self.assertIn("Tyvar, Jubilant Brawler|Counters:LOYALTY=3", lines_of(p, "activate")["humanbattlefield"].split(";"))
        self.assertIn("Tyvar, Jubilant Brawler", lines_of(p, "cast")["humanhand"].split(";"))     # cast: from hand, as before

    def test_an_aura_is_attached_to_a_creature(self):
        p = cc.Profile("Conviction", AURA)
        self.assertTrue(p.is_aura)
        bf = lines_of(p, "activate")["humanbattlefield"].split(";")
        self.assertIn("%s|Id:%d" % (cc.AURA_HOST, cc.AURA_HOST_ID), bf)
        self.assertIn("Conviction|AttachedTo:%d" % cc.AURA_HOST_ID, bf)
        self.assertLess(bf.index("%s|Id:%d" % (cc.AURA_HOST, cc.AURA_HOST_ID)), bf.index("Conviction|AttachedTo:%d" % cc.AURA_HOST_ID))

    def test_other_permanents_are_unchanged(self):
        p = cc.Profile("War Room", ROCK)
        self.assertEqual((p.loyalty, p.is_aura), ("", False))
        self.assertIn("War Room", lines_of(p, "activate")["humanbattlefield"].split(";"))

    def test_the_decks_commander_goes_back_in_the_command_zone(self):
        p = cc.Profile("War Room", ROCK)
        self.assertEqual(lines_of(p, "cast", ["Adeline, Resplendent Cathar"])["humancommand"],
                         "Adeline, Resplendent Cathar|IsCommander")
        self.assertEqual(lines_of(p, "cast", ["Tymna the Weaver", "Thrasios, Triton Hero"])["humancommand"],
                         "Tymna the Weaver|IsCommander;Thrasios, Triton Hero|IsCommander")
        self.assertNotIn("humancommand", lines_of(p, "cast"))                 # no commanders known: as before

    def test_a_commander_being_checked_is_still_put_there_itself(self):
        p = cc.Profile("Kinnan, Bonder Prodigy", "Name:Kinnan, Bonder Prodigy\nTypes:Legendary Creature Human Druid\n",
                       commander=True)
        self.assertEqual(lines_of(p, "cast", ["Kinnan, Bonder Prodigy"])["humancommand"], "Kinnan, Bonder Prodigy|IsCommander")


if __name__ == "__main__":
    unittest.main()
