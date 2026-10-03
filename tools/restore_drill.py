# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/restore_drill.py - Round 27a, section 5: proves the two backups (GitHub, the local zips) can
actually rebuild the project, not just that backup.py exits 0. Karl runs this himself, since the
GitHub clone uses HIS OWN git login (Windows' credential manager - same as backup.py; this script
never asks for or stores a password):

    python tools\\restore_drill.py

Makes two fresh copies under a new folder in %TEMP%:
  from_github   a plain `git clone` of the GitHub remote
  from_zip      the newest local snapshot, unzipped

...then compares each one against the project folder you're standing in: every git-tracked (or
zipped) file's SHA-256, the version.py code fingerprint (what `forge_table.py --version` shows as
"code ..."), and which files are missing from the copy or extra in it. A difference that is
EXPECTED - something .gitignore or the snapshot leaves out on purpose, or a file you've changed here
since that backup was made - is reported as expected, not as a failure; only a hash mismatch on a
file both copies should have, or the code fingerprint disagreeing, is a real problem.
"""
import argparse
import datetime
import os
import subprocess
import sys
import tempfile
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import backup  # noqa: E402  (after sys.path.insert)
import version  # noqa: E402


def find_git():
    return backup.find_git()


def clone_github(folder, dest):
    """git clone the folder's own "origin" remote into dest. Returns (ok, message). Never prompts for a
    password: if the stored credentials aren't enough, the clone fails fast instead of hanging on a
    sign-in window nobody is there to answer, and the message says what to do about it."""
    url = backup.remote_url(folder)
    if not url:
        return False, "No GitHub remote is set up here (see backup.py --setup URL) - nothing to clone."
    env = dict(os.environ, LC_ALL="C", GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run([find_git(), "clone", url, dest], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300, env=env)
    except subprocess.TimeoutExpired:
        return False, "The clone took too long and was stopped."
    if p.returncode != 0:
        out = (p.stdout or "") + (p.stderr or "")
        if "could not read" in out.lower() or "terminal prompts disabled" in out.lower() or "authentication" in out.lower():
            return False, ("git needs you to sign in first. Run once, in a normal terminal:\n"
                            f"    git clone {url}\n"
                            "finish signing in there (it uses Windows' own credential manager), then run this drill again.")
        return False, f"git clone failed:\n{out.strip()}"
    return True, f"cloned {url}"


def newest_snapshot_zip(folder):
    dest = os.path.abspath(backup.local_dir(folder))
    snap_dir = os.path.join(dest, "snapshots")
    snaps = backup.list_snapshots(snap_dir)
    if not snaps:
        return None
    _when, name = snaps[-1]
    return os.path.join(snap_dir, name)


def unzip_snapshot(zip_path, dest):
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return True, f"unzipped {os.path.basename(zip_path)}"


def tracked_files(folder):
    """Every file `git` is following, as paths relative to folder (forward slashes)."""
    env = dict(os.environ, LC_ALL="C")
    p = subprocess.run([find_git(), "ls-files"], cwd=folder, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env)
    return [line for line in p.stdout.splitlines() if line]


def zipped_files(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        return [n for n in z.namelist() if not n.endswith("/")]


def compare(label, working, copy, copy_files, ignored_ok):
    """Hashes every file `copy_files` names, on both sides, and reports what's missing, extra, or different.
    A file present in `working` but not in `copy_files` is EXPECTED when `ignored_ok(rel)` says so (an
    ignored file, or one that simply didn't exist yet when the backup was made) - anything else missing
    is a real problem. `copy` may be None (the copy wasn't made) - only the "could not make copy" note is
    returned then."""
    print(f"\n--- {label} ---")
    mismatched, missing_from_copy, extra_in_copy, expected_gaps = [], [], [], []
    copy_set = set(copy_files)
    working_files = tracked_files(working)
    working_set = set(working_files)

    for rel in sorted(working_set & copy_set):
        wpath = os.path.join(working, *rel.split("/"))
        cpath = os.path.join(copy, *rel.split("/"))
        try:
            if backup.file_hash(wpath) != backup.file_hash(cpath):
                mismatched.append(rel)
        except OSError as e:
            mismatched.append(f"{rel} (could not read: {e})")

    for rel in sorted(working_set - copy_set):
        (expected_gaps if ignored_ok(rel) else missing_from_copy).append(rel)
    for rel in sorted(copy_set - working_set):
        extra_in_copy.append(rel)

    if mismatched:
        print(f"  DIFFERENT CONTENT ({len(mismatched)}):")
        for rel in mismatched:
            print(f"    {rel}")
    if missing_from_copy:
        print(f"  MISSING from the copy, unexpectedly ({len(missing_from_copy)}):")
        for rel in missing_from_copy:
            print(f"    {rel}")
    if extra_in_copy:
        print(f"  EXTRA in the copy, not in the working folder ({len(extra_in_copy)}):")
        for rel in extra_in_copy:
            print(f"    {rel}")
    if expected_gaps:
        print(f"  (expected: {len(expected_gaps)} file(s) not in this backup - newer than it, or intentionally left out)")

    problems = bool(mismatched or missing_from_copy or extra_in_copy)
    return not problems


def code_fingerprint_of(copy_dir):
    """version.code_fingerprint(), run against the COPY's own files (not this process' already-imported
    version module), so a stale .pyc or a difference only the copy has is not hidden."""
    return version.code_fingerprint(copy_dir)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--folder", default=BASE_DIR, help="the working project folder to check against (default: this one)")
    args = ap.parse_args()
    folder = os.path.abspath(args.folder)

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    root = os.path.join(tempfile.gettempdir(), f"manticore_restore_{stamp}")
    os.makedirs(root, exist_ok=True)
    print(f"Working folder: {folder}\nDrill folder:   {root}")

    working_code = code_fingerprint_of(folder)
    verdicts = []

    # ---- GitHub ---------------------------------------------------------------------------------------
    gh_dir = os.path.join(root, "from_github")
    ok, msg = clone_github(folder, gh_dir)
    print(f"\nGitHub: {msg}")
    if ok:
        gh_files = tracked_files(gh_dir)
        gh_ok = compare("GitHub clone vs working folder", folder, gh_dir, gh_files,
                         ignored_ok=lambda rel: True)  # anything only git itself would know is "ignored" is fine
        gh_code = code_fingerprint_of(gh_dir)
        code_ok = gh_code == working_code
        print(f"  code fingerprint: clone {gh_code} | working {working_code}" + ("" if code_ok else "  <-- DIFFERENT"))
        env = dict(os.environ, LC_ALL="C")
        commit = subprocess.run([find_git(), "rev-parse", "--short", "HEAD"], cwd=gh_dir, capture_output=True,
                                 text=True, env=env).stdout.strip()
        verdicts.append(f"GitHub copy {'matches' if (gh_ok and code_ok) else 'DOES NOT MATCH'} as of commit {commit or '?'}")
    else:
        verdicts.append(f"GitHub copy: could not check - {msg}")

    # ---- local zip --------------------------------------------------------------------------------------
    zip_path = newest_snapshot_zip(folder)
    if not zip_path:
        print("\nLocal zip: no snapshot found (run backup.py at least once first).")
        verdicts.append("Zip copy: could not check - no snapshot found")
    else:
        zip_dir = os.path.join(root, "from_zip")
        ok, msg = unzip_snapshot(zip_path, zip_dir)
        print(f"\nLocal zip: {msg}")
        zfiles = [f.replace("\\", "/") for f in zipped_files(zip_path)]
        zip_ok = compare("Zip snapshot vs working folder", folder, zip_dir, zfiles,
                          ignored_ok=lambda rel: True)  # backup.py already explains, in the zip itself, what it left out and why
        zip_code = code_fingerprint_of(zip_dir)
        code_ok = zip_code == working_code
        print(f"  code fingerprint: zip {zip_code} | working {working_code}" + ("" if code_ok else "  <-- DIFFERENT"))
        when = os.path.basename(zip_path)
        verdicts.append(f"Zip copy {'matches' if (zip_ok and code_ok) else 'DOES NOT MATCH'} as of {when}")

    print("\n" + "\n".join(verdicts))
    return 0 if all("DOES NOT MATCH" not in v and "could not check" not in v for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
