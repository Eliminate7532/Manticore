# SPDX-License-Identifier: GPL-3.0-or-later
"""Round STARTER1: what the Adventure's starting decks are like, with and without the upgrade (developer tool; the game never runs
it). Samples N random starting decks the way a new game makes them - three Jumpstart half-decks, at most two colours between them
(rpg.newgame's rule), each next pick at random among those the colour rule allows - and for each K (swaps per half) reports Card
quality (rpg.progress.card_quality), tier-1 spells, tier 3+ spells, removal (rpg.shape), rares + mythics, creatures, and where the
upgrades came from.

    python tools/rpg_starter_measure.py --n 5000 --k 0,1,2,3,4,5,6 [--max-tier 3] [--seed 1] [--never Moat] [--json out.json]
Round STARTER1B: --never leaves those names out of the upgrades; "decks with" counts the decks holding Moat.
"""
import argparse
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from rpg import cardpool, newgame, products, progress, rules, shape, starter, tiers  # noqa: E402


def sample_picks(rng, decks, pool):
    picks = []
    while len(picks) < newgame.PICKS:
        free = [h for h in decks if h.code not in {p.code for p in picks}
                and len(products.colours_of(picks + [h], pool)) <= newgame.MAX_COLOURS]
        picks.append(rng.choice(free))
    return picks


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def deck_stats(deck, pool, tier_table, removal):
    s = shape.shape(deck, pool, removal)
    t1 = t3 = rares = spells = 0
    for n, k in deck:
        c = pool.get(n)
        if c is None or "Land" in (c.type_line or ""):
            continue
        t = tier_table.tier_of(c.name) or 0
        spells += k
        t1 += k if t <= 1 else 0
        t3 += k if t >= 3 else 0
        rares += k if c.rarity in ("R", "M") else 0
    return {"quality": progress.card_quality(deck, pool, tier_table), "size": sum(k for _n, k in deck),
            "lands": s["lands"], "spells": spells, "creatures": s["creatures"], "removal": s["removal"], "tier1": t1,
            "tier3": t3, "rares": rares, "curve": s["curve"]}


def measure(n, ks, max_tier=starter.DEFAULT_MAX_TIER, seed=1, flags=None, never=(), watch=("Moat",)):
    pool = cardpool.load()
    lists = rules.vintage_lists()
    tier_table = tiers.load(pool=pool)
    decks = products.jumpstart()
    removal = shape.removal_names()
    rng = random.Random(seed)
    samples = [(sample_picks(rng, decks, pool), rng.getrandbits(48)) for _ in range(n)]
    out = {}
    for k in ks:
        rows, sources, swaps_n = [], {"theme": 0, "pool": 0}, []
        with_watch = {w: 0 for w in watch}
        for picks, s in samples:
            deck, swaps = starter.starting_deck(picks, s, k, pool, lists, tier_table, decks, max_tier, flags,
                                                 never)
            names = {cardpool.normal_name(nm) for nm, _c in deck}
            for w in watch:
                with_watch[w] += cardpool.normal_name(w) in names
            rows.append(deck_stats(deck, pool, tier_table, removal))
            for w in swaps:
                sources[w.source] += 1
            swaps_n.append(len(swaps))
        q = [r["quality"] for r in rows if r["quality"] is not None]

        def med(key):
            return statistics.median(r[key] for r in rows)
        out[k] = {"n": n, "quality_median": round(statistics.median(q), 3), "quality_p10": round(pct(q, 0.1), 3),
                  "quality_p90": round(pct(q, 0.9), 3), "tier3_median": med("tier3"), "tier1_median": med("tier1"),
                  "removal_median": med("removal"), "removal_p10": pct([r["removal"] for r in rows], 0.1),
                  "rares_median": med("rares"), "creatures_median": med("creatures"), "lands_median": med("lands"),
                  "spells_median": med("spells"), "size_min": min(r["size"] for r in rows),
                  "size_max": max(r["size"] for r in rows), "swaps_mean": round(statistics.mean(swaps_n), 2),
                  "swaps_min": min(swaps_n), "from_theme": sources["theme"], "from_pool": sources["pool"],
                  "decks_with": with_watch, "never": list(never)}
    return out


def table(res):
    lines = ["K  median  p10-p90      tier3+  tier1  removal(p10)  rares  creatures  lands  swaps(mean,min)  theme/pool"]
    for k, r in sorted(res.items()):
        lines.append(f"{k:<2} {r['quality_median']:<7} {r['quality_p10']}-{r['quality_p90']:<7} {r['tier3_median']:<7} "
                     f"{r['tier1_median']:<6} {r['removal_median']} ({r['removal_p10']})      {r['rares_median']:<6} "
                     f"{r['creatures_median']:<10} {r['lands_median']:<6} {r['swaps_mean']},{r['swaps_min']:<12} "
                     f"{r['from_theme']}/{r['from_pool']}  decks with: {r.get('decks_with', {})}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--k", default="0,1,2,3,4,5,6")
    ap.add_argument("--max-tier", type=int, default=starter.DEFAULT_MAX_TIER)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--ai-flags", action="store_true", help="leave out cards Forge's AI is told to leave out (reads scripts)")
    ap.add_argument("--never", default="", help="Round STARTER1B: comma-separated names never chosen as an upgrade")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    flags = None
    if a.ai_flags:
        from rpg import enemies
        flags = enemies.ai_flags
    res = measure(a.n, [int(x) for x in a.k.split(",")], a.max_tier, a.seed, flags,
                  [x.strip() for x in a.never.split(",") if x.strip()])
    print(table(res))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
