# SPDX-License-Identifier: GPL-3.0-or-later
"""PROG-TUNE's tables from Adventure soak records (tools/rpg_soak.py, records/*.jsonl.gz). Developer script, not the game.

    python3 tools/rpg_soak_tables.py FOLDER [FOLDER ...] > tables.md      (Round PROG-TUNE)

Every number is computed here from the records; nothing is typed in by hand."""
import collections
import glob
import gzip
import json
import math
import os
import statistics
import sys

MIN_PER_DUEL, FIXED_MIN = 11, 6          # tools/rpg_soak.py MINUTES_PER_DUEL, FIXED_MINUTES (SLICE_ECONOMY R-e) [estimate]


def wilson(w, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def wr(w, n):
    if not n:
        return "-"
    lo, hi = wilson(w, n)
    return f"{100 * w / n:.0f}% ({w}/{n}; {lo:.0f}-{hi:.0f})"


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt(x, nd=0):
    if x is None:
        return "-"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def load(folders):
    runs, duels = [], []
    for fo in folders:
        for p in sorted(glob.glob(os.path.join(fo, "records", "*.jsonl*"))):
            op = gzip.open if p.endswith(".gz") else open
            with op(p, "rt", encoding="utf-8") as f:
                for ln in f:
                    d = json.loads(ln)
                    d["_folder"] = os.path.basename(fo.rstrip("/"))
                    (runs if d["t"] == "run" else duels).append(d)
    return runs, duels


def level_band(L):
    L = L or 1
    return "L1-2" if L <= 2 else ("L3-4" if L <= 4 else "L5+")


def tables(runs, duels, key=lambda r: (r["settings"], r["player"])):
    out = []
    groups = collections.defaultdict(list)
    for r in runs:
        groups[key(r)].append(r)
    dgroups = collections.defaultdict(list)
    for d in duels:
        dgroups[key(d)].append(d)
    out.append("## Per setting and player type (medians over runs; 'reached' = runs that got there)\n")
    out.append("| Setting / type | Runs | Duels | Win rate, regular foes (95%) | Level 3 / 5 / 10 at duel (reached) | Level at end | "
               "Boss beaten at duel (min) | Runs ending the slice <= 45 min | Gold per hour (won - lost) | Cards per hour | "
               "Gold spent | Card quality d1 -> d10 -> d20 -> d30 |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in sorted(groups):
        rs = groups[k]
        ds = dgroups[k]
        reg = [d for d in ds if d.get("enc") == "regular" and d.get("result") in ("won", "lost")]
        lv = []
        for L in ("3", "5", "10"):
            xs = [r.get("level_at", {}).get(L) for r in rs]
            got = [x for x in xs if x is not None]
            lv.append(f"{fmt(med(got))} ({len(got)}/{len(rs)})")
        boss = [r.get("boss_at") for r in rs if r.get("boss_at") is not None]
        fit45 = sum(1 for r in rs if r.get("end_minutes") is not None and r["end_minutes"] <= 45)
        hours = [(FIXED_MIN + MIN_PER_DUEL * r["duels_n"]) / 60 for r in rs]
        gph = med([(r["gold_won"] - r["gold_lost"]) / h for r, h in zip(rs, hours) if h])
        cards0 = {}
        for d in sorted(ds, key=lambda d: d["n"]):
            cards0.setdefault(d["run"], d.get("cards"))
        cph = med([((r.get("cards_end") or 0) - (cards0.get(r["run"]) or 0)) / h for r, h in zip(rs, hours) if h])
        q = []
        for n in (1, 10, 20, 30):
            q.append(fmt(med([d.get("power") for d in ds if d["n"] == n]), 2))
        out.append(f"| {k[0]} / {k[1]} | {len(rs)} | {sum(r['duels_n'] for r in rs)} | "
                   f"{wr(sum(1 for d in reg if d['result'] == 'won'), len(reg))} | {' / '.join(lv)} | "
                   f"{fmt(med([r.get('level_end') for r in rs]))} | {fmt(med(boss))} ({fmt(med([FIXED_MIN + MIN_PER_DUEL * b for b in boss]))}) | "
                   f"{fit45}/{len(rs)} | {fmt(gph)} | {fmt(cph, 1)} | {fmt(med([r['gold_spent'] for r in rs]))} | {' -> '.join(q)} |")
    return "\n".join(out)


def by_setting(runs, duels):
    out = []
    S = sorted({r["settings"] for r in runs})
    out.append("\n## Win rate against each foe kind by level (all player types pooled; regular, dungeon and boss fights)\n")
    kinds = sorted({d.get("kind") for d in duels if d.get("kind")})
    out.append("| Setting | Foe | L1-2 | L3-4 | L5+ |")
    out.append("|---|---|---|---|---|")
    for s in S:
        for k in kinds:
            cells = []
            for b in ("L1-2", "L3-4", "L5+"):
                ds = [d for d in duels if d["settings"] == s and d.get("kind") == k and level_band(d.get("level")) == b
                      and d.get("result") in ("won", "lost")]
                cells.append(wr(sum(1 for d in ds if d["result"] == "won"), len(ds)))
            out.append(f"| {s} | {k} | " + " | ".join(cells) + " |")
    out.append("\n## Gold lost per loss by kind of fight, and gold won back from foes holding it (GOLD1)\n")
    out.append("| Setting | Losses: regular (mean lost) | dungeon | boss | Gold won back (sum; wins that gave any) | First-win bonuses paid | Bounty gold (per run, median) |")
    out.append("|---|---|---|---|---|---|---|")
    for s in S:
        ds = [d for d in duels if d["settings"] == s]
        cells = []
        for e in ("regular", "dungeon", "boss"):
            ls = [d for d in ds if d.get("enc") == e and d["result"] == "lost"]
            cells.append(f"{len(ls)} ({fmt(statistics.mean([-d['gold_change'] for d in ls]) if ls else None, 1)})")
        back = [d.get("held_back") or 0 for d in ds]
        rs = [r for r in runs if r["settings"] == s]
        out.append(f"| {s} | " + " | ".join(cells) + f" | {sum(back)} ({sum(1 for b in back if b)}) | "
                   f"{sum(1 for d in ds if d.get('first_win'))} | {fmt(med([r.get('bounty_gold') for r in rs]))} |")
    out.append("\n## Packs bought by the gambler, by kind, and flips (a visit ending with more gold than it began, packs bought)\n")
    out.append("| Setting | Kind | Visits that bought | Packs | Gold spent | Flips | Net gold of those visits (sum) |")
    out.append("|---|---|---|---|---|---|---|")
    for s in S:
        pv = [v for r in runs if r["settings"] == s for v in (r.get("pack_visits") or []) if v.get("n")]
        for kind in sorted({v["kind"] for v in pv}):
            vs = [v for v in pv if v["kind"] == kind]
            out.append(f"| {s} | {kind} | {len(vs)} | {sum(v['n'] for v in vs)} | {sum(v['n'] * v['price'] for v in vs)} | "
                       f"{sum(1 for v in vs if v.get('flip'))} | {sum(v['gold_after'] - v['gold_before'] for v in vs)} |")
    out.append("\n## Loops and odd runs\n")
    for s in S:
        rs = [r for r in runs if r["settings"] == s]
        loops = [r for r in rs if r.get("loop")]
        out.append(f"- {s}: {len(loops)} of {len(rs)} runs with a pack flip (loop_gain sum {sum(r.get('loop_gain') or 0 for r in rs)}); "
                   f"stuck {sum(1 for r in rs if r.get('stuck'))}; errors {sum(1 for r in rs if r.get('error'))}; "
                   f"trades {sum(len(r.get('trades') or []) for r in rs)}; gear bought {sum(len(r.get('gear_bought') or []) for r in rs)}; "
                   f"perks taken: {dict(collections.Counter(p for r in rs for p in (r.get('perks') or [])))}")
    return "\n".join(out)


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def checks(runs, duels):
    """The instrument checks (each has a planted error that must trip it): start effects reach the duels once a run is past level
    1; each setting's XP per regular win is its own progression.json's; swaps keep the card type and mana value (rule shape)."""
    out = ["\n## Instrument checks\n"]
    lv2 = [d for d in duels if (d.get("level") or 1) >= 2]
    out.append(f"- duels at level 2+: {len(lv2)}; with start effects sent to Forge: {sum(1 for d in lv2 if d.get('spec'))}")
    for s in sorted({d["settings"] for d in duels}):
        ws = [d["xp_after"] - d["xp_before"] for d in duels if d["settings"] == s and d.get("enc") == "regular"
              and d.get("result") == "won" and d.get("xp_after") is not None]
        out.append(f"- {s}: XP per regular win, median {fmt(med(ws))} (n {len(ws)})")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    try:
        from rpg import cardpool
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import rpg_soak
        pool = cardpool.load()
        bad = tot = 0
        for r in runs:
            if r.get("rebuild_policy") != "shape":
                continue
            for a, b in r.get("swaps") or []:
                ca, cb = pool.get(a), pool.get(b)
                if ca is None or cb is None:
                    continue
                tot += 1
                bad += not rpg_soak._fits("shape", ca, cb)
        out.append(f"- rule shape: {tot} swaps, {bad} that change the card type or the mana value by more than 1")
    except Exception as e:
        out.append(f"- swap check not run: {e}")
    return "\n".join(out)


def option_c(duels, min_duels=5):
    """The data a fitted strength estimate would learn from: distinct decks (deck_key) with a measured win rate against regular
    foes, and Card quality's rank correlation with win rate across them."""
    by = collections.defaultdict(list)
    for d in duels:
        if d.get("deck_key") and d.get("enc") == "regular" and d.get("result") in ("won", "lost"):
            by[d["deck_key"]].append(d)
    sizes = sorted(len(v) for v in by.values())
    big = {k: v for k, v in by.items() if len(v) >= min_duels}
    q = [statistics.mean(d["power"] for d in v if d.get("power") is not None) for v in big.values()]
    w = [sum(1 for d in v if d["result"] == "won") / len(v) for v in big.values()]
    rho = spearman(q, w)
    return (f"\n## Option C's data (regular-foe duels, decks told apart by their exact card list)\n\n"
            f"- distinct decks: {len(by)}; duels per deck: median {fmt(med(sizes))}, max {max(sizes) if sizes else '-'}; "
            f"decks with >= {min_duels} duels: {len(big)} ({sum(len(v) for v in big.values())} duels)\n"
            f"- Spearman rank correlation, Card quality vs win rate, over those decks: "
            f"{'-' if rho is None else f'{rho:+.2f}'} (n = {len(big)})\n")


if __name__ == "__main__":
    folders = sys.argv[1:]
    runs, duels = load(folders)
    print(f"# PROG-TUNE tables from {', '.join(os.path.basename(f.rstrip('/')) for f in folders)}: {len(runs)} runs, {len(duels)} duels; "
          f"real Forge duels with turns: {sum(1 for d in duels if d.get('engine') == 'forge' and (d.get('turns') or 0) > 0)}\n")
    print(tables(runs, duels))
    print(by_setting(runs, duels))
    print(option_c(duels))
    print(checks(runs, duels))
