# SPDX-License-Identifier: GPL-3.0-or-later
"""
Round UNDO1: Undo goes back to an earlier moment of my turn, as far as its start, by rebuilding the game exactly (rewind.py).

Offline: the journal's new lines (points, drops, tracking) and its cut; the replay's fast, drop-aware mode against a scripted
stand-in engine; the point tracker; the labels; the table's Undo window, the swap and every way a rewind can fail.
Live (real Forge): a game played through the real table, rewound to the start of my turn and to a later moment, the boards
checked against the ones recorded; and a rewind whose recorded board is planted wrong keeps the running game.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import crashlog
import forge_client as fc
import forge_dialogs as dlg
import forge_table as ft
import journal
import last_session
import replay
import rewind
from tests import live
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import frame


# ---------------------------------------------------------------------------------------------------------------------------
# journal.py
# ---------------------------------------------------------------------------------------------------------------------------

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="undo1_j_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.j = journal.GameJournal(self.tmp)
        self.j.start(7, "Karl", {"player.dck": "x", "opponent1.dck": "y"}, "c", 1000.0)
        self.addCleanup(self.j.close)

    def test_old_readers_still_get_only_start_commands_and_end(self):
        self.j.command({"c": "ok", "at": 3}, 1001.0)
        self.j.mark_drop("ok", 3)
        self.j.point({"i": 1, "turn": 2, "seq": 5, "req": None, "log": 9, "phase": "MAIN1", "start": True, "sum": "ab"})
        self.j.command({"c": "card", "id": 9, "at": 5}, 1002.0)
        self.j.close()
        start, cmds, end = journal.read(self.j.path)
        self.assertEqual(start["seed"], 7)
        self.assertEqual(cmds, [{"c": "ok", "at": 3}, {"c": "card", "id": 9, "at": 5}])
        self.assertIsNone(end)
        self.assertEqual(journal.unfinished(self.tmp)[1], cmds)          # Resume sees the same game

    def test_read_full_has_drops_points_and_where_drops_are_known_from(self):
        self.j.command({"c": "ok", "at": 3}, 1001.0)
        self.assertEqual(self.j.mark_drop("ok", 3), 0)
        self.j.point({"i": 1, "turn": 2, "seq": 5, "req": None, "log": 9, "phase": "MAIN1", "start": True, "sum": "ab"})
        self.j.close()
        full = journal.read_full(self.j.path)
        self.assertEqual(full["drops"], {0})
        self.assertEqual(full["fast_from"], 0)
        self.assertEqual([p["i"] for p in full["points"]], [1])
        self.assertNotIn("t", {k for k in full["points"][0] if k == "t" and full["points"][0][k] != "point"})

    def test_a_drop_goes_to_the_newest_click_of_that_kind_and_question(self):
        self.j.command({"c": "card", "id": 1, "at": 4}, 1001.0)        # 0: applied (it started a mana ability)
        self.j.command({"c": "card", "id": 2, "at": 4}, 1001.1)        # 1: dropped "busy"
        self.j.command({"c": "ok", "at": 4}, 1001.2)                   # 2
        self.assertEqual(self.j.mark_drop("card", 4), 1)
        self.assertEqual(self.j.mark_drop("card", 4), 0)                # a second drop of that kind takes the next one back
        self.assertIsNone(self.j.mark_drop("card", 4))                  # nothing left to match
        self.assertIsNone(self.j.mark_drop("ok", 99))
        self.j.close()
        self.assertEqual(journal.read_full(self.j.path)["drops"], {0, 1})

    def test_unnumbered_commands_are_never_matched_to_a_drop(self):
        self.j.command({"c": "stops", "mine": True, "phases": []}, 1001.0)
        self.j.command({"c": "undo"}, 1001.1)
        self.assertIsNone(self.j.mark_drop("undo", None))
        self.assertEqual(self.j.recent, [])

    def test_cut_keeps_what_came_before_the_moment_and_sets_the_old_journal_aside(self):
        for k in range(6):
            self.j.command({"c": "ok", "at": k + 1}, 1001.0 + k)
        self.j.mark_drop("ok", 2)                                        # command 1
        self.j.mark_drop("ok", 5)                                        # command 4 - after the cut
        for i in (0, 2, 3, 5):
            self.j.point({"i": i, "turn": 3, "seq": i + 1, "req": None, "log": i, "phase": "MAIN1", "start": i == 0, "sum": str(i)})
        self.j.cut(3, 2000.0)
        self.assertEqual(self.j.count, 3)
        self.j.command({"c": "card", "id": 77, "at": 4}, 2001.0)
        self.j.close()
        full = journal.read_full(self.j.path)
        self.assertEqual(full["commands"], [{"c": "ok", "at": 1}, {"c": "ok", "at": 2}, {"c": "ok", "at": 3},
                                            {"c": "card", "id": 77, "at": 4}])
        self.assertEqual(full["drops"], {1})
        self.assertEqual([p["i"] for p in full["points"]], [0, 2])      # the moment itself (3) and later ones are gone
        self.assertEqual(full["start"]["seed"], 7)
        with open(os.path.join(self.tmp, journal.REWOUND), encoding="utf-8") as f:
            undone = [json.loads(line) for line in f]
        self.assertEqual(sum(1 for o in undone if o.get("t") == "cmd"), 6)       # the undone game, whole, for a bug report
        with open(self.j.path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual([o["i"] for o in lines if o.get("t") == "cmd"], [0, 1, 2, 3])

    def test_a_resumed_old_journal_is_fast_only_from_where_this_program_took_over(self):
        path = os.path.join(self.tmp, "old.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"t": "start", "seed": 1}) + "\n")
            for i in range(4):
                f.write(json.dumps({"t": "cmd", "i": i, "c": {"c": "ok", "at": i}}) + "\n")
            f.write(json.dumps({"t": "track", "i": 4}) + "\n")
        self.assertEqual(journal.read_full(path)["fast_from"], 4)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"t": "start", "seed": 1}) + "\n")
        self.assertIsNone(journal.read_full(path)["fast_from"])


# ---------------------------------------------------------------------------------------------------------------------------
# replay.py: the fast, drop-aware mode
# ---------------------------------------------------------------------------------------------------------------------------

class Scripted:
    """A stand-in engine with numbered questions. A click on the current question moves to the next one (or stays, for clicks
    listed in `stay`); a click for an old question is dropped "late"; clicks listed in `busy` are dropped "busy" the first time.
    `skip_after`: question numbers the engine leaves by itself once that many seconds have passed since they were asked."""

    def __init__(self, busy=(), stay=(), skip_after=None, requests_at=None):
        self.seq, self.version = 1, 1
        self.ready, self.exited, self.fatal = True, False, None
        self.requests, self.dropped, self.sent = [], [], []
        self.busy, self.stay = set(busy), set(stay)
        self.skip_after = skip_after or {}
        self.asked_at = time.time()
        self.requests_at = requests_at or {}
        self.state = self._state()

    def _state(self):
        return {"inputSeq": self.seq, "asking": True, "prompt": {"ok": {"enabled": True}, "cancel": {"enabled": True}},
                "players": [], "turn": 1, "phase": "MAIN1"}

    def _next(self):
        self.seq += 1
        self.version += 1
        self.asked_at = time.time()
        self.state = self._state()
        if self.seq in self.requests_at:
            self.requests.append({"id": self.requests_at[self.seq], "kind": "confirm"})

    def poll(self, limit=200):
        if self.seq in self.skip_after and time.time() - self.asked_at > self.skip_after[self.seq]:
            self._next()
        return 0

    @property
    def state_version(self):
        return self.version

    def send(self, **cmd):
        self.sent.append(dict(cmd))
        at = cmd.get("at")
        if at is not None and at != self.seq:
            self.dropped.append({"t": "dropped", "c": cmd.get("c"), "at": at, "now": self.seq})
            return True
        key = (cmd.get("c"), cmd.get("id"), at)
        if key in self.busy:
            self.busy.discard(key)
            self.dropped.append({"t": "dropped", "c": cmd.get("c"), "at": at, "reason": "busy", "now": self.seq})
            return True
        if key not in self.stay:
            self._next()
        return True


class FastReplayTests(unittest.TestCase):
    def run_replay(self, engine, commands, **kw):
        r = replay.Replayer(engine, commands, wait_limit=3, **kw)
        t0 = time.time()
        ok = r.run()
        return r, ok, time.time() - t0

    def test_dropped_clicks_are_left_out_and_the_rest_sent_as_soon_as_asked(self):
        cmds = [{"c": "ok", "at": 1}, {"c": "card", "id": 5, "at": 1}, {"c": "ok", "at": 2}, {"c": "ok", "at": 3}]
        engine = Scripted()
        r, ok, took = self.run_replay(engine, cmds, drops={1}, fast_from=0)
        self.assertTrue(ok, r.diverged)
        self.assertEqual(engine.sent, [cmds[0], cmds[2], cmds[3]])      # command 1 never reached the engine
        self.assertEqual(r.skipped, 1)
        self.assertEqual(engine.seq, 4)
        self.assertLess(took, 3 * replay.SETTLE)                         # the old way waits SETTLE before every click

    def test_without_the_new_arguments_the_replay_is_the_careful_old_one(self):
        cmds = [{"c": "ok", "at": 1}, {"c": "ok", "at": 2}, {"c": "ok", "at": 3}]
        engine = Scripted()
        r, ok, took = self.run_replay(engine, cmds)
        self.assertTrue(ok, r.diverged)
        self.assertGreaterEqual(took, 3 * replay.SETTLE)

    def test_a_busy_drop_is_sent_again(self):
        cmds = [{"c": "card", "id": 7, "at": 1}, {"c": "ok", "at": 2}]
        engine = Scripted(busy={("card", 7, 1)})
        r, ok, _ = self.run_replay(engine, cmds, fast_from=0)
        self.assertTrue(ok, r.diverged)
        self.assertEqual(r.resent, 1)
        self.assertEqual([c["c"] for c in engine.sent], ["card", "card", "ok"])

    def test_a_second_click_on_one_question_waits_as_before(self):
        cmds = [{"c": "card", "id": 1, "at": 1}, {"c": "card", "id": 2, "at": 1}]
        engine = Scripted(stay={("card", 1, 1), ("card", 2, 1)})
        r, ok, took = self.run_replay(engine, cmds, fast_from=0)
        self.assertTrue(ok, r.diverged)
        self.assertGreaterEqual(took, replay.SETTLE)

    def test_the_engine_moving_past_a_click_the_original_applied_is_a_divergence(self):
        cmds = [{"c": "ok", "at": 1}, {"c": "ok", "at": 2}]
        engine = Scripted(skip_after={2: 0.0})                           # question 2 ends by itself at once
        r, ok, _ = self.run_replay(engine, cmds, fast_from=0)
        self.assertFalse(ok)
        self.assertEqual(r.diverged[0], 1)
        self.assertIn("past question 2", r.diverged[1])

    def test_reach_stops_exactly_at_the_moment_or_says_why_not(self):
        engine = Scripted()
        r = replay.Replayer(engine, [{"c": "ok", "at": 1}], wait_limit=2, fast_from=0)
        self.assertTrue(r.run())
        self.assertTrue(r.reach(seq=2))
        engine2 = Scripted(skip_after={2: 0.0})
        r2 = replay.Replayer(engine2, [{"c": "ok", "at": 1}], wait_limit=2, fast_from=0)
        r2.run()
        self.assertFalse(r2.reach(seq=2))
        self.assertIn("past question 2", r2.diverged[1])

    def test_reach_a_request(self):
        engine = Scripted(requests_at={2: 41})
        r = replay.Replayer(engine, [{"c": "ok", "at": 1}], wait_limit=2, fast_from=0)
        self.assertTrue(r.run())
        self.assertTrue(r.reach(req=41))
        self.assertFalse(replay.Replayer(engine, [], wait_limit=0.3).reach(req=99))

    def test_cancel_stops_even_while_waiting(self):
        engine = Scripted()
        engine.ready = False                                            # the engine never starts
        r = replay.Replayer(engine, [{"c": "ok", "at": 1}], start_limit=30, cancel=lambda: True)
        with self.assertRaises(KeyboardInterrupt):
            r.run()



class BoardHashTests(unittest.TestCase):
    """rewind.board_hash: what a rebuilt board must match. Everything Forge decides; nothing that depends on timing."""

    def setUp(self):
        self.st = load_state("main1_lands")
        self.me = [p for p in self.st["players"] if p["id"] == self.st["me"]][0]
        self.h = rewind.board_hash(self.st)

    def changed(self, fn):
        st = copy.deepcopy(self.st)
        fn(st, [p for p in st["players"] if p["id"] == st["me"]][0])
        return rewind.board_hash(st)

    def test_timing_dependent_parts_do_not_count(self):
        def noise(st, me):
            st["prompt"]["message"] = "something else entirely"
            st["inputSeq"], st["eventSeq"], st["asking"] = 999, 4242, False
            for c in me["zones"]["battlefield"] + me["zones"]["hand"]:
                c["weak"], c["selectable"] = 1, True
        self.assertEqual(self.changed(noise), self.h)

    def test_what_forge_decides_counts(self):
        def life(st, me): me["life"] -= 1
        def hand(st, me): me["zones"]["hand"] = me["zones"]["hand"][1:]
        def tapped(st, me): me["zones"]["battlefield"][0]["tapped"] = not me["zones"]["battlefield"][0].get("tapped")
        def counters(st, me): me["zones"]["battlefield"][0]["counters"] = {"P1P1": 1}
        def damage(st, me): me["zones"]["battlefield"][0]["damage"] = 2
        def phase(st, me): st["phase"] = "MAIN2"
        def stack(st, me): st["stack"] = [{"card": {"id": 5, "name": "Sol Ring"}, "text": "x", "activator": 0, "targets": []}]
        for fn in (life, hand, tapped, counters, damage, phase, stack):
            with self.subTest(fn.__name__):
                self.assertNotEqual(self.changed(fn), self.h)

    def test_differences_say_what_moved(self):
        want = rewind.board_fingerprint(self.st)
        st = copy.deepcopy(self.st)
        [p for p in st["players"] if p["id"] == st["me"]][0]["life"] = 7
        diffs = rewind.board_differences(want, rewind.board_fingerprint(st))
        self.assertTrue(any("life was" in d and "rebuilt 7" in d for d in diffs), diffs)


# ---------------------------------------------------------------------------------------------------------------------------
# rewind.py: points and labels
# ---------------------------------------------------------------------------------------------------------------------------

def st(turn, seq, active=0, inp=rewind.PRIORITY_INPUT, asking=True, phase="MAIN1", life=40):
    return {"turn": turn, "phase": phase, "activePlayer": active, "me": 0, "inputSeq": seq, "asking": asking, "input": inp,
            "players": [{"id": 0, "name": "Karl", "life": life, "zones": {}}], "prompt": {"message": "Priority: Karl"}}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = rewind.Tracker()
        self.count = 0

    def see(self, state, requests=()):
        return self.t.see(state, list(requests), self.count, 10, lambda s: "h%d" % s["inputSeq"])

    def test_the_first_question_of_my_turn_is_its_start_then_each_priority(self):
        self.assertIsNone(self.see(st(1, 2, active=1)))                 # an opponent's turn
        p = self.see(st(2, 3, inp="InputConfirm"))                      # the first thing I'm asked in my turn, whatever it is
        self.assertTrue(p["start"])
        self.assertEqual((p["turn"], p["seq"], p["i"]), (2, 3, 0))
        self.count = 2
        self.assertIsNone(self.see(st(2, 3, inp="InputConfirm")))      # the same question again: nothing new
        p2 = self.see(st(2, 4))
        self.assertFalse(p2["start"])
        self.assertEqual(p2["i"], 2)
        self.assertIsNone(self.see(st(2, 5, inp="InputPayMana")))       # mid-action: a payment is not a moment to go back to
        self.assertIsNone(self.see(st(2, 6, asking=False)))             # Forge asks nothing
        self.assertIsNone(self.see(st(2, 7, active=1)))                 # an opponent's turn
        self.assertTrue(self.see(st(4, 8))["start"])                    # my next turn has its own start

    def test_a_request_can_be_the_start_but_not_a_later_moment(self):
        p = self.see(st(3, 9, inp=""), [{"id": 12}])
        self.assertTrue(p["start"])
        self.assertEqual((p["req"], p["seq"]), (12, None))
        self.assertIsNone(self.see(st(3, 9, inp=""), [{"id": 13}]))

    def test_no_points_before_the_game_or_after_it(self):
        self.assertIsNone(self.see(st(0, 1)))
        s = st(5, 2)
        s["gameOver"] = True
        self.assertIsNone(self.see(s))

    def test_reset_keeps_the_start_of_a_turn_already_recorded(self):
        self.t.reset(turn_started=6)
        self.assertFalse(self.see(st(6, 3))["start"])


class LabelTests(unittest.TestCase):
    def points(self):
        return [{"i": 3, "turn": 6, "log": 10, "phase": "MAIN1", "start": True, "seq": 1},
                {"i": 5, "turn": 6, "log": 12, "phase": "MAIN1", "start": False, "seq": 2},
                {"i": 8, "turn": 6, "log": 13, "phase": "COMBAT_DECLARE_ATTACKERS", "start": False, "seq": 3},
                {"i": 11, "turn": 6, "log": 15, "phase": "MAIN2", "start": False, "seq": 4}]

    def log(self):
        rows = [{"type": "PHASE", "text": "x"}] * 10
        rows += [{"type": "LAND", "text": "Karl played Forest (35)"}, {"type": "STACK_ADD", "text": "AI 1 cast Duress"},
                 {"type": "STACK_ADD", "text": "Karl cast Sol Ring"},
                 {"type": "COMBAT", "text": "Karl assigned Elf (12) to attack AI 1."}, {"type": "STACK_ADD", "text": "Karl triggered X"},
                 {"type": "STACK_ADD", "text": "Karl activated Grim Monolith (40)."}]
        return rows

    def test_newest_first_start_last_with_what_I_did(self):
        out = rewind.choices(self.points(), 14, self.log(), 16, "Karl")
        self.assertEqual([p["i"] for p, _ in out], [11, 8, 5, 3])
        self.assertEqual([lab for _, lab in out], ["Main 2: before you used Grim Monolith",
                                                    "Attackers: before you attacked",
                                                    "Main 1: before you cast Sol Ring",
                                                    "Start of your turn 6 (Main 1): before you played Forest"])

    def test_the_moment_I_am_at_now_and_older_turns_are_not_offered(self):
        pts = [{"i": 1, "turn": 4, "log": 0, "phase": "MAIN1", "start": True}] + self.points()
        self.assertEqual([p["i"] for p, _ in rewind.choices(pts, 11, self.log(), 16, "Karl")], [8, 5, 3])

    def test_in_my_turn_only_this_turns_moments_count(self):
        pts = [{"i": 1, "turn": 4, "log": 0, "phase": "MAIN1", "start": True}]
        self.assertEqual(rewind.choices(pts, 11, [], 0, "Karl", my_turn_now=6), [])           # my turn 6, nothing asked yet
        self.assertEqual([p["i"] for p, _ in rewind.choices(pts, 11, [], 0, "Karl", my_turn_now=None)], [1])   # an opponent's turn

    def test_without_a_log_entry_the_command_says_what_it_was(self):
        cmds = [{"c": "ok"}] * 20
        cmds[5] = {"c": "card", "id": 50}
        out = dict((p["i"], lab) for p, lab in rewind.choices(self.points(), 14, [], 16, "Karl", cmds, lambda cid: "Mox Opal"))
        self.assertEqual(out[5], "Main 1: before you clicked Mox Opal")
        self.assertEqual(out[8], "Attackers: before you passed priority")

    def test_at_most_nine_and_the_start_always_there(self):
        pts = [{"i": 0, "turn": 9, "log": 0, "phase": "UPKEEP", "start": True}]
        pts += [{"i": k, "turn": 9, "log": 0, "phase": "MAIN1", "start": False} for k in range(1, 20)]
        out = rewind.choices(pts, 25, [], 0, "Karl")
        self.assertEqual(len(out), rewind.MAX_CHOICES)
        self.assertTrue(out[-1][0]["start"])
        self.assertEqual(out[0][0]["i"], 19)

    def test_log_entries_dropped_from_the_session_give_a_plain_label(self):
        out = rewind.choices(self.points(), 14, self.log()[-2:], 16, "Karl")      # only the newest two entries are kept
        labels = dict((p["i"], lab) for p, lab in out)
        self.assertEqual(labels[11], "Main 2: before you used Grim Monolith")
        self.assertEqual(labels[5], "Main 1: before your next action")


# ---------------------------------------------------------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------------------------------------------------------

class TableSession(FakeSession):
    """FakeSession whose sends carry the question number and reach the table's hook (the journal), like the real one."""

    def send(self, **cmd):
        if cmd.get("c") in fc.NUMBERED and "at" not in cmd and (self.state or {}).get("inputSeq") is not None:
            cmd["at"] = self.state["inputSeq"]
        super().send(**cmd)
        if self.on_send is not None:
            self.on_send(cmd)
        return True


class Launcher:
    runtime = None


class FakeJob:
    kind = "rewind"

    def __init__(self, session, commands, point, label="", drops=None, fast_from=None, expected=None, **kw):
        FakeJob.made.append(self)
        self.session, self.commands, self.point, self.label = session, list(commands), point, label
        self.drops, self.fast_from, self.expected = drops, fast_from, expected
        self.done_count, self.total = 0, len(self.commands)
        self.finished, self.ok, self.diverged, self.error, self.mismatch = False, False, None, None, None
        self.cancelled, self.seconds = False, 1.0

    def start(self):
        return self


class Rebuilt(TableSession):
    closed = False

    def close(self):
        self.closed = True


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="undo1_t_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        base = load_state("main1_start")
        self.base = base
        self.session = TableSession(self.state(2, 5), load_log())
        self.gui = ft.ForgeTable(self.session, StubStore(), window_size=(1360, 840), launcher=Launcher(),
                                 saves_dir=os.path.join(self.tmp, "saves"))
        gui = self.gui                      # this table's journal, even when a test calls setUp() again (Windows can't delete an open file)
        self.addCleanup(lambda: gui.journal and gui.journal.close())
        self.gui.journal.start(11, "Karl", {"player.dck": "[metadata]\nName=Me\n", "opponent1.dck": "[metadata]\nName=AI\n"}, "c",
                               time.time(), replay=self.gui.replay_key())
        self.gui.load_rewind_points()
        FakeJob.made = []

    def state(self, turn, seq, active=0, inp=rewind.PRIORITY_INPUT, asking=True, pool=None):
        s = copy.deepcopy(self.base)
        s.update(turn=turn, activePlayer=active, inputSeq=seq, asking=asking, input=inp, phase="MAIN1")
        me = [p for p in s["players"] if p["id"] == s["me"]][0]
        me["manaPool"] = dict(pool or {})
        return s

    def show(self, s):
        self.session.handle(s)
        frame(self.gui)

    def play_a_turn(self):
        """Three moments in my turn 2, with a click after each of the first two."""
        self.session.click_card(1)                       # something before my turn (command 0)
        self.show(self.state(2, 6))                      # start of my turn, after 1 command
        self.session.click_card(2)                       # command 1
        self.show(self.state(2, 7, inp="InputPayManaOfCostPayment"))
        self.session.click_card(3)                       # command 2 (the payment)
        self.show(self.state(2, 8))                      # priority again: a moment, after 3 commands
        self.session.ok()                                # command 3
        self.show(self.state(2, 9))                      # the moment I'm at now

    def test_points_go_into_the_journal_and_the_table(self):
        self.play_a_turn()
        self.assertEqual([(p["i"], p["start"]) for p in self.gui.rewind_points], [(1, True), (3, False), (4, False)])
        full = journal.read_full(self.gui.journal.path)
        self.assertEqual([p["i"] for p in full["points"]], [1, 3, 4])
        self.assertEqual(full["points"][0]["sum"], self.gui.rewind_points[0]["sum"])

    def test_drops_reported_by_the_bridge_are_written_down(self):
        self.play_a_turn()
        frame(self.gui)
        self.session.handle({"t": "dropped", "c": "ok", "at": 8, "now": 9})
        frame(self.gui)
        self.assertEqual(journal.read_full(self.gui.journal.path)["drops"], {3})

    def test_undo_without_floating_mana_opens_the_window_and_starts_the_engine(self):
        self.play_a_turn()
        pre = (Rebuilt(), journal.read_full(self.gui.journal.path))
        with mock.patch.object(self.gui, "start_rebuild_session", return_value=pre) as start:
            self.gui.press_undo()
        self.assertIsInstance(self.gui.modal, dlg.RewindDialog)
        self.assertEqual(start.call_count, 1)
        self.assertEqual([p["i"] for p, _l in self.gui.modal.choices], [3, 1])     # newest first; "now" (4) not offered
        self.assertEqual(self.session.commands("undo"), [])
        self.gui.modal.choose(None)                                   # Keep playing
        self.assertTrue(pre[0].closed)
        self.assertIsNone(self.gui.rewind_prestart)

    def test_undo_with_floating_mana_asks_forge_first_then_offers_the_window(self):
        self.play_a_turn()
        self.show(self.state(2, 9, pool={"G": 1}))
        self.gui.press_undo()
        self.assertEqual(len(self.session.commands("undo")), 1)
        self.assertIsNone(self.gui.modal)
        with mock.patch.object(self.gui, "start_rebuild_session", return_value=(Rebuilt(), {})), \
                mock.patch.object(ft.time, "time", return_value=time.time() + ft.UNDO_WAIT + 1):
            frame(self.gui)                                           # Forge changed nothing
        self.assertIsInstance(self.gui.modal, dlg.RewindDialog)

    def later(self):
        with mock.patch.object(ft.time, "time", return_value=time.time() + ft.UNDO_WAIT + 1):
            frame(self.gui)

    def test_online_or_without_moments_it_says_so(self):
        self.gui.press_undo()                                         # no moment of my turn yet: Forge's undo, then a note
        self.assertIsNone(self.gui.modal)
        self.assertEqual(len(self.session.commands("undo")), 1)
        self.later()
        self.assertIsNone(self.gui.modal)
        self.assertEqual(self.gui.toast[0], ft.UNDO_NOTHING_YET)
        self.play_a_turn()
        self.show(self.state(3, 20, active=1))                        # an opponent's turn: my last turn's moments
        self.assertIsNone(self.gui.rewind_unavailable())
        self.show(self.state(4, 21))                                  # my next turn, nothing done in it yet: turn 2 isn't "this turn"
        self.assertEqual(self.gui.rewind_unavailable(), ft.UNDO_NOTHING_YET)
        self.session.online = "guest"
        self.gui.press_undo()
        self.assertIsNone(self.gui.modal)
        self.assertEqual(len(self.session.commands("undo")), 2)        # online: only Forge's own undo
        self.later()
        self.assertIsNone(self.gui.modal)
        self.assertEqual(self.gui.toast[0], ft.UNDO_ONLINE)

    def pick(self, index=0):
        self.play_a_turn()
        rebuilt = Rebuilt(self.state(2, 8))
        pre = (rebuilt, journal.read_full(self.gui.journal.path))
        with mock.patch.object(self.gui, "start_rebuild_session", return_value=pre), mock.patch.object(ft.grewind, "RewindJob", FakeJob):
            self.gui.press_undo()
            self.gui.modal.choose(index)
        return rebuilt, FakeJob.made[-1]

    def test_choosing_a_moment_rebuilds_up_to_it(self):
        rebuilt, job = self.pick(0)
        self.assertIs(self.gui.resuming, job)
        self.assertIs(job.session, rebuilt)
        self.assertEqual(job.point["i"], 3)
        self.assertEqual(len(job.commands), 3)
        self.assertEqual(job.fast_from, 0)
        self.assertEqual(job.expected, self.gui.rewind_expect[3])
        frame(self.gui)                                               # the progress screen, not the table
        self.assertIs(self.gui.session, self.session)                 # the running engine is untouched meanwhile

    def test_a_good_rebuild_takes_over_and_the_journal_goes_back(self):
        rebuilt, job = self.pick(0)
        job.ok, job.finished = True, True
        with mock.patch.object(self.gui, "play_cue") as cue, mock.patch.object(self.session, "close") as closed:
            frame(self.gui)
        self.assertIs(self.gui.session, rebuilt)
        closed.assert_called_once()
        cue.assert_any_call("rewind")
        self.assertIn("Rewound to", self.gui.toast[0])
        self.assertIsNone(self.gui.resuming)
        self.assertEqual(self.gui.journal.count, 3)
        self.assertEqual(len(journal.read_full(self.gui.journal.path)["commands"]), 3)
        self.assertEqual([p["i"] for p in self.gui.rewind_points if p["i"] < 3], [1])
        rebuilt.click_card(9)                                         # the game goes on in the journal from command 3
        self.assertEqual(journal.read_full(self.gui.journal.path)["commands"][-1], {"c": "card", "id": 9, "at": 8})

    def test_a_journal_that_cannot_be_cut_is_given_up_rather_than_left_wrong(self):
        rebuilt, job = self.pick(0)
        job.ok, job.finished = True, True
        with mock.patch.object(self.gui.journal, "cut", side_effect=OSError("locked")), mock.patch.object(ft.crashlog, "note"):
            frame(self.gui)
        self.assertIs(self.gui.session, rebuilt)
        self.assertFalse(self.gui.journal.active())
        self.assertIsNone(journal.unfinished(os.path.join(self.tmp, "saves")))          # Resume won't play the undone game back
        self.assertIsNotNone(self.gui.rewind_unavailable())

    def test_a_rebuild_that_does_not_match_keeps_the_running_game(self):
        for field, value, words in (("mismatch", "the rebuilt board is not the one you had", "not the one you had"),
                                    ("diverged", (2, "the engine never reached question 8"), "never reached"),
                                    ("error", "OSError: no java", "no java")):
            with self.subTest(field=field):
                self.setUp()
                rebuilt, job = self.pick(0)
                setattr(job, field, value)
                job.finished = True
                with mock.patch.object(ft.crashlog, "note") as note:
                    frame(self.gui)
                self.assertIs(self.gui.session, self.session)
                self.assertTrue(rebuilt.closed)
                self.assertIn(words, self.gui.toast[0])
                self.assertIn("Your game is as it was", self.gui.toast[0])
                note.assert_called_once()
                self.assertEqual(self.gui.journal.count, 4)            # nothing was cut

    def test_esc_cancels_and_keeps_the_game(self):
        rebuilt, job = self.pick(0)
        self.gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode=""))
        self.assertTrue(job.cancelled)
        job.error, job.finished = "cancelled", True
        frame(self.gui)
        self.assertIs(self.gui.session, self.session)
        self.assertTrue(rebuilt.closed)
        self.assertIn("cancelled", self.gui.toast[0])

    def test_closing_the_program_mid_rewind_closes_the_second_engine(self):
        rebuilt, job = self.pick(0)
        with mock.patch.object(ft.pygame, "quit"):
            self.gui.shutdown()
        self.assertTrue(rebuilt.closed)
        self.assertTrue(job.cancelled)

    def test_the_progress_screen_names_the_moment(self):
        rebuilt, job = self.pick(1)
        drawn = []
        real = ft.draw_text

        def spy(surf, text, *a, **k):
            drawn.append(text)
            return real(surf, text, *a, **k)
        with mock.patch.object(ft, "draw_text", spy):
            frame(self.gui)
        self.assertTrue(any(t.startswith("Rewinding to Start of your turn 2") for t in drawn), drawn[:5])
        self.assertTrue(any(t.startswith("Rebuilding action") for t in drawn))

    def test_the_rebuild_engine_writes_the_other_engine_log(self):
        self.play_a_turn()
        self.gui.launcher.runtime = None
        self.enterContext(mock.patch.object(ft, "DECK_DIR", os.path.join(self.tmp, "decks")))
        made = []

        class Started(Rebuilt):
            def __init__(self, deck, opps, **kw):
                super().__init__()
                made.append((deck, opps, kw))
                self.stderr_path = None

            def start(self):
                made.append(self.stderr_path)
                return self
        with mock.patch.object(ft.fc, "ForgeSession", Started):
            session, full = self.gui.start_rebuild_session()
        self.assertEqual(os.path.basename(made[-1]), "forge_engine.rewind.log")
        self.assertEqual(made[0][2]["seed"], 11)
        self.assertEqual(os.path.basename(fc.other_engine_log(made[-1])), "forge_engine.log")
        self.assertEqual(len(full["commands"]), 4)


class EngineLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="undo1_l_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(crashlog.set_engine_log, None)

    def write(self, name, text, age):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_reports_read_the_running_engines_log(self):
        self.write("forge_engine.log", "old engine\n", 10)
        live_log = self.write("forge_engine.rewind.log", "rebuilt engine\n", 100)       # older on disk, but it is the live one
        crashlog.set_engine_log(live_log)
        self.assertEqual(crashlog.engine_tail(self.tmp), ["rebuilt engine"])
        crashlog.set_engine_log(None)
        self.assertEqual(crashlog.engine_tail(self.tmp), ["old engine"])            # not told: the newer file

    def test_a_crashed_run_keeps_the_newer_of_the_two_logs(self):
        self.write("forge_engine.log", "first engine\n", 100)
        self.write("forge_engine.rewind.log", "engine after a rewind\n", 5)
        with open(last_session.marker_path(self.tmp), "w", encoding="utf-8") as f:
            json.dump({"pid": 1}, f)
        last_session.begin("v", folder=self.tmp)
        with open(os.path.join(self.tmp, last_session.CRASHED_ENGINE_LOG), encoding="utf-8") as f:
            self.assertEqual(f.read(), "engine after a rewind\n")


class KeptOutTests(unittest.TestCase):
    def test_the_second_engines_log_is_never_committed_or_backed_up(self):
        import backup
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            ignored = {line.strip() for line in f}
        for name in ("forge_engine.rewind.log", "forge_engine.rewind.prev.log"):
            self.assertIn(name, ignored)
            self.assertIn(name, backup.SKIP_FILES)
        self.assertEqual(os.path.basename(fc.previous_engine_log(os.path.join(ROOT, fc.REWIND_ENGINE_LOG))),
                         "forge_engine.rewind.prev.log")
        self.assertTrue(journal.REWOUND.endswith(".jsonl"))           # *.jsonl: ignored and skipped like every journal


# ---------------------------------------------------------------------------------------------------------------------------
# live: a real game through the real table
# ---------------------------------------------------------------------------------------------------------------------------

@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live Forge tests are switched off")
class LiveRewindTests(unittest.TestCase):
    """Play a real game through the table (the test bot clicks, the table journals and records the moments), then rewind."""

    def setUp(self):
        from deck_loader import load_deck
        from tests.forge_bot import Bot
        self.tmp = tempfile.mkdtemp(prefix="undo1_live_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(mock.patch.object(ft, "DECK_DIR", os.path.join(self.tmp, "decks")))      # not the program's forge_decks
        mine = load_deck(os.path.join(ROOT, "sample_decks", "stompy_goreclaw.txt"))
        opp = load_deck(os.path.join(ROOT, "sample_decks", "spellslinger_veyran.txt"))
        self.gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840),
                                 launcher=ft.Launcher(None, "Karl", seed=1234), saves_dir=os.path.join(self.tmp, "saves"))
        self.addCleanup(self.close)
        self.assertIsNone(self.gui.begin(mine, [opp]))
        self.bot = Bot(self.gui.session)

    def close(self):
        with mock.patch.object(ft.pygame, "quit"):
            self.gui.shutdown()

    def tick_until(self, cond, seconds, act=True):
        gui, t0 = self.gui, time.time()
        acted_at, acted_v, seen_v, last = 0.0, -1, -1, 0.0
        while time.time() - t0 < seconds:
            gui.tick(time.monotonic())
            s = gui.session
            if cond():
                return True
            if s.state_version != seen_v:
                seen_v, last = s.state_version, time.time()
            gui.vs = None
            if act and gui.resuming is None and gui.modal is not None and s.requests and \
                    not isinstance(gui.modal, dlg.RewindDialog):
                self.bot.s = s                                  # a question from Forge: the bot answers it, not the dialog
                self.bot.answer_requests()
                gui.modal = None
            elif act and gui.resuming is None and gui.modal is not None and not isinstance(gui.modal, dlg.RewindDialog):
                gui.modal = None                                # a list Forge showed (revealed cards): looked at, closed
            if act and gui.modal is None and gui.resuming is None:
                if (s.state_version != acted_v and time.time() - last > 0.3) or time.time() - acted_at > 1.5:
                    self.bot.s = s
                    n = len(s.sent_all)
                    self.bot.step()
                    if len(s.sent_all) != n:
                        acted_at, acted_v = time.time(), s.state_version
            time.sleep(0.02)
        return False

    def at_my_priority_with(self, n):
        st = self.gui.state or {}
        return (st.get("activePlayer") == st.get("me") and st.get("input") == rewind.PRIORITY_INPUT and st.get("asking")
                and len({p["turn"] for p in self.gui.rewind_points}) >= 2
                and len(rewind.latest_turn_points(self.gui.rewind_points, self.gui.journal.count)) >= n)

    def rewind_to(self, index):
        gui = self.gui
        gui.press_undo()
        self.assertIsInstance(gui.modal, dlg.RewindDialog, gui.toast)
        point, label = gui.modal.choices[index]
        old = gui.session
        gui.modal.choose(index)
        self.assertTrue(self.tick_until(lambda: gui.resuming is None, 300, act=False), "the rebuild never ended")
        return point, label, old

    def test_rewind_to_the_start_of_my_turn_and_to_a_later_moment(self):
        gui = self.gui
        self.assertTrue(self.tick_until(lambda: self.at_my_priority_with(3), 300), "never reached a turn of mine with 3 moments")
        point, label, old = self.rewind_to(-1)                          # the start of the turn: the last button
        self.assertTrue(point["start"])
        self.assertIsNot(gui.session, old, gui.toast)
        self.assertFalse(old.alive())
        self.assertEqual(rewind.board_hash(gui.state), point["sum"])
        self.assertEqual(gui.state.get("inputSeq"), point["seq"])
        self.assertEqual(gui.journal.count, point["i"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "saves", journal.REWOUND)))
        self.assertEqual(os.path.basename(gui.session.stderr_path), "forge_engine.rewind.log")
        # play on, then go back again - this time to the newest moment
        self.assertTrue(self.tick_until(lambda: self.at_my_priority_with(3), 300), "never got 3 moments again")
        point2, label2, old2 = self.rewind_to(0)
        self.assertIsNot(gui.session, old2, gui.toast)
        self.assertEqual(rewind.board_hash(gui.state), point2["sum"])
        self.assertEqual(os.path.basename(gui.session.stderr_path), "forge_engine.log")
        self.assertIn("Rewound to", gui.toast[0])

    def test_a_rebuild_that_does_not_match_the_recorded_board_changes_nothing(self):
        gui = self.gui
        self.assertTrue(self.tick_until(lambda: self.at_my_priority_with(2), 300), "never reached a turn of mine with 2 moments")
        before, count = rewind.board_hash(gui.state), gui.journal.count
        target = rewind.latest_turn_points(gui.rewind_points, count)[0]
        target["sum"] = "0" * 16                                        # a planted wrong record of that moment
        _point, _label, old = self.rewind_to(-1)
        self.assertIs(gui.session, old)
        self.assertTrue(old.alive())
        self.assertIn("Couldn't rewind exactly", gui.toast[0])
        self.assertEqual(rewind.board_hash(gui.state), before)
        self.assertEqual(gui.journal.count, count)


if __name__ == "__main__":
    unittest.main()
