# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/nightly_package.py - builds the nightly test package: one zip a tester can unpack, set up and play.

    python tools/nightly_package.py OUT_FOLDER [--date YYYY-MM-DD] [--commit SHA]

GitHub runs this every night (.github/workflows/nightly.yml) and uploads the zip as a release in the private
nightly repository. You can also run it yourself to get the same zip.

What goes in:
  * the program's source, using tools/export_public.py's file list: never my_decks/, settings.json,
    bug_report_config.json, logs or caches. It refuses outright if a Discord webhook URL turns up anywhere;
  * forge_bundle/: Forge's packed engine. setup_forge.py checks it against its checksums and unpacks it on the
    tester's PC;
  * HOW_TO_RUN.txt (the tester's steps) and BUILD_INFO.txt (date, commit, version).

What is left out on top of export_public's list: Karl's working notes and tools that only make sense on his PC
(docs/, ci/, .github/, CLAUDE.md, the backup scripts and their status files, old test/report outputs) and tests/
(a tester doesn't run them, and several tests hold fake webhook URLs on purpose, which the secret scan would
rightly stop at). The same file list and the same secret scan as export_public are used, so its rules still apply.

The tester still needs Python and Java installed; HOW_TO_RUN.txt says how. A package with Python and Java
built in is the installer round (Round 29), not this.

The last line printed is the zip's path. It never overwrites an existing zip.
"""
import argparse
import datetime
import hashlib
import os
import shutil
import sys
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import export_public as ep                                   # noqa: E402  (the same rules as the public source export)

NAME = "Manticore-nightly"
PACKAGE_SKIP_DIRS = {"docs", "ci", ".github", "tests", "build", "dist", "installer_out"}     # Round 29: build/ dist/ installer_out/ hold the bundled webhook
PACKAGE_SKIP_FILES = {"CLAUDE.md", "EVENING_CHECKLIST.txt", "backup_status.json", "backup.bat", "backup_task.bat",
                      "test_results.txt", "quick_results.txt", "card_check_report.txt"}
# Never in a package, whatever else changes: checked in the finished zip itself, not just in the copy step.
NEVER = ("my_decks/", "settings.json", "bug_report_config.json", "bug_report_config.bundled.json", "bug_reports/", "forge_runtime/", "saves/", "stats/", "cache/",
         "build/", "dist/", "installer_out/")

HOW_TO_RUN = """Manticore - nightly test build {date}
=================================

This is an automatic nightly build for testing. It may have bugs; please report them with F8 in the game.

ONE-TIME SETUP (Windows)
1. Python 3.14 (python.org, or:  winget install Python.Python.3.14)
2. Java 21:  winget install EclipseAdoptium.Temurin.21.JRE
   Then CLOSE the terminal and open a NEW one.
3. In a terminal, inside this folder:
       python -m pip install -r requirements.txt
       python setup_forge.py --check
   The second command unpacks the Forge engine (about a minute) and plays a short test game. It prints OK or PROBLEM.

PLAY
       python forge_table.py

The deck screen lists five starter decks (Typal, Tokens / Go Wide, Aristocrats, Voltron, Spellslinger).
They are practice opponents in different styles, not a balanced set, and the AI plays some styles better
than others. Paste your own deck with Ctrl+V on the deck screen.

A NEWER NIGHTLY
Unpack it into a NEW folder. Copy your own decks across from the old folder's my_decks\\ if you want them.

Licence: GPL-3.0-or-later (see LICENSE). Forge is GPL-3.0 (forge_bundle/FORGE_LICENSE.txt).
Other components: THIRD_PARTY_NOTICES.txt.
"""


class PackageError(Exception):
    pass


def package_files(base_dir):
    """export_public's file list, minus the extras above."""
    for rel in ep.iter_source_files(base_dir):
        if rel.split("/", 1)[0] in PACKAGE_SKIP_DIRS or rel in PACKAGE_SKIP_FILES:
            continue
        yield rel


def _copy_source(base_dir, stage):
    os.makedirs(stage)
    for rel in package_files(base_dir):
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(base_dir, rel), dst)
    for required in ("LICENSE", "THIRD_PARTY_NOTICES.txt", os.path.join("licenses", "NOTICES.json")):
        if not os.path.isfile(os.path.join(stage, required)):
            raise PackageError(f"{required} is missing from the source - a package must carry its licences")


def _copy_bundle(base_dir, stage):
    src = os.path.join(base_dir, "forge_bundle")
    if not os.path.isfile(os.path.join(src, "forge_runtime.manifest.json")):
        raise PackageError("forge_bundle/ (with forge_runtime.manifest.json) is missing - a tester could not install Forge")
    shutil.copytree(src, os.path.join(stage, "forge_bundle"))


def _write_zip(stage, zip_path, top):
    tmp = zip_path + ".partial"
    names = []
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        for root, dirs, files in os.walk(stage):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(root, name)
                arc = top + "/" + os.path.relpath(full, stage).replace(os.sep, "/")
                z.write(full, arc)
                names.append(arc)
    with zipfile.ZipFile(tmp) as z:                              # prove it reads back before trusting it
        if z.testzip() is not None or len(z.namelist()) != len(names):
            raise PackageError("the zip did not read back correctly")
    os.replace(tmp, zip_path)
    return names


def check_names(names, top):
    """Raises PackageError if anything private made it into the zip."""
    bad = [n for n in names for never in NEVER if n[len(top) + 1:].startswith(never)]
    if bad:
        raise PackageError("private files got into the package: " + ", ".join(bad[:5]))


def build(out_dir, date=None, commit=None, base_dir=BASE_DIR):
    date = date or datetime.date.today().isoformat()
    commit = (commit or "unknown")[:12]
    top = f"{NAME}-{date}"
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, top + ".zip")
    if os.path.exists(zip_path):
        raise PackageError(f"{zip_path} already exists - not overwriting it")
    stage = os.path.join(out_dir, top)
    if os.path.exists(stage):
        raise PackageError(f"{stage} already exists - remove it first")
    try:
        _copy_source(base_dir, stage)
        try:
            ep.scan_for_secrets(stage)
        except ep.ExportError as e:
            raise PackageError(f"the secret scan refused: {e}")
        _copy_bundle(base_dir, stage)
        version = "?"
        try:
            sys.path.insert(0, base_dir)
            import version as v
            version = v.VERSION
        except Exception:
            pass
        with open(os.path.join(stage, "HOW_TO_RUN.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(HOW_TO_RUN.format(date=date))
        with open(os.path.join(stage, "BUILD_INFO.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f"nightly {date}\nversion {version}\ncommit {commit}\n")
        names = _write_zip(stage, zip_path, top)
        check_names(names, top)
    except BaseException:
        if os.path.exists(zip_path):                         # a package that failed a check is never left behind
            os.remove(zip_path)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if os.path.exists(zip_path + ".partial"):
            os.remove(zip_path + ".partial")
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    with open(zip_path + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{h.hexdigest()}  {os.path.basename(zip_path)}\n")
    return zip_path, len(names)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="folder to write the zip into")
    ap.add_argument("--date", help="the build date (default: today)")
    ap.add_argument("--commit", help="the commit it was built from (for BUILD_INFO.txt)")
    args = ap.parse_args(argv)
    try:
        path, n = build(os.path.abspath(args.out), args.date, args.commit)
    except PackageError as e:
        print(f"Nightly package refused: {e}", file=sys.stderr)
        return 1
    print(f"{n} files, {os.path.getsize(path) / 1e6:.1f} MB")
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
