# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/report_intake.py - Round BUG1: copy new bug reports and ideas from the Discord channel(s) the game posts to (F8, the crash
offer) into a folder of a git branch, so Claude's task-list workers can read them. The game's webhook can only post; this reads
the channel back with a Discord bot that has View Channel + Read Message History there (and its Message Content intent on).

    python tools/report_intake.py --dest DIR [--channels ID[,ID]] [--backfill-days 7] [--api URL] [--no-push]

DIR is a clone of the private repository's branch claude/reports; the tool writes into it and (unless --no-push) commits and
pushes. It runs hourly as a GitHub Actions job of the PUBLIC repository (.github/workflows/report_intake.yml there), so:
  - the bot token comes from the environment (DISCORD_BOT_TOKEN), never the command line;
  - it prints counts only - no report text, no player names, no file names - because a public repository's job log is public.

What it writes (one folder per Discord message, oldest first):
    reports/bug/<YYYYMMDD-HHMMSS>_<message id>/message.json    id, channel, time, the poster's name, the text, the attachments
    reports/bug/<...>/<attachment file names>                   the zip (journal, logs, crash log, screenshot) as the game sent it
    reports/idea/<...>/...                                      the same for "Feature idea from ..." messages
    reports/other/<...>/message.json                            anything else in the channel (text only, no attachments)
    intake_state.json                                           the newest message id read per channel
The first run (no intake_state.json) reads the last --backfill-days days; later runs read only what is newer.
Exit code: 0 (also when nothing was new), 1 on any failure (the job then shows red; nothing half-written is pushed).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Eliminate7532/Manticore, 1.0)"
PAGE = 100                                   # Discord's maximum per request
MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024      # a bug report zip is a few MB; anything bigger is listed, not copied
DISCORD_EPOCH_MS = 1420070400000
STATE = "intake_state.json"
BUG_ZIP = re.compile(r"^bugreport_[\w.-]*\.zip$", re.I)
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class IntakeError(Exception):
    pass


def snowflake_for(when):
    """The smallest Discord id of a message sent at or after `when` (a timezone-aware datetime)."""
    ms = int(when.timestamp() * 1000) - DISCORD_EPOCH_MS
    return str(max(ms, 0) << 22)


def snowflake_time(sid):
    return datetime.datetime.fromtimestamp(((int(sid) >> 22) + DISCORD_EPOCH_MS) / 1000, datetime.timezone.utc)


def kind_of(msg):
    """'bug', 'idea' or 'other', from what the game posts (reporting.summary_text / idea_summary and the zip's name)."""
    content = (msg.get("content") or "").lstrip()
    names = [a.get("filename") or "" for a in msg.get("attachments") or []]
    if content.startswith("**Bug report from") or any(BUG_ZIP.match(n) for n in names):
        return "bug"
    if content.startswith("**Feature idea from"):
        return "idea"
    return "other"


class Discord:
    def __init__(self, token, api=API, opener=None, sleep=time.sleep):
        if not token:
            raise IntakeError("no bot token (DISCORD_BOT_TOKEN is not set)")
        self.token, self.api, self.sleep = token, api.rstrip("/"), sleep
        self.opener = opener or urllib.request.build_opener()

    def _get(self, url, auth=True, tries=5):
        for attempt in range(tries):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            if auth:
                req.add_header("Authorization", "Bot " + self.token)
            try:
                with self.opener.open(req, timeout=60) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                body = e.read()
                e.close()
                if e.code == 429:
                    try:
                        wait = float(json.loads(body.decode("utf-8")).get("retry_after", 1.0))
                    except (ValueError, AttributeError):
                        wait = 1.0
                    self.sleep(min(max(wait, 0.5), 60))
                    continue
                if e.code in (401, 403):
                    raise IntakeError("Discord refused the bot (HTTP %d): check the token, that the bot is in the server, can "
                                      "View Channel and Read Message History there, and has its Message Content intent on"
                                      % e.code)
                if e.code == 404:
                    raise IntakeError("Discord has no channel with that id, or the bot can't see it (HTTP 404)")
                if 500 <= e.code < 600 and attempt + 1 < tries:
                    self.sleep(2 * (attempt + 1))
                    continue
                raise IntakeError("Discord answered HTTP %d" % e.code)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt + 1 < tries:
                    self.sleep(2 * (attempt + 1))
                    continue
                raise IntakeError("Discord could not be reached: %s" % type(e).__name__)
        raise IntakeError("Discord kept asking to slow down (HTTP 429)")

    def messages_after(self, channel, after):
        """Every message of the channel newer than the id `after`, oldest first (Discord pages newest first)."""
        out, cursor = [], after
        while True:
            q = urllib.parse.urlencode({"after": cursor, "limit": PAGE})
            page = json.loads(self._get("%s/channels/%s/messages?%s" % (self.api, channel, q)).decode("utf-8"))
            if not isinstance(page, list):
                raise IntakeError("Discord's answer was not a list of messages")
            if not page:
                break
            page.sort(key=lambda m: int(m["id"]))
            out.extend(page)
            cursor = page[-1]["id"]
            if len(page) < PAGE:
                break
        seen, unique = set(), []
        for m in sorted(out, key=lambda m: int(m["id"])):
            if m["id"] not in seen:
                seen.add(m["id"])
                unique.append(m)
        return unique

    def download(self, url):
        return self._get(url, auth=False)


def folder_name(msg):
    t = snowflake_time(msg["id"]).strftime("%Y%m%d-%H%M%S")
    return "%s_%s" % (t, msg["id"])


def save_message(dest, channel, msg, discord):
    """Write one message (and, for bugs and ideas, its attachments) into dest/reports/<kind>/<folder>/. Returns the kind."""
    kind = kind_of(msg)
    folder = os.path.join(dest, "reports", kind, folder_name(msg))
    os.makedirs(folder, exist_ok=True)
    atts = []
    for a in msg.get("attachments") or []:
        name = SAFE_NAME.sub("_", os.path.basename(a.get("filename") or "file")) or "file"
        row = {"filename": name, "size": a.get("size"), "content_type": a.get("content_type")}
        if kind == "other":
            row["copied"] = False
        elif (a.get("size") or 0) > MAX_ATTACHMENT_BYTES:
            row["copied"] = False
            row["why"] = "bigger than %d MB" % (MAX_ATTACHMENT_BYTES // (1024 * 1024))
        else:
            data = discord.download(a["url"])
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
            row["copied"] = True
            row["bytes"] = len(data)
        atts.append(row)
    info = {"id": msg["id"], "channel": channel, "kind": kind, "time": snowflake_time(msg["id"]).isoformat(),
            "timestamp": msg.get("timestamp"), "poster": (msg.get("author") or {}).get("username"),
            "webhook": bool(msg.get("webhook_id")), "content": msg.get("content") or "", "attachments": atts}
    with open(os.path.join(folder, "message.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1, ensure_ascii=False)
    return kind


def load_state(dest):
    try:
        with open(os.path.join(dest, STATE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"channels": {}}


def run(dest, channels, discord, backfill_days=7, now=None):
    """Read every channel; write new messages and the state. Returns {"bug": n, "idea": n, "other": n}."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    state = load_state(dest)
    counts = {"bug": 0, "idea": 0, "other": 0}
    for ch in channels:
        last = state["channels"].get(ch) or snowflake_for(now - datetime.timedelta(days=backfill_days))
        msgs = discord.messages_after(ch, last)
        for m in msgs:
            counts[save_message(dest, ch, m, discord)] += 1
        if msgs:
            last = msgs[-1]["id"]
        state["channels"][ch] = last
    state["last_run"] = now.isoformat(timespec="seconds")
    with open(os.path.join(dest, STATE), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    return counts


def git(args, cwd):
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def commit_and_push(dest, counts, branch, tries=5, sleep=time.sleep):
    """Commit what run() wrote and push; when someone pushed in between, rebase on theirs and try again."""
    git(["add", "-A"], dest)
    total = sum(counts.values())
    msg = "intake: %d new (bug reports %d, ideas %d, other %d)" % (total, counts["bug"], counts["idea"], counts["other"])
    code, out = git(["commit", "-q", "-m", msg], dest)
    if code != 0 and "nothing to commit" not in out:
        raise IntakeError("git commit failed")
    for i in range(tries):
        code, _out = git(["push", "-q", "origin", "HEAD:" + branch], dest)
        if code == 0:
            return True
        git(["pull", "-q", "--rebase", "origin", branch], dest)
        sleep(2 + i)
    raise IntakeError("git push kept failing")


def main(argv=None, env=os.environ, now=None):
    ap = argparse.ArgumentParser(description="Copy new bug reports from Discord into claude/reports (round BUG1).")
    ap.add_argument("--dest", required=True, help="a clone of the private repository's branch claude/reports")
    ap.add_argument("--channels", default=None, help="channel ids, comma-separated (default: DISCORD_CHANNEL_IDS)")
    ap.add_argument("--backfill-days", type=float, default=7.0)
    ap.add_argument("--api", default=env.get("DISCORD_API") or API)
    ap.add_argument("--branch", default="claude/reports")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    channels = [c.strip() for c in (args.channels or env.get("DISCORD_CHANNEL_IDS") or "").split(",") if c.strip()]
    try:
        if not channels or not all(c.isdigit() for c in channels):
            raise IntakeError("no channel ids (DISCORD_CHANNEL_IDS: the channel's id, digits only; several separated by commas)")
        counts = run(args.dest, channels, Discord(env.get("DISCORD_BOT_TOKEN"), args.api), args.backfill_days, now)
        if not args.no_push:
            commit_and_push(args.dest, counts, args.branch)
    except IntakeError as e:
        print("report intake FAILED: %s" % e)
        return 1
    print("report intake: %d channel(s) read; new: %d bug report(s), %d idea(s), %d other message(s)"
          % (len(channels), counts["bug"], counts["idea"], counts["other"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
