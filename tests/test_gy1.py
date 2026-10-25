# SPDX-License-Identifier: GPL-3.0-or-later
"""Round GY1 (24 Oct 2026): playing a card from a graveyard or exile pile.

Karl's F8 report of 23 Oct (Brawl, turn 12, a Surface Pro 11 with a stylus): "Emry doesnt let me cast the cards i select from
the graveyard". Emry, Lurker of the Loch's ability had resolved on Frogmite; the snapshot marked Frogmite "weak" (Forge: you
can cast it), but no click on Frogmite ever reached Forge. Forge and the bridge were fine (LiveTests: a click on the card casts
it). The table had two gaps:
  1. nothing on the table said a pile holds a card you can play: the graveyard pile shows its top card (Mishra's Bauble), never
     glowed, and the prompt's "(glowing cards)" pointed at nothing;
  2. the graveyard window kept the cards as they were when it opened, so one opened before the card became playable never showed
     it glowing and a click on it did nothing.
Now a pile (or a pod panel's chip) holding a playable or choosable card glows like a playable card, the window says "Click a
glowing card to play it." and follows the game while it is open.

  PileGlowTests     the recorded state: Karl's graveyard pile glows, the AI's does not; a pod panel's chip glows too
  ViewerTests       the hint, a tap casts, a card that can't be played sends nothing, the window follows the game, it fits
  LiveTests         real Forge: Emry -> Frogmite through the table, the window open while the ability resolves; Think Twice's
                    flashback by a direct click; a card exiled by Light Up the Stage played from the exile pile
"""
import copy
import json
import os
import sys
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pygame

import forge_dialogs as dlg
import forge_table as ft
import gfx
import tests.live as live
from tests.forge_fake import FakeSession, StubStore
from tests.test_forge_table import click, frame, key, move

FIXTURE = os.path.join(BASE, "tests", "fixtures", "gy1", "state.json")      # the last snapshot of Karl's report, 23 Oct 21:52
KINNAN = os.path.join(BASE, "tests", "fixtures", "decks", "kinnan_nbc_moxfield_export.txt")
FROGMITE = 92
BAUBLE = 60


def recorded():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def nothing_playable(st):
    """The same board a moment earlier, while Emry's ability was still on the stack: nothing in the graveyard can be cast yet."""
    st = copy.deepcopy(st)
    for p in st["players"]:
        for cards in p["zones"].values():
            for c in cards:
                c.pop("weak", None)
                c["selectable"] = False
    return st


def make(st, size=(1360, 840), scale=1.0):
    gui = ft.ForgeTable(FakeSession(st), StubStore(), settings_path=None, window_size=size)
    gui.text_scale = scale
    frame(gui, 3)
    return gui


def pile_hit(gui, player_id, zone="graveyard"):
    for rect, kind, data in gui.hits:
        if kind == "zone" and data.get("player") == player_id and data.get("zone") == zone:
            return rect
    raise AssertionError(f"no {zone} pile for player {player_id} on screen")


def pile_edge(gui, player_id, zone="graveyard"):
    """The colour of a pile's left edge. A tile's tap area (its hit carries the top card) is the tile 3 px wider on each side and
    4 px higher, plus its label underneath (draw_zone_tile): a third of the way down the tile. A chip ("GY 8", a short panel or
    big text) is its own tap area: the middle of its left end."""
    for area, kind, data in gui.hits:
        if kind == "zone" and data.get("player") == player_id and data.get("zone") == zone:
            point = (area.x + 4, area.y + 4 + (area.h - 10) // 3) if "card" in data else (area.x + 1, area.centery)
            return tuple(gui.screen.get_at(point))[:3]
    raise AssertionError(f"no {zone} pile for player {player_id} on screen")


def open_pile(gui, player_id, zone="graveyard"):
    click(gui, pile_hit(gui, player_id, zone).center)
    frame(gui)
    return gui.modal


def tiles(modal):
    return {modal.items[i]["card"]["id"]: r for r, i in modal.tiles}


class PileGlowTests(unittest.TestCase):
    def test_the_graveyard_pile_glows_when_a_card_in_it_can_be_played(self):
        for size, scale in (((1360, 840), 1.0), ((2880, 1920), 1.5), ((1920, 1080), 1.0), ((1100, 700), 2.0)):
            with self.subTest(size=size, scale=scale):
                gui = make(recorded(), size, scale)
                self.assertEqual(pile_edge(gui, 0), tuple(gfx.YELLOW)[:3], "the pile has the yellow 'you can click this' edge")
                self.assertNotEqual(pile_edge(gui, 1), tuple(gfx.YELLOW)[:3], "the AI's graveyard holds nothing I can use")
                self.assertIn((0, "graveyard"), gui.glowing_piles)
                self.assertNotIn((1, "graveyard"), gui.glowing_piles)
                self.assertTrue(pile_hit(gui, 0).contains(gui.glowing_piles[(0, "graveyard")]), "the glow is on the pile you tap")

    def test_no_pile_glows_when_nothing_in_it_can_be_played(self):
        gui = make(nothing_playable(recorded()))
        self.assertNotEqual(pile_edge(gui, 0), tuple(gfx.YELLOW)[:3])
        self.assertEqual(gui.glowing_piles, {})

    def test_a_target_in_a_graveyard_glows_too(self):
        """Emry's own question ("Choose target artifact card in your graveyard"): Forge marks the targets selectable."""
        st = nothing_playable(recorded())
        me = next(p for p in st["players"] if p["id"] == 0)
        next(c for c in me["zones"]["graveyard"] if c["id"] == FROGMITE)["selectable"] = True
        gui = make(st)
        self.assertIn((0, "graveyard"), gui.glowing_piles)

    def test_a_pod_panels_chip_glows(self):
        """In a 3-4 player game an opponent's piles are chips ("GY 2"); a card there that can be chosen makes the chip glow."""
        st = recorded()
        base = [p for p in st["players"] if p["id"] != st["me"]][0]
        for k in (1, 2):
            p = copy.deepcopy(base)
            p["id"], p["name"] = 10 + k, f"AI {k + 1}"
            for cards in p["zones"].values():
                for c in cards:
                    c["id"] += 1000 * k
            st["players"].append(p)
        st["players"][1]["zones"]["graveyard"][0]["selectable"] = True
        gui = make(st, (1360, 840))
        chips = [d for _r, k, d in gui.hits if k == "zone" and d.get("player") == 1]
        self.assertTrue(chips and all("card" not in d for d in chips), "the opponent's piles are chips here")
        self.assertIn((1, "graveyard"), gui.glowing_piles)
        self.assertNotIn((11, "graveyard"), gui.glowing_piles)

    def test_the_tooltip_says_so(self):
        gui = make(recorded())
        move(gui, pile_hit(gui, 0).center)
        self.assertIn("glowing card", gui.tooltip_text())
        move(gui, pile_hit(gui, 1).center)
        self.assertEqual(gui.tooltip_text(), "Click to look inside the graveyard.")


class ViewerTests(unittest.TestCase):
    def test_the_window_says_a_glowing_card_can_be_played_and_a_tap_casts_it(self):
        gui = make(recorded(), (2880, 1920), 1.5)                  # Karl's window and text size
        m = open_pile(gui, 0)
        self.assertIsInstance(m, dlg.ChooseDialog)
        self.assertEqual(m.view_hint(), "Click a glowing card to play it.")
        _title, need, _sel = m.head_lines(gui, m.head_width(gui, m.panel_width(gui)))
        self.assertEqual(" ".join(need), "Click a glowing card to play it.")
        gui.session.sent.clear()
        click(gui, tiles(m)[FROGMITE].center)
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": FROGMITE}])
        self.assertIsNone(gui.modal, "the window closes once the card is played")

    def test_a_card_that_cannot_be_played_sends_nothing_and_close_still_closes(self):
        gui = make(recorded())
        m = open_pile(gui, 0)
        gui.session.sent.clear()
        click(gui, tiles(m)[BAUBLE].center)
        self.assertEqual(gui.session.sent, [])
        self.assertIs(gui.modal, m, "looking stays looking: the window stays open")
        key(gui, pygame.K_ESCAPE)
        self.assertIsNone(gui.modal)
        self.assertEqual(gui.session.sent, [])

    def test_an_opponents_graveyard_is_look_only(self):
        gui = make(recorded())
        m = open_pile(gui, 1)
        self.assertEqual(m.view_hint(), "")
        self.assertEqual(sorted(c["card"]["name"] for c in m.items), ["Fabled Passage", "Stroke of Midnight"])
        gui.session.sent.clear()
        for rect in tiles(m).values():
            click(gui, rect.center)
        self.assertEqual(gui.session.sent, [])

    def test_a_window_opened_before_the_card_became_playable_follows_the_game(self):
        """Emry's ability on the stack, the graveyard open; the ability resolves (Fast passes it for you); Frogmite now glows in the
        open window and a tap casts it. Before GY1 the window kept the old cards: no glow, and the tap did nothing."""
        gui = make(nothing_playable(recorded()))
        m = open_pile(gui, 0)
        gui.session.handle(recorded())
        frame(gui, 2)
        self.assertIs(gui.modal, m)
        gui.session.sent.clear()
        click(gui, tiles(m)[FROGMITE].center)
        self.assertEqual(gui.session.commands("card"), [{"c": "card", "id": FROGMITE}], "the tap on the glowing Frogmite casts it")
        self.assertIsNone(gui.modal)
        frog = next(it["card"] for it in m.items if it["card"]["id"] == FROGMITE)
        self.assertEqual(frog.get("weak"), 1)
        self.assertEqual(m.view_hint(), "Click a glowing card to play it.")

    def test_a_card_that_leaves_the_pile_leaves_the_window(self):
        gui = make(recorded())
        m = open_pile(gui, 0)
        st = recorded()
        me = next(p for p in st["players"] if p["id"] == 0)
        frog = next(c for c in me["zones"]["graveyard"] if c["id"] == FROGMITE)
        me["zones"]["graveyard"].remove(frog)
        me["zones"]["battlefield"].append(frog)
        gui.session.handle(st)
        frame(gui, 2)
        self.assertNotIn(FROGMITE, tiles(m))
        self.assertEqual(len(m.items), 7)

    def test_an_info_list_without_clicks_gets_no_hint(self):
        card = next(c for p in recorded()["players"] for c in p["zones"]["graveyard"] if c["id"] == FROGMITE)
        m = dlg.ChooseDialog("Revealed", [{"kind": "card", "card": card}], mode="view")
        self.assertEqual(m.view_hint(), "")

    def test_the_window_with_its_hint_fits(self):
        for size, scale in (((900, 600), 1.0), ((1100, 700), 2.0), ((1360, 840), 1.0), ((1920, 1080), 1.5), ((2880, 1920), 1.5),
                            ((4096, 1949), 1.75)):
            with self.subTest(size=size, scale=scale):
                gui = make(recorded(), size, scale)
                m = open_pile(gui, 0)
                window = pygame.Rect(0, 0, *size)
                for rect, _i in m.tiles:
                    if rect.colliderect(m.body):
                        self.assertTrue(window.contains(rect.clip(m.body)), (size, scale, rect))
                self.assertTrue(m.body.top > 0 and m.body.bottom <= size[1])
                self.assertIn(FROGMITE, tiles(m))


def prompt(s):
    return ((s.state or {}).get("prompt") or {}).get("message", "")


@unittest.skipUnless(live.live_enabled(), "needs Java and forge_runtime/")
class LiveTests(unittest.TestCase):
    """Real Forge. Each starts the engine with card_check's checker (dev set-up, the classic stops: I pass Emry's ability myself,
    as the Fast speed does for me in a real game)."""

    def checker(self):
        import card_check as cc
        chk = cc.Checker(KINNAN, say=lambda *a: None)
        chk.start()
        self.addCleanup(chk.stop)
        return chk

    def mine(self, s, zone):
        return {c["name"]: c for c in s.me()["zones"].get(zone, [])}

    def settle(self, s, until, seconds=25):
        """Pay with Forge's auto-pay and pass with my spell on the stack, until `until()`."""
        end = time.time() + seconds
        while time.time() < end:
            s.poll()
            m = prompt(s)
            if "Pay Mana" in m:
                s.ok()
                time.sleep(0.5)
            elif m.startswith("Priority") and s.state.get("stack"):
                s.ok()
                time.sleep(0.5)
            if until():
                return True
            time.sleep(0.05)
        return False

    def test_emry_on_frogmite_then_frogmite_cast_through_the_table(self):
        chk = self.checker()
        s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Brainstorm",
                 "humanbattlefield=Emry, Lurker of the Loch;Ornithopter;Ornithopter;Ornithopter;Ornithopter;Island;Island;Island",
                 "humangraveyard=Think Twice;Frogmite;Mishra's Bauble", "humanlibrary=Island;Island;Island;Island;Island;Island",
                 "aihand=", "ailibrary=Forest;Forest;Forest;Forest", "aibattlefield=", "removesummoningsickness=true"]
        self.assertTrue(chk.apply(lines, ["Brainstorm"]))
        chk.pump(1.0)
        gui = ft.ForgeTable(s, StubStore(), window_size=(1360, 840))
        me = s.state["me"]
        frame(gui, 2)
        emry = self.mine(s, "battlefield")["Emry, Lurker of the Loch"]
        s.click_card(emry["id"])
        end = time.time() + 15
        while time.time() < end and s.state.get("input") != "InputSelectTargets":
            frame(gui)
            time.sleep(0.05)
        self.assertEqual(s.state.get("input"), "InputSelectTargets")
        # the target is chosen in the graveyard window, by a tap, as Karl did
        frame(gui, 2)
        self.assertIn((me, "graveyard"), gui.glowing_piles)
        m = open_pile(gui, me)
        self.assertEqual(m.view_hint(), "Click a glowing card to choose it.")
        frog_id = self.mine(s, "graveyard")["Frogmite"]["id"]
        click(gui, tiles(m)[frog_id].center)
        end = time.time() + 15
        while time.time() < end and not (prompt(s).startswith("Priority") and s.state.get("stack")):
            frame(gui)
            time.sleep(0.05)
        self.assertTrue(s.state.get("stack"), "Emry's ability is on the stack")
        # the window opened while the ability is on the stack: Frogmite can't be cast yet (Think Twice, an instant, can)
        m = open_pile(gui, me)

        def frog_in_window():
            return next(it["card"] for it in m.items if it["card"]["id"] == frog_id)
        self.assertFalse(frog_in_window().get("weak"))
        s.ok()                                         # pass: the AI passes, the ability resolves behind the open window
        end = time.time() + 20
        while time.time() < end and not (prompt(s).startswith("Priority") and not s.state.get("stack")
                                         and self.mine(s, "graveyard")["Frogmite"].get("weak")):
            frame(gui)
            time.sleep(0.05)
        frame(gui, 2)
        self.assertIs(gui.modal, m)
        self.assertTrue(frog_in_window().get("weak"), "the open window follows the game: Frogmite glows there now")
        self.assertEqual(m.view_hint(), "Click a glowing card to play it.")
        key(gui, pygame.K_ESCAPE)
        frame(gui, 2)
        self.assertIn((me, "graveyard"), gui.glowing_piles, "the pile glows once the window is closed")
        m = open_pile(gui, me)
        click(gui, tiles(m)[frog_id].center)
        self.assertIsNone(gui.modal)
        self.assertTrue(self.settle(s, lambda: "Frogmite" in self.mine(s, "battlefield")), "Frogmite was never cast")
        self.assertNotIn("Frogmite", self.mine(s, "graveyard"))
        self.assertEqual(s.checks, [])

    def test_flashback_by_a_click_and_a_card_played_from_the_exile_pile(self):
        chk = self.checker()
        s = chk.s
        lines = ["humanlife=40", "ailife=40", "activeplayer=human", "activephase=MAIN1", "turn=3", "humanlandsplayed=0",
                 "humanhand=Light Up the Stage", "humanbattlefield=Island;Island;Island;Mountain;Mountain;Mountain",
                 "humangraveyard=Think Twice", "humanlibrary=Mountain;Lightning Bolt;Island;Island;Island;Island;Island",
                 "aihand=", "ailibrary=Forest;Forest;Forest;Forest", "aibattlefield=", "removesummoningsickness=true"]
        self.assertTrue(chk.apply(lines, ["Light Up the Stage"]))
        chk.pump(1.0)
        # Forge's own behaviour: a card click on Think Twice in the graveyard casts it with flashback
        s.click_card(self.mine(s, "graveyard")["Think Twice"]["id"])
        self.assertTrue(self.settle(s, lambda: "Think Twice" in self.mine(s, "exile") and not s.state.get("stack")),
                        "Think Twice was not cast with flashback")
        self.assertIn("Mountain", self.mine(s, "hand"))
        chk.pump(0.5)
        s.click_card(self.mine(s, "hand")["Light Up the Stage"]["id"])
        self.assertTrue(self.settle(s, lambda: "Island" in self.mine(s, "exile") and not s.state.get("stack")))
        end = time.time() + 10
        while time.time() < end and not self.mine(s, "exile")["Island"].get("weak"):
            s.poll()
            time.sleep(0.05)
        gui = ft.ForgeTable(s, StubStore(), window_size=(1360, 840))
        me = s.state["me"]
        frame(gui, 3)
        self.assertIn((me, "exile"), gui.glowing_piles)
        m = open_pile(gui, me, "exile")
        island = self.mine(s, "exile")["Island"]["id"]
        click(gui, tiles(m)[island].center)
        self.assertTrue(self.settle(s, lambda: s.me()["landsPlayed"] == 1, 10), "the Island was not played from exile")
        self.assertNotIn("Island", self.mine(s, "exile"))
        self.assertEqual(s.checks, [])


if __name__ == "__main__":
    unittest.main()
