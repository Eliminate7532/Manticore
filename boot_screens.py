# SPDX-License-Identifier: GPL-3.0-or-later
"""
boot_screens.py - Round AD2: what the program shows before the deck screen.

    the studio splash (assets/splash/studio.jpg, 2.2 s, a click or a key skips it)
      -> the title: the picture alone (assets/splash/title.png, the manticore on its plinth) and "Press any key"
      -> the main menu over a dimmed copy of it: Play / Continue / Settings / Credits / Quit
         Play = the deck screen; Continue = Resume last game (greyed when there is none, or it can't be resumed);
         Settings = the cog pop-up; Credits = Licenses and credits; Quit closes the program.

Only main() starts here (ForgeTable.open_boot); tests and the --deck command line go straight to their own screens. The deck
screen's Esc (with no game running) comes back to the main menu (not to the picture again). Each picture is shown whole, at its own
proportions, as large as the window allows (round 31; it used to be cropped to cover the window), with a blurred, darkened copy of
it filling any room left at the sides; the menu stage dims the picture so the words are readable.
"""
import math
import os
import time

import pygame

import gfx
import paths
from gfx import draw_text, get_font

SPLASH_SECONDS = 2.2
FADE = 0.35
FILES = {"studio": "studio.jpg", "title": "title.png"}
FOCUS_Y = {"title": 0.85}        # the blurred fill behind the picture: how much of its cropped height comes off the TOP
BACKDROP_DIM = 80                # that fill is multiplied by this (of 255): it frames the picture without competing with it
FEATHER = 0.07                   # the picture's edges fade into the fill over this share of its width (or height)
MENU_DIM = 105                   # the title picture is multiplied by this (of 255) behind the menu
PROMPT = "Press any key"
ITEMS = (("play", "Play"), ("continue", "Continue"), ("settings", "Settings"), ("credits", "Credits"), ("quit", "Quit"))
_PICS = {}                       # (name, size) -> Surface (one size kept per picture)


def splash_path(name):
    return os.path.join(paths.assets_dir(), "splash", FILES[name])


def fit_rect(picture_size, size):
    """Round 31 (title fix): where the whole picture goes in a window of `size` - as large as fits, its own proportions kept,
    centred. A window wider than the picture leaves room left and right; a taller one, above and below."""
    pw, ph = picture_size
    w, h = size
    s = min(w / pw, h / ph)
    fw, fh = min(w, max(1, round(pw * s))), min(h, max(1, round(ph * s)))
    return pygame.Rect((w - fw) // 2, (h - fh) // 2, fw, fh)


def _cover(raw, size, focus_y):
    """`raw` scaled (the same both ways) to cover `size`, cropped; focus_y = how much of the spare height comes off the top."""
    w, h = size
    s = max(w / raw.get_width(), h / raw.get_height())
    scaled = pygame.transform.smoothscale(raw, (max(1, round(raw.get_width() * s)), max(1, round(raw.get_height() * s))))
    surf = pygame.Surface(size)
    surf.blit(scaled, ((w - scaled.get_width()) // 2, -int((scaled.get_height() - h) * focus_y)))
    return surf


def _feather(pic, sides, band):
    """A copy of `pic` whose edges fade out over `band` px - left and right ("lr") or top and bottom ("tb")."""
    out = pygame.Surface(pic.get_size(), pygame.SRCALPHA)
    out.blit(pic, (0, 0))
    n = out.get_width() if sides == "lr" else out.get_height()
    band = max(1, min(band, n // 2))
    line = pygame.Surface((n, 1) if sides == "lr" else (1, n), pygame.SRCALPHA)
    for i in range(n):
        d = min(i, n - 1 - i)
        a = 255 if d >= band else int(255 * (d / band) ** 1.5)
        line.set_at((i, 0) if sides == "lr" else (0, i), (255, 255, 255, a))
    out.blit(pygame.transform.scale(line, out.get_size()), (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def picture(name, size):
    """The splash picture for a window of `size`; None when the file is missing (the screen is then plain).
    Round 31 (title fix, Karl 3 Oct: "fix the aspect ratio of the background image"): the WHOLE picture, at its own proportions
    (fit_rect). It used to be scaled to cover the window and cropped: on a wide screen (4096x2160, 1.9:1) that cut off the top
    fifth of the 1.49:1 title art - the wings and the head - and put the lettering under the menu. Where the picture doesn't
    reach (the sides of a wide window, above and below a tall one) a blurred, darkened copy of it fills in, and the picture's
    edges fade into that."""
    key = (name, size)
    if key in _PICS:
        return _PICS[key]
    try:
        raw = pygame.image.load(splash_path(name))
    except (pygame.error, FileNotFoundError, OSError):
        surf = None
    else:
        w, h = size
        r = fit_rect(raw.get_size(), size)
        fitted = pygame.transform.smoothscale(raw, r.size)
        if r.w >= w - 1 and r.h >= h - 1:                                 # the window has the picture's own shape
            surf = pygame.Surface(size)
            surf.blit(fitted, r.topleft)
        else:
            small = pygame.transform.smoothscale(raw, (max(1, raw.get_width() // 24), max(1, raw.get_height() // 24)))
            surf = _cover(small, size, FOCUS_Y.get(name, 0.5))           # tiny, then large: a soft blur
            surf.fill((BACKDROP_DIM,) * 3, special_flags=pygame.BLEND_RGB_MULT)
            sides = "lr" if r.w < w - 1 else "tb"
            surf.blit(_feather(fitted, sides, int((r.w if sides == "lr" else r.h) * FEATHER)), r.topleft)
        if pygame.display.get_surface() is not None:
            surf = surf.convert()
    for k in [k for k in _PICS if k[0] == name or (k[0] == "dim" and k[1] == name)]:
        del _PICS[k]
    _PICS[key] = surf
    return surf


def dimmed(name, size):
    """The picture darkened for the menu to sit on; None when the picture is missing. Cached with its picture."""
    pic = picture(name, size)
    if pic is None:
        return None
    key = ("dim", name, size)
    surf = _PICS.get(key)
    if surf is None:
        surf = pic.copy()
        surf.fill((MENU_DIM,) * 3, special_flags=pygame.BLEND_RGB_MULT)
        _PICS[key] = surf
    return surf


def window_icon():
    try:
        return pygame.image.load(os.path.join(paths.assets_dir(), "splash", "icon.png"))
    except (pygame.error, FileNotFoundError, OSError):
        return None


class BootFlow:
    def __init__(self, splash=True):
        self.stage = "splash" if splash else "menu"       # splash -> title (picture alone) -> menu; coming back from the deck screen: menu
        self.t0 = time.monotonic()
        self.focus = 0
        self.heard_focus = 0                             # round AU1: a soft sound when the highlight moves to another item
        self.sound_done = False                          # round AU1: the splash's sound has played
        self.buttons = []
        import version
        self.version = version.short()                   # once: it fingerprints the program's files
        self.update = None                               # Round 30: update_screens.UpdateNotice, made at the first draw

    def moving(self, now):
        busy = self.update is not None and self.update.busy()          # Round 30: the download's progress bar
        return self.stage in ("splash", "title") or now - self.t0 < FADE + 0.05 or busy

    def tick(self, now):
        if self.stage == "splash" and now - self.t0 >= SPLASH_SECONDS:
            self.to_title(now)

    def to_title(self, now=None):
        self.stage = "title"
        self.t0 = time.monotonic() if now is None else now

    def to_menu(self, now=None):
        self.stage = "menu"
        self.t0 = time.monotonic() if now is None else now

    # ---- what each item does --------------------------------------------------------------------------------------------
    def enabled(self, gui, name):
        if name == "continue":
            return gui.can_continue()
        return True

    def choose(self, gui, name):
        if not self.enabled(gui, name):
            return
        gui.play_cue("ui.menu_select")                  # round AU1: heavier than a table click
        if name == "play":
            gui.boot = None
            gui.open_menu()
        elif name == "continue":
            gui.boot = None
            gui.resume_last_game()
        elif name == "settings":
            import forge_settings as fset
            gui.overlay = fset.SettingsPopup()
        elif name == "credits":
            gui.open_licenses()
        elif name == "quit":
            gui.running = False

    # ---- input ----------------------------------------------------------------------------------------------------------
    def click(self, gui, pos, button):
        if self.stage == "splash":
            self.to_title()
            return
        if self.stage == "title":
            self.to_menu()
            return
        if self.update is not None and self.update.click(gui, pos):      # Round 30: the update card, top right
            return
        if self.update is not None and self.update.busy():                # no leaving the title while it downloads
            return
        for rect, name in self.buttons:
            if rect.collidepoint(pos):
                self.choose(gui, name)
                return

    def key(self, gui, ev):
        if self.stage == "splash":
            self.to_title()
            return
        if self.stage == "title":
            self.to_menu()
            return
        if self.update is not None and (self.update.key(gui, ev) or self.update.busy()):
            return                                                        # Round 30: Esc = Later; nothing else while it downloads
        names = [n for n, _l in ITEMS]
        if ev.key in (pygame.K_RIGHT, pygame.K_DOWN, pygame.K_d, pygame.K_TAB):
            self.focus = self._step(gui, names, 1)
        elif ev.key in (pygame.K_LEFT, pygame.K_UP, pygame.K_a):
            self.focus = self._step(gui, names, -1)
        elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            self.choose(gui, names[self.focus])

    def _step(self, gui, names, d):
        i = self.focus
        for _ in names:
            i = (i + d) % len(names)
            if self.enabled(gui, names[i]):
                return i
        return self.focus

    # ---- drawing ----------------------------------------------------------------------------------------------------------
    def draw(self, gui):
        scr, L = gui.screen, gui.L
        now = time.monotonic()
        self.tick(now)
        if self.update is None:                                           # Round 30: the update check starts with the splash
            import update_screens
            self.update = update_screens.UpdateNotice(gui)
        fade = min(1.0, (now - self.t0) / FADE) if gui.animations else 1.0
        pic = picture("studio" if self.stage == "splash" else "title", (L.W, L.H))
        if pic is not None:
            scr.blit(pic, (0, 0))
        else:
            scr.blit(gfx.gradient((L.W, L.H), gfx.BG_TOP, gfx.BG_BOTTOM), (0, 0))
        if self.stage == "menu":                                  # the picture dims as the menu comes in
            dim = dimmed("title", (L.W, L.H))
            if dim is not None:
                if fade < 1.0:
                    dim = dim.copy()
                    dim.set_alpha(int(255 * fade))
                scr.blit(dim, (0, 0))
        elif fade < 1.0:
            veil = pygame.Surface((L.W, L.H), pygame.SRCALPHA)
            veil.fill(gfx.SHADOW + (int(255 * (1 - fade)),))
            scr.blit(veil, (0, 0))
        self.buttons = []
        if self.stage == "splash":
            if pic is None:
                draw_text(scr, "Manticore", L.W // 2, L.H // 2, get_font(int(60 * L.fs), True, "display"), gfx.DEFEAT, "center")
            draw_text(scr, "Click to skip", L.W - L.margin, L.H - L.margin, gui.font("small"), gfx.DIM, "bottomright")
            return
        if pic is None:
            draw_text(scr, "Manticore", L.W // 2, int(L.H * 0.36), get_font(int(90 * L.fs), True, "display"), gfx.DEFEAT, "center")
        if self.stage == "title":
            self._draw_prompt(gui, now)
            return
        # the menu: one row along the bottom, under the wordmark (the title art is a logo; nothing is drawn over the word itself)
        font = get_font(int(max(22, min(30 * L.fs, L.H * 0.045))), True, "display")
        small = gui.font("small")
        gap = int(max(28, 44 * L.fs))
        widths = [font.size(label)[0] for _n, label in ITEMS]
        while sum(widths) + gap * (len(ITEMS) - 1) > L.W - 2 * L.margin and gap > 12:
            gap -= 4
        row_y = L.H - L.margin - small.get_height() - font.get_height() - int(14 * L.fs)
        strip_h = int(L.H * 0.24)
        key = ("strip", L.W, strip_h)
        strip = _PICS.get(key)
        if strip is None:                                         # dark at the bottom, clear at the top: the art fades into the menu
            strip = pygame.Surface((L.W, strip_h), pygame.SRCALPHA)
            for y in range(strip_h):
                strip.fill(gfx.BACKDROP + (int(215 * (y / max(1, strip_h - 1)) ** 1.4),), pygame.Rect(0, y, L.W, 1))
            _PICS[key] = strip
        strip_layer = strip
        if fade < 1.0:
            strip_layer = strip.copy()
            strip_layer.set_alpha(int(255 * fade))
        scr.blit(strip_layer, (0, L.H - strip_h))
        names = [n for n, _l in ITEMS]
        if not self.enabled(gui, names[self.focus]):
            self.focus = self._step(gui, names, 1)
        x = L.W // 2 - (sum(widths) + gap * (len(ITEMS) - 1)) // 2
        for i, ((name, label), w) in enumerate(zip(ITEMS, widths)):
            on = self.enabled(gui, name)
            rect = pygame.Rect(x - gap // 2, row_y - 6, w + gap, font.get_height() + 12)
            if rect.collidepoint(gui.mouse) and on:
                self.focus = i
            hot = on and i == self.focus
            colour = gfx.DEFEAT if hot else (gfx.WHITE if on else gfx.BTN_OFF_FG)
            draw_text(scr, label, x + w // 2, row_y, font, colour, "midtop")
            if hot:                                                   # the title art's diamond, under the chosen word
                cx, cy = x + w // 2, row_y + font.get_height() + 4
                pygame.draw.polygon(scr, gfx.GOLD, [(cx, cy - 4), (cx + 4, cy), (cx, cy + 4), (cx - 4, cy)])
                pygame.draw.line(scr, gfx.GOLD_DARK, (x, cy), (cx - 7, cy))
                pygame.draw.line(scr, gfx.GOLD_DARK, (cx + 7, cy), (x + w, cy))
            self.buttons.append((rect, name))
            x += w + gap
        if self.focus != self.heard_focus:
            self.heard_focus = self.focus
            gui.play_cue("ui.menu_hover")
        why = gui.continue_note()
        if why:
            draw_text(scr, why, L.W // 2, row_y - small.get_height() - int(8 * L.fs), small, gfx.DIM, "midtop")
        draw_text(scr, f"v{self.version}  -  a Commander playtester", L.margin, L.H - L.margin, gui.font("small"), gfx.DIM,
                  "bottomleft")
        self.update.draw(gui)                                            # Round 30: "a new version is ready", top right

    def _draw_prompt(self, gui, now):
        """The title: the picture alone, "Press any key" slowly breathing under it, the version bottom left."""
        scr, L = gui.screen, gui.L
        strip_h = int(L.H * 0.09)
        key = ("prompt", L.W, strip_h)
        strip = _PICS.get(key)
        if strip is None:                                         # a thin dark band at the very bottom so the words read on the bones
            strip = pygame.Surface((L.W, strip_h), pygame.SRCALPHA)
            for y in range(strip_h):
                strip.fill(gfx.BACKDROP + (int(190 * (y / max(1, strip_h - 1)) ** 1.5),), pygame.Rect(0, y, L.W, 1))
            _PICS[key] = strip
        scr.blit(strip, (0, L.H - strip_h))
        font = get_font(int(max(18, min(24 * L.fs, L.H * 0.036))), True, "display")
        beat = 0.5 + 0.5 * math.sin((now - self.t0) * 2.2) if gui.animations else 1.0
        shade = tuple(int(a + (b - a) * (0.45 + 0.55 * beat)) for a, b in zip(gfx.DIM, gfx.GOLD))
        draw_text(scr, PROMPT, L.W // 2, L.H - L.margin, font, shade, "midbottom")
        draw_text(scr, f"v{self.version}  -  a Commander playtester", L.margin, L.H - L.margin, gui.font("small"), gfx.DIM,
                  "bottomleft")
