# SPDX-License-Identifier: GPL-3.0-or-later
"""Round STARTER1 (Karl, 4 Nov 2026: "Adventure starting decks are so bad they're not even fun to play"; he picked "Upgrade picked
halves"): the player's starting deck is still three of Forge's Jumpstart half-decks (RPG plan decision 11), but each picked half
gives up its K weakest spells for stronger cards before the 60 are put together. No pygame.

    upgrade_half(half, k, ...)          -> (HalfDeck with the swaps, [Swap])
    upgrade(halves, seed, k, ...)       -> (the halves upgraded, [Swap])        every picked half, in pick order
    starting_deck(halves, seed, ...)    -> ([(name, count)], [Swap])            products.starter_deck of the upgraded halves

THE RULE (the brief's, Karl's to change - K and MAX_TIER are in rpg/data/numbers.json "starter_upgrade"):
  - per half, K swaps: its non-land spells are tried one copy at a time, weakest first (a copy with no fitting card stays and
    the next is tried): the lowest tier (an unranked card counts as 0), then the highest
    mana value, then by name - the reverse of rpg.enemies.handicap's "strongest" order;
  - each is swapped for a card of the same card type (tools/rpg_soak.py card_kind: Creature, Planeswalker, Battle, Instant,
    Sorcery, Artifact, Enchantment) with a mana value within MV_SLACK (1) - PROG-TUNE's "shape" rule, so the curve and the
    creature count stay -, in the half's own colours (or colourless), of a HIGHER tier than the card it replaces and at most
    max_tier, not a land, not banned or restricted, not an Alchemy rebalance ("A-" names, the shop's skip prefix), and not a card
    Forge's AI is told to leave out (only when a flag reader is given - the game passes none, the measurement tools do);
  - first from the same Jumpstart theme's other half-decks (same set and theme, another number: "JMP Above the Clouds 2-4" for
    "JMP Above the Clouds 1"), so the theme stays; only when none fits, from the whole Adventure pool;
  - among the fitting cards the highest tier is taken; between cards of that tier the choice is random, from the new game's seed
    (random.Random(<seed>:starter_upgrade:<pick>:<half code>)), so the same seed and picks make the same deck;
  - never a card on numbers.json's starter_upgrade "never" list (Round STARTER1B, Karl 7 Nov 2026: "Only Moat");
  - copy limits hold over the whole deck: a card is never added past four (its own limit), so products.starter_deck's clamp never
    removes an upgrade; colours never grow (the half's colours), so the RPG9 two-colour rule still holds; lands never change.
Round STARTER1C (Karl, 8 Nov 2026: starting decks of 61-65 cards "must come out at exactly 60"): Forge has two half-decks over 20
cards (J25 Goblins 3: 23, J25 Modified: 22 - counted 8 Nov 2026, Forge 9a6f776), and products.starter_deck adds the halves up, so
a deck with them came to 62-65. Before anything else each picked half is trimmed to HALF_SIZE (20) by trim_half(): of the cards over
20, round(cut * lands / size) are its most common basic land (so the half's land share stays as it was), the rest its weakest
spells, one copy at a time, in the upgrade's own weakest order. Then the upgrade runs on the trimmed half (12 swaps as before),
and products.starter_deck fills a deck under 60 (a 19-card half) with basics as it always did: every starting deck is 60.
k = 0 is today's deck exactly (but for that trim). Foes are NOT upgraded: rpg/enemies.py builds them with products.starter_deck directly.
"""
import random

from rpg import cardpool, products, rules, tiers

MV_SLACK = 1                       # PROG-TUNE's rule (Karl, 3 Nov 06:59): a swap keeps the mana value within 1
KIND_ORDER = ("Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment")
SKIP_PREFIXES = ("A-",)            # Alchemy rebalances: numbers.json's shop_skip_prefixes
HALF_SIZE = 20                     # Round STARTER1C: a Jumpstart half-deck's size; three make the 60
DEFAULT_MAX_TIER = 3               # the brief's estimate: tier 4-5 cards stay rewards and chase cards


class Swap:
    """One upgraded copy: in half `half` (its code), `out` gave way to `into` (tiers out_tier -> in_tier); source "theme" (from the
    same theme's other half-decks) or "pool"."""
    __slots__ = ("half", "out", "into", "out_tier", "in_tier", "source")

    def __init__(self, half, out, into, out_tier, in_tier, source):
        self.half, self.out, self.into = half, out, into
        self.out_tier, self.in_tier, self.source = out_tier, in_tier, source

    def as_list(self):
        return [self.half, self.out, self.into, self.out_tier, self.in_tier, self.source]

    def __repr__(self):
        return f"Swap({self.half!r}: {self.out!r} -> {self.into!r}, {self.out_tier}->{self.in_tier}, {self.source})"


def card_kind(card):
    """The card's one type for the rule: Creature for any creature (an artifact creature too), else the first of KIND_ORDER in its
    type line (the same as tools/rpg_soak.py card_kind)."""
    front = card.type_line.split(" - ")[0]
    for k in KIND_ORDER:
        if k in front.split():
            return k
    return front.split()[-1] if front.split() else "?"


def on_colour(card, cols):
    return card.colours == "C" or all(x in cols for x in card.colours if x in "WUBRG")


_TIER_CACHE = {}


def _cached(tier_table):
    """tier_table with tier_of remembered per name (tiers.Tiers.info works the lists out on every call; the upgrade asks thousands
    of times). Kept per tier table."""
    if isinstance(tier_table, _CachedTiers):
        return tier_table
    hit = _TIER_CACHE.get(id(tier_table))
    if hit is None or hit.inner is not tier_table:
        hit = _CachedTiers(tier_table)
        _TIER_CACHE.clear()
        _TIER_CACHE[id(tier_table)] = hit
    return hit


class _CachedTiers:
    def __init__(self, inner):
        self.inner, self.memo = inner, {}

    def tier_of(self, name):
        t = self.memo.get(name, -1)
        if t == -1:
            t = self.memo[name] = self.inner.tier_of(name)
        return t


def _tier(tier_table, name):
    return tier_table.tier_of(name) or 0


class _Index:
    """The Adventure pool's possible upgrades, worked out once per pool and tier table: {(kind, mana value): [(tier, name, card)]}
    of every non-land card with a known tier from 1 to 5, not banned or restricted, not an "A-" card."""
    def __init__(self, pool, tier_table, lists):
        self.by_shape = {}
        for card in pool.cards.values():
            if card.is_land or card.is_basic or card.name.startswith(SKIP_PREFIXES):
                continue
            if lists.is_banned(card.name) or lists.is_restricted(card.name):
                continue
            t = tier_table.tier_of(card.name)
            if not t:
                continue
            self.by_shape.setdefault((card_kind(card), card.mana_value), []).append((t, card.name, card))
        for v in self.by_shape.values():
            v.sort(key=lambda x: (-x[0], x[1]))

    def near(self, kind, mv):
        out = []
        for m in range(mv - MV_SLACK, mv + MV_SLACK + 1):
            out += self.by_shape.get((kind, m), [])
        return out


_INDEX = {}


def _index(pool, tier_table, lists):
    key = (id(pool), id(tier_table), id(lists))
    hit = _INDEX.get(key)
    if hit is None or hit[0] is not pool:
        hit = (pool, _Index(pool, tier_table, lists))
        _INDEX.clear()
        _INDEX[key] = hit
    return hit[1]


def weakest(half, k, pool, tier_table):
    """The k weakest non-land copies of the half: [name] (one entry per copy), weakest first (k=None: all of them)."""
    def weak(name):
        card = pool.get(name)
        return (_tier(tier_table, name), -(card.mana_value if card else 0), name)
    copies = []
    for n, c in half.cards:
        card = pool.get(n)
        if card is None or card.is_land:
            continue
        copies += [n] * int(c)
    copies.sort(key=weak)
    return copies if k is None else copies[:max(0, k)]


def trim_half(half, pool, tier_table, size=HALF_SIZE):
    """Round STARTER1C: (the half cut down to `size` cards, [(name, copies cut)]) - unchanged when it has `size` or fewer. Of the
    cut, round(cut * lands / total) are basic lands (its most common basic; never more basics than it has), the rest its weakest
    non-land copies (weakest(): lowest tier, highest mana value, name). Non-basic lands are never cut."""
    total = sum(int(c) for _n, c in half.cards)
    cut = total - size
    if cut <= 0:
        return half, []
    tier_table = _cached(tier_table)
    lands = sum(int(c) for n, c in half.cards if pool.get(n) is not None and pool.get(n).is_land)
    basics = {}
    for n, c in half.cards:
        card = pool.get(n)
        if card is not None and card.is_basic:
            basics[n] = basics.get(n, 0) + int(c)
    gone = {}
    land_cut = min(int(cut * lands / total + 0.5), sum(basics.values()))
    for _ in range(land_cut):
        b = max(sorted(basics), key=lambda n: basics[n])
        basics[b] -= 1
        gone[b] = gone.get(b, 0) + 1
    spells = weakest(half, None, pool, tier_table)
    for n in spells[:cut - land_cut]:
        gone[n] = gone.get(n, 0) + 1
    left = dict(gone)
    cards = []
    for n, c in half.cards:
        c = int(c)
        take = min(c, left.get(n, 0))
        left[n] = left.get(n, 0) - take
        if c - take > 0:
            cards.append((n, c - take))
    return products.HalfDeck(half.code, cards), sorted(gone.items())


def theme_mates(half, decks):
    """The same Jumpstart theme's other half-decks (same set and theme, another number)."""
    return [h for h in decks if h.set == half.set and h.theme == half.theme and h.code != half.code]


def _limit(card, lists):
    if lists.is_restricted(card.name):
        return 1
    if card.copy_limit < 0:
        return 10 ** 6                    # "any number" (Relentless Rats): Forge's -1
    return card.copy_limit or rules.FOUR


def upgrade_half(half, k, pool, tier_table, lists, rng, decks=(), max_tier=DEFAULT_MAX_TIER, totals=None, flags=None, never=()):
    """(the half with its k weakest spells swapped, [Swap]). totals: {normal name: copies} over the whole starting deck so far
    (the other picks and this half's cards), kept up to date so no card goes past its limit; flags(name) -> truthy for a card
    Forge's AI is told to leave out (None: not checked); never: card names never chosen as an upgrade (Round STARTER1B,
    numbers.json starter_upgrade.never - the copy tries the next fitting card, as for any card that doesn't fit)."""
    if k <= 0:
        return half, []
    never = {cardpool.normal_name(n) for n in never}
    tier_table = _cached(tier_table)
    cols = products.colours(half, pool)
    totals = {} if totals is None else totals
    index = _index(pool, tier_table, lists)
    mates = theme_mates(half, decks)
    mate_names = []
    for h in mates:
        for n, _c in h.cards:
            if n not in mate_names:
                mate_names.append(n)
    cards = [[n, int(c)] for n, c in half.cards]
    swaps = []
    flagged = {}

    def ok(card, out_card, out_tier):
        if card is None or card.is_land or card.is_basic or card.name.startswith(SKIP_PREFIXES):
            return False
        if card.name == out_card.name or not on_colour(card, cols):
            return False
        if never and cardpool.normal_name(card.name) in never:
            return False
        if card_kind(card) != card_kind(out_card) or abs(card.mana_value - out_card.mana_value) > MV_SLACK:
            return False
        t = tier_table.tier_of(card.name)
        if not t or t <= out_tier or t > max_tier:
            return False
        if lists.is_banned(card.name) or lists.is_restricted(card.name):
            return False
        if totals.get(cardpool.normal_name(card.name), 0) + 1 > _limit(card, lists):
            return False
        if flags is not None:
            key = card.name
            if key not in flagged:
                flagged[key] = bool(flags(key))
            if flagged[key]:
                return False
        return True

    done = 0
    for out in weakest(half, None, pool, tier_table):
        if done >= k:
            break
        out_card = pool.get(out)
        if totals.get(cardpool.normal_name(out), 0) > _limit(out_card, lists):
            continue                          # more copies than the deck may hold: starter_deck drops them anyway
        out_tier = _tier(tier_table, out)
        pick, source = None, None
        theme = [pool.get(n) for n in mate_names]
        theme = [c for c in theme if ok(c, out_card, out_tier)]
        if theme:
            pick, source = _choose(theme, tier_table, rng), "theme"
        else:
            near = [c for _t, _n, c in index.near(card_kind(out_card), out_card.mana_value) if ok(c, out_card, out_tier)]
            if near:
                pick, source = _choose(near, tier_table, rng), "pool"
        if pick is None:
            continue
        for row in cards:
            if row[0] == out and row[1] > 0:
                row[1] -= 1
                break
        row = next((r for r in cards if cardpool.normal_name(r[0]) == cardpool.normal_name(pick.name)), None)
        if row is None:
            cards.append([pick.name, 1])
        else:
            row[1] += 1
        ko, kp = cardpool.normal_name(out), cardpool.normal_name(pick.name)
        totals[ko] = totals.get(ko, 0) - 1
        totals[kp] = totals.get(kp, 0) + 1
        swaps.append(Swap(half.code, out, pick.name, out_tier, tier_table.tier_of(pick.name), source))
        done += 1
    new = products.HalfDeck(half.code, [(n, c) for n, c in cards if c > 0])
    return new, swaps


def _choose(cards, tier_table, rng):
    """The highest tier among `cards`; a random one (rng) between cards of that tier, in name order first so the choice is the
    same whatever order they came in."""
    top = max(tier_table.tier_of(c.name) for c in cards)
    best = sorted({c.name: c for c in cards if tier_table.tier_of(c.name) == top}.values(), key=lambda c: c.name)
    return best[rng.randrange(len(best))]


def half_rng(seed, i, half):
    from rpg.save import _seed_int
    return random.Random(_seed_int(seed, "starter_upgrade", i, half.code))


def upgrade(halves, seed, k, pool=None, tier_table=None, lists=None, decks=None, max_tier=DEFAULT_MAX_TIER, flags=None,
            never=()):
    """(every picked half upgraded, in pick order, [Swap]). The same seed and picks give the same result."""
    pool = pool or cardpool.load()
    lists = lists or rules.vintage_lists()
    tier_table = tier_table or tiers.load(pool=pool)
    decks = products.jumpstart() if decks is None else decks
    halves = [trim_half(h, pool, tier_table)[0] for h in halves]       # Round STARTER1C: every half at most 20 cards
    totals = {}
    for h in halves:
        for n, c in h.cards:
            key = cardpool.normal_name(n)
            totals[key] = totals.get(key, 0) + int(c)
    out, swaps = [], []
    for i, h in enumerate(halves):
        new, s = upgrade_half(h, k, pool, tier_table, lists, half_rng(seed, i, h), decks, max_tier, totals, flags,
                              never)
        out.append(new)
        swaps += s
    return out, swaps


def starting_deck(halves, seed, k, pool=None, lists=None, tier_table=None, decks=None, max_tier=DEFAULT_MAX_TIER, flags=None,
                  never=()):
    """([(name, count)], [Swap]) - the player's starting deck: products.starter_deck of the upgraded halves."""
    pool = pool or cardpool.load()
    lists = lists or rules.vintage_lists()
    up, swaps = upgrade(halves, seed, k, pool, tier_table, lists, decks, max_tier, flags, never)
    return products.starter_deck(up, pool, lists), swaps


def upgraded_names(swaps):
    """{name the deck got: [name it replaced, ...]} for the preview's marks."""
    out = {}
    for s in swaps:
        out.setdefault(s.into, []).append(s.out)
    return out
