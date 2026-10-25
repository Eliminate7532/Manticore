# SPDX-License-Identifier: GPL-3.0-or-later
"""Talking to people, quests and the inn (Round RPG14, the Adventure remaster's fifth round; claude/SPEC_RPG_SLICE_2026-10-23.md,
section RPG14). No pygame here: rpg_screens draws the dialogue box (rpg_ui.DialogueBox) and walks a Conversation.

The words live in rpg/data: the dialogue lane's draft (claude/RPG_SLICE_DIALOGUE_2026-10-23.md, its four files copied byte for
byte - their SHA-256 match the lane's), placeholders for Karl to edit:
  npcs.json     {"version": 1, "npcs": {id: {"name", "lines": [{"when": [conditions], "pages": [...]}, ...]}}} - the first entry
                whose conditions all hold is said; the last has none.
  quests.json   {"version": 1, "quests": {id: {"title", "giver", "goal", "offer", "yes", "no", "accepted", "declined",
                "progress", "ready", "done", "reward", "journal"}}}; goal {"beat": kind, "count": n} or {"flag": name}; reward
                {"gold": n} / {"card": "rare_from_band"} / {"flag": name}, any of them.
  inn.json      {"version": 1, "rest": [pages], "woke": [pages]}.
Conditions: rpg.conditions' words - "flag:<name>", "quest:<id>=none|active|ready|done" and (Round RPG16) "phase:<p>[|<p>]",
each with "!" for "not".

What the save keeps (save.world): "flags" [names]; "quests" {id: {"state": "active" | "ready" | "done", "count": n}} (a quest not
there is "none"); "inn" {"map", "spawn"} - where a lost duel wakes you (Round RPG14: the inn you last rested at).
Flags the game sets: "met_<npc id>" (after the first talk), "warrens_entered" (rpg_screens, the first step into the Warrens),
"grave_priest_beaten" (on_duel_won: the boss duel won), and any quest reward's flag.

Who says what (Conversation):
  - A quest giver whose quest isn't done: the first-meeting words (only the first time), then the quest's offer (with its Yes / No
    choice) when it is new, its progress when it is under way, its "ready" words, the reward and its "done" words when the goal
    is met. A quest whose
    goal is met is "ready" at once (a flag goal checked on talking; a fight goal counted by on_duel_won).
  - Everyone else, and a giver once the quest is done: their first matching entry.
  - The innkeeper (INNKEEPER): her words, then "Sleep until morning" / "Not now" (Round RPG15c); sleeping says the inn's "rest"
    pages, the clock goes on to 07:00, the game is saved and the inn becomes your return point. Round RPG17 (Karl 24 Oct Q5): by
    day the choice is "Rest until dusk" / "Not now" instead - the clock goes on to 18:00, the rest the same.
  - The duel hall's keeper (HALL_KEEPER), once the boss is beaten: his words, then "A rematch?" - Yes starts a duel against one of
    the kinds you have beaten (the encounter screen; rpg_screens).
"""
import json
import os
import textwrap

from rpg import clock, conditions

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
NPCS_FILE = os.path.join(DATA, "npcs.json")
QUESTS_FILE = os.path.join(DATA, "quests.json")
INN_FILE = os.path.join(DATA, "inn.json")
STATES = ("none", "active", "ready", "done")
GAME_FLAGS = ("warrens_entered", "grave_priest_beaten", "has_orrins_map")
INNKEEPER = "innkeeper"
HALL_KEEPER = "hall_keeper"
BOSS_FLAG = "grave_priest_beaten"
BOSS_KIND = "grave_priest"
WARRENS_FLAG = "warrens_entered"
REMATCH_PAGE = "A rematch, then? The hall remembers every foe you've beaten."
REMATCH_YES, REMATCH_NO = "A rematch", "Not now"
REST_YES, REST_NO = "Sleep until morning", "Not now"    # Round RPG15c (review R3): the innkeeper asks before you sleep
SLEEPY_PHASES = ("dusk", "night", "dawn")              # ... the answer Enter picks: Sleep at these hours, Not now by day
REST_DUSK = "Rest until dusk"                          # Round RPG17 (Karl 24 Oct Q5): by day the innkeeper offers this instead
WRAP = 34                              # characters a dialogue line holds (the lane's rule: a page is at most 3 such lines)
PAGE_LINES = 3


class TalkError(ValueError):
    pass


_CACHE = {}


def _load(path):
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _CACHE:
        with open(path, encoding="utf-8") as f:
            _CACHE[key] = json.load(f)
    return _CACHE[key]


def npcs(path=None):
    return (_load(path or NPCS_FILE).get("npcs") or {})


def quests(path=None):
    return (_load(path or QUESTS_FILE).get("quests") or {})


def inn(path=None):
    return _load(path or INN_FILE) or {}


# ---- the save's flags and quests --------------------------------------------------------------------------------------------------

def flags(world):
    return conditions.flags(world)          # Round RPG16d (review T2): only the words in it, one rule for both modules


def set_flag(world, name):
    """Round RPG16d (review T2): only a word is a flag - anything else (a list from a hand-edited reward) would make every later
    set() of the flags raise; it is left out (rpg.events says which entry it came from)."""
    if not (isinstance(name, str) and name):
        return
    fl = world.get("flags")
    if not isinstance(fl, list):
        fl = world["flags"] = [f for f in fl if isinstance(f, str) and f] if isinstance(fl, (tuple, set)) else []
    if name not in fl:
        fl.append(name)


def quest_state(world, qid):
    q = (world.get("quests") or {}).get(qid)
    return q.get("state", "none") if isinstance(q, dict) else "none"


def _quest(world, qid):
    return world.setdefault("quests", {}).setdefault(qid, {"state": "active", "count": 0})


def holds(cond, world):
    """One condition string against the save's world: rpg.conditions' words (Round RPG16: flag, quest and phase, each with "!";
    a map word never holds here - nobody's words depend on where you stand)."""
    try:
        return conditions.holds(cond, world)
    except conditions.ConditionError as e:
        raise TalkError(str(e)) from None


def entry_for(npc_id, world, people=None):
    """(name, pages) of the person's first entry whose conditions all hold."""
    people = people if people is not None else npcs()
    npc = people.get(npc_id)
    if not npc:
        raise TalkError(f"no words for {npc_id!r}")
    for e in npc.get("lines") or ():
        if all(holds(c, world) for c in e.get("when") or ()):
            return npc.get("name") or npc_id, list(e.get("pages") or ())
    return npc.get("name") or npc_id, []


def goal_met(world, q, qid):
    goal = q.get("goal") or {}
    if "flag" in goal:
        return goal["flag"] in flags(world)
    if "beat" in goal:
        return int(((world.get("quests") or {}).get(qid) or {}).get("count", 0)) >= int(goal.get("count", 1))
    return False


def refresh(world, all_quests=None):
    """Active quests whose goal is met become "ready"."""
    for qid, q in (all_quests if all_quests is not None else quests()).items():
        if quest_state(world, qid) == "active" and goal_met(world, q, qid):
            _quest(world, qid)["state"] = "ready"


def on_duel_won(world, kind, encounter, all_quests=None):
    """A duel won: the boss flag, and one more for every active quest that counts this kind."""
    if kind == BOSS_KIND and encounter == "boss":
        set_flag(world, BOSS_FLAG)
    all_quests = all_quests if all_quests is not None else quests()
    for qid, q in all_quests.items():
        goal = q.get("goal") or {}
        if quest_state(world, qid) == "active" and goal.get("beat") == kind:
            _quest(world, qid)["count"] = int(_quest(world, qid).get("count", 0)) + 1
    refresh(world, all_quests)


def journal(world, all_quests=None):
    """[(title, line, state)] for every quest taken, the ones under way first."""
    all_quests = all_quests if all_quests is not None else quests()
    order = {"ready": 0, "active": 1, "done": 2}
    out = []
    for qid, q in all_quests.items():
        st = quest_state(world, qid)
        if st == "none":
            continue
        line = q.get("journal") or ""
        goal = q.get("goal") or {}
        if st == "active" and "beat" in goal:
            n = int(((world.get("quests") or {}).get(qid) or {}).get("count", 0))
            line += f" ({n} of {goal.get('count', 1)})"
        out.append((q.get("title") or qid, line, st))
    out.sort(key=lambda t: order.get(t[2], 3))
    return out


class Conversation:
    """One talk with a person, as the dialogue box walks it: pages, then maybe a choice and its answer's pages.

        name          who speaks
        pages         what is said first
        choice        None, or (yes label, no label): asked after the last page
        answer(yes)   -> the pages said after the choice (and the change it makes)
        reward        a quest's reward to hand over (rpg.adventure.give_reward applies it), or None
        after         the pages said once the reward is handed over: the quest's "done" words (the dialogue lane's reading:
                      "ready - then hand-in - ... done pages once at hand-in")
        rest_until    Round RPG17: "morning" or "dusk" - where the innkeeper's choice takes the clock (by day "Rest until dusk")
        rest          True once you sleep at the inn - the innkeeper's choice answered "Sleep until morning" (Round RPG15c, review
                      R3: before, every word with her was a night's sleep, so a talk at 07:05 slept the whole day away): the
                      clock goes on to the morning, the game is saved and the inn is the return point
        choice_default the answer Enter picks (0 the first): the innkeeper's is Sleep at dusk, night and dawn, Not now by day
        rematch       set by answer(True) when the hall's keeper's rematch is taken
    The save's world is changed as the talk goes: "met_<id>" at once, a quest taken on Yes, a ready quest done (its reward given)."""

    def __init__(self, npc_id, world, people=None, all_quests=None, inn_words=None):
        self.npc_id, self.world = npc_id, world
        people = people if people is not None else npcs()
        all_quests = all_quests if all_quests is not None else quests()
        self._all = all_quests
        inn_words = inn_words if inn_words is not None else inn()
        first_time = f"met_{npc_id}" not in flags(world)
        refresh(world, all_quests)
        self.name, said = entry_for(npc_id, world, people)
        self.pages, self.choice, self.reward, self.rest, self.rematch = [], None, None, False, False
        self.rest_until = None                     # Round RPG17: "morning" (07:00) or "dusk" (18:00) - what resting does
        self.after = []
        self._quest = None
        self._rest = None                          # Round RPG15c: the inn's "rest" pages, said if you choose to sleep
        self.choice_default = 0
        mine = [(qid, q) for qid, q in all_quests.items() if q.get("giver") == npc_id and quest_state(world, qid) != "done"]
        if mine:
            qid, q = mine[0]
            st = quest_state(world, qid)
            if st == "none" and "flag" in (q.get("goal") or {}) and goal_met(world, q, qid):
                st = "ready"                       # Round RPG15b: done before it was asked for - straight to the hand-in (the slice walk's F5)
            self.pages = list(said) if first_time else []
            if st == "none":
                self.pages += list(q.get("offer") or ())
                self.choice = (q.get("yes") or "Yes", q.get("no") or "No")
                self._quest = (qid, q)
            elif st == "active":
                self.pages += list(q.get("progress") or ())
            elif st == "ready":
                self.pages += list(q.get("ready") or ())
                _quest(world, qid)["state"] = "done"
                self.reward = dict(q.get("reward") or {})
                self.after = list(q.get("done") or ())
                if self.reward.get("flag"):
                    set_flag(world, self.reward["flag"])
            if not self.pages:
                self.pages = list(said)
        else:
            self.pages = list(said)
        if npc_id == INNKEEPER and self.choice is None:          # Round RPG15c (review R3): asked, never assumed
            self._rest = list(inn_words.get("rest") or ())
            if clock.phase(clock.now(world)) in SLEEPY_PHASES:
                self.choice, self.choice_default, self.rest_until = (REST_YES, REST_NO), 0, "morning"
            else:                                                # Round RPG17: by day, a way to wait for night
                self.choice, self.choice_default, self.rest_until = (REST_DUSK, REST_NO), 1, "dusk"
        if npc_id == HALL_KEEPER and BOSS_FLAG in flags(world):
            self.pages.append(REMATCH_PAGE)
            self.choice = (REMATCH_YES, REMATCH_NO)
        set_flag(world, f"met_{npc_id}")
        if not self.pages:
            self.pages = ["..."]

    def answer(self, yes):
        """The choice answered: the pages said next."""
        if self._quest is not None:
            qid, q = self._quest
            self._quest = None
            if yes:
                world = self.world
                world.setdefault("quests", {})[qid] = {"state": "active", "count": 0}
                refresh(world, self._all)
                return list(q.get("accepted") or ())
            return list(q.get("declined") or ())
        if self._rest is not None:                     # Round RPG15c: the innkeeper's question
            pages, self._rest = self._rest, None
            self.rest = bool(yes)
            return pages if yes else []
        if self.choice and self.choice[0] == REMATCH_YES:
            self.rematch = bool(yes)
            return []
        return []


def wrap(text, width=WRAP):
    """The lines a page shows: Python's textwrap at `width` characters (at spaces, after a hyphen) - the same as the dialogue box
    (rpg_ui.wrap_lines) and the dialogue lane's own checker."""
    return textwrap.wrap(str(text), width) or []


def check(people=None, all_quests=None, inn_words=None, kinds=None, map_npcs=None):
    """What is wrong with the words, as sentences ([] = nothing): the condition vocabulary, the last entry without conditions,
    pages of at most PAGE_LINES lines of WRAP characters, at most 4 pages an entry, every giver a person, every fought kind a kind
    (kinds), every flag one the game or a reward sets, and (map_npcs) every person on the map has words and every word a person."""
    people = people if people is not None else npcs()
    all_quests = all_quests if all_quests is not None else quests()
    inn_words = inn_words if inn_words is not None else inn()
    out = []
    known_flags = set(GAME_FLAGS) | {f"met_{n}" for n in people} | {(q.get("reward") or {}).get("flag") for q in all_quests.values()}
    from rpg import events                              # Round RPG16: an event can set a flag too (rpg/data/events.json)
    known_flags |= events.flags_set()

    def page_ok(where, pages):
        if len(pages) > 4:
            out.append(f"{where}: {len(pages)} pages (4 at most)")
        for p in pages:
            if len(wrap(p)) > PAGE_LINES:
                out.append(f"{where}: a page of {len(wrap(p))} lines: {p[:40]!r}")

    def cond_ok(where, c):
        if conditions.problem(c) or c.lstrip("!").startswith("map:"):
            out.append(f"{where}: unknown condition {c!r}")
            return
        if c.startswith(("flag:", "!flag:")) and c.split(":", 1)[1] not in known_flags:
            out.append(f"{where}: flag {c.split(':', 1)[1]!r} is never set")
        if c.lstrip("!").startswith("quest:"):
            qid, _s, st = c.lstrip("!")[6:].partition("=")
            if qid not in all_quests or st not in STATES:
                out.append(f"{where}: no quest state {c[6:]!r}")

    for nid, n in people.items():
        lines = n.get("lines") or []
        if not lines or (lines[-1].get("when") or []):
            out.append(f"{nid}: the last entry must have no conditions")
        for i, e in enumerate(lines):
            for c in e.get("when") or ():
                cond_ok(f"{nid} entry {i + 1}", c)
            page_ok(f"{nid} entry {i + 1}", list(e.get("pages") or ()))
    for qid, q in all_quests.items():
        if q.get("giver") not in people:
            out.append(f"{qid}: giver {q.get('giver')!r} has no words")
        goal = q.get("goal") or {}
        if "beat" in goal and kinds is not None and goal["beat"] not in kinds:
            out.append(f"{qid}: no enemy kind {goal['beat']!r}")
        if "flag" in goal and goal["flag"] not in known_flags:
            out.append(f"{qid}: goal flag {goal['flag']!r} is never set")
        for key in ("offer", "accepted", "declined", "progress", "ready", "done"):
            page_ok(f"{qid} {key}", list(q.get(key) or ()))
    page_ok("inn rest", list(inn_words.get("rest") or ()))
    page_ok("inn woke", list(inn_words.get("woke") or ()))
    if map_npcs is not None:
        for nid in map_npcs:
            if nid not in people:
                out.append(f"{nid} stands on the map but has no words")
        for nid in people:
            if nid not in map_npcs:
                out.append(f"{nid} has words but isn't on the map")
    return out
