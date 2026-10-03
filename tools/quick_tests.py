# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/quick_tests.py - the fast test set Karl runs before handing a session back (Round 27a, §4).

    python tools\\quick_tests.py

Three things, in order:
  1. Checks the project folder for a Windows-reserved file name (the `nul` file that broke backups on
     2026-09-25 would have been caught here on the very next run).
  2. Runs the whole suite with MANTICORE_SKIP_LIVE=1, so nothing tries to start the real Forge engine -
     see tests/live.py. Target: under 3 minutes on Karl's PC; this script times itself and says so.
  3. Confirms the run left the real project files alone: crash_log.txt, forge_engine.log, perf_log.txt,
     backup.log, backup_status.json and saves/ must have the same size and mtime after as before. A test
     that writes one of these directly (not through MANTICORE_DATA_DIR) fails this even if it "passed".

Exit code: 0 only if the reserved-name check, the hygiene check, and the tests themselves all pass.
"""
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from backup import RESERVED_NAMES, TOP_LEVEL_SKIP  # noqa: E402  (after sys.path.insert)

WATCHED = ["crash_log.txt", "forge_engine.log", "perf_log.txt", "backup.log", "backup_status.json", "saves"]


def find_reserved_names(folder=BASE_DIR):
    """Every reserved-name file or folder under the project, as paths relative to it. Skips the same
    folders backup.py itself never looks inside (forge_runtime/, cache/, .git, etc.) - a reserved name
    inside the unpacked Forge engine or the Scryfall cache isn't Karl's to rename."""
    skip = TOP_LEVEL_SKIP | {"__pycache__"}
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        rel_root = os.path.relpath(root, folder)
        for name in dirs + files:
            if name.split(".", 1)[0].lower() in RESERVED_NAMES:
                # Build the relative path from the folder's relative path and the bare name. On Windows,
                # os.path.join(root, "NUL") is taken to mean the NUL *device* (\\.\NUL), and relpath() then
                # fails with "path is on mount '\\.\NUL'" - exactly when this check is needed most.
                found.append(name if rel_root == "." else os.path.join(rel_root, name))
    return sorted(found)


def snapshot(folder=BASE_DIR):
    """{relative path: (size, mtime)} for the watched files/folders that currently exist."""
    shot = {}
    for name in WATCHED:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            st = os.stat(path)
            shot[name] = (st.st_size, st.st_mtime)
        elif os.path.isdir(path):
            for dirpath, _dirs, files in os.walk(path):
                for f in files:
                    p = os.path.join(dirpath, f)
                    st = os.stat(p)
                    shot[os.path.relpath(p, folder)] = (st.st_size, st.st_mtime)
    return shot


def main():
    problems = []

    reserved = find_reserved_names()
    if reserved:
        problems.append("Windows-reserved file name(s) found - Windows cannot open these; delete them:\n  "
                         + "\n  ".join(f'cmd /c del "\\\\?\\{os.path.join(BASE_DIR, r)}"' for r in reserved))

    before = snapshot()
    env = dict(os.environ, MANTICORE_SKIP_LIVE="1")
    t0 = time.time()
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                             cwd=BASE_DIR, env=env)
    elapsed = time.time() - t0
    print(f"\nquick_tests: {elapsed:.1f}s (target: under 180s on Karl's PC)")
    if result.returncode != 0:
        problems.append(f"the test suite itself failed (exit {result.returncode})")

    after = snapshot()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        problems.append("these real project files changed during the run - a test wrote into them directly "
                         "instead of honouring MANTICORE_DATA_DIR:\n  " + "\n  ".join(changed))

    if problems:
        print("\nquick_tests: FAILED\n")
        for p in problems:
            print(f"- {p}\n")
        return 1
    print("quick_tests: all clear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
