# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 27c: three bugs from Karl's reports of 2026-09-26, all in the Java bridge.

1. A commander exiled or put into a graveyard was never offered the command zone (Main.java passed no applied variant).
2. Surveil showed no card: the source had the prompt's card (BridgeGui.promptCard) but the shipped jar was built before it.
3. Spiteful Visions did nothing on the player's own draw step: the second time the same triggers came up, Forge offered the
   previous order again (every item in destChoices, sourceChoices empty) and BridgeGui answered with an empty order.
"""
import os
import sys
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tests.live as live

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
JAR = os.path.join(BASE, "java_bridge", "forge_bridge.jar")
KINNAN = os.path.join(BASE, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_the_match_starts_with_the_commander_variant_applied(self):
        text = read("Main.java")
        # Round FMT1: the variant is the game's format, Commander unless the bridge was started with --format brawl
        self.assertIn('static String format = "commander";', text)
        self.assertIn('return "brawl".equals(format) ? GameType.Brawl : GameType.Commander;', text)
        self.assertIn("java.util.EnumSet.of(variant);", text)
        self.assertNotIn("startMatch(GameType.Commander, null", text)
        self.assertNotIn("startMatch(variant, null", text)

    def test_order_keeps_items_forge_already_put_in_order(self):
        text = read("BridgeGui.java")
        body = text[text.index(" order("):]
        body = body[:body.index("int n = ordered.size()")]
        self.assertIn("ordered.addAll(destChoices)", body)
        self.assertIn("ordered.addAll(sourceChoices)", body)


class JarTests(unittest.TestCase):
    """Guards against a stale jar: another session changed the Java sources without rebuilding, and the old jar shipped."""

    def test_the_jar_was_built_from_these_sources(self):
        import setup_forge as sf
        self.assertTrue(os.path.isfile(JAR))
        with zipfile.ZipFile(JAR) as z:
            self.assertIn(sf.BRIDGE_STAMP, z.namelist(), "forge_bridge.jar has no source stamp: rebuild it (python setup_forge.py)")
            stamp = z.read(sf.BRIDGE_STAMP).decode("ascii").strip()
        self.assertEqual(stamp, sf.bridge_source_hash(),
                         "java_bridge/src changed since forge_bridge.jar was built: rebuild it (python setup_forge.py) and commit the jar")

    def test_the_jar_has_the_prompt_card(self):
        with zipfile.ZipFile(JAR) as z:
            data = z.read("forge/bridge/BridgeGui.class")
        self.assertTrue(b"promptCard" in data, "BridgeGui.class has no promptCard: the jar is older than the sources")

    def test_the_hash_ignores_line_endings(self):
        import tempfile
        import setup_forge as sf
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for d, nl in ((a, b"\n"), (b, b"\r\n")):
                os.makedirs(os.path.join(d, "x"))
                with open(os.path.join(d, "x", "A.java"), "wb") as f:
                    f.write(b"class A {" + nl + b"}" + nl)
            self.assertEqual(sf.bridge_source_hash(a), sf.bridge_source_hash(b))
            with open(os.path.join(b, "x", "A.java"), "ab") as f:
                f.write(b"// changed")
            self.assertNotEqual(sf.bridge_source_hash(a), sf.bridge_source_hash(b))


def prompt(s):
    return (s.state or {}).get("prompt") or {}


def names(s, zone):
    return [c["name"] for c in s.me()["zones"].get(zone, [])]


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    def checker(self):
        import card_check as cc
        chk = cc.Checker(KINNAN)
        chk.start()
        self.addCleanup(chk.stop)
        return chk

    def pay_and_resolve(self, chk, tries=6):
        s = chk.s
        for _ in range(tries):
            s.poll()
            m = prompt(s).get("message") or ""
            if "Pay Mana" in m:
                s.ok(); time.sleep(0.6)
            elif m.startswith("Priority") and s.state.get("stack"):
                s.ok(); time.sleep(0.8)
            else:
                time.sleep(0.4)

    def test_an_exiled_commander_is_offered_the_command_zone(self):
        chk = self.checker(); s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Swords to Plowshares;Brainstorm", "humanbattlefield=Forest;Island;Plains;Plains;Island",
                 "humanlibrary=Forest;Island;Island;Island;Island;Island", "humancommand=Kinnan, Bonder Prodigy|IsCommander",
                 "aihand=", "ailibrary=Forest;Forest;Forest;Forest", "aibattlefield=Grizzly Bears", "removesummoningsickness=true"]
        self.assertTrue(chk.apply(lines, ["Swords to Plowshares", "Brainstorm"]))
        kinnan = next(c for c in s.me()["zones"]["command"] if "Kinnan" in c["name"])
        s.click_card(kinnan["id"]); time.sleep(0.5)
        self.pay_and_resolve(chk)
        chk.pump(1.5)
        kinnan = next(c for c in s.me()["zones"]["battlefield"] if "Kinnan" in c["name"])
        swords = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Swords to Plowshares")
        s.click_card(swords["id"]); time.sleep(0.6); s.poll()      # poll: the next click must carry the new question number
        s.click_card(kinnan["id"]); time.sleep(0.6); s.poll()
        self.pay_and_resolve(chk, 5)
        end, asked = time.time() + 20, False
        while time.time() < end and not asked:
            s.poll()
            asked = "command zone" in (prompt(s).get("message") or "").lower()
            time.sleep(0.05)
        self.assertTrue(asked, "Forge never asked whether to move the commander to the command zone")
        s.ok()
        chk.pump(2.0)
        self.assertIn("Kinnan, Bonder Prodigy", names(s, "command"))
        self.assertNotIn("Kinnan, Bonder Prodigy", names(s, "exile"))
        self.assertEqual(s.checks, [])

    def test_surveil_shows_the_card_on_the_table(self):
        import forge_dialogs as dlg
        import forge_table as ft
        from tests.forge_fake import StubStore
        from tests.test_forge_table import frame
        chk = self.checker(); s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Raucous Theater;Brainstorm", "humanbattlefield=Swamp",
                 "humanlibrary=Forest;Island;Swamp;Plains;Mountain", "aihand=", "ailibrary=Forest;Forest;Forest", "aibattlefield="]
        self.assertTrue(chk.apply(lines, ["Raucous Theater", "Brainstorm"]))
        theater = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Raucous Theater")
        s.click_card(theater["id"]); time.sleep(0.6)
        end, p = time.time() + 30, {}
        while time.time() < end:
            s.poll(); p = prompt(s)
            if "graveyard?" in (p.get("message") or ""):
                break
            if (p.get("message") or "").startswith("Priority") and s.state.get("stack"):
                s.ok(); time.sleep(0.4)
            time.sleep(0.1)
        self.assertIn("graveyard?", p.get("message") or "", "the surveil question never came")
        self.assertEqual((p.get("source") or {}).get("name"), "Forest")
        gui = ft.ForgeTable(s, StubStore(), window_size=(1360, 840))
        shown = []
        real = dlg.source_thumb
        with mock.patch.object(dlg, "source_thumb", side_effect=lambda g, card, *a: shown.append(card["name"]) or real(g, card, *a)):
            frame(gui, 2)
        self.assertIn("Forest", shown)
        self.assertEqual(s.checks, [])

    def test_spiteful_visions_triggers_on_every_draw_step(self):
        chk = self.checker(); s = chk.s
        orders = play_spiteful_to_my_second_draw(self, chk)
        self.assertGreaterEqual(len(orders), 2, "expected an order question on the AI's draw and again on mine")
        self.assertTrue(all(n == 2 for n in orders), orders)
        # Turn 5: Copper Tablet deals me 1 in my upkeep; then 1 normal draw + 1 from Spiteful Visions, each dealing 1 more.
        self.assertEqual(len(s.me()["zones"]["hand"]), 3)
        self.assertEqual(s.me()["life"], 37)
        self.assertEqual(s.checks, [])                          # round 27d: no bridge self-check failed along the way


def play_spiteful_to_my_second_draw(test, chk):
    """Cast Spiteful Visions on turn 3 and play on to my turn-5 main phase, answering every order question in the order given.
    Returns the number of items in each order question. Shared with tests/test_round27d.py."""
    s = chk.s
    lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
             "humanhand=Spiteful Visions;Brainstorm", "humanbattlefield=Swamp;Swamp;Mountain;Mountain;Copper Tablet",
             "humanlibrary=Forest;Island;Swamp;Plains;Mountain;Island;Island;Island;Island;Island",
             "aihand=", "ailibrary=Forest;Forest;Forest;Forest;Forest;Forest;Forest;Forest", "aibattlefield=",
             "removesummoningsickness=true"]
    test.assertTrue(chk.apply(lines, ["Spiteful Visions", "Brainstorm"]))
    visions = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Spiteful Visions")
    s.click_card(visions["id"]); time.sleep(0.6)
    orders, end = [], time.time() + 150
    while time.time() < end:
        s.poll(); st = s.state; m = prompt(s).get("message") or ""
        for r in list(s.requests):
            items = r.get("items") or []
            if r.get("kind") == "order":
                orders.append(len(items))
            s.answer(r, list(range(len(items))) if r.get("kind") == "order" else [0])
        if "Turn: 5" in m and st.get("phase") == "MAIN1" and not st.get("stack"):
            break
        if m.startswith("Priority") or "Pay Mana" in m or (prompt(s).get("ok") or {}).get("enabled"):
            s.ok(); time.sleep(0.35)
        time.sleep(0.05)
    chk.pump(1.0)
    return orders
