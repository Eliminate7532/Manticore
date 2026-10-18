# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ANS1: two of Karl's answers of 13 Oct.

Q8 "Yes, every offline game": the end dialog offers Play again (the same decks, a fresh shuffle; Enter) after every game that can be
restarted, not only after a draft game (Round CD4). Edit deck stays a draft game's. Online games are unchanged.
Q6c "Local zip only": cube drafts (saves/drafts/<id>/) stay out of git; the local backup zip still keeps them.

  EndTests     who gets Play again, what Enter does, the pod path (knocked out first, then the game ends), the fit at many sizes
  DraftsTests  .gitignore leaves saves/drafts/ out and keeps the decks a draft makes; the local zip takes the drafts
"""
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import pygame

import backup
import forge_dialogs as dlg
import forge_table as ft
from tests.forge_fake import load_state
from tests.test_deck_screen import FakeLauncher
from tests.test_forge_table import frame, make_gui

DECKS = ((["Kinnan, Bonder Prodigy"], ["Island"] * 99), [(["Lathril, Blade of the Elves"], ["Forest"] * 99)])


def offline(gui, draft=None):
    """A game started from the deck screen: a launcher and the decks Restart plays again."""
    gui.launcher = FakeLauncher()
    gui.current_decks = copy.deepcopy(DECKS)
    gui.current_draft = draft
    return gui


def labels(gui):
    return [lbl for lbl, _cb in gui.modal.extra]


def over(state="main1_start", winner=None):
    st = copy.deepcopy(load_state(state) if isinstance(state, str) else state)
    me = [p for p in st["players"] if p["id"] == st["me"]][0]
    st["gameOver"], st["winner"] = True, winner or me["name"]
    return st


class EndTests(unittest.TestCase):
    def test_an_ordinary_offline_game_offers_play_again(self):
        gui = offline(make_gui(over()))
        gui.end_continue()
        self.assertIsInstance(gui.modal, dlg.GameOverDialog)
        self.assertEqual(labels(gui), ["Play again"])
        frame(gui, 2)
        self.assertEqual([n for _r, n in gui.modal.buttons], ["extra_0", "new", "close"])

    def test_a_draft_game_still_offers_play_again_and_edit_deck(self):
        gui = offline(make_gui(over()), draft={"pod": "x", "path": "y"})
        gui.end_continue()
        self.assertEqual(labels(gui), ["Play again", "Edit deck"])

    def test_enter_plays_again_and_the_same_decks_are_asked_for(self):
        gui = offline(make_gui(over()))
        gui.end_continue()
        frame(gui, 2)
        with mock.patch.object(gui, "begin", return_value=None) as begin:
            gui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0))
        begin.assert_called_once()
        self.assertEqual(begin.call_args[0][:2], DECKS)

    def test_new_game_still_goes_to_the_deck_screen(self):
        gui = offline(make_gui(over()))
        with mock.patch.object(gui, "open_menu") as menu, mock.patch.object(gui, "restart_game") as again:
            gui.end_continue()                        # the dialog keeps the callbacks it was made with
            frame(gui, 2)
            r = [r for r, n in gui.modal.buttons if n == "new"][0]
            gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=r.center, button=1))
        menu.assert_called_once_with()
        again.assert_not_called()

    def test_no_play_again_online_resumed_or_without_the_deck_screen(self):
        gui = offline(make_gui(over()))
        gui.session.online = "guest"
        gui.end_continue()
        self.assertEqual(labels(gui), [])
        gui = offline(make_gui(over()))
        gui.current_decks = None                     # a resumed game, or one started from the command line
        gui.end_continue()
        self.assertEqual(labels(gui), [])
        gui = make_gui(over())                       # no launcher at all
        gui.end_continue()
        self.assertEqual(labels(gui), [])
        frame(gui, 2)
        self.assertEqual([n for _r, n in gui.modal.buttons], ["new", "close"])     # as before ANS1

    def test_knocked_out_in_a_pod_then_the_game_ends_offers_play_again_too(self):
        st = copy.deepcopy(load_state("declare_blockers"))
        base = [p for p in st["players"] if p["id"] != st["me"]][0]
        p3 = copy.deepcopy(base)
        p3["id"], p3["name"] = 11, "AI 2"
        for z in p3["zones"].values():
            for c in z:
                c["id"] += 1000
        st["players"].append(p3)
        [p for p in st["players"] if p["id"] == st["me"]][0]["lost"] = True
        gui = offline(make_gui(st, (1440, 900)), draft={"pod": "x", "path": "y"})
        self.assertTrue(gui.shown_out)
        gui.press("end_watch")
        frame(gui, 2)
        gui.session.handle(over(st, winner="AI 2"))
        frame(gui, 2)
        self.assertIsInstance(gui.modal, dlg.GameOverDialog)
        self.assertEqual(labels(gui), ["Play again", "Edit deck"])

    def test_the_buttons_fit_and_do_not_overlap_at_every_size(self):
        for size in ((900, 600), (1100, 700), (1280, 720), (1360, 840), (1920, 1080), (4096, 1949)):
            for scale in (1.0, 2.0):
                for draft in (None, {"pod": "x", "path": "y"}):
                    gui = offline(make_gui(over(), size, scale=scale), draft=draft)
                    gui.end_continue()
                    frame(gui, 2)
                    W, H = gui.screen.get_size()
                    rects = [(r, n) for r, n in gui.modal.buttons]
                    self.assertEqual(len(rects), 3 if draft is None else 4, (size, scale))
                    for r, n in rects:
                        self.assertTrue(pygame.Rect(0, 0, W, H).contains(r), (size, scale, n, r))
                    for a in range(len(rects)):
                        for b in range(a + 1, len(rects)):
                            self.assertFalse(rects[a][0].colliderect(rects[b][0]), (size, scale, rects[a][1], rects[b][1]))


def git_ok():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


class DraftsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ans1_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @unittest.skipUnless(git_ok(), "git is not installed")
    def test_drafts_are_left_out_of_git_and_their_decks_are_not(self):
        repo = os.path.join(self.tmp, "rules")
        os.makedirs(repo)
        shutil.copy(os.path.join(HERE, ".gitignore"), repo)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

        def ignored(path):
            out = subprocess.run(["git", "check-ignore", path], cwd=repo, capture_output=True, text=True)
            return out.stdout.strip() == path

        for path in ("saves/drafts/20261013_0912_ab12/cards.json.gz", "saves/drafts/20261013_0912_ab12/draft.json"):
            self.assertTrue(ignored(path), path)
        for path in ("my_decks/Commander Cube draft - Atraxa.txt", "stats/games.ndjson", "sample_decks/stompy_goreclaw.txt",
                     "draft_pod.py", "stock_cubes/cm.json"):
            self.assertFalse(ignored(path), path)

    def test_the_local_zip_still_takes_the_drafts(self):
        proj = os.path.join(self.tmp, "proj")
        pod = os.path.join(proj, "saves", "drafts", "20261013_0912_ab12")
        os.makedirs(pod)
        for name in ("cards.json.gz", "draft.json"):
            with open(os.path.join(pod, name), "wb") as f:
                f.write(b"x")
        files, left_out = backup.snapshot_files(proj, os.path.join(self.tmp, "zips"))
        names = [rel for rel, _full in files]
        self.assertIn("saves/drafts/20261013_0912_ab12/cards.json.gz", names)
        self.assertIn("saves/drafts/20261013_0912_ab12/draft.json", names)
        self.assertEqual(left_out, [])


if __name__ == "__main__":
    unittest.main()
