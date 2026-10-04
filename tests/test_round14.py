# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 14: the combat damage / "divide N" window (allocation.py + AssignDialog), the card-by-card check (card_check.py),
the report-to-test command and the offline GitHub check."""
import copy
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import card_check as cc
import forge_client as fc
import forge_dialogs as dlg
import replay
import tests.live as live
from allocation import Allocation
from tests.test_forge_table import frame, key, click, make_gui, move, my_hand


def card_row(name, lethal, defender=False, cid=1):
    return {"kind": "card", "card": {"id": cid, "name": name}, "lethal": lethal}


def player_row(name, lethal):
    return {"kind": "player", "id": 9, "name": name, "lethal": lethal, "defender": True}


class AllocationDamageTests(unittest.TestCase):
    def setUp(self):
        self.rows = [card_row("Bear", 2), card_row("Wall", 4), player_row("Opp", 20)]

    def test_starts_as_lethal_in_order_with_the_rest_to_the_last_row(self):
        a = Allocation("damage", 9, self.rows, order=True)
        self.assertEqual(a.amounts, [2, 4, 3])
        self.assertIsNone(a.problem())

    def test_trample_excess_goes_to_the_defender_not_the_last_blocker(self):
        a = Allocation("damage", 7, self.rows, order=True)
        self.assertEqual(a.amounts, [2, 4, 1])

    def test_not_enough_damage_all_goes_down_the_line(self):
        a = Allocation("damage", 3, self.rows, order=True)
        self.assertEqual(a.amounts, [2, 1, 0])

    def test_without_a_defender_row_the_last_blocker_takes_the_excess(self):
        a = Allocation("damage", 9, self.rows[:2], order=True)
        self.assertEqual(a.amounts, [2, 7])

    def test_damage_may_not_skip_ahead_of_an_earlier_creature(self):
        a = Allocation("damage", 7, self.rows, order=True)
        a.change(0, -1)
        a.change(1, 1)
        self.assertEqual(a.amounts, [1, 5, 1])
        self.assertIn("Bear needs lethal damage (2) before Wall", a.problem())
        self.assertFalse(a.valid)

    def test_defender_needs_every_blocker_lethal_even_when_the_blocker_order_is_free(self):
        a = Allocation("damage", 7, self.rows, order=False)
        a.amounts = [1, 6, 0]
        self.assertIsNone(a.problem())                      # blockers may be hit in any order
        a.amounts = [1, 5, 1]
        self.assertIn("before Opp", a.problem())
        a.amounts = [2, 2, 3]
        self.assertIn("needs lethal damage (4) before Opp", a.problem())

    def test_free_division_allows_anything_that_adds_up(self):
        a = Allocation("damage", 7, self.rows, order=False, free=True)
        a.amounts = [0, 0, 7]
        self.assertIsNone(a.problem())
        a.amounts = [0, 0, 6]
        self.assertEqual(a.problem(), "1 left to assign")

    def test_change_never_goes_past_the_points_left_or_below_zero(self):
        a = Allocation("damage", 4, [card_row("Bear", 2), card_row("Wall", 4)], order=True)
        self.assertEqual(a.amounts, [2, 2])
        self.assertEqual(a.change(1, 5), 0)                 # nothing left to hand out
        self.assertEqual(a.change(0, -9), -2)
        self.assertEqual(a.amounts, [0, 2])
        self.assertEqual(a.change(0, -1), 0)
        self.assertEqual(a.remaining, 2)

    def test_lethal_and_rest_helpers(self):
        a = Allocation("damage", 9, self.rows, order=True)
        a.amounts = [0, 0, 0]
        a.to_lethal(0)
        a.to_lethal(1)
        self.assertEqual(a.amounts, [2, 4, 0])
        a.to_rest(2)
        self.assertEqual(a.amounts, [2, 4, 3])
        a.clear(2)
        self.assertEqual(a.remaining, 3)

    def test_zero_lethal_rows_do_not_block_the_next_row(self):
        a = Allocation("damage", 3, [card_row("Already dying", 0), card_row("Fresh", 3)], order=True)
        self.assertEqual(a.amounts, [0, 3])
        self.assertIsNone(a.problem())

    def test_answer_is_a_plain_list_of_ints(self):
        a = Allocation("damage", 5, self.rows, order=True)
        self.assertEqual(a.answer(), [2, 3, 0])
        self.assertIsInstance(a.answer(), list)

    def test_from_request_reads_every_field(self):
        a = Allocation.from_request({"mode": "damage", "total": 5, "rows": self.rows, "order": True, "free": False, "may_skip": True})
        self.assertTrue(a.order and a.may_skip and not a.free)
        self.assertEqual(a.total, 5)


class AllocationDivideTests(unittest.TestCase):
    def rows(self, *maxes):
        return [{"kind": "card", "card": {"id": i + 1, "name": f"T{i + 1}"}, "max": m} for i, m in enumerate(maxes)]

    def test_starts_empty_or_with_one_each_when_at_least_one(self):
        self.assertEqual(Allocation("divide", 3, self.rows(3, 3)).amounts, [0, 0])
        self.assertEqual(Allocation("divide", 3, self.rows(3, 3), at_least_one=True).amounts, [1, 1])

    def test_ok_only_when_everything_is_spent(self):
        a = Allocation("divide", 3, self.rows(3, 3))
        self.assertEqual(a.problem(), "3 left to assign")
        a.change(0, 2)
        a.change(1, 1)
        self.assertIsNone(a.problem())

    def test_row_maximum_is_respected(self):
        a = Allocation("divide", 4, self.rows(1, 4))
        self.assertEqual(a.change(0, 3), 1)
        self.assertEqual(a.amounts, [1, 0])
        a.to_lethal(1)                                      # "Max" for a divide
        self.assertEqual(a.amounts, [1, 3])

    def test_at_least_one_cannot_be_taken_away(self):
        a = Allocation("divide", 3, self.rows(3, 3), at_least_one=True)
        a.change(0, 1)
        self.assertEqual(a.amounts, [2, 1])
        self.assertEqual(a.change(1, -1), 0)
        self.assertEqual(a.amounts, [2, 1])
        self.assertIsNone(a.problem())

    def test_mana_rows_have_names_from_the_symbol(self):
        a = Allocation("divide", 2, [{"kind": "mana", "symbol": "U", "name": "blue", "max": 2}, {"kind": "mana", "symbol": "R", "name": "red", "max": 2}])
        self.assertEqual(a.name(0), "blue")
        a.change(0, 1)
        a.change(1, 1)
        self.assertEqual(a.summary(), "blue 1, red 1")

    def test_auto_fills_in_order_up_to_each_maximum(self):
        a = Allocation("divide", 5, self.rows(2, 2, 5))
        a.auto()
        self.assertEqual(a.amounts, [2, 2, 1])
        b = Allocation("divide", 5, self.rows(9, 9), at_least_one=True)
        b.auto()
        self.assertEqual(sum(b.amounts), 5)
        self.assertTrue(all(x >= 1 for x in b.amounts))

    def test_missing_maximum_means_the_whole_amount(self):
        a = Allocation("divide", 3, [{"kind": "player", "name": "Opp"}, {"kind": "player", "name": "Me"}])
        self.assertEqual(a.maximum(0), 3)


class AssignDialogTests(unittest.TestCase):
    def request(self, gui, req):
        gui.session.requests.append(req)
        frame(gui)

    def damage_request(self, gui, total=7, may_skip=False, order=True, free=False):
        hand = my_hand(gui)
        rows = []
        for i, (name, lethal) in enumerate((("Grizzly Bears", 2), ("Wall of Omens", 4))):
            c = copy.deepcopy(hand[i])
            c["name"] = name
            rows.append({"kind": "card", "card": c, "lethal": lethal})
        rows.append({"kind": "player", "id": 5, "name": "Forge AI", "lethal": 20, "defender": True})
        src = copy.deepcopy(hand[3])
        src["name"] = "Trampling Beast"
        return {"id": 31, "kind": "assign", "mode": "damage", "title": "Assign combat damage from Trampling Beast", "total": total, "order": order,
                "free": free, "may_skip": may_skip, "source": src, "rows": rows}

    def test_kind_assign_opens_the_assign_window(self):
        gui = make_gui("main1_start")
        self.request(gui, self.damage_request(gui))
        self.assertIsInstance(gui.modal, dlg.AssignDialog)

    def test_hovering_a_row_card_shows_it_big_in_the_focus_panel(self):
        """Karl's request (2026-09-22): 'I should be able to read the cards in the card focus area.' The row thumbnails
        here are small (the same source_thumb() other dialogs use), and unlike ChooseDialog's card grid this window had
        no hover-to-zoom at all, so there was no way to read a blocker's text without leaving the dialog. It now reuses
        the same shared draw_focus_zoom() helper ChooseDialog's library/graveyard search already relies on."""
        gui = make_gui("main1_start")
        self.request(gui, self.damage_request(gui))
        p = gui.L.preview
        before = pygame.image.tobytes(gui.screen.subsurface(p).copy(), "RGB")
        thumb = gui.card_rects[gui.modal.alloc.rows[0]["card"]["id"]]
        move(gui, thumb.center)
        after = pygame.image.tobytes(gui.screen.subsurface(p).copy(), "RGB")
        self.assertNotEqual(before, after)

    def test_enter_sends_the_automatic_split_which_puts_the_excess_on_the_player(self):
        gui = make_gui("main1_start")
        self.request(gui, self.damage_request(gui, total=7))
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 31, "value": [2, 4, 1]})
        self.assertIsNone(gui.modal)

    def test_ok_button_is_off_while_the_split_is_illegal_and_enter_does_nothing(self):
        gui = make_gui("main1_start")
        self.request(gui, self.damage_request(gui, total=7))
        d = gui.modal
        d.alloc.change(0, -1)
        frame(gui)
        self.assertFalse(d.alloc.valid)
        self.assertNotIn("ok", [n for _r, n in d.buttons])
        before = len(gui.session.commands("reply"))
        key(gui, pygame.K_RETURN)
        self.assertEqual(len(gui.session.commands("reply")), before)
        self.assertIs(gui.modal, d)

    def test_plus_and_minus_buttons_move_damage_and_ok_sends_it(self):
        gui = make_gui("main1_start")
        self.request(gui, self.damage_request(gui, total=7, order=False))
        d = gui.modal
        names = {n: r for r, n in d.buttons}
        click(gui, names["minus2"].center)                   # 1 off the player ...
        d = gui.modal
        names = {n: r for r, n in d.buttons}
        click(gui, names["plus0"].center)                    # ... 1 on the bear
        self.assertEqual(gui.modal.alloc.amounts, [3, 4, 0])
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1]["value"], [3, 4, 0])

    def test_keys_select_a_row_and_change_it(self):
        gui = make_gui("main1_start")
        self.request(gui, self.damage_request(gui, total=7, order=False))
        key(gui, pygame.K_DOWN)
        key(gui, pygame.K_DOWN)                              # the player row
        key(gui, pygame.K_LEFT)
        key(gui, pygame.K_UP)
        key(gui, pygame.K_UP)                                # the first row
        key(gui, pygame.K_RIGHT)
        self.assertEqual(gui.modal.alloc.amounts, [3, 4, 0])
        key(gui, pygame.K_a)                                 # A = the automatic split again
        self.assertEqual(gui.modal.alloc.amounts, [2, 4, 1])

    def test_decide_later_only_when_the_engine_allows_it_and_sends_false(self):
        gui = make_gui("main1_start")
        self.request(gui, self.damage_request(gui, may_skip=False))
        key(gui, pygame.K_ESCAPE)
        self.assertIsInstance(gui.modal, dlg.AssignDialog)  # Escape does nothing when skipping is not allowed
        gui.session.requests.clear()
        gui.modal.done = True
        frame(gui)
        self.request(gui, dict(self.damage_request(gui, may_skip=True), id=32))
        self.assertIn("skip", [n for _r, n in gui.modal.buttons])
        key(gui, pygame.K_ESCAPE)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 32, "value": False})

    def divide_request(self, gui):
        hand = my_hand(gui)
        rows = []
        for i in range(2):
            c = copy.deepcopy(hand[i])
            rows.append({"kind": "card", "card": c, "max": 3})
        rows.append({"kind": "player", "id": 5, "name": "Forge AI", "max": 3})
        return {"id": 51, "kind": "assign", "mode": "divide", "title": "Divide 3 damage (Forked Bolt)", "label": "damage", "total": 3,
                "at_least_one": False, "source": copy.deepcopy(hand[3]), "rows": rows}

    def test_divide_starts_empty_and_needs_everything_handed_out(self):
        gui = make_gui("main1_start")
        self.request(gui, self.divide_request(gui))
        d = gui.modal
        self.assertEqual(d.alloc.amounts, [0, 0, 0])
        key(gui, pygame.K_RETURN)
        self.assertIs(gui.modal, d)
        key(gui, pygame.K_r)                                 # Rest: all 3 on the first row
        self.assertEqual(d.alloc.amounts, [3, 0, 0])
        key(gui, pygame.K_LEFT)
        key(gui, pygame.K_DOWN)
        key(gui, pygame.K_RIGHT)
        self.assertEqual(d.alloc.amounts, [2, 1, 0])
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 51, "value": [2, 1, 0]})

    def test_mana_colour_rows_draw_and_answer(self):
        gui = make_gui("main1_start")
        rows = [{"kind": "mana", "symbol": s, "name": n, "max": 2} for s, n in (("W", "white"), ("U", "blue"), ("B", "black"), ("R", "red"), ("G", "green"))]
        self.request(gui, {"id": 61, "kind": "assign", "mode": "divide", "title": "Divide 2 mana (Chromatic Lantern)", "label": "mana",
                           "total": 2, "at_least_one": False, "rows": rows})
        d = gui.modal
        key(gui, pygame.K_DOWN)
        key(gui, pygame.K_r)
        self.assertEqual(d.alloc.amounts, [0, 2, 0, 0, 0])
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 61, "value": [0, 2, 0, 0, 0]})

    def test_many_rows_scroll_and_draw_at_every_size_and_scale(self):
        for size, scale in (((1360, 840), 1.0), ((900, 600), 1.0), ((1100, 700), 2.0), ((1920, 1080), 1.5)):
            with self.subTest(size=size, scale=scale):
                gui = make_gui("main1_start", size, scale=scale)
                req = self.damage_request(gui, total=30)
                base = req["rows"][0]
                req["rows"] = [dict(base, card=dict(base["card"], id=900 + i, name=f"Blocker {i}"), lethal=2) for i in range(9)] + [req["rows"][-1]]
                self.request(gui, req)
                d = gui.modal
                self.assertIsInstance(d, dlg.AssignDialog)
                for _ in range(12):
                    key(gui, pygame.K_DOWN)
                self.assertGreaterEqual(d.scroll, 0)
                gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-3))
                frame(gui)
                self.assertTrue(d.rect.colliderect(gui.screen.get_rect()))


# ---------------------------------------------------------------------------------------
# card_check.py: the parts that need no engine
# ---------------------------------------------------------------------------------------

KINNAN = """Name:Kinnan, Bonder Prodigy
ManaCost:G U
Types:Legendary Creature Human Druid
PT:2/2
T:Mode$ TapsForMana | ValidCard$ Permanent.nonLand+YouCtrl | Execute$ TrigMana
A:AB$ Untap | Cost$ 2 G U | Defined$ Targeted
"""
FORCE = """Name:Force of Will
ManaCost:3 U U
Types:Instant
S:Mode$ Continuous | Affected$ Card.Self | AlternativeCost$ ExileFromHand
A:SP$ Counter | Cost$ 3 U U | TargetType$ Spell | ValidTgts$ Card
"""
LAND = """Name:Breeding Pool
Types:Land Forest Island
A:AB$ Mana | Cost$ T | Produced$ U
A:AB$ Mana | Cost$ T | Produced$ G
"""


class CardCheckPureTests(unittest.TestCase):
    def test_a_script_is_read_into_a_profile(self):
        p = cc.Profile("Kinnan, Bonder Prodigy", KINNAN, commander=True)
        self.assertEqual((p.name, p.cost, p.found), ("Kinnan, Bonder Prodigy", "G U", True))
        self.assertTrue(p.is_permanent and not p.is_land)
        self.assertEqual(p.abilities, 1)
        self.assertFalse(p.needs_stack)
        f = cc.Profile("Force of Will", FORCE)
        self.assertTrue(f.needs_stack and f.free_cast and not f.is_permanent)
        land = cc.Profile("Breeding Pool", LAND)
        self.assertTrue(land.is_land and land.abilities == 2)

    def test_a_card_without_a_script_is_not_found_but_still_has_a_name(self):
        p = cc.Profile("Mystery Card", None)
        self.assertFalse(p.found)
        self.assertEqual(p.name, "Mystery Card")

    def test_levels_follow_what_the_card_can_do(self):
        self.assertEqual(cc.levels_for(cc.Profile("Force of Will", FORCE)), ["stack"])
        self.assertEqual(cc.levels_for(cc.Profile("Breeding Pool", LAND)), ["cast", "activate"])
        self.assertEqual(cc.levels_for(cc.Profile("Kinnan, Bonder Prodigy", KINNAN, commander=True)), ["cast", "activate"])

    def test_setup_lines_put_the_card_where_the_level_needs_it(self):
        force = cc.Profile("Force of Will", FORCE)
        lines = dict(l.split("=", 1) for l in cc.setup_lines(force, "stack"))
        self.assertEqual(lines["activeplayer"], "ai")
        self.assertEqual(lines["aihand"], "Lightning Bolt")
        self.assertTrue(lines["humanhand"].startswith("Force of Will;"))
        self.assertIn("Llanowar Elves", lines["humanbattlefield"])
        pool = cc.Profile("Breeding Pool", LAND)
        act = dict(l.split("=", 1) for l in cc.setup_lines(pool, "activate"))
        self.assertTrue(act["humanbattlefield"].endswith("Breeding Pool"))
        self.assertNotIn("Breeding Pool", act["humanhand"])
        cast = dict(l.split("=", 1) for l in cc.setup_lines(pool, "cast"))
        self.assertIn("Breeding Pool", cast["humanhand"])
        self.assertEqual(cast["activeplayer"], "human")

    def test_a_commander_is_put_back_in_the_command_zone_because_every_setup_clears_it(self):
        k = cc.Profile("Kinnan, Bonder Prodigy", KINNAN, commander=True)
        lines = cc.setup_lines(k, "cast")
        self.assertIn("humancommand=Kinnan, Bonder Prodigy|IsCommander", lines)
        self.assertNotIn("Kinnan", dict(l.split("=", 1) for l in lines)["humanhand"])

    def test_answers_for_the_engines_questions(self):
        self.assertIs(cc.request_answer({"kind": "confirm"}, {}), True)
        self.assertEqual(cc.request_answer({"kind": "input", "numeric": True}, {}), "2")
        self.assertEqual(cc.request_answer({"kind": "input", "options": ["5", "6"]}, {}), "5")
        self.assertEqual(cc.request_answer({"kind": "order", "min": 2, "max": 2, "items": [{}, {}, {}]}, {}), [0, 1])

    def test_a_heading_is_never_chosen_and_finish_targeting_only_after_one_choice(self):
        req = {"kind": "choose", "title": "Select target card", "min": 0, "max": 1,
               "items": [{"kind": "text", "label": "--CARDS ON BATTLEFIELD:--"}, {"kind": "card", "card": {"name": "Bear"}},
                         {"kind": "text", "label": "[FINISH TARGETING]"}]}
        mem = {}
        self.assertEqual(cc.request_answer(req, mem), [1])
        self.assertEqual(cc.request_answer(req, mem), [2])

    def test_x_is_one_because_the_opposing_spell_is_a_lightning_bolt(self):
        req = {"kind": "choose", "title": "Choose X for Disrupting Shoal", "min": 1, "max": 1,
               "items": [{"kind": "text", "label": str(n)} for n in range(4)]}
        self.assertEqual(cc.request_answer(req, {}), [1])

    def test_the_ability_asked_for_is_the_one_chosen(self):
        req = {"kind": "choose_optional", "title": "Choose an ability", "min": 0, "max": 1, "items": [{}, {}, {}]}
        self.assertEqual(cc.request_answer(req, {"ability_index": 2}), [2])
        self.assertEqual(cc.request_answer(req, {"ability_index": 9}), [2])

    def test_a_damage_or_divide_request_gets_the_usual_split(self):
        req = {"kind": "assign", "mode": "damage", "total": 6, "order": False, "free": False,
               "rows": [{"lethal": 2}, {"lethal": 2}, {"lethal": 5, "defender": True}]}
        self.assertEqual(cc.request_answer(req, {}), [2, 2, 2])
        div = {"kind": "assign", "mode": "divide", "total": 3, "at_least_one": True, "rows": [{"max": 3}, {"max": 3}]}
        self.assertEqual(cc.request_answer(div, {}), [2, 1])

    def state(self, message, stack=None, ok=True, cancel=False, selectable=(), tapped=()):
        bf = [{"id": 100 + i, "name": "Forest", "selectable": True, "tapped": i in tapped} for i in range(len(selectable))]
        return {"me": 1, "stack": stack or [], "prompt": {"message": message, "ok": {"enabled": ok}, "cancel": {"enabled": cancel}, "selecting": False},
                "players": [{"id": 1, "zones": {"battlefield": bf, "hand": []}}, {"id": 2, "zones": {"battlefield": [], "hand": []}}]}

    def test_the_scripted_player_answers_requests_first(self):
        req = {"kind": "confirm", "id": 5}
        self.assertEqual(cc.next_move(self.state("Priority: x"), [req], {}), ("answer", req, True))

    def test_at_priority_with_an_empty_stack_there_is_nothing_to_do_but_with_a_spell_it_passes(self):
        self.assertIsNone(cc.next_move(self.state("Priority: Checker Turn 3"), [], {}))
        self.assertEqual(cc.next_move(self.state("Priority: Checker Turn 3", stack=[{"card": {}}]), [], {}), ("ok",))

    def test_paying_presses_auto_twice_then_clicks_sources_then_gives_up_and_says_so(self):
        st = self.state("Sol Ring (12) Pay Mana Cost: {1}", selectable=[0, 1, 2])
        mem = {}
        moves = [cc.next_move(st, [], mem) for _ in range(9)]
        self.assertEqual(moves[:2], [("ok",), ("ok",)])
        self.assertEqual([m[0] for m in moves[2:5]], ["click"] * 3)
        self.assertEqual(moves[7], ("ok",))                      # nothing to cancel with -> OK
        self.assertTrue(mem.get("unpayable"))

    def test_coin_toss_and_keep_hand_prompts_are_confirmed(self):
        self.assertEqual(cc.next_move(self.state("Do you keep your hand?"), [], {}), ("ok",))

    def test_repeated_steps_collapse_in_a_report(self):
        steps = ["answer choose 'x' -> [0]   [prompt: a]"] * 40 + ["OK   [prompt: b]"]
        out = cc.collapse_repeats(steps)
        self.assertEqual(out, ["answer choose 'x' -> [0]  (x40)", "OK   [prompt: b]"])

    def test_the_report_lists_failures_first_and_counts_everything(self):
        ok, bad, warn = cc.Result("Sol Ring", "cast"), cc.Result("Mox Diamond", "activate"), cc.Result("Forest", "cast")
        bad.note("the board could not be set up", cc.FAIL)
        warn.note("clicking it did nothing visible", cc.WARN)
        text = cc.format_report([ok, warn, bad], "deck.txt")
        self.assertLess(text.index("== FAIL"), text.index("== WARN"))
        self.assertLess(text.index("== WARN"), text.index("== OK"))
        self.assertIn("1 FAIL, 1 OK, 1 WARN  (3 checks)", text)
        self.assertIn("- Mox Diamond [activate]", text)

    def test_a_result_keeps_the_worst_status(self):
        r = cc.Result("X", "cast")
        r.note("a", cc.WARN)
        r.note("b", cc.FAIL)
        r.note("c", cc.WARN)
        self.assertEqual(r.status, cc.FAIL)
        self.assertEqual(r.as_dict()["notes"], ["a", "b", "c"])

    def test_scripts_of_very_new_cards_are_found_in_the_upcoming_folder(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "res", "cardsfolder", "upcoming"))
            with open(os.path.join(tmp, "res", "cardsfolder", "upcoming", "samut_tyrant_of_naktamun.txt"), "w") as f:
                f.write("Name:Samut, Tyrant of Naktamun\nManaCost:1 U\nTypes:Legendary Creature Human\n")
            with mock.patch.object(cc, "load_deck", return_value=(["Kinnan, Bonder Prodigy"], ["Samut, Tyrant of Naktamun"])):
                profiles = cc.load_profiles("deck.txt", tmp)
        by_name = {p.deck_name: p for p in profiles}
        self.assertTrue(by_name["Samut, Tyrant of Naktamun"].found)
        self.assertFalse(by_name["Kinnan, Bonder Prodigy"].found)

    def test_the_only_filter_matches_part_of_a_name(self):
        from unittest import mock
        with mock.patch.object(cc, "load_deck", return_value=(["Kinnan, Bonder Prodigy"], ["Sol Ring", "Solemn Simulacrum", "Forest"])):
            profiles = cc.load_profiles("deck.txt", "nowhere", only=["sol "])
        self.assertEqual([p.deck_name for p in profiles], ["Sol Ring"])

    def test_headings_are_told_apart_from_choices(self):
        import forge_log as flog
        self.assertTrue(flog.is_heading({"kind": "text", "label": "--CARDS IN GRAVEYARD:--"}))
        self.assertFalse(flog.is_heading({"kind": "text", "label": "[FINISH TARGETING]"}))
        self.assertFalse(flog.is_heading({"kind": "card", "card": {"name": "--"}}))
        self.assertFalse(flog.is_heading({"kind": "text", "label": "Yes -- or no"}))


class HeadingDialogTests(unittest.TestCase):
    def test_forges_section_headings_are_not_shown_as_choices_and_cannot_be_picked(self):
        gui = make_gui("main1_start")
        hand = my_hand(gui)
        items = [{"kind": "text", "label": "--CARDS ON BATTLEFIELD:--"}, {"kind": "card", "card": copy.deepcopy(hand[0])},
                 {"kind": "card", "card": dict(copy.deepcopy(hand[1]), id=9999, name="Other Card")}, {"kind": "text", "label": "[FINISH TARGETING]"}]
        gui.session.requests.append({"id": 3, "kind": "choose", "title": "Select target card", "min": 0, "max": 1, "items": items})
        frame(gui)
        d = gui.modal
        self.assertIsInstance(d, dlg.ChooseDialog)
        self.assertIn(0, d.skip)
        rows = d.text_rows(gui, 900)
        self.assertEqual([i for i, _l, _h in rows], [3])         # only "[FINISH TARGETING]" is a text choice
        d.toggle(1)
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 3, "value": [1]})


# ---------------------------------------------------------------------------------------
# replay.py --save-as / --check-saved: a replayed bug report kept as a regression test
# ---------------------------------------------------------------------------------------

def board(life=40, bf=("Forest",), turn=3):
    return {"turn": turn, "phase": "MAIN1", "prompt": {"message": "Priority: Karl"},
            "players": [{"id": 0, "name": "Karl", "life": life, "zones": {"battlefield": [{"name": n} for n in bf], "hand": [], "graveyard": [], "exile": [], "command": []}},
                        {"id": 1, "name": "AI 1", "life": 40, "zones": {"battlefield": [], "hand": [], "graveyard": [], "exile": [], "command": []}}], "me": 0}


LOG = [f"[LINE] event {i}" for i in range(60)]


def make_report_zip(complete=True, seed=5, state=None, commands=None):
    path = os.path.join(tempfile.mkdtemp(), "bugreport_test.zip")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("commands.json", json.dumps({"seed": seed, "complete": complete, "commands": commands or [{"t": 0.0, "c": "ok"}, {"t": 1.0, "c": "ok"}]}))
        z.writestr("decks/player.dck", "[Main]\n")
        z.writestr("decks/opponent1.dck", "[Main]\n")
        z.writestr("state.json", json.dumps(state or board()))
        z.writestr("game_log.txt", "\n".join(l for l in LOG))
    return path


def fake_replay(state=None, log=None, diverged=None, differences=None):
    return lambda report, runtime=None, upto=None, progress=None, keep_dir=None: {
        "state": state or board(), "log": log or LOG, "diverged": diverged, "sent": 2, "differences": differences or []}


class SaveRegressionTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.report = make_report_zip()

    def save(self, name="trample_excess", **kw):
        with mock.patch.object(replay, "replay", fake_replay(**kw.pop("fake", {}))):
            return replay.save_regression(self.report, name, kw.pop("note", "trample damage reaches the player"), root=self.root, **kw)

    def test_a_replayed_report_is_kept_with_what_it_ended_on(self):
        meta = self.save()
        folder = os.path.join(self.root, "trample_excess")
        self.assertEqual(sorted(os.listdir(folder)), ["expected.json", "report.zip"])
        with open(os.path.join(folder, "expected.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["name"], saved["seed"], saved["clicks"], saved["as_recorded"]), ("trample_excess", 5, 2, False))
        self.assertEqual(saved["note"], "trample damage reaches the player")
        self.assertEqual(saved["summary"]["players"]["Karl"]["life"], 40)
        self.assertEqual(saved["log_tail"], LOG[-replay.LOG_TAIL:])
        self.assertEqual(meta["log_tail"], saved["log_tail"])
        with open(self.report, "rb") as a, open(os.path.join(folder, "report.zip"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_bad_or_taken_names_are_refused_and_nothing_is_written(self):
        for bad in ("", "Has Space", "../up", "UPPER", "a/b"):
            with self.subTest(name=bad), self.assertRaises(replay.ReportError):
                self.save(bad)
        self.save("fine_name")
        with self.assertRaises(replay.ReportError) as cm:
            self.save("fine_name")
        self.assertIn("already exists", str(cm.exception))
        self.assertEqual(os.listdir(self.root), ["fine_name"])

    def test_a_report_holding_only_the_newest_clicks_cannot_be_kept(self):
        self.report = make_report_zip(complete=False)
        with self.assertRaises(replay.ReportError) as cm:
            self.save()
        self.assertIn("newest clicks", str(cm.exception))
        self.assertEqual(os.listdir(self.root), [])

    def test_a_replay_that_diverged_is_not_kept(self):
        with self.assertRaises(replay.ReportError) as cm:
            self.save(fake={"diverged": (1, "the ok button never became available")})
        self.assertIn("diverged after 2 clicks", str(cm.exception))
        self.assertEqual(os.listdir(self.root), [])

    def test_as_recorded_keeps_what_the_report_saw_but_only_when_the_replay_matches_it(self):
        self.report = make_report_zip(state=board(life=17))
        meta = self.save(as_recorded=True, fake={"state": board(life=17)})
        self.assertTrue(meta["as_recorded"])
        self.assertEqual(meta["summary"]["players"]["Karl"]["life"], 17)
        with self.assertRaises(replay.ReportError) as cm:
            self.save("second", as_recorded=True, fake={"state": board(life=40), "differences": ["Karl: life 17 in the report, 40 in the replay"]})
        self.assertIn("life 17 in the report", str(cm.exception))

    def test_a_saved_game_that_plays_out_the_same_is_fine_and_a_changed_one_is_described(self):
        self.save()
        with mock.patch.object(replay, "replay", fake_replay()):
            self.assertEqual(replay.check_saved("trample_excess", root=self.root)[0], [])
        with mock.patch.object(replay, "replay", fake_replay(state=board(life=37))):
            problems, meta = replay.check_saved("trample_excess", root=self.root)
        self.assertEqual(problems, ["Karl: life 40 in the report, 37 in the replay"])
        self.assertEqual(meta["note"], "trample damage reaches the player")
        with mock.patch.object(replay, "replay", fake_replay(state=board(bf=("Forest", "Sol Ring")))):
            self.assertIn("battlefield differs", replay.check_saved("trample_excess", root=self.root)[0][0])
        changed_log = LOG[:-3] + ["[LINE] something else"] * 3
        with mock.patch.object(replay, "replay", fake_replay(log=changed_log)):
            self.assertIn("the game log differs 1 line from the end", replay.check_saved("trample_excess", root=self.root)[0][0])
        with mock.patch.object(replay, "replay", fake_replay(diverged=(0, "no engine"))):
            self.assertIn("diverged", replay.check_saved("trample_excess", root=self.root)[0][0])

    def test_saved_names_lists_only_complete_folders(self):
        self.save("b_one")
        self.save("a_two")
        os.makedirs(os.path.join(self.root, "half_done"))
        open(os.path.join(self.root, "half_done", "report.zip"), "wb").close()
        self.assertEqual(replay.saved_names(self.root), ["a_two", "b_one"])
        self.assertEqual(replay.saved_names(os.path.join(self.root, "nowhere")), [])

    def cli(self, *argv, fake=None):
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch.object(replay, "REGRESSION_DIR", self.root), mock.patch.object(replay, "replay", fake or fake_replay()):
            code = replay.main(list(argv))
        return code, out.getvalue()

    def test_the_command_line_saves_and_then_checks(self):
        code, out = self.cli(self.report, "--save-as", "cli_game", "--note", "from the command line")
        self.assertEqual(code, 0, out)
        self.assertIn("Saved tests/reports/cli_game/", out)
        code, out = self.cli("--check-saved")
        self.assertEqual((code, out.count("OK ")), (0, 1))
        self.assertIn("from the command line", out)
        code, out = self.cli("--check-saved", fake=fake_replay(state=board(life=1)))
        self.assertEqual(code, 1)
        self.assertIn("DIFFERS cli_game", out)
        self.assertIn("life 40 in the report, 1 in the replay", out)

    def test_the_command_line_says_why_a_save_was_refused(self):
        code, out = self.cli(self.report, "--save-as", "Bad Name")
        self.assertEqual(code, 2)
        self.assertIn("Not saved: the name may hold only small letters", out)

    def test_checking_with_nothing_saved_explains_how_to_save_one(self):
        code, out = self.cli("--check-saved")
        self.assertEqual(code, 0)
        self.assertIn("--save-as NAME", out)

    def test_a_missing_report_argument_is_an_error_not_a_crash(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", io.StringIO()):
                replay.main([])


# ---------------------------------------------------------------------------------------
# with the real engine: combat damage and "divide" arrive as requests, are answered and are checked by the bridge
# ---------------------------------------------------------------------------------------

PROBLEM = live.live_problem()
SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")


def combat_lines(blockers=2, ai_life=5):
    return ["humanlife=40", "ailife=%d" % ai_life, "activeplayer=human", "activephase=COMBAT_DECLARE_BLOCKERS", "turn=3", "humanlandsplayed=0",
            "humanhand=Brainstorm", "humanbattlefield=Colossal Dreadmaw|Attacking;Forest;Forest", "aihand=", "aigraveyard=",
            "aibattlefield=" + ";".join(["Grizzly Bears"] * blockers), "removesummoningsickness=true"]


class DeveloperModeTests(unittest.TestCase):
    """The board-setup command only exists when the bridge was started with --dev, and only tests, card_check and replays of reports that used it do that."""

    def command_line(self, **kw):
        seen = {}

        def popen(cmd, *a, **k):
            seen["cmd"] = cmd
            raise RuntimeError("stop here")
        s = fc.ForgeSession("player.dck", ["opp.dck"], seed=7, runtime=tempfile.mkdtemp(), **kw)
        with mock.patch.object(fc, "runtime_problem", return_value=None), mock.patch.object(fc, "find_java", return_value="java"), \
                mock.patch.object(fc, "class_path", return_value="cp"), mock.patch.object(fc.subprocess, "Popen", popen):
            with self.assertRaises(RuntimeError):
                s.start()
        return seen["cmd"]

    def test_a_normal_game_does_not_start_the_bridge_in_dev_mode(self):
        self.assertNotIn("--dev", self.command_line())

    def test_a_test_game_asks_for_dev_mode_after_the_seed(self):
        cmd = self.command_line(dev=True)
        self.assertEqual(cmd[-1], "--dev")
        self.assertEqual(cmd[cmd.index("--seed") + 1], "7")

    def test_setup_sends_the_lines_as_a_list(self):
        s = fc.ForgeSession("player.dck", [])
        with mock.patch.object(s, "send") as send:
            s.setup(("humanhand=Sol Ring", "aibattlefield=Forest;Forest"))
        send.assert_called_once_with(c="setup", lines=["humanhand=Sol Ring", "aibattlefield=Forest;Forest"])

    def replay_session_kwargs(self, commands):
        report = replay.load_report(make_report_zip(commands=commands))
        session = mock.MagicMock()
        session.start.side_effect = RuntimeError("stop here")
        with mock.patch.object(replay.fc, "ForgeSession", return_value=session) as cls:
            with self.assertRaises(RuntimeError):
                replay.replay(report, runtime=tempfile.mkdtemp())
        return cls.call_args.kwargs

    def test_a_replay_starts_in_dev_mode_only_when_the_report_set_up_a_board(self):
        self.assertTrue(self.replay_session_kwargs([{"t": 0.0, "c": "setup", "lines": ["humanlife=1"]}, {"t": 1.0, "c": "ok"}])["dev"])
        self.assertFalse(self.replay_session_kwargs([{"t": 0.0, "c": "ok"}, {"t": 1.0, "c": "ok"}])["dev"])

    def test_every_check_gives_both_players_a_library(self):
        """Forge's setup EMPTIES a library it does not list: the first draw would lose the game and a search ('Mountain or Forest') found nothing -
        the fetch-land test failed that way with 'There are no cards in your library'."""
        for level in ("cast", "activate", "stack"):
            lines = dict(l.split("=", 1) for l in cc.setup_lines(cc.Profile("Force of Will", FORCE), level))
            for key in ("humanlibrary", "ailibrary"):
                with self.subTest(level=level, key=key):
                    self.assertGreaterEqual(len(lines[key].split(";")), 10)
                    self.assertIn("Forest", lines[key].split(";"))


class StackCheckRetryTests(unittest.TestCase):
    """The opposing AI sometimes keeps its Lightning Bolt (three 'stack' checks skipped in one full run of the deck): the board is set up again, up to twice."""

    def run_check(self, casts):
        ck = cc.Checker("deck.txt")
        ck.s = mock.MagicMock()
        with mock.patch.object(ck, "clean", return_value=True), mock.patch.object(ck, "new_engine_lines", return_value=[]), \
                mock.patch.object(ck, "apply", return_value=True) as apply, \
                mock.patch.object(ck, "wait_for_opponent_spell", side_effect=casts) as wait:
            result = ck.check(cc.Profile("Force of Will", FORCE), "stack")
        return result, apply, wait

    def test_a_bolt_that_never_comes_is_a_skip_after_three_tries(self):
        result, apply, wait = self.run_check([False, False, False])
        self.assertEqual(result.status, cc.SKIP)
        self.assertEqual((apply.call_count, wait.call_count), (3, 3))

    def test_a_board_that_cannot_be_set_up_again_ends_the_retries(self):
        ck = cc.Checker("deck.txt")
        ck.s = mock.MagicMock()
        with mock.patch.object(ck, "clean", return_value=True), mock.patch.object(ck, "new_engine_lines", return_value=[]), \
                mock.patch.object(ck, "apply", side_effect=[True, False]) as apply, \
                mock.patch.object(ck, "wait_for_opponent_spell", return_value=False) as wait:
            result = ck.check(cc.Profile("Force of Will", FORCE), "stack")
        self.assertEqual(result.status, cc.SKIP)
        self.assertEqual((apply.call_count, wait.call_count), (2, 1))


class ScriptedPlayerPaymentTests(unittest.TestCase):
    """The scripted player gives up on a payment it keeps seeing (it counts how often the same prompt came round). Three Flusterstorm copies each
    ask the same 'pay {1}?', with a priority prompt between them: those are three questions, not one that never goes away."""

    def state(self, message, stack=0):
        return {"me": 0, "players": [{"id": 0, "zones": {"battlefield": []}}], "stack": [{}] * stack,
                "prompt": {"message": message, "ok": {"enabled": True}, "cancel": {"enabled": True}}}

    PAY = "Flusterstorm - Counter Flusterstorm unless its controller pays {1}. Pay Mana Cost: {1}"

    def test_the_same_payment_asked_three_times_in_a_row_is_not_stuck(self):
        mem = {}
        for _copy in range(3):
            for _poll in range(4):
                cc.next_move(self.state(self.PAY, 3), [], mem)
            cc.next_move(self.state("Priority: Checker\nTurn: 3", 3), [], mem)
        self.assertNotIn("unpayable", mem)

    def test_one_payment_that_never_goes_away_is_reported(self):
        mem = {}
        for _poll in range(9):
            move = cc.next_move(self.state(self.PAY, 1), [], mem)
        self.assertIn("unpayable", mem)
        self.assertEqual(move, ("cancel",))


@unittest.skipIf(PROBLEM, f"Forge is not ready here: {PROBLEM}")
class LiveAssignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ck = cc.Checker(SAMPLE, None, 7)
        cls.ck.start()

    @classmethod
    def tearDownClass(cls):
        cls.ck.stop()

    def setUp(self):
        self.assertTrue(self.ck.clean(), "could not get back to a clean board")
        self.ck.new_engine_lines()

    def wait_request(self, kind, seconds=25, pass_priority=False):
        """The next request of this kind (others are answered the usual way). pass_priority: press OK now and then, as a player moving on to combat damage."""
        s = self.ck.s
        end = time.time() + seconds
        last_ok = time.time()
        while time.time() < end:
            s.poll()
            for r in s.requests:
                if r.get("kind") == kind:
                    return r
                s.answer(r, cc.request_answer(r, {}))
            if pass_priority and time.time() - last_ok > 1.0 and s.state and s.state["prompt"]["ok"]["enabled"]:
                s.ok()
                last_ok = time.time()
            time.sleep(0.03)
        self.fail("no %r request arrived (prompt: %s)" % (kind, ((s.state or {}).get("prompt") or {}).get("message")))

    def ai_life(self):
        return self.ck.s.opponents()[0]["life"]

    def wait_life(self, want, seconds=15):
        end = time.time() + seconds
        while time.time() < end:
            self.ck.s.poll()
            if self.ai_life() == want:
                return True
            time.sleep(0.05)
        return False

    def graveyard(self):
        return [c["name"] for c in self.ck.s.opponents()[0]["zones"]["graveyard"]]

    def wait_bears_dead(self, count=2, seconds=15):
        """Life and the dead creatures reach the table in separate snapshots, so a test must wait for each (one run saw life 3 with the Bears not yet in the graveyard)."""
        end = time.time() + seconds
        while time.time() < end:
            self.ck.s.poll()
            if self.graveyard().count("Grizzly Bears") >= count:
                return True
            time.sleep(0.05)
        return False

    def start_combat(self):
        self.ck.s.setup(combat_lines())
        return self.wait_request("assign", 40, pass_priority=True)

    def test_a_blocked_trampler_asks_how_to_split_and_the_excess_reaches_the_player(self):
        req = self.start_combat()
        self.assertEqual((req["mode"], req["total"]), ("damage", 6))
        self.assertFalse(req["may_skip"])
        self.assertEqual([r["kind"] for r in req["rows"]], ["card", "card", "player"])
        self.assertEqual([r["lethal"] for r in req["rows"]], [2, 2, 5])
        self.assertTrue(req["rows"][2]["defender"])
        self.assertEqual(req["source"]["name"], "Colossal Dreadmaw")
        self.assertNotIn("(", req["title"])                     # no Forge card number in the title
        alloc = Allocation.from_request(req)
        self.assertEqual(alloc.answer(), [2, 2, 2])
        self.ck.s.answer(req, alloc.answer())
        self.assertTrue(self.wait_life(3), "the 2 excess damage should reach the player: life %s" % self.ai_life())
        self.assertTrue(self.wait_bears_dead(), "both Bears should die: graveyard %s" % self.graveyard())

    def test_a_split_of_the_players_own_choosing_is_used(self):
        req = self.start_combat()
        self.ck.s.answer(req, [3, 3, 0])                        # everything on the blockers, nothing on the player
        self.assertTrue(self.wait_bears_dead(), "both Bears should die: graveyard %s" % self.graveyard())
        self.assertEqual(self.ai_life(), 5)

    def test_an_illegal_split_is_refused_and_the_automatic_one_is_used(self):
        req = self.start_combat()
        self.ck.s.answer(req, [0, 0, 6])                        # player hit before the blockers have lethal
        self.assertTrue(self.wait_life(3), "life %s" % self.ai_life())
        self.assertTrue(any("reply refused" in ln for ln in self.ck.new_engine_lines()))

    def test_divided_damage_asks_for_the_split_and_at_least_one_each(self):
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Arc Lightning", "humanbattlefield=Mountain;Mountain;Mountain", "aihand=", "aigraveyard=",
                 "aibattlefield=Grizzly Bears;Grizzly Bears", "removesummoningsickness=true"]
        s = self.ck.s
        s.setup(lines)
        end = time.time() + 15
        while time.time() < end and not any(c["name"] == "Arc Lightning" for c in (s.me() or {"zones": {"hand": []}})["zones"]["hand"]):
            s.poll()
            time.sleep(0.05)
        self.ck.pump(1.0)
        s.click_card(next(c for c in s.me()["zones"]["hand"] if c["name"] == "Arc Lightning")["id"])
        self.ck.pump(1.0)
        for b in [c for c in s.opponents()[0]["zones"]["battlefield"] if c["name"] == "Grizzly Bears"]:
            s.click_card(b["id"])
            self.ck.pump(0.8)
        s.ok()
        req = self.wait_request("assign")
        self.assertEqual((req["mode"], req["total"], req["at_least_one"]), ("divide", 3, True))
        self.assertEqual([r["max"] for r in req["rows"]], [3, 3])
        alloc = Allocation.from_request(req)
        self.assertEqual(alloc.amounts, [1, 1])                 # starts with 1 each
        alloc.change(0, 1)
        self.assertTrue(alloc.valid)
        s.answer(req, alloc.answer())                           # 2 to the first, 1 to the second
        for _ in range(8):
            s.poll()
            if s.state["prompt"]["ok"]["enabled"]:
                s.ok()
            self.ck.pump(1.0)
            if self.graveyard():
                break
        gy = self.graveyard()
        self.assertEqual(gy.count("Grizzly Bears"), 1, "2 damage kills one Bear, 1 damage only marks the other")
        survivor = [c for c in s.opponents()[0]["zones"]["battlefield"] if c["name"] == "Grizzly Bears"]
        self.assertEqual(len(survivor), 1)
        self.assertEqual(survivor[0].get("damage"), 1)


if __name__ == "__main__":
    unittest.main()
