# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AD2b: the full-screen moments - commander VS commander, the opening hand, VICTORY / DEFEAT (flow_screens.py)."""
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

import flow_screens as flow
import forge_dialogs as dlg
import forge_table as ft
from tests.forge_fake import load_state
from tests.test_forge_table import click, frame, key, make_gui, point_for, point_for_card

SIZES = (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1100, 700), 2.0))


def mulligan_state(**prompt):
    st = copy.deepcopy(load_state("mulligan"))
    st["prompt"].update(prompt)
    return st


def bottom_state(n=1, picked=()):
    st = copy.deepcopy(load_state("mulligan"))
    st["prompt"] = {"message": f"Return {n} card(s) to the bottom of your library", "ok": {"label": "OK", "enabled": len(picked) == n},
                    "cancel": {"label": "Auto", "enabled": True}, "selecting": True, "selMin": n, "selMax": n}
    me = [p for p in st["players"] if p["id"] == st["me"]][0]
    for c in me["zones"]["hand"]:
        c["selectable"] = True
        if c["id"] in picked:
            c["highlight"] = True
    return st


def buttons(gui):
    return {d["name"] for _r, k, d in gui.hits if k == "button"}


def labels_of(gui, name):
    """The label drawn on the LAST button with this name (the layer's, when the layer is up)."""
    seen = []
    real = gui.draw_button

    def spy(rect, label, n, *a, **kw):
        if n == name:
            seen.append(label)
        return real(rect, label, n, *a, **kw)
    with mock.patch.object(gui, "draw_button", side_effect=spy):
        frame(gui)
    return seen[-1] if seen else None


def game_over(winner="Karl", base="main1_start"):
    st = copy.deepcopy(load_state(base))
    st["gameOver"], st["winner"], st["turn"] = True, winner, 9
    return st


# ---- VS ----------------------------------------------------------------------------------------------------------------
class VsTests(unittest.TestCase):
    def make(self, seats, state=None, size=(1360, 840), scale=1.0):
        gui = make_gui(state, size, scale)
        gui.vs = flow.VsShow(seats)
        return gui

    def test_it_holds_while_the_engine_starts_then_for_a_moment_after_the_first_snapshot(self):
        gui = self.make([("You", ["Kinnan, Bonder Prodigy"], "Kinnan NBC"), ("AI", ["Tymna the Weaver"], "")])
        self.assertTrue(gui.vs.holding(gui))                       # no state yet
        gui.session.handle(load_state("main1_start"))
        frame(gui)
        now = time.monotonic()
        self.assertTrue(gui.vs.holding(gui, now))
        self.assertFalse(gui.vs.holding(gui, now + flow.VS_TAIL + 0.01))

    def test_a_click_or_space_skips_it_and_is_not_passed_to_the_game(self):
        for how in ("click", "space"):
            with self.subTest(how=how):
                gui = self.make([("You", ["Kinnan, Bonder Prodigy"], ""), ("AI", ["Tymna the Weaver"], "")])
                gui.session.handle(load_state("main1_start"))
                frame(gui)
                self.assertTrue(gui.vs.holding(gui))
                if how == "click":
                    click(gui, (5, 5))
                else:
                    key(gui, pygame.K_SPACE)
                self.assertTrue(gui.vs.skipped)
                self.assertEqual(gui.session.commands("ok"), [])      # Space skipped the screen; it did not pass priority
                self.assertTrue(any(k == "card" for _r, k, _d in gui.hits))   # the table is back

    def test_it_never_holds_over_a_dialog(self):
        gui = self.make([("You", ["A"], ""), ("AI", ["B"], "")], "main1_start")
        gui.modal = dlg.MessageDialog("x", "y")
        self.assertFalse(gui.vs.holding(gui))

    def test_two_to_four_seats_and_partners_draw_at_every_size(self):
        seats4 = [("You", ["Tymna the Weaver", "Kraum, Ludevic's Opus"], "Blue Farm"), ("AI 1", ["Kinnan, Bonder Prodigy"], "Kinnan"),
                  ("AI 2", ["Teysa Karlov"], "Aristocrats"), ("AI 3", ["Light-Paws, Emperor's Voice"], "Voltron")]
        for n in (2, 3, 4):
            for size, scale in SIZES:
                with self.subTest(n=n, size=size, scale=scale):
                    gui = self.make(seats4[:n], None, size, scale)
                    frame(gui)
                    self.assertEqual(gui.screen.get_size(), size)

    def test_the_deck_screen_start_and_restart_fill_in_the_seats(self):
        gui = make_gui("main1_start")
        gui.launcher = mock.Mock()
        gui.launcher.start.return_value = gui.session
        gui.begin((["Kinnan, Bonder Prodigy"], []), [(["Tymna the Weaver"], []), (["Teysa Karlov"], [])],
                  labels=("Kinnan NBC", ["Blue Farm", "Teysa"]))
        self.assertEqual(gui.vs.seats, [("You", ["Kinnan, Bonder Prodigy"], "Kinnan NBC"), ("AI 1", ["Tymna the Weaver"], "Blue Farm"),
                                         ("AI 2", ["Teysa Karlov"], "Teysa")])
        first = gui.vs
        gui.restart_game()                                         # Restart keeps the names
        self.assertIsNot(gui.vs, first)
        self.assertEqual(gui.vs.seats[1], ("AI 1", ["Tymna the Weaver"], "Blue Farm"))

    def test_resume_reads_the_commanders_from_the_saved_deck_files(self):
        text = "[metadata]\nName=Player\n[Commander]\n1 Tymna the Weaver\n1 Kraum, Ludevic's Opus|C16\n[Main]\n1 Sol Ring\n"
        self.assertEqual(flow.dck_commanders(text), ["Tymna the Weaver", "Kraum, Ludevic's Opus"])
        self.assertEqual(flow.dck_commanders(""), [])


# ---- the opening hand --------------------------------------------------------------------------------------------------
class MulliganScreenTests(unittest.TestCase):
    def test_the_keep_or_mulligan_question_gets_the_full_screen(self):
        gui = make_gui("mulligan")
        self.assertEqual(flow.pregame_kind(gui), "mulligan")
        self.assertTrue(any(k == "layer" for _r, k, _d in gui.hits))
        hand = gui.session.me()["zones"]["hand"]
        on_layer = [d["card"]["id"] for _r, k, d in gui.hits if k == "card"]
        self.assertTrue(set(c["id"] for c in hand) <= set(on_layer))
        self.assertTrue({"ok", "cancel", "view_table"} <= buttons(gui))

    def test_the_words_on_it(self):
        gui = make_gui("mulligan")
        self.assertEqual(flow.who_goes_first(gui), "You go first")
        self.assertEqual(labels_of(gui, "cancel"), "Mulligan")
        self.assertEqual(labels_of(gui, "ok"), "Keep")
        self.assertIn("Free: you draw a new seven.", self.texts(gui))
        self.assertIn("Play these 7 cards.", self.texts(gui))
        gui.mulligans = 2
        self.assertIn("You draw a new seven, then put 2 on the bottom.", " ".join(self.texts(gui)))   # (it may wrap)

    def texts(self, gui):
        out = []
        real = flow.draw_text
        with mock.patch.object(flow, "draw_text", side_effect=lambda scr, s, *a, **k: (out.append(s), real(scr, s, *a, **k))[1]):
            frame(gui)
        return out

    def test_each_ai_says_where_it_is_from_the_log(self):
        gui = make_gui("mulligan", log=False)
        name = gui.session.opponents()[0]["name"]
        self.assertEqual(flow.mulligan_status(gui), ["AI 1 is deciding..."])
        gui.session.handle({"t": "log", "entries": [{"type": "MULLIGAN", "text": f"{name} has mulliganed down to 6 cards."}]})
        self.assertEqual(flow.mulligan_status(gui), ["AI 1 took a mulligan, deciding on 6"])
        gui.session.handle({"t": "log", "entries": [{"type": "MULLIGAN", "text": f"{name} has kept a hand of 6 cards"}]})
        self.assertEqual(flow.mulligan_status(gui), ["AI 1 kept 6"])

    def test_keep_and_mulligan_send_exactly_what_the_action_bar_sends(self):
        """The replay test: the same click through the layer and through the bar (layer hidden) sends the same command."""
        for name in ("ok", "cancel"):
            with self.subTest(button=name):
                layer = make_gui("mulligan")
                click(layer, point_for(layer, "button", name=name))
                bar = make_gui("mulligan")
                bar.flow_hidden = bar.flow_key()
                frame(bar)
                click(bar, point_for(bar, "button", name=name))
                self.assertTrue(layer.session.sent)
                self.assertEqual(layer.session.sent, bar.session.sent)

    def test_space_still_means_keep(self):
        gui = make_gui("mulligan")
        key(gui, pygame.K_SPACE)
        self.assertEqual(len(gui.session.commands("ok")), 1)

    def test_putting_cards_on_the_bottom_sends_the_same_card_clicks(self):
        layer = make_gui(bottom_state(1))
        self.assertEqual(flow.pregame_kind(layer), "bottom")
        cid = layer.session.me()["zones"]["hand"][2]["id"]
        click(layer, point_for_card(layer, cid))
        bar = make_gui(bottom_state(1))
        bar.flow_hidden = bar.flow_key()
        frame(bar)
        click(bar, point_for_card(bar, cid))
        self.assertEqual(layer.session.commands("card"), bar.session.commands("card"))
        self.assertEqual(len(layer.session.commands("card")), 1)

    def test_the_bottom_button_counts_the_picked_cards(self):
        st = bottom_state(2)
        hand = [p for p in st["players"] if p["id"] == st["me"]][0]["zones"]["hand"]
        gui = make_gui(bottom_state(2, picked=(hand[0]["id"],)))
        self.assertEqual(labels_of(gui, "ok"), "Put on bottom")
        texts = []
        real = flow.draw_text
        with mock.patch.object(flow, "draw_text", side_effect=lambda scr, s, *a, **k: (texts.append(s), real(scr, s, *a, **k))[1]):
            frame(gui)
        self.assertIn("1 of 2 chosen.", texts)
        self.assertEqual(labels_of(gui, "cancel"), "Choose for me")

    def test_view_table_hides_this_question_only(self):
        gui = make_gui("mulligan")
        click(gui, point_for(gui, "button", name="view_table"))
        self.assertFalse(any(k == "layer" for _r, k, _d in gui.hits))
        self.assertIn("show_flow", buttons(gui))
        click(gui, point_for(gui, "button", name="show_flow"))
        self.assertTrue(any(k == "layer" for _r, k, _d in gui.hits))
        click(gui, point_for(gui, "button", name="view_table"))
        gui.session.handle(bottom_state(1))                        # the next question shows again by itself
        frame(gui)
        self.assertTrue(any(k == "layer" for _r, k, _d in gui.hits))

    def test_no_layer_once_the_game_is_under_way(self):
        gui = make_gui("main1_start")
        self.assertIsNone(flow.pregame_kind(gui))
        self.assertFalse(any(k == "layer" for _r, k, _d in gui.hits))

    def test_it_fits_at_every_size(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = make_gui("mulligan", size, scale)
                screen = pygame.Rect((0, 0), size)
                for _r, k, d in gui.hits:
                    if k == "button" and d["name"] in ("ok", "cancel", "view_table"):
                        self.assertTrue(screen.contains(_r), (d["name"], _r))


# ---- VICTORY / DEFEAT --------------------------------------------------------------------------------------------------
class EndScreenTests(unittest.TestCase):
    def test_who_won_decides_the_word(self):
        for winner, kind, word in (("Karl", "won", "VICTORY"), ("AI 1 (Kinnan)", "lost", "DEFEAT"), (None, "draw", "DRAW")):
            with self.subTest(winner=winner):
                gui = make_gui(game_over(winner))
                self.assertEqual(gui.end_screen.kind, kind)
                self.assertEqual(flow.WORDS[kind], word)
                self.assertIsNone(gui.modal)
        self.assertIn("AI 1 won on turn 9", make_gui(game_over("AI 1 (Kinnan)")).end_screen.line)

    def test_click_space_or_continue_opens_the_old_dialog(self):
        for how in ("click", "space", "button"):
            with self.subTest(how=how):
                gui = make_gui(game_over())
                if how == "click":
                    click(gui, (5, gui.L.H - 5))
                elif how == "space":
                    key(gui, pygame.K_SPACE)
                else:
                    click(gui, point_for(gui, "button", name="end_continue"))
                self.assertIsNone(gui.end_screen)
                self.assertIsInstance(gui.modal, dlg.GameOverDialog)

    def test_view_battlefield_and_back(self):
        gui = make_gui(game_over())
        click(gui, point_for(gui, "button", name="view_table"))
        self.assertFalse(any(k == "layer" for _r, k, _d in gui.hits))
        self.assertIsNotNone(gui.end_screen)
        click(gui, point_for(gui, "button", name="show_flow"))
        self.assertTrue(any(k == "layer" for _r, k, _d in gui.hits))

    def test_a_request_at_game_over_still_opens_on_top(self):
        gui = make_gui(game_over())
        gui.session.handle({"t": "request", "id": 3, "kind": "confirm", "title": "Report?", "message": "Send a report?"})
        frame(gui)
        self.assertIsNotNone(gui.modal)
        self.assertIsNotNone(gui.end_screen)                       # it waits underneath

    def test_knocked_out_of_a_pod_then_the_game_ends_shows_defeat_once(self):
        from tests.test_round8 import EliminatedInAPodTests
        pod = EliminatedInAPodTests.pod(EliminatedInAPodTests(), True)
        gui = make_gui(pod, (1440, 900))
        self.assertEqual(gui.end_screen.kind, "out")
        self.assertEqual({"end_watch", "end_leave"} & buttons(gui), {"end_watch", "end_leave"})
        click(gui, point_for(gui, "button", name="end_watch"))
        over = copy.deepcopy(pod)
        over["gameOver"], over["winner"] = True, "AI 2"
        gui.session.handle(over)
        frame(gui, 2)
        self.assertIsNone(gui.end_screen)                          # no second DEFEAT
        self.assertIsInstance(gui.modal, dlg.GameOverDialog)

    def test_it_fits_and_animates_only_with_animations_on(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = make_gui(game_over(), size, scale)
                screen = pygame.Rect((0, 0), size)
                r = [r for r, k, d in gui.hits if k == "button" and d["name"] == "end_continue"][0]
                self.assertTrue(screen.contains(r))
        gui = make_gui(game_over())
        self.assertTrue(gui.activity(time.monotonic())[0])
        gui.animations = False
        gui.end_screen.t0 -= 10
        self.assertFalse(gui.activity(time.monotonic())[0])

    def test_a_new_game_clears_it(self):
        gui = make_gui(game_over())
        gui.reset_game()
        self.assertIsNone(gui.end_screen)


class NoColourLiteralsTests(unittest.TestCase):
    def test_flow_screens_uses_gfx_tokens_only(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "flow_screens.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        found = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Tuple) and len(n.elts) in (3, 4)
                 and all(isinstance(e, ast.Constant) and type(e.value) is int and 0 <= e.value <= 255 for e in n.elts)]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
