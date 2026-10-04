# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 22: the bridge's event channel (protocol 2), prompt kinds, question numbers on clicks (stale clicks are dropped, so a replayed
journal repeats the game exactly), and the "Forge has not answered" banner."""
import copy
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

import events
import forge_client as fc
import forge_table as ft
import journal
import replay
import tests.live as live
from tests.forge_fake import load_state
from tests.test_forge_table import frame, make_gui


class Pipe:
    def __init__(self):
        self.data = []

    def write(self, t):
        self.data.append(t)

    def flush(self):
        pass


class Proc:
    def __init__(self):
        self.stdin = Pipe()

    def poll(self):
        return None


def live_session_stub():
    s = fc.ForgeSession("", [])
    s.proc = Proc()
    return s


# ---- 1. the client ------------------------------------------------------------------------------------------------------------------

class ClientTests(unittest.TestCase):
    def test_ready_says_the_protocol(self):
        s = fc.ForgeSession("", [])
        self.assertEqual(s.protocol, 1)
        s.handle({"t": "ready", "protocol": 2})
        self.assertEqual(s.protocol, 2)
        s2 = fc.ForgeSession("", [])
        s2.handle({"t": "ready"})
        self.assertEqual(s2.protocol, 1, "an old bridge says nothing: protocol 1")

    def test_events_go_to_their_own_queue_not_the_log(self):
        s = fc.ForgeSession("", [])
        s.handle({"t": "event", "seq": 1, "kind": "shuffle", "player": 0})
        self.assertEqual(len(s.events), 1)
        self.assertEqual(s.log, [])
        self.assertEqual(s.events[0][0]["kind"], "shuffle")

    def test_every_message_moves_last_rx(self):
        s = fc.ForgeSession("", [])
        s.last_rx = 0.0
        s.handle({"t": "log", "entries": []})
        self.assertGreater(s.last_rx, 0.0)

    def test_dropped_clicks_are_kept(self):
        s = fc.ForgeSession("", [])
        s.handle({"t": "dropped", "c": "ok", "at": 4, "now": 5})
        self.assertEqual(s.dropped, [{"t": "dropped", "c": "ok", "at": 4, "now": 5}])

    def test_clicks_carry_the_question_number(self):
        s = live_session_stub()
        s.state = {"inputSeq": 7}
        seen = []
        s.on_send = seen.append
        s.ok()
        s.click_card(12)
        s.use_mana("G")
        s.send(c="stops", mine=True, phases=[])
        s.answer({"id": 3, "kind": "choose"}, [0]) if hasattr(s, "answer") else None
        self.assertEqual(seen[0], {"c": "ok", "at": 7})
        self.assertEqual(seen[1], {"c": "card", "id": 12, "at": 7})
        self.assertEqual(seen[2], {"c": "mana", "color": "G", "at": 7})
        self.assertNotIn("at", seen[3], "settings commands are not answers to a question")
        self.assertTrue(all("at" not in c for c in seen[4:]), "replies already name their question by id")

    def test_an_old_bridge_gets_no_numbers(self):
        s = live_session_stub()
        s.state = {"prompt": {}}
        seen = []
        s.on_send = seen.append
        s.ok()
        self.assertEqual(seen, [{"c": "ok"}])

    def test_a_replayed_click_keeps_its_original_number(self):
        s = live_session_stub()
        s.state = {"inputSeq": 9}
        seen = []
        s.on_send = seen.append
        s.send(c="ok", at=4)
        self.assertEqual(seen, [{"c": "ok", "at": 4}])

    def test_the_journal_records_the_number(self):
        s = live_session_stub()
        s.state = {"inputSeq": 3}
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        j = journal.GameJournal(tmp)
        j.start(1, "Karl", {}, "x", 100.0)
        s.on_send = lambda cmd: j.command(cmd, 101.0)
        s.ok()
        j.close()
        _start, cmds, _end = journal.read(os.path.join(tmp, journal.CURRENT))
        self.assertEqual(cmds, [{"c": "ok", "at": 3}])


# ---- 2. the router (ported from the reference tests) ------------------------------------------------------------------------------

class RouterTests(unittest.TestCase):
    def ev(self, seq, kind, **d):
        return dict(d, t="event", seq=seq, kind=kind)

    def test_waits_for_the_snapshot_that_shows_it(self):
        r = events.EventRouter()
        r.feed(self.ev(1, "zone", card=5, **{"from": "Hand", "to": "Battlefield"}), 0.0)
        self.assertEqual(r.ready(0, 0.01), [])
        beats = r.ready(1, 0.04)
        self.assertEqual([(b.kind, b.card) for b in beats], [("zone", 5)])
        self.assertEqual(r.ready(None, 0.05), [])

    def test_a_combo_loop_folds_into_few_beats(self):
        r = events.EventRouter()
        t = 0.0
        for i in range(1, 401):                                    # 200 taps + 200 untaps of one Basalt Monolith in 2 s
            r.feed(self.ev(i, "tap", card=77, tapped=i % 2 == 1), t)
            t += 0.005
        beats = r.ready(400, t)
        self.assertLessEqual(len(beats), 4)
        self.assertEqual(sum(b.count for b in beats), 400)

    def test_many_different_cards_turn_into_a_flurry(self):
        r = events.EventRouter()
        for i in range(1, 31):
            r.feed(self.ev(i, "tap", card=i), 0.0)
        beats = r.ready(30, 0.01)
        self.assertEqual(len(beats), 30)
        self.assertTrue(any(b.flurry for b in beats))
        self.assertFalse(beats[0].flurry)

    def test_stale_events_are_marked_but_life_never(self):
        r = events.EventRouter()
        r.feed(self.ev(1, "tap", card=1), 0.0)
        r.feed(self.ev(2, "life", player=1, old=40, new=37), 0.0)
        beats = r.ready(2, 3.0)
        self.assertTrue(beats[0].stale)
        self.assertFalse(beats[1].stale)

    def test_gap_in_numbers_is_counted(self):
        r = events.EventRouter()
        r.feed(self.ev(1, "tap", card=1), 0.0)
        r.feed(self.ev(3, "tap", card=2), 0.0)
        self.assertEqual(r.gaps, 1)


# ---- 3. the table hands out beats only when the board shows them -----------------------------------------------------------------

class GatingTests(unittest.TestCase):
    def test_a_beat_waits_for_its_snapshot(self):
        gui = make_gui("main1_start")
        s = gui.session
        s.protocol = 2
        st = copy.deepcopy(gui.state)
        s.events.append(({"t": "event", "seq": 1, "kind": "tap", "card": 1, "tapped": True}, time.monotonic()))
        s.events.append(({"t": "event", "seq": 2, "kind": "tap", "card": 2, "tapped": True}, time.monotonic()))
        st["eventSeq"] = 1
        s.inbox.put(copy.deepcopy(st))
        gui.sync()
        self.assertEqual([b.card for b in gui.beats_this_frame], [1])
        gui.sync()
        self.assertEqual(gui.beats_this_frame, [], "nothing new: no beats")
        st["eventSeq"] = 2
        s.inbox.put(copy.deepcopy(st))
        gui.sync()
        self.assertEqual([b.card for b in gui.beats_this_frame], [2])

    def test_an_old_bridge_still_gets_life_beats(self):
        gui = make_gui("main1_start")
        st = copy.deepcopy(gui.state)
        st["players"][1]["life"] -= 3
        gui.session.inbox.put(st)
        gui.sync()
        life = [b for b in gui.beats_this_frame if b.kind == "life"]
        self.assertEqual(len(life), 1)
        self.assertEqual(life[0].data["new"] - life[0].data["old"], -3)

    def test_a_new_bridge_gets_no_made_up_beats(self):
        gui = make_gui("main1_start")
        gui.session.protocol = 2
        st = copy.deepcopy(gui.state)
        st["players"][1]["life"] -= 3
        gui.session.inbox.put(st)
        gui.sync()
        self.assertEqual([b for b in gui.beats_this_frame if b.kind == "life"], [], "protocol 2 sends real life events")

    def test_f3_shows_protocol_gaps_and_dropped_clicks(self):
        gui = make_gui("main1_start")
        self.assertTrue(any("event gaps 0" in ln and "dropped 0" in ln for ln in gui.perf_lines()))


# ---- 4. prompt kinds ------------------------------------------------------------------------------------------------------------

KIND_OF_FIXTURE = {"coin_toss": "InputConfirm", "declare_attackers": "InputAttack", "declare_blockers": "InputBlock",
                   "main1_lands": "InputPassPriority", "main1_start": "InputPassPriority", "mulligan": "InputConfirmMulligan",
                   "paying_mana": "InputPayManaOfCostPayment", "stack_one": "InputPassPriority", "stack_two_late": "InputPassPriority"}


class KindTests(unittest.TestCase):
    def test_kind_of(self):
        self.assertEqual(ft.kind_of("InputPassPriority"), "priority")
        self.assertEqual(ft.kind_of("InputPayManaOfCostPayment"), "pay")
        self.assertEqual(ft.kind_of("InputPayManaSomethingNew"), "pay", "every payment input, by prefix")
        self.assertIsNone(ft.kind_of(""))
        self.assertIsNone(ft.kind_of(None))
        self.assertIsNone(ft.kind_of("InputUnheardOf"))

    def test_the_kind_path_says_the_same_as_the_text_path(self):
        for name, cls in KIND_OF_FIXTURE.items():
            msg = (load_state(name).get("prompt") or {}).get("message", "")
            by_text = ft.prompt_view(msg, "Karl")
            by_kind = ft.prompt_view(msg, "Karl", kind=ft.kind_of(cls))
            self.assertEqual(by_kind, by_text, name)

    def test_changed_wording_still_reads_as_a_payment(self):
        msg = "Llanowar Elves - Creature 1 / 1\n\npay mana cost {G}"          # lower case, no colon
        self.assertFalse(ft.prompt_view(msg, "Karl")[0].startswith("Pay {G}"), "the text path alone does not recognise it")
        self.assertEqual(ft.prompt_view(msg, "Karl", kind="pay")[0], "Pay {G}  -  Llanowar Elves")

    def test_table_helpers_follow_the_kind(self):
        gui = make_gui("main1_start")
        st = copy.deepcopy(gui.state)
        st["input"] = "InputPayManaOfCostPayment"
        gui.session.inbox.put(st)
        frame(gui)
        self.assertTrue(gui.paying(), "kind says pay, although the text is a priority prompt")
        self.assertIsNone(gui.target_prompt())
        st = copy.deepcopy(st)
        st["input"] = "InputPassPriority"
        gui.session.inbox.put(st)
        frame(gui)
        self.assertFalse(gui.paying())

    def test_no_kind_keeps_the_old_behaviour(self):
        gui = make_gui("paying_mana")
        self.assertIsNone(gui.input_kind())
        self.assertTrue(gui.paying())


# ---- 5. "Forge has not answered" --------------------------------------------------------------------------------------------------

class QuietTests(unittest.TestCase):
    def setUp(self):
        # never write into the real crash_log.txt (found on Karl's PC 2026-09-24: six "Forge not answering" entries from test runs)
        patcher = mock.patch.object(ft.crashlog, "note")
        self.note = patcher.start()
        self.addCleanup(patcher.stop)

    def make(self):
        gui = make_gui("main1_start")
        gui._hook_session()
        return gui

    def test_the_banner_needs_five_quiet_seconds(self):
        gui = self.make()
        t = time.monotonic()
        gui.session.last_rx = t - 1
        gui.session.on_send({"c": "ok"})
        t = gui._last_cmd_at
        self.assertFalse(gui.not_responding(t + 4.0))
        self.assertTrue(gui.not_responding(t + 6.0))

    def test_a_ping_goes_out_first_and_is_not_journaled(self):
        gui = self.make()
        gui.session.last_rx = time.monotonic() - 1
        gui.session.on_send({"c": "ok"})
        t = gui._last_cmd_at
        gui.track_quiet(t + 1.0)
        self.assertEqual(gui.session.commands("flush"), [])
        gui.track_quiet(t + 3.0)
        self.assertEqual(len(gui.session.commands("flush")), 1)
        gui.track_quiet(t + 4.0)
        self.assertEqual(len(gui.session.commands("flush")), 1, "one ping per command")
        gui.session.on_send({"c": "flush"})
        self.assertEqual(gui._last_cmd_at, t, "the ping does not count as a new command")

    def test_the_crash_log_gets_one_line(self):
        gui = self.make()
        gui.session.last_rx = time.monotonic() - 1
        gui.session.on_send({"c": "ok"})
        t = gui._last_cmd_at
        gui.track_quiet(t + 6.0)
        gui.track_quiet(t + 7.0)
        self.assertEqual(self.note.call_count, 1)

    def test_any_message_clears_it(self):
        gui = self.make()
        gui.session.last_rx = time.monotonic() - 1
        gui.session.on_send({"c": "ok"})
        t = gui._last_cmd_at
        gui.session.handle({"t": "log", "entries": []})
        self.assertFalse(gui.not_responding(t + 6.0))

    def test_ai_thinking_is_not_a_hang(self):
        gui = self.make()
        st = copy.deepcopy(gui.state)
        st["prompt"]["message"] = "Waiting for opponent..."
        gui.session.state = st
        gui.session.last_rx = time.monotonic() - 1
        gui.session.on_send({"c": "ok"})
        self.assertFalse(gui.not_responding(gui._last_cmd_at + 6.0))

    def test_the_banner_draws_at_every_size(self):
        for size, scale in (((900, 600), 1.0), ((1360, 840), 1.0), ((1920, 1080), 2.0)):
            gui = make_gui("main1_start", size, scale)
            gui._hook_session()
            gui.session.last_rx = time.monotonic() - 10
            gui._last_cmd_at = time.monotonic() - 6
            self.assertTrue(gui.not_responding())
            gui._force_draw = True
            frame(gui, 1)


# ---- 6. the replayer follows question numbers --------------------------------------------------------------------------------------

class ReplayNumberTests(unittest.TestCase):
    def test_it_waits_for_the_question_then_sends_the_click_unchanged(self):
        class S:
            def __init__(self):
                self.ready, self.state, self.state_version, self.exited, self.fatal = True, {"inputSeq": 1, "prompt": {}}, 0, False, None
                self.sent, self.requests, self.polls = [], [], 0

            def poll(self):
                self.polls += 1
                if self.polls == 20:                   # the engine reaches question 3 a moment later
                    self.state = {"inputSeq": 3, "prompt": {}}
                    self.state_version += 1

            def send(self, **cmd):
                self.sent.append((dict(cmd), self.state["inputSeq"]))
        s = S()
        r = replay.Replayer(s, [{"c": "ok", "at": 3}], settle=0.02, wait_limit=3.0)
        self.assertTrue(r.run(), r.diverged)
        self.assertEqual(s.sent, [({"c": "ok", "at": 3}, 3)], "sent once the engine was at question 3, with its number")

    def test_a_click_for_a_question_already_gone_is_sent_anyway(self):
        """The bridge drops it, exactly as it did in the original game; the replayer does not wait for a button."""
        class S:
            ready, state, state_version, exited, fatal, requests = True, {"inputSeq": 8, "prompt": {}}, 0, False, None, []

            def __init__(self):
                self.sent = []

            def poll(self):
                pass

            def send(self, **cmd):
                self.sent.append(cmd)
        s = S()
        r = replay.Replayer(s, [{"c": "ok", "at": 5}], settle=0.02, wait_limit=1.0)
        self.assertTrue(r.run(), r.diverged)
        self.assertEqual(s.sent, [{"c": "ok", "at": 5}])


# ---- 7. live: the real engine ----------------------------------------------------------------------------------------------------


def kinnan_decks(tmp):
    from deck_loader import load_deck
    c, d = load_deck(os.path.join(ROOT, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt"))
    return (fc.write_deck_file(os.path.join(tmp, "player.dck"), c, d, "Player"),
            fc.write_deck_file(os.path.join(tmp, "opponent1.dck"), c, d, "Opponent 1"))


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r22_live_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def to_first_priority(self, s):
        from card_check import next_move
        mem, t0 = {}, time.time()
        while time.time() - t0 < 120:
            s.poll()
            st = s.state
            if st and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                return
            mv = next_move(st, list(s.requests), mem) if st else None
            if mv:
                if mv[0] == "answer":
                    s.answer(mv[1], mv[2])
                elif mv[0] == "ok":
                    s.ok()
                time.sleep(0.3)
            time.sleep(0.05)
        self.fail("never reached a priority prompt")

    def test_combat_events_name_attacker_and_blocker(self):
        """Set up two AI attackers and one blocker of mine, block one, and read the events: protocol 2, the block pair's order (this
        settled the spec's UNVERIFIED a/b question: attacker first), combat damage to me, the life change, the blocker dying,
        and clicks sent for an old question are dropped and reported."""
        me, opp = kinnan_decks(self.tmp)
        s = fc.ForgeSession(me, [opp], name="Karl", seed=5, dev=True).start()
        s.stderr_path = os.path.join(self.tmp, "e.log")
        try:
            self.to_first_priority(s)
            self.assertEqual(s.protocol, 2)
            self.assertIsNotNone(s.state.get("inputSeq"))
            s.setup(["humanlife=40", "ailife=40", "activeplayer=ai", "activephase=MAIN1", "turn=5", "removesummoningsickness=true",
                     "humanhand=", "humanbattlefield=Llanowar Elves;Forest", "aihand=", "aibattlefield=Grizzly Bears;Runeclaw Bear"])
            time.sleep(1.0)
            t0, blocked, elves = time.time(), False, None
            while time.time() - t0 < 60:
                s.poll()
                st = s.state or {}
                if s.fatal or s.exited:
                    break
                if s.requests:
                    r = s.requests[0]
                    s.answer(r, True if r["kind"] == "confirm" else (None if r["kind"] == "assign" else
                                                                     list(range(max(r.get("min", 1), 1 if r["kind"] == "choose" else 0)))))
                elif st.get("input") == "InputBlock" and not blocked:
                    mine = next(p for p in st["players"] if p["id"] == st["me"])
                    elves = next(c for c in mine["zones"]["battlefield"] if c["name"] == "Llanowar Elves")
                    s.click_card(elves["id"])
                    blocked = True
                    time.sleep(0.5)
                    s.poll()
                    s.ok()
                    s.ok()                               # a double click: the second answers a question that is gone
                elif ((st.get("prompt") or {}).get("ok") or {}).get("enabled") and st.get("input") != "InputBlock":
                    s.ok()
                    time.sleep(0.2)
                evs = [e for e, _ in s.events]
                if blocked and any(e["kind"] == "zone" and e.get("to") == "Graveyard" for e in evs):
                    break
                time.sleep(0.05)
            evs = [e for e, _ in s.events]
            names = {}
            for p in (s.state or {}).get("players", []):
                for z in p["zones"].values():
                    for c in z:
                        names[c["id"]] = c.get("name")
            blocks = [e for e in evs if e["kind"] == "block"]
            self.assertTrue(blocks, "a block event")
            pairs = blocks[-1]["pairs"]
            self.assertEqual(len(pairs), 1, "the unblocked Runeclaw Bear is not a pair")
            # by id: the name lookup reads the board at the END of the test, where the dead Elves can show no name
            self.assertEqual(pairs[0]["blocker"], elves["id"], "the blocker is my Llanowar Elves")
            self.assertEqual(names.get(pairs[0]["attacker"]), "Grizzly Bears", "the attacker comes first")
            self.assertTrue(any(e["kind"] == "damage_player" and e.get("combat") and e["amount"] == 2 for e in evs))
            self.assertTrue(any(e["kind"] == "life" and e["old"] == 40 and e["new"] == 38 for e in evs))
            self.assertTrue(any(e["kind"] == "zone" and e.get("card") == elves["id"] and e.get("to") == "Graveyard" for e in evs))
            seqs = [e["seq"] for e in evs]
            self.assertEqual(seqs, list(range(seqs[0], seqs[0] + len(seqs))), "no gaps")
            self.assertTrue(s.dropped, "the double click's second OK was dropped, and the bridge said so")
        finally:
            s.close()

    def test_a_land_played_is_a_zone_event(self):
        me, opp = kinnan_decks(self.tmp)
        s = fc.ForgeSession(me, [opp], name="Karl", seed=5, dev=True).start()
        try:
            self.to_first_priority(s)
            s.setup(["humanhand=Forest", "activeplayer=human", "activephase=MAIN1"])
            t0, forest = time.time(), None
            while time.time() - t0 < 20 and forest is None:
                s.poll()
                me_p = s.me() if s.state else None
                forest = next((c for c in (me_p or {}).get("zones", {}).get("hand", []) if c.get("name") == "Forest"), None)
                time.sleep(0.05)
            self.assertIsNotNone(forest)
            s.click_card(forest["id"])
            t0 = time.time()
            while time.time() - t0 < 10:
                s.poll()
                if any(e["kind"] == "zone" and e.get("card") == forest["id"] and e.get("to") == "Battlefield" for e, _ in s.events):
                    break
                time.sleep(0.05)
            ev = next(e for e, _ in s.events if e["kind"] == "zone" and e.get("card") == forest["id"] and e.get("to") == "Battlefield")
            self.assertEqual(ev["from"], "Hand")
        finally:
            s.close()

    def test_a_hasty_player_game_resumes_exactly(self):
        """Round 21's resume test with the impatient bot (a click every 30 ms whether or not the screen has changed): with question
        numbers the stale clicks are dropped the same way in the original and in the replay."""
        from tests.forge_bot import Bot
        me, opp = kinnan_decks(self.tmp)
        s = fc.ForgeSession(me, [opp], name="Karl", seed=4242).start()
        j = journal.GameJournal(os.path.join(self.tmp, "saves"))
        j.start(4242, "Karl", {}, "test", time.time())
        s.on_send = lambda cmd: cmd.get("c") != "quit" and j.command(cmd, time.time())
        bot = Bot(s)
        t0 = time.time()
        while time.time() - t0 < 90 and len(s.sent_all) < 20:
            s.poll()
            bot.step()
            time.sleep(0.03)
        replay.Replayer(s, []).settled()
        before = replay.summary(s.state)
        s.close()
        start, cmds = journal.unfinished(os.path.join(self.tmp, "saves"))
        self.assertTrue(all("at" in c for c in cmds if c.get("c") in fc.NUMBERED))
        s2 = fc.ForgeSession(me, [opp], name="Karl", seed=start["seed"]).start()
        try:
            rp = replay.Replayer(s2, cmds)
            self.assertTrue(rp.run(), rp.diverged)
            after = replay.summary(s2.state)
        finally:
            s2.close()
        self.assertEqual(replay.differences(before, after), [])


if __name__ == "__main__":
    unittest.main()
