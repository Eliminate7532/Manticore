# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 21: every game is written to saves/current_game.jsonl as it is played, and an unfinished game can be resumed from the deck screen."""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame

import forge_client as fc
import forge_table as ft
import journal
import tests.live as live
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_deck_screen import FakeLauncher, TempDecks
from tests.test_forge_table import frame


class JournalModuleTests(unittest.TestCase):
    def test_write_resume_finish_and_keep_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = journal.GameJournal(tmp)
            j.start(123, "Karl", {"player.dck": "[metadata]\n"}, "abcd1234", 100.0)
            j.command({"c": "ok"}, 101.0)
            j.command({"c": "card", "id": 5}, 102.5)
            start, cmds = journal.unfinished(tmp)
            self.assertEqual(start["seed"], 123)
            self.assertEqual(cmds, [{"c": "ok"}, {"c": "card", "id": 5}])
            with open(j.path, "a", encoding="utf-8") as f:
                f.write('{"t":"cmd","i":2,"dt":3.0,"c":{"c":"o')          # a crash in the middle of a line
            self.assertEqual(len(journal.unfinished(tmp)[1]), 2)
            j.end("won", 110.0)
            self.assertIsNone(journal.unfinished(tmp))
            for k in range(12):
                j.start(k, "Karl", {}, "x", 200.0 + k)
                j.command({"c": "ok"}, 200.5 + k)
                j.end("lost", 201.0 + k)
            self.assertEqual(len(os.listdir(os.path.join(tmp, "finished"))), journal.KEEP)

    def test_reopen_keeps_appending(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = journal.GameJournal(tmp)
            j.start(5, "Karl", {"player.dck": "x"}, "c", 1.0)
            j.command({"c": "ok"}, 2.0)
            j.close()
            j2 = journal.GameJournal(tmp)
            j2.reopen(1, 1.0)
            j2.command({"c": "cancel"}, 3.0)
            j2.close()                                     # Windows cannot delete the folder while the file is open
            _start, cmds = journal.unfinished(tmp)
            self.assertEqual(cmds, [{"c": "ok"}, {"c": "cancel"}])


def table_with_journal(tmp, state="main1_start"):
    gui = ft.ForgeTable(FakeSession(load_state(state), load_log()), StubStore(), window_size=(1360, 840),
                        saves_dir=os.path.join(tmp, "saves"))
    frame(gui, 2)
    return gui


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def decks(self):
        a = os.path.join(self.tmp, "p.dck")
        b = os.path.join(self.tmp, "o.dck")
        for path, text in ((a, "[metadata]\nName=Me\n"), (b, "[metadata]\nName=AI\n")):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        return a, b

    def test_start_journal_saves_seed_and_deck_files(self):
        gui = table_with_journal(self.tmp)
        a, b = self.decks()
        gui.session.deck_path, gui.session.opponent_paths, gui.session.seed = a, [b], 77
        gui.start_journal()
        gui.session.on_send({"c": "ok"})
        start, cmds = journal.unfinished(os.path.join(self.tmp, "saves"))
        self.assertEqual(start["seed"], 77)
        self.assertEqual(set(start["decks"]), {"player.dck", "opponent1.dck"})
        self.assertIn("Name=Me", start["decks"]["player.dck"])
        self.assertEqual(cmds, [{"c": "ok"}])

    def test_quit_is_not_journalled_and_replies_are(self):
        gui = table_with_journal(self.tmp)
        a, b = self.decks()
        gui.session.deck_path, gui.session.opponent_paths = a, [b]
        gui.start_journal()
        gui.session.on_send({"c": "reply", "id": 3, "value": [0]})
        gui.session.on_send({"c": "quit"})
        _s, cmds = journal.unfinished(os.path.join(self.tmp, "saves"))
        self.assertEqual(cmds, [{"c": "reply", "id": 3, "value": [0]}])

    def test_game_over_finishes_the_journal(self):
        gui = table_with_journal(self.tmp)
        a, b = self.decks()
        gui.session.deck_path, gui.session.opponent_paths = a, [b]
        gui.start_journal()
        gui.session.on_send({"c": "ok"})
        st = copy.deepcopy(gui.state)
        st["gameOver"] = True
        st["winner"] = gui.session.me()["name"]
        gui.session.inbox.put(st)
        frame(gui, 2)
        self.assertIsNone(journal.unfinished(os.path.join(self.tmp, "saves")))
        done = os.listdir(os.path.join(self.tmp, "saves", "finished"))
        self.assertTrue(any(n.endswith("_won.jsonl") for n in done), done)

    def test_concede_finishes_the_journal(self):
        gui = table_with_journal(self.tmp)
        a, b = self.decks()
        gui.session.deck_path, gui.session.opponent_paths = a, [b]
        gui.start_journal()
        gui.session.on_send({"c": "ok"})
        gui.concede()
        done = os.listdir(os.path.join(self.tmp, "saves", "finished"))
        self.assertTrue(any(n.endswith("_conceded.jsonl") for n in done), done)

    def test_starting_a_new_game_closes_the_old_file_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = journal.GameJournal(tmp)
            j.start(1, "Karl", {"player.dck": "x"}, "c", 1.0)
            old = j.f
            j.start(2, "Karl", {"player.dck": "x"}, "c", 2.0)
            self.assertTrue(old.closed, "an open file cannot be moved on Windows")
            self.assertEqual(len(os.listdir(os.path.join(tmp, "finished"))), 1)
            j.close()

    def test_no_journal_without_a_saves_folder(self):
        gui = ft.ForgeTable(FakeSession(load_state("main1_start"), load_log()), StubStore(), window_size=(1360, 840))
        self.assertIsNone(gui.journal)
        gui.start_journal()                                           # harmless
        self.assertIsNone(gui.unfinished_game())


class ResumeScreenTests(TempDecks):
    @staticmethod
    def replay_key():
        return {"forge": ft.version.forge_build(), "bridge": fc.bridge_stamp()}

    def gui_with_unfinished(self, size=(1360, 840), scale=None):
        saves = os.path.join(self.tmp, "saves")
        j = journal.GameJournal(saves)
        j.start(9, "Karl", {"player.dck": "[metadata]\nName=Me\n", "opponent1.dck": "[metadata]\nName=AI\n"}, "c", time.time(),
                replay=self.replay_key())                     # round 28d: a saved game from this very copy
        for _ in range(3):
            j.command({"c": "ok"}, time.time())
        j.close()
        self.launcher = FakeLauncher()
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=size, launcher=self.launcher, deck_dirs=self.dirs,
                            saves_dir=saves)
        if scale is not None:
            gui.text_scale = scale
        gui.open_menu()
        frame(gui, 2)
        return gui

    def test_the_resume_button_shows_and_fits(self):
        for size, scale in (((1024, 640), 1.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((4096, 2019), 1.75), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = self.gui_with_unfinished(size, scale)
                names = {name: rect for rect, name in gui.menu.btns}
                self.assertIn("resume", names)
                self.assertTrue(pygame.Rect(0, 0, *size).contains(names["resume"]))
                self.assertFalse(names["resume"].colliderect(names["start"]))

    def test_no_resume_button_without_an_unfinished_game(self):
        self.launcher = FakeLauncher()
        gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), launcher=self.launcher, deck_dirs=self.dirs,
                            saves_dir=os.path.join(self.tmp, "saves"))
        gui.open_menu()
        frame(gui, 2)
        self.assertNotIn("resume", [n for _r, n in gui.menu.btns])

    def test_resume_plays_the_commands_back_without_the_table_polling(self):
        gui = self.gui_with_unfinished()
        polls = []

        class Session(FakeSession):
            def __init__(self, *a, **k):
                super().__init__(load_state("main1_start"), load_log())
                self.seed = k.get("seed")

            def start(self):
                return self

            def poll(self, limit=200):
                polls.append(1)
                return 0

        played = []

        class Replayer:
            def __init__(self, session, commands, progress=None, **k):
                self.commands, self.progress, self.diverged = commands, progress, None

            def run(self, upto=None):
                for i, c in enumerate(self.commands):
                    played.append(c)
                    self.progress(i + 1, len(self.commands))
                    time.sleep(0.05)
                return True
        with mock.patch.object(ft.fc, "ForgeSession", Session), mock.patch("replay.Replayer", Replayer):
            click_point = next(r.center for r, n in gui.menu.btns if n == "resume")
            gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=click_point, button=1))
            self.assertIsNotNone(gui.resuming)
            self.assertIsNone(gui.menu)
            self.assertEqual(gui.session.seed, 9)
            polls.clear()
            t0 = time.time()
            while gui.resuming is not None and time.time() - t0 < 5:
                frame(gui, 1)                                          # draws the progress screen
                if gui.resuming is not None:
                    self.assertEqual(polls, [], "the table polled the engine while the resume worker owned it")
                time.sleep(0.02)
            self.assertIsNone(gui.resuming)
            self.assertEqual(played, [{"c": "ok"}] * 3)
            # new commands go into the same journal again, after the three played back
            gui.session.on_send({"c": "cancel"})
            _s, cmds = journal.unfinished(os.path.join(self.tmp, "saves"))
            self.assertEqual(cmds, [{"c": "ok"}] * 3 + [{"c": "cancel"}])

    def test_esc_cancels_a_resume(self):
        gui = self.gui_with_unfinished()

        class Session(FakeSession):
            def __init__(self, *a, **k):
                super().__init__(load_state("main1_start"), load_log())

            def start(self):
                return self

        class Replayer:
            def __init__(self, session, commands, progress=None, **k):
                self.commands, self.progress, self.diverged = commands, progress, None

            def run(self, upto=None):
                for i in range(1000):
                    self.progress(i, 1000)
                    time.sleep(0.01)
                return True
        with mock.patch.object(ft.fc, "ForgeSession", Session), mock.patch("replay.Replayer", Replayer):
            gui.resume_last_game()
            gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode=""))
            t0 = time.time()
            while gui.resuming is not None and time.time() - t0 < 5:
                frame(gui, 1)
                time.sleep(0.02)
            self.assertIsNone(gui.resuming)
            self.assertIsNotNone(gui.menu, "back on the deck screen after cancelling")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveResumeTests(unittest.TestCase):
    """A real game: play a few actions with a journal, drop the engine as a crash would, resume, and compare the boards."""

    def test_a_resumed_game_matches_the_one_that_was_left(self):
        import replay
        from deck_loader import load_deck
        from tests.forge_bot import Bot
        tmp = tempfile.mkdtemp(prefix="resume_live_")
        self.addCleanup(shutil.rmtree, tmp, True)
        c, d = load_deck(os.path.join(ROOT, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt"))
        me = fc.write_deck_file(os.path.join(tmp, "player.dck"), c, d, "Player")
        opp = fc.write_deck_file(os.path.join(tmp, "opponent1.dck"), c, d, "Opponent 1")
        s = fc.ForgeSession(me, [opp], name="Karl", seed=4242).start()
        s.stderr_path = os.path.join(tmp, "e1.log")
        j = journal.GameJournal(os.path.join(tmp, "saves"))
        with open(me, encoding="utf-8") as f1, open(opp, encoding="utf-8") as f2:
            j.start(4242, "Karl", {"player.dck": f1.read(), "opponent1.dck": f2.read()}, "test", time.time())
        s.on_send = lambda cmd: cmd.get("c") != "quit" and j.command(cmd, time.time())
        bot = Bot(s)
        t0 = time.time()
        # A patient player: one action, then wait until the engine has answered (a new snapshot that has stayed put for 0.3 s, or
        # 1.5 s at most) before the next. A player who clicks again before the screen has changed can send a command that Forge
        # applies or drops depending on timing; that case needs the bridge's input numbers (Round 22) and is tested there.
        acted_at, acted_version = 0.0, -1
        while time.time() - t0 < 90 and len(s.sent_all) < 14:
            s.poll()
            quiet = time.time() - getattr(s, "_last_state_at", 0.0) > 0.3
            if (s.state_version != acted_version and quiet) or time.time() - acted_at > 1.5:
                n = len(s.sent_all)
                bot.step()
                if len(s.sent_all) != n:
                    acted_at, acted_version = time.time(), s.state_version
            if s.state_version != getattr(s, "_seen_version", -1):
                s._seen_version, s._last_state_at = s.state_version, time.time()
            time.sleep(0.03)
        r = replay.Replayer(s, [])
        r.settled()
        before = replay.summary(s.state)
        s.close()                                                    # "the program crashed"
        start, cmds = journal.unfinished(os.path.join(tmp, "saves"))
        self.assertGreaterEqual(len(cmds), 5)
        s2 = fc.ForgeSession(me, [opp], name="Karl", seed=start["seed"]).start()
        s2.stderr_path = os.path.join(tmp, "e2.log")
        try:
            rp = replay.Replayer(s2, cmds)
            self.assertTrue(rp.run(), rp.diverged)
            after = replay.summary(s2.state)
        finally:
            s2.close()
        self.assertEqual(replay.differences(before, after), [])


if __name__ == "__main__":
    unittest.main()
