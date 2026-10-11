# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UI7: Card size, built like Text size (spec: claude/SONNET_SPEC_NOTES_2026-10-08.md section C, Karl's note 5).

What it does in this first version: a battlefield whose cards fit in ONE row shows them up to card_scale x their two-row height
(never outside the battlefield); a fuller board keeps today's two rows; the hand and the command zone are not changed. Why not
more: at 100% every card picture already fills its region's height (the two battlefield rows fill the battlefield, the hand fills
its strip), so the spec's "larger wherever there's room" and "never outside its region, never smaller than at 100%" leave only
that. CLAUDE.md (Round UI7) lists the choices for Karl."""
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_settings as fset
import forge_table as ft
import tests.ui6_layout as u6
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import click, frame, key

COG_SIZES = (((900, 600), 1.0), ((900, 600), 2.0), ((1100, 700), 2.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0),
             ((1920, 1080), 2.0), ((4096, 1949), 1.75))


def table(state, size, scale=1.0, card=1.0, settings=None):
    st = copy.deepcopy(load_state(state)) if isinstance(state, str) else copy.deepcopy(state)
    gui = ft.ForgeTable(FakeSession(st, load_log()), StubStore(), settings_path=settings, window_size=size)
    gui.text_scale = scale
    gui.card_scale = card
    gui.animations = False
    frame(gui, 3)
    return gui


def board(gui, pid=None):
    """{card id: (its drawn rect, the card height of its slot)} for one player's battlefield (mine by default). The height is the
    slot's: a tapped card's drawn rect is turned sideways."""
    pid = gui.session.me()["id"] if pid is None else pid
    return {c["id"]: (gui.card_rects[c["id"]], s.rect.h) for row in gui.slots.get(pid, []) for s in row for c in s.cards
            if c["id"] in gui.card_rects}


def heights(gui, pid=None):
    return {h for _r, h in board(gui, pid).values()}


def my_board(gui):
    return {cid: r for cid, (r, _h) in board(gui).items()}


def crowded(n=18):
    """main1_lands with n more different creatures on my battlefield: too many for one row of bigger cards."""
    st = copy.deepcopy(load_state("main1_lands"))
    me = next(p for p in st["players"] if p["id"] == st["me"])
    proto = next(c for c in me["zones"]["battlefield"] if c.get("isCreature"))
    for k in range(n):
        c = copy.deepcopy(proto)
        c["id"], c["name"] = 5000 + k, f"Bear {k}"
        me["zones"]["battlefield"].append(c)
    return st


class SettingsTests(unittest.TestCase):
    def test_missing_or_unknown_is_100_percent(self):
        for value, want in ((None, 1.0), ("big", 1.0), (5.0, 1.0), (0.5, 1.0), (-1, 1.0), (float("nan"), 1.0), ([], 1.0),
                            (1.5, 1.5), ("1.75", 1.75), (1.3, 1.25), (2.0, 2.0), (1.0, 1.0)):
            with self.subTest(value=value):
                self.assertEqual(ft.card_scale_from(value), want)

    def test_it_is_saved_and_comes_back(self):
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"text_scale": 1.25}, f)
        gui = ft.ForgeTable(FakeSession(load_state("main1_lands"), load_log()), StubStore(), settings_path=path, window_size=(1360, 840))
        self.assertEqual(gui.card_scale, 1.0)                                    # missing
        gui.change_card_scale(1)
        gui.change_card_scale(1)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["card_scale"], 1.5)
        again = ft.ForgeTable(FakeSession(load_state("main1_lands"), load_log()), StubStore(), settings_path=path, window_size=(1360, 840))
        self.assertEqual((again.card_scale, again.text_scale), (1.5, 1.25))   # and text size is its own setting

    def test_the_cog_buttons_step_like_text_size_and_stop_at_the_ends(self):
        gui = table("main1_lands", (1360, 840))
        gui.overlay = fset.SettingsPopup()
        frame(gui)
        seen = []
        for _ in range(6):
            click(gui, dict((n, r) for r, n in gui.overlay.buttons)["cards_bigger"].center)
            seen.append(gui.card_scale)
        self.assertEqual(seen, [1.25, 1.5, 1.75, 2.0, 2.0, 2.0])
        self.assertIn("already maximum", gui.toast[0])
        for _ in range(5):
            click(gui, dict((n, r) for r, n in gui.overlay.buttons)["cards_smaller"].center)
        self.assertEqual(gui.card_scale, 1.0)
        self.assertEqual(gui.text_scale, 1.0)                                    # never touched
        self.assertIsInstance(gui.overlay, fset.SettingsPopup)                  # the pop-up stays open, as for text size

    def test_ctrl_plus_and_minus_are_card_size_plain_plus_is_still_text_size(self):
        gui = table("main1_lands", (1360, 840))
        key(gui, pygame.K_EQUALS, mod=pygame.KMOD_CTRL)
        self.assertEqual((gui.card_scale, gui.text_scale), (1.25, 1.0))
        key(gui, pygame.K_KP_PLUS, mod=pygame.KMOD_CTRL)
        self.assertEqual(gui.card_scale, 1.5)
        key(gui, pygame.K_MINUS, mod=pygame.KMOD_CTRL)
        self.assertEqual(gui.card_scale, 1.25)
        key(gui, pygame.K_EQUALS)
        self.assertEqual((gui.card_scale, gui.text_scale), (1.25, 1.25))
        key(gui, pygame.K_EQUALS, mod=pygame.KMOD_SHIFT)                        # '+' on a US keyboard: text size, as before
        self.assertEqual((gui.card_scale, gui.text_scale), (1.25, 1.5))
        self.assertEqual(gui.session.sent, [])                                   # nothing goes to Forge

    def test_help_and_readme_name_the_keys(self):
        self.assertTrue(any("With Ctrl, + / - is the card size" in t for _k, t in ft.HELP_LINES))
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.txt"), encoding="utf-8") as f:
            self.assertIn("bigger / smaller cards", f.read())


class LayoutTests(unittest.TestCase):
    def test_at_100_percent_the_table_is_exactly_todays(self):
        # UI6's recorded layouts (28 tables, from the program before UI6), drawn after a trip to 200% and back
        if u6.BASELINE is None:
            self.skipTest(f"no layout was recorded for {sys.platform!r}")
        with open(u6.BASELINE, encoding="utf-8") as f:
            base = json.load(f)
        for sname, state in u6.scenes().items():
            for size, scale in u6.SIZES:
                k = f"{sname}|{size[0]}x{size[1]}|{scale}"
                with self.subTest(layout=k):
                    gui = u6.make_table(copy.deepcopy(state), size, scale)
                    gui.card_scale = 2.0
                    u6.draw(gui)
                    gui.card_scale = 1.0
                    u6.draw(gui)
                    self.assertEqual(json.loads(json.dumps(u6.snapshot(gui))), base[k])

    def test_a_board_that_fits_one_row_gets_bigger_cards(self):
        small = table("main1_lands", (1920, 1080), 1.0, 1.0)
        for card in (1.25, 1.5, 2.0):
            with self.subTest(card=card):
                gui = table("main1_lands", (1920, 1080), 1.0, card)
                L = gui.L
                [h] = heights(gui)
                self.assertEqual(h, min(L.my_bf.h - 2 * L.pad, int(L.my_row_h * card)))
                self.assertGreater(h, max(heights(small)))
                opp = gui.session.opponents()[0]
                self.assertGreater(min(heights(gui, opp["id"])), L.opp_row_h)   # the opponent's small board grows too
                self.assertLessEqual(max(heights(gui, opp["id"])), int(L.opp_row_h * card))

    def test_a_full_board_keeps_todays_two_rows(self):
        st = crowded()
        base = my_board(table(st, (1360, 840), 1.0, 1.0))
        for card in (1.5, 2.0):
            with self.subTest(card=card):
                self.assertEqual(my_board(table(st, (1360, 840), 1.0, card)), base)

    def test_no_card_leaves_its_region_or_shrinks(self):
        scenes = dict(u6.scenes())
        scenes["crowded"] = crowded(9)
        for sname, state in scenes.items():
            for size, scale in u6.SIZES:
                base = table(state, size, scale, 1.0)
                for card in (1.5, 2.0):
                    with self.subTest(scene=sname, size=size, scale=scale, card=card):
                        gui = table(state, size, scale, card)
                        L = gui.L
                        me = gui.session.me()
                        regions = {me["id"]: L.my_bf}
                        for opp, r in zip(gui.session.opponents(), L.opp_rects):
                            regions[opp["id"]] = r
                        others = [L.bar, L.hand, L.right, L.my_info] + list(gui.panel_rects.values())
                        for pid, rows in gui.slots.items():
                            for row in rows:
                                for s in row:
                                    self.assertTrue(regions[pid].contains(s.rect), (pid, s.rect, regions[pid]))
                                    self.assertFalse(any(s.rect.colliderect(o) for o in others if o.w and o.h), (pid, s.rect))
                        for pid in gui.slots:
                            before = board(base, pid)
                            for cid, (_r, h) in board(gui, pid).items():
                                if cid in before:
                                    self.assertGreaterEqual(h, before[cid][1], cid)

    def test_four_players_the_pod_rows_grow_with_card_size(self):
        st = u6.pod_state(4)
        base = table(st, (1920, 1080), 1.0, 1.0)
        big = table(st, (1920, 1080), 1.0, 2.0)
        for opp, region in zip(big.session.opponents(), big.L.opp_rects):
            # UI5's one row at 100% is already as tall as a 4-player row allows at 1920x1080: never smaller, never taller than the row
            self.assertGreaterEqual(min(heights(big, opp["id"])), max(heights(base, opp["id"])))
            self.assertLessEqual(max(heights(big, opp["id"])), region.h - 2 * big.L.pad)
        # where the row has room - 3 players at 200% text, where UI5 capped them at my own (shorter) row - they grow: 110 -> 124 px
        three = u6.pod_state(3)
        a, b = table(three, (1920, 1080), 2.0, 1.0), table(three, (1920, 1080), 2.0, 2.0)
        opp = b.session.opponents()[0]
        self.assertGreater(max(heights(b, opp["id"])), max(heights(a, opp["id"])))

    def test_clicks_land_on_the_cards_as_drawn(self):
        gui = table("main1_lands", (1920, 1080), 1.0, 2.0)
        for cid, r in my_board(gui).items():
            kind, data = gui.hit_at(r.center)
            self.assertEqual(kind, "card")
            self.assertEqual(data["card"]["id"], cid)

    def test_the_hand_and_command_zone_are_not_changed_yet(self):
        a, b = table("main1_lands", (1360, 840), 1.0, 1.0), table("main1_lands", (1360, 840), 1.0, 2.0)
        me = a.session.me()
        mine = [c["id"] for c in me["zones"]["hand"]]
        self.assertTrue(mine)
        for cid in mine:
            self.assertEqual(a.card_rects[cid], b.card_rects[cid], cid)
        self.assertEqual((a.L.hand, a.L.cmd_rect, a.L.hand_ch), (b.L.hand, b.L.cmd_rect, b.L.hand_ch))


class CogTests(unittest.TestCase):
    def test_text_and_cards_share_one_row_and_fit_at_every_size(self):
        clipped = []
        real = fset.clip_text

        def spy(text, font, width):
            out = real(text, font, width)
            if text in ("Text", "Cards") and out != text:
                clipped.append(text)
            return out
        for size, scale in COG_SIZES:
            with self.subTest(size=size, scale=scale):
                gui = table("main1_lands", size, scale, 2.0)
                gui.overlay = fset.SettingsPopup()
                with mock.patch.object(fset, "clip_text", side_effect=spy):
                    frame(gui)
                pop = gui.overlay
                b = dict((n, r) for r, n in pop.buttons)
                row = [b["smaller"], b["bigger"], b["cards_smaller"], b["cards_bigger"]]
                self.assertEqual(len({r.y for r in row}), 1)                             # one row
                for r in row:
                    self.assertTrue(pop.rect.contains(r), r)
                self.assertTrue(all(row[i].right <= row[i + 1].x for i in range(3)))     # in order, none overlapping
                self.assertLess(row[1].right, pop.rect.centerx)                          # Text in the left half
                self.assertGreater(row[2].x, pop.rect.centerx)                           # Cards in the right half
                self.assertLess(row[0].bottom, b["bg_prev"].y)                           # the Table row comes next
        self.assertEqual(clipped, [])


if __name__ == "__main__":
    unittest.main()
