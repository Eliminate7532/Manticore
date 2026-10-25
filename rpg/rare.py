# SPDX-License-Identifier: GPL-3.0-or-later
"""Rare spawns (Round RPG16, claude/SPEC_RPG16_DAYNIGHT_2026-10-23.md section 2.5; Karl 23 Oct 18:55: "maybe even rare spawns").
No pygame.

A map places a rare with an object of class "rare" (`G = rare id=old_gorsehide on :` in a layout); everything else is in
rpg/data/rare_spawns.json, one table for Karl to edit:
    {"version": 1, "rare": {id: {"kind", "name", "encounter", ["decks",] ["level",] "when": [conditions], "chance", "cooldown_days",
                                 "home", "taunt", "seen", "reward": {"gold": n} | {"card": "rare_from_band"}}}}

It is out at a time when (1) its `when` holds (rpg.conditions), (2) it was not beaten in the last cooldown_days days, and (3) the
phase's roll comes up: save.stable_rng("rare", id, clock.phase_key(now)).random() < chance - one roll for a whole phase, the same
on every frame and after a reload (stable_rng doesn't move the save's counter), so a reload can't re-roll it.

What the save keeps: world["rare"] = {id: {"beaten_at": game minutes}} (a bad value is treated as absent).
A rare's saved spot is "<map>#rare:<id>" (spot()), so editing the layout never moves it.

Round RPG16d (review T1): the game reads chance and cooldown_days through chance_of() / cooldown_of(), never float() on the raw
value - a hand edit check() would refuse ("25%", "three") is said once in crash_log.txt and the rare stays away (a chance) or
comes back at once (a cooldown, as if there were none); a number out of range is clamped. Nothing is ever raised to the walk.

Round RPG17b (Karl 24 Oct, RPG16d's question 4: "a cooldown from the rare's level - higher level, longer cooldown"): each rare has
a `level`, a whole number from 1 (level_of(); missing counts as 1, anything else is said once and counts as 1). A cooldown_days
that can't be read (missing, "three", NaN, a list) is now `level` days (LEVEL_DAYS a level) instead of 0. A cooldown that can be
read is used as it is (RPG17's 2 days); a negative number is still 0 (a number, read and clamped, as RPG16d had it).
"""
import json
import math
import os

from rpg import clock, conditions, notes

HERE = os.path.dirname(os.path.abspath(__file__))
RARE_FILE = os.path.join(HERE, "data", "rare_spawns.json")
ENCOUNTERS = ("regular", "dungeon")
REWARD_CARDS = ("rare_from_band",)
TEXT_FIELDS = ("name", "home", "taunt", "seen")
LEVEL_DAYS = 1          # Round RPG17b (Karl 24 Oct): a cooldown that can't be read is 1 day per level
DEFAULT_LEVEL = 1       # a missing or unreadable level


class RareError(ValueError):
    pass


_CACHE = {}


def spawns(path=RARE_FILE):
    """{id: entry} from the file (read once)."""
    if path not in _CACHE:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("rare"), dict):
            raise RareError(f"{os.path.basename(path)} has no rare table")
        _CACHE[path] = data["rare"]
    return _CACHE[path]


def forget():
    _CACHE.clear()


def spot(map_name, rid):
    return f"{map_name}#rare:{rid}"


def check(entries=None, kinds=None):
    """What is wrong with the table, as sentences ([] = nothing)."""
    entries = entries if entries is not None else spawns()
    out = []
    for rid, e in entries.items():
        where = f"rare {rid}"
        if not isinstance(e, dict):
            out.append(f"{where}: not a table")
            continue
        if kinds is not None and e.get("kind") not in kinds:
            out.append(f"{where}: kind {e.get('kind')!r} is not a foe kind")
        if e.get("encounter") not in ENCOUNTERS:
            out.append(f"{where}: encounter {e.get('encounter')!r} (regular or dungeon)")
        if "decks" in e and e["decks"] not in ENCOUNTERS:          # Round RPG17: whose deck lists it plays, apart from its pay
            out.append(f"{where}: decks {e.get('decks')!r} (regular or dungeon)")
        for c in e.get("when") or ():
            why = conditions.problem(c)
            if why or c.lstrip("!").startswith("map:"):
                out.append(f"{where}: {why or 'a map word means nothing here'}")
        ch = e.get("chance")
        if not isinstance(ch, (int, float)) or isinstance(ch, bool) or not 0 < ch <= 1:
            out.append(f"{where}: chance {ch!r} (above 0, at most 1)")
        if "level" in e and level_problem(e["level"]):                # Round RPG17b
            out.append(f"{where}: level {e.get('level')!r} (a whole number, 1 or more)")
        cd = e.get("cooldown_days")
        if not isinstance(cd, (int, float)) or isinstance(cd, bool) or cd < 0:
            out.append(f"{where}: cooldown_days {cd!r} (0 or more)")
        for k in TEXT_FIELDS:
            if not isinstance(e.get(k), str) or not e[k].strip():
                out.append(f"{where}: no {k}")
        if isinstance(e.get("name"), str) and e["name"].lower().startswith("the "):
            out.append(f"{where}: the name follows \"You beat the\" - leave its \"The\" out")
        rw = e.get("reward")
        if not isinstance(rw, dict) or not rw or set(rw) - {"gold", "card"} \
                or ("gold" in rw and not (isinstance(rw["gold"], int) and rw["gold"] > 0)) \
                or ("card" in rw and rw["card"] not in REWARD_CARDS):
            out.append(f"{where}: reward {rw!r} ({{\"gold\": n}} or {{\"card\": \"rare_from_band\"}})")
    return out


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v)


def chance_of(rid, e):
    """Round RPG16d (review T1): the entry's chance as a number from 0 to 1. check()'s rule is above 0 and at most 1: a number
    outside is clamped, anything else (missing, "25%", [0.25], NaN) is 0 - the rare never comes out - and either is said once."""
    v = e.get("chance") if isinstance(e, dict) else None
    if _number(v) and 0 < v <= 1:
        return float(v)
    if _number(v):
        c = min(1.0, max(0.0, float(v)))
        notes.once(f"rare:{rid}:chance", f"rare_spawns.json: {rid}'s chance {v!r} is not above 0 and at most 1 - {c:g} is used")
        return c
    notes.once(f"rare:{rid}:chance", f"rare_spawns.json: {rid}'s chance {v!r} is not a number - it never comes out")
    return 0.0


def level_problem(v):
    """Round RPG17b: what check() and level_of() refuse in a `level` - not a whole number of 1 or more (True, 2.5, "2", 0)."""
    return not (isinstance(v, int) and not isinstance(v, bool) and v >= 1)


def level_of(rid, e):
    """Round RPG17b (Karl 24 Oct): the rare's level, a whole number of 1 or more. Missing: 1 (nothing said - the file may leave
    it out). Anything else (0, -1, 2.5, "two", True): said once, and 1 is used."""
    v = e.get("level") if isinstance(e, dict) else None
    if v is None:
        return DEFAULT_LEVEL
    if level_problem(v):
        notes.once(f"rare:{rid}:level", f"rare_spawns.json: {rid}'s level {v!r} is not a whole number of 1 or more - "
                                        f"{DEFAULT_LEVEL} is used")
        return DEFAULT_LEVEL
    return v


def cooldown_of(rid, e):
    """Round RPG16d (review T1): the entry's cooldown_days, 0 or more (infinity is allowed: beaten, it never comes back). A
    negative number is 0. Round RPG17b (Karl 24 Oct): anything else (missing, "three", NaN) is the rare's level in days (1 a
    level) - it was 0 - said once."""
    v = e.get("cooldown_days") if isinstance(e, dict) else None
    if _number(v) and v >= 0:
        return float(v)
    if _number(v):
        notes.once(f"rare:{rid}:cooldown", f"rare_spawns.json: {rid}'s cooldown_days {v!r} is below 0 - 0 is used")
        return 0.0
    days = float(level_of(rid, e) * LEVEL_DAYS)
    notes.once(f"rare:{rid}:cooldown", f"rare_spawns.json: {rid}'s cooldown_days {v!r} is not a number - its level's "
                                       f"{days:g} day{'s' if days != 1 else ''} is used")
    return days


def deck_encounter(rid, e):
    """Round RPG17: the kind of encounter whose deck lists the rare plays (towns.json "enemy_decks"): its "decks" when that is
    "regular" or "dungeon", else its "encounter" (its pay always follows "encounter"). Karl 24 Oct: Old Gorsehide, measured at
    23.6% on his archetype decks (claude/SLICE_NIGHT_DUELS_2026-10-24.md), plays the regular (Jumpstart) decks. A "decks" that
    isn't one of the two is said once and the encounter's lists are used."""
    if not isinstance(e, dict):
        return None
    d = e.get("decks")
    if d is None:
        return e.get("encounter")
    if d in ENCOUNTERS:
        return d
    notes.once(f"rare:{rid}:decks", f"rare_spawns.json: {rid}'s decks {d!r} is not regular or dungeon - its encounter's are used")
    return e.get("encounter")


def beaten_at(world, rid):
    r = world.get("rare") if isinstance(world, dict) else None
    v = (r.get(rid) if isinstance(r, dict) else None) or {}
    t = v.get("beaten_at") if isinstance(v, dict) else None
    return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) else None


def roll(save, rid, minutes):
    """The phase's roll for this rare: the same number for the whole phase, on every call and after a reload."""
    return save.stable_rng("rare", rid, int(clock.phase_key(minutes))).random()


def is_out(save, rid, minutes=None, entries=None):
    """Whether the rare is out now (or at `minutes`)."""
    e = (entries if entries is not None else spawns()).get(rid)
    if not isinstance(e, dict):
        return False
    world = save.world
    t = clock.now(world) if minutes is None else float(minutes)
    try:                                    # Round RPG16d: a `when` that isn't a list is read as the map's words are (parse_when)
        if not conditions.all_hold(conditions.parse_when(e.get("when")), world, minutes=t):
            return False
    except conditions.ConditionError as ex:
        notes.once(f"rare:{rid}:when", f"rare_spawns.json: {rid}: {ex} - it never comes out")
        return False
    b = beaten_at(world, rid)
    if b is not None and t - b < cooldown_of(rid, e) * clock.DAY:      # RPG16d (review T1): never float() on the raw value
        return False
    chance = chance_of(rid, e)
    return chance > 0 and roll(save, rid, t) < chance


def on_beaten(world, rid, minutes=None):
    """A won duel against the rare: it stays away for its cooldown."""
    r = world.get("rare")
    if not isinstance(r, dict):
        r = world["rare"] = {}
    r[rid] = {"beaten_at": round(clock.now(world) if minutes is None else float(minutes), 3)}
