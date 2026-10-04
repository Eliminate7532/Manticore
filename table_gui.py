# SPDX-License-Identifier: GPL-3.0-or-later
"""
table_gui.py - Pygame table for a solo Commander game: you (bottom) vs an AI opponent (top)

Usage (run from the project folder):
    python table_gui.py                        your deck = AI deck = the bundled Kinnan sample
    python table_gui.py my_deck.txt            your deck from a text export; AI plays the same list
    python table_gui.py my_deck.txt ai.txt     separate decks for you and the AI
A deck can be a text file (e.g. Moxfield "Export" text) or an Archidekt URL.
Moxfield URLs are usually blocked for scripts, so export the text instead.

Press H in the game for the controls. The AI opponent only untaps and draws for now.

Window: resizable, F11 toggles fullscreen. Text size: + / - keys or the A- / A+ buttons.
Both settings are remembered in settings.json next to this file.
"""
import copy
import itertools
import json
import os
import queue
import random
import sys
import threading
import time
from collections import deque
from types import SimpleNamespace

if sys.platform == "win32":
    # Without this, Windows stretches the window on scaled (high-DPI) displays, which makes
    # everything blurry and reports the wrong size for fullscreen.
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pygame

import game_actions as ga
from card_data import CardDataStore
from deck_importer import DeckImportError, import_deck, import_from_text
from game_state import GameState, Player
from mana_system import COLOR_ORDER, card_face, parse_mana_cost

pygame.init()

# ---- sizes -------------------------------------------------------------------
# The layout is computed from the window size every frame (see make_layout), so these
# are only the "design" size that fonts and spacing are scaled from.
BASE_W, BASE_H = 1280, 800
DEFAULT_WINDOW = (1280, 800)
MIN_WINDOW = (800, 520)
CARD_ASPECT = 488 / 680            # width / height of a Scryfall card image
SOURCE_SIZE = (244, 340)           # card art is kept at this size and scaled from it
AI_CARD_RATIO = 0.6                # the AI's cards are this fraction of yours
TEXT_STEPS = (1.0, 1.25, 1.5, 1.75, 2.0)

# Font sizes at design size, multiplied by (window scale x text scale).
FONT_BASE = dict(title=17, body=14, small=12, btn=15, hint=13, toast=15, status=14,
                 pname=16, ptext=13, log=12, count=22, menu=15, view=15, viewhead=18,
                 help=15, boxlabel=13)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DECK = os.path.join(BASE_DIR, "sample_decks", "stompy_goreclaw.txt")      # patch 38 (was the Kinnan sample)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

ART_RETRY_SECONDS = 30   # wait this long before retrying a card whose art failed
UNDO_LIMIT = 40
IMAGES_PER_FRAME = 4     # decode at most this many downloaded images per frame

WHITE, GREY, YELLOW = (255, 255, 255), (170, 170, 175), (255, 235, 90)
TABLE_GREEN, AI_GREEN, PANEL_BG = (20, 90, 40), (14, 70, 32), (16, 16, 22)
MANA_COLOURS = {"W": (245, 238, 190), "U": (90, 150, 235), "B": (85, 75, 90),
                "R": (225, 90, 65), "G": (70, 165, 90), "C": (175, 175, 180)}
TOAST_COLOURS = {"ok": (25, 70, 40), "fail": (120, 30, 30), "info": (30, 50, 90)}
MANUAL_KEYS = {pygame.K_1: "W", pygame.K_2: "U", pygame.K_3: "B",
               pygame.K_4: "R", pygame.K_5: "G", pygame.K_6: "C"}
BIGGER_KEYS = (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS)
SMALLER_KEYS = (pygame.K_MINUS, pygame.K_KP_MINUS)

HELP_ENTRIES = [
    ("Left-click hand card", "Play a land / cast a spell. Mana is paid by auto-tapping your untapped lands and rocks."),
    ("Left-click permanent", "Tap it for mana (a menu appears if it has several options). A tapped one untaps. Fetchlands crack: you pay the life and pick the land."),
    ("Fetchland played", "After you play a fetchland you are asked: crack it now or keep it (click it later)."),
    ("Before turn 1", "T flips who goes first while you decide on your hand. After you keep, cards like Gemstone Caverns "
                      "(only if you go second) and Leylines can begin the game on the battlefield; pick them, then start."),
    ("Left-click command zone", "Cast your commander (commander tax is included)."),
    ("Left-click Library", "Draw a card.  Graveyard / Exile: open the list."),
    ("Right-click anything", "Menu: move it to another zone, play tapped, cast without paying..."),
    ("Life + / -", "Left-click changes life by 1, right-click by 5."),
    ("Space", "End turn"),
    ("D  /  Z", "Draw a card  /  undo the last action"),
    ("L  /  G  /  E", "Search library  /  look at graveyard  /  look at exile"),
    ("1-6  and  0", "Add W U B R G C to your mana pool by hand  /  empty the pool"),
    ("+  /  -", "Bigger / smaller text (also the A+ and A- buttons). Ctrl+0 = normal size."),
    ("F11  or  Alt+Enter", "Fullscreen on / off. The window can also be resized by dragging its edge."),
    ("H", "This help"),
    ("", "Spell effects, targeting, combat and abilities other than mana are NOT automated: do them by hand with the "
         "right-click menus. The AI opponent only untaps and draws for now."),
]

_FONT_CACHE = {}


def get_font(px, bold=False):
    # SysFont is slow; build each size once instead of on every card every frame.
    px = max(7, int(round(px)))
    key = (px, bold)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = pygame.font.SysFont("arial", px, bold=bold)
    return _FONT_CACHE[key]


def wrap_text(text, font, width):
    """Split text into lines no wider than `width` pixels (keeps explicit newlines)."""
    lines = []
    for paragraph in (text or "").split("\n"):
        words, line = paragraph.split(" "), ""
        for word in words:
            trial = f"{line} {word}".strip()
            if line and font.size(trial)[0] > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def clip_text(text, font, width):
    """Shorten text with '...' so it fits in `width` pixels."""
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text + "..."


def fit_text(candidates, font, width):
    """First candidate (longest first) that fits in `width`; the last one is clipped if none do."""
    for c in candidates:
        if font.size(c)[0] <= width:
            return c
    return clip_text(candidates[-1], font, width)


def layout_row(widths, x0, avail_w, gap):
    """Left x of each item in a row. Items overlap (later ones on top) when the row is too full."""
    n = len(widths)
    if n == 0:
        return []
    total = sum(widths) + gap * (n - 1)
    if total <= avail_w or n == 1:
        xs, x = [], x0
        for w in widths:
            xs.append(x)
            x += w + gap
        return xs
    lead = sum(w + gap for w in widths[:-1])
    k = max(0.05, (avail_w - widths[-1]) / lead)
    xs, x = [], float(x0)
    for w in widths[:-1]:
        xs.append(int(x))
        x += (w + gap) * k
    xs.append(int(x))
    return xs


class Menu:
    """A small pop-up list. items = [(label, callback_or_None), ...]; None means greyed out."""

    def __init__(self, items, pos, screen_size, font_px, title=None):
        font, tfont = get_font(font_px), get_font(font_px * 0.88, True)
        self.items, self.title, self.font_px = items, title, font_px
        self.row_h = font.get_linesize() + max(4, font_px // 3)
        pad = max(10, font_px)
        width = max([font.size(label)[0] for label, _ in items] + ([tfont.size(title)[0]] if title else [0])) + 2 * pad
        title_h = tfont.get_linesize() + 6 if title else 0
        height = self.row_h * len(items) + title_h + 8
        width, height = min(width, screen_size[0] - 8), min(height, screen_size[1] - 8)
        x = max(4, min(pos[0], screen_size[0] - width - 4))
        y = max(4, min(pos[1], screen_size[1] - height - 4))
        self.rect = pygame.Rect(x, y, width, height)
        self.title_h, self.pad = title_h, pad
        self.item_rects = [pygame.Rect(x + 3, y + 4 + title_h + i * self.row_h, width - 6, self.row_h)
                           for i in range(len(items))]

    def callback_at(self, pos):
        for rect, (_label, cb) in zip(self.item_rects, self.items):
            if rect.collidepoint(pos):
                return cb
        return None

    def draw(self, screen, mouse):
        pygame.draw.rect(screen, (28, 28, 34), self.rect, border_radius=6)
        pygame.draw.rect(screen, (140, 140, 150), self.rect, 1, border_radius=6)
        font = get_font(self.font_px)
        if self.title:
            tfont = get_font(self.font_px * 0.88, True)
            screen.blit(tfont.render(clip_text(self.title, tfont, self.rect.w - 16), True, YELLOW),
                        (self.rect.x + 10, self.rect.y + 4))
        for rect, (label, cb) in zip(self.item_rects, self.items):
            if cb and rect.collidepoint(mouse):
                pygame.draw.rect(screen, (60, 90, 150), rect, border_radius=4)
            colour = WHITE if cb else (110, 110, 115)
            screen.blit(font.render(clip_text(label, font, rect.w - 12), True, colour),
                        (rect.x + 8, rect.y + (self.row_h - font.get_linesize()) // 2))


def flow_buttons(specs, font, pad, gap, x0, avail_w, short=False):
    """Place buttons left to right, wrapping to a new row when full. Returns (items, row_count).

    Each spec is (full_label, short_label, name, enabled); each item is [row, x, width, label, name, enabled].
    """
    items, x, row = [], x0, 0
    for full, short_label, name, enabled in specs:
        label = short_label if short else full
        w = font.size(label)[0] + 2 * pad
        if x > x0 and x + w > x0 + avail_w:
            row += 1
            x = x0
        items.append([row, x, w, label, name, enabled])
        x += w + gap
    return items, row + 1


class TableGUI:
    def __init__(self, game_state: GameState, card_store: CardDataStore, prefetch_names=None,
                 settings_path=None, window_size=None, first_player=None):
        self.gs = game_state
        self.store = card_store
        self.settings_path = settings_path       # None = don't read or write settings (tests)
        self.text_scale = 1.0
        self.fullscreen = False
        self.windowed_size = DEFAULT_WINDOW
        self._load_settings()
        if window_size:
            self.windowed_size = window_size
        self.screen = None
        self._open_window()
        pygame.display.set_caption("Commander Solo Table")
        self.clock = pygame.time.Clock()
        self.L = None

        # interaction state
        self.mode = "mulligan"          # "mulligan" -> "bottom" (only after a mulligan) -> "play"
        self.bottom_sel = set()         # hand indexes chosen to go to the bottom
        self.menu = None
        self.viewer = None              # zone list overlay (see open_viewer)
        self.viewer_rows = []
        self.viewer_rect = pygame.Rect(0, 0, 0, 0)
        self.help_open = False
        self.undo = deque(maxlen=UNDO_LIMIT)
        # who goes first: a random roll unless the caller fixes it; T flips it before you keep
        self.gs.first_player_index = random.randrange(len(self.gs.players)) if first_player is None else first_player
        self.toast = ("Keep this hand or mulligan?  (K = keep, M = mulligan)  " + self.order_text(), "info")
        self.mouse = (-1, -1)
        self.hits, self._new_hits = [], []
        self.preview_card = None        # (name, face) shown in the right panel
        self._prev_cache = {}

        # Images. ONE background worker does all network work (card data first,
        # then art), so a 100-card deck never freezes the window and requests stay
        # under Scryfall's rate limit. The GUI thread only ever calls
        # store.peek_card / peek_image_path, which read the cache.
        self.imgs = {}                  # name -> card art Surface at SOURCE_SIZE (scaled per frame size)
        self.bad_images = set()
        self.image_error = None
        self._surf_cache = {}
        self._card_dims = None
        self._layout_key = None
        self._requested = set()         # names queued or in flight
        self._bumped = set()            # names moved to the front of the queue
        self._failed_until = {}
        self._jobs = queue.PriorityQueue()
        self._done = queue.Queue()
        self._seq = itertools.count()
        self.data_ready = threading.Event()
        self.stage = "Loading card data..."      # what the status bar says while data_ready is not set
        self.rules_note = ""                     # shown when some Forge rules scripts could not be loaded
        threading.Thread(target=self._worker, daemon=True).start()

        names = sorted(set(prefetch_names or []))
        if names:
            self._jobs.put((-1, next(self._seq), "prefetch", names))
            for n in names:                       # download all art in the background, low priority
                self._requested.add(n)
                self._jobs.put((1, next(self._seq), "art", n))
        else:
            self.data_ready.set()

    # ---- convenient views ------------------------------------------------

    @property
    def me(self):
        return self.gs.players[0]

    @property
    def ai(self):
        return self.gs.players[1]

    def say(self, message, kind="info"):
        self.toast = (message, kind)

    def font(self, key, bold=False):
        return get_font(self.L.px[key], bold)

    # ---- settings, window ----------------------------------------------------

    def _load_settings(self):
        if not self.settings_path or not os.path.isfile(self.settings_path):
            return
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            scale = float(data.get("text_scale", 1.0))
            self.text_scale = min(TEXT_STEPS, key=lambda s: abs(s - scale))
            self.fullscreen = bool(data.get("fullscreen", False))
            w, h = data.get("window_size", DEFAULT_WINDOW)
            self.windowed_size = (max(MIN_WINDOW[0], int(w)), max(MIN_WINDOW[1], int(h)))
        except (OSError, ValueError, TypeError) as e:
            print(f"[table_gui] Ignoring unreadable settings file ({e})")

    def save_settings(self):
        if not self.settings_path:
            return
        data = {"text_scale": self.text_scale, "fullscreen": self.fullscreen,
                "window_size": list(self.windowed_size)}
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            print(f"[table_gui] Could not save settings: {e}")

    def _open_window(self):
        if self.fullscreen:
            try:
                self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                return
            except pygame.error as e:
                print(f"[table_gui] Fullscreen failed ({e}); using a window")
                self.fullscreen = False
        self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)

    def toggle_fullscreen(self):
        if not self.fullscreen:
            self.windowed_size = self.screen.get_size()
        self.fullscreen = not self.fullscreen
        self._open_window()
        self.say("Fullscreen on (F11 or Esc to leave)." if self.fullscreen else "Windowed. Drag the window edge to resize.",
                 "info")
        self.save_settings()

    def resize_window(self, size):
        """Set the (windowed) size; the layout follows on the next frame."""
        if self.fullscreen:
            return
        self.windowed_size = (max(MIN_WINDOW[0], size[0]), max(MIN_WINDOW[1], size[1]))
        self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)

    def change_text_scale(self, direction=0):
        """direction +1 / -1 steps through TEXT_STEPS; 0 resets to 100%."""
        i = TEXT_STEPS.index(self.text_scale)
        j = 0 if direction == 0 else max(0, min(len(TEXT_STEPS) - 1, i + direction))
        if j == i and direction != 0:
            self.say(f"Text size is already {'maximum' if direction > 0 else 'minimum'} ({round(self.text_scale * 100)}%).", "info")
            return
        self.text_scale = TEXT_STEPS[j]
        self.say(f"Text size {round(self.text_scale * 100)}%", "info")
        self.save_settings()

    # ---- background worker & images ----------------------------------------

    def _worker(self):
        while True:
            _prio, _seq, kind, payload = self._jobs.get()
            try:
                if kind == "prefetch":
                    self.store.prefetch_cards(payload)
                    prefetch_forge = getattr(self.store, "prefetch_forge", None)
                    if prefetch_forge:
                        self.stage = "Loading Forge rules scripts..."
                        have, total = prefetch_forge(payload)
                        if have < total:
                            why = getattr(getattr(self.store, "forge", None), "last_error", None) or ""
                            self.rules_note = (f"Forge rules scripts: {have} of {total} cards loaded; the rest use their "
                                               f"card text. {why}").strip()
                    self.data_ready.set()
                    continue
                path = self.store.get_image_path(payload)
                err = None if path else self.store.last_error
                self._done.put((payload, path, err))
            except Exception as e:   # never let the worker thread die silently
                if kind == "prefetch":
                    self.data_ready.set()
                self._done.put((payload if kind == "art" else "", None,
                                f"Unexpected error in background loader: {type(e).__name__}: {e}"))

    def _collect_art_results(self):
        """Runs on the GUI thread once per frame: turn finished downloads into Surfaces."""
        for _ in range(IMAGES_PER_FRAME):
            try:
                name, path, err = self._done.get_nowait()
            except queue.Empty:
                return
            self._requested.discard(name)
            if not name:
                self.image_error = err
                continue
            if name in self.imgs:
                continue
            if not path:
                self._failed_until[name] = time.time() + ART_RETRY_SECONDS
                self.image_error = err or f"No art available for '{name}'"
                continue
            try:
                img = pygame.image.load(path).convert()
                self.imgs[name] = pygame.transform.smoothscale(img, SOURCE_SIZE)
                self._failed_until.pop(name, None)
            except Exception as e:
                self.bad_images.add(name)
                self.image_error = f"pygame could not load image for '{name}': {e}"
                print(f"[table_gui] {self.image_error}")

    def request_art(self, name):
        """Ask the worker for a card's art now (front of the queue)."""
        if name in self.imgs or name in self.bad_images:
            return
        retry_at = self._failed_until.get(name)
        if retry_at is not None and time.time() < retry_at:
            return
        if name in self._requested:
            if name not in self._bumped:
                self._bumped.add(name)
                self._jobs.put((0, next(self._seq), "art", name))
            return
        self._requested.add(name)
        self._bumped.add(name)
        self._jobs.put((0, next(self._seq), "art", name))

    def get_source(self, name):
        if name in self.imgs:
            return self.imgs[name]
        self.request_art(name)
        return None

    def preview_surface(self, name, w, h):
        key = (name, w, h)
        if key in self._prev_cache:
            return self._prev_cache[key]
        path = self.store.peek_image_path(name)
        if not path:
            self.request_art(name)
            return None
        try:
            surf = pygame.transform.smoothscale(pygame.image.load(path).convert(), (w, h))
        except Exception:
            return None
        if len(self._prev_cache) >= 12:
            self._prev_cache.pop(next(iter(self._prev_cache)))
        self._prev_cache[key] = surf
        return surf

    # ---- layout -----------------------------------------------------------------

    def button_specs(self):
        """(full label, short label, name, enabled) for the button row under the hand."""
        if self.mode == "mulligan":
            can = self.me.mulligans + 1 - ga.FREE_MULLIGANS < ga.HAND_SIZE
            first = self.gs.first_player_index == 0
            return [("Keep hand (K)", "Keep", "keep", True), ("Mulligan (M)", "Mulligan", "mulligan", can),
                    ("Going first (T)" if first else "Going second (T)", "1st" if first else "2nd", "order", True)]
        if self.mode == "opening":
            specs = [(f"Begin with {o['name']}", o["name"], f"open{o['hand_index']}", True)
                     for o in ga.opening_hand_options(self.gs, self.me, self.store)]
            return specs + [("Start game (ENTER)", "Start", "start", True)]
        if self.mode == "bottom":
            n, k = ga.cards_to_bottom(self.me), len(self.bottom_sel)
            return [(f"Confirm bottom ({k}/{n}) ENTER", f"Confirm {k}/{n}", "confirm", k == n)]
        return [("End turn (Space)", "End turn", "end", True), ("Undo (Z)", "Undo", "undo", bool(self.undo)),
                ("Draw (D)", "Draw", "draw", True), ("Search library (L)", "Search", "search", True),
                ("Help (H)", "Help", "help", True)]

    def settings_specs(self):
        full = ("Window (F11)", "Window") if self.fullscreen else ("Fullscreen (F11)", "Full")
        return [("A-", "A-", "text_down", self.text_scale > TEXT_STEPS[0]),
                ("A+", "A+", "text_up", self.text_scale < TEXT_STEPS[-1]),
                (full[0], full[1], "fullscreen", True)]

    def make_layout(self):
        """Work out where everything goes for the current window size and text size."""
        W, H = self.screen.get_size()
        t = self.text_scale
        u = max(0.6, min(W / BASE_W, H / BASE_H))
        px = {k: max(8, round(v * u * t)) for k, v in FONT_BASE.items()}

        def lh(key, bold=False):
            return get_font(px[key], bold).get_linesize()

        g, m = max(3, round(5 * u)), max(6, round(10 * u))
        L = SimpleNamespace(W=W, H=H, u=u, t=t, px=px, g=g, m=m)
        L.panel_w = panel_w = min(round(300 * u * (0.7 + 0.3 * t)), round(W * 0.42))
        L.board_w = board_w = W - panel_w

        # buttons under the hand (wrapped to more rows, or shortened, when the text is big)
        bfont, pad = get_font(px["btn"]), g * 3
        specs = self.button_specs()
        items, rows = flow_buttons(specs, bfont, pad, g, m, board_w - 2 * m)
        if rows > 1:
            short_items, short_rows = flow_buttons(specs, bfont, pad, g, m, board_w - 2 * m, short=True)
            if short_rows < rows:
                items, rows = short_items, short_rows
        btn_h = lh("btn") + 2 * g + 2
        buttons_h = rows * btn_h + (rows - 1) * g

        # fixed-height strips; the card rows take whatever height is left
        L.ai_title_h = lh("title", True) + g
        L.my_title_h = lh("title", True) + 2 * g
        L.pool_h = lh("body") + g + 2
        L.hint_h = lh("hint") + g
        L.toast_lines = 1 if t < 1.4 else 2
        L.toast_h = lh("toast") * L.toast_lines + 2 * g
        L.status_h = lh("status") + 2 * g
        fixed = (L.ai_title_h + L.my_title_h + L.pool_h + buttons_h + L.hint_h + L.toast_h
                 + L.status_h + 3 + 7 * g)
        me_h = (H - fixed) / (3 + 2 * AI_CARD_RATIO)
        me_h = min(me_h, ((board_w - 2 * m - 6 * g) / 7) / CARD_ASPECT)      # keep a 7-card hand roomy
        L.me_h = int(max(48, me_h))
        L.me_w = int(round(L.me_h * CARD_ASPECT))
        L.sm_h = int(round(L.me_h * AI_CARD_RATIO))
        L.sm_w = int(round(L.sm_h * CARD_ASPECT))

        # rows from the top
        L.ai_title_y = 0
        L.ai_row1_y = L.ai_title_h
        L.ai_row2_y = L.ai_row1_y + L.sm_h + g
        L.divider_y = L.ai_row2_y + L.sm_h + g
        L.my_title_y = L.divider_y + 3 + g
        L.my_row1_y = L.my_title_y + L.my_title_h
        L.my_row2_y = L.my_row1_y + L.me_h + g
        L.pool_y = L.my_row2_y + L.me_h + g
        L.hand_y = L.pool_y + L.pool_h + g
        # bars from the bottom
        L.status_y = H - L.status_h
        L.toast_y = L.status_y - L.toast_h
        L.hint_y = L.toast_y - L.hint_h
        L.buttons_y = L.hint_y - buttons_h
        L.buttons = [(pygame.Rect(x, L.buttons_y + row * (btn_h + g), w, btn_h), label, name, enabled)
                     for row, x, w, label, name, enabled in items]

        # right-hand column: command zone and zone boxes
        bl = get_font(px["boxlabel"], True)
        L.cmd_caption_y = L.my_row1_y + L.me_h + 1
        L.box_top = L.cmd_caption_y + lh("small") + 2
        L.box_h = max(20, (L.pool_y + L.pool_h - L.box_top - g) // 2)
        # a box shows "Label" over "count" when tall enough, else "Label count" on one line (needs to be wider)
        L.box_two_line = L.box_h >= lh("boxlabel", True) + lh("count", True) + 6
        widest = ("Graveyard", "Mulligans") if L.box_two_line else ("Graveyard 999", "Mulligans 99")
        L.box_w = max(int(L.me_w * 0.9), max(bl.size(w)[0] for w in widest) + 4 * g)
        L.rc_w = 2 * L.box_w + g
        L.rc_x = board_w - m - L.rc_w
        L.rows_x, L.rows_w = m, max(60, L.rc_x - 2 * g - m)

        # info panel on the right
        pw = panel_w - 2 * m
        sbtn_h = lh("btn") + 2 * g
        sitems, srows = flow_buttons(self.settings_specs(), bfont, pad, g, board_w + m, pw)
        if srows > 1:
            short_items, short_rows = flow_buttons(self.settings_specs(), bfont, pad, g, board_w + m, pw, short=True)
            if short_rows < srows:
                sitems, srows = short_items, short_rows
        set_h = srows * sbtn_h + (srows - 1) * g
        L.set_y = L.status_y - g - set_h
        L.set_buttons = [(pygame.Rect(x, L.set_y + row * (sbtn_h + g), w, sbtn_h), label, name, enabled)
                         for row, x, w, label, name, enabled in sitems]
        name_block = lh("pname", True) + lh("ptext") + 2 * g
        L.oracle_h = int(min(0.36, 0.20 + 0.10 * (t - 1)) * H)
        log_min = (0.20 + 0.05 * (t - 1)) * H
        prev_max = L.set_y - m - name_block - L.oracle_h - log_min - 2 * g
        prev_h = int(max(60, min(pw / CARD_ASPECT, prev_max)))
        prev_w = int(prev_h * CARD_ASPECT)
        L.prev_rect = pygame.Rect(board_w + (panel_w - prev_w) // 2, max(2, m // 2), prev_w, prev_h)
        L.name_y = L.prev_rect.bottom + g
        L.oracle_y = L.name_y + lh("pname", True) + lh("ptext") + g
        L.log_y = L.oracle_y + L.oracle_h + g
        L.log_bottom = L.set_y - g
        L.lh = lh
        return L

    # ---- drawing helpers -------------------------------------------------

    def text(self, s, x, y, key, colour=WHITE, bold=False, right=False):
        px = self.L.px[key] if isinstance(key, str) else key
        surf = get_font(px, bold).render(s, True, colour)
        rect = surf.get_rect(topright=(x, y)) if right else surf.get_rect(topleft=(x, y))
        self.screen.blit(surf, rect)
        return rect

    def _hit(self, rect, kind, **kw):
        self._new_hits.append(dict(rect=rect, kind=kind, **kw))

    def hit_at(self, pos):
        for h in reversed(self.hits):
            if h["rect"].collidepoint(pos):
                return h
        return None

    def hit_of_kind(self, kind, **match):
        """First hitbox of this kind whose extra fields match (used by tests)."""
        for h in self.hits:
            if h["kind"] == kind and all(h.get(k) == v for k, v in match.items()):
                return h
        return None

    def composed(self, name, small, face, tapped):
        """The Surface for one card as it appears on the table (art or a text placeholder)."""
        L = self.L
        w, h = (L.sm_w, L.sm_h) if small else (L.me_w, L.me_h)
        src = self.get_source(name)
        info = self.store.peek_card(name)
        key = (name, small, face, tapped, src is not None, info is not None)
        surf = self._surf_cache.get(key)
        if surf is not None:
            return surf
        fdata = card_face(info, face) if info else {}
        if src is not None:
            surf = pygame.transform.smoothscale(src, (w, h))
        else:
            surf = pygame.Surface((w, h))
            surf.fill((58, 58, 66))
            pygame.draw.rect(surf, (150, 150, 160), surf.get_rect(), 1)
            font = get_font(w * (0.17 if small else 0.135))
            y = 3
            for line in wrap_text(fdata.get("name") or name, font, w - 6)[:4]:
                surf.blit(font.render(line, True, WHITE), (3, y))
                y += font.get_linesize()
            tl = fdata.get("type_line")
            if tl and not small:
                tf = get_font(w * 0.115)
                surf.blit(tf.render(clip_text(tl, tf, w - 6), True, GREY), (3, h - tf.get_linesize() - 2))
        if face and fdata.get("name") and not small:
            bf = get_font(w * 0.115)
            pygame.draw.rect(surf, (20, 20, 50), (0, 0, w, bf.get_linesize() + 2))
            surf.blit(bf.render(clip_text(fdata["name"], bf, w - 4), True, YELLOW), (2, 1))
        if tapped:
            surf = pygame.transform.rotate(surf, -90)
        self._surf_cache[key] = surf
        return surf

    def draw_card(self, name, x, y, small=False, tapped=False, face=0, selected=False):
        surf = self.composed(name, small, face, tapped)
        rect = surf.get_rect(topleft=(x, y))
        self.screen.blit(surf, rect)
        if selected:
            pygame.draw.rect(self.screen, YELLOW, rect, max(2, round(3 * self.L.u)))
        return rect

    def draw_button(self, rect, label, name, enabled=True):
        hover = enabled and rect.collidepoint(self.mouse)
        colour = (70, 100, 160) if hover else ((45, 60, 95) if enabled else (50, 50, 55))
        pygame.draw.rect(self.screen, colour, rect, border_radius=5)
        pygame.draw.rect(self.screen, (150, 160, 190) if enabled else (80, 80, 85), rect, 1, border_radius=5)
        font = self.font("btn")
        surf = font.render(clip_text(label, font, rect.w - 4), True, WHITE if enabled else (120, 120, 125))
        self.screen.blit(surf, surf.get_rect(center=rect.center))
        if enabled:
            self._hit(rect, "btn", name=name)

    def _perm_rows(self, player):
        lands = [(i, e) for i, e in enumerate(player.battlefield) if e.get("is_land")]
        others = [(i, e) for i, e in enumerate(player.battlefield) if not e.get("is_land")]
        return others, lands

    # ---- drawing the table -----------------------------------------------

    def draw_ai_area(self):
        L, ai = self.L, self.ai
        pygame.draw.rect(self.screen, AI_GREEN, (0, 0, L.board_w, L.divider_y))
        title = fit_text(
            [f"{ai.name}   Life {ai.life}   Hand {len(ai.hand)}   Library {len(ai.library)}   "
             f"Graveyard {len(ai.graveyard)}   Exile {len(ai.exile)}",
             f"{ai.name}  Life {ai.life}  Hand {len(ai.hand)}  Lib {len(ai.library)}  GY {len(ai.graveyard)}",
             f"{ai.name}  Life {ai.life}"], self.font("title", True), L.board_w - 2 * L.m)
        self.text(title, L.m, L.ai_title_y + L.g // 2, "title", WHITE, True)
        others, lands = self._perm_rows(ai)
        for row, y in ((others, L.ai_row1_y), (lands, L.ai_row2_y)):
            widths = [L.sm_h if e["tapped"] else L.sm_w for _, e in row]
            for (i, e), x in zip(row, layout_row(widths, L.rows_x, L.rows_w, L.g)):
                ry = y + (L.sm_h - L.sm_w) if e["tapped"] else y
                rect = self.draw_card(e["name"], x, ry, small=True, tapped=e["tapped"], face=e.get("face", 0))
                self._hit(rect, "ai_perm", idx=i, card=(e["name"], e.get("face", 0)))
        for i, name in enumerate(ai.command_zone):
            rect = self.draw_card(name, L.rc_x + i * (L.sm_w + L.g), L.ai_row1_y, small=True)
            self._hit(rect, "ai_cmd", idx=i, card=(name, 0))
        self.text("Command zone", L.rc_x, L.ai_row1_y + L.sm_h + 1, "small", GREY)

    def draw_my_title(self):
        L, me = self.L, self.me
        pygame.draw.line(self.screen, (10, 50, 24), (0, L.divider_y), (L.board_w, L.divider_y), 3)
        y = L.my_title_y + L.g // 2
        r = self.text(f"{me.name}   Life {me.life}", L.m, y, "title", WHITE, True)
        size = L.lh("title", True) + 4
        minus = pygame.Rect(r.right + 2 * L.g, y - 1, size, size)
        plus = pygame.Rect(minus.right + L.g, y - 1, size, size)
        for rect, label, delta in ((minus, "-", -1), (plus, "+", 1)):
            pygame.draw.rect(self.screen, (45, 60, 95), rect, border_radius=4)
            pygame.draw.rect(self.screen, (150, 160, 190), rect, 1, border_radius=4)
            surf = get_font(L.px["title"], True).render(label, True, WHITE)
            self.screen.blit(surf, surf.get_rect(center=rect.center))
            self._hit(rect, "life", delta=delta)
        turn = f"Turn {self.gs.turn_number}" if self.mode == "play" else "Opening hand"
        tr = self.text(turn, L.board_w - L.m, y, "title", YELLOW, True, right=True)
        counts = fit_text([f"Hand {len(me.hand)}   Library {len(me.library)}", f"H {len(me.hand)}  L {len(me.library)}"],
                          self.font("body"), tr.left - plus.right - 4 * L.g)
        if self.font("body").size(counts)[0] <= tr.left - plus.right - 4 * L.g:
            self.text(counts, plus.right + 2 * L.g, y + 2, "body", GREY)

    def draw_my_rows(self):
        L, me = self.L, self.me
        others, lands = self._perm_rows(me)
        for row, y, label in ((others, L.my_row1_y, "Permanents"), (lands, L.my_row2_y, "Lands")):
            if not row:
                self.text(label, L.rows_x + 4, y + 4, "ptext", (90, 140, 100))
            widths = [L.me_h if e["tapped"] else L.me_w for _, e in row]
            for (i, e), x in zip(row, layout_row(widths, L.rows_x, L.rows_w, L.g)):
                ry = y + (L.me_h - L.me_w) if e["tapped"] else y
                rect = self.draw_card(e["name"], x, ry, tapped=e["tapped"], face=e.get("face", 0))
                self._hit(rect, "perm", idx=i, card=(e["name"], e.get("face", 0)))
                self.draw_badge(e, rect)

    def draw_badge(self, e, rect):
        """A small label on a permanent that needs attention: FETCH (click to crack) or CHOOSE."""
        if e.get("awaiting"):
            label, colour = "CHOOSE", (200, 120, 30)
        elif not e["tapped"] and e.get("is_land") and ga.fetch_spec(self.store, e):
            label, colour = "FETCH", (50, 110, 190)
        else:
            return
        font = self.font("small", True)
        text = font.render(label, True, WHITE)
        pad = max(2, self.L.g // 2)
        box = pygame.Rect(0, 0, min(text.get_width() + 2 * pad, rect.w - 2 * pad), text.get_height() + pad)
        box.bottomleft = (rect.x + pad, rect.bottom - pad)
        pygame.draw.rect(self.screen, colour, box, border_radius=4)
        pygame.draw.rect(self.screen, WHITE, box, 1, border_radius=4)
        self.screen.blit(text, text.get_rect(center=box.center), area=pygame.Rect(0, 0, box.w, text.get_height()))

    def draw_pool_bar(self):
        L, me = self.L, self.me
        y = L.pool_y
        label = self.text("Mana pool:", L.m + 4, y + 1, "body", GREY)
        r = max(7, round(L.lh("body") * 0.55))
        x, total = label.right + 2 * L.g, 0
        for c in COLOR_ORDER:
            for _ in range(me.mana_pool.pool.get(c, 0)):
                if total >= 24:
                    break
                cy = y + L.pool_h // 2
                pygame.draw.circle(self.screen, MANA_COLOURS[c], (x + r, cy), r)
                pygame.draw.circle(self.screen, (10, 10, 10), (x + r, cy), r, 1)
                surf = get_font(r * 1.2, True).render(c, True, WHITE if c == "B" else (10, 10, 10))
                self.screen.blit(surf, surf.get_rect(center=(x + r, cy)))
                x += 2 * r + 4
                total += 1
        if not total:
            self.text("empty", x, y + 1, "body", (110, 150, 120))
            x += self.font("body").size("empty")[0]
        n_lands = sum(1 for e in me.battlefield if e.get("is_land"))
        room = L.rows_x + L.rows_w - x - 3 * L.g
        stats = fit_text([f"Land drop {me.lands_played_this_turn}/1   Lands {n_lands}   Permanents {len(me.battlefield) - n_lands}",
                          f"Land drop {me.lands_played_this_turn}/1   Lands {n_lands}",
                          f"Land drop {me.lands_played_this_turn}/1"], self.font("body"), room)
        if self.font("body").size(stats)[0] <= room:
            self.text(stats, L.rows_x + L.rows_w, y + 1, "body", GREY, right=True)

    def draw_hand(self):
        L, me = self.L, self.me
        xs = layout_row([L.me_w] * len(me.hand), L.m, L.board_w - 2 * L.m, L.g)
        lift = max(4, round(8 * L.u))
        for i, (name, x) in enumerate(zip(me.hand, xs)):
            selected = self.mode == "bottom" and i in self.bottom_sel
            rect = self.draw_card(name, x, L.hand_y - (lift if selected else 0), selected=selected)
            self._hit(rect, "hand", idx=i, card=(name, 0))
        if not me.hand:
            self.text("Hand is empty", L.m + 4, L.hand_y + 4, "ptext", (90, 140, 100))

    def draw_right_column(self):
        L, me = self.L, self.me
        n = len(me.command_zone)
        for i, name in enumerate(me.command_zone):
            x = L.rc_x if n == 1 else L.rc_x + i * (L.rc_w - L.me_w)
            rect = self.draw_card(name, x, L.my_row1_y)
            self._hit(rect, "cmd", idx=i, card=(name, 0))
        if me.command_zone:
            name = me.command_zone[0]
            info = self.store.peek_card(name)
            casts = me.commander_casts.get(name, 0)
            if info:
                cost = parse_mana_cost(card_face(info, 0).get("mana_cost"))
                cost["generic"] = cost.get("generic", 0) + 2 * casts
                base = f"next cast {ga.cost_text(cost)}"
                cands = [base + (f"  (cast {casts}x)" if casts else ""), base, ga.cost_text(cost)]
            else:
                cands = ["loading card data...", "loading..."]
        else:
            cands = ["Command zone empty", "No commander"]
        self.text(fit_text(cands, self.font("small"), L.rc_w), L.rc_x, L.cmd_caption_y, "small", GREY)

        boxes = (("Library", L.rc_x, L.box_top, "library", len(me.library)),
                 ("Graveyard", L.rc_x + L.box_w + L.g, L.box_top, "graveyard", len(me.graveyard)),
                 ("Exile", L.rc_x, L.box_top + L.box_h + L.g, "exile", len(me.exile)),
                 ("Mulligans", L.rc_x + L.box_w + L.g, L.box_top + L.box_h + L.g, None, me.mulligans))
        for label, x, y, zone, count in boxes:
            rect = pygame.Rect(x, y, L.box_w, L.box_h)
            live = zone is not None
            fill = (40, 40, 85) if zone == "library" else (28, 60, 40)
            pygame.draw.rect(self.screen, fill, rect, border_radius=6)
            pygame.draw.rect(self.screen, (140, 170, 150) if live else (60, 100, 70), rect, 1, border_radius=6)
            lab_font, cnt_font = self.font("boxlabel", True), self.font("count", True)
            colour = WHITE if live else GREY
            if L.box_two_line:
                self.text(clip_text(label, lab_font, rect.w - 8), x + 5, y + 3, "boxlabel", colour, True)
                self.text(str(count), x + 5, y + 3 + lab_font.get_linesize(), "count", YELLOW if live else GREY, True)
            else:
                line = clip_text(f"{label} {count}", lab_font, rect.w - 8)
                self.text(line, x + 5, y + (rect.h - lab_font.get_linesize()) // 2, "boxlabel",
                          YELLOW if live else GREY, True)
            if live:
                self._hit(rect, "box", zone=zone)

    def draw_buttons(self):
        for rect, label, name, enabled in self.L.buttons:
            self.draw_button(rect, label, name, enabled)

    def hint_text(self):
        me = self.me
        if self.mode == "mulligan":
            n = ga.cards_to_bottom(me)
            nxt = max(0, me.mulligans + 1 - ga.FREE_MULLIGANS)
            return (f"Opening hand ({me.mulligans} mulligan{'s' if me.mulligans != 1 else ''}). "
                    f"Keeping puts {n} on the bottom; another mulligan would put {nxt}.  {self.order_text()}")
        if self.mode == "bottom":
            n = ga.cards_to_bottom(me)
            return f"Click {n} card{'s' if n != 1 else ''} in your hand to put on the bottom, then press ENTER."
        if self.mode == "opening":
            names = ", ".join(o["name"] for o in ga.opening_hand_options(self.gs, me, self.store))
            return f"Opening-hand effects you may use before the game starts: {names}.  ENTER starts the game."
        return "Left-click: play / tap / cast    Right-click: menu    Space: end turn    Z: undo    H: help"

    def draw_panel(self):
        L = self.L
        pygame.draw.rect(self.screen, PANEL_BG, (L.board_w, 0, L.panel_w, L.H))
        pr, tx, pw = L.prev_rect, L.board_w + L.m, L.panel_w - 2 * L.m
        surf = self.preview_surface(self.preview_card[0], pr.w, pr.h) if self.preview_card else None
        if surf:
            self.screen.blit(surf, pr)
        else:
            pygame.draw.rect(self.screen, (40, 40, 48), pr, 1)
            hint = "Hover a card"
            f = self.font("ptext")
            self.screen.blit(f.render(clip_text(hint, f, pr.w - 8), True, GREY),
                             (pr.x + 8, pr.centery - f.get_linesize() // 2))
        if self.preview_card:
            name, face = self.preview_card
            info = self.store.peek_card(name)
            fd = card_face(info, face) if info else {}
            pt = f"{fd['power']}/{fd['toughness']}" if fd.get("power") is not None else ""
            ptw = self.font("pname", True).size(pt)[0] + L.g if pt else 0
            self.text(clip_text(fd.get("name") or name, self.font("pname", True), pw - ptw), tx, L.name_y, "pname", WHITE, True)
            if pt:
                self.text(pt, tx + pw, L.name_y, "pname", YELLOW, True, right=True)
            self.text(clip_text(fd.get("type_line") or "", self.font("ptext"), pw),
                      tx, L.name_y + L.lh("pname", True), "ptext", GREY)
            font = self.font("ptext")
            lines = wrap_text(fd.get("oracle_text") or "", font, pw)
            room = max(0, L.oracle_h // font.get_linesize())
            if len(lines) > room:
                lines = lines[:room]
                if lines:
                    lines[-1] = clip_text(lines[-1] + " ...", font, pw)
            for i, line in enumerate(lines):
                self.screen.blit(font.render(line, True, (225, 225, 230)), (tx, L.oracle_y + i * font.get_linesize()))
        pygame.draw.line(self.screen, (60, 60, 70), (tx, L.log_y - L.g // 2), (tx + pw, L.log_y - L.g // 2))
        self.text("Game log", tx, L.log_y, "ptext", YELLOW, True)
        lf = self.font("log")
        lines = []
        for entry in self.gs.log[-30:]:
            lines.extend(wrap_text(entry, lf, pw))
        top = L.log_y + L.lh("ptext", True) + 2
        room = max(0, (L.log_bottom - top) // lf.get_linesize())
        for i, line in enumerate(lines[-room:] if room else []):
            self.screen.blit(lf.render(line, True, (200, 200, 205)), (tx, top + i * lf.get_linesize()))
        for rect, label, name, enabled in L.set_buttons:
            self.draw_button(rect, label, name, enabled)

    def draw_bottom_bars(self):
        L = self.L
        f = self.font("hint")
        self.text(clip_text(self.hint_text(), f, L.board_w - 2 * L.m), L.m, L.hint_y + L.g // 2, "hint", (215, 235, 215))
        message, kind = self.toast
        pygame.draw.rect(self.screen, TOAST_COLOURS[kind], (0, L.toast_y, L.board_w, L.toast_h))
        tf = self.font("toast")
        lines = wrap_text(message, tf, L.board_w - 2 * L.m)
        if len(lines) > L.toast_lines:
            lines = lines[:L.toast_lines]
            lines[-1] = clip_text(lines[-1] + " ...", tf, L.board_w - 2 * L.m)
        for i, line in enumerate(lines):
            self.screen.blit(tf.render(line, True, WHITE), (L.m, L.toast_y + L.g + i * tf.get_linesize()))

        failing = bool(self._failed_until or self.bad_images)
        if failing and self.image_error:
            colour, message = (120, 20, 20), f"Card art problem: {self.image_error}"
        elif not self.data_ready.is_set():
            colour, message = (30, 50, 90), self.stage
        elif self._requested:
            colour, message = (30, 50, 90), f"Loading card art... ({len(self._requested)} to go)"
        elif self.rules_note:
            colour, message = (95, 70, 20), self.rules_note
        else:
            colour, message = None, ""
        if colour:
            pygame.draw.rect(self.screen, colour, (0, L.status_y, L.W, L.status_h))
            sf = self.font("status")
            self.screen.blit(sf.render(clip_text(message, sf, L.W - 2 * L.m), True, WHITE), (L.m, L.status_y + L.g))

    # ---- overlays ----------------------------------------------------------

    def viewer_row_data(self):
        """Rows for the open zone list: (index_in_zone, card_name, count)."""
        v, me = self.viewer, self.me
        if v["zone"] == "library":
            idxs = ga.fetch_candidates(me, v["spec"], self.store) if v["mode"] == "fetch" else range(len(me.library))
            groups = {}
            for i in idxs:
                name = me.library[i]
                if name in groups:
                    groups[name][1] += 1
                else:
                    groups[name] = [i, 1]
            return [(i, name, n) for name, (i, n) in sorted(groups.items(), key=lambda kv: kv[0].lower())]
        cards = ga.zone_list(me, v["zone"])
        return [(i, cards[i], 1) for i in reversed(range(len(cards)))]      # newest first

    def draw_viewer(self):
        L, v = self.L, self.viewer
        vw = min(L.board_w - 2 * L.m, round(660 * L.u * (0.6 + 0.4 * L.t)))
        vh = round(L.H * 0.84)
        panel = pygame.Rect((L.board_w - vw) // 2, (L.H - vh) // 2, vw, vh)
        self.viewer_rect = panel
        shade = pygame.Surface((L.board_w, L.H), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 150))
        self.screen.blit(shade, (0, 0))
        pygame.draw.rect(self.screen, (24, 26, 34), panel, border_radius=8)
        pygame.draw.rect(self.screen, (150, 155, 175), panel, 1, border_radius=8)
        rows = self.viewer_row_data()
        what = (v.get("spec") or {}).get("what") or "land"
        title = {"fetch": f"Fetchland: choose a {what}", "search": "Search library (A-Z)"}.get(v["mode"])
        title = title or v["zone"].capitalize()
        head_h = L.lh("viewhead", True) + 2 * L.g
        self.text(clip_text(f"{title}  -  {sum(r[2] for r in rows)} card(s)", self.font("viewhead", True), vw - 2 * L.m),
                  panel.x + L.m, panel.y + L.g, "viewhead", YELLOW, True)
        foot = ("Click a card to move it.  Closing (ESC) shuffles the library."
                if v["zone"] == "library" and v["mode"] != "fetch"
                else "Click a card to fetch it.  ESC = fail to find (library is shuffled)." if v["mode"] == "fetch"
                else "Click a card to move it.  ESC closes.  Newest first.")
        sf = self.font("small")
        foot_y = panel.bottom - sf.get_linesize() - L.g
        self.text(clip_text(foot, sf, vw - 2 * L.m), panel.x + L.m, foot_y, "small", GREY)
        row_h, top = L.lh("view") + L.g + 2, panel.y + head_h
        visible = max(1, (foot_y - L.g - top) // row_h)
        v["scroll"] = max(0, min(v["scroll"], max(0, len(rows) - visible)))
        self.viewer_rows = []
        if not rows:
            msg = "No matching land found in the library." if v["mode"] == "fetch" else "(nothing here)"
            self.text(msg, panel.x + L.m, top + 4, "view", GREY)
        vf = self.font("view")
        for k, (idx, name, count) in enumerate(rows[v["scroll"]:v["scroll"] + visible]):
            rect = pygame.Rect(panel.x + L.g, top + k * row_h, vw - 2 * L.g, row_h)
            if rect.collidepoint(self.mouse):
                pygame.draw.rect(self.screen, (60, 90, 150), rect, border_radius=3)
                self.preview_card = (name, 0)
            info = self.store.peek_card(name)
            type_w = int(rect.w * 0.34)
            label = name + (f"  x{count}" if count > 1 else "")
            self.screen.blit(vf.render(clip_text(label, vf, rect.w - type_w - 3 * L.g), True, WHITE),
                             (rect.x + L.g, rect.y + (row_h - vf.get_linesize()) // 2))
            if info:
                tf = self.font("small")
                tl = clip_text(card_face(info, 0).get("type_line") or "", tf, type_w)
                self.screen.blit(tf.render(tl, True, GREY), (rect.right - L.g - tf.size(tl)[0],
                                                              rect.y + (row_h - tf.get_linesize()) // 2))
            self.viewer_rows.append((rect, idx, name))
        if len(rows) > visible:
            note = f"{v['scroll'] + 1}-{min(len(rows), v['scroll'] + visible)} of {len(rows)}  (mouse wheel)"
            self.text(note, panel.right - L.m, foot_y, "small", GREY, right=True)

    def draw_help(self):
        L = self.L
        shade = pygame.Surface((L.W, L.H), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 190))
        self.screen.blit(shade, (0, 0))
        # use the biggest text that still fits on the screen
        s = L.t
        while True:
            fpx = max(8, round(FONT_BASE["help"] * L.u * s))
            font, bold = get_font(fpx), get_font(fpx, True)
            key_w = max(bold.size(k)[0] for k, _ in HELP_ENTRIES)
            pw = min(L.W - 2 * L.m, key_w + round(620 * L.u * s) + 5 * L.m)
            desc_w = pw - key_w - 4 * L.m
            lay = [(k, wrap_text(d, font, desc_w)) for k, d in HELP_ENTRIES]
            line_h = font.get_linesize()
            body_h = sum(max(1, len(lines)) * line_h + L.g for _, lines in lay)
            total_h = body_h + 2 * line_h + 4 * L.m
            if total_h <= L.H - 2 * L.m or s <= 0.6:
                break
            s -= 0.1
        panel = pygame.Rect((L.W - pw) // 2, max(L.m, (L.H - total_h) // 2), pw, total_h)
        pygame.draw.rect(self.screen, (24, 26, 34), panel, border_radius=8)
        pygame.draw.rect(self.screen, (150, 155, 175), panel, 1, border_radius=8)
        y = panel.y + 2 * L.m
        self.screen.blit(bold.render("CONTROLS", True, YELLOW), (panel.x + 2 * L.m, y))
        y += line_h + L.g
        for k, lines in lay:
            self.screen.blit(bold.render(k, True, WHITE), (panel.x + 2 * L.m, y))
            for i, line in enumerate(lines):
                self.screen.blit(font.render(line, True, (215, 215, 220)), (panel.x + 2 * L.m + key_w + 2 * L.m, y + i * line_h))
            y += max(1, len(lines)) * line_h + L.g
        self.screen.blit(font.render("Click or press any key to close.", True, GREY), (panel.x + 2 * L.m, y + L.g))

    def render(self):
        self.screen = pygame.display.get_surface()
        if not self.fullscreen:
            self.windowed_size = self.screen.get_size()
        self._collect_art_results()
        self.L = L = self.make_layout()
        key = (L.W, L.H, L.t)
        if key != self._layout_key:          # a menu built for the old size would be misplaced
            self._layout_key = key
            self.menu = None
        dims = (L.me_w, L.me_h, L.sm_w, L.sm_h)
        if dims != self._card_dims:          # window or text size changed: rescale cards from the source art
            self._surf_cache.clear()
            self._card_dims = dims
        hover = None if (self.menu or self.viewer or self.help_open) else self.hit_at(self.mouse)
        if hover and hover.get("card"):
            self.preview_card = hover["card"]
        self._new_hits = []
        self.screen.fill(TABLE_GREEN)
        self.draw_ai_area()
        self.draw_my_title()
        self.draw_my_rows()
        self.draw_pool_bar()
        self.draw_hand()
        self.draw_right_column()
        self.draw_buttons()
        self.draw_bottom_bars()
        if self.viewer:
            self.draw_viewer()
        self.draw_panel()
        if self.menu:
            self.menu.draw(self.screen, self.mouse)
        if self.help_open:
            self.draw_help()
        self.hits = self._new_hits
        pygame.display.flip()
    # ---- undo & action wrapper ---------------------------------------------

    def act(self, fn, push=True):
        """Run an action returning (ok, message). A snapshot is kept for Undo only if it succeeded."""
        snap = copy.deepcopy(self.gs)
        ok, msg = fn()
        if ok:
            if push:
                self.undo.append(snap)
            self.say(msg, "ok")
            self.check_prompts()
        else:
            self.say(msg, "fail")
        return ok

    def do_undo(self):
        if not self.undo:
            self.say("Nothing to undo.", "fail")
            return
        self.gs = self.undo.pop()
        self.menu = self.viewer = None
        self.say("Undid the last action.", "info")

    def open_menu(self, items, pos, title=None):
        self.menu = Menu(items, pos, (self.L.W, self.L.H), self.L.px["menu"], title=title)

    # ---- events ---------------------------------------------------------------

    def handle_event(self, ev):
        """Process one pygame event. Returns False when the window should close."""
        if ev.type == pygame.QUIT:
            return False
        if ev.type == pygame.MOUSEMOTION:
            self.mouse = ev.pos
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            self.mouse = ev.pos
            if ev.button in (1, 3):
                self.on_click(ev.pos, ev.button)
        elif ev.type == pygame.MOUSEWHEEL:
            if self.viewer:
                self.viewer["scroll"] -= ev.y * 3
        elif ev.type == pygame.KEYDOWN:
            self.on_key(ev.key, getattr(ev, "mod", 0))
        return True

    def on_click(self, pos, button):
        if self.help_open:
            self.help_open = False
            return
        if self.menu:                       # any click closes the menu; a click on an item runs it
            menu, self.menu = self.menu, None
            cb = menu.callback_at(pos) if button == 1 else None
            if cb:
                cb()
            return
        if self.viewer:
            self.click_viewer(pos)
            return
        hit = self.hit_at(pos)
        if not hit:
            return
        kind = hit["kind"]
        if kind == "btn" and button == 1:
            self.press_button(hit["name"])
        elif self.mode == "bottom":
            if kind == "hand" and button == 1:
                idx = hit["idx"]
                if idx in self.bottom_sel:
                    self.bottom_sel.discard(idx)
                elif len(self.bottom_sel) < ga.cards_to_bottom(self.me):
                    self.bottom_sel.add(idx)
        elif self.mode != "play":
            return
        elif kind == "hand":
            self.play_hand(hit["idx"]) if button == 1 else self.hand_menu(hit["idx"], pos)
        elif kind == "perm":
            self.click_perm(hit["idx"], pos) if button == 1 else self.perm_menu(hit["idx"], pos)
        elif kind == "cmd":
            self.cast_commander(hit["idx"]) if button == 1 else self.cmd_menu(hit["idx"], pos)
        elif kind == "box":
            zone = hit["zone"]
            if zone == "library" and button == 1:
                self.draw_one()
            elif zone == "library":
                self.library_menu(pos)
            else:
                self.open_viewer(zone)
        elif kind == "life":
            delta = hit["delta"] * (5 if button == 3 else 1)
            self.act(lambda: (ga.adjust_life(self.gs, self.me, delta), (True, f"Life {self.me.life}"))[1])

    def on_key(self, key, mod=0):
        # window and text-size keys work everywhere, even with a menu or list open
        if key == pygame.K_F11 or (key == pygame.K_RETURN and mod & pygame.KMOD_ALT):
            self.toggle_fullscreen()
            return
        if key == pygame.K_0 and mod & pygame.KMOD_CTRL:
            self.change_text_scale(0)
            return
        if key in BIGGER_KEYS:
            self.change_text_scale(+1)
            return
        if key in SMALLER_KEYS:
            self.change_text_scale(-1)
            return
        if self.help_open:
            self.help_open = False
            return
        if key == pygame.K_h:
            self.help_open = True
            return
        if self.menu:
            if key == pygame.K_ESCAPE:
                self.menu = None
            return
        if self.viewer:
            if key == pygame.K_ESCAPE:
                self.close_viewer()
            elif key in (pygame.K_PAGEDOWN, pygame.K_DOWN):
                self.viewer["scroll"] += 10 if key == pygame.K_PAGEDOWN else 1
            elif key in (pygame.K_PAGEUP, pygame.K_UP):
                self.viewer["scroll"] -= 10 if key == pygame.K_PAGEUP else 1
            return
        if key == pygame.K_ESCAPE and self.fullscreen and not (self.mode == "bottom" and self.bottom_sel):
            self.toggle_fullscreen()
            return
        if self.mode == "mulligan":
            if key == pygame.K_k:
                self.keep_hand()
            elif key == pygame.K_m:
                self.do_mulligan()
            elif key == pygame.K_t:
                self.toggle_order()
        elif self.mode == "opening":
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.start_game()
        elif self.mode == "bottom":
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.confirm_bottom()
            elif key == pygame.K_ESCAPE:
                self.bottom_sel.clear()
        elif key == pygame.K_SPACE:
            self.end_turn()
        elif key == pygame.K_d:
            self.draw_one()
        elif key == pygame.K_z:
            self.do_undo()
        elif key == pygame.K_l:
            self.open_viewer("library", mode="search")
        elif key == pygame.K_g:
            self.open_viewer("graveyard")
        elif key == pygame.K_e:
            self.open_viewer("exile")
        elif key in MANUAL_KEYS:
            colour = MANUAL_KEYS[key]
            self.act(lambda: (ga.add_mana(self.gs, self.me, colour), (True, f"Added {{{colour}}} to your pool"))[1])
        elif key == pygame.K_0:
            self.act(lambda: (ga.empty_mana_pool(self.me), (True, "Mana pool emptied"))[1])

    def press_button(self, name):
        if name == "keep":
            self.keep_hand()
        elif name == "mulligan":
            self.do_mulligan()
        elif name == "confirm":
            self.confirm_bottom()
        elif name == "order":
            self.toggle_order()
        elif name == "start":
            self.start_game()
        elif name.startswith("open") and name[4:].isdigit():
            self.use_opening(int(name[4:]))
        elif name == "end":
            self.end_turn()
        elif name == "undo":
            self.do_undo()
        elif name == "draw":
            self.draw_one()
        elif name == "search":
            self.open_viewer("library", mode="search")
        elif name == "help":
            self.help_open = True
        elif name == "text_down":
            self.change_text_scale(-1)
        elif name == "text_up":
            self.change_text_scale(+1)
        elif name == "fullscreen":
            self.toggle_fullscreen()

    # ---- opening hand ---------------------------------------------------------

    def keep_hand(self):
        if ga.cards_to_bottom(self.me) == 0:
            self.begin_play()
        else:
            self.mode = "bottom"
            self.bottom_sel = set()
            self.say(f"Pick {ga.cards_to_bottom(self.me)} card(s) to put on the bottom.", "info")

    def do_mulligan(self):
        if self.me.mulligans + 1 - ga.FREE_MULLIGANS >= ga.HAND_SIZE:
            self.say("You can't mulligan any further.", "fail")
            return
        n = ga.take_mulligan(self.gs, self.me)
        self.say(f"Mulligan #{self.me.mulligans}: new seven. Keeping puts {n} on the bottom.", "info")

    def confirm_bottom(self):
        n = ga.cards_to_bottom(self.me)
        if len(self.bottom_sel) != n:
            self.say(f"Select exactly {n} card(s) first.", "fail")
            return
        ga.bottom_cards(self.gs, self.me, sorted(self.bottom_sel))
        self.bottom_sel = set()
        self.begin_play()

    def order_text(self):
        return "You go first." if self.gs.first_player_index == 0 else "You go second."

    def toggle_order(self):
        self.gs.first_player_index = (self.gs.first_player_index + 1) % len(self.gs.players)
        self.say(self.order_text() + ("  (Gemstone Caverns needs you to go second.)"
                                      if self.gs.first_player_index else ""), "info")

    def begin_play(self):
        """Mulligans are over: offer the opening-hand effects (Gemstone Caverns, Leylines), then start."""
        if ga.opening_hand_options(self.gs, self.me, self.store):
            self.mode = "opening"
            self.say("You may begin the game with some cards on the battlefield. Pick one, or start the game.", "info")
        else:
            self.start_game()

    def use_opening(self, hand_index):
        opt = next((o for o in ga.opening_hand_options(self.gs, self.me, self.store)
                    if o["hand_index"] == hand_index), None)
        if opt is None:
            return
        if opt["manual"]:
            self.say(f"{opt['name']}: this opening-hand effect isn't automated. Start the game and do it by hand "
                     "(right-click menus).", "fail")
            return
        others = []
        seen = set()
        for i, n in enumerate(self.me.hand):
            if i != hand_index and n not in seen:
                seen.add(n)
                others.append((i, n))
        if opt["exile"] and others:
            items = [(f"Exile {n}", lambda i=i: self.finish_opening(hand_index, [i])) for i, n in others]
            self.open_menu(items, self.mouse, f"{opt['name']}: exile a card from your hand")
        else:
            self.finish_opening(hand_index, [])

    def finish_opening(self, hand_index, exile):
        ok, msg = ga.begin_with_on_battlefield(self.gs, self.me, hand_index, self.store, exile)
        self.say(msg, "info" if ok else "fail")
        if ok and not ga.opening_hand_options(self.gs, self.me, self.store):
            self.start_game()

    def start_game(self):
        self.mode = "play"
        ga.begin_game(self.gs, self.store, 0)
        self.undo.clear()
        if self.gs.first_player_index == 0:
            self.say("Game on. Your turn 1 (no draw for the player who goes first).", "info")
        else:
            self.say("Game on. The other player went first, so you drew for your turn 1.", "info")

    # ---- playing cards ---------------------------------------------------------

    def play_hand(self, idx, face=0, tapped=None, free=False):
        before = len(self.me.battlefield)
        if self.act(lambda: ga.play_from_hand(self.gs, self.me, idx, self.store,
                                              face=face, tapped=tapped, free=free)):
            bf = self.me.battlefield
            if len(bf) > before and self.menu is None:
                e = bf[-1]
                if e.get("is_land") and not e["tapped"] and not e.get("awaiting") and ga.fetch_spec(self.store, e):
                    self.offer_crack(e)

    def offer_crack(self, e):
        """A fetchland just entered: ask whether to crack it now."""
        spec = ga.fetch_spec(self.store, e)
        if not spec:
            return
        life = f"pay {spec['life']} life, then " if spec["life"] else ""
        found = ga.fetch_candidates(self.me, spec, self.store)
        tail = "" if found else f"  (no {spec['what']} left in your library)"
        items = [(f"Crack it now: {life}choose a {spec['what']}{tail}", lambda: self.crack_fetch(e)),
                 ("Keep it (click it later to crack)", lambda: self.say(f"{e['name']} stays on the battlefield. "
                                                                        "Click it whenever you want to crack it.", "info"))]
        self.open_menu(items, self.mouse, e["name"])

    def check_prompts(self):
        """After an action: does a permanent that just entered need a choice (imprint, discard)?"""
        if self.mode != "play":
            return
        e = ga.awaiting_entry(self.me)
        if e:
            self.offer_prompt(e)

    def offer_prompt(self, e):
        prompt = e.get("awaiting")
        if not prompt:
            return
        seen, items = set(), []
        info = self.store.peek_card(e["name"])
        shown = card_face(info, e.get("face", 0)).get("name") or e["name"] if info else e["name"]
        if prompt["kind"] == "pay_or_tap":
            life = prompt["life"]
            items = [(f"Pay {life} life: {shown} enters untapped", lambda: self.answer_prompt(e, True)),
                     ("Don't pay: it enters tapped", lambda: self.answer_prompt(e, False))]
            self.open_menu(items, self.mouse, f"{shown}: pay {life} life?")
            return
        for hand_index, name in ga.prompt_choices(self.me, prompt, self.store):
            if name in seen:
                continue
            seen.add(name)
            verb = "Exile" if prompt["kind"] == "imprint" else "Discard"
            items.append((f"{verb} {name}", lambda i=hand_index: self.answer_prompt(e, i)))
        if prompt["kind"] == "imprint":
            items.append(("Don't imprint anything", lambda: self.answer_prompt(e, None)))
            title = f"{e['name']}: exile a card from your hand?"
        else:
            items.append((f"Don't discard: {e['name']} goes to the graveyard", lambda: self.answer_prompt(e, None)))
            title = f"{e['name']}: discard a land or lose it"
        self.open_menu(items, self.mouse, title)

    def answer_prompt(self, e, hand_index):
        # push=False: Undo rolls back the play and the choice together
        self.act(lambda: ga.resolve_prompt(self.gs, self.me, e, hand_index, self.store), push=False)

    def cast_commander(self, idx, free=False):
        self.act(lambda: ga.cast_commander(self.gs, self.me, idx, self.store, free=free))

    def move(self, src, idx, dst, **kw):
        self.act(lambda: ga.move_card(self.gs, self.me, src, idx, dst, self.store, **kw))

    def draw_one(self):
        self.act(lambda: ga.draw_cards(self.gs, self.me, 1))

    def end_turn(self):
        pending = ga.awaiting_entry(self.me)
        if pending:
            self.say(f"{pending['name']} is waiting for your choice first.", "fail")
            self.offer_prompt(pending)
            return
        before = len(self.me.hand)

        def go():
            ga.end_turn(self.gs, self.store, 0)
            drawn = self.me.hand[-1] if len(self.me.hand) > before else None
            extra = f", drew {drawn}" if drawn else ""
            over = f" You have {len(self.me.hand)} cards: discard down to 7." if len(self.me.hand) > ga.HAND_SIZE else ""
            return True, f"Turn {self.gs.turn_number}{extra}.{over}"
        self.act(go)

    # ---- tapping permanents -------------------------------------------------

    @staticmethod
    def _tap_result(result):
        return result[0] == "done", result[1]

    def click_perm(self, idx, pos):
        me = self.me
        if not 0 <= idx < len(me.battlefield):
            return
        e = me.battlefield[idx]
        if e.get("awaiting"):
            self.offer_prompt(e)
            return
        if ga.fetch_spec(self.store, e) and not e["tapped"]:
            self.crack_fetch(e)
            return
        opts, manual, reason = ga.tap_options(self.gs, me, e, self.store)
        if reason == "already tapped":
            self.act(lambda: ga.toggle_tapped(self.gs, me, e))
        elif reason == "summoning sick":
            self.say(f"{e['name']} is summoning sick (right-click > Tap / untap to override).", "fail")
        elif reason:
            self.say(f"{e['name']}: {reason}.", "fail")
        elif opts or manual:
            self.tap_mana(e, pos)
        else:
            self.act(lambda: ga.toggle_tapped(self.gs, me, e))       # not a mana source: just tap it

    def tap_mana(self, e, pos):
        snap = copy.deepcopy(self.gs)
        status, msg, opts = ga.tap_for_mana(self.gs, self.me, e, self.store)
        if status == "done":
            self.undo.append(snap)
            self.say(msg, "ok")
        elif status == "fail":
            self.say(msg, "fail")
        else:
            items = [(o.label(), (lambda i=i: self.act(
                lambda: self._tap_result(ga.tap_for_mana(self.gs, self.me, e, self.store, option_index=i)))))
                for i, o in enumerate(opts)]
            self.open_menu(items, pos, f"Tap {e['name']} for...")

    def crack_fetch(self, e):
        if not self.data_ready.is_set():
            self.say("Card data is still loading; try again in a moment.", "fail")
            return
        spec_box = {}

        def go():
            ok, msg, spec = ga.crack_fetch(self.gs, self.me, e, self.store)
            spec_box["spec"] = spec
            return ok, msg
        if self.act(go):
            self.open_viewer("library", mode="fetch", spec=spec_box["spec"])

    # ---- zone list overlay -----------------------------------------------------

    def open_viewer(self, zone, mode="view", spec=None):
        self.menu = None
        self.viewer = {"zone": zone, "mode": mode if zone == "library" else "view",
                       "spec": spec, "scroll": 0, "took": False}

    def close_viewer(self):
        v, self.viewer = self.viewer, None
        if not v:
            return
        if v["mode"] == "fetch":
            ga.shuffle_library(self.gs, self.me)
            self.say("Nothing fetched; library shuffled.", "info")
        elif v["zone"] == "library" and v["mode"] == "search" and v["took"]:
            ga.shuffle_library(self.gs, self.me)
            self.say("Library shuffled.", "info")

    def click_viewer(self, pos):
        for rect, idx, name in self.viewer_rows:
            if rect.collidepoint(pos):
                self.viewer_menu(idx, name, pos)
                return
        if not self.viewer_rect.collidepoint(pos):
            self.close_viewer()

    def viewer_menu(self, idx, name, pos):
        v = self.viewer
        if v["mode"] == "fetch":
            spec_tapped = v["spec"].get("tapped")
            items = [("Put onto the battlefield" + (" (tapped)" if spec_tapped else ""),
                      lambda: self.viewer_move(idx, "battlefield"))]
            if not spec_tapped:
                items.append(("Put onto the battlefield tapped", lambda: self.viewer_move(idx, "battlefield", True)))
        else:
            zone = v["zone"]
            items = [("To hand", lambda: self.viewer_move(idx, "hand")),
                     ("Onto the battlefield", lambda: self.viewer_move(idx, "battlefield")),
                     ("Onto the battlefield tapped", lambda: self.viewer_move(idx, "battlefield", True))]
            for dst, label in (("graveyard", "To graveyard"), ("exile", "To exile")):
                if dst != zone:
                    items.append((label, lambda dst=dst: self.viewer_move(idx, dst)))
            items.append(("To top of library", lambda: self.viewer_move(idx, "library", to_top=True)))
            items.append(("To bottom of library", lambda: self.viewer_move(idx, "library", to_top=False)))
        self.open_menu(items, pos, name)

    def viewer_move(self, idx, dst, tapped=None, to_top=True):
        v = self.viewer
        if v["mode"] == "fetch":
            spec = dict(v["spec"])
            if tapped:
                spec["tapped"] = True
            # push=False: Undo should roll back the whole crack-and-fetch in one step
            if self.act(lambda: ga.finish_fetch(self.gs, self.me, idx, spec, self.store), push=False):
                self.viewer = None
                self.check_prompts()             # a fetched shock land asks whether to pay
            return
        if self.act(lambda: ga.move_card(self.gs, self.me, v["zone"], idx, dst, self.store,
                                         tapped=True if tapped else None, to_top=to_top)):
            if v["zone"] == "library":
                v["took"] = True

    # ---- right-click menus -------------------------------------------------------

    def hand_menu(self, idx, pos):
        me = self.me
        if not 0 <= idx < len(me.hand):
            return
        name = me.hand[idx]
        info = self.store.peek_card(name)
        is_land = ga.is_land_name(self.store, name)
        items = [("Play land" if is_land else "Cast", lambda: self.play_hand(idx))]
        for oi, opt in enumerate(ga.hand_mana_options(self.gs, me, idx, self.store)):
            items.append((f"Use for mana: {opt.label()}",
                          lambda oi=oi: self.act(lambda: ga.hand_mana(self.gs, me, idx, self.store, oi))))
        if info and info.get("layout") == "modal_dfc" and len(info.get("card_faces", [])) > 1:
            back = info["card_faces"][1].get("name", "back face")
            items.append((f"Play back face: {back}", lambda: self.play_hand(idx, face=1)))
        if is_land:
            items.append(("Play land tapped", lambda: self.play_hand(idx, tapped=True)))
            items.append(("Put onto battlefield (not a land drop)", lambda: self.play_hand(idx, free=True)))
        else:
            items.append(("Cast without paying", lambda: self.play_hand(idx, free=True)))
            items.append(("Put onto battlefield", lambda: self.move("hand", idx, "battlefield")))
        items += [("Discard", lambda: self.move("hand", idx, "graveyard")),
                  ("Exile", lambda: self.move("hand", idx, "exile")),
                  ("Top of library", lambda: self.move("hand", idx, "library", to_top=True)),
                  ("Bottom of library", lambda: self.move("hand", idx, "library", to_top=False))]
        self.open_menu(items, pos, name)

    def perm_menu(self, idx, pos):
        me = self.me
        if not 0 <= idx < len(me.battlefield):
            return
        e = me.battlefield[idx]
        items = []
        if e.get("awaiting"):
            items.append(("Make its choice...", lambda: self.offer_prompt(e)))
        opts, manual, _reason = ga.tap_options(self.gs, me, e, self.store)
        if opts or manual:
            items.append(("Tap for mana", lambda: self.tap_mana(e, pos)))
        items.append(("Untap" if e["tapped"] else "Tap", lambda: self.act(lambda: ga.toggle_tapped(self.gs, me, e))))
        if ga.fetch_spec(self.store, e) and not e["tapped"]:
            items.append(("Crack fetchland", lambda: self.crack_fetch(e)))
        items += [("Return to hand", lambda: self.move("battlefield", idx, "hand")),
                  ("Graveyard", lambda: self.move("battlefield", idx, "graveyard")),
                  ("Exile", lambda: self.move("battlefield", idx, "exile")),
                  ("Top of library", lambda: self.move("battlefield", idx, "library", to_top=True)),
                  ("Bottom of library", lambda: self.move("battlefield", idx, "library", to_top=False))]
        self.open_menu(items, pos, e["name"])

    def cmd_menu(self, idx, pos):
        if not 0 <= idx < len(self.me.command_zone):
            return
        items = [("Cast (pays commander tax)", lambda: self.cast_commander(idx)),
                 ("Cast without paying", lambda: self.cast_commander(idx, free=True)),
                 ("Put onto battlefield", lambda: self.move("command", idx, "battlefield"))]
        self.open_menu(items, pos, self.me.command_zone[idx])

    def library_menu(self, pos):
        items = [("Search library", lambda: self.open_viewer("library", mode="search")),
                 ("Shuffle", lambda: self.act(lambda: (ga.shuffle_library(self.gs, self.me), (True, "Library shuffled"))[1])),
                 ("Draw a card", self.draw_one),
                 ("Mill 1 (top card to graveyard)", lambda: self.move("library", 0, "graveyard")),
                 ("Exile top card", lambda: self.move("library", 0, "exile"))]
        self.open_menu(items, pos, "Library")

    # ---- main loop ---------------------------------------------------------------

    def run(self):
        running = True
        while running:
            for ev in pygame.event.get():
                if not self.handle_event(ev):
                    running = False
            self.render()
            self.clock.tick(30)
        self.save_settings()
        pygame.quit()
        sys.exit()


# ---- deck loading --------------------------------------------------------

def load_deck(source):
    """Load a deck from a text-export file or a deck URL.

    Returns (commanders, deck_list). Raises DeckImportError with a readable message.
    """
    if source.lower().startswith(("http://", "https://")):
        return import_deck(source)

    path = os.path.abspath(os.path.expanduser(source))
    if not os.path.isfile(path):
        raise DeckImportError(f"Deck file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:   # utf-8-sig tolerates a Notepad BOM
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise DeckImportError(f"Could not read {path}: {e}")
    return import_from_text(text)


def describe_deck(label, commanders, deck):
    """Print a one-line summary and warn if the size is not a legal Commander deck."""
    names = " + ".join(commanders) if commanders else "(no commander found)"
    print(f"{label}: {names} | {len(deck)} cards in library")
    if not commanders:
        print(f"  Warning: no commander detected for {label}; the command zone will be empty.")
    if len(deck) + len(commanders) != 100:
        print(f"  Warning: expected 100 cards including the commander, got {len(deck) + len(commanders)}.")


def main(argv):
    player_source = argv[1] if len(argv) > 1 else DEFAULT_DECK
    ai_source = argv[2] if len(argv) > 2 else player_source

    try:
        commanders_a, deck_a = load_deck(player_source)
        if ai_source == player_source:
            commanders_b, deck_b = commanders_a, deck_a
        else:
            commanders_b, deck_b = load_deck(ai_source)
    except DeckImportError as e:
        print(f"Could not load deck: {e}")
        print("Usage: python table_gui.py [your_deck.txt] [ai_deck.txt]")
        sys.exit(1)

    describe_deck("You", commanders_a, deck_a)
    describe_deck("AI", commanders_b, deck_b)

    ai_label = commanders_b[0].split(",")[0] if commanders_b else "no commander"
    player_a = Player("Karl", deck_a, commanders=commanders_a)
    player_b = Player(f"AI ({ai_label})", deck_b, commanders=commanders_b)

    store = CardDataStore()
    gs = GameState(player_a, player_b)
    gs.start_game()

    names = set(deck_a) | set(deck_b) | set(commanders_a) | set(commanders_b)
    TableGUI(gs, store, prefetch_names=names, settings_path=SETTINGS_FILE).run()


if __name__ == "__main__":
    main(sys.argv)
