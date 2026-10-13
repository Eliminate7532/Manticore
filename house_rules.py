# SPDX-License-Identifier: GPL-3.0-or-later
"""
house_rules.py - a cube's own rules that Forge doesn't have (Round CD3). No pygame.

Karl, 12 Oct 2026: Turbo Cube is one of the stock cubes, "Build the emblem". Its Cube Cobra page (cubecobra.com/cube/overview/turbo,
read 12 Oct): "Players begin the game with an emblem: All spells and activated abilities cost {2} less."

Each player gets the emblem, and each emblem lowers what ITS player casts and activates by {2}, so every spell and activated ability
in the game costs {2} less - the cube's rule - and never {4} less (two emblems that each said "all spells" would stack). The
Comprehensive Rules (25 Sep 2026): 118.7a "Effects that reduce a cost by an amount of generic mana affect only the generic mana
component of that cost"; 601.2f "If the mana component of the total cost is reduced to nothing by cost reduction effects, it is
considered to be {0}." Forge does the same (CostAdjustment.applyReduceCostAbility at fb4d809: the reduction is capped at what the
cost has left).

Where a house rule is written down - the same three places as a format (formats.py):
  the deck's .txt file   a "# rules: turbo" line (draft_pod writes it on every deck drafted from Turbo Cube)
  Forge's .dck file      "Manticore Rules=turbo" in [metadata]. Forge's DeckFileHeader reads its own keys and leaves others alone
                         (checked live: the deck loads and plays). A resumed game, Undo's rebuild and a bug report's replay start
                         from the saved .dck files, so they get the same rules with no code of their own (forge_client reads it).
  the engine             java_bridge Main --house-rule turbo: HouseRules.java puts an emblem in each player's command zone when the
                         game starts (Forge's GameEventGameStarted, on the game thread, after the zones are set up and before the
                         opening hands). Its "ready" line names the rules applied; an older bridge that doesn't is a fatal error
                         ("its bridge is older than this program"), never a quiet game without the rule.
"""
from collections import namedtuple
import re

Rule = namedtuple("Rule", "key name text cube")

RULES = {
    "turbo": Rule("turbo", "Turbo Cube emblem", "All spells and activated abilities cost {2} less.", "turbo"),
}

_TXT_LINE = re.compile(r"^\s*#\s*rules\s*[:=]\s*([A-Za-z0-9_, ]*)\s*$", re.I | re.M)
_DCK_KEY = "Manticore Rules"
_DCK_LINE = re.compile(r"^\s*Manticore Rules\s*=\s*([A-Za-z0-9_, ]*)\s*$", re.I | re.M)


def normal(keys):
    """The known rule keys among `keys` (a str "a, b", an iterable, or None), lower case, sorted, each once."""
    if keys is None:
        return ()
    if isinstance(keys, str):
        keys = keys.split(",")
    out = {str(k).strip().lower() for k in keys if str(k).strip()}
    return tuple(sorted(k for k in out if k in RULES))


def unknown(keys):
    """The rule keys among `keys` this program doesn't know (a deck from a newer version) - said, not silently dropped."""
    if keys is None:
        return ()
    if isinstance(keys, str):
        keys = keys.split(",")
    out = {str(k).strip().lower() for k in keys if str(k).strip()}
    return tuple(sorted(k for k in out if k not in RULES))


# ---- the deck's .txt file -------------------------------------------------------------------------------------------------------
def from_text(text):
    """The rules a deck file's "# rules: x, y" line names (known keys only); () when it has none."""
    m = _TXT_LINE.search(text or "")
    return normal(m.group(1)) if m else ()


def text_line(keys):
    """The "# rules: turbo" line for a deck file, or "" for none."""
    k = normal(keys)
    return f"# rules: {', '.join(k)}" if k else ""


def from_file(path):
    """The rules a deck .txt file names; () when it names none or can't be read."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_text(f.read())
    except (OSError, UnicodeDecodeError, TypeError):
        return ()


def of_entries(entries):
    """The rules of a game with these decks (DeckEntry-like objects with .path): every rule any of them names. A game is
    started from one draft's decks, so in practice they all say the same."""
    out = set()
    for e in entries:
        if e is not None:
            out |= set(from_file(getattr(e, "path", None)))
    return normal(out)


# ---- Forge's .dck file ----------------------------------------------------------------------------------------------------------
def dck_lines(keys):
    """Extra [metadata] lines for forge_client.write_deck_file: ["Manticore Rules=turbo"], or [] (a .dck exactly as before)."""
    k = normal(keys)
    return [f"{_DCK_KEY}={','.join(k)}"] if k else []


def from_dck(text):
    m = _DCK_LINE.search(text or "")
    return normal(m.group(1)) if m else ()


def from_dck_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_dck(f.read())
    except (OSError, UnicodeDecodeError, TypeError):
        return ()


# ---- the engine and the screens ---------------------------------------------------------------------------------------------------
def engine_args(keys):
    """The bridge's arguments: ["--house-rule", "turbo"] per rule ([] for none: the command line is exactly what it was)."""
    out = []
    for k in normal(keys):
        out += ["--house-rule", k]
    return out


def describe(keys):
    """One line per rule, for the loading screen and the draft window: "Turbo Cube emblem: all spells and activated abilities
    cost {2} less." """
    out = []
    for k in normal(keys):
        r = RULES[k]
        out.append(f"{r.name}: {r.text[0].lower()}{r.text[1:]}")
    return out
