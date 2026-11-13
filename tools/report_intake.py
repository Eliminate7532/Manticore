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
    reports/bug/<...>/...                                       also a message a PERSON posted in the channel by hand, with its
                                                                pictures and files (round BUG1b - Karl, 2 Nov: "things I manually
                                                                add to the bug reports channel should be taken as if they were
                                                                reported from the game"); message.json "manual": true
    reports/reply/<...>/...                                     round INTAKE2: a message a person posted that answers a report
                                                                already in reports/ - a Discord reply to it ("reply_to"), or a
                                                                message in a thread started from it ("thread",
                                                                "thread_parent_message") - with its pictures and files
    reports/other/<...>/message.json                            anything else - another bot or webhook, or Discord's own notices
                                                                ("started a thread"; text only, no attachments)
    intake_state.json                                           the newest message id read per channel and per thread

Threads (round INTAKE2, Karl 8 Nov 2026): each channel's threads are read too - the active ones (the server's active-threads
list, kept to threads whose parent is the channel) and the archived public ones archived since the last run. The first run with
threads reads those with activity in the last --backfill-days days. Needs the bot's View Channel + Read Message History on the
channel (threads inherit them; listing archived public threads needs Read Message History); private threads are not read. When
the threads can't be read, the channel's own messages are still written and pushed and the run ends red (exit code 1).
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
KINDS = ("bug", "idea", "reply", "other")
PERSON_TYPES = (0, 19)                       # Discord message types a person types: a message, a reply (others are notices)
THREAD_STARTER = 21                          # the copy of the start message Discord puts at the top of a thread


class IntakeError(Exception):
    pass


def snowflake_for(when):
    """The smallest Discord id of a message sent at or after `when` (a timezone-aware datetime)."""
    ms = int(when.timestamp() * 1000) - DISCORD_EPOCH_MS
    return str(max(ms, 0) << 22)


def snowflake_time(sid):
    return datetime.datetime.fromtimestamp(((int(sid) >> 22) + DISCORD_EPOCH_MS) / 1000, datetime.timezone.utc)


def by_hand(msg):
    """A message a person typed into the channel: not the game's webhook, not a bot (round BUG1b), and not one of Discord's own
    notices such as "<name> started a thread" (round INTAKE2: message types other than a plain message or a reply)."""
    return (not msg.get("webhook_id") and not (msg.get("author") or {}).get("bot")
            and msg.get("type", 0) in PERSON_TYPES)


def reply_to(msg):
    """The id of the message this one is a Discord reply to (message_reference), or None. A thread's starter copy and a
    forwarded message carry a reference too, but they are not replies."""
    if msg.get("type", 0) != 19:
        return None
    return (msg.get("message_reference") or {}).get("message_id")


def kind_of(msg, answers=None):
    """'bug', 'idea', 'reply' or 'other', from what the game posts (reporting.summary_text / idea_summary and the zip's name).
    Round BUG1b: whatever a person posts by hand is a bug report too (Karl's own notes in the channel). Round INTAKE2: unless it
    answers a report already copied (`answers`: the id of that report, from reply_to or a thread's start) - then it is a reply."""
    content = (msg.get("content") or "").lstrip()
    names = [a.get("filename") or "" for a in msg.get("attachments") or []]
    if content.startswith("**Bug report from") or any(BUG_ZIP.match(n) for n in names):
        return "bug"
    if content.startswith("**Feature idea from"):
        return "idea"
    if by_hand(msg) and answers:
        return "reply"
    if by_hand(msg):
        return "bug"
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

    def _json(self, path, query=None):
        url = "%s/%s" % (self.api, path) + ("?" + urllib.parse.urlencode(query) if query else "")
        return json.loads(self._get(url).decode("utf-8"))

    def channel(self, channel):
        """The channel itself (its guild_id)."""
        info = self._json("channels/%s" % channel)
        if not isinstance(info, dict):
            raise IntakeError("Discord's answer about the channel was not an object")
        return info

    def threads(self, channel, since):
        """The channel's threads that may have new messages: every active one (the server's active-threads list, kept to this
        parent) and every archived public one archived at or after `since` (a datetime). Each is Discord's thread object."""
        guild = self.channel(channel).get("guild_id")
        found = {}
        if guild:
            active = self._json("guilds/%s/threads/active" % guild)
            for t in (active.get("threads") if isinstance(active, dict) else None) or []:
                if str(t.get("parent_id")) == str(channel):
                    found[t["id"]] = t
        before = None
        while True:
            q = {"limit": PAGE}
            if before:
                q["before"] = before
            page = self._json("channels/%s/threads/archived/public" % channel, q)
            if not isinstance(page, dict):
                raise IntakeError("Discord's answer about archived threads was not an object")
            items = page.get("threads") or []
            older = False
            for t in items:
                when = archive_time(t)
                if when is not None and when < since:
                    older = True
                    continue
                found.setdefault(t["id"], t)
            if not items or older or not page.get("has_more"):
                break
            last = (items[-1].get("thread_metadata") or {}).get("archive_timestamp")
            if not last or last == before:
                break
            before = last
        return [found[k] for k in sorted(found, key=int)]

    def download(self, url):
        return self._get(url, auth=False)


def archive_time(thread):
    stamp = (thread.get("thread_metadata") or {}).get("archive_timestamp")
    if not stamp:
        return None
    try:
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def known_reports(dest):
    """The Discord ids of every message already copied (any kind), from the folder names under dest/reports/."""
    ids = set()
    for kind in KINDS:
        base = os.path.join(dest, "reports", kind)
        if os.path.isdir(base):
            ids.update(name.rsplit("_", 1)[-1] for name in os.listdir(base))
    return ids


def folder_name(msg):
    t = snowflake_time(msg["id"]).strftime("%Y%m%d-%H%M%S")
    return "%s_%s" % (t, msg["id"])


def save_message(dest, channel, msg, discord, known=frozenset(), thread=None, thread_parent=None):
    """Write one message (and, for bugs, ideas and replies, its attachments) into dest/reports/<kind>/<folder>/. Returns the
    kind. `known`: the ids already copied (a reply to one of them, or a message in a thread started from one, is a reply);
    `thread`: the thread's id when the message is in a thread, `thread_parent`: the message that thread was started from."""
    ref = reply_to(msg)
    answers = next((m for m in (ref, thread_parent) if m and m in known), None)
    kind = kind_of(msg, answers)
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
            "poster_name": (msg.get("author") or {}).get("global_name"),
            "webhook": bool(msg.get("webhook_id")), "bot": bool((msg.get("author") or {}).get("bot")),
            "manual": by_hand(msg), "content": msg.get("content") or "", "attachments": atts,
            "type": msg.get("type", 0), "reply_to": ref, "thread": thread, "thread_parent_message": thread_parent,
            "answers": answers}
    with open(os.path.join(folder, "message.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1, ensure_ascii=False)
    return kind


def load_state(dest):
    try:
        with open(os.path.join(dest, STATE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"channels": {}}


def thread_parent_of(thread, msgs, known):
    """The message a thread was started from: the reference of Discord's starter copy at its top, else the thread's own id when
    that is a copied message (a thread started from a message has the message's id), else None."""
    for m in msgs:
        if m.get("type") == THREAD_STARTER and (m.get("message_reference") or {}).get("message_id"):
            return m["message_reference"]["message_id"]
    return thread["id"] if thread["id"] in known else None


def read_threads(dest, ch, discord, threads_state, parents, since, floor, known, counts):
    """Every new message in the channel's threads (round INTAKE2): changes threads_state / parents in place. A thread not seen
    before is read from its start, but never from before `floor` (the backfill's first id)."""
    for t in discord.threads(ch, since):
        tid = t["id"]
        last = threads_state.get(tid) or str(max(int(tid) - 1, floor))
        if t.get("last_message_id") and int(t["last_message_id"]) <= int(last):
            threads_state[tid] = last
            continue
        msgs = discord.messages_after(tid, last)
        parent = parents.get(tid) or thread_parent_of(t, msgs, known)
        if parent:
            parents[tid] = parent
        for m in msgs:
            if m.get("type") == THREAD_STARTER:
                continue
            counts[save_message(dest, ch, m, discord, known, thread=tid, thread_parent=parent)] += 1
            known.add(m["id"])
        if msgs:
            last = msgs[-1]["id"]
        threads_state[tid] = last
        counts["threads"] += 1


def run(dest, channels, discord, backfill_days=7, now=None):
    """Read every channel and its threads; write new messages and the state. Returns {"bug": n, "idea": n, "reply": n,
    "other": n, "threads": threads read, "thread_errors": [why, ...]}. A channel whose threads can't be read keeps its old
    thread state (they are read again next time) - its own messages are written all the same."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    state = load_state(dest)
    counts = {"bug": 0, "idea": 0, "reply": 0, "other": 0, "threads": 0, "thread_errors": []}
    known = known_reports(dest)
    cutoff = now - datetime.timedelta(days=backfill_days)
    floor = int(snowflake_for(cutoff))
    first = "threads" not in state          # the first run with threads: read those active in the last backfill days
    since = cutoff
    if not first and state.get("last_run"):
        try:
            since = max(cutoff, datetime.datetime.fromisoformat(state["last_run"]) - datetime.timedelta(days=1))
        except ValueError:
            pass
    threads_state = dict(state.get("threads") or {})
    parents = dict(state.get("thread_parents") or {})
    for ch in channels:
        last = state["channels"].get(ch) or snowflake_for(cutoff)
        msgs = discord.messages_after(ch, last)
        for m in msgs:
            counts[save_message(dest, ch, m, discord, known)] += 1
            known.add(m["id"])
        if msgs:
            last = msgs[-1]["id"]
        state["channels"][ch] = last
        trial_threads, trial_parents = dict(threads_state), dict(parents)
        try:
            read_threads(dest, ch, discord, trial_threads, trial_parents, since, floor, known, counts)
        except IntakeError as e:
            counts["thread_errors"].append(str(e))
            continue
        threads_state, parents = trial_threads, trial_parents
    if not (first and counts["thread_errors"]):
        state["threads"], state["thread_parents"] = threads_state, parents
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
    total = sum(counts.get(k, 0) for k in KINDS)
    msg = "intake: %d new (bug reports %d, ideas %d, replies %d, other %d)" % (
        total, counts["bug"], counts["idea"], counts.get("reply", 0), counts["other"])
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
    print("report intake: %d channel(s) read; new: %d bug report(s), %d idea(s), %d other message(s), %d reply(ies); "
          "%d thread(s) read" % (len(channels), counts["bug"], counts["idea"], counts["other"], counts["reply"],
                                 counts["threads"]))
    if counts["thread_errors"]:
        print("report intake FAILED for threads (the channel's own messages were copied): %s" % counts["thread_errors"][0])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
