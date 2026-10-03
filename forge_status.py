# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_status.py - the "status badges" on a player's panel: monarch, initiative, the Ring, emblems and other effects.

Forge keeps these as little effect cards in the player's COMMAND ZONE (found live: "The Monarch", "The Initiative"). The table only
ever drew the commanders from that zone, so a player could become the monarch and nothing on screen said so. This file turns the
non-commander cards of the command zone into a short list of badges. It has no pygame in it, so it is easy to test.
"""
from collections import namedtuple

# key: which badge (also picks its colours); label: the chip text; short: the text when there is very little room;
# text: what the tooltip says. KNOWN is keyed by the effect card's name in lower case.
Badge = namedtuple("Badge", "key label short text")

KNOWN = {
    "the monarch": Badge(
        "monarch", "Monarch", "Mon",
        "The monarch: draws an extra card at the beginning of their end step. Whoever deals combat damage to the monarch with a "
        "creature becomes the monarch."),
    "the initiative": Badge(
        "initiative", "Initiative", "Ini",
        "Has the initiative: ventures into the Undercity at the beginning of their upkeep. Whoever deals combat damage to that "
        "player with a creature takes the initiative."),
    "the ring": Badge(
        "ring", "The Ring", "Ring",
        "The Ring has tempted this player: one of their creatures is the Ring-bearer (legendary, and it gets more abilities each "
        "time the Ring tempts them again)."),
}

MAX_NAMED_OTHERS = 1          # with more effects than this the extras are folded into one "Effects N" chip


def _plain(text):
    return " ".join((text or "").replace("\r", " ").replace("\n", " ").split())


def badges_for(command_cards, commander_ids):
    """Badges for every card in a player's command zone that is not one of their commanders.
    monarch / initiative / Ring first (in that order), then emblems and other effects, folded into one chip when there are several."""
    commanders = set(commander_ids or [])
    known, others = {}, []
    for card in command_cards or []:
        if card.get("id") in commanders or card.get("commander"):
            continue
        name = _plain(card.get("name")) or "Effect"
        hit = KNOWN.get(name.lower())
        if hit:
            known.setdefault(hit.key, hit)
        else:
            others.append((name, _plain(card.get("text"))))
    out = [b for b in KNOWN.values() if b.key in known]
    if len(others) > MAX_NAMED_OTHERS:
        names = ", ".join(n for n, _ in others)
        out.append(Badge("effects", f"Effects {len(others)}", f"Fx {len(others)}", f"Effects in this player's command zone: {names}."))
    else:
        for name, text in others:
            label = name[4:] if name.lower().startswith("the ") else name
            emblem = "emblem" in name.lower()
            detail = text or ("An emblem: it stays for the rest of the game." if emblem else "An effect kept in the command zone.")
            out.append(Badge("emblem" if emblem else "effects", label, label[:4], f"{name}: {detail}"))
    return out
