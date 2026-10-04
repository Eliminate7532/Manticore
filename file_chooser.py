# SPDX-License-Identifier: GPL-3.0-or-later
"""
file_chooser.py - Patch 40: the system's Open window, on a thread of its own, so the game window keeps drawing (and the freeze
watchdog stays quiet) while it is up.

tkinter's askopenfilename is Windows' own Open window on Windows. It runs on this module's thread with a hidden Tk root of its
own; nothing else in the program uses that root. The window may open behind the game window - the caller says so, and offers
dragging the file onto the game window as the other way in.
"""
import os
import threading


def downloads_dir():
    """The folder the Open window starts in: Downloads when there is one (where a browser puts an export), else home."""
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def tk_open(title, patterns, start_dir=None):
    """One Open window (tkinter). Returns the chosen path, or None when nothing was chosen."""
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    try:
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        path = filedialog.askopenfilename(parent=root, title=title, filetypes=patterns,
                                          initialdir=start_dir or downloads_dir())
        return path or None
    finally:
        try:
            root.destroy()
        except Exception:
            pass


class FileChooser:
    """Opens the Open window at once, on its own thread. Poll .finished; then .path (None: nothing chosen) or .error."""

    def __init__(self, title, patterns, start_dir=None, opener=None, start=True):
        self.title, self.patterns, self.start_dir = title, list(patterns), start_dir
        self.opener = opener or tk_open
        self.finished = False
        self.path = None
        self.error = None
        self._thread = threading.Thread(target=self._run, name="file-chooser", daemon=True)
        if start:
            self._thread.start()

    def _run(self):
        try:
            self.path = self.opener(self.title, self.patterns, self.start_dir) or None
        except Exception as e:                       # no tkinter, no display: the caller offers drag and drop instead
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.finished = True

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return self
