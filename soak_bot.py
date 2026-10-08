# SPDX-License-Identifier: GPL-3.0-or-later
"""
soak_bot.py - answers every question a Forge game can ask, validly but with variety, so
tools/soak.py can drive whole games overnight without a human at the table.

No pygame and no Forge in here: given a request dict (or the table state, for priority and
declaring attackers) this returns a move, the same shape card_check.next_move already uses:
("answer", request, value) | ("click", card_id) | ("player", player_id) | ("ok",) | ("cancel",) | None.

It does NOT try to play well - it exists to reach many different kinds of question (bridge_rules.py
and Checks.java are what notice when an answer was wrong), not to win games. Everything is driven by
one random.Random(seed), so a soak run's answers can be reproduced from its seed.

Answering rules (Round 28b spec):
  confirm          yes/no 50:50, except "keep your hand" (always yes) and the rest of the mulligan
                   flow (a stable answer, so it can't spiral into an endless string of mulligans)
  order            a random permutation of a random valid-size subset (all of them, when the
                   question asks for all)
  choose           a random valid size within [min, max], then random items
  choose_optional  as choose, but skipped outright (an empty answer) 25% of the time
  assign / divide  Allocation.auto()'s usual split
Any answer this produces that the bridge reports as client_answer_unusable is a bug in this module,
not in the bot's judgement - see tests/test_round28b.py's BotAnswersTests.
"""
import random
import re

import card_check
from allocation import Allocation

PRIORITY_CLICK_CAP = 2      # don't keep clicking a card that did nothing (card_check.next_move's "clicked" does the same)
PAY_REPEAT_CAP = 4          # round SB1: the same payment question back this often in one step -> decline it
MULLIGAN_WORDS = ("mulligan", "coin toss")


def _playable(card):
    """Round 28ba: at priority Forge marks the cards you could play with the actionable highlight ("weak" in the
    snapshot), NOT "selectable" - that flag is for "choose a card" questions. Checking only "selectable" meant the bot
    never played a land or a spell in its first night (27 Sept: 0 permanents all game, every game). card_check.next_move
    uses the same either-flag test for mana sources."""
    return bool(card.get("selectable") or card.get("weak"))


def _is_land(card):
    return bool(card.get("isLand")) or "Land" in (card.get("type") or "").split()


# Round 29a: Forge's refusal of a block with too few (menace) or too many blockers on one attacker (CombatUtil.validateBlocks)
_BLOCK_COUNT = re.compile(r"\((\d+)\) cannot be blocked with \d+ creatures? you've assigned")

class SoakBot:
    """One of these per game (or per soak run - it carries no game-specific state of its own; the
    caller's `mem` dict, passed to next_action the way card_check.next_move takes one, holds that)."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    # ---- 1. answering a request from the engine --------------------------------------------
    def answer_request(self, req):
        kind = req.get("kind")
        if kind == "confirm":
            return self._answer_confirm(req)
        if kind == "input":
            return self._answer_input(req)
        if kind == "assign":
            alloc = Allocation.from_request(req)
            alloc.auto()
            return alloc.answer()
        return self._answer_items(req, kind)

    def _answer_confirm(self, req):
        title = (req.get("title") or "").lower()
        if "keep" in title and "hand" in title:
            return True
        if any(w in title for w in MULLIGAN_WORDS):
            return bool(req.get("default", True))          # stable, not randomised: the mulligan flow depends on it
        return self.rng.random() < 0.5

    def _answer_input(self, req):
        options = req.get("options") or []
        if options:
            return self.rng.choice(options)
        if req.get("numeric"):
            return str(self.rng.randint(0, 3))
        return req.get("initial") or ""

    def _answer_items(self, req, kind):
        """choose / choose_optional / order: an array of indexes into req["items"], the way the
        bridge (BridgeGui.ask) reads every one of these back."""
        items = req.get("items") or []
        real = [i for i, it in enumerate(items) if not card_check.is_heading(it) and not card_check.is_finish(it)]
        lo, hi = req.get("min", 0) or 0, req.get("max")
        if hi is None or hi < 0:
            hi = len(real)
        hi = min(hi, len(real))
        lo = min(lo, hi)
        if kind == "choose_optional" and lo == 0 and self.rng.random() < 0.25:
            return []
        n = self.rng.randint(lo, hi) if hi >= lo else lo
        n = max(0, min(n, len(real)))
        return list(self.rng.sample(real, n)) if n else []

    # ---- 2, 3, 4. what to do with no request pending --------------------------------------
    def next_action(self, state, requests, mem):
        """One move for tools/soak.py to send, exactly like card_check.next_move: it answers a
        pending request first, then decides my priority and declaring attackers itself, and
        leaves everything else (paying costs, targeting, mulligans as a prompt, ...) to
        card_check.next_move, which already knows how to reach "nothing more to do here".

        card_check.next_move deliberately returns None for a "Priority: ..." message with an empty
        stack (it's a scripted-scenario driver: once there's nothing on the stack it's supposed to
        stop, and the test framework decides what happens next). A soak bot has no such framework -
        it has to actually get through the whole game - so ANY empty-stack priority window (mine or
        not, whatever the phase: my own upkeep, an opponent's turn, an end step, ...) is answered
        here, not left to fall through to that None. Found live: every one of a first soak run's
        games stalled the moment it hit a priority window outside its own main phase (an opponent's
        upkeep was the first one), because nothing was clicking OK to pass it."""
        if requests:
            req = requests[0]
            return ("answer", req, self.answer_request(req))
        if not state:
            return None
        me = next((p for p in state.get("players", []) if p.get("id") == state.get("me")), None)
        if not me:
            return None
        prompt = state.get("prompt") or {}
        msg = prompt.get("message") or ""
        self._new_question(state, mem)
        if "keep your hand" in msg.lower():
            return self._mulligan_move(me, prompt, mem)
        if state.get("input") == "InputBlock":
            move = self._required_block_move(state, me, prompt, mem)
            if move is not None:
                return move
        if state.get("input") == "InputSelectTargets":
            move = self._target_player_move(state, me, prompt, mem)
            if move is not None:
                return move
        if (state.get("input") == "InputAttack"
                or (state.get("phase") == "COMBAT_DECLARE_ATTACKERS" and prompt.get("selecting"))) \
                and state.get("activePlayer") == me.get("id"):
            move = self._attackers_move(state, me, prompt, mem)
            if move is not None:
                return move
        if "would you like to start this game" in msg.lower():
            move = self._choose_first_player_move(state, prompt, mem)
            if move is not None:
                return move
        if any(p.get("selectable") for p in state.get("players") or []):
            move = self._pick_players_move(state, prompt, mem)
            if move is not None:
                return move
        if prompt.get("selecting") and "Pay Mana" not in msg:
            move = self._pick_cards_move(state, prompt, mem)
            if move is not None:
                return move
        if msg.startswith("Priority:") and not state.get("stack"):
            move = self._priority_move(state, me, prompt, mem)
            if move is not None:
                return move
        if "Pay Mana Cost" in msg:
            move = self._payment_loop_move(state, prompt, msg, mem)
            if move is not None:
                return move
        return card_check.next_move(state, requests, mem)

    @staticmethod
    def _payment_loop_move(state, prompt, msg, mem):
        """Round SB1 (the alpha chain's sandbox soak, 2 Oct, game 23): an AI's Rhystic Study asked the bot to pay {1}. Its
        only mana source was Transmogrant Altar ({B}, tap, sacrifice a creature). Clicking the Altar opened a second
        question, the Altar's own {B}, which the bot could not pay and cancelled - and that brought back Rhystic Study's
        question under a new question number. card_check.next_move counts a payment question as new whenever the
        previous prompt read differently, so the two questions took turns for 30 minutes (about 14,000 times, an 898 MB
        record) until the time cap. A person declines to pay. So: the same payment question (its text without ids)
        asked under more than PAY_REPEAT_CAP question numbers in one step of one turn is cancelled. Cancelling a payment
        is always a legal answer: Forge undoes a spell being cast, or treats an "unless you pay" as not paid."""
        where = (state.get("turn"), state.get("phase"))
        if mem.get("soak_pay_where") != where:
            mem["soak_pay_where"] = where
            mem["soak_pay_asked"] = {}
        seqs = mem["soak_pay_asked"].setdefault(card_check.prompt_key(msg), set())
        seqs.add(state.get("inputSeq"))
        if len(seqs) > PAY_REPEAT_CAP and (prompt.get("cancel") or {}).get("enabled"):
            return ("cancel",)
        return None

    # ---- round 28bb: what soak night 1 (27 Sept, 174 games, 16 stalls) showed the bot couldn't do -------
    @staticmethod
    def _new_question(state, mem):
        """card_check.next_move remembers which cards it clicked, and how often it has seen a prompt, for the whole
        game - fine for the card check's short scripted scenes, not for a 60-turn game. When the same question came back
        (Sylvan Library every turn, a second Butcher of Malakir sacrifice, the cleanup discard) it had already "used"
        those cards and had nothing left to click: 10 of night 1's 16 stalls. So that memory now lasts one question:
        Forge's question number (inputSeq) changes when a new question starts, not when a card is clicked in this one."""
        seq = state.get("inputSeq")
        if seq is None or mem.get("soak_question") == seq:
            return
        mem["soak_question"] = seq
        mem["clicked"] = {}
        mem["seen"] = {}
        mem["soak_players_tried"] = set()
        mem["soak_blocks_done"] = set()
        mem["soak_picked"] = set()
        mem["soak_pick_want"] = None
        mem["soak_players_picked"] = set()
        mem["soak_player_want"] = None

    def _pick_cards_move(self, state, prompt, mem):
        """Round 28bc: "choose N cards" (discard, sacrifice, Sylvan Library...). Click a selectable card that isn't picked
        yet (Forge highlights picked ones) and hasn't been clicked in this question; OK once Forge enables it.
        card_check.next_move remembers clicks by the prompt's TEXT, and the text changes as you pick ("Discard 2 card(s)"
        -> "Discard 1 card(s)"), so it clicked the picked card again - which un-picks it - went round every card that way
        and ended with nothing picked: soak night 2, Frantic Search, 4 of the 5 stalls."""
        clicked = mem.setdefault("soak_picked", set())
        selectable = [c for c, _z, _p in card_check.all_cards(state) if c.get("selectable")]
        highlighted = sum(1 for c in selectable if c.get("highlight"))
        picked = highlighted if highlighted else len(clicked)      # not every question highlights picked cards
        ok = prompt.get("ok") or {}
        want = mem.get("soak_pick_want")
        if want is None:
            # How many to pick: a random count Forge allows ("any number" is not "all": sacrificing every land isn't
            # what a player does, and it would end games oddly early).
            lo = prompt.get("selMin") or 0
            hi = prompt.get("selMax")
            hi = len(selectable) if hi is None or hi < 0 else min(hi, len(selectable))
            want = self.rng.randint(lo, hi) if hi >= lo else lo
            mem["soak_pick_want"] = want
        if picked >= want and ok.get("enabled"):
            return ("ok",)
        options = [c for c in selectable if not c.get("highlight") and c["id"] not in clicked]
        options.sort(key=lambda c: (c.get("controller") == state.get("me"), c["id"]))    # opponents' first, as card_check
        if options:
            clicked.add(options[0]["id"])
            return ("click", options[0]["id"])
        return ("ok",) if ok.get("enabled") else None

    def _pick_players_move(self, state, prompt, mem):
        """Round 28d: a "choose a player" list question - a Siege's "Choose an opponent to protect this battle" (Invasion of
        Ikoria, soak nights 5 and 6: three stalls). The bridge now marks the players Forge will accept ("selectable") and
        those already picked ("highlight"). Pick a random allowed number of them, each once, then OK."""
        clicked = mem.setdefault("soak_players_picked", set())
        selectable = [p for p in state.get("players") or [] if p.get("selectable")]
        picked = sum(1 for p in selectable if p.get("highlight"))
        ok = prompt.get("ok") or {}
        want = mem.get("soak_player_want")
        if want is None:
            lo = prompt.get("selMin") or 0
            hi = prompt.get("selMax")
            hi = len(selectable) if hi is None or hi < 0 else min(hi, len(selectable))
            want = self.rng.randint(lo, hi) if hi >= lo else lo
            mem["soak_player_want"] = want
        if picked >= want and ok.get("enabled"):
            return ("ok",)
        options = [p for p in selectable if not p.get("highlight") and p["id"] not in clicked]
        if options:
            choice = self.rng.choice(options)
            clicked.add(choice["id"])
            return ("player", choice["id"])
        return ("ok",) if ok.get("enabled") else None

    def _mulligan_move(self, me, prompt, mem):
        """Keep a hand with 2-5 lands; otherwise mulligan, at most twice a game (then keep anything). Before round 28bb
        the bot always kept, and one run's only game was a 7-card hand with no land (0 lands played, INVALID).
        Forge's buttons here: OK = keep, Cancel = mulligan (their labels say so)."""
        ok, cancel = prompt.get("ok") or {}, prompt.get("cancel") or {}
        hand = (me.get("zones") or {}).get("hand") or []
        lands = sum(1 for c in hand if _is_land(c))
        taken = mem.get("soak_mulligans", 0)
        if hand and not 2 <= lands <= 5 and taken < 2 and cancel.get("enabled") \
                and "mulligan" in (cancel.get("label") or "").lower():
            mem["soak_mulligans"] = taken + 1
            return ("cancel",)
        return ("ok",) if ok.get("enabled") else None

    def _required_block_move(self, state, me, prompt, mem):
        """Forge refuses OK on the block question while a blocking requirement isn't met, and says why in a message:
        "Kor Spiritdancer (62) must block an attacker, but has not been assigned to block any." (The Masamune: "must be
        blocked if able"). A person sees that message in a dialog; the bot never read it and pressed OK until the stall
        timer ran out (night 1, games 69 and 131). tools/soak.py now copies those messages into mem["infos"], each tagged
        with the question number it was said during ("_seq"); only one from the current question counts. A creature
        of mine named in one blocks the current attacker (one click on it); an attacker named in one gets selected first,
        then one of my untapped creatures."""
        infos = mem.get("infos") or []
        if not infos or infos[-1].get("_seq") != state.get("inputSeq"):
            return None                            # only a message said during THIS block question, not an old one
        text = infos[-1].get("text") or ""
        import re
        count = _BLOCK_COUNT.search(text)
        if count:
            return self._block_count_move(state, me, mem, int(count.group(1)), len(infos))
        if "must block" not in text and "must be blocked" not in text:
            return None
        ids = [int(x) for x in re.findall(r"\((\d+)\)", text)]
        done = mem.setdefault("soak_blocks_done", set())
        mine = {c["id"]: c for c in (me.get("zones") or {}).get("battlefield") or []}
        for cid in ids:
            if cid in done:
                continue
            done.add(cid)
            if cid in mine:
                return ("click", cid)
            blockers = [c for c in mine.values() if c.get("isCreature") and not c.get("tapped") and not c.get("blocking")]
            if blockers:
                mem.setdefault("soak_block_queue", []).append(self.rng.choice(blockers)["id"])
            return ("click", cid)                      # the attacker: makes it the one being blocked
        queue = mem.get("soak_block_queue") or []
        if queue:
            return ("click", queue.pop(0))
        return None

    def _block_count_move(self, state, me, mem, attacker_id, said):
        """Round 29a (soak night 10, game 83): "Agent Venom (401) cannot be blocked with 1 creatures you've assigned". The bot
        had put one creature on an attacker with menace (Esper Sentinel had to block something, and the attacker being asked
        about was Agent Venom), then pressed OK for 2 minutes. Forge says how many blockers it has, not how many it wants, so:
        first add one more of my free creatures to that attacker; if Forge still refuses (or nothing is free), take the
        blockers off it and make another attacker the current one. Each step is a queue of clicks, the attacker first (it
        becomes the one being blocked). Forge's InputBlock: clicking a creature that blocks the current attacker takes it
        off; clicking one that blocks another attacker does nothing. Each refusal is acted on once (said = how many messages
        so far), then OK is pressed again."""
        queue = mem.setdefault("soak_block_queue", [])
        if queue:
            return ("click", queue.pop(0))
        if mem.get("soak_block_count_said") == said:
            return None                                         # this refusal is handled: let OK be pressed again
        mem["soak_block_count_said"] = said
        tries = mem.setdefault("soak_block_count_tries", {})
        step = tries.get(attacker_id, 0)
        combat = state.get("combat") or []
        creatures = [c for c in (me.get("zones") or {}).get("battlefield") or [] if c.get("isCreature") and not c.get("tapped")]
        # The snapshot marks a creature "blocking" but (before blocks are confirmed) not which attacker it blocks.
        free = [c for c in creatures if not c.get("blocking")]
        blocking = [c["id"] for c in creatures if c.get("blocking")]
        if step == 0 and free:
            tries[attacker_id] = 1                              # too few (menace): one more blocker on that attacker
            queue.append(self.rng.choice(free)["id"])
            return ("click", attacker_id)
        if step <= 1 and blocking:
            tries[attacker_id] = 2                              # still refused, or nothing free: take them off that attacker.
            mem.pop("soak_blocks_done", None)                   # a "must block" creature may be placed again, elsewhere
            queue.extend(blocking)                              # Clicking one that blocks a DIFFERENT attacker does nothing
            others = [e.get("card") for e in combat if e.get("card") != attacker_id]   # (Forge: canBlock is false),
            if others:                                          # then make another attacker the current one, so a
                queue.append(self.rng.choice(others))           # "must block" creature goes there next, not back here
            return ("click", attacker_id)
        return None

    def _target_player_move(self, state, me, prompt, mem):
        """"target player" with no card to pick (Blood Artist): card_check.next_move only tries the first opponent, and
        only when the message has the word "player" - night 1 game 132 stalled here. Try each player still in the game
        once per question (opponents first), then OK if Forge has enabled it."""
        if any(c.get("selectable") for c, _z, _p in card_check.all_cards(state)):
            return None
        tried = mem.setdefault("soak_players_tried", set())
        players = [p for p in state.get("players") or [] if not p.get("lost")]
        players.sort(key=lambda p: p.get("id") == me.get("id"))
        for p in players:
            if p["id"] not in tried:
                tried.add(p["id"])
                return ("player", p["id"])
        ok = prompt.get("ok") or {}
        return ("ok",) if ok.get("enabled") else None

    def _priority_move(self, state, me, prompt, mem):
        ok = prompt.get("ok") or {}
        if state.get("activePlayer") == me.get("id") and state.get("phase") in ("MAIN1", "MAIN2"):
            counts = mem.setdefault("soak_priority_clicks", {})
            hand = (me.get("zones") or {}).get("hand") or []
            lands = [c for c in hand if _playable(c) and _is_land(c)]
            others = [c for c in hand if _playable(c) and not _is_land(c)]
            self.rng.shuffle(others)
            for c in lands + others:
                if counts.get(c["id"], 0) < PRIORITY_CLICK_CAP:
                    counts[c["id"]] = counts.get(c["id"], 0) + 1
                    return ("click", c["id"])
        # not my main phase (my own upkeep/combat/end step, or anyone else's turn at all): nothing to
        # play here, just pass - this is the case that used to fall through to card_check.next_move
        # and stall forever.
        return ("ok",) if ok.get("enabled") else None

    def _choose_first_player_move(self, state, prompt, mem):
        """3+ player games (only those - 2-player games use a plain yes/no play-or-draw prompt
        instead) ask 'who would you like to start this game?' by having you click a player's
        portrait - Forge's InputSelectEntitiesFromList<Player>. Unlike every other kind of
        selection this bridge answers, that class's constructor only ever calls setSelectables
        with the CARD subset of its choices (built via `if (v instanceof Card)`), and a Player is
        never a Card - so that list is always empty, and prompt.selecting/selMin/selMax report
        this exactly like a plain "click OK, nothing to select" prompt even though a real choice
        is required. card_check.next_move's own player-click heuristic keys off the word "player"
        appearing in the message, which Forge's actual wording here never uses, so it doesn't fire
        either. Left to itself the bot just kept sending "ok" with nothing selected - the engine
        accepted that "ok" (selMin/selMax both dropped to 0 as if satisfied, but nothing was ever
        actually chosen), so Forge's determineFirstTurnPlayer() returned null and crashed a few
        lines later trying to deal an opening hand to nobody. Found live: every 3+ player soak game
        hit this, every time.

        The fix: click a player's portrait ourselves with the wire protocol's own "player" command
        (Main.java's "player" case, forge_client.ForgeSession.click_player - fully wired already,
        just never reachable through prompt.selecting for this one prompt). Any player is a valid
        choice; one click is enough - if Forge still wants an explicit OK afterward, the enabled
        flag on a later poll covers that the same way every other click-then-OK flow here does."""
        if mem.get("soak_first_player_chosen"):
            ok = prompt.get("ok") or {}
            return ("ok",) if ok.get("enabled") else None
        players = state.get("players") or []
        if not players:
            return None
        mem["soak_first_player_chosen"] = True
        return ("player", self.rng.choice(players)["id"])

    @staticmethod
    def _attack_refused(mem):
        """True once for each new "Attack declaration invalid" message from Forge (the bot marks the ones it has read)."""
        for info in reversed(mem.get("infos") or []):
            if "Attack declaration invalid" in (info.get("text") or ""):
                if info.get("_soak_read"):
                    return False
                info["_soak_read"] = True
                return True
        return False

    def _attackers_move(self, state, me, prompt, mem):
        """Declare a random subset of my creatures that can attack, then OK.
        Round MP2e (found by round FB1's soaks, 3 Oct): this never ran. It waited for the prompt's "selecting" flag and
        picked "selectable" creatures, but Forge's attack question has neither: the creatures that can attack are marked
        "weak" (Forge's actionable highlight), as at priority (28ba's _playable). So the soak seat never attacked in any soak
        until now; the attacks in the soaks were all the AIs'. And when a creature of mine had to attack (a Howlsquad Heavy
        goblin, "attacks this combat if able") Forge refused the empty declaration ("Attack declaration invalid") and asked
        again, for 30 minutes, until the time cap (FB1's Brawl soak, game 3). After a refusal: Alpha Strike (Forge declares
        every creature that can attack), then OK."""
        turn = state.get("turn")
        refusals = mem.get("soak_attack_refusals")
        if not refusals or refusals[0] != turn:
            refusals = mem["soak_attack_refusals"] = [turn, 0]
        if self._attack_refused(mem):
            refusals[1] += 1
            mem["soak_attackers_chosen"] = None
        ok = prompt.get("ok") or {}
        if refusals[1]:
            k = (turn, refusals[1])
            cancel = prompt.get("cancel") or {}
            label = (cancel.get("label") or "").lower()
            if cancel.get("enabled") and mem.get("soak_alpha") != k:
                if "alpha" in label:
                    mem["soak_alpha"] = k
                    return ("cancel",)
                if "call back" in label and mem.get("soak_callback") != k:
                    # Patch 44 (the Brawl soak of 6 Oct, Goblin Rabblemaster): the refusal came after some attackers were
                    # declared, so Cancel reads "Call Back", not "Alpha Strike", and the bot pressed OK 28,000 times. Take
                    # them back first; Cancel then reads "Alpha Strike".
                    mem["soak_callback"] = k
                    return ("cancel",)
            return ("ok",) if ok.get("enabled") else None
        battlefield = (me.get("zones") or {}).get("battlefield") or []
        creatures = [c for c in battlefield if _playable(c) and c.get("isCreature") and not c.get("tapped")]
        chosen = mem.get("soak_attackers_chosen")
        if chosen is None:
            n = self.rng.randint(0, len(creatures))
            chosen = set(self.rng.sample([c["id"] for c in creatures], n)) if n else set()
            mem["soak_attackers_chosen"] = chosen
            mem["soak_attackers_clicked"] = set()
        clicked = mem.setdefault("soak_attackers_clicked", set())
        remaining = [cid for cid in chosen if cid not in clicked]
        if remaining:
            clicked.add(remaining[0])
            return ("click", remaining[0])
        mem["soak_attackers_chosen"] = None
        ok = prompt.get("ok") or {}
        return ("ok",) if ok.get("enabled") else None
