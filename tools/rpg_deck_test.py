# SPDX-License-Identifier: GPL-3.0-or-later
"""Play the RPG's enemy decks against each other through the real bridge (Round RPG2; gate 2: "generated decks pass ... the
AI-vs-AI soak"). Developer tool.

    python tools/rpg_deck_test.py --games 40            every deck in rpg/data/enemies/, 1v1 Constructed at 20 life
    python tools/rpg_deck_test.py --hours 1 --kind thornback
    python tools/rpg_deck_test.py --hours 0.3 --starters 8   also 8 starting decks (three random Jumpstart half-decks each)

It is tools/soak.py (the soak bot in one seat, Forge's AI in the other, the bridge's own checks, the canary) with the enemy decks as
the only deck pool: soak.py itself is not changed - this script hands it the pool and keeps each game's result. Afterwards every
enemy file gets a "soak" entry: the run, and per deck the games it played, how they ended and any failing finding. A deck with a
failing finding, or one that never reached game over, is listed under "soak_failed" for a person to look at; nothing is removed
by this script.
"""
import argparse
import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from rpg import enemies  # noqa: E402


def deck_files(folder, kinds=None):
    """{deck file: (kind id, deck name)} - every enemy deck written out as a deck list the game reads."""
    out = {}
    for name in sorted(os.listdir(enemies.ENEMIES_DIR)):
        if not name.endswith(".json"):
            continue
        data = enemies.read_kind(name[:-5])
        if kinds and data["kind"] not in kinds:
            continue
        for i, d in enumerate(data["decks"]):
            path = os.path.join(folder, f"{data['kind']}_{i + 1}.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(enemies.deck_text(d))
            out[path] = (data["kind"], d["name"])
    return out


def starter_files(folder, n, seed):
    """{deck file: ("starter", name)} - n starting decks, each three Jumpstart half-decks picked as the new-game screen will offer
    them (products.offer, different themes), made into a deck by products.starter_deck. Played but never recorded in a file."""
    import random
    from rpg import products
    rng = random.Random(seed)
    out = {}
    for i in range(n):
        halves = products.offer(rng, n=3)
        cards = products.starter_deck(halves)
        name = "Starter %d (%s)" % (i + 1, " + ".join(h.code for h in halves))
        path = os.path.join(folder, "starter_%d.txt" % (i + 1))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(enemies.deck_text({"name": name, "archetype": "Jumpstart", "main": [list(x) for x in cards]}))
        out[path] = ("starter", name)
    return out


def record(results, files, run_folder, when):
    """Write each kind's "soak" entry from the games' results. A result names its decks by file name only (tools/soak.py keeps
    os.path.basename of each), so the files are matched by name."""
    per_deck = {}
    by_name = {os.path.basename(p): v for p, v in files.items()}
    for r in results:
        for i, path in enumerate(r.decks):
            key = by_name.get(os.path.basename(path))
            if key is None:
                continue
            e = per_deck.setdefault(key, {"games": 0, "game_over": 0, "as_ai": 0, "failed": []})
            e["games"] += 1
            e["game_over"] += r.ended == "game_over"
            e["as_ai"] += i > 0
            if r.fail_rules() or r.ended != "game_over":
                e["failed"].append({"game": r.index, "ended": r.ended, "rules": r.fail_rules()})
    by_kind = {}
    for (kind, deck), e in per_deck.items():
        by_kind.setdefault(kind, {})[deck] = e
    for kind, decks in by_kind.items():
        if kind == "starter":
            continue
        path = os.path.join(enemies.ENEMIES_DIR, kind + ".json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["soak"] = {"run": os.path.basename(os.path.normpath(run_folder)), "when": when, "decks": decks,
                        "soak_failed": sorted(d for d, e in decks.items() if e["failed"])}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    return by_kind


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--games", type=int, default=None)
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--seed", type=int, default=1012)
    ap.add_argument("--kind", action="append", help="only these kinds (repeatable)")
    ap.add_argument("--out", default=None, help="the soak's output folder")
    ap.add_argument("--starters", type=int, default=0, help="also play this many starting decks (Jumpstart x3)")
    a = ap.parse_args(argv)
    from tools import soak
    folder = tempfile.mkdtemp(prefix="rpg_enemies_")
    files = deck_files(folder, a.kind)
    if a.starters:
        files.update(starter_files(folder, a.starters, a.seed))
    if not files:
        raise SystemExit("no enemy decks in rpg/data/enemies/ (python tools/rpg_build_data.py enemies)")
    original_pool, original_game = soak.deck_pool, soak.run_game
    results = []

    def pool(which, fmt="commander"):
        return sorted(files) if fmt == "standard" else original_pool(which, fmt)

    def game(*args, **kw):
        r = original_game(*args, **kw)
        results.append(r)
        return r

    soak.deck_pool, soak.run_game = pool, game
    out = a.out or os.path.join(BASE, "soak_runs", "rpg_enemies_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    argv2 = ["--format", "standard", "--players", "2", "--boards", "0", "--seed", str(a.seed), "--out", out]
    if a.games:
        argv2 += ["--games", str(a.games)]
    if a.hours:
        argv2 += ["--hours", str(a.hours)]
    try:
        code = soak.main(argv2)
    finally:
        soak.deck_pool, soak.run_game = original_pool, original_game
    by_kind = record(results, files, out, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    for kind, decks in sorted(by_kind.items()):
        for deck, e in sorted(decks.items()):
            print(f"{kind:16s} {deck:22s} games {e['games']:2d} (AI {e['as_ai']:2d}), game over {e['game_over']:2d}"
                  + (f", FAILED {e['failed']}" if e["failed"] else ""))
    return code


if __name__ == "__main__":
    sys.exit(main())
