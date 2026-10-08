# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/ui6_screens.py - the patch UI6 review pictures (Log and Focus modes), made without Java and without a real window.

    python tools/ui6_screens.py [OUT_FOLDER]      default: docs/incoming/ui6/screens

At 1360x840 and 1920x1080, text 100% and 200%, on the recorded "stack_two_late" position (a stack of two, the opponent's log):
  always      Log: Always,  Focus: Always      (today's table - the reference)
  corner      Log: Corner,  Focus: Always      (the mouse in the lower-right corner: the log over the stack)
  overcard    Log: Always,  Focus: Over card   (the mouse resting on a battlefield card)
  overhand    Log: Always,  Focus: Over card   (the mouse resting on a card in the hand: the picture goes above the hand)
  minimal     Log: Corner,  Focus: Over card   (the narrowest column; the stack gets the room)
  hidden      Log: Hidden,  Focus: Hidden      (nothing but the stack in the column; a right-click pin shows the card over itself)
Card pictures come from the local cache only (a stand-in face where there is none). The corner log and the over-card picture are
timed by a clock the script moves by hand, so each picture shows the mode as it looks after the delay.
"""
import copy
import os
import sys
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("MANTICORE_SKIP_LIVE", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MANTICORE_DATA_DIR", tempfile.mkdtemp(prefix="ui6_screens_"))

import pygame  # noqa: E402

import forge_table as ft  # noqa: E402
from tests.forge_fake import FakeSession, StubStore, load_log, load_state  # noqa: E402

SIZES = (((1360, 840), 1.0), ((1360, 840), 2.0), ((1920, 1080), 1.0), ((1920, 1080), 2.0))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def frames(gui, n=3):
    for _ in range(n):
        gui.sync()
        gui.render()


def make(size, scale, log, preview):
    gui = ft.ForgeTable(FakeSession(copy.deepcopy(load_state("stack_two_late")), load_log()), StubStore(), settings_path=None,
                        window_size=size)
    gui.text_scale = scale
    gui.animations = False
    gui.current_bg = "citadel"
    gui.log_mode, gui.preview_mode = log, preview
    gui.ui_clock = Clock()
    frames(gui, 3)
    return gui


def point_on(gui, cid):
    for rect, kind, data in reversed(gui.hits):
        if kind == "card" and data["card"]["id"] == cid:
            for dx in range(2, rect.w, 4):
                for dy in range(2, rect.h, 4):
                    p = (rect.x + dx, rect.y + dy)
                    k, d = gui.hit_at(p)
                    if k == "card" and d["card"]["id"] == cid:
                        return p
    raise RuntimeError(f"card {cid} is not clickable on screen")


def hover(gui, pos, seconds=0.5):
    for wait in (0.0, seconds):
        gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0)))
        gui.ui_clock.advance(wait)
        frames(gui, 1)


def shot(gui, out, name):
    frames(gui, 1)
    pygame.image.save(gui.screen, os.path.join(out, name + ".png"))
    print(name)


def battlefield_pick(gui):
    me = gui.session.me()
    cands = [c for c in me["zones"]["battlefield"] if c["id"] in gui.card_rects and not c.get("isLand")]
    return cands[0] if cands else next(c for c in me["zones"]["battlefield"] if c["id"] in gui.card_rects)


def main(argv):
    out = argv[1] if len(argv) > 1 else os.path.join(ROOT, "docs", "incoming", "ui6", "screens")
    os.makedirs(out, exist_ok=True)
    for size, scale in SIZES:
        tag = f"{size[0]}x{size[1]}_{int(scale * 100)}"
        gui = make(size, scale, "always", "always")
        shot(gui, out, f"always_{tag}")
        gui = make(size, scale, "corner", "always")
        hover(gui, gui.L.hot.center)
        shot(gui, out, f"corner_{tag}")
        gui = make(size, scale, "always", "over_card")
        hover(gui, point_on(gui, battlefield_pick(gui)["id"]))
        shot(gui, out, f"overcard_{tag}")
        gui = make(size, scale, "always", "over_card")
        hover(gui, point_on(gui, gui.session.me()["zones"]["hand"][0]["id"]))
        shot(gui, out, f"overhand_{tag}")
        gui = make(size, scale, "corner", "over_card")
        hover(gui, point_on(gui, battlefield_pick(gui)["id"]))
        shot(gui, out, f"minimal_{tag}")
        gui = make(size, scale, "hidden", "hidden")
        cid = battlefield_pick(gui)["id"]
        p = point_on(gui, cid)
        gui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=p, button=3))           # right-click: pin
        gui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(3, size[1] // 2), rel=(0, 0), buttons=(0, 0, 0)))
        frames(gui, 2)
        shot(gui, out, f"hidden_{tag}")
    print("done:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
