# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG1b: write rpg/data/removal.json - the cards of the RPG's card pool that rpg.shape's removal rule marks, from the Oracle
text in Forge's own card scripts (forge_runtime/res/cardsfolder; the pool, rpg/data/cards.json, has no rules text). Build time
only, like tools/rpg_build_data.py; run it again when Forge or the rule changes.

    python3 tools/rpg_removal.py [--runtime forge_runtime] [--out rpg/data/removal.json] [--check]

--check: say whether the file on disk is what the rule and this Forge give now (exit 1 if not), write nothing.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from rpg import cardpool, shape  # noqa: E402


def build(runtime):
    pool = cardpool.load()
    scanned = shape.scan_cardsfolder(os.path.join(runtime, "res", "cardsfolder"))
    marked, kinds, seen = {}, {}, set()
    for name, kind in scanned.items():
        card = pool.get(name)
        if card is None:
            continue
        seen.add(card.name)
        if kind and card.name not in marked:
            marked[card.name] = kind
    for k in marked.values():
        kinds[k] = kinds.get(k, 0) + 1
    with open(os.path.join(runtime, "VERSION.txt"), encoding="utf-8") as f:
        forge = f.read().strip()
    return {"forge": forge,
            "about": "Round PROG1b: the cards of rpg/data/cards.json that rpg/shape.py's removal rule marks (the rule is in that "
                     "file's docstring), from the Oracle text of Forge's card scripts. Written by tools/rpg_removal.py.",
            "pool_cards_with_a_script": len(seen), "kinds": dict(sorted(kinds.items())), "cards": sorted(marked)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=os.path.join(HERE, "forge_runtime"))
    ap.add_argument("--out", default=shape.REMOVAL_FILE)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    d = build(a.runtime)
    text = json.dumps(d, indent=1, ensure_ascii=False) + "\n"
    if a.check:
        try:
            with open(a.out, encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        print("removal.json is up to date" if same else "removal.json differs: run tools/rpg_removal.py")
        return 0 if same else 1
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{len(d['cards'])} of {d['pool_cards_with_a_script']} pool cards marked as removal: {d['kinds']} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
