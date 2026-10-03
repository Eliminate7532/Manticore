# SPDX-License-Identifier: GPL-3.0-or-later
"""
Offline tests for the rules helpers. Run from the project folder with:
    python -m unittest discover -s tests -v
They use tests/fixtures/kinnan_cards.json (Scryfall data for the Kinnan sample deck),
so no network and no pygame are needed.
"""
import json
import os
import random
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import game_actions as ga
from game_state import GameState, Player
from mana_system import ManaPool, mana_options, parse_mana_cost, plan_payment

with open(os.path.join(HERE, "fixtures", "kinnan_cards.json"), encoding="utf-8") as f:
    CARDS = json.load(f)


class FakeStore:
    """Stands in for CardDataStore.peek_card using the saved Scryfall data."""
    def peek_card(self, name):
        key = re.sub(r"(?<!/) / (?!/)", " // ", name.strip().lower())
        return CARDS.get(key)


STORE = FakeStore()
KINNAN = "Kinnan, Bonder Prodigy"


def new_game(library=None, hand=None, battlefield=None, commanders=(KINNAN,)):
    me = Player("Me", library or ["Forest"] * 30, commanders=list(commanders))
    ai = Player("AI", ["Island"] * 30)
    gs = GameState(me, ai)
    me.hand = list(hand or [])
    for name in battlefield or []:
        ga.enter_battlefield(gs, me, name, STORE, tapped=False)
        me.battlefield[-1]["summoning_sick"] = False
    return gs, me


class ManaOptionTests(unittest.TestCase):
    def labels(self, name, face=0):
        opts, manual = mana_options(STORE.peek_card(name), face, ("G", "U"))
        return [o.label() for o in opts], manual

    def test_basic_and_dual(self):
        self.assertEqual(self.labels("Forest")[0], ["{G}"])
        self.assertEqual(self.labels("Tropical Island")[0], ["{G}", "{U}"])

    def test_fixed_multi_mana_and_damage(self):
        self.assertEqual(self.labels("Sol Ring")[0], ["{C}{C}"])
        self.assertEqual(self.labels("Ancient Tomb")[0], ["{C}{C}  (2 damage to you)"])

    def test_pain_and_life_costs(self):
        self.assertTrue(all("1 damage" in l for l in self.labels("City of Brass")[0]))
        self.assertTrue(all("pay 1 life" in l for l in self.labels("Mana Confluence")[0]))

    def test_commander_identity(self):
        self.assertEqual(self.labels("Command Tower")[0], ["{G}", "{U}"])

    def test_unsupported_abilities_are_reported_not_guessed(self):
        for name in ("Chrome Mox", "Fellwar Stone", "Springleaf Drum", "Mox Amber", "Gene Pollinator"):
            opts, manual = self.labels(name)
            self.assertEqual(opts, [], name)
            self.assertTrue(manual, name)

    def test_double_faced_land_faces(self):
        self.assertEqual(self.labels("Barkchannel Pathway / Tidechannel Pathway", 0)[0], ["{G}"])
        self.assertEqual(self.labels("Barkchannel Pathway / Tidechannel Pathway", 1)[0], ["{U}"])


class PlannerTests(unittest.TestCase):
    def sources(self, *names):
        out = []
        for i, n in enumerate(names):
            opts, _ = mana_options(CARDS[n.lower()], 0, ("G", "U"))
            out.append((i, "Land" in CARDS[n.lower()]["type_line"], list(opts)))
        return out

    def test_prefers_lands_over_rocks_and_avoids_pain(self):
        ok, plan = plan_payment({"generic": 1, "U": 1}, {}, self.sources("Sol Ring", "Island", "Forest"))
        self.assertTrue(ok)
        self.assertEqual(sorted(i for i, _ in plan), [1, 2])
        ok, plan = plan_payment({"generic": 3}, {}, self.sources("Forest", "City of Brass", "Sol Ring"))
        self.assertEqual(sorted(i for i, _ in plan), [0, 2])

    def test_uses_floating_mana_first(self):
        ok, plan = plan_payment({"generic": 1, "U": 2}, {"U": 1}, self.sources("Island", "Forest"))
        self.assertTrue(ok)
        self.assertEqual(len(plan), 2)
        ok, plan = plan_payment({"U": 1}, {"U": 1}, self.sources("Island"))
        self.assertEqual((ok, plan), (True, []))

    def test_impossible(self):
        self.assertEqual(plan_payment({"generic": 4}, {}, self.sources("Forest", "Island")), (False, None))
        self.assertEqual(plan_payment({"B": 1}, {}, self.sources("Forest", "Island")), (False, None))


class LandTests(unittest.TestCase):
    def test_land_drop_limit_and_override(self):
        gs, me = new_game(hand=["Forest", "Island", "Forest"])
        self.assertTrue(ga.play_from_hand(gs, me, 0, STORE)[0])
        ok, msg = ga.play_from_hand(gs, me, 0, STORE)
        self.assertFalse(ok)
        self.assertIn("already played a land", msg)
        self.assertTrue(ga.play_from_hand(gs, me, 0, STORE, free=True)[0])
        self.assertEqual(len(me.battlefield), 2)

    def test_conditional_enters_tapped(self):
        gs, me = new_game(hand=["Botanical Sanctum"])
        ga.play_from_hand(gs, me, 0, STORE)
        self.assertFalse(me.battlefield[-1]["tapped"])          # 0 other lands: untapped
        gs, me = new_game(hand=["Botanical Sanctum"], battlefield=["Forest", "Island", "Forest"])
        ga.play_from_hand(gs, me, 0, STORE)
        self.assertTrue(me.battlefield[-1]["tapped"])           # 3 other lands: tapped

    def test_opponent_count_condition(self):
        gs, me = new_game(hand=["Rejuvenating Springs"])
        ga.play_from_hand(gs, me, 0, STORE)
        self.assertTrue(me.battlefield[-1]["tapped"])           # only 1 opponent

    def test_shock_land_pays_life_or_enters_tapped(self):
        gs, me = new_game(hand=["Breeding Pool", "Breeding Pool", "Breeding Pool"])
        ga.play_from_hand(gs, me, 0, STORE)
        pool = me.battlefield[-1]
        self.assertEqual((me.life, pool["tapped"], pool["awaiting"]), (40, True, {"kind": "pay_or_tap", "life": 2}))
        self.assertEqual(ga.tap_options(gs, me, pool, STORE)[2], "it is waiting for your choice (click it to choose)")
        self.assertTrue(ga.resolve_prompt(gs, me, pool, True, STORE)[0])          # pay the 2 life
        self.assertEqual((me.life, pool["tapped"], "awaiting" in pool), (38, False, False))
        me.lands_played_this_turn = 0
        ga.play_from_hand(gs, me, 0, STORE)
        second = me.battlefield[-1]
        ga.resolve_prompt(gs, me, second, False, STORE)                            # don't pay
        self.assertEqual((me.life, second["tapped"], "awaiting" in second), (38, True, False))
        me.lands_played_this_turn = 0
        ga.play_from_hand(gs, me, 0, STORE, tapped=True)                           # playing it tapped asks nothing
        self.assertEqual((me.life, me.battlefield[-1]["tapped"], "awaiting" in me.battlefield[-1]), (38, True, False))

    def test_unloaded_card_data_is_reported(self):
        gs, me = new_game(hand=["Totally Unknown Card"])
        ok, msg = ga.play_from_hand(gs, me, 0, STORE)
        self.assertFalse(ok)
        self.assertIn("still loading", msg)


class CastingTests(unittest.TestCase):
    def test_auto_tap_pays_and_moves_card(self):
        gs, me = new_game(hand=["Chain of Vapor"], battlefield=["Island", "Forest"])
        ok, msg = ga.play_from_hand(gs, me, 0, STORE)
        self.assertTrue(ok, msg)
        self.assertEqual(me.graveyard, ["Chain of Vapor"])       # instants go to the graveyard
        self.assertEqual([e["tapped"] for e in me.battlefield], [True, False])

    def test_cannot_pay_changes_nothing(self):
        gs, me = new_game(hand=["Consecrated Sphinx"], battlefield=["Island", "Forest"])
        ok, msg = ga.play_from_hand(gs, me, 0, STORE)
        self.assertFalse(ok)
        self.assertIn("Can't pay", msg)
        self.assertEqual(me.hand, ["Consecrated Sphinx"])
        self.assertFalse(any(e["tapped"] for e in me.battlefield))

    def test_permanent_spell_enters_battlefield(self):
        gs, me = new_game(hand=["Sol Ring"], battlefield=["Island", "Forest"])
        self.assertTrue(ga.play_from_hand(gs, me, 0, STORE)[0])
        self.assertEqual(me.battlefield[-1]["name"], "Sol Ring")

    def test_free_spell_costs_nothing(self):
        gs, me = new_game(hand=["Lotus Petal"])
        self.assertTrue(ga.play_from_hand(gs, me, 0, STORE)[0])

    def test_kinnan_adds_a_bonus_mana(self):
        gs, me = new_game(battlefield=["Sol Ring", KINNAN])
        status, _, _ = ga.tap_for_mana(gs, me, me.battlefield[0], STORE)
        self.assertEqual(status, "done")
        self.assertEqual(dict(me.mana_pool.pool), {"C": 3})     # {C}{C} + one more {C}

    def test_no_kinnan_bonus_for_lands(self):
        gs, me = new_game(battlefield=["Forest", KINNAN])
        ga.tap_for_mana(gs, me, me.battlefield[0], STORE)
        self.assertEqual(dict(me.mana_pool.pool), {"G": 1})

    def test_summoning_sick_creature_cannot_tap(self):
        gs, me = new_game(hand=["Birds of Paradise"], battlefield=["Forest"])
        ga.play_from_hand(gs, me, 0, STORE)
        status, msg, _ = ga.tap_for_mana(gs, me, me.battlefield[-1], STORE)
        self.assertEqual(status, "fail")
        self.assertIn("summoning sick", msg)

    def test_lotus_petal_is_sacrificed(self):
        gs, me = new_game(battlefield=["Lotus Petal"])
        status, _, opts = ga.tap_for_mana(gs, me, me.battlefield[0], STORE)
        self.assertEqual(status, "choose")
        ga.tap_for_mana(gs, me, me.battlefield[0], STORE, option_index=1)
        self.assertEqual((me.battlefield, me.graveyard), ([], ["Lotus Petal"]))
        self.assertEqual(dict(me.mana_pool.pool), {"U": 1})

    def test_metalcraft_needs_three_artifacts(self):
        gs, me = new_game(battlefield=["Mox Opal", "Sol Ring"])
        self.assertEqual(ga.tap_for_mana(gs, me, me.battlefield[0], STORE)[0], "fail")
        gs, me = new_game(battlefield=["Mox Opal", "Sol Ring", "Mana Vault"])
        self.assertEqual(ga.tap_for_mana(gs, me, me.battlefield[0], STORE)[0], "choose")

    def test_pain_land_costs_life(self):
        gs, me = new_game(battlefield=["Ancient Tomb"])
        ga.tap_for_mana(gs, me, me.battlefield[0], STORE)
        self.assertEqual((me.life, dict(me.mana_pool.pool)), (38, {"C": 2}))


class CommanderTests(unittest.TestCase):
    def test_commander_tax_grows_and_commander_returns(self):
        gs, me = new_game(battlefield=["Forest", "Island", "Forest", "Island", "Forest"])
        ok, _ = ga.cast_commander(gs, me, 0, STORE)              # {G}{U}
        self.assertTrue(ok)
        self.assertEqual(me.commander_casts[KINNAN], 1)
        idx = [e["name"] for e in me.battlefield].index(KINNAN)
        ga.move_card(gs, me, "battlefield", idx, "graveyard", STORE)
        self.assertEqual((me.command_zone, me.graveyard), ([KINNAN], []))
        for e in me.battlefield:
            e["tapped"] = False
        ok, _ = ga.cast_commander(gs, me, 0, STORE)              # {2}{G}{U} = 4 mana
        self.assertTrue(ok)
        self.assertEqual(me.commander_casts[KINNAN], 2)
        self.assertEqual(sum(e["tapped"] for e in me.battlefield if e["name"] != KINNAN), 4)

    def test_tax_can_make_commander_unaffordable(self):
        gs, me = new_game(battlefield=["Forest", "Island"])
        me.commander_casts[KINNAN] = 1
        ok, msg = ga.cast_commander(gs, me, 0, STORE)
        self.assertFalse(ok)
        self.assertIn("Can't pay {2}", msg)                      # {G}{U} plus {2} tax, but only two lands


class FetchTests(unittest.TestCase):
    def test_fetch_finds_matching_lands_and_shuffles(self):
        library = ["Command Tower", "Tropical Island", "Forest", "Island", "Sol Ring", "Breeding Pool"]
        gs, me = new_game(library=library, battlefield=["Flooded Strand"])
        random.seed(1)
        ok, _, spec = ga.crack_fetch(gs, me, me.battlefield[0], STORE)
        self.assertTrue(ok)
        self.assertEqual((me.life, me.battlefield, me.graveyard), (39, [], ["Flooded Strand"]))
        names = [me.library[i] for i in ga.fetch_candidates(me, spec, STORE)]
        self.assertEqual(sorted(names), ["Breeding Pool", "Island", "Tropical Island"])   # Plains or Island
        idx = me.library.index("Island")
        ga.finish_fetch(gs, me, idx, spec, STORE)
        self.assertEqual(me.battlefield[0]["name"], "Island")
        self.assertEqual(len(me.library), 5)

    def test_non_fetch_is_rejected(self):
        gs, me = new_game(battlefield=["Forest"])
        self.assertFalse(ga.crack_fetch(gs, me, me.battlefield[0], STORE)[0])


class TurnAndMulliganTests(unittest.TestCase):
    def test_turn_flow_draws_untaps_and_skips_first_draw(self):
        gs, me = new_game(battlefield=["Forest", "Mana Vault"])
        gs.start_game()                                          # 7 cards each
        ga.start_turn(gs, STORE, 0)
        self.assertEqual(len(me.hand), 7)                        # starting player skips the first draw
        for e in me.battlefield:
            e["tapped"] = True
        ga.end_turn(gs, STORE)
        self.assertEqual((gs.turn_number, gs.active_player_index), (2, 0))
        self.assertEqual(len(me.hand), 8)
        self.assertEqual(len(gs.players[1].hand), 8)             # the AI drew on its turn
        forest, vault = me.battlefield
        self.assertFalse(forest["tapped"])
        self.assertTrue(vault["tapped"])                         # Mana Vault does not untap normally

    def test_mana_pool_empties_at_end_of_turn(self):
        gs, me = new_game()
        me.mana_pool.add("G", 3)
        ga.end_turn(gs, STORE)
        self.assertEqual(me.mana_pool.total(), 0)

    def test_free_first_mulligan(self):
        gs, me = new_game()
        gs.start_game()
        self.assertEqual(ga.take_mulligan(gs, me), 0)            # first mulligan is free
        self.assertEqual(len(me.hand), 7)
        self.assertEqual(ga.take_mulligan(gs, me), 1)            # second: one card to the bottom
        before = len(me.library)
        ga.bottom_cards(gs, me, [0])
        self.assertEqual((len(me.hand), len(me.library)), (6, before + 1))


class ZoneMoveTests(unittest.TestCase):
    def test_library_top_and_bottom(self):
        gs, me = new_game(library=["A", "B"], hand=["Sol Ring", "Forest"])
        ga.move_card(gs, me, "hand", 0, "library", STORE, to_top=True)
        ga.move_card(gs, me, "hand", 0, "library", STORE, to_top=False)
        self.assertEqual(me.library, ["Sol Ring", "A", "B", "Forest"])

    def test_card_moves_are_logged(self):
        gs, me = new_game(hand=["Forest"])
        ga.move_card(gs, me, "hand", 0, "exile", STORE)
        self.assertIn("Forest", gs.log[-1])
        self.assertEqual(me.exile, ["Forest"])

    def test_stale_index_is_refused(self):
        gs, me = new_game(hand=[])
        self.assertFalse(ga.move_card(gs, me, "hand", 3, "exile", STORE)[0])


class ParseCostTests(unittest.TestCase):
    def test_pool_pay_keeps_coloured_mana_for_later(self):
        pool = ManaPool()
        pool.add("C", 2); pool.add("U", 1); pool.add("G", 1)
        self.assertTrue(pool.pay(parse_mana_cost("{2}")))
        self.assertEqual(dict((k, v) for k, v in pool.pool.items() if v), {"U": 1, "G": 1})


if __name__ == "__main__":
    unittest.main()
