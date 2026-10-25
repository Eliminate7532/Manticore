# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_netlog1c.py - Round NETLOG1c: Karl's two decisions of 24 Oct 2026 (claude/DECISIONS_2026-10-24.md) on NETLOG1.

    G3: an F8 bug report carries only the last 7 days of the connection log (connection_log.txt and its rotated files), cut by
        each attempt's own header time, whole attempts, and its CONTENTS list says so; nothing from older than that.
    G5: `Manticore-cli.exe --net-selftest` (and `python tools/net_selftest.py`) passes --port and --timeout on to the
        self-test; the default stays port 36800.

Headless; no Forge, no second PC, nothing written outside a temporary folder.
"""
import datetime
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

import forge_net as fn
import net_log
import reporting
from tools import net_selftest as st

NOW = datetime.datetime(2026, 10, 24, 12, 0, 0)
DAY = datetime.timedelta(days=1)
HOUR = datetime.timedelta(hours=1)
MARKER = "qzxv-wkjp-ymrt-58"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TempFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="netlog1c_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {"MANTICORE_DATA_DIR": self.tmp})
        env.start()
        self.addCleanup(env.stop)

    def attempt(self, age, role="join", detail=None, **facts):
        """One whole attempt `age` before NOW: header, a step, END. Returns its id."""
        a = net_log.Attempt(role, folder=self.tmp, with_interfaces=False, now=NOW - age, port=36800, **facts)
        a.step("connect", False, 3, detail or ("%s-%s" % (role, a.id)), "refused")
        a.end("FAILED")
        return a.id

    def report(self, now=NOW):
        path = reporting.build_report({"name": "t", "happened": "x"}, folder=self.tmp, now=now)
        with zipfile.ZipFile(path) as z:
            return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}

    def raw(self, *lines, path=None):
        with open(path or net_log.log_path(self.tmp), "a", encoding="utf-8") as f:
            f.write("".join(x + "\n" for x in lines))


# ---------------------------------------------------------------------------------------------------------------------
# G3: what an F8 report carries
# ---------------------------------------------------------------------------------------------------------------------
class SevenDayTests(TempFolder):
    def test_attempts_older_than_7_days_are_left_out(self):
        old = self.attempt(8 * DAY)
        new = self.attempt(HOUR)
        files = self.report()
        log = files["connection_log.txt"]
        self.assertIn("attempt %s" % new, log)
        self.assertIn("join-%s" % new, log)
        self.assertNotIn(old, log)                              # neither its header nor any of its lines
        self.assertIn("1 older attempt(s) left out", log.splitlines()[0])

    def test_an_attempt_from_6_days_23_hours_ago_is_kept_and_one_just_past_7_days_is_not(self):
        kept = self.attempt(6 * DAY + 23 * HOUR)
        gone = self.attempt(7 * DAY + datetime.timedelta(minutes=1))
        edge = self.attempt(7 * DAY)                             # exactly 7 days: kept (my reading of "the last 7 days")
        log = self.report()["connection_log.txt"]
        self.assertIn(kept, log)
        self.assertIn(edge, log)
        self.assertNotIn(gone, log)

    def test_whole_attempts_are_kept_with_their_facts_and_address_lines(self):
        a = net_log.Attempt("join", folder=self.tmp, with_interfaces=False, now=NOW - DAY, address="'203.0.113.7'", port=36800)
        net_log.write_lines(["  %s +  0.012s  this PC: vm  (read in 7 ms on a thread of their own)" % a.id,
                             "    address 192.0.2.10                               lan"], self.tmp)
        a.step("connect", True, 4, "203.0.113.7:36800")
        a.end("connected")
        with open(net_log.log_path(self.tmp), encoding="utf-8") as f:
            written = f.read().splitlines()
        rec = net_log.recent(self.tmp, now=NOW)
        self.assertEqual(rec.lines(), written)                   # every line, in order, nothing added
        log = self.report()["connection_log.txt"].splitlines()
        self.assertEqual(log[1:], written)

    def test_a_hosts_lines_after_a_newer_join_go_with_the_host(self):
        """A host's lines can come between a join's (the file is shared): the cut follows each line's attempt id, not its
        place in the file."""
        host = net_log.Attempt("host", folder=self.tmp, with_interfaces=False, now=NOW - 9 * DAY, port=36800)
        join = net_log.Attempt("join", folder=self.tmp, with_interfaces=False, now=NOW - HOUR, port=36800)
        host.step("accept", True, None, "old-host-line from 198.51.100.9")
        join.step("connect", True, 2, "new-join-line")
        host.note("old-host-note")
        join.end("connected")
        log = self.report()["connection_log.txt"]
        self.assertIn("new-join-line", log)
        self.assertIn("END connected", log)
        for gone in ("old-host-line", "old-host-note", "198.51.100.9", host.id):
            self.assertNotIn(gone, log)

    def test_interleaved_attempts_keep_the_order_they_were_written(self):
        """Each attempt's "+s" counts from its own start, so only the file's order says which line came first."""
        host = net_log.Attempt("host", folder=self.tmp, with_interfaces=False, now=NOW - 2 * HOUR, port=36800)
        host.step("listen", True, None, "first-host-line")
        join = net_log.Attempt("join", folder=self.tmp, with_interfaces=False, now=NOW - HOUR, port=36800)
        host.step("accept", True, None, "second-host-line")
        join.step("connect", True, 1, "join-line")
        host.end("cancelled by the host")
        with open(net_log.log_path(self.tmp), encoding="utf-8") as f:
            written = f.read().splitlines()
        self.assertEqual(net_log.recent(self.tmp, now=NOW).lines(), written)
        log = self.report()["connection_log.txt"]
        at = [log.index(x) for x in ("first-host-line", "JOIN attempt", "second-host-line", "join-line", "END cancelled")]
        self.assertEqual(at, sorted(at))

    def test_the_rotated_files_are_read_too(self):
        ids = {}
        for name, age, place in (("A", 10 * DAY, net_log.old_path(self.tmp, 3)), ("B", 6 * DAY, net_log.old_path(self.tmp, 2)),
                                 ("C", 4 * DAY, net_log.old_path(self.tmp, 1)), ("D", 2 * DAY, net_log.spare_path(self.tmp)),
                                 ("E", HOUR, None)):
            ids[name] = self.attempt(age, detail="block-" + name)
            if place:
                os.replace(net_log.log_path(self.tmp), place)
        log = self.report()["connection_log.txt"]
        self.assertNotIn("block-A", log)
        order = [log.index("block-" + n) for n in "BCDE"]
        self.assertEqual(order, sorted(order))                   # oldest first: .2, .1, the spare, the current file
        self.assertIn("1 older attempt(s) left out", log)
        self.assertNotIn("block-B", "\n".join(net_log.tail(self.tmp, 5000)))     # NETLOG1's tail() read only .1 and the current

    def test_contents_says_7_days(self):
        self.assertIn("your online connection attempts of the last 7 days, if any", reporting.CONTENTS)
        self.assertEqual(sum("connection" in x for x in reporting.CONTENTS), 1)
        self.attempt(HOUR)
        files = self.report()
        self.assertRegex(files["report.txt"], r"Online: +the 1 connection attempt\(s\) of the last 7 days .*connection_log\.txt")
        self.assertTrue(files["connection_log.txt"].startswith("# A bug report's copy of the connection log: the 1 connection "
                                                               "attempt(s) of the last 7 days"))

    def test_nothing_added_when_every_attempt_is_older(self):
        self.attempt(8 * DAY)
        self.attempt(30 * DAY)
        files = self.report()
        self.assertNotIn("connection_log.txt", files)
        self.assertIn("Online:   no connection attempts in the last 7 days; 2 older attempt(s) left out", files["report.txt"])

    def test_no_log_at_all_says_so_too(self):
        files = self.report()
        self.assertNotIn("connection_log.txt", files)
        self.assertIn("Online:   no connection attempts in the last 7 days", files["report.txt"])

    def test_an_unreadable_header_time_leaves_that_attempt_out_without_crashing(self):
        """My choice: left out - a report sends too little rather than too much when it can't show an attempt is recent. Its
        own lines go with it; the attempts around it are judged as usual."""
        self.attempt(HOUR, detail="before-it")
        self.raw("", "=== 2026-13-45 99:99:99  JOIN attempt abcdef  Manticore 0.28.53.12 (code x)  (python 3.13, linux)",
                 "  address as typed: 'bad-time-fact'",
                 "  abcdef +  0.010s  connect         FAILED      3 ms  bad-time-step")
        self.raw("", "=== yesterday  HOST attempt", "  port: 36800 no-id-fact")         # neither time nor id
        self.raw("\x00\x00 garbage \udcff line".encode("utf-8", "surrogateescape").decode("utf-8", "replace"))
        self.attempt(HOUR, detail="after-it")
        rec = net_log.recent(self.tmp, now=NOW)
        self.assertEqual(rec.kept, 2)
        self.assertEqual(rec.left_out, 2)
        log = self.report()["connection_log.txt"]
        self.assertIn("before-it", log)
        self.assertIn("after-it", log)
        for gone in ("abcdef", "bad-time-fact", "bad-time-step", "no-id-fact", "garbage"):
            self.assertNotIn(gone, log)

    def test_lines_whose_header_was_rotated_away_are_left_out(self):
        self.raw("  0f0f0f +123.000s  engine: nethost: orphan line from 203.0.113.99")   # an attempt whose header is gone
        new = self.attempt(HOUR)
        log = self.report()["connection_log.txt"]
        self.assertIn(new, log)
        self.assertNotIn("orphan", log)

    def test_a_time_in_the_future_counts_as_recent(self):
        future = self.attempt(-2 * DAY)                          # the PC's clock was moved back since
        self.assertIn(future, self.report()["connection_log.txt"])

    def test_redaction_and_scrubbing_are_unchanged(self):
        invite = fn.make_invite("203.0.113.7", 36800, "ab" * 32, MARKER)
        self.attempt(HOUR, detail="pasted %s from C:\\Users\\SomeName\\Desktop" % invite)
        files = self.report()
        log = files["connection_log.txt"]
        self.assertNotIn(MARKER, "".join(files.values()))
        self.assertNotIn(invite, log)
        self.assertIn("MANT1-<invite code left out>", log)
        self.assertNotIn("SomeName", log)

    def test_every_kept_line_is_a_line_of_the_file(self):
        for age in (9 * DAY, 3 * DAY, HOUR):
            self.attempt(age)
        with open(net_log.log_path(self.tmp), encoding="utf-8") as f:
            written = set(f.read().splitlines())
        self.assertTrue(set(net_log.recent(self.tmp, now=NOW).lines()) <= written)

    def test_build_report_uses_its_own_now(self):
        aid = self.attempt(HOUR)
        self.assertIn(aid, self.report(now=NOW)["connection_log.txt"])
        self.assertNotIn("connection_log.txt", self.report(now=NOW + 8 * DAY))


class ShrinkTests(TempFolder):
    """When the zip must shrink (reporting's later attempts), the newest WHOLE attempts that fit are kept."""

    def test_the_budget_keeps_the_newest_whole_attempts(self):
        ids = [self.attempt(age) for age in (5 * DAY, 3 * DAY, 2 * DAY, HOUR)]
        rec = net_log.recent(self.tmp, now=NOW)
        sizes = [len(b) for b in rec.blocks]
        lines = rec.lines(budget=sizes[-1] + sizes[-2])
        joined = "\n".join(lines)
        self.assertEqual(len(lines), sizes[-1] + sizes[-2])
        self.assertIn(ids[3], joined)
        self.assertIn(ids[2], joined)
        self.assertNotIn(ids[1], joined)
        self.assertEqual(rec.lines(), rec.lines(budget=None))

    def test_a_budget_that_falls_inside_an_attempt_leaves_that_attempt_out_whole(self):
        """Attempts of uneven sizes, so a cut by lines can't land on an attempt's edge by luck (found by planted error P6:
        with the 5-line attempts above, NETLOG1's line cut passed every test)."""
        sizes = (2, 9, 4)
        ids = []
        for n, age in zip(sizes, (3 * HOUR, 2 * HOUR, HOUR)):
            a = net_log.Attempt("join", folder=self.tmp, with_interfaces=False, now=NOW - age, port=36800)
            for k in range(n):
                a.note("%s note %d" % (a.id, k))
            ids.append(a.id)
        rec = net_log.recent(self.tmp, now=NOW)
        newest = len(rec.blocks[-1])                             # "", header, port, 4 notes
        for budget in range(newest, newest + len(rec.blocks[-2])):          # room for the newest, not for the one before
            lines = rec.lines(budget=budget)
            self.assertEqual(lines, rec.blocks[-1], budget)
        both = rec.lines(budget=newest + len(rec.blocks[-2]))
        self.assertEqual(both, rec.blocks[-2] + rec.blocks[-1])
        self.assertTrue(all(ids[0] not in x for x in both))

    def test_a_newest_attempt_bigger_than_the_budget_keeps_its_header_and_its_last_lines(self):
        a = net_log.Attempt("host", folder=self.tmp, with_interfaces=False, now=NOW - HOUR, port=36800)
        for n in range(50):
            a.note("line %02d" % n)
        a.end("done")
        lines = net_log.recent(self.tmp, now=NOW).lines(budget=10)
        self.assertEqual(len(lines), 10)
        self.assertTrue(lines[0].startswith("=== ") and ("attempt %s" % a.id) in lines[0], lines[0])
        # the block: "", the header, "port: 36800", 50 notes, END; kept: the header and the last 8 (notes 43-49, END)
        self.assertEqual(lines[1], "  ... 44 line(s) of this attempt left out to fit the report")    # the port line, notes 0-42
        self.assertTrue(lines[-1].endswith("END done"))
        self.assertTrue(lines[-2].endswith("line 49"))

    def test_a_report_that_must_shrink_still_cuts_at_attempts(self):
        ids = [self.attempt(n * HOUR) for n in range(12, 0, -1)]     # 12 attempts of 5 lines: 60 lines, more than 40
        rec = net_log.recent(self.tmp, now=NOW)
        path = reporting.build_report({"name": "t", "happened": "x"}, folder=self.tmp, now=NOW,
                                      max_bytes=1)       # every size is too big: the smallest is written
        with zipfile.ZipFile(path) as z:
            log = z.read("connection_log.txt").decode("utf-8").splitlines()
        self.assertEqual(len(log) - 1, 40)                       # the smallest size: 40 lines = the newest 8 attempts, whole
        self.assertEqual(log[1:], rec.lines(budget=40))
        self.assertEqual([i for i in ids if ("attempt " + i) in "\n".join(log)], ids[4:])


class SpeedTests(TempFolder):
    def test_a_full_log_is_read_quickly(self):
        """The biggest a log gets: KEEP_OLD old files and the current one, each about MAX_BYTES. Measured, with a loose limit."""
        ages = iter(range(10 ** 6))
        quick = mock.patch.object(net_log, "_version_line", return_value="Manticore 0 (code 0)")   # 4 ms each, not the subject
        quick.start()
        self.addCleanup(quick.stop)
        for place in [net_log.old_path(self.tmp, n) for n in (3, 2, 1)] + [None]:
            while os.path.exists(net_log.log_path(self.tmp)) is False or \
                    os.path.getsize(net_log.log_path(self.tmp)) < net_log.MAX_BYTES - 2000:
                a = net_log.Attempt("join", folder=self.tmp, with_interfaces=False,
                                    now=NOW - datetime.timedelta(minutes=next(ages)), port=36800)
                for n in range(8):
                    a.step("connect", False, 3, "x" * 40, "refused")
                a.end("FAILED")
            if place:
                os.replace(net_log.log_path(self.tmp), place)
        t = time.perf_counter()
        rec = net_log.recent(self.tmp, now=NOW)
        took = time.perf_counter() - t
        self.assertGreater(rec.kept, 500)
        print("\n  [NETLOG1c] recent() over %d bytes in %d files: %d attempts in %.3f s" % (
            sum(os.path.getsize(p) for p in net_log.report_files(self.tmp) if os.path.exists(p)),
            sum(os.path.exists(p) for p in net_log.report_files(self.tmp)), rec.kept, took))
        self.assertLess(took, 2.0, "recent() took %.3f s" % took)


# ---------------------------------------------------------------------------------------------------------------------
# G5: --port and --timeout reach the self-test
# ---------------------------------------------------------------------------------------------------------------------
class SelftestArgumentTests(unittest.TestCase):
    def run_main(self, argv):
        import forge_table
        with mock.patch.object(st, "run", return_value=(0, [])) as run, mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as stop:
                forge_table.main(argv)
        return run, stop.exception.code

    def test_the_program_passes_port_and_timeout_on(self):
        run, code = self.run_main(["x", "--net-selftest", "--port", "40000", "--timeout", "1.5"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["port"], 40000)
        self.assertEqual(run.call_args.kwargs["timeout"], 1.5)

    def test_the_default_stays_36800(self):
        self.assertEqual(fn.DEFAULT_PORT, 36800)
        run, _code = self.run_main(["x", "--net-selftest"])
        self.assertEqual(run.call_args.kwargs["port"], 36800)
        self.assertEqual(run.call_args.kwargs["timeout"], st.TIMEOUT)

    def test_the_exit_code_comes_back(self):
        import forge_table
        with mock.patch.object(st, "run", return_value=(1, [])), mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as stop:
                forge_table.main(["x", "--net-selftest", "--port", "40001"])
        self.assertEqual(stop.exception.code, 1)

    def test_the_tool_itself_passes_them_on(self):
        with mock.patch.object(st, "run", return_value=(0, [])) as run:
            self.assertEqual(st.main(["--port", "40002", "--timeout", "0.5"]), 0)
        self.assertEqual((run.call_args.kwargs["port"], run.call_args.kwargs["timeout"]), (40002, 0.5))
        with mock.patch.object(st, "run", return_value=(0, [])) as run:
            st.main([])
        self.assertEqual(run.call_args.kwargs["port"], 36800)

    def test_a_port_out_of_range_is_a_plain_message_not_a_crash(self):
        for bad in ("70000", "80", "x", "-5"):
            for argv in (["x", "--net-selftest", "--port", bad], None):
                with mock.patch.object(st, "run") as run, mock.patch("sys.stderr") as err:
                    with self.assertRaises(SystemExit) as stop:
                        if argv:
                            import forge_table
                            with mock.patch("builtins.print"):
                                forge_table.main(argv)
                        else:
                            st.main(["--port", bad])
                self.assertEqual(stop.exception.code, 2, bad)
                run.assert_not_called()
                said = "".join(str(c.args[0]) for c in err.write.call_args_list)
                self.assertTrue("port" in said.lower(), said)
                if bad == "70000":
                    self.assertIn("Use a port between 1024 and 65535", said)

    def test_a_bad_timeout_is_refused(self):
        for bad in ("0", "-1", "abc", "nan", "500"):
            with mock.patch.object(st, "run") as run, mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit) as stop:
                    st.main(["--timeout", bad])
            self.assertEqual(stop.exception.code, 2, bad)
            run.assert_not_called()

    def test_port_without_the_selftest_is_an_error(self):
        import forge_table
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as stop:
                forge_table.parse_args(["x", "--port", "40000"])
        self.assertEqual(stop.exception.code, 2)
        self.assertIsNone(forge_table.parse_args(["x"]).port)

    def test_end_to_end_through_the_program(self):
        """`python forge_table.py --net-selftest --port N --timeout T` in a child process, a free port, its own data folder:
        the self-test's log block names that port and that timeout."""
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        tmp = tempfile.mkdtemp(prefix="netlog1c_e2e_")
        self.addCleanup(shutil.rmtree, tmp, True)
        env = dict(os.environ, MANTICORE_DATA_DIR=tmp, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                   MANTICORE_SKIP_LIVE="1", MANTICORE_PORTABLE="1")
        done = subprocess.run([sys.executable, os.path.join(BASE_DIR, "forge_table.py"), "--net-selftest", "--port", str(port),
                               "--timeout", "0.5"], cwd=BASE_DIR, env=env, capture_output=True, text=True, timeout=120)
        self.assertIn(done.returncode, (0, 1), done.stdout + done.stderr)
        with open(os.path.join(tmp, net_log.LOG_NAME), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("SELFTEST attempt", log)
        self.assertIn("  port: %d" % port, log)
        self.assertIn("  timeout: 0.5 s", log)
        self.assertRegex(log, r"listen +OK +0\.0\.0\.0 port %d\n" % port)


if __name__ == "__main__":
    unittest.main()
