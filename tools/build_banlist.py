# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/build_banlist.py - the banned list bundled with the program (Round BAN1).

    python tools/build_banlist.py            fetch Scryfall's banned lists and write banned_<format>.snapshot.json for every
                                             format (round FMT1: commander and brawl - MTG Arena's 100-card Brawl)
    python tools/build_banlist.py --check    exit 1 when a live list differs from its snapshot (a release-checklist step,
                                             next to tools/build_notices.py --release)
    --format brawl                           only that format

The game itself refreshes its own copy (cache/banned_commander.json) in the background once a week; the snapshot is what a copy
with no internet and no cache falls back to, so its date is shown in the deck screen's "Banned in Commander (list as of ...)" line.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import legality  # noqa: E402


def main(argv):
    fmts = legality.FORMATS
    if "--format" in argv:
        i = argv.index("--format")
        fmts = tuple(argv[i + 1:i + 2]) or fmts
        unknown = [f for f in fmts if f not in legality.FORMATS]
        if unknown:
            print(f"Unknown format {unknown[0]} (known: {', '.join(legality.FORMATS)}).")
            return 2
    worst = 0
    for fmt in fmts:
        worst = max(worst, one(fmt, argv))
    return worst


def one(fmt, argv):
    path = os.path.join(ROOT, f"banned_{fmt}.snapshot.json")
    live = legality.fetch_banned(fmt)
    if "--check" in argv:
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f).get("cards") or []
        except (OSError, ValueError):
            old = []
        added, removed = sorted(set(live) - set(old)), sorted(set(old) - set(live))
        if added or removed:
            print(f"The {fmt} snapshot is out of date ({len(old)} cards; Scryfall has {len(live)}).")
            for n in added:
                print("  + " + n)
            for n in removed:
                print("  - " + n)
            print(f"Run: python tools/build_banlist.py --format {fmt}")
            return 1
        print(f"The {fmt} snapshot matches Scryfall ({len(live)} cards).")
        return 0
    data = legality.write_list(path, live, fmt)
    print(f"Wrote {os.path.basename(path)}: {len(live)} cards, {data['fetched_at']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
