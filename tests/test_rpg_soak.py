# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG-SOAK1: tools/rpg_soak.py, the Adventure soak.

Offline: the deck policy (rebuild_deck), the summary and its verdict, the output folder kept out of git and the backups, the
--progression file that may not exist yet, pruning. With forge_runtime/ (no Java): whole runs of the real Adventure with a
stand-in for Duels.java, and the planted errors the verdict must catch (duels decided by a coin; the deck never rebuilt; a loss
not taking gold). Live (Forge and Java): a 3-duel run reaches the game's settling step and changes gold."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
import rpg_soak  # noqa: E402

from rpg import cardpool, rules, tiers  # noqa: E402

try:
    from tests import live
    LIVE = live.live_enabled()
except Exception:
    LIVE = False
HAS_RUNTIME = os.path.isdir(os.path.join(BASE, "forge_runtime", "res"))


class Card:
    def __init__(self, name, colours="B", land=False, mv=2, basic=False):
        self.name, self.colours, self.is_land, self.mana_value, self.is_basic = name, colours, land, mv, basic


class Pool:
    def __init__(self, cards):
        self.cards = {c.name: c for c in cards}

    def get(self, n):
        return self.cards.get(n)


class Tiers:
    def __init__(self, t):
        self.t = t

    def tier_of(self, n):
        return self.t.get(n)


class Lists:
    def is_banned(self, n):
        return False

    def is_restricted(self, n):
        return False


class Coll:
    def __init__(self, counts):
        self.c = dict(counts)

    def names(self):
        return list(self.c)

    def count(self, n):
        return self.c.get(n, 0)


def fake_world():
    pool = Pool([Card("Swamp", "B", land=True, basic=True), Card("Weak", "B", mv=5), Card("Weak2", "B", mv=1),
                 Card("Good", "B", mv=3), Card("Great", "B", mv=4), Card("RedBomb", "R", mv=2), Card("Rock", "C", mv=2),
                 Card("Mid", "B", mv=2)])
    tt = Tiers({"Weak": 1, "Weak2": 1, "Good": 2, "Great": 3, "RedBomb": 5, "Rock": 3, "Mid": 1})
    return pool, tt


class PolicyTests(unittest.TestCase):
    def rebuild(self, main, owned):
        pool, tt = fake_world()
        deck = {"main": [list(x) for x in main]}
        with mock.patch("rpg.collection.problems", return_value=[]):
            swaps = rpg_soak.rebuild_deck(deck, Coll(owned), pool, tt, Lists())
        return deck, swaps

    def test_the_weakest_card_goes_first_and_the_best_owned_on_colour_card_comes_in(self):
        deck, swaps = self.rebuild([["Swamp", 17], ["Weak", 1], ["Weak2", 1], ["Mid", 1]],
                                   {"Swamp": 17, "Weak": 1, "Weak2": 1, "Mid": 1, "Great": 1, "Good": 1})
        # lowest tier, then the HIGHEST mana value: Weak (tier 1, mv 5) before Weak2 (tier 1, mv 1) and Mid (tier 1, mv 2)
        self.assertEqual(swaps[0], ("Weak", "Great"))
        self.assertEqual(swaps[1], ("Mid", "Good"))
        self.assertEqual(dict((n, k) for n, k in deck["main"])["Swamp"], 17, "the land count changed")
        self.assertEqual(sum(k for _n, k in deck["main"]), 20, "the deck's size changed")

    def test_off_colour_cards_never_come_in_and_colourless_ones_do(self):
        deck, swaps = self.rebuild([["Swamp", 17], ["Weak", 1]], {"Swamp": 17, "Weak": 1, "RedBomb": 4, "Rock": 1})
        self.assertEqual(swaps, [("Weak", "Rock")])
        self.assertNotIn("RedBomb", dict((n, k) for n, k in deck["main"]))

    def test_no_more_copies_than_owned_and_never_five(self):
        deck, _ = self.rebuild([["Swamp", 17], ["Weak", 3], ["Mid", 3]], {"Swamp": 17, "Weak": 3, "Mid": 3, "Great": 6})
        self.assertEqual(dict((n, k) for n, k in deck["main"])["Great"], 4)
        deck, _ = self.rebuild([["Swamp", 17], ["Weak", 3]], {"Swamp": 17, "Weak": 3, "Great": 2})
        self.assertEqual(dict((n, k) for n, k in deck["main"])["Great"], 2)

    def test_nothing_better_owned_means_no_swap(self):
        deck, swaps = self.rebuild([["Swamp", 17], ["Great", 1]], {"Swamp": 17, "Great": 1, "Weak": 4})
        self.assertEqual(swaps, [])
        self.assertEqual(deck["main"], [["Swamp", 17], ["Great", 1]])

    def test_a_rebuild_that_breaks_the_rpg_rules_is_undone(self):
        pool, tt = fake_world()
        deck = {"main": [["Swamp", 17], ["Weak", 1]]}
        with mock.patch("rpg.collection.problems", return_value=["something"]):
            swaps = rpg_soak.rebuild_deck(deck, Coll({"Swamp": 17, "Weak": 1, "Great": 1}), pool, tt, Lists())
        self.assertEqual(swaps, [])
        self.assertEqual(deck["main"], [["Swamp", 17], ["Weak", 1]])

    def test_deck_power_is_the_mean_tier_of_the_non_land_cards(self):
        pool, tt = fake_world()
        self.assertEqual(rpg_soak.deck_power([["Swamp", 17], ["Weak", 2], ["Great", 1], ["Good", 1]], pool, tt), 1.75)
        self.assertIsNone(rpg_soak.deck_power([["Swamp", 17]], pool, tt))


def duel(**k):
    d = {"t": "duel", "settings": "built", "player": "rusher", "kind": "ember_bandit", "enc": "regular", "result": "won",
         "engine": "forge", "turns": 12, "gold_before": 100, "gold_change": 30, "power": 1.6}
    d.update(k)
    return d


def run(**k):
    d = {"t": "run", "settings": "built", "player": "rusher", "done": True, "duels_n": 3, "wins": 2, "rebuilds": 1,
         "bought": [], "boss_at": 3, "boss_minutes": 39, "gold_end": 200, "gold_won": 150, "gold_lost": 20, "gold_spent": 0,
         "power_end": 1.7, "power_gain": 0.1, "tier1": 20, "tier2": 5, "tier3": 1, "tier4": 0, "tier5": 0}
    d.update(k)
    return d


class SummaryTests(unittest.TestCase):
    def good(self):
        return [duel(), duel(result="lost", gold_change=-20), duel(kind="grave_priest", enc="boss", gold_change=200),
                run(), run(player="shopper", bought=["X"]),
                duel(player="shopper"), duel(player="shopper", result="lost", gold_change=-10)]

    def test_a_good_run_is_valid_and_summed_per_player_and_settings(self):
        s = rpg_soak.summarize(self.good())
        self.assertEqual(s["verdict"], ("VALID", []))
        g = s["groups"]["built|rusher"]
        self.assertEqual((g["runs"], g["duels"]), (1, 3))
        self.assertEqual(g["win_rate"]["ember_bandit/regular/1.5-2.0"]["won"], 1)
        self.assertEqual(g["win_rate"]["ember_bandit/regular/1.5-2.0"]["of"], 2)
        self.assertIn("VERDICT: VALID", rpg_soak.summary_text(s))

    def test_planted_duels_decided_by_a_coin_are_invalid(self):
        recs = [r if r["t"] != "duel" else dict(r, engine="stand-in", turns=0) for r in self.good()]
        v, why = rpg_soak.summarize(recs)["verdict"]
        self.assertEqual(v, "INVALID")
        self.assertIn("real Forge games", why[0])

    def test_planted_deck_never_rebuilt_is_invalid(self):
        recs = [r if r["t"] != "run" else dict(r, rebuilds=0) for r in self.good()]
        self.assertIn("no deck was ever rebuilt", rpg_soak.summarize(recs)["verdict"][1])

    def test_planted_loss_that_takes_no_gold_is_invalid(self):
        recs = self.good() + [duel(result="lost", gold_before=80, gold_change=0)]
        v, why = rpg_soak.summarize(recs)["verdict"]
        self.assertEqual(v, "INVALID")
        self.assertTrue(any("took no gold" in w for w in why), why)

    def test_a_loss_the_games_rounding_makes_free_is_not_flagged(self):
        # found by the sandbox run: option A's 10% of 6-9 gold is int(0.6-0.9) = 0 - the game's own rounding, not a fault
        recs = self.good() + [duel(result="lost", gold_before=8, gold_change=0, loss_share=0.1)]
        self.assertEqual(rpg_soak.summarize(recs)["verdict"], ("VALID", []))
        recs = self.good() + [duel(result="lost", gold_before=10, gold_change=0, loss_share=0.1)]
        self.assertEqual(rpg_soak.summarize(recs)["verdict"][0], "INVALID")

    def test_the_no_rebuild_control_is_not_flagged_for_never_rebuilding(self):
        recs = [r if r["t"] != "run" else dict(r, rebuilds=0, rebuild_policy="none") for r in self.good()]
        self.assertEqual(rpg_soak.summarize(recs)["verdict"], ("VALID", []))

    def test_a_shopper_that_never_buys_is_invalid(self):
        recs = [r if not (r["t"] == "run" and r["player"] == "shopper") else dict(r, bought=[]) for r in self.good()]
        self.assertIn("no shopper ever bought a card", rpg_soak.summarize(recs)["verdict"][1])

    def test_compare_puts_the_bots_rates_next_to_your_own_duels_and_says_how_few(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "games.ndjson"), "w", encoding="utf-8") as f:
            for i, res in enumerate(("won", "lost")):
                f.write(json.dumps({"t": "start", "id": f"g{i}", "opponents": [{"deck": {"id": "rpg-enemy:ember_bandit"}}]}) + "\n")
                f.write(json.dumps({"t": "end", "id": f"g{i}", "result": res}) + "\n")
        txt = rpg_soak.compare_text(rpg_soak.summarize(self.good()), tmp)
        self.assertIn("2 decided game(s)", txt)
        self.assertIn("far too few", txt)
        self.assertIn("ember_bandit: bot 2/4 = 50%; you 1/2 = 50%", txt)

    def test_percentiles_and_bands(self):
        self.assertEqual(rpg_soak.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9), 9)
        self.assertEqual(rpg_soak.pct([3, 1, 2], 0.5), 2)
        self.assertEqual(rpg_soak.band_of(1.49), "<1.5")
        self.assertEqual(rpg_soak.band_of(2.0), "2.0-2.5")


class FolderTests(unittest.TestCase):
    def test_the_output_folder_is_git_ignored_and_never_backed_up(self):
        import backup
        self.assertEqual(os.path.basename(rpg_soak.OUT_DIR), "rpg_soak_runs")
        self.assertIn("rpg_soak_runs", backup.TOP_LEVEL_SKIP)
        with open(os.path.join(BASE, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("rpg_soak_runs/", [ln.strip() for ln in f])

    def test_a_missing_progression_file_is_accepted_and_ignored(self):
        self.assertIn("missing - ignored", rpg_soak.load_progression(os.path.join(BASE, "no", "progression.json")))
        self.assertEqual(rpg_soak.load_progression(None), "progression: none")

    def test_old_runs_keep_only_their_summaries(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for i in range(4):
            d = os.path.join(tmp, f"run_2026110{i}_000000")
            os.makedirs(os.path.join(d, "records"))
            os.makedirs(os.path.join(d, "java_0"))
            open(os.path.join(d, "summary.txt"), "w").close()
        rpg_soak.prune(tmp, keep=2)
        left = sorted((os.path.basename(r), sorted(os.listdir(r))) for r in [os.path.join(tmp, x) for x in os.listdir(tmp)])
        self.assertEqual([x[1] for x in left], [["summary.txt"], ["summary.txt"], ["java_0", "records", "summary.txt"],
                                                 ["java_0", "records", "summary.txt"]])


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/")
class GameRunTests(unittest.TestCase):
    """Whole runs of the real Adventure (headless), with a stand-in for Duels.java."""

    def go(self, player, decide=None, cap=8):
        Bot = rpg_soak.make_bot_class()
        recs = []
        bot = Bot()
        bot.cap = cap
        bot.setUp()
        try:
            r = bot.run_one(5, player, "built", f"t_{player}", rpg_soak.StandInDuels(decide=decide, turns=9, seed=3),
                            recs.append)
        finally:
            bot.tearDown()
            from rpg import explore
            explore.forget_areas()
        return r, recs

    def test_a_run_settles_every_duel_through_the_game_and_rebuilds_the_deck(self):
        r, recs = self.go("gambler", decide=lambda m, t, rng: "A")
        duels = [d for d in recs if d["t"] == "duel"]
        self.assertIsNone(r["error"], r["problems"])
        self.assertTrue(duels)
        self.assertTrue(all(d["result"] == "won" and d["gold_change"] > 0 for d in duels))
        self.assertGreater(r["rebuilds"], 0)
        self.assertGreater(r["packs"], 0, "the gambler bought no pack")
        v, why = rpg_soak.verdict([r], duels, expect_forge=False)
        self.assertEqual(v, "VALID", why)

    def test_rebuild_none_never_touches_the_deck_even_when_shopping(self):
        # found by the first control run: the gambler's and shopper's shopping rebuilt the deck past --rebuild none
        Bot = rpg_soak.make_bot_class()
        recs = []
        bot = Bot()
        bot.cap, bot.rebuild_policy = 8, "none"
        bot.setUp()
        try:
            r = bot.run_one(5, "gambler", "built", "t_none", rpg_soak.StandInDuels(decide=lambda m, t, rng: "A", turns=9),
                            recs.append)
        finally:
            bot.tearDown()
            from rpg import explore
            explore.forget_areas()
        self.assertGreater(r["packs"], 0)
        self.assertEqual((r["rebuilds"], r["power_end"]), (0, r["power_start"]))

    def test_planted_coin_is_caught_when_forge_was_expected(self):
        r, recs = self.go("rusher", cap=4)
        duels = [d for d in recs if d["t"] == "duel"]
        for d in duels:
            d["turns"] = 0                              # a coin has no turns
        self.assertEqual(rpg_soak.verdict([r], duels, expect_forge=True)[0], "INVALID")

    def test_planted_deck_never_rebuilt_is_caught(self):
        with mock.patch.object(rpg_soak, "rebuild_deck", return_value=[]):
            r, recs = self.go("rusher", decide=lambda m, t, rng: "A", cap=4)
        v, why = rpg_soak.verdict([r], [d for d in recs if d["t"] == "duel"], expect_forge=False)
        self.assertIn("no deck was ever rebuilt", why)

    def test_planted_loss_that_takes_no_gold_is_caught(self):
        from rpg import economy
        with mock.patch.object(economy.Numbers, "loss_share", lambda self, kind="regular": 0):   # GOLD1: the share by kind of fight
            r, recs = self.go("rusher", decide=lambda m, t, rng: "B", cap=3)
        duels = [d for d in recs if d["t"] == "duel"]
        self.assertTrue(duels)
        v, why = rpg_soak.verdict([r], duels, expect_forge=False)
        self.assertTrue(any("took no gold" in w for w in why), why)

    def test_a_loss_takes_the_games_share_of_gold(self):
        r, recs = self.go("rusher", decide=lambda m, t, rng: "B", cap=3)
        d = [x for x in recs if x["t"] == "duel"][0]
        self.assertEqual(d["result"], "lost")
        from rpg import economy                          # GOLD1 (rc15b): the share is the kind of fight's, from numbers.json
        share = economy.read_numbers().loss_shares[d["enc"] or "regular"]
        self.assertEqual(d["loss_share"], share)
        self.assertEqual(-d["gold_change"], economy.cost_of(d["gold_before"], share))

    def test_a_numbers_file_reaches_the_games_own_settling(self):
        rpg_soak.importlib_reset_numbers(os.path.join(BASE, "tools", "rpg_soak_settings", "option_a.json"))
        try:
            r, recs = self.go("rusher", decide=lambda m, t, rng: "B", cap=2)
        finally:
            rpg_soak.importlib_reset_numbers(None)
        d = [x for x in recs if x["t"] == "duel"][0]
        self.assertEqual(-d["gold_change"], int(d["gold_before"] * 0.1), "option A's loss share (0.1) didn't apply")


@unittest.skipUnless(LIVE and HAS_RUNTIME and os.path.exists(os.path.join(BASE, "java_rpg", "rpg_tools.jar")),
                     "needs Forge and Java (live tests)")
class LiveTests(unittest.TestCase):
    def test_three_real_forge_duels_reach_the_settle_step_and_change_gold(self):
        tmp = tempfile.mkdtemp(prefix="rpgsoak_")
        self.addCleanup(shutil.rmtree, tmp, True)
        Bot = rpg_soak.make_bot_class()
        eng = rpg_soak.ForgeDuels(tmp)
        recs = []
        bot = Bot()
        bot.cap = 3
        bot.setUp()
        try:
            r = bot.run_one(2, "rusher", "built", "live", eng, recs.append)
        finally:
            eng.close()
            bot.tearDown()
            from rpg import explore
            explore.forget_areas()
        duels = [d for d in recs if d["t"] == "duel"]
        self.assertEqual(len(duels), 3, r.get("problems"))
        self.assertTrue(all(d["engine"] == "forge" and d["turns"] > 0 and d["forge_result"] in ("A", "B") for d in duels))
        self.assertTrue(all(d["gold_change"] != 0 for d in duels), duels)
        why = rpg_soak.verdict([r], duels)[1]
        self.assertFalse([w for w in why if "Forge" in w or "took no gold" in w or "paid no gold" in w], why)


if __name__ == "__main__":
    unittest.main()
