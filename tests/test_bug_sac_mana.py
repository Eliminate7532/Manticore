# SPDX-License-Identifier: GPL-3.0-or-later
"""bug-1567033887345877012 (Karl, 2 Nov 2026): "creatures should never sacrifice for mana IF they can tap for mana".

His Sliver deck (Gemhide Sliver: slivers have "{T}: Add one mana of any color"; Basal Sliver: slivers have "Sacrifice this
permanent: Add {B}{B}") paid for an overloaded Cyclonic Rift with Auto: it tapped Cloudshredder Sliver, then sacrificed it,
tapped Quick Sliver, then sacrificed it - with Gemhide Sliver, The First Sliver and Sliver Gravemother untapped beside them.
Forge's payment (ComputerUtilMana, the AI's and the Auto button's) ordered abilities card by card, so a creature's sacrifice
ability came straight after its own tap ability. The bridge now carries a copy of ComputerUtilMana with the creature's
sacrifice abilities last (docs/bugs/bug-1567033887345877012.md).

  SourceTests  the copy is Forge 9a6f776's file plus its marked changes, nothing else, and names the bundled Forge build
  JarTests     the bridge jar carries the class; its members are forge.jar's plus the one helper
  LiveTests    real Forge: Auto pay taps every sliver and sacrifices none when tapping is enough; when it is not enough it
               still sacrifices one (and pays)
"""
import hashlib
import json
import os
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

SRC = os.path.join(BASE, "java_bridge", "src")
REL = "forge/ai/ComputerUtilMana.java"
CLS = "forge.ai.ComputerUtilMana"
UPSTREAM_COMMIT = "9a6f776"
UPSTREAM_SHA = "8775c783c56366a578b3701772848551c2a0563a0deab530b904185fca75b93b"   # git show 9a6f776:<file>, LF endings
CHANGES = [
    ("""            newAbilities.addAll(filtersFirst ? 0 : newAbilities.size(), filters);
            // Manticore (bug-1567033887345877012): a creature is sacrificed for mana only after every other source
            final List<SpellAbility> sacrifices = new ArrayList<>();
            newAbilities.removeIf(m -> sacrificesCreature(m) && sacrifices.add(m));
            newAbilities.addAll(sacrifices);
""",
     """            newAbilities.addAll(filtersFirst ? 0 : newAbilities.size(), filters);
"""),
    ("""            if (amount < 2 || sacrificesCreature(ma)) {      // Manticore (bug-1567033887345877012): never ahead of the others
""",
     """            if (amount < 2) {
"""),
    ("""                // Manticore (bug-1567033887345877012): a creature tapped for mana can still be sacrificed for more, as when paying
                sourcesForShards.values().removeIf(s -> CardTraitPredicates.isHostCard(saPayment.getHostCard()).test(s)
                        && !(sacrificesCreature(s) && !s.getPayCosts().hasTapCost() && !sacrificesCreature(saPayment)));
""",
     """                sourcesForShards.values().removeIf(CardTraitPredicates.isHostCard(saPayment.getHostCard()));
"""),
    ("""    // Manticore (bug-1567033887345877012): a mana ability that sacrifices the creature it is on
    private static boolean sacrificesCreature(final SpellAbility ma) {
        return ma.getHostCard() != null && ma.getHostCard().isCreature() && ComputerUtilCost.isSacrificeSelfCost(ma.getPayCosts());
    }

""", ""),
]


def read(rel):
    with open(os.path.join(SRC, rel), "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")


class SourceTests(unittest.TestCase):
    def test_the_copy_is_forges_own_file_plus_its_marked_changes(self):
        text = read(REL)
        self.assertTrue(text.startswith("/*"))
        text = text[text.index("*/\n") + 3:]
        for ours, forge in CHANGES:
            self.assertEqual(text.count(ours), 1, "change not found exactly once:\n" + ours)
            text = text.replace(ours, forge)
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), UPSTREAM_SHA,
                         "the copy differs from Forge 9a6f776's by more than its header and its marked changes")

    def test_it_names_the_bundled_forge_build(self):
        with open(os.path.join(BASE, "forge_bundle", "forge_runtime.manifest.json"), encoding="utf-8") as f:
            commit = json.load(f)["forge_commit"]
        self.assertIn(f"at Forge {UPSTREAM_COMMIT}", read(REL)[:400])
        self.assertTrue(commit.startswith(UPSTREAM_COMMIT),
                        f"Forge is now {commit[:7]}: re-copy {REL} from it and re-apply the Manticore changes")


class JarTests(unittest.TestCase):
    def test_the_bridge_jar_carries_the_class(self):
        with zipfile.ZipFile(fc.BRIDGE_SOURCE) as z:
            self.assertIn("forge/ai/ComputerUtilMana.class", z.namelist())

    def test_its_members_are_forges_plus_the_helper(self):
        javap = shutil.which("javap")
        forge = os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")
        if not javap or not os.path.isfile(forge):
            self.skipTest("needs javap and forge_runtime/forge.jar")

        def members(jar):
            r = subprocess.run([javap, "-p", "-cp", jar, CLS], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            return {ln.strip() for ln in r.stdout.splitlines() if ln.startswith("  ") and "lambda$" not in ln}   # javac's own
        ours, theirs = members(fc.BRIDGE_SOURCE), members(forge)
        self.assertEqual(theirs - ours, set())
        self.assertEqual(ours - theirs,
                         {"private static boolean sacrificesCreature(forge.game.spellability.SpellAbility);"})


DECK = os.path.join(BASE, "sample_decks", "spellslinger_veyran.txt")
SLIVERS = "Basal Sliver;Gemhide Sliver;Muscle Sliver;Watcher Sliver"


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveTests(unittest.TestCase):
    """Basal Sliver gives every sliver "Sacrifice this permanent: Add {B}{B}", Gemhide Sliver "{T}: Add one mana of any
    color": four slivers, four mana by tapping. The spell is paid with Auto (OK during the payment), as Karl did."""

    def start(self, spell):
        import card_check as cc
        chk = cc.Checker(DECK, say=lambda *a: None, classic_stops=True)
        chk.start()
        self.addCleanup(chk.stop)
        self.chk, s = chk, chk.s
        n = s.setups_done
        s.setup(["activeplayer=human", "activephase=MAIN1", "humanlife=40", "ailife=40", "ailibrary=Forest;Forest;Forest",
                 "humanlibrary=Island;Island;Island", f"humanbattlefield={SLIVERS}", f"humanhand={spell}"])
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

    def zone(self, name):
        return self.chk.s.me()["zones"][name]

    def pay_with_auto(self, spell):
        s = self.chk.s
        s.click_card(next(c for c in self.zone("hand") if c["name"] == spell)["id"])
        self.assertTrue(self.wait(lambda st: (st.get("input") or "").startswith("InputPayMana")
                                  and ((st.get("prompt") or {}).get("ok") or {}).get("enabled")), "no payment prompt")
        s.ok()                                                        # Auto
        self.assertTrue(self.wait(lambda st: not (st.get("input") or "").startswith("InputPayMana"), 20),
                        "the payment never ended")
        time.sleep(0.5)
        s.poll()

    def slivers(self):
        return [c for c in self.zone("battlefield") if "Sliver" in c["name"]]

    def test_auto_pay_taps_the_slivers_and_sacrifices_none(self):
        self.start("Concentrate")                                     # {2}{U}{U}: exactly what four taps make
        self.pay_with_auto("Concentrate")
        names = [c["name"] for c in self.slivers()]
        self.assertEqual(sorted(names), sorted(SLIVERS.split(";")), "a sliver was sacrificed although tapping was enough")
        self.assertTrue(all(c.get("tapped") for c in self.slivers()), "every sliver should have been tapped")
        self.assertIn("Concentrate", [e["card"]["name"] for e in (self.chk.s.state.get("stack") or [])])

    def test_a_sliver_is_still_sacrificed_when_tapping_is_not_enough(self):
        self.start("Tidings")                                         # {3}{U}{U}: four taps and one sacrifice
        self.pay_with_auto("Tidings")
        self.assertIn("Tidings", [e["card"]["name"] for e in (self.chk.s.state.get("stack") or [])], "the spell wasn't paid")
        self.assertEqual(len(self.slivers()), 3, "exactly one sliver should have been sacrificed")
        self.assertTrue(all(c.get("tapped") for c in self.slivers()), "the slivers left should all have been tapped first")


if __name__ == "__main__":
    unittest.main()
