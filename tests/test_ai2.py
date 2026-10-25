# SPDX-License-Identifier: GPL-3.0-or-later
"""Round AI2: the AI answers the most dangerous thing on the table, judged from Forge's own two lists (two-card combos,
Wizards' Game Changers) - java_bridge/src/forge/bridge/Threats.java. Offline: the source, the Python plumbing, ai_bench's five
new boards. With a JDK: the Java class itself on the real lists. Live: the bridge's choices on the bench's boards."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_bench
import card_check
import forge_client as fc
from tests import live

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "java_bridge", "src", "forge", "bridge")
LISTS = os.path.join(fc.DEFAULT_RUNTIME, "res", "lists")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


class SourceTests(unittest.TestCase):
    def test_the_ai_controller_asks_threats_after_forge_and_before_the_loop_guard(self):
        src = read(SRC, "LoopGuard.java")
        self.assertIn("Threats.adjust(getPlayer(), super.chooseSpellAbilityToPlay())", src)
        self.assertIn("(cap <= 0 && !Threats.on)", src)        # with the loop guard off, AI2 still needs the controller

    def test_both_engines_read_the_switch_and_say_it_is_on(self):
        for name in ("Main.java", "NetHost.java"):
            src = read(SRC, name)
            self.assertIn('case "--threat-ai": Threats.on = !"off".equalsIgnoreCase(args[++i]); break;', src, name)
            self.assertIn('ready.addProperty("threatAi", Threats.on);', src, name)

    def test_the_lists_are_forges_own(self):
        src = read(SRC, "Threats.java")
        self.assertIn("load(ForgeConstants.LISTS_DIR)", src)
        self.assertIn('"commander-bracket-combos.txt"', src)
        self.assertIn('"gamechangers.txt"', src)
        self.assertNotRegex(src, r'"Kinnan|"Basalt|"Devoted Druid|"Survival')      # no card of ours in the code

    def test_every_log_line_goes_through_quiet(self):
        """card_check.PROBLEM_LINE reads 'Error', 'bridge:' ... in an engine-log line as a problem (round 29a's loop guard line
        failed two soak games that way)."""
        src = read(SRC, "Threats.java")
        notes = [ln for ln in src.splitlines() if "System.err.println(" in ln]
        self.assertTrue(notes)
        for ln in notes:
            self.assertTrue("quiet(" in ln or '"threats: no ' in ln or '"threats: left ' in ln, ln)
            self.assertNotIn("bridge:", ln)

    def test_the_jar_holds_threats(self):
        import zipfile
        with zipfile.ZipFile(os.path.join(BASE, "java_bridge", "forge_bridge.jar")) as z:
            self.assertIn("forge/bridge/Threats.class", z.namelist())


class ClientTests(unittest.TestCase):
    def session(self, **kw):
        s = fc.ForgeSession("me.dck", ["opp.dck"], seed=1, **kw)
        return s

    def test_off_is_sent_only_when_asked_and_only_with_dev(self):
        self.assertIn("--threat-ai", self.session(dev=True, threat_ai=False)._bridge_args())
        args = self.session(dev=True, threat_ai=False)._bridge_args()
        self.assertEqual(args[args.index("--threat-ai") + 1], "off")
        for kw in ({}, {"dev": True}, {"dev": True, "threat_ai": True}, {"threat_ai": False}):
            self.assertNotIn("--threat-ai", self.session(**kw)._bridge_args(), kw)

    def test_ready_and_the_moves_are_kept(self):
        s = self.session()
        s.handle({"t": "ready", "protocol": 2, "threatAi": True, "format": "commander"})
        self.assertIs(s.threat_ai_used, True)
        m = {"t": "ai_threat", "player": "AI 1", "move": "countered", "card": "Counterspell", "target": "Basalt Monolith",
             "targetId": 7, "why": "completes a two-card combo", "turn": 5}
        s.handle(m)
        self.assertEqual(s.threat_moves, [m])

    def test_the_checker_passes_it_on(self):
        chk = card_check.Checker(ai_bench.KINNAN_DECK, threat_ai=False)
        self.addCleanup(chk.tmp.cleanup)
        self.assertIs(chk.threat_ai, False)
        self.assertIn("threat_ai=self.threat_ai", read(BASE, "card_check.py"))


def trial(board, **kw):
    t = ai_bench.Trial(ai_bench.BOARD_BY_KEY[board], 0)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class BenchTests(unittest.TestCase):
    def test_the_five_extra_boards_use_no_kinnan_card(self):
        kinnan = {ai_bench.KINNAN, ai_bench.BASALT, ai_bench.SIGNET, ai_bench.PETAL}
        for b in ai_bench.EXTRA_BOARDS:
            self.assertFalse(b.my_cards() & {ai_bench.KINNAN}, b.key)
            self.assertTrue(b.my_cards() - kinnan, b.key)
        self.assertEqual([b.key for b in ai_bench.EXTRA_BOARDS], ["X1", "X2", "X3", "X4", "X5", "X6"])

    def test_the_extra_boards_have_the_mana_for_their_spells(self):
        for key, land in (("X1", "Plains"), ("X2", "Plains"), ("X5", "Forest"), ("X6", "Swamp")):
            b = ai_bench.BOARD_BY_KEY[key]
            self.assertIn(land, b.my_lands, key)
            self.assertIn("humanbattlefield=" + ";".join(b.mine_bf + b.my_lands), b.setup_lines())

    def test_the_old_boards_keep_their_lands(self):
        for b in ai_bench.BOARDS:
            self.assertEqual(b.my_lands, ai_bench.MY_LANDS, b.key)

    def test_board_keys(self):
        self.assertEqual(ai_bench.board_keys(["extra"]), ["X1", "X2", "X3", "X4", "X5", "X6"])
        self.assertEqual(len(ai_bench.board_keys(["all"])), 16)
        self.assertEqual(ai_bench.board_keys(["c1", " x3"]), ["C1", "X3"])

    def test_any_gone_by_my_turn(self):
        judge = ai_bench.BOARD_BY_KEY["X3"].judge
        druid, vizier, sphinx = ai_bench.DRUID, ai_bench.VIZIER, ai_bench.SPHINX
        self.assertIsNone(judge(trial("X3", at_my_turn=None)))
        self.assertFalse(judge(trial("X3", at_my_turn=[druid, vizier, "Plains"])))           # the Sphinx went: not it
        self.assertFalse(judge(trial("X3", at_my_turn=[druid, vizier, sphinx])))
        self.assertTrue(judge(trial("X3", at_my_turn=[vizier, sphinx])))
        self.assertTrue(judge(trial("X3", at_my_turn=[druid, sphinx])))
        self.assertFalse(judge(trial("X3", at_my_turn=[druid])))                             # one piece and the Sphinx gone
        x4 = ai_bench.BOARD_BY_KEY["X4"].judge
        self.assertTrue(x4(trial("X4", at_my_turn=[ai_bench.BASALT, ai_bench.SOL_RING])))
        self.assertFalse(x4(trial("X4", at_my_turn=[ai_bench.RINGS, ai_bench.BASALT])))

    def test_the_summary_says_which_ai_and_where_ai2_acted(self):
        a = trial("R2", verdict=True, ai2=["removed Kinnan, Bonder Prodigy with Swords to Plowshares (a combo piece, assembled)"])
        b = trial("R2", verdict=True)
        text = ai_bench.summary_text([a, b], 7, threat_ai=None)
        self.assertIn("AI2 threat rules: on", text)
        self.assertIn("2/2   (AI2 acted in 1)", text)
        self.assertIn("AI2 threat rules: off (Forge's AI alone)", ai_bench.summary_text([b], 7, threat_ai=False))

    def test_the_results_say_whether_ai2_was_on(self):
        folder = tempfile.mkdtemp(prefix="ai2_")
        self.addCleanup(shutil.rmtree, folder, True)
        ai_bench.write_results(folder, [trial("C1", verdict=True)], 7, "", threat_ai=False)
        with open(os.path.join(folder, "ai_bench_results.json"), encoding="utf-8") as f:
            self.assertIs(json.load(f)["threat_ai"], False)


def _javac():
    java = fc.find_java()
    for cand in ([os.path.join(os.path.dirname(java), n) for n in ("javac", "javac.exe")] if java else []):
        if os.path.isfile(cand):
            return cand
    return shutil.which("javac")


PROBE = """package forge.bridge;
public class ThreatsProbe {
    public static void main(String[] a) {
        Threats.load(a[0]);
        System.out.println("pairs=" + Threats.comboCount());
        System.out.println("changers=" + Threats.changerCount());
        System.out.println("faces=" + Threats.faces("Tergrid, God of Fright // Tergrid's Lantern"));
        System.out.println("quiet=" + Threats.quiet("threats: AI 1 (Trial and Error) Exception ERROR bridge: Unable to at forge.x"));
    }
}
"""


@unittest.skipUnless(_javac() and os.path.isdir(LISTS), "needs a JDK (javac) and forge_runtime/")
class JavaTests(unittest.TestCase):
    """Threats itself, compiled against the bridge jar, on Forge's real lists."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ai2_probe_")
        src = os.path.join(cls.tmp, "forge", "bridge")
        os.makedirs(src)
        with open(os.path.join(src, "ThreatsProbe.java"), "w", encoding="utf-8") as f:
            f.write(PROBE)
        cp = os.pathsep.join([os.path.join(BASE, "java_bridge", "forge_bridge.jar"), os.path.join(fc.DEFAULT_RUNTIME, "forge.jar")])
        r = subprocess.run([_javac(), "-nowarn", "-cp", cp, "-d", cls.tmp, os.path.join(src, "ThreatsProbe.java")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        env = dict(os.environ)
        env.pop("JAVA_TOOL_OPTIONS", None)
        r = subprocess.run([fc.find_java(), "-cp", cls.tmp + os.pathsep + cp, "forge.bridge.ThreatsProbe", LISTS],
                           capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 0, r.stderr
        cls.out = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_pair_of_the_combo_list_is_read(self):
        n = 0
        with open(os.path.join(LISTS, "commander-bracket-combos.txt"), encoding="utf-8") as f:
            for line in f:
                if not line.strip().startswith("#") and len(line.split("|")) == 3:
                    n += 1
        self.assertGreater(n, 2000)
        self.assertEqual(int(self.out["pairs"]), n)

    def test_every_game_changer_is_read_with_its_faces(self):
        names = set()
        with open(os.path.join(LISTS, "gamechangers.txt"), encoding="utf-8") as f:
            for line in f:
                s = line.strip().lower()
                if s and not s.startswith("#"):
                    names.add(s)
                    if " // " in s:
                        names.update(x.strip() for x in s.split(" // "))
        self.assertGreater(len(names), 50)
        self.assertEqual(int(self.out["changers"]), len(names))
        self.assertEqual(self.out["faces"], "[tergrid, god of fright // tergrid's lantern, tergrid, god of fright, tergrid's lantern]")

    def test_a_log_line_never_reads_as_a_problem(self):
        self.assertTrue(self.out["quiet"].startswith("threats: AI 1 (Trial and Err-or)"))
        self.assertIsNone(card_check.PROBLEM_LINE.search(self.out["quiet"]), self.out["quiet"])


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    """Real Forge, the bench's own boards and judges, one trial each (seed 7)."""

    def run_boards(self, keys, threat_ai=None):
        chk = card_check.Checker(ai_bench.KINNAN_DECK, seed=7, say=lambda *_a: None, per_check=ai_bench.TRIAL_BUDGET,
                                 threat_ai=threat_ai)
        chk.start()
        self.addCleanup(chk.stop)
        out = {k: ai_bench.run_trial(chk, ai_bench.BOARD_BY_KEY[k], 0) for k in keys}
        with open(chk.s.stderr_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
        return chk.s, out, log

    def test_the_rules_act_where_forge_alone_did_not(self):
        """R2: Forge's Swords goes to the Sphinx (0 of 15 right in the baseline) - AI2 exiles Kinnan first. C3: Forge counters
        Arcane Signet (3 of 15 right) - AI2 keeps the Counterspell. X1 and X3: another combo (Devoted Druid + Vizier of
        Remedies)."""
        s, out, log = self.run_boards(["R2", "C3", "X1", "X3"])
        self.assertIs(s.threat_ai_used, True)
        for k, t in out.items():
            self.assertEqual(t.error, "", k)
            self.assertTrue(t.verdict, (k, t.as_dict()))
        self.assertTrue(any(m.startswith("removed Kinnan") for m in out["R2"].ai2), out["R2"].ai2)
        self.assertTrue(any(m.startswith("held Arcane Signet") for m in out["C3"].ai2), out["C3"].ai2)
        lines = [ln for ln in log.splitlines() if ln.startswith("threats: ")]
        self.assertTrue(lines)
        for ln in lines:
            self.assertIsNone(card_check.PROBLEM_LINE.search(ln), ln)

    def test_a_question_before_the_first_turn_is_answered(self):
        """Seed 13: Gemstone Caverns in the Kinnan list's opening hand. Forge asks which opening-hand cards to use; before
        round AI2 the checker only pressed OK and gave up after 150 s (ai_bench's seed 13, twice, 18 Oct)."""
        chk = card_check.Checker(ai_bench.KINNAN_DECK, seed=13, say=lambda *_a: None)
        self.addCleanup(chk.stop)
        self.assertTrue(chk.start())
        self.assertTrue((chk.s.state.get("prompt") or {}).get("message", "").startswith("Priority:"))

    def test_a_game_changer_forge_lets_through_is_countered(self):
        """X6: Vampiric Tutor, a Game Changer of mana value 1. Forge's default profile counters a 1-mana spell 30% of the time
        (res/ai/Default.ai: CHANCE_TO_COUNTER_CMC_1=30); AI2 counters it every time, and must have done so itself in at least one
        of 5 trials (if the AI plays that profile, Forge alone countering all 5 has a chance of 0.3 ** 5 = 0.24%)."""
        chk = card_check.Checker(ai_bench.KINNAN_DECK, seed=7, say=lambda *_a: None, per_check=ai_bench.TRIAL_BUDGET)
        chk.start()
        self.addCleanup(chk.stop)
        trials = [ai_bench.run_trial(chk, ai_bench.BOARD_BY_KEY["X6"], i) for i in range(5)]
        for t in trials:
            self.assertEqual(t.error, "")
            self.assertTrue(t.verdict, t.as_dict())
        self.assertTrue([m for t in trials for m in t.ai2 if m.startswith("countered Vampiric Tutor")], [t.ai2 for t in trials])

    def test_off_is_forges_ai_alone(self):
        s, out, log = self.run_boards(["R2", "C3"], threat_ai=False)
        self.assertIs(s.threat_ai_used, False)
        self.assertEqual(s.threat_moves, [])
        self.assertFalse([ln for ln in log.splitlines() if ln.startswith("threats: ")])
        for t in out.values():
            self.assertEqual(t.error, "")
            self.assertEqual(t.ai2, [])


if __name__ == "__main__":
    unittest.main()
