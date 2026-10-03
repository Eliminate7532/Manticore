# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/tour_screens.py - the Round UX1 review pictures: every step of the table tour, made without Java and without a real window.

    python tools/tour_screens.py [OUT_FOLDER] [--fetch]          default: docs/incoming/ux1/screens

Every step at 1920x1080 text scale 1.0 and at 1360x840 text scale 2.0 (the tightest common case), on a recorded main-phase board,
plus the cog pop-up with its new "Tour of the table" button. Card pictures come from the local cache; with --fetch the missing ones
are downloaded from Scryfall first (the normal politeness: one request at a time).
"""
import os
import sys
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("MANTICORE_SKIP_LIVE", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MANTICORE_DATA_DIR", tempfile.mkdtemp(prefix="tour_screens_"))

import pygame  # noqa: E402

import forge_table as ft  # noqa: E402
import tour  # noqa: E402
from tests.forge_fake import FakeSession, StubStore, load_log, load_state  # noqa: E402
from tools.ad2_screens import frames, settle  # noqa: E402

os.environ.pop("MANTICORE_NO_TOUR", None)           # tests/__init__.py (imported just above) switches the tour off for tests


def main(argv):
    fetch = "--fetch" in argv
    args = [a for a in argv if not a.startswith("--")]
    out = args[0] if args else os.path.join(ROOT, "docs", "incoming", "ux1", "screens")
    os.makedirs(out, exist_ok=True)
    if fetch:
        from card_data import CardDataStore
        store = CardDataStore()
    else:
        store = StubStore()
    for size, scale in (((1920, 1080), 1.0), ((1360, 840), 2.0)):
        tag = f"{size[0]}x{size[1]}_{scale}"
        gui = ft.ForgeTable(FakeSession(load_state("main1_lands"), load_log()), store, settings_path=None, window_size=size)
        gui.text_scale = scale
        gui.current_bg = "citadel"
        gui.animations = False                      # the finished spotlight, not a frame of its glide
        gui.launcher = object()
        frames(gui, 2)
        settle(gui)
        gui.maybe_start_tour()
        assert gui.tour is not None, "the tour did not start on a main-phase priority question"
        for i in range(gui.tour.total):
            frames(gui, 2)
            name = f"tour_{tag}_{i + 1:02d}_{gui.tour.step.region or 'centre'}"
            pygame.image.save(gui.screen, os.path.join(out, name + ".png"))
            print(name)
            gui.tour.next()
        gui.tour = None
        frames(gui, 1)
        gui.on_click(gui.cog_rect.center, 1)
        frames(gui, 2)
        pygame.image.save(gui.screen, os.path.join(out, f"cog_{tag}.png"))
        print(f"cog_{tag}")
    pygame.quit()


if __name__ == "__main__":
    main(sys.argv[1:])
