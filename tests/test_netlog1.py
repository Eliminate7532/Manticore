# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_netlog1.py - Round NETLOG1: the connection log (net_log.py), the host and join attempts written into it, the bug
report carrying it, and the network self-test (tools/net_selftest.py). Headless; no Forge, no second PC.
"""
import importlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import zipfile
from unittest import mock

import forge_client as fc
import forge_net as fn
import net_log
import reporting
import version
from tests.net_fake import FakeHost
from tools import net_selftest as st

MARKER = "zebra-quill-tundra-77"            # a password that must never be written anywhere
TOKEN = "tok-9f8e7d6c5b4a-secret"           # a rejoin token, the same


def read(folder):
    with open(net_log.log_path(folder), encoding="utf-8") as f:
        return f.read()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(fn_, seconds, pump=()):
    end = time.time() + seconds
    while time.time() < end:
        for s in pump:
            s.poll()
        if fn_():
            return True
        time.sleep(0.05)
    return bool(fn_())


class TempFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="netlog1_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": self.tmp})   # paths.log_dir() -> self.tmp
        env.start()
        self.addCleanup(env.stop)


# ---------------------------------------------------------------------------------------------------------------------
# 1. the file
# ---------------------------------------------------------------------------------------------------------------------
class LogFileTests(TempFolder):
    def test_it_is_in_the_logs_folder(self):
        self.assertEqual(net_log.log_path(), os.path.join(self.tmp, "connection_log.txt"))

    def test_an_attempt_has_every_field(self):
        a = net_log.Attempt("join", address_as_typed=repr(" 203.0.113.7 "), port=36800, relay="no",
                            tailscale_cli=lambda: "100.101.102.103")
        a.step("connect", False, 12.5, "to port 36800", ConnectionRefusedError(111, "Connection refused"))
        with self.assertRaises(ValueError):
            with a.timed("hello"):
                raise ValueError("the host said something odd")
        a.end("FAILED: refused_connection")
        text = read(self.tmp)
        for needle in ("JOIN attempt " + a.id, version.VERSION, "code " + version.code_fingerprint(), "python ",
                       "address as typed: ' 203.0.113.7 '", "port: 36800", "address 127.0.0.1", "loopback",
                       "Tailscale: yes, 100.101.102.103", "address 100.101.102.103", "tailscale",
                       "connect         FAILED", "12 ms", "error: ConnectionRefusedError [errno 111]",
                       "hello           FAILED", "error: ValueError: the host said something odd",
                       "END FAILED: refused_connection"):
            self.assertIn(needle, text)
        self.assertRegex(text, r"=== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d  JOIN")
        steps = [x for x in text.splitlines() if "s  " in x and x.lstrip().startswith(a.id)]
        self.assertEqual(len(steps), 3)                       # connect, hello, END: each carries the attempt's id

    # 24 Oct (Karl's first Windows run of the merge pack): these used to read this PC's real addresses. Karl's PC has a real
    # Tailscale address (100.99.152.7), so the log rightly said "Tailscale: maybe" and the "no" test failed there. The PC's
    # addresses are now stood in for (attempt_on), and a third test checks the line against whatever the real PC has.
    def attempt_on(self, *ips):
        found = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
        with mock.patch.object(net_log.socket, "gethostname", return_value="testpc"), \
                mock.patch.object(net_log, "_safe_fqdn", return_value=""), \
                mock.patch.object(net_log.socket, "getaddrinfo", return_value=found), \
                mock.patch.object(net_log, "_linux_addresses", return_value=list(ips)), \
                mock.patch.object(net_log, "_default_route_address", return_value=ips[0]):
            net_log.Attempt("host", tailscale_cli=lambda: None, port=36800)
        return read(self.tmp)

    def test_no_tailscale_is_said_plainly(self):
        text = self.attempt_on("192.168.1.20")
        self.assertIn("Tailscale: no 100.64.0.0/10 address", text)
        self.assertNotRegex(text, r"address 100\.")

    def test_a_tailscale_address_without_the_program_is_a_maybe(self):      # Karl's PC, 24 Oct
        text = self.attempt_on("192.168.1.20", "100.99.152.7")
        self.assertRegex(text, r"address 100\.99\.152\.7 +tailscale")
        self.assertIn("Tailscale: maybe - 100.99.152.7", text)
        self.assertNotIn("Tailscale: no 100.64.0.0/10 address", text)

    def test_the_tailscale_line_agrees_with_this_pcs_real_addresses(self):
        net_log.Attempt("host", tailscale_cli=lambda: None, port=36800)
        text = read(self.tmp)
        seen = re.findall(r"address (\S+) +tailscale\b", text)
        if seen:
            self.assertIn("Tailscale: maybe - " + seen[0], text)
        else:
            self.assertIn("Tailscale: no 100.64.0.0/10 address", text)

    def test_address_kinds(self):
        for ip, kind in (("127.0.0.1", "loopback"), ("192.168.1.20", "lan"), ("10.0.0.5", "lan"), ("100.64.0.1", "tailscale"),
                         ("100.127.255.254", "tailscale"), ("100.128.0.1", "public"), ("169.254.3.4", "link-local"),
                         ("8.8.4.4", "public"), ("::1", "ipv6-loopback"), ("fe80::1%eth0", "ipv6-link-local"),
                         ("fd7a:115c:a1e0::1", "tailscale-ipv6"), ("nonsense", "unknown")):
            self.assertEqual(net_log.address_kind(ip), kind, ip)

    def test_a_restart_appends_and_never_overwrites(self):
        net_log.Attempt("host", with_interfaces=False, port=1).end("first run")
        importlib.reload(net_log)                          # a new start of the program
        try:
            net_log.Attempt("join", with_interfaces=False, port=2).end("second run")
            text = read(self.tmp)
            self.assertIn("END first run", text)
            self.assertIn("END second run", text)
            self.assertLess(text.index("first run"), text.index("second run"))
        finally:
            importlib.reload(net_log)

    def test_rotation_keeps_the_last_few_files(self):
        with mock.patch.object(net_log, "MAX_BYTES", 3000):
            for i in range(80):
                a = net_log.Attempt("join", with_interfaces=False, port=i)
                a.step("connect", True, 1.0, "number %03d" % i)
        files = sorted(os.listdir(self.tmp))
        self.assertEqual(files, ["connection_log.1.txt", "connection_log.2.txt", "connection_log.3.txt",
                                 "connection_log.txt"])
        for name in files:
            self.assertLessEqual(os.path.getsize(os.path.join(self.tmp, name)), 3000)
        self.assertIn("number 079", read(self.tmp))                       # the newest is in the current file
        everything = ""
        for n in files:
            with open(os.path.join(self.tmp, n), encoding="utf-8") as f:
                everything += f.read()
        self.assertNotIn("number 000", everything)                        # the oldest went
        self.assertIn("number 079", "\n".join(net_log.tail(self.tmp, 2000)))

    def test_never_raises_where_it_cannot_write(self):
        blocker = os.path.join(self.tmp, "a_file")
        open(blocker, "w").close()
        self.assertFalse(net_log.write_lines(["x"], folder=blocker))
        a = net_log.Attempt("join", folder=blocker, with_interfaces=False)
        a.step("connect", False, error=OSError("nope"))
        a.end("x")

    def test_tail_reads_the_newest_old_file_too(self):
        with open(net_log.old_path(self.tmp, 1), "w", encoding="utf-8") as f:
            f.write("older line\n")
        net_log.write_lines(["newer line"])
        self.assertEqual(net_log.tail(self.tmp)[-2:], ["older line", "newer line"])
        self.assertEqual(net_log.tail(os.path.join(self.tmp, "nowhere")), [])


# ---------------------------------------------------------------------------------------------------------------------
# 2. never a secret
# ---------------------------------------------------------------------------------------------------------------------
class SecretTests(TempFolder):
    def test_redact_removes_every_shape_of_secret(self):
        invite = fn.make_invite("203.0.113.7", 36800, "ab" * 32, MARKER)
        hook = "https://discord.com/api/webhooks/123456789012345678/" + "x" * 68
        for text in (invite, "code: " + invite + " end", hook, '{"password": "%s"}' % MARKER, '{"token":"%s"}' % TOKEN,
                     "MANTICORE_NET_PASSWORD=" + MARKER, "password: " + MARKER, "token=" + TOKEN,
                     "MANTICORE_KEYSTORE_PASS=" + MARKER):
            out = net_log.redact(text)
            self.assertNotIn(MARKER, out, text)
            self.assertNotIn(TOKEN, out, text)
            self.assertNotIn("x" * 68, out, text)
        self.assertIn("MANT1-<invite code left out>", net_log.redact(invite))
        self.assertEqual(net_log.redact("Wrong password."), "Wrong password.")

    def test_a_line_with_a_secret_is_written_without_it(self):
        net_log.write_lines(["error: " + fn.make_invite("1.2.3.4", 1, "cd" * 32, MARKER) + ' {"password": "%s"}' % MARKER])
        self.assertNotIn(MARKER, read(self.tmp))
        self.assertNotIn("MANT1-ey", read(self.tmp))

    def test_a_join_and_a_rejoin_never_log_the_password_or_token(self):
        h = FakeHost("welcome", after=[{"t": "state", "turn": 1}]).start()
        self.addCleanup(h.stop)
        with mock.patch.object(fc.NetSession, "_connect", staticmethod(lambda a, p, fp=None, timeout=10, **k:
                                                                         socket.create_connection((a, int(p)), timeout))):
            s = fc.NetSession("127.0.0.1", h.port, "Sam", MARKER, "Deck", "[metadata]\n", code="c", welcome_timeout=1)
            s.start()
            s._token = TOKEN
            with self.assertRaises(fn.NetRefused):     # the fake host takes one connection: the rejoin gets no answer
                s._handshake(fn.rejoin_line(TOKEN))
            s.close()
        text = read(self.tmp)
        self.assertNotIn(MARKER, text)
        self.assertNotIn(TOKEN, text)
        self.assertIn("hello           OK", text)
        self.assertIn("rejoin token none", text)
        self.assertRegex(text, r"rejoin\s+FAILED")


# ---------------------------------------------------------------------------------------------------------------------
# 3. the guest's join attempt, through the real forge_net.connect_tls
# ---------------------------------------------------------------------------------------------------------------------
def make_cert(folder):
    """A self-signed certificate made with openssl, or None when there's no openssl."""
    exe = shutil.which("openssl")
    if not exe:
        return None
    key, crt = os.path.join(folder, "k.pem"), os.path.join(folder, "c.pem")
    r = subprocess.run([exe, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days",
                        "2", "-subj", "/CN=test", "-keyout", key, "-out", crt], capture_output=True)
    return (crt, key) if r.returncode == 0 else None


class TlsHost:
    """One TLS connection, answers the hello with a welcome (with a token), then sends `after`."""

    def __init__(self, cert, after=()):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(*cert)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(2)
        self.port = self.server.getsockname()[1]
        self.after = after
        with open(cert[0]) as f:
            self.fp = fn.fingerprint(ssl.PEM_cert_to_DER_cert(f.read()))
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn = None
        try:
            raw, _ = self.server.accept()
            conn = raw
            conn = self.ctx.wrap_socket(raw, server_side=True)
            f = conn.makefile("rb")
            f.readline()
            conn.sendall((json.dumps({"t": "welcome", "v": 2, "host": "Karl", "seat": 2, "code": version.code_fingerprint(),
                                      "token": TOKEN, "grace": 60}) + "\n").encode())
            for m in self.after:
                conn.sendall((json.dumps(m) + "\n").encode())
            time.sleep(2)
        except (OSError, ssl.SSLError):
            pass
        finally:
            if conn is not None:
                conn.close()

    def close(self):
        self.server.close()


class JoinLogTests(TempFolder):
    def test_nobody_at_the_port(self):
        port = free_port()
        with self.assertRaises(fn.NetRefused):
            fc.NetSession("127.0.0.1", port, "Sam", MARKER, "Deck", "", fingerprint="ab" * 32).start()
        text = read(self.tmp)
        self.assertIn("JOIN attempt", text)
        self.assertIn("address as typed: '127.0.0.1'", text)
        self.assertIn("resolve         OK", text)
        self.assertIn("'127.0.0.1' -> 127.0.0.1 (IPv4)", text)
        self.assertRegex(text, r"connect\s+FAILED .*refused.*error: ConnectionRefusedError")
        self.assertIn("END FAILED: refused_connection", text)

    def test_an_address_that_does_not_resolve(self):
        with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror(11001, "getaddrinfo failed")):
            with self.assertRaises(fn.NetRefused):
                fc.NetSession("no-such-host.invalid", 36800, "Sam", "", "Deck", "").start()
        text = read(self.tmp)
        self.assertRegex(text, r"resolve\s+FAILED .*no-such-host.invalid.*error: gaierror \[errno 11001\]: .*getaddrinfo failed")

    def test_a_plain_listener_is_not_a_game_host(self):
        h = FakeHost("silent").start()                          # plain TCP, no TLS: the handshake never completes
        self.addCleanup(h.stop)
        with self.assertRaises(fn.NetRefused):
            fc.NetSession("127.0.0.1", h.port, "Sam", MARKER, "Deck", "", connect_timeout=1).start()
        text = read(self.tmp)
        self.assertIn("connect         OK", text)
        self.assertRegex(text, r"tls\s+FAILED")
        self.assertRegex(text, r"END FAILED: (no_answer|tls)")

    def test_a_listener_that_hangs_up_fails_the_tls_step_with_its_error(self):
        h = FakeHost("close").start()                           # accepts, then closes at once: the TLS handshake breaks
        self.addCleanup(h.stop)
        with self.assertRaises(fn.NetRefused) as cm:
            fc.NetSession("127.0.0.1", h.port, "Sam", MARKER, "Deck", "", connect_timeout=2).start()
        self.assertEqual(cm.exception.reason, "tls")
        self.assertRegex(read(self.tmp), r"tls\s+FAILED\s+\d+ ms\s+error: \w+")
        self.assertIn("END FAILED: tls", read(self.tmp))

    @unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a test certificate")
    def test_every_step_of_a_good_join(self):
        cert = make_cert(self.tmp)
        self.assertIsNotNone(cert)
        h = TlsHost(cert, after=[{"t": "state", "turn": 3}])
        self.addCleanup(h.close)
        s = fc.NetSession("127.0.0.1", h.port, "Sam", MARKER, "Deck", "[metadata]\n", fingerprint=h.fp)
        s.start()
        try:
            self.assertTrue(wait_for(lambda: "game start" in read(self.tmp), 5, pump=[s]))
        finally:
            s.close()
        text = read(self.tmp)
        for step in ("resolve         OK", "connect         OK", "tls             OK", "TLSv1.3", "fingerprint     OK",
                     "hello           OK", "seat 2, host 'Karl'", "rejoin token given", "version check   OK",
                     "game start      OK", "(turn 3)", "END closed by this table"):
            self.assertIn(step, text)
        self.assertNotIn(MARKER, text)
        self.assertNotIn(TOKEN, text)

    @unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a test certificate")
    def test_a_wrong_fingerprint_is_logged_as_such(self):
        h = TlsHost(make_cert(self.tmp))
        self.addCleanup(h.close)
        with self.assertRaises(fn.NetRefused) as cm:
            fc.NetSession("127.0.0.1", h.port, "Sam", MARKER, "Deck", "", fingerprint="0" * 64).start()
        self.assertEqual(cm.exception.reason, "fingerprint")
        self.assertRegex(read(self.tmp), r"fingerprint\s+FAILED .*the invite code says 0000000000000000")

    def test_a_refusal_names_the_reason(self):
        h = FakeHost("refused:version:host speaks v1").start()
        self.addCleanup(h.stop)
        with mock.patch.object(fc.NetSession, "_connect", staticmethod(lambda a, p, fp=None, timeout=10, **k:
                                                                         socket.create_connection((a, int(p)), timeout))):
            with self.assertRaises(fn.NetRefused):
                fc.NetSession("127.0.0.1", h.port, "Sam", MARKER, "Deck", "").start()
        text = read(self.tmp)
        self.assertRegex(text, r"hello\s+FAILED .*the host refused: version host speaks v1")
        self.assertIn("END FAILED: version", text)


# ---------------------------------------------------------------------------------------------------------------------
# 4. the host's attempt, with a stand-in bridge
# ---------------------------------------------------------------------------------------------------------------------
FAKE_NETHOST = textwrap.dedent('''
    import json, os, sys, time
    def out(m):
        print(json.dumps(m)); sys.stdout.flush()
    sys.stderr.write("nethost: handshake with /198.51.100.9:50123 failed: java.net.SocketTimeoutException: Read timed out\\n")
    sys.stderr.write("nethost: 5 wrong passwords from /198.51.100.9; ignoring it for 60 s\\n")
    sys.stderr.flush()
    out({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})
    out({"t": "upnp", "ok": False, "reason": "no_router"})
    out({"t": "join_refused", "reason": "password"})
    out({"t": "guest_joined", "name": "Sam", "code": "othercode", "seat": 2, "joined": 1, "wanted": 1})
    out({"t": "ready", "protocol": 2})
    out({"t": "state", "turn": 1})
    sys.stderr.write("nethost: Sam's connection dropped (reading failed); holding the seat for 60 s\\n")
    sys.stderr.flush()
    out({"t": "peer_dropped", "who": "guest", "name": "Sam", "why": "reading failed", "grace": 60, "seat": 2})
    for line in sys.stdin:
        if '"quit"' in line:
            break
''')


class HostLogTests(TempFolder):
    def test_the_host_attempt_and_the_engines_own_lines(self):
        script = os.path.join(self.tmp, "fake_nethost.py")
        with open(script, "w") as f:
            f.write(FAKE_NETHOST)
        cert = fn.HostCert(os.path.join(self.tmp, "host.p12"), "storepass-" + MARKER, "cd" * 32)
        s = fc.HostSession("me.dck", "Karl", 36800, MARKER, cert, "guest.dck", upnp=True, code="c0de", seed=1)
        s.command = [sys.executable, script]
        s.stderr_path = os.path.join(self.tmp, "forge_engine.log")
        s.start()
        try:
            self.assertTrue(wait_for(lambda: s.peer_dropped, 10, pump=[s]))
        finally:
            s.close()
        text = read(self.tmp)
        for needle in ("HOST attempt", "port: 36800", "bind: 0.0.0.0", "upnp: on", "friends wanted: 1", "start engine    OK",
                       "listen          OK", "NetHost listening on 0.0.0.0 port 36800", "upnp            FAILED",
                       "no_router", "join refused    FAILED", "password", "accept + hello  OK", "'Sam' joined",
                       "version check   INFO", "guest's code othercode", "a warning only", "engine ready", "game start      OK",
                       "drop            FAILED", "reading failed",
                       "engine: nethost: handshake with /198.51.100.9:50123 failed",
                       "engine: nethost: 5 wrong passwords from /198.51.100.9",
                       "engine: nethost: Sam's connection dropped", "END closed by the host's table"):
            self.assertIn(needle, text)
        self.assertEqual(text.count("handshake with /198.51.100.9"), 1)          # copied once, not once per message
        self.assertNotIn(MARKER, text)

    def test_an_engine_that_cannot_start(self):
        cert = fn.HostCert(os.path.join(self.tmp, "host.p12"), "x", "cd" * 32)
        s = fc.HostSession("me.dck", "Karl", 36800, MARKER, cert, "guest.dck", seed=1)
        s.command = [os.path.join(self.tmp, "no_such_program")]
        s.stderr_path = os.path.join(self.tmp, "forge_engine.log")
        with self.assertRaises(OSError):
            s.start()
        self.assertRegex(read(self.tmp), r"start engine\s+FAILED .*error: FileNotFoundError")


# ---------------------------------------------------------------------------------------------------------------------
# 5. the bug report and the crash-report offer carry it
# ---------------------------------------------------------------------------------------------------------------------
class ReportTests(TempFolder):
    def zip_names(self, path):
        with zipfile.ZipFile(path) as z:
            return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}

    def test_f8_report_has_the_connection_log(self):
        net_log.Attempt("join", with_interfaces=False, port=36800).step("connect", False, 3, "x", "refused")
        files = self.zip_names(reporting.build_report({"name": "t", "happened": "x"}, folder=self.tmp))
        self.assertIn("connection_log.txt", files)
        self.assertIn("JOIN attempt", files["connection_log.txt"])
        self.assertIn("connect         FAILED", files["connection_log.txt"])

    def test_the_production_path_finds_it_too(self):
        """The crash-report offer and F8 at the table call build_report without a folder: the log comes from paths.log_dir()."""
        net_log.Attempt("host", with_interfaces=False, port=36800).end("cancelled by the host")
        dest = os.path.join(self.tmp, "reports")
        files = self.zip_names(reporting.build_report({"name": "t", "happened": "x"}, dest_dir=dest))
        self.assertIn("END cancelled by the host", files.get("connection_log.txt", ""))

    def test_no_log_no_file(self):
        files = self.zip_names(reporting.build_report({"name": "t", "happened": "x"}, folder=self.tmp))
        self.assertNotIn("connection_log.txt", files)

    def test_the_report_dialog_says_it_is_sent(self):
        self.assertIn("your online connection attempts of the last 7 days, if any", reporting.CONTENTS)    # NETLOG1c: 7 days


# ---------------------------------------------------------------------------------------------------------------------
# 6. the self-test
# ---------------------------------------------------------------------------------------------------------------------
LOOPBACK_ONLY = {"hostname": "test", "addresses": [("127.0.0.1", "loopback")], "default_route": None, "tailscale": None}


class SelftestTests(TempFolder):
    def test_a_real_loopback_listener_is_ok(self):
        lst = st.Listener("127.0.0.1", 0)
        self.addCleanup(lst.close)
        verdict, detail, ms, err = st.probe("127.0.0.1", lst.port, timeout=2)
        self.assertEqual(verdict, st.OK, detail)
        self.assertEqual(err, "")
        self.assertEqual(lst.seen, ["127.0.0.1"])

    # 24 Oct (Karl's first Windows run of the merge pack): with timeout=2 this said "timed out" on Windows, which tries a refused
    # connection again before it gives up (more than 2 s on Karl's PC; Linux refuses at once). The first test checks the verdict
    # with room to spare; the second checks that the self-test's own TIMEOUT is long enough on this PC.
    def test_a_closed_port_is_refused(self):
        verdict, detail, ms, err = st.probe("127.0.0.1", free_port(), timeout=10)
        self.assertEqual(verdict, st.REFUSED, "after %.0f ms: %s %s" % (ms, detail, err))
        self.assertIn("ConnectionRefusedError", err)

    def test_a_closed_port_is_refused_within_the_self_tests_own_timeout(self):
        verdict, detail, ms, err = st.probe("127.0.0.1", free_port())
        self.assertEqual(verdict, st.REFUSED, "TIMEOUT %.1f s, after %.0f ms: %s" % (st.TIMEOUT, ms, detail))

    def test_a_listener_that_never_answers(self):
        lst = st.Listener("127.0.0.1", 0, silent=True)
        self.addCleanup(lst.close)
        t = time.monotonic()
        verdict, detail, ms, err = st.probe("127.0.0.1", lst.port, timeout=1)
        self.assertEqual(verdict, st.NO_ANSWER)
        self.assertLess(time.monotonic() - t, 3)

    def test_another_program_on_the_port(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)

        def serve():
            c, _ = srv.accept()
            c.recv(100)
            c.sendall(b"HTTP/1.0 400 Bad Request\r\n\r\n")
            c.close()
        threading.Thread(target=serve, daemon=True).start()
        verdict, detail, _ms, _err = st.probe("127.0.0.1", srv.getsockname()[1], timeout=2)
        self.assertEqual(verdict, st.NO_ANSWER)
        self.assertIn("another program", detail)

    def test_a_connect_that_times_out_and_one_with_no_route(self):
        with mock.patch.object(socket, "create_connection", side_effect=socket.timeout("timed out")):
            self.assertEqual(st.probe("192.0.2.50", 36800, timeout=0.1)[0], st.TIMED_OUT)
        with mock.patch.object(socket, "create_connection", side_effect=OSError(101, "Network is unreachable")):
            self.assertEqual(st.probe("192.0.2.50", 36800, timeout=0.1)[0], st.NO_ROUTE)
        err = OSError(None, "unreachable")
        err.winerror = 10065
        with mock.patch.object(socket, "create_connection", side_effect=err):
            self.assertEqual(st.probe("192.0.2.50", 36800, timeout=0.1)[0], st.NO_ROUTE)

    def test_run_everything_ok_and_the_log(self):
        port = free_port()
        lines = []
        code, results = st.run(port=port, timeout=2, out=lines.append, info=LOOPBACK_ONLY, firewall=False)
        self.assertEqual(code, 0)
        self.assertEqual([(r[0], r[2]) for r in results], [("loopback", st.OK)])
        printed = "\n".join(lines)
        self.assertRegex(printed, r"loopback\s+127\.0\.0\.1\s+OK")
        self.assertIn("Only 127.0.0.1 could be tried", printed)
        text = read(self.tmp)
        self.assertIn("SELFTEST attempt", text)
        self.assertIn("listen          OK", text)
        self.assertRegex(text, r"join loopback\s+OK")
        self.assertIn("advice: ", text)
        self.assertIn("END 1 of 1 addresses answered", text)

    def test_run_one_address_refused(self):
        lst = st.Listener("127.0.0.1", 0)                  # listening on loopback only: the "LAN" address is turned away
        self.addCleanup(lst.close)
        info = {"hostname": "t", "addresses": [("127.0.0.1", "loopback"), ("127.0.0.2", "lan")], "default_route": None,
                "tailscale": None}
        lines = []
        code, results = st.run(port=lst.port, timeout=1, out=lines.append, info=info, firewall=False, listener=lst)
        self.assertEqual(code, 1)
        verdicts = {r[0]: r[2] for r in results}
        self.assertEqual(verdicts["loopback"], st.OK)
        self.assertNotEqual(verdicts["LAN"], st.OK)
        self.assertIn("Only 127.0.0.1 works", "\n".join(lines))
        self.assertRegex(read(self.tmp), r"join LAN\s+FAILED")

    def test_a_busy_port_moves_to_a_free_one_and_says_so(self):
        busy = socket.socket()
        busy.bind(("0.0.0.0", 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        port = busy.getsockname()[1]
        lines = []
        with mock.patch.object(st, "Listener", side_effect=[OSError(98, "Address already in use"),
                                                            st.Listener("127.0.0.1", 0)]):
            code, results = st.run(port=port, timeout=1, out=lines.append, info=LOOPBACK_ONLY, firewall=False)
        self.assertEqual(code, 0)
        self.assertIn("Port %d was busy" % port, "\n".join(lines))
        self.assertRegex(read(self.tmp), r"listen\s+FAILED .*Address already in use")

    def test_nothing_can_listen(self):
        with mock.patch.object(st, "Listener", side_effect=OSError(13, "Permission denied")):
            code, results = st.run(port=1, timeout=1, out=lambda s: None, info=LOOPBACK_ONLY, firewall=False)
        self.assertEqual((code, results), (2, []))
        self.assertIn("END FAILED: could not listen", read(self.tmp))

    def test_ways_in_and_advice(self):
        info = {"addresses": [("127.0.0.1", "loopback"), ("192.168.1.20", "lan"), ("100.70.1.2", "tailscale"),
                              ("fe80::1", "ipv6-link-local")], "tailscale": "100.70.1.2"}
        self.assertEqual(st.ways_in(info), [("loopback", "127.0.0.1"), ("LAN", "192.168.1.20"), ("Tailscale", "100.70.1.2")])
        ok = [("loopback", "127.0.0.1", st.OK, "", 1, ""), ("LAN", "192.168.1.20", st.OK, "", 1, ""),
              ("Tailscale", "100.70.1.2", st.OK, "", 1, "")]
        text = "\n".join(st.advice(ok, info, 36800, 36800))
        self.assertIn("does NOT prove another PC can get in", text)
        self.assertIn("Windows Firewall may be blocking java.exe", text)
        self.assertIn("Tailscale is on (100.70.1.2)", text)
        only_loop = ok[:1] + [("LAN", "192.168.1.20", st.TIMED_OUT, "", 1, ""), ("Tailscale", "100.70.1.2", st.REFUSED, "", 1, "")]
        self.assertIn("Only 127.0.0.1 works", "\n".join(st.advice(only_loop, info, 36800, 36800)))
        some = ok[:2] + [("Tailscale", "100.70.1.2", st.REFUSED, "", 1, "")]
        self.assertIn("Only some addresses answered (not: Tailscale)", "\n".join(st.advice(some, info, 36800, 36800)))
        bad_loop = [("loopback", "127.0.0.1", st.REFUSED, "", 1, "")]
        self.assertIn("Not even 127.0.0.1 worked", "\n".join(st.advice(bad_loop, info, 36800, 36800)))

    def test_firewall_lines_off_windows_and_parsed_on_it(self):
        if os.name != "nt":
            self.assertEqual(st.firewall_lines(), [])
        state = "Domain Profile Settings:\n----\nState                                 ON\n"
        rules = ("Rule Name:   Java(TM) Platform SE binary\nEnabled:  Yes\nDirection: In\nProfiles: Public\nAction: Block\n"
                 "Program:  C:\\Program Files\\Manticore\\jre\\bin\\java.exe\n\nRule Name: Other\nProgram: x.exe\n")
        fake = mock.Mock(side_effect=[mock.Mock(stdout=state), mock.Mock(stdout=rules)])
        with mock.patch.object(st.os, "name", "nt"):
            out = st.firewall_lines(run=fake)
        joined = "\n".join(out)
        self.assertIn("State", joined)
        self.assertIn("Action: Block", joined)
        self.assertIn("1 inbound rule(s)", joined)

    def test_the_program_has_the_option(self):
        import forge_table
        self.assertTrue(forge_table.parse_args(["x", "--net-selftest"]).net_selftest)
        self.assertFalse(forge_table.parse_args(["x"]).net_selftest)


# ---------------------------------------------------------------------------------------------------------------------
# 6b. live: the real NetHost and the real TLS connection on 127.0.0.1
# ---------------------------------------------------------------------------------------------------------------------
import tests.live as live  # noqa: E402


@unittest.skipUnless(live.live_enabled(), "needs Java and Forge (MANTICORE_SKIP_LIVE unset)")
class LiveTests(TempFolder):
    def test_a_wrong_password_then_a_game_both_sides_logged(self):
        from tests.test_mp1 import deck_file
        fc.sync_bridge()
        cert = fn.ensure_host_cert(self.tmp, fc.find_java())
        host_deck = deck_file(self.tmp, "typal_lathril.txt", "Lathril")
        guest_deck = deck_file(self.tmp, "spellslinger_veyran.txt", "Veyran")
        port = free_port()
        h = fc.HostSession(host_deck, "Karl", port, MARKER, cert, os.path.join(self.tmp, "guest.dck"), upnp=False,
                           bind="127.0.0.1", code=version.code_fingerprint(), seed=5)
        h.stderr_path = os.path.join(self.tmp, "host_engine.log")
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        with open(guest_deck, encoding="utf-8") as f:
            dck = f.read()
        with self.assertRaises(fn.NetRefused) as cm:
            fc.NetSession("127.0.0.1", port, "Sam", "not-" + MARKER, "Veyran", dck, fingerprint=cert.fingerprint).start()
        self.assertEqual(cm.exception.reason, "password")
        g = fc.NetSession("127.0.0.1", port, "Sam", MARKER, "Veyran", dck, fingerprint=cert.fingerprint,
                          code=version.code_fingerprint())
        g.start()
        self.addCleanup(g.close)
        self.assertTrue(wait_for(lambda: "game start" in read(self.tmp).split("JOIN attempt")[-1] and h._logged_start,
                                 90, pump=[h, g]), read(self.tmp))
        g.close()
        h.close()
        text = read(self.tmp)
        host_block = text[text.index("HOST attempt"):text.index("JOIN attempt")]
        first_join, second_join = text.split("JOIN attempt")[1:3]
        for needle in ("start engine    OK", "listen          OK", "port %d" % port, "join refused    FAILED",
                       "password", "accept + hello  OK", "'Sam' joined", "version check   OK"):
            self.assertIn(needle, text)
        self.assertIn("listen          OK", host_block)
        self.assertRegex(first_join, r"tls\s+OK .*TLSv1\.3")
        self.assertIn("fingerprint     OK", first_join)
        self.assertRegex(first_join, r"hello\s+FAILED .*the host refused: password")
        for needle in ("hello           OK", "version check   OK", "game start      OK", "END closed by this table"):
            self.assertIn(needle, second_join)
        self.assertIn("END closed by the host's table", text)
        self.assertIn("engine: nethost:", text)                           # NetHost's own lines were copied over
        self.assertNotIn(MARKER, text)
        self.assertNotIn(cert.store_pass, text)


# ---------------------------------------------------------------------------------------------------------------------
# 7. the log holds network addresses: never committed, backed up to GitHub or published
# ---------------------------------------------------------------------------------------------------------------------
class KeptPrivateTests(unittest.TestCase):
    NAMES = ("connection_log.txt", "connection_log.1.txt", "connection_log.2.txt", "connection_log.3.txt")

    def test_git_backup_and_the_public_copy_leave_it_out(self):
        import backup
        from tools import export_public
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, ".gitignore"), encoding="utf-8") as f:
            ignore = f.read().split()
        self.assertIn("connection_log.txt", ignore)
        self.assertIn("connection_log.*.txt", ignore)
        for name in self.NAMES:
            self.assertIn(name, backup.SKIP_FILES)
            self.assertIn(name, export_public.SKIP_FILES)
        self.assertEqual(net_log.KEEP_OLD, 3)                 # the lists above name exactly the files rotation makes

    def test_quick_tests_watch_the_real_one(self):
        from tools import quick_tests
        self.assertIn("connection_log.txt", quick_tests.WATCHED)


if __name__ == "__main__":
    unittest.main()
