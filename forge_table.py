# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_table.py - the table for a Commander game whose rules are run by the Forge engine.

Usage (from the project folder, after `python setup_forge.py` has installed the Forge runtime):
    python forge_table.py                          opens the deck screen: pick your deck, the AI's deck and how many opponents (or paste a new deck)
    python forge_table.py my_deck.txt              your deck from a Moxfield/Archidekt text export or an Archidekt URL
    python forge_table.py my_deck.txt --opp ai1.txt --opp ai2.txt    a pod: up to three AI opponents
    python forge_table.py --seed 7                 repeatable shuffles (for testing)

Forge decides everything about the rules; this file draws what Forge says and sends your clicks back.
Press H in the game for the controls. Window: resizable, F11 = fullscreen, +/- = text size.
"""
import io
import json
import os
import re
import sys
import threading
import math
import time
from types import SimpleNamespace

if sys.platform == "win32":
    try:                                    # without this Windows blurs the window on scaled displays
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import pygame

pygame.mixer.pre_init(44100, -16, 2, 512)     # round 24: must happen before pygame.init() opens the mixer with default settings

import audio
import crashlog
import deck_library as lib
import forge_client as fc
import forge_dialogs as dlg
import forge_fx as ffx
import forge_log as flog
import forge_menu as fmenu
import forge_net
import forge_settings as fset
import forge_status as fstat
import forge_why
import formats
import flow_screens as flow
import anim as fanim
import boot_screens as fboot
import gfx
import last_session
import legality
import events as gevents
import java_crash
import journal as gjournal
import rewind as grewind
import licenses_view
import mana_hint as mh
import motion as mot
import online_save
import online_screens as onl
import paths
import perf
import updater
import reporting
import stats as gstats
import tour as ftour
import version
from art_loader import ArtLoader
import card_data as cdata
from gfx import (CYAN, DIM, GOLD, GOLD_DARK, GREEN, ORANGE, RED, TEXT, WHITE, YELLOW, clip_text, draw_text, get_font, round_rect,
                 wrap_text)

pygame.init()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = paths.settings_file()          # round 28: user_dir()/settings.json (the program folder in a portable copy)
SOUNDS_DIR = paths.sounds_dir()               # unchanged: always ships with the program (Round 29: via paths, so a frozen build looks in _MEIPASS)
CUES_FILE = os.path.join(SOUNDS_DIR, "cues.json")
_AUDIO_SHARED = {"director": None, "building": False, "lock": threading.Lock(), "event": threading.Event()}  # round 24
DEFAULT_DECK = os.path.join(paths.sample_dir(), "stompy_goreclaw.txt")      # patch 38 (was the Kinnan sample)
DECK_DIR = paths.forge_decks_dir()             # round 28: local_dir()/forge_decks

DEFAULT_WINDOW = (1360, 840)
MAX_BAD_FRAMES = 60                       # this many frames in a row with an error (about a second): give up and close
PERF_LOG = paths.log_file("perf_log.txt")      # round 28: local_dir()/logs/perf_log.txt (MANTICORE_DATA_DIR still wins)
SAVES_DIR = paths.saves_dir()                  # round 28: user_dir()/saves (MANTICORE_DATA_DIR still wins)
MIN_WINDOW = (900, 600)
CARD_ASPECT = 488 / 680


def type_letter(card):
    """C/A/E/L/P for the board frame's type icon (round 26); '' when nothing obvious fits."""
    if card.get("isPlaneswalker"):
        return "P"
    if card.get("isLand"):
        return "L"
    if card.get("isCreature"):
        return "C"
    t = (card.get("type") or "").lower()
    if "artifact" in t:
        return "A"
    if "enchantment" in t:
        return "E"
    return ""


HAND_SORT_CATEGORIES = ("creature", "planeswalker", "instant", "sorcery", "battle", "artifact", "enchantment", "land")


def hand_sort_category(card):
    """A rough bucket for 'sort hand by type': creatures and planeswalkers first (the things you play as threats), then
    instants/sorceries, then artifacts/enchantments, lands last. Matches type_letter()'s own classification for the types
    it covers so the two never disagree."""
    if card.get("isCreature"):
        return "creature"
    if card.get("isPlaneswalker"):
        return "planeswalker"
    if card.get("isLand"):
        return "land"
    t = (card.get("type") or "").lower()
    for cat in ("instant", "sorcery", "battle", "artifact", "enchantment"):
        if cat in t:
            return cat
    return "other"


def hand_sort_key(card):
    cat = hand_sort_category(card)
    rank = HAND_SORT_CATEGORIES.index(cat) if cat in HAND_SORT_CATEGORIES else len(HAND_SORT_CATEGORIES)
    return (rank, card.get("name") or "")


TEXT_STEPS = (1.0, 1.25, 1.5, 1.75, 2.0)

# Round 27e: the window must fit the screen it opens on. Remote Desktop gives the same PC a different (often much smaller) screen
# than its own monitor, and the window size remembered from the monitor (e.g. 4096x2019) opened far bigger than the desktop.
SCREEN_ROOM = (0.96, 0.90)          # the most of the desktop a window may take: room for the title bar and the taskbar
DISPLAY_CHECK_SECONDS = 2.0         # how often the table looks for a changed desktop (a Remote Desktop reconnect)


def desktop_size():
    """(w, h) of the main desktop right now, or None when it can't be read (no display)."""
    try:
        sizes = pygame.display.get_desktop_sizes()
        if sizes and sizes[0][0] > 0 and sizes[0][1] > 0:
            return (int(sizes[0][0]), int(sizes[0][1]))
    except (pygame.error, AttributeError):
        pass
    return None


def fit_window(size, desktop):
    """size, made small enough to fit desktop (never below MIN_WINDOW); unchanged when the desktop is unknown."""
    w, h = max(MIN_WINDOW[0], int(size[0])), max(MIN_WINDOW[1], int(size[1]))
    if not desktop:
        return (w, h)
    room_w = max(MIN_WINDOW[0], int(desktop[0] * SCREEN_ROOM[0]))
    room_h = max(MIN_WINDOW[1], int(desktop[1] * SCREEN_ROOM[1]))
    return (min(w, room_w), min(h, room_h))


def screen_key(desktop):
    """The name a screen size is remembered under in settings.json ("screens"), e.g. "1920x1080"."""
    return f"{desktop[0]}x{desktop[1]}" if desktop else None
STACK_MAX_SHARE = 0.44           # the stack panel takes at most this much of the right-hand column (the rest scrolls)
# patch UI6: Cog > Display > Log and Focus. The game log and the focus card each have three modes (settings.json log_mode, preview_mode).
LOG_MODES = ("always", "corner", "hidden")             # always: the log panel in the right column | corner: an overlay while the mouse is in the window's lower-right corner | hidden: never drawn
PREVIEW_MODES = ("always", "over_card", "hidden")      # always: the focus panel | over_card: the big picture appears over the card the mouse rests on | hidden: only a right-click pin shows it
LOG_LABELS = {"always": "Always", "corner": "Corner", "hidden": "Hidden"}
PREVIEW_LABELS = {"always": "Always", "over_card": "Over card", "hidden": "Hidden"}
CORNER_SIDE = 48               # the log's hot corner is this many pixels (times the text-scaled font factor) on a side
LOG_CLOSE_SECONDS = 0.25       # the corner log closes this long after the mouse has left both the corner and the log
CORNER_LOG_SHARE = 0.42        # the corner log is this share of the right column's height (about what the log gets today beside no stack)
OVER_DWELL = 0.12              # the mouse must rest on a card this long before its big picture appears over it
OVER_SWITCH = 0.06             # ... and this long on the next card before the picture moves there (the AUDIT's hover hysteresis)
NARROW_RIGHT = 0.8             # with neither the panel nor the log in the column it is this much narrower (never under 260 px)
LOG_MIN_SHARE = 0.28           # with the log in the column and the panel off, the stack leaves the log at least this much of the column
ROOMY_STACK_SHARE = 0.72       # the stack may take this much of the column when the panel or the log gave up their room
AUX_MIN = 140                  # the card slot under the stack (AI casts, surveil) needs this many pixels (times the font factor) to show
ABILITY_TEXT_LINES = 5         # the card's own wording under a trigger / ability on the stack (only when Forge's text says something else)
SPELL_TEXT_LINES = 10         # a spell on the stack shows at most this many lines of its rules text
FONT_BASE = dict(title=19, body=15, small=13, tiny=11, btn=17, big=34, hint=13)

# the phase pills along the top: (Forge phase name, label). First strike shows under "Damage".
PILLS = [("UPKEEP", "Upkeep"), ("DRAW", "Draw"), ("MAIN1", "Main 1"), ("COMBAT_BEGIN", "Begin combat"),
         ("COMBAT_DECLARE_ATTACKERS", "Attackers"), ("COMBAT_DECLARE_BLOCKERS", "Blockers"), ("COMBAT_DAMAGE", "Damage"),
         ("COMBAT_END", "End combat"), ("MAIN2", "Main 2"), ("END_OF_TURN", "End step")]
PILL_SHORT = ["Upk", "Drw", "M1", "BC", "Atk", "Blk", "Dmg", "EC", "M2", "End"]
PILL_GAP_AFTER = {"DRAW", "MAIN1", "COMBAT_END", "MAIN2"}       # a wider gap between the parts of the turn
PILL_FULL = {"UPKEEP": "the upkeep step", "DRAW": "the draw step", "MAIN1": "your first main phase",
             "COMBAT_BEGIN": "the beginning of combat", "COMBAT_DECLARE_ATTACKERS": "declare attackers",
             "COMBAT_DECLARE_BLOCKERS": "declare blockers", "COMBAT_DAMAGE": "combat damage", "COMBAT_END": "the end of combat",
             "MAIN2": "the second main phase", "END_OF_TURN": "the end step"}
PILL_OF = {"COMBAT_FIRST_STRIKE_DAMAGE": "COMBAT_DAMAGE", "CLEANUP": "END_OF_TURN"}
COUNTER_NAMES = {"P1P1": "+1/+1", "M1M1": "-1/-1"}
COLOUR_WORDS = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}


def imprint_colours(card):
    """The colours of the cards exiled with this one (imprint), in WUBRG order."""
    seen = {c for i in (card.get("imprinted") or []) for c in (i.get("colors") or [])}
    return [c for c in "WUBRG" if c in seen]


TWO_WORD_KEYWORDS = {"first", "double", "living", "cumulative", "totem", "battle"}       # "first strike" must not match "first main phase"


def gained_keywords(card):
    """Keywords the permanent has right now that its own rules text does not mention: granted by a spell (Jump), an Equipment, an Aura or an
    ability, usually until end of turn. `keywords` comes from the bridge (Forge's current keyword list). A keyword counts as printed when its first
    word appears in the card's text, so a card that grants itself a keyword in some roundabout way is not flagged (never the other way round)."""
    text = (card.get("text") or "").lower()
    out = []
    for kw in card.get("keywords") or []:
        words = [w for w in re.split(r"[\s\u2014:{(-]+", kw.strip().lower()) if w]
        base = " ".join(words[:2]) if words and words[0] in TWO_WORD_KEYWORDS and len(words) > 1 else (words[0] if words else "")
        if base and base not in text and kw not in out:
            out.append(kw)
    return out


# Round 32 (Karl, 3 Oct 2026: "creatures with menace need to have a tag on them like haste, flying, lifelink"): printed keywords that
# get a tag too. Menace changes how a creature can be blocked, and a board-sized card's text is too small to read.
PRINTED_TAGS = {"menace"}


def keyword_tags(card):
    """The keyword tags drawn on a permanent, in order: each PRINTED_TAGS keyword it has right now ("Menace"), then each gained one
    with a "+" ("+Flying"). A printed keyword it has lost (a Humility) is not tagged: only Forge's current list counts."""
    gained = gained_keywords(card)
    out = []
    for kw in card.get("keywords") or []:
        if kw.strip().lower() in PRINTED_TAGS and kw not in gained and kw not in out:
            out.append(kw)
    return out + ["+" + kw for kw in gained]


def makes_imprinted_colours(card):
    """True for a Chrome Mox: 'Add one mana of any of the exiled card's colors'. Its colours are the whole point of the card, and nothing
    on the table said which they were (bug report 2026-09-20 16:14: a blue card was imprinted, {G} could not be paid)."""
    return "exiled card's colo" in (card.get("text") or "")
# status badges on a player's panel (monarch, initiative ...): key -> (fill, outline)
BADGE_COLOURS = {"monarch": (gfx.BADGE_MONARCH_BG, GOLD), "initiative": (gfx.BADGE_INITIATIVE_BG, gfx.BADGE_INITIATIVE_FG), "ring": (gfx.BADGE_RING_BG, gfx.BADGE_RING_FG),
                 "emblem": (gfx.BADGE_EMBLEM_BG, gfx.BADGE_EMBLEM_FG), "effects": (gfx.BADGE_EFFECTS_BG, gfx.BADGE_EFFECTS_FG)}
LOG_COLOURS = {"STACK_ADD": GOLD, "STACK_RESOLVE": gfx.LOG_STACK_RESOLVE, "COMBAT": ORANGE, "DAMAGE": RED, "LIFE": gfx.LOG_LIFE,
               "TURN": CYAN, "LAND": gfx.LOG_LAND, "MULLIGAN": DIM, "ZONE_CHANGE": gfx.LOG_ZONE, "DRAW": CYAN,
               "MANA": gfx.LOG_MANA}
LOG_TEXT = {flog.TEXT: gfx.LOG_TEXT_NORMAL, flog.DIM: gfx.LOG_TEXT_DIM, flog.ME: gfx.LOG_TEXT_ME, flog.OPP: gfx.LOG_TEXT_OPP,
            flog.CARD: gfx.LOG_TEXT_CARD, flog.GOOD: gfx.LOG_TEXT_GOOD, flog.BAD: gfx.LOG_TEXT_BAD}
BG_ORDER = ("graveyard", "cathedral", "citadel", "ruins")          # the pictures a game can start on, in rotation order (Round AD1)
ONLINE_PREF_KEYS = ("name", "port", "upnp", "address", "known_hosts", "guests", "ai",   # (MP2c: guests, ai)
                    "relay", "use_relay")                                                # (MP2d: the relay's address:port)      # Round MP1: settings.json "online" (never the password)
KNOWN_HOSTS_MAX = 20
BG_CHOICES = ("rotate",) + BG_ORDER + ("plain",)                    # what the cog's Table setting cycles through
BG_LABELS = {"rotate": "Rotate", "graveyard": "Graveyard", "cathedral": "Cathedral", "citadel": "Citadel", "ruins": "Ruins", "plain": "Plain"}
DISPLAY_KEYS = {"title", "btn"}                        # font keys drawn in the display face (Round AD1)
PASSING_VERSION = 43                                   # patch 43: settings written by this version know about the passing rules
REPEAT_STOP_OFFER = 2                                  # patch 43: the second stop by one card's trigger in a turn offers "Y = always pass"
SPEEDS = ("fast", "slow")                              # round PRI1, Karl's Speed (cog > GAME): fast = patch 43's stops; slow = the stops from
                                                       # before patch 43, nothing passed for me, auto-pass off (java_bridge Passing.slow)
SPEED_LABELS = {"fast": "Fast", "slow": "Slow"}
SPEED_RESEND = 3.0                                     # round PRI1: seconds before the same Speed is sent again if the seat hasn't taken it
STATS_RESULT_WAIT = 4.0                                # patch 44: seconds to wait for Forge's result after the game-over snapshot
FEED_MAX, FEED_SECONDS = 4, 7.0                        # how many opponent actions the board shows, and for how long
QUIET_PING = 2.5           # seconds after a command with no word from Forge: ask it for a fresh snapshot (a harmless "flush")
QUIET_BANNER = 5.0         # seconds after a command with no word from Forge, even after the ping: show "Forge has not answered"
UNDO_WAIT = 1.0                                        # seconds to wait for Forge to change something after Undo before saying there was nothing to undo
UNDO_NOTHING = "Nothing to undo. Forge only lets you take back a mana tap that is still unspent."
UNDO_NOTHING_YET = "Nothing to undo yet. Undo goes back as far as the start of your turn."      # round UNDO1
UNDO_ONLINE = "In an online game Undo only takes back a mana tap that is still unspent."         # round UNDO1
LIFE_FLASH_SECONDS = 2.0                               # a life total flashes (and shows +3 / -3) for this long after it changes
LIFE_GAIN, LIFE_LOSS = gfx.LIFE_GAIN_COLOUR, gfx.LIFE_LOSS_COLOUR
HELP_LINES = [
    ("Click a card", "Play it, cast it, activate it, or pick it when Forge asks you to choose. Glowing cards are the ones Forge says you can use."),
    ("OK / Space", "Pass priority, confirm a choice, or move on. What it does is written in the bar above your hand."),
    ("Enter / Ctrl / Y", "At priority, Enter passes until an opponent casts something or attacks you, or the turn ends; Shift+Enter passes the rest of the turn. Ctrl: every stop this turn (Ctrl+Shift: until pressed again). Ctrl+click a card: cast it and keep priority. Y: always pass on what's on the stack."),
    ("Cancel / Esc", "Cancel what you are doing. When the second button says End Turn or Full Send it is not on Esc, so a slip can't end your turn."),
    ("E", "Press End Turn (when that is what the second button says)."),
    ("A", "Press Full Send: attack with everything that can (shown while you declare attackers)."),
    ("S / Skip...", "Opens the Skip window: let the stack resolve, skip to your next turn, auto-pass and full control on or off, 'always pass' on the ability on the stack or 'always stop' on its card. An opponent's trigger passes unless you hold an answer to it."),
    ("U / Ctrl+Z / Undo", "Go back to an earlier moment of your turn, as far as its start: a list shows what you did (\"Main 1: before you cast Sol Ring\"), you pick one, and the game is rebuilt from the first turn with the same shuffle and your same clicks up to there - that takes a while in a long game, and your game stays as it is if the rebuilt one doesn't match. With mana floating, Undo first takes back that mana tap at once. Against AI opponents only; online it only takes back a mana tap."),
    ("M", "Sound on/off. Same switch as the cog's SOUND group; a toast confirms which."),
    ("Click a player", "Target that player, or attack them, when Forge is asking for one."),
    ("Turn bar (top)", "Shows whose turn it is and which phase you are in (the lit pill). Below each pill are two dots: click the blue one to stop there on YOUR turn, the orange one to stop there on an OPPONENT's turn. Clicking the pill itself toggles the blue one."),
    ("Rings and numbers", "A pale blue-white ring: a permanent that just entered the battlefield (yours or theirs). A purple ring with a number: a trigger from that card is on the stack. A green ring: an ability someone activated. The number is the row in the stack panel (1 resolves first). Cog > Animations turns the flashing off and keeps the rings."),
    ("Hover / right-click", "Hover a card to see it large on the right. Right-click a card to keep it there."),
    ("Library / Graveyard / Exile", "The three piles on each player's panel (left). Click a graveyard or exile pile to look inside. Your library is the face-down pile: you can't browse it, but when an effect lets you search it (a fetchland, a tutor) a window opens listing its cards: click the one you want, then press the button at the bottom right."),
    ("Command zone", "The gold frame beside your hand holds your commander; click the card to cast it (Forge adds the tax). Opponents' command zones are next to their panels."),
    ("Chips on a panel", "A gold crown chip means that player is the monarch; other chips show the initiative, the Ring and emblems. Hover one to read what it does. A life total flashes red or green with -3 / +2 when it changes."),
    ("New game / Ctrl+N", "Opens the deck screen: choose your deck, the deck the AI plays and how many opponents, or import a deck by pasting its text list (Ctrl+V there)."),
    ("Cog (top right)", "Four groups. DISPLAY: text size, a Table control (< and > pick the background picture: Rotate changes it every game, or Graveyard, Cathedral, Citadel, Ruins, Plain), switches for full screen and animations, Compact board cards (small battlefield cards use an art-crop picture with a name strip instead of the shrunk full card; off by default) and Sort hand, then Log and Focus (click to go on, right-click to go back): Log is Always (the right column), Corner (it opens over the lower-right corner while the mouse is there) or Hidden; Focus is Always (the big card panel), Over card (the big picture appears over the card the mouse rests on) or Hidden - in Over card and Hidden a right-click pins a card's picture, Esc lets go. SOUND: on/off and a Hover tick switch for the hand-hover sound, and volume. GAME: Speed (Fast: Forge passes the stops that don't matter, as since patch 43; Slow: the stops from before patch 43 - your Main 1, attackers, Main 2 and end step, each opponent's upkeep, attackers and end step, and anything on the stack - with nothing passed for you; a game on the table changes at your next priority), New game... (asks: the same decks again with a fresh shuffle, or choose decks) and Concede... (asks: concede and stay on the table, or concede and close the program). HELP: this help, Bug or idea (the report window, with a Suggest a feature tab), Open my data folder (settings, decks and saves - the program folder itself, unless this is an installed copy), Tour of the table (the one-minute look at the table that starts by itself in your first game), and an Updates switch (an installed copy looks for a new version on the title screen; your own git copy never does). Everything that used to be a button up there lives in it."),
    ("F11 / + / -", "Fullscreen / bigger and smaller text. Both are remembered."),
    ("F3", "Shows how fast the table draws on this computer (frame times) and how quickly Forge answers. Press again to hide."),
    ("F8 / Bug or idea", "Something wrong? The game packs a report (a picture of the table, the board, the log, the version) into one zip and sends it to Karl if his Discord is set up, even with a question on screen. The second tab, Suggest a feature, sends him an idea instead (F8 on the deck screen opens on it)."),
    ("Licenses and credits", "Cog > Licenses and credits (or Shift+F1): the program's licence (GNU GPL v3), where to get the source, and the licences of everything it uses."),
    ("Damage / divide window", "Opens when a blocked creature deals its damage among several things, or a spell divides an amount (Arc Lightning, mana of any combination). One row per target with - and +, a Lethal (or Max) button and a Rest button. Up / Down pick a row, Left / Right change it, L = lethal, R = the rest, A = the automatic split, Enter = OK (only when the split is legal; the window says what to fix)."),
    ("Mouse wheel", "Scrolls the game log and long card lists."),
    ("Where your data is", paths.describe() + ". Cog > Open my data folder opens it."),
]


def deck_meta(entry):
    """Patch 44: a deck as the stats name it - the deck library's id (the same deck across renames of nothing else) and name."""
    if entry is None:
        return {"id": None, "name": ""}
    return {"id": getattr(entry, "id", None), "name": getattr(entry, "name", "") or ""}


def online_stats_id(folder):
    """Patch 44: a hosted game's stats id is its save folder's, so a game continued another day is the same game."""
    return "online-" + os.path.basename(os.path.normpath(folder)) if folder else gstats.new_id()


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def round_number(turn, player_count):
    """Forge's own 'turn' counter goes up by one every PLAYER turn (turn 1 = the first player's 1st turn, turn 2
    = the second player's 1st turn, turn 3 = the first player's 2nd turn, ...), same as Magic Arena/MTGO number
    it. Karl's PC (round 15): with 2 players that made the top bar say "Turn 12" for what is really everyone's
    6th turn. This converts to that round number - every player's Nth turn reads as round N - which is what the
    top bar shows; ln.turn in the game log stays Forge's raw per-player count on purpose, since that log lists
    each individual turn as its own row."""
    if not turn or not player_count:
        return turn
    return -(-turn // player_count)   # ceil division without importing math


def layout_row(widths, x0, avail_w, gap):
    """Left x of each item in a row; items overlap (later ones on top) when the row is too full."""
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


def clean_text(s):
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


_PRIORITY = re.compile(r"^Priority: .*? Turn: \d+ \(.*\) Phase: (.*?) Stack: (.*?)\.?$", re.S)


def pretty_prompt(msg):
    """Forge's priority prompt is one long sentence; show 'Your priority - Main phase - Stack: Empty' instead."""
    m = _PRIORITY.match(" ".join(clean_text(msg).split()))
    if not m:
        return msg
    phase, stack = m.group(1).strip(), m.group(2).strip()
    return f"Your priority  -  {phase}  -  Stack: {stack}"


_PRIORITY_FULL = re.compile(r"^Priority: (?P<me>.*?) Turn: (?P<n>\d+) \((?P<owner>.*)\) Phase: (?P<phase>.*?) Stack: (?P<stack>.*?)\.?$", re.S)
BUTTON_NAMES = {"Alpha Strike": "Full Send"}      # what a Forge button is called on screen
_PAY = re.compile(r"^(?P<src>.*?)\s*Pay Mana Cost: (?P<cost>.*)$", re.S)
_ATTACK_PROMPT = re.compile(r"^Select creatures to attack (?P<d>.+?) or select player/card you wish to attack\.?$")
_BLOCK_PROMPT = re.compile(r"^Select creatures to block (?P<a>.+?) or select another attacker to declare blockers for\.?$")
_RETURN_PROMPT = re.compile(r"^Return (?P<n>\d+) card\(s\) to the bottom of your library$")
_GOING = re.compile(r"you are going (?P<pos>\w+?)[.!]", re.I)
PHASE_FRIENDLY = {"main phase, precombat": "Main 1", "main phase, postcombat": "Main 2", "upkeep step": "Upkeep", "upkeep": "Upkeep",
                  "draw step": "Draw", "draw": "Draw", "beginning of combat step": "Beginning of combat",
                  "declare attackers step": "Declare attackers", "declare blockers step": "Declare blockers",
                  "first strike damage step": "First-strike damage", "combat damage step": "Combat damage",
                  "end of combat step": "End of combat", "end step": "End step", "cleanup step": "Cleanup", "untap step": "Untap"}


def short_name(name):
    """'AI 1 (Kinnan)' -> 'AI 1' where space is tight."""
    return re.sub(r"\s*\([^)]*\)$", "", name or "") or name


def strip_ids(text):
    """'Kinnan, Bonder Prodigy (155)' -> 'Kinnan, Bonder Prodigy' (Forge's card numbers mean nothing to a player)."""
    return re.sub(r"\s\(\d{1,5}\)", "", text or "")


_TARGET = re.compile(r"^(?P<src>.+?) \((?P<id>\d+)\)(?: - (?P<rest>.*?))?\s*\n\s*\nSelect (?:a )?targets?\b ?(?P<what>.*?)\.?$", re.S)


def parse_target_prompt(message):
    """Forge asks for a target with '<Card> (<id>) - <cost>: <effect>\\n\\nSelect target <what>.'
    Returns {"source", "id", "cost", "effect", "what"}, or None when the prompt is not a target prompt."""
    m = _TARGET.match(clean_text(message or "").strip())
    if not m:
        return None
    rest = (m.group("rest") or "").strip()
    cost, effect = rest.split(": ", 1) if ": " in rest else ("", rest)
    return {"source": m.group("src").strip(), "id": int(m.group("id")), "cost": cost.strip(),
            "effect": " ".join(effect.split()), "what": " ".join(m.group("what").split())}


# What Forge is asking, by the class of its current Input (bridge protocol 2 sends it as the snapshot's "input"). The text of a prompt
# can change between Forge versions or languages; the class cannot, so these decide WHICH prompt it is, and the regexes above only pull
# the details (cost, source, count) out of the text. None (an old bridge, test fixtures) = classify by the text, as before round 22.
INPUT_KIND = {"InputPassPriority": "priority", "InputPayMana": "pay", "InputPayManaOfCostPayment": "pay",
              "InputAttack": "attack", "InputBlock": "block", "InputSelectTargets": "target",
              "InputConfirmMulligan": "mulligan", "InputLondonMulligan": "bottom", "InputConfirm": "confirm",
              "InputSelectCardsFromList": "select", "InputSelectEntitiesFromList": "select",
              "InputSelectCardsForConvokeOrImprovise": "convoke", "InputChooseStartingHand": "starting_hand"}


def kind_of(input_class):
    """'priority' | 'pay' | ... for a Forge Input class name; None when unknown or empty (fall back to the text)."""
    if not input_class:
        return None
    if input_class.startswith("InputPayMana"):            # Forge has several payment inputs; all of them are "pay"
        return "pay"
    return INPUT_KIND.get(input_class)


_PAY_LOOSE = re.compile(r"^(?P<src>.*?)\s*pay\s+mana\s+cost\s*:?\s*(?P<cost>.*)$", re.S | re.I)
_RETURN_LOOSE = re.compile(r"(?P<n>\d+)")


def _pay_view(src, cost, floating, trouble):
    src = re.split(r"\s-\s", src)[0].strip() if src else ""
    hint = ("Click floating mana (yellow, above) or tap glowing lands / mana rocks, or press Auto (floating mana first). "
            "Cancel = take the spell back." if floating else
            "Tap lands / mana rocks (glowing) to pay, or press Auto to let Forge tap them. Cancel = take the spell back.")
    if trouble:                                              # mana_hint.explain: why what you have cannot pay this
        hint = trouble + " Cancel takes the spell back."
    return (f"Pay {cost}" if cost else "Pay the cost") + (f"  -  {src}" if src else ""), hint, None


def _view_by_kind(kind, message, text, flat, my_name, mulligans, floating, trouble):
    """prompt_view for a known kind: the kind picks the branch, the text only supplies details. None = let the text decide."""
    if kind == "pay":
        m = _PAY.match(text) or _PAY_LOOSE.match(text)
        return _pay_view(m.group("src").strip(" -\n") if m else "", " ".join(m.group("cost").split()) if m else "", floating, trouble)
    if kind == "target":
        tgt = parse_target_prompt(message)
        if tgt:
            return None                                      # the text path below words it (same result)
        return "Choose a target", "Click a glowing card or player. Cancel backs out.", None
    if kind == "attack" and not _ATTACK_PROMPT.match(flat):
        return ("Declare attackers",
                "Click each creature that attacks, then OK (none = no attack). Full Send = all. Click another player to attack them.", None)
    if kind == "block" and not _BLOCK_PROMPT.match(flat):
        return "Declare blockers", "Click your creatures that block, then OK when done (none = no blocks).", None
    if kind == "bottom" and not _RETURN_PROMPT.match(flat):
        m = _RETURN_LOOSE.search(flat)
        n = int(m.group("n")) if m else 0
        return (f"Put {n} card{'s' if n != 1 else ''} on the bottom of your library",
                f"Click {n} card{'s' if n != 1 else ''} in your hand, then OK. Auto picks for you." if n else "Press OK.", None)
    if kind == "priority" and not _PRIORITY_FULL.match(flat):
        return "Your priority", "Play a land or cast a spell (glowing cards), or Pass priority.", "Pass priority"
    return None


# Round 27e: Forge's command-zone question is the whole rules text (Karl, 2026-09-26: "This text doesn't fit. Should be short.")
_COMMANDER_ZONE = re.compile(r"^(?P<name>.+?):\s*If a commander is in a graveyard or in exile", re.I)


def commander_zone_name(message):
    """The commander's name when message is Forge's "move your commander to the command zone?" question, else None."""
    m = _COMMANDER_ZONE.match(" ".join(strip_ids(clean_text(message or "")).split()))
    return m.group("name").strip() if m else None


def prompt_view(message, my_name="", mulligans=0, floating=False, trouble="", kind=None):
    """What the bar above the hand says. Returns (headline, hint, ok_label or None). Forge's own wording is one long sentence
    per prompt ('Select creatures to block Kinnan, Bonder Prodigy (155) or select another attacker to declare blockers for.');
    this gives a short headline, one line saying what to do, and (at priority) a clearer name for the OK button."""
    text = strip_ids(clean_text(message)).strip()
    flat = " ".join(text.split())
    if not flat:
        return "", "", None
    name = commander_zone_name(message)
    if name:
        return "Return your commander to the command zone?", f"{name}  -  No leaves it where it is.", None
    if kind:
        by_kind = _view_by_kind(kind, message, text, flat, my_name, mulligans, floating, trouble)
        if by_kind is not None:
            return by_kind
    m = _PRIORITY_FULL.match(flat)
    if m:
        phase = PHASE_FRIENDLY.get(m.group("phase").strip().lower(), m.group("phase").strip())
        owner, stack = m.group("owner").strip(), m.group("stack").strip()
        turn = "your turn" if owner == my_name else f"{owner}'s turn"
        if stack.lower() == "empty":
            return (f"Your priority  -  {phase}, {turn}",
                    "Play a land or cast a spell (glowing cards), or Pass priority.", "Pass priority")
        n = re.match(r"(\d+) to resolve", stack, re.I)
        on_stack = f"{n.group(1)} item{'s' if n.group(1) != '1' else ''} on the stack" if n else f"{stack.rstrip('.')} on the stack"
        return (f"Your priority  -  {on_stack}  -  {phase}, {turn}",
                "Respond (glowing cards) or Pass priority to resolve the top item.", "Pass priority")
    if flat.startswith("Waiting for"):
        return flat.rstrip("."), "", None
    m = _ATTACK_PROMPT.match(flat)
    if m:
        return (f"Declare attackers  -  attacking {m.group('d')}",
                "Click each creature that attacks, then OK (none = no attack). Full Send = all. Click another player to attack them.", None)
    m = _BLOCK_PROMPT.match(flat)
    if m:
        return (f"Declare blockers  -  choose blockers for {m.group('a')}",
                "Click your creatures that block it, or another attacker to pick its blockers. OK when done (none = no blocks).", None)
    tgt = parse_target_prompt(message)
    if tgt:
        what = tgt["what"] or "target"
        pay = f" You pay {tgt['cost']} after you choose." if tgt["cost"] else ""
        return (f"Choose the target for {tgt['source']}",
                f"Click a glowing {what}. The orange SOURCE tag marks {tgt['source']} itself.{pay} Cancel backs out.", None)
    m = _PAY.match(text)
    if m:
        return _pay_view(m.group("src").strip(" -\n"), m.group("cost").strip(), floating, trouble)
    m = _RETURN_PROMPT.match(flat)
    if m:
        n = int(m.group("n"))
        return (f"Put {n} card{'s' if n != 1 else ''} on the bottom of your library",
                f"Click {n} card{'s' if n != 1 else ''} in your hand, then OK. Auto picks for you." if n else "Press OK.", None)
    if "Do you want to keep your hand?" in flat:
        m = _GOING.search(flat)
        order = "You go first" if "you are going first" in flat.lower() else (f"You go {m.group('pos')}" if m else "")
        free = mulligans == 0
        return ((order + "  -  " if order else "") + "keep this hand?",
                ("Keep plays this hand. Mulligan draws a new seven (the first is free)." if free else
                 f"Keep plays this hand. Mulligan draws a new seven and puts {mulligans} on the bottom."),
                None)
    if "who would you like to start" in flat.lower():         # round 28ba: 3-4 players - pick a portrait, not Play/Draw
        return ("You won the coin toss  -  choose who starts",
                "Click a player's panel (your own to go first), then OK. OK stays grey until you pick someone.", None)
    m = re.search(r"Choose an? (?P<who>opponent|player)\b(?P<rest>[^.\n]*)", flat)
    if m:                                                     # round 28d: "choose a player" list questions (a Siege's protector)
        src = flat[:m.start()].strip(" -")
        what = ("Choose an opponent" if m.group("who") == "opponent" else "Choose a player") + m.group("rest").rstrip()
        return (what, (f"{src}  " if src else "") + "Click a glowing player panel, then OK.", None)
    if "won the coin toss" in flat or "lost the last game" in flat:
        return "You won the coin toss  -  play first or draw?", "Play = you take the first turn. Draw = the opponent goes first.", None
    m = re.match(r"^(?P<src>.*?)\s*Select a card from your hand$", flat)
    if m and m.group("src"):
        return "Choose a card from your hand", f"{m.group('src').strip(' -')}  Click the card in your hand.", None
    if flat.startswith("Looking at cards in"):
        return flat.rstrip("."), "Press OK when you are done looking.", None
    return text.replace("\n\n", "  -  ").replace("\n", " "), "", None


class LogLine:
    """One wrapped line of the game log panel. kind: 'line' | 'turn' | 'phase' | 'spacer'."""
    __slots__ = ("kind", "tokens", "bar", "indent", "label", "mine", "turn")

    def __init__(self, kind, tokens=(), bar=None, indent=0, label="", mine=False, turn=0):
        self.kind, self.tokens, self.bar, self.indent, self.label, self.mine, self.turn = kind, list(tokens), bar, indent, label, mine, turn

    def text(self):
        return "".join(t for t, _s, _r in self.tokens) or self.label


class Slot:
    """One thing on a battlefield: a card, or a stack of identical lands/tokens, plus its attachments."""
    __slots__ = ("cards", "attached", "rect", "tapped", "_layout")

    def __init__(self, cards, attached=()):
        self.cards = cards
        self.attached = list(attached)
        self.rect = None
        self.tapped = cards[0].get("tapped", False)


class Launcher:
    """Starts a Forge game for the deck screen (and for the command line): writes the .dck files and launches the engine."""

    def __init__(self, runtime=None, name="Karl", seed=None, record=None):
        self.runtime, self.name, self.seed, self.record = runtime, name, seed, record

    def start(self, mine, opponents, fmt=None):
        """mine = (commanders, deck); opponents = [(commanders, deck), ...]; fmt (round FMT1) "commander" (None) or "brawl".
        Returns a started ForgeSession."""
        fmt = formats.normal(fmt)
        os.makedirs(DECK_DIR, exist_ok=True)
        my_path = fc.write_deck_file(os.path.join(DECK_DIR, "player.dck"), mine[0], mine[1], "Player", self.runtime, fmt)
        opp_paths = [fc.write_deck_file(os.path.join(DECK_DIR, f"opponent{i}.dck"), o[0], o[1], f"Opponent {i}", self.runtime, fmt)
                     for i, o in enumerate(opponents, 1)]
        return fc.ForgeSession(my_path, opp_paths, name=self.name, seed=self.seed, runtime=self.runtime,
                               record_path=self.record, fmt=fmt).start()


class ResumeJob:
    """Plays an unfinished game's commands into a new engine on a worker thread (replay.Replayer does the waiting and the sending).
    While it runs, only the worker talks to the session: the table must not poll it."""

    def __init__(self, session, commands):
        self.session, self.commands = session, commands
        self.done_count, self.total = 0, len(commands)
        self.finished, self.ok, self.diverged, self.error = False, False, None, None
        self.cancelled = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="resume")

    def start(self):
        self.thread.start()
        return self

    def _progress(self, i, n):
        self.done_count = i
        if self.cancelled:
            raise KeyboardInterrupt("cancelled")

    def _run(self):
        import replay
        try:
            r = replay.Replayer(self.session, self.commands, progress=self._progress)
            self.ok = r.run()
            self.diverged = r.diverged
        except KeyboardInterrupt:
            self.error = "cancelled"
        except Exception as e:                       # the table must survive whatever happens here
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.finished = True


class CardMotion:
    """One per card id that is currently animating (hover lift, press dip, or a spring flight). Round 23. Cards not moving are not
    kept here at all: ForgeTable.step_motion() drops an entry once its springs are at rest and it has not flown or been touched
    for MOTION_IDLE_SECONDS, so the dict never grows with the length of the game."""
    __slots__ = ("lift", "scale", "x", "y", "flight_start", "landed_at")

    def __init__(self):
        self.lift = mot.Spring(0.0, *mot.HOVER)          # hand hover, in pixels
        self.scale = mot.Spring(1.0, *mot.PRESS)         # the press dip (1.0 = rest)
        self.x = None                                    # Spring | None: only set while flying
        self.y = None
        self.flight_start = None                         # time.monotonic() the flight began (0.6 s cutoff)
        self.landed_at = None                             # time.monotonic() the flight ended (for the 90 ms landing squash)

    def flying(self):
        return self.x is not None

    def at_rest(self, now):
        """True once nothing about this entry needs to keep being stepped or drawn specially."""
        if self.flying():
            return False
        if not (self.lift.settled() and abs(self.lift.target) < 0.01):
            return False
        if not (self.scale.settled(0.01) and abs(self.scale.target - 1.0) < 0.01):
            return False
        if self.landed_at is not None and now - self.landed_at < MOTION_IDLE_SECONDS:
            return False
        return True


MOTION_IDLE_SECONDS = 1.0        # a settled, non-flying entry is dropped this long after it last mattered
HOVER_DWELL = 0.06                # the mouse must sit over a *different* hand card this long before the hover switches (hysteresis)
FLIGHT_TIMEOUT = 0.6               # a flight that never settles (window resized mid-flight, say) ends anyway after this long
LANDING_SQUASH = 0.09              # seconds of squash-and-recover after a flight ends

# ---- round 25: departure ghosts, particles, shakes ---------------------------------------------------------------------------------

BOARD_FRAME_BELOW = 200             # a battlefield card shorter than this (times max(1, fs/1.3)) gets a compact board frame
COPYRIGHT_BAND = 0.065              # bottom fraction of a real Scryfall picture kept clear of badges (an estimate - Round 27)

GHOST_MAX = 12                      # more than this and the oldest are dropped, so a big board wipe cannot flood the screen
GHOST_SECONDS = 0.28                 # a permanent leaving the battlefield
DISCARD_GHOST_SECONDS = 0.24         # a card leaving the hand
PARTICLE_PALETTE = {"red": gfx.PARTICLE_RED, "green": gfx.PARTICLE_GREEN, "gold": gfx.PARTICLE_GOLD, "white": gfx.PARTICLE_WHITE,
                     "purple": gfx.PARTICLE_PURPLE,
                     # patch 48: embers (hits, the banner, VICTORY), ash (lands, fizzles, DEFEAT), bone dust (creatures),
                     # the void (exile), magic (spells, enchantments)
                     "ember": gfx.PARTICLE_EMBER, "ash": gfx.PARTICLE_ASH, "bone": gfx.PARTICLE_BONE, "void": gfx.PARTICLE_VOID,
                     "magic": gfx.PARTICLE_MAGIC}
PARTICLE_CAPACITY = 320               # patch 48: was 160; the arrival dust and the spell streams need more in a busy turn
PARTICLE_RADIUS = 5                   # patch 48: at layout scale 1.0 (the round-25 size); scaled by L.fs (ensure_particle_size)
FRONT_PARTICLE_CAPACITY = 256         # patch 48: the pool drawn over the flow layer (banner embers, VICTORY / DEFEAT)
DESTROY_EMBERS = 12                   # patch 48: embers off a permanent going to the graveyard
EXILE_MOTES = 14                      # patch 48: motes draining into the exile tile


class Ghost:
    """A fading picture of a card that just left a zone, gliding from its last on-screen rect to the zone tile it went to (round 25)."""
    __slots__ = ("surf", "start", "end", "t0", "duration", "tint")

    def __init__(self, surf, start, end, t0, duration, tint=None):
        self.surf, self.start, self.end, self.t0, self.duration, self.tint = surf, pygame.Rect(start), pygame.Rect(end), t0, duration, tint


class ForgeTable:
    def __init__(self, session, store=None, prefetch_names=None, settings_path=None, window_size=None, art=None, deck_names=(),
                 launcher=None, deck_dirs=None, saves_dir=None):
        self.session = session
        self.deck_names = set(deck_names or ())      # my own deck's card names (to say 'your deck has Gemstone Caverns' at the coin toss)
        self.launcher = launcher                     # starts games for the deck screen (None: no deck screen possible)
        self.deck_dirs = deck_dirs or (lib.LIBRARY_DIR, lib.SAMPLE_DIR, lib.BASE_DIR)
        self.menu = None                             # the deck screen, when it is showing
        self.boot = None                             # Round AD2: the studio splash / title with the main menu (boot_screens.BootFlow)
        self.title_ok = False                        # Round AD2: started by main(), so the deck screen's Esc can go back to the title
        self.last_run = None                         # round 28d: last_session.begin()'s answer when the last run closed unexpectedly
        self.crash_zip = None                        # round 28d: the report zip about it, once built
        self.crash_job = None                        # round 28d: a reporting.SendJob sending it
        self.overlay = None                          # the settings pop-up or the bug report form: above everything, gets every click first
        self.reporter_name = ""                       # who the bug reports say they are from (remembered in settings.json)
        self.cog_rect = None                         # where the settings cog was drawn
        self.report_folder = None                    # where bug reports are written (None: next to the program; tests point it elsewhere)
        self.deck_choice = {}                        # what was picked there last time (saved in settings.json)
        self.current_decks = None                    # (my deck, [the AI decks]) of the game on screen: what Restart plays again
        self.current_format = formats.DEFAULT        # round FMT1: that game's format (Restart plays it again)
        self.settings_path = settings_path
        self.text_scale = 1.0
        self.fullscreen = False
        self.animations = True              # rings flash and pulse; off = the same rings, standing still (cog: "Animations")
        self.sound = {"on": True, "master": 0.8, "ui": 0.6, "sfx": 0.8, "stinger": 0.8, "music": 0.45, "ambience": 0.6, "hover_tick": True,
                      "music_on": True, "ambience_on": True}        # round AU1: music and ambience, each with its own switch
        self.frames = False                 # round 26: small battlefield cards use an art-crop board frame; default OFF (Karl, OPEN_QUESTIONS A5)
        self.hand_sort_by_type = False      # off = Forge's own hand order (usually draw order); on = grouped by card type (cog: "Sort hand by type")
        self.art_picker_zoom = 1.0          # patch 38: the Card art window's card size (art_picker.ZOOM_STEPS), remembered
        self.auto_pass = True               # Forge passes for me whenever I have nothing to do (Skip window); remembered between runs
                                            # (patch 43: on by default; a settings file from before patch 43 is switched on once)
        self.always_stop = []               # patch 43: card names whose stack items always stop me (Skip window); remembered
        self.speed = "fast"                 # round PRI1: Karl's Speed - "fast" | "slow" (SPEEDS); cog > GAME; remembered
        self._speed_sent = None             # round PRI1: ((session id, speed), when) of the last Speed command not yet seen taken
        self.passing_intro_due = False      # patch 43: say once, at the first game after the update, what passes for you now
        self._ctrl_tap = False              # patch 43: Ctrl (or Ctrl+Shift) pressed and released with nothing else in between
        self._ctrl_shift = False
        self.stats_rec = None               # patch 44: stats.Recorder of the game on the table (None: no game, or watching)
        self._stats_session = None          # the session that recorder follows (a new session = a new game)
        self._stats_state = None            # the last snapshot it was given
        self._stats_over_at = None          # when the game ended without Forge's result yet (it follows the snapshot)
        self.stats_end = None               # the end line of the game just finished (the end-of-game screen shows it)
        self._stats_lines = None            # the end-of-game screen's stats lines, worked out once
        self.stats_decks = None             # ({"id", "name"} of my deck, [same for AI 1, AI 2, ...]) from the deck screen
        self.table_background = "rotate"    # Round AD1: "rotate" (a new picture each game) | one of BG_ORDER | "plain"; cog: Table
        self.table_background_last = None   # the picture the last game started on (None: no game yet, so the first one is BG_ORDER[0])
        self.log_mode = "always"            # patch UI6: "always" | "corner" | "hidden"; cog: Log
        self.preview_mode = "always"        # patch UI6: "always" | "over_card" | "hidden"; cog: Focus
        self.ui_clock = time.monotonic      # patch UI6: the clock the corner log and the over-card picture time themselves by (tests replace it)
        self._log_open = False              # the corner log is showing
        self._log_left = None               # when the mouse left the corner log (it closes LOG_CLOSE_SECONDS later)
        self._over_cand = None              # (key, since): the card the mouse has just moved onto
        self._over_key = None               # the key of the card whose big picture is showing
        self._over_miss = None              # when the mouse left the card whose picture is showing
        self._over_target = None            # (card, rect) the showing picture belongs to
        self.over_rect = None               # where the over-card picture was drawn this frame (None: none)
        self.tour_done = 0                  # Round UX1: the TOUR_VERSION last finished or skipped (settings.json "tour_done")
        self.resumed_game = False           # Round UX1: the game on screen was resumed from a save (never auto-starts the tour)
        self.online_prefs = {}              # Round MP1: settings.json "online": name, port, upnp, address, known_hosts
        self.check_updates = True           # Round 30: an installed copy looks for a newer build on the title screen
        self.skip_update_version = None     # Round 30: "Skip this version" on the update card
        self.windowed_size = DEFAULT_WINDOW
        self.desktop = desktop_size()       # round 27e: the screen this window opens on
        self._display_checked = time.monotonic()
        self._load_settings()
        self.current_bg = self.resting_background()      # what the deck screen shows before any game starts
        self._fit_to_screen = not window_size       # round 27e: a size given by the caller (tests) is used as given
        if window_size:
            self.windowed_size = window_size
        self.screen = None
        icon = fboot.window_icon()                   # Round AD2: the manticore's head in the title bar and the taskbar
        if icon is not None:
            pygame.display.set_icon(icon)
        self._open_window()
        pygame.display.set_caption(f"Commander - Forge table  (v{version.short()})")
        self.clock = pygame.time.Clock()
        self.art = art or (ArtLoader(store, prefetch_names, custom_dir=paths.my_art_dir()) if store else None)
        self.seat_printings = []            # round ALT1: per seat (me, AI 1, ...) {card name lower: (set, cn)} from the deck files
        self._seat_ids = (None, None)       # round ALT1: (players' ids, {player id: seat index}) cached per snapshot layout
        self.audio = None                   # AudioDirector, once its background thread has finished building it (round 24)
        self._start_audio()
        self.particles = mot.ParticlePool(PARTICLE_PALETTE, capacity=PARTICLE_CAPACITY)      # round 25: one pool for the whole program
        self.front_particles = mot.ParticlePool(PARTICLE_PALETTE, capacity=FRONT_PARTICLE_CAPACITY)   # patch 48: over the flow layer
        self.screen_shaker = mot.Shaker(max_px=1.0)             # unit shaker; scaled by 10*fs when read (round 25)
        self.hits = []
        self.mouse = (-1, -1)
        self.modal = None
        self.hover_card = None              # set by dialogs so they can show a large copy of the card under the mouse
        self.running = True
        self.perf = perf.FrameStats()       # frame times and engine reply times (F3 shows them; perf_log.txt and bug reports keep them)
        self.pacer = perf.FramePacer()      # draws only when something changed or moves (round 20)
        self.show_perf = False              # F3
        self._force_draw = True
        self._last_draw_now = None       # time.monotonic() of the last stepped frame (round 23 motion)
        self.perf_log_path = PERF_LOG
        self.journal = gjournal.GameJournal(saves_dir) if saves_dir else None      # None: no journal (tests, and copies without a deck screen)
        self.resuming = None                # a ResumeJob while an unfinished game is being played back (round UNDO1: or a RewindJob)
        self.rewind_points = []             # round UNDO1: the moments of my turns Undo can go back to (rewind.py), this game
        self.rewind_expect = {}             # round UNDO1: point index -> its board (replay.summary), to say what differs
        self.rewind_tracker = grewind.Tracker()
        self.rewind_prestart = None         # round UNDO1: (session, journal contents) started while the Undo window is open
        self._drops_session, self._drops_seen = None, 0      # round UNDO1: the bridge's "dropped" answers already in the journal
        self._points_version = None
        self.vs = None                      # Round AD2b: flow.VsShow - who plays whom, shown while the engine starts
        self.current_deck_labels = None     # Round AD2b: (my deck's name, [each AI deck's name]) of the game on screen, for Restart's VS
        self.current_printings = []         # round ALT1: the printings of the game on screen (Restart plays them again)
        self._art_refused_said = set()      # round ALT1: my_art pictures already toasted as refused
        self.hosting_wait = None            # Round MP1: online_screens.HostWait while hosting and no friend has joined yet
        self.invite_code = None             # Round MP2: the hosted game's invite code, kept for Ctrl+I (someone to watch)
        self.online_save_folder = None      # Round MP2b: saves/online/<id>/ of the game being hosted (online_save.py)
        self.online_save_resumed = False    # ... and whether it is a saved game being continued
        self.online_save_turn = None
        self._hook_session()
        self.L = None
        self.reset_game()
        crashlog.set_context(self.crash_context)      # every crash_log.txt entry says what the table was doing

    def reset_game(self):
        """Everything that belongs to ONE game; called at the start and again for every new game."""
        self.modal = None
        self.overlay = None
        self.pinned = None                  # card id kept in the preview by a right-click
        self.last_preview = None            # the preview keeps showing the last card you looked at
        self.toast = None                   # (text, colour, until)
        self.slots = {}                     # player id -> [Slot]
        self.card_rects = {}                # card id -> rect on screen this frame (for combat lines)
        self.stack_rects = {}               # source card id -> its row in the stack panel this frame
        self.stack_row_rects = {}           # row index (0 = resolves first) -> the visible part of its row this frame
        self.panel_rects = {}               # player id -> that player's panel this frame
        self.log_scroll = 0
        self.log_rect = pygame.Rect(0, 0, 0, 0)
        self.stack_scroll = 0                                     # pixels the stack panel is scrolled (mouse wheel over it)
        self.formatter = flog.LogFormatter()
        self._log_rows = []                 # every readable row so far (kept, so a resize can re-wrap the whole log)
        self._log_lines = []                # the rows cut into lines for the current panel width
        self._log_key = None
        self._log_seen = None
        self.feed = []                      # the newest opponent actions, flashed on the board for a few seconds
        self.life_seen = {}                 # player id -> life at the last snapshot
        self.arrivals = ffx.Arrivals()      # which permanents just entered the battlefield (see forge_fx.py)
        self.auto_pass_sent = None          # my saved auto-pass choice is sent to Forge once per game, with the first snapshot
        self._passed_seen = 0               # patch 43: session.passed messages already in the feed
        self._stop_counts = {}              # patch 43: (turn, card) -> times that card's trigger / ability stopped me this turn
        self._stop_seq = None
        self._offered_yield = set()         # card names already offered "Y = always pass" this game
        self.undo_check = None              # (deadline, board signature) while waiting to see whether Undo did anything
        self.hand_seen = None               # ids of the cards in my hand at the last snapshot (None until the first one)
        self._extra_rows = []               # log rows the table makes itself (Forge does not log draws), waiting to be added to the log
        self.life_flash = {}                # player id -> (change, until): a life total that just moved
        self.pool_rect = None               # where your floating-mana pill was drawn (None when the pool is empty)
        self.spot = None                    # (card name, until): the card the opponent just cast / played glows
        self.mulligans = 0                  # mulligans I have taken this game (read from Forge's 'return N cards' prompts)
        self.answered_prompt = None         # the pre-game prompt a dialog already answered (the old text lingers a few frames)
        self.auto_ok_at = None
        self.shown_over = False
        self.shown_out = False              # the "you are out, the others play on" window (pods only) was shown
        self.seen_infos = 0
        self.started = time.time()
        self.shown_fatal = False
        self.shown_peer_left = False        # Round MP1: "Your friend left" / "The host left" was shown
        self.refused_seen = 0               # Round MP1: the host's engine refused N of my commands (toast once each)
        self.mp2_seen = {"peer_drops": 0, "peer_backs": 0, "net_drops": 0, "reconnects": 0, "specs": []}   # Round MP2: toasts once each
        self.drop_noticed = None            # Round MP2: (time.monotonic(), grace) when a drop was first seen - the banner's countdown
        self._replay_noted = False          # Round MP2b: the "restored up to action N" note was given
        self.router = gevents.EventRouter()  # Forge's game events -> beats for sounds and animations (round 22; used from round 24)
        self.beats_this_frame = []          # the beats that became ready at this sync (a later fold updates a Beat's count in place)
        self.motion = {}                    # card id -> CardMotion: hover lift, press dip and spring flights (round 23)
        self._raw_hover_idx = None          # the hand index the mouse is over right now, before the hysteresis dwell
        self._raw_hover_since = 0.0
        self.hover_hand_index = None        # the hand index actually treated as hovered (after the dwell)
        self.prev_card_rects = {}           # last frame's card_rects, kept one frame for departure ghosts (round 25)
        self._last_cmd_at = None            # time.monotonic() of the newest command sent (not counting pings)
        self._pinged_at = None
        self.not_responding_noted = False   # crash_log.txt got its one "Forge not answering" line for this game
        self._bad_action_seen = 0.0         # round 24: the last session.bad_action_at we already played ui.error for
        self._last_hover_cue_index = None   # round 24: the hand index ui.hover was last played for (never repeats on the same card)
        self.ghosts = []                     # round 25: fading pictures of cards that just left a zone
        self.panel_shakers = {}              # round 25: player id -> Shaker, for a jolt when that player is hit
        self._particle_seen = {}             # round 25: (kind, key) -> monotonic time last spawned, for the max-4-hits-per-100ms rule
        self.end_screen = None               # Round AD2b: flow.EndScreen - VICTORY / DEFEAT before the end-of-game dialog
        self.anim = fanim.Animator()         # Round AD2c: the readability animations (anim.py)
        self.flow_hidden = None              # Round AD2b: the full-screen moment hidden by "View table" (its key), else None
        self.tour = None                     # Round UX1: tour.Tour while the table tour is showing
        self.resumed_game = False

    # ---- settings, window -----------------------------------------------------------

    def _load_settings(self):
        if not self.settings_path or not os.path.isfile(self.settings_path):
            return
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            scale = float(data.get("text_scale", 1.0))
            self.text_scale = min(TEXT_STEPS, key=lambda s: abs(s - scale))
            self.fullscreen = bool(data.get("fullscreen", False))
            self.animations = bool(data.get("animations", True))
            if isinstance(data.get("sound"), dict):
                self.sound.update({k: v for k, v in data["sound"].items() if k in self.sound})
            self.frames = bool(data.get("frames", False))
            self.hand_sort_by_type = bool(data.get("hand_sort_by_type", False))
            try:                                                    # patch 38
                self.art_picker_zoom = max(0.5, min(3.0, float(data.get("art_picker_zoom", 1.0))))
            except (TypeError, ValueError):
                self.art_picker_zoom = 1.0
            self.auto_pass = bool(data.get("auto_pass", True))
            if int(data.get("passing_version", 0) or 0) < PASSING_VERSION:
                # Patch 43: auto-pass used to be off by default, and nearly every saved file says so only because it was.
                # It is switched on once; the Skip window still turns it off, and that choice is kept from then on.
                self.auto_pass = True
                self.passing_intro_due = True
            names = data.get("always_stop")
            self.always_stop = [str(n)[:120] for n in names][:60] if isinstance(names, list) else []
            sp = data.get("speed")                                            # round PRI1: a missing or unknown value is Fast
            self.speed = sp if sp in SPEEDS else "fast"
            bg = data.get("table_background")
            self.table_background = bg if bg in BG_CHOICES else "rotate"         # a missing or unknown value (even "alternate") is Rotate
            last = data.get("table_background_last")
            self.table_background_last = last if last in BG_ORDER + ("plain",) else None
            lm, pm = data.get("log_mode"), data.get("preview_mode")                    # patch UI6: a missing or unknown value is Always
            self.log_mode = lm if lm in LOG_MODES else "always"
            self.preview_mode = pm if pm in PREVIEW_MODES else "always"
            w, h = data.get("window_size", DEFAULT_WINDOW)
            self.windowed_size = (max(MIN_WINDOW[0], int(w)), max(MIN_WINDOW[1], int(h)))
            self._apply_screen_entry(data.get("screens"), self.desktop)
            if isinstance(data.get("decks"), dict):
                self.deck_choice = data["decks"]
            self.reporter_name = str(data.get("reporter_name", ""))[:40]
            done = data.get("tour_done", 0)
            self.tour_done = done if isinstance(done, int) and not isinstance(done, bool) else 0
            self.check_updates = bool(data.get("check_updates", True))            # Round 30
            skip = data.get("skip_update_version")
            self.skip_update_version = skip if isinstance(skip, str) and updater.parse_version(skip) else None
            if isinstance(data.get("online"), dict):         # Round MP1: never holds the game password
                self.online_prefs = {k: v for k, v in data["online"].items() if k in ONLINE_PREF_KEYS}
        except (OSError, ValueError, TypeError) as e:
            print(f"[forge_table] Ignoring unreadable settings file ({e})")

    def save_settings(self):
        if not self.settings_path:
            return
        data = {}
        try:                                # keep whatever else the other table saved in the same file
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            pass
        data.update({"text_scale": self.text_scale, "fullscreen": self.fullscreen, "animations": self.animations,
                     "auto_pass": self.auto_pass, "window_size": list(self.windowed_size), "sound": self.sound,
                     "frames": self.frames, "hand_sort_by_type": self.hand_sort_by_type,
                     "table_background": self.table_background, "table_background_last": self.table_background_last,
                     "log_mode": self.log_mode, "preview_mode": self.preview_mode,
                     "art_picker_zoom": self.art_picker_zoom,
                     "passing_version": PASSING_VERSION, "always_stop": list(self.always_stop), "speed": self.speed})
        key = screen_key(self.desktop)
        if key:                             # round 27e: each screen size keeps its own window and text size
            screens = data.get("screens") if isinstance(data.get("screens"), dict) else {}
            screens[key] = {"window_size": list(self.windowed_size), "text_scale": self.text_scale, "fullscreen": self.fullscreen}
            data["screens"] = screens
        if self.deck_choice:
            data["decks"] = self.deck_choice
        if self.reporter_name:
            data["reporter_name"] = self.reporter_name
        if self.tour_done:
            data["tour_done"] = self.tour_done          # Round UX1
        data["check_updates"] = bool(self.check_updates)          # Round 30
        if self.skip_update_version:
            data["skip_update_version"] = self.skip_update_version
        else:
            data.pop("skip_update_version", None)
        if self.online_prefs:
            data["online"] = {k: v for k, v in self.online_prefs.items() if k in ONLINE_PREF_KEYS}
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            print(f"[forge_table] Could not save settings: {e}")

    def _apply_screen_entry(self, screens, desktop):
        """Use what was remembered for this screen size (round 27e), when there is something."""
        entry = screens.get(screen_key(desktop)) if isinstance(screens, dict) and desktop else None
        if not isinstance(entry, dict):
            return False
        try:
            w, h = entry.get("window_size", self.windowed_size)
            self.windowed_size = (max(MIN_WINDOW[0], int(w)), max(MIN_WINDOW[1], int(h)))
            if "text_scale" in entry:
                self.text_scale = min(TEXT_STEPS, key=lambda s: abs(s - float(entry["text_scale"])))
            if "fullscreen" in entry:
                self.fullscreen = bool(entry["fullscreen"])
        except (TypeError, ValueError):
            return False
        return True

    def check_display(self, now=None, force=False):
        """Round 27e: notice a changed desktop (Remote Desktop connected or disconnected, a new resolution) and follow it: remember
        the old screen's sizes, take the new screen's (or fit the window to it), and reopen the window. True when it changed."""
        now = time.monotonic() if now is None else now
        if not force and now - self._display_checked < DISPLAY_CHECK_SECONDS:
            return False
        self._display_checked = now
        desktop = desktop_size()
        if not desktop or desktop == self.desktop:
            return False
        self.save_settings()                # the old screen's entry, under the old size
        self.desktop = desktop
        screens = None
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                screens = json.load(f).get("screens")
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._apply_screen_entry(screens, desktop)
        self.windowed_size = fit_window(self.windowed_size, desktop)
        self._open_window()
        gfx.clear_caches()
        self._force_draw = True
        self.say(f"Screen changed to {desktop[0]}x{desktop[1]}: window resized.", DIM)
        return True

    def _open_window(self):
        if self._fit_to_screen:             # round 27e: a remembered size is never bigger than the screen
            self.windowed_size = fit_window(self.windowed_size, self.desktop)
        if self.fullscreen:
            try:
                self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                return
            except pygame.error as e:
                print(f"[forge_table] Fullscreen failed ({e}); using a window")
                self.fullscreen = False
        self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)

    def toggle_fullscreen(self):
        if not self.fullscreen:
            self.windowed_size = self.screen.get_size()
        self.fullscreen = not self.fullscreen
        self._force_draw = True
        self._open_window()
        gfx.clear_caches()
        self.say("Fullscreen on (F11 to leave)." if self.fullscreen else "Windowed.", DIM)
        self.save_settings()

    def toggle_animations(self):
        self.animations = not self.animations
        self._force_draw = True
        self.say("Animations on: rings flash and pulse." if self.animations else
                 "Animations off: the rings stay, but they stand still.", DIM)
        self.save_settings()

    # ---- sound (round 24) -----------------------------------------------------------

    def _start_audio(self):
        """Synthesising every cue takes about a second: build the AudioDirector on a background thread so start-up is not delayed.
        One process only ever needs one director (a new game does not need its sounds resynthesised), so it is built once and
        shared by every ForgeTable in this process -- also what keeps repeatedly making tables in the test suite fast.
        NOT VERIFIED: the spec's caution about creating pygame.mixer.Sound objects off the main thread on Windows was not tested
        in this sandbox (no display/audio device here) -- if sound is silent, glitchy, or crashes on Karl's PC, that thread-safety
        assumption is the first thing to check; the safer split (build the raw sample arrays on the thread, create the
        pygame.mixer.Sound objects on the main thread in sync()) described in the spec was not implemented."""
        with _AUDIO_SHARED["lock"]:
            need_build = not _AUDIO_SHARED["building"]
            _AUDIO_SHARED["building"] = True
        if need_build:
            threading.Thread(target=self._build_shared_audio, daemon=True, name="audio").start()
        else:
            threading.Thread(target=self._wait_for_shared_audio, daemon=True, name="audio-wait").start()

    def _build_shared_audio(self):
        try:
            d = audio.AudioDirector(CUES_FILE, SOUNDS_DIR)
        except Exception as e:                      # sound must never be able to stop the game from starting
            print(f"[forge_table] Sound did not start: {e}")
            d = None
        _AUDIO_SHARED["director"] = d
        _AUDIO_SHARED["event"].set()
        self.audio = d
        self._apply_sound_volumes()

    def _wait_for_shared_audio(self):
        """A second (or later) ForgeTable in the same process: wait for whichever one is already building it, instead of
        synthesising every cue again."""
        _AUDIO_SHARED["event"].wait(timeout=10)
        self.audio = _AUDIO_SHARED["director"]
        self._apply_sound_volumes()

    SOUND_SWITCHES = ("on", "hover_tick", "music_on", "ambience_on")

    def _apply_sound_volumes(self):
        if self.audio:
            self.audio.volumes.update({k: v for k, v in self.sound.items() if k not in self.SOUND_SWITCHES})
            self.audio.streams_on = {"music": self.sound.get("music_on", True), "ambience": self.sound.get("ambience_on", True)}

    def toggle_stream(self, key):
        """Cog > Music / Ambience (round AU1)."""
        self.sound[key] = not self.sound.get(key, True)
        self._apply_sound_volumes()
        what = "Music" if key == "music_on" else "Ambience"
        self.say(f"{what} {'on' if self.sound[key] else 'off'}.", DIM)
        self.save_settings()

    def soundscape(self):
        """(ambience cue, music mode) for what is on screen (round AU1). Music mode None = silence, "keep" = leave it alone (the
        VICTORY / DEFEAT sting is playing out)."""
        boot = self.boot
        if boot is not None:
            return (None, None) if boot.stage == "splash" else ("amb.title", "title")
        if self.game_showing():
            amb = f"amb.{self.current_bg}" if f"amb.{self.current_bg}" in getattr(self.audio, "cues", {}) else "amb.plain"
            if self.vs is not None and self.vs.holding(self):
                return amb, None                                        # the VS screen: the commanders, a bell, quiet
            return amb, ("game" if self.game_in_progress() else "keep")
        if self.menu is not None or self.title_ok:
            return "amb.title", "title"
        return None, None

    def toggle_sound(self):
        self.sound["on"] = not self.sound["on"]
        self.say("Sound on (M)." if self.sound["on"] else "Sound off (M).", DIM)
        self.save_settings()

    def change_sound_volume(self, delta):
        v = round(max(0.0, min(1.0, self.sound.get("master", 0.8) + delta)), 2)
        self.sound["master"] = v
        if self.audio:
            self.audio.set_volume("master", v)
        self.say(f"Volume {round(v * 100)}%", DIM)
        self.save_settings()

    def toggle_hover_tick(self):
        self.sound["hover_tick"] = not self.sound.get("hover_tick", True)
        self.save_settings()

    def toggle_check_updates(self):
        """Round 30: the cog's Updates switch. Says what it means for this copy (a git copy never looks)."""
        self.check_updates = not self.check_updates
        self.save_settings()
        if not self.check_updates:
            self.say("Updates off: this copy won't look for a new version.", DIM)
            return
        ok, why = updater.enabled(True)
        self.say("Updates on: the title screen looks for a new version." if ok else "Updates on, but " + why + ".", DIM)

    def toggle_frames(self):
        self.frames = not self.frames
        gfx.clear_caches()
        self._force_draw = True
        self.say("Compact board cards on." if self.frames else "Compact board cards off.", DIM)
        self.save_settings()

    def toggle_hand_sort(self):
        self.hand_sort_by_type = not self.hand_sort_by_type
        self._force_draw = True
        self.say("Hand sorted by type." if self.hand_sort_by_type else "Hand back in Forge's own order.", DIM)
        self.save_settings()

    def pan_for(self, cid):
        rect = self.card_rects.get(cid) if cid is not None else None
        if rect and self.L and self.L.W:
            return max(-1.0, min(1.0, (rect.centerx / self.L.W) * 2 - 1))
        return 0.0

    def play_cue(self, cue_id, pan=0.0, gain_db=0.0):
        if not self.sound.get("on", True) or not self.audio or not self.audio.ready:
            return False
        return self.audio.play(cue_id, time.monotonic(), pan, gain_db)

    def apply_audio(self):
        """Once a tick: let ducking/finished voices update, note a bad_action for ui.error, and turn this frame's beats into cues
        (round 24). Runs every tick, not only drawn frames -- sound should never wait on the frame pacer."""
        now = time.monotonic()
        if self.audio:
            a = self.audio
            a.muted = not self.sound.get("on", True)
            amb, music = self.soundscape()
            a.set_ambience(amb)
            if music != "keep":
                a.set_music(music, now)
            if self.boot is not None and self.boot.stage == "splash" and not getattr(self.boot, "sound_done", False) and a.ready:
                self.boot.sound_done = True
                self.play_cue("boot.splash")
            a.update(now)
        ba = getattr(self.session, "bad_action_at", 0.0)
        if ba and ba != self._bad_action_seen:
            self._bad_action_seen = ba
            self.play_cue("ui.error")
        if not self.sound.get("on", True) or not self.beats_this_frame:
            return
        me = self.session.me()
        my_id = me["id"] if me else None
        my_name = me.get("name") if me else None
        for b in self.beats_this_frame:
            if b.stale:
                continue
            self._play_beat(b, my_id, my_name)

    def _play_beat(self, b, my_id, my_name):
        d = b.last
        k = b.kind
        if k == "zone":
            frm, to = d.get("from"), d.get("to")
            if to == "Hand" and frm == "Library":
                self.play_cue("card.draw", self.pan_for(b.card))
            elif frm == "Hand" and to == "Stack":
                self.play_cue("card.play", self.pan_for(b.card))
            elif to == "Battlefield" and frm != "Battlefield":
                card = self.session.card(b.card) if b.card is not None else None
                self.play_cue("land.play" if card and card.get("isLand") else "card.land", self.pan_for(b.card))
            elif frm == "Hand" and to in ("Graveyard", "Library"):
                self.play_cue("card.discard", self.pan_for(b.card))
            elif frm == "Battlefield" and to == "Graveyard":
                self.play_cue("destroy", self.pan_for(b.card))
            elif to == "Exile":
                self.play_cue("exile", self.pan_for(b.card))
        elif k == "sacrifice":
            self.play_cue("destroy", self.pan_for(b.card))
        elif k == "shuffle":
            self.play_cue("deck.shuffle")
        elif k == "tap":
            if b.flurry:
                self.play_cue("flurry")
            else:
                self.play_cue("tap" if d.get("tapped") else "untap", self.pan_for(b.card))
        elif k == "cast":
            self.play_cue("ability.trigger" if d.get("trigger") else "spell.cast", self.pan_for(d.get("card")))
        elif k == "resolve":
            self.play_cue("spell.fizzle" if d.get("fizzled") else "spell.resolve", self.pan_for(b.card))
        elif k == "damage_card":
            self.play_cue("damage.creature", self.pan_for(b.card), gain_db=min(6, d.get("amount", 1)) - 3)
        elif k == "damage_player":
            # patch 47: louder for a bigger hit, as damage.creature already is (-2 dB for 1 point, +3 dB from 6 up)
            self.play_cue("damage.player", gain_db=min(6, d.get("amount") or 1) - 3)
        elif k == "life":
            if (d.get("new", 0) or 0) > (d.get("old", 0) or 0):
                self.play_cue("life.gain")
            elif not any(o.kind == "damage_player" and o.last.get("player") == d.get("player") for o in self.beats_this_frame):
                self.play_cue("life.loss")
        elif k == "counters":
            new, old = d.get("new"), d.get("old")
            if new is not None and old is not None and new != old:
                self.play_cue("counter.add" if new > old else "counter.remove", self.pan_for(b.card))
        elif k == "token":
            self.play_cue("token.create")
        elif k == "attack":
            if d.get("attackers"):
                self.play_cue("combat.attack")
        elif k == "block":
            if d.get("pairs"):
                self.play_cue("combat.block")
        elif k == "turn":
            self.play_cue("phase.turn_mine" if d.get("player") == my_id else "phase.turn_theirs")
        elif k == "outcome":
            winner = d.get("winner")
            won = winner is not None and winner in (my_id, my_name)
            if self.audio:
                self.audio.stop_bus("sfx")                       # cut the table's sounds - not the bell that says who won (round AU1)
                self.audio.music_sting_after("music.victory" if won else "music.defeat", time.monotonic(), 2.5)
            self.play_cue("game.win" if won else "game.lose")

    # ---- departure ghosts, particles and shakes (round 25) --------------------------------------------------------------

    def zone_tile_rect(self, player_id, zone):
        """Where a departure ghost should aim: the tile actually drawn for that player's library/graveyard/exile this frame
        (recorded only as a hit -- draw_player_panel never keeps the tile rects themselves), else that player's panel."""
        kind = "library" if zone == "library" else "zone"
        for rect, k, data in self.hits:
            if k == kind and data.get("player") == player_id and (kind == "library" or data.get("zone") == zone):
                return pygame.Rect(rect)
        return self.panel_rects.get(player_id)

    def hand_target_rect(self, player_id):
        me = self.session.me()
        if me is not None and player_id == me["id"]:
            return self.L.hand
        return self.panel_rects.get(player_id)

    def ensure_particle_size(self, fs):
        """Patch 48: the particle sprites grow with the layout's scale (a 5 px dot at 1280x800 is lost on a 4K screen). The pools
        pre-render their sprites once, so they are remade when the size changes (a resize or a text-size step: the few particles
        alive at that moment are dropped)."""
        radius = max(PARTICLE_RADIUS, int(round(PARTICLE_RADIUS * fs)))
        if self.particles.radius != radius:
            self.particles = mot.ParticlePool(PARTICLE_PALETTE, capacity=PARTICLE_CAPACITY, radius=radius)
            self.front_particles = mot.ParticlePool(PARTICLE_PALETTE, capacity=FRONT_PARTICLE_CAPACITY, radius=radius)

    def spawn_ghost(self, cid, start, end, duration, tint=None):
        """A fading picture of `cid` gliding from `start` to `end`. Skipped with animations off or when either rect is
        unknown (the card was never on screen, or the destination tile isn't drawn this frame). Capped at GHOST_MAX; the
        oldest is dropped first so a board wipe cannot flood the screen. The tinted picture is cached, not copied per ghost."""
        if not self.animations or not start or not end:
            return
        card = self.session.card(cid)
        if not card:
            return
        w, h = max(1, start.w), max(1, start.h)
        base_key, base = self.card_surface_keyed(card, w, h, plate=True)
        surf = base
        if tint:
            key = ("ghost", tint, base_key)
            surf = gfx.recall(key)
            if surf is None:
                surf = base.copy()
                if tint == "red":
                    surf.fill((255, 120, 120), special_flags=pygame.BLEND_RGB_MULT)
                else:                                       # a white fade for exile
                    surf.fill((60, 60, 60), special_flags=pygame.BLEND_RGB_ADD)
                gfx.remember(key, surf)
        if len(self.ghosts) >= GHOST_MAX:
            self.ghosts.pop(0)
        self.ghosts.append(Ghost(surf, start, end, time.monotonic(), duration, tint))
        fs = max(1.0, self.L.fs)
        if tint == "red":                                   # patch 48: a destroyed permanent throws embers as it goes
            self.particles.spawn(DESTROY_EMBERS, start.centerx, start.centery, "ember", speed=170 * fs, spread=math.pi * 2,
                                 life=0.55, gravity=220 * fs)
        elif tint == "white":                               # patch 48: exile drains it into the void
            self.particles.stream(EXILE_MOTES, start.centerx, start.centery, end.centerx, end.centery, "void",
                                  speed=max(300.0, 2.2 * math.hypot(end.centerx - start.centerx, end.centery - start.centery) / max(0.2, duration)))

    def draw_ghosts(self):
        now = time.monotonic()
        alive = []
        for g in self.ghosts:
            if now - g.t0 >= g.duration:
                continue
            e = mot.ease_in_cubic(mot.clamp01((now - g.t0) / g.duration))
            x = mot.lerp(g.start.centerx, g.end.centerx, e)
            y = mot.lerp(g.start.centery, g.end.centery, e)
            alpha = max(0, min(255, int(255 * (1 - e))))
            g.surf.set_alpha(alpha)                          # a cached surface -- reset every blit, never copied per ghost
            self.screen.blit(g.surf, g.surf.get_rect(center=(int(x), int(y))))
            alive.append(g)
        self.ghosts = alive

    def apply_effects(self, now):
        """Once a tick: this frame's beats spawn departure ghosts, particles and panel/screen shakes. Nothing here runs with
        animations off, and a stale beat (superseded before its flurry delay elapsed) is skipped, exactly like apply_audio."""
        if not self.animations:
            return
        if self.state:
            self.anim.feed(self, self.beats_this_frame, now)      # Round AD2c: spotlight, floating numbers, tap turns
            self.anim.watch(self, now)                           # Round AD2c: turn banner, the flush when I am asked, line births
        if not self.beats_this_frame:
            return
        me = self.session.me()
        my_id = me["id"] if me else None
        my_name = me.get("name") if me else None
        for b in self.beats_this_frame:
            if not b.stale:
                self._play_effect(b, my_id, my_name, now)

    def _play_effect(self, b, my_id, my_name, now):
        d, k = b.last, b.kind
        if k == "zone":
            cid = b.card
            frm, to = d.get("from"), d.get("to")
            card = self.session.card(cid) if cid is not None else None
            owner = card.get("controller") if card else None
            if frm == "Battlefield" and to in ("Graveyard", "Exile", "Hand", "Library"):
                start = self.prev_card_rects.get(cid)
                end = self.hand_target_rect(owner) if to == "Hand" else self.zone_tile_rect(owner, to.lower())
                tint = "red" if to == "Graveyard" else ("white" if to == "Exile" else None)
                self.spawn_ghost(cid, start, end, GHOST_SECONDS, tint)
            elif frm == "Hand" and to in ("Graveyard", "Library"):
                start = self.prev_card_rects.get(cid) or self.L.hand
                self.spawn_ghost(cid, start, self.zone_tile_rect(owner, to.lower()), DISCARD_GHOST_SECONDS,
                                  "red" if to == "Graveyard" else None)
            elif frm == "Library" and to == "Hand" and owner == my_id:
                self.start_flight(cid, self.zone_tile_rect(owner, "library"))     # round 23's flight spring, from the library tile
        elif k == "sacrifice":
            cid = b.card
            card = self.session.card(cid) if cid is not None else None
            owner = card.get("controller") if card else None
            self.spawn_ghost(cid, self.prev_card_rects.get(cid), self.zone_tile_rect(owner, "graveyard"), GHOST_SECONDS, "red")
        elif k == "damage_player":
            pid, amount = d.get("player"), d.get("amount") or 1
            sh = self.panel_shakers.setdefault(pid, mot.Shaker(max_px=6.0))
            sh.add(min(1.0, 0.35 * amount / 5), now)
            if pid == my_id and amount >= 10:
                self.screen_shaker.add(1.0, now)
            rect = self.panel_rects.get(pid)
            if rect:
                self.particles.spawn(6, rect.centerx, rect.centery, "red", speed=140, life=0.5, gravity=180,
                                      angle=-math.pi / 2, spread=math.pi / 2)
        elif k == "life":
            pid, new, old = d.get("player"), d.get("new"), d.get("old")
            rect = self.panel_rects.get(pid)
            if rect is None or new is None or old is None or new == old:
                return
            if new > old:
                self.particles.spawn(8, rect.centerx, rect.centery, "green", speed=90, life=0.6, gravity=-60,
                                      angle=-math.pi / 2, spread=math.pi / 3)
            else:
                self.particles.spawn(8, rect.centerx, rect.centery, "red", speed=90, life=0.6, gravity=140,
                                      angle=math.pi / 2, spread=math.pi / 3)
                already = any(o.kind == "damage_player" and o.last.get("player") == pid for o in self.beats_this_frame)
                if pid == my_id and old - new >= 10 and not already:      # damage_player already shook the screen for this hit
                    self.screen_shaker.add(1.0, now)
        elif k == "counters":
            new, old = d.get("new"), d.get("old")
            rect = self.card_rects.get(b.card)
            if rect and new is not None and old is not None and new > old:
                self.particles.spawn(3, rect.centerx, rect.centery, "gold", speed=70, life=0.5, gravity=40)
        elif k == "token":
            pass                                            # patch 48: the token's puff is its arrival aura (anim.draw_auras), spawned once the table knows where it is
        elif k == "outcome":
            winner = d.get("winner")
            if winner is not None and winner not in (my_id, my_name):
                self.screen_shaker.add(1.0, now)

    def resize_window(self, size):
        if self.fullscreen:
            return
        self.windowed_size = (max(MIN_WINDOW[0], size[0]), max(MIN_WINDOW[1], size[1]))
        self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)
        self._force_draw = True
        self.save_settings()

    def background_label(self):
        return BG_LABELS.get(self.table_background, "Rotate")

    def resting_background(self):
        """The picture behind the deck screen while no game has started: the one chosen in Settings, or (Rotate) the one the last game
        used - the first ever is the first in BG_ORDER."""
        if self.table_background != "rotate":
            return self.table_background
        return self.table_background_last or BG_ORDER[0]

    def start_background(self):
        """Once at the start of every game (Start, Restart, Resume): choose the picture. Rotate moves on to the next one in BG_ORDER
        after the last game's; any other setting uses that one. Remembered in settings.json."""
        if self.table_background == "rotate":
            last = self.table_background_last
            name = BG_ORDER[(BG_ORDER.index(last) + 1) % len(BG_ORDER)] if last in BG_ORDER else BG_ORDER[0]
        else:
            name = self.table_background
        self.current_bg = name
        self.table_background_last = name
        self.save_settings()

    def change_background(self, direction=1):
        """Cog > Table [<] [>]: Rotate, Graveyard, Cathedral, Citadel, Ruins, Plain, round again. A fixed picture shows at once; Rotate
        keeps what is on screen and only moves on when the next game starts."""
        i = BG_CHOICES.index(self.table_background)
        self.table_background = BG_CHOICES[(i + direction) % len(BG_CHOICES)]
        if self.table_background != "rotate":
            self.current_bg = self.table_background
        self.save_settings()

    def log_label(self):
        return LOG_LABELS.get(self.log_mode, "Always")

    def preview_label(self):
        return PREVIEW_LABELS.get(self.preview_mode, "Always")

    def change_log_mode(self, direction=1):
        """Cog > Log: Always -> Corner -> Hidden, round again (left click goes on, right click goes back). Hidden means not drawn, never
        not recorded: the log keeps collecting every line and the feed and bug reports are unchanged."""
        i = LOG_MODES.index(self.log_mode if self.log_mode in LOG_MODES else "always")
        self.log_mode = LOG_MODES[(i + direction) % len(LOG_MODES)]
        self._log_open, self._log_left = False, None
        self._force_draw = True
        self.save_settings()

    def change_preview_mode(self, direction=1):
        """Cog > Focus: Always -> Over card -> Hidden, round again (right click goes back)."""
        i = PREVIEW_MODES.index(self.preview_mode if self.preview_mode in PREVIEW_MODES else "always")
        self.preview_mode = PREVIEW_MODES[(i + direction) % len(PREVIEW_MODES)]
        self._over_cand = self._over_key = self._over_miss = self._over_target = None
        self._force_draw = True
        self.save_settings()

    def change_text_scale(self, direction=0):
        i = TEXT_STEPS.index(self.text_scale)
        j = 0 if direction == 0 else clamp(i + direction, 0, len(TEXT_STEPS) - 1)
        if j == i and direction != 0:
            self.say(f"Text size is already {'maximum' if direction > 0 else 'minimum'} ({round(self.text_scale * 100)}%).", DIM)
            return
        self.text_scale = TEXT_STEPS[j]
        self.say(f"Text size {round(self.text_scale * 100)}%", DIM)
        self.save_settings()

    def say(self, text, colour=TEXT, seconds=3.0):
        self.toast = (text, colour, time.time() + seconds)
        self._force_draw = True

    def _hook_session(self, journal=True):
        """Every command sent to Forge: time it until its answer arrives (the F3 overlay's 'engine reply' figure) and write it into the game
        journal (round 21). journal=False while a resumed game is being played back: those commands are already in the journal."""
        jr = self.journal if journal else None
        crashlog.set_engine_log(getattr(self.session, "stderr_path", None))     # round UNDO1: reports read this engine's log

        def on_send(cmd):
            if cmd.get("c") == "flush":                   # our own "are you there?" ping: not a move, not timed, not journaled
                return
            now = time.monotonic()
            self.perf.command_sent(now)
            self._last_cmd_at, self._pinged_at = now, None
            if jr is not None and jr.active() and cmd.get("c") != "quit":
                jr.command(cmd, time.time())
        self.session.on_send = on_send

    def start_journal(self):
        """A new game started: open a fresh journal with what it takes to play it again (seed, deck files, player name, code version)."""
        if self.journal is None:
            return
        s = self.session
        if getattr(s, "online", None):
            return                                         # Round MP1: online games aren't journalled (MP2 adds resume)
        decks = {}
        for label, path in [("player.dck", getattr(s, "deck_path", ""))] + [(f"opponent{i}.dck", p) for i, p in
                                                                            enumerate(getattr(s, "opponent_paths", []) or [], 1)]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    decks[label] = f.read()
            except (OSError, TypeError):
                return                                     # no deck files (an idle session): nothing that could be replayed
        try:
            self.journal.start(getattr(s, "seed", None), getattr(s, "name", ""), decks, version.code_fingerprint(), time.time(),
                               replay=self.replay_key(),
                               printings=[{n: list(p) for n, p in m.items()} for m in getattr(self, "current_printings", None) or []],
                               fmt=self.game_format(), stats=getattr(s, "stats_info", None), speed=self.speed)
        except OSError as e:
            print(f"[forge_table] Could not start the game journal: {e}")
        self.load_rewind_points()                  # round UNDO1: a new game has none yet

    def end_journal(self, result):
        if self.journal is not None and self.journal.active():
            try:
                self.journal.end(result, time.time())
            except OSError:
                pass

    def game_format(self):
        """Round FMT1: the format of the game on screen ("commander" or "brawl")."""
        return formats.normal(getattr(self.session, "fmt", None))

    def format_line(self):
        """Round FMT1: one line about a non-Commander game's rules for the loading screen ("" for Commander)."""
        fmt = self.game_format()
        if fmt == formats.DEFAULT:
            return ""
        players = 1 + len(getattr(self.session, "opponent_paths", None) or [])
        damage = "" if formats.get(fmt).commander_damage else ", no commander damage"
        return f"{formats.name(fmt)} - {formats.starting_life(fmt, players)} life each{damage}."

    def replay_key(self):
        """Round 28d: what must match for a saved game to be played back (journal.replay_matches): the Forge build and the
        bridge jar's source stamp (None when unreadable)."""
        runtime = getattr(self.launcher, "runtime", None) or getattr(self.session, "runtime", None)
        return {"forge": version.forge_build(runtime), "bridge": fc.bridge_stamp(runtime)}

    def discard_last_game(self):
        """Round 28d: the deck screen's Discard for a saved game this copy can't replay."""
        if self.journal is not None:
            try:
                self.journal.discard(time.time())
            except OSError:
                pass
        if self.menu:
            self.menu.resume_info = None
        self.say("The saved game was put aside (it is kept in saves/finished).", DIM, 5.0)

    def unfinished_game(self):
        """(start, commands) of a game that was never finished (the program closed or crashed), or None."""
        if self.journal is None:
            return None
        try:
            return gjournal.unfinished(self.journal.folder)
        except OSError:
            return None

    # ---- state shortcuts -------------------------------------------------------------

    @property
    def state(self):
        return self.session.state

    def short_player(self, name):
        return short_name(name)

    def focus_busy(self):
        """Round AD2c: the focus panel is showing something the player asked for (a hovered or pinned card, the card a question is
        about), so the AI-action spotlight stays out of it."""
        if self.preview_mode != "always":                       # patch UI6: only a question's card uses the card slot under the stack
            return self.aux_forced() is not None
        kind, data = self.hit_at(self.mouse) if not self.modal else (None, None)
        if kind in ("card", "stack", "zone") and data.get("card") or kind == "logcard":
            return True
        if self.pinned is not None or self.prompt_library_card() is not None:
            return True
        return bool(commander_zone_name(((self.state or {}).get("prompt") or {}).get("message")))

    def round_of(self, turn):
        return round_number(turn, len((self.state or {}).get("players") or ()))

    def asks_me(self):
        """Round AD2c: Forge wants a decision from me other than a plain priority pass (anim.py cuts its queue then)."""
        if self.session.requests or self.modal is not None:
            return True
        if not self.state or self.waiting():
            return False
        kind = self.input_kind()
        if kind is not None:
            return kind != "priority"
        return not str(self.prompt().get("message", "")).startswith("Priority")

    def flow_key(self):
        """Which full-screen moment is up now (Round AD2b), so "View table" hides that one only: the next one shows again."""
        if self.end_screen is not None:
            return ("end", self.end_screen.kind)
        kind = flow.pregame_kind(self)
        return ("pregame", kind, self.mulligans) if kind else None

    def font(self, key, bold=False):
        # Round AD1: headings ("title") and big buttons ("btn") use the display face; every other key is body text,
        # including "big", which draws numbers (the display face draws 0 as a slashed zero: K3).
        return get_font(self.L.px[key], bold, "display" if key in DISPLAY_KEYS else "body")

    def ui_font(self, key, bold=False):
        """Round 32 (Karl, 3 Oct 2026: "Menus and UI elements should be in the Avqest font"): the display face at the size of `key`,
        for what is chrome rather than content - every button, the settings pop-up's words, the phase bar, the prompt's headline.
        Digits in it are drawn in the body face (gfx.DisplayFont, K3); under gfx.DISPLAY_MIN_PX it is the body font."""
        return get_font(self.L.px[key], bold, "display")

    def my_id(self):
        return (self.state or {}).get("me")

    def prompt(self):
        return (self.state or {}).get("prompt") or {}

    def target_prompt(self):
        """The parsed 'choose a target for <source>' prompt while Forge is asking for one, else None."""
        p = self.prompt()
        kind = self.input_kind()
        if kind is not None and kind != "target":
            return None
        if kind is None and not p.get("selecting"):
            return None
        return parse_target_prompt(p.get("message", ""))

    def waiting(self):
        """True while the engine is running the AI's turn or otherwise not asking you anything."""
        p = self.prompt()
        msg = p.get("message", "")
        if any(pl.get("selectable") for pl in (self.state or {}).get("players") or []):
            return False                    # round 28d: "choose a player" - OK is grey until you pick, but you ARE being asked
        return msg.startswith("Waiting for") or (not p.get("ok", {}).get("enabled") and not p.get("cancel", {}).get("enabled")
                                                  and not p.get("selecting"))

    def find_card(self, cid):
        return self.session.card(cid)

    # ---- layout ---------------------------------------------------------------------

    def make_layout(self):
        W, H = self.screen.get_size()
        fs = clamp(min(W / 1280, H / 800), 0.8, 1.7) * (0.85 + 0.15 * self.text_scale) * self.text_scale ** 0.55
        px = {k: max(8, int(v * fs)) for k, v in FONT_BASE.items()}
        L = SimpleNamespace(W=W, H=H, fs=fs, px=px)
        self.ensure_particle_size(fs)
        L.margin = int(clamp(8 * fs, 6, 14))
        L.top_h = int(max(58, 62 * fs))
        L.right_w = int(clamp(W * 0.26 * (0.9 + 0.1 * self.text_scale), 260, 540 * max(1.0, fs / 1.3)))
        L.right_w_full = L.right_w                                  # patch UI6: what the column measures with everything in it
        if self.preview_mode != "always" and self.log_mode != "always":
            L.right_w = min(L.right_w, max(260, int(L.right_w * NARROW_RIGHT)))      # only the stack lives in the column now
        m = L.margin
        # Karl's request (2026-09-22): once the match is over the top bar has nothing left to show (no turn, no phase
        # pills) except the settings cog, so the board and the focus card/log column no longer need to leave room for
        # it - they can start right under the window's own edge instead of under the full-height bar. The cog itself
        # stays exactly where it always was; draw_frame() repaints it after the card so it is never hidden behind it.
        L.game_over = bool(self.state) and bool(self.session.game_over or self.state.get("gameOver"))
        top_gap = m if L.game_over else (L.top_h + m)
        L.main = pygame.Rect(m, top_gap, W - L.right_w - 3 * m, H - top_gap - m)
        L.right = pygame.Rect(W - L.right_w - m, top_gap, L.right_w, L.main.h)
        opps = self.session.opponents()
        n = max(1, len(opps))
        avail = L.main.h
        opp_frac = {1: 0.30, 2: 0.34, 3: 0.38}.get(n, 0.4)
        opp_total = int(avail * opp_frac)
        L.opp_h = (opp_total - m * (n - 1)) // n
        my_total = avail - opp_total - m
        L.bar_h = int(max(54, 58 * fs))
        L.hand_ch = int(clamp(L.main.h * (0.20 if n == 1 else 0.17), 90, 240 * max(1.0, fs)))
        L.lift = int(L.hand_ch * 0.12)
        L.my_bf_h = my_total - L.bar_h - L.hand_ch - L.lift - 2 * m
        pad = int(clamp(6 * fs, 4, 12))
        L.pad = pad
        L.my_row_h = int(clamp((L.my_bf_h - 3 * pad) / 2, 60, 230 * max(1.0, fs)))
        L.opp_row_h = int(min((L.opp_h - 3 * pad) / 2, L.my_row_h * 0.82))
        L.info_w = int(clamp(190 * fs, 150, 330 * max(1.0, fs / 1.5)))
        L.opp_info_w = L.info_w                                     # an opponent's panel keeps its width (its piles share one row)
        L.my_info_w = L.info_w if L.main.w * 0.24 < 300 else min(L.info_w, max(int(L.info_w * 0.75), int(L.main.w * 0.24)))  # Round UI2: at big text my panel took the action bar's room
        L.opp_rects = []
        y = L.main.y
        for _ in range(n):
            L.opp_rects.append(pygame.Rect(L.main.x, y, L.main.w, L.opp_h))
            y += L.opp_h + m
        L.my_bf = pygame.Rect(L.main.x, y, L.main.w, L.my_bf_h)
        y = L.my_bf.bottom + m
        L.my_info = pygame.Rect(L.main.x, y, L.my_info_w, L.bar_h + L.hand_ch + L.lift + m)
        hand_cw = int(L.hand_ch * CARD_ASPECT)
        me = self.session.me()
        ncmd = max(1, len((me or {}).get("commanders") or []))
        L.cmd_ch = L.hand_ch - 6                       # the commander card is a hair smaller than a hand card
        L.cmd_cw = int(L.cmd_ch * CARD_ASPECT)
        cmd_w = 0 if (me and self.commanders_on_board(me)) else ncmd * (L.cmd_cw + 6) + 2 * pad
        L.cmd_rect = pygame.Rect(L.my_info.right + m, y, cmd_w, L.bar_h + L.hand_ch + L.lift + m)
        left = L.cmd_rect.right + m if cmd_w else L.my_info.right + m
        L.bar = pygame.Rect(left, y, L.main.right - left, L.bar_h)
        L.hand = pygame.Rect(left, y + L.bar_h + m, L.main.right - left, L.hand_ch + L.lift)
        # right column: preview, its optional caption strip, stack, log
        has_stack = bool((self.state or {}).get("stack"))
        panel_on = self.preview_mode == "always"                 # patch UI6: the focus panel is in the column
        log_col = self.log_mode == "always"                      # patch UI6: the game log is in the column
        L.panel_on, L.log_col = panel_on, log_col
        # The focus panel's designed size (no caption): also the size of the over-card picture and of a dialog's zoom.
        zh = min(int(L.right_w_full / CARD_ASPECT), max(40, int(L.right.h * 0.62)))
        zw = int(zh * CARD_ASPECT)
        L.over_size = (zw, zh)
        side = int(CORNER_SIDE * fs)                             # the corner log's hot zone, in the window's lower-right corner
        L.hot = pygame.Rect(W - side, H - side, side, side)
        ov_h = min(L.right.h, max(int(150 * fs), int(L.right.h * CORNER_LOG_SHARE)))
        L.log_overlay = pygame.Rect(L.right.x, L.right.bottom - ov_h, L.right_w, ov_h)       # where the corner log opens
        L.aux, L.aux_ok = None, False
        if panel_on:
            avail_h = int(L.right.h * (0.46 if has_stack else 0.62))
            # Scryfall's image rules say a picture's own copyright/artist line must stay visible, so a caption (what the
            # art can't show: imprinted colours, gained keywords) is a strip BELOW the picture, never on top of it - the
            # picture only shrinks to make room for one on frames that actually need it (self.hits, read here before
            # draw_frame clears it for this frame, still holds last frame's hit-test, which is what decides what the
            # mouse is over right now); most frames show no caption, so the focus card stays at its full designed size.
            preview_card = self.preview_card() if self.state else None
            if preview_card is None:
                preview_card = getattr(self, "last_preview", None)
            needs_caption = bool(preview_card) and bool(self.imprint_caption(preview_card) or self.keyword_caption(preview_card))
            cap_reserve = 0
            if needs_caption:
                cap_f = get_font(px["small"], True)
                cap_reserve = 4 * cap_f.get_height() + 8 + m       # draw_imprint_caption's worst case: 4 lines + its own padding
            prev_h = min(int(L.right_w / CARD_ASPECT), max(40, avail_h - cap_reserve))
            prev_w = int(prev_h * CARD_ASPECT)
            L.preview = pygame.Rect(L.right.x + (L.right_w - prev_w) // 2, L.right.y, prev_w, prev_h)
            L.caption = pygame.Rect(L.preview.x, L.preview.bottom + 4, L.preview.w, max(0, cap_reserve - 4))
            stack_top = L.caption.bottom + m
        else:                                                    # placed again below, once the stack has its room
            L.preview = pygame.Rect(L.right.x, L.right.y, 0, 0)
            L.caption = pygame.Rect(L.right.x, L.right.y, 0, 0)
            stack_top = L.right.y
        stack = (self.state or {}).get("stack", [])
        L.stack_rows, L.stack_need = self.stack_rows(L, stack) if stack else ([], 0)
        if panel_on and log_col:
            stack_h = 0 if not stack else int(min(L.stack_need, L.right.h * STACK_MAX_SHARE))     # exactly as before patch UI6
        else:                                                    # the stack gets the height the panel and the log gave up
            keep = (int(L.right.h * LOG_MIN_SHARE) + m) if log_col else 0
            cap = min(max(0, L.right.bottom - stack_top - keep), int(L.right.h * ROOMY_STACK_SHARE))
            stack_h = 0 if not stack else int(min(L.stack_need, cap))
        L.stack = pygame.Rect(L.right.x, stack_top, L.right_w, stack_h)
        top = L.stack.bottom + (m if stack_h else 0)
        L.log = pygame.Rect(L.right.x, top, L.right_w, (L.right.bottom - top) if log_col else 0)
        if panel_on:
            L.zoom = L.preview
        else:
            # A card that needs a place of its own with no panel (what a question is about, an AI cast): under the stack, when there is room.
            free_h = max(0, L.right.bottom - top)
            if log_col:
                free_h = int(free_h * 0.7)                       # the log's newest lines are at its bottom: leave them showing
            want = int(L.right_w / CARD_ASPECT)
            if free_h >= int(AUX_MIN * fs):
                ph, y0, L.aux_ok = min(want, free_h), top, True
            else:                                                # no room: a dialog's zoom may still cover the stack's foot, nothing else uses it
                ph = min(want, int(L.right.h * 0.45))
                y0 = L.right.bottom - ph
            pw = int(ph * CARD_ASPECT)
            L.preview = pygame.Rect(L.right.x + (L.right_w - pw) // 2, y0, pw, ph)
            L.caption = pygame.Rect(L.preview.x, L.preview.bottom, L.preview.w, 0)
            if L.aux_ok:
                L.aux = L.preview
            zx = L.right.right - zw if zw > L.right_w else L.right.x + (L.right_w - zw) // 2
            L.zoom = pygame.Rect(zx, L.right.y, zw, zh)         # a dialog's zoom keeps its old place: the top of the column
        return L

    # ---- hit testing ----------------------------------------------------------------

    def add_hit(self, rect, kind, **data):
        self.hits.append((pygame.Rect(rect), kind, data))

    def hit_at(self, pos):
        for rect, kind, data in reversed(self.hits):
            if rect.collidepoint(pos):
                return kind, data
        return None, None

    # ---- card drawing ------------------------------------------------------------------

    def art_name(self, card):
        """The name to look the picture up under, or None when the card has no real picture (Treasure, Goblin and other tokens).
        A token that is a COPY of a real card (Consecrated Sphinx made by a copy effect) shares that card's name, so it gets the
        real card's picture instead of a drawn stand-in."""
        if not self.art:
            return None
        name = card.get("name", "?")
        if not card.get("token"):
            return name
        store = self.art.store
        peek_card = getattr(store, "peek_card", None)
        peek_image = getattr(store, "peek_image_path", None)
        for cand in (name, card.get("oracleName")):
            if cand and (cand in self.art.imgs or (peek_card and peek_card(cand)) or (peek_image and peek_image(cand))):
                return cand
        return None

    def art_key(self, card):
        """Round ALT1: which picture this card gets - card_data.art_key(): its name (a str) for Scryfall's default printing, or
        an ArtKey when the deck it came from names a printing ("1 Sol Ring (C21) 263"). The printing is looked up in its OWNER's
        deck when the snapshot says who that is ("owner", not sent by the bridge yet), else its controller's. A token gets the
        default printing. None when the card has no real picture (art_name)."""
        name = self.art_name(card)
        if name is None or card.get("token") or not self.seat_printings:
            return name
        pid = card.get("owner", card.get("controller"))
        seat = self.seat_of(pid)
        pmap = self.seat_printings[seat] if seat is not None and seat < len(self.seat_printings) else None
        pr = (pmap or {}).get(name.lower())
        return cdata.art_key(name, *pr) if pr else name

    def seat_of(self, player_id):
        """Round ALT1: 0 for me, 1.. for the AIs in the snapshot's player order (the order the decks were given to Forge)."""
        st = self.state or {}
        players = tuple(p.get("id") for p in st.get("players") or [])
        if self._seat_ids[0] != (players, st.get("me")):
            me = st.get("me")
            order = [me] + [i for i in players if i != me]
            self._seat_ids = ((players, me), {pid: n for n, pid in enumerate(order)})
        return self._seat_ids[1].get(player_id)

    def set_seat_printings(self, printings):
        """Round ALT1: [mine, AI 1's, ...] as DeckEntry.printings ({name lower: (set, cn)}); a double-faced card's line is also
        found by its front face's name (Forge names it so on the table)."""
        out = []
        for m in printings or []:
            d = {}
            for name, pr in (m or {}).items():
                if not pr:
                    continue
                d.setdefault(name.lower(), tuple(pr))
                front = re.split(r"\s+//?\s+", name)[0].strip().lower()
                d.setdefault(front, tuple(pr))
                faces = [f for f in re.split(r"\s*//?\s*", name) if f.strip()]
                if len(faces) > 1:                        # patch 40: "Spiked Corridor/Torture Pit" as Forge may name it too
                    d.setdefault(" // ".join(faces).lower(), tuple(pr))
                    d.setdefault(faces[0].strip().lower(), tuple(pr))
            out.append(d)
        self.seat_printings = out if any(out) else []

    def printing_keys(self):
        """Every chosen printing at the table, as picture keys (to fetch them in the background with the rest)."""
        out = set()
        for m in self.seat_printings:
            for name, pr in m.items():
                out.add(cdata.art_key(name, *pr))
        return out

    def card_surface(self, card, w, h, plate=True):
        """The card as it appears at (w, h): picture (or a drawn stand-in) plus badges. Cached."""
        return self.card_surface_keyed(card, w, h, plate)[1]

    def card_surface_keyed(self, card, w, h, plate=True):
        """(cache key, surface) for card_surface. The key names the picture by what is in it (never by id(), which Python reuses
        once the least-recently-used cache has dropped a surface)."""
        radius = max(3, int(w * 0.055))
        if card.get("hidden") or card.get("faceDown"):
            base = gfx.card_back(w, h, radius)
            base_key = ("back", w, h)
        else:
            akey = self.art_key(card)
            img = None
            if self.art and akey is not None:
                img = self.art.get(akey)
            if img is not None:
                base_key = ("img", akey, w, h)
                if self.art.is_custom(akey):                       # round ALT1: my_art/ - its own key, renewed by Reload my art
                    base_key = ("img", akey, w, h, "custom", self.art.custom_gen)
                base = gfx.recall(base_key) or gfx.remember(base_key, gfx.rounded_image(img, w, h, radius))
            else:
                base = gfx.card_face(card, w, h, radius, show_pt=not plate)
                base_key = ("face", card.get("name"), card.get("cost"), w, h, not plate)
        if not plate or card.get("hidden"):
            return base_key, base
        counters = tuple(sorted((card.get("counters") or {}).items()))
        key = ("card", card.get("name"), w, h, card.get("power"), card.get("toughness"), card.get("damage"), counters,
               card.get("sick"), card.get("commander"), card.get("loyalty"), card.get("faceDown"),
               tuple(imprint_colours(card)) if makes_imprinted_colours(card) else None, tuple(keyword_tags(card)), base_key)
        surf = gfx.recall(key)
        if surf is not None:
            return key, surf
        surf = base.copy()
        self._badges(surf, card, w, h, real=(base_key[0] == "img" and "custom" not in base_key))
        return key, gfx.remember(key, surf)

    def board_frame_surface(self, card, w, h):
        """(cache key, surface) for a compact 'board frame': the Scryfall art_crop as a background, a dark name strip, a
        type-icon letter and every badge the full card gets (round 26, cog switch 'Compact board cards', default OFF).
        Falls back to the plain full-card picture until the art_crop has finished downloading."""
        akey = self.art_key(card)
        crop = self.art.art_crop(akey, w, h) if (self.art and akey is not None) else None
        if crop is None:
            return self.card_surface_keyed(card, w, h, plate=True)
        radius = max(3, int(w * 0.055))
        counters = tuple(sorted((card.get("counters") or {}).items()))
        key = ("frame", akey, w, h, card.get("power"), card.get("toughness"), card.get("damage"), counters,
               card.get("sick"), card.get("commander"), card.get("loyalty"), card.get("faceDown"),
               tuple(imprint_colours(card)) if makes_imprinted_colours(card) else None, tuple(keyword_tags(card)))
        surf = gfx.recall(key)
        if surf is not None:
            return key, surf
        surf = gfx.rounded_image(crop, w, h, radius)
        strip_h = max(12, int(h * 0.16))
        shade = pygame.Surface((w, strip_h), pygame.SRCALPHA)
        shade.fill(gfx.SHADE_BG)
        surf.blit(shade, (0, 0))
        f = get_font(max(9, int(strip_h * 0.6)), True)
        letter = type_letter(card)
        room = w - 8 - (f.size(letter)[0] + 6 if letter else 0)
        draw_text(surf, clip_text(card.get("name", "?"), f, room), 4, strip_h // 2, f, WHITE, "midleft")
        if letter:
            draw_text(surf, letter, w - 4, strip_h // 2, f, gfx.STRIP_TEXT, "midright")
        self._badges(surf, card, w, h, real=True)     # art_crop is always a real Scryfall picture
        return key, gfx.remember(key, surf)

    def _badges(self, surf, card, w, h, real=False):
        """real=True (a real Scryfall picture, not a drawn stand-in face) keeps the bottom COPYRIGHT_BAND of the
        image clear of badges, per Scryfall's image-usage guidelines: don't cover the artist/copyright line printed
        on the card. COPYRIGHT_BAND is an estimate from the modern card frame - see CLAUDE.md, Round 27."""
        f = get_font(max(9, h * 0.13), True)
        small = get_font(max(8, h * 0.085), True)
        floor = h - int(h * COPYRIGHT_BAND) if real else h
        if card.get("isPlaneswalker") and card.get("loyalty") is not None:
            r = pygame.Rect(0, 0, int(w * 0.36), int(h * 0.15))
            r.bottomright = (w - 3, floor - 3)
            pygame.draw.rect(surf, gfx.BADGE_DARK, r, border_radius=6)
            pygame.draw.rect(surf, GOLD, r, 2, border_radius=6)
            draw_text(surf, str(card["loyalty"]), r.centerx, r.centery, f, WHITE, "center")
        elif card.get("power") is not None:
            dmg = card.get("damage") or 0
            r = pygame.Rect(0, 0, int(w * 0.46), int(h * 0.15))
            r.bottomright = (w - 3, floor - 3)
            pygame.draw.rect(surf, gfx.BADGE_DARK, r, border_radius=6)
            pygame.draw.rect(surf, RED if dmg else GOLD, r, 2, border_radius=6)
            draw_text(surf, f"{card['power']}/{card['toughness']}", r.centerx, r.centery, f, RED if dmg else WHITE, "center")
            if dmg:
                d = pygame.Rect(0, 0, int(w * 0.3), int(h * 0.1))
                d.bottomright = (r.right, r.top - 2)
                pygame.draw.rect(surf, RED, d, border_radius=5)
                draw_text(surf, f"-{dmg}", d.centerx, d.centery, small, WHITE, "center")
        y = floor - 3
        if makes_imprinted_colours(card):                       # Chrome Mox: the colours it can make, or "no mana" while nothing is imprinted
            cols = imprint_colours(card)
            r = max(5, int(w * 0.075))
            if cols:
                pill = pygame.Rect(3, y - 2 * r - 6, len(cols) * (2 * r + 3) + 6, 2 * r + 6)
            else:
                label = "no mana"
                tw, th = small.size(label)
                pill = pygame.Rect(3, y - th - 8, tw + 12, th + 8)
            pygame.draw.rect(surf, gfx.BADGE_DARK, pill, border_radius=pill.h // 2)
            pygame.draw.rect(surf, GOLD, pill, 1, border_radius=pill.h // 2)
            if cols:
                for k, col in enumerate(cols):
                    gfx.mana_symbol(surf, col, pill.x + 3 + r + 1 + k * (2 * r + 3), pill.centery, r)
            else:
                draw_text(surf, label, pill.centerx, pill.centery, small, DIM, "center")
            y = pill.top - 2
        gained = keyword_tags(card)       # "+Flying" chips: what it has now that its text does not say (Jump, an Equipment ...); "Menace" (round 32)
        for kw in gained[:2]:
            label = clip_text(kw, small, int(w * 0.8))
            tw, th = small.size(label)
            r = pygame.Rect(3, y - th - 4, tw + 10, th + 4)
            pygame.draw.rect(surf, gfx.STATUS_PURPLE_BG, r, border_radius=5)
            pygame.draw.rect(surf, gfx.STATUS_PURPLE_EDGE, r, 1, border_radius=5)
            draw_text(surf, label, r.centerx, r.centery, small, WHITE, "center")
            y = r.top - 2
        if len(gained) > 2:
            label = f"+{len(gained) - 2} more"
            tw, th = small.size(label)
            r = pygame.Rect(3, y - th - 4, tw + 10, th + 4)
            pygame.draw.rect(surf, gfx.STATUS_PURPLE_BG, r, border_radius=5)
            draw_text(surf, label, r.centerx, r.centery, small, WHITE, "center")
            y = r.top - 2
        for name, n in list((card.get("counters") or {}).items())[:3]:
            label = f"{n} x {COUNTER_NAMES.get(name, name.title())}" if n > 1 else COUNTER_NAMES.get(name, name.title())
            label = clip_text(label, small, int(w * 0.55))
            tw, th = small.size(label)
            r = pygame.Rect(3, y - th - 4, tw + 10, th + 4)
            pygame.draw.rect(surf, gfx.STATUS_TEAL_BG, r, border_radius=5)
            pygame.draw.rect(surf, gfx.STATUS_TEAL_EDGE, r, 1, border_radius=5)
            draw_text(surf, label, r.centerx, r.centery, small, WHITE, "center")
            y = r.top - 2
        if card.get("commander"):
            c = (int(w * 0.14) + 3, int(w * 0.14) + 3)
            pygame.draw.circle(surf, gfx.BADGE_DARK, c, int(w * 0.13))
            pygame.draw.circle(surf, GOLD, c, int(w * 0.13), 2)
            draw_text(surf, "C", c[0], c[1], small, GOLD, "center")
        if card.get("sick") and card.get("isCreature"):
            r = pygame.Rect(0, 0, int(w * 0.32), int(h * 0.11))
            r.topright = (w - 3, 3)
            pygame.draw.rect(surf, gfx.BADGE_SLEEP_BG, r, border_radius=5)
            draw_text(surf, "Zz", r.centerx, r.centery, small, gfx.STATUS_SLEEP_TEXT, "center")

    def draw_card_at(self, card, x, y, w, h, tapped=False, plate=True, flags=True, lift=0, board=False):
        """Draw a card (rotated 90 degrees when tapped). Returns the rect it covers on screen. `board=True` (battlefield slots
        only) uses the compact art-crop board frame instead of the full card picture once it is small enough and the
        'Compact board cards' cog switch is on (round 26); everywhere else always gets the full card."""
        if self.is_flying(card):                                      # its picture is drawn gliding in (draw_effects); only its place is kept
            return pygame.Rect(x, y - lift, w, h)
        use_frame = (board and self.frames and not card.get("hidden") and not card.get("faceDown")
                     and h < BOARD_FRAME_BELOW * max(1.0, self.L.fs / 1.3))
        skey, surf = self.board_frame_surface(card, w, h) if use_frame else self.card_surface_keyed(card, w, h, plate)
        angle = self.anim.tap_angle(card.get("id"), tapped, time.monotonic()) if (self.animations and card.get("id") is not None) else None
        if angle is not None:                                    # Round AD2c: tapping / untapping turns the card instead of snapping
            turned = pygame.transform.rotate(surf, -angle)
            rect = turned.get_rect(center=(x + w // 2, y + h // 2))
            gfx.shadowed(self.screen, rect, radius=max(3, int(w * 0.06)))
            self.screen.blit(turned, rect)
            if flags:
                self.draw_card_flags(card, rect)
            return rect
        if tapped:
            key = ("rot", skey)
            rot = gfx.recall(key)
            if rot is None:
                rot = pygame.transform.rotate(surf, -90)
                # Scryfall's image-usage guidelines say not to colour-shift a card's art; the rotation alone reads as
                # "tapped", so mark it with a thin grey outline instead of the tint this used to apply (Round 27).
                outline = max(2, int(2 * self.L.fs))
                pygame.draw.rect(rot, gfx.FRAME_DIM, rot.get_rect(), outline)
                gfx.remember(key, rot)
            surf = rot
            rect = rot.get_rect(center=(x + w // 2, y + h // 2))
        else:
            rect = pygame.Rect(x, y - lift, w, h)
        gfx.shadowed(self.screen, rect, radius=max(3, int(w * 0.06)))
        self.screen.blit(surf, rect)
        if flags:
            self.draw_card_flags(card, rect)
        return rect

    def draw_card_flags(self, card, rect):
        radius = max(3, int(min(rect.w, rect.h) * 0.07))
        if card.get("attacking"):
            ffx.draw_attack_glow(self.screen, rect, time.time(), self.animations, RED)
        elif card.get("blocking"):
            ffx.draw_attack_glow(self.screen, rect, time.time(), self.animations, CYAN)
        if card.get("selectable") or card.get("highlight") or card.get("weak"):
            # every "you can click this" card gets the same bold yellow: a glow and a thick outline
            gfx.glow(self.screen, rect, YELLOW, radius, 4, 4)
            pygame.draw.rect(self.screen, YELLOW, rect, max(3, int(min(rect.w, rect.h) * 0.035)), border_radius=radius)
        tgt = self.target_prompt()
        if tgt and tgt["id"] == card.get("id"):
            self.draw_source_badge(rect, radius)

    def draw_source_badge(self, rect, radius):
        """While choosing a target: the card whose ability it is wears an orange frame and a SOURCE tag, so it is not mistaken for
        a target (Forge lets it target itself, and it is glowing like every other legal target)."""
        pygame.draw.rect(self.screen, ORANGE, rect, max(4, int(min(rect.w, rect.h) * 0.05)), border_radius=radius)
        f = self.font("tiny", True)
        label = gfx.pick_font(f, "SOURCE").render("SOURCE", True, gfx.INK_GOLD)
        pad = max(3, f.get_height() // 4)
        pill = pygame.Rect(0, 0, min(label.get_width() + 2 * pad, rect.w), label.get_height() + pad)
        pill.midtop = (rect.centerx, rect.y + max(2, rect.h // 40))
        pygame.draw.rect(self.screen, ORANGE, pill, border_radius=pill.h // 2)
        self.screen.blit(label, label.get_rect(center=pill.center), area=pygame.Rect(0, 0, pill.w - 2, label.get_height()))

    # ---- battlefield layout ----------------------------------------------------------------

    def group_key(self, c):
        if not (c.get("isLand") or c.get("token")) or c.get("counters") or c.get("attacking") or c.get("blocking"):
            return None
        if c.get("isCreature") and not c.get("token"):
            return None
        return (c.get("name"), c.get("tapped"), c.get("sick"), c.get("token"), bool(c.get("selectable")),
                bool(c.get("weak")), bool(c.get("highlight")), c.get("damage") or 0, c.get("power"), c.get("toughness"),
                c.get("controller"))

    def build_slots(self, player):
        """Split a battlefield into two rows of slots: (nonland permanents, lands)."""
        cards = player["zones"]["battlefield"]
        ids = {c["id"] for c in cards}
        attached = {}
        for c in cards:
            host = c.get("attachedTo")
            if host in ids and host != c["id"]:
                attached.setdefault(host, []).append(c)
        hosted = {a["id"] for lst in attached.values() for a in lst}
        rows = ([], [])
        groups = {}
        for c in cards:
            if c["id"] in hosted:
                continue
            row = rows[1] if c.get("isLand") and not c.get("isCreature") else rows[0]
            key = self.group_key(c) if c["id"] not in attached else None
            if key is not None and key in groups and groups[key][1] is row:
                groups[key][0].cards.append(c)
                continue
            slot = Slot([c], attached.get(c["id"], []))
            row.append(slot)
            if key is not None:
                groups[key] = (slot, row)

        def order(slot):
            c = slot.cards[0]
            return 0 if c.get("isCreature") else 1 if c.get("isPlaneswalker") else 2
        rows[0].sort(key=order)                     # stable: creatures first, then walkers, then artifacts/enchantments
        return rows

    @staticmethod
    def row_widths(slots, ch):
        """(widths, meta, gap) of a row of slots `ch` tall: each slot's width (a tapped card is turned sideways, a stack fans
        out, attachments peek out on the left) and the gap between slots."""
        cw = int(ch * CARD_ASPECT)
        gap = max(4, int(cw * 0.1))
        cascade = max(5, int(cw * 0.16))
        widths, meta = [], []
        for s in slots:
            base = ch if s.tapped else cw
            extra = (min(len(s.cards), 4) - 1) * cascade
            att = min(len(s.attached), 2) * int(cw * 0.22)
            widths.append(base + extra + att)
            meta.append((base, extra, att))
        return widths, meta, gap

    def one_row_height(self, slots, avail_w, ch_min, ch_max):
        """Round UI5: the tallest card height (from ch_max down) at which all `slots` fit side by side in avail_w without
        overlapping, or None when one row wouldn't be clearly bigger (a third or more) than two rows of ch_min: a crowded board
        keeps its two rows, nonland permanents above the lands."""
        if not slots:
            return None
        floor = int(ch_min * 1.35)
        for h in range(int(ch_max), floor - 1, -2):
            widths, _meta, gap = self.row_widths(slots, h)
            if sum(widths) + gap * (len(widths) - 1) <= avail_w:
                return h
        return None

    def place_row(self, slots, region, ch, row_y):
        """Give each slot a rect inside `region` (a row `ch` tall starting at row_y)."""
        cw = int(ch * CARD_ASPECT)
        widths, meta, gap = self.row_widths(slots, ch)
        xs = layout_row(widths, region.x, region.w, gap)
        for s, x, w, (base, extra, att) in zip(slots, xs, widths, meta):
            s.rect = pygame.Rect(x, row_y, w, ch)
            s._layout = (base, extra, att, cw, ch)

    def draw_slot(self, slot, hover_pos):
        base, extra, att, cw, ch = slot._layout
        x0, y0 = slot.rect.x, slot.rect.y
        shown = min(len(slot.cards), 4)
        cascade = extra // (shown - 1) if shown > 1 else 0
        # attachments peek out from behind the host, on its left
        offx = 0
        for i, a in enumerate(slot.attached[:2]):
            r = self.draw_card_at(a, x0 + i * int(cw * 0.22), y0 - int(ch * 0.05), cw, ch, tapped=False, flags=True, board=True)
            self.card_rects[a["id"]] = r
            self.add_hit(pygame.Rect(r.x, r.y, int(cw * 0.22), r.h), "card", card=a)
            offx += int(cw * 0.22)
        last = None
        for i in range(shown):
            card = slot.cards[len(slot.cards) - shown + i] if len(slot.cards) > 4 else slot.cards[i]
            cx = x0 + offx + i * cascade
            tapped = card.get("tapped", False)
            tx = cx if not tapped else cx + (base - cw) // 2
            dy = 0
            if self.animations and card.get("id") is not None:        # Round AD2c: an attacker steps toward who it attacks
                toward = -1 if card.get("controller") == self.my_id() else 1
                mono = time.monotonic()
                dy = int(toward * ch * self.anim.step(card["id"], bool(card.get("attacking")), mono))
                nx, ny = self.anim.nudge(card["id"], mono)                 # patch 48: a creature that was just hit is knocked
                if nx or ny:
                    fs = max(1.0, self.L.fs)
                    tx, dy = tx + int(nx * fs), dy + int(ny * fs)
            r = self.draw_card_at(card, tx, y0 + dy, cw, ch, tapped=tapped, board=True)
            self.card_rects[card["id"]] = r
            self.add_hit(r if i == shown - 1 else pygame.Rect(r.x, r.y, max(cascade, 6), r.h), "card", card=self.pick(slot, card, i, shown))
            last = r
        if len(slot.cards) > 1 and last is not None:
            f = get_font(max(10, ch * 0.15), True)
            label = f"x{len(slot.cards)}"
            tw, th = f.size(label)
            b = pygame.Rect(0, 0, tw + 12, th + 4)
            b.topright = (last.right - 3, last.top + 3)
            pygame.draw.rect(self.screen, gfx.INK_DARK, b, border_radius=8)
            pygame.draw.rect(self.screen, GOLD, b, 1, border_radius=8)
            draw_text(self.screen, label, b.centerx, b.centery, f, WHITE, "center")

    @staticmethod
    def pick(slot, card, i, shown):
        """Clicking a stack of lands should hit one that is actionable (glowing), else the top card."""
        if len(slot.cards) > 1:
            for c in slot.cards:
                if c.get("selectable") or c.get("weak"):
                    return c
            for c in slot.cards:
                if not c.get("tapped"):
                    return c
        return card

    def draw_battlefield(self, player, region, ch, one_row_max=None):
        """Two rows `ch` tall: nonland permanents, then lands. Round UI5: with one_row_max (an opponent in a pod), a board
        that fits in ONE row of bigger cards (up to one_row_max tall) is drawn that way instead; a fuller board keeps the
        two rows. In a 4-player game the two rows were about 50 px tall at 1080p, with most of the row's width empty."""
        pad = self.L.pad
        rows = self.build_slots(player)
        self.slots[player["id"]] = rows
        inner = pygame.Rect(region.x + pad, region.y, region.w - 2 * pad, region.h)
        h = self.one_row_height(rows[0] + rows[1], inner.w, ch, one_row_max) if one_row_max else None
        if h:
            self.place_row(rows[0] + rows[1], inner, h, region.y + (region.h - h) // 2)
        else:
            row_ys = [region.y + pad, region.y + pad * 2 + ch]
            for slots, y in zip(rows, row_ys):
                self.place_row(slots, inner, ch, y)
        for slots in rows:
            for slot in slots:
                self.draw_slot(slot, self.mouse)

    # ---- small widgets ------------------------------------------------------------------

    def draw_button(self, rect, label, name, enabled=True, primary=False, focus=False, hit=True, fkey="btn"):
        rect = pygame.Rect(rect)
        mouse_over = enabled and rect.collidepoint(self.mouse)
        if primary and enabled:
            top, bottom, edge, fg = gfx.BTN_GOLD_TOP, gfx.BTN_GOLD_BOTTOM, gfx.BTN_GOLD_EDGE, gfx.BTN_GOLD_FG
        elif enabled:
            top, bottom, edge, fg = gfx.BTN_TOP, gfx.BTN_BOTTOM, gfx.BTN_EDGE, WHITE
        else:
            top, bottom, edge, fg = gfx.BTN_OFF_TOP, gfx.BTN_OFF_BOTTOM, gfx.BTN_OFF_EDGE, gfx.BTN_OFF_FG
        if mouse_over:
            top, bottom = gfx.lerp(top, (255, 255, 255), 0.18), gfx.lerp(bottom, (255, 255, 255), 0.12)
        if focus and enabled:
            pulse = 0.5 + 0.5 * abs(((time.time() * 1.6) % 2) - 1)
            gfx.glow(self.screen, rect, gfx.lerp(gfx.GLOW_GOLD, GOLD, pulse), 10, 3, 4)
        surf = pygame.Surface(rect.size, pygame.SRCALPHA)
        grad = gfx.gradient(rect.size, top, bottom)
        surf.blit(grad, (0, 0))
        radius = gfx.BUTTON_RADIUS if gfx.FRAME_STYLE else 10        # Round AD2: square-cornered stone buttons
        mask = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
        surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self.screen.blit(surf, rect)
        pygame.draw.rect(self.screen, edge, rect, 2 if primary and enabled else 1, border_radius=radius)
        if gfx.FRAME_STYLE:
            gfx.bevel(self.screen, rect, edge)
        font = self.ui_font(fkey, True)                     # round 32: every button's label is in the display face
        draw_text(self.screen, clip_text(label, font, rect.w - 10), rect.centerx, rect.centery, font, fg, "center")
        if hit and enabled:
            self.add_hit(rect, "button", name=name)

    def button_width(self, label, key="btn"):
        return self.ui_font(key, True).size(label)[0] + int((34 if key == "btn" else 22) * self.L.fs)

    # ---- top bar -------------------------------------------------------------------------

    def draw_cog_button(self):
        """The settings gear, in its fixed top-right spot. After a match ends the focus card and log column are
        allowed to reach up into the (now mostly empty) top bar - draw_frame() calls this again once they are drawn
        so the cog always ends up on top and stays visible and clickable, never hidden behind the card."""
        self.draw_button(self.cog_rect, "", "settings", fkey="small")
        gfx.draw_cog(self.screen, self.cog_rect.centerx, self.cog_rect.centery, int(self.cog_rect.w * 0.64), WHITE)

    def draw_top_bar(self):
        L, s, scr = self.L, self.state, self.screen
        active = next((p for p in s["players"] if p["id"] == s.get("activePlayer")), None) if s else None
        mine_turn = bool(s and s.get("activePlayer") == s.get("me"))
        accent = GOLD if mine_turn else ORANGE
        bar = pygame.Rect(0, 0, L.W, L.top_h)
        scr.blit(gfx.gradient(bar.size, gfx.BAR_TOP, gfx.BAR_BOTTOM), bar)
        pygame.draw.line(scr, accent if active else gfx.TOP_RULE, (0, L.top_h - 2), (L.W, L.top_h - 2), 2)
        # right-hand side: one cog button; everything else (text size, full screen, new game, help, report a bug) is in its pop-up
        bh = int(L.top_h * 0.56)
        x = L.W - L.margin - bh
        self.cog_rect = pygame.Rect(x, (L.top_h - bh) // 2, bh, bh)
        self.draw_cog_button()
        x -= 6
        left_end = x - 6
        if L.game_over:
            return          # the turn counter and phase pills mean nothing once the match is over; see make_layout()
        # whose turn it is: "Turn 4" over a coloured "YOUR TURN" / "AI 1'S TURN" tag ("Turn" here means the round -
        # every player's Nth turn - not Forge's raw per-player counter; see round_number())
        f1, f2 = self.font("title", True), self.ui_font("small", True)          # round 32: the turn tag in the display face too
        turn_txt = (f"Turn {round_number(s['turn'], len(s.get('players') or ()))}" if s and s.get("turn")
                   else ("Before turn 1" if s else "Starting"))
        who = "YOUR TURN" if mine_turn else (f"{active['name']}'s turn".upper() if active else "")
        max_left = int(L.W * 0.2)
        who = clip_text(who, f2, max_left - 16) if who else ""
        y1 = int(L.top_h * 0.10)
        r1 = draw_text(scr, turn_txt, L.margin + 4, y1, f1, WHITE)
        if who:
            tag = pygame.Rect(L.margin + 4, r1.bottom + 3, f2.size(who)[0] + 16, f2.get_height() + 4)
            round_rect(scr, tag, accent, tag.h // 2)
            draw_text(scr, who, tag.centerx, tag.centery, f2, gfx.INK_GOLD_DARK, "center")
            left_w = max(r1.w, tag.w)
        else:
            left_w = r1.w
        # the phase pills
        stops = (s or {}).get("stops", {"mine": [], "theirs": []})
        cur = PILL_OF.get((s or {}).get("phase"), (s or {}).get("phase"))
        cur_i = next((i for i, (ph, _l) in enumerate(PILLS) if ph == cur), None)
        cap = self.ui_font("tiny")
        cap_w = cap.size("stops:")[0]
        x0 = L.margin + 4 + left_w + 16 + cap_w
        avail = left_end - x0
        names_full = [lab for _p, lab in PILLS]

        def total(fnt, names, pad):
            gaps = sum(9 if ph in PILL_GAP_AFTER else 3 for ph, _l in PILLS[:-1])
            return sum(fnt.size(n)[0] + 2 * pad for n in names) + gaps

        fnt, labels, pad = self.ui_font("small", True), names_full, 5
        for key, names in (("body", names_full), ("small", names_full), ("tiny", names_full), ("tiny", PILL_SHORT)):
            fnt, labels = self.ui_font(key, True), names          # round 32: the phase pills in the display face
            pad = 12
            while pad > 4 and total(fnt, labels, pad) > avail:
                pad -= 1
            if total(fnt, labels, pad) <= avail:
                break
        ph_h = int(L.top_h * 0.46)
        y = int(L.top_h * 0.10)
        stops_x = x0
        for i, ((phase, _full), label) in enumerate(zip(PILLS, labels)):
            w = fnt.size(label)[0] + 2 * pad
            r = pygame.Rect(x0, y, w, ph_h)
            if i == cur_i:
                gfx.glow(scr, r, accent, ph_h // 2, 2, 3)
                round_rect(scr, r, accent, ph_h // 2)
                fg = gfx.INK_GOLD_DARK
            elif cur_i is not None and i < cur_i:
                round_rect(scr, r, gfx.PHASE_BG, ph_h // 2, 1, gfx.PHASE_EDGE)
                fg = gfx.PHASE_FG
            else:
                round_rect(scr, r, gfx.PHASE_ACTIVE_BG, ph_h // 2, 1, gfx.PHASE_ACTIVE_EDGE)
                fg = gfx.PHASE_ACTIVE_FG
            draw_text(scr, label, r.centerx, r.centery, fnt, fg, "center")
            self.add_hit(r, "pill", phase=phase)
            # stop markers under each pill: blue = stop on your turns, orange = on opponents' turns
            cy = r.bottom + max(7, int(L.top_h * 0.15))
            dot = max(5, int(5 * L.fs))                      # the dots and their click areas grow with the text size (were fixed 5 px / 16 px)
            reach = max(8, int(8 * L.fs))
            for dx, who_, colour in ((-reach, "mine", CYAN), (reach, "theirs", ORANGE)):
                on = phase in stops.get(who_, [])
                c = (r.centerx + dx, cy)
                if on:
                    pygame.draw.circle(scr, colour, c, dot)
                else:
                    pygame.draw.circle(scr, gfx.DOT_EDGE, c, dot, max(1, dot // 5))
                self.add_hit(pygame.Rect(c[0] - reach, c[1] - reach, 2 * reach, 2 * reach), "stop", phase=phase, who=who_)
            x0 += w + (9 if phase in PILL_GAP_AFTER else 3)
        draw_text(scr, "stops:", stops_x - 8, y + ph_h + max(7, int(L.top_h * 0.15)), cap, DIM, "midright")

    def panel_targetable(self, player):
        """True when Forge may be asking you to pick this (opponent) player: a target, or someone to attack."""
        if self.waiting():
            return False
        players = (self.state or {}).get("players") or []
        if any(p.get("selectable") for p in players):
            # Round 28d: the bridge says exactly which players this question accepts (a Siege's "Choose an opponent to
            # protect this battle", other "choose a player" lists) - glow those, and only those, even your own panel.
            return bool(player.get("selectable")) and not player.get("highlight")
        if player["id"] == (self.state or {}).get("me"):
            return False
        prompt = self.prompt()
        msg = prompt.get("message", "").lower()
        return bool(prompt.get("selecting") or "player" in msg or "attack" in msg)

    # ---- player panels ---------------------------------------------------------------------

    def draw_player_panel(self, player, rect, mine):
        L, scr, s = self.L, self.screen, self.state
        pid = player["id"]
        self.add_hit(rect, "player", id=pid)                    # round 25: the hit rect stays put even while the panel jitters
        self.panel_rects[pid] = pygame.Rect(rect)
        shaker = self.panel_shakers.get(pid)
        if shaker is not None and self.animations:
            rect = rect.move(*shaker.offset(time.monotonic()))
        active = s.get("activePlayer") == pid
        base = gfx.PANEL_MINE if mine else gfx.PANEL_OPP
        round_rect(scr, rect, base, 12, 2, GOLD if active else gfx.PANEL_EDGE, alpha=235)
        if self.panel_targetable(player):
            gfx.glow(scr, rect, GOLD, 12, 2, 3)
        elif player.get("highlight"):                        # round 28d: a player you have picked in this question
            gfx.glow(scr, rect, GREEN, 12, 3, 4)
        old_clip = scr.get_clip()
        scr.set_clip(rect.clip(old_clip))                     # Round UI2: nothing in a panel ever draws over the next one
        try:
            self._draw_panel_body(player, rect, L, scr)
        finally:
            scr.set_clip(old_clip)

    def _draw_panel_body(self, player, rect, L, scr):
        x, y = rect.x + L.pad + 2, rect.y + L.pad
        w = rect.w - 2 * (L.pad + 2)
        f = self.font("body", True)
        name = player["name"]
        big = self.font("big", True)
        small = self.font("small")
        life = player["life"]
        colour = WHITE if life >= 20 else gfx.LIFE_MID_COLOUR if life >= 10 else RED
        shown = str(self.anim.life(player["id"], life, time.monotonic()) if self.animations else life)    # Round AD2c: it counts
        delta, glow = self.life_flash_of(player["id"])
        if delta:                                                 # a life total that just moved flashes red (loss) or green (gain)
            colour = gfx.lerp(colour, LIFE_GAIN if delta > 0 else LIFE_LOSS, glow)
            ffx.draw_life_flash(scr, rect, LIFE_GAIN if delta > 0 else LIFE_LOSS, glow, time.time(), self.animations)
        compact = rect.h < 3.3 * big.get_height()
        if compact:                          # short panels (pods): name and life share one line
            lf = self.font("title", True)
            chip_need = self.font("tiny", True).get_height() + 4
            for nf_, lf_ in ((f, lf), (f, f), (self.font("small", True), self.font("small", True))):
                f, lf = nf_, lf_             # Round UI2: a very short pod panel (big text) steps the name line down until the
                if L.pad + max(f.get_height(), lf.get_height()) + 1 + chip_need <= rect.h - L.pad // 2:
                    break                    # piles' row fits under it
            draw_text(scr, shown, rect.right - L.pad - 4, y - 1, lf, colour, "topright")
            draw_text(scr, clip_text(name, f, w - lf.size(str(life))[0] - 14), x, y, f, WHITE)
            if delta:
                self.draw_life_change(delta, glow, rect.right - L.pad - 4 - lf.size(str(life))[0] - 8, y, lf, "topright")
            y += max(f.get_height(), lf.get_height()) + 1
        else:
            draw_text(scr, clip_text(name, f, w - 26), x, y, f, WHITE)
            y += f.get_height() + 2
            draw_text(scr, shown, x, y, big, colour)
            lw = big.size(str(life))[0]
            if delta:
                self.draw_life_change(delta, glow, x + lw + 10, y, self.font("title", True), "topleft")
            if player.get("poison"):
                draw_text(scr, f"Poison {player['poison']}", x + lw + 10, y + big.get_height() - small.get_height() - 4, small, gfx.POISON_COLOUR)
            y += big.get_height()
        if player.get("priority"):
            if compact:                                  # Round UI2: beside the life total, not on top of its last digit
                lw_ = self.font("title", True).size(shown)[0]
                pygame.draw.circle(scr, GREEN, (rect.right - L.pad - 4 - lw_ - 10, rect.y + L.pad + f.get_height() // 2), 5)
            else:
                pygame.draw.circle(scr, GREEN, (rect.right - L.pad - 8, rect.y + L.pad + f.get_height() // 2), 5)
        # the piles: library, graveyard, exile as little stacks with counts (or plain chips when the panel is too short)
        tiny = self.font("tiny", True)
        gap = max(4, int(6 * L.fs))
        chip_h = tiny.get_height() + (4 if compact else 6)                # Round AD1: Alegreya's taller line box would push a pod panel's last chip under the next panel
        reserve = len(player.get("commanderDamage") or {}) * (small.get_height() + 1)
        label_h = tiny.get_height() + 3
        cy = y
        hand_txt = f"Hand {player['handCount']}"
        cw = tiny.size(hand_txt)[0] + 14
        round_rect(scr, pygame.Rect(x, cy, cw, chip_h), gfx.CHIP_BG, chip_h // 2, 1, gfx.CHIP_EDGE)
        draw_text(scr, hand_txt, x + cw // 2, cy + chip_h // 2, tiny, WHITE if player["handCount"] else DIM, "center")
        badges = self.player_badges(player)
        room_r = x + w
        if badges and compact:                                    # short panels: tiny chips flush right on the hand row
            placed, _ = self.place_badges(badges, x + cw + 8, cy, w - cw - 8, tiny, chip_h, short=True, right=True)
            self.draw_badges(placed, tiny, chip_h, player['id'])
            room_r = min([r.x for r, _ in placed] or [room_r]) - 6
        pool = self.pool_items(player)
        row_end = x + cw
        if pool:                                                  # floating mana, right beside the hand count
            px = x + cw + 8
            rr = max(6, chip_h // 2 - 1)
            for sym, n in pool:
                px = self.draw_pool_item(sym, n, px, cy + chip_h // 2, rr, tiny, room_r)
            row_end = px
        y = cy + chip_h + 4
        hand_row_used = False                                     # Round UI5: the piles went onto the hand row (no room for a chip)
        if badges and not compact:                                # monarch, initiative, the Ring, emblems: a row of chips
            placed, used = self.place_badges(badges, x, y, w, tiny, chip_h)
            self.draw_badges(placed, tiny, chip_h, player['id'])
            y += used
        tile_w_max = (w - 2 * gap) // 3
        tile_h = min(int(tile_w_max / CARD_ASPECT), rect.bottom - L.pad - y - reserve - label_h)
        if not compact and tile_h >= 46:
            tile_w = min(tile_w_max, int(tile_h * CARD_ASPECT))
            tile_h = int(tile_w / CARD_ASPECT)
            room = max(tile_w, (w - tile_w) // 2 - 4)                # the widest a label may be before it bumps its neighbour
            for i, (zone, labels) in enumerate((("library", ("Library", "Lib")), ("graveyard", ("Graveyard", "Grave", "GY")),
                                                ("exile", ("Exile",)))):
                label = next((t for t in labels if tiny.size(t)[0] <= room), labels[-1])
                tx = x + (i * (w - tile_w)) // 2
                self.draw_zone_tile(player, zone, pygame.Rect(tx, y, tile_w, tile_h), clip_text(label, tiny, room), tiny, label_h)
            y += tile_h + label_h + 2
        else:
            chips = [("Library", player["libraryCount"], "library"), ("Graveyard", len(player["zones"]["graveyard"]), "graveyard"),
                     ("Exile", len(player["zones"]["exile"]), "exile")]
            short = {"Library": "Lib", "Graveyard": "GY", "Exile": "Ex"}

            def need(items, cpad=14):
                return sum(tiny.size(f"{l} {n}")[0] + cpad + 4 for l, n, _z in items) - 4
            cx, cpad = x, 14
            if compact and y + chip_h > rect.bottom - L.pad:
                # Round UI2: a pod panel at big text has no room for a second row (the piles were hidden behind the next
                # panel) - put them on the hand row, with short labels when the long ones don't fit
                cx, y = row_end + 6, cy
                hand_row_used = True
            if compact:
                # Round UI5: a pod panel has room for ONE row of piles. With long labels "Exile 0" wrapped onto the next row,
                # under the panel's edge (a real 4-player game at 1080p: "Library 86  Graveyard 2" filled the row); at 125%
                # in 1280x720 even "Lib 88  GY 12  Ex 1" was 4 px too wide for the hand row. Short labels, then tighter chips.
                limit = room_r if y == cy else x + w                  # the hand row ends where its badges begin
                if cx + need(chips) > limit:
                    chips = [(short[l], n, z) for l, n, z in chips]
                if cx + need(chips) > limit:
                    cpad = 8
            for label, n, zone in chips:
                txt = f"{label} {n}"
                cw = tiny.size(txt)[0] + cpad
                if cx + cw > x + w and cx > x:
                    cx, y = x, y + chip_h + 3
                r = pygame.Rect(cx, y, cw, chip_h)
                hot = n and r.collidepoint(self.mouse)
                round_rect(scr, r, gfx.CHIP_HOT_BG if hot else gfx.CHIP_BG, chip_h // 2, 1, gfx.CHIP_HOT_EDGE)
                draw_text(scr, txt, r.centerx, r.centery, tiny, WHITE if n else DIM, "center")
                if zone == "library":
                    self.add_hit(r, "library", player=player["id"])
                elif n:
                    self.add_hit(r, "zone", player=player["id"], zone=zone)
                cx += cw + 4
            y += chip_h + 4
        # commander damage taken (round FMT1: none in Brawl - Forge doesn't count it there either)
        damage = list(((player.get("commanderDamage") or {}) if formats.get(self.game_format()).commander_damage else {}).items())
        if damage and compact and y + len(damage) * (small.get_height() + 1) > rect.bottom - L.pad // 2:
            # Round UI5: a pod panel has no room for "Cmdr dmg: Adeline 5/21" under its piles (a real 4-player game at 1080p
            # showed the top half of it, cut by the panel's edge) - "Cmdr 5" chips at the right end of the hand row instead,
            # the most damage first (a 1v1 panel still names the commander)
            if hand_row_used:                    # big text: the piles fill the hand row - one chip between the name and the life
                name_h = max(f.get_height(), lf.get_height())
                right = rect.right - L.pad - 4 - lf.size(shown)[0] - (22 if player.get("priority") else 8)
                left, top = x + f.size(clip_text(name, f, w - lf.size(str(life))[0] - 14))[0] + 8, rect.y + L.pad + (name_h - chip_h) // 2
                most = 1
            else:
                right, left, top, most = room_r, row_end + 6, cy, 2
            for cid, dmg in sorted(damage, key=lambda kv: -kv[1])[:most]:
                txt = f"Cmdr {dmg}"
                cw_ = tiny.size(txt)[0] + 12
                if right - cw_ < left:
                    break
                r = pygame.Rect(right - cw_, top, cw_, chip_h)
                round_rect(scr, r, gfx.BANNER_RED_BG if dmg >= 15 else gfx.CHIP_BG, chip_h // 2, 1, RED if dmg >= 15 else gfx.CHIP_EDGE)
                draw_text(scr, txt, r.centerx, r.centery, tiny, RED if dmg >= 15 else WHITE, "center")
                right = r.x - 4
            damage = []
        for cid, dmg in damage:
            card = self.find_card(int(cid))
            nm = (card or {}).get("name", "Commander").split(",")[0]
            col = RED if dmg >= 15 else TEXT
            draw_text(scr, clip_text(f"Cmdr dmg: {nm} {dmg}/21", small, w), x, y, small, col)
            y += small.get_height() + 1
        if player.get("lost"):
            draw_text(scr, "Defeated", rect.centerx, rect.bottom - L.pad - 8, self.font("title", True), RED, "center", True)

    def draw_life_change(self, delta, glow, x, y, font, anchor):
        """'-3' or '+2' beside a life total: it rises a little and fades out."""
        img = gfx.pick_font(font, f"{delta:+d}").render(f"{delta:+d}", True, LIFE_GAIN if delta > 0 else LIFE_LOSS)
        img.set_alpha(int(255 * min(1.0, glow * 2)))
        rect = img.get_rect()
        setattr(rect, anchor, (x, y - int((1 - glow) * 10)))
        self.screen.blit(img, rect)

    def player_badges(self, player):
        """Monarch / initiative / the Ring / emblems for this player, from the effect cards Forge keeps in their command zone."""
        return fstat.badges_for((player.get("zones") or {}).get("command", []), self.commander_ids(player))

    def place_badges(self, badges, x, y, w, font, h, short=False, right=False):
        """Where each badge chip goes: [(rect, badge)] and the height used. Chips flow left to right and wrap onto a new row; with
        right=True they sit in one row flush with the right edge (short panels)."""
        items = []
        for b in badges:
            label = b.short if short else b.label
            cw = (h + 8 if b.key == "monarch" else 7) + font.size(label)[0] + 7        # the crown takes room before the text
            items.append((b, min(cw, w)))
        placed, cx, cy = [], x, y
        if right:
            cx = max(x, x + w - (sum(cw for _, cw in items) + 4 * (len(items) - 1)))
        for b, cw in items:
            if not right and cx + cw > x + w and cx > x:
                cx, cy = x, cy + h + 3
            placed.append((pygame.Rect(cx, cy, cw, h), b))
            cx += cw + 4
        return placed, (0 if not placed else cy + h + 3 - y)

    def draw_badges(self, placed, font, h, pid=None):
        scr = self.screen
        for r, b in placed:
            fill, edge = BADGE_COLOURS.get(b.key, BADGE_COLOURS["effects"])
            round_rect(scr, r, fill, h // 2, 1, edge)
            tx = r.x + 7
            if b.key == "monarch":
                icon = pygame.Rect(r.x + 6, r.y + 3, h - 2, h - 8)
                gfx.draw_crown(scr, icon, edge)
                tx = icon.right + 4
            label = b.short if r.right - tx - 7 < font.size(b.label)[0] else b.label
            draw_text(scr, clip_text(label, font, r.right - tx - 5), tx, r.centery, font, WHITE, "midleft")
            self.add_hit(r, "badge", text=b.text, player=pid)

    POOL_ORDER = "WUBRGC"

    def pool_items(self, player):
        """Floating mana as [(symbol, amount)] in colour order."""
        pool = (player or {}).get("manaPool") or {}
        return [(c, pool[c]) for c in self.POOL_ORDER if pool.get(c)] + \
               [(c, n) for c, n in pool.items() if c not in self.POOL_ORDER and n]

    def draw_pool_item(self, sym, n, x, cy, r, font, limit):
        """One colour of floating mana: its symbol and 'x3' when there is more than one. Returns the next x."""
        label = f"x{n}" if n > 1 else ""
        need = 2 * r + 2 + (font.size(label)[0] + 4 if label else 0) + 6
        if x + need > limit:
            return x
        gfx.mana_symbol(self.screen, sym, x + r, cy, r)
        x += 2 * r + 3
        if label:
            draw_text(self.screen, label, x, cy, font, WHITE, "midleft")
            x += font.size(label)[0] + 4
        return x + 6

    POOL_NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green", "C": "colourless"}

    def input_kind(self):
        """What Forge is asking by its Input class (bridge protocol 2), e.g. 'priority', 'pay'; None = unknown, use the text."""
        return kind_of((self.state or {}).get("input"))

    def paying(self):
        """True while Forge is asking you to pay a mana cost (that is when floating mana can be clicked to pay)."""
        kind = self.input_kind()
        if kind is not None:
            return kind == "pay"
        return bool(_PAY.match(strip_ids(clean_text(self.prompt().get("message", ""))).strip()))

    def pay_trouble(self):
        """While paying: a sentence saying why your untapped mana cannot pay the cost, or '' (can pay, or not sure - never guesses)."""
        if not self.paying():
            return ""
        me = self.session.me()
        text = strip_ids(clean_text(self.prompt().get("message", ""))).strip()
        m = _PAY.match(text) or _PAY_LOOSE.match(text)
        if not me or not m:
            return ""
        try:
            return mh.explain(" ".join(m.group("cost").split()), (me.get("zones") or {}).get("battlefield") or [], me.get("manaPool"))
        except Exception:                                    # a hint must never take the table down
            return ""

    def draw_mana_pool(self, me):
        """Your floating mana, big, just above the prompt bar - it empties at the end of every step, so it matters. While Forge is
        asking for a payment the whole pill turns bold yellow and each mana in it can be clicked to spend one."""
        items = self.pool_items(me)
        self.pool_rect = None
        if not items or self.modal:
            return
        L, scr = self.L, self.screen
        paying = self.paying()
        f, fb = self.font("body", True), self.font("title", True)
        r = int(max(13, 15 * L.fs))
        head = "Click to pay" if paying else "Mana pool"
        hw = f.size(head)[0]
        w = 14 + hw + 12 + sum(2 * r + 8 + (fb.size(f"x{n}")[0] + 6 if n > 1 else 0) for _c, n in items) + 6
        h = 2 * r + 14
        rect = pygame.Rect(L.bar.right - w, L.bar.y - h - 6, w, h)
        self.pool_rect = rect
        if paying:
            gfx.glow(scr, rect, YELLOW, 12, 4, 4)
        round_rect(scr, rect, gfx.PAY_BG, 12, 4 if paying else 2, YELLOW if paying else gfx.PAY_EDGE, alpha=245)
        self.add_hit(rect, "pool", sym=None)                     # the pill itself: a click on it must not reach what is behind it
        draw_text(scr, head, rect.x + 14, rect.centery, f, YELLOW if paying else gfx.PAY_TEXT, "midleft")
        x = rect.x + 14 + hw + 12
        for sym, n in items:
            item = pygame.Rect(x - 3, rect.y + 5, 2 * r + 10 + (fb.size(f"x{n}")[0] + 6 if n > 1 else 0), rect.h - 10)
            self.add_hit(item, "pool", sym=sym)
            if paying:
                hot = item.collidepoint(self.mouse)
                round_rect(scr, item, gfx.PICK_HOT_BG if hot else gfx.PICK_BG, 10, 3, YELLOW, alpha=255)
            gfx.mana_symbol(scr, sym, x + r, rect.centery, r)
            x += 2 * r + 4
            if n > 1:
                draw_text(scr, f"x{n}", x, rect.centery, fb, WHITE, "midleft")
                x += fb.size(f"x{n}")[0] + 6
            x += 4

    def spend_pool(self, sym):
        """A click on floating mana: spend one of it on the payment in progress."""
        if not sym:
            return
        if self.paying():
            self.session.use_mana(sym)
        else:
            self.say("Floating mana is spent when Forge asks you to pay for something. It empties at the end of each step.", CYAN, 4.0)

    def draw_zone_tile(self, player, zone, r, label, font, label_h):
        """One pile (library / graveyard / exile): a card-shaped tile with a count, and its name underneath."""
        scr = self.screen
        n = player["libraryCount"] if zone == "library" else len(player["zones"].get(zone, []))
        radius = max(3, int(r.w * 0.07))
        top = player["zones"].get(zone, [])[-1] if zone != "library" and player["zones"].get(zone) else None
        clickable = zone == "library" or n > 0
        hot = clickable and r.collidepoint(self.mouse) and not self.modal
        if zone == "library" and n:
            back = gfx.recall(("libback", r.w, r.h)) or gfx.remember(("libback", r.w, r.h), gfx.card_back(r.w, r.h, radius))
            for off in (4, 2):                                   # a little stack: cards peeking out behind the top one
                if r.w > 40:
                    shade = back.copy()
                    shade.fill((150, 150, 160), special_flags=pygame.BLEND_RGB_MULT)
                    scr.blit(shade, (r.x + off, r.y - off))
            gfx.shadowed(scr, r, radius, 2, 90)
            scr.blit(back, r)
        elif top is not None:
            self.draw_card_at(top, r.x, r.y, r.w, r.h, plate=False, flags=False)
        else:
            round_rect(scr, r, gfx.HOVER_BG, radius, 1, gfx.HOVER_EDGE, alpha=200)
        if hot:
            pygame.draw.rect(scr, YELLOW, r.inflate(2, 2), 3, border_radius=radius)
        # count in a dark badge, centred on the library and in the corner of the others
        big = self.font("body", True) if r.w >= 44 else font
        txt = str(n)
        tw, th = big.size(txt)
        badge = pygame.Rect(0, 0, tw + 10, th + 2)
        if zone == "library" and n:
            badge.center = r.center
        else:
            badge.bottomright = (r.right - 2, r.bottom - 2)
        if n or zone == "library":
            round_rect(scr, badge, gfx.TAG_BG, badge.h // 2, 1, gfx.TAG_EDGE, alpha=225)
            draw_text(scr, txt, badge.centerx, badge.centery, big, WHITE, "center")
        elif not n:
            draw_text(scr, "0", r.centerx, r.centery, font, DIM, "center")
        draw_text(scr, label, r.centerx, r.bottom + 2, font, WHITE if n or zone == "library" else DIM, "midtop")
        area = pygame.Rect(r.x - 3, r.y - 4, r.w + 6, r.h + label_h + 6)
        if zone == "library":
            self.add_hit(area, "library", player=player["id"])
        elif n:
            self.add_hit(area, "zone", player=player["id"], zone=zone, card=top)

    def commander_ids(self, player):
        return list(player.get("commanders") or [])

    def commanders_on_board(self, player):
        """True when every commander of this player is on the battlefield. The command zone is empty then, so its frame is left out
        and the room goes to the hand (yours) or the battlefield (an opponent's). It comes back when a commander returns."""
        ids = self.commander_ids(player) if player else []
        return bool(ids) and all(self.where_is(player, cid) == "battlefield" for cid in ids)

    def where_is(self, player, cid):
        for zone, cards in player["zones"].items():
            if any(c["id"] == cid for c in cards):
                return zone
        return None

    def draw_command_zone(self, player, frame, ch, compact=False):
        """A labelled gold frame holding the player's commander(s); `ch` is the card height inside it. Round UI5: `compact` (a
        short pod row at big text) has a one-line title in the tiny font and no names line, so the card keeps some size."""
        scr, L = self.screen, self.L
        round_rect(scr, frame, gfx.IMPRINT_BG, 12, 2, GOLD_DARK, alpha=235)
        f1, f2 = (self.font("tiny", True), self.font("tiny")) if compact else (self.font("small", True), self.font("tiny"))
        pad = L.pad
        tax = self.title_tax(player) if compact else ""
        title, f1 = next(((t + tax, f) for f in (f1, self.font("tiny", True)) for t in ("COMMAND ZONE", "COMMANDER", "CMDR", "CMD")
                          if f.size(t + tax)[0] <= frame.w - 8), ("CMD" + tax, self.font("tiny", True)))
        draw_text(scr, clip_text(title, f1, frame.w - 6), frame.centerx, frame.y + 5, f1, GOLD, "midtop")
        ids = self.commander_ids(player)
        names = [((self.find_card(cid) or {}).get("name") or "").split(",")[0] for cid in ids]
        cw = int(ch * CARD_ASPECT)
        casts = player.get("commanderCasts") or {}
        in_zone = {c["id"]: c for c in player["zones"].get("command", [])}
        x = frame.x + pad + max(0, (frame.w - 2 * pad - max(1, len(ids)) * (cw + 6)) // 2)    # Round UI5: centred in a wider frame
        y = frame.bottom - 4 - ch
        for i, cid in enumerate(ids or [None]):
            slot = pygame.Rect(x, y, cw, ch)
            card = in_zone.get(cid)
            if card is not None:
                r = self.draw_card_at(card, x, y, cw, ch, plate=False)
                self.card_rects[card["id"]] = r
                self.add_hit(r, "card", card=card)
                n = 0 if tax else casts.get(str(cid), 0)               # Round UI5: a compact frame has it in its title
                if n:
                    tf = self.font("tiny", True)
                    tag = f"Tax +{2 * n}"
                    if tf.size(tag)[0] + 10 > frame.w - 6:      # Round UI5: a narrow pod frame (partners at big text): "+2"
                        tag = f"+{2 * n}"
                    tr = pygame.Rect(0, 0, tf.size(tag)[0] + 10, tf.get_height() + 3)
                    tr.midbottom = (r.centerx, r.bottom - 4)
                    round_rect(scr, tr, gfx.TRAY_BG, tr.h // 2, 1, GOLD, alpha=235)
                    draw_text(scr, tag, tr.centerx, tr.centery, tf, GOLD, "center")
            else:
                # the commander is somewhere else: say where instead of leaving an unexplained hole
                round_rect(scr, slot, gfx.SLOT_BG, max(4, int(cw * 0.06)), 1, gfx.SLOT_EDGE, alpha=200)
                zone = self.where_is(player, cid) if cid is not None else None
                msg = {"battlefield": "in play", "graveyard": "in graveyard", "exile": "in exile", "hand": "in hand",
                       "library": "in library", "stack": "on the stack"}.get(zone, "elsewhere") if zone else "empty"
                ty = slot.y + 8
                for ln in wrap_text(msg, f2, cw - 8)[:3]:
                    if ty + f2.get_height() > slot.bottom - 2 or f2.size(ln)[0] > cw - 4:
                        break                                  # Round UI2: a tiny slot (pods at big text) shows no words, never words outside it
                    draw_text(scr, ln, slot.centerx, ty, f2, DIM, "midtop")
                    ty += f2.get_height()
            x += cw + 6
        # names line under the title
        line = ", ".join(n for n in names if n) if not compact else ""
        if line:
            draw_text(scr, clip_text(line, f2, frame.w - 8), frame.centerx, frame.y + 5 + f1.get_height() + 1, f2, gfx.IMPRINT_TEXT, "midtop")

    def cmd_head_h(self, compact=False):
        """The height of a command-zone frame's title and names lines (compact: the tiny title only)."""
        if compact:
            return self.font("tiny", True).get_height() + 8
        return self.font("small", True).get_height() + self.font("tiny").get_height() + 12

    def cmd_frame_for(self, player, x, y, ch, compact=False):
        """The frame rectangle for an opponent's command zone. Round UI5: never narrower than its shortest title ("CMD" in the
        tiny font) - at 200% text in a 4-player game the frame was so narrow that the title read "C..."."""
        ncmd = max(1, len(self.commander_ids(player)))
        cw = int(ch * CARD_ASPECT)
        tax = self.title_tax(player) if compact else ""
        title_w = self.font("tiny", True).size("CMD" + tax)[0] + 12
        return pygame.Rect(x, y, max(ncmd * (cw + 6) + 2 * self.L.pad, title_w), self.cmd_head_h(compact) + ch + 8)

    def title_tax(self, player):
        """Round UI5: " +2" when a compact frame (a pod row at big text) shows the commander tax in its title, not as a "Tax +2" tag
        over a card too small to carry one (a real 4-player game at 200%: the tag covered most of a 34 px card). One commander
        in the command zone, cast at least once; "" otherwise (partners keep a tag on each card)."""
        ids = self.commander_ids(player)
        in_zone = {c["id"] for c in player["zones"].get("command", [])}
        n = (player.get("commanderCasts") or {}).get(str(ids[0]), 0) if len(ids) == 1 and ids[0] in in_zone else 0
        return f" +{2 * n}" if n else ""

    def draw_opponent(self, player, rect):
        L, scr = self.L, self.screen
        info = pygame.Rect(rect.x + 2, rect.y + 2, L.opp_info_w, rect.h - 4)
        self.draw_player_panel(player, info, False)
        x = info.right + L.pad
        pod = len(self.session.opponents()) >= 2
        ch, compact = L.opp_row_h, False
        if pod:
            # Round UI5: in a pod the commander card fills the row's height (it was the battlefield row's ~50 px at 1080p). When
            # the two-line title (title + names) would leave the card too short - under 40 px (200% text: the frame ran ~30 px
            # into the next opponent's row), or under 3/4 of what the one-line title leaves (a real 4-player game at 1080p:
            # 53 px, half hidden under its "Tax +2" tag) - the frame gets the compact one-line title instead.
            full_ch = rect.h - 4 - self.cmd_head_h() - 8
            compact = full_ch < 40 or full_ch < 0.75 * (rect.h - 4 - self.cmd_head_h(True) - 8)
            ch = max(L.opp_row_h, min(rect.h - 4 - self.cmd_head_h(compact) - 8, L.my_row_h))
        if not self.commanders_on_board(player):               # a commander on the battlefield: no frame, the room goes to the board
            frame = self.cmd_frame_for(player, x, rect.y + 2, ch, compact)
            if frame.bottom > rect.bottom - 2:                 # very short rows: shrink the card to fit the frame
                ch = max(30, ch - (frame.bottom - rect.bottom + 2))
                frame = self.cmd_frame_for(player, x, rect.y + 2, ch, compact)
            self.draw_command_zone(player, frame, ch, compact)
            x = frame.right + L.pad
        region = pygame.Rect(x, rect.y, rect.right - x, rect.h)
        # Round UI5: in a pod, a board that fits in one row gets cards up to the row's full height (never taller than mine)
        self.draw_battlefield(player, region, L.opp_row_h,
                              one_row_max=min(region.h - 2 * L.pad, L.my_row_h) if pod else None)

    def draw_my_side(self, me):
        L = self.L
        self.draw_player_panel(me, L.my_info, True)
        # my battlefield sits on a soft panel of its own
        round_rect(self.screen, L.my_bf, gfx.BF_MINE_BG, 12, 1, gfx.BF_MINE_EDGE, alpha=150)
        self.draw_battlefield(me, L.my_bf, L.my_row_h)
        if L.cmd_rect.w:
            self.draw_command_zone(me, L.cmd_rect, L.cmd_ch)
        self.draw_action_bar()
        self.draw_hand(me)

    # ---- action bar and hand -----------------------------------------------------------------

    def draw_action_bar(self):
        L, scr, s = self.L, self.screen, self.state
        bar = L.bar
        flash = time.time() - self.session.bad_action_at < 0.35
        round_rect(scr, bar, gfx.HUD_BAR_BG, 12, 2, RED if flash else gfx.HUD_BAR_EDGE, alpha=240)
        p = self.prompt()
        waiting = self.waiting()
        ok, cancel = p.get("ok", {}), p.get("cancel", {})
        me = self.session.me()
        headline, hint, ok_label = prompt_view(p.get("message", ""), me["name"] if me else "", self.mulligans, bool(self.pool_items(me)), self.pay_trouble(), self.input_kind()) if s.get("prompt") else ("", "", None)
        # buttons, right to left
        specs = []
        if ok.get("label"):
            specs.append(("ok", ok_label if (ok_label and ok.get("enabled")) else ok["label"], bool(ok.get("enabled")), True, bool(ok.get("focus"))))
        if cancel.get("label"):
            label = BUTTON_NAMES.get(cancel["label"], cancel["label"])     # Forge's own word is kept for the logic; only the button text changes
            if label == "Mulligan" and self.mulligans == 0:
                label = "Mulligan (free)"
            specs.append(("cancel", label, bool(cancel.get("enabled")), False, False))
        if self.skip_available():
            specs.append(("skip", self.skip_label(), True, False, False))
        specs.append(("undo", "Undo", not waiting, False, False))
        bh = bar.h - 12
        hf, sf = self.ui_font("body"), self.font("small")    # round 32: the headline in the display face; the hint stays readable body text
        colour = DIM if waiting else WHITE
        if waiting and headline:
            headline = headline.rstrip(".") + "." * (int(time.time() * 2) % 4)

        # A single-card decision (surveil, scry, and similar "look at the top card, choose a zone"
        # effects) carries a "source" card on the same prompt state Forge already sends - the card
        # being looked at. Show its face next to the text instead of leaving Karl to read only the
        # name in the headline (2026-09-25). Falls back to text-only when an older bridge build
        # (or a prompt that isn't about one card) doesn't send "source".
        src = p.get("source")
        thumb_w = 0
        text_x0 = bar.x + 12
        if src and src.get("id") is not None:
            thumb_w = dlg.source_thumb(self, src, bar.x + 8, bar.y + 6, bh)
            text_x0 = bar.x + 8 + thumb_w + 14

        def fit(button_specs, hf=hf, min_last=0):
            fk = "btn"
            for fk in ("btn", "small", "tiny"):
                ws = [max(self.button_width(lab, fk), int(90 * L.fs)) if pr else self.button_width(lab, fk)
                      for _n, lab, _e, pr, _f in button_specs]
                if sum(ws) + 8 * len(ws) < bar.w * 0.5:
                    break
            if min_last and ws:
                ws[-1] = max(ws[-1], min_last)            # Round UI3: the column that also holds Undo underneath
            x0 = bar.right - 8 - sum(ws) - 8 * len(ws)
            tw = x0 - text_x0 - 2
            lines_all = wrap_text(headline, hf, tw)
            max_head = max(2, (bar.h - 8) // max(1, hf.get_height()))    # Round UI2: a long headline may use the whole bar
            head = lines_all[:max_head]
            if len(lines_all) > max_head and head:
                head[-1] = clip_text(head[-1] + " ...", hf, tw)
            room = bar.h - 8 - len(head) * hf.get_height()
            hints = wrap_text(hint, sf, tw) if hint and not waiting else []
            fits = max(0, room // sf.get_height())
            cut = len(hints) > fits or len(lines_all) > max_head
            if len(hints) > fits:
                hints = hints[:fits]
                if hints:
                    hints[-1] = clip_text(hints[-1] + " ...", sf, tw)
            return fk, ws, x0, head, hints, cut, hf

        # Round UI2: when the words don't fit (big text), the headline first steps down to the small size; only then does Undo
        # (the U key still works) give its room to the words. At 200% Undo and the hint line used to vanish together.
        small_f = self.ui_font("small")
        cands = [(specs, hf), (specs, small_f)]
        if len(specs) > 1:
            cands += [(specs[:-1], hf), (specs[:-1], small_f)]
        chosen = None
        for sp, f_ in cands:
            lay = fit(sp, f_)
            narrow = sum(lay[1]) + 8 * len(lay[1]) > bar.w * 0.6 and len(sp) > 1
            if not lay[5] and not narrow:
                chosen = (sp, lay)
                break
        if chosen is None:                                    # nothing fits whole: the fewest buttons, the smaller words
            chosen = (cands[-1][0], fit(*cands[-1]))
        specs, layout = chosen
        fkey, widths, x, head_lines, hint_lines, _cut, hf = layout
        # Round UI3: Undo gave its room to the words, but a tall bar (big text) has room for it UNDER the last button on the
        # left (End Turn / Cancel): that column is split in two, each half in the small font
        undo_spec = ("undo", "Undo", not waiting, False, False)
        stack_undo = None
        half = (bh - 6) // 2
        stack_key = next((k for k in ("small", "tiny") if half >= self.font(k, True).get_height() + 10), None)
        if undo_spec not in specs and len(specs) >= 1 and stack_key:
            stack_undo = len(specs) - 1
            need = max(self.button_width(specs[-1][1], stack_key), self.button_width("Undo", stack_key))
            layout = fit(specs, hf, min_last=need)
            if layout[5]:                                     # the words don't fit whole: the smaller headline first
                alt = fit(specs, self.font("small"), min_last=need)
                if not alt[5] or len(alt[4]) > len(layout[4]):
                    layout = alt
            fkey, widths, x, head_lines, hint_lines, _cut, hf = layout
        bx = bar.right - 8
        for i, ((name, label, enabled, primary, focus), w) in enumerate(zip(specs, widths)):
            if i == stack_undo:
                bx -= w
                self.draw_button(pygame.Rect(bx, bar.y + 6, w, half), label, name, enabled, primary, focus, fkey=stack_key)
                self.draw_button(pygame.Rect(bx, bar.y + 6 + half + 6, w, half), "Undo", "undo", undo_spec[2], False, False,
                                 fkey=stack_key)
                bx -= 8
                continue
            bx -= w
            self.draw_button(pygame.Rect(bx, bar.y + 6, w, bh), label, name, enabled, primary, focus, fkey=fkey)
            bx -= 8
        # message: a headline (what is being asked) and, under it, one line on what to do about it
        total = len(head_lines) * hf.get_height() + len(hint_lines) * sf.get_height()
        y = bar.centery - total // 2
        for ln in head_lines:
            self.draw_line(ln, text_x0, y, hf, colour)
            y += hf.get_height()
        for ln in hint_lines:
            self.draw_line(ln, text_x0, y, sf, gfx.HINT_LINE_TEXT)
            y += sf.get_height()

    def draw_line(self, text, x, y, font, colour):
        """One line at its top edge, with {G}-style mana symbols drawn as symbols."""
        if "{" in text:
            gfx.draw_rich(self.screen, text, x, y + font.get_height() // 2, font, colour)
        else:
            draw_text(self.screen, text, x, y, font, colour)

    # ---- advice before the game starts -------------------------------------------------------------

    def opening_hand_cards(self, me):
        """Distinct cards in my hand that say 'opening hand' (Gemstone Caverns, Leylines, Chancellors ...)."""
        seen = {}
        for c in me["zones"].get("hand", []):
            text = (c.get("text") or "").lower()
            if "opening hand" in text and not c.get("hidden"):
                seen.setdefault(c["name"], text)
        return seen

    def advisory(self):
        """(text, colour) lines drawn above the bar while Forge asks 'keep your hand?': what a mulligan costs, and whether an
        opening-hand card in that hand can be used given who goes first."""
        me = self.session.me()
        flat = " ".join(clean_text(self.prompt().get("message", "")).split())
        if not me or "Do you want to keep your hand?" not in flat:
            return []
        n = self.mulligans
        first = "you are going first" in flat.lower()
        if n == 0:
            out = [("Your first mulligan is free: you draw a fresh seven and put nothing back.", GOLD)]
        else:
            out = [(f"You have mulliganed {n} time{'s' if n != 1 else ''}. Another mulligan draws seven and puts {n} card{'s' if n != 1 else ''} "
                    "on the bottom of your library.", GOLD)]
        for name, text in self.opening_hand_cards(me).items():
            needs_second = "not the starting player" in text
            if needs_second and first:
                out.append((f"{name}: it can only begin on the battlefield when you go second. You go first, so it stays in your hand.",
                            gfx.NOTE_ORANGE))
            elif needs_second:
                out.append((f"{name}: you go second, so you can begin the game with it on the battlefield - Forge asks right after you keep.",
                            gfx.NOTE_GREEN))
            else:
                out.append((f"{name}: you may begin the game with it on the battlefield - Forge asks right after you keep.", gfx.NOTE_GREEN))
        return out

    def draw_advisory(self):
        lines = self.advisory() if self.state and not self.modal else []
        if not lines:
            return
        L, scr = self.L, self.screen
        f = self.font("small", True)
        tw = L.bar.w - 28
        wrapped = [(ln, c) for text, c in lines for ln in wrap_text(text, f, tw)]
        h = len(wrapped) * f.get_height() + 12
        r = pygame.Rect(L.bar.x, L.bar.y - h - 6, L.bar.w, h)
        round_rect(scr, r, gfx.NOTE_BG, 10, 2, gfx.GOLD_DARK, alpha=242)
        y = r.y + 6
        for ln, c in wrapped:
            draw_text(scr, ln, r.x + 12, y, f, c)
            y += f.get_height()

    def draw_hand(self, me):
        L, scr = self.L, self.screen
        hand = me["zones"].get("hand", [])
        if not hand:
            return
        ch = L.hand_ch
        cw = int(ch * CARD_ASPECT)
        gap = int(cw * 0.06)
        xs = layout_row([cw] * len(hand), L.hand.x, L.hand.w, gap)
        y = L.hand.y + L.lift
        # `positions[pos]` is the hand index shown at screen position `pos`; sort_by_type reorders the cards without
        # touching Forge's own hand array, so hand[i] (the click target) still matches the id Forge expects.
        positions = sorted(range(len(hand)), key=lambda i: hand_sort_key(hand[i])) if self.hand_sort_by_type \
            else list(range(len(hand)))
        raw_hovered = None
        for pos in range(len(positions) - 1, -1, -1):
            if pygame.Rect(xs[pos], y - L.lift, cw, ch + L.lift).collidepoint(self.mouse) and \
                    L.hand.collidepoint(self.mouse):
                raw_hovered = pos
                break
        hovered = self.hover_hand_target(raw_hovered, time.monotonic())        # round 23: 60 ms hysteresis before it switches
        if hovered != self._last_hover_cue_index:
            self._last_hover_cue_index = hovered
            if hovered is not None and self.sound.get("hover_tick", True) and \
                    (hand[positions[hovered]].get("selectable") or hand[positions[hovered]].get("weak")):
                self.play_cue("ui.hover")            # round 24: never for a card you cannot act on
        order = [pos for pos in range(len(positions)) if pos != hovered] + ([hovered] if hovered is not None else [])
        for pos in order:
            i = positions[pos]
            c = hand[i]
            cm = self.get_motion(c["id"])
            target_lift = L.lift if pos == hovered else (L.lift // 3 if (c.get("weak") or c.get("selectable")) else 0)
            cm.lift.target = target_lift
            lift = cm.lift.value if self.animations else float(target_lift)
            hover_scale = 1.0 + 0.06 * (lift / L.lift) if (pos == hovered and L.lift > 0) else 1.0
            scale = hover_scale * cm.scale.value
            if abs(scale - 1.0) > 0.001:
                r = self.draw_hand_card_scaled(c, xs[pos], y, cw, ch, lift, scale)
            else:
                r = self.draw_card_at(c, xs[pos], y, cw, ch, plate=False, lift=int(round(lift)))
            self.card_rects[c["id"]] = r
            hit = pygame.Rect(r.x, r.y, r.w, r.h + int(round(lift))) if pos == hovered else r
            self.add_hit(hit, "card", card=c)

    def draw_hand_card_scaled(self, card, x, y, w, h, lift, scale):
        """Like draw_card_at, but the whole picture is scaled around its centre: the hand's hover pop and press dip (round 23). The
        scaled surface is cached per (card key, scale quantised to 0.01) so hovering does not smoothscale every frame."""
        if self.is_flying(card):
            return pygame.Rect(x, y - lift, w, h)
        skey, surf = self.card_surface_keyed(card, w, h, plate=False)
        qscale = round(scale, 2)
        key = ("scaled", skey, qscale)
        scaled = gfx.recall(key)
        if scaled is None:
            sw, sh = max(2, int(w * qscale)), max(2, int(h * qscale))
            scaled = gfx.remember(key, surf if qscale == 1.0 else pygame.transform.smoothscale(surf, (sw, sh)))
        rect = scaled.get_rect(center=(int(x + w / 2), int(y - lift + h / 2)))
        gfx.shadowed(self.screen, rect, radius=max(3, int(w * 0.06)))
        self.screen.blit(scaled, rect)
        self.draw_card_flags(card, rect)
        return pygame.Rect(x, int(y - lift), w, h)

    def press_dip(self, cid):
        """A card was clicked: dip its scale to 0.96 and let the PRESS spring bring it back to 1.0 (round 23). The click itself is
        sent to Forge immediately elsewhere; this is purely visual feedback."""
        cm = self.get_motion(cid)
        cm.scale.value, cm.scale.velocity, cm.scale.target = 0.96, 0.0, 1.0

    # ---- right column -------------------------------------------------------------------------

    def preview_card(self):
        kind, data = self.hit_at(self.mouse) if not self.modal else (None, None)
        if kind == "card" and data.get("card"):
            return data["card"]
        if kind == "stack" and data.get("card"):
            return data["card"]
        if kind == "zone" and data.get("card"):                 # hovering a graveyard / exile pile shows its top card
            return data["card"]
        if kind == "logcard":                                   # a card name in the game log
            return self.card_by_name(data["name"])
        if self.pinned is not None:
            return self.find_card(self.pinned)
        src = self.prompt_library_card()
        if src is not None:                                     # round 27e: surveil / scry 1 / "look at the top card"
            return src
        name = commander_zone_name(((self.state or {}).get("prompt") or {}).get("message"))
        if name:                                                # round 27e: the commander the command-zone question is about
            card = self.card_by_name(name)
            if card is not None:
                return card
        name = self.spot_name()
        if name:
            card = self.card_by_name(name)
            if card is not None:
                return card
        return None

    def prompt_library_card(self):
        """The card Forge's current question is about when it is one you can't see anywhere on the table - the top of a
        library (surveil, scry 1, "look at the top card"). Karl, 2026-09-26: the action bar's thumbnail was too small to read,
        so the focus panel shows it while the question is open. None otherwise."""
        src = ((self.state or {}).get("prompt") or {}).get("source")
        if isinstance(src, dict) and src.get("zone") == "Library" and not src.get("hidden"):
            return src
        return None

    def spot_room(self):
        """Patch UI6: the AI spotlight needs somewhere to go: the focus panel, or the card slot under the stack when it has room."""
        L = self.L
        return bool(getattr(L, "panel_on", True) or getattr(L, "aux_ok", False))

    def aux_forced(self):
        """Patch UI6: the card a question is about, which needs a place to be seen when the focus panel is off: the top of a library
        (surveil, scry) or the commander the command-zone question names. None otherwise."""
        src = self.prompt_library_card()
        if src is not None:
            return src
        name = commander_zone_name(((self.state or {}).get("prompt") or {}).get("message"))
        return self.card_by_name(name) if name else None

    def draw_aux_card(self):
        """Patch UI6 (Focus: Over card or Hidden): the card slot under the stack. It shows what a question is about and, with Animations
        off (the AI spotlight is an animation), the card the AI just cast, as the panel does for three seconds."""
        r = self.L.aux
        if r is None or not self.state:
            return
        card = self.aux_forced()
        if card is None and not self.animations:
            name = self.spot_name()
            card = self.card_by_name(name) if name else None
        if card is None:
            return
        gfx.shadowed(self.screen, r, max(6, int(r.w * 0.05)), 4, 110)
        self.screen.blit(self.preview_surface(card, r.w, r.h), r)

    def flow_up(self):
        """A full-screen moment (the opening hand, VICTORY / DEFEAT) is covering the table."""
        key = self.flow_key()
        return key is not None and self.flow_hidden != key

    def ui_free(self):
        """Patch UI6: the table itself is under the mouse - no dialog, pop-up, tour or full-screen moment on top of it."""
        return self.modal is None and self.overlay is None and not self.tour_visible() and not self.flow_up()

    def update_log_corner(self, now):
        """Log: Corner. The log opens when the mouse is in the window's lower-right corner, stays while the mouse is over the corner or
        the log, and closes LOG_CLOSE_SECONDS after it has left both. Returns whether it is open."""
        L = self.L
        free = self.log_mode == "corner" and self.ui_free()
        inside = free and (L.hot.collidepoint(self.mouse) or (self._log_open and L.log_overlay.collidepoint(self.mouse)))
        if inside:
            self._log_open, self._log_left = True, None
        elif self._log_open:
            if not free:
                self._log_open, self._log_left = False, None
            elif self._log_left is None:
                self._log_left = now
            elif now - self._log_left >= LOG_CLOSE_SECONDS:
                self._log_open, self._log_left = False, None
        return self._log_open

    def draw_log_overlay(self):
        """Log: Corner - the log over the right column's lower part while the mouse is in the corner; a faint "Log" mark where the
        corner is otherwise. Drawn over the stack (never over the action bar, which is not in this column) and over the cards."""
        if self.log_mode != "corner":
            return
        L = self.L
        if self.update_log_corner(self.ui_clock()):
            self.add_hit(L.log_overlay, "logpanel")             # first, so a card name drawn inside it (registered next) wins
            self._draw_log_panel(L.log_overlay, 255)           # opaque: the stack's words must not show through the log's lines
        elif self.ui_free():
            mark = gfx.lerp(gfx.DIM, gfx.BACKDROP, 0.35)
            draw_text(self.screen, "Log", L.W - 8, L.H - 5, self.font("tiny"), mark, "bottomright")

    def hover_card_target(self):
        """(card, rect) for the card picture the mouse is over - in the hand, on the battlefield, in the command zone, a row of the
        stack, a pile's top card, or a card name in the log - else None. The rect is where the card is drawn."""
        if self.modal is not None:
            return None
        for rect, kind, data in reversed(self.hits):
            if not rect.collidepoint(self.mouse):
                continue
            if kind in ("card", "stack", "zone"):
                card = data.get("card")
            elif kind == "logcard":
                card = self.card_by_name(data["name"])
            else:
                card = None
            if card is None:
                return None
            drawn = self.card_rects.get(card.get("id")) if kind == "card" else None
            return card, pygame.Rect(drawn if drawn is not None else rect)
        return None

    def update_over_card(self, now):
        """Focus: Over card. The big picture shows once the mouse has rested on one card for OVER_DWELL; it moves to another card after
        OVER_SWITCH on it and goes OVER_SWITCH after the mouse leaves every card. Sweeping across cards shows nothing."""
        hov = self.hover_card_target() if self.preview_mode == "over_card" else None
        key = None
        if hov is not None:
            cid = hov[0].get("id")
            key = ("c", cid) if cid is not None else ("n", hov[0].get("name"))
        if key is None:
            self._over_cand = None
            if self._over_key is not None:
                if self._over_miss is None:
                    self._over_miss = now
                elif now - self._over_miss >= OVER_SWITCH:
                    self._over_key = self._over_miss = self._over_target = None
        elif key == self._over_key:
            self._over_cand = self._over_miss = None
            self._over_target = hov
        else:
            if self._over_cand is None or self._over_cand[0] != key:
                self._over_cand = (key, now)
            need = OVER_DWELL if self._over_key is None else OVER_SWITCH
            if now - self._over_cand[1] >= need:
                self._over_key, self._over_target = key, hov
                self._over_cand = self._over_miss = None

    def draw_over_card(self, now):
        """Focus: Over card - the card's big picture centred over the card the mouse rests on (above the hand for a hand or command-zone
        card), kept inside the window. With Over card or Hidden, a right-click pin shows it the same way. It takes no clicks: hits
        stay with the cards underneath."""
        self.over_rect = None
        if self.preview_mode == "always" or not self.ui_free():
            self._over_cand = self._over_key = self._over_miss = self._over_target = None
            return
        self.update_over_card(now)
        tgt = self._over_target if self._over_key is not None else None
        if tgt is None and self.pinned is not None:
            pc, pr = self.find_card(self.pinned), self.card_rects.get(self.pinned)
            if pc is not None and pr is not None:
                tgt = (pc, pr)
        if tgt is None:
            return
        card, crect = tgt
        L, scr = self.L, self.screen
        m = L.margin
        w, h = L.over_size
        cap = self.caption_height(card, w)
        extra = (cap + 4) if cap else 0
        if h + extra > L.H - 2 * m:                             # a very short window: the picture gives way, never the caption
            h = max(40, L.H - 2 * m - extra)
            w = int(h * CARD_ASPECT)
        block = h + extra
        x = crect.centerx - w // 2
        y = (crect.top - 6 - block) if crect.centery >= L.bar.y else (crect.centery - h // 2)
        x = clamp(x, m, max(m, L.W - m - w))
        y = clamp(y, m, max(m, L.H - m - block))
        ceiling = L.bar.y - 6 - block                           # never over the action bar (its prompt and buttons) where it fits above
        if ceiling >= m and pygame.Rect(x, y, w, block).colliderect(L.bar):
            y = ceiling
        pr = pygame.Rect(x, y, w, h)
        gfx.shadowed(scr, pr, max(6, int(w * 0.05)), 6, 150)
        scr.blit(self.preview_surface(card, w, h), pr)
        if self.pinned is not None and card.get("id") == self.pinned:
            draw_text(scr, "pinned", pr.right - 6, pr.top + 4, self.font("tiny", True), GOLD, "topright", True)
        if self.art and not (card.get("hidden") or card.get("faceDown")) and self.art_name(card) and \
                self.art.is_custom(self.art_key(card)):
            draw_text(scr, "custom art", pr.left + 6, pr.top + 4, self.font("tiny", True), GOLD, "topleft", True)
        if cap:
            self.draw_imprint_caption(card, pygame.Rect(pr.x, pr.bottom + 4, pr.w, cap))
        self.over_rect = pr

    def preview_surface(self, card, w, h):
        """The big picture of a card at w x h, shared by the focus panel and (patch UI6) the over-card picture and the card slot under the
        stack: its Scryfall picture with rounded corners, a drawn stand-in when there is none, a card back for a hidden card."""
        radius = max(6, int(w * 0.05))
        if card.get("hidden") or card.get("faceDown"):
            return gfx.card_back(w, h, radius)
        if self.art_name(card):
            aname = self.art_key(card)
            big = self.art.preview(aname, w, h)
            if big is not None:
                custom = self.art.is_custom(aname)
                key = ("prev", aname, w, h) + (("custom", self.art.custom_gen) if custom else ())
                return gfx.recall(key) or gfx.remember(key, gfx.rounded_image(big, w, h, radius))
        return gfx.card_face(card, w, h, radius, show_pt=True)

    def draw_preview(self):
        if self.preview_mode != "always":                       # patch UI6: no panel; only the card slot under the stack
            self.draw_aux_card()
            return
        L, scr = self.L, self.screen
        r = L.preview
        card = self.preview_card()
        if card is not None:
            self.last_preview = card
        else:
            card = self.last_preview
        if card is None:
            round_rect(scr, r, gfx.LOG_BG, 12, 1, gfx.LOG_EDGE, alpha=200)
            draw_text(scr, "Hover a card", r.centerx, r.centery, self.font("small"), DIM, "center")
            return
        radius = max(6, int(r.w * 0.05))
        surf = self.preview_surface(card, r.w, r.h)
        gfx.shadowed(scr, r, radius, 4, 110)
        scr.blit(surf, r)
        if self.pinned is not None and card.get("id") == self.pinned:
            draw_text(scr, "pinned", r.right - 6, r.top + 4, self.font("tiny", True), GOLD, "topright", True)
        if self.art and not (card.get("hidden") or card.get("faceDown")) and self.art_name(card) and \
                self.art.is_custom(self.art_key(card)):                # round ALT1: your own picture, not Scryfall's
            draw_text(scr, "custom art", r.left + 6, r.top + 4, self.font("tiny", True), GOLD, "topleft", True)
        self.draw_imprint_caption(card, L.caption)

    def imprint_caption(self, card):
        """'Imprinted: Samut, Tyrant of Naktamun (blue)' for a card with something exiled under it, '' for any other card."""
        if card.get("hidden") or card.get("faceDown"):
            return ""
        imp = card.get("imprinted") or []
        if not imp:
            return "Nothing imprinted: makes no mana" if makes_imprinted_colours(card) else ""
        parts = []
        for i in imp:
            cols = "/".join(COLOUR_WORDS[c] for c in "WUBRG" if c in (i.get("colors") or [])) or "colourless"
            parts.append(f"{i.get('name', '?')} ({cols})")
        return "Imprinted: " + ", ".join(parts)

    def keyword_caption(self, card):
        """'Gained: Flying' when the permanent has a keyword its own text does not mention, '' otherwise (and for hidden cards)."""
        if card.get("hidden") or card.get("faceDown"):
            return ""
        gained = gained_keywords(card)
        return "Gained: " + ", ".join(gained) if gained else ""

    def caption_height(self, card, width):
        """How tall draw_imprint_caption's strip is for this card at this width (0: it has nothing to say)."""
        notes = [t for t in (self.imprint_caption(card), self.keyword_caption(card)) if t]
        if not notes:
            return 0
        f = self.font("small", True)
        lines = [ln for t in notes for ln in wrap_text(t, f, width - 16)][:4]
        return len(lines) * f.get_height() + 8

    def draw_imprint_caption(self, card, r):
        """A dark strip BELOW the big card picture (r = L.caption, reserved by make_layout - never over the picture
        itself, so it never covers the artist/copyright line Scryfall's images carry) with what the card's own art
        cannot show: what is imprinted on it and any keyword it has gained (wrapped, at most 4 lines)."""
        notes = [t for t in (self.imprint_caption(card), self.keyword_caption(card)) if t]
        if not notes:
            return None
        f = self.font("small", True)
        lines = [ln for t in notes for ln in wrap_text(t, f, r.w - 16)][:4]
        lh = f.get_height()
        strip = pygame.Rect(r.x + 4, r.y, r.w - 8, min(r.h, len(lines) * lh + 8))
        scr = self.screen
        pygame.draw.rect(scr, gfx.STRIP_BG, strip, border_radius=6)
        pygame.draw.rect(scr, GOLD, strip, 1, border_radius=6)
        for k, ln in enumerate(lines):
            draw_text(scr, ln, strip.centerx, strip.y + 4 + k * lh + lh // 2, f, WHITE, "center")
        return strip

    # ---- what just happened: the stack, the log, the opponent's action feed -----------------------------------

    def player_label(self, pid):
        me = self.session.me()
        if me is not None and pid == me["id"] and not getattr(self.session, "spectator", False):   # Round MP2: a spectator is nobody
            return "You"
        pl = self.session.player(pid)
        return pl["name"] if pl else "?"

    def target_names(self, item):
        names = []
        for t in item.get("targets") or []:
            try:
                n = int(t[1:])
            except ValueError:
                continue
            if t.startswith("c"):
                card = self.session.card(n)
                names.append(card["name"] if card and not card.get("hidden") else "a card")
            else:
                names.append(self.player_label(n))
        return names

    def stack_lines(self, item, index, count, small, sbold, width, room=None):
        """The text beside a stack item's picture: who put it there and what kind of thing it is, its name, its targets, and what it
        does: the ability's own words for a trigger or ability, the card's rules text for a spell (at most SPELL_TEXT_LINES lines of
        it; hover the row to read the whole card). Every line fits `width`. With `room`, only that many lines are returned."""
        who = short_name(self.player_label(item.get("activator")))
        kind = "trigger" if item.get("trigger") else "ability" if item.get("ability") else "spell"
        card = item.get("card") or {}
        name = card.get("name") or ""
        head = f"{who}  -  {kind}" + ("  -  next" if index == 0 and count > 1 else "")
        def whole_or_wrapped(text, font):                     # keeps the wide gaps in the heading unless it has to wrap
            return [text] if font.size(text)[0] <= width else wrap_text(text, font, width)
        lines = [(ln, sbold, LOG_TEXT[flog.ME if who == "You" else flog.OPP]) for ln in whole_or_wrapped(head, sbold)]
        if name:
            lines += [(ln, sbold, WHITE) for ln in whole_or_wrapped(name, sbold)]
        extra = []
        targets = self.target_names(item)
        if targets:
            extra += [(ln, small, LOG_TEXT[flog.CARD]) for ln in wrap_text("Targets: " + ", ".join(targets), small, width)]
        detail = flog.tidy(clean_text(item.get("text", ""))).replace("\n", " ")
        if kind != "spell" and detail and detail != name:
            if name and detail.startswith(name):
                detail = detail[len(name):].lstrip(" :-")
            extra += [(ln, small, LOG_TEXT[flog.DIM]) for ln in wrap_text(detail, small, width)]
        if kind != "spell":
            printed = flog.printed_wording(item)
            if printed:                                       # Forge worked a number out too early: show what the card itself says
                words = wrap_text("Card says: " + clean_text(printed).replace("\n", " "), small, width)
                if len(words) > ABILITY_TEXT_LINES:
                    words = words[:ABILITY_TEXT_LINES]
                    words[-1] = clip_text(words[-1] + " ...", small, width)
                extra += [(ln, small, LOG_TEXT[flog.CARD]) for ln in words]
        if kind == "spell" and card.get("text"):
            words = []
            for para in clean_text(card["text"]).replace("\r", "").split("\n"):
                if para.strip():
                    words += wrap_text(para.strip(), small, width)
            if len(words) > SPELL_TEXT_LINES:
                words = words[:SPELL_TEXT_LINES]
                words[-1] = clip_text(words[-1] + " ...", small, width)
            extra += [(ln, small, LOG_TEXT[flog.DIM]) for ln in words]
        out = lines + extra
        return out if room is None else out[:max(1, room)]

    def stack_geometry(self, L):
        """Sizes shared by the layout (how tall the stack panel must be) and draw_stack."""
        th = int(60 * L.fs)
        return SimpleNamespace(th=th, tw=int(th * CARD_ASPECT), min_h=th + 8, gap=3, head=int(get_font(L.px["small"], True).get_height()) + 12)

    def stack_rows(self, L, stack):
        """[(item, lines, row height)] for the whole stack, and the height the panel needs to show all of it."""
        g = self.stack_geometry(L)
        small, sbold = get_font(L.px["small"]), get_font(L.px["small"], True)
        lh = max(small.get_linesize(), small.get_height())                 # the height a line of text is really drawn at
        rows, total = [], g.head + 4
        for i, item in enumerate(stack):
            width = L.right_w - 12 - 16 - (g.tw + 4 if item.get("card") else 0)
            lines = self.stack_lines(item, i, len(stack), small, sbold, max(40, width), None)
            h = max(g.min_h, lh * len(lines) + 12)
            rows.append((item, lines, h))
            total += h + g.gap
        return rows, total

    def draw_stack(self):
        L, scr, s = self.L, self.screen, self.state
        stack = s.get("stack", [])
        if not stack:
            return
        r = L.stack
        g = self.stack_geometry(L)
        spot = self.spot_name()
        flash = bool(spot) and int(time.time() * 4) % 2 == 0 and any((it.get("card") or {}).get("name") == spot for it in stack)
        round_rect(scr, r, gfx.PROMPT_GOLD_BG, 12, 2, GOLD if flash else gfx.GOLD_DARK, alpha=235)
        f = self.font("small", True)
        small = self.font("small")
        lh = max(small.get_linesize(), small.get_height())
        content = pygame.Rect(r.x + 3, r.y + g.head, r.w - 6, max(10, r.h - g.head - 4))
        total = sum(h + g.gap for _i, _l, h in L.stack_rows)
        max_scroll = max(0, total - content.h)
        self.stack_scroll = max(0, min(self.stack_scroll, max_scroll))
        head = (f"Stack ({len(stack)})  -  wheel: more" if max_scroll
                else f"Stack ({len(stack)})" + ("  -  top resolves first" if len(stack) > 1 else ""))
        draw_text(scr, clip_text(head, f, r.w - 20), r.x + 10, r.y + 6, f, GOLD)
        saved_clip = scr.get_clip()
        scr.set_clip(content)
        y = content.y - self.stack_scroll
        for i, (item, lines, h) in enumerate(L.stack_rows):
            rr = pygame.Rect(r.x + 6, y, r.w - 12, h)
            y += h + g.gap
            if rr.bottom < content.top or rr.top > content.bottom:
                continue
            round_rect(scr, rr, gfx.ROW_FIRST_BG if i == 0 else gfx.ROW_GOLD_BG, 8, 1 if i == 0 else 0, gfx.GOLD_DARK)
            card = item.get("card")
            tx = rr.x + 6
            if card:
                scr.blit(self.card_surface(card, g.tw, g.th, plate=False), (rr.x + 4, rr.y + 4))
                tx = rr.x + g.tw + 10
            if i < ffx.STACK_MARK_MAX or ffx.kind_of(item) == "spell":
                nr = max(8, int(8 * L.fs))                                    # the number that ties this row to the ring on its source card
                ffx.draw_number(scr, (rr.x + 4 + nr - 2, rr.y + 4 + nr - 2), nr, ffx.KIND_COLOUR[ffx.kind_of(item)], i + 1, dim=i != 0)
            ty = rr.y + max(6, (rr.h - lh * len(lines)) // 2)
            for text, font, colour in lines:
                draw_text(scr, text, tx, ty, font, colour)
                ty += lh
            visible = rr.clip(content)
            if visible.w > 0 and visible.h > 0:
                self.add_hit(visible, "stack", card=card, item=item)
                self.stack_row_rects[i] = visible
                if card and card.get("id") is not None:
                    self.stack_rects.setdefault(card["id"], visible)
        scr.set_clip(saved_clip)

    # -- the game log

    def card_names(self):
        names = set()
        for c in self.session._cards.values():
            if not c.get("hidden") and not c.get("faceDown"):
                names.add(c.get("name"))
                names.add(c.get("oracleName"))
        names.discard(None)
        names.discard("")
        return names

    def card_by_name(self, name):
        found = None
        for c in self.session._cards.values():
            if name in (c.get("name"), c.get("oracleName")) and not c.get("hidden"):
                if c.get("zone") in ("Battlefield", "Stack"):
                    return c
                found = found or c
        return found

    def wrap_row(self, row, width, f, fb):
        if row.kind == "spacer":
            return [LogLine("spacer")]
        if row.kind == "turn":
            return [LogLine("turn", label=row.label, mine=row.mine, turn=row.turn)]
        if row.kind == "phase":
            return [LogLine("phase", label=row.label)]
        indent = 14 if row.indent else 0
        parts = flog.wrap_segments(row.segs, width - 8 - indent, f, fb, 0, 12)
        return [LogLine("line", tokens=p, bar=row.bar, indent=indent + (12 if i else 0)) for i, p in enumerate(parts)]

    def log_panel_rect(self):
        """Where the log is drawn (or would be): the column's rect, or the corner overlay's. Its width decides how the lines wrap."""
        L = self.L
        return L.log if L.log_col else L.log_overlay

    def update_log_cache(self):
        s, L = self.session, self.L
        key = (self.log_panel_rect().w, L.px["small"])
        history = self._log_seen is None                          # the first look at the log is history, not news
        if history:
            self._log_seen = max(0, s.log_total - len(s.log))
        new = s.log_total - self._log_seen
        fresh = []
        if new > 0:
            entries = s.log[-new:] if new <= len(s.log) else s.log
            self._log_seen = s.log_total
            me = s.me()
            self.formatter.context(me["name"] if me else "", [o["name"] for o in s.opponents()], self.card_names())
            for e in entries:
                fresh.extend(self.formatter.format(e))
            for row in fresh:
                if row.feed and self.state and not history:
                    self.add_feed(row)
            self._log_rows.extend(fresh)
            del self._log_rows[:-3000]
        if self._extra_rows:
            self._log_rows.extend(self._extra_rows)
            fresh.extend(self._extra_rows)
            self._extra_rows = []
        if key != self._log_key:                                  # the panel changed size: wrap the whole history again
            self._log_key, self._log_lines = key, []
            fresh = list(self._log_rows)
        if not fresh:
            return
        width = self.log_panel_rect().w - 20
        f, fb = get_font(L.px["small"]), get_font(L.px["small"], True)
        for row in fresh:
            if row.kind == "spacer" and not self._log_lines:
                continue
            self._log_lines.extend(self.wrap_row(row, width, f, fb))
        del self._log_lines[:-4000]

    def draw_log(self):
        """The log in the right column. Patch UI6: with Log set to Corner or Hidden nothing is drawn here, but the lines are still taken
        in (they also feed the opponent feed and the AI spotlight); the corner overlay draws later, over everything else."""
        self.update_log_cache()
        if self.L.log_col:
            self._draw_log_panel(self.L.log, 235)
        else:
            self.log_rect = pygame.Rect(0, 0, 0, 0)

    def _draw_log_panel(self, r, alpha):
        L, scr = self.L, self.screen
        round_rect(scr, r, gfx.PANEL_DEEP, 12, 1, gfx.OVERLAY_EDGE, alpha=alpha)
        self.log_rect = r
        f, fb = self.font("small"), self.font("small", True)
        draw_text(scr, "Game log", r.x + 10, r.y + 6, self.ui_font("small", True), DIM)      # round 32: a heading, in the display face
        lh = f.get_height() + 2
        top = r.y + 8 + fb.get_height() + 2
        rows = max(1, (r.bottom - top - 6) // lh)
        lines = self._log_lines
        self.log_scroll = clamp(self.log_scroll, 0, max(0, len(lines) - rows))
        end = len(lines) - self.log_scroll
        start = max(0, end - rows)
        y = top
        scr.set_clip(pygame.Rect(r.x + 2, top, r.w - 4, r.bottom - top - 4))
        for ln in lines[start:end]:
            self.draw_log_line(ln, r.x + 8, y, r.w - 16, lh, f, fb)
            y += lh
        scr.set_clip(None)
        if self.log_scroll:
            draw_text(scr, "scroll down for newest", r.right - 8, r.y + 6, self.font("tiny"), GOLD, "topright")

    def draw_log_line(self, ln, x, y, w, lh, f, fb):
        scr = self.screen
        if ln.kind == "turn":
            accent = GOLD if ln.mine else ORANGE
            box = pygame.Rect(x, y + 1, w, lh - 2)
            round_rect(scr, box, gfx.FEED_MINE_BG if ln.mine else gfx.FEED_OPP_BG, 6, 1, accent)
            who = "your turn" if ln.mine else f"{ln.label}'s turn"
            draw_text(scr, clip_text(f"Turn {ln.turn}  -  {who}", fb, w - 14), box.x + 8, box.centery, fb, accent, "midleft")
        elif ln.kind == "phase":
            end = draw_text(scr, ln.label, x + 6, y + 1, f, LOG_TEXT[flog.DIM]).right
            pygame.draw.line(scr, gfx.LOG_DIVIDER, (end + 8, y + lh // 2), (x + w - 4, y + lh // 2), 1)
        elif ln.kind == "line":
            pygame.draw.rect(scr, LOG_COLOURS.get(ln.bar, DIM), pygame.Rect(x, y + 2, 3, lh - 4), border_radius=1)
            px = x + 9 + ln.indent
            for text, style, ref in ln.tokens:
                rect = draw_text(scr, text, px, y + 1, fb if style in flog.BOLD_STYLES else f, LOG_TEXT.get(style, TEXT))
                if ref:
                    self.add_hit(rect, "logcard", name=ref)
                px = rect.right

    # -- the opponent's latest actions, flashed on the board

    def spot_name(self):
        return self.spot[0] if self.spot and time.time() < self.spot[1] else None

    def add_feed(self, row):
        now = time.time()
        self.feed.append((row, now))
        del self.feed[:-FEED_MAX]
        if row.card:
            self.spot = (row.card, now + 3.0)

    def draw_feed(self):
        now = time.time()
        self.feed = [(r, t) for r, t in self.feed if now - t < FEED_SECONDS]
        if not self.feed:
            return
        L, scr = self.L, self.screen
        f, fb = self.font("small"), self.font("small", True)
        lh = f.get_height()
        w = int(min(L.main.w * 0.46, 540 * max(1.0, L.fs)))
        x, y = L.main.right - w - 6, L.main.y + 4
        for row, born in self.feed:
            age = now - born
            lines = flog.wrap_segments(row.segs, w - 26, f, fb)
            h = len(lines) * lh + 10
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            colour = LOG_COLOURS.get(row.bar, DIM)
            pygame.draw.rect(surf, gfx.OVERLAY_BG, surf.get_rect(), border_radius=10)
            pygame.draw.rect(surf, colour, surf.get_rect(), 1, border_radius=10)
            pygame.draw.rect(surf, colour, pygame.Rect(0, 6, 4, h - 12), border_radius=2)
            ty = 5
            for parts in lines:
                px = 14
                for text, style, _ref in parts:
                    rect = draw_text(surf, text, px, ty, fb if style in flog.BOLD_STYLES else f, LOG_TEXT.get(style, TEXT))
                    px = rect.right
                ty += lh
            surf.set_alpha(255 if age < FEED_SECONDS - 1 else max(0, int(255 * (FEED_SECONDS - age))))
            scr.blit(surf, (x, y))
            y += h + 4

    def draw_spotlight(self):
        """The card an opponent just cast or played glows orange for a moment. Only the copy that just arrived: an older copy with the
        same name (two Forests, a second Sol Ring) used to glow as well."""
        name = self.spot_name()
        if not name:
            return
        recent = set(self.arrivals.recent(time.time()))
        pulse = 2 + int(2 * abs(time.time() * 2 % 2 - 1)) if self.animations else 3
        for cid, rect in self.card_rects.items():
            card = self.find_card(cid) if cid in recent else None
            if card and card.get("name") == name and card.get("zone") == "Battlefield":
                gfx.glow(self.screen, rect, ORANGE, max(4, int(rect.w * 0.06)), pulse, 5)

    def draw_effects(self):
        """A ring on every permanent that just entered, and on the source of every trigger / ability on the stack (with the same
        number as its row in the stack panel)."""
        now = time.time()
        now_m = time.monotonic()
        for cid, rect in self.card_rects.items():
            cm = self.motion.get(cid)
            look = self.arrivals.look(cid, now)
            if look and not (cm and cm.flying()):        # the ring waits until the card has actually landed (round 23: spring flights)
                age, dur, land = look
                if age >= 0:
                    ffx.draw_arrival(self.screen, rect, age, dur, land, self.animations)
            if cm and cm.flying():
                card = self.session.card(cid)
                if card:
                    ffx.draw_spring_flight(self.screen, self.card_surface(card, rect.w, rect.h, plate=True), cm, now_m)
            elif cm and cm.landed_at is not None and now_m - cm.landed_at < LANDING_SQUASH:
                card = self.session.card(cid)
                if card and not card.get("isLand"):        # lands come and go every turn; the squash would be noisy there
                    t = (now_m - cm.landed_at) / LANDING_SQUASH
                    ffx.draw_landing_squash(self.screen, rect, self.card_surface(card, rect.w, rect.h, plate=True), t)
        for cid, marks in ffx.stack_marks(self.state.get("stack", [])).items():
            rect = self.card_rects.get(cid)
            if rect:
                ffx.draw_marks(self.screen, rect, marks, now, self.animations)

    def is_flying(self, card):
        """True while this permanent is gliding in from where it came from (animations on): the table skips drawing it in place."""
        cm = self.motion.get(card.get("id"))
        return bool(self.animations and card.get("zone") == "Battlefield" and cm and cm.flying())

    # ---- motion (round 23: springs for hover lift, press dip and card flights) ------------------

    def get_motion(self, cid):
        cm = self.motion.get(cid)
        if cm is None:
            cm = self.motion[cid] = CardMotion()
        return cm

    def start_flight(self, cid, origin):
        """A permanent just arrived from somewhere the table can point at: fly its picture there with FLIGHT springs, retargeted
        every frame in step_motion() to wherever it ends up laid out (so a resize mid-flight still lands correctly)."""
        if not self.animations or origin is None:
            return
        cm = self.get_motion(cid)
        cm.x = mot.Spring(origin.centerx, *mot.FLIGHT)
        cm.y = mot.Spring(origin.centery, *mot.FLIGHT)
        cm.flight_start = time.monotonic()
        cm.landed_at = None

    def step_motion(self, now):
        """Step every card's hover/press springs once for this drawn frame (dt since the last drawn frame). Flight (x/y) springs
        are stepped separately in step_flights(), once this frame's card_rects are known (see there for why the order matters)."""
        dt = 0.0 if self._last_draw_now is None else now - self._last_draw_now
        self._last_draw_now = now
        self._motion_dt = dt
        for cm in self.motion.values():
            if not self.animations:
                cm.lift.snap(cm.lift.target)
                cm.scale.snap(cm.scale.target)
            else:
                cm.lift.step(dt)
                cm.scale.step(dt)

    def step_flights(self, now):
        """Advance any card that is mid-flight, and drop motion entries that are fully at rest. Called after this frame's
        card_rects are populated (battlefield and hand are drawn before this), so a flight can be retargeted to the card's
        real destination before its springs are checked: on the very first step after a flight starts, the spring's target
        still equals its starting value (it has nowhere else to point yet), and checking settled() before that first
        retarget would call it 'arrived' immediately."""
        dt = getattr(self, "_motion_dt", 0.0)
        dead = []
        for cid, cm in self.motion.items():
            if cm.x is not None:
                if not self.animations:
                    cm.x = cm.y = None
                    cm.landed_at = None
                else:
                    rect = self.card_rects.get(cid)
                    if rect is not None:
                        cm.x.target, cm.y.target = rect.centerx, rect.centery
                        cm.x.step(dt)
                        cm.y.step(dt)
                    age = now - cm.flight_start if cm.flight_start else 0.0
                    if (rect is not None and cm.x.settled(1.5) and cm.y.settled(1.5)) or age > FLIGHT_TIMEOUT:
                        cm.x = cm.y = None
                        cm.landed_at = now
                        if rect is not None:                    # round 25: a little burst of sparks when it actually lands
                            card = self.session.card(cid)
                            if card and not card.get("isLand"):
                                self.particles.spawn(10, rect.centerx, rect.centery, "white", speed=180, life=0.5, gravity=0)
            if cm.at_rest(now):
                dead.append(cid)
        for cid in dead:
            del self.motion[cid]

    def hover_hand_target(self, raw_hovered, now):
        """The hand index to actually treat as hovered: `raw_hovered` (from the mouse position, this frame) only takes effect once
        the mouse has sat over that (different) card for HOVER_DWELL seconds, or at once if it left the hand entirely (round 23)."""
        if raw_hovered != self._raw_hover_idx:
            self._raw_hover_idx = raw_hovered
            self._raw_hover_since = now
        if raw_hovered is None:
            self.hover_hand_index = None
        elif self.hover_hand_index is None or now - self._raw_hover_since >= HOVER_DWELL:
            self.hover_hand_index = raw_hovered
        return self.hover_hand_index

    def draw_target_lines(self):
        """A dashed arrow from every trigger / ability / spell on the stack to what it targets, numbered like its row in the stack panel."""
        now = time.time()
        for n, kind, source, targets in ffx.stack_targets(self.state.get("stack", [])):
            start = self.card_rects.get(source) if kind != "spell" else None
            if start is None:
                start = self.stack_row_rects.get(n - 1)
            if start is None:
                continue
            for t in targets:
                tid = int(t[1:])
                dest = self.card_rects.get(tid) if t[0] == "c" else self.panel_rects.get(tid)
                if dest is None or dest == start:
                    continue
                ffx.draw_link(self.screen, start, dest, ffx.KIND_COLOUR[kind], now, self.animations, n == 1, n,
                              badge_r=max(9, int(9 * self.L.fs)))

    # ---- combat lines --------------------------------------------------------------------------

    def draw_combat_lines(self):
        now = time.monotonic()

        def grown(start, end, k):                              # Round AD2c: a new line grows out from the attacker
            t = self.anim.grow(a["card"], k, now) if self.animations else 1.0
            return t, (int(start[0] + (end[0] - start[0]) * t), int(start[1] + (end[1] - start[1]) * t))
        for a in self.state.get("combat", []):
            ra = self.card_rects.get(a["card"])
            if ra is None:
                continue
            for b in a.get("blockers", []):
                rb = self.card_rects.get(b)
                if rb is not None:
                    t, tip = grown(ra.center, rb.center, ("b", b))
                    pygame.draw.line(self.screen, CYAN, ra.center, tip, 3)
                    if t >= 1:
                        pygame.draw.circle(self.screen, CYAN, rb.center, 5)
            d = a.get("defender", "")
            if d.startswith("c"):
                rd = self.card_rects.get(int(d[1:]))
                if rd is not None:
                    pygame.draw.line(self.screen, RED, ra.center, grown(ra.center, rd.center, ("d", d))[1], 2)
            elif d.startswith("p"):                            # round 25: a pod attack points at the defending player's panel
                rd = self.panel_rects.get(int(d[1:]))
                if rd is not None:
                    t, tip = grown(ra.center, rd.center, ("d", d))
                    if t >= 1:
                        ffx.draw_link(self.screen, ra, rd, RED, time.time(), self.animations, True, None)
                    else:
                        pygame.draw.line(self.screen, RED, ra.center, tip, 2)

    # ---- frame ---------------------------------------------------------------------------------

    def draw_splash(self):
        scr, L = self.screen, self.L
        s = self.session
        big, body, small = self.font("big", True), self.font("body"), self.font("small")
        gap = max(10, int(14 * L.fs))

        def lh(font):                                                            # the height a line of text is really drawn at
            return max(font.get_linesize(), font.get_height(), font.size("Ag")[1])

        room = max(200, min(L.W - 2 * L.margin - 40, int(760 * L.fs)))        # text never runs wider than this
        if s.fatal or s.exited:
            msg = s.fatal or "Forge stopped before the game started."
            title = wrap_text("Forge could not start the game", big, room)
            lines = wrap_text(msg, body, room)[:6]
            foot = wrap_text("Details are in forge_engine.log next to this program. Close the window to quit.", small, room)
            total = (len(title) * lh(big) + gap + len(lines) * (lh(body) + 4) + gap
                     + len(foot) * lh(small))
            y = max(L.margin, (L.H - total) // 2)
            for ln in title:
                draw_text(scr, ln, L.W // 2, y, big, RED, "midtop")
                y += lh(big)
            y += gap
            for ln in lines:
                draw_text(scr, ln, L.W // 2, y, body, WHITE, "midtop")
                y += lh(body) + 4
            y += gap
            for ln in foot:
                draw_text(scr, ln, L.W // 2, y, small, DIM, "midtop")
                y += lh(small)
            return
        # the words are measured, so the lines can never overlap however big the text setting is; the dots sit in a fixed slot
        # (and the whole title is centred with all three of them) so the title does not shake from side to side
        title = "Loading"                                                  # Karl, 2 Oct: just "Loading..." (the dots are drawn below)
        lobby = getattr(s, "lobby", None) if getattr(s, "online", None) == "guest" and not s.ready else None
        if lobby:
            title = "Waiting for players"                                  # Round MP2c: the others haven't all joined yet
        px = L.px["big"]
        if self.vs is not None and self.vs.seats:                        # Round AD2b: commander VS commander, the loading line under it
            px = L.px["title"]
            vs_area = pygame.Rect(L.margin, L.margin + int(10 * L.fs), L.W - 2 * L.margin, int(L.H * 0.68))
            flow.draw_vs(self, self.vs, vs_area)
        while px > 12 and get_font(px, True).size(title + "...")[0] > room:          # a narrow window: shrink the title to fit
            px -= 2
        big = get_font(px, True)
        stage = ""
        if self.art and not self.art.data_ready.is_set():
            stage = self.art.stage
        elif self.art:
            stage = f"Card pictures loading: {self.art.pending()} left"
        hint = wrap_text("The first start takes 10-20 seconds while Forge reads its card scripts.", small, room)
        if lobby:
            stage = onl.lobby_line(lobby, getattr(s, "name", ""))
            hint = wrap_text("The game starts as soon as everyone the host invited has joined.", small, room)
        fmt_line = self.format_line()                                      # round FMT1: "Brawl - 25 life each, no commander damage."
        fmt_rows = wrap_text(fmt_line, small, room) if fmt_line else []
        hint = fmt_rows + hint
        status = wrap_text(stage, body, room)[:2] if stage else []
        bar_w, bar_h = min(room, int(420 * L.fs)), max(5, int(6 * L.fs))
        total = (lh(big) + gap + bar_h + gap + len(status) * lh(body)
                 + (gap // 2 if status else 0) + len(hint) * lh(small))
        y = max(L.margin, (L.H - total) // 2 - int(L.H * 0.03))
        if self.vs is not None and self.vs.seats:
            y = max(y, vs_area.bottom + gap)
            if self.state:                                                 # the engine is ready: say so instead of "Loading..."
                draw_text(scr, "Ready", L.W // 2, y, big, GOLD, "midtop")
                draw_text(scr, "Click or press Space to continue", L.W // 2, y + lh(big) + gap // 2, small, DIM, "midtop")
                if fmt_line:
                    draw_text(scr, clip_text(fmt_line, small, room), L.W // 2, y + lh(big) + gap // 2 + lh(small), small, GOLD, "midtop")
                return
        x0 = L.W // 2 - big.size(title + "...")[0] // 2
        draw_text(scr, title, x0, y, big, WHITE, "topleft")
        draw_text(scr, "." * (1 + int(time.time() * 2) % 3), x0 + big.size(title)[0], y, big, WHITE, "topleft")
        y += lh(big) + gap
        track = pygame.Rect(L.W // 2 - bar_w // 2, y, bar_w, bar_h)          # a bar that slides to and fro: "working", not a percentage
        round_rect(scr, track, gfx.PANEL_EDGE, radius=bar_h // 2)
        seg = max(bar_h * 4, bar_w // 4)
        t = (time.time() * 0.8) % 2.0
        pos = int((bar_w - seg) * (t if t <= 1.0 else 2.0 - t))
        round_rect(scr, pygame.Rect(track.x + pos, track.y, seg, bar_h), gfx.GOLD, radius=bar_h // 2)
        y += bar_h + gap
        for ln in status:
            draw_text(scr, ln, L.W // 2, y, body, TEXT, "midtop")
            y += lh(body)
        if status:
            y += gap // 2
        for i, ln in enumerate(hint):
            draw_text(scr, ln, L.W // 2, y, small, GOLD if i < len(fmt_rows) else DIM, "midtop")
            y += lh(small)

    def draw_flow_layer(self, me):
        """Round AD2b: the full-screen moment, if one is up - VICTORY / DEFEAT, or the opening hand - or, while "View table" hid
        it, one button that brings it back. Drawn after the table, so its hits come last and win (ForgeTable.hit_at)."""
        key = self.flow_key()
        if key is None:
            self.flow_hidden = None
            return
        if self.flow_hidden == key:
            flow.back_button(self, "Back to the result" if key[0] == "end" else "Back to your hand")
            return
        self.flow_hidden = None
        if self.end_screen is not None:
            flow.draw_end(self, self.end_screen)
        elif me:
            flow.draw_pregame(self, me)

    def end_continue(self):
        """VICTORY / DEFEAT -> the end-of-game dialog (New game / Look at the board), as before Round AD2b."""
        self.end_screen = None
        me = self.session.me()
        self.modal = dlg.game_over_dialog(self.state or {}, me["name"] if me else "", self.open_menu,
                                          spectator=getattr(self.session, "spectator", False))
        self.modal.closes_game = False

    # ---- Round UX1: the table tour (tour.py) --------------------------------------------------------------------------------
    def tour_wanted(self):
        """True when the tour should start by itself now: never seen (or an older version), a real program (a launcher), a new game
        (not resumed), and Forge asking me for priority with nothing else on screen - after the VS screen and the mulligan."""
        if os.environ.get("MANTICORE_NO_TOUR") or self.tour is not None or self.tour_done >= ftour.TOUR_VERSION:
            return False
        if self.launcher is None or self.resumed_game or not self.state or self.L is None or self.cog_rect is None:
            return False
        if self.menu or self.boot is not None or self.modal or self.overlay or self.resuming is not None:
            return False
        if (self.vs is not None and self.vs.holding(self)) or self.end_screen is not None or self.flow_key() is not None:
            return False
        if self.state.get("asking") is False or self.L.game_over:
            return False
        kind = self.input_kind()
        return kind == "priority" if kind is not None else str(self.prompt().get("message", "")).startswith("Priority:")

    def maybe_start_tour(self):
        if self.tour_wanted():
            self.start_tour(manual=False)

    def start_tour(self, manual=False):
        """Show the tour now (manual: Cog > Help > Tour of the table). Needs a game on screen; nothing is sent to Forge."""
        if not self.state or self.L is None or self.menu or self.boot is not None:
            self.say("Start a game first - the tour shows the table.", DIM)
            return False
        self.overlay = None
        self.tour = ftour.Tour(self, manual=manual)
        self._force_draw = True
        return True

    def tour_available(self):
        """Cog > Tour of the table is enabled: a game is on the table (not the deck screen, the title or a resume)."""
        return bool(self.state and self.L is not None and not self.menu and self.boot is None and self.resuming is None)

    def tour_visible(self):
        return self.tour is not None and not self.modal and not self.menu and self.boot is None and self.resuming is None

    def _after_tour_input(self):
        self._force_draw = True
        t = self.tour
        if t is None or t.finished is None:
            return
        self.tour = None
        if self.tour_done < ftour.TOUR_VERSION:
            self.tour_done = ftour.TOUR_VERSION
            self.save_settings()
        if t.finished == "skipped" and not t.manual:
            self.say("Tour skipped - Cog > Help > Tour of the table shows it again.", DIM, 5.0)

    def render(self):
        self.draw_frame()
        if self.tour_visible():
            self.tour.draw(self)                # Round UX1: over the table, under the cog pop-up / bug report form
        if self.overlay:
            self.overlay.draw(self)
        pygame.display.flip()

    def draw_frame(self):
        """Everything on the screen except the overlay (settings pop-up / bug report form); no flip."""
        self.L = self.make_layout()
        L, scr = self.L, self.screen
        self.step_motion(time.monotonic())      # round 23: springs advance once per drawn frame, using last frame's card_rects
        self.dim_done = False
        if self.modal is not None and not self.menu and self.freeze_valid():
            scr.blit(self._frozen[1], (0, 0))                 # the table behind a dialog does not change: reuse the dimmed picture
            self.dim_done = True
            self.modal.draw(self)
            return
        self.hits = []
        if self.card_rects:
            self.prev_card_rects = self.card_rects       # round 25: one frame of history, for departure ghosts
        self.card_rects = {}
        self.stack_rects, self.stack_row_rects, self.panel_rects = {}, {}, {}
        scr.blit(gfx.table_background((L.W, L.H), self.current_bg), (0, 0))
        s = self.state
        if self.boot is not None and not self.menu:
            self.boot.draw(self)                    # Round AD2: the splash / the title and main menu
        elif self.menu:
            self.menu.draw(self)
        elif self.resuming is not None:
            self.draw_resume()
        elif self.hosting_wait is not None:
            self.hosting_wait.draw(self)                # Round MP1: the invite code, until the friend joins
        elif not s or (self.vs is not None and self.vs.holding(self)):
            self.draw_splash()
        else:
            opps = self.session.opponents()
            me = self.session.me()
            self.draw_top_bar()
            for opp, rect in zip(opps, L.opp_rects):
                self.draw_opponent(opp, rect)
            if me:
                self.draw_my_side(me)
            self.draw_combat_lines()
            self.draw_spotlight()
            self.step_flights(time.monotonic())      # round 23: retarget/settle flights now that this frame's card_rects exist
            self.draw_effects()
            self.draw_ghosts()                        # round 25
            if self.animations:
                mono = time.monotonic()
                self.anim.draw_auras(self, mono)      # patch 48: arrival glows (and their dust, into the pool before it is drawn)
                self.anim.draw_hits(self, mono)       # patch 48: impact flashes on creatures and panels
                self.anim.draw_pops(self, mono)       # patch 48: the counters badge pops
                self.particles.update(getattr(self, "_motion_dt", 0.0))
                self.particles.draw(self.screen)
            self.draw_stack()
            self.draw_target_lines()
            if self.animations:
                self.anim.draw_links(self, time.monotonic())   # patch 48: trigger pulses + lines to the stack, resolved entries flying to their targets
            self.draw_log()
            self.draw_preview()          # after the stack and the log: their hover areas are registered while they are drawn
            if L.game_over:
                self.draw_cog_button()   # the focus card now reaches up over the old top-bar row; keep the cog on top of it
            self.draw_feed()
            self.draw_advisory()
            self.draw_mana_pool(me)
            if self.animations:              # Round AD2c: drawn over the table, never clicked
                mono = time.monotonic()
                self.anim.draw_floats(self, mono)
                self.anim.draw_spot(self, mono)
                self.anim.draw_banner(self, mono)
            self.draw_log_overlay()          # patch UI6: Log: Corner - the log over the right column while the mouse is in the corner
            self.draw_over_card(self.ui_clock())   # patch UI6: Focus: Over card - the big picture over the card the mouse rests on
            self.draw_flow_layer(me)        # Round AD2b: the opening hand / VICTORY or DEFEAT, over the table
            if self.animations:              # patch 48: banner embers and the VICTORY / DEFEAT particles, over the flow layer
                self.front_particles.update(getattr(self, "_motion_dt", 0.0))
                self.front_particles.draw(self.screen)
            self.draw_toast()
            self.draw_quiet_banner()
            self.draw_online_banner()
            self.draw_tooltip()
        if self.show_perf:
            self.draw_perf()
        if self.animations and not self.modal and self.screen_shaker.active(time.monotonic()):
            dx, dy = self.screen_shaker.offset(time.monotonic())     # round 25: only I losing a lot of life shakes the whole table
            if dx or dy:
                scr.blit(scr.copy(), (dx, dy))
        if self.modal:
            if not self.menu:
                dlg.dim_screen(self)                           # dim once, keep the dimmed table, then let the dialog draw on top
                self._frozen = (self.freeze_key(), scr.copy())
                self.dim_done = True
            self.modal.draw(self)

    def freeze_key(self):
        """What the dimmed table behind a dialog depends on. It is redrawn when one of these changes, otherwise it stays a still picture
        (redrawing a 4K table under a dialog every frame cost about 20 ms of a 16.7 ms budget)."""
        toast = bool(self.toast and time.time() < self.toast[2])
        return (self.screen.get_size(), self.text_scale, self.session.state_version, id(self.modal), self.animations, toast, self.current_bg,
                self.log_mode, self.preview_mode)

    def freeze_valid(self):
        f = getattr(self, "_frozen", None)
        return f is not None and f[0] == self.freeze_key()

    def perf_lines(self):
        """The F3 overlay's text: frame times with and without a dialog, engine reply time, picture cache size, frames drawn."""
        t, d, e = self.perf.summary("table"), self.perf.summary("dialog"), self.perf.engine_summary()
        total = self.perf.drawn + self.perf.skipped
        return [f"Table  p50 {t['p50']} ms   p95 {t['p95']} ms   (n={t['n']})",
                f"Dialog p50 {d['p50']} ms   p95 {d['p95']} ms   (n={d['n']})",
                f"Forge reply  p50 {e['p50']} ms   p95 {e['p95']} ms",
                f"Picture cache {gfx.cache_megabytes():.0f} MB    frames drawn {self.perf.drawn} of {total}",
                f"Bridge protocol {getattr(self.session, 'protocol', 1)}    event gaps {self.router.gaps}    "
                f"stale clicks dropped {len(getattr(self.session, 'dropped', []))}",
                "F3 hides this"]

    def draw_perf(self):
        L, scr = self.L, self.screen
        f = self.font("tiny", True)
        lines = self.perf_lines()
        w = max(f.size(ln)[0] for ln in lines) + 20
        h = len(lines) * f.get_height() + 12
        top = (L.top_h + 4) if L and not getattr(L, "game_over", False) else 6
        r = pygame.Rect(L.margin if L else 6, top, w, h)
        round_rect(scr, r, gfx.PROMPT_BG, 8, 1, CYAN, alpha=230)
        for i, ln in enumerate(lines):
            draw_text(scr, ln, r.x + 10, r.y + 6 + i * f.get_height(), f, CYAN if i < 4 else DIM)

    def draw_toast(self):
        if not self.toast or time.time() > self.toast[2]:
            return
        text, colour, _until = self.toast
        f = self.font("body", True)
        lines = wrap_text(text, f, int(self.L.main.w * 0.7))
        w = max(f.size(ln)[0] for ln in lines)
        h = len(lines) * f.get_height()
        r = pygame.Rect(0, 0, w + 30, h + 14)
        r.midtop = (self.L.main.centerx, self.L.top_h + 6)
        round_rect(self.screen, r, gfx.PANEL_DEEP, 12, 1, colour, alpha=240)
        for i, ln in enumerate(lines):
            draw_text(self.screen, ln, r.centerx, r.y + 7 + i * f.get_height(), f, colour, "midtop")

    # ---- actions --------------------------------------------------------------------------------

    def press(self, name):
        self.play_cue("ui.click")           # round 24
        s = self.session
        if name == "ok":
            s.ok()
        elif name == "cancel":
            s.cancel()
        elif name == "undo":
            self.press_undo()
        elif name == "skip":
            self.open_skip()
        elif name == "settings":
            self.overlay = fset.SettingsPopup()
        elif name == "help":
            self.open_help()
        elif name == "newgame":
            self.ask_new_game()
        elif name == "concede":
            self.ask_concede()
        elif name == "full":
            self.toggle_fullscreen()
        elif name == "motion":
            self.toggle_animations()
        elif name == "bigger":
            self.change_text_scale(1)
        elif name == "smaller":
            self.change_text_scale(-1)
        elif name == "view_table":                  # Round AD2b: hide the full-screen moment to look at the board
            self.flow_hidden = self.flow_key()
        elif name == "show_flow":
            self.flow_hidden = None
        elif name == "end_continue":
            self.end_continue()
        elif name == "end_watch":                   # knocked out of a pod: keep watching the others
            self.end_screen = None
        elif name == "end_leave":
            self.end_screen = None
            self.open_menu()

    def toggle_stop(self, phase, who):
        s = self.state
        if not s:
            return
        stops = s.setdefault("stops", {"mine": [], "theirs": []})
        phases = list(stops.get(who, []))
        if phase in phases:
            phases.remove(phase)
        else:
            phases.append(phase)
        stops[who] = phases                          # show the change at once; Forge confirms with the next snapshot
        self.session.set_stops(who == "mine", phases)
        turn = "your turns" if who == "mine" else "opponents' turns"
        label = dict(PILLS).get(phase, phase)
        self.say(f"{'Stop' if phase in phases else 'No stop'} at {label} on {turn}", CYAN if who == "mine" else ORANGE)

    def explain_library(self, player_id):
        p = self.session.player(player_id)
        if not p:
            return
        if p["id"] == self.session.state.get("me"):
            self.say(f"Your library: {p['libraryCount']} cards, face down. You draw from the top. When an effect lets you search it "
                     "(a fetchland, a tutor) a list of its cards opens.", CYAN, 6.0)
        else:
            self.say(f"{p['name']}'s library: {p['libraryCount']} cards.", CYAN, 3.0)

    def tooltip_text(self):
        """A short hint for whatever the mouse rests on (turn bar, piles)."""
        if self.modal or self.overlay or not self.state:
            return ""
        kind, data = self.hit_at(self.mouse)
        if kind == "stop":
            who = "your turn" if data["who"] == "mine" else "an opponent's turn"
            on = data["phase"] in self.state.get("stops", {}).get(data["who"], [])
            return (f"Stop at {PILL_FULL.get(data['phase'], data['phase'])} on {who}: " + ("ON" if on else "off") +
                    "  (click to change)")
        if kind == "pill":
            on = data["phase"] in self.state.get("stops", {}).get("mine", [])
            return (f"{dict(PILLS).get(data['phase'], data['phase'])}. Click to {'remove' if on else 'add'} a stop here on your turn "
                    "(the blue dot); the orange dot is for opponents' turns.")
        if kind == "button" and data.get("name") == "settings":
            return "Settings: text size, full screen, animations, new game, concede, help, report a bug."
        if kind == "pool":
            if data.get("sym") and self.paying():
                return f"Click to spend one {self.POOL_NAMES.get(data['sym'], data['sym'])} mana on this payment."
            return "Your floating mana. It empties at the end of each step. It pays for things when you press Auto or click it during a payment."
        if kind == "badge":
            return data.get("text", "")
        if kind == "library":
            return "Library (face down). Click for details."
        if kind == "zone":
            return f"Click to look inside the {data['zone']}."
        return ""

    def draw_tooltip(self):
        text = self.tooltip_text()
        if not text:
            return
        f = self.font("small")
        lines = wrap_text(text, f, min(420, self.L.W // 2))
        w = max(f.size(ln)[0] for ln in lines) + 20
        h = len(lines) * f.get_height() + 12
        x = min(self.mouse[0] + 14, self.L.W - w - 6)
        y = self.mouse[1] + 20
        if y + h > self.L.H - 6:
            y = self.mouse[1] - h - 8
        r = pygame.Rect(x, y, w, h)
        round_rect(self.screen, r, gfx.TAG_BG, 8, 1, gfx.TAG_EDGE, alpha=240)
        for i, ln in enumerate(lines):
            draw_text(self.screen, ln, r.x + 10, r.y + 6 + i * f.get_height(), f, WHITE)

    def open_zone(self, player_id, zone):
        player = self.session.player(player_id)
        if player:
            self.modal = dlg.zone_dialog(player, zone, lambda card: self.session.click_card(card["id"]))

    def cancel_label(self):
        return self.prompt().get("cancel", {}).get("label", "").lower()

    def cancel_is_safe(self):
        """Esc may press Cancel only when it really means 'cancel' (not End Turn, not Full Send)."""
        label = self.cancel_label()
        return label in ("cancel", "undo") or label.startswith("undo")

    # ---- events ---------------------------------------------------------------------------------

    # ---- the deck screen ---------------------------------------------------------------------------

    def read_clipboard(self):
        return fmenu.read_clipboard()

    def write_clipboard(self, text):
        return fmenu.write_clipboard(text)

    def game_showing(self):
        """True while there is a game to go back to (running, or just finished and still on screen)."""
        return bool(self.session.state) and not self.session.fatal

    # ---- settings pop-up, help, bug report ------------------------------------------------------

    def open_help(self):
        self.overlay = None
        self.modal = dlg.HelpDialog()

    def open_licenses(self):
        self.overlay = None
        self.modal = licenses_view.LicensesDialog()

    def open_data_folder(self):
        """Cog > Open my data folder (round 28): settings.json, my_decks/ and saves/ - the program folder itself
        in a portable copy (Karl's own), or the per-user folder in an installed one."""
        if not reporting.open_folder(paths.user_dir()):
            self.say("Could not open the folder.", DIM)

    def capture_frame(self):
        """A copy of the screen as the player sees it, without the pop-up on top (the picture in a bug report)."""
        saved, self.overlay = self.overlay, None
        try:
            self.draw_frame()
            return self.screen.copy()
        finally:
            self.overlay = saved

    @staticmethod
    def screenshot_encoder(frame):
        """A function(max_width) -> (bytes, 'jpg'|'png') that shrinks and encodes the captured frame (a report tries smaller and
        smaller pictures until the zip fits)."""
        def encode(max_width):
            img = frame
            if img.get_width() > max_width:
                img = pygame.transform.smoothscale(img, (max_width, max(1, int(img.get_height() * max_width / img.get_width()))))
            for ext in ("jpg", "png"):
                buf = io.BytesIO()
                try:
                    pygame.image.save(img, buf, f"screenshot.{ext}")
                    return buf.getvalue(), ext
                except (pygame.error, ValueError, TypeError):
                    continue
            raise RuntimeError("could not encode the screenshot")
        return encode

    def report_context(self):
        s = self.session
        sent = getattr(s, "sent_all", None) or getattr(s, "sent_log", [])          # every command since the game began (a replay needs them all)
        t0 = sent[0][0] if sent else 0
        return {"seed": getattr(s, "seed", None), "format": self.game_format(), "state": self.state, "perf": "Frame times: " + self.perf.line(),
                "log_lines": [f"[{e.get('type')}] {e.get('text')}" for e in getattr(s, "log", [])],
                "commands": [(t - t0, c) for t, c in sent],
                "screenshot": self.screenshot_encoder(self.capture_frame()),
                "extra_files": reporting.java_crash_files(self.java_crash_report())}      # patch 46

    def java_crash_report(self):
        """Patch 46: Java's own crash report for this game's engine, if Java crashed (None otherwise, and for a session that
        has no engine of its own - an online guest, a test's stand-in)."""
        try:
            path = self.session.crash_report() if hasattr(self.session, "crash_report") else None
        except Exception:
            return None
        return path if isinstance(path, str) else None

    def remember_reporter(self, name):
        if name and name != self.reporter_name:
            self.reporter_name = name
            self.save_settings()

    def open_report(self, tab=None):
        """The bug report form (F8 or the cog). The picture is taken now, before the form covers the table. Round FR1: it has a
        second tab, Suggest a feature; F8 opens on it when no game is on the screen (the deck screen, the title), where ideas
        come up between games. The cog's "Bug or idea" opens on the bug tab."""
        if tab is None:
            tab = fset.ReportDialog.IDEA if (self.menu is not None or self.boot is not None) else fset.ReportDialog.BUG
        self.overlay = fset.ReportDialog(self.report_context(), name=self.reporter_name, on_name=self.remember_reporter,
                                         folder=self.report_folder, tab=tab)

    def open_menu(self):
        """Show the deck screen (the game, if any, keeps waiting behind it)."""
        if self.launcher is None:
            self.say("This copy was started without the deck screen.", DIM)
            return
        library, samples, base = self.deck_dirs
        entries = lib.list_decks(library, samples, base)
        self.modal = None
        self.menu = fmenu.DeckMenu(entries, self.deck_choice, has_game=self.game_showing(), runtime=self.launcher.runtime,
                                   dirs=self.deck_dirs)
        self.menu.crash_offer = last_session.banner_text(self.last_run)     # round 28d: "" when the last run closed properly
        try:                                                # Round BAN1: the banned list, a refresh if stale, card data - all in the background
            legality.start([(e.load().commanders, e.deck) for e in entries if not e.error],
                           store=getattr(self.art, "store", None))
        except Exception as e:
            crashlog.note(f"Legality check not started: {type(e).__name__}: {e}")
        info = self.unfinished_game() if not self.game_in_progress() else None
        if info:
            start, cmds = info
            self.menu.resume_info = {"actions": len(cmds), "when": time.strftime("%d %b %H:%M", time.localtime(start.get("at") or 0))}
            if not gjournal.replay_matches(start, self.replay_key()):
                self.menu.resume_info["stale"] = True       # round 28d: another Forge or bridge - replaying it would go wrong

    def open_boot(self, splash=True):
        """Round AD2: the studio splash, then the title with the main menu. main() starts here."""
        self.title_ok = True
        self.menu = None
        self.boot = fboot.BootFlow(splash)

    def open_title(self):
        self.menu = None
        self.boot = fboot.BootFlow(False)

    def can_continue(self):
        """The main menu's Continue: an unfinished game that this copy can replay."""
        if self.launcher is None:
            return False
        info = self.unfinished_game()
        return bool(info and gjournal.replay_matches(info[0], self.replay_key()))

    def continue_note(self):
        info = self.unfinished_game() if self.launcher is not None else None
        if info and not gjournal.replay_matches(info[0], self.replay_key()):
            return "The saved game is from another version and can't be resumed (the deck screen can discard it)."
        return ""

    def start_game(self, mine, opp, count, choice=None, fmt=None):
        """Called by the deck screen's Start button: end the current game (if any) and start a new one with these decks.
        `opp` is one deck for every AI, or (round 27b) a list with one deck per AI seat. fmt (round FMT1): the deck screen's
        format; None = my deck's own.
        Returns None when it worked, or a message for the deck screen to show."""
        fmt = formats.normal(fmt or getattr(mine, "format", None))
        seats = list(opp) if isinstance(opp, (list, tuple)) else [opp] * count
        seats = (seats + seats[-1:] * count)[:count] if seats else []
        checks = [("your deck", mine)] + [((f"AI {i + 1}'s deck" if count > 1 else "the opponent's deck"), e) for i, e in enumerate(seats)]
        for who, e in checks:
            if e is None:
                return f"No deck is chosen for {who}."
        for e in {id(e): e for _w, e in checks}.values():
            e.load()
        for who, e in checks:
            if not e.ok:
                return f"Could not read {who}: {e.error or 'no commander found'}"
        for who, e in checks:                                   # round FMT1: one format at the table
            if formats.normal(getattr(e, "format", None)) != fmt:
                return (f"{who[0].upper() + who[1:]} is a {formats.name(getattr(e, 'format', None))} deck, and this is a "
                        f"{formats.name(fmt)} game.")
        self.stats_decks = (deck_meta(mine), [deck_meta(e) for e in seats])        # patch 44
        problem = self.begin((mine.commanders, mine.deck), [(e.commanders, e.deck) for e in seats],
                             labels=(getattr(mine, "name", ""), [getattr(e, "name", "") for e in seats]),
                             printings=[getattr(e, "printings", None) or {} for e in [mine] + seats], fmt=fmt)
        if problem:
            return problem
        self.menu = None
        self.deck_choice = dict(choice or {})
        self.save_settings()
        return None

    def begin(self, my_deck, opp_decks, labels=None, printings=None, fmt=None):
        """End the current game (if any) and start a new one with these decks ((commanders, cards) each).
        labels (Round AD2b): (my deck's name, [each AI deck's name]) for the VS screen; Restart passes the last game's.
        printings (Round ALT1): [my deck's, each AI deck's] {card name lower: (set, cn)}; Restart passes the last game's.
        fmt (Round FMT1): "commander" (None) or "brawl"; Restart passes the last game's.
        Returns None when it worked, or a message saying why not."""
        old = self.session
        fmt = formats.normal(fmt)
        try:
            old.close()                                       # first: the old engine still holds forge_engine.log
            if fmt == formats.DEFAULT:
                session = self.launcher.start(my_deck, list(opp_decks))
            else:
                session = self.launcher.start(my_deck, list(opp_decks), fmt=fmt)
        except Exception as e:                                # Java missing, runtime broken, ...
            self.session = fc.ForgeSession("", [])
            self.current_decks = None
            if self.menu:
                self.menu.has_game = False
            return f"Could not start the game: {e}"
        self.session = session
        session.stats_info = self.local_stats_info(labels, fmt)                   # patch 44 (the journal keeps it for Resume)
        self._hook_session()
        self.set_seat_printings(printings if printings is not None else [])
        self.current_printings = [dict(m or {}) for m in (printings or [])]
        self.start_journal()
        names = set(my_deck[0]) | set(my_deck[1])
        self.deck_names = set(names)
        if self.art:
            for o in opp_decks:
                names |= set(o[0]) | set(o[1])
            self.art.add_names(names | self.printing_keys())
        self.current_decks = (my_deck, list(opp_decks))
        self.current_format = fmt
        self.current_deck_labels = labels or self.current_deck_labels
        self.reset_game()
        self.start_background()
        self.vs = flow.VsShow(self.vs_seats(my_deck, opp_decks, self.current_deck_labels))
        self.play_cue("vs.reveal")                     # round AU1
        return None

    def vs_seats(self, my_deck, opp_decks, labels=None):
        """[(label, commanders, deck name)] for the VS screen: me first, then each AI in seat order."""
        mine_name, opp_names = labels if labels else ("", [])
        opp_names = list(opp_names or [])
        seats = [("You", list(my_deck[0] or []), mine_name or "")]
        for i, o in enumerate(opp_decks):
            seats.append((f"AI {i + 1}" if len(opp_decks) > 1 else "AI", list(o[0] or []), opp_names[i] if i < len(opp_names) else ""))
        return seats

    def resume_last_game(self):
        """Deck screen > Resume last game: start the same game again (same seed and decks) and play the saved commands back into it."""
        info = self.unfinished_game()
        if not info or self.launcher is None:
            self.say("There is no unfinished game to resume.", DIM)
            return
        start, cmds = info
        if not gjournal.replay_matches(start, self.replay_key()):
            self.say("That saved game is from another version of the program and can't be resumed.", RED, 6.0)
            return
        decks = start.get("decks") or {}
        if "player.dck" not in decks:
            self.say("The saved game has no deck files, so it cannot be resumed.", RED, 6.0)
            return
        os.makedirs(DECK_DIR, exist_ok=True)
        paths = {}
        for name, text in decks.items():
            paths[name] = os.path.join(DECK_DIR, "resume_" + os.path.basename(name))
            with open(paths[name], "w", encoding="utf-8") as f:
                f.write(text)
        opps = [paths[n] for n in sorted((n for n in paths if re.fullmatch(r"opponent\d+\.dck", n)), key=lambda n: int(re.findall(r"\d+", n)[0]))]
        try:
            self.session.close()
            session = fc.ForgeSession(paths["player.dck"], opps, name=start.get("name") or "Karl", seed=start.get("seed"),
                                      runtime=self.launcher.runtime)
            session.start()
        except Exception as e:
            self.say(f"Could not start the engine to resume: {e}", RED, 8.0)
            return
        self.session = session
        saved = start.get("stats") if isinstance(start.get("stats"), dict) else None          # patch 44: the same game
        session.stats_info = dict(saved, resumed=True) if saved else dict(self.local_stats_info(None, None), resumed=True)
        self.current_format = getattr(session, "fmt", formats.DEFAULT)       # round FMT1: from the saved player.dck
        self._hook_session(journal=False)                 # the commands being played back are already in the journal
        self.set_seat_printings(start.get("printings") or [])    # round ALT1: the printings the game was started with
        self.current_printings = [dict(m or {}) for m in (start.get("printings") or [])]
        if self.art and self.seat_printings:
            self.art.add_names(self.printing_keys())
        self.reset_game()
        self.resumed_game = True                          # Round UX1: a resumed game never starts the tour by itself
        self.start_background()
        self.vs = flow.VsShow(self.vs_seats((flow.dck_commanders(decks.get("player.dck")), []),
                                            [(flow.dck_commanders(decks.get(os.path.basename(o)[len("resume_"):])), [])
                                             for o in opps]))
        self.play_cue("vs.reveal")                     # round AU1
        self.menu = None
        self.current_decks = None
        self.resuming = ResumeJob(session, cmds).start()
        self.journal.reopen(len(cmds), start.get("at") or time.time())

    def track_resume(self):
        """While a resume runs: nothing but its progress. When it ends: hand the session back to the table."""
        job = self.resuming
        if job is None or not job.finished:
            return
        if getattr(job, "kind", "resume") == "rewind":   # round UNDO1
            self.finish_rewind(job)
            return
        self.resuming = None
        self.load_rewind_points()                         # round UNDO1: the journal's points come back with the game
        self._hook_session()                              # from now on new commands go into the journal again
        if job.error == "cancelled":
            self.say("Resume cancelled.", DIM)
            self.open_menu()
        elif job.error:
            self.say("The game could not be resumed: " + job.error, RED, 8.0)
        elif job.diverged:
            self.say("The game was rebuilt up to action %d of %d; after that it played out differently, so it may not match what you "
                     "saw. %s" % (job.done_count, job.total, job.diverged[1]), ORANGE, 12.0)
        else:
            self.say(f"Game resumed ({job.total} actions played back).", GREEN, 5.0)

    def draw_resume(self):
        """The screen shown while a game is being rebuilt."""
        scr, L = self.screen, self.L
        job = self.resuming
        big, body, small = self.font("big", True), self.font("body"), self.font("small")
        y = L.H // 2 - big.get_height() * 2
        if self.vs is not None and self.vs.seats:                        # Round AD2b: who is playing whom, above the progress
            area = pygame.Rect(L.margin, L.margin + int(10 * L.fs), L.W - 2 * L.margin, int(L.H * 0.6))
            flow.draw_vs(self, self.vs, area)
            y = max(y, area.bottom + int(12 * L.fs))
        rewinding = getattr(job, "kind", "resume") == "rewind"           # round UNDO1: the same screen for a rewind
        title = clip_text("Rewinding to " + job.label, big, L.W - 80) if rewinding else "Resuming your last game"
        draw_text(scr, title, L.W // 2, y, big, WHITE, "midtop")
        y += big.get_height() + 16
        bar_w, bar_h = min(L.W - 80, int(520 * L.fs)), max(8, int(10 * L.fs))
        track = pygame.Rect(L.W // 2 - bar_w // 2, y, bar_w, bar_h)
        round_rect(scr, track, gfx.PANEL_EDGE, radius=bar_h // 2)
        frac = job.done_count / max(1, job.total)
        if frac > 0:
            round_rect(scr, pygame.Rect(track.x, track.y, max(bar_h, int(bar_w * frac)), bar_h), GOLD, radius=bar_h // 2)
        y += bar_h + 14
        draw_text(scr, f"{'Rebuilding' if rewinding else 'Playing back'} action {job.done_count} of {job.total}", L.W // 2, y, body,
                  TEXT, "midtop")
        y += body.get_height() + 10
        foot = ("The game is played again from the first turn with the same shuffle and your same clicks, stopping at that moment, "
                "so a long game takes a while. Esc cancels - your game stays as it is." if rewinding else
                "The same shuffle and every click are sent again, one at a time, waiting for Forge each time, so a long game "
                "takes a few minutes. Esc cancels.")
        for ln in wrap_text(foot, small, min(L.W - 80, int(640 * L.fs))):
            draw_text(scr, ln, L.W // 2, y, small, DIM, "midtop")
            y += small.get_height()

    # ---- Round MP1: playing a friend online -------------------------------------------------------------------------------
    def open_host(self, entry):
        """The deck screen's Host online: the dialog that asks for the name, password and port."""
        problem = self._online_deck_problem(entry)
        if problem and not self.online_saved():          # Round MP2b: a saved game can be continued whatever deck is chosen
            return problem
        self.modal = onl.HostDialog(self, entry, self.online_defaults(), deck_problem=problem)
        return None

    def open_join(self, entry):
        if self.launcher is None:
            return "This copy was started without the deck screen."
        # Round MP2: watching needs no deck, so a deck problem no longer keeps the dialog shut - Join says it instead
        self.modal = onl.JoinDialog(self, entry, self.online_defaults(), deck_problem=self._online_deck_problem(entry))
        return None

    def online_defaults(self):
        prefs = dict(self.online_prefs)
        prefs.setdefault("name", self.reporter_name or "")
        return prefs

    def _online_deck_problem(self, entry):
        if self.launcher is None:
            return "This copy was started without the deck screen."
        if entry is None:
            return "Choose your deck first."
        entry.load()
        if not entry.ok:
            return f"Could not read your deck: {entry.error or 'no commander found'}"
        if not formats.get(getattr(entry, "format", None)).online:          # round FMT1: online games are Commander only
            return f"Online games are Commander only for now, and {entry.name} is a {formats.name(entry.format)} deck."
        for text, blocking in entry.problems(self.launcher.runtime):
            if blocking:
                return f"Your deck ({entry.name}): {text}"
        return None

    def _remember_online(self, **prefs):
        self.online_prefs.update({k: v for k, v in prefs.items() if k in ONLINE_PREF_KEYS})
        self.save_settings()

    def host_game(self, entry, name, port, password, upnp, guests=1, ai=0, relay=None, resume=None):
        """HostDialog's Host: start NetHost with my deck and wait for the friend (HostWait). None when it started, else a message.
        Round MP2c: `guests` friends (1-3) and `ai` AI players, whose decks are the deck screen's AI 1, AI 2 choices.
        Round MP2b: resume = (folder, meta) of a saved online game (online_save.latest) continues it instead."""
        if resume:
            return self._host_saved_game(resume, port, password, upnp, relay)
        problem = self._online_deck_problem(entry)
        if problem:
            return problem
        ai_entries = []
        if ai:
            menu = self.menu if hasattr(self.menu, "resolve_seats") else None
            seats, why = menu.resolve_seats() if menu is not None else ([], None)
            if why:
                return why
            ai_entries = [(seats[i] if i < len(seats or []) and seats[i] is not None else entry) for i in range(ai)]
            for i, e in enumerate(ai_entries):
                e.load()
                if not e.ok:
                    return f"AI {i + 1}'s deck can't be read: {e.error or 'no commander found'}"
        runtime = self.launcher.runtime
        java = fc.find_java()
        try:
            cert = forge_net.ensure_host_cert(paths.user_dir(), java)
        except forge_net.CertError as e:
            return str(e)
        os.makedirs(DECK_DIR, exist_ok=True)
        my_path = fc.write_deck_file(os.path.join(DECK_DIR, "player.dck"), entry.commanders, entry.deck, entry.name or "Player",
                                     runtime)
        ai_paths = [fc.write_deck_file(os.path.join(DECK_DIR, "online_ai_%d.dck" % (i + 1)), e.commanders, e.deck,
                                       e.name or "AI", runtime) for i, e in enumerate(ai_entries)]
        try:
            folder = online_save.new_folder(SAVES_DIR)               # Round MP2b: NetHost's journal of this game goes here
        except OSError:
            folder = None
        session = fc.HostSession(my_path, name, port, password, cert, os.path.join(DECK_DIR, "guest.dck"), upnp=upnp,
                                 code=version.code_fingerprint(), runtime=runtime,
                                 **({"guests": guests, "ai_paths": ai_paths} if (guests, ai) != (1, 0) else {}),
                                 **({"relay": relay} if relay else {}),
                                 **({"journal_path": online_save.journal_path(folder)} if folder else {}))
        try:
            self.session.close()
            session.start()
        except Exception as e:                                   # Java missing, runtime broken, ...
            self.session = fc.ForgeSession("", [])
            return f"Could not start hosting: {e}"
        self.session = session
        self._hook_session(journal=False)
        self.reset_game()
        self.start_background()
        self.menu = None
        self.vs = None
        self.current_decks = None
        self.current_deck_labels = (entry.name, [])
        self.deck_names = set(entry.commanders) | set(entry.deck)
        if self.art:
            self.art.add_names(self.deck_names)
        session.stats_info = {"id": online_stats_id(folder), "format": formats.DEFAULT, "online": "host",      # patch 44
                              "deck": deck_meta(entry)}
        self.hosting_wait = onl.HostWait(session, password, port, upnp)
        self.online_save_folder, self.online_save_resumed, self.online_save_turn = folder, False, None
        self._remember_online(name=name, port=port, upnp=bool(upnp), guests=guests, ai=ai, use_relay=bool(relay),
                              **({"relay": "%s:%d" % tuple(relay)} if relay else {}))
        self.set_title()
        return None

    # ---- Round MP2b: saved online games ----------------------------------------------------------------------------------
    def online_saved(self):
        """(folder, meta, playable) of the newest unfinished hosted game, or None. playable is False when this copy's Forge or
        bridge differs from the one that played it (a play-back would not give the same game)."""
        try:
            found = online_save.latest(SAVES_DIR)
        except OSError:
            found = None
        if not found:
            return None
        folder, meta = found
        return folder, meta, gjournal.replay_matches(meta, self.replay_key())

    def discard_online_save(self):
        found = self.online_saved()
        if found:
            online_save.finish(found[0], "discarded")

    def _host_saved_game(self, resume, port, password, upnp, relay):
        folder, meta = resume
        if not gjournal.replay_matches(meta, self.replay_key()):
            return "This saved game was played with a different version of Forge or the bridge, so it can't be continued."
        runtime = self.launcher.runtime
        java = fc.find_java()
        try:
            cert = forge_net.ensure_host_cert(paths.user_dir(), java)
        except forge_net.CertError as e:
            return str(e)
        replay = online_save.prepare_resume(folder)
        guests = [(int(g["seat"]), g["name"], os.path.join(folder, g["deck"])) for g in meta.get("guests") or []]
        ai_paths = [os.path.join(folder, a) for a in meta.get("ai") or []]
        session = fc.HostSession(os.path.join(folder, meta.get("mine") or "host.dck"), meta.get("host") or "Host", port, password,
                                 cert, os.path.join(DECK_DIR, "guest.dck"), upnp=upnp, code=version.code_fingerprint(),
                                 runtime=runtime, seed=meta.get("seed"), ai_paths=ai_paths,
                                 journal_path=online_save.journal_path(folder), resume={"replay": replay, "guests": guests},
                                 **({"relay": relay} if relay else {}))
        try:
            self.session.close()
            session.start()
        except Exception as e:
            self.session = fc.ForgeSession("", [])
            return f"Could not start hosting: {e}"
        self.session = session
        self._hook_session(journal=False)
        self.reset_game()
        self.start_background()
        self.menu = None
        self.vs = None
        self.current_decks = None
        self.current_deck_labels = ((meta.get("labels") or {}).get("mine") or "", [])
        self.deck_names = set()
        session.stats_info = {"id": online_stats_id(folder), "format": formats.DEFAULT, "online": "host", "resumed": True,
                              "deck": dict(meta.get("stats_deck") or {"id": None, "name": self.current_deck_labels[0]})}
        self.hosting_wait = onl.HostWait(session, password, port, upnp)
        self.online_save_folder, self.online_save_resumed, self.online_save_turn = folder, True, meta.get("turn")
        self.set_title()
        return None

    def begin_online_save(self):
        """A hosted game has started: what it takes to continue it later (NetHost writes the commands themselves)."""
        s, folder = self.session, self.online_save_folder
        if not folder or self.online_save_resumed or getattr(s, "online", None) != "host":
            return
        decks, guests, ai = {"host.dck": s.deck_path}, [], []
        for g in getattr(s, "seat_list", []) or []:
            name = "guest_%d.dck" % int(g.get("seat") or 0)
            decks[name] = g.get("deck")
            guests.append({"seat": g.get("seat"), "name": g.get("name"), "deck": name})
        for i, path in enumerate(getattr(s, "opponent_paths", []) or [], 1):
            decks["ai_%d.dck" % i] = path
            ai.append("ai_%d.dck" % i)
        meta = {"seed": s.seed, "host": s.name, "players": 1 + len(guests) + len(ai), "guests": guests, "ai": ai,
                "mine": "host.dck", "replay": self.replay_key(), "code": version.code_fingerprint(),
                "labels": {"mine": (self.current_deck_labels or ("", []))[0] or ""},
                "stats_deck": (getattr(s, "stats_info", None) or {}).get("deck")}           # patch 44: the deck the stats count
        try:
            online_save.start(folder, meta, {k: v for k, v in decks.items() if v and os.path.isfile(v)})
            online_save.give_up_others(SAVES_DIR, folder)
        except OSError as e:
            print(f"[forge_table] Could not save the online game: {e}")

    def note_online_save(self):
        """Every tick: the turn reached, for the Host dialog's "turn 12, with Sam"."""
        folder = self.online_save_folder
        turn = (self.state or {}).get("turn")
        if folder and turn and turn != self.online_save_turn and getattr(self.session, "online", None) == "host":
            self.online_save_turn = turn
            online_save.note(folder, turn=turn, saved=time.time())

    def end_online_save(self, result):
        folder = self.online_save_folder
        if folder and getattr(self.session, "online", None) == "host":
            online_save.finish(folder, result)
            self.online_save_folder = None

    def cancel_hosting(self):
        """HostWait's Cancel / Back: stop waiting, close the engine, back to the deck screen."""
        s = self.session
        if not self.online_save_resumed:
            online_save.forget_unstarted(self.online_save_folder)        # Round MP2b: it never started: nothing to keep
        self.online_save_folder = None
        if getattr(s, "online", None) == "host" and s.alive() and not s.ready:      # Round MP2c: guests may be waiting already
            s.cancel_host()
        try:
            s.close()
        except Exception:
            pass
        self.session = fc.ForgeSession("", [])
        self.hosting_wait = None
        self.set_title()
        self.open_menu()

    def make_guest_session(self, entry, name, address, port, password, fingerprint, by_hand=False, watch=False, room=None):
        """JoinDialog's Join: a NetSession with my deck, not started yet (a JoinJob starts it). (session, None) or (None, why).
        Round MP2: watch=True (JoinDialog's Watch) needs no deck."""
        if watch:
            session = fc.NetSession(address, port, name, password, "", "", fingerprint=fingerprint,
                                    code=version.code_fingerprint(), watch=True, room=room)
            session.deck_entry = None
            prefs = {"name": name}
            if by_hand:
                prefs.update(address=address, port=port)
            self._remember_online(**prefs)
            return session, None
        problem = self._online_deck_problem(entry)
        if problem:
            return None, problem
        os.makedirs(DECK_DIR, exist_ok=True)
        path = fc.write_deck_file(os.path.join(DECK_DIR, "online_mine.dck"), entry.commanders, entry.deck,
                                  entry.name or "Deck", self.launcher.runtime)
        try:
            with open(path, "r", encoding="utf-8") as f:
                dck = f.read()
        except OSError as e:
            return None, f"Could not prepare your deck: {e}"
        if len(dck.encode("utf-8")) > forge_net.DECK_MAX_BYTES:
            return None, "Your deck is too large to send."
        session = fc.NetSession(address, port, name, password, entry.name or "Deck", dck, fingerprint=fingerprint,
                                code=version.code_fingerprint(), room=room)
        session.deck_entry = entry
        prefs = {"name": name}
        if by_hand:                                    # an invite code's address and port are the host's, not something to keep
            prefs.update(address=address, port=port)
        self._remember_online(**prefs)
        return session, None

    def begin_online(self, session):
        """The friend's game accepted me (JoinJob finished): show its table."""
        old = self.session
        try:
            old.close()
        except Exception:
            pass
        self.session = session
        self._hook_session(journal=False)
        self.reset_game()
        self.start_background()
        self.menu = None
        self.current_decks = None
        entry = getattr(session, "deck_entry", None)
        if not getattr(session, "spectator", False):           # patch 44: a separate record for online games
            session.stats_info = {"id": gstats.new_id(), "format": formats.DEFAULT, "online": "guest", "deck": deck_meta(entry)}
        mine = (list(entry.commanders), list(entry.deck)) if entry else ([], [])
        self.deck_names = set(mine[0]) | set(mine[1])
        if self.art:
            self.art.add_names(self.deck_names)
        self.current_deck_labels = (getattr(entry, "name", ""), [])
        self.vs = flow.VsShow(self.online_vs_seats(mine[0]))
        self._note_known_host(session)
        self.check_peer_version()
        self.set_title()
        if getattr(session, "spectator", False):                 # Round MP2
            self.say("You're watching %s vs %s. Only the players can act; hands and libraries stay hidden." % (
                session.peer_name or "the host", session.watched_guest or "their friend"), DIM, 8.0)

    def online_vs_from_state(self):
        """Round MP2c: every seat of an online game from the snapshot - me first ("You"; a spectator has no "me"), then the others
        in Forge's order, each with its commanders. Seats whose commanders aren't known keep a "?"."""
        s, st = self.session, self.state or {}
        players = st.get("players") or []
        if not players:
            return None
        spectating = getattr(s, "spectator", False)
        me = s.me()
        mine = [me] if me is not None else []
        order = mine + [p for p in players if me is None or p.get("id") != me.get("id")]
        seats = []
        for p in order:
            names = [c["name"] for z in p.get("zones", {}).values() for c in z
                     if c.get("id") in (p.get("commanders") or []) and c.get("name")]
            if p is me and not spectating:
                seats.append(("You", names or ["?"], (self.current_deck_labels or ("", []))[0] or ""))
            else:
                seats.append((p.get("name") or "?", names or ["?"], ""))
        return seats

    def online_vs_seats(self, my_commanders):
        if getattr(self.session, "spectator", False):           # Round MP2: both players' commanders come from the first snapshot
            return [(self.session.peer_name or "Host", ["?"], ""),
                    (getattr(self.session, "watched_guest", None) or "Guest", ["?"], "")]
        mine_name = (self.current_deck_labels or ("", []))[0] or ""
        return [("You", list(my_commanders or []), mine_name), (self.session.peer_name or "Your friend", ["?"], "")]

    def _note_known_host(self, session):
        """Remember the host PC's fingerprint for this address, so typing the address by hand next time is checked too."""
        if getattr(session, "room", None):
            return                                     # Round MP2d: that address is the relay's, not the host PC's
        try:
            der = session.proc.sock.getpeercert(binary_form=True)
        except Exception:
            return
        if not der:
            return
        known = dict(self.online_prefs.get("known_hosts") or {})
        known["%s:%d" % (session.address, session.port)] = forge_net.fingerprint(der)
        while len(known) > KNOWN_HOSTS_MAX:
            known.pop(next(iter(known)))
        self._remember_online(known_hosts=known)

    def check_peer_version(self):
        code = getattr(self.session, "peer_code", None)
        if code and code != version.code_fingerprint():
            self.say("Your friend runs a different version (%s). If something looks wrong, update both." % code, ORANGE, 8.0)

    def set_title(self):
        s = self.session
        extra = ""
        if getattr(s, "spectator", False):                      # Round MP2
            extra = "  ·  Watching %s vs %s" % (s.peer_name or "the host", getattr(s, "watched_guest", None) or "their friend")
        elif getattr(s, "online", None) == "host":
            names = list(getattr(s, "guests_joined", []) or []) or ([s.peer_name] if s.peer_name else [])
            extra = "  ·  Online with %s" % " and ".join(names) if names else "  ·  Hosting online"
        elif getattr(s, "online", None) == "guest":
            extra = "  ·  Online, hosted by %s" % (s.peer_name or "a friend")
        watching = len(getattr(s, "spectators", []) or [])
        if watching and not getattr(s, "spectator", False):
            extra += "  ·  %d watching" % watching
        try:
            pygame.display.set_caption(f"Commander - Forge table  (v{version.short()}){extra}")
        except pygame.error:
            pass

    def track_online(self):
        """Every tick: the host's waiting screen ending, the other table leaving, refused commands. True when it opened a dialog."""
        s = self.session
        online = getattr(s, "online", None)
        if not online:
            return False
        if self.hosting_wait is not None:
            if s.hosting_cancelled:
                self.cancel_hosting()
                return False
            resume = getattr(s, "resume", None)
            if resume and getattr(s, "replay_result", None) and not getattr(self, "_replay_noted", False):
                self._replay_noted = True                         # Round MP2b: say if the play-back stopped early
                r = s.replay_result
                if r.get("diverged"):
                    self.say("The saved game could only be restored up to action %d of %d; it goes on from there." % (
                        r.get("applied") or 0, r.get("total") or 0), ORANGE, 12.0)
            ready = (s.ready and not s.resuming and s.everyone_back()) if resume else (s.peer_name and s.ready)
            if ready:                                         # the friend arrived: on to the game
                try:
                    self.invite_code = self.hosting_wait.invite()    # Round MP2: the same code lets someone watch (Ctrl+I)
                except Exception:
                    self.invite_code = None
                self.hosting_wait = None
                self.begin_online_save()                          # Round MP2b
                cmds = []
                try:
                    with open(s.deck_path, "r", encoding="utf-8") as f:
                        cmds = flow.dck_commanders(f.read())
                except OSError:
                    pass
                self.vs = flow.VsShow(self.online_vs_seats(cmds))
                who = list(getattr(s, "guests_joined", []) or []) or [s.peer_name]       # Round MP2c: one or several
                who = who[0] if len(who) == 1 else ", ".join(who[:-1]) + " and " + who[-1]
                self.say(f"{who} joined your game." + ("  Someone else can watch with the same invite code "
                                                               "(Ctrl+I copies it)." if self.invite_code else ""), GREEN, 7.0)
                self.check_peer_version()
                self.set_title()
            return False
        if self.vs is not None and self.state:                   # the others' commanders, once the first snapshot shows them
            seats = self.vs.seats
            players = self.state.get("players") or []
            if len(seats) != len(players) or any(not cmd or cmd == ["?"] for _lab, cmd, _deck in seats):
                new = self.online_vs_from_state()
                if new:
                    self.vs.seats[:] = new
        while self.refused_seen < len(s.refused_cmds):
            self.refused_seen += 1
            self.say("The host's game does not allow that from a guest.", ORANGE, 4.0)
        self.track_mp2(s)
        self.note_online_save()                               # Round MP2b
        gone = s.peer_left is not None or (online == "guest" and s.exited)
        if gone and not self.shown_peer_left:
            self.shown_peer_left = True
            spectating = getattr(s, "spectator", False)
            who = "guest" if online == "host" else "host"
            left = s.peer_left or {}
            if left.get("who") == "guest" and spectating:
                who = "player"
            if s.game_over or (self.state or {}).get("gameOver"):
                self.say(("Your friend" if who == "guest" else "The host" if who == "host" else "A player")
                         + " has left the table.", DIM, 6.0)
                return False
            self.end_screen = None
            self.modal = onl.connection_lost_dialog(who, self.leave_online_game, left, getattr(s, "net_lost", None),
                                                     spectating)
            self.modal.closes_game = False
            return True
        return False

    def track_mp2(self, s):
        """Round MP2: a connection dropping and coming back, and people starting or stopping to watch - a toast for each, and the
        countdown the banner shows."""
        seen = self.mp2_seen
        now = time.monotonic()
        drop = getattr(s, "peer_dropped", None)
        if getattr(s, "net_state", None) == "reconnecting":
            if self.drop_noticed is None:
                self.drop_noticed = (now, getattr(s, "grace", 0) or 0)
        elif drop:
            if self.drop_noticed is None:
                self.drop_noticed = (now, drop.get("grace") or 0)
        else:
            self.drop_noticed = None
        if getattr(s, "peer_backs", 0) > seen["peer_backs"]:
            seen["peer_backs"] = s.peer_backs
            self.say("%s is back." % (getattr(s, "peer_back_name", None) or s.peer_name or "Your friend"), GREEN, 4.0)
        left = getattr(s, "players_left", []) or []
        while seen.setdefault("players_left", 0) < len(left):        # Round MP2c: one of three or four left; the rest play on
            m = left[seen["players_left"]]
            seen["players_left"] += 1
            why = ("didn't come back in time" if m.get("expired") else "left" if m.get("left") else "lost their connection")
            self.say("%s %s and is out of the game." % (m.get("name") or "A player", why), ORANGE, 6.0)
        if getattr(s, "reconnects", 0) > seen["reconnects"]:
            seen["reconnects"] = s.reconnects
            self.say("Reconnected to %s's game." % (s.peer_name or "the host"), GREEN, 4.0)
        specs = list(getattr(s, "spectators", []) or [])
        if specs != seen["specs"]:
            for name in specs:
                if name not in seen["specs"]:
                    self.say("%s is watching." % name, DIM, 4.0)
            for name in seen["specs"]:
                if name not in specs:
                    self.say("%s stopped watching." % name, DIM, 4.0)
            seen["specs"] = specs
            self.set_title()

    def leave_online_game(self):
        try:
            self.session.close()
        except Exception:
            pass
        self.session = fc.ForgeSession("", [])
        self.hosting_wait = None
        self.invite_code = None
        self.set_title()
        self.open_menu()

    def game_in_progress(self):
        return self.game_showing() and not (self.session.game_over or (self.state or {}).get("gameOver"))

    def can_restart(self):
        return bool(self.launcher and self.current_decks) and not getattr(self.session, "online", None)

    def ask_new_game(self):
        """Cog > New game...: play the same decks again (fresh shuffle) or pick decks. This window is the question, so it is also the
        warning when a game is running. Without a launcher or a game to repeat there is only one answer, so it goes straight to the decks."""
        if self.launcher is None:
            self.say("This copy was started without the deck screen.", DIM)
            return
        if not self.can_restart():
            self.open_menu()
            return
        running = self.game_in_progress()
        self.modal = dlg.OptionsDialog(
            "New game", ("The game in progress ends. " if running else "") + "Play the same decks again with a fresh shuffle, or choose decks first.",
            [("Same decks, new shuffle", self.restart_game, "normal"), ("Choose decks...   (Ctrl+N)", self.open_menu, "normal")],
            "Keep playing" if running else "Cancel")

    def restart_game(self):
        problem = self.begin(*self.current_decks, labels=self.current_deck_labels,
                             printings=getattr(self, "current_printings", None), fmt=getattr(self, "current_format", None))
        if problem:
            self.say(problem, RED, 8.0)
            self.open_menu()

    def ask_concede(self):
        """Cog > Concede...: give the game up (Forge counts it as a loss), then go to the main menu, stay on the table, or close the
        program. Always asks first."""
        if not self.game_in_progress():
            self.say("There is no game in progress to concede.", DIM)
            return
        self.modal = dlg.OptionsDialog(
            "Concede this game?", "You lose the game. Afterwards you can start another (cog > New game).",
            [("Concede and return to the main menu", self.concede_to_title, "danger"),          # round 32 (Karl, 3 Oct)
             ("Concede and stay on the table", self.concede, "danger"),
             ("Concede and close the program", self.concede_and_close, "danger")], "Keep playing")

    def concede(self):
        self.session.concede()
        self.end_journal("conceded")
        if self.stats_rec is not None and self._stats_session is self.session:
            self.stats_rec.conceded = True          # patch 44: not counted in the record (Karl's decision 7)

    def concede_to_title(self):
        """Round 32 (Karl, 3 Oct 2026: "Concede and return to main menu should be an option"): concede, end this game's engine (as
        concede-and-close does, and as leaving an online game does), and show the title's main menu - Play, Continue, Settings,
        Credits, Quit. The game is conceded in its journal first, so Continue doesn't offer it."""
        self.concede()
        try:
            self.session.close()
        except Exception:
            pass
        self.session = fc.ForgeSession("", [])
        self.modal = self.overlay = self.end_screen = None
        self.hosting_wait = None
        self.invite_code = None
        self.set_title()
        self.title_ok = True                                 # the deck screen's Esc comes back here, as when the program starts here
        self.open_title()
        self.boot.to_menu()

    def concede_and_close(self):
        self.concede()
        self.running = False                       # run() ends its loop, and shutdown() closes the engine

    def handle_event(self, ev):
        """Process one pygame event. Returns False when the window should close."""
        if ev.type == pygame.QUIT:
            return False
        if ev.type in (pygame.KEYDOWN, pygame.KEYUP, pygame.MOUSEBUTTONDOWN, pygame.MOUSEWHEEL):
            self.note_modifier(ev)                     # patch 43: Ctrl / Ctrl+Shift taps
        if ev.type == pygame.MOUSEMOTION:
            self.mouse = ev.pos
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            self.mouse = ev.pos
            if ev.button in (1, 3):
                self.on_click(ev.pos, ev.button)
        elif ev.type == pygame.MOUSEWHEEL:
            if self.overlay:
                self.overlay.wheel(self, ev.y)
            elif self.tour_visible():
                pass                                    # Round UX1: the log does not scroll under the tour
            elif self.modal:
                self.modal.wheel(self, ev.y)
            elif self.menu:
                self.menu.wheel(self, ev.y)
            elif self._log_open and self.L and self.L.log_overlay.collidepoint(self.mouse):
                self.log_scroll += ev.y * 3                                        # patch UI6: the corner log is over the stack
            elif self.L and self.state and self.state.get("stack") and self.L.stack.collidepoint(self.mouse):
                self.stack_scroll = max(0, self.stack_scroll - ev.y * 40)          # draw_stack clamps it
            elif self.log_rect.collidepoint(self.mouse):
                self.log_scroll += ev.y * 3
        elif ev.type == pygame.KEYDOWN:
            self.on_key(ev)
        elif ev.type == getattr(pygame, "DROPFILE", -1):              # a deck file dragged onto the window
            target = self.modal if hasattr(self.modal, "drop_file") else (self.menu if not self.modal else None)
            if target is not None:
                target.drop_file(self, ev.file)
        elif ev.type == getattr(pygame, "WINDOWDISPLAYCHANGED", -1):          # round 27e: moved to another screen
            self.check_display(force=True)
        elif ev.type == getattr(pygame, "WINDOWLEAVE", -1):                    # patch UI6: the mouse left the window - nothing is hovered
            self.mouse = (-1, -1)
        elif ev.type in (getattr(pygame, "WINDOWRESIZED", -1), pygame.VIDEORESIZE):
            self._force_draw = True
            if not self.check_display(force=True) and not self.fullscreen:
                self.windowed_size = self.screen.get_size()
        return True

    def on_click(self, pos, button):
        if self.resuming is not None:                        # nothing to click while the game is being rebuilt
            return
        if self.overlay:
            self.overlay.click(self, pos, button)
            return
        if self.modal:
            self.modal.click(self, pos, button)
            return
        if self.tour_visible():                              # Round UX1: every click is the tour's while it shows
            if button == 1:
                self.tour.click(pos)
                self._after_tour_input()
            return
        if self.boot is not None and not self.menu:
            self.boot.click(self, pos, button)
            return
        if self.menu:
            self.menu.click(self, pos, button)
            return
        if self.hosting_wait is not None:
            self.hosting_wait.click(self, pos, button)
            return
        if self.vs is not None and self.vs.holding(self):        # Round AD2b: a click skips the VS screen, nothing more
            self.vs.skipped = True
            return
        if not self.state:
            return
        kind, data = self.hit_at(pos)
        if kind == "layer":                                     # Round AD2b: a click on a full-screen moment, not on its buttons
            if self.end_screen is not None and self.end_screen.kind != "out" and button == 1:
                self.end_continue()
            return
        s = self.session
        if button == 3:
            if kind == "card":
                cid = data["card"]["id"]
                self.pinned = None if self.pinned == cid else cid
            elif kind in ("pill",):
                self.toggle_stop(data["phase"], "theirs")
            else:
                self.pinned = None
            return
        if kind == "card":
            tgt = self.target_prompt()
            card = data["card"]
            if tgt and tgt["id"] == card["id"]:
                self.confirm_self_target(tgt)                       # the source is glowing like every legal target: ask first
            else:
                if card.get("selectable") or card.get("weak"):
                    self.press_dip(card["id"])          # round 23: visual-only; the click below is unaffected
                    self.play_cue("card.pickup", self.pan_for(card["id"]))     # round 24
                if (pygame.key.get_mods() & pygame.KMOD_CTRL) and card.get("weak") and self.smart_passing() and self.at_priority():
                    s.hold_priority()                   # patch 43: Ctrl held as you cast - keep priority over it
                    self.say(f"Holding priority over {card.get('name', 'it')}.", DIM, 2.5)
                s.click_card(card["id"])
        elif kind == "player":
            s.click_player(data["id"])
        elif kind == "button":
            self.press(data["name"])
        elif kind == "pool":
            self.spend_pool(data.get("sym"))
        elif kind == "pill":
            self.toggle_stop(data["phase"], "mine")
        elif kind == "stop":
            self.toggle_stop(data["phase"], data["who"])
        elif kind == "badge":                                     # a chip sits on the panel: a click there still means "this player"
            player = s.player(data.get("player")) if data.get("player") is not None else None
            if player and self.panel_targetable(player):
                s.click_player(player["id"])
        elif kind in ("zone", "library"):
            player = s.player(data["player"])
            if player and self.panel_targetable(player):        # the piles sit on the panel: a click there still means "this player"
                s.click_player(player["id"])
            elif kind == "zone":
                self.open_zone(data["player"], data["zone"])
            else:
                self.explain_library(data["player"])

    def confirm_self_target(self, tgt):
        cid = tgt["id"]
        self.modal = dlg.QuestionDialog(
            f"Target {tgt['source']} itself?",
            f"{tgt['source']} is the card whose ability you are using. Forge allows it to target itself, but that is rarely what you want. "
            "Choose No to pick another target.",
            "Yes, target it", "No, choose another", lambda: self.session.click_card(cid), enter_yes=False)

    def on_key(self, ev):
        key, mod = ev.key, getattr(ev, "mod", 0)
        if self.resuming is not None:
            if key == pygame.K_ESCAPE:
                self.resuming.cancelled = True
            return
        if key == pygame.K_F11 and not (self.overlay and not isinstance(self.overlay, fset.SettingsPopup)):
            self.overlay = None
            self.toggle_fullscreen()
            return
        if key == pygame.K_F8 and not isinstance(self.overlay, fset.ReportDialog):
            self.open_report()
            return
        if key == pygame.K_F3:
            self.show_perf = not self.show_perf
            return
        if key == pygame.K_F1 and (mod & pygame.KMOD_SHIFT) and not isinstance(self.modal, licenses_view.LicensesDialog):
            self.open_licenses()
            return
        if key == pygame.K_i and (mod & pygame.KMOD_CTRL) and getattr(self.session, "online", None) == "host" \
                and getattr(self, "invite_code", None) and not self.overlay:
            self.write_clipboard(self.invite_code)       # Round MP2: give it to someone who wants to watch
            self.say("Invite code copied: someone who pastes it into Join online and presses Watch can watch this game.", DIM, 6.0)
            return
        if self.overlay:
            self.overlay.key(self, ev)
            return
        if self.modal:
            self.modal.key(self, ev)
            return
        if self.tour_visible() and not ftour.passes_through(ev):       # Round UX1: keys are the tour's, except H, M, + and -
            self.tour.key(ev)
            self._after_tour_input()
            return
        if self.boot is not None and not self.menu:
            self.boot.key(self, ev)
            return
        if self.hosting_wait is not None and not self.menu:
            self.hosting_wait.key(self, ev)
            return
        if self.vs is not None and not self.menu and self.vs.holding(self) and \
                key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
            self.vs.skipped = True                              # Round AD2b: the key skips the VS screen and is not passed on
            return
        if self.end_screen is not None and not self.menu and self.flow_hidden is None and \
                key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER) and self.end_screen.kind != "out":
            self.end_continue()
            return
        if self.menu and self.menu.wants_key(ev):              # round 27b: typing into the deck search, "-" included
            self.menu.key(self, ev)
        elif key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            self.change_text_scale(1)
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.change_text_scale(-1)
        elif self.menu:
            self.menu.key(self, ev)
        elif key == pygame.K_n and mod & pygame.KMOD_CTRL:
            self.open_menu()
        elif key == pygame.K_h:
            self.open_help()
        elif key == pygame.K_m:
            self.toggle_sound()
        elif not self.state:
            return
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.smart_passing() and self.at_priority() \
                and self.prompt().get("ok", {}).get("enabled"):
            if mod & pygame.KMOD_SHIFT:                 # patch 43, Arena's keys: Shift+Enter passes the turn,
                self.pass_the_turn()
            else:                                       # Enter passes until an opponent acts or the turn ends
                self.pass_until_something()
        elif key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.prompt().get("ok", {}).get("enabled"):
                self.session.ok()
        elif key == pygame.K_ESCAPE:
            self.pinned = None
            if self.prompt().get("cancel", {}).get("enabled") and self.cancel_is_safe():
                self.session.cancel()
        elif key == pygame.K_e:
            if self.prompt().get("cancel", {}).get("enabled") and self.cancel_label().startswith("end turn"):
                self.session.cancel()
        elif key == pygame.K_a:
            if self.prompt().get("cancel", {}).get("enabled") and self.cancel_label().startswith("alpha"):
                self.session.cancel()
        elif key == pygame.K_u or (key == pygame.K_z and mod & pygame.KMOD_CTRL):
            self.press_undo()
        elif key == pygame.K_s and not mod & pygame.KMOD_CTRL:
            self.open_skip()
        elif key == pygame.K_y and not mod & pygame.KMOD_CTRL and self.at_priority():
            self.always_pass_top()                      # patch 43 (Forge's own key for "always yes / auto-yield" is Y too)

    # ---- keeping in step with the engine --------------------------------------------------------

    def undo_signature(self):
        """What Undo could change: my permanents (and whether each is tapped), my floating mana, the stack and my hand size."""
        st = self.state
        me = self.session.me() if st else None
        if not me:
            return None
        z = me["zones"]
        return (tuple((c["id"], bool(c.get("tapped"))) for c in z.get("battlefield", [])),
                tuple(sorted((me.get("manaPool") or {}).items())), len(st.get("stack", [])), len(z.get("hand", [])))

    def press_undo(self):
        """U / Ctrl+Z / the Undo button. Round UNDO1: with mana floating, Forge's own undo first (it takes back that mana tap at
        once); otherwise - or when Forge's undo changes nothing - the Undo window with this turn's moments (open_rewind). Online
        and in a game without a journal there is only Forge's undo, as before."""
        if self.can_rewind() and not self.mana_floating():
            self.open_rewind()
            return
        self.session.undo()
        self.undo_check = (time.time() + UNDO_WAIT, self.undo_signature())

    def mana_floating(self):
        me = self.session.me() if self.state else None
        pool = (me or {}).get("manaPool") or {}
        try:
            return sum(int(v or 0) for v in pool.values()) > 0
        except (TypeError, ValueError, AttributeError):
            return False

    # ---- round UNDO1: rewinding to an earlier moment of my turn (rewind.py) -----------------------------------------------------

    def rewind_unavailable(self):
        """None when Undo can go back to an earlier moment now, else a sentence saying why not."""
        s = self.session
        if getattr(s, "online", None) or getattr(s, "spectator", False):
            return UNDO_ONLINE
        if self.journal is None or not self.journal.active() or self.launcher is None:
            return UNDO_NOTHING
        if not self.state or self.state.get("gameOver") or getattr(s, "game_over", False) or self.resuming is not None:
            return UNDO_NOTHING
        if not grewind.latest_turn_points(self.rewind_points, self.journal.count, self.my_turn_now()):
            return UNDO_NOTHING_YET
        return None

    def my_turn_now(self):
        """The turn number when it is my turn right now, else None (see rewind.latest_turn_points)."""
        st = self.state or {}
        return st.get("turn") if st.get("activePlayer") is not None and st.get("activePlayer") == st.get("me") else None

    def can_rewind(self):
        return self.rewind_unavailable() is None

    def rewind_choices(self):
        me = self.session.me() if self.state else None
        try:
            commands = gjournal.read_full(self.journal.path)["commands"] if self.journal else None
        except (OSError, ValueError):
            commands = None
        cards = getattr(self.session, "_cards", None) or {}

        def card_name(cid):
            return strip_ids((cards.get(cid) or {}).get("name") or "") or None
        return grewind.choices(self.rewind_points, self.journal.count if self.journal else 0, getattr(self.session, "log", []),
                               getattr(self.session, "log_total", 0), (me or {}).get("name"), commands, card_name,
                               self.my_turn_now())

    def load_rewind_points(self):
        """The points of the game in the journal (after a new start: none; after a resume or a rewind: the journal's)."""
        self.rewind_points, self.rewind_expect = [], {}
        if self.journal is not None and self.journal.active():
            try:
                self.rewind_points = gjournal.read_full(self.journal.path)["points"]
            except (OSError, ValueError):
                self.rewind_points = []
        starts = [p.get("turn") for p in self.rewind_points if p.get("start")]
        self.rewind_tracker.reset(turn_started=max(starts) if starts else None)

    def track_points(self):
        """Every new snapshot: is this a moment of my turn Undo should be able to go back to? Then it goes into the journal."""
        s = self.session
        jr = self.journal
        if jr is None or not jr.active() or getattr(s, "online", None) or getattr(s, "spectator", False):
            return
        key = (id(s), s.state_version, len(s.requests))
        if key == self._points_version:
            return
        self._points_version = key
        p = self.rewind_tracker.see(s.state, s.requests, jr.count, getattr(s, "log_total", 0), grewind.board_hash)
        if p is None:
            return
        if any(q.get("i") == p["i"] and q.get("seq") == p["seq"] and q.get("req") == p["req"] for q in self.rewind_points):
            return                                        # a resumed game's current moment is in the journal already
        self.rewind_points.append(p)
        self.rewind_expect[p["i"]] = grewind.board_fingerprint(s.state)
        try:
            jr.point(p)
        except OSError:
            pass

    def track_drops(self):
        """The bridge said it dropped a click (too late for its question, busy, a greyed button): write which one into the journal,
        so a rebuild leaves it out (journal.mark_drop)."""
        s = self.session
        if s is not self._drops_session:
            self._drops_session, self._drops_seen = s, len(getattr(s, "dropped", []) or [])
            return
        dropped = getattr(s, "dropped", None) or []
        if len(dropped) <= self._drops_seen:
            return
        new, self._drops_seen = dropped[self._drops_seen:], len(dropped)
        if self.journal is None or not self.journal.active():
            return
        for m in new:
            if m.get("at") is not None:
                try:
                    self.journal.mark_drop(m.get("c"), m.get("at"))
                except OSError:
                    pass

    def open_rewind(self):
        """The Undo window: this turn's moments, newest first. The rebuild engine starts at once, while you choose (Java takes a
        while to start), and is closed again if you keep playing."""
        why = self.rewind_unavailable()
        options = self.rewind_choices() if why is None else []
        if not options:
            self.say(why or UNDO_NOTHING_YET, ORANGE, 5.0)
            return
        self.close_rewind_prestart()
        try:
            self.rewind_prestart = self.start_rebuild_session()
        except Exception as e:                            # it starts again (and says why) when a moment is picked
            self.rewind_prestart = None
            crashlog.note(f"Undo: the rebuild engine could not start in advance: {e}")
        self.modal = dlg.RewindDialog(options, self.rewind_to, self.close_rewind_prestart)

    def close_rewind_prestart(self):
        pre, self.rewind_prestart = self.rewind_prestart, None
        if pre is not None:
            try:
                pre[0].close()
            except Exception:
                pass

    def start_rebuild_session(self):
        """(a started ForgeSession for the same game - same seed, decks, name and format - and the journal's contents). Its engine
        log is the one the running engine isn't writing (Windows can't share it): forge_engine.log <-> forge_engine.rewind.log."""
        full = gjournal.read_full(self.journal.path)
        start = full.get("start") or {}
        if not gjournal.replay_matches(start, self.replay_key()):
            raise RuntimeError("this game was started by another version of the program")
        decks = start.get("decks") or {}
        if "player.dck" not in decks:
            raise RuntimeError("the game's journal has no deck files")
        os.makedirs(DECK_DIR, exist_ok=True)
        paths = {}
        for name, text in decks.items():
            paths[name] = os.path.join(DECK_DIR, "rewind_" + os.path.basename(name))
            with open(paths[name], "w", encoding="utf-8") as f:
                f.write(text)
        opps = [paths[n] for n in sorted((n for n in paths if re.fullmatch(r"opponent\d+\.dck", n)), key=lambda n: int(re.findall(r"\d+", n)[0]))]
        session = fc.ForgeSession(paths["player.dck"], opps, name=start.get("name") or "Karl", seed=start.get("seed"),
                                  runtime=self.launcher.runtime)
        session.stderr_path = fc.other_engine_log(getattr(self.session, "stderr_path", None))
        session.start()
        return session, full

    def rewind_to(self, point, label):
        """The Undo window's choice: rebuild the game up to `point` in a second engine (RewindJob). The running engine stays as
        it is until the rebuild has reached that moment and its board matches."""
        pre, self.rewind_prestart = self.rewind_prestart, None
        try:
            if pre is None:
                pre = self.start_rebuild_session()
        except Exception as e:
            self.say(f"Could not start the engine to rewind: {e}", RED, 8.0)
            return
        session, full = pre
        cut = int(point["i"])
        self.undo_check = None
        self.resuming = grewind.RewindJob(session, full["commands"][:cut], point, label, drops=full["drops"],
                                          fast_from=full["fast_from"], expected=self.rewind_expect.get(cut)).start()

    def finish_rewind(self, job):
        """A RewindJob ended: on success the rebuilt engine takes over (the journal is cut back to that moment); otherwise it is
        closed and the game goes on exactly as it was."""
        self.resuming = None
        why = None
        if job.error == "cancelled":
            why = "cancelled"
        elif job.error:
            why = job.error
        elif job.diverged:
            why = job.diverged[1]
        elif job.mismatch:
            why = job.mismatch
        elif not job.ok:
            why = "the rebuild did not finish"
        if why is not None:
            try:
                job.session.close()
            except Exception:
                pass
            if why == "cancelled":
                self.say("Rewind cancelled - your game is as it was.", DIM, 5.0)
            else:
                crashlog.note(f"Undo could not rewind to '{job.label}' (command {job.point.get('i')} of {len(job.commands)}): {why}")
                self.say(f"Couldn't rewind exactly ({why}). Your game is as it was.", ORANGE, 12.0)
            return
        old, new = self.session, job.session
        info = getattr(old, "stats_info", None)
        new.stats_info = dict(info, resumed=True) if isinstance(info, dict) else None
        self.session = new
        try:
            old.close()
        except Exception:
            pass
        cut = int(job.point["i"])
        try:
            self.journal.cut(cut, time.time())
        except OSError as e:
            # The journal still holds the undone game: Resume would play THAT back. Give it up (kept as "not_resumable").
            crashlog.note(f"Undo: the journal could not be cut back, so this game can't be resumed or rewound again: {e}")
            try:
                self.journal.discard(time.time())
            except OSError:
                pass
        self._hook_session()
        self.reset_game()
        self.resumed_game = True                          # Round UX1: no tour
        self.rewind_points = [p for p in self.rewind_points if p.get("i", 0) < cut]
        self.rewind_expect = {k: v for k, v in self.rewind_expect.items() if k < cut}
        starts = [p.get("turn") for p in self.rewind_points if p.get("start")]
        self.rewind_tracker.reset(turn_started=max(starts) if starts else None)
        self._drops_session, self._drops_seen = new, len(getattr(new, "dropped", []) or [])
        self._stats_sync_session()                        # the old game's record is closed (unfinished), this one goes on
        rec, events = self.stats_rec, getattr(new, "events", None)
        while events:                                     # the rebuilt turns' events count for the stats, but make no sound
            ev, _rx = events.popleft()
            if rec is not None:
                rec.note_event(ev)
        self.play_cue("rewind")
        took = f" in {job.seconds:.0f} s" if job.seconds else ""
        self.say(f"Rewound to {job.label} ({len(job.commands)} actions rebuilt{took}).", GREEN, 6.0)

    # ---- passing for me: Skip window, auto-pass ---------------------------------------------------------

    def yield_state(self):
        """What Forge is doing on my behalf: {mode, phase, autoPass, autoYields, autoTriggers} ({} from a bridge that predates this)."""
        return (self.state or {}).get("yield") or {}

    def skip_available(self):
        """The Skip button / S key work while I hold priority (or a skip is running, to stop it) and the bridge knows how to skip."""
        st = self.state
        if not st or "yield" not in st or self.session.me() is None:
            return False
        kind = self.input_kind()
        at_priority = kind == "priority" if kind is not None else self.prompt().get("message", "").startswith("Priority:")
        return at_priority or bool(self.yield_state().get("mode"))

    def smart_passing(self):
        """Patch 43: the bridge passes the meaningless priority stops for me (Passing.java). False for an older bridge, a classic-stops
        session (the card check), a spectator, and (round PRI1) my seat in Slow: then the table plays the way it did before patch 43
        (Enter is OK, no Ctrl keys, the old Skip window). The newest snapshot says how my seat plays now; before the first one (and
        for a bridge whose snapshots don't say), the "ready" line does. An online guest's table never sees that line at all."""
        s = self.session
        if getattr(s, "spectator", False) or s.me() is None:
            return False
        p = ((self.state or {}).get("yield") or {}).get("passing")
        if isinstance(p, dict) and "classic" in p:
            return p.get("classic") is False
        return getattr(s, "passing", None) == "smart"

    # ---- round PRI1: Speed, Fast or Slow (Karl, 9 Oct) ----

    def speed_label(self):
        return SPEED_LABELS.get(self.speed, "Fast")

    def skip_label(self):
        """The Skip button's words (patch 43: "Full control" while it is on; round PRI1: never "(auto)" in Slow, where auto-pass is off)."""
        if self.full_control_on():
            return "Full control"
        return "Skip (auto)" if self.auto_pass and not self.slow_now() else "Skip..."

    def slow_now(self):
        """My seat plays Slow right now (the bridge's own word for it, from the newest snapshot)."""
        return bool(self.passing_state().get("slow"))

    def speed_supported(self):
        """The bridge takes the Speed command for my seat: its snapshots say "slow" (round PRI1 on), and the whole engine isn't
        playing the old way anyway (--classic-stops, the card check)."""
        p = self.passing_state()
        return "slow" in p and not (p.get("classic") and not p.get("slow"))

    def sync_speed(self):
        """Every sync: make my seat play the Speed in the settings. It is sent at the first snapshot that says how the seat plays
        (a new engine starts Fast; the mulligan comes before any priority), and again when the setting changes - again after
        SPEED_RESEND if the seat still hasn't taken it (a command that reached the bridge before its game did is dropped). Nothing
        is sent for Fast unless the seat is Slow, so a game in Fast sends exactly what it did before this round."""
        st = self.state
        if not st or self.session.me() is None or getattr(self.session, "spectator", False) or not self.speed_supported():
            return False
        want = self.speed == "slow"
        if want == self.slow_now():
            self._speed_sent = None
            return False
        key, now = (id(self.session), self.speed), time.monotonic()
        if self._speed_sent is not None and self._speed_sent[0] == key and now - self._speed_sent[1] < SPEED_RESEND:
            return False
        self._speed_sent = (key, now)
        self.session.set_speed(self.speed)
        return True

    def toggle_speed(self):
        """Cog > GAME > Speed: Fast <-> Slow. Saved at once; a game on the table changes at my next priority."""
        self.speed = "slow" if self.speed != "slow" else "fast"
        self.save_settings()
        sent = self.game_in_progress() and self.sync_speed()
        now = " from your next priority" if sent else (" from the next game" if self.game_in_progress() and not self.speed_supported()
                                                        else "")
        if self.speed == "slow":
            self.say(f"Slow{now}: you pass priority yourself at the old stops (your Main 1, attackers, Main 2 and end step; each "
                     "opponent's upkeep, attackers and end step; anything on the stack), even when you can't act.", CYAN, 8.0)
        else:
            self.say(f"Fast{now}: Forge passes the stops that don't matter again (patch 43).", CYAN, 5.0)

    def at_priority(self):
        """Forge is asking me for priority right now (not a payment, a target, an attack...)."""
        st = self.state
        if not st or st.get("asking") is False:
            return False
        kind = self.input_kind()
        return kind == "priority" if kind is not None else self.prompt().get("message", "").startswith("Priority:")

    def passing_state(self):
        return ((self.state or {}).get("yield") or {}).get("passing") or {}

    def full_control_on(self):
        p = self.passing_state()
        return bool(p.get("fullControl") or p.get("turnControl"))

    def track_yield(self):
        """Once per game, with the first snapshot that has Forge's yield state: send my saved auto-pass choice and (patch 43) the
        cards I always want to stop on. A patch-43 bridge starts with auto-pass on, so "off" is sent too."""
        st = self.state
        if not st or "yield" not in st or self.auto_pass_sent is not None:
            return
        self.auto_pass_sent = self.auto_pass
        if self.smart_passing():
            if not self.auto_pass:
                self.session.auto_pass(False)
            if self.always_stop:
                self.session.always_stop(self.always_stop, True)
        elif self.auto_pass and not st["yield"].get("autoPass"):
            self.session.auto_pass(True)

    def track_passing(self):
        """Patch 43, every tick: the opponents' triggers and abilities passed for me go into the feed; the second stop in a turn by
        the same card's trigger or ability offers Y = always pass on it; the first game after the update says what changed."""
        s, st = self.session, self.state
        if not st:
            return
        passed = getattr(s, "passed", [])
        if self._passed_seen > len(passed):
            self._passed_seen = 0
        for m in passed[self._passed_seen:]:
            what = "trigger" if m.get("kind") == "trigger" else "ability"
            segs = [("Passed for you: ", flog.DIM), (str(m.get("player", "?")), flog.OPP), ("'s ", flog.TEXT),
                    (str(m.get("card", "?")), flog.CARD), (f" {what}", flog.TEXT)]
            row = flog.Row("line", segs, "dim", flog.OPP, feed=True)
            self.add_feed(row)
            self._extra_rows.append(row)
        self._passed_seen = len(passed)
        if self.passing_intro_due and self.smart_passing() and self.at_priority():
            self.passing_intro_due = False
            self.say("Only the stops that matter now: Forge passes for you when you can't do anything, and opponents' triggers pass "
                     "unless you hold an answer. Enter: pass until an opponent acts. Ctrl+Shift: full control. H for all keys.",
                     CYAN, 9.0)
            self.save_settings()
        if not self.smart_passing() or not self.at_priority():
            return
        stack = st.get("stack") or []
        top = stack[0] if stack else None
        if not top or not (top.get("trigger") or top.get("ability")) or not top.get("key"):
            return
        seq = st.get("inputSeq")
        if seq == self._stop_seq:
            return
        self._stop_seq = seq
        name = (top.get("card") or {}).get("name") or ""
        if not name or name in self._offered_yield or top.get("autoYield"):
            return
        k = (st.get("turn"), name)
        self._stop_counts = {kk: v for kk, v in self._stop_counts.items() if kk[0] == st.get("turn")}
        self._stop_counts[k] = self._stop_counts.get(k, 0) + 1
        if self._stop_counts[k] >= REPEAT_STOP_OFFER:
            self._offered_yield.add(name)
            self.say(f"{self.ability_name(top)} has stopped you {self._stop_counts[k]} times this turn. "
                     "Press Y to always pass on it this game (S lists your choices).", CYAN, 8.0)

    # ---- patch 44: stats (stats.py) ----

    def local_stats_info(self, labels, fmt):
        """The stats identity of a game started from the deck screen (or Restart): a new id, my deck and the AIs' decks."""
        mine, opps = self.stats_decks or (None, None)
        labels = labels or self.current_deck_labels or ("", [])
        if mine is None:
            mine = {"id": None, "name": labels[0] or ""}
            opps = [{"id": None, "name": n or ""} for n in (labels[1] or [])]
        return {"id": gstats.new_id(), "format": formats.normal(fmt), "online": None, "deck": dict(mine),
                "opponent_decks": [dict(o) for o in opps or []]}

    def _stats_sync_session(self):
        """A new session is a new game: the old one's recorder is closed (unfinished, conceded or lost) and a new one starts.
        Nobody's record is kept for someone only watching."""
        s = self.session
        if s is self._stats_session:
            return
        self.stats_close()
        self._stats_session = s
        self._stats_state = self._stats_over_at = None
        self.stats_end = None
        self._stats_lines = None
        if getattr(s, "spectator", False):
            self.stats_rec = None
            return
        info = getattr(s, "stats_info", None)
        if not info:
            info = self.local_stats_info(None, getattr(s, "fmt", None))
            if getattr(s, "online", None):
                info.update(online=s.online, opponent_decks=[])
        self.stats_rec = gstats.Recorder(info)

    def track_stats(self):
        """Every tick: the snapshot to the recorder, and at game over its end line - with Forge's own result (it comes with
        game_over, just after the last snapshot), or without it after STATS_RESULT_WAIT."""
        self._stats_sync_session()
        rec, s, st = self.stats_rec, self.session, self.state
        if rec is None or rec.end is not None:
            return
        if st and st is not self._stats_state:
            self._stats_state = st
            rec.note_state(st)
        result = (getattr(s, "game_over_info", None) or {}).get("result")
        if getattr(s, "game_over", False) and isinstance(result, dict):
            self.stats_end = rec.finish(result, st)
        elif getattr(s, "game_over", False) or (st or {}).get("gameOver"):
            now = time.monotonic()
            if self._stats_over_at is None:
                self._stats_over_at = now
            elif now - self._stats_over_at > STATS_RESULT_WAIT:
                self.stats_end = rec.finish(None, st)

    def stats_close(self):
        """The game on the table goes away (a new game, the main menu, closing the program): write down how it stands."""
        rec = self.stats_rec
        if rec is not None and rec.end is None:
            try:
                self.stats_end = rec.close()
            except Exception as e:                       # stats must never stop the program
                print(f"[forge_table] stats: {e}")

    def end_stats_lines(self):
        """Patch 44: the end-of-game screen's lines - how the game ended, and this deck's record (decision 8)."""
        rec = self.stats_rec
        if rec is None:
            return []
        if rec.end is None:
            line = rec.out_line()                      # out of a pod that plays on
            return [line] if line else []
        if self._stats_lines is None:
            try:
                games = gstats.load(rec.where)
                summary = gstats.summarize(games, gstats.deck_key(rec.start), bool(rec.start.get("online")))
                name = (rec.start.get("deck") or {}).get("name") or "This deck"
                self._stats_lines = gstats.end_lines(rec.end, summary, name)
            except Exception as e:
                print(f"[forge_table] stats: {e}")
                self._stats_lines = gstats.end_lines(rec.end)
        return self._stats_lines

    def toggle_auto_pass(self):
        self.auto_pass = not self.auto_pass
        self.auto_pass_sent = self.auto_pass
        self.session.auto_pass(self.auto_pass)
        self.say("Auto-pass on: Forge passes for you when you have nothing to do. You still stop when you could answer something."
                 if self.auto_pass else "Auto-pass off: you get every stop you have set again.", DIM, 5.0)
        self.save_settings()

    # ---- patch 43: the Arena keys ----

    def pass_until_something(self):
        """Enter at priority: Forge's pass-until-end-of-turn, which an opponent's spell (if you can answer it) or an attack ends."""
        self.session.yield_turn()
        self.say("Passing until an opponent casts something or attacks you, or the turn ends. Esc stops it.", DIM, 3.0)

    def pass_the_turn(self):
        """Shift+Enter at priority: pass everything for the rest of this turn (you are still asked to block)."""
        self.session.pass_turn()
        self.say("Passing the rest of this turn.", DIM, 3.0)

    def toggle_full_control(self):
        """Ctrl+Shift: every stop, nothing passed for you, until you press it again."""
        on = self.passing_state().get("fullControl")
        self.session.full_control("off" if on else "on")
        self.say("Full control off: Forge passes the stops that don't matter again." if on else
                 "Full control on: you stop at every step and nothing is passed for you. Ctrl+Shift turns it off.",
                 CYAN, 5.0)

    def full_control_this_turn(self):
        """Ctrl: every stop until this turn ends."""
        if self.passing_state().get("fullControl"):
            return
        self.session.full_control("turn")
        self.say("Full control until this turn ends.", CYAN, 3.0)

    def note_modifier(self, ev):
        """A Ctrl tap (pressed and released with nothing else) is full control for this turn; Ctrl+Shift toggles it for good."""
        ctrl_keys = (pygame.K_LCTRL, pygame.K_RCTRL)
        shift_keys = (pygame.K_LSHIFT, pygame.K_RSHIFT)
        if ev.type == pygame.KEYDOWN:
            mod = getattr(ev, "mod", 0)
            if ev.key in ctrl_keys:
                self._ctrl_tap, self._ctrl_shift = True, bool(mod & pygame.KMOD_SHIFT)
            elif ev.key in shift_keys and (mod & pygame.KMOD_CTRL):
                self._ctrl_shift = True
            else:
                self._ctrl_tap = False
        elif ev.type == pygame.KEYUP and ev.key in ctrl_keys:
            tapped, shift = self._ctrl_tap, self._ctrl_shift
            self._ctrl_tap = self._ctrl_shift = False
            if tapped and self.state and self.smart_passing() and not (self.modal or self.overlay or self.menu or
                                                                      self.tour_visible() or self.boot is not None):
                if shift:
                    self.toggle_full_control()
                else:
                    self.full_control_this_turn()
        elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEWHEEL):
            self._ctrl_tap = False

    def always_pass_top(self):
        """Y: always pass on the trigger or ability on top of the stack (Forge's own "auto-yield", this game)."""
        stack = (self.state or {}).get("stack") or []
        top = next((it for it in stack if it.get("ability") and it.get("key")), None)
        if not top:
            self.say("Nothing on the stack to always pass on.", DIM, 3.0)
            return
        self.session.auto_yield(top["key"], True)
        self.say(f"Always passing on {self.ability_name(top)} this game. S lists your choices.", DIM, 5.0)

    def set_always_stop(self, name, on):
        names = [n for n in self.always_stop if n != name]
        if on:
            names.append(name)
        self.always_stop = names[-60:]
        self.session.always_stop([name], on)
        self.say(f"Always stopping on {name}: its spells, triggers and abilities stop you when you could act." if on else
                 f"No longer always stopping on {name}.", DIM, 5.0)
        self.save_settings()

    def ability_name(self, item):
        card = (item.get("card") or {}).get("name") or "this"
        return f"{card}'s {'trigger' if item.get('trigger') else 'ability'}"

    def skip_options(self):
        """[(label, run, style)] for the Skip window, from what is on the table and what Forge is already doing for me."""
        s, y, st = self.session, self.yield_state(), self.state or {}
        stack = st.get("stack", [])
        opts = []
        smart = self.smart_passing()
        if y.get("mode"):
            opts.append(("Stop skipping: give me priority again", s.yield_clear, "normal"))
        if stack:
            opts.append(("Let the stack resolve", s.yield_stack, "normal"))
        opts.append(("Skip to my next turn", s.yield_until, "normal"))
        if self.slow_now():                                # round PRI1: auto-pass is off while Slow lasts; this is the way back
            opts.append(("Speed is Slow: switch to Fast", self.toggle_speed, "normal"))
        else:
            opts.append((f"Auto-pass when I can't do anything: {'ON' if self.auto_pass else 'OFF'}", self.toggle_auto_pass, "normal"))
        if smart:
            opts.append((f"Full control (Ctrl+Shift): {'ON' if self.passing_state().get('fullControl') else 'OFF'}",
                         self.toggle_full_control, "normal"))
        top = next((it for it in stack if it.get("ability") and it.get("key")), None)
        if top:
            name = self.ability_name(top)
            key = top["key"]
            opts.append((f"Always pass on {name}", lambda: s.auto_yield(key, True), "normal"))     # the Y key does the same (patch 43)
            if top.get("optional"):
                if top.get("decision") == "ask":
                    opts.append((f"Always use {name}", lambda: s.trigger_decision(key, "accept"), "normal"))
                    opts.append((f"Never use {name}", lambda: s.trigger_decision(key, "decline"), "normal"))
                else:
                    opts.append((f"Ask me again about {name}", lambda: s.trigger_decision(key, "ask"), "normal"))
        top_card = ((stack[0] if stack else {}).get("card") or {}).get("name")
        if smart and top_card and top_card not in self.always_stop:
            opts.append((f"Always stop on {top_card}", lambda n=top_card: self.set_always_stop(n, True), "normal"))
        for name in self.always_stop[:1]:
            opts.append((f"Stop always stopping on {name}", lambda n=name: self.set_always_stop(n, False), "normal"))
        rules = list(y.get("autoYields") or [])
        for text in rules[:2]:
            opts.append(("Stop always passing: " + flog.tidy(text), lambda t=text: s.auto_yield(t, False), "normal"))
        n = len(rules) + len(y.get("autoTriggers") or {})
        if n > 0:
            opts.append((f"Forget all my 'always' choices ({n})", s.reset_yields, "normal"))
        return opts[:9]

    def open_skip(self):
        if not self.skip_available():
            return
        if self.slow_now():                                # round PRI1
            text = ("Speed is Slow (Cog > GAME): you get priority at the stops from before patch 43 - your Main 1, attackers, Main 2 "
                    "and end step; each opponent's upkeep, attackers and end step - and whenever something is on the stack, and "
                    "nothing is passed for you. A skip below stops early if an opponent casts a spell or attacks you; Esc or Cancel "
                    "ends it.")
        elif self.smart_passing():
            text = ("Forge passes for you when you can't do anything, your own spells resolve without a stop (hold Ctrl as you cast "
                    "to keep priority), and an opponent's trigger or ability passes unless you hold an answer to it. Enter passes "
                    "until an opponent casts something or attacks you, Shift+Enter passes the rest of the turn, Ctrl gives full control "
                    "for this turn and Ctrl+Shift until you turn it off.")
        else:
            text = ("Forge can pass priority for you while you have nothing to do. A skip stops early if an opponent casts a spell or "
                    "attacks you, and Esc or Cancel ends it. Auto-pass makes Forge pass by itself whenever it finds nothing you can do; a hand with "
                    "free spells or instants nearly always has something, so with a deck like that it will seldom pass for you.")
        self.modal = dlg.OptionsDialog("Skip ahead", text, self.skip_options(), "Never mind")

    def track_events(self):
        """Hand Forge's game events to the router; keep the beats the current snapshot already shows (bridge protocol 2)."""
        s, now = self.session, time.monotonic()
        events = getattr(s, "events", None)
        self._stats_sync_session()                      # patch 44: the recorder of this session gets every event
        rec = self.stats_rec
        while events:
            ev, rx = events.popleft()
            if rec is not None:
                rec.note_event(ev)
            self.router.feed(ev, rx)
        self.beats_this_frame = self.router.ready((self.state or {}).get("eventSeq"), now)

    def synthetic_beat(self, kind, **data):
        """An old bridge (protocol 1) sends no events: make the beat from the snapshot change the trackers just saw."""
        if getattr(self.session, "protocol", 1) >= 2:
            return
        self.beats_this_frame.extend(self.router.synthetic(kind, time.monotonic(), **data))

    def track_quiet(self, now=None):
        """A command sent and not a word back: after QUIET_PING ask Forge for a snapshot (its game thread may be busy, but the bridge
        answers a ping at once while the program is alive); after QUIET_BANNER the banner shows and crash_log.txt gets one line."""
        s = self.session
        now = time.monotonic() if now is None else now
        last = self._last_cmd_at
        if last is None or getattr(s, "last_rx", now) >= last:
            return
        if now - last >= QUIET_PING and self._pinged_at is None and s.alive():
            self._pinged_at = now
            s.send(c="flush")
        if self.not_responding(now) and not self.not_responding_noted:
            self.not_responding_noted = True
            crashlog.note("Forge not answering", f"No message from Forge for {now - last:.1f} s after a command. Prompt: "
                          f"{self.prompt().get('message', '')[:200]!r}. Engine alive: {s.alive()}.")

    def not_responding(self, now=None):
        """True while the banner shows: a command went out more than QUIET_BANNER s ago, nothing at all has come back since, the engine
        process is running, and Forge is not simply thinking for an AI player ('Waiting for ...')."""
        s = self.session
        now = time.monotonic() if now is None else now
        last = self._last_cmd_at
        if last is None or getattr(s, "last_rx", now) >= last or now - last < QUIET_BANNER:
            return False
        if not s.alive() or self.prompt().get("message", "").startswith("Waiting for"):
            return False
        if getattr(s, "peer_dropped", None) or getattr(s, "net_state", None) == "reconnecting":
            return False                                   # Round MP2: the online banner says what is going on
        return True

    def online_banner_text(self, now=None):
        """Round MP2: the line shown while a connection is down: the guest's own ("reconnecting"), or the guest's seen from the
        host's table or a spectator's ("waiting for them to come back"), with the seconds left. None when all is well."""
        s = self.session
        now = time.monotonic() if now is None else now
        if getattr(s, "net_state", None) == "reconnecting":
            left = self._drop_left(now, getattr(s, "grace", 0))
            return "Connection to %s lost  -  reconnecting...%s" % (s.peer_name or "the host",
                                                                 "  (%d s left)" % left if left is not None else "")
        drop = getattr(s, "peer_dropped", None)
        if drop:
            names = [m.get("name") or "Your friend" for m in (getattr(s, "dropped_seats", None) or {}).values()] or \
                [drop.get("name") or "Your friend"]
            who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            left = self._drop_left(now, drop.get("grace") or 0)
            return "%s's connection%s dropped  -  waiting for them to come back%s" % (
                who, "s" if len(names) > 1 else "", "  (%d s left)" % left if left is not None else "")
        return None

    def _drop_left(self, now, grace):
        if not self.drop_noticed or not grace:
            return None
        return max(0, int(round(self.drop_noticed[1] - (now - self.drop_noticed[0]))))

    def draw_online_banner(self):
        text = self.online_banner_text()
        if not text:
            return
        f = self.font("small", True)
        r = pygame.Rect(self.L.main.x, self.L.top_h, self.L.main.w, f.get_height() + 8)
        round_rect(self.screen, r, gfx.BANNER_BG, 0, 1, ORANGE, alpha=235)
        draw_text(self.screen, text, r.centerx, r.y + 4, f, ORANGE, "midtop")

    def draw_quiet_banner(self):
        if not self.not_responding():
            return
        f = self.font("small", True)
        waited = int(time.monotonic() - self._last_cmd_at)
        if getattr(self.session, "online", None) == "guest":      # Round MP1: the engine is on the host's PC
            text = f"No answer from the host for {waited} s  -  their connection may be slow.  (F8 to report)"
        else:
            text = f"Forge has not answered for {waited} s  -  still working?  (F8 to report)"
        r = pygame.Rect(self.L.main.x, self.L.top_h, self.L.main.w, f.get_height() + 8)
        round_rect(self.screen, r, gfx.BANNER_BG, 0, 1, ORANGE, alpha=235)
        draw_text(self.screen, text, r.centerx, r.y + 4, f, ORANGE, "midtop")

    def track_arrivals(self):
        """Note every permanent on the battlefield; the ones that were not there at the last snapshot get their ring."""
        st = self.state
        if not st:
            return
        cards, origins = {}, {}
        seen = self.arrivals.seen
        for p in st.get("players", []):
            for c in p.get("zones", {}).get("battlefield", []):
                if c.get("id") is not None:
                    cards[c["id"]] = bool(c.get("isLand"))
                    if seen is not None and c["id"] not in seen:
                        origin = self.arrival_origin(c)
                        origins[c["id"]] = origin
                        self.start_flight(c["id"], origin)          # round 23: spring flight, retargeted every frame
                        self.synthetic_beat("zone", card=c["id"], to="Battlefield")
        self.arrivals.update(cards, time.time(), origins)

    def arrival_origin(self, card):
        """Where a permanent that just arrived was a moment ago, as a rect on the screen: its place in my hand or command zone, its row in
        the stack panel, else the panel of the player who controls it (my hand for me). None for a token: it came from nowhere."""
        cid = card["id"]
        for rects in (self.card_rects, self.stack_rects):           # last frame's
            if rects.get(cid) is not None:
                return pygame.Rect(rects[cid])
        if card.get("token"):
            return None
        me = self.session.me()
        if me is not None and card.get("controller") == me["id"]:
            h = self.L.hand
            return pygame.Rect(h.centerx - 20, h.y, 40, 60)
        panel = self.panel_rects.get(card.get("controller"))
        return pygame.Rect(panel) if panel is not None else None

    def track_undo(self):
        if not self.undo_check:
            return
        due, before = self.undo_check
        if self.undo_signature() != before:
            self.undo_check = None                      # something changed, so Undo did its job
            self.play_cue("rewind")                     # patch 47: AU1 built this cue (a swish played backwards); nothing played it
        elif time.time() >= due:
            self.undo_check = None
            why = self.rewind_unavailable()               # round UNDO1: Forge had nothing to undo - offer this turn's moments
            if why is None and not self.modal:
                self.open_rewind()
            else:
                self.say(why or UNDO_NOTHING, ORANGE, 5.0)

    def track_life(self):
        """Remember each player's life so a change can flash on their panel (several hits in a row add up while it is showing)."""
        st = self.state
        if not st:
            return
        now = time.time()
        for p in st.get("players", []):
            pid, life = p.get("id"), p.get("life")
            if life is None:
                continue
            old = self.life_seen.get(pid)
            if old is not None and life != old:
                self.synthetic_beat("life", player=pid, old=old, new=life)
                prev = self.life_flash.get(pid)
                delta = life - old + (prev[0] if prev and prev[1] > now else 0)
                self.life_flash[pid] = (delta, now + LIFE_FLASH_SECONDS)
            self.life_seen[pid] = life

    def track_hand(self):
        """Forge says nothing in its log when you draw (a Ring tap, Rhystic Study, the draw step), so a card that arrives in your hand
        gets a log line and, outside the draw step, a toast. The words are 'Into your hand', not 'You drew': a tutor or a bounce
        puts a card there too, and from the outside those look the same."""
        st = self.state
        me = self.session.me() if st else None
        if not me:
            return
        hand = [c for c in me["zones"].get("hand", []) if not c.get("hidden")]
        if self.hand_seen is not None and (st.get("turn") or 0) >= 1:
            new = [c for c in hand if c["id"] not in self.hand_seen]
            for c in new:
                self.synthetic_beat("zone", card=c["id"], **{"from": "Library", "to": "Hand"})
            if new:
                segs = [("Into your hand: ", flog.TEXT)]
                for i, c in enumerate(new):
                    segs += ([(", ", flog.TEXT)] if i else []) + [(c.get("name") or "a card", flog.CARD)]
                self._extra_rows.append(flog.Row("line", segs, "DRAW", flog.ME))
                if st.get("phase") != "DRAW":
                    self.say("Into your hand: " + ", ".join(c.get("name") or "a card" for c in new), CYAN, 4.0)
        self.hand_seen = {c["id"] for c in hand}

    def life_flash_of(self, pid):
        """(change, how much of the flash is left 0..1) for a player whose life just changed, else (0, 0.0)."""
        fl = self.life_flash.get(pid)
        if not fl:
            return 0, 0.0
        left = (fl[1] - time.time()) / LIFE_FLASH_SECONDS
        return (fl[0], min(1.0, left)) if left > 0 and fl[0] else (0, 0.0)

    def sync(self):
        """Apply what Forge sent since the last frame and open the dialog it is waiting for, if any. Returns True when anything
        changed that needs a redraw: a message from Forge, a picture, or a dialog / overlay / deck screen that opened or closed."""
        before = (id(self.modal) if self.modal else 0, id(self.overlay) if self.overlay else 0, id(self.menu) if self.menu else 0)
        version = self.session.state_version
        n = self._sync()
        if self.session.state_version != version:
            self.perf.snapshot_arrived(time.monotonic())
        after = (id(self.modal) if self.modal else 0, id(self.overlay) if self.overlay else 0, id(self.menu) if self.menu else 0)
        if bool(before[0]) != bool(after[0]):            # round 24: self.modal opened or closed
            self.play_cue("ui.dialog_open" if after[0] else "ui.dialog_close")
        return bool(n) or before != after

    def _sync(self):
        """The work of sync(); returns how many messages and pictures arrived."""
        s = self.session
        if self.resuming is not None:                         # the resume worker is the only one talking to the engine now
            self.track_resume()
            if self.art:
                self.art.collect()
            return 1                                          # keep the progress screen moving
        n = s.poll() or 0
        self.track_points()                        # round UNDO1: before anything of the table's own answers this snapshot
        self.track_drops()
        self.track_events()
        self.track_quiet()
        self.track_life()
        self.track_hand()
        self.track_arrivals()
        self.track_undo()
        self.track_yield()
        self.sync_speed()                          # round PRI1: my seat plays the Speed in the settings (Slow from the first snapshot)
        self.track_passing()                       # patch 43
        self.track_stats()                         # patch 44
        if self.art:
            n += self.art.collect() or 0
            for path, why in list(getattr(self.art, "custom_refused", {}).items()):     # round ALT1: a my_art picture not used
                if path not in self._art_refused_said:
                    self._art_refused_said.add(path)
                    self.say(why + " - Scryfall's picture is used instead.", ORANGE, 8.0)
        if self.overlay and self.overlay.done:
            self.overlay = None
        if self.modal and self.modal.done:
            closes = self.modal.closes_game
            self.modal = None
            if closes:
                self.running = False
        if self.rewind_prestart is not None and not isinstance(self.modal, dlg.RewindDialog) and self.resuming is None:
            self.close_rewind_prestart()                       # round UNDO1: the Undo window went away without a choice
        if self.track_online():                               # Round MP1: the other table left - before any other dialog
            return n
        if self.modal or self.menu:
            return n
        self.track_pregame()
        if (s.fatal or (s.exited and not s.game_over)) and not self.shown_fatal and self.state and getattr(s, "online", None) != "guest":
            self.shown_fatal = True
            # patch 46: when Java itself crashed, say so and name its crash report (forge_engine.log has nothing then)
            self.modal = dlg.MessageDialog("Forge stopped", s.fatal or java_crash.stopped_message(getattr(s, "stderr_path", None),
                                                                                                   self.java_crash_report()),
                                           "Close", True, RED)
        elif s.requests:
            self.modal = dlg.make_request_dialog(s.requests[0], self)
        elif s.infos:
            m = s.infos.popleft()
            title = m.get("title", "")
            if m.get("t") == "message":
                if m.get("text"):
                    why = forge_why.explain(m["text"], self.find_card)          # Karl, 2 Oct: say WHY Forge refused (menace, ...)
                    if why:
                        self.modal = dlg.MessageDialog(why.title, why.body)
                        card = self.find_card(why.card_id) if why.card_id is not None else None
                        if card and card.get("name"):
                            self.spot = (card["name"], time.time() + 6.0)       # the card it's about glows and shows in the focus panel
                    else:
                        self.modal = dlg.MessageDialog(title, strip_ids(m["text"]))
            elif "Deck" in title or "can't play" in title.lower():
                self.say("Forge noted deck-conformance warnings (see forge_engine.log)", DIM)
            elif m.get("items"):
                self.modal = dlg.info_dialog(m)
        elif self.wants_play_draw():
            self.modal = dlg.PlayDrawDialog(self.prompt().get("message", ""), self.deck_names)
        elif (s.game_over or (self.state or {}).get("gameOver")) and not self.shown_over:
            self.shown_over = True
            me = self.session.me()
            winner = (self.state or {}).get("winner")
            self.end_journal("won" if me and winner and winner == me["name"] else "lost")
            self.end_online_save("won" if me and winner and winner == me["name"] else "lost")     # Round MP2b
            if self.shown_out:                       # Round AD2b: I was knocked out earlier - DEFEAT was shown then, not again
                self.end_screen = None
                self.modal = dlg.game_over_dialog(self.state or {}, me["name"] if me else "", self.open_menu)
                self.modal.closes_game = False
            else:
                self.end_screen = flow.end_screen_for(self)
        elif self.eliminated_in_pod() and not self.shown_out:
            self.shown_out = True                    # found live: after Concede in a pod the AIs play on and nothing said so
            self.end_screen = flow.end_screen_for(self)    # Round AD2b: DEFEAT, then Keep watching / Leave the game
        return n

    def eliminated_in_pod(self):
        """I have lost, but the game goes on between the others (three or more players; a two-player game just ends)."""
        me = self.session.me()
        st = self.state or {}
        return bool(me and me.get("lost") and len(st.get("players", [])) > 2 and not (self.session.game_over or st.get("gameOver")))

    def wants_play_draw(self):
        p = self.prompt()
        msg = " ".join(clean_text(p.get("message", "")).split())
        return ("won the coin toss" in msg or "lost the last game" in msg) and p.get("ok", {}).get("label") == "Play" \
            and p.get("cancel", {}).get("label") == "Draw" and msg != self.answered_prompt

    def track_pregame(self):
        """Count my mulligans from Forge's 'return N cards' prompts (the first, free one returns none), and press OK for
        the one prompt that has nothing to choose: 'Return 0 card(s) to the bottom of your library'."""
        p = self.prompt()
        flat = " ".join(clean_text(p.get("message", "")).split())
        kind = self.input_kind()
        if kind is not None:
            m = re.search(r"(\d+)", flat) if kind == "bottom" else None
        else:
            m = re.match(r"^Return (\d+) card\(s\) to the bottom of your library$", flat)
        if not m:
            return
        n = int(m.group(1))
        self.mulligans = max(self.mulligans, n + 1)
        if n == 0 and p.get("ok", {}).get("enabled") and self.auto_ok_at is None:
            self.auto_ok_at = time.time()                     # once only: the old prompt lingers a few frames and OK on the next one is 'Keep'
            self.session.ok()

    def activity(self, now):
        """(moving, pulsing) from what is on screen, for the frame pacer. moving: something changes every frame (a ring flashing, a card
        gliding in, a life total flashing, a feed line fading, the loading screen). pulsing: only slow glows and marching dashes."""
        t = time.time()
        st = self.state
        ui_wait = bool(self._over_cand is not None or self._over_miss is not None        # patch UI6: a hover timer is running
                       or (self._log_open and self._log_left is not None))
        flow_moving = bool((self.vs is not None and self.vs.holding(self))
                           or (self.end_screen is not None and now - self.end_screen.t0 < flow.END_ANIM + 0.1)
                           or (self.animations and self.anim.active(now))          # Round AD2c
                           or (self.boot is not None and self.boot.moving(now))     # Round AD2
                           or (self.tour_visible() and self.tour.moving(now, self.animations)))   # Round UX1
        moving = bool(st is None or self.menu or flow_moving or ui_wait or self.arrivals.recent(t) or self.spot_name()
                      or any(fl[1] > t for fl in self.life_flash.values())
                      or any(t - born > FEED_SECONDS - 1.2 for _row, born in self.feed)
                      or any(not cm.at_rest(now) for cm in self.motion.values())        # round 23: springs still settling
                      or self.ghosts or self.particles.count() or self.front_particles.count()    # round 25, patch 48
                      or self.screen_shaker.active(now) or any(sh.active(now) for sh in self.panel_shakers.values()))
        if not self.animations:
            moving = bool(st is None or self.menu or flow_moving or ui_wait or any(t - born > FEED_SECONDS - 1.2 for _row, born in self.feed))
        pulsing = bool(self.modal or self.overlay or self.menu or self.show_perf)
        if st and self.animations and not pulsing:
            p = self.prompt()
            pulsing = bool(p.get("ok", {}).get("focus") or st.get("stack") or self.waiting()
                           or any(c.get("attacking") or c.get("blocking")
                                  for pl in st.get("players", []) for c in pl.get("zones", {}).get("battlefield", [])))
        return moving, pulsing

    def tick(self, now):
        """One pass of the main loop: events, engine sync, and a redraw only when needed. Returns True when a frame was drawn."""
        had_input = False
        for ev in pygame.event.get():
            had_input = True
            if not self.handle_event(ev):
                self.running = False
        self.check_display(now)             # round 27e: a Remote Desktop reconnect changes the screen under a running game
        self.poll_crash_job()               # round 28d
        changed = self.sync()
        self.maybe_start_tour()             # Round UX1: the first priority question of the first game
        self.apply_audio()                  # round 24: sound follows every tick, not only drawn frames
        self.apply_effects(now)             # round 25: ghosts, particles and shakes follow every tick too
        moving, pulsing = self.activity(now)
        if moving:
            self.pacer.moving(now)
        if not self.pacer.should_draw(now, had_input or changed or self._force_draw):
            self.perf.skip()
            return False
        self._force_draw = False
        t0 = time.perf_counter()
        self.render()
        self.perf.frame((time.perf_counter() - t0) * 1000, "dialog" if (self.modal or self.overlay) else "table")
        self.pacer.drew(now, pulsing)
        return True

    # ---- round 28d: the last run closed unexpectedly (last_session.py) -----------------------------------------------------
    def crash_report(self, action):
        """The deck screen's banner buttons. "send": build the report (once) and post it, or save it when Discord isn't set
        up; "look": build it and show its folder, keeping the offer open; "dismiss": forget it. Never raises."""
        try:
            if action == "dismiss":
                self._end_crash_offer()
                return
            if not self.crash_zip:
                name = getattr(self.launcher, "name", "") or ""
                journal_path = self.journal.path if self.journal else None
                self.crash_zip = reporting.build_report(last_session.report_info(self.last_run, name),
                                                        extra_files=last_session.report_files(journal_path=journal_path))
            if action == "look":
                reporting.open_folder(os.path.dirname(self.crash_zip))
                self.say(f"The report is {os.path.basename(self.crash_zip)} - press Send report when you're happy with it.", DIM, 6.0)
                return
            url, why = reporting.config_status()
            if url:
                info = last_session.report_info(self.last_run, getattr(self.launcher, "name", "") or "")
                self.crash_job = reporting.SendJob(url, reporting.summary_text(info, time.strftime("%Y-%m-%d %H:%M")), self.crash_zip)
                self.crash_job.start()
                self.say("Sending the report...", DIM, 4.0)
            else:
                self.say(f"Saved {os.path.basename(self.crash_zip)} in the bug_reports folder. {why}", ORANGE, 8.0)
            self._end_crash_offer()
        except Exception as e:                                  # a helper: it must never take the program down
            crashlog.record(e, "ERROR building the crash report")
            self.say("The report could not be made (saved in crash_log.txt).", RED, 6.0)
            self._end_crash_offer()

    def _end_crash_offer(self):
        self.last_run = None
        if self.menu:
            self.menu.crash_offer = ""

    def poll_crash_job(self):
        job = self.crash_job
        if job is not None and job.finished:
            self.crash_job = None
            ok, _kind, msg = job.result
            self.say(msg if ok else f"The report was not sent: {msg} It is saved in the bug_reports folder.", GREEN if ok else ORANGE, 6.0)

    def run(self):
        watchdog = crashlog.Watchdog().start()             # writes to crash_log.txt if the window stops responding
        bad_frames = 0
        try:
            while self.running:
                try:
                    self.tick(time.monotonic())
                    bad_frames = 0
                except Exception as e:                     # one bad frame must not take the whole game down
                    bad_frames += 1
                    self._force_draw = True
                    crashlog.record(e, "ERROR in the game loop")
                    if bad_frames == 1:
                        try:
                            self.say("Something went wrong (saved in crash_log.txt). Tell Claude if the table looks odd.", RED, 6.0)
                        except Exception:
                            pass
                    if bad_frames >= MAX_BAD_FRAMES:
                        raise                              # the same error every frame: the excepthook writes the CRASH entry and tells you
                self.clock.tick(60)
                watchdog.beat()
        finally:
            watchdog.stop()
            self.shutdown()

    def crash_context(self):
        """A few lines about what the table is doing, for crash_log.txt."""
        s = self.state or {}
        if self.menu:
            showing = "the deck screen"
        elif self.modal:
            showing = f"a dialog ({type(self.modal).__name__})"
        else:
            showing = "the table"
        lines = [f"Showing: {showing}" + (f"; window {self.L.W}x{self.L.H}, text size {self.text_scale}" if self.L else "")]
        if s:
            waiting = " ".join(clean_text(self.prompt().get("message", "")).split())[:120]
            lines.append(f"Game: turn {s.get('turn')}, phase {s.get('phase')}, {len(s.get('players', []))} players, "
                         f"{len(s.get('stack', []))} on the stack; Forge is asking: {waiting!r}")
        else:
            lines.append("Game: none running")
        alive = getattr(self.session, "alive", None)
        fatal = getattr(self.session, "fatal", None)
        lines.append("Forge engine: " + ("running" if alive and alive() else "not running") + (f" (fatal: {fatal})" if fatal else ""))
        online = getattr(self.session, "online", None)
        if online:                                         # Round MP1 (never the password or the address)
            lines.append(f"Online: {online}, with {getattr(self.session, 'peer_name', None) or 'nobody yet'}")
        if self.speed == "slow" or self.slow_now():         # round PRI1 (Fast, the default, adds no line)
            lines.append(f"Speed: {self.speed_label()} in the settings; my seat plays {'Slow' if self.slow_now() else 'Fast'}")
        return lines

    def write_perf_log(self):
        """One line per session in perf_log.txt: when, which copy, window, text size, and the frame-time figures."""
        if not self.perf.drawn or not self.perf_log_path:
            return
        try:
            size = self.screen.get_size() if self.screen else (0, 0)
            line = (f"{time.strftime('%Y-%m-%d %H:%M')} | code {version.code_fingerprint()} | {size[0]}x{size[1]} | "
                    f"text {int(self.text_scale * 100)}% | {self.perf.line()}\n")
            with open(self.perf_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def shutdown(self):
        self.write_perf_log()
        self.close_rewind_prestart()               # round UNDO1: an engine started for a rewind never outlives the program
        job = self.resuming
        if getattr(job, "kind", None) == "rewind":
            job.cancelled = True
            try:
                job.session.close()
            except Exception:
                pass
        self.stats_close()                         # patch 44: a game still going is written down as unfinished
        if self.journal is not None:
            self.journal.close()
        self.save_settings()
        try:
            self.session.close()
        finally:
            pygame.quit()


# ---- launcher ---------------------------------------------------------------------------------

def parse_args(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Play Commander against Forge's AI, with Forge's rules engine.")
    ap.add_argument("deck", nargs="?", default=None,
                    help="your deck: a text export or an Archidekt URL (leave it out to pick decks on the deck screen)")
    ap.add_argument("--opp", action="append", default=[], help="an AI opponent's deck (repeat for up to 3 opponents)")
    ap.add_argument("--name", default="Karl", help="your name at the table")
    ap.add_argument("--seed", type=int, default=None, help="shuffle seed, for repeatable games")
    ap.add_argument("--record", default=None, help="write everything Forge sends to this file (debugging)")
    ap.add_argument("--runtime", default=None, help="folder with forge.jar and forge_bridge.jar")
    ap.add_argument("--version", action="store_true", help="print which copy of the program this is, and stop")
    ap.add_argument("--report-test", action="store_true", help="send a short test message to the bug report Discord channel, and stop")
    ap.add_argument("--soak", type=int, default=None, metavar="N",
                    help="play N soak-test games against Forge with the built-in bot (a canary first), write the summary to the soak "
                         "folder, and stop. Used by the installer's checks; exit code 0 means every game was valid")
    return ap.parse_args(argv[1:])


def soak_argv(games):
    """Round 29 (§4.4): what `--soak N` hands to tools.soak.main - N games on the sample decks, no mid-game boards, and the
    canary left ON (soak's default: a run whose canary fault isn't caught is marked not valid). The output goes to soak's own
    default_out_dir(): local_dir()/soak in an installed copy."""
    return ["--games", str(int(games)), "--decks", "sample", "--boards", "0"]


def ensure_std_streams():
    """Round 29: the installer's windowed Manticore.exe has no console, and PyInstaller then leaves sys.stdout / sys.stderr as
    None - a bare print() is harmless but .write() or .flush() on None would crash the game before its window opens. Point
    them at the null device. (Manticore-cli.exe is the console build and keeps its real streams.)"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _check_updates_setting():
    """Round 30: settings.json's check_updates (True when the file or the key is missing)."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("check_updates", True))
    except (OSError, ValueError, AttributeError):
        return True


def main(argv):
    ensure_std_streams()
    args = parse_args(argv)
    if args.version:
        print(version.describe())
        print(version.forge_build() or "Forge: unknown (forge_runtime/VERSION.txt not found)")
        print(paths.describe())
        print(updater.describe(_check_updates_setting()))           # Round 30
        print("Free software under the GNU GPL v3 or later; NO WARRANTY. Cog > Licenses and credits for details.")
        return
    if args.report_test:
        print(version.describe())
        ok, message = reporting.send_test(name=args.name)
        print(("OK: " if ok else "NOT SENT: ") + message)
        sys.exit(0 if ok else 1)
    if args.soak is not None:                         # round 29: Manticore-cli.exe --soak N (the console build; the windowed exe prints nothing)
        from tools import soak
        print(version.describe())
        print(paths.describe())
        sys.exit(soak.main(soak_argv(args.soak)))
    paths.ensure_dirs()                               # round 28: make sure the per-user folders exist before anything reads/writes them
    copied = paths.migrate_from_program_dir()         # D28-2: an installed copy's first start copies old data here once, never moves it
    crashlog.install()                                # from here on, a crash or a freeze is written to crash_log.txt
    if copied:
        crashlog.note("Round 28: copied old data into the per-user folder on first start",
                      f"copied {len(copied)} item(s) ({', '.join(copied)}) from {paths.program_dir()} to {paths.user_dir()}")
    print(version.describe())
    print(paths.describe())
    last_run = last_session.begin(version.describe())   # round 28d: did the last run close properly? (asks once, on the deck screen)
    if last_run is not None:
        crashlog.note("The last run did not close properly", f"it started {last_run.get('started')}; a report is offered on the deck screen")
    import forge_client as fc
    from card_data import CardDataStore
    from deck_importer import DeckImportError
    from deck_loader import describe_deck, load_deck

    fc.sync_bridge(args.runtime)                      # an updated bridge jar replaces the old copy in forge_runtime/
    problem = fc.runtime_problem(args.runtime)
    if problem:
        print(problem)
        sys.exit(1)
    launcher = Launcher(args.runtime, args.name, args.seed, args.record)
    fc.warm_card_index(args.runtime)                      # patch 38: Forge's card-name index, read or built in the background

    if args.deck is None and not args.opp:
        # No deck named on the command line: open on the deck screen, with no game running yet.
        table = ForgeTable(fc.ForgeSession("", []), CardDataStore(), prefetch_names=None, settings_path=SETTINGS_FILE,
                           launcher=launcher, saves_dir=SAVES_DIR)
        if copied:
            table.say(f"Your {' and '.join(copied)} were copied to {paths.user_dir()}", DIM, seconds=6.0)
        table.last_run = last_run
        if fc.small_pc():                                 # round 28d: under 8 GB of RAM - Forge gets less memory (default_memory_mb)
            table.say("This PC has less than 8 GB of memory: 4-player games may be slow.", ORANGE, seconds=8.0)
        table.open_boot()                                 # Round AD2: studio splash -> title and main menu -> Play = the deck screen
        table.run()
        last_session.end()                                # round 28d: closed properly - nothing to ask about next time
        return

    deck = args.deck or DEFAULT_DECK
    try:
        mine = load_deck(deck)
        opp_sources = args.opp or [deck]
        opps = [mine if src == deck else load_deck(src) for src in opp_sources[:3]]
    except DeckImportError as e:
        print(f"Could not load deck: {e}")
        sys.exit(1)
    describe_deck("You", *mine)
    for i, o in enumerate(opps, 1):
        describe_deck(f"AI {i}", *o)
    session = launcher.start(mine, opps)
    names = set(mine[0]) | set(mine[1])
    for o in opps:
        names |= set(o[0]) | set(o[1])
    table = ForgeTable(session, CardDataStore(), prefetch_names=names, settings_path=SETTINGS_FILE,
                       deck_names=set(mine[0]) | set(mine[1]), launcher=launcher, saves_dir=SAVES_DIR)
    if copied:
        table.say(f"Your {' and '.join(copied)} were copied to {paths.user_dir()}", DIM, seconds=6.0)
    table.start_journal()
    table.current_decks = (mine, list(opps))            # so Restart in the cog can play the same decks again
    table.last_run = last_run
    table.run()
    last_session.end()                                  # round 28d


if __name__ == "__main__":
    main(sys.argv)
