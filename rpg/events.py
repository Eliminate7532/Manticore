# SPDX-License-Identifier: GPL-3.0-or-later
"""Random events on the map (Round RPG16, claude/SPEC_RPG16_DAYNIGHT_2026-10-23.md section 2.6; Karl 23 Oct 18:55: "maybe even
rare spawns and random events"). No pygame: rpg_screens says the pages in the dialogue box.

rpg/data/events.json: {"version": 1, "events": {id: {"title", "map", "where": [col0, row0, col1, row1] (optional, inclusive),
"when": [conditions], "weight", "cooldown_days" | "once": true, "pages": [...], and either "effect": {...} or "choice": [yes label,
no label] with "yes" / "no": {"pages": [...], effect keys}}}}. Effect keys: "gold" (n), "card" ("rare_from_band"), "flag" (name),
"minutes" (game minutes passed). No event starts a fight.

When one happens (the pattern of RPG13's random fights): a map with events has the property event_rate (average tiles walked
between checks). The tiles to the next check are drawn from save.rng("event:gap"), every value from EVENT_MIN_GAP to
2 x rate - EVENT_MIN_GAP as likely, and kept in world["events"]["gap"]. At a check: the map's events whose `when` and `where` hold,
whose cooldown has passed and which are not a finished `once`; one of them by weight from save.rng("event:pick").

What the save keeps: world["events"] = {"gap": tiles, "last": {id: minutes}, "done": [ids]} (a bad value is treated as absent).

Round RPG16d (the RPG16c review's T2-T5, T9): events.json is Karl's to edit by hand and the game never runs check() on it, so what
the game reads goes through check()'s own rules: an entry whose `where`, `when`, cooldown or weight can't be used is said once in
crash_log.txt and left out of the checks (a cooldown or weight that isn't a number is read as if it were missing; Round RPG17b,
Karl 24 Oct: a `where` that can't be read is read as absent - the event can happen anywhere on its map); an effect value
check() would refuse (a flag that isn't a word, gold that isn't a whole number above 0, a card other than rare_from_band, minutes
that aren't a number above 0) is said once and not given; minutes over a day are a day. Nothing is ever raised to the walk or the
event's box.
"""
import json
import math
import os

from rpg import clock, conditions, notes

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS_FILE = os.path.join(HERE, "data", "events.json")
EVENT_MIN_GAP = 100                  # the fewest tiles between two checks - my estimate
EFFECT_KEYS = ("gold", "card", "flag", "minutes")
MAX_PAGES = 4


class EventError(ValueError):
    pass


_CACHE = {}


def events(path=EVENTS_FILE):
    if path not in _CACHE:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("events"), dict):
            raise EventError(f"{os.path.basename(path)} has no events table")
        _CACHE[path] = data["events"]
    return _CACHE[path]


def forget():
    _CACHE.clear()


def effect_of(e, answer=None):
    """The effect dict of an event (answer None) or of its "yes" / "no" answer. Round RPG16d (review T5): an effect or an answer
    that isn't a table ("effect": 5, "yes": "Thanks for the gold") has no effects - {} - instead of raising TypeError."""
    if not isinstance(e, dict):
        return {}
    if answer is None:
        eff = e.get("effect")
        return dict(eff) if isinstance(eff, dict) else {}
    part = e.get(answer)
    return {k: part[k] for k in EFFECT_KEYS if k in part} if isinstance(part, dict) else {}


def pages_of(e, answer=None):
    """The pages an event (answer None) or its answer says. Round RPG16d (review T5): an answer that isn't a table says nothing
    (it raised AttributeError out of the event's box); a "pages" that is one sentence is that one page, not a page per letter."""
    part = e if answer is None else (e.get(answer) if isinstance(e, dict) else None)
    pages = part.get("pages") if isinstance(part, dict) else None
    if isinstance(pages, str):
        return [pages]
    return list(pages) if isinstance(pages, (list, tuple)) else []


def choice_of(eid, e):
    """Round RPG16d: the event's two answers, or None. check()'s rule is two answers that are words; anything else (one answer,
    numbers - the box measures each with len()) is said once and the event is said without a choice."""
    ch = e.get("choice") if isinstance(e, dict) else None
    if ch is None:
        return None
    if isinstance(ch, list) and len(ch) == 2 and all(isinstance(x, str) and x.strip() for x in ch):
        return tuple(ch)
    notes.once(f"event:{eid}:choice", f"events.json: {eid}'s choice {ch!r} is not two answers - said without a choice")
    return None


def usable_effect(eid, eff, answer=None):
    """Round RPG16d (review T2, T3, T9): the part of an effect the game can give, by check()'s effect rules. A value check() would
    refuse is said once in crash_log.txt and left out - a flag that isn't a word (a list poisoned the save's flags: T2), gold that
    isn't a whole number above 0 (a toll took the save below 0, which can't be loaded: T3), a card other than rare_from_band,
    minutes that aren't a number above 0; minutes over a day are a day (T9: no sum the clock can't hold)."""
    where = f"events.json: {eid}" + (f" {answer}" if answer else "")
    key = f"event:{eid}:{answer or 'effect'}"
    eff = eff if isinstance(eff, dict) else {}
    out = {}
    if "gold" in eff:
        g = eff["gold"]
        if isinstance(g, int) and not isinstance(g, bool) and g > 0:
            out["gold"] = g
        else:
            notes.once(key + ":gold", f"{where}: gold {g!r} is not a whole number above 0 - no gold is given or taken")
    if "card" in eff:
        if eff["card"] == "rare_from_band":
            out["card"] = eff["card"]
        else:
            notes.once(key + ":card", f"{where}: card {eff['card']!r} is not rare_from_band - no card is given")
    if "flag" in eff:
        f = eff["flag"]
        if isinstance(f, str) and f:
            out["flag"] = f
        else:
            notes.once(key + ":flag", f"{where}: flag {f!r} is not a word - no flag is set")
    if "minutes" in eff:
        m = eff["minutes"]
        if isinstance(m, (int, float)) and not isinstance(m, bool) and math.isfinite(m) and m > 0:
            if m > clock.DAY:
                notes.once(key + ":minutes", f"{where}: minutes {m!r} is more than a day - a day passes")
                m = clock.DAY
            out["minutes"] = m
        else:
            notes.once(key + ":minutes", f"{where}: minutes {m!r} is not a number above 0 - no time passes")
    return out


def flags_set(entries=None):
    """Every flag an event can set (talk.check's "is never set" knows them)."""
    entries = entries if entries is not None else events()
    out = set()
    for e in entries.values():
        if not isinstance(e, dict):
            continue
        for part in (e.get("effect"), e.get("yes"), e.get("no")):
            if isinstance(part, dict) and isinstance(part.get("flag"), str) and part["flag"]:    # Round RPG16d: only words
                out.add(part["flag"])
    return out


def check(entries=None, maps=None, known_flags=None, wrap=None, page_lines=None):
    """What is wrong with the events, as sentences ([] = nothing). maps: {name: (width, height)} in tiles, to check `where`;
    wrap/page_lines: the dialogue box's (rpg.talk), to check every page fits."""
    entries = entries if entries is not None else events()
    out = []

    def pages_ok(where, pages):
        if not pages:
            out.append(f"{where}: says nothing")
        if len(pages) > MAX_PAGES:
            out.append(f"{where}: {len(pages)} pages ({MAX_PAGES} at most)")
        if wrap is not None:
            for p in pages:
                if len(wrap(p)) > page_lines:
                    out.append(f"{where}: a page of {len(wrap(p))} lines: {p[:40]!r}")

    def effect_ok(where, eff):
        if "gold" in eff and not (isinstance(eff["gold"], int) and not isinstance(eff["gold"], bool) and eff["gold"] > 0):
            out.append(f"{where}: gold {eff['gold']!r}")
        if "card" in eff and eff["card"] != "rare_from_band":
            out.append(f"{where}: card {eff['card']!r} (rare_from_band)")
        if "minutes" in eff and not (isinstance(eff["minutes"], (int, float)) and not isinstance(eff["minutes"], bool)
                                     and 0 < eff["minutes"] <= clock.DAY):
            out.append(f"{where}: minutes {eff['minutes']!r} (above 0, at most a day)")
        if "flag" in eff and not (isinstance(eff["flag"], str) and eff["flag"]):
            out.append(f"{where}: flag {eff['flag']!r}")

    for eid, e in entries.items():
        where = f"event {eid}"
        if not isinstance(e, dict):
            out.append(f"{where}: not a table")
            continue
        if not isinstance(e.get("title"), str) or not e["title"].strip():
            out.append(f"{where}: no title")
        if maps is not None and e.get("map") not in maps:
            out.append(f"{where}: no map {e.get('map')!r}")
        box = e.get("where")
        if box is not None:
            ok = (isinstance(box, list) and len(box) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
                  and box[0] <= box[2] and box[1] <= box[3] and min(box) >= 0)
            if ok and maps is not None and e.get("map") in maps:
                w, h = maps[e["map"]]
                ok = box[2] < w and box[3] < h
            if not ok:
                out.append(f"{where}: where {box!r} is not [col0, row0, col1, row1] inside the map")
        for c in e.get("when") or ():
            why = conditions.problem(c)
            if why or c.lstrip("!").startswith("map:"):
                out.append(f"{where}: {why or 'a map word means nothing here (the event names its map)'}")
            elif known_flags is not None and c.lstrip("!").startswith("flag:") and c.split(":", 1)[1] not in known_flags:
                out.append(f"{where}: flag {c.split(':', 1)[1]!r} is never set")
        wt = e.get("weight", 1)
        if not isinstance(wt, (int, float)) or isinstance(wt, bool) or not wt > 0:
            out.append(f"{where}: weight {wt!r}")
        once, cd = e.get("once"), e.get("cooldown_days")
        if once is not True and not (isinstance(cd, (int, float)) and not isinstance(cd, bool) and cd >= 0):
            out.append(f"{where}: needs cooldown_days (0 or more) or once: true")
        pages_ok(where, pages_of(e))
        if "choice" in e:
            ch = e.get("choice")
            if not (isinstance(ch, list) and len(ch) == 2 and all(isinstance(x, str) and x.strip() for x in ch)):
                out.append(f"{where}: choice is two answers")
            if "effect" in e:
                out.append(f"{where}: a choice's effects go in its yes and no")
            for ans in ("yes", "no"):
                if not isinstance(e.get(ans), dict):
                    out.append(f"{where}: no {ans}")
                    continue
                pages_ok(f"{where} {ans}", pages_of(e, ans))
                bad = set(e[ans]) - set(EFFECT_KEYS) - {"pages"}
                if bad:
                    out.append(f"{where} {ans}: unknown {sorted(bad)}")
                effect_ok(f"{where} {ans}", effect_of(e, ans))
        else:
            eff = e.get("effect")
            if not isinstance(eff, dict):
                out.append(f"{where}: needs an effect or a choice")
            else:
                bad = set(eff) - set(EFFECT_KEYS)
                if bad:
                    out.append(f"{where}: unknown effect {sorted(bad)}")
                effect_ok(where, eff)
    return out


def state(world):
    """world["events"], repaired: {"gap": int or None, "last": {id: minutes}, "done": [ids]}."""
    st = world.get("events")
    if not isinstance(st, dict):
        st = world["events"] = {}
    g = st.get("gap")
    if not (isinstance(g, (int, float)) and not isinstance(g, bool) and math.isfinite(g) and g >= 0):
        st["gap"] = None
    last = st.get("last")
    if not isinstance(last, dict):
        last = st["last"] = {}
    for k in [k for k, v in last.items() if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))]:
        del last[k]
    if not isinstance(st.get("done"), list):
        st["done"] = []
    return st


def next_gap(save, rate):
    """The tiles to walk until the next check: from EVENT_MIN_GAP to 2 x rate - EVENT_MIN_GAP, all as likely."""
    hi = max(EVENT_MIN_GAP, 2 * int(rate) - EVENT_MIN_GAP)
    return save.rng("event:gap").randint(EVENT_MIN_GAP, hi)


def in_box(box, tile):
    return box is None or (box[0] <= tile[0] <= box[2] and box[1] <= tile[1] <= box[3])


def _real(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def box_ok(box):
    """Round RPG16d (review T4): whether `where` is a box the game can use - absent (anywhere), or [col0, row0, col1, row1]: four
    numbers, col0 <= col1 and row0 <= row1. [] (meant as "anywhere"), [col, row] (one tile) or a sentence is not."""
    return box is None or (isinstance(box, (list, tuple)) and len(box) == 4 and all(_real(v) for v in box)
                           and box[0] <= box[2] and box[1] <= box[3])


def cooldown_of(eid, e):
    """Round RPG16d: an event's cooldown_days, 0 or more (infinity allowed); anything else is said once and read as 0 (as if it
    were missing - the event may come again at the next check)."""
    v = e.get("cooldown_days")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:      # NaN fails v >= 0; infinity passes
        return float(v)
    notes.once(f"event:{eid}:cooldown", f"events.json: {eid}'s cooldown_days {v!r} is not a number of 0 or more - 0 is used")
    return 0.0


def weight_of(eid, e):
    """Round RPG16d: an event's weight, above 0 (missing: 1); anything else is said once and read as 1."""
    v = e.get("weight", 1)
    if _real(v) and v > 0:
        return float(v)
    notes.once(f"event:{eid}:weight", f"events.json: {eid}'s weight {v!r} is not a number above 0 - 1 is used")
    return 1.0


def _fits(eid, e, world, map_name, tile, t, st):
    if e.get("map") != map_name:
        return False
    box = e.get("where")
    if not box_ok(box):                     # Round RPG17b (Karl 24 Oct, RPG16d's question 3): anywhere on its map, not never
        notes.once(f"event:{eid}:where", f"events.json: {eid}'s where {box!r} is not [col0, row0, col1, row1] - "
                                         "it can happen anywhere on its map")
        box = None
    if not in_box(box, tile):
        return False
    when = e.get("when")
    if when is not None and not isinstance(when, (list, tuple)):
        notes.once(f"event:{eid}:when", f"events.json: {eid}'s when {when!r} is not a list - it never happens")
        return False
    try:
        if not conditions.all_hold(when, world, minutes=t):
            return False
    except conditions.ConditionError as ex:
        notes.once(f"event:{eid}:when", f"events.json: {eid}: {ex} - it never happens")
        return False
    if e.get("once") is True:
        return eid not in st["done"]
    last = st["last"].get(eid)
    return last is None or not t - last < cooldown_of(eid, e) * clock.DAY


def eligible(world, map_name, tile, minutes=None, entries=None):
    """The ids of the events that could happen here and now. Round RPG16d: an entry the game can't read is said once and left
    out; the others are still checked (before, one odd entry stopped every event on every map)."""
    entries = entries if entries is not None else events()
    st = state(world)
    t = clock.now(world) if minutes is None else float(minutes)
    out = []
    for eid, e in entries.items():
        if not isinstance(e, dict):
            notes.once(f"event:{eid}:table", f"events.json: {eid} is not a table - it never happens")
            continue
        try:
            if _fits(eid, e, world, map_name, tile, t, st):
                out.append(eid)
        except (TypeError, ValueError, KeyError, AttributeError, IndexError) as ex:     # what no rule above foresaw
            notes.once(f"event:{eid}:odd", f"events.json: {eid} can't be read ({type(ex).__name__}: {ex}) - it never happens")
    return out


def pick(save, ids, entries=None):
    entries = entries if entries is not None else events()
    if not ids:
        return None
    rng = save.rng("event:pick")
    weights = [weight_of(i, entries[i]) for i in ids]       # Round RPG16d: never float() on a hand-edited weight
    return rng.choices(ids, weights=weights)[0]


def step(save, map_name, tile, rate, minutes=None, entries=None, steps=1.0):
    """`steps` tiles walked (1 by default) on a map with event_rate `rate`: an event id when a check comes up and one fits, else
    None."""
    if not rate or rate <= 0 or not steps > 0:
        return None
    st = state(save.world)
    if st["gap"] is None:
        st["gap"] = next_gap(save, rate)
    st["gap"] = round(st["gap"] - steps, 6)
    if st["gap"] > 1e-9:
        return None
    st["gap"] = next_gap(save, rate)
    return pick(save, eligible(save.world, map_name, tile, minutes, entries), entries)


def phase_notes(path=EVENTS_FILE):
    """{phase: the map's one line when the phase changes while you walk} (rpg/data/events.json "phase_notes")."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    pn = data.get("phase_notes") if isinstance(data, dict) else None
    return {k: v for k, v in pn.items() if isinstance(v, str)} if isinstance(pn, dict) else {}   # RPG16d: a list says nothing


def fired(world, eid, minutes=None, entries=None):
    """The event happened: its time goes into "last", a once event into "done"."""
    e = (entries if entries is not None else events()).get(eid) or {}
    st = state(world)
    st["last"][eid] = round(clock.now(world) if minutes is None else float(minutes), 3)
    if e.get("once") is True and eid not in st["done"]:
        st["done"].append(eid)


def apply(game, eid, answer=None, entries=None):
    """The effects of the event (answer None, "yes" or "no") into the save: gold and card through adventure.give_reward, the flag
    with talk.set_flag, minutes with clock.pass_minutes. Returns the lines that say what was given."""
    from rpg import adventure, talk
    e = (entries if entries is not None else events()).get(eid) or {}
    part = (e.get("effect") if answer is None else e.get(answer)) if isinstance(e, dict) else e
    if part is not None and not isinstance(part, dict):              # Round RPG16d (review T5): "effect": 5, "yes": "a sentence"
        notes.once(f"event:{eid}:{answer or 'effect'}:table",
                   f"events.json: {eid}'s {answer or 'effect'} {part!r} is not a table - it gives nothing"[:300])
    eff = usable_effect(eid, effect_of(e, answer), answer)          # Round RPG16d (review T2, T3, T5, T9)
    world = game.save.world
    lines = adventure.give_reward(game, {k: eff[k] for k in ("gold", "card") if k in eff})
    if eff.get("flag"):
        talk.set_flag(world, eff["flag"])
    if eff.get("minutes"):
        clock.pass_minutes(world, eff["minutes"])
    return lines
