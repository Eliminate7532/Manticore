# SPDX-License-Identifier: GPL-3.0-or-later
"""Round GOLD1 - bug report from Erik, 1 Nov 2026 (Adventure, the shop in Lantern Hollow): "Couldn't find my total gold easy" /
"Find my gold total easier. Maybe make yellow also?".

The town's gold was the start of a grey line ("100 gold · Your deck: ..."). Now it has a line of its own, in yellow (DawnBringer's
DB_GOLD in the shop's DawnLike style), under the town's name; the map's corner window and the pause window show it in yellow too."""
import unittest
from unittest import mock

import gfx
import rpg_screens as rs
import rpg_ui
from tests import test_rpg8 as r8
from tests.test_forge_table import frame
from tests.test_rpg14b import SIZES, Spy, StyleBase


class GoldTests(StyleBase):
    def town(self, size=(1360, 840), scale=None):
        g = self.gui(size, scale)
        f = self.open(g, slot=1)
        f.enter_town(g, f.game.world.start_town)
        f.tab = "singles"
        f.game.save.gold = 137
        return g, f

    def test_the_town_shows_the_gold_on_its_own_line_in_yellow(self):
        r8.new_game(None)
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                g, f = self.town(size, scale)
                spy = Spy()
                with mock.patch.object(rs.gfx, "draw_text", spy):
                    frame(g)
                gold = [(s, c, r) for s, _f, c, r in spy.calls if s == "137 gold"]
                self.assertEqual(len(gold), 1, [s for s, *_ in spy.calls][:8])
                _s, colour, rect = gold[0]
                self.assertEqual(colour, tuple(gfx.DB_GOLD))
                self.assertEqual(f.gold_rect, rect)
                others = [(s, r) for s, _f, _c, r in spy.calls if s != "137 gold"]
                self.assertFalse([s for s, r in others if r.colliderect(rect)])          # nothing else on its line
                deck = [(s, c) for s, _f, c, _r in spy.calls if s.startswith("Your deck:")]
                self.assertTrue(deck)
                self.assertNotIn("gold", deck[0][0])
                self.assertEqual(deck[0][1], tuple(gfx.DB_DIM))
                self.close(g)

    def test_the_corner_window_and_the_pause_window_say_the_gold_in_yellow(self):
        r8.new_game(None)
        g, f = self.town()
        seen = []
        real = rpg_ui.text

        def text(surface, s, x, y, colour, *a, **kw):
            seen.append((s, tuple(colour)[:3]))
            return real(surface, s, x, y, colour, *a, **kw)
        with mock.patch.object(rpg_ui, "text", text):
            f.draw_hud(g, "Lantern Hollow", {"gold": 137, "deck": "Starting deck", "cards": 60})
        self.assertIn(("Gold 137", tuple(gfx.DB_GOLD)), seen)
        self.assertIn(("Deck Starting deck (60)", tuple(gfx.DB_TEXT)), seen)
        f.stage = "map"
        f.open_pause(g)
        self.assertIn((f"Gold  {f.game.save.gold}", gfx.DB_GOLD), f.menu.info)
        self.close(g)


if __name__ == "__main__":
    unittest.main()


# ==== Round GOLD1 (task gold1, 3 Nov 2026) - what a lost Adventure duel costs, and winning it back ==============================
# Karl, 3 Nov 07:31 CST: "Change the way you lose gold. Make it a sliding scale based on duel difficulty but never lose more than
# 20%. Then add the lost amount to the winnings if you beat that person after." - "Harder fight, bigger loss": regular 5%,
# dungeon 10%, boss 20% of the gold you carry. (This file's first half is the earlier Round GOLD1 of 1 Nov - the gold in yellow;
# the two rounds share the name, so the new tests live here, as the task's brief says.)
#   LossShareTests   numbers.json's table by kind; an old plain number; a share above 20% refused and named; the clamp
#   ThickSkinTests   the perk is a cut (a quarter less), not a share; "loss_share" is no perk effect any more; the clamp with a perk
#   HeldTests        the foe holds what it took: two losses add up, one win gives it back once, a spot-less duel holds nothing,
#                    a draw / an unfinished duel change nothing, it survives towns and the beaten list, old saves load and play
#   ScreenGoldTests  through the window: the encounter screen's "Lose:" and "Holds" lines, the lines after a loss and after a win
#                    with gold won back, the map's coin; everything inside the window at three sizes and 200% text
import copy
import json
import os
import tempfile
import time

import pygame

import rpg_encounter
import rpg_results
from rpg import adventure as adv
from rpg import duel as rduel
from rpg import economy, levels
from rpg import save as sv
from tests import test_rpg4 as r4
from tests.test_forge_table import key
from tests.test_rpg13 import ScreenBase

GOLD1_SIZES = (((1360, 840), None), ((1920, 1080), None), ((2736, 1824), None), ((1360, 840), 2.0), ((1920, 1080), 2.0),
               ((2736, 1824), 2.0))
TABLE = {"regular": 0.05, "dungeon": 0.10, "boss": 0.20}


def gold1_numbers(share=None):
    return r4.numbers(loss_gold_share=copy.deepcopy(TABLE) if share is None else share)


class _Perky:
    """A save as rpg.levels sees it: gold and the perks taken."""

    def __init__(self, gold, perks=()):
        self.gold = gold
        self.progress = {"xp": 0, "perks": list(perks), "offers": []}


class LossShareTests(unittest.TestCase):
    def test_the_shipped_file_is_a_table_by_kind_of_fight(self):
        n = economy.read_numbers()
        self.assertEqual({k: n.loss_share(k) for k in economy.ENCOUNTERS}, TABLE)
        self.assertEqual(n.loss_gold_share, 0.2)                       # the largest: tools/rpg_soak.py's report reads it
        self.assertEqual((n.loss_cost(500, "regular"), n.loss_cost(500, "dungeon"), n.loss_cost(500, "boss")), (25, 50, 100))
        self.assertEqual(n.loss_cost(500), 25)                         # no kind: a regular fight
        with open(economy.NUMBERS_FILE, encoding="utf-8") as f:
            about = json.load(f)["about"]
        self.assertIn("GOLD1", about)

    def test_an_old_plain_number_is_one_share_for_every_kind(self):
        n = gold1_numbers(0.1)                                         # tools/rpg_soak_settings/option_a.json's 0.1
        self.assertEqual({k: n.loss_share(k) for k in economy.ENCOUNTERS}, dict.fromkeys(economy.ENCOUNTERS, 0.1))
        self.assertEqual(n.loss_cost(101, "boss"), 10)
        self.assertEqual(n.loss_gold_share, 0.1)

    def test_a_share_above_twenty_percent_is_refused_and_named(self):
        for bad, name in ((0.25, "loss_gold_share"), (1.0, "loss_gold_share"),
                          (dict(TABLE, boss=0.3), "loss_gold_share[boss]"), (dict(TABLE, regular=0.21), "loss_gold_share[regular]")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    gold1_numbers(bad)
                self.assertIn(name, str(cm.exception))
                self.assertIn("20%", str(cm.exception))
        self.assertEqual(gold1_numbers(0.2).loss_share("boss"), 0.2)   # exactly 20% is allowed
        for bad in ({"regular": 0.05, "boss": 0.2}, dict(TABLE, rare=0.1), dict(TABLE, boss="lots"), dict(TABLE, boss=-0.1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                gold1_numbers(bad)
            self.assertIn("loss_gold_share", str(cm.exception))

    def test_loss_cost_clamps_to_twenty_percent_whatever_reaches_it(self):
        n = gold1_numbers()
        n.loss_shares["boss"] = 0.9                                    # a value put in after the file was read (a settings tool)
        self.assertEqual(n.loss_cost(1000, "boss"), 200)
        self.assertEqual(economy.cost_of(100, 5), 20)
        self.assertEqual(economy.cost_of(100, -1), 0)
        self.assertEqual(economy.cost_of(100, float("nan")), 0)
        self.assertEqual(economy.MAX_LOSS_SHARE, 0.2)

    def test_rounded_down_and_never_below_zero(self):
        n = gold1_numbers()
        self.assertEqual(n.loss_cost(119, "regular"), 5)               # 5.95 down
        self.assertEqual(n.loss_cost(19, "regular"), 0)
        self.assertEqual(n.loss_cost(-40, "boss"), 0)
        self.assertEqual(economy.cost_of(100, 0.2 * 0.75), 15)        # 14.999... would be 14 without the hair


class ThickSkinTests(unittest.TestCase):
    def test_thick_skin_takes_a_quarter_off_each_kind(self):
        n, lv = gold1_numbers(), levels.read()
        perk = lv.perks["thick_skin"]
        self.assertEqual(perk.effect, {"loss_cut": 0.25})
        self.assertEqual(perk.text, "A loss takes a quarter less gold.")
        s = _Perky(2000, ["thick_skin"])
        self.assertEqual({k: levels.loss_cost(s, n, k, lv) for k in economy.ENCOUNTERS},
                         {"regular": 75, "dungeon": 150, "boss": 300})
        self.assertEqual({k: levels.loss_cost(_Perky(2000), n, k, lv) for k in economy.ENCOUNTERS},
                         {"regular": 100, "dungeon": 200, "boss": 400})

    def test_loss_share_is_no_longer_a_perk_effect(self):
        self.assertNotIn("loss_share", levels.PERK_EFFECTS)
        with self.assertRaises(ValueError) as cm:
            levels.Perk("old", {"name": "Old", "text": "x", "effect": {"loss_share": 0.15}})
        self.assertIn("loss_share", str(cm.exception))
        with self.assertRaises(ValueError):
            levels.Perk("big", {"name": "Big", "text": "x", "effect": {"loss_cut": 1.5}})

    def test_the_clamp_holds_with_a_perk_and_a_settings_file(self):
        lv = levels.read()
        hacked = copy.deepcopy(lv.perks["thick_skin"].effect)
        lv.perks["thick_skin"].effect["loss_cut"] = -3.0              # a perk that would make a loss bigger: never past 20%
        try:
            s = _Perky(1000, ["thick_skin"])
            self.assertEqual(levels.loss_cost(s, gold1_numbers(), "boss", lv), 200)
            self.assertEqual(levels.loss_cost(s, gold1_numbers(0.2), "regular", lv), 200)   # a plain-number settings file
        finally:
            lv.perks["thick_skin"].effect.update(hacked)
        settings = gold1_numbers(0.2)
        settings.loss_shares = dict.fromkeys(economy.ENCOUNTERS, 0.6)  # a settings tool writing past the check
        self.assertEqual(levels.loss_cost(_Perky(1000, ["thick_skin"]), settings, "dungeon", lv), 150)   # 0.2 x 0.75
        self.assertEqual(rduel.loss_cost(_Perky(1000), settings, "dungeon"), 200)


class HeldTests(r4.Temp):
    """rpg.duel.finish over test_rpg4's made-up deck and enemy, with GOLD1's table."""

    def setUp(self):
        super().setUp()
        for data in (r4.ENEMY, r4.NO_BAND):
            with open(os.path.join(self.tmp, data["kind"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f)
        r4.shop._CANDIDATES.clear()

    def start(self, s, kind="elf_gang", encounter="regular", spot="m#7"):
        return rduel.start(s, kind, encounter, r4.POOL, r4.lists(), folder=self.tmp, now=5.0, spot=spot)

    def finish(self, s, result, numbers=None):
        return rduel.finish(s, result, numbers or gold1_numbers(), r4.world(), r4.POOL, r4.FakeTiers(), r4.lists(), folder=self.tmp)

    def test_two_losses_to_the_same_foe_add_up_and_one_win_gives_it_back_once(self):
        s = r4.started_save()
        s.gold = 1000
        self.start(s)
        out = self.finish(s, "lost")
        self.assertEqual((out["gold"], out["held_by"], out["held"]), (-50, "m#7", 50))
        self.start(s, spot="m#8")
        self.finish(s, "conceded")                                     # another foe: its own entry
        self.start(s)
        out = self.finish(s, "lost")
        self.assertEqual(s.gold, 1000 - 50 - 47 - 45)
        self.assertEqual(s.world["held_gold"], {"m#7": 95, "m#8": 47})
        self.assertEqual(out["held"], 95)
        self.start(s)
        before = s.gold
        out = self.finish(s, "won")
        self.assertEqual(out["won_back"], 95)
        self.assertEqual(s.gold - before, out["gold"])
        self.assertGreaterEqual(out["gold"], 95 + 20)                  # the win's own 20-40 on top
        self.assertEqual(s.world["held_gold"], {"m#8": 47})            # gone for that foe only
        self.start(s)
        out = self.finish(s, "won")
        self.assertNotIn("won_back", out)
        self.assertLessEqual(out["gold"], 40)                          # never given twice

    def test_the_last_entry_going_leaves_no_empty_table(self):
        s = r4.started_save()
        self.start(s)
        self.finish(s, "lost")
        self.start(s)
        self.finish(s, "won")
        self.assertNotIn("held_gold", s.world)

    def test_the_share_is_the_fight_s_kind(self):
        for kind, cost in (("regular", 50), ("dungeon", 100), ("boss", 200)):
            with self.subTest(kind=kind):
                s = r4.started_save()
                s.gold = 1000
                self.start(s, encounter=kind)
                self.assertEqual(self.finish(s, "lost")["gold"], -cost)
                self.assertEqual(s.world["held_gold"], {"m#7": cost})

    def test_won_back_gold_gets_no_perk_bonus(self):
        s = r4.started_save()
        s.gold = 1000
        s.progress["perks"] = ["bounty"]                               # Bounty Hunter: +10% of a win's own gold only
        s.world["held_gold"] = {"m#7": 100}
        rewards = copy.deepcopy(r4.NUMBERS["rewards"])
        rewards["regular"]["gold"] = [30, 30]
        self.start(s)
        out = self.finish(s, "won", r4.numbers(loss_gold_share=copy.deepcopy(TABLE), rewards=rewards))
        self.assertEqual((out["gold"], out["won_back"], s.gold), (33 + 100, 100, 1133))   # not (30 + 100) x 1.1 = 143

    def test_a_duel_with_no_spot_holds_nothing(self):
        s = r4.started_save()
        s.gold = 1000
        self.start(s, spot=None)
        out = self.finish(s, "lost")
        self.assertEqual((out["gold"], s.gold), (-50, 950))
        self.assertNotIn("held_by", out)
        self.assertNotIn("held_gold", s.world)

    def test_no_gold_to_lose_holds_nothing(self):
        s = r4.started_save()
        s.gold = 10                                                    # 5% of 10 is 0
        self.start(s)
        out = self.finish(s, "lost")
        self.assertEqual(out["gold"], 0)
        self.assertNotIn("held_gold", s.world)

    def test_a_draw_and_an_unfinished_duel_change_nothing(self):
        s = r4.started_save()
        s.world["held_gold"] = {"m#7": 30}
        for result in ("draw", "unfinished"):
            with self.subTest(result=result):
                self.start(s)
                gold = s.gold
                out = self.finish(s, result)
                self.assertEqual((s.gold, s.world["held_gold"]), (gold, {"m#7": 30}))
                self.assertNotIn("won_back", out)

    def test_it_survives_towns_inns_and_the_beaten_list(self):
        s = r4.started_save()
        s.gold = 1000
        s.world["inn"] = {"map": "green_town", "spawn": "inn"}
        self.start(s)
        self.finish(s, "lost")                                         # the inn clears the beaten list
        rduel.visit_town(s, "blue_town", r4.world())
        s.world["beaten"] = []
        back = sv.SaveGame.from_dict(json.loads(json.dumps(s.to_dict())), pool=r4.POOL)
        self.assertEqual(back.world["held_gold"], {"m#7": 50})

    def test_an_old_save_with_no_held_gold_loads_and_plays(self):
        s = r4.started_save()
        d = json.loads(json.dumps(s.to_dict()))
        d["world"].pop("held_gold", None)
        back = sv.SaveGame.from_dict(d, pool=r4.POOL)
        self.assertEqual(rduel.held_gold(back), {})
        self.assertEqual(rduel.held_by(back, "m#7"), 0)
        self.start(back)
        self.assertEqual(self.finish(back, "won").get("won_back"), None)
        self.start(back)
        self.finish(back, "lost")
        self.assertEqual(list(back.world["held_gold"]), ["m#7"])

    def test_a_damaged_entry_is_dropped_never_a_refused_save(self):
        s = r4.started_save()
        d = json.loads(json.dumps(s.to_dict()))
        d["world"]["held_gold"] = {"m#7": 30, "m#8": -4, "m#9": "lots", "m#10": True}
        back = sv.SaveGame.from_dict(d, pool=r4.POOL)
        self.assertEqual(back.world["held_gold"], {"m#7": 30})
        d["world"]["held_gold"] = ["not", "a", "table"]
        self.assertNotIn("held_gold", sv.SaveGame.from_dict(d, pool=r4.POOL).world)


class ScreenGoldTests(ScreenBase):
    """Through the Adventure window, by tap (rpg8's press clicks the button)."""

    def first_foe(self, f):
        return next(fo for fo in f.explorer.encounters() if fo.encounter == "regular")

    def duel(self, g, foe, won):
        self.meet(g, foe)
        self.press(g, "fight")
        g.vs = None
        frame(g, 2)
        r8.end_game(g, won=won)
        g.end_continue()
        frame(g)
        return g.modal

    def drawn_lines(self, g, colour=None):
        seen = []
        real = rpg_ui.text

        def text(surface, s, x, y, c, *a, **kw):
            r = real(surface, s, x, y, c, *a, **kw)
            seen.append((s, tuple(c)[:3], r))
            return r
        enc = getattr(g.modal, "encounter", None)
        if enc is not None:
            enc._frames = {}                                           # the encounter screen keeps its picture: draw it again
        with mock.patch.object(rpg_ui, "text", text):
            frame(g)
        return [(s, c, r) for s, c, r in seen if colour is None or c == tuple(colour)[:3]]

    def test_the_encounter_screen_says_the_new_loss_and_what_the_foe_holds(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        foe = self.first_foe(f)
        f.game.save.gold = 500
        enc = self.meet(g, foe)
        self.assertIn("Lose: 25 of your 500 gold, and back to Lantern Hollow.", enc.lines)
        self.assertFalse([ln for ln in enc.lines if ln.startswith("Holds")])
        f.sub, f.encounter = None, None
        f.game.save.world["held_gold"] = {foe.spot: 40}
        enc = self.meet(g, foe)
        self.assertEqual(enc.lines[enc.lines.index("Lose: 25 of your 500 gold, and back to Lantern Hollow.") + 1],
                         "Holds 40 of your gold - win it back.")
        gold = " ".join(s for s, _c, _r in self.drawn_lines(g, gfx.DB_GOLD))
        self.assertIn("Holds 40 of your gold - win it back.", gold)

    def test_after_a_loss_the_foe_has_it_and_after_a_win_it_comes_back(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        foe = self.first_foe(f)
        name = adv.foe_name(foe)
        f.game.save.gold = 400
        f = self.duel(g, foe, won=False)
        self.assertEqual(f.result[1][1], f"You lost 20 gold - the {name} has it now. Beat them to win it back.")
        self.assertEqual(f.game.save.world["held_gold"], {foe.spot: 20})
        self.assertEqual(r8.on_disk(self.folder)["world"]["held_gold"], {foe.spot: 20})
        key(g, pygame.K_RETURN)
        f = self.on_map(g, "ashfen_outskirts")
        foe = next(fo for fo in f.explorer.encounters() if fo.spot == foe.spot)
        f.game.save.world["first_win_day"] = 10 ** 6        # Round PROG5 (expected change): the day's first win paid already
        f = self.duel(g, foe, won=True)
        lines = f.result[1]
        gained = f.results.info["gold_change"]
        self.assertGreaterEqual(gained, 20 + 20)
        self.assertEqual(lines[0], f"You beat the {name}: +{gained} gold.")
        self.assertEqual(lines[1], f"20 of it won back from the {name}.")
        self.assertNotIn("held_gold", r8.on_disk(self.folder)["world"])

    def test_the_map_shows_a_coin_by_a_foe_that_holds_your_gold(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts")
        foe = self.first_foe(f)
        view = f.views[f.explorer.area.name]
        self.assertEqual(view.coin_rects, {})
        f.game.save.world["held_gold"] = {foe.spot: 12}
        frame(g)
        self.assertEqual(set(view.coin_rects), {foe.spot})
        coin = view.coin_rects[foe.spot]
        x0, y0, x1, y1 = foe.box
        cam = f.camera
        fx, fy = x1 - cam.x, y0 - cam.y                                 # the canvas is the map at zoom 1 from the camera
        self.assertLess(abs(coin.centerx - fx), f.explorer.area.tw)
        self.assertLess(abs(coin.centery - fy), f.explorer.area.th)

    def test_every_line_fits_at_every_size(self):
        r8.new_game(None)
        for size, scale in GOLD1_SIZES:
            with self.subTest(size=size, scale=scale):
                g = self.gui(size=size, scale=scale)
                self.open(g, slot=1)
                f = self.on_map(g, "ashfen_outskirts")
                foe = self.first_foe(f)
                f.game.save.world["held_gold"] = {foe.spot: 40}
                f.game.save.gold = 500
                self.meet(g, foe)
                held = [(s, r) for s, _c, r in self.drawn_lines(g, gfx.DB_GOLD) if "Holds" in s or "win it back" in s]
                self.assertTrue(held)
                canvas = pygame.Rect(0, 0, 640, 480)
                for s, r in held:
                    self.assertTrue(canvas.contains(r), (s, r))
                f.sub, f.encounter = None, None
                name = adv.foe_name(foe)
                for out in ({"result": "lost", "gold": -25, "cards": [], "moved_to": "lantern_hollow", "held_by": foe.spot,
                             "held": 65, "enemy": name},
                            {"result": "won", "gold": 77, "won_back": 40, "cards": [], "enemy": name}):
                    lines = adv.outcome_lines(out, f.game.world)
                    info = progress.results(dict(out, new=[]), lines, f.game.save.gold, f.game.tiers())
                    f.results = rpg_results.ResultsScreen(info, time.monotonic() - 60, pool=f.game.pool)
                    f.result, f.sub = ("Defeat" if out["result"] == "lost" else "Victory", info["lines"], rs.GREEN), "result"
                    drawn = self.drawn_lines(g)
                    whole = g.screen.get_rect()
                    self.assertTrue(whole.contains(f.results.rect))
                    want = lines[1]
                    pieces = [(s, r) for s, _c, r in drawn if s and s in want]
                    self.assertEqual(" ".join(s for s, _r in pieces), want, [s for s, *_ in drawn])
                    for s, r in pieces:
                        self.assertTrue(f.results.rect.contains(r), (s, r, f.results.rect))
                    f.sub, f.results = None, None
                self.close(g)


from rpg import progress  # noqa: E402  (after the classes: test_rpg8 imports first)
import rpg_screens as rs  # noqa: E402,F811
from tests.test_forge_table import frame  # noqa: E402,F811
