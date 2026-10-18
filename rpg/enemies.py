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

Round RPG9 (Karl, 14 Oct 2026: the first map was "way too hard"; he chose "Jumpstart foes first"): a kind may also have "jumpstart"
decks - three Jumpstart half-decks made into 60 cards exactly as a starting deck is (products.starter_deck), at most two colours
with the kind's colour in at least two of the halves, every card inside the region's tier band, no card Forge's AI is told to
leave out. Which list a fight uses is the region's choice by kind of encounter (towns.json "enemy_decks"; rpg.duel.start): on the
first region regular foes play Jumpstart decks, the cave's dungeon foes and its boss the archetype ones. Jumpstart against
Jumpstart came out about even (47% for the starting decks with Forge's AI on both sides), so each foe's deck has a handicap: its
strongest spells swapped for basic lands (handicap()); tools/rpg_balance.py tunes how many against Karl's target (65%), writes
the decks (jumpstart_candidates, write_jumpstart) and measures them (rpg/data/balance.json).
"""
import json
import os
import random
import re

from rpg import cardpool, data_path, products, rules, tiers

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


# ---- Round RPG9: Jumpstart decks ----------------------------------------------------------------------------------------

JUMPSTART = "jumpstart"
ARCHETYPE = "archetype"
SOURCES = (ARCHETYPE, JUMPSTART)
JUMPSTART_HALVES = 3
JUMPSTART_MAX_COLOURS = 2        # the same rule as a starting deck (rpg.newgame.MAX_COLOURS)
JUMPSTART_TRIES = 4000           # random draws per kind before jumpstart_candidates gives up looking for more


def deck_list(data, source):
    """(source used, [decks]) - a kind file's Jumpstart decks when asked for and it has some, else its archetype decks."""
    if source == JUMPSTART and data.get(JUMPSTART):
        return JUMPSTART, data[JUMPSTART]
    return ARCHETYPE, data.get("decks") or []


def regular_kinds(folder=None):
    """The kinds the shipped maps' regular encounters use (the first map's foes), sorted."""
    from rpg import explore
    folder = folder or explore.MAPS_DIR
    kinds = set()
    for name in explore.map_names(folder):
        for t in explore.load_area(name, folder).triggers:
            if t.kind == "encounter" and t.encounter == "regular" and t.enemy:
                kinds.add(t.enemy)
    return sorted(kinds)


def half_problems(half, band, pool, tier_table, flags=ai_flags):
    """[reason] a half-deck can't be part of an enemy's Jumpstart deck for (a card Forge's AI is told to leave out, or a card
    above the band's top tier), [] when it can."""
    out = []
    flagged = sorted({n for n, _k in half.cards if flags(n)})
    if flagged:
        out.append("Forge's AI is told to leave these out: " + ", ".join(flagged))
    over = sorted({f"{n} (tier {tier_table.tier_of(n)})" for n, _k in half.cards if (tier_table.tier_of(n) or 0) > band[1]})
    if over:
        out.append(f"above tier {band[1]}: " + ", ".join(over))
    return out


def jumpstart_problems(kind, halves, pool=None, lists=None):
    """[reason] three half-decks can't be this kind's Jumpstart deck for (the colours, the deck rules), [] when they can."""
    pool = pool or cardpool.load()
    lists = lists or rules.vintage_lists()
    out = []
    if len(halves) != JUMPSTART_HALVES or len({h.code for h in halves}) != JUMPSTART_HALVES:
        out.append(f"{JUMPSTART_HALVES} different half-decks are needed")
    cols = products.colours_of(halves, pool)
    if len(cols) > JUMPSTART_MAX_COLOURS:
        out.append(f"{len(cols)} colours ({cols}); at most {JUMPSTART_MAX_COLOURS}")
    kc = kind.get("colour", "")
    if any(c not in cols for c in kc):
        out.append(f"the kind's colour {kc} is missing ({cols})")
    if len(kc) == 1 and sum(1 for h in halves if products.colours(h, pool) == kc) < 2:
        out.append(f"fewer than two half-decks of the kind's colour {kc}")
    main = products.starter_deck(halves, pool, lists)
    out += [p.text for p in rules.check(main, [], pool, lists)]
    return out


def handicap(main, k, pool=None, tier_table=None):
    """(deck, swapped) - the deck [(name, count)] with its k strongest spells swapped for basic lands, and [(name, count)] of what
    went. Strongest: the highest tier (rpg/tiers.py), then the highest mana value, then by name; one copy at a time. The basics
    come in proportion to those the deck already has (largest remainder; the most common first on a tie). Lands are never swapped.
    k=0, or a deck with no basics or no spells to swap: the deck as it is. Round RPG9: Jumpstart against Jumpstart comes out about
    even with Forge's AI on both sides (47% for the starting decks, 14 Oct 2026), so a first-map foe's deck gives up some of its
    best cards to land near the 65% Karl asked for."""
    pool = pool or cardpool.load()
    deck = [(n, int(c)) for n, c in main]
    if k <= 0:
        return deck, []
    tier_table = tier_table or tiers.load(pool=pool)
    basics = {n: c for n, c in deck if pool.get(n) is not None and pool.get(n).is_basic}
    spells = [(n, c) for n, c in deck if pool.get(n) is None or not pool.get(n).is_land]
    if not basics or not spells:
        return deck, []

    def strength(name):
        card = pool.get(name)
        return (-(tier_table.tier_of(name) or 0), -(card.mana_value if card else 0), name)
    copies = sorted((n for n, c in spells for _ in range(c)), key=strength)[:k]
    gone = {}
    for n in copies:
        gone[n] = gone.get(n, 0) + 1
    k = len(copies)
    total = sum(basics.values())
    share = {n: k * c / total for n, c in basics.items()}
    add = {n: int(v) for n, v in share.items()}
    order = sorted(basics, key=lambda n: (-(share[n] - add[n]), -basics[n], n))
    for n in order[:k - sum(add.values())]:
        add[n] += 1
    out = []
    for n, c in deck:
        c = c - gone.get(n, 0) + add.get(n, 0)
        if c > 0:
            out.append((n, c))
    return out, sorted(gone.items(), key=lambda kv: strength(kv[0]))


def jumpstart_deck(kind, halves, pool=None, lists=None, swap=0, tier_table=None):
    """The kind's Jumpstart deck from three half-decks: {"name", "archetype": "Jumpstart", "halves", "handicap", "swapped",
    "main", "sideboard"} - the 60 cards a starting deck of those halves has (products.starter_deck), with `swap` of its strongest
    spells swapped for basic lands (handicap())."""
    main, swapped = handicap(products.starter_deck(halves, pool, lists), swap, pool, tier_table)
    return {"name": kind["name"], "archetype": "Jumpstart", "halves": [h.code for h in halves], "handicap": swap,
            "swapped": [[n, c] for n, c in swapped], "main": [[n, c] for n, c in main], "sideboard": []}


def jumpstart_candidates(kind, n, seed, band=None, decks=None, pool=None, lists=None, tier_table=None, flags=ai_flags, swap=0):
    """Up to n different Jumpstart decks for the kind, made with random.Random("<seed>:<kind id>") (the same every run): two
    half-decks of the kind's colour (for a two-colour kind: two inside its colours), then a third that keeps the deck within two
    colours; every half-deck passes half_problems and the three pass jumpstart_problems. Each with `swap` spells swapped for
    basic lands (jumpstart_deck)."""
    pool = pool or cardpool.load()
    lists = lists or rules.vintage_lists()
    tier_table = tier_table or tiers.load(pool=pool)
    if band is None:
        from rpg import economy
        band = economy.read_world().band(1)
    halves = [h for h in (decks if decks is not None else products.jumpstart())
              if products.colours(h, pool) and not half_problems(h, band, pool, tier_table, flags)]
    kc = kind.get("colour", "")
    own = [h for h in halves if products.colours(h, pool) and set(products.colours(h, pool)) <= set(kc)]
    if len(kc) == 1:
        own = [h for h in own if products.colours(h, pool) == kc]
    rng = random.Random(f"{seed}:{kind['id']}")
    out, seen = [], set()
    for _ in range(JUMPSTART_TRIES):
        if len(out) >= n or len(own) < 2:
            break
        a, b = rng.sample(own, 2)
        third = [h for h in halves if h.code not in (a.code, b.code)
                 and len(products.colours_of([a, b, h], pool)) <= JUMPSTART_MAX_COLOURS
                 and all(c in products.colours_of([a, b, h], pool) for c in kc)]
        if not third:
            continue
        c = rng.choice(third)
        key = tuple(sorted((a.code, b.code, c.code)))
        if key in seen:
            continue
        seen.add(key)
        if jumpstart_problems(kind, [a, b, c], pool, lists):
            continue
        out.append(jumpstart_deck(kind, [a, b, c], pool, lists, swap, tier_table))
    return out


def write_jumpstart(kind_id, decks, info, tier_table=None, folder=None):
    """Put `decks` into the kind's file as its "jumpstart" decks, with `info` (how they were chosen and measured) as
    "jumpstart_balance"; the archetype "decks" and everything else in the file are left as they are."""
    tier_table = tier_table or tiers.load()
    folder = folder or ENEMIES_DIR
    path = os.path.join(folder, kind_id + ".json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for d in decks:
        d["tiers"] = {str(k): v for k, v in sorted(tier_profile(d["main"], tier_table).items(), key=lambda kv: kv[0])}
    data[JUMPSTART] = decks
    data["jumpstart_balance"] = info
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    return path


def jumpstart_digest(kind_ids, folder=None):
    """A SHA-256 over these kinds' shipped Jumpstart deck lists (names, halves, cards): rpg/data/balance.json records it, so a
    test can tell that the balance measurement is of exactly the decks that ship."""
    import hashlib
    h = hashlib.sha256()
    for k in sorted(kind_ids):
        decks = read_kind(k, folder).get(JUMPSTART) or []
        h.update(json.dumps([k, [[d["name"], d.get("halves"), d["main"]] for d in decks]], ensure_ascii=False,
                            sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def deck_text(deck):
    """A deck list the game reads (deck_loader / deck_importer): "# format: standard" and one "N Name" line per card."""
    lines = ["# format: standard", f"# RPG enemy deck: {deck['name']} (Forge archetype {deck['archetype']})", "", "Deck"]
    lines += [f"{k} {n}" for n, k in deck["main"]]
    if deck.get("sideboard"):
        lines += ["", "Sideboard"] + [f"{k} {n}" for n, k in deck["sideboard"]]
    return "\n".join(lines) + "\n"
