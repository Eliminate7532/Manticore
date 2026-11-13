# SPDX-License-Identifier: GPL-3.0-or-later
"""Bug 1567709499526226001 (Karl, 4 Nov 2026, 0.28.53.15): in a two-player Standard game the opening-hand screen said the
first mulligan was free, then asked him to put a card on the bottom. The bridge (MulliganService, round CON1) gives a free
first mulligan only with commander rules or more than two players (CR 103.5c); the table's words assumed it always."""
import copy
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flow_screens as flow
import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_state
from tests.test_forge_table import frame


def gui_for(fmt, state=None, players=None, message=None, ok=None, cancel=None):
    st = copy.deepcopy(load_state("mulligan") if state is None else state)
    if message is not None:
        st["prompt"]["message"] = message
    if ok is not None:
        st["prompt"]["ok"] = ok
    if cancel is not None:
        st["prompt"]["cancel"] = cancel
    if players is not None:
        base = st["players"]
        while len(st["players"]) < players:
            extra = copy.deepcopy([p for p in base if p["id"] != st["me"]][0])
            extra["id"] = max(p["id"] for p in st["players"]) + 1
            extra["name"] = f"AI {len(st['players'])}"
            st["players"].append(extra)
    session = FakeSession(st, None)
    session.fmt = fmt
    gui = ft.ForgeTable(session, StubStore(), window_size=(1360, 840))
    frame(gui, 3)
    return gui


def drawn_texts(gui):
    out = []
    real = flow.draw_text
    with mock.patch.object(flow, "draw_text", side_effect=lambda scr, s, *a, **k: (out.append(s), real(scr, s, *a, **k))[1]):
        frame(gui)
    return " ".join(out)


class StandardOneVsOneTests(unittest.TestCase):
    def test_the_opening_hand_does_not_promise_a_free_mulligan(self):
        gui = gui_for("standard")
        self.assertFalse(gui.first_mulligan_free())
        self.assertEqual(gui.mulligan_cost(), 1)
        texts = drawn_texts(gui)
        self.assertNotIn("Free: you draw a new seven.", texts)
        self.assertIn("then put 1 on the bottom", texts)
        advice = gui.advisory()[0][0]
        self.assertNotIn("free:", advice.lower().replace("no free", ""))
        self.assertIn("puts 1 card on the bottom", advice)

    def test_the_bar_words_and_button_do_not_say_free(self):
        gui = gui_for("standard")
        head, hint, _ = ft.prompt_view(gui.prompt()["message"], "Karl", gui.mulligan_cost())
        self.assertNotIn("free", hint)
        self.assertIn("puts 1 on the bottom", hint)
        labels = []
        real = ft.ForgeTable.draw_button
        with mock.patch.object(ft.ForgeTable, "draw_button", autospec=True,
                               side_effect=lambda self_, r, label, *a, **k: (labels.append(label), real(self_, r, label, *a, **k))[1]):
            gui.flow_hidden = gui.flow_key()       # "View table": the action bar shows
            frame(gui)
        self.assertNotIn("Mulligan (free)", labels)

    def test_return_one_card_after_one_mulligan_counts_one(self):
        gui = gui_for("standard", message="Return 1 card(s) to the bottom of your library",
                      ok={"label": "OK", "enabled": False, "focus": True})
        self.assertEqual(gui.mulligans, 1)
        self.assertEqual(gui.mulligan_cost(), 2)


class FreeFirstMulliganStaysTests(unittest.TestCase):
    def test_commander_keeps_its_free_first_mulligan(self):
        gui = gui_for("commander")
        self.assertTrue(gui.first_mulligan_free())
        self.assertIn("Free: you draw a new seven.", drawn_texts(gui))
        self.assertIn("first mulligan is free", gui.advisory()[0][0])

    def test_brawl_and_commander_draft_are_free(self):
        for fmt in ("brawl", "commander_draft"):
            self.assertTrue(gui_for(fmt).first_mulligan_free(), fmt)

    def test_a_three_player_standard_game_is_free(self):
        gui = gui_for("standard", players=3)
        self.assertTrue(gui.first_mulligan_free())
        self.assertEqual(gui.mulligan_cost(), 0)

    def test_a_two_player_draft_game_is_not(self):
        self.assertFalse(gui_for("draft").first_mulligan_free())

    def test_commander_return_counts_as_before(self):
        gui = gui_for("commander", message="Return 2 card(s) to the bottom of your library",
                      ok={"label": "OK", "enabled": False, "focus": True})
        self.assertEqual(gui.mulligans, 3)


if __name__ == "__main__":
    unittest.main()
