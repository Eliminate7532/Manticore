# SPDX-License-Identifier: GPL-3.0-or-later
r"""Round UI6 diagnostic (not part of the game): where does this computer's layout differ from the recorded baseline, and is the
program or the baseline file the odd one out?

    python tools\ui6_layout_diff.py --base <folder holding a clean copy of patch 48's program>

It lays out the 28 tables of tests\ui6_layout.py three ways ON THIS COMPUTER - this tree, the clean copy, and this kind of computer's
recorded layout file (tests\fixtures\ui6) - and writes what differs, key by key, to docs\incoming\ui6\win_layout_diff.txt.
It also prints the width of "CMD" in the tiny bold font, which is what made Windows and Linux differ (tests\fixtures\ui6\README.txt).
It copies tests\ui6_layout.py and this file into the clean copy (so that copy can lay itself out); it changes nothing else."""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(HERE, "docs", "incoming", "ui6", "win_layout_diff.txt")


def dump(out):
    """Lay the tables out in THIS tree and save the snapshots, with what this computer's text engine says."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    sys.path.insert(0, HERE)
    import pygame
    import forge_table as ft
    from tests import ui6_layout as u
    data = u.all_snapshots()
    env = {"tree": HERE, "forge_table": os.path.abspath(ft.__file__), "python": sys.version.split()[0], "platform": platform.platform(),
           "pygame": pygame.version.ver, "sdl": ".".join(map(str, pygame.get_sdl_version()))}
    try:
        env["sdl_ttf"] = ".".join(map(str, pygame.font.get_sdl_ttf_version()))
    except Exception as e:                                           # an older pygame: say so, do not guess
        env["sdl_ttf"] = f"unknown ({e})"
    fonts = {}
    for kind in ("body", "display"):
        for bold in (False, True):
            for px in (12, 16, 20, 24, 32, 40):
                try:
                    f = ft.get_font(px, bold, kind)
                    fonts[f"{kind}|bold={int(bold)}|{px}px"] = [f.get_height(), f.get_linesize()]
                except Exception as e:
                    fonts[f"{kind}|bold={int(bold)}|{px}px"] = f"error: {e}"
    probe = {}                                  # what decides the opponents' board in a 4-player game: the width of "CMD" in the tiny bold font
    for size, scale in u.SIZES:
        gui = u.make_table(u.scenes()["four_players"], size, scale)
        u.draw(gui, 1)
        probe[f"{size[0]}x{size[1]}|{scale}"] = [gui.L.px["tiny"], gui.font("tiny", True).size("CMD")[0]]
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"env": env, "fonts": fonts, "probe": probe, "layouts": data}, fh, sort_keys=True)


def diff_snap(a, b):
    """key -> (a value, b value) for every key that differs; the cards are compared id by id."""
    out = {}
    for k in sorted(set(a) | set(b)):
        if k == "cards":
            ca = {c[0]: c[1:] for c in a.get(k, [])}
            cb = {c[0]: c[1:] for c in b.get(k, [])}
            gone = sorted(set(ca) - set(cb))
            new = sorted(set(cb) - set(ca))
            moved = {i: (ca[i], cb[i]) for i in sorted(set(ca) & set(cb)) if ca[i] != cb[i]}
            if gone or new or moved:
                out[k] = (f"{len(ca)} cards", f"{len(cb)} cards; only in 1st: {gone[:5]}; only in 2nd: {new[:5]}; "
                          f"{len(moved)} moved, e.g. {dict(list(moved.items())[:3])}")
        elif a.get(k) != b.get(k):
            out[k] = (a.get(k), b.get(k))
    return out


def run_dump(root, out):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, os.path.join(root, "tools", "ui6_layout_diff.py"), "--dump", out], cwd=root, env=env,
                       capture_output=True, text=True)
    if p.returncode:
        sys.exit(f"laying out {root} failed:\n{p.stdout}\n{p.stderr}")
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", help="a folder holding a clean copy of patch 48's program (tests\\forge_fake.py, forge_table.py ...)")
    ap.add_argument("--baseline", help="the recorded layout file to compare with (default: this kind of computer's file in tests\\fixtures\\ui6)")
    ap.add_argument("--report", default=REPORT, help="where to write the report")
    ap.add_argument("--dump", help="(internal) lay out this tree and save the snapshots here")
    args = ap.parse_args()
    if args.dump:
        dump(args.dump)
        return
    if not args.base or not os.path.isfile(os.path.join(args.base, "forge_table.py")):
        sys.exit("give --base <folder> : a clean copy of patch 48's program (it must hold forge_table.py)")
    sys.path.insert(0, HERE)
    from tests import ui6_layout as u
    args.baseline = args.baseline or u.baseline_path()
    if not args.baseline:
        sys.exit(f"no recorded layout is named for {sys.platform!r} (BASELINE_FILES in tests/ui6_layout.py); give --baseline <file>")
    base = os.path.abspath(args.base)
    if os.path.normcase(base) == os.path.normcase(HERE):
        sys.exit("--base must be a different folder from this one")
    os.makedirs(os.path.join(base, "tools"), exist_ok=True)
    shutil.copy(os.path.abspath(__file__), os.path.join(base, "tools", "ui6_layout_diff.py"))
    shutil.copy(os.path.join(HERE, "tests", "ui6_layout.py"), os.path.join(base, "tests", "ui6_layout.py"))
    cache = os.path.join(HERE, "cache")
    os.makedirs(cache, exist_ok=True)
    mine = run_dump(HERE, os.path.join(cache, f"ui6_layout_here_{sys.platform}.json"))         # named for the computer: a run on one never overwrites another's
    clean = run_dump(base, os.path.join(cache, f"ui6_layout_clean_{sys.platform}.json"))
    with open(args.baseline, encoding="utf-8") as fh:
        rec = json.load(fh)
    arms = (("A", "recorded baseline", rec, "UI6 tree here", mine["layouts"]),
            ("B", "recorded baseline", rec, "clean patch 48 here", clean["layouts"]),
            ("C", "clean patch 48 here", clean["layouts"], "UI6 tree here", mine["layouts"]))
    lines = ["UI6 layout diff", "",
             "UI6 tree : " + json.dumps(mine["env"], sort_keys=True),
             "clean 48 : " + json.dumps(clean["env"], sort_keys=True),
             "baseline : " + args.baseline, "",
             "font heights [height, linesize] as this computer measures them (UI6 tree | clean copy):"]
    for k in sorted(mine["fonts"]):
        lines.append(f"  {k:28} {mine['fonts'][k]} | {clean['fonts'].get(k)}")
    lines += ["", 'width of "CMD" in the tiny bold font, [font px, width] (UI6 tree | clean copy) - the frame round an opponent\'s commander is this + 12 wide:']
    for k in sorted(mine["probe"]):
        lines.append(f"  {k:16} {mine['probe'][k]} | {clean['probe'].get(k)}")
    lines += ["", "Differences per pair (1st value | 2nd value):"]
    table = {}
    for tag, an, a, bn, b in arms:
        lines += ["", f"== {tag}: {an}  vs  {bn} =="]
        keys = sorted(set(a) | set(b))
        n = 0
        for key in keys:
            d = diff_snap(a.get(key, {}), b.get(key, {})) if key in a and key in b else {"(layout)": ("missing" if key not in a else "present", "missing" if key not in b else "present")}
            table.setdefault(key, {})[tag] = len(d)
            if d:
                n += 1
                lines.append(f"{key}:")
                for k, (x, y) in d.items():
                    lines.append(f"    {k}: {x}  |  {y}")
        lines.append(f"-- {n} of {len(keys)} layouts differ")
    lines += ["", "Summary (number of differing keys; 0 = identical):", f"{'layout':40} {'A base|UI6':>11} {'B base|clean':>13} {'C clean|UI6':>12}"]
    for key in sorted(table):
        t = table[key]
        lines.append(f"{key:40} {t['A']:>11} {t['B']:>13} {t['C']:>12}")
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[-(len(table) + 3):]))
    print(f"\nfull report: {args.report}")


if __name__ == "__main__":
    main()
