# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CHK1: the card checker's own set-up gaps (docs/incoming/deck_sweep/README.md)."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_check as cc


class ScriptLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "res", "cardsfolder")
        for folder, fn, text in (("m", "malakir_rebirth_malakir_mire.txt", "Name:Malakir Rebirth\nManaCost:B\nTypes:Instant\n"
                                  "ALTERNATE\nName:Malakir Mire\n"),
                                 ("m", "malakir_rebirth_wrong.txt", "Name:Something Else\n"),
                                 ("s", "sol_ring.txt", "Name:Sol Ring\nManaCost:1\nTypes:Artifact\n"),
                                 ("c", "chaos_warp.txt", "Name:Chaos Warp\nManaCost:2 R\nTypes:Instant\n")):
            os.makedirs(os.path.join(self.root, folder), exist_ok=True)
            with open(os.path.join(self.root, folder, fn), "w", encoding="utf-8") as f:
                f.write(text)
        ed = os.path.join(self.tmp, "res", "editions")
        os.makedirs(ed)
        with open(os.path.join(ed, "Secret Lair Drop.txt"), "w", encoding="utf-8") as f:
            f.write('[cards]\n741 R Chaos Warp @Zack Stella ${"flavorName": "Chaos Theory"}\n1 C Plain Card @Someone\n')
        cc._FLAVOR.clear()
        self.addCleanup(cc._FLAVOR.clear)

    def test_a_plain_name(self):
        self.assertIn("Name:Sol Ring", cc.find_script(self.root, "Sol Ring"))

    def test_a_modal_double_faced_card_is_found_by_its_front_face(self):
        self.assertIn("Name:Malakir Rebirth", cc.find_script(self.root, "Malakir Rebirth"))
        self.assertIsNone(cc.find_script(self.root, "Not A Card"))

    def test_a_flavor_name_reads_the_real_cards_script(self):
        self.assertEqual(cc.flavor_names(self.tmp).get("chaos theory"), "Chaos Warp")
        deck = os.path.join(self.tmp, "d.txt")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("Commander\n1 Sol Ring\n\nDeck\n1 Chaos Theory\n1 Malakir Rebirth\n")
        profiles = {p.deck_name: p for p in cc.load_profiles(deck, runtime=self.tmp)}
        self.assertTrue(all(p.found for p in profiles.values()), {n: p.found for n, p in profiles.items()})
        self.assertEqual(profiles["Chaos Theory"].name, "Chaos Warp")         # the set-up asks Forge for the real name


class BoardTests(unittest.TestCase):
    def board(self, level):
        prof = cc.Profile("Ram Through", "Name:Ram Through\nManaCost:1 G\nTypes:Sorcery\n")
        return dict(l.split("=", 1) for l in cc.setup_lines(prof, level))

    def test_every_board_has_a_creature_of_mine(self):
        for level in ("cast", "stack", "activate"):
            self.assertIn(cc.MY_CREATURE, self.board(level)["humanbattlefield"].split(";"), level)

    def test_three_lands_of_every_colour(self):
        lands = self.board("cast")["humanbattlefield"].split(";")
        for basic in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
            self.assertGreaterEqual(lands.count(basic), 3, basic)

    def test_targets_for_despark_and_victimize(self):
        # Round UI3: an opponent's permanent of mana value 4+ (Despark), creature cards in my graveyard (Victimize)
        board = self.board("cast")
        self.assertIn("Gilded Lotus", board["aibattlefield"].split(";"))
        self.assertEqual(board["humangraveyard"].split(";"), cc.MY_GRAVEYARD)

    def test_a_creature_of_mine_already_wears_an_aura(self):
        # Round CHK2: Daybreak Coronet enchants only a creature with another Aura attached to it
        mine = self.board("cast")["humanbattlefield"].split(";")
        self.assertIn("Trained Armodon|Id:%d" % cc.ENCHANTED_ID, mine)
        self.assertIn("Holy Strength|AttachedTo:%d" % cc.ENCHANTED_ID, mine)
        self.assertNotEqual(cc.ENCHANTED_ID, cc.AURA_HOST_ID)


class CombatTargetTests(unittest.TestCase):
    def test_a_channel_ability_that_targets_only_combatants_is_marked(self):
        eiganjo = ("Name:Eiganjo, Seat of the Empire\nTypes:Legendary Land\nA:AB$ DealDamage | PrecostDesc$ Channel | Cost$ 2 W "
                   "Discard<1/CARDNAME> | ActivationZone$ Hand | ValidTgts$ Creature.attacking,Creature.blocking | NumDmg$ 4\n")
        self.assertTrue(cc.Profile("Eiganjo", eiganjo).needs_combat)
        bolt = "Name:Lightning Bolt\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3\n"
        self.assertFalse(cc.Profile("Lightning Bolt", bolt).needs_combat)
        smite = "Name:Smite\nTypes:Instant\nA:SP$ Destroy | ValidTgts$ Creature.blockedBySource,Creature.blocked\n"
        self.assertFalse(cc.Profile("Smite", smite).needs_combat)       # "blocked" is not "blocking": only the two words count


class Chk2ChoiceTests(unittest.TestCase):
    def test_a_creature_without_flash_is_not_checked_on_the_stack(self):
        kitsa = ("Name:Kitsa, Otterball Elite\nManaCost:1 U\nTypes:Legendary Creature Otter Wizard\nK:Vigilance\n"
                 "A:AB$ CopySpellAbility | Cost$ 2 T | ValidTgts$ Instant.YouCtrl,Sorcery.YouCtrl | TargetType$ Spell\n")
        prof = cc.Profile("Kitsa", kitsa)
        self.assertTrue(prof.needs_stack)
        self.assertEqual(cc.levels_for(prof)[0], "cast")
        flash = cc.Profile("Spellstutter", "Name:Spellstutter Sprite\nTypes:Creature Faerie\nK:Flash\n"
                                           "T:Mode$ ChangesZone | Execute$ X\nSVar:X:DB$ Counter | TargetType$ Spell\n")
        self.assertEqual(cc.levels_for(flash)[0], "stack")

    def test_the_card_that_asks_is_picked_last(self):
        me = {"id": 1, "zones": {"battlefield": [], "hand": []}}
        lands = [{"id": 5, "name": "Izzet Boilerworks", "controller": 1, "selectable": True},
                 {"id": 9, "name": "Island", "controller": 1, "selectable": True}]
        me["zones"]["battlefield"] = lands
        state = {"me": 1, "players": [me, {"id": 2, "zones": {"battlefield": []}}],
                 "prompt": {"message": "Izzet Boilerworks (5) - Return a land you control to its owner's hand. Select a card",
                            "selecting": True, "ok": {}, "cancel": {}}}
        self.assertEqual(cc.next_move(state, [], {}), ("click", 9))


class DroppedFirstClickTests(unittest.TestCase):
    """Kinnan sweep (2 Oct): a counterspell's click sometimes landed on an old question and the bridge dropped it."""

    class Session:
        def __init__(self, drops):
            self.dropped, self.requests, self.state, self.exited, self.fatal = [], [], None, False, None
            self.state_version, self.drops = 0, drops

        def poll(self):
            pass

    def run_drive(self, drops):
        s = self.Session(drops)
        clicks = []

        def first():
            clicks.append(1)
            if len(clicks) <= s.drops:
                s.dropped.append({"t": "dropped"})
                s.state_version += 1

        checker = cc.Checker.__new__(cc.Checker)
        checker.s, checker.per_check = s, 5.0
        checker.drive(cc.Result("x", "stack"), first)
        return len(clicks)

    def test_a_dropped_first_click_is_sent_again(self):
        self.assertEqual(self.run_drive(0), 1)
        self.assertEqual(self.run_drive(1), 2)
        self.assertEqual(self.run_drive(9), 4)          # at most four tries


class CancelGraceTests(unittest.TestCase):
    def test_ok_gets_a_moment_to_light_before_cancel(self):
        # Aether Gale (2 Oct): six targets clicked, the next snapshot still had OK grey, and the checker cancelled the spell
        me = {"id": 1, "zones": {"battlefield": [], "hand": []}}
        state = {"me": 1, "players": [me, {"id": 2, "zones": {"battlefield": []}}],
                 "prompt": {"message": "Aether Gale - Return six target nonland permanents", "selecting": True,
                            "ok": {"enabled": False}, "cancel": {"enabled": True}}}
        mem = {}
        with mock.patch.object(cc.time, "time", return_value=100.0):
            self.assertIsNone(cc.next_move(state, [], mem))
        with mock.patch.object(cc.time, "time", return_value=100.0 + cc.CANCEL_GRACE + 0.01):
            self.assertEqual(cc.next_move(state, [], mem), ("cancel",))
        state["prompt"]["ok"]["enabled"] = True
        self.assertEqual(cc.next_move(state, [], mem), ("ok",))


if __name__ == "__main__":
    unittest.main()
