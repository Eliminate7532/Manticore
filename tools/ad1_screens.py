# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/ad1_screens.py - the Round AD1 review pictures, made without Java and without a real window.

    python tools/ad1_screens.py [OUT_FOLDER]          default: docs/incoming/ad1/screens

The deck screen and a mid-game 4-player table at 1360x840 and 1920x1080, each at text scale 1.0 and 2.0; the table in all five
backgrounds (four pictures and Plain) at 1920x1080 / 1.0; the Settings pop-up; one ChooseDialog. File names are
<screen>_<w>x<h>_<scale>_<bg>.png. Card faces are the program's own stand-ins (the sandbox has no Scryfall pictures), so compare colour,
type and background with docs/incoming/ad1/reference/mock_*.png, not the cards. Run it on Windows to see Segoe-free, real-font pages.
"""
import os
import shutil
import sys
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("MANTICORE_SKIP_LIVE", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MANTICORE_DATA_DIR", tempfile.mkdtemp(prefix="ad1_screens_"))

import pygame  # noqa: E402

import forge_dialogs as dlg  # noqa: E402
import forge_table as ft  # noqa: E402
import gfx  # noqa: E402
from tests.forge_fake import FakeSession, StubStore, load_log, load_state  # noqa: E402
from tests.test_deck_screen import FakeLauncher  # noqa: E402
from tools.bench_frame import pod  # noqa: E402

SAMPLE = os.path.join(ROOT, "sample_decks")


def frames(gui, n=3):
    for _ in range(n):
        gui.sync()
        gui.render()


def save(gui, out, screen, size, scale, bg):
    frames(gui, 3)
    name = f"{screen}_{size[0]}x{size[1]}_{scale}_{bg}.png"
    pygame.image.save(gui.screen, os.path.join(out, name))
    print(name)


def table(size, scale, bg, state):
    gui = ft.ForgeTable(FakeSession(state, load_log()), StubStore(), settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.table_background = bg if bg != "rotate" else "graveyard"
    gui.current_bg = gui.table_background
    frames(gui, 3)
    return gui


def deck_screen(size, scale, bg, tmp):
    import forge_client as fc
    lib_dir = os.path.join(tmp, "my_decks")
    samples = os.path.join(tmp, "sample_decks")
    os.makedirs(samples, exist_ok=True)
    for f in os.listdir(SAMPLE):
        shutil.copy(os.path.join(SAMPLE, f), samples)
    gui = ft.ForgeTable(fc.ForgeSession("", []), StubStore(), settings_path=None, window_size=size, launcher=FakeLauncher(),
                        deck_dirs=(lib_dir, samples, tmp))
    gui.text_scale = scale
    gui.table_background = gui.current_bg = bg
    gui.open_menu()
    frames(gui, 3)
    return gui


def main(out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ad1_decks_")
    board = pod("main1_lands", 2)
    for size in ((1360, 840), (1920, 1080)):
        for scale in (1.0, 2.0):
            save(table(size, scale, "graveyard", board), out, "table4", size, scale, "graveyard")
            save(deck_screen(size, scale, "graveyard", tmp), out, "deck", size, scale, "graveyard")
    for bg in gfx.BACKGROUND_FILES.keys() | {"plain"}:
        save(table((1920, 1080), 1.0, bg, board), out, "table4", (1920, 1080), 1.0, bg)
    gui = table((1360, 840), 1.0, "graveyard", board)
    pos = gui.cog_rect.center
    gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1))
    save(gui, out, "settings", (1360, 840), 1.0, "graveyard")
    gui = table((1360, 840), 1.0, "graveyard", load_state("main1_lands"))
    items = [{"kind": "text", "label": "Forest (44)"}, {"kind": "text", "label": "Mox Diamond"}, {"kind": "text", "label": "Swamp (12)"}]
    gui.modal = dlg.ChooseDialog("Choose a land to discard", items, minimum=1, maximum=1)
    save(gui, out, "choose", (1360, 840), 1.0, "graveyard")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "incoming", "ad1", "screens"))
