# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PERF1 (Karl, 3 Nov 2026: "Waiting after I act" on a big board): the bridge's two speed-ups, its timing lines and
tools/bench_board.py.

1. --skip-scan (Passing.skipScan, default on): at a priority in a step this seat doesn't stop at, with an empty stack, Forge
   passes for me whatever its "can this player act?" scan finds - the bridge passes without the scan. "compare" runs Forge's
   way and reports any priority where Forge asked instead ("bridge: passscan compare DIFFERENCE" in the engine log).
2. --question-flush (BridgeGui.questionFlush, default on): when Forge shows this seat a question, its snapshot goes as soon as
   the question is complete, not after the ordinary 40 ms wait.

The live tests run the bench's own 60-permanent board through the real Forge engine with --timing. On the base
(claude/round-eng-brawl) they fail: that bridge sends no timing lines at all; and inside this bridge, "--skip-scan off" /
"--question-flush off" (Forge's / the old way) fail the "on" assertions for the real reason - shown by the *_off tests, which
assert the opposite.
"""
import os
import re
import tempfile
import time
import unittest

import forge_client as fc
import tests.live as live
from tools import bench_board as bb

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
PROBLEM = live.live_problem()


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_skip_only_when_the_scan_cannot_matter(self):
        src = read("Passing.java")
        body = src[src.index("boolean skippedStep("):]
        body = body[:body.index("\n    }\n") + 6]
        for needed in ("!autoPass", "pc.isMacroActive()", "yc.isYieldActive()", "gv.peekStack() != null", "gui.isUiSetToSkipPhase"):
            self.assertIn(needed, body)
        # full control and Slow return before the stack-empty branch, so they never reach the skip
        d = src[src.index("Decision decide("):src.index("boolean skippedStep(")]
        self.assertLess(d.index("if (full)"), d.index("Decision.SKIP"))
        self.assertLess(d.index("if (slow)"), d.index("Decision.SKIP"))

    def test_skip_is_handled_like_pass(self):
        src = read("Passing.java")
        self.assertIn("if (d == Decision.PASS || d == Decision.SKIP) {", src)
        self.assertIn('if (d == Decision.SKIP && "compare".equals(skipScan))', src)

    def test_modes_and_flags(self):
        main = read("Main.java")
        for flag in ('"--timing"', '"--skip-scan"', '"--question-flush"', '"--skip-scan-plant"', '"--sample"'):
            self.assertIn(flag, main)
        self.assertIn("--skip-scan-plant ignored (the bridge was not started with --dev)", main)
        self.assertIn('ready.addProperty("skipScan"', main)
        self.assertIn('static volatile String skipScan = "on";', read("Passing.java"))
        self.assertIn("static volatile boolean questionFlush = true;", read("BridgeGui.java"))

    def test_timing_is_off_by_default(self):
        self.assertIn("static volatile boolean on = false;", read("Timing.java"))
        self.assertFalse(fc.ForgeSession("x.dck", []).timing)
        self.assertNotIn("--timing", fc.ForgeSession("x.dck", [], seed=1)._bridge_args())
        self.assertIn("--timing", fc.ForgeSession("x.dck", [], seed=1, timing=True)._bridge_args())

    def test_question_flush_waits_for_swing_and_the_question(self):
        src = read("BridgeGui.java")
        body = src[src.index("private void questionFlush(long seq)"):]
        body = body[:body.index("private boolean quickFlush")]
        self.assertIn("SwingUtilities.invokeLater(done::countDown)", body)
        self.assertIn("inputSeq() != seq || uiSeq != seq || !dirty.get()", body)
        self.assertIn("flusher.schedule(this::flushIfDirty, 40", src)


class BenchSplitTests(unittest.TestCase):
    def test_split_puts_each_line_in_its_step(self):
        t0 = 1000.0
        lines = [{"k": "rx", "ms": 0, "at": t0 * 1000 + 1}, {"k": "handle", "ms": 0.5, "queued": 0.2, "at": t0 * 1000 + 2},
                 {"k": "decide", "ms": 0.1, "d": "SKIP", "at": t0 * 1000 + 3},
                 {"k": "forge", "ms": 30, "ret": "pass", "at": t0 * 1000 + 40},
                 {"k": "forge", "ms": 20, "ret": "ask", "at": t0 * 1000 + 70}, {"k": "ai", "ms": 5, "at": t0 * 1000 + 50},
                 {"k": "input", "ms": 0, "at": t0 * 1000 + 70},
                 {"k": "snap", "ms": 2, "json": 1, "send": 1, "bytes": 100, "at": t0 * 1000 + 75},
                 {"k": "ai", "ms": 999, "at": t0 * 1000 + 5000}]                       # outside the window: left out
        out = bb.Bench.split("pass", t0, t0 + 0.1, {"_bytes": 100, "_parse_ms": 0.5}, lines)
        self.assertAlmostEqual(out["total"], 100.0)
        self.assertEqual(out["forge"], 50)
        self.assertEqual(out["forge_pass"], 30)
        self.assertEqual(out["forge_ask"], 20)
        self.assertEqual(out["ai"], 5)
        self.assertEqual(out["decisions"], ["SKIP"])
        self.assertEqual(out["input_to_snap"], 5)
        self.assertAlmostEqual(out["rest"], 100 - (1 + 0.2 + 0.5 + 0.1 + 50 + 5 + 2 + 1 + 1 + 0.5), places=3)

    def test_boards_have_the_size_asked_for(self):
        for size in (60, 150, 300):
            mine, theirs = bb.board(size)
            self.assertAlmostEqual(len(mine) + len(theirs), size, delta=2)
            self.assertGreater(len(mine), len(theirs))
        lines = bb.setup_lines(150)
        self.assertIn("activephase=MAIN1", lines)
        self.assertTrue(any(ln.startswith("humanbattlefield=") and "T:g_1_1_elf_warrior" in ln for ln in lines))

    def test_the_table_has_a_row_per_step(self):
        rows = [dict(action=a, total=10.0) for a in bb.ACTIONS]
        text = "\n".join(bb.table(rows, 60))
        for _key, label in bb.STEPS:
            self.assertIn(label, text)


class CommandLineTests(unittest.TestCase):
    def test_bench_board_from_the_program(self):
        import forge_table as ft
        self.assertEqual(ft.bench_board_argv(), ["--runs", "5", "--both"])
        with open(os.path.join(BASE, "commander_sim.spec"), encoding="utf-8") as f:
            self.assertIn('"tools.bench_board"', f.read())          # the installed build carries it (Manticore-cli.exe --bench-board)

    def test_a_portable_copy_writes_where_backups_never_look(self):
        from unittest import mock
        import paths
        with mock.patch.object(paths, "is_portable", return_value=True):
            self.assertEqual(bb.default_out(), os.path.join(bb.ROOT, "soak_runs", "bench_board"))
        with open(os.path.join(BASE, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("soak_runs/", f.read())
        with mock.patch.object(paths, "is_portable", return_value=False), \
                mock.patch.object(paths, "local_dir", return_value="/x/local"):
            self.assertEqual(bb.default_out(), os.path.join("/x/local", "bench_board"))


@unittest.skipIf(PROBLEM, f"Forge is not ready here: {PROBLEM}")
class LiveTests(unittest.TestCase):
    """The bench's 60-permanent board through the real engine, with --timing."""

    def run_pass(self, extra, runs=2, before=None):
        folder = tempfile.mkdtemp(prefix="perf1_test_")
        tmp = tempfile.mkdtemp(prefix="perf1_decks_")
        deck = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Lathril, Blade of the Elves"], ["Forest"] * 99, "Bench")
        ai = fc.write_deck_file(os.path.join(tmp, "ai.dck"), ["Adeline, Resplendent Cathar"], ["Plains"] * 99, "BenchAI")
        b = bb.Bench(deck, ai, seed=7, folder=folder, extra_args=extra)
        b.say = lambda *_: None
        b.start("t")
        timings, rows, states = [], [], []
        try:
            for _ in range(runs):
                self.assertTrue(b.apply_board(60), "the board could not be set up")
                if before:
                    before(b)
                row, st, req = b.timed("pass", b.s.ok)
                rows.append(row)
                timings += list(b.s.timings)
                states.append(st)
                b.resolve_extras(st, req)
        finally:
            b.stop()
        with open(os.path.join(folder, "engine_t.log"), encoding="utf-8", errors="replace") as f:
            log = f.read()
        return rows, timings, states, log, b

    def test_a_pass_from_main_1_skips_the_scan_and_still_reaches_the_attack(self):
        rows, timings, states, log, _ = self.run_pass([])
        for row, st in zip(rows, states):
            self.assertIn("SKIP", row["decisions"])                # beginning of combat: a step I don't stop at
            self.assertEqual(row["forge_pass"], 0, row)             # no scan for a priority Forge would pass anyway
            self.assertIn("attack", ((st or {}).get("prompt") or {}).get("message", "").lower())
        self.assertNotIn("passscan compare DIFFERENCE", log)

    def test_with_the_skip_off_forge_scans_that_priority(self):
        rows, timings, states, log, _ = self.run_pass(["--skip-scan", "off"])
        for row, st in zip(rows, states):
            self.assertNotIn("SKIP", row["decisions"])
            self.assertGreater(row["forge_n"], 0, row)              # Forge's own scan ran and passed by itself
            self.assertIn("attack", ((st or {}).get("prompt") or {}).get("message", "").lower())

    def test_no_skip_when_auto_pass_is_off(self):
        rows, timings, states, log, _ = self.run_pass([], runs=1, before=lambda b: (b.s.auto_pass(False), b.settle(0.5)))
        self.assertNotIn("SKIP", rows[0]["decisions"])

    def test_compare_mode_agrees_and_reports_a_planted_difference(self):
        rows, timings, states, log, b = self.run_pass(["--skip-scan", "compare"], runs=2)
        for row in rows:
            self.assertIn("SKIP", row["decisions"])                 # compare: the skip is decided ...
            self.assertGreater(row["forge_pass"], 0, row)            # ... but Forge's own way runs, and passed by itself
        self.assertNotIn("passscan compare DIFFERENCE", log)
        rows, timings, states, log, b = self.run_pass(["--skip-scan", "compare", "--skip-scan-plant", "1"], runs=1)
        self.assertIn("bridge: passscan compare DIFFERENCE", log)    # the planted difference is caught

    def test_the_question_snapshot_is_quick_and_complete(self):
        rows, timings, states, log, _ = self.run_pass([], runs=3)
        snaps = [t for t in timings if t["k"] == "snap"]
        quick = [t for t in snaps if t.get("quick")]
        self.assertTrue(quick, "no question was sent by the quick flush")
        by_seq = {}
        for t in snaps:
            by_seq.setdefault(t["seq"], []).append(t)
        for q in quick:                                              # never an out-of-date prompt: a later snapshot of the same
            later = [t for t in by_seq[q["seq"]] if t["at"] >= q["at"]]   # question says the same thing
            self.assertEqual({t.get("prompt") for t in later}, {q.get("prompt")}, later)
        inputs = {t["seq"]: t["at"] for t in timings if t["k"] == "input"}
        self.assertTrue(any(q["at"] - inputs.get(q["seq"], -1e9) < 30 for q in quick), quick)   # well under the 40 ms wait

    def test_with_the_quick_flush_off_every_snapshot_waits_for_the_ordinary_flush(self):
        rows, timings, states, log, _ = self.run_pass(["--question-flush", "off"], runs=3)
        self.assertFalse([t for t in timings if t["k"] == "snap" and t.get("quick")])
        # (the gap itself is not asserted: the ordinary 40 ms wait may have started before the question, so it varies)


if __name__ == "__main__":
    unittest.main()
