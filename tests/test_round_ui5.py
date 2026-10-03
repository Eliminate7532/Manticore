# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UI5: the opponents of a 3-4 player pod (alpha screenshot 19: tiny battlefield cards, a command zone reading "C...").
Rebuilt as patch 33, with the settings switches' ON/OFF fit (SwitchFitTests)."""
import copy
import os
import re
import sys
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_log, load_state
from tests.test_forge_table import frame

SIZES = (((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1366, 768), 1.0), ((1366, 768), 1.5), ((1280, 720), 1.25),
         ((2560, 1440), 2.0))
NAMES = ["Sol Ring", "Arcane Signet", "Mana Vault", "Rhystic Study", "Mystic Remora", "Faerie Mastermind", "Grim Monolith",
         "Basalt Monolith", "Birds of Paradise", "Llanowar Elves", "Consecrated Sphinx", "Kinnan, Bonder Prodigy", "Chrome Mox"]
LANDS = ["Forest", "Island", "Breeding Pool", "Misty Rainforest", "Tropical Island", "Yavimaya Coast", "Gemstone Caverns",
         "Otawara, Soaring City"]


def pod_state(players, boards=None, state="main1_lands"):
    """`state` with `players` seats (copies of its first opponent, fresh ids); boards: [(nonlands, lands)] per opponent."""
    st = copy.deepcopy(load_state(state))
    base = [p for p in st["players"] if p["id"] != st["me"]][0]
    st["players"] = [p for p in st["players"] if p["id"] == st["me"]]
    nid = 7000
    for k in range(players - 1):
        p = copy.deepcopy(base)
        p["id"], p["name"] = 20 + k, f"AI {k + 1}"
        for z in p["zones"].values():
            for c in z:
                c["id"] += 1000 * (k + 1)
        if boards:
            n, l = boards[k]
            bf = []
            for i, name in enumerate(NAMES[:n] + LANDS[:l]):
                bf.append({"id": nid, "name": name, "isLand": i >= n, "isCreature": False, "tapped": nid % 3 == 0,
                           "controller": p["id"], "owner": p["id"]})
                nid += 1
            p["zones"]["battlefield"] = bf
        st["players"].append(p)
    return st


def table(st, size, scale):
    gui = ft.ForgeTable(FakeSession(st, load_log()), StubStore(), settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.animations = False
    frame(gui, 2)
    return gui


def rects_of(gui, player):
    ids = [c["id"] for c in player["zones"]["battlefield"]]
    return [gui.card_rects[i] for i in ids if i in gui.card_rects]


class PodBattlefieldTests(unittest.TestCase):
    def test_a_small_board_gets_one_row_of_bigger_cards(self):
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                gui = table(pod_state(4, [(2, 2)] * 3), size, scale)
                for opp, rect in zip(gui.session.opponents(), gui.L.opp_rects):
                    rs = rects_of(gui, opp)
                    self.assertEqual(len(rs), 4)
                    self.assertEqual(len({r.centery for r in rs}), 1, rs)              # one row (tapped cards lie on its middle)
                    tall = max(r.h for r in rs)
                    self.assertGreaterEqual(tall, int(gui.L.opp_row_h * 1.35), (tall, gui.L.opp_row_h))
                    self.assertLessEqual(tall, gui.L.my_row_h)                          # never bigger than my own cards
                    for r in rs:
                        self.assertTrue(rect.contains(r), (r, rect))                    # inside the opponent's row

    def test_a_crowded_board_keeps_two_rows(self):
        gui = table(pod_state(4, [(13, 8), (6, 4), (2, 2)]), (1920, 1080), 1.0)
        crowded = gui.session.opponents()[0]
        rs = rects_of(gui, crowded)
        self.assertEqual(len({r.centery for r in rs}), 2)                              # nonland permanents above the lands
        self.assertEqual(max(r.h for r in rs), gui.L.opp_row_h)
        lands = [gui.card_rects[c["id"]] for c in crowded["zones"]["battlefield"] if c["isLand"]]
        others = [gui.card_rects[c["id"]] for c in crowded["zones"]["battlefield"] if not c["isLand"]]
        self.assertGreater(min(r.centery for r in lands), max(r.centery for r in others))

    def test_two_players_are_unchanged(self):
        gui = table(load_state("main1_lands"), (1920, 1080), 1.0)
        opp = gui.session.opponents()[0]
        untapped = [gui.card_rects[c["id"]] for c in opp["zones"]["battlefield"]
                    if not c.get("tapped") and c["id"] in gui.card_rects]
        self.assertTrue(untapped)
        self.assertTrue(all(r.h == gui.L.opp_row_h for r in untapped), (untapped, gui.L.opp_row_h))

    def test_one_row_height(self):
        gui = table(pod_state(4), (1920, 1080), 1.0)
        rows = gui.build_slots(gui.session.opponents()[0])
        slots = rows[0] + rows[1]
        self.assertIsNone(gui.one_row_height([], 1000, 40, 100))
        h = gui.one_row_height(slots, 2000, 40, 100)
        self.assertEqual(h, 100)                                                       # plenty of room: the full height
        self.assertIsNone(gui.one_row_height(slots, 60, 40, 100))                      # no room: two rows
        h = gui.one_row_height(slots, 400, 40, 100)
        widths, _m, gap = gui.row_widths(slots, h)
        self.assertLessEqual(sum(widths) + gap * (len(widths) - 1), 400)


class PodCommandZoneTests(unittest.TestCase):
    def test_the_title_is_readable_and_the_frame_stays_in_its_row(self):
        for size, scale in SIZES:
            for players in (3, 4):
                with self.subTest(size=size, scale=scale, players=players):
                    gui = table(pod_state(players), size, scale)
                    tiny = gui.font("tiny", True)
                    for opp, rect in zip(gui.session.opponents(), gui.L.opp_rects):
                        if gui.commanders_on_board(opp):
                            continue
                        info_right = rect.x + 2 + gui.L.opp_info_w
                        frame_ = gui.cmd_frame_for(opp, info_right + gui.L.pad, rect.y + 2, 30)
                        self.assertGreaterEqual(frame_.w - 8, tiny.size("CMD")[0])    # "CMD" fits, never "C..."
                    # the drawn frames: none may run into the next opponent's row (200% text did, by ~30 px)
                    frames_ = [r for r, kind in self.frames(gui)]
                    for f, rect in zip(frames_, gui.L.opp_rects):
                        self.assertLessEqual(f.bottom, rect.bottom, (f, rect))

    def frames(self, gui, with_ch=False):
        out = []
        real = ft.ForgeTable.draw_command_zone

        def spy(self_, player, frame_, ch, compact=False):
            out.append((pygame.Rect(frame_), compact, ch) if with_ch else (pygame.Rect(frame_), compact))
            return real(self_, player, frame_, ch, compact)
        ft.ForgeTable.draw_command_zone = spy
        try:
            frame(gui, 1)
        finally:
            ft.ForgeTable.draw_command_zone = real
        return out[:len(gui.session.opponents())]

    def test_the_tax_tag_stays_inside_the_frame(self):
        """A real 4-player game at 200% (3 Oct): "Tax +2" was wider than the narrow pod frame and stuck out of both sides, over a
        card it nearly covered. A compact frame says "CMD +2" in its title instead; every tax is drawn inside its frame."""
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                st = pod_state(4)
                for p in st["players"]:
                    if p["id"] != st["me"]:                       # Kinnan back in the command zone after one cast
                        kinnan = next(c for c in p["zones"]["battlefield"] if c["name"].startswith("Kinnan"))
                        p["zones"]["battlefield"].remove(kinnan)
                        p["zones"]["command"] = [kinnan]
                        p["commanders"], p["commanderCasts"] = [kinnan["id"]], {str(kinnan["id"]): 1}
                drawn, real = [], ft.draw_text

                def spy(scr, text, *a, **k):
                    r = real(scr, text, *a, **k)
                    if re.match(r"^(Tax |CMD |CMDR |COMMANDER )?\+\d+$", str(text)):
                        drawn.append(pygame.Rect(r))
                    return r
                with mock.patch.object(ft, "draw_text", spy):
                    gui = table(st, size, scale)
                frames_ = PodCommandZoneTests.frames(self, gui)
                self.assertEqual(len(drawn), 2 * len(frames_))           # (two frames drawn: table() and frames())
                for t in drawn:                                          # the pill is the words + 5 px each side
                    self.assertTrue(any(f.contains(t.inflate(10, 0)) for f, _c in frames_), (t, frames_))
                if all(c for _f, c in frames_):
                    self.assertTrue(all(t.y < f.y + gui.cmd_head_h(True) for t, (f, _c) in zip(drawn, frames_ * 2)))   # in the title

    def test_a_short_row_uses_the_compact_title(self):
        for players, scale, compact in ((4, 2.0, True), (4, 1.0, True), (3, 1.0, False)):
            with self.subTest(players=players, scale=scale):
                gui = table(pod_state(players), (1920, 1080), scale)
                kinds = [c for _r, c in PodCommandZoneTests.frames(self, gui)]
                self.assertEqual(kinds, [compact] * (players - 1))

    def test_the_commander_card_gets_the_rows_height(self):
        """A real 4-player game at 1080p (3 Oct): the two-line title left the card 53 px of a 117 px row, half of it under its
        "Tax +2" tag (before UI5: 46 px). Now 76 px there; at every size never less than the battlefield row's old height
        and at least 40% of the row (the title takes the rest at 200%)."""
        for size, scale in SIZES:
            for players in (3, 4):
                with self.subTest(size=size, scale=scale, players=players):
                    gui = table(pod_state(players), size, scale)
                    for (_f, _c, ch), rect in zip(PodCommandZoneTests.frames(self, gui, True), gui.L.opp_rects):
                        self.assertGreaterEqual(ch, max(gui.L.opp_row_h, int(rect.h * 0.4)), (ch, rect.h))
        gui = table(pod_state(4), (1920, 1080), 1.0)
        self.assertGreaterEqual(min(ch for _f, _c, ch in PodCommandZoneTests.frames(self, gui, True)), 70)


class PodPanelTests(unittest.TestCase):
    def test_commander_damage_in_a_pod_panel_is_a_chip_not_a_cut_line(self):
        """A real 4-player game at 1080p (3 Oct): "Cmdr dmg: Adeline 5/21" was drawn under a pod panel's piles, its top half
        showing above the panel's edge. A short panel gets a "Cmdr 5" chip on its hand row instead."""
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                st = pod_state(4)
                opp = [p for p in st["players"] if p["id"] != st["me"]][1]
                opp["commanderDamage"] = {"7001": 5, "7002": 16}
                drawn, real = [], ft.draw_text

                def spy(scr, text, *a, **k):
                    drawn.append(text)
                    return real(scr, text, *a, **k)
                with mock.patch.object(ft, "draw_text", spy):
                    gui = table(st, size, scale)
                self.assertFalse([t for t in drawn if str(t).startswith("Cmdr dmg")], "a line that doesn't fit the panel")
                chips = [t for t in drawn if str(t).startswith("Cmdr ")]
                self.assertIn("Cmdr 16", chips)                    # the most damage always shows (at big text: on the name row)

    def test_a_1v1_panel_keeps_its_commander_damage_lines(self):
        st = load_state("main1_lands")
        opp = [p for p in st["players"] if p["id"] != st["me"]][0]
        opp["commanderDamage"] = {"7001": 5}
        drawn, real = [], ft.draw_text

        def spy(scr, text, *a, **k):
            drawn.append(text)
            return real(scr, text, *a, **k)
        with mock.patch.object(ft, "draw_text", spy):
            table(st, (1920, 1080), 1.0)
        self.assertTrue([t for t in drawn if str(t).startswith("Cmdr dmg: ")], drawn[:40])

    def test_the_piles_stay_inside_a_pod_panel(self):
        """A real 4-player game at 1080p (3 Oct): "Library 86  Graveyard 2" filled the panel's second row and "Exile 0" wrapped
        onto a third, under the panel's edge. A pod panel has one row for the piles: short labels when the long ones don't fit."""
        for size, scale in SIZES:
            with self.subTest(size=size, scale=scale):
                st = pod_state(4)
                for k, p in enumerate(st["players"]):
                    p["libraryCount"] = 86 + k
                    p["name"] = p["name"] if p["id"] == st["me"] else f"AI {k} (D{k})"
                    p["zones"]["graveyard"] = [{"id": 8000 + 10 * k + i, "name": "Forest", "isLand": True} for i in range(2 + 5 * k)]
                    p["zones"]["exile"] = [{"id": 8900 + k, "name": "Island", "isLand": True}]        # (an empty pile has no hit)
                gui = table(st, size, scale)
                for opp in gui.session.opponents():
                    panel = gui.panel_rects[opp["id"]]
                    piles = [r for r, kind, d in gui.hits if kind in ("library", "zone") and d.get("player") == opp["id"]]
                    self.assertEqual(len(piles), 3, piles)
                    for r in piles:
                        self.assertTrue(panel.contains(r), (r, panel))


class SwitchFitTests(unittest.TestCase):
    """Patch 33 (with UI5): a switch's ON/OFF stays inside its pill. At 200% text in a 1080-high window the settings pop-up's rows
    are squeezed, and "OFF" ran past the pill's edge (in Alegreya before round 32, a little further in AvQest after it)."""

    def test_on_and_off_stay_inside_the_switch(self):
        import forge_settings as fset
        from tests.test_forge_table import click, point_for
        for size, scale in SIZES + (((4096, 2160), 1.75), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = table(load_state("main1_start"), size, scale)
                drawn, real = [], fset.draw_text

                def spy(scr, text, *a, **k):
                    r = real(scr, text, *a, **k)
                    if text in ("ON", "OFF"):
                        drawn.append(pygame.Rect(r))
                    return r
                click(gui, point_for(gui, "button", name="settings"))
                with mock.patch.object(fset, "draw_text", spy):
                    frame(gui, 1)
                self.assertGreaterEqual(len(drawn), 8)
                for t in drawn:
                    row = next(r for r, _n in gui.overlay.buttons if r.collidepoint(t.center))
                    ph = int(row.h * 0.56)
                    pill = pygame.Rect(0, 0, int(ph * 2.5), ph)
                    pill.midright = (row.right - 12, row.centery)
                    self.assertGreaterEqual(t.left, pill.left, (t, pill))
                    self.assertLessEqual(t.right, pill.right, (t, pill))
                    self.assertLessEqual(t.h, ph + 4, (t, pill))


if __name__ == "__main__":
    unittest.main()
