# SPDX-License-Identifier: GPL-3.0-or-later
"""Round LAST1c (12 Oct 2026): LAST1's bridge round - the two bridge fixes LAST1b found (ROADMAP: "LAST1, the next bridge round").

1. A restore that fails says so. The bridge's SetupState sent {"t":"setup_done"} after "bridge: setup failed" too, so a saved game
   that Forge refused (LAST1's first live run: a 4-player save taken after a knock-out, "Non-matching number of players, (4 vs. 2)")
   left the table saying "Restored to turn 44." over a fresh game. A restore (loadstate) now answers {"t":"state_load_failed",
   "reason", "untouched"}; the table trusts a failure before "done", and after "done" waits for the table to show the saved turn
   (an older bridge that still says setup_done is caught there). A dev set-up still says setup_done, with "ok" and "reason".
2. A save keeps The Monarch, The Initiative and the City's Blessing. They are effect cards with no paper card in the command
   zone, and Forge's GameState leaves those out, so a restore dropped them. Main.saveState appends "#manticore" lines (Forge's
   own parser skips lines starting with '#'), SetupState reads them back and puts the designations on the table.
   NOT kept (no format for them in GameState): hideaway and other cards' own effects, emblems, a dungeon and its room.

Offline tests need pygame for the table; the live tests need Java and forge_runtime/ (MANTICORE_SKIP_LIVE=1 skips them)."""
import copy
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
import forge_dialogs as dlg
import last_good
import tests.live as live
from tests.forge_fake import load_state
from tests.test_forge_table import make_gui

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
FOUR = os.path.join(BASE, "tests", "fixtures", "last1b", "four_players.state")
DECK = os.path.join(BASE, "sample_decks", "stompy_goreclaw.txt")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def restoring_table(turn_now=1):
    st = copy.deepcopy(load_state("main1_start"))
    st["turn"] = turn_now
    gui = make_gui(st)
    gui.session.last_state_ok = True
    gui.pending_restore = {"path": "x", "turn": 7, "stage": "apply", "t0": 0.0, "t1": 10.0, "t2": None, "expect": 1}
    gui.modal = dlg.WorkingDialog("t", "t")
    return gui, st


class SourceTests(unittest.TestCase):
    def test_a_restore_failure_is_state_load_failed_and_a_dev_setup_still_says_done(self):
        src = read(os.path.join(SRC, "SetupState.java"))
        self.assertIn('m.addProperty("t", "state_load_failed")', src)
        self.assertIn('m.addProperty("untouched", untouched)', src)
        self.assertIn('m.addProperty("ok", failure == null)', src)
        self.assertIn("catch (Throwable e)", src)
        main = read(os.path.join(SRC, "Main.java"))
        self.assertIn("st.applyThenSignal(match.getGame(), Checks.wire, true, gui::markDirty)", main)   # loadstate is a restore
        self.assertIn("st.applyThenSignal(match.getGame(), Checks.wire);", main)            # the dev setup is not

    def test_the_save_carries_the_marks_and_forge_skips_them(self):
        main = read(os.path.join(SRC, "Main.java"))
        self.assertIn("gs.toString() + SetupState.marks(g)", main)
        src = read(os.path.join(SRC, "SetupState.java"))
        self.assertIn('MARK = "#manticore "', src)                # Forge's GameState.splitLine skips a line starting with '#'
        for word in ("monarch=p", "initiative=p", "blessing=p"):
            self.assertIn(word, src)


class ClientTests(unittest.TestCase):
    def session(self):
        s = fc.ForgeSession.__new__(fc.ForgeSession)
        fc.ForgeSession.__init__(s, "a.dck", ["b.dck"])
        return s

    def test_a_failed_dev_setup_is_counted_and_kept(self):
        s = self.session()
        s.handle({"t": "setup_done", "ok": True})
        s.handle({"t": "setup_done", "ok": False, "reason": "java.lang.RuntimeException: Non-matching number of players"})
        s.handle({"t": "setup_done"})                             # an older bridge: no "ok" at all
        self.assertEqual(s.setups_done, 3)
        self.assertEqual(s.setup_failures, ["java.lang.RuntimeException: Non-matching number of players"])

    def test_a_failed_restore_says_whether_the_table_was_touched(self):
        s = self.session()
        s.handle({"t": "state_load_failed", "reason": "the save holds 2 player(s), this game 4", "untouched": True})
        self.assertEqual((s.state_load_failed, s.state_load_untouched), ("the save holds 2 player(s), this game 4", True))
        s.send = lambda **cmd: True
        s.load_state("y")                                         # a new attempt forgets the old answer
        self.assertEqual((s.state_load_failed, s.state_load_untouched), (None, False))
        s.handle({"t": "state_load_failed", "reason": "NPE"})
        self.assertEqual((s.state_load_failed, s.state_load_untouched), ("NPE", False))

    def test_the_ready_line_says_whether_saves_keep_the_marks(self):
        s = self.session()
        s.handle({"t": "ready", "lastState": True})
        self.assertFalse(s.last_state_marks)
        s.handle({"t": "ready", "lastState": True, "lastStateMarks": True})
        self.assertTrue(s.last_state_marks)


class PlayersInTests(unittest.TestCase):
    def test_the_mark_lines_are_not_players(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "s.state")
        with open(path, "w", encoding="utf-8") as f:
            f.write(read(FOUR) + "#manticore monarch=p3|CN2\n#manticore initiative=p0|\n#manticore blessing=p5|\n")
        self.assertEqual(last_good.players_in(path), 4)


class RestoreTickTests(unittest.TestCase):
    def test_a_failure_wins_even_when_setup_done_also_came(self):
        """An older bridge sent setup_done after a failed set-up; this one sends state_load_failed. If both are there, it failed."""
        gui, st = restoring_table()
        gui.session.handle({"t": "setup_done"})
        gui.session.handle({"t": "state_load_failed", "reason": "boom", "untouched": True})
        with mock.patch.object(gui, "say") as say:
            gui.restore_tick(12.0)
        self.assertIsNone(gui.pending_restore)
        self.assertTrue(gui.modal.done)
        self.assertIn("boom", say.call_args[0][0])
        self.assertNotIn("Restored", say.call_args[0][0])
        self.assertIn("keep playing this new game", say.call_args[0][0])

    def test_a_failure_after_the_table_was_changed_says_to_start_again(self):
        gui, st = restoring_table()
        gui.session.handle({"t": "state_load_failed", "reason": "NullPointerException"})
        with mock.patch.object(gui, "say") as say:
            gui.restore_tick(12.0)
        self.assertIn("may not be right", say.call_args[0][0])
        self.assertNotIn("keep playing", say.call_args[0][0])

    def test_done_then_the_saved_turn_on_the_table_is_restored(self):
        gui, st = restoring_table(turn_now=1)
        gui.session.handle({"t": "setup_done", "ok": True})
        with mock.patch.object(gui, "say") as say:
            gui.restore_tick(12.0)
            self.assertIsNotNone(gui.pending_restore)             # done, but the table still shows the new game's turn 1
            self.assertEqual(gui.pending_restore["stage"], "check")
            say.assert_not_called()
            st["turn"] = 7
            gui.restore_tick(12.5)
        self.assertIsNone(gui.pending_restore)
        self.assertTrue(gui.modal.done)
        self.assertEqual(say.call_args[0][0], "Restored to turn 7.")

    def test_done_but_the_turn_never_comes_says_it_may_not_be_back(self):
        """What an older bridge's setup_done after a failed set-up looks like: done, and the table still at the new game's turn."""
        gui, st = restoring_table(turn_now=1)
        gui.session.handle({"t": "setup_done"})
        with mock.patch.object(gui, "say") as say:
            gui.restore_tick(12.0)
            gui.restore_tick(16.9)
            self.assertIsNotNone(gui.pending_restore)
            gui.restore_tick(17.1)
        self.assertIsNone(gui.pending_restore)
        text = say.call_args[0][0]
        self.assertIn("may not have been put back", text)
        self.assertIn("turn 1", text)
        self.assertIn("turn 7", text)
        self.assertNotIn("Restored", text)


# ---- live ----------------------------------------------------------------------------------------------------------------

BOARD = ["activeplayer=p0", "activephase=MAIN1", "turn=5", "p0life=35", "p1life=30",
         "p0battlefield=" + ";".join(["Forest"] * 10 + ["Wayward Swordtooth"]),      # ten permanents and Ascend: the City's Blessing
         "p0hand=Forest;Forest", "p0library=" + ";".join(["Forest"] * 8),
         "p1battlefield=Forest;Forest", "p1library=" + ";".join(["Forest"] * 8),
         "p0precast=Palace Jailer:TrigMonarch",                                       # p0 becomes the monarch
         "p1precast=Bloodboil Sorcerer:TrigInitiative"]                               # p1 takes the initiative


def command_names(state, index):
    p = state["players"][index]
    return [c.get("name") for c in (p.get("zones") or {}).get("command", [])]


def wait(s, what, seconds=60):
    """Poll until what() holds. A set-up changes the board without a new question, so ask for a fresh snapshot now and then
    (the table does the same after 2.5 s of quiet: QUIET_PING)."""
    end = time.time() + seconds
    ping = time.time() + 1.0
    while time.time() < end:
        s.poll()
        if what():
            return True
        if time.time() > ping:
            s.send(c="flush")
            ping = time.time() + 1.0
        time.sleep(0.1)
    return False


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    def checker(self):
        import card_check as cc
        chk = cc.Checker(DECK)
        chk.start()
        self.addCleanup(chk.stop)
        return chk.s

    def setup_board(self, s, lines):
        before = s.setups_done
        s.setup(lines)
        self.assertTrue(wait(s, lambda: s.setups_done > before), "no setup_done")
        return s.setups_done

    def test_monarch_initiative_and_blessing_survive_a_save_and_a_restore(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = self.checker()
        self.setup_board(s, BOARD)
        self.assertEqual(s.setup_failures, [])
        self.assertTrue(wait(s, lambda: "The Monarch" in command_names(s.state, 0)), command_names(s.state, 0))
        self.assertIn("City's Blessing", command_names(s.state, 0))
        self.assertIn("The Initiative", command_names(s.state, 1))
        self.assertTrue(wait(s, lambda: (s.state.get("asking") is True
                                         and (s.state.get("prompt") or {}).get("message", "").startswith("Priority:"))))
        path = os.path.join(tmp, "saved.state")
        n = s.save_state(path)
        self.assertTrue(wait(s, lambda: any(a.get("n") == n for a in s.state_saves)))
        answer = [a for a in s.state_saves if a.get("n") == n][-1]
        self.assertTrue(answer.get("ok"), answer)
        text = read(path)
        self.assertIn("#manticore monarch=p0|", text)
        self.assertIn("#manticore initiative=p1|", text)
        self.assertIn("#manticore blessing=p0|", text)
        # The board that comes back must not give the blessing again by itself: take Wayward Swordtooth (Ascend) out of the save.
        nosword = os.path.join(tmp, "no_swordtooth.state")
        with open(nosword, "w", encoding="utf-8") as f:
            f.write(re.sub(r";Wayward Swordtooth\|[^;\n]*", "", text))
        self.assertNotIn("Wayward Swordtooth", read(nosword))
        # a NEW game, as the table's Restore makes one; the save goes in at its first priority stop
        s2 = self.checker()
        before = s2.setups_done
        s2.load_state(nosword)
        self.assertTrue(wait(s2, lambda: s2.setups_done > before or s2.state_load_failed), "no answer to loadstate")
        self.assertIsNone(s2.state_load_failed)
        self.assertEqual(s2.setup_failures, [])
        self.assertTrue(wait(s2, lambda: s2.state.get("turn") == 5))
        self.assertTrue(wait(s2, lambda: "The Monarch" in command_names(s2.state, 0)), command_names(s2.state, 0))
        self.assertIn("City's Blessing", command_names(s2.state, 0))
        self.assertIn("The Initiative", command_names(s2.state, 1))
        self.assertNotIn("The Monarch", command_names(s2.state, 1))
        bf = [c["name"] for c in s2.state["players"][0]["zones"]["battlefield"]]
        self.assertNotIn("Wayward Swordtooth", bf)
        self.assertEqual(bf.count("Forest"), 10)
        # without the marks (what every save was before this round) the three are gone
        bare = os.path.join(tmp, "bare.state")
        with open(bare, "w", encoding="utf-8") as f:
            f.write("".join(ln for ln in read(nosword).splitlines(True) if not ln.startswith("#manticore")))
        s3 = self.checker()
        before = s3.setups_done
        s3.load_state(bare)
        self.assertTrue(wait(s3, lambda: s3.setups_done > before or s3.state_load_failed))
        self.assertTrue(wait(s3, lambda: s3.state.get("turn") == 5))
        time.sleep(1.0)
        s3.poll()
        for i in (0, 1):
            for name in ("The Monarch", "The Initiative", "City's Blessing"):
                self.assertNotIn(name, command_names(s3.state, i))

    def test_the_restored_monarch_still_draws_at_the_end_step(self):
        """Not only the card's name: the restored Monarch is the real effect. Passing priority through p0's turn 5 from the
        restored board, p0's hand goes from 2 to 3 at its end step (The Monarch's draw)."""
        import card_check as cc
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "m.state")
        lines = [ln for ln in BOARD if not ln.startswith("p1precast")] + []
        state_text = "\n".join(ln for ln in lines if not ln.startswith("p0precast")) + "\n#manticore monarch=p0|\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(state_text)
        s = self.checker()
        before = s.setups_done
        s.load_state(path)
        self.assertTrue(wait(s, lambda: s.setups_done > before or s.state_load_failed))
        self.assertIsNone(s.state_load_failed)
        self.assertTrue(wait(s, lambda: s.state.get("turn") == 5 and "The Monarch" in command_names(s.state, 0)))
        hand = lambda: len(s.state["players"][0]["zones"]["hand"])
        self.assertEqual(hand(), 2)
        end = time.time() + 90
        mem = {}
        while time.time() < end and s.state.get("turn") == 5:
            s.poll()
            if s.requests:
                req = s.requests[0]
                s.answer(req, cc.request_answer(req, {}))
            elif s.state.get("asking") and ((s.state.get("prompt") or {}).get("ok") or {}).get("enabled"):
                s.ok()
                time.sleep(0.3)
            time.sleep(0.05)
        self.assertNotEqual(s.state.get("turn"), 5, "turn 5 never ended")
        s.poll()
        self.assertEqual(hand(), 3, "The Monarch did not draw at the end step")

    def test_a_save_with_fewer_players_than_the_game_is_refused_and_says_so(self):
        """LAST1's live finding the other way round: a 4-player save into a 2-player game. Nothing is applied."""
        s = self.checker()
        before_turn = s.state.get("turn")
        before = s.setups_done
        s.load_state(FOUR)
        self.assertTrue(wait(s, lambda: s.state_load_failed or s.setups_done > before), "no answer")
        self.assertEqual(s.setups_done, before, "an older bridge's setup_done")
        self.assertIn("4 player(s), this game 2", s.state_load_failed)
        self.assertTrue(s.state_load_untouched)
        s.poll()
        self.assertEqual(s.state.get("turn"), before_turn)
        self.assertEqual(s.state["players"][1]["life"], 40)

    def test_a_failed_dev_setup_still_says_done_with_ok_false(self):
        s = self.checker()
        self.setup_board(s, ["p0life=33", "p3life=20"])          # a 4th player in a 2-player game: Forge refuses
        self.assertEqual(len(s.setup_failures), 1)
        self.assertIn("Non-matching number of players", s.setup_failures[0])
        self.assertIsNone(s.state_load_failed)


if __name__ == "__main__":
    unittest.main()
