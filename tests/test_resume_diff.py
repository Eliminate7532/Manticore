# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RESUME-DIFF: tools/online_soak.py --resume compares the boards only where the game waits for a person.

The 26 Oct online soak's game 12 (merge pack 26b) failed online_resume: the soak took its "before" picture 1.5 s after the
guest's bot had answered a question, while the AIs were still playing their turns (turn 12, AI 1's Swiftfoot Boots on the
stack, the host's auto-pass deciding), and compared it with the continued game 2 s after everyone was back. The continued
game had passed through exactly that board and played on. These tests drive _resume_game with stand-in sessions whose
snapshots change over time, as Forge's do; no Forge is needed."""
import os
import tempfile
import time
import unittest

from tools import online_soak as osk

osk._load_modules()


def board(turn, phase, prompt, ai_bf, asking=False, seq=14, stack=()):
    return {"turn": turn, "phase": phase, "asking": asking, "inputSeq": seq, "prompt": {"message": prompt},
            "stack": [{"text": t} for t in stack],
            "players": [{"id": 1, "name": "Host", "life": 30, "zones": {"battlefield": [{"name": "Plains"}]}},
                        {"id": 2, "name": "Guest", "life": 28, "zones": {"battlefield": [{"name": "Forest"}]}},
                        {"id": 3, "name": "AI 1 (ai0)", "life": 25,
                         "zones": {"battlefield": [{"name": "Card %d" % k} for k in range(ai_bf)]}}]}


# game 12, in short: the guest answers on turn 11; the AIs play; AI 1's Boots go on the stack (the board the old soak
# compared); the Boots resolve and AI 1 attacks; then the host is asked for priority - the game waits for a person.
GUEST_ASKED = board(11, "MAIN1", "Priority: Guest\nTurn: 11 (Guest)", 3, asking=True, seq=7)
BOOTS = board(12, "MAIN1", "Waiting for AI 1 (ai0)...", 4, stack=["Swiftfoot Boots"])
COMBAT = board(12, "COMBAT_END", "Waiting for AI 1 (ai0)...", 5)
HOST_ASKED = board(12, "MAIN2", "Priority: Host\nTurn: 12 (AI 1)", 5, asking=True, seq=15)
GUEST_WAITS = dict(HOST_ASKED, asking=False, inputSeq=8, prompt={"message": "Waiting for Host..."})


class Fake:
    """A session whose snapshot follows a script of (seconds after its first poll, state, requests)."""

    def __init__(self, script, **attrs):
        self.script, self.t0, self.idx = list(script), None, -1
        self.state, self.state_version, self.requests, self.infos = None, 0, [], []
        self.closed_state, self.closed = None, False
        for k, v in attrs.items():
            setattr(self, k, v)

    def poll(self):
        now = time.monotonic()
        if self.t0 is None:
            self.t0 = now
        idx = max(i for i, (t, _, _) in enumerate(self.script) if t <= now - self.t0)
        if idx != self.idx:
            self.idx, (_, self.state, self.requests) = idx, self.script[idx]
            self.state_version += 1

    def close(self):
        self.closed, self.closed_state = True, self.state


class Stop(Exception):
    pass


def stopping_host(*a, **k):
    raise Stop()


class Cert:
    fingerprint = "ab"


class ResumeDiffBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = os.path.join(self.tmp.name, "cmds.jsonl")
        with open(self.journal, "w") as f:
            f.write('{"s": 1, "c": {"c": "ok", "at": 3}}\n')
        self.real_host, self.real_net = osk.fc.HostSession, osk.fc.NetSession

    def tearDown(self):
        osk.fc.HostSession, osk.fc.NetSession = self.real_host, self.real_net
        self.tmp.cleanup()

    def host_and_guest(self, host_script, guest_script):
        host = Fake(host_script, seat_list=[{"seat": 2, "name": "Guest", "deck": "g.dck"}], deck_path="h.dck")
        guest = Fake(guest_script)
        seats = [osk.Seat("host", host, 1), osk.Seat("guest", guest, 2)]
        seats[1].answered_seq = 7                # the guest's bot has just clicked its answer to question 7
        return host, guest, seats

    def resume(self, host, guest, seats):
        return osk._resume_game(osk.OnlineResult(12, 2822, []), 2822, self.tmp.name, Cert(), host, [guest], seats,
                                self.journal, self.tmp.name, {})


class BeforePictureTests(ResumeDiffBase):
    def test_the_board_is_taken_where_the_game_waits_for_a_person_not_mid_flight(self):
        # base: a fixed 1.5 s wait closed the tables on the Boots board, while the AIs were still playing
        osk.fc.HostSession = stopping_host
        host, guest, seats = self.host_and_guest(
            [(0, board(11, "MAIN1", "Waiting for Guest...", 3), []), (0.3, BOOTS, []), (1.8, COMBAT, []), (2.4, HOST_ASKED, [])],
            [(0, GUEST_ASKED, []), (0.3, dict(BOOTS, inputSeq=8), []), (1.8, dict(COMBAT, inputSeq=8), []),
             (2.4, GUEST_WAITS, [])])
        with self.assertRaises(Stop):
            self.resume(host, guest, seats)
        self.assertTrue(host.closed and guest.closed)
        self.assertEqual(host.closed_state["phase"], "MAIN2")
        self.assertTrue(host.closed_state["asking"])
        self.assertEqual(guest.closed_state["prompt"]["message"], "Waiting for Host...")

    def test_a_question_the_bot_just_answered_is_not_a_waiting_point(self):
        f = Fake([(0, GUEST_ASKED, [])])
        f.poll()
        self.assertFalse(osk._person_asked(f, 7))
        self.assertTrue(osk._person_asked(f, None))
        self.assertTrue(osk._person_asked(f, 6))

    def test_the_seat_remembers_which_question_its_bot_answered(self):
        f = Fake([(0, GUEST_ASKED, [])])
        seat = osk.Seat("guest", f, 2)
        seat.bot.next_action = lambda state, requests, mem: ("ok",)
        sent = []
        f.ok = lambda: sent.append("ok")
        real_do = osk.soak._do
        osk.soak._do = lambda s, move: sent.append(move)
        try:
            seat.step()
        finally:
            osk.soak._do = real_do
        self.assertEqual(sent, [("ok",)])
        self.assertEqual(seat.answered_seq, 7)

    def test_a_request_while_waiting_means_try_later_and_nothing_is_closed(self):
        osk.fc.HostSession = stopping_host
        host, guest, seats = self.host_and_guest(
            [(0, board(11, "MAIN1", "Waiting for Guest...", 3), []), (0.3, BOOTS, [{"id": 5, "kind": "choose"}])],
            [(0, GUEST_ASKED, []), (0.3, dict(BOOTS, inputSeq=8), [])])
        self.assertEqual(self.resume(host, guest, seats), "later")
        self.assertFalse(host.closed or guest.closed)


class AfterPictureTests(ResumeDiffBase):
    def test_the_continued_game_is_compared_once_it_waits_for_a_person_again(self):
        # the saved moment is a real waiting point (the host asked); the continued game is played back to the last saved
        # click and plays on by itself, passing through the Boots and the combat, and only then asks the host again
        host, guest, seats = self.host_and_guest([(0, HOST_ASKED, [])], [(0, GUEST_WAITS, [])])
        made = {}

        class H2(Fake):
            def __init__(s, *a, **k):
                Fake.__init__(s, [(0, BOOTS, []), (1.0, COMBAT, []), (2.6, HOST_ASKED, [])], resuming=False, hosting=1,
                              exited=False, replay_result={"applied": 11, "skipped": 0, "total": 11})
                made["host"] = s

            def start(s):
                pass

            def everyone_back(s):
                return True

        class G2(Fake):
            def __init__(s, *a, **k):
                Fake.__init__(s, [(0, dict(BOOTS, inputSeq=8), []), (1.0, dict(COMBAT, inputSeq=8), []),
                                  (2.6, GUEST_WAITS, [])], ready=True)

            def start(s):
                pass

        osk.fc.HostSession, osk.fc.NetSession = H2, G2
        result = osk.OnlineResult(12, 2822, [])
        got = osk._resume_game(result, 2822, self.tmp.name, Cert(), host, [guest], seats, self.journal, self.tmp.name, {})
        self.assertIsInstance(got, tuple)
        self.assertEqual([f for f in result.findings if f["rule"] == "online_resume"], [])
        self.assertEqual(made["host"].state["phase"], "MAIN2")

    def test_a_continued_game_that_really_differs_is_still_a_failure(self):
        host, guest, seats = self.host_and_guest([(0, HOST_ASKED, [])], [(0, GUEST_WAITS, [])])

        class H2(Fake):
            def __init__(s, *a, **k):
                Fake.__init__(s, [(0, BOOTS, []), (0.5, dict(HOST_ASKED, players=board(12, "x", "", 7)["players"]), [])],
                              resuming=False, hosting=1, exited=False, replay_result={"applied": 11})

            def start(s):
                pass

            def everyone_back(s):
                return True

        class G2(Fake):
            def __init__(s, *a, **k):
                Fake.__init__(s, [(0, GUEST_WAITS, [])], ready=True)

            def start(s):
                pass

        osk.fc.HostSession, osk.fc.NetSession = H2, G2
        result = osk.OnlineResult(12, 2822, [])
        osk._resume_game(result, 2822, self.tmp.name, Cert(), host, [guest], seats, self.journal, self.tmp.name, {})
        fails = [f["detail"] for f in result.findings if f["rule"] == "online_resume"]
        self.assertEqual(len(fails), 1)
        self.assertIn("battlefield differs (report 5 cards, replay 7)", fails[0])


if __name__ == "__main__":
    unittest.main()
