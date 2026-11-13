# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1567760778684272653 (a player, 5 Nov 2026: "The autotapper in multiplayer needs to be sped up significantly ... the only
pauses we should have to click pass priority should be when we actually have a piece of interaction").

Cause: java_bridge Passing.decide returned DEFER for an opponent's SPELL, so Forge's auto-pass stopped the player whenever they
could do anything at all (an Opt, a removal spell, any non-mana ability) - at every spell of every opponent in a pod. Triggers
and activated abilities already stopped only when the player held an answer that can target them.

Karl, 7 Nov 2026: "Only when I can answer". An opponent's spell now goes through the same hasAnswer scan, with Forge's
TargetType word "Spell" (Counterspell, Negate, Misdirection count; Stifle doesn't). Slow keeps every old stop.

  SourceTests   decide() no longer hands an opponent's spell to Forge; the stack kind; the "passed" line says spell; the jar
  TableTests    the feed says "Passed for you: AI 1's Grizzly Bears spell"
  LiveTests     real Forge: Opt in hand no longer stops me at an opponent's spell (the end step still does); Counterspell does;
                Stifle doesn't; Slow still stops at the spell
"""
import os
import sys
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tests.live as live
from tests import test_patch43 as p43

BRIDGE = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")


def read(name):
    with open(os.path.join(BRIDGE, name), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_an_opponents_spell_goes_to_the_answer_scan(self):
        src = read("Passing.java")
        decide = src[src.index("Decision decide("):src.index("boolean skippedStep(")]
        self.assertNotIn("isSpell()", decide, "an opponent's spell must not be handed to Forge's auto-pass any more")
        mine = decide.index("if (mine)")
        self.assertLess(mine, decide.index("hasAnswer(me, top)"))
        self.assertIn('return top.isSpell() ? "Spell" : top.isTrigger() ? "Triggered" : "Activated";', src)
        self.assertIn("String want = stackKind(top);", src)
        self.assertNotIn('trigger ? "Triggered" : "Activated"', src)

    def test_the_passed_line_names_a_spell(self):
        self.assertIn('m.addProperty("kind", top.isSpell() ? "spell" : top.isTrigger() ? "trigger" : "ability");',
                      read("Passing.java"))

    def test_slow_still_defers_before_the_answer_scan(self):
        decide = read("Passing.java")
        decide = decide[decide.index("Decision decide("):decide.index("boolean skippedStep(")]
        self.assertLess(decide.index("if (slow)"), decide.index("hasAnswer(me, top)"))

    def test_the_jar_is_built_from_these_sources(self):
        import setup_forge
        import zipfile
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            stamp = z.read(setup_forge.BRIDGE_STAMP).decode("ascii").strip()
        self.assertEqual(stamp, setup_forge.bridge_source_hash())


class TableTests(p43.TableTests):
    """Only the feed test; the rest of patch 43's table tests run in their own module."""

    def test_a_passed_spell_shows_as_a_spell(self):
        gui, s = self.make()
        s.handle({"t": "passed", "card": "Grizzly Bears", "player": "AI 1", "kind": "spell"})
        gui.track_passing()
        texts = ["".join(t for t, _st in row.segs) for row, _born in gui.feed]
        self.assertIn("Passed for you: AI 1's Grizzly Bears spell", texts)


# p43.TableTests' own tests are not repeated here.
for _name in list(vars(p43.TableTests)):
    if _name.startswith("test_"):
        setattr(TableTests, _name, None)


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    classic = False
    setUp = p43.LiveTests.setUp
    setup = p43.LiveTests.setup
    watch = p43.LiveTests.watch
    at_main1 = p43.LiveTests.at_main1

    AI = ("aibattlefield=Forest;Forest;Forest", "aihand=Grizzly Bears")

    def opponents_turn(self, hand, slow=False, until_end=False):
        """Pass my turn with `hand` (two Islands untapped) and log every priority question on the opponent's turn until the
        Bears are cast and resolved (or, with until_end, their end step)."""
        self.setup("humanbattlefield=Island;Island", "humanhand=" + hand, *self.AI)
        self.at_main1()
        if slow:
            self.s.set_speed("slow")
            self.chk.pump(0.5)
        turn, seq = self.s.state.get("turn"), self.s.state.get("inputSeq")
        log = []
        self.s.pass_turn()

        def done(st):
            if st.get("turn", 0) <= turn:
                return False
            if until_end:
                return st.get("phase") == "END_OF_TURN"
            return bool(st.get("stack"))
        st = self.watch(25, until=done, log=log, after_seq=seq, auto_ok=not until_end)
        return turn, st, log

    def bears_on_their_side(self):
        return [c for c in self.s.opponents()[0]["zones"]["battlefield"] if c["name"] == "Grizzly Bears"]

    def test_an_instant_i_could_cast_is_no_answer_to_a_creature_spell(self):
        turn, st, log = self.opponents_turn("Opt", until_end=True)
        self.assertEqual(st.get("phase"), "END_OF_TURN", "the opponent's end step still stops me (Opt castable)")
        self.assertEqual(len(self.bears_on_their_side()), 1, "the Bears resolved")
        spell_stops = [row for row in log if row[0] == turn + 1 and row[3]]
        self.assertEqual(spell_stops, [], "no stop at the opponent's Grizzly Bears with only Opt in hand")
        kinds = [(m.get("card"), m.get("kind")) for m in self.s.passed[self.passed0:]]
        self.assertIn(("Grizzly Bears", "spell"), kinds)

    def test_counterspell_stops_me_at_the_spell(self):
        _turn, st, _log = self.opponents_turn("Counterspell")
        self.assertEqual(p43.stack_names(st), [("Grizzly Bears", "S")])

    def test_stifle_is_no_answer_to_a_spell(self):
        turn, st, log = self.opponents_turn("Stifle", until_end=False)
        # with nothing to stop for, the watch runs to its end: the Bears resolved on the opponent's turn, never asked
        self.assertEqual([row for row in log if row[0] == turn + 1 and row[3]], [])
        self.assertEqual(len(self.bears_on_their_side()), 1)

    def test_slow_still_stops_at_the_spell(self):
        _turn, st, _log = self.opponents_turn("Opt", slow=True)
        self.assertEqual(p43.stack_names(st), [("Grizzly Bears", "S")], "Slow: the stops from before patch 43")


if __name__ == "__main__":
    unittest.main()
