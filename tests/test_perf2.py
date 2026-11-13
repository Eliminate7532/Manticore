# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PERF2 (Karl, 4 Nov 2026): Forge's auto-pass pauses become a setting - Pauses: Normal / Short / Off (default Normal; Short from 0.28.53.16, rc16).

When Forge passes priority for me by itself (its own auto-pass, a yield, an auto-yielded ability) it first sleeps 0.4 s with
something on the stack and 0.2 s at a step I stop at (PlayerControllerHuman.chooseSpellAbilityToPlay, FControlGamePlayback's
resolveDelay / phasesDelay). Normal = Forge as it is; Short = half; Off = none. Per seat, like Speed (round PRI1).

  SourceTests    the bridge: Passing.PAUSE_MODES, the two Forge preferences, the pause counted / made in awaitNextInput, the
                 "pauses" command, a guest may send it, the plants need --dev, the jar
  ClientTests    forge_client.set_pauses, ForgeSession(pauses=)
  TableTests     the setting (missing / unknown = Short since rc16), what is sent and when (nothing in Normal), cycling, the crash context
  CogTests       Speed | Pauses | New game... | Quit... in one row, inside the pop-up at every size, labels not clipped
  LiveTests      real Forge: my own trigger that Forge passes for me waits 400 ms in Normal, 200 in Short, 0 in Off (counted and
                 timed); the two plants (Off still pausing, Short = Normal) go red
  LiveResumeTests  a game recorded in Normal and played back from its commands with Off: the same board (timing only)
  LiveOnlineTests  a guest's Off reaches the guest's seat only
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
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame

pygame.init()

import forge_client as fc
import forge_table as ft
import tests.live as live
from tests.forge_fake import FakeSession, StubStore
from tests.test_patch43 import DECK, priority_state
from tests.test_settings_report import SIZES, open_cog, popup_point
from tests.test_forge_table import click, frame, make_gui
from tests.test_pri1 import CogTests as PriCog

BRIDGE = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


def read(name):
    with open(os.path.join(BRIDGE, name), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------------------------------
# 1. the bridge's source
# ---------------------------------------------------------------------------------------------------------------------
class SourceTests(unittest.TestCase):
    def test_the_modes_and_forges_two_preferences(self):
        src = read("Passing.java")
        self.assertIn('static final List<String> PAUSE_MODES = List.of("normal", "short", "off");', src)
        self.assertIn('static volatile String defaultPauses = "normal";', src)
        sync = src[src.index("void syncAutoPass("):src.index('// ---- "do I hold an answer')]
        self.assertIn('boolean skip = !"normal".equals(pauseMode());', sync)
        self.assertIn("FPref.YIELD_SKIP_PHASE_DELAY, String.valueOf(skip)", sync)
        self.assertIn("FPref.YIELD_SKIP_RESOLVE_DELAY, String.valueOf(skip)", sync)

    def test_forges_own_rule_is_copied(self):
        src = read("Passing.java")
        rule = src[src.index("static int forgeDelay("):src.index("void notePause()")]
        self.assertIn("FControlGamePlayback.resolveDelay", rule)          # 400: something on the stack
        self.assertIn("FControlGamePlayback.phasesDelay", rule)           # 200: an empty stack at a step I stop at
        self.assertIn("pc.isUiSetToSkipPhase(", rule)                     # none at a step I skip

    def test_short_is_half_and_off_none(self):
        src = read("Passing.java")
        note = src[src.index("void notePause()"):src.index("JsonObject pausesJson()")]
        self.assertIn("delay / 2", note)
        self.assertIn("pausePlant == 1 ? delay : 0", note)              # Off: none (the plant only with --dev)
        self.assertIn("Thread.sleep(wait)", note)

    def test_the_pause_is_counted_before_awaitnextinput_takes_its_lock(self):
        gui = read("BridgeGui.java")
        body = gui[gui.index("public void awaitNextInput() {"):]
        self.assertLess(body.index("passing.notePause();"), body.index("synchronized (awaitLock)"))
        src = read("Passing.java")
        fc_ = src[src.index("private List<SpellAbility> forgeChooses("):src.index("private List<SpellAbility> compareSkip(")]
        self.assertIn("PENDING.set(new int[] {forgeDelay(this)});", fc_)
        self.assertIn("PENDING.remove();", fc_)
        self.assertIn('m.add("pauses", passing.pausesJson());', gui)

    def test_the_command_and_a_guest_may_send_it(self):
        main = read("Main.java")
        case = main[main.index('case "pauses": {'):main.index('case "alwaysstop": {')]
        self.assertIn("gui.passing.pauses =", case)
        self.assertIn("gui.passing.syncAutoPass(", case)
        self.assertIn('case "--pauses":', main)
        self.assertIn("--pauses-plant ignored (the bridge was not started with --dev)", main)
        net = read("NetHost.java")
        allowed = net[net.index("REMOTE_ALLOWED = Set.of("):]
        self.assertIn('"pauses"', allowed[:allowed.index(";")])

    def test_the_jar_is_built_from_these_sources(self):
        import setup_forge
        import zipfile
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            stamp = z.read(setup_forge.BRIDGE_STAMP).decode("ascii").strip()
        self.assertEqual(stamp, setup_forge.bridge_source_hash())


# ---------------------------------------------------------------------------------------------------------------------
# 2. the client
# ---------------------------------------------------------------------------------------------------------------------
class ClientTests(unittest.TestCase):
    def test_set_pauses(self):
        s = FakeSession()
        for m in ("off", "short", "normal", "turbo"):
            s.set_pauses(m)
        self.assertEqual(s.sent, [{"c": "pauses", "mode": "off"}, {"c": "pauses", "mode": "short"},
                                  {"c": "pauses", "mode": "normal"}, {"c": "pauses", "mode": "normal"}])

    def test_the_command_line(self):
        self.assertNotIn("--pauses", fc.ForgeSession("x.dck", [], seed=1)._bridge_args())
        args = fc.ForgeSession("x.dck", [], seed=1, pauses="off")._bridge_args()
        self.assertEqual(args[args.index("--pauses") + 1], "off")


# ---------------------------------------------------------------------------------------------------------------------
# 3. the table
# ---------------------------------------------------------------------------------------------------------------------
def pause_state(mode="normal", **kw):
    return priority_state(passing={"slow": False, "classic": False, "pauses": mode}, **kw)


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = os.path.join(self.tmp.name, "settings.json")

    def make(self, state=None, settings=None, ready="smart", sync=True):
        if settings is not None:
            with open(self.settings, "w", encoding="utf-8") as f:
                json.dump(settings, f)
        session = FakeSession(state if state is not None else pause_state())
        if ready:
            session.handle({"t": "ready", "protocol": 2, "passing": ready})
        gui = ft.ForgeTable(session, StubStore(), settings_path=self.settings, window_size=(1360, 840))
        if sync:
            gui.sync()
            gui.render()
        return gui, session

    def saved(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def test_missing_and_unknown_are_short(self):              # rc16 (Karl, 4 Nov): the default is Short (was Normal)
        for value, want in ((None, "short"), ("fast", "short"), (0, "short"), ("short", "short"), ("off", "off"),
                            ("normal", "normal")):
            with self.subTest(value=value):
                data = {"passing_version": 43}
                if value is not None:
                    data["pauses"] = value
                gui, _s = self.make(settings=data, sync=False)
                self.assertEqual(gui.pauses, want)

    def test_normal_sends_nothing(self):                         # rc16: Normal is now chosen, not the default
        _gui, s = self.make(settings={"passing_version": 43, "pauses": "normal"})
        self.assertEqual(s.commands("pauses"), [])

    def test_the_default_short_is_sent_once(self):              # rc16: the bridge starts each seat in Normal
        gui, s = self.make(settings={"passing_version": 43})
        self.assertEqual(s.commands("pauses"), [{"c": "pauses", "mode": "short"}])
        s.handle(pause_state("short"))
        gui.sync()
        self.assertEqual(len(s.commands("pauses")), 1)

    def test_off_is_sent_once_at_the_first_snapshot_and_not_again_once_taken(self):
        gui, s = self.make(settings={"passing_version": 43, "pauses": "off"})
        self.assertEqual(s.commands("pauses"), [{"c": "pauses", "mode": "off"}])
        for _ in range(3):
            gui.sync()
        self.assertEqual(len(s.commands("pauses")), 1)
        s.handle(pause_state("off"))
        later = time.monotonic() + ft.SPEED_RESEND + 1
        with mock.patch.object(ft.time, "monotonic", return_value=later):
            gui.sync()
        self.assertEqual(len(s.commands("pauses")), 1, "taken: nothing more")

    def test_resent_if_the_seat_never_took_it(self):
        gui, s = self.make(settings={"passing_version": 43, "pauses": "short"})
        later = time.monotonic() + ft.SPEED_RESEND + 1
        with mock.patch.object(ft.time, "monotonic", return_value=later):
            gui.sync()
            gui.sync()
        self.assertEqual(len(s.commands("pauses")), 2)

    def test_an_older_bridge_and_a_spectator_get_nothing(self):
        _gui, s = self.make(state=priority_state(passing={"slow": False, "classic": False}),
                            settings={"passing_version": 43, "pauses": "off"})
        self.assertEqual(s.commands("pauses"), [])
        state = pause_state()
        state["spectator"] = True
        gui, s = self.make(state=state, settings={"passing_version": 43, "pauses": "off"}, sync=False)
        s.spectator = True
        gui.sync()
        self.assertEqual(s.commands("pauses"), [])

    def test_an_online_guest_sends_it_too(self):
        _gui, s = self.make(ready=None, settings={"passing_version": 43, "pauses": "off"})
        self.assertEqual(s.commands("pauses"), [{"c": "pauses", "mode": "off"}])

    def test_cycling_saves_sends_and_right_click_goes_back(self):
        gui, s = self.make(settings={"passing_version": 43, "something_else": 7, "pauses": "normal"})   # rc16: chosen
        seen = []
        for _ in range(3):
            gui.cycle_pauses()
            seen.append(gui.pauses)
            s.handle(pause_state(gui.pauses))
            gui.sync()
        self.assertEqual(seen, ["short", "off", "normal"])
        self.assertEqual([c["mode"] for c in s.commands("pauses")], ["short", "off", "normal"])
        gui.cycle_pauses(-1)
        self.assertEqual(gui.pauses, "off")
        self.assertEqual(self.saved()["pauses"], "off")
        self.assertEqual(self.saved()["something_else"], 7)
        self.assertIn("Pauses: Off", gui.toast[0])

    def test_the_crash_context(self):                            # rc16: Short, the default and taken, adds no line
        gui, s = self.make(state=pause_state("short"), settings={"passing_version": 43})
        self.assertFalse(any(l.startswith("Pauses:") for l in gui.crash_context()))
        gui.pauses = "off"
        self.assertIn("Pauses: Off in the settings; my seat plays Short", gui.crash_context())
        gui.pauses = "short"
        s.handle(pause_state("normal"))
        self.assertIn("Pauses: Short in the settings; my seat plays Normal", gui.crash_context())


class CogTests(unittest.TestCase):
    def test_four_buttons_share_the_game_row_at_every_size(self):
        for size, scale in SIZES + PriCog.MORE_SIZES:
            for pauses in ft.PAUSES:
                with self.subTest(size=size, scale=scale, pauses=pauses):
                    gui = make_gui("main1_start", size=size, scale=scale)
                    gui.pauses = pauses
                    open_cog(gui)
                    frame(gui)
                    pop = gui.overlay
                    rects = {n: r for r, n in pop.buttons}
                    names = [n for _r, n in pop.buttons]
                    i = names.index("speed")
                    self.assertEqual(names[i:i + 4], ["speed", "pauses", "newgame", "quit"])
                    row = [rects[n] for n in ("speed", "pauses", "newgame", "quit")]
                    self.assertEqual({r.y for r in row}, {row[0].y})
                    for a, b in zip(row, row[1:]):
                        self.assertLess(a.right, b.x)
                    for r in row:
                        self.assertTrue(pop.rect.contains(r), r)
                    self.assertTrue(gui.screen.get_rect().contains(pop.rect))
                    texts = (f"Speed: {gui.speed_label()}", f"Pauses: {ft.PAUSE_LABELS[pauses]}", "New game...", "Quit...")
                    for text, r in zip(texts, row):
                        self.assertLessEqual(gui.button_width(text, "small"), r.w, (text, r))

    def test_the_pop_up_does_not_change_width_as_you_click(self):
        widths = set()
        for pauses in ft.PAUSES:
            for speed in ft.SPEEDS:
                gui = make_gui("main1_start", size=(1360, 840), scale=1.0)
                gui.pauses, gui.speed = pauses, speed
                open_cog(gui)
                frame(gui)
                widths.add(gui.overlay.rect.w)
        self.assertEqual(len(widths), 1, widths)

    def test_a_click_cycles_and_a_right_click_goes_back(self):
        gui = make_gui("main1_start")
        open_cog(gui)
        click(gui, popup_point(gui, "pauses"))
        self.assertEqual(gui.pauses, "off")                     # rc16: from the default Short
        frame(gui)
        click(gui, popup_point(gui, "pauses"), button=3)
        self.assertEqual(gui.pauses, "short")

    def test_the_help_names_it(self):
        text = " ".join(t for _k, t in ft.HELP_LINES)
        self.assertIn("Pauses (Short, the default", text)     # rc16


# ---------------------------------------------------------------------------------------------------------------------
# 4. live
# ---------------------------------------------------------------------------------------------------------------------
WARDEN = "humanbattlefield=Forest;Forest;Island;Island;Soul Warden"


def with_bridge_args(extra):
    """Tests only: extra bridge arguments (the plants) for every session started inside the context."""
    orig = fc.ForgeSession._bridge_args

    def args(self):
        return orig(self) + list(extra)
    return mock.patch.object(fc.ForgeSession, "_bridge_args", args)


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    """Soul Warden on my battlefield, Grizzly Bears cast: my own spell resolves at once (patch 43 passes it - no pause), then my
    Soul Warden trigger, with nothing castable, is passed by Forge's own auto-pass - one 400 ms pause in Normal."""

    def engine(self):
        import card_check as cc
        self.chk = cc.Checker(DECK, classic_stops=False, say=lambda *a: None)
        self.chk.start()
        self.addCleanup(self.chk.stop)
        self.s = self.chk.s

    from tests.test_patch43 import LiveTests as _P
    setup, watch, at_main1 = _P.setup, _P.watch, _P.at_main1
    del _P

    def paused(self):
        return dict(self.s.passing_state().get("paused") or {})

    def set_mode(self, mode):
        self.s.set_pauses(mode)
        end = time.time() + 5
        while time.time() < end and self.s.passing_state().get("pauses") != mode:
            self.s.poll()
            time.sleep(0.05)
        self.assertEqual(self.s.passing_state().get("pauses"), mode, "the bridge didn't take the setting")

    def warden_trigger(self, mode):
        """(pauses counted, ms waited, seconds from the click to Main 1 again) for one Soul Warden trigger Forge passes for me."""
        self.engine()
        self.setup(WARDEN, "humanhand=Grizzly Bears;Divination", "aibattlefield=Plains")
        self.at_main1()
        self.set_mode(mode)
        before = self.paused()
        card = next(c for c in self.s.me()["zones"]["hand"] if c["name"] == "Grizzly Bears")
        seq = self.s.state.get("inputSeq")
        t0 = time.time()
        self.s.click_card(card["id"])
        st = self.watch(10, until=lambda st: st.get("phase") == "MAIN1" and not st.get("stack")
                        and st.get("inputSeq") != seq and self.s.me()["life"] == 41, after_seq=seq)
        took = time.time() - t0
        self.assertEqual(self.s.me()["life"], 41, "the trigger resolved")
        self.assertTrue(st)
        after = self.paused()
        d = {k: after.get(k, 0) - before.get(k, 0) for k in ("phase", "resolve", "ms")}
        d["took"] = took
        d["forge"] = 400 * d["resolve"] + 200 * d["phase"]          # what Forge's own rule would wait for these pauses
        return d

    def test_normal_short_and_off(self):
        got = {}
        for mode in ("normal", "short", "off"):
            got[mode] = self.warden_trigger(mode)
            self.chk.stop()
        n = {m: (g["phase"], g["resolve"]) for m, g in got.items()}
        self.assertEqual(len(set(n.values())), 1, got)               # the same auto-passes in every mode
        self.assertGreaterEqual(got["normal"]["resolve"], 1, got)    # at least the Soul Warden trigger (400 ms)
        full = got["normal"]["forge"]
        self.assertEqual(got["normal"]["ms"], full, got)
        self.assertEqual(got["short"]["ms"], full // 2, got)
        self.assertEqual(got["off"]["ms"], 0, got)
        # the wait is real: Normal takes clearly longer than Off (by about `full` ms; noise here is a few tenths of that)
        self.assertGreater(got["normal"]["took"] - got["off"]["took"], 0.6 * full / 1000, got)
        self.assertGreater(got["normal"]["took"] - got["short"]["took"], 0.25 * full / 1000, got)

    def test_off_reaches_the_seat_from_the_command_line(self):
        import card_check as cc
        with mock.patch.object(fc.ForgeSession, "__init__", _with_default(fc.ForgeSession.__init__, pauses="off")):
            self.chk = cc.Checker(DECK, classic_stops=False, say=lambda *a: None)
            self.chk.start()
        self.addCleanup(self.chk.stop)
        self.s = self.chk.s
        self.setup(WARDEN, "humanhand=Opt", "aibattlefield=Plains")
        self.at_main1()
        self.assertEqual(self.s.passing_state().get("pauses"), "off")

    def plant_run(self, plant, mode):
        with with_bridge_args(["--pauses-plant", str(plant)]):
            return self.warden_trigger(mode)

    def test_plant_off_still_pausing_is_caught(self):
        d = self.plant_run(1, "off")
        self.assertGreater(d["ms"], 0, "the plant really pauses in Off - test_normal_short_and_off (0 in Off) would go red")

    def test_plant_short_equals_normal_is_caught(self):
        d = self.plant_run(2, "short")
        self.assertEqual(d["ms"], d["forge"], "the plant makes Short wait Forge's whole pause - test_normal_short_and_off "
                                              "(half in Short) would go red")


def _with_default(init, **defaults):
    def wrapped(self, *a, **kw):
        for k, v in defaults.items():
            kw.setdefault(k, v)
        return init(self, *a, **kw)
    return wrapped


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveResumeTests(unittest.TestCase):
    """The pauses are timing only: a game recorded in Normal, played back from its commands (what Resume and Undo's rebuild do)
    with Off from the start, comes to the same board."""

    def test_a_normal_game_replayed_with_off_is_the_same_game(self):
        import replay
        from deck_loader import load_deck
        from tests.forge_bot import Bot
        tmp = tempfile.mkdtemp(prefix="perf2_resume_")
        self.addCleanup(shutil.rmtree, tmp, True)
        c, d = load_deck(os.path.join(BASE, "sample_decks", "stompy_goreclaw.txt"))
        me = fc.write_deck_file(os.path.join(tmp, "player.dck"), c, d, "Player")
        opp = fc.write_deck_file(os.path.join(tmp, "opponent1.dck"), c, d, "Opponent 1")
        s = fc.ForgeSession(me, [opp], name="Karl", seed=4242).start()
        s.stderr_path = os.path.join(tmp, "e1.log")
        cmds = []
        s.on_send = lambda cmd: cmd.get("c") != "quit" and cmds.append(dict(cmd))
        bot = Bot(s)
        t0, acted_at, acted_version = time.time(), 0.0, -1
        while time.time() - t0 < 150 and len(cmds) < 40:
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
        replay.Replayer(s, []).settled()
        before = replay.summary(s.state)
        normal_paused = dict(s.passing_state().get("paused") or {})
        s.close()
        self.assertGreater(normal_paused.get("phase", 0) + normal_paused.get("resolve", 0), 0, "the recorded game had pauses")
        s2 = fc.ForgeSession(me, [opp], name="Karl", seed=4242, pauses="off").start()
        s2.stderr_path = os.path.join(tmp, "e2.log")
        try:
            rp = replay.Replayer(s2, cmds)
            self.assertTrue(rp.run(), rp.diverged)
            self.assertEqual(s2.passing_state().get("pauses"), "off")
            self.assertEqual((s2.passing_state().get("paused") or {}).get("ms"), 0)
            self.assertEqual(replay.differences(before, replay.summary(s2.state)), [])
        finally:
            s2.close()


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveOnlineTests(unittest.TestCase):
    """Each human seat uses its own player's setting: a guest's Off reaches the guest's seat only."""

    def test_a_guest_plays_off_and_the_host_normal(self):
        import forge_net as fn
        from tests.test_mp2c import deck_file, free_port, wait_for
        fc.sync_bridge()
        tmp = tempfile.mkdtemp(prefix="perf2_online_")
        self.addCleanup(shutil.rmtree, tmp, True)
        cert = fn.ensure_host_cert(tmp, fc.find_java())
        host_deck = deck_file(tmp, "typal_lathril.txt", "Lathril")
        guest_deck = deck_file(tmp, "spellslinger_veyran.txt", "Veyran")
        port = free_port()
        h = fc.HostSession(host_deck, "Karl", port, "pw-perf2", cert, os.path.join(tmp, "guest.dck"), upnp=False,
                           bind="127.0.0.1", code="hostcode", seed=7)
        h.stderr_path = os.path.join(tmp, "host.log")
        h.start()
        self.addCleanup(h.close)
        self.assertTrue(wait_for(lambda: h.hosting is not None or h.exited, 120, pump=[h]), "NetHost never got ready")
        with open(guest_deck, encoding="utf-8") as f:
            g = fc.NetSession("127.0.0.1", port, "Sam", "pw-perf2", "Veyran", f.read(), fingerprint=cert.fingerprint,
                              code="guestcode")
        g.start()
        self.addCleanup(g.close)
        self.assertTrue(wait_for(lambda: h.ready and g.ready and h.state and g.state and "yield" in g.state, 60, pump=[h, g]))
        g.set_pauses("off")
        self.assertTrue(wait_for(lambda: g.passing_state().get("pauses") == "off", 10, pump=[h, g]), "the guest's seat took Off")
        wait_for(lambda: False, 1.0, pump=[h, g])
        self.assertEqual(h.passing_state().get("pauses"), "normal", "the host's own seat is still Normal")


if __name__ == "__main__":
    unittest.main()
