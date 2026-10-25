# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_netlog1d.py - Round NETLOG1d (the NETLOG1 review's gap G1): the network self-test says which networks Windows calls
Public and which Private, and names a Public network in its advice. Tested on Karl's own recorded output (24 Oct 2026, English
Windows: tests/fixtures/net/fw_profile.txt and fw_current.txt, as PowerShell's Out-File wrote them - a UTF-8 BOM and CRLF), on
made-up output, and on a PowerShell that is missing, fails or hangs. Headless; no Forge, no Windows needed.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import net_log
from tools import net_selftest as st

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(HERE, "fixtures", "net", "fw_profile.txt")
CURRENT_FILE = os.path.join(HERE, "fixtures", "net", "fw_current.txt")

ALL_OK = [("loopback", "127.0.0.1", st.OK, "", 1, ""), ("LAN", "192.168.1.20", st.OK, "", 1, "")]
INFO = {"hostname": "t", "addresses": [("127.0.0.1", "loopback")], "default_route": None, "tailscale": None}

ALL_PRIVATE = (b"\r\n\r\nInterfaceAlias  : Wi-Fi\r\nNetworkCategory : Private\r\n\r\n"
               b"InterfaceAlias  : Tailscale\r\nNetworkCategory : Private\r\n\r\n\r\n\r\n")
DOMAIN = b"\nInterfaceAlias:Ethernet 2\nNetworkCategory   :   DomainAuthenticated\n\n"


def raw(path):
    with open(path, "rb") as f:
        return f.read()


def done(stdout=b"", returncode=0, stderr=b""):
    return subprocess.CompletedProcess(st.PROFILE_COMMAND, returncode, stdout, stderr)


class KarlsFilesTests(unittest.TestCase):
    def test_the_fixtures_are_karls_files_unchanged(self):
        self.assertEqual(hashlib.sha256(raw(PROFILE_FILE)).hexdigest()[:8], "644a7b94")
        self.assertEqual(hashlib.sha256(raw(CURRENT_FILE)).hexdigest()[:8], "be8f5978")
        self.assertTrue(raw(PROFILE_FILE).startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", raw(PROFILE_FILE))

    def test_karls_profiles_with_the_bom(self):
        self.assertEqual(st.parse_profiles(st.decode_output(raw(PROFILE_FILE))),
                         [("Ethernet", "Public"), ("Tailscale", "Private")])

    def test_karls_profiles_without_the_bom(self):          # the program reads the command's own bytes: no BOM there
        self.assertEqual(st.parse_profiles(st.decode_output(raw(PROFILE_FILE)[3:])),
                         [("Ethernet", "Public"), ("Tailscale", "Private")])

    def test_karls_currentprofile_file_is_not_read_as_networks(self):
        # netsh's section headers (translated on other Windows languages) never become a network or a category
        self.assertEqual(st.parse_profiles(st.decode_output(raw(CURRENT_FILE))), [])

    def test_karls_pc_gets_the_public_line_for_ethernet_only(self):
        lines = st.advice(ALL_OK, INFO, 36800, 36800, categories=st.parse_profiles(st.decode_output(raw(PROFILE_FILE))))
        public = [x for x in lines if "a Public network" in x]
        self.assertEqual(len(public), 1, lines)
        self.assertIn("Windows calls Ethernet a Public network", public[0])
        self.assertIn("java.exe must be allowed on Public networks too", public[0])
        self.assertIn("Settings > Network & internet > Ethernet > Private", public[0])
        self.assertNotIn("Tailscale a Public", "\n".join(lines))

    def test_the_lines_for_the_output_and_the_log(self):
        cats = st.parse_profiles(st.decode_output(raw(PROFILE_FILE)))
        self.assertEqual(st.category_lines(cats, ""), ["  network Ethernet: Public", "  network Tailscale: Private"])


class ParserTests(unittest.TestCase):
    def test_all_private_has_no_public_line(self):
        cats = st.parse_profiles(st.decode_output(ALL_PRIVATE))
        self.assertEqual(cats, [("Wi-Fi", "Private"), ("Tailscale", "Private")])
        self.assertFalse([x for x in st.advice(ALL_OK, INFO, 36800, 36800, categories=cats) if "a Public network" in x])

    def test_domain_authenticated_and_odd_spacing(self):
        cats = st.parse_profiles(st.decode_output(DOMAIN))
        self.assertEqual(cats, [("Ethernet 2", "DomainAuthenticated")])
        self.assertFalse([x for x in st.advice(ALL_OK, INFO, 36800, 36800, categories=cats) if "a Public network" in x])

    def test_case_is_ignored_and_an_unknown_value_is_shown_as_it_is(self):
        cats = st.parse_profiles("InterfaceAlias : Wi-Fi\nNetworkCategory : PUBLIC\n\nInterfaceAlias : X\n"
                                 "NetworkCategory : Öffentlich\n\nInterfaceAlias : Y\n")
        self.assertEqual(cats, [("Wi-Fi", "Public"), ("X", "Öffentlich"), ("Y", "")])
        self.assertEqual(st.public_networks(cats), ["Wi-Fi"])        # an unknown value is never guessed to be Public
        self.assertEqual(st.category_lines(cats, "")[1:], ["  network X: Öffentlich", "  network Y: (no category given)"])

    def test_a_public_tailscale_is_left_out_but_every_other_public_network_is_named(self):
        cats = [("Ethernet", "Public"), ("Wi-Fi", "Public"), ("Tailscale", "Public")]
        text = "\n".join(st.advice(ALL_OK, INFO, 36800, 36800, categories=cats))
        self.assertIn("Windows calls Ethernet a Public", text)
        self.assertIn("Windows calls Wi-Fi a Public", text)
        self.assertNotIn("Windows calls Tailscale", text)

    def test_empty_output(self):
        self.assertEqual(st.parse_profiles(""), [])
        self.assertEqual(st.parse_profiles(None), [])
        with mock.patch.object(st.os, "name", "nt"):
            cats, why = st.network_categories(run=mock.Mock(return_value=done(b"")))
        self.assertEqual(cats, [])
        self.assertIn("listed no network", why)

    def test_unknown_gives_no_advice_line(self):
        for cats in (None, []):
            self.assertFalse([x for x in st.advice(ALL_OK, INFO, 36800, 36800, categories=cats) if "a Public network" in x])


class CommandTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(st.os, "name", "nt")
        p.start()
        self.addCleanup(p.stop)

    def test_the_command_no_window_and_a_timeout(self):
        run = mock.Mock(return_value=done(raw(PROFILE_FILE)[3:]))
        cats, why = st.network_categories(run=run)
        self.assertEqual((cats, why), ([("Ethernet", "Public"), ("Tailscale", "Private")], ""))
        args, kw = run.call_args
        self.assertEqual(args[0][0], "powershell")
        self.assertIn("-NonInteractive", args[0])
        self.assertIn("Get-NetConnectionProfile | Format-List InterfaceAlias,NetworkCategory", args[0])
        self.assertEqual(kw["creationflags"], 0x08000000)
        self.assertTrue(kw["capture_output"])
        self.assertNotIn("text", kw)                         # bytes, decoded by decode_output
        self.assertGreater(kw["timeout"], 0)

    def test_powershell_missing(self):
        cats, why = st.network_categories(run=mock.Mock(side_effect=FileNotFoundError(2, "No such file", "powershell")))
        self.assertEqual(cats, [])
        self.assertIn("PowerShell couldn't be run", why)

    def test_powershell_fails(self):
        cats, why = st.network_categories(run=mock.Mock(return_value=done(b"", 1, b"Get-NetConnectionProfile : Access denied\r\n")))
        self.assertEqual(cats, [])
        self.assertIn("exit 1", why)
        self.assertIn("Access denied", why)

    def test_powershell_hangs(self):
        cats, why = st.network_categories(run=mock.Mock(side_effect=subprocess.TimeoutExpired("powershell", 15)))
        self.assertEqual(cats, [])
        self.assertIn("didn't answer", why)

    def test_anything_else_never_raises(self):
        cats, why = st.network_categories(run=mock.Mock(side_effect=ValueError("odd")))
        self.assertEqual(cats, [])
        self.assertIn("odd", why)


class NotWindowsTests(unittest.TestCase):
    def test_off_windows_nothing_is_run(self):
        run = mock.Mock()
        with mock.patch.object(st.os, "name", "posix"):
            self.assertEqual(st.network_categories(run=run), ([], "not Windows"))
        run.assert_not_called()
        self.assertEqual(st.category_lines([], "not Windows"),
                         ["  networks: couldn't tell which Windows calls Public or Private (not Windows)"])


class SelftestRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="netlog1d_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": self.tmp})
        env.start()
        self.addCleanup(env.stop)

    def listener(self):
        lst = st.Listener("127.0.0.1", 0)
        self.addCleanup(lst.close)
        return lst

    def log(self):
        with open(net_log.log_path(self.tmp), encoding="utf-8") as f:
            return f.read()

    def test_the_output_and_the_log_list_each_network(self):
        lines = []
        cats = st.parse_profiles(st.decode_output(raw(PROFILE_FILE)))
        code, _ = st.run(port=0, timeout=1, out=lines.append, info=INFO, firewall=False, categories=(cats, ""),
                         listener=self.listener())
        printed = "\n".join(lines)
        self.assertIn("network Ethernet: Public", printed)
        self.assertIn("network Tailscale: Private", printed)
        self.assertIn("Windows calls Ethernet a Public network", printed)
        text = self.log()
        self.assertIn("network Ethernet: Public", text)
        self.assertIn("network Tailscale: Private", text)
        self.assertLess(text.index("network Ethernet: Public"), text.index("listen "))     # at the top of the attempt
        self.assertIn("advice: Windows calls Ethernet a Public network", text)

    def test_when_it_can_t_tell_one_line_says_why(self):
        lines = []
        st.run(port=0, timeout=1, out=lines.append, info=INFO, firewall=False, categories=([], "not Windows"),
               listener=self.listener())
        printed = "\n".join(lines)
        self.assertEqual(printed.count("couldn't tell which Windows calls Public or Private (not Windows)"), 1)
        self.assertNotIn("a Public network", printed)
        self.assertIn("couldn't tell which Windows calls Public or Private", self.log())

    def test_the_default_run_asks_windows_and_never_raises(self):
        lines = []
        with mock.patch.object(st, "network_categories", return_value=([], "PowerShell didn't answer within 15 s")) as nc, \
                mock.patch.object(st, "firewall_lines", return_value=[]):
            code, _ = st.run(port=0, timeout=1, out=lines.append, info=INFO, listener=self.listener())
        nc.assert_called_once_with()
        self.assertIn("PowerShell didn't answer", "\n".join(lines))

    def test_no_firewall_does_not_ask(self):
        with mock.patch.object(st, "network_categories") as nc:
            st.run(port=0, timeout=1, out=lambda s: None, info=INFO, firewall=False, listener=self.listener())
        nc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
