# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for reporting.py: the report zip, the size cap, path scrubbing, the Discord webhook rules and the upload itself
(against a small web server on this computer, so nothing leaves the machine)."""
import email
import http.server
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
import zipfile

import reporting
from tests.forge_fake import load_state

FAKE_HOME = "C:\\Users\\Friend"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name

    def write(self, name, text):
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class ScrubTests(unittest.TestCase):
    def test_the_users_folder_becomes_a_tilde(self):
        text = f"file {FAKE_HOME}\\Documents\\commander_sim\\forge_table.py line 5"
        out = reporting.scrub(text, home=FAKE_HOME)
        self.assertNotIn("Friend", out)
        self.assertIn("~\\Documents\\commander_sim", out)

    def test_forward_slashes_and_any_other_users_folder_too(self):
        self.assertNotIn("Friend", reporting.scrub("C:/Users/Friend/x.py", home=FAKE_HOME))
        self.assertEqual(reporting.scrub("D:\\Users\\Bob\\y", home=FAKE_HOME), "~\\y")
        self.assertEqual(reporting.scrub("/Users/al/z", home=FAKE_HOME), "~/z")

    def test_plain_text_is_left_alone(self):
        self.assertEqual(reporting.scrub("Karl cast Sol Ring", home=FAKE_HOME), "Karl cast Sol Ring")
        self.assertEqual(reporting.scrub("", home=FAKE_HOME), "")

    def test_typed_text_loses_control_characters_and_is_limited(self):
        self.assertEqual(reporting.clean_field("a\r\nb\rc\td\x00e"), "a\nb\nc    de")
        self.assertEqual(len(reporting.clean_field("x" * 5000)), reporting.FIELD_LIMIT)
        self.assertEqual(reporting.clean_field(None), "")

    def test_file_name_pieces(self):
        self.assertEqual(reporting.slug("Friend A!"), "Friend-A")
        self.assertEqual(reporting.slug(""), "player")
        self.assertLessEqual(len(reporting.slug("x" * 100)), 24)


class WebhookRuleTests(Fixture):
    GOOD = "https://discord.com/api/webhooks/123456789012345678/AbC-dEf_123"

    def test_real_discord_addresses_are_accepted(self):
        for url in (self.GOOD, self.GOOD.replace("discord.com", "ptb.discord.com"), self.GOOD.replace("discord.com", "canary.discord.com"),
                    self.GOOD.replace("discord.com", "discordapp.com"), self.GOOD + "/"):
            with self.subTest(url=url):
                self.assertIsNone(reporting.webhook_problem(url))

    def test_anything_else_is_refused(self):
        for url in ("", None, 5, "http://discord.com/api/webhooks/1/x", "https://evil.example/api/webhooks/1/x",
                    "https://discord.com.evil.example/api/webhooks/1/x", "https://discord.com/api/webhooks/abc/x",
                    "https://discord.com/api/webhooks/1", "https://discord.com/api/webhooks/1/x/extra",
                    "https://evil.example/?https://discord.com/api/webhooks/1/x", "PASTE YOUR WEBHOOK ADDRESS HERE"):
            with self.subTest(url=url):
                self.assertIsNotNone(reporting.webhook_problem(url))

    def test_config_file_reading_is_forgiving(self):
        self.assertEqual(reporting.load_config(self.folder), {})
        self.write(reporting.CONFIG_NAME, "{ this is not json")
        self.assertEqual(reporting.load_config(self.folder), {})
        self.write(reporting.CONFIG_NAME, "[1, 2]")
        self.assertEqual(reporting.load_config(self.folder), {})
        with open(reporting.config_path(self.folder), "wb") as f:                # Notepad adds a byte-order mark
            f.write(b"\xef\xbb\xbf" + json.dumps({"discord_webhook": self.GOOD}).encode())
        self.assertEqual(reporting.webhook_url(self.folder), self.GOOD)

    def test_status_messages_say_what_is_wrong(self):
        url, why = reporting.config_status(self.folder)
        self.assertIsNone(url)
        self.assertIn("not set up", why)
        self.write(reporting.CONFIG_NAME, json.dumps({"discord_webhook": "PASTE THE ADDRESS HERE"}))
        self.assertIsNone(reporting.config_status(self.folder)[0])
        self.write(reporting.CONFIG_NAME, json.dumps({"discord_webhook": "https://example.com/x"}))
        url, why = reporting.config_status(self.folder)
        self.assertIsNone(url)
        self.assertIn("not a Discord webhook", why)
        self.assertIsNone(reporting.webhook_url(self.folder))
        self.write(reporting.CONFIG_NAME, json.dumps({"discord_webhook": self.GOOD, "owner_name": "Sam"}))
        url, why = reporting.config_status(self.folder)
        self.assertEqual(url, self.GOOD)
        self.assertIn("Sam", why)

    def test_the_owner_is_karl_unless_the_file_says_otherwise(self):
        self.assertEqual(reporting.owner_name(self.folder), "Karl")
        self.write(reporting.CONFIG_NAME, json.dumps({"owner_name": "  Sam  "}))
        self.assertEqual(reporting.owner_name(self.folder), "Sam")


class BuildReportTests(Fixture):
    def setUp(self):
        super().setUp()
        self.write("forge_decks/player.dck", "[Main]\n1 Sol Ring\n")
        self.write("forge_decks/notes.txt", "not a deck")
        self.write("forge_engine.log", "engine line 1\nFile " + FAKE_HOME + "\\x.jar failed\nengine line 3\n")
        self.write("crash_log.txt", "crash entry\n")
        self.state = load_state("main1_start")
        self.info = {"name": "Friend A", "happened": "The mirror copied itself", "expected": "It should copy Monolith", "seed": 42}

    def build(self, **kw):
        args = dict(state=self.state, log_lines=["[LAND] Karl played Forest", "[STACK_ADD] Karl cast Sol Ring"],
                    commands=[(0.0, {"c": "card", "id": 5}), (1.5, {"c": "ok"})],
                    screenshot=lambda w: (b"\xff\xd8fakejpeg" * 50, "jpg"), folder=self.folder)
        args.update(kw)
        return reporting.build_report(self.info, **args)

    def names(self, path):
        with zipfile.ZipFile(path) as z:
            return set(z.namelist())

    def read(self, path, name):
        with zipfile.ZipFile(path) as z:
            return z.read(name).decode("utf-8")

    def test_the_zip_holds_everything_needed(self):
        path = self.build()
        self.assertTrue(path.startswith(os.path.join(self.folder, "bug_reports")))
        self.assertTrue(os.path.basename(path).startswith("bugreport_") and path.endswith("Friend-A.zip"))
        self.assertEqual(self.names(path), {"report.txt", "screenshot.jpg", "state.json", "game_log.txt", "commands.json",
                                            "decks/player.dck", "forge_engine.log.txt", "crash_log.txt"})

    def test_report_txt_says_who_what_and_which_build(self):
        text = self.read(self.build(), "report.txt")
        for needle in ("Friend A", "The mirror copied itself", "It should copy Monolith", "Seed:     42", "Manticore",
                       "code ", "turn 1"):
            self.assertIn(needle, text)

    def test_state_and_commands_come_through_intact(self):
        path = self.build()
        self.assertEqual(json.loads(self.read(path, "state.json"))["me"], self.state["me"])
        cmds = json.loads(self.read(path, "commands.json"))
        self.assertEqual(cmds["seed"], 42)
        self.assertEqual(cmds["commands"][0], {"t": 0.0, "c": "card", "id": 5})
        self.assertEqual(cmds["commands"][1]["c"], "ok")
        self.assertIn("Karl cast Sol Ring", self.read(path, "game_log.txt"))
        self.assertIn("Sol Ring", self.read(path, "decks/player.dck"))

    def test_only_deck_files_are_included(self):
        self.assertNotIn("decks/notes.txt", self.names(self.build()))

    def test_no_state_no_crash_log_no_engine_log_still_works(self):
        os.remove(os.path.join(self.folder, "crash_log.txt"))
        os.remove(os.path.join(self.folder, "forge_engine.log"))
        path = self.build(state=None, commands=None, log_lines=None, screenshot=None)
        names = self.names(path)
        self.assertLessEqual({"report.txt", "game_log.txt", "commands.json"}, names)
        self.assertNotIn("state.json", names)
        self.assertNotIn("crash_log.txt", names)
        self.assertIn("no game running", self.read(path, "report.txt"))

    def test_the_users_folder_never_reaches_the_zip(self):
        home_backup = os.environ.get("USERPROFILE"), os.environ.get("HOME")
        os.environ["HOME"] = os.environ["USERPROFILE"] = FAKE_HOME
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v else os.environ.pop(k, None)
                                 for k, v in zip(("USERPROFILE", "HOME"), home_backup)])
        path = self.build()
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith((".txt", ".json", ".dck")):
                    self.assertNotIn("Friend\\", z.read(name).decode("utf-8", "replace"), name)
        self.assertIn("~", self.read(path, "forge_engine.log.txt"))

    def test_a_failing_screenshot_does_not_lose_the_report(self):
        def boom(width):
            raise RuntimeError("no picture for you")
        path = self.build(screenshot=boom)
        self.assertIn("screenshot_failed.txt", self.names(path))
        self.assertIn("report.txt", self.names(path))

    def test_names_never_collide(self):
        now = __import__("datetime").datetime(2026, 9, 20, 12, 0, 0)
        a, b = self.build(now=now), self.build(now=now)
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_too_big_reports_shrink_the_picture_first(self):
        widths = []

        def shot(width):
            widths.append(width)
            return os.urandom(width * 4000), "jpg"               # incompressible: 1600 wide = 6.4 MB
        path = self.build(screenshot=shot)
        self.assertLessEqual(os.path.getsize(path), reporting.MAX_ZIP_BYTES)
        self.assertEqual(widths[:2], [1600, 1100])
        self.assertIn("screenshot.jpg", self.names(path))

    def test_an_impossible_picture_is_dropped_but_the_rest_is_kept(self):
        path = self.build(screenshot=lambda w: (os.urandom(9_000_000), "jpg"))
        self.assertLessEqual(os.path.getsize(path), reporting.MAX_ZIP_BYTES)
        self.assertNotIn("screenshot.jpg", self.names(path))
        self.assertIn("report.txt", self.names(path))

    def test_huge_logs_are_cut_to_the_newest_lines(self):
        lines = [f"[LOG] line {i} " + "x" * 60 for i in range(50_000)]
        path = self.build(log_lines=lines)
        rows = self.read(path, "game_log.txt").splitlines()
        self.assertLessEqual(len(rows), reporting.LOG_LINES)
        self.assertIn("line 49999", rows[-1])

    def test_a_write_problem_is_not_swallowed_silently_here(self):
        blocker = self.write("blocker", "a file where the folder should be")
        with self.assertRaises(OSError):
            self.build(dest_dir=os.path.join(blocker, "sub"))


class SummaryTests(unittest.TestCase):
    def test_summary_names_the_player_and_fits_in_a_discord_message(self):
        text = reporting.summary_text({"name": "Friend A", "happened": "It broke", "expected": "It works"}, "2026-09-20 12:00")
        self.assertIn("Friend A", text)
        self.assertIn("It broke", text)
        self.assertIn("Expected", text)
        long = reporting.summary_text({"name": "F", "happened": "x" * 5000})
        self.assertLessEqual(len(long), 1900)

    def test_missing_pieces_are_named_not_blank(self):
        text = reporting.summary_text({})
        self.assertIn("someone", text)
        self.assertIn("nothing written", text)


# ---- the upload, against a local server ------------------------------------------------------------------------------------

class Recorder(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.server.delay:
            time.sleep(self.server.delay)
        self.send_response(self.server.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class UploadTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        self.server.requests, self.server.status, self.server.delay = [], 200, 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/webhooks/123/tok_en-1"
        real = reporting.WEBHOOK_RE
        reporting.WEBHOOK_RE = re.compile(r"^http://127\.0\.0\.1:\d+/api/webhooks/\d+/[\w-]+$")      # only for this test
        self.addCleanup(lambda: setattr(reporting, "WEBHOOK_RE", real))
        self.zip = self.write("r.zip", "")
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("report.txt", "hello")
        reporting._last_send[0] = 0.0
        self.addCleanup(lambda: reporting._last_send.__setitem__(0, 0.0))

    def parts(self, req):
        msg = email.message_from_bytes(b"Content-Type: " + req["headers"]["Content-Type"].encode() + b"\r\n\r\n" + req["body"])
        out = {}
        for part in msg.get_payload():
            out[part.get_param("name", header="content-disposition")] = part
        return out

    def test_the_zip_and_the_summary_arrive_with_mentions_switched_off(self):
        ok, kind, message = reporting.post_to_discord(self.url, "**Bug report** @everyone hi", self.zip)
        self.assertEqual((ok, kind), (True, "sent"))
        self.assertIn("Sent", message)
        req = self.server.requests[0]
        self.assertIn("wait=true", req["path"])
        parts = self.parts(req)
        payload = json.loads(parts["payload_json"].get_payload(decode=True))
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn("@everyone hi", payload["content"])
        self.assertEqual(payload["username"], reporting.BOT_NAME)
        data = parts["files[0]"].get_payload(decode=True)
        self.assertEqual(zipfile.ZipFile(io.BytesIO(data)).read("report.txt"), b"hello")
        self.assertEqual(parts["files[0]"].get_filename(), "r.zip")

    def test_a_text_only_message_works_too(self):
        ok, kind, _ = reporting.post_to_discord(self.url, "just a test")
        self.assertEqual((ok, kind), (True, "sent"))
        self.assertEqual(json.loads(self.server.requests[0]["body"])["content"], "just a test")

    def test_204_counts_as_success(self):
        self.server.status = 204
        self.assertEqual(reporting.post_to_discord(self.url, "x", self.zip)[:2], (True, "sent"))

    def test_success_says_the_report_has_been_submitted(self):
        ok, kind, message = reporting.post_to_discord(self.url, "x", self.zip)
        self.assertEqual((ok, kind, message), (True, "sent", "Sent! Your bug report has been submitted."))

    def test_each_kind_of_refusal_gets_its_own_plain_message(self):
        expected = {413: "too_big", 429: "rate", 404: "bad_webhook", 401: "bad_webhook", 403: "bad_webhook", 500: "error", 400: "error"}
        for status, kind in expected.items():
            with self.subTest(status=status):
                self.server.status = status
                ok, got, message = reporting.post_to_discord(self.url, "x", self.zip)
                self.assertFalse(ok)
                self.assertEqual(got, kind)
                self.assertTrue(message)

    def test_a_slow_server_times_out_instead_of_freezing(self):
        self.server.delay = 1.5
        started = time.time()
        ok, kind, _ = reporting.post_to_discord(self.url, "x", self.zip, timeout=(1, 0.4))
        self.assertEqual((ok, kind), (False, "offline"))
        self.assertLess(time.time() - started, 1.4)

    def test_nobody_listening_means_offline(self):
        self.server.shutdown()
        self.server.server_close()                                   # the port is now closed: connection refused
        ok, kind, message = reporting.post_to_discord(self.url, "x", self.zip, timeout=(1, 1))
        self.assertEqual((ok, kind), (False, "offline"))
        # Linux refuses a closed port at once ("no internet connection?"); Windows keeps retrying it for a couple of seconds, so
        # the 1-second timeout ends it first ("did not answer in time"). Both mean the same to the player: it did not go out.
        self.assertTrue("internet" in message or "in time" in message, message)

    def test_a_missing_zip_is_reported_not_raised(self):
        ok, kind, _ = reporting.post_to_discord(self.url, "x", os.path.join(self.folder, "gone.zip"))
        self.assertEqual((ok, kind), (False, "error"))

    def test_a_bad_address_never_touches_the_network(self):
        reporting.WEBHOOK_RE = re.compile(r"^https://discord\.com/api/webhooks/\d+/[\w-]+$")
        ok, kind, _ = reporting.post_to_discord(self.url, "x", self.zip)
        self.assertEqual((ok, kind), (False, "bad_webhook"))
        self.assertEqual(self.server.requests, [])

    def test_the_background_job_reports_back_and_starts_the_wait(self):
        job = reporting.SendJob(self.url, "hello", self.zip)
        job.start()
        job.join(10)
        self.assertTrue(job.finished)
        self.assertEqual(job.result[:2], (True, "sent"))
        self.assertGreater(reporting.seconds_until_send_allowed(), 50)
        self.assertEqual(reporting.seconds_until_send_allowed(now=time.time() + 61), 0)

    def test_a_failed_send_does_not_start_the_wait(self):
        self.server.status = 500
        job = reporting.SendJob(self.url, "hello", self.zip)
        job.start()
        job.join(10)
        self.assertFalse(job.result[0])
        self.assertEqual(reporting.seconds_until_send_allowed(), 0)

    def test_report_test_needs_the_config_file(self):
        ok, message = reporting.send_test(self.folder)
        self.assertFalse(ok)
        self.assertIn("not set up", message)
        self.write(reporting.CONFIG_NAME, json.dumps({"discord_webhook": self.url}))
        ok, message = reporting.send_test(self.folder, name="Tester")
        self.assertTrue(ok, message)
        self.assertIn("Test message from Tester", json.loads(self.server.requests[-1]["body"])["content"])


if __name__ == "__main__":
    unittest.main()
