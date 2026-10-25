# SPDX-License-Identifier: GPL-3.0-or-later
"""Random fights and what the encounter screen says about a foe (Round RPG13, the Adventure remaster's fourth round; Karl's
decision 6, 23 Oct 2026: "Both: visible foes on the overworld and outskirts; random encounters inside dungeons";
claude/SPEC_RPG_SLICE_2026-10-23.md, section RPG13). No pygame here: rpg_encounter.py draws the screen.

A map with random fights has two properties (Tiled map properties; tools/rpg_make_maps.py writes them from a layout's head):
  encounter_rate   the average number of steps (tiles walked) between two fights, at least MIN_GAP + 1.
  encounters       which foes, and how often each: "plague_rat 3, grave_robber 1" (enemy kind ids, whole-number weights).
A step is a tile walked (rpg.explore.Walker.walked, in map pixels, over the tile's width): pushing against a wall, standing
still and being carried through an exit are not steps.

How far the next fight is, in steps, is drawn when the last one starts (or when you first walk on such a map): a whole number
between MIN_GAP and 2 x rate - MIN_GAP, every value as likely - so the average is the rate, two fights are never closer than
MIN_GAP steps, and the longest wait is bounded (Final Fantasy's own counter has no upper bound; mine is my choice). The draws
come from the save's rng (rpg.save.Save.rng), so a game's fights can be replayed. The steps still to go are kept in the save
(world["danger"]), so saving and loading neither brings a fight closer nor pushes it away.

The spec's numbers, my estimates (the FF4 and FF6 feel): about one fight every 40-60 steps (the Warrens: rate 50), never two
within 12 steps.

kind_text(kind) is what the encounter screen says about a foe: where it lives ("home"), what its deck does ("plan") and its
line ("taunt"), from rpg/data/kinds_text.json - placeholders of mine until the dialogue lane's text (claude/
RPG_SLICE_DIALOGUE_2026-10-23.md in the Project, same shape) replaces the file; anything missing falls back to the kind's own
data.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
KINDS_TEXT_FILE = os.path.join(DATA, "kinds_text.json")
ENEMY_KINDS_FILE = os.path.join(DATA, "enemy_kinds.json")
MIN_GAP = 12                    # steps: never two random fights closer than this - the spec's number (my estimate)
RANDOM_ENCOUNTER = "dungeon"    # a random fight is a dungeon fight: rpg.economy's rewards, the region's deck list for dungeons
HOME_MAX, PLAN_MAX, TAUNT_MAX = 40, 60, 60
COLOUR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


class EncounterError(ValueError):
    pass


class RandomFoe:
    """A foe met at random: what rpg.explore.Explorer.step returns as ("random", foe), and what rpg.adventure.start_duel takes
    in place of a map trigger. It has no spot on the map, so winning clears nothing and losing sends you to town as usual."""
    __slots__ = ("enemy",)
    kind = "encounter"
    encounter = RANDOM_ENCOUNTER
    once = False
    spot = None
    random = True
    wander = False
    tag = "AMBUSH"                  # what the encounter screen writes beside where it lives
    backdrop = None                 # None: the map's

    def __init__(self, enemy):
        self.enemy = enemy

    def __repr__(self):
        return f"{type(self).__name__}({self.enemy!r})"


class RematchFoe(RandomFoe):
    """Round RPG14: a foe the duel hall's keeper lets you fight again (rpg.talk, once the boss is beaten) - a regular fight, in
    the hall (the dungeon backdrop), with no spot on the map."""
    __slots__ = ()
    encounter = "regular"
    tag = "REMATCH"
    backdrop = "dungeon"


def parse_table(text):
    """'plague_rat 3, grave_robber 1' -> [("plague_rat", 3), ("grave_robber", 1)]. A kind without a number weighs 1.
    EncounterError says what is wrong."""
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"([a-z][a-z0-9_]*)(?:\s+(\d+))?", part)
        if not m:
            raise EncounterError(f"encounters: {part!r} isn't 'kind weight' (e.g. 'plague_rat 3')")
        kind, weight = m.group(1), int(m.group(2) or 1)
        if weight < 1:
            raise EncounterError(f"encounters: {kind} weighs {weight}; a weight is at least 1")
        if any(k == kind for k, _w in out):
            raise EncounterError(f"encounters: {kind} is listed twice")
        out.append((kind, weight))
    if not out:
        raise EncounterError("encounters: no foes listed")
    return out


def check_rate(rate):
    """The rate as a whole number of steps, or EncounterError."""
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate != int(rate):
        raise EncounterError(f"encounter_rate {rate!r} isn't a whole number of steps")
    rate = int(rate)
    if rate <= MIN_GAP:
        raise EncounterError(f"encounter_rate {rate} must be more than {MIN_GAP} (two fights are never closer than that)")
    return rate


def next_gap(rng, rate):
    """Steps to the next fight: MIN_GAP .. 2 x rate - MIN_GAP, every value as likely (the average is the rate)."""
    return rng.randint(MIN_GAP, 2 * check_rate(rate) - MIN_GAP)


def pick(rng, table):
    """One kind from [(kind, weight)], as likely as its weight."""
    kinds = [k for k, _w in table]
    return rng.choices(kinds, weights=[w for _k, w in table])[0]


# ---- what the encounter screen says --------------------------------------------------------------------------------------------

_CACHE = {}


def _load(path):
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _CACHE:
        try:
            with open(path, encoding="utf-8") as f:
                _CACHE[key] = json.load(f)
        except (OSError, ValueError):
            _CACHE[key] = {}
    return _CACHE[key]


def kind_data(kind_id, path=None):
    """The kind's entry in rpg/data/enemy_kinds.json ({} when it isn't there)."""
    data = _load(path or ENEMY_KINDS_FILE)
    return next((k for k in data.get("kinds", []) if k.get("id") == kind_id), {})


def kind_text(kind_id, path=None, kinds_path=None):
    """{"home", "plan", "taunt"} (+ "intro"/"defeated" pages when the file has them) for the encounter screen. A missing
    file or field falls back to the kind's data: its colours for the plan, nothing for the home and the taunt."""
    text = dict((_load(path or KINDS_TEXT_FILE).get("kinds") or {}).get(kind_id) or {})
    if not text.get("plan"):
        kd = kind_data(kind_id, kinds_path)
        cols = colour_names(kd.get("colour", ""))
        text["plan"] = f"Plays {cols}." if cols else ""
    text.setdefault("home", "")
    text.setdefault("taunt", "")
    return text


def colour_names(code):
    """'BR' -> 'Black and Red'; '' -> ''."""
    names = [COLOUR_NAMES[c] for c in str(code or "") if c in COLOUR_NAMES]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
