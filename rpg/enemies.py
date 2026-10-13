# SPDX-License-Identifier: GPL-3.0-or-later
"""Enemy decks (RPG plan decision 10: bosses built by hand; regular enemies from recipes or archetypes).

A kind of enemy (rpg/data/enemy_kinds.json - names, colours and sources are placeholders until the region's content is written) takes
its decks from archetypes in Forge's own data: forge.rpgtools.RpgData decks runs Forge's DeckgenUtil.buildLDACArchetypeDeck, the deck
builder Forge's "Generate archetype deck" uses. Every deck is checked here BEFORE it ships (gate 2: "generated decks pass the card
check and the AI-vs-AI soak"):
  - Forge built the archetype that was asked for: when a build comes out wrong (card count, too many lands) Forge silently builds a
    RANDOM other archetype instead; its deck name says which ("<archetype> Generated Deck"),
  - the RPG's deck rules (rpg/rules.py: 60+, four of a card, Vintage's restricted and banned lists, only cards Forge can play),
  - no card Forge marks for its AI to leave out (AI:RemoveDeck:All / Random in the card's script),
  - no card above the kind's max_tier (rpg/tiers.py),
then tools/rpg_deck_test.py plays them against each other through the real bridge (Forge's AI) and records the result in the file.
rpg/data/enemies/<kind>.json holds the decks that passed and the reasons the others didn't.
"""
import json
import os
import re

from rpg import cardpool, data_path, rules, tiers

KINDS_FILE = data_path("enemy_kinds.json")
ENEMIES_DIR = data_path("enemies")
FALLBACK_SUFFIX = " Generated Deck"
_REMOVE = re.compile(r"^AI:RemoveDeck:(All|Random)\s*$", re.M)


def read_kinds(path=KINDS_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["kinds"]


def ai_flags(name, runtime=None):
    """The card's AI:RemoveDeck flags in Forge's script ([] when none, None when Forge has no script for it)."""
    import card_check
    root = os.path.join(runtime or rules.runtime_dir(), "res", "cardsfolder")
    script = card_check.find_script(root, name)
    if script is None:
        return None
    return _REMOVE.findall(script)


def tier_profile(main, tier_table):
    """{tier: copies} over the main deck (basic lands are tier 0)."""
    out = {}
    for n, k in main:
        t = tier_table.tier_of(n)
        out[t] = out.get(t, 0) + k
    return out


def judge(deck, kind, pool=None, lists=None, tier_table=None, flags=ai_flags):
    """[reason] a generated deck can't ship for, [] when it can. deck: the Java tool's entry."""
    pool = pool or cardpool.load()
    lists = lists or rules.vintage_lists()
    tier_table = tier_table or tiers.load()
    if deck.get("error"):
        return [f"Forge could not build it: {deck['error']}"]
    reasons = []
    wanted = deck["archetype"] + FALLBACK_SUFFIX
    if deck.get("deckName") != wanted:
        reasons.append(f"Forge built another archetype instead ({deck.get('deckName')!r}, not {wanted!r})")
    main, side = [tuple(x) for x in deck["main"]], [tuple(x) for x in deck.get("sideboard", [])]
    reasons += [p.text for p in rules.check(main, side, pool, lists)]
    flagged = []
    for n, _k in main + side:
        f = flags(n)
        if f:
            flagged.append(f"{n} ({'/'.join(f)})")
    if flagged:
        reasons.append("Forge's AI is told to leave these out: " + ", ".join(sorted(set(flagged))))
    cap = kind.get("max_tier")
    if cap is not None:
        over = sorted({f"{n} (tier {tier_table.tier_of(n)})" for n, _k in main if (tier_table.tier_of(n) or 0) > cap})
        if over:
            reasons.append(f"above tier {cap}: " + ", ".join(over))
    return reasons


def select(candidates, kind, **kw):
    """(accepted decks, rejected [{archetype, index, reasons}]) - at most kind["decks"] accepted, in the order Forge built them,
    and never the same card list twice."""
    accepted, rejected, seen = [], [], set()
    for d in candidates:
        why = judge(d, kind, **kw)
        key = json.dumps(sorted(map(tuple, d.get("main", []))))
        if not why and key in seen:
            why = ["the same list as a deck already chosen"]
        if why or len(accepted) >= kind["decks"]:
            if why:
                rejected.append({"archetype": d.get("archetype"), "index": d.get("index"), "reasons": why})
            continue
        seen.add(key)
        accepted.append({"name": f"{kind['name']} {len(accepted) + 1}", "archetype": d["archetype"], "index": d.get("index"),
                         "main": [list(x) for x in d["main"]], "sideboard": [list(x) for x in d.get("sideboard", [])]})
    return accepted, rejected


def write_kind(kind, accepted, rejected, forge_version, seed, tier_table=None, folder=None):
    tier_table = tier_table or tiers.load()
    folder = folder or ENEMIES_DIR
    os.makedirs(folder, exist_ok=True)
    for d in accepted:
        d["tiers"] = {str(k): v for k, v in sorted(tier_profile(d["main"], tier_table).items(), key=lambda kv: kv[0])}
    data = {"kind": kind["id"], "name": kind["name"], "forge": forge_version, "seed": seed,
            "source": {"format": kind["format"], "archetypes": kind["archetypes"]}, "max_tier": kind.get("max_tier"),
            "decks": accepted, "rejected": rejected, "soak": None}
    path = os.path.join(folder, kind["id"] + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return path


def read_kind(kind_id, folder=None):
    with open(os.path.join(folder or ENEMIES_DIR, kind_id + ".json"), encoding="utf-8") as f:
        return json.load(f)


def deck_text(deck):
    """A deck list the game reads (deck_loader / deck_importer): "# format: standard" and one "N Name" line per card."""
    lines = ["# format: standard", f"# RPG enemy deck: {deck['name']} (Forge archetype {deck['archetype']})", "", "Deck"]
    lines += [f"{k} {n}" for n, k in deck["main"]]
    if deck.get("sideboard"):
        lines += ["", "Sideboard"] + [f"{k} {n}" for n, k in deck["sideboard"]]
    return "\n".join(lines) + "\n"
