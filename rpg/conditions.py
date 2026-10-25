# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's condition words, in one place (Round RPG16, claude/SPEC_RPG16_DAYNIGHT_2026-10-23.md section 2.3). No pygame.

The words npcs.json's "when" lists, the hints, map objects' `when=` and the events use:

    flag:<name>            the save has that flag            !flag:<name>    it doesn't
    quest:<id>=<state>     the quest is in that state        !quest:...      it isn't   (state: none, active, ready, done)
    phase:<p>[|<p>...]     the clock's phase is one of them  !phase:...      none of them   (p: dawn, day, dusk, night)
    map:<name>             you stand on that map (only where a place is known: the hints)   !map:...

    problem(cond)                   None when the words read, else a sentence (what talk.check and Area.check say)
    holds(cond, world, minutes, place)   True/False; a condition with a problem raises ConditionError
    all_hold(conds, world, ...)     every one holds (an empty list holds)
    parse_when(text)                a map object's "a,b" -> ["a", "b"]

Quest ids, map names and flag names are checked by the callers that know them (talk.check, hints.check, Area.check).
"""
from rpg import clock

STATES = ("none", "active", "ready", "done")
WORDS = ("flag", "quest", "phase", "map")


class ConditionError(ValueError):
    pass


def _parts(cond):
    if not isinstance(cond, str):
        return None, None, None
    neg = cond.startswith("!")
    word, sep, rest = (cond[1:] if neg else cond).partition(":")
    return (neg, word, rest) if sep else (neg, None, None)


def problem(cond):
    """None when the condition reads, else what is wrong with it."""
    neg, word, rest = _parts(cond)
    if word not in WORDS:
        return f"unknown condition {cond!r}"
    if not rest:
        return f"{cond!r} names nothing"
    if word == "quest":
        qid, sep, state = rest.partition("=")
        if not sep or not qid or state not in STATES:
            return f"{cond!r}: a quest condition is quest:<id>=none|active|ready|done"
    if word == "phase":
        bad = [p for p in rest.split("|") if p not in clock.PHASE_NAMES]
        if bad:
            return f"{cond!r}: unknown phase {bad[0]!r} (dawn, day, dusk or night)"
    return None


def flags(world):
    """The save's flags. Round RPG16d (review T2): only the words in it - a list or a number among them (a save an older copy
    wrote) is skipped, not hashed (set() of a list raised on every refresh)."""
    f = world.get("flags") if isinstance(world, dict) else None
    return {x for x in f if isinstance(x, str)} if isinstance(f, (list, tuple, set)) else set()


def quest_state(world, qid):
    qs = world.get("quests") if isinstance(world, dict) else None
    q = qs.get(qid) if isinstance(qs, dict) else None
    return q.get("state", "none") if isinstance(q, dict) else "none"


def holds(cond, world, minutes=None, place=None):
    """One condition against the save's world. minutes: the time to judge a phase at (default: the save's clock). place: the map
    you stand on (a map condition with no place known holds never, and its negation always)."""
    why = problem(cond)
    if why:
        raise ConditionError(why)
    neg, word, rest = _parts(cond)
    if word == "flag":
        ok = rest in flags(world)
    elif word == "quest":
        qid, _s, state = rest.partition("=")
        ok = quest_state(world, qid) == state
    elif word == "phase":
        t = clock.now(world) if minutes is None else minutes
        ok = clock.phase(t) in rest.split("|")
    else:                                                   # map
        ok = place is not None and place == rest
    return ok != neg


def all_hold(conds, world, minutes=None, place=None):
    return all(holds(c, world, minutes, place) for c in conds or ())


def parse_when(text):
    """A map object's `when` property ("phase:night,!flag:met_elder") as a list; a list is returned as it is."""
    if isinstance(text, (list, tuple)):
        return [str(c).strip() for c in text if str(c).strip()]
    return [c.strip() for c in str(text or "").split(",") if c.strip()]
