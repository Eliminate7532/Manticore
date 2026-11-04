# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's progression, part 2: VISIBLE REWARDS (Round PROG2; Karl, 2 Nov 2026: after a win "pick 1 of 3 reward cards" from
"the beaten foe's deck, your colours first"; the design is the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.2).
No pygame: rpg_results.py draws the choice; every rule is here, once.

    deck_colours(deck, pool)          the colours a player's deck can make: its cards' colours and its basic lands' types
    castable(card, colours)           THE "your deck can cast it" rule: every colour of the card is one of the deck's (colourless
                                      cards always)
    offer_cards(...)                  the cards a win offers: n groups of `per` (progression.json rewards.choices_per_card), each
                                      group one reward card to pick - from the beaten foe's deck (inside the region's band, its
                                      handicap's swapped cards too: rpg.duel.reward_cards' rules), castable ones first, different
                                      names where possible; the region's band at large when the deck has too few
    signature_card(deck, ...)         a boss's "Guards: <card>" - the highest-tier card of the deck he plays
    choose(save, picks)               the choice made: the picked cards into the collection (save.pending_pick cleared)
    default_picks(pending)            the first card of each group (the screen shows them picked until you pick another)

Readings of mine (Karl's to change, each in one place):
  - "Can cast" is by the card pool's colours (rpg/data/cards.json has colours, not colour identity): a card fits when each of its
    colours is one of the deck's. The deck's colours are its non-land cards' colours plus the colours its basic lands make.
  - A group's first card is its best: castable first, then the deck's weighting (more copies, more likely), as before. The screen
    shows the first of each group picked, so Continue at once takes those (a player in a hurry gets the old one-card reward, but
    castable).
  - The signature card is the deck's highest-tier non-land card (copies, then the name, break a tie); lands (the Grave Priest's
    highest tier is Bojuka Bog, a tier-4 land), banned cards and cards no tier table knows are never it - lands are left out as
    the deck-power meter leaves them out. It is not held to the region's band (the handover: "the highest-tier card of the deck he will play").
"""
from rpg import cardpool

BASIC_COLOURS = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}


def deck_colours(deck, pool):
    """The colours ("W", "U", "B", "R", "G") a deck's cards are and its basic lands make, as a sorted string ("" for none)."""
    out = set()
    pairs = list((deck or {}).get("main") or [])
    for name, _k in pairs:
        card = pool.get(name)
        if card is None:
            continue
        if card.is_land:
            for land, c in BASIC_COLOURS.items():
                if land in card.type_line:
                    out.add(c)
            continue
        out |= {c for c in card.colours if c in "WUBRG"}
    return "".join(c for c in "WUBRG" if c in out)


def castable(card, colours):
    """THE rule: a card the deck can cast - colourless, or every colour of it one of the deck's."""
    if card is None:
        return False
    cols = [c for c in (card.colours or "C") if c in "WUBRG"]
    return all(c in colours for c in cols)


def offer_cards(save, enemy_deck, n, band, pool, tier_table, lists, numbers, colours, per=3):
    """[[name, ...], ...] - n groups of `per` cards (fewer when nothing more can be found). Drawn with the save's rng ("reward")."""
    from rpg.duel import _band_ok
    if n <= 0 or per <= 0:
        return []
    rng = save.rng("reward")
    skip = numbers.shop_skip_prefixes
    weights = {}
    for name, k in list(enemy_deck.get("main", [])) + list(enemy_deck.get("sideboard", [])):
        if _band_ok(name, pool, tier_table, lists, band, skip):
            shown = pool.get(name).name
            weights[shown] = weights.get(shown, 0) + int(k)
    want = n * per
    order = []
    for first in (True, False):                     # the castable cards first, then the rest of the deck's
        part = {x: w for x, w in weights.items() if castable(pool.get(x), colours) == first}
        while part and len(order) < want:
            names = sorted(part)
            pick = rng.choices(names, weights=[part[x] for x in names])[0]
            del part[pick]
            order.append(pick)
    if len(order) < want:                           # the region's band at large: castable first, then any
        from rpg.shop import candidates
        for first in (True, False):
            tiers_in_band = list(range(band[0], band[1] + 1))
            while len(order) < want and tiers_in_band:
                t = rng.choice(tiers_in_band)
                names = [x for x in candidates(t, "", pool, tier_table, lists, skip)
                         if x not in order and castable(pool.get(x), colours) == first]
                if not names:
                    tiers_in_band.remove(t)
                    continue
                order.append(rng.choice(names))
    # deal them into the groups so each group's first is one of the best (round robin: 1st, 2nd .. nth best lead the groups)
    groups = [[] for _ in range(n)]
    for i, name in enumerate(order):
        groups[i % n].append(name)
    return [g for g in groups if g]


def default_picks(pending):
    """The first card of each group."""
    return [g[0] for g in (pending or {}).get("groups") or () if g]


def check_picks(pending, picks):
    """None when `picks` (a list of names, one per group, in the groups' order) is a valid choice; else the sentence why not."""
    groups = (pending or {}).get("groups") or []
    if not isinstance(picks, (list, tuple)) or len(picks) != len(groups):
        return f"Pick one card from each of the {len(groups)} groups."
    for g, p in zip(groups, picks):
        if p not in g:
            return f"{p!r} isn't one of that group's cards."
    return None


def choose(save, picks):
    """The choice made: each picked card into the collection (the Card Book's log and the bestiary with it); save.pending_pick is
    cleared. Returns [(name, "deck")] given. ValueError (a sentence) for a choice that doesn't fit the groups."""
    pending = save.pending_pick
    if not pending:
        raise ValueError("There is no reward to choose.")
    why = check_picks(pending, picks)
    if why:
        raise ValueError(why)
    from rpg import progress
    given = []
    for name in picks:
        save.collection.add(name, 1)
        given.append((name, "deck"))
    kind = pending.get("kind")
    if kind:
        progress.note_beaten(save, kind, list(picks))
    save.pending_pick = None
    return given


def signature_card(deck, pool, tier_table, lists):
    """The boss's "Guards: <card>": the highest-tier non-land card of `deck`'s main (copies, then the name, break a tie); None when no card
    of it has a tier."""
    best, key = None, None
    copies = {}
    for name, k in (deck or {}).get("main") or ():
        card = pool.get(name)
        if card is None or card.is_land or lists.is_banned(name):     # lands left out, as the deck-power meter does
            continue
        copies[card.name] = copies.get(card.name, 0) + int(k)
    for name, k in copies.items():
        t = tier_table.tier_of(name)
        if t is None:
            continue
        here = (-t, -k, cardpool.normal_name(name))
        if key is None or here < key:
            best, key = name, here
    return best
