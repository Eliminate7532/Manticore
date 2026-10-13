# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG3 (RPG plan, gate 2 and phase 3 content): two optional tier caps Karl can switch on in tier_overrides.json (lands,
cards once printed at common or uncommon) with a side-by-side comparison, and five more kinds of enemy (ten, the plan's "about 10"),
built without changing the first five.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from rpg import cardpool, enemies, rules, tiers

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("FORGE_RUNTIME") or os.path.join(BASE, "forge_runtime")
HAS_RUNTIME = os.path.isfile(os.path.join(RUNTIME, rules.VINTAGE_FILE))


def pool():
    return cardpool.CardPool([
        ["Island", "Basic Land - Island", 0, "C", "L", 0],
        ["Polluted Delta", "Land", 0, "C", "R", 0],
        ["Ancient Tomb", "Land", 0, "C", "U", 0],
        ["Library of Alexandria", "Land", 0, "C", "U", 0],
        ["Counterspell", "Instant", 2, "U", "C", 0],
        ["Force of Will", "Instant", 5, "U", "U", 0],
        ["Jace, the Mind Sculptor", "Legendary Planeswalker - Jace", 4, "U", "M", 0],
        ["Brainstorm", "Instant", 1, "U", "C", 0],
        ["Grizzly Bears", "Creature - Bear", 2, "G", "C", 0],
    ], "Forge test")


def lists():
    return rules.Lists(banned=[], restricted=["Brainstorm", "Library of Alexandria"], source="made up")


DATA = {"cards": {"Polluted Delta": [5, 15.0, {}], "Ancient Tomb": [5, 4.6, {}], "Counterspell": [5, 5.2, {}],
                  "Force of Will": [5, 19.3, {}], "Jace, the Mind Sculptor": [5, 5.3, {}], "Brainstorm": [5, 12.5, {}]}}


def table(caps=None, overrides=None):
    return tiers.Tiers(DATA, overrides or {}, pool(), lists(), caps)


class CapTests(unittest.TestCase):
    def test_no_caps_by_default(self):
        t = table()
        for name in ("Polluted Delta", "Ancient Tomb", "Counterspell", "Force of Will", "Jace, the Mind Sculptor"):
            self.assertEqual(t.tier_of(name), 5, name)
            self.assertNotIn("capped", t.info(name))

    def test_lands_max_caps_non_basic_lands_only(self):
        t = table({"lands_max": 3})
        self.assertEqual(t.info("Polluted Delta"), {"tier": 3, "source": "data", "score": 15.0, "formats": {}, "capped": "lands_max"})
        self.assertEqual(t.tier_of("Island"), 0)
        self.assertEqual(t.tier_of("Counterspell"), 5)
        self.assertEqual(t.tier_of("Library of Alexandria"), 5)          # restricted: not capped

    def test_common_max_caps_cards_ever_printed_at_common_or_uncommon(self):
        t = table({"common_max": 4})
        self.assertEqual(t.tier_of("Counterspell"), 4)
        self.assertEqual(t.tier_of("Force of Will"), 4)                   # first printed uncommon (Alliances)
        self.assertEqual(t.tier_of("Jace, the Mind Sculptor"), 5)        # mythic only
        self.assertEqual(t.tier_of("Polluted Delta"), 5)                 # rare: not capped
        self.assertEqual(t.tier_of("Brainstorm"), 5)                     # restricted: not capped
        self.assertEqual(t.tier_of("Grizzly Bears"), 1)                  # below the cap already

    def test_both_and_an_override_wins(self):
        t = table({"lands_max": 3, "common_max": 4}, overrides={"Ancient Tomb": 5, "Counterspell": 2})
        self.assertEqual(t.tier_of("Ancient Tomb"), 5)
        self.assertEqual(t.tier_of("Counterspell"), 2)
        self.assertEqual(t.tier_of("Polluted Delta"), 3)

    def test_the_rules_in_the_override_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "o.json")
        self.assertEqual(tiers.read_caps(path), tiers.NO_CAPS)            # no file
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"cards": {}}, f)
        self.assertEqual(tiers.read_caps(path), tiers.NO_CAPS)            # no "rules"
        for bad in ({"lands_max": 0}, {"lands_max": 6}, {"common_max": "4"}, {"common_max": True}, {"commons": 4}):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"rules": bad, "cards": {}}, f)
            with self.subTest(rules=bad), self.assertRaises(ValueError):
                tiers.read_caps(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"rules": {"lands_max": 3, "common_max": None}, "cards": {}}, f)
        self.assertEqual(tiers.read_caps(path), {"lands_max": 3, "common_max": None})

    def test_load_takes_the_caps_from_the_override_file(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        data, over = os.path.join(tmp, "tiers.json"), os.path.join(tmp, "o.json")
        with open(data, "w", encoding="utf-8") as f:
            json.dump(DATA, f)
        with open(over, "w", encoding="utf-8") as f:
            json.dump({"rules": {"lands_max": 3, "common_max": None}, "cards": {"Counterspell": 2}}, f)
        t = tiers.load(data, over, pool(), lists())
        self.assertEqual((t.tier_of("Polluted Delta"), t.tier_of("Counterspell"), t.tier_of("Force of Will")), (3, 2, 5))

    def test_the_shipped_override_file_has_both_rules_off(self):
        with open(tiers.OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["rules"], {"lands_max": None, "common_max": None})
        self.assertEqual(tiers.read_caps(), tiers.NO_CAPS)


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/ (the restricted list)")
class ShippedCapTests(unittest.TestCase):
    def test_caps_off_change_nothing_and_each_option_only_lowers(self):
        pool_ = cardpool.load()
        lists_ = rules.vintage_lists(RUNTIME)
        with open(tiers.TIERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        plain = tiers.Tiers(data, {}, pool_, lists_)
        names = [c.name for c in pool_.cards.values()]
        base = {n: plain.tier_of(n) for n in names}
        off = tiers.Tiers(data, {}, pool_, lists_, {"lands_max": None, "common_max": None})
        self.assertEqual({n: off.tier_of(n) for n in names}, base)
        for caps in ({"lands_max": 3}, {"common_max": 4}, {"lands_max": 3, "common_max": 4}):
            t = tiers.Tiers(data, {}, pool_, lists_, caps)
            moved = {n: (base[n], t.tier_of(n)) for n in names if t.tier_of(n) != base[n]}
            with self.subTest(caps=caps):
                self.assertTrue(moved)
                self.assertTrue(all(b < a for a, b in moved.values()))
                self.assertTrue(all(not lists_.is_restricted(n) for n in moved))
        self.assertEqual(tiers.Tiers(data, {}, pool_, lists_, {"lands_max": 3}).tier_of("Polluted Delta"), 3)
        self.assertEqual(tiers.Tiers(data, {}, pool_, lists_, {"common_max": 4}).tier_of("Llanowar Elves"), 4)

    def test_the_comparison(self):
        from tools import rpg_build_data as b
        text = b.compare(10)
        self.assertIn("CARDS PER TIER", text)
        self.assertIn("TOP 10 BY SCORE", text)
        for label, _caps in b.CAP_OPTIONS[1:]:
            self.assertIn(f"WHAT '{label}' MOVES", text)


# ---- ten kinds of enemy ------------------------------------------------------------------------------------------------------

FIRST_FIVE = ("grave_robber", "ember_bandit", "thornback", "drowned_cultist", "zealot")
NEW_FIVE = ("wild_elf", "hive_spawn", "clockwork_golem", "grave_priest", "plague_rat")


class KindTests(unittest.TestCase):
    def test_ten_kinds_the_first_five_unchanged_in_order(self):
        kinds = enemies.read_kinds()
        self.assertEqual(tuple(k["id"] for k in kinds), FIRST_FIVE + NEW_FIVE)
        for k in kinds:
            with self.subTest(kind=k["id"]):
                self.assertEqual(k["format"], "Pauper")
                self.assertEqual(k["decks"], 4)
                self.assertIn(k["max_tier"], (4, 5))
                if k["max_tier"] == 5:
                    self.assertIn("note", k)                 # a raised cap says why

    def test_the_tool_builds_only_the_kinds_named(self):
        from tools import rpg_build_data as b
        calls = []

        def fake_java(command, *args, rt=None):
            calls.append(args)
            return {"decks": []}

        with mock.patch.object(b, "java_json", fake_java), mock.patch.object(enemies, "write_kind", lambda *a, **k: None), \
                mock.patch.object(b, "forge_version", lambda rt=None: "Forge X"), mock.patch("builtins.print"):
            b.build_enemies(only=["hive_spawn"])
        self.assertEqual(len(calls), 1)
        seed, fmt, tries, *names = calls[0]
        self.assertEqual((fmt, tries, names), ("Pauper", 16, ["Slivers Sliver Fractius"]))
        with self.assertRaises(SystemExit):
            b.build_enemies(only=["no_such_kind"])

    def test_the_first_five_files_are_the_ones_rpg2_card_checked_and_soaked(self):
        # their decks must be exactly RPG2's: same seed, same archetypes, same lists (the RPG2 soak entry names its run)
        for kind in FIRST_FIVE:
            data = enemies.read_kind(kind)
            with self.subTest(kind=kind):
                self.assertEqual(data["seed"], 20261012)
                self.assertEqual(len(data["decks"]), 4)


@unittest.skipUnless(HAS_RUNTIME, "needs forge_runtime/")
class ShippedNewKindTests(unittest.TestCase):
    def test_the_new_kinds_ship_four_decks_each_and_every_deck_passes(self):
        pool_, lists_, t = cardpool.load(), rules.vintage_lists(RUNTIME), tiers.load()
        for k in enemies.read_kinds():
            if k["id"] not in NEW_FIVE:
                continue
            data = enemies.read_kind(k["id"])
            self.assertEqual(len(data["decks"]), 4, k["id"])
            for d in data["decks"]:
                with self.subTest(deck=d["name"]):
                    entry = dict(d, deckName=d["archetype"] + enemies.FALLBACK_SUFFIX)
                    self.assertEqual(enemies.judge(entry, k, pool_, lists_, t, lambda n: enemies.ai_flags(n, RUNTIME)), [])


if __name__ == "__main__":
    unittest.main()
