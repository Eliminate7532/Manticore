# SPDX-License-Identifier: GPL-3.0-or-later
"""The RPG's deck rules (RPG plan, decisions 3 and 8; "Rules of play").

A duel is an ordinary 1v1 Constructed game at 20 life. A deck may hold:
  - 60 cards or more in the main deck (Comprehensive Rules 100.2a),
  - a sideboard of at most 15 (100.4a),
  - at most four copies of a card, deck and sideboard together, except basic lands and cards that say otherwise
    ("any number of cards named", "up to N cards named"),
  - one copy at most of each card on Vintage's restricted list, and none of Vintage's banned cards,
  - only cards Forge can play (the card pool, rpg/cardpool.py).
The Vintage lists are read from Forge's own format file, forge_runtime/res/formats/Sanctioned/Vintage.txt, at run time, so a
Forge update brings its list with it.
"""
import os

from rpg import cardpool

FOUR = 4
MIN_DECK = 60
MAX_SIDEBOARD = 15
VINTAGE_FILE = os.path.join("res", "formats", "Sanctioned", "Vintage.txt")


class Lists:
    """Vintage's banned and restricted cards (normal_name keys) and where they were read."""

    def __init__(self, banned=(), restricted=(), source=""):
        self.banned = {cardpool.normal_name(n): n for n in banned}
        self.restricted = {cardpool.normal_name(n): n for n in restricted}
        self.source = source

    def is_banned(self, name):
        return cardpool.normal_name(name) in self.banned

    def is_restricted(self, name):
        return cardpool.normal_name(name) in self.restricted


def runtime_dir():
    import paths
    return os.environ.get("FORGE_RUNTIME") or paths.runtime_dir()


def read_format_file(path):
    """(banned names, restricted names) from a Forge format file: "Banned:a; b; c" and "Restricted:..." lines."""
    banned, restricted = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.partition(":")
            if not sep:
                continue
            names = [n.strip() for n in value.split(";") if n.strip()]
            if key.strip() == "Banned":
                banned += names
            elif key.strip() == "Restricted":
                restricted += names
    return banned, restricted


_LISTS = {}


def vintage_lists(runtime=None):
    runtime = runtime or runtime_dir()
    if runtime not in _LISTS:
        path = os.path.join(runtime, VINTAGE_FILE)
        banned, restricted = read_format_file(path)
        _LISTS[runtime] = Lists(banned, restricted, path)
    return _LISTS[runtime]


class Problem:
    __slots__ = ("kind", "text", "cards")

    def __init__(self, kind, text, cards=()):
        self.kind = kind
        self.text = text
        self.cards = list(cards)

    def __repr__(self):
        return f"Problem({self.kind!r}, {self.text!r})"


def counts(cards):
    """{name: count} from a list of names, a {name: count} dict, or a list of (name, count) pairs."""
    if isinstance(cards, dict):
        return {n: int(k) for n, k in cards.items() if int(k) > 0}
    out = {}
    for c in cards or ():
        if isinstance(c, (tuple, list)):
            n, k = c[0], int(c[1])
        else:
            n, k = c, 1
        out[n] = out.get(n, 0) + k
    return out


def check(main, sideboard=(), pool=None, lists=None):
    """[Problem] for a deck (empty = legal). main / sideboard: names, (name, count) pairs or {name: count}."""
    pool = pool or cardpool.load()
    lists = lists or vintage_lists()
    main_c, side_c = counts(main), counts(sideboard)
    problems = []
    n_main, n_side = sum(main_c.values()), sum(side_c.values())
    if n_main < MIN_DECK:
        problems.append(Problem("size", f"This deck has {n_main} cards; it needs at least {MIN_DECK}."))
    if n_side > MAX_SIDEBOARD:
        problems.append(Problem("sideboard", f"The sideboard has {n_side} cards; it may have at most {MAX_SIDEBOARD}."))
    total = {}
    for n, k in list(main_c.items()) + list(side_c.items()):
        key = cardpool.normal_name(n)
        name, have = total.get(key, (n, 0))
        total[key] = (name, have + k)
    unknown, banned, restricted, over = [], [], [], []
    for key, (name, k) in sorted(total.items()):
        card = pool.get(name)
        if card is None:
            unknown.append(name)
            continue
        if lists.is_banned(name):
            banned.append(card.name)
            continue
        if lists.is_restricted(name):
            if k > 1:
                restricted.append(f"{card.name} x{k}")
            continue
        if card.is_basic or card.copy_limit < 0:
            continue
        limit = card.copy_limit if card.copy_limit > 0 else FOUR
        if k > limit:
            over.append(f"{card.name} x{k}" + (f" (up to {limit})" if limit != FOUR else ""))
    if unknown:
        problems.append(Problem("unknown", "Not a card Forge can play: " + ", ".join(unknown) + ".", unknown))
    if banned:
        problems.append(Problem("banned", "Banned (Vintage's list): " + ", ".join(banned) + ".", banned))
    if restricted:
        problems.append(Problem("restricted", "Restricted to one copy (Vintage's list): " + ", ".join(restricted) + ".",
                                restricted))
    if over:
        problems.append(Problem("copies", "More than four copies: " + ", ".join(over) + ".", over))
    return problems


def is_legal(main, sideboard=(), pool=None, lists=None):
    return not check(main, sideboard, pool, lists)
