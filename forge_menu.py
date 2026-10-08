# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_menu.py - the deck screen: choose your deck, the deck the AI opponents play and how many of them, import a new
deck by pasting its text, then press Start. It is drawn by ForgeTable in place of the game (ForgeTable.menu).

    DeckMenu      the screen itself
    ImportDialog  "paste a deck" window (Ctrl+V, or drag a .txt file onto the window)
    read_clipboard()
    write_clipboard(text)

Only the drawing lives here; reading and saving decks is deck_library.py, and starting a game is ForgeTable.start_game().
"""
import os
import random
import threading

import pygame

import backup_banner
import deck_library as lib
import forge_dialogs as dlg
import formats
import gfx
import legality as lg
import reporting
from deck_importer import DeckImportError
from gfx import CYAN, DIM, GOLD, GREEN, ORANGE, RED, WHITE, clip_text, draw_text, round_rect, wrap_text

CARD_ASPECT = 488 / 680
MAX_OPPONENTS = 3
BLUE_TAG = gfx.TAG_BLUE
ORANGE_TAG = gfx.TAG_ORANGE
RANDOM = "*random*"                     # round 27b: an AI seat set to "a random deck from the library, picked at Start"
SEAT_KEYS = ("opp", "opp2", "opp3")     # the slot/active names of AI seats 1-3 ("opp" is seat 1, as before round 27b)
MAIN_MENU_LABEL = "Main menu"            # patch 42: the deck screen's button back to the title and main menu
HINTS = ("Pick your deck, then one for each AI (or Random), then press Start.  Type to search the list.  "
         "Paste a new deck with Import (or Ctrl+V).  F8: a bug or an idea.",
         "Pick your deck and the AIs', then Start.  Type to search.  Ctrl+V pastes a deck.  F8: a bug or an idea.")   # Round FR1

# Round 27b: pods known to freeze before turn 1 (a Forge engine bug, 2026-09-25 bug hunt: SIM_BUGHUNT_50GAMES_2026-09-25.md,
# seed 221). Each entry is a set of commander names; the warning shows when ALL of them are at the table.
KNOWN_HANG_PODS = [
    {"Valgavoth, Harrower of Souls", "Light-Paws, Emperor's Voice", "Kinnan, Bonder Prodigy", "Ojer Axonil, Deepest Might"},
]


def known_hang(commanders):
    """True when this set of commanders contains a pod that is known to freeze Forge before the game starts."""
    names = set(commanders)
    return any(pod <= names for pod in KNOWN_HANG_PODS)


def matches(entry, query):
    """Deck search (round 27b): every word must appear in the deck's name, its commanders or its file name (any case)."""
    words = query.lower().split()
    if not words:
        return True
    hay = " ".join([entry.name, " ".join(entry.commanders or []), os.path.basename(entry.path)]).lower()
    return all(w in hay for w in words)


def read_clipboard():
    """The text on the clipboard, or '' when it can't be read (the window then says so)."""
    try:
        import pygame.scrap as scrap
        scrap.init()
        text = scrap.get_text() if hasattr(scrap, "get_text") else None
        if not text and hasattr(scrap, "get"):
            raw = scrap.get(getattr(scrap, "SCRAP_TEXT", "text/plain;charset=utf-8"))
            text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        if text:
            return text.replace("\x00", "")
    except Exception:
        pass
    try:                                                     # tkinter ships with Python on Windows and reads the clipboard reliably
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""


def write_clipboard(text):
    """Puts text on the clipboard. Returns True on success (used by the licences dialog's Copy button)."""
    try:
        import pygame.scrap as scrap
        scrap.init()
        if hasattr(scrap, "put_text"):
            scrap.put_text(text)
            return True
        if hasattr(scrap, "put"):
            scrap.put(getattr(scrap, "SCRAP_TEXT", "text/plain;charset=utf-8"), text.encode("utf-8"))
            return True
    except Exception:
        pass
    try:                                                     # tkinter ships with Python on Windows and writes the clipboard reliably
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            return True
        finally:
            root.destroy()
    except Exception:
        return False


def _fs(gui):
    return gui.L.fs


def legal_tag(entry):
    """Round BAN1: ("NOT LEGAL", colour) / ("UNCHECKED", colour) / None for a deck. Never raises."""
    try:
        text = lg.label(entry.legality()) if entry is not None else None
    except Exception:
        return None
    if text is None:
        return None
    return text, (gfx.DEFEAT if text == "NOT LEGAL" else gfx.BTN_OFF_EDGE)


def draw_tag(gui, tag, right, cy, font):
    """A pill ending at x=right, centred on cy; returns its left edge (the tag style of the deck list rows)."""
    label, colour = tag
    fs = _fs(gui)
    w = font.size(label)[0] + int(16 * fs)
    r = pygame.Rect(right - w, cy - (font.get_height() + 6) // 2, w, font.get_height() + 6)
    round_rect(gui.screen, r, colour, r.h // 2)
    draw_text(gui.screen, label, r.centerx, r.centery, font, WHITE, "center")
    return r.x


class DeckMenu:
    """The start screen. `choice` = {"mine": deck id, "opps": [seat 1, seat 2, seat 3], "count": 1..3} where each seat is a deck id,
    None (= the same deck as mine) or RANDOM (round 27b). A choice saved before round 27b has one "opp" for every AI: still read.
    Round FMT1: the screen shows one format's decks at a time (the buttons beside the title). The choice also has "format" and
    "formats" ({format: {"mine", "opps", "count"}}), so each format remembers its own decks; "mine"/"opps"/"count" are the
    shown format's, as before."""

    def __init__(self, entries, choice=None, has_game=False, runtime=None, dirs=None):
        self.library_dir, self.sample_dir, self.base_dir = dirs or (lib.LIBRARY_DIR, lib.SAMPLE_DIR, lib.BASE_DIR)
        self.runtime = runtime
        self.entries = entries
        choice = choice or {}
        self.fmt = formats.normal(choice.get("format"))
        saved = choice.get("formats")
        self.saved = {k: dict(v) for k, v in saved.items() if k in formats.FORMATS and isinstance(v, dict)} \
            if isinstance(saved, dict) else {}
        self._apply(choice)
        self.active = "mine"
        self.focus_id = self.mine_id
        self.search = ""                                     # round 27b: typing filters the deck list
        self.rng = random.Random()                            # tests set a seed
        self.picked = {}                                      # seat index -> the deck a RANDOM seat got at the last Start
        self.scroll = 0
        self.has_game = has_game
        self.message = None                                   # (text, colour)
        self.rows, self.btns, self.slots = [], [], {}
        self.list_rect = pygame.Rect(0, 0, 0, 0)
        self.content_h = 0
        self.resume_info = None                               # {"actions", "when"} when an unfinished game can be resumed (round 21)
        self.crash_offer = ""                                   # round 28d: "closed unexpectedly last time - send a report?" (the table sets it)
        self.backup_banner = backup_banner.banner_text(self.base_dir)          # round 27a §2: checked once, when the screen opens
        if self.backup_banner:
            threading.Thread(target=backup_banner.maybe_alert_discord, args=(self.base_dir,), daemon=True).start()

    def _apply(self, choice):
        """Set mine / opps / count from a {"mine", "opps", "count"} choice, keeping only decks of the shown format."""
        try:
            self.count = min(MAX_OPPONENTS, max(1, int(choice.get("count") or 1)))
        except (TypeError, ValueError):
            self.count = 1
        pool = self.in_format()
        first = next((e for e in pool if e.ok), None)
        mine = lib.find(pool, choice.get("mine")) or first
        self.mine_id = mine.id if mine else None
        seats = choice.get("opps")
        if not isinstance(seats, list):
            seats = [choice.get("opp")] * MAX_OPPONENTS             # a pre-27b choice: one deck for every AI
        seats = (list(seats) + [None] * MAX_OPPONENTS)[:MAX_OPPONENTS]
        self.opps = [s if s == RANDOM or (s and lib.find(pool, s)) else None for s in seats]

    # ---- what is chosen ----------------------------------------------------------------
    def in_format(self, entries=None):
        """Round FMT1: the decks of the format the screen shows."""
        return [e for e in (self.entries if entries is None else entries)
                if formats.normal(getattr(e, "format", None)) == getattr(self, "fmt", formats.DEFAULT)]

    def set_format(self, fmt):
        """Round FMT1: show another format's decks. Each format keeps its own choice of decks and opponents."""
        fmt = formats.normal(fmt)
        if fmt == self.fmt:
            return
        self.saved[self.fmt] = {"mine": self.mine_id, "opps": list(self.opps), "count": self.count}
        self.fmt = fmt
        self._apply(self.saved.get(fmt) or {"count": self.count})
        self.active, self.focus_id, self.scroll = "mine", self.mine_id, 0
        self.picked = {}
        self.say(formats.BLURB[fmt] + ("" if self.in_format() else "  No decks yet: Import one."), DIM)

    def entry(self, deck_id):
        return lib.find(self.entries, deck_id)

    @property
    def mine(self):
        return self.entry(self.mine_id)

    # Seat 1 keeps its pre-27b names (opp_id / opp) so older callers and tests still read it.
    @property
    def opp_id(self):
        return self.opps[0]

    @opp_id.setter
    def opp_id(self, value):
        self.opps[0] = value

    @property
    def opp(self):
        return self.seat_entry(0)

    def seat_entry(self, i):
        """The deck AI seat i (0-based) plays: its own deck, mine when it is None, or None while it is RANDOM (picked at Start)."""
        s = self.opps[i]
        if s == RANDOM:
            return None
        return self.entry(s) if s else self.mine

    def active_seat(self):
        """0-2 when an AI seat's slot is the one the list sets, else None (my own deck)."""
        return SEAT_KEYS.index(self.active) if self.active in SEAT_KEYS else None

    def choice(self):
        # "opp" is kept for a copy of the program from before round 27b that reads the same settings.json
        now = {"mine": self.mine_id, "opps": list(self.opps), "count": self.count}
        return dict(now, opp=self.opps[0] if self.opps[0] != RANDOM else None, format=self.fmt,
                    formats=dict(self.saved, **{self.fmt: now}))

    def say(self, text, colour=DIM):
        self.message = (text, colour)

    def visible(self):
        """The deck list as shown: the shown format's decks (round FMT1), filtered by the search box."""
        pool = self.in_format()
        return [e for e in pool if matches(e, self.search)] if self.search.strip() else pool

    def refresh(self, entries=None):
        self.entries = entries if entries is not None else lib.list_decks(self.library_dir, self.sample_dir, self.base_dir)
        pool = {e.id for e in self.in_format()}
        for attr in ("mine_id", "focus_id"):
            if getattr(self, attr) and getattr(self, attr) not in pool:
                setattr(self, attr, None)
        self.opps = [s if s == RANDOM or (s and s in pool) else None for s in self.opps]
        if self.mine_id is None:
            first = next((e for e in self.in_format() if e.ok), None)
            self.mine_id = first.id if first else None

    def assign(self, entry):
        self.focus_id = entry.id
        seat = self.active_seat()
        if seat is None:
            self.mine_id = entry.id
            self.active = "opp"                                # the natural next question
        else:
            self.opps[seat] = None if entry.id == self.mine_id else entry.id
            if seat + 1 < self.count:
                self.active = SEAT_KEYS[seat + 1]              # ... and then the next AI, if there is one
        self.message = None

    def set_seat(self, seat, value):
        """The Same-as-mine (None) and Random (RANDOM) buttons of one AI seat."""
        self.opps[seat] = value
        self.active = SEAT_KEYS[seat]
        self.message = None

    def step_count(self, d):
        self.count = min(MAX_OPPONENTS, max(1, self.count + d))
        if self.active_seat() is not None and self.active_seat() >= self.count:
            self.active = SEAT_KEYS[self.count - 1]

    def set_search(self, text):
        self.search = text[:60]
        self.scroll = 0

    # ---- actions ------------------------------------------------------------------------
    def playable(self, e):
        return e is not None and e.ok and not any(blocking for _t, blocking in e.problems(self.runtime))

    def resolve_seats(self):
        """The deck each AI seat plays this game, RANDOM seats picked now: a playable deck from the library, preferring one that
        nobody else at the table plays. Returns (entries, None) or (None, the reason it can't start)."""
        chosen = [self.seat_entry(i) for i in range(self.count)]
        pool = [e for e in self.in_format() if self.playable(e)]          # round FMT1: the same format as mine
        self.picked = {}
        for i in range(self.count):
            if self.opps[i] != RANDOM:
                continue
            if not pool:
                return None, f"AI {i + 1} is set to Random, but no {formats.name(self.fmt)} deck in the library can be played."
            used = {self.mine_id} | {e.id for e in chosen if e is not None}
            fresh = [e for e in pool if e.id not in used]
            chosen[i] = self.rng.choice(fresh or pool)
            self.picked[i] = chosen[i]
        return chosen, None

    def start(self, gui, confirmed=False, seats=None, hang_ok=False):
        mine = self.mine
        if mine is None:
            return self.say("Your deck isn't chosen yet.", RED)
        for text, blocking in mine.problems(self.runtime):
            if blocking:
                return self.say(f"Your deck ({mine.name}): {text}", RED)
        if seats is None:
            for i in range(self.count):
                e = self.seat_entry(i)
                if self.opps[i] == RANDOM:
                    continue
                if e is None:
                    return self.say(f"AI {i + 1}'s deck isn't chosen yet.", RED)
                for text, blocking in e.problems(self.runtime):
                    if blocking:
                        return self.say(f"AI {i + 1} ({e.name}): {text}", RED)
            seats, problem = self.resolve_seats()
            if problem:
                return self.say(problem, RED)
        if self.has_game and not confirmed:
            gui.modal = dlg.QuestionDialog("Start a new game?", "This ends the game you are playing now.", "End it and start",
                                           "Keep playing", lambda: self.start(gui, True, seats, hang_ok))
            return None
        commanders = set(mine.commanders or [])
        for e in seats:
            commanders |= set(e.commanders or [])
        if not hang_ok and known_hang(commanders):
            gui.modal = dlg.QuestionDialog(
                "This pod is known to freeze",
                "These four commanders together make Forge freeze before turn 1 (a Forge bug found in the 2026-09-25 bug hunt). "
                "Change one deck, or start anyway to try it.", "Start anyway", "Change decks",
                lambda: self.start(gui, True, seats, True), enter_yes=False)
            return None
        error = gui.start_game(mine, list(seats), self.count, self.choice())
        if error:
            self.say(error, RED)
            return None
        notes = []
        if self.picked:
            notes.append("Random: " + ",  ".join(f"AI {i + 1} plays {e.name}" for i, e in sorted(self.picked.items())))
        left_out = []                                   # round 28d (F2): never drop a card silently - say so as the game starts
        for who, e in [("your deck", mine)] + [(f"AI {i + 1}", e) for i, e in enumerate(seats)]:
            missing = lib.unknown_in(e, self.runtime)
            if missing:
                left_out.append(f"{who}: " + ", ".join(missing[:4]) + (f" +{len(missing) - 4}" if len(missing) > 4 else ""))
        if left_out:
            notes.append("Not in this version of Forge yet, so left out - " + ";  ".join(left_out))
        if notes and hasattr(gui, "say"):
            gui.say(".   ".join(notes), ORANGE if left_out else CYAN, 10.0 if left_out else 6.0)
        return None

    def can_go_to_title(self, gui):
        """Patch 42: True when Esc (and the Main menu button) goes back to the title and main menu: no game running, and the
        program came through the title (main(); not the --deck command line or a test)."""
        return not self.has_game and bool(getattr(gui, "title_ok", False))

    def back(self, gui):
        if self.has_game:
            gui.menu = None
        elif getattr(gui, "title_ok", False):                # Round AD2: no game yet - Esc goes back to the title and main menu
            gui.open_title()

    def open_import(self, gui, text=""):
        gui.modal = ImportDialog(self, text)

    def imported(self, entry):
        if formats.normal(getattr(entry, "format", None)) != self.fmt:
            self.set_format(entry.format)                     # round FMT1: show the format the new deck was saved as
        self.refresh()
        self.set_search("")                                   # the new deck must be in the list that is shown
        self.assign(entry)
        self.scroll_to(entry.id)
        self.say(f"Saved '{entry.name}' in the deck library.", GREEN)

    def open_art(self, gui):
        """Round ALT1: the card-art picker for the deck in focus."""
        e = self.entry(self.focus_id)
        if e is None or not e.load().ok:
            return self.say("Click a deck in the list first, then press Card art.", DIM)
        import art_picker
        gui.modal = art_picker.ArtPicker(self, e)
        return None

    def open_stats(self, gui):
        """Patch 44: the Stats page for the deck in focus (or every deck, when none is chosen)."""
        import stats_view
        rec = getattr(gui, "stats_rec", None)
        current = rec.id if rec is not None and rec.end is None else None       # the game on the table isn't finished yet
        gui.modal = stats_view.StatsPage(self.entry(self.focus_id), current_id=current)
        return None

    def remove_focus(self, gui):
        e = self.entry(self.focus_id)
        if e is None:
            return self.say("Click a deck in the list first, then press Remove.", DIM)
        if e.builtin:
            return self.say("The sample decks that come with the program can't be removed.", ORANGE)
        gui.modal = dlg.QuestionDialog(f"Remove '{e.name}'?", "It leaves this list; the file is moved to my_decks/_removed/, "
                                       "so you can still get it back.", "Remove", "Keep it", lambda: self._remove(e))
        return None

    def _remove(self, e):
        try:
            lib.remove(e, self.library_dir)
        except (DeckImportError, OSError) as err:
            return self.say(str(err), RED)
        self.refresh()
        return self.say(f"Removed '{e.name}'.", DIM)

    def scroll_to(self, deck_id):
        for i, e in enumerate(self.visible()):
            if e.id == deck_id:
                self.scroll = max(0, i * self.row_h_cache - self.list_rect.h // 3) if getattr(self, "row_h_cache", 0) else 0

    # ---- input --------------------------------------------------------------------------
    def click(self, gui, pos, button):
        if button != 1:
            return
        for rect, name in reversed(self.btns):
            if rect.collidepoint(pos):
                return self.press(gui, name)
        for rect, entry in self.rows:
            if rect.collidepoint(pos) and self.list_rect.collidepoint(pos):
                return self.assign(entry)
        for name, rect in self.slots.items():
            if rect.collidepoint(pos):
                self.active = name
                return None
        return None

    def press(self, gui, name):
        if name == "start":
            self.start(gui)
        elif name == "back":
            self.back(gui)
        elif name == "main_menu":                                     # patch 42
            if self.can_go_to_title(gui):
                gui.open_title()
        elif name == "resume":
            gui.resume_last_game()
        elif name == "discard_saved":                                 # round 28d
            gui.discard_last_game()
        elif name == "import":
            self.open_import(gui)
        elif name == "remove":
            self.remove_focus(gui)
        elif name == "card_art":                                      # round ALT1
            self.open_art(gui)
        elif name == "stats":                                         # patch 44
            self.open_stats(gui)
        elif name == "minus":
            self.step_count(-1)
        elif name == "plus":
            self.step_count(1)
        elif name.startswith("same") or name.startswith("random"):      # round 27b: one pair of buttons per AI seat
            base = "same" if name.startswith("same") else "random"
            seat = int(name[len(base):] or 1) - 1
            self.set_seat(seat, None if base == "same" else RANDOM)
        elif name == "slot_mine":
            self.active = "mine"
        elif name.startswith("slot_opp"):
            self.active = name[len("slot_"):]
        elif name == "clear_search":
            self.set_search("")
        elif name.startswith("fmt_"):                                   # round FMT1: Commander / Brawl
            self.set_format(name[len("fmt_"):])
        elif name in ("crash_send", "crash_look", "crash_dismiss"):    # round 28d
            gui.crash_report(name[len("crash_"):])
        elif name in ("host_online", "join_online"):                  # Round MP1: play a friend over the internet
            if not formats.get(self.fmt).online:                       # round FMT1: online play is Commander only for now
                return self.say(f"Online games are Commander only for now. Switch to Commander (top right) to "
                                f"{'host' if name == 'host_online' else 'join'} one.", ORANGE)
            problem = (gui.open_host if name == "host_online" else gui.open_join)(self.mine)
            if problem:
                self.say(problem, RED)
        elif name == "open_backup_log":
            reporting.open_folder(os.path.join(self.base_dir, "backup.log"))

    def wants_key(self, ev):
        """True when this key is typing into the search box, so the table must not use it for something else (the +/- text size)."""
        ch = getattr(ev, "unicode", "") or ""
        return bool(self.search) and ch.isprintable() and ch != "" and not getattr(ev, "mod", 0) & pygame.KMOD_CTRL

    def key(self, gui, ev):
        k, mod = ev.key, getattr(ev, "mod", 0)
        ch = getattr(ev, "unicode", "") or ""
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.start(gui)
        elif k == pygame.K_ESCAPE:
            if self.search:
                self.set_search("")                           # Esc clears the search first
            else:
                self.back(gui)
        elif k == pygame.K_TAB:
            order = ["mine"] + list(SEAT_KEYS[:self.count])
            i = order.index(self.active) if self.active in order else 0
            self.active = order[(i + (-1 if mod & pygame.KMOD_SHIFT else 1)) % len(order)]
        elif k == pygame.K_v and mod & pygame.KMOD_CTRL:
            self.open_import(gui, gui.read_clipboard())
        elif k in (pygame.K_UP, pygame.K_DOWN):
            self.move_focus(-1 if k == pygame.K_UP else 1)
        elif k in (pygame.K_LEFT, pygame.K_RIGHT):
            self.step_count(-1 if k == pygame.K_LEFT else 1)
        elif k == pygame.K_BACKSPACE:
            self.set_search(self.search[:-1] if not mod & pygame.KMOD_CTRL else "")
        elif ch and ch.isprintable() and not mod & (pygame.KMOD_CTRL | pygame.KMOD_ALT):
            self.set_search(self.search + ch)                 # round 27b: typing anywhere on the screen searches the list

    def move_focus(self, d):
        shown = self.visible()
        ids = [e.id for e in shown]
        if not ids:
            return
        i = ids.index(self.focus_id) if self.focus_id in ids else -1
        e = shown[max(0, min(len(ids) - 1, i + d))]
        side = self.active                                    # arrow keys change the deck for the side you are on
        self.assign(e)
        self.active = side

    def wheel(self, gui, dy):
        if self.list_rect.collidepoint(gui.mouse):
            self.scroll = max(0, min(self.scroll - dy * int(50 * _fs(gui) * 0.6), max(0, self.content_h - self.list_rect.h)))

    def drop_file(self, gui, path):
        ext = os.path.splitext(path)[1].lower()
        if os.path.isdir(path) or ext in (".zip", ".png", ".jpg", ".jpeg"):     # patch 40: card pictures, not a deck list
            return self.say("That looks like card pictures: click the deck in the list, press Card art, and drop it there "
                            "(they go into that deck only).", GOLD)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            return self.say(f"Could not read that file: {e}", RED)
        return self.open_import(gui, text)

    # ---- drawing ------------------------------------------------------------------------
    def button(self, gui, rect, label, name, enabled=True, primary=False, focus=False, fkey="btn"):
        gui.draw_button(rect, label, name, enabled, primary, focus, hit=False, fkey=fkey)
        if enabled:
            self.btns.append((pygame.Rect(rect), name))

    def draw(self, gui):
        L, scr = gui.L, gui.screen
        fs = _fs(gui)
        self.rows, self.btns, self.slots = [], [], {}
        m = int(max(14, 16 * fs))
        big, title, body, small, tiny = (gui.font("big", True), gui.font("title", True), gui.font("body"), gui.font("small"),
                                         gui.font("tiny", True))
        tx, self.main_menu_rect = m, None
        if self.can_go_to_title(gui):
            # Patch 42: a Main menu button at the top left, shown exactly when Esc goes back to the title and main menu (no game
            # running, and the program came through the title). Before, only Esc did it, and nothing on the screen said so.
            mh = int(min(big.get_height(), max(30, 38 * fs)))
            mw = max(gui.button_width(MAIN_MENU_LABEL, "small"), int(120 * fs))
            r = pygame.Rect(m, m - 4 + (big.get_height() - mh) // 2, mw, mh)
            self.button(gui, r, MAIN_MENU_LABEL, "main_menu", True, False, False, fkey="small")
            self.main_menu_rect = r
            tx = r.right + int(16 * fs)
        title_text = "Choose your decks"
        self.formats_left = None
        self.draw_formats(gui, L.W - m, m - 4, big.get_height(), tx + big.size(title_text)[0] + int(24 * fs))
        room = (self.formats_left or L.W - m) - int(16 * fs) - tx
        self.title_rect = draw_text(scr, clip_text(title_text, big, room), tx, m - 4, big, GOLD)
        y = m - 4 + big.get_height()
        hint = HINTS[0] if small.size(HINTS[0])[0] <= L.W - 2 * m else HINTS[1]        # Round FR1: the F8 hint, a shorter line at big text
        draw_text(scr, clip_text(hint, small, L.W - 2 * m), m, y, small, DIM)
        y += small.get_height() + int(6 * fs)
        if self.crash_offer:                                  # round 28d: the last run didn't close properly (last_session.py)
            ban_h = int(max(30, 34 * fs))
            ban = pygame.Rect(m, y, L.W - 2 * m, ban_h)
            round_rect(scr, ban, gfx.BANNER_RED_BG, 8, 1, RED)
            bx = ban.right - int(8 * fs)
            by = ban.y + (ban.h - int(28 * fs)) // 2
            for label, name in (("Not now", "crash_dismiss"), ("Look first", "crash_look"), ("Send report", "crash_send")):
                bw = max(gui.button_width(label), int(100 * fs))
                bx -= bw
                self.button(gui, pygame.Rect(bx, by, bw, int(28 * fs)), label, name, True, name == "crash_send", False, fkey="small")
                bx -= int(6 * fs)
            draw_text(scr, clip_text(self.crash_offer, small, bx - ban.x - int(18 * fs)), ban.x + int(12 * fs),
                      ban.centery, small, WHITE, "midleft")
            y = ban.bottom + int(8 * fs)
        if self.backup_banner:                                # round 27a §2: shown until backup_status.json looks healthy again
            ban_h = int(max(30, 34 * fs))
            ban = pygame.Rect(m, y, L.W - 2 * m, ban_h)
            round_rect(scr, ban, gfx.BANNER_ORANGE_BG, 8, 1, ORANGE)
            bw = max(gui.button_width("Open backup.log"), int(160 * fs))
            btn = pygame.Rect(ban.right - int(8 * fs) - bw, ban.y + (ban.h - int(28 * fs)) // 2, bw, int(28 * fs))
            self.button(gui, btn, "Open backup.log", "open_backup_log", True, False, False, fkey="small")
            draw_text(scr, clip_text(self.backup_banner, small, btn.x - ban.x - int(24 * fs)), ban.x + int(12 * fs),
                      ban.centery, small, ORANGE, "midleft")
            y = ban.bottom + int(8 * fs)
        top = y + m
        bh = int(max(46, 52 * fs))
        msg_h = small.get_height() * 2 + 4
        disc_h = tiny.get_height() + 2
        bottom = L.H - m - disc_h - 4
        act_y = bottom - bh
        msg_y = act_y - msg_h - 4
        area = pygame.Rect(m, top, L.W - 2 * m, max(120, msg_y - 6 - top))
        lw = int((area.w - m) * 0.48)
        left = pygame.Rect(area.x, area.y, lw, area.h)
        right = pygame.Rect(left.right + m, area.y, area.w - lw - m, area.h)
        # Round UI2: the Start row only needs the right-hand side; when its buttons leave the left column free, the deck list
        # runs down to the bottom (at 200% text it showed about one and a half decks)
        sw0 = max(gui.button_width("Start game"), int(240 * fs))
        row_left = L.W - m - sw0 - (12 + max(gui.button_width("Back to the game"), int(200 * fs)) if (self.has_game or self.resume_info) else 0)
        if self.resume_info and not self.has_game:
            row_left = min(row_left, L.W - m - sw0 - 12 - int(280 * fs))
        online = None
        if hasattr(gui, "open_host"):                         # Round MP1's Host / Join share the Start row
            o_labels = ("Host online", "Join online")
            o_w = max(max(gui.button_width(t) for t in o_labels), int(150 * fs))
            if 2 * o_w + 10 > (L.W - 2 * m - sw0 - 12) * 0.55:
                o_labels = ("Host", "Join")
                o_w = max(max(gui.button_width(t) for t in o_labels), int(80 * fs))
            if row_left - (2 * o_w + 22) < right.x and o_labels[0] != "Host":
                o_labels = ("Host", "Join")                   # Round UI2: the short labels if that lets the list run down
                o_w = max(max(gui.button_width(t) for t in o_labels), int(80 * fs))
            online = (o_labels, o_w)
            row_left -= 2 * o_w + 10 + 12                     # they go just left of the rest of the row when the list is tall
        tall_list = row_left >= right.x
        if tall_list:
            left.h = bottom - top
        self.draw_library(gui, left, m, bh)
        self.draw_play(gui, right, m)
        # message + start row
        if self.message:
            text, colour = self.message
            mx = right.x if tall_list else m
            for i, ln in enumerate(wrap_text(text, small, L.W - m - mx)[:2]):
                draw_text(scr, ln, mx, msg_y + i * small.get_height(), small, colour)
        ok = self.mine is not None and all(self.seat_entry(i) is not None or self.opps[i] == RANDOM for i in range(self.count))
        sw = max(gui.button_width("Start game"), int(240 * fs))
        self.button(gui, pygame.Rect(L.W - m - sw, act_y, sw, bh), "Start game", "start", ok, True, ok)
        # Round MP1: play online, on the left of the same row. Your deck plays; the friend brings their own.
        online_w = 0
        if online:
            labels, ow = online
            ox = right.x if tall_list else m                    # Round UI2: beside Start when the deck list runs to the bottom
            for i, (label, name) in enumerate(zip(labels, ("host_online", "join_online"))):
                self.button(gui, pygame.Rect(ox + i * (ow + 10), act_y, ow, bh), label, name, self.mine is not None)
            online_w = 2 * ow + 10 + 12
        if self.has_game:
            bw = min(max(gui.button_width("Back to the game"), int(200 * fs)), L.W - 2 * m - sw - 12 - online_w)
            self.button(gui, pygame.Rect(L.W - m - sw - 12 - bw, act_y, bw, bh), "Back to the game", "back")
        elif self.resume_info:
            stale = self.resume_info.get("stale")          # round 28d: saved by another Forge / bridge - can't be replayed
            label = (f"Saved game ({self.resume_info['when']}) is from an older version and can't be resumed - Discard" if stale
                     else f"Resume last game  ({self.resume_info['actions']} actions, {self.resume_info['when']})")
            room = L.W - 2 * m - sw - 12 - online_w
            rw = min(room, max(gui.button_width(label, "small"), int(280 * fs)))
            self.button(gui, pygame.Rect(L.W - m - sw - 12 - rw, act_y, rw, bh), clip_text(label, gui.font("small", True), rw - 16),
                        "discard_saved" if stale else "resume", True, False, False, fkey="small")
        disclaimer = ("Manticore is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by "
                      "Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.  "
                      "·  Cog > Licenses and credits")
        dln = wrap_text(disclaimer, tiny, L.W - 2 * m)[:1]
        if dln:
            draw_text(scr, dln[0], m, L.H - m - tiny.get_height(), tiny, DIM)

    def draw_formats(self, gui, right, y, h, left_limit):
        """Round FMT1: "Format  [Commander] [Brawl]" ending at x=right on the title row; the shown format's button is lit."""
        fs = _fs(gui)
        labels = [(formats.name(k), "fmt_" + k, k == self.fmt) for k in formats.ORDER]
        for fkey in ("btn", "small"):
            widths = [max(gui.button_width(t, fkey), int((110 if fkey == "btn" else 80) * fs)) for t, _n, _on in labels]
            bh = int(min(h, max(30, 38 * fs))) if fkey == "btn" else int(min(h, max(26, 30 * fs)))
            cap = gui.font("small", True)
            total = sum(widths) + 6 * (len(widths) - 1) + cap.size("Format")[0] + int(12 * fs)
            if right - total >= left_limit or fkey == "small":
                break
        x = right - sum(widths) - 6 * (len(widths) - 1)
        by = y + (h - bh) // 2
        self.formats_left = x - int(12 * fs) - cap.size("Format")[0]          # patch 42: the title is clipped to end before it
        draw_text(gui.screen, "Format", x - int(12 * fs), by + bh // 2, cap, DIM, "midright")
        self.format_rects = {}
        for (label, name, on), w in zip(labels, widths):
            r = pygame.Rect(x, by, w, bh)
            self.button(gui, r, label, name, True, on, False, fkey=fkey)
            self.format_rects[name] = r
            x += w + 6

    def draw_library(self, gui, rect, m, bh):
        scr, fs = gui.screen, _fs(gui)
        round_rect(scr, rect, gfx.MENU_CARD_BG, 14, 1, gfx.MENU_EDGE)
        title, body, small, tiny = gui.font("title", True), gui.font("body", True), gui.font("small"), gui.font("tiny", True)
        pad = int(12 * fs)
        shown = self.visible()
        draw_text(scr, "Deck library", rect.x + pad, rect.y + pad - 2, title, WHITE)
        kind = "" if self.fmt == formats.DEFAULT else formats.name(self.fmt) + " "         # round FMT1: "4 Brawl decks"
        total = len(self.in_format())
        count = f"{len(shown)} of {total} {kind}decks" if self.search.strip() else f"{total} {kind}decks"
        draw_text(scr, count, rect.right - pad, rect.y + pad + 2, small, DIM, "topright")
        foot_h = int(max(40, 44 * fs))
        # round 27b: the search box. Typing anywhere on the screen fills it; Esc or the x clears it.
        sy = rect.y + pad + title.get_height() + 6
        sh = small.get_height() + int(12 * fs)
        box = pygame.Rect(rect.x + pad // 2, sy, rect.w - pad, sh)
        self.search_rect = box
        round_rect(scr, box, gfx.SEARCH_BG, 8, 2 if self.search else 1, GOLD if self.search else gfx.MENU_EDGE)
        tx = box.x + int(10 * fs)
        if self.search:
            cw = sh
            clear = pygame.Rect(box.right - cw - 2, box.y + 2, cw, sh - 4)
            self.button(gui, clear, "x", "clear_search", True, False, False, fkey="small")
            draw_text(scr, clip_text(self.search, small, clear.x - tx - 8) + "|", tx, box.centery, small, WHITE, "midleft")
        else:
            draw_text(scr, clip_text("Type to search: name, commander or style (Typal, Voltron, ...)", small, box.w - 2 * (tx - box.x)),
                      tx, box.centery, small, DIM, "midleft")
        list_top = box.bottom + 6
        self.list_rect = pygame.Rect(rect.x + pad // 2, list_top, rect.w - pad, rect.bottom - foot_h - pad - list_top)
        row_h = int(2 * body.get_height() + 16 * fs)
        self.one_line = self.list_rect.h < 4 * (row_h + 6)              # Round UI2: a short list (big text) gets one-line rows
        if self.one_line:
            row_h = int(body.get_height() + 12 * fs)
        self.row_h_cache = row_h + 6
        self.content_h = len(shown) * (row_h + 6)
        self.scroll = max(0, min(self.scroll, max(0, self.content_h - self.list_rect.h)))
        scr.set_clip(self.list_rect)
        y = self.list_rect.y - self.scroll
        for e in shown:
            r = pygame.Rect(self.list_rect.x, y, self.list_rect.w - int(8 * fs), row_h)
            if r.bottom >= self.list_rect.y and r.y <= self.list_rect.bottom:
                self.draw_row(gui, r, e, body, small, tiny)
                self.rows.append((r, e))
            y += row_h + 6
        if not shown and self.search.strip():
            draw_text(scr, f"No deck matches \"{self.search.strip()}\".  Esc clears the search.", self.list_rect.x + pad,
                      self.list_rect.y + pad, small, DIM)
        elif not shown:                                                   # round FMT1: a format with no decks yet
            for i, ln in enumerate(wrap_text(f"No {formats.name(self.fmt)} decks yet. Press Import a deck and choose "
                                             f"{formats.name(self.fmt)}.", small, self.list_rect.w - 2 * pad)[:3]):
                draw_text(scr, ln, self.list_rect.x + pad, self.list_rect.y + pad + i * small.get_height(), small, DIM)
        scr.set_clip(None)
        if self.content_h > self.list_rect.h:
            frac = self.list_rect.h / self.content_h
            bar_h = max(30, int(self.list_rect.h * frac))
            bar_y = self.list_rect.y + int((self.list_rect.h - bar_h) * (self.scroll / max(1, self.content_h - self.list_rect.h)))
            pygame.draw.rect(scr, gfx.SCROLLBAR, pygame.Rect(self.list_rect.right - 5, bar_y, 5, bar_h), border_radius=3)
        by = rect.bottom - foot_h - pad // 2
        e = self.entry(self.focus_id)
        labels = ["Import a deck", "Remove", "Card art", "Stats"]           # round ALT1: Card art; patch 44: Stats
        widths = [max(gui.button_width("Import a deck"), int(190 * fs)), max(gui.button_width("Remove"), int(120 * fs)),
                  max(gui.button_width("Card art"), int(120 * fs)), max(gui.button_width("Stats"), int(100 * fs))]
        room = rect.w - 2 * pad - 30
        if sum(widths) > room:                                              # big text: the buttons only as wide as their words
            widths = [gui.button_width(t) for t in labels]
        if sum(widths) > room:
            labels[2], widths[2] = "Art", gui.button_width("Art")
        bx = rect.x + pad
        self.button(gui, pygame.Rect(bx, by, widths[0], foot_h), labels[0], "import", True, False, False)
        bx += widths[0] + 10
        self.button(gui, pygame.Rect(bx, by, widths[1], foot_h), labels[1], "remove", bool(e and not e.builtin))
        bx += widths[1] + 10
        if bx + widths[2] <= rect.right - pad // 2:
            self.button(gui, pygame.Rect(bx, by, widths[2], foot_h), labels[2], "card_art", bool(e and e.ok))
            bx += widths[2] + 10
            if bx + widths[3] <= rect.right - pad // 2:
                self.button(gui, pygame.Rect(bx, by, widths[3], foot_h), labels[3], "stats", True)

    def row_tags(self, e):
        """The coloured tags on a list row: YOU, and which AI seats play this deck (random seats are not tagged)."""
        tags = []
        if e.id == self.mine_id:
            tags.append(("YOU", BLUE_TAG))
        seats = [str(i + 1) for i in range(self.count) if self.opps[i] != RANDOM and (self.opps[i] or self.mine_id) == e.id]
        if seats:
            tags.append(("AI" if self.count == 1 else "AI " + ",".join(seats), ORANGE_TAG))
        legal = legal_tag(e)                                  # Round BAN1: NOT LEGAL / UNCHECKED
        if legal:
            tags.append(legal)
        return tags

    def draw_row(self, gui, r, e, body, small, tiny):
        scr, fs = gui.screen, _fs(gui)
        hot = r.collidepoint(gui.mouse) and self.list_rect.collidepoint(gui.mouse)
        focus = e.id == self.focus_id
        round_rect(scr, r, gfx.ROW_HOT_BG if hot else gfx.ROW_BG, 10, 2 if focus else 1, GOLD if focus else gfx.ROW_EDGE)
        x = r.right - int(10 * fs)
        for label, colour in self.row_tags(e):
            w = tiny.size(label)[0] + int(16 * fs)
            th = tiny.get_height() + 6
            tr = pygame.Rect(x - w, (r.y + (r.h - th) // 2) if getattr(self, "one_line", False) else r.y + int(8 * fs), w, th)
            round_rect(scr, tr, colour, tr.h // 2)
            draw_text(scr, label, tr.centerx, tr.centery, tiny, WHITE, "center")
            x -= w + 6
        tx = r.x + int(12 * fs)
        room = max(40, x - tx - 4)
        if getattr(self, "one_line", False):                        # Round UI2: the name only; the deck's box shows the rest
            draw_text(scr, clip_text(e.name, body, room), tx, r.centery, body, WHITE if not e.error else RED, "midleft")
            return
        draw_text(scr, clip_text(e.name, body, room), tx, r.y + int(7 * fs), body, WHITE if not e.error else RED)
        draw_text(scr, clip_text(e.summary(), small, r.right - tx - int(10 * fs)), tx, r.y + int(7 * fs) + body.get_height() + 2, small,
                  RED if e.error else DIM)

    def draw_play(self, gui, rect, m):
        scr, fs = gui.screen, _fs(gui)
        title, small = gui.font("title", True), gui.font("small")
        ctl_h = int(max(56, 62 * fs))
        gap = int(10 * fs)
        room = rect.h - ctl_h - gap * (self.count + 1)
        mine_h = room // 2 if self.count == 1 else int(room * (0.40 if self.count == 2 else 0.31))
        seat_h = (room - mine_h) // self.count
        a = pygame.Rect(rect.x, rect.y, rect.w, mine_h)
        self.draw_slot(gui, a, "YOUR DECK", self.mine, self.active == "mine", "mine")
        self.slots = {"mine": a}
        y = a.bottom + gap
        for i in range(self.count):
            b = pygame.Rect(rect.x, y, rect.w, seat_h)
            label = "THE AI OPPONENT PLAYS" if self.count == 1 else f"AI {i + 1} PLAYS"
            self.draw_slot(gui, b, label, self.seat_entry(i), self.active == SEAT_KEYS[i], SEAT_KEYS[i], seat=i)
            self.slots[SEAT_KEYS[i]] = b
            y = b.bottom + gap
        c = pygame.Rect(rect.x, y, rect.w, ctl_h)
        round_rect(scr, c, gfx.MENU_CARD_BG, 14, 1, gfx.MENU_EDGE)
        pad = int(14 * fs)
        draw_text(scr, "Opponents", c.x + pad, c.centery, title, WHITE, "midleft")
        x = c.x + pad + title.size("Opponents")[0] + int(20 * fs)
        sq = int(c.h * 0.62)
        self.button(gui, pygame.Rect(x, c.centery - sq // 2, sq, sq), "-", "minus", self.count > 1)
        draw_text(scr, str(self.count), x + sq + int(18 * fs), c.centery, gui.font("big", True), GOLD, "center")
        self.button(gui, pygame.Rect(x + sq + int(36 * fs), c.centery - sq // 2, sq, sq), "+", "plus", self.count < MAX_OPPONENTS)
        note_x = x + 2 * sq + int(56 * fs)
        note = {1: "A two-player game.", 2: "A three-player pod.", 3: "A four-player pod."}[self.count]
        life = formats.life_note(self.fmt, self.count + 1)                 # round FMT1: "25 life each." in Brawl
        if life:
            note += "  " + life
        draw_text(scr, clip_text(note, small, c.right - note_x - pad), note_x, c.centery, small, DIM, "midleft")

    def draw_slot(self, gui, rect, label, entry, active, name, seat=None):
        """One slot on the right: my deck, or one AI seat. Nothing it draws may spill out of the slot (round 27b)."""
        if active:
            gfx.glow(gui.screen, rect, GOLD, 12, 2, 3)
        old = gui.screen.get_clip()
        gui.screen.set_clip(rect.inflate(2, 2).clip(old) if old else rect.inflate(2, 2))
        try:
            self._draw_slot(gui, rect, label, entry, active, name, seat)
        finally:
            gui.screen.set_clip(old)

    def _draw_slot(self, gui, rect, label, entry, active, name, seat=None):
        scr, fs = gui.screen, _fs(gui)
        title, body, small, tiny = gui.font("title", True), gui.font("body"), gui.font("small"), gui.font("tiny", True)
        round_rect(scr, rect, gfx.MENU_ITEM_ACTIVE_BG if active else gfx.MENU_ITEM_BG, 14, 2 if active else 1, GOLD if active else gfx.MENU_EDGE)
        pad = int(14 * fs)
        head_y = rect.y + int(10 * fs)
        draw_text(scr, label, rect.x + pad, head_y, tiny, GOLD if active else DIM)
        head_h = tiny.get_height()
        value = self.opps[seat] if seat is not None else None
        right = rect.right - pad
        if seat is not None:
            # round 27b: each AI seat has its own Random and Same-as-mine buttons, in the heading row
            suffix = "" if seat == 0 else str(seat + 1)
            bh = int(max(24, 28 * fs))
            bh = min(bh, max(20, rect.bottom - 2 - (head_y - int(4 * fs))))     # Round AD1: a slot a few px shorter (taller title text above it) still holds its buttons
            for text, bname, shown in (("Same as mine", "same" + suffix, value is not None),
                                       ("Random", "random" + suffix, value != RANDOM)):
                if not shown:
                    continue
                bw = max(gui.button_width(text, "small"), int(96 * fs))
                self.button(gui, pygame.Rect(right - bw, head_y - int(4 * fs), bw, bh), text, bname, True, False, False, fkey="small")
                right -= bw + int(8 * fs)
            head_h = max(head_h, bh - int(4 * fs))
        top = head_y + head_h + int(8 * fs)
        if rect.bottom - pad // 2 - top < title.get_height():
            # round 27b: too short for the full card (a four-player pod in a small window, or big text): one line in the heading
            if value == RANDOM:
                text = "Random deck"
            elif entry is None:
                text = "Nothing chosen"
            elif seat is not None and value is None:
                text = f"Same as yours ({entry.name})"
            else:
                text = entry.name
            lx = rect.x + pad + tiny.size(label)[0] + int(12 * fs)
            tag = legal_tag(entry) if value != RANDOM else None          # Round BAN1
            if tag and right - lx > tiny.size(tag[0])[0] + int(60 * fs):
                right = draw_tag(gui, tag, right - int(4 * fs), head_y + tiny.get_height() // 2, tiny) - int(6 * fs)
            if right - lx > 30:
                draw_text(scr, clip_text(text, small, right - lx - int(8 * fs)), lx, head_y + tiny.get_height() // 2, small,
                          RED if entry is None and value != RANDOM else WHITE, "midleft")
            return
        if seat is not None:
            if active and right - (rect.x + pad + tiny.size(label)[0]) > tiny.size("click a deck in the list")[0] + 2 * pad:
                draw_text(scr, "click a deck in the list", right - int(4 * fs), head_y, tiny, GOLD, "topright")
        elif active:
            draw_text(scr, "click a deck in the list to choose it", rect.right - pad, head_y, tiny, GOLD, "topright")
        th = max(10, rect.bottom - pad - top)
        tw = int(th * CARD_ASPECT)
        x = rect.x + pad
        if th < max(40, int(36 * fs)):                        # too small to recognise: leave the picture out
            tw = -pad
        elif entry is not None and entry.commanders:
            self.draw_thumb(gui, entry.commanders[0], x, top, tw, th)
        else:
            box = pygame.Rect(x, top, tw, th)
            round_rect(scr, box, gfx.MENU_SEARCH_BG, 6, 1, gfx.MENU_EDGE)
            if value == RANDOM:
                draw_text(scr, "?", box.centerx, box.centery, gui.font("big", True), GOLD, "center")
        tx = x + tw + pad
        tw_text = rect.right - pad - tx
        bottom = rect.bottom - pad // 2
        if value == RANDOM:
            draw_text(scr, "Random deck", tx, top, title, WHITE)
            if top + title.get_height() + small.get_height() <= bottom:
                for ln in wrap_text("A playable deck from your library is picked when you press Start, one nobody else at the table "
                                    "plays if there is one.", small, tw_text)[:3]:
                    if top + title.get_height() + 4 + small.get_height() > bottom:
                        break
                    draw_text(scr, ln, tx, top + title.get_height() + 4, small, DIM)
                    top += small.get_height()
            return
        if entry is None:
            draw_text(scr, "Nothing chosen", tx, top, title, RED)
            return
        ty = top
        if seat is not None and value is None:
            draw_text(scr, "Same deck as yours", tx, ty, title, WHITE)
            ty += title.get_height() + 2
            if ty + body.get_height() > bottom:
                return
            draw_text(scr, clip_text(entry.name, body, tw_text), tx, ty, body, DIM)
            ty += body.get_height() + 4
        else:
            name_w = tw_text
            tag = legal_tag(entry)                                       # Round BAN1: next to the deck's name
            if tag and tw_text > tiny.size(tag[0])[0] + int(80 * fs):
                name_w = draw_tag(gui, tag, tx + tw_text, ty + title.get_height() // 2, tiny) - int(8 * fs) - tx
            draw_text(scr, clip_text(entry.name, title, name_w), tx, ty, title, WHITE)
            ty += title.get_height() + 4
        if entry.error:
            for ln in wrap_text(entry.error, small, tw_text)[:3]:
                if ty + small.get_height() > bottom:
                    break
                draw_text(scr, ln, tx, ty, small, RED)
                ty += small.get_height()
            return
        cm = " + ".join(entry.commanders) if entry.commanders else "no commander found"
        for ln in wrap_text(f"Commander: {cm}", small, tw_text)[:2]:
            if ty + small.get_height() > bottom:
                return
            draw_text(scr, ln, tx, ty, small, gfx.BODY_TEXT)
            ty += small.get_height()
        if ty + small.get_height() > bottom:
            return
        draw_text(scr, f"{entry.total} cards", tx, ty, small, DIM)
        ty += small.get_height() + 4
        for text, blocking in entry.problems(self.runtime):
            for ln in wrap_text(text, small, tw_text)[:3]:
                if ty + small.get_height() > bottom:
                    break
                draw_text(scr, ln, tx, ty, small, RED if blocking else ORANGE)
                ty += small.get_height()

    def draw_thumb(self, gui, commander, x, y, w, h):
        card = {"name": commander, "type": "Legendary Creature", "colors": [], "cost": "", "text": ""}
        surf = gui.card_surface(card, w, h, plate=False)
        gfx.shadowed(gui.screen, pygame.Rect(x, y, w, h), 6, 3, 100)
        gui.screen.blit(surf, (x, y))


class ImportDialog(dlg.Dialog):
    """Paste a deck's text list (Ctrl+V or the Paste button), check it, give it a name, save it into the library."""

    def __init__(self, menu, text=""):
        super().__init__()
        self.menu = menu
        self.text = ""
        self.name = ""
        self.name_edited = False
        self.commanders = self.deck = None
        self.error = "Nothing pasted yet."
        self.problems = []
        self.renames = {}
        self.save_error = None
        self.scroll = 0
        self.preview_rect = pygame.Rect(0, 0, 0, 0)
        self.flash = None
        self.fmt = formats.normal(getattr(menu, "fmt", None))     # round FMT1: the deck screen's format, unless the text names one
        if text and text.strip():
            self.set_text(text)

    # ---- state
    def set_text(self, text):
        self.text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        named = formats.declared(self.text)
        if named:
            self.fmt = named                                      # round FMT1: a "# format: brawl" line in the pasted text
        self.commanders, self.deck, self.error = lib.analyse(self.text)
        self.renames = {}
        if not self.error:
            self.commanders, self.deck, self.renames = lib.resolve_flavor_names(self.commanders, self.deck, self.menu.runtime)
        self.problems = (lib.describe_problems(self.commanders, self.deck, None, self.menu.runtime, self.fmt)
                         if not self.error else [])
        self._lg_version = lg.version()
        self.verdict = lib.legality(self.commanders, self.deck, self.fmt) if not self.error else None
        if not self.error:
            lg.ensure_card_data(list(self.commanders or []) + list(self.deck or []))      # Round BAN1: in the background
        if self.renames:
            shown = ", ".join(f"{old} -> {new}" for old, new in list(self.renames.items())[:4])
            more = f" and {len(self.renames) - 4} more" if len(self.renames) > 4 else ""
            self.problems = [(f"Renamed to the card's real name (printed name Forge does not use): {shown}{more}.",
                              False)] + self.problems
        self.save_error = None
        self.scroll = 0
        if not self.name_edited:
            self.name = lib.suggest_name(self.commanders) if self.commanders else ""

    @property
    def can_save(self):
        return not self.error and bool(self.commanders) and bool(self.name.strip())

    def paste(self, gui):
        text = gui.read_clipboard()
        if not text or not text.strip():
            self.flash = "The clipboard is empty, or the program could not read it. You can also drag a .txt deck file onto this window."
            return
        self.flash = None
        self.set_text(text)

    def set_format(self, fmt):
        """Round FMT1: the format the deck is saved as (the buttons beside Paste); the checks below follow it."""
        self.fmt = formats.normal(fmt)
        if self.text and not self.error:
            renamed = [p for p in self.problems if p[0].startswith("Renamed to the card's real name")]
            self.problems = renamed + lib.describe_problems(self.commanders, self.deck, None, self.menu.runtime, self.fmt)
            self.verdict = lib.legality(self.commanders, self.deck, self.fmt)

    def save(self, gui):
        if not self.can_save:
            return
        try:
            entry = lib.save_text(self.name, self.text, self.menu.library_dir, self.menu.base_dir, fmt=self.fmt)
        except DeckImportError as e:
            self.save_error = str(e)
            return
        self.menu.imported(entry)
        self.done = True

    # ---- drawing
    def draw(self, gui):
        L, scr = gui.L, gui.screen
        fs = L.fs
        k = max(1.0, fs)
        pw, ph = min(int(1000 * k), L.W - 40), min(int(720 * k), L.H - 40)
        rect = self.panel(gui, pw, ph, real=True)
        title, body, bodyb, small, smallb = (gui.font("title", True), gui.font("body"), gui.font("body", True), gui.font("small"),
                                             gui.font("small", True))
        x, y, tw = rect.x + 22, rect.y + 18, rect.w - 44
        draw_text(scr, "Import a deck", x, y, title, WHITE)
        y += title.get_height() + 4
        for ln in wrap_text("Copy your deck's text list, then press Paste. Moxfield: open the deck, choose Export and copy the text. "
                            "Archidekt: Export > Text. MTG Arena: the deck's Export button. You can also drag a .txt deck file "
                            "onto this window.", small, tw):
            draw_text(scr, ln, x, y, small, gfx.BODY_TEXT)
            y += small.get_height()
        y += 8
        bh = int(max(38, 42 * fs))
        pwid = max(gui.button_width("Paste from clipboard (Ctrl+V)"), int(320 * fs))
        self.button(gui, pygame.Rect(x, y, pwid, bh), "Paste from clipboard (Ctrl+V)", "paste", True, not self.text, not self.text)
        cw = max(gui.button_width("Clear"), int(110 * fs))
        self.button(gui, pygame.Rect(x + pwid + 10, y, cw, bh), "Clear", "clear", bool(self.text))
        # round FMT1: which format the deck is saved as, at the right of the same row (a row of its own when there's no room)
        fws = [max(gui.button_width(formats.name(k)), int(110 * fs)) for k in formats.ORDER]
        need = sum(fws) + 6 * len(fws) + small.size("Format")[0] + 12
        if x + pwid + 10 + cw + 16 + need > rect.right - 22:
            y += bh + 8
        fx = rect.right - 22
        self.format_rects = {}
        for key, fw in reversed(list(zip(formats.ORDER, fws))):
            fx -= fw
            r = pygame.Rect(fx, y, fw, bh)
            self.button(gui, r, formats.name(key), "fmt_" + key, True, key == self.fmt)
            self.format_rects["fmt_" + key] = r
            fx -= 6
        draw_text(scr, "Format", fx - 6, y + bh // 2, small, DIM, "midright")
        y += bh + 8
        # bottom block first (so the preview gets what is left)
        foot = int(max(44, 48 * fs))
        name_h = int(max(36, 40 * fs))
        analysis = self.analysis_lines(small, smallb, tw)
        a_h = max(1, len(analysis)) * small.get_height() + 6
        bottom_y = rect.bottom - 18 - foot
        name_y = bottom_y - 10 - name_h
        an_y = name_y - 8 - a_h
        self.preview_rect = pygame.Rect(x, y, tw, max(60, an_y - 8 - y))
        self.draw_preview(gui, small)
        for i, (ln, colour, font) in enumerate(analysis):
            draw_text(scr, ln, x, an_y + i * small.get_height(), font, colour)
        # name field
        draw_text(scr, "Save as:", x, name_y + name_h // 2, bodyb, WHITE, "midleft")
        fx = x + bodyb.size("Save as:")[0] + 12
        box = pygame.Rect(fx, name_y, rect.right - 22 - fx, name_h)
        round_rect(scr, box, gfx.FIELD_BG, 8, 2, GOLD)
        blink = "|" if int(pygame.time.get_ticks() / 500) % 2 == 0 else ""
        draw_text(scr, clip_text(self.name, body, box.w - 24) + blink, box.x + 12, box.centery, body, WHITE, "midleft")
        # buttons
        cancel_w = max(gui.button_width("Cancel"), int(130 * fs))
        self.button(gui, pygame.Rect(x, bottom_y, cancel_w, foot), "Cancel", "cancel")
        sw = max(gui.button_width("Save to library"), int(240 * fs))
        self.button(gui, pygame.Rect(rect.right - 22 - sw, bottom_y, sw, foot), "Save to library", "save", self.can_save, True,
                    self.can_save)

    def analysis_lines(self, small, smallb, width):
        out = []
        if self.flash:
            out += [(ln, ORANGE, small) for ln in wrap_text(self.flash, small, width)]
        if self.save_error:
            out += [(ln, RED, smallb) for ln in wrap_text(self.save_error, small, width)]
        if self.error:
            if self.text:
                out += [(ln, RED, smallb) for ln in wrap_text(self.error, small, width)]
        else:
            if getattr(self, "_lg_version", None) != lg.version():       # Round BAN1: card data or the banned list arrived
                self._refresh_problems()
            cm = " + ".join(self.commanders) if self.commanders else "none found"
            out.append((f"Commander: {cm}  -  {len(self.deck)} more cards", GREEN if self.commanders else RED, smallb))
            tag = lg.label(getattr(self, "verdict", None))
            if tag:
                blocked = lg.blocks_start(getattr(self, "verdict", None))
                fname = formats.name(self.fmt)
                out.append(((f"NOT LEGAL in {fname} - you can save it, but it can't start until its commander is fixed."
                             if blocked else f"{tag} in {fname} - you can still save and play it.") if tag == "NOT LEGAL" else
                            "UNCHECKED - legality not fully checked yet.", RED if tag == "NOT LEGAL" else DIM, smallb))
            for text, blocking in self.problems:
                out += [(ln, RED if blocking else ORANGE, small) for ln in wrap_text(text, small, width)[:2]]
        return out[:5] or [("", DIM, small)]

    def _refresh_problems(self):
        renamed = [p for p in self.problems if p[0].startswith("Renamed to the card's real name")]
        self.problems = renamed + lib.describe_problems(self.commanders, self.deck, None, self.menu.runtime, self.fmt)
        self.verdict = lib.legality(self.commanders, self.deck, self.fmt)
        self._lg_version = lg.version()

    def draw_preview(self, gui, small):
        scr, r = gui.screen, self.preview_rect
        round_rect(scr, r, gfx.FIELD_BG, 8, 1, gfx.MENU_EDGE)
        lines = self.text.split("\n") if self.text else []
        if not lines:
            draw_text(scr, "The pasted list appears here.", r.centerx, r.centery, small, DIM, "center")
            return
        lh = small.get_height()
        room = max(1, (r.h - 12) // lh)
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - room)))
        y = r.y + 6
        scr.set_clip(r.inflate(-4, -4))
        for ln in lines[self.scroll:self.scroll + room]:
            draw_text(scr, clip_text(ln, small, r.w - 24), r.x + 10, y, small, gfx.SOFT_TEXT)
            y += lh
        scr.set_clip(None)
        if len(lines) > room:
            draw_text(scr, f"{len(lines)} lines - scroll to read", r.right - 8, r.bottom - 4, gui.font("tiny"), DIM, "bottomright")

    # ---- input
    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name == "paste":
            self.paste(gui)
        elif name == "clear":
            self.text, self.commanders, self.deck, self.problems = "", None, None, []
            self.error, self.flash, self.save_error = "Nothing pasted yet.", None, None
            if not self.name_edited:
                self.name = ""
        elif name == "cancel":
            self.done = True
        elif name == "save":
            self.save(gui)
        elif name and name.startswith("fmt_"):                           # round FMT1
            self.set_format(name[len("fmt_"):])

    def key(self, gui, ev):
        k, mod = ev.key, getattr(ev, "mod", 0)
        if k == pygame.K_ESCAPE:
            self.done = True
        elif k == pygame.K_v and mod & pygame.KMOD_CTRL:
            self.paste(gui)
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.save(gui)
        elif k == pygame.K_BACKSPACE:
            self.name = self.name[:-1]
            self.name_edited = True
        elif ev.unicode and ev.unicode.isprintable() and len(self.name) < 60 and not mod & pygame.KMOD_CTRL:
            self.name += ev.unicode
            self.name_edited = True

    def wheel(self, gui, dy):
        self.scroll = max(0, self.scroll - dy * 3)

    def drop_file(self, gui, path):
        ext = os.path.splitext(path)[1].lower()
        if os.path.isdir(path) or ext in (".zip", ".png", ".jpg", ".jpeg"):     # patch 40: card pictures, not a deck list
            return self.say("That looks like card pictures: click the deck in the list, press Card art, and drop it there "
                            "(they go into that deck only).", GOLD)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                self.set_text(f.read())
            self.flash = None
        except (OSError, UnicodeDecodeError) as e:
            self.flash = f"Could not read that file: {e}"
