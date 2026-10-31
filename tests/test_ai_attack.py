# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AI-ATTACK (27 Oct 2026): the AI's attack planner on huge boards, the same choices in seconds instead of half an hour.

Round TOKENS (claude/TOKENS_2026-10-27.md) measured an AI turn with ~1,000 Scute Swarm tokens a side that didn't finish in 30
minutes, all of it inside AiAttackController.doAssault: every attacker against every blocker, and every one of those questions
walking every card's static abilities. doAssault only looks at the board, so the bridge's copy of AiAttackController
(java_bridge/src/forge/ai/) runs it inside a forge.bridge.AttackCache scope where what depends only on the board is worked out
once; --attack-cache off gives Forge's own planner back, --attack-cache compare works everything out both ways and logs any
difference.

  SourceTests  the copy is Forge 9a6f776's own file plus its marked changes, nothing else; it names the bundled Forge build;
               the scope always ends (finally); outside a scope AttackCache is CombatUtil; the switch is in Main, NetHost, "ready"
  JarTests     the bridge jar carries the classes; the copy's members match forge.jar's own (a Forge update turns this red)
  ClientTests  ForgeSession passes --attack-cache only in dev mode and reads "attackCache" from "ready"
  LiveTests    real Forge: on a board with flying, reach, menace, fear, "can't be blocked", a lure, Bower Passage, Champion of
               Lambholt, Goblin War Drums, Tetsuko Umezawa and Delney, "compare" finds no difference and a planted wrong answer
               is found; "on" and "off" declare the same attack; with 511 Scute Swarm tokens a side the AI's whole turn takes
               under two minutes (Forge's own: 232-276 s); in a 4-player game with 255 tokens a side the AI's attack
               declaration stops at its 30 s budget (Forge's own: over 20 minutes). The last two fail on the jar before this
               round (checked, 27 Oct).

The budget (AttackCache.declareWithBudget, --attack-budget N, 0 = none) is the one part that can change what the AI does: an
attack declaration still planning after 30 s - only boards like these: Forge's own 20 s limit starts after the 4-player
defender choice - keeps what it decided so far if that is legal, else declares Forge's legal minimum.
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
UPSTREAM_COMMIT = "9a6f776"
COPY = "forge/ai/AiAttackController.java"
COPY_CLASS = "forge.ai.AiAttackController"
UPSTREAM_SHA = "35fd5b74fa81d551311039ffabc073c02d6e4d775f790ceaee8eb7356780860e"   # Forge 9a6f776's file (git show, LF)
CHANGES = [   # (the copy's lines, Forge's lines)
    ("        forge.bridge.AttackCache.sortByPowerDesc(this.attackers);   // Manticore (round AI-ATTACK)\n",
     "        CardLists.sortByPowerDesc(this.attackers);\n"),
    ("            if (!forge.bridge.AttackCache.canBeBlocked(attacker, accountedBlockers, null)   // Manticore (round AI-ATTACK)\n",
     "            if (!CombatUtil.canBeBlocked(attacker, accountedBlockers, null)\n"),
    ("                accountedBlockers.removeAll(forge.bridge.AttackCache.getPotentialBestBlockers(attacker, accountedBlockers, null));"
     "   // Manticore (round AI-ATTACK)\n",
     "                accountedBlockers.removeAll(CombatUtil.getPotentialBestBlockers(attacker, accountedBlockers, null));\n"),
    ("                    if (forge.bridge.AttackCache.canBlock(attacker, blocker)) {   // Manticore (round AI-ATTACK)\n",
     "                    if (CombatUtil.canBlock(attacker, blocker)) {\n"),
    ("                if (forge.bridge.AttackCache.canBlock(attacker, blocker)) {   // Manticore (round AI-ATTACK)\n",
     "                if (CombatUtil.canBlock(attacker, blocker)) {\n"),
    ("""            // Manticore (round AI-ATTACK): doAssault only looks at the board, so its answers are kept while it runs
            forge.bridge.AttackCache.begin(ai, this.attackers.size(), this.blockers.size());
            final boolean bAssault;
            try {
                bAssault = doAssault();
            } finally {
                forge.bridge.AttackCache.end();   // Manticore (round AI-ATTACK)
            }
""", "            final boolean bAssault = doAssault();\n"),
]


def read(rel):
    with open(os.path.join(SRC, rel), "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")


def upstream_from(text):
    assert text.startswith("/*"), "the copy should start with its header comment"
    text = text[text.index("*/\n") + 3:]
    for ours, forge in CHANGES:
        if text.count(ours) != 1:
            raise AssertionError("change not found exactly once in the copy:\n" + ours)
        text = text.replace(ours, forge)
    return text


class SourceTests(unittest.TestCase):
    def test_the_copy_is_forges_own_file_plus_its_marked_changes(self):
        rebuilt = upstream_from(read(COPY))
        self.assertEqual(hashlib.sha256(rebuilt.encode("utf-8")).hexdigest(), UPSTREAM_SHA,
                         COPY + " differs from Forge 9a6f776's by more than its header and its marked changes")

    def test_every_change_is_marked(self):
        text = read(COPY)
        self.assertEqual(text.count("forge.bridge.AttackCache."), 7)
        for line in text.splitlines():
            if "forge.bridge.AttackCache." in line and "begin(" not in line:
                self.assertIn("Manticore (round AI-ATTACK)", line)

    def test_it_names_the_forge_build_it_was_copied_from_and_that_is_the_bundled_one(self):
        with open(os.path.join(BASE, "forge_bundle", "forge_runtime.manifest.json"), encoding="utf-8") as f:
            commit = json.load(f)["forge_commit"]
        self.assertIn(f"at Forge {UPSTREAM_COMMIT}", read(COPY)[:400])
        self.assertTrue(commit.startswith(UPSTREAM_COMMIT),
                        f"Forge is now {commit[:7]}: re-copy {COPY} from it and re-apply the Manticore changes")

    def test_the_scope_always_ends(self):
        body = read(COPY)
        body = body[body.index("forge.bridge.AttackCache.begin("):]
        self.assertLess(body.index("try {"), body.index("bAssault = doAssault();"))
        self.assertLess(body.index("bAssault = doAssault();"), body.index("} finally {"))
        self.assertLess(body.index("} finally {"), body.index("forge.bridge.AttackCache.end();"))

    def test_outside_a_scope_attackcache_is_combatutil(self):
        text = read("forge/bridge/AttackCache.java")
        for method, forge in (("public static boolean canBlock(", "return CombatUtil.canBlock(attacker, blocker);"),
                              ("public static boolean canBeBlocked(", "return CombatUtil.canBeBlocked(attacker, blockers, combat);"),
                              ("public static List<Card> getPotentialBestBlockers(",
                               "return CombatUtil.getPotentialBestBlockers(attacker, blockers, combat);"),
                              ("public static void sortByPowerDesc(final List<Card> list)", "CardLists.sortByPowerDesc(list);")):
            body = text[text.index(method):]
            body = body[:body.index("\n    }\n")]
            self.assertIn("if (s == null", body)
            self.assertIn(forge, body)
            self.assertIn('if ("compare".equals(mode)) {', body)        # compare: Forge's answer is the one used

    def test_off_opens_no_scope_and_a_planted_error_needs_dev(self):
        text = read("forge/bridge/AttackCache.java")
        begin = text[text.index("public static void begin("):]
        self.assertIn('if ("off".equals(mode)) {\n            return;', begin[:begin.index("\n    }\n")])
        main = read("forge/bridge/Main.java")
        self.assertIn("if (AttackCache.plantedError != 0 && !dev) {", main)
        self.assertNotIn("--attack-cache-plant", read("forge/bridge/NetHost.java"))

    def test_the_switch_is_in_main_nethost_and_ready(self):
        main = read("forge/bridge/Main.java")
        self.assertIn('case "--attack-cache":', main)
        self.assertIn('ready.addProperty("attackCache", AttackCache.mode);', main)
        self.assertIn('case "--attack-cache":', read("forge/bridge/NetHost.java"))
        self.assertIn('List.of("on", "off", "compare")', read("forge/bridge/AttackCache.java"))

    def test_the_ai_attack_declaration_has_a_budget_and_falls_back_to_a_legal_attack(self):
        guard = read("forge/bridge/LoopGuard.java")
        self.assertIn("AttackCache.declareWithBudget(attacker, combat, () -> super.declareAttackers(attacker, combat));", guard)
        text = read("forge/bridge/AttackCache.java")
        body = text[text.index("static void declareWithBudget("):]
        body = body[:body.index("\n    }\n")]
        self.assertIn("if (secs <= 0) {\n            forge.run();\n            return;", body)       # 0 = Forge's own
        self.assertLess(body.index("AITimeoutTracker.arm("), body.index("forge.run();\n        } catch"))
        self.assertIn("} catch (AITimeoutTracker.Expired e) {", body)
        self.assertIn("} finally {\n            AITimeoutTracker.disarm(limit);", body)
        fall = text[text.index("private static void fallBack("):]
        fall = fall[:fall.index("\n    }\n")]
        self.assertIn("if (CombatUtil.validateAttackers(combat)) {", fall)       # what it had, if legal
        self.assertIn("getLegalAttackers().getLeft()", fall)                      # else Forge's legal minimum
        self.assertIn("public static volatile int budgetSeconds = 30;", text)
        for f in ("forge/bridge/Main.java", "forge/bridge/NetHost.java"):
            self.assertIn('case "--attack-budget":', read(f))
        self.assertIn('ready.addProperty("attackBudget", AttackCache.budgetSeconds);', read("forge/bridge/Main.java"))

    def test_the_readme_names_the_copied_class(self):
        with open(os.path.join(BASE, "README.txt"), encoding="utf-8") as f:
            text = f.read()
        for name in ("AiAttackController", "test_ai_attack"):
            self.assertIn(name, text)


class JarTests(unittest.TestCase):
    def test_the_bridge_jar_carries_the_classes(self):
        with zipfile.ZipFile(fc.BRIDGE_SOURCE) as z:
            names = set(z.namelist())
        for cls in ("forge/ai/AiAttackController.class", "forge/bridge/AttackCache.class"):
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

    def test_the_copys_members_match_forges_own_class(self):
        forge = os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")
        if not os.path.isfile(forge):
            self.skipTest("forge_runtime/forge.jar is not set up here")
        ours, theirs = self.members(fc.BRIDGE_SOURCE, COPY_CLASS), self.members(forge, COPY_CLASS)
        self.assertEqual(theirs - ours, set(), "members of Forge's class missing from the bridge's copy")
        self.assertEqual(ours - theirs, set(), "members the bridge's copy added")


class ClientTests(unittest.TestCase):
    def session(self, **kw):
        return fc.ForgeSession("me.dck", ["opp.dck"], name="Me", **kw)

    def test_attack_cache_goes_on_the_command_line_only_in_dev_mode(self):
        args = self.session(dev=True, attack_cache="compare")._bridge_args()
        self.assertEqual(args[args.index("--attack-cache") + 1], "compare")
        self.assertNotIn("--attack-cache", self.session(attack_cache="off")._bridge_args())
        self.assertNotIn("--attack-cache", self.session(dev=True)._bridge_args())

    def test_ready_says_which_mode_the_bridge_plays(self):
        s = self.session()
        self.assertIsNone(s.attack_cache_used)
        s.handle({"t": "ready", "protocol": 2, "format": "commander", "attackCache": "on"})
        self.assertEqual(s.attack_cache_used, "on")


# Tetsuko Umezawa and Delney: "can't be blocked" rules held by a card that is neither the attacker nor the blocker, for some
# attackers only (the elves, power 1; Delney's: power 2 or less, by blockers of power 3+ - the Hill Giant). Bower Passage and
# Champion of Lambholt keep Ornithopter and Wall of Wood from blocking anything, so Forge leaves those out of the planner's
# blockers before it starts (a planted error that ignored such cards' rules went unnoticed on this board until Tetsuko and
# Delney were added: checkpoint/plants/ in round AI-ATTACK's branch).
ATTACKERS = ["Serra Angel", "Boggart Brute", "Phantom Warrior", "Severed Legion", "Prized Unicorn", "Champion of Lambholt",
             "Bower Passage", "Goblin War Drums", "Tetsuko Umezawa, Fugitive", "Delney, Streetwise Lookout",
             "Grizzly Bears", "Grizzly Bears"] + ["Llanowar Elves"] * 12 + ["Forest"] * 7
BLOCKERS = ["Giant Spider", "Grizzly Bears|Tapped", "Wall of Wood", "Ornithopter", "Severed Legion", "Hill Giant", "Hill Giant"] + \
    ["Grizzly Bears"] * 10 + \
    ["Plains"] * 6


class _Plant(fc.ForgeSession):
    plant = 0

    def _bridge_args(self):
        args = super()._bridge_args()
        if self.plant:
            args += ["--attack-cache-plant", str(self.plant)]
        return args


@unittest.skipUnless(live.live_enabled(), "needs Forge (tests/live.py)")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fc.sync_bridge()          # the game's own start-up step: the jar built from these sources, not one left in forge_runtime/

    def start(self, mode=None, plant=0, seed=7, players=2):
        import card_check as cc
        tmp = tempfile.mkdtemp(prefix="aiattack_")
        mine = fc.write_deck_file(os.path.join(tmp, "me.dck"), ["Teysa Karlov"], ["Plains"] * 99, "Me")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 99, "Opp")
        s = _Plant(mine, [opp] * (players - 1), name="Me", seed=seed, dev=True, classic_stops=False, attack_cache=mode)
        s.plant = plant
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end, mem = time.time() + 150, {}
        while time.time() < end:
            s.poll()
            st = s.state
            msg = ((st or {}).get("prompt") or {}).get("message", "")
            if msg.startswith("Priority:"):
                return s
            if st and "start this game" in msg.lower() and not mem.get("first_player"):
                mem["first_player"] = True
                s.click_player(st.get("me"))
            else:
                mv = cc.next_move(st, list(s.requests), mem) if st else None
                if mv:
                    {"answer": lambda: s.answer(mv[1], mv[2]), "click": lambda: s.click_card(mv[1]),
                     "player": lambda: s.click_player(mv[1]), "ok": s.ok, "cancel": s.cancel}[mv[0]]()
            time.sleep(0.3)
        self.fail("the game did not start")

    def setup(self, s, lines, me_cards):
        n = s.setups_done
        s.setup(lines)
        end = time.time() + 120
        while time.time() < end:
            s.poll()
            st = s.state or {}
            mine = len((s.me() or {}).get("zones", {}).get("battlefield", []))
            if s.setups_done > n and mine == me_cards and st.get("asking") is not False:
                return
            time.sleep(0.02)
        self.fail("the board was never set up")

    def ai_turn(self, s, limit):
        """Answer everything with OK / card_check until the AI has declared its attack and combat is over, or my next turn.
        Returns (the attack as {attacker name: defender}, seconds)."""
        import card_check as cc
        me = (s.state or {}).get("me")
        t0, mem, attack = time.time(), {}, None
        while time.time() - t0 < limit:
            s.poll()
            st = s.state or {}
            if s.exited or s.fatal:
                self.fail(s.fatal or "Forge stopped")
            if st.get("combat") and attack is None and st.get("activePlayer") != me:
                names = {c["id"]: c.get("name") for p in st.get("players", []) for c in p.get("zones", {}).get("battlefield", [])}
                attack = sorted("%s>%s" % (names.get(a["card"], a["card"]), a.get("defender")) for a in st["combat"])
            if st.get("activePlayer") == me and (st.get("turn") or 0) > 8 and \
                    (st.get("prompt") or {}).get("message", "").startswith("Priority") and st.get("asking"):
                return (attack or []), time.time() - t0
            if s.requests:
                s.answer(s.requests[0], cc.request_answer(s.requests[0], mem))
            elif st.get("asking") and ((st.get("prompt") or {}).get("ok") or {}).get("enabled"):
                s.ok()
            time.sleep(0.05)
        return None, time.time() - t0

    def rich(self, mode=None, plant=0):
        s = self.start(mode, plant)
        lines = ["activeplayer=p1", "activephase=MAIN1", "turn=8", "removesummoningsickness=true",
                 "p1battlefield=" + ";".join(ATTACKERS), "p1library=" + ";".join(["Forest"] * 30), "p1hand=", "p1life=40",
                 "p0battlefield=" + ";".join(BLOCKERS), "p0library=" + ";".join(["Plains"] * 30), "p0hand=", "p0life=40"]
        self.setup(s, lines, len(BLOCKERS))
        attack, _ = self.ai_turn(s, 150)
        self.assertIsNotNone(attack, "the AI's turn did not end")
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            log = [ln.strip() for ln in f if ln.startswith("attackcache:")]
        return s, attack, log

    @staticmethod
    def compare_counts(lines):
        out = []
        for ln in lines:
            m = re.match(r"attackcache: compare (\d+) attackers x (\d+) blockers: canBlock asked=(\d+) differ=(\d+) "
                         r"lists asked=(\d+) differ=(\d+) sorts=(\d+) differ=(\d+)", ln)
            if m:
                out.append(tuple(int(x) for x in m.groups()))
        return out

    def test_compare_finds_no_difference_on_a_board_of_evasion_and_blocking_rules(self):
        s, _attack, lines = self.rich("compare")
        self.assertEqual(s.attack_cache_used, "compare")
        counts = self.compare_counts(lines)
        self.assertTrue(counts, f"no compare lines: {lines}")
        self.assertGreater(sum(c[2] for c in counts), 50)            # canBlock asked
        self.assertGreater(sum(c[4] for c in counts), 5)             # lists asked
        self.assertEqual([c[3] for c in counts], [0] * len(counts), lines)
        self.assertEqual([c[5] for c in counts], [0] * len(counts), lines)
        self.assertEqual([c[7] for c in counts], [0] * len(counts), lines)

    def test_compare_finds_a_planted_wrong_answer(self):
        _s, _attack, lines = self.rich("compare", plant=1)
        counts = self.compare_counts(lines)
        self.assertTrue(counts, f"no compare lines: {lines}")
        self.assertGreater(sum(c[3] for c in counts), 0, f"a wrong cached canBlock answer went unnoticed: {lines}")

    def test_on_and_off_declare_the_same_attack(self):
        s_on, on, lines_on = self.rich()
        self.assertEqual(s_on.attack_cache_used, "on")
        s_off, off, lines_off = self.rich("off")
        self.assertEqual(s_off.attack_cache_used, "off")
        self.assertEqual(lines_off, [])
        self.assertTrue(on, "the AI did not attack on this board (the test needs an attack to compare)")
        self.assertEqual(on, off)

    def test_a_four_player_attack_declaration_stops_at_its_budget(self):
        """4 players, 255 Scute Swarm tokens for me and AI 1: AI 1 picks whom to attack by predicting each opponent's next
        combat, before Forge's own time limit starts (over five minutes in this round's bench). The budget ends it."""
        s = self.start(players=4)
        big = ["Forest"] * 10 + ["Scute Swarm"] + ["Scute Swarm|IsToken"] * 255
        lines = ["activeplayer=p1", "activephase=MAIN1", "turn=8", "removesummoningsickness=true"]
        for k in ("p0", "p1", "p2", "p3"):
            lines += ["%sbattlefield=%s" % (k, ";".join(big if k in ("p0", "p1") else ["Forest"] * 10)), "%shand=" % k,
                      "%slife=40" % k, "%slibrary=%s" % (k, ";".join(["Llanowar Elves", "Grizzly Bears", "Giant Growth"] * 4))]
        self.setup(s, lines, 266)
        import card_check as cc
        end, mem, found, last = time.time() + 180, {}, None, 0
        while time.time() < end and not found:
            s.poll()
            st = s.state or {}
            if s.exited or s.fatal:
                self.fail(s.fatal or "Forge stopped")
            if s.requests:
                s.answer(s.requests[0], cc.request_answer(s.requests[0], mem))
            elif st.get("asking") and ((st.get("prompt") or {}).get("ok") or {}).get("enabled"):
                s.ok()                                   # pass, no attack, no blocks: the AIs play on
            if time.time() - last > 1:
                last = time.time()
                with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
                    found = [ln for ln in f if "attack plan passed its 30 s budget" in ln]
            time.sleep(0.05)
        self.assertTrue(found, "AI 1's attack declaration never stopped at its budget")
        self.assertIn("AI 1", found[0])
        self.assertRegex(found[0], r"budget \((3\d|4\d)\d\d\d ms\)")          # 30-49 s, not minutes

    def test_the_ai_turn_with_511_tokens_a_side_takes_under_two_minutes(self):
        s = self.start()
        big = ["Forest"] * 10 + ["Scute Swarm"] + ["Scute Swarm|IsToken"] * 511
        lines = ["activeplayer=p1", "activephase=MAIN1", "turn=8", "removesummoningsickness=true"]
        for k in ("p0", "p1"):
            lines += ["%sbattlefield=%s" % (k, ";".join(big)), "%shand=" % k, "%slife=40" % k,
                      "%slibrary=%s" % (k, ";".join(["Llanowar Elves", "Grizzly Bears", "Giant Growth"] * 4))]
        self.setup(s, lines, 522)
        attack, secs = self.ai_turn(s, 120)       # Forge's own planner: 232-260 s (round TOKENS), this round's: about 25 s
        self.assertIsNotNone(attack, "the AI's turn took over 120 s")
        with open(s.stderr_path, encoding="utf-8", errors="replace") as f:
            self.assertTrue(any(ln.startswith("attackcache: ") and "attackers x" in ln for ln in f))


if __name__ == "__main__":
    unittest.main()
