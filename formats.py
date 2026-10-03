# SPDX-License-Identifier: GPL-3.0-or-later
"""
formats.py - the game formats (Round FMT1). No pygame, no I/O.

Two formats today:
  commander   the default, and what every deck was before this round. 100 cards, 40 life, 21 commander damage.
  brawl       MTG Arena's 100-card Brawl (Karl, 2 Oct 2026: "Arena 100 card brawl"). Forge plays it as its Brawl variant.

Arena's Brawl, as the Comprehensive Rules' Brawl option (903.12, rules of 25 Sep 2026) with Arena's 100-card deck:
  903.12c  the commander is a legendary creature, planeswalker, Vehicle, or Spacecraft with a power/toughness box
  903.12d  (60 cards in paper Brawl; Arena's Brawl is 100, singleton, commander included)
  903.12e  a commander with no colours in its colour identity: any number of basic lands of ONE basic land type
  903.12f  25 life in a two-player game, 30 in a multiplayer game
  903.12h  no commander damage (rule 704.6c isn't used)
  Arena's card pool and banned list: Scryfall's "brawl" legality and banned:brawl (Scryfall's "standardbrawl" is the 60-card one)
  The first mulligan is free: Forge's MulliganService does that for Brawl, and this program's mulligans were free already.

Where a deck's format is written down:
  the deck's .txt file    a "# format: brawl" line (no line = Commander, so every deck from before this round stays Commander).
                          deck_importer skips lines starting with "#".
  the .dck file Forge reads  "Deck Type=Brawl" in [metadata] (a key Forge itself writes and reads: DeckFileHeader.DECK_TYPE).
                          Only a Brawl deck gets it, so a Commander .dck is exactly what it was. A journal, a bug report and
                          replay.py carry the .dck texts, so a resumed or replayed game knows its format from them.
  the engine            java_bridge Main --format brawl; its "ready" line says which format it started.
"""
from collections import namedtuple
import re

Format = namedtuple("Format", "key name deck_size life_two life_pod commander_damage planeswalker_commanders online dck_type")

FORMATS = {
    "commander": Format("commander", "Commander", 100, 40, 40, True, False, True, None),
    "brawl": Format("brawl", "Brawl", 100, 25, 30, False, True, False, "Brawl"),
}
ORDER = ("commander", "brawl")          # the order of the deck screen's format buttons
DEFAULT = "commander"

BLURB = {
    "commander": "Commander: 100 cards, 40 life, 21 commander damage.",
    "brawl": "Brawl (MTG Arena): 100 cards, a legendary creature or planeswalker commander, 25 life (30 in a pod), "
             "no commander damage, Arena's cards and banned list.",
}

_TXT_LINE = re.compile(r"^\s*#\s*format\s*[:=]\s*([A-Za-z]+)\s*$", re.I | re.M)
_DCK_LINE = re.compile(r"^\s*Deck Type\s*=\s*(\S+)\s*$", re.I | re.M)


def normal(key):
    """A known format key ("commander", "brawl"); anything else (None, "", a typo) is the default."""
    k = (key or "").strip().lower()
    return k if k in FORMATS else DEFAULT


def get(key):
    return FORMATS[normal(key)]


def name(key):
    return get(key).name


def starting_life(key, players):
    f = get(key)
    return f.life_two if players <= 2 else f.life_pod


def life_note(key, players):
    """'25 life each' for the deck screen's Opponents box (Brawl only; Commander says nothing new)."""
    if normal(key) == DEFAULT:
        return ""
    return f"{starting_life(key, players)} life each."


# ---- the deck's .txt file -------------------------------------------------------------------------------------------------------
def from_text(text):
    """The format a deck file's text names with a "# format: X" line; Commander when there is none (or X isn't known)."""
    m = _TXT_LINE.search(text or "")
    return normal(m.group(1)) if m else DEFAULT


def declared(text):
    """The format a "# format: X" line names (a known key), or None when the text has no such line."""
    m = _TXT_LINE.search(text or "")
    return normal(m.group(1)) if m else None


def text_line(key):
    """The line a saved deck file starts with: "# format: brawl", or "" for Commander (old files have no line)."""
    k = normal(key)
    return "" if k == DEFAULT else f"# format: {k}"


def with_format(text, key):
    """`text` with its "# format:" line set to `key` (removed for Commander). Other lines are untouched."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body = "\n".join(ln for ln in lines if not _TXT_LINE.match(ln)).strip("\n")
    line = text_line(key)
    return (line + "\n" + body) if line else body


# ---- Forge's .dck file ----------------------------------------------------------------------------------------------------------
def dck_lines(key):
    """Extra [metadata] lines for write_deck_file: ["Deck Type=Brawl"] for Brawl, [] for Commander."""
    t = get(key).dck_type
    return [f"Deck Type={t}"] if t else []


def from_dck(text):
    """The format of a .dck file's text (its "Deck Type=" line); Commander when it has none."""
    m = _DCK_LINE.search(text or "")
    if not m:
        return DEFAULT
    t = m.group(1).strip().lower()
    return next((k for k, f in FORMATS.items() if f.dck_type and f.dck_type.lower() == t), DEFAULT)


def from_dck_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_dck(f.read())
    except (OSError, UnicodeDecodeError, TypeError):
        return DEFAULT
