# SPDX-License-Identifier: GPL-3.0-or-later
"""Round LAST1b (needs pygame for the table tests): what LAST1's first live run found (12 Oct 2026, real Forge fb4d809).

- Forge's saved game (GameState) holds only the players still in the game. A save taken after a knock-out in a 4-player game had
  p0 and p1 only, and loading it into a new 4-player game failed in the bridge ("bridge: setup failed: java.lang.RuntimeException:
  Non-matching number of players, (4 vs. 2)") while the bridge still sent setup_done - so the table said "Restored to turn 44."
  over a fresh game. Now: no save once a player is out, and no Restore offered then (nor with a save that holds fewer players).
- The first save of every game was asked during the mulligans (turn 0, no phase) and failed in the bridge with a
  NullPointerException (PhaseHandler.getPhase() is null), leaving a "bridge: savestate failed" line in every engine log.
  Now: no save before the game's first phase.

The two .state fixtures are real saves from that run (tests/fixtures/last1b/), each zone list cut to its first two cards."""
import copy
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_dialogs as dlg
import journal
import last_good
from last_good import LastGood
from tests.forge_fake import load_state
from tests.test_forge_table import make_gui

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "last1b")
FOUR = os.path.join(FIXTURES, "four_players.state")
TWO_LEFT = os.path.join(FIXTURES, "two_players_left.state")


def table(asking=True, my_turn=False):
    st = copy.deepcopy(load_state("main1_start"))
    me = st["me"]
    other = next(p["id"] for p in st["players"] if p["id"] != me)
    st["activePlayer"] = me if my_turn else other
    st["asking"] = asking
    if asking:
        st["prompt"] = {"message": "Priority: Your turn", "ok": {"enabled": True}, "cancel": {"enabled": False}}
    else:
        st["prompt"] = {"message": "Waiting for AI 1.", "ok": {"enabled": False}, "cancel": {"enabled": False}}
    gui = make_gui(st)
    gui._think_sig = gui._think_since = None          # make_gui drew with the real clock (see tests/test_laststate.py)
    gui.session.last_state_ok = True
    gui.launcher, gui.current_decks = object(), ("a", ["b"])
    return gui, st


def answer_with(gui, content, turn=4):
    """The bridge's answer to the newest savestate, with this file content (as the bridge would have written it)."""
    cmd = gui.session.commands("savestate")[-1]
    with open(cmd["path"], "w", encoding="utf-8") as f:
        f.write(content)
    gui.session.handle({"t": "state_saved", "n": cmd["n"], "ok": True, "turn": turn})
    gui.last_good_tick(gui.last_good.in_flight[3] + 0.5)


def journalled(gui, folder):
    """A game journal on the table, fed the way a real ForgeSession feeds it (send -> on_send)."""
    j = journal.GameJournal(folder)
    j.start(1, "Karl", {"player.dck": "x"}, "code", 0.0)
    gui.journal = j
    gui._hook_session()
    plain = gui.session.send

    def send(**cmd):
        plain(**cmd)
        gui.session.on_send(cmd)
        return True
    gui.session.send = send
    return j


def two_player_save_text():
    """A real-shaped save of a 2-player game: the first two players' lines of the 4-player fixture."""
    lines = [ln for ln in open(FOUR, encoding="utf-8").read().splitlines() if not ln.startswith(("p2", "p3"))]
    return "\n".join(lines) + "\n"


class PlayersInTests(unittest.TestCase):
    def test_a_real_four_player_save_holds_four(self):
        self.assertEqual(last_good.players_in(FOUR), 4)

    def test_a_real_save_after_two_knock_outs_holds_two(self):
        self.assertEqual(last_good.players_in(TWO_LEFT), 2)

    def test_a_file_that_names_no_player_or_none_at_all_is_unknown(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "s.state")
        with open(p, "w") as f:
            f.write("turn=5\n")
        self.assertIsNone(last_good.players_in(p))
        self.assertIsNone(last_good.players_in(os.path.join(d, "missing.state")))


class LatestTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.lg = LastGood(os.path.join(self.dir, "g"))

    def save(self, src, now, sig, turn):
        n, path = self.lg.next_path()
        self.lg.sent(now, n, path, sig)
        shutil.copy(src, path)
        self.assertTrue(self.lg.reply({"n": n, "ok": True, "turn": turn}, now + 0.5))
        return path

    def test_latest_with_players_skips_a_newer_save_that_holds_fewer(self):
        four = self.save(FOUR, 100.0, "A", 10)
        two = self.save(TWO_LEFT, 130.0, "B", 44)
        self.assertEqual(self.lg.latest()["path"], two)                 # unchanged without players
        self.assertEqual(self.lg.latest(players=4)["path"], four)
        self.assertEqual(self.lg.latest(players=2)["path"], two)
        self.assertIsNone(self.lg.latest(players=3))


class TableTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = mock.patch.object(fc, "DATA_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)

    def test_no_save_during_the_mulligans_then_one_once_the_game_has_a_phase(self):
        gui, st = table(asking=True)
        st["turn"], st["phase"] = 0, ""                                 # what the bridge's snapshots say during the mulligans
        gui.last_good_tick(100.0)
        self.assertEqual(gui.session.commands("savestate"), [])
        st["turn"], st["phase"] = 1, "MAIN1"
        gui.last_good_tick(101.0)
        self.assertEqual(len(gui.session.commands("savestate")), 1)

    def test_no_save_once_a_player_is_out(self):
        gui, st = table(asking=True)
        st["players"][1]["lost"] = True
        gui.last_good_tick(100.0)
        self.assertEqual(gui.session.commands("savestate"), [])

    def test_a_save_is_not_written_into_the_game_journal(self):
        """The live UNDO1 rewind tests failed on main 49a4822: every savestate went into the journal, so the count of commands a
        rewind point records was one off, and Resume / Undo's rebuild sent old saves again. A save changes nothing in the game."""
        gui, st = table(asking=True)
        j = journalled(gui, os.path.join(self.dir, "saves"))
        self.addCleanup(j.close)
        gui.last_good_tick(100.0)
        self.assertEqual(len(gui.session.commands("savestate")), 1)      # the save still goes to Forge
        gui.session.send(c="ok")
        self.assertEqual(j.count, 1)
        j.close()
        self.assertEqual([c.get("c") for c in journal.read_full(j.path)["commands"]], ["ok"])

    def stall_offer(self, gui, st):
        st["asking"] = False
        st["prompt"] = {"message": "Waiting for AI 1.", "ok": {"enabled": False}, "cancel": {"enabled": False}}
        gui.watchdog_tick(100.0)
        gui.watchdog_tick(281.0)
        self.assertIsInstance(gui.modal, dlg.OptionsDialog)
        return [o[0] for o in gui.modal.options]

    def test_the_offer_restores_a_save_that_holds_every_player(self):
        gui, st = table(asking=True)
        gui.last_good_tick(10.0)
        answer_with(gui, two_player_save_text(), turn=4)
        self.assertEqual(self.stall_offer(gui, st), ["Restore the last good state (turn 4)", "End this game"])

    def test_after_a_knock_out_the_offer_has_no_restore_and_says_why(self):
        gui, st = table(asking=True)
        gui.last_good_tick(10.0)
        answer_with(gui, two_player_save_text(), turn=4)                # a good save from before the knock-out
        st["players"][1]["lost"] = True
        self.assertEqual(self.stall_offer(gui, st), ["End this game"])
        self.assertIn("already out of this game", gui.modal.text)

    def test_a_save_that_holds_fewer_players_than_the_table_is_never_restored(self):
        gui, st = table(asking=True)
        gui.last_good_tick(10.0)
        answer_with(gui, open(TWO_LEFT, encoding="utf-8").read().replace("p1", "p0"), turn=44)    # one player in the file
        self.assertEqual(last_good.players_in(gui.last_good.latest()["path"]), 1)
        self.assertEqual(self.stall_offer(gui, st), ["End this game"])
        with mock.patch.object(gui, "restart_game") as restart, mock.patch.object(gui, "say") as say:
            gui.restore_last_good()
        restart.assert_not_called()
        self.assertIn("no saved state that can be restored", say.call_args[0][0])
        self.assertIsNone(getattr(gui, "pending_restore", None))


if __name__ == "__main__":
    unittest.main()
