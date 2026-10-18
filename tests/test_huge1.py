# SPDX-License-Identifier: GPL-3.0-or-later
"""Round HUGE1: a soak record too big to read back whole is judged from a cut-down copy instead of being skipped.

Found in the soak of 15-16 Oct (run_20261015_211150): games 18 and 96 were "huge_record ... not read back", so no rule ever ran
on them. Reading them line by line with every snapshot cut down (bridge_rules.load_stream_reduced) judged them in seconds. These
tests check that the cut-down copy gives exactly the findings and game health the whole record gives, on every recorded game in
tests/fixtures, and that tools/soak.py now uses it.
"""
import copy
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import bridge_rules as br                 # noqa: E402
import soak_report                         # noqa: E402

FIXTURES = os.path.join(BASE, "tests", "fixtures")
RECORDS = sorted(
    [os.path.join(FIXTURES, "bridge", f) for f in os.listdir(os.path.join(FIXTURES, "bridge")) if f.endswith(".jsonl.gz")] +
    [os.path.join(FIXTURES, "soak", f) for f in os.listdir(os.path.join(FIXTURES, "soak")) if f.endswith(".jsonl.gz")])


def engine_log_for(record):
    log = record.replace(".jsonl.gz", ".engine.log")
    return br.load_engine_log(log) if os.path.isfile(log) else []


def write_gz(path, messages):
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:   # "\n" on Windows too (18 Oct: +20 bytes there)
        for m in messages:
            f.write(json.dumps(m) + "\n")


def comparable(findings):
    return [(f["rule"], f["severity"], f["at"], f["detail"]) for f in findings]


def tmpdir(test):
    d = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, d, True)
    return d


def board_state(turn, tokens, me=1, exiled=True):
    """A snapshot with a big board: `tokens` creature tokens for player 2, a commander of mine in exile (or the command zone)."""
    return {"t": "state", "turn": turn, "phase": "MAIN1", "me": me, "asking": True, "input": "InputPassPriority",
            "inputSeq": turn * 10, "gameOver": False,
            "prompt": {"message": "Priority: Main phase", "ok": {"label": "OK", "enabled": True}},
            "players": [
                {"id": 1, "name": "Soak", "life": 40, "lost": False, "handCount": 3,
                 "zones": {"battlefield": [{"id": 5, "name": "Forest", "controller": 1, "isLand": True, "tapped": False,
                                            "text": "T: Add G." * 20}],
                           ("exile" if exiled else "command"): [{"id": 100, "name": "Veyran, Voice of Duality", "owner": 1,
                                                                  "controller": 1, "commander": True, "isCreature": True}],
                           "graveyard": [{"id": 6, "name": "Opt", "owner": 1}]}},
                {"id": 2, "name": "AI 1 (opp1)", "life": 40, "lost": False, "ai": True,
                 "zones": {"battlefield": [{"id": 1000 + i, "name": "Citizen Token", "controller": 2, "isCreature": True,
                                            "token": True, "power": 1, "toughness": 1, "text": "A 1/1 Citizen." * 10}
                                           for i in range(tokens)]}}]}


class EquivalenceTests(unittest.TestCase):
    def test_there_are_recorded_games_to_compare(self):
        self.assertGreaterEqual(len(RECORDS), 10, RECORDS)

    def test_every_recorded_game_gives_the_same_findings_cut_down(self):
        for path in RECORDS:
            with self.subTest(record=os.path.basename(path)):
                full = br.load_stream(path)
                cut, info = br.load_stream_reduced(path)
                self.assertEqual(len(cut), len(full))
                self.assertFalse(info["truncated"])
                log = engine_log_for(path)
                self.assertEqual(comparable(br.check_stream(cut, log)), comparable(br.check_stream(full, log)))

    def test_the_faulted_games_still_fail_cut_down(self):
        # The red fixtures must stay red: equal-and-empty would also pass the test above.
        for name, rules in (("spiteful_fault", {"bridge_check"}), ("cmdzone_fault", {"bridge_check", "commander_stranded"})):
            path = os.path.join(FIXTURES, "bridge", name + ".jsonl.gz")
            cut, _info = br.load_stream_reduced(path)
            failed = {f["rule"] for f in br.check_stream(cut, engine_log_for(path)) if f["severity"] == br.FAIL}
            self.assertTrue(rules <= failed, (name, failed))

    def test_every_recorded_game_gives_the_same_game_health_cut_down(self):
        for path in RECORDS:
            with self.subTest(record=os.path.basename(path)):
                full = soak_report.game_health(br.load_stream(path))
                cut = soak_report.game_health(br.load_stream_reduced(path)[0])
                self.assertEqual(cut, full)

    def test_every_recorded_snapshot_counts_the_same_boards_cut_down(self):
        # ai_loop compares each player's board (permanents, creatures) between activations (_boards).
        states = 0
        for path in RECORDS:
            full = [m for m in br.load_stream(path) if m.get("t") == "state"]
            cut = [m for m in br.load_stream_reduced(path)[0] if m.get("t") == "state"]
            for a, b in zip(full, cut):
                self.assertEqual(br._boards(b), br._boards(a), os.path.basename(path))
                states += 1
        self.assertGreater(states, 100)

    def test_a_planted_loop_is_found_in_the_cut_down_copy(self):
        d = tmpdir(self)
        path = os.path.join(d, "loop.jsonl.gz")
        msgs = []
        for _ in range(AI_REPEATS):
            msgs.append(board_state(9, 300))
            msgs.append({"t": "log", "entries": [{"type": "STACK_ADD", "text": "AI 1 (opp1) activated Grim Monolith (77)."}]})
        write_gz(path, msgs)
        full = br.check_stream(br.load_stream(path))
        cut = br.check_stream(br.load_stream_reduced(path)[0])
        self.assertEqual(comparable(cut), comparable(full))
        self.assertIn("ai_loop", {f["rule"] for f in cut})

    def test_a_stranded_commander_is_found_in_the_cut_down_copy(self):
        # My commander in exile, then a priority question with no command-zone question before it: commander_stranded.
        d = tmpdir(self)
        path = os.path.join(d, "stranded.jsonl.gz")
        before = board_state(4, 50)
        before["players"][0]["zones"]["exile"] = []
        before["players"][0]["zones"]["command"] = [{"id": 100, "name": "Veyran, Voice of Duality", "owner": 1,
                                                     "commander": True}]
        after = board_state(4, 50)
        after["inputSeq"] = before["inputSeq"] + 1
        write_gz(path, [before, after])
        full = br.check_stream(br.load_stream(path))
        cut = br.check_stream(br.load_stream_reduced(path)[0])
        self.assertEqual(comparable(cut), comparable(full))
        self.assertIn("commander_stranded", {f["rule"] for f in cut})


AI_REPEATS = br.AI_LOOP_LIMIT + 1


class ReduceTests(unittest.TestCase):
    def test_a_thousand_tokens_cost_a_list_of_shared_pointers(self):
        shared = {}
        big = board_state(57, 1000)
        cut = br.reduce_state(big, shared)
        cards = cut["players"][1]["zones"]["battlefield"]
        self.assertEqual(len(cards), 1000)
        self.assertEqual(len({id(c) for c in cards}), 1)            # one dict for every "creature controlled by 2"
        again = br.reduce_state(board_state(58, 1000), shared)
        self.assertIs(again["players"][1]["zones"]["battlefield"][0], cards[0])   # and across snapshots
        self.assertEqual(cards[0], {"isCreature": True, "controller": 2})         # two fields, not the card's eight

    def test_what_the_rules_read_is_kept_and_the_rest_is_not(self):
        cut = br.reduce_state(board_state(3, 2))
        for k in ("turn", "phase", "me", "asking", "input", "inputSeq", "gameOver"):
            self.assertIn(k, cut)
        self.assertEqual(cut["prompt"], {"message": "Priority: Main phase", "source": None})
        mine = cut["players"][0]
        self.assertEqual(mine["zones"]["exile"][0]["id"], 100)      # a commander, in any zone, with its id and owner
        self.assertEqual(mine["zones"]["exile"][0]["owner"], 1)
        self.assertTrue(mine["zones"]["exile"][0]["commander"])
        self.assertEqual(mine["zones"]["graveyard"], [])           # not a commander, not on the battlefield
        self.assertEqual(mine["zones"]["battlefield"], [{"isCreature": False, "controller": 1}])
        self.assertNotIn("text", json.dumps(cut))

    def test_a_card_without_a_controller_keeps_none(self):
        # _boards falls back to the player's own id when a card has no "controller"; the cut-down card must not invent one.
        s = board_state(3, 0)
        s["players"][0]["zones"]["battlefield"] = [{"id": 7, "name": "Sol Ring"}]
        cut = br.reduce_state(s)
        self.assertNotIn("controller", cut["players"][0]["zones"]["battlefield"][0])
        self.assertEqual(br._boards(cut), br._boards(s))

    def test_a_record_past_the_limit_is_counted_not_kept(self):
        d = tmpdir(self)
        path = os.path.join(d, "long.jsonl.gz")
        write_gz(path, [board_state(i, 3) for i in range(50)])
        cut, info = br.load_stream_reduced(path, limit=10)
        self.assertEqual(len(cut), 10)
        self.assertEqual(info, {"messages": 50, "kept": 10, "truncated": True, "error": None})


    def test_a_stream_cut_short_still_knows_how_the_game_ended(self):
        # Review of 16 Oct: with only a prefix kept, the reply and game_over past the cut made unanswered_request and engine_error
        # FAILs the whole record does not have. Past the limit the ends of the game are kept.
        d = tmpdir(self)
        path = os.path.join(d, "cut.jsonl.gz")
        msgs = [{"t": "request", "id": 7, "kind": "confirm", "message": "Keep your hand?"}]
        msgs += [board_state(i, 3, exiled=False) for i in range(20)]
        msgs += [{"t": "_sent", "time": 1.0, "cmd": {"c": "reply", "id": 7, "value": True}},
                 {"t": "game_over", "winner": "AI 1 (opp1)"}]
        write_gz(path, msgs)
        log = ["java.util.ConcurrentModificationException", "\tat forge.game.Game.copyLastState(Game.java:1)"]
        whole = comparable(br.check_stream(br.load_stream(path), log))
        cut, info = br.load_stream_reduced(path, limit=5)
        self.assertTrue(info["truncated"])
        self.assertEqual({(f[0], f[1]) for f in comparable(br.check_stream(cut, log))}, {(f[0], f[1]) for f in whole})
        self.assertNotIn("fail", {f[1] for f in whole})

    def test_a_record_that_ends_early_gives_what_it_has(self):
        d = tmpdir(self)
        path = os.path.join(d, "short.jsonl.gz")
        rows = []
        for i in range(60):                                   # random padding: compresses badly, so half a file is many lines
            row = board_state(i, 3)
            row["pad"] = os.urandom(2000).hex()
            rows.append(row)
        write_gz(path, rows)
        with open(path, "rb") as f:
            data = f.read()
        half = os.path.join(d, "half.jsonl.gz")
        with open(half, "wb") as f:
            f.write(data[: len(data) // 2])                       # a gzip file cut in the middle (a soak stopped hard)
        cut, info = br.load_stream_reduced(half)
        self.assertGreater(len(cut), 0)
        self.assertIn("ends early", info["error"])
        line = os.path.join(d, "line.jsonl")
        with open(line, "w", encoding="utf-8") as f:
            f.write(json.dumps(board_state(1, 3)) + "\n" + json.dumps(board_state(2, 3))[:50])
        cut, info = br.load_stream_reduced(line)
        self.assertEqual(len(cut), 1)
        self.assertEqual(info["messages"], 1)
        self.assertIn("ends early, after 1 messages", info["error"])

    def test_every_field_the_rules_read_from_a_snapshot_is_kept(self):
        # The "keep this list in step" comment, made a test: run the rules and the health over every recorded game with each
        # snapshot wrapped so that every key read from it is noted, then check reduce_state keeps every one of them.
        seen = {"state": set(), "prompt": set(), "player": set(), "card": set()}

        class Spy(dict):
            def __init__(self, data, level):
                super().__init__(data)
                self.level = level

            def get(self, k, default=None):
                seen[self.level].add(k)
                return super().get(k, default)

            def __getitem__(self, k):
                seen[self.level].add(k)
                return super().__getitem__(k)

            def __contains__(self, k):
                seen[self.level].add(k)
                return super().__contains__(k)

        def wrap(state):
            players = []
            for p in state.get("players") or []:
                zones = {z: [Spy(c, "card") for c in cards or []] for z, cards in (p.get("zones") or {}).items()}
                players.append(Spy(dict(p, zones=zones), "player"))
            return Spy(dict(state, prompt=Spy(state.get("prompt") or {}, "prompt"), players=players), "state")

        for path in RECORDS:
            msgs = [wrap(m) if m.get("t") == "state" else m for m in br.load_stream(path)]
            br.check_stream(msgs, engine_log_for(path))
            soak_report.game_health(msgs)
        self.assertTrue(seen["state"] and seen["player"] and seen["card"])
        self.assertLessEqual(seen["state"], set(br.STATE_KEEP) | {"prompt", "players"})
        self.assertLessEqual(seen["prompt"], {"message", "source"})
        self.assertLessEqual(seen["player"], set(br.PLAYER_KEEP) | {"zones"})
        self.assertLessEqual(seen["card"], {"id", "name", "owner", "controller", "commander", "isCreature"})

    def test_a_commander_back_in_the_command_zone_then_exiled_again(self):
        # Review of 16 Oct: no test pinned commanders in the command zone. Exiled, asked, returned, exiled again, then a priority
        # question with no new command-zone question: the whole record says commander_stranded, and so must the cut-down copy.
        def at(seq, zone, prompt="Priority: Main phase"):
            s = board_state(5, 10, exiled=False)
            mine = s["players"][0]["zones"]
            mine.pop("command", None)
            mine[zone] = [{"id": 100, "name": "Veyran, Voice of Duality", "owner": 1, "controller": 1, "commander": True}]
            s["inputSeq"], s["prompt"] = seq, {"message": prompt}
            return s
        stream = [at(1, "command"), at(2, "exile", "Return your commander to the command zone?"), at(3, "command"),
                  at(4, "exile"), at(5, "exile")]
        d = tmpdir(self)
        path = os.path.join(d, "twice.jsonl.gz")
        write_gz(path, stream)
        whole = br.check_stream(br.load_stream(path))
        cut = br.check_stream(br.load_stream_reduced(path)[0])
        self.assertIn("commander_stranded", {f["rule"] for f in whole})
        self.assertEqual(comparable(cut), comparable(whole))


class SoakTests(unittest.TestCase):
    def setUp(self):
        from tools import soak
        soak._load_modules()
        self.soak = soak

    def huge(self, messages):
        d = tmpdir(self)
        path = os.path.join(d, "record.jsonl.gz")
        write_gz(path, messages)
        return d, path

    def test_a_huge_record_with_a_failure_is_now_reported(self):
        # Before HUGE1 this game had only the huge_record warning: the planted self-check was never seen.
        d, path = self.huge([board_state(5, 200), {"t": "check", "q": "order", "rule": "wrong_count", "detail": "PLANTED"},
                             board_state(6, 200)])
        report = os.path.join(d, "r.zip")
        open(report, "wb").close()
        with mock.patch.object(self.soak, "RECORD_JUDGE_LIMIT", 1000), \
                mock.patch.object(self.soak.reporting, "build_report", return_value=report):
            result = self.soak.GameResult(0, 1, 2)
            session = mock.Mock(stderr_path=os.path.join(d, "none.log"), state={}, log=[], sent_all=[])
            self.soak._finish_report(result, session, path, d, d)
        rules = {(f["rule"], f["severity"]) for f in result.findings}
        self.assertIn(("huge_record", "warn"), rules)
        self.assertIn(("bridge_check", "fail"), rules)

    def test_the_warning_says_how_much_was_judged(self):
        d, path = self.huge([board_state(i, 3, exiled=False) for i in range(30)])
        with mock.patch.object(self.soak, "RECORD_JUDGE_LIMIT", 100), \
                mock.patch.object(br, "REDUCED_MESSAGE_LIMIT", 12):
            result = self.soak.GameResult(0, 1, 2)
            self.soak._finish_report(result, mock.Mock(stderr_path=os.path.join(d, "x.log")), path, d, d)
        detail = [f["detail"] for f in result.findings if f["rule"] == "huge_record"][0]
        self.assertIn("cut-down copy", detail)
        self.assertIn("past the first 12 of 30 messages only the ends of the game were kept", detail)

    def test_a_huge_games_health_is_counted_in_the_summary(self):
        # write_outputs used to give a huge game an empty health (0 seats): its line and the run's counts left it out.
        path = os.path.join(FIXTURES, "bridge", "spiteful_ok.jsonl.gz")
        whole = soak_report.game_health(self.soak._load_messages(path))
        with mock.patch.object(self.soak, "RECORD_JUDGE_LIMIT", 10):
            self.assertTrue(self.soak._too_big(path))
            cut = soak_report.game_health(self.soak._load_messages(path))
        self.assertEqual(cut, whole)
        self.assertGreater(cut["seats"], 0)

    def test_a_gzipped_record_is_measured_by_what_it_holds(self):
        # Game 18 (15-16 Oct): 36 MB gzipped, 1.2 GB inside. "Ten times the .gz" said 360 MB, under the limit, so the
        # end-of-run summary read the whole 1.2 GB back. A big board compresses far better than ten times.
        d = tmpdir(self)
        path = os.path.join(d, "record.jsonl.gz")
        write_gz(path, [board_state(i, 200) for i in range(20)])       # repetitive: compresses well over ten times
        inside = sum(len(json.dumps(board_state(i, 200))) + 1 for i in range(20))
        gz = os.path.getsize(path)
        self.assertGreater(inside, gz * 10)                              # the case the old estimate missed
        self.assertEqual(self.soak._gzip_inner_size(path), inside)
        with mock.patch.object(self.soak, "RECORD_JUDGE_LIMIT", gz * 10 + 1):
            self.assertTrue(self.soak._too_big(path))

    def test_the_summary_line_of_a_huge_game_is_filled_in(self):
        # Review of 16 Oct: the health test above never called write_outputs. This one writes the real summary.
        d = tmpdir(self)
        fixture = os.path.join(FIXTURES, "soak", "huge1_live_game002.jsonl.gz")
        path = os.path.join(d, "record.jsonl.gz")
        shutil.copy(fixture, path)
        whole = soak_report.game_health(br.load_stream(path))
        self.assertGreater(whole["lands"], 0)
        result = self.soak.GameResult(0, 1612, 2)
        result.record_path, result.turns, result.ended = path, 18, "game_over"
        with mock.patch.object(self.soak, "RECORD_JUDGE_LIMIT", 10):
            self.assertTrue(self.soak._too_big(path))
            self.soak.write_outputs(d, [result], {})
        with open(os.path.join(d, "soak_summary.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("bot: %d land(s), %d spell(s), %d answer(s)" % (whole["lands"], whole["spells"], whole["answered"]), text)

    def test_a_huge_record_that_cannot_be_read_keeps_its_warning(self):
        d, path = self.huge([board_state(i, 3, exiled=False) for i in range(5)])
        with mock.patch.object(self.soak, "RECORD_JUDGE_LIMIT", 100), \
                mock.patch.object(br, "load_stream_reduced", side_effect=OSError("disk gone")):
            result = self.soak.GameResult(0, 1, 2)
            self.soak._finish_report(result, mock.Mock(stderr_path=os.path.join(d, "x.log")), path, d, d)
        detail = [f["detail"] for f in result.findings if f["rule"] == "huge_record"]
        self.assertEqual(len(detail), 1)
        self.assertIn("could not be read", detail[0])

    def test_a_record_that_is_not_there_is_still_empty(self):
        self.assertEqual(self.soak._load_messages(None), [])
        self.assertEqual(self.soak._load_messages(os.path.join(tmpdir(self), "missing.jsonl.gz")), [])


if __name__ == "__main__":
    unittest.main()
