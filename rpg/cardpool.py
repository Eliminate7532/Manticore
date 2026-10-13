# SPDX-License-Identifier: GPL-3.0-or-later
"""Every card Forge can play, once each (RPG plan decision 7: the card pool is every card Forge can play).

rpg/data/cards.json is written by tools/rpg_build_data.py from Forge's own card database (forge.rpgtools.RpgData catalogue:
StaticData's common cards, one entry per name). It is Forge's data, reshaped: GPL like the rest. One row per card:
    [name, type line, mana value, colours ("W", "UB", ... or "C"), lowest rarity, copy limit]
lowest rarity = the most common rarity any printing had (C, U, R, M, S = special, L = basic land); copy limit = 0 (the usual four),
-1 ("A deck can have any number of cards named ...") or N ("... up to N cards named ...").

A card's name is matched without regard to case. "Fire/Ice" (how some of Forge's own data writes a split card) is read as
"Fire // Ice".
"""
import json
import os
import unicodedata

from rpg import data_path

CARDS_FILE = data_path("cards.json")
RARITY_ORDER = {"L": 0, "C": 1, "U": 2, "R": 3, "M": 4, "S": 5}
RARITY_WORDS = {"L": "basic land", "C": "common", "U": "uncommon", "R": "rare", "M": "mythic", "S": "special"}


class Card:
    __slots__ = ("name", "type_line", "mana_value", "colours", "rarity", "copy_limit")

    def __init__(self, name, type_line, mana_value, colours, rarity, copy_limit=0):
        self.name = name
        self.type_line = type_line
        self.mana_value = int(mana_value or 0)
        self.colours = colours or "C"
        self.rarity = rarity or "C"
        self.copy_limit = int(copy_limit or 0)

    @property
    def is_basic(self):
        """A basic land (Forge's type line starts "Basic"): no copy limit, tier 0."""
        return self.type_line.startswith("Basic")

    @property
    def is_land(self):
        return "Land" in self.type_line.split(" - ")[0]

    def __repr__(self):
        return f"Card({self.name!r}, {self.type_line!r}, {self.mana_value}, {self.colours!r}, {self.rarity!r})"


_SAME = str.maketrans({"\u00e6": "ae", "\u00c6": "Ae", "\u2019": "'", "\u2018": "'"})


def normal_name(name):
    """The key a card is found by: lower case, accents dropped ("Nazgul" finds Nazgûl, "AEther Vial" Æther Vial), curly
    apostrophes straight, "A/B" and "A // B" alike, surrounding spaces gone."""
    n = " ".join(str(name or "").translate(_SAME).split())
    if "/" in n and " // " not in n:
        n = " // ".join(p.strip() for p in n.split("/"))
    n = "".join(ch for ch in unicodedata.normalize("NFKD", n) if not unicodedata.combining(ch))
    return n.lower()


class CardPool:
    def __init__(self, rows, forge=""):
        self.forge = forge
        self.cards = {}
        for row in rows:
            c = Card(*row[:6])
            self.cards[normal_name(c.name)] = c

    def __len__(self):
        return len(self.cards)

    def __contains__(self, name):
        return self.get(name) is not None

    def get(self, name):
        """The card, or None. A name Forge's other data writes as "Front // Back" for a card the pool knows by its front face
        alone (a flip card: "Bushi Tenderfoot // Kenzo the Hardhearted") is found by the front face."""
        key = normal_name(name)
        card = self.cards.get(key)
        if card is None and " // " in key:
            card = self.cards.get(key.split(" // ")[0])
        return card

    def names(self):
        return sorted(c.name for c in self.cards.values())


_POOL = None


def load(path=None):
    """The pool from cards.json (kept after the first call when no path is given)."""
    global _POOL
    if path is None and _POOL is not None:
        return _POOL
    with open(path or CARDS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    pool = CardPool(data["cards"], data.get("forge", ""))
    if path is None:
        _POOL = pool
    return pool


def write(path, catalogue, forge_version):
    """cards.json from the Java tool's catalogue (build time): the rows without the per-set printing lists."""
    rows = [c[:6] for c in catalogue["cards"]]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write('{"forge": %s,\n "about": %s,\n "cards": [\n' % (
            json.dumps(forge_version), json.dumps("Every card Forge can play, from Forge's card database "
                                                 "(tools/rpg_build_data.py). Row: name, type line, mana value, colours, "
                                                 "lowest rarity, copy limit (0 normal, -1 any number, N up to N).")))
        f.write(",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows))
        f.write("\n]}\n")
    os.replace(tmp, path)
    return len(rows)
