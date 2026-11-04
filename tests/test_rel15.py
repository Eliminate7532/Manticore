# SPDX-License-Identifier: GPL-3.0-or-later
"""Release candidate 0.28.53.15 (3 Nov 2026): the places where the merged branches met.

  1  DuelsTests      java_rpg Duels.java: RPG-SOAK1's stdin mode and PROG3's seat-A start effects - both loops read the sixth
                     column; live, a bad start-effects SPEC sent on standard input is refused (so the stdin loop really applies it).
  2  MapTests        rpg/maps: PROG2's chests and rly2's traders had the same object ids in ashfen_outskirts.tmx; every map's ids
                     are unique and below nextobjectid, and the outskirts keep both chests, both traders and the town_3 spawn.
  3  ScreenTests, FrameTests  rpg_screens.AdventureFlow draws PROG2's chests and rly2's shop signs in the same frame (source, and a
                     real frame on the outskirts: both chests reach the map view, the stall in view gets its sign).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DUELS = os.path.join(BASE, "java_rpg", "src", "forge", "rpgtools", "Duels.java")
JAR = os.path.join(BASE, "java_rpg", "rpg_tools.jar")
try:
    from tests import live
    LIVE = live.live_enabled()
except Exception:
    LIVE = False
HAS_RUNTIME = os.path.isdir(os.path.join(BASE, "forge_runtime", "res"))


class DuelsTests(unittest.TestCase):
    def test_both_loops_pass_the_sixth_column(self):
        d = open(DUELS, encoding="utf-8").read()
        self.assertEqual(d.count("p.length > 5 ? p[5] : null"), 2, "the file loop and the stdin loop must both read startA")
        calls = re.findall(r"\bplay\(w, (.*?)\);", d, re.S)
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual(c.count(","), 6, c)                  # after w: id, a, b, games, seed, timeout, startA

    @unittest.skipUnless(LIVE and HAS_RUNTIME and os.path.exists(JAR), "needs Forge and Java (live tests)")
    def test_a_bad_spec_on_standard_input_is_refused(self):
        import forge_client
        tmp = tempfile.mkdtemp(prefix="rel15_")
        self.addCleanup(shutil.rmtree, tmp, True)
        deck = os.path.join(tmp, "a.dck")
        forge_client.write_deck_file(deck, [], ["Mountain"] * 20 + ["Lightning Bolt"] * 4 + ["Goblin Guide"] * 4 * 9,
                                     name="a", fmt="standard")
        out = os.path.join(tmp, "out.jsonl")
        rt = os.path.join(BASE, "forge_runtime")
        cp = JAR + os.pathsep + os.path.join(rt, "forge.jar")
        p = subprocess.Popen([forge_client.find_java(), "-Xmx1g", "-Djava.awt.headless=true", "-cp", cp, "forge.rpgtools.Duels",
                              "-", out, "60"], cwd=rt, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, text=True)
        try:
            p.stdin.write("\t".join(("x1", deck, deck, "1", "7", "life=banana")) + "\n")
            p.stdin.close()
            p.wait(timeout=240)
        finally:
            if p.poll() is None:
                p.kill()
        lines = [json.loads(x) for x in open(out, encoding="utf-8") if x.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("bad start effects", lines[0].get("error", ""), lines)


class MapTests(unittest.TestCase):
    def test_every_maps_object_ids_are_unique_and_below_nextobjectid(self):
        maps = glob.glob(os.path.join(BASE, "rpg", "maps", "*.tmx"))
        self.assertTrue(maps)
        for path in maps:
            s = open(path, encoding="utf-8").read()
            ids = [int(i) for i in re.findall(r'<object id="(\d+)"', s)]
            self.assertEqual(len(ids), len(set(ids)), os.path.basename(path))
            nxt = re.search(r'nextobjectid="(\d+)"', s)
            if ids and nxt:
                self.assertLess(max(ids), int(nxt.group(1)), os.path.basename(path))

    def test_the_outskirts_keep_both_chests_both_traders_and_the_spawn(self):
        s = open(os.path.join(BASE, "rpg", "maps", "ashfen_outskirts.tmx"), encoding="utf-8").read()
        for needle in ('value="moor_hollow"', 'value="moor_cairn"', 'value="brackwater_trader"', 'value="cinder_trader"',
                       'name="town_3" class="spawn"'):
            self.assertIn(needle, s)


def _window_base():
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from tests.test_rpgtap import TapBase
    return TapBase


class FrameTests(_window_base()):
    def test_the_outskirts_frame_draws_both_chests_and_both_shop_signs(self):
        from unittest import mock
        import rpg_map_view
        from tests.test_forge_table import frame
        g, _l = self.setup_game()
        f = self.on_map(g, "ashfen_outskirts", "town_3")
        seen = {}
        real = rpg_map_view.MapView.draw

        def spy(view, *a, **k):
            seen["chests"] = [t.chest for t, _o, _d in k.get("chests") or []]
            return real(view, *a, **k)

        with mock.patch.object(rpg_map_view.MapView, "draw", spy):
            frame(g)
        self.assertEqual(sorted(seen.get("chests", [])), ["moor_cairn", "moor_hollow"])
        self.assertIn("Cinder Gate", [w for w, _r in f.sign_words])     # the stall in view (signs are drawn only on screen)


class ScreenTests(unittest.TestCase):
    def test_the_adventure_frame_draws_chests_and_shop_signs(self):
        s = open(os.path.join(BASE, "rpg_screens.py"), encoding="utf-8").read()
        i = s.index("chests=self.chest_list())")
        j = s.index("self.draw_shop_signs(scr, ex.area)")
        self.assertLess(i, j)
        self.assertLess(j - i, 400, "the shop signs are drawn right after the map view")


if __name__ == "__main__":
    unittest.main()
