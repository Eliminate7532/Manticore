# SPDX-License-Identifier: GPL-3.0-or-later
"""
draft_deck.py - a 60-card Commander Draft deck from a drafted pool, and the check of one (Round CD1). No pygame, no I/O.

The rules (Comprehensive Rules effective 25 Sep 2026):
  903.13e  the cards a player drafted become that player's card pool
  903.13f  Commander deck construction (903.5) with three exceptions: at least 60 cards, no maximum; any number of cards with the
           same name from the pool; (Commander Masters' partner rule - not used: a cube isn't Commander Masters boosters)
  903.5c   a card can be in the deck only if every colour of its colour identity is in the commander's
  903.3    the commander: a legendary creature, Vehicle or Spacecraft with power/toughness, or a card that says it can be;
  702.124  two commanders only with matching partner abilities (legality.pair_ok)
Basic lands: 903.13 doesn't say. The rules' glossary defines a draft as building "solely from the chosen cards and basic land cards"
and 100.2b says the same for limited play, so basics are free here (Claude's reading, 9 Oct; Arena gives them free too). A basic still
has a colour identity, so only the commander's colours' basics (Wastes for a colourless commander).

The builder is the AIs' (and, through "Auto-build", the player's - Karl's answer 9): pick the commander (or pair) whose colours hold the
most good cards of the pool, then the best cards of those colours by Cube Cobra Elo (a little less for a top-heavy curve), the pool's
lands of those colours, and basics split by the colours' mana symbols until the deck has DECK_MIN cards. Cards Forge's developers mark
"AI:RemoveDeck:All" go in only when nothing else of those colours is left (Claude's default, 9 Oct). LANDS = 25: the middle of
"Recommended land count is between 24 to 26 lands" on EsMag's Commander Cube page (cubecobra.com/cube/overview/Command, read 9 Oct) -
Commander Cube (cm), the default, says nothing about lands.

Round CD3, the classic draft's 40-card deck (style "classic"; Comprehensive Rules effective 25 Sep 2026):
  100.2b   "In limited play ... each deck has a minimum deck size of 40 cards. A limited deck may contain as many duplicates of a
           card as are included with the product."
  Draft    (glossary) "construct a deck solely from the chosen cards and basic land cards" - the basics are the cube's own
           (cube_source.basic_station: Wastes only in the Devoid Cube, snow basics in the Spooty Peasant Cube).
build_classic tries every one- and two-colour pair the cube has basics for (and colourless) and keeps the deck whose spells are
best by the same value as above: CLASSIC_SPELLS spells, the pool's lands of those colours, basics to CLASSIC_SIZE. 17 lands in 40
cards is the usual limited count (Claude's default, 12 Oct, general knowledge - not from a cube's page; most of the stock cubes say
nothing about land counts). No splash: a third colour is left for the player to add by hand.
"""
from collections import namedtuple
from itertools import combinations
import statistics

import cube_source as cs

DECK_MIN = 60                            # 903.13f(1)
LANDS = 25
LANDS_MAX = 26                           # round CD4a: the top of "24 to 26 lands" (EsMag's page): a deck a spell or so short plays more
TOP_END_CMC = 6                          # mana value from which a card costs a little more (Claude's estimate)
TOP_END_COST = 0.25                      # per point of mana value over TOP_END_CMC - 1, in Elo standard deviations
AVOID_COST = 5.0                         # an AI-flagged card is used only after every other card of the colours
SHORT_COST = 3.0                         # per basic land that only stands in for a missing spell (choosing the commander);
                                         # round CD4a: 1.5 -> 3.0 (tools/draft_sim.py, docs/rounds/CD4a.md)
COMMANDER_WEIGHT = 1.5                   # the commander's own strength, when choosing between commanders
CLASSIC_SIZE = 40                        # round CD3: 100.2b
CLASSIC_LANDS = 17                       # round CD3: the usual limited land count (Claude's default)
COLOUR_WORDS = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}

Deck = namedtuple("Deck", "commanders spells lands basics identity score short")


def zscores(cards):
    """Each card's Elo in standard deviations from this cube's mean (the AIs' and the builder's card value)."""
    elos = [c.elo for c in cards]
    if not elos:
        return []
    mean = statistics.fmean(elos)
    sd = statistics.pstdev(elos) or 1.0
    return [(e - mean) / sd for e in elos]


def identity_of(cards, commanders):
    ident = set()
    for i in commanders:
        ident |= set(cards[i].identity)
    return frozenset(ident)


def fits(card, ident):
    return set(card.identity) <= set(ident)


def _is_background(card):
    return "background" in card.type_line.lower() and "legendary" in card.type_line.lower()


def pair_ok(a, b):
    try:
        import legality
        ok, _unknown, _why = legality.pair_ok(cs.scryfall_like(a), cs.scryfall_like(b), a.name, b.name)
        return bool(ok)
    except Exception:
        return False


def commander_options(cards, pool):
    """Every commander or legal pair in the pool, as tuples of card ints (one per name)."""
    seen, singles, backgrounds = set(), [], []
    for i in pool:
        c = cards[i]
        if c.name in seen:
            continue
        seen.add(c.name)
        if c.commander:
            singles.append(i)
        elif _is_background(c):
            backgrounds.append(i)
    out = [(i,) for i in singles]
    partners = singles + backgrounds
    for x in range(len(partners)):
        for y in range(x + 1, len(partners)):
            a, b = partners[x], partners[y]
            if (cards[a].commander or cards[b].commander) and pair_ok(cards[a], cards[b]):
                out.append((a, b))
    return out


def spell_value(card, z, avoid=False):
    v = z - TOP_END_COST * max(0.0, card.cmc - (TOP_END_CMC - 1))
    return v - AVOID_COST if avoid else v


def pips(card):
    """{colour: count} of the coloured mana symbols in its cost (a hybrid or Phyrexian symbol counts half for each colour)."""
    out = {}
    for sym in card.cost:
        cols = [ch.upper() for ch in sym if ch in "wubrg"]
        if not cols:
            continue
        share = 1.0 if (len(cols) == 1 and "p" not in sym) else 0.5
        for col in cols:
            out[col] = out.get(col, 0) + share
    return out


def split_basics(cards, spells, ident, count, names=None):
    """{"Forest": n, ...} - `count` basics split by the spells' mana symbols (largest remainder), at least one of each colour of
    the identity when there is room. A colourless identity gets Wastes. Round CD3: `names` = {colour: basic land name} (the cube's
    own basics, cube_source.basic_station); the ordinary ones when None."""
    names = names or cs.BASIC_NAME
    if count <= 0:
        return {}
    cols = [c for c in "WUBRG" if c in ident]
    if not cols:
        return {names.get("", cs.BASIC_NAME[""]): count}
    weight = {c: 0.0 for c in cols}
    for i in spells:
        for col, n in pips(cards[i]).items():
            if col in weight:
                weight[col] += n
    if sum(weight.values()) == 0:
        weight = {c: 1.0 for c in cols}
    base = {c: (1 if count >= len(cols) else 0) for c in cols}
    left = count - sum(base.values())
    total = sum(weight.values())
    raw = {c: left * weight[c] / total for c in cols}
    floor = {c: int(raw[c]) for c in cols}
    rest = left - sum(floor.values())
    for c in sorted(cols, key=lambda c: (-(raw[c] - floor[c]), cols.index(c)))[:rest]:
        floor[c] += 1
    return {names.get(c, cs.BASIC_NAME[c]): base[c] + floor[c] for c in cols if base[c] + floor[c] > 0}


def build_for(cards, pool, commanders, z=None, avoid=frozenset(), size=DECK_MIN, lands=LANDS):
    """The deck for these commanders from this pool (a Deck). Never fewer than `size` cards: missing spells become basics.
    Round CD4a: a deck short of spells with the default 25 lands plays up to LANDS_MAX (26) lands first, inside the 24-26 its source
    recommends; only what is still missing then counts as short."""
    deck = _build_for(cards, pool, commanders, z, avoid, size, lands)
    if deck.short and lands == LANDS and lands < LANDS_MAX:
        deck = _build_for(cards, pool, commanders, z, avoid, size, min(LANDS_MAX, lands + deck.short))
    return deck


def _build_for(cards, pool, commanders, z=None, avoid=frozenset(), size=DECK_MIN, lands=LANDS):
    z = z if z is not None else zscores(cards)
    ident = identity_of(cards, commanders)
    used = set(commanders)
    names_used = {cards[i].name for i in commanders}
    spells_avail, lands_avail = [], []
    for i in pool:
        c = cards[i]
        if i in used or c.name in names_used or not fits(c, ident):
            continue
        (lands_avail if cs.is_land(c) else spells_avail).append(i)
    need_spells = size - lands - len(commanders)
    spells_avail.sort(key=lambda i: -spell_value(cards[i], z[i], cards[i].name in avoid))
    spells = spells_avail[:need_spells]
    short = max(0, need_spells - len(spells))
    lands_avail.sort(key=lambda i: -(z[i] - (AVOID_COST if cards[i].name in avoid else 0)))
    land_cards = [i for i in lands_avail if not cs.is_basic(cards[i])][:lands]
    basics_wanted = size - len(commanders) - len(spells) - len(land_cards)
    basics = split_basics(cards, spells, ident, basics_wanted)
    score = sum(spell_value(cards[i], z[i], cards[i].name in avoid) for i in spells) \
        + COMMANDER_WEIGHT * sum(z[i] for i in commanders) - SHORT_COST * short
    return Deck(tuple(commanders), tuple(spells), tuple(land_cards), basics, ident, score, short)


def build(cards, pool, z=None, avoid=frozenset(), size=DECK_MIN, lands=LANDS, commanders=None):
    """The best deck this pool makes (the commander chosen too, unless given). None when the pool has no commander."""
    z = z if z is not None else zscores(cards)
    options = [tuple(commanders)] if commanders else commander_options(cards, pool)
    best = None
    for opt in options:
        deck = build_for(cards, pool, opt, z, avoid, size, lands)
        if best is None or deck.score > best.score:
            best = deck
    return best


# ---- round CD3: the classic draft's 40-card deck ----------------------------------------------------------------------------
def colour_options(station):
    """Every colour choice a classic deck can make with this cube's basics: colourless, each colour, each pair of colours."""
    cols = [c for c in "WUBRG" if c in (station or cs.BASIC_NAME)]
    return [frozenset()] + [frozenset(c) for c in cols] + [frozenset(p) for p in combinations(cols, 2)]


def build_colours(cards, pool, colours, z=None, avoid=frozenset(), station=None, size=CLASSIC_SIZE, lands=CLASSIC_LANDS):
    """The classic deck in these colours from this pool (a Deck with no commanders; identity = the colours). Never fewer than
    `size` cards: missing spells become basics (short), as in build_for."""
    z = z if z is not None else zscores(cards)
    station = station or cs.BASIC_NAME
    ident = frozenset(colours)
    spells_avail, lands_avail = [], []
    for i in pool:
        c = cards[i]
        if not fits(c, ident):
            continue
        (lands_avail if cs.is_land(c) else spells_avail).append(i)
    need_spells = size - lands
    spells_avail.sort(key=lambda i: -spell_value(cards[i], z[i], cards[i].name in avoid))
    spells = spells_avail[:need_spells]
    short = max(0, need_spells - len(spells))
    # the pool's lands: those that make one of the deck's colours first, then colourless ones; at least 3 basics a colour stay
    lands_avail = [i for i in lands_avail if not cs.is_basic(cards[i])]
    lands_avail.sort(key=lambda i: (-bool(set(cards[i].identity) & ident), -(z[i] - (AVOID_COST if cards[i].name in avoid else 0))))
    land_cards = lands_avail[:max(0, lands - 3 * len(ident))]
    basics = split_basics(cards, spells, ident, size - len(spells) - len(land_cards), station)
    score = sum(spell_value(cards[i], z[i], cards[i].name in avoid) for i in spells) - SHORT_COST * short
    return Deck((), tuple(spells), tuple(land_cards), basics, ident, score, short)


def build_classic(cards, pool, z=None, avoid=frozenset(), station=None, size=CLASSIC_SIZE, lands=CLASSIC_LANDS, colours=None):
    """The best classic deck this pool makes: every colour choice the cube's basics allow (colour_options), unless `colours` is
    given. Never None - a pool with nothing playable still gets basics to 40."""
    z = z if z is not None else zscores(cards)
    options = [frozenset(colours)] if colours is not None else colour_options(station)
    best = None
    for opt in options:
        deck = build_colours(cards, pool, opt, z, avoid, station, size, lands)
        if best is None or deck.score > best.score:
            best = deck
    return best


def check_classic(cards, pool, main, station=None, size=CLASSIC_SIZE):
    """Problems (plain sentences) with a classic draft deck: `main` holds card ints from the pool or basic land names (str). An
    empty list = legal (100.2b: at least 40, as many of a card as were drafted; the cube's own basic lands)."""
    station = station or cs.BASIC_NAME
    basics = {n.lower() for n in station.values()}
    problems = []
    pool_count = {}
    for i in pool:
        pool_count[i] = pool_count.get(i, 0) + 1
    used = {}
    for x in main:
        if isinstance(x, str):
            if x.strip().lower() not in basics:
                problems.append(f"{x} isn't one of this cube's basic lands.")
            continue
        used[x] = used.get(x, 0) + 1
    for i, n in used.items():
        if n > pool_count.get(i, 0):
            problems.append(f"{cards[i].name}: {n} in the deck, {pool_count.get(i, 0)} drafted.")
    if len(main) < size:
        problems.append(f"The deck has {len(main)} cards; it needs at least {size}.")
    return list(dict.fromkeys(problems))


def colour_name(ident):
    """'Blue-Red', 'Green', 'Colourless' - a classic deck's colours in words (its name in My decks)."""
    cols = [c for c in "WUBRG" if c in (ident or ())]
    return "-".join(COLOUR_WORDS[c] for c in cols) or "Colourless"


def deck_size(deck):
    return len(deck.commanders) + len(deck.spells) + len(deck.lands) + sum(deck.basics.values())


def deck_names(cards, deck):
    """(commander names, every other card's name - basics repeated): what forge_client.write_deck_file takes."""
    main = [cards[i].name for i in deck.spells + deck.lands]
    for name, n in deck.basics.items():
        main += [name] * n
    return [cards[i].name for i in deck.commanders], main


BASIC_IDENTITY = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G", "wastes": ""}


def check(cards, pool, commanders, main, size=DECK_MIN):
    """Problems (plain sentences) with a Commander Draft deck: `commanders` are card ints from the pool, `main` the other cards -
    card ints from the pool, or basic land names (str). An empty list = legal."""
    problems = []
    if not commanders:
        problems.append("Choose a commander from your drafted cards.")
    if len(commanders) > 2:
        problems.append("Only one or two commanders are allowed.")
    pool_count = {}
    for i in pool:
        pool_count[i] = pool_count.get(i, 0) + 1
    for i in commanders:
        if i not in pool_count:
            problems.append(f"{cards[i].name} isn't one of your drafted cards.")
    if len(commanders) == 1 and not cards[commanders[0]].commander:
        problems.append(f"{cards[commanders[0]].name} can't be a commander.")
    if len(commanders) == 2:
        a, b = (cards[i] for i in commanders)
        if not ((a.commander or b.commander) and pair_ok(a, b)):
            problems.append(f"{a.name} and {b.name} can't be commanders together.")
    ident = identity_of(cards, commanders)
    used = {}
    for x in main:
        if isinstance(x, str):
            col = BASIC_IDENTITY.get(x.strip().lower())
            if col is None:
                problems.append(f"{x} isn't a basic land.")
            elif col and col not in ident:                          # Wastes (no colour) fits any commander
                problems.append(f"{x} is outside your commander's colours.")
            continue
        used[x] = used.get(x, 0) + 1
    for i in commanders:
        used[i] = used.get(i, 0) + 1
    for i, n in used.items():
        if n > pool_count.get(i, 0):
            problems.append(f"{cards[i].name}: {n} in the deck, {pool_count.get(i, 0)} drafted.")
        elif i not in commanders and not fits(cards[i], ident):
            problems.append(f"{cards[i].name} is outside your commander's colours.")
    total = len(commanders) + len(main)
    if total < size:
        problems.append(f"The deck has {total} cards; it needs at least {size} (commander included).")
    return list(dict.fromkeys(problems))                       # ten Plains outside the colours is one problem, not ten
