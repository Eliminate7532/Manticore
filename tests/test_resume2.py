# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RESUME2 (Karl, 8 Nov 2026, claude/DECISIONS_2026-11-08.md): three follow-ups to RESUME1.

1. "Skip guests who are out": when a saved online game is continued, the host no longer waits for a saved guest whose player
   had lost (left a pod and was conceded for, or lost the ordinary way). NetHost names them in replay_done's "out";
   HostSession.everyone_back / waited_for leave them out. (The live case is tests/test_resume1.py.)
2. RESUME1's "seqs" field and the play-back's wait on it are gone (tests/test_resume1.py SourceTests).
3. A guest whose connection was cut just as the game ended gets game_over again when it reconnects (RESUME1's 1 h soak, game
   23: online_mismatch "game over on host only").
"""
import os
import sys
import tempfile
import shutil
import socket
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_net as fn
import tests.live as live
from tests.test_mp1 import deck_file, free_port, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "java_bridge", "src", "forge", "bridge")


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


def body_of(src, head):
    start = src.index(head)
    return src[start:src.index("\n    }\n", start)]


def host_session(seat_list=(), resume_guests=(), replay_result=None):
    """A HostSession with just the fields everyone_back reads (no engine)."""
    h = object.__new__(fc.HostSession)
    h.seat_list = list(seat_list)
    h.resume = {"guests": list(resume_guests)} if resume_guests else {"guests": []}
    h.replay_result = replay_result
    h.guests_joined = []
    return h


class OutGuestTests(unittest.TestCase):
    SEATS = [{"seat": 2, "name": "Sam", "deck": "a"}, {"seat": 3, "name": "Ann", "deck": "b"}]

    def test_an_out_guest_is_not_waited_for(self):
        h = host_session(self.SEATS, replay_result={"applied": 9, "out": [{"seat": 3, "name": "Ann"}]})
        self.assertEqual(h.out_guests(), {"Ann"})
        self.assertEqual(h.waited_for(), ["Sam"])
        self.assertFalse(h.everyone_back())
        h.guests_joined = ["Sam"]
        self.assertTrue(h.everyone_back())

    def test_a_guest_still_in_the_game_is_waited_for(self):
        h = host_session(self.SEATS, replay_result={"applied": 9, "out": []})
        h.guests_joined = ["Sam"]
        self.assertFalse(h.everyone_back(), "Ann hasn't lost: the host waits for her")
        h.guests_joined = ["Sam", "Ann"]
        self.assertTrue(h.everyone_back())

    def test_a_bridge_before_resume2_says_nothing_and_everyone_is_waited_for(self):
        h = host_session(self.SEATS, replay_result={"applied": 9})
        h.guests_joined = ["Sam"]
        self.assertEqual(h.out_guests(), set())
        self.assertFalse(h.everyone_back())

    def test_from_the_resume_list_before_the_seats_message(self):
        h = host_session(resume_guests=[(2, "Sam", "a"), (3, "Ann", "b")], replay_result={"out": [{"seat": 2, "name": "Sam"}]})
        self.assertEqual(h.waited_for(), ["Ann"])

    def test_everyone_out_means_nobody_to_wait_for(self):
        h = host_session(self.SEATS, replay_result={"out": [{"seat": 2, "name": "Sam"}, {"seat": 3, "name": "Ann"}]})
        self.assertTrue(h.everyone_back())


class SourceTests(unittest.TestCase):
    def test_the_play_back_end_names_the_out_seats(self):
        src = read("NetHost.java")
        body = body_of(src, "static void replayFinished(ResumeFeeder feeder)")
        self.assertIn('m.add("out", markOutSeats(', body)
        self.assertLess(body.index("markOutSeats("), body.index("hostWire.send(m)"))

    def test_out_means_a_concede_in_the_journal_or_a_lost_player(self):
        body = body_of(read("NetHost.java"), "static com.google.gson.JsonArray markOutSeats(")
        self.assertIn('"concede".equals(', body)
        self.assertIn("conceded.contains(g.seat) || playerLost(g.gui)", body)
        self.assertIn("g.out = true", body)

    def test_cancel_does_not_wait_for_an_out_guest_and_a_returning_guest_takes_a_live_seat_first(self):
        src = read("NetHost.java")
        self.assertIn("everyoneBack &= g.attached() || g.out;", src)
        self.assertIn("(pass == 1 || !g.out)", body_of(src, "static void resumeJoin("))

    def test_game_over_is_kept_and_sent_under_one_lock(self):
        body = body_of(read("BridgeGui.java"), "public void finishGame()")
        self.assertRegex(body, r"synchronized \(gameOverLock\) \{[^\n]*\s*gameOverMsg = m;\s*wire\.send\(m\);")

    def test_rejoin_sends_a_game_over_it_missed(self):
        body = body_of(read("NetHost.java"), "static void rejoin(")
        self.assertIn("missedOver = sgui != null ? sgui.gameOverMsg : null;", body)
        self.assertIn("seat.send(missedOver);", body)
        # read under the same lock finishGame sends under, right after the new connection is attached
        lock = body.index("synchronized (sgui != null ? sgui.gameOverLock")
        self.assertLess(lock, body.index("seat.attach("))
        self.assertLess(body.index("seat.attach("), body.index("missedOver = "))
        # a game Forge has finished for that seat is no longer refused
        self.assertIn("(gameOver && !overForSeat)", body)


class ScreenTests(unittest.TestCase):
    def test_the_wait_screen_waits_only_for_guests_still_in(self):
        with open(os.path.join(ROOT, "online_screens.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("s.waited_for()", src)
        self.assertIn("out of the game - not waiting for", src)


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are switched off")
class LiveGameOverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="resume2_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        cls.decks = {name: deck_file(cls.tmp, sample, name) for name, sample in (
            ("Lathril", "typal_lathril.txt"), ("Veyran", "spellslinger_veyran.txt"))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_guest_cut_off_as_the_game_ends_gets_game_over_when_it_comes_back(self):
        port = free_port()
        h = fc.HostSession(self.decks["Lathril"], "Karl", port, "pw-live-r2", self.cert,
                           os.path.join(self.tmp, "guest_%d.dck" % port), upnp=False, bind="127.0.0.1", code="hostcode",
                           seed=62)
        h.stderr_path = os.path.join(self.tmp, "host_%d.log" % port)
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        with open(self.decks["Veyran"], encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, "Sam", "pw-live-r2", "Veyran", f.read(), fingerprint=self.cert.fingerprint,
                              code="guestcode")
        overs = []
        handle = g.handle

        def counting(m):
            if isinstance(m, dict) and m.get("t") == "game_over":
                overs.append(g.reconnects)
            return handle(m)
        g.handle = counting
        blocked = {"on": False}
        shake = g._handshake

        def gated(line):
            if blocked["on"]:
                raise OSError("held back by the test")
            return shake(line)
        g._handshake = gated
        g.start()
        self.addCleanup(g.close)
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.state and g.state, 90, pump=[h, g]), "the game never started")
        # the guest's connection is cut and it can't get back yet
        blocked["on"] = True
        try:
            g.proc.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.assertTrue(wait_for(lambda: h.peer_dropped is not None, 30, pump=[h, g]), "the host never saw the guest drop")
        # the host concedes: the game ends while the guest is away
        h.concede()
        self.assertTrue(wait_for(lambda: h.game_over, 60, pump=[h, g]), "the host's game never ended")
        self.assertFalse(g.game_over, "the guest heard game over while it had no connection")
        blocked["on"] = False
        self.assertTrue(wait_for(lambda: g.reconnects >= 1, 30, pump=[h, g]), "the guest never got back (%s)" % g.net_lost)
        self.assertTrue(wait_for(lambda: g.game_over, 20, pump=[h, g]), "game over was not sent again on rejoin")
        self.assertTrue(wait_for(lambda: (g.state or {}).get("gameOver"), 15, pump=[h, g]), "the guest's board never said game over")
        wait_for(lambda: False, 2, pump=[h, g])
        self.assertEqual(overs, [1], "game over should come once, on the new connection")


if __name__ == "__main__":
    unittest.main()
