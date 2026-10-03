# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/soak_prune.py - keep soak_runs from filling the disk (Round 28d fix 1).

Every soak game keeps a folder with its full recording (record.jsonl) and Forge's log. A night was about 6 GB, and by
30 Sept Karl's soak_runs held 42 GB with 33 GB free on the disk. Those per-game folders are only needed while a night
is being looked into; what matters afterwards is kept elsewhere in the same run folder:
  - the summaries (soak_summary.txt, nightly_summary.txt, card_check_*.txt/json),
  - the bug-report zips of the games that failed (each zip carries its own copy of the record and logs),
  - known_signatures.json, nightly_state.json, soak_shapes.json.

So this removes the game_NNN folders of every run except the newest few, and nothing else.

    python tools\\soak_prune.py                  keep the newest 3 runs' games, remove older ones' game folders
    python tools\\soak_prune.py --keep 5
    python tools\\soak_prune.py --dry-run        only say what it would remove

tools\\nightly.py runs it at the start of every night (keep 3), so this is only needed by hand to catch up.
"""
import argparse
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP_RUNS = 3
_RUN = re.compile(r"^(run|night)_\d{8}_\d{6}$")
_GAME = re.compile(r"^game_\d+$")


def _size(path):
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def runs(root):
    """The run folders (run_<date>_<time> and night_<date>_<time>), oldest first by the time in their name."""
    try:
        names = [n for n in os.listdir(root) if _RUN.match(n) and os.path.isdir(os.path.join(root, n))]
    except OSError:
        return []
    return sorted(names, key=lambda n: n.split("_", 1)[1])


def game_folders(run_dir):
    """A run's per-game folders: run_*/game_NNN, and night_*/soak/game_NNN."""
    out = []
    for where in (run_dir, os.path.join(run_dir, "soak")):
        try:
            for n in os.listdir(where):
                p = os.path.join(where, n)
                if _GAME.match(n) and os.path.isdir(p):
                    out.append(p)
        except OSError:
            continue
    return sorted(out)


def prune(root, keep=KEEP_RUNS, dry_run=False, say=print):
    """Remove the game folders of every run but the newest `keep`. Returns (folders removed, bytes freed). Never raises
    for a folder it can't remove: it says so and carries on."""
    names = runs(root)
    old = names[:-keep] if keep > 0 else names
    removed, freed = 0, 0
    for name in old:
        for g in game_folders(os.path.join(root, name)):
            size = _size(g)
            if dry_run:
                removed, freed = removed + 1, freed + size
                continue
            try:
                shutil.rmtree(g)
                removed, freed = removed + 1, freed + size
            except OSError as e:
                say("could not remove %s: %s" % (g, e))
    return removed, freed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=os.path.join(BASE, "soak_runs"))
    ap.add_argument("--keep", type=int, default=KEEP_RUNS, help="how many of the newest runs keep their game folders")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    n, freed = prune(args.root, args.keep, args.dry_run)
    verb = "would remove" if args.dry_run else "removed"
    print("%s %d game folder(s), %.1f GB, from runs older than the newest %d in %s" % (verb, n, freed / 1e9, args.keep, args.root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
