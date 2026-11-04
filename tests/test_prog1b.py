# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG1b (Karl, 3 Nov 2026 06:59 CST: "A now" - the deck-power meter becomes Card quality, says what it is, and the deck
editor shows the deck's shape; the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md section 8).

  1  NameTests     "Card quality" wherever the meter shows (the map's corner window, the deck editor, the +/- after a change);
                   the same formula (card_quality; deck_power another name for it); progression.json's old "meter" key reads
  2  ShapeTests    the mana curve (0 ... 5, 6+) of the non-land cards and the counts (creatures, other spells, lands, removal),
                   in the deck editor, updated by every tap
  3  RemovalTests  the removal rule on Oracle text (rpg.shape), and rpg/data/removal.json
  4  ExplainTests  the one line saying what Card quality is: under it in the editor when it fits, said by a tap on it (map and
                   editor); a tap on the map's line never walks; no win-rate anywhere
  5  OldSaveTests  a save from before the round loads and its deck editor shows the shape
  6  FitTests      the shape and the line inside the window at 1360x840, 1920x1080, 2736x1824, at 100% and 200% text

Run: MANTICORE_SKIP_LIVE=1 MANTICORE_PORTABLE=1 SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -W ignore -m unittest tests.test_prog1b
"""
import json
import time
import unittest
from unittest import mock

import pygame

import rpg_screens as rs
from rpg import progress, shape
from rpg import save as sv
from tests import test_rpg8 as r8
from tests.test_forge_table import click, frame, key
from tests.test_prog1 import PROG_SIZES, PlayedBase


class Base(PlayedBase):
    def to_deck(self, g):
        f = g.modal
        key(g, pygame.K_ESCAPE)
        self.press(g, "deck")
        frame(g)
        self.assertEqual(f.stage, "deck")
        return f

    def drawn(self, f, kind):
        return [(t, r) for k, t, r in f.shape_drawn["lines"] if k == kind]


class NameTests(Base):
    def test_the_formula_keeps_its_old_name_and_the_text_is_card_quality(self):
        self.assertIs(progress.deck_power, progress.card_quality)
        self.assertIs(progress.power_text, progress.quality_text)
        self.assertEqual(progress.quality_text(1.75), "Card quality 1.8")
        self.assertEqual(progress.quality_text(None), "Card quality -")

    def test_the_map_and_the_deck_editor_say_card_quality_never_deck_power(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        words = [t for t, _r in f.hud_words]
        want = progress.quality_text(progress.card_quality(f.game.deck()["main"], f.game.pool, f.game.tiers()))
        self.assertIn(want, words)
        self.assertFalse([w for w in words if "power" in w.lower()], words)
        f = self.to_deck(g)
        lines = [t for t, _r in self.drawn(f, "quality")]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("Card quality "), lines)
        e = f.editor
        tier = f.game.tiers().tier_of
        low = min(tier(n) for n in e.main if tier(n) and not shape.is_land(f.game.pool.get(n)))
        for n in [n for n in e.main if tier(n) == low and not shape.is_land(f.game.pool.get(n))]:
            while e.remove(n):
                pass
        frame(g)
        lines = [t for t, _r in self.drawn(f, "quality")]
        self.assertRegex(lines[0], r"^Card quality \d\.\d\s+[+-]\d\.\d$")      # the change after it

    def test_progression_json_with_the_old_meter_key_still_reads(self):
        with open(progress.PROGRESSION_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertIn("card_quality", d)
        self.assertNotIn("meter", d)
        old = dict(d)
        old["meter"] = {"change_seconds": 2.5, "decimals": 2}
        old.pop("card_quality")
        p = progress.Progression(old)
        self.assertEqual((p.change_seconds, p.decimals, p.explain), (2.5, 2, progress.QUALITY_EXPLAIN))
        d["card_quality"]["explain"] = ""
        with self.assertRaisesRegex(ValueError, "card_quality.explain"):
            progress.Progression(d)


class ShapeTests(Base):
    def test_the_curve_and_the_counts(self):
        game = r8.new_game(None)
        cards = [("Forest", 17), ("Llanowar Elves", 4), ("Lightning Bolt", 2), ("Murder", 1), ("Craw Wurm", 2),
                 ("Ulamog, the Infinite Gyre", 1), ("Ornithopter", 3), ("Divination", 2), ("Thriving Grove", 2), ("Nope", 5)]
        s = shape.shape(cards, game.pool)
        self.assertEqual(s["curve"], [3, 4 + 2, 0, 1 + 2, 0, 0, 2 + 1])       # Murder and Divination cost 3
        self.assertEqual((s["creatures"], s["other"], s["lands"], s["removal"]), (4 + 2 + 1 + 3, 2 + 1 + 2, 19, 2 + 1 + 1))   # Ulamog: "destroy target permanent"
        self.assertEqual(shape.shape_lines(s), ["Creatures 10", "Other spells 5", "Lands 19", "Removal 4"])
        self.assertEqual(shape.CURVE_LABELS, ("0", "1", "2", "3", "4", "5", "6+"))

    def test_the_deck_editor_shows_it_and_a_tap_changes_it(self):
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = self.to_deck(g)
        e = f.editor
        want = shape.shape(e.as_deck()["main"], f.game.pool)
        rect, bars = f.shape_drawn["curve"]
        self.assertEqual([lab for lab, _n, _r in bars], list(shape.CURVE_LABELS))
        self.assertEqual([n for _lab, n, _r in bars], want["curve"])
        counts = " ".join(t for t, _r in self.drawn(f, "counts"))
        for word in shape.shape_lines(want):
            self.assertIn(word, counts)
        tall = max(range(7), key=lambda i: want["curve"][i])
        self.assertGreater(bars[tall][2].h, 0)
        line = next((r, n) for r, k, n in f.rows if k == "deckline" and not shape.is_land(f.game.pool.get(n)))
        click(g, line[0].center)                                          # a tap on a deck line: one less
        frame(g)
        after = shape.shape(e.as_deck()["main"], f.game.pool)
        self.assertEqual(sum(after["curve"]), sum(want["curve"]) - 1)
        self.assertEqual([n for _lab, n, _r in f.shape_drawn["curve"][1]], after["curve"])
        mv = min(shape.CURVE_TOP, f.game.pool.get(line[1]).mana_value)
        self.assertEqual(after["curve"][mv], want["curve"][mv] - 1)


class RemovalTests(unittest.TestCase):
    YES = {
        "Lightning Bolt deals 3 damage to any target.": "damage",
        "Destroy target nonblack creature.": "destroy",
        "Exile target creature. Its controller gains life equal to its power.": "exile",
        "Enchant creature\\nEnchanted creature can't attack or block.": "lock",
        "Target creature gets -3/-3 until end of turn.": "shrink",
        "Target creature you control fights target creature you don't control.": "fight",
        "Target player sacrifices a creature.": "edict",
        "Destroy all creatures. They can't be regenerated.": "destroy",
        "When this creature enters, it deals 4 damage to target creature.": "damage",
        "It deals 5 damage divided as you choose among any number of target attacking or blocking creatures.": "damage",
        "Put a -1/-1 counter on each creature your opponents control.": "shrink",
    }
    NO = ("Flying", "Flying, vigilance\\nLifelink", "Counter target spell.", "Return target creature to its owner's hand.",
          "Exile target card from a graveyard.", "As an additional cost to cast this spell, sacrifice a creature.\\nDraw two cards.",
          "Put a -1/-1 counter on Devoted Druid: Untap Devoted Druid.", "{T}: This deals 2 damage to target player or planeswalker.",
          "Exile target creature you control, then return that card to the battlefield under its owner's control.",
          "Destroy target land.", "Prevent all damage that would be dealt to target creature this turn.")

    def test_the_rule_marks_removal(self):
        for text, kind in self.YES.items():
            with self.subTest(text=text):
                self.assertEqual(shape.removal_reason(text), kind)

    def test_the_rule_leaves_the_rest(self):
        for text in self.NO:
            with self.subTest(text=text):
                self.assertIsNone(shape.removal_reason(text))

    def test_removal_json_is_the_rule_over_forge_s_scripts(self):
        names = shape.removal_names()
        for n in ("Lightning Bolt", "Murder", "Pacifism", "Swords to Plowshares", "Doom Blade"):
            self.assertIn(progress.cardpool.normal_name(n), names, n)
        for n in ("Grizzly Bears", "Llanowar Elves", "Counterspell", "Serra Angel", "Unsummon", "Forest"):
            self.assertNotIn(progress.cardpool.normal_name(n), names, n)
        with open(shape.REMOVAL_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertIn("9a6f776", d["forge"])
        self.assertGreater(len(d["cards"]), 1000)

    def test_a_script_s_faces(self):
        dfc = "Name:A\nAlternateMode:DoubleFaced\nOracle:Flying\n\nALTERNATE\n\nName:B\nOracle:Destroy target creature."
        self.assertEqual(shape.script_faces(dfc), [("A", "Flying")])               # a transforming card's back doesn't count
        mdfc = dfc.replace("DoubleFaced", "Modal")
        self.assertEqual(shape.script_faces(mdfc), [("A", "Flying"), ("B", "Destroy target creature.")])

    def test_a_missing_file_counts_none(self):
        self.assertEqual(shape.removal_names("/nonexistent/removal.json"), set())


class ExplainTests(Base):
    def test_a_tap_on_the_map_s_line_says_what_it_is_and_never_walks(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        frame(g)
        where = (f.game.save.world.get("x"), f.game.save.world.get("y"))
        r = next(r for t, r in f.hud_words if t.startswith("Card quality"))
        click(g, r.center)
        frame(g)
        self.assertEqual(f.message[0], progress.read().explain)
        self.assertIsNone(f.tap)
        self.assertEqual((f.game.save.world.get("x"), f.game.save.world.get("y")), where)

    def test_the_editor_says_it_under_the_line_and_by_a_tap(self):
        g, _l = self.setup_game(size=(2736, 1824))
        self.on_map(g, "ashfen_outskirts")
        f = self.to_deck(g)
        said = " ".join(t for t, _r in self.drawn(f, "explain"))
        self.assertEqual(said.split(), progress.read().explain.split())
        r = self.drawn(f, "quality")[0][1]
        click(g, r.center)
        frame(g)
        self.assertEqual(f.message[0], progress.read().explain)

    def test_no_win_rate_anywhere(self):
        self.assertNotIn("%", progress.read().explain)
        self.assertNotIn("win", progress.read().explain.lower())


class OldSaveTests(Base):
    def test_a_save_from_before_the_round_shows_its_shape(self):
        game = r8.new_game(None)
        d = game.save.to_dict()
        for k in ("found", "bestiary"):
            d.pop(k, None)
        s = sv.SaveGame.from_dict(d, pool=self.pool)
        main = (s.deck() or {}).get("main") or []
        self.assertEqual(sum(shape.shape(main, self.pool)["curve"]) + shape.shape(main, self.pool)["lands"],
                         sum(k for _n, k in main))
        g, _l = self.setup_game()
        self.on_map(g, "ashfen_outskirts")
        f = self.to_deck(g)
        self.assertIsNotNone(f.shape_drawn["curve"])


class FitTests(Base):
    def test_the_shape_and_the_line_fit(self):
        for size, scale in PROG_SIZES:
            with self.subTest(size=size, scale=scale):
                r8.new_game(None)
                g = self.gui(size=size, scale=scale)
                self.open(g, slot=1)
                f = self.on_map(g, "ashfen_outskirts")
                frame(g)
                whole = g.screen.get_rect()
                for t, r in f.hud_words:
                    self.assertTrue(whole.contains(r), (t, r))
                f = self.to_deck(g)
                panel = f.decklist_rect
                rect, bars = f.shape_drawn["curve"]
                self.assertTrue(panel.contains(rect), (rect, panel))
                for _lab, _n, bar in bars:
                    self.assertTrue(rect.contains(bar) or bar.h == 0, (bar, rect))
                ys = []
                for kind, t, r in f.shape_drawn["lines"]:
                    self.assertTrue(panel.contains(r), (kind, t, r, panel))
                    ys.append((r.top, r.bottom))
                    if kind != "curve":
                        self.assertFalse(r.colliderect(rect), (kind, t, r, rect))
                for (a0, a1), (b0, b1) in zip(ys, ys[1:]):
                    self.assertLessEqual(a1, b0 + 1)                       # lines one under another
                self.close(g)


if __name__ == "__main__":
    unittest.main()
