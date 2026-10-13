# SPDX-License-Identifier: GPL-3.0-or-later
"""Release 0.28.53.10 (12 Oct 2026): where two of its rounds meet in the bridge.

Round CD3 (Turbo Cube's emblem) and Round LAST1c (a restore keeps The Monarch, The Initiative and the City's Blessing) both changed
SetupState.applyThenSignal, which runs Forge's Setup Game State for a dev set-up and for the table's Restore (LAST1's loadstate).
Forge's set-up empties every command zone; CD3 puts the house rule's emblems back (HouseRules.reapply), LAST1c puts the saved
designations back (applyMarks). Merged, both run, the emblem first. Neither round's own tests covers the two together: a Turbo
game restored from its last good state must come back with one emblem per player AND the saved designations.

The emblem is never in a save (GameState leaves out every card with no paper card, as for The Monarch), so it can only come back
from the restoring game's own rule. Live tests: Java and forge_runtime/ (MANTICORE_SKIP_LIVE=1 skips them)."""
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
from tests import live

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
DECK = ["Island"] * 17 + ["Divination"] * 23
EMBLEM = "Turbo Cube Emblem"


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def command_names(state, index):
    return [c.get("name") for c in (state["players"][index].get("zones") or {}).get("command") or []]


class SourceTests(unittest.TestCase):
    def test_a_set_up_puts_back_the_emblems_and_then_the_marks(self):
        text = _read(os.path.join(SRC, "SetupState.java"))
        body = text[text.index("void applyThenSignal(final Game game, final Wire wire, final boolean restore"):]
        apply_at = body.index("applyGameOnThread(game);")
        rules_at = body.index("HouseRules.reapply(game);")
        marks_at = body.index("applyMarks(game);")
        self.assertLess(apply_at, rules_at)
        self.assertLess(rules_at, marks_at)
        # both inside the try, so a failure in either still answers (state_load_failed / setup_done)
        try_at, catch_at = body.index("try {"), body.index("} catch (Throwable e)")
        self.assertTrue(try_at < rules_at < catch_at and try_at < marks_at < catch_at)
        # once each, and neither only for a restore: the soak's mid-game boards and the card check need the emblem too
        self.assertEqual(len(re.findall(r"HouseRules\.reapply\(", body)), 1)
        self.assertNotRegex(body, r"if\s*\(\s*restore\s*\)[^;]*HouseRules\.reapply")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveRestoreTests(unittest.TestCase):
    def setUp(self):
        fc.sync_bridge()
        self.tmp = tempfile.mkdtemp(prefix="rel10_live_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def game(self, tag):
        p0 = fc.write_deck_file(os.path.join(self.tmp, tag + "_p.dck"), [], DECK, "P", fmt="draft", rules=("turbo",))
        p1 = fc.write_deck_file(os.path.join(self.tmp, tag + "_o.dck"), [], DECK, "O", fmt="draft", rules=("turbo",))
        s = fc.ForgeSession(p0, [p1], name="Tester", seed=5)
        s.stderr_path = os.path.join(self.tmp, tag + "_engine.log")
        s.start()
        self.addCleanup(s.close)
        end = time.time() + 150
        while time.time() < end:                              # to my first priority question, keeping the opening hand
            s.poll()
            self.assertFalse(s.fatal or s.exited, s.fatal or "Forge stopped while starting")
            st = s.state
            msg = (st.get("prompt") or {}).get("message", "") if st else ""
            if st and s.is_mulligan_prompt():
                s.ok()
                time.sleep(0.5)
            elif st and msg.startswith("Priority") and st.get("asking", True):
                return s
            time.sleep(0.1)
        self.fail("never reached my first priority question")

    def wait(self, s, what, seconds=60):
        end = time.time() + seconds
        ping = time.time() + 1.0
        while time.time() < end:
            s.poll()
            if what():
                return True
            if time.time() > ping:
                s.send(c="flush")
                ping = time.time() + 1.0
            time.sleep(0.1)
        return False

    def test_a_turbo_game_restored_from_its_save_has_one_emblem_each_and_the_monarch(self):
        s = self.game("a")
        self.assertEqual(s.rules_used, ("turbo",))
        self.assertTrue(s.last_state_ok and s.last_state_marks)
        self.assertEqual([command_names(s.state, 0), command_names(s.state, 1)], [[EMBLEM], [EMBLEM]])
        path = os.path.join(self.tmp, "turbo.state")
        n = s.save_state(path)
        self.assertTrue(self.wait(s, lambda: any(a.get("n") == n for a in s.state_saves)), "no answer to savestate")
        answer = [a for a in s.state_saves if a.get("n") == n][-1]
        self.assertTrue(answer.get("ok"), answer)
        text = _read(path)
        self.assertNotIn(EMBLEM, text)                        # no paper card: never in a save
        with open(path, "a", encoding="utf-8") as f:          # a designation, as Main.saveState writes it (LAST1c)
            f.write("#manticore monarch=p0|\n")
        s.close()
        # the table's Restore: a NEW game with the same rules, the save loaded at its first priority stop
        s2 = self.game("b")
        before = s2.setups_done
        s2.load_state(path)
        self.assertTrue(self.wait(s2, lambda: s2.setups_done > before or s2.state_load_failed), "no answer to loadstate")
        self.assertIsNone(s2.state_load_failed)
        self.assertEqual(s2.setup_failures, [])
        self.assertTrue(self.wait(s2, lambda: "The Monarch" in command_names(s2.state, 0)), command_names(s2.state, 0))
        time.sleep(1.0)
        s2.poll()
        self.assertEqual(sorted(command_names(s2.state, 0)), sorted([EMBLEM, "The Monarch"]))
        self.assertEqual(command_names(s2.state, 1), [EMBLEM])
        log = _read(s2.stderr_path)
        self.assertEqual(log.count("has the Turbo Cube emblem"), 4)       # the start, then the restore
        self.assertNotIn("setup failed", log)
        self.assertEqual(s2.checks, [])


if __name__ == "__main__":
    unittest.main()
