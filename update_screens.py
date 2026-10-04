# SPDX-License-Identifier: GPL-3.0-or-later
"""
update_screens.py - Round 30: the title screen's "a new version is ready" card (updater.py does the work).

    checking (nothing shown) -> offer: "Manticore 0.30.1 is ready (you have 0.30.0)", the notes, Update now / Later / Skip this
    version -> downloading: a bar and Cancel -> installing: "Manticore will close and reopen by itself" -> the program closes.
    A failed download shows why, with Try again / Later.

Only the title screen's menu shows it (boot_screens.BootFlow), so it can never interrupt a game. It sits in the top-right
corner, clear of the menu row along the bottom.
"""
import time

import pygame

import gfx
import updater
from gfx import DIM, GOLD, GREEN, ORANGE, WHITE, draw_text, round_rect, wrap_text

_STARTED = {"done": False}            # one check per run of the program, however often the title is shown


class UpdateNotice:
    def __init__(self, gui):
        self.state = "idle"           # idle -> checking -> offer -> downloading -> installing; or hidden / failed
        self.check_job = None
        self.download = None
        self.info = None
        self.message = None
        self.buttons = []
        self.rect = None
        self.closing_at = 0.0
        ok, url = updater.enabled(getattr(gui, "check_updates", True))
        if ok and not _STARTED["done"]:
            _STARTED["done"] = True
            self.check_job = updater.CheckJob(url).start()
            self.state = "checking"

    # ---- ticking --------------------------------------------------------------------------------------------------------
    def poll(self, gui):
        if self.state == "installing" and time.monotonic() >= self.closing_at:
            gui.running = False                                     # the helper waits for this program to close
            return
        if self.state == "checking" and self.check_job and self.check_job.finished:
            job, self.check_job = self.check_job, None
            if job.info is not None and job.info.version != getattr(gui, "skip_update_version", None):
                self.info = job.info
                self.state = "offer"
            else:
                self.state = "hidden"
                if job.error and job.error != updater.NO_RELEASE:          # patch 38: no release yet isn't a problem to log
                    _note("Update check failed", job.error)
        elif self.state == "downloading" and self.download and self.download.finished:
            job, self.download = self.download, None
            if job.cancelled:
                self.state = "offer"
            elif job.error:
                self.state = "failed"
                self.message = job.error
                _note("Update download failed", job.error)
            else:
                self.install(gui, job.path)

    def visible(self):
        return self.state in ("offer", "downloading", "installing", "failed")

    def busy(self):
        return self.state in ("downloading", "installing")

    # ---- actions ----------------------------------------------------------------------------------------------------------
    def press(self, gui, name):
        if name == "update" and self.info is not None:
            self.download = updater.DownloadJob(self.info).start()
            self.state = "downloading"
            self.message = None
        elif name == "later":
            self.state = "hidden"
        elif name == "skip" and self.info is not None:
            gui.skip_update_version = self.info.version
            gui.save_settings()
            self.state = "hidden"
        elif name == "cancel" and self.download is not None:
            self.download.cancel()

    def install(self, gui, path):
        game = bool(getattr(gui, "game_in_progress", lambda: False)())
        ok, why = updater.launch_installer(path, game_running=game)
        if not ok:
            self.state = "failed"
            self.message = why
            _note("Update could not start the installer", why)
            return
        _note("Update started", "installing %s; the program closes itself now" % self.info.version)
        self.state = "installing"
        self.closing_at = time.monotonic() + 1.2                # long enough to read the line, then close

    def click(self, gui, pos):
        """True when the click was on the card."""
        if not self.visible() or self.rect is None or not self.rect.collidepoint(pos):
            return False
        for rect, name in reversed(self.buttons):
            if rect.collidepoint(pos):
                self.press(gui, name)
                break
        return True

    def key(self, gui, ev):
        """Esc = Later while the offer is up. True when the key was used."""
        if self.state in ("offer", "failed") and ev.key == pygame.K_ESCAPE:
            self.state = "hidden"
            return True
        return False

    # ---- drawing ----------------------------------------------------------------------------------------------------------
    def button(self, gui, rect, label, name, primary=False):
        gui.draw_button(rect, label, name, True, primary, primary, hit=False, fkey="small")
        self.buttons.append((pygame.Rect(rect), name))

    def draw(self, gui):
        self.poll(gui)
        self.buttons = []
        self.rect = None
        if not self.visible():
            return
        scr, L = gui.screen, gui.L
        fs = L.fs
        title, body, small = gui.font("title", True), gui.font("body"), gui.font("small")
        w = min(max(int(460 * fs), 300), L.W - 2 * L.margin)
        tw = w - 2 * int(16 * fs)
        pad = int(16 * fs)
        bh = int(max(34, 38 * fs))
        gap = int(8 * fs)
        info = self.info
        head = "A new version is ready" if self.state != "failed" else "The update didn't work"
        lines = []
        if info is not None:
            lines.append(("Manticore %s  (you have %s)" % (info.version, updater.version.VERSION), body, WHITE))
        if self.state == "offer" and info is not None and info.notes:
            lines += [(ln, small, gfx.BODY_TEXT) for ln in wrap_text(info.notes, small, tw)[:3]]
        if self.state == "offer" and info is not None:
            lines.append(("%d MB to download. Your decks, settings and saved games stay." % max(1, round(info.size / 1e6)),
                          small, DIM))
        if self.state == "downloading" and self.download is not None:
            lines.append(("Downloading...  %d%%" % int(self.download.fraction() * 100), small, WHITE))
        if self.state == "installing":
            lines += [(ln, small, GREEN) for ln in wrap_text("Installing: Manticore will close and reopen by itself.", small, tw)]
        if self.state == "failed" and self.message:
            lines += [(ln, small, ORANGE) for ln in wrap_text(self.message, small, tw)[:3]]
        h = pad + title.get_height() + gap + sum(f.get_height() + 2 for _t, f, _c in lines) + gap
        if self.state == "downloading":
            h += int(10 * fs) + gap
        if self.state != "installing":
            h += bh + pad
        else:
            h += pad
        rect = pygame.Rect(L.W - L.margin - w, L.margin, w, h)
        self.rect = rect
        gfx.shadowed(scr, rect, 12, 5, 120)
        round_rect(scr, rect, gfx.DIALOG_BG, 12, 2, GOLD)
        x, y = rect.x + pad, rect.y + pad
        draw_text(scr, head, x, y, title, GOLD if self.state != "failed" else ORANGE)
        y += title.get_height() + gap
        for text, f, colour in lines:
            draw_text(scr, text, x, y, f, colour)
            y += f.get_height() + 2
        y += gap
        if self.state == "downloading" and self.download is not None:
            bar = pygame.Rect(x, y, tw, int(10 * fs))
            round_rect(scr, bar, gfx.PANEL_EDGE, bar.h // 2)
            done = int(tw * self.download.fraction())
            if done > 0:
                round_rect(scr, pygame.Rect(x, y, max(bar.h, done), bar.h), GOLD, bar.h // 2)
            y = bar.bottom + gap
        if self.state == "installing":
            return
        by = rect.bottom - pad - bh
        if self.state == "offer":
            specs = (("Update now", "update", True), ("Later", "later", False), ("Skip this version", "skip", False))
        elif self.state == "downloading":
            specs = (("Cancel", "cancel", False),)
        else:
            specs = (("Try again", "update", True), ("Later", "later", False))
        widths = [max(gui.button_width(label, "small"), int(90 * fs)) for label, _n, _p in specs]
        bx = x
        for (label, name, primary), bw in zip(specs, widths):
            bw = min(bw, rect.right - pad - bx)
            self.button(gui, pygame.Rect(bx, by, bw, bh), label, name, primary)
            bx += bw + gap


def _note(title, text):
    try:
        import crashlog
        crashlog.note(title, text)
    except Exception:
        pass


def reset_for_tests():
    _STARTED["done"] = False
