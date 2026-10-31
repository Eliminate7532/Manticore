# SPDX-License-Identifier: GPL-3.0-or-later
"""Round NET-RESUME (26 Oct 2026): the two online faults the long online soak of 24 Oct found.

O1 - a game continued from the host's save whose friend got nothing: the play-back's last click made Forge ask the host or a
     friend something on Swing's thread, which then waited there for the answer; the "welcome back" and the re-asking that
     would have let anyone answer were queued behind it on the same thread, so nothing came (see docs/rounds/NET-RESUME.md).
O2 - in a pod, a live seat's click on a card that had just left the game with the player who conceded: Forge looked it up in no
     zone and logged "[ERROR] Game: findByView: ... not found in any zone". The bridge now drops it ("gone").
"""
import os
import re
import shutil
import tempfile
import time
import unittest

import json

import forge_client as fc
from tests import live
from tests import test_mp2b, test_mp2c
from tests.test_mp2c import free_port, wait_for

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "java_bridge", "src", "forge", "bridge")


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_a_click_on_a_card_that_left_with_a_loser_is_dropped_as_gone(self):
        s = read("Main.java")
        card = s[s.index('case "card": {'):]
        card = card[:card.index("break;\n            }")]
        self.assertIn("leftWithALoser(cv)", card)
        self.assertIn('"gone"', card)
        self.assertLess(card.index("leftWithALoser(cv)"), card.index("gc.selectCard"))
        fn = s[s.index("static boolean leftWithALoser"):]
        fn = fn[:fn.index("\n    }\n")]
        self.assertIn("findById", fn)
        self.assertIn("hasLost()", fn)

    def test_the_play_back_ends_with_nothing_queued_behind_a_question_on_swings_thread(self):
        s = read("NetHost.java")
        fin = s[s.index("static void replayFinished"):]
        fin = fin[:fin.index("\n    }\n")]
        # the welcome back and the re-asking are not queued on Swing's thread behind the play-back's last click
        code = "\n".join(ln.split("//")[0] for ln in fin.splitlines())
        self.assertNotIn("invokeLater", code)
        self.assertIn("resendPending", code)
        join = s[s.index("static void resumeJoin"):]
        join = join[:join.index("\n    }\n")]
        self.assertNotIn("invokeLater(() -> welcomeBack", join)
        wb = s[s.index("static void welcomeBack"):]
        wb = wb[:wb.index("\n    }\n")]
        self.assertIn("resendPending", wb)

    def test_a_guest_who_joins_as_the_play_back_ends_is_never_left_muted(self):
        s = read("NetHost.java")
        join = s[s.index("static void resumeJoin"):]
        join = join[:join.index("\n    }\n")]
        # muting and the check of replayDone happen under the same lock as replayFinished's unmuting
        self.assertIn("synchronized (RESUME_LOCK)", join)
        fin = s[s.index("static void replayFinished"):]
        fin = fin[:fin.index("\n    }\n")]
        self.assertIn("synchronized (RESUME_LOCK)", fin)


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveConcedeClickTests(unittest.TestCase):
    """O2 (the online soak of 24 Oct, game 9): Ann concedes in a game of three; Karl then clicks one of Ann's cards."""
    setUpClass = test_mp2c.LiveTests.__dict__["setUpClass"]
    tearDownClass = test_mp2c.LiveTests.__dict__["tearDownClass"]
    start_host = test_mp2c.LiveTests.start_host
    guest = test_mp2c.LiveTests.guest
    msg = staticmethod(test_mp2c.LiveTests.msg)
    play_pregame = test_mp2c.LiveTests.play_pregame

    def test_a_click_on_a_card_of_a_player_who_conceded_is_dropped(self):
        h, port = self.start_host(guests=2)
        sam = self.guest(port, "Sam", "Veyran")
        ann = self.guest(port, "Ann", "Adeline")
        everyone = [h, sam, ann]
        self.assertTrue(wait_for(lambda: all(s.ready and s.state for s in everyone), 60, pump=everyone))
        self.assertTrue(self.play_pregame(everyone, lambda: any(self.msg(s).startswith("Priority") for s in everyone)),
                        "the game never got past the opening hands")
        ann_player = next(p for p in h.state["players"] if p["name"] == "Ann")
        ann_cards = [c["id"] for z in ("command", "battlefield") for c in ann_player["zones"].get(z, []) or []]
        self.assertTrue(ann_cards, "Ann has no card Karl can see")
        ann.concede()
        self.assertTrue(wait_for(lambda: any(p["name"] == "Ann" and p.get("lost") for p in (h.state or {}).get("players", [])),
                                 30, pump=everyone), "Ann's concede never showed")
        wait_for(lambda: False, 1.0, pump=[h, sam])
        h.click_card(ann_cards[0])
        gone = wait_for(lambda: any(d.get("reason") == "gone" for d in h.dropped), 10, pump=[h, sam])
        time.sleep(0.5)
        with open(h.stderr_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
        found = [ln for ln in log.splitlines() if "findByView" in ln]
        self.assertEqual(found, [], "Forge looked the card up in no zone")
        self.assertTrue(gone, h.dropped)
        # and the game goes on for the two still in it
        self.assertFalse(h.exited)


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveResumeTests(unittest.TestCase):
    """O1: the host's program closes while Forge asks the host "Choose an ability" (Lorien Revealed: cast it or islandcycle) -
    a question asked ON Swing's thread, after the click. The saved game ends with that click. Continuing it, the play-back's
    last click asks the question again on Swing's thread; before the fix the welcome back and the re-asking were queued on
    Swing's thread behind it, so neither table was told anything: the friend got no snapshot (the soak's game 5).

    Task o1-test-0027 (27 Oct): the test used to catch its planted error (the re-asking put back inside an invokeLater, with
    replay_done still sent from the feeder) only 1 run in 4. The freeze shows only when the play-back's last click asks the host
    on Swing's thread BEFORE the host's table hears replay_done: the table drops questions while it is resuming, so the question
    can then only come back through the re-asking. With the save ending on the click, the feeder sent replay_done at once and
    usually won the race against Swing's thread, so the question simply arrived after it. The test now appends one harmless line
    for the host after the click ({"c": "flush"}, which the host's table sends by itself when Forge is quiet): the feeder lets the
    host's view settle before applying it (ResumeFeeder.settle), so the click is handled and the question asked while the
    play-back still runs. The test checks that this happened (the question reached the host's table while it was resuming) and
    only then that it was asked again."""
    setUpClass = test_mp2b.LiveTests.__dict__["setUpClass"]
    tearDownClass = test_mp2b.LiveTests.__dict__["tearDownClass"]
    host = test_mp2b.LiveTests.host
    guest = test_mp2b.LiveTests.guest

    def test_a_save_that_ends_on_a_question_asked_on_swings_thread_continues(self):
        from tests.test_mp2 import LiveTests as MP2Live
        folder = tempfile.mkdtemp(prefix="save_", dir=self.tmp)
        journal = os.path.join(folder, "cmds.jsonl")
        port = free_port()
        h = self.host(port, journal, seed=33, dev=True)
        g = self.guest(port)
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.seat_list, 60, pump=[h, g]))
        self.assertTrue(MP2Live.play_pregame(self, [h, g], lambda: any(MP2Live.msg(s).startswith("Priority") for s in (h, g))))
        h.setup(["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=L\u00f3rien Revealed", "humanbattlefield=Island;Island;Island;Island;Island",
                 "humanlibrary=Island;Forest;Mountain;Plains;Swamp;Island;Island;Island",
                 "aihand=Forest", "ailibrary=Forest;Forest;Forest", "aibattlefield=", "removesummoningsickness=true"])
        self.assertTrue(wait_for(lambda: h.setups_done >= 1, 30, pump=[h, g]))
        asked, end = None, time.time() + 60
        while time.time() < end and asked is None:
            h.poll()
            g.poll()
            for r in list(h.requests):
                if "ability" in str(r.get("title", "")).lower():
                    asked = r
            if asked is None:
                mh = MP2Live.msg(h)
                hand = [c for c in (h.me() or {}).get("zones", {}).get("hand", []) if "Revealed" in c.get("name", "")]
                if hand and mh.startswith("Priority") and "Main phase" in mh:
                    h.click_card(hand[0]["id"])
                    wait_for(lambda: bool(h.requests), 5, pump=[h, g])
                elif MP2Live.msg(g).startswith("Priority"):
                    g.ok()
                    wait_for(lambda: False, 0.4, pump=[h, g])
                elif mh.startswith("Priority") or h.is_mulligan_prompt():
                    h.ok()
                    wait_for(lambda: False, 0.4, pump=[h, g])
            time.sleep(0.1)
        self.assertIsNotNone(asked, "never asked to choose an ability")
        wait_for(lambda: False, 1.0, pump=[h, g])
        seat = h.seat_list[0]
        h.close()                                                      # the host's program closes with the question open
        g.close()
        src = os.path.join(folder, "cmds_1.jsonl")
        shutil.copyfile(journal, src)
        with open(src, encoding="utf-8") as f:
            last = json.loads(f.read().strip().splitlines()[-1])
        self.assertEqual(last["c"].get("c"), "card", last)               # the save ends on the click
        with open(src, "a", encoding="utf-8") as f:                     # o1-test: the question is asked during the play-back
            f.write(json.dumps({"s": last["s"], "c": {"c": "flush"}}) + "\n")
        port2 = free_port()
        h2 = self.host(port2, journal, seed=33, dev=True,
                       resume={"replay": src, "guests": [(seat["seat"], seat["name"], seat["deck"])]})
        order = []                                                      # what the host's table hears, and when
        handle = h2.handle

        def watch(m, handle=handle):
            if m.get("t") == "replay_done":
                order.append("replay_done")
            elif m.get("t") == "request" and "ability" in str(m.get("title", "")).lower():
                order.append("ability (resuming)" if h2.resuming else "ability")
            handle(m)
        h2.handle = watch
        self.assertTrue(wait_for(lambda: not h2.resuming or h2.exited, 180, pump=[h2]), "the play-back never finished")
        self.assertIn("ability (resuming)", order,
                      "set-up: the play-back's last click was not asked before the play-back ended (%s)" % order)
        g2 = self.guest(port2)
        got = wait_for(lambda: h2.everyone_back() and g2.ready and g2.state, 30, pump=[h2, g2])
        asked_again = wait_for(lambda: any("ability" in str(r.get("title", "")).lower() for r in h2.requests), 15,
                               pump=[h2, g2])
        self.assertTrue(got, "the friend got no snapshot after the play-back (ready %s, state %s)" % (g2.ready, bool(g2.state)))
        self.assertTrue(asked_again, "the host was not asked to choose an ability again (%s)" % order)
        self.assertEqual(order[:2], ["ability (resuming)", "replay_done"], order)


if __name__ == "__main__":
    unittest.main()
