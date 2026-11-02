# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RLY1: online games through a relay, several at once (tools/relay_load.py), a guest bot that joins a real host from an
invite code (tools/net_guest_bot.py), and the relay's cloud-init (tools/make_relay_cloud_init.py)."""
import base64
import datetime
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout

from tests import live
from tools import make_relay_cloud_init, net_guest_bot, online_soak, relay_load

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ParseRelayTests(unittest.TestCase):
    def test_host_and_port(self):
        self.assertEqual(online_soak.parse_relay("relay.example.com:36901"), ("relay.example.com", 36901))
        self.assertEqual(online_soak.parse_relay(" 203.0.113.7:36900 "), ("203.0.113.7", 36900))

    def test_the_port_defaults_to_36900(self):
        self.assertEqual(online_soak.parse_relay("relay.example.com"), ("relay.example.com", 36900))

    def test_bad_addresses_say_so(self):
        for bad in ("", ":36900", "host:", "host:abc", "host:0", "host:70000"):
            with self.assertRaises(ValueError, msg=bad):
                online_soak.parse_relay(bad)


class OverlapTests(unittest.TestCase):
    def test_peak_of_overlapping_games(self):
        self.assertEqual(relay_load.peak_overlap([(0, 10), (5, 15), (8, 9)])[0], 3)

    def test_one_ending_as_another_starts_is_not_overlap(self):
        self.assertEqual(relay_load.peak_overlap([(0, 10), (10, 20)])[0], 1)

    def test_nothing(self):
        self.assertEqual(relay_load.peak_overlap([]), (0, None))

    def test_rooms_from_the_relay_log(self):
        lines = ["2026-11-02 10:29:13,686 room aaaaaaaaaa opened",
                 "2026-11-02 10:29:13,791 room aaaaaaaaaa: guest 1 paired (1 pairs so far)",
                 "2026-11-02 10:29:14,000 room bbbbbbbbbb opened",
                 "2026-11-02 10:29:20,000 room aaaaaaaaaa closed",
                 "2026-11-02 10:29:30,500 room cccccccccc opened"]
        spans = relay_load.rooms_from_log(lines)
        self.assertEqual(len(spans), 3)
        self.assertEqual(relay_load.peak_overlap(spans)[0], 2)           # a and b; c opens after a closed


def _row(i, started, seconds=60, ended="game_over", via="relay", fails=()):
    return {"index": i, "started": started, "seconds": seconds, "ended": ended, "via": via, "fails": list(fails)}


class JudgeTests(unittest.TestCase):
    def test_three_tables_at_once_pass(self):
        rows = [[_row(0, 100), _row(1, 200)], [_row(0, 110)], [_row(0, 120)]]
        verdict, reasons, facts = relay_load.judge(3, rows)
        self.assertEqual(verdict, "PASS", reasons)
        self.assertEqual(facts["peak_games"], 3)

    def test_one_after_another_is_not_simultaneous(self):
        rows = [[_row(0, 0, 10)], [_row(0, 20, 10)], [_row(0, 40, 10)]]
        verdict, reasons, _f = relay_load.judge(3, rows)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("at most 1 game" in r for r in reasons), reasons)

    def test_a_failing_game_a_direct_game_and_an_empty_table_fail(self):
        rows = [[_row(0, 100, fails=["stall"])], [_row(0, 110, via="direct")], []]
        verdict, reasons, _f = relay_load.judge(3, rows)
        self.assertEqual(verdict, "FAIL")
        text = " | ".join(reasons)
        self.assertIn("table 2 played no game", text)
        self.assertIn("did not join through the relay", text)
        self.assertIn("stall", text)

    def test_the_relay_log_must_show_the_rooms_at_once_too(self):
        rows = [[_row(0, 100)], [_row(0, 110)]]
        verdict, reasons, _f = relay_load.judge(2, rows, room_spans=[(100, 150), (160, 170)])
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("relay's log" in r for r in reasons), reasons)


class InviteTextTests(unittest.TestCase):
    def test_from_a_file_and_from_the_environment(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "invite.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n  MANT1-abc  \n")
            self.assertEqual(net_guest_bot.read_invite_text(p), "MANT1-abc")
        self.assertEqual(net_guest_bot.read_invite_text(None, {"MANTICORE_INVITE": "MANT1-xyz"}), "MANT1-xyz")
        with self.assertRaises(ValueError):
            net_guest_bot.read_invite_text(None, {})

    def test_the_invite_is_never_a_command_line_argument(self):
        with open(os.path.join(BASE, "tools", "net_guest_bot.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('add_argument("--invite"', src)
        self.assertNotIn("add_argument('--invite'", src)


class CloudInitTests(unittest.TestCase):
    def test_the_committed_file_holds_the_current_relay(self):
        self.assertEqual(make_relay_cloud_init.main(["--check"]), 0)

    def test_it_installs_a_service_and_opens_the_port(self):
        text = make_relay_cloud_init.build(36955)
        self.assertTrue(text.startswith("#cloud-config\n"))
        self.assertIn("--port 36955", text)
        self.assertIn("--dport 36955", text)
        self.assertIn("--add-port=36955/tcp", text)
        self.assertIn("enable, --now, manticore-relay", text)
        line = next(ln for ln in text.splitlines() if ln.strip().startswith("content:") and len(ln) > 200)
        relay = base64.b64decode(line.split("content:", 1)[1].strip())
        with open(make_relay_cloud_init.RELAY, "rb") as f:
            self.assertEqual(relay, f.read())
        self.assertIn(hashlib.sha256(relay).hexdigest(), text)


class GuestBotJoinFailureTests(unittest.TestCase):
    """No Forge needed: a join to a port nobody listens on fails in plain words, and the password goes nowhere."""

    def test_a_closed_port_fails_in_words_and_never_writes_the_password(self):
        import forge_net
        password = "secret-otter-%d" % random.randrange(10 ** 6)
        port = online_soak._free_port()
        code = forge_net.make_invite("127.0.0.1", port, "a" * 64, password)
        with tempfile.TemporaryDirectory() as d:
            inv = os.path.join(d, "invite.txt")
            with open(inv, "w", encoding="utf-8") as f:
                f.write(code)
            out = os.path.join(d, "out")
            buf = io.StringIO()
            old = os.environ.get("MANTICORE_DATA_DIR")
            try:
                with redirect_stdout(buf):
                    rc = net_guest_bot.main(["--invite-file", inv, "--out", out, "--minutes", "0.2"])
            finally:
                if old is None:
                    os.environ.pop("MANTICORE_DATA_DIR", None)
                else:
                    os.environ["MANTICORE_DATA_DIR"] = old
            self.assertEqual(rc, 1)
            with open(os.path.join(out, "net_guest_result.json"), encoding="utf-8") as f:
                result = json.load(f)
            self.assertFalse(result["joined"])
            self.assertEqual(result["verdict"], "FAIL")
            self.assertTrue(result.get("join_error"))
            self.assertNotIn(password, buf.getvalue())
            for root, _dirs, files in os.walk(out):
                for name in files:
                    with open(os.path.join(root, name), "rb") as f:
                        self.assertNotIn(password.encode(), f.read(), name)


def _host_until_over(host, seed, stop, concede_turn=6):
    """Play the host's seat with the soak bot until the game is over (it concedes at concede_turn to keep the test short)."""
    seat = online_soak.Seat("host", host, seed)
    conceded = False
    while not stop.is_set() and not host.game_over and not host.exited:
        seat.step()
        if not conceded and ((host.state or {}).get("turn") or 0) >= concede_turn:
            host.concede()
            conceded = True
        time.sleep(0.02)


@unittest.skipUnless(live.live_enabled(), "needs Forge: %s" % live.live_problem())
class LiveTests(unittest.TestCase):
    """Real Forge on this machine: a game through a relay started here, and the guest bot joining a real host."""

    def _host(self, tmp, relay=None):
        online_soak._load_modules()
        fc, fn = online_soak.fc, online_soak.fn
        cert = fn.ensure_host_cert(tmp, fc.find_java())
        deck = online_soak.build_deck(os.path.join(BASE, "sample_decks", "typal_lathril.txt"), tmp, "host")
        port = online_soak._free_port()
        password = fn.make_password(random.Random(7))
        extra = {"relay": relay} if relay else {}
        host = fc.HostSession(deck, "Host", port, password, cert, os.path.join(tmp, "incoming.dck"), upnp=False,
                              bind="127.0.0.1", code="rly1test", seed=4242, **extra)
        host.stderr_path = os.path.join(tmp, "host_engine.log")
        host.start()
        end = time.monotonic() + 120
        while host.hosting is None and not host.exited and time.monotonic() < end:
            host.poll()
            time.sleep(0.05)
        self.assertIsNotNone(host.hosting, "NetHost never got ready")
        addr, gport, room, why = online_soak._guest_target(host, port, relay)
        self.assertIsNone(why)
        return host, fn.make_invite(addr, gport, cert.fingerprint, password, room=room), password

    def _bot_joins(self, relay=None):
        with tempfile.TemporaryDirectory() as tmp:
            relay_proc = None
            if relay == "start":
                relay_proc, relay = online_soak.start_local_relay(os.path.join(tmp, "relay.log"))
            host, invite, password = self._host(tmp, relay)
            stop = threading.Event()
            t = threading.Thread(target=_host_until_over, args=(host, 99, stop), daemon=True)
            t.start()
            inv_path = os.path.join(tmp, "invite.txt")
            with open(inv_path, "w", encoding="utf-8") as f:
                f.write(invite)
            out = os.path.join(tmp, "bot")
            env = dict(os.environ, MANTICORE_PORTABLE="1")
            p = subprocess.run([sys.executable, os.path.join(BASE, "tools", "net_guest_bot.py"), "--invite-file", inv_path,
                                "--out", out, "--minutes", "8", "--seed", "5"], cwd=BASE, env=env, capture_output=True,
                               text=True, timeout=600)
            stop.set()
            t.join(30)
            host.close()
            if relay_proc is not None:
                relay_proc.terminate()
                relay_proc.wait(10)
            with open(os.path.join(out, "net_guest_result.json"), encoding="utf-8") as f:
                result = json.load(f)
            self.assertNotIn(password, p.stdout + p.stderr)
            return p, result

    def test_the_guest_bot_joins_directly_and_plays_to_game_over(self):
        p, result = self._bot_joins()
        self.assertEqual(p.returncode, 0, p.stdout[-800:])
        self.assertTrue(result["joined"])
        self.assertFalse(result["relay"])
        self.assertEqual(result["ended"], "game_over")
        self.assertEqual(result["fails"], [])

    def test_the_guest_bot_joins_through_a_relay(self):
        p, result = self._bot_joins(relay="start")
        self.assertEqual(p.returncode, 0, p.stdout[-800:])
        self.assertTrue(result["relay"])
        self.assertEqual(result["ended"], "game_over")

    def test_two_soak_games_at_once_through_one_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "load")
            rc = relay_load.main(["--start-relay", "--tables", "2", "--games", "1", "--seed", "77", "--pods", "0",
                                  "--drops", "0", "--concede", "1", "--resume", "0", "--out", out])
            with open(os.path.join(out, "relay_load_summary.txt"), encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(rc, 0, text[:1500])
            self.assertIn("RELAY LOAD TEST: PASS", text)


if __name__ == "__main__":
    unittest.main()
