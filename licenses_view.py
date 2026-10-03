# SPDX-License-Identifier: GPL-3.0-or-later
"""
licenses_view.py - the "Licenses and credits" window (Cog > Licenses and credits, or Shift+F1).

Everything it shows comes from licenses/NOTICES.json plus the license and notice texts alongside it
(licenses/texts/*.txt, licenses/notices/*.txt). That file is the single source of truth: the same manifest
also drives tools/build_notices.py, which writes THIRD_PARTY_NOTICES.txt for the folks who read a text file
instead of opening the program.

    load_manifest()   reads and lightly normalises licenses/NOTICES.json (never raises - missing/broken
                       pieces turn into entries that say so, in the dialog, rather than crashing it)
    LicensesDialog     the window itself: a scrollable list on the left, the selected entry's text on the right

GPLv3 section 5(d) requires an interactive program to show "Appropriate Legal Notices" - this window (plus
the About entry's wording and the --version line) is how Manticore does that.
"""
import json
import os
import re

import pygame

import gfx
import paths
from gfx import DIM, GOLD, RED, WHITE, YELLOW, clip_text, draw_text, round_rect, wrap_text
from forge_dialogs import Dialog

BASE_DIR = paths.program_dir()                  # Round 29: for a frozen build, _MEIPASS
LICENSES_DIR = paths.licenses_dir()
MANIFEST_PATH = os.path.join(LICENSES_DIR, "NOTICES.json")

_SPDX_SPLIT = re.compile(r"\s+(?:OR|AND|WITH)\s+")


def license_ids(expr):
    """'Apache-2.0 OR EPL-1.0' -> ['Apache-2.0', 'EPL-1.0']; 'CDDL-1.1 OR GPL-2.0-only WITH Classpath-exception-2.0' ->
    ['CDDL-1.1', 'GPL-2.0-only', 'Classpath-exception-2.0']. Plain 'MIT' -> ['MIT']."""
    if not expr:
        return []
    cleaned = expr.replace("(", " ").replace(")", " ")
    parts = [p.strip() for p in _SPDX_SPLIT.split(cleaned) if p.strip()]
    return parts or [expr.strip()]


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def license_text(spdx_id):
    """The text of one SPDX id's licence, or None with the reason it's missing."""
    path = os.path.join(LICENSES_DIR, "texts", f"{spdx_id}.txt")
    text = _read_text(path)
    if text is None:
        return None, f"licenses/texts/{spdx_id}.txt is missing - reinstall or tell Karl"
    return text, None


def notice_text(notice_file):
    """notice_file is a path like 'notices/netty.txt' relative to licenses/, or a list of them, or None."""
    if not notice_file:
        return []
    files = notice_file if isinstance(notice_file, list) else [notice_file]
    out = []
    for rel in files:
        path = os.path.join(LICENSES_DIR, rel)
        text = _read_text(path)
        if text is None:
            out.append((rel, None, f"licenses/{rel} is missing - reinstall or tell Karl"))
        else:
            out.append((rel, text, None))
    return out


def load_manifest():
    """The manifest dict, or {'_error': '...'} when NOTICES.json itself can't be read/parsed - the dialog then
    still opens and says so (a missing manifest must never crash the program, GPLv3 5(d) or not)."""
    text = _read_text(MANIFEST_PATH)
    if text is None:
        return {"_error": "licenses/NOTICES.json is missing - reinstall or tell Karl"}
    try:
        return json.loads(text)
    except ValueError as e:
        return {"_error": f"licenses/NOTICES.json could not be read ({e}) - reinstall or tell Karl"}


class Entry:
    """One row in the left-hand list and the block of text it shows on the right."""
    def __init__(self, key, label, group, lines_fn):
        self.key = key
        self.label = label
        self.group = group                  # left-column grouping header; None = no header above this row
        self.lines_fn = lines_fn             # () -> [(text, colour_or_None, bold)] - built once, cached by the dialog

    def build(self):
        return self.lines_fn()


def _about_lines(manifest, describe_fn):
    project = manifest.get("project", {})
    out = [(project.get("name", "Manticore") + " - about this copy", None, True), ("", None, False),
           (describe_fn(), DIM, False), ("", None, False),
           (project.get("copyright", ""), None, False), ("", None, False),
           (project.get("notice", ""), None, False), ("", None, False),
           ("Getting the source", GOLD, True),
           ("This program is free software licensed under the GNU General Public License v3 or later. The complete "
            "source code for this exact copy is what it was built from - ask Karl for it, or (once published) get it "
            "from the project's repository.", None, False)]
    src = project.get("source_url")
    if src:
        out.append((src, None, False))
    return out


def _license_lines(title, expr, note=None):
    out = [(title, None, True)]
    if note:
        out.append(("", None, False))
        out.append((note, DIM, False))
    for spdx in license_ids(expr):
        text, err = license_text(spdx)
        out.append(("", None, False))
        out.append((f"--- {spdx} ---", GOLD, True))
        out.append(("", None, False))
        if err:
            out.append((err, RED, False))
        else:
            for ln in text.splitlines():
                out.append((ln, None, False))
    return out


def _credits_lines(manifest):
    out = [("Wizards of the Coast, Scryfall, card art, deck sites", None, True)]
    for c in manifest.get("credits", []):
        out.append(("", None, False))
        out.append((c.get("title", ""), GOLD, True))
        out.append(("", None, False))
        out.append((c.get("text", ""), None, False))
    return out


def _component_lines(comp, is_java=False):
    name = comp.get("name", comp.get("id", "?"))
    out = [(name, None, True), ("", None, False)]
    version = comp.get("version")
    if version:
        out.append((f"Version: {version}", DIM, False))
    if is_java:
        arts = comp.get("artifacts") or []
        if arts:
            out.append(("Maven artifacts: " + ", ".join(arts), DIM, False))
    lic = comp.get("license", "?")
    out.append((f"License: {lic}", None, False))
    status = comp.get("status", "not_collected")
    if status == "attribution":
        out.append(("No licence to name; the attribution the source asks for is given: " + (comp.get("attribution") or ""),
                    None, False))
    elif status != "verified":
        out.append((f"Status: {status} - this line has not been double-checked yet", YELLOW, False))
    if comp.get("copyright"):
        out.append((comp["copyright"], None, False))
    if comp.get("url"):
        out.append((comp["url"], DIM, False))
    if comp.get("used_for"):
        out.append((f"Used for: {comp['used_for']}", DIM, False))
    if comp.get("note"):
        out.append(("", None, False))
        out.append((comp["note"], DIM, False))
    for spdx in license_ids(lic):
        if spdx == "NOASSERTION":                        # Round AD1: no licence is claimed, so there is no text file to be missing
            out.append(("", None, False))
            out.append((f"Licence not established (status: {status}).", YELLOW, False))
            continue
        text, err = license_text(spdx)
        out.append(("", None, False))
        out.append((f"--- {spdx} license text ---", GOLD, True))
        out.append(("", None, False))
        if err:
            out.append((err, RED, False))
        else:
            for ln in text.splitlines():
                out.append((ln, None, False))
    for rel, text, err in notice_text(comp.get("notice_file")):
        out.append(("", None, False))
        out.append((f"--- NOTICE ({rel}) ---", GOLD, True))
        out.append(("", None, False))
        if err:
            out.append((err, RED, False))
        else:
            for ln in text.splitlines():
                out.append((ln, None, False))
    return out


def build_entries(manifest, describe_fn):
    """The manifest -> a flat list of Entry, in the order the left column shows them. Components and
    java_components are flattened into one "Third-party software" list (a player doesn't care whether a
    library came from PyPI or Maven Central) - java_components is a dict keyed by Maven groupId, so its
    entries are sorted by display name for a stable, readable order."""
    if "_error" in manifest:
        return [Entry("error", "Licenses and credits", None, lambda: [(manifest["_error"], RED, False)])]

    entries = [
        Entry("about", "About Manticore", None, lambda: _about_lines(manifest, describe_fn)),
        Entry("gpl", "GNU General Public License v3", None,
              lambda: _license_lines("GNU General Public License v3", manifest.get("project", {}).get("license", "GPL-3.0-or-later"))),
        Entry("credits", "Wizards, Scryfall, card art, deck sites", None, lambda: _credits_lines(manifest)),
    ]
    comps = list(manifest.get("components", []))
    java = manifest.get("java_components", {})
    java_list = [dict(v, id=k) for k, v in java.items()]
    java_list.sort(key=lambda c: c.get("name", c.get("id", "")).lower())
    for c in comps:
        entries.append(Entry(f"c:{c.get('id')}", c.get("name", c.get("id", "?")),
                              c.get("group", "Third-party software"), (lambda c=c: _component_lines(c, False))))
    for c in java_list:
        entries.append(Entry(f"j:{c.get('id')}", c.get("name", c.get("id", "?")), "Third-party software (Forge)",
                              (lambda c=c: _component_lines(c, True))))
    natives = manifest.get("native_libraries", {})
    status = natives.get("_status")
    libs = natives.get("libraries") or []
    if status or libs:
        def _native_lines():
            out = [("Native libraries (pygame)", None, True)]
            if status:
                out.append(("", None, False))
                out.append((status, YELLOW, False))
            for lib in libs:
                out.append(("", None, False))
                out.extend(_component_lines(lib, False))
            return out
        entries.append(Entry("natives", "Native libraries (pygame)", "Third-party software", _native_lines))
    return entries


class LicensesDialog(Dialog):
    """Scrollable list on the left (grouped by manifest group), the selected entry's text on the right.
    Wrapping is done once per (entry, width, font-size) and cached, so scrolling never re-wraps."""

    def __init__(self):
        super().__init__()
        import version
        self.manifest = load_manifest()
        self.entries = build_entries(self.manifest, version.describe)
        self.selected = 0
        self.list_scroll = 0
        self.pane_scroll = 0
        self.list_rect = pygame.Rect(0, 0, 0, 0)
        self.pane_rect = pygame.Rect(0, 0, 0, 0)
        self.focus = "list"        # which side the mouse wheel / Home / End affect
        self._wrap_cache = {}      # (entry_key, width, font_id) -> [(wrapped_lines, colour, bold, source_line_h)]
        self.copy_flash = 0

    # ---- text layout (cached; only re-runs when the entry, pane width, or font size changes) --------------

    def _wrap_entry(self, gui, entry, width, font):
        cache_key = (entry.key, width, id(font))
        cached = self._wrap_cache.get(cache_key)
        if cached is not None:
            return cached
        out = []
        for text, colour, bold in entry.build():
            f = gui.font("body", True) if bold else font
            if text == "":
                out.append(([""], colour, bold, f.get_height()))
                continue
            for ln in wrap_text(text, f, width):
                out.append(([ln], colour, bold, f.get_height()))
        if len(self._wrap_cache) > 60:              # the manifest is bounded (a few dozen entries), so this is a generous cap
            self._wrap_cache.clear()
        self._wrap_cache[cache_key] = out
        return out

    # ---- draw ------------------------------------------------------------------------------------------

    def draw(self, gui):
        L = gui.L
        w = min(int(980 * max(1.0, L.fs)), L.W - 40)
        h = min(int(640 * max(1.0, L.fs)), L.H - 40)
        rect = self.panel(gui, w, h, real=True)
        tf = gui.font("title", True)
        draw_text(gui.screen, "Licenses and credits", rect.x + 22, rect.y + 16, tf, GOLD)
        head = 24 + tf.get_height() + 10
        bh = int(40 * L.fs)
        foot = bh + 24
        body_top = rect.y + head
        body_h = max(20, rect.h - head - foot)
        list_w = int(rect.w * 0.32)
        self.list_rect = pygame.Rect(rect.x + 14, body_top, list_w - 20, body_h)
        self.pane_rect = pygame.Rect(rect.x + list_w + 10, body_top, rect.w - list_w - 24, body_h)
        self._draw_list(gui)
        self._draw_pane(gui)
        bw = max(gui.button_width("Close"), int(110 * L.fs))
        ow = max(gui.button_width("Open licences folder"), int(210 * L.fs))
        cw = max(gui.button_width("Copy"), int(110 * L.fs))
        by = rect.bottom - bh - 12
        self.button(gui, pygame.Rect(rect.x + 14, by, ow, bh), "Open licences folder", "open_folder", True)
        self.button(gui, pygame.Rect(rect.x + 14 + ow + 10, by, cw, bh), "Copy" if not self.copy_flash else "Copied", "copy", True)
        self.button(gui, pygame.Rect(rect.right - bw - 18, by, bw, bh), "Close", "close", True, True)

    def _draw_list(self, gui):
        scr = gui.L
        round_rect(gui.screen, self.list_rect, gfx.LIST_BG, 10, 1, gfx.LIST_EDGE)
        kf, gf = gui.font("small", True), gui.font("tiny", True)
        row_h = kf.get_height() + 10
        y = self.list_rect.y - self.list_scroll
        rows = []
        last_group = None
        saved_clip = gui.screen.get_clip()
        gui.screen.set_clip(self.list_rect)
        for i, e in enumerate(self.entries):
            if e.group and e.group != last_group:
                if self.list_rect.y - 20 < y < self.list_rect.bottom:
                    draw_text(gui.screen, e.group.upper(), self.list_rect.x + 8, y + 2, gf, DIM)
                y += gf.get_height() + 6
                last_group = e.group
            r = pygame.Rect(self.list_rect.x + 4, y, self.list_rect.w - 8, row_h)
            rows.append((r, i))
            if r.bottom >= self.list_rect.y and r.y <= self.list_rect.bottom:
                if i == self.selected:
                    round_rect(gui.screen, r, gfx.LIST_SEL_BG, 6)
                colour = WHITE if i == self.selected else gfx.SOFT_TEXT
                draw_text(gui.screen, clip_text(e.label, kf, r.w - 14), r.x + 8, r.y + 5, kf, colour)
            y += row_h + 2
        gui.screen.set_clip(saved_clip)
        self._list_rows = rows
        content_h = (y + self.list_scroll) - self.list_rect.y
        self.list_max_scroll = max(0, content_h - self.list_rect.h)
        self.list_scroll = max(0, min(self.list_scroll, self.list_max_scroll))

    def _draw_pane(self, gui):
        round_rect(gui.screen, self.pane_rect, gfx.PANE_BG, 10, 1, gfx.LIST_EDGE)
        entry = self.entries[self.selected]
        font = gui.font("small")
        pad = 12
        width = self.pane_rect.w - pad * 2
        lines = self._wrap_entry(gui, entry, width, font)
        y = self.pane_rect.y + pad - self.pane_scroll
        saved_clip = gui.screen.get_clip()
        gui.screen.set_clip(self.pane_rect)
        for wrapped, colour, bold, line_h in lines:
            f = gui.font("body", True) if bold else font
            c = colour or gfx.LIST_TEXT
            if y + line_h > self.pane_rect.y and y < self.pane_rect.bottom:
                draw_text(gui.screen, wrapped[0], self.pane_rect.x + pad, y, f, c)
            y += line_h + 2
        gui.screen.set_clip(saved_clip)
        content_h = (y + self.pane_scroll) - (self.pane_rect.y + pad)
        self.pane_max_scroll = max(0, content_h - (self.pane_rect.h - pad))
        self.pane_scroll = max(0, min(self.pane_scroll, self.pane_max_scroll))
        if self.pane_max_scroll:
            frac = self.pane_rect.h / max(1, content_h)
            bar_h = max(24, int(self.pane_rect.h * frac))
            bar_y = self.pane_rect.y + int((self.pane_rect.h - bar_h) * (self.pane_scroll / self.pane_max_scroll))
            pygame.draw.rect(gui.screen, gfx.SCROLLBAR, pygame.Rect(self.pane_rect.right - 5, bar_y, 5, bar_h), border_radius=3)

    # ---- selected text, for the Copy button -------------------------------------------------------------

    def selected_text(self):
        entry = self.entries[self.selected]
        return "\n".join(t for t, _c, _b in entry.build())

    # ---- input -------------------------------------------------------------------------------------------

    def click(self, gui, pos, button):
        name = self.button_at(pos)
        if name == "close":
            self.done = True
            return
        if name == "open_folder":
            self._open_folder(gui)
            return
        if name == "copy":
            ok = gui.write_clipboard(self.selected_text())
            gui.say("Copied to clipboard" if ok else "Couldn't reach the clipboard", DIM if ok else RED)
            return
        if self.list_rect.collidepoint(pos):
            self.focus = "list"
            for r, i in getattr(self, "_list_rows", []):
                if r.collidepoint(pos):
                    if i != self.selected:
                        self.selected = i
                        self.pane_scroll = 0
                    return
        elif self.pane_rect.collidepoint(pos):
            self.focus = "pane"

    def _open_folder(self, gui):
        try:
            if os.name == "nt":
                os.startfile(LICENSES_DIR)  # noqa: S606 - a local folder we shipped, not user input
                return
        except Exception:
            pass
        gui.say(f"Licences folder: {LICENSES_DIR}", DIM, 6.0)

    def wheel(self, gui, dy):
        if self.focus == "pane":
            self.pane_scroll = max(0, min(getattr(self, "pane_max_scroll", 0), self.pane_scroll - dy * 40))
        else:
            self.list_scroll = max(0, min(getattr(self, "list_max_scroll", 0), self.list_scroll - dy * 40))

    def key(self, gui, ev):
        if ev.key == pygame.K_ESCAPE:
            self.done = True
        elif ev.key == pygame.K_TAB:
            self.focus = "pane" if self.focus == "list" else "list"
        elif ev.key == pygame.K_UP:
            if self.selected > 0:
                self.selected -= 1
                self.pane_scroll = 0
        elif ev.key == pygame.K_DOWN:
            if self.selected < len(self.entries) - 1:
                self.selected += 1
                self.pane_scroll = 0
        elif ev.key == pygame.K_PAGEUP:
            self.pane_scroll = max(0, self.pane_scroll - int(self.pane_rect.h * 0.8))
        elif ev.key == pygame.K_PAGEDOWN:
            self.pane_scroll = min(getattr(self, "pane_max_scroll", 0), self.pane_scroll + int(self.pane_rect.h * 0.8))
        elif ev.key == pygame.K_HOME:
            self.pane_scroll = 0
        elif ev.key == pygame.K_END:
            self.pane_scroll = getattr(self, "pane_max_scroll", 0)
