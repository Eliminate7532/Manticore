# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AI1 (rebuilt 7 Oct 2026): the AI interaction benchmark, ai_bench.py and tools/ai_bench.py.

Everything but the last class runs without Forge: the boards (only Kinnan-list cards on my side, every zone written in the
set-up), the verdicts on hand-made trials, the turn read from Forge's prompt text, the Watcher on a stand-in session, the
summary and the files, and the tool's own folder rule. The live class plays board C1 once and expects the counter (the doc's
20 of 20 on 3 Oct; the smoke runs of the rebuild: 6 of 6).
"""
import collections
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)                 # the project's ai_bench.py (the boards) is the one `import ai_bench` must find

import ai_bench
import deck_loader
from tests import live


def load_tool():
    """tools/ai_bench.py, under its own name (it shares the module's file name)."""
    spec = importlib.util.spec_from_file_location("ai_bench_tool", os.path.join(BASE, "tools", "ai_bench.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

ME, AI = 0, 1


def trial(board="C1", ai_cast=(), zones=None, at_my_turn=None):
    t = ai_bench.Trial(ai_bench.BOARD_BY_KEY[board], 0)
    t.ai_cast = list(ai_cast)
    t.zones = dict(zones or {})
    t.at_my_turn = at_my_turn
    return t


class BoardTests(unittest.TestCase):
    def test_there_are_ten_boards_with_the_keys_of_the_round_doc(self):
        self.assertEqual([b.key for b in ai_bench.BOARDS], ["C1", "C2", "C3", "C4", "F1", "F2", "R1", "R2", "R3", "I1"])
        self.assertEqual(set(ai_bench.BOARD_BY_KEY), set(b.key for b in ai_bench.BOARDS))

    def test_every_card_on_my_side_is_in_the_kinnan_list(self):
        commanders, deck = deck_loader.load_deck(ai_bench.KINNAN_DECK)
        names = set(commanders) | set(deck)
        for b in ai_bench.BOARDS:
            for name in b.my_cards() | set(ai_bench.MY_LANDS):
                self.assertIn(name, names, "%s: %s is not in the Kinnan list" % (b.key, name))

    def test_the_ai_side_is_plain_interaction_and_its_lands(self):
        for b in ai_bench.BOARDS:
            self.assertEqual(len(b.ai_hand), 1, b.key)
            self.assertIn(b.ai_hand[0], ai_bench.COUNTERS + ai_bench.REMOVAL, b.key)
            self.assertTrue(all(c in ("Island", "Plains", "Mountain") for c in b.ai_bf), b.key)

    def test_the_setup_writes_every_zone_and_the_bench_turn(self):
        for b in ai_bench.BOARDS:
            lines = b.setup_lines()
            keys = [ln.split("=", 1)[0] for ln in lines]
            for key in ("humanhand", "humanbattlefield", "humanlibrary", "humangraveyard", "humanexile",
                        "aihand", "aibattlefield", "ailibrary", "aigraveyard", "aiexile"):
                self.assertIn(key, keys, "%s: %s not written (a zone left out keeps the last trial's cards)" % (b.key, key))
            self.assertIn("turn=%d" % ai_bench.SETUP_TURN, lines, b.key)
            self.assertIn("activeplayer=" + ("ai" if b.key.startswith("R") else "human"), lines, b.key)
            self.assertIn("removesummoningsickness=true", lines, b.key)
            bf = next(ln for ln in lines if ln.startswith("humanbattlefield=")).split("=", 1)[1].split(";")
            self.assertEqual(bf, b.mine_bf + ai_bench.MY_LANDS, b.key)

    def test_the_setup_never_touches_the_command_zone(self):
        for b in ai_bench.BOARDS:
            self.assertFalse(any(ln.startswith(("humancommand", "aicommand")) for ln in b.setup_lines()), b.key)

    def test_my_turn_boards_play_their_hand_and_the_ai_turn_boards_play_nothing(self):
        for b in ai_bench.BOARDS:
            if b.active == "ai":
                self.assertEqual(b.plays, [], b.key)
                self.assertEqual(b.mine_hand, [], b.key)
            else:
                self.assertEqual(b.plays, b.mine_hand, b.key)


class VerdictTests(unittest.TestCase):
    def test_countered_needs_the_counter_cast_and_the_spell_in_my_graveyard(self):
        j = ai_bench.countered("Basalt Monolith")
        self.assertTrue(j(trial(ai_cast=["Counterspell"], zones={"Basalt Monolith": "my graveyard"})))
        self.assertFalse(j(trial(ai_cast=[], zones={"Basalt Monolith": "my graveyard"})), "no counter cast: the spell fizzled some other way")
        self.assertFalse(j(trial(ai_cast=["Counterspell"], zones={"Basalt Monolith": "my battlefield"})), "the counter missed")
        self.assertTrue(j(trial(ai_cast=["Force of Will"], zones={"Basalt Monolith": "my graveyard"})))

    def test_resolved_is_the_spell_on_my_battlefield(self):
        j = ai_bench.resolved("Arcane Signet")
        self.assertTrue(j(trial(zones={"Arcane Signet": "my battlefield"})))
        self.assertFalse(j(trial(zones={"Arcane Signet": "my graveyard"})))
        self.assertFalse(j(trial(zones={})))

    def test_both_needs_every_judge(self):
        j = ai_bench.both(ai_bench.resolved("Arcane Signet"), ai_bench.countered("Basalt Monolith"))
        self.assertTrue(j(trial(ai_cast=["Counterspell"], zones={"Arcane Signet": "my battlefield", "Basalt Monolith": "my graveyard"})))
        self.assertFalse(j(trial(ai_cast=["Counterspell"], zones={"Arcane Signet": "my graveyard", "Basalt Monolith": "my battlefield"})),
                         "the Signet was countered and Basalt resolved: the C4 miss")

    def test_gone_by_my_turn_reads_the_board_at_my_turn_not_the_end(self):
        j = ai_bench.gone_by_my_turn("Kinnan, Bonder Prodigy")
        self.assertTrue(j(trial("R1", at_my_turn=["Basalt Monolith", "Forest"])))
        self.assertFalse(j(trial("R1", ai_cast=["Swords to Plowshares"], zones={"Kinnan, Bonder Prodigy": "my exile"},
                                at_my_turn=["Kinnan, Bonder Prodigy", "Basalt Monolith"])),
                         "Swords in my upkeep is too late: Kinnan was there when my turn began")
        self.assertIsNone(j(trial("R1")), "no snapshot of my turn: the trial can't be judged")

    def test_gone_by_my_turn_with_keep_wants_the_other_creature_still_there(self):
        j = ai_bench.gone_by_my_turn("Kinnan, Bonder Prodigy", keep="Consecrated Sphinx")
        self.assertTrue(j(trial("R2", at_my_turn=["Consecrated Sphinx", "Basalt Monolith"])))
        self.assertFalse(j(trial("R2", at_my_turn=["Kinnan, Bonder Prodigy", "Basalt Monolith"])), "the Sphinx was exiled instead")
        self.assertFalse(j(trial("R2", at_my_turn=["Basalt Monolith"])), "both gone is not the right answer either")

    def test_exiled_this_turn_needs_the_removal_cast_and_the_creature_off_my_battlefield(self):
        j = ai_bench.exiled_this_turn("Kinnan, Bonder Prodigy")
        self.assertTrue(j(trial("I1", ai_cast=["Swords to Plowshares"], zones={"Kinnan, Bonder Prodigy": "my exile"})))
        self.assertFalse(j(trial("I1", ai_cast=[], zones={"Kinnan, Bonder Prodigy": "my battlefield"})))
        self.assertFalse(j(trial("I1", ai_cast=["Swords to Plowshares"], zones={"Kinnan, Bonder Prodigy": "my battlefield"})), "cast, but at something else")

    def test_every_board_has_a_judge_that_answers_a_hand_made_trial(self):
        for b in ai_bench.BOARDS:
            self.assertIn(b.judge(trial(b.key)), (True, False, None), b.key)

    def test_verdict_word(self):
        t = trial(); t.verdict = True
        self.assertEqual(ai_bench.verdict_word(t), "yes")
        t.verdict = False
        self.assertEqual(ai_bench.verdict_word(t), "NO")
        t.verdict = None
        self.assertEqual(ai_bench.verdict_word(t), "?")
        t.error = "the board could not be set up"
        self.assertEqual(ai_bench.verdict_word(t), "error")


class PromptTurnTests(unittest.TestCase):
    def test_the_turn_and_whose_it_is_come_from_forges_prompt(self):
        st = {"asking": True, "prompt": {"message": "Priority: Checker\nTurn: 5 (AI 1 (Filler))\nPhase: Declare Attackers"}}
        self.assertEqual(ai_bench.prompt_turn(st), (5, "AI 1 (Filler)"))
        st["prompt"]["message"] = "Priority: Checker\nTurn: 6 (Checker)\nPhase: Main 1"
        self.assertEqual(ai_bench.prompt_turn(st), (6, "Checker"))

    def test_a_question_forge_is_not_asking_now_is_stale_and_ignored(self):
        st = {"asking": False, "prompt": {"message": "Priority: Checker\nTurn: 7 (Checker)\nPhase: Main 1"}}
        self.assertIsNone(ai_bench.prompt_turn(st))
        self.assertIsNone(ai_bench.prompt_turn({"asking": True, "prompt": {"message": "Waiting for AI 1 (Filler)..."}}))
        self.assertIsNone(ai_bench.prompt_turn(None))

    def test_an_old_bridge_without_asking_is_trusted(self):
        st = {"prompt": {"message": "Priority: Checker\nTurn: 5 (Checker)\nPhase: Main 1"}}
        self.assertEqual(ai_bench.prompt_turn(st), (5, "Checker"))


class FakeSession:
    """A stand-in ForgeSession for the Watcher: poll() takes the next scripted state (and events)."""

    def __init__(self, script):
        self.script = collections.deque(script)
        self.state = None
        self.events = collections.deque()
        self._cards = {}

    def poll(self):
        if self.script:
            st, events = self.script.popleft()
            self.state = st
            for ev in events:
                self.events.append((ev, 0.0))
        return True

    def card(self, cid):
        return self._cards.get(cid)


def state(turn, whose, stack=(), asking=True, my_bf=("Kinnan, Bonder Prodigy", "Forest"), ai_cards=()):
    return {"asking": asking, "me": ME, "turn": 99, "activePlayer": 99,       # the snapshot's own fields are not to be trusted after a set-up
            "prompt": {"message": "Priority: Checker\nTurn: %d (%s)\nPhase: Main 1" % (turn, whose), "ok": {"enabled": True}},
            "players": [{"id": ME, "name": "Checker", "zones": {"battlefield": [{"id": 100 + i, "name": n} for i, n in enumerate(my_bf)]}},
                        {"id": AI, "name": "AI 1 (Filler)", "zones": {"battlefield": [], "graveyard": [{"id": c[0], "name": c[1]} for c in ai_cards]}}],
            "stack": list(stack)}


def entry(cid, name, controller, text, targets=()):
    return {"card": {"id": cid, "name": name, "controller": controller}, "activator": controller, "key": text, "targets": list(targets)}


class WatcherTests(unittest.TestCase):
    def test_the_ais_stack_entries_are_kept_once_with_their_targets_named(self):
        basalt = entry(203, "Basalt Monolith", ME, "Basalt Monolith (203): Artifact")
        counter = entry(230, "Counterspell", AI, "Counter target spell.", targets=["c203"])
        s = FakeSession([(state(5, "Checker", stack=[basalt]), []), (state(5, "Checker", stack=[counter, basalt]), []),
                         (state(5, "Checker", stack=[counter, basalt]), []), (state(5, "Checker"), [])])
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["C1"], 0)
        with ai_bench.Watcher(s, t, ME, AI, "Checker"):
            for _ in range(4):
                s.poll()
        self.assertEqual(t.ai_entries, ["Counterspell (230): Counter target spell. [targets: Basalt Monolith (203)]"])
        self.assertEqual(t.my_entries, ["Basalt Monolith (203): Artifact"])

    def test_a_cast_event_from_the_hidden_hand_is_named_from_the_stack_when_the_trial_ends(self):
        counter = entry(230, "Counterspell", AI, "Counter target spell.")
        cast = {"t": "event", "seq": 12, "kind": "cast", "card": 230, "player": AI}
        s = FakeSession([(state(5, "Checker"), [cast]), (state(5, "Checker", stack=[counter]), []), (state(5, "Checker"), [])])
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["C1"], 0)
        with ai_bench.Watcher(s, t, ME, AI, "Checker"):
            for _ in range(3):
                s.poll()
        self.assertEqual(t.ai_cast, ["Counterspell"])

    def test_events_from_before_the_trial_and_the_ais_triggers_are_not_its_casts(self):
        old = {"t": "event", "seq": 3, "kind": "cast", "card": 50, "player": AI}
        trig = {"t": "event", "seq": 20, "kind": "cast", "card": 60, "player": AI, "trigger": True}
        mine = {"t": "event", "seq": 21, "kind": "cast", "card": 70, "player": ME}
        s = FakeSession([(state(5, "Checker"), [trig, mine]), (state(5, "Checker"), [])])
        s.events.append((old, 0.0))                       # already there when the trial starts
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["C1"], 0)
        with ai_bench.Watcher(s, t, ME, AI, "Checker"):
            s.poll(); s.poll()
        self.assertEqual(t.ai_cast, [])

    def test_my_turn_is_read_only_after_a_question_on_the_setup_turn_was_seen(self):
        # the first snapshot after a set-up can still carry the question from before it: a stale "Turn: 7 (Checker)" must
        # not count as my next turn, and neither must the snapshot's own turn field
        s = FakeSession([(state(7, "Checker", my_bf=("Old Card",)), []),                 # stale, before the set-up's own question
                         (state(5, "AI 1 (Filler)"), []),                               # the set-up's turn: settled
                         (state(6, "Checker", my_bf=("Basalt Monolith", "Forest")), []),  # my turn begins
                         (state(6, "Checker", my_bf=("Forest",)), [])])                   # later: not the first question
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["R1"], 0)
        with ai_bench.Watcher(s, t, ME, AI, "Checker") as w:
            s.poll()
            self.assertIsNone(t.at_my_turn)
            self.assertIsNone(w.next_turn)
            s.poll(); s.poll()
            self.assertEqual(w.next_turn, (6, "Checker"))
            self.assertEqual(t.at_my_turn, ["Basalt Monolith", "Forest"])
            s.poll()
            self.assertEqual(t.at_my_turn, ["Basalt Monolith", "Forest"])

    def test_the_ais_next_turn_ends_the_watch_without_a_board_of_mine(self):
        s = FakeSession([(state(5, "Checker"), []), (state(6, "AI 1 (Filler)"), [])])
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["C1"], 0)
        with ai_bench.Watcher(s, t, ME, AI, "Checker") as w:
            s.poll(); s.poll()
        self.assertEqual(w.next_turn, (6, "AI 1 (Filler)"))
        self.assertIsNone(t.at_my_turn)

    def test_a_stale_question_is_not_a_turn(self):
        s = FakeSession([(state(5, "Checker"), []), (state(6, "Checker", asking=False), []), (state(6, "Checker"), [])])
        t = ai_bench.Trial(ai_bench.BOARD_BY_KEY["R1"], 0)
        with ai_bench.Watcher(s, t, ME, AI, "Checker") as w:
            s.poll(); s.poll()
            self.assertIsNone(w.next_turn)
            s.poll()
            self.assertEqual(w.next_turn, (6, "Checker"))


class FakeChecker:
    def __init__(self, zones):
        self.zones = zones

    def where_is(self, name):
        return list(self.zones.get(name, []))


class WhereTests(unittest.TestCase):
    def test_the_command_zones_are_left_out_while_the_card_is_somewhere_real(self):
        chk = FakeChecker({"Kinnan, Bonder Prodigy": ["my battlefield", "my command", "opponent's command"]})
        self.assertEqual(ai_bench.where(chk, "Kinnan, Bonder Prodigy"), "my battlefield")
        chk = FakeChecker({"Kinnan, Bonder Prodigy": ["my exile", "my command", "opponent's command"]})
        self.assertEqual(ai_bench.where(chk, "Kinnan, Bonder Prodigy"), "my exile")

    def test_only_the_command_zone_means_the_commander_is_home(self):
        chk = FakeChecker({"Kinnan, Bonder Prodigy": ["my command", "opponent's command"]})
        self.assertEqual(ai_bench.where(chk, "Kinnan, Bonder Prodigy"), "my command")
        self.assertEqual(ai_bench.where(chk, "Basalt Monolith"), "")

    def test_two_real_zones_are_both_named(self):
        chk = FakeChecker({"Forest": ["my battlefield", "my hand"]})
        self.assertEqual(ai_bench.where(chk, "Forest"), "my battlefield, my hand")


class SummaryTests(unittest.TestCase):
    def results(self):
        out = []
        for key, verdicts in (("C1", [True, True]), ("C3", [False, True]), ("R2", [None, False])):
            for i, v in enumerate(verdicts):
                t = trial(key, ai_cast=["Counterspell"] if key.startswith("C") else ["Swords to Plowshares"],
                          zones={"Arcane Signet": "my graveyard"} if key == "C3" else {},
                          at_my_turn=["Basalt Monolith"] if key == "R2" and v is not None else None)
                t.index, t.verdict = i, v
                t.ai_entries = ["Counterspell (1): Counter target spell."] if key.startswith("C") else []
                if v is None:
                    t.error = "my turn did not begin within 90 seconds"
                out.append(t)
        return out

    def test_the_score_counts_only_judged_trials(self):
        per, total = ai_bench.score(self.results())
        self.assertEqual(per, {"C1": (2, 2), "C3": (1, 2), "R2": (0, 1)})
        self.assertEqual(total, (3, 5))

    def test_the_summary_names_the_score_the_misses_and_the_errors(self):
        text = ai_bench.summary_text(self.results(), 7, "Manticore 0.28.52")
        self.assertIn("score 3/5", text)
        self.assertIn("seed 7  |  Manticore 0.28.52", text)
        self.assertIn("C1 ", text)
        self.assertIn("2/2", text)
        self.assertIn("C3 misses (1): what the AI did:", text)
        self.assertIn("trial 1: AI cast Counterspell; stack: Counterspell (1): Counter target spell.; ended: Arcane Signet in my graveyard", text)
        self.assertIn("R2 misses (1)", text)
        self.assertIn("my turn began with Basalt Monolith", text)
        self.assertIn("errors (1, not judged):", text)
        self.assertIn("R2 trial 1: my turn did not begin within 90 seconds", text)
        self.assertNotIn("F1 ", text, "a board that was not run is not listed")

    def test_write_results_writes_the_summary_and_every_trial_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "ai_bench_20261007_120000")
            text = ai_bench.write_results(folder, self.results(), 7, "v")
            with open(os.path.join(folder, "ai_bench_summary.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), text)
            with open(os.path.join(folder, "ai_bench_results.json"), encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["seed"], 7)
            self.assertEqual(len(data["trials"]), 6)
            self.assertEqual(data["trials"][0], {"board": "C1", "index": 0, "verdict": True,
                                                 "ai_entries": ["Counterspell (1): Counter target spell."], "ai_cast": ["Counterspell"],
                                                 "zones": {}, "at_my_turn": None, "stack_end": [], "error": "", "seconds": 0.0})

    def test_unknown_boards_are_refused_before_forge_starts(self):
        with self.assertRaises(ValueError) as cm:
            ai_bench.run(boards=["C1", "Z9"])
        self.assertIn("Z9", str(cm.exception))


class ToolTests(unittest.TestCase):
    def test_the_run_folder_is_named_by_the_time(self):
        tool = load_tool()
        folder = tool.run_folder("/x", datetime.datetime(2026, 10, 7, 12, 34, 56))
        self.assertEqual(folder, os.path.join("/x", "ai_bench_20261007_123456"))

    def test_the_tool_finds_the_boards_module_even_when_imported_under_the_modules_name(self):
        tool = load_tool()
        self.assertIs(tool._load(), ai_bench)
        self.assertTrue(hasattr(tool._load(), "BOARDS"))

    def test_the_tool_points_forge_client_at_its_own_folder_before_importing_it(self):
        # in a fresh process (here forge_client is long imported): the tool's folder, not the project, is where the engine
        # log would go - the same rule tools/soak.py follows (Round 28b)
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import sys, os; sys.path.insert(0, %r); sys.argv = ['ai_bench.py']; import ai_bench as tool; "
                    "tool.main(['--list', '--out', %r]); import forge_client; print('DATA_DIR=' + forge_client.DATA_DIR)"
                    % (os.path.join(BASE, "tools"), tmp))
            env = dict(os.environ)
            env.pop("MANTICORE_DATA_DIR", None)
            out = subprocess.run([sys.executable, "-c", code], cwd=BASE, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("C1  Kinnan out, you cast Basalt Monolith", out.stdout)
            data_dir = next(ln for ln in out.stdout.splitlines() if ln.startswith("DATA_DIR=")).split("=", 1)[1]
            self.assertTrue(os.path.abspath(data_dir).startswith(os.path.abspath(tmp)), data_dir)


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveTests(unittest.TestCase):
    def test_board_c1_the_ai_counters_basalt_monolith(self):
        results = ai_bench.run(trials=1, seed=7, boards=["C1"], say=lambda *_a: None)
        self.assertEqual(len(results), 1)
        t = results[0]
        self.assertEqual(t.error, "")
        self.assertIn("Counterspell", t.ai_cast)
        self.assertEqual(t.zones.get("Basalt Monolith"), "my graveyard")
        self.assertTrue(t.verdict)
        self.assertTrue(any("Counterspell" in e for e in t.ai_entries), t.ai_entries)


if __name__ == "__main__":
    unittest.main()
