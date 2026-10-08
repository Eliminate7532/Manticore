# SPDX-License-Identifier: GPL-3.0-or-later
"""
stats.py - patch 44: your record and matchups, and when and how your games end (Karl's decisions of 6 Oct 2026,
claude/CLICKS_AND_STATS_2026-10-05.md, section 0, decisions 6-9).

What is kept: stats/games.ndjson (paths.stats_dir()), one JSON object per line, written as each game goes, so a crash or a
closed window never loses a finished game:
    {"t": "start", "v": 1, "id", "at", "format", "online": None | "host" | "guest", "players": 4,
     "deck": {"id", "name", "commanders"}, "opponents": [{"name", "ai", "deck": {"id", "name"} | None, "commanders"}]}
    {"t": "end", "v": 1, "id", "at", "result": "won" | "lost" | "draw" | "conceded" | "unfinished",
     "seat": 1-4, "my_turns", "turn", "how": {"cause", "card", "by"}, "eliminations": [...], "source": "forge" | "table"}
A game is the newest "end" line for its id (a resumed game ends again under the same id). A "start" with no "end" is unfinished.
(.ndjson, not .jsonl: the backup skips *.jsonl - the game journals - and these should be backed up like your decks.)

Only games played to the end (won, lost, draw) count in W-L and win % (decision 7). Conceded and unfinished games are counted
apart, so they don't vanish. Online games are a separate record (decision 9).

How a game ended comes from Forge (the bridge's "result" in game_over, java_bridge Outcome.java: each player's seat, turns
and loss reason, the card of an alternate win). Which card and player did it is worked out here from the bridge's events: the
last damage to that player (its source card), or for a life loss without damage, the spell or ability whose resolution (or
casting, when the life was paid as a cost) came with it. When Forge's result is missing (the engine closed first), the board
decides: life 0, 10 poison, 21 commander damage, an empty library.
No pygame.
"""
import collections
import json
import os
import time
import uuid

import paths

FILE = "games.ndjson"
V = 1

# Forge's GameLossReason -> our cause (Forge's LifeReachedZero is split into combat / damage / life_loss by the events)
LOSS_CAUSE = {"LifeReachedZero": "life", "CommanderDamage": "commander", "Poisoned": "poison", "Milled": "milled",
              "SpellEffect": "lose_effect", "OpponentWon": "alt_win", "Conceded": "conceded", "IntentionalDraw": "draw"}
CAUSE_WORDS = {"combat": "combat damage", "damage": "noncombat damage", "life_loss": "life loss", "life": "life to 0",
               "commander": "commander damage", "poison": "poison", "milled": "an empty library",
               "lose_effect": "a \"lose the game\" card", "alt_win": "an alternate win", "conceded": "a concede",
               "draw": "a draw", "unknown": "unknown"}
COUNTED = ("won", "lost", "draw")
MILL_NEAR = 10                         # a library this small just before the player went out, and empty after: milled
NOT_COUNTED = ("conceded", "unfinished")


def folder():
    return paths.stats_dir()


def stats_path(where=None):
    return os.path.join(where or folder(), FILE)


def new_id():
    return uuid.uuid4().hex[:16]


def append(obj, where=None):
    """Add one line; never raises (stats must not get in the way of a game)."""
    try:
        d = where or folder()
        os.makedirs(d, exist_ok=True)
        with open(stats_path(d), "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        return True
    except (OSError, TypeError, ValueError) as e:
        print(f"[stats] could not write: {e}")
        return False


# ---- one game ---------------------------------------------------------------------------------------------------------------

class Recorder:
    """Follows one game for the player at this table. The table feeds it every snapshot it shows (note_state) and every
    event (note_event), and calls finish() at game over or close() when the game goes away unfinished.

    info: {"id", "format", "online", "deck": {"id", "name"}, "opponent_decks": [{"id", "name"}, ...] (AI 1, AI 2, ... in seat
    order; None or [] when not known), "folder" (tests)}."""

    def __init__(self, info, now=None):
        self.info = dict(info or {})
        self.id = self.info.get("id") or new_id()
        self.where = self.info.get("folder")
        self.started_at = now if now is not None else time.time()
        self.start = None                    # the start line, once written
        self.end = None                      # the end line, once written
        self.me = None
        self.names = {}                      # card id -> name (everything the snapshots have shown)
        self.ctrl = {}                       # card id -> controller player id
        self.players = {}                    # player id -> name
        self.commanders = {}                 # player id -> [commander card ids]
        self.last_hit = {}                   # player id -> {"kind", "card" (id), "name"}
        self._damage = {}                    # player id -> amount of the damage event just seen (its life event follows)
        self._waiting = set()                # player ids with a life loss whose card isn't known yet
        self.lost_at = {}                    # player id -> {"turn", "my_turns", "cause", "card", "by"} when first seen lost
        self._library = {}                   # player id -> their library's size the last time they were still in the game
        self.turns = set()                   # my turns (game turn numbers)
        self.order = []                      # player ids in the order they took their first turn
        self.turn = 0
        self.conceded = False
        self.last_state = None

    # -- feeding
    def note_state(self, st):
        if not st or self.end is not None:
            return
        players = st.get("players") or []
        if not players:
            return
        self.last_state = st
        if self.me is None and st.get("me") is not None:
            self.me = st.get("me")
        for p in players:
            pid = p.get("id")
            self.players[pid] = p.get("name") or "?"
            if p.get("commanders"):
                self.commanders[pid] = list(p.get("commanders"))
            for zone in (p.get("zones") or {}).values():
                for c in zone or []:
                    self._card(c)
        for item in st.get("stack") or []:
            self._card(item.get("card"))
        turn, active = st.get("turn") or 0, st.get("activePlayer")
        if turn:
            self.turn = max(self.turn, turn)
            self._turn(turn, active)
        if self.start is None:
            self._write_start(st)
        for p in players:                    # who is out, and when (the first snapshot that shows it)
            pid = p.get("id")
            if not p.get("lost") and isinstance(p.get("libraryCount"), int):
                # A player who loses a multiplayer game leaves it with all their cards, so an empty library says "milled"
                # only when it was (nearly) empty while they were still playing.
                self._library[pid] = p["libraryCount"]
            if p.get("lost") and pid not in self.lost_at:
                cause, card, by = self._cause(pid, None, p)
                self.lost_at[pid] = {"turn": self.turn, "my_turns": len(self.turns), "cause": cause, "card": card, "by": by}

    def _card(self, c):
        if not c or c.get("id") is None:
            return
        name = c.get("name")
        if name and name != "Face-down card":
            self.names[c["id"]] = name
        if c.get("controller") is not None:
            self.ctrl[c["id"]] = c.get("controller")

    def _turn(self, turn, player):
        if player is None:
            return
        if player not in self.order:
            self.order.append(player)
        if player == self.me:
            self.turns.add(turn)

    def note_event(self, m):
        if self.end is not None or not isinstance(m, dict):
            return
        kind = m.get("kind")
        if kind == "turn":
            t = m.get("turn") or 0
            self.turn = max(self.turn, t)
            self._turn(t, m.get("player"))
        elif kind == "damage_player":
            pid = m.get("player")
            self.last_hit[pid] = {"kind": "combat" if m.get("combat") else "damage", "card": m.get("source")}
            self._damage[pid] = (self._damage.get(pid) or 0) + (m.get("amount") or 0)
            self._waiting.discard(pid)
        elif kind == "life":
            pid, old, new = m.get("player"), m.get("old"), m.get("new")
            if not isinstance(old, int) or not isinstance(new, int) or new >= old:
                return
            lost = old - new
            if self._damage.get(pid):        # the life change of the damage just seen
                self._damage[pid] = max(0, self._damage[pid] - lost)
                return
            self.last_hit[pid] = {"kind": "life_loss", "card": None}
            self._waiting.add(pid)
        elif kind in ("resolve", "cast"):
            # A life loss with no damage belongs to the spell or ability resolving with it (Forge sends "resolve" after the
            # effects), or - when it was a cost - to the spell cast right after it.
            for pid in self._waiting:
                if pid in self.last_hit and self.last_hit[pid].get("card") is None:
                    self.last_hit[pid]["card"] = m.get("card")
            self._waiting.clear()
            self._damage.clear()
        elif kind in ("phase",):
            self._waiting.clear()
            self._damage.clear()

    # -- what happened to a player
    def card_name(self, cid):
        if cid is None:
            return None
        return self.names.get(cid)

    def by_of(self, cid):
        pid = self.ctrl.get(cid)
        return self.players.get(pid) if pid is not None else None

    def _commander_hit(self, pid, player=None):
        dmg = ((player or {}).get("commanderDamage") or {}) if player else {}
        if not dmg and self.last_state:
            for p in self.last_state.get("players") or []:
                if p.get("id") == pid:
                    dmg = p.get("commanderDamage") or {}
        if not dmg:
            return None, None
        cid, _n = max(((int(k), v) for k, v in dmg.items()), key=lambda kv: kv[1])
        return self.card_name(cid), self.by_of(cid)

    def _infer_loss(self, player):
        """Forge's loss reason from the board, when its own isn't known."""
        if not player:
            return None
        if (player.get("life") or 0) <= 0:
            return "LifeReachedZero"
        if (player.get("poison") or 0) >= 10:
            return "Poisoned"
        if any((v or 0) >= 21 for v in (player.get("commanderDamage") or {}).values()):
            return "CommanderDamage"
        left = self._library.get(player.get("id"))
        if left is not None and left <= MILL_NEAR and player.get("libraryCount") == 0:
            return "Milled"
        return None

    def _cause(self, pid, row, player=None, outcome=None):
        """(cause, card, by) for player pid. row: Forge's result row for the player (or None)."""
        if not (row or {}).get("loss") and pid in self.lost_at and self.lost_at[pid].get("cause") != "unknown":
            at = self.lost_at[pid]                   # without Forge's word, the board as it was when they went out
            return at["cause"], at["card"], at["by"]
        loss = (row or {}).get("loss") or self._infer_loss(player)
        cause = LOSS_CAUSE.get(loss, "unknown") if loss else "unknown"
        card = by = None
        if cause == "life":
            hit = self.last_hit.get(pid)
            if hit:
                cause = hit["kind"]
                card = self.card_name(hit.get("card"))
                by = self.by_of(hit.get("card"))
        elif cause == "commander":
            card, by = self._commander_hit(pid, player)
        elif cause == "poison":
            hit = self.last_hit.get(pid)
            if hit and hit.get("card") is not None:
                card, by = self.card_name(hit["card"]), self.by_of(hit["card"])
        elif cause == "lose_effect":
            card = (row or {}).get("lossSpell")
        elif cause == "alt_win":
            for r in (outcome or {}).get("players") or []:
                if r.get("won"):
                    card = r.get("altWin") or (outcome or {}).get("winSpell")
                    by = r.get("name")
            card = card or (outcome or {}).get("winSpell")
        return cause, card, by

    # -- the lines
    def _write_start(self, st):
        me = self.me
        opp_decks = list(self.info.get("opponent_decks") or [])
        opps, i = [], 0
        for p in st.get("players") or []:
            if p.get("id") == me:
                continue
            deck = opp_decks[i] if i < len(opp_decks) else None
            i += 1
            opps.append({"name": p.get("name") or "?", "ai": bool(p.get("ai")), "deck": deck,
                         "commanders": self._commander_names(p.get("id"))})
        self.start = {"t": "start", "v": V, "id": self.id, "at": round(self.started_at, 3), "format": self.info.get("format") or "commander",
                      "online": self.info.get("online") or None, "players": len(st.get("players") or []),
                      "deck": dict(self.info.get("deck") or {}, commanders=self._commander_names(me)), "opponents": opps}
        if self.info.get("resumed"):
            self.start["resumed"] = True
        append(self.start, self.where)

    def _commander_names(self, pid):
        return [n for n in (self.card_name(c) for c in self.commanders.get(pid) or []) if n]

    def my_seat(self, outcome=None):
        for r in (outcome or {}).get("players") or []:
            if r.get("id") == self.me and r.get("seat"):
                return r["seat"]
        if self.me in self.order:
            return self.order.index(self.me) + 1
        return len(self.order) + 1 if self.order else None

    def finish(self, outcome=None, state=None, now=None, result=None):
        """The game is over (or gone): write the end line once and return it. outcome: the bridge's "result" (or None).
        result forces "unfinished" / "conceded" / "lost" for a game that went away before Forge finished it."""
        if self.end is not None:
            return self.end
        if state is not None:
            self.note_state(state)
        if self.start is None:
            return None                      # never saw the game start (no snapshot with players): nothing to record
        st = self.last_state or {}
        outcome = outcome if isinstance(outcome, dict) and outcome.get("players") else None
        me = self.me
        mine = next((r for r in (outcome or {}).get("players") or [] if r.get("id") == me), None)
        players = {p.get("id"): p for p in st.get("players") or []}
        if result is None:
            if mine is not None:
                if outcome.get("draw") or mine.get("loss") == "IntentionalDraw":
                    result = "draw"
                elif mine.get("won"):
                    result = "won"
                elif mine.get("loss") == "Conceded" or self.conceded:
                    result = "conceded"
                else:
                    result = "lost"
            else:
                winner = st.get("winner")
                my_name = self.players.get(me)
                if not st.get("gameOver") and not (players.get(me) or {}).get("lost"):
                    result = "conceded" if self.conceded else "unfinished"
                elif self.conceded:
                    result = "conceded"
                elif winner and winner == my_name:
                    result = "won"
                elif st.get("gameOver") and not winner:
                    result = "draw"
                else:
                    result = "lost"
        how = {}
        if result in ("lost", "conceded"):
            cause, card, by = self._cause(me, mine, players.get(me), outcome)
            if result == "conceded" or (mine or {}).get("loss") == "Conceded":
                cause, card, by = "conceded", None, None
            how = {"cause": cause, "card": card, "by": by}
        elif result == "won":
            if mine and (mine.get("altWin") or (outcome or {}).get("endReason") == "WinsGameSpellEffect"):
                how = {"cause": "alt_win", "card": mine.get("altWin") or outcome.get("winSpell"), "by": self.players.get(me)}
            else:
                last = self._last_opponent_out(outcome, players)
                if last is not None:
                    cause, card, by = self._cause(last, self._row(outcome, last), players.get(last), outcome)
                    how = {"cause": cause, "card": card, "by": by}
        elif result == "draw":
            how = {"cause": "draw"}
        if how.get("by") and how.get("by") == self.players.get(me):
            how["mine"] = True               # my own card (a won game's, or a Toxic Deluge's life)
        elims = []
        for pid, name in self.players.items():
            if pid == me:
                continue
            row = self._row(outcome, pid)
            out = (row is not None and row.get("won") is False) or (players.get(pid) or {}).get("lost") or pid in self.lost_at
            if not out:
                continue
            at = self.lost_at.get(pid) or {}
            cause, card, by = self._cause(pid, row, players.get(pid), outcome)
            elims.append({"name": name, "cause": cause, "card": card, "by": by, "turn": at.get("turn", self.turn)})
        my_turns = (mine or {}).get("turns")
        self.end = {"t": "end", "v": V, "id": self.id, "at": round(now if now is not None else time.time(), 3), "result": result,
                    "seat": self.my_seat(outcome), "my_turns": my_turns if isinstance(my_turns, int) else len(self.turns),
                    "turn": (outcome or {}).get("lastTurn") or self.turn, "how": how, "eliminations": elims,
                    "source": "forge" if mine is not None else "table"}
        if mine is not None and isinstance(mine.get("mulligans"), int):
            self.end["mulligans"] = mine["mulligans"]       # recorded for a later build; not shown (decision 6)
        append(self.end, self.where)
        return self.end

    @staticmethod
    def _row(outcome, pid):
        return next((r for r in (outcome or {}).get("players") or [] if r.get("id") == pid), None)

    def _last_opponent_out(self, outcome, players):
        """The opponent who went out last (the one whose loss won me the game)."""
        others = [pid for pid in self.players if pid != self.me]
        lost = [pid for pid in others if (self._row(outcome, pid) or {}).get("won") is False or (players.get(pid) or {}).get("lost")]
        if not lost:
            return others[-1] if others else None
        return max(lost, key=lambda pid: (self.lost_at.get(pid, {}).get("turn", 10 ** 6), others.index(pid)))

    def close(self, now=None):
        """The game went away before Forge finished it (a new game, the main menu, the program closing): conceded if I
        conceded, lost if I was already out, else unfinished. Nothing is written for a game that never showed a board."""
        if self.end is not None or self.start is None:
            return self.end
        st = self.last_state or {}
        me = next((p for p in st.get("players") or [] if p.get("id") == self.me), None)
        if self.conceded:
            return self.finish(now=now, result="conceded")
        if me and me.get("lost"):
            return self.finish(now=now, result="lost")
        if st.get("gameOver"):
            return self.finish(now=now)
        return self.finish(now=now, result="unfinished")

    # -- for the table
    def out_line(self):
        """While I am out of a pod that plays on: "Out on your turn 6 - AI 2's combat damage (Grizzly Bears)." or ""."""
        st = self.last_state or {}
        me = next((p for p in st.get("players") or [] if p.get("id") == self.me), None)
        if not me or not me.get("lost"):
            return ""
        cause, card, by = self._cause(self.me, None, me)
        how = {"cause": cause, "card": card, "by": by, "mine": bool(by) and by == self.players.get(self.me)}
        return "Out on your turn %d - %s." % (len(self.turns), how_words(how))


# ---- words ------------------------------------------------------------------------------------------------------------------

def how_words(how, mine=False):
    """'combat damage from AI 2's Grizzly Bears', 'an alternate win with your Thassa's Oracle', 'commander damage' ..."""
    how = how or {}
    mine = mine or bool(how.get("mine"))
    cause = how.get("cause") or "unknown"
    words = CAUSE_WORDS.get(cause, cause)
    card, by = how.get("card"), how.get("by")
    if not card:
        return words + (f" ({by})" if by and cause not in ("conceded", "draw") else "")
    who = "your" if mine else (f"{by}'s" if by else "")
    joiner = "with" if cause in ("alt_win",) else "from"
    return f"{words} {joiner} {who + ' ' if who else ''}{card}".strip()


def end_lines(end, summary=None, deck_name=None):
    """Two short lines for the end-of-game screen: how the game ended, and this deck's record."""
    if not end:
        return []
    r, how = end.get("result"), end.get("how") or {}
    when = f"your turn {end.get('my_turns')}" if end.get("my_turns") else "your first turn"
    if r == "won":
        first = f"Won on {when} (game turn {end.get('turn')}) - {how_words(how)}." if how else f"Won on {when}."
    elif r == "lost":
        first = f"Lost on {when} (game turn {end.get('turn')}) - {how_words(how)}."
    elif r == "draw":
        first = f"A draw on {when}."
    elif r == "conceded":
        first = f"Conceded on {when} - not counted in your record."
    else:
        first = "Not finished - not counted in your record."
    out = [first]
    if summary:
        name = deck_name or "This deck"
        where = " online" if summary.get("online") else ""
        out.append(f"{name}{where}: {record_words(summary)}.")
    return out


def record_words(s):
    w, l, d = s.get("won", 0), s.get("lost", 0), s.get("draw", 0)
    text = f"{w} won, {l} lost" + (f", {d} drawn" if d else "")
    played = w + l + d
    if played:
        text += f" ({pct(w, played)})"
    return text


def pct(n, total):
    return f"{round(100 * n / total)}%" if total else "-"


# ---- reading it back ----------------------------------------------------------------------------------------------------------

def load(where=None):
    """Every game: [{"id", "start": {...}, "end": {...} | None}], oldest first. A torn last line is skipped."""
    games = collections.OrderedDict()
    try:
        with open(stats_path(where), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or not obj.get("id"):
                    continue
                g = games.setdefault(obj["id"], {"id": obj["id"], "start": None, "end": None})
                if obj.get("t") == "start":
                    if g["start"] is None:
                        g["start"] = obj
                elif obj.get("t") == "end":
                    if g["end"] is None or g["end"].get("result") == "unfinished" or obj.get("result") != "unfinished":
                        g["end"] = obj                # a resumed game's later end replaces "unfinished"
    except OSError:
        return []
    return [g for g in games.values() if g["start"] is not None]


def result_of(g, current_id=None):
    if g.get("end"):
        return g["end"].get("result") or "unfinished"
    return "playing" if g["id"] == current_id else "unfinished"


def opponent_label(o):
    deck = o.get("deck") or {}
    if deck.get("name"):
        return deck["name"]
    cmd = " + ".join(o.get("commanders") or [])
    return cmd or o.get("name") or "?"


def deck_key(start):
    deck = (start or {}).get("deck") or {}
    return deck.get("id") or ("cmd:" + " + ".join(deck.get("commanders") or [])) or "?"


def summarize(games, deck_id=None, online=False, current_id=None):
    """The numbers for one deck (deck_id; None = every deck), local games or online ones."""
    s = {"online": online, "won": 0, "lost": 0, "draw": 0, "conceded": 0, "unfinished": 0, "games": 0,
         "pods": collections.defaultdict(lambda: [0, 0, 0]), "seats": collections.defaultdict(lambda: [0, 0, 0]),
         "opponents": collections.defaultdict(lambda: [0, 0, 0]), "win_turns": [], "loss_turns": [],
         "won_by": collections.Counter(), "lost_to": collections.Counter(), "killers": collections.Counter(),
         "win_cards": collections.Counter(), "name": None, "last": None}
    for g in games:
        st = g["start"]
        if bool(st.get("online")) != bool(online):
            continue
        if deck_id is not None and deck_key(st) != deck_id:
            continue
        r = result_of(g, current_id)
        if r == "playing":
            continue
        s["games"] += 1
        s["name"] = (st.get("deck") or {}).get("name") or s["name"]
        s["last"] = g
        if r not in COUNTED:
            s[r if r in NOT_COUNTED else "unfinished"] += 1
            continue
        s[r] += 1
        i = COUNTED.index(r)
        end = g["end"] or {}
        s["pods"][st.get("players") or 0][i] += 1
        if end.get("seat"):
            s["seats"][end["seat"]][i] += 1
        for o in st.get("opponents") or []:
            s["opponents"][opponent_label(o)][i] += 1
        how = end.get("how") or {}
        if r == "won":
            s["win_turns"].append(end.get("my_turns") or 0)
            s["won_by"][how.get("cause") or "unknown"] += 1
            if how.get("card"):
                s["win_cards"][how["card"]] += 1
        elif r == "lost":
            s["loss_turns"].append(end.get("my_turns") or 0)
            s["lost_to"][how.get("cause") or "unknown"] += 1
            if how.get("card"):
                s["killers"][(how["card"], how.get("by"))] += 1
    return s


def decks(games, current_id=None):
    """[(deck_key, name, local summary, online summary)] for every deck with a game, most games first."""
    keys = collections.OrderedDict()
    for g in games:
        keys.setdefault(deck_key(g["start"]), (g["start"].get("deck") or {}).get("name") or "?")
    out = []
    for k, name in keys.items():
        loc = summarize(games, k, False, current_id)
        onl = summarize(games, k, True, current_id)
        out.append((k, loc["name"] or onl["name"] or name, loc, onl))
    out.sort(key=lambda row: -(row[2]["games"] + row[3]["games"]))
    return out


def turn_words(turns):
    """'average 7.3, fastest 5' for a list of your-turn counts."""
    turns = [t for t in turns if t]
    if not turns:
        return "-"
    return f"average {sum(turns) / len(turns):.1f}, fastest {min(turns)}, slowest {max(turns)}"
