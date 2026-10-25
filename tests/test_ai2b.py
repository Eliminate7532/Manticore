# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AI2b: ai_bench's boards for 3 and 4 players (P1-P6). Offline: the set-up lines, the verdicts, the Watcher at a table of
three, one engine per table size, the summary's pod section, and card_check.Checker's filler AIs and its answer to "Who would you
like to start this game?". Live: one trial of P1 (an AI counters at a table of three), P3 (the fourth seat), P5 (another AI's
combo)."""
import collections
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_bench
import card_check
import forge_client as fc
from tests import live

ME, AI1, AI2, AI3 = 1, 2, 3, 4


class SetupTests(unittest.TestCase):
    def test_every_seat_gets_every_zone_and_the_set_up_names_the_active_seat(self):
        for b in ai_bench.POD_BOARDS:
            lines = b.setup_lines()
            self.assertIn("activeplayer=p%d" % b.active, lines, b.key)
            self.assertIn("turn=%d" % ai_bench.SETUP_TURN, lines, b.key)
            for i in range(b.players):
                for zone in ("life", "landsplayed", "hand", "battlefield", "library", "graveyard", "exile"):
                    self.assertEqual(sum(1 for ln in lines if ln.startswith("p%d%s=" % (i, zone))), 1, (b.key, i, zone))
            self.assertFalse([ln for ln in lines if ln.startswith(("human", "ai"))], b.key)     # never the 1v1 keys
            self.assertFalse([ln for ln in lines if ln.startswith("p%d" % b.players)], b.key)   # no seat beyond the table

    def test_the_tables_and_whose_turn(self):
        self.assertEqual([(b.key, b.players, b.active) for b in ai_bench.POD_BOARDS],
                         [("P1", 3, 0), ("P2", 3, 0), ("P3", 4, 0), ("P4", 3, 1), ("P5", 3, 2), ("P6", 3, 1),
                          ("P7", 3, 2)])                                                    # round AI2c: P7
        self.assertEqual([b.key for b in ai_bench.POD_BOARDS if b.until_mine], ["P4", "P5", "P6", "P7"])

    def test_the_1v1_boards_are_unchanged(self):
        for b in ai_bench.ALL_BOARDS:
            self.assertEqual(b.players, 2, b.key)
            self.assertFalse(b.until_mine, b.key)
            self.assertEqual(tuple(b.watch), (), b.key)
            self.assertTrue(b.setup_lines()[0].startswith("humanlife="), b.key)

    def test_every_ai_has_the_mana_for_its_spell(self):
        cost = {"Counterspell": ("Island", 2), "Swords to Plowshares": ("Plains", 1), ai_bench.BASALT: (None, 3)}
        for b in ai_bench.POD_BOARDS:
            for seat, (bf, hand, lands) in enumerate(b.seats[1:], 1):
                for card in hand:
                    colour, n = cost[card]
                    have = [ln for ln in lands if colour is None or ln == colour]
                    self.assertGreaterEqual(len(have), n, (b.key, seat, card))

    def test_my_spells_are_in_my_hand_and_paid_for(self):
        for b in ai_bench.POD_BOARDS:
            self.assertEqual(sorted(b.plays), sorted(b.mine_hand), b.key)
            if b.plays:
                self.assertEqual(b.seats[0][2], ai_bench.MY_LANDS, b.key)

    def test_board_keys(self):
        self.assertEqual(ai_bench.board_keys(["pod"]), ["P1", "P2", "P3", "P4", "P5", "P6", "P7"])   # round AI2c: P7
        self.assertEqual(len(ai_bench.board_keys(["all"])), 16)              # "all" is still the sixteen 1v1 boards
        self.assertEqual(ai_bench.board_keys(["c1", "p5"]), ["C1", "P5"])


def trial(board, **kw):
    t = ai_bench.Trial(ai_bench.BOARD_BY_KEY[board], 0)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class VerdictTests(unittest.TestCase):
    def test_p1_to_p3_count_any_ais_counter(self):
        for key in ("P1", "P2", "P3"):
            judge = ai_bench.BOARD_BY_KEY[key].judge
            self.assertTrue(judge(trial(key, ai_cast=["Counterspell"], zones={ai_bench.BASALT: "my graveyard"})), key)
            self.assertFalse(judge(trial(key, ai_cast=[], zones={ai_bench.BASALT: "my battlefield"})), key)

    def test_counters_spent(self):
        self.assertEqual(ai_bench.counters_cast(trial("P2", ai_cast=["Counterspell", "Counterspell", "Brainstorm"])), 2)
        self.assertEqual(ai_bench.counters_cast(trial("P2", ai_cast=[])), 0)

    def test_p5_ai_1_must_counter_ai_2s_basalt(self):
        judge = ai_bench.BOARD_BY_KEY["P5"].judge
        b = ai_bench.BASALT
        self.assertIsNone(judge(trial("P5", casts=[], zones={b: "opponent's hand"})))           # AI 2 never cast it
        self.assertTrue(judge(trial("P5", casts=["AI 2: Basalt Monolith", "AI 1: Counterspell"], zones={b: "opponent's graveyard"})))
        self.assertFalse(judge(trial("P5", casts=["AI 2: Basalt Monolith"], zones={b: "opponent's battlefield"})))
        self.assertFalse(judge(trial("P5", casts=["AI 3: Counterspell"], zones={b: "opponent's graveyard"})))  # not AI 1's counter
        self.assertFalse(judge(trial("P5", casts=["AI 1: Counterspell"], zones={b: "opponent's battlefield"})))

    def test_p4_my_kinnan_goes_and_ai_2s_sphinx_stays(self):
        judge = ai_bench.BOARD_BY_KEY["P4"].judge
        k, b, s = ai_bench.KINNAN, ai_bench.BASALT, ai_bench.SPHINX
        self.assertIsNone(judge(trial("P4")))
        self.assertTrue(judge(trial("P4", at_my_turn=[b], boards={"me": [b], "AI 2": [s, "Forest"]})))
        self.assertFalse(judge(trial("P4", at_my_turn=[k, b], boards={"me": [k, b], "AI 2": ["Forest"]})))   # the Sphinx went
        self.assertFalse(judge(trial("P4", at_my_turn=[b], boards={"me": [b], "AI 2": ["Forest"]})))

    def test_p6_ai_2s_combo_answered_and_my_sphinx_kept(self):
        judge = ai_bench.BOARD_BY_KEY["P6"].judge
        d, v, s = ai_bench.DRUID, ai_bench.VIZIER, ai_bench.SPHINX
        self.assertIsNone(judge(trial("P6", at_my_turn=[s])))                                     # no boards: not judged
        self.assertTrue(judge(trial("P6", at_my_turn=[s], boards={"AI 2": [v]})))
        self.assertTrue(judge(trial("P6", at_my_turn=[s], boards={"AI 2": [d]})))
        self.assertFalse(judge(trial("P6", at_my_turn=[s], boards={"AI 2": [d, v]})))
        self.assertFalse(judge(trial("P6", at_my_turn=[], boards={"AI 2": [d, v]})))              # my Sphinx went instead


class FakeSession:
    """poll() takes the next scripted (state, events)."""

    def __init__(self, script):
        self.script = collections.deque(script)
        self.state = None
        self.events = collections.deque()

    def poll(self):
        if self.script:
            st, events = self.script.popleft()
            self.state = st
            for ev in events:
                self.events.append((ev, 0.0))
        return True

    def card(self, _cid):
        return None


def pod_state(turn, whose, boards=None, stack=()):
    boards = boards or {}
    names = {ME: "Checker", AI1: "AI 1 (Filler)", AI2: "AI 2 (Filler)"}
    return {"asking": True, "me": ME,
            "prompt": {"message": "Priority: Checker\nTurn: %d (%s)\nPhase: Main 1" % (turn, whose), "ok": {"enabled": True}},
            "players": [{"id": pid, "name": names[pid],
                         "zones": {"battlefield": [{"id": pid * 100 + i, "name": n} for i, n in enumerate(boards.get(pid, []))]}}
                        for pid in (ME, AI1, AI2)],
            "stack": list(stack)}


class PodWatcherTests(unittest.TestCase):
    def test_on_an_ais_turn_the_watch_waits_for_my_turn_and_keeps_every_board(self):
        k, b, s = ai_bench.KINNAN, ai_bench.BASALT, ai_bench.SPHINX
        script = [(pod_state(5, "AI 1 (Filler)", {ME: [k, b], AI2: [s]}), []),
                  (pod_state(6, "AI 2 (Filler)", {ME: [b], AI2: [s]}), []),           # AI 2's turn: not yet
                  (pod_state(7, "Checker", {ME: [b], AI2: [s, "Forest"]}), [])]         # mine
        sess = FakeSession(script)
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["P4"], 0)
        with ai_bench.Watcher(sess, t, ME, [AI1, AI2], "Checker", until_mine=True) as w:
            sess.poll(); sess.poll()
            self.assertIsNone(w.next_turn)
            sess.poll()
        self.assertEqual(w.next_turn, (7, "Checker"))
        self.assertEqual(t.at_my_turn, [b])
        self.assertEqual(t.boards, {"me": [b], "AI 1": [], "AI 2": [s, "Forest"]})
        self.assertTrue(ai_bench.BOARD_BY_KEY["P4"].judge(t))

    def test_without_until_mine_the_next_turn_ends_it_as_in_1v1(self):
        sess = FakeSession([(pod_state(5, "Checker"), []), (pod_state(6, "AI 1 (Filler)"), [])])
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["P1"], 0)
        with ai_bench.Watcher(sess, t, ME, [AI1, AI2], "Checker") as w:
            sess.poll(); sess.poll()
        self.assertEqual(w.next_turn, (6, "AI 1 (Filler)"))

    def test_every_ais_casts_and_stack_entries_are_kept_and_named_by_seat(self):
        c1 = {"card": {"id": 230, "name": "Counterspell", "controller": AI1}, "activator": AI1, "key": "Counter target spell.",
              "targets": ["c203"]}
        c2 = {"card": {"id": 330, "name": "Counterspell", "controller": AI2}, "activator": AI2, "key": "Counter target spell.",
              "targets": ["c230"]}
        evs = [{"t": "event", "seq": 10, "kind": "cast", "card": 230, "player": AI1},
               {"t": "event", "seq": 11, "kind": "cast", "card": 330, "player": AI2},
               {"t": "event", "seq": 12, "kind": "cast", "card": 203, "player": ME}]
        sess = FakeSession([(pod_state(5, "Checker", stack=[c1]), evs[:1]), (pod_state(5, "Checker", stack=[c2, c1]), evs[1:]),
                            (pod_state(5, "Checker"), [])])
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["P2"], 0)
        with ai_bench.Watcher(sess, t, ME, [AI1, AI2], "Checker"):
            sess.poll(); sess.poll(); sess.poll()
        self.assertEqual(t.casts, ["AI 1: Counterspell", "AI 2: Counterspell"])
        self.assertEqual(t.ai_cast, ["Counterspell", "Counterspell"])
        self.assertEqual(len(t.ai_entries), 2)
        self.assertEqual(ai_bench.counters_cast(t), 2)

    def test_1v1_entries_still_say_the_ai(self):
        st = pod_state(5, "Checker")
        st["players"] = st["players"][:2]
        target = {"card": {"id": 230, "name": "Swords to Plowshares", "controller": AI1}, "activator": AI1,
                  "key": "Exile target creature.", "targets": ["p%d" % ME]}
        st["stack"] = [target]
        sess = FakeSession([(st, [])])
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["R1"], 0)
        with ai_bench.Watcher(sess, t, ME, AI1, "Checker"):
            sess.poll()
        self.assertEqual(t.ai_entries, ["Swords to Plowshares (230): Exile target creature. [targets: me]"])


class FakeChecker:
    made = []

    def __init__(self, deck, opponents=1, **kw):
        self.opponents = opponents
        self.boards = []
        FakeChecker.made.append(self)

    def start(self):
        return True

    def stop(self):
        pass


class RunTests(unittest.TestCase):
    def setUp(self):
        FakeChecker.made = []

    def run_with(self, keys):
        def fake_trial(chk, board, index, say=None):
            chk.boards.append(board.key)
            return ai_bench.Trial(board, index)
        with mock.patch.object(ai_bench.cc, "Checker", FakeChecker), mock.patch.object(ai_bench, "run_trial", fake_trial):
            return ai_bench.run(trials=1, seed=7, boards=keys, say=lambda *_a: None)

    def test_one_engine_per_table_size_in_the_order_asked(self):
        out = self.run_with(["C1", "C2", "P1", "P2", "P3", "P4"])
        self.assertEqual([(c.opponents, c.boards) for c in FakeChecker.made],
                         [(1, ["C1", "C2"]), (2, ["P1", "P2"]), (3, ["P3"]), (2, ["P4"])])
        self.assertEqual([t.board for t in out], ["C1", "C2", "P1", "P2", "P3", "P4"])

    def test_a_1v1_run_is_one_engine_with_one_ai_as_before(self):
        self.run_with(None)
        self.assertEqual([(c.opponents, len(c.boards)) for c in FakeChecker.made], [(1, 10)])


class StartSession:
    """A stand-in ForgeSession for Checker.start: 'Who would you like to start this game?' until a player is clicked."""
    made = []

    def __init__(self, mine, opps, **kw):
        self.opps = list(opps)
        self.clicked = []
        self.oks = 0
        self.requests = []
        self.exited = False
        self.fatal = None
        self.state = None
        self.stderr_path = None
        StartSession.made.append(self)

    def start(self):
        pass

    def poll(self):
        if not self.clicked:
            self.state = {"me": 1, "players": [{"id": 1}, {"id": 2}, {"id": 3}],
                          "prompt": {"message": "Who would you like to start this game?", "ok": {"enabled": False}}}
        else:
            self.state = {"me": 1, "players": [], "prompt": {"message": "Priority: Checker\nTurn: 1 (Checker)", "ok": {"enabled": True}}}

    def click_player(self, pid):
        self.clicked.append(pid)

    def ok(self):
        self.oks += 1

    def close(self):
        pass


class CheckerTests(unittest.TestCase):
    def test_the_filler_ais_and_who_starts(self):
        StartSession.made = []
        with mock.patch.object(fc, "ForgeSession", StartSession), mock.patch.object(fc, "sync_bridge", lambda *_a: None):
            chk = card_check.Checker(ai_bench.KINNAN_DECK, opponents=2, say=lambda *_a: None)
            self.addCleanup(chk.tmp.cleanup)
            self.assertTrue(chk.start())
        s = StartSession.made[0]
        self.assertEqual(len(s.opps), 2)
        self.assertEqual(len(set(s.opps)), 2, "each filler AI has its own deck file")
        self.assertEqual(s.clicked, [1], "I pick myself to start, once")
        self.assertEqual(s.oks, 0, "never an OK while OK is grey")

    def test_opponents_is_one_to_three(self):
        for asked, got in ((0, 1), (1, 1), (2, 2), (3, 3), (5, 3)):
            chk = card_check.Checker(ai_bench.KINNAN_DECK, opponents=asked)
            self.addCleanup(chk.tmp.cleanup)
            self.assertEqual(chk.opponents, got)


class SummaryTests(unittest.TestCase):
    def test_the_pod_section_names_who_cast_what(self):
        a = trial("P2", verdict=True, ai_cast=["Counterspell", "Counterspell"], casts=["AI 1: Counterspell", "AI 2: Counterspell"])
        b = trial("P5", verdict=None, casts=[], zones={ai_bench.BASALT: "opponent's hand"})
        c = trial("P4", verdict=False, casts=["AI 1: Swords to Plowshares"], at_my_turn=[ai_bench.KINNAN],
                  boards={"me": [ai_bench.KINNAN], "AI 1": ["Plains"], "AI 2": ["Forest"]})
        text = ai_bench.summary_text([a, b, c], 7)
        self.assertIn("pod tables - what each AI cast (per trial), and counters spent:", text)
        self.assertIn("P2: AI 1: Counterspell, AI 2: Counterspell (2)", text)
        self.assertIn("not judged (the spell to answer was never cast): P5 trial 1", text)
        self.assertIn("P4 misses (1)", text)
        self.assertIn("AI 1 had lands; AI 2 had lands", text)

    def test_a_1v1_summary_has_no_pod_section(self):
        text = ai_bench.summary_text([trial("C1", verdict=True, ai_cast=["Counterspell"], casts=["AI 1: Counterspell"])], 7)
        self.assertNotIn("pod tables", text)


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveTests(unittest.TestCase):
    def run_board(self, key, trials=1):
        results = ai_bench.run(trials=trials, seed=7, boards=[key], say=lambda *_a: None)
        self.assertEqual(len(results), trials)
        for t in results:
            self.assertEqual(t.error, "", (key, t.error))
        return results

    def test_p1_an_ai_counters_basalt_at_a_table_of_three(self):
        t = self.run_board("P1")[0]
        self.assertIn("AI 1: Counterspell", t.casts)
        self.assertEqual(t.zones.get(ai_bench.BASALT), "my graveyard")
        self.assertTrue(t.verdict)

    def test_p3_four_players_start_and_the_fourth_seat_answers(self):
        t = self.run_board("P3")[0]
        self.assertTrue(any(c.startswith("AI 3: ") for c in t.casts), t.casts)

    def test_p4_the_board_is_read_when_my_turn_begins(self):
        t = self.run_board("P4")[0]
        self.assertIsNotNone(t.boards)
        self.assertEqual(sorted(t.boards), ["AI 1", "AI 2", "me"])
        self.assertIsNotNone(t.verdict)


if __name__ == "__main__":
    unittest.main()
