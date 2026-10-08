# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch 45 (6 Oct 2026): the mana-payment race (soak nights 11-12, "A, B, C"; claude/FIX_PAYMENT_RACE_SPEC_2026-10-04.md).

Paying with a source whose cost asks a question (Ashnod's Altar, Phyrexian Altar, Springleaf Drum): Forge runs the mana ability
on a game thread, and when the cost's question closes its input queue shows the payment again on the GUI thread while that
thread is still paying. Forge's InputPayMana.showMessage() then scanned the whole board (the "you could act with this" glow the
bridge turns on), rebuilding static effects beside the game thread, and unlocked the payment early, so a second tap started a
second mana ability beside the first. Reproduced live before the fix (sandbox, 6 Oct): see LiveTests.

The bridge now carries its own copy of Forge's InputPayMana (java_bridge/src/forge/gamemodes/match/input/), as it does for
MulliganService, with two changes: showMessage() waits while a mana ability is still being paid, and an ability that throws
still unlocks the payment.

  SourceTests  the copy is Forge fb4d809's own file plus the two marked changes, nothing else; it names the bundled Forge build
  JarTests     the bridge jar carries the class; its members match forge.jar's own InputPayMana (a Forge update that changes
               the class turns this red: re-copy it and re-apply the two changes)
  LiveTests    real Forge: a tap while the Altar's ability is still running is refused as "busy" (fails on patch 44's bridge);
               the glow comes back once it has paid; a concede while it waits for its colour still ends the game
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
import tests.live as live

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "gamemodes", "match", "input", "InputPayMana.java")
CLASS = "forge/gamemodes/match/input/InputPayMana.class"
# sha256 of forge-gui/src/main/java/forge/gamemodes/match/input/InputPayMana.java at Forge fb4d809 (git show, LF endings)
UPSTREAM_SHA256 = "fd11c0ad585c0dd380bc62fc3f3e890fb3ccec90fa030c9e77ad1ad99119e938"
UPSTREAM_COMMIT = "fb4d809"

# The two changes, exactly as they stand in the copy, and what Forge has in their place.
CHANGES = [
    ("""        if (isFinished()) { return; }
        if (locked) {
            // Manticore (change 1): a mana ability is still being paid on the game thread. Its own onStateChanged() refreshes
            // the payment when it ends; scanning the board or unlocking here would race it.
            updateButtons();
            return;
        }
        getController().pushActionableCards(true);""",
     """        if (isFinished()) { return; }
        getController().pushActionableCards(true);"""),
    ("""        game.getAction().invoke(() -> {
            try {
            if (PlaySpellAbility""",
     """        game.getAction().invoke(() -> {
            if (PlaySpellAbility"""),
    ("""            }
            } catch (RuntimeException | Error e) {
                unlockAfterError();                     // Manticore (change 2)
                throw e;
            }
            // Need to call this to unlock""",
     """            }
            // Need to call this to unlock"""),
    ("""                try {
                    runAsAi(proc);
                } catch (RuntimeException | Error e) {
                    unlockAfterError();                 // Manticore (change 2)
                    throw e;
                }
                onStateChanged();""",
     """                runAsAi(proc);
                onStateChanged();"""),
    ("""    /** Manticore (change 2): the ability threw. Unlock on the GUI thread (where "locked" is read and normally cleared), and
     *  refresh the payment the usual way if it is still open. Forge's original left it locked: every click was refused. */
    private void unlockAfterError() {
        FThreads.invokeInEdtNowOrLater(() -> {
            locked = false;
            if (!isFinished()) {
                onStateChanged();
            }
        });
    }

""", ""),
]


def read_copy():
    with open(SRC, "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")       # a Windows checkout may have CRLF


def upstream_from(text):
    """Forge's file, rebuilt from the copy by taking out the header and the two changes. Raises if the copy has moved on."""
    head_end = text.index("*/\n") + 3
    assert text.startswith("/*"), "the copy should start with its header comment"
    text = text[head_end:]
    for ours, forge in CHANGES:
        if text.count(ours) != 1:
            raise AssertionError("change not found exactly once in the copy:\n" + ours)
        text = text.replace(ours, forge)
    return text


class SourceTests(unittest.TestCase):
    def test_the_copy_is_forges_own_file_plus_the_two_marked_changes(self):
        rebuilt = upstream_from(read_copy())
        self.assertEqual(hashlib.sha256(rebuilt.encode("utf-8")).hexdigest(), UPSTREAM_SHA256,
                         "InputPayMana.java differs from Forge fb4d809's by more than its header and two marked changes")

    def test_it_names_the_forge_build_it_was_copied_from_and_that_is_the_bundled_one(self):
        self.assertIn(f"at Forge {UPSTREAM_COMMIT}", read_copy()[:400])
        with open(os.path.join(BASE, "forge_bundle", "forge_runtime.manifest.json"), encoding="utf-8") as f:
            commit = json.load(f)["forge_commit"]
        self.assertTrue(commit.startswith(UPSTREAM_COMMIT),
                        f"Forge is now {commit[:7]}: re-copy InputPayMana.java from it and re-apply the two Manticore changes")

    def test_show_message_waits_while_a_mana_ability_is_being_paid(self):
        body = read_copy().split("public void showMessage() {", 1)[1].split("\n    }\n", 1)[0]
        self.assertLess(body.index("if (locked)"), body.index("pushActionableCards"))
        guard = body[body.index("if (locked)"):body.index("pushActionableCards")]
        code = "\n".join(ln for ln in guard.splitlines() if not ln.strip().startswith("//"))
        self.assertIn("return;", code)
        self.assertNotIn("onStateChanged", code)             # the unlock waits for the game thread's own refresh

    def test_both_game_thread_tasks_unlock_after_an_error(self):
        text = read_copy()
        self.assertEqual(text.count("unlockAfterError();"), 2)        # the mana ability and Auto
        self.assertIn("private void unlockAfterError()", text)
        self.assertEqual(text.count("throw e;"), 2)                   # the error still goes where it went before

    def test_the_readme_names_both_copied_forge_classes(self):
        with open(os.path.join(BASE, "README.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("InputPayMana", text)
        self.assertIn("MulliganService", text)


class JarTests(unittest.TestCase):
    def test_the_bridge_jar_carries_the_payment_class(self):
        with zipfile.ZipFile(fc.BRIDGE_SOURCE) as z:
            names = set(z.namelist())
        self.assertIn(CLASS, names)
        self.assertIn("forge/game/mulligan/MulliganService.class", names)

    @staticmethod
    def members(jar):
        javap = shutil.which("javap")
        r = subprocess.run([javap, "-p", "-cp", jar, "forge.gamemodes.match.input.InputPayMana"], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-500:])
        lines = {ln.strip() for ln in r.stdout.splitlines() if ln.startswith("  ")}
        return {ln for ln in lines if "lambda$" not in ln}           # javac numbers the lambdas itself

    @unittest.skipUnless(shutil.which("javap"), "needs a JDK (javap)")
    def test_its_members_match_forges_own_class(self):
        forge = os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")
        if not os.path.isfile(forge):
            self.skipTest("forge_runtime/forge.jar is not set up here")
        ours, theirs = self.members(fc.BRIDGE_SOURCE), self.members(forge)
        self.assertEqual(theirs - ours, set(), "members of Forge's InputPayMana missing from the bridge's copy")
        self.assertEqual(ours - theirs, {"private void unlockAfterError();"})


DECK = os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt")


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveTests(unittest.TestCase):
    """Phyrexian Altar paying for Coalition Victory ({3}{W}{U}{B}{R}{G}): after its "sacrifice a creature" question, Forge asks
    which colour the Altar makes - on the game thread, in the middle of the ability. That pause makes the race certain: before
    the fix the payment was already unlocked by then, and a tap on a Forest started a second mana ability beside the first."""

    def start(self, battlefield="Phyrexian Altar;Grizzly Bears;Forest"):
        import card_check as cc
        self.cc = cc
        chk = cc.Checker(DECK, say=lambda *a: None, classic_stops=True)
        chk.start()
        self.addCleanup(chk.stop)
        self.chk, s = chk, chk.s
        n = s.setups_done
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=40", "ailibrary=Forest;Forest;Forest",
                 "humanlibrary=Island;Island;Island", f"humanbattlefield={battlefield}", "humanhand=Coalition Victory"])
        self.wait(lambda st: s.setups_done > n, 60)
        chk.pump(1.0)
        return s

    def wait(self, pred, secs=20):
        s = self.chk.s
        end = time.time() + secs
        while time.time() < end:
            s.poll()
            if pred(s.state or {}):
                return True
            time.sleep(0.01)
        return False

    def card(self, zone, name):
        return next(c for c in self.chk.s.me()["zones"][zone] if c["name"] == name)

    def to_the_colour_question(self):
        """Cast Coalition Victory, tap the Altar, sacrifice the Bears: Forge then asks for the colour, mid-ability."""
        s = self.chk.s
        s.click_card(self.card("hand", "Coalition Victory")["id"])
        self.assertTrue(self.wait(lambda st: (st.get("input") or "").startswith("InputPayMana")), "no payment prompt")
        s.click_card(self.card("battlefield", "Phyrexian Altar")["id"])
        self.assertTrue(self.wait(lambda st: st.get("input") == "InputSelectCardsFromList"), "no sacrifice question")
        s.click_card(self.card("battlefield", "Grizzly Bears")["id"])
        time.sleep(0.2)
        s.poll()
        st = s.state or {}
        if st.get("input") == "InputSelectCardsFromList" and ((st.get("prompt") or {}).get("ok") or {}).get("enabled"):
            s.ok()
        self.assertTrue(self.wait(lambda st: bool(s.requests), 10), "Forge never asked for the Altar's colour")
        time.sleep(0.3)
        s.poll()

    def engine_problems(self):
        return [ln for ln in self.chk.new_engine_lines() if re.search(r"Exception|Report a crash", ln)]

    def test_a_tap_while_the_altar_is_still_paying_is_refused_as_busy(self):
        s = self.start()
        self.to_the_colour_question()
        self.assertTrue((s.state.get("input") or "").startswith("InputPayMana"))
        before = len(s.dropped)
        s.click_card(self.card("battlefield", "Forest")["id"])
        self.wait(lambda st: len(s.dropped) > before, 3)
        time.sleep(0.5)
        s.poll()
        self.assertIn("busy", [d.get("reason") for d in s.dropped[before:]], "a tap was let through mid-ability")
        self.assertFalse(self.card("battlefield", "Forest").get("tapped"), "a second mana ability ran beside the Altar's")
        req = s.requests[0]
        s.answer(req, self.cc.request_answer(req, {}))                 # finish the Altar's ability
        tapped = False
        end = time.time() + 15
        while time.time() < end and not tapped:                         # as a person would: tap again until it takes
            s.click_card(self.card("battlefield", "Forest")["id"])
            tapped = self.wait(lambda st: self.card("battlefield", "Forest").get("tapped"), 0.6)
        self.assertTrue(tapped, "the Forest tap once the Altar's ability had finished")
        self.assertEqual(self.engine_problems(), [])

    def test_the_glow_comes_back_once_the_altar_has_paid(self):
        s = self.start("Phyrexian Altar;Grizzly Bears;Grizzly Bears;Forest")
        self.to_the_colour_question()
        req = s.requests[0]
        s.answer(req, self.cc.request_answer(req, {}))
        glowing = lambda name: (self.card("battlefield", name).get("weak") or 0) > 0
        self.assertTrue(self.wait(lambda st: not s.requests and (st.get("input") or "").startswith("InputPayMana")
                                  and glowing("Forest") and glowing("Phyrexian Altar"), 10),     # one more Bears to sacrifice
                        "the Forest and the Altar aren't glowing after the Altar paid")
        self.assertEqual(self.engine_problems(), [])

    def test_a_concede_while_the_altar_waits_for_its_colour_still_ends_the_game(self):
        s = self.start()
        self.to_the_colour_question()
        s.concede()
        self.assertTrue(self.wait(lambda st: s.game_over, 30), "the concede never ended the game")
        self.assertEqual([ln for ln in self.engine_problems() if "InterruptedException" not in ln], [])


if __name__ == "__main__":
    unittest.main()
