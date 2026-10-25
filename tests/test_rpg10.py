# SPDX-License-Identifier: GPL-3.0-or-later
"""Round RPG10 (the Adventure remaster's first round, claude/SPEC_RPG_SLICE_2026-10-23.md): the art in the program, credited,
and tiles that move.

  AssetTests    assets/rpg/ is exactly what tools/rpg_import_art.py copied (MANIFEST.sha256), each pack has its README or
                notice, its licence text and a verified NOTICES.json entry, the credits name the artists, git keeps the bytes
  TmxTests      rpg.tmx keeps Tiled's tile animations and DawnLike's pairs of sheets (tileset property frame2 / frame_ms);
                frame_at says what to draw when; broken animations are refused in plain words
  ViewTests     rpg_map_view draws the frame of the moment (a Tiled animation, a pair of sheets), and a still map as before
"""
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from rpg import explore, tmx

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(BASE, "assets", "rpg")
NOTICES = os.path.join(BASE, "licenses", "NOTICES.json")
PACKS = {"dawnlike": ("rpg-art-dawnlike", "CC-BY-4.0"), "simple_battles": ("rpg-art-simple-battles", "CC-BY-3.0")}


def manifest():
    rows = {}
    with open(os.path.join(ART, "MANIFEST.sha256"), encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                digest, rel = line.rstrip("\n").split("  ", 1)
                rows[rel] = digest
    return rows


def notices():
    with open(NOTICES, encoding="utf-8") as f:
        return json.load(f)


class AssetTests(unittest.TestCase):
    def test_every_file_is_byte_for_byte_what_the_import_copied(self):
        rows = manifest()
        self.assertGreater(len(rows), 80)
        on_disk = set()
        for folder, _dirs, files in os.walk(ART):
            for name in files:
                rel = os.path.relpath(os.path.join(folder, name), ART).replace(os.sep, "/")
                if rel in ("MANIFEST.sha256", "README.txt"):
                    continue
                on_disk.add(rel)
        self.assertEqual(on_disk, set(rows), "a file in assets/rpg/ that the import didn't copy, or one missing")
        changed = []
        for rel, digest in rows.items():
            with open(os.path.join(ART, *rel.split("/")), "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != digest:
                    changed.append(rel)
        self.assertEqual(changed, [], "these files differ from their pack's zip")

    def test_what_the_slice_needs_is_there(self):
        rows = manifest()
        for rel in ("dawnlike/Commissions/Warrior.png", "dawnlike/Commissions/Mage.png", "dawnlike/GUI/GUI0.png",
                    "dawnlike/GUI/SDS_8x8.ttf", "dawnlike/Objects/Floor.png", "dawnlike/Objects/Wall.png",
                    "dawnlike/Characters/Undead0.png", "dawnlike/Characters/Undead1.png", "dawnlike/README.txt",
                    "simple_battles/scene_dungeon.png", "simple_battles/scene_cave.png", "simple_battles/scene_forest.png",
                    "simple_battles/monster_cultleader.png"):
            self.assertIn(rel, rows)
        self.assertEqual(sum(1 for r in rows if r.startswith("simple_battles/")), 19)

    def test_each_pack_is_credited_with_its_licence(self):
        n = notices()
        comps = {c["id"]: c for c in n["components"]}
        texts = os.path.join(BASE, "licenses", "texts")
        for folder, (cid, lic) in PACKS.items():
            c = comps.get(cid)
            self.assertIsNotNone(c, cid)
            self.assertEqual(c["license"], lic)
            self.assertEqual(c["status"], "verified")
            self.assertIn(f"assets/rpg/{folder}/", c["used_for"])
            self.assertTrue(os.path.exists(os.path.join(texts, lic + ".txt")), lic)
            self.assertTrue(os.path.exists(os.path.normpath(os.path.join(BASE, "licenses", c["notice_file"]))), c["notice_file"])
            self.assertIn("unmodified", c["version"])
        dawn = comps["rpg-art-dawnlike"]
        self.assertIn("DragonDePlatino", dawn["copyright"])
        self.assertIn("DawnBringer", dawn["copyright"])          # the page: "you must accredit DawnBringer"
        self.assertIn("Hyptosis", comps["rpg-art-simple-battles"]["copyright"])
        credit = {c["id"]: c for c in n["credits"]}["adventure-art"]["text"]
        for who in ("DragonDePlatino", "DawnBringer", "Hyptosis", "CC-BY 4.0", "CC-BY 3.0"):
            self.assertIn(who, credit)

    def test_the_readme_says_where_platino_is_and_he_is_there(self):
        with open(os.path.join(ART, "README.txt"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("Platino", readme)
        self.assertIn("x 48, y 192", readme)
        # read the cell without pygame: decode the PNG's pixels and look for anything opaque in the 16 x 16 cell
        for sheet in ("Reptile0.png", "Reptile1.png"):
            self.assertTrue(cell_has_pixels(os.path.join(ART, "dawnlike", "Characters", sheet), 48, 192, 16, 16), sheet)

    def test_git_keeps_the_bytes(self):
        with open(os.path.join(BASE, ".gitattributes"), encoding="utf-8") as f:
            self.assertIn("assets/rpg/** binary", f.read().splitlines())

    def test_third_party_notices_are_up_to_date(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, os.path.join(BASE, "tools", "build_notices.py"), "--check"], capture_output=True,
                           text=True, timeout=120, cwd=BASE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


def cell_has_pixels(path, x, y, w, h):
    """Any non-transparent pixel in a rectangle of a PNG (8-bit RGBA or palette with tRNS), read with zlib only."""
    import struct
    import zlib
    with open(path, "rb") as f:
        data = f.read()
    pos, idat, plte, trns = 8, b"", None, None
    width = height = depth = ctype = None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", body[:10])
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    if depth != 8 or ctype not in (3, 6):
        raise unittest.SkipTest(f"{os.path.basename(path)}: PNG type {ctype}/{depth} not read by this helper")
    bpp = 4 if ctype == 6 else 1
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows, prev, i = [], bytearray(stride), 0
    for _r in range(height):
        ft, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for k in range(stride):
            a = line[k - bpp] if k >= bpp else 0
            b = prev[k]
            c = prev[k - bpp] if k >= bpp else 0
            if ft == 1:
                line[k] = (line[k] + a) & 255
            elif ft == 2:
                line[k] = (line[k] + b) & 255
            elif ft == 3:
                line[k] = (line[k] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[k] = (line[k] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if ctype == 6:
                if rows[yy][xx * 4 + 3]:
                    return True
            else:
                idx = rows[yy][xx]
                alpha = trns[idx] if trns is not None and idx < len(trns) else 255
                if alpha:
                    return True
    return False


# ---- maps that move ---------------------------------------------------------------------------------------------------------

def png(path, colours):
    """A 32 x 16 tileset picture: tile 0 in colours[0], tile 1 in colours[1]."""
    import pygame
    surf = pygame.Surface((32, 16))
    surf.fill(colours[0], (0, 0, 16, 16))
    surf.fill(colours[1], (16, 0, 16, 16))
    pygame.image.save(surf, path)


TSX_ANIM = """<?xml version="1.0" encoding="UTF-8"?>
<tileset name="anim" tilewidth="16" tileheight="16" tilecount="2" columns="2">
 <image source="a.png" width="32" height="16"/>
 <tile id="0"><animation><frame tileid="0" duration="200"/><frame tileid="1" duration="300"/></animation></tile>
</tileset>
"""

TSX_PAIR = """<?xml version="1.0" encoding="UTF-8"?>
<tileset name="pair" tilewidth="16" tileheight="16" tilecount="2" columns="2">
 <properties><property name="frame2" value="{frame2}"/>{extra}</properties>
 <image source="p0.png" width="32" height="16"/>
</tileset>
"""

TMX = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.10" orientation="orthogonal" width="3" height="1" tilewidth="16" tileheight="16" infinite="0">
 <tileset firstgid="1" source="a.tsx"/>
 <tileset firstgid="3" source="p.tsx"/>
 <layer name="ground" width="3" height="1"><data encoding="csv">1,3,2</data></layer>
 <objectgroup name="o"/>
</map>
"""

RED, BLUE, GREEN, GOLD = (200, 10, 10), (10, 10, 200), (10, 200, 10), (220, 180, 20)


class Maps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame
        pygame.init()
        cls.pg = pygame

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rpg10_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        explore.forget_areas()
        png(os.path.join(self.tmp, "a.png"), (RED, BLUE))
        png(os.path.join(self.tmp, "p0.png"), (GREEN, (0, 0, 0)))
        png(os.path.join(self.tmp, "p1.png"), (GOLD, (0, 0, 0)))

    def write(self, anim=TSX_ANIM, frame2="p1.png", extra=""):
        with open(os.path.join(self.tmp, "a.tsx"), "w", encoding="utf-8") as f:
            f.write(anim)
        with open(os.path.join(self.tmp, "p.tsx"), "w", encoding="utf-8") as f:
            f.write(TSX_PAIR.format(frame2=frame2, extra=extra))
        path = os.path.join(self.tmp, "m.tmx")
        with open(path, "w", encoding="utf-8") as f:
            f.write(TMX)
        return path


class TmxTests(Maps):
    def test_tiled_animations_and_pairs_are_read(self):
        m = tmx.read_map(self.write())
        anim, pair = m.tilesets
        self.assertEqual(anim.anims, {0: [(0, 200), (1, 300)]})
        self.assertIsNone(anim.image2)
        self.assertEqual(pair.anims, {})
        self.assertEqual(os.path.basename(pair.image2), "p1.png")
        self.assertEqual(pair.frame_ms, tmx.FRAME_MS)
        self.assertEqual(pair.tprops.get("frame2"), "p1.png")

    def test_frame_at_says_what_to_draw_when(self):
        m = tmx.read_map(self.write())
        # gid 1: animated 200 ms tile 0, then 300 ms tile 1, round again every 500 ms
        self.assertEqual([m.frame_at(1, t)[0] for t in (0, 199, 200, 499, 500, 700, 1000)], [1, 1, 2, 2, 1, 2, 1])
        self.assertEqual(m.frame_at(2, 250), (2, 0))                      # tile 1 of that tileset has no animation
        ms = tmx.FRAME_MS
        self.assertEqual([m.frame_at(3, t) for t in (0, ms - 1, ms, 2 * ms - 1, 2 * ms)], [(3, 0), (3, 0), (3, 1), (3, 1), (3, 0)])
        self.assertEqual(m.frame_at(0, 123), (0, 0))

    def test_frame_ms_sets_the_pairs_pace(self):
        m = tmx.read_map(self.write(extra='<property name="frame_ms" type="int" value="250"/>'))
        self.assertEqual([m.frame_at(3, t)[1] for t in (0, 249, 250, 499, 500)], [0, 0, 1, 1, 0])

    def test_broken_animations_are_refused_in_plain_words(self):
        with self.assertRaises(tmx.TmxError) as e:
            tmx.read_map(self.write(frame2="missing.png"))
        self.assertIn("frame2", str(e.exception))
        self.assertIn("missing.png", str(e.exception))
        for bad in ("0", "fast"):
            with self.assertRaises(tmx.TmxError) as e:
                tmx.read_map(self.write(extra=f'<property name="frame_ms" value="{bad}"/>'))
            self.assertIn("frame_ms", str(e.exception))
        too_far = TSX_ANIM.replace('tileid="1" duration="300"', 'tileid="7" duration="300"')
        with self.assertRaises(tmx.TmxError) as e:
            tmx.read_map(self.write(anim=too_far))
        self.assertIn("animation", str(e.exception))

    def test_the_placeholder_maps_still_read_and_have_no_animations(self):
        # Round RPG12: the placeholders moved to tests/fixtures/rpg_maps; the shipped maps are DawnLike's (next test).
        from tests.rpg_fixture_maps import FIXTURE_MAPS
        names = [n[:-4] for n in sorted(os.listdir(FIXTURE_MAPS)) if n.endswith(".tmx")]
        self.assertEqual(names, ["sample_dungeon", "sample_region"])
        for name in names:
            m = tmx.read_map(os.path.join(FIXTURE_MAPS, name + ".tmx"))
            self.assertTrue(all(not ts.anims and ts.image2 is None for ts in m.tilesets), name)

    def test_the_shipped_maps_read_and_every_second_sheet_matches_its_first(self):
        # Round RPG12: the shipped maps are DawnLike's; their moving tiles are the sheet pairs (frame2), each the same size.
        import pygame
        names = explore.map_names()
        self.assertGreaterEqual(len(names), 4, names)
        pairs = 0
        for name in names:
            m = tmx.read_map(os.path.join(explore.MAPS_DIR, name + ".tmx"))
            for ts in m.tilesets:
                if ts.image2 is None:
                    continue
                pairs += 1
                self.assertEqual(pygame.image.load(ts.image2).get_size(), pygame.image.load(ts.image).get_size(),
                                 (name, ts.name))
        self.assertGreater(pairs, 0)


class ViewTests(Maps):
    def draw(self, now):
        import rpg_map_view
        pygame = self.pg
        a = explore.load_area("m", self.tmp)
        view = rpg_map_view.MapView(a)
        screen = pygame.Surface((48, 16))
        cam = explore.Camera(48, 16, 1)
        cam.follow(24, 8, 48, 16)
        view.draw(screen, cam, now=now)
        return [tuple(screen.get_at((8 + 16 * i, 8)))[:3] for i in range(3)]

    def test_each_tile_shows_the_frame_of_the_moment(self):
        self.write()
        self.assertEqual(self.draw(0.0), [RED, GREEN, BLUE])
        self.assertEqual(self.draw(0.25), [BLUE, GREEN, BLUE])              # the animation's second frame from 200 ms
        self.assertEqual(self.draw(tmx.FRAME_MS / 1000), [RED, GOLD, BLUE])  # the pair's second sheet; the animation round again
        self.assertEqual(self.draw(0.75), [BLUE, GOLD, BLUE])

    def test_a_still_map_is_drawn_as_before(self):
        import rpg_map_view
        self.write()
        a = explore.load_area("m", self.tmp)
        view = rpg_map_view.MapView(a)
        self.assertEqual(view.moving, {1, 3})
        for ts in a.map.tilesets:
            ts.anims, ts.image2 = {}, None
        view = rpg_map_view.MapView(a)
        self.assertEqual(view.moving, set())


if __name__ == "__main__":
    unittest.main()
