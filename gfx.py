# SPDX-License-Identifier: GPL-3.0-or-later
"""
gfx.py - drawing helpers for the Forge-driven table (forge_table.py).

Pure pygame; nothing here knows about Forge. Everything that is expensive (fonts, rounded
card images, glows, gradients) is cached, because the table redraws 60 times a second.
"""
import math
import os
import re
from collections import OrderedDict

import pygame

import paths

# ---- palette -------------------------------------------------------------------------
BG_TOP = (29, 18, 8)
BG_BOTTOM = (47, 33, 23)
PANEL = (42, 29, 21)
PANEL_MINE = (30, 42, 32)
PANEL_OPP = (52, 24, 22)
PANEL_EDGE = (86, 68, 52)
WHITE = (248, 242, 231)
TEXT = (233, 227, 216)
DIM = (159, 150, 139)
GOLD = (206, 164, 82)
GOLD_DARK = (128, 96, 40)
YELLOW = (255, 221, 0)                 # the one 'you can click this / this is highlighted' colour
CYAN = (96, 200, 236)
GREEN = (98, 210, 130)
RED = (230, 90, 84)
ORANGE = (240, 150, 70)
SHADOW = (0, 0, 0)

FRAME_COLOURS = {"W": (226, 220, 190), "U": (70, 118, 184), "B": (62, 56, 68), "R": (196, 78, 56),
                 "G": (66, 136, 86)}
MANA_FILL = {"W": (250, 246, 214), "U": (172, 212, 242), "B": (176, 166, 168), "R": (242, 166, 134),
             "G": (156, 204, 156), "C": (206, 206, 212)}
# ---- AD1: every colour the interface draws lives here (Round AD1) ---------------------------------------------
# CHROME tokens are the umber/parchment interface colours: warm_palette.warm() of the old blue-grey value, which keeps each
# colour's lightness (so contrast ratios are unchanged). One token per role; two roles may share a value on purpose.
BACKDROP = (8, 5, 2)                    # behind a dialog (alpha added where it is used)
# Round AD2b: the full-screen moments (flow_screens.py)
VS_TEXT = (240, 206, 132)               # the big "VS" between the commanders
VS_SHADOW = (92, 22, 14)                # its dark-red shadow
VICTORY = (236, 196, 104)               # VICTORY
DEFEAT = (196, 52, 42)                  # DEFEAT (dried blood, brighter than PANEL_OPP so it reads on the dimmed table)
# Round UX1: the first-game tour (tour.py)
CLEAR = (0, 0, 0, 0)                    # a fully transparent hole (the tour's spotlight in its dimming layer)
KEYCAP_BG = (24, 14, 6)                 # a key name drawn as a key cap in the tour's captions ("Space", "F8")
KEYCAP_EDGE = (162, 145, 125)
BADGE_DARK = (25, 19, 15)                   # was (20, 20, 26)
BADGE_EFFECTS_BG = (50, 38, 29)             # was (34, 40, 56)
BADGE_EFFECTS_FG = (159, 143, 126)          # was (130, 146, 180)
BADGE_SLEEP_BG = (35, 19, 1)                # was (20, 20, 40)
BAR_BOTTOM = (33, 22, 12)                   # was (18, 24, 38)
BAR_TOP = (55, 39, 26)                      # was (32, 42, 64)
BF_MINE_BG = (44, 30, 20)                   # was (20, 34, 52)
BF_MINE_EDGE = (82, 64, 48)                 # was (48, 68, 100)
BODY_TEXT = (219, 213, 202)                 # was (206, 214, 228)
BTN_BOTTOM = (71, 53, 37)                   # was (44, 56, 82)
BTN_EDGE = (162, 145, 125)                  # was (128, 148, 186)
BTN_OFF_BOTTOM = (43, 35, 29)               # was (32, 36, 46)
BTN_OFF_EDGE = (73, 62, 54)                 # was (58, 64, 78)
BTN_OFF_FG = (127, 116, 105)                # was (110, 118, 134)
BTN_OFF_TOP = (54, 44, 37)                  # was (40, 46, 58)
BTN_TOP = (107, 88, 70)                     # was (74, 92, 124)
CHIP_BG = (52, 37, 26)                      # was (30, 40, 60)
CHIP_EDGE = (88, 70, 54)                    # was (60, 74, 100)
CHIP_HOT_BG = (103, 84, 67)                 # was (70, 88, 122)
CHIP_HOT_EDGE = (130, 112, 93)              # was (96, 116, 150)
CHOICE_BG = (62, 45, 31)                    # was (36, 48, 72)
CHOICE_EDGE = (119, 100, 82)                # was (84, 104, 140)
CHOICE_HOT_BG = (89, 70, 54)                # was (58, 74, 104)
CLOSE_BG = (24, 14, 6)                      # was (12, 16, 26)
DIALOG_BG = (40, 27, 18)                    # was (22, 30, 46)
DIALOG_EDGE = (131, 113, 94)                # was (96, 116, 158)
DOT_EDGE = (131, 112, 93)                   # was (100, 116, 146)
FIELD_BG = (24, 14, 6)                      # was (12, 16, 26)
FIELD_EDGE = (105, 87, 69)                  # was (74, 90, 122)
FIELD_EDGE_OFF = (101, 83, 65)              # was (70, 86, 118)
FRAME_DIM = (140, 128, 114)                 # was (120, 130, 150)
FX_INK = (22, 15, 9)                        # was (16, 16, 22)
HEADING_RULE = (91, 72, 56)                 # was (62, 76, 104)
HINT_LINE_TEXT = (188, 172, 151)            # was (150, 176, 214)
HINT_TEXT = (125, 109, 93)                  # was (100, 112, 136)
HOVER_BG = (37, 26, 17)                     # was (20, 28, 42)
HOVER_EDGE = (99, 80, 63)                   # was (70, 84, 112)
HUD_BAR_BG = (36, 23, 13)                   # was (18, 26, 42)
HUD_BAR_EDGE = (101, 83, 65)                # was (70, 86, 118)
INK_DARK = (22, 15, 9)                      # was (16, 16, 22)
KNOB_DARK = (22, 15, 9)                     # was (16, 16, 22)
LIST_BG = (30, 20, 11)                      # was (16, 22, 34)
LIST_EDGE = (81, 63, 47)                    # was (54, 66, 92)
LIST_ROW_BG = (44, 31, 23)                  # was (26, 34, 50)
LIST_ROW_SEL_BG = (52, 37, 26)              # was (30, 40, 60)
LIST_ROW_SEL_EDGE = (101, 83, 65)           # was (70, 86, 118)
LIST_SEL_BG = (75, 57, 41)                  # was (46, 60, 92)
LIST_TEXT = (214, 207, 196)                 # was (200, 208, 224)
LOG_BG = (40, 27, 18)                       # was (22, 30, 46)
LOG_DIVIDER = (81, 63, 47)                  # was (52, 66, 96)
LOG_EDGE = (81, 63, 47)                     # was (52, 66, 96)
LOG_TEXT_DIM = (152, 137, 120)              # was (128, 140, 164)
LOG_TEXT_NORMAL = (219, 213, 202)           # was (206, 214, 228)
LOG_ZONE = (171, 159, 143)                  # was (150, 160, 190)
MENU_CARD_BG = (37, 26, 17)                 # was (20, 28, 42)
MENU_EDGE = (86, 68, 52)                    # was (58, 72, 98)
MENU_ITEM_ACTIVE_BG = (53, 37, 24)          # was (28, 40, 62)
MENU_ITEM_BG = (40, 27, 18)                 # was (22, 30, 46)
MENU_SEARCH_BG = (30, 20, 11)               # was (16, 22, 34)
OVERLAY_BG = (29, 18, 8, 246)               # was (14, 20, 32, 246)
OVERLAY_EDGE = (81, 63, 47)                 # was (52, 66, 96)
PANEL_DEEP = (30, 20, 11)                   # was (16, 22, 34)
PANE_BG = (30, 20, 11)                      # was (16, 22, 34)
PHASE_ACTIVE_BG = (73, 54, 39)              # was (44, 58, 86)
PHASE_ACTIVE_EDGE = (127, 109, 89)          # was (92, 112, 150)
PHASE_ACTIVE_FG = (227, 221, 210)           # was (214, 222, 236)
PHASE_BG = (51, 37, 27)                     # was (30, 40, 58)
PHASE_EDGE = (79, 61, 45)                   # was (52, 64, 90)
PHASE_FG = (135, 119, 101)                  # was (108, 122, 148)
PROMPT_BG = (19, 10, 4)                     # was (8, 12, 20)
RADIO_BG = (93, 75, 58)                     # was (64, 78, 108)
RADIO_EDGE = (162, 145, 125)                # was (128, 148, 186)
ROW_BG = (62, 45, 31)                       # was (36, 48, 72)
ROW_EDGE = (113, 94, 76)                    # was (78, 98, 134)
ROW_HOT_BG = (97, 78, 61)                   # was (64, 82, 116)
SCROLLBAR = (123, 105, 86)                  # was (90, 108, 146)
SEARCH_BG = (29, 18, 8)                     # was (14, 20, 32)
SHADE_BG = (19, 10, 4, 200)                 # was (10, 12, 18, 200)
SOFT_TEXT = (206, 199, 187)                 # was (190, 200, 220)
STRIP_BG = (27, 18, 11)                     # was (16, 20, 30)
STRIP_TEXT = (222, 215, 205)                # was (215, 215, 225)
SWITCH_OFF_BG = (79, 63, 50)                # was (58, 66, 88)
SWITCH_OFF_EDGE = (139, 121, 101)           # was (110, 124, 158)
SWITCH_OFF_KNOB = (205, 197, 184)           # was (188, 198, 220)
TAG_BG = (24, 14, 6)                        # was (12, 16, 26)
TAG_EDGE = (141, 123, 103)                  # was (110, 126, 160)
TOP_RULE = (101, 83, 65)                    # was (70, 86, 118)

# SEMANTIC tokens carry game meaning (life gain/loss, log categories, particles, badges, notes, mana...). Same values as before AD1.
BADGE_EMBLEM_BG = (40, 34, 66)
BADGE_EMBLEM_FG = (150, 140, 230)
BADGE_INITIATIVE_BG = (62, 38, 14)
BADGE_INITIATIVE_FG = (240, 150, 60)
BADGE_MONARCH_BG = (58, 46, 10)
BADGE_RING_BG = (62, 22, 22)
BADGE_RING_FG = (232, 96, 96)
BANNER_BG = (60, 36, 14)
BANNER_ORANGE_BG = (58, 38, 20)
BANNER_RED_BG = (58, 30, 30)            # round 28d: "closed unexpectedly last time" banner on the deck screen
BTN_GOLD_BOTTOM = (196, 142, 44)
BTN_GOLD_EDGE = (255, 236, 170)
BTN_GOLD_FG = (32, 22, 6)
BTN_GOLD_TOP = (250, 214, 116)
CHOICE_ON_BG = (70, 88, 60)
FEED_MINE_BG = (66, 54, 22)
FEED_OPP_BG = (66, 42, 22)
FX_ABILITY = (90, 220, 170)
FX_ARRIVE = (200, 232, 255)
FX_TRIGGER = (190, 120, 255)
GLOW_GOLD = (200, 150, 40)
IMPRINT_BG = (44, 36, 18)
IMPRINT_TEXT = (214, 196, 150)
INK_GOLD = (30, 20, 8)
INK_GOLD_DARK = (30, 22, 8)
LIFE_GAIN_COLOUR = (120, 235, 140)
LIFE_LOSS_COLOUR = (255, 110, 110)
LIFE_MID_COLOUR = (240, 200, 120)
LOG_LAND = (120, 190, 130)
LOG_LIFE = (240, 130, 130)
LOG_MANA = (170, 150, 210)
LOG_STACK_RESOLVE = (150, 136, 92)
LOG_TEXT_BAD = (255, 128, 128)
LOG_TEXT_CARD = (255, 226, 150)
LOG_TEXT_GOOD = (130, 220, 140)
LOG_TEXT_ME = (128, 196, 255)
LOG_TEXT_OPP = (255, 170, 96)
NOTE_BG = (28, 26, 16)
NOTE_GREEN = (130, 220, 140)
NOTE_ORANGE = (255, 190, 110)
PARTICLE_GOLD = (230, 195, 90)
PARTICLE_GREEN = (120, 235, 140)
PARTICLE_PURPLE = (190, 120, 255)
PARTICLE_RED = (255, 90, 80)
PARTICLE_WHITE = (220, 235, 255)
PAY_BG = (16, 30, 26)
PAY_EDGE = (110, 200, 150)
PAY_TEXT = (150, 235, 185)
PICK_BG = (40, 36, 6)
PICK_HOT_BG = (70, 62, 8)
POISON_COLOUR = (150, 230, 120)
PROMPT_GOLD_BG = (30, 26, 18)
ROW_FIRST_BG = (58, 48, 26)
ROW_GOLD_BG = (40, 34, 24)
SHADOW_SOFT = (0, 0, 0, 90)
SLOT_BG = (30, 26, 14)
SLOT_EDGE = (110, 90, 46)
STATUS_PURPLE_BG = (74, 52, 130)
STATUS_PURPLE_EDGE = (196, 176, 250)
STATUS_SLEEP_TEXT = (170, 190, 240)
STATUS_TEAL_BG = (16, 110, 130)
STATUS_TEAL_EDGE = (150, 226, 240)
SWITCH_ON_BG = (52, 150, 96)
SWITCH_ON_EDGE = (130, 220, 165)
TAG_BLUE = (70, 130, 200)
TAG_ORANGE = (214, 122, 50)
TICK_INK = (20, 20, 20)
TRAY_BG = (30, 22, 8)

FONT_NAMES = "segoeui,helveticaneue,helvetica,arial,dejavusans,freesans"

_FONTS = {}
_ROUNDED = OrderedDict()                # key -> Surface, least recently used first (see remember / recall)
_ROUNDED_BYTES = [0]
_CACHE_BYTES = 192 * 1024 * 1024        # composed card pictures may use this much memory; the least recently used go first
_GLOWS = {}
_GRADIENTS = {}
_LAYERS = OrderedDict()                 # translucent rounded rectangles (panels): key -> Surface, reused every frame
_LAYER_LIMIT = 256


# ---- fonts (Round AD1) -----------------------------------------------------------------
# Two roles. BODY (Alegreya, SIL OFL) is for everything readable, and for every number. DISPLAY is for headings and big buttons only.
# The display face is AvQest (assets/fonts/AvQest.ttf, GemFonts, free under 1001fonts' FFC licence; Karl's choice 2026-10-01, an
# Exocet look-alike). It is NOT open source and is left out of the public source copy (tools/export_public.py), so when it is missing
# the display face is Cinzel Decorative Bold (SIL OFL) instead: FALLBACK_FILES. Karl re-checks AvQest's licence before a public release.
# Neither display face has real lowercase or plain digits, and both lack a few symbols. Until round 32 `pick_font` dropped a WHOLE
# string to the body twin when it had a digit or a missing glyph ("Turn 7", "Main 1", "AI 2" were all Alegreya). Now a display font
# is a DisplayFont: it draws each run of characters in the face that can draw it - digits and missing glyphs in the body twin (K3
# still holds: AvQest's 0 is slashed and its 1 is an I), everything else in the display face. A string with no letter at all
# ("175%", "+", "<") is still all body twin.
BODY_SCALE = 1.10                       # Alegreya's x-height is 0.452 em against Segoe UI's 0.5
DISPLAY_MIN_PX = 16                     # below this the display face is unreadable: the body font is used instead
FONT_DIR_NAME = "fonts"
FONT_FILES = {"body": "Alegreya-Regular.ttf", "bold": "Alegreya-Bold.ttf", "display": "AvQest.ttf"}
FALLBACK_FILES = {"display": "CinzelDecorative-Bold.ttf"}   # used when the FONT_FILES face is missing (the public source copy)
_TWINS = {}                             # display Font -> the body Font of the same size (see pick_font)
_FONT_NOTED = set()
_OK_CACHE = {}


def font_path(which):
    """The file used for `which`: FONT_FILES[which], or FALLBACK_FILES[which] when the first is not there."""
    path = os.path.join(paths.assets_dir(), FONT_DIR_NAME, FONT_FILES[which])
    spare = FALLBACK_FILES.get(which)
    if spare and not os.path.isfile(path):
        other = os.path.join(paths.assets_dir(), FONT_DIR_NAME, spare)
        if os.path.isfile(other):
            return other
    return path


def _note_missing(which, why):
    if which in _FONT_NOTED:
        return
    _FONT_NOTED.add(which)
    try:
        import crashlog
        crashlog.note("font file not used", f"{os.path.basename(font_path(which))}: {why}. Using the system font instead.")
    except Exception:
        pass


def _load(which, px):
    """The bundled font file at `px`, or None (noted once in the crash log) when it is missing or will not load."""
    path = font_path(which)
    try:
        return pygame.font.Font(path, px)
    except Exception as e:
        _note_missing(which, f"{type(e).__name__}: {e}")
        return None


def get_font(px, bold=False, role="body"):
    """The one font factory. role "body": Alegreya at round(px * BODY_SCALE), Bold when `bold`. role "display": the display
    face at exactly `px` (bold is ignored, the face is already heavy); under DISPLAY_MIN_PX it is the body font instead.
    A missing font file falls back to the system font (as before AD1), so a copy without assets/ still runs."""
    px = max(7, int(round(px)))
    key = (px, bool(bold), role)
    font = _FONTS.get(key)
    if font is not None:
        return font
    if role == "display":
        if px < DISPLAY_MIN_PX:
            font = get_font(px, bold, "body")
        else:
            face = _load("display", px)
            if face is None:
                font = pygame.font.SysFont(FONT_NAMES, px, bold=bool(bold))
            else:
                twin = get_font(px, bool(bold), "body")
                font = DisplayFont(face, twin)
                _TWINS[font] = twin
    else:
        font = _load("bold" if bold else "body", max(7, int(round(px * BODY_SCALE))))
        if font is None:
            font = pygame.font.SysFont(FONT_NAMES, px, bold=bool(bold))
    _FONTS[key] = font
    return font


_GLYPH_OK = {}
_NOTDEF = {}


def _picture(font, ch):
    surf = font.render(ch, False, (255, 255, 255))
    return (surf.get_size(), (pygame.image.tobytes if hasattr(pygame.image, "tobytes") else pygame.image.tostring)(surf, "RGBA"))


def _has_glyph(font, ch):
    """Does `font` draw `ch`? Font.metrics() says None for a missing glyph on some builds, but pygame-ce 2.5.8 returns the metrics
    of the font's "missing" box instead, so the test also compares the drawn picture with the picture of a character that no font has
    (U+FFFF). The answer is cached per character."""
    ok = _GLYPH_OK.get(ch)
    if ok is None and ch.isspace():
        ok = _GLYPH_OK[ch] = True        # a space draws nothing, like a "missing" box that is blank (Cinzel's is): always fine
    if ok is None:
        try:
            m = font.metrics(ch)
            if not m or m[0] is None:
                ok = False
            else:
                if id(font) not in _NOTDEF:
                    _NOTDEF[id(font)] = (font.metrics("\uffff"), _picture(font, "\uffff"))
                nd_metrics, nd_picture = _NOTDEF[id(font)]
                ok = not (m == nd_metrics and _picture(font, ch) == nd_picture)
        except Exception:
            ok = False
        _GLYPH_OK[ch] = ok
    return ok


def display_ok(text):
    """True when `text` can be set in the display font: no digit anywhere (K3: numbers are always the body font), at least one
    letter (a label that is only symbols, like the "+" and "-" buttons, stays in the body font: AvQest draws "+" as a cross
    pattee and "-" as a small bar), and a glyph for every character."""
    ok = _OK_CACHE.get(text)
    if ok is None:
        ok = not any(ch.isdigit() for ch in text) and (not text or any(ch.isalpha() for ch in text))
        if ok and text:
            font = get_font(DISPLAY_MIN_PX + 4, role="display")
            ok = all(_has_glyph(font, ch) for ch in set(text))
        if len(_OK_CACHE) > 4096:
            _OK_CACHE.clear()
        _OK_CACHE[text] = ok
    return ok


def pick_font(font, text):
    """`font`, or its body twin when `font` is a display font and `text` has no letter at all (round 32: a DisplayFont draws the
    digits and missing glyphs of any other text in its twin by itself)."""
    twin = _TWINS.get(font)
    if twin is None or any(ch.isalpha() for ch in text or ""):
        return font
    return twin


TWIN_CHARS = set("+-\u2212%")       # drawn in the body twin even inside a word ("A+", "A-"): AvQest's "+" is a cross pattee, its "-" a dot


class DisplayFont:
    """Round 32 (Karl, 3 Oct 2026: "Menus and UI elements should be in the Avqest font"): the display face, with its body twin for
    what the face can't or shouldn't draw - every digit (K3) and any character it has no glyph for - chosen per run of characters,
    so "Turn 7" is "Turn " in AvQest and "7" in Alegreya, on one baseline. size() measures the same mixed runs, so the layout code
    that fits labels with font.size() gets the width that is drawn. Everything else (get_height, get_ascent ...) is the face's,
    except the heights, which cover both faces."""

    def __init__(self, face, twin):
        self.face, self.twin = face, twin

    def __getattr__(self, name):
        return getattr(self.face, name)

    def _in_face(self, ch):
        return not ch.isdigit() and ch not in TWIN_CHARS and _has_glyph(self.face, ch)

    def runs(self, text):
        """[(text, font)] in order. Spaces go with the run before them; text with no letter is all twin."""
        text = text or ""
        if not any(ch.isalpha() for ch in text):
            return [(text, self.twin)] if text else []
        out, cur, cur_font = [], "", None
        for ch in text:
            f = cur_font if (ch.isspace() and cur_font is not None) else (self.face if self._in_face(ch) else self.twin)
            if f is not cur_font and cur:
                out.append((cur, cur_font))
                cur = ""
            cur_font = f
            cur += ch
        if cur:
            out.append((cur, cur_font))
        return out

    def size(self, text):
        runs = self.runs(text)
        if len(runs) == 1:
            return runs[0][1].size(runs[0][0])                # one face: exactly what that face says
        if not runs:
            return 0, self.get_height()
        return sum(f.size(t)[0] for t, f in runs), self.get_height()

    def get_height(self):
        """Room for both faces on one baseline: the taller ascent plus the deeper descent."""
        return self.get_ascent() + max(f.get_height() - f.get_ascent() for f in (self.face, self.twin))

    def get_linesize(self):
        return max(self.face.get_linesize(), self.twin.get_linesize())

    def get_ascent(self):
        return max(self.face.get_ascent(), self.twin.get_ascent())

    def render(self, text, antialias, color, background=None):
        runs = self.runs(text)
        if len(runs) == 1:
            return runs[0][1].render(text, antialias, color, background)      # one face: exactly what that face draws
        if not runs:
            return self.face.render("", antialias, color, background)
        base = self.get_ascent()
        w, h = self.size(text)
        surf = pygame.Surface((max(1, w), h), pygame.SRCALPHA)
        if background is not None:
            surf.fill(background)
        x = 0
        for t, f in runs:
            img = f.render(t, antialias, color)
            surf.blit(img, (x, base - f.get_ascent()))
            x += img.get_width()
        return surf


# ---- the table background (Round AD1) --------------------------------------------------------
BACKGROUND_FILES = {"graveyard": "graveyard.jpg", "cathedral": "cathedral.jpg", "citadel": "citadel.png", "ruins": "ruins.jpg"}
BG_DARKEN = (150, 150, 150)             # multiplied over the picture so cards and text stay the brightest things on screen
BG_VIGNETTE = 150                       # alpha of the black at the corners
_BG_RAW = {}                            # name -> the decoded picture (about 3.7 MB each); None when it would not load
_BG_SLOT = [None, None]                 # the ONE finished background: [(size, name), Surface]. Not in the card LRU: 33 MB at 4096x2019.
_BG_NOTED = set()
_GRAIN = [None]


def background_path(name):
    return os.path.join(paths.assets_dir(), "backgrounds", BACKGROUND_FILES[name])


def _bg_note(name, why):
    if name in _BG_NOTED:
        return
    _BG_NOTED.add(name)
    try:
        import crashlog
        crashlog.note("table background not used", f"{BACKGROUND_FILES.get(name, name)}: {why}. Showing the plain background instead.")
    except Exception:
        pass


def _bg_raw(name):
    if name not in _BG_RAW:
        try:
            img = pygame.image.load(background_path(name))
            _BG_RAW[name] = img.convert() if pygame.display.get_surface() else img
        except Exception as e:
            _bg_note(name, f"{type(e).__name__}: {e}")
            _BG_RAW[name] = None
    return _BG_RAW[name]


def _grain_tile():
    """A 512x512 speckle, made once from random.Random(7): about 1 dot per 30 px2, grey at random, alpha 14."""
    if _GRAIN[0] is None:
        import random
        rng = random.Random(7)
        tile = pygame.Surface((512, 512), pygame.SRCALPHA)
        for _ in range(512 * 512 // 30):
            g = rng.randint(0, 255)
            tile.set_at((rng.randrange(512), rng.randrange(512)), (g, g, g, 14))
        _GRAIN[0] = tile
    return _GRAIN[0]


def _vignette(size):
    """Black that is clear in the middle and BG_VIGNETTE alpha at the corners: a small radial ramp, smooth-scaled up."""
    w, h = size
    sw, sh = max(8, w // 16), max(8, h // 16)
    small = pygame.Surface((sw, sh), pygame.SRCALPHA)
    for y in range(sh):
        dy = (y + 0.5) / sh * 2 - 1
        for x in range(sw):
            dx = (x + 0.5) / sw * 2 - 1
            t = min(1.0, math.hypot(dx, dy) / math.sqrt(2))
            t = max(0.0, (t - 0.35) / 0.65)
            small.set_at((x, y), (0, 0, 0, int(BG_VIGNETTE * t * t)))
    return pygame.transform.smoothscale(small, (w, h))


def _build_background(size, name):
    raw = _bg_raw(name)
    if raw is None:
        return None
    w, h = size
    rw, rh = raw.get_size()
    k = max(w / rw, h / rh)                                       # cover: fill the window, crop the overflow from the centre
    sw, sh = max(w, int(math.ceil(rw * k))), max(h, int(math.ceil(rh * k)))
    scaled = pygame.transform.smoothscale(raw, (sw, sh))
    out = pygame.Surface((w, h))
    out.blit(scaled, ((w - sw) // 2, (h - sh) // 2))
    out.fill(BG_DARKEN, special_flags=pygame.BLEND_RGB_MULT)
    out.blit(_vignette((w, h)), (0, 0))
    tile = _grain_tile()
    for ty in range(0, h, 512):
        for tx in range(0, w, 512):
            out.blit(tile, (tx, ty))
    return out.convert() if pygame.display.get_surface() else out


def table_background(size, name):
    """The picture behind the table, deck screen and resume screen: one of BACKGROUND_FILES scaled to cover `size`, darkened,
    vignetted and grained (baked in once); "plain" is the old gradient. A missing or broken file gives "plain" (noted once in the
    crash log). The last result is kept, so a frame is one blit."""
    size = (int(size[0]), int(size[1]))
    key = (size, name)
    if _BG_SLOT[0] == key:
        return _BG_SLOT[1]
    surf = _build_background(size, name) if name in BACKGROUND_FILES else None
    if surf is None:
        surf = gradient(size, BG_TOP, BG_BOTTOM)
    _BG_SLOT[0], _BG_SLOT[1] = key, surf
    return surf


def wrap_text(text, font, width):
    """Lines no wider than `width` px (explicit newlines kept)."""
    lines = []
    for paragraph in (text or "").split("\n"):
        line = ""
        for word in paragraph.split(" "):
            trial = f"{line} {word}".strip()
            if line and font.size(trial)[0] > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def clip_text(text, font, width):
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text + "..."


_TEXT_CACHE = {}                 # (font id, text, colour) -> rendered picture; Alegreya costs ~5x the old fallback font per render (Round AD1)
_TEXT_CACHE_MAX = 1500


def _rendered(font, s, colour):
    key = (id(font), s, colour)
    img = _TEXT_CACHE.get(key)
    if img is None:
        img = font.render(s, True, colour)
        if len(_TEXT_CACHE) >= _TEXT_CACHE_MAX:
            _TEXT_CACHE.clear()
        _TEXT_CACHE[key] = img
    return img


def draw_text(screen, s, x, y, font, colour=TEXT, anchor="topleft", shadow=False):
    font = pick_font(font, s)
    img = _rendered(font, s, colour)
    rect = img.get_rect()
    setattr(rect, anchor, (x, y))
    if shadow:
        sh = _rendered(font, s, SHADOW)
        screen.blit(sh, rect.move(1, 1))
    screen.blit(img, rect)
    return rect


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(size, top, bottom):
    key = (size, top, bottom)
    surf = _GRADIENTS.get(key)
    if surf is None:
        w, h = size
        surf = pygame.Surface((max(1, w), max(1, h)))
        for y in range(h):
            pygame.draw.line(surf, lerp(top, bottom, y / max(1, h - 1)), (0, y), (w, y))
        if len(_GRADIENTS) > 20:
            _GRADIENTS.clear()
        _GRADIENTS[key] = surf
    return surf


# ---- Round AD2: stone frames instead of rounded bubbles -------------------------------------------------------------------
FRAME_STYLE = True              # panels, dialogs and buttons get square corners, a bevel and (big ones) gold corner studs
FRAME_RADIUS = 2
STUD_MIN = (110, 56)            # a framed panel at least this big gets the four corner studs
BUTTON_RADIUS = 3


def framed(rect, radius):
    """True for a panel that is drawn as a stone frame: anything with rounded corners that is not a pill (a pill - switches,
    chips, badges - has a radius of about half its height and keeps its shape)."""
    return FRAME_STYLE and radius > FRAME_RADIUS and radius < min(rect.w, rect.h) // 2 - 1


def bevel(surface, rect, edge, alpha=255):
    """Light top and left, dark bottom and right: a 1 px bevel just inside `rect`'s outline."""
    light, dark = lerp(edge, WHITE, 0.30), lerp(edge, SHADOW, 0.60)
    r = pygame.Rect(rect).inflate(-2, -2)
    if r.w < 4 or r.h < 4:
        return
    a = (alpha,) if alpha < 255 else ()
    pygame.draw.line(surface, light + a, (r.left, r.top), (r.right - 1, r.top))
    pygame.draw.line(surface, light + a, (r.left, r.top), (r.left, r.bottom - 1))
    pygame.draw.line(surface, dark + a, (r.left, r.bottom - 1), (r.right - 1, r.bottom - 1))
    pygame.draw.line(surface, dark + a, (r.right - 1, r.top), (r.right - 1, r.bottom - 1))


def studs(surface, rect, alpha=255):
    r = pygame.Rect(rect)
    if r.w < STUD_MIN[0] or r.h < STUD_MIN[1]:
        return
    s = 5 if r.w < 300 else 6
    a = (alpha,) if alpha < 255 else ()
    for x, y in ((r.left + 2, r.top + 2), (r.right - 2 - s, r.top + 2), (r.left + 2, r.bottom - 2 - s), (r.right - 2 - s, r.bottom - 2 - s)):
        pygame.draw.rect(surface, GOLD_DARK + a, (x, y, s, s))
        pygame.draw.line(surface, GOLD + a, (x, y), (x + s - 1, y))


CARD_CORNER = 0.048             # a real card's corner radius, as a share of its width (about 3 mm on 63 mm)


def card_in_frame(screen, pic, rect, key=None, alpha=255, edge=None, width=2):
    """Round AD2b: a card picture with its corners rounded like the real card (Scryfall's scans have square corners, so the
    white or black of the scan showed there), sitting in a stone frame with a gold edge. Only the corners outside the card
    are cut; the copyright line and the artist name are untouched. `key` caches the rounded picture."""
    rect = pygame.Rect(rect)
    radius = max(3, int(rect.w * CARD_CORNER))
    rounded = recall(("cardframe", key, rect.size)) if key is not None else None
    if rounded is None:
        rounded = rounded_image(pic, rect.w, rect.h, radius)
        if key is not None:
            remember(("cardframe", key, rect.size), rounded)
    pad = max(4, int(rect.w * 0.03))
    frame = rect.inflate(2 * pad, 2 * pad)
    shadowed(screen, frame, 4, max(3, pad), int(150 * alpha / 255))
    round_rect(screen, frame, PANEL_DEEP, 12, width, edge or GOLD_DARK, alpha=None if alpha >= 255 else alpha)
    if alpha < 255:
        rounded = rounded.copy()
        rounded.set_alpha(alpha)
    screen.blit(rounded, rect)
    return frame


def round_rect(screen, rect, colour, radius=10, border=0, border_colour=None, alpha=None):
    """Filled rounded rectangle, optionally translucent and outlined. Round AD2: a panel (not a pill) is drawn as a stone frame -
    square corners, a bevel inside its outline and, when it is big enough, gold corner studs."""
    rect = pygame.Rect(rect)
    if rect.w < 2 or rect.h < 2:
        return
    frame = framed(rect, radius) and bool(border and border_colour)
    if framed(rect, radius):
        radius = FRAME_RADIUS
    if alpha is not None and alpha < 255:
        key = (rect.size, tuple(colour), radius, border, tuple(border_colour) if border_colour else None, alpha, frame)
        layer = _LAYERS.get(key)
        if layer is None:                                   # made once per size/colour, not once per frame
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(layer, (*colour, alpha), layer.get_rect(), border_radius=radius)
            if border and border_colour:
                pygame.draw.rect(layer, (*border_colour, min(255, alpha + 60)), layer.get_rect(), border, border_radius=radius)
            if frame:
                bevel(layer, layer.get_rect(), border_colour, min(255, alpha + 60))
                studs(layer, layer.get_rect(), min(255, alpha + 60))
            _LAYERS[key] = layer
            if len(_LAYERS) > _LAYER_LIMIT:
                _LAYERS.popitem(last=False)
        else:
            _LAYERS.move_to_end(key)
        screen.blit(layer, rect)
        return
    pygame.draw.rect(screen, colour, rect, border_radius=radius)
    if border and border_colour:
        pygame.draw.rect(screen, border_colour, rect, border, border_radius=radius)
    if frame:
        bevel(screen, rect, border_colour)
        studs(screen, rect)


def glow(screen, rect, colour, radius=8, width=3, layers=4):
    """A soft coloured halo just outside `rect` (used for 'you can act with this')."""
    rect = pygame.Rect(rect)
    key = (rect.size, colour, radius, width, layers)
    surf = _GLOWS.get(key)
    if surf is None:
        pad = width * layers
        surf = pygame.Surface((rect.w + pad * 2, rect.h + pad * 2), pygame.SRCALPHA)
        for i in range(layers, 0, -1):
            a = int(200 * (1 - i / (layers + 1)) ** 2) + 20
            r = pygame.Rect(pad - i, pad - i, rect.w + 2 * i, rect.h + 2 * i)
            pygame.draw.rect(surf, (*colour, a), r, width, border_radius=radius + i)
        if len(_GLOWS) > 200:
            _GLOWS.clear()
        _GLOWS[key] = surf
    pad = width * layers
    screen.blit(surf, (rect.x - pad, rect.y - pad))


def rounded_image(img, w, h, radius):
    """Card art scaled to (w, h) with rounded corners (a fresh Surface with per-pixel alpha)."""
    scaled = pygame.transform.smoothscale(img, (w, h)).convert_alpha()
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
    scaled.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return scaled


_SHADOWS = {}


def shadowed(screen, rect, radius=8, offset=3, alpha=90):
    key = (rect.w, rect.h, radius, alpha)
    layer = _SHADOWS.get(key)
    if layer is None:
        layer = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        pygame.draw.rect(layer, (0, 0, 0, alpha), layer.get_rect(), border_radius=radius)
        if len(_SHADOWS) > 300:
            _SHADOWS.clear()
        _SHADOWS[key] = layer
    screen.blit(layer, (rect.x + offset, rect.y + offset))


# ---- mana symbols ---------------------------------------------------------------------

def parse_cost(cost):
    """'{2}{G}{G}' -> ['2', 'G', 'G'];  '{W/U}' -> ['W/U']."""
    return re.findall(r"\{([^}]*)\}", cost or "")


# Round MANA1: real mana symbols from the Mana font (Andrew Gioia, https://mana.andrewgioia.com, v1.18.0; the font is SIL OFL 1.1,
# assets/fonts/mana.ttf). The codepoints below come from its css/mana.css (MIT licence). The symbols themselves are Wizards of the
# Coast's, used under the Fan Content Policy like the card pictures. A symbol the table doesn't have (a number above 20, anything
# new) - or no font file - falls back to the old letter in a circle.
MANA_FONT_FILE = "mana.ttf"
MANA_GLYPHS = {"W": "\ue600", "U": "\ue601", "B": "\ue602", "R": "\ue603", "G": "\ue604", "C": "\ue904", "S": "\ue619",
               "X": "\ue615", "Y": "\ue616", "Z": "\ue617", "T": "\ue61a", "Q": "\ue61b", "E": "\ue907", "P": "\ue618",
               "CHAOS": "\ue61d", "1/2": "\ue902", "\u221e": "\ue903", "0": "\ue605", "1": "\ue606", "2": "\ue607",
               "3": "\ue608", "4": "\ue609", "5": "\ue60a", "6": "\ue60b", "7": "\ue60c", "8": "\ue60d", "9": "\ue60e",
               "10": "\ue60f", "11": "\ue610", "12": "\ue611", "13": "\ue612", "14": "\ue613", "15": "\ue614", "16": "\ue62a",
               "17": "\ue62b", "18": "\ue62c", "19": "\ue62d", "20": "\ue62e"}
MANA_INK = (24, 24, 30)                 # the glyph on a coloured circle
MANA_EDGE = (30, 30, 36)
MANA_SHADOW = (20, 20, 24)
GLYPH_SCALE = 1.25                      # glyph em size / circle radius (Mana's own .ms-cost draws the glyph at about this)
HALF_SCALE = 0.62                       # a hybrid's two half glyphs, relative to a full one
_MANA_FONTS = {}


def mana_font(px):
    """The Mana font at px (cached), or None when the file is missing or unreadable (noted once in crash_log.txt)."""
    px = max(6, int(px))
    if px in _MANA_FONTS:
        return _MANA_FONTS[px]
    path = os.path.join(paths.assets_dir(), FONT_DIR_NAME, MANA_FONT_FILE)
    font = None
    if os.path.isfile(path):
        try:
            font = pygame.font.Font(path, px)
        except (OSError, pygame.error) as e:
            _note_mana(f"{e}")
    else:
        _note_mana("the file is missing")
    _MANA_FONTS[px] = font
    return font


def _note_mana(why):
    if "mana" in _FONT_NOTED:
        return
    _FONT_NOTED.add("mana")
    try:
        import crashlog
        crashlog.note("font file not used", f"{MANA_FONT_FILE}: {why}. Mana symbols are drawn as letters instead.")
    except Exception:
        pass


def mana_parts(sym):
    """'W' -> ('W',); 'W/U' -> ('W', 'U'); 'W/P' -> ('W', 'P'); 'G/U/P' -> ('G', 'U', 'P'); '1/2' stays one symbol."""
    s = (sym or "").strip().upper()
    if s in MANA_GLYPHS:
        return (s,)
    return tuple(p for p in s.split("/") if p)


def _fill(part):
    return MANA_FILL.get(part, MANA_FILL["C"]) if part in "WUBRG" and part else MANA_FILL["C"]


def _glyph(font, ch, size_px, colour):
    """A glyph cropped to its own ink, so it can be centred by what is drawn rather than by the font's line box."""
    surf = font.render(ch, True, colour)
    box = surf.get_bounding_rect()
    return surf.subsurface(box).copy() if box.w and box.h else None


def _mana_surface(sym, r):
    parts = mana_parts(sym)
    d = 2 * r + 2
    surf = pygame.Surface((d, d), pygame.SRCALPHA)
    c = (r, r)
    known = parts and all(p in MANA_GLYPHS for p in parts) and len(parts) <= 3
    font = mana_font(r * GLYPH_SCALE) if known else None
    if font is None:
        return None
    phyrexian = len(parts) >= 2 and parts[-1] == "P"
    colours = [p for p in parts if p != "P"] or ["C"]
    pygame.draw.circle(surf, MANA_SHADOW, (r + 1, r + 1), r)
    if len(colours) == 1:
        pygame.draw.circle(surf, _fill(colours[0]), c, r)
        g = _glyph(font, MANA_GLYPHS["P" if phyrexian else colours[0]], 0, MANA_INK)
        if g is not None:
            surf.blit(g, g.get_rect(center=c))
    else:                                                     # hybrid: split on the diagonal, top-left the first colour
        pygame.draw.circle(surf, _fill(colours[0]), c, r)
        half = pygame.Surface((d, d), pygame.SRCALPHA)
        pygame.draw.circle(half, _fill(colours[1]), c, r)
        pygame.draw.polygon(half, (0, 0, 0, 0), [(0, 0), (d, 0), (0, d)])
        surf.blit(half, (0, 0))
        small = mana_font(r * GLYPH_SCALE * HALF_SCALE)
        for i, part in enumerate(colours[:2]):
            g = _glyph(small, MANA_GLYPHS["P" if phyrexian else part], 0, MANA_INK)
            if g is not None:
                off = round(r * 0.42)
                surf.blit(g, g.get_rect(center=(r - off, r - off) if i == 0 else (r + off, r + off)))
    pygame.draw.circle(surf, MANA_EDGE, c, r, 1)
    return surf


def mana_symbol(screen, sym, cx, cy, r):
    r = max(3, int(r))
    key = ("mana", sym, r)
    surf = recall(key)
    if surf is None:
        surf = _mana_surface(sym, r)
        if surf is not None:
            remember(key, surf)
    if surf is not None:
        screen.blit(surf, (cx - r, cy - r))
        return
    colour = MANA_FILL.get(sym[:1], MANA_FILL["C"]) if not sym[:1].isdigit() else MANA_FILL["C"]      # the old letter circle
    pygame.draw.circle(screen, MANA_SHADOW, (cx + 1, cy + 1), r)
    pygame.draw.circle(screen, colour, (cx, cy), r)
    pygame.draw.circle(screen, MANA_EDGE, (cx, cy), r, 1)
    label = sym if len(sym) <= 2 else sym[0]
    font = get_font(max(8, int(r * 1.35)), True)
    draw_text(screen, label, cx, cy, font, MANA_INK, "center")


def mana_cost(screen, cost, right, y, r):
    """Draw a cost right-aligned at x=`right`; returns the left edge."""
    syms = parse_cost(cost)
    x = right
    for sym in reversed(syms):
        x -= r * 2
        mana_symbol(screen, sym, x + r, y + r, r)
        x -= 2
    return x


# ---- cards that have no image ---------------------------------------------------------

def frame_colour(card):
    cols = card.get("colors") or []
    if len(cols) >= 2:
        return (200, 168, 84)
    if len(cols) == 1:
        return FRAME_COLOURS[cols[0]]
    if card.get("isLand"):
        return (140, 116, 88)
    return (150, 160, 172)


def card_back(w, h, radius):
    key = ("back", w, h)
    surf = recall(key)
    if surf is None:
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (74, 46, 26), surf.get_rect(), border_radius=radius)
        inner = surf.get_rect().inflate(-max(4, w // 10), -max(4, w // 10))
        pygame.draw.rect(surf, (52, 22, 20), inner, border_radius=max(2, radius - 2))
        pygame.draw.ellipse(surf, (176, 132, 60), pygame.Rect(0, 0, w * 0.55, w * 0.55).move(w * 0.225, h / 2 - w * 0.275), 2)
        pygame.draw.ellipse(surf, (120, 90, 44), pygame.Rect(0, 0, w * 0.3, w * 0.3).move(w * 0.35, h / 2 - w * 0.15), 2)
        remember(key, surf)
    return surf


def card_face(card, w, h, radius, show_pt=False):
    """A drawn stand-in for a card whose picture is not available (tokens, or art still loading)."""
    key = ("face", card.get("name"), card.get("cost"), w, h, show_pt)
    surf = recall(key)
    if surf is not None:
        return surf
    frame = frame_colour(card)
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(surf, (18, 18, 20), surf.get_rect(), border_radius=radius)
    body = surf.get_rect().inflate(-max(3, w // 22), -max(3, w // 22))
    pygame.draw.rect(surf, frame, body, border_radius=max(2, radius - 1))
    pad = max(3, w // 18)
    name_font = get_font(max(8, h * 0.075), True)
    small = get_font(max(7, h * 0.058))
    tiny = get_font(max(7, h * 0.05))
    name = card.get("name", "?")
    # title bar
    bar = pygame.Rect(body.x + pad, body.y + pad, body.w - 2 * pad, int(h * 0.11))
    pygame.draw.rect(surf, lerp(frame, (255, 255, 255), 0.55), bar, border_radius=4)
    cost_right = bar.right - 2
    if h >= 90:
        cost_r = max(5, int(bar.h * 0.36))
        cost_right = mana_cost(surf, card.get("cost", ""), bar.right - 2, bar.centery - cost_r, cost_r)
    draw_text(surf, clip_text(name, name_font, max(10, cost_right - bar.x - 6)), bar.x + 4, bar.centery, name_font,
              (20, 20, 24), "midleft")
    # art box
    art = pygame.Rect(bar.x, bar.bottom + pad, bar.w, int(h * 0.36))
    pygame.draw.rect(surf, lerp(frame, (0, 0, 0), 0.45), art, border_radius=3)
    pygame.draw.rect(surf, lerp(frame, (0, 0, 0), 0.25), art.inflate(-6, -6), border_radius=3)
    # type line
    tl = pygame.Rect(bar.x, art.bottom + pad, bar.w, int(h * 0.085))
    pygame.draw.rect(surf, lerp(frame, (255, 255, 255), 0.55), tl, border_radius=4)
    draw_text(surf, clip_text(card.get("type", ""), small, tl.w - 8), tl.x + 4, tl.centery, small, (20, 20, 24), "midleft")
    # rules text
    box = pygame.Rect(bar.x, tl.bottom + pad, bar.w, body.bottom - tl.bottom - pad * 2)
    pygame.draw.rect(surf, (238, 232, 216), box, border_radius=3)
    if h >= 150:
        y = box.y + 3
        for line in wrap_text(card.get("text", ""), tiny, box.w - 8):
            if y + tiny.get_height() > box.bottom - 2:
                break
            draw_text(surf, line, box.x + 4, y, tiny, (30, 30, 34))
            y += tiny.get_height()
    if show_pt and card.get("power") is not None:
        pt = pygame.Rect(0, 0, int(w * 0.28), int(h * 0.075))
        pt.bottomright = (body.right - pad, body.bottom - pad)
        pygame.draw.rect(surf, (238, 232, 216), pt, border_radius=4)
        pygame.draw.rect(surf, (30, 30, 34), pt, 1, border_radius=4)
        draw_text(surf, f"{card['power']}/{card['toughness']}", pt.centerx, pt.centery, small, (20, 20, 24), "center")
    return remember(key, surf)


def _bytes(surf):
    return surf.get_width() * surf.get_height() * surf.get_bytesize()


def remember(key, surf):
    """Keyed cache for composed pictures (rounded card images at a size, badges, rotations). Bounded by MEMORY, not by count: when it
    would pass _CACHE_BYTES the least recently used pictures are dropped one at a time (the old version cleared everything at once,
    which rebuilt every card on screen in one frame)."""
    old = _ROUNDED.pop(key, None)
    if old is not None:
        _ROUNDED_BYTES[0] -= _bytes(old)
    _ROUNDED[key] = surf
    _ROUNDED_BYTES[0] += _bytes(surf)
    while _ROUNDED_BYTES[0] > _CACHE_BYTES and len(_ROUNDED) > 1:
        _k, gone = _ROUNDED.popitem(last=False)
        _ROUNDED_BYTES[0] -= _bytes(gone)
    return surf


def recall(key):
    surf = _ROUNDED.get(key)
    if surf is not None:
        _ROUNDED.move_to_end(key)
    return surf


def cache_megabytes():
    return _ROUNDED_BYTES[0] / (1024 * 1024)


def clear_caches():
    _ROUNDED.clear()
    _ROUNDED_BYTES[0] = 0
    _LAYERS.clear()
    _GLOWS.clear()
    _SHADOWS.clear()


# ---- text with mana symbols in it ---------------------------------------------------------

_SYMBOL = re.compile(r"(\{[^}]*\})")


def rich_width(text, font, r):
    w = 0
    for part in _SYMBOL.split(text):
        if part.startswith("{") and part.endswith("}"):
            w += r * 2 + 2
        else:
            w += font.size(part)[0]
    return w


def draw_rich(screen, text, x, y, font, colour=TEXT, r=None):
    """One line of text where {G}, {2}, {T} ... are drawn as little mana symbols. y is the vertical centre."""
    r = r or max(6, int(font.get_height() * 0.42))
    for part in _SYMBOL.split(text):
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            mana_symbol(screen, part[1:-1], x + r, y, r)
            x += r * 2 + 2
        else:
            img = pick_font(font, part).render(part, True, colour)
            screen.blit(img, img.get_rect(midleft=(x, y)))
            x += img.get_width()
    return x


# ---- the settings cog ---------------------------------------------------------------------

_COGS = {}


def cog(diameter, colour, hole=None, teeth=8):
    """A smooth gear picture `diameter` px wide (drawn 4x as big and scaled down; cached)."""
    key = (diameter, colour, hole, teeth)
    surf = _COGS.get(key)
    if surf is None:
        big = max(8, diameter) * 4
        img = pygame.Surface((big, big), pygame.SRCALPHA)
        c, r_out, r_in = big / 2, big / 2 * 0.98, big / 2 * 0.74
        pts = []
        for i in range(teeth):
            a0 = 2 * math.pi * i / teeth
            step = 2 * math.pi / teeth
            for frac, rad in ((-0.24, r_in), (-0.15, r_out), (0.15, r_out), (0.24, r_in)):
                a = a0 + frac * step * 2
                pts.append((c + rad * math.cos(a), c + rad * math.sin(a)))
        pygame.draw.polygon(img, colour, pts)
        pygame.draw.circle(img, colour, (c, c), r_in * 1.02)
        pygame.draw.circle(img, (0, 0, 0, 0), (c, c), big / 2 * 0.30)               # the hole in the middle
        surf = pygame.transform.smoothscale(img, (max(8, diameter), max(8, diameter)))
        if len(_COGS) > 40:
            _COGS.clear()
        _COGS[key] = surf
    return surf


def draw_cog(screen, cx, cy, diameter, colour=WHITE):
    surf = cog(int(diameter), tuple(colour))
    screen.blit(surf, surf.get_rect(center=(cx, cy)))


def draw_crown(screen, rect, colour):
    """A little filled crown (three points) that fills `rect` - the monarch's mark."""
    x, y, w, h = rect
    pts = [(0, 1), (0, .28), (.25, .58), (.5, 0), (.75, .58), (1, .28), (1, 1)]
    pygame.draw.polygon(screen, colour, [(x + px * (w - 1), y + py * (h - 1)) for px, py in pts])
