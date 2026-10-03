# SPDX-License-Identifier: GPL-3.0-or-later
"""
tools/alpha_screens.py - one picture of every screen an alpha tester sees (alpha must-have 16: "Karl reviews screenshots of every
screen a friend sees, at 1080p and at 200% text"), made without Java and without a real window.

    python tools/alpha_screens.py [OUT_FOLDER] [--fetch] [--sizes 1920x1080@1.0,1920x1080@2.0]

The order follows a first game: splash, title, menu, deck screen (and its paste-a-deck window), VS, the opening hand, the tour,
the table at each step (main phase, attacking, blocking, paying, the stack, 4 players), every kind of question Forge asks (choose,
library search, confirm, order, damage assignment), the cog and everything it opens, Victory / Defeat and the end-of-game window.
Recorded snapshots stand in for Forge. --fetch downloads the card pictures from Scryfall first (one request at a time).
Writes OUT_FOLDER/index.txt listing each picture with what it shows.
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
os.environ.setdefault("MANTICORE_DATA_DIR", tempfile.mkdtemp(prefix="alpha_screens_"))

import pygame  # noqa: E402

import flow_screens as flow  # noqa: E402
import forge_settings as fset  # noqa: E402
import forge_table as ft  # noqa: E402
from tests.forge_fake import FakeSession, StubStore, load_log, load_state  # noqa: E402
from tools.ad2_screens import VS4, frames, settle  # noqa: E402

os.environ.pop("MANTICORE_NO_TOUR", None)

INDEX = []


def shot(gui, out, tag, n, name, what, wait=True):
    if wait:
        settle(gui)
    if gui.end_screen is not None:
        gui.end_screen.t0 -= 5
    frames(gui, 2)
    fn = f"{n:02d}_{name}_{tag}.png"
    pygame.image.save(gui.screen, os.path.join(out, fn))
    INDEX.append((fn, what))
    print(fn)


def pod(state, n):
    """The same snapshot with n players: copies of the first opponent with fresh ids (as the deck screen's tests do)."""
    st = copy.deepcopy(load_state(state) if isinstance(state, str) else state)
    base = [p for p in st["players"] if p["id"] != st["me"]][0]
    st["players"] = [p for p in st["players"] if p["id"] == st["me"]]
    for k in range(n - 1):
        p = copy.deepcopy(base)
        p["id"], p["name"] = 20 + k, f"AI {k + 1}"
        for z in p["zones"].values():
            for c in z:
                c["id"] += 1000 * (k + 1)
        st["players"].append(p)
    return st


def table(state, size, scale, store, players=None):
    st = pod(state, players) if players else (load_state(state) if isinstance(state, str) else state)
    gui = ft.ForgeTable(FakeSession(st, load_log()), store, settings_path=None, window_size=size)
    gui.text_scale = scale
    gui.current_bg = "citadel"
    gui.animations = False
    frames(gui, 2)
    return gui


def hand_cards(gui, n=4):
    out = []
    for i, c in enumerate(gui.session.me()["zones"]["hand"][:n]):
        out.append({"kind": "card", "card": copy.deepcopy(c)})
    return out


def run(out, size, scale, store):
    tag = f"{size[0]}x{size[1]}_{int(scale * 100)}pct"
    n = 0

    def nxt():
        nonlocal n
        n += 1
        return n

    # ---- starting the program ----
    gui = table(None, size, scale, store)
    gui.open_boot(True)
    gui.boot.t0 -= 0.8
    shot(gui, out, tag, nxt(), "splash", "Studio splash (2.2 s; a click skips it)", wait=False)
    gui.boot.to_title(time.monotonic() - 5)
    shot(gui, out, tag, nxt(), "title", "Title: the picture alone, 'Press any key'", wait=False)
    if hasattr(gui.boot, "to_menu"):
        gui.boot.to_menu(time.monotonic() - 5)
    gui.boot.focus = 0
    shot(gui, out, tag, nxt(), "menu", "Main menu: Play / Continue / Settings / Credits / Quit", wait=False)

    # ---- the deck screen ----
    from tests.test_deck_screen import FakeLauncher
    lib = tempfile.mkdtemp(prefix="alpha_decks_")
    gui = ft.ForgeTable(FakeSession(None, None), store, settings_path=None, window_size=size, launcher=FakeLauncher(),
                        deck_dirs=(lib, os.path.join(ROOT, "sample_decks"), ROOT))
    gui.text_scale = scale
    gui.current_bg = "graveyard"
    frames(gui, 2)
    gui.open_menu()
    frames(gui, 2)
    shot(gui, out, tag, nxt(), "decks", "Deck screen: the five starter decks, your deck and one box per AI")
    gui.menu.crash_offer = "Manticore closed unexpectedly last time."
    shot(gui, out, tag, nxt(), "decks_crash_offer", "Deck screen after a crash: the report offer (Send report / Look first / Not now)")
    gui.menu.crash_offer = ""
    gui.menu.open_import(gui, "1 Kinnan, Bonder Prodigy\n1 Basalt Monolith\n1 Forest\n")
    shot(gui, out, tag, nxt(), "paste_deck", "Paste a deck (Moxfield / Archidekt text)")
    gui.modal = None
    try:                                                                 # Round ALT1: Card art, the grid and one card's printings
        import art_picker
        kin = next(e for e in gui.menu.entries if "Kinnan" in e.name)
        gui.menu.assign(kin)
        gui.modal = art_picker.ArtPicker(gui.menu, kin)
        shot(gui, out, tag, nxt(), "card_art", "Card art: every card of the deck (a gold dot = a chosen printing)")
        gui.modal.open_card("Sol Ring")
        for _ in range(60):                                              # the list of printings comes from Scryfall
            frames(gui, 1)
            if gui.art is None or gui.art.printings("Sol Ring") is not None:
                break
            time.sleep(0.25)
        for _ in range(30):                                              # the small pictures of the visible printings
            frames(gui, 1)
            time.sleep(0.2)
        shot(gui, out, tag, nxt(), "card_art_printings", "Card art: the printings of one card (Default first)")
    except Exception as e:
        INDEX.append(("(skipped) card_art", f"could not be opened offline ({e.__class__.__name__}: {e})"))
    gui.modal = None

    # ---- the game starts ----
    gui = table(None, size, scale, store)
    gui.vs = flow.VsShow(VS4)
    shot(gui, out, tag, nxt(), "vs_4p", "Commander VS commander while Forge starts (4 players)")
    gui = table("mulligan", size, scale, store)
    shot(gui, out, tag, nxt(), "opening_hand", "The opening hand: Mulligan or Keep")
    st = copy.deepcopy(load_state("mulligan"))
    me = [p for p in st["players"] if p["id"] == st["me"]][0]
    st["prompt"] = {"message": "Return 1 card(s) to the bottom of your library", "ok": {"label": "OK", "enabled": False},
                    "cancel": {"label": "Auto", "enabled": True}, "selecting": True, "selMin": 1, "selMax": 1}
    for c in me["zones"]["hand"]:
        c["selectable"] = True
    gui = table(st, size, scale, store)
    gui.mulligans = 1
    shot(gui, out, tag, nxt(), "put_on_bottom", "After a mulligan: choose the card to put on the bottom")

    # ---- the tour ----
    gui = table("main1_lands", size, scale, store)
    gui.launcher = object()
    settle(gui)
    if hasattr(gui, "maybe_start_tour"):
        gui.maybe_start_tour()
    if getattr(gui, "tour", None) is not None:
        shot(gui, out, tag, nxt(), "tour_first_step", "The first-game tour, step 1 (Esc skips it)")
        gui.tour = None

    # ---- the table ----
    for state, name, what, players in (
            ("main1_start", "table_turn1", "Turn 1, your main phase", None),
            ("main1_lands", "table_main", "A few turns in: your main phase", None),
            ("declare_attackers", "table_attack", "Declaring attackers", None),
            ("declare_blockers", "table_block", "Declaring blockers", None),
            ("paying_mana", "table_paying", "Paying a spell's mana cost", None),
            ("stack_two_late", "table_stack", "Late game with two spells on the stack", None),
            ("main1_lands", "table_4p", "A 4-player pod", 4)):
        gui = table(state, size, scale, store, players)
        shot(gui, out, tag, nxt(), name, what)

    # ---- Forge's questions ----
    gui = table("main1_lands", size, scale, store)
    settle(gui)
    for req, name, what in (
            ({"id": 1, "kind": "choose", "title": "Choose a card to discard", "min": 1, "max": 1, "items": hand_cards(gui)},
             "q_choose", "Forge asks you to pick a card"),
            ({"id": 2, "kind": "choose_optional", "title": "Select a card from your library", "min": 0, "max": 1,
              "items": hand_cards(gui, 6)}, "q_search", "A library search (Find nothing is a separate button)"),
            ({"id": 3, "kind": "confirm", "title": "Return your commander to the command zone?", "default": True,
              "options": ["Yes", "No"]}, "q_confirm", "A yes / no question"),
            ({"id": 4, "kind": "order", "title": "Order the triggered abilities", "min": 3, "max": 3, "items": hand_cards(gui, 3)},
             "q_order", "Putting triggers in order")):
        gui.modal = None
        gui.session.requests.append(req)
        frames(gui, 2)
        shot(gui, out, tag, nxt(), name, what)
        gui.session.requests.clear()
        gui.modal = None
        frames(gui, 1)

    # ---- the cog and what it opens ----
    gui = table("main1_lands", size, scale, store)
    gui.launcher = FakeLauncher()                                 # New game... asks "same decks or pick decks" only with a launcher
    gui.current_decks = (None, [None])
    settle(gui)
    gui.overlay = fset.SettingsPopup()
    shot(gui, out, tag, nxt(), "cog", "The cog: display, sound, game and help settings")
    gui.overlay = None
    for opener, name, what in ((gui.open_help, "controls", "Controls (H)"), (gui.open_licenses, "licences", "Licences and credits"),
                               (gui.open_report, "report_bug", "Report a bug (F8)"),
                               (lambda: gui.open_report("idea"), "suggest_idea", "Suggest a feature (F8's second tab, Round FR1)"),
                               (gui.ask_new_game, "new_game", "New game..."),
                               (gui.ask_concede, "concede", "Concede...")):
        gui.modal = None
        gui.overlay = None
        try:
            opener()
        except Exception as e:                                  # a screen that needs more than a snapshot: say so, keep going
            INDEX.append((f"(skipped) {name}", f"{what}: could not be opened offline ({e.__class__.__name__}: {e})"))
            continue
        shot(gui, out, tag, nxt(), name, what)
    gui.modal = None
    gui.overlay = None
    opp = gui.session.opponents()[0]
    try:
        gui.open_zone(opp["id"], "graveyard")
        shot(gui, out, tag, nxt(), "zone_graveyard", "Looking through a graveyard")
    except Exception as e:
        INDEX.append(("(skipped) zone_graveyard", f"could not be opened offline ({e.__class__.__name__}: {e})"))
    gui.modal = None

    # ---- the end ----
    for winner, name, what in (("Karl", "victory", "VICTORY"), ("AI 1 (Kinnan)", "defeat", "DEFEAT")):
        st = copy.deepcopy(load_state("combat_damage"))
        st["gameOver"], st["winner"], st["turn"] = True, winner, 9
        gui = table(st, size, scale, store)
        shot(gui, out, tag, nxt(), name, what)
    gui.end_continue()
    shot(gui, out, tag, nxt(), "game_over_window", "The end-of-game window (after Continue)")


def main(argv):
    fetch = "--fetch" in argv
    sizes = [((1920, 1080), 1.0), ((1920, 1080), 2.0)]
    for i, a in enumerate(argv):
        if a == "--sizes" and i + 1 < len(argv):
            sizes = []
            for s in argv[i + 1].split(","):
                wh, sc = s.split("@")
                w, h = wh.split("x")
                sizes.append(((int(w), int(h)), float(sc)))
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--sizes")]
    out = args[0] if args else os.path.join(ROOT, "docs", "incoming", "alpha_screens")
    os.makedirs(out, exist_ok=True)
    if fetch:
        from card_data import CardDataStore
        store = CardDataStore()
    else:
        store = StubStore()
    for size, scale in sizes:
        run(out, size, scale, store)
    with open(os.path.join(out, "index.txt"), "w", encoding="utf-8") as f:
        for fn, what in INDEX:
            f.write(f"{fn}  -  {what}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
