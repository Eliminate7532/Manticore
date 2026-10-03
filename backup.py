# SPDX-License-Identifier: GPL-3.0-or-later
"""
backup.py - back the project up in TWO places with ONE command: a local copy on this computer, and GitHub (any git remote).

    python backup.py                       local copy first, then save + upload to GitHub
    python backup.py "what I changed"      the same, with your own note for the GitHub history
    python backup.py --setup URL           first time only: connect this folder to an EMPTY GitHub repository
    python backup.py --local-only          only the local copy (works before Git or GitHub are set up)
    python backup.py --local-dir FOLDER    keep the local copies in FOLDER from now on (another drive is safest)
    python backup.py --status              is everything backed up?
    python backup.py --enable-tests-on-github   once: GitHub runs the offline tests after every upload (ci/tests.yml)
    python backup.py --auto                for a scheduled task: no questions, writes backup.log

LOCAL COPY: a dated zip of the whole project (backups folder > snapshots), made only when something changed. Open a zip in
File Explorer to get any file back. Forge's packed engine (forge_bundle) is kept once, beside the zips, instead of in each one.
Recent zips are all kept; older ones are thinned to one per day, and anything over 90 days old is removed.
GITHUB: every backup is a new "commit", so any older version can be brought back; nothing is overwritten.
What is left out of both (Forge's unpacked engine, downloaded card art, logs, passwords) is listed in .gitignore / EXCLUDED below.
Only the Python standard library is needed for the local copy; the GitHub part also needs git.
"""
import argparse
import contextlib
import datetime
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import zipfile

FOLDER = os.path.dirname(os.path.abspath(__file__))
MAX_FILE_BYTES = 90 * 1024 * 1024                 # GitHub refuses files over 100 MB
BRANCH = "main"
SETTINGS_FILE = "backup_settings.json"            # remembers --local-dir (not backed up to GitHub: it is specific to this computer)
STATUS_FILE = "backup_status.json"                # last_ok/last_error/error/commit - read by the table's deck-screen banner (round 27a)
SNAP_PREFIX = "commander_sim"
KEEP_ALL_DAYS = 3                                 # every local zip from the last 3 days is kept
KEEP_DAILY_DAYS = 90                              # then the newest zip of each day, until 90 days, then they are removed
SNAPSHOT_MAX_FILE = 50 * 1024 * 1024              # a file bigger than this is left out of the zip (and reported)
TOP_LEVEL_SKIP = {"forge_runtime", "cache", "forge_decks", "forge_bundle", "bug_reports", "soak_runs",
                  "build", "dist", "installer_out", "net"}       # rebuilt or downloaded again (forge_bundle: kept separately); bug_reports: other people's data; soak_runs: tools\soak.py's own output (Round 28b), can be large; net: the online host's private key (Round MP1); build/ dist/ installer_out/: the Round 29 installer build, which holds the bundled Discord webhook and must never be committed or backed up
ANYWHERE_SKIP = {"__pycache__", ".git"}
SKIP_FILES = {"forge_engine.log", "forge_engine.prev.log", "forge_engine.crashed.log", "session.json", "backup.log", "crash_log.txt", "crash_log.old.txt", "crash_native.txt", "perf_log.txt",
              "bug_report_config.json", "bug_report_config.bundled.json", STATUS_FILE}          # both hold the Discord webhook address: a secret
SKIP_SUFFIXES = (".pyc", ".tmp", ".jsonl", ".partial")
NO_PROMPTS = False                                # True for --auto: nobody is there to answer a question, so git must not wait for one
SECRET_NAMES = [".env", "*.pem", "*.key", "*.p12", "id_rsa*", "id_ed25519*", "credentials*.json", "*secret*", "*password*", "*token*"]
# Windows treats these names specially (a request to open "nul.txt" opens the NUL device, not a file called that), whatever the
# extension. One of these broke the backup on 2026-09-24/25 (see docs/incoming/STATUS_2026-09-24.md): a stray `nul`, created by
# an AI session that redirected output to it (meaning /dev/null on Linux), got the NUL device's own timestamp - 1970 - which
# zipfile refused. Round 27a: these are now skipped (snapshot) and refused (git) instead of crashing.
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
LOCK_STALE_MINUTES = 10                           # a .git/index.lock older than this, with no git process running, is safe to clear
GIT_HELP = ("Git is not installed (or this window was opened before it was). Install it once with:\n"
            "    winget install --id Git.Git -e\n"
            "then CLOSE this window, open a NEW one, and run the command again.")


def is_reserved_name(name):
    """True for a Windows-reserved device name (con, nul, com1, ...), with or without an extension, case-insensitive."""
    return name.split(".", 1)[0].lower() in RESERVED_NAMES


CI_SOURCE = os.path.join("ci", "tests.yml")                        # the workflow lives here until it is switched on ...
CI_TARGET = os.path.join(".github", "workflows", "tests.yml")      # ... and GitHub only looks here


class BackupError(Exception):
    pass


def say(text=""):
    print(text, flush=True)


# ---- git ------------------------------------------------------------------------------------------------------------

def find_git():
    path = shutil.which("git")
    if path:
        return path
    for cand in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe",
                 os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")):
        if os.path.isfile(cand):
            return cand
    raise BackupError(GIT_HELP)


def git(args, folder, check=True, timeout=120):
    """Run git; returns (exit code, all its output as text)."""
    env = dict(os.environ, LC_ALL="C")                            # English messages, so explain_push_failure() can read them
    if NO_PROMPTS:
        env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        p = subprocess.run([find_git()] + list(args), cwd=folder, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise BackupError("git took too long and was stopped. If a sign-in window is open, finish signing in and run the command again.")
    out = (p.stdout or "") + (p.stderr or "")
    if check and p.returncode != 0:
        raise BackupError(f"git {' '.join(args)} failed:\n{out.strip()}")
    return p.returncode, out


def is_repo(folder):
    try:
        code, out = git(["rev-parse", "--show-toplevel"], folder, check=False)
    except BackupError:
        return False
    return code == 0 and os.path.normcase(os.path.realpath(out.strip())) == os.path.normcase(os.path.realpath(folder))


def remote_url(folder):
    code, out = git(["remote", "get-url", "origin"], folder, check=False)
    return out.strip() if code == 0 else None


def git_process_running():
    """Best-effort check for a running git.exe (Windows) / git (elsewhere). False (not True) on any error: refusing to
    ever clear a lock would be as bad as clearing one it shouldn't - erring towards "assume not running" only matters
    once the lock is ALSO already past LOCK_STALE_MINUTES, which is the real safety margin."""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq git.exe"], capture_output=True, text=True, timeout=10).stdout
            return "git.exe" in out
        out = subprocess.run(["pgrep", "-x", "git"], capture_output=True, text=True, timeout=10).stdout
        return bool(out.strip())
    except Exception:
        return False


def clear_stale_lock(folder):
    """git leaves .git/index.lock behind if a command is interrupted mid-write (2026-09-25: this blocked the backup
    after the `nul` incident). A lock newer than LOCK_STALE_MINUTES, or with git still running, is left alone - this
    run gives up with a clear message instead of risking a live command; only a lock that is both old AND has no git
    process behind it is removed."""
    lock = os.path.join(folder, ".git", "index.lock")
    if not os.path.isfile(lock):
        return
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(lock))
    age = datetime.datetime.now() - mtime
    if age < datetime.timedelta(minutes=LOCK_STALE_MINUTES):
        raise BackupError(f"A git lock file exists ({lock}) and is only {int(age.total_seconds() // 60)} minute(s) old - "
                          "another git command may still be running. Try again in a few minutes.")
    if git_process_running():
        raise BackupError(f"A git lock file exists ({lock}) and a git process is currently running. Try again once it finishes.")
    try:
        os.remove(lock)
    except OSError as e:
        raise BackupError(f"A stale git lock file ({lock}, from {mtime:%Y-%m-%d %H:%M}) exists and could not be removed: {e}. "
                          "Delete it by hand and try again.")
    say(f"Removed a stale git lock from {mtime:%Y-%m-%d %H:%M} (a previous git command was interrupted).")


def ensure_identity(folder, name=None, email=None, interactive=True):
    """Git puts a name and an email on every backup. Ask once and remember it for this folder."""
    def current(key):
        return git(["config", key], folder, check=False)[1].strip()
    if name:
        git(["config", "user.name", name], folder)
    if email:
        git(["config", "user.email", email], folder)
    have_name, have_email = current("user.name"), current("user.email")
    if have_name and have_email:
        return
    if not interactive or not sys.stdin or not sys.stdin.isatty():
        raise BackupError("Git does not know your name and email yet. Run this once, with your own details:\n"
                          "    python backup.py --name \"Karl\" --email \"you@example.com\"\n"
                          "(GitHub > Settings > Emails shows a private 'noreply' address you can use, so your real email stays hidden.)")
    say("Git puts a name and an email on every backup. It only has to ask once.")
    say("Tip: on GitHub open Settings > Emails to find a private 'noreply' address, so your real email stays hidden.")
    if not have_name:
        git(["config", "user.name", input("Your name: ").strip() or "Karl"], folder)
    if not have_email:
        entered = input("Your email: ").strip()
        if not entered:
            raise BackupError("An email is needed (any address works; the GitHub 'noreply' one is best).")
        git(["config", "user.email", entered], folder)


# ---- checks before saving --------------------------------------------------------------------------------------------

def staged_files(folder):
    code, out = git(["diff", "--cached", "--name-status", "-z"], folder)
    parts = [p for p in out.split("\0") if p]
    result, i = [], 0
    while i < len(parts):
        status = parts[i]
        n = 2 if status[:1] in "RC" else 1
        result.append((status[:1], parts[i + n]))
        i += 1 + n
    return result


def problems_with(folder, staged):
    """Reasons NOT to save right now: files GitHub would refuse, files that look like passwords, and Windows-reserved names
    (which break more than just the local zip - git itself can behave oddly around them)."""
    big, secret, reserved = [], [], []
    for status, path in staged:
        if status == "D":
            continue
        full = os.path.join(folder, path)
        if os.path.isfile(full) and os.path.getsize(full) > MAX_FILE_BYTES:
            big.append((path, os.path.getsize(full)))
        base = os.path.basename(path)
        if any(fnmatch.fnmatch(base.lower(), pat) for pat in SECRET_NAMES):
            secret.append(path)
        if is_reserved_name(base):
            reserved.append(path)
    return big, secret, reserved


def default_message(staged, auto=False):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    head = f"{'Automatic backup' if auto else 'Backup'} {stamp}"
    names = {"A": "added", "M": "changed", "D": "deleted", "R": "renamed", "C": "copied", "T": "changed"}
    body = [f"{names.get(s, s)}: {p}" for s, p in staged[:40]]
    if len(staged) > 40:
        body.append(f"... and {len(staged) - 40} more files")
    return head + ("\n\n" + "\n".join(body) if body else "")


def explain_push_failure(out, url):
    low = out.lower()
    if "workflow" in low and ("scope" in low or "refusing to allow" in low):
        return ("GitHub refused the automatic-tests file (.github/workflows/tests.yml): the sign-in this computer uses is not allowed to change "
                "'workflow' files. Nothing else is affected: your other changes are saved, and if this came from --enable-tests-on-github "
                "the file was taken out again, so the normal backups keep working. To allow it, do ONE of these and run the command again:\n"
                "  - If you use the GitHub command-line tool:  gh auth refresh -s workflow\n"
                "  - Otherwise remove the stored GitHub sign-in (Windows: Control Panel > Credential Manager > Windows Credentials, delete the "
                "entries that start with git:https://github.com), then run the command again and sign in again in the browser window "
                "that opens, accepting the 'workflow' permission.\n"
                "Do NOT create the file on the github.com website instead: the online copy would then have a change this computer lacks, "
                "and the backups would stop until it is sorted out.")
    if "gh001" in low or "exceeds github's file size limit" in low or "large files detected" in low:
        return ("GitHub refused a file because it is over 100 MB (see the message above for which one). Add that file to .gitignore, "
                "or tell Claude. The rest of your changes are saved on this computer.")
    if "not found" in low or "404" in low or "does not appear to be a git repository" in low:
        return (f"GitHub could not find the repository at {url}. Check the address (github.com/YOUR-NAME/commander_sim) and that "
                "you created it while signed in to the right account.")
    if any(k in low for k in ("authentication failed", "could not read username", "terminal prompts disabled", "403", "permission",
                              "denied", "invalid credentials")):
        return ("GitHub did not accept the sign-in. The first time, Git normally opens a browser window to sign in to GitHub - "
                "finish that, then run the command again. Make sure you are signed in as the account that owns the repository. "
                "Never paste a password or token into this window or into chat.")
    if any(k in low for k in ("rejected", "fetch first", "non-fast-forward", "unrelated histories")):
        return ("The online copy already has changes this computer does not have (usually because the repository was created with a "
                "README or .gitignore). Nothing was overwritten. Tell Claude and paste the message above; do not force it.")
    if any(k in low for k in ("could not resolve", "unable to access", "network is unreachable", "timed out", "connection")):
        return "Could not reach GitHub (no internet?). Your changes are saved on this computer; run the same command again when you are online."
    return "The upload failed; the message above is from git. Your changes are still saved on this computer."


# ---- the local copy --------------------------------------------------------------------------------------------------

def load_settings(folder):
    try:
        with open(os.path.join(folder, SETTINGS_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(folder, **values):
    data = load_settings(folder)
    data.update(values)
    with open(os.path.join(folder, SETTINGS_FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def local_dir(folder):
    """Where the local copies go: the folder chosen with --local-dir, else a folder NEXT TO the project (never inside it)."""
    chosen = load_settings(folder).get("local_dir")
    if chosen:
        return chosen
    folder = os.path.abspath(folder)
    return os.path.join(os.path.dirname(folder), os.path.basename(folder) + "_backups")


def _inside(child, parent):
    child, parent = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def snapshot_files(folder, dest):
    """What goes into a zip. Returns (files [(relative path, full path)], left_out [(relative path, reason)])."""
    files, left_out = [], []
    for root, dirs, names in os.walk(folder):
        rel_root = os.path.relpath(root, folder)
        top = rel_root == "."
        keep = []
        for d in sorted(dirs):
            full = os.path.join(root, d)
            if d in ANYWHERE_SKIP or (top and (d in TOP_LEVEL_SKIP or d.startswith("shots"))) or _inside(full, dest):
                continue
            keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            rel = n if top else os.path.join(rel_root, n)
            full = os.path.join(root, n)
            low = n.lower()
            if n in SKIP_FILES or low.endswith(SKIP_SUFFIXES) or os.path.islink(full):
                continue
            if is_reserved_name(n):
                left_out.append((rel, "Windows reserved name, delete it: see README"))
            elif any(fnmatch.fnmatch(low, pat) for pat in SECRET_NAMES):
                left_out.append((rel, "looks like a password, key or token"))
            elif os.path.getsize(full) > SNAPSHOT_MAX_FILE:
                left_out.append((rel, "bigger than %d MB" % (SNAPSHOT_MAX_FILE // 1024 // 1024)))
            else:
                files.append((rel.replace("\\", "/"), full))
    return files, left_out


def fingerprint(files):
    h = hashlib.sha256()
    unreadable = []
    for rel, full in sorted(files):
        try:
            h.update(rel.encode("utf-8") + b"\0" + file_hash(full).encode() + b"\n")
        except OSError as e:
            unreadable.append((rel, str(e)))
    return h.hexdigest(), unreadable


def snapshot_name(now, taken):
    base = f"{SNAP_PREFIX}_{now:%Y-%m-%d_%H-%M-%S}"
    name, n = base + ".zip", 2
    while name in taken:
        name = f"{base}_{n}.zip"
        n += 1
    return name


_SNAP_RE = re.compile(r"^" + SNAP_PREFIX + r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})(?:_\d+)?\.zip$")


def list_snapshots(snap_dir):
    """[(datetime, file name)] oldest first."""
    found = []
    for n in os.listdir(snap_dir) if os.path.isdir(snap_dir) else []:
        m = _SNAP_RE.match(n)
        if m:
            found.append((datetime.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H-%M-%S"), n))
    return sorted(found)


def prune_snapshots(snap_dir, now=None):
    """Thin out old zips: all of the last KEEP_ALL_DAYS days, then one per day (the newest of that day) up to KEEP_DAILY_DAYS days.
    Only files named like our zips are ever touched, and the newest zip always stays. Returns how many were removed."""
    now = now or datetime.datetime.now()
    snaps = list_snapshots(snap_dir)
    keep, seen_days = set(), set()
    for when, name in reversed(snaps):                                   # newest first
        age = now - when
        if name == snaps[-1][1] or age <= datetime.timedelta(days=KEEP_ALL_DAYS):
            keep.add(name)
        elif age <= datetime.timedelta(days=KEEP_DAILY_DAYS) and when.date() not in seen_days:
            keep.add(name)
        seen_days.add(when.date())
    removed = 0
    for _when, name in snaps:
        if name not in keep:
            try:
                os.remove(os.path.join(snap_dir, name))
                removed += 1
            except OSError:
                pass
    return removed


def sync_forge_bundle(folder, dest):
    """Keep ONE exact copy of forge_bundle/ beside the zips (it is 35 MB and almost never changes). Returns how many files were copied."""
    src, dst = os.path.join(folder, "forge_bundle"), os.path.join(dest, "forge_bundle")
    if not os.path.isdir(src):
        return 0
    os.makedirs(dst, exist_ok=True)
    copied = 0
    names = sorted(n for n in os.listdir(src) if os.path.isfile(os.path.join(src, n)))
    for n in names:
        a, b = os.path.join(src, n), os.path.join(dst, n)
        if os.path.isfile(b) and os.path.getsize(a) == os.path.getsize(b) and file_hash(a) == file_hash(b):
            continue
        tmp = b + ".partial"
        shutil.copyfile(a, tmp)
        os.replace(tmp, b)
        copied += 1
    for n in os.listdir(dst):                                            # our own copy only: drop files the project no longer has
        if n not in names and os.path.isfile(os.path.join(dst, n)):
            os.remove(os.path.join(dst, n))
    return copied


def take_snapshot(folder, dest=None, now=None):
    """Make a dated zip of the project in `dest` if anything changed since the last one. Returns a dict describing what happened."""
    folder = os.path.abspath(folder)
    dest = os.path.abspath(dest or local_dir(folder))
    now = now or datetime.datetime.now()
    if _inside(dest, folder):
        raise BackupError(f"The local backup folder ({dest}) is inside the project. Choose one outside it, for example:\n"
                          "    python backup.py --local-dir D:\\Backups\\commander_sim")
    snap_dir = os.path.join(dest, "snapshots")
    try:
        os.makedirs(snap_dir, exist_ok=True)
    except OSError as e:
        raise BackupError(f"Could not use the local backup folder {dest}: {e}\n"
                          "If it is on a USB drive, plug the drive in. To choose another place:\n"
                          "    python backup.py --local-dir D:\\Backups\\commander_sim")
    for n in os.listdir(snap_dir):                                       # leftovers of a run that was interrupted
        if n.endswith(".partial"):
            try:
                os.remove(os.path.join(snap_dir, n))
            except OSError:
                pass
    files, left_out = snapshot_files(folder, dest)
    key, unreadable = fingerprint(files)
    result = {"dest": dest, "files": len(files), "left_out": left_out, "unreadable": unreadable, "made": None, "pruned": 0}
    last_file = os.path.join(dest, "last_snapshot.json")
    try:
        with open(last_file, "r", encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    if last.get("fingerprint") == key and last.get("name") and os.path.isfile(os.path.join(snap_dir, last["name"])):
        result["unchanged"] = last["name"]
    else:
        name = snapshot_name(now, {n for _w, n in list_snapshots(snap_dir)})
        tmp = os.path.join(snap_dir, name + ".partial")
        top = os.path.basename(folder)
        bad = {rel for rel, _e in unreadable}
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
                for rel, full in files:
                    if rel not in bad:
                        z.write(full, top + "/" + rel)
            with zipfile.ZipFile(tmp) as z:                              # prove the zip reads back before trusting it
                if z.testzip() is not None or len(z.namelist()) != len(files) - len(bad):
                    raise BackupError("The new zip did not pass its own check, so it was thrown away. Nothing was lost; try again.")
            os.replace(tmp, os.path.join(snap_dir, name))
        except OSError as e:
            raise BackupError(f"Could not write the local copy in {snap_dir}: {e}")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        with open(last_file, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": key, "name": name}, f)
        result["made"] = name
        result["size"] = os.path.getsize(os.path.join(snap_dir, name))
    try:
        result["bundle_copied"] = sync_forge_bundle(folder, dest)
    except OSError as e:
        raise BackupError(f"The zip was saved, but copying forge_bundle to {dest} failed: {e}")
    result["pruned"] = prune_snapshots(snap_dir, now)
    return result


def local_backup(folder):
    r = take_snapshot(folder)
    if r["made"]:
        where = os.path.join(r["dest"], "snapshots")
        say(f"Local copy saved: {r['made']} ({r['files']} files, {r['size'] / 1024 / 1024:.1f} MB) in {where}")
    else:
        say(f"Local copy: no changes since {r['unchanged']}.")
    if r.get("bundle_copied"):
        say(f"Local copy: refreshed Forge's packed engine ({r['bundle_copied']} file{'s' if r['bundle_copied'] != 1 else ''}).")
    if r["pruned"]:
        say(f"Local copy: removed {r['pruned']} old zip{'s' if r['pruned'] != 1 else ''} (older than the last {KEEP_ALL_DAYS} days, "
            "one per day is kept).")
    for rel, why in r["left_out"]:
        say(f"Local copy: left out {rel} ({why}).")
    if r["unreadable"]:
        names = ", ".join(rel for rel, _e in r["unreadable"][:5])
        raise BackupError(f"The local zip was saved but these files could not be read (is a program using them?): {names}. "
                          "They are missing from that zip; run the command again.")
    return r


def local_status(folder):
    """Prints the local-copy state. Returns True when the newest zip matches the project exactly."""
    dest = os.path.abspath(local_dir(folder))
    snaps = list_snapshots(os.path.join(dest, "snapshots"))
    say(f"Local copies: {dest}")
    if not snaps:
        say("   none yet (run: python backup.py --local-only)")
        return False
    when, name = snaps[-1]
    say(f"   {len(snaps)} zip{'s' if len(snaps) != 1 else ''}; newest {name}")
    files, _left = snapshot_files(os.path.abspath(folder), dest)
    key, _bad = fingerprint(files)
    try:
        with open(os.path.join(dest, "last_snapshot.json"), "r", encoding="utf-8") as f:
            same = json.load(f).get("fingerprint") == key
    except (OSError, ValueError):
        same = False
    say("   the newest zip matches the project." if same else "   the project has changed since the newest zip (run: python backup.py).")
    return same


# ---- the actions -----------------------------------------------------------------------------------------------------

def backup(folder, message=None, auto=False, name=None, email=None):
    if not is_repo(folder):
        raise BackupError("This folder is not set up for backup yet. Run the one-time setup:\n"
                          "    python backup.py --setup https://github.com/YOUR-NAME/commander_sim.git")
    url = remote_url(folder)
    if not url:
        raise BackupError("No GitHub address is connected yet. Run:\n    python backup.py --setup https://github.com/YOUR-NAME/commander_sim.git")
    clear_stale_lock(folder)
    ensure_identity(folder, name, email, interactive=not auto)
    git(["add", "-A"], folder)
    staged = staged_files(folder)
    big, secret, reserved = problems_with(folder, staged)
    if big or secret or reserved:
        git(["reset", "-q"], folder, check=False)               # un-stage everything; nothing was saved
        lines = ["Nothing was backed up, because:"]
        for path, size in big:
            lines.append(f"  - {path} is {size / 1024 / 1024:.0f} MB and GitHub refuses files over 100 MB. Add it to .gitignore.")
        for path in secret:
            lines.append(f"  - {path} looks like a password, key or token. Add it to .gitignore, or tell Claude if it is safe.")
        for path in reserved:
            full = os.path.join(folder, path)
            lines.append(f"  - {path} is a name Windows treats specially (a reserved device name). Delete it:\n"
                        f"        cmd /c del \"\\\\?\\{full}\"")
        raise BackupError("\n".join(lines))
    if staged:
        git(["commit", "-q", "-m", message or default_message(staged, auto)], folder)
        say(f"Saved {len(staged)} changed file{'s' if len(staged) != 1 else ''}.")
    else:
        say("No new changes to save.")
    # Push HEAD to a same-named remote branch (not always "main"): a round is worked on its own branch
    # (round-27a's convention), and pushing a hardcoded BRANCH would silently push a stale main instead,
    # leaving the round's commits stuck on this PC only. --follow-tags carries any tag made on this branch.
    code, out = git(["push", "-u", "origin", "HEAD", "--follow-tags"], folder, check=False, timeout=300)
    if code != 0:
        say(out.strip())
        raise BackupError(explain_push_failure(out, url))
    head = git(["rev-parse", "--short", "HEAD"], folder)[1].strip()
    say(f"Backed up. Latest version {head} is on {url}")
    return 0


def actions_page(url):
    """https://github.com/NAME/REPO/actions for a github.com address (https or ssh), else None."""
    m = re.match(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:)([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return f"https://github.com/{m.group(1)}/{m.group(2)}/actions" if m else None


def enable_tests(folder, name=None, email=None):
    """Copy ci/tests.yml to .github/workflows/ and upload it as a commit of its own. GitHub refuses that file when the sign-in lacks the
    'workflow' permission; then the commit and the file are taken back out, so the ordinary backups (git add -A) are never held up by it."""
    src, dst = os.path.join(folder, CI_SOURCE), os.path.join(folder, CI_TARGET)
    if not os.path.isfile(src):
        raise BackupError(f"{CI_SOURCE} is missing; it comes with the project. Restore it from a backup or tell Claude.")
    say("First a normal backup, so that everything else is safe before the new file goes up.")
    backup(folder, name=name, email=email)                        # the same checks and messages as any backup
    with open(src, "rb") as f:
        wanted = f.read()
    try:
        with open(dst, "rb") as f:
            if f.read() == wanted:
                say("The automatic tests are already switched on and up to date.")
                return 0
    except OSError:
        pass
    existed = os.path.isfile(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(wanted)
    git(["add", "--", CI_TARGET.replace(os.sep, "/")], folder)
    git(["commit", "-q", "-m", "Turn on the automatic tests" if not existed else "Update the automatic tests", "--", CI_TARGET.replace(os.sep, "/")], folder)
    code, out = git(["push", "-u", "origin", BRANCH], folder, check=False, timeout=300)
    if code != 0:
        say(out.strip())
        if git(["log", "-1", "--format=%s"], folder, check=False)[1].strip() in ("Turn on the automatic tests", "Update the automatic tests"):
            git(["reset", "-q", "--mixed", "HEAD~1"], folder, check=False)      # the commit goes; every file stays as it is
        if not existed:
            with contextlib.suppress(OSError):
                os.remove(dst)
                os.rmdir(os.path.dirname(dst))
                os.rmdir(os.path.dirname(os.path.dirname(dst)))
        else:
            git(["checkout", "-q", "--", CI_TARGET.replace(os.sep, "/")], folder, check=False)
        raise BackupError(explain_push_failure(out, remote_url(folder)))
    page = actions_page(remote_url(folder))
    say("Switched on. GitHub now runs the offline tests after every upload." + (f" Results: {page}" if page else ""))
    say("(The first run can take a few minutes. A red cross there means a test failed on the GitHub computer.)")
    return 0


def setup(folder, url, name=None, email=None):
    if not url:
        raise BackupError("Give the address of your GitHub repository, for example:\n"
                          "    python backup.py --setup https://github.com/YOUR-NAME/commander_sim.git")
    problems = []
    try:
        local_backup(folder)                                      # a local copy first: it needs nothing but this computer
    except BackupError as e:
        problems.append("LOCAL COPY: " + str(e))
    try:
        git(["--version"], folder)                                # is git here at all
        if not is_repo(folder):
            git(["init", "-q"], folder)
            git(["symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"], folder)
            say("Created the backup history in this folder (a hidden .git folder).")
        if remote_url(folder):
            git(["remote", "set-url", "origin", url], folder)
        else:
            git(["remote", "add", "origin", url], folder)
        ensure_identity(folder, name, email)
        say(f"Connected to {url}")
        say("Making the first GitHub backup. The first time, a browser window may open so you can sign in to GitHub.")
        backup(folder, message="First backup", name=name, email=email)
    except BackupError as e:
        problems.append("GITHUB: " + str(e))
    if problems:
        raise BackupError("\n\n".join(problems))
    return 0


def status(folder):
    local_ok = local_status(folder)
    say()
    online_ok = online_status(folder)
    return 0 if local_ok and online_ok else 1


def online_status(folder):
    """Prints the GitHub state. Returns True when everything is uploaded."""
    if not is_repo(folder):
        say("GitHub: not set up yet (run: python backup.py --setup https://github.com/YOUR-NAME/commander_sim.git).")
        return False
    url = remote_url(folder)
    say(f"GitHub: {url or '(none connected)'}")
    code, out = git(["log", "-1", "--format=%h  %ad  %s", "--date=format:%Y-%m-%d %H:%M"], folder, check=False)
    say(f"Latest saved version: {out.strip() if code == 0 else '(nothing saved yet)'}")
    changed = git(["status", "--porcelain"], folder)[1].strip().splitlines()
    ahead = None
    code, out = git(["rev-list", "--count", "@{u}..HEAD"], folder, check=False)
    if code == 0:
        ahead = int(out.strip() or 0)
    if changed:
        say(f"NOT saved yet: {len(changed)} changed file{'s' if len(changed) != 1 else ''} (run: python backup.py)")
        for line in changed[:15]:
            say("   " + line)
        if len(changed) > 15:
            say(f"   ... and {len(changed) - 15} more")
    if ahead is None:
        say("Never uploaded yet (run: python backup.py).")
    elif ahead:
        say(f"Saved on this computer but NOT uploaded yet: {ahead} version{'s' if ahead != 1 else ''} (run: python backup.py).")
    if not changed and ahead == 0:
        say("Everything is backed up.")
    return not changed and ahead == 0


def run_backup(folder, message=None, auto=False, name=None, email=None, local=True, online=True):
    """The local copy first (it needs nothing but this computer), then GitHub. One failing never stops the other -
    including an unexpected (non-BackupError) exception in either stage: it used to escape straight to main()'s
    console, which is how the 2026-09-24/25 silent failure happened (a ValueError from zipfile, never written
    anywhere). Now every stage's traceback is printed (so it lands in backup.log under --auto) and the other stage
    still runs."""
    problems = []
    if local:
        try:
            local_backup(folder)
        except BackupError as e:
            problems.append("LOCAL COPY: " + str(e))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            problems.append(f"LOCAL COPY: unexpected error: {type(e).__name__}: {e}")
    if online:
        try:
            backup(folder, message, auto, name, email)
        except BackupError as e:
            problems.append("GITHUB: " + str(e))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            problems.append(f"GITHUB: unexpected error: {type(e).__name__}: {e}")
    if problems:
        raise BackupError("\n\n".join(problems))
    return 0


def write_status(folder, ok, error=None):
    """backup_status.json: what the deck-screen banner (and anyone else) reads to know if backups are working.
    Written atomically (a temp file then os.replace) so a reader never sees a half-written file."""
    path = os.path.join(folder, STATUS_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    data.setdefault("last_ok", None)
    data.setdefault("last_error", None)
    if ok:
        data["last_ok"] = now
        data["error"] = None
        try:
            code, out = git(["rev-parse", "--short", "HEAD"], folder, check=False)
            data["commit"] = out.strip() if code == 0 else data.get("commit")
        except Exception:
            data["commit"] = data.get("commit")
    else:
        data["last_error"] = now
        data["error"] = error
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass                                        # status reporting must never itself break a backup run


def run_backup_and_report_status(folder, message=None, auto=False, name=None, email=None, local=True, online=True):
    """run_backup, plus keeping backup_status.json up to date either way."""
    try:
        code = run_backup(folder, message, auto, name, email, local, online)
        write_status(folder, True)
        return code
    except BackupError as e:
        write_status(folder, False, str(e))
        raise


def main(argv=None, folder=FOLDER):
    ap = argparse.ArgumentParser(description="Back the project up to a local copy and to GitHub.")
    ap.add_argument("message", nargs="?", help="a short note about what you changed (optional)")
    ap.add_argument("--setup", metavar="URL", help="first time only: connect to an empty GitHub repository")
    ap.add_argument("--status", action="store_true", help="show whether everything is backed up")
    ap.add_argument("--local-only", action="store_true", help="only the local copy (no Git or GitHub needed)")
    ap.add_argument("--local-dir", metavar="FOLDER", help="keep the local copies in FOLDER from now on (outside the project)")
    ap.add_argument("--enable-tests-on-github", action="store_true", help="once: let GitHub run the offline tests after every upload")
    ap.add_argument("--auto", action="store_true", help="for a scheduled task: no questions; results go to backup.log")
    ap.add_argument("--name", help="your name for the history (asked once if missing)")
    ap.add_argument("--email", help="your email for the history (asked once if missing)")
    args = ap.parse_args(argv)
    global NO_PROMPTS
    NO_PROMPTS = args.auto
    log = None
    try:
        if args.auto:
            log = open(os.path.join(folder, "backup.log"), "a", encoding="utf-8")
            sys.stdout = log
            say(f"--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
        if args.local_dir:
            chosen = os.path.abspath(os.path.expanduser(args.local_dir))
            if _inside(chosen, folder):
                raise BackupError("Choose a folder OUTSIDE the project for the local copies, for example D:\\Backups\\commander_sim")
            save_settings(folder, local_dir=chosen)
            say(f"Local copies will be kept in {chosen}")
        if args.status:
            return status(folder)
        if args.setup:
            return setup(folder, args.setup, args.name, args.email)
        if args.enable_tests_on_github:
            return enable_tests(folder, args.name, args.email)
        if args.local_only:
            local_backup(folder)
            return 0
        if (args.name or args.email) and is_repo(folder):
            ensure_identity(folder, args.name, args.email, interactive=False)
            if not args.message:
                say("Saved your name and email for the backup history.")
                return 0
        if args.local_dir and not args.message:
            return run_backup_and_report_status(folder, None, args.auto, args.name, args.email, online=False)
        return run_backup_and_report_status(folder, args.message, args.auto, args.name, args.email)
    except BackupError as e:
        say("")
        say("PROBLEM: " + str(e))
        return 2
    except Exception:
        # Something got past every try/except above - a real bug, not a foreseen BackupError. Still leave a record
        # instead of a silent crash to a console nobody is watching (this is exactly what happened 2026-09-24/25).
        say("")
        say("PROBLEM: an unexpected error stopped the backup - see the traceback below.")
        traceback.print_exc(file=sys.stdout)
        return 3
    finally:
        if log:
            sys.stdout = sys.__stdout__
            log.close()


if __name__ == "__main__":
    sys.exit(main())
