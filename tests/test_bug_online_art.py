# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1567727149127831654 (Karl, 4 Nov 2026, 0.28.53.15): "custom card art does not appear in multiplayer".

Round ALT1 gives each seat the printings (and imported / MPC Autofill pictures) its deck file names, through
ForgeTable.set_seat_printings, but only begin() and Resume called it: hosting a game online (host_game), joining one
(begin_online_game) and continuing a saved online game (_host_saved_game) never did, so an online table drew every card with
Scryfall's default picture - or with the printings of the last offline game, which reset_game doesn't clear. Now my own seat
gets my deck's printings on my table when I host or join; the other seats keep the default (their art files are on their PCs).
"""
import os
import shutil
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_data as cdata
import forge_client as fc
import forge_net as fn
import tests.test_deck_screen as tds
from tests.test_mp1 import FakeHostSession

KINNAN = "kinnan, bonder prodigy"           # the sample deck's line: "1 Kinnan, Bonder Prodigy (IKO) 338"


class OnlineArtTests(tds.TempDecks):
    def setUp(self):
        super().setUp()
        self.cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)

    def host(self, gui):
        with mock.patch.object(fn, "ensure_host_cert", return_value=self.cert), \
                mock.patch.object(fc, "HostSession", side_effect=lambda *a, **k: FakeHostSession(*a, **k)):
            return gui.host_game(gui.menu.mine, "Karl", 36800, "pw-123", False)

    def mine_is_drawn_with_my_printing(self, gui):
        st = {"me": 1, "players": [{"id": 1}, {"id": 2}]}
        with mock.patch.object(type(gui), "state", new_callable=mock.PropertyMock, return_value=st), \
                mock.patch.object(gui, "art_name", side_effect=lambda c: c.get("name")):
            mine = gui.art_key({"name": "Kinnan, Bonder Prodigy", "controller": 1})
            theirs = gui.art_key({"name": "Kinnan, Bonder Prodigy", "controller": 2})
        self.assertEqual(mine, cdata.art_key("Kinnan, Bonder Prodigy", "iko", "338"))
        self.assertEqual(theirs, "Kinnan, Bonder Prodigy")          # the other seat: Scryfall's default picture

    def test_the_host_sees_his_own_decks_printings(self):
        gui = self.idle_gui()
        self.assertIn(KINNAN, gui.menu.mine.printings)
        self.assertIsNone(self.host(gui))
        self.assertEqual(len(gui.seat_printings), 1)
        self.assertEqual(gui.seat_printings[0][KINNAN], ("iko", "338"))
        self.mine_is_drawn_with_my_printing(gui)

    def test_the_guest_sees_his_own_decks_printings(self):
        gui = self.idle_gui()
        entry = gui.menu.mine
        entry.load()
        session = mock.Mock(deck_entry=entry, spectator=False, fmt="commander", peer_name="Karl", peer_code=None,
                            draft=False, room="r1")
        gui.begin_online_game(session)
        self.assertEqual(gui.seat_printings[0][KINNAN], ("iko", "338"))
        self.mine_is_drawn_with_my_printing(gui)

    def test_an_offline_games_printings_dont_follow_into_an_online_game(self):
        gui = self.idle_gui()
        gui.set_seat_printings([{"sol ring": ("c21", "263")}, {"sol ring": ("lea", "270")}])
        session = mock.Mock(deck_entry=None, spectator=True, fmt="commander", peer_name="Karl", peer_code=None,
                            draft=False, room="r1", watched_guest="Sam")
        gui.begin_online_game(session)
        self.assertEqual(gui.seat_printings, [])


if __name__ == "__main__":
    unittest.main()
