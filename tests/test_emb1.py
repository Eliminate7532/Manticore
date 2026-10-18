# SPDX-License-Identifier: GPL-3.0-or-later
"""Round EMB1 (15 Oct 2026): Turbo Cube's emblem on the table says what it does.

CD3's notes said the emblem "isn't drawn on the table" in a Draft game (no command-zone frame). Checked live on 15 Oct: it IS -
forge_status's badge chips (on every player's panel, from the command zone's non-commander cards) show "Turbo Cube Emblem" - but
its hover text was only "An emblem: it stays for the rest of the game.", because the snapshot sends no rules text for an effect
card. house_rules.EMBLEMS now gives the emblem's own words (HouseRules.java's Description$), and the chip's tooltip uses them.

  WordsTests    house_rules.emblem_text and the Java source agree
  BadgeTests    forge_status.badges_for: the house-rule words, Forge's own text first, other emblems unchanged
  TableTests    a Draft game (no command-zone frame), 1v1 and a pod: a chip on every panel, inside it, its tooltip the words
  LiveTests     the real engine (fb4d809) through the real table: both chips, with the words
"""
import copy
import json
import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import forge_client as fc
import forge_status as fstat
import forge_table as ft
import house_rules as hr
from tests import live
from tests.forge_fake import FakeSession, StubStore
from tests.test_forge_table import frame, load_log, load_state, move

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(HERE, "java_bridge", "src", "forge", "bridge", "HouseRules.java")
NAME = "Turbo Cube Emblem"
WORDS = "cost {2} less"


def emblem(cid, text=""):
    return {"id": cid, "name": NAME, "text": text, "type": ""}          # what the live snapshot sends (15 Oct: text "", type "")


def draft_state(players=2):
    """main1_lands as a Draft game: no commanders anywhere, a Turbo emblem in every player's command zone; `players` seats."""
    st = copy.deepcopy(load_state("main1_lands"))
    base = [p for p in st["players"] if p["id"] != st["me"]][0]
    me = [p for p in st["players"] if p["id"] == st["me"]][0]
    seats = [me]
    for k in range(players - 1):
        p = copy.deepcopy(base)
        p["id"], p["name"] = 20 + k, f"AI {k + 1}"
        for z in p["zones"].values():
            for c in z:
                c["id"] += 1000 * (k + 1)
        seats.append(p)
    for i, p in enumerate(seats):
        for z in p["zones"].values():
            for c in z:
                c["commander"] = False
        p["zones"]["command"] = [emblem(9000 + i)]
        p["commanders"] = []
    st["players"] = seats
    return st


def table(st, size, scale=1.0):
    session = FakeSession(st, load_log())
    session.fmt, session.rules = "draft", ("turbo",)
    gui = ft.ForgeTable(session, StubStore(), settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.animations = False
    frame(gui, 2)
    return gui


def badge_hits(gui):
    return [(r, d) for r, k, d in gui.hits if k == "badge"]


class WordsTests(unittest.TestCase):
    def test_the_emblem_words_by_its_card_name(self):
        for name in (NAME, "turbo cube emblem", "  Turbo   Cube Emblem "):
            words = hr.emblem_text(name)
            self.assertIn("Spells you cast and abilities you activate cost {2} less", words)
            self.assertIn("every player starts the game with this emblem", words)
        for other in ("", None, "The Monarch", "Emblem", "Turbo Cube"):
            self.assertEqual(hr.emblem_text(other), "")

    def test_the_words_are_the_bridge_s_own(self):
        """The name and the Description$ of the effect card HouseRules.java makes - so the chip can't drift from the engine."""
        with open(JAVA, encoding="utf-8") as f:
            src = f.read()
        name = re.search(r'TURBO_NAME\s*=\s*"([^"]+)"', src).group(1)
        desc = re.search(r'Description\$ ([^"]+)"', src).group(1)
        self.assertEqual(name.lower(), "turbo cube emblem")
        self.assertIn(name.lower(), hr.EMBLEMS)
        self.assertEqual(hr.EMBLEMS[name.lower()][0], desc)


class BadgeTests(unittest.TestCase):
    def test_a_turbo_emblem_with_no_text_gets_its_words(self):
        badges = fstat.badges_for([emblem(1)], [])
        self.assertEqual([(b.key, b.label) for b in badges], [("emblem", NAME)])
        self.assertIn(WORDS, badges[0].text)
        self.assertTrue(badges[0].text.startswith(NAME + ": "))

    def test_forge_s_own_text_still_comes_first(self):
        badges = fstat.badges_for([emblem(1, "Whatever Forge says.")], [])
        self.assertEqual(badges[0].text, NAME + ": Whatever Forge says.")

    def test_another_emblem_without_text_is_as_before(self):
        badges = fstat.badges_for([{"id": 2, "name": "Emblem - Kaya", "text": ""}], [])
        self.assertEqual(badges[0].text, "Emblem - Kaya: An emblem: it stays for the rest of the game.")
        badges = fstat.badges_for([{"id": 3, "name": "Some Effect", "text": ""}], [])
        self.assertEqual(badges[0].text, "Some Effect: An effect kept in the command zone.")


class TableTests(unittest.TestCase):
    SIZES = [((1360, 840), 1.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1100, 700), 2.0), ((4096, 2019), 1.75)]

    def check(self, players):
        for size, scale in self.SIZES:
            gui = table(draft_state(players), size, scale)
            with self.subTest(players=players, size=size, scale=scale):
                self.assertFalse(gui.has_command_zone())
                hits = badge_hits(gui)
                owners = sorted(d.get("player") for _, d in hits)
                self.assertEqual(owners, sorted(p["id"] for p in gui.state["players"]))      # one chip per player
                for r, d in hits:
                    panel = gui.panel_rects[d["player"]]
                    self.assertTrue(panel.contains(r), f"chip {r} outside its panel {panel}")
                    move(gui, r.center)
                    tip = gui.tooltip_text()
                    self.assertIn(NAME, tip)
                    self.assertIn(WORDS, tip)

    def test_a_1v1_draft_game(self):
        self.check(2)

    def test_a_pod_of_three_and_four(self):
        self.check(3)
        self.check(4)

    def test_no_chip_is_drawn_under_a_pile(self):
        """Pod panels whose piles share the hand row (Round UI2): the piles ran over the badge chips (4 players at 1360x840 showed
        "rb" of "Turb" beside "Ex 1"). The emblem in a Draft pod, and the monarch in a Commander pod, never under a pile chip."""
        for kind in ("emblem", "monarch"):
            for players in (3, 4):
                for size, scale in self.SIZES + [((1280, 720), 1.25), ((1366, 768), 1.5)]:
                    st = draft_state(players)
                    if kind == "monarch":
                        for p in st["players"]:
                            p["zones"]["command"] = [{"id": 8800 + p["id"], "name": "The Monarch", "text": "", "type": ""}]
                    gui = table(st, size, scale)
                    with self.subTest(kind=kind, players=players, size=size, scale=scale):
                        piles = [r for r, k, _d in gui.hits if k in ("zone", "library")]
                        chips = badge_hits(gui)
                        self.assertEqual(len(chips), players)
                        me = gui.state["me"]
                        for r, d in chips:
                            self.assertTrue(gui.panel_rects[d["player"]].contains(r))
                            self.assertEqual(gui.hit_at(r.center)[0], "badge", f"the chip {r} is under something")
                            if d["player"] == me:
                                continue             # my panel's piles are tiles below the chip row, not chips beside it
                            for p in piles:
                                self.assertFalse(r.colliderect(p), f"chip {r} under the pile {p}")

    def test_three_badges_fold_into_one_chip_when_the_name_row_is_short(self):
        """Monarch + initiative + the Ring in a 4-player pod: where the name row can't hold three chips they fold into one "Fx 3"
        chip whose tooltip has all three; nowhere is a chip under a pile or on another chip."""
        folded = 0
        for size, scale in self.SIZES + [((1280, 720), 1.25), ((1366, 768), 1.5)]:
            st = draft_state(4)
            for p in st["players"]:
                p["zones"]["command"] = [{"id": 8800 + p["id"], "name": "The Monarch", "text": ""},
                                         {"id": 8900 + p["id"], "name": "The Initiative", "text": ""},
                                         {"id": 8950 + p["id"], "name": "The Ring", "text": ""}]
            gui = table(st, size, scale)
            with self.subTest(size=size, scale=scale):
                me = gui.state["me"]
                piles = [r for r, k, _d in gui.hits if k in ("zone", "library")]
                for pid in (20, 21, 22):
                    chips = [(r, d) for r, d in badge_hits(gui) if d["player"] == pid]
                    self.assertTrue(chips)
                    for i, (r, _d) in enumerate(chips):
                        self.assertTrue(gui.panel_rects[pid].contains(r))
                        self.assertFalse(any(r.colliderect(p) for p in piles), f"{r} under a pile")
                        self.assertFalse(any(r.colliderect(o) for o, _ in chips[i + 1:]), f"{r} on another chip")
                    text = " ".join(d["text"] for _r, d in chips)
                    for word in ("monarch", "initiative", "Ring"):
                        self.assertIn(word, text)
                    if len(chips) == 1:
                        folded += 1
                        move(gui, chips[0][0].center)
                        self.assertIn("initiative", gui.tooltip_text())
        self.assertGreater(folded, 0, "no size folded the chips - this test proves nothing about the fold")

    def test_a_commander_damage_chip_keeps_clear_of_badges_on_the_name_row(self):
        """Round UI5 puts one "Cmdr N" chip between the name and the life when the piles fill the hand row (big text); badges
        moved there (EMB1) sit at its right end, so the Cmdr chip goes left of them, never on top."""
        for size, scale in (((1920, 1080), 2.0), ((1100, 700), 2.0), ((1366, 768), 1.5), ((1280, 720), 1.25)):
            st = draft_state(4)
            for p in st["players"]:
                p["zones"]["command"] = [{"id": 8800 + p["id"], "name": "The Monarch", "text": "", "type": ""}]
                p["commanderDamage"] = {"777": 9}
            drawn = []
            real = ft.draw_text

            def spy(surf, text, x, y, font, *a, **k):
                r = real(surf, text, x, y, font, *a, **k)
                if isinstance(text, str) and re.fullmatch(r"Cmdr \d+", text):      # the chip, not my panel's "Cmdr dmg:" line
                    drawn.append(r)
                return r
            session = FakeSession(st, load_log())
            session.fmt = "commander"
            with mock.patch.object(ft, "draw_text", spy):
                gui = ft.ForgeTable(session, StubStore(), settings_path=None, window_size=size)
                gui.text_scale = scale
                gui.animations = False
                frame(gui, 2)
                drawn.clear()
                frame(gui, 1)
            with self.subTest(size=size, scale=scale):
                chips = [r for r, d in badge_hits(gui) if d["player"] != gui.state["me"]]
                self.assertEqual(len(chips), 3)
                self.assertEqual(len(drawn), 3, "one 'Cmdr 9' chip per opponent on the name row")
                for c in drawn:
                    self.assertIsInstance(c, pygame.Rect)
                    for b in chips:
                        self.assertFalse(c.colliderect(b), f"Cmdr text {c} on the badge {b}")


def named_pod(names, command, damage=None):
    """draft_state(4) with the three opponents named `names`, each with `command` (a list of effect cards made per player) and,
    when given, commander damage taken."""
    st = draft_state(4)
    for p in st["players"]:
        if p["id"] != st["me"]:
            p["name"] = names[p["id"] - 20]
        p["zones"]["command"] = [dict(c, id=c["id"] + p["id"]) for c in command]
        if damage:
            p["commanderDamage"] = {"777": damage}
    return st


def commander_table(st, size, scale):
    """A Commander table over `st` (commander damage counts there), drawn; returns (gui, the "Cmdr N" chip texts drawn)."""
    drawn = []
    real = ft.draw_text

    def spy(surf, text, x, y, font, *a, **k):
        r = real(surf, text, x, y, font, *a, **k)
        if isinstance(text, str) and re.fullmatch(r"Cmdr \d+", text):
            drawn.append(r)
        return r
    session = FakeSession(st, load_log())
    session.fmt = "commander"
    with mock.patch.object(ft, "draw_text", spy):
        gui = ft.ForgeTable(session, StubStore(), settings_path=None, window_size=size)
        gui.text_scale = scale
        gui.animations = False
        frame(gui, 2)
        drawn.clear()
        frame(gui, 1)
    return gui, drawn


MONARCH = [{"id": 8800, "name": "The Monarch", "text": "", "type": ""}]
BIG_TEXT = (((1920, 1080), 2.0), ((1100, 700), 2.0), ((1366, 768), 1.5), ((1280, 720), 1.25))


class ReviewTests(unittest.TestCase):
    """What the independent review of 15 Oct found in EMB1's first version (each red on it)."""

    def test_moved_badges_never_cost_a_commander_damage_chip(self):
        """Opponents named like the bridge names them ("AI 1 (Kinnan)"), the Monarch, 18 commander damage: the name row held a
        "Cmdr 18" chip before EMB1; the moved Monarch chip took its room and no Cmdr chip was drawn at all. Now a panel draws as
        many Cmdr chips with its badges as without them."""
        seen = 0
        for size, scale in BIG_TEXT:
            for n in (4, 6, 8, 10):                                     # names of 11-17 characters ("AI 1 (Kinnan)" is 13)
                names = [f"AI {k} (" + "Abcdefghijklm"[:n] + ")" for k in (1, 2, 3)]
                _g, without = commander_table(named_pod(names, [], damage=18), size, scale)
                gui, with_badges = commander_table(named_pod(names, MONARCH, damage=18), size, scale)
                seen += len(without)
                with self.subTest(size=size, scale=scale, name=names[0]):
                    self.assertEqual(len(with_badges), len(without), "a badge on the name row took the Cmdr chip's place")
                    chips = [r for r, d in badge_hits(gui) if d["player"] != gui.state["me"]]
                    for c in with_badges:
                        for b in chips:
                            self.assertFalse(c.colliderect(b), f"Cmdr chip {c} on the badge {b}")
        self.assertGreater(seen, 0, "no size drew a Cmdr chip - this test proves nothing")

    def test_a_long_name_never_leaves_a_sliver_of_a_chip(self):
        """A long opponent name left the moved chips 1-9 px wide, or negative (hover found the panel, not the chip). A chip is
        whole or it isn't moved."""
        for size, scale in BIG_TEXT + (((1360, 840), 1.0),):
            for name in ("AI 1 (Point It at Their Face (Red Aggro))", "AI 1 (Kinnan, Bonder Prodigy of the Simic) 20",
                         "Bartholomew the Exceedingly Verbose Online Friend"):          # no "(": the whole name is its floor
                for command in (MONARCH, [{"id": 8800, "name": NAME, "text": "", "type": ""}]):
                    st = named_pod([name, name.replace("AI 1", "AI 2"), name.replace("AI 1", "AI 3")], command)
                    st["players"][1]["priority"] = True
                    gui = table(st, size, scale)
                    piles = [r for r, k, _d in gui.hits if k in ("zone", "library")]
                    with self.subTest(size=size, scale=scale, name=name, badge=command[0]["name"]):
                        for r, d in badge_hits(gui):
                            self.assertGreaterEqual(r.w, r.h, f"a sliver of a chip: {r}")
                            if not any(r.colliderect(p) for p in piles):
                                self.assertEqual(gui.hit_at(r.center)[0], "badge", f"the chip {r} can't be hovered")

    def test_a_long_name_as_the_bridge_makes_it_gives_way_to_the_chips(self):
        """The bridge names an AI "AI 1 (<deck name>)" (Main.java). With names that long the first fix moved almost nothing: the
        name kept the row and the Monarch stayed under the piles (159 of 195 cases in the review's sweep). Now the name shortens,
        never below "AI 1...", and the chip is whole, uncovered and hoverable."""
        real = ft.draw_text
        for kind in ("emblem", "monarch"):
            command = MONARCH if kind == "monarch" else [{"id": 8800, "name": NAME, "text": "", "type": ""}]
            for size, scale in TableTests.SIZES + [((1280, 720), 1.25), ((1366, 768), 1.5)]:
                names = [f"AI {k} (We Just Need to Punch Them (Stompy))" for k in (1, 2, 3)]
                drawn = []

                def spy(surf, text, x, y, font, *a, **k):
                    r = real(surf, text, x, y, font, *a, **k)
                    if isinstance(text, str) and text.startswith("AI "):
                        drawn.append((text, r))
                    return r
                with mock.patch.object(ft, "draw_text", spy):
                    gui = table(named_pod(names, command), size, scale)
                    drawn.clear()
                    frame(gui, 1)
                piles = [r for r, k, _d in gui.hits if k in ("zone", "library")]
                with self.subTest(kind=kind, size=size, scale=scale):
                    chips = [(r, d) for r, d in badge_hits(gui) if d["player"] != gui.state["me"]]
                    self.assertEqual(len(chips), 3)
                    for r, _d in chips:
                        self.assertFalse(any(r.colliderect(p) for p in piles), f"the chip {r} is under a pile")
                        self.assertEqual(gui.hit_at(r.center)[0], "badge")
                    for k in (1, 2, 3):
                        self.assertTrue([t for t, _r in drawn if t.startswith(f"AI {k}")], f"AI {k}'s name isn't drawn: {drawn}")
                    for t, nr in drawn:
                        for r, _d in chips:
                            self.assertFalse(isinstance(nr, pygame.Rect) and nr.colliderect(r), f"the name {t!r} {nr} runs under {r}")

    def test_the_priority_dot_is_never_under_a_chip(self):
        """At big text the dot was placed by the "title" font's width while the life total (and the chips' room) used a smaller
        font: the Monarch chip covered the dot at 200% in 4-player pods. The dot now sits by the life total as drawn."""
        real = pygame.draw.circle
        for command in (MONARCH, [{"id": 8800, "name": "The Monarch", "text": ""}, {"id": 8900, "name": "The Initiative", "text": ""},
                                  {"id": 8950, "name": "The Ring", "text": ""}]):
            for size, scale in BIG_TEXT + (((900, 600), 2.0), ((2560, 1440), 2.0), ((1360, 840), 1.0)):
                st = named_pod(["AI 1", "AI 2", "AI 3"], command)
                for p in st["players"]:
                    p["priority"] = p["id"] != st["me"]
                dots = []

                def spy(surf, colour, center, radius, *a, **k):
                    if tuple(colour)[:3] == tuple(ft.GREEN)[:3] and radius == 5:
                        dots.append(pygame.Rect(center[0] - radius, center[1] - radius, 2 * radius, 2 * radius))
                    return real(surf, colour, center, radius, *a, **k)
                with mock.patch.object(ft.pygame.draw, "circle", spy):
                    gui = table(st, size, scale)
                    dots.clear()
                    frame(gui, 1)
                with self.subTest(size=size, scale=scale, badges=len(command)):
                    self.assertEqual(len(dots), 3, "one dot per opponent with priority")
                    for r, _d in badge_hits(gui):
                        for dot in dots:
                            self.assertFalse(r.colliderect(dot), f"the chip {r} covers the priority dot {dot}")


LIVE_PROBLEM = live.live_problem()
DECK = ["Island"] * 17 + ["Divination"] * 23


@unittest.skipIf(LIVE_PROBLEM, f"Forge is not ready here: {LIVE_PROBLEM}")
class LiveTests(unittest.TestCase):
    """A Turbo Draft game in the real engine, drawn by the real table at the first priority question."""

    def test_both_panels_show_the_emblem_with_its_words(self):
        fc.sync_bridge()
        tmp = tempfile.mkdtemp(prefix="emb1_live_")
        self.addCleanup(shutil.rmtree, tmp, True)
        p0 = fc.write_deck_file(os.path.join(tmp, "p.dck"), [], DECK, "P", fmt="draft", rules=("turbo",))
        p1 = fc.write_deck_file(os.path.join(tmp, "o.dck"), [], DECK, "O", fmt="draft", rules=("turbo",))
        s = fc.ForgeSession(p0, [p1], name="Tester", seed=5)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        settings = os.path.join(tmp, "settings.json")
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"passing_version": 43, "tour_done": 99}, f)
        gui = ft.ForgeTable(s, StubStore(), window_size=(1360, 840), settings_path=settings)
        self.addCleanup(lambda: self._close(gui))
        end = time.time() + 150
        while time.time() < end:
            gui.tick(time.monotonic())
            gui.vs = None
            st = gui.state or {}
            msg = (st.get("prompt") or {}).get("message", "")
            if st and s.is_mulligan_prompt():
                s.ok()
                time.sleep(0.5)
            elif st and msg.startswith("Priority") and st.get("asking", True):
                break
            time.sleep(0.05)
        self.assertFalse(s.fatal or s.exited, s.fatal or "Forge stopped")
        frame(gui, 3)
        self.assertEqual((gui.game_format(), s.rules_used), ("draft", ("turbo",)))
        self.assertEqual([[c["name"] for c in p["zones"].get("command") or []] for p in gui.state["players"]], [[NAME]] * 2)
        hits = badge_hits(gui)
        self.assertEqual(sorted(d.get("player") for _, d in hits), sorted(p["id"] for p in gui.state["players"]))
        for _, d in hits:
            self.assertIn(WORDS, d["text"])
        self.assertEqual(s.checks, [])

    @staticmethod
    def _close(gui):
        with mock.patch.object(ft.pygame, "quit"):
            gui.shutdown()


if __name__ == "__main__":
    unittest.main()
