# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/bench_frame.py - how long the Forge table takes to draw one frame, without Java and without a real window.

    python tools/bench_frame.py                              the standard scenes at 1360x840, 1920x1080 and Karl's 4096x1949 @ 175 %
    python tools/bench_frame.py --size 4096x1949 --scale 1.75 --frames 240
    python tools/bench_frame.py --profile                    also print where the time goes (cProfile, top 20)

It builds ForgeTable on the saved snapshots in tests/fixtures/forge_states (the same FakeSession the GUI tests use), warms up 5 frames,
puts the mouse over the board, and times sync() + render() for each frame. Numbers from a headless run leave out the real screen's
present / vsync time, so on a real 4K window expect somewhat more. Use it to compare before / after a change on the SAME machine.
"""
import argparse
import copy
import cProfile
import os
import pstats
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import forge_table as ft                                      # noqa: E402  (after the SDL settings)
from tests.forge_fake import FakeSession, StubStore, load_log, load_state      # noqa: E402

SCENES = ["main1_start", "stack_two_late", "declare_blockers", "combat_damage"]      # combat_damage has a dialog open
SIZES = [((1360, 840), 1.0), ((1920, 1080), 1.0), ((4096, 1949), 1.75)]


def pod(state, extra=2):
    """The same board with `extra` more opponents (a 4-player pod when extra=2)."""
    st = copy.deepcopy(load_state(state))
    base = [p for p in st["players"] if p["id"] != st["me"]][0]
    for k in range(1, extra + 1):
        p = copy.deepcopy(base)
        p["id"], p["name"] = 10 + k, f"AI {k + 1}"
        for z in p["zones"].values():
            for c in z:
                c["id"] += 1000 * k
        st["players"].append(p)
    return st


def measure(scene, size, scale, frames=120, profile=False):
    st = scene if isinstance(scene, dict) else load_state(scene)
    gui = ft.ForgeTable(FakeSession(st, load_log()), StubStore(), window_size=size)
    gui.text_scale = scale
    for _ in range(5):
        gui.sync()
        gui.render()
    gui.mouse = (size[0] // 3, size[1] * 2 // 3)
    prof = cProfile.Profile() if profile else None
    times = []
    if prof:
        prof.enable()
    for _ in range(frames):
        t0 = time.perf_counter()
        gui.sync()
        gui.render()
        times.append((time.perf_counter() - t0) * 1000)
    if prof:
        prof.disable()
    times.sort()
    out = {"p50": times[len(times) // 2], "p95": times[int(len(times) * 0.95)], "max": times[-1], "dialog": gui.modal is not None}
    return out, prof


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", help="WxH, e.g. 4096x1949")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--pod", action="store_true", help="also time a 4-player pod")
    ap.add_argument("--profile", action="store_true")
    a = ap.parse_args(argv)
    sizes = [(tuple(int(v) for v in a.size.lower().split("x")), a.scale)] if a.size else SIZES
    scenes = list(SCENES) + ([("pod", pod("stack_two_late"))] if a.pod else [])
    for size, scale in sizes:
        for sc in scenes:
            name, st = sc if isinstance(sc, tuple) else (sc, sc)
            r, prof = measure(st, size, scale, a.frames, a.profile)
            print(f"{name:18s} {size[0]}x{size[1]} @{int(scale * 100)}%  p50 {r['p50']:6.2f} ms  p95 {r['p95']:6.2f} ms  max {r['max']:6.2f} ms"
                  + ("   (dialog open)" if r["dialog"] else ""))
            if prof:
                pstats.Stats(prof).sort_stats("tottime").print_stats(20)


if __name__ == "__main__":
    main()
