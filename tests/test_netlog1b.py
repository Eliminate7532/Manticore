# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_netlog1b.py - Round NETLOG1b: the fixes for the review of Round NETLOG1 (docs/REVIEW_NETLOG1_2026-10-24.md, R1-R9 and
G2). tests/test_review_netlog1.py holds the review's own tests (one or two per finding); these cover what a fix needed beyond
them: the decoding itself (the review's R1 test can only reach the second layer, the catch), the parts of a fix the review's
test doesn't see, and that nothing new leaks. Headless; no Forge, no second PC, nothing written outside a temporary folder.
"""
import os
import shutil
import socket
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from unittest import mock

import forge_client as fc
import forge_net as fn
import net_log
from tests.test_netlog1 import TlsHost, make_cert
from tools import net_selftest as st

PASSWORD = "qzxv-wkjp-ymrt-58"


def read(folder):
    try:
        with open(net_log.log_path(folder), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


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
        self.tmp = tempfile.mkdtemp(prefix="netlog1b_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": self.tmp})
        env.start()
        self.addCleanup(env.stop)


# ---------------------------------------------------------------------------------------------------------------------
# R1: netsh's output is decoded by the self-test, not by Python's text=True
# ---------------------------------------------------------------------------------------------------------------------
# What netsh prints for a German Windows, as bytes in code page 850 (made here from the English layout the round's own test
# uses, with German key names - NOT recorded on a real PC; no Windows here). 0x81 is u-umlaut in cp850 and nothing in cp1252.
GERMAN_RULES = ("Regelname:                            Remoteunterst\u00fctzung (RA-Server TCP-In)\r\n"
                "Aktiviert:                            Ja\r\n"
                "Programm:                             %SystemRoot%\\system32\\raserver.exe\r\n"
                "\r\n"
                "Regelname:                            Java(TM) Platform SE binary\r\n"
                "Aktiviert:                            Ja\r\n"
                "Profile:                              \u00d6ffentlich\r\n"
                "Aktion:                               Blockieren\r\n"
                "Programm:                             C:\\Program Files\\Manticore\\jre\\bin\\java.exe\r\n"
                "\r\nOK.\r\n").encode("cp850")
STATE = "Profileinstellungen f\u00fcr Dom\u00e4ne:\r\n----------\r\nStatus                                EIN\r\n".encode("cp850")


class DecodeTests(unittest.TestCase):
    def fake_run(self, outputs):
        """Like subprocess.run on an English or German Windows: with text=True (or an encoding) Python decodes the bytes
        itself, with the ANSI code page cp1252 unless told otherwise - and raises on a byte cp1252 has no character for."""
        calls = []

        def run(cmd, **kw):
            calls.append(kw)
            data = outputs[len(calls) - 1]
            if kw.get("text") or kw.get("encoding") or kw.get("universal_newlines"):
                data = data.decode(kw.get("encoding") or "cp1252", kw.get("errors") or "strict")
            return types.SimpleNamespace(stdout=data)
        return run, calls

    def test_oem_bytes_are_read_as_oem_text(self):
        run, calls = self.fake_run([STATE, GERMAN_RULES])
        with mock.patch.object(st.os, "name", "nt"):
            out = st.firewall_lines(run=run, oem="cp850")
        text = "\n".join(out)
        self.assertIn("Dom\u00e4ne", text)
        self.assertIn("1 inbound rule(s)", text)               # the Java rule; the umlaut rule doesn't name java
        self.assertIn("Blockieren", text)                       # German keys: the rule's own first lines are kept
        self.assertIn("\u00d6ffentlich", text)
        for kw in calls:                                        # Python never decodes netsh's output itself any more
            self.assertNotIn("text", kw)
            self.assertNotIn("encoding", kw)

    def test_decode_output_never_raises(self):
        self.assertEqual(st.decode_output("already text"), "already text")
        self.assertEqual(st.decode_output(None), "")
        self.assertEqual(st.decode_output("gr\u00fc\u00df".encode("utf-8")), "gr\u00fc\u00df")       # a PC set to UTF-8
        self.assertEqual(st.decode_output(b"Remoteunterst\x81tzung", oem="cp850"), "Remoteunterst\u00fctzung")
        self.assertIsInstance(st.decode_output(b"\x81\x8d\x8f\x90\x9d", oem=None), str)          # no OEM page known
        self.assertIsInstance(st.decode_output(b"\x81", oem="no-such-codec"), str)

    def test_a_failing_first_command_still_reads_the_rules(self):
        rules = ("Rule Name: Java(TM) Platform SE binary\r\nEnabled: Yes\r\nAction: Allow\r\n\r\n").encode("ascii")

        def run(cmd, **kw):
            if "state" in cmd:
                raise UnicodeDecodeError("cp1252", b"\x81", 0, 1, "character maps to <undefined>")
            return types.SimpleNamespace(stdout=rules)
        with mock.patch.object(st.os, "name", "nt"):
            out = st.firewall_lines(run=run)
        text = "\n".join(out)
        self.assertIn("profiles could not be read (UnicodeDecodeError", text)
        self.assertIn("1 inbound rule(s)", text)
        self.assertIn("Action: Allow", text)


# ---------------------------------------------------------------------------------------------------------------------
# R2: the host's addresses are read beside the window's thread, and still reach the log
# ---------------------------------------------------------------------------------------------------------------------
class BackgroundAddressTests(TempFolder):
    def test_the_same_addresses_arrive_as_a_block_that_names_the_attempt(self):
        a = net_log.Attempt("host", with_interfaces="background", port=36800, tailscale_cli=lambda: "100.70.1.2")
        self.assertTrue(wait_for(lambda: "Tailscale: yes, 100.70.1.2" in read(self.tmp), 5))
        text = read(self.tmp)
        self.assertRegex(text, r"\n  %s \+ *\d+\.\d+s  this PC: .*\(read in \d+ ms on a thread of their own\)\n" % a.id)
        for needle in ("address 127.0.0.1", "loopback", "internet route via:", "address 100.70.1.2"):
            self.assertIn(needle, text)
        self.assertEqual(a.info.get("tailscale"), "100.70.1.2")
        b = net_log.Attempt("host", port=36800, tailscale_cli=lambda: "100.70.1.2")     # the old way, for comparison
        text = read(self.tmp)
        mine = [x for x in net_log.interface_lines(b.info)[1:]]
        for line in mine:
            self.assertEqual(text.count(line), 2, line)           # every address line twice: once per attempt

    def test_a_lookup_that_ends_after_its_folder_is_gone_writes_nothing(self):
        gate = threading.Event()

        def slow():
            gate.wait(5)
            return None
        folder = os.path.join(self.tmp, "gone")
        net_log.Attempt("host", folder=folder, with_interfaces="background", port=1, tailscale_cli=slow)
        shutil.rmtree(folder)
        gate.set()
        time.sleep(0.3)
        self.assertFalse(os.path.exists(folder), "the folder was made again by a lookup that finished late")

    def test_a_lookup_writes_where_the_attempt_began(self):
        gate = threading.Event()
        other = tempfile.mkdtemp(prefix="netlog1b_other_")
        self.addCleanup(shutil.rmtree, other, True)

        def slow():
            gate.wait(5)
            return None
        net_log.Attempt("host", with_interfaces="background", port=1, tailscale_cli=slow)
        with mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": other}):       # the logs folder changes meanwhile
            gate.set()
            self.assertTrue(wait_for(lambda: "this PC:" in read(self.tmp), 5))
        self.assertEqual(read(other), "")


# ---------------------------------------------------------------------------------------------------------------------
# R4: NetHost's lines - whole lines only, and a late copy says when it was written
# ---------------------------------------------------------------------------------------------------------------------
class HarvestTests(TempFolder):
    def session(self):
        cert = fn.HostCert(os.path.join(self.tmp, "host.p12"), "storepass", "cd" * 32)
        s = fc.HostSession("me.dck", "Karl", 36800, PASSWORD, cert, "guest.dck", upnp=False, code="c0de", seed=1)
        s.stderr_path = os.path.join(self.tmp, "forge_engine.log")
        s._attempt = net_log.Attempt("host", with_interfaces=False, port=36800)
        s._harvest_mono = time.monotonic()
        return s

    def test_half_a_line_waits_for_the_rest(self):
        s = self.session()
        with open(s.stderr_path, "w") as f:
            f.write("nethost: handshake with /198.51.100.9:5 fai")
        s._harvest_nethost()
        self.assertNotIn("handshake", read(self.tmp))
        with open(s.stderr_path, "a") as f:
            f.write("led: java.io.EOFException\nnethost: no hello from /198.51.100.9:6 wit")
        s._harvest_nethost()
        self.assertIn("engine: nethost: handshake with /198.51.100.9:5 failed: java.io.EOFException", read(self.tmp))
        self.assertNotIn("no hello", read(self.tmp))
        s._harvest_nethost(final=True)                          # the end: whatever is there
        self.assertIn("engine: nethost: no hello from /198.51.100.9:6 wit", read(self.tmp))
        self.assertEqual(read(self.tmp).count("handshake with"), 1)

    def test_a_late_copy_says_when_the_line_was_written(self):
        s = self.session()
        with open(s.stderr_path, "w") as f:
            f.write("nethost: no hello from /198.51.100.9:6 within 15 s\n")
        s._harvest_mono = time.monotonic() - 10                 # the window hasn't polled for 10 s
        s._harvest_nethost()
        self.assertRegex(read(self.tmp), r"engine: nethost: no hello from .*\(copied late: written between \+\d+\.\d s and "
                                         r"\+\d+\.\d s\)")
        with open(s.stderr_path, "a") as f:
            f.write("nethost: 5 wrong passwords from /198.51.100.9; ignoring it for 60 s\n")
        s._harvest_nethost()                                    # a moment later: not late
        line = [x for x in read(self.tmp).splitlines() if "wrong passwords" in x][0]
        self.assertNotIn("copied late", line)

    def test_poll_copies_on_its_own_without_a_message(self):
        s = self.session()
        with open(s.stderr_path, "w") as f:
            f.write("nethost: no hello from /198.51.100.9:6 within 15 s\n")
        s._next_harvest = 0.0
        s.poll()
        self.assertIn("no hello from", read(self.tmp))


# ---------------------------------------------------------------------------------------------------------------------
# R5: why the game's port couldn't be used
# ---------------------------------------------------------------------------------------------------------------------
class ListenProblemTests(TempFolder):
    INFO = {"addresses": [("127.0.0.1", "loopback")], "tailscale": None}

    def denied(self):
        e = PermissionError(13, "An attempt was made to access a socket in a way forbidden by its access permissions")
        e.winerror = 10013
        return e

    def test_the_kinds(self):
        busy_win = OSError(10048, "Only one usage of each socket address is normally permitted")
        busy_win.winerror = 10048
        for e, kind in ((OSError(98, "Address already in use"), "busy"), (busy_win, "busy"), (self.denied(), "denied"),
                        (OSError(13, "Permission denied"), "denied"), (OSError(10049, "not valid in its context"), "other")):
            self.assertEqual(st.listen_problem(e), kind, e)

    def test_a_reserved_port_fails_the_test_and_says_what_to_do(self):
        lines = []
        with mock.patch.object(st, "Listener", side_effect=[self.denied(), st.Listener("127.0.0.1", 0)]):
            code, _ = st.run(port=36800, timeout=1, out=lines.append, info=self.INFO, firewall=False)
        printed = "\n".join(lines)
        self.assertEqual(code, 1, printed)                      # a hosted game couldn't listen there either
        self.assertIn("Windows would not let a program listen on port 36800", printed)
        self.assertIn("excludedportrange", printed)
        self.assertIn("choose another port", printed)
        self.assertRegex(read(self.tmp), r"listen\s+FAILED .*port 36800 \(denied\).*WinError 10013")
        self.assertIn("port 36800 can't be used", read(self.tmp))

    def test_another_error_is_named(self):
        lines = []
        err = OSError(10049, "The requested address is not valid in its context")
        with mock.patch.object(st, "Listener", side_effect=[err, st.Listener("127.0.0.1", 0)]):
            code, _ = st.run(port=36800, timeout=1, out=lines.append, info=self.INFO, firewall=False)
        printed = "\n".join(lines)
        self.assertEqual(code, 1)
        self.assertIn("Port 36800 couldn't be used (OSError [errno 10049]", printed)
        self.assertNotIn("was busy", printed)


# ---------------------------------------------------------------------------------------------------------------------
# R6: a 100.64/10 address the tailscale program didn't confirm is a "maybe"
# ---------------------------------------------------------------------------------------------------------------------
class CarrierNatTests(unittest.TestCase):
    INFO = {"hostname": "surface", "addresses": [("127.0.0.1", "loopback"), ("100.88.7.6", "tailscale")],
            "default_route": "100.88.7.6", "tailscale": "100.88.7.6", "tailscale_cli": None}

    def test_the_header_and_the_advice_say_maybe(self):
        header = "\n".join(net_log.interface_lines(self.INFO))
        self.assertIn("Tailscale: maybe - 100.88.7.6", header)
        self.assertIn("carrier-grade NAT", header)
        ok = [("loopback", "127.0.0.1", st.OK, "", 1, ""), ("Tailscale", "100.88.7.6", st.OK, "", 1, "")]
        text = "\n".join(st.advice(ok, self.INFO, 36800, 36800))
        self.assertNotIn("Tailscale is on", text)
        self.assertIn("may be a mobile or internet provider's carrier-grade NAT address", text)

    def test_a_confirmed_and_answering_tailscale_is_still_advised(self):
        info = dict(self.INFO, tailscale_cli="100.88.7.6")
        ok = [("loopback", "127.0.0.1", st.OK, "", 1, ""), ("Tailscale", "100.88.7.6", st.OK, "", 1, "")]
        self.assertIn("Tailscale is on (100.88.7.6): a friend on the same tailnet", "\n".join(st.advice(ok, info, 36800, 36800)))
        self.assertIn("Tailscale: yes, 100.88.7.6", "\n".join(net_log.interface_lines(info)))

    def test_only_loopback_and_tailscale_failed_does_not_offer_tailscale(self):
        info = dict(self.INFO, tailscale_cli="100.88.7.6")
        bad = [("loopback", "127.0.0.1", st.OK, "", 1, ""), ("Tailscale", "100.88.7.6", st.REFUSED, "", 1, "")]
        text = "\n".join(st.advice(bad, info, 36800, 36800))
        self.assertIn("Only 127.0.0.1 works", text)
        self.assertNotRegex(text, r"Only 127\.0\.0\.1 works[^\n]*or Tailscale")
        self.assertIn("couldn't reach itself through that address", text)


# ---------------------------------------------------------------------------------------------------------------------
# R7: a rotation never loses an old file for nothing
# ---------------------------------------------------------------------------------------------------------------------
class RotationTests(TempFolder):
    def fill(self):
        texts = {net_log.log_path(self.tmp): "CURRENT " * 40, net_log.old_path(self.tmp, 1): "ONE\n",
                 net_log.old_path(self.tmp, 2): "TWO\n", net_log.old_path(self.tmp, 3): "THREE\n"}
        for path, text in texts.items():
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

    def everything(self):
        out = ""
        for name in os.listdir(self.tmp):
            with open(os.path.join(self.tmp, name), encoding="utf-8") as f:
                out += f.read()
        return out

    def test_an_old_file_that_cannot_move_stops_the_shift_and_loses_nothing(self):
        self.fill()
        real = os.replace
        held = os.path.abspath(net_log.old_path(self.tmp, 1))      # F8's tail() is reading .1 just then

        def replace(src, dst):
            if os.path.abspath(src) == held:
                raise PermissionError(13, "being used by another process")
            return real(src, dst)
        with mock.patch.object(net_log, "MAX_BYTES", 100), mock.patch.object(net_log.os, "replace", replace):
            net_log.write_lines(["a new line"], folder=self.tmp)
        for marker in ("CURRENT", "ONE", "TWO", "a new line"):     # .3 ("THREE") is the one a rotation drops
            self.assertIn(marker, self.everything())
        self.assertFalse(os.path.exists(net_log.spare_path(self.tmp)))

    def test_a_normal_rotation_leaves_no_spare_and_drops_only_the_oldest(self):
        self.fill()
        with mock.patch.object(net_log, "MAX_BYTES", 100):
            net_log.write_lines(["a new line"], folder=self.tmp)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["connection_log.1.txt", "connection_log.2.txt", "connection_log.3.txt",
                                                        "connection_log.txt"])
        everything = self.everything()
        self.assertNotIn("THREE", everything)
        for marker in ("CURRENT", "ONE", "TWO", "a new line"):
            self.assertIn(marker, everything)

    def test_a_spare_left_by_an_unfinished_rotation_is_placed_next_time(self):
        self.fill()
        with open(net_log.spare_path(self.tmp), "w", encoding="utf-8") as f:
            f.write("LEFT OVER\n")
        with mock.patch.object(net_log, "MAX_BYTES", 100):
            net_log.write_lines(["a new line"], folder=self.tmp)
        self.assertFalse(os.path.exists(net_log.spare_path(self.tmp)))
        with open(net_log.old_path(self.tmp, 1), encoding="utf-8") as f:
            self.assertEqual(f.read(), "LEFT OVER\n")
        for marker in ("CURRENT", "ONE", "TWO", "a new line"):     # the current file waits for the next rotation
            self.assertIn(marker, self.everything())

    def test_the_spare_name_is_kept_private_like_the_others(self):
        import backup
        from tools import export_public
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, ".gitignore"), encoding="utf-8") as f:
            ignore = f.read().split()
        self.assertIn("connection_log.*.txt", ignore)               # connection_log.rotating.txt matches it
        self.assertTrue(net_log.SPARE_NAME.startswith("connection_log.") and net_log.SPARE_NAME.endswith(".txt"))
        self.assertIn(net_log.SPARE_NAME, backup.SKIP_FILES)
        self.assertIn(net_log.SPARE_NAME, export_public.SKIP_FILES)


# ---------------------------------------------------------------------------------------------------------------------
# R8: broken invite codes
# ---------------------------------------------------------------------------------------------------------------------
class InviteRedactTests(unittest.TestCase):
    def test_the_words_after_a_whole_code_stay(self):
        code = fn.make_invite("203.0.113.7", 36800, "ab" * 32, PASSWORD)
        self.assertEqual(net_log.redact("pasted " + code + " then pressed Join"),
                         "pasted MANT1-<invite code left out> then pressed Join")
        broken = code[:50] + "\n" + code[50:100] + " " + code[100:]
        self.assertEqual(net_log.redact("pasted " + broken + " then pressed Join"),
                         "pasted MANT1-<invite code left out> then pressed Join")

    def test_a_cut_short_code_takes_what_follows_with_it(self):
        code = fn.make_invite("203.0.113.7", 36800, "ab" * 32, PASSWORD)
        out = net_log.redact("pasted " + code[:60] + " " + code[60:-5] + " more words")
        self.assertEqual(out, "pasted MANT1-<invite code left out>")


# ---------------------------------------------------------------------------------------------------------------------
# G2: where the expected host certificate came from
# ---------------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a test certificate")
class FingerprintSourceTests(TempFolder):
    def test_a_join_typed_by_hand_names_this_tables_memory(self):
        h = TlsHost(make_cert(self.tmp))
        self.addCleanup(h.close)
        with self.assertRaises(fn.NetRefused):
            fc.NetSession("127.0.0.1", h.port, "Sam", PASSWORD, "Deck", "", fingerprint="0" * 64,
                          fingerprint_from="remembered").start()
        text = read(self.tmp)
        self.assertIn("fingerprint: from this table's memory of 127.0.0.1:%d from an earlier game (0000" % h.port, text)
        self.assertRegex(text, r"fingerprint\s+FAILED .*this table's memory of 127\.0\.0\.1:\d+ from an earlier game says 0{16}")
        self.assertNotIn("from the invite code", text)
        self.assertNotIn("the invite code says", text)

    def test_a_reconnect_names_the_first_connection(self):
        h = TlsHost(make_cert(self.tmp))
        self.addCleanup(h.close)
        s = fc.NetSession("127.0.0.1", h.port, "Sam", PASSWORD, "Deck", "", fingerprint="0" * 64)
        s._attempt = net_log.Attempt("join", with_interfaces=False, port=h.port)
        s._peer_fp = "1" * 64                                    # seen on the game's first connection
        with self.assertRaises(fn.NetRefused):
            s._handshake(fn.rejoin_line("t"))
        self.assertRegex(read(self.tmp), r"fingerprint\s+FAILED .*this game's first connection says 1{16}")

    def test_the_table_says_which_it_is(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "forge_table.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count('fingerprint_from="remembered" if by_hand else "invite"'), 2)   # join and watch


# ---------------------------------------------------------------------------------------------------------------------
# R2 / R9 together: hosting through the real HostSession with a stand-in NetHost
# ---------------------------------------------------------------------------------------------------------------------
class HostStartTests(TempFolder):
    def test_hosting_waits_for_neither_lookup_and_the_log_still_has_them(self):
        script = os.path.join(self.tmp, "nh.py")
        with open(script, "w") as f:
            f.write(textwrap.dedent('''
                import json, sys
                print(json.dumps({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})); sys.stdout.flush()
                for line in sys.stdin:
                    if '"quit"' in line:
                        break
            '''))
        cert = fn.HostCert(os.path.join(self.tmp, "host.p12"), "storepass", "cd" * 32)
        s = fc.HostSession("me.dck", "Karl", 36800, PASSWORD, cert, "guest.dck", upnp=False, code="c0de", seed=1)
        s.command = [sys.executable, script]
        s.stderr_path = os.path.join(self.tmp, "forge_engine.log")

        def slow():
            time.sleep(1.0)
            return "100.70.1.2"
        real_fqdn = socket.getfqdn
        with mock.patch.object(fn, "tailscale_address", slow), \
                mock.patch.object(socket, "getfqdn", lambda *a: (time.sleep(1.0), real_fqdn(*a))[1]):
            t = time.monotonic()
            s.start()
            took = time.monotonic() - t
            try:
                self.assertLess(took, 0.75)
                self.assertTrue(wait_for(lambda: "Tailscale: yes, 100.70.1.2" in read(self.tmp), 6, pump=[s]))
            finally:
                s.close()
        text = read(self.tmp)
        self.assertIn("NetHost started (pid %s): %s" % (s.proc.pid, sys.executable), text)
        self.assertLess(text.index("start engine"), text.index("this PC:"))
        self.assertNotIn(PASSWORD, text)


if __name__ == "__main__":
    unittest.main()
