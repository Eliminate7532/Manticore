# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PROG4: draws the Adventure's gear icons, assets/gear/<icon>.png (16 x 16, on DawnBringer's 16-colour palette, the
palette of the Adventure's DawnLike art) from the character grids below. Manticore's own work: the pictures are dedicated to the
public domain (CC0 1.0), credited in licenses/NOTICES.json ("rpg-gear-icons"). The output is the same every run (no randomness),
and tests/test_prog4.py checks that the files are what this script draws.

    python tools/rpg_gear_icons.py          # (re)write the PNGs
    python tools/rpg_gear_icons.py --check  # exit 1 when a file differs from what the grids draw

Each grid is 16 rows of 16 characters; '.' is transparent, every other character a palette colour (PALETTE).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "assets", "gear")

# DawnBringer's 16 colours (https://pixeljoint.com/forum/forum_posts.asp?TID=12795; the palette itself is free to use)
PALETTE = {
    "k": (20, 12, 28), "p": (68, 36, 52), "n": (48, 52, 109), "d": (78, 74, 78), "b": (133, 76, 48), "g": (52, 101, 36),
    "r": (208, 70, 72), "m": (117, 113, 97), "u": (89, 125, 206), "o": (210, 125, 44), "s": (133, 149, 161),
    "l": (109, 170, 44), "t": (210, 170, 153), "c": (109, 194, 202), "y": (218, 212, 94), "w": (222, 238, 214),
}

ICONS = {
    "spear": [
        "..............w.",
        ".............ws.",
        "............wsk.",
        "...........ssk..",
        "..........bk....",
        ".........bk.....",
        "........bk......",
        ".......bk.......",
        "......bk........",
        ".....bk.........",
        "....bk..........",
        "...bk...........",
        "..bk............",
        ".bk.............",
        "bk..............",
        "................",
    ],
    "pike": [
        "............yww.",
        "...........yrws.",
        "..........yrrsk.",
        "...........ssk..",
        "..........bk....",
        ".........bk.....",
        "........bk......",
        ".......bk.......",
        "......bk........",
        ".....bk.........",
        "....bk..........",
        "...bk...........",
        "..bk............",
        ".bk.............",
        "bk..............",
        "................",
    ],
    "lantern": [
        "......kkkk......",
        ".....k....k.....",
        "......kkkk......",
        ".....kddddk.....",
        "....kdyyyydk....",
        "....kyowwoyk....",
        "....kyowwoyk....",
        "....kyoyyoyk....",
        "....kyyooyyk....",
        "....kyyyyyyk....",
        "....kdyyyydk....",
        ".....kddddk.....",
        "......kkkk......",
        "................",
        "................",
        "................",
    ],
    "charm": [
        "....kk....kk....",
        "...k..k..k..k...",
        "..k....kk....k..",
        "..k..........k..",
        "...k........k...",
        "....k......k....",
        ".....k....k.....",
        "......kook......",
        ".....kobbok.....",
        "....kobootbk....",
        "....kbootobk....",
        "....kbooobbk....",
        ".....kbbbbk.....",
        "......kkkk......",
        "................",
        "................",
    ],
    "jerkin": [
        "................",
        "...kkk....kkk...",
        "..kbbbk..kbbbk..",
        ".kbbbbbkkbbbbbk.",
        ".kbbmbbbbbbmbbk.",
        ".kbbbbbbbbbbbbk.",
        "..kkbbmbbmbbkk..",
        "....kbbbbbbk....",
        "....kbmbbmbk....",
        "....kbbbbbbk....",
        "....kbmbbmbk....",
        "....kbbbbbbk....",
        "....kbbbbbbk....",
        "....kkkkkkkk....",
        "................",
        "................",
    ],
    "boots": [
        "................",
        "....kkkk........",
        "....kbbk........",
        "....kbbk........",
        "....kbbk........",
        "....kbbk........",
        "....kbbk........",
        "....kbbk........",
        "....kbbbk.......",
        "....kbbbbkk.....",
        "....kbbbbbbk....",
        "....kbbbbbbbk...",
        "....kkkkkkkkk...",
        "................",
        "................",
        "................",
    ],
    "wayboots": [
        "................",
        "....kkkk........",
        "....kggk........",
        "....kggk........",
        "....kyyk........",
        "....kggk........",
        "....kggk........",
        "....kggk........",
        "....kgggk.......",
        "....kggggkk.....",
        "....kggggggk....",
        "....kgggggggk...",
        "....kkkkkkkkk...",
        "................",
        "................",
        "................",
    ],
    "ward": [
        "................",
        ".....kkkkkk.....",
        "....kwwwwwwk....",
        "...kwwwwwwwwk...",
        "...kwkkwwkkwk...",
        "...kwkkwwkkwk...",
        "...kwwwwwwwwk...",
        "....kwwkkwwk....",
        "....kwwwwwwk....",
        ".....kwkwkwk....",
        ".....kkkkkk.....",
        "................",
        "......kssk......",
        ".....ksssk......",
        "......kkk.......",
        "................",
    ],
    "horn": [
        "................",
        "................",
        "...........kk...",
        "..........ktlk..",
        ".........kttlk..",
        "........ktttk...",
        ".......ktttk....",
        "......ktttk.....",
        "....kkttttk.....",
        "...kgttttk......",
        "..kggtttk.......",
        "..kgggkk........",
        "..kgggk.........",
        "...kkk..........",
        "................",
        "................",
    ],
    "coin": [
        "................",
        ".....kkkkkk.....",
        "....kyyyyyyk....",
        "...kyyooooyyk...",
        "..kyyoyyyyoyyk..",
        "..kyoyykkyyoyk..",
        "..kyoykyyyyoyk..",
        "..kyoyykkyyoyk..",
        "..kyoyyyykyoyk..",
        "..kyoyykkyyoyk..",
        "..kyyoyyyyoyyk..",
        "...kyyooooyyk...",
        "....kyyyyyyk....",
        ".....kkkkkk.....",
        "................",
        "................",
    ],
    "monocle": [
        "................",
        "......kkkk......",
        ".....kyyyyk.....",
        "....kyccccyk....",
        "...kyccwccc yk..",
        "...kycwccccyk...",
        "...kycccccc yk..",
        "...kyccccccyk...",
        "....kyccccyk....",
        ".....kyyyyk.....",
        "......kkkky.....",
        "..........y.....",
        "...........y....",
        "............y...",
        ".............y..",
        "................",
    ],
    "cloak": [
        "................",
        "......kkkk......",
        ".....kgllgk.....",
        "....kglyylgk....",
        "...kgggyygggk...",
        "...kgglggglgk...",
        "..kggggggggggk..",
        "..kglgggglgggk..",
        "..kgggggggggk...",
        ".kgggglgggggglk.",
        ".kggggggglggggk.",
        ".kglgggggggglgk.",
        ".kggggggggggggk.",
        ".kkkkkkkkkkkkkk.",
        "................",
        "................",
    ],
    "censer": [
        "........d.......",
        ".......d........",
        "........d.......",
        ".......kk.......",
        "......k..k......",
        ".....k....k.....",
        "....kkkkkkkk....",
        "...kpdddddddpk..",
        "...kddrdrdrddk..",
        "...kdddddddddk..",
        "....kddddddddk..",
        ".....kddddddk...",
        "......kkkkkk....",
        ".......kddk.....",
        "......kkkkkk....",
        "................",
    ],
}


def grid(name):
    """The icon's 16 rows, each exactly 16 characters (a space in a grid above is transparent too: it is read as '.')."""
    rows = [r.replace(" ", "")[:16].ljust(16, ".") for r in ICONS[name]]
    if len(rows) != 16:
        raise ValueError(f"{name}: 16 rows are needed, not {len(rows)}")
    for r in rows:
        bad = [c for c in r if c != "." and c not in PALETTE]
        if bad:
            raise ValueError(f"{name}: {bad[0]!r} isn't a palette colour")
    return rows


def surface(name):
    import pygame
    surf = pygame.Surface((16, 16), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    for y, row in enumerate(grid(name)):
        for x, c in enumerate(row):
            if c != ".":
                surf.set_at((x, y), PALETTE[c] + (255,))
    return surf


def pixels(name):
    """The icon's RGBA pixels as bytes (what the PNG holds), for the check."""
    import pygame
    return pygame.image.tobytes(surface(name), "RGBA")


def write(folder=OUT):
    import pygame
    os.makedirs(folder, exist_ok=True)
    for name in ICONS:
        pygame.image.save(surface(name), os.path.join(folder, name + ".png"))
    return sorted(ICONS)


def check(folder=OUT):
    """Names of the icons whose file is missing or differs from the grid."""
    import pygame
    bad = []
    for name in ICONS:
        path = os.path.join(folder, name + ".png")
        try:
            got = pygame.image.tobytes(pygame.image.load(path).convert_alpha() if pygame.display.get_init() and
                                       pygame.display.get_surface() else pygame.image.load(path), "RGBA")
        except (OSError, pygame.error):
            bad.append(name)
            continue
        if got != pixels(name):
            bad.append(name)
    return bad


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    if "--check" in argv:
        bad = check()
        print("gear icons: " + ("OK" if not bad else "differ: " + ", ".join(bad)))
        return 1 if bad else 0
    print("wrote " + ", ".join(write()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
