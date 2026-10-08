# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/export_public.py - copies the project into a clean folder ready to publish as the public source
repository (GPL-3.0-or-later section 5(d): anyone who gets a copy of the program is owed the source).

    python tools/export_public.py DEST_FOLDER
    python tools/export_public.py --check          (copies nothing: would it go through, and how many files)

DEST_FOLDER is created if it doesn't exist and must be empty (or not exist yet) - this never writes into a
folder that already has files in it, so it can't silently mix an old export with a new one.

What it leaves OUT, because it is either private (Karl's own decks and bug reports), rebuilt or downloaded
again rather than shipped as source (Forge's unpacked engine, card-art cache, save files, soak runs), or a secret
(settings with a Discord webhook in them): my_decks/, bug_reports/, "Claude outputs/", cache/, forge_runtime/,
forge_decks/, saves/, soak_runs/, settings.json, bug_report_config.json, logs, __pycache__, .git - the list
backup.py already skips for its own local/GitHub copies, kept in sync by hand rather than by import (backup.py
must keep working with no other tools/ files on the path).

Round PUB1 (2 Oct): it also leaves out Karl's working notes, the way tools/nightly_package.py already did: docs/
(round notes, specs, delivered patches, screenshots - 98 MB, with his home folder's path in many of them),
CLAUDE.md, EVENING_CHECKLIST.txt, .github/ (his own CI, with the name of its secret), and the backup scripts that
cd into his folder (backup.bat, backup_task.bat). tests/ and ci/ stay: they are part of the source.

It also leaves out the AvQest display font (NOT_PUBLIC_FILES): it is free to use but not to publish, and the
game draws headings in Cinzel Decorative (SIL OFL) when it is missing.

What it makes sure IS there: LICENSE, THIRD_PARTY_NOTICES.txt, licenses/, and sample_decks/ (never my_decks/).

Before it finishes, it scans every copied file for something that looks like a Discord webhook URL and
REFUSES (naming the file) if it finds one - a stray secret is worse than a late export. Round PUB1: only a
real-shaped one (a 15-21 digit id and a token of 50+ characters; Discord's are 17-20 and 68). Several tests hold
short fake ones on purpose ("webhooks/123/SECRETTOKEN"), and the old pattern refused on them, so the export
could not finish on the project at all.
"""
import argparse
import fnmatch
import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOP_LEVEL_SKIP = {"forge_runtime", "cache", "forge_decks", "forge_bundle", "bug_reports", "my_decks", "my_art", "mpc_art", "deck_art", "saves", "stats",
                   "Claude outputs", "backups", "soak_runs",
                   "docs", ".github",                      # Round PUB1: Karl's working notes; his own CI (see the docstring)
                   "build", "dist", "installer_out",      # Round 29: the installer build holds the bundled Discord webhook
                   "net"}                                  # Round MP1: the online host's private key
ANYWHERE_SKIP = {"__pycache__", ".git", ".pytest_cache"}
SKIP_FILES = {"forge_engine.log", "backup.log", "crash_log.txt", "crash_log.old.txt", "crash_native.txt", "perf_log.txt",
              "bug_report_config.json", "bug_report_config.bundled.json", "settings.json", "backup_settings.json",
              "session.json", "backup_status.json", "test_results.txt", "quick_results.txt", "card_check_report.txt"}
# Round PUB1: top-level files that are Karl's own (working notes; scripts that cd into his folder).
TOP_LEVEL_SKIP_FILES = {"CLAUDE.md", "EVENING_CHECKLIST.txt", "backup.bat", "backup_task.bat"}
SKIP_SUFFIXES = (".pyc", ".tmp", ".jsonl", ".partial", ".log")
# Not open source: free to use, but not to publish (licenses/NOTICES.json "font-display"). gfx falls back to Cinzel Decorative without it.
NOT_PUBLIC_FILES = {"assets/fonts/AvQest.ttf", "assets/fonts/1001fonts-avqest-eula.txt"}
SECRET_NAMES = [".env", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "credentials*.json", "*secret*", "*password*", "*token*"]

WEBHOOK_RE = re.compile(r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{15,21}/[\w-]{50,}")

# Text-ish suffixes worth scanning for a leaked webhook. Binary files (images, the jar) can't contain a plain-text
# URL in a way this simple scan would catch reliably, and scanning every byte of every asset would be slow for no
# real benefit, so the scan is limited to files that are actually text.
TEXT_SUFFIXES = {".py", ".txt", ".json", ".md", ".cfg", ".ini", ".yml", ".yaml", ".java", ".bat", ".ps1", ".sh", ".toml",
                 ".cmd", ".iss", ".spec", ".csv", ".html", ".xml"}
# Round PUB1: the only .txt files at the top that are part of the source. Any other one there is left out: two captured test
# runs (quick.txt, r28d_test.txt, PowerShell's UTF-16 output with the home folder's path in every line) had been committed.
PUBLIC_TOP_TEXT = {"README.txt", "ROADMAP.txt", "THIRD_PARTY_NOTICES.txt", "START_HERE.txt", "requirements.txt",
                   "requirements-build.txt"}


class ExportError(Exception):
    pass


def _skip_name(name):
    if name in ANYWHERE_SKIP:
        return True
    if any(fnmatch.fnmatch(name.lower(), pat) for pat in SECRET_NAMES):
        return True
    return False


def iter_source_files(base_dir=BASE_DIR):
    """Yields relative paths (posix-style) of every file that should be copied."""
    for root, dirs, files in os.walk(base_dir):
        rel_root = os.path.relpath(root, base_dir)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in TOP_LEVEL_SKIP and not _skip_name(d)]
        else:
            dirs[:] = [d for d in dirs if not _skip_name(d)]
        if rel_root == "my_decks":                     # extra caution: never descend into it even if TOP_LEVEL_SKIP is edited later
            continue
        for name in files:
            if name in SKIP_FILES or _skip_name(name) or name.endswith(SKIP_SUFFIXES):
                continue
            if rel_root == "." and (name in TOP_LEVEL_SKIP_FILES or (name.lower().endswith(".txt") and name not in PUBLIC_TOP_TEXT)):
                continue
            rel = (name if rel_root == "." else os.path.join(rel_root, name)).replace(os.sep, "/")
            if rel in NOT_PUBLIC_FILES:
                continue
            yield rel


def home_forms(home=None):
    """Round PUB1: the ways this computer's home folder can be written in a file ("C:\\Users\\Name", "C:/Users/Name",
    "C:\\\\Users\\\\Name" as in a Python or JSON string), lower-case. Nothing for a home with fewer than two folders below
    the root (/root, C:\\Admin): too short to tell from ordinary text. Read at run time, so no one's name is written here."""
    m = re.match(r"^([A-Za-z]:)?(.*)$", home or os.path.expanduser("~"))
    drive, parts = m.group(1) or "", [p for p in re.split(r"[\\/]+", m.group(2)) if p]
    if len(parts) < 2:
        return []
    forms = {drive + "\\" + "\\".join(parts), drive + "/" + "/".join(parts), drive + "\\\\" + "\\\\".join(parts)}
    return sorted(f.lower() for f in forms)


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):            # PowerShell's "> file" writes UTF-16
        return raw.decode("utf-16", errors="ignore")
    return raw.decode("utf-8", errors="ignore")


def problem(path, forms=None):
    """Why this file must not be published, or None: a webhook-shaped URL (a secret), or this computer's home folder path."""
    if os.path.splitext(path)[1].lower() not in TEXT_SUFFIXES:
        return None
    try:
        text = _read_text(path)
    except OSError:
        return None
    if WEBHOOK_RE.search(text):
        return "contains what looks like a Discord webhook URL"
    low = text.lower()
    for form in (home_forms() if forms is None else forms):
        if form in low:
            return "contains this computer's home folder path (%s)" % form
    return None


def _has_webhook(path):
    return problem(path, forms=[]) is not None


def scan_for_secrets(dest_dir):
    """Raises ExportError, naming the file, if any copied text file holds a webhook-shaped URL or the home folder's path."""
    forms = home_forms()
    for root, _dirs, files in os.walk(dest_dir):
        for name in files:
            path = os.path.join(root, name)
            why = problem(path, forms)
            if why:
                rel = os.path.relpath(path, dest_dir)
                raise ExportError(f"{rel} {why} - refusing to export until it is removed")


def check(base_dir=BASE_DIR):
    """Round PUB1: what an export would refuse on, without copying anything: [(relative path, why), ...] of the files that
    would be exported and must not be. Empty = the export would go through."""
    forms = home_forms()
    out = []
    for rel in iter_source_files(base_dir):
        why = problem(os.path.join(base_dir, rel), forms)
        if why:
            out.append((rel, why))
    return out


def export(dest_dir, base_dir=BASE_DIR):
    if os.path.isdir(dest_dir) and os.listdir(dest_dir):
        raise ExportError(f"{dest_dir} already has files in it - point this at an empty or new folder")
    os.makedirs(dest_dir, exist_ok=True)
    copied = 0
    for rel in iter_source_files(base_dir):
        src = os.path.join(base_dir, rel)
        dst = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    for required in ("LICENSE", "THIRD_PARTY_NOTICES.txt"):
        if not os.path.isfile(os.path.join(dest_dir, required)):
            raise ExportError(f"{required} wasn't found in the source tree - run tools/build_notices.py first "
                               f"(and make sure LICENSE exists) before exporting")
    if not os.path.isdir(os.path.join(dest_dir, "licenses")):
        raise ExportError("licenses/ wasn't found in the source tree - Round 27 should have created it")
    try:
        scan_for_secrets(dest_dir)
    except ExportError:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    return copied


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dest", nargs="?", help="empty (or not-yet-existing) folder to export the public source into")
    ap.add_argument("--check", action="store_true", help="copy nothing: say how many files would go and whether the export would refuse")
    args = ap.parse_args(argv)
    if args.check:
        files = list(iter_source_files())
        hits = check()
        print(f"{len(files)} files would be exported.")
        for rel, why in hits:
            print(f"Would refuse: {rel} {why}")
        return 1 if hits else 0
    if not args.dest:
        ap.error("give a folder to export into, or --check")
    try:
        n = export(os.path.abspath(args.dest))
    except ExportError as e:
        print(f"Export refused: {e}", file=sys.stderr)
        return 1
    print(f"Exported {n} files to {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
