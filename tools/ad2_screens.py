# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/ad2_screens.py - the Round AD2 / AD2b / AD2c review pictures, made without Java and without a real window.

    python tools/ad2_screens.py [OUT_FOLDER]          default: docs/incoming/ad2/screens

Commander VS (2 and 4 players, partners), the opening hand (keep or mulligan; put on the bottom), VICTORY, DEFEAT and "You are out",
at 1360x840 and 1920x1080, text scale 1.0 (and 2.0 for the hand and the result). Card pictures come from the local cache; with
--fetch the missing ones are downloaded from Scryfall first (the normal politeness: one request at a time).
"""
import copy
import os
import sys
import tempfile
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("MANTICORE_SKIP_LIVE", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MANTICORE_DATA_DIR", tempfile.mkdtemp(prefix="ad2_screens_"))

import pygame  # noqa: E402

import flow_screens as flow  # noqa: E402
import forge_table as ft  # noqa: E402
from tests.forge_fake import FakeSession, StubStore, load_log, load_state  # noqa: E402

VS4 = [("You", ["Tymna the Weaver", "Kraum, Ludevic's Opus"], "Blue Farm"), ("AI 1", ["Kinnan, Bonder Prodigy"], "Kinnan NBC"),
       ("AI 2", ["Teysa Karlov"], "Aristocrats (Teysa)"), ("AI 3", ["Light-Paws, Emperor's Voice"], "Voltron (Light-Paws)")]


def frames(gui, n=3):
    for _ in range(n):
        gui.sync()
        gui.render()


def settle(gui, seconds=20.0):
    """Draw until the card pictures this screen asked for have arrived (or the time is up)."""
    end = time.time() + seconds
    while time.time() < end:
        frames(gui, 1)
        if gui.art and gui.art.pending() == 0:
            frames(gui, 2)
            return
        time.sleep(0.2)


def save(gui, out, name):
    settle(gui)
    if gui.end_screen is not None:
        gui.end_screen.t0 -= 5                       # the finished picture, not the first frame of the fade-in
        frames(gui, 2)
    pygame.image.save(gui.screen, os.path.join(out, name + ".png"))
    print(name)


def make(state, size, scale, store):
    gui = ft.ForgeTable(FakeSession(state, load_log() if state else None), store, settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.current_bg = "citadel"
    frames(gui, 2)
    return gui


def main(argv):
    fetch = "--fetch" in argv
    args = [a for a in argv if not a.startswith("--")]
    out = args[0] if args else os.path.join(ROOT, "docs", "incoming", "ad2", "screens")
    os.makedirs(out, exist_ok=True)
    if fetch:
        from card_data import CardDataStore
        store = CardDataStore()
    else:
        store = StubStore()
    for size in ((1360, 840), (1920, 1080)):
        tag = f"{size[0]}x{size[1]}"
        for n in (2, 4):
            gui = make(None, size, 1.0, store)
            gui.vs = flow.VsShow(VS4[:n] if n == 4 else [VS4[1], ("AI", ["Teysa Karlov"], "Aristocrats (Teysa)")])
            save(gui, out, f"vs_{n}p_{tag}")
        for scale in ((1.0, 2.0) if size == (1920, 1080) else (1.0,)):
            gui = make(load_state("mulligan"), size, scale, store)
            save(gui, out, f"mulligan_{tag}_{scale}")
            st = copy.deepcopy(load_state("mulligan"))
            me = [p for p in st["players"] if p["id"] == st["me"]][0]
            st["prompt"] = {"message": "Return 2 card(s) to the bottom of your library", "ok": {"label": "OK", "enabled": False},
                            "cancel": {"label": "Auto", "enabled": True}, "selecting": True, "selMin": 2, "selMax": 2}
            for i, c in enumerate(me["zones"]["hand"]):
                c["selectable"] = True
                c["highlight"] = i == 1
            gui = make(st, size, scale, store)
            gui.mulligans = 2
            save(gui, out, f"bottom_{tag}_{scale}")
            for winner, word in (("Karl", "victory"), ("AI 1 (Kinnan)", "defeat")):
                st = copy.deepcopy(load_state("combat_damage"))
                st["gameOver"], st["winner"], st["turn"] = True, winner, 9
                gui = make(st, size, scale, store)
                save(gui, out, f"{word}_{tag}_{scale}")
        # Round AD2: the studio splash, the title and main menu, and a framed dialog over the table
        import boot_screens
        gui = make(None, size, 1.0, store)
        gui.open_boot(True)
        gui.boot.t0 -= 5
        gui.boot.stage = "splash"
        frames(gui, 2)
        pygame.image.save(gui.screen, os.path.join(out, f"splash_{tag}.png"))
        gui.boot.to_title(time.monotonic() - 5)
        frames(gui, 2)
        pygame.image.save(gui.screen, os.path.join(out, f"title_{tag}.png"))
        print(f"splash_{tag}", f"title_{tag}")
        gui = make(load_state("main1_lands"), size, 1.0, store)
        settle(gui)
        gui.open_help()
        frames(gui, 2)
        pygame.image.save(gui.screen, os.path.join(out, f"frames_help_{tag}.png"))
        gui.modal = None
        gui.overlay = None
        frames(gui, 2)
        pygame.image.save(gui.screen, os.path.join(out, f"frames_table_{tag}.png"))
        print(f"frames_help_{tag}", f"frames_table_{tag}")
        # Round AD2c: an AI spell in the spotlight, a damage number rising off a creature, and the turn banner (caught mid-way)
        import anim
        import events
        gui = make(load_state("main1_lands"), size, 1.0, store)
        opp = gui.session.opponents()[0]
        now = time.monotonic()
        cid = opp["zones"]["battlefield"][0]["id"]
        mine = next(c for c in gui.session.me()["zones"]["battlefield"] if not c.get("isLand"))["id"]
        gui.anim.feed(gui, [events.Beat({"kind": "cast", "card": cid, "seq": 0}, now),
                            events.Beat({"kind": "damage_card", "card": mine, "amount": 3, "seq": 0}, now)], now)
        gui.anim.watch(gui, now)
        settle(gui)
        t = time.monotonic()
        gui.anim.spot.t0, gui.anim.spot.end = t - 0.3, t + 5
        gui.anim.floats = [(f[0], f[1], f[2], f[3], t - 0.25) for f in gui.anim.floats]
        gui.anim.banner = (f"{gui.short_player(opp['name'])}'s turn", "Turn 3", t - 0.3)
        frames(gui, 1)
        pygame.image.save(gui.screen, os.path.join(out, f"animations_{tag}.png"))
        print(f"animations_{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
