# SPDX-License-Identifier: GPL-3.0-or-later
"""
ai_bench.py - does the AI answer the right threat? Ten fixed boards, each a yes / no, played through the real Forge engine with the
card checker's machinery (card_check.Checker: Forge's "Setup Game State", a scripted player who takes the first legal answer).

    python tools/ai_bench.py                      10 boards x 5 trials, about 4 minutes
    python tools/ai_bench.py --trials 10 --seed 11 --boards C1,C2,R2

Your side holds only cards from the Kinnan list (tests/fixtures/decks/kinnan_nbc_moxfield_export.txt); the AI's side holds plain
interaction (Counterspell, Force of Will, Swords to Plowshares, Shatter) and the lands to cast it. Each trial: the board is set up
(Forge's Setup Game State, always as turn 5), a scripted Karl makes the same moves every time, the turn is played out to the next
one, the AI's own stack entries are kept as evidence ("Swords to Plowshares (279): Exile target creature. [targets: Kinnan, Bonder
Prodigy (415)]"), and the verdict is a plain yes / no against what a good player would do.

When removal counts: on boards played on the AI's turn (R1-R3), removal counts only if your card is already gone when your turn
begins - from your upkeep on you can start the Basalt Monolith loop in response - so the board is read at the first question of
your turn (a Swords cast in your upkeep is still on the stack then, and counts as too late). On your own turn (C1-C4, F1, F2, I1)
the AI gets every priority of the turn - your main phase, combat, your end step - and the board is read when its turn begins.

Rebuilt 7 Oct 2026 (Claude, Fable) from claude/ROUND_AI1_2026-10-02.md: the 3 Oct build was only ever posted in a chat and never saved.
Same boards, same scripted moves, same rules for the verdicts; the code is new. Never imported by the game.
"""
import json
import os
import re
import time

import card_check as cc

KINNAN_DECK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")
MY_LANDS = ["Forest", "Forest", "Island", "Island", "Island", "Island"]        # six: Basalt Monolith (3) with Kinnan's tax covered
AI_BLUE = ["Island"] * 5                                                     # Force of Will's {3}{U}{U} hard cast
AI_WHITE = ["Plains"] * 2
AI_RED = ["Mountain"] * 2
COUNTERS = ("Counterspell", "Force of Will")
REMOVAL = ("Swords to Plowshares", "Shatter")
SETUP_TURN = 5              # every board is set up as turn 5; the trial ends when Forge's prompt says turn 6 has begun
TURN_WAIT = 90.0            # seconds to let a turn play out before the trial is an error
TRIAL_BUDGET = 40.0         # seconds for one scripted play
TURN_LINE = re.compile(r"Turn:\s*(\d+)\s*\((.*)\)")


class Board:
    __slots__ = ("key", "title", "right", "mine_bf", "mine_hand", "ai_bf", "ai_hand", "active", "plays", "judge", "why", "my_lands")
    players = 2                 # round AI2b: every board above is 1v1; PodBoard has 3 or 4
    until_mine = False          # 1v1: the turn after the set-up's is always the one the board is read at
    watch = ()                  # round AI2b: cards not mine whose zone the verdict needs

    def __init__(self, key, title, right, mine_bf, mine_hand, ai_bf, ai_hand, active, plays, judge, why="", my_lands=None):
        self.key, self.title, self.right = key, title, right
        self.mine_bf, self.mine_hand, self.ai_bf, self.ai_hand = list(mine_bf), list(mine_hand), list(ai_bf), list(ai_hand)
        self.active, self.plays, self.judge, self.why = active, list(plays), judge, why
        self.my_lands = list(my_lands) if my_lands else list(MY_LANDS)       # round AI2: the extra boards need white or green

    def setup_lines(self):
        """Forge's Setup Game State lines, the card checker's way. Every zone is written, the empty ones too: a zone left out keeps
        what the last trial left in it (seen live: the exiled Kinnan of one trial still in exile at the next). The deck's real
        commander stays in the command zone untouched; the Kinnan these boards put out is a fresh card, not the commander."""
        return ["humanlife=40", "ailife=40", "activeplayer=" + self.active, "activephase=MAIN1", "turn=%d" % SETUP_TURN,
                "humanlandsplayed=0", "humanhand=" + ";".join(self.mine_hand), "humanbattlefield=" + ";".join(self.mine_bf + self.my_lands),
                "humanlibrary=" + ";".join(cc.LIBRARY), "humangraveyard=", "humanexile=",
                "aihand=" + ";".join(self.ai_hand), "ailibrary=" + ";".join(cc.LIBRARY), "aibattlefield=" + ";".join(self.ai_bf),
                "aigraveyard=", "aiexile=", "removesummoningsickness=true"]

    def my_cards(self):
        return set(self.mine_bf) | set(self.mine_hand)


class Trial:
    """One run of one board: what the AI did (its stack entries as Forge words them), where my cards ended, the verdict."""

    def __init__(self, board, index):
        self.board, self.index = board.key, index
        self.ai_entries = []            # "Counterspell (230): ... [targets: Basalt Monolith (203)]" in the order first seen
        self.my_entries = []
        self.ai_cast = []               # names the AI cast (from the "cast" events; triggers and abilities are not casts)
        self.zones = {}                 # my card name -> where it ended ("my battlefield", "my graveyard", "my exile", ...)
        self.at_my_turn = None          # R1-R3: my battlefield at the first question of my turn
        self.stack_end = []             # what was still on the stack when the board was read (a Swords cast in my upkeep, say)
        self.verdict = None             # True / False / None (the trial could not be judged)
        self.error = ""
        self.seconds = 0.0
        self.ai2 = []                   # round AI2: what the bridge's threat rules changed ("countered Basalt Monolith with ...")
        self.casts = []                 # round AI2b: who cast what, in order ("AI 1: Counterspell")
        self.boards = None              # round AI2b: every player's battlefield when my turn begins ({"me": [...], "AI 2": [...]})

    def as_dict(self):
        return {"board": self.board, "index": self.index, "verdict": self.verdict, "ai_entries": self.ai_entries,
                "ai_cast": self.ai_cast, "zones": self.zones, "at_my_turn": self.at_my_turn, "stack_end": self.stack_end,
                "error": self.error, "seconds": round(self.seconds, 1), "ai2": self.ai2, "casts": self.casts,
                "boards": self.boards}


# ---- the verdicts (pure functions of a Trial, so they are tested without Forge) ----------------------------------------------

def countered(name):
    """The AI countered my spell: it cast a counter while the spell was on the stack, and the spell ended in my graveyard."""
    def judge(t):
        return any(c in COUNTERS for c in t.ai_cast) and t.zones.get(name) == "my graveyard"
    return judge


def resolved(name):
    """My spell resolved: it is on my battlefield at the end (nothing the AI did stopped it)."""
    def judge(t):
        return t.zones.get(name) == "my battlefield"
    return judge


def both(*judges):
    def judge(t):
        return all(j(t) for j in judges)
    return judge


def gone_by_my_turn(name, keep=None):
    """The AI's turn: `name` is no longer on my battlefield when my turn begins (and `keep`, if given, still is)."""
    def judge(t):
        if t.at_my_turn is None:
            return None
        if name in t.at_my_turn:
            return False
        return keep is None or keep in t.at_my_turn
    return judge


def any_gone_by_my_turn(names, keep=None):
    """Round AI2: the AI's turn - at least one of `names` (the pieces of a combo) is off my battlefield when my turn begins, and
    `keep`, if given, is still there."""
    def judge(t):
        if t.at_my_turn is None:
            return None
        if all(n in t.at_my_turn for n in names):
            return False
        return keep is None or keep in t.at_my_turn
    return judge


def exiled_this_turn(name):
    """My turn: the AI answered the creature I cast with its removal before its own turn began (the card is off my battlefield)."""
    def judge(t):
        return any(c in REMOVAL for c in t.ai_cast) and t.zones.get(name) != "my battlefield"
    return judge


KINNAN = "Kinnan, Bonder Prodigy"
BASALT = "Basalt Monolith"
SIGNET = "Arcane Signet"
PETAL = "Lotus Petal"
SPHINX = "Consecrated Sphinx"
SOL_RING = "Sol Ring"

BOARDS = [
    Board("C1", "Kinnan out, you cast Basalt Monolith; AI holds Counterspell", "counter Basalt",
          [KINNAN], [BASALT], AI_BLUE, ["Counterspell"], "human", [BASALT], countered(BASALT)),
    Board("C2", "Basalt out, you cast Kinnan", "counter Kinnan",
          [BASALT], [KINNAN], AI_BLUE, ["Counterspell"], "human", [KINNAN], countered(KINNAN)),
    Board("C3", "Nothing going on, you cast Arcane Signet", "let it resolve",
          [], [SIGNET], AI_BLUE, ["Counterspell"], "human", [SIGNET], resolved(SIGNET)),
    Board("C4", "Kinnan out: Arcane Signet, then Basalt", "let Signet resolve, counter Basalt",
          [KINNAN], [SIGNET, BASALT], AI_BLUE, ["Counterspell"], "human", [SIGNET, BASALT], both(resolved(SIGNET), countered(BASALT))),
    Board("F1", "Kinnan out, Basalt; AI has only Force of Will", "Force of Will on Basalt",
          [KINNAN], [BASALT], AI_BLUE, ["Force of Will"], "human", [BASALT], countered(BASALT)),
    Board("F2", "Kinnan out: Lotus Petal, then Basalt; only Force of Will", "save it for Basalt",
          [KINNAN], [PETAL, BASALT], AI_BLUE, ["Force of Will"], "human", [PETAL, BASALT], both(resolved(PETAL), countered(BASALT))),
    Board("R1", "AI's turn; you have Kinnan + Basalt; AI has Swords", "exile Kinnan before your turn",
          [KINNAN, BASALT], [], AI_WHITE, ["Swords to Plowshares"], "ai", [], gone_by_my_turn(KINNAN)),
    Board("R2", "AI's turn; you have Kinnan + Basalt + Consecrated Sphinx", "exile Kinnan, not the Sphinx",
          [KINNAN, BASALT, SPHINX], [], AI_WHITE, ["Swords to Plowshares"], "ai", [], gone_by_my_turn(KINNAN, keep=SPHINX)),
    Board("R3", "AI's turn; you have Kinnan + Basalt + Sol Ring; AI has Shatter", "Shatter Basalt",
          [KINNAN, BASALT, SOL_RING], [], AI_RED, ["Shatter"], "ai", [], gone_by_my_turn(BASALT)),
    Board("I1", "Basalt out, you cast Kinnan; AI holds Swords", "exile Kinnan on your turn",
          [BASALT], [KINNAN], AI_WHITE, ["Swords to Plowshares"], "human", [KINNAN], exiled_this_turn(KINNAN)),
]
# Round AI2: six more boards with OTHER cards than the ten above (none with Kinnan), to see whether AI2's rules work beyond the
# Kinnan pair: a two-creature combo (Devoted Druid + Vizier of Remedies, white and green), a two-artifact one (Rings of Brighthearth +
# Basalt Monolith), a plain 2-mana rock, and two Game Changers (Survival of the Fittest; Vampiric Tutor, mana value 1, which Forge's
# default profile counters only 30% of the time: CHANCE_TO_COUNTER_CMC_1=30). Not run by default: `--boards extra`.
DRUID = "Devoted Druid"
VIZIER = "Vizier of Remedies"
RINGS = "Rings of Brighthearth"
MIND_STONE = "Mind Stone"
SURVIVAL = "Survival of the Fittest"
VAMPIRIC = "Vampiric Tutor"
WG_LANDS = ["Plains", "Plains", "Forest", "Forest", "Island", "Island"]
BG_LANDS = ["Swamp", "Swamp", "Forest", "Forest", "Island", "Island"]
EXTRA_BOARDS = [
    Board("X1", "Devoted Druid out, you cast Vizier of Remedies; AI holds Counterspell", "counter Vizier",
          [DRUID], [VIZIER], AI_BLUE, ["Counterspell"], "human", [VIZIER], countered(VIZIER), my_lands=WG_LANDS),
    Board("X2", "Nothing going on, you cast Mind Stone", "let it resolve",
          [], [MIND_STONE], AI_BLUE, ["Counterspell"], "human", [MIND_STONE], resolved(MIND_STONE), my_lands=WG_LANDS),
    Board("X3", "AI's turn; you have Devoted Druid + Vizier of Remedies + Consecrated Sphinx", "exile Druid or Vizier, not the Sphinx",
          [DRUID, VIZIER, SPHINX], [], AI_WHITE, ["Swords to Plowshares"], "ai", [], any_gone_by_my_turn([DRUID, VIZIER], keep=SPHINX),
          my_lands=WG_LANDS),
    Board("X4", "AI's turn; you have Rings of Brighthearth + Basalt Monolith + Sol Ring; AI has Shatter", "Shatter Rings or Basalt",
          [RINGS, BASALT, SOL_RING], [], AI_RED, ["Shatter"], "ai", [], any_gone_by_my_turn([RINGS, BASALT])),
    Board("X5", "Nothing going on, you cast Survival of the Fittest (a Game Changer)", "counter it",
          [], [SURVIVAL], AI_BLUE, ["Counterspell"], "human", [SURVIVAL], countered(SURVIVAL), my_lands=WG_LANDS),
    Board("X6", "Nothing going on, you cast Vampiric Tutor (a Game Changer, mana value 1)", "counter it",
          [], [VAMPIRIC], AI_BLUE, ["Counterspell"], "human", [VAMPIRIC], countered(VAMPIRIC), my_lands=BG_LANDS),
]
ALL_BOARDS = BOARDS + EXTRA_BOARDS


# ---- round AI2b: the same questions at a table of 3 or 4 ----------------------------------------------------------------------
# AI2's judgement call 4 ("in a pod the AI answers another AI's combo too") and every AI1/AI2 measurement so far were 1v1: the
# bench's Checker played against one filler AI. These boards seat 2 or 3 filler AIs (all on the same Kinnan + 99 Forest deck as
# the 1v1 bench, so the AIs' commanders and libraries are the same as before) and ask who answers, and what.

class PodBoard:
    """A board for 3 or 4 players. seats[0] is me, seats[1..] the AIs in turn order (AI 1, AI 2, AI 3); each seat is
    (battlefield, hand, lands). active = the seat whose turn the set-up makes it. On an AI's turn the board is read when MY turn
    begins (until_mine), so every AI after it plays its turn first. watch = cards not mine whose zone the verdict needs."""
    __slots__ = ("key", "title", "right", "seats", "active", "plays", "judge", "why", "watch")

    def __init__(self, key, title, right, seats, active, plays, judge, why="", watch=()):
        self.key, self.title, self.right = key, title, right
        self.seats = [(list(bf), list(hand), list(lands)) for bf, hand, lands in seats]
        self.active, self.plays, self.judge, self.why, self.watch = active, list(plays), judge, why, tuple(watch)

    @property
    def players(self):
        return len(self.seats)

    @property
    def until_mine(self):
        return self.active != 0

    @property
    def mine_bf(self):
        return self.seats[0][0]

    @property
    def mine_hand(self):
        return self.seats[0][1]

    def setup_lines(self):
        """Forge's Setup Game State lines for every seat (p0 = me; GameState refuses a set-up with fewer player states than the
        game has players). Every zone is written, as Board.setup_lines does, for the same reason."""
        lines = ["activeplayer=p%d" % self.active, "activephase=MAIN1", "turn=%d" % SETUP_TURN, "removesummoningsickness=true"]
        for i, (bf, hand, lands) in enumerate(self.seats):
            p = "p%d" % i
            lines += [p + "life=40", p + "landsplayed=0", p + "hand=" + ";".join(hand), p + "battlefield=" + ";".join(bf + lands),
                      p + "library=" + ";".join(cc.LIBRARY), p + "graveyard=", p + "exile="]
        return lines

    def my_cards(self):
        return set(self.mine_bf) | set(self.mine_hand)


def counters_cast(t):
    """How many counterspells the AIs cast in the trial (a second counter on a spell already countered is a card wasted)."""
    return sum(1 for c in t.ai_cast if c in COUNTERS)


def countered_by(name, seat):
    """An AI's spell: `seat` ("AI 1") cast a counter and `name` ended in its owner's graveyard. None (not judged) when the
    spell never left its owner's hand - the AI whose turn it was didn't cast it, so there was nothing to answer."""
    def judge(t):
        where_now = t.zones.get(name) or ""
        if where_now.endswith("hand"):
            return None
        return any(c.split(": ", 1)[-1] in COUNTERS and c.startswith(seat + ":") for c in t.casts) \
            and where_now.endswith("graveyard")
    return judge


def removed_keeping(gone, keep_seat, keep):
    """My `gone` is off my battlefield when my turn begins, and `keep` is still on `keep_seat`'s."""
    def judge(t):
        if t.at_my_turn is None or t.boards is None:
            return None
        return gone not in t.at_my_turn and keep in (t.boards.get(keep_seat) or [])
    return judge


def protected_own(name, seat, attacker):
    """Round AI2c: `seat`'s own spell `name`, countered by `attacker`, and `seat` answered with a counter of its own: right when
    the spell resolved (on its battlefield at the end). None (not judged) when the spell was never cast or `attacker` never
    countered it - there was nothing to protect it from."""
    def judge(t):
        where_now = t.zones.get(name) or ""
        if where_now.endswith("hand"):
            return None
        if not any(c.split(": ", 1)[-1] in COUNTERS and c.startswith(attacker + ":") for c in t.casts):
            return None
        return where_now.endswith("battlefield")
    return judge


def their_combo_answered(seat, pieces, keep_mine):
    """At least one of `seat`'s combo `pieces` is off its battlefield when my turn begins, and my `keep_mine` is still on mine."""
    def judge(t):
        if t.at_my_turn is None or t.boards is None:
            return None
        theirs = t.boards.get(seat) or []
        return not all(p in theirs for p in pieces) and keep_mine in t.at_my_turn
    return judge


BASIC_NAMES = {"Forest", "Island", "Swamp", "Mountain", "Plains"}
NOTHING = ([], [], AI_BLUE)       # an AI seat with only its lands
POD_BOARDS = [
    PodBoard("P1", "3 players: Kinnan out, you cast Basalt; AI 1 holds Counterspell, AI 2 nothing", "counter Basalt",
             [([KINNAN], [BASALT], MY_LANDS), ([], ["Counterspell"], AI_BLUE), NOTHING], 0, [BASALT], countered(BASALT)),
    PodBoard("P2", "3 players: Kinnan out, you cast Basalt; both AIs hold Counterspell", "counter Basalt (once)",
             [([KINNAN], [BASALT], MY_LANDS), ([], ["Counterspell"], AI_BLUE), ([], ["Counterspell"], AI_BLUE)], 0, [BASALT],
             countered(BASALT), why="the summary also counts the counters spent: a second one on a countered spell is wasted"),
    PodBoard("P3", "4 players: Kinnan out, you cast Basalt; only AI 3 (last in turn order) holds Counterspell", "counter Basalt",
             [([KINNAN], [BASALT], MY_LANDS), NOTHING, NOTHING, ([], ["Counterspell"], AI_BLUE)], 0, [BASALT], countered(BASALT)),
    PodBoard("P4", "3 players, AI 1's turn: you have Kinnan + Basalt; AI 2 has Consecrated Sphinx; AI 1 holds Swords",
             "exile your Kinnan, not AI 2's Sphinx",
             [([KINNAN, BASALT], [], MY_LANDS), ([], ["Swords to Plowshares"], AI_WHITE), ([SPHINX], [], ["Forest"] * 2)], 1, [],
             removed_keeping(KINNAN, "AI 2", SPHINX)),
    PodBoard("P5", "3 players, AI 2's turn: AI 2 has Kinnan and casts Basalt; AI 1 holds Counterspell", "AI 1 counters AI 2's Basalt",
             [([], [], MY_LANDS), ([], ["Counterspell"], AI_BLUE), ([KINNAN], [BASALT], ["Forest", "Forest", "Island", "Island"])],
             2, [], countered_by(BASALT, "AI 1"), why="judgement call 4: another AI's combo piece", watch=[BASALT]),
    PodBoard("P6", "3 players, AI 1's turn: AI 2 has Devoted Druid + Vizier of Remedies; you have Consecrated Sphinx; AI 1 has Swords",
             "exile AI 2's Druid or Vizier, not your Sphinx",
             [([SPHINX], [], MY_LANDS), ([], ["Swords to Plowshares"], AI_WHITE), ([DRUID, VIZIER], [], ["Forest"] * 2)], 1, [],
             their_combo_answered("AI 2", [DRUID, VIZIER], SPHINX), why="judgement call 4: another AI's combo"),
    # round AI2c: the guard for rule 4 - an AI still protects its OWN spell from another AI's counter
    PodBoard("P7", "3 players, AI 2's turn: AI 2 has Kinnan and casts Basalt; AI 1 and AI 2 both hold Counterspell",
             "AI 2 protects its own Basalt", [([], [], MY_LANDS), ([], ["Counterspell"], AI_BLUE),
                                              ([KINNAN], [BASALT, "Counterspell"], ["Forest"] * 3 + ["Island"] * 3)],
             2, [], protected_own(BASALT, "AI 2", "AI 1"),
             why="round AI2c's guard: rule 4 must not stop an AI protecting its own spell", watch=[BASALT]),
]
BOARD_BY_KEY = {b.key: b for b in ALL_BOARDS + POD_BOARDS}


def _entry_text(item, names=None, players=None):
    """A stack entry as evidence: "Counterspell (230): Counter target spell. [targets: Basalt Monolith (203)]"."""
    c = item.get("card") or {}
    text = item.get("key") or item.get("text") or ""
    head = "%s (%s)" % (c.get("name", "?"), c.get("id", "?"))
    if text and not text.startswith(head):
        text = head + ": " + text
    text = text or head
    targets = []
    for tg in item.get("targets") or []:
        tg = str(tg)
        if tg.startswith("c") and tg[1:].isdigit():
            cid = int(tg[1:])
            targets.append("%s (%d)" % ((names or {}).get(cid, "card"), cid))
        elif tg.startswith("p") and tg[1:].isdigit():
            targets.append((players or {}).get(int(tg[1:]), "player " + tg[1:]))
        else:
            targets.append(tg)
    if targets:
        text += " [targets: " + ", ".join(targets) + "]"
    return text


def prompt_turn(state):
    """(turn number, whose turn) from Forge's own prompt text ("Priority: Checker / Turn: 5 (AI 1 (Filler)) / Phase: ..."), or None.
    Only a question Forge is asking now counts ("asking" not false): right after a set-up the snapshot can still carry the question
    from before it. The snapshot's own "turn" and "activePlayer" fields are not used: after Setup Game State they kept the old turn
    number until the next turn began (seen live, 7 Oct), so a trial could not tell the set-up's turn 5 from the turn before."""
    if not state or state.get("asking") is False:
        return None
    m = TURN_LINE.search(((state.get("prompt") or {}).get("message")) or "")
    return (int(m.group(1)), m.group(2)) if m else None


class Watcher:
    """Wraps the session's poll() for one trial: notes every stack entry as it appears, the AI's casts, the turn as Forge's prompts
    report it, and my board at the first question of my next turn (R1-R3). The AI's hand is hidden, so a cast event names only a
    card id; the name is looked up from the stack entry (or any zone that later shows it) when the trial ends (`finish`)."""

    def __init__(self, session, trial, my_id, ai_id, my_name, until_mine=False):
        self.s, self.t, self.me, self.my_name = session, trial, my_id, my_name
        # round AI2b: one AI (an id, as before) or every AI at a pod's table (a list of ids)
        self.ais = set(ai_id) if isinstance(ai_id, (list, tuple, set)) else {ai_id}
        self.until_mine = until_mine    # round AI2b: an AI's turn in a pod - read the board when MY turn begins, not the next turn
        self.labels = {}                # player id -> "me" / "AI 1" (from the players' names)
        self.seen = set()
        self.names = {}                 # card id -> name, from every stack entry and zone seen during the trial
        self.cast_ids = []              # the AI's casts, as the events name them, in order
        self.settled = False            # a question asked on the set-up's own turn has been seen (what came before it is stale)
        self.next_turn = None           # (turn, whose) once a question on a later turn is asked
        self.last_seq = max([ev.get("seq") or 0 for ev, _rx in list(session.events)] or [0])   # events from before this trial are not its
        self.ai2_seen = len(getattr(session, "threat_moves", None) or [])                    # round AI2: the same for its moves
        self.orig = session.poll

    def __enter__(self):
        self.s.poll = self.poll
        return self

    def __exit__(self, *exc):
        self.s.poll = self.orig
        self.finish()
        return False

    def poll(self, *a, **k):
        r = self.orig(*a, **k)
        st = self.s.state or {}
        for c, _zone, _pid in cc.all_cards(st):
            if c.get("id") is not None and c.get("name"):
                self.names[c["id"]] = c["name"]
        for p in st.get("players", []):
            self.labels[p["id"]] = "me" if p["id"] == self.me else (p.get("name") or "AI").split(" (")[0]
        one_ai = len(self.ais) == 1
        players = {pid: ("the AI" if one_ai and pid != self.me else label) for pid, label in self.labels.items()}
        for item in st.get("stack") or []:
            c = item.get("card") or {}
            key = (c.get("id"), c.get("name"), item.get("activator"))
            if key in self.seen:
                continue
            self.seen.add(key)
            (self.t.ai_entries if item.get("activator") in self.ais or c.get("controller") in self.ais else self.t.my_entries).append(
                _entry_text(item, self.names, players))
        turn = prompt_turn(st)
        if turn and turn[0] == SETUP_TURN:
            self.settled = True
        elif turn and self.settled and turn[0] > SETUP_TURN and self.next_turn is None \
                and (not self.until_mine or turn[1] == self.my_name):
            self.next_turn = turn
            if turn[1] == self.my_name and self.t.at_my_turn is None:
                me = next((p for p in st.get("players", []) if p["id"] == self.me), None)
                self.t.at_my_turn = [c.get("name") for c in (me or {}).get("zones", {}).get("battlefield", [])]
                self.t.boards = {self.labels.get(p["id"], str(p["id"])): [c.get("name") for c in p.get("zones", {}).get("battlefield", [])]
                                 for p in st.get("players", [])}
        for ev, _rx in list(self.s.events):
            seq = ev.get("seq") or 0
            if seq <= self.last_seq:
                continue
            self.last_seq = seq
            if ev.get("kind") == "cast" and ev.get("player") in self.ais and not ev.get("trigger") and not ev.get("ability"):
                self.cast_ids.append((ev.get("player"), ev.get("card")))
        moves = getattr(self.s, "threat_moves", None) or []
        for m in moves[self.ai2_seen:]:
            self.t.ai2.append("%s %s with %s (%s)" % (m.get("move"), m.get("target"), m.get("card"), m.get("why")))
        self.ai2_seen = len(moves)
        return r

    def finish(self):
        self.poll()
        names = [self.names.get(cid) or ((self.s.card(cid) or {}).get("name")) or ("card %s" % cid) for _pid, cid in self.cast_ids]
        self.t.ai_cast = names
        self.t.casts = ["%s: %s" % (self.labels.get(pid, "AI"), name) for (pid, _cid), name in zip(self.cast_ids, names)]


def where(chk, name):
    """The one zone my card ended in. Forge's command zone carries a card named after each commander for as long as the game lasts
    (seen live: Kinnan on the battlefield shows as "my battlefield, my command, opponent's command" - the filler deck's commander is
    Kinnan too), so the command zones are left out unless nothing else holds the card (the commander went back there)."""
    zones = sorted(set(chk.where_is(name)))
    real = [z for z in zones if not z.endswith(" command")]
    if not real:
        real = [z for z in zones if z == "my command"]
    return real[0] if len(real) == 1 else (", ".join(real) if real else "")


def pass_the_turn(chk, watcher, seconds=TURN_WAIT):
    """Pass priority (and answer whatever is asked on the way, as the scripted player does) until the next turn begins: the
    AI's after mine, mine after the AI's. Returns an error text, or "" when Forge's prompt says the next turn has begun."""
    s = chk.s
    end = time.time() + seconds
    mem = {}
    while time.time() < end:
        s.poll()
        if s.exited or s.fatal:
            return "the engine stopped: " + (s.fatal or "exited")
        st = s.state or {}
        if watcher.next_turn is not None:
            chk.pump(0.5)
            return ""
        move = cc.next_move(st, list(s.requests), mem)
        prompt = st.get("prompt") or {}
        if move is None and prompt.get("message", "").startswith("Priority:") and (prompt.get("ok") or {}).get("enabled"):
            move = ("ok",)                                   # a plain priority with nothing on the stack: next_move leaves it alone
        if move is not None:
            version = s.state_version
            chk.do(move)
            chk.wait_change(version, 1.5)
        else:
            time.sleep(0.05)
    return "the next turn did not begin within %d seconds" % seconds


def run_trial(chk, board, index, say=lambda *_a: None):
    """One trial of one board on a running Checker. Returns a Trial."""
    t = Trial(board, index)
    t0 = time.time()
    s = chk.s
    try:
        if not chk.clean():
            t.error = "could not get back to a clean board"
            return t
        hand = list(board.mine_hand)
        if not chk.apply(board.setup_lines(), hand, board.mine_bf[0] if board.mine_bf and not hand else None):
            t.error = "the board could not be set up"
            return t
        me = s.me()
        ais = [p["id"] for p in s.opponents()]
        ai = ais[0] if board.players == 2 else ais          # 1v1 exactly as before; a pod watches every AI
        with Watcher(s, t, me["id"], ai, me.get("name") or "Checker", until_mine=board.until_mine) as watcher:
            for name in board.plays:                                  # my turn's boards: the scripted plays, one after the other
                card = chk.find_in(name, "hand")
                if not card:
                    t.error = "%s is not in my hand" % name
                    return t
                result = cc.Result(name, "cast")
                chk.drive(result, lambda cid=card["id"]: s.click_card(cid), budget=TRIAL_BUDGET)
                if any(n.startswith(("stalled", "the engine stopped")) for n in result.notes):
                    t.error = "; ".join(result.notes)
                    return t
            # then the turn is played out: on my turn the AI gets every priority of it (an instant "on your turn", I1, can come in
            # my main phase, combat or end step); on the AI's turn (R1-R3) this passes until my turn begins, where the board is read
            turns = board.players - board.active if board.until_mine else 1    # round AI2b: every AI after the active one plays first
            t.error = pass_the_turn(chk, watcher, TURN_WAIT * turns)
            if t.error:
                return t
            for name in sorted(board.my_cards() | set(board.watch)):
                t.zones[name] = where(chk, name)
            t.stack_end = [_entry_text(item, watcher.names) for item in (s.state or {}).get("stack") or []]
        t.verdict = board.judge(t)
    except Exception as exc:                      # a trial never stops the run: it is reported as an error
        t.error = "%s: %s" % (type(exc).__name__, exc)
    finally:
        t.seconds = time.time() - t0
        say("  %s trial %d: %s  %s" % (board.key, index + 1, verdict_word(t), "; ".join(t.ai_entries[:2]) or t.error))
    return t


def verdict_word(t):
    return "error" if t.error else ("yes" if t.verdict else "NO" if t.verdict is False else "?")


def board_keys(boards=None):
    """The boards to run: the ten by default; "extra" = round AI2's six, "all" = all sixteen (1v1), "pod" = the boards for 3-4
    players (P1-P7); else the keys given."""
    if not boards:
        return [b.key for b in BOARDS]
    keys = []
    for k in boards:
        k = k.strip().upper()
        keys += ([b.key for b in EXTRA_BOARDS] if k == "EXTRA" else [b.key for b in ALL_BOARDS] if k == "ALL"
                 else [b.key for b in POD_BOARDS] if k == "POD" else [k])
    return keys


def run(trials=5, seed=7, boards=None, say=print, deck=None, runtime=None, threat_ai=None):
    """Every board, `trials` times each, on one engine. Returns [Trial]. threat_ai=False: Forge's AI alone (round AI2's
    rules off - the baseline); None: the bridge's own default (on)."""
    keys = board_keys(boards)
    unknown = [k for k in keys if k not in BOARD_BY_KEY]
    if unknown:
        raise ValueError("unknown board(s): %s (have %s)" % (", ".join(unknown), ", ".join(BOARD_BY_KEY)))
    # round AI2b: one engine per table size (a pod board needs its 2 or 3 filler AIs), in the order the boards were asked for
    groups = []
    for key in keys:
        n = BOARD_BY_KEY[key].players
        if not groups or groups[-1][0] != n:
            groups.append((n, []))
        groups[-1][1].append(key)
    out = []
    for players, group in groups:
        chk = cc.Checker(deck or KINNAN_DECK, runtime=runtime, seed=seed, say=say, per_check=TRIAL_BUDGET, classic_stops=True,
                         threat_ai=threat_ai, opponents=players - 1)
        say("Starting Forge (seed %d, %d players) ..." % (seed, players))
        chk.start()
        try:
            for key in group:
                board = BOARD_BY_KEY[key]
                say("%s  %s  ->  %s" % (board.key, board.title, board.right))
                for i in range(trials):
                    out.append(run_trial(chk, board, i, say))
        finally:
            chk.stop()
    return out


def score(results):
    """{board key: (yes, judged)} and the total."""
    per = {}
    for t in results:
        yes, n = per.get(t.board, (0, 0))
        if t.verdict is None:
            continue
        per[t.board] = (yes + (1 if t.verdict else 0), n + 1)
    total = (sum(y for y, _n in per.values()), sum(n for _y, n in per.values()))
    return per, total


def threat_words(threat_ai):
    """Round AI2: which AI the run measured."""
    return ("AI2 threat rules: off (Forge's AI alone)" if threat_ai is False
            else "AI2 threat rules: on (the bridge's default)" if threat_ai is None or threat_ai else "")


def summary_text(results, seed, version="", threat_ai=None):
    per, (yes, n) = score(results)
    errors = [t for t in results if t.error]
    lines = ["AI BENCH - does the AI answer the right threat?", "seed %d%s" % (seed, ("  |  " + version) if version else ""),
             threat_words(threat_ai), "score %d/%d" % (yes, n), ""]
    lines.append("%-4s %-62s %-36s %s" % ("", "board", "the right answer", "yes/judged"))
    for b in ALL_BOARDS + POD_BOARDS:
        if b.key not in per and not any(t.board == b.key for t in results):
            continue
        y, k = per.get(b.key, (0, 0))
        acted = sum(1 for t in results if t.board == b.key and t.ai2)
        lines.append("%-4s %-62s %-36s %d/%d%s" % (b.key, b.title[:62], b.right[:36], y, k,
                                                  ("   (AI2 acted in %d)" % acted) if acted else ""))
    pods = [t for t in results if BOARD_BY_KEY.get(t.board) in POD_BOARDS and not t.error]
    if pods:                                          # round AI2b: who answered at the pod tables, and how many counters it took
        lines.append("")
        lines.append("pod tables - what each AI cast (per trial), and counters spent:")
        for b in POD_BOARDS:
            mine = [t for t in pods if t.board == b.key]
            if mine:
                lines.append("    %s: %s" % (b.key, " | ".join("%s (%d)" % (", ".join(t.casts) or "nothing", counters_cast(t))
                                                               for t in mine)))
        not_judged = [t for t in pods if t.verdict is None]
        if not_judged:
            lines.append("    not judged (the spell to answer was never cast): " +
                         ", ".join("%s trial %d" % (t.board, t.index + 1) for t in not_judged))
    lines.append("")
    for b in ALL_BOARDS + POD_BOARDS:
        misses = [t for t in results if t.board == b.key and t.verdict is False]
        if misses:
            lines.append("%s misses (%d): what the AI did:" % (b.key, len(misses)))
            for t in misses[:5]:
                others = ""
                if b in POD_BOARDS and t.boards:     # round AI2b: the AIs' battlefields too, when my turn began
                    others = "; " + "; ".join("%s had %s" % (who, ", ".join(n for n in names if n not in BASIC_NAMES) or "lands")
                                              for who, names in sorted(t.boards.items()) if who != "me")
                lines.append("    trial %d: AI cast %s; stack: %s; ended: %s%s%s%s" % (
                    t.index + 1, ", ".join(t.casts if b in POD_BOARDS else t.ai_cast) or "nothing", " | ".join(t.ai_entries) or "-",
                    ", ".join("%s in %s" % (k, v or "?") for k, v in sorted(t.zones.items())),
                    ("; my turn began with " + ", ".join(t.at_my_turn)) if t.at_my_turn is not None else "",
                    others, ("; still on the stack: " + " | ".join(t.stack_end)) if t.stack_end else ""))
    if errors:
        lines.append("")
        lines.append("errors (%d, not judged):" % len(errors))
        for t in errors[:10]:
            lines.append("    %s trial %d: %s" % (t.board, t.index + 1, t.error))
    return "\n".join(lines) + "\n"


def write_results(folder, results, seed, version="", threat_ai=None):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "ai_bench_results.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": seed, "version": version, "threat_ai": threat_ai is not False, "trials": [t.as_dict() for t in results]},
                  f, indent=1)
    text = summary_text(results, seed, version, threat_ai)
    with open(os.path.join(folder, "ai_bench_summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    return text
