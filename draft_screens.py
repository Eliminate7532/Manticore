# SPDX-License-Identifier: GPL-3.0-or-later
"""
draft_screens.py - Round CD2: the cube draft window, from the main menu's Draft (Karl, 9 Oct 2026 23:05: "build a main menu option
for draft"; the scope he chose: "Choose cube -> pick 2 at a time vs 3 AIs -> review -> your deck Auto-built -> all 4 decks saved to
My decks and chosen on the deck screen -> Start. Hand deck-editing (CD4) comes later.").

One window over the deck screen (as the Card art window is), in four stages:
  setup   the cube: the default (Commander Cube by tritoch) or a Cube Cobra link or id, loaded in the background (cube_source: the
          copy kept on this computer, else Cube Cobra). The cards this Forge can't play are left out and named. An unfinished
          draft is offered here: Continue it / Discard it.
  pick    the pack in front of you across the window. Click two cards (or what is left of the pack), then Confirm (Enter) - Arena's
          way: "picks must be confirmed rather than just clicking". A third click swaps out the first card you clicked. The AI
          drafters pick at once (draft_core.run_ai: 0.02-0.05 s for a whole draft, measured 9 Oct - no thread needed). The header
          says "Pack 2, pick 3 of 10" and where your pack goes (903.13c); of the others you see only how many cards they have
          (903.13d). The card under the mouse is shown big at the right, over your picks so far (commanders, colours, curve).
  review  after the last pick: each seat's picks in order, with the pack each came from - the cards taken in gold (Claude's
          default in the spec: seeing the AIs' picks is how you learn to read the signals).
  decks   every seat's deck built by draft_deck.build (the AIs avoid the cards Forge marks AI:RemoveDeck:All). "Save the decks"
          writes the four to My decks as Commander Draft decks, chooses them on the deck screen - yours in YOUR DECK, AI 1-3 in
          seat order, three opponents - and closes the window: Start plays the 4-player game (Commander rules, 40 life - 903.13g).
The draft is kept after every pick (draft_pod.py). "Save and leave" (Esc) goes back to where Draft was opened (the main menu).

Round CD3 (Karl, 12 Oct 2026): the stock cubes - a row of buttons on the setup stage, one per cube_source.STOCK_CUBES entry, each
loaded from its bundled list (no network), with its owner and Cube Cobra link said under it; the link box stays for any other cube.
The classic draft for the classic cubes (style "classic"): one card a pick from packs of 15, then 40-card decks and a 1v1 game
against the AI drafter you choose on the decks stage ("Play against: AI 1 / AI 2 / AI 3") - all four decks are still saved to My
decks (Draft decks, listed under Standard with the DRAFT tag) and yours and the chosen AI's are chosen on the deck screen. A stock
cube's style is its own; a pasted cube's is cube_source.guess_style's, which the "Draft style" buttons change. A cube with a house
rule says so (Turbo Cube's emblem: house_rules.py) and its decks carry it into the game.
"""
import threading
import time

import pygame

import art_loader
import crashlog
import cube_source as cs
import draft_builder as db
import draft_core as dc
import draft_deck as dd
import draft_pod as dp
import forge_dialogs as dlg
import gfx
import house_rules
from gfx import DIM, GOLD, GREEN, ORANGE, RED, WHITE, clip_text, draw_text, round_rect, wrap_text

CARD_ASPECT = 488 / 680
CURVE_TOP = 7                            # the curve's last bar is "7+"
NO_COMMANDER_WORRY = 24                  # from this many cards drafted, "no commander yet" is said in orange
MAX_CARD_W = 300                         # a pick card's width at 100% text, at most (the last picks of a pack hold 2 cards)
SIDE_SHARE = 0.27                        # the pick screen's right column (the big card and your picks), of the window's width
TITLE = "Cube draft"
INTRO = ("You and three AI drafters each open a pack of 20 cards, take two, and pass the rest - left, then right, then left. Three "
         "packs make 60 cards each. Then a deck is built from each pool (a commander and 59 or more cards) and you play one "
         "4-player Commander game against the three AI drafters: 40 life, 21 commander damage.")
INTRO_CLASSIC = ("You and three AI drafters each open a pack of 15 cards, take one, and pass the rest - left, then right, then left. "
                 "Three packs make 45 cards each. Then a 40-card deck is built from each pool, and you play a game against the AI "
                 "drafter you choose: 20 life.")                                   # round CD3
INTROS = {"commander": INTRO, "classic": INTRO_CLASSIC}
FIELD_HINT = "Or paste a Cube Cobra link or id"
REF_HELP = ("Any other cube: a Cube Cobra link (cubecobra.com/cube/overview/...) or its id, then Load. It is downloaded once, "
            "then kept on this computer. The stock cubes above come with Manticore.")
STEPS = ("1. Pick: the pack in front of you fills the window. Click two cards, then Confirm (Enter). The AIs pick at once.",
         "2. Review: after the last pick, every seat's picks in order, with the pack each came from.",
         "3. Decks: a deck is built from each pool, yours too, and all four are saved to My decks (tagged DRAFT).",
         "4. Play: the deck screen shows the four chosen - press Start for the 4-player game.",
         "The draft is saved after every pick: Save and leave (Esc) any time, and Draft on the main menu continues it.")
STEPS_CLASSIC = ("1. Pick: the pack in front of you fills the window. Click a card, then Confirm (Enter). The AIs pick at once.",
                 "2. Review: after the last pick, every seat's picks in order, with the pack each came from.",
                 "3. Decks: a 40-card deck is built from each pool, yours too; choose the AI you play, and all four are saved "
                 "to My decks (Draft decks, tagged DRAFT).",
                 "4. Play: the deck screen shows yours and that AI's - press Start for the game.",
                 "The draft is saved after every pick: Save and leave (Esc) any time, and Draft on the main menu continues it.")
STEPS_BY_STYLE = {"commander": STEPS, "classic": STEPS_CLASSIC}
STYLE_NAMES = {"commander": "Commander Draft", "classic": "Classic draft"}
LEAVE_SAID = "Your draft is saved: Draft on the main menu continues it."
CHOSEN_SAID = ("Your draft's four decks are chosen: yours, and the three AI drafters' as AI 1-3. Press Start to play them "
               "(Commander rules, 40 life).")
CHOSEN_SAID_CLASSIC = ("Your draft's decks are saved and chosen: yours, and {ai}'s as your opponent. Press Start to play "
                       "(20 life).")                                               # round CD3


def card_picture(gui, card, w, h):
    """A cube card's picture at w x h (the art loader's, fetched in the background) - a drawn stand-in until it is here."""
    art = getattr(gui, "art", None)
    radius = max(3, int(w * 0.05))
    key = ("draft", card.name, w, h)
    hit = gfx.recall(key)
    if hit is not None:
        return hit
    big = h > art_loader.SOURCE_SIZE[1] + 20
    if art is not None:
        try:
            if big:
                pic = art.preview(card.name, w, h)
                if pic is not None:
                    return gfx.remember(key, gfx.rounded_image(pic, w, h, radius))
            img = art.get(card.name)
        except Exception:                                    # a picture must never stop the draft
            img = None
        if img is not None:
            surf = gfx.rounded_image(img, w, h, radius)
            return surf if big else gfx.remember(key, surf)
    cost = "".join("{%s}" % s.upper() for s in sorted(card.cost, key=lambda s: (not s.isdigit(), s)))
    return gfx.card_face({"name": card.name, "cost": cost, "type": card.type_line, "text": card.oracle_text,
                          "colors": list(card.colors)}, w, h, radius)


def grid(n, area, gap, label_h, max_w):
    """Rects for n cards in `area`: as big as fits (each with label_h under it), at most max_w wide, the block centred."""
    if n <= 0:
        return []
    best = None
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        by_w = (area.w - gap * (cols - 1)) / cols
        by_h = ((area.h - gap * (rows - 1)) / rows - label_h) * CARD_ASPECT
        w = int(min(by_w, by_h, max_w))
        if best is None or w > best[0]:
            best = (w, cols, rows)
    w, cols, rows = best
    w = max(8, w)
    h = int(w / CARD_ASPECT)
    total_w, total_h = cols * w + gap * (cols - 1), rows * (h + label_h) + gap * (rows - 1)
    x0, y0 = area.x + (area.w - total_w) // 2, area.y + max(0, (area.h - total_h) // 2)
    return [pygame.Rect(x0 + (i % cols) * (w + gap), y0 + (i // cols) * (h + label_h + gap), w, h) for i in range(n)]


class CubeJob:
    """Load a cube and check it against this Forge, on its own thread (a download and ~800 card scripts: seconds, not frames)."""

    def __init__(self, ref, runtime=None, lookup=None, refresh=False, folder=None):
        self.ref, self.runtime, self.lookup, self.refresh, self.folder = ref, runtime, lookup, refresh, folder
        self.done, self.cube, self.check, self.note, self.error = False, None, None, "", None
        self.thread = threading.Thread(target=self.run, name="cube-load", daemon=True)
        self.thread.start()

    def run(self):
        try:
            cube, note = cs.load(self.ref, refresh=self.refresh, folder=self.folder)
            lookup = self.lookup or cs.forge_script_lookup(self.runtime)
            self.check = cs.check_forge(cube.cards, lookup)
            self.cube, self.note = cube, note
        except cs.CubeError as e:
            self.error = str(e)
        except Exception as e:                               # anything else: said in words, noted for a report
            self.error = f"The cube couldn't be loaded ({type(e).__name__}: {e})."
            crashlog.note(f"Cube draft: loading {self.ref!r} failed: {type(e).__name__}: {e}")
        finally:
            self.done = True

    @property
    def playable(self):
        return len(self.check.playable) if self.check else 0


class DraftFlow(dlg.Dialog):
    """The cube draft window. from_title: Draft was chosen on the main menu (leaving goes back there). root, lookup, cube_folder:
    tests give a folder for the pods, a stand-in for Forge's card scripts and a folder of kept cubes."""

    def __init__(self, gui, from_title=False, root=None, lookup=None, cube_folder=None, edit=None):
        super().__init__()
        import forge_settings as fset
        self.from_title, self.root, self.lookup, self.cube_folder = from_title, root, lookup, cube_folder
        self.runtime = getattr(getattr(gui, "launcher", None), "runtime", None)
        self.stage = "setup"
        self.field = fset.TextField("Cube", "", multiline=False, limit=300, lines=1, hint=FIELD_HINT)
        self.job = None
        self.loaded_ref = None
        self.message = None                                   # (text, colour) under the current stage
        self.pod = None
        self.builder = None                                   # round CD4: the build stage's draft_builder.Builder
        self.by_hand = False                                  # round CD4: your deck was changed in the builder
        self.edit = edit                                      # round CD4: {"pod", "path", "names"} - Edit deck after a game
        self.build_scroll = 0
        self.build_max_scroll = 0
        self.choosing = False                                 # round CD4: the commander list is open
        self.btiles = []                                      # round CD4: (rect, "pool" | "deck", card) drawn this frame
        try:
            self.resume = None if edit else dp.unfinished(root)
        except Exception as e:                                # a broken saved draft must not stop the window
            crashlog.note(f"Cube draft: the unfinished draft couldn't be read: {type(e).__name__}: {e}")
            self.resume = None
        self.stock = cs.DEFAULT_CUBE                          # round CD3: the stock cube chosen (None: the link box decides)
        self.style_choice = None                              # round CD3: the player's draft style for a cube that isn't stock
        self.opponent = 1                                     # round CD3: the AI seat you play after a classic draft
        self.selected = []                                    # the cards (ints) you have clicked in this pack, in order
        self.tiles = []                                       # (rect, card int) drawn this frame, for the mouse
        self.hover = None
        self.review_seat = 0
        self.scroll = 0
        self.max_scroll = 0
        self.decks = None
        self.problems = None
        remembered = getattr(gui, "draft_cube", None)          # round CDFIX1: the cube of the last draft started
        if isinstance(remembered, dict):
            if remembered.get("ref"):
                self.field.text = str(remembered["ref"])[:300]
                self.stock = None
            elif remembered.get("stock") in {st.key for st in cs.STOCK_CUBES}:
                self.stock = remembered["stock"]
        if edit:                                              # round CD4: straight to the builder, on the saved deck
            self.pod = edit["pod"]
            self.builder = db.Builder(self.pod, None)
            lost = self.builder.load_names(*edit["names"])
            self.stage = "build"
            if lost:
                self.message = (f"Not in your drafted cards, left out: {', '.join(lost[:6])}{' ...' if len(lost) > 6 else ''}",
                                ORANGE)
        elif self.resume is None:
            self.load_cube()
            if isinstance(remembered, dict) and remembered.get("ref") and remembered.get("style") in cs.STYLES:
                self.style_choice = remembered["style"]

    # ---- setup -------------------------------------------------------------------------------------------------------------------
    def ref(self):
        return self.field.text.strip() or self.stock or cs.DEFAULT_CUBE

    def load_cube(self, refresh=False):
        try:
            cs.parse_ref(self.ref())
        except cs.CubeError as e:
            self.message = (str(e), RED)
            return
        self.job = CubeJob(self.ref(), self.runtime, self.lookup, refresh, self.cube_folder)
        self.loaded_ref = self.ref()
        self.style_choice = None
        self.message = None

    def choose_stock(self, gui, i):
        """Round CD3: a stock cube's button - its bundled list loads at once (the link box is cleared)."""
        if self.resume is not None or not 0 <= i < len(cs.STOCK_CUBES):
            return
        self.stock = cs.STOCK_CUBES[i].key
        self.field.text = ""
        gui.play_cue("ui.click")
        self.load_cube()

    def style(self):
        """Round CD3: the draft style of the cube loaded: a stock cube's own, else the player's choice or the guess."""
        j = self.job
        cube = j.cube if (j and j.done and not j.error) else None
        if cube is None:
            return "commander"
        if getattr(cube, "stock", False) or cs.stock_entry(cube.id) is not None:
            return cube.style or "commander"
        return self.style_choice or cube.style or "commander"

    def style_fixed(self):
        j = self.job
        return bool(j and j.done and not j.error and (getattr(j.cube, "stock", False) or cs.stock_entry(j.cube.id) is not None))

    def set_style(self, gui, style):
        if style in cs.STYLES and not self.style_fixed():
            self.style_choice = style
            gui.play_cue("ui.click")

    def ready(self):
        j = self.job
        return bool(j and j.done and not j.error and j.playable >= dc.needed_for(self.style()))

    def start_draft(self, gui):
        if not self.ready():
            return
        try:
            self.pod = dp.Pod.new(self.job.cube, self.job.check, root=self.root, style=self.style())
        except (dc.DraftError, OSError) as e:
            self.message = (f"The draft couldn't start: {e}", RED)
            gui.play_cue("ui.error")
            return
        gui.play_cue("deck.shuffle")
        self.remember_cube(gui)
        self.to_pick(gui)

    def remember_cube(self, gui):
        """Round CDFIX1: the next draft opens on this cube (settings.json "draft_cube"). A link keeps the style you chose."""
        ref = self.field.text.strip()
        gui.draft_cube = ({"ref": ref, "style": self.style_choice} if self.style_choice else {"ref": ref}) if ref else \
            {"stock": self.stock or cs.DEFAULT_CUBE}
        if hasattr(gui, "save_settings"):
            gui.save_settings()

    def continue_draft(self, gui):
        self.pod, self.resume = self.resume, None
        gui.play_cue("ui.click")
        if self.pod.done:
            self.to_review()
        else:
            self.to_pick(gui)

    def discard_resume(self, gui):
        try:
            self.resume.discard()
        except OSError as e:
            self.message = (f"The saved draft couldn't be changed: {e}", RED)
            return
        self.resume = None
        gui.play_cue("ui.click")
        if self.job is None:
            self.load_cube()

    # ---- moving between stages ---------------------------------------------------------------------------------------------------
    def to_pick(self, gui):
        self.stage, self.selected, self.hover, self.message = "pick", [], None, None
        self.prefetch(gui)

    def to_review(self):
        self.stage, self.review_seat, self.scroll, self.hover, self.message = "review", 0, 0, None, None

    def to_decks(self, gui):
        self.decks = self.pod.build_all()
        self.problems = [self.pod.problems(s, d) if d is not None else None for s, d in enumerate(self.decks)]
        self.pod.stage = "built"
        self.pod.save()
        self.stage, self.hover, self.message = "decks", None, None
        gui.play_cue("deck.shuffle")

    def to_build(self, gui):
        """Round CD4: your deck, by hand - from the decks stage, starting from the deck shown there."""
        if self.pod is None or not self.decks:
            return
        self.builder = db.Builder(self.pod, self.decks[0])
        self.stage, self.hover, self.message, self.build_scroll, self.choosing = "build", None, None, 0, False
        if not self.pod.classic and self.decks[0] is None:
            self.choosing = bool(self.builder.commander_options())
        gui.play_cue("ui.click")

    def build_done(self, gui):
        """Round CD4: back to the decks stage with your deck as you built it (saved with the others there)."""
        b = self.builder
        if b is None:
            return
        if b.blocking():
            self.message = (b.blocking()[0], RED)
            gui.play_cue("ui.error")
            return
        deck = b.deck()
        self.decks[0] = deck
        self.by_hand = self.by_hand or b.changed                          # the file will say "built by hand"
        if self.problems:
            self.problems[0] = b.problems()
        self.stage, self.hover, self.message, self.choosing = "decks", None, None, False
        gui.play_cue("ui.click")

    def save_edit(self, gui, play=False):
        """Round CD4, Edit deck after a game: your deck file is written again in place (the AIs' stay as they are); then the deck
        screen with the pod's decks chosen, or (play) the same pod again at once."""
        b = self.builder
        if b is None or self.edit is None:
            return False
        if b.blocking():
            self.message = (b.blocking()[0], RED)
            gui.play_cue("ui.error")
            return False
        try:
            self.pod.save_deck_in_place(0, b.deck(), self.edit["path"], by_hand=True)
        except OSError as e:
            self.message = (f"Your deck couldn't be saved: {e}", RED)
            gui.play_cue("ui.error")
            return False
        gui.play_cue("ui.menu_select")
        self.done = True
        if hasattr(gui, "after_draft_edit"):
            gui.after_draft_edit(self.edit["path"], play)
        return True

    def prefetch(self, gui):
        art = getattr(gui, "art", None)
        pack = self.pod.pack() if self.pod else None
        if art is not None and pack:
            try:
                art.add_names({self.pod.cards[i].name for i in pack})
            except Exception:
                pass

    def leave(self, gui):
        """Save and leave: back to the main menu (or the deck screen, when Draft was opened there)."""
        if self.pod is not None and self.pod.stage not in ("saved", "discarded"):
            self.pod.save()
            gui.say(LEAVE_SAID, DIM, 6.0)
        self.done = True                                      # (the table plays ui.dialog_close as the window goes)
        if self.from_title and hasattr(gui, "open_title"):
            gui.open_title()

    # ---- the picks ---------------------------------------------------------------------------------------------------------------
    def toggle(self, gui, card):
        if card in self.selected:
            self.selected.remove(card)
        else:
            self.selected.append(card)
            need = self.pod.needed()
            while len(self.selected) > need:                  # a third click swaps out the first card clicked
                self.selected.pop(0)
        gui.play_cue("ui.click")

    def confirm(self, gui):
        if self.pod is None or len(self.selected) != self.pod.needed():
            gui.play_cue("ui.error")
            need = self.pod.needed() if self.pod else 0
            self.message = (f"Choose {need} card{'s' if need != 1 else ''} first.", ORANGE)
            return False
        try:
            self.pod.pick(self.selected)
        except (dc.DraftError, OSError) as e:
            self.message = (str(e), RED)
            gui.play_cue("ui.error")
            return False
        gui.play_cue("card.pickup")
        self.selected, self.hover, self.message = [], None, None
        if self.pod.done:
            self.to_review()
        else:
            self.prefetch(gui)
        return True

    # ---- the decks ---------------------------------------------------------------------------------------------------------------
    def save_decks(self, gui):
        if not self.decks or self.decks[0] is None:
            gui.play_cue("ui.error")
            return False
        library, _samples, base = getattr(gui, "deck_dirs", (None, None, None))
        try:
            entries = self.pod.save_decks(self.decks, library, base, by_hand=(0,) if self.by_hand else ())
        except OSError as e:
            self.message = (f"The decks couldn't be saved: {e}", RED)
            gui.play_cue("ui.error")
            return False
        try:                                                  # Round BAN1's checks want the new cards' data (background)
            import legality as lg
            lg.ensure_card_data({n for e in entries if e for n in list(e.commanders or []) + list(e.deck or [])})
        except Exception:
            pass
        menu = getattr(gui, "menu", None)
        if menu is None and hasattr(gui, "open_menu"):
            gui.open_menu()
            menu = gui.menu
        else:
            menu.refresh()
        if menu is not None and self.pod.classic:            # round CD3: 1v1 against the AI drafter you chose
            ai = entries[self.opponent] if 0 < self.opponent < len(entries) else None
            menu.choose_draft(entries[0].id, [ai.id] if ai is not None else [], fmt=self.pod.format)
            menu.say(CHOSEN_SAID_CLASSIC.format(ai=dp.SEAT_NAMES[self.opponent]), GREEN)
        elif menu is not None:
            menu.choose_draft(entries[0].id, [e.id for e in entries[1:] if e is not None])
            menu.say(CHOSEN_SAID, GREEN)
            gui.deck_choice = menu.choice()
            if hasattr(gui, "save_settings"):
                gui.save_settings()
        gui.play_cue("ui.menu_select")
        self.done = True
        return True

    # ---- input ---------------------------------------------------------------------------------------------------------------------
    def press(self, gui, name):
        if name == "leave" or name == "back":
            self.leave(gui)
        elif name == "load":
            gui.play_cue("ui.click")
            self.load_cube(refresh=self.loaded_ref == self.ref() and bool(self.job and self.job.done))
        elif name == "start":
            self.start_draft(gui)
        elif name == "continue":
            self.continue_draft(gui)
        elif name == "discard":
            self.discard_resume(gui)
        elif name == "confirm":
            self.confirm(gui)
        elif name == "clear":
            self.selected = []
            gui.play_cue("ui.click")
        elif name == "build":
            self.to_decks(gui)
        elif name == "to_review":
            self.to_review()
            gui.play_cue("ui.click")
        elif name == "save":
            self.save_decks(gui)
        elif name.startswith("seat_"):
            self.review_seat, self.scroll = int(name[5:]), 0
            gui.play_cue("ui.click")
        elif name.startswith("stock_"):                       # round CD3
            self.choose_stock(gui, int(name[6:]))
        elif name.startswith("style_"):
            self.set_style(gui, name[6:])
        elif name.startswith("versus_"):
            self.opponent = int(name[7:])
            gui.play_cue("ui.click")
        elif name == "edit":                                  # round CD4
            self.to_build(gui)
        elif name == "leave_edit":
            self.done = True
        elif name == "build_done":
            self.build_done(gui)
        elif name in ("save_edit", "save_play"):
            self.save_edit(gui, play=name == "save_play")
        elif name == "auto":
            if self.builder.auto_build():
                self.message = ("Auto-build: the best deck your pool makes" + (" with this commander." if
                                self.builder.commanders and not self.pod.classic else "."), GREEN)
            gui.play_cue("deck.shuffle")
        elif name == "suggest":
            self.builder.suggest_lands()
            self.message = (f"Basic lands to {self.builder.size} cards.", GREEN)
            gui.play_cue("ui.click")
        elif name == "choose_cmd":
            self.choosing = True
            gui.play_cue("ui.click")
        elif name == "cmd_cancel":
            self.choosing = False
            gui.play_cue("ui.click")
        elif name.startswith("cmd_"):
            opts = self.builder.commander_options()
            k = int(name[4:])
            if 0 <= k < len(opts):
                out = self.builder.set_commanders(opts[k])
                self.message = ((f"Taken out (outside the new colours): {', '.join(out[:5])}{' ...' if len(out) > 5 else ''}",
                                 ORANGE) if out else None)
            self.choosing = False
            gui.play_cue("ui.click")
        elif name.startswith("basic+_") or name.startswith("basic-_"):
            self.builder.step_basic(name[7:], 1 if name[5] == "+" else -1)
            gui.play_cue("ui.click")
        elif name.startswith("f_"):                           # f_colour_W, f_type_land, f_mv_2, f_clear
            if name == "f_clear":
                self.builder.clear_filters()
            else:
                _f, kind, value = name.split("_", 2)
                self.builder.toggle_filter(kind, value)
            self.build_scroll = 0
            gui.play_cue("ui.click")

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name:
            self.press(gui, name)
            return
        if self.stage == "pick" and button == 1:
            for rect, card in self.tiles:
                if rect.collidepoint(pos):
                    self.toggle(gui, card)
                    return
        if self.stage == "build" and button == 1 and not self.choosing:    # round CD4: pool -> deck, deck -> pool
            for rect, where, card in reversed(self.btiles):
                if rect.collidepoint(pos):
                    b = self.builder
                    if where == "deck":
                        b.remove(card)
                        self.message = None
                        gui.play_cue("card.pickup")
                    elif b.add(card):
                        self.message = None
                        gui.play_cue("card.pickup")
                    else:
                        self.message = (b.why_not(card) or "That card is already in your deck.", ORANGE)
                        gui.play_cue("ui.error")
                    return

    def key(self, gui, ev):
        k = ev.key
        enter = k in (pygame.K_RETURN, pygame.K_KP_ENTER)
        if k == pygame.K_ESCAPE:
            if self.stage == "build" and self.choosing and self.builder.commanders:     # round CD4
                self.choosing = False
            elif self.stage == "build" and self.edit is None:
                self.build_done(gui)
            elif self.stage == "build":
                self.done = True                              # Edit deck after a game: Esc leaves without saving
            elif self.stage == "decks":
                self.to_review()
            else:
                self.leave(gui)
            return
        if self.stage == "setup":
            if enter:
                if self.resume is not None:
                    self.continue_draft(gui)
                elif self.ref() != self.loaded_ref or (self.job and self.job.done and self.job.error):
                    self.load_cube()
                else:
                    self.start_draft(gui)
                return
            import forge_menu as fmenu
            self.field.key(ev, fmenu.read_clipboard)
        elif self.stage == "pick":
            if enter:
                self.confirm(gui)
            elif k in (pygame.K_BACKSPACE, pygame.K_DELETE):
                self.selected = []
        elif self.stage == "review" and enter:
            self.to_decks(gui)
        elif self.stage == "decks" and enter:
            self.save_decks(gui)

    def wheel(self, gui, dy):
        if self.stage == "build":                            # round CD4: the pool scrolls
            step = int(80 * gui.L.fs)
            self.build_scroll = max(0, min(self.build_max_scroll, self.build_scroll - dy * step))
            return
        if self.stage == "review":
            step = int(80 * gui.L.fs)
            self.scroll = max(0, min(self.max_scroll, self.scroll - dy * step))

    # ---- drawing -------------------------------------------------------------------------------------------------------------------
    def draw(self, gui):
        L = gui.L
        rect = self.panel(gui, L.W - 40, L.H - 40, real=True)
        self.tiles = []
        fs = L.fs
        self.pad = int(16 * fs)
        self.bh = int(max(36, 42 * fs))
        if self.stage == "setup":
            self.draw_setup(gui, rect)
        elif self.stage == "pick":
            self.draw_pick(gui, rect)
        elif self.stage == "review":
            self.draw_review(gui, rect)
        elif self.stage == "build":                           # round CD4
            self.draw_build(gui, rect)
        else:
            self.draw_decks(gui, rect)

    def footer(self, gui, rect, left, right):
        """Buttons along the bottom: `left` from the left edge, `right` from the right edge (label, name, enabled, primary).
        Returns the footer's top."""
        pad, bh = self.pad, self.bh
        y = rect.bottom - pad - bh
        x = rect.x + pad
        for label, name, on, primary in left:
            w = gui.button_width(label)
            self.button(gui, pygame.Rect(x, y, w, bh), label, name, on, primary)
            x += w + int(10 * gui.L.fs)
        x = rect.right - pad
        for label, name, on, primary in right:
            w = gui.button_width(label)
            x -= w
            self.button(gui, pygame.Rect(x, y, w, bh), label, name, on, primary)
            x -= int(10 * gui.L.fs)
        return y

    def header(self, gui, rect, title, sub=None):
        """The title (and a line under it); returns the y below them."""
        scr, pad = gui.screen, self.pad
        tf, small = gui.font("title", True), gui.font("small")
        draw_text(scr, clip_text(title, tf, rect.w - 2 * pad), rect.x + pad, rect.y + pad, tf, GOLD)
        y = rect.y + pad + tf.get_height() + int(4 * gui.L.fs)
        if sub:
            draw_text(scr, clip_text(sub, small, rect.w - 2 * pad), rect.x + pad, y, small, DIM)
            y += small.get_height() + int(4 * gui.L.fs)
        return y + int(6 * gui.L.fs)

    def draw_message(self, gui, x, y, w):
        if self.message:
            small = gui.font("small", True)
            text, colour = self.message
            draw_text(gui.screen, clip_text(text, small, w), x, y, small, colour)

    # setup
    def stock_rows(self, gui, x0, wrap_w):
        """Round CD3: [(rect, i)] for the stock cube buttons, wrapped to wrap_w, from (x0, 0)."""
        gap, bh = int(8 * gui.L.fs), self.bh
        out, x, y = [], x0, 0
        for i, st in enumerate(cs.STOCK_CUBES):
            w = gui.button_width(st.label)
            if x + w > x0 + wrap_w and x > x0:
                x, y = x0, y + bh + gap
            out.append((pygame.Rect(x, y, w, bh), i))
            x += w + gap
        return out

    def draw_setup(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        body, small, smallb = gui.font("body"), gui.font("small"), gui.font("small", True)
        y = self.header(gui, rect, TITLE)
        wrap_w = min(rect.w - 2 * pad, int(1100 * fs))
        foot_top = rect.bottom - pad - self.bh
        room = foot_top - int(8 * fs)
        style = self.style()
        bh, gap = self.bh, int(8 * fs)
        # round CD3: what must show (the stock cubes, the link box, the style, what the cube is) is measured first; the intro,
        # the help line and "How it goes" take only the room left, so a small window never loses a house rule or a red line
        stock = self.stock_rows(gui, rect.x + pad, wrap_w)
        stock_h = (max(r.bottom for r, _i in stock) + int(10 * fs) + smallb.get_height() + 4) if stock else 0
        fh = body.get_height() + 14
        j = self.job
        style_row = bool(j and j.done and not j.error and self.resume is None and not self.style_fixed())
        status = [(ln, colour) for text, colour in self.status_lines() for ln in wrap_text(text, small, wrap_w)[:4]]
        need = stock_h + fh + 6 + int(8 * fs) + (bh + int(8 * fs) if style_row else 0) + len(status) * (small.get_height() + 1)
        if self.resume is not None:
            need += smallb.get_height() + small.get_height() + bh + int(34 * fs)
        intro = wrap_text(INTROS.get(style, INTRO), body, wrap_w)[:6]
        intro_h = len(intro) * (body.get_height() + 2) + int(10 * fs)
        if y + intro_h + need <= room:
            for ln in intro:
                draw_text(scr, ln, rect.x + pad, y, body, WHITE)
                y += body.get_height() + 2
            y += int(10 * fs)
        if stock:
            draw_text(scr, "Stock cubes", rect.x + pad, y, smallb, GOLD)
            y += smallb.get_height() + 4
            chosen = (self.loaded_ref or "").strip().lower()
            for r, i in stock:
                st = cs.STOCK_CUBES[i]
                self.button(gui, r.move(0, y), st.label, f"stock_{i}", self.resume is None, st.key.lower() == chosen)
            y += max(r.bottom for r, _i in stock) + int(10 * fs)
        load_w = gui.button_width("Load")
        fw = min(wrap_w, int(760 * fs)) - load_w - int(10 * fs)
        fr = pygame.Rect(rect.x + pad, y, max(120, fw), fh)
        self.field.draw(gui, fr, self.resume is None)
        if not self.field.text:                               # the hint, said in the empty box (after the cursor)
            draw_text(scr, clip_text(FIELD_HINT, body, fr.w - 30), fr.x + 18, fr.y + 6, body, gfx.HINT_TEXT)
        loading = bool(j and not j.done)
        self.button(gui, pygame.Rect(rect.x + pad + max(120, fw) + int(10 * fs), y + (fh - bh) // 2, load_w, bh),
                    "Load", "load", not loading and bool(self.field.text.strip()))
        y += fh + 6
        help_lines = wrap_text(REF_HELP, small, wrap_w)[:2]
        left = room - y - (need - stock_h - fh - 6)
        if left >= len(help_lines) * (small.get_height() + 1) + int(4 * fs):
            for ln in help_lines:
                draw_text(scr, ln, rect.x + pad, y, small, DIM)
                y += small.get_height() + 1
        y += int(8 * fs)
        if style_row and y + bh < room:                     # round CD3: a pasted cube's style, chosen here
            label = "Draft style:"
            draw_text(scr, label, rect.x + pad, y + (bh - smallb.get_height()) // 2, smallb, GOLD)
            x = rect.x + pad + smallb.size(label)[0] + int(12 * fs)
            for st_key in cs.STYLES:
                w = gui.button_width(STYLE_NAMES[st_key])
                self.button(gui, pygame.Rect(x, y, w, bh), STYLE_NAMES[st_key], f"style_{st_key}", True, st_key == style)
                x += w + gap
            y += bh + int(8 * fs)
        for ln, colour in status:
            if y + small.get_height() > room:
                break
            draw_text(scr, ln, rect.x + pad, y, small, colour)
            y += small.get_height() + 1
        if self.resume is not None:
            y += int(8 * fs)
            box_h = smallb.get_height() + small.get_height() + bh + int(26 * fs)
            box = pygame.Rect(rect.x + pad, min(y, foot_top - box_h - int(8 * fs)), wrap_w, box_h)
            round_rect(scr, box, gfx.ROW_BG, 10, 2, GOLD)
            draw_text(scr, "Your draft isn't finished", box.x + int(12 * fs), box.y + int(8 * fs), smallb, GOLD)
            when = time.strftime("%d %b %H:%M", time.localtime(self.resume.created or 0)).lstrip("0")
            draw_text(scr, clip_text(f"{self.resume.progress_text()} (started {when})", small, box.w - int(24 * fs)),
                      box.x + int(12 * fs), box.y + int(10 * fs) + smallb.get_height(), small, WHITE)
            by = box.bottom - bh - int(8 * fs)
            cw = gui.button_width("Continue it")
            self.button(gui, pygame.Rect(box.x + int(12 * fs), by, cw, bh), "Continue it", "continue", True, True)
            self.button(gui, pygame.Rect(box.x + int(22 * fs) + cw, by, gui.button_width("Discard it"), bh), "Discard it",
                        "discard", True)
            y = box.bottom
        y += int(14 * fs)
        if y + smallb.get_height() < room:
            draw_text(scr, "How it goes", rect.x + pad, y, smallb, GOLD)
            y += smallb.get_height() + 3
            for step in STEPS_BY_STYLE.get(style, STEPS):
                for ln in wrap_text(step, small, wrap_w)[:2]:
                    if y + small.get_height() > room:
                        break
                    draw_text(scr, ln, rect.x + pad, y, small, WHITE)
                    y += small.get_height() + 1
                y += 3
        top = self.footer(gui, rect, [("Back", "back", True, False)],
                          [("Start the draft", "start", self.ready() and self.resume is None, self.resume is None)])
        self.draw_message(gui, rect.x + pad + gui.button_width("Back") + int(16 * fs), top + (bh - smallb.get_height()) // 2,
                          rect.w - 2 * pad - gui.button_width("Back") - gui.button_width("Start the draft") - int(40 * fs))

    def status_lines(self):
        """[(text, colour)] about the cube being loaded."""
        j = self.job
        if j is None:
            return []
        if not j.done:
            return [(f"Loading the cube '{j.ref}' (the built-in list for a stock cube; else Cube Cobra the first time, then the "
                     "copy kept on this computer) and checking which of its cards this Forge can play...", DIM)]
        if j.error:
            return [(j.error, RED)]
        cube, check = j.cube, j.check
        style = self.style()
        owner = f" by {cube.owner}" if cube.owner else ""
        n_cmd = sum(1 for c in check.playable if c.commander)
        tail = f", {n_cmd} can be commanders." if style == "commander" else "."
        if self.style_fixed():                                # round CD3: a stock cube's style, said here (no style row)
            tail += f" A {STYLE_NAMES[style].lower()}." if style == "classic" else f" A {STYLE_NAMES[style]}."
        out = [(f"{cube.name or cube.id}{owner}: {len(cube.cards)} cards; {len(check.playable)} of them this Forge can play{tail}",
                GREEN)]
        # Round CD3: the lines that change the game first - a small window shows only the first few
        for line in house_rules.describe(getattr(cube, "rules", ())):
            out.append((f"House rule - {line} Every player starts with this emblem.", ORANGE))
        need = dc.needed_for(style)
        if len(check.playable) < need:
            packs, size, _picks = dc.shape(style)
            out.append((f"Too small for a draft: 4 players x {packs} packs x {size} cards needs {need} cards, and this cube has "
                        f"{len(check.playable)} that this Forge can play.", RED))
        if check.missing:
            shown = ", ".join(check.missing[:8]) + (f" and {len(check.missing) - 8} more" if len(check.missing) > 8 else "")
            out.append((f"Left out (this Forge has no script for them): {shown}.", DIM))
        if j.note:
            out.append((j.note, ORANGE))
        if getattr(cube, "stock", False):                     # round CD3: whose list it is, and where it came from
            when = time.strftime("%d %b %Y", time.gmtime(cube.fetched)).lstrip("0") if cube.fetched else "?"
            out.append((f"Built into Manticore: {cube.owner}'s list from Cube Cobra ({cs.CUBE_PAGE_URL.format(id=cube.id)}), "
                        f"as it was on {when}.", DIM))
        return out

    # pick
    def draw_pick(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        pod = self.pod
        smallb, tiny = gui.font("small", True), gui.font("tiny", True)
        need = pod.needed()
        word, to = pod.passing()
        counts = pod.counts()
        others = "   ".join(f"{dp.SEAT_NAMES[s]}: {counts[s]} cards" for s in range(1, len(counts)))
        y = self.header(gui, rect, f"{pod.label()}  -  take {need}",
                        f"Passing {word}, to {to}.   You have {counts[0]} cards.   {others}")
        foot = rect.bottom - pad - self.bh - int(10 * fs)
        side_w = max(int(200 * fs), min(int(rect.w * SIDE_SHARE), int(520 * fs)))
        area = pygame.Rect(rect.x + pad, y, rect.w - 3 * pad - side_w, foot - y)
        side = pygame.Rect(area.right + pad, y, side_w, foot - y)
        pack = pod.pack() or []
        label_h = tiny.get_height() + 4
        rects = grid(len(pack), area, int(8 * fs), label_h, int(MAX_CARD_W * fs))
        self.hover = None
        for r, card in zip(rects, pack):
            if r.collidepoint(gui.mouse):
                self.hover = card
        for r, card in zip(rects, pack):
            c = pod.cards[card]
            chosen = card in self.selected
            dr = r.move(0, -int(6 * fs)) if chosen else r
            scr.blit(card_picture(gui, c, dr.w, dr.h), dr)
            if chosen:
                pygame.draw.rect(scr, GOLD, dr.inflate(6, 6), 3, border_radius=max(3, int(dr.w * 0.05)) + 3)
            elif card == self.hover:
                pygame.draw.rect(scr, gfx.HOVER_EDGE, dr.inflate(4, 4), 2, border_radius=max(3, int(dr.w * 0.05)) + 2)
            cmd = c.commander and not pod.classic                     # round CD3: no commanders in a classic draft
            words = ("Taking" if chosen else "") + (" - " if chosen and cmd else "") + ("Commander" if cmd else "")
            if words:
                draw_text(scr, clip_text(words, tiny, r.w + 8), r.centerx, r.bottom + 2, tiny, GOLD if chosen else gfx.VS_TEXT,
                          "midtop")
            self.tiles.append((pygame.Rect(r), card))
        self.draw_side(gui, side)
        left_label = "Save and leave"
        top = self.footer(gui, rect, [(left_label, "leave", True, False)],
                          [("Confirm", "confirm", len(self.selected) == need, True), ("Clear", "clear", bool(self.selected), False)])
        if self.message:
            self.draw_message(gui, rect.x + pad + gui.button_width(left_label) + int(16 * fs),
                              top + (self.bh - smallb.get_height()) // 2, int(rect.w * 0.45))
        else:
            left = need - len(self.selected)
            text = (f"Click {left} more card{'s' if left != 1 else ''}." if left > 0 else "Confirm (Enter) to take them.")
            draw_text(scr, text, rect.x + pad + gui.button_width(left_label) + int(16 * fs),
                      top + (self.bh - smallb.get_height()) // 2, smallb, GOLD if left == 0 else WHITE)

    def draw_side(self, gui, side, picks=True):
        """The big card (the one under the mouse, else the last one clicked) and, under it, your picks so far."""
        scr, fs = gui.screen, gui.L.fs
        pod = self.pod
        show = self.hover if self.hover is not None else (self.selected[-1] if self.selected else None)
        pic_h = int(min(side.w / CARD_ASPECT, side.h * (0.58 if picks else 1.0)))
        pic_w = int(pic_h * CARD_ASPECT)
        pr = pygame.Rect(side.x + (side.w - pic_w) // 2, side.y, pic_w, pic_h)
        if show is not None:
            scr.blit(card_picture(gui, pod.cards[show], pr.w, pr.h), pr)
        else:
            round_rect(scr, pr, gfx.SLOT_BG, max(3, int(pr.w * 0.05)), 1, gfx.SLOT_EDGE)
            small = gui.font("small")
            for i, ln in enumerate(wrap_text("Point at a card to see it here.", small, pr.w - 20)[:3]):
                draw_text(scr, ln, pr.centerx, pr.centery + i * small.get_height(), small, DIM, "midtop")
        if picks:
            self.draw_picks(gui, pygame.Rect(side.x, pr.bottom + int(10 * fs), side.w, side.bottom - pr.bottom - int(10 * fs)))

    def draw_picks(self, gui, r):
        """Your picks so far: your commanders, your colours, your curve, your newest cards."""
        if r.h < 30:
            return
        scr, fs = gui.screen, gui.L.fs
        pod = self.pod
        small, smallb, tiny = gui.font("small"), gui.font("small", True), gui.font("tiny")
        pool = [pod.cards[i] for i in pod.pool(0)]
        prev = scr.get_clip()
        scr.set_clip(r)
        y = r.y
        draw_text(scr, f"Your picks ({len(pool)})", r.x, y, smallb, GOLD)
        y += smallb.get_height() + 2
        cmds = [c.name for c in pool if c.commander]
        if pod.classic:                                           # round CD3: what the cube's basics allow, not commanders
            station = pod.station
            cols = "".join(c for c in "WUBRG" if c in station)
            text = (f"Basic lands: {', '.join(dict.fromkeys(station.values()))} - a 40-card deck, any colours you have basics for."
                    if cols != "WUBRG" else "A 40-card deck: one or two colours, plus basic lands.")
            colour = DIM
        elif cmds:
            text, colour = "Commanders: " + ", ".join(dict.fromkeys(cmds)), WHITE
        else:
            text = "No commander yet - take a card marked Commander."
            colour = ORANGE if len(pool) >= NO_COMMANDER_WORRY else DIM
        for ln in wrap_text(text, small, r.w)[:2]:
            draw_text(scr, ln, r.x, y, small, colour)
            y += small.get_height()
        spells = [c for c in pool if not cs.is_land(c)]
        per = {col: sum(1 for c in spells if col in c.identity) for col in "WUBRG"}
        x = r.x
        rr = max(6, int(small.get_height() * 0.42))
        for col in "WUBRG":
            x = gfx.draw_rich(scr, "{%s} %d   " % (col, per[col]), x, y + small.get_height() // 2 + 2, small, WHITE, rr)
        y += small.get_height() + int(8 * fs)
        bars = [0] * (CURVE_TOP + 1)
        for c in spells:
            bars[min(CURVE_TOP, int(c.cmc))] += 1
        bh_max = int(min(70 * fs, max(0, r.bottom - y - 2 * tiny.get_height() - 8)))
        if bh_max >= 12:
            bw = max(6, (r.w - CURVE_TOP * 4) // (CURVE_TOP + 1))
            top = max(bars) or 1
            base = y + tiny.get_height() + bh_max
            for mv, n in enumerate(bars):
                bx = r.x + mv * (bw + 4)
                h = int(bh_max * n / top)
                if h:
                    pygame.draw.rect(scr, gfx.GOLD_DARK, pygame.Rect(bx, base - h, bw, h))
                if n:
                    draw_text(scr, str(n), bx + bw // 2, base - h - 2, tiny, WHITE, "midbottom")
                draw_text(scr, f"{mv}+" if mv == CURVE_TOP else str(mv), bx + bw // 2, base + 2, tiny, DIM, "midtop")
            y = base + tiny.get_height() + 6
        for c in reversed(pool):                                   # the newest first
            if y + tiny.get_height() > r.bottom:
                break
            draw_text(scr, clip_text(c.name, tiny, r.w), r.x, y, tiny, gfx.VS_TEXT if (c.commander and not pod.classic) else DIM)
            y += tiny.get_height()
        scr.set_clip(prev)

    # review
    def draw_review(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        pod = self.pod
        small = gui.font("small")
        y = self.header(gui, rect, "The draft is done - every pick, in order",
                        "In gold: the cards that seat took. The rest of each row is the pack they were taken from.")
        x = rect.x + pad
        for s, name in enumerate(dp.SEAT_NAMES):
            w = gui.button_width(name)
            self.button(gui, pygame.Rect(x, y, w, self.bh), name, f"seat_{s}", True, s == self.review_seat)
            x += w + int(8 * fs)
        y += self.bh + int(10 * fs)
        foot = rect.bottom - pad - self.bh - int(10 * fs)
        side_w = max(int(180 * fs), min(int(rect.w * 0.22), int(420 * fs)))
        area = pygame.Rect(rect.x + pad, y, rect.w - 3 * pad - side_w, foot - y)
        side = pygame.Rect(area.right + pad, y, side_w, foot - y)
        label_w = small.size("Pack 3, pick 10")[0] + int(12 * fs)
        gap = max(2, int(4 * fs))
        size = pod.draft.pack_size                                 # round CD3: 15 in a classic draft
        tile_w = max(16, min(int(110 * fs), (area.w - label_w - (size - 1) * gap) // size))
        tile_h = int(tile_w / CARD_ASPECT)
        row_h = tile_h + int(10 * fs)
        picks = pod.draft.review(self.review_seat)
        self.max_scroll = max(0, len(picks) * row_h - area.h)
        self.scroll = min(self.scroll, self.max_scroll)
        prev = scr.get_clip()
        scr.set_clip(area)
        self.hover = None
        shade = pygame.Surface((tile_w, tile_h), pygame.SRCALPHA)
        shade.fill(gfx.BACKDROP + (130,))
        for k, p in enumerate(picks):
            ry = area.y + k * row_h - self.scroll
            if ry + row_h < area.y or ry > area.bottom:
                continue
            draw_text(scr, f"Pack {p.round + 1}, pick {p.turn}", area.x, ry + tile_h // 2, small, DIM, "midleft")
            order = list(p.cards) + [c for c in p.pack_before if c not in p.cards]
            tx = area.x + label_w
            for card in order:
                if tx + tile_w > area.right:
                    break
                tr = pygame.Rect(tx, ry, tile_w, tile_h)
                scr.blit(card_picture(gui, pod.cards[card], tile_w, tile_h), tr)
                if card in p.cards:
                    pygame.draw.rect(scr, GOLD, tr.inflate(4, 4), 2, border_radius=max(3, int(tile_w * 0.05)) + 2)
                else:
                    scr.blit(shade, tr)
                if tr.collidepoint(gui.mouse) and area.collidepoint(gui.mouse):
                    self.hover = card
                self.tiles.append((tr, card))
                tx += tile_w + gap
        scr.set_clip(prev)
        if self.max_scroll:
            bar_h = max(20, int(area.h * area.h / (area.h + self.max_scroll)))
            by = area.y + int((area.h - bar_h) * self.scroll / self.max_scroll)
            pygame.draw.rect(scr, gfx.SCROLLBAR, pygame.Rect(area.right - 4, by, 4, bar_h))
        self.draw_side(gui, side, picks=False)
        self.footer(gui, rect, [("Save and leave", "leave", True, False)], [("Build the decks", "build", True, True)])

    # decks
    def draw_decks(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        pod = self.pod
        small, smallb, body = gui.font("small"), gui.font("small", True), gui.font("body", True)
        if pod.classic:                                           # round CD3
            y = self.header(gui, rect, "The decks - choose the AI you play",
                            "Each is the best 40-card deck its pool makes: one or two colours, their best cards by Cube Cobra's "
                            "ratings, the pool's lands, then this cube's basic lands to 40. Click an AI's deck to play against it.")
        else:
            y = self.header(gui, rect, "The decks",
                            "Each is the best deck its pool makes: a commander, the best cards of its colours by Cube Cobra's "
                            "ratings, the pool's lands, then basic lands to 60. Edit my deck builds yours by hand.")
        foot = rect.bottom - pad - self.bh - int(10 * fs)
        n = len(self.decks or [])
        gap = int(14 * fs)
        col_w = (rect.w - 2 * pad - gap * (n - 1)) // max(1, n)
        self.hover = None
        mine_col = None
        for s, deck in enumerate(self.decks or []):
            col = pygame.Rect(rect.x + pad + s * (col_w + gap), y, col_w, foot - y)
            if s == 0:
                mine_col = col
            versus = pod.classic and s == self.opponent           # round CD3: the AI you will play
            round_rect(scr, col, gfx.ROW_BG, 10, 2 if (s == 0 or versus) else 1,
                       GOLD if s == 0 else (gfx.VS_TEXT if versus else gfx.ROW_EDGE))
            if pod.classic and s > 0:
                self.buttons.append((pygame.Rect(col), f"versus_{s}"))      # a click on an AI's column chooses it
            inner = col.inflate(-2 * int(10 * fs), -2 * int(10 * fs))
            if s == 0:                                            # round CD4: room for Edit my deck at the bottom
                inner.h -= self.bh + int(8 * fs)
            prev = scr.get_clip()
            scr.set_clip(col)
            cy = inner.y
            title = (dp.SEAT_NAMES[s] if s else "You") + ("  -  you play this one" if versus else "")
            draw_text(scr, clip_text(title, body, inner.w), inner.x, cy, body,
                      GOLD if s == 0 else (gfx.VS_TEXT if versus else WHITE))
            cy += body.get_height() + 4
            if deck is None:
                for ln in wrap_text("No card in this pool can be a commander, so there is no deck: a Commander Draft deck needs "
                                    "one (903.13).", small, inner.w)[:6]:
                    draw_text(scr, ln, inner.x, cy, small, RED)
                    cy += small.get_height()
                scr.set_clip(prev)
                continue
            pic_h = max(0, min(int(inner.w / CARD_ASPECT), int((inner.bottom - cy) * 0.42)))
            pic_w = int(pic_h * CARD_ASPECT)
            if pic_h >= 40:
                cmds = list(deck.commanders) or self.face(deck)         # round CD3: a classic deck shows its best card
                step = pic_w // 3 if len(cmds) > 1 else 0
                w2 = pic_w - step if len(cmds) > 1 else pic_w
                h2 = int(w2 / CARD_ASPECT)
                for k, i in enumerate(cmds):
                    pr = pygame.Rect(inner.x + (inner.w - pic_w) // 2 + k * step, cy + k * (pic_h - h2), w2, h2)
                    scr.blit(card_picture(gui, pod.cards[i], pr.w, pr.h), pr)
                    if pr.collidepoint(gui.mouse):
                        self.hover = i
                cy += pic_h + 6
            for text, colour, font in self.deck_lines(s, deck, {"small": small, "smallb": smallb}):
                if text.startswith("Colours: {"):                     # the colours as mana symbols
                    gfx.draw_rich(scr, text, inner.x, cy + font.get_height() // 2, font, colour)
                    cy += font.get_height() + 1
                    continue
                for ln in wrap_text(text, font, inner.w)[:3]:
                    draw_text(scr, ln, inner.x, cy, font, colour)
                    cy += font.get_height() + 1
            cy += 4
            tiny = gui.font("tiny")
            for ln in self.card_list(deck):
                if cy + tiny.get_height() > inner.bottom:
                    break
                draw_text(scr, clip_text(ln, tiny, inner.w), inner.x, cy, tiny, DIM)
                cy += tiny.get_height()
            scr.set_clip(prev)
        mine_ok = bool(self.decks) and self.decks[0] is not None
        save_label = (f"Save the decks and play {dp.SEAT_NAMES[self.opponent]}" if pod.classic
                      else "Save the decks and choose them")
        top = self.footer(gui, rect, [("Back to the review", "to_review", True, False), ("Save and leave", "leave", True, False)],
                          [(save_label, "save", mine_ok, True)])
        if mine_col is not None:                              # round CD4: in your deck's column, under its card list
            w = min(mine_col.w - int(16 * fs), max(gui.button_width("Edit my deck"), int(140 * fs)))
            self.button(gui, pygame.Rect(mine_col.centerx - w // 2, mine_col.bottom - self.bh - int(8 * fs), w, self.bh),
                        "Edit my deck", "edit", True, False, fkey="small" if w < gui.button_width("Edit my deck") else "btn")
        if self.message:
            self.draw_message(gui, rect.x + pad, top - smallb.get_height() - 6, rect.w - 2 * pad)

    # ---- round CD4: the build stage ---------------------------------------------------------------------------------------------
    def draw_build(self, gui, rect):
        scr, fs, pad = gui.screen, gui.L.fs, self.pad
        b, pod = self.builder, self.pod
        small, smallb, tiny = gui.font("small"), gui.font("small", True), gui.font("tiny")
        self.btiles, self.hover = [], None
        sub = ("Click a card in your pool to put it in your deck; click a card in the deck to take it out. "
               + ("Any colours; this cube's own basic lands are free." if pod.classic else
                  "Greyed: outside your commander's colours. Basic lands are free."))
        y = self.header(gui, rect, "Build your deck", sub)
        gap = int(8 * fs)
        # the top row: commander, counter, Auto-build, Suggest lands
        row = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, self.bh)
        x = row.x
        if not pod.classic:
            names = " + ".join(pod.cards[i].name for i in b.commanders) or "no commander yet"
            label = "Commander: "
            draw_text(scr, label, x, row.centery, smallb, GOLD, "midleft")
            x += smallb.size(label)[0]
            cw = gui.button_width("Change")
            room = max(40, row.w // 2 - (x - row.x) - cw - gap)
            text = clip_text(names, small, room)
            draw_text(scr, text, x, row.centery, small, WHITE if b.commanders else ORANGE, "midleft")
            for i in b.commanders:                            # hover a commander's name for its picture
                tw = small.size(text)[0]
                if pygame.Rect(x, row.y, tw, row.h).collidepoint(gui.mouse):
                    self.hover = i
            x += small.size(text)[0] + gap
            self.button(gui, pygame.Rect(x, row.y, cw, row.h), "Change", "choose_cmd", bool(b.commander_options()))
            x += cw + gap
        total = b.total()
        big = gui.font("body", True)
        ccol = GREEN if total == b.size else (ORANGE if total < b.size else WHITE)
        right = [("Suggest lands", "suggest"), ("Auto-build", "auto")]
        rx = row.right
        for label, name in right:
            w = gui.button_width(label)
            rx -= w
            self.button(gui, pygame.Rect(rx, row.y, w, row.h), label, name, True, name == "auto")
            rx -= gap
        counter = b.counter()
        cx = max(x + big.size(counter)[0] // 2 + gap, (x + rx) // 2)
        if x + big.size(counter)[0] < rx:
            draw_text(scr, counter, cx, row.centery, big, ccol, "center")
        y = row.bottom + gap
        # the second row: filters (left), basic land steppers (right)
        fh = int(max(26, 30 * fs))
        pool_w = int((rect.w - 2 * pad) * 0.44)
        frow = pygame.Rect(rect.x + pad, y, pool_w, fh)
        fx = frow.x
        chips = [("colour", c, "{%s}" % c if c in "WUBRG" else ("Colourless" if c == "C" else "Multi")) for c in db.COLOUR_FILTERS]
        chips += [("type", t, t.title()) for t in db.TYPE_FILTERS] + [("mv", m, m) for m in db.MV_FILTERS]
        fy = frow.y
        for kind, value, label in chips:
            on = value in b.filters[kind]
            w = (tiny.size(label)[0] if "{" not in label else fh) + int(14 * fs)
            if fx + w > frow.right:
                fx, fy = frow.x, fy + fh + 4
            r = pygame.Rect(fx, fy, w, fh)
            round_rect(scr, r, gfx.ROW_BG if not on else gfx.GOLD_DARK, 6, 1, GOLD if on else gfx.ROW_EDGE)
            if "{" in label:
                gfx.draw_rich(scr, label, r.x + (r.w - fh) // 2 + 2, r.centery, tiny, WHITE)
            else:
                draw_text(scr, label, r.centerx, r.centery, tiny, WHITE if on else DIM, "center")
            self.buttons.append((r, f"f_{kind}_{value}"))
            fx += w + 4
        if any(b.filters.values()):
            w = tiny.size("Clear")[0] + int(14 * fs)
            if fx + w > frow.right:
                fx, fy = frow.x, fy + fh + 4
            r = pygame.Rect(fx, fy, w, fh)
            round_rect(scr, r, gfx.ROW_BG, 6, 1, gfx.ROW_EDGE)
            draw_text(scr, "Clear", r.centerx, r.centery, tiny, ORANGE, "center")
            self.buttons.append((r, "f_clear"))
        filters_bottom = fy + fh
        sx = rect.x + pad + pool_w + int(18 * fs)
        sy = y
        for name in b.basic_names():
            n = b.basics.get(name, 0)
            label = f"{n} {name}"
            w = fh + small.size(label)[0] + fh + int(16 * fs)
            if sx + w > rect.right - pad:
                sx, sy = rect.x + pad + pool_w + int(18 * fs), sy + fh + 4
            self.button(gui, pygame.Rect(sx, sy, fh, fh), "-", f"basic-_{name}", n > 0, fkey="small")
            draw_text(scr, label, sx + fh + int(8 * fs), sy + fh // 2, small, WHITE if n else DIM, "midleft")
            self.button(gui, pygame.Rect(sx + fh + int(8 * fs) + small.size(label)[0] + int(8 * fs), sy, fh, fh), "+",
                        f"basic+_{name}", True, fkey="small")
            sx += w + int(12 * fs)
        y = max(filters_bottom, sy + fh) + gap
        foot = rect.bottom - pad - self.bh - small.get_height() - int(14 * fs)
        pool_area = pygame.Rect(rect.x + pad, y, pool_w, max(40, foot - y))
        deck_area = pygame.Rect(pool_area.right + int(18 * fs), y, rect.right - pad - pool_area.right - int(18 * fs),
                                pool_area.h)
        self.draw_pool(gui, pool_area)
        self.draw_deck_columns(gui, deck_area)
        # the footer
        if self.edit is not None:
            top = self.footer(gui, rect, [("Leave without saving", "leave_edit", True, False)],
                              [("Save and play again", "save_play", True, True), ("Save", "save_edit", True, False)])
        else:
            top = self.footer(gui, rect, [("Done - back to the decks", "build_done", True, True)], [])
        problems = b.problems()
        if self.message:
            self.draw_message(gui, rect.x + pad, top - smallb.get_height() - 6, rect.w - 2 * pad)
        elif problems:
            draw_text(scr, clip_text(problems[0] + ("  (it still plays)" if not b.blocking() else ""), small, rect.w - 2 * pad),
                      rect.x + pad, top - small.get_height() - 6, small, RED if b.blocking() else ORANGE)
        if self.choosing:
            self.draw_commander_list(gui, rect)
        elif self.hover is not None:
            self.draw_big(gui, rect, self.hover)

    def tile_size(self, gui, area, per_row):
        w = max(30, (area.w - (per_row - 1) * int(6 * gui.L.fs)) // per_row)
        return w, int(w / CARD_ASPECT)

    def draw_pool(self, gui, area):
        scr, fs, b = gui.screen, gui.L.fs, self.builder
        tiny = gui.font("tiny")
        cards = b.available()
        label = f"Your pool  -  {len(cards)} card{'s' if len(cards) != 1 else ''} not in the deck"
        draw_text(scr, clip_text(label, tiny, area.w), area.x, area.y, tiny, DIM)
        area = pygame.Rect(area.x, area.y + tiny.get_height() + 4, area.w, area.h - tiny.get_height() - 4)
        per_row = max(3, area.w // int(96 * fs))
        w, h = self.tile_size(gui, area, per_row)
        gap = int(6 * fs)
        rows = -(-len(cards) // per_row) if cards else 0
        self.build_max_scroll = max(0, rows * (h + gap) - area.h)
        self.build_scroll = min(self.build_scroll, self.build_max_scroll)
        prev = scr.get_clip()
        scr.set_clip(area)
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill(gfx.BACKDROP + (170,))
        for k, i in enumerate(cards):
            r = pygame.Rect(area.x + (k % per_row) * (w + gap), area.y + (k // per_row) * (h + gap) - self.build_scroll, w, h)
            if r.bottom < area.y or r.y > area.bottom:
                continue
            scr.blit(card_picture(gui, self.pod.cards[i], w, h), r)
            if not b.fits(i):
                scr.blit(shade, r)
            vis = r.clip(area)
            self.btiles.append((vis, "pool", i))
            if vis.collidepoint(gui.mouse):
                self.hover = i
        scr.set_clip(prev)
        if not cards:
            draw_text(scr, "No card matches these filters." if any(b.filters.values()) else "Every card you drafted is in your deck.",
                      area.centerx, area.y + 20, tiny, DIM, "midtop")

    def draw_deck_columns(self, gui, area):
        scr, fs, b = gui.screen, gui.L.fs, self.builder
        tiny, tinyb = gui.font("tiny"), gui.font("tiny", True)
        cols = b.columns()
        names = list(cols)
        gap = int(6 * fs)
        cw = max(30, (area.w - gap * (len(names) - 1)) // len(names))
        ch = int(cw / CARD_ASPECT)
        head = tinyb.get_height() + 4
        for k, key in enumerate(names):
            x = area.x + k * (cw + gap)
            n = len(cols[key])
            draw_text(scr, clip_text(f"{key}  ({n})" if key != "Lands" else f"Lands ({n})", tinyb, cw), x, area.y, tinyb, GOLD)
            if not n:
                continue
            room = area.h - head - ch
            step = max(6, min(int(ch * 0.17), room // max(1, n - 1))) if n > 1 else 0
            for j, i in enumerate(cols[key]):
                r = pygame.Rect(x, area.y + head + j * step, cw, ch)
                scr.blit(card_picture(gui, self.pod.cards[i], cw, ch), r)
                vis = pygame.Rect(r.x, r.y, cw, step if j < n - 1 else ch).clip(area)
                self.btiles.append((vis, "deck", i))
                if vis.collidepoint(gui.mouse):
                    self.hover = i
        basics = sum(b.basics.values())
        if basics:
            text = "Basic lands: " + ", ".join(f"{k} {n}" for n, k in b.basics.items() if k)
            draw_text(scr, clip_text(text, tiny, area.w), area.x, area.bottom - tiny.get_height(), tiny, DIM)

    def draw_big(self, gui, rect, i):
        """The card under the mouse, big, on the side of the window away from it."""
        h = int(rect.h * 0.55)
        w = int(h * CARD_ASPECT)
        x = rect.x + self.pad if gui.mouse[0] > rect.centerx else rect.right - self.pad - w
        r = pygame.Rect(x, rect.y + int(rect.h * 0.2), w, h)
        gfx.shadowed(gui.screen, r, radius=max(4, int(w * 0.05)))
        gui.screen.blit(card_picture(gui, self.pod.cards[i], w, h), r)

    def draw_commander_list(self, gui, rect):
        """Round CD4: the pool's commanders (and legal pairs), one button each."""
        scr, fs, b = gui.screen, gui.L.fs, self.builder
        small, smallb = gui.font("small"), gui.font("small", True)
        opts = b.commander_options()
        shade = pygame.Surface(rect.size, pygame.SRCALPHA)
        shade.fill(gfx.BACKDROP + (200,))
        scr.blit(shade, rect)
        self.buttons = []                                    # only this list (and Cancel) takes clicks now
        w = min(rect.w - 4 * self.pad, int(720 * max(1.0, fs)))
        rh = int(max(30, 34 * fs))
        h = min(rect.h - 4 * self.pad, smallb.get_height() * 2 + (len(opts) + 1) * (rh + 4) + 3 * self.pad)
        panel = pygame.Rect(rect.centerx - w // 2, rect.centery - h // 2, w, h)
        round_rect(scr, panel, gfx.ROW_BG, 10, 2, GOLD)
        y = panel.y + self.pad
        draw_text(scr, "Choose your commander", panel.x + self.pad, y, smallb, GOLD)
        y += smallb.get_height() + 6
        if not opts:
            draw_text(scr, "No card in your pool can be a commander (903.13).", panel.x + self.pad, y, small, RED)
        bottom = panel.bottom - self.pad - rh
        for k, opt in enumerate(opts):
            if y + rh > bottom - 4:
                draw_text(scr, f"... and {len(opts) - k} more", panel.x + self.pad, y, small, DIM)
                break
            r = pygame.Rect(panel.x + self.pad, y, panel.w - 2 * self.pad, rh)
            names = " + ".join(self.pod.cards[i].name for i in opt)
            ident = dd.identity_of(self.pod.cards, opt)
            colours = "".join("{%s}" % c for c in "WUBRG" if c in ident) or "colourless"
            chosen = tuple(b.commanders) == tuple(opt)
            round_rect(scr, r, gfx.GOLD_DARK if chosen else gfx.ROW_BG, 6, 1, GOLD if chosen else gfx.ROW_EDGE)
            draw_text(scr, clip_text(names, small, r.w - int(120 * fs)), r.x + 8, r.centery, small, WHITE, "midleft")
            gfx.draw_rich(scr, colours, r.right - int(110 * fs), r.centery, small, DIM)
            self.buttons.append((r, f"cmd_{k}"))
            if r.collidepoint(gui.mouse):
                self.hover = opt[0]
            y += rh + 4
        if b.commanders:
            cw = gui.button_width("Cancel")
            self.button(gui, pygame.Rect(panel.right - self.pad - cw, bottom, cw, rh), "Cancel", "cmd_cancel")

    def face(self, deck):
        """Round CD3: the card that stands for a classic deck - its strongest spell by Cube Cobra's rating (or a land)."""
        z = self.pod.z
        cards = list(deck.spells) or list(deck.lands)
        return [max(cards, key=lambda i: z[i])] if cards else []

    def card_list(self, deck):
        """The deck's spells by mana value ("2  Arcane Signet"), then its lands, for the column under its commander."""
        pod = self.pod
        spells = sorted((pod.cards[i] for i in deck.spells), key=lambda c: (c.cmc, c.name))
        out = [f"{int(c.cmc)}  {c.name}" for c in spells]
        out += [f"Land  {pod.cards[i].name}" for i in sorted(deck.lands, key=lambda i: pod.cards[i].name)]
        out += [f"{n}x  {name}" for name, n in sorted(deck.basics.items()) if n]
        return out

    def deck_lines(self, seat, deck, fonts):
        """[(text, colour, font)] under a deck's commander picture. fonts: {"small", "smallb"}."""
        pod = self.pod
        names = [pod.cards[i].name for i in deck.commanders] or [dd.colour_name(deck.identity)]     # round CD3: classic
        colours = "".join(c for c in "WUBRG" if c in deck.identity) or "colourless"
        basics = sum(deck.basics.values())
        total = len(deck.commanders) + len(deck.spells) + len(deck.lands) + basics
        f = fonts
        out = [(" + ".join(names), WHITE, f["smallb"]),
               ("Colours: " + ("".join("{%s}" % c for c in colours) if colours != "colourless" else colours), DIM, f["small"]),
               (f"{total} cards: {len(deck.spells)} spell{'s' if len(deck.spells) != 1 else ''}, {len(deck.lands)} "
                f"land{'s' if len(deck.lands) != 1 else ''} from the pool, {basics} basic land{'s' if basics != 1 else ''}", DIM,
                f["small"])]
        if deck.short:
            out.append((f"{deck.short} basic land{'s' if deck.short != 1 else ''} stand in for spells this pool didn't have.",
                        ORANGE, f["small"]))
        for p in (self.problems[seat] if self.problems else None) or []:
            out.append((p, RED, f["small"]))
        return out
