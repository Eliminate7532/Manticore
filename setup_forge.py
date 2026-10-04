# SPDX-License-Identifier: GPL-3.0-or-later
"""
setup_forge.py - installs the Forge rules engine next to this program.

    python setup_forge.py            install (or repair) from the forge_bundle folder
    python setup_forge.py --check    install if needed, then start a short real game to prove it works
    python setup_forge.py --force    reinstall even if everything looks fine

What it does: checks that the pieces in forge_bundle/ are complete (sizes and SHA-256 checksums), unpacks them
into forge_runtime/ (forge.jar plus Forge's card scripts and data), puts the small forge_bridge.jar (built from
java_bridge/src) beside them, and checks that Java 17 or newer is available. Nothing is downloaded and nothing
outside this folder is touched.

Forge (https://github.com/Card-Forge/forge) is free software under the GNU GPL v3; see forge_bundle/FORGE_LICENSE.txt.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time

import forge_client as fc

BASE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(BASE, "forge_bundle")
MANIFEST = os.path.join(BUNDLE, "forge_runtime.manifest.json")
RUNTIME = os.path.join(BASE, "forge_runtime")
BRIDGE_JAR = os.path.join(BASE, "java_bridge", "forge_bridge.jar")
BRIDGE_SRC = os.path.join(BASE, "java_bridge", "src")

JAVA_HELP = (
    "Java 17 or newer is needed to run Forge, and it was not found.\n"
    "  Windows:  open a terminal and run   winget install EclipseAdoptium.Temurin.21.JRE\n"
    "            (or download the JRE from https://adoptium.net), then open a NEW terminal window.\n"
    "  Then run this script again if it stopped, or just start the game."
)


class SetupError(Exception):
    pass


def say(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------------------------------------
# the bundle
# ---------------------------------------------------------------------------------------------

def read_manifest(path=None):
    path = path or MANIFEST
    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except OSError:
        raise SetupError(f"The Forge bundle is missing: {path} was not found. The forge_bundle folder must sit next to "
                         "this script (it holds the manifest and the forge_runtime.tar.xz.part* files).")
    except ValueError as e:
        raise SetupError(f"{path} is damaged ({e}).")
    if not manifest.get("parts"):
        raise SetupError(f"{path} lists no parts.")
    return manifest


def sha256_of(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(block)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


def verify_parts(manifest, bundle=None):
    """Return the part paths in order; raise SetupError naming the first missing or damaged part."""
    bundle = bundle or BUNDLE
    paths = []
    for part in manifest["parts"]:
        path = os.path.join(bundle, part["name"])
        if not os.path.isfile(path):
            raise SetupError(f"Part {part['name']} is missing from {bundle}. Copy all the forge_runtime.tar.xz.part* "
                             "files into that folder.")
        size = os.path.getsize(path)
        if size != part["size"]:
            raise SetupError(f"Part {part['name']} is {size} bytes but should be {part['size']}: the copy was cut off "
                             "or changed. Copy it again.")
        if sha256_of(path) != part["sha256"]:
            raise SetupError(f"Part {part['name']} has the right size but the wrong checksum: the copy is damaged. "
                             "Copy it again.")
        paths.append(path)
    return paths


class PartsReader:
    """The parts read back to back as one file, hashing what passes through."""

    def __init__(self, paths):
        self.paths = list(paths)
        self.f = None
        self.hash = hashlib.sha256()
        self.total = 0

    def read(self, n=-1):
        out = b""
        while n < 0 or len(out) < n:
            if self.f is None:
                if not self.paths:
                    break
                self.f = open(self.paths.pop(0), "rb")
            want = -1 if n < 0 else n - len(out)
            chunk = self.f.read(want)
            if not chunk:
                self.f.close()
                self.f = None
                continue
            out += chunk
        self.hash.update(out)
        self.total += len(out)
        return out


def safe_extract(tar, dest):
    """Extract a stream-mode tar into dest, refusing anything that would land outside it."""
    root = os.path.realpath(dest)
    count = 0
    for member in tar:
        target = os.path.realpath(os.path.join(dest, member.name))
        if target != root and not target.startswith(root + os.sep):
            raise SetupError(f"The bundle contains an unsafe path ({member.name}); refusing to unpack it.")
        if not (member.isfile() or member.isdir()):
            continue                                            # no links or devices in a Forge bundle
        try:
            tar.extract(member, dest, filter="data")
        except TypeError:                                       # Python without extraction filters
            tar.extract(member, dest)
        count += 1
        if count % 5000 == 0:
            say(f"  ... {count} files")
    return count


def install(manifest, force=False):
    say(f"Checking the Forge bundle ({len(manifest['parts'])} parts) ...")
    paths = verify_parts(manifest)
    tmp = RUNTIME + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    say("Unpacking Forge (this takes about a minute) ...")
    reader = PartsReader(paths)
    try:
        with tarfile.open(fileobj=reader, mode="r|xz") as tar:
            files = safe_extract(tar, tmp)
        while reader.read(1 << 20):                              # drain so the checksum covers the whole stream
            pass
    except (tarfile.TarError, EOFError, OSError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SetupError(f"Could not unpack the bundle ({e}).")
    except SetupError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if reader.hash.hexdigest() != manifest["tar_sha256"]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SetupError("The unpacked bundle does not match its checksum. Copy the forge_bundle folder again.")
    say(f"  {files} files unpacked.")
    old = RUNTIME + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(RUNTIME):
        os.replace(RUNTIME, old)
    os.replace(tmp, RUNTIME)
    shutil.rmtree(old, ignore_errors=True)


def install_bridge(rebuild=False):
    """Put forge_bridge.jar into the runtime folder (compile it from source if needed and possible)."""
    if rebuild or not os.path.isfile(BRIDGE_JAR):
        build_bridge()
    if not os.path.isfile(BRIDGE_JAR):
        raise SetupError(f"{BRIDGE_JAR} is missing and could not be built.")
    shutil.copy2(BRIDGE_JAR, os.path.join(RUNTIME, "forge_bridge.jar"))


BRIDGE_STAMP = "bridge_source.sha256"


def bridge_source_hash(src=None):
    """SHA-256 over every .java file under java_bridge/src (relative path + text, line endings made \\n so a Windows
    checkout gives the same answer). build_bridge() stores it in the jar as BRIDGE_STAMP; tests/test_round27c.py checks
    that the jar's stamp matches the sources, so a Java change without a rebuild fails the tests (round 27c: an old jar
    without the surveil card was shipped that way)."""
    src = src or BRIDGE_SRC
    h = hashlib.sha256()
    files = sorted(os.path.relpath(os.path.join(dp, f), src).replace(os.sep, "/")
                   for dp, _d, fs in os.walk(src) for f in fs if f.endswith(".java"))
    for rel in files:
        with open(os.path.join(src, rel), "rb") as f:
            data = f.read().replace(b"\r\n", b"\n")
        h.update(rel.encode("utf-8") + b"\0" + data + b"\0")
    return h.hexdigest()


def build_bridge():
    """Compile java_bridge/src against forge.jar. Needs a JDK (javac and jar), not only a JRE."""
    javac, jar = shutil.which("javac"), shutil.which("jar")
    if not javac or not jar or not os.path.isdir(BRIDGE_SRC):
        say("  (no JDK found, so the bridge cannot be compiled here)")
        return
    out = os.path.join(BASE, "java_bridge", "out")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    sources = [os.path.join(dp, f) for dp, _d, fs in os.walk(BRIDGE_SRC) for f in fs if f.endswith(".java")]
    cp = os.path.join(RUNTIME, "forge.jar")
    r = subprocess.run([javac, "--release", "17", "-encoding", "UTF-8", "-cp", cp, "-d", out] + sources, capture_output=True, text=True)
    if r.returncode != 0:
        raise SetupError("Compiling the Java bridge failed:\n" + r.stderr[-2000:])
    with open(os.path.join(out, BRIDGE_STAMP), "w", encoding="ascii") as f:
        f.write(bridge_source_hash() + "\n")
    r = subprocess.run([jar, "cf", BRIDGE_JAR, "-C", out, "."], capture_output=True, text=True)
    if r.returncode != 0:
        raise SetupError("Packing forge_bridge.jar failed:\n" + r.stderr[-2000:])
    shutil.rmtree(out, ignore_errors=True)
    say("  forge_bridge.jar compiled from source.")


def write_notes(manifest):
    lic = os.path.join(BUNDLE, "FORGE_LICENSE.txt")
    if os.path.isfile(lic):
        shutil.copy2(lic, os.path.join(RUNTIME, "FORGE_LICENSE.txt"))
    with open(os.path.join(RUNTIME, "VERSION.txt"), "w", encoding="utf-8") as f:
        f.write(f"Forge {manifest.get('forge_version', '?')} (commit {manifest.get('forge_commit', '?')})\n"
                "Free software under the GNU General Public License v3. Source: https://github.com/Card-Forge/forge\n"
                "Installed by setup_forge.py from forge_bundle/.\n")


# ---------------------------------------------------------------------------------------------
# checking that it really works
# ---------------------------------------------------------------------------------------------

def smoke_test(seconds=150):
    """Start a real two-player game and wait for the first snapshot. Returns (ok, message)."""
    from deck_loader import load_deck
    sample = os.path.join(BASE, "sample_decks", "stompy_goreclaw.txt")      # patch 38 (was the Kinnan sample)
    commanders, deck = load_deck(sample)
    path = fc.write_deck_file(os.path.join(BASE, "forge_decks", "check.dck"), commanders, deck, "Check", RUNTIME)
    session = fc.ForgeSession(path, [path], name="Check", seed=1, runtime=RUNTIME)
    t0 = time.time()
    try:
        session.start()
        while time.time() - t0 < seconds:
            session.poll()
            if session.state and session.state.get("players"):
                return True, f"Forge started a game in {time.time() - t0:.0f} seconds."
            if session.fatal or session.exited:
                return False, (session.fatal or "Forge stopped while starting") + f"  (see {session.stderr_path})"
            time.sleep(0.1)
        return False, f"Forge did not produce a game within {seconds} seconds (see {session.stderr_path})."
    finally:
        session.close()


# ---------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Install the Forge rules engine for the Commander table.")
    ap.add_argument("--check", action="store_true", help="also start a short real game as a test")
    ap.add_argument("--force", action="store_true", help="unpack again even if the runtime looks complete")
    ap.add_argument("--rebuild-bridge", action="store_true", help="compile java_bridge/src again (needs a JDK)")
    args = ap.parse_args(argv)
    try:
        java = fc.find_java()
        if java:
            say(f"Java: found ({java})")
        complete = all(os.path.isfile(os.path.join(RUNTIME, n)) for n in ("forge.jar", "forge_bridge.jar")) and \
            os.path.isdir(os.path.join(RUNTIME, "res", "cardsfolder"))
        outdated = fc.runtime_outdated(RUNTIME, MANIFEST) if complete else None      # Round FB1
        if outdated:
            say(f"forge_runtime/ holds Forge {outdated[0]}; forge_bundle/ has Forge {outdated[1]}. Updating ...")
        if complete and not outdated and not args.force:
            say("Forge is already installed in forge_runtime/.")
            if args.rebuild_bridge:
                install_bridge(True)
            elif fc.sync_bridge(RUNTIME, BRIDGE_JAR):
                say("Updated forge_bridge.jar in forge_runtime/ to the version in java_bridge/.")
        else:
            manifest = read_manifest()
            install(manifest, args.force)
            install_bridge(args.rebuild_bridge)
            write_notes(manifest)
            say(f"Forge {manifest.get('forge_version', '')} installed in forge_runtime/.")
        problem = fc.runtime_problem(RUNTIME)
        if problem:
            say("")
            say(JAVA_HELP if "Java" in problem else problem)
            return 1
        if args.check:
            say("Starting a test game ...")
            ok, message = smoke_test()
            say(("OK: " if ok else "PROBLEM: ") + message)
            return 0 if ok else 1
        say("Ready. Start the game with:  python forge_table.py")
        return 0
    except SetupError as e:
        say(f"\nSetup problem: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
