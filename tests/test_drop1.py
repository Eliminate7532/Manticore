# SPDX-License-Identifier: GPL-3.0-or-later
"""
Round DROP1 (12 Oct 2026): the dropped clicks of the soak night of 11-12 Oct (run_20261011_200529: 770 dropped in 319 games,
30 games with the old "dropped_clicks" warning). Measured that night: 676 were the soak bot clicking again right after it
had answered a pop-up (kicker, a library search, the order of triggers) - the question key went back to what it was before
the pop-up, the loop took that for a new question and asked the bot again from the same, not yet updated snapshot, and the
second click carried the old question number. 78 were "busy" (a mana ability running), 7 "disabled", 9 the same click again
without a pop-up, and none was a question whose only answer was lost.

Fix 1 (tools/soak.py): no second click on a question until the question as it stands has changed (pop-ups left out of the
comparison), or MOVE_WAIT_SECONDS have passed. Fix 2 (bridge_rules.classify_drops / drop_counts, soak_report.drop_lines,
tools/drop_report.py): every drop gets a cause, and the warning is only for a lost answer or a burst of busy drops.

tests/fixtures/soak/drop1_real_drops.jsonl.gz: four short windows of that night's real records (game 41: three drops after a
pop-up - Galadriel's Dismissal's kicker, Open the Armory's library search, Land Tax's search; game 128: a busy OK), the
states trimmed to the fields the question key uses.
"""
import argparse
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "tools")
for p in (ROOT, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

import bridge_rules as br       # noqa: E402
import soak_report              # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "soak", "drop1_real_drops.jsonl.gz")


def real_drops():
    return br.load_stream(FIXTURE)


def sent(cmd):
    return {"t": "_sent", "time": 0, "cmd": cmd}


class ClassifyTests(unittest.TestCase):
    def test_the_real_drops_after_a_pop_up_are_repeats_and_the_busy_one_is_busy(self):
        drops = br.classify_drops(real_drops())
        self.assertEqual([cause for _i, cause, _c in drops], ["repeat", "repeat", "repeat", "busy"])
        self.assertEqual(drops[0][2], {"c": "card", "id": 3, "at": 17})

    def test_counts(self):
        counts = br.drop_counts(real_drops())
        self.assertEqual(counts, {"repeat": 3, "lost": 0, "busy": 1, "disabled": 0, "out": 0, "clicks": 13})

    def test_without_the_first_click_the_same_drop_is_a_lost_answer(self):
        msgs = real_drops()
        first = next(i for i, m in enumerate(msgs) if m.get("t") == "_sent" and m["cmd"] == {"c": "card", "id": 3, "at": 17})
        del msgs[first]                      # only the dropped copy is left: nothing reached Forge for question 17
        self.assertEqual(br.classify_drops(msgs)[0][1], "lost")

    def test_a_different_card_after_an_accepted_click_is_still_a_repeat(self):
        msgs = [sent({"c": "card", "id": 3, "at": 17}), sent({"c": "reply", "id": 1, "value": [0]}),
                sent({"c": "card", "id": 4, "at": 17}), {"t": "dropped", "c": "card", "at": 17, "now": 18}]
        self.assertEqual(br.classify_drops(msgs), [(3, "repeat", {"c": "card", "id": 4, "at": 17})])

    def test_when_every_click_for_a_question_is_dropped_the_answer_was_lost(self):
        msgs = [sent({"c": "ok", "at": 7}), sent({"c": "ok", "at": 7}),
                {"t": "dropped", "c": "ok", "at": 7, "now": 8}, {"t": "dropped", "c": "ok", "at": 7, "now": 8}]
        self.assertIn("lost", [c for _i, c, _x in br.classify_drops(msgs)])
        self.assertIn("dropped_lost", {f["rule"] for f in br.check_stream(msgs)})

    def test_the_bridges_own_reasons_pass_through(self):
        for reason in ("busy", "disabled", "out"):
            msgs = [sent({"c": "ok", "at": 5}), {"t": "dropped", "c": "ok", "reason": reason, "now": 5}]
            self.assertEqual(br.classify_drops(msgs)[0][1], reason)

    def test_a_drop_with_no_click_to_match_is_lost(self):
        self.assertEqual(br.classify_drops([{"t": "dropped"}])[0][1], "lost")

    def test_two_drops_of_the_same_number_each_match_their_own_click(self):
        msgs = [sent({"c": "ok", "at": 7}), sent({"c": "ok", "at": 7}), sent({"c": "ok", "at": 7}),
                {"t": "dropped", "c": "ok", "at": 7, "now": 8}, {"t": "dropped", "c": "ok", "at": 7, "now": 8}]
        self.assertEqual([c for _i, c, _x in br.classify_drops(msgs)], ["repeat", "repeat"])


class RuleTests(unittest.TestCase):
    def rules(self, msgs):
        return {f["rule"] for f in br.check_stream(msgs)}

    def test_repeats_alone_are_no_warning(self):
        msgs = []
        for at in range(1, 30, 2):           # 15 repeats in one game: the old rule warned from 6
            msgs += [sent({"c": "ok", "at": at}), sent({"c": "ok", "at": at}),
                     {"t": "dropped", "c": "ok", "at": at, "now": at + 1}]
        self.assertEqual(self.rules(msgs) & {"dropped_lost", "dropped_busy", "dropped_clicks"}, set())

    def test_one_lost_answer_is_a_warning(self):
        msgs = [sent({"c": "ok", "at": 3}), {"t": "dropped", "c": "ok", "at": 3, "now": 4}]
        findings = [f for f in br.check_stream(msgs) if f["rule"] == "dropped_lost"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], br.WARN)
        self.assertIn("ok at question 3", findings[0]["detail"])

    def test_six_busy_drops_warn_and_five_do_not(self):
        def busy(n):
            msgs = []
            for _ in range(n):
                msgs += [sent({"c": "card", "id": 9, "at": 11}), {"t": "dropped", "c": "card", "reason": "busy", "now": 11}]
            return msgs
        self.assertIn("dropped_busy", self.rules(busy(6)))
        self.assertNotIn("dropped_busy", self.rules(busy(5)))

    def test_the_real_drops_give_no_warning(self):
        self.assertEqual(self.rules(real_drops()) & {"dropped_lost", "dropped_busy"}, set())


class SummaryTests(unittest.TestCase):
    def test_game_health_counts_drops_by_cause(self):
        h = soak_report.game_health(real_drops())
        self.assertEqual(h["dropped"], 4)
        self.assertEqual(h["drops"]["repeat"], 3)
        self.assertEqual(h["drops"]["busy"], 1)

    def test_the_summary_has_a_dropped_clicks_section(self):
        lines = soak_report.drop_lines([soak_report.game_health(real_drops()), soak_report.game_health([])])
        text = "\n".join(lines)
        self.assertIn("4 of 13 numbered clicks dropped (30.8%) over 2 game(s)", text)
        self.assertIn("repeat 3, lost 0, busy 1, disabled 0, out 0", text)

    def test_no_section_without_games(self):
        self.assertEqual(soak_report.drop_lines([]), [])


class DropReportTests(unittest.TestCase):
    def run_report(self, folder):
        import drop_report
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = drop_report.main([folder])
        return code, out.getvalue()

    def make_run(self, root, msgs):
        game = os.path.join(root, "game_000")
        os.makedirs(game)
        with gzip.open(os.path.join(game, "record.jsonl.gz"), "wt", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps(m) + "\n")

    def test_a_run_with_only_repeats_and_busy_exits_zero(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_run(root, real_drops())
            code, text = self.run_report(root)
        self.assertEqual(code, 0, text)
        self.assertIn("1 game(s), 13 numbered clicks, 4 dropped", text)
        self.assertIn("games with a lost click: 0", text)

    def test_a_lost_click_exits_one_and_is_shown(self):
        msgs = real_drops()
        first = next(i for i, m in enumerate(msgs) if m.get("t") == "_sent" and m["cmd"] == {"c": "card", "id": 3, "at": 17})
        del msgs[first]
        with tempfile.TemporaryDirectory() as root:
            self.make_run(root, msgs)
            code, text = self.run_report(root)
        self.assertEqual(code, 1, text)
        self.assertIn("e.g. lost", text)
        self.assertIn("games with a lost click: 1", text)


def _write_popup_bridge(log_path):
    """A stand-in bridge that does what Forge did in game 41: my main phase, two cards I can cast (question 17); a click on
    one asks a pop-up (optional costs); once it is answered Forge moves on (question 18: asking nothing) and only 0.4 s
    later shows the next question (19: priority with the spell on the stack). A click whose number is not current is
    dropped, as Main.handle does. An OK at 19 ends the game. Every command it got is written to log_path."""
    fd, path = tempfile.mkstemp(suffix="_bridge_popup.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys, time\n"
            "LOG = %r\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "def state(seq, asking, stack, weak):\n"
            "    hand = [{'id': 3, 'name': 'Divination', 'weak': weak}, {'id': 4, 'name': 'Opt', 'weak': weak}]\n"
            "    msg = 'Priority: Soak\\nTurn: 5 (Soak)\\nPhase: Main phase, precombat\\nStack: ' + ('1 to Resolve' if stack else 'Empty')\n"
            "    out(t='state', turn=5, phase='MAIN1', me=0, activePlayer=0, input='InputPassPriority' if asking else '',\n"
            "        inputSeq=seq, asking=asking, stack=[{'id': 3, 'name': 'Divination'}] if stack else [],\n"
            "        players=[{'id': 0, 'name': 'Soak', 'life': 40, 'zones': {'hand': hand, 'battlefield': []}},\n"
            "                 {'id': 1, 'name': 'AI', 'life': 40, 'zones': {'battlefield': []}}],\n"
            "        prompt={'message': msg, 'ok': {'label': 'OK', 'enabled': True}, 'cancel': {'label': 'End Turn', 'enabled': True}})\n"
            "out(t='ready', protocol=2)\n"
            "seq = 17\n"
            "state(seq, True, False, 1)\n"
            "for line in sys.stdin:\n"
            "    cmd = json.loads(line)\n"
            "    with open(LOG, 'a') as lg: lg.write(json.dumps(cmd) + '\\n')\n"
            "    c = cmd['c']\n"
            "    if c == 'quit':\n"
            "        break\n"
            "    if c in ('ok', 'cancel', 'card', 'player', 'mana') and cmd.get('at') != seq:\n"
            "        out(t='dropped', c=c, at=cmd.get('at'), now=seq); continue\n"
            "    if c == 'card' and seq == 17:\n"
            "        out(t='request', id=1, kind='choose', title='Choose optional costs', min=0, max=1,\n"
            "            items=[{'kind': 'text', 'label': 'Kicker - {2}'}])\n"
            "    elif c == 'reply' and seq == 17:\n"
            "        seq = 18\n"
            "        time.sleep(0.4)\n"
            "        seq = 19\n"
            "        state(seq, True, True, 0)\n"
            "    elif c == 'ok' and seq == 19:\n"
            "        out(t='game_over')\n"
            % log_path)
    return path


class SoakLoopTests(unittest.TestCase):
    def play(self):
        import soak
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "commands.jsonl")
            bridge = _write_popup_bridge(log)
            self.addCleanup(os.remove, bridge)
            out = os.path.join(tmp, "run")
            args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                      turn_cap=25, game_timeout=12.0, session_command=[sys.executable, bridge],
                                      canary=False)
            soak.run(args)
            with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                summary = f.read()
            msgs = br.load_stream(os.path.join(out, "game_000", "record.jsonl.gz"))
            with open(log, encoding="utf-8") as f:
                got = [json.loads(line) for line in f]
        return summary, msgs, got

    def test_no_second_click_after_answering_a_pop_up(self):
        summary, msgs, got = self.play()
        self.assertIn("ended=game_over", summary, summary[-800:])
        dropped = [m for m in msgs if m.get("t") == "dropped"]
        self.assertEqual(dropped, [], [c for c in got])
        clicks = [c for c in got if c["c"] in ("card", "ok")]
        self.assertEqual([c["c"] for c in clicks], ["card", "ok"], clicks)       # one cast, then one OK to resolve it
        self.assertEqual(clicks[1]["at"], 19)


if __name__ == "__main__":
    unittest.main()
