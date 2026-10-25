# SPDX-License-Identifier: GPL-3.0-or-later
"""Round FB2 (trial, 23 Oct 2026): Forge fb4d809 -> 9a6f776 (master).

  SourceTests  BridgeGui follows Forge's new setSelection (the old three overrides are gone); --forge-undo in Main and NetHost,
               "off" by default, sent in "ready", set before a match can start; Forge's replay recording off
  ClientTests  ForgeSession passes --forge-undo only with dev=True and reads "forgeUndo"
  LiveTests    real Forge: "ready" says off; with off Forge offers no Undo at my priority after a land (and records no replay);
               with free it does
"""
import os
import re
import sys
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
import tests.live as live

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
KINNAN = os.path.join(BASE, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_bridge_gui_follows_set_selection(self):
        src = read("BridgeGui.java")
        self.assertIn("public void setSelection(Selection selection)", src)
        self.assertIn("super.setSelection(selection);", src)
        for gone in ("void setSelectables(", "void clearSelectables(", "void setHighlighted(", "getGamestate()"):
            self.assertNotIn(gone, src, gone + " is no longer part of Forge's IGuiGame (9a6f776)")
        # the ids are swapped in whole (a snapshot built on another thread never sees a half-cleared set)
        self.assertIn("private volatile java.util.Set<Integer> selectableIds", src)
        self.assertIn("selectableIds = java.util.Collections.unmodifiableSet(ids);", src)

    def test_forge_undo_is_off_unless_asked(self):
        main, host = read("Main.java"), read("NetHost.java")
        self.assertIn('static String forgeUndo = "off";', main)
        for v in ('"off"', '"single"', '"free"'):
            self.assertIn(v, main)
        self.assertIn('case "--forge-undo"', main)
        self.assertIn('case "--forge-undo"', host)
        self.assertIn('ready.addProperty("forgeUndo", forgeUndo);', main)
        init = main[main.index("static void initForge("):]
        init = init[:init.index("\n    }\n")]
        self.assertIn("setPref(FPref.UI_UNDO_MODE, FORGE_UNDO.get(forgeUndo))", init)

    def test_forge_records_no_replays(self):
        init = read("Main.java")
        init = init[init.index("static void initForge("):]
        init = init[:init.index("\n    }\n")]
        self.assertIn("setPref(FPref.UI_RECORD_REPLAYS, false)", init)


class ClientTests(unittest.TestCase):
    def args(self, **kw):
        s = fc.ForgeSession("mine.dck", ["opp.dck"], seed=1, **kw)
        return s._bridge_args()

    def test_forge_undo_only_with_dev(self):
        self.assertNotIn("--forge-undo", self.args(forge_undo="free"))
        a = self.args(forge_undo="free", dev=True)
        self.assertEqual(a[a.index("--forge-undo") + 1], "free")
        self.assertNotIn("--forge-undo", self.args(dev=True))


@unittest.skipUnless(live.live_enabled(), live.live_problem() or "live tests are off")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()

    def play_a_land(self, forge_undo=None):
        import card_check as cc
        chk = cc.Checker(KINNAN)
        if forge_undo:
            base = fc.ForgeSession._bridge_args
            fc.ForgeSession._bridge_args = lambda s: base(s) + ["--forge-undo", forge_undo]
            self.addCleanup(setattr, fc.ForgeSession, "_bridge_args", base)
        chk.start()
        self.addCleanup(chk.stop)
        s = chk.s
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=40",
                 "humanbattlefield=Forest", "humanhand=Forest;Island", "aibattlefield=Hill Giant"])

        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                s.poll()
                time.sleep(0.1)

        pump(3)
        land = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Island")
        s.click_card(land["id"])
        end = time.time() + 20
        while time.time() < end and not any(c["name"] == "Island" for c in s.me()["zones"]["battlefield"]):
            pump(0.5)
        end = time.time() + 20
        while time.time() < end and not (s.state.get("asking") and s.state.get("input") == "InputPassPriority"):
            pump(0.3)
        self.assertEqual(s.state.get("input"), "InputPassPriority")
        return s

    def test_off_by_default_no_forge_undo_after_a_land(self):
        s = self.play_a_land()
        self.assertEqual(s.forge_undo_used, "off")
        self.assertFalse(re.match(r"Undo", s.state["prompt"]["cancel"]["label"]), s.state["prompt"]["cancel"])
        # and no replay recorder: Forge's ReplaySession logs each batch of events it encodes ("ProtocolGuiGame: Sending batch")
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertNotIn("ProtocolGuiGame: Sending batch", f.read())

    def test_free_offers_forge_undo_after_a_land(self):
        s = self.play_a_land("free")
        self.assertEqual(s.forge_undo_used, "free")
        self.assertTrue(s.state["prompt"]["cancel"]["label"].startswith("Undo"), s.state["prompt"]["cancel"])


if __name__ == "__main__":
    unittest.main()
