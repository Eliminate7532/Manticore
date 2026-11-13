# SPDX-License-Identifier: GPL-3.0-or-later
"""Release candidate 0.28.53.16 (task rc16, 4 Nov 2026): Karl's three picks and the places the merged rounds met.

  1  PickTests     Karl, 4 Nov, after the 8 h soak (claude/PROG_TUNE_OVERNIGHT_2026-11-04.md): the game's rpg/data/numbers.json
                   is option A's gold with GOLD1's losses (tools/rpg_soak_settings/option_a_gold1), its progression.json's XP is
                   doubled (xp_x2) and nothing else in it moved, and a missing Pauses setting is Short. No other test pins the
                   game's own numbers, so a planted change to them went unnoticed until this file.
  2  MergeTests    claude/round-spar1 met main in a criss-cross merge (both had merged claude/round-rpg-soak1; git's virtual base
                   held conflict markers): rpg_screens.py keeps main's SND1 world sounds and bug-sell-scroll-2 scroll bars next
                   to PROG4's Gear, PROG5's Trade up and SPAR1's yard; rpg/duel.py keeps GOLD1's loss by kind next to PROG5's
                   gauntlet; licenses/NOTICES.json names PROG4's gear icons and THIRD_PARTY_NOTICES.txt is up to date; the soak
                   records the kind's share (PROG-TUNE's share0, not the largest).
  3  VersionTests  0.28.53.16 with its history line.
  4  RebuildTests  task rc16b (8 Nov 2026, Karl 7 Nov: "Fold them in first"): the candidate rebuilt with STARTER1/1B/1C and five
                   bug fixes. The places merged by hand: bug-1567709499526226001 and bug-1567714045170159718 fixed the same
                   report twice - 1567714's implementation is kept (prompt_view itself knows the rule, free_first=), with
                   1567709's fallback to the session's player count before the first board; SurveilGuard's override sits in
                   LoopGuard's controller with the AI-ATTACK comment back above its own method; the game's starter_upgrade is
                   STARTER1B's (K 4, never Moat).
"""
import json
import os
import subprocess
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
SETTINGS = os.path.join(BASE, "tools", "rpg_soak_settings")


def load(*parts):
    with open(os.path.join(BASE, *parts), encoding="utf-8") as f:
        return json.load(f)


def read(*parts):
    with open(os.path.join(BASE, *parts), encoding="utf-8") as f:
        return f.read()


class PickTests(unittest.TestCase):
    def test_gold_is_option_a_with_gold1_losses(self):
        game, pick = load("rpg", "data", "numbers.json"), load("tools", "rpg_soak_settings", "option_a_gold1", "numbers.json")
        skip = ("about", "starter_upgrade")              # Round STARTER1's key came after the pick (the soak setting has none: K 0)
        self.assertEqual({k: v for k, v in game.items() if k not in skip}, {k: v for k, v in pick.items() if k not in skip})
        self.assertEqual(game["loss_gold_share"], {"regular": 0.05, "dungeon": 0.1, "boss": 0.2})
        self.assertEqual(game["rewards"]["regular"]["gold"], [40, 80])
        self.assertIn("Karl 4 Nov 2026, after the 8 h soak", game["about"])

    def test_xp_is_doubled_and_nothing_else_moved(self):
        game = load("rpg", "data", "progression.json")
        built, x2 = load("tools", "rpg_soak_settings", "as_built", "progression.json"), load("tools", "rpg_soak_settings", "xp_x2", "progression.json")
        self.assertEqual(game["xp"], x2["xp"])
        for k, v in built["xp"].items():
            with self.subTest(kind=k):
                self.assertEqual(game["xp"][k], 2 * v)
        for k in built:
            if k not in ("about", "xp"):
                with self.subTest(key=k):
                    self.assertEqual(game[k], built[k], "only the xp section is Karl's pick")
        self.assertIn("xp is doubled", game["about"])

    def test_the_game_reads_the_picks(self):
        from rpg import economy
        n = economy.read_numbers()
        self.assertEqual(n.loss_shares, {"regular": 0.05, "dungeon": 0.1, "boss": 0.2})
        self.assertEqual(n.restock_after_duels, 3)

    def test_pauses_default_is_short(self):
        import forge_table as ft
        self.assertEqual(ft.PAUSES_DEFAULT, "short")
        src = read("forge_table.py")
        self.assertIn("self.pauses = pz if pz in PAUSES else PAUSES_DEFAULT", src)
        self.assertIn("self.pauses = PAUSES_DEFAULT", src)

    def test_as_built_says_it_is_0_28_53_15(self):
        for f in ("numbers.json", "progression.json"):
            with self.subTest(file=f):
                self.assertIn("0.28.53.15", load("tools", "rpg_soak_settings", "as_built", f)["about"])


class MergeTests(unittest.TestCase):
    def test_rpg_screens_keeps_both_sides(self):
        src = read("rpg_screens.py")
        for line in ("from rpg import worldsound", "import scrollbar", "from rpg import gear as rgear",
                     "from rpg import extras as rextras", "from rpg import spar as rspar"):
            with self.subTest(line=line):
                self.assertIn("\n" + line + "\n", src)
        for marker in ("<<<<<<<", ">>>>>>>", "|||||||"):
            self.assertNotIn("\n" + marker, src)
        self.assertIn("def world_sound_tick(", src)                 # main: SND1
        self.assertIn("def scroll_bar(", src)                       # main: bug-sell-scroll-2
        self.assertIn('("gear", "Gear", True)', src)                # PROG4: the Menu's Gear
        self.assertIn('("trade", "Trade up")', src)                 # PROG5: the shop's Trade up tab

    def test_duel_keeps_gold1_and_the_gauntlet(self):
        src = read("rpg", "duel.py")
        self.assertIn('cost = loss_cost(save, numbers, pd.get("encounter") or "regular")', src)
        self.assertIn('if pd.get("gauntlet"):', src)
        self.assertIn('gold = 0 if gauntlet else numbers.reward_gold(pd["encounter"], save.rng("gold"))', src)

    def test_notices(self):
        ids = [c.get("id") for c in load("licenses", "NOTICES.json")["components"]]
        self.assertIn("rpg-gear-icons", ids)
        self.assertIn("Its gear icons are Manticore's own pixel art (CC0).", read("THIRD_PARTY_NOTICES.txt"))
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "build_notices.py"), "--check"], cwd=BASE,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_soak_records_the_kinds_share(self):
        src = read("tools", "rpg_soak.py")
        self.assertIn('"loss_share": 0.0 if pd.get("gauntlet") else share0,', src)
        self.assertNotIn("self.numbers.loss_gold_share}", src)


class RebuildTests(unittest.TestCase):
    def test_prompt_view_knows_the_free_first_rule_and_the_table_passes_it(self):
        import forge_table as ft
        msg = "Karl, you are going first. Do you want to keep your hand?"
        _h, hint, _ok = ft.prompt_view(msg, "Karl", 0, free_first=False)
        self.assertIn("puts 1 on the bottom", hint)
        _h, hint, _ok = ft.prompt_view(msg, "Karl", 0)
        self.assertIn("the first is free", hint)
        self.assertIn("free_first=self.first_mulligan_free()", read("forge_table.py"))

    def test_before_the_first_board_the_session_player_count_decides(self):
        import types
        import forge_table as ft
        for players, want in ((2, False), (3, True)):
            gui = types.SimpleNamespace(session=types.SimpleNamespace(fmt="standard", state=None, players=players),
                                        game_format=lambda: "standard")
            self.assertIs(ft.ForgeTable.first_mulligan_free(gui), want, players)
        gui = types.SimpleNamespace(session=types.SimpleNamespace(fmt="commander", state=None, players=2),
                                    game_format=lambda: "commander")
        self.assertTrue(ft.ForgeTable.first_mulligan_free(gui))

    def test_the_opening_hand_screen_uses_the_same_rule(self):
        src = read("flow_screens.py")
        self.assertIn('"Free: you draw a new seven." if free_first and gui.mulligans == 0 else', src)
        self.assertEqual(read("forge_table.py").count("def first_mulligan_free"), 1)

    def test_surveil_goes_through_the_guard_and_the_comments_sit_right(self):
        src = read("java_bridge", "src", "forge", "bridge", "LoopGuard.java")
        self.assertIn("return SurveilGuard.limit(getPlayer(), super.arrangeForSurveil(topN));", src)
        self.assertIn("/** Round AI-ATTACK: the attack declaration with a time budget (see AttackCache.declareWithBudget). */\n"
                      "        @Override\n        public void declareAttackers(", src)

    def test_the_starting_decks_are_starter1bs(self):
        self.assertEqual(load("rpg", "data", "numbers.json")["starter_upgrade"],
                         {"per_half": 4, "max_tier": 3, "never": ["Moat"]})


class VersionTests(unittest.TestCase):
    def test_version(self):
        import version
        # rc17: 0.28.53.17 follows; .16's history line must stay (was assertEqual "0.28.53.16")
        self.assertGreaterEqual(tuple(int(x) for x in version.VERSION.split(".")), (0, 28, 53, 16))
        self.assertIn('0.28.53.16 Release candidate (Karl, 4 Nov 2026', read("version.py"))
        self.assertIn("rebuilt 8 Nov as claude/rc-0.28.53.16b", read("version.py"))


if __name__ == "__main__":
    unittest.main()
