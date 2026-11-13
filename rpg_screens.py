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
import os
import time

import pygame

import art_loader
import crashlog
import forge_dialogs as dlg
import gfx
import pack_art
import panning
import rpg_encounter
import rpg_map_view
import rpg_results
import rpg_ui
import scrollbar
from gfx import DIM, GOLD, GREEN, ORANGE, RED, TEXT, clip_text, wrap_text
from rpg import adventure as adv
from rpg import cardpool
from rpg import duel as rduel
from rpg import clock, encounters, events, explore, hints, newgame, products, progress, talk
from rpg import tapwalk
from rpg import shape as rshape
from rpg.tiers import TIER_NAMES
from rpg import worldsound
from rpg import save as rsave
from rpg import shop as rshop
from rpg import gear as rgear
from rpg import extras as rextras
from rpg import spar as rspar

CARD_ASPECT = 488 / 680
TITLE = "Adventure"
INTRO = ("Walk the land, duel whatever blocks your way, and build your deck from the cards you win and buy. Every duel is a "
         "60-card game at 20 life against the AI, on the same table as your other games.")
PICK_INTRO = ("Three Jumpstart half-decks make your first 60 cards, in at most two colours. Click the one you want; Show others "
              "offers three more.")
UPGRADE_TAG = "  up"                     # Round STARTER1: after an upgraded card's line in the starting deck (Karl to edit)
UPGRADE_NOTE = "{n} weak card{s} upgraded (green). "
UPGRADE_HOVER = "{name} replaced: {outs}"
MAP_KEYS = ("Tap: walk or act", "Arrows or WASD: walk", "Enter: talk", "H: hint", "Esc: menu")   # RPG14: talk; RPG15b: the hint;
# Round RPG-TAP (Karl 27 Oct, a pen only on his Surface): a tap walks there, or up to a person, door or foe and acts
PEN_REPEAT = 0.3                 # Round RPG-TAP: seconds - a second left press this soon after a map tap, at the same place, is the
PEN_SLOP = 12                    # same pen tap told twice (px x text scale) and is ignored - my estimates
WALK_KEYS = (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN, pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s)
PERK_ITEM = "Choose a perk"         # Round PROG3: the Menu's item while a perk waits, and the perk window's title
PERK_LATER = "Later"
PERK_HUD = "Perk ready: Menu"
HUD_ICON = 16                       # Round PROG4: the worn gear's icons on the corner panel, in its UI pixels (the 16 px art as drawn)
PAUSE_ITEMS = (("resume", "Back to the map", True), ("hint", "Hint", True), ("deck", "Deck", True), ("collection", "Collection", True),
               ("book", "Card Book", True), ("beasts", "Bestiary", True),           # Round PROG1
               ("gear", "Gear", True),                                              # Round PROG4
               ("save", "Save", True), ("light", "Light", True), ("settings", "Settings", True),       # Round RPG14 (the spec's);
               ("leave", "Main menu", True))           # Round RPG15: "light" - its label says "Light: on" or "Light: off"
# Round RPG15: the music each place asks for when its map names none, and the boss's (audio's "loop:<cue>" mode loops one track)
BOSS_MUSIC = "music.rpg_boss"
NIGHT_MUSIC_PHASES = ("dusk", "night")  # Round RPG-NM: the clock phases a map's night_music plays in (rpg.clock.PHASES);
                                        # RPG18b (Karl 25 Oct): from dusk, 18:00-05:00 (was night only, 20:00)
VICTORY_STING, DEFEAT_STING = "music.rpg_victory", "music.rpg_defeat"
WELCOME = ("Your 60 cards are your deck. This town's shop sells single cards, packs and basic lands, and buys cards back. "
           "Leave town to explore; walking into a foe asks before a duel starts.")
TABS = (("singles", "Singles"), ("packs", "Packs"), ("lands", "Basic lands"), ("gear", "Gear"), ("trade", "Trade up"),
        ("sell", "Sell"))   # PROG4: Gear; PROG5: Trade up
SPAR_TITLE = "Sparring"                  # Round SPAR1: the sparring yard's windows (rpg.spar)
GAUNTLET_TITLE = "The gauntlet"            # Round PROG5: the result window's title for the gauntlet's entry and prize
# Round PROG4: the Gear screen's and the shop's Gear tab's words (Karl to edit; rpg/gear.py holds the rest)
GEAR_SUB = "What you wear counts in every duel. Tap Wear to put a piece on, Take off to take it off."
GEAR_WORN_HEAD = "Worn"
GEAR_OWNED_HEAD = "Your gear"
GEAR_NONE = "No gear yet. Town shops sell some; chests, bosses, quests and rare foes give the rest."
GEAR_EMPTY_SLOT = "empty"
GEAR_SHOP_TITLE = "Gear  -  common pieces; better ones are found"
GEAR_SHOP_NONE = "You have every piece this shop sells."
GEAR_BUY = "Buy"
GEAR_WEAR, GEAR_OFF, GEAR_TO_PACK, GEAR_WORN_TAG = "Wear", "Take off", "In pack", "Worn"
# Round PACK1: the packs tab's words for the new kinds (Karl to edit)
PREMIUM_NAME = "{set} - premium pack"
PREMIUM_SUB = "{code}   ·   more uncommons, an extra rare   ·   {price} gold"
BUNDLE_NAME = "{set} - bundle of 3"
BUNDLE_SUB = "{code}   ·   {packs} packs at once   ·   {price} gold"
THEME_SUB = "theme pack   ·   {cards} cards   ·   {colours}   ·   {price} gold"
PAGE_WORDS = "Pack {page} of {pages}."
# Round PACK2: the shelf's words (Karl to edit) - on the wrapper, and in the details at the right
WRAP_PLAIN = "Booster - {cards} cards"
WRAP_PREMIUM = "Premium - {cards} cards, {rares} rares"
WRAP_BUNDLE = "{packs} packs"
WRAP_THEME = "Jumpstart - {cards} cards"
HOLDS_PACK = "{cards} cards: {template}."
HOLDS_BUNDLE = "{packs} packs of {cards} cards, each {template}."
HOLDS_THEME = "{cards} cards, ready to play: {colours}."
SHELF_HINT = "Tap a pack to see what it holds."
PACK_BUY = "Buy and open"
SINGLES_BUY = "Buy"                                      # task bug-1567699607696441414: the singles gallery
SINGLES_SOLD_OUT = "sold out"
SOLD_OUT_VEIL = 150                                      # the alpha of the shade over a sold-out card
TEMPLATE_WORDS = (("consisting of ", ""), ("RareMythic", "rare or mythic"), ("BasicLand", "basic land"), ("Common", "common"),
                  ("Uncommon", "uncommon"), ("Rare", "rare"), ("Mythic", "mythic"), ("Special", "special"))
SEP = "   ·   "                    # between the parts of a shop line's second half
GROUP_BUTTONS = ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land")
RARITY = {"M": 0, "S": 0, "R": 1, "U": 2, "C": 3}
MAX_DT = 0.1                     # seconds: a frame that took longer walks this far at most (a stall never throws you into a wall)
BASIC_STEPS = [1, 5, 10]          # a list: a 3-number tuple reads as a colour to test_ad1's guard
WHEEL_ROWS = 3                   # rows a wheel notch scrolls a list
# Task bug-sell-scroll-2 (Karl, 3 Nov 2026, Surface Pro 11): every Adventure list gets a scroll bar a finger can grab
BAR_WIDTH = 24                   # px at 100% text (the deck screen's bar is 12)
BAR_MIN_SHARE = 0.035            # ... and never narrower than this share of the window's height (67 px at 1920 high: about 6 mm on
                                 # the Surface's 267 dpi screen - my estimate of a fingertip's room)
NAV_KEYS = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_PAGEUP: "pageup", pygame.K_PAGEDOWN: "pagedown",
            pygame.K_HOME: "home", pygame.K_END: "end", pygame.K_KP8: "up", pygame.K_KP2: "down"}
DAWN_COLOUR = {tuple(GREEN): gfx.DB_GREEN, tuple(RED): gfx.DB_RED, tuple(ORANGE): gfx.DB_ORANGE, tuple(GOLD): gfx.DB_GOLD}
# Round RPG14b: the shop, the pack, the deck editor and the Collection are drawn in DawnLike's window and SDS font (DawnGui); while
# one of them draws, _DAWN["k"] is the frame's pixel size and the words' colours are DawnBringer's (DAWN_TEXT)
DAWN_STAGES = ("town", "pack", "deck", "collection", "book", "beasts",    # Round PROG1: the Card Book and the bestiary
               "gear")                                                    # Round PROG4: the Gear screen
DAWN_TEXT = dict(DAWN_COLOUR)
DAWN_TEXT.update({tuple(TEXT): gfx.DB_TEXT, tuple(DIM): gfx.DB_DIM, tuple(gfx.BTN_GOLD_FG): gfx.DB_GOLD})
_DAWN = {"k": 0}
# Round PROG1: the Card Book's and the bestiary's words (Karl's to edit)
BOOK_TITLE = "Card Book"
BOOK_EMPTY = "No cards yet."
BOOK_NONE = "No card you have found matches."
BEASTS_TITLE = "Bestiary"
BEAST_UNMET = "Not met yet."
BEAST_NO_CARDS = "none yet"
TIER_WORDS = dict(TIER_NAMES)                 # the tier filter's buttons, 0 Basic .. 5 Power (rpg.tiers)


def rpg_enemy_kinds():
    """Round PROG1: enemy_kinds.json's kinds, in its order."""
    from rpg import enemies
    return enemies.read_kinds()


def beast_picture(battler, side, shadow=False):
    """Round PROG1: a foe kind's battle picture (rpg_encounter.picture, its empty border cut off) fitted in side x side by whole
    pixels where it can be; shadow: drawn as a dark shape (a kind not met yet)."""
    key = ("beast", battler, side, bool(shadow))
    hit = gfx.recall(key)
    if hit is not None:
        return hit
    img = rpg_encounter.picture(battler or "") if battler else None
    if img is None or side <= 0:
        return None
    box = img.get_bounding_rect()
    if box.w == 0 or box.h == 0:
        return None
    img = img.subsurface(box).copy()
    s = max(1, side // max(box.w, box.h))
    w, h = (box.w * s, box.h * s) if s * max(box.w, box.h) <= side else (side * box.w // max(box.w, box.h),
                                                                           side * box.h // max(box.w, box.h))
    img = pygame.transform.scale(img, (max(1, w), max(1, h)))
    if shadow:                                                      # its outline only, in the frame's grey
        img = pygame.mask.from_surface(img).to_surface(setcolor=gfx.DB_DIM, unsetcolor=gfx.CLEAR)
    return gfx.remember(key, img)


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


_GEAR_ICONS = {}
GEAR_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "gear")


def gear_icon(icon, size):
    """Round PROG4: a gear icon (assets/gear/<icon>.png, 16 x 16, tools/rpg_gear_icons.py) scaled to `size` px without smoothing
    (pixel art); None when the file is missing (the row then shows the words only)."""
    size = max(8, int(size))
    key = (icon, size)
    if key not in _GEAR_ICONS:
        try:
            img = pygame.image.load(os.path.join(GEAR_ICON_DIR, f"{icon}.png"))
            _GEAR_ICONS[key] = pygame.transform.scale(img, (size, size))
        except (OSError, pygame.error, FileNotFoundError):
            _GEAR_ICONS[key] = None
    return _GEAR_ICONS[key]


def template_words(template):
    """Round PACK2: Forge's booster template ("consisting of 10 Common, 3 Uncommon, 1 RareMythic, and 1 BasicLand") in words."""
    t = template or ""
    for a, b in TEMPLATE_WORDS:
        t = t.replace(a, b)
    return t.strip()


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


GALLERY_TWO_ROWS = 56             # Round SHOPMSG2: below this card height (x text scale) a gallery gives up its second row
GALLERY_MIN_H = 40                # ... and below this (x text scale) no row is drawn at all (the gallery scrolls instead)


def gallery_shape(height, width, label_h, label_w, gap, fs, aspect, max_h, count):
    """The Singles / Packs gallery's grid in a `height` x `width` area (Round SHOPMSG2): (picture w, picture h, column pitch,
    columns, row height, rows, rows that fit). Two rows when each picture can still be GALLERY_TWO_ROWS x fs tall, else one row as
    tall as the area allows, never under GALLERY_MIN_H x fs - and only rows that fit whole are counted (0 when not even one does;
    the old shelves always counted one, so its bottom ran under the message strip). A column is as wide as its price words even
    when the picture is narrower (the picture is centred in it), so the words stay whole at big text without making the picture
    taller than the room."""
    two = (height - 2 * (label_h + gap)) // 2 - 1
    one = height - (label_h + gap) - 1
    ch = min(two if two >= int(GALLERY_TWO_ROWS * fs) else one, max_h)
    ch = max(int(GALLERY_MIN_H * fs), ch)
    cw = max(1, int(ch * aspect))
    pitch = max(cw, label_w)
    cols = max(1, (width + gap) // (pitch + gap))
    if cols == 1 and pitch > width:                  # a very narrow window: the column shrinks to the area
        pitch = max(8, width)
        if cw > pitch:
            cw = pitch
            ch = int(cw / aspect)
    row_h = ch + label_h + gap
    return cw, ch, pitch, cols, row_h, (count + cols - 1) // cols, max(0, height // row_h)


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
        self._bars = {}                  # task bug-sell-scroll-2: list key -> its scrollbar.ScrollBar (kept: a drag lasts frames)
        self._bars_drawn = {}            # ... the bars drawn this frame: key -> (bar, unit px)
        self._fits = {}                  # ... list key -> (rows shown, rows in all) this frame, for the keys
        self.hover = None                # (name, card) under the mouse - shown big
        self.upgrade_hover = None        # Round STARTER1: (name, [replaced]) of the upgraded card under the mouse
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
        self.pack_page = 0               # Round PACK1: which pack of a bundle the pack screen shows
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
        self.results = None              # Round PROG1: the results screen after a duel (rpg_results.ResultsScreen), else None
        self.book_colours, self.book_tier = set(), None     # Round PROG1: the Card Book's filters
        self.meters = {}                 # Round PROG1: where the Card quality meter (PROG1b; was deck power) shows -> PowerMeter
        self.shape_drawn = None          # Round PROG1b: the deck editor's shape as last drawn (for the tests): see draw_shape
        self.book_big = None             # Round PROG1: (name, card) a tap on the Card Book showed big
        self._places = {}
        self.result = None               # (title, [lines], colour)
        self.confirm = None              # (title, [lines], yes label, action)
        self.field = fset.TextField("Name", "", multiline=False, limit=rsave.NAME_MAX, lines=1, hint="Your name")
        self._prefetched = set()
        self.retry = None                # a duel's result the table couldn't write: tried again (open_slot, write)
        self.tap = None                  # Round RPG-TAP: the rpg.tapwalk.TapWalk a tap on the map started (None: none)
        self.sparring = None             # Round SPAR1: the practice duels under way (rpg.spar.Sparring), else None
        self.spar_box = None             # ... their progress window (rpg_ui.MessageWindow with a bar)
        self.last_spar = None            # ... the last spar's result (rpg.spar.Sparring.result), for the tests
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
        # Round SND1: a load is silent (the clock's time is only noted); the table's own reopening after a duel keeps listening
        AdventureFlow.world_sound_tick(self, gui, reset=outcome is None and error is None and retry is None)
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
            self.open_results(gui, outcome, self.result[1])
        elif self.open_pending_pick(gui, notes):           # Round PROG2: the reward cards still to choose, offered again
            pass
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
                self.open_results(None, out, self.result[1])
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
        if getattr(self, "sparring", None) is not None:       # Round SPAR1: a spar under way ends with the program
            self.sparring.stop()
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
        AdventureFlow.world_sound_tick(self, gui, reset=True)     # Round SND1: a new game is silent
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

    def world_sound_tick(self, gui, reset=False):
        """Round SND1: the howl at dusk and the rooster at dawn (rpg.worldsound has the rule). Every tick, and with reset=True when
        a save is loaded or a new game begins (silent: the time is only noted). The tracker is kept on the table (`gui`), so the
        window the table opens again after a duel goes on with the same one."""
        game = getattr(self, "game", None)
        if game is None:
            return []
        tracker = getattr(gui, "rpg_world_sounds", None)
        if not isinstance(tracker, worldsound.Tracker):
            tracker = worldsound.Tracker()
            try:
                gui.rpg_world_sounds = tracker
            except AttributeError:
                pass
        key = (getattr(self, "folder", None), getattr(self, "slot", None))
        minutes = clock.now(game.save.world)
        if reset:
            tracker.reset(key, minutes)
            return []
        cues = tracker.tick(key, minutes, worldsound.can_hear(self))
        for cue in cues:                         # literals: test_au1 finds played cues by their text
            if cue == "rpg.howl":
                gui.play_cue("rpg.howl")
            elif cue == "rpg.rooster":
                gui.play_cue("rpg.rooster")      # SND1B: ships (alys's public-domain rooster, candidate A)
        return cues

    def walk_tick(self, gui, now, pressed=None):
        """Every tick of the table's loop: walk on the map. pressed: (dx, dy) instead of the keyboard (tests)."""
        AdventureFlow.world_sound_tick(self, gui)            # Round SND1 (the class's: stand-in flows call walk_tick too)
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
        if kind == "chest":                                  # Round PROG2: a treasure chest walked into
            self.open_chest(gui, what)
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
        if hasattr(ex, "talked_to"):
            ex.talked_to(nid)                     # Round RPG18b: a person walking carries on once the talk ends
        return AdventureFlow.talk_with(self, gui, nid)       # (the class's own: tests call talk_to on a stand-in)

    def talk_with(self, gui, nid):
        """Round PROG5: the talk itself (talk_to's, and the gauntlet's warden after a gauntlet duel's results)."""
        ex = self.explorer
        world = self.game.save.world
        try:
            conv = talk.Conversation(nid, world)
        except (talk.TalkError, OSError, ValueError) as e:
            self.say(f"{nid} has nothing to say ({e}).", ORANGE)
            return True
        pages = list(conv.pages)
        if nid == talk.BOUNTY_CLERK:                                 # Round PROG5: the board - posted, paid, listed
            try:
                posted = rextras.post_bounties(self.game.save)
                paid = rextras.collect_bounties(self.game)
                pages += rextras.split_pages(rextras.board_pages(self.game.save, posted, paid))
                if paid:
                    gui.play_cue("card.pickup")
            except (OSError, ValueError) as e:
                crashlog.note(f"Adventure: the bounty board couldn't be read: {type(e).__name__}: {e}")
                pages.append(f"The board can't be read ({e}).")
        if conv.reward is not None:                                  # a quest handed in (its reward may be only a flag)
            given = adv.give_reward(self.game, conv.reward) if conv.reward.get("gold") or conv.reward.get("card") else []
            pages += [" ".join(given)] if given else []
            xp = adv.quest_xp(self.game)                             # Round PROG3: a quest handed in gives XP (said under the
            xp += adv.quest_gear(self.game, getattr(conv, "reward_quest", None))     # map, not in the person's words); PROG4:
            if xp:                                                   # ... and the quest's piece of gear, the same way
                self.say(" ".join(xp), GREEN)
        pages += list(conv.after)                                    # the quest's "done" words, once, after the reward
        self.talk_shop = ex.facing_shop() if ex is not None else None    # Round RPG15b: a shopkeeper's words, then the shop
        if ex is not None:
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
            elif getattr(conv, "gauntlet", None):                        # Round PROG5: the warden's answer
                self.close_talk()
                self.gauntlet_do(gui, conv.gauntlet)
            elif getattr(conv, "spar", False):                           # Round SPAR1: the yard master's "Spar"
                self.close_talk()
                self.spar_start(gui)
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
        g = outcome.get("gauntlet")
        if isinstance(g, dict) and g and not g.get("done"):           # Round PROG5: the warden asks for the next duel
            return ("talk", talk.GAUNTLET_WARDEN)
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

    # ---- Round PROG5: the town gauntlet ----------------------------------------------------------------------------------------
    def gauntlet_do(self, gui, what):
        """The warden's answer: "enter" (pay and fight the first duel), "next" (the next duel), "stop" (the prize for the wins so far)."""
        g = self.game
        if what == "stop":
            lines = rextras.stop_gauntlet(g)
            self.write()
            self.result = (GAUNTLET_TITLE, lines, GOLD)
            self.sub = "result"
            gui.play_cue("card.pickup")
            return
        if what == "enter":
            try:
                rextras.enter_gauntlet(g.save)
            except (rextras.TradeError, OSError, ValueError) as e:
                self.result = (GAUNTLET_TITLE, [str(e)], ORANGE)
                self.sub = "result"
                gui.play_cue("ui.error")
                return
            self.write()
        foe = rextras.next_foe(g.save.world)
        if foe is None:
            return
        self.fight = encounters.GauntletFoe(foe)
        self.encounter = None
        self.sub = "fight"
        gui.play_cue("rpg.encounter")              # rpg_encounter.STING (a literal: test_au1 finds played cues by their text)

    # ---- Round SPAR1: the sparring yard (rpg.spar) ----------------------------------------------------------------------------
    def spar_start(self, gui, n=None):
        """Yardmaster Corin's "Spar": N practice duels of your deck on a background thread (rpg.spar.Sparring), the progress
        window meanwhile (Stop at any time). Your deck must be playable; no gold, cards, XP or time come of it."""
        g = self.game
        wrong = g.deck_problems() if g is not None else None
        why = None
        if g is None or g.save.deck() is None:
            why = "You have no deck to spar with."
        elif wrong:
            why = f"Your deck can't be played yet: {wrong[0].text}"
        else:
            why = rspar.tool_problem()
            why = rspar.NO_TOOL.format(what=why) if why else None
        if why is None:
            try:
                save = g.save
                seed = save.stable_rng("spar", len(save.sparring or ()), rspar.fingerprint(save.deck())).randrange(1 << 40)
                self.sparring = rspar.Sparring(save, n=n, seed=seed).start()
            except (rspar.SparError, OSError, ValueError) as e:
                why = str(e)
        if why is not None:
            self.sparring = None
            self.result = (SPAR_TITLE, [why], ORANGE)
            self.sub = "result"
            gui.play_cue("ui.error")
            return False
        self.spar_box = None
        self.sub = "spar"
        gui.play_cue("ui.click")
        return True

    def spar_lines(self):
        sp = self.sparring
        if sp is None:
            return [], 0.0
        done, total, wins, started = sp.progress()
        if sp.stopped and not sp.finished:
            return [rspar.STOPPING], done / total
        if not started:
            return [rspar.STARTING], 0.0
        return [rspar.PLAYING.format(n=min(total, done + 1), total=total, wins=wins)], done / total

    def spar_tick(self, gui):
        """Each frame while sparring: once the run has ended, its result into the save (written) and the result window."""
        sp = getattr(self, "sparring", None)
        if sp is None or not sp.finished:
            return False
        self.sparring = None
        res = sp.result()
        rec = None
        try:
            rec = rspar.record(self.game.save, res)
        except Exception as e:                               # the result is still said; it is only not kept
            crashlog.note(f"Adventure: a spar's result couldn't be kept: {type(e).__name__}: {e}")
        if rec is not None:
            self.write()
        self.last_spar = res
        self.result = (SPAR_TITLE, rspar.result_lines(res), ORANGE if res.get("error") else GOLD)
        self.message_box = None
        self.sub = "result"
        gui.play_cue("ui.error" if res.get("error") else "card.pickup")
        return True

    def spar_stop(self, gui):
        sp = getattr(self, "sparring", None)
        if sp is not None:
            sp.stop()
            gui.play_cue("ui.click")

    def draw_spar(self, gui):
        """The progress window over the map: what is being played, a bar, and Stop."""
        if self.spar_tick(gui):
            return self.draw_result_over(gui)
        lines, frac = self.spar_lines()
        box = getattr(self, "spar_box", None)
        if box is None:
            box = self.spar_box = rpg_ui.MessageWindow(SPAR_TITLE, lines, gfx.DB_GOLD, ok=rspar.STOP, bar=frac)
        box.lines, box.bar = [str(x) for x in lines], frac
        return list(box.draw(gui.screen))

    def open_pause(self, gui):
        """Esc on the map: the pause window (DawnLike's window and font): Back to the map / Your deck / Save / Settings / Save and
        leave, beside your gold, deck and quests."""
        if self.explorer is not None:
            self.explorer.store()
        self.write()
        g = self.game
        info = g.panel_info()
        lines = [(g.save.name, gfx.DB_TEXT), (f"Gold  {g.save.gold}", gfx.DB_GOLD)]    # Erik, 1 Nov 2026: the gold in yellow
        if info.get("deck"):
            cards = f" ({info['cards']} cards)" if info.get("cards") is not None else ""
            lines.append((f"Deck  {info['deck']}{cards}", gfx.DB_TEXT))
        if info.get("level"):                                   # Round PROG3: your level and the perks you took
            lines.append((info["level"], gfx.DB_GREEN))
            try:
                from rpg import levels
                taken = [p.name for p in levels.perks_of(g.save)]
            except (OSError, ValueError):
                taken = []
            if taken:
                lines.append(("Perks  " + ", ".join(taken), gfx.DB_TEXT))
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
        bounties = rextras.journal_lines(g.save)                  # Round PROG5: the board's jobs under way
        for title, line in bounties:
            lines.append((f"{title} - {line}", gfx.DB_TEXT))
        if not book and not bounties:
            lines.append(("None yet. Talk to people in town.", gfx.DB_DIM))
        self.menu = rpg_ui.ListMenu("Menu", self.pause_items(gui), lines)
        self.sub = "pause"

    def pause_items(self, gui):
        """PAUSE_ITEMS, the Light item saying how it is now (Round RPG15)."""
        items = [(n, (f"Light: {'on' if light_on(gui) else 'off'}" if n == "light" else label), on) for n, label, on in PAUSE_ITEMS]
        if self.perk_pending():                    # Round PROG3: a perk to choose waits here (it never stops the walk)
            items.insert(0, ("perk", PERK_ITEM, True))
        return items

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
                self.say(self.sold_words(args[0], got), GREEN if not s.taken else ORANGE)
            elif what == "pack":
                cards = s.buy_pack(args[0])
                name = next((n for c, n, _p in s.packs() if c == args[0]), args[0])
                self.opened = (name, cards)
                self.pack_page = 0
                self.set_stage("pack")
            elif what in ("premium", "bundle"):              # Round PACK1: a premium pack, a bundle of 3
                cards = s.buy_premium(args[0]) if what == "premium" else s.buy_bundle(args[0])
                name = next((n for c, n, _p in s.packs() if c == args[0]), args[0])
                self.opened = (PREMIUM_NAME.format(set=name) if what == "premium" else BUNDLE_NAME.format(set=name), cards)
                self.pack_page = 0
                self.set_stage("pack")
            elif what == "trade":                            # Round PROG5: 4 copies for 1 card a tier higher
                g = self.game
                ex = rextras.read()
                got, tier, taken = rextras.trade_up(g.save, args[0], g.pool, g.tiers(), g.lists(), g.numbers, s.band, ex)
                s.taken = taken
                self.say(self.traded_words(args[0], ex.trade_copies, got, tier), GREEN if not taken else ORANGE)
            elif what == "gearbuy":                          # Round PROG4: a common piece of gear
                p = rgear.buy(self.game.save, args[0], self.game.save.world.get("region") or 1)
                self.say(rgear.BOUGHT_LINE.format(name=p.name, price=p.price), GREEN)
            elif what == "theme":                            # Round PACK1: a Jumpstart theme pack
                half = next((h for h in s.theme_stock() if h.code == args[0]), None)
                cards = s.buy_theme(args[0])
                self.opened = (products.theme_name(half) if half else args[0], cards)
                self.pack_page = 0
                self.set_stage("pack")
        except (rshop.ShopError, rgear.GearError, rextras.TradeError) as e:
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
        elif head in ("pack", "premium", "bundle", "theme"):    # Round PACK1: every kind of pack on the shelf
            self.shop_do(gui, head, rest)
        elif name in ("pack_prev", "pack_next"):             # Round PACK1: the packs of a bundle, one page each
            self.pack_page = max(0, min(self.pack_pages() - 1, self.pack_page + (1 if name == "pack_next" else -1)))
        elif head == "basic":
            land, _s, k = rest.rpartition(":")
            self.shop_do(gui, "basic", land, k)
        elif head == "sell":
            self.shop_do(gui, "sell", rest)
        elif head == "trade":                                # Round PROG5: Trade up
            self.shop_do(gui, "trade", rest)
        elif name == "pack_done":
            self.opened = None
            self.pack_page = 0
            self.stage = "town"
            self.message = None
        elif name == "collection_done":
            self.enter_map(gui)
        elif name in ("book_done", "beasts_done"):           # Round PROG1: back from the Card Book / the bestiary
            self.book_big = None
            self.enter_map(gui)
        elif name == "gear_done":                            # Round PROG4: back from the Gear screen
            self.enter_map(gui)
        elif head in ("gear_wear", "gear_pack", "gear_off"):     # Round PROG4: wear / take off a piece (one tap, one action)
            self.gear_do(gui, head, rest)
        elif head == "gearbuy":                              # Round PROG4: the shop's Gear tab
            self.shop_do(gui, "gearbuy", rest)
        elif head == "bookc":                                # Round PROG1: the Card Book's colour filter (any of them)
            self.book_colours ^= {rest}
            self.scroll["book"] = 0
        elif head == "bookt":                                # ... and its tier filter (one tier, or every tier)
            t = None if rest == "all" else int(rest)
            self.book_tier = None if self.book_tier == t else t
            self.scroll["book"] = 0
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
        if sub == "perk":                                         # Round PROG3: a perk chosen, or Later
            if name.startswith("perk:"):
                self.take_perk(gui, name.split(":", 1)[1])
            elif name == "perk_later":
                self.sub = None
            return
        if sub == "spar":                                         # Round SPAR1: Stop (the window's one button)
            if name == "ok":
                self.spar_stop(gui)
            return
        if sub == "fight":
            self.encounter_do(gui, self.encounter.choose(name, time.monotonic()) if self.encounter is not None else name)
        elif sub == "result":
            if name == "results_skip":                               # Round PROG1: the first tap finishes the results screen
                if self.results is not None:
                    self.results.skip(time.monotonic())
                return
            if name == "results_wait":                               # Round PROG2: a tap beside the cards to choose: nothing
                return
            if name.startswith("rpick:") and self.results is not None:     # Round PROG2: a card picked in its group
                _h, g, i = name.split(":")
                self.results.pick(int(g), int(i))
                return
            if name == "ok" and self.results is not None and self.results.choosing() and self.game is not None:
                if not self.take_rewards(gui):                       # Round PROG2: the picked cards into the collection
                    return
            self.result = None
            self.message_box = None
            self.results = None
            self.sub = None
            after, self.after_result = self.after_result, None
            if after and after[0] == "talk" and self.stage == "map":  # Round PROG5: a person speaks (the gauntlet's warden)
                self.talk_with(gui, after[1])
            elif after and self.stage == "map":                      # Round RPG14: the innkeeper / a beaten boss speaks
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
            if name == "perk" and self.perk_pending():   # Round PROG3: the perk choice
                self.sub, self.perk_focus = "perk", 0
                return
            if name == "hint":                       # Round RPG15b: the Menu's Hint
                self.show_hint(gui)
            elif name == "deck":
                self.open_deck(gui, "map")
            elif name == "collection":               # Round RPG14: every card you own, by type
                if self.explorer is not None:
                    self.explorer.store()
                self.set_stage("collection")
            elif name in ("book", "beasts"):         # Round PROG1: the Card Book and the bestiary
                if self.explorer is not None:
                    self.explorer.store()
                self.book_big = None
                self.set_stage(name)
            elif name == "gear":                     # Round PROG4: the Gear screen
                if self.explorer is not None:
                    self.explorer.store()
                self.set_stage("gear")
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
        if button == 1 and self.sub is None and self.bar_press(pos):
            return                                           # task bug-sell-scroll-2: a list's scroll bar
        name = self.button_at(pos)
        if name:
            self.press(gui, name)
            return
        if self.sub is not None:
            return
        if self.stage == "deck" and button == 1 and self.shape_drawn:       # Round PROG1b: a tap on Card quality says what it is
            for kind, _ln, r in self.shape_drawn["lines"]:
                if kind in ("quality", "explain") and r.collidepoint(pos):
                    self.say(self.quality_explain(), TEXT)
                    return
        if self.stage == "map" and self.explorer is not None:
            if button == 1 and self.quality_tapped(pos):     # Round PROG1b: a tap on Card quality says what it is
                return
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
            elif kind == "shelf" and button == 1:            # Round PACK2: a tap on a wrapper shows that pack's details
                self.shelf_pick = data
                self.message = None
                gui.play_cue("ui.click")
            elif kind == "single" and button == 1:           # task bug-1567699607696441414: a tap on a card shows its details
                self.singles_pick = data
                gui.play_cue("ui.click")
            elif kind == "bookcard":                         # Round PROG1: a tap on a Card Book line shows it big (again: not)
                self.book_big = None if self.book_big and self.book_big[0] == data[0] else data
            return

    def key(self, gui, ev):
        k = ev.key
        enter = k in (pygame.K_RETURN, pygame.K_KP_ENTER)
        esc = k == pygame.K_ESCAPE
        if self.sub == "fight":                      # Round RPG13: the encounter screen's own keys
            if self.encounter is not None:
                self.encounter_do(gui, self.encounter.key(k, time.monotonic()))
            return
        if self.sub == "perk":                       # Round PROG3: 1-3 (or Up / Down and Enter) choose; Esc waits
            ids = self.perk_offer()
            if pygame.K_1 <= k <= pygame.K_9 and k - pygame.K_1 < len(ids):
                self.take_perk(gui, ids[k - pygame.K_1])
            elif k in (pygame.K_UP, pygame.K_DOWN) and ids:
                self.perk_focus = (getattr(self, "perk_focus", 0) + (1 if k == pygame.K_DOWN else -1)) % len(ids)
            elif enter and ids:
                self.take_perk(gui, ids[getattr(self, "perk_focus", 0) % len(ids)])
            elif esc:                                # later: it waits in the Menu
                self.sub = None
                gui.play_cue("ui.dialog_close")
            return
        if self.sub == "spar":                       # Round SPAR1: only Esc stops it (Enter / Space don't: a stray key mustn't)
            if esc:
                self.spar_stop(gui)
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
        if self.sub == "result" and self.results is not None:              # Round PROG1: tap / Space / Enter finishes, then continues
            arrows = {pygame.K_LEFT: (-1, 0), pygame.K_RIGHT: (1, 0), pygame.K_UP: (0, -1), pygame.K_DOWN: (0, 1)}
            if k in arrows and self.results.choosing() and self.results.done(time.monotonic()):   # Round PROG2: pick by keys
                if self.results.arrow(*arrows[k]):
                    gui.play_cue("ui.click")
                return
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE, pygame.K_ESCAPE):
                if self.results.tap(time.monotonic()) == "continue":
                    self.press_sub(gui, "ok")
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
        if st in ("town", "deck", "collection") and self.nav_key(gui, k):
            return                                   # task bug-sell-scroll-2: the arrow, page, Home and End keys move the list
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
        if st in ("book", "beasts", "gear"):         # Round PROG1; Round PROG4: the Gear screen the same way
            if esc or enter:
                self.press(gui, st + "_done")
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

    # ---- Round SELL1: drag a list with the pen or a finger --------------------------------------------------------------------
    # Karl, 2 Nov 2026 (on his Surface): "Shops in Adventure mode need to let me scroll through all my cards in the SELL menu".
    # The shop's lists, the deck editor's and the Collection scrolled with the mouse wheel only; a pen or a finger has none.
    def note_pan(self, key, area, scrolls, step):
        """A list drawn this frame: where it is, whether it has more than it shows, and how far one row is (px)."""
        if not hasattr(self, "_pan_views"):
            self._pan_views = {}
        self._pan_views[key] = (pygame.Rect(area), bool(scrolls), max(1, int(step)))

    def pan_view(self, gui, pos):
        """The table's PEN1 hook: a press on a list that scrolls waits - a drag moves the list a row per row's height, a press
        that doesn't move is still a click (a Sell button in a row works as before)."""
        for key, (area, scrolls, step) in (getattr(self, "_pan_views", None) or {}).items():
            if scrolls and area.collidepoint(pos):
                return panning.PanView(notch=lambda n, k=key: self._pan_rows(k, n), step=step)
        return None

    def _pan_rows(self, key, n):
        self.scroll[key] = max(0, self.scroll.get(key, 0) - n)      # drag down (n > 0) shows what is above; drawing clamps

    def wheel(self, gui, dy):
        key = {"town": "town:" + self.tab, "deck": "deckpool", "pick": None, "ready": None,
               "collection": "collection", "book": "book", "beasts": "beasts", "gear": "gear"}.get(self.stage)               # Round RPG15c (review R1): the Collection scrolls
        if self.stage == "deck" and getattr(self, "decklist_rect", None) is not None and self.decklist_rect.collidepoint(gui.mouse):
            key = "decklist"
        if key:
            self.scroll[key] = max(0, self.scroll.get(key, 0) - dy * WHEEL_ROWS)

    # ---- task bug-sell-scroll-2: scroll bars and keys on every Adventure list ---------------------------------------------------
    # Karl, 3 Nov 2026 (0.28.53.14, Surface Pro 11): the Sell list showed its first 13 cards with nothing saying there were more,
    # and a finger, the pen and the arrow keys didn't move it. Each list now has a bar (grab it, or tap its groove for a page),
    # always says "1-13 of 87", and Up/Down (a row), PageUp/PageDown (a page), Home/End move it.
    def scroll_bar(self, gui, key, view, count, fit, top, unit):
        """A list `key` shows rows top..top+fit-1 of `count` in `view` (rows `unit` px apart). When it has more than it shows,
        draws its bar at the right of `view` and returns the room left for the rows; otherwise returns `view` unchanged."""
        self._fits[key] = (fit, count)
        if count <= fit:
            return pygame.Rect(view)
        bar = self._bars.get(key)
        if bar is None:
            bar = self._bars[key] = scrollbar.ScrollBar(base=BAR_WIDTH)
        bar.min_px = int(gui.screen.get_height() * BAR_MIN_SHARE)
        bar.layout(pygame.Rect(view.x, view.y, view.w, fit * unit), count * unit, top * unit, self.fs)
        bar.draw(gui.screen, gui.mouse)
        self._bars_drawn[key] = (bar, unit)
        return pygame.Rect(view.x, view.y, max(1, view.w - bar.track.w - self.gap), view.h)

    @property
    def bar(self):
        """forge_table.scroll_drag_owner's hook (Round UI8): the bar whose handle is held, or None."""
        for b, _unit in self._bars_drawn.values():
            if b.dragging:
                return b
        return next((b for b in self._bars.values() if b.dragging), None)

    def bar_press(self, pos):
        """A left press: True when it was on a list's bar (a grab of the handle, or a page from the groove)."""
        for key, (b, unit) in self._bars_drawn.items():
            new = b.press(pos, self.scroll.get(key, 0) * unit)
            if new is not None:
                if not b.dragging:
                    self.scroll[key] = int(round(new / unit))
                return True
        return False

    def drag_motion(self, gui, pos):
        for key, (b, unit) in self._bars_drawn.items():
            new = b.drag(pos)
            if new is not None:
                self.scroll[key] = int(round(new / unit))
                return

    def drag_release(self, gui, pos):
        for b in self._bars.values():
            b.release()

    def nav_list(self, gui):
        """The list the keys move: the one under the mouse when two are drawn (the deck editor), else the stage's own."""
        if self.stage == "town":
            return "town:" + self.tab
        if self.stage == "collection":
            return "collection"
        if self.stage == "deck":
            dl = getattr(self, "decklist_rect", None)
            return "decklist" if dl is not None and dl.collidepoint(gui.mouse) else "deckpool"
        return None

    def nav_key(self, gui, k):
        """Up/Down/PageUp/PageDown/Home/End on a list: True when the key was one of those (and the list was moved)."""
        what = NAV_KEYS.get(k)
        key = self.nav_list(gui) if what else None
        if key is None:
            return False
        fit, count = self._fits.get(key, (1, 0))
        top, page = self.scroll.get(key, 0), max(1, fit - 1)
        top = {"up": top - 1, "down": top + 1, "pageup": top - page, "pagedown": top + page, "home": 0,
               "end": count}[what]
        self.scroll[key] = max(0, min(top, max(0, count - fit)))
        return True

    # ---- drawing --------------------------------------------------------------------------------------------------------------
    def draw(self, gui):
        L = gui.L
        self.rows = []
        self.hover = None
        self.big_fixed = None                                 # Round STARTER1C: set by draw_ready only
        self.buttons = []
        self._pan_views = {}                                  # Round SELL1: the lists a pen or a finger can drag this frame
        self._bars_drawn, self._fits = {}, {}                 # task bug-sell-scroll-2: this frame's scroll bars and list sizes
        self._title_end = self._search_end = None             # Round RPG14b: set again by this frame's list titles
        fs = max(1.0, L.fs)
        self.fs = fs
        self.pad = int(16 * fs)
        self.bh = int(max(36, 42 * fs))
        self.gap = int(10 * fs)
        if self.sub == "fight":                      # Round RPG13: the encounter screen fills the window
            self.draw_encounter(gui)
            return
        if self.sub == "perk" and self.stage == "map" and self.explorer is not None:
            self.draw_map(gui)
            self.buttons = []
            self.draw_sub(gui)
            return
        if self.sub in ("talk", "pause", "result", "spar") and self.stage == "map" and self.explorer is not None:
            self.draw_map(gui)                       # Round RPG14: the map under the dialogue box / the pause window /
            self.buttons = []                        # the result window, all three in DawnLike's window and font
            if self.sub == "talk" and self.talk_box is not None:
                self.buttons = list(self.talk_box.draw(gui.screen, time.monotonic()))
            elif self.sub in ("pause", "result", "spar"):
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
                    self.buttons = self.draw_result_over(gui)
                elif self.sub == "spar":                      # Round SPAR1: the practice duels' progress
                    self.buttons = self.draw_spar(gui)
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
            self.buttons = self.draw_result_over(gui)
            self.rect = scr.get_rect()
        elif self.sub is not None:
            self.draw_sub(gui)

    def power_info(self, where, cards):
        """Round PROG1: the meter's line for `where` ("map", "deck"): {"power": "Card quality 1.4  +0.1", "power_up"/
        "power_down": True while a change shows}. {} when it can't be worked out (the meter must never stop a screen).
        Round PROG1b: it was "Deck power"; it is Card quality now (rpg.progress.card_quality, the same formula)."""
        try:
            n = progress.read()
            value = progress.card_quality(cards, self.game.pool, self.game.tiers())
            m = self.meters.get(where)
            if m is None:
                m = self.meters[where] = progress.PowerMeter(n.change_seconds, n.decimals)
            text, change = m.see(value, time.monotonic())
        except Exception as e:
            if not getattr(self, "_meter_noted", False):
                self._meter_noted = True
                crashlog.note(f"Adventure: the Card quality meter: {type(e).__name__}: {e}")
            return {}
        out = {"power": text + (f"  {change}" if change else "")}
        if change:
            out["power_up" if change.startswith("+") else "power_down"] = True
        return out

    def quality_tapped(self, pos):
        """Round PROG1b: a tap on the corner window's Card quality line says, under the map, what Card quality is (it never walks)."""
        for text, r in self.hud_words or []:
            if text.startswith(progress.QUALITY_NAME) and r.collidepoint(pos):
                self.say(self.quality_explain(), TEXT)
                return True
        return False

    @staticmethod
    def quality_explain():
        """The one line saying what Card quality is (progression.json card_quality.explain; Karl to edit)."""
        try:
            return progress.read().explain
        except Exception:
            return progress.QUALITY_EXPLAIN

    def place_name(self, map_id, town_id=None):
        """Round PROG1: a place's name for the Card Book ("Lantern Hollow", "The Warrens") from a map or town id."""
        key = (map_id, town_id)
        if key not in self._places:
            name = None
            if town_id:
                try:
                    name = self.game.world.town(town_id).name
                except Exception:
                    name = None
            if name is None and map_id:
                try:
                    name = self.game.world.town(map_id).name
                except Exception:
                    try:
                        name = explore.load_area(map_id).title
                    except Exception:
                        name = None
            self._places[key] = name
        return self._places[key]

    # ---- Round PROG3: perks ----
    PERK_TITLE = PERK_ITEM
    PERK_LINE = "Level {level}: pick one. It lasts for the rest of your adventure."

    def perk_pending(self):
        """A perk choice is waiting (rpg.levels) and the map or a town is showing."""
        if self.game is None or self.stage not in ("map", "town"):
            return False
        try:
            from rpg import levels
            return bool(levels.offers(self.game.save))
        except Exception:
            return False

    def perk_offer(self):
        try:
            from rpg import levels
            grp = levels.offers(self.game.save)
            return [x for x in grp[0] if x in levels.read().perks] if grp else []
        except Exception:
            return []

    def take_perk(self, gui, perk_id):
        try:
            perk = adv.choose_perk(self.game, perk_id)
        except (ValueError, OSError) as e:
            crashlog.note(f"Adventure: the perk couldn't be taken: {type(e).__name__}: {e}")
            self.say(f"Your perk couldn't be saved yet: {e}", RED)
            gui.play_cue("ui.error")
            return
        gui.play_cue("ui.menu_select")
        self.sub = None
        self.say(f"{perk.name}: {perk.text}", GREEN)

    def draw_perk(self, gui):
        """Round PROG3: "Choose a perk" - one full-width button per perk (its name), its words under it; a tap on one takes it."""
        from rpg import levels
        L, scr = gui.L, gui.screen
        lv = levels.read()
        ids = self.perk_offer()
        tf, body, small = gui.font("title", True), gui.font("body"), gui.font("small")
        width = min(L.W - 40, max(int(640 * self.fs), max([gui.button_width(lv.perks[i].name) for i in ids] or [0]) + 2 * self.pad))
        inner = width - 2 * self.pad
        level_now = levels.level(self.game.save, lv)
        head = wrap_text(self.PERK_LINE.format(level=level_now), body, inner)
        texts = [wrap_text(lv.perks[i].text, small, inner) for i in ids]
        h = (2 * self.pad + tf.get_height() + self.gap + len(head) * body.get_height() + self.gap
             + sum(self.bh + len(t) * small.get_height() + self.gap for t in texts) + self.bh)
        box = pygame.Rect(0, 0, width, min(L.H - 20, h))
        box.center = (L.W // 2, L.H // 2)
        gfx.shadowed(scr, box, 16, 6, 120)
        round_rect(scr, box, gfx.DIALOG_BG, 16, 2, gfx.DIALOG_EDGE)
        self.rect = box
        y = box.y + self.pad
        draw_text(scr, clip_text(self.PERK_TITLE, tf, inner), box.x + self.pad, y, tf, GOLD)
        y += tf.get_height() + self.gap
        for ln in head:
            draw_text(scr, ln, box.x + self.pad, y, body, TEXT)
            y += body.get_height()
        y += self.gap
        self.perk_rects = []
        focus = getattr(self, "perk_focus", 0)
        for n, (pid, t) in enumerate(zip(ids, texts)):
            r = pygame.Rect(box.x + self.pad, y, inner, self.bh)
            self.button(gui, r, f"{n + 1}. {lv.perks[pid].name}", f"perk:{pid}", True, n == focus)
            self.perk_rects.append((r, pid))
            y += self.bh
            for ln in t:
                draw_text(scr, ln, box.x + self.pad, y, small, DIM)
                y += small.get_height()
            y += self.gap
        lw = gui.button_width(PERK_LATER)
        self.button(gui, pygame.Rect(box.right - self.pad - lw, y, lw, self.bh), PERK_LATER, "perk_later", True, False)

    def open_results(self, gui, outcome, lines):
        """Round PROG1: the results screen for a settled duel (won, lost, drawn or conceded) - self.result keeps the same words."""
        if not outcome or outcome.get("result") not in progress.BANNERS:
            self.results = None
            return
        try:
            from rpg import rewards
            g = self.game
            colours = rewards.deck_colours(g.save.deck(), g.pool)       # Round PROG2: dim what the deck can't cast; NEW! as before
            info = progress.results(outcome, lines, g.save.gold, g.tiers(), is_new=lambda n: progress.is_new(g.save, n),
                                    can_cast=lambda n: rewards.castable(g.pool.get(n), colours))
            anim = True if gui is None else bool(getattr(gui, "animations", True))
            self.results = rpg_results.ResultsScreen(info, time.monotonic(), pool=self.game.pool, animations=anim,
                                                     picture=lambda n, c, w, h, g=gui: card_picture(g, n, c, w, h))
        except Exception as e:                       # the screen must never stop the Adventure: the old window instead
            crashlog.note(f"Adventure: the results screen couldn't open: {type(e).__name__}: {e}")
            self.results = None

    def chest_list(self):
        """Round PROG2: [(trigger, open now, daily)] of the map's treasure chests, for the view."""
        try:
            table = progress.read().chests
        except (OSError, ValueError):
            table = {}
        return [(t, o, bool((table.get(t.chest) or {}).get("daily"))) for t, o in self.explorer.chests()]

    def open_chest(self, gui, trigger):
        """Round PROG2: a chest walked into (or tapped): its contents into the save, said in the result window, and written."""
        from rpg import chests
        g = self.game
        self.explorer.store()
        try:
            n = progress.read()
            entry = n.chests.get(trigger.chest)
            lines, _cards = chests.open_chest(g.save, trigger.chest, entry, g.pool, g.tiers(), g.lists(), g.numbers,
                                              g.world.band(g.save.world.get("region") or 1), refill_hour=n.refill_hour)
        except Exception as e:                               # a broken table or pack never stops the walk: said, never raised
            crashlog.note(f"Adventure: chest {trigger.chest!r} couldn't be opened: {type(e).__name__}: {e}")
            self.say(f"The chest won't open: {e}", RED)
            return
        if not lines:
            return
        self.write()
        self.result = (chests.TITLE, lines, GOLD)
        self.message_box = None
        self.sub = "result"
        gui.play_cue("card.pickup")

    def take_rewards(self, gui):
        """Round PROG2: Continue on the choosing screen - the picked cards go in and the save is written. False (the screen stays,
        with the reason under it) when they can't be."""
        picks = self.results.picked()
        try:
            lines = adv.choose_rewards(self.game, picks)
        except (ValueError, OSError) as e:
            crashlog.note(f"Adventure: the reward cards couldn't be taken: {type(e).__name__}: {e}")
            self.say(f"Your reward cards couldn't be saved yet: {e}", RED)
            gui.play_cue("ui.error")
            return False
        if self.result and lines:
            self.result = (self.result[0], [ln for ln in self.result[1] if not ln.startswith("Choose your ")] + lines,
                           self.result[2])
        gui.play_cue("card.pickup")
        return True

    def open_pending_pick(self, gui, notes=()):
        """Round PROG2: a game closed on the choosing screen opens on it again, with the same cards (the load's notes first)."""
        out = adv.pending_pick_outcome(self.game)
        if out is None:
            return False
        self.result = ("Victory", list(notes or ()) + adv.outcome_lines(out, self.game.world), GREEN)
        self.sub = "result"
        self.open_results(gui, out, self.result[1])
        return True

    def draw_result_over(self, gui):
        """The result over the map or a town: the results screen after a duel (Round PROG1), else the result window."""
        if self.results is not None:
            if self.results.picture is not None and getattr(self.results, "_gui", None) is not gui:
                self.results._gui = gui
                self.results.picture = lambda n, c, w, h, g=gui: card_picture(g, n, c, w, h)
            self.prefetch(gui, [c["name"] for c in self.results.info.get("cards") or ()] +
                          [c["name"] for grp in self.results.offers for c in grp])
            return list(self.results.draw(gui.screen, gui, time.monotonic()))
        return list(self.result_window().draw(gui.screen))

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

    def header(self, gui, rect, title, sub=None, gold=None):
        """The title, then (bug report from Erik, 1 Nov 2026: "Couldn't find my total gold easy ... Maybe make yellow also?") your
        gold in yellow, bold, on a line of its own when `gold` is given, then `sub` in grey. self.gold_rect: where the gold was drawn."""
        scr, pad = gui.screen, self.pad
        tf, small = gui.font("title", True), gui.font("small")
        draw_text(scr, clip_text(title, tf, rect.w - 2 * pad), rect.x + pad, rect.y + pad, tf, GOLD)
        y = rect.y + pad + tf.get_height() + int(4 * self.fs)
        self.gold_rect = None
        if gold is not None:
            gf = gui.font("body", True)
            self.gold_rect = draw_text(scr, f"{gold} gold", rect.x + pad, y, gf, GOLD)     # DawnBringer's yellow in the shop
            y += gf.get_height() + int(2 * self.fs)
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

    MESSAGE_MAX_LINES = 6          # Round SHOPMSG1: the town's message grows to this many lines before its last line is shortened

    def message_lines(self, gui, w, cap=2):
        """The message wrapped to `w` px, at most `cap` lines (Round SHOPMSG1): what doesn't fit goes on the last line, shortened
        with "..." - so nothing runs past the box, and nothing is lost below it unseen."""
        if not self.message:
            return []
        small = gui.font("small", True)
        lines = wrap_text(self.message[0], small, w)
        if len(lines) > cap:
            lines = lines[:cap - 1] + [clip_text(" ".join(lines[cap - 1:]), small, w)]
        return lines

    def draw_message(self, gui, x, y, w, bottom=None, lines=None):
        """The message under the stage's content: two lines from y (or `lines`, Round SHOPMSG1: the town's box grows to fit), or
        (Round PROG1b, the deck editor) up to four ending at `bottom`, so a longer line (what Card quality is) is said whole."""
        if self.message:
            small = gui.font("small", True)
            _text, colour = self.message
            lines = self.message_lines(gui, w, 4 if bottom is not None else (lines or 2))
            if bottom is not None:
                y = bottom - len(lines) * small.get_height()
                back = pygame.Rect(x - 4, y - 2, max(small.size(ln)[0] for ln in lines) + 8, len(lines) * small.get_height() + 4)
                round_rect(gui.screen, back, gfx.PANEL_DEEP, 4, 1, gfx.PANEL_EDGE)    # readable over the cards
            for ln in lines:
                draw_text(gui.screen, ln, x, y, small, colour)
                y += small.get_height()

    def draw_big(self, gui, rect):
        """The card under the mouse, big, on the side of the window away from it."""
        name, card = self.hover
        h = int(rect.h * 0.55)
        w = int(h * CARD_ASPECT)
        x = rect.x + self.pad if gui.mouse[0] > rect.centerx else rect.right - self.pad - w
        r = pygame.Rect(x, rect.y + int(rect.h * 0.2), w, h)
        fixed = getattr(self, "big_fixed", None)
        if fixed is not None and self.stage == "ready":     # Round STARTER1C: the starting-deck screen's own strip
            r = pygame.Rect(fixed)
            w, h = r.size
        self.big_drawn = pygame.Rect(r)
        gfx.shadowed(gui.screen, r, radius=max(4, int(w * 0.05)))
        gui.screen.blit(card_picture(gui, name, card, w, h), r)

    def list_rows(self, gui, area, key, count, row_h):
        """The rows of a scrolling list that fit `area`: [(index, rect)]; the scroll is kept in whole rows.
        Task bug-sell-scroll-2: a list with more than it shows has a scroll bar at its right, and its "1-13 of 87" note is always
        shown - the title (list_title) is shortened to make room for it, never the note."""
        fit = max(1, area.h // row_h)
        top = max(0, min(self.scroll.get(key, 0), max(0, count - fit)))
        self.scroll[key] = top
        rows = self.scroll_bar(gui, key, area, count, fit, top, row_h)
        self.note_pan(key, pygame.Rect(rows.x, rows.y, rows.w, fit * row_h), count > fit, row_h)     # Round SELL1
        out = []
        for j, i in enumerate(range(top, min(count, top + fit))):
            out.append((i, pygame.Rect(rows.x, rows.y + j * row_h, rows.w, row_h)))
        tiny = gui.font("tiny")
        note = ""
        if count > fit:
            note = f"{top + 1}-{min(count, top + fit)} of {count}  (drag, wheel or arrow keys)"
            if tiny.size(note)[0] > area.w // 2:             # Round RPG14b: DawnLike's wide letters - the short form
                note = f"{top + 1}-{min(count, top + fit)} of {count}"
            draw_text(gui.screen, note, area.right, area.y - 2, tiny, DIM, "bottomright")
        title = getattr(self, "_title_text", None)
        if title:
            room = area.w - (tiny.size(note)[0] + self.gap if note else 0)
            r = draw_text(gui.screen, clip_text(title, tiny, max(1, room)), area.x, area.y - 2, tiny, DIM, "bottomleft")
            self._title_end = r.right
        self._title_text = None
        return out

    def list_title(self, gui, area, text):
        """A shop list's title over its top left corner, drawn by list_rows (next) beside the list's "1-10 of 37" note: the note
        is always shown, the title is shortened when both don't fit (task bug-sell-scroll-2; Round RPG14b drew the title first
        and dropped the note)."""
        self._title_text = text
        self._title_end = area.x

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
        marks = f.upgraded()                                 # Round STARTER1
        n_up = len(f.upgrades())
        total = sum(k for _n, k in deck)
        sub = (f"{total} cards from " + ", ".join(h.theme for h in f.picks) + ". "
               + (UPGRADE_NOTE.format(n=n_up, s="" if n_up == 1 else "s") if n_up else "")
               + f"Begin saves the game in slot {self.slot} and opens the town.")
        y = self.header(gui, rect, f"{f.name}'s starting deck", sub)
        foot = self.footer(gui, rect, left=[("Back", "back", True, False)],
                           right=[("Begin the adventure", "begin", True, True)])
        self.upgrade_hover = None
        small = gui.font("small")
        # Round STARTER1: with upgrades, the list stops a line short of the message so the hover line has its own room
        bottom = foot - self.gap - (3 * small.get_height() if marks else 0)
        area = pygame.Rect(rect.x + pad, y, rect.w - 2 * pad, max(small.get_height(), bottom - y))
        # Round STARTER1C (Karl, 8 Nov 2026): the card preview has its own strip at the right, so the list never runs under it
        # (at 1360x840 @200% the second column ran under the picture); the list takes the rest, in the font and columns that
        # fit it all (small, then tiny - names shortened to the column if they must be).
        big = self.ready_big_rect(rect, area)
        self.big_fixed = big
        area = pygame.Rect(area.x, area.y, max(1, big.x - self.gap - area.x), area.h)
        fkey, min_col = self.ready_list_font(gui, area, deck, f._pool())
        self.ready_list_area = area
        self.draw_deck_text(gui, area, deck, f._pool(), marks=marks, font_key=fkey, min_col=min_col)
        if self.upgrade_hover:                               # Round STARTER1: what the hovered upgrade replaced
            name, outs = self.upgrade_hover
            words = UPGRADE_HOVER.format(name=name, outs=", ".join(sorted(set(outs))))
            draw_text(gui.screen, clip_text(words, small, area.w), area.x, area.bottom + small.get_height() // 4, small,
                      GREEN)
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * small.get_height(), rect.w - 2 * pad)

    def ready_big_rect(self, rect, area):
        """Round STARTER1C: where the starting-deck screen shows the hovered card - a strip at the area's right, as tall as
        draw_big's picture would be (55% of the window) or as the area, never wider than 30% of it."""
        h = min(int(rect.h * 0.55), area.h)
        w = int(h * CARD_ASPECT)
        if w > int(area.w * 0.3):
            w = int(area.w * 0.3)
            h = int(w / CARD_ASPECT)
        return pygame.Rect(area.right - w, area.y, w, h)

    def ready_list_font(self, gui, area, deck, pool):
        """Round STARTER1C: (font key, column width) for the starting-deck list: the first of "small" then "tiny" whose lines fit
        the area (flow_deck's rules): small with columns of at least 200 x text scale, then tiny at 200, 150, 120 (names cut to
        the column); the last when none does (draw_deck_text then stops at the area's end, as before)."""
        blocks = self.deck_blocks(deck, pool)
        tries = [("small", 200), ("tiny", 200), ("tiny", 150), ("tiny", 120)]
        for key, base in tries:
            min_col = max(1, int(base * self.fs))
            lh = gui.font(key).get_height()
            cols = max(1, min(4, area.w // min_col))
            if self.flow_deck(blocks, max(1, area.h // lh), cols) is not None:
                return key, min_col
        return tries[-1][0], max(1, int(tries[-1][1] * self.fs))

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
            draw_text(gui.screen, f"{hidden} more - scroll or drag", rect.x + self.pad, foot + self.bh // 2, small,
                      DIM, "midleft")

    # ---- Round PROG1: the Card Book and the bestiary (the pause window's) ----------------------------------------------------
    def book_filters(self, gui, rect, y):
        """The Card Book's filters: a button per colour (any of the lit ones) and per tier (one at a time; again: every tier)."""
        tiny = gui.font("tiny", True)
        fh = max(int(28 * self.fs), tiny.get_height() + 10)
        x = rect.x + self.pad
        right = rect.right - self.pad
        for c in adv.COLOURS:
            w = max(fh, gui.button_width(c, "tiny"))
            self.button(gui, pygame.Rect(x, y, w, fh), c, f"bookc:{c}", True, c in self.book_colours, fkey="tiny")
            x += w + 4
        x += self.gap
        for t in sorted(TIER_WORDS):
            label = TIER_WORDS[t]
            w = gui.button_width(label, "tiny")
            if x + w > right and x > rect.x + self.pad:
                x, y = rect.x + self.pad, y + fh + 4
            self.button(gui, pygame.Rect(x, y, w, fh), label, f"bookt:{t}", True, self.book_tier == t, fkey="tiny")
            x += w + 4
        return y + fh + int(8 * self.fs)

    # ---- Round PROG4: the Gear screen (the pause window's) and the shop's Gear tab ----------------------------------------------
    def gear_do(self, gui, what, arg):
        """One tap on the Gear screen: gear_wear:<piece> (its own slot), gear_pack:<piece> (the Pack slot), gear_off:<slot>. The save
        is written and the walk speed follows at once."""
        s = self.game.save
        try:
            if what == "gear_off":
                pid = rgear.take_off(s, arg)
                p = rgear.read().piece(pid) if pid else None
                if p is not None:
                    self.say(rgear.OFF_LINE.format(name=p.name), GREEN)
            else:
                sl = rgear.wear(s, arg, "pack" if what == "gear_pack" else None)
                self.say(rgear.WORE_LINE.format(name=rgear.read().piece(arg).name, slot=sl.name), GREEN)
        except (rgear.GearError, OSError, ValueError) as e:
            self.say(str(e), ORANGE)
            gui.play_cue("ui.error")
            return
        if self.explorer is not None:
            self.explorer.apply_speed()
        self.write()
        gui.play_cue("card.pickup")

    def gear_rows(self):
        """The Gear screen's lines: ("head", words) / ("slot", Slot, Piece or None) / ("piece", Piece, worn slot id or None) /
        ("none", words)."""
        g = self.game
        gr = rgear.read()
        worn = dict((sl.id, p) for sl, p in rgear.worn(g.save, gr))
        rows = [("head", GEAR_WORN_HEAD)]
        for sl in rgear.open_slots(g.save, gr):
            rows.append(("slot", sl, worn.get(sl.id)))
        rows.append(("head", GEAR_OWNED_HEAD))
        mine = rgear.owned(g.save, gr)
        if not mine:
            rows.append(("none", GEAR_NONE))
        for p in mine:
            rows.append(("piece", p, rgear.worn_in(g.save, p.id, gr)))
        return rows

    def draw_gear(self, gui, rect):
        """Round PROG4: the slots and what is worn in each (Take off), then every piece you own (Wear / In pack, or Worn). One tap
        = one action; drag or the wheel to scroll."""
        g = self.game
        y = self.header(gui, rect, rgear.GEAR_TITLE, GEAR_SUB)
        foot = self.footer(gui, rect, right=[("Back to the map", "gear_done", True, True)])
        small = gui.font("small")
        msg_h = 2 * small.get_height() + self.gap
        self.draw_message(gui, rect.x + self.pad, foot - msg_h, rect.w - 2 * self.pad)
        y += gui.font("tiny").get_height()                  # room for the list's "1-5 of 16" note under the words above
        area = pygame.Rect(rect.x + self.pad, y, rect.w - 2 * self.pad, foot - msg_h - self.gap - y)
        try:
            rows = self.gear_rows()
            gr = rgear.read()
            pack_open = any(sl.any for sl in rgear.open_slots(g.save, gr))
        except Exception as e:                               # never a crash: said instead
            crashlog.note(f"Adventure: the Gear screen: {type(e).__name__}: {e}")
            draw_text(gui.screen, clip_text(f"The gear can't be shown: {e}", small, area.w), area.x, area.y, small, ORANGE)
            return
        for i, r in self.list_rows(gui, area, "gear", len(rows), self.row_h(gui)):
            row = rows[i]
            if row[0] == "head":
                draw_text(gui.screen, clip_text(row[1], gui.font("body", True), r.w), r.x, r.centery, gui.font("body", True),
                          GOLD, "midleft")
            elif row[0] == "none":
                draw_text(gui.screen, clip_text(row[1], small, r.w), r.x, r.centery, small, DIM, "midleft")
            elif row[0] == "slot":
                sl, p = row[1], row[2]
                text = f"{sl.name}: {p.name}" if p else f"{sl.name}: {GEAR_EMPTY_SLOT}"
                self.draw_row(gui, r, text, p.text if p else "", [(GEAR_OFF, f"gear_off:{sl.id}", p is not None, False)],
                              icon=p.icon if p else "")
            else:
                p, where = row[1], row[2]
                sub = f"{gr.slot_name(p.slot)}{SEP}{p.rarity}{SEP}{p.text}"
                if where:
                    buttons = [(GEAR_WORN_TAG, f"gear_worn:{p.id}", False, False)]
                else:
                    buttons = [(GEAR_WEAR, f"gear_wear:{p.id}", True, True)]
                    if pack_open:
                        buttons.append((GEAR_TO_PACK, f"gear_pack:{p.id}", True, False))
                self.draw_row(gui, r, p.name, sub, buttons, icon=p.icon)

    def draw_tab_gear(self, gui, area):
        """Round PROG4: the shop's Gear tab - the region's common pieces you don't own yet, with their price and what they do."""
        g = self.game
        try:
            offers = rgear.shop_offers(g.save, g.save.world.get("region") or 1)
            gr = rgear.read()
        except Exception as e:
            crashlog.note(f"Adventure: the shop's Gear tab: {type(e).__name__}: {e}")
            draw_text(gui.screen, clip_text(f"The gear can't be shown: {e}", gui.font("small"), area.w), area.x, area.y,
                      gui.font("small"), ORANGE)
            return
        self.list_title(gui, area, GEAR_SHOP_TITLE)
        if not offers:
            draw_text(gui.screen, GEAR_SHOP_NONE, area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:gear", len(offers), self.row_h(gui)):
            p = offers[i]
            sub = f"{gr.slot_name(p.slot)}{SEP}{p.price} gold{SEP}{p.text}"
            self.draw_row(gui, r, p.name, sub, [(GEAR_BUY, f"gearbuy:{p.id}", g.save.gold >= p.price, True)], icon=p.icon)

    def draw_book(self, gui, rect):
        """Round PROG1: every card you have ever owned - its picture, tier, copies owned and in your deck, where and when you first
        found it. Filters by colour and tier; a tap on a line shows the card big; drag or the wheel to scroll."""
        g = self.game
        try:
            rows = progress.book_rows(g.save, g.pool, g.tiers(), deck=g.deck(), colours=self.book_colours, tier=self.book_tier,
                                      place_name=self.place_name)
            every = progress.book_rows(g.save, g.pool, g.tiers(), place_name=self.place_name) \
                if (self.book_colours or self.book_tier is not None) else rows
        except Exception as e:                               # never a crash: said instead
            crashlog.note(f"Adventure: the Card Book: {type(e).__name__}: {e}")
            rows = every = []
        owned = sum(1 for r in every if r.owned)
        sub = f"{len(every)} cards found, {owned} of them owned now. Tap a line to see the card; drag to scroll."
        y = self.header(gui, rect, BOOK_TITLE, sub)
        foot = self.footer(gui, rect, right=[("Back to the map", "book_done", True, True)])
        y = self.book_filters(gui, rect, y)
        area = pygame.Rect(rect.x + self.pad, y, rect.w - 2 * self.pad, foot - self.gap - y)
        scr, small, body = gui.screen, gui.font("small"), gui.font("body")
        lh = small.get_height()
        row_h = max(body.get_height() + 2 * lh + int(12 * self.fs), int(64 * self.fs))
        if not rows:
            draw_text(scr, BOOK_EMPTY if not every else BOOK_NONE, area.x, area.y, small, DIM)
        shown = self.list_rows(gui, area, "book", len(rows), row_h)
        self.prefetch(gui, [rows[i].name for i, _r in shown])
        for i, r in shown:
            row = rows[i]
            box = r.inflate(0, -int(4 * self.fs))
            hot = box.collidepoint(gui.mouse) or (self.book_big is not None and self.book_big[0] == row.name)
            round_rect(scr, box, gfx.PANEL_DEEP if not hot else gfx.PANEL, 6, 1, gfx.PANEL_EDGE)
            ph = box.h - int(10 * self.fs)
            pw = int(ph * CARD_ASPECT)
            pic = pygame.Rect(box.x + int(8 * self.fs), box.y + int(5 * self.fs), pw, ph)
            pygame.draw.rect(scr, rpg_results.tier_colour(row.tier), pic.inflate(4, 4), 2)
            scr.blit(card_picture(gui, row.name, row.card, pw, ph), pic)
            tx = pic.right + int(12 * self.fs)
            room = box.right - tx - int(8 * self.fs)
            ty = box.y + int(5 * self.fs)
            draw_text(scr, clip_text(row.name, body, room), tx, ty, body, GOLD if hot else TEXT)
            ty += body.get_height()
            tier = TIER_WORDS.get(row.tier, "No tier") if row.tier is not None else "No tier"
            said = draw_text(scr, clip_text(tier, small, room), tx, ty, small, rpg_results.tier_colour(row.tier))
            have = "-" if row.owned is None else str(row.owned)
            draw_text(scr, clip_text(f"   own {have}, in your deck {row.in_deck}", small, max(1, room - said.w)), said.right, ty,
                      small, TEXT)
            ty += lh
            draw_text(scr, clip_text(row.found, small, room), tx, ty, small, DIM)
            self.rows.append((pygame.Rect(box), "bookcard", (row.name, row.card)))
            if box.collidepoint(gui.mouse):
                self.hover = (row.name, row.card)
        if self.hover is None and self.book_big is not None:
            self.hover = self.book_big

    def draw_beasts(self, gui, rect):
        """Round PROG1: each foe kind you have met - its battle picture, where it lives, your wins and losses against it (the
        Adventure's duel record and the save) and the cards it gave you. A kind never met is "???", its picture a shadow."""
        g = self.game
        try:
            import stats as gstats
            games = gstats.load(adv.duel_stats_dir(g.folder))
        except Exception:
            games = []
        try:
            kinds = rpg_enemy_kinds()
            rows = progress.bestiary_rows(g.save, kinds, games, slot=self.slot,
                                          home_of=lambda k: (encounters.kind_text(k) or {}).get("home"))
        except Exception as e:
            crashlog.note(f"Adventure: the bestiary: {type(e).__name__}: {e}")
            rows = []
        met = sum(1 for r in rows if r.met)
        y = self.header(gui, rect, BEASTS_TITLE, f"{met} of {len(rows)} kinds of foe met. Drag to scroll.")
        foot = self.footer(gui, rect, right=[("Back to the map", "beasts_done", True, True)])
        area = pygame.Rect(rect.x + self.pad, y, rect.w - 2 * self.pad, foot - self.gap - y)
        scr, small, body = gui.screen, gui.font("small"), gui.font("body")
        lh = small.get_height()
        row_h = max(body.get_height() + 3 * lh + int(14 * self.fs), int(72 * self.fs))
        for i, r in self.list_rows(gui, area, "beasts", len(rows), row_h):
            row = rows[i]
            box = r.inflate(0, -int(4 * self.fs))
            round_rect(scr, box, gfx.PANEL_DEEP, 6, 1, gfx.PANEL_EDGE)
            side = box.h - int(10 * self.fs)
            spot = pygame.Rect(box.x + int(8 * self.fs), box.y + int(5 * self.fs), side, side)
            img = beast_picture(row.battler, side, not row.met)
            if img is not None:
                scr.blit(img, img.get_rect(midbottom=spot.midbottom))
            tx = spot.right + int(12 * self.fs)
            room = box.right - tx - int(8 * self.fs)
            ty = box.y + int(5 * self.fs)
            draw_text(scr, clip_text(row.name, body, room), tx, ty, body, GOLD if row.met else DIM)
            ty += body.get_height()
            if not row.met:
                draw_text(scr, clip_text(BEAST_UNMET, small, room), tx, ty, small, DIM)
                continue
            if row.home:
                draw_text(scr, clip_text(row.home, small, room), tx, ty, small, DIM)
                ty += lh
            draw_text(scr, clip_text(f"Won {row.won}   Lost {row.lost}   Drawn {row.draw}", small, room), tx, ty, small, TEXT)
            ty += lh
            cards = ", ".join(n + (f" x{k}" if k > 1 else "") for n, k in row.cards) if row.cards else BEAST_NO_CARDS
            draw_text(scr, clip_text("Cards won: " + cards, small, room), tx, ty, small, TEXT if row.cards else DIM)

    def draw_deck_text(self, gui, area, deck, pool, scroll_key=None, marks=None, font_key="small", min_col=None):
        """A deck as text in columns by type ("Creature (14)", then "2  Llanowar Elves" lines); the mouse shows a card big.
        Round RPG15c (review R1): with a scroll_key the columns grow as tall as the list needs and the area scrolls (self.scroll,
        in lines, the wheel), so every card can be reached; returns how many cards are out of view (0 without a scroll_key)."""
        if scroll_key is not None:
            return self.draw_deck_scrolled(gui, area, deck, pool, scroll_key)
        scr, small, bold = gui.screen, gui.font(font_key), gui.font(font_key, True)
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
        cols = max(1, min(4, area.w // (min_col or int(260 * self.fs))))
        cw = (area.w - self.gap * (cols - 1)) // cols
        x, y, c = area.x, area.y, 0
        self.deck_line_rects = []                            # Round STARTER1C: where each line went (the tests' check)
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
                self.deck_line_rects.append((name, r))
                up = (marks or {}).get(name)
                if up:                                       # Round STARTER1: an upgraded card, in green with a short tag
                    tag = UPGRADE_TAG
                    tw = small.size(tag)[0]
                    line = clip_text(f"{k}  {name}", small, max(20, cw - tw))
                    draw_text(scr, line, x, y, small, GREEN)
                    draw_text(scr, tag, x + small.size(line)[0], y, small, DIM)
                else:
                    draw_text(scr, clip_text(f"{k}  {name}", small, cw), x, y, small, TEXT)
                if r.collidepoint(gui.mouse):
                    self.hover = (name, card)
                    if up:
                        self.upgrade_hover = (name, up)
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
        rows = max(1, area.h // lh)

        def flow(width):
            cols = max(1, min(4, width // col_w))
            lines, tall = self.flow_deck(blocks, rows, cols), rows
            while lines is None:                                         # the columns as tall as the list needs
                tall += 1
                lines = self.flow_deck(blocks, tall, cols)
            return cols, lines, tall
        cols, lines, tall = flow(area.w)
        if tall > rows:                                                  # task bug-sell-scroll-2: room for the scroll bar
            probe = scrollbar.ScrollBar(base=BAR_WIDTH, min_px=int(gui.screen.get_height() * BAR_MIN_SHARE))
            probe.layout(pygame.Rect(0, 0, 10, 10), 20, 0, self.fs)
            cols, lines, tall = flow(area.w - probe.track.w - self.gap)
        top = max(0, min(self.scroll.get(key, 0), int(tall - rows)))
        self.scroll[key] = top
        area = self.scroll_bar(gui, key, area, tall, rows, top, lh)
        cw = (area.w - self.gap * (cols - 1)) // cols
        self.note_pan(key, area, tall > rows, lh)                           # Round SELL1
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
        view.held = set(rduel.held_gold(g.save))                          # Round GOLD1: a coin by each foe that holds your gold
        view.lit, view.flags = light_on(gui), flags                  # Round RPG15: the map's darkness and its lights,
        view.darkness = ex.area.darkness_at(clock.now(g.save.world))  # ... by the clock on an outdoor map
        view.flicker = bool(getattr(gui, "animations", True))
        self.camera.follow(ex.walker.x, ex.walker.y, ex.area.width, ex.area.height)
        cam = self.camera                                            # Round RPG16: things come and go outside this view
        ex.view_half = (int(cam.view_w / cam.zoom / ex.area.tw / 2) + 1, int(cam.view_h / cam.zoom / ex.area.th / 2) + 1)
        view.draw(scr, self.camera, ex.walker, ex.encounters(), time.monotonic(),
                  people=[p for p in ex.present_people() if p["when"]] + ex.walking_people(),   # RPG17: and those walking
                  chests=self.chest_list())                                                     # PROG2: treasure chests
        self.draw_shop_signs(scr, ex.area)                                                      # rly2 (bug 1566241279598264410)
        self.tap_marker_rect = None
        if self.tap is not None and self.tap.goal.area == ex.area.name:     # Round RPG-TAP: where the tap's walk is going
            self.draw_tap_marker(scr, self.tap.marker(ex), ex.area)
        self.hud = self.draw_hud(gui, ex.area.title, dict(g.panel_info(), time=time_text(g.save.world),
                                                          **self.power_info("map", (g.deck() or {}).get("main") or [])))
        small = gui.font("small")
        bw = gui.button_width("Menu")
        self.button(gui, pygame.Rect(W - bw - 8, H - self.bh - 8, bw, self.bh), "Menu", "pause")
        hw = gui.button_width("Hint")                        # Round RPG15b: the Hint button beside Menu
        self.button(gui, pygame.Rect(W - bw - 8 - self.gap - hw, H - self.bh - 8, hw, self.bh), "Hint", "hint")
        draw_text(scr, "   ".join(MAP_KEYS), 8, H - 8, small, DIM, "bottomleft", shadow=True)
        mx, my, mw = 8, H - 8 - 4 * small.get_height(), W // 2
        if self.message:
            self.draw_message(gui, mx, my, mw)

    def draw_shop_signs(self, scr, area):
        """Bug 1566241279598264410 (Karl, 3 Nov 2026, 'Visible stall'): a shop on a map that isn't a town (Brackwater's and Cinder
        Gate's stalls on the Ashfen outskirts) has a sign with the town's name over its counter - before, the shop was a hidden tile
        of plain road. self.sign_words holds [(text, screen Rect)] as drawn, for the tests."""
        self.sign_words = []
        if getattr(area, "town", None):                    # a town's own counters (Lantern Hollow) need no sign
            return
        cam, s = self.camera, rpg_ui.ui_scale(*scr.get_size())
        for t in area.triggers:
            if t.kind != "town":
                continue
            name = self.game.town_name(t.town)
            img = rpg_ui.font().render(rpg_ui.plain(name), False, gfx.DB_GOLD)
            plate = pygame.Surface((img.get_width() + 6, img.get_height() + 4))
            plate.fill(gfx.DB_BG)
            pygame.draw.rect(plate, gfx.DB_DIM, plate.get_rect(), 1)
            plate.blit(img, (3, 2))
            cx, top = cam.to_screen((t.box[0] + t.box[2]) / 2, t.box[1] - 2 * area.th)  # above the keeper and the goods behind
            w, h = plate.get_width() * s, plate.get_height() * s
            rect = pygame.Rect(int(cx - w / 2), int(top - h), w, h)
            if rect.colliderect(scr.get_rect()):
                rpg_ui._blit_scaled(scr, plate, s, rect.topleft)
                self.sign_words.append((name, rect))

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
            lines.append((f"Gold {info['gold']}", gfx.DB_GOLD))           # Erik, 1 Nov 2026: easy to find, in yellow
        if info.get("level"):                                            # Round PROG3: "Level 3  XP 12/60"
            lines.append((info["level"], gfx.DB_GREEN))
        if info.get("perk"):                                             # Round PROG3: a perk waits in the Menu
            lines.append((PERK_HUD, gfx.DB_GOLD))
        if info.get("deck"):
            cards = f" ({info['cards']})" if info.get("cards") is not None else ""
            lines.append((f"Deck {info['deck']}{cards}", gfx.DB_TEXT))
        if info.get("power"):                                            # Round PROG1: the Card quality meter (and its change)
            lines.append((info["power"], gfx.DB_GREEN if info.get("power_up") else (gfx.DB_ORANGE if info.get("power_down")
                                                                                    else gfx.DB_TEXT)))
        if info.get("time"):                                             # Round RPG15: the time of day ("Dusk 18:20")
            lines.append((info["time"], gfx.DB_DIM))
        pad = rpg_ui.PAD - 2
        cw = rpg_ui.font().size("M")[0]
        most = max(4, (W // 2 // s - 2 * pad - 8) // cw)               # characters a line may have: the window stays <= W / 2
        lines = [(t if len(t) <= most else t[:most - 3] + "...", c) for t, c in lines]
        worn = list(info.get("gear") or ())                              # Round PROG4: the worn gear's icons, a row under the words
        icon_h = HUD_ICON + 2 if worn else 0
        w = max(max(len(t) for t, _c in lines) * cw, len(worn) * (HUD_ICON + 2)) + 2 * pad
        h = 2 * pad + len(lines) * rpg_ui.PITCH_UI - 2 + icon_h
        box = pygame.Surface((w, h), pygame.SRCALPHA)
        rpg_ui.window(box, box.get_rect())
        words = []
        for i, (t, c) in enumerate(lines):
            words.append((t, rpg_ui.text(box, t, pad, pad + i * rpg_ui.PITCH_UI, c)))
        icons = []
        iy = pad + len(lines) * rpg_ui.PITCH_UI
        for j, (_slot, pid, icon, _name) in enumerate(worn):
            img = gear_icon(icon, HUD_ICON)
            spot = pygame.Rect(pad + j * (HUD_ICON + 2), iy, HUD_ICON, HUD_ICON)
            if img is not None:
                box.blit(img, spot)
            icons.append((pid, spot))
        x0 = y0 = 4 * s
        rect = rpg_ui._blit_scaled(scr, box, s, (x0, y0))
        self.hud_words = [(t, pygame.Rect(x0 + r.x * s, y0 + r.y * s, r.w * s, r.h * s)) for t, r in words]
        self.hud_icons = [(pid, pygame.Rect(x0 + r.x * s, y0 + r.y * s, r.w * s, r.h * s)) for pid, r in icons]   # PROG4
        return rect

    # town
    def draw_town(self, gui, rect):
        scr, pad = gui.screen, self.pad
        g = self.game
        d = g.deck()
        wrong = g.deck_problems()
        sub = f"Your deck: {d['name'] if d else 'none'} ({g.deck_size()} cards)" + \
              ("  -  can't be played yet" if wrong else "")
        mw = gui.button_width("Main menu", "small")
        mh = gui.font("small").get_height() + int(12 * self.fs)
        self.button(gui, pygame.Rect(rect.right - pad - mw, rect.y + pad, mw, mh), "Main menu", "menu", fkey="small")
        y = self.header(gui, pygame.Rect(rect.x, rect.y, rect.w - mw - self.gap, rect.h), g.town_name(self.town), sub,
                        gold=g.save.gold)
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
        # Round SHOPMSG2 (Karl, 8 Nov 2026: "Grow the strip + fix gallery"): the strip under the lists keeps two lines for the
        # message, and grows by a line for each line past two (up to MESSAGE_MAX_LINES; past that the last line ends in "...") -
        # the lists' area shrinks by as much, so the message never covers them.
        msg_w = rect.w - 2 * pad
        lines = self.message_lines(gui, msg_w, self.MESSAGE_MAX_LINES)
        msg_h = max(2 * small.get_height(), max(2, len(lines)) * gui.font("small", True).get_height()) + self.gap
        self.msg_strip = pygame.Rect(rect.x + pad, rect.bottom - pad - msg_h + self.gap, msg_w, msg_h - self.gap)
        share = 1.0 if self.tab in ("packs", "gear", "singles") else 0.66   # Round PACK1: a pack has no card to show at the right -
        # the whole width; Round PROG4: nor has a piece of gear; the singles gallery shows its details itself
        area = pygame.Rect(rect.x + pad, y + small.get_height(), int((rect.w - 2 * pad) * share), rect.bottom - pad - msg_h - y - small.get_height())
        getattr(self, "draw_tab_" + self.tab)(gui, area)
        if self.hover is None and self.tab not in ("packs", "gear", "singles"):
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
        # The message last (Round SHOPMSG1: drawn whole, every line inside the window), in its strip under the lists.
        self.draw_message(gui, self.msg_strip.x, self.msg_strip.y, msg_w, lines=max(2, len(lines)))

    def row_h(self, gui):
        return max(self.bh, gui.font("body").get_height() + int(14 * self.fs)) + int(4 * self.fs)

    def draw_row(self, gui, r, text, sub, buttons, name=None, icon=None):
        """One shop line: the text (and a dim second part), buttons at the right ((label, name, enabled, primary)). Round PROG4:
        `icon` (a gear icon's name) is drawn before the text."""
        scr, fs = gui.screen, self.fs
        body, small = gui.font("body"), gui.font("small")
        box = r.inflate(0, -int(4 * fs))
        lead = 0
        if icon is not None:                                 # "" keeps the icon's room empty (a slot with nothing worn)
            side = max(16, min(box.h - int(8 * fs), int(32 * fs)))
            img = gear_icon(icon, side)
            if img is not None:
                scr.blit(img, img.get_rect(midleft=(box.x + int(8 * fs), box.centery)))
            lead = side + int(8 * fs)
            box = pygame.Rect(box.x + lead, box.y, box.w - lead, box.h)
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
        """Task bug-1567699607696441414 (kr.bot.01, 5 Nov 2026: "the shop page for singles should show each card like a gallery";
        Karl, 7 Nov: "Gallery replaces list"): the singles as a grid of card pictures, like the Packs shelf - the price and the
        copies left under each card; a tap on a card shows it large at the right with its tier, what you own, and Buy. The first
        card is shown until another is tapped. Pictures come through the art loader in the background (card_picture)."""
        s, g = self.shop, self.game
        scr, fs, gap = gui.screen, self.fs, self.gap
        offers = s.singles()
        self.prefetch(gui, [o.name for o in offers])
        small = gui.font("small")
        tiny = gui.font("tiny")
        title = f"Singles  -  restocked every {g.numbers.restock_after_duels} duels"
        self._title_text = None
        if not offers:
            draw_text(scr, clip_text(title, tiny, area.w), area.x, area.y - 2, tiny, DIM, "bottomleft")
            draw_text(scr, "Nothing for sale.", area.x, area.y, small, DIM)
            self.note_pan("town:singles", pygame.Rect(area.x, area.y, area.w, 1), False, 1)
            self._fits["town:singles"] = (1, 0)
            return
        slots = [o.slot for o in offers]
        if getattr(self, "singles_pick", None) not in slots:
            self.singles_pick = slots[0]                     # the first card until another is tapped: its Buy is always there
        pick = next(o for o in offers if o.slot == self.singles_pick)
        det_w = max(int(area.w * 0.32), min(area.w // 2, gui.button_width(SINGLES_BUY) + 2 * self.pad))
        grid = pygame.Rect(area.x, area.y, area.w - det_w - 2 * gap, area.h)
        det = pygame.Rect(grid.right + 2 * gap, area.y, det_w, area.h)
        px = max(1, _DAWN["k"] or 1)
        label_h = 2 * small.get_height() + int(4 * fs) + 3 * px
        label_w = max(max(small.size(self.single_price(o))[0], small.size(self.single_left(o))[0]) for o in offers)

        def shape(width):
            return gallery_shape(grid.h, width, label_h, label_w, gap, fs, CARD_ASPECT, int(300 * fs), len(offers))

        cw, ch, pitch, cols, row_h, rows, fit = shape(grid.w)
        if rows > fit:                                       # room for the scroll bar, as the Packs shelf
            bar_w = max(int(scr.get_height() * BAR_MIN_SHARE), max(10, int(BAR_WIDTH * max(1.0, fs))))
            cw, ch, pitch, cols, row_h, rows, fit = shape(max(1, grid.w - bar_w - gap))
        if fit < 1:                                          # Round SHOPMSG2: not even one whole row fits - none is drawn
            self.note_pan("town:singles", pygame.Rect(grid.x, grid.y, grid.w, 1), False, 1)
            self._fits["town:singles"] = (0, rows)
            self.draw_single_details(gui, det, pick)
            return
        top = max(0, min(self.scroll.get("town:singles", 0), max(0, rows - fit)))
        self.scroll["town:singles"] = top
        view = self.scroll_bar(gui, "town:singles", grid, rows, fit, top, row_h)
        grid = pygame.Rect(grid.x, grid.y, view.w, grid.h)
        self.note_pan("town:singles", pygame.Rect(grid.x, grid.y, grid.w, fit * row_h), rows > fit, row_h)
        room = grid.w
        if rows > fit:                                       # "1-8 of 12" always shown; the title is shortened for it
            note = f"{top * cols + 1}-{min(len(offers), (top + fit) * cols)} of {len(offers)}"
            nr = draw_text(scr, note, grid.right, grid.y - 2, tiny, DIM, "bottomright")
            room = nr.x - grid.x - gap
        draw_text(scr, clip_text(title, tiny, max(1, room)), grid.x, grid.y - 2, tiny, DIM, "bottomleft")
        span = cols * pitch + (cols - 1) * gap
        x0 = grid.x + max(0, (grid.w - span) // 2)
        for j in range(top * cols, min(len(offers), (top + fit) * cols)):
            o = offers[j]
            col_x = x0 + (j % cols) * (pitch + gap)
            r = pygame.Rect(col_x + (pitch - cw) // 2, grid.y + (j // cols - top) * row_h, cw, ch)
            scr.blit(card_picture(gui, o.name, g.pool.get(o.name), cw, ch), r)
            if o.left <= 0:                                  # sold out: the card dimmed
                veil = pygame.Surface(r.size, pygame.SRCALPHA)
                veil.fill((*gfx.SHADOW, SOLD_OUT_VEIL))
                scr.blit(veil, r)
            if o.slot == pick.slot:                          # picked: a light frame, as a picked pack
                pygame.draw.rect(scr, gfx.DB_TEXT, r.inflate(6 * px, 6 * px), max(2, 2 * px))
            elif r.collidepoint(gui.mouse):
                pygame.draw.rect(scr, gfx.DB_DIM, r.inflate(4 * px, 4 * px), px)
            y = r.bottom + 3 * px + int(2 * fs)
            draw_text(scr, clip_text(self.single_price(o), small, pitch), r.centerx, y, small,
                      GOLD if g.save.gold >= o.price and o.left > 0 else DIM, "midtop")
            draw_text(scr, clip_text(self.single_left(o), small, pitch), r.centerx, y + small.get_height(), small,
                      DIM if o.left > 0 else ORANGE, "midtop")
            self.rows.append((r.union(pygame.Rect(col_x, r.bottom, pitch, label_h)), "single", o.slot))
        self.draw_single_details(gui, det, pick)

    @staticmethod
    def single_price(o):
        return f"{o.price} gold"

    @staticmethod
    def single_left(o):
        return f"{o.left} left" if o.left > 0 else SINGLES_SOLD_OUT

    def draw_single_details(self, gui, det, o):
        """The picked single at the right: the card large, its name, tier, price, copies left, what you own, and Buy."""
        scr, pad = gui.screen, self.pad
        g = self.game
        small, body = gui.font("small"), gui.font("body")
        rpg_ui.window(scr, det, max(1, _DAWN["k"] or 1))
        inner = det.inflate(-2 * pad, -2 * pad)
        owned = g.save.collection.count(o.name) or 0
        tier = f"Tier {o.tier}" + (f" ({TIER_NAMES[o.tier]})" if o.tier in TIER_NAMES else "")
        lines = []
        for t, font, colour in ((o.name, body, TEXT), (str(tier), small, DIM), (self.single_price(o), body, GOLD),
                                (self.single_left(o), small, DIM if o.left > 0 else ORANGE),
                                (f"You own {owned}" if owned else "You own none yet", small, DIM)):
            for ln in wrap_text(t, font, inner.w):
                lines.append((ln, font, colour))
        bh = self.bh
        words_h = sum(f.get_height() for _l, f, _c in lines)
        room = inner.h - words_h - bh - 3 * self.gap
        h = min(room, int(inner.w / CARD_ASPECT))
        w = int(h * CARD_ASPECT)
        y = inner.y
        if h >= int(40 * self.fs):
            r = pygame.Rect(inner.centerx - w // 2, inner.y, w, h)
            gfx.shadowed(scr, r, radius=max(4, int(w * 0.05)))
            scr.blit(card_picture(gui, o.name, g.pool.get(o.name), w, h), r)
            y = r.bottom + self.gap
        for ln, font, colour in lines:
            if y + font.get_height() > inner.bottom - bh - self.gap:
                break
            draw_text(scr, clip_text(ln, font, inner.w), inner.x, y, font, colour)
            y += font.get_height()
        ok = o.left > 0 and g.save.gold >= o.price
        bw = min(inner.w, gui.button_width(SINGLES_BUY))
        self.button(gui, pygame.Rect(inner.x, min(inner.bottom - bh, y + self.gap), bw, bh), SINGLES_BUY, f"buy:{o.slot}", ok, True)

    def draw_tab_packs(self, gui, area):
        """Round PACK2 (Karl, 3 Nov 2026: "a visual image of what those packs look like"): the shelf - each kind of pack as its
        wrapper (pack_art) with its price under it, in a grid that scrolls; a tap on a wrapper shows that kind's details at the right
        (what it holds, the price, Buy and open). The card pictures come through the art loader in the background: a wrapper whose
        picture isn't here yet is drawn without its window, and replaced when the picture arrives - the shelf never waits."""
        s, g = self.shop, self.game
        scr, fs, gap = gui.screen, self.fs, self.gap
        shelf = s.shelf()                                    # Round PACK1: theme packs, then each set's pack, premium, bundle
        names = {c: n for c, n, _p in s.packs()}
        halves = {h.code: h for h in s.theme_stock()}
        self.list_title(gui, area, "Booster packs  -  every card goes into your collection")
        if not shelf:
            draw_text(scr, "This town sells no packs.", area.x, area.y, gui.font("small"), DIM)
            return
        small = gui.font("small")
        det_w = max(int(area.w * 0.36), min(area.w // 2, gui.button_width(PACK_BUY) + 2 * self.pad))
        grid = pygame.Rect(area.x, area.y, area.w - det_w - 2 * gap, area.h)
        det = pygame.Rect(grid.right + 2 * gap, area.y, det_w, area.h)
        label_h = small.get_height() + int(4 * fs) + 3 * max(1, _DAWN["k"] or 1)
        label_w = max(small.size(f"{p} gold")[0] for _k, _c, p in shelf)

        def shape(width):
            return gallery_shape(grid.h, width, label_h, label_w, gap, fs, pack_art.ASPECT, int(320 * fs), len(shelf))

        ww, wh, pitch, cols, row_h, rows, fit = shape(grid.w)
        if rows > fit:                                       # rc15b (PACK2 x bug-sell-scroll-2): room for the scroll bar
            bar_w = max(int(gui.screen.get_height() * BAR_MIN_SHARE), max(10, int(BAR_WIDTH * max(1.0, fs))))
            ww, wh, pitch, cols, row_h, rows, fit = shape(max(1, grid.w - bar_w - gap))
        faces = self.pack_faces(shelf, halves)
        if fit < 1:                                          # Round SHOPMSG2: not even one whole row fits - none is drawn
            pick = getattr(self, "shelf_pick", None)
            if pick not in [(k, c) for k, c, _p in shelf]:
                pick = self.shelf_pick = None
            self.note_pan("town:packs", pygame.Rect(grid.x, grid.y, grid.w, 1), False, 1)
            self._fits["town:packs"] = (0, rows)
            self.draw_shelf_details(gui, det, pick, shelf, names, halves, faces)
            return
        top = max(0, min(self.scroll.get("town:packs", 0), max(0, rows - fit)))
        self.scroll["town:packs"] = top
        view = self.scroll_bar(gui, "town:packs", grid, rows, fit, top, row_h)     # the bar every Adventure list has
        grid = pygame.Rect(grid.x, grid.y, view.w, grid.h)
        self.note_pan("town:packs", pygame.Rect(grid.x, grid.y, grid.w, fit * row_h), rows > fit, row_h)
        if rows > fit:                                       # as list_rows: "4-6 of 18" beside the title, when there is room
            tiny = gui.font("tiny")
            note = f"{top * cols + 1}-{min(len(shelf), (top + fit) * cols)} of {len(shelf)}"
            if tiny.size(note)[0] <= grid.right - (getattr(self, "_title_end", None) or grid.x) - gap:
                draw_text(scr, note, grid.right, grid.y - 2, tiny, DIM, "bottomright")
        span = cols * pitch + (cols - 1) * gap
        x0 = grid.x + max(0, (grid.w - span) // 2)
        self.prefetch(gui, [k for k in faces.values() if k])
        pick = getattr(self, "shelf_pick", None)
        px = max(1, _DAWN["k"] or 1)
        if pick not in [(k, c) for k, c, _p in shelf]:
            pick = self.shelf_pick = None
        for j in range(top * cols, min(len(shelf), (top + fit) * cols)):
            kind, code, price = shelf[j]
            col_x = x0 + (j % cols) * (pitch + gap)
            r = pygame.Rect(col_x + (pitch - ww) // 2, grid.y + (j // cols - top) * row_h, ww, wh)
            title, sub = self.wrapper_words(kind, code, names, halves)
            face = faces.get((kind, code))
            pic = self.pack_picture(gui, face, int(ww * 0.7), int(ww * 0.7 / pack_art.CARD_ASPECT))
            surf = pack_art.pack_surface(kind, title, sub, self.pack_colours(face), pic, ww, wh,
                                         card_key=None if pic is None else (face, pic.get_size()))
            scr.blit(surf, r)
            if (kind, code) == pick:                         # picked: a light frame (gold is a premium pack's own edge)
                pygame.draw.rect(scr, gfx.DB_TEXT, r.inflate(6 * px, 6 * px), max(2, 2 * px))
            elif r.collidepoint(gui.mouse):
                pygame.draw.rect(scr, gfx.DB_DIM, r.inflate(4 * px, 4 * px), px)
            words = f"{price} gold"
            draw_text(scr, clip_text(words, small, pitch), r.centerx, r.bottom + 3 * px + int(2 * fs), small,
                      GOLD if g.save.gold >= price else DIM, "midtop")
            self.rows.append((r.union(pygame.Rect(col_x, r.bottom, pitch, label_h)), "shelf", (kind, code)))
        self.draw_shelf_details(gui, det, pick, shelf, names, halves, faces)

    def pack_faces(self, shelf, halves):
        """Round PACK2: {(kind, code): the picture key of the card on its wrapper} (rpg.pack_faces' rule)."""
        from rpg import pack_faces
        g = self.game
        key = (id(g), tuple(shelf), tuple(sorted(halves)))
        hit = getattr(self, "_faces_cache", None)
        if hit is not None and hit[0] == key:
            return hit[1]
        out = {}
        for kind, code, _p in shelf:
            if kind == "theme":
                h = halves.get(code)
                out[(kind, code)] = pack_faces.theme_face(h, g.pool, self.shop.tiers) if h is not None else None
            else:
                out[(kind, code)] = pack_faces.set_face_key(code, g.pool, self.shop.tiers, world=g.world)
        self._faces_cache = (key, out)
        return out

    def pack_colours(self, face):
        from rpg import pack_faces
        import card_data
        return pack_faces.face_colours(card_data.key_name(face) if face else None, self.game.pool)

    def pack_picture(self, gui, face, w, h):
        """Round PACK2: the card's picture for a wrapper's window, or None while it isn't on disk (never waits): the full card the
        art loader shows everywhere else (art.get / art.preview), never an art crop."""
        art = getattr(gui, "art", None)
        if art is None or not face or w < 16:
            return None
        try:
            if h > art_loader.SOURCE_SIZE[1] + 20:
                return art.preview(face, w, h)
            return art.get(face)
        except Exception:                                    # a picture must never stop the Adventure
            return None

    def wrapper_words(self, kind, code, names, halves):
        """Round PACK2: (title, the line under it) printed on a wrapper."""
        if kind == "theme":
            h = halves.get(code)
            return (h.theme if h is not None else code), WRAP_THEME.format(cards=h.size if h is not None else 20)
        title = names.get(code, code)
        try:
            pool = products.read_pool(code)
            size = len(pool["packs"][0]) if pool.get("packs") else 15
        except (OSError, ValueError, KeyError):
            size = 15
        if kind == "premium":
            rares = 2
            try:
                p = products.read_premium(code)["packs"][0]
                rares = sum(1 for c in p if c[3] in ("R", "M")) or rares
                size = len(p)
            except (OSError, ValueError, KeyError, IndexError):
                pass
            return title, WRAP_PREMIUM.format(cards=size, rares=rares)
        if kind == "bundle":
            return title, WRAP_BUNDLE.format(packs=rshop.BUNDLE_PACKS)
        return title, WRAP_PLAIN.format(cards=size)

    def holds_words(self, kind, code, halves):
        """Round PACK2: what a kind of pack holds, in words (the details at the right)."""
        if kind == "theme":
            h = halves.get(code)
            if h is None:
                return ""
            return HOLDS_THEME.format(cards=h.size, colours=products.colour_words(products.colours(h, self.game.pool)))
        try:
            data = products.read_premium(code) if kind == "premium" else products.read_pool(code)
            tmpl = template_words(data.get("template", ""))
            size = len(data["packs"][0])
        except (OSError, ValueError, KeyError, IndexError):
            return ""
        if kind == "bundle":
            return HOLDS_BUNDLE.format(packs=rshop.BUNDLE_PACKS, cards=size, template=tmpl)
        return HOLDS_PACK.format(cards=size, template=tmpl)

    def draw_shelf_details(self, gui, det, pick, shelf, names, halves, faces):
        """Round PACK2: the picked kind of pack at the right - its wrapper large, its name, what it holds, the price, Buy and open."""
        scr, fs, pad = gui.screen, self.fs, self.pad
        small, body = gui.font("small"), gui.font("body")
        rpg_ui.window(scr, det, max(1, _DAWN["k"] or 1))
        inner = det.inflate(-2 * pad, -2 * pad)
        if pick is None:
            y = inner.y
            for ln in wrap_text(SHELF_HINT, small, inner.w)[:4]:
                draw_text(scr, ln, inner.x, y, small, DIM)
                y += small.get_height()
            return
        kind, code = pick
        price = next(p for k, c, p in shelf if (k, c) == pick)
        text, sub = self.shelf_words(kind, code, price, names, halves)
        bh = self.bh
        lines = []
        for t, font, colour in ((text, body, TEXT), (self.holds_words(kind, code, halves), small, DIM),
                                (f"{price} gold", body, GOLD)):
            for ln in wrap_text(t, font, inner.w):
                lines.append((ln, font, colour))
        while lines and sum(f.get_height() for _l, f, _c in lines) > inner.h - bh - 2 * self.gap:
            drop = next((i for i, (_l, f, c) in enumerate(lines) if c is DIM), None)     # what it holds goes first, last line
            if drop is None:                                                             # first; the name and price stay
                break
            last = max(i for i, (_l, _f, c) in enumerate(lines) if c is DIM)
            lines.pop(last)
        words_h = sum(f.get_height() for _l, f, _c in lines)
        room = inner.h - words_h - bh - 3 * self.gap
        pw, ph = pack_art.fit(inner.w, room)
        if ph >= int(40 * fs):
            title, wsub = self.wrapper_words(kind, code, names, halves)
            face = faces.get(pick)
            pic = self.pack_picture(gui, face, int(pw * 0.7), int(pw * 0.7 / pack_art.CARD_ASPECT))
            surf = pack_art.pack_surface(kind, title, wsub, self.pack_colours(face), pic, pw, ph,
                                         card_key=None if pic is None else (face, pic.get_size()))
            scr.blit(surf, (inner.centerx - pw // 2, inner.y))
            y = inner.y + ph + self.gap
        else:
            y = inner.y
        for ln, font, colour in lines:
            draw_text(scr, ln, inner.x, y, font, colour)
            y += font.get_height()
        bw = min(inner.w, gui.button_width(PACK_BUY))
        self.button(gui, pygame.Rect(inner.x, min(inner.bottom - bh, y + self.gap), bw, bh), PACK_BUY,
                    f"{'pack' if kind == 'plain' else kind}:{code}", self.game.save.gold >= price, True)

    def draw_row2(self, gui, r, text, sub, buttons):
        """Round PACK1: a shop line in two lines - the text, and under it a dim second part (dropping its parts without "gold"
        while it doesn't fit) - with the buttons at the right, as draw_row's."""
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
        parts = sub.split(SEP) if sub else []
        while len(parts) > 1 and small.size(rpg_ui.plain(SEP.join(parts)))[0] > room:
            drop = next((i for i, p in enumerate(parts) if "gold" not in p), None)
            if drop is None:
                break
            parts.pop(drop)
        lines_h = body.get_height() + (small.get_height() if parts else 0)
        top = box.centery - lines_h // 2
        draw_text(scr, clip_text(text, body, room), box.x + int(10 * fs), top, body, GOLD if hot and _DAWN["k"] else TEXT)
        if parts:
            draw_text(scr, clip_text(SEP.join(parts), small, room), box.x + int(10 * fs), top + body.get_height(), small, DIM)

    def shelf_words(self, kind, code, price, names, halves):
        """Round PACK1: (the line, its dim second part) for one pack on the shelf."""
        if kind == "theme":
            h = halves.get(code)
            if h is None:
                return code, f"{price} gold"
            cols = products.colour_words(products.colours(h, self.game.pool))
            return products.theme_name(h), THEME_SUB.format(cards=h.size, colours=cols, price=price)
        name = names.get(code, code)
        if kind == "premium":
            return PREMIUM_NAME.format(set=name), PREMIUM_SUB.format(code=code, price=price)
        if kind == "bundle":
            return BUNDLE_NAME.format(set=name), BUNDLE_SUB.format(code=code, packs=rshop.BUNDLE_PACKS, price=price)
        return name, f"{code}   ·   {price} gold"

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

    def sold_words(self, name, got):
        """Round SELL1: what a sale says - and, when it took the card out of a deck, which deck and how big it is now."""
        words = f"Sold one {name} for {got} gold."
        for deck_name, n in getattr(self.shop, "taken", None) or []:
            d = next((d for d in self.game.save.decks if d.get("name") == deck_name), None)
            size = sum(int(k) for _n, k in d.get("main", [])) if d else None
            words += f" Taken out of '{deck_name}'" + (f": {size} cards now." if size is not None else ".")
        if getattr(self.shop, "taken", None) and self.game.deck_problems():
            words += " Add cards before you duel."
        return words

    def traded_words(self, name, copies, got, tier):
        """Round PROG5: what a trade says - and, as a sale does (SELL1), which deck it took copies out of."""
        words = rextras.TRADE_DONE.format(copies=copies, name=name, got=got, tier=tier)
        for deck_name, n in getattr(self.shop, "taken", None) or []:
            d = next((d for d in self.game.save.decks if d.get("name") == deck_name), None)
            size = sum(int(k) for _n, k in d.get("main", [])) if d else None
            words += f" Taken out of '{deck_name}'" + (f": {size} cards now." if size is not None else ".")
        if getattr(self.shop, "taken", None) and self.game.deck_problems():
            words += " Add cards before you duel."
        return words

    def trade_list(self):
        g, s = self.game, self.shop
        return rextras.trade_rows(g.save, g.tiers(), s.band, adv.deck_uses(g))

    def draw_tab_trade(self, gui, area):
        """Round PROG5: Trade up - every card you own enough copies of, at a tier the region trades up from."""
        try:
            ex = rextras.read()
            rows = self.trade_list()
        except (OSError, ValueError) as e:
            draw_text(gui.screen, f"Trade up can't be read: {e}", area.x, area.y, gui.font("small"), RED)
            return
        self.list_title(gui, area, rextras.TRADE_TITLE.format(copies=ex.trade_copies))
        if not rows:
            tiers = sorted(rextras.trade_tiers(self.shop.band, ex))
            draw_text(gui.screen, rextras.TRADE_NONE.format(copies=ex.trade_copies, tiers=" or ".join(map(str, tiers))),
                      area.x, area.y, gui.font("small"), DIM)
        label = f"Trade {ex.trade_copies}"
        for i, r in self.list_rows(gui, area, "town:trade", len(rows), self.row_h(gui)):
            name, tier, have, used = rows[i]
            sub = f"tier {tier} -> {tier + 1}   ·   you own {have}" + (f"   ·   your deck uses {used}" if used else "")
            self.draw_row(gui, r, name, sub, [(label, f"trade:{name}", True, False)], name=name)

    def draw_tab_sell(self, gui, area):
        rows = self.sell_list()
        self.list_title(gui, area, "Sell  -  a card your deck uses comes out of the deck")       # Round SELL1
        if not rows:
            draw_text(gui.screen, "You have nothing to sell.", area.x, area.y, gui.font("small"), DIM)
        for i, r in self.list_rows(gui, area, "town:sell", len(rows), self.row_h(gui)):
            name, have, each, used = rows[i]
            sub = f"you own {have}   ·   {each} gold each" + (f"   ·   your deck uses {used}" if used else "")
            ok = each >= 1 and have >= 1                  # Round SELL1 (Karl, 2 Nov 2026): any card, not only spare copies
            self.draw_row(gui, r, name, sub, [("Sell 1", f"sell:{name}", ok, False)], name=name)

    # pack
    def pack_pages(self):
        """Round PACK1: how many packs the opened cards came in (1, or a bundle's 3)."""
        cards = (self.opened or ("", []))[1]
        return 1 + max((c.get("pack", 0) for c in cards), default=0)

    def draw_pack(self, gui, rect):
        scr, pad = gui.screen, self.pad
        name, cards = self.opened or ("", [])
        new = sum(1 for c in cards if c.get("new"))
        pages = self.pack_pages()                            # Round PACK1: a bundle shows its packs one page each
        sub = f"{len(cards)} cards, {new} new to your collection. You have {self.game.save.gold} gold."
        left = ()
        if pages > 1:
            page = max(0, min(pages - 1, self.pack_page))
            sub = PAGE_WORDS.format(page=page + 1, pages=pages) + "   " + sub
            cards = [c for c in cards if c.get("pack", 0) == page]
            left = [("Previous pack", "pack_prev", page > 0, False), ("Next pack", "pack_next", page < pages - 1, True)]
        y = self.header(gui, rect, f"{name}: your pack" + ("s" if pages > 1 else ""), sub)
        foot = self.footer(gui, rect, left=left, right=[("Back to the shop", "pack_done", True, True)])
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
        self.draw_message(gui, rect.x + pad, foot - self.gap - 2 * small.get_height(), int(rect.w * 0.6), bottom=foot - self.gap)

    def draw_pool(self, gui, area):
        scr, fs, e = gui.screen, self.fs, self.editor
        rows = e.collection_rows()
        self.prefetch(gui, [r.name for r in rows[:120]])
        gap = int(6 * fs)
        full = area

        def grid(width):
            per_row = max(3, width // int(118 * fs))
            w = max(30, (width - (per_row - 1) * gap) // per_row)
            h = int(w / CARD_ASPECT)
            if h > area.h:                                   # Round RPG14b: a row taller than the room (big letters push the
                h = max(1, area.h)                            # filters down) is shrunk to it, never drawn over the buttons
                w = int(h * CARD_ASPECT)
            return per_row, w, h, max(1, (area.h + gap) // (h + gap)), (-(-len(rows) // per_row) if rows else 0)
        per_row, w, h, fit_rows, total_rows = grid(area.w)
        if total_rows > fit_rows:                            # task bug-sell-scroll-2: room for the scroll bar at the right
            bw = scrollbar.ScrollBar(base=BAR_WIDTH, min_px=int(gui.screen.get_height() * BAR_MIN_SHARE))
            bw.layout(pygame.Rect(0, 0, 10, 10), 20, 0, fs)
            per_row, w, h, fit_rows, total_rows = grid(area.w - bw.track.w - self.gap)
        top = max(0, min(self.scroll.get("deckpool", 0), max(0, total_rows - fit_rows)))
        self.scroll["deckpool"] = top
        area = self.scroll_bar(gui, "deckpool", full, total_rows, fit_rows, top, h + gap)
        self.note_pan("deckpool", area, total_rows > fit_rows, h + gap)     # Round SELL1
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
            room = full.right - (getattr(self, "_search_end", None) or full.x) - self.gap
            note = f"rows {top + 1}-{min(total_rows, top + fit_rows)} of {total_rows}  (drag, wheel or arrow keys)"
            if tiny_f.size(note)[0] > room:                  # Round RPG14b: DawnLike's wide letters - the short form, or none
                note = f"rows {top + 1}-{min(total_rows, top + fit_rows)} of {total_rows}"
            if tiny_f.size(note)[0] <= room:                 # (the scroll bar beside the cards says it scrolls when this can't)
                draw_text(scr, note, full.right, full.y - 2, tiny_f, DIM, "bottomright")

    def draw_deck_list(self, gui, area):
        """The deck on the right, by type; it scrolls (the wheel over it) when it is longer than the panel. The rules' problems,
        or "ready to duel", stay at the bottom."""
        scr, e = gui.screen, self.editor
        small, bold = gui.font("small"), gui.font("small", True)
        tiny = gui.font("tiny")
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
        main = e.as_deck().get("main") or []
        pw = self.power_info("deck", main)                                  # Round PROG1: the meter (PROG1b: Card quality)
        foot = [("text", ln, colour, small) for ln, colour in foot]
        head = []
        if pw:
            colour = GREEN if pw.get("power_up") else (ORANGE if pw.get("power_down") else GOLD)
            head = [("quality", ln, colour, small) for ln in wrap_text(pw["power"], small, inner.w)[:1]]
            said = wrap_text(self.quality_explain(), tiny, inner.w)
            if len(said) <= 4 and inner.h - (len(said) + 6) * tiny.get_height() > 10 * lh:
                head += [("explain", ln, DIM, tiny) for ln in said]      # under it when it fits whole; else a tap says it
        shape = self.shape_items(gui, main, inner.w)
        spar_lines = self.spar_history(e.as_deck())                         # Round SPAR1: the yard's measured win chance
        tries = []
        if spar_lines:                                       # the whole history, then the last only, then one short line
            full = []
            for i, said in enumerate(spar_lines):
                full += [("spar", ln, TEXT if i == 0 else DIM, tiny) for ln in wrap_text(said, tiny, inner.w)[:3 if i == 0 else 2]]
            last = [x for x in full if x[2] == TEXT]
            short = [("spar", ln, TEXT, tiny) for ln in wrap_text(rspar.short_line(self.game.save, e.as_deck()), tiny, inner.w)[:2]]
            tries = [full, last, short]

        def sized(items):
            self._curve_compact = False
            hs = [self.shape_curve_h(gui) if kind == "curve" else f.get_height() for kind, _t, _c, f in items]
            if inner.h - sum(hs) < 6 * lh:                   # the deck list would keep under 6 lines: the curve without bars
                self._curve_compact = True
                hs = [self.shape_curve_h(gui) if kind == "curve" else f.get_height() for kind, _t, _c, f in items]
            return hs
        foot = head + shape + foot
        heights = sized(foot)
        for k, extra in enumerate(tries):                    # the first that leaves the deck list 6 lines (else the short one)
            hs = sized(head + extra + shape + foot[len(head) + len(shape):])
            if inner.h - sum(hs) >= 6 * lh or k == len(tries) - 1:
                foot = head + extra + shape + foot[len(head) + len(shape):]
                heights = hs
                break
        foot_top = inner.bottom - sum(heights)
        y_f = foot_top
        self.shape_drawn = {"lines": [], "curve": None}
        for (kind, ln, colour, f), h in zip(foot, heights):
            if kind == "curve":
                self.draw_curve(gui, pygame.Rect(inner.x, y_f, inner.w, h), ln)
            else:
                r = draw_text(scr, ln, inner.x, y_f, f, colour)
                self.shape_drawn["lines"].append((kind, ln, pygame.Rect(r)))
            y_f += h
        lines = []
        for g, items in e.deck_rows():
            lines.append(("head", f"{g} ({sum(k for _n, k, _c in items)})", None))
            lines += [("card", name, (k, card)) for name, k, card in items]
        if not lines:
            draw_text(scr, "Your deck is empty: click cards on the left.", inner.x, inner.y, small, DIM)
            return
        room = max(lh, foot_top - lh // 2 - inner.y)
        fit = max(1, room // lh)
        if len(lines) > fit:                                 # the "lines 1-14 of 45" note goes inside the panel, above the status
            fit = max(1, (room - tiny.get_height()) // lh)
        top = max(0, min(self.scroll.get("decklist", 0), max(0, len(lines) - fit)))
        self.scroll["decklist"] = top
        inner = self.scroll_bar(gui, "decklist", inner, len(lines), fit, top, lh)     # task bug-sell-scroll-2
        self.note_pan("decklist", inner, len(lines) > fit, lh)              # Round SELL1
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
            note = clip_text(f"lines {top + 1}-{min(len(lines), top + fit)} of {len(lines)}", tiny, inner.w)
            draw_text(scr, note, inner.right, foot_top - lh // 4, tiny, DIM, "bottomright")

    def spar_history(self, deck):
        """Round SPAR1: "Last sparred: Day 4, 65% (50-78%)" (+ "Before: ...") for this deck; [] when never sparred."""
        try:
            return rspar.history_lines(self.game.save, deck)
        except Exception as e:                               # it must never stop the editor
            if not getattr(self, "_spar_noted", False):
                self._spar_noted = True
                crashlog.note(f"Adventure: the deck's sparring history: {type(e).__name__}: {e}")
            return []

    # ---- Round PROG1b: the deck's shape (rpg.shape), under Card quality in the deck editor ----
    def shape_items(self, gui, main, width):
        """The shape's footer items: the mana curve, then the counts ("Creatures 14   Other spells 9   Lands 17   Removal 4",
        wrapped). [] when it can't be worked out (it must never stop the editor)."""
        try:
            s = rshape.shape(main, self.game.pool)
        except Exception as e:
            if not getattr(self, "_shape_noted", False):
                self._shape_noted = True
                crashlog.note(f"Adventure: the deck's shape: {type(e).__name__}: {e}")
            return []
        self.shape_info = s
        tiny = gui.font("tiny")
        counts, line = [], ""
        for item in rshape.shape_lines(s):                   # a count never splits from its word
            if line and tiny.size(line + "   " + item)[0] > width:
                counts.append(line)
                line = item
            else:
                line = f"{line}   {item}" if line else item
        counts.append(line)
        return [("curve", s["curve"], TEXT, tiny)] + [("counts", ln, TEXT, tiny) for ln in counts]

    def shape_curve_h(self, gui):
        th = gui.font("tiny").get_height()
        if getattr(self, "_curve_compact", False):           # numbers over mana values, no bars (a small window at big text)
            return 2 * th + 2 + max(2, th // 3)
        return 2 * th + max(6, int(1.6 * th)) + 4 + max(2, th // 3)

    def draw_curve(self, gui, rect, curve):
        """The mana curve: one column per mana value (0 ... 5, 6+), the count over a bar, the mana value under it."""
        scr, tiny = gui.screen, gui.font("tiny")
        th = tiny.get_height()
        bar_room = max(0, rect.h - 2 * th - 4 - max(2, th // 3))
        most = max(curve) or 1
        cw = rect.w / len(curve)
        bars = []
        for i, n in enumerate(curve):
            cx = int(rect.x + (i + 0.5) * cw)
            bh = int(round(bar_room * n / most))
            bw = max(3, int(cw * 0.6))
            bar = pygame.Rect(cx - bw // 2, rect.y + th + 2 + bar_room - bh, bw, bh)
            if bh:
                pygame.draw.rect(scr, gfx.BTN_GOLD_BOTTOM, bar)
            draw_text(scr, str(n), cx, bar.y - 1, tiny, TEXT if n else DIM, "midbottom")
            draw_text(scr, rshape.CURVE_LABELS[i], cx, rect.bottom - max(2, th // 3), tiny, DIM, "midbottom")
            bars.append((rshape.CURVE_LABELS[i], n, bar))
        self.shape_drawn["curve"] = (pygame.Rect(rect), bars)

    # the small windows over a stage
    def draw_sub(self, gui):
        L, scr = gui.L, gui.screen
        layer = pygame.Surface(scr.get_size(), pygame.SRCALPHA)
        layer.fill(gfx.BACKDROP + (150,))
        scr.blit(layer, (0, 0))
        self.buttons = []
        fs = self.fs
        tf, body = gui.font("title", True), gui.font("body")
        if self.sub == "perk":                                    # Round PROG3: choose a perk
            self.draw_perk(gui)
            return
        if self.sub == "spar":                                    # Round SPAR1: the practice duels' progress (any stage)
            self.buttons = self.draw_spar(gui)
            self.rect = scr.get_rect()
            return
        if self.sub == "result" and self.results is not None:     # Round PROG1: the results screen over any other stage
            self.buttons = self.draw_result_over(gui)
            self.rect = scr.get_rect()
            return
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
