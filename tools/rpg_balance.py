# SPDX-License-Identifier: GPL-3.0-or-later
"""How often a starting deck beats the RPG's enemies, measured with Forge's AI on both sides (Round RPG9; developer tool - the game
never runs it).

    python tools/rpg_balance.py measure [--source jumpstart|archetype] [--rule two|any] [--starters 24] [--games 2]
                                        [--kind ID ...] [--seed N] [--workers 2]
    python tools/rpg_balance.py tune    --handicap N [--handicap M ...] [--keep 4] [--starters N] [--games N] [--seed N]
    python tools/rpg_balance.py make    --handicap N [--keep 4] [--seed N] [--tuned-by FOLDER]
    python tools/rpg_balance.py record  --measured FOLDER --before FOLDER [--step FOLDER[@N] ...] [--mirror FOLDER]

measure and tune start java_rpg/rpg_tools.jar's forge.rpgtools.Duels (Forge's own AI-vs-AI game loop, as "forge.exe sim" runs it,
many duels per Java start) in --workers Java processes, with forge_runtime/ as the working folder. Every duel is the RPG's: two
60-card decks, 20 life, Forge's Constructed rules, seat A = a starting deck, seat B = an enemy deck, --games games a pairing (Forge
picks who starts the first; the loser of a game starts the next).

  starting decks   made as the new-game screen makes them (rpg.newgame.NewGameFlow): three half-decks, each one picked at random
                   from what the screen offers at that moment (no "Show others"). --rule two = the RPG9 rule (two colours at most);
                   --rule any = before RPG9 (any three, as Karl's 13 Oct deck: three colours).
  enemy decks      the kind files' "decks" (--source archetype: Forge's Pauper archetype decks, RPG2/RPG3) or "jumpstart" (RPG9).
                   Without --kind: the kinds that the first map's regular encounters use (rpg.enemies.regular_kinds()).

measure prints the starting decks' share of the decided games per enemy kind and in all, with a 95% interval (Wilson), and writes
every game to <out>/games.jsonl plus a summary to <out>/summary.json. tune plays the same starting decks against each kind's first
--keep Jumpstart decks (rpg.enemies.jumpstart_candidates, --seed) at every --handicap (that many of the deck's strongest spells
swapped for basic lands, rpg.enemies.handicap) and says which handicap lands nearest --target; it changes no data file. make writes
those decks, at one handicap, into rpg/data/enemies/<kind>.json as "jumpstart" (the archetype "decks" are left as they are).
record writes rpg/data/balance.json from a measurement of the shipped decks and the one from before RPG9.

Needs Java 17+ and java_rpg/rpg_tools.jar built from the current sources (python tools/rpg_build_data.py jar, needs a JDK).
"""
import argparse
import datetime
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from rpg import cardpool, enemies, newgame, rules, tiers  # noqa: E402

JAR = os.path.join(BASE, "java_rpg", "rpg_tools.jar")
TIMEOUT = 120                    # seconds a game may take before it is a draw (forge.exe sim's own default)
TARGET = 0.65                    # Karl, 14 Oct 2026: a typical starting deck beats a first-map enemy about 65% of the time


# ---- decks -------------------------------------------------------------------------------------------------------------

class AnyColours(newgame.NewGameFlow):
    """The new-game flow as it was before Round RPG9: any three half-decks."""

    def allowed(self, half):
        return True


def starter_decks(n, seed, rule="two", decks=None, pool=None, lists=None):
    """[(name, [(card, count)], [half codes])] - n starting decks, each made through NewGameFlow with a random pick from every offer."""
    rng = random.Random(seed)
    out = []
    cls = newgame.NewGameFlow if rule == "two" else AnyColours
    for i in range(n):
        flow = cls(seed=rng.randrange(1 << 40), decks=decks, pool=pool, lists=lists)
        flow.set_name("Balance")
        while flow.step == "pick":
            flow.pick(rng.choice(flow.offer()).code)
        out.append((f"Starter {i + 1}", flow.preview(), [h.code for h in flow.picks]))
    return out


def enemy_decks(kinds, source):
    """[(kind id, deck name, [(card, count)])] from the kind files."""
    out = []
    for k in kinds:
        data = enemies.read_kind(k)
        for d in data.get("jumpstart" if source == "jumpstart" else "decks") or []:
            out.append((k, d["name"], [(n, c) for n, c in d["main"]]))
    return out


def write_dck(folder, name, cards):
    import forge_client
    path = os.path.join(folder, "".join(ch if ch.isalnum() else "_" for ch in name) + ".dck")
    forge_client.write_deck_file(path, [], [n for n, k in cards for _ in range(k)], name=name, fmt="standard")
    return path


# ---- running Forge -------------------------------------------------------------------------------------------------------

def java_command(pairs_file, out_file, timeout=TIMEOUT, memory="2g"):
    import forge_client
    java = forge_client.find_java()
    if not java:
        raise SystemExit("No Java 17 or newer found (JAVA_HOME or PATH).")
    rt = rules.runtime_dir()
    cp = JAR + os.pathsep + os.path.join(rt, "forge.jar")
    return [java, f"-Xmx{memory}", "-Djava.awt.headless=true", "-cp", cp, "forge.rpgtools.Duels", pairs_file, out_file,
            str(timeout)], rt


def run_pairs(pairs, folder, workers=2, timeout=TIMEOUT, log=print):
    """pairs: [(id, deck A path, deck B path, games, seed)]. Plays them in `workers` Java processes; returns the game lines."""
    os.makedirs(folder, exist_ok=True)
    chunks = [pairs[i::workers] for i in range(workers)]
    procs = []
    for i, chunk in enumerate(chunks):
        if not chunk:
            continue
        pf = os.path.join(folder, f"pairs_{i}.tsv")
        with open(pf, "w", encoding="utf-8", newline="\n") as f:
            for p in chunk:
                f.write("\t".join(str(x) for x in p) + "\n")
        of = os.path.join(folder, f"games_{i}.jsonl")
        if os.path.exists(of):
            os.remove(of)
        cmd, cwd = java_command(pf, of, timeout)
        err = open(os.path.join(folder, f"java_{i}.log"), "w", encoding="utf-8")
        procs.append((subprocess.Popen(cmd, cwd=cwd, stdout=err, stderr=subprocess.STDOUT), of, err))
    want = sum(int(p[3]) for p in pairs)
    t0 = time.time()
    while any(p.poll() is None for p, _o, _e in procs):
        time.sleep(15)
        done = sum(_count(o) for _p, o, _e in procs)
        log(f"  {done} of {want} games, {int(time.time() - t0)} s")
    for p, _o, err in procs:
        err.close()
    games = []
    for _p, o, _e in procs:
        if os.path.exists(o):
            with open(o, encoding="utf-8") as f:
                games += [json.loads(ln) for ln in f if ln.strip()]
    return games


def _count(path):
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


# ---- numbers -------------------------------------------------------------------------------------------------------------

def wilson(wins, n, z=1.96):
    """(low, high) - the 95% interval for a share of wins out of n decided games."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def tally(games):
    """{"games", "starter", "enemy", "draws", "share" (starter wins / decided games), "low", "high", "turns" (average)}."""
    a = sum(1 for g in games if g.get("winner") == "A")
    b = sum(1 for g in games if g.get("winner") == "B")
    d = sum(1 for g in games if g.get("winner") not in ("A", "B"))
    n = a + b
    lo, hi = wilson(a, n)
    turns = [g["turns"] for g in games if g.get("turns")]
    return {"games": len(games), "starter": a, "enemy": b, "draws": d, "share": round(a / n, 4) if n else None,
            "low": round(lo, 4), "high": round(hi, 4), "turns": round(sum(turns) / len(turns), 1) if turns else None}


def pct(t):
    if t["share"] is None:
        return "no decided games"
    return (f"{100 * t['share']:.0f}% ({t['starter']}-{t['enemy']}" + (f", {t['draws']} draws" if t["draws"] else "")
            + f"; 95%: {100 * t['low']:.0f}-{100 * t['high']:.0f}%)")


# ---- the commands --------------------------------------------------------------------------------------------------------

def measure(kinds, source, rule, n_starters, games, seed, workers, out, log=print):
    pool, lists = cardpool.load(), rules.vintage_lists()
    folder = os.path.join(out, "decks")
    os.makedirs(folder, exist_ok=True)
    starters = starter_decks(n_starters, seed, rule, pool=pool, lists=lists)
    foes = enemy_decks(kinds, source)
    if not foes:
        raise SystemExit(f"no {source} decks for {', '.join(kinds)}")
    s_paths = [write_dck(folder, name, cards) for name, cards, _h in starters]
    e_paths = [write_dck(folder, f"{k}_{name}", cards) for k, name, cards in foes]
    pairs, labels = [], {}
    rng = random.Random(seed)
    for si, sp in enumerate(s_paths):
        for ei, ep in enumerate(e_paths):
            pid = f"s{si}_e{ei}"
            pairs.append((pid, sp, ep, games, rng.randrange(1 << 40)))
            labels[pid] = (si, ei)
    log(f"{len(starters)} starting decks ({rule}) x {len(foes)} enemy decks ({source}) x {games} games = {len(pairs) * games}")
    played = run_pairs(pairs, out, workers, log=log)
    with open(os.path.join(out, "games.jsonl"), "w", encoding="utf-8") as f:
        for g in played:
            si, ei = labels.get(g["id"], (None, None))
            g = dict(g, starter=starters[si][0] if si is not None else None, halves=starters[si][2] if si is not None else None,
                     kind=foes[ei][0] if ei is not None else None, enemy=foes[ei][1] if ei is not None else None)
            f.write(json.dumps(g) + "\n")
    info = run_info(source, rule, n_starters, games, seed)
    if source == "jumpstart":
        info["handicap"] = sorted({d.get("handicap", 0) for k in kinds for d in enemies.read_kind(k).get(enemies.JUMPSTART) or []})
    summary = with_info(summarize(played, labels, starters, foes), info)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    log(report(summary))
    return summary


def summarize(played, labels, starters, foes):
    by_deck, by_kind, by_starter = {}, {}, {}
    for g in played:
        si, ei = labels.get(g["id"], (None, None))
        if ei is None:
            continue
        k, name, _c = foes[ei]
        by_deck.setdefault(f"{k}|{name}", []).append(g)
        by_kind.setdefault(k, []).append(g)
        by_starter.setdefault(starters[si][0], []).append(g)
    return {"all": tally(played), "kinds": {k: tally(v) for k, v in sorted(by_kind.items())},
            "decks": {k: tally(v) for k, v in sorted(by_deck.items())},
            "by_starter": {k: dict(tally(v), halves=next(s[2] for s in starters if s[0] == k))
                           for k, v in sorted(by_starter.items())}}


def run_info(source, rule, n_starters, games, seed):
    """What a measurement was of: the keys summary.json has beside summarize()'s."""
    return {"source": source, "rule": rule, "starters": n_starters, "games_per_pair": games, "seed": seed,
            "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "forge": _forge()}


def with_info(summary, info):
    """summary + info; a key in both is a mistake (the first baseline run's per-starting-deck numbers were overwritten by the
    number of starting decks that way, 14 Oct 2026)."""
    clash = set(summary) & set(info)
    if clash:
        raise ValueError(f"summary and run info share {sorted(clash)}")
    return dict(summary, **info)


def report(summary):
    lines = [f"Starting decks against the enemies: {pct(summary['all'])}"]
    for k, t in summary["kinds"].items():
        lines.append(f"  {k:16s} {pct(t)}  (average game {t['turns']} turns)")
    return "\n".join(lines)


def _forge():
    try:
        with open(os.path.join(rules.runtime_dir(), "VERSION.txt"), encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return ""


def tune(kinds, keep, swaps, target, n_starters, games, seed, workers, out, log=print):
    """Play the same starting decks against each kind's first `keep` Jumpstart candidates at every handicap in `swaps` (spells
    swapped for basic lands, rpg.enemies.handicap) and say which handicap lands nearest the target. Writes <out>/tune.json; changes
    no data file."""
    pool, lists = cardpool.load(), rules.vintage_lists()
    tier_table = tiers.load(pool=pool)
    folder = os.path.join(out, "decks")
    os.makedirs(folder, exist_ok=True)
    starters = starter_decks(n_starters, seed, "two", pool=pool, lists=lists)
    s_paths = [write_dck(folder, name, cards) for name, cards, _h in starters]
    pairs, labels = [], {}
    rng = random.Random(seed)
    for k in kinds:
        kind = next(x for x in enemies.read_kinds() if x["id"] == k)
        for swap in swaps:
            for ci, d in enumerate(enemies.jumpstart_candidates(kind, keep, seed, pool=pool, lists=lists, tier_table=tier_table,
                                                                swap=swap)):
                ep = write_dck(folder, f"{k}_{ci}_swap{swap}", d["main"])
                for si, sp in enumerate(s_paths):
                    pid = f"{swap}|{k}|{ci}|{si}"
                    pairs.append((pid, sp, ep, games, rng.randrange(1 << 40)))
                    labels[pid] = (swap, k)
    log(f"{len(swaps)} handicaps x {len(kinds)} kinds x {keep} decks x {len(starters)} starting decks x {games} games = "
        f"{len(pairs) * games}")
    played = run_pairs(pairs, out, workers, log=log)
    with open(os.path.join(out, "games.jsonl"), "w", encoding="utf-8") as f:
        for g in played:
            f.write(json.dumps(g) + "\n")
    by = {}
    for g in played:
        lab = labels.get(g["id"])
        if lab:
            by.setdefault(lab[0], {}).setdefault(lab[1], []).append(g)
    result = {"target": target, "keep": keep, "seed": seed, "starters": n_starters, "games_per_pair": games, "forge": _forge(),
              "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "handicaps": {}}
    for swap in swaps:
        per = by.get(swap, {})
        allg = [g for v in per.values() for g in v]
        result["handicaps"][str(swap)] = {"all": tally(allg), "kinds": {k: tally(v) for k, v in sorted(per.items())}}
        log(f"  {swap:2d} spells swapped: {pct(tally(allg))}")
    result["suggested"] = suggest([(swap, result["handicaps"][str(swap)]["all"]["share"]) for swap in swaps], target)
    log(f"nearest the target ({100 * target:.0f}%): {result['suggested']} spells swapped")
    with open(os.path.join(out, "tune.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    return result


def suggest(points, target):
    """The handicap (a whole number) where the measured share would be the target: straight lines between the measured points
    (sorted by handicap), the nearest end when the target is outside them. points: [(handicap, share or None)]."""
    pts = sorted((h, s) for h, s in points if s is not None)
    if not pts:
        return None
    for (h0, s0), (h1, s1) in zip(pts, pts[1:]):
        if min(s0, s1) <= target <= max(s0, s1) and s1 != s0:
            return round(h0 + (target - s0) * (h1 - h0) / (s1 - s0))
    return min(pts, key=lambda p: abs(p[1] - target))[0]


def make(kinds, keep, swap, seed, target, tuned_by=None, log=print):
    """Write each kind's first `keep` Jumpstart candidates, with `swap` spells swapped for basic lands, into its file."""
    pool, lists = cardpool.load(), rules.vintage_lists()
    tier_table = tiers.load(pool=pool)
    info = {"when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "forge": _forge(), "seed": seed, "keep": keep,
            "handicap": swap, "target": target, "rule": "two", "tuned_by": tuned_by}
    out = {}
    for k in kinds:
        kind = next(x for x in enemies.read_kinds() if x["id"] == k)
        decks = enemies.jumpstart_candidates(kind, keep, seed, pool=pool, lists=lists, tier_table=tier_table, swap=swap)
        if len(decks) < keep:
            raise SystemExit(f"{k}: only {len(decks)} Jumpstart decks could be made")
        for i, d in enumerate(decks):
            d["name"] = f"{kind['name']} {i + 1}"
        enemies.write_jumpstart(k, decks, info, tier_table=tier_table)
        out[k] = decks
        log(f"{k}: " + "; ".join(" + ".join(d["halves"]) for d in decks))
    return out


def record(measured, before, steps=(), mirror=None, path=None):
    """rpg/data/balance.json from a measurement of the shipped decks (a measure --source jumpstart folder), the measurement before
    RPG9 (measure --source archetype --rule any) and, when given, the tuning steps (tuning_steps) and the Jumpstart-without-
    handicap numbers (a folder with summary.json or partial_summary.json)."""
    m = _load(measured, "summary.json")
    if (m.get("source"), m.get("rule")) != ("jumpstart", "two"):
        raise SystemExit("the measurement must be of the Jumpstart decks with the two-colour rule")
    kinds = sorted(m["kinds"])
    handicaps = sorted({d.get("handicap", 0) for k in kinds for d in enemies.read_kind(k).get(enemies.JUMPSTART) or []})
    if m.get("handicap") is not None and m["handicap"] != handicaps:
        raise SystemExit(f"the measurement was of decks with {m['handicap']} spells swapped; the files have {handicaps}")
    data = {"about": "Round RPG9 (Karl, 14 Oct 2026: the first map was \"way too hard\"; \"About 65%\"): how often a starting deck "
                     "(three Jumpstart half-decks, at most two colours, picked at random from the new-game offers) beats the first "
                     "map's regular foes, Forge's AI playing both sides (tools/rpg_balance.py; 60 cards, 20 life, Forge's "
                     "Constructed rules). share = the starting decks' wins / decided games; low-high = the 95% interval (Wilson). "
                     "handicap = how many of each foe deck's strongest spells are swapped for basic lands (rpg.enemies.handicap). "
                     "decks_sha = rpg.enemies.jumpstart_digest of the decks measured; a test checks it against the shipped decks.",
            "target": TARGET, "handicap": handicaps[0] if len(handicaps) == 1 else handicaps,
            "decks_sha": enemies.jumpstart_digest(kinds), "measured": _short(m), "before": _short(_load(before, "summary.json"))}
    if mirror:
        name = "partial_summary.json" if os.path.exists(os.path.join(mirror, "partial_summary.json")) else "summary.json"
        data["jumpstart_without_handicap"] = _short(_load(mirror, name))
    if steps:
        data["tuning"] = tuning_steps(steps)
    path = path or os.path.join(BASE, "rpg", "data", "balance.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return data


def tuning_steps(specs):
    """[{"handicap", "seed", "starters", "games_per_pair", "all"}] sorted by handicap, from tune folders (every handicap in their
    tune.json) and measure folders ("FOLDER@N": decks with N spells swapped, when its summary.json doesn't say)."""
    out = []
    for spec in specs:
        folder, _, n = spec.partition("@")
        if os.path.exists(os.path.join(folder, "tune.json")):
            t = _load(folder, "tune.json")
            out += [{"handicap": int(h), "seed": t["seed"], "starters": t["starters"], "games_per_pair": t["games_per_pair"],
                     "all": v["all"]} for h, v in t["handicaps"].items()]
        else:
            m = _load(folder, "summary.json")
            h = int(n) if n else (m.get("handicap") or [None])[0]
            out.append({"handicap": h, "seed": m["seed"], "starters": m["starters"], "games_per_pair": m["games_per_pair"],
                        "all": m["all"]})
    return sorted(out, key=lambda x: (x["handicap"], x["seed"]))


def _load(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as f:
        return json.load(f)


def _short(summary):
    """A summary without its per-deck and per-starting-deck lines (they stay in the run's own folder)."""
    return {k: v for k, v in summary.items() if k not in ("decks", "by_starter")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=("measure", "tune", "make", "record"))
    ap.add_argument("--source", choices=("jumpstart", "archetype"), default="jumpstart")
    ap.add_argument("--rule", choices=("two", "any"), default="two")
    ap.add_argument("--starters", type=int, default=24)
    ap.add_argument("--games", type=int, default=2)
    ap.add_argument("--kind", action="append", help="only these enemy kinds (repeatable); default: the first map's regular foes")
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--keep", type=int, default=4, help="Jumpstart decks per kind (tune, make)")
    ap.add_argument("--handicap", type=int, action="append",
                    help="spells swapped for basic lands (tune: repeat for each to try; make: the one to write)")
    ap.add_argument("--target", type=float, default=TARGET)
    ap.add_argument("--tuned-by", default=None, help="make: the tune folder the handicap came from (written into the files)")
    ap.add_argument("--measured", help="record: the measure folder of the shipped decks")
    ap.add_argument("--before", help="record: the measure folder from before RPG9 (--source archetype --rule any)")
    ap.add_argument("--step", action="append", default=[],
                    help="record: a tune folder, or a measure folder as FOLDER@N (decks with N spells swapped); repeatable")
    ap.add_argument("--mirror", help="record: a folder with Jumpstart-without-handicap numbers")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    kinds = a.kind or enemies.regular_kinds()
    if a.what == "record":
        if not (a.measured and a.before):
            ap.error("record needs --measured and --before")
        data = record(a.measured, a.before, a.step, a.mirror)
        print(f"rpg/data/balance.json: {pct(data['measured']['all'])}")
        return 0
    if a.what == "make":
        if not a.handicap or len(a.handicap) != 1:
            ap.error("make needs one --handicap")
        make(kinds, a.keep, a.handicap[0], a.seed, a.target, a.tuned_by)
        return 0
    out = a.out or os.path.join(BASE, "soak_runs", f"rpg_balance_{a.what}_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    if a.what == "measure":
        measure(kinds, a.source, a.rule, a.starters, a.games, a.seed, a.workers, out)
    else:
        if not a.handicap:
            ap.error("tune needs at least one --handicap")
        tune(kinds, a.keep, a.handicap, a.target, a.starters, a.games, a.seed, a.workers, out)
    print("written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
