# SPDX-License-Identifier: GPL-3.0-or-later
"""
last_session.py - did Manticore close properly last time? (Round 28d, ALPHA_FINISH_LINE must-have 15)

A friend won't press F8 on a program that has just died, so the crashes we most need to hear about are the ones nobody
reports. At start the program writes session.json (process id, version, start time) in the logs folder and deletes it
when it closes normally. If the file is still there at the next start, the last run ended some other way - a crash, the
window killed in Task Manager, the PC switched off - and the deck screen offers to send a report about it, once.

The crashed run's Forge log would be lost: forge_engine.log is rewritten whenever Forge starts. So begin() keeps a copy of
it as forge_engine.crashed.log straight away, before anything can start Forge.

No pygame here, so it is easy to test. Nothing in here ever raises: this is a helper, the game must start regardless.
"""
import datetime
import json
import os

import paths

MARKER_NAME = "session.json"
ENGINE_LOG = "forge_engine.log"
CRASHED_ENGINE_LOG = "forge_engine.crashed.log"
JOURNAL_LINES = 400


def _log_dir(folder=None):
    return folder or paths.log_dir()


def marker_path(folder=None):
    return os.path.join(_log_dir(folder), MARKER_NAME)


def previous(folder=None):
    """The marker the last run left behind - {"pid", "version", "started"} - or None when it closed properly (or this is
    the first run). A marker that can't be read still counts: something left it there."""
    path = marker_path(folder)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"started": "?"}
    except (OSError, ValueError):
        return {"started": "?"}


def begin(version_text="", folder=None, now=None):
    """Call once at start. Returns what previous() said (None = the last run closed properly). When the last run did not
    close properly, its Forge log is kept as forge_engine.crashed.log first. Then a fresh marker is written, so the
    question is asked about each unexpected close once, whatever the player answers."""
    before = previous(folder)
    log_dir = _log_dir(folder)
    if before is not None:
        try:
            src = os.path.join(log_dir, ENGINE_LOG)
            if os.path.isfile(src):
                with open(src, "rb") as f:
                    data = f.read()
                with open(os.path.join(log_dir, CRASHED_ENGINE_LOG), "wb") as f:
                    f.write(data)
        except OSError:
            pass
    try:
        os.makedirs(log_dir, exist_ok=True)
        now = now or datetime.datetime.now()
        with open(marker_path(folder), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "version": version_text, "started": now.strftime("%Y-%m-%d %H:%M:%S")}, f)
    except OSError:
        pass
    return before


def end(folder=None):
    """Call when the program closes normally (the window closed, Quit): no marker means nothing to ask next time."""
    try:
        os.remove(marker_path(folder))
    except OSError:
        pass


def banner_text(before):
    """What the deck screen says about the last run, or "" when there's nothing to say."""
    if before is None:
        return ""
    when = before.get("started") or "?"
    return f"Manticore closed unexpectedly last time (started {when}). Send a report so Karl can look at it?"


def report_info(before, name=""):
    """The "what happened" of the report (reporting.build_report's info)."""
    when = (before or {}).get("started") or "?"
    ver = (before or {}).get("version") or "?"
    return {"name": name or "", "happened": f"Manticore closed unexpectedly (a run started {when}, {ver}). "
            "Sent from the offer at the next start.", "expected": "", "seed": None}


def report_files(folder=None, journal_path=None):
    """Extra files for that report: the crashed run's Forge log and the unfinished game's journal (its last lines), if any.
    [(name in the zip, text)]."""
    out = []
    log_dir = _log_dir(folder)
    for name, path, limit in ((CRASHED_ENGINE_LOG + ".txt", os.path.join(log_dir, CRASHED_ENGINE_LOG), 600),
                              ("journal_tail.jsonl", journal_path, JOURNAL_LINES)):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                rows = f.read().splitlines()
            out.append((name, "\n".join(rows[-limit:]) + "\n"))
        except OSError:
            continue
    return out
