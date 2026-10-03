# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/online_soak.py's checks that need no Forge: hidden cards, names the guest has seen, names in its log."""
import os
import tempfile
import unittest

from tools import online_soak as osk


def state(me_cards=(), opp_hand=(), opp_library=(), battlefield=()):
    return {"me": 1, "players": [
        {"id": 1, "name": "Guest", "zones": {"hand": list(me_cards), "battlefield": list(battlefield)}},
        {"id": 2, "name": "Host", "zones": {"hand": list(opp_hand), "library": list(opp_library)}}]}


HIDDEN = {"id": 9, "hidden": True, "name": "Face-down card"}


class HiddenTests(unittest.TestCase):
    def test_face_down_cards_are_fine(self):
        self.assertEqual(osk.hidden_leaks(state(opp_hand=[HIDDEN], opp_library=[dict(HIDDEN, id=10)])), [])

    def test_a_revealed_card_is_forges_call_not_a_leak(self):
        self.assertEqual(osk.hidden_leaks(state(opp_hand=[{"id": 9, "hidden": False, "name": "Sol Ring"}])), [])

    def test_a_hidden_card_with_a_name_or_text_is_a_leak(self):
        leaks = osk.hidden_leaks(state(opp_hand=[{"id": 9, "hidden": True, "name": "Sol Ring"}],
                                       opp_library=[{"id": 10, "hidden": True, "name": "Face-down card", "text": "Draw."}]))
        self.assertEqual(len(leaks), 2)
        self.assertIn("Sol Ring", leaks[0])

    def test_my_own_hand_is_never_checked(self):
        self.assertEqual(osk.hidden_leaks(state(me_cards=[{"id": 3, "hidden": True, "name": "Island"}])), [])


class NameTests(unittest.TestCase):
    def test_visible_names_skip_face_down_cards(self):
        seen = osk.visible_names(state(opp_hand=[HIDDEN], battlefield=[{"id": 4, "name": "Llanowar Elves"}]), set())
        self.assertIn("Llanowar Elves", seen)
        self.assertNotIn("Face-down card", seen)

    def test_a_name_the_guest_never_saw_in_its_log_is_reported(self):
        log = ["Host searched their library.", "Host cast Demonic Tutor.", "Host put Sol Ring into their hand."]
        found = osk.names_seen_in_log(log, {"Sol Ring", "Demonic Tutor", "Forest"}, {"Island"}, seen={"Demonic Tutor"})
        self.assertEqual([n for n, _l in found], ["Sol Ring"])

    def test_basics_its_own_cards_and_partial_words_are_ignored(self):
        log = ["Host played Forest.", "Guest cast Counterspell.", "Host's Sol Ringleader attacks."]
        self.assertEqual(osk.names_seen_in_log(log, {"Forest", "Counterspell", "Sol Ring"}, {"Counterspell"}, set()), [])

    def test_a_longer_name_of_the_guests_own_does_not_count(self):
        # online night 1, game 1: "Guest cast The Mind Stone" was reported as the host's "Mind Stone"
        log = ["Guest cast The Mind Stone", "Host cast Mind Stone"]
        found = osk.names_seen_in_log(log, {"Mind Stone"}, {"The Mind Stone"}, set())
        self.assertEqual(found, [("Mind Stone", "Host cast Mind Stone")])

    def test_a_name_inside_a_longer_known_name_is_not_a_leak(self):
        log = ["Guest cast The Mind Stone"]
        self.assertEqual(osk.names_seen_in_log(log, {"Mind Stone"}, {"The Mind Stone"}, set()), [])
        self.assertEqual(len(osk.names_seen_in_log(["Host searched for Mind Stone"], {"Mind Stone"}, {"The Mind Stone"}, set())), 1)

    def test_deck_names_read_a_dck(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.dck")
            with open(p, "w", encoding="utf-8") as f:
                f.write("[metadata]\nName=x\n[Commander]\n1 Kinnan, Bonder Prodigy|IKO\n[Main]\n1 Sol Ring\n10 Forest\n")
            self.assertEqual(osk.deck_names(p), {"Kinnan, Bonder Prodigy", "Sol Ring", "Forest"})


class SummaryTests(unittest.TestCase):
    def test_the_summary_groups_failures_and_lists_games(self):
        a = osk.OnlineResult(0, 5, ["a.txt", "b.txt"])
        a.ended, a.turns = "game_over", 12
        b = osk.OnlineResult(1, 6, ["c.txt", "d.txt"])
        b.ended = "stall"
        b.findings = [{"rule": "stall", "severity": "fail", "detail": "no new snapshot"},
                      {"rule": "online_name_seen", "severity": "warn", "detail": "'Sol Ring'"}]
        a.conceded = "guest"
        text = osk.summary_text([a, b], "2026-10-02 11:00", None, False)
        self.assertTrue(text.startswith("ONLINE SOAK: 2 game(s), 1 game_over, 1 stall"))
        self.assertIn("FAILURES (1 kind(s))", text)
        self.assertIn("  stall: 1 game(s)", text)
        self.assertIn("WARNINGS (1 kind(s))", text)
        self.assertIn("game 0: seed=5 decks=a+b turns=12 ended=game_over (guest conceded)", text)


class SoakClosedTests(unittest.TestCase):
    """Online night 1 (2 Oct), game 0: the soak ended a game at the turn cap, closed the guest first, and the host's game thread
    died on its next question - reported as an engine_error. It was the harness, not the game."""

    def lines(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "fixtures", "online", "turn_cap_close_engine.log"), encoding="utf-8") as f:
            return f.read().splitlines()

    def rules(self, lines):
        import bridge_rules
        return [f["rule"] for f in bridge_rules.check_stream([], lines) if f["severity"] == "fail"]

    def test_the_recorded_log_was_a_failure(self):
        self.assertIn("engine_error", self.rules(self.lines()))

    def test_after_a_turn_or_time_cap_the_close_is_not_judged(self):
        for ended in ("turn_cap", "time_cap"):
            self.assertEqual(self.rules(osk.before_soak_closed(self.lines(), ended)), [], ended)

    def test_a_guest_leaving_mid_game_is_still_judged(self):
        for ended in ("game_over", "peer_left", "stall"):
            self.assertIn("engine_error", self.rules(osk.before_soak_closed(self.lines(), ended)), ended)


if __name__ == "__main__":
    unittest.main()
