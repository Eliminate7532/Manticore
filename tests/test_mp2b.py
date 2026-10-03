# SPDX-License-Identifier: GPL-3.0-or-later
"""Round MP2b: a hosted online game can be continued another day.

Fast tests need no Java: online_save.py (the folder, meta.json, the deck copies, the fullest journal, finishing and giving up
older games), the host's session in play-back mode (questions and events kept back, the arguments), the Host dialog's saved
game row (Continue it / Discard / a game this version can't continue), the table writing, updating and finishing the save, and
the Host screen while a saved game is restored. Live tests (skipped without Java and forge_runtime/) play a real game for a few
turns, close the host's engine in the middle, continue it from the save with the guest back in its own seat, and check that the
board is exactly as it was: once 1v1, once with an AI player too.
"""
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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forge_client as fc
import forge_net as fn
import online_save
import online_screens as onl
import tests.live as live
from tests.test_deck_screen import TempDecks
from tests.test_forge_table import frame
from tests.test_mp1 import FakeHostSession, deck_file, free_port, press_button, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = {"forge": "Forge 2.0.15-SNAPSHOT | commit 3a74143", "bridge": "abc123"}


def write(path, text="x"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------------------------------------------------------------
# 1. online_save.py
# ---------------------------------------------------------------------------------------------------------------------
class SaveTests(unittest.TestCase):
    def setUp(self):
        self.saves = tempfile.mkdtemp(prefix="mp2b_")
        self.addCleanup(shutil.rmtree, self.saves, True)

    def game(self, turn=None, cmds=3, guests=("Sam",)):
        folder = online_save.new_folder(self.saves)
        decks = {"host.dck": write(os.path.join(self.saves, "h.dck"), "host deck")}
        gl = []
        for i, name in enumerate(guests):
            decks["guest_%d.dck" % (i + 2)] = write(os.path.join(self.saves, "g%d.dck" % i), "guest deck %d" % i)
            gl.append({"seat": i + 2, "name": name, "deck": "guest_%d.dck" % (i + 2)})
        online_save.start(folder, {"seed": 7, "host": "Karl", "guests": gl, "ai": [], "mine": "host.dck", "replay": KEY}, decks)
        with open(online_save.journal_path(folder), "w", encoding="utf-8") as f:
            for i in range(cmds):
                f.write(json.dumps({"s": 1, "c": {"c": "ok", "at": i + 1}}) + "\n")
        if turn:
            online_save.note(folder, turn=turn)
        return folder

    def test_a_started_game_is_found_with_its_decks(self):
        folder = self.game(turn=7)
        found = online_save.latest(self.saves)
        self.assertIsNotNone(found)
        self.assertEqual(found[0], folder)
        meta = found[1]
        self.assertEqual((meta["seed"], meta["turn"], meta["v"]), (7, 7, 1))
        with open(os.path.join(folder, "guest_2.dck"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "guest deck 0")
        self.assertIn("turn 7, with Sam", online_save.describe(meta))
        text = json.dumps(meta)
        self.assertNotIn("password", text)
        self.assertNotIn("token", text)

    def test_nothing_is_offered_until_the_game_started_or_without_actions(self):
        folder = online_save.new_folder(self.saves)
        self.assertIsNone(online_save.latest(self.saves))              # no meta.json yet
        online_save.forget_unstarted(folder)
        self.assertFalse(os.path.exists(folder))
        self.game(cmds=0)
        self.assertIsNone(online_save.latest(self.saves))              # nothing to play back

    def test_the_fullest_journal_is_the_one_played_back(self):
        folder = self.game(cmds=5)
        src = online_save.prepare_resume(folder)
        self.assertEqual(online_save.count_lines(src), 5)
        with open(online_save.journal_path(folder), "w", encoding="utf-8") as f:   # a resume that stopped after 2
            f.write('{"s":1,"c":{"c":"ok"}}\n' * 2)
        self.assertEqual(online_save.count_lines(online_save.replay_source(folder)), 5)
        self.assertNotEqual(online_save.prepare_resume(folder), src)   # never overwrites an earlier copy

    def test_finishing_and_giving_up_older_games(self):
        old = self.game()
        time.sleep(1.1)                                                # folder names sort by time
        new = self.game()
        online_save.give_up_others(self.saves, new)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(any(n.endswith("_abandoned") for n in os.listdir(os.path.join(online_save.root(self.saves), "finished"))))
        self.assertEqual(online_save.latest(self.saves)[0], new)
        online_save.finish(new, "won")
        self.assertIsNone(online_save.latest(self.saves))
        for i in range(online_save.KEEP + 3):
            online_save.finish(self.game(), "lost%d" % i)
        self.assertEqual(len(os.listdir(os.path.join(online_save.root(self.saves), "finished"))), online_save.KEEP)


class SoakToolTests(unittest.TestCase):
    def test_a_quiet_moment_and_the_game_line(self):
        from tools import online_soak as osk

        class S:
            def __init__(self, msg, asking, requests=()):
                self.state = {"prompt": {"message": msg}, "asking": asking}
                self.requests = list(requests)

        class Seat:
            def __init__(self, session):
                self.session = session
        self.assertTrue(osk._quiet([Seat(S("Priority (Main 1)", True)), Seat(S("Waiting for Host...", False))]))
        self.assertFalse(osk._quiet([Seat(S("Priority (Main 1)", True)), Seat(S("", False, [{"id": 1}]))]))
        self.assertFalse(osk._quiet([Seat(S("Declare attackers", True))]))
        r = osk.OnlineResult(1, 5, ["a.txt", "b.txt"])
        r.resumes, r.resume_turn = 1, 9
        self.assertIn("resumed@9", r.line())


class SessionTests(unittest.TestCase):
    def host(self, **kw):
        cert = fn.HostCert("x.p12", "sp", "ab" * 32)
        return fc.HostSession("me.dck", "Karl", 36800, "pw", cert, "guest.dck", upnp=False, **kw)

    def test_the_journal_and_the_resume_arguments(self):
        h = self.host(journal_path="/s/cmds.jsonl", seed=7, ai_paths=["/s/ai_1.dck"],
                      resume={"replay": "/s/cmds_1.jsonl", "guests": [(2, "Sam", "/s/guest_2.dck"), (3, "Ann", "/s/guest_3.dck")]})
        args = h._bridge_args()
        self.assertEqual(args[args.index("--journal") + 1], "/s/cmds.jsonl")
        self.assertEqual(args[args.index("--replay") + 1], "/s/cmds_1.jsonl")
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "--resume-guest"],
                         ["2|Sam|/s/guest_2.dck", "3|Ann|/s/guest_3.dck"])
        self.assertNotIn("--guests", args)                             # the saved seats say how many
        self.assertEqual(h.guests_wanted, 2)
        self.assertEqual(args[args.index("--seed") + 1], "7")
        self.assertTrue(h.resuming)
        self.assertNotIn("--journal", self.host()._bridge_args())

    def test_questions_and_events_wait_until_the_play_back_is_done(self):
        h = self.host(resume={"replay": "x", "guests": [(2, "Sam", "g")]})
        h.handle({"t": "request", "id": 4, "kind": "confirm"})
        h.handle({"t": "event", "kind": "tap", "card": 5, "seq": 1})
        h.handle({"t": "replay", "done": 40, "total": 90})
        self.assertEqual((list(h.requests), len(h.events), h.replay_progress), ([], 0, (40, 90)))
        h.handle({"t": "seats", "guests": [{"seat": 2, "name": "Sam", "deck": "g"}]})
        h.handle({"t": "replay_done", "applied": 90, "skipped": 0, "total": 90})
        self.assertFalse(h.resuming)
        h.handle({"t": "request", "id": 4, "kind": "confirm"})          # asked again after the play-back
        self.assertEqual([r["id"] for r in h.requests], [4])
        self.assertFalse(h.everyone_back())
        h.handle({"t": "guest_joined", "name": "Sam", "seat": 2, "resumed": True})
        self.assertTrue(h.everyone_back())


# ---------------------------------------------------------------------------------------------------------------------
# 2. the table
# ---------------------------------------------------------------------------------------------------------------------
class TableTests(TempDecks):
    def saved_game(self, gui, key=None):
        saves = self.saves
        folder = online_save.new_folder(saves)
        online_save.start(folder, {"seed": 7, "host": "Karl", "guests": [{"seat": 2, "name": "Sam", "deck": "guest_2.dck"}],
                                   "ai": [], "mine": "host.dck", "replay": key or gui.replay_key(), "turn": 9},
                          {"host.dck": write(os.path.join(self.tmp, "h.dck")), "guest_2.dck": write(os.path.join(self.tmp, "g.dck"))})
        write(online_save.journal_path(folder), '{"s":1,"c":{"c":"ok","at":1}}\n')
        return folder

    def setUp(self):
        super().setUp()
        import forge_table
        self.saves = os.path.join(self.tmp, "saves")
        p = mock.patch.object(forge_table, "SAVES_DIR", self.saves)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch.object(forge_table.ForgeTable, "replay_key", lambda self: dict(KEY))
        p2.start()
        self.addCleanup(p2.stop)

    def test_the_host_dialog_offers_the_saved_game(self):
        gui = self.idle_gui()
        folder = self.saved_game(gui)
        gui.menu.press(gui, "host_online")
        d = gui.modal
        self.assertEqual(d.saved[0], folder)
        frame(gui, 1)
        self.assertIn("toggle_resume", [n for _r, n in d.buttons])
        press_button(gui, d, "toggle_resume")
        self.assertTrue(d.resume)
        frame(gui, 1)
        self.assertNotIn("guests_plus", [n for _r, n in d.buttons])     # the saved seats, not new ones
        press_button(gui, d, "toggle_resume")                          # "New game" goes back
        self.assertFalse(d.resume)
        press_button(gui, d, "toggle_resume")
        d.fields["name"].set("Karl")
        with mock.patch.object(gui, "host_game", return_value=None) as hg:
            d.host(gui)
        self.assertEqual(hg.call_args.kwargs["resume"][0], folder)
        gui.discard_online_save()
        self.assertIsNone(online_save.latest(self.saves))

    def test_discard_in_the_dialog(self):
        gui = self.idle_gui()
        self.saved_game(gui)
        gui.menu.press(gui, "host_online")
        d = gui.modal
        press_button(gui, d, "discard_saved")
        self.assertIsNone(d.saved)
        self.assertIsNone(online_save.latest(self.saves))
        frame(gui, 1)
        self.assertNotIn("toggle_resume", [n for _r, n in d.buttons])

    def test_a_save_from_another_version_can_only_be_discarded(self):
        gui = self.idle_gui()
        self.saved_game(gui, key={"forge": "other", "bridge": "other"})
        gui.menu.press(gui, "host_online")
        d = gui.modal
        frame(gui, 1)
        names = [n for _r, n in d.buttons]
        self.assertNotIn("toggle_resume", names)
        self.assertIn("discard_saved", names)
        msg = gui._host_saved_game(d.saved[:2], 36800, "pw", False, None)
        self.assertIn("different version", msg)

    def test_a_saved_game_opens_the_dialog_even_with_a_deck_problem(self):
        gui = self.idle_gui()
        self.saved_game(gui)
        with mock.patch.object(gui, "_online_deck_problem", return_value="Your deck: 3 cards are banned"):
            gui.menu.press(gui, "host_online")
        d = gui.modal
        self.assertIsInstance(d, onl.HostDialog)
        d.fields["name"].set("Karl")
        d.host(gui)                                                    # a new game: refused, the deck can't be used
        self.assertIn("banned", d.message[0])

    def test_hosting_writes_the_save_when_the_game_starts_and_finishes_it(self):
        gui = self.idle_gui()
        made = {}

        def fake_host(*a, **kw):
            made.update(kw)
            s = FakeHostSession(*a, **kw)
            s.seed = 11
            return s
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", side_effect=fake_host):
            self.assertIsNone(gui.host_game(gui.menu.mine, "Karl", 36800, "pw-123", False))
        folder = gui.online_save_folder
        self.assertEqual(made["journal_path"], online_save.journal_path(folder))
        s = gui.session
        guest_deck = write(os.path.join(self.tmp, "guest.dck"), "[metadata]\nName=Veyran\n")
        s.handle({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})
        s.handle({"t": "guest_joined", "name": "Sam", "code": "x", "seat": 2, "joined": 1, "wanted": 1})
        s.handle({"t": "seats", "guests": [{"seat": 2, "name": "Sam", "deck": guest_deck}]})
        from tests.forge_fake import load_state
        s.handle({"t": "ready", "protocol": 2, "seats": 2, "online": True})
        s.handle(load_state("main1_start"))
        frame(gui, 2)
        meta = online_save.read_meta(folder)
        self.assertEqual((meta["seed"], meta["host"], meta["guests"][0]["name"]), (11, "Karl", "Sam"))
        self.assertTrue(os.path.isfile(os.path.join(folder, "guest_2.dck")))
        self.assertEqual(meta.get("turn"), (gui.state or {}).get("turn"))
        gui.end_online_save("won")
        self.assertFalse(os.path.exists(folder))

    def test_cancelling_before_the_start_leaves_nothing(self):
        gui = self.idle_gui()
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", FakeHostSession):
            gui.host_game(gui.menu.mine, "Karl", 36800, "pw-123", False)
        folder = gui.online_save_folder
        self.assertTrue(os.path.isdir(folder))
        gui.cancel_hosting()
        self.assertFalse(os.path.exists(folder))

    def test_the_host_screen_while_restoring(self):
        gui = self.idle_gui()
        folder = self.saved_game(gui)

        def fake_host(*a, **kw):
            s = FakeHostSession(*a, **kw)
            s.seed = kw.get("seed")
            s.resume, s.resuming, s.replay_progress, s.seat_list = kw["resume"], True, (0, 0), []
            s.everyone_back = lambda: {"Sam"} <= set(s.guests_joined)
            return s
        cert = fn.HostCert(os.path.join(self.tmp, "x.p12"), "sp", "cd" * 32)
        with mock.patch.object(fn, "ensure_host_cert", return_value=cert), mock.patch.object(fc, "HostSession", side_effect=fake_host):
            self.assertIsNone(gui.host_game(None, "Karl", 36800, "pw-9", False, resume=(folder, online_save.read_meta(folder))))
        s = gui.session
        self.assertEqual(s.seed, 7)
        self.assertEqual(s.name, "Karl")
        s.handle({"t": "hosting", "port": 36800, "bind": "0.0.0.0"})
        s.handle({"t": "ready", "protocol": 2, "seats": 2, "online": True})
        s.replay_progress = (30, 80)
        texts = []
        real = onl.draw_text
        with mock.patch.object(onl, "draw_text", side_effect=lambda scr, t, *a, **k: texts.append(t) or real(scr, t, *a, **k)):
            frame(gui, 1)
        self.assertTrue(any("Restoring the saved game" in t and "30 of 80" in t for t in texts), texts)
        self.assertIsNotNone(gui.hosting_wait)
        s.resuming = False
        s.replay_result = {"applied": 50, "skipped": 0, "total": 80, "diverged": "action 51: ..."}
        frame(gui, 1)
        self.assertIsNotNone(gui.hosting_wait)                         # Sam isn't back yet
        self.assertIn("up to action 50 of 80", gui.toast[0])
        s.handle({"t": "guest_joined", "name": "Sam", "seat": 2, "resumed": True})
        from tests.forge_fake import load_state
        s.handle(load_state("main1_start"))
        frame(gui, 2)
        self.assertIsNone(gui.hosting_wait)
        self.assertTrue(os.path.isdir(folder))                         # the same save goes on

    def test_the_guest_is_told_the_host_is_restoring(self):
        self.assertIn("restoring", onl.lobby_line({"resuming": True, "players": ["Karl", "Sam"], "joined": 1, "wanted": 1}, "Sam"))


# ---------------------------------------------------------------------------------------------------------------------
# 3. live: a real game, saved, closed and continued
# ---------------------------------------------------------------------------------------------------------------------
LIVE = live.live_enabled()


@unittest.skipUnless(LIVE, "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()
        cls.tmp = tempfile.mkdtemp(prefix="mp2b_")
        cls.cert = fn.ensure_host_cert(cls.tmp, fc.find_java())
        cls.decks = {name: deck_file(cls.tmp, sample, name) for name, sample in (
            ("Lathril", "typal_lathril.txt"), ("Veyran", "spellslinger_veyran.txt"), ("Teysa", "aristocrats_teysa.txt"))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def host(self, port, journal, **kw):
        h = fc.HostSession(kw.pop("deck", self.decks["Lathril"]), "Karl", port, "pw-live-5", self.cert,
                           os.path.join(self.tmp, "guest_%d.dck" % port), upnp=False, bind="127.0.0.1", code="hostcode",
                           journal_path=journal, **kw)
        h.stderr_path = os.path.join(self.tmp, "host_%d.log" % port)
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        return h

    def guest(self, port):
        with open(self.decks["Veyran"], encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, "Sam", "pw-live-5", "Veyran", f.read(), fingerprint=self.cert.fingerprint,
                              code="guestcode")
        g.start()
        self.addCleanup(g.close)
        return g

    @staticmethod
    def quiet_board(h, g):
        """Both tables idle at a priority question nobody has answered: a moment to save at."""
        for s in (h, g):
            if s.requests:
                return False
        return any(((s.state or {}).get("prompt") or {}).get("message", "").startswith("Priority") and (s.state or {}).get("asking")
                   for s in (h, g))

    def play_and_resume(self, ai_paths=()):
        from tools import online_soak as osk
        osk._load_modules()
        import replay
        folder = tempfile.mkdtemp(prefix="save_", dir=self.tmp)
        journal = os.path.join(folder, "cmds.jsonl")
        port = free_port()
        h = self.host(port, journal, seed=31, ai_paths=list(ai_paths), **({"guests": 1} if ai_paths else {}))
        g = self.guest(port)
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.seat_list, 60, pump=[h, g]))
        seats = [osk.Seat("host", h, 31), osk.Seat("guest", g, 41)]
        end = time.time() + 240
        while time.time() < end and ((h.state or {}).get("turn") or 0) < 5:
            for seat in seats:
                seat.step()
            time.sleep(0.02)
        self.assertGreaterEqual((h.state or {}).get("turn") or 0, 5, "the game never reached turn 5")
        self.assertTrue(wait_for(lambda: self.quiet_board(h, g), 60, pump=[h, g]), "no quiet moment to save at")
        wait_for(lambda: False, 1.5, pump=[h, g])                       # nothing else happening
        before_host, before_guest = replay.summary(h.state, any_waiting=True), replay.summary(g.state, any_waiting=True)
        lines = online_save.count_lines(journal)
        self.assertGreater(lines, 10)
        guest_seat = h.seat_list[0]
        h.close()                                                       # the host's program closes mid-game
        self.assertTrue(wait_for(lambda: g.exited, 15, pump=[g]))
        g.close()
        # ---- the next day: continue it
        src = os.path.join(folder, "cmds_1.jsonl")
        shutil.copyfile(journal, src)
        saved_guest = os.path.join(folder, "guest_2.dck")
        shutil.copyfile(guest_seat["deck"], saved_guest)
        port2 = free_port()
        h2 = self.host(port2, journal, seed=31, ai_paths=list(ai_paths),
                       resume={"replay": src, "guests": [(guest_seat["seat"], guest_seat["name"], saved_guest)]})
        self.assertTrue(wait_for(lambda: not h2.resuming or h2.exited, 300, pump=[h2]), "the play-back never finished")
        self.assertIsNone((h2.replay_result or {}).get("diverged"), h2.replay_result)
        g2 = self.guest(port2)
        self.assertEqual(g2.seat, guest_seat["seat"])
        self.assertTrue(wait_for(lambda: h2.everyone_back() and g2.ready and g2.state, 30, pump=[h2, g2]))
        wait_for(lambda: False, 2.0, pump=[h2, g2])
        self.assertEqual(replay.differences(before_host, replay.summary(h2.state, any_waiting=True)), [])
        self.assertEqual(replay.differences(before_guest, replay.summary(g2.state, any_waiting=True)), [])
        self.assertGreaterEqual(online_save.count_lines(journal), lines - h2.replay_result.get("skipped", 0))
        # and it goes on
        seats = [osk.Seat("host", h2, 32), osk.Seat("guest", g2, 42)]
        turn = (h2.state or {}).get("turn") or 0
        end = time.time() + 120
        while time.time() < end and ((h2.state or {}).get("turn") or 0) < turn + 1 and not h2.game_over:
            for seat in seats:
                seat.step()
            time.sleep(0.02)
        self.assertTrue(((h2.state or {}).get("turn") or 0) > turn or h2.game_over, "the continued game didn't go on")

    def test_a_question_asked_by_a_click_is_answered_from_the_save(self):
        """The click and its question: clicking Lorien Revealed asks "Choose an ability" (cast it, or islandcycling) on Swing's
        thread, and blocks there until the answer comes - the next line of the save. The first resumed game of the online
        soak deadlocked on exactly this (ResumeFeeder used invokeAndWait)."""
        import replay
        folder = tempfile.mkdtemp(prefix="save_", dir=self.tmp)
        journal = os.path.join(folder, "cmds.jsonl")
        port = free_port()
        h = self.host(port, journal, seed=33, dev=True)
        g = self.guest(port)
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.seat_list, 60, pump=[h, g]))
        from tests.test_mp2 import LiveTests as MP2Live
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
                    g.ok()                                             # the guest passes (the set-up board is the host's turn)
                    wait_for(lambda: False, 0.4, pump=[h, g])
                elif mh.startswith("Priority") or h.is_mulligan_prompt():
                    h.ok()
                    wait_for(lambda: False, 0.4, pump=[h, g])
            time.sleep(0.1)
        self.assertIsNotNone(asked, "never asked to choose an ability")
        cycling = next(i for i, it in enumerate(asked.get("items") or []) if "cycling" in json.dumps(it).lower())
        h.answer(asked, [cycling])                                     # islandcycling: pay {1}, discard it, find an Island
        end = time.time() + 60
        while time.time() < end and not any(c.get("name") == "L\u00f3rien Revealed"
                                            for c in h.me()["zones"].get("graveyard", [])):
            h.poll()
            g.poll()
            for r in list(h.requests):
                h.answer(r, True if r["kind"] == "confirm" else [0])
            mh = MP2Live.msg(h)
            if "Pay Mana" in mh or ((h.state or {}).get("prompt") or {}).get("selecting"):
                lands = [c for c in h.me()["zones"]["battlefield"] if c["name"] == "Island" and not c.get("tapped")]
                if lands:
                    h.click_card(lands[0]["id"])
            elif mh.startswith("Priority") and h.state.get("stack"):
                h.ok()
            elif MP2Live.msg(g).startswith("Priority"):
                g.ok()
            time.sleep(0.3)
        self.assertTrue(wait_for(lambda: self.quiet_board(h, g), 60, pump=[h, g]), "no quiet moment to save at")
        wait_for(lambda: False, 1.5, pump=[h, g])
        before = replay.summary(h.state, any_waiting=True)
        seat = h.seat_list[0]
        h.close()
        g.close()
        src = os.path.join(folder, "cmds_1.jsonl")
        shutil.copyfile(journal, src)
        with open(src, encoding="utf-8") as f:
            self.assertIn('"reply"', f.read())                         # the question's answer is in the save
        port2 = free_port()
        h2 = self.host(port2, journal, seed=33, dev=True,
                       resume={"replay": src, "guests": [(seat["seat"], seat["name"], seat["deck"])]})
        self.assertTrue(wait_for(lambda: not h2.resuming or h2.exited, 180, pump=[h2]), "the play-back never finished")
        self.assertIsNone((h2.replay_result or {}).get("diverged"), h2.replay_result)
        g2 = self.guest(port2)
        self.assertTrue(wait_for(lambda: h2.everyone_back() and g2.state, 30, pump=[h2, g2]))
        wait_for(lambda: False, 2.0, pump=[h2, g2])
        self.assertEqual(replay.differences(before, replay.summary(h2.state, any_waiting=True)), [])

    def test_a_1v1_game_continued_from_its_save_is_the_same_board(self):
        self.play_and_resume()

    def test_a_game_with_an_ai_player_continued_from_its_save(self):
        self.play_and_resume(ai_paths=[self.decks["Teysa"]])
