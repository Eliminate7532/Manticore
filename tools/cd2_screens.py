# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/cd2_screens.py - pictures of the cube draft's screens (Round CD2), for the round's delivery and for looking at the layout.
Never imported by the game.

    python tools/cd2_screens.py --cube cube_cm.json --out shots/            stand-in card faces (no network)
    python tools/cd2_screens.py --cube cube_cm.json --out shots/ --fetch    real card pictures from Scryfall (the art loader)

--cube: a Cube Cobra JSON file (cubecobra.com/cube/api/cubeJSON/<id>, saved). Every window size below, at 100% and 200% text:
the main menu, the cube loaded, a pick with one card chosen and one under the mouse, the review, the decks, and the deck screen
with the four decks chosen. The pod and the decks go to a scratch folder; nothing touches your saves or My decks.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

SIZES = (((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1024, 640), 1.0))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--cube", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--sizes", help="only these, e.g. 1360x840@1.0,1920x1080@2.0")
    a = ap.parse_args(argv)
    sizes = SIZES
    if a.sizes:
        sizes = []
        for part in a.sizes.split(","):
            wh, _at, sc = part.partition("@")
            w, h = (int(v) for v in wh.lower().split("x"))
            sizes.append(((w, h), float(sc or 1.0)))
    tmp = tempfile.mkdtemp(prefix="cd2_screens_")
    os.environ["MANTICORE_DATA_DIR"] = os.path.join(tmp, "data")
    if not a.fetch:
        os.environ["MANTICORE_OFFLINE"] = "1"
    import pygame
    import cube_source as cs
    import draft_ai as da
    import draft_pod as dp
    import draft_screens as ds
    import forge_client as fc
    import forge_table as ft
    with open(a.cube, encoding="utf-8") as f:
        data = json.load(f)
    data = data.get("cube", data) if "cards" not in data else data
    cubes = os.path.join(tmp, "cubes")
    cs.save_cache("cm", data, cubes)
    dirs = (os.path.join(tmp, "my_decks"), os.path.join(BASE_DIR, "sample_decks"), BASE_DIR)
    os.makedirs(a.out, exist_ok=True)
    if a.fetch:
        import card_data
        store = card_data.CardDataStore()
    else:
        from tests.forge_fake import StubStore
        store = StubStore()

    class Launcher:
        runtime = None

    lookup = (lambda name: "Name:" + name) if not a.fetch else None
    for size, scale in sizes:
        tag = f"{size[0]}x{size[1]}_{int(scale * 100)}"
        gui = ft.ForgeTable(fc.ForgeSession("", []), store, settings_path=None, window_size=size, launcher=Launcher(),
                            deck_dirs=dirs)
        gui.text_scale = scale
        gui.open_boot(splash=False)
        gui.sync()
        gui.render()
        pygame.image.save(gui.screen, os.path.join(a.out, f"{tag}_1_menu.png"))
        gui.boot = None
        gui.open_menu()
        gui.modal = ds.DraftFlow(gui, from_title=True, root=os.path.join(tmp, "pods"), lookup=lookup, cube_folder=cubes)
        gui.modal.job.thread.join()
        gui.sync()
        gui.render()
        pygame.image.save(gui.screen, os.path.join(a.out, f"{tag}_2_setup.png"))
        flow = gui.modal
        flow.pod = dp.Pod.new(flow.job.cube, flow.job.check, seed=a.seed, root=os.path.join(tmp, "pods"))
        flow.to_pick(gui)
        chooser = da.chooser(flow.pod.cards, 77, flow.pod.z)
        for _ in range(3):                                         # a few picks in, so "your picks" shows something
            flow.pod.pick(chooser(0, flow.pod.pack(), flow.pod.pool(), flow.pod.needed()))
        gui.render()
        flow.selected = [flow.pod.pack()[0]]
        tiles = flow.tiles
        gui.mouse = tiles[3][0].center if len(tiles) > 3 else (0, 0)
        for _ in range(2 if not a.fetch else 40):
            gui.sync()
            gui.render()
            if a.fetch:
                pygame.time.wait(150)
        pygame.image.save(gui.screen, os.path.join(a.out, f"{tag}_3_pick.png"))
        while not flow.pod.done:
            flow.pod.pick(chooser(0, flow.pod.pack(), flow.pod.pool(), flow.pod.needed()))
        flow.to_review()
        gui.mouse = (0, 0)
        gui.render()
        gui.mouse = flow.tiles[1][0].center if len(flow.tiles) > 1 else (0, 0)
        for _ in range(2 if not a.fetch else 30):
            gui.sync()
            gui.render()
            if a.fetch:
                pygame.time.wait(150)
        pygame.image.save(gui.screen, os.path.join(a.out, f"{tag}_4_review.png"))
        flow.to_decks(gui)
        gui.mouse = (0, 0)
        for _ in range(2 if not a.fetch else 20):
            gui.sync()
            gui.render()
            if a.fetch:
                pygame.time.wait(150)
        pygame.image.save(gui.screen, os.path.join(a.out, f"{tag}_5_decks.png"))
        flow.save_decks(gui)
        gui.sync()
        gui.render()
        pygame.image.save(gui.screen, os.path.join(a.out, f"{tag}_6_deck_screen.png"))
        shutil.rmtree(dirs[0], ignore_errors=True)
        with mock.patch.object(ft.pygame, "quit"):
            gui.shutdown()
        print(tag, "done")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
