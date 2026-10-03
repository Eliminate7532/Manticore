# SPDX-License-Identifier: GPL-3.0-or-later
"""The Forge table GUI, driven headlessly with saved game snapshots and simulated clicks/keys."""
import copy
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_dialogs as dlg
import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_log, load_state

FIXTURE_NAMES = ["coin_toss", "mulligan", "main1_start", "main1_lands", "paying_mana", "stack_one", "stack_two_late",
                 "declare_attackers", "declare_blockers", "combat_damage"]


def make_gui(state="main1_start", size=(1360, 840), scale=None, settings=None, log=True):
    st = load_state(state) if isinstance(state, str) else state
    session = FakeSession(st, load_log() if log else None)
    gui = ft.ForgeTable(session, StubStore(), settings_path=settings, window_size=size)
    if scale is not None:
        gui.text_scale = scale
    frame(gui, 3)
    return gui


def frame(gui, n=1):
    for _ in range(n):
        gui.sync()
        gui.render()


def click(gui, pos, button=1):
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button))
    frame(gui)


def move(gui, pos):
    gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0)))
    frame(gui)


def key(gui, k, mod=0, unicode=""):
    gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod, unicode=unicode))
    frame(gui)


def point_for_card(gui, cid):
    """A screen point that really lands on this card (overlapping cards hide parts of each other)."""
    for rect, kind, data in reversed(gui.hits):
        if kind == "card" and data["card"]["id"] == cid:
            for dx in range(2, rect.w, 4):
                for dy in range(2, rect.h, 4):
                    p = (rect.x + dx, rect.y + dy)
                    k, d = gui.hit_at(p)
                    if k == "card" and d["card"]["id"] == cid:
                        return p
    raise AssertionError(f"card {cid} is not clickable on screen")


def point_for(gui, kind, **match):
    for rect, k, data in reversed(gui.hits):
        if k == kind and all(data.get(a) == b for a, b in match.items()):
            return rect.center
    raise AssertionError(f"no {kind} {match} on screen")


def my_hand(gui):
    return gui.session.me()["zones"]["hand"]


class RenderTests(unittest.TestCase):
    def test_every_recorded_state_renders_at_several_sizes(self):
        for name in FIXTURE_NAMES:
            for size, scale in (((1360, 840), 1.0), ((900, 600), 1.0), ((1920, 1080), 1.5), ((1100, 700), 2.0)):
                with self.subTest(state=name, size=size, scale=scale):
                    gui = make_gui(name, size, scale)
                    self.assertEqual(gui.screen.get_size(), size)
                    self.assertTrue(gui.hits)

    def test_splash_before_the_first_state(self):
        gui = make_gui(None)
        frame(gui)
        self.assertFalse(gui.hits)

    def test_error_splash_when_forge_dies_early(self):
        gui = make_gui(None)
        gui.session.fatal = "Java exploded"
        frame(gui, 2)

    def test_pod_of_four_renders(self):
        st = copy.deepcopy(load_state("declare_blockers"))
        base = [p for p in st["players"] if p["id"] != st["me"]][0]
        for k in (1, 2):
            p = copy.deepcopy(base)
            p["id"], p["name"] = 10 + k, f"AI {k + 1}"
            for z in p["zones"].values():
                for c in z:
                    c["id"] += 1000 * k
            st["players"].append(p)
        gui = make_gui(st, (1440, 900))
        self.assertEqual(len(gui.L.opp_rects), 3)

    def test_layout_keeps_everything_inside_the_window(self):
        for size in ((900, 600), (1360, 840), (1920, 1080)):
            gui = make_gui("main1_start", size)
            L = gui.L
            window = pygame.Rect(0, 0, *size)
            for r in (L.main, L.right, L.my_bf, L.my_info, L.bar, L.hand, L.cmd_rect, *L.opp_rects):
                self.assertTrue(window.contains(r), (size, r))
            self.assertGreater(L.my_row_h, 50)

    def test_the_focus_card_is_bigger_and_anchored_to_the_top_of_the_right_column(self):
        """Karl: 'I want to make the focus card bigger, scale it to the top of the screen.'"""
        for size in ((900, 600), (1360, 840), (1920, 1080)):
            gui = make_gui("main1_start", size)
            L = gui.L
            self.assertTrue(pygame.Rect(0, 0, *size).contains(L.preview), (size, L.preview))
            self.assertEqual(L.preview.y, L.right.y)                    # flush to the top of the right column
            self.assertGreaterEqual(L.preview.h, L.right.h * 0.55)      # meaningfully larger than before (was 0.5/0.36)

    def test_lands_of_the_same_kind_are_grouped(self):
        gui = make_gui("stack_two_late")
        rows = gui.slots[gui.my_id()]
        counts = sorted(len(s.cards) for s in rows[1])
        self.assertIn(3, counts)                     # three tapped Forests in one pile
        self.assertLess(len(rows[1]), len(gui.session.me()["zones"]["battlefield"]))

    def test_attachments_ride_with_their_host(self):
        st = copy.deepcopy(load_state("stack_two_late"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        bf = me["zones"]["battlefield"]
        host = next(c for c in bf if c.get("isCreature"))
        aura = copy.deepcopy(bf[-1])
        aura.update(id=999, name="Test Aura", isCreature=False, isLand=False, attachedTo=host["id"], tapped=False)
        bf.append(aura)
        gui = make_gui(st)
        slots = [s for row in gui.slots[me["id"]] for s in row]
        self.assertEqual(sum(len(s.cards) for s in slots), len(bf) - 1)
        self.assertTrue(any(s.attached and s.attached[0]["id"] == 999 for s in slots))
        self.assertIn(999, gui.card_rects)


class PromptTextTests(unittest.TestCase):
    def test_priority_sentence_is_shortened(self):
        self.assertEqual(ft.pretty_prompt("Priority: Karl Turn: 10 (AI 1 (Kinnan)) Phase: Main phase, postcombat Stack: 2 to Resolve."),
                         "Your priority  -  Main phase, postcombat  -  Stack: 2 to Resolve")
        self.assertEqual(ft.pretty_prompt("Select creatures to attack"), "Select creatures to attack")


class ClickTests(unittest.TestCase):
    def test_clicking_a_glowing_hand_card_sends_that_card(self):
        gui = make_gui("main1_start")
        weak = [c for c in my_hand(gui) if c.get("weak")]
        self.assertTrue(weak)
        click(gui, point_for_card(gui, weak[0]["id"]))
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": weak[0]["id"]}])

    def test_ok_cancel_undo_buttons(self):
        gui = make_gui("main1_start")
        click(gui, point_for(gui, "button", name="ok"))
        click(gui, point_for(gui, "button", name="cancel"))
        click(gui, point_for(gui, "button", name="undo"))
        self.assertEqual([c["c"] for c in gui.session.sent], ["ok", "cancel", "undo"])

    def test_disabled_buttons_do_nothing(self):
        gui = make_gui("declare_blockers")
        self.assertFalse(gui.prompt()["cancel"]["enabled"])
        with self.assertRaises(AssertionError):
            point_for(gui, "button", name="cancel")

    def test_clicking_a_player_sends_the_player(self):
        gui = make_gui("declare_attackers")
        opp = gui.session.opponents()[0]
        click(gui, point_for(gui, "player", id=opp["id"]))
        self.assertEqual(gui.session.commands("player"), [{"c": "player", "id": opp["id"]}])

    def test_alpha_strike_is_forges_own_cancel_button_while_declaring_attackers(self):
        gui = make_gui("declare_attackers")
        self.assertEqual(gui.prompt()["cancel"]["label"], "Alpha Strike")
        click(gui, point_for(gui, "button", name="cancel"))
        self.assertEqual(gui.session.commands("cancel"), [{"c": "cancel"}])

    def test_the_alpha_strike_button_is_named_full_send_on_screen_but_still_works(self):
        gui = make_gui("declare_attackers")
        shown = []
        real = gui.draw_button
        gui.draw_button = lambda rect, label, name, *a, **k: (shown.append((name, label)), real(rect, label, name, *a, **k))[1]
        frame(gui)
        self.assertIn(("cancel", "Full Send"), shown)
        self.assertNotIn("Alpha Strike", [lab for _n, lab in shown])
        click(gui, point_for(gui, "button", name="cancel"))
        self.assertEqual(gui.session.commands("cancel"), [{"c": "cancel"}])
        gui.session.sent.clear()
        key(gui, pygame.K_a)                                 # the A shortcut still finds Forge's own "Alpha Strike" label
        self.assertEqual(gui.session.commands("cancel"), [{"c": "cancel"}])

    def test_phase_pill_toggles_my_stop_and_right_click_toggles_theirs(self):
        gui = make_gui("main1_start")
        mine = list(gui.state["stops"]["mine"])
        self.assertIn("MAIN1", mine)
        click(gui, point_for(gui, "pill", phase="MAIN1"))
        cmd = gui.session.commands("stops")[-1]
        self.assertEqual(cmd["mine"], True)
        self.assertNotIn("MAIN1", cmd["phases"])
        click(gui, point_for(gui, "pill", phase="DRAW"), button=3)
        cmd = gui.session.commands("stops")[-1]
        self.assertEqual(cmd["mine"], False)
        self.assertIn("DRAW", cmd["phases"])
        click(gui, point_for(gui, "stop", phase="MAIN2", who="mine"))
        self.assertNotIn("MAIN2", gui.session.commands("stops")[-1]["phases"])

    def test_right_click_pins_a_card_in_the_preview(self):
        gui = make_gui("main1_start")
        cid = my_hand(gui)[0]["id"]
        click(gui, point_for_card(gui, cid), button=3)
        self.assertEqual(gui.pinned, cid)
        move(gui, (5, 5))
        self.assertEqual(gui.preview_card()["id"], cid)
        click(gui, point_for_card(gui, cid), button=3)
        self.assertIsNone(gui.pinned)

    def test_hover_shows_the_card_in_the_preview(self):
        gui = make_gui("stack_two_late")
        card = gui.session.me()["zones"]["battlefield"][0]
        move(gui, point_for_card(gui, card["id"]))
        self.assertEqual(gui.preview_card()["id"], gui.hit_at(gui.mouse)[1]["card"]["id"])

    def test_a_spotlighted_opponent_card_shows_in_the_preview_when_nothing_is_hovered_or_pinned(self):
        """Karl: 'all cards being played on opponent turns should pop up in the focus area.' The board already
        glows the newest opponent action (self.spot, via add_feed) - the preview panel should just follow it."""
        gui = make_gui("stack_two_late")
        card = gui.session.opponents()[0]["zones"]["battlefield"][0]
        gui.spot = (card["name"], time.time() + 3.0)
        move(gui, (5, 5))                                    # off any card, nothing pinned
        self.assertIsNone(gui.pinned)
        shown = gui.preview_card()
        self.assertIsNotNone(shown)
        self.assertEqual(shown["name"], card["name"])

    def test_a_hovered_card_still_wins_over_the_spotlight(self):
        gui = make_gui("stack_two_late")
        opp_card = gui.session.opponents()[0]["zones"]["battlefield"][0]
        gui.spot = (opp_card["name"], time.time() + 3.0)
        mine = gui.session.me()["zones"]["battlefield"][0]
        move(gui, point_for_card(gui, mine["id"]))
        self.assertEqual(gui.preview_card()["id"], mine["id"])

    def test_the_spotlight_expires_and_stops_driving_the_preview(self):
        gui = make_gui("stack_two_late")
        card = gui.session.opponents()[0]["zones"]["battlefield"][0]
        gui.spot = (card["name"], time.time() - 1.0)         # already expired
        move(gui, (5, 5))
        self.assertIsNone(gui.preview_card())

    def test_clicking_a_pile_of_lands_hits_an_actionable_one(self):
        st = copy.deepcopy(load_state("stack_two_late"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        forests = [c for c in me["zones"]["battlefield"] if c["name"] == "Forest" and not c["tapped"]]
        self.assertGreaterEqual(len(forests), 2)
        for c in forests:
            c["weak"] = 1                                # Forge says any of them could be tapped for mana
        gui = make_gui(st)
        pile = next(s for row in gui.slots[me["id"]] for s in row if len(s.cards) > 1 and not s.tapped)
        cid = gui.pick(pile, pile.cards[0], 0, 2)["id"]
        self.assertIn(cid, [c["id"] for c in forests])
        clicked = [d["card"]["id"] for _r, k, d in gui.hits if k == "card" and d["card"]["id"] in {c["id"] for c in pile.cards}]
        self.assertTrue(clicked)
        click(gui, [r for r, k, d in gui.hits if k == "card" and d["card"]["id"] == clicked[-1]][-1].center)
        self.assertEqual(len(gui.session.commands("card")), 1)

    def test_clicks_do_nothing_before_the_game_starts(self):
        gui = make_gui(None)
        click(gui, (300, 300))
        self.assertEqual(gui.session.sent, [])


class KeyTests(unittest.TestCase):
    def test_space_and_enter_press_ok(self):
        gui = make_gui("main1_start")
        key(gui, pygame.K_SPACE)
        key(gui, pygame.K_RETURN)
        self.assertEqual([c["c"] for c in gui.session.sent], ["ok", "ok"])

    def test_escape_never_ends_the_turn_but_e_does(self):
        gui = make_gui("main1_start")
        self.assertEqual(gui.prompt()["cancel"]["label"], "End Turn")
        key(gui, pygame.K_ESCAPE)
        self.assertEqual(gui.session.commands("cancel"), [])
        key(gui, pygame.K_e)
        self.assertEqual(len(gui.session.commands("cancel")), 1)

    def test_escape_cancels_a_real_cancel(self):
        st = copy.deepcopy(load_state("paying_mana"))
        st["prompt"]["cancel"] = {"label": "Cancel", "enabled": True}
        gui = make_gui(st)
        key(gui, pygame.K_ESCAPE)
        self.assertEqual(len(gui.session.commands("cancel")), 1)

    def test_undo_and_alpha_keys(self):
        gui = make_gui("declare_attackers")
        key(gui, pygame.K_ESCAPE)                        # Esc must not fire Full Send
        self.assertEqual(gui.session.sent, [])
        key(gui, pygame.K_a)                             # A does, because that is what the button says
        key(gui, pygame.K_z, mod=pygame.KMOD_CTRL)
        key(gui, pygame.K_u)
        self.assertEqual([c["c"] for c in gui.session.sent], ["cancel", "undo", "undo"])

    def test_alpha_key_ignored_when_the_button_is_not_alpha_strike(self):
        gui = make_gui("main1_start")
        key(gui, pygame.K_a)
        self.assertEqual(gui.session.sent, [])

    def test_help_opens_and_closes(self):
        gui = make_gui("main1_start")
        key(gui, pygame.K_h)
        self.assertIsInstance(gui.modal, dlg.HelpDialog)
        key(gui, pygame.K_ESCAPE)
        frame(gui)
        self.assertIsNone(gui.modal)

    def test_modal_swallows_game_keys_and_clicks(self):
        gui = make_gui("main1_start")
        key(gui, pygame.K_h)
        key(gui, pygame.K_SPACE)             # closes help, must not also press OK
        click(gui, point_for(gui, "button", name="ok"))
        self.assertEqual([c["c"] for c in gui.session.sent], ["ok"])


class WindowSettingsTests(unittest.TestCase):
    def test_text_scale_steps_and_is_saved_without_losing_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"text_scale": 1.0, "other_table_setting": 7}, f)
            gui = make_gui("main1_start", settings=path)
            key(gui, pygame.K_EQUALS)
            self.assertEqual(gui.text_scale, 1.25)
            key(gui, pygame.K_MINUS)
            key(gui, pygame.K_MINUS)
            self.assertEqual(gui.text_scale, 1.0)         # already the minimum
            key(gui, pygame.K_PLUS)
            with open(path) as f:
                saved = json.load(f)
            self.assertEqual(saved["text_scale"], 1.25)
            self.assertEqual(saved["other_table_setting"], 7)
            again = make_gui("main1_start", settings=path)
            self.assertEqual(again.text_scale, 1.25)

    def test_bad_settings_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                f.write("{not json")
            gui = make_gui("main1_start", settings=path)
            self.assertEqual(gui.text_scale, 1.0)


class GameOverLayoutTests(unittest.TestCase):
    def test_focus_card_and_log_reach_the_top_once_the_game_is_over(self):
        """Karl's request (2026-09-22): once a match ends, the top bar has nothing left in it but the settings cog -
        no turn counter, no phase pills - so the board and the right-hand column (focus card + log) were leaving a
        tall strip of dead space up top for no reason. They now start right under the window edge instead, and the
        cog stays exactly where it always was and stays clickable even though the taller card now reaches up behind
        it (draw_frame redraws the cog after the card so it is never hidden)."""
        st = copy.deepcopy(load_state("main1_start"))
        gui = make_gui(st)
        self.assertFalse(gui.L.game_over)
        right_before, main_before, preview_h_before = gui.L.right.y, gui.L.main.y, gui.L.preview.h
        st2 = copy.deepcopy(st)
        st2["gameOver"], st2["winner"] = True, "Karl"
        gui.session.handle(st2)
        frame(gui)                         # let sync() notice the new state: Round AD2b's VICTORY screen comes first
        self.assertEqual(gui.end_screen.kind, "won")
        key(gui, pygame.K_SPACE)           # past it, to the "Game Over" dialog
        self.assertIsInstance(gui.modal, dlg.GameOverDialog)
        key(gui, pygame.K_ESCAPE)          # dismiss it, as in test_game_over_dialog_appears_once
        frame(gui, 2)
        self.assertIsNone(gui.modal)
        self.assertTrue(gui.L.game_over)
        self.assertLess(gui.L.right.y, right_before)
        self.assertLess(gui.L.main.y, main_before)
        self.assertGreaterEqual(gui.L.preview.h, preview_h_before)
        self.assertEqual(point_for(gui, "button", name="settings"), gui.cog_rect.center)
        self.assertFalse([k for _r, k, _d in gui.hits if k in ("pill", "stop")])   # no stale turn/phase info left to show


class DialogTests(unittest.TestCase):
    def request(self, gui, req):
        gui.session.requests.append(req)
        frame(gui)

    def test_confirm_yes_no_and_default(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 11, "kind": "confirm", "title": "Exile it?", "default": True, "options": ["Yes", "No"]})
        self.assertIsInstance(gui.modal, dlg.ConfirmDialog)
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply"), [{"c": "reply", "id": 11, "value": True}])
        frame(gui)
        self.assertIsNone(gui.modal)
        self.request(gui, {"id": 12, "kind": "confirm", "title": "Again?", "default": True, "options": ["Keep", "Mulligan"]})
        click(gui, gui.modal.buttons[0][0].center if gui.modal.buttons[0][1] == "no" else gui.modal.buttons[1][0].center)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 12, "value": False})

    def test_engine_waits_no_more_after_the_dialog_is_answered(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 1, "kind": "confirm", "title": "?", "default": False, "options": []})
        key(gui, pygame.K_y)
        self.assertEqual(len(gui.session.requests), 0)

    def cards(self, gui, n=4):
        """n distinct cards (different names, so the dialog does not group them into stacks)."""
        out = []
        for i, c in enumerate(my_hand(gui)[:n]):
            c = copy.deepcopy(c)
            c["name"] = f"{c['name']} #{i}"
            out.append({"kind": "card", "card": c})
        return out

    def test_choose_needs_the_right_number_before_done(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 3, "kind": "choose", "title": "Pick two", "min": 2, "max": 2, "items": self.cards(gui)})
        d = gui.modal
        self.assertFalse(d.valid())
        d.toggle(0)
        self.assertFalse(d.valid())
        d.toggle(2)
        self.assertTrue(d.valid())
        d.toggle(3)                                      # a third pick is refused
        self.assertEqual(d.sel, [0, 2])
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply"), [{"c": "reply", "id": 3, "value": [0, 2]}])

    def test_choose_by_clicking_a_card_then_done(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 4, "kind": "choose", "title": "Pick one", "min": 1, "max": 1, "items": self.cards(gui)})
        first_tile = gui.modal.tiles[1][0]
        click(gui, first_tile.center)
        self.assertEqual(gui.modal.sel, [1])
        gui.modal.last_click = (None, 0.0)                # (a second click that is not a quick double-click)
        click(gui, first_tile.move(0, 0).center)          # clicking again un-picks
        self.assertEqual(gui.modal.sel, [])
        click(gui, gui.modal.tiles[2][0].center)
        click(gui, gui.modal.tiles[3][0].center)           # single choice: replaces the pick
        self.assertEqual(gui.modal.sel, [3])
        done = next(r for r, n in gui.modal.buttons if n == "done")
        click(gui, done.center)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 4, "value": [3]})

    def test_optional_choice_can_be_skipped(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 5, "kind": "choose_optional", "title": "Maybe", "min": 0, "max": 1, "items": self.cards(gui)})
        skip = next(r for r, n in gui.modal.buttons if n == "skip")
        click(gui, skip.center)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 5, "value": []})

    def test_text_and_player_choices(self):
        gui = make_gui("main1_start")
        items = [{"kind": "text", "label": "Draw a card"}, {"kind": "player", "id": 1, "name": "AI"},
                 {"kind": "text", "label": "Gain 3 life"}]
        self.request(gui, {"id": 6, "kind": "choose", "title": "Mode", "min": 1, "max": 1, "items": items})
        click(gui, gui.modal.tiles[2][0].center)
        click(gui, next(r for r, n in gui.modal.buttons if n == "done").center)
        self.assertEqual(gui.session.commands("reply")[-1]["value"], [2])

    def test_order_dialog_returns_clicks_in_order_and_needs_all(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 7, "kind": "order", "title": "Order triggers", "min": 3, "max": 3, "items": self.cards(gui, 3)})
        d = gui.modal
        for i in (2, 0):
            d.toggle(i)
        self.assertFalse(d.valid())
        d.toggle(1)
        self.assertTrue(d.valid())
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1]["value"], [2, 0, 1])

    def test_order_dialog_has_a_select_all_button(self):
        """Karl's request (2026-09-22, after the 'select order for simultaneous abilities' screenshot): ordering
        two-or-more triggers meant clicking each one by hand even though the order rarely matters. A 'Select all'
        button now fills the rest in list order in one click, and Reset still clears it."""
        gui = make_gui("main1_start")
        self.request(gui, {"id": 43, "kind": "order", "title": "Select order for simultaneous abilities",
                           "min": 2, "max": 2, "items": self.cards(gui, 2)})
        d = gui.modal
        self.assertFalse(d.can_select_all() is False and d.sel)   # nothing picked yet, so it should be available
        self.assertTrue(d.can_select_all())
        all_btn = next(r for r, n in gui.modal.buttons if n == "select_all")
        click(gui, all_btn.center)
        self.assertEqual(d.sel, [0, 1])
        self.assertFalse(d.can_select_all())                      # nothing left to add
        reset = next(r for r, n in gui.modal.buttons if n == "reset")
        click(gui, reset.center)
        self.assertEqual(d.sel, [])
        d.toggle(1)
        click(gui, next(r for r, n in gui.modal.buttons if n == "select_all").center)
        self.assertEqual(d.sel, [1, 0])                            # keeps the manual pick first, then fills the rest
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1]["value"], [1, 0])

    def test_choose_some_in_order_can_pick_none_or_a_subset(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 30, "kind": "order", "title": "Choose cards to reveal and their order",
                           "min": 0, "max": 2, "items": self.cards(gui, 2)})
        d = gui.modal
        self.assertNotIsInstance(d, dlg.OpeningHandDialog)
        self.assertTrue(d.valid())                        # none is a legal answer
        d.toggle(1)
        self.assertTrue(d.valid())
        d.toggle(0)
        d.toggle(1)                                       # picking again removes it
        self.assertEqual(d.sel, [0])
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 30, "value": [0]})
        self.request(gui, {"id": 31, "kind": "order", "title": "Reveal", "min": 0, "max": 2, "items": self.cards(gui, 2)})
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 31, "value": []})

    def test_opening_hand_question_starts_with_every_card_switched_on(self):
        """Forge's list for Gemstone Caverns started with nothing chosen, so pressing Done quietly skipped the card."""
        gui = make_gui("main1_start")
        self.request(gui, {"id": 40, "kind": "order", "title": "Choose cards to activate from opening hand and their order",
                           "min": 0, "max": 2, "items": self.cards(gui, 2)})
        d = gui.modal
        self.assertIsInstance(d, dlg.OpeningHandDialog)
        self.assertEqual(d.sel, [0, 1])
        self.assertIn("begin the game", d.title.lower())
        d.toggle(1)                                       # switch the second one off
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 40, "value": [0]})
        self.request(gui, {"id": 41, "kind": "order", "title": "Choose cards to activate from opening hand and their order",
                           "min": 0, "max": 1, "items": self.cards(gui, 1)})
        self.assertEqual(gui.modal.done_label(), "Begin with it")
        click(gui, gui.modal.buttons[[n for _r, n in gui.modal.buttons].index("skip")][0].center)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 41, "value": []})
        self.request(gui, {"id": 42, "kind": "order", "title": "Choose cards to activate from opening hand and their order",
                           "min": 0, "max": 1, "items": self.cards(gui, 1)})
        key(gui, pygame.K_ESCAPE)                         # Escape = no
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 42, "value": []})

    def test_many_cards_scroll_and_never_crash(self):
        gui = make_gui("main1_start", (900, 600))
        items = [{"kind": "card", "card": copy.deepcopy(my_hand(gui)[i % 7])} for i in range(60)]
        for n, it in enumerate(items):
            it["card"]["id"] = 5000 + n
            it["card"]["name"] = f"Card {n}"
        self.request(gui, {"id": 8, "kind": "choose", "title": "Pick", "min": 1, "max": 1, "items": items})
        self.assertGreater(gui.modal.content_h, gui.modal.body.h)
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-5))
        frame(gui)
        self.assertGreater(gui.modal.scroll, 0)

    def test_input_dialog_types_a_number(self):
        gui = make_gui("main1_start")
        self.request(gui, {"id": 9, "kind": "input", "title": "X?", "initial": "2", "numeric": True, "options": []})
        key(gui, pygame.K_BACKSPACE)
        key(gui, pygame.K_5, unicode="5")
        key(gui, pygame.K_a, unicode="a")                  # letters are ignored for numbers
        key(gui, pygame.K_UP)
        key(gui, pygame.K_RETURN)
        self.assertEqual(gui.session.commands("reply")[-1], {"c": "reply", "id": 9, "value": "6"})

    def test_look_at_these_cards_message_opens_a_viewer_but_deck_warnings_do_not(self):
        gui = make_gui("main1_start")
        gui.session.handle({"t": "info", "title": "AI can't play these cards well from AI 1's  Deck", "items": self.cards(gui, 2)})
        frame(gui)
        self.assertIsNone(gui.modal)
        gui.session.handle({"t": "info", "title": "Looking at cards in AI's hand", "items": self.cards(gui, 2)})
        frame(gui)
        self.assertIsInstance(gui.modal, dlg.ChooseDialog)
        self.assertEqual(gui.modal.mode, "view")
        key(gui, pygame.K_RETURN)
        self.assertIsNone(gui.modal)

    def test_engine_messages_are_shown(self):
        gui = make_gui("main1_start")
        gui.session.handle({"t": "message", "title": "Note", "text": "The AI conceded."})
        frame(gui)
        self.assertIsInstance(gui.modal, dlg.MessageDialog)

    def test_zone_viewer_opens_from_the_graveyard_chip_and_can_act_on_glowing_cards(self):
        st = copy.deepcopy(load_state("main1_start"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        card = copy.deepcopy(me["zones"]["hand"][0])
        card.update(id=777, zone="Graveyard", weak=1)
        me["zones"]["graveyard"].append(card)
        gui = make_gui(st)
        click(gui, point_for(gui, "zone", player=me["id"], zone="graveyard"))
        self.assertIsInstance(gui.modal, dlg.ChooseDialog)
        click(gui, gui.modal.tiles[0][0].center)
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": 777}])
        frame(gui)
        self.assertIsNone(gui.modal)

    def test_empty_zone_chip_is_not_clickable(self):
        gui = make_gui("main1_start")
        me = gui.session.me()
        with self.assertRaises(AssertionError):
            point_for(gui, "zone", player=me["id"], zone="exile")

    def test_game_over_dialog_appears_once(self):
        st = copy.deepcopy(load_state("combat_damage"))
        st["gameOver"], st["winner"] = True, "Karl"
        gui = make_gui(st)
        self.assertEqual(gui.end_screen.kind, "won")   # Round AD2b: VICTORY first, then the dialog
        key(gui, pygame.K_SPACE)
        self.assertIsInstance(gui.modal, dlg.GameOverDialog)
        self.assertIn("won", gui.modal.text)
        key(gui, pygame.K_ESCAPE)                      # 'Look at the board'
        frame(gui, 2)
        self.assertIsNone(gui.modal)
        self.assertIsNone(gui.menu)

    def test_a_dead_engine_is_reported_once(self):
        gui = make_gui("main1_start")
        gui.session.fatal = "Forge crashed: OutOfMemoryError"
        frame(gui)
        self.assertIsInstance(gui.modal, dlg.MessageDialog)
        self.assertIn("OutOfMemory", gui.modal.text)
        self.assertTrue(gui.modal.closes_game)
        key(gui, pygame.K_RETURN)
        frame(gui)
        self.assertFalse(gui.running)

    def test_dialogs_render_at_small_and_large_text(self):
        for size, scale in (((900, 600), 1.0), ((1360, 840), 2.0)):
            gui = make_gui("main1_start", size, scale)
            self.request(gui, {"id": 20, "kind": "choose", "title": "A very long question " * 8, "min": 1, "max": 3,
                               "items": self.cards(gui, 7) + [{"kind": "text", "label": "Something else"}],
                               "source": my_hand(gui)[0]})
            frame(gui)
            gui.modal.done = True
            frame(gui)
            for req in ({"id": 21, "kind": "confirm", "title": "Long " * 40, "default": True, "options": []},
                        {"id": 22, "kind": "input", "title": "X", "initial": "1", "numeric": True, "options": ["1", "2", "3", "4"]}):
                self.request(gui, req)
                frame(gui)
                gui.modal.done = True
                frame(gui)


class ZonesAndTurnBarTests(unittest.TestCase):
    """The library / graveyard / exile piles, the command-zone frame and the turn bar (all reported as hard to read)."""

    def test_every_player_has_three_pile_tiles_with_counts(self):
        gui = make_gui("main1_lands")
        for player in gui.state["players"]:
            zones = {d["zone"] for _r, k, d in gui.hits if k == "zone" and d["player"] == player["id"]}
            libs = [d for _r, k, d in gui.hits if k == "library" and d["player"] == player["id"]]
            self.assertTrue(libs, f"{player['name']} has no library tile")
            self.assertLessEqual(zones, {"graveyard", "exile"})

    def test_the_library_tile_says_what_it_is(self):
        gui = make_gui("main1_lands")
        click(gui, point_for(gui, "library", player=gui.state["me"]))
        self.assertIsNotNone(gui.toast)
        self.assertIn("library", gui.toast[0].lower())
        self.assertIsNone(gui.modal)                                   # there is nothing to browse: it only explains

    def test_a_graveyard_pile_with_cards_opens_its_list(self):
        gui = make_gui("main1_lands")
        opp = gui.session.opponents()[0]
        self.assertTrue(opp["zones"]["graveyard"])
        click(gui, point_for(gui, "zone", player=opp["id"], zone="graveyard"))
        self.assertIsInstance(gui.modal, dlg.ChooseDialog)
        self.assertEqual(gui.modal.mode, "view")

    def test_clicking_a_pile_while_choosing_an_opponent_still_targets_them(self):
        gui = make_gui("declare_attackers")
        opp = gui.session.opponents()[0]
        click(gui, point_for(gui, "library", player=opp["id"]))
        self.assertEqual(gui.session.commands("player"), [{"c": "player", "id": opp["id"]}])

    def test_short_panels_fall_back_to_count_chips(self):
        gui = make_gui("main1_lands", size=(900, 600), scale=2.0)
        self.assertTrue([1 for _r, k, _d in gui.hits if k == "library"])   # still reachable, drawn as a chip

    def test_command_zone_frame_holds_a_clickable_commander(self):
        gui = make_gui("main1_lands")
        me = gui.session.me()
        self.assertTrue(me["zones"]["command"])
        cmd = me["zones"]["command"][0]
        self.assertTrue(gui.L.cmd_rect.contains(gui.card_rects[cmd["id"]]))
        click(gui, point_for_card(gui, cmd["id"]))
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": cmd["id"]}])

    def test_command_zone_draws_with_a_tax_showing(self):
        st = load_state("main1_lands")
        me = next(p for p in st["players"] if p["id"] == st["me"])
        me["commanderCasts"] = {str(me["commanders"][0]): 2}
        gui = make_gui(st)                                             # only checking that it draws with a tax and does not crash
        gui.render()
        self.assertIn(me["commanders"][0], [c["id"] for c in gui.session.me()["zones"]["command"]])

    def test_command_zone_says_where_the_commander_went(self):
        gui = make_gui("main1_lands")
        opp = gui.session.opponents()[0]
        self.assertEqual(opp["zones"]["command"], [])                  # in this snapshot the AI's commander is already in play
        self.assertEqual(gui.where_is(opp, opp["commanders"][0]), "battlefield")
        self.assertIsNone(gui.where_is(opp, 123456))

    def test_turn_bar_has_ten_pills_with_two_stop_dots_each(self):
        gui = make_gui("main1_lands")
        pills = [d["phase"] for _r, k, d in gui.hits if k == "pill"]
        self.assertEqual(pills, [ph for ph, _l in ft.PILLS])
        stops = [(d["phase"], d["who"]) for _r, k, d in gui.hits if k == "stop"]
        self.assertEqual(len(stops), 20)

    def test_turn_bar_lit_pill_follows_the_phase(self):
        for name, phase in (("main1_lands", "MAIN1"), ("declare_attackers", "COMBAT_DECLARE_ATTACKERS")):
            gui = make_gui(name)
            self.assertEqual(gui.state["phase"], phase)
            self.assertIn(phase, [ph for ph, _l in ft.PILLS])

    def test_turn_bar_tooltips_explain_the_dots(self):
        gui = make_gui("main1_lands")
        move(gui, point_for(gui, "stop", phase="UPKEEP", who="theirs"))
        text = gui.tooltip_text()
        self.assertIn("opponent", text)
        self.assertIn("upkeep", text.lower())
        move(gui, point_for(gui, "pill", phase="MAIN2"))
        self.assertIn("Main 2", gui.tooltip_text())

    def test_turn_bar_fits_at_every_text_size(self):
        for scale in (1.0, 1.5, 2.0):
            for size in ((900, 600), (1360, 840), (2560, 1300)):
                gui = make_gui("main1_lands", size=size, scale=scale)
                for rect, k, _d in gui.hits:
                    if k == "pill":
                        self.assertLessEqual(rect.right, gui.L.W, f"pill off screen at {size} x{scale}")

    def test_round_number_converts_forges_raw_per_player_turn_count(self):
        """Karl's PC (round 15): the top bar said 'Turn 12' in a 2-player game where each player had only had 6
        turns - Forge's own 'turn' field counts every player's turn separately (Arena/MTGO do the same), which is
        not what a person means by 'turn 12'."""
        self.assertEqual(ft.round_number(12, 2), 6)      # Karl's exact example
        self.assertEqual(ft.round_number(11, 2), 6)      # the odd turn of the same round
        self.assertEqual(ft.round_number(1, 2), 1)
        self.assertEqual(ft.round_number(9, 3), 3)       # a 3-player pod
        self.assertEqual(ft.round_number(5, 1), 5)       # solitaire: no conversion needed
        self.assertEqual(ft.round_number(0, 2), 0)       # no game yet: unchanged, not a ZeroDivisionError
        self.assertEqual(ft.round_number(5, 0), 5)       # no player list yet: unchanged, not a ZeroDivisionError

    def test_top_bar_shows_the_round_not_forges_raw_turn_count(self):
        st = load_state("main1_lands")
        st["turn"] = 12
        st["players"] = st["players"][:2]                              # a 2-player game, like Karl's
        gui = make_gui(st)
        self.assertEqual(ft.round_number(gui.state["turn"], len(gui.state["players"])), 6)
        gui.render()                                                    # only checking draw_top_bar does not crash

    def test_priority_with_something_on_the_stack_says_ok_resolves_it(self):
        gui = make_gui("stack_one")
        self.assertIn("Stack:", gui.prompt()["message"])
        # the hint is drawn into the bar; make sure rendering it does not fail for the empty-stack case either
        frame(gui)
        gui2 = make_gui("main1_lands")
        frame(gui2)


class SearchDialogTests(unittest.TestCase):
    """The 'search your library' list: repeated names are stacked, wording says what to click, double-click confirms."""

    def library_request(self, gui, forests=30, mountains=25, optional=True):
        base = my_hand(gui)[0]
        items = []
        for n in range(forests + mountains):
            c = copy.deepcopy(base)
            c["id"] = 9000 + n
            c["name"] = "Forest" if n < forests else "Mountain"
            items.append({"kind": "card", "card": c})
        req = {"id": 77, "kind": "choose_optional" if optional else "choose", "title": "Select a card from your library",
               "min": 0 if optional else 1, "max": 1, "items": items}
        gui.session.requests.append(req)
        frame(gui)
        return req

    def test_identical_cards_are_shown_once_with_a_count(self):
        gui = make_gui("main1_start")
        self.library_request(gui)
        d = gui.modal
        self.assertEqual(len(d.tiles), 2)                              # Forest and Mountain, not 55 tiles
        self.assertEqual(sorted(len(v) for v in d.stacks.values()), [25, 30])

    def test_choosing_a_stack_answers_with_one_real_index(self):
        gui = make_gui("main1_start")
        req = self.library_request(gui)
        d = gui.modal
        names = [d.items[i]["card"]["name"] for _r, i in d.tiles]
        forest_tile = d.tiles[names.index("Forest")][0]
        click(gui, forest_tile.center)
        self.assertEqual(len(d.sel), 1)
        self.assertEqual(d.items[d.sel[0]]["card"]["name"], "Forest")
        done = next(r for r, n in d.buttons if n == "done")
        click(gui, done.center)
        value = gui.session.commands("reply")[-1]["value"]
        self.assertEqual(len(value), 1)
        self.assertEqual(req["items"][value[0]]["card"]["name"], "Forest")

    def test_double_click_takes_the_card(self):
        gui = make_gui("main1_start")
        self.library_request(gui, optional=False)
        d = gui.modal
        pos = d.tiles[0][0].center
        click(gui, pos)
        self.assertFalse(d.done)
        click(gui, pos)
        self.assertTrue(gui.session.commands("reply"))
        self.assertEqual(len(gui.session.commands("reply")[-1]["value"]), 1)

    def test_library_wording_and_buttons(self):
        gui = make_gui("main1_start")
        self.library_request(gui)
        labels = {n: gui.modal.done_label() if n == "done" else gui.modal.skip_label() for _r, n in gui.modal.buttons}
        self.assertEqual(labels, {"skip": "Find nothing"})          # round 27b: Take card stays greyed out until a card is picked
        self.assertEqual(gui.modal.done_label(), "Take card")
        gui.modal.toggle(0)
        frame(gui)
        labels = {n: gui.modal.done_label() if n == "done" else gui.modal.skip_label() for _r, n in gui.modal.buttons}
        self.assertEqual(labels, {"skip": "Find nothing", "done": "Take card"})
        gui.modal.toggle(0)
        frame(gui)
        skip = next(r for r, n in gui.modal.buttons if n == "skip")
        click(gui, skip.center)
        self.assertEqual(gui.session.commands("reply")[-1]["value"], [])

    def test_other_choices_keep_the_plain_wording(self):
        gui = make_gui("main1_start")
        gui.session.requests.append({"id": 5, "kind": "choose", "title": "Choose a creature", "min": 1, "max": 1,
                                     "items": [{"kind": "card", "card": copy.deepcopy(c)} for c in my_hand(gui)[:3]]})
        frame(gui)
        self.assertEqual(gui.modal.done_label(), "Done")
        self.assertIsNone(gui.modal.zone)

    def test_multi_pick_lists_are_not_stacked(self):
        gui = make_gui("main1_start")
        base = my_hand(gui)[0]
        items = []
        for n in range(6):
            c = copy.deepcopy(base)
            c["id"] = 8000 + n
            c["name"] = "Forest"
            items.append({"kind": "card", "card": c})
        gui.session.requests.append({"id": 6, "kind": "choose", "title": "Choose two", "min": 2, "max": 2, "items": items})
        frame(gui)
        self.assertEqual(len(gui.modal.tiles), 6)

    def test_the_dialog_draws_at_small_and_large_sizes(self):
        for size in ((900, 600), (1360, 840), (2560, 1300)):
            gui = make_gui("main1_start", size=size, scale=1.5)
            self.library_request(gui)
            frame(gui, 2)
            self.assertIsInstance(gui.modal, dlg.ChooseDialog)


class ArtTests(unittest.TestCase):
    def test_tokens_and_missing_art_get_drawn_cards(self):
        st = copy.deepcopy(load_state("stack_two_late"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        token = copy.deepcopy(me["zones"]["battlefield"][0])
        token.update(id=888, name="Treasure", token=True, type="Token Artifact - Treasure", isLand=False, isCreature=False,
                     text="{T}, Sacrifice this artifact: Add one mana of any color.", cost="")
        me["zones"]["battlefield"].append(token)
        gui = make_gui(st)
        self.assertIn(888, gui.card_rects)

    def test_attacking_and_blocking_creatures_get_the_pulsing_glow(self):
        """Karl asked (twice) for attacking creatures to have 'an animation that makes it clearer'."""
        st = load_state("combat_damage")
        attackers = [c for p in st["players"] for c in p["zones"]["battlefield"] if c.get("attacking")]
        self.assertTrue(attackers)
        gui = make_gui(copy.deepcopy(st))
        gui.modal = None                   # round 20: the table behind a dialog is a frozen picture, so look at it with the dialog closed
        gui.session.requests.clear()
        with mock.patch("forge_table.ffx.draw_attack_glow") as spy:
            frame(gui)
        red_calls = [call for call in spy.call_args_list if call.args[4] == ft.RED]  # (screen, rect, now, animate, colour)
        self.assertTrue(red_calls, "no attacking creature was drawn with the (animated) attack glow")
        red_rects = [call.args[1] for call in red_calls]
        for a in attackers:
            self.assertIn(gui.card_rects[a["id"]], red_rects)
        self.assertTrue(all(call.args[3] == gui.animations for call in red_calls))

    def test_face_down_and_hidden_cards_show_a_card_back(self):
        st = copy.deepcopy(load_state("stack_two_late"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        me["zones"]["battlefield"][0].update(faceDown=True)
        opp = [p for p in st["players"] if p["id"] != st["me"]][0]
        opp["zones"]["battlefield"][0] = {"id": 4242, "hidden": True, "name": "Face-down card", "zone": "Battlefield",
                                          "tapped": False}
        gui = make_gui(st)
        self.assertIn(4242, gui.card_rects)


class LogTests(unittest.TestCase):
    def test_log_hides_phase_and_mana_noise_and_wraps(self):
        gui = make_gui("main1_start", log=False)
        gui.session.handle({"t": "log", "entries": [{"type": "PHASE", "text": "Karl's Untap step"},
                                                    {"type": "MANA", "text": "Forest - {T}: Add {G}."},
                                                    {"type": "STACK_ADD", "text": "Karl cast Llanowar Elves " * 12}]})
        frame(gui)
        texts = [ln.text() for ln in gui._log_lines]
        self.assertTrue(texts)
        self.assertFalse(any("Untap" in t or "Add {G}" in t for t in texts))
        self.assertGreater(len(texts), 1)

    def test_log_scrolls_with_the_wheel_only_over_the_log(self):
        gui = make_gui("stack_two_late")
        move(gui, gui.log_rect.center)
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=2))
        frame(gui)
        self.assertEqual(gui.log_scroll, 6)
        move(gui, (10, 300))
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=2))
        self.assertEqual(gui.log_scroll, 6)


CAVERNS_TEXT = ("If Gemstone Caverns is in your opening hand and you're not the starting player, you may begin the game with "
                "Gemstone Caverns on the battlefield with a luck counter on it. If you do, exile a card from your hand.\n{T}: Add {C}.")


def with_caverns_in_hand(st, message=None):
    st = copy.deepcopy(st)
    me = next(p for p in st["players"] if p["id"] == st["me"])
    c = copy.deepcopy(me["zones"]["hand"][0])
    c.update(id=9001, name="Gemstone Caverns", oracleName="Gemstone Caverns", type="Legendary Land", isLand=True, text=CAVERNS_TEXT)
    me["zones"]["hand"][0] = c
    if message:
        st["prompt"]["message"] = message
    return st


class PromptViewTests(unittest.TestCase):
    def view(self, msg, mulligans=0):
        return ft.prompt_view(msg, "Karl", mulligans)

    def test_priority_at_my_turn_and_theirs(self):
        head, hint, ok = self.view("Priority: Karl\nTurn: 3 (Karl)\nPhase: Main phase, precombat\nStack: Empty")
        self.assertEqual(head, "Your priority  -  Main 1, your turn")
        self.assertEqual(ok, "Pass priority")
        self.assertIn("glowing", hint)
        head, hint, ok = self.view("Priority: Karl\nTurn: 10 (AI 1 (Kinnan))\nPhase: Main phase, postcombat\nStack: 2 to Resolve.")
        self.assertIn("2 items on the stack", head)
        self.assertIn("AI 1 (Kinnan)'s turn", head)
        self.assertEqual(ok, "Pass priority")

    def test_combat_prompts_lose_card_numbers_and_say_what_to_click(self):
        head, hint, _ = self.view("Select creatures to block Kinnan, Bonder Prodigy (155) or select another attacker to declare blockers for.")
        self.assertEqual(head, "Declare blockers  -  choose blockers for Kinnan, Bonder Prodigy")
        self.assertIn("Click your creatures", hint)
        head, hint, _ = self.view("Select creatures to attack AI 1 (Kinnan) or select player/card you wish to attack.")
        self.assertEqual(head, "Declare attackers  -  attacking AI 1 (Kinnan)")
        self.assertIn("Full Send", hint)

    def test_paying_mana_names_the_cost_and_the_source(self):
        head, hint, _ = self.view("Llanowar Elves - Creature 1 / 1\n\nPay Mana Cost: {G}")
        self.assertEqual(head, "Pay {G}  -  Llanowar Elves")
        self.assertIn("Auto", hint)
        self.assertEqual(self.view("Pay Mana Cost: {1}{G}")[0], "Pay {1}{G}")

    def test_mulligan_and_bottoming_prompts(self):
        head, hint, _ = self.view("Karl, you are going first!\n\nDo you want to keep your hand?")
        self.assertEqual(head, "You go first  -  keep this hand?")
        self.assertIn("first is free", hint)
        head, hint, _ = self.view("AI 1 (Kinnan) is going first.\nKarl, you are going 2nd.\n\nDo you want to keep your hand?", 1)
        self.assertEqual(head, "You go 2nd  -  keep this hand?")
        self.assertIn("puts 1 on the bottom", hint)
        head, hint, _ = self.view("Return 2 card(s) to the bottom of your library")
        self.assertEqual(head, "Put 2 cards on the bottom of your library")
        self.assertIn("Click 2 cards", hint)
        self.assertEqual(self.view("Return 1 card(s) to the bottom of your library")[0], "Put 1 card on the bottom of your library")

    def test_the_caverns_exile_step_and_unknown_prompts(self):
        head, hint, _ = self.view("Gemstone Caverns (18)\n - If you do, exile a card from your hand.\nSelect a card from your hand")
        self.assertEqual(head, "Choose a card from your hand")
        self.assertIn("exile a card", hint)
        self.assertEqual(self.view("Something new and odd (5)")[0], "Something new and odd")
        self.assertEqual(self.view("")[0], "")


class PregameTests(unittest.TestCase):
    def test_coin_toss_opens_a_play_or_draw_dialog_and_answers_once(self):
        gui = make_gui("coin_toss", log=False)
        self.assertIsInstance(gui.modal, dlg.PlayDrawDialog)
        key(gui, pygame.K_RETURN)                                   # Enter never picks for you
        self.assertFalse(gui.session.commands("ok") or gui.session.commands("cancel"))
        key(gui, pygame.K_d)
        self.assertEqual(len(gui.session.commands("cancel")), 1)    # Draw = Forge's Cancel
        frame(gui, 5)
        self.assertIsNone(gui.modal)                                # the old prompt lingers a moment; the dialog must not come back
        self.assertEqual(len(gui.session.commands("cancel")), 1)

    def test_play_first_is_the_ok_button_and_the_click_works(self):
        gui = make_gui("coin_toss", log=False)
        play = next(r for r, n in gui.modal.buttons if n == "play")
        click(gui, play.center)
        self.assertEqual(len(gui.session.commands("ok")), 1)

    def test_coin_toss_warns_when_the_deck_has_gemstone_caverns(self):
        gui = make_gui("coin_toss", log=False)
        self.assertFalse(gui.modal.caverns)
        gui = ft.ForgeTable(FakeSession(load_state("coin_toss")), StubStore(), window_size=(1360, 840), deck_names={"Gemstone Caverns", "Forest"})
        frame(gui, 3)
        self.assertTrue(gui.modal.caverns)

    def test_mulligan_advice_free_first_then_the_cost(self):
        gui = make_gui(with_caverns_in_hand(load_state("mulligan")), log=False)
        lines = [t for t, _c in gui.advisory()]
        self.assertIn("first mulligan is free", lines[0])
        self.assertTrue(any("Gemstone Caverns" in t and "You go first" in t for t in lines), lines)
        gui.mulligans = 2
        self.assertIn("mulliganed 2 times", gui.advisory()[0][0])
        self.assertIn("puts 2 cards", gui.advisory()[0][0])

    def test_caverns_is_usable_when_going_second(self):
        st = with_caverns_in_hand(load_state("mulligan"), "AI 1 (Kinnan) is going first.\nKarl, you are going 2nd.\n\nDo you want to keep your hand?")
        gui = make_gui(st, log=False)
        lines = [t for t, _c in gui.advisory()]
        self.assertTrue(any("you go second" in t and "Gemstone Caverns" in t for t in lines), lines)
        self.assertFalse(any("stays in your hand" in t for t in lines))

    def test_no_advice_outside_the_mulligan_prompt(self):
        self.assertEqual(make_gui("main1_start", log=False).advisory(), [])

    def test_the_free_mulligan_button_says_so(self):
        gui = make_gui("mulligan", log=False)
        gui.render()
        self.assertEqual(gui.mulligans, 0)
        gui.mulligans = 1
        gui.render()                                                # no crash with the plain label either

    def test_return_zero_cards_is_answered_for_you_exactly_once(self):
        st = load_state("mulligan")
        st["prompt"].update(message="Return 0 card(s) to the bottom of your library")
        st["prompt"]["ok"] = {"label": "OK", "enabled": True, "focus": True}
        st["prompt"]["cancel"] = {"label": "Auto", "enabled": False}
        gui = make_gui(st, log=False)
        frame(gui, 6)
        self.assertEqual(len(gui.session.commands("ok")), 1)
        self.assertEqual(gui.mulligans, 1)

    def test_return_n_cards_is_left_to_the_player_and_counts_the_mulligans(self):
        st = load_state("mulligan")
        st["prompt"].update(message="Return 2 card(s) to the bottom of your library")
        st["prompt"]["ok"] = {"label": "OK", "enabled": False, "focus": True}
        gui = make_gui(st, log=False)
        frame(gui, 3)
        self.assertEqual(gui.session.commands("ok"), [])
        self.assertEqual(gui.mulligans, 3)


class ActionReadabilityTests(unittest.TestCase):
    def test_stack_rows_say_who_what_and_against_whom(self):
        gui = make_gui("stack_two_late")
        stack = gui.state["stack"]
        small, bold = gui.font("small"), gui.font("small", True)
        top = gui.stack_lines(stack[0], 0, 2, small, bold, 400, 5)
        self.assertEqual(top[0][0], "AI 1  -  trigger  -  next")
        self.assertEqual(top[1][0], "City of Brass")
        self.assertTrue(any("deals 1 damage" in t for t, _f, _c in top[2:]))
        spell = gui.stack_lines(stack[1], 1, 2, small, bold, 400, 5)
        texts = [t for t, _f, _c in spell]
        self.assertEqual(texts[:2], ["AI 1  -  spell", "Basalt Monolith"])
        self.assertTrue(any("{T}: Add {C}{C}{C}" in t for t in texts[2:]), texts)          # a spell shows what its card says
        tight = gui.stack_lines(stack[0], 0, 2, small, bold, 400, 2)
        self.assertEqual(len(tight), 2)

    def test_stack_targets_are_named(self):
        gui = make_gui("stack_two_late")
        item = dict(gui.state["stack"][1], targets=["c%d" % gui.session.me()["zones"]["battlefield"][0]["id"], "p%d" % gui.session.me()["id"]])
        lines = gui.stack_lines(item, 1, 2, gui.font("small"), gui.font("small", True), 500, 5)
        self.assertTrue(any(t.startswith("Targets: ") and "You" in t for t, _f, _c in lines), lines)

    def test_the_log_reads_in_plain_words_and_has_turn_headers(self):
        gui = make_gui("stack_two_late")
        text = [ln.text() for ln in gui._log_lines]
        self.assertTrue(any(t == "You cast Llanowar Elves" for t in text), text[:20])
        self.assertTrue(any(ln.kind == "turn" for ln in gui._log_lines))
        self.assertTrue(any(ln.kind == "phase" and ln.label == "Main 1" for ln in gui._log_lines))
        self.assertFalse(any("(44)" in t or "Zone Changer" in t for t in text))

    def test_old_log_is_history_and_new_opponent_actions_flash_on_the_board(self):
        gui = make_gui("stack_two_late")
        self.assertEqual(gui.feed, [])                              # what was already in the log is not news
        self.assertIsNone(gui.spot_name())
        gui.session.handle({"t": "log", "entries": [{"type": "STACK_ADD", "text": "AI 1 (Kinnan) cast Sol Ring"},
                                                    {"type": "STACK_ADD", "text": "Karl cast Forest"}]})
        frame(gui)
        self.assertEqual(len(gui.feed), 1)                          # my own actions are not echoed back at me
        self.assertEqual(gui.feed[0][0].card, "Sol Ring")
        self.assertEqual(gui.spot_name(), "Sol Ring")
        gui.feed = [(r, t - 60) for r, t in gui.feed]
        gui.spot = ("Sol Ring", 0)
        frame(gui)
        self.assertEqual(gui.feed, [])
        self.assertIsNone(gui.spot_name())

    def test_feed_keeps_only_the_newest_few(self):
        gui = make_gui("stack_two_late")
        gui.session.handle({"t": "log", "entries": [{"type": "LAND", "text": f"AI 1 (Kinnan) played Forest ({i})"} for i in range(9)]})
        frame(gui)
        self.assertEqual(len(gui.feed), ft.FEED_MAX)

    def test_hovering_a_card_name_in_the_log_shows_that_card(self):
        # The log only registers a "logcard" hit for a line that is actually drawn on screen, and which
        # lines fit in the fixed test window depends on the system font's own metrics (see the round-8
        # lesson above: Karl's real Segoe UI is taller than this sandbox's fallback font, so the same log
        # wraps to more lines and an early "cast Llanowar Elves" can scroll out of the default (newest-first)
        # view on his machine, or on GitHub's Windows CI runner, even though it is visible here). Scroll back
        # through the log - exactly what a player would do to find it - instead of assuming it starts visible.
        gui = make_gui("stack_two_late")
        rect = None
        for scroll in range(0, len(gui._log_lines) + 1):
            gui.log_scroll = scroll
            frame(gui)
            hit = next((r for r, k, d in gui.hits if k == "logcard" and d["name"] == "Llanowar Elves"), None)
            if hit:
                rect = hit
                break
        self.assertIsNotNone(rect, "no visible 'Llanowar Elves' log-card hit at any scroll position")
        move(gui, rect.center)
        card = gui.preview_card()
        self.assertIsNotNone(card)
        self.assertEqual(card["name"], "Llanowar Elves")

    def test_a_resize_re_wraps_the_whole_log_instead_of_losing_it(self):
        gui = make_gui("stack_two_late")
        before = [ln.text() for ln in gui._log_lines if ln.kind == "line"]
        gui.change_text_scale(1)
        frame(gui, 2)
        after = [ln.text() for ln in gui._log_lines if ln.kind == "line"]
        self.assertTrue(after)
        self.assertIn("You cast Sol Ring", before)
        self.assertIn("You cast Sol Ring", after)

    def test_priority_bar_says_pass_priority_but_space_still_sends_ok(self):
        gui = make_gui("main1_start")
        key(gui, pygame.K_SPACE)
        self.assertEqual(len(gui.session.commands("ok")), 1)

    def test_bar_and_log_render_at_every_size_for_every_recorded_prompt(self):
        for name in FIXTURE_NAMES:
            for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((4096, 2019), 1.75)):
                gui = make_gui(name, size, scale)
                gui.render()
                self.assertGreater(gui.L.bar.h, 40)


REPLACEMENT_ITEMS = [
    {"kind": "text", "label": "Invasion of Ikoria (28) - (As a Siege enters, choose an opponent to protect it. You and allies of the "
                              "chosen player may cast spells from the top of your library and this text goes on for a good while)"},
    {"kind": "text", "label": "Invasion of Ikoria (28) - Invasion of Ikoria enters with six defense counters on it."}]
SIZES_AND_SCALES = [((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((4096, 2019), 1.75)]


class FeedbackRoundThreeTests(unittest.TestCase):
    """Karl's third report: the coin-toss window did not fit, the stack did not preview on hover, choice text was cut off,
    floating mana could not be seen."""

    def inside(self, inner, outer):
        return outer.contains(inner)

    def test_coin_toss_window_fits_at_every_size_and_scale(self):
        for size, scale in SIZES_AND_SCALES:
            for deck in ((), ("Gemstone Caverns",)):
                gui = make_gui("coin_toss", size, scale, log=False)
                gui.deck_names = set(deck)
                gui.sync()
                self.assertIsInstance(gui.modal, dlg.PlayDrawDialog)
                gui.modal.caverns = bool(deck)
                frame(gui, 2)
                m = gui.modal
                window = pygame.Rect(0, 0, *size)
                self.assertTrue(self.inside(m.rect, window), (size, scale, m.rect))
                names = {n: r for r, n in m.buttons}
                self.assertEqual(set(names), {"play", "draw"})
                for n, r in names.items():
                    self.assertTrue(self.inside(r, m.rect), (size, scale, n, r, m.rect))
                self.assertFalse(names["play"].colliderect(names["draw"]))

    def test_coin_toss_text_ends_above_the_buttons(self):
        # measure what the dialog draws: everything above the buttons row must be text-free space at the buttons' top edge
        for size, scale in SIZES_AND_SCALES:
            gui = make_gui("coin_toss", size, scale, log=False)
            gui.modal.caverns = True
            frame(gui, 2)
            m = gui.modal
            top = min(r.y for r, _n in m.buttons)
            f = gui.font("small")
            self.assertGreaterEqual(top - m.rect.y, 4 * f.get_height(), "no room left for the text above the buttons")
            # the panel is sized from its text: render it with the caverns note and without and the note adds height
            with_note = m.rect.h
            m.caverns = False
            frame(gui, 1)
            self.assertLess(m.rect.h, with_note)

    def test_hovering_a_stack_item_shows_that_card_in_the_preview_panel(self):
        gui = make_gui("stack_two_late", (1360, 840))
        rects = [(r, d["card"]["name"]) for r, k, d in gui.hits if k == "stack"]
        self.assertEqual(len(rects), 2)
        # first hover something else so the preview holds another card, then move onto each stack row
        first = next(c for c in gui.session.me()["zones"]["hand"])
        move(gui, point_for_card(gui, first["id"]))
        self.assertEqual(gui.last_preview["id"], first["id"])
        for r, name in rects:
            move(gui, r.center)
            self.assertEqual(gui.last_preview["name"], name, "the preview panel did not follow the mouse onto the stack")

    def test_hovering_a_log_card_name_shows_that_card(self):
        gui = make_gui("stack_two_late", (1360, 840))
        hits = [(r, d["name"]) for r, k, d in gui.hits if k == "logcard"]
        self.assertTrue(hits, "no card names in the log to hover")
        r, name = hits[-1]
        move(gui, r.center)
        self.assertEqual(gui.last_preview["name"], name)

    def test_choice_text_is_never_cut_off(self):
        for size, scale in SIZES_AND_SCALES:
            gui = make_gui("main1_start", size, scale, log=False)
            gui.session.handle({"t": "request", "id": 5, "kind": "order", "title": "Select order for replacement effects",
                                "min": 1, "max": 2, "items": REPLACEMENT_ITEMS})
            frame(gui, 3)
            d = gui.modal
            self.assertIsInstance(d, dlg.ChooseDialog)
            rows = d.text_rows(gui, d.body.w)
            self.assertEqual(len(rows), 2)
            for (i, lines, _h), item in zip(rows, REPLACEMENT_ITEMS):
                shown = " ".join(lines)
                self.assertNotIn("...", shown)
                self.assertEqual(shown, item["label"].replace(" (28)", ""), "the row lost some of its text")
            # every row is fully inside the list, and the list is inside the window
            for r, _i in d.tiles:
                self.assertTrue(d.body.contains(r), (size, scale, r, d.body))
            self.assertTrue(self.inside(d.rect, pygame.Rect(0, 0, *size)))
            for r, _n in d.buttons:
                self.assertTrue(self.inside(r, d.rect))

    def test_selected_line_is_short_and_ids_are_hidden(self):
        gui = make_gui("main1_start", (1360, 840), log=False)
        gui.session.handle({"t": "request", "id": 5, "kind": "order", "title": "Select order for replacement effects",
                            "min": 1, "max": 2, "items": REPLACEMENT_ITEMS})
        frame(gui, 3)
        d = gui.modal
        d.sel = [0, 1]
        names = d.selected_names()
        self.assertEqual(names[0], "Invasion of Ikoria")                 # a long choice is shortened to the part before ' - '
        self.assertEqual(len(names), 2)
        self.assertFalse(any("(28)" in n for n in names))

    def test_two_choices_that_only_differ_by_their_id_keep_the_id(self):
        gui = make_gui("main1_start", (1360, 840), log=False)
        items = [{"kind": "text", "label": "Forest (44)"}, {"kind": "text", "label": "Forest (45)"}]
        d = dlg.ChooseDialog("Pick one", items, minimum=1, maximum=1)
        self.assertEqual(d.row_labels(items), ["Forest (44)", "Forest (45)"])
        self.assertEqual(d.row_labels(items[:1]), ["Forest"])

    def pool_state(self, mine=None, theirs=None):
        st = copy.deepcopy(load_state("main1_lands"))
        me = next(p for p in st["players"] if p["id"] == st["me"])
        opp = next(p for p in st["players"] if p["id"] != st["me"])
        me["manaPool"] = mine or {}
        opp["manaPool"] = theirs or {}
        return st

    def test_floating_mana_is_shown_big_above_the_bar(self):
        for size, scale in SIZES_AND_SCALES:
            gui = make_gui(self.pool_state({"G": 2, "U": 1, "C": 3}), size, scale)
            r = gui.pool_rect
            self.assertIsNotNone(r, "no mana pool pill")
            self.assertTrue(self.inside(r, pygame.Rect(0, 0, *size)), (size, scale, r))
            self.assertLessEqual(r.bottom, gui.L.bar.y, "the pill should sit above the prompt bar")
            self.assertGreaterEqual(r.h, 26)
            self.assertEqual(gui.pool_items(gui.session.me()), [("U", 1), ("G", 2), ("C", 3)])

    def test_no_pill_when_the_pool_is_empty_and_the_panel_chip_shows_mana(self):
        gui = make_gui(self.pool_state(), (1360, 840))
        self.assertIsNone(gui.pool_rect)
        base = pygame.image.tostring(gui.screen, "RGB")
        gui2 = make_gui(self.pool_state({"R": 2}, {"B": 1}), (1360, 840))
        self.assertIsNotNone(gui2.pool_rect)
        self.assertNotEqual(base, pygame.image.tostring(gui2.screen, "RGB"))
        self.assertEqual(gui2.pool_items(gui2.session.opponents()[0]), [("B", 1)])

    def test_pool_pill_is_hidden_behind_dialogs(self):
        gui = make_gui(self.pool_state({"G": 1}), (1360, 840))
        self.assertIsNotNone(gui.pool_rect)
        gui.modal = dlg.HelpDialog()
        frame(gui, 2)
        self.assertIsNone(gui.pool_rect)

    def test_other_dialogs_fit_at_big_text(self):
        for size, scale in SIZES_AND_SCALES:
            gui = make_gui("main1_start", size, scale, log=False)
            window = pygame.Rect(0, 0, *size)
            for d in (dlg.MessageDialog("", "A short message about the game.\n\nAnd a second paragraph that is a bit longer to wrap.",
                                        "Close", heading="Game over"),
                      dlg.ConfirmDialog({"kind": "confirm", "id": 1, "title": "Do you want to pay 1 life? " * 3, "options": ["Yes", "No"]}),
                      dlg.InputDialog({"kind": "input", "id": 2, "title": "Choose a value for X", "numeric": True, "initial": "2"})):
                gui.modal = d
                frame(gui, 2)
                self.assertTrue(self.inside(d.rect, window), (type(d).__name__, size, scale, d.rect))
                for r, _n in d.buttons:
                    self.assertTrue(self.inside(r, d.rect), (type(d).__name__, size, scale, r, d.rect))


class ArtFor:
    """A card store whose pictures are tiny generated files: art for exactly the names given."""

    last_error = None

    def __init__(self, folder, names):
        self.paths = {}
        for n in names:
            surf = pygame.Surface((244, 340))
            surf.fill((200, 30, 30))
            path = os.path.join(folder, n.replace(" ", "_") + ".png")
            pygame.image.save(surf, path)
            self.paths[n] = path

    def prefetch_cards(self, names):
        return 0

    def peek_card(self, name):
        return {"name": name} if name in self.paths else None

    def peek_image_path(self, name):
        return self.paths.get(name)

    def get_image_path(self, name):
        return self.paths.get(name)


class CopyTokenArtTests(unittest.TestCase):
    """Karl: 'somehow we lost the card image for Consecrated Sphinx' - the second Sphinx on the AI's side was drawn as a plain
    stand-in card. A token that is a copy of a real card has that card's name, so it must get that card's picture."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def is_picture(colour):
        """The test pictures are plain red; a drawn stand-in never is."""
        return colour[0] > 170 and colour[1] < 70 and colour[2] < 70

    def table_with(self, token_name, real_art=("Consecrated Sphinx",)):
        st = copy.deepcopy(load_state("main1_start"))
        opp = next(p for p in st["players"] if p["id"] != st["me"])
        me = next(p for p in st["players"] if p["id"] == st["me"])
        base = copy.deepcopy(me["zones"]["hand"][0])
        base.update(id=901, name=token_name, oracleName=token_name, token=True, isLand=False, isCreature=True, type="Creature - Sphinx",
                    cost="{4}{U}{U}", power=4, toughness=6, tapped=False, sick=True)
        opp["zones"]["battlefield"].append(base)
        gui = ft.ForgeTable(FakeSession(st), ArtFor(self.tmp.name, real_art), window_size=(1360, 840))
        for _ in range(100):
            gui.sync()
            gui.render()
            if not real_art or all(n in gui.art.imgs for n in real_art):
                break
            time.sleep(0.02)                                      # the pictures load on a background thread
        return gui, base

    def test_a_token_copy_of_a_real_card_uses_the_real_picture(self):
        gui, token = self.table_with("Consecrated Sphinx")
        self.assertEqual(gui.art_name(token), "Consecrated Sphinx")
        self.assertIn("Consecrated Sphinx", gui.art.imgs)
        surf = gui.card_surface(dict(token, sick=False, power=None), 120, 168, plate=False)
        self.assertTrue(self.is_picture(surf.get_at((60, 84))), "the copy was drawn as a stand-in instead of with the picture")

    def test_a_real_token_still_gets_a_drawn_card(self):
        gui, token = self.table_with("Treasure", real_art=())
        self.assertIsNone(gui.art_name(token))
        surf = gui.card_surface(dict(token, power=None), 120, 168, plate=False)
        self.assertFalse(self.is_picture(surf.get_at((60, 84))))

    def test_the_preview_of_a_token_copy_uses_the_real_picture(self):
        gui, token = self.table_with("Consecrated Sphinx")
        gui.pinned = token["id"]
        frame(gui, 3)
        r = gui.L.preview
        self.assertTrue(self.is_picture(gui.screen.get_at((r.centerx, r.centery))))


if __name__ == "__main__":
    unittest.main()
