# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the table feedback round: floating mana you can click, bold yellow highlights, the library-search zoom, the command
zone that steps aside, and clearer targeting (SOURCE badge, self-target question, no dangling 'targeting' in the log)."""
import copy
import unittest

import pygame

import forge_dialogs as dlg
import forge_log as flog
import forge_table as ft
from tests.forge_fake import load_state
from tests.test_forge_table import SearchDialogTests, click, frame, key, make_gui, move, my_hand, point_for, point_for_card


def is_yellow(px):
    return px[0] > 200 and px[1] > 170 and px[2] < 90


def with_pool(state, pool):
    st = copy.deepcopy(load_state(state) if isinstance(state, str) else state)
    me = [p for p in st["players"] if p["id"] == st["me"]][0]
    me["manaPool"] = dict(pool)
    return st


class FloatingManaTests(unittest.TestCase):
    def test_clicking_a_floating_mana_while_paying_spends_it(self):
        gui = make_gui(with_pool("paying_mana", {"C": 4, "G": 1}))
        self.assertEqual([s for s, _n in gui.pool_items(gui.session.me())], ["G", "C"])
        click(gui, point_for(gui, "pool", sym="C"))
        self.assertEqual(gui.session.commands("mana"), [{"c": "mana", "color": "C"}])
        click(gui, point_for(gui, "pool", sym="G"))
        self.assertEqual(gui.session.commands("mana")[-1], {"c": "mana", "color": "G"})

    def test_clicking_it_when_nothing_is_being_paid_only_explains(self):
        gui = make_gui(with_pool("main1_start", {"C": 4}))
        click(gui, point_for(gui, "pool", sym="C"))
        self.assertEqual(gui.session.commands("mana"), [])
        self.assertIsNotNone(gui.toast)
        self.assertIn("pay", gui.toast[0].lower())

    def test_clicking_the_pill_background_sends_nothing(self):
        gui = make_gui(with_pool("paying_mana", {"C": 4}))
        r = gui.pool_rect
        self.assertEqual(gui.hit_at((r.x + 5, r.centery))[0], "pool")
        click(gui, (r.x + 5, r.centery))
        self.assertEqual(gui.session.commands("mana"), [])

    def test_the_pill_is_yellow_while_paying_and_green_otherwise(self):
        paying = make_gui(with_pool("paying_mana", {"C": 4}))
        r = paying.pool_rect
        self.assertTrue(any(is_yellow(paying.screen.get_at((x, r.y + 1))) for x in range(r.x + 20, r.right - 20)))
        idle = make_gui(with_pool("main1_start", {"C": 4}))
        r = idle.pool_rect
        self.assertFalse(any(is_yellow(idle.screen.get_at((x, r.y + 1))) for x in range(r.x + 20, r.right - 20)))

    def test_no_pill_without_floating_mana(self):
        gui = make_gui("paying_mana")
        self.assertIsNone(gui.pool_rect)

    def test_tooltip_says_what_a_click_does(self):
        gui = make_gui(with_pool("paying_mana", {"C": 4}))
        move(gui, point_for(gui, "pool", sym="C"))
        self.assertIn("spend one colourless", gui.tooltip_text())

    def test_the_pay_hint_talks_about_floating_mana_only_when_there_is_some(self):
        msg = "Llanowar Elves - Creature 1 / 1\n\nPay Mana Cost: {G}"
        self.assertIn("Click floating mana", ft.prompt_view(msg, "Karl", 0, True)[1])
        self.assertNotIn("Click floating mana", ft.prompt_view(msg, "Karl", 0, False)[1])


class HighlightTests(unittest.TestCase):
    def edge(self, gui, card):
        """The most-lit pixel just outside the card's left edge. A band, not one fixed pixel: the combat glow (round 15)
        pulses outward with time.time(), so how far its ring sits from the card shifts frame to frame."""
        gui.screen.fill((0, 0, 0))
        rect = pygame.Rect(300, 300, 130, 180)
        gui.draw_card_flags(card, rect)
        band = [gui.screen.get_at((x, rect.centery)) for x in range(rect.x - 16, rect.x + 4)]
        return max(band, key=lambda px: px[0] + px[1] + px[2])

    def test_everything_clickable_is_bold_yellow(self):
        gui = make_gui()
        for flag in ("weak", "selectable", "highlight"):
            with self.subTest(flag=flag):
                self.assertTrue(is_yellow(self.edge(gui, {"id": 5, flag: True})), flag)

    def test_plain_cards_get_nothing(self):
        self.assertEqual(tuple(self.edge(make_gui(), {"id": 5}))[:3], (0, 0, 0))

    def test_combat_colours_are_unchanged(self):
        gui = make_gui()
        att, blk = self.edge(gui, {"id": 5, "attacking": True}), self.edge(gui, {"id": 5, "blocking": True})
        self.assertTrue(att[0] > att[2] + 40, att)
        self.assertTrue(blk[2] > blk[0] + 40, blk)


class LibrarySearchZoomTests(unittest.TestCase):
    def test_hovering_a_card_in_the_search_list_shows_it_in_the_focus_panel(self):
        gui = make_gui()
        SearchDialogTests("library_request").library_request(gui)
        self.assertIsNotNone(gui.modal)
        p = gui.L.preview
        before = pygame.image.tobytes(gui.screen.subsurface(p).copy(), "RGB")
        rect, _i = gui.modal.tiles[0]
        move(gui, rect.center)
        after = pygame.image.tobytes(gui.screen.subsurface(p).copy(), "RGB")
        self.assertNotEqual(before, after)

    def test_the_zoom_is_drawn_exactly_in_the_focus_panel(self):
        gui = make_gui()
        SearchDialogTests("library_request").library_request(gui)
        gui.screen.fill((0, 0, 0))
        gui.modal.draw_zoom(gui, my_hand(gui)[0])
        p = gui.L.preview
        self.assertNotEqual(tuple(gui.screen.get_at(p.center))[:3], (0, 0, 0))
        self.assertEqual(tuple(gui.screen.get_at((5, gui.screen.get_height() - 5)))[:3], (0, 0, 0))


class CommandZoneTests(unittest.TestCase):
    def on_board(self, everyone=True):
        st = copy.deepcopy(load_state("main1_start"))
        for p in st["players"]:
            if everyone or p["id"] == st["me"]:
                p["zones"]["battlefield"] += p["zones"]["command"]
                p["zones"]["command"] = []
        return st

    def spy(self, gui):
        calls = []
        real = gui.draw_command_zone
        gui.draw_command_zone = lambda *a, **k: (calls.append(a[0]["id"]), real(*a, **k))[1]
        frame(gui)
        return calls

    def test_the_zone_is_drawn_while_a_commander_waits_in_it(self):
        gui = make_gui()
        self.assertGreater(gui.L.cmd_rect.w, 0)
        self.assertTrue(self.spy(gui))

    def test_the_zone_steps_aside_when_the_commander_is_on_the_battlefield(self):
        shown, hidden = make_gui(), make_gui(self.on_board())
        self.assertEqual(hidden.L.cmd_rect.w, 0)
        self.assertEqual(self.spy(hidden), [])
        self.assertLess(hidden.L.hand.x, shown.L.hand.x)                # the room went to the hand
        self.assertGreater(hidden.L.hand.w, shown.L.hand.w)

    def test_it_comes_back_when_the_commander_returns(self):
        gui = make_gui(self.on_board())
        self.assertEqual(gui.L.cmd_rect.w, 0)
        gui.session.state = load_state("main1_start")
        frame(gui, 2)
        self.assertGreater(gui.L.cmd_rect.w, 0)

    def test_only_my_zone_hides_when_only_my_commander_is_out(self):
        gui = make_gui(self.on_board(everyone=False))
        self.assertEqual(gui.L.cmd_rect.w, 0)
        calls = self.spy(gui)
        self.assertNotIn(gui.session.me()["id"], calls)
        self.assertTrue(gui.commanders_on_board(gui.session.me()))
        self.assertFalse(gui.commanders_on_board(gui.session.opponents()[0]))

    def test_a_player_without_commanders_never_counts_as_on_board(self):
        gui = make_gui()
        self.assertFalse(gui.commanders_on_board({"zones": {}, "commanders": []}))
        self.assertFalse(gui.commanders_on_board(None))


TARGET_MSG = ("Mirage Mirror (6) - {2}: Mirage Mirror becomes a copy of target artifact, creature, enchantment, or land until end of turn."
              "\n\nSelect target artifact, creature, enchantment, or land.")


def target_state():
    st = copy.deepcopy(load_state("main1_start"))
    me = [p for p in st["players"] if p["id"] == st["me"]][0]
    hand = me["zones"]["hand"]
    src = copy.deepcopy(hand[0])
    src.update(id=6, name="Mirage Mirror", isLand=False, selectable=True, weak=False)
    other = copy.deepcopy(hand[1])
    other.update(id=50, name="Basalt Monolith", isLand=False, selectable=True, weak=False)
    me["zones"]["battlefield"] = [src, other]
    st["prompt"] = {"message": TARGET_MSG, "ok": {"label": "OK", "enabled": False, "focus": False},
                    "cancel": {"label": "Cancel", "enabled": True}, "selecting": True, "selMin": 1, "selMax": 1}
    return st


class TargetPromptTests(unittest.TestCase):
    def test_the_prompt_is_taken_apart(self):
        t = ft.parse_target_prompt(TARGET_MSG)
        self.assertEqual((t["source"], t["id"], t["cost"]), ("Mirage Mirror", 6, "{2}"))
        self.assertEqual(t["what"], "artifact, creature, enchantment, or land")
        self.assertTrue(t["effect"].startswith("Mirage Mirror becomes a copy of target"))

    def test_a_spell_without_a_cost_and_other_prompts(self):
        t = ft.parse_target_prompt("Lightning Bolt (12) - Lightning Bolt deals 3 damage to any target.\n\nSelect target creature, planeswalker, or player.")
        self.assertEqual((t["source"], t["id"], t["cost"]), ("Lightning Bolt", 12, ""))
        self.assertIsNone(ft.parse_target_prompt("Priority: Karl\nTurn: 1 (Karl)\nPhase: Main phase, precombat\nStack: Empty"))
        self.assertIsNone(ft.parse_target_prompt("Select target creature."))
        self.assertIsNone(ft.parse_target_prompt(""))
        self.assertIsNone(ft.parse_target_prompt(None))

    def test_the_bar_says_it_is_a_target_and_what_to_do(self):
        head, hint, ok = ft.prompt_view(TARGET_MSG, "Karl")
        self.assertIn("Choose the target", head)
        self.assertIn("Mirage Mirror", head)
        self.assertNotIn("(6)", head)
        self.assertIn("SOURCE", hint)
        self.assertIn("{2}", hint)
        self.assertIn("Cancel", hint)
        self.assertIsNone(ok)

    def test_paying_afterwards_still_reads_as_a_payment(self):
        head, _hint, _ = ft.prompt_view("Mirage Mirror (6) - {2}: Mirage Mirror becomes a copy of target thing.\n\nPay Mana Cost: {2}", "Karl")
        self.assertEqual(head, "Pay {2}  -  Mirage Mirror")

    def test_the_source_gets_an_orange_frame_and_targets_do_not(self):
        gui = make_gui(target_state())
        self.assertEqual(gui.target_prompt()["id"], 6)
        gui.screen.fill((0, 0, 0))
        rect = pygame.Rect(300, 300, 130, 180)
        gui.draw_card_flags({"id": 6, "selectable": True}, rect)
        src = gui.screen.get_at((rect.x + 1, rect.centery))
        gui.screen.fill((0, 0, 0))
        gui.draw_card_flags({"id": 50, "selectable": True}, rect)
        other = gui.screen.get_at((rect.x + 1, rect.centery))
        self.assertTrue(src[0] > 200 and 110 < src[1] < 190 and src[2] < 110 and not is_yellow(src), tuple(src))    # orange
        self.assertTrue(is_yellow(other), tuple(other))

    def test_no_badge_outside_a_target_prompt(self):
        self.assertIsNone(make_gui().target_prompt())

    def test_clicking_another_permanent_targets_it_at_once(self):
        gui = make_gui(target_state())
        click(gui, point_for_card(gui, 50))
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": 50}])
        self.assertIsNone(gui.modal)

    def test_clicking_the_source_asks_first(self):
        gui = make_gui(target_state())
        click(gui, point_for_card(gui, 6))
        self.assertEqual(gui.session.commands("card"), [])
        self.assertIsInstance(gui.modal, dlg.QuestionDialog)
        self.assertIn("Mirage Mirror itself", gui.modal.title)

    def test_enter_does_not_confirm_and_y_does(self):
        gui = make_gui(target_state())
        click(gui, point_for_card(gui, 6))
        key(gui, pygame.K_RETURN)                           # Enter must not confirm by accident
        self.assertEqual(gui.session.commands("card"), [])
        click(gui, point_for_card(gui, 6))
        key(gui, pygame.K_n)
        self.assertEqual(gui.session.commands("card"), [])
        self.assertIsNone(gui.modal)
        click(gui, point_for_card(gui, 6))
        key(gui, pygame.K_y)
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": 6}])

    def test_clicking_the_source_outside_a_target_prompt_is_unchanged(self):
        st = target_state()
        st["prompt"] = load_state("main1_start")["prompt"]
        gui = make_gui(st)
        click(gui, point_for_card(gui, 6))
        self.assertIsNone(gui.modal)
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": 6}])

    def test_the_target_bar_renders_at_several_sizes(self):
        for size, scale in (((1360, 840), 1.0), ((900, 600), 1.0), ((1920, 1080), 1.5), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                self.assertTrue(make_gui(target_state(), size, scale).hits)


class ActionLogTests(unittest.TestCase):
    def test_a_dangling_targeting_is_dropped_from_the_log_line(self):
        m = flog._ACTION.match("Karl activated Mirage Mirror targeting")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("c"), "Mirage Mirror")
        self.assertIsNone(m.group("t"))
        m = flog._ACTION.match("Karl activated Mirage Mirror targeting Basalt Monolith")
        self.assertEqual((m.group("c"), m.group("t")), ("Mirage Mirror", "Basalt Monolith"))
        self.assertEqual(flog._ACTION.match("Karl cast Llanowar Elves").group("c"), "Llanowar Elves")


if __name__ == "__main__":
    unittest.main()
