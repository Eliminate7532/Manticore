# SPDX-License-Identifier: GPL-3.0-or-later
"""Round ENG-SCUTE: the Scute Swarm out-of-memory crash (Brawl soak game 56 of 3 Nov: 4 players, turn 30, OutOfMemoryError on
Forge's game thread with 650 permanents, 588 of them Scute Swarm).

The cause was not the tokens themselves (about 0.1 MB each) but a chain of Forge's last-known-information (LKI) snapshots: a
replacement effect's ability keeps the last-state battlefield it was given (ReplacementHandler sets it, nothing clears it),
SpellAbility.copy is shallow, so every LKI copy of Doubling Season kept the previous snapshot alive, and every token made took a
new snapshot. The bridge now carries a copy of Forge's CardCopyService whose LKI copies forget that last state.

  SourceTests  the copy is Forge 9a6f776's own file plus the header and one marked change; it names the bundled Forge build
  JarTests     the bridge jar carries the class; its members match forge.jar's CardCopyService plus the one new method
  LiveTests    (live) an AI with Doubling Season and Scute Swarm grows past 400 permanents: Java's largest live heap stays far
               below what the chain held (over 1.2 GB at 400 permanents with the 0.28.53.15 jar); needs jcmd (a JDK)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import tests.live as live
import forge_client as fc

SRC = os.path.join(BASE, "java_bridge", "src", "forge", "game", "card", "CardCopyService.java")
CLASS = "forge/game/card/CardCopyService.class"
# sha256 of forge-game/src/main/java/forge/game/card/CardCopyService.java at Forge 9a6f776 (raw.githubusercontent.com, LF endings)
UPSTREAM_SHA256 = "1490cdc3455c1575dc501444bae84e5f3b6575fbfe404b5cfa5a0f422c18c52d"
UPSTREAM_COMMIT = "9a6f776"

CHANGES = [
    ("""import forge.game.player.Player;
import forge.game.replacement.ReplacementEffect;                 // Manticore (change 1)
""", """import forge.game.player.Player;
"""),
    ("""        newCopy.setMayPlay(copyFrom.getMayPlay());

        forgetLastStates(newCopy);                      // Manticore (change 1)

        return newCopy;
    }

    /** Manticore (change 1): an LKI copy's replacement abilities drop the last state copied from the original (see the header). */
    private static void forgetLastStates(final Card lki) {
        for (final CardStateName s : lki.getStates()) {
            for (final ReplacementEffect re : lki.getState(s).getReplacementEffects()) {
                for (SpellAbility sa = re.getOverridingAbility(); sa != null; sa = sa.getSubAbility()) {
                    sa.clearLastState();
                }
            }
        }
    }
""", """        newCopy.setMayPlay(copyFrom.getMayPlay());

        return newCopy;
    }
"""),
]


def read_copy():
    with open(SRC, "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")       # a Windows checkout may have CRLF


def upstream_from(text):
    """Forge's file, rebuilt from the copy by taking out the header and the change. Raises if the copy has moved on."""
    assert text.startswith("/*"), "the copy should start with its header comment"
    text = text[text.index("*/\n") + 3:]
    for ours, forge in CHANGES:
        if text.count(ours) != 1:
            raise AssertionError("change not found exactly once in the copy:\n" + ours)
        text = text.replace(ours, forge)
    return text


class SourceTests(unittest.TestCase):
    def test_the_copy_is_forges_own_file_plus_the_marked_change(self):
        rebuilt = upstream_from(read_copy())
        self.assertEqual(hashlib.sha256(rebuilt.encode("utf-8")).hexdigest(), UPSTREAM_SHA256,
                         "CardCopyService.java differs from Forge 9a6f776's by more than its header and the marked change")

    def test_it_names_the_forge_build_it_was_copied_from_and_that_is_the_bundled_one(self):
        self.assertIn(f"at Forge {UPSTREAM_COMMIT}", read_copy()[:400])
        with open(os.path.join(BASE, "forge_bundle", "forge_runtime.manifest.json"), encoding="utf-8") as f:
            commit = json.load(f)["forge_commit"]
        self.assertTrue(commit.startswith(UPSTREAM_COMMIT),
                        f"Forge is now {commit[:7]}: re-copy CardCopyService.java from it and re-apply the Manticore change")

    def test_the_lki_copy_forgets_its_replacement_abilities_last_state_last(self):
        body = read_copy().split("public Card getLKICopy(Map<Integer, Card> cachedMap) {", 1)[1]
        body = body.split("\n    }\n", 1)[0]
        code = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("//")]
        self.assertEqual(code[-2:], ["forgetLastStates(newCopy);                      // Manticore (change 1)", "return newCopy;"])

    def test_the_readme_names_the_copied_class(self):
        with open(os.path.join(BASE, "README.txt"), encoding="utf-8") as f:
            self.assertIn("CardCopyService", f.read())


class JarTests(unittest.TestCase):
    def test_the_bridge_jar_carries_the_class(self):
        with zipfile.ZipFile(fc.BRIDGE_SOURCE) as z:
            self.assertIn(CLASS, set(z.namelist()))

    @staticmethod
    def members(jar):
        r = subprocess.run([shutil.which("javap"), "-p", "-cp", jar, "forge.game.card.CardCopyService"], capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-500:])
        return {ln.strip() for ln in r.stdout.splitlines() if ln.startswith("  ") and "lambda$" not in ln}

    @unittest.skipUnless(shutil.which("javap"), "needs a JDK (javap)")
    def test_its_members_match_forges_own_class(self):
        forge = os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")
        if not os.path.isfile(forge):
            self.skipTest("forge_runtime/forge.jar is not set up here")
        ours, theirs = self.members(fc.BRIDGE_SOURCE), self.members(forge)
        self.assertEqual(theirs - ours, set(), "members of Forge's CardCopyService missing from the bridge's copy")
        self.assertEqual(ours - theirs, {"private static void forgetLastStates(forge.game.card.Card);"})


# Measured in the sandbox (Linux, 2 cores, Forge 9a6f776, -Xmx3072m), live heap after a full GC (jcmd GC.class_histogram) at 400
# AI permanents with this board: 1,215 MB on the 0.28.53.15 jar, 234 MB with the fix (checkpoint/runs/r4_ds, r5_ds_fixed on claude/round-eng-scute).
LIVE_LIMIT_MB = 700
GROW_TO = 400


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
@unittest.skipUnless(shutil.which("jcmd"), "needs a JDK (jcmd) to read Java's live heap")
class LiveTests(unittest.TestCase):
    def test_doubling_season_and_scute_swarm_do_not_pile_up_snapshots(self):
        import card_check as cc
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Nissa, Who Shakes the World"], ["Forest"] * 99, "Opp")
        fc.sync_bridge()
        s = fc.ForgeSession(mine, [opp], name="Me", seed=7, dev=True, memory_mb=3072)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end, mem = time.time() + 150, {}
        while time.time() < end:
            s.poll()
            st = s.state
            if st and (st.get("prompt") or {}).get("message", "").startswith("Priority:"):
                break
            if s.requests:
                s.answer(s.requests[0], cc.request_answer(s.requests[0], {}))
            mv = cc.next_move(st, [], mem) if st else None
            if mv and mv[0] == "ok":
                s.ok()
                time.sleep(0.4)
            time.sleep(0.05)
        before = s.setups_done
        s.setup(["activeplayer=p1", "activephase=MAIN1", "turn=5", "p0life=1000000", "p1life=40", "removesummoningsickness=true",
                 "p0battlefield=Island", "p0hand=Island", "p0library=" + ";".join(["Island"] * 40),
                 "p1battlefield=" + ";".join(["Forest"] * 6 + ["Scute Swarm"] * 40 + ["Doubling Season"]),
                 "p1hand=Forest;Forest;Forest", "p1library=" + ";".join(["Forest"] * 40)])
        end = time.time() + 60
        while time.time() < end and s.setups_done == before:
            s.poll()
            time.sleep(0.1)
        self.assertGreater(s.setups_done, before, "the set-up never finished")
        grown, end = False, time.time() + 300
        while time.time() < end and not s.game_over and not s.exited:
            s.poll()
            st = s.state or {}
            players = st.get("players") or []
            if len(players) > 1 and len((players[1].get("zones") or {}).get("battlefield", [])) >= GROW_TO:
                grown = True
                break
            if s.requests:
                s.answer(s.requests[0], cc.request_answer(s.requests[0], {}))
            elif st.get("asking") and (st.get("prompt") or {}).get("ok"):
                s.ok()
            time.sleep(0.05)
        self.assertFalse(s.exited, "the engine died (out of memory?)")
        self.assertTrue(grown, "the swarm never reached %d permanents (game over: %s)" % (GROW_TO, s.game_over))
        live_mb = live_heap_mb(s.proc.pid)
        self.assertLess(live_mb, LIVE_LIMIT_MB, "live heap after a full GC at %d+ permanents" % GROW_TO)


def live_heap_mb(pid):
    """Java's live heap, from its own class histogram (jcmd runs a full GC first): the "Total" line's bytes, in MB."""
    out = subprocess.run([shutil.which("jcmd"), str(pid), "GC.class_histogram"], capture_output=True, text=True,
                         timeout=300).stdout
    total = [ln for ln in out.splitlines() if ln.startswith("Total")]
    if not total:
        raise AssertionError("no histogram from jcmd: " + out[-300:])
    return int(total[-1].split()[2]) / (1024 * 1024)

if __name__ == "__main__":
    unittest.main()
