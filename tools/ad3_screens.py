# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/ad3_screens.py - the patch 48 (AD3a, Scope B) review pictures, made without Java and without a real window.

    python tools/ad3_screens.py [OUT_FOLDER] [--fetch]      default: docs/incoming/p48/screens

Each picture freezes an effect part-way (its clock is set back a fraction of its length, the particles stepped the same amount):
a trigger's pulse and its line to the stack with a spell's energy on its way; arrivals of every kind with their dust; creatures hit
for 2, 6 and 12 with the knock and the growing numbers and a hit panel; a resolved entry flying to its target beside a fizzled one;
embers off a destroyed permanent and a card draining into exile; the turn banner's embers; VICTORY and DEFEAT a second in.
At 1360x840 and 1920x1080 (text scale 1.0), plus 1920x1080 at 2.0 for the hits. Card pictures come from the local cache; with
--fetch the missing ones are downloaded from Scryfall first.
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
os.environ.setdefault("MANTICORE_DATA_DIR", tempfile.mkdtemp(prefix="ad3_screens_"))

import pygame  # noqa: E402

import anim  # noqa: E402
import events  # noqa: E402
import flow_screens as flow  # noqa: E402
import forge_table as ft  # noqa: E402
from tests.forge_fake import FakeSession, StubStore, load_log, load_state  # noqa: E402


def beat(kind, **data):
    return events.Beat(dict(data, kind=kind, seq=0), time.monotonic())


def frames(gui, n=3):
    for _ in range(n):
        gui.sync()
        gui.render()


def settle(gui, seconds=20.0):
    end = time.time() + seconds
    while time.time() < end:
        frames(gui, 1)
        if gui.art and gui.art.pending() == 0:
            frames(gui, 2)
            return
        time.sleep(0.2)


def make(state, size, scale, store, bg="citadel"):
    gui = ft.ForgeTable(FakeSession(state, load_log() if state else None), store, settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.current_bg = bg
    frames(gui, 2)
    settle(gui)
    return gui


def step_particles(gui, seconds, steps=6):
    for _ in range(steps):
        gui.particles.update(seconds / steps)
        gui.front_particles.update(seconds / steps)


def shot(gui, out, name):
    frames(gui, 1)
    pygame.image.save(gui.screen, os.path.join(out, name + ".png"))
    print(name)


def creature(player, index=0):
    return [c for c in player["zones"]["battlefield"] if c.get("isCreature")][index]


def scene_stack(gui, out, tag):
    """stack_two_late: City of Brass's trigger pulses and links to its row; Basalt Monolith's energy flies to the stack."""
    now = time.monotonic()
    stack = gui.state["stack"]
    city = [it for it in stack if it["card"]["name"] == "City of Brass"][0]["card"]
    opp = gui.session.opponents()[0]
    gui.anim.feed(gui, [beat("cast", card=city["id"], trigger=True)], now - 0.2)
    gui.anim.cast_energy(gui, {"name": "x"}, opp["id"])
    step_particles(gui, 0.12)
    shot(gui, out, f"stack_trigger_{tag}")


def scene_arrivals(gui, out, tag):
    """main1_lands: the auras of every kind on the opponent's and my permanents, dust 0.12 s in."""
    now = time.monotonic()
    kinds = {}
    for p in gui.state["players"]:
        for c in p["zones"]["battlefield"]:
            k = anim.arrival_kind(c)
            kinds.setdefault(k, c)
    beats = []
    for k, c in kinds.items():
        beats.append(beat("zone", card=c["id"], **{"from": "Hand", "to": "Battlefield"}))
    gui.anim.feed(gui, beats, now - 0.12)
    gui.anim.draw_auras(gui, now - 0.12 + 0.01)                        # the dust is thrown
    step_particles(gui, 0.12)
    shot(gui, out, f"arrivals_{tag}")
    print("   kinds shown:", ", ".join(f"{k} ({c['name']})" for k, c in kinds.items()))


def scene_hits(gui, out, tag):
    """declare_blockers: three creatures hit for 2, 6 and 12 (the knock, the growing numbers, embers), the opponent's panel hit."""
    now = time.monotonic()
    me = gui.session.me()
    opp = gui.session.opponents()[0]
    targets = [c for p in (opp, me) for c in p["zones"]["battlefield"] if c.get("isCreature")][:3]
    beats = [beat("damage_card", card=c["id"], amount=a) for c, a in zip(targets, (2, 6, 12))]
    beats.append(beat("damage_player", player=opp["id"], amount=8))
    gui.anim.feed(gui, beats, now - 0.08)
    gui.anim.draw_hits(gui, now - 0.07)
    step_particles(gui, 0.08)
    gui.anim.feed(gui, [beat("counters", card=targets[0]["id"], counter="P1P1", old=0, new=2)], now - 0.12)
    shot(gui, out, f"hits_{tag}")


def scene_resolve(gui, out, tag):
    """stack_two_late with a target added: Basalt Monolith's entry flies to the opponent's creature; City of Brass fizzles."""
    now = time.monotonic()
    opp = gui.session.opponents()[0]
    st = copy.deepcopy(gui.state)
    target = creature(opp)
    for it in st["stack"]:
        if it["card"]["name"] == "Basalt Monolith":
            it["targets"] = [f"c{target['id']}"]
    gui.session.handle(st)
    frames(gui, 2)
    gui.anim.watch(gui, now)
    mono = [it for it in st["stack"] if it["card"]["name"] == "Basalt Monolith"][0]["card"]["id"]
    city = [it for it in st["stack"] if it["card"]["name"] == "City of Brass"][0]["card"]["id"]
    gui.anim.feed(gui, [beat("resolve", card=mono, fizzled=False), beat("resolve", card=city, fizzled=True)], now - 0.25)
    for k in range(6):                                                  # the comet's trail so far
        gui.anim.draw_links(gui, now - 0.25 + 0.04 * k)
        step_particles(gui, 0.04, 1)
    shot(gui, out, f"resolve_{tag}")


def scene_ghosts(gui, out, tag):
    """main1_lands: one opponent creature destroyed (embers), one exiled (draining into the void), 0.1 s in."""
    opp = gui.session.opponents()[0]
    cs = [c for c in opp["zones"]["battlefield"] if c.get("isCreature")]
    a, b = cs[0], cs[min(1, len(cs) - 1)]
    gui.spawn_ghost(a["id"], gui.card_rects[a["id"]], gui.zone_tile_rect(opp["id"], "graveyard"), ft.GHOST_SECONDS, "red")
    gui.spawn_ghost(b["id"], gui.card_rects[b["id"]], gui.zone_tile_rect(opp["id"], "exile"), ft.GHOST_SECONDS, "white")
    for g in gui.ghosts:
        g.t0 -= 0.1
    step_particles(gui, 0.1)
    shot(gui, out, f"destroy_exile_{tag}")


def scene_banner(gui, out, tag):
    now = time.monotonic()
    opp = gui.session.opponents()[0]
    gui.anim.banner = (f"{gui.short_player(opp['name'])}'s turn", "Turn 5", now - 0.45)
    for k in range(12):                                                 # a few frames of embers
        gui.anim.draw_banner(gui, now - 0.45 + 0.03 * k)
        step_particles(gui, 0.03, 1)
    shot(gui, out, f"banner_{tag}")


def scene_end(gui, out, tag, kind):
    now = time.monotonic()
    gui.end_screen = flow.EndScreen(kind, "You won on turn 9." if kind == "won" else "AI 1 won on turn 9.")
    gui.end_screen.t0 = now - 1.5
    for k in range(90):                                                 # a second and a half of embers / ash
        gui.anim.end_particles(gui, gui.end_screen, now - 1.5 + k / 60)
        gui.front_particles.update(1 / 60)
    shot(gui, out, f"{'victory' if kind == 'won' else 'defeat'}_{tag}")


def main(argv):
    fetch = "--fetch" in argv
    args = [a for a in argv if not a.startswith("--")]
    out = args[0] if args else os.path.join(ROOT, "docs", "incoming", "p48", "screens")
    os.makedirs(out, exist_ok=True)
    if fetch:
        from card_data import CardDataStore
        store = CardDataStore()
    else:
        store = StubStore()
    for size in ((1360, 840), (1920, 1080)):
        tag = f"{size[0]}x{size[1]}"
        scene_stack(make(load_state("stack_two_late"), size, 1.0, store), out, tag)
        scene_arrivals(make(load_state("main1_lands"), size, 1.0, store, "graveyard"), out, tag)
        scene_hits(make(load_state("declare_blockers"), size, 1.0, store, "ruins"), out, tag)
        scene_resolve(make(load_state("stack_two_late"), size, 1.0, store), out, tag)
        scene_ghosts(make(load_state("main1_lands"), size, 1.0, store, "cathedral"), out, tag)
        scene_banner(make(load_state("main1_lands"), size, 1.0, store), out, tag)
        for kind in ("won", "lost"):
            scene_end(make(load_state("combat_damage"), size, 1.0, store), out, tag, kind)
    scene_hits(make(load_state("declare_blockers"), (1920, 1080), 2.0, store, "ruins"), out, "1920x1080_2.0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
