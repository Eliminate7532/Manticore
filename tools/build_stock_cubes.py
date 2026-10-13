# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/build_stock_cubes.py - write the stock cubes' bundled lists (Round CD3). A developer tool: the game never runs it.

    python tools\\build_stock_cubes.py            downloads every cube in cube_source.STOCK_CUBES from Cube Cobra and writes
                                                 stock_cubes/<key>.json (the files the draft reads, shipped with the program)
    python tools\\build_stock_cubes.py --check    reads the bundled files only (no network) and says what each holds; exit code 1
                                                 when one is missing, unreadable, or names a different cube or owner than the table

Karl, 12 Oct 2026: the stock cubes' lists ship in the program, each with its owner credited (the draft window and Licenses and
credits - licenses/NOTICES.json, "stock-cubes"). Each file is Cube Cobra's own cubeJSON answer (cube_source.CUBE_JSON_URL, the same
download a pasted link makes), trimmed to what cube_source.from_cubecobra reads, one card per line so a later update shows as a
readable diff. "fetched" says when it was downloaded; "source" is the cube's page.
Cube Cobra's public cubeJSON allows 100 requests a minute; this asks for one cube a second.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import cube_source as cs  # noqa: E402

DETAIL_KEYS = ("name", "oracle_id", "type", "oracle_text", "parsed_cost", "cmc", "colors", "color_identity", "elo", "power",
               "toughness", "keywords", "set", "collector_number", "scryfall_id")
PAGE = "https://cubecobra.com/cube/list/{key}"


def trim(data):
    """Cube Cobra's cubeJSON answer, with only what cube_source.from_cubecobra reads."""
    cards = data.get("cards") or {}
    owner = data.get("owner")
    owner = owner.get("username") if isinstance(owner, dict) else owner

    def det(entry, keys):
        d = entry.get("details") if isinstance(entry, dict) else None
        return {"details": {k: d.get(k) for k in keys if isinstance(d, dict) and k in d}} if isinstance(d, dict) else None
    main = [x for x in (det(e, DETAIL_KEYS) for e in cards.get("mainboard") or []) if x]
    basics = [x for x in (det(e, ("name", "type")) for e in cards.get("basics") or []) if x]
    return {"name": data.get("name"), "shortId": data.get("shortId"), "id": data.get("id"), "owner": {"username": owner},
            "dateLastUpdated": data.get("dateLastUpdated"), "tags": list(data.get("tags") or []),
            "categoryOverride": data.get("categoryOverride"), "categoryPrefixes": list(data.get("categoryPrefixes") or []),
            "cards": {"mainboard": main, "basics": basics}}


def dumps(key, cube, fetched):
    """The file's text: the header, then one card per line."""
    head = {k: v for k, v in cube.items() if k != "cards"}
    lines = ["{", f'"fetched": {json.dumps(fetched)},', f'"source": {json.dumps(PAGE.format(key=key))},',
             '"cube": ' + json.dumps(head, ensure_ascii=False)[:-1] + ', "cards": {"mainboard": [']
    main = cube["cards"]["mainboard"]
    lines += [json.dumps(c, ensure_ascii=False, sort_keys=True) + ("," if i < len(main) - 1 else "") for i, c in enumerate(main)]
    lines.append('], "basics": ' + json.dumps(cube["cards"]["basics"], ensure_ascii=False) + "}}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write(folder=None, session=None, sleep=1.0, only=None):
    folder = folder or cs.stock_dir()
    os.makedirs(folder, exist_ok=True)
    out = []
    for i, st in enumerate(cs.STOCK_CUBES):
        if only and st.key not in only:
            continue
        if i and sleep:
            time.sleep(sleep)
        data = cs.fetch(st.key, session=session)
        cube = trim(data)
        if cube["name"] != st.name or cube["owner"]["username"] != st.owner:
            print(f"  NOTE {st.key}: Cube Cobra now says {cube['name']!r} by {cube['owner']['username']!r}; the table says "
                  f"{st.name!r} by {st.owner!r} - update cube_source.STOCK_CUBES")
        path = cs.stock_path(st.key, folder)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(dumps(st.key, cube, time.time()))
        os.replace(tmp, path)
        out.append((st.key, len(cube["cards"]["mainboard"]), os.path.getsize(path)))
        print(f"  {st.key}: {cube['name']} by {cube['owner']['username']} - {len(cube['cards']['mainboard'])} cards, "
              f"{os.path.getsize(path):,} bytes")
    return out


def check(folder=None):
    """Problems with the bundled files (plain sentences); [] when every stock cube's file is there and names its cube."""
    problems = []
    for st in cs.STOCK_CUBES:
        data, fetched = cs.read_stock(st.key, folder)
        if data is None:
            problems.append(f"{st.key}: {cs.stock_path(st.key, folder)} is missing or unreadable")
            continue
        cube = cs.from_cubecobra(data, st.key)
        if cube.name != st.name or cube.owner != st.owner:
            problems.append(f"{st.key}: the file names {cube.name!r} by {cube.owner!r}, the table {st.name!r} by {st.owner!r}")
        when = time.strftime("%Y-%m-%d", time.gmtime(fetched)) if fetched else "?"
        print(f"  {st.key}: {cube.name} by {cube.owner} - {len(cube.cards)} cards, basics {list(cube.basics)}, fetched {when}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="read the bundled files only (no network)")
    ap.add_argument("--only", nargs="*", help="these stock keys only")
    a = ap.parse_args(argv)
    if a.check:
        problems = check()
        for p in problems:
            print("PROBLEM", p)
        return 1 if problems else 0
    write(only=a.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
