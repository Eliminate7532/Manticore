# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools\\ai_bench.py - does the AI answer the right threat? Ten fixed boards through the real Forge engine (Round AI1).

    python tools\\ai_bench.py                              10 boards x 5 trials, seed 7
    python tools\\ai_bench.py --trials 10 --seed 11 --boards C1,C2,R2
    python tools\\ai_bench.py --out C:\\somewhere

Writes soak_runs\\ai_bench_<date>_<time>\\ai_bench_summary.txt (the score per board, what the AI did in every miss) and
ai_bench_results.json (every trial: the AI's stack entries, its casts, where my cards ended). soak_runs\\ is never backed up
or committed. The boards and the verdicts are in ai_bench.py at the project's root.
"""
import argparse
import datetime
import importlib.util
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:            # `python tools\ai_bench.py` puts tools\ on sys.path[0], not the project root
    sys.path.insert(0, BASE_DIR)

# forge_client (and crashlog, which card_check pulls in) read MANTICORE_DATA_DIR once, at import time, to decide where
# crash_log.txt, forge_engine.log and saves\ go. ai_bench.py imports card_check at its top, so it is imported lazily, by
# _load(), after main() has pointed MANTICORE_DATA_DIR at this run's own folder (the same rule as tools\soak.py).
ai_bench = None


def _load():
    """The project's ai_bench.py (the boards). This file has the same name, so when tools\\ is first on the path (a test that
    imported this tool as "ai_bench") a plain import would find this file again: the module is loaded from its own path."""
    global ai_bench
    if ai_bench is None:
        mod = sys.modules.get("ai_bench")
        if mod is None or not hasattr(mod, "BOARDS"):
            spec = importlib.util.spec_from_file_location("ai_bench", os.path.join(BASE_DIR, "ai_bench.py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["ai_bench"] = mod
            spec.loader.exec_module(mod)
        ai_bench = mod
    return ai_bench


def default_out_dir():
    """<program folder>/soak_runs for a portable copy (Karl's git checkout), paths.local_dir()/soak for an installed one -
    exactly tools\\soak.py's rule, so the runs land beside the soak nights."""
    try:
        import paths
    except ImportError:
        return os.path.join(BASE_DIR, "soak_runs")
    if paths.is_portable():
        return os.path.join(BASE_DIR, "soak_runs")
    return os.path.join(paths.local_dir(), "soak")


def run_folder(out_dir=None, now=None):
    now = now or datetime.datetime.now()
    return os.path.join(out_dir or default_out_dir(), "ai_bench_" + now.strftime("%Y%m%d_%H%M%S"))


def version_text():
    try:
        import version
        return version.describe()
    except Exception:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="Does the AI answer the right threat? Ten fixed boards, played through the real Forge engine.")
    ap.add_argument("--trials", type=int, default=5, help="trials per board (default 5)")
    ap.add_argument("--seed", type=int, default=7, help="Forge's random seed (default 7; the same seed gives the same trials)")
    ap.add_argument("--boards", default="", help="a comma-separated subset, e.g. C1,C2,R2 (default: all ten)")
    ap.add_argument("--out", default=None, help="where the run's folder goes (default: soak_runs\\ next to the program)")
    ap.add_argument("--list", action="store_true", help="print the boards and leave")
    args = ap.parse_args(argv)
    folder = run_folder(args.out)
    os.makedirs(folder, exist_ok=True)
    os.environ["MANTICORE_DATA_DIR"] = folder           # before _load(): see the comment above it
    ab = _load()
    if args.list:
        for b in ab.BOARDS:
            print("%-3s %-64s -> %s" % (b.key, b.title, b.right))
        return 0
    boards = [k for k in args.boards.split(",") if k.strip()] or None
    try:
        results = ab.run(trials=args.trials, seed=args.seed, boards=boards, say=print)
    except ValueError as exc:
        print(str(exc))
        return 2
    text = ab.write_results(folder, results, args.seed, version_text())
    print()
    print(text)
    print("written to", folder)
    return 0 if not any(t.error for t in results) else 1


if __name__ == "__main__":
    sys.exit(main())
