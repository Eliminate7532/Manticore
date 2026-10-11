# SPDX-License-Identifier: GPL-3.0-or-later
"""cue_types.py - which typed sound a card gets (patch 49, AU2).

Karl's 7 Oct review asked for arrivals by creature type ("creatures need different sounds based off creature type, artifacts need to
sound like rings/amulets from diablo, enchantments need a magical flair"), lands by land type ("a quick sample of the type of land
they are") and destruction by permanent type. A typed cue is the parent's name plus a suffix - card.land.artifact, land.play.forest,
destroy.enchantment - and choose() falls back to the parent when the card's type isn't known (its data not loaded) or the typed cue
isn't in sounds/cues.json (a family whose sound hasn't been picked yet). The parents are the commonest case: card.land = a creature
(humanoids and every creature outside a family), destroy = a creature's break, land.play = any other land.

The kind of permanent comes from anim.arrival_kind() (patch 48), the one function that reads a card's type line and flags; the
creature family and the land type from the type line's subtypes ("Legendary Creature - Human Wizard", "Basic Land - Island")."""
import re

import anim

LAND_TYPES = ("forest", "island", "swamp", "mountain", "plains")          # land.play.<type>; anything else is land.play itself
FAMILIES = ("dragon", "demon", "undead", "spirit", "elemental", "vermin", "beast")   # the order decides a creature with two

# Forge's creature types (res/lists/TypeLists.txt), the ones that belong to a family; every other type - Human, Elf, Goblin, Wizard,
# Warrior, Cleric, Soldier, Construct and the rest - is the humanoid / generic sound, card.land itself. Judgement calls, listed so they
# can be moved: Angel and Archon are spirits (heavenly, not beasts); Phoenix, Hydra, Wurm and the sea giants are "dragon" (huge, wings
# or scales); Treefolk, Ooze and Golem are elementals; Horror and Eldrazi are demons; Bird is a beast; Snake and Frog are vermin.
FAMILY_TYPES = {
    "dragon": {"dragon", "drake", "wurm", "hydra", "leviathan", "kraken", "serpent", "dinosaur", "phoenix", "wyvern", "behemoth"},
    "demon": {"demon", "devil", "imp", "horror", "nightmare", "eldrazi", "fiend"},
    "undead": {"zombie", "skeleton", "vampire", "mummy", "wraith", "lich", "ghoul"},
    "spirit": {"spirit", "specter", "shade", "angel", "archon", "illusion", "faerie", "wisp", "kami", "avatar", "incarnation"},
    "elemental": {"elemental", "treefolk", "ooze", "golem", "gargoyle", "djinn", "efreet", "dryad", "salamander"},
    "vermin": {"rat", "insect", "spider", "bat", "squirrel", "snake", "scorpion", "worm", "leech", "slug", "saproling", "fungus", "frog",
               "mite", "mouse", "ferret", "weasel", "rabbit", "crab", "jellyfish", "starfish"},
    "beast": {"beast", "cat", "wolf", "werewolf", "bear", "boar", "elk", "horse", "hound", "dog", "ape", "hyena", "fox", "badger", "lizard",
              "crocodile", "rhino", "elephant", "mammoth", "ox", "bird", "hippo", "wolverine", "gorgon", "manticore", "griffin",
              "hippogriff", "pegasus", "unicorn", "sphinx", "chimera", "cockatrice", "basilisk", "kavu", "lhurgoyf", "fish", "whale",
              "turtle", "octopus", "shark", "squid", "bison", "camel", "goat", "sheep", "yeti", "beeble", "hellion", "jackal",
              "lion", "panther", "tiger", "owl", "raven", "crow", "eagle", "hawk", "bull", "aurochs", "antelope", "armadillo", "beaver",
              "monkey", "otter", "raccoon", "possum", "pig", "nightstalker", "dauthi", "moose", "caribou"},
}
_DASH = re.compile(r"\s+[-—–]\s+")

TYPED = {"card.land": ("artifact", "enchantment") + FAMILIES,             # every typed cue the table can ask for (test_au1 reads it)
         "land.play": LAND_TYPES + ("other",),
         "destroy": ("artifact", "enchantment", "land")}


def subtypes(card):
    """The words after the type line's dash, lower case: "Legendary Creature - Human Wizard" -> ["human", "wizard"]."""
    line = (card or {}).get("type") or ""
    parts = _DASH.split(line, maxsplit=1)
    return [w.lower() for w in parts[1].split()] if len(parts) == 2 else []


def kind(card):
    """creature / artifact / enchantment / planeswalker / land / other, from anim.arrival_kind - a token by its own type."""
    if not card:
        return "other"
    if card.get("token"):
        card = dict(card, token=False)
    return anim.arrival_kind(card)


def creature_family(card):
    """The family a creature's subtypes put it in (FAMILIES order wins), or None: the humanoid / generic sound."""
    words = set(subtypes(card))
    for fam in FAMILIES:
        if words & FAMILY_TYPES[fam]:
            return fam
    return None


def land_type(card):
    """forest / island / swamp / mountain / plains - the first basic land type on the line - else "other"."""
    for w in subtypes(card):
        if w in LAND_TYPES:
            return w
    return "other"


def arrival_suffix(card):
    """What arrived, for card.land.<suffix>: a creature's family (None for humanoids and the rest), artifact, or enchantment
    (planeswalkers and battles too: the magical sound). None when the card isn't known."""
    k = kind(card)
    if k == "creature":
        return creature_family(card)
    if k == "artifact":
        return "artifact"
    if k in ("enchantment", "planeswalker"):
        return "enchantment"
    return None


def destroy_suffix(card):
    """What broke, for destroy.<suffix>: artifact, enchantment (planeswalkers and battles too), land; None for a creature (the parent)
    and for a card that isn't known."""
    k = kind(card)
    if k == "artifact":
        return "artifact"
    if k in ("enchantment", "planeswalker"):
        return "enchantment"
    if k == "land":
        return "land"
    return None


def choose(base, suffix, cues):
    """base.suffix when that cue exists in the manifest, else base."""
    if suffix:
        cid = f"{base}.{suffix}"
        if cid in (cues or {}):
            return cid
    return base


def for_arrival(card, cues):
    """The cue for a permanent entering the battlefield: land.play[.type] for a land, card.land[.suffix] for anything else."""
    if card and (card.get("isLand") or kind(card) == "land"):
        t = land_type(card)
        return choose("land.play", t, cues)
    return choose("card.land", arrival_suffix(card), cues)


def for_destroy(card, cues):
    return choose("destroy", destroy_suffix(card), cues)
