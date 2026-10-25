# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's Hint button (Round RPG15b, Karl 23 Oct 2026 19:07: "make a hint button that players can press that gives them
hints about where what to do to progress in the game"). No pygame: rpg_screens shows the pages in the dialogue box.

rpg/data/hints.json: {"version": 1, "hints": [{"id", "if": [conditions], "say": [pages]}, ...]} - the first entry whose conditions all
hold is said; the last has none. The words are Claude's draft for Karl to edit.

Conditions: talk's own ("flag:<name>", "!flag:<name>", "quest:<id>=none|active|ready|done"), "!quest:<id>=<state>", and
"map:<map name>" / "!map:<map name>" (the map you stand on) and "phase:<dawn|day|dusk|night>" / "!phase:..." (rpg.clock).
A page may say "{count:<quest id>}" (the fights counted so far, at most the goal) and "{goal:<quest id>}" (the goal's count).
A quest whose goal is already met counts as "ready" here, as it does once you talk to its giver (talk.refresh).
"""
import copy
import json
import os
import re

from rpg import clock, conditions, talk

HERE = os.path.dirname(os.path.abspath(__file__))
HINTS_FILE = os.path.join(HERE, "data", "hints.json")
NAME = "Hint"                            # the dialogue box's name line
MAX_PAGES = 4
FIELD = re.compile(r"\{(count|goal):([a-z0-9_]+)\}")
PHASE_NAMES = clock.PHASE_NAMES


class HintError(ValueError):
    pass


_CACHE = {}


def hints(path=HINTS_FILE):
    """The list of hint entries in the file (read once)."""
    if path not in _CACHE:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("hints"), list):
            raise HintError(f"{os.path.basename(path)} has no hints list")
        _CACHE[path] = data["hints"]
    return _CACHE[path]


def forget():
    _CACHE.clear()


def holds(cond, world, place=None, minutes=None):
    """One condition against the save's world, the map you are on and the time (rpg.conditions' words, Round RPG16)."""
    try:
        return conditions.holds(cond, world, minutes=minutes, place=place)
    except conditions.ConditionError as e:
        raise HintError(str(e)) from None


def _quest_numbers(world, qid, all_quests):
    goal = int(((all_quests.get(qid) or {}).get("goal") or {}).get("count", 1))
    count = int(((world.get("quests") or {}).get(qid) or {}).get("count", 0))
    return min(count, goal), goal


def fill(page, world, all_quests=None):
    """A page's {count:<quest>} and {goal:<quest>} filled in."""
    all_quests = all_quests if all_quests is not None else talk.quests()

    def one(m):
        count, goal = _quest_numbers(world, m.group(2), all_quests)
        return str(count if m.group(1) == "count" else goal)
    return FIELD.sub(one, page)


def hint_for(world, place, minutes=None, entries=None, all_quests=None):
    """(id, [pages]) of the first entry whose conditions hold; (None, []) when none does (the file's last entry always does)."""
    entries = entries if entries is not None else hints()
    all_quests = all_quests if all_quests is not None else talk.quests()
    w = copy.deepcopy(world)
    talk.refresh(w, all_quests)                       # a met goal counts as ready, as it does when you talk to its giver
    for e in entries:
        if all(holds(c, w, place, minutes) for c in e.get("if") or ()):
            return e.get("id"), [fill(p, w, all_quests) for p in e.get("say") or ()]
    return None, []


def check(entries=None, all_quests=None, maps=None, known_flags=None):
    """What is wrong with the hints, as sentences ([] = nothing): unique ids, known conditions (maps that exist, quests and their
    states, flags the game sets), the last entry without conditions, at most MAX_PAGES pages, and every page within the dialogue
    box (talk.PAGE_LINES lines of talk.WRAP characters) with each number at its widest."""
    entries = entries if entries is not None else hints()
    all_quests = all_quests if all_quests is not None else talk.quests()
    out = []
    seen = set()
    for i, e in enumerate(entries):
        where = f"hint {e.get('id') or i + 1}"
        if not e.get("id"):
            out.append(f"{where}: no id")
        elif e["id"] in seen:
            out.append(f"{where}: two hints have this id")
        seen.add(e.get("id"))
        for c in e.get("if") or ():
            body = c[1:] if c.startswith("!") else c
            if conditions.problem(c):
                out.append(f"{where}: unknown condition {c!r}")
                continue
            if body.startswith("map:") and maps is not None and body[4:] not in maps:
                out.append(f"{where}: no map {body[4:]!r}")
            if body.startswith("quest:"):
                qid, _s, st = body[6:].partition("=")
                if qid not in all_quests or st not in talk.STATES:
                    out.append(f"{where}: no quest state {body[6:]!r}")
            if body.startswith("flag:") and known_flags is not None and body[5:] not in known_flags:
                out.append(f"{where}: flag {body[5:]!r} is never set")
        pages = list(e.get("say") or ())
        if not pages:
            out.append(f"{where}: says nothing")
        if len(pages) > MAX_PAGES:
            out.append(f"{where}: {len(pages)} pages ({MAX_PAGES} at most)")
        for p in pages:
            for m in FIELD.finditer(p):
                if m.group(2) not in all_quests:
                    out.append(f"{where}: no quest {m.group(2)!r} for {m.group(0)}")
            widest = FIELD.sub(lambda m: "9" * len(str(_quest_numbers({}, m.group(2), all_quests)[1])), p)
            lines = talk.wrap(widest)
            if len(lines) > talk.PAGE_LINES:
                out.append(f"{where}: a page of {len(lines)} lines: {p[:40]!r}")
    if entries and (entries[-1].get("if") or []):
        out.append("the last hint must have no conditions (it is said when nothing else fits)")
    return out
