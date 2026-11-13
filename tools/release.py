# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/release.py - Round REL1: one command releases Manticore to testers (Karl, 3 Nov 2026: "yes queue REL1 for Option 1").

    python tools\\release.py --branch <local or origin branch> --notes "What's new, for testers." --dry-run
    python tools\\release.py --branch <local or origin branch> --notes "What's new, for testers."

Run it on the Windows PC, from the commander_sim folder. It does, in order, what claude/RELEASE_RUNBOOK.md lists by hand:

  0 checks    Windows; the repo root; `git status` clean; `gh auth status` OK; the public folder is a git repo whose remote
              is Eliminate7532/Manticore; not between :05-:08 or :35-:38 (the backup task commits at :06 and :36) - it waits.
  1 test      fetch, check out the branch, setup_forge.py, forge_table.py --version, tools\\quick_tests.py. A pass is
              remembered for that exact commit (release_logs/tested.json), so a rerun after a later failure doesn't repeat it.
  2 version   VERSION in the release commit itself (`git show <sha>:version.py`, not the working tree) must be higher
              than the published latest.json (no release yet / 404 = allowed). Before the merge (Round REL1B, Karl 4 Nov
              2026), so a refusal leaves main untouched.
  3 merge     main fast-forwarded to the tested commit and pushed. Anything but a fast-forward is refused.
  4 build     tools\\build_installer.py --test; the installer and its .sha256 must exist.
  5 feed      tools\\make_update_feed.py with --notes (a warning above 211 characters: 3 lines on the update card).
  6 public    export_public --check (refused on any "Would refuse"), export, robocopy /MIR into the public folder (exit 0-7 =
              success), git add / commit "Manticore <v>" / push there.
  7 publish   "Publish Manticore <v> to testers? [y/N]" (--yes skips it); gh release create ... --latest (never a
              pre-release or a draft), or gh release upload --clobber when the release exists already.
  8 check     gh release view; latest.json read back over https: its version is <v> and its sha256 the installer's.

It stops at the first failure, saying what failed and how to carry on (`--from <step>`). `--dry-run` prints every command
and changes nothing: no checkout, merge, push, build or publish (read-only git and gh commands still run). Every command and
its output go to release_logs/<v>_<time>.txt (git-ignored, not backed up, not exported); tokens and webhook addresses are
blanked first. It never reads bug_report_config*.json.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STEPS = ["checks", "test", "version", "merge", "build", "feed", "public", "publish", "check"]
PUBLIC_REPO = "Eliminate7532/Manticore"
FEED_URL = f"https://github.com/{PUBLIC_REPO}/releases/latest/download/latest.json"
NOTES_WARN = 211                     # RELEASE_RUNBOOK: 211 characters fitted 3 lines on the update card, 256 needed 4
BACKUP_WINDOWS = ((5, 8), (35, 38))  # minutes past the hour: the backup task commits at :06 and :36
LOG_DIR = "release_logs"
STAMP_FILE = "tested.json"
ROBOCOPY_OK = range(0, 8)            # robocopy: 0-7 = success, 8+ = failure
FEED_TRIES = 3                       # GitHub's "latest" redirect can lag a few seconds behind a new release

_SECRET_PATTERNS = [
    re.compile(r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+", re.I),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)(token|password|secret)(\s*[:=]\s*)\S+"),
]


def redact(text):
    """Blank tokens and webhook addresses before anything is printed or logged."""
    text = str(text)
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(lambda m: m.group(1) + m.group(2) + "[hidden]", text)
        else:
            text = pat.sub("[hidden]", text)
    return text


def parse_version(text):
    text = str(text or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*", text):
        return None
    return tuple(int(p) for p in text.split("."))


def higher(a, b):
    """True when version string a is later than b (missing parts count as 0)."""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return False
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) > pb + (0,) * (n - len(pb))


def version_from_source(text):
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text or "", re.M)
    return m.group(1) if m else None


class StepFailed(Exception):
    def __init__(self, step, why, resume=None):
        super().__init__(why)
        self.step, self.why, self.resume = step, why, resume


class Runner:
    """Runs one command and returns (exit code, output). Tests replace it with a fake that records and scripts results."""

    def __init__(self, log=None):
        self.log = log

    def __call__(self, cmd, cwd=None):
        try:
            p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace")
            return p.returncode, p.stdout or ""
        except OSError as e:
            return 127, f"could not start {cmd[0]}: {e}"


def fetch_json(url, timeout=15):
    """(status, data): (200, dict), (404, None), or (None, error text)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Manticore-release-tool"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read(256 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(e)


class Release:
    def __init__(self, args, run=None, fetch=fetch_json, ask=input, now=datetime.datetime.now, sleep=time.sleep,
                 base=BASE_DIR, is_windows=None, say=print, python=sys.executable, getcwd=os.getcwd):
        self.a = args
        self.base = base
        self.fetch, self.ask, self.now, self.sleep = fetch, ask, now, sleep
        self.is_windows = (os.name == "nt") if is_windows is None else is_windows
        self._say = say
        self.python = python
        self.getcwd = getcwd
        self.dry = bool(args.dry_run)
        self.public_dir = args.public_dir or os.path.join(os.path.expanduser("~"), "Documents", "manticore_public")
        self.docs_dir = os.path.dirname(os.path.abspath(self.public_dir))
        self.log_lines = []
        self.run_cmd = run or Runner()
        self.sha = None          # the commit being released
        self.ver = None
        self.ref = None
        self.ran = []            # every command actually executed (for the log and the tests)

    # ---------- output ----------
    def say(self, text=""):
        text = redact(text)
        self.log_lines.append(text)
        self._say(text)

    def write_log(self):
        try:
            folder = os.path.join(self.base, LOG_DIR)
            os.makedirs(folder, exist_ok=True)
            stamp = self.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(folder, f"{self.ver or 'unknown'}_{stamp}{'_dry' if self.dry else ''}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(redact(x) for x in self.log_lines) + "\n")
            return path
        except OSError:
            return None

    # ---------- commands ----------
    def run(self, cmd, cwd=None, changes=True, quiet=False):
        """changes=True: skipped (only printed) in --dry-run. Returns (code, output)."""
        shown = " ".join(_quote(c) for c in cmd)
        where = f"  (in {cwd})" if cwd and os.path.abspath(cwd) != os.path.abspath(self.base) else ""
        if self.dry and changes:
            self.say(f"    would run: {shown}{where}")
            return 0, ""
        self.log_lines.append(redact(f"    $ {shown}{where}"))
        code, out = self.run_cmd(cmd, cwd=cwd or self.base)
        self.ran.append(list(cmd))
        out = redact(out or "")
        self.log_lines.extend("      " + line for line in out.splitlines())
        self.log_lines.append(f"      (exit {code})")
        if not quiet:
            self.say(f"    $ {shown}{where} -> exit {code}")
        return code, out

    def must(self, step, cmd, why, cwd=None, changes=True, resume=None):
        code, out = self.run(cmd, cwd=cwd, changes=changes)
        if code != 0:
            tail = "\n".join(out.strip().splitlines()[-8:])
            raise StepFailed(step, f"{why} (exit {code})" + (f":\n{tail}" if tail else ""), resume)
        return out

    def git(self, *a, cwd=None):
        return ["git"] + (["-C", cwd] if cwd else []) + list(a)

    def py(self, *a):
        return [self.python] + list(a)

    # ---------- helpers ----------
    def installer(self):
        return os.path.join(self.base, "installer_out", f"Manticore-{self.ver}-setup.exe")

    def feed_file(self):
        return os.path.join(self.base, "installer_out", "latest.json")

    def stamp_path(self):
        return os.path.join(self.base, LOG_DIR, STAMP_FILE)

    def tested(self, sha):
        try:
            with open(self.stamp_path(), encoding="utf-8") as f:
                return sha in (json.load(f) or {})
        except (OSError, ValueError, TypeError):
            return False

    def remember_tested(self, sha):
        data = {}
        try:
            with open(self.stamp_path(), encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
        data[sha] = self.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.stamp_path()), exist_ok=True)
        with open(self.stamp_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)

    def avoid_backup_window(self, step):
        """Wait until the backup task's minutes are past (or refuse with --no-wait)."""
        while True:
            m = self.now().minute
            window = next(((a, b) for a, b in BACKUP_WINDOWS if a <= m <= b), None)
            if window is None:
                return
            if self.a.no_wait:
                raise StepFailed(step, f"it is :{m:02d}, inside the backup task's window (:{window[0]:02d}-:{window[1]:02d}; "
                                 "it commits at :06 and :36) and --no-wait was given", step)
            self.say(f"    waiting: the backup task commits at :06 and :36 - carrying on at :{window[1] + 1:02d}")
            if self.dry:
                return
            self.sleep(30)

    def resolve_ref(self, step):
        b = self.a.branch
        if not b:
            raise StepFailed(step, "no --branch given", step)
        for ref in (f"refs/heads/{b}", f"refs/remotes/origin/{b}"):
            code, out = self.run(self.git("rev-parse", "--verify", "--quiet", ref + "^{commit}"), changes=False, quiet=True)
            if code == 0 and out.strip():
                return ref, out.strip().splitlines()[-1]
        raise StepFailed(step, f"no branch {b} here or on origin", step)

    def read_version(self, step, rev):
        code, out = self.run(self.git("show", f"{rev}:version.py"), changes=False, quiet=True)
        v = version_from_source(out) if code == 0 else None
        if not v:
            raise StepFailed(step, f"can't read VERSION from {rev}:version.py", step)
        return v

    # ---------- steps ----------
    def step_checks(self):
        if not self.is_windows:
            if not self.dry:
                raise StepFailed("checks", "this runs on Windows (the installer is built with PyInstaller and Inno Setup there)")
            self.say("    not Windows: allowed only because this is a dry run")
        if not os.path.isdir(os.path.join(self.base, ".git")) or \
                os.path.normcase(os.path.abspath(self.getcwd())) != os.path.normcase(os.path.abspath(self.base)):
            raise StepFailed("checks", f"run it from the commander_sim folder: cd \"{self.base}\"")
        out = self.must("checks", self.git("status", "--porcelain"), "git status failed", changes=False)
        if out.strip():
            raise StepFailed("checks", "the working tree isn't clean - commit or put away these first:\n" + out.strip())
        self.must("checks", ["gh", "auth", "status"], "gh isn't signed in: run `gh auth login`", changes=False)
        if not os.path.isdir(os.path.join(self.public_dir, ".git")):
            raise StepFailed("checks", f"the public folder {self.public_dir} isn't a git repository (--public-dir to change)")
        url = self.must("checks", self.git("remote", "get-url", "origin", cwd=self.public_dir),
                        "the public folder has no origin remote", changes=False).strip()
        if not re.search(r"github\.com[/:]" + re.escape(PUBLIC_REPO) + r"(?:\.git)?/?$", url, re.I):
            raise StepFailed("checks", f"the public folder's remote is {url}, not github.com/{PUBLIC_REPO}")
        self.avoid_backup_window("checks")
        self.say("    checks passed")

    def step_test(self):
        self.must("test", self.git("fetch", "origin"), "git fetch failed")
        self.ref, self.sha = self.resolve_ref("test")
        self.ver = self.read_version("test", self.sha)
        self.say(f"    {self.a.branch} = {self.sha[:9]}, version {self.ver}")
        if self.ref.startswith("refs/heads/"):
            self.must("test", self.git("checkout", self.a.branch), "checkout failed", resume="test")
        else:
            self.must("test", self.git("checkout", "-b", self.a.branch, "--track", f"origin/{self.a.branch}"),
                      "checkout failed", resume="test")
        if self.tested(self.sha):
            self.say(f"    quick_tests already passed for {self.sha[:9]} - not run again")
            return
        self.must("test", self.py("setup_forge.py"), "setup_forge.py failed", resume="test")
        out = self.must("test", self.py("forge_table.py", "--version"), "forge_table.py --version failed", resume="test")
        for line in out.strip().splitlines()[:3]:
            self.say("    " + line)
        self.must("test", self.py(os.path.join("tools", "quick_tests.py")), "quick_tests failed", resume="test")
        if not self.dry:
            self.remember_tested(self.sha)

    def step_merge(self):
        if self.sha is None:
            self.ref, self.sha = self.resolve_ref("merge")
        if not self.dry and not self.tested(self.sha):
            raise StepFailed("merge", f"{self.sha[:9]} hasn't passed quick_tests here", "test")
        self.avoid_backup_window("merge")
        self.must("merge", self.git("checkout", "main"), "checkout main failed", resume="merge")
        self.must("merge", self.git("pull", "--ff-only"), "git pull --ff-only failed", resume="merge")
        code, _ = self.run(self.git("merge-base", "--is-ancestor", "main", self.sha), changes=False, quiet=True)
        if code != 0:
            why = (f"main is not an ancestor of {self.sha[:9]}: not a fast-forward - refused "
                   "(rebase or merge the branch on main first)")
            if not self.dry:
                raise StepFailed("merge", why)
            self.say("    (today's local main) " + why)
        self.must("merge", self.git("merge", "--ff-only", self.sha), "git merge --ff-only failed", resume="merge")
        if not self.dry:
            head = self.must("merge", self.git("rev-parse", "HEAD"), "git rev-parse failed", changes=False).strip()
            if head != self.sha:
                raise StepFailed("merge", f"main is {head[:9]} after the merge, not {self.sha[:9]}", "merge")
        self.must("merge", self.git("push", "origin", "main"), "git push origin main failed", resume="merge")

    def step_version(self):
        # Round REL1B: runs before the merge, so it reads the commit being released, never the working tree or HEAD.
        if self.sha is None:
            self.ref, self.sha = self.resolve_ref("version")
        self.ver = self.read_version("version", self.sha)
        status, data = self.fetch(FEED_URL)
        if status == 404:
            self.say(f"    no release published yet: {self.ver} allowed")
            return
        if status != 200 or not isinstance(data, dict):
            raise StepFailed("version", f"couldn't read the published latest.json ({status}: {data})", "version")
        pub = str(data.get("version") or "")
        if not higher(self.ver, pub):
            raise StepFailed("version", f"{self.ver} is not higher than the published {pub} - raise version.py first")
        self.say(f"    {self.ver} > published {pub}")

    def step_build(self):
        self.ver = self.ver or self.read_version("build", "HEAD")
        self.must("build", self.py(os.path.join("tools", "build_installer.py"), "--test"), "build_installer failed",
                  resume="build")
        if self.dry:
            return
        for path in (self.installer(), self.installer() + ".sha256"):
            if not os.path.isfile(path):
                raise StepFailed("build", f"the build didn't make {path}", "build")

    def step_feed(self):
        self.ver = self.ver or self.read_version("feed", "HEAD")
        notes = " ".join((self.a.notes or "").split())
        if not notes:
            raise StepFailed("feed", "--notes is empty: write a line or two for testers", "feed")
        if len(notes) > NOTES_WARN:
            self.say(f"    WARNING: the notes are {len(notes)} characters; above {NOTES_WARN} they may need a 4th line "
                     "on the update card")
        self.must("feed", self.py(os.path.join("tools", "make_update_feed.py"), self.installer(), "--notes", notes),
                  "make_update_feed failed", resume="feed")
        if self.dry:
            return
        try:
            with open(self.feed_file(), encoding="utf-8") as f:
                feed = json.load(f)
        except (OSError, ValueError) as e:
            raise StepFailed("feed", f"latest.json wasn't written: {e}", "feed")
        if feed.get("version") != self.ver:
            raise StepFailed("feed", f"latest.json says {feed.get('version')}, not {self.ver}", "feed")

    def needs_xf(self):
        """The public repo holds tools/report_intake.py (BUG1); /MIR would delete it unless this release has it too."""
        return not os.path.isfile(os.path.join(self.base, "tools", "report_intake.py"))

    def step_public(self):
        self.ver = self.ver or self.read_version("public", "HEAD")
        code, out = self.run(self.py(os.path.join("tools", "export_public.py"), "--check"), changes=False)
        if code != 0 or "Would refuse" in out:
            refused = [l for l in out.splitlines() if "Would refuse" in l]
            raise StepFailed("public", "export_public --check refuses:\n" + "\n".join(refused or out.splitlines()[-5:]))
        dest = os.path.join(self.docs_dir, f"manticore_export_{self.ver}")
        if os.path.exists(dest):
            dest += "_" + self.now().strftime("%H%M%S")
        self.must("public", self.py(os.path.join("tools", "export_public.py"), dest), "export_public failed", resume="public")
        cmd = ["robocopy", dest, self.public_dir, "/MIR", "/XD", ".git", ".github"]
        if self.needs_xf():
            cmd += ["/XF", "report_intake.py"]
        code, out = self.run(cmd)
        if code not in ROBOCOPY_OK:
            raise StepFailed("public", f"robocopy failed (exit {code}; 8 or more is a failure)", "public")
        self.avoid_backup_window("public")
        self.must("public", self.git("add", "-A", cwd=self.public_dir), "git add failed", resume="public")
        code, out = self.run(self.git("status", "--porcelain", cwd=self.public_dir), changes=False, quiet=True)
        if code == 0 and not out.strip() and not self.dry:
            self.say("    the public folder already holds this source: nothing to commit")
        else:
            self.must("public", self.git("commit", "-m", f"Manticore {self.ver}", cwd=self.public_dir),
                      "git commit failed in the public folder", resume="public")
        self.must("public", self.git("push", cwd=self.public_dir), "git push failed in the public folder", resume="public")

    def step_publish(self):
        self.ver = self.ver or self.read_version("publish", "HEAD")
        tag = f"v{self.ver}"
        if not self.a.yes:
            if self.dry:
                self.say(f"    would ask: Publish Manticore {self.ver} to testers? [y/N]")
            else:
                answer = str(self.ask(f"Publish Manticore {self.ver} to testers? [y/N] ") or "").strip().lower()
                self.log_lines.append(f"    answer: {answer!r}")
                if answer not in ("y", "yes"):
                    raise StepFailed("publish", "not published (you said no) - nothing reached testers", "publish")
        code, _ = self.run(["gh", "release", "view", tag, "--repo", PUBLIC_REPO], changes=False, quiet=True)
        files = [self.installer(), self.feed_file()]
        if code == 0:
            self.say(f"    {tag} exists already: uploading the files to it")
            self.must("publish", ["gh", "release", "upload", tag] + files + ["--repo", PUBLIC_REPO, "--clobber"],
                      "gh release upload failed", resume="publish")
        else:
            notes = " ".join((self.a.notes or "").split()) or f"Manticore {self.ver}"
            self.must("publish", ["gh", "release", "create", tag] + files +
                      ["--repo", PUBLIC_REPO, "--title", f"Manticore {self.ver}", "--notes", notes, "--latest"],
                      "gh release create failed", resume="publish")

    def step_check(self):
        self.ver = self.ver or self.read_version("check", "HEAD")
        if self.dry:
            self.say(f"    would run: gh release view v{self.ver} --repo {PUBLIC_REPO}")
            self.say(f"    would read {FEED_URL}: version {self.ver} and the installer's sha256")
            return
        self.must("check", ["gh", "release", "view", f"v{self.ver}", "--repo", PUBLIC_REPO], "gh release view failed",
                  changes=False, resume="check")
        try:
            with open(self.installer() + ".sha256", encoding="utf-8") as f:
                want = f.read().split()[0].lower()
        except (OSError, IndexError):
            raise StepFailed("check", "can't read the installer's .sha256", "check")
        last = None
        for i in range(FEED_TRIES):
            status, data = self.fetch(FEED_URL)
            if status == 200 and isinstance(data, dict):
                last = data
                if data.get("version") == self.ver and str(data.get("sha256", "")).lower() == want:
                    self.say(f"    latest.json: {self.ver}, sha256 {want[:16]}... - testers will get it")
                    return
            if i + 1 < FEED_TRIES:
                self.sleep(10)
        raise StepFailed("check", f"the published latest.json doesn't match (got {last and last.get('version')}, "
                         f"sha256 {last and str(last.get('sha256'))[:16]}...)", "check")

    # ---------- driver ----------
    def main(self):
        start = STEPS.index(self.a.from_step) if self.a.from_step else 0
        self.say(f"Manticore release{' (DRY RUN: nothing will change)' if self.dry else ''}: branch {self.a.branch or '-'}")
        code = 0
        try:
            for i, name in enumerate(STEPS):
                if i < start and name != "checks":
                    self.say(f"[{i}] {name}: skipped (--from {self.a.from_step})")
                    continue
                self.say(f"[{i}] {name}")
                getattr(self, "step_" + name)()
            self.say(f"Done: Manticore {self.ver} {'would be ' if self.dry else ''}released.")
        except StepFailed as e:
            self.say(f"STOPPED at {e.step}: {e.why}")
            again = e.resume or e.step
            self.say(f"Fix it, then carry on with: python tools\\release.py --branch {self.a.branch or '<branch>'} "
                     f"--notes \"...\" --from {again}")
            code = 1
        except KeyboardInterrupt:
            self.say("STOPPED: interrupted")
            code = 130
        path = self.write_log()
        if path:
            self._say(f"(log: {path})")
        return code


def _quote(s):
    s = str(s)
    return f'"{s}"' if (" " in s or not s) else s


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Release Manticore to testers in one command (run on Windows, from commander_sim).")
    ap.add_argument("--branch", help="the release branch (local, or on origin)")
    ap.add_argument("--notes", default="", help="what's new, for testers (the update card and the GitHub release)")
    ap.add_argument("--from", dest="from_step", choices=STEPS, help="carry on from this step (checks always run)")
    ap.add_argument("--dry-run", action="store_true", help="print every command, change nothing")
    ap.add_argument("--yes", action="store_true", help="don't ask before publishing")
    ap.add_argument("--no-wait", action="store_true", help="refuse instead of waiting out the backup task's minutes")
    ap.add_argument("--public-dir", help="the public repository's folder (default Documents\\manticore_public)")
    a = ap.parse_args(argv)
    if not a.branch and (a.from_step is None or STEPS.index(a.from_step) <= STEPS.index("merge")):
        ap.error("--branch is needed up to the merge step")
    return a


def main(argv=None):
    return Release(parse_args(argv)).main()


if __name__ == "__main__":
    sys.exit(main())
