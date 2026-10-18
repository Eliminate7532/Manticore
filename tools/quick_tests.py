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
     The scheduled backup (backup.py --auto, :06 and :36) writes backup.log and backup_status.json itself; the run takes
     longer than half an hour's gap on Karl's PC (1010 s on 13 Oct), so when backup_status.json says a backup finished
     during the run, those two files are said to be the backup's, not blamed on a test (round QT1). Also when backup.log
     shows a backup that started during the run and was still running at its end (18 Oct 2026).

Exit code: 0 only if the reserved-name check, the hygiene check, and the tests themselves all pass.
"""
import json
import os
import re
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from backup import RESERVED_NAMES, TOP_LEVEL_SKIP  # noqa: E402  (after sys.path.insert)

WATCHED = ["crash_log.txt", "forge_engine.log", "perf_log.txt", "backup.log", "backup_status.json", "saves"]
BACKUP_FILES = ("backup.log", "backup_status.json")   # round QT1: what the scheduled backup itself writes
STATUS_FILE = "backup_status.json"
BACKUP_LOG = "backup.log"
_LOG_HEADER = re.compile(r"^--- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$")   # backup.py: say(f"--- {now:%Y-%m-%d %H:%M:%S}")


def backup_finished_between(t0, t1, folder=BASE_DIR):
    """Round QT1: the local time at which the scheduled backup finished a run between t0 and t1 (time.time() values), from
    backup_status.json's last_ok / last_error (backup.write_status: local time, to the second), or None. Never raises."""
    try:
        with open(os.path.join(folder, STATUS_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("last_ok", "last_error"):
        value = data.get(key)
        try:
            ts = time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%S"))
        except (TypeError, ValueError, OverflowError):
            continue
        if t0 - 1 <= ts <= t1 + 1:                   # written to the second, so a second's slack either side
            return value
    return None


def backup_started_between(t0, t1, folder=BASE_DIR):
    """18 Oct 2026: a backup still running when the suite ends has written backup.log but not yet backup_status.json, so
    backup_finished_between misses it (Karl's run of 18 Oct ended inside the 10:06 backup and blamed backup.log). The local
    time of backup.log's newest "--- <time>" header when it falls between t0 and t1, or None. Never raises."""
    try:
        with open(os.path.join(folder, BACKUP_LOG), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        m = _LOG_HEADER.match(line)
        if m:
            try:
                ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except (ValueError, OverflowError):
                return None
            return m.group(1).replace(" ", "T") if t0 - 1 <= ts <= t1 + 1 else None
    return None


def split_backup_changes(changed, t0, t1, folder=BASE_DIR):
    """Round QT1: (changes to blame on a test, the backup's finish time or None). backup.log and backup_status.json are left
    out only when backup_status.json says the scheduled backup finished during the run; every other file always counts."""
    when = backup_finished_between(t0, t1, folder) or backup_started_between(t0, t1, folder)
    if when is None:
        return list(changed), None
    return [c for c in changed if c not in BACKUP_FILES], when


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
    changed, backup_at = split_backup_changes(changed, t0, time.time())
    if backup_at:
        print(f"quick_tests: the scheduled backup ran during the tests ({backup_at.replace('T', ' ')}) - "
              "backup.log and backup_status.json changed because of it, not because of a test.")
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
