# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PRI1 (Karl, 8-9 Oct 2026): Speed - Fast or Slow - in Cog > GAME.

Karl's note: "Add fast/slow option to the auto tapper in settings"; 8 Oct: "fast as the sped up version of the auto tapper, and
slow as manually tapping, manually passing priority for each step, even when you can't do anything. The way we used to have it."
His answers of 9 Oct: Slow = the old stops (as before patch 43: my Main 1, attackers, Main 2 and end step; each opponent's upkeep,
attackers and end step; anything on the stack), auto-pass off, nothing passed for me; payment stays as it is in both speeds.

  SourceTests    the bridge: Passing.slow (per seat), Slow's own stop pair, the "speed" command, a guest may send it, the jar
  ClientTests    forge_client.set_speed
  TableTests     the setting (missing / unknown = Fast), what is sent and when (only Slow sends anything), the journal, the table
                 playing the old way in Slow (Enter, Ctrl, the Skip window and button), auto_pass never rewritten
  CogTests       Speed | New game... | Concede... in one row, inside the pop-up at every size, labels not clipped, a click toggles
  LiveTests      real Forge: the old stops, an opponent's trigger with no answer stops me, a stop with nothing to do, payment asked
                 in both speeds, switching mid-game both ways, auto-pass back after Slow, a Slow game resumed from its journal
                 is Slow, and an online guest's own seat
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame

pygame.init()

import forge_client as fc
import forge_settings as fset
import forge_table as ft
import journal
import tests.live as live
from tests.forge_fake import FakeSession, StubStore
from tests.test_patch43 import DECK, ISLANDS5, KAMBAL, priority_state, stack_names, trigger_item
from tests.test_settings_report import SIZES, open_cog, popup_point
from tests.test_forge_table import click, frame, make_gui

BRIDGE = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
OLD_MINE = ["COMBAT_DECLARE_ATTACKERS", "END_OF_TURN", "MAIN1", "MAIN2"]
OLD_THEIRS = ["COMBAT_DECLARE_ATTACKERS", "END_OF_TURN", "UPKEEP"]


def read(name):
    with open(os.path.join(BRIDGE, name), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------------------------------
# 1. the bridge's source
# ---------------------------------------------------------------------------------------------------------------------
class SourceTests(unittest.TestCase):
    def test_slow_is_per_seat_and_decides_like_the_old_way(self):
        src = read("Passing.java")
        self.assertIn("volatile boolean slow = false;", src)
        self.assertIn("static volatile boolean classic = false;", src)          # the card check's engine-wide switch stays
        decide = src[src.index("Decision decide("):src.index("boolean fullControlAt(")]
        # full control first (more stops), then Slow (Forge decides, as before patch 43), then the patch-43 rules
        order = ["syncAutoPass(pc.getYieldController(), full)", "if (full)", "if (slow)", "passTurn == turn", "hasAnswer(me, top)"]
        at = [decide.index(o) for o in order]
        self.assertEqual(at, sorted(at))
        slow = decide[decide.index("if (slow)"):decide.index("passTurn == turn")]
        self.assertIn("return Decision.DEFER;", slow)
        self.assertNotIn("PASS", slow)

    def test_auto_pass_is_off_in_slow_but_the_wish_is_kept(self):
        src = read("Passing.java")
        sync = src[src.index("void syncAutoPass("):src.index("// ---- \"do I hold an answer")]
        self.assertIn("autoPass && !full && !classicNow()", sync)
        self.assertIn("RESPECTS_INTERRUPTS, String.valueOf(classicNow())", sync)
        self.assertNotIn("autoPass =", sync)                  # the player's wish is never rewritten
        self.assertIn("return classic || slow;", src)
        self.assertIn('o.addProperty("slow", slow);', src)
        self.assertIn('o.addProperty("classic", classicNow());', src)

    def test_slow_has_its_own_pair_of_old_stops(self):
        src = read("BridgeGui.java")
        init = src[src.index("// Round PRI1: Slow (one seat"):src.index("/** Round PRI1: the stops in use")]
        self.assertIn("slowMine, PhaseType.MAIN1, PhaseType.COMBAT_DECLARE_ATTACKERS, PhaseType.MAIN2,", init)
        self.assertIn("PhaseType.END_OF_TURN);", init)
        self.assertIn("slowTheirs, PhaseType.UPKEEP, PhaseType.COMBAT_DECLARE_ATTACKERS, PhaseType.END_OF_TURN);", init)
        stop_set = src[src.index("java.util.Set<PhaseType> stopSet(boolean mine)"):src.index("void setStops(")]
        self.assertIn("if (passing.slow)", stop_set)
        self.assertIn("java.util.Set<PhaseType> target = stopSet(mine);", src)
        self.assertIn("return !stopSet(mine).contains(phase);", src)
        snap = read("Snapshot.java")
        self.assertIn("gui.stopSet(true)", snap)
        self.assertIn("gui.stopSet(false)", snap)
        self.assertNotIn("gui.stopsMine", snap)

    def test_the_speed_command_and_a_guest_may_send_it(self):
        main = read("Main.java")
        case = main[main.index('case "speed":'):main.index('case "alwaysstop":')]
        self.assertIn('gui.passing.slow = cmd.has("mode") && "slow".equals(cmd.get("mode").getAsString());', case)
        self.assertIn("gui.passing.syncAutoPass(", case)
        net = read("NetHost.java")
        allowed = net[net.index("REMOTE_ALLOWED = Set.of("):]
        allowed = allowed[:allowed.index(";")]
        self.assertIn('"speed"', allowed)

    def test_the_jar_is_built_from_these_sources(self):
        import setup_forge
        import zipfile
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            stamp = z.read(setup_forge.BRIDGE_STAMP).decode("ascii").strip()
        self.assertEqual(stamp, setup_forge.bridge_source_hash())


class VersionTests(unittest.TestCase):
    def test_the_version_karl_reserved(self):
        import updater
        import version
        self.assertGreaterEqual(updater.parse_version(version.VERSION), updater.parse_version("0.28.53.3"))


# ---------------------------------------------------------------------------------------------------------------------
# 2. the client
# ---------------------------------------------------------------------------------------------------------------------
class ClientTests(unittest.TestCase):
    def test_set_speed(self):
        s = FakeSession()
        s.set_speed("slow")
        s.set_speed("fast")
        s.set_speed("anything else")
        self.assertEqual(s.sent, [{"c": "speed", "mode": "slow"}, {"c": "speed", "mode": "fast"}, {"c": "speed", "mode": "fast"}])


# ---------------------------------------------------------------------------------------------------------------------
# 3. the table
# ---------------------------------------------------------------------------------------------------------------------
class HookedSession(FakeSession):
    """A FakeSession whose commands also reach on_send, as a real one's do (that is how the journal gets them)."""

    def send(self, **cmd):
        super().send(**cmd)
        if self.on_send is not None:
            self.on_send(cmd)
        return True


def slow_state(slow=False, classic=None, **kw):
    return priority_state(passing={"slow": slow, "classic": slow if classic is None else classic}, **kw)


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = os.path.join(self.tmp.name, "settings.json")

    def make(self, state=None, settings=None, ready="smart", session_cls=FakeSession, saves=None, sync=True):
        if settings is not None:
            with open(self.settings, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        state = state if state is not None else slow_state()
        session = session_cls(state)
        if ready:
            session.handle({"t": "ready", "protocol": 2, "passing": ready})
        gui = ft.ForgeTable(session, StubStore(), settings_path=self.settings, window_size=(1360, 840), saves_dir=saves)
        if sync:
            gui.sync()
            gui.render()
        return gui, session

    def saved(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def key(self, gui, k, mod=0, up=False):
        gui.handle_event(pygame.event.Event(pygame.KEYUP if up else pygame.KEYDOWN, key=k, mod=mod, unicode=""))

    # -- the setting --
    def test_missing_and_unknown_are_fast_and_slow_is_kept(self):
        for value, want in ((None, "fast"), ("turbo", "fast"), (1, "fast"), ("fast", "fast"), ("slow", "slow")):
            with self.subTest(value=value):
                data = {"passing_version": 43}
                if value is not None:
                    data["speed"] = value
                gui, _s = self.make(settings=data, sync=False)
                self.assertEqual(gui.speed, want)

    def test_toggling_saves_and_keeps_the_rest_of_the_file(self):
        gui, _s = self.make(settings={"passing_version": 43, "auto_pass": False, "always_stop": ["Kambal, Consul of Allocation"],
                                      "something_else": 7})
        gui.toggle_speed()
        data = self.saved()
        self.assertEqual(data["speed"], "slow")
        self.assertEqual(data["something_else"], 7)
        self.assertFalse(data["auto_pass"], "the player's own auto-pass choice is never rewritten")
        self.assertEqual(data["always_stop"], ["Kambal, Consul of Allocation"])
        gui.toggle_speed()
        self.assertEqual(self.saved()["speed"], "fast")
        self.assertFalse(self.saved()["auto_pass"])
        self.assertFalse(gui.auto_pass)

    # -- what is sent, and when --
    def test_fast_sends_nothing_new(self):
        _gui, s = self.make(settings={"passing_version": 43})
        self.assertEqual(s.commands("speed"), [])

    def test_slow_is_sent_once_at_the_first_snapshot(self):
        gui, s = self.make(settings={"passing_version": 43, "speed": "slow"})
        self.assertEqual(s.commands("speed"), [{"c": "speed", "mode": "slow"}])
        for _ in range(3):                                         # the bridge hasn't answered yet: still once
            gui.sync()
        self.assertEqual(len(s.commands("speed")), 1)

    def test_nothing_is_sent_when_the_seat_already_plays_slow(self):
        _gui, s = self.make(state=slow_state(True), settings={"passing_version": 43, "speed": "slow"})
        self.assertEqual(s.commands("speed"), [])

    def test_an_older_bridge_a_classic_engine_and_a_spectator_get_nothing(self):
        old = priority_state()                                     # patch 43's bridge: no "slow" in the snapshot
        cases = {"older bridge": (old, "smart"), "card check (classic engine)": (slow_state(False, classic=True), "classic")}
        for what, (state, ready) in cases.items():
            with self.subTest(what):
                _gui, s = self.make(state=state, ready=ready, settings={"passing_version": 43, "speed": "slow"})
                self.assertEqual(s.commands("speed"), [])
        state = slow_state()
        state["spectator"] = True
        gui, s = self.make(state=state, settings={"passing_version": 43, "speed": "slow"}, sync=False)
        s.spectator = True
        gui.sync()
        self.assertEqual(s.commands("speed"), [])

    def test_an_online_guest_sends_it_too(self):
        # A guest's table never sees the host engine's "ready" line; its snapshots carry the passing state.
        _gui, s = self.make(ready=None, settings={"passing_version": 43, "speed": "slow"})
        self.assertEqual(s.commands("speed"), [{"c": "speed", "mode": "slow"}])

    def test_switching_mid_game_is_sent_at_once_both_ways(self):
        gui, s = self.make(settings={"passing_version": 43})
        gui.toggle_speed()
        self.assertEqual(s.commands("speed"), [{"c": "speed", "mode": "slow"}])
        self.assertIn("next priority", gui.toast[0])
        s.handle(slow_state(True))
        gui.sync()
        gui.toggle_speed()
        self.assertEqual(s.commands("speed")[-1], {"c": "speed", "mode": "fast"})

    def test_a_new_game_gets_the_setting_again(self):
        gui, s = self.make(settings={"passing_version": 43, "speed": "slow"})
        self.assertEqual(len(s.commands("speed")), 1)
        s2 = FakeSession(slow_state(False))                          # a new game: a fresh engine, which starts Fast
        s2.handle({"t": "ready", "protocol": 2, "passing": "smart"})
        gui.session = s2
        gui.reset_game()
        gui.sync()
        self.assertEqual(s2.commands("speed"), [{"c": "speed", "mode": "slow"}])

    def test_a_command_the_seat_never_took_is_sent_again(self):
        from unittest import mock
        gui, s = self.make(settings={"passing_version": 43, "speed": "slow"})
        self.assertEqual(len(s.commands("speed")), 1)
        later = time.monotonic() + ft.SPEED_RESEND + 1
        with mock.patch.object(ft.time, "monotonic", return_value=later):
            gui.sync()
            gui.sync()
        self.assertEqual(len(s.commands("speed")), 2, "once more after SPEED_RESEND, not every frame")
        s.handle(slow_state(True))                                    # taken: nothing more
        with mock.patch.object(ft.time, "monotonic", return_value=later + 10):
            gui.sync()
        self.assertEqual(len(s.commands("speed")), 2)

    def test_the_journal_keeps_the_command_and_names_the_speed(self):
        saves = os.path.join(self.tmp.name, "saves")
        gui, s = self.make(settings={"passing_version": 43, "speed": "slow"}, session_cls=HookedSession, saves=saves, sync=False)
        s.deck_path = os.path.join(self.tmp.name, "player.dck")
        with open(s.deck_path, "w", encoding="utf-8") as f:
            f.write("[metadata]\nName=Player\n")
        s.opponent_paths = []
        s.seed = 99
        gui.start_journal()
        gui.sync()
        full = journal.read_full(gui.journal.path)
        self.assertEqual(full["start"].get("speed"), "slow")
        self.assertIn({"c": "speed", "mode": "slow"}, [c for c in full["commands"]])
        gui.journal.close()

    def test_a_fast_journal_has_no_speed(self):
        saves = os.path.join(self.tmp.name, "saves")
        j = journal.GameJournal(saves)
        j.start(1, "Karl", {"player.dck": "x"}, "code", 0.0, speed="fast")
        j.close()
        self.assertNotIn("speed", journal.read_full(j.path)["start"])

    # -- the table plays the old way in Slow --
    def test_slow_plays_the_old_way_at_the_table(self):
        gui, s = self.make(state=slow_state(True), settings={"passing_version": 43, "speed": "slow"})
        self.assertFalse(gui.smart_passing())
        self.key(gui, pygame.K_RETURN)                              # Enter is OK (one pass), not "pass until something"
        self.assertEqual(s.commands("ok"), [{"c": "ok"}])
        self.assertEqual(s.commands("yield"), [])
        self.key(gui, pygame.K_LCTRL)                               # a Ctrl tap does nothing
        self.key(gui, pygame.K_LCTRL, up=True)
        self.assertEqual(s.commands("fullcontrol"), [])

    def test_fast_still_has_the_patch_43_keys(self):
        gui, s = self.make(state=slow_state(False), settings={"passing_version": 43})
        self.assertTrue(gui.smart_passing())
        self.key(gui, pygame.K_RETURN)
        self.assertEqual(s.commands("ok"), [])

    def test_the_skip_window_in_slow(self):
        gui, _s = self.make(state=slow_state(True, stack=[trigger_item(mine=False)]),
                            settings={"passing_version": 43, "speed": "slow"})
        labels = [o[0] for o in gui.skip_options()]
        self.assertIn("Speed is Slow: switch to Fast", labels)
        self.assertFalse(any(lab.startswith("Auto-pass") for lab in labels), labels)
        self.assertFalse(any(lab.startswith("Full control") for lab in labels), labels)
        gui.open_skip()
        self.assertIn("Speed is Slow", gui.modal.text)
        gui2, _s2 = self.make(state=slow_state(False), settings={"passing_version": 43})
        self.assertTrue(any(lab.startswith("Auto-pass") for lab in (o[0] for o in gui2.skip_options())))

    def test_a_crash_report_says_slow_and_fast_adds_nothing(self):
        gui, _s = self.make(state=slow_state(True), settings={"passing_version": 43, "speed": "slow"})
        self.assertIn("Speed: Slow in the settings; my seat plays Slow", gui.crash_context())
        fast, _s2 = self.make(state=slow_state(False), settings={"passing_version": 43})
        self.assertFalse(any(line.startswith("Speed") for line in fast.crash_context()))

    def test_the_skip_button_doesnt_say_auto_in_slow(self):
        gui, _s = self.make(state=slow_state(True), settings={"passing_version": 43, "speed": "slow"})
        self.assertTrue(gui.auto_pass, "the player's own auto-pass wish is on")
        self.assertEqual(gui.skip_label(), "Skip...")
        self.assertIn("skip", [d.get("name") for _r, k, d in gui.hits if k == "button"])
        fast, _s2 = self.make(state=slow_state(False), settings={"passing_version": 43})
        self.assertEqual(fast.skip_label(), "Skip (auto)")


# ---------------------------------------------------------------------------------------------------------------------
# 4. the cog
# ---------------------------------------------------------------------------------------------------------------------
class CogTests(unittest.TestCase):
    MORE_SIZES = (((1920, 1080), 2.0), ((1366, 768), 1.0), ((1280, 720), 1.25), ((4096, 1949), 1.75))

    def test_speed_new_game_and_concede_share_the_game_row_at_every_size(self):
        for size, scale in SIZES + self.MORE_SIZES:
            for speed in ("fast", "slow"):
                with self.subTest(size=size, scale=scale, speed=speed):
                    gui = make_gui("main1_start", size=size, scale=scale)
                    gui.speed = speed
                    open_cog(gui)
                    frame(gui)
                    pop = gui.overlay
                    rects = {n: r for r, n in pop.buttons}
                    names = [n for _r, n in pop.buttons]
                    self.assertEqual(names[names.index("speed"):names.index("speed") + 3], ["speed", "newgame", "concede"])
                    sp, ng, co = rects["speed"], rects["newgame"], rects["concede"]
                    self.assertEqual(sp.y, ng.y)
                    self.assertEqual(sp.y, co.y)
                    self.assertLess(sp.right, ng.x)
                    self.assertLess(ng.right, co.x)
                    for r in (sp, ng, co):
                        self.assertTrue(pop.rect.contains(r), r)
                    label = f"Speed: {ft.SPEED_LABELS[speed]}"
                    for text, r in ((label, sp), ("New game...", ng), ("Concede...", co)):
                        self.assertLessEqual(gui.button_width(text, "small"), r.w, (text, r))

    def test_the_pop_up_still_fits_the_window(self):
        for size, scale in SIZES + self.MORE_SIZES:
            with self.subTest(size=size, scale=scale):
                gui = make_gui("main1_start", size=size, scale=scale)
                open_cog(gui)
                frame(gui)
                self.assertTrue(gui.screen.get_rect().contains(gui.overlay.rect))
                for rect, name in gui.overlay.buttons:
                    self.assertTrue(gui.overlay.rect.contains(rect), (name, rect))

    def test_a_click_toggles_it_and_the_label_follows(self):
        gui = make_gui("main1_start")
        open_cog(gui)
        click(gui, popup_point(gui, "speed"))
        self.assertEqual(gui.speed, "slow")
        frame(gui)
        self.assertIsInstance(gui.overlay, fset.SettingsPopup, "the pop-up stays open, so you see it change")
        click(gui, popup_point(gui, "speed"))
        self.assertEqual(gui.speed, "fast")

    def test_the_help_names_it(self):
        cog = next(t for k, t in ft.HELP_LINES if k.startswith("Cog"))
        self.assertIn("Speed (Fast", cog)
        self.assertIn("Slow", cog)


# ---------------------------------------------------------------------------------------------------------------------
# 5. live: real Forge
# ---------------------------------------------------------------------------------------------------------------------
NOTHING_TO_DO = "humanhand=Darksteel Colossus"          # 11 mana: in hand, never castable here


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    def setUp(self):
        import card_check as cc
        self.cc = cc
        self.chk = cc.Checker(DECK, classic_stops=False, say=lambda *a: None)
        self.chk.start()
        self.addCleanup(self.chk.stop)
        self.s = self.chk.s
        self.passed0 = len(self.s.passed)

    # the same helpers as patch 43's live tests
    setup = __import__("tests.test_patch43", fromlist=["LiveTests"]).LiveTests.setup
    watch = __import__("tests.test_patch43", fromlist=["LiveTests"]).LiveTests.watch
    at_main1 = __import__("tests.test_patch43", fromlist=["LiveTests"]).LiveTests.at_main1
    cast = __import__("tests.test_patch43", fromlist=["LiveTests"]).LiveTests.cast
    hand = __import__("tests.test_patch43", fromlist=["LiveTests"]).LiveTests.hand

    def speed(self, mode):
        self.s.set_speed(mode)
        end = time.time() + 5
        while time.time() < end and bool(self.s.passing_state().get("slow")) != (mode == "slow"):
            self.s.poll()
            time.sleep(0.05)
        self.assertEqual(bool(self.s.passing_state().get("slow")), mode == "slow", "the bridge didn't take the speed")

    def stops(self):
        return {k: sorted(v) for k, v in self.s.state["stops"].items()}

    def test_slow_has_the_old_stops_and_fast_patch_43s(self):
        self.setup(ISLANDS5, "humanhand=Opt", "aibattlefield=Plains")
        self.at_main1()
        self.assertEqual(self.stops(), {"mine": ["MAIN1", "MAIN2"], "theirs": ["END_OF_TURN"]})
        self.speed("slow")
        self.assertEqual(self.stops(), {"mine": OLD_MINE, "theirs": OLD_THEIRS})
        self.assertTrue(self.s.passing_state().get("classic"), "the table plays the old way")
        self.speed("fast")
        self.assertEqual(self.stops(), {"mine": ["MAIN1", "MAIN2"], "theirs": ["END_OF_TURN"]})
        self.assertFalse(self.s.passing_state().get("classic"))

    def test_slow_stops_at_an_opponents_trigger_i_cant_answer(self):
        self.speed("slow")
        self.setup(ISLANDS5, "humanhand=Divination;Opt", KAMBAL)
        st = self.cast("Divination", seconds=6)
        self.assertEqual(stack_names(st)[:1], [("Kambal, Consul of Allocation", "T")], "Slow: the trigger stops me")
        self.assertEqual(self.s.me()["life"], 40)
        self.assertEqual(self.s.passed[self.passed0:], [], "nothing passed for me")

    def two_turns(self, mode):
        """Every priority question over the rest of my turn and the opponent's, with no card I could play (and no land to play):
        {(turn - this turn, phase, mine)} for the questions with an empty stack."""
        self.speed(mode)                                          # before the board: Fast would pass everything during the set-up
        self.setup("humanbattlefield=Island", NOTHING_TO_DO, "aibattlefield=Plains")
        turn, log = self.s.state.get("turn"), []
        self.watch(25, until=lambda st: st.get("turn", 0) > turn + 1, auto_ok=True, log=log)
        return {(t - turn, ph, mine) for t, ph, mine, stack in log if not stack}, log

    def test_slow_stops_with_nothing_to_do_where_fast_passes(self):
        """My Main 2 and end step, the opponent's upkeep and end step, with nothing I could play: Slow asks, Fast doesn't."""
        fast, fast_log = self.two_turns("fast")
        self.new_engine()
        slow, slow_log = self.two_turns("slow")
        for want in ((0, "MAIN2", True), (0, "END_OF_TURN", True), (1, "UPKEEP", False), (1, "END_OF_TURN", False)):
            self.assertIn(want, slow, slow_log)
            self.assertNotIn(want, fast, fast_log)
        self.assertEqual({ph for _t, ph, _m in slow} - set(OLD_MINE + OLD_THEIRS), set(), slow_log)
        self.assertEqual({ph for _t, ph, mine in slow if not mine} - set(OLD_THEIRS), set(), slow_log)

    def new_engine(self):
        """A second engine in the same test (Forge's set-up doesn't clear the stack or the turn)."""
        self.chk.stop()
        self.chk = self.cc.Checker(DECK, classic_stops=False, say=lambda *a: None)
        self.chk.start()
        self.addCleanup(self.chk.stop)
        self.s = self.chk.s
        self.passed0 = len(self.s.passed)

    def test_casting_asks_me_to_pay_in_both_speeds(self):
        for mode in ("fast", "slow"):
            with self.subTest(mode=mode):
                if mode == "slow":
                    self.new_engine()
                self.speed(mode)
                self.setup(ISLANDS5, "humanhand=Divination;Opt", "aibattlefield=Plains")
                self.at_main1()
                card = next(c for c in self.s.me()["zones"]["hand"] if c["name"] == "Divination")
                self.s.click_card(card["id"])
                end, asked = time.time() + 8, None
                while time.time() < end and asked is None:
                    self.s.poll()
                    st = self.s.state or {}
                    if st.get("asking") and (st.get("input") or "").startswith("InputPayMana"):
                        asked = st
                    time.sleep(0.03)
                self.assertIsNotNone(asked, f"{mode}: casting opened the payment step")
                self.assertEqual(sum(1 for c in self.s.me()["zones"]["battlefield"] if c.get("tapped")), 0, "nothing tapped yet")

    def test_switching_mid_game_both_ways(self):
        log = []
        self.setup(ISLANDS5, "humanhand=Opt", "aibattlefield=Plains")     # Opt: Fast stops at my Main 1 too
        self.at_main1()
        turn, seq = self.s.state.get("turn"), self.s.state.get("inputSeq")
        self.speed("slow")                                      # at a priority: from the next one
        self.s.ok()
        self.watch(25, until=lambda st: st.get("turn", 0) > turn + 1, auto_ok=True, log=log, after_seq=seq)
        self.assertIn("UPKEEP", {ph for t, ph, mine, _s in log if t == turn + 1 and not mine}, log)
        self.speed("fast")
        t2, seq2 = self.s.state.get("turn"), self.s.state.get("inputSeq")
        log2 = []
        self.s.ok()
        self.watch(25, until=lambda st: st.get("turn", 0) > t2 + 2, auto_ok=True, log=log2, after_seq=seq2)
        self.assertNotIn("UPKEEP", {ph for t, ph, mine, _s in log2 if not mine}, log2)

    def test_auto_pass_comes_back_after_slow(self):
        self.setup(ISLANDS5, "humanhand=Opt", "aibattlefield=Plains")
        self.at_main1()
        self.assertTrue(self.s.state["yield"].get("autoPass"), "Fast: auto-pass on (the default)")
        self.speed("slow")
        self.s.send(c="flush")
        end = time.time() + 3
        while time.time() < end and self.s.state["yield"].get("autoPass"):
            self.s.poll()
            time.sleep(0.05)
        self.assertFalse(self.s.state["yield"].get("autoPass"), "Slow: auto-pass off")
        self.speed("fast")
        self.s.send(c="flush")
        end = time.time() + 3
        while time.time() < end and not self.s.state["yield"].get("autoPass"):
            self.s.poll()
            time.sleep(0.05)
        self.assertTrue(self.s.state["yield"].get("autoPass"), "Fast again: the player's wish is back")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveResumeTests(unittest.TestCase):
    """A game played in Slow from its first snapshot, its engine dropped as a crash would, then resumed from the journal: the
    new engine is Slow again (the journal's "speed" command, replayed like any other) and the boards match."""

    def test_a_slow_game_resumes_slow(self):
        import replay
        from deck_loader import load_deck
        from tests.forge_bot import Bot
        tmp = tempfile.mkdtemp(prefix="pri1_resume_")
        self.addCleanup(shutil.rmtree, tmp, True)
        c, d = load_deck(os.path.join(BASE, "sample_decks", "stompy_goreclaw.txt"))
        me = fc.write_deck_file(os.path.join(tmp, "player.dck"), c, d, "Player")
        opp = fc.write_deck_file(os.path.join(tmp, "opponent1.dck"), c, d, "Opponent 1")
        s = fc.ForgeSession(me, [opp], name="Karl", seed=5150).start()
        s.stderr_path = os.path.join(tmp, "e1.log")
        j = journal.GameJournal(os.path.join(tmp, "saves"))
        with open(me, encoding="utf-8") as f1, open(opp, encoding="utf-8") as f2:
            j.start(5150, "Karl", {"player.dck": f1.read(), "opponent1.dck": f2.read()}, "test", time.time(), speed="slow")
        s.on_send = lambda cmd: cmd.get("c") != "quit" and j.command(cmd, time.time())
        t0 = time.time()
        while time.time() - t0 < 60 and not (s.state and "yield" in s.state):
            s.poll()
            time.sleep(0.03)
        s.set_speed("slow")                                       # what the table's track_yield sends at the first snapshot
        bot = Bot(s)
        acted_at, acted_version = 0.0, -1
        while time.time() - t0 < 120 and len(s.sent_all) < 16:
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
        self.assertTrue(s.passing_state().get("slow"))
        before = replay.summary(s.state)
        stops_before = {k: sorted(v) for k, v in s.state["stops"].items()}
        s.close()
        j.close()
        start, cmds = journal.unfinished(os.path.join(tmp, "saves"))
        self.assertEqual(start.get("speed"), "slow")
        self.assertIn({"c": "speed", "mode": "slow"}, cmds)
        s2 = fc.ForgeSession(me, [opp], name="Karl", seed=start["seed"]).start()
        s2.stderr_path = os.path.join(tmp, "e2.log")
        try:
            rp = replay.Replayer(s2, cmds)
            self.assertTrue(rp.run(), rp.diverged)
            after = replay.summary(s2.state)
            self.assertTrue(s2.passing_state().get("slow"), "the resumed game is Slow")
            self.assertEqual({k: sorted(v) for k, v in s2.state["stops"].items()}, stops_before)
        finally:
            s2.close()
        self.assertEqual(stops_before, {"mine": OLD_MINE, "theirs": OLD_THEIRS})
        self.assertEqual(replay.differences(before, after), [])


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTableTests(unittest.TestCase):
    """The whole path through the real table: Slow in settings.json reaches the seat at the first snapshot, the opponent's upkeep
    stops me, the journal holds the command, and Undo's rebuild (round UNDO1) comes back Slow."""

    def setUp(self):
        from unittest import mock
        from deck_loader import load_deck
        from tests.forge_bot import Bot
        self.tmp = tempfile.mkdtemp(prefix="pri1_table_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(mock.patch.object(ft, "DECK_DIR", os.path.join(self.tmp, "decks")))
        settings = os.path.join(self.tmp, "settings.json")
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"passing_version": 43, "speed": "slow", "tour_done": 99}, f)
        mine = load_deck(os.path.join(BASE, "sample_decks", "stompy_goreclaw.txt"))
        opp = load_deck(os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt"))
        self.gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), settings_path=settings,
                                 launcher=ft.Launcher(None, "Karl", seed=1234), saves_dir=os.path.join(self.tmp, "saves"))
        self.addCleanup(self.close)
        self.assertEqual(self.gui.speed, "slow")
        self.assertIsNone(self.gui.begin(mine, [opp]))
        self.bot = Bot(self.gui.session)

    from tests.test_undo1 import LiveRewindTests as _U
    close, tick_until, at_my_priority_with, rewind_to = _U.close, _U.tick_until, _U.at_my_priority_with, _U.rewind_to
    del _U

    def test_slow_from_the_settings_through_the_table_and_undos_rebuild(self):
        gui, seen = self.gui, []

        def note():
            st = gui.state or {}
            if st.get("asking") and st.get("input") == "InputPassPriority" and not st.get("stack"):
                seen.append((st.get("turn"), st.get("phase"), st.get("activePlayer") == st.get("me")))
            return False

        self.assertTrue(self.tick_until(lambda: gui.slow_now(), 120), "the table never made my seat Slow")
        self.assertEqual({k: sorted(v) for k, v in gui.state["stops"].items()}, {"mine": OLD_MINE, "theirs": OLD_THEIRS})
        cmds = journal.read_full(gui.journal.path)["commands"]
        self.assertIn({"c": "speed", "mode": "slow"}, cmds)
        self.assertEqual(journal.read_full(gui.journal.path)["start"].get("speed"), "slow")
        self.assertTrue(self.tick_until(lambda: note() or self.at_my_priority_with(2), 300), "never reached a turn of mine with 2 moments")
        self.assertTrue(any(ph == "UPKEEP" and not mine for _t, ph, mine in seen), seen)
        self.assertFalse(any(ph not in OLD_MINE + OLD_THEIRS for _t, ph, _m in seen), seen)
        point, _label, old = self.rewind_to(-1)
        self.assertIsNot(gui.session, old, gui.toast)
        self.assertTrue(gui.slow_now(), "the rebuilt game is Slow")
        self.assertEqual({k: sorted(v) for k, v in gui.state["stops"].items()}, {"mine": OLD_MINE, "theirs": OLD_THEIRS})


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveOnlineTests(unittest.TestCase):
    """Each human seat uses its own player's switch: a guest's Slow reaches the guest's seat only."""

    def test_a_guest_plays_slow_and_the_host_fast(self):
        import forge_net as fn
        from tests.test_mp2c import LiveTests as MP, deck_file, free_port, wait_for
        fc.sync_bridge()
        tmp = tempfile.mkdtemp(prefix="pri1_online_")
        self.addCleanup(shutil.rmtree, tmp, True)
        cert = fn.ensure_host_cert(tmp, fc.find_java())
        host_deck = deck_file(tmp, "typal_lathril.txt", "Lathril")
        guest_deck = deck_file(tmp, "spellslinger_veyran.txt", "Veyran")
        port = free_port()
        h = fc.HostSession(host_deck, "Karl", port, "pw-pri1", cert, os.path.join(tmp, "guest.dck"), upnp=False,
                           bind="127.0.0.1", code="hostcode", seed=7)
        h.stderr_path = os.path.join(tmp, "host.log")
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        with open(guest_deck, encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, "Sam", "pw-pri1", "Veyran", f.read(), fingerprint=cert.fingerprint,
                              code="guestcode")
        g.start()
        self.addCleanup(g.close)
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.state and g.state and "yield" in g.state, 60, pump=[h, g]))
        g.set_speed("slow")
        self.assertTrue(wait_for(lambda: g.passing_state().get("slow"), 10, pump=[h, g]), "the guest's seat took Slow")
        self.assertEqual(g.refused if hasattr(g, "refused") else [], [])
        wait_for(lambda: False, 1.0, pump=[h, g])
        self.assertFalse(h.passing_state().get("slow"), "the host's own seat is still Fast")
        self.assertEqual({k: sorted(v) for k, v in g.state["stops"].items()}, {"mine": OLD_MINE, "theirs": OLD_THEIRS})
        self.assertEqual({k: sorted(v) for k, v in h.state["stops"].items()}, {"mine": ["MAIN1", "MAIN2"], "theirs": ["END_OF_TURN"]})


if __name__ == "__main__":
    unittest.main()
