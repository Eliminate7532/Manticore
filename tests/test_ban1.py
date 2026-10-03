# SPDX-License-Identifier: GPL-3.0-or-later
"""Round BAN1: format legality on the deck screen (legality.py). A not-legal deck is labelled and still plays (Karl, 1 Oct).

Card data: tests/fixtures/legality/cards.json - real Scryfall objects (trimmed to the fields legality reads), fetched 2 Oct 2026.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import backup
import deck_library as lib
import legality as lg

with open(os.path.join(BASE, "tests", "fixtures", "legality", "cards.json"), encoding="utf-8") as _f:
    CARDS = json.load(_f)
BANNED = ["Mana Crypt", "Chaos Orb", "Jeweled Lotus", "Dockside Extortionist", "Primeval Titan"]


class Store:
    """peek_card / prefetch_cards over the fixture; `known` limits what is 'cached' so far."""

    def __init__(self, known=None):
        self.known = set(CARDS) if known is None else {k.lower() for k in known}
        self.fetched = []

    def peek_card(self, name):
        k = name.strip().lower()
        return CARDS.get(k) if k in self.known else None

    def prefetch_cards(self, names):
        self.fetched.append(sorted(names))
        self.known |= {n.lower() for n in names if n.lower() in CARDS}
        return len(names)


def deck(commanders, *cards, fill="Forest"):
    """A 100-card list: the given cards, then basics."""
    body = list(cards)
    body += [fill] * (100 - len(commanders) - len(body))
    return list(commanders), body


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ban1_")
        self._env = mock.patch.dict(os.environ, {"MANTICORE_USER_DIR": self.tmp})
        self._env.start()
        self._saved = dict(lg._BANNED)
        lg._BANNED["commander"] = lg.BanList(lg._names(BANNED), "2026-10-02T10:00:00+00:00", "scryfall")
        self.store = Store()

    def tearDown(self):
        lg._BANNED.clear()
        lg._BANNED.update(self._saved)
        self._env.stop()
        shutil.rmtree(self.tmp, True)

    def check(self, commanders, cards, store=None):
        return lg.check(commanders, cards, store or self.store)

    def kinds(self, v):
        return [k for k, _t, _c in v.issues]


class CardRuleTests(Case):
    def test_banned_card(self):
        v = self.check(*deck(["Kinnan, Bonder Prodigy"], "Mana Crypt"))
        self.assertEqual((v.status, self.kinds(v)), ("not_legal", ["banned"]))
        self.assertEqual(v.issues[0][2], ["Mana Crypt"])

    def test_silver_border_is_not_legal_not_banned(self):
        v = self.check(*deck(["Kinnan, Bonder Prodigy"], "Mox Lotus", "Chaos Orb"))
        by = {k: c for k, _t, c in v.issues}
        self.assertEqual(by["not_legal"], ["Mox Lotus"])
        self.assertEqual(by["banned"], ["Chaos Orb"])           # banned in Commander, not "not legal" (checked 2 Oct)

    def test_case_and_faces(self):
        lg._BANNED["commander"] = lg.BanList(lg._names(["Delver of Secrets // Insectile Aberration"]), None, "scryfall")
        for name in ("delver of secrets", "Insectile Aberration", "Delver of Secrets // Insectile Aberration"):
            with self.subTest(name=name):
                v = self.check(*deck(["Kinnan, Bonder Prodigy"], name), store=Store([]))
                self.assertIn("banned", self.kinds(v))

    def test_the_commander_slot_counts(self):
        lg._BANNED["commander"] = lg.BanList(lg._names(["Kinnan, Bonder Prodigy"]), None, "scryfall")
        self.assertIn("banned", self.kinds(self.check(*deck(["Kinnan, Bonder Prodigy"]))))


class DeckRuleTests(Case):
    def test_size(self):
        c, d = deck(["Kinnan, Bonder Prodigy"])
        self.assertEqual(self.check(c, d).status, "legal")
        self.assertEqual(self.kinds(self.check(c, d[:-1])), ["size"])
        self.assertEqual(self.kinds(self.check(c, d + ["Forest"])), ["size"])

    def test_singleton_and_its_exceptions(self):
        self.assertEqual(self.kinds(self.check(*deck(["Kinnan, Bonder Prodigy"], "Sol Ring", "Sol Ring"))), ["singleton"])
        basics = ["Snow-Covered Island"] * 10 + ["Wastes"] * 5
        self.assertEqual(self.check(*deck(["Kinnan, Bonder Prodigy"], *basics)).issues, [])
        rats = deck(["Tymna the Weaver", "Thrasios, Triton Hero"], *(["Relentless Rats"] * 40))
        self.assertNotIn("singleton", self.kinds(self.check(*rats)))
        self.assertIn("singleton", self.kinds(self.check(*deck(["Esika, God of the Tree"], *(["Seven Dwarves"] * 8)))))
        self.assertNotIn("singleton", self.kinds(self.check(*deck(["Esika, God of the Tree"], *(["Seven Dwarves"] * 7)))))

    def test_basics_pass_by_name_without_card_data(self):
        v = self.check(*deck(["Kinnan, Bonder Prodigy"], *(["Island"] * 30)), store=Store([]))
        self.assertNotIn("singleton", self.kinds(v))

    def test_colour_identity(self):
        v = self.check(*deck(["Kinnan, Bonder Prodigy"], "Lightning Bolt", "Sol Ring"))
        self.assertEqual(self.kinds(v), ["identity"])
        self.assertEqual(v.issues[0][2], ["Lightning Bolt"])
        self.assertIn("Kinnan's colours (U/G)", lg.problem_lines(v)[0][0])
        hybrid = self.check(*deck(["Kinnan, Bonder Prodigy"], "Kitchen Finks"))         # G/W hybrid: Scryfall says G, W
        self.assertEqual(self.kinds(hybrid), ["identity"])

    def test_identity_unchecked_without_commander_data(self):
        known = set(CARDS) - {"kinnan, bonder prodigy"}
        v = self.check(*deck(["Kinnan, Bonder Prodigy"], "Lightning Bolt"), store=Store(known))
        self.assertEqual(v.status, "unchecked")
        self.assertIn(("colour identity", None), v.unchecked)

    def test_commander_eligibility(self):
        bad = self.check(*deck(["Llanowar Elves"]))
        self.assertEqual(self.kinds(bad)[0], "commander")
        line = [t for t, _b in lg.problem_lines(bad) if t.startswith("Can't be a commander")][0]
        self.assertIn("change it before starting", line)                       # Karl, 2 Oct: only legal commanders
        self.assertTrue(lg.blocks_start(bad))
        for ok in ("Teferi, Temporal Archmage", "Hearthhull, the Worldseed", "Kinnan, Bonder Prodigy"):
            with self.subTest(ok=ok):
                self.assertNotIn("commander", self.kinds(self.check(*deck([ok]))))
        self.assertIn("commander", self.kinds(self.check(*deck(["Grist, the Hunger Tide"]))))      # a planeswalker without the text
        self.assertIn("commander", self.kinds(self.check(*deck(["Sothera, the Supervoid"]))))

    def test_pairs(self):
        cases = {
            ("Thrasios, Triton Hero", "Tymna the Weaver"): None,
            ("Kinnan, Bonder Prodigy", "Thrasios, Triton Hero"): "pair",
            ("Pir, Imaginative Rascal", "Toothy, Imaginary Friend"): None,
            ("Pir, Imaginative Rascal", "Shabraz, the Skyshark"): "pair",
            ("Wilson, Refined Grizzly", "Raised by Giants"): None,
            ("The Tenth Doctor", "Rose Tyler"): None,
            ("Bjorna, Nightfall Alchemist", "Cecily, Haunted Mage"): None,          # partner-Friends forever
            ("Abby, Merciless Soldier", "Ellie, Vengeful Hunter"): None,           # partner-Survivors
            ("Kratos, Stoic Father", "Atreus, Impulsive Son"): None,               # partner-Father & son
            ("Donatello, the Brains", "Leonardo, the Balance"): None,              # partner-Character select
            ("Bjorna, Nightfall Alchemist", "Abby, Merciless Soldier"): "pair",    # different partner-[text]s (702.124f)
            ("Thrasios, Triton Hero", "Bjorna, Nightfall Alchemist"): "pair",      # partner + partner-[text]
        }
        for (a, b), want in cases.items():
            with self.subTest(pair=(a, b)):
                kinds = self.kinds(self.check(*deck([a, b])))
                if want:
                    self.assertIn(want, kinds)
                else:
                    self.assertNotIn("pair", kinds)
                    self.assertNotIn("commander", kinds)

    def test_an_unknown_partner_variant_is_unchecked_not_illegal(self):
        a = dict(CARDS["thrasios, triton hero"], oracle_text="Partner—Space pals (You can have two commanders if both have this ability.)")
        b = dict(CARDS["tymna the weaver"], oracle_text="Partner—Space pals (You can have two commanders if both have this ability.)")
        store = Store()
        store.peek_card = lambda n: {"x": a, "y": b}.get(n.lower()) or CARDS.get(n.lower())
        v = lg.check(["X", "Y"], ["Forest"] * 98, store)
        self.assertNotIn("pair", self.kinds(v))
        self.assertTrue(any(w == "commander pair" for w, _d in v.unchecked))

    def test_three_commanders(self):
        v = self.check(*deck(["Thrasios, Triton Hero", "Tymna the Weaver", "Bjorna, Nightfall Alchemist"]))
        self.assertIn("pair", self.kinds(v))


class StatusTests(Case):
    def test_an_issue_beats_missing_data(self):
        v = self.check(*deck(["Kinnan, Bonder Prodigy"], "Sol Ring", "Sol Ring"), store=Store(["Kinnan, Bonder Prodigy"]))
        self.assertEqual(v.status, "not_legal")

    def test_unchecked_and_legal(self):
        self.assertEqual(self.check(*deck(["Kinnan, Bonder Prodigy"], "Arcane Signet"), store=Store(["Kinnan, Bonder Prodigy"])).status,
                         "unchecked")
        self.assertEqual(self.check(*deck(["Kinnan, Bonder Prodigy"], "Arcane Signet")).status, "legal")

    def test_no_banned_list_is_never_silence(self):
        lg._BANNED["commander"] = lg.BanList(frozenset(), None, "none")
        v = self.check(*deck(["Kinnan, Bonder Prodigy"]))
        self.assertEqual(v.status, "unchecked")
        self.assertIn("Banned list not checked (no internet and no saved list).", [t for t, _b in lg.problem_lines(v)])


class ProblemLineTests(Case):
    def setUp(self):
        super().setUp()
        lg.set_store(self.store)

    def tearDown(self):
        lg.set_store(None)
        super().tearDown()

    def test_every_line_is_a_warning_and_no_commander_still_blocks(self):
        c, d = deck(["Kinnan, Bonder Prodigy"], "Mana Crypt", "Sol Ring", "Sol Ring", "Lightning Bolt", "Mox Lotus")
        lines = lib.describe_problems(c, d[:-1])
        self.assertTrue(lines)
        self.assertTrue(all(not b for _t, b in lines))
        self.assertEqual(sum(1 for t, _b in lines if "a Commander deck has 100" in t), 1)
        self.assertEqual(lines[-1][0], "You can still play this deck. It's just marked Not legal.")
        self.assertNotIn("UNCHECKED", [lg.label(self.check(c, d))])
        self.assertIn(("No commander found. Put the commander on the first line, or under a line that says Commander.", True),
                      lib.describe_problems([], ["Forest"] * 100))

    def test_an_ineligible_commander_is_the_one_line_that_blocks(self):
        lines = lib.describe_problems(*deck(["Llanowar Elves"], "Mana Crypt", "Sol Ring", "Sol Ring"))
        blocking = [t for t, b in lines if b]
        self.assertEqual(len(blocking), 1)
        self.assertTrue(blocking[0].startswith("Can't be a commander: Llanowar Elves"))
        self.assertEqual(lines[-1][0], "Only the commander stops this deck from starting; the other lines are warnings.")

    def test_a_commander_without_card_data_does_not_block(self):
        lines = lib.describe_problems(*deck(["Llanowar Elves"]))
        self.assertTrue(any(b for _t, b in lines))
        lg.set_store(Store([]))
        lines = lib.describe_problems(*deck(["Llanowar Elves"]))
        self.assertFalse(any(b for _t, b in lines))                 # unknown is UNCHECKED, never a block

    def test_banned_line_says_the_list_date(self):
        v = self.check(*deck(["Kinnan, Bonder Prodigy"], "Mana Crypt"))
        self.assertEqual(lg.problem_lines(v)[0][0], "Banned in Commander (list as of 2 Oct 2026): Mana Crypt.")

    def test_a_deck_breaking_every_rule_but_the_commander_is_still_playable(self):
        import pygame
        pygame.init()
        import forge_menu as fmenu
        folder = os.path.join(self.tmp, "decks")
        os.makedirs(folder)
        text = "Commander\n1 Kinnan, Bonder Prodigy\nDeck\n1 Mana Crypt\n2 Sol Ring\n1 Lightning Bolt\n1 Mox Lotus\n80 Forest\n"
        with open(os.path.join(folder, "bad.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(folder, "elves.txt"), "w", encoding="utf-8") as f:
            f.write("Commander\n1 Llanowar Elves\nDeck\n99 Forest\n")
        entries = lib.list_decks(folder, os.path.join(self.tmp, "none"), self.tmp)
        menu = fmenu.DeckMenu(entries, runtime=None, dirs=(folder, os.path.join(self.tmp, "none"), self.tmp))
        e, elves = [x.load() for x in sorted(entries, key=lambda x: x.name)]
        self.assertEqual((e.name, elves.name), ("bad", "elves"))
        self.assertEqual(e.legality().status, "not_legal")
        self.assertTrue(menu.playable(e))
        self.assertFalse(menu.playable(elves))                         # Karl, 2 Oct: only legal commanders start
        self.assertEqual(fmenu.legal_tag(elves)[0], "NOT LEGAL")
        self.assertEqual(fmenu.legal_tag(e)[0], "NOT LEGAL")
        self.assertIn("NOT LEGAL", [t for t, _c in menu.row_tags(e)])
        menu.opps = [fmenu.RANDOM, None, None]
        seats, problem = menu.resolve_seats()
        self.assertIsNone(problem)
        self.assertEqual(seats[0].id, e.id)


class CacheTests(Case):
    def test_problems_recompute_when_the_version_moves(self):
        folder = os.path.join(self.tmp, "d")
        os.makedirs(folder)
        with open(os.path.join(folder, "k.txt"), "w", encoding="utf-8") as f:
            f.write("Commander\n1 Kinnan, Bonder Prodigy\nDeck\n1 Lightning Bolt\n98 Forest\n")
        e = lib.list_decks(folder, os.path.join(self.tmp, "n"), self.tmp)[0].load()
        store = Store(["Forest"])
        lg.set_store(store)
        try:
            first = e.problems()
            self.assertTrue(any("not checked yet" in t for t, _b in first))
            t = lg.ensure_card_data(["Kinnan, Bonder Prodigy", "Lightning Bolt"])
            self.assertIsNone(t)                                   # MANTICORE_OFFLINE (tests/__init__.py): no fetch in a test
            with mock.patch.dict(os.environ, {"MANTICORE_OFFLINE": ""}):
                lg.ensure_card_data(["Kinnan, Bonder Prodigy", "Lightning Bolt", "Forest"]).join(5)
            self.assertEqual(store.fetched, [["Kinnan, Bonder Prodigy", "Lightning Bolt"]])     # only what was missing
            again = e.problems()
            self.assertTrue(any(t.startswith("Outside Kinnan's colours") for t, _b in again))
        finally:
            lg.set_store(None)


class BanListFileTests(Case):
    def test_load_order_cache_then_snapshot_then_none(self):
        with mock.patch.object(lg, "snapshot_file", return_value=os.path.join(self.tmp, "snap.json")):
            self.assertEqual(lg.load().source, "none")
            lg.write_list(os.path.join(self.tmp, "snap.json"), ["Mana Crypt"])
            self.assertEqual(lg.load().source, "snapshot")
            lg.write_list(lg.cache_file(), ["Mana Crypt", "Nadu, Winged Wisdom"])
            got = lg.load()
            self.assertEqual(got.source, "scryfall")
            self.assertIn("nadu, winged wisdom", got.names)
            with open(lg.cache_file(), "w") as f:
                f.write("{broken")
            self.assertEqual(lg.load().source, "snapshot")             # a corrupt cache falls back

    def test_the_bundled_snapshot_is_real_and_safe_to_back_up(self):
        path = os.path.join(BASE, "banned_commander.snapshot.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertGreater(len(data["cards"]), 50)
        self.assertIn("Mana Crypt", data["cards"])
        name = os.path.basename(path)
        import fnmatch
        self.assertFalse(any(fnmatch.fnmatch(name.lower(), pat) for pat in backup.SECRET_NAMES))
        self.assertNotIn(name.split(".")[0].lower(), backup.RESERVED_NAMES)

    def test_refresh_follows_pages_writes_atomically_and_bumps(self):
        pages = [{"data": [{"name": "Mana Crypt"}], "has_more": True, "next_page": "https://api.scryfall.com/page2"},
                 {"data": [{"name": "Nadu, Winged Wisdom"}], "has_more": False}]
        session = mock.Mock()
        session.get.side_effect = [mock.Mock(status_code=200, json=mock.Mock(return_value=p)) for p in pages]
        before = lg.version()
        with mock.patch.object(lg.time, "sleep"):
            self.assertTrue(lg.refresh(session=session))
        self.assertEqual(session.get.call_count, 2)
        self.assertGreater(lg.version(), before)
        self.assertFalse(os.path.exists(lg.cache_file() + ".tmp"))
        self.assertIn("nadu, winged wisdom", lg.banned().names)

    def test_a_failed_refresh_keeps_the_old_list_and_notes_it(self):
        old = lg.banned()
        session = mock.Mock()
        session.get.return_value = mock.Mock(status_code=503)
        with mock.patch("crashlog.note") as note:
            self.assertFalse(lg.refresh(session=session))
        self.assertEqual(lg.banned(), old)
        note.assert_called_once()

    def test_fresh_cache_means_no_request(self):
        lg.write_list(lg.cache_file(), ["Mana Crypt"])
        with mock.patch.dict(os.environ, {"MANTICORE_OFFLINE": ""}):
            self.assertIsNone(lg.refresh_in_background())
            os.utime(lg.cache_file(), (time.time() - 8 * 86400,) * 2)
            self.assertTrue(lg.needs_refresh())


@unittest.skipUnless(os.environ.get("MANTICORE_LIVE_NETWORK"), "set MANTICORE_LIVE_NETWORK=1 to ask Scryfall")
class LiveTests(unittest.TestCase):
    def test_the_real_banned_list(self):
        names = lg.fetch_banned()
        self.assertGreater(len(names), 50)
        self.assertIn("Mana Crypt", names)


if __name__ == "__main__":
    unittest.main()
