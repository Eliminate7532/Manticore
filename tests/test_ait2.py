# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AIT2 (23 Oct 2026): the AI's combat predictions without the minutes (round AIT1 found where its 20 s went).

Forge's AI works two things out again and again on a board that doesn't change while it thinks: PumpAllAi asks
ComputerUtilCard.shouldPumpCard about every creature, and before combat each question plans a whole attack
(doesSpecifiedCreatureAttackAI); and a combat prediction asks ComputerUtil.canRegenerate about the same creatures many times
(Ezuri, Renegade Leader's "{G}: Regenerate another target Elf" with 20 mana elves: most of a 15-70 s decision). The bridge now
carries copies of PumpAllAi, ComputerUtilCard and ComputerUtil (java_bridge/src/forge/ai/) that keep those answers inside a scope,
through forge.bridge.PlanCache; --plan-cache off gives Forge's own behaviour back, --plan-cache compare works everything out as
Forge does and logs how often a kept answer would have been different.

  SourceTests  each copy is Forge 9a6f776's own file plus its marked changes, nothing else; each names the bundled Forge build;
               the scopes always end (finally); answers are kept only inside a scope; the switch is in Main, NetHost and "ready"
  JarTests     the bridge jar carries the classes; their members match forge.jar's own (a Forge update turns this red)
  ClientTests  ForgeSession passes --plan-cache only in dev mode and reads "planCache" from "ready"
  LiveTests    real Forge: identical elves share one attack plan and the AI still pumps; "off" keeps nothing and decides the
               same; with everything worked out ("compare") the kept answers never differ
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import forge_client as fc
import tests.live as live

SRC = os.path.join(BASE, "java_bridge", "src")
UPSTREAM_COMMIT = "9a6f776"                  # was fb4d809; the copies were made again from 9a6f776 when FB2 merged (23 Oct)

# file in java_bridge/src -> (its class in the jars, sha256 of Forge 9a6f776's file (git show, LF endings), the change: ours, Forge's)
COPIES = {
    "forge/ai/ability/PumpAllAi.java": (
        "forge.ai.ability.PumpAllAi",
        "69ad014843002ca2bf704f5d3158726917d414b5560a7a28a44bb07ac8ca3d06",
        [("""        boolean result;
        forge.bridge.PlanCache.begin(true);             // Manticore (round AIT2): identical creatures share one attack plan
        try {
        result = ai.getCreaturesInPlay().anyMatch(c -> c.isValid(valid, source.getController(), source, sa)
                && ComputerUtilCard.shouldPumpCard(ai, sa, c, defense, power, keywords));
        } finally {
            forge.bridge.PlanCache.endPump(source);     // Manticore (round AIT2)
        }
""",
          """        boolean result = ai.getCreaturesInPlay().anyMatch(c -> c.isValid(valid, source.getController(), source, sa)
                && ComputerUtilCard.shouldPumpCard(ai, sa, c, defense, power, keywords));
""")]),
    "forge/ai/ComputerUtilCard.java": (
        "forge.ai.ComputerUtilCard",
        "f881eddaf11a1fe6e7efc252cfefdf3aa6ef1837ebb3fdc8392de338c98da78d",
        [("""        final Boolean known = forge.bridge.PlanCache.known(ai, card);       // Manticore (round AIT2)
        if (known != null) {
            return known;
        }
        AiAttackController aiAtk = new AiAttackController(ai, card);
        Combat combat = new Combat(ai);
        aiAtk.declareAttackers(combat);
        return forge.bridge.PlanCache.remember(ai, card, combat.isAttacking(card));   // Manticore (round AIT2)
""",
          """        AiAttackController aiAtk = new AiAttackController(ai, card);
        Combat combat = new Combat(ai);
        aiAtk.declareAttackers(combat);
        return combat.isAttacking(card);
""")]),
    "forge/ai/ComputerUtil.java": (
        "forge.ai.ComputerUtil",
        "0689a4aec686b1141c38fe7e25b7827ef5cf30e64ac0935a9645ecae5d2fff73",
        [("""    public static boolean canRegenerate(Player ai, final Card card) {
        final Boolean known = forge.bridge.PlanCache.regenerationKnown(ai, card);    // Manticore (round AIT2)
        if (known != null) {
            return known;
        }
        if (!card.canBeShielded()) {
            return forge.bridge.PlanCache.regeneration(ai, card, false);            // Manticore (round AIT2)
        }
""",
          """    public static boolean canRegenerate(Player ai, final Card card) {
        if (!card.canBeShielded()) {
            return false;
        }
"""),
         ("""                return forge.bridge.PlanCache.regeneration(ai, card, true);         // Manticore (round AIT2)
            }
        }

        return forge.bridge.PlanCache.regeneration(ai, card, false);                // Manticore (round AIT2)
    }
""",
          """                return true;
            }
        }

        return false;
    }
"""),
         ("""        forge.bridge.PlanCache.begin(false);            // Manticore (round AIT2): one board, so regeneration answers are kept
        try {
        // life won't change
""",
          """        // life won't change
"""),
         ("""        return remainingLife;
        } finally {
            forge.bridge.PlanCache.end();               // Manticore (round AIT2)
        }
    }
""",
          """        return remainingLife;
    }
""")]),
}


def read(rel):
    with open(os.path.join(SRC, rel), "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")         # a Windows checkout may have CRLF


def upstream_from(text, changes):
    """Forge's file, rebuilt from the copy by taking out the header and the marked change. Raises if the copy has moved on."""
    assert text.startswith("/*"), "the copy should start with its header comment"
    text = text[text.index("*/\n") + 3:]
    for ours, forge in changes:
        if text.count(ours) != 1:
            raise AssertionError("change not found exactly once in the copy:\n" + ours)
        text = text.replace(ours, forge)
    return text


class SourceTests(unittest.TestCase):
    def test_each_copy_is_forges_own_file_plus_its_marked_change(self):
        for rel, (_cls, sha, changes) in COPIES.items():
            with self.subTest(rel):
                rebuilt = upstream_from(read(rel), changes)
                self.assertEqual(hashlib.sha256(rebuilt.encode("utf-8")).hexdigest(), sha,
                                 rel + " differs from Forge 9a6f776's by more than its header and its marked change")

    def test_each_names_the_forge_build_it_was_copied_from_and_that_is_the_bundled_one(self):
        with open(os.path.join(BASE, "forge_bundle", "forge_runtime.manifest.json"), encoding="utf-8") as f:
            commit = json.load(f)["forge_commit"]
        for rel in COPIES:
            with self.subTest(rel):
                self.assertIn(f"at Forge {UPSTREAM_COMMIT}", read(rel)[:400])
                self.assertTrue(commit.startswith(UPSTREAM_COMMIT),
                                f"Forge is now {commit[:7]}: re-copy {rel} from it and re-apply the Manticore change")

    def test_the_scope_always_ends(self):
        text = read("forge/ai/ability/PumpAllAi.java")
        body = text[text.index("forge.bridge.PlanCache.begin(true);"):]
        self.assertLess(body.index("try {"), body.index("anyMatch"))
        self.assertLess(body.index("anyMatch"), body.index("} finally {"))
        self.assertLess(body.index("} finally {"), body.index("forge.bridge.PlanCache.endPump(source);"))
        text = read("forge/ai/ComputerUtil.java")
        body = text[text.index("forge.bridge.PlanCache.begin(false);"):]
        self.assertLess(body.index("try {"), body.index("for (Player opp: opps)"))
        self.assertLess(body.index("return remainingLife;"), body.index("} finally {"))
        self.assertLess(body.index("} finally {"), body.index("forge.bridge.PlanCache.end();"))

    def test_answers_are_only_kept_inside_a_scope(self):
        text = read("forge/bridge/PlanCache.java")
        for method, guard in (("public static Boolean known(", "if (s == null || s.pumpDepth == 0) {\n            return null;"),
                              ("public static Boolean regenerationKnown(", "if (s == null) {\n            return null;")):
            body = text[text.index(method):]
            body = body[:body.index("\n    }\n")]
            self.assertIn(guard, body)                                          # no scope (any other caller): work it out
            self.assertIn('if ("compare".equals(mode)) {\n            return null;', body)

    def test_the_switch_is_in_main_netHost_and_ready(self):
        main = read("forge/bridge/Main.java")
        self.assertIn('case "--plan-cache":', main)
        self.assertIn('ready.addProperty("planCache", PlanCache.mode);', main)
        self.assertIn('case "--plan-cache":', read("forge/bridge/NetHost.java"))
        self.assertIn('List.of("on", "off", "compare")', read("forge/bridge/PlanCache.java"))

    def test_the_readme_names_the_copied_classes(self):
        with open(os.path.join(BASE, "README.txt"), encoding="utf-8") as f:
            text = f.read()
        for name in ("PumpAllAi", "ComputerUtilCard", "ComputerUtil.java", "test_ait2"):
            self.assertIn(name, text)


class JarTests(unittest.TestCase):
    def test_the_bridge_jar_carries_the_classes(self):
        with zipfile.ZipFile(fc.BRIDGE_SOURCE) as z:
            names = set(z.namelist())
        for cls in ("forge/ai/ability/PumpAllAi.class", "forge/ai/ComputerUtilCard.class", "forge/ai/ComputerUtil.class",
                    "forge/bridge/PlanCache.class"):
            self.assertIn(cls, names)

    @staticmethod
    def members(jar, cls):
        javap = shutil.which("javap")
        if not javap:
            raise unittest.SkipTest("needs javap (a JDK)")
        r = subprocess.run([javap, "-p", "-cp", jar, cls], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-500:])
        return {ln.strip() for ln in r.stdout.splitlines() if ln.startswith("  ")}

    def test_their_members_match_forges_own_classes(self):
        forge = os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")
        if not os.path.isfile(forge):
            self.skipTest("forge_runtime/forge.jar is not set up here")
        for _rel, (cls, _sha, _ch) in COPIES.items():
            with self.subTest(cls):
                ours, theirs = self.members(fc.BRIDGE_SOURCE, cls), self.members(forge, cls)
                self.assertEqual(theirs - ours, set(), "members of Forge's class missing from the bridge's copy")
                self.assertEqual(ours - theirs, set(), "members the bridge's copy added")


class ClientTests(unittest.TestCase):
    def session(self, **kw):
        return fc.ForgeSession("me.dck", ["opp.dck"], name="Me", **kw)

    def test_plan_cache_goes_on_the_command_line_only_in_dev_mode(self):
        args = self.session(dev=True, plan_cache="off")._bridge_args()
        self.assertEqual(args[args.index("--plan-cache") + 1], "off")
        self.assertNotIn("--plan-cache", self.session(plan_cache="off")._bridge_args())
        self.assertNotIn("--plan-cache", self.session(dev=True)._bridge_args())

    def test_ready_says_which_mode_the_bridge_plays(self):
        s = self.session()
        self.assertIsNone(s.plan_cache_used)
        s.handle({"t": "ready", "protocol": 2, "format": "commander", "planCache": "on"})
        self.assertEqual(s.plan_cache_used, "on")


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveTests(unittest.TestCase):
    """29 Llanowar Elves and Elvish Warmaster ({5}{G}{G}: Elves you control get +2/+2 and deathtouch) against 10 Grizzly Bears,
    the AI active from its upkeep (round AIT1's benchmark board, 2 players): before declare attackers PumpAllAi asks about every
    elf, and each question would plan a whole attack (round AIT2's bench: 26 questions in one decision). The AI pumps in its draw
    step in both modes (bench: Forge's own 7.4 s, this round's 6.3 s). With Ezuri, Renegade Leader instead ("{G}: Regenerate
    another target Elf"), every elf can regenerate and Ezuri can't: a regeneration answer kept for the wrong card shows."""

    LORD = "Elvish Warmaster"

    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()          # the game's own start-up step: the jar built from these sources, not one left in forge_runtime/

    def play(self, mode=None, players=2, elves=29, limit=120, lord=None):
        import card_check as cc
        tmp = tempfile.mkdtemp(prefix="ait2_")
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Teysa Karlov"], ["Plains"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
        s = fc.ForgeSession(mine, [opp] * (players - 1), name="Me", seed=7, dev=True, classic_stops=True, plan_cache=mode)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end, mem = time.time() + 150, {}
        while time.time() < end:                                  # to my first priority, answering what comes before it
            s.poll()
            st = s.state
            msg = ((st or {}).get("prompt") or {}).get("message", "")
            if msg.startswith("Priority:"):
                break
            if st and "start this game" in msg.lower() and not mem.get("first_player"):
                mem["first_player"] = True
                s.click_player(st.get("me"))
            else:
                mv = cc.next_move(st, list(s.requests), mem) if st else None
                if mv:
                    {"answer": lambda: s.answer(mv[1], mv[2]), "click": lambda: s.click_card(mv[1]),
                     "player": lambda: s.click_player(mv[1]), "ok": s.ok, "cancel": s.cancel}[mv[0]]()
            time.sleep(0.3)
        else:
            self.fail("the game did not start")
        lines = ["activeplayer=p1", "activephase=UPKEEP", "turn=8", "removesummoningsickness=true",
                 "p1battlefield=" + ";".join([lord or self.LORD] + ["Llanowar Elves"] * elves + ["Forest"] * 7),
                 "p1library=" + ";".join(["Forest"] * 30), "p1hand=", "p1life=40"]
        for i in [0] + list(range(2, players)):
            lines += ["p%dlife=40" % i, "p%dbattlefield=%s" % (i, ";".join(["Grizzly Bears"] * 10 + ["Plains"])),
                      "p%dlibrary=%s" % (i, ";".join(["Plains"] * 20)), "p%dhand=" % i]
        n = s.setups_done
        s.setup(lines)
        end = time.time() + 60
        while s.setups_done == n and time.time() < end:
            s.poll()
            time.sleep(0.02)
        self.assertGreater(s.setups_done, n, "the board was never set up")
        end, top = time.time() + limit, None
        while time.time() < end:
            s.poll()
            st = s.state or {}
            if st.get("stack"):
                top = st["stack"][0]
                break
            if st.get("phase") in ("COMBAT_DECLARE_ATTACKERS", "MAIN2", "END_OF_TURN"):
                break
            if st.get("asking") and ((st.get("prompt") or {}).get("ok") or {}).get("enabled"):
                s.ok()
                time.sleep(0.05)
            time.sleep(0.01)
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.startswith("plancache:")]
        return s, top, lines

    def test_identical_elves_share_one_attack_plan_and_the_ai_still_pumps(self):
        s, top, lines = self.play()
        self.assertEqual(s.plan_cache_used, "on")
        self.assertIsNotNone(top, "the AI put nothing on the stack before combat")
        self.assertEqual((top.get("card") or {}).get("name"), self.LORD)
        m = [re.match(r"plancache: (.+): (\d+) attack questions, (\d+) planned, (\d+) answered", ln) for ln in lines]
        m = [x for x in m if x and x.group(1) == self.LORD]
        self.assertTrue(m, f"no shared plans logged: {lines}")
        asked, planned, reused = (int(m[0].group(i)) for i in (2, 3, 4))
        self.assertGreaterEqual(reused, 5)                # sandbox, seed 7: 26 questions, 2 planned, 24 answered
        self.assertLessEqual(planned * 3, asked)
        self.assertEqual(asked, planned + reused)

    def test_off_keeps_nothing_and_decides_the_same(self):
        s, top, lines = self.play("off")
        self.assertEqual(s.plan_cache_used, "off")
        self.assertEqual(lines, [])
        self.assertIsNotNone(top, "the AI put nothing on the stack before combat")
        self.assertEqual((top.get("card") or {}).get("name"), self.LORD)

    def test_kept_answers_never_differ_when_everything_is_worked_out(self):
        s, _top, lines = self.play("compare")
        self.assertEqual(s.plan_cache_used, "compare")
        mine = [ln for ln in lines if ln.startswith(f"plancache: compare {self.LORD}:")]
        self.assertTrue(mine, f"no compare line: {lines}")
        # How many creatures are asked depends on shouldPumpCard's random draws, which run differently once every plan is made
        # (sandbox, seed 7: 4 questions, 2 of them repeats, against 26 with plans shared); regeneration is asked thousands of times.
        self.assertGreaterEqual(sum(int(re.search(r" same=(\d+)", ln).group(1)) for ln in mine), 1)
        self.assertGreaterEqual(sum(int(re.search(r"regeneration asked=\d+ repeats=(\d+)", ln).group(1)) for ln in mine), 1000)
        for ln in lines:                                  # attack plans and regeneration, in the pump-all check and elsewhere
            for d in re.findall(r"differ=(\d+)", ln):
                self.assertEqual(int(d), 0, ln)

    def test_regeneration_answers_never_differ_with_ezuri(self):
        s, top, lines = self.play("compare", elves=12, lord="Ezuri, Renegade Leader")
        mine = [ln for ln in lines if ln.startswith("plancache: compare Ezuri, Renegade Leader:")]
        self.assertTrue(mine, f"no compare line: {lines}")
        self.assertGreaterEqual(sum(int(re.search(r"regeneration asked=\d+ repeats=(\d+)", ln).group(1)) for ln in mine), 1000)
        for ln in lines:
            for d in re.findall(r"differ=(\d+)", ln):
                self.assertEqual(int(d), 0, ln)
