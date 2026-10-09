# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_settings.py - the settings pop-up (the cog at the top right) and the bug report form (Round FR1: with a "Suggest a feature" tab).

Both are "overlays": small objects with draw(gui) / click(gui, pos, button) / key(gui, event) / wheel(gui, dy) and a `done` flag, like
the dialogs in forge_dialogs.py, but they sit ABOVE everything (even a question from Forge) and get every click and key first.
"""
import os
import time

import pygame

import gfx
import reporting
from forge_dialogs import Dialog
from gfx import CYAN, DIM, GOLD, GREEN, ORANGE, WHITE, clip_text, draw_text, round_rect, wrap_text


# ---------------------------------------------------------------------------------------------------------------------------------
# the cog's pop-up
# ---------------------------------------------------------------------------------------------------------------------------------

class SettingsPopup(Dialog):
    """Hangs under the cog, in four groups. DISPLAY: text size, the table background (Round AD1), full screen and animations (the last two are on/off switches that stay
    put while you flip them). GAME: Speed (round PRI1: Fast | Slow), New game... and Concede... (each of the last two opens a small
    window that asks what exactly). HELP: controls and
    help, bug or idea (Round FR1: the report window has a Suggest a feature tab), licences, the data folder, and the tour of the
    table (Round UX1). Anything outside it (or Esc) closes it."""

    SWITCHES = (("full", "Full screen   (F11)"), ("motion", "Animations"), ("frames", "Compact board cards"),
                ("hand_sort", "Sort hand by type"))              # (patch UI6: the last two are buttons in one row now)

    def draw(self, gui):
        L, scr = gui.L, gui.screen
        fs = max(1.0, L.fs)
        w, pad, gap = int(340 * fs), int(12 * fs), int(8 * fs)
        head, sect = gui.ui_font("body", True), gui.ui_font("small", True)      # round 32: the display face
        sect_h = sect.get_height() + int(6 * fs)
        nrows, nsect = 12, 4                    # DISPLAY(5: text size, Table (round AD1), Full screen | Animations (round AU1) and 2 switches) +
                                                 # SOUND(3: Sound | Hover tick, Music | Ambience (round AU1), Volume) + GAME(1: New game | Concede,
                                                 # Round UX1) + HELP(3: round 28 pairs "Open my data folder" with Licenses; Round UX1 adds the tour) rows,
                                                 # 4 headings. Round 28 found that 13 rows cannot fit at the smallest window size.
        room = L.H - L.top_h - 10                            # rows shrink (never below a readable height) to keep it all on screen
        fixed = 2 * pad + head.get_height() + gap + nsect * sect_h + (nrows - 1) * gap
        excess = fixed + nrows * 30 - room                   # even at the row-height floor it still wouldn't fit: tighten the gaps too
        if excess > 0:
            gap = max(4, gap - -(-excess // nrows))
            fixed = 2 * pad + head.get_height() + gap + nsect * sect_h + (nrows - 1) * gap
        row_h = int(max(30, min(int(40 * fs), (room - fixed) / nrows)))
        h = fixed + nrows * row_h
        w, h = min(w, L.W - 20), min(h, room)
        rect = pygame.Rect(0, 0, w, h)
        anchor = getattr(gui, "cog_rect", None)
        rect.topright = (min(L.W - 8, anchor.right if anchor else L.W - 8), L.top_h + 2)
        gfx.shadowed(scr, rect, 14, 5, 130)
        round_rect(scr, rect, gfx.DIALOG_BG, 14, 2, gfx.DIALOG_EDGE)
        self.buttons = []
        self.rect = rect
        x, y = rect.x + pad, rect.y + pad
        inner = rect.w - 2 * pad
        draw_text(scr, "Settings", x, y, head, GOLD)
        y += head.get_height() + gap

        def heading(text):
            nonlocal y
            label = draw_text(scr, text, x, y, sect, DIM)
            pygame.draw.line(scr, gfx.HEADING_RULE, (label.right + 8, label.centery), (x + inner, label.centery), 1)
            y += sect_h

        heading("DISPLAY")
        half = (inner - gap) // 2
        # text size:  Text size  [A-]  100%  [A+]
        bw = int(max(gui.button_width("A+", "small"), 46 * fs))
        r_plus = pygame.Rect(rect.right - pad - bw, y, bw, row_h)
        r_minus = pygame.Rect(r_plus.x - int(78 * fs) - bw, y, bw, row_h)
        draw_text(scr, "Text size", x, y + row_h // 2, gui.ui_font("body"), WHITE, "midleft")
        draw_text(scr, f"{round(gui.text_scale * 100)}%", (r_minus.right + r_plus.x) // 2, y + row_h // 2, gui.font("body", True), WHITE, "center")
        self._small_button(gui, r_minus, "A-", "smaller")
        self._small_button(gui, r_plus, "A+", "bigger")
        y += row_h + gap
        # table background:  Table  [<]  Rotate  [>]     (round AD1; the same shape as the Text size row)
        draw_text(scr, "Table", x, y + row_h // 2, gui.ui_font("body"), WHITE, "midleft")
        b_next = pygame.Rect(rect.right - pad - bw, y, bw, row_h)
        b_prev = pygame.Rect(b_next.x - int(96 * fs) - bw, y, bw, row_h)
        mid = (b_prev.right + b_next.x) // 2
        vfont = gui.ui_font("body", True)
        draw_text(scr, clip_text(gui.background_label(), vfont, b_next.x - b_prev.right - 4), mid, y + row_h // 2, vfont, WHITE, "center")
        self._small_button(gui, b_prev, "<", "bg_prev")
        self._small_button(gui, b_next, ">", "bg_next")
        y += row_h + gap
        # round AU1: Full screen | Animations share a row (short labels; F11 is in the Controls list), so SOUND has room for
        # Music | Ambience and the pop-up stays at 12 rows
        self.switch(gui, pygame.Rect(x, y, half, row_h), "Full screen", "full", gui.fullscreen, "small")
        self.switch(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h), "Animations", "motion", gui.animations, "small")
        y += row_h + gap
        # patch UI6: Compact cards | Sort hand share a row (two buttons, gold while on), which makes room for Log | Focus below
        # without the pop-up growing past 12 rows (a half-width switch has only about 60 px left for its label)
        self.button(gui, pygame.Rect(x, y, half, row_h), f"Compact: {'On' if gui.frames else 'Off'}", "frames", True,
                    primary=bool(gui.frames), fkey="small")
        self.button(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h),
                    f"Sort hand: {'On' if gui.hand_sort_by_type else 'Off'}", "hand_sort", True, primary=bool(gui.hand_sort_by_type), fkey="small")
        y += row_h + gap
        # patch UI6: the game log and the focus card each cycle through three modes (right click goes back)
        self.button(gui, pygame.Rect(x, y, half, row_h), f"Log: {gui.log_label()}", "log_mode", True,
                    primary=gui.log_mode != "always", fkey="small")
        self.button(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h), f"Focus: {gui.preview_label()}", "preview_mode", True,
                    primary=gui.preview_mode != "always", fkey="small")
        y += row_h + gap
        heading("SOUND")
        half = (inner - gap) // 2
        self.switch(gui, pygame.Rect(x, y, half, row_h), "Sound", "sound", gui.sound.get("on", True), "small")
        self.switch(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h), "Hover tick", "hover_tick", gui.sound.get("hover_tick", True), "small")
        y += row_h + gap
        self.switch(gui, pygame.Rect(x, y, half, row_h), "Music", "music_on", gui.sound.get("music_on", True), "small")      # round AU1
        self.switch(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h), "Ambience", "ambience_on", gui.sound.get("ambience_on", True), "small")
        y += row_h + gap
        vw = int(max(gui.button_width("-", "small"), 46 * fs))
        v_plus = pygame.Rect(rect.right - pad - vw, y, vw, row_h)
        v_minus = pygame.Rect(v_plus.x - int(78 * fs) - vw, y, vw, row_h)
        draw_text(scr, "Volume", x, y + row_h // 2, gui.ui_font("body"), WHITE, "midleft")
        draw_text(scr, f"{round(gui.sound.get('master', 0.8) * 100)}%", (v_minus.right + v_plus.x) // 2, y + row_h // 2,
                  gui.font("body", True), WHITE, "center")
        self._small_button(gui, v_minus, "-", "vol_down")
        self._small_button(gui, v_plus, "+", "vol_up")
        y += row_h + gap
        heading("GAME")                         # Round UX1: New game... and Concede... share one row, so HELP has room for the tour
        # Round PRI1: Speed (Fast | Slow) joins them, three to the row - a 13th row doesn't fit at the smallest window (round 28),
        # and the three labels fit a third of the pop-up at every size the tests use (gold while Slow, like Log and Focus)
        third = (inner - 2 * gap) // 3
        self.button(gui, pygame.Rect(x, y, third, row_h), f"Speed: {gui.speed_label()}", "speed", True,
                    primary=gui.speed == "slow", fkey="small")
        self.button(gui, pygame.Rect(x + third + gap, y, third, row_h), "New game...", "newgame", True, fkey="small")
        concede = pygame.Rect(x + 2 * (third + gap), y, inner - 2 * (third + gap), row_h)
        self.button(gui, concede, "Concede...", "concede", gui.game_in_progress(), fkey="small")
        if gui.game_in_progress():
            pygame.draw.rect(scr, gfx.RED, concede, 2, border_radius=10)      # the one button that gives something up
        y += row_h + gap
        heading("HELP")
        self.button(gui, pygame.Rect(x, y, half, row_h), "Controls   (H)", "help", True, fkey="small")
        self.button(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h), "Bug or idea   (F8)", "report", True, fkey="small")      # Round FR1
        y += row_h + gap
        self.button(gui, pygame.Rect(x, y, half, row_h), "Licenses", "licenses", True, fkey="small")
        self.button(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h), "My data folder", "data_folder", True, fkey="small")  # round 28
        y += row_h + gap
        self.button(gui, pygame.Rect(x, y, half, row_h), "Tour of the table", "tour", gui.tour_available(), fkey="small")  # Round UX1
        self.switch(gui, pygame.Rect(x + half + gap, y, inner - half - gap, row_h), "Updates", "updates",
                    gui.check_updates, "small")                                                    # Round 30

    def switch(self, gui, rect, label, name, on, fkey="body"):
        """A row with an on/off switch at its right end. The whole row is the click target. Half-width rows pass fkey="small"."""
        scr = gui.screen
        gui.draw_button(rect, "", name, True, False, False, hit=False, fkey="small")
        self.buttons.append((pygame.Rect(rect), name))
        ph = int(rect.h * 0.56)
        pill = pygame.Rect(0, 0, int(ph * 2.5), ph)
        pill.midright = (rect.right - 12, rect.centery)
        font = gui.ui_font(fkey, True)
        draw_text(scr, clip_text(label, font, pill.x - rect.x - 24), rect.x + 14, rect.centery, font, WHITE, "midleft")
        round_rect(scr, pill, gfx.SWITCH_ON_BG if on else gfx.SWITCH_OFF_BG, ph // 2, 1, gfx.SWITCH_ON_EDGE if on else gfx.SWITCH_OFF_EDGE)
        knob = ph - 6
        kx = pill.right - 3 - knob if on else pill.x + 3
        pygame.draw.ellipse(scr, WHITE if on else gfx.SWITCH_OFF_KNOB, pygame.Rect(kx, pill.y + 3, knob, knob))
        tiny = self.switch_font(gui, pill.w - knob - 9, ph + 4)
        tx = pill.x + (pill.w - knob - 3) // 2 + (0 if on else knob + 3)
        draw_text(scr, "ON" if on else "OFF", tx, pill.centery, tiny, WHITE if on else gfx.SWITCH_OFF_KNOB, "center")

    @staticmethod
    def switch_font(gui, room_w, room_h):
        """The font for a switch's ON/OFF: the display face when "OFF" fits beside the knob, else the body face, smaller if it must.
        Patch 33 (with UI5): at 200% text in a 1080-high window the pop-up's rows are squeezed, and "OFF" ran past the switch's
        edge - in Alegreya before round 32 already, a little further in AvQest after it."""
        font = gui.ui_font("tiny", True)
        if font.size("OFF")[0] <= room_w and font.get_height() <= room_h:
            return font
        px = gui.L.px["tiny"]
        font = gfx.get_font(px, True)
        while px > 8 and (font.size("OFF")[0] > room_w or font.get_height() > room_h):
            px -= 1
            font = gfx.get_font(px, True)
        return font

    def _small_button(self, gui, rect, label, name):
        gui.draw_button(rect, label, name, True, False, False, hit=False, fkey="small")
        self.buttons.append((pygame.Rect(rect), name))

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name is None:
            if not (self.rect and self.rect.collidepoint(pos)):
                self.done = True                             # a click anywhere else just closes it
            return
        gui.play_cue("ui.click")            # round 24
        if name == "smaller":
            gui.change_text_scale(-1)
        elif name == "bigger":
            gui.change_text_scale(1)
        elif name == "bg_prev":
            gui.change_background(-1)
        elif name == "bg_next":
            gui.change_background(1)
        elif name == "full":
            gui.toggle_fullscreen()                          # the pop-up stays open, so you see the switch move
        elif name == "motion":
            gui.toggle_animations()
        elif name == "frames":
            gui.toggle_frames()
        elif name == "hand_sort":
            gui.toggle_hand_sort()
        elif name == "log_mode":
            gui.change_log_mode(-1 if button == 3 else 1)          # patch UI6: right click goes back
        elif name == "preview_mode":
            gui.change_preview_mode(-1 if button == 3 else 1)
        elif name == "sound":
            gui.toggle_sound()
        elif name == "vol_down":
            gui.change_sound_volume(-0.1)
        elif name == "vol_up":
            gui.change_sound_volume(0.1)
        elif name == "hover_tick":
            gui.toggle_hover_tick()
        elif name in ("music_on", "ambience_on"):
            gui.toggle_stream(name)
        elif name == "updates":
            gui.toggle_check_updates()                       # Round 30
        elif name == "speed":
            gui.toggle_speed()                               # round PRI1
        else:
            self.done = True
            gui.overlay = None
            if name == "newgame":
                gui.ask_new_game()
            elif name == "concede":
                gui.ask_concede()
            elif name == "help":
                gui.open_help()
            elif name == "report":
                gui.open_report(ReportDialog.BUG)
            elif name == "licenses":
                gui.open_licenses()
            elif name == "data_folder":
                gui.open_data_folder()
            elif name == "tour":
                gui.start_tour(manual=True)

    def key(self, gui, ev):
        if ev.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.done = True
        elif ev.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            gui.change_text_scale(1)
        elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            gui.change_text_scale(-1)
        elif ev.key == pygame.K_F11:
            gui.overlay = None
            self.done = True
            gui.toggle_fullscreen()


# ---------------------------------------------------------------------------------------------------------------------------------
# text boxes
# ---------------------------------------------------------------------------------------------------------------------------------

def wrap_field(text, font, width):
    """Lines of `text` no wider than `width` px; explicit newlines kept and words longer than a line are broken."""
    lines = []
    for paragraph in (text or "").split("\n"):
        line = ""
        for word in paragraph.split(" "):
            trial = f"{line} {word}" if line else word
            if font.size(trial)[0] <= width:
                line = trial
                continue
            if line:
                lines.append(line)
            line = ""
            while font.size(word)[0] > width and len(word) > 1:      # a very long word (a link): cut it
                cut = len(word)
                while cut > 1 and font.size(word[:cut])[0] > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    if (text or "").endswith(" ") and lines:
        lines[-1] += " "
    return lines


class TextField:
    """A text box that only edits at the end (type, backspace, paste): enough for a bug report, and hard to get wrong."""

    def __init__(self, label, text="", multiline=True, limit=reporting.FIELD_LIMIT, lines=3, hint=""):
        self.label, self.text, self.multiline, self.limit, self.lines, self.hint = label, text, multiline, limit, lines, hint
        self.rect = pygame.Rect(0, 0, 0, 0)

    def key(self, ev, paste):
        """Returns True when the key was used."""
        k, mod = ev.key, getattr(ev, "mod", 0)
        if k == pygame.K_BACKSPACE:
            self.text = self.text[:-1]
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if not self.multiline or mod & pygame.KMOD_CTRL:
                return False
            self.add("\n")
        elif k == pygame.K_v and mod & pygame.KMOD_CTRL:
            self.add(paste())
        elif mod & pygame.KMOD_CTRL:
            return False
        elif ev.unicode and ev.unicode.isprintable():
            self.add(ev.unicode)
        else:
            return False
        return True

    def add(self, text):
        text = reporting.clean_field(text, self.limit)
        if not self.multiline:
            text = text.replace("\n", " ")
        self.text = (self.text + text)[:self.limit]

    def draw(self, gui, rect, focused):
        scr = gui.screen
        self.rect = pygame.Rect(rect)
        f = gui.font("body")
        round_rect(scr, rect, gfx.FIELD_BG, 8, 2, GOLD if focused else gfx.FIELD_EDGE)
        inner = rect.w - 20
        lines = wrap_field(self.text, f, inner)
        shown = max(1, (rect.h - 10) // f.get_height())
        show = lines[-shown:]
        if not self.text and not focused and self.hint:
            draw_text(scr, clip_text(self.hint, f, inner), rect.x + 10, rect.y + 6, f, gfx.HINT_TEXT)
        y = rect.y + 6
        for ln in show:
            draw_text(scr, ln, rect.x + 10, y, f, WHITE)
            y += f.get_height()
        if focused and int(time.time() * 2) % 2 == 0:
            last = show[-1] if show else ""
            cx = rect.x + 10 + f.size(last)[0] + 1
            cy = rect.y + 6 + (len(show) - 1) * f.get_height()
            pygame.draw.line(scr, GOLD, (min(cx, rect.right - 6), cy + 2), (min(cx, rect.right - 6), cy + f.get_height() - 2), 2)
        if len(lines) > shown:
            draw_text(scr, "...", rect.right - 8, rect.y + 2, f, DIM, "topright")


# ---------------------------------------------------------------------------------------------------------------------------------
# the bug report form
# ---------------------------------------------------------------------------------------------------------------------------------

class ReportDialog(Dialog):
    """Asks what went wrong, packs the report into one zip and (when a Discord webhook is set up) posts it.

    Round FR1: two tabs at the top, "Report a bug" | "Suggest a feature". The idea tab sends one short text message (no zip, no
    logs; a picture of the screen only when ticked) and always saves bug_reports/idea_*.txt. The name box is shared, and what is
    typed in either tab stays there when you switch. `tab` is the one it opens on (F8 on the deck screen: "idea").

    `context` is what the table hands over:  {"name", "seed", "state", "log_lines", "commands", "screenshot" (a function(max_width))}
    and `on_name(name)` is told the player's name so it is remembered."""

    FORM, WORKING, SENDING, SAVED, SENT, FAILED = "form", "working", "sending", "saved", "sent", "failed"
    BUG, IDEA = "bug", "idea"
    TABS = ((BUG, "Report a bug"), (IDEA, "Suggest a feature"))

    def __init__(self, context, name="", folder=None, on_name=None, url=None, tab=BUG, idea_url=None):
        super().__init__()
        self.context, self.folder, self.on_name = context, folder, on_name
        cfg_url, self.destination_note = reporting.config_status(folder)
        idea_cfg, self.idea_note = reporting.idea_config_status(folder)
        self.owner = reporting.owner_name(folder)
        self.url = cfg_url if url is None else url
        self.idea_url = (idea_cfg if url is None else url) if idea_url is None else idea_url
        name_box = TextField("Your name", name, multiline=False, limit=40, lines=1, hint=f"so {self.owner} knows who sent it")
        self.tab_fields = {
            self.BUG: [name_box, TextField("What happened?", "", lines=4, hint="Describe what went wrong, and what you clicked just before"),
                       TextField("What did you expect to happen? (optional)", "", lines=3)],
            self.IDEA: [name_box, TextField("What would you like?", "", lines=4, hint="Something the game could do, show or have"),
                        TextField("What would it help you do? (optional)", "", lines=3)]}
        self.tab = tab if tab in (self.BUG, self.IDEA) else self.BUG
        self.focus = 1 if name else 0
        self.area = None                       # the idea's "Where in the game?" chip, or None
        self.picture = False                   # the idea's "Add a picture of the screen" box
        self.phase = self.FORM
        self.path = None
        self.picture_path = None
        self.job = None
        self.message = ""
        self.error = ""
        self.retry_ok = False
        self.chips = []                        # (rect, area) drawn this frame
        self.check_rect = None

    # ---- the two tabs
    @property
    def fields(self):
        return self.tab_fields[self.tab]

    @property
    def idea(self):
        return self.tab == self.IDEA

    def switch_tab(self, tab):
        if tab != self.tab and tab in self.tab_fields and self.phase == self.FORM:
            self.tab = tab
            self.focus = 1 if self.fields[0].text.strip() else 0

    def send_url(self):
        return self.idea_url if self.idea else self.url

    def note(self):
        return self.idea_note if self.idea else self.destination_note

    # ---- actions
    @property
    def happened(self):
        return self.fields[1].text.strip()

    def ready(self):
        return len(self.happened) >= 5

    def info(self):
        if self.idea:
            return {"name": self.fields[0].text.strip(), "idea": reporting.clean_field(self.fields[1].text.strip()),
                    "why": reporting.clean_field(self.fields[2].text.strip()), "area": self.area}
        return {"name": self.fields[0].text.strip(), "happened": reporting.clean_field(self.fields[1].text.strip()),
                "expected": reporting.clean_field(self.fields[2].text.strip()), "seed": self.context.get("seed"),
                "format": self.context.get("format")}

    def make_zip(self, gui):
        info = self.info()
        if self.on_name:
            self.on_name(info["name"])
        try:
            if self.idea:
                shot = self.context.get("screenshot") if self.picture else None
                self.path, self.picture_path = reporting.save_idea(info, folder=self.folder, screenshot=shot)
            else:
                self.path = reporting.build_report(info, self.context.get("state"), self.context.get("log_lines"), self.context.get("commands"),
                                                   self.context.get("screenshot"), folder=self.folder,
                                                   extra_env=[self.context["perf"]] if self.context.get("perf") else None,
                                                   extra_files=self.context.get("extra_files"))     # patch 46: Java's crash report
            return True
        except Exception as e:                                 # the report is a helper: it must never take the game down
            what = "idea" if self.idea else "report"
            self.phase, self.error = self.FAILED, f"The {what} could not be written ({type(e).__name__}: {e})."
            self.message = self.error
            self.path = None
            return False

    def submit(self, gui, send):
        if not self.ready():
            return
        wait = reporting.seconds_until_send_allowed() if send else 0
        if wait:
            return
        if not self.path or not send:                          # (a retry re-uses the file already written)
            if not self.make_zip(gui):
                return
        url = self.send_url()
        if send and url:
            when = time.strftime("%Y-%m-%d %H:%M")
            if self.idea:
                self.job = reporting.SendJob(url, reporting.idea_summary(self.info(), when), self.picture_path,
                                             username=reporting.IDEA_BOT_NAME)
            else:
                self.job = reporting.SendJob(url, reporting.summary_text(self.info(), when), self.path)
            self.job.start()
            self.phase = self.SENDING
        else:
            self.phase = self.SAVED

    def poll(self):
        if self.phase == self.SENDING and self.job and self.job.finished:
            ok, kind, msg = self.job.result
            self.message = "Sent! Your idea has been submitted." if ok and self.idea else msg
            self.phase = self.SENT if ok else self.FAILED
            self.retry_ok = kind in ("offline", "rate", "error")

    # ---- drawing
    def rel_path(self, path=None):
        path = path or self.path
        if not path:
            return ""
        try:
            return os.path.relpath(path, self.folder or reporting.BASE_DIR)
        except ValueError:
            return path

    def draw(self, gui):
        self.poll()
        if self.phase == self.FORM:
            self.draw_form(gui)
        else:
            self.draw_result(gui)

    def chip_rows(self, gui, w):
        """The idea's area chips laid out in rows no wider than w: [[(label, width), ...], ...]."""
        fs = max(1.0, gui.L.fs)
        rows, row, used = [], [], 0
        for area in reporting.IDEA_AREAS:
            cw = max(gui.button_width(area, "small"), int(60 * fs))
            if row and used + cw > w:
                rows.append(row)
                row, used = [], 0
            row.append((area, cw))
            used += cw + int(6 * fs)
        if row:
            rows.append(row)
        return rows

    def draw_form(self, gui):
        L, scr = gui.L, gui.screen
        fs = max(1.0, L.fs)
        bf, sf = gui.font("body"), gui.font("small")
        pw = min(int(760 * fs), L.W - 30)
        avail_h = L.H - 30
        bh = int(42 * fs)
        tab_h = int(max(34, 40 * fs))
        if self.idea:
            info_text = (f"This sends only what you write{' and the picture' if self.picture else ''}: no logs, no files. "
                         f"A copy is saved in the bug_reports folder.")
        else:
            info_text = "This sends: " + ", ".join(reporting.CONTENTS) + ". Your Windows user name is removed from file paths."
        info_lines = wrap_text(info_text, sf, pw - 56)
        label_h, gap = bf.get_height() + 4, int(10 * fs)
        chip_h = sf.get_height() + int(10 * fs)
        chip_rows = self.chip_rows(gui, pw - 56) if self.idea else []
        check_h = max(bf.get_height(), int(26 * fs)) if self.idea else 0
        note_lines = wrap_text(self.note(), sf, pw - 56)

        def total(field_lines, with_info, with_chips):
            t = 18 + tab_h + 8 + sf.get_height() * len(note_lines) + gap
            t += sum(label_h + field_lines[i] * bf.get_height() + 14 + gap for i in range(3))
            if with_chips and chip_rows:
                t += label_h + len(chip_rows) * (chip_h + int(6 * fs)) + gap
            if check_h:
                t += check_h + gap
            if with_info:
                t += len(info_lines) * sf.get_height() + gap
            return t + bh + 18

        counts, with_info, with_chips = [f.lines for f in self.fields], True, True
        while total(counts, with_info, with_chips) > avail_h and (counts[1] > 2 or counts[2] > 2):
            for i in (1, 2):
                counts[i] = max(2, counts[i] - 1) if counts[i] > 2 else counts[i]
        if total(counts, with_info, with_chips) > avail_h:
            with_info = False
        if total(counts, with_info, with_chips) > avail_h:
            with_chips = False                                 # the smallest windows: no "Where in the game?" chips
        need = total(counts, with_info, with_chips)
        rect = self.panel(gui, pw, min(need, avail_h), real=True)
        x, y, w = rect.x + 28, rect.y + 18, rect.w - 56
        # the tabs: Report a bug | Suggest a feature
        tx = x
        for tab, label in self.TABS:
            tw = max(gui.button_width(label), int(170 * fs))
            on = tab == self.tab
            self.button(gui, pygame.Rect(tx, y, tw, tab_h), label, "tab_" + tab, True, on, False)
            tx += tw + int(8 * fs)
        pygame.draw.line(scr, GOLD, (x, y + tab_h + 3), (x + w, y + tab_h + 3), 1)
        y += tab_h + 8
        for ln in note_lines:
            draw_text(scr, ln, x, y, sf, GREEN if self.send_url() else ORANGE)
            y += sf.get_height()
        y += gap
        for i, field in enumerate(self.fields):
            draw_text(scr, field.label, x, y, bf, WHITE)
            y += label_h
            box = pygame.Rect(x, y, w, counts[i] * bf.get_height() + 14)
            field.draw(gui, box, i == self.focus)
            y += box.h + gap
        self.chips, self.check_rect = [], None
        if self.idea and with_chips:
            draw_text(scr, "Where in the game? (optional)", x, y, bf, WHITE)
            y += label_h
            for row in chip_rows:
                cx = x
                for area, cw in row:
                    r = pygame.Rect(cx, y, cw, chip_h)
                    self.button(gui, r, area, "area", True, area == self.area, False, fkey="small")
                    self.chips.append((r, area))
                    cx += cw + int(6 * fs)
                y += chip_h + int(6 * fs)
            y += gap
        if self.idea:
            box = pygame.Rect(x, y + (check_h - int(22 * fs)) // 2, int(22 * fs), int(22 * fs))
            round_rect(scr, box, gfx.FIELD_BG, 4, 2, GOLD if self.picture else gfx.FIELD_EDGE)
            if self.picture:
                pygame.draw.lines(scr, GOLD, False, [(box.x + box.w * 0.2, box.centery), (box.x + box.w * 0.42, box.bottom - box.h * 0.22),
                                                     (box.right - box.w * 0.18, box.y + box.h * 0.22)], max(2, int(3 * fs)))
            label = "Add a picture of the screen"
            draw_text(scr, label, box.right + int(10 * fs), y + check_h // 2, bf, WHITE, "midleft")
            self.check_rect = pygame.Rect(x, y, box.w + int(10 * fs) + bf.size(label)[0], check_h)
            y += check_h + gap
        if with_info:
            for ln in info_lines:
                draw_text(scr, ln, x, y, sf, DIM)
                y += sf.get_height()
            y += gap
        by = rect.bottom - bh - 18
        cw = max(gui.button_width("Cancel"), int(110 * fs))
        self.button(gui, pygame.Rect(rect.right - 28 - cw, by, cw, bh), "Cancel", "cancel")
        right = rect.right - 28 - cw - 12
        ready = self.ready()
        if self.send_url():
            wait = reporting.seconds_until_send_allowed()
            label = "Submit" if not wait else f"Submit (wait {wait}s)"
            sw = max(gui.button_width(label), int(150 * fs))
            self.button(gui, pygame.Rect(right - sw, by, sw, bh), label, "send", ready and not wait, True, ready and not wait)
        else:
            label = "Save idea file" if self.idea else "Save report file"
            sw = max(gui.button_width(label), int(190 * fs))
            self.button(gui, pygame.Rect(right - sw, by, sw, bh), label, "save", ready, True, ready)
        if not ready:
            hint = "Say what you'd like first." if self.idea else "Say what happened first."
            draw_text(scr, clip_text(hint, sf, max(10, right - sw - 12 - x)), x, by + bh // 2, sf, ORANGE, "midleft")

    def draw_result(self, gui):
        L, scr = gui.L, gui.screen
        fs = max(1.0, L.fs)
        tf, bf = gui.font("title", True), gui.font("body")
        pw = min(int(700 * fs), L.W - 30)
        bh = int(42 * fs)
        what = "idea" if self.idea else "report"
        files = self.rel_path()
        if self.picture_path:
            files += f"  (and the picture, {os.path.basename(self.picture_path)})"
        if self.phase in (self.WORKING, self.SENDING):
            heading, colour = "Sending...", CYAN
            body = f"Posting your {what} to Discord. This takes a few seconds."
        elif self.phase == self.SENT:
            heading, colour = "Sent - thank you!", GREEN
            body = f"{self.message}\nA copy is saved as {self.rel_path()}"
        elif self.phase == self.SAVED:
            heading, colour = ("Idea saved" if self.idea else "Report saved"), GREEN
            size = "" if self.idea else f"  ({reporting.describe_size(self.path)})"
            body = (f"Please send this file to {self.owner} on Discord:\n{files}{size}\n\n"
                    "Press Open folder, then drag the file into Discord.")
        else:
            heading, colour = "It did not go through", ORANGE
            if self.path:
                size = "" if self.idea else f"  ({reporting.describe_size(self.path)})"
                body = (f"{self.message}\n\nYour {what} is saved. Please send this file to {self.owner} on Discord instead:\n{files}"
                        f"{size}")
            else:
                body = self.message
        lines = wrap_text(body, bf, pw - 56)
        need = 20 + tf.get_height() + 14 + len(lines) * bf.get_height() + 24 + bh + 18
        rect = self.panel(gui, pw, need, real=True)
        x, y = rect.x + 28, rect.y + 20
        draw_text(scr, heading, x, y, tf, colour)
        y += tf.get_height() + 14
        for ln in lines:
            draw_text(scr, ln, x, y, bf, WHITE)
            y += bf.get_height()
        by = rect.bottom - bh - 18
        right = rect.right - 28
        if self.phase in (self.WORKING, self.SENDING):
            return
        cw = max(gui.button_width("Close"), int(120 * fs))
        self.button(gui, pygame.Rect(right - cw, by, cw, bh), "Close", "close", True, self.phase == self.SENT)
        right -= cw + 12
        if self.path:
            ow = max(gui.button_width("Open folder"), int(150 * fs))
            self.button(gui, pygame.Rect(right - ow, by, ow, bh), "Open folder", "folder", True, self.phase != self.SENT)
            right -= ow + 12
        if self.phase == self.FAILED and self.path and self.send_url() and self.retry_ok:
            rw = max(gui.button_width("Try again"), int(130 * fs))
            wait = reporting.seconds_until_send_allowed()
            self.button(gui, pygame.Rect(right - rw, by, rw, bh), "Try again", "retry", not wait)

    # ---- input
    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if self.phase == self.FORM:
            if name == "cancel":
                self.done = True
            elif name in ("tab_" + self.BUG, "tab_" + self.IDEA):
                self.switch_tab(name[4:])
            elif name == "send":
                self.submit(gui, True)
            elif name == "save":
                self.submit(gui, False)
            elif name == "area":
                area = next((a for r, a in self.chips if r.collidepoint(pos)), None)
                self.area = None if area == self.area else area          # a second click clears it
            elif self.idea and self.check_rect and self.check_rect.collidepoint(pos):
                self.picture = not self.picture
            else:
                for i, f in enumerate(self.fields):
                    if f.rect.collidepoint(pos):
                        self.focus = i
            return
        if name == "close":
            self.done = True
        elif name == "folder" and self.path:
            if not reporting.open_folder(os.path.dirname(self.path)):
                gui.say("Could not open the folder. It is called bug_reports, inside the game's folder.", ORANGE)
        elif name == "retry":
            self.submit(gui, True)

    def key(self, gui, ev):
        k, mod = ev.key, getattr(ev, "mod", 0)
        if self.phase != self.FORM:
            if k in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER) and self.phase not in (self.WORKING, self.SENDING):
                self.done = True
            return
        if k == pygame.K_ESCAPE:
            self.done = True
        elif k == pygame.K_TAB and mod & pygame.KMOD_CTRL:                 # Round FR1: Ctrl+Tab switches between the two tabs
            self.switch_tab(self.BUG if self.idea else self.IDEA)
        elif k == pygame.K_TAB:
            self.focus = (self.focus + (-1 if mod & pygame.KMOD_SHIFT else 1)) % len(self.fields)
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) and (mod & pygame.KMOD_CTRL or self.focus == 0):
            if self.focus == 0 and not mod & pygame.KMOD_CTRL:
                self.focus = 1
            else:
                self.submit(gui, bool(self.send_url()))
        else:
            self.fields[self.focus].key(ev, gui.read_clipboard)
