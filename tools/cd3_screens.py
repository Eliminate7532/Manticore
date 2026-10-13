# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/cd3_screens.py - pictures of the cube draft with the stock cubes and the classic draft (Round CD3). Never imported by the game.

    python tools/cd3_screens.py --out shots/            stand-in card faces (no network)
    python tools/cd3_screens.py --out shots/ --fetch    real card pictures from Scryfall (the art loader)

At each window size: the setup with the default stock cube (Commander Cube), the setup with Turbo Cube chosen (its house rule
said), a pasted cube that isn't stock (the Draft style buttons), a classic pick, the review, the decks with the AI you play, and the
deck screen with yours and that AI's deck chosen. The pods and decks go to a scratch folder; nothing touches your saves or My decks.
"""
import argparse
import os
import shutil
import sys
import tempfile
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

SIZES = (((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0), ((1024, 640), 1.0),
         ((900, 600), 1.0))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--out", required=True)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--cube", default="turbo", help="the stock cube drafted (its key)")
    ap.add_argument("--sizes", help="only these, e.g. 1360x840@1.0,1920x1080@2.0")
    a = ap.parse_args(argv)
    sizes = SIZES
    if a.sizes:
        sizes = []
        for part in a.sizes.split(","):
            wh, _at, sc = part.partition("@")
            w, h = (int(v) for v in wh.lower().split("x"))
            sizes.append(((w, h), float(sc or 1.0)))
    tmp = tempfile.mkdtemp(prefix="cd3_screens_")
    os.environ["MANTICORE_DATA_DIR"] = os.path.join(tmp, "data")
    if not a.fetch:
        os.environ["MANTICORE_OFFLINE"] = "1"
    import pygame
    import cube_source as cs
    import draft_pod as dp
    import draft_screens as ds
    import forge_client as fc
    import forge_table as ft
    cubes = os.path.join(tmp, "cubes")
    data, _fetched = cs.read_stock("devoid")                  # a "pasted" cube: a stock list kept under another id
    cs.save_cache("pastedcube", data, cubes)
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
    stock_i = next(i for i, st in enumerate(cs.STOCK_CUBES) if st.key == a.cube)

    def shot(gui, name, waits=2):
        for _ in range(waits if not a.fetch else max(waits, 30)):
            gui.sync()
            gui.render()
            if a.fetch:
                pygame.time.wait(150)
        pygame.image.save(gui.screen, os.path.join(a.out, f"{tag}_{name}.png"))

    for size, scale in sizes:
        tag = f"{size[0]}x{size[1]}_{int(scale * 100)}"
        gui = ft.ForgeTable(fc.ForgeSession("", []), store, settings_path=None, window_size=size, launcher=Launcher(),
                            deck_dirs=dirs)
        gui.text_scale = scale
        gui.open_boot(splash=False)
        gui.sync()
        gui.boot = None
        gui.open_menu()
        flow = ds.DraftFlow(gui, from_title=True, root=os.path.join(tmp, "pods"), lookup=lookup, cube_folder=cubes)
        gui.modal = flow
        flow.job.thread.join()
        shot(gui, "1_setup_default")
        flow.press(gui, f"stock_{stock_i}")
        flow.job.thread.join()
        shot(gui, "2_setup_stock")
        flow.field.text = "pastedcube"
        flow.load_cube()
        flow.job.thread.join()
        shot(gui, "3_setup_pasted")
        flow.field.text = ""
        flow.press(gui, f"stock_{stock_i}")
        flow.job.thread.join()
        flow.pod = dp.Pod.new(flow.job.cube, flow.job.check, seed=a.seed, root=os.path.join(tmp, "pods"), style=flow.style())
        flow.to_pick(gui)
        for _ in range(4):
            flow.pod.pick(flow.pod.chooser(0, flow.pod.pack(), flow.pod.pool(), flow.pod.needed()))
        gui.render()
        flow.selected = [flow.pod.pack()[0]]
        gui.mouse = flow.tiles[3][0].center if len(flow.tiles) > 3 else (0, 0)
        shot(gui, "4_pick")
        while not flow.pod.done:
            flow.pod.pick(flow.pod.chooser(0, flow.pod.pack(), flow.pod.pool(), flow.pod.needed()))
        flow.to_review()
        gui.mouse = (0, 0)
        gui.render()
        gui.mouse = flow.tiles[1][0].center if len(flow.tiles) > 1 else (0, 0)
        shot(gui, "5_review")
        flow.to_decks(gui)
        flow.press(gui, "versus_2")
        gui.mouse = (0, 0)
        shot(gui, "6_decks")
        flow.save_decks(gui)
        shot(gui, "7_deck_screen")
        shutil.rmtree(dirs[0], ignore_errors=True)
        with mock.patch.object(ft.pygame, "quit"):
            gui.shutdown()
        print(tag, "done")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
