# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools\\coverage_report.py - turns "we ran N games" into "we exercised these kinds of question, and
never these" (Round 28b).

    python tools\\coverage_report.py soak_runs\\*\\soak_shapes.json
    python tools\\coverage_report.py soak_runs\\*\\soak_shapes.json tests_shapes.jsonl

Reads one or more shapes files:
  * a soak_shapes.json (tools/soak.py's summed {"question|shape": count} for one run), or
  * a MANTICORE_SHAPES_LOG file (one JSON line per closed session: {"shapes": {...}}, or the raw
    {q: {shape: n}} form ForgeSession.shapes holds - see forge_client.ForgeSession.close()).

Prints every shape seen with its count, then the REQUIRED shapes (FORGE_REPEAT_BEHAVIOURS.md section
D) that were never seen among them. Exit code 1 when one is missing, 0 when the whole list was hit.
"""
import argparse
import glob
import json
import sys

# (question, a substring of its shape string that is enough to know the shape was seen). Full shape
# strings for "order" (Checks.java: order()); a substring for "choose" (only its max= bucket
# matters here) and "choose_one" (only optional/required); the exact strings BridgeGui.java's
# confirm()/showConfirmDialog() send for "confirm".
REQUIRED_SHAPES = [
    ("order", "all:sorted=0:unsorted=2+"),
    ("order", "all:sorted=2+:unsorted=0"),
    ("order", "some:sorted=0:unsorted=2+"),
    ("choose", "max=1"),
    ("choose", "max=2+"),
    ("choose_one", "optional"),
    ("choose_one", "required"),
    ("confirm", "card"),
    ("confirm", "no_card"),
]


def _add(totals, q, shape, n=1):
    totals[(q, shape)] = totals.get((q, shape), 0) + n


def load_shapes(path):
    """Every (q, shape) -> count this file adds. Understands soak_shapes.json ({"q|shape": n}), a
    MANTICORE_SHAPES_LOG file (one {"shapes": {...}} per line, or a bare shapes dict per line, or
    {"q":..,"shape":..} check-style lines - anything ForgeSession might have written), and a plain
    JSON {"q": {"shape": n}} dump."""
    totals = {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and not _looks_like_jsonl_only(text):
        _merge_shapes_obj(totals, data)
        return totals
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            if "shapes" in obj and isinstance(obj["shapes"], dict):
                _merge_shapes_obj(totals, obj["shapes"])
            elif "q" in obj and "shape" in obj:
                _add(totals, obj["q"], obj["shape"], obj.get("n", 1))
            else:
                _merge_shapes_obj(totals, obj)
    return totals


def _looks_like_jsonl_only(text):
    """True when the file is one JSON object per line (more than one line of it), so a dict parse
    of the WHOLE file (which json.loads would only succeed on for a single-object file) is not
    what was meant."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) > 1


def _merge_shapes_obj(totals, obj):
    """A {"q|shape": n} map (soak_shapes.json) or a {"q": {"shape": n}} map (ForgeSession.shapes,
    written with a str() key or as nested JSON - both are read)."""
    for k, v in obj.items():
        if isinstance(v, dict):
            for shape, n in v.items():
                _add(totals, k, shape, n)
        elif "|" in k:
            q, shape = k.split("|", 1)
            _add(totals, q, shape, v)


def missing_required(totals):
    seen_by_q = {}
    for (q, shape) in totals:
        seen_by_q.setdefault(q, []).append(shape)
    missing = []
    for q, needle in REQUIRED_SHAPES:
        shapes = seen_by_q.get(q, [])
        if not any(needle in shape for shape in shapes):
            missing.append((q, needle))
    return missing


def format_report(totals):
    lines = ["Shapes seen:"]
    for (q, shape), n in sorted(totals.items()):
        lines.append("  %-12s %-28s x%d" % (q, shape, n))
    if not totals:
        lines.append("  (none)")
    missing = missing_required(totals)
    lines.append("")
    if missing:
        lines.append("REQUIRED shapes never seen (%d):" % len(missing))
        for q, needle in missing:
            lines.append("  %-12s %s" % (q, needle))
    else:
        lines.append("Every required shape was seen.")
    return "\n".join(lines), missing


def main(argv=None):
    ap = argparse.ArgumentParser(description="Which kinds of question a soak run (or a test run) exercised, and which required ones it never did.")
    ap.add_argument("paths", nargs="+", help="soak_shapes.json / MANTICORE_SHAPES_LOG files (globs are expanded)")
    args = ap.parse_args(argv)
    files = []
    for p in args.paths:
        matched = sorted(glob.glob(p))
        files.extend(matched or [p])
    totals = {}
    read_any = False
    for path in files:
        try:
            for k, n in load_shapes(path).items():
                totals[k] = totals.get(k, 0) + n
            read_any = True
        except OSError as e:
            print("could not read %s: %s" % (path, e))
    report, missing = format_report(totals)
    print(report)
    if not read_any:
        print("\nno shapes file could be read.")
        return 2
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
