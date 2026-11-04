# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PACK2 (Karl, 3 Nov 2026: "Our own wrapper + a card"): which real card shows on the front of a pack's wrapper, and the
wrapper's colours.

The rule (in code, not a hand-made list):
  - a booster set (its plain pack, premium pack and bundle): the highest-tier rare or mythic in the set's plain pack pool; ties go
    to the card that comes up most often in the pool, then to the name (A before Z). Each set gets its own card (Karl): a card
    another set already shows is passed over for that set's next one - the sets are taken in the shops' order (towns.json, town by
    town, each town's set list), then any other set with a pool by its code. The picture is that set's own printing of the card
    (the set and collector number its pool has most often), so the wrapper shows a card from that set;
  - a Jumpstart theme pack: its half-deck's highest-tier non-basic card; ties go to the name.
The wrapper's colours are that card's colours (Forge's, cardpool.Card.colours: "C" for colourless).

rpg/data/pack_faces.json can name another card for a set ("sets": {"M10": "Baneslayer Angel"}) or a theme pack ("themes":
{"JMP Angels 1": "Serra Angel"}); it is empty to start, for Karl to fill in if a set's card should be another one. A name there
that isn't in the card pool is ignored (the rule's card is used).
"""
import json
import os

from rpg import cardpool, data_path, products

FACES_FILE = data_path("pack_faces.json")
_OVERRIDES = {}


def read_overrides(path=None):
    """{"sets": {code: name}, "themes": {half-deck code: name}} - {} parts when the file is missing or unreadable."""
    path = path or FACES_FILE
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return {"sets": {}, "themes": {}}
    hit = _OVERRIDES.get(path)
    if hit is None or hit[0] != stamp:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        out = {"sets": {}, "themes": {}}
        for part in out:
            v = d.get(part) if isinstance(d, dict) else None
            if isinstance(v, dict):
                out[part] = {str(k): str(n) for k, n in v.items() if isinstance(n, str) and n.strip()}
        hit = (stamp, out)
        _OVERRIDES[path] = hit
    return hit[1]


def _tier(tier_table, name):
    t = tier_table.tier_of(name)
    return -1 if t is None else t


def _set_ranking(code, pool, tier_table):
    """[(name, (set, number))] - the set's rares and mythics, best first by the rule, each with its most common printing."""
    seen, prints = {}, {}
    for pack in products.read_pool(code)["packs"]:
        for c in pack:
            if c[3] not in ("R", "M"):
                continue
            card = pool.get(c[0])
            name = card.name if card is not None else c[0]
            seen[name] = seen.get(name, 0) + 1
            pr = prints.setdefault(name, {})
            key = (str(c[1]), str(c[2]))
            pr[key] = pr.get(key, 0) + 1
    order = sorted(seen, key=lambda n: (-_tier(tier_table, n), -seen[n], n))
    return [(n, min(prints[n], key=lambda k: (-prints[n][k], k))) for n in order]


def set_order(world=None):
    """Every set with a pack pool, in the shops' order (towns.json), then the rest by code."""
    if world is None:
        from rpg import economy
        world = economy.read_world()
    out = []
    for town in world.towns.values():
        for code in town.sets:
            if code.upper() not in out and products.has_packs(code):
                out.append(code.upper())
    try:
        files = sorted(f[:-5].upper() for f in os.listdir(products.PACKS_DIR) if f.endswith(".json"))
    except OSError:
        files = []
    return out + [c for c in files if c not in out]


_FACES = {}


def set_faces(pool=None, tier_table=None, overrides=None, world=None):
    """{set code: (card name, set code of the printing, collector number)} for every set with a pool - one card per set (see the
    module's rule); the printing is (None, None) for an override (Scryfall's default picture)."""
    pool = pool or cardpool.load()
    if tier_table is None:
        from rpg import tiers
        tier_table = tiers.load(pool=pool)
    ov = (overrides if overrides is not None else read_overrides())["sets"]
    order = set_order(world)
    key = (id(pool), id(tier_table), tuple(sorted(ov.items())), tuple(order))
    if key in _FACES:
        return _FACES[key]
    out, taken = {}, set()
    for code in order:                                       # the overrides first: they are Karl's, never passed over
        name = ov.get(code)
        if name and pool.get(name) is not None:
            out[code] = (pool.get(name).name, None, None)
            taken.add(pool.get(name).name)
    for code in order:
        if code in out:
            continue
        ranking = _set_ranking(code, pool, tier_table)
        if not ranking:
            continue
        name, (pset, num) = next(((n, p) for n, p in ranking if n not in taken), ranking[0])
        out[code] = (name, pset, num)
        taken.add(name)
    _FACES[key] = out
    return out


def set_face(code, pool=None, tier_table=None, overrides=None, world=None):
    """The card on the front of set `code`'s wrapper (plain, premium and bundle): a card name, or None when the set has no pool."""
    hit = set_faces(pool, tier_table, overrides, world).get(code.upper())
    return hit[0] if hit else None


def set_face_key(code, pool=None, tier_table=None, overrides=None, world=None):
    """The picture key (card_data.art_key) of set `code`'s card: that set's printing, or None."""
    from card_data import art_key
    hit = set_faces(pool, tier_table, overrides, world).get(code.upper())
    return art_key(*hit) if hit else None


def theme_face(half, pool=None, tier_table=None, overrides=None):
    """The card on the front of a Jumpstart half-deck's wrapper: a card name, or None when it has no non-basic card."""
    pool = pool or cardpool.load()
    if tier_table is None:
        from rpg import tiers
        tier_table = tiers.load(pool=pool)
    ov = (overrides if overrides is not None else read_overrides())["themes"].get(half.code)
    if ov and pool.get(ov) is not None:
        return pool.get(ov).name
    names = set()
    for n, _k in half.cards:
        card = pool.get(n)
        if card is None or card.is_basic:
            continue
        names.add(card.name)
    if not names:
        return None
    return min(names, key=lambda n: (-_tier(tier_table, n), n))


def face_colours(name, pool=None):
    """The wrapper's colours: the card's ("W" .. "WUBRG", "C" colourless; "C" for no card)."""
    pool = pool or cardpool.load()
    card = pool.get(name) if name else None
    return card.colours if card is not None else "C"
