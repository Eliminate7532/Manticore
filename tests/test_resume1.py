# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RESUME1: a guest who left an online game of three or four is in the host's save, so the game continues from it.

ORS1 (the online soak through the relay, 4 Nov) found two games whose play-back stopped early, the same on 127.0.0.1 (seeds
43063 and 44095). In both a guest had left the pod (--leave): NetHost conceded for it (guestLeft), but that concede was never
written to the journal, so the continued game still had that player in it and waited for them at the next question the original
had asked someone else ("seat 3 never reached question 91"). Now guestLeft writes {"c":"concede","left":true} for the seat.

Fast tests read the source; the live test (Java and forge_runtime/) plays a 3-player game, one friend leaves, the game goes on,
the host's engine closes at a quiet moment, and the game is continued from the save: the play-back must reach the end and the
host and the friend who stayed must see the same board as before.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_net as fn
import online_save
import tests.live as live
from tests.test_mp1 import deck_file, free_port, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETHOST = os.path.join(ROOT, "java_bridge", "src", "forge", "bridge", "NetHost.java")


def guest_left_body():
    with open(NETHOST, encoding="utf-8") as f:
        src = f.read()
    start = src.index("static void guestLeft(SeatWire seat)")
    return src[start:src.index("\n    }\n", start)]


class SourceTests(unittest.TestCase):
    def test_guest_left_journals_the_concede_when_it_really_happens(self):
        body = guest_left_body()
        self.assertIn("journal(seat.seat, leftConcede())", body)
        self.assertRegex(body, r"Main\.concedeWhenSafe\(g, gc, System\.currentTimeMillis\(\),\s*\(\) -> journal\(seat\.seat")

    def test_the_callback_runs_right_before_the_concede(self):
        with open(os.path.join(ROOT, "java_bridge", "src", "forge", "bridge", "Main.java"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("Runnable atConcede) {"):]
        body = body[:body.index("\n    }\n")]
        self.assertLess(body.index("atConcede.run()"), body.index("gc.concede()"))
        self.assertIn("concedeWhenSafe(gui, gc, since, atConcede)", body)        # the retry keeps it

    def test_a_player_already_out_is_neither_conceded_nor_journalled_again(self):
        body = guest_left_body()
        self.assertRegex(body, r"hasLost\(\)")
        self.assertIn("!out", body)

    def test_the_journal_line_is_a_plain_concede_marked_left(self):
        # round RESUME2 (Karl, 8 Nov): RESUME1's "seqs" field and the play-back's wait on it are gone
        with open(NETHOST, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"static JsonObject leftConcede\(\) \{(.*?)\n    \}", src, re.S)
        self.assertIsNotNone(m)
        self.assertIn('addProperty("c", "concede")', m.group(1))
        self.assertIn('addProperty("left", true)', m.group(1))
        self.assertNotIn("seqs", m.group(1))

    def test_the_play_back_no_longer_waits_on_seqs(self):
        with open(os.path.join(ROOT, "java_bridge", "src", "forge", "bridge", "ResumeFeeder.java"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('"seqs"', src)


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are switched off")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="resume1_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        cls.decks = {name: deck_file(cls.tmp, sample, name) for name, sample in (
            ("Lathril", "typal_lathril.txt"), ("Veyran", "spellslinger_veyran.txt"), ("Adeline", "go_wide_adeline.txt"))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def host(self, port, journal, **kw):
        h = fc.HostSession(self.decks["Lathril"], "Karl", port, "pw-live-r1", self.cert,
                           os.path.join(self.tmp, "guest_%d.dck" % port), upnp=False, bind="127.0.0.1", code="hostcode",
                           journal_path=journal, seed=61, **kw)
        h.stderr_path = os.path.join(self.tmp, "host_%d.log" % port)
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        return h

    def guest(self, port, name, deck):
        with open(self.decks[deck], encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, name, "pw-live-r1", deck, f.read(), fingerprint=self.cert.fingerprint,
                              code="guestcode")
        g.start()
        self.addCleanup(g.close)
        return g

    @staticmethod
    def quiet(sessions):
        if any(s.requests for s in sessions):
            return False
        return any(((s.state or {}).get("prompt") or {}).get("message", "").startswith("Priority") and (s.state or {}).get("asking")
                   for s in sessions)

    def test_a_game_a_friend_left_continues_from_its_save(self):
        from tools import online_soak as osk
        osk._load_modules()
        import replay
        folder = tempfile.mkdtemp(prefix="save_", dir=self.tmp)
        journal = os.path.join(folder, "cmds.jsonl")
        port = free_port()
        h = self.host(port, journal, guests=2)
        sam = self.guest(port, "Sam", "Veyran")
        self.assertTrue(wait_for(lambda: h.guests_joined == ["Sam"], 15, pump=[h, sam]))
        ann = self.guest(port, "Ann", "Adeline")
        self.assertTrue(wait_for(lambda: h.ready and sam.ready and ann.ready and h.seat_list, 60, pump=[h, sam, ann]))
        seats = [osk.Seat("host", h, 61), osk.Seat("guest", sam, 71), osk.Seat("guest2", ann, 81)]

        def play(until, seconds):
            end = time.time() + seconds
            while time.time() < end and not until() and not h.game_over:
                for seat in seats:
                    seat.step()
                time.sleep(0.02)
            return until()

        self.assertTrue(play(lambda: ((h.state or {}).get("turn") or 0) >= 3, 240), "the game never reached turn 3")
        ann_seat = ann.seat
        ann.close()                                                    # Ann closes her window: NetHost concedes for her
        seats = seats[:2]
        self.assertTrue(play(lambda: bool(h.players_left), 30), "the host never heard that Ann left")
        left_at = online_save.count_lines(journal)
        turn = (h.state or {}).get("turn") or 0
        # play on long enough that the original asks the others things Ann's seat would have been asked in her place
        self.assertTrue(play(lambda: ((h.state or {}).get("turn") or 0) >= turn + 3 and online_save.count_lines(journal) > left_at + 4,
                             300), "the game didn't go on after Ann left")
        self.assertFalse(h.game_over, "the game ended before it could be saved")
        # a quiet moment: the bots play on until the game waits for a person, then nobody answers anything
        self.assertTrue(osk._wait_for_person([(h, None), (sam, None)], limit=10) or
                        play(lambda: osk._wait_for_person([(h, None), (sam, None)], limit=3), 120),
                        "no quiet moment to save at")
        before_host, before_sam = replay.summary(h.state, any_waiting=True), replay.summary(sam.state, any_waiting=True)
        saved = list(h.seat_list)
        with open(journal, encoding="utf-8") as f:
            lines = [json.loads(x) for x in f if x.strip()]
        left = [x for x in lines if x["s"] == ann_seat and x["c"].get("c") == "concede" and x["c"].get("left")]
        self.assertEqual(len(left), 1, "Ann's leaving is not in the save (once)")
        self.assertNotIn("seqs", left[0]["c"])                         # round RESUME2
        h.close()
        self.assertTrue(wait_for(lambda: sam.exited, 15, pump=[sam]))
        sam.close()
        # ---- continue it
        src = os.path.join(folder, "cmds_1.jsonl")
        shutil.copyfile(journal, src)
        port2 = free_port()
        h2 = self.host(port2, journal, resume={"replay": src, "guests": [(g["seat"], g["name"], g["deck"]) for g in saved]})
        self.assertTrue(wait_for(lambda: not h2.resuming or h2.exited, 300, pump=[h2]), "the play-back never finished")
        self.assertIsNone((h2.replay_result or {}).get("diverged"), h2.replay_result)
        # round RESUME2 (Karl, 8 Nov: "skip guests who are out"): Ann lost, so the host doesn't wait for her - only Sam joins
        self.assertEqual(h2.out_guests(), {"Ann"}, h2.replay_result)
        self.assertEqual(h2.waited_for(), ["Sam"])
        self.assertFalse(h2.everyone_back(), "the host didn't wait for Sam")
        sam2 = self.guest(port2, "Sam", "Veyran")
        self.assertTrue(wait_for(lambda: h2.everyone_back() and sam2.ready and sam2.state and h2.state, 60,
                                 pump=[h2, sam2]), "the continued game didn't go on without Ann")
        self.assertNotIn("Ann", h2.guests_joined)
        # the continued game plays on by itself from the last saved click until it waits for a person (round RESUME-DIFF)
        self.assertTrue(osk._wait_for_person([(h2, None), (sam2, None)]), "the continued game never waited for a person")
        if os.environ.get("RESUME1_DEBUG"):
            print("\nBEFORE", before_host, before_sam, "\nAFTER", replay.summary(h2.state, any_waiting=True),
                  replay.summary(sam2.state, any_waiting=True), "\nRESULT", h2.replay_result, "\nJOURNAL", src, h2.stderr_path)
            shutil.copytree(folder, "/home/claude/work/dbg_%d" % port2)
            shutil.copy(h2.stderr_path, "/home/claude/work/dbg_%d/" % port2)
            shutil.copy(h.stderr_path, "/home/claude/work/dbg_%d/orig.log" % port2)
        self.assertEqual(replay.differences(before_host, replay.summary(h2.state, any_waiting=True)), [])
        self.assertEqual(replay.differences(before_sam, replay.summary(sam2.state, any_waiting=True)), [])
        ann_player = next(p for p in h2.state["players"] if p.get("name") == "Ann")
        self.assertTrue(ann_player.get("lost"), "Ann is back in the continued game")


if __name__ == "__main__":
    unittest.main()
