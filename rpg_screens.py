# SPDX-License-Identifier: GPL-3.0-or-later
"""
rpg_screens.py - Round RPG8: the Adventure's screens (Karl, 13 Oct 2026, 22:30: "You could focus on refining the RPG mode", then
"First playable loop": main menu > Adventure > new game > walk the map > duel an enemy on the real table > gold and cards > the
town's shop > the deck editor > save).

One full-window dialog (as the cube draft's DraftFlow is), opened from the main menu's Adventure (the slot Continue left on 13 Oct
for it). Its stages:
  slots   the save slots (numbers.json save_slots): Continue a game, or start one in an empty slot (a used slot asks first)
  name    your name (rpg.save.check_name's rules)
  pick    three Jumpstart half-decks, one at a time (rpg.newgame: three offered at once, "Show others" for more, Back)
  ready   the 60 cards they make; Begin writes the save and opens the start town
  map     the Zelda-style room view (rpg.explore + rpg_map_view, Rounds RPG5-RPG7), walked with the arrows or WASD; Esc is the
          menu (Back to the map / Your deck / Save and leave). Walking into a foe asks first (who, what a win gives, what a loss
          costs), then Fight plays the duel on the real table (ForgeTable.start_adventure_duel). A town's gate opens the town.
          Round RPG13: that question is the encounter screen now (rpg_encounter: the way in, the foe on its backdrop, Duel /
          See its deck / Turn back), for a foe walked into and for a random fight in a dungeon alike (self.sub "fight").
  town    the town: Singles, Packs, Basic lands (Round RPG7: 1 gold each), Sell (rpg.shop); Your deck; Leave town
  pack    the cards of the pack you just opened (the new ones marked)
  deck    the deck editor (rpg.adventure.DeckEditor): your cards on the left (click: one more in the deck; right-click: one
          less), the deck on the right (click a line: one less), colour and type filters and a search (just type)
Small windows over a stage (self.sub): the fight question, a result (after a duel, or a note on loading), the map's menu, and
"replace the game in this slot?".

Everything is saved as it happens: a new game when Begin is pressed, a town when you enter it, every purchase and sale at once,
the deck when you leave the editor, your place on the map when you open the menu, a town or a fight, and when you leave.
The rules and numbers are rpg/'s (no pygame there); this module draws and routes clicks. Every colour is a gfx.py token.
"""
import time

import pygame

import art_loader
import crashlog
import forge_dialogs as dlg
import gfx
import rpg_encounter
import rpg_map_view
import rpg_ui
from gfx import DIM, GOLD, GREEN, ORANGE, RED, TEXT, clip_text, wrap_text
from rpg import adventure as adv
from rpg import cardpool
from rpg import duel as rduel
from rpg import clock, encounters, events, explore, hints, newgame, products, talk
from rpg import tapwalk
from rpg import save as rsave
from rpg import shop as rshop

CARD_ASPECT = 488 / 680
TITLE = "Adventure"
INTRO = ("Walk the land, duel whatever blocks your way, and build your deck from the cards you win and buy. Every duel is a "
         "60-card game at 20 life against the AI, on the same table as your other games.")
PICK_INTRO = ("Three Jumpstart half-decks make your first 60 cards, in at most two colours. Click the one you want; Show others "
              "offers three more.")
MAP_KEYS = ("Tap: walk or act", "Arrows or WASD: walk", "Enter: talk", "H: hint", "Esc: menu")   # RPG14: talk; RPG15b: the hint;
# Round RPG-TAP (Karl 27 Oct, a pen only on his Surface): a tap walks there, or up to a person, door or foe and acts
PEN_REPEAT = 0.3                 # Round RPG-TAP: seconds - a second left press this soon after a map tap, at the same place, is the
PEN_SLOP = 12                    # same pen tap told twice (px x text scale) and is ignored - my estimates
WALK_KEYS = (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s)
PAUSE_ITEMS = (("resume", "Back to the map", True), ("hint", "Hint", True), ("deck", "Deck", True), ("collection", "Collection", True),
               ("save", "Save", True), ("light", "Light", True), ("settings", "Settings", True),       # Round RPG14 (the spec's);
               ("leave", "Main menu", True))           # Round RPG15: "light" - its label says "Light: on" or "Light: off"
# Round RPG15: the music each place asks for when its map names none, and the boss's (audio's "loop:<cue>" mode loops one track)
BOSS_MUSIC = "music.rpg_boss"
NIGHT_MUSIC_PHASES = ("dusk", "night")  # Round RPG-NM: the clock phases a map's night_music plays in (rpg.clock.PHASES);
                                        # RPG18b (Karl 25 Oct): from dusk, 18:00-05:00 (was night only, 20:00)
VICTORY_STING, DEFEAT_STING = "music.rpg_victory", "music.rpg_defeat"
WELCOME = ("Your 60 cards are your deck. This town's shop sells single cards, packs and basic lands, and buys cards back. "
           "Leave town to explore; walking into a foe asks before a duel starts.")
TABS = (("singles", "Singles"), ("packs", "Packs"), ("lands", "Basic lands"), ("sell", "Sell"))
SEP = "   ·   "                    # between the parts of a shop line's second half
GROUP_BUTTONS = ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land")
RARITY = {"M": 0, "S": 0, "R": 1, "U": 2, "C": 3}
MAX_DT = 0.1                     # seconds: a frame that took longer walks this far at most (a stall never throws you into a wall)
BASIC_STEPS = [1, 5, 10]          # a list: a 3-number tuple reads as a colour to test_ad1's guard
WHEEL_ROWS = 3                   # rows a wheel notch scrolls a list
DAWN_COLOUR = {tuple(GREEN): gfx.DB_GREEN, tuple(RED): gfx.DB_RED, tuple(ORANGE): gfx.DB_ORANGE, tuple(GOLD): gfx.DB_GOLD}
# Round RPG14b: the shop, the pack, the deck editor and the Collection are drawn in DawnLike's window and SDS font (DawnGui); while
# one of them draws, _DAWN["k"] is the frame's pixel size and the words' colours are DawnBringer's (DAWN_TEXT)
DAWN_STAGES = ("town", "pack", "deck", "collection")
DAWN_TEXT = dict(DAWN_COLOUR)
DAWN_TEXT.update({tuple(TEXT): gfx.DB_TEXT, tuple(DIM): gfx.DB_DIM, tuple(gfx.BTN_GOLD_FG): gfx.DB_GOLD})
_DAWN = {"k": 0}


def time_text(world):
    """Round RPG15: the corner window's line for the clock. Round RPG17 (Karl 24 Oct Q10): with the day - "Day 2, Dusk 18:20"."""
    return clock.stamp(clock.now(world))


def light_on(gui):
    """Round RPG15: the Adventure's Light setting (Menu > Light; settings.json "rpg_light", kept by the table) - off = the plain art."""
    return bool(getattr(gui, "rpg_light", True))


def draw_text(screen, s, x, y, font, colour=TEXT, anchor="topleft", shadow=False):
    """gfx.draw_text; on a DawnLike stage in DawnBringer's colours, without the shadow (Round RPG14b)."""
    if _DAWN["k"]:
        colour, shadow, s = DAWN_TEXT.get(tuple(colour)[:3], colour), False, rpg_ui.plain(s)
    return gfx.draw_text(screen, s, x, y, font, colour, anchor, shadow)


def round_rect(screen, rect, colour, radius=10, border=0, border_colour=None, alpha=None):
    """gfx.round_rect; on a DawnLike stage DawnLike's window instead (Round RPG14b)."""
    if _DAWN["k"]:
        return rpg_ui.window(screen, rect, _DAWN["k"])
    return gfx.round_rect(screen, rect, colour, radius, border, border_colour, alpha)


class DawnGui:
    """Round RPG14b: the table as a DawnLike stage's drawing sees it - the same screen, mouse, art and layout (everything is
    passed through), but every font is DawnLike's SDS 8x8 at a whole multiple of 8 px (about the table's own "small" size;
    "title" twice that) and every button a DawnLike window, so the stages' own drawing code is unchanged."""

    def __init__(self, gui):
        self._gui = gui
        self.unit = 8 * max(1, int(gui.L.px["small"] / 8 + 0.5))
        self.k = max(1, self.unit // 8)                      # the frames' pixel size: the font's

    def __getattr__(self, name):
        return getattr(self._gui, name)

    def font(self, key, bold=False):
        return rpg_ui.spaced_font(self.unit * (2 if key == "title" else 1))

    def ui_font(self, key, bold=False):
        return self.font(key)

    def button_width(self, label, key="btn"):
        return self.font(key).size(label)[0] + 2 * self.inset()

    def inset(self):
        """Room between a button's frame and its words (the frame's line is 2 of its pixels)."""
        return 4 * self.k

    def draw_button(self, rect, label, name, enabled=True, primary=False, focus=False, hit=True, fkey="btn"):
        rect = pygame.Rect(rect)
        scr = self._gui.screen
        rpg_ui.window(scr, rect, self.k)
        over = enabled and rect.collidepoint(self._gui.mouse)
        colour = gfx.DB_DIM if not enabled else (gfx.DB_GOLD if primary or over else gfx.DB_TEXT)
        if over:                                             # under the mouse: a gold line just inside the frame
            pygame.draw.rect(scr, gfx.DB_GOLD, rect.inflate(-4 * self.k, -4 * self.k), self.k)
        font = self.font(fkey)
        room = rect.w - 2 * self.k                           # a short label (a colour's letter) may use the inset too
        shown = label if font.size(label)[0] <= room else clip_text(label, font, room)
        gfx.draw_text(scr, rpg_ui.plain(shown), rect.centerx, rect.centery, font, colour, "center")
        if hit and enabled:
            self._gui.add_hit(rect, "button", name=name)


def card_picture(gui, name, card, w, h):
    """A card's picture at w x h (the art loader's, fetched in the background); a drawn stand-in until it is here."""
    art = getattr(gui, "art", None)
    radius = max(3, int(w * 0.05))
    key = ("rpg", name, w, h)
    hit = gfx.recall(key)
    if hit is not None:
        return hit
    big = h > art_loader.SOURCE_SIZE[1] + 20
    img = None
    if art is not None:
        try:
            if big:
                pic = art.preview(name, w, h)
                if pic is not None:
                    return gfx.remember(key, gfx.rounded_image(pic, w, h, radius))
            img = art.get(name)
        except Exception:                                    # a picture must never stop the Adventure
            img = None
    if img is not None:
        surf = gfx.rounded_image(img, w, h, radius)
        return surf if big else gfx.remember(key, surf)
    colours = [c for c in (card.colours if card is not None else "") if c in "WUBRG"]
    return gfx.card_face({"name": name, "cost": "", "type": card.type_line if card is not None else "", "text": "",
                          "colors": colours}, w, h, radius)


def face_of(half, pool):
    """The card that stands for a half-deck: its rarest non-basic card, the costliest of those, the first on a tie."""
    best, key = None, None
    for i, (n, _k) in enumerate(half.cards):
        card = pool.get(n)
        if card is None or card.is_basic:
            continue
        k = (RARITY.get(card.rarity, 4), -card.mana_value, i)
        if key is None or k < key:
            best, key = card.name, k
    return best


class AdventureFlow(dlg.Dialog):
    """The Adventure window. folder: where the saves are (tests); slot: open that game at once (coming back from a duel), with
    the duel's outcome (rpg.duel.finish's dict) or an error to say; pool: a card pool already loaded (tests)."""

    def __init__(self, gui, folder=None, slot=None, outcome=None, error=None, pool=None, retry=None):
        super().__init__()
        import forge_settings as fset
        self.folder, self.pool = folder, pool
        self.stage = "slots"
        self.sub = None                  # None | "fight" | "result" | "pause" | "confirm"
        self.message = None              # (text, colour) under the stage's content
        self.game = None                 # adv.Game once a slot is open
        self.flow = None                 # newgame.NewGameFlow while a new game is being made
        self.slot = None
        self.slots = []
        self.rows = []                   # (rect, kind, data) drawn this frame, for the mouse
        self.scroll = {}                 # list key -> first row shown
        self.hover = None                # (name, card) under the mouse - shown big
        self.explorer = None
        self.views = {}
        self.camera, self._cam_key = None, None
        self.hud = None
        self.hud_words = []
        self.sprites = None              # Round RPG11: the heroes on the name screen (rpg_map_view.Sprites, made when first drawn)
        self.last = None                 # the last update's clock (walking)
        self.tab = "singles"
        self.shop = None
        self.town = None
        self.editor = None
        self.deck_back = "map"
        self.opened = None               # (set name, [card dicts]) - the pack just opened
        self.fight = None                # the rpg.explore.Trigger walked into (Round RPG13: or an rpg.encounters.RandomFoe)
        self.encounter = None            # Round RPG13: the fight's rpg_encounter.EncounterScreen (made when first drawn)
        self._enc_mouse = None           # the mouse where it was last frame (the encounter screen follows it only when it moves)
        self.talk_conv = None            # Round RPG14: the rpg.talk.Conversation going on (self.sub "talk")
        self.talk_box = None             # ... and its rpg_ui.DialogueBox
        self.menu = None                 # Round RPG14: the pause window's rpg_ui.ListMenu (self.sub "pause")
        self.talk_after = None           # Round RPG14: "fight" - the encounter screen opens when the box closes (a boss's words)
        self.talk_shop = None            # Round RPG15b: the shop (a town trigger on the counter) a shopkeeper's talk opens
        self.talk_event = None           # Round RPG16: [event id, answered] - a random event said in the dialogue box
        self.after_result = None         # Round RPG14: (name, pages) said in the dialogue box once the result window closes
        self.message_box = None          # Round RPG14: the result window on the map (rpg_ui.MessageWindow)
        self.result = None               # (title, [lines], colour)
        self.confirm = None              # (title, [lines], yes label, action)
        self.field = fset.TextField("Name", "", multiline=False, limit=rsave.NAME_MAX, lines=1, hint="Your name")
        self._prefetched = set()
        self.retry = None                # a duel's result the table couldn't write: tried again (open_slot, write)
        self.tap = None                  # Round RPG-TAP: the rpg.tapwalk.TapWalk a tap on the map started (None: none)
        self._last_tap = None            # ... (time, pos) of the last left press that was a map tap (a pen tap told twice acts once)
        if slot is not None:
            self.open_slot(gui, slot, outcome=outcome, error=error, retry=retry)
        else:
            self.refresh_slots()

    # ---- moving between stages -------------------------------------------------------------------------------------------
    def say(self, text, colour=DIM):
        self.message = (text, colour)

    def refresh_slots(self):
        try:
            self.slots = adv.slot_rows(self.folder)
        except Exception as e:                               # numbers.json unreadable: say so, never crash the menu
            self.slots = []
            self.say(f"The Adventure's data couldn't be read: {e}", RED)

    def set_stage(self, stage):
        self.stage = stage
        self.tap = None                                      # Round RPG-TAP: a walk never carries over to another screen
        self.message = None
        self.hover = None
        self.last = None

    def open_slot(self, gui, slot, outcome=None, error=None, retry=None):
        if retry:                                            # the table couldn't write the duel's result: once more, now
            try:
                outcome, error = adv.finish_duel(slot, retry, self.folder, pool=self.pool), None
                if outcome is None:
                    error = "The save had no duel going on, so nothing was won or lost."
                retry = None
            except Exception as e:
                error = (f"The duel's result ({retry}) couldn't be saved yet: {e}. It is tried again each time the game is "
                         "saved; it counts once it is written.")
        try:
            loader = adv.Game.load_raw if retry else adv.Game.load      # an unsaved result keeps its duel marked
            game, notes = loader(slot, self.folder, pool=self.pool)
        except FileNotFoundError:
            self.refresh_slots()
            self.say(f"Slot {slot} is empty.", ORANGE)
            return False
        except (rsave.SaveError, ValueError, OSError) as e:
            self.refresh_slots()
            self.say(str(e), RED)
            return False
        self.game, self.slot, self.pool = game, slot, game.pool
        self.retry = retry
        self.explorer = None
        w = game.save.world
        inside = w.get("in_town") if w.get("in_town") in game.world.towns else None
        if inside or (w.get("map") in game.world.towns and w.get("x") is None):
            self.enter_town(gui, inside or w["map"], announce=False)
        else:
            self.enter_map(gui)
        if error:
            self.result = ("The duel", [error], RED)
            self.sub = "result"
        elif outcome:
            won = outcome.get("result") == "won"
            title = {"won": "Victory", "lost": "Defeat", "conceded": "Defeat", "draw": "A draw"}.get(outcome.get("result"), "")
            self.result = (title, adv.outcome_lines(outcome, game.world), GREEN if won else (RED if title == "Defeat" else GOLD))
            self.sub = "result"
            self.after_result = self.words_after(outcome)
        elif notes:
            self.result = ("Welcome back", notes, ORANGE)
            self.sub = "result"
        return True

    def enter_map(self, gui):
        g = self.game
        if self.explorer is None:
            try:
                self.explorer = explore.Explorer(g.save, start_town=g.world.start_town)
            except Exception as e:                           # a damaged map, PyTMX missing (TmxError), ...: said, never raised
                crashlog.note(f"Adventure: the map couldn't be opened: {type(e).__name__}: {e}")
                self.set_stage("town" if self.town else "slots")
                if self.stage == "slots":
                    self.refresh_slots()
                self.say(f"The map couldn't be opened: {e}", RED)
                return
        self.set_stage("map")
        self.town = None
        self.shop = None

    def enter_town(self, gui, town_id, announce=True, tab=None):
        g = self.game
        try:
            rduel.visit_town(g.save, town_id, g.world)
        except KeyError:
            self.say(f"There is no town {town_id!r} in the Adventure's data.", RED)
            return
        g.save.world.update(map=town_id, x=None, y=None) if self.explorer is None else self.explorer.store()
        g.save.world["in_town"] = town_id                    # a game saved in a town opens there again
        self.town = town_id
        self.shop = rshop.Shop(g.save, town_id, g.world, g.numbers, g.pool, g.tiers(), g.lists())
        self.write()
        self.set_stage("town")
        self.tab = tab if tab in {k for k, _l in TABS} else "singles"     # Round RPG12: a counter opens its own tab
        if announce:
            gui.play_cue("ui.dialog_open")

    def leave_town(self, gui):
        g = self.game
        try:
            if self.explorer is None:
                self.explorer = explore.Explorer(g.save, start_town=g.world.start_town)
            self.explorer.leave_town(self.town)
        except Exception as e:                               # a damaged map, PyTMX missing (TmxError), ...: said, never raised
            crashlog.note(f"Adventure: the map couldn't be opened: {type(e).__name__}: {e}")
            self.say(f"The map couldn't be opened: {e}  (Main menu, top right, saves and leaves.)", RED)
            return
        g.save.world.pop("in_town", None)
        self.write()
        self.enter_map(gui)

    def write(self):
        if self.game is None:
            return True
        if self.retry and self.game.save.pending_duel is not None:    # the duel's result first, now that the disk may take it
            try:
                out = adv.finish_loaded(self.game, self.retry)
                self.retry = None
                self.result = ("The duel", ["Your last duel's result is saved now."] + adv.outcome_lines(out, self.game.world), GOLD)
                self.sub = self.sub or "result"
            except Exception as e:
                crashlog.note(f"Adventure: the duel's result still couldn't be saved: {type(e).__name__}: {e}")
        try:
            self.game.write()
            return True
        except OSError as e:
            crashlog.note(f"Adventure: the save couldn't be written: {type(e).__name__}: {e}")
            self.say(f"Your game couldn't be saved: {e}", RED)
            return False

    def adventure_close(self):
        """The program is closing with this window open: where you stand is saved."""
        if self.stage == "map" and self.explorer is not None:
            self.explorer.store()
        self.write()

    def leave(self, gui):
        """Back to the main menu (the game is saved first)."""
        if self.stage == "map" and self.explorer is not None:
            self.explorer.store()
        self.write()
        self.done = True
        if hasattr(gui, "open_title"):
            gui.open_title()
            if getattr(gui, "boot", None) is not None:
                gui.boot.to_menu()

    # ---- a new game ----------------------------------------------------------------------------------------------------------
    def new_game(self, gui, slot, sure=False):
        row = next((r for r in self.slots if r["slot"] == slot), None)
        if row and row["used"] and not sure:
            who = row.get("name") or "the game"
            self.confirm = (f"Replace slot {slot}?", [f"{who}'s game in slot {slot} will be replaced by a new one. This can't be undone."],
                            "Replace it", ("new", slot))
            self.sub = "confirm"
            return
        try:
            self.flow = newgame.NewGameFlow(pool=self.pool)
        except Exception as e:
            self.say(f"A new game can't be started: {e}", RED)
            return
        self.slot = slot
        self.field.text = ""
        self.set_stage("name")

    def name_next(self, gui):
        why = self.flow.set_name(self.field.text)
        if why:
            self.say(why, ORANGE)
            gui.play_cue("ui.error")
            return
        try:
            self.flow.offer()
        except Exception as e:                               # Forge's Jumpstart files missing
            self.flow.undo()
            self.say(f"The starting decks can't be read: {e}", RED)
            return
        self.set_stage("pick")

    def pick(self, gui, code):
        try:
            self.flow.pick(code)
        except ValueError as e:
            self.say(str(e), RED)
            return
        gui.play_cue("card.pickup")
        self.set_stage("ready" if self.flow.step == "done" else "pick")

    def back_new(self, gui):
        if self.stage == "name":
            self.flow = None
            self.refresh_slots()
            self.set_stage("slots")
            return
        self.flow.undo()
        self.set_stage("name" if self.flow.step == "name" else "pick")
        if self.stage == "name":
            self.field.text = ""

    def begin(self, gui):
        try:
            import version
            forge = version.forge_build() or ""
        except Exception:
            forge = ""
        try:
            self.game = adv.Game.create(self.flow, self.slot, self.folder, forge=forge)
        except (rsave.SaveError, OSError, ValueError) as e:
            self.say(f"The new game couldn't be saved: {e}", RED)
            return
        self.pool = self.game.pool
        self.flow = None
        self.explorer = None
        gui.play_cue("ui.menu_select")
        self.enter_town(gui, self.game.world.start_town, announce=False)
        self.result = (f"Welcome, {self.game.save.name}", [WELCOME], GOLD)
        self.sub = "result"

    # ---- walking ---------------------------------------------------------------------------------------------------------------
    def soundscape(self):
        """(ambience cue, music mode) while this window is open (ForgeTable.soundscape asks): the title's on the slots and the new
        game's screens; on the map the region's ambience and the game's playlist (a dungeon is the graveyard's); a town and the
        deck editor the deck screen's track."""
        a = self.sound_area()
        if a is not None and self.stage in ("map", "town", "deck", "pack", "collection"):
            # Round RPG15: the place's own music and ambience (map properties)
            amb = a.ambience or ("amb.graveyard" if a.dungeon else "amb.plain")
            if self.sub == "fight" and getattr(self.fight, "encounter", None) == "boss":
                return amb, "loop:" + BOSS_MUSIC             # the boss's screen: his music (the table keeps it for the duel)
            music = self.place_music(a)
            if music:
                return amb, "loop:" + music
            if self.stage == "map":
                return amb, "game"
        if self.stage in ("town", "deck", "pack", "collection"):
            return "amb.cathedral", "deck"
        return "amb.title", "title"

    def place_music(self, a):
        """Round RPG-NM (Karl's night picks, 25 Oct 2026): a map's night_music in the clock's dusk and night phases (18:00-05:00 -
        Round RPG18b, Karl 25 Oct: "from dusk"; RPG-NM had night only), else its music; dawn keeps the day music. Only an outdoor
        map follows the clock. A new mode fades the old track out and the new one in
        (audio.AudioDirector.set_music), so the change at 18:00 and 05:00 is a fade, not a cut."""
        game, ex = getattr(self, "game", None), getattr(self, "explorer", None)
        save = game.save if game is not None else getattr(ex, "save", None)    # the same save in play; a bare flow has only the walk's
        if a.night_music and a.outdoor and save is not None \
                and clock.phase(clock.now(save.world)) in NIGHT_MUSIC_PHASES:
            return a.night_music
        return a.music

    def sound_area(self):
        """The map whose music and ambience play: the Explorer's, or - Round RPG15c (review R6) - when a game opens in a shop
        before any map was made, the map the town is on (the shop sounded like the deck screen until you walked out and in)."""
        if self.explorer is not None:
            return self.explorer.area
        if self.town is None:
            return None
        try:
            name = explore.town_map(self.town)
            return explore.load_area(name) if name else None
        except Exception as e:                            # a damaged map: the deck screen's sounds, as before
            crashlog.note(f"Adventure: no music for {self.town}: {type(e).__name__}: {e}")
            return None

    def walk_tick(self, gui, now, pressed=None):
        """Every tick of the table's loop: walk on the map. pressed: (dx, dy) instead of the keyboard (tests)."""
        tap = getattr(self, "tap", None)                     # Round RPG-TAP (getattr: a test's stand-in flow has none)
        if tap is not None and (self.stage != "map" or self.explorer is None):
            self.tap = tap = None
        if self.stage != "map" or self.sub is not None or self.explorer is None or getattr(gui, "overlay", None) is not None:
            self.last = None                                 # nothing walks behind a window (F8's bug report, the cog)
            return
        dt = 0.0 if self.last is None else min(MAX_DT, max(0.0, now - self.last))
        self.last = now
        if pressed is None:
            try:
                k = pygame.key.get_pressed()
                dx = (k[pygame.K_RIGHT] or k[pygame.K_d]) - (k[pygame.K_LEFT] or k[pygame.K_a])
                dy = (k[pygame.K_DOWN] or k[pygame.K_s]) - (k[pygame.K_UP] or k[pygame.K_w])
            except pygame.error:
                dx = dy = 0
        else:
            dx, dy = pressed
        if (dx or dy) and tap is not None:
            self.tap = tap = None                            # Round RPG-TAP: any walking key ends a tap's walk
        if not dt:
            return
        if tap is not None:                                  # Round RPG-TAP: a tap's walk, through the same Explorer.step
            self.tap_tick(gui, dt)
            return
        AdventureFlow.after_step(self, gui, self.explorer.step(dx, dy, dt), dt)    # (the class's: stand-in flows call this)

    def after_step(self, gui, event, dt):
        """What follows one Explorer.step of dt seconds (the keys' or a tap's walk): the clock, the notes, and what was walked
        into. True when something happened (a duel, a town, a map, an event)."""
        if self.explorer.walker.moving:                      # Round RPG15 (Karl's "A"): the clock runs while you walk
            clock.advance(self.game.save.world, dt)
        for note in self.explorer.take_notes():             # Round RPG16: "Night falls.", a rare that came out
            self.say(note, GOLD)
        if event is None:
            return False
        AdventureFlow.walk_event(self, gui, event)
        return True

    def walk_event(self, gui, event):
        kind, what = event
        if kind == "event":                                  # Round RPG16: a random event (rpg.events), in the dialogue box
            self.start_event(gui, what)
            return
        if kind == "town":
            self.enter_town(gui, what, tab=getattr(self.explorer.came_from, "tab", None))
        elif kind in ("encounter", "random"):          # Round RPG13: a foe walked into, or one met at random in a dungeon
            self.explorer.store()
            self.write()
            self.fight = what
            self.encounter = None                      # the way in starts from the next frame's picture of the map
            intro = self.boss_words(getattr(what, "enemy", None), getattr(what, "encounter", None), "intro")
            if intro:                                  # Round RPG14: a boss speaks first (kinds_text "intro"), then the way in
                self.talk_conv, self.talk_after = None, "fight"
                self.talk_box = rpg_ui.DialogueBox(intro[0], intro[1], time.monotonic())
                self.sub = "talk"
                gui.play_cue("ui.dialog_open")
                return
            self.sub = "fight"
            gui.play_cue("rpg.encounter")              # rpg_encounter.STING (a literal: test_au1 finds played cues by their text)
        elif kind == "map":
            self.say(f"{self.explorer.area.title}", GOLD)
            self.visit_here()                                # Round RPG15b: walking into a town brings the beaten foes back
            if what.startswith("warrens"):               # Round RPG14: the people of Lantern Hollow know you went down
                talk.set_flag(self.game.save.world, talk.WARRENS_FLAG)
            self.write()

    # ---- Round RPG-TAP: a tap (a pen, a finger, the mouse) walks there, or up to a person, door or foe and acts -----------------
    def map_tap(self, gui, pos):
        """A left press on the map: what is there becomes the walk's goal (rpg.tapwalk.goal_at); no way there says so."""
        ex = self.explorer
        if self.camera is None or (self.hud is not None and self.hud.collidepoint(pos)):
            return
        mx, my = self.camera.to_map(*pos)
        goal, why = tapwalk.goal_at(ex, mx, my)
        if goal is not None and tapwalk.plan(ex, goal)[0] is None:
            goal, why = None, tapwalk.NO_WAY
        if goal is None:
            self.tap = None
            self.say(why, ORANGE)
            gui.play_cue("ui.error")
            return
        self.tap = tapwalk.TapWalk(goal)
        if self.message and self.message[0] in (tapwalk.NO_WAY, tapwalk.WALL, tapwalk.BLOCKED):
            self.message = None

    def tap_tick(self, gui, dt):
        """Walk a tap's way on by dt seconds (rpg.tapwalk.TapWalk), then talk, stop or say why not."""
        walk = self.tap
        status = walk.tick(self.explorer, dt, lambda event, part: self.after_step(gui, event, part))
        if status == "walking":
            return
        if self.tap is walk:
            self.tap = None
        if status == "talk" and self.sub is None:
            self.talk_to(gui)                                # beside them, facing them: as Enter does
        elif status == "blocked":
            self.say(tapwalk.BLOCKED, ORANGE)

    def fight_go(self, gui):
        trigger, self.fight = self.fight, None
        enc, self.encounter = self.encounter, None
        index = enc.deck_index if enc is not None and enc.trigger is trigger else None    # the deck "See its deck" showed
        self.sub = None
        try:
            duel = adv.start_duel(self.game, trigger, index=index)
        except rduel.DuelError as e:
            self.result = ("No duel", [str(e)], ORANGE)
            self.sub = "result"
            gui.play_cue("ui.error")
            return
        except OSError as e:
            self.game.save.pending_duel = None               # never left marked in memory (every later Fight would refuse)
            self.result = ("No duel", [f"Your game couldn't be saved, so the duel didn't start: {e}"], RED)
            self.sub = "result"
            return
        start = getattr(gui, "start_adventure_duel", None)
        problem = start(self, duel) if start else "This copy can't start duels."
        if problem:
            adv.cancel_duel(self.game)
            self.result = ("No duel", [problem], RED)
            self.sub = "result"

    # ---- Round RPG14: people, the inn, rematches, the pause window ------------------------------------------------------------
    def talk_to(self, gui):
        """Enter / Space on the map: talk to the person in front of you (rpg.talk). False when nobody is there."""
        ex = self.explorer
        nid = ex.facing_npc() if ex is not None else None
        if nid is None:
            return False
        world = self.game.save.world
        if hasattr(ex, "talked_to"):
            ex.talked_to(nid)                     # Round RPG18b: a person walking carries on once the talk ends
        try:
            conv = talk.Conversation(nid, world)
        except (talk.TalkError, OSError, ValueError) as e:
            self.say(f"{nid} has nothing to say ({e}).", ORANGE)
            return True
        pages = list(conv.pages)
        if conv.reward:
            pages += [" ".join(adv.give_reward(self.game, conv.reward))] if conv.reward.get("gold") or conv.reward.get("card") else []
        pages += list(conv.after)                                    # the quest's "done" words, once, after the reward
        self.talk_shop = ex.facing_shop()                            # Round RPG15b: a shopkeeper's words, then the shop
        ex.store()
        self.write()
        self.talk_conv = conv
        self.talk_box = rpg_ui.DialogueBox(conv.name, pages, time.monotonic(), choice=conv.choice, default=conv.choice_default)
        self.sub = "talk"
        gui.play_cue("ui.dialog_open")
        return True

    def talk_do(self, gui, result):
        """What the dialogue box said happened: "answer" (the choice was made), "close" (the talk is over), None (nothing more)."""
        conv = self.talk_conv
        if self.talk_event is not None and result in ("answer", "close"):
            self.event_do(gui, result)
            return
        if result == "answer" and conv is not None:
            more = conv.answer(self.talk_box.answer, getattr(self.talk_box, "picked", None))   # RPG18b: Marta's three answers
            if conv.rest:
                self.sleep_at_inn(conv.rest_until or "morning")
            self.write()
            if conv.rematch:
                self.close_talk()
                self.start_rematch(gui)
            elif more:
                self.talk_box = rpg_ui.DialogueBox(conv.name, more, time.monotonic())
            else:
                self.close_talk()
        elif result == "close":
            after, self.talk_after = self.talk_after, None
            shop, self.talk_shop = self.talk_shop, None
            self.close_talk()
            if after == "fight" and self.fight is not None:          # the boss has spoken: the way into the duel
                self.encounter = None
                self.sub = "fight"
                gui.play_cue("rpg.encounter")              # rpg_encounter.STING (a literal: test_au1 finds played cues by their text)
            elif shop is not None and self.explorer is not None:     # Round RPG15b: the shopkeeper has spoken - their shop
                self.explorer.came_from = shop
                self.enter_town(gui, shop.town, tab=shop.tab)
            else:
                gui.play_cue("ui.dialog_close")

    def sleep_at_inn(self, until="morning"):
        """Round RPG15c (review R3): the innkeeper's "Sleep until morning" - before, every word with her did this. The inn is
        the return point, the clock goes on to 07:00 (Round RPG15, Karl's "A") and the beaten foes come back (Round RPG15b).
        Round RPG17 (Karl 24 Oct Q5): until="dusk" - "Rest until dusk", by day: the clock goes on to 18:00, the rest the same."""
        ex, world = self.explorer, self.game.save.world
        if ex is None or explore.INN_SPAWN not in ex.area.spawns:
            return
        world["inn"] = {"map": ex.area.name, "spawn": explore.INN_SPAWN}
        if until == "dusk":
            clock.to_dusk(world)
        else:
            clock.to_morning(world)
        self.visit_here()
        self.woke()                                                  # Round RPG16: the morning's people and foes at once
        ex.store()

    def show_hint(self, gui):
        """Round RPG15b (Karl 19:07): the Hint button, the Menu's Hint and the H key - what to do next, in the dialogue box
        (rpg.hints: the first hint in rpg/data/hints.json whose conditions hold)."""
        ex = self.explorer
        if ex is None:
            return False
        world = self.game.save.world
        try:
            _hid, pages = hints.hint_for(world, ex.area.name, clock.now(world))
        except (hints.HintError, talk.TalkError, OSError, ValueError) as e:
            pages = [f"No hint here ({e})."]
        self.talk_conv, self.talk_after, self.talk_shop = None, None, None
        self.talk_box = rpg_ui.DialogueBox(hints.NAME, pages or ["No hint here."], time.monotonic())
        self.sub = "talk"
        gui.play_cue("ui.dialog_open")
        return True

    def woke(self):
        """Round RPG16: after a night's sleep everything is as morning has it at once (the screen changes anyway), and the map
        says so."""
        ex = self.explorer
        if ex is not None:
            ex.refresh(force=True)
            ex.take_notes()
            note = explore.phase_note(clock.phase(clock.now(self.game.save.world)))
            if note:
                self.say(note, GOLD)

    def visit_here(self):
        """Round RPG15b: standing on a town's own map (its map property town) is a visit to that town: the beaten foes come back
        and it is the last town (rpg.duel.visit_town) - before, only opening a shop's window did that (the slice walk's F3)."""
        ex = self.explorer
        town = getattr(getattr(ex, "area", None), "town", None)
        if not town:
            return
        try:
            rduel.visit_town(self.game.save, town, self.game.world)
        except KeyError:
            pass

    # ---- Round RPG16: random events ----------------------------------------------------------------------------------------
    def start_event(self, gui, eid):
        """A random event (rpg/data/events.json) in the dialogue box: its title as the name, its pages, its choice."""
        try:
            e = events.events()[eid]
        except (KeyError, OSError, ValueError):
            return
        self.explorer.store()               # Round RPG16b (review R7): the event counts as happened when it is answered or closed
        self.write()                        # (event_do), so a game closed with its box open meets it again
        self.talk_conv, self.talk_after, self.talk_shop = None, None, None
        self.talk_event = [eid, False]
        choice = events.choice_of(eid, e)          # Round RPG16d: two answers that are words, or none (the box len()s each one)
        self.talk_box = rpg_ui.DialogueBox(e.get("title") or "", events.pages_of(e), time.monotonic(), choice=choice)
        self.sub = "talk"
        gui.play_cue("ui.dialog_open")

    def event_do(self, gui, result):
        """The event's box answered or closed: a choice's answer says its own pages and gives its effects; an event with no
        choice gives its effects when the box closes. What was given is said on the map's message line."""
        eid, answered = self.talk_event
        e = events.events().get(eid) or {}
        if not answered:
            events.fired(self.game.save.world, eid)       # RPG16b (R7): its time and a once event's "done" (close_talk writes it)
        if result == "answer":
            ans = "yes" if self.talk_box.answer else "no"
            lines = self._event_effects(eid, ans)
            self.talk_event = [eid, True]
            self.talk_box = rpg_ui.DialogueBox(e.get("title") or "", events.pages_of(e, ans) + lines, time.monotonic())
            return
        if not answered and "choice" not in e:
            lines = self._event_effects(eid, None)
            lines += self._event_notes()                   # Round RPG16d (review T7): the phase its minutes reached, on the same line
            if lines:
                self.say("  ".join(lines), GOLD)           # Round RPG16c (review N3): every line, not only the last
        self.talk_event = None
        self.close_talk()
        gui.play_cue("ui.dialog_close")

    def _event_effects(self, eid, answer):
        try:
            lines = events.apply(self.game, eid, answer)
        except Exception as ex:                    # Round RPG16d (review T5): whatever the effect raises, the box still closes
            crashlog.note(f"Adventure: the event {eid}'s effect failed: {type(ex).__name__}: {ex}")
            lines = [f"(the event's effect failed: {ex})"]
        self.write()
        return lines

    def _event_notes(self):
        """Round RPG16d (review T7): an event's "minutes" can take the clock into a new phase; the walk's next refresh would say
        "Night falls." on the map's one message line and replace the event's own line before anyone read it. The refresh is done
        now and its notes go on the end of the event's line (the next frame then has nothing new to say)."""
        ex = self.explorer
        if ex is None or getattr(ex, "walker", None) is None:
            return []
        try:
            ex.refresh()
        except Exception as e:                     # the walk's own refresh will try again (and say what went wrong there)
            crashlog.note(f"Adventure: after an event, the map couldn't be brought up to date: {type(e).__name__}: {e}")
            return []
        return ex.take_notes()

    def close_talk(self):
        self.talk_conv, self.talk_box = None, None
        self.talk_event = None
        if self.sub == "talk":
            self.sub = None
        self.write()

    @staticmethod
    def boss_words(kind, encounter, key):
        """(name, pages) of a boss's kinds_text "intro" / "defeated" words, or None (anyone else, or no words)."""
        if encounter != "boss" or not kind:
            return None
        try:
            pages = list(encounters.kind_text(kind).get(key) or ())
        except (OSError, ValueError, KeyError):
            pages = []
        return (adv.enemy_name(kind), pages) if pages else None

    def words_after(self, outcome):
        """What is said in the dialogue box after a duel's result window: the innkeeper when a lost duel wakes you at her inn
        (inn.json "woke"), a beaten boss's last words (kinds_text "defeated"). None for everything else."""
        if outcome.get("inn"):
            try:
                pages = list(talk.inn().get("woke") or ())
                name = (talk.npcs().get(talk.INNKEEPER) or {}).get("name") or "The innkeeper"
            except (OSError, ValueError):
                pages, name = [], ""
            return (name, pages) if pages else None
        if outcome.get("result") == "won" and outcome.get("encounter") == "boss":
            return self.boss_words(outcome.get("kind"), "boss", "defeated")
        return None

    def start_rematch(self, gui):
        """The hall's keeper's rematch: one of the kinds you have beaten, on the encounter screen (rpg.encounters.RematchFoe)."""
        beaten = sorted(k for k, n in (self.game.save.record.get("defeated") or {}).items() if n)
        if not beaten:
            self.result = ("No rematch", ["You haven't beaten anyone the hall remembers yet."], ORANGE)
            self.sub = "result"
            return
        self.fight = encounters.RematchFoe(self.game.save.rng("rematch").choice(beaten))
        self.encounter = None
        self.sub = "fight"
        gui.play_cue("rpg.encounter")              # rpg_encounter.STING (a literal: test_au1 finds played cues by their text)

    def open_pause(self, gui):
        """Esc on the map: the pause window (DawnLike's window and font): Back to the map / Your deck / Save / Settings / Save and
        leave, beside your gold, deck and quests."""
        if self.explorer is not None:
            self.explorer.store()
        self.write()
        g = self.game
        info = g.panel_info()
        lines = [(g.save.name, gfx.DB_GOLD), (f"Gold  {g.save.gold}", gfx.DB_TEXT)]
        if info.get("deck"):
            cards = f" ({info['cards']} cards)" if info.get("cards") is not None else ""
            lines.append((f"Deck  {info['deck']}{cards}", gfx.DB_TEXT))
        lines.append((f"Slot {self.slot}. Your game is saved.", gfx.DB_DIM))
        lines.append(("", gfx.DB_DIM))
        lines.append(("Quests", gfx.DB_GOLD))
        try:
            book = talk.journal(g.save.world)
        except (OSError, ValueError):
            book = []
        for title, line, state in book:
            mark = {"ready": "Done! Go back:", "done": "Finished:"}.get(state, "")
            lines.append((f"{title}" + (f" - {mark}" if mark else ""), gfx.DB_TEXT if state != "done" else gfx.DB_DIM))
            if state != "done":
                lines.append((line, gfx.DB_DIM))
        if not book:
            lines.append(("None yet. Talk to people in town.", gfx.DB_DIM))
        self.menu = rpg_ui.ListMenu("Menu", self.pause_items(gui), lines)
        self.sub = "pause"

    def pause_items(self, gui):
        """PAUSE_ITEMS, the Light item saying how it is now (Round RPG15)."""
        return [(n, (f"Light: {'on' if light_on(gui) else 'off'}" if n == "light" else label), on) for n, label, on in PAUSE_ITEMS]

    def encounter_do(self, gui, action):
        """Round RPG13: what the encounter screen's choice does - "fight" starts the duel, "no" goes back to the map, "refused" (a
        boss) only sounds the error; None (the deck view, the cursor) nothing more."""
        if action == "fight":
            self.fight_go(gui)
        elif action == "no":
            self.fight, self.encounter, self.sub = None, None, None
            gui.play_cue("ui.dialog_close")
        elif action == "refused":
            gui.play_cue("ui.error")

    def draw_encounter(self, gui):
        """Round RPG13: the way into a fight and its screen (rpg_encounter). The first frame takes the picture of the map the
        spiral covers."""
        now = time.monotonic()
        scr = gui.screen
        if self.encounter is None or self.encounter.trigger is not self.fight:
            snap, backdrop, dark = None, "dungeon", 0.0
            if self.stage == "map" and self.explorer is not None:
                self.draw_map(gui)
                snap, backdrop = scr.copy(), self.explorer.area.backdrop
            indoors = getattr(self.fight, "backdrop", None)                      # Round RPG14: a rematch is in the hall
            if self.explorer is not None and light_on(gui) and not indoors:      # Round RPG15: the place's darkness now, the foe lit
                dark = self.explorer.area.darkness_at(clock.now(self.game.save.world))   # (Round RPG15c, review N6: not the
            backdrop = indoors or backdrop                                       # town's night in the candle-lit hall)
            self.encounter = rpg_encounter.EncounterScreen(self.game, self.fight, now,
                                                           animations=getattr(gui, "animations", True), snapshot=snap,
                                                           backdrop=backdrop, darkness=dark)
        mouse = tuple(getattr(gui, "mouse", (0, 0)))
        if mouse != self._enc_mouse:
            self._enc_mouse = mouse
            self.encounter.hover(mouse, scr.get_size(), now)
        self.rect = scr.get_rect()
        self.buttons = list(self.encounter.draw(scr, now))

    # ---- the deck editor ---------------------------------------------------------------------------------------------------------
    def open_deck(self, gui, back):
        if self.stage == "map" and self.explorer is not None:
            self.explorer.store()
        self.editor = adv.DeckEditor(self.game)
        self.deck_back = back
        self.sub = None
        self.set_stage("deck")

    def deck_done(self, gui, keep=True):
        e = self.editor
        if keep and e is not None and e.changed:
            try:
                left = e.apply()
            except OSError as err:
                self.say(f"Your deck couldn't be saved: {err}", RED)
                return
        else:
            left = self.game.deck_problems()
        self.editor = None
        back = self.deck_back
        if back == "town" and self.town:
            self.stage = "town"
            self.message = None
        else:
            self.enter_map(gui)
        if left:
            self.say("Your deck can't be played yet: " + left[0].text, ORANGE)

    # ---- the shop -----------------------------------------------------------------------------------------------------------------
    def shop_do(self, gui, what, *args):
        s = self.shop
        try:
            if what == "buy":
                o = s.buy_single(args[0])
                self.say(f"Bought {o.name} for {o.price} gold.", GREEN)
            elif what == "basic":
                cost = s.buy_basic(args[0], int(args[1]))
                self.say(f"Bought {args[1]} {args[0]} for {cost} gold.", GREEN)
            elif what == "sell":
                got = s.sell(args[0], 1)
                self.say(f"Sold one {args[0]} for {got} gold.", GREEN)
            elif what == "pack":
                cards = s.buy_pack(args[0])
                name = next((n for c, n, _p in s.packs() if c == args[0]), args[0])
                self.opened = (name, cards)
                self.set_stage("pack")
        except rshop.ShopError as e:
            self.say(str(e), ORANGE)
            gui.play_cue("ui.error")
            return
        self.write()
        gui.play_cue("card.pickup")

    # ---- input --------------------------------------------------------------------------------------------------------------------
    def press(self, gui, name):
        gui.play_cue("ui.click")
        head, _sep, rest = name.partition(":")
        if self.sub is not None:
            self.press_sub(gui, name)
            return
        if head == "slot":
            self.open_slot(gui, int(rest))
        elif head == "new":
            self.new_game(gui, int(rest))
        elif name == "menu":
            self.leave(gui)
        elif name == "next":
            self.name_next(gui)
        elif head == "hero":
            self.flow.set_hero(rest)
        elif name == "back":
            self.back_new(gui)
        elif name == "reroll":
            self.flow.reroll()
        elif head == "pick":
            self.pick(gui, rest)
        elif name == "begin":
            self.begin(gui)
        elif name == "pause":
            self.open_pause(gui)
        elif name == "hint":                                 # Round RPG15b: the Hint button beside Menu
            self.show_hint(gui)
        elif head == "tab":
            self.tab = rest
            self.message = None
        elif name == "leave_town":
            self.leave_town(gui)
        elif name == "town_deck":
            self.open_deck(gui, "town")
        elif head == "buy":
            self.shop_do(gui, "buy", rest)
        elif head == "pack":
            self.shop_do(gui, "pack", rest)
        elif head == "basic":
            land, _s, k = rest.rpartition(":")
            self.shop_do(gui, "basic", land, k)
        elif head == "sell":
            self.shop_do(gui, "sell", rest)
        elif name == "pack_done":
            self.opened = None
            self.stage = "town"
            self.message = None
        elif name == "collection_done":
            self.enter_map(gui)
        elif name == "deck_done":
            self.deck_done(gui, True)
        elif name == "deck_cancel":
            self.deck_done(gui, False)
        elif head == "colour":
            self.editor.toggle_colour(rest)
            self.scroll["deckpool"] = 0
        elif head == "group":
            self.editor.set_group(rest)
            self.scroll["deckpool"] = 0
        elif name == "clear_search":
            self.editor.search = ""
            self.scroll["deckpool"] = 0

    def press_sub(self, gui, name):
        sub = self.sub
        if sub == "fight":
            self.encounter_do(gui, self.encounter.choose(name, time.monotonic()) if self.encounter is not None else name)
        elif sub == "result":
            self.result = None
            self.message_box = None
            self.sub = None
            after, self.after_result = self.after_result, None
            if after and self.stage == "map":                        # Round RPG14: the innkeeper / a beaten boss speaks
                self.talk_conv, self.talk_after = None, None
                self.talk_box = rpg_ui.DialogueBox(after[0], after[1], time.monotonic())
                self.sub = "talk"
        elif sub == "talk":                          # Round RPG14: the dialogue box's clicks
            if self.talk_box is not None:
                self.talk_do(gui, self.talk_box.pick(name, time.monotonic()))
        elif sub == "pause":
            if name == "save":                       # Round RPG14: the pause window's Save and Settings
                self.explorer.store() if self.explorer is not None else None
                if self.write():
                    self.sub, self.menu = None, None
                    self.say("Saved.", GREEN)
                return
            if name == "settings":
                import forge_settings as fset
                gui.overlay = fset.SettingsPopup()
                return
            if name == "light":                      # Round RPG15: the darkness and lights on or off; the menu stays open
                gui.rpg_light = not light_on(gui)
                save = getattr(gui, "save_settings", None)
                if save is not None:
                    save()
                if self.menu is not None:
                    self.menu.items = self.pause_items(gui)
                gui.play_cue("ui.click")
                return
            self.sub, self.menu = None, None
            if name == "hint":                       # Round RPG15b: the Menu's Hint
                self.show_hint(gui)
            elif name == "deck":
                self.open_deck(gui, "map")
            elif name == "collection":               # Round RPG14: every card you own, by type
                if self.explorer is not None:
                    self.explorer.store()
                self.set_stage("collection")
            elif name == "leave":
                self.leave(gui)
        elif sub == "confirm":
            action, self.confirm = (self.confirm or (None, None, None, None))[3], None
            self.sub = None
            if name == "yes" and action and action[0] == "new":
                self.new_game(gui, action[1], sure=True)

    def click(self, gui, pos, button):
        if button == 1 and self._last_tap is not None:       # Round RPG-TAP: a pen tap that comes twice acts once
            when, at = self._last_tap
            slop = PEN_SLOP * max(1.0, getattr(self, "fs", 1.0))
            if time.monotonic() - when < PEN_REPEAT and abs(pos[0] - at[0]) <= slop and abs(pos[1] - at[1]) <= slop:
                return
        self._last_tap = None
        name = self.button_at(pos)
        if name:
            self.press(gui, name)
            return
        if self.sub is not None:
            return
        if self.stage == "map" and self.explorer is not None:
            if button == 1:                                  # Round RPG-TAP: walk there, or up to it and act
                self._last_tap = (time.monotonic(), tuple(pos))
                self.map_tap(gui, pos)
            return
        for rect, kind, data in reversed(self.rows):
            if not rect.collidepoint(pos):
                continue
            if kind == "pick" and button == 1:
                self.pick(gui, data)
            elif kind == "pool":
                e = self.editor
                if button == 3:
                    if e.remove(data):
                        gui.play_cue("card.pickup")
                elif e.add(data):
                    self.message = None
                    gui.play_cue("card.pickup")
                else:
                    self.say(e.why_not(data) or "That card can't go in.", ORANGE)
                    gui.play_cue("ui.error")
            elif kind == "deckline":
                if self.editor.remove(data):
                    gui.play_cue("card.pickup")
            return

    def key(self, gui, ev):
        k = ev.key
        enter = k in (pygame.K_RETURN, pygame.K_KP_ENTER)
        esc = k == pygame.K_ESCAPE
        if self.sub == "fight":                      # Round RPG13: the encounter screen's own keys
            if self.encounter is not None:
                self.encounter_do(gui, self.encounter.key(k, time.monotonic()))
            return
        if self.sub == "talk":                       # Round RPG14: the dialogue box
            if self.talk_box is not None:
                self.talk_do(gui, self.talk_box.key(k, time.monotonic()))
            return
        if self.sub == "pause" and self.menu is not None:
            if esc:
                self.press_sub(gui, "resume")
            else:
                picked = self.menu.key(k)
                if picked:
                    gui.play_cue("ui.click")
                    self.press_sub(gui, picked)
            return
        if self.sub == "result" and self.message_box is not None:          # Round RPG14: the result window on the map
            if self.message_box.key(k):
                self.press_sub(gui, "ok")
            return
        if self.sub is not None:
            if esc:
                self.press_sub(gui, "no")
            elif enter:
                self.press_sub(gui, {"fight": "fight", "confirm": "yes"}.get(self.sub, "ok"))
            return
        st = self.stage
        if st == "map":
            if k in WALK_KEYS:
                self.tap = None                      # Round RPG-TAP: a walking key ends a tap's walk
            if esc:
                self.press(gui, "pause")
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.talk_to(gui)                    # Round RPG14: talk to the person you face
            elif k == pygame.K_h:                    # Round RPG15b: the hint (the Adventure window gets keys before the table)
                self.show_hint(gui)
            return
        if st == "slots":
            if esc:
                self.leave(gui)
            return
        if st == "name":
            if esc:
                self.back_new(gui)
            elif enter:
                self.name_next(gui)
            else:
                import forge_menu as fmenu
                self.field.key(ev, fmenu.read_clipboard)
            return
        if st in ("pick", "ready"):
            if esc:
                self.back_new(gui)
            elif enter and st == "ready":
                self.begin(gui)
            return
        if st == "town":
            if esc:
                self.leave_town(gui)
            return
        if st == "pack":
            if esc or enter:
                self.press(gui, "pack_done")
            return
        if st == "collection":
            if esc or enter:
                self.press(gui, "collection_done")
            return
        if st == "deck":
            e = self.editor
            if esc:
                if e.search:
                    e.search = ""
                else:
                    self.deck_done(gui, True)
            elif enter:
                self.deck_done(gui, True)
            elif k == pygame.K_BACKSPACE:
                e.search = e.search[:-1]
                self.scroll["deckpool"] = 0
            elif ev.unicode and ev.unicode.isprintable() and not (getattr(ev, "mod", 0) & pygame.KMOD_CTRL):
                e.search = (e.search + ev.unicode)[:40]
                self.scroll["deckpool"] = 0

    def wheel(self, gui, dy):
        key = {"town": "town:" + self.tab, "deck": "deckpool", "pick": None, "ready": None,
               "collection": "collection"}.get(self.stage)               # Round RPG15c (review R1): the Collection scrolls
        if self.stage == "deck" and getattr(self, "decklist_rect", None) is not None and self.decklist_rect.collidepoint(gui.mouse):
            key = "decklist"
        if key:
            self.scroll[key] = max(0, self.scroll.get(key, 0) - dy * WHEEL_ROWS)

    # ---- drawing --------------------------------------------------------------------------------------------------------------
    def draw(self, gui):
        L = gui.L
        self.rows = []
        self.hover = None
        self.buttons = []
        self._title_end = self._search_end = None             # Round RPG14b: set again by this frame's list titles
        fs = max(1.0, L.fs)
        self.fs = fs
        self.pad = int(16 * fs)
        self.bh = int(max(36, 42 * fs))
        self.gap = int(10 * fs)
        if self.sub == "fight":                      # Round RPG13: the encounter screen fills the window
            self.draw_encounter(gui)
            return
        if self.sub in ("talk", "pause", "result") and self.stage == "map" and self.explorer is not None:
            self.draw_map(gui)                       # Round RPG14: the map under the dialogue box / the pause window /
            self.buttons = []                        # the result window, all three in DawnLike's window and font
            if self.sub == "talk" and self.talk_box is not None:
                self.buttons = list(self.talk_box.draw(gui.screen, time.monotonic()))
            elif self.sub in ("pause", "result"):
                layer = pygame.Surface(gui.screen.get_size(), pygame.SRCALPHA)
                layer.fill(gfx.DB_BG + (150,))
                gui.screen.blit(layer, (0, 0))
                if self.sub == "pause" and self.menu is not None:
                    mouse = tuple(getattr(gui, "mouse", (0, 0)))
                    if mouse != self._enc_mouse:
                        self._enc_mouse = mouse
                        self.menu.hover(mouse)
                    self.buttons = list(self.menu.draw(gui.screen))
                elif self.sub == "result":
                    self.buttons = list(self.result_window().draw(gui.screen))
            self.rect = gui.screen.get_rect()
            return
        if self.stage in DAWN_STAGES and self.game is not None:          # Round RPG14b
            self.draw_dawn(gui)
            return
        if self.stage == "map" and self.explorer is not None:
            self.draw_map(gui)
        else:
            gui.screen.fill(gfx.BACKDROP)
            rect = self.panel(gui, L.W - 40, L.H - 40, real=True)
            getattr(self, "draw_" + self.stage)(gui, rect)
            if self.hover is not None and self.sub is None:
                self.draw_big(gui, rect)
        if self.sub is not None:
            self.draw_sub(gui)

    def draw_dawn(self, gui):
        """Round RPG14b: the shop, the pack, the deck editor or the Collection in one DawnLike window filling the screen, its words
        in DawnLike's font (DawnGui); the result window over it is rpg_ui's too."""
        scr = gui.screen
        W, H = scr.get_size()
        dg = DawnGui(gui)
        scr.fill(gfx.DB_BG)
        m = max(8, 3 * dg.k)
        rect = pygame.Rect(m, m, W - 2 * m, H - 2 * m)
        self.rect = rect
        _DAWN["k"] = dg.k
        try:
            rpg_ui.window(scr, rect, dg.k)
            getattr(self, "draw_" + self.stage)(dg, rect)
            if self.hover is not None and self.sub is None:
                self.draw_big(dg, rect)
        finally:
            _DAWN["k"] = 0
        if self.sub == "result":
            layer = pygame.Surface(scr.get_size(), pygame.SRCALPHA)
            layer.fill(gfx.DB_BG + (150,))
            scr.blit(layer, (0, 0))
            self.buttons = list(self.result_window().draw(scr))
            self.rect = scr.get_rect()
        elif self.sub is not None:
            self.draw_sub(gui)

    def result_window(self):
        """The result window (rpg_ui.MessageWindow), made again when the result changes."""
        if self.message_box is None or getattr(self.message_box, "source", None) is not self.result:
            title, lines, colour = self.result or ("", [], GOLD)
            self.message_box = rpg_ui.MessageWindow(title, lines, DAWN_COLOUR.get(tuple(colour), gfx.DB_GOLD))
            self.message_box.source = self.result
        return self.message_box

    def prefetch(self, gui, names):
        art = getattr(gui, "art", None)
        new = {n for n in names if n} - self._prefetched
        if art is None or not new:
            return
        self._prefetched |= new
        try:
            art.add_names(new)
        except Exception:
            pass

    def header(self, gui, rect, title, sub=None):
        scr, pad = gui.screen, self.pad
        tf, small = gui.font("title", True), gui.font("small")
        draw_text(scr, clip_text(title, tf, rect.w - 2 * pad), rect.x + pad, rect.y + pad, tf, GOLD)
        y = rect.y + pad + tf.get_height() + int(4 * self.fs)
        for line in ([sub] if isinstance(sub, str) else (sub or [])):
            for ln in wrap_text(line, small, rect.w - 2 * pad)[:3]:
                draw_text(scr, ln, rect.x + pad, y, small, DIM)
                y += small.get_height()
        return y + int(8 * self.fs)

    def footer(self, gui, rect, left=(), right=()):
        """Buttons along the bottom: `left` from the left edge, `right` from the right edge ((label, name, enabled, primary)).
        Returns the footer's top."""
        pad, bh = self.pad, self.bh
        y = rect.bottom - pad - bh
        x = rect.x + pad
        for label, name, on, primary in left:
            w = gui.button_width(label)
            self.button(gui, pygame.Rect(x, y, w, bh), label, name, on, primary)
            x += w + self.gap
        x = rect.right - pad
        for label, name, on, primary in right:
            w = gui.button_width(label)
            x -= w
            self.button(gui, pygame.Rect(x, y, w, bh), label, name, on, primary)
            x -= self.gap
        return y

    def draw_message(self, gui, x, y, w):
        if self.message:
            small = gui.font("small", True)
            text, colour = self.message
            for ln in wrap_text(text, small, w)[:2]:
                draw_text(gui.screen, ln, x, y, small, colour)
                y += small.get_height()

    def draw_big(self, gui, rect):
        """The card under the mouse, big, on the side of the window away from it."""
        name, card = self.hover
        h = int(rect.h * 0.55)
        w = int(h * CARD_ASPECT)
        x = rect.x + self.pad if gui.mouse[0] > rect.centerx else rect.right - self.pad - w
        r = pygame.Rect(x, rect.y + int(rect.h * 0.2), w, h)
        gfx.shadowed(gui.screen, r, radius=max(4, int(w * 0.05)))
        gui.screen.blit(card_picture(gui, name, card, w, h), r)

    def list_rows(self, gui, area, key, count, row_h):
        """The rows of a scrolling list that fit `area`: [(index, rect)]; the scroll is kept in whole rows."""
        fit = max(1, area.h // row_h)
        top = max(0, min(self.scroll.get(key, 0), max(0, count - fit)))
        self.scroll[key] = top
        out = []
        for j, i in enumerate(range(top, min(count, top + fit))):
            out.append((i, pygame.Rect(area.x, area.y + j * row_h, area.w, row_h)))
        if count > fit:
            tiny = gui.font("tiny")
            room = area.right - (getattr(self, "_title_end", None) or area.x) - self.gap
            note = f"{top + 1}-{min(count, top + fit)} of {count}  (wheel to scroll)"
            if tiny.size(note)[0] > room:                    # Round RPG14b: DawnLike's wide letters - the short form, or none
                note = f"{top + 1}-{min(count, top + fit)} of {count}"
            if tiny.size(note)[0] <= room:
                draw_text(gui.screen, note, area.right, area.y - 2, tiny, DIM, "bottomright")
        return out

    def list_title(self, gui, area, text):
        """A shop list's title over its top left corner; the list's "1-10 of 37" note keeps clear of it (Round RPG14b)."""
        r = draw_text(gui.screen, clip_text(text, gui.font("tiny"), area.w), area.x, area.y - 2, gui.font("tiny"), DIM,
                      "bottomleft")
        self._title_end = r.right

    # slots
    def draw_slots(self, gui, rect):
        scr, pad, fs = gui.screen, self.pad, self.fs
        y = self.header(gui, rect, TITLE, INTRO)
        bold, small = gui.font("body", True), gui.font("small")
        bottom = rect.bottom - pad - self.bh - self.gap
        n = max(1, len(self.slots))
        box_h = max(int(64 * fs), min(int(110 * fs), (bottom - y - self.gap * (n - 1) - 2 * small.get_height()) // n))
        for row in self.slots:
            box = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, box_h)
            round_rect(scr, box, gfx.PANEL, 8, 1, gfx.PANEL_EDGE)
            lines_h = bold.get_height() + small.get_height() * (2 if row.get("problem") else 1) if row["used"] else bold.get_height()
            tx, ty = box.x + pad, box.y + max(4, (box.h - lines_h) // 2)
            bw = max(gui.button_width("Continue"), gui.button_width("New game"))
            bh = min(self.bh, box.h - 8)
            btn = pygame.Rect(box.right - pad - bw, box.centery - bh // 2, bw, bh)
            room = btn.x - tx - pad
            if row["used"] and row.get("name"):
                when = time.strftime("%d %b %H:%M", time.localtime(row["updated"])) if row.get("updated") else ""
                draw_text(scr, clip_text(f"Slot {row['slot']}  -  {row['name']}", bold, room), tx, ty, bold, GOLD)
                wins = row.get("wins") or 0
                line = f"{row['gold']} gold   ·   {wins} duel{'s' if wins != 1 else ''} won   ·   last played {when}"
                draw_text(scr, clip_text(line, small, room), tx, ty + bold.get_height(), small, DIM)
                if row.get("problem"):
                    draw_text(scr, clip_text(row["problem"], small, room), tx, ty + bold.get_height() + small.get_height(),
                              small, ORANGE)
                self.button(gui, btn, "Continue", f"slot:{row['slot']}", True, True)
                nb = gui.button_width("New game")
                if btn.x - nb - self.gap > tx + room // 2:
                    self.button(gui, pygame.Rect(btn.x - nb - self.gap, btn.y, nb, bh), "New game", f"new:{row['slot']}")
            elif row["used"]:
                draw_text(scr, clip_text(f"Slot {row['slot']}  -  can't be read", bold, room), tx, ty, bold, RED)
                draw_text(scr, clip_text(row.get("problem") or "", small, room), tx, ty + bold.get_height(), small, ORANGE)
                self.button(gui, btn, "New game", f"new:{row['slot']}")
            else:
                draw_text(scr, clip_text(f"Slot {row['slot']}  -  empty", bold, room), tx, ty, bold, DIM)
                self.button(gui, btn, "New game", f"new:{row['slot']}", True, True)
            y += box_h + self.gap
        self.draw_message(gui, rect.x + pad, y, rect.w - 2 * pad)
        self.footer(gui, rect, left=[("Main menu", "menu", True, False)])

    # name
    def draw_name(self, gui, rect):
        pad, fs = self.pad, self.fs
        y = self.header(gui, rect, f"A new adventure  -  slot {self.slot}", "What should the game call you? (Up to "
                        f"{rsave.NAME_MAX} letters; only shown, never a file name.)")
        body = gui.font("body")
        fw = min(rect.w - 2 * pad, int(520 * fs))
        box = pygame.Rect(rect.x + pad, y + self.gap, fw, body.get_height() + 14)
        self.field.draw(gui, box, True)
        self.draw_message(gui, rect.x + pad, box.bottom + self.gap, rect.w - 2 * pad)
        foot = self.footer(gui, rect, left=[("Back", "back", True, False)], right=[("Next", "next", True, True)])
        self.draw_heroes(gui, rect, box.bottom + self.gap + 2 * gui.font("small", True).get_height() + self.gap, foot - self.gap)

    def draw_heroes(self, gui, rect, top, bottom):
        """Round RPG11: the five heroes you can walk the map as (DawnLike's class sheets, standing, facing you); the picked one in
        gold. Each is a button "hero:<name>"."""
        scr, pad, fs = gui.screen, self.pad, self.fs
        body, small = gui.font("body", True), gui.font("small")
        draw_text(scr, "Your hero", rect.x + pad, top, body, TEXT)
        top += body.get_height() + int(4 * fs)
        n, gap = len(rsave.HEROES), self.gap
        label_h = small.get_height() + int(6 * fs)
        side = min(int(110 * fs), (rect.w - 2 * pad - (n - 1) * gap) // n, bottom - top - label_h)
        if side < 24:
            return
        if self.sprites is None:
            self.sprites = rpg_map_view.Sprites()
        zoom = max(1, (side - int(12 * fs)) // 16)
        for i, hero in enumerate(rsave.HEROES):
            r = pygame.Rect(rect.x + pad + i * (side + gap), top, side, side + label_h)
            on = hero == self.flow.hero
            round_rect(scr, r, gfx.PANEL, 8, 2 if on else 1, GOLD if on else gfx.PANEL_EDGE)
            img = self.sprites.walker("down", 0, False, 16, zoom, hero)
            if img is not None:
                scr.blit(img, img.get_rect(center=(r.centerx, r.y + side // 2)))
            draw_text(scr, clip_text(hero, small, side - 8), r.centerx, r.bottom - int(4 * fs), small, GOLD if on else TEXT,
                      "midbottom")
            self.buttons.append((r, f"hero:{hero}"))

    # pick
    def draw_pick(self, gui, rect):
        scr, pad, fs = gui.screen, self.pad, self.fs
        f = self.flow
        y = self.header(gui, rect, f"Your starting deck  -  half-deck {len(f.picks) + 1} of {newgame.PICKS}", PICK_INTRO)
        foot = self.footer(gui, rect, left=[("Back", "back", True, False)], right=[("Show others", "reroll", True, False)])
        side_w = int(rect.w * 0.2)
        area = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad - side_w - self.gap, foot - self.gap - y)
        offer = f.offer()
        pool = f._pool()
        self.prefetch(gui, [face_of(h, pool) for h in offer])
        n = max(1, len(offer))
        cw = (area.w - self.gap * (n - 1)) // n
        small, tiny, bold = gui.font("small"), gui.font("tiny"), gui.font("body", True)
        for i, h in enumerate(offer):
            col = pygame.Rect(area.x + i * (cw + self.gap), area.y, cw, area.h)
            hot = col.collidepoint(gui.mouse)
            round_rect(scr, col, gfx.PANEL, 8, 2 if hot else 1, GOLD if hot else gfx.PANEL_EDGE)
            self.rows.append((col, "pick", h.code))
            inner = col.inflate(-2 * int(8 * fs), -2 * int(8 * fs))
            ph = min(int(inner.h * 0.42), int(inner.w / CARD_ASPECT))
            pw = int(ph * CARD_ASPECT)
            face = face_of(h, pool)
            yy = inner.y
            if face:
                pr = pygame.Rect(inner.centerx - pw // 2, yy, pw, ph)
                scr.blit(card_picture(gui, face, pool.get(face), pw, ph), pr)
                if pr.collidepoint(gui.mouse):
                    self.hover = (face, pool.get(face))
                yy = pr.bottom + int(6 * fs)
            draw_text(scr, clip_text(h.theme, bold, inner.w), inner.x, yy, bold, GOLD)
            yy += bold.get_height()
            draw_text(scr, clip_text(products.SET_NAMES.get(h.set, h.set) + (f"  ·  {h.number}" if h.number else "")
                                     + "  ·  " + products.colour_words(products.colours(h, pool)), tiny,   # Round RPG9
                                     inner.w), inner.x, yy, tiny, DIM)
            yy += tiny.get_height() + int(4 * fs)
            basics = 0
            lines = []
            for name, k in h.cards:
                card = pool.get(name)
                if card is not None and card.is_basic:
                    basics += k
                else:
                    lines.append(f"{k}  {name}" if k > 1 else name)
            if basics:
                lines.append(f"+ {basics} basic land{'s' if basics != 1 else ''}")
            fit = max(1, (inner.bottom - yy) // small.get_height())
            if len(lines) > fit:
                lines = lines[:fit - 1] + [f"... and {len(lines) - fit + 1} more"]
            for ln in lines:
                draw_text(scr, clip_text(ln, small, inner.w), inner.x, yy, small, TEXT)
                yy += small.get_height()
        side = pygame.Rect(area.right + self.gap, area.y, side_w, area.h)
        draw_text(scr, "Picked so far", side.x, side.y, gui.font("small", True), GOLD)
        yy = side.y + small.get_height() + 4
        for p in f.picks:
            draw_text(scr, clip_text(p.theme, small, side.w), side.x, yy, small, TEXT)
            yy += small.get_height()
        if not f.picks:
            draw_text(scr, "nothing yet", side.x, yy, small, DIM)
            yy += small.get_height()
        yy += int(6 * fs)
        for ln in wrap_text(f.colour_note(), small, side.w):         # Round RPG9: the colour rule as it stands now
            if yy + small.get_height() > side.bottom:
                break
            draw_text(scr, ln, side.x, yy, small, DIM)
            yy += small.get_height()
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * small.get_height(), rect.w - 2 * pad)

    def draw_ready(self, gui, rect):
        pad = self.pad
        f = self.flow
        deck = f.preview()
        total = sum(k for _n, k in deck)
        sub = (f"{total} cards from " + ", ".join(h.theme for h in f.picks) + ". Begin saves the game in slot "
               f"{self.slot} and opens the town.")
        y = self.header(gui, rect, f"{f.name}'s starting deck", sub)
        foot = self.footer(gui, rect, left=[("Back", "back", True, False)],
                           right=[("Begin the adventure", "begin", True, True)])
        self.draw_deck_text(gui, pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, foot - self.gap - y), deck, f._pool())
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * gui.font("small").get_height(), rect.w - 2 * pad)

    def draw_collection(self, gui, rect):
        """Round RPG14: the pause window's Collection - every card you own, by type, with how many (the deck editor builds
        decks from them)."""
        coll = self.game.save.collection
        cards = [(n, k) for n, k in ((n, coll.count(n)) for n in coll.names()) if isinstance(k, int) and k > 0]   # None: free
        total = sum(k for _n, k in cards)
        y = self.header(gui, rect, "Your cards", f"{total} cards, {len(cards)} different. Deck, in the menu, builds a deck "
                                                 "from them.")
        foot = self.footer(gui, rect, right=[("Back to the map", "collection_done", True, True)])
        hidden = self.draw_deck_text(gui, pygame.Rect(rect.x + self.pad, y, rect.w - 2 * self.pad, foot - self.gap - y), cards,
                                     self.pool, scroll_key="collection")
        if hidden:                                           # Round RPG15c (review R1): said, and the wheel reaches them
            small = gui.font("small")
            draw_text(gui.screen, f"{hidden} more - scroll with the mouse wheel", rect.x + self.pad, foot + self.bh // 2, small,
                      DIM, "midleft")

    def draw_deck_text(self, gui, area, deck, pool, scroll_key=None):
        """A deck as text in columns by type ("Creature (14)", then "2  Llanowar Elves" lines); the mouse shows a card big.
        Round RPG15c (review R1): with a scroll_key the columns grow as tall as the list needs and the area scrolls (self.scroll,
        in lines, the wheel), so every card can be reached; returns how many cards are out of view (0 without a scroll_key)."""
        if scroll_key is not None:
            return self.draw_deck_scrolled(gui, area, deck, pool, scroll_key)
        scr, small, bold = gui.screen, gui.font("small"), gui.font("small", True)
        groups = {}
        for name, k in deck:
            card = pool.get(name)
            groups.setdefault(adv.type_group(card), []).append((name, k, card))
        blocks = []
        for g in adv.TYPE_ORDER:
            if g in groups:
                items = sorted(groups[g], key=lambda e: (e[2].mana_value if e[2] else 99, e[0].lower()))
                blocks.append((f"{g} ({sum(k for _n, k, _c in items)})", items))
        lh = small.get_height()
        cols = max(1, min(4, area.w // int(260 * self.fs)))
        cw = (area.w - self.gap * (cols - 1)) // cols
        x, y, c = area.x, area.y, 0
        for head, items in blocks:
            need = lh * (len(items) + 1)
            if y + min(need, 3 * lh) > area.bottom and c < cols - 1:
                c += 1
                x, y = area.x + c * (cw + self.gap), area.y
            if y + lh > area.bottom:                         # Round RPG14b: no room left in the last column
                break
            draw_text(scr, clip_text(head, bold, cw), x, y, bold, GOLD)
            y += lh
            for name, k, card in items:
                if y + lh > area.bottom:
                    if c < cols - 1:
                        c += 1
                        x, y = area.x + c * (cw + self.gap), area.y
                    else:
                        break
                r = pygame.Rect(x, y, cw, lh)
                draw_text(scr, clip_text(f"{k}  {name}", small, cw), x, y, small, TEXT)
                if r.collidepoint(gui.mouse):
                    self.hover = (name, card)
                y += lh
            y += lh // 2
        return 0

    def deck_blocks(self, deck, pool):
        """[(heading, [(name, count, card)])] by type, as draw_deck_text shows them."""
        groups = {}
        for name, k in deck:
            card = pool.get(name)
            groups.setdefault(adv.type_group(card), []).append((name, k, card))
        blocks = []
        for g in adv.TYPE_ORDER:
            if g in groups:
                items = sorted(groups[g], key=lambda e: (e[2].mana_value if e[2] else 99, e[0].lower()))
                blocks.append((f"{g} ({sum(k for _n, k, _c in items)})", items))
        return blocks

    @staticmethod
    def flow_deck(blocks, rows, cols):
        """Round RPG15c: the lines placed in `cols` columns of `rows` lines, by draw_deck_text's rules (a heading moves to the next
        column when fewer than 3 of its lines fit). [(col, row, text, name, card, heading?)], or None when it doesn't fit."""
        out, c, r = [], 0, 0
        for head, items in blocks:
            if r + min(len(items) + 1, 3) > rows and r > 0:
                c, r = c + 1, 0
            if c >= cols:
                return None
            out.append((c, r, head, None, None, True))
            r += 1
            for name, k, card in items:
                if r >= rows:
                    c, r = c + 1, 0
                    if c >= cols:
                        return None
                out.append((c, r, f"{k}  {name}", name, card, False))
                r += 1
            r += 0.5
        return out

    def draw_deck_scrolled(self, gui, area, deck, pool, key):
        scr, small, bold = gui.screen, gui.font("small"), gui.font("small", True)
        lh = small.get_height()
        blocks = self.deck_blocks(deck, pool)
        widest = max([small.size(f"{k}  {n}")[0] for _h, items in blocks for n, k, _c in items] or [0])
        col_w = max(int(260 * self.fs), widest + self.gap)               # Round RPG15c: no name cut short when it can be helped
        cols = max(1, min(4, area.w // col_w))
        cw = (area.w - self.gap * (cols - 1)) // cols
        rows = max(1, area.h // lh)
        lines, tall = self.flow_deck(blocks, rows, cols), rows
        while lines is None:                                             # the columns as tall as the list needs
            tall += 1
            lines = self.flow_deck(blocks, tall, cols)
        top = max(0, min(self.scroll.get(key, 0), int(tall - rows)))
        self.scroll[key] = top
        hidden = 0
        clip = scr.get_clip()
        scr.set_clip(area)
        for c, r, text, name, card, head in lines:
            row = r - top
            if row < 0 or row + 1 > rows:
                hidden += 0 if head else 1
                continue
            x, y = area.x + c * (cw + self.gap), area.y + int(row * lh)
            if head:
                draw_text(scr, clip_text(text, bold, cw), x, y, bold, GOLD)
                continue
            draw_text(scr, clip_text(text, small, cw), x, y, small, TEXT)
            if pygame.Rect(x, y, cw, lh).collidepoint(gui.mouse):
                self.hover = (name, card)
        scr.set_clip(clip)
        return hidden

    # map
    def draw_map(self, gui):
        """Round RPG11: the camera follows the walker (rpg.explore.Camera, whole map pixels); a small window in the top left
        corner says where you are, your gold and your deck; the keys and Menu at the bottom (RPG6's side panels are gone)."""
        scr = gui.screen
        W, H = scr.get_size()
        ex, g = self.explorer, self.game
        scr.fill(gfx.BACKDROP)
        key = (W, H, ex.area.tw, ex.area.th)
        if self.camera is None or self._cam_key != key:
            self.camera, self._cam_key = explore.Camera(W, H, explore.Camera.zoom_for(H, ex.area.th)), key
        view = self.views.get(ex.area.name)
        if view is None:
            view = self.views[ex.area.name] = rpg_map_view.MapView(ex.area)
            if view.missing:                                         # Round RPG15c (review R8): said, never raised
                self.say(f"Some of this map's pictures are missing ({', '.join(sorted(set(view.missing)))}), so it is drawn "
                         "plain. Reinstalling Manticore puts them back.", ORANGE)
        view.hero = g.save.world.get("hero")
        flags = talk.flags(g.save.world)
        view.dark_tiles = ex.area.dark(flags)                        # Round RPG14: the duel hall stays dark until the boss falls
        view.lit, view.flags = light_on(gui), flags                  # Round RPG15: the map's darkness and its lights,
        view.darkness = ex.area.darkness_at(clock.now(g.save.world))  # ... by the clock on an outdoor map
        view.flicker = bool(getattr(gui, "animations", True))
        self.camera.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        cam = self.camera                                            # Round RPG16: things come and go outside this view
        ex.view_half = (int(cam.view_w / cam.zoom / ex.area.tw / 2) + 1, int(cam.view_h / cam.zoom / ex.area.th / 2) + 1)
        view.draw(scr, self.camera, ex.walker, ex.encounters(), time.monotonic(),
                  people=[p for p in ex.present_people() if p["when"]] + ex.walking_people())   # RPG17: and those walking
        self.tap_marker_rect = None
        if self.tap is not None and self.tap.goal.area == ex.area.name:     # Round RPG-TAP: where the tap's walk is going
            self.draw_tap_marker(scr, self.tap.marker(ex), ex.area)
        self.hud = self.draw_hud(gui, ex.area.title, dict(g.panel_info(), time=time_text(g.save.world)))
        small = gui.font("small")
        bw = gui.button_width("Menu")
        self.button(gui, pygame.Rect(W - bw - 8, H - self.bh - 8, bw, self.bh), "Menu", "pause")
        hw = gui.button_width("Hint")                        # Round RPG15b: the Hint button beside Menu
        self.button(gui, pygame.Rect(W - bw - 8 - self.gap - hw, H - self.bh - 8, hw, self.bh), "Hint", "hint")
        draw_text(scr, "   ".join(MAP_KEYS), 8, H - 8, small, DIM, "bottomleft", shadow=True)
        mx, my, mw = 8, H - 8 - 4 * small.get_height(), W // 2
        if self.message:
            self.draw_message(gui, mx, my, mw)

    def draw_tap_marker(self, scr, tile, area):
        """Round RPG-TAP: a small gold ring on the goal's tile (self.tap_marker_rect: where, for the tests)."""
        cam = self.camera
        cx, cy = cam.to_screen(*tapwalk.middle(area, tile))
        r = max(4, int(min(area.tw, area.th) * cam.zoom * 0.32))
        self.tap_marker_rect = pygame.Rect(cx - r, cy - r, 2 * r, 2 * r)
        pygame.draw.circle(scr, gfx.DB_BG, (cx, cy), r + 1, max(2, r // 4) + 2)
        pygame.draw.circle(scr, gfx.DB_GOLD, (cx, cy), r, max(2, r // 4))

    def draw_hud(self, gui, title, info):
        """Round RPG11: the corner window - the map's name, gold, the deck and its size. Returns its rect.
        Round RPG14: in DawnLike's window and its 8 px font, at the UI's pixel size (rpg_ui.ui_scale), like the dialogue box;
        self.hud_words holds each line as drawn, [(text, screen Rect)], for the tests (the lines no longer go through draw_text)."""
        scr = gui.screen
        W, H = scr.get_size()
        s = rpg_ui.ui_scale(W, H)
        lines = [(title, gfx.DB_GOLD)]
        if info.get("gold") is not None:
            lines.append((f"Gold {info['gold']}", gfx.DB_TEXT))
        if info.get("deck"):
            cards = f" ({info['cards']})" if info.get("cards") is not None else ""
            lines.append((f"Deck {info['deck']}{cards}", gfx.DB_TEXT))
        if info.get("time"):                                             # Round RPG15: the time of day ("Dusk 18:20")
            lines.append((info["time"], gfx.DB_DIM))
        pad = rpg_ui.PAD - 2
        cw = rpg_ui.font().size("M")[0]
        most = max(4, (W // 2 // s - 2 * pad - 8) // cw)               # characters a line may have: the window stays <= W / 2
        lines = [(t if len(t) <= most else t[:most - 3] + "...", c) for t, c in lines]
        w = max(len(t) for t, _c in lines) * cw + 2 * pad
        h = 2 * pad + len(lines) * rpg_ui.PITCH_UI - 2
        box = pygame.Surface((w, h), pygame.SRCALPHA)
        rpg_ui.window(box, box.get_rect())
        words = []
        for i, (t, c) in enumerate(lines):
            words.append((t, rpg_ui.text(box, t, pad, pad + i * rpg_ui.PITCH_UI, c)))
        x0 = y0 = 4 * s
        rect = rpg_ui._blit_scaled(scr, box, s, (x0, y0))
        self.hud_words = [(t, pygame.Rect(x0 + r.x * s, y0 + r.y * s, r.w * s, r.h * s)) for t, r in words]
        return rect

    # town
    def draw_town(self, gui, rect):
        scr, pad = gui.screen, self.pad
        g = self.game
        d = g.deck()
        wrong = g.deck_problems()
        sub = f"{g.save.gold} gold   ·   Your deck: {d['name'] if d else 'none'} ({g.deck_size()} cards)" + \
              ("  -  can't be played yet" if wrong else "")
        mw = gui.button_width("Main menu", "small")
        mh = gui.font("small").get_height() + int(12 * self.fs)
        self.button(gui, pygame.Rect(rect.right - pad - mw, rect.y + pad, mw, mh), "Main menu", "menu", fkey="small")
        y = self.header(gui, pygame.Rect(rect.x, rect.y, rect.w - mw - self.gap, rect.h), g.town_name(self.town), sub)
        # tabs and the town's own buttons
        x = rect.x + pad
        tab_h = self.bh
        for key, label in TABS:
            w = gui.button_width(label)
            self.button(gui, pygame.Rect(x, y, w, tab_h), label, f"tab:{key}", True, key == self.tab)
            x += w + self.gap
        own = (("Leave town", "leave_town"), ("Your deck", "town_deck"))
        if rect.right - pad - sum(gui.button_width(label) + self.gap for label, _n in own) <= x:
            y, x = y + tab_h + self.gap, rect.x + pad       # Round RPG14b: no room beside the tabs (big text) - a row of their own
        right = rect.right - pad
        for label, name in own:
            w = gui.button_width(label)
            if right - w > x + self.gap:
                right -= w
                self.button(gui, pygame.Rect(right, y, w, tab_h), label, name, True, name == "leave_town")
                right -= self.gap
        y += tab_h + self.gap
        small = gui.font("small")
        msg_h = 2 * small.get_height() + self.gap
        area = pygame.Rect(rect.x + pad, y + small.get_height(), int((rect.w - 2 * pad) * 0.66), rect.bottom - pad - msg_h - y - small.get_height())
        self.draw_message(gui, rect.x + pad, rect.bottom - pad - msg_h + self.gap, rect.w - 2 * pad)
        getattr(self, "draw_tab_" + self.tab)(gui, area)
        if self.hover is None and self.tab != "packs":
            side_x = area.right + 2 * self.gap
            draw_text(scr, clip_text("Point at a card to see it here.", small, rect.right - pad - side_x), side_x, area.y, small, DIM)
        if self.hover is not None:                           # the card under the mouse, big, in the room at the right
            name, card = self.hover
            side = pygame.Rect(area.right + 2 * self.gap, area.y, rect.right - pad - area.right - 2 * self.gap, area.h)
            h = min(side.h, int(side.w / CARD_ASPECT))
            w = int(h * CARD_ASPECT)
            if w > 40:
                r = pygame.Rect(side.x + (side.w - w) // 2, side.y, w, h)
                gfx.shadowed(scr, r, radius=max(4, int(w * 0.05)))
                scr.blit(card_picture(gui, name, card, w, h), r)
            self.hover = None                                # shown here, not again by draw()

    def row_h(self, gui):
        return max(self.bh, gui.font("body").get_height() + int(14 * self.fs)) + int(4 * self.fs)

    def draw_row(self, gui, r, text, sub, buttons, name=None):
        """One shop line: the text (and a dim second part), buttons at the right ((label, name, enabled, primary))."""
        scr, fs = gui.screen, self.fs
        body, small = gui.font("body"), gui.font("small")
        box = r.inflate(0, -int(4 * fs))
        hot = box.collidepoint(gui.mouse)
        round_rect(scr, box, gfx.PANEL_DEEP if not hot else gfx.PANEL, 6, 1, gfx.PANEL_EDGE)
        x = box.right - int(8 * fs)
        bh = min(self.bh, box.h - 6)
        for label, bname, on, primary in reversed(buttons):
            w = gui.button_width(label)
            x -= w
            self.button(gui, pygame.Rect(x, box.centery - bh // 2, w, bh), label, bname, on, primary)
            x -= int(6 * fs)
        room = x - box.x - int(16 * fs)
        tw = min(body.size(text)[0], int(room * 0.55)) if sub else room
        if sub and _DAWN["k"]:                               # Round RPG14b: DawnLike's wide letters - the price before the name's end
            parts = sub.split(SEP)
            while len(parts) > 1 and small.size(rpg_ui.plain(SEP.join(parts)))[0] > room * 0.6:
                drop = next((i for i, p in enumerate(parts) if "gold" not in p), None)
                if drop is None:
                    break
                parts.pop(drop)                              # the price stays; the tier, the code, "you own" go first
            sub = SEP.join(parts)
            want = small.size(rpg_ui.plain(sub))[0] + int(10 * fs) + small.size("  ")[0]
            tw = min(body.size(text)[0], max(int(room * 0.4), room - want))
        draw_text(scr, clip_text(text, body, tw), box.x + int(10 * fs), box.centery, body, GOLD if hot and _DAWN["k"] else TEXT,
                  "midleft")
        if sub:
            gap = int(22 * fs) if not _DAWN["k"] else int(10 * fs) + small.size("  ")[0]     # Round RPG14b: wide letters
            draw_text(scr, clip_text(sub, small, max(10, room - tw - gap + int(10 * fs))), box.x + gap + tw, box.centery,
                      small, DIM, "midleft")
        if hot and name:
            self.hover = (name, self.game.pool.get(name))

    def draw_tab_singles(self, gui, area):
        s, g = self.shop, self.game
        offers = s.singles()
        self.prefetch(gui, [o.name for o in offers])
        self.list_title(gui, area, f"Singles  -  restocked every {g.numbers.restock_after_duels} duels")
        if not offers:
            draw_text(gui.screen, "Nothing for sale.", area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:singles", len(offers), self.row_h(gui)):
            o = offers[i]
            owned = g.save.collection.count(o.name) or 0
            sub = f"tier {o.tier}   ·   {o.price} gold   ·   {o.left} left" + (f"   ·   you own {owned}" if owned else "")
            ok = o.left > 0 and g.save.gold >= o.price
            self.draw_row(gui, r, o.name, sub, [("Buy", f"buy:{o.slot}", ok, True)], name=o.name)

    def draw_tab_packs(self, gui, area):
        packs = self.shop.packs()
        g = self.game
        self.list_title(gui, area, "Booster packs  -  every card goes into your collection")
        if not packs:
            draw_text(gui.screen, "This town sells no packs.", area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:packs", len(packs), self.row_h(gui)):
            code, name, price = packs[i]
            self.draw_row(gui, r, name, f"{code}   ·   {price} gold", [("Buy and open", f"pack:{code}", g.save.gold >= price, True)])

    def draw_tab_lands(self, gui, area):
        basics = self.shop.basics()
        g = self.game
        self.list_title(gui, area, "Basic lands  -  as many as you like")
        if not basics:
            draw_text(gui.screen, "Basic lands are free in this game: there is nothing to buy.", area.x, area.y,
                      gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:lands", len(basics), self.row_h(gui)):
            name, price = basics[i]
            owned = g.save.collection.count(name) or 0
            buttons = [(f"+{k}", f"basic:{name}:{k}", g.save.gold >= price * k, k == 1) for k in BASIC_STEPS]
            self.draw_row(gui, r, name, f"{price} gold each   ·   you own {owned}", buttons, name=name)

    def sell_list(self):
        g, s = self.game, self.shop
        uses = adv.deck_uses(g)
        out = []
        for name in g.save.collection.names():
            if g.save.collection.is_free(name):
                continue
            have = g.save.collection.count(name)
            each = s.sell_price(name)
            used = uses.get(cardpool.normal_name(name), 0)
            out.append((name, have, each, used))
        return out

    def draw_tab_sell(self, gui, area):
        rows = self.sell_list()
        self.list_title(gui, area, "Sell  -  a card your deck still needs can't be sold")
        if not rows:
            draw_text(gui.screen, "You have nothing to sell.", area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:sell", len(rows), self.row_h(gui)):
            name, have, each, used = rows[i]
            sub = f"you own {have}   ·   {each} gold each" + (f"   ·   your deck uses {used}" if used else "")
            ok = each >= 1 and have - 1 >= used
            self.draw_row(gui, r, name, sub, [("Sell 1", f"sell:{name}", ok, False)], name=name)

    # pack
    def draw_pack(self, gui, rect):
        scr, pad = gui.screen, self.pad
        name, cards = self.opened or ("", [])
        new = sum(1 for c in cards if c.get("new"))
        y = self.header(gui, rect, f"{name}: your pack", f"{len(cards)} cards, {new} new to your collection. "
                        f"You have {self.game.save.gold} gold.")
        foot = self.footer(gui, rect, right=[("Back to the shop", "pack_done", True, True)])
        area = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, foot - self.gap - y)
        self.prefetch(gui, [c["name"] for c in cards])
        n = max(1, len(cards))
        per_row = max(1, min(8, n))
        rows = -(-n // per_row)
        while True:
            w = (area.w - self.gap * (per_row - 1)) // per_row
            h = int(w / CARD_ASPECT)
            if rows * h + (rows - 1) * self.gap <= area.h or per_row >= n:
                break
            per_row += 1
            rows = -(-n // per_row)
        h = min(h, (area.h - (rows - 1) * self.gap) // max(1, rows))
        w = int(h * CARD_ASPECT)
        tiny = gui.font("tiny", True)
        for i, c in enumerate(cards):
            r = pygame.Rect(area.x + (i % per_row) * (w + self.gap), area.y + (i // per_row) * (h + self.gap), w, h)
            card = self.game.pool.get(c["name"])
            scr.blit(card_picture(gui, c["name"], card, w, h), r)
            if c.get("new"):
                tag = pygame.Rect(r.x + 4, r.y + 4, tiny.size("NEW")[0] + 10, tiny.get_height() + 4)
                round_rect(scr, tag, gfx.BTN_GOLD_BOTTOM, 4, 1, gfx.BTN_GOLD_EDGE)
                draw_text(scr, "NEW", tag.centerx, tag.centery, tiny, gfx.BTN_GOLD_FG, "center")
            if r.collidepoint(gui.mouse):
                self.hover = (c["name"], card)

    # the deck editor
    def draw_deck(self, gui, rect):
        scr, pad, fs = gui.screen, self.pad, self.fs
        e = self.editor
        total = e.total()
        sub = (f"{total} cards (at least 60)   ·   click a card: one more in the deck;  right-click it or click a line on "
               "the right: one less;  type to search")
        y = self.header(gui, rect, f"Your deck  -  {e.name}", sub)
        foot = self.footer(gui, rect, left=[("Cancel changes", "deck_cancel", e.changed, False)],
                           right=[("Done", "deck_done", True, True)])
        left = pygame.Rect(rect.x + pad, y, int((rect.w - 2 * pad) * 0.64), foot - self.gap - y)
        right = pygame.Rect(left.right + 2 * self.gap, y, rect.right - pad - left.right - 2 * self.gap, left.h)
        # filters: colours, types, the search
        tiny, small = gui.font("tiny", True), gui.font("small")
        fh = max(int(28 * fs), tiny.get_height() + 10)
        x = left.x
        for c in adv.COLOURS:
            w = max(fh, tiny.size(c)[0] + 14 if not _DAWN["k"] else gui.button_width(c, "tiny"))     # RPG14b: the frame's room
            self.button(gui, pygame.Rect(x, y, w, fh), c, f"colour:{c}", True, c in e.colours, fkey="tiny")
            x += w + 4
        x += self.gap
        for gname in GROUP_BUTTONS:                          # every type button, on a second row when the first is full
            label = gname if gname != "Planeswalker" else "Walker"
            w = gui.button_width(label, "tiny")
            if x + w > left.right and x > left.x:
                x, y = left.x, y + fh + 4
            self.button(gui, pygame.Rect(x, y, w, fh), label, f"group:{gname}", True, e.group == gname, fkey="tiny")
            x += w + 4
        y2 = y + fh + 6
        stext = f"Search: {e.search}" if e.search else "Type to search by name or type"
        said = draw_text(scr, clip_text(stext, small, left.w - 120), left.x, y2, small, TEXT if e.search else DIM)
        self._search_end = said.right                     # the pool's "rows ... of ..." note keeps clear of it (Round RPG14b)
        if e.search:
            w = gui.button_width("Clear", "tiny")
            self.button(gui, pygame.Rect(left.right - w, y2, w, small.get_height()), "Clear", "clear_search", fkey="tiny")
        y2 += small.get_height() + 6
        pool_area = pygame.Rect(left.x, y2, left.w, left.bottom - y2)
        self.draw_pool(gui, pool_area)
        self.draw_deck_list(gui, right)
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * small.get_height(), int(rect.w * 0.6))

    def draw_pool(self, gui, area):
        scr, fs, e = gui.screen, self.fs, self.editor
        rows = e.collection_rows()
        self.prefetch(gui, [r.name for r in rows[:120]])
        per_row = max(3, area.w // int(118 * fs))
        w = max(30, (area.w - (per_row - 1) * int(6 * fs)) // per_row)
        h = int(w / CARD_ASPECT)
        gap = int(6 * fs)
        if h > area.h:                                       # Round RPG14b: a row taller than the room (big letters push the
            h = max(1, area.h)                                # filters down) is shrunk to it, never drawn over the buttons
            w = int(h * CARD_ASPECT)
        fit_rows = max(1, (area.h + gap) // (h + gap))
        total_rows = -(-len(rows) // per_row) if rows else 0
        top = max(0, min(self.scroll.get("deckpool", 0), max(0, total_rows - fit_rows)))
        self.scroll["deckpool"] = top
        tiny = gui.font("tiny", True)
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        shade.fill(gfx.BACKDROP + (160,))
        for k, row in enumerate(rows[top * per_row:(top + fit_rows) * per_row]):
            r = pygame.Rect(area.x + (k % per_row) * (w + gap), area.y + (k // per_row) * (h + gap), w, h)
            scr.blit(card_picture(gui, row.name, row.card, w, h), r)
            owned = "-" if row.owned is None else str(row.owned)
            if e.why_not(row.name):
                scr.blit(shade, r)
            label = f"{row.used}/{owned}"
            tag = pygame.Rect(r.right - tiny.size(label)[0] - 12, r.bottom - tiny.get_height() - 8, tiny.size(label)[0] + 8,
                              tiny.get_height() + 4)
            round_rect(scr, tag, gfx.BTN_GOLD_BOTTOM if row.used else gfx.PANEL_DEEP, 4, 1, gfx.BTN_GOLD_EDGE if row.used
                       else gfx.PANEL_EDGE)
            draw_text(scr, label, tag.centerx, tag.centery, tiny, gfx.BTN_GOLD_FG if row.used else TEXT, "center")
            self.rows.append((r, "pool", row.name))
            if r.collidepoint(gui.mouse):
                self.hover = (row.name, row.card)
        if not rows:
            draw_text(scr, "No card of yours matches." if (e.colours or e.group or e.search) else "You own no cards.",
                      area.centerx, area.y + 20, gui.font("small"), DIM, "midtop")
        elif total_rows > fit_rows:
            tiny_f = gui.font("tiny")
            room = area.right - (getattr(self, "_search_end", None) or area.x) - self.gap
            note = f"rows {top + 1}-{min(total_rows, top + fit_rows)} of {total_rows}  (wheel to scroll)"
            if tiny_f.size(note)[0] > room:                  # Round RPG14b: DawnLike's wide letters - the short form, or none
                note = f"rows {top + 1}-{min(total_rows, top + fit_rows)} of {total_rows}"
            if tiny_f.size(note)[0] <= room:
                draw_text(scr, note, area.right, area.y - 2, tiny_f, DIM, "bottomright")

    def draw_deck_list(self, gui, area):
        """The deck on the right, by type; it scrolls (the wheel over it) when it is longer than the panel. The rules' problems,
        or "ready to duel", stay at the bottom."""
        scr, e = gui.screen, self.editor
        small, bold = gui.font("small"), gui.font("small", True)
        self.decklist_rect = pygame.Rect(area)
        round_rect(scr, area, gfx.PANEL_DEEP, 8, 1, gfx.PANEL_EDGE)
        inner = area.inflate(-int(16 * self.fs), -int(12 * self.fs))
        lh = small.get_height()
        problems = e.problems()
        foot = []
        for p in problems[:3]:
            foot += [(ln, ORANGE) for ln in wrap_text(p.text, small, inner.w)[:3]]
        if not problems:                                     # Round RPG14b: wrapped like the problems (wide letters)
            foot = [(ln, GREEN) for ln in wrap_text(f"{e.total()} cards - ready to duel.", small, inner.w)[:2]]
        foot_top = inner.bottom - len(foot) * lh
        for i, (ln, colour) in enumerate(foot):
            draw_text(scr, ln, inner.x, foot_top + i * lh, small, colour)
        lines = []
        for g, items in e.deck_rows():
            lines.append(("head", f"{g} ({sum(k for _n, k, _c in items)})", None))
            lines += [("card", name, (k, card)) for name, k, card in items]
        if not lines:
            draw_text(scr, "Your deck is empty: click cards on the left.", inner.x, inner.y, small, DIM)
            return
        tiny = gui.font("tiny")
        room = max(lh, foot_top - lh // 2 - inner.y)
        fit = max(1, room // lh)
        if len(lines) > fit:                                 # the "lines 1-14 of 45" note goes inside the panel, above the status
            fit = max(1, (room - tiny.get_height()) // lh)
        top = max(0, min(self.scroll.get("decklist", 0), max(0, len(lines) - fit)))
        self.scroll["decklist"] = top
        y = inner.y
        for kind, text, data in lines[top:top + fit]:
            if kind == "head":
                draw_text(scr, clip_text(text, bold, inner.w), inner.x, y, bold, GOLD)
            else:
                k, card = data
                r = pygame.Rect(inner.x, y, inner.w, lh)
                hot = r.collidepoint(gui.mouse)
                draw_text(scr, clip_text(f"{k}  {text}", small, inner.w), inner.x, y, small, GOLD if hot else TEXT)
                self.rows.append((r, "deckline", text))
                if hot:
                    self.hover = (text, card)
            y += lh
        if len(lines) > fit:
            note = clip_text(f"lines {top + 1}-{min(len(lines), top + fit)} of {len(lines)}  (wheel)", tiny, inner.w)
            draw_text(scr, note, inner.right, foot_top - lh // 4, tiny, DIM, "bottomright")

    # the small windows over a stage
    def draw_sub(self, gui):
        L, scr = gui.L, gui.screen
        layer = pygame.Surface(scr.get_size(), pygame.SRCALPHA)
        layer.fill(gfx.BACKDROP + (150,))
        scr.blit(layer, (0, 0))
        self.buttons = []
        fs = self.fs
        tf, body = gui.font("title", True), gui.font("body")
        if self.sub == "result":                      # (Round RPG13: a fight has its own screen - draw_encounter)
            title, lines, colour = self.result or ("", [], GOLD)
            buttons = [("OK", "ok", True, True)]
        elif self.sub == "pause":
            title, lines, colour = "Menu", [f"{self.game.save.name}  ·  {self.game.save.gold} gold  ·  slot {self.slot}. "
                                            "Your game is saved."], GOLD
            buttons = [("Save and leave", "leave", True, False), ("Your deck", "deck", True, False),
                       ("Back to the map", "resume", True, True)]
        else:
            title, lines, _yes, _a = self.confirm or ("", [], "Yes", None)
            colour = ORANGE
            buttons = [("Keep it", "no", True, False), (self.confirm[2] if self.confirm else "Yes", "yes", True, True)]
        width = min(L.W - 40, int(640 * fs))
        btn_w = sum(gui.button_width(b[0]) for b in buttons) + self.gap * (len(buttons) - 1)
        width = max(width, min(L.W - 40, btn_w + 2 * self.pad))
        wrapped = []
        for ln in lines:
            wrapped += wrap_text(ln, body, width - 2 * self.pad)
        h = self.pad * 2 + tf.get_height() + self.gap + len(wrapped) * body.get_height() + self.gap + self.bh + self.pad
        box = pygame.Rect(0, 0, width, min(L.H - 40, h))
        box.center = (L.W // 2, L.H // 2)
        gfx.shadowed(scr, box, 16, 6, 120)
        round_rect(scr, box, gfx.DIALOG_BG, 16, 2, gfx.DIALOG_EDGE)
        self.rect = box
        y = box.y + self.pad
        draw_text(scr, clip_text(title, tf, box.w - 2 * self.pad), box.x + self.pad, y, tf, colour)
        y += tf.get_height() + self.gap
        for ln in wrapped:
            if y + body.get_height() > box.bottom - self.pad - self.bh - self.gap:
                break
            draw_text(scr, ln, box.x + self.pad, y, body, TEXT)
            y += body.get_height()
        x = box.right - self.pad
        by = box.bottom - self.pad - self.bh
        for label, name, on, primary in reversed(buttons):
            w = gui.button_width(label)
            x -= w
            self.button(gui, pygame.Rect(x, by, w, self.bh), label, name, on, primary)
            x -= self.gap
