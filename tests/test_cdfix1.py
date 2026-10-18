# SPDX-License-Identifier: GPL-3.0-or-later
"""Round CDFIX1: two small things CD2 left (docs/rounds/CD2.md, "Not done").

1. A resumed or rewound Commander Draft game read its format from player.dck and showed as Commander: a Commander Draft .dck had
   no "Deck Type" line (Forge plays it as a Commander deck). Now it says "Manticore Format=commander_draft" in [metadata] (a key
   Forge's reader skips, as CD3's "Manticore Rules="), and formats.from_dck reads that first. Every other format's file is as before.
2. The cube you drafted wasn't remembered: the next draft opened on Commander Cube. Now settings.json "draft_cube" keeps it
   ({"stock": key}, or {"ref": link, "style": the style you chose}) once a draft starts, and the draft window opens on it.

  FormatLineTests     formats.dck_lines / from_dck for every format; an unknown value; an old file
  DeckFileTests       forge_client.write_deck_file and ForgeSession's format from player.dck
  CubeMemoryTests     the draft window (tests/test_cd2.py's offline harness): opens on the last cube; a link keeps its style
  LiveTests (live)    Forge starts a Commander game from the new .dck, and the session says Commander Draft
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import cube_source as cs
import draft_pod as dp
import draft_screens as ds
import forge_client as fc
import forge_table as ft
import formats
from tests import live
import tests.test_cd2 as cd2
from tests.test_cd2 import every_card_known


class FormatLineTests(unittest.TestCase):
    def test_only_commander_draft_gets_the_line(self):
        self.assertEqual(formats.dck_lines("commander"), [])
        self.assertEqual(formats.dck_lines("brawl"), ["Deck Type=Brawl"])
        self.assertEqual(formats.dck_lines("standard"), ["Deck Type=Constructed"])
        self.assertEqual(formats.dck_lines("draft"), ["Deck Type=Limited"])
        self.assertEqual(formats.dck_lines("commander_draft"), ["Manticore Format=commander_draft"])

    def test_every_format_reads_back_as_itself(self):
        for key in formats.FORMATS:
            with self.subTest(key=key):
                text = "\n".join(["[metadata]", "Name=x"] + formats.dck_lines(key) + ["[Main]", "1 Island"])
                self.assertEqual(formats.from_dck(text), key)

    def test_an_unknown_value_falls_back_to_the_deck_type(self):
        self.assertEqual(formats.from_dck("[metadata]\nManticore Format=nonsense\nDeck Type=Brawl\n"), "brawl")
        self.assertEqual(formats.from_dck("[metadata]\nManticore Format=nonsense\n"), "commander")

    def test_a_file_from_before_this_round_still_reads_as_commander(self):
        self.assertEqual(formats.from_dck("[metadata]\nName=Draft deck\n[Commander]\n1 Kinnan\n[Main]\n59 Island\n"), "commander")


class DeckFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cdfix1_")

    def write(self, fmt, name="d.dck"):
        return fc.write_deck_file(os.path.join(self.tmp, name), ["Kinnan, Bonder Prodigy"], ["Island"] * 59, "Draft", None, fmt)

    def test_a_commander_draft_file_says_so(self):
        with open(self.write("commander_draft"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Manticore Format=commander_draft", text.split("[Commander]")[0])
        self.assertIn("[Commander]", text)                         # still a Commander deck for Forge
        self.assertEqual(formats.from_dck_file(os.path.join(self.tmp, "d.dck")), "commander_draft")

    def test_a_commander_file_is_unchanged(self):
        with open(self.write("commander", "c.dck"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("Manticore Format", text)
        self.assertNotIn("Deck Type", text)

    def test_a_session_from_the_file_knows_the_format(self):
        s = fc.ForgeSession(self.write("commander_draft"), [])
        self.assertEqual(s.fmt, "commander_draft")
        self.assertEqual(formats.engine(s.fmt), "commander")       # the engine still plays Commander


REAL_STOCK = cs.STOCK_CUBES            # before tests/test_cd2's set-up empties it


class CubeMemoryTests(unittest.TestCase):
    """tests/test_cd2.py's offline set-up (a made-up cube saved as "cm", no stock cubes), borrowed, not its tests."""
    setUp = cd2.ScreenTests.setUp
    gui = cd2.ScreenTests.gui
    close = staticmethod(cd2.ScreenTests.close)

    def flow(self, g):
        f = ds.DraftFlow(g, root=self.pods, lookup=every_card_known, cube_folder=self.cubes)
        if f.job is not None:
            f.job.thread.join(10)
        return f

    def test_starting_a_draft_remembers_the_cube(self):
        g = self.gui(title=False)
        f = self.flow(g)
        f.start_draft(g)
        self.assertEqual(g.draft_cube, {"stock": "cm"})

    def test_a_link_and_its_style_are_remembered_and_come_back(self):
        g = self.gui(title=False)
        f = self.flow(g)
        f.field.text = "cm"                                         # what was typed into the link box
        f.load_cube()
        f.job.thread.join(10)
        f.style_choice = "classic"
        f.remember_cube(g)                                          # what start_draft does (no draft left unfinished here)
        self.assertEqual(g.draft_cube, {"ref": "cm", "style": "classic"})
        again = self.flow(g)
        self.assertEqual((again.field.text, again.style_choice, again.ref()), ("cm", "classic", "cm"))

    def test_a_stock_cube_comes_back(self):
        with mock.patch.object(cs, "STOCK_CUBES", REAL_STOCK):
            g = self.gui(title=False)
            g.draft_cube = {"stock": "modovintage"}
            f = self.flow(g)
            self.assertEqual((f.stock, f.ref(), f.field.text), ("modovintage", "modovintage", ""))
            g.draft_cube = {"stock": "no-longer-a-stock-cube"}
            self.assertEqual(self.flow(g).stock, cs.DEFAULT_CUBE)

    def test_an_unfinished_draft_is_still_offered_first(self):
        g = self.gui(title=False)
        f = self.flow(g)
        f.start_draft(g)
        g.draft_cube = {"ref": "somewhere-else"}
        again = self.flow(g)
        self.assertIsNotNone(again.resume)                          # Continue / Discard, as before
        self.assertIsNone(again.job)                                # nothing loaded behind it

    def test_settings_keep_it_and_refuse_junk(self):
        path = os.path.join(self.tmp, "settings.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"draft_cube": {"ref": "https://cubecobra.com/cube/overview/abc", "style": "classic", "x": 1}}, fh)
        g = ft.ForgeTable(fc.ForgeSession("", []), self.gui(title=False).art.store, settings_path=path, window_size=(1360, 840))
        self.addCleanup(self.close, g)
        self.assertEqual(g.draft_cube, {"ref": "https://cubecobra.com/cube/overview/abc", "style": "classic"})
        g.draft_cube = {"stock": "cm"}
        g.save_settings()
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["draft_cube"], {"stock": "cm"})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"draft_cube": ["not", "a", "dict"]}, fh)
        g2 = ft.ForgeTable(fc.ForgeSession("", []), g.art.store, settings_path=path, window_size=(1360, 840))
        self.addCleanup(self.close, g2)
        self.assertIsNone(g2.draft_cube)

    def test_no_draft_no_key(self):
        path = os.path.join(self.tmp, "settings2.json")
        g = ft.ForgeTable(fc.ForgeSession("", []), self.gui(title=False).art.store, settings_path=path, window_size=(1360, 840))
        self.addCleanup(self.close, g)
        g.save_settings()
        with open(path, encoding="utf-8") as fh:
            self.assertNotIn("draft_cube", json.load(fh))


PROBLEM = live.live_problem()


@unittest.skipIf(PROBLEM, "Forge is not ready here: %s" % PROBLEM)
class LiveTests(unittest.TestCase):
    def test_forge_plays_the_new_file_as_commander_and_the_session_says_commander_draft(self):
        tmp = tempfile.mkdtemp(prefix="cdfix1_live_")
        mine = fc.write_deck_file(os.path.join(tmp, "player.dck"), ["Kinnan, Bonder Prodigy"], ["Island"] * 59, "Draft",
                                  None, "commander_draft")
        opp = fc.write_deck_file(os.path.join(tmp, "opp.dck"), ["Kinnan, Bonder Prodigy"], ["Forest"] * 59, "Opp", None,
                                 "commander_draft")
        fc.sync_bridge()
        s = fc.ForgeSession(mine, [opp], name="Me", seed=3)
        s.stderr_path = os.path.join(tmp, "engine.log")
        s.start()
        self.addCleanup(s.close)
        end = time.time() + 120
        while time.time() < end and not s.state and not s.fatal and not s.exited:
            s.poll()
            time.sleep(0.05)
        self.assertIsNone(s.fatal)
        self.assertEqual((s.fmt, s.format_used), ("commander_draft", "commander"))

        def mine():
            st = s.state or {}
            return ([p for p in st.get("players", []) if p.get("id") == st.get("me")] or [{}])[0]
        end = time.time() + 60                                      # the first snapshot comes before the commanders are out
        while time.time() < end and not (mine().get("zones") or {}).get("command"):
            s.poll()
            time.sleep(0.05)
        self.assertEqual(mine().get("life"), 40)
        self.assertIn("Kinnan, Bonder Prodigy", [c.get("name") for c in (mine().get("zones") or {}).get("command") or []])


if __name__ == "__main__":
    unittest.main()
