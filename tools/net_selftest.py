# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/net_selftest.py - Round NETLOG1: can this PC host? A self-test that needs no second PC.

    python tools\\net_selftest.py                 (Karl's own copy)
    Manticore-cli.exe --net-selftest            (an installed copy)
    python tools\\net_selftest.py --port 36800 --timeout 3

It listens on the game's port (36800, forge_net.DEFAULT_PORT) on every address, like a hosted game does, and then joins
itself through each way in: 127.0.0.1, each LAN address, the Tailscale address (if there is one), and this PC's own
public address (if it has one on an interface). For each it prints one line - OK / refused / timed out / no answer / no route
- and what that means in plain words, and writes the same into the connection log (connection_log.txt in the logs folder),
where F8 bug reports pick it up. On Windows it also lists the firewall profiles and any inbound firewall rules that name
java.exe, python.exe or Manticore, and (round NETLOG1d) which networks Windows calls Public or Private.

What it proves and what it doesn't (general knowledge, my estimate for Windows):
  * OK through an address = something on this PC can listen on the game's port and be reached at that address from THIS PC.
  * Windows Firewall does not filter a PC's connections to itself, so an OK through the LAN or Tailscale address does NOT prove
    another PC can get in. Only the two-PC test (or the firewall rules listed) says that.
  * The listener here is this program (python.exe or Manticore-cli.exe), not java.exe, which is what listens in a real
    hosted game: a firewall rule is per program.
  * Nothing goes to the internet: no "what is my IP" site is asked, and the router's port forwarding is not tested.

Exit code: 0 when every address answered, 1 when one didn't (or the game's port can't be used for a reason other than a program
holding it - round NETLOG1b), 2 when the test couldn't listen at all.
"""
import argparse
import errno
import os
import secrets
import socket
import subprocess
import sys
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import forge_net        # noqa: E402
import net_log          # noqa: E402

HELLO = b"MANTICORE-SELFTEST "
ANSWER = b"MANTICORE-SELFTEST-OK "
TIMEOUT = 3.0

# verdict words (the first column of each line)
OK, REFUSED, TIMED_OUT, NO_ANSWER, NO_ROUTE, ERROR = "OK", "refused", "timed out", "no answer", "no route", "error"

UNREACHABLE_ERRNOS = (101, 113, 10051, 10065)      # network / host unreachable (Linux and Windows numbers)
IN_USE_ERRNOS = (errno.EADDRINUSE, 98, 48, 10048)  # round NETLOG1b: "address already in use" (this system's, Linux's, macOS's,
                                                   # WSAEADDRINUSE - a Linux-shaped error in a test must read the same on Windows)
DENIED_ERRNOS = (errno.EACCES, 10013)              # ... "forbidden by its access permissions" (WSAEACCES)


def listen_problem(e):
    """Round NETLOG1b (review R5): why the game's port couldn't be used - "busy" (a program holds it), "denied" (nothing holds
    it, but Windows has reserved it - the excluded port ranges Hyper-V, WSL or Docker set up - or security software refuses;
    WinError 10013), or "other". Only "busy" goes away by closing a hosted game."""
    no, win = getattr(e, "errno", None), getattr(e, "winerror", None)
    if no in IN_USE_ERRNOS or win in IN_USE_ERRNOS:
        return "busy"
    if isinstance(e, PermissionError) or no in DENIED_ERRNOS or win in DENIED_ERRNOS:
        return "denied"
    return "other"


class Listener:
    """Accepts connections on (bind, port) and answers each probe line with its nonce. port=0 picks a free port."""

    def __init__(self, bind="0.0.0.0", port=forge_net.DEFAULT_PORT, silent=False):
        self.silent = silent                        # tests: a listener that accepts and never answers
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, int(port)))           # raises OSError (in use, not allowed): the caller says so
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.seen = []                              # the address each connection came from
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="net-selftest")
        self.thread.start()

    def _run(self):
        while not self._stop:
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.seen.append(addr[0])
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn):
        try:
            conn.settimeout(TIMEOUT + 2)
            data = b""
            while not data.endswith(b"\n") and len(data) < 200:
                part = conn.recv(200)
                if not part:
                    break
                data += part
            if self.silent:
                time.sleep(TIMEOUT + 1)
            elif data.startswith(HELLO):
                conn.sendall(ANSWER + data[len(HELLO):])
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


def probe(address, port, timeout=TIMEOUT):
    """Join the listener at (address, port) the way a friend's table would start to: (verdict, detail, ms, error text)."""
    nonce = secrets.token_hex(4).encode()
    t = time.monotonic()

    def ms():
        return (time.monotonic() - t) * 1000

    try:
        s = socket.create_connection((address, int(port)), timeout=timeout)
    except ConnectionRefusedError as e:
        return REFUSED, "nothing answered on port %s at this address (or a firewall turned it away)" % port, ms(), \
            net_log.error_text(e)
    except socket.timeout as e:
        return TIMED_OUT, "no answer at all within %.0f s" % timeout, ms(), net_log.error_text(e)
    except OSError as e:
        if getattr(e, "errno", None) in UNREACHABLE_ERRNOS or getattr(e, "winerror", None) in UNREACHABLE_ERRNOS:
            return NO_ROUTE, "this PC has no way to that address", ms(), net_log.error_text(e)
        return ERROR, "the connection failed", ms(), net_log.error_text(e)
    try:
        s.settimeout(timeout)
        s.sendall(HELLO + nonce + b"\n")
        data = b""
        while not data.endswith(b"\n") and len(data) < 200:
            part = s.recv(200)
            if not part:
                break
            data += part
    except socket.timeout as e:
        return NO_ANSWER, "connected, but nothing answered within %.0f s (something else is on this port?)" % timeout, ms(), \
            net_log.error_text(e)
    except OSError as e:
        return ERROR, "connected, then the connection broke", ms(), net_log.error_text(e)
    finally:
        try:
            s.close()
        except OSError:
            pass
    if data.strip() == (ANSWER + nonce).strip():
        return OK, "reached the test listener and it answered", ms(), ""
    if not data:
        return NO_ANSWER, "connected, but the other end closed without answering (something else is on this port?)", ms(), ""
    return NO_ANSWER, "connected, but the answer wasn't the test's (another program is on this port)", ms(), ""


def ways_in(info):
    """[(label, address)] to try, in order: loopback, LAN, Tailscale, a public address on an interface."""
    out = [("loopback", "127.0.0.1")]
    for kind, label in (("lan", "LAN"), ("tailscale", "Tailscale"), ("public", "public (on this PC)")):
        for ip, k in info.get("addresses") or []:
            if k == kind and (label, ip) not in out and ":" not in ip:
                out.append((label, ip))
    ts = info.get("tailscale")
    if ts and ("Tailscale", ts) not in out:
        out.append(("Tailscale", ts))
    return out


def advice(results, info, port, listened_port, listen_error=None, categories=None):
    """Plain-English lines from the verdicts. results: [(label, address, verdict, detail, ms, error)].
    listen_error: why the game's own port couldn't be used, when it couldn't (round NETLOG1b).
    categories: [(alias, category)] from network_categories (round NETLOG1d); None or [] = not known, no line about it."""
    lines = []
    by = {}
    for label, _a, verdict, *_ in results:
        by.setdefault(label, []).append(verdict)
    loop_ok = OK in by.get("loopback", [])
    others = [(lab, v) for lab, vs in by.items() if lab != "loopback" for v in vs]
    why = listen_problem(listen_error) if listen_error is not None else "busy"
    if listened_port != port and why == "busy":
        lines.append("Port %s was busy (a game already hosting, or another program), so this test used port %s. Close "
                     "Manticore's hosting and run it again to test the real port." % (port, listened_port))
    elif listened_port != port and why == "denied":
        lines.append("Windows would not let a program listen on port %s, and no program holds it: Windows has reserved it (some "
                     "programs - Hyper-V, WSL, Docker - reserve port ranges; 'netsh int ipv4 show excludedportrange "
                     "protocol=tcp' lists them) or security software refuses. A hosted game (java.exe) can't use it either: "
                     "choose another port on the Host screen and run this test with --port <that port>. This test used port "
                     "%s." % (port, listened_port))
    elif listened_port != port:
        lines.append("Port %s couldn't be used (%s), so this test used port %s. A hosted game would hit the same error on "
                     "port %s." % (port, net_log.error_text(listen_error), listened_port, port))
    ts_probes = by.get("Tailscale", [])                 # round NETLOG1b (review R6): what the Tailscale address itself did
    if not loop_ok:
        lines.append("Not even 127.0.0.1 worked: something on this PC (security software?) stops programs from listening or "
                     "connecting. Hosting can't work until that's fixed.")
        return lines
    if not others:
        lines.append("Only 127.0.0.1 could be tried: this PC has no LAN or Tailscale address. Is it connected to a network?")
    elif all(v == OK for _l, v in others):
        lines.append("Every address answered. This PC can listen on port %s. That does NOT prove another PC can get in: "
                     "Windows Firewall doesn't filter a PC's connections to itself." % listened_port)
    elif all(v != OK for _l, v in others):
        lines.append("Only 127.0.0.1 works: something on this PC (a firewall rule or security software) turns away connections "
                     "to its own network addresses. Another PC will need the firewall opened (java.exe, Private networks)%s."
                     % ("" if ts_probes else " or Tailscale"))
    else:
        bad = sorted({lab for lab, v in others if v != OK})
        lines.append("Only some addresses answered (not: %s): another PC will need the firewall opened for that network%s."
                     % (", ".join(bad), "" if "Tailscale" in bad else ", or Tailscale"))
    lines.append("If a friend still can't connect: Windows Firewall may be blocking java.exe (the game's host) or python.exe /"
                 " Manticore: allow it on Private networks (and on Public if Windows calls your network Public).")
    for alias in public_networks(categories):         # round NETLOG1d (review G1): say which network is Public, by name
        lines.append("Windows calls %s a Public network: java.exe must be allowed on Public networks too, or that network set "
                     "to Private (Settings > Network & internet > %s > Private)." % (alias, alias))
    ts = info.get("tailscale")
    ts_failed = any(v != OK for v in ts_probes)
    if ts and "tailscale_cli" in info and not info["tailscale_cli"]:   # round NETLOG1b (review R6): 100.64.0.0/10 is also CGNAT's
        lines.append("%s looks like a Tailscale address, but the tailscale program didn't answer: it may be a mobile or internet "
                     "provider's carrier-grade NAT address instead. Check that Tailscale is running and signed in." % ts)
    elif ts and ts_failed:                             # ... and the probe of that address is what decides the advice
        lines.append("Tailscale is on (%s), but this PC couldn't reach itself through that address: check that Tailscale "
                     "says Connected. A friend on the tailnet can't join there until it answers." % ts)
    elif ts:
        lines.append("Tailscale is on (%s): a friend on the same tailnet can join that address directly - no router port "
                     "needed." % ts)
    else:
        lines.append("No Tailscale address on this PC: across the internet, the router must forward port %s to this PC "
                     "(or use Tailscale, README part C)." % port)
    if not any(k == "public" for _ip, k in info.get("addresses") or []):
        lines.append("This PC has no public address of its own (normal behind a home router): this test can't check the "
                     "router's port forwarding - only a friend's PC can.")
    return lines


def _oem_codec():
    """Round NETLOG1b (review R1): the console's OEM code page (437, 850 ...), which netsh writes in - not the ANSI one (1252)
    Python's text=True decodes with. Windows only; None elsewhere or when it can't be read."""
    try:
        import ctypes
        cp = ctypes.windll.kernel32.GetOEMCP()
        return "cp%d" % cp if cp else None
    except Exception:
        return None


def decode_output(data, oem=None):
    """netsh's bytes as text, never raising: UTF-8 when it is valid UTF-8 (a PC set to "Use Unicode UTF-8"), else the OEM code
    page, else Windows-1252, else Latin-1 (every byte is something in Latin-1). A str is returned as it is (tests)."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for codec in ("utf-8", oem or _oem_codec(), "cp1252"):
        if not codec:
            continue
        try:
            return data.decode(codec)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("latin-1")


RULE_KEYS = ("rule name", "enabled", "action", "profiles", "program", "localport", "protocol")


def firewall_lines(run=subprocess.run, oem=None):
    """Windows only: the firewall profiles' state and inbound rules naming java / python / Manticore. [] elsewhere.
    Round NETLOG1b (review R1): netsh's output is read as bytes and decoded here (decode_output) - text=True decoded it with
    the ANSI code page and a rule name with an umlaut stopped the self-test with a traceback; anything that still goes wrong
    becomes one "could not be read" line. A rule whose keys aren't English (another Windows language) keeps its first lines."""
    if os.name != "nt":
        return []
    flags = 0x08000000
    out = []

    def netsh(args, timeout):
        try:
            done = run(["netsh"] + args, capture_output=True, timeout=timeout, creationflags=flags)
            return decode_output(done.stdout, oem), None
        except Exception as e:                      # noqa: BLE001 - the self-test must reach its advice and its END line
            return "", e

    state, e = netsh(["advfirewall", "show", "allprofiles", "state"], 10)
    if e is not None:
        out.append("firewall: profiles could not be read (%s)" % net_log.error_text(e))
    out += ["firewall: " + x.strip() for x in state.splitlines() if x.strip() and not x.startswith("-")][:12]
    rules, e = netsh(["advfirewall", "firewall", "show", "rule", "name=all", "dir=in", "verbose"], 20)
    if e is not None:
        return out + ["firewall: could not be read (%s)" % net_log.error_text(e)]
    hits = 0
    for block in rules.replace("\r\n", "\n").split("\n\n"):
        low = block.lower()
        if any(w in low for w in ("java", "python", "manticore")):
            hits += 1
            if hits <= 12:
                lines = [x.strip() for x in block.splitlines() if x.strip() and not x.strip().startswith("-")]
                keep = [x for x in lines if x.split(":")[0].strip().lower() in RULE_KEYS] or lines[:7]
                out.append("firewall rule: " + " | ".join(keep))
    out.append("firewall: %d inbound rule(s) name java / python / Manticore" % hits)
    return out


# Round NETLOG1d (review gap G1): which networks Windows calls Public and which Private. A firewall prompt answered for Private
# only leaves java.exe blocked on a Public network (general knowledge), so the advice names a Public network when there is one.
# PowerShell's Get-NetConnectionProfile, not netsh's "currentprofile": netsh's section headers are translated on other Windows
# languages; NetworkCategory's values are the enum's names (Public, Private, DomainAuthenticated) - not translated, my estimate.
PROFILE_COMMAND = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                   "Get-NetConnectionProfile | Format-List InterfaceAlias,NetworkCategory"]
PROFILE_TIMEOUT = 15                                # PowerShell can take a few seconds to start (my estimate)
CATEGORIES = {"public": "Public", "private": "Private", "domainauthenticated": "DomainAuthenticated"}


def parse_profiles(text):
    """Get-NetConnectionProfile's Format-List text -> [(alias, category)], in its order. Blocks are separated by blank lines;
    each line is "Name : Value" (the spaces around the colon vary). A known category is given its usual spelling whatever its
    case; any other value is kept as it is (never guessed); a block with no NetworkCategory line gives "". A leading BOM (a
    file PowerShell wrote) and CRLF line ends are fine."""
    out = []
    block = {}

    def flush():
        alias = block.get("interfacealias")
        if alias:
            cat = block.get("networkcategory", "")
            out.append((alias, CATEGORIES.get(cat.lower(), cat)))
        block.clear()

    for raw in (text or "").replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            block[key.strip().lower()] = value.strip()
    flush()
    return out


def network_categories(run=subprocess.run, timeout=PROFILE_TIMEOUT):
    """([(alias, category)], why) - why is "" when Windows answered, else why it can't tell. Never raises; no window."""
    if os.name != "nt":
        return [], "not Windows"
    try:
        done = run(PROFILE_COMMAND, capture_output=True, timeout=timeout, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        return [], "PowerShell didn't answer within %s s" % timeout
    except Exception as e:                          # noqa: BLE001 - powershell.exe missing, or anything else: say so, go on
        return [], "PowerShell couldn't be run (%s)" % net_log.error_text(e)
    try:
        text = decode_output(done.stdout)
        cats = parse_profiles(text)
    except Exception as e:                          # noqa: BLE001
        return [], "its answer couldn't be read (%s)" % net_log.error_text(e)
    if cats:
        return cats, ""
    code = getattr(done, "returncode", None)
    err = decode_output(getattr(done, "stderr", b"")).strip().splitlines()
    if code:
        return [], "Get-NetConnectionProfile failed (exit %s%s)" % (code, (": " + err[0].strip()[:160]) if err else "")
    return [], "Get-NetConnectionProfile listed no network"


def category_lines(cats, why):
    """The lines for the output and the connection log: one per network, or one saying why it couldn't tell."""
    if not cats:
        return ["  networks: couldn't tell which Windows calls Public or Private (%s)" % (why or "no answer")]
    return ["  network %s: %s" % (alias, cat or "(no category given)") for alias, cat in cats]


def public_networks(cats):
    """The networks other than Tailscale that Windows calls Public (the brief: Tailscale is left out)."""
    return [alias for alias, cat in cats or [] if cat == "Public" and "tailscale" not in alias.lower()]


def run(port=forge_net.DEFAULT_PORT, timeout=TIMEOUT, out=print, folder=None, info=None, firewall=True, listener=None,
        categories=None):
    """The self-test. Returns (exit code, results). info / listener / categories: stand-ins for tests (categories: what
    network_categories returns). With firewall=False Windows is not asked about its networks either (round NETLOG1d)."""
    attempt = net_log.Attempt("selftest", folder=folder, with_interfaces=info is None, port=port, timeout="%s s" % timeout)
    if info is None:
        info = attempt.info
    else:
        attempt.note("addresses given by the caller: %s" % info)
    out("Manticore network self-test")
    for line in net_log.interface_lines(info):
        out(line)
    cats = None
    if firewall or categories is not None:          # round NETLOG1d (review G1): which networks are Public or Private
        cats, why = categories if categories is not None else network_categories()
        for line in category_lines(cats, why):
            out(line)
            attempt.note(line.strip())
    own = listener is None
    try:
        listen_error = None
        if own:
            try:
                listener = Listener("0.0.0.0", port)
            except OSError as e:
                listen_error = e                    # round NETLOG1b (review R5): busy, or reserved / refused (WinError 10013)
                attempt.step("listen", False, detail="0.0.0.0 port %s (%s)" % (port, listen_problem(e)), error=e)
                out("Port %s: can't listen (%s). Trying a free port instead." % (port, net_log.error_text(e)))
                try:
                    listener = Listener("0.0.0.0", 0)
                except OSError as e2:
                    attempt.step("listen", False, detail="0.0.0.0 any port", error=e2)
                    attempt.end("FAILED: could not listen")
                    out("Could not listen on any port (%s): something on this PC stops programs from listening."
                        % net_log.error_text(e2))
                    return 2, []
        attempt.step("listen", True, detail="0.0.0.0 port %s" % listener.port)
        results = []
        for label, address in ways_in(info):
            verdict, detail, ms, err = probe(address, listener.port, timeout)
            results.append((label, address, verdict, detail, ms, err))
            attempt.step("join %s" % label, verdict == OK, ms, "%s:%s -> %s: %s" % (address, listener.port, verdict, detail),
                         err or None)
            out("  %-22s %-16s %-10s %s%s" % (label, address, verdict, detail, ("  [" + err + "]") if err else ""))
        lines = advice(results, info, port, listener.port, listen_error, cats)
        if firewall:
            fw = firewall_lines()
            for x in fw:
                attempt.note(x)
            lines += fw[-1:] if fw else []
        out("")
        for x in lines:
            out(x)
            attempt.note("advice: " + x)
        failed = [r for r in results if r[2] != OK]
        port_ok = listen_error is None or listen_problem(listen_error) == "busy"   # round NETLOG1b: a game couldn't listen there
        attempt.end("%d of %d addresses answered%s" % (len(results) - len(failed), len(results),
                                                      "" if port_ok else "; port %s can't be used" % port))
        out("")
        out("Written to %s - an F8 bug report includes it." % net_log.log_path(folder))
        return (1 if failed or not port_ok else 0), results
    finally:
        if own and listener is not None:
            listener.close()


def port_number(text):
    """Round NETLOG1c: --port by the Host screen's own rule (forge_net.validate_port: 1024-65535), so an out-of-range port is a
    plain message (argparse's, exit 2) instead of bind()'s OverflowError - now that Manticore-cli.exe passes --port on."""
    port, problem = forge_net.validate_port(text)
    if problem:
        raise argparse.ArgumentTypeError(problem)
    return port


def seconds(text):
    """Round NETLOG1c: --timeout in seconds, more than 0 (0 would make every probe socket non-blocking)."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("The timeout is a number of seconds, like %g." % TIMEOUT)
    if not 0 < value <= 120:
        raise argparse.ArgumentTypeError("Use a timeout between 0 and 120 seconds.")
    return value


def main(argv=None):
    ap = argparse.ArgumentParser(description="Can this PC host an online game? Joins itself through each of its addresses.")
    ap.add_argument("--port", type=port_number, default=forge_net.DEFAULT_PORT)
    ap.add_argument("--timeout", type=seconds, default=TIMEOUT)
    ap.add_argument("--no-firewall", action="store_true", help="don't list the Windows firewall's rules")
    args = ap.parse_args(argv)
    code, _results = run(port=args.port, timeout=args.timeout, firewall=not args.no_firewall)
    return code


if __name__ == "__main__":
    sys.exit(main())
