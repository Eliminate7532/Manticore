# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 8: status badges (monarch, initiative, the Ring, emblems), Restart and Concede in the cog, and the life-change flash."""
import copy
import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_dialogs as dlg
import forge_settings as fset
import forge_status as fstat
import flow_screens as flow
import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_deck_screen import FakeLauncher
from tests.test_forge_table import click, frame, key, make_gui, move, point_for


def effect(cid, name, text=""):
    """A card of the kind Forge keeps in the command zone for monarch, initiative, emblems ... (found live: text is empty)."""
    return {"id": cid, "hidden": False, "zone": "Command", "name": name, "oracleName": name, "type": "", "cost": "", "colors": [],
            "text": text, "commander": False}


def with_effects(state, mine=(), theirs=()):
    st = copy.deepcopy(load_state(state) if isinstance(state, str) else state)
    me = [p for p in st["players"] if p["id"] == st["me"]][0]
    opp = [p for p in st["players"] if p["id"] != st["me"]][0]
    me["zones"]["command"] += [effect(*e) for e in mine]
    opp["zones"]["command"] += [effect(*e) for e in theirs]
    return st


def cog_button(gui, name):
    click(gui, point_for(gui, "button", name="settings"))
    return next(r.center for r, n in gui.overlay.buttons if n == name)


class BadgeDataTests(unittest.TestCase):
    def test_a_command_zone_with_only_commanders_has_no_badges(self):
        cmd = [{"id": 1, "name": "Kinnan, Bonder Prodigy", "commander": True}]
        self.assertEqual(fstat.badges_for(cmd, [1]), [])
        self.assertEqual(fstat.badges_for([], []), [])
        self.assertEqual(fstat.badges_for(None, None), [])

    def test_monarch_initiative_and_ring_have_their_own_badges_in_a_fixed_order(self):
        cmd = [effect(5, "The Ring"), effect(6, "The Monarch"), effect(7, "The Initiative")]
        self.assertEqual([b.key for b in fstat.badges_for(cmd, [])], ["monarch", "initiative", "ring"])

    def test_a_commander_is_never_a_badge_even_with_an_odd_name(self):
        cmd = [{"id": 1, "name": "The Monarch", "commander": True}, effect(2, "The Monarch")]
        self.assertEqual(len(fstat.badges_for(cmd, [1])), 1)
        self.assertEqual(len(fstat.badges_for([effect(3, "The Monarch")], [3])), 0)

    def test_the_same_effect_twice_is_one_badge(self):
        self.assertEqual(len(fstat.badges_for([effect(1, "The Monarch"), effect(2, "The Monarch")], [])), 1)

    def test_an_emblem_uses_its_own_text_or_a_plain_explanation(self):
        b = fstat.badges_for([effect(1, "Teferi Emblem", "You may cast spells as though they had flash.")], [])[0]
        self.assertEqual((b.key, b.label), ("emblem", "Teferi Emblem"))
        self.assertIn("as though they had flash", b.text)
        b = fstat.badges_for([effect(1, "Teferi Emblem")], [])[0]
        self.assertIn("emblem", b.text.lower())

    def test_several_other_effects_fold_into_one_chip_that_names_them(self):
        cmd = [effect(1, "A Emblem"), effect(2, "B Emblem"), effect(3, "Dungeon of Doom")]
        (b,) = fstat.badges_for(cmd, [])
        self.assertEqual(b.label, "Effects 3")
        for name in ("A Emblem", "B Emblem", "Dungeon of Doom"):
            self.assertIn(name, b.text)

    def test_a_leading_the_is_dropped_from_the_label_of_an_unknown_effect(self):
        (b,) = fstat.badges_for([effect(1, "The Cloak")], [])
        self.assertEqual(b.label, "Cloak")


class BadgeOnTableTests(unittest.TestCase):
    def badges(self, gui):
        return [(r, d) for r, k, d in gui.hits if k == "badge"]

    def test_no_effects_no_chips(self):
        self.assertEqual(self.badges(make_gui("main1_start")), [])

    def test_the_monarch_shows_on_my_panel_and_the_tooltip_explains_it(self):
        gui = make_gui(with_effects("main1_start", mine=[(901, "The Monarch")]))
        (rect, data), = self.badges(gui)
        self.assertIn("monarch", data["text"].lower())
        move(gui, rect.center)
        self.assertIn("extra card", gui.tooltip_text())

    def test_an_opponents_initiative_and_ring_show_on_their_panel(self):
        gui = make_gui(with_effects("main1_start", theirs=[(902, "The Initiative"), (903, "The Ring")]))
        found = self.badges(gui)
        self.assertEqual(len(found), 2)
        opp_rects = gui.L.opp_rects
        for rect, _ in found:
            self.assertTrue(any(o.contains(rect) for o in opp_rects), (rect, opp_rects))

    def test_effects_do_not_appear_as_extra_commander_cards(self):
        plain = make_gui("main1_start")
        gui = make_gui(with_effects("main1_start", mine=[(901, "The Monarch")], theirs=[(902, "The Initiative")]))
        cards = lambda g: sorted(d["card"]["id"] for _r, k, d in g.hits if k == "card")
        self.assertEqual(cards(gui), cards(plain))

    def test_chips_stay_inside_their_panel_at_every_size_and_in_a_pod(self):
        mine = [(901, "The Monarch"), (904, "The Ring"), (905, "The Initiative")]
        theirs = [(902, "The Initiative"), (903, "The Ring"), (906, "The Monarch"), (907, "X Emblem"), (908, "Y Emblem")]
        for size, scale in (((1360, 840), 1.0), ((900, 600), 1.0), ((1100, 700), 2.0), ((1920, 1080), 1.0)):
            gui = make_gui(with_effects("main1_start", mine=mine, theirs=theirs), size=size, scale=scale)
            panels = [gui.L.my_info] + list(gui.L.opp_rects)
            found = self.badges(gui)
            self.assertTrue(found, (size, scale))
            for rect, _ in found:
                self.assertTrue(any(p.contains(rect) for p in panels), (size, scale, rect, panels))

    def test_pod_panels_keep_their_chips_on_the_panel(self):
        st = with_effects("declare_blockers", theirs=[(902, "The Initiative"), (903, "The Ring")])
        base = [p for p in st["players"] if p["id"] != st["me"]][0]
        for k in (1, 2):
            p = copy.deepcopy(base)
            p["id"], p["name"] = 10 + k, f"AI {k + 1}"
            for z in p["zones"].values():
                for c in z:
                    c["id"] += 1000 * k
            st["players"].append(p)
        gui = make_gui(st, (1440, 900))
        found = self.badges(gui)
        self.assertEqual(len(found), 6)
        for rect, _ in found:
            self.assertTrue(any(o.contains(rect) for o in gui.L.opp_rects), rect)

    def test_a_chip_on_a_targetable_panel_still_means_click_the_player(self):
        gui = make_gui(with_effects("main1_start", theirs=[(902, "The Monarch")]))
        opp = [p for p in gui.state["players"] if p["id"] != gui.state["me"]][0]
        rect, _ = self.badges(gui)[0]
        gui.panel_targetable = lambda player: True
        click(gui, rect.center)
        self.assertEqual(gui.session.commands("player"), [{"c": "player", "id": opp["id"]}])

    def test_a_chip_on_a_panel_that_is_not_targetable_sends_nothing(self):
        gui = make_gui(with_effects("main1_start", theirs=[(902, "The Monarch")]))
        rect, _ = self.badges(gui)[0]
        click(gui, rect.center)
        self.assertEqual(gui.session.commands("player"), [])

    def test_the_crown_is_drawn_for_the_monarch(self):
        gui = make_gui(with_effects("main1_start", mine=[(901, "The Monarch")]))
        rect, _ = self.badges(gui)[0]
        gold = [gui.screen.get_at((x, y))[:3] for x in range(rect.x, rect.x + rect.h + 6) for y in range(rect.y, rect.bottom)]
        self.assertIn(tuple(ft.GOLD[:3]), gold)


class RestartConcedeTests(unittest.TestCase):
    def make(self, state="main1_start", launcher=True, decks=True):
        session = FakeSession(load_state(state), load_log())
        self.launcher = FakeLauncher() if launcher else None
        gui = ft.ForgeTable(session, StubStore(), window_size=(1360, 840), launcher=self.launcher)
        if decks:
            gui.current_decks = ((["Kinnan, Bonder Prodigy"], ["Forest"] * 99), [(["Tymna the Weaver"], ["Island"] * 99)])
        frame(gui, 3)
        return gui

    def test_the_pop_up_lists_new_game_and_concede_once_each(self):
        gui = self.make()
        click(gui, point_for(gui, "button", name="settings"))
        names = [n for _r, n in gui.overlay.buttons]
        for n in ("newgame", "concede", "help", "report", "full", "motion", "smaller", "bigger"):
            self.assertIn(n, names)
        for gone in ("restart", "concede_close"):                    # folded into the two windows below
            self.assertNotIn(gone, names)

    def test_new_game_asks_what_you_mean_and_enter_does_nothing(self):
        gui = self.make()
        click(gui, cog_button(gui, "newgame"))
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertIn("in progress", gui.modal.text)
        self.assertEqual(gui.modal.cancel, "Keep playing")
        key(gui, pygame.K_RETURN)                                     # a stray Enter must not end the game
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertEqual(self.launcher.calls, [])
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.modal)
        self.assertEqual(self.launcher.calls, [])

    def test_new_game_same_decks_starts_them_again_with_a_new_session(self):
        gui = self.make()
        old = gui.session
        decks = copy.deepcopy(gui.current_decks)
        gui.life_seen, gui.life_flash = {1: 1}, {1: (-3, time.time() + 5)}
        click(gui, cog_button(gui, "newgame"))
        click(gui, next(r.center for r, n in gui.modal.buttons if n == "opt0"))
        self.assertEqual(len(self.launcher.calls), 1)
        mine, opps = self.launcher.calls[0]
        self.assertEqual((mine, opps), (decks[0], decks[1]))
        self.assertIsNot(gui.session, old)
        self.assertEqual(gui.life_flash, {})                                         # reset_game ran
        self.assertNotIn(1, gui.life_seen)
        self.assertEqual(gui.current_decks, decks)

    def test_new_game_choose_decks_opens_the_deck_screen_and_starts_nothing(self):
        gui = self.make()
        click(gui, cog_button(gui, "newgame"))
        click(gui, next(r.center for r, n in gui.modal.buttons if n == "opt1"))
        self.assertIsNotNone(gui.menu)
        self.assertEqual(self.launcher.calls, [])

    def test_new_game_with_no_decks_known_goes_straight_to_the_deck_screen(self):
        gui = self.make(decks=False)
        self.assertFalse(gui.can_restart())
        click(gui, cog_button(gui, "newgame"))
        self.assertIsNone(gui.modal)
        self.assertIsNotNone(gui.menu)

    def test_new_game_without_the_deck_screen_launcher_says_so(self):
        gui = self.make(launcher=False)
        self.assertFalse(gui.can_restart())
        gui.ask_new_game()
        self.assertIsNone(gui.modal)
        self.assertIsNotNone(gui.toast)

    def test_new_game_after_the_game_is_over_does_not_warn_about_a_game_in_progress(self):
        gui = self.make()
        gui.session.game_over = True
        self.assertFalse(gui.game_in_progress())
        gui.ask_new_game()
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertNotIn("in progress", gui.modal.text)
        self.assertEqual(gui.modal.cancel, "Cancel")

    def test_a_restart_that_cannot_start_says_so_and_opens_the_deck_screen(self):
        gui = self.make()
        self.launcher.fail = "Java is missing"
        gui.restart_game()
        self.assertIn("Java is missing", gui.toast[0])
        self.assertIsNotNone(gui.menu)
        self.assertFalse(gui.can_restart())

    def test_the_deck_screen_start_remembers_the_decks_for_restart(self):
        gui = self.make(decks=False)

        class Entry:
            ok, error = True, None
            commanders, deck = ["Tymna the Weaver"], ["Plains"] * 99

            def load(self):
                pass

        self.assertIsNone(gui.start_game(Entry(), Entry(), 2, {"count": 2}))
        self.assertEqual(len(gui.current_decks[1]), 2)
        self.assertEqual(gui.current_decks[0], (["Tymna the Weaver"], ["Plains"] * 99))
        self.assertTrue(gui.can_restart())

    def test_concede_asks_and_nothing_is_sent_until_you_choose(self):
        gui = self.make()
        click(gui, cog_button(gui, "concede"))
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertEqual([style for _l, _r, style in gui.modal.options], ["danger", "danger", "danger"])
        key(gui, pygame.K_RETURN)                                     # a stray Enter must not concede
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        self.assertEqual(gui.session.commands("concede"), [])
        key(gui, pygame.K_ESCAPE)                                     # Esc = keep playing
        self.assertIsNone(gui.modal)
        self.assertEqual(gui.session.commands("concede"), [])
        self.assertTrue(gui.running)

    def test_concede_and_stay_gives_up_but_keeps_the_program_open(self):
        gui = self.make()
        click(gui, cog_button(gui, "concede"))
        click(gui, next(r.center for r, n in gui.modal.buttons if n == "opt1"))
        self.assertEqual(gui.session.commands("concede"), [{"c": "concede"}])
        self.assertTrue(gui.running)

    def test_concede_and_close_gives_up_and_ends_the_program(self):
        gui = self.make()
        click(gui, cog_button(gui, "concede"))
        click(gui, next(r.center for r, n in gui.modal.buttons if n == "opt2"))
        self.assertEqual(gui.session.commands("concede"), [{"c": "concede"}])
        self.assertFalse(gui.running)

    def test_the_number_keys_choose_the_options(self):
        gui = self.make()
        click(gui, cog_button(gui, "concede"))
        key(gui, pygame.K_3)
        self.assertEqual(gui.session.commands("concede"), [{"c": "concede"}])
        self.assertFalse(gui.running)

    def test_concede_and_return_to_the_main_menu(self):
        """Round 32 (Karl, 3 Oct: "Concede and return to main menu should be an option"): the first option concedes, ends the
        game's engine and shows the title's main menu; the program keeps running and Play opens the deck screen."""
        gui = self.make()
        session = gui.session
        closed = []
        session.close = lambda: closed.append(True)
        click(gui, cog_button(gui, "concede"))
        self.assertEqual(gui.modal.options[0][0], "Concede and return to the main menu")
        click(gui, next(r.center for r, n in gui.modal.buttons if n == "opt0"))
        self.assertEqual(session.commands("concede"), [{"c": "concede"}])
        self.assertEqual(closed, [True])                              # this game's engine is ended
        self.assertTrue(gui.running)
        self.assertIsNone(gui.modal)
        self.assertIsNotNone(gui.boot)
        self.assertEqual(gui.boot.stage, "menu")
        self.assertFalse(gui.game_showing())
        frame(gui, 2)
        self.assertEqual([n for _r, n in gui.boot.buttons], ["play", "continue", "settings", "credits", "quit"])
        click(gui, next(r.center for r, n in gui.boot.buttons if n == "play"))
        frame(gui, 1)
        self.assertIsNotNone(gui.menu)
        self.assertFalse(gui.menu.has_game)

    def test_the_safe_button_is_the_highlighted_one_only_when_something_is_at_stake(self):
        gui = self.make()
        click(gui, cog_button(gui, "concede"))
        frame(gui, 1)
        self.assertTrue(gui.modal.options and any(s == "danger" for _l, _r, s in gui.modal.options))
        gui.modal = dlg.OptionsDialog("Pick", "one", [("A", lambda: None, "normal")], "Never mind")
        frame(gui, 1)
        self.assertIn("cancel", [n for _r, n in gui.modal.buttons])

    def test_concede_is_off_when_no_game_is_running(self):
        gui = self.make()
        gui.shown_over = True                                         # the game-over window was already dismissed
        gui.session.game_over = True
        frame(gui, 1)
        click(gui, point_for(gui, "button", name="settings"))
        self.assertNotIn("concede", [n for _r, n in gui.overlay.buttons])
        gui.overlay = None
        gui.ask_concede()
        self.assertIsNone(gui.modal)
        self.assertIsNotNone(gui.toast)

    def test_the_option_windows_fit_at_small_sizes_and_big_text(self):
        for size, scale in (((900, 600), 1.0), ((900, 600), 2.0), ((1100, 700), 2.0), ((1920, 1080), 1.0)):
            for ask in ("ask_concede", "ask_new_game"):
                gui = self.make()
                gui.text_scale = scale
                gui.screen = pygame.display.set_mode(size)
                frame(gui, 2)
                getattr(gui, ask)()
                frame(gui, 2)
                self.assertTrue(pygame.Rect(0, 0, *size).contains(gui.modal.rect), (size, scale, ask))
                for r, n in gui.modal.buttons:
                    self.assertTrue(gui.modal.rect.contains(r), (size, scale, ask, n))
                    self.assertGreaterEqual(r.h, 40)

    def test_the_pop_up_fits_the_window(self):
        for size, scale in (((1360, 840), 1.0), ((900, 600), 1.0), ((900, 600), 2.0), ((1100, 700), 2.0), ((1920, 1080), 1.75)):
            gui = self.make()
            gui.text_scale = scale
            frame(gui, 2)
            click(gui, point_for(gui, "button", name="settings"))
            window = pygame.Rect(0, 0, *gui.screen.get_size())
            self.assertTrue(window.contains(gui.overlay.rect), (size, scale, gui.overlay.rect))
            for r, n in gui.overlay.buttons:
                self.assertTrue(gui.overlay.rect.contains(r), (size, scale, n, r))
                self.assertGreaterEqual(r.h, 28)

    def test_help_describes_the_three_groups(self):
        text = " ".join(h for _k, h in ft.HELP_LINES)
        for word in ("DISPLAY", "GAME", "HELP", "New game...", "Concede...", "same decks"):
            self.assertIn(word, text)


class HelpWindowTests(unittest.TestCase):
    """The controls list used to sit in a fixed-size panel; the list grew and its last lines ran under the Close button."""

    def open_help(self, size, scale):
        gui = make_gui("main1_start", size=size, scale=scale)
        key(gui, pygame.K_h)
        self.assertIsInstance(gui.modal, dlg.HelpDialog)
        frame(gui, 2)
        return gui

    def test_every_line_is_readable_above_the_close_button_or_reachable_by_scrolling(self):
        for size, scale in (((1360, 840), 1.0), ((1920, 1080), 1.0), ((900, 600), 1.0), ((1100, 700), 2.0), ((1360, 840), 1.5)):
            gui = self.open_help(size, scale)
            h = gui.modal
            close = next(r for r, n in h.buttons if n == "close")
            self.assertLessEqual(h.viewport.bottom, close.y, (size, scale))
            self.assertTrue(gui.screen.get_rect().contains(h.rect), (size, scale))
            rows = h.rows(gui, h.rect.w)
            total = sum(r[2] for r in rows)
            self.assertEqual(h.max_scroll, max(0, total - h.viewport.h), (size, scale))
            if h.max_scroll:                                   # too small for all of it: the wheel reaches the end
                for _ in range(80):
                    h.wheel(gui, -1)
                self.assertEqual(h.scroll, h.max_scroll)
                frame(gui, 1)

    # Not "a normal window needs no scrolling": how tall the list is depends on the font (Karl's Windows font is taller than the
    # one on the machine these tests were first written on, so the list needed scrolling at 1360x840 and even at 1920x1080).
    # What must hold for any font: a window big enough has no scrolling, and in a smaller one the end of the list is reachable.
    BIG = (3000, 2600)                    # text stops growing past fs 1.7, so a taller window only adds room (Windows Segoe UI needed it at round 20)

    def test_a_very_big_window_shows_the_whole_list_without_scrolling(self):
        gui = self.open_help(self.BIG, 1.0)
        self.assertEqual(gui.modal.max_scroll, 0)

    def test_the_wheel_does_nothing_when_there_is_nothing_to_scroll(self):
        gui = self.open_help(self.BIG, 1.0)
        gui.modal.wheel(gui, -3)
        self.assertEqual(gui.modal.scroll, 0)

    def test_the_last_help_line_can_be_scrolled_into_the_panel(self):
        for size in ((1360, 840), (1920, 1080), (900, 600)):
            gui = self.open_help(size, 1.0)
            h = gui.modal
            for _ in range(80):
                h.wheel(gui, -1)
            frame(gui, 1)
            rows = h.rows(gui, h.rect.w)
            self.assertLessEqual(h.viewport.y - h.scroll + sum(r[2] for r in rows), h.viewport.bottom, size)
            self.assertEqual(rows[-1][0], "Where your data is")     # round 28 adds this as the new last help line


class StackTextTests(unittest.TestCase):
    """Karl: "the text of effects on the stack needs to be visible" - an Endurance trigger read "...up to one target player put..."."""
    ETB = ("Endurance - When Endurance enters, up to one target player puts all the cards from their graveyard on the bottom of "
           "their library in a random order.")

    def state(self, extra=0):
        st = copy.deepcopy(load_state("stack_two_late"))
        st["stack"][0]["text"] = self.ETB
        for _ in range(extra):
            st["stack"].append(copy.deepcopy(st["stack"][0]))
        return st

    def rows_text(self, gui, i=0):
        return " ".join(t for t, _f, _c in gui.L.stack_rows[i][1])

    def test_a_trigger_shows_every_word_of_its_text_at_any_size(self):
        for size, scale in (((1360, 840), 1.0), ((1920, 1080), 1.75), ((900, 600), 1.0), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = make_gui(self.state(), size, scale)
                text = self.rows_text(gui)
                self.assertIn("in a random order.", text)
                self.assertNotIn("...", text)
                self.assertNotIn("\u2026", text)

    def test_the_panel_grows_to_the_text_and_stays_inside_the_window(self):
        for size, scale in (((1360, 840), 1.0), ((1920, 1080), 1.75), ((900, 600), 1.5)):
            for extra in (0, 1, 5):
                with self.subTest(size=size, scale=scale, extra=extra):
                    gui = make_gui(self.state(extra), size, scale)
                    window = pygame.Rect(0, 0, *size)
                    self.assertTrue(window.contains(gui.L.stack), gui.L.stack)
                    self.assertGreater(gui.L.log.h, 0)
                    self.assertLessEqual(gui.L.stack.h, gui.L.right.h * ft.STACK_MAX_SHARE + 1)
                    if gui.L.stack_need <= gui.L.right.h * ft.STACK_MAX_SHARE:
                        self.assertGreaterEqual(gui.L.stack.h, gui.L.stack_need - 1)      # nothing to scroll: all of it fits

    def test_when_there_is_too_much_to_fit_the_wheel_scrolls_the_stack(self):
        gui = make_gui(self.state(6), (900, 600), 1.5)
        L = gui.L
        self.assertGreater(L.stack_need, L.stack.h)
        move(gui, L.stack.center)
        self.assertEqual(gui.stack_scroll, 0)
        for _ in range(60):
            gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1))
        frame(gui, 2)
        self.assertGreater(gui.stack_scroll, 0)
        stacks = [r for r, k, d in gui.hits if k == "stack"]
        self.assertTrue(stacks)
        for r in stacks:
            self.assertTrue(gui.L.stack.contains(r), (r, gui.L.stack))                       # nothing is drawn or clickable outside the panel
        last = gui.L.stack_rows[-1]
        content_bottom = gui.L.stack.bottom - 4
        self.assertLessEqual(max(r.bottom for r in stacks), content_bottom)
        for _ in range(60):                                                                    # and back to the top
            gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1))
        frame(gui, 2)
        self.assertEqual(gui.stack_scroll, 0)

    def test_the_wheel_over_the_log_still_scrolls_the_log(self):
        gui = make_gui(self.state(), (1360, 840))
        move(gui, gui.log_rect.center)
        before = gui.log_scroll
        gui.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=2))
        self.assertEqual(gui.stack_scroll, 0)
        self.assertNotEqual(gui.log_scroll, before)

    def test_a_very_long_spell_text_is_cut_after_a_few_lines(self):
        st = self.state()
        st["stack"][1]["card"]["text"] = "\r\n\r\n".join(f"Paragraph {n} of a card with a great deal to say." for n in range(30))
        gui = make_gui(st)
        lines = [t for t, _f, _c in gui.L.stack_rows[1][1]]
        self.assertLessEqual(len(lines), 2 + ft.SPELL_TEXT_LINES)
        self.assertTrue(lines[-1].endswith("..."), lines[-1])

    def test_hovering_a_stack_row_still_previews_its_card(self):
        gui = make_gui(self.state(), (1360, 840))
        rects = [(r, d["card"]["name"]) for r, k, d in gui.hits if k == "stack" and d.get("card")]
        self.assertTrue(rects)
        r, name = rects[0]
        move(gui, r.center)
        self.assertEqual(gui.last_preview["name"], name)


class HandNoticeTests(unittest.TestCase):
    """Karl tapped The One Ring: the card was drawn, but Forge writes nothing in its log for a draw and nothing on screen changed
    except a number. A card arriving in your hand now says so."""

    def gui_with_card(self, phase=None, turn=None):
        gui = make_gui("main1_start")
        frame(gui, 2)
        st = copy.deepcopy(load_state("main1_start"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        card = copy.deepcopy(me["zones"]["hand"][0])
        card["id"], card["name"], card["oracleName"] = 9999, "Sol Ring", "Sol Ring"
        me["zones"]["hand"].append(card)
        if phase:
            st["phase"] = phase
        if turn is not None:
            st["turn"] = turn
        return gui, st

    def log_text(self, gui):
        return [ln.text() for ln in gui._log_lines]

    def test_a_new_card_in_hand_gets_a_log_line_and_a_toast(self):
        gui, st = self.gui_with_card()
        self.assertFalse([t for t in self.log_text(gui) if "Into your hand" in t])        # the cards already there are not news
        gui.session.handle(st)
        frame(gui, 2)
        self.assertIn("Into your hand: Sol Ring", self.log_text(gui))
        self.assertIn("Into your hand: Sol Ring", gui.toast[0])
        frame(gui, 3)
        self.assertEqual(self.log_text(gui).count("Into your hand: Sol Ring"), 1)         # once, not every frame

    def test_the_draw_step_is_logged_but_does_not_pop_up_a_toast(self):
        gui, st = self.gui_with_card(phase="DRAW")
        gui.toast = None
        gui.session.handle(st)
        frame(gui, 2)
        self.assertIn("Into your hand: Sol Ring", self.log_text(gui))
        self.assertIsNone(gui.toast)

    def test_the_opening_hand_and_mulligans_are_not_announced(self):
        gui, st = self.gui_with_card(turn=0)
        gui.session.handle(st)
        frame(gui, 2)
        self.assertFalse([t for t in self.log_text(gui) if "Into your hand" in t])

    def test_a_new_game_starts_fresh(self):
        gui, st = self.gui_with_card()
        gui.session.handle(st)
        frame(gui, 2)
        gui.reset_game()
        self.assertIsNone(gui.hand_seen)
        self.assertEqual(gui._extra_rows, [])


class UndoFeedbackTests(unittest.TestCase):
    """Karl expected Undo to go back a step. Forge only reverses a mana tap that is still unspent, and it answers nothing when it
    can't, so the table used to look as if the button was broken. Now it says what happened."""

    def setUp(self):
        self.gui = make_gui("main1_start")
        frame(self.gui, 2)
        self.gui.toast = None

    def after_deadline(self):
        due, sig = self.gui.undo_check
        self.gui.undo_check = (time.time() - 1, sig)

    def test_undo_that_changes_nothing_says_so(self):
        click(self.gui, point_for(self.gui, "button", name="undo"))
        self.assertEqual([c["c"] for c in self.gui.session.sent][-1], "undo")
        frame(self.gui, 2)
        self.assertIsNone(self.gui.toast)                      # still giving Forge a moment to answer
        self.after_deadline()
        frame(self.gui, 2)
        self.assertEqual(self.gui.toast[0], ft.UNDO_NOTHING)
        self.assertIsNone(self.gui.undo_check)

    def test_the_u_key_and_ctrl_z_do_the_same(self):
        for k, mod in ((pygame.K_u, 0), (pygame.K_z, pygame.KMOD_CTRL)):
            self.gui.toast = None
            key(self.gui, k, mod=mod)
            self.after_deadline()
            frame(self.gui, 2)
            self.assertEqual(self.gui.toast[0], ft.UNDO_NOTHING)

    def board_with_land_tapped(self, tapped):
        st = copy.deepcopy(load_state("main1_start"))
        me = [p for p in st["players"] if p["id"] == st["me"]][0]
        land = copy.deepcopy(me["zones"]["hand"][0])
        land["id"], land["tapped"], land["isLand"] = 9998, tapped, True
        me["zones"]["battlefield"].append(land)
        return st

    def test_undo_that_worked_stays_quiet(self):
        self.gui.session.handle(self.board_with_land_tapped(True))      # I tapped a land for mana ...
        frame(self.gui, 2)
        key(self.gui, pygame.K_u)
        self.gui.session.handle(self.board_with_land_tapped(False))     # ... and Forge sends the board with it untapped again
        frame(self.gui, 2)
        self.assertIsNone(self.gui.undo_check)
        self.assertIsNone(self.gui.toast)

    def test_a_new_game_forgets_a_pending_check(self):
        key(self.gui, pygame.K_u)
        self.gui.reset_game()
        self.assertIsNone(self.gui.undo_check)

    def test_help_tells_the_truth_about_undo(self):
        text = " ".join(f"{k} {v}" for k, v in ft.HELP_LINES)
        self.assertIn("Undo", text)
        self.assertIn("mana", text.lower())
        self.assertNotIn("undo the last land or spell", text)


class EliminatedInAPodTests(unittest.TestCase):
    def pod(self, lost, players=3):
        st = copy.deepcopy(load_state("declare_blockers"))
        base = [p for p in st["players"] if p["id"] != st["me"]][0]
        for k in range(1, players - 1):
            p = copy.deepcopy(base)
            p["id"], p["name"] = 10 + k, f"AI {k + 1}"
            for z in p["zones"].values():
                for c in z:
                    c["id"] += 1000 * k
            st["players"].append(p)
        [p for p in st["players"] if p["id"] == st["me"]][0]["lost"] = lost
        return st

    def test_losing_in_a_pod_says_so_once_and_offers_the_deck_screen(self):
        gui = make_gui(self.pod(True), (1440, 900))
        self.assertEqual(gui.end_screen.kind, "out")                    # Round AD2b: a full-screen DEFEAT with Keep watching / Leave
        self.assertIsNone(gui.modal)
        gui.press("end_watch")
        frame(gui, 3)
        self.assertIsNone(gui.end_screen)                               # it does not come back every frame
        self.assertIsNone(gui.modal)
        gui.end_screen = flow.end_screen_for(gui)
        with mock.patch.object(gui, "open_menu") as menu:
            gui.press("end_leave")
        menu.assert_called_once_with()                                  # Leave = the deck screen
        self.assertIsNone(gui.end_screen)

    def test_it_is_not_shown_while_i_am_still_alive_or_in_a_two_player_game(self):
        alive = make_gui(self.pod(False), (1440, 900))
        self.assertIsNone(alive.modal)
        self.assertIsNone(alive.end_screen)
        two = make_gui(self.pod(True, players=2), (1440, 900))
        self.assertNotIsInstance(two.modal, dlg.GameOverDialog)
        self.assertIsNone(two.end_screen)

    def test_when_the_whole_game_is_over_the_normal_game_over_window_wins(self):
        st = self.pod(True)
        st["gameOver"], st["winner"] = True, "AI 2"
        gui = make_gui(st, (1440, 900))
        self.assertEqual(gui.end_screen.kind, "lost")                   # Round AD2b: DEFEAT (the game is over), not "You are out"
        gui.end_continue()
        self.assertIsInstance(gui.modal, dlg.GameOverDialog)
        self.assertNotEqual(gui.modal.heading, "You are out")

    def test_a_new_game_can_show_it_again(self):
        gui = make_gui(self.pod(True), (1440, 900))
        self.assertTrue(gui.shown_out)
        gui.reset_game()
        self.assertFalse(gui.shown_out)


class LifeFlashTests(unittest.TestCase):
    def set_life(self, gui, pid, life):
        st = copy.deepcopy(gui.session.state)
        for p in st["players"]:
            if p["id"] == pid:
                p["life"] = life
        gui.session.state = st
        gui.sync()

    def test_no_flash_at_the_first_snapshot(self):
        gui = make_gui("main1_start")
        self.assertEqual(gui.life_flash, {})
        self.assertEqual(gui.life_flash_of(gui.my_id()), (0, 0.0))

    def test_a_loss_flashes_with_the_amount(self):
        gui = make_gui("main1_start")
        me = gui.my_id()
        self.set_life(gui, me, gui.session.me()["life"] - 3)
        change, left = gui.life_flash_of(me)
        self.assertEqual(change, -3)
        self.assertGreater(left, 0.9)

    def test_a_gain_flashes_positive_and_hits_in_a_row_add_up(self):
        gui = make_gui("main1_start")
        opp = gui.session.opponents()[0]["id"]
        life = gui.session.player(opp)["life"]
        self.set_life(gui, opp, life + 2)
        self.assertEqual(gui.life_flash_of(opp)[0], 2)
        self.set_life(gui, opp, life + 2 - 5)
        self.assertEqual(gui.life_flash_of(opp)[0], -3)

    def test_the_flash_fades_out(self):
        gui = make_gui("main1_start")
        me = gui.my_id()
        self.set_life(gui, me, gui.session.me()["life"] - 1)
        gui.life_flash[me] = (-1, time.time() - 0.01)
        self.assertEqual(gui.life_flash_of(me), (0, 0.0))
        self.set_life(gui, me, gui.session.me()["life"] - 1)                     # a new change starts a fresh flash, not a sum
        self.assertEqual(gui.life_flash_of(me)[0], -1)

    def test_a_new_game_forgets_the_old_life_totals(self):
        gui = make_gui("main1_start")
        me = gui.my_id()
        self.set_life(gui, me, 30)
        gui.reset_game()
        self.assertEqual((gui.life_seen, gui.life_flash), ({}, {}))

    def red_pixels(self, gui, rect):
        n = 0
        for x in range(rect.x, rect.right, 2):
            for y in range(rect.y, rect.bottom, 2):
                r, g, b = gui.screen.get_at((x, y))[:3]
                if r > 235 and 90 < g < 130 and 90 < b < 130:
                    n += 1
        return n

    def test_the_life_number_and_the_change_are_drawn_in_red_for_a_loss(self):
        gui = make_gui("main1_start")
        me = gui.my_id()
        panel = gui.L.my_info
        before = self.red_pixels(gui, panel)
        self.set_life(gui, me, gui.session.me()["life"] - 3)
        frame(gui, 1)
        self.assertGreater(self.red_pixels(gui, panel), before + 20)
        gui.life_flash[me] = (-3, time.time() - 1)                                  # expired
        frame(gui, 1)
        self.assertLessEqual(self.red_pixels(gui, panel), before + 2)

    def test_a_flashing_life_total_glows_the_whole_panel(self):
        """Karl: 'More flashing around your health total for damage/lifegain' (round 15) - not just the number itself."""
        gui = make_gui("main1_start")
        me = gui.my_id()
        self.set_life(gui, me, gui.session.me()["life"] - 3)
        with mock.patch("forge_table.ffx.draw_life_flash") as spy:
            frame(gui, 1)
        self.assertTrue(spy.call_args_list, "draw_life_flash was never called for the player whose life just dropped")
        rect, colour, strength = spy.call_args_list[0].args[1], spy.call_args_list[0].args[2], spy.call_args_list[0].args[3]
        self.assertEqual(rect, gui.L.my_info)
        self.assertEqual(colour, ft.LIFE_LOSS)
        self.assertGreater(strength, 0.9)

    def test_no_glow_once_the_flash_has_faded(self):
        gui = make_gui("main1_start")
        me = gui.my_id()
        self.set_life(gui, me, gui.session.me()["life"] - 3)
        gui.life_flash[me] = (-3, time.time() - 1)                                  # expired
        with mock.patch("forge_table.ffx.draw_life_flash") as spy:
            frame(gui, 1)
        self.assertFalse(spy.call_args_list, "an expired flash should not still glow the panel")

    def test_compact_pod_panels_show_the_change_too(self):
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
        opp = gui.session.opponents()[0]["id"]
        self.set_life(gui, opp, gui.session.player(opp)["life"] - 4)
        frame(gui, 2)
        self.assertEqual(gui.life_flash_of(opp)[0], -4)
        self.assertGreater(self.red_pixels(gui, gui.L.opp_rects[0]), 20)


class SplashTests(unittest.TestCase):
    """The loading screen ("Loading...", was "Starting the Forge rules engine"): lines are measured, so they can never sit on top of each other."""

    class Art:
        def __init__(self, stage, ready):
            import threading
            self.stage, self.data_ready = stage, threading.Event()
            if ready:
                self.data_ready.set()

        def pending(self):
            return 0

        def collect(self):
            pass

    def drawn(self, gui):
        """(text, rect) for everything the splash draws with draw_text during one frame."""
        seen, real = [], ft.draw_text

        def spy(scr, s, x, y, font, colour=None, anchor="topleft", shadow=False):
            rect = real(scr, s, x, y, font, colour, anchor, shadow) if colour is not None else real(scr, s, x, y, font)
            seen.append((s, rect))
            return rect
        ft.draw_text = spy
        try:
            gui.sync()
            gui.render()
        finally:
            ft.draw_text = real
        return seen

    def check_clean(self, gui, what):
        window = pygame.Rect(0, 0, *gui.screen.get_size())
        items = [(s, r) for s, r in self.drawn(gui) if s.strip(".") and s.strip()]
        self.assertTrue(items, what)
        for s, r in items:
            self.assertTrue(window.contains(r), (what, s, r))
        for i, (a, ra) in enumerate(items):
            for b, rb in items[i + 1:]:
                self.assertFalse(ra.colliderect(rb), (what, a, ra, b, rb))

    def test_lines_never_overlap_at_any_window_or_text_size(self):
        long_stage = "Reading card scripts: 12345 of 30000 (this is a much longer status line than usual, on purpose)"
        for size in ((900, 600), (1360, 840), (1920, 1080), (640, 480)):
            for scale in (0.85, 1.0, 1.5, 2.0):
                for stage in ("", "Card pictures loading: 0 left", long_stage):
                    with self.subTest(size=size, scale=scale, stage=stage[:12]):
                        gui = make_gui(None, size, scale)
                        if stage:
                            gui.art = self.Art(stage, ready=stage.startswith("Card"))
                        self.check_clean(gui, (size, scale, stage))

    def test_the_title_does_not_move_as_the_dots_change(self):
        gui = make_gui(None, (1360, 840))
        xs = set()
        real = time.time
        try:
            for t in (1000.0, 1000.6, 1001.2, 1001.8):
                time.time = lambda t=t: t
                title = [r for s, r in self.drawn(gui) if s.startswith("Loading")]
                self.assertEqual(len(title), 1)
                xs.add(title[0].x)
        finally:
            time.time = real
        self.assertEqual(len(xs), 1, xs)

    def test_error_screen_with_a_long_message_stays_clean(self):
        for size in ((900, 600), (1920, 1080)):
            for scale in (1.0, 2.0):
                gui = make_gui(None, size, scale)
                gui.session.fatal = "Java could not start because " + "the engine files are missing or damaged, " * 8
                self.check_clean(gui, ("error", size, scale))


if __name__ == "__main__":
    unittest.main()
