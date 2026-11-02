# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RLY1: online games through a relay, several at once (tools/relay_load.py), a guest bot that joins a real host from an
invite code (tools/net_guest_bot.py), the encrypted mailbox a public repository's runner uses (tools/ci_drop.py), and the
relay's cloud-init (tools/make_relay_cloud_init.py)."""
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
from shutil import which as shutil_which

from tests import live
from tools import ci_drop, make_relay_cloud_init, net_guest_bot, online_soak, relay_load

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


class CiMaskTests(unittest.TestCase):
    def test_masks_only_on_a_github_runner(self):
        out = io.StringIO()
        self.assertEqual(net_guest_bot.ci_mask(["198.51.100.4", "otter-lamp-12", None, "ab"], {"GITHUB_ACTIONS": "true"}, out), 2)
        self.assertEqual(out.getvalue(), "::add-mask::198.51.100.4\n::add-mask::otter-lamp-12\n")
        out = io.StringIO()
        self.assertEqual(net_guest_bot.ci_mask(["198.51.100.4"], {}, out), 0)
        self.assertEqual(out.getvalue(), "")


def _git(args, cwd):
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError("git %s: %s" % (args, p.stderr))
    return p.stdout


@unittest.skipUnless(shutil_which("openssl") and shutil_which("git"), "needs openssl and git")
class CiDropTests(unittest.TestCase):
    """The invite and the results go through a branch encrypted; a wrong key, a changed byte or a foreign option are refused."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.priv, self.pub = os.path.join(self.d, "k.pem"), os.path.join(self.d, "k.pub")
        ci_drop.make_key_pair(self.priv, self.pub, bits=2048)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seal_and_open(self):
        data = os.urandom(5000)
        self.assertEqual(ci_drop.open_bytes(ci_drop.seal_bytes(data, self.pub), self.priv), data)

    def test_a_wrong_key_is_refused(self):
        other, other_pub = os.path.join(self.d, "o.pem"), os.path.join(self.d, "o.pub")
        ci_drop.make_key_pair(other, other_pub, bits=2048)
        with self.assertRaises(ci_drop.DropError):
            ci_drop.open_bytes(ci_drop.seal_bytes(b"secret", self.pub), other)

    def test_a_changed_byte_is_refused(self):
        env = json.loads(ci_drop.seal_bytes(b"x" * 300, self.pub))
        body = bytearray(base64.b64decode(env["body"]))
        body[len(body) // 2] ^= 1
        env["body"] = base64.b64encode(bytes(body)).decode()
        with self.assertRaises(ci_drop.DropError):
            ci_drop.open_bytes(json.dumps(env).encode(), self.priv)

    def test_only_the_bot_options_pass(self):
        self.assertEqual(ci_drop.bot_args({"minutes": 5, "address": "192.168.0.9"}),
                         ["--address", "192.168.0.9", "--minutes", "5"])
        with self.assertRaises(ci_drop.DropError):
            ci_drop.bot_args({"command": "anything"})

    def test_the_invite_is_taken_masked_and_written_private(self):
        import forge_net
        code = forge_net.make_invite("198.51.100.23", 36800, "c" * 64, "lamp-otter-river-77")
        env = ci_drop.seal_bytes(json.dumps({"invite": code, "options": {"minutes": 3}}).encode(), self.pub)
        inv, argf = os.path.join(self.d, "invite.txt"), os.path.join(self.d, "args.json")
        out = io.StringIO()
        with redirect_stdout(out):
            old = os.environ.get("GITHUB_ACTIONS")
            os.environ["GITHUB_ACTIONS"] = "true"
            try:
                args, secrets = ci_drop.take_invite(env, self.priv, inv, argf)
            finally:
                if old is None:
                    os.environ.pop("GITHUB_ACTIONS", None)
                else:
                    os.environ["GITHUB_ACTIONS"] = old
        self.assertEqual(args, ["--minutes", "3"])
        self.assertIn("::add-mask::198.51.100.23", out.getvalue())
        self.assertIn("::add-mask::lamp-otter-river-77", out.getvalue())
        with open(inv, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), code)
        if os.name == "posix":
            self.assertEqual(os.stat(inv).st_mode & 0o077, 0)

    def test_the_status_is_scrubbed(self):
        res = os.path.join(self.d, "r.json")
        with open(res, "w", encoding="utf-8") as f:
            json.dump({"verdict": "FAIL", "joined": False,
                       "join_error": "no answer from 198.51.100.23:36800 (password lamp-otter-river-77)"}, f)
        text = ci_drop.status_text(res, ["198.51.100.23", "lamp-otter-river-77"], 1)
        self.assertNotIn("198.51.100.23", text)
        self.assertNotIn("lamp-otter-river-77", text)
        self.assertIn("FAIL", text)

    def test_the_runner_loop_through_a_branch(self):
        """A bare repository stands in for GitHub: the runner publishes its key, takes an invite, 'plays' (a stand-in bot that
        writes a result naming the address), and pushes back a sealed result and a scrubbed status - nothing in the repository
        ever holds the address or the password in the clear."""
        import forge_net
        origin = os.path.join(self.d, "origin.git")
        _git(["init", "-q", "--bare", origin], self.d)
        op = os.path.join(self.d, "op")
        _git(["clone", "-q", origin, op], self.d)
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            _git(["config", k, v], op)
        _git(["checkout", "-q", "-b", "claude/net-test"], op)
        os.makedirs(os.path.join(op, "ci_drop"))
        oppriv = os.path.join(self.d, "operator.pem")
        ci_drop.make_key_pair(oppriv, os.path.join(op, "ci_drop", "operator.pub"), bits=2048)
        _git(["add", "-A"], op)
        _git(["commit", "-q", "-m", "start"], op)
        _git(["push", "-q", "origin", "claude/net-test"], op)
        runner = os.path.join(self.d, "runner")
        _git(["clone", "-q", "--branch", "claude/net-test", origin, runner], self.d)
        for k, v in (("user.name", "r"), ("user.email", "r@r")):
            _git(["config", k, v], runner)
        address, password = "198.51.100.23", "lamp-otter-river-77"
        code = forge_net.make_invite(address, 36800, "c" * 64, password)
        seen = []

        def bot(cmd):
            seen.append(cmd)
            out = cmd[cmd.index("--out") + 1]
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "net_guest_result.json"), "w", encoding="utf-8") as f:
                json.dump({"verdict": "FAIL", "joined": False, "join_error": "no answer from %s:36800" % address}, f)
            return 1

        def operator():
            end = time.monotonic() + 30
            while time.monotonic() < end:
                pub = ci_drop.remote_file(op, "origin", "claude/net-test", "ci_drop/9/runner.pub")
                if pub:
                    p = os.path.join(self.d, "runner9.pub")
                    with open(p, "wb") as f:
                        f.write(pub)
                    env = ci_drop.seal_bytes(json.dumps({"invite": code, "options": {"minutes": 1}}).encode(), p)
                    ci_drop.publish(op, "origin", "claude/net-test", {"ci_drop/9/invite.1.enc": env}, "invite 1")
                    return
                time.sleep(0.2)

        t = threading.Thread(target=operator, daemon=True)
        t.start()
        played = ci_drop.runner_loop("9", "claude/net-test", tries=2, wait_minutes=0.05, cwd=runner,
                                     temp=os.path.join(self.d, "rt"), poll=0.2, bot=bot, log=lambda *_a: None)
        t.join(5)
        self.assertEqual(played, 1)
        self.assertEqual(len(seen), 1)
        self.assertIn("--invite-file", seen[0])
        self.assertNotIn(code, " ".join(seen[0]))                 # the invite is a file, never a command-line argument
        status = ci_drop.remote_file(op, "origin", "claude/net-test", "ci_drop/9/status.1.txt").decode()
        self.assertIn("FAIL", status)
        self.assertNotIn(address, status)
        result = ci_drop.remote_file(op, "origin", "claude/net-test", "ci_drop/9/result.1.enc")
        opened = os.path.join(self.d, "opened")
        with open(os.path.join(self.d, "r.enc"), "wb") as f:
            f.write(result)
        self.assertEqual(ci_drop.main(["open-results", "--priv", oppriv, "--file", os.path.join(self.d, "r.enc"),
                                       "--out", opened]), 0)
        with open(os.path.join(opened, "out_1", "net_guest_result.json"), encoding="utf-8") as f:
            self.assertIn(address, f.read())                      # the operator sees everything ...
        objs = _git(["cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)"], origin).split()
        for oid, kind in zip(objs[0::2], objs[1::2]):
            data = subprocess.run(["git", "cat-file", kind, oid], cwd=origin, capture_output=True).stdout
            self.assertNotIn(address.encode(), data, oid)          # ... the repository nothing
            self.assertNotIn(password.encode(), data, oid)
        self.assertIsNotNone(ci_drop.remote_file(op, "origin", "claude/net-test", "ci_drop/9/status.2.txt"))   # gave up on try 2


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
