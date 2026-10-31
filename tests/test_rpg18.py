# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG18 (25 Oct 2026): the Adventure slice in about 45 minutes - Plan 2 "Short road, fair boss" with the rewards add-on
(Karl, 24 Oct; claude/DECISIONS_2026-10-24.md "The 45-minute slice", the edits of claude/SLICE_45MIN_PLANS_2026-10-24.md
section 6). Each test fails on RPG17b's data:

  1  The Lamplighter's Debt needs one Ember Bandit, "their leader by the bridge"; no line of Pell's quest or of the gate guard's /
     night watch's words about it still counts three;
  2  the outskirts' Ember Bandits leave the road once Pell's quest is ready or done (marker E's `when`);
  3  a one-way stair up from the Grave Priest's hall into Lantern Hollow (the chapel crypt);
  4  the Warrens' encounter_rate is 200 (source and built map);
  5  the Grave Priest's four decks each have 5 spells swapped for basic lands (rpg.enemies.handicap), and the measurement of
     the decks that ship is recorded with them;
  6  the boss gives 5 cards, The Dark Hall 100 gold.
"""
import hashlib
import json
import os
import re
import unittest

from rpg import cardpool, conditions, enemies, explore, talk

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "rpg", "data")


def load(*parts):
    with open(os.path.join(DATA, *parts), encoding="utf-8") as fh:
        return json.load(fh)


def gp_digest(decks):
    """SHA-256 over the Grave Priest's decks as they ship (name, main, swapped) - recorded with the boss's measurement."""
    return hashlib.sha256(json.dumps([[d["name"], d["main"], d.get("swapped")] for d in decks], sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


THREE = re.compile(r"\bthree\b", re.I)


class PellTests(unittest.TestCase):
    def test_one_bandit_and_no_three_left_in_the_quest(self):
        q = load("quests.json")["quests"]["lamplighters_debt"]
        self.assertEqual(q["goal"], {"beat": "ember_bandit", "spot": "ashfen_outskirts#6", "count": 1,   # RPG18b: the leader only
                                     "others_flag": "road_bandits_fled"})   # RPG18c (expected change): the others flee
        words = [p for k in ("offer", "accepted", "progress", "ready", "done") for p in q[k]] + [q["journal"]]
        self.assertEqual([p for p in words if THREE.search(p)], [])
        self.assertTrue(any("leader by the bridge" in p for p in q["offer"]), q["offer"])

    def test_the_guard_and_the_watch_no_longer_count_three(self):
        people = load("npcs.json")["npcs"]
        lines = [p for nid in ("gate_guard", "night_watch", "lamplighter") for e in people[nid]["lines"]
                 if any("lamplighters_debt" in c for c in e.get("when") or []) for p in e["pages"]]
        self.assertTrue(lines)
        self.assertEqual([p for p in lines if THREE.search(p)], [])

    def test_one_win_makes_it_ready(self):
        w = {}
        talk.Conversation("lamplighter", w).answer(True)
        talk.on_duel_won(w, "ember_bandit", "regular", spot="ashfen_outskirts#6")   # RPG18b (expected change): the bridge one
        self.assertEqual(talk.quest_state(w, "lamplighters_debt"), "ready")


class MapTests(unittest.TestCase):
    def setUp(self):
        explore.forget_areas()

    def test_the_bandits_leave_once_the_quest_is_ready_or_done(self):
        a = explore.load_area("ashfen_outskirts")
        bandits = [t for t in a.triggers if t.kind == "encounter" and t.enemy == "ember_bandit" and not t.rare]   # not the Red Sash
        self.assertGreaterEqual(len(bandits), 1)
        for state, out in (("none", True), ("active", True), ("ready", False), ("done", False)):
            w = {"quests": {"lamplighters_debt": {"state": state, "count": 0}}} if state != "none" else {}
            for t in bandits:
                with self.subTest(state=state, spot=t.spot):
                    self.assertEqual(conditions.all_hold(t.when, w, minutes=12 * 60), out)

    def test_a_one_way_stair_from_the_boss_hall_to_lantern_hollow(self):
        w2 = explore.load_area("warrens_2")
        up = [t for t in w2.triggers if t.kind == "exit" and t.to_map == "lantern_hollow"]
        self.assertEqual(len(up), 1)
        self.assertIn(up[0].to_spawn, explore.load_area("lantern_hollow").spawns)
        boss = next(t for t in w2.triggers if t.kind == "encounter" and t.encounter == "boss")
        (uc, ur), (bc, br) = [w2.tile_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2) for t in (up[0], boss)]
        self.assertLessEqual(abs(uc - bc) + abs(ur - br), 4, "the stair isn't in the Grave Priest's hall")
        hollow = explore.load_area("lantern_hollow")
        self.assertEqual([t for t in hollow.triggers if t.kind == "exit" and t.to_map == "warrens_2"], [], "the stair is one way")

    def test_the_warrens_rate_is_200(self):
        for name in ("warrens_1", "warrens_2"):
            with self.subTest(map=name):
                self.assertEqual(explore.load_area(name).fight_rate, 200)
                with open(os.path.join(BASE, "rpg", "maps", "src", name + ".txt"), encoding="utf-8") as fh:
                    self.assertIn("encounter_rate: 200\n", fh.read())


class BossTests(unittest.TestCase):
    def test_each_deck_gave_five_spells_to_basic_lands(self):
        pool = cardpool.load()
        gp = load("enemies", "grave_priest.json")
        self.assertEqual(len(gp["decks"]), 4)
        for d in gp["decks"]:
            with self.subTest(deck=d["name"]):
                self.assertEqual(d["handicap"], 5)
                self.assertEqual(sum(c for _n, c in d["swapped"]), 5)
                self.assertEqual(sum(c for _n, c in d["main"]), 60)
                self.assertTrue(all(not pool.get(n).is_land for n, _c in d["swapped"]))
                # the handicap's own pick: putting the swapped spells back and the basics out gives the same deck again
                main = dict((n, c) for n, c in d["main"])
                for n, c in d["swapped"]:
                    main[n] = main.get(n, 0) + c
                basics = [n for n in main if pool.get(n) is not None and pool.get(n).is_basic]
                again = None
                for take in _basic_splits(main, basics, 5):
                    whole = [(n, c - take.get(n, 0)) for n, c in main.items() if c - take.get(n, 0) > 0]
                    deck, sw = enemies.handicap(whole, 5, pool)
                    if sorted(map(tuple, deck)) == sorted(map(tuple, d["main"])) and sorted(sw) == sorted(map(tuple, d["swapped"])):
                        again = take
                        break
                self.assertIsNotNone(again, "the deck isn't rpg.enemies.handicap(original, 5)")

    def test_the_measurement_is_of_the_decks_that_ship(self):
        gp = load("enemies", "grave_priest.json")
        m = gp["balance"]
        self.assertEqual(m["handicap"], 5)
        self.assertEqual(m["decks_sha"], gp_digest(gp["decks"]))
        self.assertTrue(0.45 <= m["measured"]["share"] <= 0.55, m["measured"])


def _basic_splits(main, basics, k):
    """Every way to take k basic lands out of `main` (the handicap added them)."""
    def rec(i, left):
        if i == len(basics):
            if left == 0:
                yield {}
            return
        n = basics[i]
        for c in range(min(left, main[n]) + 1):
            for rest in rec(i + 1, left - c):
                yield dict(rest, **({n: c} if c else {}))
    yield from rec(0, k)


class RewardTests(unittest.TestCase):
    def test_the_boss_gives_five_cards_and_the_dark_hall_100_gold(self):
        self.assertEqual(load("numbers.json")["rewards"]["boss"]["cards"], 5)
        self.assertEqual(load("quests.json")["quests"]["the_dark_hall"]["reward"]["gold"], 100)


if __name__ == "__main__":
    unittest.main()
