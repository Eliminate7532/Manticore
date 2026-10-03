# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28b: a simulation that catches bridge bugs.

Every rule and tool here has to be shown going RED on a known-bad input, not only staying green on
good input (docs/incoming/round27d's SONNET_SPEC_R28B.md section 3). The red cases are either one of
the fixtures Round 27d recorded WITH a fault already baked in (tests/fixtures/bridge/*_fault), or a
"red twin" made here by editing a good fixture's already-loaded messages - never by editing the
fixture files themselves (tests/fixtures/bridge/README.md: "Don't edit them").

All of this runs offline (no Java, no Forge): bridge_rules.py, soak_bot.py and tools/coverage_report.py
are pure Python over recorded messages, and the soak runner is driven here against a stand-in bridge
instead of the real one (tests.fake_bridge / a small scripted stream of our own).
"""
import copy
import gzip
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import bridge_rules as br
import card_check
import coverage_report as cov
import soak
from soak_bot import SoakBot

FIXTURES = os.path.join(BASE, "tests", "fixtures", "bridge")
FAKE_BRIDGE = os.path.join(BASE, "tests", "fake_bridge.py")

GOOD = ("spiteful_ok", "cmdzone_yes", "cmdzone_no", "surveil_ok")
FAULTED = {"spiteful_fault": {"bridge_check"}, "cmdzone_fault": {"bridge_check", "commander_stranded"}}
ALL_FIXTURES = GOOD + tuple(FAULTED)


def load_fixture(name):
    return br.load_stream(os.path.join(FIXTURES, name + ".jsonl.gz"))


def fail_rules(findings):
    return {f["rule"] for f in findings if f["severity"] == "fail"}


# ---------------------------------------------------------------------------------------------
# 1. bridge_rules against the fixtures, and their red twins
# ---------------------------------------------------------------------------------------------
class FixtureRuleTests(unittest.TestCase):
    def test_good_fixtures_have_no_fail_findings(self):
        for name in GOOD:
            with self.subTest(name):
                findings = br.check_stream(load_fixture(name))
                self.assertEqual(fail_rules(findings), set(), findings)

    def test_faulted_fixtures_carry_exactly_the_expected_fail_rules(self):
        for name, expected in FAULTED.items():
            with self.subTest(name):
                findings = br.check_stream(load_fixture(name))
                self.assertEqual(fail_rules(findings), expected, findings)

    def test_expected_fail_rules_in_the_fixture_json_match_this_module(self):
        """The fixtures' own notes (tests/fixtures/bridge/*.json) agree with what bridge_rules.py finds."""
        for name in ALL_FIXTURES:
            with self.subTest(name):
                with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as f:
                    doc = json.load(f)
                findings = br.check_stream(load_fixture(name))
                self.assertEqual(fail_rules(findings), set(doc["expected_fail_rules"]))


class RedTwinTests(unittest.TestCase):
    """Every rule not already exercised by a *_fault fixture, forced red by editing a GOOD
    fixture's loaded messages (never the fixture files - see the module docstring)."""

    def test_missing_prompt_source_trips_prompt_card_missing(self):
        msgs = load_fixture("surveil_ok")
        touched = False
        for m in msgs:
            if m.get("t") == "state" and (m.get("prompt") or {}).get("source"):
                m["prompt"] = dict(m["prompt"])
                m["prompt"]["source"] = None
                touched = True
        self.assertTrue(touched, "the surveil fixture should have a state with prompt.source")
        findings = br.check_stream(msgs)
        self.assertIn("prompt_card_missing", {f["rule"] for f in findings})
        self.assertEqual(findings[0]["severity"], "warn")

    def test_a_never_answered_request_trips_unanswered_request(self):
        msgs = load_fixture("spiteful_ok")
        msgs = [m for m in msgs if not (m.get("t") == "_sent" and (m.get("cmd") or {}).get("c") == "reply"
                                        and (m.get("cmd") or {}).get("id") == 1)]
        findings = br.check_stream(msgs)
        self.assertEqual(fail_rules(findings), {"unanswered_request"})

    def test_the_last_request_of_a_stopped_stream_is_not_blamed(self):
        """"Skip the last request if the stream simply ends (a stopped test)."""
        msgs = [{"t": "ready"}, {"t": "request", "id": 1, "kind": "confirm", "title": "Keep?"}]
        self.assertEqual(fail_rules(br.check_stream(msgs)), set())

    def test_a_fatal_message_trips_fatal(self):
        msgs = load_fixture("surveil_ok") + [{"t": "fatal", "text": "boom"}]
        findings = br.check_stream(msgs)
        self.assertEqual(fail_rules(findings), {"fatal"})

    def test_exit_without_game_over_or_quit_also_trips_fatal(self):
        # the fixture itself ends with a recorded "_sent quit" (Checker.stop() closing the session
        # normally) - strip it so this proves the OTHER case: an _exit that was never asked for.
        msgs = [m for m in load_fixture("surveil_ok") if not (m.get("t") == "_sent" and (m.get("cmd") or {}).get("c") == "quit")]
        msgs.append({"t": "_exit"})
        findings = br.check_stream(msgs)
        self.assertEqual(fail_rules(findings), {"fatal"})

    def test_a_quit_before_exit_is_not_fatal(self):
        msgs = load_fixture("surveil_ok") + [{"t": "_sent", "time": 0, "cmd": {"c": "quit"}}, {"t": "_exit"}]
        self.assertEqual(fail_rules(br.check_stream(msgs)), set())

    def test_six_dropped_commands_trip_dropped_clicks(self):
        msgs = load_fixture("surveil_ok") + [{"t": "dropped"} for _ in range(6)]
        findings = br.check_stream(msgs)
        self.assertEqual(fail_rules(findings), set())
        self.assertIn("dropped_clicks", {f["rule"] for f in findings})

    def test_five_dropped_commands_do_not(self):
        msgs = load_fixture("surveil_ok") + [{"t": "dropped"} for _ in range(5)]
        findings = br.check_stream(msgs)
        self.assertNotIn("dropped_clicks", {f["rule"] for f in findings})

    def test_an_exception_line_in_the_engine_log_trips_engine_error(self):
        msgs = load_fixture("surveil_ok")
        findings = br.check_stream(msgs, ["NullPointerException at forge.game.Game.doThing"])
        self.assertEqual(fail_rules(findings), {"engine_error"})

    def test_an_allow_listed_engine_log_line_does_not(self):
        msgs = load_fixture("surveil_ok")
        findings = br.check_stream(msgs, ["Breeding Pool Did not have activator set in SpellAbility_Condition.checkConditions()"])
        self.assertEqual(fail_rules(findings), set())

    def test_a_check_failed_line_is_not_double_counted_as_an_engine_error(self):
        msgs = load_fixture("spiteful_fault")
        with open(os.path.join(FIXTURES, "spiteful_fault.engine.log"), encoding="utf-8") as f:
            log = f.read().splitlines()
        findings = br.check_stream(msgs, log)
        self.assertNotIn("engine_error", {f["rule"] for f in findings})

    def test_cmdzone_no_with_the_command_zone_question_deleted_strands_the_commander(self):
        """Read tests/fixtures/bridge/README.md: cmdzone_no's question is a PROMPT, not a request,
        and it can arrive in the same state that first shows the commander in exile."""
        msgs = load_fixture("cmdzone_no")
        touched = False
        for m in msgs:
            if m.get("t") == "state":
                p = m.get("prompt") or {}
                if "command zone" in (p.get("message") or "").lower():
                    m["prompt"] = dict(p)
                    m["prompt"]["message"] = "Priority: Checker (edited away)"
                    touched = True
        self.assertTrue(touched)
        findings = br.check_stream(msgs)
        self.assertEqual(fail_rules(findings), {"commander_stranded"})

    def test_a_stale_priority_prompt_beside_the_exiled_commander_is_not_a_stranding(self):
        """Round 28ba, found live (scenario 7 failed about 1 run in 4): a snapshot can show the commander already in
        exile while its prompt is still the OLD 'Priority ... Stack: 1 to Resolve' - the same question number as before
        the removal resolved. Only a NEW priority question (a higher inputSeq) may count as 'nobody asked'."""
        msgs = copy.deepcopy(load_fixture("cmdzone_yes"))
        states = [m for m in msgs if m.get("t") == "state"]
        first_exiled = next(i for i, m in enumerate(states)
                            if any(z.lower() == "exile" for _c, z, _o in br._iter_commander_cards(m)))
        before = states[first_exiled - 1]
        stale = copy.deepcopy(states[first_exiled])
        stale["prompt"] = dict(before.get("prompt") or {}, message="Priority: Checker\nTurn: 3 (Checker)\nStack: 1 to Resolve.")
        stale["inputSeq"] = before.get("inputSeq")
        msgs.insert(msgs.index(states[first_exiled]), stale)             # the stale snapshot comes just before the question
        self.assertEqual(fail_rules(br.check_stream(msgs)), set())

    def test_an_ai_owned_stranded_commander_is_not_reported(self):
        """Round 28bb (was "only a warning" in the 28b spec): the AI decides about the command zone without asking, so
        every AI commander that died was reported - 17 games of noise in soak night 1. Simplest way
        to prove that branch: relabel the commander as someone else's before it strands, and also
        remove the command-zone question (so it strands with nobody having asked about it)."""
        edited = copy.deepcopy(load_fixture("cmdzone_no"))
        for m in edited:
            if m.get("t") == "state":
                for c, _zone, _owner in br._iter_commander_cards(m):
                    c["owner"] = 99
                p = m.get("prompt") or {}
                if "command zone" in (p.get("message") or "").lower():
                    m["prompt"] = dict(p)
                    m["prompt"]["message"] = "Priority: Checker (edited away)"
        findings = br.check_stream(edited)
        self.assertEqual(fail_rules(findings), set())
        self.assertNotIn("commander_stranded", {f["rule"] for f in findings})


class LoaderTests(unittest.TestCase):
    def test_loads_plain_jsonl_too(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False, encoding="utf-8") as f:
            f.write('{"t": "ready"}\n{"t": "state", "turn": 1}\n')
            path = f.name
        self.addCleanup(os.remove, path)
        self.assertEqual(br.load_stream(path), [{"t": "ready"}, {"t": "state", "turn": 1}])

    def test_engine_log_missing_file_is_empty_not_an_error(self):
        self.assertEqual(br.load_engine_log(os.path.join(BASE, "nope_no_such_file.log")), [])


# ---------------------------------------------------------------------------------------------
# 2. the bot: every answer valid, in range, deterministic per seed
# ---------------------------------------------------------------------------------------------
class BotAnswersTests(unittest.TestCase):
    def _requests_from(self, name):
        return [m for m in load_fixture(name) if m.get("t") == "request"]

    def test_every_recorded_request_gets_a_valid_answer_across_many_seeds(self):
        requests = []
        for name in ALL_FIXTURES:
            requests += self._requests_from(name)
        self.assertTrue(requests)
        for seed in range(200):
            bot = SoakBot(seed=seed)
            for req in requests:
                ans = bot.answer_request(req)
                self._assert_valid(req, ans)

    def _assert_valid(self, req, ans):
        kind = req.get("kind")
        if kind == "confirm":
            self.assertIsInstance(ans, bool)
            return
        if kind == "assign":
            self.assertIsInstance(ans, list)
            self.assertEqual(len(ans), len(req.get("rows") or []))
            self.assertTrue(all(isinstance(a, int) and a >= 0 for a in ans))
            return
        if kind == "input":
            self.assertIsInstance(ans, str)
            options = req.get("options") or []
            if options:
                self.assertIn(ans, options)
            return
        items = req.get("items") or []
        real = {i for i, it in enumerate(items) if not card_check.is_heading(it) and not card_check.is_finish(it)}
        self.assertIsInstance(ans, list)
        self.assertEqual(len(set(ans)), len(ans), "the bot answered the same item twice: %r" % ans)
        self.assertTrue(set(ans) <= real, "the bot answered something that was not offered: %r" % ans)
        lo, hi = req.get("min", 0) or 0, req.get("max")
        if hi is None or hi < 0:
            hi = len(real)
        hi = min(hi, len(real))
        if kind != "choose_optional":
            self.assertTrue(min(lo, hi) <= len(ans) <= hi, (req, ans))

    def test_random_items_requests_of_every_shape_get_a_valid_answer(self):
        bot = SoakBot(seed=7)
        rng = random.Random(4)
        for _ in range(500):
            n = rng.randint(0, 8)
            items = [{"kind": "text", "label": "x%d" % i} for i in range(n)]
            lo = rng.randint(0, n)
            hi = rng.randint(lo, n)
            kind = rng.choice(["choose", "choose_optional", "order"])
            req = {"kind": kind, "title": "t", "min": lo, "max": hi, "items": items}
            self._assert_valid(req, bot.answer_request(req))

    def test_headings_and_the_finish_marker_are_never_answered(self):
        items = [{"kind": "text", "label": "--CARDS ON BATTLEFIELD:--"}, {"kind": "text", "label": "Sol Ring"},
                 {"kind": "text", "label": "[FINISH TARGETING]"}]
        bot = SoakBot(seed=1)
        for _ in range(50):
            ans = bot.answer_request({"kind": "choose", "title": "t", "min": 0, "max": 3, "items": items})
            self.assertNotIn(0, ans)
            self.assertNotIn(2, ans)

    def test_the_same_seed_gives_the_same_answers(self):
        req = {"kind": "order", "title": "t", "min": 3, "max": 3,
              "items": [{"kind": "text", "label": str(i)} for i in range(3)]}
        a = [SoakBot(seed=55).answer_request(req) for _ in range(5)]
        self.assertTrue(all(x == a[0] for x in a))

    def test_keep_your_hand_is_always_yes(self):
        bot = SoakBot(seed=1)
        for _ in range(30):
            self.assertTrue(bot.answer_request({"kind": "confirm", "title": "Keep your hand?", "default": False}))

    def test_mulligan_flow_confirms_are_stable_not_random(self):
        bot = SoakBot(seed=1)
        answers = {bot.answer_request({"kind": "confirm", "title": "Mulligan again?", "default": True}) for _ in range(30)}
        self.assertEqual(answers, {True})


# ---------------------------------------------------------------------------------------------
# 2b. next_action must reach a move for every empty-stack priority window, not just my own main
# phase - found live: a first soak run stalled every single game the moment it hit a priority
# window outside its own main phase (an opponent's upkeep was the first one it hit).
# card_check.next_move returns None on purpose for "Priority: ... stack empty" (it's a scripted-
# scenario driver, meant to stop there), so next_action can't just fall through to it for those.
# ---------------------------------------------------------------------------------------------
def _priority_state(active_player, me_id, phase, phase_label, turn_owner_name, hand=()):
    return {
        "turn": 1, "phase": phase, "activePlayer": active_player, "me": me_id, "stack": [],
        "players": [
            {"id": 0, "name": "Soak", "zones": {"hand": list(hand), "battlefield": []}},
            {"id": 1, "name": "AI 1", "zones": {"hand": [], "battlefield": []}},
        ],
        "prompt": {"message": "Priority: Soak\nTurn: 1 (%s)\nPhase: %s\nStack: Empty" % (turn_owner_name, phase_label),
                  "ok": {"label": "OK", "enabled": True}, "cancel": {"label": "End Turn", "enabled": False}},
    }


class NextActionDispatchTests(unittest.TestCase):
    def test_priority_during_someone_elses_upkeep_is_answered_not_stalled(self):
        # exactly the state that stalled a real soak run: my priority during AI 1's upkeep, empty stack
        state = _priority_state(active_player=1, me_id=0, phase="UPKEEP", phase_label="Upkeep step", turn_owner_name="AI 1")
        move = SoakBot(seed=1).next_action(state, [], {})
        self.assertEqual(move, ("ok",))

    def test_priority_in_my_own_upkeep_is_answered(self):
        state = _priority_state(active_player=0, me_id=0, phase="UPKEEP", phase_label="Upkeep step", turn_owner_name="Soak")
        move = SoakBot(seed=1).next_action(state, [], {})
        self.assertEqual(move, ("ok",))

    def test_priority_at_someone_elses_end_step_is_answered(self):
        state = _priority_state(active_player=1, me_id=0, phase="END_OF_TURN", phase_label="End Step", turn_owner_name="AI 1")
        move = SoakBot(seed=1).next_action(state, [], {})
        self.assertEqual(move, ("ok",))

    def test_my_main_phase_still_prefers_playing_a_land(self):
        hand = [{"id": 42, "isLand": True, "type": "Basic Land - Plains", "selectable": True}]
        state = _priority_state(active_player=0, me_id=0, phase="MAIN1", phase_label="Main phase, precombat",
                                turn_owner_name="Soak", hand=hand)
        move = SoakBot(seed=1).next_action(state, [], {})
        self.assertEqual(move, ("click", 42))

    def test_my_main_phase_with_nothing_playable_passes(self):
        state = _priority_state(active_player=0, me_id=0, phase="MAIN1", phase_label="Main phase, precombat", turn_owner_name="Soak")
        move = SoakBot(seed=1).next_action(state, [], {})
        self.assertEqual(move, ("ok",))

    def test_choosing_who_starts_a_3plus_player_game_clicks_a_player_not_ok(self):
        # exactly the live crash: Forge's own "who goes first" prompt never appears as a card
        # selection (InputSelectEntitiesFromList<Player> only ever populates setSelectables with
        # its choices' Card subset, and a Player is never a Card), so prompt.selecting/selMin stay
        # exactly like a plain "click OK" prompt with nothing to select - and the message doesn't
        # contain the word "player" either, so card_check's own player-click heuristic misses it
        # too. Left alone the bot sent "ok" with nothing chosen; Forge accepted that "ok" as if
        # satisfied, then crashed a few lines later (determineFirstTurnPlayer() returned null)
        # trying to deal an opening hand to nobody.
        state = {
            "turn": 0, "phase": "", "activePlayer": 0, "me": 0, "stack": [],
            "players": [
                {"id": 0, "name": "Soak", "zones": {"hand": [], "battlefield": []}},
                {"id": 1, "name": "AI 1", "zones": {"hand": [], "battlefield": []}},
                {"id": 2, "name": "AI 2", "zones": {"hand": [], "battlefield": []}},
            ],
            "prompt": {"message": "Soak, you have won the coin toss.\n\nWho would you like to start this game? "
                                   "(Click on the portrait.)",
                       "ok": {"label": "OK", "enabled": False, "focus": True},
                       "cancel": {"label": "Cancel", "enabled": False},
                       "selecting": False, "selMin": 1, "selMax": 1},
        }
        move = SoakBot(seed=1).next_action(state, [], {})
        self.assertEqual(move[0], "player")
        self.assertIn(move[1], (0, 1, 2))

    def test_choosing_who_starts_falls_back_to_ok_once_a_player_is_already_clicked(self):
        state = {
            "turn": 0, "phase": "", "activePlayer": 0, "me": 0, "stack": [],
            "players": [{"id": 0, "name": "Soak", "zones": {"hand": [], "battlefield": []}}],
            "prompt": {"message": "Soak, you have won the coin toss.\n\nWho would you like to start this game? "
                                   "(Click on the portrait.)",
                       "ok": {"label": "OK", "enabled": True, "focus": True},
                       "cancel": {"label": "Cancel", "enabled": False},
                       "selecting": False, "selMin": 0, "selMax": 0},
        }
        move = SoakBot(seed=1).next_action(state, [], {"soak_first_player_chosen": True})
        self.assertEqual(move, ("ok",))


# ---------------------------------------------------------------------------------------------
# 3. tools/coverage_report.py
# ---------------------------------------------------------------------------------------------
class CoverageReportTests(unittest.TestCase):
    FULL = {
        "order|all:sorted=0:unsorted=2+": 1, "order|all:sorted=2+:unsorted=0": 1, "order|some:sorted=0:unsorted=2+": 1,
        "choose|min=0:max=1:offered=2+": 1, "choose|min=1:max=2+:offered=2+": 1,
        "choose_one|optional:offered=2+": 1, "choose_one|required:offered=1": 1,
        "confirm|card": 1, "confirm|no_card": 1,
    }

    def write(self, data):
        f = tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False, encoding="utf-8")
        json.dump(data, f)
        f.close()
        self.addCleanup(os.remove, f.name)
        return f.name

    def test_the_full_list_exits_zero_and_says_so(self):
        path = self.write(self.FULL)
        report, missing = cov.format_report(cov.load_shapes(path))
        self.assertEqual(missing, [])
        self.assertIn("Every required shape was seen.", report)
        self.assertEqual(cov.main([path]), 0)

    def test_a_missing_shape_exits_one_and_is_listed(self):
        data = dict(self.FULL)
        del data["order|all:sorted=2+:unsorted=0"]
        path = self.write(data)
        report, missing = cov.format_report(cov.load_shapes(path))
        self.assertEqual(missing, [("order", "all:sorted=2+:unsorted=0")])
        self.assertIn("order", report)
        self.assertEqual(cov.main([path]), 1)

    def test_reads_a_shapes_log_with_one_shapes_object_per_line(self):
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False, encoding="utf-8")
        for k, v in self.FULL.items():
            f.write(json.dumps({"shapes": {k: v}}) + "\n")
        f.close()
        self.addCleanup(os.remove, f.name)
        totals = cov.load_shapes(f.name)
        self.assertEqual(cov.missing_required(totals), [])

    def test_several_files_are_summed_together(self):
        data = dict(self.FULL)
        missing_key = "confirm|no_card"
        n = data.pop(missing_key)
        path_a = self.write(data)
        path_b = self.write({missing_key: n})
        self.assertEqual(cov.main([path_a, path_b]), 0)


# ---------------------------------------------------------------------------------------------
# 4. the soak runner, offline, against a stand-in bridge
# ---------------------------------------------------------------------------------------------
def _write_check_bridge():
    """A tiny scripted stream that sends one bridge {"t":"check"} straight after its first state -
    a stand-in for the real bridge, only for this offline test (never edits tests/fake_bridge.py).
    Written under the system temp folder, never inside Karl's project folder."""
    fd, path = tempfile.mkstemp(suffix="_bridge_stub_with_check.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "out(t='ready')\n"
            "out(t='state', turn=1, phase='MAIN1', me=0, activePlayer=0, players=[\n"
            "    {'id': 0, 'name': 'Me', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "    {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Priority: Me', 'ok': {'label': 'OK', 'enabled': True}, 'cancel': {'label': 'End', 'enabled': False}})\n"
            "out(t='check', q='order', rule='wrong_count', detail='test check')\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            "    out(t='log', entries=[{'type': 'TEST', 'text': 'got'}])\n"
        )
    return path


def _write_idle_bridge():
    """A stand-in bridge that says 'ready', sends one priority state whose OK stays enabled, and then goes
    quiet on every click - simulating a real engine that takes real wall-clock time to process one. This is
    exactly what a live run hit for real: run_game() had no pacing on repeated identical sends, so it
    flooded the bridge as fast as the loop could spin (8261 of 8265 sent commands dropped in one live game,
    stuck on its first few clicks for over ten minutes before genuinely stalling). Written under the system
    temp folder, never inside Karl's project folder."""
    fd, path = tempfile.mkstemp(suffix="_bridge_stub_idle.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "out(t='ready')\n"
            "out(t='state', turn=1, phase='MAIN1', me=0, activePlayer=0, players=[\n"
            "    {'id': 0, 'name': 'Me', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "    {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Priority: Me', 'ok': {'label': 'OK', 'enabled': True}, 'cancel': {'label': 'End', 'enabled': False}})\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            # deliberately sends nothing back for an ordinary click - simulates a slow-to-respond engine
        )
    return path


def _write_stuck_ack_bridge():
    """A stand-in bridge that pushes one Priority state, then acknowledges every click with "dropped"
    forever and never pushes a new state again - exactly what a real soak run hit live: Forge threw an
    uncaught NullPointerException on a background thread during game setup (a bad card entry in a deck
    reached GameAction.startGame with a null player), so the bridge process stayed alive and kept
    acknowledging clicks, but the game itself never advanced. The old stall check counted ANY bytes from
    Forge as progress, and these "dropped" acks kept its clock refreshed forever, so a crash like this
    ran for the whole game_timeout undetected (see WatchdogTests for the much longer-fused safety net
    under that). Written under the system temp folder, never inside Karl's project folder."""
    fd, path = tempfile.mkstemp(suffix="_bridge_stub_stuck.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "out(t='ready')\n"
            "out(t='state', turn=1, phase='MAIN1', me=0, activePlayer=0, players=[\n"
            "    {'id': 0, 'name': 'Me', 'life': 40, 'zones': {'hand': [], 'battlefield': []}},\n"
            "    {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Priority: Me', 'ok': {'label': 'OK', 'enabled': True}, 'cancel': {'label': 'End', 'enabled': False}})\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    if cmd['c'] == 'quit':\n"
            "        break\n"
            "    out(t='dropped', c=cmd['c'], at=cmd.get('at'), now=cmd.get('at'))\n"
        )
    return path


class SoakRunnerOfflineTests(unittest.TestCase):
    def make_args(self, command, out_dir, **extra):
        import argparse
        base = dict(games=1, hours=None, players=2, decks="sample", seed=123, fault=None,
                   out=out_dir, turn_cap=25, game_timeout=1.0, session_command=command)
        base.update(extra)
        return argparse.Namespace(**base)

    def test_a_game_that_produces_a_check_writes_a_zip_and_a_summary_line(self):
        bridge = _write_check_bridge()
        self.addCleanup(lambda: os.path.exists(bridge) and os.remove(bridge))
        with tempfile.TemporaryDirectory() as out_dir:
            args = self.make_args([sys.executable, bridge], out_dir)
            code = soak.run(args)
            self.assertEqual(code, 1)
            zips = [n for n in os.listdir(out_dir) if n.endswith(".zip")]
            self.assertEqual(len(zips), 1)
            self.assertTrue(zips[0].startswith("soak_"))
            self.assertIn("bridge_check", zips[0])
            with zipfile.ZipFile(os.path.join(out_dir, zips[0])) as z:
                self.assertIn("record.jsonl", z.namelist())
            with open(os.path.join(out_dir, "soak_summary.txt"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("bridge_check", text)
            self.assertIn("game 0", text)

    def test_a_clean_game_writes_no_zip(self):
        with tempfile.TemporaryDirectory() as out_dir:
            args = self.make_args([sys.executable, FAKE_BRIDGE], out_dir)
            code = soak.run(args)
            self.assertEqual(code, 0)
            zips = [n for n in os.listdir(out_dir) if n.endswith(".zip")]
            self.assertEqual(zips, [])
            with open(os.path.join(out_dir, "soak_summary.txt"), encoding="utf-8") as f:
                self.assertIn("0 game(s) with a failing finding.", f.read())

    def test_shapes_are_summed_into_soak_shapes_json(self):
        with tempfile.TemporaryDirectory() as out_dir:
            args = self.make_args([sys.executable, FAKE_BRIDGE], out_dir)
            soak.run(args)
            with open(os.path.join(out_dir, "soak_shapes.json"), encoding="utf-8") as f:
                shapes = json.load(f)
            self.assertIsInstance(shapes, dict)

    def test_run_game_paces_identical_resends_instead_of_flooding(self):
        bridge = _write_idle_bridge()
        self.addCleanup(lambda: os.path.exists(bridge) and os.remove(bridge))
        with tempfile.TemporaryDirectory() as out_dir:
            args = self.make_args([sys.executable, bridge], out_dir, game_timeout=0.6)
            soak.run(args)
            with gzip.open(os.path.join(out_dir, "game_000", "record.jsonl.gz"), "rt", encoding="utf-8") as f:   # round 28d fix 1: kept gzipped
                sent = [json.loads(line) for line in f if json.loads(line).get("t") == "_sent"]
            oks = [m for m in sent if (m.get("cmd") or {}).get("c") == "ok"]
            # a 0.6s game against an engine that never acknowledges the click should send "ok" a small
            # handful of times (roughly game_timeout / MOVE_RESEND_SECONDS), never hundreds or thousands
            self.assertLess(len(oks), 10, "run_game is flooding identical resends instead of pacing them: %d" % len(oks))

    def test_stall_detection_isnt_fooled_by_endless_dropped_acks(self):
        # the exact live incident: Forge crashed on a background thread right after game setup: the
        # bridge process survived and kept acking every resend with "dropped", but no real state ever
        # came again. Real progress has to mean a new state, not bytes of any kind.
        bridge = _write_stuck_ack_bridge()
        self.addCleanup(lambda: os.path.exists(bridge) and os.remove(bridge))
        old_stall, old_resend = soak.STALL_SECONDS, soak.MOVE_RESEND_SECONDS
        soak.STALL_SECONDS, soak.MOVE_RESEND_SECONDS = 0.5, 0.05
        try:
            with tempfile.TemporaryDirectory() as out_dir:
                args = self.make_args([sys.executable, bridge], out_dir, game_timeout=5.0)
                soak.run(args)
                with open(os.path.join(out_dir, "soak_summary.txt"), encoding="utf-8") as f:
                    text = f.read()
                self.assertIn("stall", text)
        finally:
            soak.STALL_SECONDS, soak.MOVE_RESEND_SECONDS = old_stall, old_resend

    def test_ctrl_c_still_writes_the_summary(self):
        with tempfile.TemporaryDirectory() as out_dir:
            args = self.make_args([sys.executable, FAKE_BRIDGE], out_dir, games=1000)

            calls = {"n": 0}
            real_run_game = soak.run_game

            def flaky(index, args, rng, out_root, watchdog_holder=None):
                calls["n"] += 1
                if calls["n"] > 1:
                    raise KeyboardInterrupt
                return real_run_game(index, args, rng, out_root, watchdog_holder=watchdog_holder)

            soak.run_game = flaky
            try:
                code = soak.run(args)
            finally:
                soak.run_game = real_run_game
            self.assertIn(code, (0, 1))
            self.assertTrue(os.path.isfile(os.path.join(out_dir, "soak_summary.txt")))


# ---------------------------------------------------------------------------------------------
# 4b. spec 2.3: MANTICORE_DATA_DIR must be set before forge_client (and crashlog) are imported,
# so a soak run's own crash notes, engine logs and saves never land in the real project's files.
# ---------------------------------------------------------------------------------------------
def _run_soak_in_a_fresh_process(out_dir, command):
    """A subprocess is the only reliable way to test this: inside THIS test process, forge_client is
    already imported - by test_forge_client.py, or by tests/__init__.py's own MANTICORE_DATA_DIR setup
    running first - so its DATA_DIR is already fixed before this test ever runs, and the bug this is
    guarding against couldn't show up here even if soak.py regressed. Karl's real run is a fresh
    `python tools\\soak.py`, so a fresh interpreter is the honest way to check it. Prints
    forge_client.DATA_DIR (after the run) as its last line of stdout."""
    script = (
        "import argparse, os, sys\n"
        "os.environ.pop('MANTICORE_DATA_DIR', None)\n"
        "sys.path.insert(0, %r)\n"
        "sys.path.insert(0, %r)\n"
        "import soak\n"
        "args = argparse.Namespace(games=1, hours=None, players=2, decks='sample', seed=123, fault=None,\n"
        "                          out=%r, turn_cap=25, game_timeout=1.0, session_command=%r)\n"
        "soak.run(args)\n"
        "import forge_client\n"
        "print(forge_client.DATA_DIR)\n"
    ) % (BASE, os.path.join(BASE, "tools"), out_dir, command)
    env = dict(os.environ)
    env.pop("MANTICORE_DATA_DIR", None)          # simulate Karl's own shell: nothing has set this yet
    return subprocess.run([sys.executable, "-c", script], cwd=BASE, capture_output=True, text=True, timeout=90, env=env)


class SoakEntryPointTests(unittest.TestCase):
    """`python tools\\soak.py ...` (the documented command) is not the same as `import soak` after manually
    putting the project root on sys.path - every other test here does the latter. Run this way, Python puts
    tools\\ on sys.path[0], not the project root, so an early build that never added BASE_DIR itself worked
    in every test but failed for real the first time it was actually run as `python tools\\soak.py` (Karl hit
    this directly: ModuleNotFoundError: No module named 'bridge_rules'). This runs the real entry point, the
    real way, to make sure that specific failure can't come back unnoticed."""

    def test_running_the_script_directly_finds_its_project_root_imports(self):
        with tempfile.TemporaryDirectory() as out_dir:
            script = os.path.join(BASE, "tools", "soak.py")
            # Round 28ba: point FORGE_RUNTIME at an empty folder, so the one game fails at once (Forge unavailable) even
            # where Forge IS installed (Karl's PC, the sandbox) - otherwise a real game starts and runs past the timeout.
            env = dict(os.environ, FORGE_RUNTIME=os.path.join(out_dir, "no_forge_here"))
            result = subprocess.run(
                [sys.executable, script, "--games", "1", "--out", out_dir],
                cwd=BASE, capture_output=True, text=True, timeout=60, env=env)
            self.assertNotIn("ModuleNotFoundError", result.stderr, result.stderr)
            self.assertNotIn("Traceback", result.stderr, result.stderr)
            # No real Forge here, so the game itself can't play out (fc.ForgeUnavailable -> ended=crash) - that's
            # expected and fine; the only thing this test is guarding is that the imports and argv parsing work.
            self.assertIn("game 0:", result.stdout, result.stdout)


class SoakDataDirIsolationTests(unittest.TestCase):
    def test_a_soak_run_points_forge_client_at_its_own_output_folder_not_the_project(self):
        with tempfile.TemporaryDirectory() as out_dir:
            result = _run_soak_in_a_fresh_process(out_dir, [sys.executable, FAKE_BRIDGE])
            self.assertEqual(result.returncode, 0, result.stderr)
            printed = result.stdout.strip().splitlines()[-1]
            self.assertEqual(os.path.normcase(os.path.abspath(printed)), os.path.normcase(os.path.abspath(out_dir)))
            self.assertNotEqual(os.path.normcase(os.path.abspath(printed)), os.path.normcase(BASE))

    def test_a_soak_run_leaves_the_real_crash_log_untouched(self):
        real_crash_log = os.path.join(BASE, "crash_log.txt")
        existed_before = os.path.isfile(real_crash_log)
        before = os.path.getmtime(real_crash_log) if existed_before else None
        with tempfile.TemporaryDirectory() as out_dir:
            result = _run_soak_in_a_fresh_process(out_dir, [sys.executable, FAKE_BRIDGE])
            self.assertEqual(result.returncode, 0, result.stderr)
        exists_after = os.path.isfile(real_crash_log)
        self.assertEqual(existed_before, exists_after, "a soak run created or removed the real crash_log.txt")
        if existed_before:
            self.assertEqual(before, os.path.getmtime(real_crash_log), "a soak run modified the real crash_log.txt")


# ---------------------------------------------------------------------------------------------
# 4c. the watchdog: a live soak run needed the whole machine restarted after a game stopped making
# any progress at all (2026-09-26) - the exact cause couldn't be pinned down afterwards (the
# terminal was already closed), so this is a safety net for "whatever it was", not a fix for a
# diagnosed bug: it guarantees a game's Forge process gets killed if run_game() doesn't finish well
# past its own timeout, so the run can move on instead of needing a restart by hand.
# ---------------------------------------------------------------------------------------------
class WatchdogTests(unittest.TestCase):
    def test_watchdog_kill_terminates_the_sessions_process(self):
        import forge_client as fc
        session = fc.ForgeSession("mine.dck", [], name="Soak", command=[sys.executable, FAKE_BRIDGE])
        session.start()
        self.addCleanup(session.close)
        self.assertTrue(session.alive())
        soak._watchdog_kill({"session": session})
        end = time.time() + 3
        while time.time() < end and session.alive():
            time.sleep(0.05)
        self.assertFalse(session.alive(), "the watchdog did not kill a live session's process")

    def test_watchdog_kill_with_no_session_yet_does_nothing(self):
        soak._watchdog_kill({})              # a game that hung before session.start() ever ran - must not raise

    def test_watchdog_kill_on_an_already_closed_session_does_nothing(self):
        import forge_client as fc
        session = fc.ForgeSession("mine.dck", [], name="Soak", command=[sys.executable, FAKE_BRIDGE])
        session.start()
        session.close()
        soak._watchdog_kill({"session": session})     # must not raise, even though the process is already gone


if __name__ == "__main__":
    unittest.main()
