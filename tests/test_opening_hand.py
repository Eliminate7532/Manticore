# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests for opening-hand effects (Gemstone Caverns, Leylines), the choice of who goes first,
and the GUI step between the mulligan and turn 1.  Offline (Forge script fixtures + hand-written
Scryfall entries).
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import forge_rules as fr
import game_actions as ga
from forge_scripts import parse_script
from forge_store import ForgeStoreMixin
from game_state import GameState, Player
from test_forge_rules import CARDS, EXTRA

CAVERNS_TEXT = ("If Gemstone Caverns is in your opening hand and you're not the starting player, you may begin the game "
                "with Gemstone Caverns on the battlefield with a luck counter on it. If you do, exile a card from your hand.\n"
                "{T}: Add {C}. If Gemstone Caverns has a luck counter on it, instead add one mana of any color.")
LEYLINE_TEXT = ("If this card is in your opening hand, you may begin the game with it on the battlefield.\n"
                "Your opponents can't cast spells.")
CHANCELLOR_TEXT = ("You may reveal this card from your opening hand. If you do, at the beginning of your first upkeep, "
                   "create a 1/1 green Insect creature token.")
LEYLINE_SCRIPT = """Name:Leyline of Sanctity
ManaCost:2 W W
Types:Enchantment
K:MayEffectFromOpeningHand:FromOpeningHand
SVar:FromOpeningHand:DB$ ChangeZone | Defined$ Self | Origin$ Hand | Destination$ Battlefield | SpellDescription$ begin the game with it
Oracle:If this card is in your opening hand, you may begin the game with it on the battlefield.
"""
CHANCELLOR_SCRIPT = """Name:Chancellor of the Tangle
ManaCost:5 G G
Types:Creature Horror
PT:6/6
K:Vigilance
K:MayEffectFromOpeningHand:FromOpeningHand
SVar:FromOpeningHand:DB$ Reveal | Defined$ You | SubAbility$ Later
SVar:Later:DB$ Token | TokenScript$ g_1_1_insect
Oracle:You may reveal this card from your opening hand.
"""

SCRYFALL = dict(CARDS)
SCRYFALL.update({
    "gemstone caverns": {"name": "Gemstone Caverns", "type_line": "Legendary Land", "mana_cost": "", "layout": "normal",
                         "oracle_text": CAVERNS_TEXT, "color_identity": []},
    "leyline of sanctity": {"name": "Leyline of Sanctity", "type_line": "Enchantment", "mana_cost": "{2}{W}{W}",
                            "layout": "normal", "oracle_text": LEYLINE_TEXT, "color_identity": ["W"]},
    "chancellor of the tangle": {"name": "Chancellor of the Tangle", "type_line": "Creature — Horror",
                                 "mana_cost": "{5}{G}{G}", "layout": "normal", "oracle_text": CHANCELLOR_TEXT,
                                 "color_identity": ["G"]},
})
SCRYFALL.update(EXTRA)
EXTRA_SCRIPTS = {"leyline of sanctity": LEYLINE_SCRIPT, "chancellor of the tangle": CHANCELLOR_SCRIPT}


class TextStore:
    """Scryfall data only: the rules come from the card text."""
    last_error = None

    def peek_card(self, name):
        return SCRYFALL.get(re.sub(r"(?<!/) / (?!/)", " // ", name.strip().lower()))

    def peek_image_path(self, name):
        return None

    def get_image_path(self, name):
        return None

    def prefetch_cards(self, names):
        return 0


class ForgeStore(ForgeStoreMixin, TextStore):
    def peek_forge(self, name):
        script = EXTRA_SCRIPTS.get(name.strip().lower())
        if script:
            return parse_script(script)
        return super().peek_forge(name)


def new_game(hand, first=0, players=2):
    me = Player("Me", ["Forest"] * 30, commanders=[])
    gs = GameState(me, Player("AI", ["Island"] * 30))
    for i in range(players - 2):
        gs.players.append(Player(f"AI{i + 2}", ["Island"] * 30))
    me.hand = list(hand)
    gs.first_player_index = first
    return gs, me


class ScriptReadingTests(unittest.TestCase):
    def test_gemstone_caverns_script(self):
        ff = ForgeStore().peek_forge("Gemstone Caverns").face(0)
        self.assertEqual(fr.opening_hand_effects(ff),
                         [{"name": "FromOpeningHand", "not_first": True, "counter": ("LUCK", 1), "exile": 1}])

    def test_leyline_has_no_counter_or_exile_and_no_first_player_bar(self):
        ff = ForgeStore().peek_forge("Leyline of Sanctity").face(0)
        self.assertEqual(fr.opening_hand_effects(ff),
                         [{"name": "FromOpeningHand", "not_first": False, "counter": None, "exile": 0}])

    def test_a_reveal_effect_is_unsupported(self):
        ff = ForgeStore().peek_forge("Chancellor of the Tangle").face(0)
        with self.assertRaises(fr.ForgeUnsupported):
            fr.opening_hand_effects(ff)

    def test_ordinary_cards_have_none(self):
        self.assertEqual(fr.opening_hand_effects(ForgeStore().peek_forge("Sol Ring").face(0)), [])


class OpeningOptionsTests(unittest.TestCase):
    def check_both_stores(self, fn):
        for store in (ForgeStore(), TextStore()):
            with self.subTest(store=type(store).__name__):
                fn(store)

    def test_caverns_is_offered_only_when_you_are_not_the_starting_player(self):
        def go(store):
            gs, me = new_game(["Forest", "Gemstone Caverns", "Sol Ring"], first=1)
            opts = ga.opening_hand_options(gs, me, store)
            self.assertEqual([(o["name"], o["hand_index"], o["exile"], o["counter"]) for o in opts],
                             [("Gemstone Caverns", 1, 1, ("LUCK", 1))])
            gs.first_player_index = 0
            self.assertEqual(ga.opening_hand_options(gs, me, store), [])
        self.check_both_stores(go)

    def test_leyline_is_offered_even_to_the_starting_player(self):
        def go(store):
            gs, me = new_game(["Leyline of Sanctity", "Forest"], first=0)
            opts = ga.opening_hand_options(gs, me, store)
            self.assertEqual([o["name"] for o in opts], ["Leyline of Sanctity"])
            self.assertEqual((opts[0]["exile"], opts[0]["counter"]), (0, None))
        self.check_both_stores(go)

    def test_unmodelled_effects_are_flagged_manual(self):
        for store in (ForgeStore(), TextStore()):
            gs, me = new_game(["Chancellor of the Tangle"], first=0)
            opts = ga.opening_hand_options(gs, me, store)
            self.assertEqual(len(opts), 1)
            self.assertTrue(opts[0]["manual"])
            ok, msg = ga.begin_with_on_battlefield(gs, me, 0, store)
            self.assertFalse(ok)
            self.assertEqual(me.hand, ["Chancellor of the Tangle"])


class BeginWithTests(unittest.TestCase):
    def test_caverns_lands_with_a_luck_counter_and_exiles_the_chosen_card(self):
        for store in (ForgeStore(), TextStore()):
            gs, me = new_game(["Forest", "Gemstone Caverns", "Sol Ring", "Island"], first=1)
            ok, msg = ga.begin_with_on_battlefield(gs, me, 1, store, [2])
            self.assertTrue(ok, msg)
            self.assertEqual(me.hand, ["Forest", "Island"])
            self.assertEqual(me.exile, ["Sol Ring"])
            e = me.battlefield[0]
            self.assertEqual((e["name"], e["counters"], e["tapped"], e["summoning_sick"]),
                             ("Gemstone Caverns", {"LUCK": 1}, False, False))
            self.assertEqual(me.lands_played_this_turn, 0)                 # it is not your land drop
            if isinstance(store, ForgeStore):                              # the text fallback can't read the luck counter
                opts, _manual, _why = ga.tap_options(gs, me, e, store)
                self.assertEqual(len(opts), 5)                              # any colour, thanks to the counter

    def test_wrong_number_of_exiled_cards_changes_nothing(self):
        gs, me = new_game(["Gemstone Caverns", "Forest", "Island"], first=1)
        for pick in ([], [1, 2], [0]):
            ok, _ = ga.begin_with_on_battlefield(gs, me, 0, ForgeStore(), pick)
            self.assertFalse(ok)
        self.assertEqual(me.hand, ["Gemstone Caverns", "Forest", "Island"])
        self.assertEqual(me.battlefield, [])

    def test_caverns_alone_in_hand_needs_no_exile(self):
        gs, me = new_game(["Gemstone Caverns"], first=1)
        ok, msg = ga.begin_with_on_battlefield(gs, me, 0, ForgeStore(), [])
        self.assertTrue(ok, msg)
        self.assertEqual((me.hand, me.exile), ([], []))

    def test_leyline_enters_untapped(self):
        gs, me = new_game(["Leyline of Sanctity", "Forest"], first=0)
        ok, _ = ga.begin_with_on_battlefield(gs, me, 0, ForgeStore())
        self.assertTrue(ok)
        self.assertEqual([e["name"] for e in me.battlefield], ["Leyline of Sanctity"])
        self.assertEqual(me.hand, ["Forest"])


class BeginGameTests(unittest.TestCase):
    def test_going_first_skips_your_first_draw(self):
        gs, me = new_game(["Forest"] * 7, first=0)
        ga.begin_game(gs, ForgeStore(), 0)
        self.assertEqual(len(me.hand), 7)
        self.assertEqual(gs.active_player_index, 0)

    def test_going_second_draws_on_your_first_turn_and_the_opponent_does_not(self):
        gs, me = new_game(["Forest"] * 7, first=1)
        opp = gs.players[1]
        opp.hand = ["Island"] * 7
        ga.begin_game(gs, ForgeStore(), 0)
        self.assertEqual(len(opp.hand), 7)                 # the starting player skipped the draw
        self.assertEqual(len(me.hand), 8)
        self.assertEqual(gs.active_player_index, 0)
        self.assertEqual(gs.turn_number, 1)
        ga.end_turn(gs, ForgeStore(), 0)                   # the opponent now draws for their second turn
        self.assertEqual(len(opp.hand), 8)

    def test_pod_seating_runs_the_players_before_you(self):
        gs, me = new_game(["Forest"] * 7, first=2, players=4)
        for p in gs.players[1:]:
            p.hand = ["Island"] * 7
        ga.begin_game(gs, ForgeStore(), 0)
        self.assertEqual([len(p.hand) for p in gs.players], [8, 7, 7, 8])   # seats 2 (first) skips, 3 draws, me draws
        ga.end_turn(gs, ForgeStore(), 0)
        self.assertEqual([len(p.hand) for p in gs.players], [9, 8, 8, 9])   # seat 1 first turn; seats 2 and 3 second turns; my second turn


try:
    import pygame
    import table_gui as tg
except ImportError:
    pygame = None


@unittest.skipIf(pygame is None, "pygame is not installed")
class OpeningStepGuiTests(unittest.TestCase):
    def make(self, hand, first, store=None):
        me = Player("Me", ["Forest"] * 30, commanders=[])
        gs = GameState(me, Player("AI", ["Island"] * 30))
        gs.start_game()
        me.hand = list(hand)
        me.library = ["Forest"] * 20
        gui = tg.TableGUI(gs, store or ForgeStore(), first_player=first)
        gui.render()
        return gui

    def press(self, gui, name):
        gui.press_button(name)
        gui.render()

    def test_no_special_cards_goes_straight_to_the_game(self):
        gui = self.make(["Forest", "Island", "Sol Ring"], first=0)
        gui.keep_hand()
        self.assertEqual(gui.mode, "play")

    def test_caverns_when_going_first_is_not_offered(self):
        gui = self.make(["Forest", "Gemstone Caverns"], first=0)
        gui.keep_hand()
        self.assertEqual(gui.mode, "play")

    def test_caverns_when_going_second_lets_you_begin_with_it(self):
        gui = self.make(["Forest", "Gemstone Caverns", "Sol Ring"], first=1)
        gui.keep_hand()
        self.assertEqual(gui.mode, "opening")
        gui.render()
        self.assertEqual([spec[2] for spec in gui.button_specs()], ["open1", "start"])
        self.assertIn("Gemstone Caverns", gui.hint_text())
        gui.press_button("open1")
        self.assertIsNotNone(gui.menu)                                      # which card do you exile?
        self.assertEqual(sorted(label for label, _cb in gui.menu.items), ["Exile Forest", "Exile Sol Ring"])
        row = [label for label, _cb in gui.menu.items].index("Exile Sol Ring")
        gui.on_click(gui.menu.item_rects[row].center, 1)                    # a real click on the menu row
        self.assertEqual(gui.mode, "play")                                  # nothing else to use: the game starts
        me = gui.me
        self.assertEqual([e["name"] for e in me.battlefield], ["Gemstone Caverns"])
        self.assertEqual(me.battlefield[0]["counters"], {"LUCK": 1})
        self.assertEqual(me.exile, ["Sol Ring"])
        self.assertEqual(me.hand, ["Forest", "Forest"])                     # Forest kept, plus the turn-one draw

    def test_start_button_skips_the_effect(self):
        gui = self.make(["Forest", "Gemstone Caverns"], first=1)
        gui.keep_hand()
        gui.press_button("start")
        self.assertEqual(gui.mode, "play")
        self.assertIn("Gemstone Caverns", gui.me.hand)

    def test_turn_order_toggle_before_keeping(self):
        gui = self.make(["Forest", "Gemstone Caverns"], first=0)
        self.assertEqual([spec[2] for spec in gui.button_specs()], ["keep", "mulligan", "order"])
        gui.press_button("order")
        self.assertEqual(gui.gs.first_player_index, 1)
        self.assertIn("second", gui.hint_text())
        gui.render()
        gui.keep_hand()
        self.assertEqual(gui.mode, "opening")

    def test_random_first_player_is_used_when_not_fixed(self):
        seen = set()
        for _ in range(40):
            me = Player("Me", ["Forest"] * 30)
            gs = GameState(me, Player("AI", ["Island"] * 30))
            seen.add(tg.TableGUI(gs, TextStore()).gs.first_player_index)
        self.assertEqual(seen, {0, 1})


if __name__ == "__main__":
    unittest.main()
