# SPDX-License-Identifier: GPL-3.0-or-later
"""Round 28b, section 2.2: Forge's "do it twice" shapes, live.

FORGE_REPEAT_BEHAVIOURS.md section E lists ten situations worth playing twice in one game, with a
different answer the second time, because Forge sometimes asks a DIFFERENT SHAPE of question the
second time round (round 27c's Spiteful Visions bug was exactly this). Scenario 1 is
tests/test_round27c.py's play_spiteful_to_my_second_draw; this module covers what could be built
with confidence from a card's own rules text, without a JDK to probe Forge directly:

  built here   4 (surveil twice), 6 (a modal spell twice), 7 (a commander removed twice, plus its
               NO_COMMANDER_VARIANT red twin), 10 (yield "until end of turn")
  skipped      2 (two replacement effects: FORGE_REPEAT_BEHAVIOURS.md itself calls this "a real
               unknown... the first probe will show it"), 3 (an optional "you may" trigger fired
               twice: no card I could name with confidence scripts a plain repeatable confirm() for
               this), 5 (scry 2 twice: the exact request shape - "the many + order path" - isn't
               shown by anything already proven live), 9 (blocked by two creatures twice: whether
               the AI's blocks can be forced through Setup Game State is unverified)

Each scenario asserts session.checks == [] AND that bridge_rules.check_stream finds no `fail` in
its OWN record file (a temp file attached to the session's already-running bridge - see
start_recording/stop_recording below; Checker doesn't take a record_path itself, per
docs/incoming/round27d's spec section 2.5: "card_check.py: unchanged... don't copy it").

These are live tests (@unittest.skipUnless(live.live_enabled(), ...)): they're skipped in the VM
(no JDK there) and run by Karl on Windows (EVENING_CHECKLIST.txt, Part 1).
"""
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import bridge_rules as br
import tests.live as live

KINNAN = os.path.join(BASE, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")


def prompt(s):
    return (s.state or {}).get("prompt") or {}


def names(s, zone):
    return [c["name"] for c in s.me()["zones"].get(zone, [])]


def start_recording(chk):
    """Attach a record file to an already-running Checker session, in a temp folder, so this
    scenario's OWN messages (not the whole game since Checker.start()) can be checked with
    bridge_rules.check_stream. Round 28b spec section 2.2 asks for exactly this; Checker itself
    takes no record_path (section 2.5 says not to change card_check.py for it)."""
    tmp_dir = tempfile.mkdtemp(prefix="round28b_scenario_")
    path = os.path.join(tmp_dir, "record.jsonl")
    chk.s._record = open(path, "w", encoding="utf-8")
    return path


def stop_recording(chk):
    if chk.s._record:
        chk.s._record.close()
        chk.s._record = None


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class ScenarioTests(unittest.TestCase):
    def checker(self, *faults):
        import card_check as cc
        chk = cc.Checker(KINNAN, faults=faults)
        chk.start()
        self.addCleanup(chk.stop)
        return chk

    def assert_clean(self, chk, record_path):
        chk.s.poll()
        self.assertEqual(chk.s.checks, [])
        stop_recording(chk)
        findings = br.check_stream(br.load_stream(record_path))
        fails = [f for f in findings if f["severity"] == "fail"]
        self.assertEqual(fails, [], fails)

    def pump_priority(self, s, tries=6, pause=0.5):
        """Pass priority while something is on the stack, or a mana payment is up; used between
        clicks so a spell actually resolves instead of sitting at Priority forever."""
        for _ in range(tries):
            s.poll()
            msg = prompt(s).get("message") or ""
            if "Pay Mana" in msg:
                s.ok()
                time.sleep(pause)
            elif msg.startswith("Priority") and s.state.get("stack"):
                s.ok()
                time.sleep(pause)
            else:
                time.sleep(0.2)

    # -----------------------------------------------------------------------------------------
    # scenario 4: surveil 1 twice - graveyard the first time, top of library the second
    # -----------------------------------------------------------------------------------------
    def test_scenario4_surveil_twice_graveyard_then_top(self):
        chk = self.checker()
        s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Raucous Theater;Consider;Brainstorm", "humanbattlefield=Swamp;Island",
                 "humanlibrary=Forest;Island;Swamp;Plains;Mountain;Island;Island",
                 "aihand=", "ailibrary=Forest;Forest;Forest", "aibattlefield=", "removesummoningsickness=true"]
        self.assertTrue(chk.apply(lines, ["Raucous Theater", "Consider", "Brainstorm"]))
        record_path = start_recording(chk)

        def play_surveil_land(name, keep_on_top):
            card = next(c for c in s.me()["zones"]["hand"] if c["name"] == name)
            s.click_card(card["id"])
            time.sleep(0.6)
            end, p = time.time() + 30, {}
            while time.time() < end:
                s.poll()
                p = prompt(s)
                if "graveyard?" in (p.get("message") or ""):
                    break
                if "Pay Mana" in (p.get("message") or ""):
                    s.ok(); time.sleep(0.4); continue
                self.pump_priority(s, tries=1, pause=0.3)
                time.sleep(0.1)
            self.assertIn("graveyard?", p.get("message") or "", "%s: the surveil question never came" % name)
            (s.ok() if keep_on_top else s.cancel())
            chk.pump(1.0)

        play_surveil_land("Raucous Theater", keep_on_top=False)          # -> graveyard
        self.assertIn("Forest", names(s, "graveyard"), "the surveilled Forest should be in the graveyard")
        # Round 28ba: the second surveil is a spell (Consider: surveil 1, then draw), not a second land drop - resetting
        # humanlandsplayed through Setup Game State did not give a second land drop (checked live: the click did nothing).
        play_surveil_land("Consider", keep_on_top=True)                  # -> stays on top, then Consider draws it
        chk.pump(1.0)
        self.assertEqual(names(s, "graveyard").count("Forest"), 1, "only the first surveil should have binned a card")
        self.assert_clean(chk, record_path)

    # -----------------------------------------------------------------------------------------
    # scenario 6: a modal spell twice, choosing a different mode each time
    # -----------------------------------------------------------------------------------------
    def cast_modal_choosing(self, chk, name, mode_text):
        s = chk.s
        card = next(c for c in s.me()["zones"]["hand"] if c["name"] == name)
        s.click_card(card["id"])
        time.sleep(0.6)
        end, asked = time.time() + 20, None
        while time.time() < end and asked is None:
            s.poll()
            for r in list(s.requests):
                if r.get("kind") == "choose" and len(r.get("items") or []) >= 2:
                    asked = r
                else:
                    s.answer(r, [0] if (r.get("items") or []) else [])
            time.sleep(0.05)
        self.assertIsNotNone(asked, "%s: the mode choice never came" % name)
        # Round 28ba: pick the mode by its text. Forge leaves out modes with no legal target (Abzan Charm's "exile target
        # creature with power 3 or greater" with only Grizzly Bears around), so a position means a different mode per board.
        labels = [(it.get("label") or "") for it in asked.get("items") or []]
        idx = next((i for i, lab in enumerate(labels) if mode_text.lower() in lab.lower()), None)
        self.assertIsNotNone(idx, "%s: no mode containing %r in %s" % (name, mode_text, labels))
        s.answer(asked, [idx])
        self.pump_priority(s, tries=8, pause=0.5)                       # pay, target (the AI's Bears), resolve
        chk.pump(1.0)
        return asked

    def test_scenario6_modal_spell_twice_different_modes(self):
        chk = self.checker()
        # Round 28ba: one board with both charms (a second Setup Game State in the same test was refused), cast in turn.
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Abzan Charm;Jeskai Charm;Brainstorm",
                 "humanbattlefield=Plains;Plains;Swamp;Forest;Island;Island;Mountain",
                 "humanlibrary=Forest;Island;Island;Island", "aihand=", "ailibrary=Forest;Forest;Forest",
                 "aibattlefield=Grizzly Bears", "removesummoningsickness=true"]
        self.assertTrue(chk.apply(lines, ["Abzan Charm", "Jeskai Charm", "Brainstorm"]))
        record_path = start_recording(chk)
        first = self.cast_modal_choosing(chk, "Abzan Charm", "draw two")                   # no target needed
        self.assertIn("Jeskai Charm", names(chk.s, "hand"))
        second = self.cast_modal_choosing(chk, "Jeskai Charm", "lifelink")                  # a different mode, no target
        self.assertNotIn("Jeskai Charm", names(chk.s, "hand"))
        self.assertGreaterEqual(len(first.get("items") or []), 2)
        self.assertGreaterEqual(len(second.get("items") or []), 2)
        self.assert_clean(chk, record_path)

    # -----------------------------------------------------------------------------------------
    # scenario 7: a commander removed twice - to the command zone the first time, left in exile
    # the second (plus the NO_COMMANDER_VARIANT red twin the spec table asks for)
    # -----------------------------------------------------------------------------------------
    def _commander_setup_lines(self, hand):
        return ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                "humanhand=" + ";".join(hand),
                # Round 28ba: 9 lands - Kinnan (UG), Swords (W), Kinnan again with commander tax ({2}{G}{U}), Path (W).
                "humanbattlefield=Forest;Forest;Island;Island;Island;Plains;Plains;Plains;Plains",
                "humanlibrary=Forest;Island;Island;Island;Island;Island", "humancommand=Kinnan, Bonder Prodigy|IsCommander",
                "aihand=", "ailibrary=Forest;Forest;Forest;Forest", "aibattlefield=Grizzly Bears",
                "removesummoningsickness=true"]

    def _cast_kinnan_then_remove(self, chk, removal_name):
        s = chk.s
        kinnan = next(c for c in s.me()["zones"]["command"] if "Kinnan" in c["name"])
        s.click_card(kinnan["id"])
        time.sleep(0.5)
        self.pump_priority(s, tries=6, pause=0.6)
        chk.pump(1.5)
        kinnan = next(c for c in s.me()["zones"]["battlefield"] if "Kinnan" in c["name"])
        removal = next(c for c in s.me()["zones"]["hand"] if c["name"] == removal_name)
        s.click_card(removal["id"])
        time.sleep(0.6)
        s.poll()
        s.click_card(kinnan["id"])
        time.sleep(0.6)
        s.poll()
        self.pump_priority(s, tries=5, pause=0.6)
        end, asked = time.time() + 20, False
        while time.time() < end and not asked:
            s.poll()
            for r in list(s.requests):
                s.answer(r, False if r.get("kind") == "confirm" else [])
            msg = prompt(s).get("message") or ""
            if msg.startswith("Search your library?"):       # Round 28ba: Path to Exile asks me first (a Yes/No prompt)
                s.cancel()
                time.sleep(0.5)
                continue
            asked = "command zone" in msg.lower()
            time.sleep(0.05)
        self.assertTrue(asked, "%s: Forge never asked about the command zone" % removal_name)

    def test_scenario7_commander_removed_twice_yes_then_no(self):
        chk = self.checker()
        s = chk.s
        self.assertTrue(chk.apply(self._commander_setup_lines(["Swords to Plowshares", "Path to Exile", "Brainstorm"]),
                                  ["Swords to Plowshares", "Path to Exile", "Brainstorm"]))
        record_path = start_recording(chk)

        self._cast_kinnan_then_remove(chk, "Swords to Plowshares")
        s.ok()                                                          # Yes: back to the command zone
        chk.pump(2.0)
        self.assertIn("Kinnan, Bonder Prodigy", names(s, "command"))
        self.assertNotIn("Kinnan, Bonder Prodigy", names(s, "exile"))

        self._cast_kinnan_then_remove(chk, "Path to Exile")
        s.cancel()                                                      # No: stays in exile
        chk.pump(2.0)
        self.assertIn("Kinnan, Bonder Prodigy", names(s, "exile"))
        self.assertNotIn("Kinnan, Bonder Prodigy", names(s, "command"))

        self.assert_clean(chk, record_path)

    def test_scenario7_fault_red_twin_no_commander_variant(self):
        chk = self.checker("NO_COMMANDER_VARIANT")
        s = chk.s
        self.assertTrue(chk.apply(self._commander_setup_lines(["Swords to Plowshares", "Brainstorm"]),
                                  ["Swords to Plowshares", "Brainstorm"]))
        record_path = start_recording(chk)
        kinnan = next(c for c in s.me()["zones"]["command"] if "Kinnan" in c["name"])
        s.click_card(kinnan["id"])
        time.sleep(0.5)
        self.pump_priority(s, tries=6, pause=0.6)
        chk.pump(1.5)
        kinnan = next(c for c in s.me()["zones"]["battlefield"] if "Kinnan" in c["name"])
        swords = next(c for c in s.me()["zones"]["hand"] if c["name"] == "Swords to Plowshares")
        s.click_card(swords["id"])
        time.sleep(0.6)
        s.poll()
        s.click_card(kinnan["id"])
        time.sleep(0.6)
        s.poll()
        self.pump_priority(s, tries=5, pause=0.6)
        chk.pump(2.0)
        self.assertIn(("start", "commander_variant_missing"), [(c.get("q"), c.get("rule")) for c in s.checks])
        stop_recording(chk)
        findings = br.check_stream(br.load_stream(record_path))
        fail_rules = {f["rule"] for f in findings if f["severity"] == "fail"}
        # Round 28ba: the start/commander_variant_missing check arrives when the game starts, before this scenario's own
        # recording begins, so it is asserted from session.checks above, not from the record.
        self.assertIn("commander_stranded", fail_rules, findings)

    # -----------------------------------------------------------------------------------------
    # scenario 10: yield "until end of turn", then check the next turn still asks
    # -----------------------------------------------------------------------------------------
    def test_scenario10_yield_turn_then_next_turn_still_asks(self):
        chk = self.checker()
        s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Brainstorm", "humanbattlefield=Forest;Forest;Forest;Forest;Forest",
                 "humanlibrary=Forest;Island;Swamp;Plains;Mountain;Forest;Forest;Forest",
                 "aihand=", "ailibrary=Forest;Forest;Forest;Forest;Forest;Forest;Forest;Forest",
                 "aibattlefield=", "removesummoningsickness=true"]
        self.assertTrue(chk.apply(lines, ["Brainstorm"]))
        record_path = start_recording(chk)
        turn_at_yield = s.state.get("turn")
        self.assertTrue(s.yield_turn())
        end, saw_next_turn_priority = time.time() + 90, False
        while time.time() < end and not saw_next_turn_priority:
            s.poll()
            st = s.state or {}
            msg = prompt(s).get("message") or ""
            if msg.startswith("Priority") and (st.get("turn") or 0) > turn_at_yield and st.get("activePlayer") == st.get("me"):
                saw_next_turn_priority = True
                break
            # Round 28ba: pass every other priority stop too (the AI's upkeep, its end step, ...), or the test waits there.
            if msg.startswith("Priority") and (st.get("stack") or st.get("activePlayer") != st.get("me")):
                s.ok()
                time.sleep(0.4)
            elif (prompt(s).get("ok") or {}).get("enabled") and not msg.startswith("Priority"):
                s.ok()
                time.sleep(0.4)
            else:
                time.sleep(0.1)
        self.assertTrue(saw_next_turn_priority, "the yield should stop at the end of this turn, not carry into my next turn")
        self.assert_clean(chk, record_path)

    # -----------------------------------------------------------------------------------------
    # scenarios that need a Windows probe first - see the module docstring
    # -----------------------------------------------------------------------------------------
    def test_scenario2_two_replacement_effects_needs_a_probe(self):
        self.skipTest("needs a probe on Windows: which cards make Forge ask to order two replacement "
                      "effects at all (FORGE_REPEAT_BEHAVIOURS.md section E, #2, calls this unverified "
                      "even for Opus's own research - candidates: two counter-doublers such as Hardened "
                      "Scales + Doubling Season on one +1/+1 counter, or two 'if it would die, exile "
                      "instead' effects). Once a working pair is found, mirror test_scenario7's shape: "
                      "trigger the replacement twice with a different order each time, and add the "
                      "ORDER_IGNORES_SORTED red twin the spec table asks for.")

    def test_scenario3_optional_trigger_twice_needs_a_probe(self):
        self.skipTest("needs a probe on Windows: a permanent with a repeatable 'you may' triggered "
                      "ability, scripted as a plain confirm() (not a two-branch choice like Bloodgift "
                      "Demon's 'if you don't'), that can be made to trigger twice in one game through "
                      "Setup Game State. Answer yes the first time, no the second, and check the "
                      "ability actually did or didn't happen each time.")

    def test_scenario5_scry_twice_needs_a_probe(self):
        self.skipTest("needs a probe on Windows: FORGE_REPEAT_BEHAVIOURS.md section E, #5 calls this "
                      "'the many + order path', but nothing already proven live shows the exact request "
                      "shape scry uses (unlike surveil, which is a plain prompt with ok/cancel). Cast a "
                      "scry-2 spell (e.g. Preordain), see what kind of request(s) actually arrive, THEN "
                      "write the answering code, bottoming one card the first time and reordering both "
                      "on top the second.")

    def test_scenario9_combat_damage_order_twice_needs_a_probe(self):
        self.skipTest("needs a probe on Windows: whether Setup Game State can force the AI to block one "
                      "attacker with two specific creatures (so an assign/damage-order request actually "
                      "comes up) is unverified - the AI otherwise decides its own blocks. If a setup line "
                      "for that exists, do it twice with the two blockers ordered differently each time.")


if __name__ == "__main__":
    unittest.main()
