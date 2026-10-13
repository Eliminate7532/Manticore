# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/drop_report.py - Round DROP1: why the bridge dropped clicks, over one or more soak runs.

    python tools\\drop_report.py soak_runs\\run_20261011_200529
    python tools\\drop_report.py soak_runs\\night_20261008_193059\\soak  soak_runs\\run_20261011_000736
    python tools\\drop_report.py some\\game_041\\record.jsonl.gz

Each argument is a run folder (its game_NNN\\record.jsonl[.gz] files are read) or one record file. For every dropped click it
says why (bridge_rules.classify_drops):

    repeat    a second click on a question already answered - dropped by design (Round 22), harmless
    lost      a question whose only answer was dropped - nothing reached Forge for it
    busy      clicked while a mana ability was being activated (Round 28bd)
    disabled  a greyed-out OK or Cancel (Round 28ba)
    out       a click from a player who has already lost (patch 44)

Exit code 1 when any click was "lost", else 0 - so a night can be checked with it. Reads only; writes nothing.
"""
import argparse
import glob
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def record_files(path):
    """The record files a path stands for: the file itself, or every game_*/record.jsonl[.gz] in a run folder."""
    if os.path.isfile(path):
        return [path]
    found = sorted(glob.glob(os.path.join(path, "game_*", "record.jsonl.gz")) +
                   glob.glob(os.path.join(path, "game_*", "record.jsonl")))
    return found


def report(paths, examples=3, out=print):
    """Prints the report; returns the totals {cause: n, "clicks": n, "games": n}."""
    import bridge_rules
    totals = {c: 0 for c in bridge_rules.DROP_CAUSES}
    totals["clicks"] = 0
    totals["games"] = 0
    per_game = []
    shown = {c: 0 for c in bridge_rules.DROP_CAUSES}
    for path in paths:
        files = record_files(path)
        if not files:
            out("%s: no record files" % path)
        for f in files:
            try:
                messages = bridge_rules.load_stream(f)
            except (OSError, ValueError, EOFError) as e:
                out("%s: could not be read (%s)" % (f, e))
                continue
            drops = bridge_rules.classify_drops(messages)
            counts = bridge_rules.drop_counts(messages)
            totals["games"] += 1
            totals["clicks"] += counts["clicks"]
            for c in bridge_rules.DROP_CAUSES:
                totals[c] += counts[c]
            per_game.append((f, counts))
            for i, cause, cmd in drops:
                if cause in ("lost", "busy") and shown[cause] < examples:
                    shown[cause] += 1
                    out("  e.g. %s: %s - %s (message %d)" % (cause, f, cmd, i))
    n = sum(totals[c] for c in bridge_rules.DROP_CAUSES)
    rate = (100.0 * n / totals["clicks"]) if totals["clicks"] else 0.0
    out("%d game(s), %d numbered clicks, %d dropped (%.2f%%)" % (totals["games"], totals["clicks"], n, rate))
    for c in bridge_rules.DROP_CAUSES:
        out("  %-9s %5d" % (c, totals[c]))
    busy_games = [f for f, c in per_game if c["busy"] > bridge_rules.BUSY_DROP_LIMIT]
    lost_games = [f for f, c in per_game if c["lost"]]
    out("games with a lost click: %d; games with more than %d busy drops: %d"
        % (len(lost_games), bridge_rules.BUSY_DROP_LIMIT, len(busy_games)))
    return totals


def main(argv=None):
    ap = argparse.ArgumentParser(description="Why the bridge dropped clicks, over soak runs or record files.")
    ap.add_argument("paths", nargs="+", help="a run folder (game_NNN\\record.jsonl[.gz] inside) or a record file")
    ap.add_argument("--examples", type=int, default=3, help="how many lost / busy drops to show (default 3)")
    args = ap.parse_args(argv)
    totals = report(args.paths, examples=args.examples)
    return 1 if totals["lost"] else 0


if __name__ == "__main__":
    sys.exit(main())
