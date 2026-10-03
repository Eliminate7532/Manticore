# SPDX-License-Identifier: GPL-3.0-or-later
"""
backup_banner.py - Round 27a section 2: tells the deck screen, and optionally Discord, when backups
have stopped working. Reads backup_status.json (written by backup.py's write_status()); never runs
backup.py or touches git itself, so it works even on a copy where those aren't set up.

    problem(folder)              None, or why backups are unhappy right now
    banner_text(folder)          None, or the one line the deck screen shows in orange
    maybe_alert_discord(folder)  posts once per failure streak, if a webhook is configured
"""
import datetime
import json
import os
import socket

import reporting

STATUS_FILE = "backup_status.json"
STALE_HOURS = 2                                    # a good backup older than this, while the scheduled task should be
                                                     # running twice an hour, counts as stopped rather than just slow


def _read(folder):
    try:
        with open(os.path.join(folder, STATUS_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _parse(iso):
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso)
    except ValueError:
        return None


def problem(folder, now=None):
    """None when backups look fine, or backup_status.json doesn't exist yet (backups may simply not be
    set up on this copy - that is not itself a problem). Otherwise a short sentence: the stored error,
    or that the last good backup is more than STALE_HOURS old."""
    data = _read(folder)
    if not data:
        return None
    now = now or datetime.datetime.now()
    last_ok, last_error = _parse(data.get("last_ok")), _parse(data.get("last_error"))
    if last_error and (not last_ok or last_error > last_ok):
        return (data.get("error") or "unknown error").splitlines()[0][:200]
    if last_ok and now - last_ok > datetime.timedelta(hours=STALE_HOURS):
        return f"nothing backed up since {last_ok:%Y-%m-%d %H:%M}"
    return None


def banner_text(folder, now=None):
    """The deck-screen line, or None to show nothing (fresh, or backups never run here)."""
    p = problem(folder, now)
    if not p:
        return None
    data = _read(folder) or {}
    since = _parse(data.get("last_error")) or _parse(data.get("last_ok"))
    when = f"{since:%H:%M}" if since else "an unknown time"
    return f"Backups are failing since {when}: {p}. See backup.log."


def maybe_alert_discord(folder, now=None):
    """Posts once per failure streak - a repeat call for the SAME last_error timestamp is a no-op, so this
    is safe to call every time the deck screen opens. Never includes the webhook address, a file path, or
    a traceback (write_status() never stores one - see backup.run_backup())."""
    data = _read(folder)
    if not data:
        return
    p = problem(folder, now)
    if not p:
        return
    if data.get("last_error") and data.get("alerted_at") == data.get("last_error"):
        return
    url = reporting.webhook_url(folder)
    if not url:
        return                                                  # not set up: skip silently (OPEN_QUESTIONS C1)
    pc = socket.gethostname()
    text = f"Manticore backup failing on {pc} since {data.get('last_error') or '?'}: {p}"
    ok, _kind, _msg = reporting.post_to_discord(url, text)
    if ok:
        _mark_alerted(folder, data)


def _mark_alerted(folder, data):
    data = dict(data)
    data["alerted_at"] = data.get("last_error")
    path = os.path.join(folder, STATUS_FILE)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass                                                    # same principle as write_status: never break anything over this
