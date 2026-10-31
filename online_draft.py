# SPDX-License-Identifier: GPL-3.0-or-later
"""
online_draft.py - Round CD5: drafting a Commander cube together online, then one 4-player game. No pygame, no sockets.

Karl, 27 Oct 2026 (~17:40 UTC): "Draft together, Commander Cube first"; "No timer"; a friend who drops mid-draft: "Everyone waits".

The host's table runs the draft exactly as offline (draft_core / draft_pod / draft_ai): the host is seat 0, the friends who joined
are seats 1.. in the order they joined, the AI drafters fill the rest. NetHost (--draft) only carries the messages: HostDraft.send
is HostSession.draft_send, and what a friend sends reaches HostDraft.on_message. The friends' tables hold a GuestPod: just enough
of a draft_pod.Pod for the same pick, review and deck-building screens, filled from the host's messages.

903.13d - each person sees only the pack in front of them and their own picks. So the host never sends a seat's pack or picks to
anyone else: a friend's "view" carries that friend's pack and pool, and of the others only their names, how many cards each holds
and who is still picking (the draft waits for them: no pick timer). The cube's card list is sent to every friend at the start: it
is the cube itself (public - anyone can look it up on Cube Cobra), in the cube's order, not the packs' (the seed never leaves the
host). After the last pick the review shows a friend their own picks and the AI drafters' (Claude's default in the brief: not the
other people's).

Every message from a friend is checked here: a pick must be that seat's current pack, the right number of cards, at the right
moment ("at": the round and pick it was made for - a click on an old screen is refused, not taken from the next pack); a deck may
only use that seat's drafted cards (and basic lands), and its commander must be legal (only a commander problem stops a deck, as
offline - draft_builder.Builder.blocking).

A friend who drops: the draft pauses for everyone ("Waiting for Sam to come back") until Sam is back (NetHost keeps the seat; the
rejoin token, or the password and the same name) or the host presses "Let the AI pick for Sam" - then the AI picks for that seat
until Sam is back, and Sam takes the seat back from the next pick (Claude's default in the brief). A friend who leaves on purpose
(closes the draft) gives the seat to the AI for good. During deck building the same: the AI's Auto-build stands in for a friend
who is away, and that seat is an AI player in the game unless the friend is back before it starts.

Message kinds ("k"), host -> friend (NetHost wraps each as {"t":"draft","msg":...}):
  cards   {part, of, fields, cards: [[...], ...]}   the cube's cards, in chunks of CARDS_PER_PART
  start   {seat, names, people, cube, style, rules, basics, packs, pack_size, picks, id, created}
  view    {stage, label, round, turn, need, pack, pool, counts, waiting, paused, passing, ai_for_you, done}
  refused {text}
  review  {picks: [[seat, round, turn, cards, pack_before], ...]}       (yours and the AI drafters')
  build   {ready: {name: bool}, waiting: [names], paused: [names], deck_ok, deck_refused}
  deck_ok / deck_refused {text}
friend -> host ({"c":"draft","msg":...}, NetHost hands it over as {"t":"draft_from","seat","name","msg"}):
  pick    {cards: [ints], at: [round, pick]}
  deck    {commanders: [ints], main: [ints], basics: {name: count}, by_hand}
  unready {}
  view    {}                                 (send me everything again: after my table reconnected or reopened)
  leave   {}                                 (I'm going for good: the AI takes my seat)
"""
import random
import time
from collections import namedtuple

import cube_source as cs
import draft_core as dc
import draft_deck as dd
import draft_pod as dp

CARDS_PER_PART = 60
MSG_KINDS = frozenset({"pick", "deck", "unready", "view", "leave"})
BASIC_NAMES = frozenset(cs.BASIC_NAME.values())
MAX_FRIENDS = 3
STAGES = ("pick", "build", "game")


def _ints(value, limit):
    """A list of ints from a friend's message, or None when it isn't one (or is longer than `limit`)."""
    if not isinstance(value, list) or len(value) > limit:
        return None
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in value):
        return None
    return list(value)


def names_text(names):
    names = list(names)
    if not names:
        return ""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def deck_of(cards, commanders, main, basics):
    """A draft_deck.Deck from card ints (commanders, the rest) and basic land counts - as draft_builder.Builder.deck() makes it."""
    spells = [i for i in main if not cs.is_land(cards[i])]
    lands = [i for i in main if cs.is_land(cards[i])]
    return dd.Deck(tuple(commanders), tuple(spells), tuple(lands), dict(basics), dd.identity_of(cards, list(commanders)), 0.0, 0)


def deck_message(deck, by_hand=False):
    """What a friend's table sends for its deck."""
    return {"k": "deck", "commanders": list(deck.commanders), "main": list(deck.spells) + list(deck.lands),
            "basics": {str(n): int(c) for n, c in deck.basics.items() if c}, "by_hand": bool(by_hand)}


# ==================================================================================================================================
class HostDraft:
    """The host's side. `friends` = [(net seat, name)] in the order they joined (draft seats 1..); `send(net_seat, msg)` sends one
    draft message to that friend's table (dropped by NetHost while the friend is away - everything is sent again on return)."""

    def __init__(self, pod, friends, send, now=time.monotonic):
        """friends: [(net seat, name)] or [(net seat, name, connected)] - draft seats 1.. in this order. A continued draft (the
        host's program had closed: Continue it) passes them in the pod's order, connected False for those not back yet."""
        self.pod = pod
        self.send = send
        self.now = now
        self.net_of = {d: int(f[0]) for d, f in enumerate(friends, 1)}               # draft seat -> NetHost seat
        self.seat_of = {ns: d for d, ns in self.net_of.items()}
        self.connected = {d: (bool(f[2]) if len(f) > 2 else True) for d, f in enumerate(friends, 1)}
        online = pod.online or {}
        # friends' seats the AI picks for, until the friend is back (the host chose) - kept with the pod, so Continue it keeps it
        self.ai_for = {int(d) for d in online.get("ai_for") or () if int(d) in self.net_of}
        self.left = {int(d) for d in online.get("left") or () if int(d) in self.net_of}   # left for good: the AI's seat
        self.decks = {}              # draft seat -> draft_deck.Deck, once that seat is ready
        self.by_hand = set()
        self.stage = "build" if pod.done else "pick"
        self.refusals = []           # (draft seat, text) of every refused message - the tests and the soak read it
        # every friend's table numbers its picks ("n", per table "sid"): a pick seen once is never taken (or refused) again - a
        # table that reconnected sends its pick again, in case the first went down with the old connection
        self.last_pick = {}          # draft seat -> (sid, n)
        self.sent = 0
        self.changed = 0             # bumped on every change, for the host's screen

    # ---- who is where ---------------------------------------------------------------------------------------------------------
    @classmethod
    def seats_for(cls, host_name, friend_names, n=dc.SEATS):
        """The pod's seats: the host, the friends ("remote"), then AI drafters "AI 1".. to n seats."""
        seats = [(host_name or "Host", "human")] + [(name, "remote") for name in friend_names]
        k = 1
        while len(seats) < n:
            seats.append((f"AI {k}", "ai"))
            k += 1
        return seats

    def friends(self):
        return sorted(self.net_of)

    def name(self, seat):
        return self.pod.seat_name(seat)

    def is_ai(self, seat):
        """The AI picks (and builds) for this seat now: an AI drafter, or a friend's seat given to the AI."""
        return self.pod.draft.seats[seat].kind == "ai" or seat in self.ai_for or seat in self.left

    def ai_seats(self):
        return {s for s in range(self.pod.draft.n) if self.is_ai(s)}

    def paused_on(self):
        """The friends the draft waits for: away, and the host hasn't given their seat to the AI."""
        return [d for d in self.friends() if not self.connected[d] and not self.is_ai(d)]

    def people(self):
        return [0] + self.friends()

    def waiting_for(self):
        """The people (not AI seats) who have a pack in front of them: the ones everyone else waits for."""
        if self.stage != "pick":
            return []
        return [s for s in self.pod.draft.seats_to_act() if not self.is_ai(s)]

    def paused_text(self):
        names = [self.name(d) for d in self.paused_on()]
        return f"Waiting for {names_text(names)} to come back." if names else ""

    # ---- the draft --------------------------------------------------------------------------------------------------------
    def start(self):
        """Every friend gets the cube's cards and where they sit; the AI drafters make their first picks."""
        self._run_ai()
        self._save()
        for d in self.friends():
            self._send_all(d)
        self.changed += 1

    def _run_ai(self):
        if self.paused_on() or self.stage != "pick":
            return []
        made = self.pod.run_ai(self.ai_seats())
        if self.pod.done:
            self._to_build()
        return made

    def host_pick(self, cards):
        """The host's own pick (the draft window's Confirm). Raises draft_core.DraftError for anything not allowed."""
        if self.stage != "pick":
            raise dc.DraftError("The draft is over.")
        if self.paused_on():
            raise dc.DraftError(self.paused_text())
        self.pod.draft.pick(0, list(cards))
        self._after_change()

    def _save(self):
        self.pod.online = dict(self.pod.online or {}, ai_for=sorted(self.ai_for), left=sorted(self.left))
        self.pod.save()

    def _after_change(self):
        self._run_ai()
        self._save()
        self.broadcast()
        self.changed += 1

    def _pick(self, seat, msg):
        if self.stage != "pick":
            return "The draft is over."
        if self.is_ai(seat):
            return "The AI is picking for you right now."
        if self.paused_on():
            return self.paused_text()
        cards = _ints(msg.get("cards"), self.pod.draft.picks)
        if cards is None:
            return "That isn't a pick."
        at = msg.get("at")
        draft = self.pod.draft
        if not (isinstance(at, list) and len(at) == 2 and at == [draft.round, draft.turns[seat]]):
            return "That pick was for a pack you no longer have."
        try:
            draft.pick(seat, cards)
        except dc.DraftError as e:
            return str(e)
        self._after_change()
        return None

    # ---- after the draft: the decks ---------------------------------------------------------------------------------------
    def _to_build(self):
        self.stage = "build"
        self.pod.stage = "built"
        for s in self.ai_seats():
            self.decks.setdefault(s, self.pod.build(s))
        for d in self.friends():
            if self.connected[d]:
                self.send_review(d)

    def review_for(self, seat):
        """The picks a seat may see after the draft: its own and the AI drafters' (never another person's)."""
        show = {seat} | {s for s in range(self.pod.draft.n) if self.pod.draft.seats[s].kind == "ai"}
        return [[p.seat, p.round, p.turn, list(p.cards), list(p.pack_before)] for p in self.pod.draft.log if p.seat in show]

    def send_review(self, d):
        self._send(d, {"k": "review", "picks": self.review_for(d)})

    def _deck(self, seat, msg):
        if self.stage != "build":
            return "The decks aren't being built yet."
        cards, pool = self.pod.cards, self.pod.pool(seat)
        commanders = _ints(msg.get("commanders"), 2)
        main = _ints(msg.get("main"), 200)
        basics = msg.get("basics")
        if commanders is None or main is None or not isinstance(basics, dict) or len(basics) > len(BASIC_NAMES):
            return "That isn't a deck."
        clean = {}
        for name, count in basics.items():
            if name not in BASIC_NAMES or not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 99:
                return "That isn't a deck (basic lands)."
            if count:
                clean[name] = count
        if any(not 0 <= i < len(cards) for i in commanders + main):
            return "That deck has a card that isn't in this cube."
        names = [n for n, c in clean.items() for _ in range(c)]
        problems = dd.check(cards, pool, commanders, main + names, size=0)
        theft = [p for p in problems if "isn't one of your drafted cards" in p or " drafted." in p]
        if theft:
            return theft[0]
        blocking = dd.check(cards, pool, commanders, [], size=0)      # only a commander problem stops a deck (BAN1/D7)
        if blocking:
            return blocking[0]
        self.decks[seat] = deck_of(cards, commanders, main, clean)
        if msg.get("by_hand"):
            self.by_hand.add(seat)
        else:
            self.by_hand.discard(seat)
        return None

    def host_deck(self, deck, by_hand=False):
        """The host is ready with this deck (draft_deck.Deck)."""
        if self.stage != "build":
            raise dc.DraftError("The decks aren't being built yet.")
        self.decks[0] = deck
        (self.by_hand.add if by_hand else self.by_hand.discard)(0)
        self.broadcast()
        self.changed += 1

    def host_unready(self):
        self.decks.pop(0, None)
        self.broadcast()
        self.changed += 1

    def ready(self):
        """{seat: True/False} for every person."""
        return {s: s in self.decks for s in self.people()}

    def all_ready(self):
        """Every seat has a deck, and every friend who plays is here (an AI seat's deck is its Auto-build)."""
        if self.stage != "build" or self.paused_on():
            return False
        return all(s in self.decks for s in range(self.pod.draft.n))

    def plan(self):
        """Who plays what: {"host": Deck, "friends": [(net seat, draft seat, Deck)], "ai": [(draft seat, Deck)]} - the AI seats are
        the AI drafters and any friend's seat the AI took over (that friend gave it up, or wasn't back in time)."""
        friends = [(self.net_of[d], d, self.decks[d]) for d in self.friends() if not self.is_ai(d) and self.connected[d]]
        playing = {d for _ns, d, _deck in friends}
        ai = [(s, self.decks[s]) for s in range(1, self.pod.draft.n) if s not in playing and self.decks.get(s) is not None]
        return {"host": self.decks.get(0), "friends": friends, "ai": ai}

    def to_game(self):
        self.stage = "game"
        self.changed += 1

    # ---- messages ---------------------------------------------------------------------------------------------------------
    def on_message(self, net_seat, msg):
        """A friend's draft message. Returns None when it was taken, else the refusal (which is also sent back)."""
        d = self.seat_of.get(net_seat)
        if d is None:
            return "unknown seat"
        if not isinstance(msg, dict) or msg.get("k") not in MSG_KINDS:
            return self._refuse(d, "That message isn't part of a draft.")
        k = msg["k"]
        if k == "view":
            self._send_all(d)
            return None
        if k == "leave":
            self.on_left(net_seat)
            return None
        if k == "pick":
            n, sid = msg.get("n"), msg.get("sid")
            numbered = isinstance(n, int) and not isinstance(n, bool)
            if numbered:
                seen = self.last_pick.get(d)
                if seen is not None and seen[0] == sid and n <= seen[1]:
                    return None                      # this pick was handled already (it was sent again after a reconnect)
            why = self._pick(d, msg)
            if numbered and (why is None or self.connected.get(d)):
                self.last_pick[d] = (sid, n)          # (a refusal that can't reach an away table isn't "handled": the pick
            return self._refuse(d, why, n) if why else None     # comes again when it's back)
        if k == "deck":
            why = self._deck(d, msg)
            if why:
                self._send(d, {"k": "deck_refused", "text": why})
                return self._refuse(d, why)
            self._send(d, {"k": "deck_ok"})
            self.broadcast()
            self.changed += 1
            return None
        if k == "unready":
            if self.stage == "build":
                self.decks.pop(d, None)
                self.broadcast()
                self.changed += 1
            return None
        return None

    def _refuse(self, d, text, n=None):
        self.refusals.append((d, text))
        self._send(d, {"k": "refused", "text": text, **({"n": n} if isinstance(n, int) else {})})
        self._send(d, self.view_for(d))
        return text

    def on_drop(self, net_seat):
        d = self.seat_of.get(net_seat)
        if d is None or not self.connected[d]:
            return
        self.connected[d] = False
        self.broadcast()
        self.changed += 1

    def on_back(self, net_seat):
        """A friend is back (token or name): their seat is theirs again from the next pick, and they get everything again."""
        d = self.seat_of.get(net_seat)
        if d is None or d in self.left:
            return
        self.connected[d] = True
        self.ai_for.discard(d)
        if self.stage == "build" and d in self.decks and d not in self.by_hand:
            self.decks.pop(d)                       # the AI's stand-in deck goes: they build their own (Auto-build is a click)
        self._send_all(d)
        self._run_ai()
        self.broadcast()
        self.changed += 1

    def on_left(self, net_seat):
        d = self.seat_of.get(net_seat)
        if d is None:
            return
        self.connected[d] = False
        self.left.add(d)
        self._take_over(d)

    def give_to_ai(self, seat):
        """The host's "Let the AI pick for Sam": the AI picks (or builds) for that seat until Sam is back."""
        if seat not in self.net_of or self.is_ai(seat):
            return False
        self.ai_for.add(seat)
        self._take_over(seat)
        return True

    def _take_over(self, d):
        if self.stage == "build":
            self.decks.setdefault(d, self.pod.build(d))
        self._run_ai()
        self._save()
        self.broadcast()
        self.changed += 1

    # ---- what each friend is told -----------------------------------------------------------------------------------------
    def start_message(self, d):
        """Where friend d sits. Never the seed (it would give away every pack)."""
        pod, n = self.pod, self.pod.draft.n
        return {"k": "start", "seat": d, "names": [self.name(s) for s in range(n)],
                "people": [s for s in range(n) if pod.draft.seats[s].kind != "ai"],
                "cube": dict(pod.cube), "style": pod.style, "rules": list(pod.rules), "basics": list(pod.basics),
                "packs": pod.draft.packs, "pack_size": pod.draft.pack_size, "picks": pod.draft.picks, "id": pod.id,
                "created": pod.created, "flagged": sorted(pod.flagged)}

    def view_for(self, d):
        """Seat d's own view: its pack and its picks, and of the others only names, counts and who is picking."""
        draft = self.pod.draft
        word = "left" if draft.direction() > 0 else "right"
        paused = [self.name(s) for s in self.paused_on()]
        msg = {"k": "view", "stage": self.stage, "done": draft.done, "counts": [len(p) for p in draft.pools],
               "pool": list(draft.pools[d]), "paused": paused, "ai_for_you": self.is_ai(d),
               "waiting": [self.name(s) for s in self.waiting_for() if s != d]}
        if not draft.done:
            msg.update(label=draft.label(d), round=draft.round, turn=draft.turns[d], need=draft.needed(d),
                       pack=draft.pack_for(d), passing=[word, self.name(draft.next_seat(d))])
        if self.stage == "build":
            msg["ready"] = {self.name(s): s in self.decks for s in self.people()}
            msg["mine_ready"] = d in self.decks
        return msg

    def _send(self, d, msg):
        if self.connected.get(d):
            self.sent += 1
            self.send(self.net_of[d], msg)

    def _send_all(self, d):
        """Everything a friend's table needs, from nothing: the cards, where they sit, their view (and the review once done)."""
        if not self.connected.get(d):
            return
        cards = self.pod.cards
        fields = list(cs.CubeCard._fields)
        parts = max(1, -(-len(cards) // CARDS_PER_PART))
        for k in range(parts):
            chunk = cards[k * CARDS_PER_PART:(k + 1) * CARDS_PER_PART]
            self._send(d, {"k": "cards", "part": k, "of": parts, "fields": fields, "cards": [list(c) for c in chunk]})
        self._send(d, self.start_message(d))
        if self.stage != "pick":
            self.send_review(d)
        self._send(d, self.view_for(d))

    def broadcast(self):
        for d in self.friends():
            self._send(d, self.view_for(d))


# ==================================================================================================================================
GuestPick = namedtuple("GuestPick", "seat round turn cards pack_before")


class _GuestDraft:
    """The bits of draft_core.Draft the screens read on a friend's table: the seats, the pack size, the review."""

    def __init__(self, guest):
        self.g = guest

    @property
    def n(self):
        return len(self.g.names)

    @property
    def pack_size(self):
        return self.g.pack_size

    @property
    def packs(self):
        return self.g.packs

    @property
    def picks(self):
        return self.g.picks_per

    @property
    def done(self):
        return self.g.done

    @property
    def seats(self):
        return [dc.Seat(self.g.seat_name(s), "ai" if s not in self.g.people_local else "remote") for s in range(self.n)]

    def review(self, seat=None):
        return [p for p in self.g.review_picks if seat is None or p.seat == seat]


class GuestPod(dp.Pod):
    """A friend's table's view of the host's draft, made from the host's messages (feed()), as much of a draft_pod.Pod as the
    pick, review and deck-building screens use. The friend is seat 0 here: the host's seat numbers are turned so that seat 0 is
    me and "left" is still the next seat, so every screen that says "you" for seat 0 works unchanged."""

    def __init__(self, send):
        super().__init__(folder="", cube_meta={}, cards=[], flagged=(), missing=(), draft=None, created=0)
        self.send = send
        self.draft = _GuestDraft(self)
        self.real_seat = None
        self.names = []
        self.people_local = set()
        self.pack_size, self.packs, self.picks_per = dc.PACK_SIZE, dc.PACKS, dc.PICKS
        self._parts = {}
        self._fields = None
        self.view = None
        self.review_picks = []
        self.started = False
        self.pending = None           # the pick sent and not answered yet (the screen shows it as taken)
        self.pending_at = None        # ... and the [round, pick] it was for
        self.pick_n = 0               # my picks, numbered: the host's refusal names the one it refuses
        self.pending_msg = None
        self.sid = "%08x" % random.SystemRandom().getrandbits(32)      # this table (a reopened one numbers from 1 again)
        self.refused = None           # the host's last refusal, for the screen
        self.deck_state = None        # None, "sent", "ok" or the refusal text
        self.version = 0

    # ---- the host's messages ----------------------------------------------------------------------------------------------
    def local(self, real):
        return (real - self.real_seat) % len(self.names) if self.names else real

    def real(self, local):
        return (local + self.real_seat) % len(self.names) if self.names else local

    def feed(self, msg):
        """One message from the host's table. Returns True when anything changed."""
        if not isinstance(msg, dict):
            return False
        k = msg.get("k")
        self.version += 1
        if k == "cards":
            self._fields = msg.get("fields") or self._fields
            self._parts[int(msg.get("part") or 0)] = msg.get("cards") or []
            if len(self._parts) >= int(msg.get("of") or 1):
                fields = self._fields or list(cs.CubeCard._fields)
                rows = [row for k2 in sorted(self._parts) for row in self._parts[k2]]
                self.cards = [cs.CubeCard(**{f: (tuple(v) if isinstance(v, list) else v) for f, v in zip(fields, row)})
                              for row in rows]
                self._z = None
            return True
        if k == "start":
            self.real_seat = int(msg.get("seat") or 0)
            self.names = list(msg.get("names") or [])
            self.people_local = {self.local(s) for s in msg.get("people") or []}
            self.cube = dict(msg.get("cube") or {})
            self.style = msg.get("style") or "commander"
            self.rules = tuple(msg.get("rules") or ())
            self.basics = tuple(msg.get("basics") or ())
            self.packs = int(msg.get("packs") or dc.PACKS)
            self.pack_size = int(msg.get("pack_size") or dc.PACK_SIZE)
            self.picks_per = int(msg.get("picks") or dc.PICKS)
            self.id = str(msg.get("id") or "online")
            self.created = msg.get("created") or time.time()
            self.flagged = frozenset(msg.get("flagged") or ())
            self.started = True
            # "start" comes first whenever the host sends everything again (I'm back after a drop): a pick I sent may have gone
            # down with the old connection - send it again (the same number: the host takes it once, whichever arrives)
            if self.pending is not None and self.pending_msg is not None:
                self.send(dict(self.pending_msg))
            return True
        if k == "view":
            self.view = msg
            # a view sent for someone else's pick can arrive before the host has my pick: the same round and pick as the one I
            # sent means the host hasn't taken it yet - keep it shown as taken (or the screen offers that pack again)
            if self.pending_at is None or [msg.get("round"), msg.get("turn")] != self.pending_at or msg.get("stage") != "pick":
                self.pending, self.pending_at = None, None
            return True
        if k == "review":
            self.review_picks = [GuestPick(self.local(p[0]), p[1], p[2], tuple(p[3]), tuple(p[4]))
                                 for p in msg.get("picks") or [] if isinstance(p, list) and len(p) == 5]
            return True
        if k == "refused":
            self.refused = msg.get("text") or "The host refused that."
            # only the refusal of the pick I'm waiting on frees it: one for an older pick (sent before the screen changed) may
            # arrive after the newer pick has gone - freeing it then offers the same pack twice
            if msg.get("n") is None or msg.get("n") == self.pick_n:
                self.pending, self.pending_at = None, None
            return True
        if k == "deck_ok":
            self.deck_state = "ok"
            return True
        if k == "deck_refused":
            self.deck_state = msg.get("text") or "The host refused that deck."
            return True
        return False

    @property
    def ready_to_show(self):
        return bool(self.started and self.cards and self.view)

    # ---- what the screens ask ---------------------------------------------------------------------------------------------
    def seat_name(self, seat):
        if seat == 0:
            return "You"
        return self.names[self.real(seat)] if self.names else f"Seat {seat + 1}"

    @property
    def stage_name(self):
        return (self.view or {}).get("stage") or "pick"

    @property
    def done(self):
        return bool(self.view and (self.view.get("done") or self.view.get("stage") != "pick"))

    def pack(self):
        if self.pending is not None or not self.view:
            return None
        return self.view.get("pack")

    def needed(self):
        return int((self.view or {}).get("need") or 0)

    def label(self):
        return (self.view or {}).get("label") or "Draft complete"

    def passing(self):
        word, to = ((self.view or {}).get("passing") or ["left", ""])[:2]
        return word, to

    def counts(self):
        real = list((self.view or {}).get("counts") or [])
        return [real[self.real(s)] for s in range(len(real))] if real and self.names else real

    def pool(self, seat=0):
        return list((self.view or {}).get("pool") or []) if seat == 0 else []

    def waiting(self):
        return list((self.view or {}).get("waiting") or [])

    def paused(self):
        return list((self.view or {}).get("paused") or [])

    def ai_for_me(self):
        return bool((self.view or {}).get("ai_for_you"))

    def pick(self, cards):
        """Send this pick to the host; the screen waits for the next view (or a refusal)."""
        v = self.view or {}
        cards = [int(c) for c in cards]
        self.pending, self.pending_at = cards, [v.get("round"), v.get("turn")]
        self.refused = None
        self.pick_n += 1
        self.pending_msg = {"k": "pick", "cards": cards, "at": self.pending_at, "n": self.pick_n, "sid": self.sid}
        self.send(dict(self.pending_msg))

    def can_pick(self):
        """There is a pack in front of me, the draft isn't paused, the AI isn't picking for me, and no pick is on its way."""
        return bool(self.pack()) and not self.paused() and not self.ai_for_me() and self.pending is None

    def run_ai(self, seats=None):
        return []

    def save(self):
        """Nothing to keep during the draft: the host's table holds it."""

    # ---- the decks --------------------------------------------------------------------------------------------------------
    def send_deck(self, deck, by_hand=False):
        self.deck_state = "sent"
        self.send(deck_message(deck, by_hand))

    def unready(self):
        self.deck_state = None
        self.send({"k": "unready"})

    def ready_list(self):
        """[(name, ready)] of every person, from the host's last view."""
        return list(((self.view or {}).get("ready") or {}).items())

    def save_my_deck(self, deck, library_dir=None, base_dir=None, by_hand=False):
        """My deck to My decks on this PC (the host's table writes its own copy for the game). Returns its DeckEntry."""
        import os
        import deck_library as lib
        library_dir = library_dir or lib.LIBRARY_DIR
        base_dir = base_dir or lib.BASE_DIR
        os.makedirs(library_dir, exist_ok=True)
        name = lib.free_name(self.deck_name(0, deck), library_dir)
        path = os.path.join(library_dir, name + ".txt")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.deck_text(0, deck, by_hand=by_hand))
        os.replace(tmp, path)
        return lib.DeckEntry(path, name, False, base_dir).load()


def pod_friends(pod):
    """The friends' names of an online draft's pod, in seat order."""
    return [pod.seat_name(d) for d in range(pod.draft.n) if pod.draft.seats[d].kind == "remote"]


def continue_seats(pod, locked):
    """Continue it (the host's program had closed): NetHost's draft_locked seats matched to the pod's friends by name. Returns
    (friends for HostDraft - [(net seat, name, connected)] in the pod's order -, [net seats of people who aren't in this draft])."""
    by_name = {str(o.get("name") or "").lower(): o for o in locked}
    friends = []
    for k, name in enumerate(pod_friends(pod)):
        o = by_name.pop(name.lower(), None)
        friends.append((int(o["seat"]), name, bool(o.get("connected"))) if o else (100 + k, name, False))
    return friends, [int(o["seat"]) for o in by_name.values()]


NOT_IN_DRAFT = ("This draft started without you: it is a saved draft, and you weren't in it. Ask the host to start another one.")


# ==================================================================================================================================
# Pumping the messages: the table (and the soak) call these every frame.
def pump_host(session, hd):
    """Hand the host's HostSession's draft messages and connection notes to the HostDraft, in the order they came. Returns how
    many were handled."""
    n = 0
    session.draft_events = []
    inbox, session.draft_inbox = session.draft_inbox, []
    for m in inbox:
        n += 1
        t, seat = m.get("t"), m.get("seat")
        if t == "draft_from":
            hd.on_message(seat, m.get("msg"))
        elif t == "peer_dropped":
            hd.on_drop(seat)
        elif t == "peer_back":
            hd.on_back(seat)
        elif t == "peer_left":
            hd.on_left(seat)
    return n


def pump_guest(session, gp):
    """Feed a friend's NetSession's draft messages to its GuestPod. Returns how many changed it."""
    inbox, session.draft_inbox = session.draft_inbox, []
    return sum(1 for m in inbox if gp.feed(m))


def guest_sender(session):
    """GuestPod's `send`: one draft message to the host."""
    return lambda msg: session.send(c="draft", msg=msg)


def write_game_decks(hd, folder, runtime=None):
    """The game's .dck files from the decks everyone made: (host path, [(net seat, path)], [AI paths], {draft seat: label}).
    The friends' files are written here, on the host, from the decks they sent (each was checked against their own picks)."""
    import os
    import forge_client as fc
    plan = hd.plan()
    pod = hd.pod
    os.makedirs(folder, exist_ok=True)
    labels = {}

    def write(seat, deck, file_name):
        commanders, main = dd.deck_names(pod.cards, deck)
        label = pod.deck_name(seat, deck)
        labels[seat] = label
        return fc.write_deck_file(os.path.join(folder, file_name), commanders, main, label, runtime, pod.format,
                                  rules=pod.rules or None)

    host_path = write(0, plan["host"], "draft_host.dck")
    friends = [(ns, write(d, deck, "draft_friend_%d.dck" % d)) for ns, d, deck in plan["friends"]]
    ai = [write(s, deck, "draft_ai_%d.dck" % s) for s, deck in plan["ai"]]
    return host_path, friends, ai, labels
