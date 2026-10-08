# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/check_dist.py - the smoke test for a finished Manticore build folder (Round 29, SONNET_SPEC_R29 section 6).

    python tools\check_dist.py dist\Manticore             everything, including running the two executables (Windows)
    python tools\check_dist.py dist\Manticore --no-run    layout, licences and secrets only (no process is started)
    python tools\check_dist.py dist\Manticore --release   a licence-list gap is an error, not a to-do

tools\build_installer.py runs this as its step 6 and stops if it fails. What it checks:

  1. the layout: every file the program needs is there, and nothing that must not ship (.git, portable.txt, settings.json,
     my_decks, a real bug_report_config.json, soak_runs, cache, __pycache__, loose .py source);
  2. `Manticore-cli.exe --version`, run with PATH cut down to System32 and JAVA_HOME unset: it names this version, this build's
     code (never da39a3ee, the hash of nothing, and never "unknown"), the Forge build and a per-user data folder;
  3. `Manticore-cli.exe --soak 1` in the same stripped environment (MANTICORE_USER_DIR = a temp folder): the summary is VALID, the
     canary is OK, and the run wrote NOTHING inside the program folder (an installed copy is read-only in use);
  4. licences: every .dll and .pyd outside jre/ has an entry in licenses/NOTICES.json -> native_libraries -> libraries (SPEC 4.6:
     this is the item that has blocked build_notices --release since Round 27), and every Maven artifact inside forge.jar is covered;
  5. secrets: no file except bug_report_config.bundled.json holds a Discord webhook address. File NAMES are printed, never contents.

The native-library list is filled in from the first real Windows build, so on that first build item 4 reports a to-do (the names
and sizes of every unlisted file, also written to a text file) instead of failing a --test build; --release makes it an error.
"""
import argparse
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import java_crash                                  # patch 46: is_crash_file

BUNDLED_CONFIG = "bug_report_config.bundled.json"
EMPTY_HASH_CODE = "da39a3ee"                      # sha1 of nothing: what a frozen copy printed before build_info.json existed
EXPECTED_SAMPLE_DECKS = 10                         # round FMT1: 6 Commander + 4 Brawl
WEBHOOK_BYTES = re.compile(rb"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{8,}")

REQUIRED_FILES = ["Manticore{exe}", "Manticore-cli{exe}", "jre/bin/java{exe}", "forge_runtime/forge.jar",
                  "forge_runtime/forge_bridge.jar", "forge_runtime/VERSION.txt", "sounds/cues.json", "licenses/NOTICES.json",
                  "LICENSE", "THIRD_PARTY_NOTICES.txt", "build_info.json", "README_FIRST.txt", "START_HERE.txt",
                  "banned_commander.snapshot.json", "banned_brawl.snapshot.json",
                  "forge_card_names.json"]                         # patch 38: Forge's card-name index, made by the build
REQUIRED_DIRS = ["sample_decks", "assets/fonts", "forge_runtime/res/cardsfolder", "jre/legal"]
FORBIDDEN_TOP = [".git", "portable.txt", "settings.json", "my_decks", "bug_report_config.json", "soak_runs", "cache"]


class Result:
    """problems: block the build. todo: the licence-list gap (blocking only for --release). notes: informational lines."""

    def __init__(self):
        self.problems = []
        self.todo = []
        self.notes = []

    def blocking(self, release=False):
        return self.problems + (self.todo if release else [])


# ---- the file tree, scanned once --------------------------------------------------------------------------------------

def scan(folder):
    """[(relative path with forward slashes, size)] for every file under folder."""
    out = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, folder).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append((rel, size))
    return out


def scan_dirs(folder):
    """Relative paths of every directory under folder."""
    out = []
    for root, dirs, _files in os.walk(folder):
        for d in dirs:
            out.append(os.path.relpath(os.path.join(root, d), folder).replace("\\", "/"))
    return out


# ---- 1. layout --------------------------------------------------------------------------------------------------------

def check_layout(folder, tree, dirs, exe=".exe", sample_decks=EXPECTED_SAMPLE_DECKS):
    """[problems]"""
    problems = []
    have = {rel.lower() for rel, _ in tree}
    for pattern in REQUIRED_FILES:
        rel = pattern.format(exe=exe)
        if rel.lower() not in have:
            problems.append(f"missing file: {rel}")
    dirset = {d.lower() for d in dirs}
    for rel in REQUIRED_DIRS:
        if rel.lower() not in dirset:
            problems.append(f"missing folder: {rel}")
    decks = [rel for rel, _ in tree if rel.lower().startswith("sample_decks/") and rel.lower().endswith(".txt")]
    if sample_decks is not None and len(decks) != sample_decks:
        problems.append(f"sample_decks/ has {len(decks)} deck(s), expected {sample_decks}")
    top = {rel.split("/", 1)[0].lower() for rel, _ in tree} | {d.split("/", 1)[0].lower() for d in dirs}
    for name in FORBIDDEN_TOP:
        if name.lower() in top:
            problems.append(f"must not ship: {name} is in the build")
    if any(d.lower().endswith("__pycache__") or d.lower() == "__pycache__" for d in dirs):
        problems.append("must not ship: a __pycache__ folder is in the build")
    loose = [rel for rel, _ in tree if "/" not in rel and rel.lower().endswith(".py")]
    if loose:
        problems.append(f"must not ship: loose .py source in the program folder ({', '.join(sorted(loose)[:5])})")
    crashes = sorted(rel for rel, _ in tree if java_crash.is_crash_file(rel))       # patch 46 (soak night 14)
    if crashes:
        problems.append("must not ship: a Java crash report is in the build (" + ", ".join(crashes[:3]) + ") - it holds the "
                        "PC's user name, PATH and temp folder")
    return problems


# ---- 4. licences ------------------------------------------------------------------------------------------------------

def load_manifest(folder=None, path=None):
    path = path or os.path.join(folder or BASE_DIR, "licenses", "NOTICES.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def native_patterns(manifest):
    """Lower-case fnmatch patterns from native_libraries.libraries[*].file and .files (a name, or a glob such as '*.pyd')."""
    pats = []
    for lib in (manifest.get("native_libraries") or {}).get("libraries") or []:
        for key in ("file", "files"):
            value = lib.get(key)
            for item in ([value] if isinstance(value, str) else (value or [])):
                if isinstance(item, str) and item.strip():
                    pats.append(item.strip().replace("\\", "/").lower())
    return pats


def is_covered(rel, patterns):
    rel = rel.lower()
    base = rel.rsplit("/", 1)[-1]
    for pat in patterns:
        if fnmatch.fnmatchcase(rel if "/" in pat else base, pat):
            return True
    return False


def unlisted_natives(tree, manifest):
    """[(relative path, size)] of every .dll/.pyd outside jre/ (the jlinked Java has its own licence entry) that no
    native_libraries entry covers."""
    pats = native_patterns(manifest)
    return [(rel, size) for rel, size in sorted(tree)
            if rel.lower().endswith((".dll", ".pyd")) and not rel.lower().startswith("jre/") and not is_covered(rel, pats)]


def jar_coverage_problems(forge_jar, manifest):
    """Every Maven artifact inside forge.jar is covered by a java_components entry (the same rule build_notices applies to
    the checkout's own forge.jar, here for the jar that is actually in the build)."""
    if not os.path.isfile(forge_jar):
        return []
    try:
        from tools import build_notices
    except ImportError:
        import build_notices
    covered = set()
    for comp in manifest.get("java_components", {}).values():
        covered.update(comp.get("artifacts", []))
    problems = []
    for group_artifact, version in build_notices.jar_maven_artifacts(forge_jar).items():
        pair = f"{group_artifact.split(':', 1)[1]}:{version}"
        if pair not in covered and not build_notices.is_forge_module(group_artifact, version, manifest):
            problems.append(f"forge.jar has Maven artifact {group_artifact}:{version} with no java_components entry")
    return problems


# ---- 5. secrets -------------------------------------------------------------------------------------------------------

def webhook_files(folder, tree, block=1 << 20):
    """Relative paths of every file EXCEPT the bundled one that holds a Discord webhook address. Never reads contents out."""
    hits = []
    for rel, size in tree:
        if rel == BUNDLED_CONFIG or size == 0:
            continue
        try:
            with open(os.path.join(folder, rel), "rb") as f:
                tail = b""
                while True:
                    chunk = f.read(block)
                    if not chunk:
                        break
                    if WEBHOOK_BYTES.search(tail + chunk):
                        hits.append(rel)
                        break
                    tail = chunk[-300:]
        except OSError:
            continue
    return hits


# ---- 2 and 3. running the executables --------------------------------------------------------------------------------

def stripped_env(user_dir, base=None):
    """The environment of a PC with nothing installed: PATH is System32 only, no JAVA_HOME, and MANTICORE_USER_DIR points at a
    scratch folder so the real per-user folders are never touched."""
    base = os.environ if base is None else base
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE",
            "HOMEPATH", "USERNAME", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS", "PATHEXT", "HOME"}
    env = {k: v for k, v in base.items() if k.upper() in keep}
    system_root = env.get("SYSTEMROOT") or env.get("SystemRoot") or r"C:\Windows"
    env["PATH"] = os.path.join(system_root, "System32") if os.name == "nt" else "/usr/bin:/bin"
    env.pop("JAVA_HOME", None)
    env["MANTICORE_USER_DIR"] = user_dir
    env.pop("MANTICORE_PORTABLE", None)
    return env


def read_build_info(folder):
    try:
        with open(os.path.join(folder, "build_info.json"), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check_version_run(folder, runner=subprocess.run, exe=".exe", timeout=180):
    """[problems] from `Manticore-cli.exe --version` in a stripped environment."""
    info = read_build_info(folder)
    user = tempfile.mkdtemp(prefix="manticore_check_version_")
    try:
        proc = runner([os.path.join(folder, "Manticore-cli" + exe), "--version"], capture_output=True, text=True,
                      timeout=timeout, env=stripped_env(user), cwd=user)
    except (OSError, subprocess.SubprocessError) as e:
        return [f"--version could not run: {type(e).__name__}: {e}"]
    out = (proc.stdout or "")
    problems = []
    if proc.returncode != 0:
        problems.append(f"--version exited with {proc.returncode}")
    version = info.get("version")
    if not version or version not in out:
        problems.append(f"--version output does not name the build's version ({version!r})")
    m = re.search(r"\bcode ([0-9a-f]{8}|unknown)\b", out)
    code = m.group(1) if m else None
    if code is None:
        problems.append("--version output has no 'code ...' part")
    elif code == EMPTY_HASH_CODE or code == "unknown":
        problems.append(f"--version reports code {code}: the build does not know its own code (build_info.json)")
    elif info.get("code") and code != info["code"]:
        problems.append(f"--version reports code {code}, build_info.json says {info['code']}")
    if not any(line.startswith("Forge ") for line in out.splitlines()):
        problems.append("--version output has no 'Forge ...' line")
    data_lines = [line for line in out.splitlines() if line.startswith("data:")]
    if not data_lines:
        problems.append("--version output has no 'data: ...' line")
    elif "portable" in data_lines[0]:
        problems.append(f"--version says this copy is portable ({data_lines[0]}); an installed build keeps its data per user")
    return problems


def one_game_problems(summary):
    """[problems] from one game's soak_summary.txt. One game is too short for the run verdict's "VALID" (it also wants 3+ kinds of
    question over the run - Round 29 review, 2 Oct), so here: no problem found, and every game ended at game over. The Sandbox's
    --soak 10 still needs VALID."""
    lines = summary.splitlines()
    first = (lines[0] if lines else "").strip()
    if first == "SOAK RUN: VALID":
        return []
    out = []
    problems = next((ln for ln in lines if ln.startswith("PROBLEMS (")), None)
    if problems is None or not problems.startswith("PROBLEMS (0 kind"):
        out.append(f"--soak 1 summary says: {first or 'nothing'}; {problems or 'no PROBLEMS line'}")
    games = [ln for ln in lines if ln.strip().startswith("game ") and " ended=" in ln]
    if not games:
        out.append("--soak 1 summary lists no game")
    for ln in games:
        if " ended=game_over" not in ln:
            out.append("--soak 1: a game did not reach game over: " + ln.strip()[:160])
    return out


def check_one_game(folder, runner=subprocess.run, exe=".exe", timeout=40 * 60):
    """[problems] from `Manticore-cli.exe --soak 1` in a stripped environment, including 'it wrote nothing into its own folder'."""
    user = tempfile.mkdtemp(prefix="manticore_check_soak_")
    before = {rel for rel, _ in scan(folder)}
    try:
        proc = runner([os.path.join(folder, "Manticore-cli" + exe), "--soak", "1"], capture_output=True, text=True,
                      timeout=timeout, env=stripped_env(user), cwd=user)
    except subprocess.TimeoutExpired:
        return [f"--soak 1 did not finish within {timeout // 60} minutes"]
    except (OSError, subprocess.SubprocessError) as e:
        return [f"--soak 1 could not run: {type(e).__name__}: {e}"]
    problems = []
    if proc.returncode != 0:
        problems.append(f"--soak 1 exited with {proc.returncode}")
    out = proc.stdout or ""
    if "canary: OK" not in out:
        problems.append("--soak 1: the canary did not report OK")
    summaries = sorted(glob.glob(os.path.join(user, "soak", "**", "soak_summary.txt"), recursive=True), key=os.path.getmtime)
    if not summaries:
        problems.append("--soak 1 wrote no soak_summary.txt under the per-user folder")
    else:
        with open(summaries[-1], "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        problems += one_game_problems(text)
    new = sorted({rel for rel, _ in scan(folder)} - before)
    if new:
        problems.append(f"the program wrote {len(new)} file(s) inside its own folder (an installed copy is read-only in use): "
                        + ", ".join(new[:5]))
    return problems


# ---- everything -------------------------------------------------------------------------------------------------------

def update_source_note(folder):
    """Round 30: one line saying where this build's installed copies will look for updates (never blocking)."""
    path = os.path.join(folder, "update_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError:
        return "updates: off in this build (no update_config.json)"
    except ValueError:
        return "updates: off in this build (update_config.json isn't valid JSON)"
    cfg = cfg if isinstance(cfg, dict) else {}
    feed = str(cfg.get("feed_url") or "").strip()
    repo = str(cfg.get("repo") or "").strip()
    if feed:
        return f"updates: on, from {feed}"
    if repo:
        return f"updates: on, from github.com/{repo} (Releases -> latest.json)"
    return "updates: off in this build (update_config.json names no repo or feed_url)"


PLACEHOLDER = re.compile(r"\[[^\]\n]{1,80}\]")


def start_here_placeholders(folder):
    """The [bracketed] blanks still in START_HERE.txt (disk size, version, known issues): a TO DO, blocking only --release."""
    try:
        with open(os.path.join(folder, "START_HERE.txt"), "r", encoding="utf-8") as f:
            return PLACEHOLDER.findall(f.read())
    except OSError:
        return []                                     # a missing file is check_layout's problem


def check_dist(folder, run=True, release=False, runner=subprocess.run, exe=".exe", sample_decks=EXPECTED_SAMPLE_DECKS,
               natives_file=None):
    """Run every check. Returns a Result. `run=False` skips the two executables (layout, licences and secrets only)."""
    res = Result()
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        res.problems.append(f"not a folder: {folder}")
        return res
    tree = scan(folder)
    dirs = scan_dirs(folder)
    res.notes.append(f"{len(tree)} files, {sum(s for _, s in tree) / (1024 * 1024):.0f} MB")
    res.problems += check_layout(folder, tree, dirs, exe=exe, sample_decks=sample_decks)
    try:
        manifest = load_manifest(folder)
    except (OSError, ValueError) as e:
        manifest = {}
        res.problems.append(f"licenses/NOTICES.json could not be read ({type(e).__name__})")
    gaps = unlisted_natives(tree, manifest)
    if gaps:
        res.todo.append(f"{len(gaps)} .dll/.pyd file(s) have no native_libraries entry in licenses/NOTICES.json")
        if natives_file:
            with open(natives_file, "w", encoding="utf-8") as f:
                f.write("Unlisted native libraries in the build (file names and sizes only). Fill licenses/NOTICES.json ->\n"
                        "native_libraries.libraries from this list: one entry per library, 'file' (or 'files' with * patterns).\n\n")
                for rel, size in gaps:
                    f.write(f"{rel}\t{size}\n")
            res.notes.append(f"unlisted native libraries written to {natives_file}")
    blanks = start_here_placeholders(folder)
    if blanks:
        res.todo.append("START_HERE.txt still has blanks to fill in: " + ", ".join(blanks[:5]))
    res.problems += jar_coverage_problems(os.path.join(folder, "forge_runtime", "forge.jar"), manifest)
    res.notes.append(update_source_note(folder))
    leaks = webhook_files(folder, tree)
    if leaks:
        res.problems.append("a Discord webhook address is in: " + ", ".join(leaks[:5]) + " (names only; see SPEC 4.5)")
    if run and not res.problems:                      # no point starting a broken build
        res.problems += check_version_run(folder, runner=runner, exe=exe)
        res.problems += check_one_game(folder, runner=runner, exe=exe)
    elif run:
        res.notes.append("executables not run: fix the problems above first")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Smoke-test a finished Manticore build folder.")
    ap.add_argument("folder", help="the build folder (dist\\Manticore)")
    ap.add_argument("--no-run", action="store_true", help="don't start the executables")
    ap.add_argument("--release", action="store_true", help="a native-library list gap is an error")
    ap.add_argument("--natives-file", default=None, help="write the unlisted native libraries to this text file")
    args = ap.parse_args(argv)
    res = check_dist(args.folder, run=not args.no_run, release=args.release, natives_file=args.natives_file,
                     exe=".exe" if os.name == "nt" else "")
    for line in res.notes:
        print("  " + line)
    for line in res.todo:
        print(("FAIL: " if args.release else "TO DO: ") + line)
    for line in res.problems:
        print("FAIL: " + line)
    bad = res.blocking(release=args.release)
    print("check_dist: " + ("FAILED" if bad else "OK"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
