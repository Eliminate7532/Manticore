# SPDX-License-Identifier: GPL-3.0-or-later
"""The player's cards (RPG plan decision 2, "Classic Shandalar: start weak, win and buy cards"; Karl, 11 Oct: the collection lives
only in the RPG's save, the main deck screen stays as it is).

A Collection counts copies by card. Names are kept as the card pool writes them (so a save file reads like a deck list) and looked
up by cardpool.normal_name, so "lim-dul's vault" finds "Lim-Dûl's Vault". Basic lands are free and never counted when numbers.json
says basic_lands_free (RPG4's first choice, as on MTG Arena). Round RPG7 (Karl, 13 Oct): they aren't any more - basic lands are
counted like any card, bought in shops (rpg.shop, 1 gold each) and found in packs; a save keeps which rule it was made under.

A deck is two lists of [name, count] (main and sideboard). problems() is the RPG's deck rules (rpg/rules.py) plus one more: you
must own every copy the deck uses. Several decks may share the same copies (a deck is a list, not a set of cards taken out); a card
can't be SOLD while any saved deck uses more copies than would be left (sell_problem).
"""
from rpg import cardpool, rules


class Collection:
    def __init__(self, counts=None, pool=None, basics_free=True):
        self.pool = pool
        self.basics_free = basics_free
        self._n = {}           # normal name -> count
        self._name = {}        # normal name -> display name
        for name, k in (counts or {}).items():
            self.add(name, k)

    def _key(self, name):
        card = self.pool.get(name) if self.pool is not None else None
        shown = card.name if card is not None else name
        return cardpool.normal_name(shown), shown

    def is_free(self, name):
        if not self.basics_free or self.pool is None:
            return False
        card = self.pool.get(name)
        return card is not None and card.is_basic

    def count(self, name):
        """Copies owned; None = unlimited (a free basic land)."""
        if self.is_free(name):
            return None
        return self._n.get(self._key(name)[0], 0)

    def add(self, name, k=1):
        k = int(k)
        if k < 0:
            raise ValueError("add: a negative count")
        if k == 0 or self.is_free(name):
            return
        key, shown = self._key(name)
        self._n[key] = self._n.get(key, 0) + k
        self._name.setdefault(key, shown)

    def remove(self, name, k=1):
        k = int(k)
        key, _shown = self._key(name)
        have = self._n.get(key, 0)
        if k < 0 or k > have:
            raise ValueError(f"remove: {name} x{k}, but only {have} owned")
        if have == k:
            del self._n[key]
            self._name.pop(key, None)
        else:
            self._n[key] = have - k

    def total(self):
        return sum(self._n.values())

    def names(self):
        return sorted(self._name.values(), key=str.lower)

    def to_dict(self):
        return {self._name[k]: n for k, n in sorted(self._n.items())}

    def __len__(self):
        return len(self._n)


def deck_counts(deck):
    """{normal name: (display name, copies)} over main and sideboard together (the four-card rule counts both)."""
    out = {}
    for part in ("main", "sideboard"):
        for n, k in deck.get(part, []):
            key = cardpool.normal_name(n)
            name, have = out.get(key, (n, 0))
            out[key] = (name, have + int(k))
    return out


def owned_problems(deck, collection):
    """[rules.Problem] for copies the deck uses that the collection doesn't hold (free basics never)."""
    short = []
    for _key, (name, k) in sorted(deck_counts(deck).items()):
        have = collection.count(name)
        if have is not None and k > have:
            short.append(f"{name} (the deck uses {k}, you own {have})")
    if not short:
        return []
    return [rules.Problem("owned", "You don't own enough copies: " + ", ".join(short) + ".", short)]


def problems(deck, collection, pool=None, lists=None):
    """Everything wrong with a deck: the RPG's deck rules, then ownership. [] = it can be played."""
    pool = pool or collection.pool or cardpool.load()
    return rules.check(deck.get("main", []), deck.get("sideboard", []), pool, lists) + owned_problems(deck, collection)


def sell_problem(name, k, collection, decks):
    """Why `k` copies of `name` can't be sold (a sentence), or None. Refused when you don't own them, when the card is free, or
    when a saved deck would be left using more copies than you'd own."""
    if collection.is_free(name):
        return f"{name} is a free basic land - there is nothing to sell."
    have = collection.count(name)
    if k < 1 or k > have:
        return f"You own {have} {name}; you can't sell {k}."
    left = have - k
    key = cardpool.normal_name(collection._key(name)[1])
    for d in decks:
        used = deck_counts(d).get(key)
        if used and used[1] > left:
            return f"Your deck '{d.get('name', '?')}' uses {used[1]} {name} - take them out first."
    return None
