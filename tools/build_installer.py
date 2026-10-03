# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/build_installer.py - builds the Manticore installer for alpha testers (Round 29, SONNET_SPEC_R29 section 5).

Run on Windows, from the project folder (Python 3.14, the JDK, PyInstaller and Inno Setup installed - see requirements-build.txt):

    python tools\\build_installer.py --test       for the Windows Sandbox and your own PC; no public source link needed
    python tools\\build_installer.py --release    for friends; refuses unless everything below passes

Every step prints ONE line, "[n/9] what ... OK" or "... FAILED: why", and the build stops at the first failure:

  1. tools      Inno Setup (iscc) is found and PyInstaller is 6.22.x; if not, the install commands are printed;
  2. checks     forge_runtime/ is complete, the bridge jar matches java_bridge/src, the working tree is clean (--test only
                warns); --release also needs licenses/NOTICES.json -> project.source_url set (the GPL source offer, SOURCE_URL) and
                `build_notices.py --release` passing;
  3. jre        jlink makes build/jre (about 57 MB) from the JDK's Temurin 21, with the 13 modules jdeps named plus
                jdk.crypto.ec (Round MP1: online play's TLS key exchange and keytool's keys), and the JDK's legal/ folder is
                copied in unchanged;
  4. pyinstaller  commander_sim.spec -> dist/Manticore/ (Manticore.exe the game, Manticore-cli.exe for --version / --soak);
  5. runtime    forge_runtime/ and jre/ are copied into that folder (after PyInstaller: 37,000 card scripts through its analysis
                would be slow and pointless);
  6. identity   build_info.json (version, commit, tag, code, built_at, Forge, bridge stamp), README_FIRST.txt, and the alpha
                webhook copied as bug_report_config.bundled.json. The webhook file is COPIED AS BYTES: this script never reads,
                prints or logs its contents, and only says "bundled webhook: yes/no";
  7. check_dist tools\\check_dist.py on the folder (it runs both executables with PATH cut down to System32);
  8. inno       iscc installer\\commander_sim.iss -> installer_out\\Manticore-<version>-setup.exe and a .sha256 beside it;
  9. sandbox    installer\\sandbox\\sandbox_8gb.wsb and sandbox_4gb.wsb are written with this checkout's absolute paths (Windows
                Sandbox needs absolute host paths), then the installer size, the installed size and the file count are printed.

build/, dist/ and installer_out/ hold the bundled webhook: they are in .gitignore, backup.py's TOP_LEVEL_SKIP and export_public's
skip list, and must never be committed.

Sonnet's workspace cannot run PyInstaller for Windows or Inno Setup: the steps that need them were written and tested with mocks only.
"""
import argparse
import datetime
import glob
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
from xml.sax.saxutils import escape

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

APP = "Manticore"
PYINSTALLER_SERIES = "6.22"
JAVA_MAJOR = 21
# jdeps --multi-release 21 --ignore-missing-deps --print-module-deps forge.jar forge_bridge.jar (Opus, 1 Oct; UNVERIFIED on Windows)
# + jdk.crypto.ec (MP1, 2 Oct): jdeps doesn't name it (a service provider), but without it keytool can't make EC keys and TLS
#   falls back to slow finite-field key exchange. forge_net.ensure_host_cert also falls back to RSA, so an older build still works.
JLINK_MODULES = ("java.base,java.compiler,java.desktop,java.management,java.naming,java.net.http,java.rmi,java.scripting,"
                 "java.security.jgss,java.sql,jdk.crypto.ec,jdk.httpserver,jdk.sctp,jdk.unsupported")
COPYRIGHT = "Copyright (C) 2026 the Manticore contributors. Free software under the GNU GPL v3 or later."
STEP_TITLES = ["tools", "checks", "jre", "pyinstaller", "runtime", "identity", "check_dist", "inno", "sandbox"]
INSTALL_HELP = ("Install Inno Setup 6 once with:  winget install JRSoftware.InnoSetup\n"
                "and the build tools with:        python -m pip install -r requirements-build.txt")


class BuildError(Exception):
    """A step failed; the message is what the one-line report says."""


def _rmtree(path):
    """shutil.rmtree that also removes read-only files (Windows)."""
    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=onerror)


def file_count_and_size(folder):
    n = size = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            n += 1
            try:
                size += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return n, size


def version_parts(version):
    nums = [int(x) for x in re.findall(r"\d+", version)][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def version_resource_text(version):
    """The Windows version resource (Properties > Details of the .exe) in the form PyInstaller's `version=` reads."""
    v = version_parts(version)
    dotted = ".".join(str(x) for x in v)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', ''),
      StringStruct('FileDescription', '{APP}'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', '{APP}'),
      StringStruct('LegalCopyright', '{COPYRIGHT}'),
      StringStruct('OriginalFilename', '{APP}.exe'),
      StringStruct('ProductName', '{APP}'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def make_ico(png_path, ico_path, sizes=(256, 64, 48, 32, 16)):
    """assets/splash/icon.png -> a multi-size .ico (PNG-compressed entries), with pygame so the build needs no Pillow."""
    import pygame
    src = pygame.image.load(png_path)
    rgba = pygame.Surface((1, 1), pygame.SRCALPHA, 32)
    src = src.convert(rgba)
    images = []
    for s in sizes:
        buf = io.BytesIO()
        pygame.image.save(pygame.transform.smoothscale(src, (s, s)), buf, "icon.png")
        images.append((s, buf.getvalue()))
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    blobs = b""
    for s, data in images:
        out += struct.pack("<BBBBHHII", 0 if s >= 256 else s, 0 if s >= 256 else s, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    with open(ico_path, "wb") as f:
        f.write(out + blobs)
    return ico_path


def find_iscc(which=shutil.which, environ=None):
    environ = os.environ if environ is None else environ
    found = which("iscc") or which("ISCC.exe")
    if found:
        return found
    for base in (environ.get("ProgramFiles(x86)"), environ.get("ProgramFiles"),
                 os.path.join(environ.get("LOCALAPPDATA", ""), "Programs") if environ.get("LOCALAPPDATA") else None):
        if base:
            candidate = os.path.join(base, "Inno Setup 6", "ISCC.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def find_jlink(environ=None, which=shutil.which):
    environ = os.environ if environ is None else environ
    exe = "jlink.exe" if os.name == "nt" else "jlink"
    home = environ.get("JAVA_HOME")
    if home and os.path.isfile(os.path.join(home, "bin", exe)):
        return os.path.join(home, "bin", exe)
    return which("jlink")


class Builder:
    def __init__(self, release, base=BASE_DIR, run=subprocess.run, which=shutil.which, environ=None, say=print):
        self.release = release
        self.base = base
        self.run = run
        self.which = which
        self.environ = dict(os.environ if environ is None else environ)
        self.say = say
        self.notes = []
        self.build_dir = os.path.join(base, "build")
        self.dist_dir = os.path.join(base, "dist", APP)
        self.out_dir = os.path.join(base, "installer_out")
        self.jre_dir = os.path.join(self.build_dir, "jre")
        self.iscc = None
        self.version = None
        self.installer_path = None

    # ---- helpers -------------------------------------------------------------------------------------------------------
    def _run(self, cmd, **kw):
        try:
            return self.run(cmd, capture_output=True, text=True, **kw)
        except OSError as e:
            raise BuildError(f"could not start {os.path.basename(str(cmd[0]))}: {e}")

    def _version(self):
        if self.version is None:
            import version
            self.version = version.VERSION
        return self.version

    # ---- 1. tools ------------------------------------------------------------------------------------------------------
    def check_tools(self):
        self.iscc = find_iscc(self.which, self.environ)
        if not self.iscc:
            raise BuildError("Inno Setup (iscc) was not found. " + INSTALL_HELP)
        proc = self._run([sys.executable, "-m", "PyInstaller", "--version"])
        got = (proc.stdout or "").strip()
        if proc.returncode != 0 or not got.startswith(PYINSTALLER_SERIES):
            raise BuildError(f"PyInstaller {PYINSTALLER_SERIES}.x is needed (found {got or 'none'}). " + INSTALL_HELP)
        return f"iscc found, PyInstaller {got}"

    # ---- 2. checks -----------------------------------------------------------------------------------------------------
    def check_sources(self):
        import forge_client as fc
        import setup_forge
        problem = fc.runtime_problem()
        if problem:
            raise BuildError(problem)
        stamp, want = fc.bridge_stamp(), setup_forge.bridge_source_hash()[:12]
        if stamp != want:
            raise BuildError(f"the bridge jar in forge_runtime/ ({stamp or 'no stamp'}) does not match java_bridge/src ({want}): "
                             "run  python setup_forge.py")
        dirty = ""
        proc = self._run(["git", "status", "--porcelain"], cwd=self.base)
        if proc.returncode == 0:
            dirty = (proc.stdout or "").strip()
        elif self.release:
            raise BuildError("git status failed, so the working tree could not be checked")
        if dirty:
            n = len(dirty.splitlines())
            if self.release:
                raise BuildError(f"the working tree is not clean ({n} changed file(s)): commit first, a friends build must be a commit")
            self.notes.append(f"WARNING: the working tree is not clean ({n} changed file(s)); this test build is not exactly any commit")
        if self.release:
            with open(os.path.join(self.base, "licenses", "NOTICES.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            if not str((manifest.get("project") or {}).get("source_url") or "").strip():
                raise BuildError("SOURCE_URL is not set (licenses/NOTICES.json -> project.source_url): a friends build must offer "
                                 "the public source link (GPL). Use --test until it exists")
            proc = self._run([sys.executable, os.path.join(self.base, "tools", "build_notices.py"), "--release"], cwd=self.base)
            if proc.returncode != 0:
                first = ((proc.stderr or proc.stdout or "").strip().splitlines() or ["no output"])[0]
                raise BuildError(f"build_notices.py --release failed: {first}")
        else:
            self.notes.append("test build: SOURCE_URL is not checked; README_FIRST.txt says the source link is not set yet")
        return "forge_runtime complete, bridge jar matches" + ("" if not dirty else ", tree not clean (warning)")

    # ---- 3. jre --------------------------------------------------------------------------------------------------------
    def make_jre(self):
        jlink = find_jlink(self.environ, self.which)
        if not jlink:
            raise BuildError("jlink was not found: set JAVA_HOME to the Temurin 21 JDK (not a JRE), or put its bin\\ on PATH")
        jdk = os.path.dirname(os.path.dirname(os.path.abspath(jlink)))
        ver = self._run([jlink, "--version"])
        text = (ver.stdout or ver.stderr or "").strip()
        if not re.match(rf"{JAVA_MAJOR}(\.|$)", text):
            raise BuildError(f"jlink is version {text or '?'}, but the alpha must run on Java {JAVA_MAJOR}: use the Temurin {JAVA_MAJOR} JDK")
        _rmtree(self.jre_dir)                            # jlink refuses to write into an existing folder
        os.makedirs(self.build_dir, exist_ok=True)
        proc = self._run([jlink, "--module-path", os.path.join(jdk, "jmods"), "--add-modules", JLINK_MODULES, "--strip-debug",
                          "--no-header-files", "--no-man-pages", "--compress=zip-6", "--output", self.jre_dir])
        if proc.returncode != 0:
            raise BuildError("jlink failed: " + ((proc.stderr or proc.stdout or "").strip().splitlines() or ["no output"])[-1])
        legal = os.path.join(jdk, "legal")
        if os.path.isdir(legal):
            shutil.copytree(legal, os.path.join(self.jre_dir, "legal"), dirs_exist_ok=True)     # unchanged, as the licence asks
        else:
            raise BuildError(f"the JDK has no legal\\ folder ({legal}); Temurin's licence texts must ship with the runtime")
        exe = "java.exe" if os.name == "nt" else "java"
        if not os.path.isfile(os.path.join(self.jre_dir, "bin", exe)):
            raise BuildError("jlink ran but jre\\bin\\java was not made")
        n, size = file_count_and_size(self.jre_dir)
        return f"{size / (1024 * 1024):.0f} MB, {n} files"

    # ---- 4. pyinstaller ------------------------------------------------------------------------------------------------
    def run_pyinstaller(self):
        os.makedirs(self.build_dir, exist_ok=True)
        env = dict(self.environ)
        icon = os.path.join(self.base, "assets", "splash", "icon.png")
        if os.path.isfile(icon):
            env["MANTICORE_BUILD_ICON"] = make_ico(icon, os.path.join(self.build_dir, "manticore.ico"))
        version_file = os.path.join(self.build_dir, "version_info.txt")
        with open(version_file, "w", encoding="utf-8") as f:
            f.write(version_resource_text(self._version()))
        env["MANTICORE_BUILD_VERSION_FILE"] = version_file
        work = os.path.join(self.build_dir, "pyinstaller")
        proc = self._run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", os.path.dirname(self.dist_dir),
                          "--workpath", work, os.path.join(self.base, "commander_sim.spec")], cwd=self.base, env=env)
        log = os.path.join(self.build_dir, "pyinstaller.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write((proc.stdout or "") + "\n" + (proc.stderr or ""))
        if proc.returncode != 0:
            tail = ((proc.stderr or proc.stdout or "").strip().splitlines() or ["no output"])[-1]
            raise BuildError(f"PyInstaller failed ({tail}); full output in {log}")
        for name in (f"{APP}.exe", f"{APP}-cli.exe"):
            if not os.path.isfile(os.path.join(self.dist_dir, name)) and os.name == "nt":
                raise BuildError(f"PyInstaller finished but {name} is missing from {self.dist_dir}")
        warn = os.path.join(work, "commander_sim", "warn-commander_sim.txt")
        if os.path.isfile(warn):
            with open(warn, "r", encoding="utf-8", errors="replace") as f:
                missing = sum(1 for line in f if "missing module" in line.lower())
            self.notes.append(f"PyInstaller's warnings file ({missing} 'missing module' lines; the optional/platform ones are normal) is {warn}")
        return f"{APP}.exe and {APP}-cli.exe"

    # ---- 5. runtime ----------------------------------------------------------------------------------------------------
    def add_runtime(self):
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        for src, name in ((os.path.join(self.base, "forge_runtime"), "forge_runtime"), (self.jre_dir, "jre")):
            dest = os.path.join(self.dist_dir, name)
            _rmtree(dest)
            shutil.copytree(src, dest, ignore=ignore)
        n, size = file_count_and_size(self.dist_dir)
        return f"{n} files, {size / (1024 * 1024):.0f} MB in the build folder"

    # ---- 6. identity ---------------------------------------------------------------------------------------------------
    def write_identity(self):
        import forge_client as fc
        import version
        info = {"version": version.VERSION, "commit": version.commit(), "tag": version.tag(), "code": version.code_fingerprint(),
                "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "forge": version.forge_build(), "bridge_stamp": fc.bridge_stamp()}
        with open(os.path.join(self.dist_dir, "build_info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
            f.write("\n")
        src = os.path.join(self.base, "bug_report_config.json")
        bundled = os.path.isfile(src)
        if bundled:                                     # bytes only: never read, parse, print or log the address
            shutil.copyfile(src, os.path.join(self.dist_dir, "bug_report_config.bundled.json"))
        with open(os.path.join(self.dist_dir, "README_FIRST.txt"), "w", encoding="utf-8") as f:
            f.write(readme_first(info["version"], self._source_url()))
        return f"build_info.json, README_FIRST.txt; bundled webhook: {'yes' if bundled else 'no'}"

    def _source_url(self):
        try:
            with open(os.path.join(self.base, "licenses", "NOTICES.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            return str((manifest.get("project") or {}).get("source_url") or "").strip()
        except (OSError, ValueError):
            return ""

    # ---- 7. check_dist -------------------------------------------------------------------------------------------------
    def run_check_dist(self):
        from tools import check_dist
        decks = len(glob.glob(os.path.join(self.base, "sample_decks", "*.txt")))
        res = check_dist.check_dist(self.dist_dir, run=True, release=self.release, exe=".exe" if os.name == "nt" else "",
                                    sample_decks=decks, natives_file=os.path.join(self.build_dir, "native_libraries_found.txt"))
        self.notes += res.notes
        for line in res.todo:
            self.notes.append(("FAIL: " if self.release else "TO DO: ") + line)
        bad = res.blocking(release=self.release)
        if bad:
            raise BuildError("; ".join(bad[:3]) + (f" (and {len(bad) - 3} more)" if len(bad) > 3 else ""))
        return "layout, licences, secrets, --version and one game all pass"

    # ---- 8. inno -------------------------------------------------------------------------------------------------------
    def run_inno(self):
        os.makedirs(self.out_dir, exist_ok=True)
        cmd = [self.iscc, f"/DMyAppVersion={self._version()}", f"/DSourceDir={self.dist_dir}", f"/DOutputDir={self.out_dir}"]
        ico = os.path.join(self.build_dir, "manticore.ico")
        if os.path.isfile(ico):
            cmd.append(f"/DIconFile={ico}")
        if not self.release:
            cmd.append("/DTestBuild=1")
        cmd.append(os.path.join(self.base, "installer", "commander_sim.iss"))
        proc = self._run(cmd, cwd=self.base)
        if proc.returncode != 0:
            tail = ((proc.stdout or "").strip().splitlines() or ["no output"])[-1]
            raise BuildError(f"Inno Setup failed ({tail})")
        self.installer_path = os.path.join(self.out_dir, f"{APP}-{self._version()}-setup.exe")
        if not os.path.isfile(self.installer_path):
            raise BuildError(f"Inno Setup finished but {os.path.basename(self.installer_path)} is not in {self.out_dir}")
        h = hashlib.sha256()
        with open(self.installer_path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        with open(self.installer_path + ".sha256", "w", encoding="utf-8") as f:
            f.write(f"{h.hexdigest()} *{os.path.basename(self.installer_path)}\n")
        return os.path.basename(self.installer_path)

    # ---- 9. sandbox + the numbers --------------------------------------------------------------------------------------
    def write_sandbox_files(self):
        sandbox = os.path.join(self.base, "installer", "sandbox")
        scripts = os.path.join(sandbox, "scripts")
        results = os.path.join(sandbox, "results")
        os.makedirs(results, exist_ok=True)
        with open(os.path.join(scripts, "sandbox_test.wsb.template"), "r", encoding="utf-8") as f:
            template = f.read()
        written = []
        for name, mb in (("sandbox_8gb.wsb", 8192), ("sandbox_4gb.wsb", 4096), ("sandbox_test.wsb", 8192)):
            text = (template.replace("@@MEMORY@@", str(mb)).replace("@@INSTALLER_OUT@@", escape(self.out_dir))
                    .replace("@@SCRIPTS@@", escape(scripts)).replace("@@RESULTS@@", escape(results)))
            with open(os.path.join(sandbox, name), "w", encoding="utf-8") as f:
                f.write(text)
            written.append(name)
        n, installed = file_count_and_size(self.dist_dir)
        size = os.path.getsize(self.installer_path) if self.installer_path and os.path.isfile(self.installer_path) else 0
        return (f"{', '.join(written[:2])}; installer {size / (1024 * 1024):.0f} MB, installed {installed / (1024 * 1024):.0f} MB, "
                f"{n} files")


def readme_first(version, source_url):
    source = (f"Source code: {source_url}" if source_url else
              "Source code: TEST BUILD - the public source link is not set yet. It must be set before this goes to friends.")
    return f"""{APP} {version} - alpha test build
{'=' * (len(APP) + len(version) + 20)}

Thank you for testing. {APP} is a free Magic: The Gathering playtester with AI opponents. Nothing here is for sale.

Playing
  Start {APP} from the Start menu. Pick a deck on the deck screen. Java and Forge (the rules engine) are inside this folder:
  there is nothing else to install.

Where your things are kept
  Your decks, settings and saved games:       %APPDATA%\\{APP}
  The card picture cache, logs and reports:   %LOCALAPPDATA%\\{APP}
  Uninstalling keeps both unless you say yes to deleting them. Forge's own folder (%APPDATA%\\Forge) is never touched.

If something goes wrong
  Press F8 in the game to send a bug report, or use Cog > Licenses and credits for the legal notices.
  Windows may say "Windows protected your PC" when you run the installer (it is not code-signed yet during the alpha):
  choose "More info", then "Run anyway".

Licence
  {APP} is free software under the GNU General Public License v3 or later; Forge is GPL v3 too. See LICENSE and
  THIRD_PARTY_NOTICES.txt in this folder.
  {source}
"""


def run_steps(builder, say=print):
    """Runs the nine steps, printing one line each. Returns 0 when all passed, 1 at the first failure."""
    steps = [builder.check_tools, builder.check_sources, builder.make_jre, builder.run_pyinstaller, builder.add_runtime,
             builder.write_identity, builder.run_check_dist, builder.run_inno, builder.write_sandbox_files]
    total = len(steps)
    for i, (fn, title) in enumerate(zip(steps, STEP_TITLES), 1):
        say(f"[{i}/{total}] {title} ...", end=" ", flush=True)
        try:
            detail = fn()
        except BuildError as e:
            say(f"FAILED: {e}")
            for note in builder.notes:
                say("      " + note)
            return 1
        say(f"OK ({detail})" if detail else "OK")
    for note in builder.notes:
        say("  note: " + note)
    say(f"Done: {builder.installer_path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Manticore installer (Windows).")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", action="store_true", help="a test build for the Sandbox and your own PC (no source URL needed)")
    mode.add_argument("--release", action="store_true", help="a build for friends: refuses unless every check passes")
    args = ap.parse_args(argv)
    return run_steps(Builder(release=args.release))


if __name__ == "__main__":
    sys.exit(main())
