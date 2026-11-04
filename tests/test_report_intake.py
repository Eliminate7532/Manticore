# SPDX-License-Identifier: GPL-3.0-or-later
"""Round BUG1: tools/report_intake.py against a stand-in Discord API (a local HTTP server) and a throwaway git remote."""
import datetime
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from shutil import which
from urllib.parse import parse_qs, urlparse

from tools import report_intake as ri

NOW = datetime.datetime(2026, 11, 2, 18, 0, tzinfo=datetime.timezone.utc)


def sid(when):
    """A Discord id for a message sent at `when`."""
    return str(((int(when.timestamp() * 1000) - ri.DISCORD_EPOCH_MS) << 22) + 1)


class FakeDiscord:
    """messages: {channel: [message, ...]}; files: {path: bytes}. Serves the two endpoints the tool uses."""

    def __init__(self):
        self.messages, self.files, self.token = {}, {}, "TEST-TOKEN"
        self.throttle_once, self.requests = False, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                fake.requests.append(self.path)
                if u.path.startswith("/files/"):
                    data = fake.files.get(u.path)
                    return self._send(200, data, "application/zip") if data is not None else self._send(404, b"{}")
                parts = u.path.strip("/").split("/")          # api/v10/channels/<id>/messages
                if self.headers.get("Authorization") != "Bot " + fake.token:
                    return self._send(401, b'{"message": "401: Unauthorized"}')
                if fake.throttle_once:
                    fake.throttle_once = False
                    return self._send(429, b'{"retry_after": 0.01}')
                if len(parts) < 2 or parts[-1] != "messages" or parts[-2] not in fake.messages:
                    return self._send(404, b'{"message": "Unknown Channel"}')
                q = parse_qs(u.query)
                after, limit = int(q["after"][0]), int(q.get("limit", ["50"])[0])
                newer = sorted((m for m in fake.messages[parts[-2]] if int(m["id"]) > after), key=lambda m: int(m["id"]))
                page = list(reversed(newer[:limit]))           # Discord: the messages closest after `after`, newest first
                self._send(200, json.dumps(page).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, channel, when, content, attachments=(), webhook=True, bot=False, author="Manticore bug report"):
        m = {"id": sid(when), "content": content, "timestamp": when.isoformat(), "author": {"username": author},
             "attachments": []}
        if webhook:
            m["webhook_id"] = "123"
        if bot:
            m["author"]["bot"] = True
        for name, data in attachments:
            path = "/files/%s/%s" % (m["id"], name)
            self.files[path] = data
            m["attachments"].append({"id": m["id"], "filename": name, "size": len(data), "url": self.base + path,
                                     "content_type": "application/zip"})
        self.messages.setdefault(channel, []).append(m)
        return m

    def close(self):
        self.server.shutdown()


def _git(args, cwd):
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError("git %s: %s" % (args, p.stderr))
    return p.stdout


@unittest.skipUnless(which("git"), "needs git")
class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.fake = FakeDiscord()
        self.api = self.fake.base + "/api/v10"
        self.origin = os.path.join(self.d, "origin.git")
        _git(["init", "-q", "--bare", self.origin], self.d)
        seed = os.path.join(self.d, "seed")
        _git(["init", "-q", "-b", "claude/reports", seed], self.d)
        with open(os.path.join(seed, "README.md"), "w") as f:
            f.write("reports\n")
        _git(["add", "-A"], seed)
        _git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "start"], seed)
        _git(["push", "-q", self.origin, "claude/reports"], seed)
        self.dest = self.clone("dest")

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()

    def clone(self, name):
        p = os.path.join(self.d, name)
        _git(["clone", "-q", "-b", "claude/reports", self.origin, p], self.d)
        _git(["config", "user.name", "intake"], p)
        _git(["config", "user.email", "intake@x"], p)
        return p

    def intake(self, now=NOW, channels="111", token="TEST-TOKEN", push=True):
        out = io.StringIO()
        env = {"DISCORD_BOT_TOKEN": token, "DISCORD_CHANNEL_IDS": channels}
        argv = ["--dest", self.dest, "--api", self.api]
        if not push:
            argv.append("--no-push")
        with redirect_stdout(out):
            rc = ri.main(argv, env=env, now=now)
        return rc, out.getvalue()

    def remote_files(self):
        return _git(["ls-tree", "-r", "--name-only", "claude/reports"], self.origin).split()

    def test_reports_ideas_and_others_are_copied_and_pushed(self):
        zip_bytes = os.urandom(4000)
        self.fake.add("111", NOW - datetime.timedelta(days=30), "**Bug report from Old** too old for the first run")
        bug = self.fake.add("111", NOW - datetime.timedelta(hours=5), "**Bug report from Sam** | Manticore 0.28.53.13\n"
                            "**What happened:** the stack froze", [("bugreport_20261102_120000_Sam.zip", zip_bytes)])
        self.fake.add("111", NOW - datetime.timedelta(hours=4), "**Feature idea from Ann** | Manticore 0.28.53.13\n**Idea:** x")
        self.fake.add("111", NOW - datetime.timedelta(hours=3), "hello channel", [("cat.png", b"png")], webhook=False, bot=True)
        rc, out = self.intake()
        self.assertEqual(rc, 0, out)
        self.assertIn("new: 1 bug report(s), 1 idea(s), 1 other message(s)", out)
        files = self.remote_files()
        folder = "reports/bug/%s_%s" % (ri.snowflake_time(bug["id"]).strftime("%Y%m%d-%H%M%S"), bug["id"])
        self.assertIn(folder + "/message.json", files)
        self.assertIn(folder + "/bugreport_20261102_120000_Sam.zip", files)
        data = subprocess.run(["git", "show", "claude/reports:" + folder + "/bugreport_20261102_120000_Sam.zip"],
                              cwd=self.origin, capture_output=True).stdout
        self.assertEqual(hashlib.sha256(data).hexdigest(), hashlib.sha256(zip_bytes).hexdigest())
        self.assertEqual(sum(1 for f in files if f.startswith("reports/idea/") and f.endswith("message.json")), 1)
        others = [f for f in files if f.startswith("reports/other/")]
        self.assertEqual(len(others), 1, others)                          # another bot's message: its picture is not copied
        self.assertFalse(any("Old" in f for f in files))                  # older than the first run's 7 days
        info = json.loads(subprocess.run(["git", "show", "claude/reports:" + folder + "/message.json"], cwd=self.origin,
                                         capture_output=True, text=True).stdout)
        self.assertEqual(info["kind"], "bug")
        self.assertIn("the stack froze", info["content"])

    def test_a_message_posted_by_hand_is_a_bug_report_with_its_pictures(self):
        """Round BUG1b - Karl, 2 Nov 20:37 CST: "things I manually add to the bug reports channel should be taken as if they were
        reported from the game"."""
        png = os.urandom(300)
        m = self.fake.add("111", NOW - datetime.timedelta(hours=2), "The game crashed when I cast Doubling Season",
                          [("crash.png", png)], webhook=False, author="kr.bot.01")
        rc, out = self.intake()
        self.assertEqual(rc, 0, out)
        self.assertIn("new: 1 bug report(s), 0 idea(s), 0 other message(s)", out)
        folder = "reports/bug/%s_%s" % (ri.snowflake_time(m["id"]).strftime("%Y%m%d-%H%M%S"), m["id"])
        self.assertIn(folder + "/crash.png", self.remote_files())
        data = subprocess.run(["git", "show", "claude/reports:" + folder + "/crash.png"], cwd=self.origin, capture_output=True).stdout
        self.assertEqual(data, png)
        info = json.loads(subprocess.run(["git", "show", "claude/reports:" + folder + "/message.json"], cwd=self.origin,
                                         capture_output=True, text=True).stdout)
        self.assertEqual((info["kind"], info["manual"], info["webhook"], info["bot"], info["poster"]),
                         ("bug", True, False, False, "kr.bot.01"))
        self.assertNotIn("kr.bot.01", out)                                # the public log still shows counts only
        self.assertNotIn("Doubling Season", out)

    def test_the_log_shows_counts_only(self):
        self.fake.add("111", NOW - datetime.timedelta(hours=1), "**Bug report from Zebediah** secret words here",
                      [("bugreport_20261102_170000_Zebediah.zip", b"zz")])
        rc, out = self.intake()
        self.assertEqual(rc, 0)
        self.assertNotIn("Zebediah", out)
        self.assertNotIn("secret words", out)
        self.assertNotIn("bugreport_", out)

    def test_a_second_run_takes_only_what_is_new(self):
        self.fake.add("111", NOW - datetime.timedelta(hours=2), "**Bug report from A**", [("bugreport_a.zip", b"a")])
        self.assertEqual(self.intake()[0], 0)
        self.fake.add("111", NOW - datetime.timedelta(hours=1), "**Bug report from B**", [("bugreport_b.zip", b"b")])
        rc, out = self.intake()
        self.assertIn("new: 1 bug report(s), 0 idea(s)", out)
        bugs = [f for f in self.remote_files() if f.startswith("reports/bug/") and f.endswith("message.json")]
        self.assertEqual(len(bugs), 2)
        rc, out = self.intake()
        self.assertIn("new: 0 bug report(s)", out)

    def test_many_messages_are_paged_in_order(self):
        for i in range(230):
            self.fake.add("111", NOW - datetime.timedelta(minutes=300 - i), "**Feature idea from P%d**" % i)
        rc, out = self.intake(push=False)
        self.assertIn("230 idea(s)", out)
        with open(os.path.join(self.dest, ri.STATE)) as f:
            state = json.load(f)
        self.assertEqual(state["channels"]["111"], self.fake.messages["111"][-1]["id"])    # the newest, not the oldest
        self.assertGreaterEqual(sum(1 for r in self.fake.requests if "/messages" in r), 3)

    def test_slow_down_is_obeyed(self):
        self.fake.throttle_once = True
        self.fake.add("111", NOW - datetime.timedelta(hours=1), "**Feature idea from Q**")
        rc, out = self.intake(push=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("1 idea(s)", out)

    def test_a_refused_bot_fails_in_words_and_pushes_nothing(self):
        before = self.remote_files()
        rc, out = self.intake(token="WRONG")
        self.assertEqual(rc, 1)
        self.assertIn("Discord refused the bot (HTTP 401)", out)
        self.assertNotIn("WRONG", out)
        self.assertEqual(self.remote_files(), before)

    def test_bad_channel_ids_are_refused(self):
        self.assertEqual(self.intake(channels="")[0], 1)
        self.assertEqual(self.intake(channels="abc")[0], 1)
        rc, out = self.intake(channels="999")
        self.assertEqual(rc, 1)
        self.assertIn("HTTP 404", out)

    def test_a_huge_attachment_is_listed_not_copied(self):
        m = self.fake.add("111", NOW - datetime.timedelta(hours=1), "**Bug report from Big**", [("bugreport_big.zip", b"x")])
        m["attachments"][0]["size"] = ri.MAX_ATTACHMENT_BYTES + 1
        self.assertEqual(self.intake()[0], 0)
        files = self.remote_files()
        self.assertFalse(any(f.endswith("bugreport_big.zip") for f in files))
        mj = [f for f in files if f.endswith("message.json")][0]
        info = json.loads(subprocess.run(["git", "show", "claude/reports:" + mj], cwd=self.origin, capture_output=True,
                                         text=True).stdout)
        self.assertFalse(info["attachments"][0]["copied"])

    def test_someone_else_pushing_first_is_rebased_on(self):
        other = self.clone("other")
        with open(os.path.join(other, "note.txt"), "w") as f:
            f.write("a worker wrote this\n")
        _git(["add", "-A"], other)
        _git(["commit", "-q", "-m", "other"], other)
        _git(["push", "-q", "origin", "claude/reports"], other)
        self.fake.add("111", NOW - datetime.timedelta(hours=1), "**Bug report from R**", [("bugreport_r.zip", b"r")])
        rc, out = self.intake()
        self.assertEqual(rc, 0, out)
        files = self.remote_files()
        self.assertIn("note.txt", files)
        self.assertTrue(any(f.endswith("bugreport_r.zip") for f in files))


class KindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(ri.kind_of({"content": "**Bug report from X** | ..."}), "bug")
        self.assertEqual(ri.kind_of({"content": "", "attachments": [{"filename": "bugreport_1_X.zip"}]}), "bug")
        self.assertEqual(ri.kind_of({"content": "**Feature idea from X**"}), "idea")
        self.assertEqual(ri.kind_of({"content": "hi", "webhook_id": "1", "attachments": [{"filename": "x.zip"}]}), "other")
        self.assertEqual(ri.kind_of({"content": "hi", "author": {"bot": True}}), "other")         # another bot
        self.assertEqual(ri.kind_of({"content": "the game crashed", "author": {"username": "kr"}}), "bug")   # by hand

    def test_ids_and_times_agree(self):
        when = datetime.datetime(2026, 10, 1, 7, 3, 7, tzinfo=datetime.timezone.utc)
        self.assertEqual(ri.snowflake_time(ri.snowflake_for(when)), when)


if __name__ == "__main__":
    unittest.main()
