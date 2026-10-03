# SPDX-License-Identifier: GPL-3.0-or-later
"""Round FB1b: two things round FB1's checks found (3 Oct 2026), neither caused by the Forge update.

1. The host never says a friend dropped after saying they were back (MP2a's reconnect).
   FB1's full suite failed test_mp2's live drop test once, under load: the host's table still said "Sam's connection
   dropped - waiting for them to come back (60 s left)" after Sam had reconnected and was playing. NetHost announced a
   connection's end (detach, then "peer_dropped") and a reconnect (attach, then "peer_back") on two threads with nothing
   between them, so when the old connection's end was noticed just before the new one arrived, "dropped" could be sent after
   "back". Both now hold the seat's own lock (SeatWire.announce). The live test widens the gap on purpose (NetHost
   --drop-notice-delay-ms, tests only): the old code fails it every time; with the lock the reconnect waits for "dropped"
   and then says "back".
2. The soak seat attacks (soak_bot). Its attack code never ran: it waited for the prompt's "selecting" flag and picked
   "selectable" creatures, and Forge's attack question has neither (the creatures that can attack are "weak", Forge's
   actionable highlight) - on the old Forge too (round 28bb's recorded games). Every attack in every soak so far was an AI's.
   And when a creature of mine had to attack (a Howlsquad Heavy goblin), Forge refused the empty declaration and the bot
   asked again for 30 minutes (FB1's Brawl soak, game 3; tests/fixtures/soak/fb1_brawl_howlsquad_attack.json).
"""
import json
import os
import re
import socket
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import soak_bot
from tests import test_mp2 as mp2
from tests.test_mp1 import wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_HOST = os.path.join(ROOT, "java_bridge", "src", "forge", "bridge", "NetHost.java")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "soak", "fb1_brawl_howlsquad_attack.json")
DELAY_MS = 3000


def block_end(text, start=0):
    """The index of the "}" that closes the first "{" at or after start."""
    depth, i = 0, text.index("{", start)
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1


def method_body(src, name):
    """The text of one Java method, from its signature to its closing brace."""
    start = re.search(r"\n    static [^\n]*\b" + name + r"\(", src).start()
    return src[start:block_end(src, start) + 1]


# ---------------------------------------------------------------------------------------------------------------------
# 1. "dropped" never after "back"
# ---------------------------------------------------------------------------------------------------------------------
class SourceTests(unittest.TestCase):
    def setUp(self):
        with open(NET_HOST, encoding="utf-8") as f:
            self.src = f.read()

    def test_the_drop_and_the_reconnect_announce_under_the_seats_lock(self):
        ended = method_body(self.src, "guestConnectionEnded")
        rejoin = method_body(self.src, "rejoin")
        for body, first, then in ((ended, "seat.detach(c)", '"peer_dropped"'), (rejoin, "seat.attach(", '"peer_back"')):
            self.assertIn("synchronized (seat.announce)", body)
            at = body.index("synchronized (seat.announce)")
            locked = body[at:block_end(body, at) + 1]
            self.assertIn(first, locked)
            self.assertIn(then, locked)

    def test_the_seat_leaves_outside_the_lock(self):
        """guestLeft and lobbyLeft can take a while (a concede waits for a safe moment): they never run under the lock. (The
        grace timer's guestLeft is scheduled from inside it, but runs later on the timer's thread.)"""
        ended = method_body(self.src, "guestConnectionEnded")
        at = ended.index("synchronized (seat.announce)")
        locked = ended[at:block_end(ended, at) + 1]
        timer = locked.index("timers.schedule(")
        locked = locked[:timer] + locked[block_end(locked, timer) + 1:]
        self.assertNotIn("guestLeft(", locked)
        self.assertNotIn("lobbyLeft(", locked)
        after = ended[block_end(ended, at) + 1:]
        self.assertIn("lobbyLeft(seat)", after)
        self.assertIn("guestLeft(seat)", after)

    def test_the_test_pause_is_off_unless_asked_for(self):
        self.assertIn("static int dropNoticeDelayMs = 0;", self.src)
        cert = types.SimpleNamespace(path="host.p12", password="x", fingerprint="ab")
        h = fc.HostSession("d.dck", "Karl", 1, "pw", cert, "g.dck", upnp=False)
        self.assertNotIn("--drop-notice-delay-ms", h._bridge_args())
        h = fc.HostSession("d.dck", "Karl", 1, "pw", cert, "g.dck", upnp=False, drop_notice_delay_ms=500)
        self.assertIn("--drop-notice-delay-ms", h._bridge_args())


@unittest.skipUnless(mp2.LIVE, "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    setUpClass = classmethod(mp2.LiveTests.setUpClass.__func__)
    tearDownClass = classmethod(mp2.LiveTests.tearDownClass.__func__)
    start_host = mp2.LiveTests.start_host
    guest = mp2.LiveTests.guest
    msg = staticmethod(mp2.LiveTests.msg)
    play_pregame = mp2.LiveTests.play_pregame
    started = mp2.LiveTests.started
    fact_or_fiction = mp2.LiveTests.fact_or_fiction

    def test_a_slow_drop_notice_still_comes_before_back(self):
        h, port = self.start_host(dev=True, drop_notice_delay_ms=DELAY_MS)
        g = self.guest(port)
        self.started(h, g)
        req = self.fact_or_fiction(h, g)
        g.proc.sock.shutdown(socket.SHUT_RDWR)                        # the guest's Wi-Fi goes for a moment
        self.assertTrue(wait_for(lambda: g.reconnects == 1 and h.peer_backs == 1, 30, pump=[h, g]), "no reconnect")
        wait_for(lambda: False, DELAY_MS / 1000 + 2, pump=[h, g])     # past the pause: a late "dropped" would be here by now
        self.assertIsNone(h.peer_dropped, "the host's table still shows the guest as dropped")
        self.assertEqual(h.dropped_seats, {})
        self.assertEqual(h.peer_backs, 1)
        # and the game goes on: the question asked again is answered once
        self.assertTrue(wait_for(lambda: bool(g.requests), 10, pump=[h, g]))
        self.assertEqual([r["id"] for r in g.requests], [req["id"]])
        r = g.requests[0]
        g.answer(r, list(range(max(r.get("min", 1), 1))))
        self.assertTrue(wait_for(lambda: not g.requests, 30, pump=[h, g]), "the answer after the reconnect was not taken")
        self.assertIsNone(h.peer_dropped)


# ---------------------------------------------------------------------------------------------------------------------
# 2. the soak seat attacks
# ---------------------------------------------------------------------------------------------------------------------
class SoakBotAttackTests(unittest.TestCase):
    """The soak seat declares attackers on Forge's real attack question (recorded in FB1's Brawl soak)."""

    def setUp(self):
        with open(FIXTURE, encoding="utf-8") as f:
            self.fx = json.load(f)
        self.q = self.fx["attack_question"]
        me = next(p for p in self.q["players"] if p["id"] == self.q["me"])
        self.can_attack = {c["id"] for c in me["zones"]["battlefield"]
                           if c.get("isCreature") and not c.get("tapped") and (c.get("weak") or c.get("selectable"))}

    def test_the_recorded_question_is_the_one_the_old_bot_skipped(self):
        self.assertEqual(self.q["input"], "InputAttack")
        self.assertFalse(self.q["prompt"].get("selecting"))
        self.assertTrue(self.can_attack, "the board has creatures that can attack")
        me = next(p for p in self.q["players"] if p["id"] == self.q["me"])
        self.assertFalse(any(c.get("selectable") for c in me["zones"]["battlefield"]))

    def play_question(self, seed, state=None, mem=None, limit=20):
        """The bot's moves on one attack question, until it presses OK or Alpha Strike (Cancel)."""
        bot = soak_bot.SoakBot(seed=seed)
        mem = {} if mem is None else mem
        moves = []
        for _ in range(limit):
            move = bot.next_action(state or self.q, [], mem)
            moves.append(move)
            if move is None or move[0] in ("ok", "cancel"):
                break
        return moves, mem

    def test_it_declares_attackers_and_only_creatures_that_can_attack(self):
        clicked_any = 0
        for seed in range(40):
            moves, _ = self.play_question(seed)
            self.assertEqual(moves[-1], ("ok",), moves)
            self.assertTrue(all(m[0] == "click" for m in moves[:-1]), moves)
            clicks = [m[1] for m in moves[:-1]]
            self.assertLessEqual(set(clicks), self.can_attack)
            self.assertEqual(len(clicks), len(set(clicks)), "a creature clicked twice would be taken back out")
            clicked_any += bool(clicks)
        self.assertGreater(clicked_any, 20, "over 40 seeds the bot should usually attack with something")

    def test_a_refused_declaration_gets_alpha_strike_then_ok(self):
        mem = {"infos": [dict(self.fx["message"], _seq=self.q["inputSeq"])]}
        again = self.fx["asked_again"]
        self.assertEqual(again["prompt"]["cancel"]["label"], "Alpha Strike")
        moves, mem = self.play_question(3, state=again, mem=mem)
        self.assertEqual(moves, [("cancel",)], "Alpha Strike first")
        after = json.loads(json.dumps(again))
        after["prompt"]["cancel"]["label"] = "Call Back"           # Forge's button once everything is declared
        moves, mem = self.play_question(3, state=after, mem=mem)
        self.assertEqual(moves, [("ok",)])

    def test_each_refusal_is_read_once(self):
        mem = {"infos": [dict(self.fx["message"])]}
        self.assertTrue(soak_bot.SoakBot._attack_refused(mem))
        self.assertFalse(soak_bot.SoakBot._attack_refused(mem))
        mem["infos"].append(dict(self.fx["message"]))
        self.assertTrue(soak_bot.SoakBot._attack_refused(mem))

    def test_not_my_turn_is_left_alone(self):
        theirs = json.loads(json.dumps(self.q))
        theirs["activePlayer"] = next(p["id"] for p in theirs["players"] if p["id"] != theirs["me"])
        move = soak_bot.SoakBot(seed=1).next_action(theirs, [], {})
        self.assertFalse(move and move[0] == "click" and move[1] in self.can_attack)


if __name__ == "__main__":
    unittest.main()
