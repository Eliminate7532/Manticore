# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28d: alpha hardening, and what soak nights 4-6 found.

  PlayerChoiceTests (live)  a Siege's "Choose an opponent to protect this battle" marks the players Forge accepts; the soak
                            bot picks one (nights 5 and 6: three stalls on Invasion of Ikoria)
  PlayerChoiceBotTests      the bot and the table on night 5's recorded question
  EngineLogTests            a Guava EventBus logging header is part of the exception below it (night 5 game 123); an AI
                            think abandoned at its timeout before a game-thread death is reported as ai_timeout_race (night 4)
  MemoryTests               the bridge reports its peak memory at game over; the soak summary shows the largest per seat
                            count; less Java memory on a PC with under 8 GB
  ForgeBuildTests (F1)      --version, crash reports and the soak header say which Forge build ran
  UnknownCardTests (F2)     "not in this version of Forge yet", on the deck screen and as the game starts
  SplitNameTests            the card check asks Forge for a split card by both names (Funeral Room // Awakening Hall)
  ConfigTests               a broken bug_report_config.json is named as broken, not "not set up"
  LastSessionTests          a run that didn't close properly is offered as a report once, with its Forge log kept
  ReplayKeyTests            a saved game from another Forge or bridge is refused and can be discarded, not replayed wrongly
  AiTimeoutTests            the bridge gives the AI 20 seconds (Forge's default 5 left threads racing the game thread)
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import tests.live as live

FIX = os.path.join(BASE, "tests", "fixtures", "soak")
KINNAN = os.path.join(BASE, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")
JAVA = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _battle_state(mark=True):
    """Night 5 game 55's stuck question, as the 28d bridge would send it: opponents 1-3 selectable."""
    with open(os.path.join(FIX, "night5_game55_battle_state.json"), encoding="utf-8") as f:
        st = json.load(f)
    if mark:
        for p in st["players"]:
            if p["id"] != st["me"]:
                p["selectable"] = True
    return st


# ---------------------------------------------------------------------------------------------------------------------------
# the Siege's protector (nights 5 and 6)
# ---------------------------------------------------------------------------------------------------------------------------

def _four_player_session(tmp):
    import forge_client as fc
    from deck_loader import load_deck
    c, d = load_deck(KINNAN)
    me = fc.write_deck_file(os.path.join(tmp, "player.dck"), c, d, "Player")
    opps = [fc.write_deck_file(os.path.join(tmp, "opponent%d.dck" % i), c, d, "Opponent %d" % i) for i in (1, 2, 3)]
    s = fc.ForgeSession(me, opps, name="Soak", seed=7, dev=True)
    s.stderr_path = os.path.join(tmp, "engine.log")
    return s.start()


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class PlayerChoiceTests(unittest.TestCase):
    def test_the_siege_protector_question_marks_the_opponents_and_the_bot_answers_it(self):
        """4 players (with one opponent Forge picks the protector itself): cast Invasion of Ikoria for X=0 and let it
        resolve. Before 28d the question showed no selectable player and the soak bot never answered (night 5, games 55
        and 116; night 6, game 79)."""
        import soak
        import soak_bot
        tmp = tempfile.mkdtemp(prefix="r28d_siege_")
        self.addCleanup(shutil.rmtree, tmp, True)
        s = _four_player_session(tmp)
        self.addCleanup(s.close)
        bot, mem = soak_bot.SoakBot(seed=1), {}

        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                s.poll()
                time.sleep(0.05)

        end = time.time() + 90
        while time.time() < end:                         # to my first priority (mulligans, who starts)
            s.poll()
            st = s.state or {}
            if st.get("asking") and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                break
            if s.requests or st.get("asking"):
                mv = bot.next_action(st, list(s.requests), mem)
                if mv:
                    soak._do(s, mv)
            pump(0.5)
        lines = ["activeplayer=p0", "activephase=MAIN1", "turn=5"]
        for k in range(4):
            lines += ["p%dlife=40" % k, "p%dlibrary=Forest;Forest;Forest" % k]
        lines += ["p0battlefield=Forest;Forest;Forest;Forest", "p0hand=Invasion of Ikoria",
                  "p1battlefield=Island", "p2battlefield=Island", "p3battlefield=Island", "p1hand=", "p2hand=", "p3hand="]
        n = s.setups_done
        s.setup(lines)
        while s.setups_done == n and time.time() < end:
            pump(0.3)
        pump(1)
        inv = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Invasion of Ikoria")
        s.click_card(inv["id"])
        pump(2)
        self.assertTrue(s.requests, "Forge should ask for X")
        s.answer(s.requests[0], [0])
        pump(2)
        for c in s.me()["zones"]["battlefield"]:
            if (s.state.get("input") or "").startswith("InputPayMana") and not c.get("tapped"):
                s.click_card(c["id"])
                pump(1.2)
        s.ok()                                           # pass priority: it resolves
        seen, last = None, None
        end = time.time() + 60
        while time.time() < end:
            pump(0.5)
            st = s.state
            if any(c["name"] == "Invasion of Ikoria" for c in s.me()["zones"]["battlefield"]):
                break
            if st.get("input") == "InputSelectEntitiesFromList" and "protect" in st["prompt"]["message"]:
                seen = [p["id"] for p in st["players"] if p.get("selectable")]
            key = soak._question_key(st, s.requests)
            if key == last or not (st.get("asking") or s.requests):
                continue
            last = key
            mv = bot.next_action(st, list(s.requests), mem)
            if mv:
                soak._do(s, mv)
        self.assertEqual(sorted(seen or []), [1, 2, 3], "the three opponents should be marked selectable, not me")
        self.assertTrue(any(c["name"] == "Invasion of Ikoria" for c in s.me()["zones"]["battlefield"]))


class PlayerChoiceBotTests(unittest.TestCase):
    def test_the_bot_picks_a_selectable_opponent_then_ok(self):
        import soak_bot
        st = _battle_state()
        bot, mem = soak_bot.SoakBot(seed=4), {}
        move = bot.next_action(st, [], mem)
        self.assertEqual(move[0], "player")
        self.assertIn(move[1], (1, 2, 3))
        for p in st["players"]:                          # Forge highlights the pick; OK becomes possible
            if p["id"] == move[1]:
                p["highlight"] = True
        st["prompt"]["ok"]["enabled"] = True
        self.assertEqual(bot.next_action(st, [], mem), ("ok",))

    def test_without_the_marks_the_bot_had_nothing_to_do(self):
        """The recorded night-5 state as the 28c bridge sent it: no move - the stall."""
        import soak_bot
        self.assertIsNone(soak_bot.SoakBot(seed=4).next_action(_battle_state(mark=False), [], {}))

    def test_the_question_key_changes_when_a_player_is_picked(self):
        import soak
        st = _battle_state()
        before = soak._question_key(st, [])
        st["players"][2]["highlight"] = True
        self.assertNotEqual(before, soak._question_key(st, []))

    def test_the_table_glows_only_the_selectable_players_and_words_the_question(self):
        import forge_table as ft
        headline, hint, _ok = ft.prompt_view("Invasion of Ikoria (406)\n\nChoose an opponent to protect this battle",
                                             kind="InputSelectEntitiesFromList")
        self.assertEqual(headline, "Choose an opponent to protect this battle")
        self.assertIn("Click a glowing player panel", hint)
        from tests.forge_fake import FakeSession, StubStore, load_log
        st = _battle_state()
        gui = ft.ForgeTable(FakeSession(st, load_log()), StubStore(), window_size=(1360, 840))
        glowing = {p["id"]: gui.panel_targetable(p) for p in st["players"]}
        self.assertEqual(glowing, {0: False, 1: True, 2: True, 3: True})

    def test_the_snapshot_marks_players_from_the_list_question(self):
        snap, gui = _read(os.path.join(JAVA, "Snapshot.java")), _read(os.path.join(JAVA, "BridgeGui.java"))
        self.assertIn("gui.playerChoices()", snap)
        self.assertIn("InputSelectEntitiesFromList", gui)
        self.assertIn("getValidChoices()", gui)


# ---------------------------------------------------------------------------------------------------------------------------
# engine log rules
# ---------------------------------------------------------------------------------------------------------------------------

class EngineLogTests(unittest.TestCase):
    def test_eventbus_headers_belong_to_the_exception_below_them(self):
        """Night 5 game 123 reached game over; its Forge-internal errors are warnings. Before 28d the two logging header
        lines of each GameLogFormatter NullPointerException were engine_error FAILs of their own."""
        import bridge_rules
        lines = _read(os.path.join(FIX, "night5_game123_engine.log")).splitlines()
        findings = bridge_rules._rule_engine_error([{"t": "game_over"}], lines)
        self.assertEqual([f["rule"] for f in findings if f["severity"] == bridge_rules.FAIL], [])
        self.assertIn("forge_internal_error", {f["rule"] for f in findings})

    def test_the_same_log_in_a_game_that_did_not_finish_still_fails(self):
        import bridge_rules
        lines = _read(os.path.join(FIX, "night5_game123_engine.log")).splitlines()
        findings = bridge_rules._rule_engine_error([], lines)
        self.assertIn("engine_error", {f["rule"] for f in findings if f["severity"] == bridge_rules.FAIL})

    def test_a_game_thread_death_after_an_abandoned_ai_think_is_ai_timeout_race(self):
        """Night 4 game 3: "AI eval thread at timeout:" and then "Game-0 > java.util.NoSuchElementException"."""
        import soak
        bridge = _write_dead_bridge(os.path.join(FIX, "night4_game3_engine.log"))
        self.addCleanup(os.remove, bridge)
        text = _soak_one_game(bridge)
        self.assertIn("ai_timeout_race", text)
        self.assertIn("NoSuchElementException", text)
        self.assertNotIn("game_thread_died", text)

    def test_a_game_thread_death_without_one_stays_game_thread_died(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as f:
            f.write("Game-0 > java.util.ConcurrentModificationException\n\tat forge.game.StaticEffects.clear(StaticEffects.java:46)\n")
        self.addCleanup(os.remove, f.name)
        bridge = _write_dead_bridge(f.name)
        self.addCleanup(os.remove, bridge)
        text = _soak_one_game(bridge)
        self.assertIn("game_thread_died", text)
        self.assertNotIn("ai_timeout_race", text)


def _write_dead_bridge(engine_log):
    """A stand-in bridge: Forge asks nothing and never will, and its engine log is `engine_log`."""
    fd, path = tempfile.mkstemp(suffix="_bridge_dead.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(
            "import json, sys\n"
            "def out(**m):\n"
            "    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
            "out(t='ready', protocol=2)\n"
            "out(t='state', turn=52, phase='COMBAT_DECLARE_ATTACKERS', me=0, activePlayer=2, input='', asking=False,\n"
            "    inputSeq=900, players=[{'id': 0, 'name': 'Soak', 'life': 15, 'zones': {'hand': [], 'battlefield': []}},\n"
            "    {'id': 2, 'name': 'AI 2 (opp2)', 'life': 21, 'zones': {'battlefield': []}}],\n"
            "    prompt={'message': 'Waiting for AI 2 (opp2)...', 'ok': {'enabled': False}, 'cancel': {'enabled': False}})\n"
            "sys.stderr.write(open(%r, encoding='utf-8').read())\n"
            "sys.stderr.flush()\n"
            "for line in sys.stdin:\n"
            "    if json.loads(line)['c'] == 'quit':\n"
            "        break\n" % engine_log)
    return path


def _soak_one_game(bridge):
    import soak
    old = soak.STALL_SECONDS, soak.AI_STALL_SECONDS
    soak.STALL_SECONDS, soak.AI_STALL_SECONDS = 0.5, 1.0
    try:
        with tempfile.TemporaryDirectory() as out:
            args = argparse.Namespace(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out,
                                      turn_cap=60, game_timeout=10.0, session_command=[sys.executable, bridge], canary=False)
            soak.run(args)
            return _read(os.path.join(out, "soak_summary.txt"))
    finally:
        soak.STALL_SECONDS, soak.AI_STALL_SECONDS = old


# ---------------------------------------------------------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------------------------------------------------------

class MemoryTests(unittest.TestCase):
    def test_game_health_reads_the_game_over_figures(self):
        import soak_report
        h = soak_report.game_health([{"t": "state", "me": 0, "players": [{}, {}, {}]},
                                     {"t": "game_over", "peakHeapMb": 900, "peakLiveMb": 410, "maxHeapMb": 3072}])
        self.assertEqual(h["memory"], {"peakHeapMb": 900, "peakLiveMb": 410, "maxHeapMb": 3072})

    def test_the_summary_shows_the_largest_per_player_count(self):
        import soak_report
        healths = [{"seats": 4, "memory": {"peakLiveMb": 500, "peakHeapMb": 1200, "maxHeapMb": 3072}},
                   {"seats": 4, "memory": {"peakLiveMb": 700, "peakHeapMb": 1000, "maxHeapMb": 3072}},
                   {"seats": 2, "memory": {"peakLiveMb": 300, "peakHeapMb": 600, "maxHeapMb": 3072}}, {"seats": 3}]
        text = "\n".join(soak_report.memory_lines(healths))
        self.assertIn("4 players: live 700 MB, heap 1200 MB (Java limit 3072 MB; 2 game(s))", text)
        self.assertIn("2 players: live 300 MB", text)
        self.assertNotIn("3 players", text)
        self.assertIn("not reported", "\n".join(soak_report.memory_lines([{"seats": 2}])))

    def test_less_java_memory_on_a_small_pc(self):
        import forge_client as fc
        self.assertEqual(fc.default_memory_mb(16 * 1024), fc.DEFAULT_MEMORY_MB)
        self.assertEqual(fc.default_memory_mb(7_900), fc.DEFAULT_MEMORY_MB)          # an "8 GB" PC reports a little under 8192
        self.assertEqual(fc.default_memory_mb(4 * 1024), fc.SMALL_PC_MEMORY_MB)
        self.assertEqual(fc.default_memory_mb(None), fc.DEFAULT_MEMORY_MB)            # unknown: as before
        self.assertIsInstance(fc.total_ram_mb(), (int, type(None)))

    def test_the_bridge_sends_its_memory_with_game_over(self):
        self.assertIn("peakLiveMb", _read(os.path.join(JAVA, "BridgeGui.java")))
        self.assertIn("HeapWatch.start()", _read(os.path.join(JAVA, "Main.java")))


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveReadyAndGameOverTests(unittest.TestCase):
    def test_ready_says_the_ai_time_limit_and_game_over_says_the_memory(self):
        import card_check as cc
        chk = cc.Checker(KINNAN)
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        self.assertEqual(s.ai_timeout, 20)
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=0"])     # the AI loses at once
        end = time.time() + 30
        while time.time() < end and not s.game_over:
            s.poll()
            time.sleep(0.1)
        self.assertTrue(s.game_over)
        info = s.game_over_info
        self.assertGreater(info.get("peakLiveMb", 0), 0)
        self.assertGreaterEqual(info.get("peakHeapMb", 0), info.get("peakLiveMb", 0))
        self.assertGreater(info.get("maxHeapMb", 0), 0)


# ---------------------------------------------------------------------------------------------------------------------------
# F1, F2, split names, the bug report config
# ---------------------------------------------------------------------------------------------------------------------------

class ForgeBuildTests(unittest.TestCase):
    def test_forge_build_reads_version_txt(self):
        import version
        with tempfile.TemporaryDirectory() as rt:
            with open(os.path.join(rt, "VERSION.txt"), "w", encoding="utf-8") as f:
                f.write("Forge 2.0.15-SNAPSHOT (built from the master branch) (commit 3a74143aa2a4d1475e929e140865bd0c574adf1c)\n"
                        "Free software under the GNU General Public License v3.\n")
            self.assertEqual(version.forge_build(rt), "Forge 2.0.15-SNAPSHOT | commit 3a74143")
            os.remove(os.path.join(rt, "VERSION.txt"))
            self.assertIsNone(version.forge_build(rt))

    def test_reports_and_the_soak_header_name_the_forge_build(self):
        import crashlog
        import soak
        import version
        from unittest import mock
        with mock.patch.object(version, "forge_build", return_value="Forge 9.9 | commit abcdef1"):
            self.assertIn("Forge 9.9 | commit abcdef1", crashlog.environment())
            args = argparse.Namespace(games=1, hours=None, players=None, decks="sample", seed=None, fault=None)
            self.assertIn("Forge:    Forge 9.9 | commit abcdef1", soak._header(args, "now", []))

    def test_version_prints_the_forge_line(self):
        self.assertIn("version.forge_build()", _read(os.path.join(BASE, "forge_table.py")))


class UnknownCardTests(unittest.TestCase):
    def test_the_deck_screen_says_not_in_this_version_of_forge(self):
        import deck_library as lib
        import forge_client as fc
        from unittest import mock
        with mock.patch.object(fc, "unknown_cards", return_value=["Brand New Card"]):
            problems = lib.describe_problems(["Kinnan, Bonder Prodigy"], ["Island"] * 99)
        text = " ".join(t for t, _b in problems)
        self.assertIn("Not in this version of Forge yet", text)
        self.assertIn("Brand New Card", text)
        self.assertIn("left out of the game", text)

    def test_starting_a_game_names_the_cards_left_out(self):
        import forge_menu as fmenu
        import deck_library as lib
        from unittest import mock

        class Entry:
            def __init__(self, name, cards):
                self.name, self.commanders, self.deck, self.ok = name, ["Cmdr"], cards, True

            def problems(self, runtime=None):
                return []

        said = []

        class Gui:
            def start_game(self, *a):
                return None

            def say(self, text, colour=None, seconds=0):
                said.append(text)

        menu = fmenu.DeckMenu.__new__(fmenu.DeckMenu)
        menu.count, menu.has_game, menu.picked, menu.runtime = 1, False, {}, None
        menu.opps = [None]
        menu.message = None
        menu.choice = lambda: {}
        with mock.patch.object(fmenu.DeckMenu, "mine", Entry("Mine", ["New Card"])), \
                mock.patch.object(lib, "unknown_in", side_effect=lambda e, rt=None: ["New Card"] if e.name == "Mine" else []):
            menu.start(Gui(), confirmed=True, seats=[Entry("AI deck", ["Island"])], hang_ok=True)
        self.assertTrue(said and "Not in this version of Forge yet" in said[-1] and "your deck: New Card" in said[-1], said)


class SplitNameTests(unittest.TestCase):
    ROOM = ("Name:Funeral Room\nManaCost:2 B\nTypes:Enchantment Room\nAlternateMode:Split\n\nALTERNATE\n\n"
            "Name:Awakening Hall\nManaCost:6 B B\nTypes:Enchantment Room\n")

    def test_a_split_card_is_set_up_by_both_names(self):
        import card_check
        p = card_check.Profile("Funeral Room/Awakening Hall", self.ROOM)
        self.assertEqual(p.name, "Funeral Room // Awakening Hall")
        self.assertIn("Funeral Room // Awakening Hall", " ".join(card_check.setup_lines(p, "cast")))

    def test_a_transform_card_keeps_its_front_name(self):
        import card_check
        dfc = "Name:Delver of Secrets\nManaCost:U\nTypes:Creature Human Wizard\nAlternateMode:DoubleFaced\n\nALTERNATE\n\nName:Insectile Aberration\n"
        self.assertEqual(card_check.Profile("Delver of Secrets // Insectile Aberration", dfc).name, "Delver of Secrets")


class ConfigTests(unittest.TestCase):
    def test_an_address_without_quotes_is_named_as_broken_and_never_shown(self):
        import reporting
        with tempfile.TemporaryDirectory() as d:
            secret = "https://discord.com/api/webhooks/123/SECRETTOKEN"
            with open(os.path.join(d, reporting.CONFIG_NAME), "w", encoding="utf-8") as f:
                f.write('{\n  "discord_webhook": %s,\n  "owner_name": "Karl"\n}\n' % secret)
            url, why = reporting.config_status(d)
            self.assertIsNone(url)
            self.assertIn("isn't valid (line 2)", why)
            self.assertIn("quotes", why)
            self.assertNotIn("SECRETTOKEN", why)

    def test_missing_and_good_files_are_unchanged(self):
        import reporting
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("not set up", reporting.config_status(d)[1])
            with open(os.path.join(d, reporting.CONFIG_NAME), "w", encoding="utf-8") as f:
                json.dump({"discord_webhook": "https://discord.com/api/webhooks/1/abc", "owner_name": "Karl"}, f)
            self.assertIsNotNone(reporting.config_status(d)[0])
            self.assertIsNone(reporting.config_file_problem(d))


# ---------------------------------------------------------------------------------------------------------------------------
# the last run closed unexpectedly (must-have 15)
# ---------------------------------------------------------------------------------------------------------------------------

class LastSessionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="r28d_last_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_clean_close_leaves_nothing_to_ask(self):
        import last_session as ls
        self.assertIsNone(ls.begin("v1", self.dir))
        ls.end(self.dir)
        self.assertIsNone(ls.begin("v1", self.dir))

    def test_an_unexpected_close_is_offered_once_with_its_forge_log(self):
        import last_session as ls
        ls.begin("Commander Sim 0.28.5", self.dir)
        with open(os.path.join(self.dir, "forge_engine.log"), "w", encoding="utf-8") as f:
            f.write("Game-0 > java.util.NoSuchElementException\n")
        before = ls.begin("Commander Sim 0.28.5", self.dir)            # the next start, without end() in between
        self.assertEqual(before["version"], "Commander Sim 0.28.5")
        self.assertIn("closed unexpectedly", ls.banner_text(before))
        with open(os.path.join(self.dir, "forge_engine.log"), "w", encoding="utf-8") as f:
            f.write("a new game's log\n")                              # a new game rewrites the log...
        files = dict(ls.report_files(self.dir))
        self.assertIn("NoSuchElementException", files["forge_engine.crashed.log.txt"])      # ...the crashed one was kept
        ls.end(self.dir)
        self.assertIsNone(ls.begin("x", self.dir), "asked once: this run closed properly")

    def test_the_report_carries_the_crashed_log_and_the_journal(self):
        import last_session as ls
        import reporting
        journal_path = os.path.join(self.dir, "current_game.jsonl")
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write('{"t":"start","seed":5}\n{"t":"cmd","c":{"c":"ok"}}\n')
        with open(os.path.join(self.dir, ls.CRASHED_ENGINE_LOG), "w", encoding="utf-8") as f:
            f.write("Game-0 > boom\n")
        path = reporting.build_report(ls.report_info({"started": "2026-09-30 01:00:00", "version": "v"}, "Karl"),
                                      folder=self.dir, extra_files=ls.report_files(self.dir, journal_path))
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            self.assertIn("forge_engine.crashed.log.txt", names)
            self.assertIn("journal_tail.jsonl", names)
            self.assertIn("closed unexpectedly", z.read("report.txt").decode())

    def test_the_deck_screen_shows_the_offer_and_its_buttons(self):
        import pygame
        import forge_client as fc
        import forge_table as ft
        from tests.forge_fake import StubStore
        from tests.test_deck_screen import FakeLauncher
        from tests.test_forge_table import frame
        with tempfile.TemporaryDirectory() as tmp:
            dirs = (os.path.join(tmp, "my_decks"), os.path.join(BASE, "sample_decks"), tmp)
            gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), launcher=FakeLauncher(),
                                deck_dirs=dirs)
            gui.last_run = {"started": "2026-09-30 01:00:00", "version": "v"}
            gui.open_menu()
            frame(gui, 2)
            names = {n: r for r, n in gui.menu.btns}
            for n in ("crash_send", "crash_look", "crash_dismiss"):
                self.assertIn(n, names)
                self.assertTrue(pygame.Rect(0, 0, 1360, 840).contains(names[n]))
            gui.crash_report("dismiss")
            frame(gui, 2)
            self.assertNotIn("crash_send", [n for _r, n in gui.menu.btns])

    def test_main_writes_the_marker_and_clears_it_on_a_normal_close(self):
        src = _read(os.path.join(BASE, "forge_table.py"))
        self.assertIn("last_session.begin(", src)
        self.assertEqual(src.count("last_session.end()"), 2)


class EngineLogKeepTests(unittest.TestCase):
    def test_the_previous_engine_log_is_kept_when_forge_starts_again(self):
        import forge_client as fc
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "forge_engine.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write("old game\n")
            fc.keep_previous_engine_log(log)
            self.assertFalse(os.path.exists(log))
            self.assertEqual(_read(fc.previous_engine_log(log)), "old game\n")
            fc.keep_previous_engine_log(log)                    # nothing there: nothing happens
            self.assertEqual(_read(fc.previous_engine_log(log)), "old game\n")


# ---------------------------------------------------------------------------------------------------------------------------
# resuming a game saved by another version (must-have 7)
# ---------------------------------------------------------------------------------------------------------------------------

class ReplayKeyTests(unittest.TestCase):
    def test_replay_matches(self):
        import journal
        key = {"forge": "Forge 2.0.15 | commit 3a74143", "bridge": "abc123"}
        self.assertTrue(journal.replay_matches({"replay": dict(key)}, key))
        self.assertFalse(journal.replay_matches({"replay": dict(key, bridge="zzz")}, key))
        self.assertFalse(journal.replay_matches({"replay": dict(key, forge="Forge 2.0.16 | commit 1234567")}, key))
        self.assertFalse(journal.replay_matches({}, key), "a journal from before 28d can't be checked")
        self.assertTrue(journal.replay_matches({"replay": dict(key), "code": "old"}, key), "the Python code may differ")

    def test_the_journal_stores_the_key(self):
        import journal
        with tempfile.TemporaryDirectory() as d:
            j = journal.GameJournal(d)
            j.start(1, "Karl", {"player.dck": "x"}, "c", 1.0, replay={"forge": "F", "bridge": "B"})
            j.command({"c": "ok"}, 2.0)
            j.close()
            start, _cmds = journal.unfinished(d)
            self.assertEqual(start["replay"], {"forge": "F", "bridge": "B"})

    def test_the_deck_screen_offers_discard_for_a_stale_save_and_resume_refuses_it(self):
        import forge_client as fc
        import forge_table as ft
        import journal
        from tests.forge_fake import StubStore
        from tests.test_deck_screen import FakeLauncher
        from tests.test_forge_table import frame
        with tempfile.TemporaryDirectory() as tmp:
            saves = os.path.join(tmp, "saves")
            j = journal.GameJournal(saves)
            j.start(9, "Karl", {"player.dck": "[metadata]\nName=Me\n"}, "c", time.time(), replay={"forge": "Forge 1.0", "bridge": "old"})
            j.command({"c": "ok"}, time.time())
            j.close()
            dirs = (os.path.join(tmp, "my_decks"), os.path.join(BASE, "sample_decks"), tmp)
            gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), window_size=(1360, 840), launcher=FakeLauncher(),
                                deck_dirs=dirs, saves_dir=saves)
            gui.open_menu()
            frame(gui, 2)
            names = [n for _r, n in gui.menu.btns]
            self.assertIn("discard_saved", names)
            self.assertNotIn("resume", names)
            gui.resume_last_game()
            self.assertIsNone(gui.resuming, "a stale save must not be played back")
            gui.discard_last_game()
            self.assertIsNone(journal.unfinished(saves))
            self.assertTrue(any("not_resumable" in n for n in os.listdir(os.path.join(saves, "finished"))))


# ---------------------------------------------------------------------------------------------------------------------------
# the AI's time limit
# ---------------------------------------------------------------------------------------------------------------------------

class AiTimeoutTests(unittest.TestCase):
    def test_the_bridge_sets_forges_ai_timeout(self):
        src = _read(os.path.join(JAVA, "Main.java"))
        self.assertIn("AI_TIMEOUT_SECONDS = 20", src)
        self.assertIn("FPref.MATCH_AI_TIMEOUT", src)
        self.assertIn('"--ai-timeout"', src)


# ---------------------------------------------------------------------------------------------------------------------------
# fix 1: soak_runs filled the disk (42 GB on 30 Sept)
# ---------------------------------------------------------------------------------------------------------------------------

class DiskTests(unittest.TestCase):
    def test_a_judged_game_record_is_gzipped_and_still_readable(self):
        import bridge_rules
        import soak
        with tempfile.TemporaryDirectory() as d:
            rec = os.path.join(d, "record.jsonl")
            with open(rec, "w", encoding="utf-8") as f:
                for i in range(2000):
                    f.write(json.dumps({"t": "state", "turn": i, "players": [{"id": 0, "name": "x" * 50}]}) + "\n")
            size = os.path.getsize(rec)
            gz = soak._compress_record(rec)
            self.assertTrue(gz.endswith(".jsonl.gz"))
            self.assertFalse(os.path.exists(rec))
            self.assertLess(os.path.getsize(gz), size / 5)
            self.assertEqual(len(bridge_rules.load_stream(gz)), 2000)
            self.assertIsNone(soak._compress_record(None))

    def test_prune_removes_old_runs_game_folders_and_nothing_else(self):
        import soak_prune
        with tempfile.TemporaryDirectory() as root:
            def make(path, text="x"):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            names = ["run_20260927_094456", "run_20260928_081136", "night_20260929_131607", "night_20260930_072830"]
            for n in names:
                games = os.path.join(root, n, "soak") if n.startswith("night") else os.path.join(root, n)
                make(os.path.join(games, "game_000", "record.jsonl"), "y" * 1000)
                make(os.path.join(games, "soak_summary.txt"))
                make(os.path.join(games, "soak_x_1_stall.zip"))
            make(os.path.join(root, "known_signatures.json"))
            n, freed = soak_prune.prune(root, keep=2, dry_run=True)
            self.assertEqual((n, freed), (2, 2000))
            self.assertTrue(os.path.isdir(os.path.join(root, names[0], "game_000")), "a dry run removes nothing")
            n, _freed = soak_prune.prune(root, keep=2)
            self.assertEqual(n, 2)
            self.assertFalse(os.path.exists(os.path.join(root, names[0], "game_000")))
            self.assertFalse(os.path.exists(os.path.join(root, names[1], "game_000")))
            self.assertTrue(os.path.isdir(os.path.join(root, names[2], "soak", "game_000")))
            self.assertTrue(os.path.isdir(os.path.join(root, names[3], "soak", "game_000")))
            for n_ in names[:2]:
                self.assertTrue(os.path.isfile(os.path.join(root, n_, "soak_summary.txt")))
                self.assertTrue(os.path.isfile(os.path.join(root, n_, "soak_x_1_stall.zip")))
            self.assertTrue(os.path.isfile(os.path.join(root, "known_signatures.json")))

    def test_the_backup_test_no_longer_copies_soak_runs(self):
        self.assertIn('"soak_runs"', _read(os.path.join(BASE, "tests", "test_backup.py")))
        self.assertIn("soak_prune.prune(", _read(os.path.join(BASE, "tools", "nightly.py")))


if __name__ == "__main__":
    unittest.main()
