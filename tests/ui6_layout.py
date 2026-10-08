# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UI6 helper: a plain-data picture of the table's layout, so a test can say "this is exactly what it was before".

    python -m tests.ui6_layout --write      (rewrites THIS computer's baseline file in tests/fixtures/ui6/ - run it only in a tree of
                                             the program as it was BEFORE round UI6, never in this one)

The baseline files were written from the program as it was BEFORE round UI6 (patch 48's tree, 0.28.52), one for each kind of computer
(see tests/fixtures/ui6/README.txt for why there are two). The Always-on / Always-on test compares today's layout with its computer's
file, rectangle by rectangle."""
import copy
import json
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

import forge_table as ft
from tests.forge_fake import FakeSession, StubStore, load_log, load_state

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "ui6")
# One recorded layout per kind of computer: an opponent's board in a 4-player game at 200% text starts after its command-zone frame, which is as
# wide as the word "CMD" in the tiny font, and Windows measures that word 1 px differently from Linux (tests/fixtures/ui6/README.txt).
BASELINE_FILES = {"linux": "layout_baseline.json", "win32": "layout_baseline_win32.json"}


def baseline_path(platform=None):
    """The recorded layout for this kind of computer (sys.platform), or None when none was recorded for it."""
    name = BASELINE_FILES.get(platform or sys.platform)
    return os.path.join(FIXTURES, name) if name else None


BASELINE = baseline_path()

# the five window sizes of the brief, at 100% and 200% text where they make sense (the smallest window at 200% is not a size anyone uses)
SIZES = (((900, 600), 1.0), ((1100, 700), 2.0), ((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0),
         ((4096, 1949), 1.75))
RECT_NAMES = ("main", "right", "preview", "caption", "stack", "log", "bar", "hand", "my_bf", "my_info", "cmd_rect")
NUMBER_NAMES = ("right_w", "top_h", "margin", "hand_ch", "my_row_h", "opp_row_h", "bar_h")


def pod_state(players, stack=False):
    """main1_lands with `players` seats (copies of its first opponent, fresh ids); optionally with a stack."""
    st = copy.deepcopy(load_state("main1_lands"))
    base = [p for p in st["players"] if p["id"] != st["me"]][0]
    st["players"] = [p for p in st["players"] if p["id"] == st["me"]]
    for k in range(players - 1):
        p = copy.deepcopy(base)
        p["id"], p["name"] = 20 + k, f"AI {k + 1}"
        for z in p["zones"].values():
            for c in z:
                c["id"] += 1000 * (k + 1)
        st["players"].append(p)
    if stack:
        st["stack"] = copy.deepcopy(load_state("stack_two_late")["stack"])
    return st


def scenes():
    """name -> a fresh snapshot."""
    return {"two_players": copy.deepcopy(load_state("main1_lands")),
            "two_players_stack": copy.deepcopy(load_state("stack_two_late")),
            "four_players": pod_state(4),
            "four_players_stack": pod_state(4, stack=True)}


def make_table(state, size, scale):
    gui = ft.ForgeTable(FakeSession(state, load_log()), StubStore(), settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.animations = False
    return gui


def rect_list(r):
    return [int(r.x), int(r.y), int(r.w), int(r.h)]


def snapshot(gui):
    """The layout the table just drew with, as plain lists: the named rectangles and numbers, the opponents' rows, and every card
    rectangle the frame drew (sorted by id)."""
    L = gui.L
    out = {n: rect_list(getattr(L, n)) for n in RECT_NAMES}
    out.update({n: int(getattr(L, n)) for n in NUMBER_NAMES})
    out["opp_rects"] = [rect_list(r) for r in L.opp_rects]
    out["cards"] = sorted([int(cid), *rect_list(r)] for cid, r in gui.card_rects.items())
    return out


def draw(gui, frames=3):
    for _ in range(frames):
        gui.sync()
        gui.render()


def all_snapshots():
    out = {}
    for sname, state in scenes().items():
        for size, scale in SIZES:
            gui = make_table(state, size, scale)
            draw(gui)
            out[f"{sname}|{size[0]}x{size[1]}|{scale}"] = snapshot(gui)
    return out


if __name__ == "__main__":
    if "--write" in sys.argv:
        if BASELINE is None:
            sys.exit(f"no baseline file is named for {sys.platform!r}: add it to BASELINE_FILES first")
        data = all_snapshots()
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=0, sort_keys=True)
        print(f"wrote {len(data)} layouts to {BASELINE}")
    else:
        print(__doc__)
