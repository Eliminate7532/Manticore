# SPDX-License-Identifier: GPL-3.0-or-later
"""Round COV1 (15 Oct 2026): tools/coverage_report.py reads the file tools/soak.py writes.

claude/SOAK_RUN_2026-10-15.md found it: soak.py writes soak_shapes.json with json.dump(..., indent=1), one object over many
lines; coverage_report took every multi-line file for one-object-per-line, no line parsed, and the report said "Shapes seen:
(none)" with all 9 required shapes missing - for every soak run since 27 Sep. Also: "confirm card" / "confirm no_card" are no
longer required, because desktop Forge asks yes/no questions in the prompt bar (InputConfirm), not through the bridge's
confirm() request (docs/rounds/COV1.md has the bytecode evidence).

Offline: no Java, no Forge, no pygame.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import coverage_report as cov

FIXTURE = os.path.join(BASE, "tests", "fixtures", "soak", "cov1_soak_shapes_2026-10-15.json")

SEVEN = {
    "order|all:sorted=0:unsorted=2+": 3, "order|all:sorted=2+:unsorted=0": 1, "order|some:sorted=0:unsorted=2+": 1,
    "choose|min=0:max=1:offered=2+": 2, "choose|min=1:max=2+:offered=2+": 1,
    "choose_one|optional:offered=2+": 5, "choose_one|required:offered=1": 1,
}


class CoverageFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_text(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_what_soak_writes_is_read_back_exactly(self):
        """The real writer (tools/soak.py write_outputs), then the real reader: every shape and count comes back."""
        import soak
        soak.write_outputs(self.tmp, [], dict(SEVEN))
        path = os.path.join(self.tmp, "soak_shapes.json")
        with open(path, encoding="utf-8") as f:
            self.assertGreater(len(f.read().splitlines()), 1, "soak.py no longer writes over several lines - "
                                                             "this test would then prove nothing")
        totals = cov.load_shapes(path)
        expected = {tuple(k.split("|", 1)): n for k, n in SEVEN.items()}
        self.assertEqual(totals, expected)
        self.assertEqual(cov.main([path]), 0)

    def test_the_real_15_october_file_is_read(self):
        """Karl's soak of 15 Oct (soak_runs/run_20261015_103607/soak_shapes.json, copied as a fixture): 9 shapes, and every
        required one is among them. Before COV1 this printed '(none)' and exited 1."""
        totals = cov.load_shapes(FIXTURE)
        self.assertEqual(len(totals), 9)
        self.assertEqual(totals[("choose_one", "optional:offered=2+")], 253)
        self.assertEqual(totals[("order", "all:sorted=2+:unsorted=0")], 28)
        report, missing = cov.format_report(totals)
        self.assertEqual(missing, [])
        self.assertNotIn("(none)", report)
        self.assertEqual(cov.main([FIXTURE]), 0)

    def test_a_one_line_shapes_log_is_read_as_a_log_line(self):
        """A MANTICORE_SHAPES_LOG with one session ({"shapes": {...}} on one line) parses as ONE JSON object too; it must
        still give the shapes inside it, not a question called 'shapes'."""
        path = self.write_text("one.jsonl", json.dumps({"shapes": {"order|all:sorted=0:unsorted=2+": 4}}) + "\n")
        self.assertEqual(cov.load_shapes(path), {("order", "all:sorted=0:unsorted=2+"): 4})

    def test_a_one_line_check_style_record(self):
        path = self.write_text("rec.jsonl", json.dumps({"q": "choose", "shape": "min=0:max=1:offered=1", "n": 3}) + "\n")
        self.assertEqual(cov.load_shapes(path), {("choose", "min=0:max=1:offered=1"): 3})

    def test_a_log_of_several_lines_is_still_read_line_by_line(self):
        lines = [json.dumps({"shapes": {"order|all:sorted=0:unsorted=2+": 1}}),
                 json.dumps({"shapes": {"order|all:sorted=0:unsorted=2+": 2, "choose_one|required:offered=1": 1}}),
                 "not json - a torn last line"]
        path = self.write_text("log.jsonl", "\n".join(lines) + "\n")
        self.assertEqual(cov.load_shapes(path), {("order", "all:sorted=0:unsorted=2+"): 3,
                                                 ("choose_one", "required:offered=1"): 1})

    def test_counts_from_soak_s_file_and_a_shapes_log_are_added_up(self):
        """The review of 15 Oct: test_round28b's summing test had left out a shape that is no longer required, so it passed with
        one file alone. Here the same shape comes from soak.py's file (2) and a two-line shapes log (3 + 4): 9 in all, and a
        required shape only the log has makes the pair complete."""
        import soak
        first = dict(SEVEN)
        only_in_log = "order|some:sorted=0:unsorted=2+"
        del first[only_in_log]
        first["choose|min=0:max=1:offered=2+"] = 2
        soak.write_outputs(self.tmp, [], first)
        path_a = os.path.join(self.tmp, "soak_shapes.json")
        path_b = self.write_text("shapes.jsonl", json.dumps({"shapes": {"choose|min=0:max=1:offered=2+": 3}}) + "\n" +
                                 json.dumps({"shapes": {"choose|min=0:max=1:offered=2+": 4, only_in_log: 1}}) + "\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cov.main([path_a]), 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cov.main([path_a, path_b]), 0)
        line = [l for l in out.getvalue().splitlines() if "min=0:max=1:offered=2+" in l]
        self.assertEqual(len(line), 1, out.getvalue())
        self.assertTrue(line[0].rstrip().endswith("x9"), line[0])      # 2 + 3 + 4, as the report prints it

    def test_an_empty_object_reads_as_nothing(self):
        path = self.write_text("empty.json", "{}\n")
        self.assertEqual(cov.load_shapes(path), {})
        self.assertEqual(cov.main([path]), 1)


class RequiredListTests(unittest.TestCase):
    def test_the_required_list_is_the_seven_desktop_forge_can_ask(self):
        self.assertEqual(cov.REQUIRED_SHAPES, [
            ("order", "all:sorted=0:unsorted=2+"), ("order", "all:sorted=2+:unsorted=0"),
            ("order", "some:sorted=0:unsorted=2+"), ("choose", "max=1"), ("choose", "max=2+"),
            ("choose_one", "optional"), ("choose_one", "required")])
        self.assertEqual([(q, s) for q, s, _ in cov.NOT_REQUIRED], [("confirm", "card"), ("confirm", "no_card")])

    def test_confirm_shapes_are_counted_when_they_come_but_never_missing(self):
        totals = {tuple(k.split("|", 1)): n for k, n in SEVEN.items()}
        report, missing = cov.format_report(totals)
        self.assertEqual(missing, [])
        self.assertIn("Every required shape was seen.", report)
        self.assertIn("confirm      card", report)              # listed as not required and not seen
        self.assertIn("confirm      no_card", report)
        totals[("confirm", "card")] = 2
        report, missing = cov.format_report(totals)
        self.assertEqual(missing, [])
        self.assertIn("confirm      card                         x2", report)
        unseen = report.split("Not required, and not seen", 1)[1]
        self.assertNotIn("confirm      card ", unseen)          # 'card' was seen; only no_card is left in that list
        self.assertIn("no_card", unseen)

    def test_no_card_seen_does_not_count_as_card_seen(self):
        totals = {tuple(k.split("|", 1)): n for k, n in SEVEN.items()}
        totals[("confirm", "no_card")] = 1
        report, _ = cov.format_report(totals)
        self.assertIn("Not required, and not seen", report)     # 'card' still unseen: "card" is not in "no_card"'s place
        unseen = report.split("Not required, and not seen", 1)[1]
        self.assertIn("confirm      card ", unseen)
        self.assertNotIn("no_card", unseen.split("Yes/no questions")[0])

    def test_the_report_says_where_yes_no_questions_go(self):
        report, _ = cov.format_report({})
        self.assertIn("prompt bar (InputConfirm)", report)


if __name__ == "__main__":
    unittest.main()
