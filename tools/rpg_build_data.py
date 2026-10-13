# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the RPG mode's data from Forge's own (Round RPG2; developer tool - the game never runs it).

    python tools/rpg_build_data.py cards            rpg/data/cards.json    every card Forge can play
    python tools/rpg_build_data.py tiers            rpg/data/tiers.json    power tiers from Forge's archetype data
    python tools/rpg_build_data.py packs [SET ...]  rpg/data/packs/*.json  booster pools opened by Forge's BoosterGenerator
    python tools/rpg_build_data.py enemies [KIND ...] rpg/data/enemies/*.json enemy decks from Forge's archetype deck builder, checked
    python tools/rpg_build_data.py review [N]       the top N of the tier list, for Karl (gate 2)
    python tools/rpg_build_data.py compare [N]      the tier caps side by side (round RPG3): counts, the top N, what moves
    python tools/rpg_build_data.py all              cards, tiers, packs
    python tools/rpg_build_data.py jar              rebuild java_rpg/rpg_tools.jar from java_rpg/src (needs a JDK)

It runs java_rpg/rpg_tools.jar (forge.rpgtools.RpgData, built from java_rpg/src - NOT part of the game bridge, so building it never
changes forge_bridge.jar's source stamp) against forge_runtime/forge.jar, with forge_runtime/ as the working folder, as the bridge
does. Needs Java 17+ (the program's own). Re-run after a Forge update: every file says which Forge build it came from.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from rpg import cardpool, data_path, enemies, products, rules, tiers  # noqa: E402

JAR = os.path.join(BASE, "java_rpg", "rpg_tools.jar")
SRC = os.path.join(BASE, "java_rpg", "src")
STAMP = "rpg_tools_source.sha256"            # inside the jar: which sources it was built from (as the bridge's bridge_source.sha256)
FORMATS = ("Vintage", "Legacy", "Modern", "Pioneer", "Historic", "Standard", "Pauper")
PACK_SEED = 20261012
ENEMY_SEED = 20261012
ENEMY_TRIES = 4                  # decks Forge builds per archetype name; select() keeps the first that pass
PACKS_PER_SET = 200
# The sets whose packs ship for the first region (phase 3 picks the towns' lists; these are core sets - gentle, every colour).
DEFAULT_PACK_SETS = ("M10", "M11", "M12", "M13", "M14", "M15", "ORI", "M19", "M20", "M21")


def source_hash(src=SRC):
    """SHA-256 over every .java under java_rpg/src (relative path + text, line endings made \\n): setup_forge's rule for the bridge."""
    h = hashlib.sha256()
    files = sorted(os.path.relpath(os.path.join(dp, f), src).replace(os.sep, "/")
                   for dp, _d, fs in os.walk(src) for f in fs if f.endswith(".java"))
    for rel in files:
        with open(os.path.join(src, rel), "rb") as f:
            data = f.read().replace(b"\r\n", b"\n")
        h.update(rel.encode("utf-8") + b"\0" + data + b"\0")
    return h.hexdigest()


def jar_stamp(jar=JAR):
    try:
        with zipfile.ZipFile(jar) as z:
            return z.read(STAMP).decode("ascii").strip()
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def build_jar(rt=None):
    """Compile java_rpg/src against forge.jar into java_rpg/rpg_tools.jar, with the source stamp. Needs javac."""
    javac = shutil.which("javac")
    if not javac:
        raise SystemExit("javac not found: building rpg_tools.jar needs a JDK.")
    rt = rt or runtime()
    sources = [os.path.join(dp, f) for dp, _d, fs in os.walk(SRC) for f in fs if f.endswith(".java")]
    with tempfile.TemporaryDirectory() as out:
        r = subprocess.run([javac, "--release", "17", "-encoding", "UTF-8", "-cp", os.path.join(rt, "forge.jar"), "-d", out]
                           + sorted(sources), capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("javac failed:\n" + r.stderr[-3000:])
        tmp = JAR + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for dp, _d, fs in sorted(os.walk(out)):
                for f in sorted(fs):
                    full = os.path.join(dp, f)
                    info = zipfile.ZipInfo(os.path.relpath(full, out).replace(os.sep, "/"), date_time=(2026, 1, 1, 0, 0, 0))
                    with open(full, "rb") as fh:
                        z.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
            z.writestr(zipfile.ZipInfo(STAMP, date_time=(2026, 1, 1, 0, 0, 0)), source_hash() + "\n")
        os.replace(tmp, JAR)
    print(f"java_rpg/rpg_tools.jar: built from {len(sources)} source file(s), stamp {source_hash()[:12]}")


def runtime():
    return rules.runtime_dir()


def forge_version(rt=None):
    with open(os.path.join(rt or runtime(), "VERSION.txt"), encoding="utf-8") as f:
        return f.readline().strip()


def run_java(args, rt=None, memory="3g"):
    import forge_client
    java = forge_client.find_java()
    if not java:
        raise SystemExit("No Java 17 or newer found (JAVA_HOME or PATH).")
    rt = rt or runtime()
    cp = JAR + os.pathsep + os.path.join(rt, "forge.jar")
    cmd = [java, f"-Xmx{memory}", "-Djava.awt.headless=true", "-cp", cp, "forge.rpgtools.RpgData"] + list(args)
    r = subprocess.run(cmd, cwd=rt, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"RpgData {args[0]} failed ({r.returncode}):\n{r.stderr[-3000:]}")
    return r


def java_json(command, *args, rt=None):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, command + ".json")
        run_java([command, out] + [str(a) for a in args], rt=rt)
        with open(out, encoding="utf-8") as f:
            return json.load(f)


def build_cards():
    cat = java_json("catalogue")
    n = cardpool.write(cardpool.CARDS_FILE, cat, forge_version())
    print(f"rpg/data/cards.json: {n} cards ({forge_version()})")
    return cat


def build_tiers():
    arch = java_json("archetypes", *FORMATS)["formats"]
    pool = cardpool.load(cardpool.CARDS_FILE)
    built = tiers.build(arch, pool)
    tiers.write(tiers.TIERS_FILE, built, forge_version())
    counts = {}
    for _n, (t, _s, _f) in built["cards"].items():
        counts[t] = counts.get(t, 0) + 1
    print(f"rpg/data/tiers.json: {built['method']['with_probability']} names have a probability in Forge's archetype data; "
          f"{len(built['cards'])} score {tiers.CUTS[-1][1]} or more; by tier {dict(sorted(counts.items()))}")
    missing = built["method"]["missing_from_pool"]
    if missing:
        print(f"  {len(missing)} names in the archetype data are not in the card pool (left out): {', '.join(missing[:12])}"
              + (" ..." if len(missing) > 12 else ""))
    return built


def build_packs(sets=None, count=PACKS_PER_SET, seed=PACK_SEED):
    sets = [s.upper() for s in (sets or DEFAULT_PACK_SETS)]
    out = java_json("boosters", seed, count, *sets)
    done = products.write_pools(out, products.PACKS_DIR, forge_version(), sets)
    for code in sets:
        entry = out["sets"].get(code, {})
        print(f"  {code}: " + (f"{done[code]} packs ({entry.get('template')})" if code in done else f"NOT written: {entry.get('error')}"))
    return done


def build_enemies(seed=ENEMY_SEED, tries=ENEMY_TRIES, only=None):
    """Every kind, or only the kinds named (round RPG3: adding kinds must not rebuild - and so change - the ones already
    card-checked and soak-tested: Forge's builder draws from one random stream per run, so other names change the decks)."""
    kinds = enemies.read_kinds()
    if only:
        unknown = sorted(set(only) - {k["id"] for k in kinds})
        if unknown:
            raise SystemExit(f"no such enemy kind: {', '.join(unknown)}")
        kinds = [k for k in kinds if k["id"] in only]
    by_format, tries_by_format = {}, {}
    for k in kinds:
        by_format.setdefault(k["format"], set()).update(k["archetypes"])
        tries_by_format[k["format"]] = max(tries_by_format.get(k["format"], 0), k.get("tries", tries))   # a kind's own "tries"
    built = {}
    for fmt, names in sorted(by_format.items()):
        built[fmt] = java_json("decks", seed, fmt, tries_by_format[fmt], *sorted(names))["decks"]
    t = tiers.load(tiers.TIERS_FILE, pool=cardpool.load(cardpool.CARDS_FILE))
    for k in kinds:
        cands = [d for d in built[k["format"]] if d["archetype"] in k["archetypes"]]
        cands.sort(key=lambda d: (k["archetypes"].index(d["archetype"]), d.get("index", 0)))
        accepted, rejected = enemies.select(cands, k, tier_table=t)
        enemies.write_kind(k, accepted, rejected, forge_version(), seed, tier_table=t)
        print(f"  {k['id']}: {len(accepted)} of {k['decks']} decks ({len(cands)} built, {len(rejected)} turned down)")
    return built


def review(n=50):
    t = tiers.load(tiers.TIERS_FILE, pool=cardpool.load(cardpool.CARDS_FILE))
    lines = [f"Top {n} by score ({t.forge}). Weights {t.method.get('weights')}; cuts {t.method.get('cuts')}.",
             "rank  tier  score   card  -  expected copies per deck where it is played most"]
    for i, (name, info) in enumerate(t.top(n), 1):
        fm = sorted(info["formats"].items(), key=lambda kv: -kv[1])[:3]
        where = ", ".join(f"{f} {c:.2f}" for f, c in fm)
        lines.append(f"{i:4d}  {info['tier']} {tiers.TIER_NAMES[info['tier']]:<6} {info['score']:6.2f}  {name}  -  {where}"
                     + ("  [restricted]" if info["source"] == "restricted" else ""))
    return "\n".join(lines)


CAP_OPTIONS = (("as it is", {}), ("lands at most 3", {"lands_max": 3}), ("once-common at most 4", {"common_max": 4}),
               ("both", {"lands_max": 3, "common_max": 4}))


def compare(n=60):
    """Round RPG3 (gate 2): the same tier list under each cap option - how many pooled cards each tier holds, the top n by
    score with the tier each option gives it, and every card whose tier an option changes."""
    pool = cardpool.load(cardpool.CARDS_FILE)
    lists = rules.vintage_lists()
    with open(tiers.TIERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    overrides = tiers.read_overrides()
    opts = [(label, tiers.Tiers(data, overrides, pool, lists, caps)) for label, caps in CAP_OPTIONS]
    base = opts[0][1]
    names = [c.name for c in pool.cards.values()]
    lines = [f"Tier caps compared ({base.forge}). Caps leave the restricted list and your own overrides alone.", ""]
    lines.append("CARDS PER TIER (every card in the pool)")
    lines.append("  " + "option".ljust(24) + "".join(f"{t:>8}" for t in range(6)) + "    none")
    finals = []
    for label, t in opts:
        tiers_by_name = {n: t.tier_of(n) for n in names}
        finals.append(tiers_by_name)
        counts = {}
        for v in tiers_by_name.values():
            counts[v] = counts.get(v, 0) + 1
        lines.append("  " + label.ljust(24) + "".join(f"{counts.get(k, 0):>8}" for k in range(6)) + f"{counts.get(None, 0):>8}")
    lines += ["", f"TOP {n} BY SCORE - the tier under each option"]
    lines.append("  rank  score   " + "".join(label[:14].ljust(16) for label, _t in opts) + "card")
    for i, (name, _info) in enumerate(base.top(n), 1):
        cells = "".join(str(t.tier_of(name)).ljust(16) for _label, t in opts)
        lines.append(f"  {i:4d}  {base.info(name)['score']:6.2f}  {cells}{name}")
    for (label, _t), final in zip(opts[1:], finals[1:]):
        moved = sorted((n for n in names if final[n] != finals[0][n]), key=lambda n: (-(finals[0][n] or 0), n))
        by_step = {}
        for nm in moved:
            by_step.setdefault((finals[0][nm], final[nm]), []).append(nm)
        lines += ["", f"WHAT '{label}' MOVES: {len(moved)} card(s)"]
        for (a, b), group in sorted(by_step.items(), key=lambda kv: (-kv[0][0], -kv[0][1])):
            lines.append(f"  tier {a} -> {b}: {len(group)}  e.g. " + ", ".join(group[:12]) + (" ..." if len(group) > 12 else ""))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=("cards", "tiers", "packs", "enemies", "review", "compare", "all", "jar"))
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if a.what == "jar":
        build_jar()
        return 0
    if jar_stamp() != source_hash():
        raise SystemExit("java_rpg/rpg_tools.jar was not built from the sources in java_rpg/src: run  rpg_build_data.py jar")
    if a.what in ("cards", "all"):
        build_cards()
    if a.what in ("tiers", "all"):
        build_tiers()
    if a.what in ("packs", "all"):
        build_packs(a.args if a.what == "packs" else None)
    if a.what in ("enemies", "all"):
        build_enemies(only=a.args if a.what == "enemies" else None)
    if a.what == "review":
        print(review(int(a.args[0]) if a.args else 50))
    if a.what == "compare":
        print(compare(int(a.args[0]) if a.args else 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
