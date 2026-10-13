# SPDX-License-Identifier: GPL-3.0-or-later
"""Power tiers 0-5 for every card (RPG plan decisions 8 and 9: tiers set automatically from Forge's archetype data, plus an
override file; regions gate which tiers their shops, rewards and enemies may use).

  0 Basic   - basic lands (free everywhere)
  1 Common  - not played in Forge's archetype data (score under 0.01), and common or uncommon
  2 Solid   - not played and rare or mythic, or played a little (score 0.01 to 0.1)
  3 Strong  - score 0.1 to 0.5
  4 Elite   - score 0.5 to 2
  5 Power   - score 2 or more, and every card on Vintage's restricted list

"Seen" = in res/deckgendecks/<Format>.raw.dat, Forge's archetypes (LDA topics) of real tournament decks for Vintage, Legacy, Modern,
Pioneer, Historic, Standard and Pauper. Each archetype has a deck count (its share of that format's decks) and a probability per card
(roughly copies / 60). A card's expected copies per deck in a format = sum over its archetypes of share x probability x 60; its
score = sum over formats of WEIGHTS[format] x that. Older formats weigh more (the plan's rule): a card that holds its place in
Vintage or Legacy is stronger than one that only sees Standard play. The cuts are absolute scores, not ranks: Forge's LDA topics
give a tiny probability to thousands of cards nobody plays (12,662 names have some probability, 3,569 a score of 0.01 or more), and
a rank cut made Doom Blade "Elite". The weights and cuts are my estimates; Karl approves the top of the list (gate 2), and
rpg/data/tier_overrides.json beats everything else.

rpg/data/tiers.json is written by tools/rpg_build_data.py (build time) and holds only the cards at or above the floor.

Round RPG3: two optional caps, set in tier_overrides.json's "rules" (both off unless Karl turns them on - gate 2):
  lands_max  - a non-basic land is never above this tier (lands fix mana rather than win games; 23 of the top 60 are lands)
  common_max - a card ever printed at common or uncommon is never above this tier (cheap cards every deck plays)
The restricted list (tier 5) and Karl's own overrides are not capped.
"""
import json
import os

from rpg import cardpool, data_path, rules

TIERS_FILE = data_path("tiers.json")
OVERRIDES_FILE = data_path("tier_overrides.json")

TIER_NAMES = {0: "Basic", 1: "Common", 2: "Solid", 3: "Strong", 4: "Elite", 5: "Power"}
WEIGHTS = {"Vintage": 6, "Legacy": 5, "Modern": 4, "Pioneer": 3, "Historic": 2, "Standard": 2, "Pauper": 1}
CUTS = ((5, 2.0), (4, 0.5), (3, 0.1), (2, 0.01))    # (tier, lowest score); under the last one a card counts as not played
DECK = 60
RARITY_TIER = {"L": 1, "C": 1, "U": 1, "R": 2, "M": 2, "S": 2}


def expected_copies(archetypes, pool):
    """{card name: {format: expected copies per deck}} for every pooled, non-basic card in the archetype data, and the names
    the data has that the pool doesn't (shown by the build tool, never silently dropped)."""
    out, missing = {}, set()
    for fmt, arch in archetypes.items():
        total = sum(a["decks"] for a in arch) or 1
        for a in arch:
            share = a["decks"] / total
            for name, p in a["cards"]:
                card = pool.get(name)
                if card is None:
                    missing.add(name)
                    continue
                if card.is_basic:
                    continue
                per = out.setdefault(card.name, {})
                per[fmt] = per.get(fmt, 0.0) + share * p * DECK
    return out, missing


def build(archetypes, pool, weights=None, cuts=CUTS):
    """{"method": ..., "cards": {name: [tier, score, {format: copies}]}} - deterministic for the same inputs."""
    weights = weights or WEIGHTS
    copies, missing = expected_copies(archetypes, pool)
    scores = {n: sum(weights.get(f, 0) * e for f, e in per.items()) for n, per in copies.items()}
    ranked = sorted(scores, key=lambda n: (-scores[n], n))
    cards, below = {}, 0
    for n in ranked:
        t = next((cut_tier for cut_tier, cut in cuts if scores[n] >= cut), None)
        if t is None:
            below += 1
            continue
        cards[n] = [t, round(scores[n], 4), {f: round(e, 4) for f, e in sorted(copies[n].items())}]
    method = {"weights": weights, "cuts": [list(c) for c in cuts], "deck": DECK,
              "formats": {f: {"archetypes": len(a), "decks": sum(x["decks"] for x in a)} for f, a in archetypes.items()},
              "with_probability": len(ranked), "below_floor": below, "missing_from_pool": sorted(missing)}
    return {"method": method, "cards": cards}


def write(path, built, forge_version):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write('{"forge": %s,\n "about": %s,\n "method": %s,\n "cards": {\n' % (
            json.dumps(forge_version),
            json.dumps("Power tiers from Forge's archetype data (rpg/tiers.py). Row: tier, score, expected copies per deck by format."),
            json.dumps(built["method"], ensure_ascii=False)))
        f.write(",\n".join("  %s: %s" % (json.dumps(n, ensure_ascii=False), json.dumps(v)) for n, v in built["cards"].items()))
        f.write("\n}}\n")
    os.replace(tmp, path)


class Tiers:
    def __init__(self, data, overrides=None, pool=None, lists=None, caps=None):
        self.forge = data.get("forge", "")
        self.method = data.get("method", {})
        self.seen = {cardpool.normal_name(n): (n, v) for n, v in data.get("cards", {}).items()}
        self.overrides = {cardpool.normal_name(n): int(t) for n, t in (overrides or {}).items()}
        self.pool = pool or cardpool.load()
        self.lists = lists
        self.caps = dict(NO_CAPS, **(caps or {}))

    def _lists(self):
        if self.lists is None:
            self.lists = rules.vintage_lists()
        return self.lists

    def info(self, name):
        """{"tier", "source", "score", "formats"} - tier None for a card the RPG never uses (banned, or not in the pool)."""
        key = cardpool.normal_name(name)
        card = self.pool.get(name)
        if card is None:
            return {"tier": None, "source": "unknown", "score": 0.0, "formats": {}}
        lists = self._lists()
        seen = self.seen.get(key)
        score, formats = (seen[1][1], seen[1][2]) if seen else (0.0, {})
        if lists.is_banned(name):
            return {"tier": None, "source": "banned", "score": score, "formats": formats}
        if key in self.overrides:
            return {"tier": self.overrides[key], "source": "override", "score": score, "formats": formats}
        if card.is_basic:
            return {"tier": 0, "source": "basic", "score": score, "formats": formats}
        if lists.is_restricted(name):
            return {"tier": 5, "source": "restricted", "score": score, "formats": formats}
        if seen:
            out = {"tier": seen[1][0], "source": "data", "score": score, "formats": formats}
        else:
            out = {"tier": RARITY_TIER.get(card.rarity, 1), "source": "rarity", "score": 0.0, "formats": {}}
        return self._capped(card, out)

    def _capped(self, card, out):
        """Round RPG3: the caps in tier_overrides.json's "rules" (none by default)."""
        lands, common = self.caps.get("lands_max"), self.caps.get("common_max")
        if lands is not None and card.is_land and not card.is_basic and out["tier"] > lands:
            out = dict(out, tier=lands, capped="lands_max")
        if common is not None and card.rarity in ("C", "U") and out["tier"] > common:
            out = dict(out, tier=common, capped="common_max")
        return out

    def tier_of(self, name):
        return self.info(name)["tier"]

    def top(self, n=50):
        """The n highest-scored cards Forge's data has, with their final tier (overrides and the restricted list applied)."""
        rows = sorted(self.seen.values(), key=lambda nv: (-nv[1][1], nv[0]))[:n]
        return [(name, self.info(name)) for name, _v in rows]

    def counts(self):
        """{tier: number of pooled cards} - every card in the pool, tier None = never used."""
        out = {}
        for c in self.pool.cards.values():
            t = self.tier_of(c.name)
            out[t] = out.get(t, 0) + 1
        return out


NO_CAPS = {"lands_max": None, "common_max": None}


def read_caps(path=OVERRIDES_FILE):
    """{"lands_max": tier or None, "common_max": tier or None} from the override file's "rules" (round RPG3)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return dict(NO_CAPS)
    rules_ = data.get("rules") or {}
    unknown = sorted(set(rules_) - set(NO_CAPS))
    if unknown:
        raise ValueError(f"{path}: unknown rule(s) {unknown}; the rules are {sorted(NO_CAPS)}")
    for k, t in rules_.items():
        if t is not None and (not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= 5):
            raise ValueError(f"{path}: {k} must be null or a whole number 1-5, not {t!r}")
    return dict(NO_CAPS, **rules_)


def read_overrides(path=OVERRIDES_FILE):
    """{name: tier} from the override file ({"cards": {name: tier}, ...}); none when it's missing."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    cards = data.get("cards", {})
    bad = {n: t for n, t in cards.items() if not isinstance(t, int) or not 0 <= t <= 5}
    if bad:
        raise ValueError(f"{path}: a tier must be a whole number 0-5: {bad}")
    return cards


_TIERS = None


def load(path=None, overrides_path=None, pool=None, lists=None):
    global _TIERS
    default = path is None and overrides_path is None and pool is None and lists is None
    if default and _TIERS is not None:
        return _TIERS
    with open(path or TIERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    t = Tiers(data, read_overrides(overrides_path or OVERRIDES_FILE), pool, lists, read_caps(overrides_path or OVERRIDES_FILE))
    if default:
        _TIERS = t
    return t
