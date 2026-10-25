# SPDX-License-Identifier: GPL-3.0-or-later
"""An independent review of Rounds RPG10-RPG15 (24 Oct 2026): one test per finding in claude/REVIEW_RPG10_RPG15_2026-10-24.md.

The R* tests FAIL on round-rpg15 at a5453fb - each is a finding, named after its number in the review doc. The two Guard*
tests pass there; they are the doc's proof of method (each failed on a copy with a planted bug that test_rpg10-15 let through).
Nothing here changes program code.
"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import pygame

import rpg_map_view
import rpg_screens as rs
from rpg import clock, explore, talk
from tests import test_rpg14 as r14
from tests.test_forge_table import frame, key
from tests.test_rpg12 import make_tool

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "rpg", "maps", "src")


def shown_names(drawn, owned):
    """The owned names drawn as "<count>  <name>" lines - a name clip_text cut short ("Dionus, Elvish...") counts as drawn."""
    lines = [s.split("  ", 1)[1] for s in drawn if "  " in s and s.split("  ", 1)[0].isdigit()]
    out = set()
    for n in owned:
        for ln in lines:
            cut = ln[:-3] if ln.endswith("...") else ln[:-1] if ln.endswith("\u2026") else None
            if ln == n or (cut and n.startswith(cut)):
                out.add(n)
                break
    return out


class R1CollectionTests(r14.WindowBase):
    """R1: Menu > Collection says "every card you own", but a collection taller than the window is cut off - no scroll, no
    "and N more"; test_rpg14's test only looks for the first five names."""

    def test_every_owned_card_is_shown_or_said_to_be_more(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        coll = f.game.save.collection
        names = sorted(n for n in f.game.pool.names() if not f.game.pool.get(n).is_basic)[:400]
        for n in names:
            coll.add(n, 1)
        key(g, pygame.K_ESCAPE)
        f.press_sub(g, "collection")
        drawn = []
        real = rs.draw_text

        def spy(screen, s, x, y, font, *a, **k):
            drawn.append(s)
            return real(screen, s, x, y, font, *a, **k)
        with mock.patch.object(rs, "draw_text", spy):
            frame(g)
        owned = [n for n in coll.names() if isinstance(coll.count(n), int) and coll.count(n) > 0]
        shown = shown_names(drawn, owned)
        missing = [n for n in owned if n not in shown]
        said_more = any("more" in s for s in drawn)
        self.assertTrue(not missing or said_more,
                        f"{len(missing)} of {len(owned)} owned cards are neither drawn nor said to be there "
                        f"(the wheel can't scroll the Collection: AdventureFlow.wheel has no key for it)")


class R2ClockRateTests(unittest.TestCase):
    """R2: clock.advance rounds the clock to 1/1000 game minute on every call, and the table calls it once a tick (60 a
    second), so a walked second is 1.02 game minutes at 60 ticks a second (and 0.96 at 240)."""

    def test_ten_seconds_walked_at_60_ticks_a_second_is_ten_game_minutes(self):
        w = {}
        for _ in range(600):
            clock.advance(w, 1 / 60)
        self.assertAlmostEqual(clock.now(w) - clock.START, 10.0, delta=0.05)


class R3InnTests(r14.WindowBase):
    """R3: every talk to the innkeeper is a night's sleep (rpg_screens.talk_to calls clock.to_morning whenever conv.rest) - a
    word with her at 07:05, just after waking, skips a whole day."""

    def test_talking_to_her_in_the_morning_does_not_lose_a_day(self):
        g, _l = self.setup_game()
        f = self.in_town(g)
        world = f.game.save.world
        world["clock"] = 1 * clock.DAY + 7 * 60 + 5          # day 2, 07:05 - you just woke
        self.stand(g, talk.INNKEEPER)
        key(g, pygame.K_RETURN)
        # Round RPG15c (the fix's API): her talk now ends with "Sleep until morning" / "Not now", and say_all() can't walk a
        # choice without an answer (its second Enter talks to her again). Enter alone, as a player would press it:
        box = g.modal.talk_box
        for _ in range(20):
            if box.choosing:
                break
            key(g, pygame.K_RETURN)
        self.assertTrue(box.choosing, "her talk asks before you sleep")
        self.assertEqual(box.choice[box.cursor], talk.REST_NO, "by day Enter picks Not now")
        key(g, pygame.K_RETURN)
        self.assertNotEqual(g.modal.sub, "talk")
        self.assertLess(clock.now(world), 2 * clock.DAY, "a talk at 07:05, just after waking, slept the whole day away")


class R4MaskCacheTests(unittest.TestCase):
    """R4: MapView.light_mask keeps one picture per (radius, alpha, strength) for good; on an outdoor map the alpha follows the
    clock, so a day of walking in one view keeps hundreds of masks (819, 44 MB, in the town at zoom 9)."""

    def test_a_day_of_dusk_and_dawn_keeps_a_bounded_number_of_masks(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        explore.forget_areas()
        a = explore.load_area("lantern_hollow")
        v = rpg_map_view.MapView(a)
        cam = explore.Camera(456, 240, 1)
        w = explore.Walker(a, *a.spawn("start"))
        cam.follow(w.x, w.y, a.width, a.height)
        canvas = pygame.Surface((456, 240))
        for m in range(0, clock.DAY, 2):
            v.darkness = a.darkness_at(m)
            v.draw_light(canvas, cam, w, m)
        masks = [k for k in v.cache if k[0] == "mask"]
        self.assertLessEqual(len(masks), 64, f"{len(masks)} light masks kept after one day")


class R5ObjectIdTests(unittest.TestCase):
    """R5: a saved spot is "<map>#<object id>", and tools/rpg_make_maps.py numbers objects by where their marker is (row, then
    column). Adding any marker above an existing one - RPG16's night foes - renumbers it: the beaten Grave Priest's spot then
    names the new foe, and the Priest is back."""

    def test_adding_a_foe_above_the_boss_keeps_the_bosss_spot(self):
        # Round RPG15c (the fix's API): ids now come from the map file being replaced (tool.read_ids / tmx_text's `old`), not from
        # the place in `objects`, so the test builds the file, adds the foe, and builds again over that file.
        import xml.etree.ElementTree as ET
        tool = make_tool()
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            shutil.copy(os.path.join(SRC, "warrens_2.txt"), src)
            path = os.path.join(src, "warrens_2.txt")
            built = os.path.join(tmp, "warrens_2.tmx")

            def build():
                lay = tool.read_layout(path)
                text = tool.tmx_text(lay, tool.Sheets(), tool.read_ids(built))
                with open(built, "w", encoding="utf-8") as fh:
                    fh.write(text)
                obs = {}
                for ob in ET.parse(built).getroot().iter("object"):
                    props = {p.get("name"): p.get("value") for p in ob.iter("property")}
                    obs[int(ob.get("id"))] = (ob.get("class"), props.get("kind"), props.get("encounter"))
                return obs
            before = build()
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            text = text.replace("[map]\n", "R = encounter kind=plague_rat on +\n[map]\n", 1)
            rows = text.split("[map]\n", 1)
            grid = rows[1].split("\n")
            grid[4] = grid[4][:24] + "R" + grid[4][25:]               # a new foe in the boss's hall, above him
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(rows[0] + "[map]\n" + "\n".join(grid))
            after = build()
        boss = next(i for i, v in before.items() if v[2] == "boss")
        self.assertEqual(after[boss], before[boss], "the boss's saved spot moved: a cleared boss comes back, the new foe is 'cleared'")
        self.assertEqual({i: after[i] for i in before}, before, "an old object's id moved")
        self.assertEqual([after[i] for i in after if i not in before], [("encounter", "plague_rat", None)])
        self.assertGreater(min(i for i in after if i not in before), max(before))


class R6SoundscapeTests(r14.WindowBase):
    """R6: the town plays the deck screen's track when the game opens in a shop (no Explorer yet), but Lantern Hollow's own
    music when you walk into the same shop from the map."""

    def test_the_shop_plays_the_same_music_however_you_got_there(self):
        g, _l = self.setup_game()                                     # a new game opens in the start town's shop
        f = g.modal
        self.assertEqual(f.stage, "town")
        first = f.soundscape()
        f.leave_town(g)
        frame(g)
        t = next(tr for tr in f.explorer.area.triggers if tr.kind == "town")
        f.enter_town(g, t.town)
        self.assertEqual(f.soundscape(), first)


if __name__ == "__main__":
    unittest.main()


class R7FoeJumpTests(unittest.TestCase):
    """R7: a foe that wandered is stilled where it stands when you step past ACTIVE_TILES (12) and put back at its home when you
    step in again (Foe.wake -> reset). The view is wider than 12 tiles each side (30 x 17 tiles at 1920 x 1080), so the jump
    happens on screen: walk back and forth across the line and the foe hops home."""

    def test_a_foe_in_view_never_jumps(self):
        class Save:
            def __init__(self):
                self.world = {"map": "ashfen_outskirts", "x": None, "y": None}

            def rng(self, purpose):
                import random
                return random.Random(1)
        explore.forget_areas()
        ex = explore.Explorer(Save())
        a = ex.area
        cam = explore.Camera(1920, 1080, explore.Camera.zoom_for(1080, a.th))
        foe = ex.foes["ashfen_outskirts#6"]                          # an Ember Bandit with room either side
        hx, hy = foe.home
        w = ex.walker
        near, far = hx + 11.6 * a.tw, hx + 12.4 * a.tw
        self.assertFalse(a.box_blocked(w.box(near, hy)) or a.box_blocked(w.box(far, hy)))
        w.x, w.y = near, hy
        ex._wake_near()
        for _ in range(120):                                          # it wanders for four seconds
            foe.step(1 / 30)
        was = (foe.x, foe.y)
        w.x = far                                                     # a step out: it stands still
        ex._wake_near()
        w.x = near                                                    # a step back in
        ex._wake_near()
        cam.follow(w.x, w.y, a.width, a.height)
        vw, vh = cam.view_w / cam.zoom, cam.view_h / cam.zoom
        in_view = cam.x <= was[0] <= cam.x + vw and cam.y <= was[1] <= cam.y + vh
        jump = ((was[0] - foe.x) ** 2 + (was[1] - foe.y) ** 2) ** 0.5 / a.tw
        self.assertTrue(in_view)
        self.assertLess(jump, 0.5, f"the foe hopped {jump:.1f} tiles back home in plain view")


class R8MissingTileArtTests(r14.WindowBase):
    """R8: rpg.tmx checks that a tileset's second sheet (frame2) is there, not its first; rpg_map_view.MapView loads the sheets
    with no try, inside draw(). One missing tile picture makes every frame raise, and ForgeTable.run gives up after
    MAX_BAD_FRAMES (60, about a second) and closes the program - the task's "missing art" case."""

    def test_a_missing_tile_picture_is_said_not_raised(self):
        g, _l = self.setup_game()
        f = self.on_map(g, "lantern_hollow", "start")
        f.views.clear()
        real = rpg_map_view.pygame.image.load

        def load(path, *a, **k):
            if os.path.basename(str(path)).startswith("Floor"):
                raise FileNotFoundError(path)
            return real(path, *a, **k)
        with mock.patch.object(rpg_map_view.pygame.image, "load", load):
            try:
                frame(g, 3)
            except Exception as e:                                    # what the game loop would count as a bad frame
                self.fail(f"drawing the map raised {type(e).__name__}: {e} (60 of these in a row close the program)")


# ---- the review's own method, proven: two checks that PASS on a5453fb and failed on each planted copy (the doc's "Method") ----

class GuardTests(r14.WindowBase):
    def test_a_collection_that_fits_shows_every_card(self):
        """R1's check on a collection small enough to fit: every card owned is drawn (test_rpg14's test looks at five)."""
        g, _l = self.setup_game()
        f = self.in_town(g)
        key(g, pygame.K_ESCAPE)
        f.press_sub(g, "collection")
        drawn = []
        real = rs.draw_text

        def spy(screen, s, x, y, font, *a, **k):
            drawn.append(s)
            return real(screen, s, x, y, font, *a, **k)
        with mock.patch.object(rs, "draw_text", spy):
            frame(g)
        coll = f.game.save.collection
        owned = [n for n in coll.names() if isinstance(coll.count(n), int) and coll.count(n) > 0]
        shown = shown_names(drawn, owned)
        self.assertEqual([n for n in owned if n not in shown], [])


class GuardLightTests(unittest.TestCase):
    def test_one_view_draws_each_darkness_it_is_given(self):
        """R4's area: the mask cache must never hand back a mask made for another darkness (dusk -> night in one view)."""
        pygame.init()
        pygame.display.set_mode((64, 64))
        explore.forget_areas()
        a = explore.load_area("lantern_hollow")
        v = rpg_map_view.MapView(a)
        lamp = next(li for li in a.lights if li.flag is None and li.radius == 4)
        cam = explore.Camera(480, 320, 1)
        cam.follow(lamp.x, lamp.y, a.width, a.height)
        v.flicker = False
        got = []
        for dark in (0.3, 0.7):
            v.darkness = dark
            scr = pygame.Surface((480, 320))
            scr.fill((255, 255, 255))
            v.draw_light(scr, cam, None, 0.0)
            got.append(sum(scr.get_at((int(lamp.x - cam.x), int(lamp.y - cam.y)))[:3]) / 3)
        self.assertGreater(got[0], got[1] + 20, f"the lamp's middle at dusk {got[0]:.0f}, at night {got[1]:.0f}")
