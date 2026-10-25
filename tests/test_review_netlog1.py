# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_review_netlog1.py - an independent review of Round NETLOG1 (the connection log, the host / join attempts written
into it, the network self-test). Written cold from the code, 24 Oct 2026.

Two kinds of test here:
  * BUG tests: each fails on NETLOG1 as delivered (patch sha256 7013ecdf...) because of the finding it names (R1, R2, ...
    in claude/REVIEW_NETLOG1_2026-10-24.md). They should pass once that finding is fixed.
  * GUARD tests (class names end in "Guard"): they pass now and guard a claim the round makes, more strictly than
    tests/test_netlog1.py does (two planted faults that test_netlog1 misses are caught by them - see the review).
Headless; no Forge, no second PC, nothing written outside a temporary folder.
"""
import base64
import binascii
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from unittest import mock

import forge_client as fc
import forge_net as fn
import net_log
from tests.net_fake import FakeHost
from tools import net_selftest as st

PASSWORD = "qzxv-wkjp-ymrt-58"          # odd on purpose: no 5-letter piece of it can turn up in a log by chance
TOKEN = "Tq9Zx2Vw7Ks4Lm1Np6Hd"


def read(folder):
    try:
        with open(net_log.log_path(folder), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def pieces(secret, n=5):
    return {secret[i:i + n] for i in range(len(secret) - n + 1)}


class TempFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="review_netlog1_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": self.tmp})   # paths.log_dir() -> self.tmp
        env.start()
        self.addCleanup(env.stop)


WAITING_NETHOST = textwrap.dedent('''
    import json, sys, time
    print(json.dumps({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})); sys.stdout.flush()
    time.sleep(0.6)
    # what NetHost.java prints when a friend's table reaches the port but the TLS handshake fails - and it sends the host's
    # table no message for it (NetHost.java line 710)
    sys.stderr.write("nethost: handshake with /198.51.100.9:50123 failed: javax.net.ssl.SSLHandshakeException: "
                     "Remote host terminated the handshake\\n")
    sys.stderr.flush()
    for line in sys.stdin:
        if '"quit"' in line:
            break
''')


def stand_in_host(tmp, script=WAITING_NETHOST):
    path = os.path.join(tmp, "stand_in_nethost.py")
    with open(path, "w") as f:
        f.write(script)
    cert = fn.HostCert(os.path.join(tmp, "host.p12"), "storepass", "cd" * 32)
    s = fc.HostSession("me.dck", "Karl", 36800, PASSWORD, cert, "guest.dck", upnp=False, code="c0de", seed=1)
    s.command = [sys.executable, path]
    s.stderr_path = os.path.join(tmp, "forge_engine.log")
    return s


def pump(session, seconds):
    end = time.time() + seconds
    while time.time() < end:
        session.poll()
        time.sleep(0.05)


# ---------------------------------------------------------------------------------------------------------------------
# R1: hosting runs this PC's address lookups (getfqdn, getaddrinfo, `tailscale ip -4` with a 3 s timeout) on the
#     window's thread - host_game() is called from HostDialog.host(), inside the event loop
# ---------------------------------------------------------------------------------------------------------------------
class HostingDoesNotWaitForLookupsTests(TempFolder):
    def test_a_slow_tailscale_program_does_not_hold_up_hosting(self):
        def slow_tailscale():
            time.sleep(1.5)                      # forge_net.tailscale_address waits up to 3 s for `tailscale ip -4`
            return None
        s = stand_in_host(self.tmp)
        with mock.patch.object(fn, "tailscale_address", slow_tailscale):
            t = time.monotonic()
            s.start()
            took = time.monotonic() - t
        try:
            self.assertLess(took, 0.75, "HostSession.start() (on the window's thread) waited %.1f s for the address "
                                        "lookups" % took)
        finally:
            s.close()

    def test_a_slow_reverse_lookup_does_not_hold_up_hosting(self):
        real = socket.getfqdn

        def slow_fqdn(*a):
            time.sleep(1.5)                      # socket.getfqdn is a reverse DNS lookup; it can wait on a slow DNS server
            return real(*a)
        s = stand_in_host(self.tmp)
        with mock.patch.object(socket, "getfqdn", slow_fqdn), mock.patch.object(fn, "tailscale_address", lambda: None):
            t = time.monotonic()
            s.start()
            took = time.monotonic() - t
        try:
            self.assertLess(took, 0.75, "HostSession.start() waited %.1f s for socket.getfqdn" % took)
        finally:
            s.close()


# ---------------------------------------------------------------------------------------------------------------------
# R2: the self-test's firewall listing crashes on netsh output it can't decode (subprocess text=True decodes with the ANSI
#     code page, netsh writes the OEM one; only OSError and SubprocessError are caught)
# ---------------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(shutil.which("printf"), "needs printf to make undecodable output")
class FirewallDecodeTests(unittest.TestCase):
    def test_a_rule_name_with_an_umlaut_does_not_crash_the_self_test(self):
        def netsh_like(cmd, **kw):
            # a German Windows: "Remoteunterstutzung" with u-umlaut in code page 850 is byte 0x81, which cp1252 (what
            # Python's text=True uses on an English or German Windows) cannot decode
            kw.pop("creationflags", None)
            kw.pop("text", None)
            return subprocess.run(["printf", "Regelname: Remoteunterst\\201tzung (RA-Server TCP-In)\\n"], encoding="cp1252",
                                  **kw)
        with mock.patch.object(st, "os", types.SimpleNamespace(name="nt")):
            try:
                out = st.firewall_lines(run=netsh_like)
            except UnicodeDecodeError as e:
                self.fail("firewall_lines raised %r: the self-test stops with a traceback, before its advice and END" % e)
        self.assertTrue(out)


# ---------------------------------------------------------------------------------------------------------------------
# R3: the router's own words for a refused UPnP mapping (the "text" of NetHost's {"t":"upnp"} message) are never logged
# ---------------------------------------------------------------------------------------------------------------------
class UpnpTextTests(TempFolder):
    def test_the_routers_refusal_text_is_in_the_log(self):
        a = net_log.Attempt("host", with_interfaces=False, port=36800)
        session = types.SimpleNamespace(_logged_start=False, online="host", _harvest_nethost=lambda: None)
        fc._log_online_message(a, {"t": "upnp", "ok": False, "reason": "refused",
                                   "text": "Error executing action: ConflictInMappingEntry (718)"}, session)
        self.assertIn("ConflictInMappingEntry", read(self.tmp))

    def test_an_upnp_error_text_is_in_the_log(self):
        a = net_log.Attempt("host", with_interfaces=False, port=36800)
        session = types.SimpleNamespace(_logged_start=False, online="host", _harvest_nethost=lambda: None)
        fc._log_online_message(a, {"t": "upnp", "ok": False, "reason": "no_network", "text": "This PC has no network address."},
                               session)
        self.assertIn("no network address", read(self.tmp))


# ---------------------------------------------------------------------------------------------------------------------
# R4: NetHost's own lines are copied into the log only at the next online message or at close - a failed TLS handshake
#     sends the host's table no message, so while the host waits (and in an F8 report made then) the log lacks it
# ---------------------------------------------------------------------------------------------------------------------
class HarvestWhileWaitingTests(TempFolder):
    def test_a_failed_handshake_reaches_the_log_while_the_host_waits(self):
        s = stand_in_host(self.tmp)
        s.start()
        try:
            pump(s, 3.0)
            self.assertIn("handshake with /198.51.100.9:50123 failed", read(self.tmp),
                          "3 s after NetHost printed it, the connection log still has no trace of the failed handshake")
        finally:
            s.close()


# ---------------------------------------------------------------------------------------------------------------------
# R5: the self-test calls any error on the game's port "busy (a game already hosting, or another program)" - also a
#     permission error (WinError 10013: a port Windows has reserved, e.g. for Hyper-V / WSL, or security software), when
#     nothing holds the port and the real game could not listen there either
# ---------------------------------------------------------------------------------------------------------------------
class ReservedPortTests(TempFolder):
    def test_a_permission_error_is_not_called_busy(self):
        err = PermissionError(13, "An attempt was made to access a socket in a way forbidden by its access permissions")
        err.winerror = 10013
        lines = []
        with mock.patch.object(st, "Listener", side_effect=[err, st.Listener("127.0.0.1", 0)]):
            code, _results = st.run(port=36800, timeout=1, out=lines.append, info={"addresses": [("127.0.0.1", "loopback")],
                                                                                  "tailscale": None}, firewall=False)
        printed = "\n".join(lines)
        self.assertNotIn("was busy", printed, printed)
        self.assertNotIn("Close Manticore's hosting", printed, printed)


# ---------------------------------------------------------------------------------------------------------------------
# R6: the self-test's advice about Tailscale ignores the Tailscale probe's own result
# ---------------------------------------------------------------------------------------------------------------------
class TailscaleAdviceTests(unittest.TestCase):
    INFO = {"addresses": [("127.0.0.1", "loopback"), ("192.168.1.20", "lan"), ("100.70.1.2", "tailscale")],
            "tailscale": "100.70.1.2", "tailscale_cli": "100.70.1.2"}
    RESULTS = [("loopback", "127.0.0.1", st.OK, "", 1, ""), ("LAN", "192.168.1.20", st.OK, "", 1, ""),
               ("Tailscale", "100.70.1.2", st.TIMED_OUT, "no answer", 3000, "TimeoutError: timed out")]

    def test_a_failed_tailscale_probe_is_not_advised_as_working(self):
        text = "\n".join(st.advice(self.RESULTS, self.INFO, 36800, 36800))
        self.assertNotIn("a friend on the same tailnet can join that address directly", text, text)

    def test_when_tailscale_is_what_failed_tailscale_is_not_the_remedy(self):
        text = "\n".join(st.advice(self.RESULTS, self.INFO, 36800, 36800))
        self.assertNotRegex(text, r"not: Tailscale\)[^\n]*or Tailscale", text)


# ---------------------------------------------------------------------------------------------------------------------
# R7: a rotation that can't move the current file (Windows: another process or thread has it open - os.replace gives
#     PermissionError, WinError 32) still shifts the old files, and the oldest one is lost without making room
# ---------------------------------------------------------------------------------------------------------------------
class RotationTests(TempFolder):
    def test_a_rotation_that_cannot_move_the_current_file_loses_no_old_file(self):
        names = {net_log.log_path(self.tmp): "CURRENT " * 40, net_log.old_path(self.tmp, 1): "OLDEST-BUT-TWO\n",
                 net_log.old_path(self.tmp, 2): "OLDEST-BUT-ONE\n", net_log.old_path(self.tmp, 3): "THE-OLDEST\n"}
        for path, text in names.items():
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        real = os.replace
        current = os.path.abspath(net_log.log_path(self.tmp))

        def replace(src, dst):
            if os.path.abspath(src) == current:
                raise PermissionError(13, "The process cannot access the file because it is being used by another process")
            return real(src, dst)
        with mock.patch.object(net_log, "MAX_BYTES", 100), mock.patch.object(net_log.os, "replace", replace):
            self.assertTrue(net_log.write_lines(["a new line"], folder=self.tmp))
        everything = ""
        for name in os.listdir(self.tmp):
            with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
                everything += f.read()
        for marker in ("OLDEST-BUT-TWO", "OLDEST-BUT-ONE", "THE-OLDEST", "a new line"):
            self.assertIn(marker, everything)


# ---------------------------------------------------------------------------------------------------------------------
# R8: redact() leaves the password-bearing end of an invite code that a chat program broke with a space or a newline
#     (read_invite() accepts such a code: "chat programs break long lines")
# ---------------------------------------------------------------------------------------------------------------------
def _decodes_to(text, secret):
    """True when some base64url run in `text`, read from any of its first four characters, holds `secret`."""
    for run in re.findall(r"[A-Za-z0-9_\-]{12,}", text):
        for off in range(4):
            body = run[off:]
            try:
                raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            except (binascii.Error, ValueError):
                continue
            if secret.encode() in raw:
                return True
    return False


class BrokenInviteTests(TempFolder):
    def test_an_invite_code_broken_by_a_newline_is_still_left_out(self):
        code = fn.make_invite("203.0.113.7", 36800, "ab" * 32, PASSWORD)
        self.assertEqual(fn.read_invite(code[:70] + "\n" + code[70:])["password"], PASSWORD)    # the program accepts it
        net_log.write_lines(["pasted: " + code[:70] + "\n" + code[70:]])
        self.assertFalse(_decodes_to(read(self.tmp), PASSWORD), read(self.tmp))

    def test_an_invite_code_broken_by_a_space_is_still_left_out(self):
        code = fn.make_invite("203.0.113.7", 36800, "ab" * 32, PASSWORD)
        self.assertFalse(_decodes_to(net_log.redact("code: " + code[:90] + " " + code[90:]), PASSWORD))


# ---------------------------------------------------------------------------------------------------------------------
# R9: the host's attempt never says which java.exe listens (Windows Firewall rules are per program; the self-test lists
#     every rule naming "java", and the PC may have several)
# ---------------------------------------------------------------------------------------------------------------------
class EngineProgramTests(TempFolder):
    def test_the_host_attempt_names_the_program_that_listens(self):
        s = stand_in_host(self.tmp)
        s.start()
        try:
            program = s.proc.args[0]
            self.assertIn(program, read(self.tmp))
        finally:
            s.close()


# ---------------------------------------------------------------------------------------------------------------------
# GUARDS (pass now)
# ---------------------------------------------------------------------------------------------------------------------
class OnlineMessageGuard(TempFolder):
    """Every online message, with any field missing or None, is logged without raising (an exception there would come out
    of NetSession.handle / HostSession.handle, i.e. out of the table's poll())."""

    TYPES = tuple(fc.ONLINE_MESSAGES) + ("state", "ready", "game_over", "fatal", "_exit", "request", "event", "log")
    FIELDS = ("name", "seat", "joined", "wanted", "code", "reason", "text", "why", "grace", "who", "left", "expired", "port",
              "bind", "ok", "external_ip", "turn", "protocol", "players", "after", "done", "total")

    def test_no_message_shape_raises(self):
        a = net_log.Attempt("host", with_interfaces=False, port=1)
        for online in ("host", "guest"):
            for t in self.TYPES:
                for m in ({"t": t}, dict({"t": t}, **{k: None for k in self.FIELDS}),
                          dict({"t": t}, **{k: "x" for k in self.FIELDS}), dict({"t": t}, **{k: 3 for k in self.FIELDS})):
                    session = types.SimpleNamespace(_logged_start=False, online=online, _harvest_nethost=lambda: None)
                    try:
                        fc._log_online_message(a, m, session)
                    except Exception as e:                       # noqa: BLE001 - the point of the test
                        self.fail("%s message %r (%s) raised %r" % (online, m, t, e))


class NoPieceOfASecretGuard(TempFolder):
    """No 5-character piece of the password or the rejoin token reaches the log, in a join, a watch and a rejoin
    (tests/test_netlog1.py checks only for the whole password, so a cut-off copy of it would pass there)."""

    def _session(self, host, **kw):
        return fc.NetSession("127.0.0.1", host.port, "Sam", PASSWORD, "Deck", "[metadata]\n", code="c", welcome_timeout=1,
                             **kw)

    def test_join_watch_and_rejoin(self):
        plain = staticmethod(lambda a, p, fp=None, timeout=10, **k: socket.create_connection((a, int(p)), timeout))
        with mock.patch.object(fc.NetSession, "_connect", plain):
            h = FakeHost("welcome", after=[{"t": "state", "turn": 1}]).start()
            self.addCleanup(h.stop)
            s = self._session(h)
            s.start()
            s._token = TOKEN
            with self.assertRaises(fn.NetRefused):
                s._handshake(fn.rejoin_line(TOKEN))
            s.close()
            w = FakeHost("refused:password").start()
            self.addCleanup(w.stop)
            with self.assertRaises(fn.NetRefused):
                self._session(w, watch=True).start()
        text = read(self.tmp)
        self.assertIn("hello", text)
        self.assertIn("watch", text)
        self.assertIn("rejoin", text)
        for secret in (PASSWORD, TOKEN):
            leaked = sorted(p for p in pieces(secret) if p in text)
            self.assertEqual(leaked, [], "pieces of a secret in the connection log: %s" % leaked)


class NoLogNoExtraLookupGuard(unittest.TestCase):
    """Without an attempt active on the thread, connect_tls does exactly what it did before NETLOG1 (no extra lookup)."""

    def test_no_resolve_without_an_attempt(self):
        with mock.patch.object(net_log, "resolve", side_effect=AssertionError("resolve called")), \
                mock.patch.object(socket, "create_connection", side_effect=ConnectionRefusedError(111, "refused")):
            with self.assertRaises(fn.NetRefused) as cm:
                fn.connect_tls("127.0.0.1", 9)
        self.assertEqual(cm.exception.reason, "refused_connection")


if __name__ == "__main__":
    unittest.main()
