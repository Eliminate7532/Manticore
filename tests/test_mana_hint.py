# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 13: the 'Why can't I pay?' hint. mana_hint.explain says why the untapped mana cannot pay a cost (and stays silent when unsure);
prompt_view / ForgeTable.pay_trouble put it in the prompt bar while a payment is asked for."""
import copy
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_table as ft
import mana_hint as mh
from tests.test_forge_table import make_gui
from tests.test_round11 import mox, state_with


def land(name, colour, tapped=False):
    return {"id": abs(hash(name)) % 900 + 100, "name": name, "type": "Land", "text": "{T}: Add {%s}." % colour, "isLand": True,
            "isCreature": False, "tapped": tapped, "sick": False}


def elves(sick=False, tapped=False):
    return {"id": 300, "name": "Llanowar Elves", "type": "Creature - Elf Druid", "text": "{T}: Add {G}.", "isLand": False,
            "isCreature": True, "tapped": tapped, "sick": sick, "keywords": []}


def kinnan():
    return {"id": 301, "name": "Kinnan, Bonder Prodigy", "type": "Legendary Creature", "isLand": False, "isCreature": True, "tapped": False, "sick": False,
            "text": "Whenever you tap a nonland permanent for mana, add one mana of any type that permanent produced.\r\n{5}{G}{G}: Look at the top five cards."}


def blue_imprint():
    return [{"name": "Samut, Voice of Dissent", "colors": ["U"]}]


class ExplainTests(unittest.TestCase):
    def test_nothing_is_said_when_the_cost_can_be_paid(self):
        self.assertEqual(mh.explain("{G}", [land("Forest", "G")]), "")
        self.assertEqual(mh.explain("{1}{G}", [land("Forest", "G"), land("Island", "U")]), "")

    def test_a_colour_nothing_makes_is_named(self):
        text = mh.explain("{G}", [land("Island", "U")])
        self.assertIn("Can't pay {G}", text)
        self.assertIn("green", text)
        self.assertIn("Island (blue)", text)

    def test_chrome_mox_with_a_blue_imprint_only_makes_blue(self):
        text = mh.explain("{G}", [mox(blue_imprint()), land("Otawara, Soaring City", "U")])
        self.assertIn("Chrome Mox (only blue, from its imprint)", text)
        self.assertIn("nothing you have untapped makes green", text)

    def test_chrome_mox_with_the_right_imprint_pays(self):
        self.assertEqual(mh.explain("{G}", [mox([{"name": "Elf", "colors": ["G"]}])]), "")

    def test_chrome_mox_with_no_imprint_makes_nothing(self):
        text = mh.explain("{U}", [mox([])])
        self.assertIn("Can't pay {U}", text)

    def test_tapped_permanents_do_not_count(self):
        text = mh.explain("{G}", [land("Forest", "G", tapped=True)])
        self.assertIn("Can't pay {G}", text)
        self.assertNotIn("Untapped: Forest", text)

    def test_a_summoning_sick_creature_is_named_as_the_reason(self):
        text = mh.explain("{G}", [elves(sick=True)])
        self.assertIn("Llanowar Elves is summoning sick", text)

    def test_a_sick_creature_with_haste_can_tap(self):
        card = elves(sick=True)
        card["keywords"] = ["Haste"]
        self.assertEqual(mh.explain("{G}", [card]), "")

    def test_too_little_mana_says_how_short_you_are(self):
        text = mh.explain("{3}{G}", [land("Forest", "G"), land("Island", "U")])
        self.assertIn("2 mana short", text)

    def test_floating_mana_counts(self):
        self.assertEqual(mh.explain("{G}", [land("Island", "U")], {"G": 1}), "")
        self.assertIn("Can't pay {G}", mh.explain("{G}", [land("Island", "U")], {"U": 1}))

    def test_hybrid_takes_either_colour(self):
        self.assertEqual(mh.explain("{G/U}", [land("Island", "U")]), "")
        self.assertIn("blue or green", mh.explain("{G/U}", [land("Mountain", "R")]))

    def test_a_mana_doubler_in_play_silences_it(self):
        self.assertEqual(mh.explain("{G}{G}", [kinnan(), elves()]), "")

    def test_symbols_it_cannot_judge_are_never_judged(self):
        for cost in ("{X}{G}", "{G/P}", "{2/W}", "{S}"):
            self.assertEqual(mh.explain(cost, [land("Island", "U")]), "", cost)

    def test_a_permanent_it_cannot_read_might_make_anything(self):
        weird = {"id": 500, "name": "Mystery Rock", "type": "Artifact", "isLand": False, "isCreature": False, "tapped": False, "sick": False,
                 "text": "{T}: Add mana of a colour chosen by a coin flip in a way we cannot read."}
        self.assertEqual(mh.explain("{G}", [weird]), "")

    def test_a_card_with_no_text_at_all_does_not_crash(self):
        self.assertIn("Can't pay {G}", mh.explain("{G}", [{"name": "Bear", "type": "Creature", "tapped": False}]))

    def test_an_empty_or_odd_cost_says_nothing(self):
        for cost in ("", "{0}", "no cost", None):
            self.assertEqual(mh.explain(cost, [land("Island", "U")]), "")

    def test_the_untapped_list_stops_at_four(self):
        text = mh.explain("{G}", [land("Island %d" % i, "U") for i in range(6)])
        self.assertIn(", ...", text)


class PromptViewTests(unittest.TestCase):
    MESSAGE = "Kinnan, Bonder Prodigy - Creature 2 / 2\n\nPay Mana Cost: {G}"

    def test_without_trouble_the_hint_is_the_usual_one(self):
        _h, hint, _ok = ft.prompt_view(self.MESSAGE)
        self.assertIn("Tap lands", hint)

    def test_trouble_replaces_the_hint_and_keeps_the_cancel_note(self):
        _h, hint, _ok = ft.prompt_view(self.MESSAGE, trouble="Can't pay {G}: nothing makes green.")
        self.assertTrue(hint.startswith("Can't pay {G}"))
        self.assertIn("Cancel takes the spell back", hint)
        self.assertNotIn("Tap lands", hint)

    def test_trouble_is_ignored_outside_a_payment(self):
        _h, hint, _ok = ft.prompt_view("Do you want to keep your hand?", trouble="Can't pay {G}")
        self.assertNotIn("Can't pay", hint)

    def test_headline_is_unchanged(self):
        h1, _a, _b = ft.prompt_view(self.MESSAGE)
        h2, _c, _d = ft.prompt_view(self.MESSAGE, trouble="x")
        self.assertEqual(h1, h2)


class PayTroubleTests(unittest.TestCase):
    def gui_paying(self, battlefield, pool=None, cost="{G}"):
        st = copy.deepcopy(state_with(mox(blue_imprint())))
        me = next(p for p in st["players"] if p["id"] == st["me"])
        me["zones"]["battlefield"] = battlefield
        me["manaPool"] = pool or {}
        st["prompt"] = dict(st.get("prompt") or {})
        st["prompt"]["message"] = "Kinnan, Bonder Prodigy - Creature 2 / 2\n\nPay Mana Cost: " + cost
        return make_gui(st)

    def test_it_explains_a_payment_that_cannot_work(self):
        gui = self.gui_paying([mox(blue_imprint()), land("Otawara, Soaring City", "U")])
        text = gui.pay_trouble()
        self.assertIn("nothing you have untapped makes green", text)
        self.assertIn("only blue, from its imprint", text)

    def test_it_is_silent_when_the_payment_can_work(self):
        self.assertEqual(self.gui_paying([land("Forest", "G")]).pay_trouble(), "")

    def test_it_is_silent_when_nothing_is_being_paid(self):
        st = state_with(mox(blue_imprint()))
        st["prompt"] = dict(st.get("prompt") or {})
        st["prompt"]["message"] = "Priority: Karl Turn: 3 (Karl) Phase: Main phase, precombat Stack: Empty"
        self.assertEqual(make_gui(st).pay_trouble(), "")

    def test_floating_mana_from_the_state_is_used(self):
        self.assertEqual(self.gui_paying([mox(blue_imprint())], pool={"G": 1}).pay_trouble(), "")

    def test_a_crash_inside_the_hint_never_reaches_the_table(self):
        gui = self.gui_paying([land("Island", "U")])
        real = ft.mh.explain
        ft.mh.explain = lambda *a, **k: 1 / 0
        try:
            self.assertEqual(gui.pay_trouble(), "")
        finally:
            ft.mh.explain = real


if __name__ == "__main__":
    unittest.main()
