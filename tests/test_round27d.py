# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 27d: the bridge checks its own answers (java_bridge/src/forge/bridge/Checks.java).

Every check here must be shown to go RED on a known mistake, not only to stay green on good code. The mistakes are switched on
with the bridge's --fault option (dev only), so no JDK is needed to build a broken jar:
  ORDER_IGNORES_SORTED   round 27c's Spiteful Visions bug (order() read only the unsorted items)
  NO_COMMANDER_VARIANT   round 27c's command-zone bug (the match started without the Commander variant)
  CHOICES_OVER_MAX       a choose-N answer with one item too many
"""
import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
import tests.live as live

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
KINNAN = os.path.join(BASE, "sample_decks", "kinnan_nbc_moxfield_export.txt")


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


def method_body(text, signature):
    """The text of one Java method, from its signature to the next method's @Override (good enough for these checks)."""
    start = text.index(signature)
    end = text.find("@Override", start + len(signature))
    return text[start:end if end > 0 else len(text)]


class SourceTests(unittest.TestCase):
    """Every question Forge can ask the human goes through a check. A new callback without one fails here."""

    CHECKED = {
        "public <T> OrderResult<T> order(": "Checks.order(",
        "public <T> List<T> getChoices(": "Checks.choices(",
        "public GameEntityView chooseSingleEntityForEffect(": "Checks.single(",
        "public List<GameEntityView> chooseEntitiesForEffect(": "Checks.choices(",
        "public SpellAbilityView getAbilityToPlay(": "Checks.single(",
        "public boolean confirm(": "Checks.unusable(",
        "public boolean showConfirmDialog(": "Checks.unusable(",
        "public List<CardView> manipulateCardList(": "Checks.autoAnswered(",
        "public Map<CardView, Integer> assignCombatDamage(": "Checks.unusable(",
        "public Map<Object, Integer> assignGenericAmount(": "Checks.unusable(",
    }

    def test_each_question_is_checked(self):
        text = read("BridgeGui.java")
        for signature, call in self.CHECKED.items():
            with self.subTest(signature):
                self.assertIn(call, method_body(text, signature))

    def test_the_start_checks_the_commander_variant(self):
        self.assertIn("commander_variant_missing", read("BridgeGui.java"))

    def test_faults_need_dev(self):
        checks = read("Checks.java")
        self.assertIn("return Main.dev && faults.contains(f);", checks)


class SessionTests(unittest.TestCase):
    def session(self):
        return fc.ForgeSession("me.dck", ["opp.dck"], runtime="nowhere")

    def test_checks_are_kept_and_noted_once_per_kind(self):
        s = self.session()
        with mock.patch("crashlog.note") as note:
            for detail in ("a", "b"):
                s.handle({"t": "check", "q": "order", "rule": "wrong_count", "detail": detail})
            s.handle({"t": "check", "q": "choose", "rule": "wrong_count", "detail": "c"})
        self.assertEqual([c["detail"] for c in s.checks], ["a", "b", "c"])
        self.assertEqual(note.call_count, 2)
        self.assertIn("order / wrong_count", note.call_args_list[0][0][0])

    def test_shapes_are_counted(self):
        s = self.session()
        for _ in range(3):
            s.handle({"t": "shape", "q": "order", "shape": "all:sorted=2+:unsorted=0"})
        s.handle({"t": "shape", "q": "confirm", "shape": "card"})
        self.assertEqual(s.shapes[("order", "all:sorted=2+:unsorted=0")], 3)
        self.assertEqual(s.shapes[("confirm", "card")], 1)

    def test_faults_are_passed_only_in_dev_mode(self):
        seen = []

        def popen(cmd, *a, **k):
            seen.append(cmd)
            raise OSError("stop here")

        for dev in (True, False):
            s = fc.ForgeSession("me.dck", ["opp.dck"], runtime="nowhere", dev=dev, faults=["order_ignores_sorted"])
            s.stderr_path = os.devnull
            with mock.patch.object(fc, "runtime_problem", return_value=None), mock.patch.object(fc, "find_java", return_value="java"), \
                    mock.patch.object(fc.subprocess, "Popen", side_effect=popen):
                try:
                    s.start()
                except Exception:
                    pass
        self.assertEqual(len(seen), 2)
        self.assertIn("--fault", seen[0])
        self.assertNotIn("--fault", seen[1])


class FixtureTests(unittest.TestCase):
    """The recorded games Round 28b builds on (tests/fixtures/bridge): readable, both directions, and the faults show."""
    FOLDER = os.path.join(BASE, "tests", "fixtures", "bridge")
    NAMES = ("spiteful_ok", "spiteful_fault", "cmdzone_yes", "cmdzone_no", "cmdzone_fault", "surveil_ok")

    def load(self, name):
        import gzip
        import json
        with gzip.open(os.path.join(self.FOLDER, name + ".jsonl.gz"), "rt", encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_every_fixture_has_both_directions_and_its_notes(self):
        for name in self.NAMES:
            with self.subTest(name):
                msgs = self.load(name)
                kinds = {m.get("t") for m in msgs}
                self.assertTrue({"ready", "state", "_sent"} <= kinds, kinds)
                for ext in (".json", ".engine.log"):
                    self.assertTrue(os.path.isfile(os.path.join(self.FOLDER, name + ext)))

    def test_the_faulted_games_carry_their_check_and_the_good_ones_none(self):
        expected = {"spiteful_fault": ("order", "wrong_count"), "cmdzone_fault": ("start", "commander_variant_missing")}
        for name in self.NAMES:
            with self.subTest(name):
                checks = [(m.get("q"), m.get("rule")) for m in self.load(name) if m.get("t") == "check"]
                self.assertEqual(checks, [expected[name]] if name in expected else [])

    def test_the_command_zone_question_is_in_the_asked_games_only(self):
        for name, asked in (("cmdzone_yes", True), ("cmdzone_no", True), ("cmdzone_fault", False)):
            with self.subTest(name):
                texts = [((m.get("prompt") or {}).get("message") or "") for m in self.load(name) if m.get("t") == "state"]
                self.assertEqual(any("command zone" in s.lower() for s in texts), asked)


class CardCheckTests(unittest.TestCase):
    def test_a_failed_check_is_a_fail_in_the_card_check(self):
        import card_check as cc
        with open(cc.__file__, encoding="utf-8") as f:
            self.assertIn("CHECK FAILED", f.read())


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    def checker(self, *faults):
        import card_check as cc
        chk = cc.Checker(KINNAN, faults=faults)
        chk.start()
        self.addCleanup(chk.stop)
        return chk

    def rules(self, s):
        s.poll()
        return [(c.get("q"), c.get("rule")) for c in s.checks]

    # ---- the order check ----
    def test_the_spiteful_visions_bug_is_caught_by_the_order_check(self):
        from tests.test_round27c import play_spiteful_to_my_second_draw
        chk = self.checker("ORDER_IGNORES_SORTED"); s = chk.s
        play_spiteful_to_my_second_draw(self, chk)
        self.assertEqual(len(s.me()["zones"]["hand"]), 2, "with the fault on, the second draw should lose its trigger (the 27c bug)")
        self.assertIn(("order", "wrong_count"), self.rules(s))
        order_shapes = [shape for (q, shape) in s.shapes if q == "order"]
        self.assertIn("all:sorted=0:unsorted=2+", order_shapes)      # the first time
        self.assertIn("all:sorted=2+:unsorted=0", order_shapes)      # the second time: the shape that was never tested

    # ---- the commander variant ----
    def test_a_match_without_the_commander_variant_is_caught_at_the_start(self):
        chk = self.checker("NO_COMMANDER_VARIANT")
        self.assertIn(("start", "commander_variant_missing"), self.rules(chk.s))

    def test_a_normal_start_has_no_failed_checks(self):
        chk = self.checker()
        self.assertEqual(self.rules(chk.s), [])

    # ---- the choose check ----
    def cast_charm(self, chk):
        s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Abzan Charm;Brainstorm", "humanbattlefield=Plains;Swamp;Forest",
                 "humanlibrary=Forest;Island;Island;Island", "aihand=", "ailibrary=Forest;Forest;Forest",
                 "aibattlefield=Grizzly Bears", "removesummoningsickness=true"]
        self.assertTrue(chk.apply(lines, ["Abzan Charm", "Brainstorm"]))
        charm = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Abzan Charm")
        s.click_card(charm["id"]); time.sleep(0.6)
        end, asked = time.time() + 20, None
        while time.time() < end and asked is None:
            s.poll()
            for r in list(s.requests):
                if r.get("kind") == "choose" and len(r.get("items") or []) >= 2:
                    asked = r
                else:
                    s.answer(r, [0])
            time.sleep(0.05)
        self.assertIsNotNone(asked, "the mode choice never came")
        s.answer(asked, [0])
        chk.pump(1.0)
        return asked

    def test_one_item_too_many_is_caught_by_the_choose_check(self):
        chk = self.checker("CHOICES_OVER_MAX")
        self.cast_charm(chk)
        self.assertIn(("choose", "wrong_count"), self.rules(chk.s))

    def test_a_normal_choice_has_no_failed_checks_and_records_its_shape(self):
        chk = self.checker()
        self.cast_charm(chk)
        self.assertEqual(self.rules(chk.s), [])
        self.assertTrue(any(q == "choose" for (q, _shape) in chk.s.shapes), chk.s.shapes)
