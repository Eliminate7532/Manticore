# SPDX-License-Identifier: GPL-3.0-or-later
"""Talking to people, quests and the inn (Round RPG14, the Adventure remaster's fifth round; claude/SPEC_RPG_SLICE_2026-10-23.md,
section RPG14). No pygame here: rpg_screens draws the dialogue box (rpg_ui.DialogueBox) and walks a Conversation.

The words live in rpg/data: the dialogue lane's draft (claude/RPG_SLICE_DIALOGUE_2026-10-23.md, its four files copied byte for
byte - their SHA-256 match the lane's), placeholders for Karl to edit:
  npcs.json     {"version": 1, "npcs": {id: {"name", "lines": [{"when": [conditions], "pages": [...]}, ...]}}} - the first entry
                whose conditions all hold is said; the last has none.
  quests.json   {"version": 1, "quests": {id: {"title", "giver", "goal", "offer", "yes", "no", "accepted", "declined",
                "progress", "ready", "done", "reward", "journal"}}}; goal {"beat": kind, "count": n[, "spot": "<map>#<name>"]} or {"flag": name}; reward
                {"gold": n} / {"card": "rare_from_band"} / {"flag": name}, any of them.
  inn.json      {"version": 1, "rest": [pages], "woke": [pages]}.
Conditions: rpg.conditions' words - "flag:<name>", "quest:<id>=none|active|ready|done" and (Round RPG16) "phase:<p>[|<p>]",
each with "!" for "not".

What the save keeps (save.world): "flags" [names]; "quests" {id: {"state": "active" | "ready" | "done", "count": n}} (a quest not
there is "none"); "inn" {"map", "spawn"} - where a lost duel wakes you (Round RPG14: the inn you last rested at).
Flags the game sets: "met_<npc id>" (after the first talk), "warrens_entered" (rpg_screens, the first step into the Warrens),
"grave_priest_beaten" (on_duel_won: the boss duel won), any quest reward's flag, and a goal's "others_flag" (Round RPG18c: on_duel_won,
a duel of the goal's kind won away from the goal's spot while the quest is active).

Who says what (Conversation):
  - A quest giver whose quest isn't done: the first-meeting words (only the first time), then the quest's offer (with its Yes / No
    choice) when it is new, its progress when it is under way, its "ready" words, the reward and its "done" words when the goal
    is met. A quest whose
    goal is met is "ready" at once (a flag goal checked on talking; a fight goal counted by on_duel_won).
  - Everyone else, and a giver once the quest is done: their first matching entry.
  - The innkeeper (INNKEEPER): her words, then "Sleep until morning" / "Not now" (Round RPG15c); sleeping says the inn's "rest"
    pages, the clock goes on to 07:00, the game is saved and the inn becomes your return point. Round RPG17 (Karl 24 Oct Q5): by
    day the choice is "Rest until dusk" / "Not now" instead - the clock goes on to 18:00, the rest the same. Round RPG18b (Karl 25
    Oct): by day three answers - "Rest until dusk" / "Sleep until morning" / "Not now" ("Not now" the default).
  - Round PROG5: the bounty clerk (BOUNTY_CLERK) - her words, then the board (rpg_screens: rpg.extras posts, pays and lists the
    jobs); the gauntlet's warden (GAUNTLET_WARDEN) - his words, then the gauntlet: "Enter: 50 gold" / "Not now", or, while one is
    under way, "Next duel" / "Stop here" (Conversation.gauntlet says which was picked).
  - Round SPAR1: the sparring yard's master (YARD_MASTER) - his words, then "I'll run 40 practice duels: ..." and "Spar: 40
    duels" / "Not now" (Conversation.spar is True when Spar was picked; rpg_screens plays them, rpg.spar).
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
BOUNTY_CLERK = "bounty_clerk"          # Round PROG5: the bounty board's clerk (rpg.extras; rpg_screens adds the board's pages)
GAUNTLET_WARDEN = "gauntlet_warden"    # Round PROG5: the town gauntlet's warden
YARD_MASTER = "yard_master"            # Round SPAR1: the sparring yard's master (rpg.spar; rpg_screens plays the practice duels)
SPAR_OFFER = "I'll run {n} practice duels: your deck, your level and gear, against {against}."
SPAR_YES, SPAR_NO = "Spar: {n} duels", "Not now"
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


def counts_for(goal, kind, spot=None):
    """Round RPG18b: a won duel against `kind` at map spot `spot` counts for a "beat" goal - the kind is the goal's, and a goal that
    names a spot ("spot": "<map>#<name>", Karl 25 Oct: only the bandits' leader by the bridge counts for Pell) only that spot."""
    if goal.get("beat") != kind:
        return False
    want = goal.get("spot")
    return not want or want == spot


def on_duel_won(world, kind, encounter, all_quests=None, spot=None):
    """A duel won: the boss flag, and one more for every active quest that counts this kind (Round RPG18b: and, for a goal that
    names a spot, only a duel at that spot - spot is the duel's map spot, rpg.duel's pending_duel["spot"])."""
    if kind == BOSS_KIND and encounter == "boss":
        set_flag(world, BOSS_FLAG)
    all_quests = all_quests if all_quests is not None else quests()
    for qid, q in all_quests.items():
        goal = q.get("goal") or {}
        if quest_state(world, qid) == "active" and counts_for(goal, kind, spot):
            _quest(world, qid)["count"] = int(_quest(world, qid).get("count", 0)) + 1
        elif quest_state(world, qid) == "active" and others_flee(goal, kind, spot):
            set_flag(world, goal["others_flag"])
    refresh(world, all_quests)


def others_flee(goal, kind, spot):
    """Round RPG18c (Karl 26 Oct: "the other bandits flee after one win"): a goal that names a spot and an "others_flag" sets that
    flag when a duel of its kind is won at another spot on the same map while the quest is active (a random duel, with no spot,
    doesn't count - my reading) - the map's other foes of that kind carry
    `!flag:<it>` in their `when`, so after one such win they leave (the goal's own foe stays until he is beaten)."""
    want = goal.get("spot")
    return (goal.get("beat") == kind and isinstance(want, str) and bool(want) and isinstance(spot, str) and spot != want
            and spot.partition("#")[0] == want.partition("#")[0]          # on the goal's own map (not a random duel)
            and isinstance(goal.get("others_flag"), str) and bool(goal.get("others_flag")))


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
                      (Round RPG18b: by day the choice has three answers, so Not now is 2)
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
        self.reward_quest = None           # Round PROG4: the id of the quest handed in now (its gear, rpg.gear sources.quests)
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
                self.reward_quest = qid
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
            else:                                                # Round RPG17: by day, a way to wait for night; Round RPG18b
                # (Karl 25 Oct): and "Sleep until morning" by day too - three answers, "Not now" still the default
                self.choice, self.choice_default, self.rest_until = (REST_DUSK, REST_YES, REST_NO), 2, "dusk"
        self.gauntlet = None                       # Round PROG5: "enter" / "next" / "stop" - the warden's choice answered
        self._gauntlet = False
        if npc_id == GAUNTLET_WARDEN and self.choice is None:    # Round PROG5: the town gauntlet (rpg.extras)
            from rpg import extras
            try:
                more, self.choice = extras.gauntlet_pages(world)
                self.pages += extras.split_pages(more)
                self._gauntlet = True
                self.choice_default = 0
            except (OSError, ValueError):
                pass
        self.spar = False                          # Round SPAR1: the yard master's "Spar" picked
        self._spar = False
        if npc_id == YARD_MASTER and self.choice is None:        # Round SPAR1: the sparring yard (rpg.spar)
            from rpg import spar
            try:
                st = spar.settings()
            except (OSError, ValueError):
                st = spar.Settings()
            self.pages.append(SPAR_OFFER.format(n=st.duels, against=st.against_words(world.get("region"))))
            self.choice, self.choice_default, self._spar = (SPAR_YES.format(n=st.duels), SPAR_NO), 0, True
        if npc_id == HALL_KEEPER and BOSS_FLAG in flags(world):
            self.pages.append(REMATCH_PAGE)
            self.choice = (REMATCH_YES, REMATCH_NO)
        set_flag(world, f"met_{npc_id}")
        if not self.pages:
            self.pages = ["..."]

    def answer(self, yes, picked=None):
        """The choice answered: the pages said next. picked (Round RPG18b): the index of the answer given, for a choice of three
        (the innkeeper by day: 0 Rest until dusk, 1 Sleep until morning, 2 Not now); None - the first answer when yes, else the
        last."""
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
            n = len(self.choice or ())
            if not (isinstance(picked, int) and not isinstance(picked, bool) and 0 <= picked < n):
                picked = 0 if yes else n - 1
            self.rest = picked < n - 1                 # the last answer is "Not now"
            if self.rest and self.choice[picked] == REST_YES:
                self.rest_until = "morning"            # Round RPG18b: "Sleep until morning" by day
            elif self.rest and self.choice[picked] == REST_DUSK:
                self.rest_until = "dusk"
            return pages if self.rest else []
        if self._gauntlet:                             # Round PROG5: Enter / Next duel, or Not now / Stop here
            self._gauntlet = False
            from rpg import extras
            under_way = self.choice and self.choice[0] == extras.GAUNTLET_GO
            self.gauntlet = ("next" if under_way else "enter") if yes else ("stop" if under_way else None)
            return []
        if self._spar:                                 # Round SPAR1: Spar or Not now
            self._spar = False
            self.spar = bool(yes)
            return []
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
    known_flags |= {(q.get("goal") or {}).get("others_flag") for q in all_quests.values()} - {None}   # Round RPG18c
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
