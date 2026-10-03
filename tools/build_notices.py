# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/build_notices.py - generates THIRD_PARTY_NOTICES.txt from licenses/NOTICES.json, and checks that the
manifest actually covers everything the program ships. This is the one program that turns the manifest into
the plain-text file for people who would rather read a file than open Cog > Licenses and credits (which reads
the same manifest, in licenses_view.py) - the manifest is the single source of truth for both.

    python tools/build_notices.py                write THIRD_PARTY_NOTICES.txt
    python tools/build_notices.py --check         exit 1 if the file on disk is out of date, print nothing changed
    python tools/build_notices.py --release       also fail while any manifest entry is not yet "verified", or a
                                                   coverage check below finds something the manifest doesn't mention

No pygame import here on purpose: this has to run in CI and from a plain terminal with nothing installed but the
standard library, well before anyone has run `pip install -r requirements.txt`.
"""
import argparse
import json
import os
import re
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LICENSES_DIR = os.path.join(BASE_DIR, "licenses")
MANIFEST_PATH = os.path.join(LICENSES_DIR, "NOTICES.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "THIRD_PARTY_NOTICES.txt")
REQUIREMENTS_PATH = os.path.join(BASE_DIR, "requirements.txt")
FORGE_JAR = os.path.join(BASE_DIR, "forge_runtime", "forge.jar")

_SPDX_SPLIT = re.compile(r"\s+(?:OR|AND|WITH)\s+")
_PKG_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*==")


# "attribution": no licence can be named (NOASSERTION - e.g. AI-generated art that may carry no copyright), but what the
# source's terms require has been done; the entry's "attribution" field says what and where. --release accepts it like
# "verified" (Karl, 2026-10-02: the Grok art, credited "Created with Grok" in Licenses and credits).
RELEASE_OK = ("verified", "attribution")
NOT_ASSERTED = "NOASSERTION"        # SPDX's "no licence claimed". Not a licence id: allowed only while the entry is not "verified", needs no text file


def license_ids(expr):
    if not expr:
        return []
    cleaned = expr.replace("(", " ").replace(")", " ")
    parts = [p.strip() for p in _SPDX_SPLIT.split(cleaned) if p.strip()]
    return parts or [expr.strip()]


def load_manifest():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _license_text(spdx_id):
    text = _read(os.path.join(LICENSES_DIR, "texts", f"{spdx_id}.txt"))
    return text if text is not None else f"[MISSING: licenses/texts/{spdx_id}.txt]"


def _notice_texts(notice_file):
    if not notice_file:
        return []
    files = notice_file if isinstance(notice_file, list) else [notice_file]
    out = []
    for rel in files:
        text = _read(os.path.join(LICENSES_DIR, rel))
        out.append((rel, text if text is not None else f"[MISSING: licenses/{rel}]"))
    return out


def _rule(char="="):
    return char * 78


def _component_block(comp, is_java=False):
    lines = [_rule("-"), comp.get("name", comp.get("id", "?"))]
    if comp.get("version"):
        lines.append(f"Version: {comp['version']}")
    if is_java and comp.get("artifacts"):
        lines.append("Maven artifacts: " + ", ".join(comp["artifacts"]))
    lines.append(f"License: {comp.get('license', '?')}")
    if comp.get("copyright"):
        lines.append(comp["copyright"])
    if comp.get("url"):
        lines.append(comp["url"])
    if comp.get("attribution"):
        lines.append("")
        lines.append("Attribution: " + comp["attribution"])
    if comp.get("note"):
        lines.append("")
        lines.append("Note: " + comp["note"])
    for spdx in license_ids(comp.get("license", "")):
        if spdx == NOT_ASSERTED:                     # no licence is claimed, so there is no text to print (see validate_manifest)
            lines.append("")
            if comp.get("status") == "attribution":
                lines.append("No licence to name; the attribution the source requires is given (see above).")
            else:
                lines.append(f"Licence not established (status: {comp.get('status', 'not_collected')}).")
            continue
        lines.append("")
        lines.append(f"--- {spdx} license text ---")
        lines.append("")
        lines.append(_license_text(spdx))
    for rel, text in _notice_texts(comp.get("notice_file")):
        lines.append("")
        lines.append(f"--- NOTICE ({rel}) ---")
        lines.append("")
        lines.append(text)
    lines.append("")
    return "\n".join(lines)


def generate_notices_text(manifest):
    project = manifest.get("project", {})
    out = [_rule(), project.get("name", "Manticore") + " - third-party notices", _rule(), "",
           project.get("copyright", ""), "", project.get("notice", ""), ""]
    out.append(_rule())
    out.append("Wizards of the Coast, Scryfall, card art, deck sites")
    out.append(_rule())
    out.append("")
    for c in manifest.get("credits", []):
        out.append(c.get("title", ""))
        out.append(c.get("text", ""))
        out.append("")
    out.append(_rule())
    out.append("Third-party software")
    out.append(_rule())
    out.append("")
    for c in manifest.get("components", []):
        out.append(_component_block(c, is_java=False))
    java = manifest.get("java_components", {})
    for key in sorted(java, key=lambda k: java[k].get("name", k).lower()):
        c = dict(java[key], id=key)
        out.append(_component_block(c, is_java=True))
    natives = manifest.get("native_libraries", {})
    if natives.get("_status") or natives.get("libraries"):
        out.append(_rule("-"))
        out.append("Native libraries (pygame)")
        if natives.get("_status"):
            out.append("")
            out.append(natives["_status"])
        for lib in natives.get("libraries") or []:
            out.append(_component_block(lib, is_java=False))
    return "\n".join(out).rstrip() + "\n"


# ---- coverage checks (only meaningful with the runtime/requirements present; used by --release) ------------

def jar_maven_artifacts(jar_path):
    """{'groupId:artifactId': 'version', ...} read from every META-INF/maven/*/pom.properties in the jar."""
    out = {}
    with zipfile.ZipFile(jar_path) as zf:
        for name in zf.namelist():
            if name.startswith("META-INF/maven/") and name.endswith("pom.properties"):
                props = {}
                with zf.open(name) as f:
                    for raw in f.read().decode("utf-8", "replace").splitlines():
                        if "=" in raw and not raw.startswith("#"):
                            k, v = raw.split("=", 1)
                            props[k.strip()] = v.strip()
                if "groupId" in props and "artifactId" in props:
                    out[f"{props['groupId']}:{props['artifactId']}"] = props.get("version", "")
    return out


FORGE_GROUP = "forge"


def is_forge_module(group_artifact, version, manifest):
    """Forge's own modules inside forge.jar (forge:forge-core, forge-game, forge-ai, forge-gui, forge-gui-desktop) are the
    "forge" component itself (GPL-3.0-or-later, in components), not third-party libraries, so java_components never listed
    them and every --release with a real forge.jar present named all five. Covered when the component's version starts with
    the module's version ("2.0.15-SNAPSHOT (commit 3a74143...)"): a different Forge build is still reported."""
    if group_artifact.split(":", 1)[0] != FORGE_GROUP:
        return False
    forge = next((c for c in manifest.get("components", []) if c.get("id") == "forge"), None)
    return bool(forge and version and str(forge.get("version", "")).startswith(version))


def check_jar_coverage(manifest):
    """[problem strings]. Empty means every artifact in forge.jar is covered by a java_components entry. The
    manifest's artifacts are listed as 'artifactId:version' (no groupId - see NOTICES.json); the jar's own
    pom.properties files give the same 'artifactId:version' pairs, keyed there by groupId:artifactId."""
    if not os.path.isfile(FORGE_JAR):
        return []          # not present in this checkout - can't check, and that's fine (spec: skip without the jar)
    covered = set()
    for comp in manifest.get("java_components", {}).values():
        covered.update(comp.get("artifacts", []))
    problems = []
    for group_artifact, version in jar_maven_artifacts(FORGE_JAR).items():
        artifact_id = group_artifact.split(":", 1)[1]
        pair = f"{artifact_id}:{version}"
        if pair not in covered and not is_forge_module(group_artifact, version, manifest):
            problems.append(f"forge.jar has Maven artifact {group_artifact}:{version} with no matching java_components entry")
    return problems


def check_requirements_coverage(manifest):
    if not os.path.isfile(REQUIREMENTS_PATH):
        return []
    pinned = []
    with open(REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            m = _PKG_LINE.match(line.strip())
            if m:
                pinned.append(m.group(1).lower())
    known = {c.get("id", "").lower() for c in manifest.get("components", [])}
    known |= {c.get("name", "").lower() for c in manifest.get("components", [])}
    return [f"requirements.txt pins {pkg}, which has no components[] entry" for pkg in pinned if pkg not in known]


def check_dll_coverage(manifest):
    if os.name != "nt":
        return []          # Windows only - the DLLs a Linux/macOS pygame wheel bundles are a different, already-listed set
    try:
        import pygame
    except ImportError:
        return []
    import glob
    pkg_dir = os.path.dirname(pygame.__file__)
    dlls = {os.path.basename(p).lower() for p in glob.glob(os.path.join(pkg_dir, "*.dll"))}
    known = {os.path.basename(lib.get("file", "")).lower() for lib in manifest.get("native_libraries", {}).get("libraries", [])}
    if dlls and not known:
        return [f"{len(dlls)} DLL(s) found in {pkg_dir} but native_libraries.libraries is empty in the manifest - run "
                "the collection command in native_libraries._status and fill it in"]
    return [f"{dll} is in {pkg_dir} but not in native_libraries.libraries" for dll in sorted(dlls - known)]


def _all_entries(manifest):
    for c in manifest.get("components", []):
        yield c.get("id", "?"), c
    for key, c in manifest.get("java_components", {}).items():
        yield key, c
    for lib in manifest.get("native_libraries", {}).get("libraries", []):
        yield lib.get("id", lib.get("name", "?")), lib


def validate_manifest(manifest):
    """Problems with the manifest itself (always checked). NOASSERTION is allowed only while status != "verified": a verified
    entry must name a real licence."""
    out = [f"{cid}: license is NOASSERTION but status is 'verified' - name the licence, or mark it unverified"
           for cid, c in _all_entries(manifest)
           if NOT_ASSERTED in license_ids(c.get("license", "")) and c.get("status") == "verified"]
    out += [f"{cid}: status is 'attribution' but there is no \"attribution\" field saying what the source requires and "
            "where it is given" for cid, c in _all_entries(manifest)
            if c.get("status") == "attribution" and not (c.get("attribution") or "").strip()]
    return out


def unverified_entries(manifest):
    """["name [id]: status"] for every entry that is not verified - the ids let --release say exactly which."""
    return [f"{c.get('name', cid)} [{cid}]: {c.get('status', 'not_collected')}"
            for cid, c in _all_entries(manifest) if c.get("status", "not_collected") not in RELEASE_OK]


def collect_statuses(manifest):
    """[(where, status)] for every entry that carries a status - used by --release."""
    out = []
    for c in manifest.get("components", []):
        out.append((c.get("name", c.get("id", "?")), c.get("status", "not_collected")))
    for key, c in manifest.get("java_components", {}).items():
        out.append((c.get("name", key), c.get("status", "not_collected")))
    for lib in manifest.get("native_libraries", {}).get("libraries", []):
        out.append((lib.get("name", "?"), lib.get("status", "not_collected")))
    if not manifest.get("native_libraries", {}).get("libraries") and manifest.get("native_libraries", {}).get("_status"):
        out.append(("native_libraries", "not_collected"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if THIRD_PARTY_NOTICES.txt is out of date; don't write it")
    ap.add_argument("--release", action="store_true", help="also fail while any manifest entry isn't 'verified', or coverage is incomplete")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest()
    except (OSError, ValueError) as e:
        print(f"Can't read {MANIFEST_PATH}: {e}", file=sys.stderr)
        return 2

    bad = validate_manifest(manifest)
    if bad:
        for p in bad:
            print(p, file=sys.stderr)
        return 1

    text = generate_notices_text(manifest)

    if args.check:
        current = _read(OUTPUT_PATH)
        if current != text:
            print("THIRD_PARTY_NOTICES.txt is out of date - run: python tools/build_notices.py", file=sys.stderr)
            return 1
        print("THIRD_PARTY_NOTICES.txt is up to date.")
    else:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {OUTPUT_PATH}")

    if args.release:
        problems = []
        problems.extend(f"not verified - {p}" for p in unverified_entries(manifest))
        if not manifest.get("native_libraries", {}).get("libraries") and manifest.get("native_libraries", {}).get("_status"):
            problems.append("not verified - native_libraries: not_collected")
        problems.extend(check_jar_coverage(manifest))
        problems.extend(check_requirements_coverage(manifest))
        problems.extend(check_dll_coverage(manifest))
        if problems:
            print("\n--release found problems that must be fixed before shipping:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("--release: every entry is verified and coverage looks complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
