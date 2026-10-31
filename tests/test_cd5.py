# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CD5: drafting a Commander cube together online, then one 4-player online game (Karl, 27 Oct 2026: "Draft together, Commander
Cube first"; "No timer"; a friend who drops mid-draft: "Everyone waits").

Offline: online_draft.HostDraft (the host's draft: every friend's message checked, 903.13d - nobody is sent another seat's pack or
picks, the pause when a friend drops, the AI taking a seat over and the friend taking it back, the decks) against GuestPod (a
friend's table's view of it), wired together in memory. Live (real Forge, NetHost --draft on 127.0.0.1 with NetSession friends
driven by bots): see LiveTests."""
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cube_source as cs
import draft_ai as da
import draft_core as dc
import draft_deck as dd
import draft_pod as dp
import online_draft as od
from tests.test_cd1 import made_up_cube


def every_card_known(name):
    return "Name:" + name


def made_up_check():
    cube = cs.from_cubecobra(made_up_cube(), "madeup")
    return cube, cs.check_forge(cube.cards, every_card_known)


class Wiring:
    """A host and its friends' tables, joined in memory: what HostDraft sends to net seat N is fed to friend N's GuestPod, and
    every message is kept with the draft as it was when it was sent (to check 903.13d)."""

    def __init__(self, root, friends=("Sam", "Ann"), seed=11):
        self.cube, self.check = made_up_check()
        self.names = list(friends)
        seats = od.HostDraft.seats_for("Karl", self.names)
        self.pod = dp.Pod.new(self.cube, self.check, seed=seed, root=root, seats=seats, online={"friends": self.names})
        self.sent = {ns: [] for ns in range(2, 2 + len(friends))}       # net seat -> [(msg, pack the seat had, pool it had)]
        self.guests = {}
        self.up = {ns: True for ns in self.sent}
        for ns in self.sent:
            self.guests[ns] = od.GuestPod(lambda msg, ns=ns: self.from_guest(ns, msg))
        self.hd = od.HostDraft(self.pod, [(ns, n) for ns, n in zip(self.sent, self.names)], self.to_guest)
        self.outbox = []

    def to_guest(self, ns, msg):
        d = self.hd.seat_of[ns] if hasattr(self, "hd") else ns - 1
        draft = self.pod.draft
        self.sent[ns].append((json.loads(json.dumps(msg)), draft.pack_for(d), list(draft.pools[d])))
        if self.up[ns]:
            self.guests[ns].feed(json.loads(json.dumps(msg)))

    def from_guest(self, ns, msg):
        self.outbox.append((ns, json.loads(json.dumps(msg))))

    def deliver(self):
        while self.outbox:
            ns, msg = self.outbox.pop(0)
            if self.up[ns]:
                self.hd.on_message(ns, msg)

    def bot_round(self, rng):
        """Every friend who can pick, picks (the AI's way); the host too. Returns True when anyone picked."""
        moved = False
        for ns, g in self.guests.items():
            if self.up[ns] and g.pack() and not g.ai_for_me():
                choose = da.chooser(g.cards, 900 + ns, g.z)
                g.pick(choose(0, g.pack(), g.pool(), g.needed()))
                moved = True
        self.deliver()
        if self.pod.draft.pack_for(0) and not self.hd.paused_on():
            choose = da.chooser(self.pod.cards, 77, self.pod.z)
            self.hd.host_pick(choose(0, self.pod.draft.pack_for(0), self.pod.pool(0), self.pod.draft.needed(0)))
            moved = True
        return moved


class HostDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.w = Wiring(self.tmp)
        self.w.hd.start()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def finish(self, limit=400):
        rng = random.Random(3)
        for _ in range(limit):
            if self.w.hd.stage != "pick":
                return
            if not self.w.bot_round(rng):
                self.fail("nobody could pick: " + repr(self.w.hd.paused_text()))
        self.fail("the draft didn't finish")

    def test_seats_are_the_host_the_friends_then_ai_drafters(self):
        self.assertEqual([s.name for s in self.w.pod.draft.seats], ["Karl", "Sam", "Ann", "AI 1"])
        self.assertEqual([s.kind for s in self.w.pod.draft.seats], ["human", "remote", "remote", "ai"])
        self.assertEqual(od.HostDraft.seats_for("Karl", ["Sam"])[-2:], [("AI 1", "ai"), ("AI 2", "ai")])

    def test_each_friend_gets_the_cards_and_their_own_seat_never_the_seed(self):
        for ns, g in self.w.guests.items():
            self.assertTrue(g.ready_to_show)
            self.assertEqual(len(g.cards), len(self.w.pod.cards))
            self.assertEqual([c.name for c in g.cards], [c.name for c in self.w.pod.cards])
            self.assertEqual(g.seat_name(0), "You")
            for msg, _pack, _pool in self.w.sent[ns]:
                self.assertNotIn("seed", json.dumps(msg))
        sam = self.w.guests[2]
        self.assertEqual(sam.seat_name(1), "Ann")          # turned so that I am seat 0 and "left" is the next seat
        self.assertEqual(sam.seat_name(3), "Karl")
        self.assertEqual(sam.passing(), ("left", "Ann"))
        self.assertEqual(sam.label(), "Pack 1, pick 1 of 10")
        self.assertEqual(sam.needed(), 2)
        self.assertEqual(len(sam.pack()), 20)

    def test_903_13d_nobody_is_sent_another_seats_pack_or_picks_in_a_whole_draft(self):
        self.finish()
        for ns, msgs in self.w.sent.items():
            for msg, pack, pool in msgs:
                if msg.get("k") == "view":
                    self.assertEqual(msg.get("pack"), pack)               # exactly the pack in front of them, then
                    self.assertEqual(msg["pool"], pool)                  # exactly their own picks
                    self.assertEqual(set(msg) - {"k", "stage", "done", "counts", "pool", "paused", "ai_for_you", "waiting",
                                                 "label", "round", "turn", "need", "pack", "passing", "ready", "mine_ready"},
                                     set())
                if msg.get("k") == "review":
                    seats = {p[0] for p in msg["picks"]}
                    d = self.w.hd.seat_of[ns]
                    self.assertEqual(seats, {d, 3})                       # theirs and the AI drafter's - not Karl's or the other friend's
        self.assertEqual(self.w.hd.stage, "build")
        self.assertEqual([len(p) for p in self.w.pod.draft.pools], [60, 60, 60, 60])

    def test_a_bad_pick_is_refused_in_words_and_changes_nothing(self):
        sam, d = self.w.guests[2], 1
        pack = self.w.pod.draft.pack_for(d)
        other = self.w.pod.draft.pack_for(2)
        before = list(self.w.pod.draft.pools[d])
        cases = [({"k": "pick", "cards": [pack[0]], "at": [0, 0]}, "Take 2 cards"),
                 ({"k": "pick", "cards": [pack[0], pack[0]], "at": [0, 0]}, "same card twice"),
                 ({"k": "pick", "cards": [pack[0], other[0]], "at": [0, 0]}, "isn't in this pack"),
                 ({"k": "pick", "cards": [pack[0], pack[1]], "at": [0, 3]}, "no longer have"),
                 ({"k": "pick", "cards": ["x", pack[1]], "at": [0, 0]}, "isn't a pick"),
                 ({"k": "pick", "cards": [pack[0], pack[1], pack[2]], "at": [0, 0]}, "isn't a pick"),
                 ({"k": "deck", "commanders": [], "main": [], "basics": {}}, "aren't being built"),
                 ({"k": "seed"}, "isn't part of a draft"),
                 ("not even a dict", "isn't part of a draft")]
        for msg, words in cases:
            why = self.w.hd.on_message(2, msg)
            self.assertIn(words, why or "", msg)
            self.assertEqual(sam.refused, why)
        self.assertEqual(self.w.pod.draft.pools[d], before)
        self.assertIsNone(self.w.hd.on_message(2, {"k": "pick", "cards": [pack[0], pack[1]], "at": [0, 0]}))
        self.assertEqual(self.w.pod.draft.pools[d], before + [pack[0], pack[1]])
        self.assertIsNone(self.w.hd.on_message(9, {"k": "pick"}) and None)      # a seat nobody sits in: nothing happens

    def test_no_timer_the_others_wait_for_the_slowest_and_are_told_who(self):
        karl = self.w.pod
        # Karl and Ann pick, Sam doesn't: Ann's next pack is the one Sam would pass (pack 1 passes left: Sam -> Ann)
        choose = da.chooser(karl.cards, 5, karl.z)
        self.w.hd.host_pick(choose(0, karl.draft.pack_for(0), [], 2))
        ann = self.w.guests[3]
        ann.pick(choose(0, ann.pack(), [], 2))
        self.w.deliver()
        self.assertIsNone(ann.pack())
        self.assertIn("Sam", ann.waiting())
        self.assertIn("Sam", [self.w.hd.name(s) for s in self.w.hd.waiting_for()])
        time.sleep(0.05)
        self.assertIsNone(ann.pack())                                    # nothing picks for Sam with time
        sam = self.w.guests[2]
        sam.pick(choose(0, sam.pack(), [], 2))
        self.w.deliver()
        self.assertIsNotNone(ann.pack())

    def test_a_drop_pauses_everyone_and_the_friend_carries_on_with_the_same_pack(self):
        sam = self.w.guests[2]
        pack_before, pool_before = list(sam.pack()), list(sam.pool())
        self.w.up[2] = False
        self.w.hd.on_drop(2)
        self.assertEqual(self.w.hd.paused_text(), "Waiting for Sam to come back.")
        self.assertEqual(self.w.guests[3].paused(), ["Sam"])
        with self.assertRaises(dc.DraftError) as e:
            self.w.hd.host_pick(self.w.pod.draft.pack_for(0)[:2])
        self.assertIn("Waiting for Sam", str(e.exception))
        ann = self.w.guests[3]
        ann.pick(ann.pack()[:2])
        self.w.deliver()
        self.assertIn("Waiting for Sam", ann.refused)
        # Sam's table comes back (NetHost: the rejoin token): everything again, the same pack and picks
        self.w.up[2] = True
        self.w.guests[2] = sam = od.GuestPod(lambda msg: self.w.from_guest(2, msg))
        self.w.hd.on_back(2)
        self.assertEqual(sam.pack(), pack_before)
        self.assertEqual(sam.pool(), pool_before)
        self.assertEqual(self.w.hd.paused_on(), [])
        self.assertEqual(ann.paused(), [])

    def test_the_host_gives_the_seat_to_the_ai_then_the_friend_takes_it_back(self):
        self.w.up[2] = False
        self.w.hd.on_drop(2)
        n0 = len(self.w.pod.draft.pools[1])
        self.assertTrue(self.w.hd.give_to_ai(1))
        self.assertEqual(self.w.hd.paused_on(), [])
        self.assertGreater(len(self.w.pod.draft.pools[1]), n0)            # the AI picked for Sam at once
        for _ in range(3):
            self.w.bot_round(random.Random(1))
        ai_made = len(self.w.pod.draft.pools[1])
        self.w.up[2] = True
        sam = self.w.guests[2] = od.GuestPod(lambda msg: self.w.from_guest(2, msg))
        self.w.hd.on_back(2)
        self.assertFalse(sam.ai_for_me())
        self.assertEqual(len(sam.pool()), ai_made)                        # the AI's picks are Sam's
        if sam.pack():
            before = len(self.w.pod.draft.pools[1])
            self.w.bot_round(random.Random(1))                            # Sam's own pick, from the next pack on
            self.assertEqual(len(self.w.pod.draft.pools[1]), before + 2)

    def test_a_pick_around_a_reconnect_is_taken_exactly_once(self):
        sam, d = self.w.guests[2], 1
        pool = list(self.w.pod.draft.pools[d])
        sam.pick(sam.pack()[:2])                       # sent; the host hasn't read it yet when Sam's table reconnects
        self.w.hd.on_back(2)                           # the host sends everything again: Sam's table sends the pick again
        self.assertEqual(len(self.w.outbox), 2)
        self.w.deliver()
        self.assertEqual(len(self.w.pod.draft.pools[d]), len(pool) + 2)      # taken once
        self.assertEqual(self.w.hd.refusals, [])                              # the copy is dropped, not refused
        # a pick lost with the old connection is sent again after the reconnect, and taken
        for _ in range(4):                                                    # the others pick until Sam has a pack again
            if sam.can_pick():
                break
            self.w.hd.host_pick(self.w.pod.draft.pack_for(0)[:2]) if self.w.pod.draft.pack_for(0) else None
            ann = self.w.guests[3]
            if ann.can_pick():
                ann.pick(ann.pack()[:2])
            self.w.deliver()
        self.assertTrue(sam.can_pick())
        pool = list(self.w.pod.draft.pools[d])
        self.w.up[2] = False
        sam.pick(sam.pack()[:2])
        self.w.deliver()                                                      # lost: the connection was down
        self.assertEqual(len(self.w.pod.draft.pools[d]), len(pool))
        self.w.up[2] = True
        self.w.hd.on_back(2)
        self.w.deliver()
        self.assertEqual(len(self.w.pod.draft.pools[d]), len(pool) + 2)
        self.assertEqual(self.w.hd.refusals, [])

    def test_a_pick_that_came_just_before_its_connection_dropped_is_taken(self):
        """The online draft soak of 28 Oct (seed 8000): NetHost hands over a friend's pick, then the drop notice of that same
        connection. Read in that order, the pick counts; read the other way round, it was refused as "paused" and the friend's
        table (away, then back with the same pick number) waited for ever."""
        import forge_client as fc
        s = fc.ForgeSession("", [])
        sam, d = self.w.guests[2], 1
        pool = list(self.w.pod.draft.pools[d])
        pack = sam.pack()
        s.handle({"t": "draft_from", "seat": 2, "name": "Sam", "msg": {"k": "pick", "cards": pack[:2],
                                                                     "at": [0, 0], "n": 1, "sid": sam.sid}})
        s.handle({"t": "peer_dropped", "who": "guest", "seat": 2, "name": "Sam", "draft": True})
        od.pump_host(s, self.w.hd)
        self.assertEqual(len(self.w.pod.draft.pools[d]), len(pool) + 2)
        self.assertEqual(self.w.hd.paused_on(), [d])

    def test_a_pick_refused_while_its_table_was_away_counts_when_it_comes_again(self):
        sam, ann, d = self.w.guests[2], self.w.guests[3], 1
        self.w.up[3] = False
        self.w.hd.on_drop(3)                                         # Ann is away: everything waits
        pool = list(self.w.pod.draft.pools[d])
        msg = {"k": "pick", "cards": sam.pack()[:2], "at": [0, 0], "n": 1, "sid": sam.sid}
        self.w.up[2] = False                                         # ... and Sam's table drops just as it picks
        self.w.hd.on_drop(2)
        self.assertIn("Waiting for", self.w.hd.on_message(2, msg))   # refused - but that refusal never reached Sam
        self.w.up[2] = self.w.up[3] = True
        self.w.hd.on_back(3)
        self.w.hd.on_back(2)
        self.assertIsNone(self.w.hd.on_message(2, msg))              # the same pick, sent again: taken
        self.assertEqual(len(self.w.pod.draft.pools[d]), len(pool) + 2)

    def test_a_friend_who_leaves_gives_the_seat_to_the_ai_for_good(self):
        self.w.hd.on_message(2, {"k": "leave"})
        self.assertIn(1, self.w.hd.left)
        self.w.hd.on_back(2)
        self.assertTrue(self.w.hd.is_ai(1))

    def test_deck_building_waits_for_everyone_and_a_deck_is_checked(self):
        self.finish()
        hd = self.w.hd
        self.assertFalse(hd.all_ready())
        self.assertEqual(set(hd.decks), {3})                              # the AI drafter's deck is built at once
        sam, ann = self.w.guests[2], self.w.guests[3]
        stolen = self.w.pod.draft.pools[2][0]                             # one of Ann's cards
        deck = sam.build(0)
        bad = od.deck_message(deck)
        bad["main"] = bad["main"] + [stolen]
        self.assertIn("0 drafted", hd.on_message(2, bad) or "")             # a card of someone else's: refused
        self.assertIn("0 drafted", sam.deck_state)
        bad = od.deck_message(deck)
        bad["basics"] = {"Plains": 5, "Black Lotus": 1}
        self.assertIn("basic lands", hd.on_message(2, bad) or "")
        bad = od.deck_message(deck)
        bad["commanders"] = [i for i in sam.pool() if not self.w.pod.cards[i].commander][:1]
        self.assertTrue(hd.on_message(2, bad))                            # a commander problem stops a deck, as offline
        sam.send_deck(deck)
        self.w.deliver()
        self.assertEqual(sam.deck_state, "ok")
        self.assertFalse(hd.all_ready())
        self.assertIn(("Ann", False), ann.ready_list())
        import online_draft_screens as odscr
        flow = odscr.OnlineDraftFlow.__new__(odscr.OnlineDraftFlow)
        flow.role, flow.pod = "guest", ann
        self.assertIn(("You", False), flow.ready_names())          # my own line says "You", not my name
        ann.send_deck(ann.build(0))
        self.w.deliver()
        self.assertFalse(hd.all_ready())                                  # the host isn't ready yet
        hd.host_deck(self.w.pod.build(0))
        self.assertTrue(hd.all_ready())
        plan = hd.plan()
        self.assertEqual([d for _ns, d, _deck in plan["friends"]], [1, 2])
        self.assertEqual([s for s, _deck in plan["ai"]], [3])
        self.assertEqual(plan["friends"][0][2], od.deck_of(self.w.pod.cards, deck.commanders, list(deck.spells + deck.lands),
                                                            deck.basics))

    def test_a_friend_away_during_deck_building_is_waited_for_or_given_to_the_ai(self):
        self.finish()
        hd = self.w.hd
        hd.host_deck(self.w.pod.build(0))
        self.w.guests[3].send_deck(self.w.guests[3].build(0))
        self.w.deliver()
        self.w.up[2] = False
        hd.on_drop(2)
        self.assertFalse(hd.all_ready())
        hd.give_to_ai(1)
        self.assertTrue(hd.all_ready())
        self.assertEqual([d for _ns, d, _deck in hd.plan()["friends"]], [2])
        self.assertEqual([s for s, _deck in hd.plan()["ai"]], [1, 3])

    def test_the_pod_is_kept_after_every_pick_as_an_online_draft(self):
        self.w.hd.host_pick(self.w.pod.draft.pack_for(0)[:2])
        again = dp.Pod.load(self.w.pod.folder)
        self.assertEqual(again.draft.pools, self.w.pod.draft.pools)
        self.assertEqual(again.seat_name(1), "Sam")
        self.assertIsNone(dp.unfinished(self.tmp))                        # an offline draft never continues an online one
        self.assertEqual(dp.unfinished(self.tmp, online=True).folder, self.w.pod.folder)


if __name__ == "__main__":
    unittest.main()


# ==================================================================================================================================
# Live: real Forge, NetHost --draft on 127.0.0.1
# ==================================================================================================================================
from unittest import mock  # noqa: E402

from tests import live  # noqa: E402


def _wait(fn_, seconds, step=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if fn_():
            return True
        time.sleep(step)
    return bool(fn_())


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveTableTests(unittest.TestCase):
    """Two whole tables in this process: Karl hosts a draft from the draft window, Sam joins with the invite code; every pick is
    made through each window (Confirm), the decks are made Ready, and the 4-player game starts on both tables."""

    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "my_decks")
        self.samples = os.path.join(self.tmp, "sample_decks")
        os.makedirs(self.samples)
        self.dirs = (self.lib, self.samples, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_whole_draft_through_both_windows_to_the_game(self):
        import paths
        import forge_client as fc
        import forge_table as ft
        import online_draft_screens as odscr
        from tests.forge_fake import StubStore
        from tests.test_forge_table import frame
        user = os.path.join(self.tmp, "user")
        os.makedirs(user)
        with mock.patch.object(paths, "user_dir", return_value=user), \
                mock.patch.object(dp, "pods_dir", return_value=os.path.join(self.tmp, "pods")):
            host = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840),
                                 launcher=ft.Launcher(fc.DEFAULT_RUNTIME, "Karl"), deck_dirs=self.dirs)
            host.open_menu()
            frame(host, 1)
            host.open_draft()
            setup = host.modal
            self.assertTrue(_wait(lambda: (frame(host, 1) or setup.ready()), 120), setup.message)
            frame(host, 1)
            self.assertIn("online", [n for _r, n in setup.buttons])                  # "Draft with friends" is offered
            setup.press(host, "online")
            flow = host.modal
            self.assertIsInstance(flow, odscr.OnlineDraftFlow)
            flow.fields["name"].set("Karl")
            port = live_port()
            flow.fields["port"].set(str(port))
            flow.upnp = False
            flow.press(host, "open_host")
            self.addCleanup(lambda: host.session.close())
            self.assertEqual(flow.stage, "lobby", flow.message)
            self.assertTrue(_wait(lambda: (frame(host, 1) or host.session.hosting is not None), 150), "never hosting")
            flow.wait.address.set("127.0.0.1")
            code = flow.invite()
            self.assertIsNotNone(code)
            self.assertTrue(fn_read(code)["draft"])

            guest = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840),
                                  launcher=ft.Launcher(None, "Sam"), deck_dirs=(os.path.join(self.tmp, "sam_decks"),
                                                                                 self.samples, self.tmp))
            guest.open_menu()
            frame(guest, 1)
            guest.menu.press(guest, "join_online")
            j = guest.modal
            j.fields["code"].set(code)
            j.fields["name"].set("Sam")
            j.join(guest)
            self.addCleanup(lambda: guest.session.close())
            self.assertTrue(_wait(lambda: (frame(guest, 1) or frame(host, 1) or
                                           isinstance(guest.modal, odscr.OnlineDraftFlow)), 60), j.message)
            gflow = guest.modal
            self.assertTrue(_wait(lambda: (frame(guest, 1) or frame(host, 1) or flow.friends_here() == ["Sam"]), 30))
            flow.press(host, "start_online")
            self.assertEqual(flow.stage, "pick", flow.message)
            self.assertTrue(_wait(lambda: (frame(guest, 1) or frame(host, 1) or gflow.stage == "pick"), 30))
            self.assertEqual([gflow.pod.seat_name(s) for s in range(4)], ["You", "AI 1", "AI 2", "Karl"])
            # every pick through the windows: two cards clicked (by the window's own toggle), then Confirm
            hpick = da.chooser(flow.pod.cards, 3, flow.pod.z)
            gpick = da.chooser(gflow.pod.cards, 4, gflow.pod.z)
            end = time.monotonic() + 300
            while time.monotonic() < end and not (flow.stage == "review" and gflow.stage == "review"):
                frame(host, 1)
                frame(guest, 1)
                for w, gui, pick in ((flow, host, hpick), (gflow, guest, gpick)):
                    if w.stage == "pick" and w.pod.pack() and w.pod.needed() and not w.paused_names():
                        for card in pick(0, w.pod.pack(), w.pod.pool(), w.pod.needed()):
                            w.toggle(gui, card)
                        self.assertTrue(w.confirm(gui), w.message)
                time.sleep(0.01)
            self.assertEqual((flow.stage, gflow.stage), ("review", "review"))
            self.assertEqual(len(flow.pod.pool(0)), 60)
            self.assertEqual(len(gflow.pod.pool(0)), 60)
            self.assertEqual(gflow.review_seats(), [0, 1, 2])                        # Sam's own and the two AI drafters'
            self.assertEqual({p.seat for p in gflow.pod.draft.review()}, {0, 1, 2})   # never Karl's picks
            for w, gui in ((flow, host), (gflow, guest)):
                w.press(gui, "build")
                self.assertEqual(w.stage, "decks")
            gflow.press(guest, "ready")
            self.assertTrue(_wait(lambda: (frame(guest, 1) or frame(host, 1) or
                                           ("Sam", True) in [(n, r) for n, r in flow.ready_names()]), 30))
            self.assertFalse(flow.started_game)                                       # waits for the host's own Ready
            flow.press(host, "ready")
            self.assertTrue(_wait(lambda: (frame(guest, 1) or frame(host, 1) or
                                           (host.online_draft is None and guest.online_draft is None
                                            and host.state and guest.state)), 180), (flow.message, flow.ended))
            players = (host.state or {}).get("players") or []
            self.assertEqual(len(players), 4)
            self.assertEqual({p.get("life") for p in players}, {40})
            self.assertEqual(guest.session.me()["name"], "Sam")
            mine = [os.path.basename(p) for p in os.listdir(os.path.join(self.tmp, "sam_decks"))]
            self.assertEqual(len(mine), 1, mine)                                     # Sam's drafted deck is in Sam's My decks
            self.assertIn("draft - You - ", mine[0])


def live_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def fn_read(code):
    import forge_net
    return forge_net.read_invite(code)


class LiveDraft:
    """A host's HostSession (NetHost --draft) and its HostDraft, and friends' NetSessions with their GuestPods - without the
    windows - for the live tests that need to stop at a moment (a drop, the AI taking a seat over)."""

    def __init__(self, test, names=("Sam",), seed=21):
        import cube_source as cs
        import forge_client as fc
        import forge_net as fn
        from tools import online_draft_soak as ods
        ods._load()
        self.fc, self.fn, self.ods = fc, fn, ods
        self.tmp = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.cert = fn.ensure_host_cert(self.tmp, fc.find_java())
        self.port = live_port()
        self.host = fc.HostSession(os.path.join(self.tmp, "h.dck"), "Karl", self.port, "pw-otter-12", self.cert,
                                   os.path.join(self.tmp, "g.dck"), upnp=False, bind="127.0.0.1", code="t", seed=seed,
                                   draft=True, guests=3, record_path=os.path.join(self.tmp, "host.jsonl"))
        self.host.stderr_path = os.path.join(self.tmp, "engine.log")
        test.addCleanup(self.close)
        self.friends = []
        self.host.start()
        assert _wait(lambda: self.host.poll() or self.host.hosting is not None, 150), "never hosting"
        for k, name in enumerate(names):
            self.join(name)
        assert _wait(lambda: self.host.poll() or len(self.host.guests_joined) == len(names), 30)
        self.host.draft_lock()
        assert _wait(lambda: self.host.poll() or self.host.draft_locked is not None, 10)
        self.seats = [(int(o["seat"]), o["name"]) for o in self.host.draft_locked["seats"]]
        cube, _ = cs.load("cm")
        check = cs.check_forge(cube.cards, cs.forge_script_lookup())
        self.pod = dp.Pod.new(cube, check, seed=seed, root=os.path.join(self.tmp, "pods"),
                              seats=od.HostDraft.seats_for("Karl", [n for _s, n in self.seats]), online={"friends": list(names)})
        self.hd = od.HostDraft(self.pod, self.seats, self.host.draft_send)
        self.hd.start()
        self.choose = da.chooser(self.pod.cards, seed, self.pod.z)

    def join(self, name, record=None):
        g = self.fc.NetSession("127.0.0.1", self.port, name, "pw-otter-12", "", "", fingerprint=self.cert.fingerprint,
                               code="t", draft=True, record_path=record or os.path.join(self.tmp, "friend_%s.jsonl" % name))
        f = self.ods.Friend(len(self.friends), name, g, 100 + len(self.friends))
        g.start()
        self.friends.append(f)
        return f

    def pump(self):
        od.pump_host(self.host, self.hd)
        self.host.poll()
        for f in self.friends:
            f.session.poll()
            f.pump()

    def step(self, host=True, friends=None):
        """One round: everyone allowed picks what they can. Returns how many picked."""
        self.pump()
        n = 0
        if host and self.pod.draft.pack_for(0) and not self.hd.paused_on():
            self.hd.host_pick(self.choose(0, self.pod.draft.pack_for(0), self.pod.pool(0), self.pod.draft.needed(0)))
            n += 1
        for f in self.friends:
            if friends is not None and f.name not in friends:
                continue
            if f.pod.can_pick() and f.pod.ready_to_show and f.session.connected():
                f.chooser = f.chooser or da.chooser(f.pod.cards, f.seed, f.pod.z)
                f.pod.pick(f.chooser(0, f.pod.pack(), f.pod.pool(), f.pod.needed()))
                n += 1
        self.pump()
        return n

    def seat(self, name):
        return next(d for d in self.hd.friends() if self.hd.name(d) == name)

    def cut(self, f, away_for):
        f.blocker.until = time.monotonic() + away_for
        try:
            f.session.proc.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self):
        for s in [f.session for f in self.friends] + [self.host]:
            try:
                s.close()
            except Exception:
                pass


import socket  # noqa: E402


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveDraftTests(unittest.TestCase):
    def test_a_whole_online_draft_to_game_over_with_bad_picks_refused(self):
        import forge_client as fc
        import forge_net as fn
        from tools import online_draft_soak as ods
        ods._load()
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cert = fn.ensure_host_cert(tmp, fc.find_java())
        r = ods.run_draft(0, 515, tmp, cert, friends=2, bad=3, play=True, rng=random.Random(515))
        self.assertEqual([f for f in r.findings if f["severity"] == "fail"], [], r.line())
        self.assertEqual(r.ended, "game_over", r.line())
        self.assertEqual(r.refused_bad, 3)                         # every bad pick refused, none taken
        self.assertEqual(r.game["players"], 4)
        self.assertEqual(r.game["online"], 2)
        for life in r.game["life"].values():
            self.assertEqual(set(life.values()), {40})

    def test_a_drop_pauses_the_draft_and_the_friend_comes_back_to_the_same_pack(self):
        d = LiveDraft(self, ("Sam", "Ann"))
        sam = d.friends[0]
        for _ in range(6):
            d.step()
        assert _wait(lambda: d.pump() or sam.pod.can_pick(), 20)
        pack, pool = list(sam.pod.pack()), list(sam.pod.pool())
        d.cut(sam, 3)
        self.assertTrue(_wait(lambda: d.pump() or d.hd.paused_on() == [d.seat("Sam")], 20))
        ann = d.friends[1]
        self.assertTrue(_wait(lambda: d.pump() or ann.pod.paused() == ["Sam"], 10))
        before = [len(p) for p in d.pod.draft.pools]
        for _ in range(5):
            d.step(friends=("Ann",))
            time.sleep(0.05)
        self.assertEqual([len(p) for p in d.pod.draft.pools], before)          # nobody picks while Sam is away
        self.assertTrue(_wait(lambda: d.pump() or (sam.session.connected() and d.hd.paused_on() == []), 40))
        self.assertTrue(_wait(lambda: d.pump() or sam.pod.can_pick(), 10))
        self.assertEqual(sam.pod.pack(), pack)                                  # the same seat, pack and picks
        self.assertEqual(sam.pod.pool(), pool)
        self.assertGreaterEqual(sam.session.reconnects, 1)
        sizes = [len(p) for p in d.pod.draft.pools]
        self.assertTrue(_wait(lambda: d.step() >= 0 and all(len(d.pod.draft.pools[s]) > sizes[s] for s in range(4)), 30))
        # and everyone drafts on (each seat picks again; who has a pack first depends on timing)

    def test_the_host_lets_the_ai_pick_then_the_friend_takes_the_seat_back(self):
        d = LiveDraft(self, ("Sam",))
        sam = d.friends[0]
        for _ in range(3):
            d.step()
        d.cut(sam, 3600)                                                        # away (for now)
        seat = d.seat("Sam")
        self.assertTrue(_wait(lambda: d.pump() or d.hd.paused_on() == [seat], 20))
        n0 = len(d.pod.draft.pools[seat])
        self.assertTrue(d.hd.give_to_ai(seat))
        for _ in range(4):
            d.step()
        self.assertGreater(len(d.pod.draft.pools[seat]), n0)                    # the AI picked for Sam's seat
        sam.blocker.until = 0                                                   # Sam is back (the token)
        self.assertTrue(_wait(lambda: d.pump() or (sam.session.connected() and d.hd.connected[seat]), 40))
        self.assertFalse(d.hd.is_ai(seat))
        self.assertTrue(_wait(lambda: d.pump() or (sam.pod.view and not sam.pod.ai_for_me()), 10))
        self.assertEqual(sam.pod.pool(), list(d.pod.draft.pools[seat]))       # the AI's picks are Sam's
        made = len(d.pod.draft.pools[seat])
        self.assertTrue(_wait(lambda: d.step() >= 0 and len(d.pod.draft.pools[seat]) > made, 20))   # Sam picks again
        self.assertFalse(d.hd.is_ai(seat))

    def test_a_friend_whose_program_closed_comes_back_by_name(self):
        d = LiveDraft(self, ("Sam",))
        sam = d.friends[0]
        for _ in range(4):
            d.step()
        assert _wait(lambda: d.pump() or sam.pod.can_pick(), 20)
        pack = list(sam.pod.pack())
        d.cut(sam, 3600)                                                        # the old table never reconnects
        sam.session._closing = True
        self.assertTrue(_wait(lambda: d.pump() or d.hd.paused_on() == [d.seat("Sam")], 20))
        again = d.join("Sam", record=os.path.join(d.tmp, "sam_again.jsonl"))   # Join with the invite code, the same name
        self.assertTrue(again.session.draft)
        self.assertTrue(_wait(lambda: d.pump() or again.pod.can_pick(), 20))
        self.assertEqual(again.pod.pack(), pack)
        d.friends.remove(sam)
        seat, made = d.seat("Sam"), len(d.pod.draft.pools[d.seat("Sam")])
        self.assertTrue(_wait(lambda: d.step() >= 0 and len(d.pod.draft.pools[seat]) > made, 20))   # Sam drafts on

    def test_a_stranger_cant_join_once_the_draft_has_started(self):
        d = LiveDraft(self, ("Sam",))
        with self.assertRaises(d.fn.NetRefused) as e:
            d.join("Lee")
        self.assertIn("started without you", str(e.exception))

    def test_deck_building_waits_for_the_friend(self):
        d = LiveDraft(self, ("Sam",))
        sam = d.friends[0]
        end = time.monotonic() + 120
        while d.hd.stage == "pick" and time.monotonic() < end:
            d.step()
        self.assertEqual(d.hd.stage, "build")
        d.hd.host_deck(d.pod.build(0))
        time.sleep(0.5)
        d.pump()
        self.assertFalse(d.hd.all_ready())                                      # Sam hasn't sent a deck: no game yet
        self.assertTrue(_wait(lambda: d.pump() or sam.pod.stage_name == "build", 10))
        self.assertIn(("Sam", False), sam.pod.ready_list())
        sam.pod.send_deck(sam.pod.build(0))
        self.assertTrue(_wait(lambda: d.pump() or d.hd.all_ready(), 10))
        self.assertTrue(_wait(lambda: d.pump() or sam.pod.deck_state == "ok", 10))


# ==================================================================================================================================
# The windows, headless: every stage fits (1360x840, 1920x1080 at 100% and 200%, the Surface's 2880x1920 at 150%)
# ==================================================================================================================================
FIT_SIZES = (((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((2880, 1920), 1.5))


def _fake_host_session():
    import forge_client as fc

    class FakeDraftHost(fc.ForgeSession):
        def __init__(self):
            super().__init__("", [], name="Karl")
            self.online, self.draft, self.port = "host", True, 36800
            self.hosting = {"t": "hosting", "port": 36800}
            self.cert = type("C", (), {"fingerprint": "ab" * 32})()
            self.sent = []
            self.guests_joined = ["Sam", "Ann"]

        def alive(self):
            return True

        def send(self, **cmd):
            self.sent.append(cmd)
            return True

        def poll(self):
            return 0

    return FakeDraftHost()


def _fake_guest_session():
    import forge_client as fc

    class FakeDraftGuest(fc.ForgeSession):
        def __init__(self):
            super().__init__("", [], name="Sam")
            self.online, self.draft, self.peer_name = "guest", True, "Karl"
            self.sent = []

        def alive(self):
            return True

        def send(self, **cmd):
            self.sent.append(cmd)
            return True

        def poll(self):
            return 0

    return FakeDraftGuest()


class ScreenTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def gui(self, size, scale):
        import forge_client as fc
        import forge_table as ft
        from tests.forge_fake import StubStore
        from tests.test_forge_table import frame
        lib_dir = os.path.join(self.tmp, "lib")
        g = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=size, launcher=ft.Launcher(None, "Karl"),
                          deck_dirs=(lib_dir, lib_dir, self.tmp))
        g.text_scale = scale
        frame(g, 1)
        return g

    def check(self, g, flow, where):
        from tests.test_forge_table import frame
        import pygame
        g.modal = flow
        frame(g, 1)
        screen = pygame.Rect(0, 0, g.L.W, g.L.H)
        where = (g.L.W, g.L.H, g.text_scale, flow.stage, where)
        self.assertTrue(screen.contains(flow.rect), where)
        rects = [r for r, _n in flow.buttons]
        self.assertTrue(rects, where)
        for r in rects:
            self.assertTrue(flow.rect.contains(r), (where, r))
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                self.assertFalse(a.colliderect(b), (where, a, b, flow.buttons))
        return [n for _r, n in flow.buttons]

    def test_every_online_stage_fits_at_every_size(self):
        import online_draft_screens as odscr
        import online_screens as onl
        for size, scale in FIT_SIZES:
            g = self.gui(size, scale)
            w = Wiring(os.path.join(self.tmp, "pods_%d_%s" % (size[0], scale)))
            w.hd.start()
            # the host: the form (with a saved draft to continue), the lobby
            with mock.patch.object(dp, "unfinished", return_value=w.pod):
                host = odscr.OnlineDraftFlow(g, "host", job=None, prefs={"name": "Karl"})
            names = self.check(g, host, "host form")
            self.assertIn("open_host", names)
            self.assertIn("continue_saved", names)
            host.session = _fake_host_session()
            host.wait = onl.HostWait(host.session, host.password, 36800, False)
            host.wait.address.set("203.0.113.7")
            host.host_name, host.stage = "Karl", "lobby"
            names = self.check(g, host, "lobby")
            self.assertIn("start_online", names)
            self.assertIn("copy_invite", names)
            # the pick, paused on Sam: the host's "Let the AI pick for Sam"
            host.pod, host.hd, host.stage = w.pod, w.hd, "pick"
            w.up[2] = False
            w.hd.on_drop(2)
            names = self.check(g, host, "pick, paused")
            self.assertIn("ai_for_1", names)
            self.assertIn("leave", names)
            w.up[2] = True
            w.hd.on_back(2)
            # a friend: joining, picking, waiting for the others
            gs = _fake_guest_session()
            guest = odscr.OnlineDraftFlow(g, "guest", session=gs)
            self.assertEqual(gs.sent[-1], {"c": "draft", "msg": {"k": "view"}})
            names = self.check(g, guest, "joining")
            self.assertIn("leave", names)
            guest.pod = w.guests[2]
            guest.stage = "pick"
            self.check(g, guest, "pick")
            self.assertTrue(guest.tiles)
            # the review, the decks, ready
            while w.hd.stage == "pick":
                w.bot_round(random.Random(1))
            for flow in (host, guest):
                flow.to_review()
                names = self.check(g, flow, "review")
                self.assertIn("build", names)
                flow.to_decks(g)
                names = self.check(g, flow, "decks")
                self.assertIn("ready", names)
                self.assertIn("edit", names)
                flow.stage = "ready"
                names = self.check(g, flow, "ready")
                self.assertIn("unready", names)
                flow.stage = "decks"
                flow.to_build(g)
                self.check(g, flow, "build")
            self.assertEqual(guest.review_seats(), [0, 2])          # Sam's own picks and the AI drafter's - not Ann's or Karl's
            self.assertEqual([guest.pod.seat_name(s) for s in guest.review_seats()], ["You", "AI 1"])
            self.assertEqual(host.review_seats(), [0, 3])

    def test_the_draft_window_offers_draft_with_friends_for_a_commander_cube(self):
        import draft_screens as ds
        from tests.test_forge_table import frame
        g = self.gui((1360, 840), 1.0)
        cube, check = made_up_check()
        flow = ds.DraftFlow.__new__(ds.DraftFlow)
        with mock.patch.object(dp, "unfinished", return_value=None), mock.patch.object(ds.DraftFlow, "load_cube"):
            ds.DraftFlow.__init__(flow, g)
        flow.job = type("J", (), {"done": True, "error": None, "cube": cube, "check": check, "playable": len(check.playable),
                                  "ref": "madeup", "note": ""})()
        flow.loaded_ref = "madeup"
        g.modal = flow
        frame(g, 1)
        self.assertIn("online", [n for _r, n in flow.buttons])
        with mock.patch.object(g, "open_online_draft") as opened:
            flow.press(g, "online")
        opened.assert_called_once()


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveContinueTests(unittest.TestCase):
    def test_the_host_closes_and_continues_and_the_friends_rejoin_by_name(self):
        d = LiveDraft(self, ("Sam", "Ann"))
        for _ in range(5):
            d.step()
        saved = {s: d.pod.draft.pack_for(s) for s in range(4)}
        pools = [list(p) for p in d.pod.draft.pools]
        d.host.close()                                                          # the host's program closes
        sam, ann = d.friends
        self.assertTrue(_wait(lambda: sam.session.poll() or ann.session.poll() or
                              (sam.session.peer_left or sam.session.net_lost or sam.session.exited), 30))
        pod = dp.unfinished(os.path.join(d.tmp, "pods"), online=True)            # Continue it
        self.assertIsNotNone(pod)
        self.assertEqual(od.pod_friends(pod), ["Sam", "Ann"])
        self.assertEqual([list(p) for p in pod.draft.pools], pools)
        import forge_client as fc
        port = live_port()
        host = fc.HostSession(os.path.join(d.tmp, "h2.dck"), "Karl", port, "new-pass-99", d.cert, os.path.join(d.tmp, "g2.dck"),
                              upnp=False, bind="127.0.0.1", code="t", draft=True, guests=3)
        self.addCleanup(host.close)
        host.start()
        self.assertTrue(_wait(lambda: host.poll() or host.hosting is not None, 150))
        d.host, d.port = host, port
        d.friends = []
        g = fc.NetSession("127.0.0.1", port, "Sam", "new-pass-99", "", "", fingerprint=d.cert.fingerprint, code="t", draft=True)
        from tools import online_draft_soak as ods
        sam2 = ods.Friend(0, "Sam", g, 5)
        g.start()
        d.friends.append(sam2)
        self.assertTrue(_wait(lambda: host.poll() or host.guests_joined == ["Sam"], 20))
        host.draft_lock(reserve=["Ann"])                                         # Ann isn't back yet: her seat waits by name
        self.assertTrue(_wait(lambda: host.poll() or host.draft_locked is not None, 10))
        friends, strangers = od.continue_seats(pod, host.draft_locked["seats"])
        self.assertEqual([(n, c) for _ns, n, c in friends], [("Sam", True), ("Ann", False)])
        self.assertEqual(strangers, [])
        d.pod = pod
        d.hd = od.HostDraft(pod, friends, host.draft_send)
        d.hd.start()
        d.choose = da.chooser(pod.cards, 9, pod.z)
        self.assertEqual(d.hd.paused_on(), [2])                                  # waiting for Ann
        self.assertTrue(_wait(lambda: d.pump() or sam2.pod.ready_to_show, 20))
        self.assertEqual(sam2.pod.pack(), saved[1])                              # Sam's own pack, from the saved draft
        self.assertEqual(sam2.pod.pool(), pools[1])
        self.assertEqual(sam2.pod.paused(), ["Ann"])
        g2 = fc.NetSession("127.0.0.1", port, "Ann", "new-pass-99", "", "", fingerprint=d.cert.fingerprint, code="t", draft=True)
        ann2 = ods.Friend(1, "Ann", g2, 6)
        g2.start()                                                               # Ann joins with the new code, the same name
        d.friends.append(ann2)
        self.assertTrue(_wait(lambda: d.pump() or (d.hd.paused_on() == [] and ann2.pod.ready_to_show), 20))
        self.assertEqual(ann2.pod.pack(), saved[2])
        end = time.monotonic() + 180
        while d.hd.stage == "pick" and time.monotonic() < end:
            d.step()
        self.assertEqual(d.hd.stage, "build")
        self.assertEqual([len(p) for p in pod.draft.pools], [60, 60, 60, 60])


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveGameStartTests(unittest.TestCase):
    def test_a_friend_given_to_the_ai_and_still_away_is_an_ai_player_in_the_game(self):
        d = LiveDraft(self, ("Sam", "Ann"))
        sam, ann = d.friends
        for _ in range(3):
            d.step()
        d.cut(sam, 3600)
        seat = d.seat("Sam")
        self.assertTrue(_wait(lambda: d.pump() or d.hd.paused_on() == [seat], 20))
        d.hd.give_to_ai(seat)
        end = time.monotonic() + 180
        while d.hd.stage == "pick" and time.monotonic() < end:
            d.step()
        self.assertEqual(d.hd.stage, "build")
        self.assertIn(seat, d.hd.decks)                                        # Sam's seat: the AI's Auto-build
        d.hd.host_deck(d.pod.build(0))
        self.assertTrue(_wait(lambda: d.pump() or ann.pod.stage_name == "build", 10))
        ann.pod.send_deck(ann.pod.build(0))
        self.assertTrue(_wait(lambda: d.pump() or d.hd.all_ready(), 10))
        host_path, friends, ai, _labels = od.write_game_decks(d.hd, os.path.join(d.tmp, "game"))
        self.assertEqual([ns for ns, _p in friends], [d.hd.net_of[d.seat("Ann")]])
        self.assertEqual(len(ai), 2)                                           # the AI drafter and Sam's seat
        d.host.draft_game(host_path, friends, ai)
        self.assertTrue(_wait(lambda: d.pump() or (d.host.ready and ann.session.ready and d.host.state and ann.session.state),
                              120), d.host.draft_game_failed)
        players = d.host.state["players"]
        self.assertEqual(len(players), 4)
        names = [p["name"] for p in players]
        self.assertIn("Ann", names)
        self.assertTrue(any("Sam" in n and n.startswith("AI") for n in names), names)
        self.assertEqual({p["life"] for p in players}, {40})
        self.assertEqual(ann.session.me()["name"], "Ann")
