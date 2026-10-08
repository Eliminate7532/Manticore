# SPDX-License-Identifier: GPL-3.0-or-later
"""
updater.py - Round 30: an installed copy of Manticore updates itself. No pygame in here (update_screens.py draws it).

How it works, for a friend's installed copy (Karl's own git checkout never updates itself: it is "portable"):
  1. On the title screen, once per run and never during a game, a background thread fetches a small latest.json (CheckJob):
         {"version": "0.30.1", "url": "https://github.com/<owner>/<repo>/releases/download/v0.30.1/Manticore-0.30.1-setup.exe",
          "sha256": "<64 hex>", "size": 123456789, "notes": "what's new, in a line or two", "published": "2026-10-05"}
     Where from: update_config.json in the program folder ({"repo": "owner/name"} or {"feed_url": "https://..."}); with a
     repo it's https://github.com/<repo>/releases/latest/download/latest.json (GitHub's "latest" skips pre-releases, so a
     release meant for friends must not be marked pre-release). Nothing set: updates are off, and --version says so.
  2. A newer version: the title shows "Update now / Later / Skip this version".
  3. Update now: the installer downloads into the per-user updates/ folder (DownloadJob), its size and SHA-256 must match the
     feed, and then launch_installer() starts the installer itself and the program closes itself (1.2 s later):
         "<updates>\\Manticore-0.30.1-setup.exe" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATE /LOG="<updates>\\install.log"
     /SILENT is a progress window with no questions; the same AppId installs over this copy (decks, settings and saves live in
     the per-user folders, so they stay); /CLOSEAPPLICATIONS lets Inno's Restart Manager close this program and its Java if
     they still hold a file it must replace (CloseApplications=force in the .iss); /UPDATE makes the .iss's last [Run] line
     start the new Manticore.exe; /LOG keeps a record of every install in updates\\install.log.
     Patch 41 (4 Oct 2026): this used to be a hidden PowerShell helper (-EncodedCommand) that waited for the program, ran the
     installer and started the new copy. On Karl's Surface (Windows 11 on Arm) it never installed anything and never reopened
     the program, three times out of three, with no trace: its script silenced its own errors and kept no log.
  4. The next start reads updates\\pending_update.json (last_attempt): the version it is now at says whether that update
     finished. A finished one is one crash-log line; one that didn't finish is a crash-log entry with install.log's last lines
     (so an F8 report carries it), and the next Update now opens the installer's own window (no /SILENT) so its questions and
     errors can be seen.

Trust: the feed and the installer both come over HTTPS from the same place (GitHub), and the checksum in the feed must match
the download - the same trust as downloading the installer by hand from that page. Signed updates (an offline key) are the
"production" step in BRIEFS_ROUNDS_28_31 (tufup); not this round.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import paths
import version

CONFIG_NAME = "update_config.json"
FEED_TIMEOUT = 5                       # seconds; the title screen never waits on it (it runs on a thread)
FEED_MAX_BYTES = 64 * 1024
MIN_SIZE = 1_000_000                   # an installer is ~100+ MB; anything tiny is a broken feed
MAX_SIZE = 1_500_000_000
CHUNK = 256 * 1024
NOTES_MAX = 2000
SETUP_SWITCHES = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE")
VISIBLE_SWITCHES = ("/UPDATE",)       # the installer's own window, after an update that didn't finish
INSTALL_LOG = "install.log"            # Inno Setup's /LOG, in updates/
PENDING_NAME = "pending_update.json"   # written when an installer is started; read at the next start (last_attempt)
COMMAND_NAME = "last_update.txt"       # what was started, how, and when: for support
LOG_TAIL_LINES = 25
MANUAL = "The installer is open: follow its steps. Manticore closes now."
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateInfo:
    def __init__(self, version, url, sha256, size, notes="", published=""):
        self.version, self.url, self.sha256, self.size = version, url, sha256.lower(), int(size)
        self.notes, self.published = notes, published

    def file_name(self):
        name = os.path.basename(urlparse(self.url).path) or ""
        return name if re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.exe", name) else "Manticore-%s-setup.exe" % self.version

    def __repr__(self):
        return "UpdateInfo(%s, %d bytes)" % (self.version, self.size)


def testing():
    """MANTICORE_UPDATE_TEST=1: the tests may use http://127.0.0.1 and a portable copy."""
    return os.environ.get("MANTICORE_UPDATE_TEST") == "1"


# ---- where the feed is, and whether to look -------------------------------------------------------------------------
def read_config(folder=None):
    path = os.path.join(folder or paths.program_dir(), CONFIG_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def feed_url(folder=None):
    """The latest.json address, or None when this build has no update source."""
    env = os.environ.get("MANTICORE_UPDATE_FEED")
    if env:
        return env.strip()
    cfg = read_config(folder)
    url = str(cfg.get("feed_url") or "").strip()
    if url:
        return url
    repo = str(cfg.get("repo") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return "https://github.com/%s/releases/latest/download/latest.json" % repo
    return None


def installed():
    """A copy installed by the installer (frozen, not portable); tests can stand in with MANTICORE_UPDATE_TEST=1."""
    if testing():
        return True
    return bool(getattr(sys, "frozen", False)) and not paths.is_portable()


def enabled(check_updates=True, folder=None):
    """(True, feed URL) or (False, the reason in plain words)."""
    if os.environ.get("MANTICORE_NO_UPDATE_CHECK") == "1" and not testing():
        return False, "turned off for this run"
    if not installed():
        return False, "this copy isn't an installed one (your own copy updates through git)"
    url = feed_url(folder)
    if not url:
        return False, "no update source is set up in this build"
    if not check_updates:
        return False, "turned off in Settings"
    return True, url


def describe(check_updates=True):
    """The --version line."""
    ok, what = enabled(check_updates)
    return "updates: on (%s)" % (urlparse(what).netloc or what) if ok else "updates: off - " + what


# ---- versions and the feed ------------------------------------------------------------------------------------------
def parse_version(text):
    text = str(text or "").strip()
    if not _VERSION_RE.match(text):
        return None
    return tuple(int(p) for p in text.split("."))


def newer(candidate, current=None):
    """True when `candidate` is a later version than `current` (this copy's version.VERSION)."""
    a, b = parse_version(candidate), parse_version(current if current is not None else version.VERSION)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def url_ok(url):
    p = urlparse(str(url or ""))
    if p.scheme == "https" and p.netloc:
        return True
    return testing() and p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost")


def parse_feed(data):
    """(UpdateInfo, None) or (None, what's wrong)."""
    if not isinstance(data, dict):
        return None, "the update feed isn't a JSON object"
    v = str(data.get("version") or "").strip()
    if parse_version(v) is None:
        return None, "the update feed has no usable version"
    url = str(data.get("url") or "").strip()
    if not url_ok(url):
        return None, "the update feed's download address isn't https"
    sha = str(data.get("sha256") or "").strip()
    if not _SHA_RE.match(sha):
        return None, "the update feed's checksum is missing or wrong"
    try:
        size = int(data.get("size"))
    except (TypeError, ValueError):
        return None, "the update feed has no size"
    if not MIN_SIZE <= size <= MAX_SIZE:
        return None, "the update feed's size is not believable (%d bytes)" % size
    notes = str(data.get("notes") or "").strip()[:NOTES_MAX]
    return UpdateInfo(v, url, sha, size, notes, str(data.get("published") or "")[:40]), None


def _user_agent():
    return "Manticore/%s (+update check)" % version.VERSION


# Patch 38: GitHub answers 404 for releases/latest/... while the repository has no release yet - every start of Karl's installed
# test copy wrote "Update check failed" into the crash log (4 Oct). Still "failed", but not worth a crash-log entry.
NO_RELEASE = "no release has been published yet (the update feed answered 404)"


def check(url, timeout=FEED_TIMEOUT, session=None):
    """(UpdateInfo, None) when the feed could be read, else (None, the reason). Never raises."""
    if not url_ok(url):
        return None, "the update address isn't https"
    try:
        import requests
        http = session or requests
        r = http.get(url, timeout=timeout, headers={"User-Agent": _user_agent()}, stream=True)
        if r.status_code == 404:
            return None, NO_RELEASE
        if r.status_code != 200:
            return None, "the update feed answered %s" % r.status_code
        raw = b""
        for chunk in r.iter_content(8192):
            raw += chunk
            if len(raw) > FEED_MAX_BYTES:
                return None, "the update feed is too big"
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, "the update feed isn't valid JSON"
    except Exception as e:                                   # no network, DNS, TLS, timeout...: silent, one line in the log
        return None, "couldn't reach the update feed (%s)" % type(e).__name__
    return parse_feed(data)


class CheckJob:
    """check() on a thread: the title screen never waits for the network."""

    def __init__(self, url, current=None):
        self.url, self.current = url, current
        self.finished = False
        self.info, self.error = None, None
        self.thread = threading.Thread(target=self._run, daemon=True, name="update-check")

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        try:
            info, why = check(self.url)
            if info is not None and not newer(info.version, self.current):
                info, why = None, None                       # up to date
            self.info, self.error = info, why
        finally:
            self.finished = True


# ---- the download ---------------------------------------------------------------------------------------------------
def updates_dir():
    return os.path.join(paths.local_dir(), "updates")


class DownloadJob:
    """Downloads the installer to updates/<name>.part, checks its size and SHA-256 against the feed, and renames it. On any
    problem the partial file is deleted and `error` says what happened in plain words."""

    def __init__(self, info, folder=None, timeout=30, session=None):
        self.info = info
        self.folder = folder or updates_dir()
        self.timeout, self.session = timeout, session
        self.done_bytes, self.total = 0, info.size
        self.finished, self.cancelled = False, False
        self.path, self.error = None, None
        self.thread = threading.Thread(target=self._run, daemon=True, name="update-download")

    def start(self):
        self.thread.start()
        return self

    def cancel(self):
        self.cancelled = True

    def fraction(self):
        return min(1.0, self.done_bytes / max(1, self.total))

    def _run(self):
        final = os.path.join(self.folder, self.info.file_name())
        part = final + ".part"
        try:
            os.makedirs(self.folder, exist_ok=True)
            if self._verified(final):                          # already downloaded (an earlier try was interrupted later)
                self.path = final
                return
            import requests
            http = self.session or requests
            digest = hashlib.sha256()
            r = http.get(self.info.url, timeout=self.timeout, headers={"User-Agent": _user_agent()}, stream=True)
            if r.status_code != 200:
                raise _Fail("The download failed (the server answered %s). Try again later." % r.status_code)
            if not url_ok(getattr(r, "url", self.info.url)):
                raise _Fail("The download was redirected somewhere unsafe, so it was stopped.")
            with open(part, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if self.cancelled:
                        raise _Fail("Cancelled.")
                    if not chunk:
                        continue
                    f.write(chunk)
                    digest.update(chunk)
                    self.done_bytes += len(chunk)
                    if self.done_bytes > self.info.size:
                        raise _Fail("The download was bigger than expected, so it was stopped.")
            if self.done_bytes != self.info.size or digest.hexdigest() != self.info.sha256:
                raise _Fail("The download was damaged (its checksum didn't match). Try again later.")
            os.replace(part, final)
            self.path = final
            self._prune(keep=final)
        except _Fail as e:
            self.error = str(e)
        except Exception as e:
            self.error = "The download failed (%s). Try again later." % type(e).__name__
        finally:
            if self.path is None:
                _remove(part)
            self.finished = True

    def _verified(self, path):
        try:
            if os.path.getsize(path) != self.info.size:
                return False
            return sha256_file(path) == self.info.sha256
        except OSError:
            return False

    def _prune(self, keep):
        """Only the newest installer is kept: each is ~100+ MB."""
        try:
            for name in os.listdir(self.folder):
                p = os.path.join(self.folder, name)
                if p != keep and name.lower().endswith((".exe", ".part")):
                    _remove(p)
        except OSError:
            pass


class _Fail(Exception):
    pass


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- running the installer ------------------------------------------------------------------------------------------
def setup_command(setup, log_path, visible=False):
    """The installer's command line, as one string: Inno Setup reads /LOG="<path>" with the quotes around the path only, and
    Windows paths can't contain a quote, so nothing here needs escaping."""
    switches = VISIBLE_SWITCHES if visible else SETUP_SWITCHES
    return '"%s" %s /LOG="%s"' % (setup, " ".join(switches), log_path)


def in_job():
    """Is this program inside a Windows job object? (None = unknown.) Written down with each update: a job that closes with the
    program can take the installer with it, which is what CREATE_BREAKAWAY_FROM_JOB is for."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        result = ctypes.c_int(0)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        if not k32.IsProcessInJob(ctypes.c_void_p(k32.GetCurrentProcess()), None, ctypes.byref(result)):
            return None
        return bool(result.value)
    except Exception:
        return None


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write_text(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def launch_installer(setup, game_running=False, to_version=None, visible=False, popen=None, startfile=None):
    """Start the downloaded installer, then the caller closes the program. Never during a game.
    (True, None): the installer is running silently and will reopen Manticore. (True, MANUAL): it couldn't be started that
    way, so its own window was opened instead (os.startfile) for the person to click through. (False, why): nothing started.
    Either way that something started, updates/pending_update.json says what, for last_attempt() at the next start."""
    if game_running:
        return False, "Finish or leave the game first."
    if os.name != "nt" and not testing():
        return False, "Updating itself only works on Windows."
    if not os.path.isfile(setup):
        return False, "The downloaded installer is missing."
    folder = os.path.dirname(os.path.abspath(setup))
    log_path = os.path.join(folder, INSTALL_LOG)
    _remove(log_path)                                                # install.log is always this attempt's
    cmd = setup_command(setup, log_path, visible)
    job = in_job()
    tries = [0]
    if os.name == "nt":
        flags = 0x00000200 | 0x00000008                              # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        # CREATE_BREAKAWAY_FROM_JOB first: if whatever started Manticore put it in a job that closes with it, the installer must
        # not die with it. A job that forbids breaking away refuses that flag (OSError), so then try without it.
        tries = [flags | 0x01000000, flags]
    error, how = None, None
    for flags in tries:
        try:
            (popen or subprocess.Popen)(cmd, creationflags=flags, close_fds=True, cwd=folder, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            how = "visible" if visible else "silent"
            break
        except OSError as e:
            error = e
    result = (True, None)
    if how is None:
        try:
            (startfile or getattr(os, "startfile"))(setup)            # the installer's own window: the person clicks through
            how = "manual"
            result = (True, MANUAL)
        except (OSError, AttributeError) as e:
            _write_text(os.path.join(folder, COMMAND_NAME), "%s\nNOT started: %s / %s\n%s\n" % (_now(), error, e, cmd))
            return False, "Couldn't start the installer (%s)." % (error or e)
    _write_text(os.path.join(folder, COMMAND_NAME),
                "%s\nstarted: %s (creation flags %s; in a job: %s)\n%s\n" % (_now(), how, hex(flags) if how != "manual" else "-",
                                                                             job, cmd if how != "manual" else setup))
    try:
        with open(os.path.join(folder, PENDING_NAME), "w", encoding="utf-8") as f:
            json.dump({"from": version.VERSION, "to": str(to_version or ""), "how": how, "at": _now(), "in_job": job}, f)
    except OSError:
        pass
    return result


def log_tail(path, lines=LOG_TAIL_LINES, max_bytes=64 * 1024):
    """The last lines of a log, [] when there is none. Only its end is read: Inno's log names every file it copies (~37,000 in
    this program). UTF-8 or UTF-16 (a byte-order mark, or a zero second byte), whichever the installer wrote - UNVERIFIED which."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - max_bytes)
            f.seek(start)
            raw = f.read()
    except OSError:
        return []
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or head[1:2] == b"\x00":
        raw = raw[start % 2:]
        text = raw.decode("utf-16-be" if head.startswith(b"\xfe\xff") else "utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    out = text.lstrip("\ufeff").splitlines()
    if start and out:
        out = out[1:]                                                # the first line read is most likely cut
    return out[-lines:]


def last_attempt(folder=None, current=None):
    """What became of the last update this copy started, read once per run (update_screens). None: none pending. Else the
    pending record plus "ok" (this copy is now at least that version) and "log" (install.log's last lines).
    A finished one's record is removed, and so is the installer it ran (~130 MB each). One that didn't finish keeps its record,
    marked "failed" (the next Update now opens the installer's own window) and "noted" (its crash-log entry is written once, not
    at every start), and keeps the installer (Update now reuses a download whose size and checksum still match)."""
    folder = folder or updates_dir()
    path = os.path.join(folder, PENDING_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or parse_version(data.get("to")) is None:
        _remove(path)
        return None
    ok = not newer(data["to"], current)
    result = dict(data, ok=ok, log=log_tail(os.path.join(folder, INSTALL_LOG)))
    if ok:
        _remove(path)
        try:
            for name in os.listdir(folder):
                if name.lower().endswith((".exe", ".part")):
                    _remove(os.path.join(folder, name))
        except OSError:
            pass
    else:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dict(data, failed=True, noted=True), f)
        except OSError:
            pass
    return result
