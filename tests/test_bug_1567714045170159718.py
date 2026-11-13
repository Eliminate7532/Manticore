# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1567714045170159718 (Karl, 4 Nov 2026, 0.28.53.15, a 1v1 Standard game): the opening-hand screen said "Mulligan (free)" /
"Free: you draw a new seven", but the bridge's MulliganService (round CON1, CR 103.5c) makes the first mulligan free only in a
game with commanders or with more than two players. The table now says what the engine does."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import forge_table as ft
from tests.forge_fake import load_state
import tests.test_ad2b as ad2b
from tests.test_forge_table import frame, make_gui


def gui_for(fmt, players=2):
    st = copy.deepcopy(load_state("mulligan"))
    while len(st["players"]) < players:
        extra = copy.deepcopy(st["players"][-1])
        extra["id"] = max(p["id"] for p in st["players"]) + 1
        extra["name"] = f"AI {len(st['players'])}"
        st["players"].append(extra)
    gui = make_gui(st, log=False)
    gui.session.fmt = fmt
    frame(gui, 2)
    return gui


def texts(gui):
    return " ".join(ad2b.MulliganScreenTests.texts(ad2b.MulliganScreenTests(), gui))


class FreeMulliganTests(unittest.TestCase):
    def test_rule_matches_the_bridge(self):
        self.assertTrue(gui_for("commander").first_mulligan_free())
        self.assertTrue(gui_for("brawl").first_mulligan_free())
        self.assertFalse(gui_for("standard").first_mulligan_free())
        self.assertFalse(gui_for("draft").first_mulligan_free())
        self.assertTrue(gui_for("standard", players=3).first_mulligan_free())

    def test_two_player_standard_says_the_mulligan_costs_a_card(self):
        gui = gui_for("standard")
        self.assertEqual(gui.mulligan_cost(), 1)
        words = texts(gui)
        self.assertNotIn("Free", words)
        self.assertIn("You draw a new seven, then put 1 on the bottom.", words)
        self.assertNotIn("is free", gui.advisory()[0][0])
        self.assertIn("puts 1 card on the bottom", gui.advisory()[0][0])
        _h, hint, _ok = ft.prompt_view(gui.prompt().get("message", ""), "Karl", 0, free_first=False)
        self.assertNotIn("free", hint)
        self.assertIn("puts 1 on the bottom", hint)

    def test_two_player_standard_counts_mulligans_from_the_bottom_prompt(self):
        gui = gui_for("standard")
        gui.session.state["prompt"]["message"] = "Return 1 card(s) to the bottom of your library"
        gui.track_pregame()
        self.assertEqual(gui.mulligans, 1)          # one taken, it put one back
        self.assertEqual(gui.mulligan_cost(), 2)    # the next puts two back

    def test_commander_keeps_the_free_first_mulligan(self):
        gui = gui_for("commander")
        self.assertEqual(gui.mulligan_cost(), 0)
        self.assertIn("Free: you draw a new seven.", texts(gui))
        self.assertIn("first mulligan is free", gui.advisory()[0][0])
        gui.session.state["prompt"]["message"] = "Return 0 card(s) to the bottom of your library"
        gui.track_pregame()
        self.assertEqual((gui.mulligans, gui.mulligan_cost()), (1, 1))

    def test_bar_button_label(self):
        for fmt, want in (("commander", "Mulligan (free)"), ("standard", "Mulligan")):
            gui = gui_for(fmt)
            seen = []
            real = gui.draw_button

            def spy(rect, label, n, *a, **kw):
                if n == "cancel":
                    seen.append(label)
                return real(rect, label, n, *a, **kw)
            gui.draw_button = spy
            frame(gui)
            self.assertIn(want, seen, (fmt, seen))
            if fmt == "standard":
                self.assertNotIn("Mulligan (free)", seen)


if __name__ == "__main__":
    unittest.main()
