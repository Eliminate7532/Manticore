# SPDX-License-Identifier: GPL-3.0-or-later
"""
net_log.py - Round NETLOG1: a log of every online host and join attempt, kept across runs.

Why (Karl, 23 Oct 2026): his first two-PC test "couldn't connect at all", and nothing was left to look at - the installed
copy's forge_engine.log is rewritten every time Forge starts, and hosting and joining weren't logged anywhere. This file is
the evidence for the next test.

    connection_log.txt in the logs folder (paths.log_dir()), next to crash_log.txt.
    * Appended, never opened in "w" mode: a restart keeps what the last run wrote.
    * Rotated by size: at MAX_BYTES the file becomes connection_log.1.txt (.1 -> .2 ...); KEEP_OLD old files are kept.
    * One block per attempt: a header line with the attempt's id, then every later line starts with that id (a host's lines
      can come between a join's in the same file). The header has the time, role (host / join / watch / selftest), the
      program's version, the address as typed, what it resolved to, the port, this PC's network addresses (Tailscale's
      100.64.0.0/10 one named), then one line per step (listen, accept, resolve, connect, relay, tls, fingerprint, hello,
      version check, game start, drop, back, end) with its result, how long it took and the exact error text.
    * Each line is written (and the file closed) the moment it happens, so a crash mid-attempt keeps the steps before it.
    * Never a secret: the program never hands a password, rejoin token or keystore password to this module, and redact()
      also removes invite codes (they hold the password), Discord webhook addresses and anything shaped like
      password=/token=/"password": "..." from every line, in case an error text ever quotes one.

Nothing here talks to the network except interfaces(): it asks the system for this PC's addresses (getaddrinfo of the host
name, and a UDP socket "connected" to a public address - which sends no packet - to learn which address the PC would use
for the internet) and runs `tailscale ip -4` when Tailscale is installed. Never raises: logging must not break a game.
"""
import base64
import binascii
import contextlib
import datetime
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time

LOG_NAME = "connection_log.txt"
MAX_BYTES = 256 * 1024          # my estimate: a few hundred attempts per file; F8 reports carry the last lines of the newest
KEEP_OLD = 3                    # connection_log.1.txt .. connection_log.3.txt
LINE_MAX = 600                  # one error text can be long (a Java stack line); cut, never wrapped
SPARE_NAME = "connection_log.rotating.txt"    # round NETLOG1b: the current file's name during a rotation (spare_path)

TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")

_lock = threading.Lock()
_local = threading.local()      # the attempt a forge_net.connect_tls call on this thread belongs to (active())


# ---- where, and the file itself ---------------------------------------------------------------------------------------------
def log_folder(folder=None):
    if folder:
        return folder
    try:
        import paths
        return paths.log_dir()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def log_path(folder=None):
    return os.path.join(log_folder(folder), LOG_NAME)


def old_path(folder, n):
    base, ext = os.path.splitext(LOG_NAME)
    return os.path.join(log_folder(folder), "%s.%d%s" % (base, n, ext))


def spare_path(folder):
    """Round NETLOG1b: the current file's name for the moment of a rotation (kept private like the others: .gitignore's
    connection_log.*.txt, backup.py's and export_public.py's SKIP_FILES)."""
    return os.path.join(log_folder(folder), SPARE_NAME)


def _rotate(folder):
    """connection_log.txt -> .1, .1 -> .2, ... the oldest beyond KEEP_OLD is removed. Only our own files.
    Round NETLOG1b (review R7): the current file is moved out of the way FIRST. On Windows that is the step that fails while
    another program or thread has it open (WinError 32); then nothing else is touched - the file just grows past MAX_BYTES
    until a later write can rotate it - instead of the old files shifting along and the oldest being lost for nothing. A
    shift that fails stops the shifting, so no old file is ever overwritten by its neighbour. A spare left by a rotation
    that couldn't finish is placed first, by the next rotation, and never overwritten."""
    cur, spare = log_path(folder), spare_path(folder)
    if not os.path.exists(spare):
        try:
            os.replace(cur, spare)
        except OSError:
            return
    for n in range(KEEP_OLD, 1, -1):                          # .2 -> .3 (the old .3 goes), then .1 -> .2
        src, dst = old_path(folder, n - 1), old_path(folder, n)
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                break
    for dst in (old_path(folder, 1), cur):                    # .1 if it is free now, else back where it was
        if not os.path.exists(dst):
            try:
                os.replace(spare, dst)
                return
            except OSError:
                pass


def write_lines(lines, folder=None, make_folder=True):
    """Append lines (each redacted and cut to LINE_MAX) to the log. Never raises. True when written.
    make_folder=False: skip the lines when the folder is gone (a background lookup finishing after its folder was removed)."""
    text = "".join(redact(str(x))[:LINE_MAX].replace("\r", " ").replace("\n", " | ") + "\n" for x in lines)
    if not text:
        return True
    try:
        with _lock:
            path = log_path(folder)
            if not make_folder and not os.path.isdir(os.path.dirname(path)):
                return False
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                if os.path.getsize(path) + len(text) > MAX_BYTES:
                    _rotate(folder)
            except OSError:
                pass                                          # no file yet
            with open(path, "a", encoding="utf-8", errors="replace") as f:
                f.write(text)
        return True
    except Exception:
        return False


def tail(folder=None, lines=400):
    """The newest `lines` lines across the current file and the newest old one (for bug reports). [] when there's none."""
    out = []
    for path in (old_path(folder, 1), log_path(folder)):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                out.extend(f.read().splitlines())
        except OSError:
            continue
    return out[-lines:]


# ---- what an F8 report carries: the attempts of the last few days (round NETLOG1c) --------------------------------------------
REPORT_DAYS = 7                 # Karl, 24 Oct 2026 (G3): an F8 report carries only the last 7 days of attempts, not 500 lines
_HEADER_START = "=== "         # "=== 2026-10-24 08:06:36  JOIN attempt 5a02a9  Manticore ..." (Attempt.__init__)
_HEADER_TIME = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s")
_HEADER_ID = re.compile(r"\battempt ([0-9a-f]{6})\b")
_OWNED = re.compile(r"^  ([0-9a-f]{6}) \+")     # "  5a02a9 +  0.010s  ..." - a step, a note, END, the background address block


def report_files(folder=None):
    """Every file of the log, oldest first: .3, .2, .1, the spare of a rotation that couldn't finish (it holds what came just
    before the current file - _rotate), then the current file."""
    return [old_path(folder, n) for n in range(KEEP_OLD, 0, -1)] + [spare_path(folder), log_path(folder)]


class Recent:
    """recent()'s answer. blocks: one list of lines per attempt kept, oldest first (each whole); kept / left_out: attempts;
    since: the oldest time kept. lines() gives them back in the order they were written in the file: a host's lines and a
    join's can be interleaved there, and each attempt's "+s" times count from its own start, so the file's order is the only
    record of which came first."""

    def __init__(self, blocks, left_out, since, days, rows=None):
        self.blocks, self.left_out, self.since, self.days = blocks, left_out, since, days
        self.rows = rows if rows is not None else [(i, x) for i, b in enumerate(blocks) for x in b]   # (block index, line)

    @property
    def kept(self):
        return len(self.blocks)

    def lines(self, budget=None):
        """The kept attempts' lines in file order. With a `budget` (lines), the newest whole attempts that fit in it; a newest
        attempt bigger than the whole budget keeps its header line and its last lines (said in a line between)."""
        chosen, used = set(), 0
        for i in range(len(self.blocks) - 1, -1, -1):
            block = self.blocks[i]
            if budget is not None and used + len(block) > budget:
                if not chosen:                                 # the newest attempt alone is too big: its header, its end
                    at = next((k for k, x in enumerate(block) if x.startswith(_HEADER_START)), 0)
                    keep = max(1, budget - 2)
                    cut = len(block) - (at + 1) - keep
                    if cut > 0:
                        return [block[at], "  ... %d line(s) of this attempt left out to fit the report" % cut] + block[-keep:]
                    return list(block)
                break
            chosen.add(i)
            used += len(block)
        return [line for i, line in self.rows if i in chosen]

    def summary(self):
        """One line for the report: what it carries, or that there's nothing from the last `days` days."""
        if not self.blocks:
            text = "no connection attempts in the last %d days" % self.days
        else:
            text = "the %d connection attempt(s) of the last %d days (the oldest %s, this computer's clock)" % (
                self.kept, self.days, self.since.strftime("%Y-%m-%d %H:%M"))
        if self.left_out:
            text += "; %d older attempt(s) left out" % self.left_out
        return text


def _header_time(line):
    """(time, id) of a header line; either is None when it can't be read (the time and the id are read apart, so an attempt
    whose time is unreadable still keeps its own lines together - and leaves them all out)."""
    m = _HEADER_ID.search(line)
    aid = m.group(1) if m else None
    m = _HEADER_TIME.match(line)
    if not m:
        return None, aid
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), aid
    except ValueError:
        return None, aid


def recent(folder=None, days=REPORT_DAYS, now=None):
    """Round NETLOG1c (Karl, 24 Oct 2026, G3): the attempts whose header is at most `days` days old, read from every file of
    the log (report_files), whole. Each attempt's lines are found by the id they start with (a host's lines can come between
    a join's); a line without an id (the header's facts, the address lines) goes with the line before it, as write_lines
    writes them together. Left out, and counted in left_out: an attempt older than that; an attempt whose header's time
    can't be read (it can't be shown to be recent - a report sends too little rather than too much); lines whose attempt's
    header is in no file any more (rotated away - older than everything kept). A time in the future (the clock was moved)
    counts as recent. Never raises; the lines are as written (write_lines has already redacted every one)."""
    now = now or datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=days)
    rows = []
    with _lock:                                                # a rotation in this program can't move a file mid-read
        for path in report_files(folder):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    rows.extend(f.read().splitlines())
            except OSError:
                continue
    blocks, order, by_id = {}, [], {}        # block key -> {"time", "lines"}; keys in the order their headers came
    owner, pending, n, owned = None, [], 0, []                # owned: (block key, line) in file order
    for line in rows:
        if not line.strip():
            pending.append(line)                               # a blank line belongs to the line after it (the header writes "")
            continue
        if line.startswith(_HEADER_START):
            when, aid = _header_time(line)
            n += 1
            key = n
            blocks[key] = {"time": when, "lines": []}
            order.append(key)
            if aid:
                by_id[aid] = key
            owner = key
        else:
            m = _OWNED.match(line)
            if m:
                owner = by_id.get(m.group(1))                  # None: its header is gone (rotated away)
        if owner is not None:
            for x in pending + [line]:
                blocks[owner]["lines"].append(x)
                owned.append((owner, x))
        pending = []
    kept = [k for k in order if blocks[k]["time"] is not None and blocks[k]["time"] >= cutoff]
    index = {k: i for i, k in enumerate(kept)}
    since = min((blocks[k]["time"] for k in kept), default=None)
    return Recent([blocks[k]["lines"] for k in kept], len(order) - len(kept), since, days,
                  rows=[(index[k], x) for k, x in owned if k in index])


# ---- never a secret ---------------------------------------------------------------------------------------------------------
_INVITE = re.compile(r"MANT1-[A-Za-z0-9_\-]+(?:\s+[A-Za-z0-9_\-]+)*")
_INVITE_LEFT_OUT = "MANT1-<invite code left out>"


def _whole_invite(code):
    """True when `code` (no spaces) reads as a whole invite code: base64url of one JSON object (forge_net.read_invite)."""
    body = code[len("MANT1-"):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    return isinstance(data, dict)


def _invite_sub(m):
    """Round NETLOG1b (review R8): forge_net.read_invite accepts a code a chat program broke with spaces or newlines, so the
    pieces after a break are left out too - up to where the joined pieces read as a whole code (the password is its last
    field). A code that never reads whole takes every following piece with it: too much left out, never too little."""
    parts = re.split(r"(\s+)", m.group(0))
    joined = ""
    for i in range(0, len(parts), 2):
        joined += parts[i]
        if _whole_invite(joined):
            return _INVITE_LEFT_OUT + "".join(parts[i + 1:])
    return _INVITE_LEFT_OUT


_REDACTIONS = (
    (_INVITE, _invite_sub),
    (re.compile(r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\S+", re.I), "<webhook address left out>"),
    (re.compile(r'(?i)("(?:password|pass|token|secret|store_?pass|keystore_?pass)"\s*:\s*)"[^"]*"'), r'\1"<left out>"'),
    (re.compile(r"(?i)\b((?:MANTICORE_NET_PASSWORD|MANTICORE_KEYSTORE_PASS|password|passwd|token|secret)\s*[=:]\s*)\S+"),
     r"\1<left out>"),
)


def redact(text):
    text = text or ""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


# ---- this PC's addresses ----------------------------------------------------------------------------------------------------
def address_kind(ip):
    """loopback / tailscale / lan / link-local / public / ipv6-... for one address (general knowledge: RFC 1918, RFC 6598)."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return "unknown"
    if a.version == 6:
        if a.is_loopback:
            return "ipv6-loopback"
        if a.is_link_local:
            return "ipv6-link-local"
        if a in ipaddress.ip_network("fd7a:115c:a1e0::/48"):     # Tailscale's IPv6 range (general knowledge)
            return "tailscale-ipv6"
        return "ipv6"
    if a.is_loopback:
        return "loopback"
    if a in TAILSCALE_NET:
        # 100.64.0.0/10 is also carrier-grade NAT's range; on a PC's own interface it is nearly always Tailscale - and when the
        # tailscale program doesn't confirm it, interface_lines() and the self-test's advice say "maybe" (round NETLOG1b)
        return "tailscale"
    if a.is_link_local:
        return "link-local"
    if a.is_private:
        return "lan"
    return "public"


def _default_route_address():
    """The address this PC would use to reach the internet. A UDP connect sends nothing; it only picks a route."""
    for target in ("192.0.2.1", "8.8.8.8"):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 9))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("0."):
                return ip
        except OSError:
            continue
        finally:
            if s is not None:
                s.close()
    return None


def _linux_addresses():
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)/", out)


def _tailscale_cli():
    try:
        import forge_net
        return forge_net.tailscale_address()
    except Exception:
        return None


def interfaces(tailscale_cli=None):
    """{"hostname", "addresses": [(ip, kind)], "default_route", "tailscale", "tailscale_cli"} - best effort, never raises.
    tailscale_cli: a function standing in for `tailscale ip -4` (tests)."""
    found = []

    def add(ip):
        if ip and ip not in found:
            found.append(ip)

    add("127.0.0.1")
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    for name in {host, _safe_fqdn()} - {""}:
        try:
            for fam, _t, _p, _c, sa in socket.getaddrinfo(name, None):
                if fam in (socket.AF_INET, socket.AF_INET6):
                    add(sa[0])
        except (OSError, UnicodeError):
            pass
    if sys.platform.startswith("linux"):
        for ip in _linux_addresses():
            add(ip)
    route = _default_route_address()
    add(route)
    ts_cli = (tailscale_cli or _tailscale_cli)()
    add(ts_cli)
    addrs = [(ip, address_kind(ip)) for ip in found]
    ts = ts_cli or next((ip for ip, k in addrs if k == "tailscale"), None)
    return {"hostname": host, "addresses": addrs, "default_route": route, "tailscale": ts, "tailscale_cli": ts_cli}


def _safe_fqdn():
    try:
        return socket.getfqdn()
    except OSError:
        return ""


def interface_lines(info):
    lines = ["  this PC: %s" % (info.get("hostname") or "(no name)")]
    for ip, kind in info.get("addresses") or []:
        lines.append("    address %-40s %s" % (ip, kind))
    lines.append("    internet route via: %s" % (info.get("default_route") or "none (no network, or no default route)"))
    ts = info.get("tailscale")
    if ts and info.get("tailscale_cli"):
        lines.append("    Tailscale: yes, %s" % ts)
    elif ts:
        # Round NETLOG1b (review R6): 100.64.0.0/10 is also carrier-grade NAT's range (a mobile-broadband adapter can have one)
        lines.append("    Tailscale: maybe - %s is on an interface, but the tailscale program didn't answer (100.64.0.0/10 is "
                     "also what a mobile or internet provider's carrier-grade NAT hands out)" % ts)
    else:
        lines.append("    Tailscale: no 100.64.0.0/10 address on this PC (not installed, not signed in, or off)")
    return lines


def resolve(address, port):
    """[(ip, family name)] that `address` resolves to, or raises the exact OSError socket.create_connection would see."""
    out = []
    for fam, _t, _p, _c, sa in socket.getaddrinfo(address, int(port), proto=socket.IPPROTO_TCP):
        item = (sa[0], "IPv6" if fam == socket.AF_INET6 else "IPv4")
        if item not in out:
            out.append(item)
    return out


def error_text(e):
    """The exact error: class, errno (if any) and message - what Karl needs to search for or send on."""
    if e is None:
        return ""
    no = getattr(e, "errno", None)
    win = getattr(e, "winerror", None)
    bits = [type(e).__name__]
    if win:
        bits.append("[WinError %s]" % win)
    elif no:
        bits.append("[errno %s]" % no)
    msg = str(e)
    return " ".join(bits) + (": " + msg if msg else "")


# ---- one attempt ------------------------------------------------------------------------------------------------------------
def _version_line():
    try:
        import version
        return "%s %s (code %s)" % (getattr(version, "APP_NAME", "Manticore"), version.VERSION, version.code_fingerprint())
    except Exception as e:
        return "version unknown (%s)" % type(e).__name__


class Attempt:
    """One host, join or self-test attempt. Every method writes at once and never raises.

        a = Attempt("join", address="203.0.113.7", port=36800)     # header + this PC's addresses
        a.step("connect", True, ms=12)                              # or:  with a.timed("connect"): ...
        a.end("connected")
    """

    def __init__(self, role, folder=None, with_interfaces=True, now=None, tailscale_cli=None, **facts):
        """with_interfaces: True = this PC's addresses in the header (the lookups wait here); False = none;
        "background" = read on a thread of their own and written as a block that names this attempt the moment they are
        known (round NETLOG1b, review R2: the host's attempt is made on the window's thread, and the lookups - a reverse DNS
        lookup, `tailscale ip -4` with its 3 s limit - must not hold the window up)."""
        self.role, self.folder = role, folder
        self.id = secrets.token_hex(3)
        self.t0 = time.monotonic()
        self.ended = False
        when = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        lines = ["", "=== %s  %s attempt %s  %s  (python %s, %s)" % (when, role.upper(), self.id, _version_line(),
                                                                       sys.version.split()[0], sys.platform)]
        for key, value in facts.items():
            if value is not None and value != "":
                lines.append("  %s: %s" % (key.replace("_", " "), value))
        if with_interfaces == "background":
            self.info = {}
            write_lines(lines, folder)
            where = log_folder(folder)          # now: a test's (or a soak's) folder may be gone back to the real one later
            threading.Thread(target=self._addresses_later, args=(tailscale_cli, where), daemon=True,
                             name="net-log-addresses").start()
            return
        if with_interfaces:
            try:
                self.info = interfaces(tailscale_cli=tailscale_cli)
            except Exception as e:
                self.info = {}
                lines.append("  this PC's addresses: could not be read (%s)" % error_text(e))
            else:
                lines.extend(interface_lines(self.info))
        else:
            self.info = {}
        write_lines(lines, folder)

    def _addresses_later(self, tailscale_cli, where):
        t = time.monotonic()
        try:
            info = interfaces(tailscale_cli=tailscale_cli)
        except Exception as e:
            write_lines(["  %s +%7.3fs  this PC's addresses: could not be read (%s)" % (self.id, self._elapsed(), error_text(e))],
                        where, make_folder=False)
            return
        self.info = info
        lines = interface_lines(info)
        lines[0] = "  %s +%7.3fs  %s  (read in %.0f ms on a thread of their own)" % (
            self.id, self._elapsed(), lines[0].strip(), (time.monotonic() - t) * 1000)
        write_lines(lines, where, make_folder=False)

    def _elapsed(self):
        return time.monotonic() - self.t0

    def note(self, text):
        write_lines(["  %s +%7.3fs  %s" % (self.id, self._elapsed(), text)], self.folder)

    def step(self, name, ok, ms=None, detail="", error=None):
        """ok: True -> OK, False -> FAILED, None -> a plain note (INFO)."""
        word = "OK" if ok is True else ("FAILED" if ok is False else "INFO")
        took = ("%6.0f ms" % ms) if ms is not None else " " * 9
        text = "  %s +%7.3fs  %-15s %-6s %s" % (self.id, self._elapsed(), name, word, took)
        if detail:
            text += "  " + str(detail)
        if error is not None:
            text += "  error: " + (error if isinstance(error, str) else error_text(error))
        write_lines([text.rstrip()], self.folder)

    @contextlib.contextmanager
    def timed(self, name, detail=""):
        """Times the block; logs OK, or FAILED with the exception's exact text (and lets the exception go on)."""
        t = time.monotonic()
        box = {"detail": detail}
        try:
            yield box
        except BaseException as e:
            self.step(name, False, (time.monotonic() - t) * 1000, box.get("detail") or "", e)
            raise
        self.step(name, True, (time.monotonic() - t) * 1000, box.get("detail") or "")

    def end(self, result, detail=""):
        if self.ended:
            return
        self.ended = True
        write_lines(["  %s +%7.3fs  END %s%s" % (self.id, self._elapsed(), result, ("  " + detail) if detail else "")],
                    self.folder)


@contextlib.contextmanager
def active(attempt):
    """forge_net.connect_tls on this thread logs its steps into `attempt` (no change to its arguments, so the tests' stand-in
    connect functions keep working)."""
    old = getattr(_local, "attempt", None)
    _local.attempt = attempt
    try:
        yield attempt
    finally:
        _local.attempt = old


def current():
    return getattr(_local, "attempt", None)
