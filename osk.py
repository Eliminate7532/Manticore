# SPDX-License-Identifier: GPL-3.0-or-later
"""Round PEN1 (24 Oct 2026; Karl, on his Surface with a stylus and no keyboard: "It was impossible for me to ... type an answer
with a stylus"): an on-screen keyboard.

Every place that takes typing (Forge's "type an answer" window, the Find box of a long list, the deck search, the bug report)
has a Keyboard button; it opens this along the bottom of the window (Move puts it at the top, Hide closes it). A key sends the
same key press a real keyboard would, straight to the window that opened it - so the table's own one-letter shortcuts (H for
Help, M for sound, + and - for the text size) never fire while you type. It closes by itself when that window goes away.
"""
import pygame

import gfx
from gfx import GOLD, WHITE, draw_text, round_rect

KEY_H = 52              # px at 100% text: a key a pen or a finger can hit
MAX_SHARE = 0.45        # the keyboard never takes more than this share of the window's height
ROWS = (tuple("1234567890-'"), tuple("qwertyuiop"), tuple("asdfghjkl"),
        ("shift",) + tuple("zxcvbnm,.") + ("back",), ("move", "space", "enter", "hide"))
WIDE = {"shift": 1.6, "back": 1.6, "move": 2.2, "space": 5.6, "enter": 2.2, "hide": 2.0}
SPECIAL = {"back": (pygame.K_BACKSPACE, "\b"), "enter": (pygame.K_RETURN, "\r"), "space": (pygame.K_SPACE, " ")}


def key_event(name, shift=False):
    """The pygame KEYDOWN a real keyboard would send for this key (None for Shift, Move and Hide)."""
    if name in SPECIAL:
        code, ch = SPECIAL[name]
        return pygame.event.Event(pygame.KEYDOWN, key=code, unicode=ch, mod=0, scancode=0, osk=True)
    if len(name) != 1:
        return None
    ch = name.upper() if shift and name.isalpha() else name
    return pygame.event.Event(pygame.KEYDOWN, key=ord(name), unicode=ch, mod=pygame.KMOD_SHIFT if ch != name else 0,
                              scancode=0, osk=True)


class Keyboard:
    def __init__(self):
        self.owner = None                   # the window or screen that opened it (keys go there)
        self.shift = False                  # the next letter is a capital
        self.top = False                    # Move: along the top of the window instead of the bottom
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.keys = []                      # (rect, name), filled by layout()

    @property
    def open(self):
        return self.owner is not None

    def show(self, owner):
        self.owner = owner

    def hide(self):
        self.owner = None
        self.shift = False

    def label(self, name):
        return {"shift": "SHIFT" if self.shift else "Shift", "back": "Back", "move": "Move down" if self.top else "Move up",
                "space": "Space", "enter": "Enter", "hide": "Hide"}.get(name, name.upper() if self.shift else name)

    def layout(self, W, H, fs):
        """Where the keyboard and its keys go in a W x H window at text scale fs."""
        k = max(1.0, fs)
        pad = int(8 * k)
        gap = max(3, int(5 * k))
        kh = min(int(KEY_H * k), int((H * MAX_SHARE - 2 * pad) / len(ROWS)) - gap)
        units = max(sum(WIDE.get(n, 1.0) for n in row) for row in ROWS)
        u = min((W - 24 - 2 * pad - gap * 12) / units, kh * 1.6)
        width = int(units * u + gap * 11 + 2 * pad)
        height = len(ROWS) * (kh + gap) - gap + 2 * pad
        self.rect = pygame.Rect((W - width) // 2, 8 if self.top else H - height - 8, width, height)
        self.keys = []
        y = self.rect.y + pad
        for row in ROWS:
            row_w = sum(WIDE.get(n, 1.0) * u for n in row) + gap * (len(row) - 1)
            x = self.rect.x + (self.rect.w - row_w) / 2
            for n in row:
                w = WIDE.get(n, 1.0) * u
                self.keys.append((pygame.Rect(int(x), y, int(w), kh), n))
                x += w + gap
            y += kh + gap
        return self.rect

    def draw(self, gui):
        if not self.open:
            return
        L = gui.L
        self.layout(L.W, L.H, L.fs)
        gfx.shadowed(gui.screen, self.rect, 14, 6, 140)
        round_rect(gui.screen, self.rect, gfx.DIALOG_BG, 12, 2, gfx.DIALOG_EDGE)
        kh = self.keys[0][0].h if self.keys else 40
        letter = gfx.get_font(max(14, int(kh * 0.46)), True)  # the body face: the display face's letters are too fancy to read on keys
        for r, n in self.keys:
            single = len(n) == 1
            gui.draw_button(r, "" if single else self.label(n), "osk_" + n, True, n == "enter" or (n == "shift" and self.shift), False,
                            hit=False, fkey="small")
            if single:
                draw_text(gui.screen, self.label(n), r.centerx, r.centery, letter, WHITE, "center")
        if self.shift:
            r = next(r for r, n in self.keys if n == "shift")
            pygame.draw.rect(gui.screen, GOLD, r, 2, border_radius=gfx.BUTTON_RADIUS)

    def hit(self, pos):
        """The key at pos, or "pad" for the keyboard's own background (a tap there does nothing), or None (not on it)."""
        if not self.open or not self.rect.collidepoint(pos):
            return None
        for r, n in self.keys:
            if r.collidepoint(pos):
                return n
        return "pad"

    def press(self, gui, name):
        """A tap on key `name`: Shift / Move / Hide change the keyboard; every other key is sent to the window that opened it."""
        if name == "shift":
            self.shift = not self.shift
        elif name == "move":
            self.top = not self.top
        elif name == "hide":
            self.hide()
        elif name != "pad":
            ev = key_event(name, self.shift)
            if ev is not None:
                if len(name) == 1 and name.isalpha():
                    self.shift = False                   # one capital, like a phone's keyboard
                gui.keyboard_send(ev)


def toggle_button(dialog, gui, rect, name="keyboard"):
    """The Keyboard button a window draws beside its text box (Dialog.button: drawn, and registered for its own click())."""
    dialog.button(gui, rect, "Keyboard", name, True, False, bool(getattr(gui, "keyboard", None) and gui.keyboard.owner is dialog),
                  fkey="small")


def button_width(gui):
    return gui.button_width("Keyboard", "small")
