# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests that game_actions gets its rules from Forge card scripts when it has them, and that the
answers agree with the older Scryfall-text parser wherever both understand a card.
Offline: Scryfall data and Forge scripts both come from tests/fixtures/.
    python -m unittest discover -s tests -v
"""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import forge_rules as fr
import game_actions as ga
from forge_store import ForgeStoreMixin
from game_state import GameState, Player
from mana_system import mana_options

with open(os.path.join(HERE, "fixtures", "kinnan_cards.json"), encoding="utf-8") as f:
    CARDS = json.load(f)
KINNAN = "Kinnan, Bonder Prodigy"


# A few cards that are not in the saved deck, written by hand in Scryfall's shape for these tests.
EXTRA = {
    "evolving wilds": {"name": "Evolving Wilds", "type_line": "Land", "mana_cost": "", "layout": "normal",
                       "oracle_text": "{T}, Sacrifice this land: Search your library for a basic land card, "
                                      "put it onto the battlefield tapped, then shuffle.", "color_identity": []},
    "simic guildgate": {"name": "Simic Guildgate", "type_line": "Land — Gate", "mana_cost": "", "layout": "normal",
                        "oracle_text": "Simic Guildgate enters tapped.\n{T}: Add {G} or {U}.", "color_identity": ["G", "U"]},
    "snow-covered forest": {"name": "Snow-Covered Forest", "type_line": "Basic Snow Land — Forest", "mana_cost": "",
                            "layout": "normal", "oracle_text": "({T}: Add {G}.)", "color_identity": ["G"]},
}


class TextStore:
    """Scryfall data only: every rule comes from the card text (the fallback path)."""
    def peek_card(self, name):
        key = re.sub(r"(?<!/) / (?!/)", " // ", name.strip().lower())
        return CARDS.get(key) or EXTRA.get(key)


class ForgeStore(ForgeStoreMixin, TextStore):
    """Scryfall data plus Forge scripts."""


FORGE, TEXT = ForgeStore(), TextStore()


def new_game(store=FORGE, battlefield=(), hand=(), library=None, opp_battlefield=(), commanders=(KINNAN,)):
    me = Player("Me", library or ["Forest"] * 30, commanders=list(commanders))
    ai = Player("AI", ["Island"] * 30)
    gs = GameState(me, ai)
    me.hand = list(hand)
    for player, names in ((me, battlefield), (ai, opp_battlefield)):
        for name in names:
            ga.enter_battlefield(gs, player, name, store, tapped=False)
            player.battlefield[-1]["summoning_sick"] = False
    return gs, me, ai


def labels(name, store=FORGE, face=0, battlefield=(), opp_battlefield=(), counters=None, **kw):
    """Option labels for a permanent placed on an otherwise-controlled battlefield."""
    gs, me, ai = new_game(store, battlefield=battlefield, opp_battlefield=opp_battlefield, **kw)
    ga.enter_battlefield(gs, me, name, store, tapped=False, face=face)
    entry = me.battlefield[-1]
    entry["summoning_sick"] = False
    if counters:
        entry["counters"] = counters
    opts, manual, reason = ga.tap_options(gs, me, entry, store)
    return [o.label() for o in opts], manual, reason


class EntryPolicyTests(unittest.TestCase):
    def enter(self, name, lands=0, face=0, players=2):
        me = Player("Me", ["Forest"] * 10)
        gs = GameState(me, Player("P1", ["Island"] * 10))
        for i in range(2, players):
            gs.players.append(Player(f"P{i}", ["Island"] * 10))
        for _ in range(lands):
            ga.enter_battlefield(gs, me, "Forest", FORGE, tapped=False)
        life = me.life
        entry, note = ga.enter_battlefield(gs, me, name, FORGE, face=face)
        return entry["tapped"], life - me.life

    def test_plain_lands_enter_untapped(self):
        self.assertEqual(self.enter("Forest"), (False, 0))
        self.assertEqual(self.enter("Tropical Island"), (False, 0))
        self.assertEqual(self.enter("Command Tower"), (False, 0))

    def test_always_tapped(self):
        self.assertEqual(self.enter("Simic Guildgate"), (True, 0))

    def test_botanical_sanctum_is_untapped_only_with_two_or_fewer_other_lands(self):
        self.assertEqual(self.enter("Botanical Sanctum", lands=0)[0], False)
        self.assertEqual(self.enter("Botanical Sanctum", lands=2)[0], False)
        self.assertEqual(self.enter("Botanical Sanctum", lands=3)[0], True)

    def test_rejuvenating_springs_needs_two_opponents(self):
        self.assertEqual(self.enter("Rejuvenating Springs", players=2)[0], True)
        self.assertEqual(self.enter("Rejuvenating Springs", players=3)[0], False)
        self.assertEqual(self.enter("Rejuvenating Springs", players=4)[0], False)

    def test_shock_lands_ask_whether_to_pay(self):
        for name, face, life in (("Breeding Pool", 0, 2), ("Sink into Stupor // Soporific Springs", 1, 3)):
            gs, me, _ = new_game()
            entry, note = ga.enter_battlefield(gs, me, name, FORGE, face=face)
            self.assertEqual(entry["awaiting"], {"kind": "pay_or_tap", "life": life}, name)
            self.assertEqual((entry["tapped"], me.life), (True, 40), name)          # nothing is paid until you say so
            self.assertIn(f"pay {life} life", note)
            self.assertTrue(ga.resolve_prompt(gs, me, entry, True, FORGE)[0])
            self.assertEqual((entry["tapped"], me.life, "awaiting" in entry), (False, 40 - life, False), name)
            gs, me, _ = new_game()
            entry, _ = ga.enter_battlefield(gs, me, name, FORGE, face=face)
            ga.resolve_prompt(gs, me, entry, False, FORGE)
            self.assertEqual((entry["tapped"], me.life), (True, 40), name)

    def test_forced_tapped_pays_nothing(self):
        gs, me, _ = new_game()
        ga.enter_battlefield(gs, me, "Breeding Pool", FORGE, tapped=True)
        self.assertEqual((me.battlefield[-1]["tapped"], me.life), (True, 40))

    def test_matches_the_text_parser_for_every_land_in_the_saved_deck(self):
        for lands in (0, 2, 3, 6):
            for name, info in CARDS.items():
                if "Land" not in info["type_line"] or fr.face_of(FORGE, name) is None:
                    continue
                results = []
                for store in (FORGE, TEXT):
                    gs, me, _ = new_game(store, battlefield=["Forest"] * lands)
                    entry, _note = ga.enter_battlefield(gs, me, name, store)
                    results.append((entry["tapped"], 40 - me.life))
                self.assertEqual(results[0], results[1], f"{name} with {lands} lands")


class FetchTests(unittest.TestCase):
    LIB = ["Forest", "Island", "Breeding Pool", "Tropical Island", "Sol Ring", "Command Tower",
           "Barkchannel Pathway // Tidechannel Pathway", "Sink into Stupor // Soporific Springs"]

    def spec_and_names(self, land, lib=None):
        gs, me, _ = new_game(library=lib or self.LIB, battlefield=[land])
        spec = ga.fetch_spec(FORGE, me.battlefield[0])
        return spec, sorted(me.library[i] for i in ga.fetch_candidates(me, spec, FORGE)) if spec else None

    def test_wooded_foothills(self):
        spec, names = self.spec_and_names("Wooded Foothills")
        self.assertEqual((spec["life"], spec["what"], spec["tapped"]), (1, "Mountain or Forest", False))
        self.assertEqual(names, ["Breeding Pool", "Forest", "Tropical Island"])     # Mountain or Forest by land type

    def test_misty_rainforest_finds_forest_or_island_types(self):
        spec, names = self.spec_and_names("Misty Rainforest")
        self.assertEqual(spec["what"], "Forest or Island")
        self.assertEqual(names, ["Breeding Pool", "Forest", "Island", "Tropical Island"])

    def test_evolving_wilds_fetches_only_basics_tapped(self):
        spec, names = self.spec_and_names("Evolving Wilds", self.LIB + ["Snow-Covered Forest"])
        self.assertEqual((spec["life"], spec["what"], spec["tapped"]), (0, "basic land", True))
        self.assertEqual(names, ["Forest", "Island", "Snow-Covered Forest"])

    def test_a_land_that_is_only_a_land_on_its_back_face_is_not_found(self):
        _spec, names = self.spec_and_names("Misty Rainforest")
        self.assertNotIn("Sink into Stupor // Soporific Springs", names)     # an Instant while in the library

    def test_normal_lands_and_mana_rocks_are_not_fetchlands(self):
        for name in ("Forest", "Breeding Pool", "Sol Ring", "City of Brass", "Waterlogged Grove"):
            gs, me, _ = new_game(battlefield=[name])
            self.assertIsNone(ga.fetch_spec(FORGE, me.battlefield[0]), name)

    def test_agrees_with_the_text_parser(self):
        for land in ("Wooded Foothills", "Misty Rainforest", "Windswept Heath", "Evolving Wilds"):
            gs, me, _ = new_game(library=self.LIB, battlefield=[land])
            a, b = ga.fetch_spec(FORGE, me.battlefield[0]), ga.fetch_spec(TEXT, me.battlefield[0])
            self.assertEqual((a["life"], a["tapped"]), (b["life"], b["tapped"]), land)
            self.assertEqual(ga.fetch_candidates(me, a, FORGE), ga.fetch_candidates(me, b, TEXT), land)

    def test_crack_pays_life_and_reports_it(self):
        gs, me, _ = new_game(library=self.LIB, battlefield=["Wooded Foothills"])
        ok, msg, spec = ga.crack_fetch(gs, me, me.battlefield[0], FORGE)
        self.assertTrue(ok)
        self.assertEqual(me.life, 39)
        self.assertIn("Paid 1 life (now 39)", msg)
        self.assertIn("Mountain or Forest", msg)
        self.assertEqual(me.graveyard, ["Wooded Foothills"])

    def test_tapped_fetchland_cannot_be_cracked(self):
        gs, me, _ = new_game(library=self.LIB, battlefield=["Wooded Foothills"])
        me.battlefield[0]["tapped"] = True
        self.assertFalse(ga.crack_fetch(gs, me, me.battlefield[0], FORGE)[0])
        self.assertEqual(me.life, 40)


class ManaTests(unittest.TestCase):
    def test_simple_sources(self):
        self.assertEqual(labels("Sol Ring")[0], ["{C}{C}"])
        self.assertEqual(labels("Forest")[0], ["{G}"])
        self.assertEqual(labels("Tropical Island")[0], ["{G}", "{U}"])
        self.assertEqual(labels("Llanowar Elves")[0], ["{G}"])
        self.assertEqual(labels("Mana Vault")[0], ["{C}{C}{C}"])
        self.assertEqual(labels("Birds of Paradise")[0], ["{W}", "{U}", "{B}", "{R}", "{G}"])
        self.assertEqual(labels("Barkchannel Pathway // Tidechannel Pathway", face=0)[0], ["{G}"])
        self.assertEqual(labels("Barkchannel Pathway // Tidechannel Pathway", face=1)[0], ["{U}"])

    def test_costs_and_damage(self):
        self.assertEqual(labels("Ancient Tomb")[0], ["{C}{C}  (2 damage to you)"])
        self.assertTrue(all("1 damage" in l for l in labels("City of Brass")[0]))          # from its tap trigger
        self.assertEqual(len(labels("City of Brass")[0]), 5)
        self.assertTrue(all("pay 1 life" in l for l in labels("Mana Confluence")[0]))
        self.assertEqual(labels("Waterlogged Grove")[0], ["{G}  (pay 1 life)", "{U}  (pay 1 life)"])
        self.assertEqual(labels("Lotus Petal")[0][0], "{W}  (sacrifice)")

    def test_two_separate_abilities(self):
        self.assertEqual(labels("Yavimaya Coast")[0], ["{C}", "{G}  (1 damage to you)", "{U}  (1 damage to you)"])
        self.assertEqual(labels("Talisman of Curiosity")[0], ["{C}", "{G}  (1 damage to you)", "{U}  (1 damage to you)"])
        self.assertEqual(labels("Tarnished Citadel")[0][0], "{C}")
        self.assertEqual(len(labels("Tarnished Citadel")[0]), 6)

    def test_commander_identity(self):
        self.assertEqual(labels("Command Tower")[0], ["{U}", "{G}"])           # W U B R G order
        self.assertEqual(labels("Arcane Signet")[0], ["{U}", "{G}"])
        self.assertEqual(labels("Command Tower", commanders=())[0], [])       # no commander: nothing to choose

    def test_metalcraft(self):
        opts, _, reason = labels("Mox Opal")
        self.assertEqual(opts, [])
        self.assertIn("metalcraft", reason)
        opts, _, _ = labels("Mox Opal", battlefield=["Sol Ring", "Mana Vault"])       # plus the Mox itself = three
        self.assertEqual(len(opts), 5)

    def test_restricted_mana_is_flagged_and_left_out_of_auto_payment(self):
        gs, me, _ = new_game()
        ga.enter_battlefield(gs, me, "Delighted Halfling", FORGE, tapped=False)
        e = me.battlefield[0]
        e["summoning_sick"] = False
        shown = ga.tap_options(gs, me, e, FORGE)[0]
        self.assertEqual([o.label() for o in shown][0], "{C}")
        self.assertTrue(any(o.restrict for o in shown))
        planned = ga.tap_options(gs, me, e, FORGE, for_planning=True)[0]
        self.assertEqual([o.label() for o in planned], ["{C}"])

    def test_gemstone_caverns_gives_any_colour_only_with_a_luck_counter(self):
        self.assertEqual(labels("Gemstone Caverns")[0], ["{C}"])
        self.assertEqual(len(labels("Gemstone Caverns", counters={"luck": 1})[0]), 5)

    def test_reflected_mana(self):
        opts, _, reason = labels("Fellwar Stone")
        self.assertEqual(opts, [])
        self.assertIn("can't make mana", reason)
        self.assertEqual(labels("Fellwar Stone", opp_battlefield=["Tropical Island", "Sol Ring"])[0], ["{U}", "{G}"])
        self.assertEqual(labels("Mox Amber")[0], [])                                    # no legendary creature yet
        # Kinnan is legendary (so Mox Amber makes {U} or {G}) and Kinnan's own trigger adds one more of it
        self.assertEqual(labels("Mox Amber", battlefield=[KINNAN])[0], ["{U}{U}  (+{U} bonus)", "{G}{G}  (+{G} bonus)"])

    def test_tapping_another_permanent_is_a_cost(self):
        opts, _, reason = labels("Springleaf Drum")
        self.assertEqual(opts, [])
        self.assertIn("nothing untapped", reason)
        opts, _, _ = labels("Springleaf Drum", battlefield=["Llanowar Elves"])
        self.assertEqual(len(opts), 5)
        self.assertTrue(all("tap Llanowar Elves" in o for o in opts))
        self.assertEqual(len(labels("Gene Pollinator", battlefield=["Forest"])[0]), 5)      # any permanent
        self.assertEqual(labels("Springleaf Drum", battlefield=["Forest"])[0], [])           # Forest isn't a creature

    def test_summoning_sick_creatures_and_hasty_rules(self):
        gs, me, _ = new_game()
        ga.enter_battlefield(gs, me, "Llanowar Elves", FORGE)
        self.assertEqual(ga.tap_options(gs, me, me.battlefield[0], FORGE)[2], "summoning sick")

    def test_kinnan_adds_one_more_mana_of_a_produced_type_for_nonland_permanents(self):
        self.assertEqual(labels("Sol Ring", battlefield=[KINNAN])[0], ["{C}{C}{C}  (+{C} bonus)"])
        self.assertEqual(labels("Llanowar Elves", battlefield=[KINNAN])[0], ["{G}{G}  (+{G} bonus)"])
        self.assertEqual(labels("Forest", battlefield=[KINNAN])[0], ["{G}"])                 # lands get no bonus

    def test_mana_from_hand(self):
        gs, me, _ = new_game(hand=["Elvish Spirit Guide", "Sol Ring"])
        opts = ga.hand_mana_options(gs, me, 0, FORGE)
        self.assertEqual([o.label() for o in opts], ["{G}  (exile it from your hand)"])
        self.assertEqual(ga.hand_mana_options(gs, me, 1, FORGE), [])
        ok, _ = ga.hand_mana(gs, me, 0, FORGE)
        self.assertTrue(ok)
        self.assertEqual((me.mana_pool.pool["G"], me.exile, me.hand), (1, ["Elvish Spirit Guide"], ["Sol Ring"]))

    def test_without_a_script_the_text_parser_is_used(self):
        self.assertEqual(labels("Sol Ring", store=TEXT)[0], ["{C}{C}"])
        opts, manual, _ = labels("Chrome Mox", store=TEXT)
        self.assertEqual((opts, len(manual)), ([], 1))

    def test_a_script_we_cannot_read_falls_back_to_the_text(self):
        class Odd(ForgeStore):
            def peek_forge(self, name):
                card = super().peek_forge(name)
                if card and card.name == "Sol Ring":
                    card.faces[0].abilities[0]["Mystery"] = "true"        # a parameter the simulator doesn't know
                return card
        store = Odd()
        self.assertEqual(labels("Sol Ring", store=store)[0], ["{C}{C}"])

    def test_agrees_with_the_text_parser_wherever_both_understand_a_card(self):
        checked = 0
        for name, info in CARDS.items():
            if fr.face_of(FORGE, name) is None:
                continue
            faces = len(info.get("card_faces") or [0]) if info.get("layout") == "modal_dfc" else 1
            for face in range(faces):
                text_opts, text_manual = mana_options(info, face, ("G", "U"))
                if text_manual or not text_opts:
                    continue
                forge_opts = ga._mana_abilities(*self.ctx(name, face))[0]
                key = lambda o: (tuple(sorted(o.produces.items())), o.life_cost, o.damage, o.sacrifice, o.condition)
                self.assertEqual(sorted(map(key, forge_opts)), sorted(map(key, text_opts)), f"{name} face {face}")
                checked += 1
        self.assertGreater(checked, 20)

    @staticmethod
    def ctx(name, face):
        gs, me, _ = new_game()
        return gs, me, {"name": name, "face": face, "counters": {}}, FORGE


class PaymentTests(unittest.TestCase):
    def test_springleaf_drum_pays_using_a_creature_that_makes_no_mana(self):
        gs, me, _ = new_game(battlefield=["Springleaf Drum", "Kinnan, Bonder Prodigy"])
        me.battlefield[1]["summoning_sick"] = True                       # Kinnan can't tap for mana but can pay the cost
        ok, msg = ga.pay_mana_cost(gs, me, {"U": 1}, FORGE)
        self.assertTrue(ok, msg)
        self.assertTrue(me.battlefield[0]["tapped"] and me.battlefield[1]["tapped"])
        self.assertEqual(me.mana_pool.pool["U"], 1)                          # Kinnan's bonus mana is left over

    def test_a_mana_creature_can_be_the_cost_when_nothing_else_will_do(self):
        gs, me, _ = new_game(battlefield=["Springleaf Drum", "Llanowar Elves"])
        ok, msg = ga.pay_mana_cost(gs, me, {"U": 1}, FORGE)       # Elves could make {G}, but {U} needs the Drum
        self.assertTrue(ok, msg)
        self.assertTrue(all(e["tapped"] for e in me.battlefield))

    def test_a_creature_cannot_be_both_the_cost_and_a_mana_source(self):
        gs, me, _ = new_game(battlefield=["Springleaf Drum", "Llanowar Elves"])
        ok, _ = ga.pay_mana_cost(gs, me, {"U": 1, "G": 1}, FORGE)  # would need Elves twice
        self.assertFalse(ok)
        self.assertFalse(any(e["tapped"] for e in me.battlefield))

    def test_sacrifice_cost_does_not_confuse_the_tap_targets(self):
        gs, me, _ = new_game(battlefield=["Lotus Petal", "Springleaf Drum", "Llanowar Elves", "Forest"])
        me.battlefield[2]["summoning_sick"] = True
        ok, msg = ga.pay_mana_cost(gs, me, {"W": 1, "U": 1, "G": 1}, FORGE)
        self.assertTrue(ok, msg)
        self.assertEqual(me.graveyard, ["Lotus Petal"])
        self.assertEqual([e["name"] for e in me.battlefield], ["Springleaf Drum", "Llanowar Elves", "Forest"])
        self.assertTrue(all(e["tapped"] for e in me.battlefield))

    def test_two_tap_cost_rocks_cannot_share_one_creature(self):
        gs, me, _ = new_game(battlefield=["Springleaf Drum", "Gene Pollinator", "Kinnan, Bonder Prodigy"])
        for e in me.battlefield:
            e["summoning_sick"] = True
        me.battlefield[1]["summoning_sick"] = True
        # Drum and Pollinator both need something to tap; Kinnan alone can serve only one of them.
        ok, _ = ga.pay_mana_cost(gs, me, {"U": 1, "G": 1}, FORGE)
        self.assertFalse(ok)
        self.assertFalse(any(e["tapped"] for e in me.battlefield))       # nothing changed

    def test_planner_prefers_lands_and_avoids_pain(self):
        gs, me, _ = new_game(battlefield=["Forest", "Yavimaya Coast", "Sol Ring"])
        ok, _ = ga.pay_mana_cost(gs, me, {"G": 1}, FORGE)
        self.assertTrue(ok)
        self.assertEqual(me.life, 40)
        self.assertTrue(me.battlefield[0]["tapped"])

    def test_pain_land_damage_is_taken_when_needed(self):
        gs, me, _ = new_game(battlefield=["Ancient Tomb"])
        ok, _ = ga.pay_mana_cost(gs, me, {"generic": 2}, FORGE)
        self.assertTrue(ok)
        self.assertEqual(me.life, 38)

    def test_kinnan_bonus_mana_stays_in_the_pool(self):
        gs, me, _ = new_game(battlefield=[KINNAN, "Sol Ring"])
        ok, _ = ga.pay_mana_cost(gs, me, {"generic": 2}, FORGE)          # Sol Ring makes {C}{C}{C}: one is left over
        self.assertTrue(ok)
        self.assertEqual(me.mana_pool.pool["C"], 1)


class EntryPromptTests(unittest.TestCase):
    def test_chrome_mox_prompt_lists_only_nonartifact_nonland_cards(self):
        gs, me, _ = new_game(hand=["Forest", "Sol Ring", "Elvish Spirit Guide", "Llanowar Elves"])
        ga.enter_battlefield(gs, me, "Chrome Mox", FORGE)
        mox = me.battlefield[0]
        self.assertEqual(mox["awaiting"]["kind"], "imprint")
        choices = ga.prompt_choices(me, mox["awaiting"], FORGE)
        self.assertEqual([n for _i, n in choices], ["Elvish Spirit Guide", "Llanowar Elves"])
        self.assertEqual(ga.tap_options(gs, me, mox, FORGE)[2], "it is waiting for your choice (click it to choose)")

    def test_imprint_moves_the_card_to_exile_and_gives_its_colour(self):
        gs, me, _ = new_game(hand=["Elvish Spirit Guide"])
        ga.enter_battlefield(gs, me, "Chrome Mox", FORGE)
        mox = me.battlefield[0]
        ok, _ = ga.resolve_prompt(gs, me, mox, 0, FORGE)
        self.assertTrue(ok)
        self.assertEqual((me.hand, me.exile, mox["imprinted"], "awaiting" in mox), ([], ["Elvish Spirit Guide"], ["Elvish Spirit Guide"], False))
        self.assertEqual([o.label() for o in ga.tap_options(gs, me, mox, FORGE)[0]], ["{G}"])

    def test_declining_the_imprint_leaves_a_mox_that_makes_nothing(self):
        gs, me, _ = new_game(hand=["Elvish Spirit Guide"])
        ga.enter_battlefield(gs, me, "Chrome Mox", FORGE)
        mox = me.battlefield[0]
        ga.resolve_prompt(gs, me, mox, None, FORGE)
        self.assertEqual(me.hand, ["Elvish Spirit Guide"])
        self.assertEqual(ga.tap_options(gs, me, mox, FORGE)[0], [])

    def test_no_prompt_when_there_is_nothing_to_imprint(self):
        gs, me, _ = new_game(hand=["Forest", "Sol Ring"])
        _entry, note = ga.enter_battlefield(gs, me, "Chrome Mox", FORGE)
        self.assertNotIn("awaiting", me.battlefield[0])
        self.assertIn("nothing to imprint", note)

    def test_a_card_that_is_not_a_choice_cannot_be_imprinted(self):
        gs, me, _ = new_game(hand=["Sol Ring", "Elvish Spirit Guide"])
        ga.enter_battlefield(gs, me, "Chrome Mox", FORGE)
        ok, _ = ga.resolve_prompt(gs, me, me.battlefield[0], 0, FORGE)          # Sol Ring
        self.assertFalse(ok)
        self.assertIn("awaiting", me.battlefield[0])

    def test_mox_diamond_discard_a_land_or_it_dies(self):
        gs, me, _ = new_game(hand=["Forest", "Sol Ring"])
        ga.enter_battlefield(gs, me, "Mox Diamond", FORGE)
        mox = me.battlefield[0]
        self.assertEqual(mox["awaiting"]["kind"], "discard_or_bin")
        ok, _ = ga.resolve_prompt(gs, me, mox, 0, FORGE)
        self.assertTrue(ok)
        self.assertEqual((me.graveyard, me.hand, len(me.battlefield)), (["Forest"], ["Sol Ring"], 1))

        gs, me, _ = new_game(hand=["Forest"])
        ga.enter_battlefield(gs, me, "Mox Diamond", FORGE)
        ga.resolve_prompt(gs, me, me.battlefield[0], None, FORGE)
        self.assertEqual((me.graveyard, me.battlefield, me.hand), (["Mox Diamond"], [], ["Forest"]))

    def test_mox_diamond_with_no_land_in_hand_goes_straight_to_the_graveyard(self):
        gs, me, _ = new_game(hand=["Sol Ring"])
        _entry, note = ga.enter_battlefield(gs, me, "Mox Diamond", FORGE)
        self.assertEqual((me.battlefield, me.graveyard), ([], ["Mox Diamond"]))
        self.assertIn("graveyard", note)

    def test_a_choice_for_a_permanent_that_left_the_battlefield_is_refused(self):
        gs, me, _ = new_game(hand=["Elvish Spirit Guide"])
        ga.enter_battlefield(gs, me, "Chrome Mox", FORGE)
        entry = me.battlefield[0]
        ga.move_card(gs, me, "battlefield", 0, "hand", FORGE)
        ok, msg = ga.resolve_prompt(gs, me, entry, 0, FORGE)
        self.assertFalse(ok)
        self.assertIn("no longer on the battlefield", msg)
        self.assertEqual((me.exile, me.hand), ([], ["Elvish Spirit Guide", "Chrome Mox"]))

    def test_undo_style_deepcopy_keeps_the_prompt(self):
        import copy
        gs, me, _ = new_game(hand=["Elvish Spirit Guide"])
        ga.enter_battlefield(gs, me, "Chrome Mox", FORGE)
        clone = copy.deepcopy(gs)
        self.assertIsNotNone(ga.awaiting_entry(clone.players[0]))

    def test_without_scripts_nothing_is_prompted(self):
        gs, me, _ = new_game(TEXT, hand=["Elvish Spirit Guide"])
        ga.enter_battlefield(gs, me, "Chrome Mox", TEXT)
        self.assertNotIn("awaiting", me.battlefield[0])


class UntapTests(unittest.TestCase):
    def test_mana_vault_stays_tapped_but_other_things_untap(self):
        gs, me, _ = new_game(battlefield=["Mana Vault", "Sol Ring"])
        for e in me.battlefield:
            e["tapped"] = True
        ga.start_turn(gs, FORGE, 0)
        self.assertEqual([e["tapped"] for e in me.battlefield], [True, False])

    def test_same_result_from_the_text_parser(self):
        gs, me, _ = new_game(TEXT, battlefield=["Mana Vault", "Sol Ring"])
        for e in me.battlefield:
            e["tapped"] = True
        ga.start_turn(gs, TEXT, 0)
        self.assertEqual([e["tapped"] for e in me.battlefield], [True, False])


class ReportTests(unittest.TestCase):
    def test_report_on_the_saved_deck_finds_no_disagreements_and_lists_the_key_cards(self):
        import rules_report
        lines = rules_report.build_report(FORGE, [KINNAN], [c["name"] for c in CARDS.values()])
        text = "\n".join(lines)
        self.assertNotIn("DIFFERENT", text)
        self.assertIn("agree everywhere", text)
        self.assertIn("Wooded Foothills  [Forge script]", text)
        self.assertIn("fetchland: pay 1 life, search for Mountain or Forest", text)
        self.assertIn("Breeding Pool  [Forge script]\n    asks: pay 2 life or enter tapped", text)
        self.assertIn("The Cabbage Merchant  [card text only]", text)

    def test_report_flags_a_script_that_disagrees_with_the_card_text(self):
        import rules_report

        class Wrong(ForgeStore):
            def peek_forge(self, name):
                card = super().peek_forge(name)
                if card and card.name == "Sol Ring":
                    card.faces[0].abilities[0]["Amount"] = "3"        # Forge would say {C}{C}{C}
                return card
        lines = rules_report.build_report(Wrong(), [KINNAN], ["Sol Ring"])
        self.assertTrue(any(l.startswith("DIFFERENT  Sol Ring: mana differs") for l in lines), lines)

    def test_report_lists_cards_scryfall_does_not_know(self):
        import rules_report
        lines = rules_report.build_report(FORGE, [KINNAN], ["Not A Real Card"])
        self.assertIn("1 card(s) not found on Scryfall: Not A Real Card", lines)


if __name__ == "__main__":
    unittest.main()
