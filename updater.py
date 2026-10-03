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
     feed, and then launch_installer() starts a small hidden PowerShell helper and the program closes itself. The helper
     waits until this program (and so its Java, which follows within ~2 s: ParentWatch) has gone, runs the installer with
     /SILENT (a progress window, no questions; the same AppId installs over this copy, and decks, settings and saves live in
     the per-user folders, so they stay), then starts the new Manticore.exe.

Trust: the feed and the installer both come over HTTPS from the same place (GitHub), and the checksum in the feed must match
the download - the same trust as downloading the installer by hand from that page. Signed updates (an offline key) are the
"production" step in BRIEFS_ROUNDS_28_31 (tufup); not this round.
"""
import base64
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
SETUP_SWITCHES = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
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


def check(url, timeout=FEED_TIMEOUT, session=None):
    """(UpdateInfo, None) when the feed could be read, else (None, the reason). Never raises."""
    if not url_ok(url):
        return None, "the update address isn't https"
    try:
        import requests
        http = session or requests
        r = http.get(url, timeout=timeout, headers={"User-Agent": _user_agent()}, stream=True)
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
def ps_quote(text):
    """A PowerShell single-quoted string: nothing inside is special except the quote itself, which is doubled."""
    return "'" + str(text).replace("'", "''") + "'"


def helper_script(setup, app_exe, pid):
    """The PowerShell the helper runs: wait for this program (and its Java) to go, install silently, start the new copy."""
    args = ",".join(ps_quote(s) for s in SETUP_SWITCHES)
    app_dir = os.path.dirname(app_exe)
    return "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        "Wait-Process -Id %d -Timeout 120" % int(pid),
        # Java exits within about 2 s of its parent (ParentWatch); wait for any java.exe from this program's own folder
        "$deadline = (Get-Date).AddSeconds(20)",
        "while ((Get-Date) -lt $deadline -and (Get-Process java -ErrorAction SilentlyContinue | Where-Object { $_.Path -like (%s + '*') })) "
        "{ Start-Sleep -Milliseconds 500 }" % ps_quote(app_dir),
        "Start-Sleep -Seconds 1",
        "$p = Start-Process -FilePath %s -ArgumentList %s -PassThru -Wait" % (ps_quote(setup), args),
        "Start-Process -FilePath %s" % ps_quote(app_exe),
    ])


def encoded(script):
    """powershell -EncodedCommand wants base64 of the script in UTF-16LE: no quoting problems at all on the command line."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def app_exe():
    """Manticore.exe in this program's folder (also when running Manticore-cli.exe)."""
    return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "Manticore.exe")


def launch_installer(setup, game_running=False, exe=None, pid=None, popen=None):
    """Start the hidden helper (see helper_script). (True, None) once it's running - the caller then closes the program -
    or (False, why). Never during a game."""
    if game_running:
        return False, "Finish or leave the game first."
    if os.name != "nt" and not testing():
        return False, "Updating itself only works on Windows."
    if not os.path.isfile(setup):
        return False, "The downloaded installer is missing."
    exe = exe or app_exe()
    pid = pid or os.getpid()
    script = helper_script(setup, exe, pid)
    try:
        with open(os.path.join(os.path.dirname(setup), "last_update.ps1.txt"), "w", encoding="utf-8") as f:
            f.write(script + "\n")                                  # for support: what the helper was told to do
    except OSError:
        pass
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
           "-EncodedCommand", encoded(script)]
    tries = [0]
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        # CREATE_BREAKAWAY_FROM_JOB first: if whatever started Manticore put it in a job that closes with it, the helper must
        # not die with it. A job that forbids breaking away refuses that flag, so then try without it.
        tries = [flags | 0x01000000, flags]
    error = None
    for flags in tries:
        try:
            (popen or subprocess.Popen)(cmd, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, None
        except OSError as e:
            error = e
    return False, "Couldn't start the installer (%s)." % error
