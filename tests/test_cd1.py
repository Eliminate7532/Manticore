# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CD1 (9 Oct 2026): the Commander cube draft's engine - cube_source.py, draft_core.py, draft_ai.py, draft_deck.py and
tools/draft_sim.py. Offline: every cube here is made up in this file (no one's real list is kept in the tests). One test reads this
computer's Forge scripts when forge_runtime/ is there (Karl's PC), and is skipped otherwise."""
import json
import os
import random
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import cube_source as cs  # noqa: E402
import draft_ai as da  # noqa: E402
import draft_core as dc  # noqa: E402
import draft_deck as dd  # noqa: E402

COLOURS = "WUBRG"
BASIC_OF = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}


def details(name, type_line, identity="", elo=1300.0, cost=(), oracle="", power=None, cmc=None):
    return {"name": name, "type": type_line, "color_identity": list(identity), "colors": list(identity), "elo": elo,
            "parsed_cost": list(cost), "cmc": cmc if cmc is not None else len(cost), "oracle_text": oracle, "power": power,
            "set": "tst", "collector_number": "1", "oracle_id": "o-" + name, "scryfall_id": "s-" + name}


def made_up_cube(seed=5, per_colour=44, legends_per_colour=8, pairs=10, colourless=20, lands=20):
    """A Commander-cube-shaped list of made-up cards: mono-coloured spells and legends, two-colour legends, colourless artifacts and
    two-colour lands, with Elo values spread like a real cube's (1100-1700)."""
    rng = random.Random(seed)
    out = []
    for col in COLOURS:
        for k in range(per_colour):
            out.append(details(f"{col} Spell {k}", "Creature — Test" if k % 2 else "Instant", col, rng.uniform(1100, 1600),
                               [col.lower()] + ["1"] * rng.randint(0, 5)))
        for k in range(legends_per_colour):
            out.append(details(f"{col} Legend {k}", "Legendary Creature — Test", col, rng.uniform(1150, 1550),
                               [col.lower(), "2"], power="2"))
    for k in range(pairs):
        a, b = rng.sample(COLOURS, 2)
        out.append(details(f"{a}{b} Legend {k}", "Legendary Creature — Test", a + b, rng.uniform(1200, 1650),
                           [a.lower(), b.lower(), "1"], power="3"))
    for k in range(colourless):
        out.append(details(f"Artifact {k}", "Artifact", "", rng.uniform(1100, 1700), ["2"]))
    for k in range(lands):
        a, b = rng.sample(COLOURS, 2)
        out.append(details(f"{a}{b} Land {k}", "Land", a + b, rng.uniform(1150, 1500), []))
    return {"name": "Made-up Cube", "shortId": "madeup", "owner": {"username": "nobody"},
            "dateLastUpdated": 1, "cards": {"mainboard": [{"details": d} for d in out]}}


def cards_of(data=None):
    return list(cs.from_cubecobra(data or made_up_cube(), "madeup").cards)


def full_draft(cards, seed=3, kinds=("ai",) * 4):
    d = dc.Draft(len(cards), [dc.Seat(f"S{i}", k) for i, k in enumerate(kinds)], seed)
    dc.run_ai(d, da.chooser(cards, seed), seats=range(len(kinds)))
    return d


# ==================================================================================================================================
class CubeSourceTests(unittest.TestCase):
    def test_a_link_or_an_id_gives_the_id(self):
        for text in ("cm", " cm ", "https://cubecobra.com/cube/overview/cm", "cubecobra.com/cube/list/cm?view=table",
                     "https://www.cubecobra.com/cube/playtest/cm", "https://cubecobra.com/cube/api/cubeJSON/cm",
                     "https://cubecobra.com/cube/download/forge/cm"):
            self.assertEqual(cs.parse_ref(text), "cm", text)
        self.assertEqual(cs.parse_ref("https://cubecobra.com/cube/overview/5561fe39-6e8d-4e5d-b9b6-cbadaca433ba"),
                         "5561fe39-6e8d-4e5d-b9b6-cbadaca433ba")

    def test_anything_else_is_refused_in_words(self):
        for text in ("", "   ", "https://example.com/cube/overview/cm", "a/b", "cm cm", "x" * 65):
            with self.assertRaises(cs.CubeError):
                cs.parse_ref(text)

    def test_the_cards_come_from_the_mainboard_with_their_rules_fields(self):
        data = {"name": "T", "cards": {"mainboard": [
            {"details": details("Kinnan, Bonder Prodigy", "Legendary Creature — Human Druid", "UG", 1500, ["g", "u"], power="2")},
            {"details": details("Llanowar Elves", "Creature — Elf Druid", "G", 1300, ["g"], power="1")},
            {"details": details("Grist, the Hunger Tide", "Legendary Planeswalker — Grist", "BG", 1450, ["1", "b", "g"],
                                oracle="As long as Grist, the Hunger Tide isn't on the battlefield, it's a 1/1 Insect creature "
                                       "in addition to its other types.")},
            {"details": details("Commodore Guff", "Legendary Planeswalker — Guff", "URW", 1300, ["3", "u", "r", "w"],
                                oracle="Commodore Guff can be your commander.")},
            {"details": details("Candlekeep Sage", "Legendary Enchantment — Background", "U", 1250, ["1", "u"])},
            {"details": {"name": "No Elo Card", "type": "Artifact", "color_identity": []}},
            {"details": None}, {"no": "details"}],
            "maybeboard": [{"details": details("Not Drafted", "Artifact", "", 1700)}]}}
        cube = cs.from_cubecobra(data, "t")
        by = {c.name: c for c in cube.cards}
        self.assertEqual(len(cube.cards), 6)                                       # mainboard only, broken entries skipped
        self.assertTrue(by["Kinnan, Bonder Prodigy"].commander)
        self.assertEqual(by["Kinnan, Bonder Prodigy"].identity, ("U", "G"))         # WUBRG order
        self.assertFalse(by["Llanowar Elves"].commander)
        self.assertFalse(by["Grist, the Hunger Tide"].commander)                    # 903.3: a planeswalker needs the words
        self.assertTrue(by["Commodore Guff"].commander)                             # 903.3a
        self.assertFalse(by["Candlekeep Sage"].commander)                           # a Background only beside its commander
        self.assertEqual(by["No Elo Card"].elo, 1200.0)                             # Cube Cobra's DefaultElo

    def test_lands_and_basics(self):
        land = cs.card_from_details(details("Breeding Pool", "Land — Forest Island", "UG"))
        basic = cs.card_from_details(details("Snow-Covered Forest", "Basic Snow Land — Forest", "G"))
        spell = cs.card_from_details(details("Growth Spiral", "Instant", "UG"))
        self.assertTrue(cs.is_land(land) and cs.is_land(basic) and not cs.is_land(spell))
        self.assertTrue(cs.is_basic(basic) and not cs.is_basic(land))
        mdfc = cs.card_from_details(details("Turntimber Symbiosis", "Creature — Elemental // Land", "G"))
        self.assertFalse(cs.is_land(mdfc))                                          # its front is a creature

    def test_the_kept_copy_is_used_and_a_failed_refresh_falls_back_to_it(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(cs.CubeError):                                   # nothing kept and offline
                cs.load("madeup", folder=folder)
            cs.save_cache("madeup", made_up_cube(), folder, now=123.0)
            cube, note = cs.load("https://cubecobra.com/cube/overview/madeup", folder=folder)
            self.assertEqual((cube.name, cube.fetched, note), ("Made-up Cube", 123.0, ""))
            cube, note = cs.load("madeup", refresh=True, folder=folder)             # MANTICORE_OFFLINE: the refresh fails
            self.assertEqual(cube.name, "Made-up Cube")
            self.assertIn("Using the copy kept on this computer", note)

    def test_fetch_says_why_in_words(self):
        class Resp:
            def __init__(self, code, body=b"{}"):
                self.status_code, self.body = code, body

            def iter_content(self, n):
                for k in range(0, len(self.body), n):
                    yield self.body[k:k + n]

        class Sess:
            def __init__(self, resp):
                self.resp, self.urls = resp, []

            def get(self, url, **kw):
                self.urls.append((url, kw["headers"]["User-Agent"]))
                return self.resp

        with mock.patch.dict(os.environ, {"MANTICORE_OFFLINE": ""}):
            s = Sess(Resp(200, json.dumps(made_up_cube()).encode()))
            self.assertEqual(cs.fetch("madeup", session=s)["name"], "Made-up Cube")
            self.assertEqual(s.urls[0][0], "https://cubecobra.com/cube/api/cubeJSON/madeup")
            self.assertTrue(s.urls[0][1].startswith("Manticore/"))
            for code, words in ((404, "no cube called"), (429, "too many"), (500, "answered 500")):
                with self.assertRaises(cs.CubeError) as e:
                    cs.fetch("madeup", session=Sess(Resp(code)))
                self.assertIn(words, str(e.exception))
            with mock.patch.object(cs, "MAX_BYTES", 10), self.assertRaises(cs.CubeError):
                cs.fetch("madeup", session=Sess(Resp(200, b"{" + b" " * 100 + b"}")))
            with self.assertRaises(cs.CubeError):
                cs.fetch("madeup", session=Sess(Resp(200, b"not json")))

    def test_forge_check_leaves_out_unscripted_cards_and_names_the_flagged_ones(self):
        cards = [cs.card_from_details(details(n, "Artifact")) for n in ("Sol Ring", "Missing Card", "Flagged Card", "Sol Ring",
                                                                       "Missing Card")]
        scripts = {"Sol Ring": "Name:Sol Ring\nA:AB$ Mana", "Flagged Card": "Name:Flagged Card\nAI:RemoveDeck:All\n"}
        asked = []
        chk = cs.check_forge(cards, lambda n: asked.append(n) or scripts.get(n))
        self.assertEqual([c.name for c in chk.playable], ["Sol Ring", "Flagged Card", "Sol Ring"])  # a cube's doubles stay
        self.assertEqual(sorted(asked), ["Flagged Card", "Missing Card", "Sol Ring"])          # each name looked up once
        self.assertEqual(chk.missing, ("Missing Card",))
        self.assertEqual(chk.ai_flagged, frozenset({"Flagged Card"}))
        mild = cs.check_forge([cards[0]], {"Sol Ring": "Name:Sol Ring\nAI:RemoveDeck:Random\n"}.get)
        self.assertEqual(mild.ai_flagged, frozenset())                              # only "All": "Random" is a milder flag

    @unittest.skipUnless(os.path.isdir(os.path.join(BASE, "forge_runtime", "res", "cardsfolder")), "no forge_runtime here")
    def test_this_computers_forge_knows_a_real_card_and_not_a_made_up_one(self):
        look = cs.forge_script_lookup(os.path.join(BASE, "forge_runtime"))
        self.assertIsNotNone(look("Sol Ring"))
        self.assertIsNone(look("Zzyx the Imaginary"))


# ==================================================================================================================================
class DraftCoreTests(unittest.TestCase):
    def seats(self, kinds=("ai",) * 4):
        return [dc.Seat(f"S{i}", k) for i, k in enumerate(kinds)]

    def test_the_standard_draft_is_903_13_with_four_seats(self):
        self.assertEqual((dc.SEATS, dc.PACKS, dc.PACK_SIZE, dc.PICKS, dc.DIRECTIONS), (4, 3, 20, 2, (1, -1, 1)))
        self.assertEqual(dc.cards_needed(), 240)

    def test_a_cube_too_small_is_refused_with_the_numbers(self):
        with self.assertRaises(dc.DraftError) as e:
            dc.Draft(239, self.seats(), 1)
        self.assertIn("239", str(e.exception))
        self.assertIn("240", str(e.exception))
        dc.Draft(240, self.seats(), 1)

    def test_every_card_is_drafted_once_and_every_seat_ends_with_sixty(self):
        d = dc.Draft(300, self.seats(), 9)
        dc.run_ai(d, lambda s, pack, pool, n: pack[:n])
        self.assertTrue(d.done)
        self.assertEqual([len(p) for p in d.pools], [60] * 4)
        allc = [c for p in d.pools for c in p]
        self.assertEqual(len(set(allc)), 240)
        self.assertEqual(len(d.log), 4 * 3 * 10)                                    # ten turns of two cards per pack round

    def test_packs_pass_left_right_left(self):
        for rnd, step in ((0, 1), (1, -1), (2, 1)):
            d = dc.Draft(240, self.seats(), 2)
            while d.round < rnd:                                                     # finish the earlier rounds
                for s in d.seats_to_act():
                    d.pick(s, d.pack_for(s)[:d.needed(s)])
            opened = d.pack_for(0)
            d.pick(0, opened[:2])
            nxt = (0 + step) % 4
            self.assertEqual(d.waiting_packs(nxt), 2, f"round {rnd + 1}")
            self.assertEqual(list(d.queues[nxt][1].cards), opened[2:])

    def test_a_pick_takes_exactly_two_and_only_from_the_pack_in_front_of_you(self):
        d = dc.Draft(240, self.seats(), 4)
        pack = d.pack_for(1)
        other = d.pack_for(2)
        for bad in ([pack[0]], pack[:3], [pack[0], pack[0]], [pack[0], other[0]]):
            with self.assertRaises(dc.DraftError):
                d.pick(1, bad)
        with self.assertRaises(dc.DraftError):
            d.pick(7, pack[:2])
        d.pick(1, pack[:2])
        self.assertEqual(d.pools[1], pack[:2])

    def test_the_last_pick_of_an_odd_pack_is_one_card(self):
        d = dc.Draft(40, self.seats(), 4, packs=1, pack_size=5)
        picks = []
        while not d.done:
            for s in d.seats_to_act():
                picks.append(d.needed(s))
                d.pick(s, d.pack_for(s)[:d.needed(s)])
        self.assertEqual(sorted(set(picks)), [1, 2])
        self.assertEqual([len(p) for p in d.pools], [5] * 4)

    def test_you_see_only_your_pack_and_a_copy_of_it(self):
        d = dc.Draft(240, self.seats(), 4)
        pack = d.pack_for(0)
        pack.clear()                                                                  # 903.13d: a copy, not the pack
        self.assertEqual(len(d.pack_for(0)), 20)

    def test_the_next_round_opens_only_when_every_pack_is_empty(self):
        d = dc.Draft(240, self.seats(("human", "ai", "ai", "ai")), 6)
        dc.run_ai(d, lambda s, pack, pool, n: pack[:n])
        self.assertEqual(d.round, 0)
        self.assertEqual(d.seats_to_act(), [0])                                     # the AIs wait for the person
        self.assertGreater(d.waiting_packs(0), 1)                                    # packs queue up in front of them
        while not d.done:
            if d.pack_for(0) is not None:
                d.pick(0, d.pack_for(0)[:d.needed(0)])
            dc.run_ai(d, lambda s, pack, pool, n: pack[:n])
        rounds = [p.round for p in d.log]
        self.assertEqual(rounds, sorted(rounds))                                     # no pick of a round before the last one ends
        for s in range(4):
            for r in range(3):
                self.assertEqual(sum(len(p.cards) for p in d.log if p.seat == s and p.round == r), 20)

    def test_the_same_seed_makes_the_same_packs_and_a_save_rebuilds_the_draft(self):
        a, b = dc.Draft(300, self.seats(), 11), dc.Draft(300, self.seats(), 11)
        self.assertEqual(a.pack_for(2), b.pack_for(2))
        self.assertNotEqual(a.pack_for(2), dc.Draft(300, self.seats(), 12).pack_for(2))
        for _ in range(17):                                                          # stop part-way through round 2
            for s in a.seats_to_act():
                a.pick(s, a.pack_for(s)[-a.needed(s):])
        saved = json.loads(json.dumps(a.to_dict(extra={"cube": "madeup"})))
        c = dc.Draft.from_dict(saved)
        self.assertEqual((c.round, c.pools, c.turns), (a.round, a.pools, a.turns))
        self.assertEqual([c.pack_for(s) for s in range(4)], [a.pack_for(s) for s in range(4)])
        self.assertEqual(saved["extra"], {"cube": "madeup"})
        with self.assertRaises(dc.DraftError):
            dc.Draft.from_dict(dict(saved, v=99))

    def test_the_label_says_pack_and_pick(self):
        d = dc.Draft(240, self.seats(), 1)
        self.assertEqual(d.label(0), "Pack 1, pick 1 of 10")
        d.pick(0, d.pack_for(0)[:2])
        self.assertEqual(d.label(0), "Pack 1, pick 2 of 10")

    def test_the_review_lists_every_pick_with_the_pack_it_came_from(self):
        cards = cards_of()
        d = full_draft(cards)
        mine = d.review(2)
        self.assertEqual(len(mine), 30)
        for p in mine:
            self.assertTrue(set(p.cards) <= set(p.pack_before))
        self.assertEqual([p.turn for p in mine[:10]], list(range(1, 11)))


# ==================================================================================================================================
class DraftAITests(unittest.TestCase):
    def setUp(self):
        self.cards = cards_of()
        self.z = dd.zscores(self.cards)
        self.idx = {c.name: i for i, c in enumerate(self.cards)}

    def test_it_takes_the_right_number_of_cards_from_the_pack_and_is_the_same_for_a_seed(self):
        pack = list(range(20))
        a = da.Drafter(self.cards, 1, self.z).choose(pack, [], 2)
        self.assertEqual(len(set(a)), 2)
        self.assertTrue(set(a) <= set(pack))
        self.assertEqual(a, da.Drafter(self.cards, 1, self.z).choose(pack, [], 2))
        self.assertEqual(len(da.Drafter(self.cards, 1, self.z).choose(pack[:1], [], 1)), 1)

    def test_early_it_takes_what_people_take(self):
        pack = sorted(range(len(self.cards)), key=lambda i: self.cards[i].elo)[:19] + [max(range(len(self.cards)),
                                                                                           key=lambda i: self.cards[i].elo)]
        hits = sum(pack[-1] in da.Drafter(self.cards, s, self.z).choose(pack, [], 2) for s in range(50))
        self.assertGreaterEqual(hits, 48)                                            # noise never hides a far stronger card

    def test_later_it_stays_in_its_colours(self):
        greens = sorted((i for i, c in enumerate(self.cards) if c.identity == ("G",) and not c.commander),
                        key=lambda i: self.cards[i].elo)
        g, pool = greens[0], greens[1:31]
        legend = next(i for i, c in enumerate(self.cards) if c.identity == ("G",) and c.commander)
        pool = pool + [legend]
        blue = min((i for i, c in enumerate(self.cards) if c.identity == ("U",) and c.elo > self.cards[g].elo + 30),
                   key=lambda i: self.cards[i].elo)                                  # a little stronger, another colour
        self.assertLess(self.cards[blue].elo - self.cards[g].elo, 150)
        picks = [da.Drafter(self.cards, s, self.z).choose([g, blue], pool, 1)[0] for s in range(30)]
        self.assertGreaterEqual(picks.count(g), 27)

    def test_a_far_stronger_card_of_another_colour_is_still_taken(self):
        greens = [i for i, c in enumerate(self.cards) if c.identity == ("G",) and not c.commander]
        pool = sorted(greens, key=lambda i: self.cards[i].elo)[1:13]                 # 12 cards in: settling, not settled
        g = sorted(greens, key=lambda i: self.cards[i].elo)[0]
        bomb = max((i for i, c in enumerate(self.cards) if c.identity == ("U",)), key=lambda i: self.cards[i].elo)
        picks = [da.Drafter(self.cards, s, self.z).choose([g, bomb], pool, 1)[0] for s in range(30)]
        self.assertGreaterEqual(picks.count(bomb), 27)                               # people take bombs early

    def test_it_makes_sure_of_a_commander_that_fits(self):
        reds = sorted((i for i, c in enumerate(self.cards) if c.identity == ("R",) and not c.commander),
                      key=lambda i: self.cards[i].elo)
        legend = min((i for i, c in enumerate(self.cards) if c.identity == ("R",) and c.commander),
                     key=lambda i: self.cards[i].elo)
        better = min((i for i in reds if self.cards[i].elo > self.cards[legend].elo + 30), key=lambda i: self.cards[i].elo)
        pool = [i for i in reds if i != better][:36]
        self.assertLess(self.cards[better].elo - self.cards[legend].elo, 150)
        picks = [da.Drafter(self.cards, s, self.z).choose([better, legend], pool, 1)[0] for s in range(30)]
        self.assertGreaterEqual(picks.count(legend), 27)
        early = [da.Drafter(self.cards, s, self.z).choose([better, legend], pool[:6], 1)[0] for s in range(30)]
        self.assertLess(early.count(legend), 15)                                     # no worry yet with 6 cards drafted

    def test_the_commander_worry_starts_at_card_twelve_and_is_full_at_thirty_six(self):
        reds = [i for i, c in enumerate(self.cards) if c.identity == ("R",) and not c.commander]
        legend = next(i for i, c in enumerate(self.cards) if c.identity == ("R",) and c.commander)
        dr = da.Drafter(self.cards, 1, self.z)
        with mock.patch.object(da, "NOISE", 0.0):
            def bonus(n):
                pool = reds[:n]
                colours, cmd = dr.plan(pool)
                base = dr.score(legend, pool, colours, bool(cmd))
                with mock.patch.object(da, "COMMANDER_BONUS", 0.0):
                    return base - dr.score(legend, pool, colours, bool(cmd))
            self.assertEqual(bonus(11), 0.0)
            self.assertEqual(bonus(12), 0.0)
            self.assertAlmostEqual(bonus(24), da.COMMANDER_BONUS * 0.5)
            self.assertAlmostEqual(bonus(36), da.COMMANDER_BONUS)

    def test_a_whole_draft_gives_every_ai_a_commander(self):
        d = full_draft(self.cards, seed=21)
        for pool in d.pools:
            self.assertTrue(any(self.cards[i].commander for i in pool))


# ==================================================================================================================================
class DraftDeckTests(unittest.TestCase):
    def setUp(self):
        self.cards = cards_of()
        self.z = dd.zscores(self.cards)

    def ints(self, deck):
        return list(deck.spells + deck.lands) + [n for n, k in deck.basics.items() for _ in range(k)]

    def test_every_ai_deck_is_legal_and_sixty(self):
        for seed in (1, 2, 3):
            d = full_draft(self.cards, seed)
            for s in range(4):
                deck = dd.build(self.cards, d.pools[s], self.z)
                self.assertEqual(dd.deck_size(deck), 60)
                self.assertEqual(dd.check(self.cards, d.pools[s], list(deck.commanders), self.ints(deck)), [])
                cmd, main = dd.deck_names(self.cards, deck)
                self.assertEqual(len(cmd) + len(main), 60)

    def test_the_check_names_each_problem(self):
        d = full_draft(self.cards, 4)
        pool = d.pools[0]
        deck = dd.build(self.cards, pool, self.z)
        cmd, main = list(deck.commanders), self.ints(deck)
        outside = next(i for i in pool if not dd.fits(self.cards[i], deck.identity))
        not_mine = next(i for i in d.pools[1] if dd.fits(self.cards[i], deck.identity) and not cs.is_land(self.cards[i]))
        non_cmd = next(i for i in pool if not self.cards[i].commander)
        wrong_basic = next(BASIC_OF[c] for c in COLOURS if c not in deck.identity)
        cases = [(cmd, main[:-1], "it needs at least 60"), (cmd, main + [outside], "outside your commander's colours"),
                 (cmd, main + [not_mine], "drafted"), (cmd, main + [main[0]], "1 drafted"), ([non_cmd], main, "can't be a commander"),
                 (cmd, main + [wrong_basic], f"{wrong_basic} is outside"), (cmd, main + ["Lightning Bolt"], "isn't a basic land"),
                 ([], main, "Choose a commander")]
        for c, m, words in cases:
            probs = dd.check(self.cards, pool, c, m)
            self.assertTrue(any(words in p for p in probs), (words, probs))

    def test_ten_wrong_basics_are_one_problem(self):
        d = full_draft(self.cards, 4)
        deck = dd.build(self.cards, d.pools[0], self.z)
        wrong = next(BASIC_OF[c] for c in COLOURS if c not in deck.identity)
        probs = dd.check(self.cards, d.pools[0], list(deck.commanders), self.ints(deck) + [wrong] * 10)
        self.assertEqual(sum(wrong in p for p in probs), 1)

    def test_basics_follow_the_mana_symbols_and_every_colour_gets_one(self):
        mk = cs.card_from_details
        cards = [mk(details("A", "Instant", "W", cost=["w", "w", "w"])), mk(details("B", "Instant", "U", cost=["u"])),
                 mk(details("C", "Instant", "WU", cost=["w-u"])), mk(details("D", "Instant", "G", cost=["g-p"]))]
        self.assertEqual(dd.pips(cards[2]), {"W": 0.5, "U": 0.5})
        self.assertEqual(dd.pips(cards[3]), {"G": 0.5})
        b = dd.split_basics(cards, [0, 1, 2], frozenset("WUB"), 20)
        self.assertEqual(sum(b.values()), 20)
        self.assertGreaterEqual(b["Swamp"], 1)                                       # a colour with no symbols still gets one
        self.assertGreater(b["Plains"], b["Island"])
        self.assertEqual(dd.split_basics(cards, [0], frozenset(), 7), {"Wastes": 7})
        self.assertEqual(dd.split_basics(cards, [0], frozenset("W"), 0), {})

    def test_a_colourless_commander_plays_wastes(self):
        data = made_up_cube()
        data["cards"]["mainboard"].append({"details": details("Colourless Legend", "Legendary Artifact Creature — Golem", "",
                                                              1800, ["4"], power="4")})
        cards = cards_of(data)
        legend = len(cards) - 1
        pool = [legend] + [i for i, c in enumerate(cards) if not c.identity and i != legend][:20] + \
               [i for i, c in enumerate(cards) if c.identity == ("R",)][:30]
        deck = dd.build(cards, pool, commanders=(legend,))
        self.assertEqual(list(deck.basics), ["Wastes"])
        self.assertEqual(dd.deck_size(deck), 60)
        self.assertEqual(dd.check(cards, pool, [legend], list(deck.spells + deck.lands) +
                                  ["Wastes"] * deck.basics["Wastes"]), [])

    def test_ai_flagged_cards_go_in_only_when_nothing_else_is_left(self):
        d = full_draft(self.cards, 8)
        pool = d.pools[0]
        plain = dd.build(self.cards, pool, self.z)
        flag = self.cards[plain.spells[0]].name                                     # its best spell, flagged
        flagged = dd.build(self.cards, pool, self.z, avoid=frozenset({flag}), commanders=plain.commanders)
        spare = len([i for i in pool if dd.fits(self.cards[i], plain.identity) and not cs.is_land(self.cards[i])]) \
            - len(plain.commanders) - len(plain.spells)
        if spare > 0:
            self.assertNotIn(flag, [self.cards[i].name for i in flagged.spells])
        thin = dd.build_for(self.cards, plain.spells[:5] + plain.commanders, plain.commanders, self.z, avoid=frozenset({flag}))
        self.assertIn(flag, [self.cards[i].name for i in thin.spells])               # a thin pool still uses it

    def test_partner_and_background_pairs_are_offered(self):
        mk = cs.card_from_details
        cards = [mk(details("Tymna the Weaver", "Legendary Creature — Human Cleric", "WB", 1500, ["1", "w", "b"], "Partner", "2")),
                 mk(details("Kraum, Ludevic's Opus", "Legendary Creature — Zombie Horror", "UR", 1450, ["3", "u", "r"],
                            "Flying, haste\nPartner", "4")),
                 mk(details("Wilson, Refined Grizzly", "Legendary Creature — Bear Warrior", "G", 1400, ["1", "g"],
                            "Choose a Background (You can have a Background as a second commander.)", "2")),
                 mk(details("Raised by Giants", "Legendary Enchantment — Background", "G", 1300, ["5", "g"])),
                 mk(details("Ruhan of the Fomori", "Legendary Creature — Giant Warrior", "WUR", 1300, ["1", "r", "w", "u"],
                            "", "6"))]
        opts = dd.commander_options(cards, range(5))
        self.assertIn((0, 1), opts)
        self.assertIn((2, 3), opts)
        self.assertNotIn((0, 4), opts)
        self.assertEqual(dd.identity_of(cards, (0, 1)), frozenset("WUBR"))
        self.assertEqual(dd.check(cards, [0, 1], [0, 1], ["Plains"] * 58), [])
        self.assertTrue(any("can't be commanders together" in p for p in dd.check(cards, [0, 4], [0, 4], ["Plains"] * 58)))

    def test_no_commander_in_the_pool_means_no_deck(self):
        pool = [i for i, c in enumerate(self.cards) if not c.commander][:60]
        self.assertIsNone(dd.build(self.cards, pool, self.z))


# ==================================================================================================================================
class SimTests(unittest.TestCase):
    def test_the_simulation_measures_a_few_drafts(self):
        import draft_sim
        cards = cards_of()
        m = draft_sim.measure(cards, drafts=3, seed=1)
        self.assertEqual((m["seats"], m["legal"], m["no_commander"]), (12, 12, 0))
        self.assertGreater(m["early_top"], 0.5)
        text = draft_sim.report("Made-up Cube", cards, m, frozenset(), ())
        self.assertIn("Legal decks:          12 of 12", text)

    def test_the_command_line_reads_a_kept_file(self):
        import draft_sim
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "madeup.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"fetched": 1, "cube": made_up_cube()}, f)                # the cache's own layout
            out = os.path.join(folder, "m.json")
            with mock.patch("sys.stdout"):
                self.assertEqual(draft_sim.main(["--cube", path, "--drafts", "2", "--json", out]), 0)
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["seats"], 8)


class NoGameCodeTests(unittest.TestCase):
    def test_the_engine_never_imports_pygame_or_the_table(self):
        for name in ("cube_source.py", "draft_core.py", "draft_ai.py", "draft_deck.py"):
            with open(os.path.join(BASE, name), encoding="utf-8") as f:
                text = f.read()
            for bad in ("import pygame", "forge_table", "gfx"):
                self.assertNotIn(bad, text, name)


if __name__ == "__main__":
    unittest.main()
