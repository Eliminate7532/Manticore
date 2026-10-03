# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28ba: make the soak test trustworthy (SOAK_PLAN_2026-09-27.md, Phase A).

The first real night (27 Sept) reported "0 game(s) with a failing finding" while the bot in my seat never played a
card. Each test here fails on the code as it was that night and passes after the fix:

  1. the bot plays highlighted ("weak") cards at priority, not only "selectable" ones
  2. bot health per game, and a run marked INVALID when the bot did too little (night 1's recording is INVALID)
  3. a canary game with a deliberate fault that must be reported
  4. an honest summary: VALID/INVALID, the program version, problems grouped by signature with what's new, warnings
     shown, the real seat count; each default run in its own folder
Plus: crashlog.uninstall() goes back to the test run's folder (the Round 27f fix, folded in here).
"""
import argparse
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import crashlog
import soak
import soak_bot
import soak_report
from tests.test_round28b import FAKE_BRIDGE, _write_check_bridge

SOAK_FIXTURES = os.path.join(BASE, "tests", "fixtures", "soak")


def night1_state():
    with open(os.path.join(SOAK_FIXTURES, "night1_turn4_main1.json"), encoding="utf-8") as f:
        return json.load(f)


def load_gz(name):
    with gzip.open(os.path.join(SOAK_FIXTURES, name), "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


# ---------------------------------------------------------------------------------------------
# 1. the bot plays cards
# ---------------------------------------------------------------------------------------------
class BotPlaysTests(unittest.TestCase):
    def test_night1_turn4_the_bot_plays_a_land(self):
        """Night 1, game 3, turn 4, my main phase: Swamp, Plains, Plains, Swamp in hand, all highlighted ("weak")."""
        st = night1_state()
        hand = {c["id"]: c for c in st["players"][[p["id"] for p in st["players"]].index(st["me"])]["zones"]["hand"]}
        move = soak_bot.SoakBot(seed=1).next_action(st, [], {})
        self.assertEqual(move[0], "click", move)
        self.assertTrue(hand[move[1]].get("isLand"), hand[move[1]]["name"])

    def test_the_old_selectable_only_test_would_pass_instead(self):
        """The red twin: with night 1's rule (selectable only), the same state gives a pass."""
        with mock.patch.object(soak_bot, "_playable", lambda c: bool(c.get("selectable"))):
            self.assertEqual(soak_bot.SoakBot(seed=1).next_action(night1_state(), [], {}), ("ok",))

    def test_a_card_nothing_highlights_is_not_clicked(self):
        st = night1_state()
        for p in st["players"]:
            for c in p["zones"].get("hand", []):
                c.pop("weak", None)
                c["selectable"] = False
        self.assertEqual(soak_bot.SoakBot(seed=1).next_action(st, [], {}), ("ok",))


# ---------------------------------------------------------------------------------------------
# 2. bot health and INVALID runs
# ---------------------------------------------------------------------------------------------
def healthy_game():
    msgs = [{"t": "state", "me": 0, "players": [{"id": 0}, {"id": 1}, {"id": 2}]}]
    msgs += [{"t": "event", "kind": "land", "player": 0}] * 3 + [{"t": "event", "kind": "land", "player": 1}]
    msgs += [{"t": "event", "kind": "cast", "player": 0, "trigger": False, "ability": False},
             {"t": "event", "kind": "cast", "player": 0, "trigger": True, "ability": False}]
    msgs += [{"t": "shape", "q": q, "shape": s} for q, s in (("order", "all:sorted=0:unsorted=2+"), ("confirm", "card"),
                                                              ("choose", "min=1:max=1:offered=2+"))]
    msgs += [{"t": "_sent", "cmd": {"c": "reply", "id": 1}}, {"t": "dropped"}]
    return msgs


class HealthTests(unittest.TestCase):
    def test_what_a_game_counts(self):
        h = soak_report.game_health(healthy_game())
        self.assertEqual((h["seats"], h["lands"], h["spells"], h["answered"], h["dropped"], len(h["kinds"])), (3, 3, 1, 1, 1, 3))

    def test_night1_is_invalid(self):
        h = soak_report.game_health(load_gz("night1_game003_compact.jsonl.gz"))
        self.assertEqual((h["lands"], h["spells"]), (0, 0))
        self.assertEqual(h["seats"], 4)
        valid, reasons = soak_report.run_verdict([h])
        self.assertFalse(valid)
        self.assertTrue(any("land" in r for r in reasons) and any("spells" in r for r in reasons), reasons)

    def test_a_healthy_run_is_valid(self):
        valid, reasons = soak_report.run_verdict([soak_report.game_health(healthy_game())], canary=(True, "ok"))
        self.assertTrue(valid, reasons)

    def test_a_failed_canary_makes_any_run_invalid(self):
        valid, reasons = soak_report.run_verdict([soak_report.game_health(healthy_game())], canary=(False, "no check"))
        self.assertFalse(valid)
        self.assertIn("canary", reasons[0])

    def test_no_game_at_all_is_invalid(self):
        self.assertFalse(soak_report.run_verdict([])[0])


# ---------------------------------------------------------------------------------------------
# 3. the canary
# ---------------------------------------------------------------------------------------------
def args_for(out_dir, command, **extra):
    base = dict(games=1, hours=None, players=2, decks="sample", seed=123, fault=None, out=out_dir, turn_cap=25,
                game_timeout=1.0, session_command=command, canary=False)
    base.update(extra)
    return argparse.Namespace(**base)


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.old = soak.CANARY_SECONDS
        soak.CANARY_SECONDS = 3
        self.addCleanup(setattr, soak, "CANARY_SECONDS", self.old)

    def test_a_canary_whose_check_arrives_is_ok(self):
        bridge = _write_check_bridge_with("start", "commander_variant_missing")
        self.addCleanup(os.remove, bridge)
        with tempfile.TemporaryDirectory() as out:
            soak._load_modules()
            ok, detail = soak.run_canary(args_for(out, None, canary_command=[sys.executable, bridge]), out)
            self.assertTrue(ok, detail)

    def test_a_canary_with_no_check_fails_and_the_run_says_invalid(self):
        with tempfile.TemporaryDirectory() as out:
            args = args_for(out, [sys.executable, FAKE_BRIDGE], canary=True, canary_command=[sys.executable, FAKE_BRIDGE])
            soak.run(args)
            with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("SOAK RUN: INVALID", text)
            self.assertIn("Canary: FAILED", text)


def _write_check_bridge_with(q, rule):
    fd, path = tempfile.mkstemp(suffix="_canary_bridge.py")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("import json, sys\n"
                "def out(**m):\n    sys.stdout.write(json.dumps(m) + '\\n'); sys.stdout.flush()\n"
                "out(t='ready')\n"
                "out(t='check', q=%r, rule=%r, detail='canary test')\n"
                "for line in sys.stdin:\n    if json.loads(line)['c'] == 'quit':\n        break\n" % (q, rule))
    return path


# ---------------------------------------------------------------------------------------------
# 4. the summary
# ---------------------------------------------------------------------------------------------
class SummaryTests(unittest.TestCase):
    def test_the_same_problem_in_two_games_is_one_signature(self):
        a = soak_report.signature({"rule": "commander_stranded", "detail": "commander 217 stranded (at 29)"})
        b = soak_report.signature({"rule": "commander_stranded", "detail": "commander 221 stranded (at 31)"})
        self.assertEqual(a, b)

    def test_a_run_with_a_check_lists_it_as_new_then_known(self):
        bridge = _write_check_bridge()
        self.addCleanup(os.remove, bridge)
        with tempfile.TemporaryDirectory() as out:
            soak.run(args_for(out, [sys.executable, bridge]))
            with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                first = f.read()
            self.assertIn("NEW  bridge_check: order / wrong_count", first)
            self.assertIn("Program:  Manticore", first)
            self.assertIn("SOAK RUN: INVALID", first)          # the stand-in bridge plays nothing: not a real test
            soak.run(args_for(out, [sys.executable, bridge]))
            with open(os.path.join(out, "soak_summary.txt"), encoding="utf-8") as f:
                second = f.read()
            self.assertIn("bridge_check: order / wrong_count", second)
            self.assertNotIn("NEW  bridge_check", second)
            self.assertIn("1 game(s) with a failing finding.", second)

    def test_warnings_are_shown(self):
        r = soak.GameResult(0, 1, 2)
        r.findings = [{"rule": "dropped_clicks", "severity": "warn", "at": 1, "detail": "7 dropped"}]
        text = soak_report.summary_text([], [r], [soak_report.game_health([])], (False, ["x"]), None,
                                        soak_report.group_findings([r]), set())
        self.assertIn("WARNINGS (1 kind(s))", text)
        self.assertIn("dropped_clicks", text)

    def test_a_default_run_gets_its_own_folder_and_a_latest_summary(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(soak, "default_out_dir", return_value=root):
                soak.run(args_for(None, [sys.executable, FAKE_BRIDGE]))
                soak.run(args_for(None, [sys.executable, FAKE_BRIDGE]))
            runs = [d for d in os.listdir(root) if d.startswith("run_")]
            self.assertGreaterEqual(len(runs), 1)                # two runs in the same second share a folder name
            with open(os.path.join(root, "soak_summary.txt"), encoding="utf-8") as f:
                self.assertIn("(the latest run: run_", f.read())
            self.assertTrue(os.path.isfile(os.path.join(root, "known_signatures.json")))


# ---------------------------------------------------------------------------------------------
# the Round 27f fix, folded in: tests stay out of the real crash_log.txt
# ---------------------------------------------------------------------------------------------
class CrashLogTests(unittest.TestCase):
    def test_uninstall_goes_back_to_the_data_folder_not_the_project(self):
        self.assertNotEqual(os.path.abspath(crashlog.DATA_DIR), os.path.abspath(crashlog.BASE_DIR))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        crashlog.install(tmp, dialog=False)
        crashlog.uninstall()
        self.assertEqual(crashlog._cfg["folder"], crashlog.DATA_DIR)


# ---------------------------------------------------------------------------------------------
# found by the soak: OK on a greyed-out button crashed Forge ("who starts" in a 3-4 player game)
# ---------------------------------------------------------------------------------------------
class WhoStartsWordingTests(unittest.TestCase):
    def test_the_three_player_who_starts_question_is_not_called_play_or_draw(self):
        import forge_table as ft
        msg = "Me, you have won the coin toss.\n\nWho would you like to start this game? (Click on the portrait.)"
        headline, hint, _ok = ft.prompt_view(msg, "Me")
        self.assertIn("choose who starts", headline)
        self.assertIn("panel", hint)
        self.assertNotIn("play first or draw", headline)


import tests.live as live


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class GreyedButtonLiveTests(unittest.TestCase):
    def test_ok_while_greyed_is_refused_and_a_portrait_then_ok_starts_the_game(self):
        """Seed 5 with these three sample decks: I win the toss in a 3-player game. Before round 28ba an OK here (greyed,
        nobody picked) was accepted and Forge crashed with a NullPointerException; the game never started."""
        import time
        import deck_loader as dl
        import forge_client as fc
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)

        def dck(name, out):
            c, d = dl.load_deck(os.path.join(BASE, "sample_decks", name))
            return fc.write_deck_file(os.path.join(tmp, out + ".dck"), c, d, out)

        s = fc.ForgeSession(dck("spellslinger_veyran.txt", "mine"), [dck("typal_lathril.txt", "o1"), dck("go_wide_adeline.txt", "o2")],
                            name="Me", seed=5)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        msg = lambda: ((s.state or {}).get("prompt") or {}).get("message") or ""
        end = time.time() + 120
        while time.time() < end and "Who would you like to start" not in msg():
            s.poll()
            time.sleep(0.1)
        self.assertIn("Who would you like to start", msg(), "seed 5 no longer gives me the toss: pick another seed")
        s.ok()
        time.sleep(3)
        s.poll()
        self.assertIn("Who would you like to start", msg())
        self.assertTrue(any(d.get("reason") == "disabled" for d in s.dropped), s.dropped)
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertNotIn("NullPointerException", f.read())
        s.click_player(s.state["me"])
        end = time.time() + 30
        while time.time() < end and "keep your hand" not in msg().lower():
            s.poll()
            if ((s.state or {}).get("prompt") or {}).get("ok", {}).get("enabled") and "Who would you like" in msg():
                s.ok()
                time.sleep(0.5)
            time.sleep(0.1)
        self.assertIn("keep your hand", msg().lower())


# ---------------------------------------------------------------------------------------------
# "Funeral Room/Awakening Hall" (no spaces round the slash) was dropped by Forge
# ---------------------------------------------------------------------------------------------
class SplitNameTests(unittest.TestCase):
    def test_a_slash_without_spaces_still_splits_the_faces(self):
        import forge_client as fc
        if not os.path.isfile(os.path.join(fc.DEFAULT_RUNTIME, "res", "cardsfolder", "f", "funeral_room_awakening_hall.txt")):
            self.skipTest("needs Forge's card scripts (forge_runtime/)")
        self.assertEqual(fc.forge_card_name("Funeral Room/Awakening Hall"), "Funeral Room // Awakening Hall")
        self.assertEqual(fc.forge_card_name("Fire/Ice"), "Fire // Ice")
        self.assertEqual(fc.forge_card_name("Barkchannel Pathway/Tidechannel Pathway"), "Barkchannel Pathway")

    def test_the_teysa_sample_deck_keeps_karls_room_card(self):
        with open(os.path.join(BASE, "sample_decks", "aristocrats_teysa.txt"), encoding="utf-8") as f:
            self.assertIn("1 Funeral Room/Awakening Hall", f.read())


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class TeysaLiveTests(unittest.TestCase):
    def test_forge_accepts_every_card_of_the_teysa_deck(self):
        import time
        import deck_loader as dl
        import forge_client as fc
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        c, d = dl.load_deck(os.path.join(BASE, "sample_decks", "aristocrats_teysa.txt"))
        mine = fc.write_deck_file(os.path.join(tmp, "teysa.dck"), c, d, "teysa")
        c2, d2 = dl.load_deck(os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt"))
        opp = fc.write_deck_file(os.path.join(tmp, "veyran.dck"), c2, d2, "veyran")
        s = fc.ForgeSession(mine, [opp], name="Me", seed=3)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end = time.time() + 120
        msg = lambda: ((s.state or {}).get("prompt") or {}).get("message") or ""
        while time.time() < end and "keep your hand" not in msg().lower():
            s.poll()
            if "won the coin toss" in msg() and ((s.state or {}).get("prompt") or {}).get("ok", {}).get("enabled"):
                s.ok()
                time.sleep(0.5)
            time.sleep(0.1)
        self.assertIn("keep your hand", msg().lower())
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
        self.assertNotIn("unsupported card", log.lower())
        me = s.me()
        total = sum(len(me["zones"].get(z, [])) for z in ("hand", "command")) + (me.get("libraryCount") or 0)
        self.assertEqual(total, 100)


# ---------------------------------------------------------------------------------------------
# engine errors: one finding per problem; Forge's AI think-time limit is a warning (first real night, game 86)
# ---------------------------------------------------------------------------------------------
class EngineErrorTests(unittest.TestCase):
    AI_TIMEOUT = ["Game-0 > java.util.concurrent.TimeoutException",
                  "\tat java.base/java.util.concurrent.FutureTask.get(FutureTask.java:204)",
                  "\tat forge.ai.AiController.chooseSpellAbilityToPlayFromList(AiController.java:1686)",
                  "\tat forge.ai.AiController.getSpellAbilityToPlay(AiController.java:1577)"]

    def test_an_ai_think_timeout_is_one_warning_not_a_failure(self):
        import bridge_rules as br
        findings = br.check_stream([], self.AI_TIMEOUT)
        self.assertEqual([(f["rule"], f["severity"]) for f in findings], [("ai_think_timeout", "warn")])

    def test_a_real_exception_is_one_failure_however_long_its_stack(self):
        import bridge_rules as br
        log = ["java.lang.NullPointerException: Cannot invoke \"x\" because \"takesAction\" is null",
               "\tat forge.game.GameAction.runPreOpeningHandActions(GameAction.java:2400)",
               "\tat forge.game.GameAction.startGame(GameAction.java:2100)", "\t... 3 more"]
        findings = br.check_stream([], log)
        self.assertEqual([(f["rule"], f["severity"]) for f in findings], [("engine_error", "fail")])
