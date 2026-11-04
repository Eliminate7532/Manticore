# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PLOT1 - bug report from Karl, 1 Nov 2026: "I used plot on a 'Tumbleweed Rising' and was unable to play it later".

His game: Tumbleweed Rising plotted on turn 7; on turn 9, his main phase with an empty stack, the card in exile had no glow (no
"weak"), so the exile pile did not glow and the exile window would not let him click it (it acts only on glowing cards). Forge's
own scan of what you could do (AvailableActions: hand, battlefield, getCardsIn(Flashback)) leaves plotted cards out; Forge does
cast one when it is clicked (checked live, 2 Nov). The bridge now marks a plotted card "weak" when Forge's own rule for the
"(Plotted)" spell holds: its owner's card, in exile, not plotted this turn, and the owner could cast a sorcery now."""
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import tests.live as live

SNAPSHOT = os.path.join(ROOT, "java_bridge", "src", "forge", "bridge", "Snapshot.java")
KINNAN = os.path.join("tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")


class SourceTests(unittest.TestCase):
    def test_the_bridge_marks_a_plotted_card_with_a_read_only_check(self):
        with open(SNAPSHOT, encoding="utf-8") as f:
            src = f.read()
        body = re.search(r"static boolean plottedCastable\(.*?\n    }\n", src, re.S).group(0)
        for need in ("isPlotted()", "ZoneType.Exile", "enteredThisTurn()", "canCastSorcery()", "InputPassPriority", "hasLost()"):
            self.assertIn(need, body)
        self.assertNotIn("getAllPossibleAbilities", body)          # it would run checkStaticAbilities on Swing's thread
        self.assertIn("plottedCastable(c, viewer, gui)", src)


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    def test_a_plotted_card_glows_on_a_later_turn_and_is_cast_from_the_exile_window(self):
        import forge_client as fc
        import forge_table as ft
        import soak
        import soak_bot
        from deck_loader import load_deck
        from tests.forge_fake import StubStore
        from tests.test_forge_table import frame
        tmp = tempfile.mkdtemp(prefix="plot1_")
        self.addCleanup(shutil.rmtree, tmp, True)
        c, d = load_deck(KINNAN)
        me = fc.write_deck_file(os.path.join(tmp, "player.dck"), c, d, "Player")
        opp = fc.write_deck_file(os.path.join(tmp, "opponent1.dck"), c, d, "Opponent 1")
        s = fc.ForgeSession(me, [opp], name="Karl", seed=11, dev=True)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        bot, mem = soak_bot.SoakBot(seed=1), {}

        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                s.poll()
                time.sleep(0.05)

        def to_priority(limit, my_main1=False):
            end = time.time() + limit
            while time.time() < end:
                s.poll()
                st = s.state or {}
                mine = st.get("activePlayer") == st.get("me") and st.get("phase") == "MAIN1"
                if st.get("asking") and (st.get("prompt") or {}).get("message", "").startswith("Priority:") and (mine or not my_main1):
                    return True
                if s.requests or st.get("asking"):
                    mv = bot.next_action(st, list(s.requests), mem)
                    if mv:
                        soak._do(s, mv)
                pump(0.4)
            return False

        self.assertTrue(to_priority(120))
        n = s.setups_done
        s.setup(["activeplayer=p0", "activephase=MAIN1", "turn=5", "p0life=20", "p1life=20",
                 "p0library=Forest;Forest;Forest;Forest;Forest", "p1library=Island;Island;Island;Island;Island",
                 "p0hand=Tumbleweed Rising;Grizzly Bears", "p1hand=",
                 "p0battlefield=Forest;Forest;Forest;Forest;Forest;Forest;Grizzly Bears", "p1battlefield=Island"])
        # three Forests stay untapped after the plot and Grizzly Bears can still be cast, so Forge's auto-pass keeps Main 1 open
        end = time.time() + 60
        while s.setups_done == n and time.time() < end:
            pump(0.3)
        pump(1)
        tw = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Tumbleweed Rising")
        s.click_card(tw["id"])
        pump(2)
        req = s.requests[0]
        plot = next(i for i, o in enumerate(req.get("items") or []) if "plot" in str(o).lower())
        s.answer(req, [plot])
        pump(2)
        for card in s.me()["zones"]["battlefield"]:
            if (s.state.get("input") or "").startswith("InputPayMana") and card["name"] == "Forest" and not card.get("tapped"):
                s.click_card(card["id"])
                pump(1.2)
        self.assertTrue(to_priority(30))
        self.assertEqual((s.state.get("phase"), s.state.get("activePlayer")), ("MAIN1", s.state.get("me")))
        exiled = {c["name"]: c for c in s.me()["zones"]["exile"]}
        self.assertIn("Tumbleweed Rising", exiled)
        self.assertFalse(exiled["Tumbleweed Rising"].get("weak"))     # plotted this turn: not yet (Forge's own rule)
        bears = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Grizzly Bears")
        self.assertEqual(bears.get("weak"), 1)                          # while the hand card it can cast does glow
        s.ok()
        pump(1)
        self.assertTrue(to_priority(240, my_main1=True))
        pump(2)
        card = next(c for c in s.me()["zones"]["exile"] if c["name"] == "Tumbleweed Rising")
        self.assertEqual(card.get("weak"), 1)                          # before PLOT1: no glow, and the window would not click it
        gui = ft.ForgeTable(s, StubStore(), window_size=(1600, 900))
        frame(gui, 3)
        self.assertTrue(gui.pile_actionable(s.me(), "exile"))
        gui.open_zone(s.me()["id"], "exile")
        dialog = gui.modal
        index = next(i for i, it in enumerate(dialog.items) if it["card"]["name"] == "Tumbleweed Rising")
        dialog.pick(gui, index)
        pump(2)
        s.ok()                                                          # let it resolve
        end = time.time() + 30
        while time.time() < end and not any(c["name"] == "Elemental Token" for c in s.me()["zones"]["battlefield"]):
            pump(0.5)
        self.assertIn("Elemental Token", [c["name"] for c in s.me()["zones"]["battlefield"]])
        self.assertIn("Tumbleweed Rising", [c["name"] for c in s.me()["zones"]["graveyard"]])


if __name__ == "__main__":
    unittest.main()
