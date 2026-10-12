# SPDX-License-Identifier: GPL-3.0-or-later
"""
formats.py - the game formats (Round FMT1). No pygame, no I/O.

Four formats today (round CD2: Commander Draft):
  commander   the default, and what every deck was before this round. 100 cards, 40 life, 21 commander damage.
  brawl       MTG Arena's 100-card Brawl (Karl, 2 Oct 2026: "Arena 100 card brawl"). Forge plays it as its Brawl variant.
  standard    Round CON1: 60-card Standard (Karl, 2 Oct 2026: "60-card Standard"; and the first step of the RPG plan of 9 Oct, whose
              duels are 60-card Constructed games). Forge plays it as a plain Constructed game: no commander, no variant.
  commander_draft  Round CD2: a deck built from a cube draft's pool (Karl, 9 Oct 2026: "the official Commander Draft (CR 903.13)").
              Forge plays it as Commander (903.13g). It has no button of its own on the deck screen: its decks are listed under
              Commander with a DRAFT tag (screen()), and the cube draft (draft_screens.py) is the only thing that makes them.

Commander Draft, from the Comprehensive Rules effective 25 Sep 2026 (CD1's spec, docs/incoming/cd1/SPEC_CUBE_DRAFT_2026-10-09.md):
  903.13f  deck construction follows 903.5 except: (1) at least 60 cards, no maximum; (2) any number of cards with the same name
           from the player's card pool (draft_deck.check counts the pool's copies; legality.py can't see the pool, so it skips the
           one-copy rule for this format)
  903.13g  the games follow the Commander rules: 40 life (903.7), 21 combat damage from one commander (903.10a)
  No banned list: a cube's cards are its owner's choice (the spec's default, section 1).

Standard, from the Comprehensive Rules effective 25 Sep 2026 (read at media.wizards.com, 9 Oct 2026):
  100.2a  at least 60 cards; at most four cards with the same English name, basic lands excepted
  100.4a  a sideboard of at most fifteen cards; the four-card limit counts the deck and the sideboard together
  103.4   each player starts with 20 life
  103.5c  the first mulligan is free in a multiplayer game (and in Brawl) - so not in a two-player Standard game
  The card pool and the banned list: Scryfall's "standard" legality and banned:standard.

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
  the engine            java_bridge Main --format brawl; its "ready" line says which format it started. Round CON1: the
                          engine's name for a format is engine(key) - "constructed" for Standard (the bridge only knows how a
                          game is played, not which cards a format allows; the deck screen checks those).
  Round CON1: Standard's .dck says "Deck Type=Constructed" (Forge's own value for a 60-card deck).
"""
from collections import namedtuple
import re

# Round CON1 added the last five fields:
#   commander  the deck has a commander (False: every card is in the main deck, there is no command zone)
#   copies     at most this many cards with one name (basic lands and "a deck can have any number" cards excepted); round CD2:
#              None = any number (Commander Draft: as many as the pool holds - 903.13f(2))
#   sideboard  at most this many sideboard cards (0: a sideboard isn't part of the format)
#   exact      the deck has exactly deck_size cards (True) or at least deck_size (False)
#   engine     the bridge's --format: how Forge plays it ("commander", "brawl", "constructed")
Format = namedtuple("Format", "key name deck_size life_two life_pod commander_damage planeswalker_commanders online dck_type "
                              "commander copies sideboard exact engine")

FORMATS = {
    "commander": Format("commander", "Commander", 100, 40, 40, True, False, True, None, True, 1, 0, True, "commander"),
    "brawl": Format("brawl", "Brawl", 100, 25, 30, False, True, False, "Brawl", True, 1, 0, True, "brawl"),
    "standard": Format("standard", "Standard", 60, 20, 20, False, False, False, "Constructed", False, 4, 15, False, "constructed"),
    # Round CD2: online off until CD5 (drafting with friends); no Deck Type line (Forge's .dck is a Commander one).
    "commander_draft": Format("commander_draft", "Commander Draft", 60, 40, 40, True, False, False, None, True, None, 0, False,
                              "commander"),
}
ORDER = ("commander", "brawl", "standard")          # the order of the deck screen's format buttons
DEFAULT = "commander"
SCREEN = {"commander_draft": "commander"}           # round CD2: a format listed under another one's deck-screen button
DRAFTED = frozenset({"commander_draft"})             # round CD2: formats whose decks come from a draft's pool (no banned list)

BLURB = {
    "commander": "Commander: 100 cards, 40 life, 21 commander damage.",
    "brawl": "Brawl (MTG Arena): 100 cards, a legendary creature or planeswalker commander, 25 life (30 in a pod), "
             "no commander damage, Arena's cards and banned list.",
    "standard": "Standard: 60 cards or more, up to 4 of a card, no commander, 20 life, Standard's cards and banned list.",
    "commander_draft": "Commander Draft: a commander and 59 or more cards from your draft's pool, 40 life, 21 commander damage.",
}

_TXT_LINE = re.compile(r"^\s*#\s*format\s*[:=]\s*([A-Za-z_]+)\s*$", re.I | re.M)      # round CD2: "commander_draft"
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


def has_commander(key):
    """Round CON1: does a deck of this format have a commander (and a game a command zone)?"""
    return get(key).commander


def engine(key):
    """Round CON1: the bridge's --format for this format: "commander", "brawl" or "constructed"."""
    return get(key).engine


def screen(key):
    """Round CD2: the deck-screen format button a format's decks are listed under ("commander_draft" -> "commander")."""
    k = normal(key)
    return SCREEN.get(k, k)


def drafted(key):
    """Round CD2: True for a format whose decks come from a draft's pool (Commander Draft)."""
    return normal(key) in DRAFTED


def any_copies(key):
    """Round CD2: True when the format allows any number of cards with one name (Commander Draft, from the pool)."""
    return get(key).copies is None


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
