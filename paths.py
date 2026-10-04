# SPDX-License-Identifier: GPL-3.0-or-later
"""
paths.py - the one place that knows where a copy of Manticore keeps its own data (no pygame here, so
it is easy to test).

Two kinds of copy (Round 28, OPEN_QUESTIONS A13):
  portable   - Karl's own working folder (a git checkout) or any copy with a `portable.txt` file next to
               the program. Everything stays in the program folder, exactly as it always has.
  installed  - anything else (the Round 29 installer's copy, an unpacked nightly zip). Settings, decks,
               the card cache, saves and logs move to a folder that belongs to this Windows/macOS/Linux
               user, so an update can replace the program files without touching what the player built.

Two environment variables override the automatic detection, read fresh on every call (never cached at
import time), so a test can switch modes with mock.patch.dict(os.environ, ...) and no reload:
  MANTICORE_PORTABLE=1 / =0   force portable / installed mode, either way
  MANTICORE_USER_DIR=<folder> stand-in "home" for user_dir() and local_dir() - for tests, and for support
                              ("run it pointed at a scratch folder"). The internal layout (flat in portable
                              mode, nested under cache/forge_decks/logs/... in installed mode) still follows
                              whichever mode is in effect.

MANTICORE_DATA_DIR keeps its Round 27a meaning and stays the narrower override: when set, it wins for logs
and saves ONLY, in both modes (tests/__init__.py sets it for the whole test run so a test never touches the
project's real crash_log.txt or saves\\). Modules that call log_dir()/log_file()/saves_dir() get this for
free; don't widen it further - MANTICORE_USER_DIR is the new, wider switch.

Nothing in this module creates a folder just by being imported or called - ensure_dirs() (called once at
program start, from forge_table.main()) is the only thing that makes directories, and migrate_from_program_dir()
only touches files under user_dir()/local_dir(), never the program folder.
"""
import os
import shutil
import sys

PROGRAM_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Manticore"          # Round 29 (decision D1): was "CommanderSim". No tester ever had an installed copy, so nothing is orphaned and there is no migration.
MIGRATED_MARKER = ".migrated"


# ---- which copy is this -------------------------------------------------------------------------------

def program_dir():
    """The folder the program's own read-only files live in (sample decks, sounds, licenses, the Forge
    runtime, java_bridge/). A frozen PyInstaller build (Round 29) is next to the .exe, not next to this
    .py file. The build uses contents_directory='.', so sys._MEIPASS and the .exe's folder are the same place; this
    asks PyInstaller first so it stays right if that layout ever changes."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    return PROGRAM_DIR


def assets_dir():
    """Fonts and table backgrounds (Round AD1): read-only program data, next to sounds/, the same in portable and installed
    mode. Never moves to the per-user folder."""
    return os.path.join(program_dir(), "assets")


def is_portable():
    """True: this copy keeps its data in the program folder, exactly as before Round 28."""
    forced = os.environ.get("MANTICORE_PORTABLE")
    if forced is not None:
        return forced.strip() not in ("0", "false", "False", "")
    folder = program_dir()
    if os.path.isfile(os.path.join(folder, "portable.txt")):
        return True
    return os.path.isdir(os.path.join(folder, ".git"))


# ---- per-user folders (installed mode) ----------------------------------------------------------------

def _win_env_dir(env_name, home_parts):
    v = os.environ.get(env_name)
    return v if v else os.path.join(os.path.expanduser("~"), *home_parts)


def _platform_user_dir():
    """%APPDATA%\\Manticore, ~/Library/Application Support/Manticore, or $XDG_DATA_HOME/manticore."""
    if sys.platform == "win32":
        return os.path.join(_win_env_dir("APPDATA", ("AppData", "Roaming")), APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "manticore")


def _platform_local_dir():
    """%LOCALAPPDATA%\\Manticore, ~/Library/Caches/Manticore, or $XDG_CACHE_HOME/manticore."""
    if sys.platform == "win32":
        return os.path.join(_win_env_dir("LOCALAPPDATA", ("AppData", "Local")), APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", APP_NAME)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "manticore")


def user_dir():
    """Small things that roam: settings.json, bug_report_config.json, my_decks/, saves/."""
    override = os.environ.get("MANTICORE_USER_DIR")
    if override:
        return override
    return program_dir() if is_portable() else _platform_user_dir()


def local_dir():
    """Big or rebuildable things: the card/image cache, forge_decks/, bug_reports/, logs/."""
    override = os.environ.get("MANTICORE_USER_DIR")
    if override:
        return override
    return program_dir() if is_portable() else _platform_local_dir()


def log_dir():
    """Where crash_log.txt and friends go. Flat in the program folder in portable mode (exactly as before
    Round 28); local_dir()/logs in installed mode. MANTICORE_DATA_DIR (Round 27a) wins over both."""
    override = os.environ.get("MANTICORE_DATA_DIR")
    if override:
        return override
    base = local_dir()
    return base if is_portable() else os.path.join(base, "logs")


def saves_dir():
    """The game journal (Round 21). MANTICORE_DATA_DIR wins here too, same as before this round."""
    override = os.environ.get("MANTICORE_DATA_DIR")
    if override:
        return os.path.join(override, "saves")
    return os.path.join(user_dir(), "saves")


def cache_dir():
    return os.path.join(local_dir(), "cache")


def settings_file():
    return os.path.join(user_dir(), "settings.json")


def bug_config_file():
    return os.path.join(user_dir(), "bug_report_config.json")


def library_dir():
    return os.path.join(user_dir(), "my_decks")


def my_art_dir():
    """Round ALT1: your own card pictures (proxy art) - my_art/ beside my_decks/. Backed up like my_decks (Karl, 2 Oct); never in the public source copy."""
    return os.path.join(user_dir(), "my_art")


def mpc_art_dir():
    """Patch 38: the MPC Autofill pictures you picked in the Card art window, kept for good (not in the card cache): mpc_art/
    beside my_art/. Downloaded once when picked; they stay if the cache is cleared, the computer is offline, or the maker takes
    the file down. In the local backup zips but not git (.gitignore), never in the public source copy."""
    return os.path.join(user_dir(), "mpc_art")


def deck_art_dir():
    """Patch 40: pictures imported into one deck from a .zip or a folder (deck_art.py), one JPEG each, named by its content:
    deck_art/ beside my_art/. In the local backup zips but not git (.gitignore), never in the public source copy."""
    return os.path.join(user_dir(), "deck_art")


def forge_decks_dir():
    return os.path.join(local_dir(), "forge_decks")


def bug_reports_dir():
    return os.path.join(local_dir(), "bug_reports")


def log_file(name):
    """log_dir()/name - crash_log.txt, crash_log.old.txt, crash_native.txt, perf_log.txt, forge_engine.log,
    rules_report.txt all go through this one function."""
    return os.path.join(log_dir(), name)


# ---- the program's own read-only files (both modes) ---------------------------------------------------

def sample_dir():
    return os.path.join(program_dir(), "sample_decks")


def sounds_dir():
    return os.path.join(program_dir(), "sounds")


def licenses_dir():
    return os.path.join(program_dir(), "licenses")


def runtime_dir():
    return os.path.join(program_dir(), "forge_runtime")


# ---- housekeeping --------------------------------------------------------------------------------------

def describe():
    """One line for --version, the help screen and a bug report: where this copy's data lives."""
    if is_portable():
        return "data: portable (program folder)"
    return f"data: {user_dir()}"


def ensure_dirs():
    """Create every folder the current mode needs. Call once, at program start - not at import time, and
    not from any other function in this module."""
    for d in (user_dir(), local_dir(), cache_dir(), library_dir(), my_art_dir(), mpc_art_dir(), deck_art_dir(), forge_decks_dir(), bug_reports_dir(),
              log_dir(), saves_dir()):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def migrate_from_program_dir():
    """D28-2: on an installed copy's first start, COPY (never move, never overwrite) settings.json,
    bug_report_config.json, my_decks/ and saves/ out of the program folder if an older, unpacked copy left
    them there. Never touches the originals. A `.migrated` marker in user_dir() makes a second call a no-op,
    so this is safe to call on every startup. Returns the list of names actually copied ([] the first time
    there was nothing there to copy, and every time after the first)."""
    if is_portable():
        return []
    marker = os.path.join(user_dir(), MIGRATED_MARKER)
    if os.path.exists(marker):
        return []
    old = program_dir()
    # Only the two BASE folders, not the full ensure_dirs() - that also creates my_decks/ and saves/ as
    # empty folders, which would make the "never overwrite" check below see them as already there and
    # skip copying into them.
    os.makedirs(user_dir(), exist_ok=True)
    os.makedirs(local_dir(), exist_ok=True)
    copied = []
    for name, dest in (("settings.json", settings_file()), ("bug_report_config.json", bug_config_file())):
        src = os.path.join(old, name)
        if os.path.isfile(src) and not os.path.exists(dest):
            try:
                shutil.copy2(src, dest)
                copied.append(name)
            except OSError:
                pass
    for name, dest in (("my_decks", library_dir()), ("saves", saves_dir())):
        src = os.path.join(old, name)
        if os.path.isdir(src) and not os.path.exists(dest):
            try:
                shutil.copytree(src, dest)
                copied.append(name)
            except OSError:
                pass
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("migrated\n")
    except OSError:
        pass
    return copied
