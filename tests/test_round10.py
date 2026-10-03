# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 10: the card's own wording under a trigger / ability on the stack (forge_log.printed_wording), and the Skip window
(Forge's yield features: let the stack resolve, skip to my next turn, always pass an ability, auto-pass)."""
import copy
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_fx as ffx
import forge_log as flog
from unittest import mock
from tests.forge_fake import load_state
import pygame

from tests.test_forge_table import click, frame, key, make_gui, point_for

RING_TEXT = ("Indestructible\r\nWhen The One Ring enters, if you cast it, you gain protection from everything until your next turn.\r\n"
             "At the beginning of your upkeep, you lose 1 life for each burden counter on The One Ring.\r\n"
             "{T}: Put a burden counter on The One Ring, then draw a card for each burden counter on The One Ring.")
RING = {"id": 5, "name": "The One Ring", "text": RING_TEXT}
BRASS = {"id": 6, "name": "City of Brass", "text": "Whenever City of Brass becomes tapped, it deals 1 damage to you.\r\n\r\n{T}: Add one mana of any color."}


def ability(text, card, trigger=False):
    return {"ability": True, "trigger": trigger, "text": text, "card": card}


class PrintedWordingTests(unittest.TestCase):
    def test_the_rings_tap_shows_what_the_card_really_says(self):
        got = flog.printed_wording(ability("The One Ring (5) - {T}: Put a burden counter on The One Ring, then draw zero cards.", RING))
        self.assertEqual(got, "{T}: Put a burden counter on The One Ring, then draw a card for each burden counter on The One Ring.")

    def test_nothing_is_added_when_forge_already_says_the_same(self):
        same = ability("Whenever City of Brass becomes tapped, it deals 1 damage to you. [Tapped: City of Brass (81)]", BRASS, trigger=True)
        self.assertIsNone(flog.printed_wording(same))

    def test_a_trigger_gets_a_trigger_paragraph_not_an_activated_one(self):
        got = flog.printed_wording(ability("At the beginning of your upkeep, you lose 0 life.", RING, trigger=True))
        self.assertTrue(got.startswith("At the beginning of your upkeep"))

    def test_an_activated_ability_gets_the_colon_paragraph(self):
        got = flog.printed_wording(ability("", BRASS))
        self.assertEqual(got, "{T}: Add one mana of any color.")

    def test_spells_and_cards_without_text_get_nothing(self):
        self.assertIsNone(flog.printed_wording({"ability": False, "trigger": False, "text": "The One Ring", "card": RING}))
        self.assertIsNone(flog.printed_wording(ability("something", {"id": 1, "name": "Face-down thing"})))
        self.assertIsNone(flog.printed_wording({"ability": True, "text": "x"}))

    def test_two_triggers_and_no_clear_match_shows_nothing_rather_than_the_wrong_one(self):
        card = {"id": 9, "name": "Twin", "text": "When Twin enters, draw a card.\nWhen Twin dies, create a Treasure token."}
        self.assertIsNone(flog.printed_wording(ability("Zzz qqq.", card, trigger=True)))
        pick = flog.printed_wording(ability("Create a Treasure token when it dies.", card, trigger=True))
        self.assertEqual(pick, "When Twin dies, create a Treasure token.")


class OnTheStackTests(unittest.TestCase):
    def ring_stack_gui(self, size=(1360, 840), scale=None):
        st = copy.deepcopy(load_state("stack_two_late"))
        item = st["stack"][0]
        item["text"] = "The One Ring (5) - {T}: Put a burden counter on The One Ring, then draw zero cards."
        item["trigger"] = False
        item["card"] = dict(item["card"], name="The One Ring", text=RING_TEXT)
        gui = make_gui(st, size=size, scale=scale)
        frame(gui, 2)
        return gui

    def test_the_stack_row_shows_both_lines(self):
        gui = self.ring_stack_gui()
        words = " ".join(t for t, _f, _c in gui.L.stack_rows[0][1])
        self.assertIn("draw zero cards", words)                       # Forge's text stays: it says what will happen now
        self.assertIn("Card says:", words)
        self.assertIn("for each burden counter", words)

    def test_it_still_fits_at_big_text_and_small_windows(self):
        for size, scale in (((900, 600), 2.0), ((1360, 840), 1.75)):
            gui = self.ring_stack_gui(size, scale)
            rows = gui.L.stack_rows
            self.assertTrue(rows)
            self.assertLessEqual(len([r for r in rows[0][1]]), 40)


def with_yield(name, **extra):
    st = copy.deepcopy(load_state(name))
    st["yield"] = dict({"autoPass": False, "autoYields": [], "autoTriggers": {}}, **extra)
    return st


def labels(gui):
    return [label for label, _run, _style in gui.modal.options]


def choose(gui, text):
    for i, (label, _run, _style) in enumerate(gui.modal.options):
        if label.startswith(text):
            click(gui, gui.modal.buttons[[n for _r, n in gui.modal.buttons].index(f"opt{i}")][0].center)
            return
    raise AssertionError(f"no option starting {text!r} in {labels(gui)}")


class SkipWindowTests(unittest.TestCase):
    def open_skip(self, st, **kw):
        gui = make_gui(st, **kw)
        frame(gui, 2)
        click(gui, point_for(gui, "button", name="skip"))
        frame(gui, 1)
        self.assertIsNotNone(gui.modal)
        return gui

    def test_no_skip_button_from_an_older_bridge(self):
        gui = make_gui("stack_two_late")
        frame(gui, 2)
        self.assertNotIn("skip", [d.get("name") for _r, k, d in gui.hits if k == "button"])
        key(gui, pygame.K_s, unicode="s")
        self.assertIsNone(gui.modal)

    def test_no_skip_button_while_forge_asks_something_else(self):
        st = with_yield("stack_two_late")
        st["prompt"]["message"] = "Select target player"
        gui = make_gui(st)
        frame(gui, 2)
        self.assertNotIn("skip", [d.get("name") for _r, k, d in gui.hits if k == "button"])

    def test_s_opens_the_window_and_esc_closes_it(self):
        gui = make_gui(with_yield("stack_two_late"))
        key(gui, pygame.K_s, unicode="s")
        self.assertIsNotNone(gui.modal)
        key(gui, pygame.K_ESCAPE)
        frame(gui, 1)
        self.assertIsNone(gui.modal)
        self.assertEqual(gui.session.commands("yield"), [])

    def test_skip_to_my_next_turn_sends_the_yield_command(self):
        gui = self.open_skip(with_yield("stack_two_late"))
        choose(gui, "Skip to my next turn")
        self.assertEqual(gui.session.commands("yield"), [{"c": "yield", "mode": "until", "phase": "UPKEEP"}])

    def test_let_the_stack_resolve_only_when_there_is_something_on_it(self):
        gui = self.open_skip(with_yield("stack_two_late"))
        self.assertTrue(any(l.startswith("Let the stack resolve") for l in labels(gui)))
        choose(gui, "Let the stack resolve")
        self.assertEqual(gui.session.commands("yield"), [{"c": "yield", "mode": "stack"}])
        empty = with_yield("stack_two_late")
        empty["stack"] = []
        gui = self.open_skip(empty)
        self.assertFalse(any(l.startswith("Let the stack resolve") for l in labels(gui)))

    def test_always_pass_uses_the_key_of_the_ability_on_top(self):
        gui = self.open_skip(with_yield("stack_two_late"))
        self.assertIn("Always pass on City of Brass's trigger", labels(gui))
        choose(gui, "Always pass on")
        self.assertEqual(gui.session.commands("autoyield"),
                         [{"c": "autoyield", "key": "City of Brass (81): Whenever City of Brass becomes tapped, it deals 1 damage to you.", "on": True}])

    def test_an_optional_trigger_of_mine_can_be_always_used_or_never_used(self):
        st = with_yield("stack_two_late")
        st["stack"][0].update({"optional": True, "decision": "ask"})
        gui = self.open_skip(st)
        self.assertIn("Always use City of Brass's trigger", labels(gui))
        self.assertIn("Never use City of Brass's trigger", labels(gui))
        choose(gui, "Never use")
        self.assertEqual(gui.session.commands("trigger")[0]["decision"], "decline")
        st["stack"][0]["decision"] = "accept"
        gui = self.open_skip(st)
        self.assertNotIn("Always use City of Brass's trigger", labels(gui))
        choose(gui, "Ask me again")
        self.assertEqual(gui.session.commands("trigger")[0]["decision"], "ask")

    def test_the_rules_already_made_can_be_undone_one_by_one_or_all_at_once(self):
        st = with_yield("stack_one", autoYields=["{T}, Sacrifice it: Draw a card."], autoTriggers={"Whenever a land enters, you may draw.": "accept"})
        gui = self.open_skip(st)
        self.assertIn("Stop always passing: {T}, Sacrifice it: Draw a card.", labels(gui))
        self.assertIn("Forget all my 'always' choices (2)", labels(gui))
        choose(gui, "Stop always passing")
        self.assertEqual(gui.session.commands("autoyield")[0]["on"], False)
        gui = self.open_skip(st)
        choose(gui, "Forget all")
        self.assertEqual(len(gui.session.commands("yieldreset")), 1)

    def test_a_running_skip_can_be_stopped_from_the_window(self):
        st = with_yield("stack_two_late", mode="until", phase="UPKEEP")
        st["prompt"]["message"] = "Yielding until Karl's Upkeep step.\n\nYou may cancel this yield to take an action."
        gui = self.open_skip(st)
        self.assertTrue(labels(gui)[0].startswith("Stop skipping"))
        choose(gui, "Stop skipping")
        self.assertEqual(gui.session.commands("yield"), [{"c": "yield", "mode": "clear"}])

    def test_auto_pass_is_a_switch_that_is_remembered(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "settings.json")
        gui = self.open_skip(with_yield("stack_two_late"), settings=path)
        self.assertIn("Auto-pass when I can't do anything: OFF", labels(gui))
        choose(gui, "Auto-pass")
        self.assertEqual(gui.session.commands("autopass"), [{"c": "autopass", "on": True}])
        self.assertTrue(gui.auto_pass)
        with open(path, encoding="utf-8") as f:
            self.assertIs(json.load(f)["auto_pass"], True)
        again = make_gui(with_yield("stack_two_late"), settings=path)         # a new run: told to Forge once, with the first snapshot
        frame(again, 3)
        self.assertEqual(again.session.commands("autopass"), [{"c": "autopass", "on": True}])
        self.assertIn("Skip (auto)", [t for t in gui_button_labels(again)])

    def test_the_window_fits_at_small_sizes_and_big_text_with_every_option(self):
        st = with_yield("stack_two_late", mode="stack", autoYields=["a" * 90, "b" * 90], autoTriggers={"x": "accept"})
        st["stack"][0].update({"optional": True, "decision": "ask"})
        for size, scale in (((900, 600), 2.0), ((900, 600), 1.0), ((1360, 840), 1.75), ((1920, 1080), 1.0)):
            gui = make_gui(st, size=size, scale=scale)
            frame(gui, 2)
            key(gui, pygame.K_s, unicode="s")
            frame(gui, 1)
            self.assertIsNotNone(gui.modal, (size, scale))
            n = len(gui.modal.options)
            self.assertLessEqual(n, 9)
            screen = gui.screen.get_rect()
            rects = [r for r, _n in gui.modal.buttons]
            self.assertEqual(len(rects), n + 1, (size, scale))
            for r in rects:
                self.assertTrue(screen.contains(r), (size, scale, r))
                self.assertTrue(gui.modal.rect.contains(r), (size, scale, r))


class FxGeometryTests(unittest.TestCase):
    def test_stack_targets_lists_only_items_that_target_something(self):
        stack = [{"trigger": True, "card": {"id": 5}, "targets": ["c12", "p1"]}, {"card": {"id": 6}, "targets": []},
                 {"ability": True, "card": {"id": 7}, "targets": ["c3", "junk", "cX"]}]
        self.assertEqual(ffx.stack_targets(stack), [(1, "trigger", 5, ["c12", "p1"]), (3, "ability", 7, ["c3"])])
        self.assertEqual(len(ffx.stack_targets([{"card": {"id": i}, "targets": ["c1"]} for i in range(20)])), ffx.LINK_MAX)

    def test_edge_point_leaves_the_rectangle_on_the_side_facing_the_target(self):
        r = pygame.Rect(100, 100, 40, 60)                                # centre (120, 130)
        x, y = ffx.edge_point(r, (500, 130))
        self.assertEqual((round(x), round(y)), (140, 130))
        x, y = ffx.edge_point(r, (120, -400))
        self.assertEqual((round(x), round(y)), (120, 100))
        self.assertEqual(ffx.edge_point(r, r.center), r.center)

    def test_ease_out_starts_fast_and_lands_exactly(self):
        self.assertEqual((ffx.ease_out(0), ffx.ease_out(1)), (0.0, 1.0))
        self.assertGreater(ffx.ease_out(0.25), 0.25)
        self.assertEqual(ffx.ease_out(7), 1.0)

    def test_a_flight_needs_an_origin_and_ends_when_the_card_has_landed(self):
        a = ffx.Arrivals()
        a.update({1: False}, 0.0)
        a.update({1: False, 2: False, 3: True}, 10.0, {2: pygame.Rect(0, 0, 10, 10), 3: pygame.Rect(5, 5, 10, 10)})
        a.update({1: False, 2: False, 3: True, 4: False}, 10.1)                         # 4 has no origin: it just appears (a token)
        t, origin = a.flight(2, 10.0 + ffx.FLY_SECONDS / 2)
        self.assertAlmostEqual(t, 0.5)
        self.assertEqual(origin, pygame.Rect(0, 0, 10, 10))
        self.assertIsNone(a.flight(2, 10.0 + ffx.FLY_SECONDS + 0.01))
        self.assertIsNone(a.flight(4, 10.2))
        self.assertIsNotNone(a.flight(3, 10.0 + ffx.FLY_LAND_SECONDS - 0.01))
        self.assertIsNone(a.flight(3, 10.0 + ffx.FLY_LAND_SECONDS + 0.01))                # lands are quicker
        self.assertIsNone(a.flight(1, 10.0))                                              # it was already there

    def test_draw_flight_and_link_draw_something_and_stay_on_the_surface(self):
        screen = pygame.Surface((300, 200))
        screen.fill((0, 0, 0))
        card = pygame.Surface((60, 84))
        card.fill((200, 50, 50))
        r = ffx.draw_flight(screen, card, pygame.Rect(0, 0, 20, 20), pygame.Rect(200, 100, 60, 84), 0.5)
        self.assertTrue(screen.get_rect().colliderect(r))
        self.assertNotEqual(screen.get_at(r.center)[:3], (0, 0, 0))
        screen.fill((0, 0, 0))
        ends = ffx.draw_link(screen, pygame.Rect(10, 10, 30, 40), pygame.Rect(200, 120, 40, 50), ffx.TRIGGER, 1.0, True, True, 2)
        self.assertIsNotNone(ends)
        self.assertGreater(pygame.mask.from_threshold(screen, ffx.TRIGGER, (30, 30, 30, 255)).count(), 20)
        self.assertIsNone(ffx.draw_link(screen, (100, 100), pygame.Rect(98, 98, 4, 4), ffx.TRIGGER, 0, False))     # too short to draw


def region_diff(a, b, rect):
    ra = pygame.Rect(rect).clip(a.get_rect())
    diff = 0
    sa, sb = a.subsurface(ra), b.subsurface(ra)
    for x in range(ra.w):
        for y in range(ra.h):
            if sa.get_at((x, y)) != sb.get_at((x, y)):
                diff += 1
    return diff


class TargetLineTests(unittest.TestCase):
    def state_with(self, targets):
        st = copy.deepcopy(load_state("stack_two_late"))
        st["stack"][0]["targets"] = targets
        return st

    def shots(self, targets, animations=True):
        """The table with the target lines, and the very same table without them (the stack row's own 'Targets:' text stays)."""
        gui = make_gui(self.state_with(targets))
        gui.animations = animations
        frame(gui, 2)
        with mock.patch("time.time", return_value=500.0):
            gui.render()
            with_line = gui.screen.copy()
            with mock.patch.object(gui, "draw_target_lines"):
                gui.render()
            without = gui.screen.copy()
        return gui, without, with_line

    def test_a_line_runs_from_the_stack_item_to_a_permanent_it_targets(self):
        gui0 = make_gui("stack_two_late")
        frame(gui0, 2)
        target = next(cid for cid in gui0.card_rects if cid not in [(it.get("card") or {}).get("id") for it in gui0.state["stack"]])
        gui, without, with_line = self.shots([f"c{target}"])
        rect = gui.card_rects[target]
        self.assertGreater(region_diff(without, with_line, rect.inflate(40, 40)), 30)

    def test_a_line_runs_to_a_player_it_targets(self):
        gui, without, with_line = self.shots(["p1"])
        self.assertIn(1, gui.panel_rects)
        self.assertGreater(region_diff(without, with_line, gui.panel_rects[1].inflate(40, 40)), 30)

    def test_the_lines_get_the_animation_setting(self):
        for animations in (True, False):
            gui = make_gui(self.state_with(["p1"]))
            gui.animations = animations
            frame(gui, 2)
            with mock.patch.object(ffx, "draw_link") as link:
                gui.render()
            self.assertTrue(link.called)
            self.assertIs(link.call_args.args[5], animations)
            self.assertEqual(link.call_args.args[7], 1)                      # the number of the stack row it belongs to

    def test_dashes_march_only_when_animating(self):
        def picture(now, animate):
            screen = pygame.Surface((300, 200))
            screen.fill((0, 0, 0))
            ffx.draw_link(screen, pygame.Rect(10, 10, 30, 40), pygame.Rect(200, 120, 40, 50), ffx.TRIGGER, now, animate, True)
            return screen
        self.assertGreater(region_diff(picture(1.0, True), picture(1.13, True), pygame.Rect(0, 0, 300, 200)), 0)
        self.assertEqual(region_diff(picture(1.0, False), picture(1.13, False), pygame.Rect(0, 0, 300, 200)), 0)

    def test_targets_that_are_not_on_screen_draw_nothing_and_do_not_crash(self):
        gui, without, with_line = self.shots(["c99999", "p77"])
        self.assertEqual(region_diff(without, with_line, gui.screen.get_rect()), 0)

    def test_the_lines_draw_at_small_windows_and_big_text(self):
        for size, scale in (((900, 600), 2.0), ((1360, 840), 1.75), ((1920, 1080), 1.0)):
            gui = make_gui(self.state_with(["p1"]), size=size, scale=scale)
            frame(gui, 3)


class FlightTests(unittest.TestCase):
    def arrival_state(self, from_hand=True, token=False, new_id=9100):
        st = copy.deepcopy(load_state("main1_lands"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        card = me["zones"]["hand"][0] if from_hand else copy.deepcopy(me["zones"]["battlefield"][0])
        if from_hand:
            me["zones"]["hand"] = [c for c in me["zones"]["hand"] if c["id"] != card["id"]]
            card = dict(card, zone="Battlefield", tapped=False, isLand=False)
        else:
            card = dict(card, id=new_id, tapped=False, isLand=False)
        card["token"] = token
        me["zones"]["battlefield"].append(card)
        return st, card["id"]

    def test_a_card_played_from_my_hand_starts_where_it_was_in_my_hand(self):
        gui = make_gui("main1_lands")
        frame(gui, 2)
        hand_id = gui.session.me()["zones"]["hand"][0]["id"]
        before = pygame.Rect(gui.card_rects[hand_id])
        st, cid = self.arrival_state()
        self.assertEqual(cid, hand_id)
        gui.session.handle(st)
        gui.sync()
        self.assertEqual(gui.arrivals.origin[cid], before)

    def test_a_token_has_no_origin_and_a_card_from_nowhere_starts_from_the_controllers_area(self):
        gui = make_gui("main1_lands")
        frame(gui, 2)
        st, cid = self.arrival_state(from_hand=False, token=True)
        gui.session.handle(st)
        gui.sync()
        self.assertNotIn(cid, gui.arrivals.origin)
        st, cid = self.arrival_state(from_hand=False, token=False, new_id=9101)
        gui.session.handle(st)
        gui.sync()
        self.assertIn(cid, gui.arrivals.origin)

    def test_the_card_is_not_drawn_in_place_while_it_glides_and_is_afterwards(self):
        # Round 23: the glide is now driven by FLIGHT springs stepped from real time.monotonic() dt between drawn frames, not
        # by wall-clock time.time() progress, so both clocks are mocked together (monotonic() is what forge_table now reads).
        gui = make_gui("main1_lands")
        frame(gui, 2)
        st, cid = self.arrival_state()
        gui.session.handle(st)
        with mock.patch("time.time", return_value=3000.0), mock.patch("time.monotonic", return_value=1000.0):
            gui.sync()
            gui.render()
            dest = pygame.Rect(gui.card_rects[cid])
            start = gui.screen.copy()
            flying = gui.is_flying(gui.session.card(cid))
        with mock.patch("time.time", return_value=3000.0 + ffx.FLY_SECONDS + 0.05), \
             mock.patch("time.monotonic", return_value=1000.0 + 0.75):     # well past the 0.6 s flight cutoff
            gui.render()
            landed = gui.screen.copy()
        self.assertTrue(flying)
        # at the start the destination is (nearly) empty table; once landed the card picture is there
        inner = dest.inflate(-dest.w // 3, -dest.h // 3)
        self.assertGreater(region_diff(start, landed, inner), inner.w * inner.h // 4)

    def test_with_animations_off_the_card_is_simply_there(self):
        gui = make_gui("main1_lands")
        gui.animations = False
        frame(gui, 2)
        st, cid = self.arrival_state()
        gui.session.handle(st)
        with mock.patch("time.time", return_value=3500.0):
            gui.sync()
            gui.render()
            self.assertFalse(gui.is_flying(gui.session.card(cid)))


def gui_button_labels(gui):
    """The words on the bar's buttons this frame (found by drawing them again with a recording draw_button)."""
    seen = []
    original = gui.draw_button

    def record(rect, label, *a, **kw):
        seen.append(label)
        return original(rect, label, *a, **kw)
    gui.draw_button = record
    gui.render()
    gui.draw_button = original
    return seen


if __name__ == "__main__":
    unittest.main()
