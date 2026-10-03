# SPDX-License-Identifier: GPL-3.0-or-later

"""
bug_report.py - Simple in-app bug reporting for playtesters.

Lets a tester file a bug report from inside the running app. Reports are
saved locally as timestamped text files (no network/server needed yet) -
good enough for solo and friend playtesting. Can be upgraded later to
email/webhook submission once there's a place for reports to go.
"""
import os
import json
import platform
from datetime import datetime, timezone

REPORTS_DIR = "bug_reports"


def _ensure_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


def file_bug_report(description, tester_name="anonymous", game_state=None, extra=None):
    """
    Save a bug report to a local timestamped file.
    game_state: optional GameState/PodGameState instance - if given, its log
                and player board summaries are captured automatically so the
                tester doesn't have to describe the whole board by hand.
    extra: optional dict of any additional structured info to attach.
    """
    _ensure_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{REPORTS_DIR}/bug_{timestamp}.json"

    report = {
        "timestamp_utc": timestamp,
        "tester_name": tester_name,
        "description": description,
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "extra": extra or {},
    }

    if game_state is not None:
        try:
            report["game_log_tail"] = game_state.log[-30:]
            report["turn_number"] = game_state.turn_number
            report["players"] = [p.board_summary() for p in game_state.players]
        except Exception as e:
            report["game_state_capture_error"] = str(e)

    with open(filename, "w") as f:
        json.dump(report, f, indent=2)

    return filename


def list_bug_reports():
    _ensure_dir()
    return sorted(os.listdir(REPORTS_DIR))


def export_all_reports_as_text(output_file="bug_reports_export.txt"):
    """Bundle all bug reports into a single readable text file - handy for
    a playtester to send back to the developer without hunting for files."""
    _ensure_dir()
    reports = list_bug_reports()
    with open(output_file, "w") as out:
        out.write(f"Bug Report Export - {len(reports)} report(s)\n")
        out.write("=" * 50 + "\n\n")
        for fname in reports:
            with open(f"{REPORTS_DIR}/{fname}") as f:
                data = json.load(f)
            out.write(f"--- {fname} ---\n")
            out.write(f"Tester: {data.get('tester_name')}\n")
            out.write(f"Time (UTC): {data.get('timestamp_utc')}\n")
            out.write(f"Description: {data.get('description')}\n")
            if "game_log_tail" in data:
                out.write("Recent game log:\n")
                for line in data["game_log_tail"]:
                    out.write(f"  {line}\n")
            out.write("\n")
    return output_file
