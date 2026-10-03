# SPDX-License-Identifier: GPL-3.0-or-later
"""
crashlog.py - when something goes wrong, leave a file that says exactly what happened (no pygame needed to import this).

Everything goes into ONE text file, crash_log.txt, next to the program. Each entry has the date, which copy of the program was
running (see version.py), the Python / Windows / pygame versions, what the game was doing, the error with its full traceback, and
the last lines of forge_engine.log. If something breaks, send that file (or the newest entry in it) - nothing else is needed.

What gets written:
  ERROR      a mistake inside one frame of the game. The game keeps running; the same error is written in full only once
             (then a one-line count at the 10th, 100th and 1000th repeat, so a loop of errors cannot fill the disk).
  CRASH      the program is closing because of an error nothing handled.
  THREAD     a helper thread (art download, Forge reader) died. The program carries on but that part stopped.
  FROZEN     the window stopped responding for 30 seconds: what every thread was doing is written, and a second entry says when
             (and if) it woke up again. Dragging or holding the window's title bar also stops it, which is not a bug.
  HARD CRASH the whole process died (a fault in a native library). Python cannot write a nice report at that moment, so it leaves
             crash_native.txt, and the next start copies that into crash_log.txt.

Nothing in here may ever raise: a broken logger must not turn one problem into two.
"""
import atexit
import datetime
import faulthandler
import os
import platform
import sys
import threading
import time
import traceback

import paths
import version

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = paths.log_dir()      # round 28: the program folder in portable mode (as before); MANTICORE_DATA_DIR (Round
                                 # 27a) still wins in both modes, so a test run never touches the project's own crash_log.txt
LOG_NAME = "crash_log.txt"
OLD_NAME = "crash_log.old.txt"                    # the log is moved here when it passes MAX_LOG_BYTES (one old copy is kept)
NATIVE_NAME = "crash_native.txt"
ENGINE_LOG = "forge_engine.log"
MAX_LOG_BYTES = 1_000_000
ENGINE_TAIL_LINES = 20
TAIL_READ_BYTES = 16_000
REPEAT_MARKS = (10, 100, 1000)
FREEZE_SECONDS = 30.0

_cfg = {"folder": DATA_DIR, "dialog": True, "context": None}
_seen = {}                                        # error signature -> how many times it happened this run
_hooks = {}                                       # what install() replaced, so uninstall() can put it back
_lock = threading.RLock()


def log_path(folder=None):
    return os.path.join(folder or _cfg["folder"], LOG_NAME)


def set_context(fn):
    """`fn()` returns a short text (or list of lines) about what the program is doing; it is added to every entry."""
    _cfg["context"] = fn


# ---- gathering facts ---------------------------------------------------------------------------------------------------------

def environment(folder=None):
    """Lines about this computer and this copy of the program."""
    folder = folder or _cfg["folder"]
    lines = []
    try:
        lines.append(version.describe(folder))
    except Exception as e:
        lines.append(f"version unknown ({e})")
    try:
        lines.append(f"Python {platform.python_version()} ({platform.machine()}), {platform.platform()}")
    except Exception:
        pass
    try:
        pg = sys.modules.get("pygame")
        if pg:
            bits = f"pygame-ce {pg.version.ver}, SDL {'.'.join(map(str, pg.get_sdl_version()))}"
            if pg.display.get_init():
                bits += f", window {pg.display.get_window_size()}, screens {pg.display.get_desktop_sizes()}"
            lines.append(bits)
    except Exception:
        pass
    try:
        lines.append(version.forge_build() or "Forge: unknown")          # round 28d (F1): which Forge the report came from
    except Exception:
        pass
    try:
        lines.append(paths.describe())
    except Exception:
        pass
    try:
        rt = os.path.join(folder, "forge_runtime")
        if os.path.isdir(rt):
            jars = [f"{n} {os.path.getsize(os.path.join(rt, n))} bytes" for n in sorted(os.listdir(rt)) if n.lower().endswith(".jar")]
            lines.append("forge_runtime: " + (", ".join(jars) if jars else "no .jar files"))
        else:
            lines.append("forge_runtime: missing")
    except Exception:
        pass
    return lines


def _context_lines():
    fn = _cfg["context"]
    if not fn:
        return []
    try:
        out = fn()
    except Exception as e:                        # the game may be half broken: say so and carry on
        return [f"(could not describe the game state: {type(e).__name__}: {e})"]
    if out is None:
        return []
    return [out] if isinstance(out, str) else [str(x) for x in out]


def engine_tail(folder=None, lines=ENGINE_TAIL_LINES):
    """The last lines of forge_engine.log (what Forge itself printed), or []."""
    path = os.path.join(folder or _cfg["folder"], ENGINE_LOG)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_READ_BYTES))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows = [r[:300] for r in text.splitlines() if r.strip()]
    return rows[-lines:]


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---- writing -----------------------------------------------------------------------------------------------------------------

def _append(text, folder=None):
    """Add to crash_log.txt. Returns True if it was written."""
    path = log_path(folder)
    try:
        try:
            if os.path.getsize(path) > MAX_LOG_BYTES:
                os.replace(path, os.path.join(os.path.dirname(path), OLD_NAME))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8", errors="replace", newline="\n") as f:
            f.write(text)
        return True
    except OSError:
        return False


def write_entry(title, body, folder=None, with_engine=True):
    """One entry: header, environment, game state, `body` (a string or lines), Forge's last output. Returns True if written."""
    with _lock:
        folder = folder or _cfg["folder"]
        parts = [f"==== {_now()}  {title} ===="]
        parts += environment(folder)
        parts += _context_lines()
        parts.append("")
        parts += [body] if isinstance(body, str) else list(body)
        if with_engine:
            tail = engine_tail(folder)
            if tail:
                parts += ["", f"--- last {len(tail)} lines of {ENGINE_LOG} ---"] + tail
        return _append("\n".join(parts).rstrip("\n") + "\n\n", folder)


def _as_tuple(exc):
    if isinstance(exc, tuple):
        return exc
    return type(exc), exc, exc.__traceback__


def signature(exc):
    """The same mistake in the same place gives the same text, so repeats can be counted instead of written out again."""
    etype, value, tb = _as_tuple(exc)
    try:
        last = traceback.extract_tb(tb)[-1]
        where = f"{os.path.basename(last.filename)}:{last.lineno}"
    except Exception:
        where = str(value)[:80]
    return f"{etype.__name__} at {where}"


def record(exc, kind="ERROR", dedupe=True):
    """Write an exception to the log. Returns True when a full entry was written (False: a repeat, or the write failed)."""
    try:
        etype, value, tb = _as_tuple(exc)
        sig = signature((etype, value, tb))
        with _lock:
            if dedupe:
                _seen[sig] = _seen.get(sig, 0) + 1
                n = _seen[sig]
                if n > 1:
                    if n in REPEAT_MARKS:
                        _append(f"---- {_now()}  the same error again ({sig}): {n} times so far this run ----\n\n")
                    return False
            body = "".join(traceback.format_exception(etype, value, tb))
            return write_entry(f"{kind}: {sig}", body)
    except Exception:
        return False


def note(title, text=""):
    """A plain entry with no traceback (for the program to say 'this happened')."""
    try:
        return write_entry(title, text)
    except Exception:
        return False


def show_dialog(title, text):
    """A message window (the console can be gone by then). Returns True if one was shown. Off when install(dialog=False)."""
    if not _cfg["dialog"]:
        return False
    try:
        import pygame
        if not pygame.display.get_init():
            pygame.display.init()
        pygame.display.message_box(title, text, message_type="error")
        return True
    except Exception:
        return False


# ---- hooks -------------------------------------------------------------------------------------------------------------------

def _excepthook(etype, value, tb):
    if issubclass(etype, KeyboardInterrupt):
        return _hooks.get("sys", sys.__excepthook__)(etype, value, tb)
    record((etype, value, tb), "CRASH - the program closed because of this", dedupe=False)
    try:
        _hooks.get("sys", sys.__excepthook__)(etype, value, tb)         # still print it in the terminal window
    except Exception:
        pass
    show_dialog("Manticore has to close",
                f"The program hit an error and has to close.\n\n{etype.__name__}: {str(value)[:300]}\n\n"
                f"The details were saved in:\n{log_path()}\n\nStart the game again and press F8 to send a bug report (it includes this file), "
                "or send that file to whoever gave you the game.")


def _thread_hook(args):
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread else "unknown"
    record((args.exc_type, args.exc_value, args.exc_traceback), f"THREAD '{name}' stopped")
    try:
        _hooks["thread"](args)
    except Exception:
        pass


def _collect_native(folder):
    """A crash_native.txt with text in it means the last run died hard: move that text into crash_log.txt."""
    path = os.path.join(folder, NATIVE_NAME)
    try:
        if os.path.getsize(path) == 0:
            return False
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
        with open(path, "w"):
            pass
    except OSError:
        return False
    return write_entry("HARD CRASH in the previous run (found at startup)",
                       ["The whole program died without a chance to write a report; Python left this:", text], folder, with_engine=True)


def _open_native(folder):
    path = os.path.join(folder, NATIVE_NAME)
    try:
        _hooks["faulthandler_was_on"] = faulthandler.is_enabled()
        f = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=f, all_threads=True)
        _hooks["native"] = (f, path)
    except (OSError, RuntimeError, ValueError):
        pass


def _close_native():
    native = _hooks.pop("native", None)
    if native:
        f, path = native
        try:
            faulthandler.disable()
            if _hooks.pop("faulthandler_was_on", False):
                faulthandler.enable()
            f.close()
            if os.path.getsize(path) == 0:
                os.remove(path)
        except (OSError, ValueError):
            pass


def install(folder=None, dialog=True):
    """Start catching problems. Call once, first thing in main(). Safe to call again (it only updates the settings)."""
    _cfg["folder"] = folder or DATA_DIR
    _cfg["dialog"] = dialog
    if "sys" in _hooks:
        return
    _hooks["sys"] = sys.excepthook
    sys.excepthook = _excepthook
    _hooks["thread"] = threading.excepthook
    threading.excepthook = _thread_hook
    _collect_native(_cfg["folder"])
    _open_native(_cfg["folder"])
    atexit.register(_close_native)


def uninstall():
    """Put everything back (used by the tests)."""
    if "sys" in _hooks:
        sys.excepthook = _hooks.pop("sys")
    if "thread" in _hooks:
        threading.excepthook = _hooks.pop("thread")
    _close_native()
    atexit.unregister(_close_native)
    _seen.clear()
    # Round 28ba (the Round 27f fix): back to DATA_DIR, not BASE_DIR. With BASE_DIR, every crash-log note made after
    # test_crashlog.py ran went to the project's REAL crash_log.txt instead of the test run's temp folder
    # (tests/__init__.py) - Round 27d's deliberate-fault tests left four "Bridge self-check failed" entries in Karl's.
    _cfg.update(folder=DATA_DIR, dialog=True, context=None)


# ---- freeze detection --------------------------------------------------------------------------------------------------------

class Watchdog:
    """The game loop calls beat() every frame. If that stops for `limit` seconds a helper thread writes what every thread is
    doing (once per freeze), and beat() writes a follow-up when the window comes back."""

    def __init__(self, limit=FREEZE_SECONDS, poll=1.0, clock=time.monotonic, main_ident=None):
        self.limit, self.poll, self.clock = limit, poll, clock
        self.main = main_ident or threading.get_ident()
        self.last = clock()
        self.reported_at = None                   # when the freeze was written up (None while everything is fine)
        self._stop = threading.Event()
        self._thread = None

    def beat(self):
        now = self.clock()
        if self.reported_at is not None:
            gap = now - self.last
            self.reported_at = None
            note("FROZEN - the window responded again", f"It was unresponsive for about {gap:.0f} seconds.")
        self.last = now

    def stacks(self):
        names = {t.ident: t.name for t in threading.enumerate()}
        out = []
        frames = sys._current_frames()
        for ident in sorted(frames, key=lambda i: (i != self.main, i)):
            label = "main thread (the window)" if ident == self.main else f"thread '{names.get(ident, ident)}'"
            out.append(f"--- {label} ---")
            out += "".join(traceback.format_stack(frames[ident])).rstrip("\n").splitlines()
        return out

    def check(self):
        """One look. Returns True if a freeze was just written up."""
        idle = self.clock() - self.last
        if self.reported_at is not None or idle < self.limit:
            return False
        self.reported_at = self.clock()
        try:
            note(f"FROZEN - the window has not responded for {idle:.0f} seconds",
                 ["If you were holding the window's title bar this is normal. Otherwise this shows what each part was doing:"]
                 + self.stacks())
        except Exception:
            pass
        return True

    def _run(self):
        while not self._stop.wait(self.poll):
            self.check()

    def start(self):
        self.last = self.clock()
        self._thread = threading.Thread(target=self._run, name="crashlog-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
