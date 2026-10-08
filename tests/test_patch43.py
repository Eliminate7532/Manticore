# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 43 (Karl, 5-6 Oct 2026): only the meaningful priority stops.

Karl's games had 685 of 936 commands (73%) be OK; night 12's soak seat got 175 priority stops a game. His decisions, one question
at a time (claude/CLICKS_AND_STATS_2026-10-05.md, section 0), built in java_bridge Passing.java and the table:

  SourceTests        the bridge's default stops, the passing rules, the commands, NetHost's allowed list, the jar
  ClientTests        forge_client: --classic-stops, the "ready" line, "passed" lines, the new commands; card_check stays classic
  TableTests         settings (auto-pass switched on once), what is sent at the start, the keys (Enter, Shift+Enter, Ctrl, Ctrl+Shift,
                     Ctrl+click, Y), the feed of passed triggers, the second-stop offer, the Skip window, the button's label
  LiveTests          real Forge: an opponent's trigger passes unless I hold an answer (Stifle yes, Counterspell no), "always stop",
                     holding priority, my own trigger stops me only when I can act, the new default stops, full control, Shift+Enter,
                     an opponent's spell stops me only when I could respond, and --classic-stops gives the old stops back
"""
import json
import os
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
import forge_table as ft
import tests.live as live
from tests.forge_fake import FakeSession, StubStore, load_state

BRIDGE = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


def read(name):
    with open(os.path.join(BRIDGE, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_the_default_stops_are_karls(self):
        src = read("BridgeGui.java")
        smart = src[src.index("} else {                                         // patch 43") + 8:]
        smart = smart[:smart.index("}")]
        self.assertIn("PhaseType.MAIN1, PhaseType.MAIN2", smart)
        self.assertIn("stopsTheirs, PhaseType.END_OF_TURN", smart)
        self.assertNotIn("UPKEEP", smart)
        self.assertNotIn("COMBAT_DECLARE_ATTACKERS", smart)
        self.assertIn("if (Passing.classic)", src)
        self.assertIn("passing.fullControlNow(getGameView())", src)

    def test_the_passing_rules(self):
        src = read("Passing.java")
        decide = src[src.index("Decision decide("):src.index("boolean fullControlAt(")]
        # in this order: full control, Shift+Enter, empty stack -> Forge, always stop, mine, an opponent's spell, an answer
        order = ["if (full)", "passTurn == turn", "stack.isEmpty()", "alwaysStop.contains", "if (mine)", "top.isSpell()",
                 "hasAnswer(me, top)"]
        at = [decide.index(o) for o in order]
        self.assertEqual(at, sorted(at))
        self.assertIn("top.isTrigger() ? Decision.DEFER : Decision.PASS", decide)
        self.assertIn('"Triggered" : "Activated"', src)
        self.assertIn("canTargetSpellAbility", src)
        self.assertIn("YIELD_AUTO_PASS_RESPECTS_INTERRUPTS, String.valueOf(classic)", src)
        self.assertIn("return null;                                // Forge's PhaseHandler", src)

    def test_main_takes_the_new_commands_and_seats_use_the_rules(self):
        src = read("Main.java")
        for c in ('case "hold"', 'case "passturn"', 'case "fullcontrol"', 'case "alwaysstop"', 'case "--classic-stops"'):
            self.assertIn(c, src)
        self.assertIn("new Passing.Human(name)", src)
        self.assertIn('ready.addProperty("passing"', src)
        net = read("NetHost.java")
        self.assertIn('"hold", "passturn", "fullcontrol", "alwaysstop"', net)
        self.assertEqual(net.count("new Passing.Human("), 2)
        self.assertNotIn("new LobbyPlayerHuman(", net)
        self.assertIn('y.add("passing"', read("Snapshot.java"))

    def test_the_jar_is_built_from_these_sources(self):
        import setup_forge
        import zipfile
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            names = z.namelist()
            stamp = z.read(setup_forge.BRIDGE_STAMP).decode("ascii").strip()
        self.assertIn("forge/bridge/Passing.class", names)
        self.assertIn("forge/bridge/Passing$Controller.class", names)
        self.assertEqual(stamp, setup_forge.bridge_source_hash())


class ClientTests(unittest.TestCase):
    def args(self, **kw):
        s = fc.ForgeSession("me.dck", ["opp.dck"], seed=1, **kw)
        return s._bridge_args()

    def test_classic_stops_on_the_command_line_only_when_asked(self):
        self.assertNotIn("--classic-stops", self.args())
        self.assertIn("--classic-stops", self.args(classic_stops=True))

    def test_ready_passed_and_the_new_commands(self):
        s = FakeSession()
        s.handle({"t": "ready", "protocol": 2, "passing": "smart"})
        self.assertEqual(s.passing, "smart")
        s.handle({"t": "passed", "card": "Kambal, Consul of Allocation", "player": "AI 1", "kind": "trigger"})
        self.assertEqual(s.passed[-1]["card"], "Kambal, Consul of Allocation")
        s.hold_priority()
        s.pass_turn()
        s.full_control("turn")
        s.always_stop(["Thassa's Oracle"], True)
        self.assertEqual(s.sent, [{"c": "hold"}, {"c": "passturn"}, {"c": "fullcontrol", "mode": "turn"},
                                  {"c": "alwaysstop", "names": ["Thassa's Oracle"], "on": True}])
        old = FakeSession()
        old.handle({"t": "ready", "protocol": 2})
        self.assertIsNone(old.passing)

    def test_the_card_check_plays_with_the_classic_stops(self):
        import card_check
        self.assertTrue(card_check.Checker("x.txt").classic_stops)
        self.assertFalse(card_check.Checker("x.txt", classic_stops=False).classic_stops)


def priority_state(base="stack_one", passing=None, stack=None, turn=3, seq=7, **extra):
    st = load_state(base)
    st.update({"input": "InputPassPriority", "asking": True, "inputSeq": seq, "turn": turn,
               "yield": {"autoPass": True, "autoYields": [], "autoTriggers": {},
                         "passing": dict({"classic": False, "fullControl": False, "turnControl": False, "passTurn": False,
                                          "hold": False, "passed": 0, "alwaysStop": []}, **(passing or {}))}})
    if stack is not None:
        st["stack"] = stack
    st.update(extra)
    return st


def trigger_item(name="Blood Artist", key="Blood Artist (40): trigger", mine=True):
    return {"key": key, "text": name + " trigger", "trigger": True, "ability": True,
            "card": {"id": 40, "name": name, "controller": 0 if mine else 1, "zone": "Battlefield"}}


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = os.path.join(self.tmp.name, "settings.json")

    def make(self, state=None, smart=True, settings=None, sync=True):
        if settings is not None:
            with open(self.settings, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        state = state or priority_state()
        if smart is False:                  # a bridge from before patch 43: no "passing" in its ready line or its snapshots
            state.get("yield", {}).pop("passing", None)
        session = FakeSession(state)
        if smart:
            session.handle({"t": "ready", "protocol": 2, "passing": "smart"})
        gui = ft.ForgeTable(session, StubStore(), settings_path=self.settings, window_size=(1360, 840))
        if sync:
            gui.sync()
            gui.render()
        return gui, session

    def key(self, gui, k, mod=0, up=False):
        gui.handle_event(pygame.event.Event(pygame.KEYUP if up else pygame.KEYDOWN, key=k, mod=mod, unicode=""))

    # -- settings --
    def test_a_settings_file_from_before_turns_auto_pass_on_once(self):
        gui, _s = self.make(settings={"auto_pass": False}, sync=False)
        self.assertTrue(gui.auto_pass)
        self.assertTrue(gui.passing_intro_due)
        gui.save_settings()
        with open(self.settings, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["passing_version"], ft.PASSING_VERSION)
        self.assertTrue(data["auto_pass"])

    def test_after_that_off_stays_off_and_always_stop_is_kept(self):
        gui, _s = self.make(settings={"auto_pass": False, "passing_version": ft.PASSING_VERSION, "always_stop": ["Thassa's Oracle"]})
        self.assertFalse(gui.auto_pass)
        self.assertFalse(gui.passing_intro_due)
        self.assertEqual(gui.always_stop, ["Thassa's Oracle"])

    def test_a_new_player_has_auto_pass_on(self):
        gui, _s = self.make()
        self.assertTrue(gui.auto_pass)

    # -- what goes to the bridge at the start --
    def test_the_start_sends_off_and_the_always_stop_list(self):
        gui, s = self.make(settings={"auto_pass": False, "passing_version": ft.PASSING_VERSION, "always_stop": ["Thassa's Oracle"]})
        gui.track_yield()
        self.assertIn({"c": "autopass", "on": False}, s.sent)
        self.assertIn({"c": "alwaysstop", "names": ["Thassa's Oracle"], "on": True}, s.sent)

    def test_the_start_sends_nothing_when_auto_pass_is_on_and_nothing_is_marked(self):
        gui, s = self.make()
        gui.track_yield()
        self.assertEqual([c for c in s.sent if c["c"] in ("autopass", "alwaysstop")], [])

    def test_an_older_bridge_gets_the_old_message(self):
        st = priority_state()
        st["yield"]["autoPass"] = False
        gui, s = self.make(st, smart=False)
        gui.track_yield()
        self.assertIn({"c": "autopass", "on": True}, s.sent)

    # -- keys --
    def test_enter_at_priority_passes_until_something_happens(self):
        gui, s = self.make()
        self.key(gui, pygame.K_RETURN)
        self.assertEqual(s.commands("yield"), [{"c": "yield", "mode": "turn"}])
        self.assertEqual(s.commands("ok"), [])

    def test_shift_enter_passes_the_turn(self):
        gui, s = self.make()
        self.key(gui, pygame.K_RETURN, pygame.KMOD_SHIFT)
        self.assertEqual(s.commands("passturn"), [{"c": "passturn"}])

    def test_space_is_still_one_pass(self):
        gui, s = self.make()
        self.key(gui, pygame.K_SPACE)
        self.assertEqual([c["c"] for c in s.sent if c["c"] in ("ok", "yield", "passturn")], ["ok"])

    def test_enter_is_ok_when_forge_is_not_giving_me_priority(self):
        st = priority_state(input="InputConfirm")
        st["prompt"]["message"] = "Do you want to pay 2 life?"
        gui, s = self.make(st)
        self.key(gui, pygame.K_RETURN)
        self.assertEqual([c["c"] for c in s.sent if c["c"] in ("ok", "yield")], ["ok"])

    def test_enter_with_an_older_bridge_is_ok(self):
        gui, s = self.make(smart=False)
        self.key(gui, pygame.K_RETURN)
        self.assertEqual([c["c"] for c in s.sent if c["c"] in ("ok", "yield")], ["ok"])

    def test_an_online_guest_reads_the_mode_from_its_snapshots(self):
        # A friend's table never gets the host engine's "ready" line; its seat's snapshots say how it is passed for.
        gui, s = self.make(smart=None)
        self.assertIsNone(s.passing)
        self.assertTrue(gui.smart_passing())
        self.key(gui, pygame.K_RETURN)
        self.assertEqual(s.commands("yield"), [{"c": "yield", "mode": "turn"}])
        classic = priority_state(passing={"classic": True})
        gui2, s2 = self.make(classic, smart=None)
        self.assertFalse(gui2.smart_passing())
        spectator = priority_state()
        spectator["spectator"] = True
        gui3, s3 = self.make(spectator, smart=None)
        s3.spectator = True
        self.assertFalse(gui3.smart_passing())

    def test_a_ctrl_tap_is_full_control_for_this_turn(self):
        gui, s = self.make()
        self.key(gui, pygame.K_LCTRL, pygame.KMOD_LCTRL)
        self.key(gui, pygame.K_LCTRL, 0, up=True)
        self.assertEqual(s.commands("fullcontrol"), [{"c": "fullcontrol", "mode": "turn"}])

    def test_ctrl_shift_turns_full_control_on_and_off(self):
        gui, s = self.make()
        self.key(gui, pygame.K_LCTRL, pygame.KMOD_LCTRL)
        self.key(gui, pygame.K_LSHIFT, pygame.KMOD_LCTRL | pygame.KMOD_LSHIFT)
        self.key(gui, pygame.K_LCTRL, pygame.KMOD_LSHIFT, up=True)
        self.assertEqual(s.commands("fullcontrol"), [{"c": "fullcontrol", "mode": "on"}])
        s.handle(priority_state(passing={"fullControl": True}, seq=8))
        gui.sync()
        self.key(gui, pygame.K_LSHIFT, pygame.KMOD_LSHIFT)
        self.key(gui, pygame.K_LCTRL, pygame.KMOD_LSHIFT | pygame.KMOD_LCTRL)
        self.key(gui, pygame.K_LCTRL, pygame.KMOD_LSHIFT, up=True)
        self.assertEqual(s.commands("fullcontrol")[-1], {"c": "fullcontrol", "mode": "off"})

    def test_ctrl_z_is_not_a_ctrl_tap(self):
        gui, s = self.make()
        self.key(gui, pygame.K_LCTRL, pygame.KMOD_LCTRL)
        self.key(gui, pygame.K_z, pygame.KMOD_LCTRL)
        self.key(gui, pygame.K_LCTRL, 0, up=True)
        self.assertEqual(s.commands("fullcontrol"), [])
        self.assertEqual(s.commands("undo"), [{"c": "undo"}])

    def test_ctrl_click_on_a_card_holds_priority_first(self):
        st = priority_state(stack=[])
        st["prompt"]["message"] = st["prompt"]["message"].replace("Stack: 1 to Resolve.", "")
        me = next(p for p in st["players"] if p["id"] == st["me"])
        hand = me["zones"]["hand"]
        self.assertTrue(hand)
        hand[0]["weak"] = True
        gui, s = self.make(st)
        gui.render()
        point = next(r.center for r, kind, data in reversed(gui.hits) if kind == "card" and data["card"]["id"] == hand[0]["id"])
        old = pygame.key.get_mods
        pygame.key.get_mods = lambda: pygame.KMOD_LCTRL
        try:
            gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=point, button=1))
        finally:
            pygame.key.get_mods = old
        cmds = [c["c"] for c in s.sent if c["c"] in ("hold", "card")]
        self.assertEqual(cmds, ["hold", "card"])

    def test_y_always_passes_on_the_ability_on_top(self):
        gui, s = self.make(priority_state(stack=[trigger_item()]))
        self.key(gui, pygame.K_y)
        self.assertEqual(s.commands("autoyield"), [{"c": "autoyield", "key": "Blood Artist (40): trigger", "on": True}])

    # -- the feed, the offer, the Skip window, the button --
    def test_a_passed_trigger_shows_in_the_feed_and_the_log(self):
        gui, s = self.make()
        s.handle({"t": "passed", "card": "Rhystic Study", "player": "AI 2", "kind": "trigger"})
        gui.track_passing()
        texts = ["".join(t for t, _st in row.segs) for row, _born in gui.feed]
        self.assertIn("Passed for you: AI 2's Rhystic Study trigger", texts)
        gui.track_passing()
        self.assertEqual(len([t for t in texts if "Rhystic" in t]), 1)

    def test_the_second_stop_by_one_cards_trigger_offers_y(self):
        gui, s = self.make(priority_state(stack=[trigger_item()], seq=10))
        said = []
        gui.say = lambda text, *a, **k: said.append(text)
        gui.passing_intro_due = False
        gui.track_passing()
        self.assertFalse(any("Press Y" in t for t in said))
        s.handle(priority_state(stack=[trigger_item()], seq=11))
        gui.sync()
        gui.track_passing()
        self.assertTrue(any("Press Y" in t and "Blood Artist" in t for t in said))
        s.handle(priority_state(stack=[trigger_item()], seq=12))
        gui.sync()
        gui.track_passing()
        self.assertEqual(len([t for t in said if "Press Y" in t]), 1, "offered once per card")

    def test_the_intro_line_once(self):
        gui, s = self.make(settings={"auto_pass": False}, sync=False)
        said = []
        gui.say = lambda text, *a, **k: said.append(text)
        gui.sync()
        gui.track_passing()
        gui.track_passing()
        self.assertEqual(len([t for t in said if t.startswith("Only the stops that matter")]), 1)
        with open(self.settings, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["passing_version"], ft.PASSING_VERSION)

    def test_the_skip_window_has_full_control_and_always_stop(self):
        gui, s = self.make(priority_state(stack=[trigger_item("Thassa's Oracle", "Thassa's Oracle (9): trigger", mine=False)]))
        labels = [o[0] for o in gui.skip_options()]
        self.assertIn("Full control (Ctrl+Shift): OFF", labels)
        self.assertIn("Always stop on Thassa's Oracle", labels)
        run = dict((o[0], o[1]) for o in gui.skip_options())["Always stop on Thassa's Oracle"]
        run()
        self.assertEqual(gui.always_stop, ["Thassa's Oracle"])
        self.assertIn({"c": "alwaysstop", "names": ["Thassa's Oracle"], "on": True}, s.sent)
        labels = [o[0] for o in gui.skip_options()]
        self.assertIn("Stop always stopping on Thassa's Oracle", labels)
        self.assertLessEqual(len(gui.skip_options()), 9)

    def test_the_button_says_full_control(self):
        gui, _s = self.make(priority_state(passing={"turnControl": True}))
        drawn = []
        real = gui.draw_button
        gui.draw_button = lambda rect, label, name, *a, **k: (drawn.append((name, label)), real(rect, label, name, *a, **k))[1]
        gui.render()
        self.assertIn(("skip", "Full control"), drawn)

    def test_the_help_names_the_keys(self):
        text = " ".join(a + " " + b for a, b in ft.HELP_LINES)
        for words in ("Shift+Enter", "Ctrl+Shift", "Ctrl+click", "Y: always pass", "until an opponent casts something or attacks you"):
            self.assertIn(words, text)


# ---- live: real Forge ---------------------------------------------------------------------------------------------------------

DECK = os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt")
ISLANDS5 = "humanbattlefield=Island;Island;Island;Island;Island"
KAMBAL = "aibattlefield=Kambal, Consul of Allocation;Plains;Plains"


def stack_names(st):
    return [((it.get("card") or {}).get("name"), "T" if it.get("trigger") else ("A" if it.get("ability") else "S"))
            for it in (st or {}).get("stack", [])]


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    classic = False

    def setUp(self):
        # One engine per test: Forge's set-up replaces the zones but not the stack, so a test that ends with spells waiting
        # would leave them to the next one.
        import card_check as cc
        self.cc = cc
        self.chk = cc.Checker(DECK, classic_stops=self.classic, say=lambda *a: None)
        self.chk.start()
        self.addCleanup(self.chk.stop)
        self.s = self.chk.s
        self.passed0 = len(self.s.passed)

    def setup(self, *extra):
        s = self.s
        n = s.setups_done
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=40",
                 "humanlibrary=Island;Island;Island;Island;Island;Island;Island;Island",
                 "ailibrary=Plains;Plains;Plains;Plains;Plains;Plains;Plains;Plains"] + list(extra))
        end = time.time() + 30
        while time.time() < end and s.setups_done <= n:
            s.poll()
            time.sleep(0.05)
        self.chk.pump(1.0)

    def watch(self, seconds, until=None, auto_ok=False, log=None, after_seq=None):
        """Every priority question Forge asks me, until `until(state)`; payments and other questions answered. With after_seq,
        the question open when the watch starts (the one just answered) doesn't count."""
        s, last = self.s, None
        if after_seq is not None:
            last = (after_seq, "InputPassPriority")
        end = time.time() + seconds
        while time.time() < end:
            s.poll()
            st = s.state or {}
            key = (st.get("inputSeq"), st.get("input"))
            if st.get("asking") and key != last:
                last = key
                inp = st.get("input") or ""
                if inp == "InputPassPriority":
                    if log is not None:
                        log.append((st.get("turn"), st.get("phase"), st.get("activePlayer") == st.get("me"), stack_names(st)))
                    if until and until(st):
                        return st
                    if auto_ok:
                        s.ok()
                elif inp.startswith("InputPayMana"):
                    s.ok()
                elif inp == "InputAttack":
                    s.ok()
                elif auto_ok and (st.get("prompt") or {}).get("ok", {}).get("enabled"):
                    s.ok()
            if s.requests:
                req = s.requests[0]
                s.answer(req, self.cc.request_answer(req, {}))
            time.sleep(0.03)
        return s.state or {}

    def at_main1(self):
        st = self.watch(5, until=lambda st: st.get("phase") == "MAIN1" and not st.get("stack"))
        self.assertEqual(st.get("phase"), "MAIN1")

    def cast(self, name, before=None, seconds=8):
        self.at_main1()
        if before:
            before()
        card = next(c for c in self.s.me()["zones"]["hand"] if c["name"] == name)
        self.s.click_card(card["id"])
        return self.watch(seconds)

    def hand(self):
        return sorted(c["name"] for c in self.s.me()["zones"]["hand"])

    def test_an_opponents_trigger_passes_when_i_hold_no_answer(self):
        self.setup(ISLANDS5, "humanhand=Divination;Opt", KAMBAL)
        st = self.cast("Divination")
        self.assertEqual(stack_names(st), [])
        self.assertEqual(self.s.me()["life"], 38, "Kambal's trigger resolved without a stop")
        self.assertEqual(self.hand(), ["Island", "Island", "Opt"], "Divination resolved without a stop")
        self.assertEqual([m["card"] for m in self.s.passed[self.passed0:]], ["Kambal, Consul of Allocation"])

    def test_stifle_in_hand_stops_me_at_the_trigger(self):
        self.setup(ISLANDS5, "humanhand=Divination;Stifle", KAMBAL)
        st = self.cast("Divination")
        self.assertEqual(stack_names(st), [("Kambal, Consul of Allocation", "T"), ("Divination", "S")])
        self.assertEqual(self.s.me()["life"], 40)

    def test_counterspell_is_no_answer_to_a_trigger(self):
        self.setup(ISLANDS5, "humanhand=Divination;Counterspell", KAMBAL)
        st = self.cast("Divination")
        self.assertEqual(stack_names(st), [])
        self.assertEqual(self.s.me()["life"], 38)

    def test_always_stop_on_a_card(self):
        self.setup(ISLANDS5, "humanhand=Divination;Opt", KAMBAL)
        st = self.cast("Divination", before=lambda: self.s.always_stop(["Kambal, Consul of Allocation"], True))
        self.assertEqual(stack_names(st), [("Kambal, Consul of Allocation", "T"), ("Divination", "S")])

    def test_holding_priority_over_my_own_spell(self):
        self.setup(ISLANDS5, "humanhand=Divination;Opt", KAMBAL)
        st = self.cast("Divination", before=self.s.hold_priority)
        self.assertEqual(stack_names(st), [("Divination", "S")], "Kambal's trigger passed; then a stop over my own spell")

    def test_always_pass_on_a_trigger_i_could_answer(self):
        # Y on the table: Forge's own auto-yield works on a stop these rules make (an opponent's trigger with Stifle in hand).
        # (Divination, not Opt: Opt's scry is a question of its own, which watch() leaves open)
        self.setup("humanbattlefield=" + ";".join(["Island"] * 8), "humanhand=Divination;Divination;Stifle", KAMBAL)
        st = self.cast("Divination")
        self.assertEqual(stack_names(st), [("Kambal, Consul of Allocation", "T"), ("Divination", "S")])
        self.s.auto_yield(st["stack"][0]["key"], True)
        self.watch(6)
        self.assertEqual(self.s.me()["life"], 38, "the trigger resolved once always passed on")
        st = self.cast("Divination")
        self.assertEqual(stack_names(st), [], "the second trigger never stopped me")
        self.assertEqual(self.s.me()["life"], 36)

    WARDEN = "humanbattlefield=Forest;Forest;Island;Island;Soul Warden"

    def test_my_trigger_stops_me_when_i_can_act(self):
        self.setup(self.WARDEN, "humanhand=Grizzly Bears;Opt", "aibattlefield=Plains")
        st = self.cast("Grizzly Bears")
        self.assertEqual(stack_names(st), [("Soul Warden", "T")], "Opt castable: a stop on my own trigger")

    def test_my_trigger_passes_when_i_cannot_act(self):
        self.setup(self.WARDEN, "humanhand=Grizzly Bears;Divination", "aibattlefield=Plains")
        self.cast("Grizzly Bears", seconds=5)
        self.assertEqual(self.s.me()["life"], 41, "the trigger resolved without a stop")

    def test_the_default_stops_and_an_opponents_spell(self):
        log = []
        self.setup("humanbattlefield=Island;Island", "humanhand=Opt", "aibattlefield=Forest;Forest;Forest",
                   "aihand=Grizzly Bears")
        self.at_main1()
        self.assertEqual({k: sorted(v) for k, v in self.s.state["stops"].items()}, {"mine": ["MAIN1", "MAIN2"], "theirs": ["END_OF_TURN"]})
        seq = self.s.state.get("inputSeq")
        self.s.pass_turn()
        st = self.watch(20, until=lambda st: bool(st.get("stack")), log=log, after_seq=seq)
        self.assertEqual(stack_names(st), [("Grizzly Bears", "S")], "an opponent's spell I could respond to (Opt)")
        self.assertFalse(any(not mine and ph in ("UPKEEP", "COMBAT_DECLARE_ATTACKERS") for _t, ph, mine, _s in log), log)
        st = self.watch(20, until=lambda st: st.get("phase") == "END_OF_TURN", auto_ok=True, log=log)
        self.assertEqual(st.get("phase"), "END_OF_TURN", "the opponent's end step, Opt in hand")

    def test_an_opponents_spell_passes_when_i_could_not_respond(self):
        self.setup("humanbattlefield=Island;Island", "humanhand=Divination", "aibattlefield=Forest;Forest;Forest",
                   "aihand=Grizzly Bears;Grizzly Bears")
        self.at_main1()
        turn, seq = self.s.state.get("turn"), self.s.state.get("inputSeq")
        log = []
        self.s.pass_turn()
        st = self.watch(25, until=lambda st: st.get("turn", 0) > turn + 1, log=log, after_seq=seq)
        bears = [c for c in self.s.opponents()[0]["zones"]["battlefield"] if c["name"] == "Grizzly Bears"]
        self.assertEqual(len(bears), 2)
        self.assertEqual([row for row in log if row[0] == turn + 1], [], "no stop on the opponent's turn")

    def test_full_control_stops_at_every_step(self):
        log = []
        self.setup(ISLANDS5, "humanhand=Opt", "aibattlefield=Plains")
        self.at_main1()
        turn = self.s.state.get("turn")
        self.s.full_control("on")
        self.chk.pump(0.5)
        self.s.ok()
        self.watch(10, until=lambda st: st.get("turn", 0) > turn + 1, auto_ok=True, log=log)
        phases = {ph for t, ph, _m, _s in log if t == turn + 1}
        self.assertTrue({"UPKEEP", "DRAW", "COMBAT_BEGIN"} <= phases, phases)

    def test_shift_enter_passes_the_rest_of_my_turn(self):
        log = []
        self.setup(ISLANDS5, "humanhand=Opt", "aibattlefield=Plains")
        self.at_main1()
        turn, seq = self.s.state.get("turn"), self.s.state.get("inputSeq")
        self.s.pass_turn()
        self.watch(12, until=lambda st: st.get("turn", 0) > turn, log=log, after_seq=seq)
        self.assertEqual([row for row in log if row[0] == turn], [], "no stop for the rest of my turn")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveClassicTests(LiveTests):
    """--classic-stops: the stops from before patch 43, for the card check."""
    classic = True

    def test_an_opponents_trigger_passes_when_i_hold_no_answer(self):
        self.setup(ISLANDS5, "humanhand=Divination;Opt", KAMBAL)
        st = self.cast("Divination", seconds=6)
        self.assertEqual(stack_names(st)[0], ("Kambal, Consul of Allocation", "T"), "classic: every stack item is a stop")
        self.assertEqual({k: sorted(v) for k, v in self.s.state["stops"].items()},
                         {"mine": ["COMBAT_DECLARE_ATTACKERS", "END_OF_TURN", "MAIN1", "MAIN2"],
                          "theirs": ["COMBAT_DECLARE_ATTACKERS", "END_OF_TURN", "UPKEEP"]})

    # The rest is the smart rules' behaviour: not what classic does.
    test_stifle_in_hand_stops_me_at_the_trigger = None
    test_counterspell_is_no_answer_to_a_trigger = None
    test_always_stop_on_a_card = None
    test_holding_priority_over_my_own_spell = None
    test_my_trigger_stops_me_when_i_can_act = None
    test_my_trigger_passes_when_i_cannot_act = None
    test_the_default_stops_and_an_opponents_spell = None
    test_an_opponents_spell_passes_when_i_could_not_respond = None
    test_full_control_stops_at_every_step = None
    test_shift_enter_passes_the_rest_of_my_turn = None
    test_always_pass_on_a_trigger_i_could_answer = None


if __name__ == "__main__":
    unittest.main()
