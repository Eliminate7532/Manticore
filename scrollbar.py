# SPDX-License-Identifier: GPL-3.0-or-later
"""Round UI8 (23 Oct 2026; Karl: "This scroll bar on the side of the deck list is almost unusable. It needs to be 'clickable' by
mouse, and maybe even mouse-wheel scroll up/down"): a scroll bar you can use. The deck list's bar was a 5 px line that only showed
where you were. This one is a groove with a handle: drag the handle, or click the groove above or below it to move a page. Used by
the deck list (forge_menu.DeckMenu) and the card-picking windows (forge_dialogs.ChooseDialog).

The owner keeps its own `scroll` (pixels from the top) and asks the bar what a press, a drag or the end of a drag does to it.
"""
import pygame

import gfx

WIDTH = 12              # px at 100% text (the old deck-list bar was 5 px, the card windows' 5 px)
MIN_HANDLE = 36         # px at 100% text: a handle never smaller than this, however long the list
PAGE_KEEP = 0.15        # a page step keeps this share of the view on screen, so you can see where you were


class ScrollBar:
    def __init__(self):
        self.track = pygame.Rect(0, 0, 0, 0)
        self.handle = pygame.Rect(0, 0, 0, 0)
        self.view = 0
        self.content = 0
        self.grab = None                     # while dragging: where on the handle the pointer took hold (px from its top)

    @staticmethod
    def width(fs):
        """The bar's width at this text scale (the room the owner leaves for it at the right of its view)."""
        return max(10, int(WIDTH * max(1.0, fs)))

    @property
    def needed(self):
        """True when there is more to see than the view shows (otherwise there is no bar)."""
        return self.content > self.view > 0

    @property
    def dragging(self):
        return self.grab is not None

    def max_scroll(self):
        return max(0, self.content - self.view)

    def clamp(self, scroll):
        return max(0, min(int(scroll), self.max_scroll()))

    def layout(self, view_rect, content_h, scroll, fs):
        """Place the bar along the right edge of `view_rect` for this much content and this scroll."""
        self.view, self.content = view_rect.h, content_h
        w = self.width(fs)
        self.track = pygame.Rect(view_rect.right - w, view_rect.y, w, view_rect.h)
        if not self.needed:
            self.handle = pygame.Rect(self.track.x, self.track.y, w, 0)
            self.grab = None
            return
        hh = max(int(MIN_HANDLE * max(1.0, fs)), int(self.track.h * self.view / self.content))
        hh = min(hh, self.track.h)
        room = self.track.h - hh
        top = self.track.y + (int(room * self.clamp(scroll) / self.max_scroll()) if self.max_scroll() else 0)
        self.handle = pygame.Rect(self.track.x, top, w, hh)

    def draw(self, screen, mouse):
        if not self.needed:
            return
        r = self.track.w // 2
        pygame.draw.rect(screen, gfx.SCROLL_TRACK, self.track, border_radius=r)
        hot = self.dragging or self.handle.collidepoint(mouse)
        pygame.draw.rect(screen, gfx.SCROLL_HANDLE_HOT if hot else gfx.SCROLLBAR, self.handle.inflate(-2, 0), border_radius=r)

    def press(self, pos, scroll):
        """A left-button press. None when it isn't on the bar (the owner handles it as before); otherwise the new scroll: a press
        on the handle starts a drag and keeps the scroll, a press on the groove moves a page towards it."""
        if not self.needed or not self.track.collidepoint(pos):
            return None
        if self.handle.collidepoint(pos):
            self.grab = pos[1] - self.handle.y
            return self.clamp(scroll)
        page = max(1, int(self.view * (1 - PAGE_KEEP)))
        return self.clamp(scroll - page if pos[1] < self.handle.y else scroll + page)

    def drag(self, pos):
        """The pointer moved with the button held: the new scroll, or None when no drag is going on."""
        if self.grab is None:
            return None
        room = self.track.h - self.handle.h
        if room <= 0:
            return 0
        top = pos[1] - self.grab - self.track.y
        return self.clamp(round(top / room * self.max_scroll()))

    def release(self):
        """The button went up: True when that ended a drag."""
        was = self.grab is not None
        self.grab = None
        return was
