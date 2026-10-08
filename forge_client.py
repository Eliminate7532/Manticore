# SPDX-License-Identifier: GPL-3.0-or-later
"""
forge_client.py - talks to the Forge rules engine.

Forge (https://github.com/Card-Forge/forge, GPL-3.0) is a Java program. This module starts it as a
child process through the small bridge in java_bridge/ and speaks JSON lines with it:

    Forge -> us:  {"t": "state", ...}    a full snapshot of the game after every change
                  {"t": "request", ...}  a question the engine needs answered (choose a card, yes/no, ...)
                  {"t": "log", ...}, {"t": "info", ...}, {"t": "message", ...}, {"t": "game_over"}
    us -> Forge:  {"c": "card", "id": 12}   click a card          {"c": "ok"} / {"c": "cancel"}
                  {"c": "player", "id": 1}  click a player         {"c": "reply", "id": 5, "value": ...}
                  {"c": "stops", "mine": true, "phases": [...]}    {"c": "undo"} {"c": "quit"} ...
                  {"c": "yield", "mode": "turn" | "stack" | "stackall" | "until" | "clear"}   pass for me until ... (see the yield_* methods)

Everything the engine decides (rules, the stack, combat, the AI opponents) happens in Forge. This
file only launches it, keeps the latest state, and hands the GUI what it needs. There is no pygame
in here, so it can be tested without a window.
"""
import filecmp
import json
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque

import forge_net
import java_crash
import paths

BASE_DIR = paths.program_dir()      # Round 29: for a frozen build this is _MEIPASS, not wherever this module's __file__ claims to be
DATA_DIR = paths.log_dir()      # round 28: the program folder in portable mode (as before); tests/__init__.py's
                                 # MANTICORE_DATA_DIR still wins in both modes, same as before this round
DEFAULT_RUNTIME = paths.runtime_dir()      # unchanged: forge_runtime/ always stays in the program folder
# Clicks that answer "the question Forge is asking now". With bridge protocol 2 each one carries "at", the question number from the
# snapshot the player was looking at; the bridge drops (and reports) a click whose question has already gone, instead of applying it
# to the next question or losing it depending on timing. That makes a replayed journal repeat the game exactly (round 22).
NUMBERED = ("ok", "cancel", "card", "player", "mana")
BRIDGE_MAIN = "forge.bridge.Main"
NET_HOST_MAIN = "forge.bridge.NetHost"     # round MP1: a 1v1 game with a friend's table on the network (HostSession)
ONLINE_MESSAGES = ("hosting", "upnp", "guest_joined", "join_refused", "peer_left", "refused_cmd", "hosting_cancelled",
                   "host_failed",
                   # round MP2: the guest's connection dropped / came back; someone started / stopped watching; and the guest
                   # table's own notes about its connection (made by NetSession's reader, never sent by the host)
                   "peer_dropped", "peer_back", "spectator_joined", "spectator_left", "net_dropped", "net_back", "net_lost",
                   # round MP2c: online games of 3-4 (guests waiting for each other, one leaving before the start)
                   "lobby", "guest_left_lobby",
                   # round MP2b: who sits where (for the host's save), and the play-back of a saved online game
                   "seats", "replay", "replay_done")
REPLIES_KEPT = 200                      # round MP2: answers kept to send again if the host asks the same question after a reconnect
MIN_JAVA = 17

PHASES = ["UNTAP", "UPKEEP", "DRAW", "MAIN1", "COMBAT_BEGIN", "COMBAT_DECLARE_ATTACKERS",
          "COMBAT_DECLARE_BLOCKERS", "COMBAT_FIRST_STRIKE_DAMAGE", "COMBAT_DAMAGE", "COMBAT_END",
          "MAIN2", "END_OF_TURN", "CLEANUP"]
PHASE_LABELS = {"UNTAP": "Untap", "UPKEEP": "Upkeep", "DRAW": "Draw", "MAIN1": "Main 1",
                "COMBAT_BEGIN": "Combat", "COMBAT_DECLARE_ATTACKERS": "Attack",
                "COMBAT_DECLARE_BLOCKERS": "Block", "COMBAT_FIRST_STRIKE_DAMAGE": "First strike",
                "COMBAT_DAMAGE": "Damage", "COMBAT_END": "End combat", "MAIN2": "Main 2",
                "END_OF_TURN": "End", "CLEANUP": "Cleanup"}


class ForgeUnavailable(Exception):
    """Java or the Forge runtime is missing; the message says what to install."""


# ---------------------------------------------------------------------------
# finding Java and the runtime
# ---------------------------------------------------------------------------
def bundled_java_path():
    """program folder/jre/bin/java(.exe): the jlinked Temurin 21 the Round 29 installer ships beside the program."""
    return os.path.join(paths.program_dir(), "jre", "bin", "java.exe" if os.name == "nt" else "java")


def bundled_runtime_expected():
    """Round 29 (§4.3): True for an INSTALLED copy that must run on its own jre/ - the installer's frozen build, or an unpacked
    copy that has a jre/ folder. Such a copy never falls back to some other Java on the PC: the alpha has to run on the Java it
    was tested with, and a missing one is a broken install to report plainly. Karl's own (portable) copy has no jre/ and
    keeps the old order (JAVA_HOME, then PATH); so does an unpacked nightly zip without a jre/, which has never shipped Java."""
    if paths.is_portable():
        return False
    return bool(getattr(sys, "frozen", False)) or os.path.isdir(os.path.join(paths.program_dir(), "jre"))


def find_java():
    """Path of a Java 17+ executable, or None. An installed copy (bundled_runtime_expected) uses ONLY its own jre/ - None when
    that is missing or won't start; any other copy tries JAVA_HOME, then PATH."""
    if bundled_runtime_expected():
        path = bundled_java_path()
        return path if os.path.isfile(path) and java_major(path) >= MIN_JAVA else None
    candidates = []
    home = os.environ.get("JAVA_HOME")
    if home:
        candidates.append(os.path.join(home, "bin", "java.exe" if os.name == "nt" else "java"))
    found = shutil.which("java")
    if found:
        candidates.append(found)
    for path in candidates:
        if path and os.path.exists(path) and java_major(path) >= MIN_JAVA:
            return path
    return None


def java_major(java):
    """Major version of a java executable (17, 21, ...), 0 if it can't be read."""
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=20,
                             env=_clean_env()).stderr
    except (OSError, subprocess.SubprocessError):
        return 0
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
    if not m:
        return 0
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major


def _clean_env():
    env = dict(os.environ)
    env.pop("JAVA_TOOL_OPTIONS", None)       # its "Picked up ..." banner would land on stderr, not the wire; harmless but noisy
    return env


BRIDGE_SOURCE = os.path.join(paths.program_dir(), "java_bridge", "forge_bridge.jar")
BUNDLE_MANIFEST = os.path.join(paths.program_dir(), "forge_bundle", "forge_runtime.manifest.json")
_COMMIT_RE = re.compile(r"commit\s+([0-9a-f]{7,40})", re.I)


def runtime_commit(runtime=None):
    """Round FB1: the Forge commit named on the first line of forge_runtime/VERSION.txt (setup_forge.py writes it), in
    lower case, or None when there is no such file or line (a runtime put together by hand, or a test's own)."""
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    try:
        with open(os.path.join(runtime, "VERSION.txt"), "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return None
    m = _COMMIT_RE.search(first)
    return m.group(1).lower() if m else None


def bundle_commit(manifest=None):
    """Round FB1: the Forge commit forge_bundle/ was built from (its manifest's forge_commit), or None when there is no
    readable manifest (an installed copy ships forge_runtime/ itself and no forge_bundle/)."""
    try:
        with open(manifest or BUNDLE_MANIFEST, "r", encoding="utf-8") as f:
            commit = json.load(f).get("forge_commit")
    except (OSError, ValueError, AttributeError):
        return None
    return str(commit).lower() if commit else None


def runtime_outdated(runtime=None, manifest=None):
    """Round FB1: (installed, bundled) as 7-character commits when forge_runtime/ holds a different Forge build from the one
    in forge_bundle/, else None. Updating the program brings a new forge_bundle/ (and a bridge compiled against it), but
    the unpacked forge_runtime/ stays the old one until setup_forge.py runs again - and the new bridge on the old Forge
    fails in ways that look like game bugs. Only judged when BOTH commits are known."""
    have, want = runtime_commit(runtime), bundle_commit(manifest)
    if not have or not want:
        return None
    n = min(len(have), len(want))
    if have[:n] == want[:n]:
        return None
    return have[:7], want[:7]


def sync_bridge(runtime=None, source=None):
    """java_bridge/forge_bridge.jar is the current bridge; copy it into the runtime folder when that copy is missing or
    different (so an update of this program never leaves an old bridge running). True when a copy was made.
    Round 28: in an installed copy the Round 29 installer ships the right jar already, and forge_runtime/ is
    program-folder-only - never write there from an installed copy. Does nothing (returns False) in that case."""
    if not paths.is_portable():
        return False
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    source = source or BRIDGE_SOURCE
    target = os.path.join(runtime, "forge_bridge.jar")
    if not os.path.isfile(source) or not os.path.isdir(runtime):
        return False
    try:
        if os.path.isfile(target) and filecmp.cmp(source, target, shallow=False):
            return False
        shutil.copy2(source, target)
        return True
    except OSError:                                # e.g. a Forge from an earlier run still holds the file open on Windows
        return False


def class_path(runtime):
    """The bridge comes BEFORE forge.jar: it carries two patched Forge classes (MulliganService: the free first mulligan;
    patch 45's InputPayMana: the payment waits while a mana ability is being paid), and the first jar on the class path wins."""
    return os.path.join(runtime, "forge_bridge.jar") + os.pathsep + os.path.join(runtime, "forge.jar")


def runtime_problem(runtime=None):
    """None when everything needed is present, else a plain-language sentence saying what is missing."""
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    installed = bundled_runtime_expected()           # Round 29: a friend has no Python, so never tell them to run setup_forge.py
    if not find_java():
        if installed:
            where = os.path.dirname(os.path.dirname(os.path.dirname(bundled_java_path())))
            if os.path.isfile(bundled_java_path()):
                return f"The bundled Java runtime in {where} won't start. Reinstall Manticore."
            return f"The bundled Java runtime is missing from {where}. Reinstall Manticore."
        return (f"Java {MIN_JAVA} or newer is not installed (or not on PATH). Install a JDK/JRE such as "
                "Temurin from adoptium.net, then start again.")
    fix = "Reinstall Manticore." if installed else "Run:  python setup_forge.py"
    for name in ("forge.jar", "forge_bridge.jar"):
        if not os.path.isfile(os.path.join(runtime, name)):
            return f"{name} is missing from {runtime}. {fix}"
    if not os.path.isdir(os.path.join(runtime, "res", "cardsfolder")):
        return f"The Forge card scripts are missing from {runtime}. {fix}"
    old = None if installed else runtime_outdated(runtime)
    if old:
        return (f"forge_runtime holds Forge {old[0]}, but this version of the program comes with Forge {old[1]} "
                f"(forge_bundle). {fix}")
    return None


# ---------------------------------------------------------------------------
# decks
# ---------------------------------------------------------------------------
_ALT_MODES = {}


def _slug(name):
    """Forge's card-script file name for a card name: lower case, apostrophes dropped, everything else between words -> '_'."""
    name = name.lower().replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_")


def alternate_mode(faces, runtime=None):
    """'Split', 'DoubleFaced', 'Modal', 'Adventure', ... for a card with several names, read from Forge's own card script
    (cardsfolder/<letter>/<face_face>.txt); None when the script is not there."""
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    slug = _slug(" ".join(faces))
    key = (runtime, slug)
    if key not in _ALT_MODES:
        mode = None
        try:
            path = next((p for p in (os.path.join(runtime, "res", "cardsfolder", folder, slug + ".txt")
                                     for folder in (slug[:1], "upcoming", "rebalanced")) if os.path.isfile(p)), None)
            with open(path or os.devnull, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("AlternateMode:"):
                        mode = line.split(":", 1)[1].strip()
                        break
                    if line.strip() == "ALTERNATE":
                        break
        except OSError:
            pass
        _ALT_MODES[key] = mode
    return _ALT_MODES[key]


# Round 28ba: faces are split on "/" or "//" WITH OR WITHOUT spaces. Only spaced slashes used to count, so a list line like
# "Funeral Room/Awakening Hall" (Karl's Teysa deck) reached Forge as that exact text, Forge answered "An unsupported card was
# requested" and dropped the card - while forge_knows(), guessing the script's file name, said the card was fine. No real
# card name has a slash except multi-faced ones.
_FACE_SPLIT = re.compile(r"\s*/{1,2}\s*")


def forge_card_name(name, runtime=None):
    """The name Forge's card database knows a card by. Moxfield writes two-faced cards as 'Front / Back'. Forge knows split
    cards (Fire // Ice) by both names, but every other two-faced card (transform, modal, adventure, ...) by its FRONT name
    only - asking for 'Barkchannel Pathway // Tidechannel Pathway' makes it drop the card from the deck."""
    name = name.strip()
    faces = [f.strip() for f in _FACE_SPLIT.split(name) if f.strip()]
    if len(faces) < 2:
        return name
    mode = alternate_mode(faces, runtime)
    if mode == "Split":
        return " // ".join(faces)
    return faces[0]


_NAME_INDEX = {}
_INDEX_THREADS = {}          # patch 38: runtime -> the thread building its index in the background
_INDEX_LOCK = threading.Lock()
CARD_INDEX_FILE = "forge_card_names.json"     # patch 38: the index kept on disk, keyed by the Forge build


def _index_key(runtime):
    """Which Forge build an index belongs to: forge_runtime/VERSION.txt ("Forge 2.0.16-SNAPSHOT | commit fb4d809"), or None."""
    try:
        with open(os.path.join(runtime, "VERSION.txt"), encoding="utf-8", errors="replace") as f:
            return f.read().strip() or None
    except OSError:
        return None


def card_index_files():
    """Where a saved index is looked for: the one an installed build ships beside the program (tools/build_installer.py writes
    it), then the one this computer saved in its card cache."""
    return [os.path.join(paths.program_dir(), CARD_INDEX_FILE), os.path.join(paths.cache_dir(), CARD_INDEX_FILE)]


def _load_saved_index(runtime):
    key = _index_key(runtime)
    if not key:
        return None
    for path in card_index_files():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("forge") == key and isinstance(data.get("names"), list) and data["names"]:
            return set(data["names"])
    return None


def save_card_index(names, runtime, path=None):
    """Write an index (sorted names) for this runtime's Forge build. Atomic; a failure only costs a rebuild next time."""
    key = _index_key(runtime)
    if not key or not names:
        return None
    if path is None and os.environ.get("MANTICORE_NO_CARD_INDEX_SAVE"):      # the test suite: never into the project's cache/
        return None
    path = path or os.path.join(paths.cache_dir(), CARD_INDEX_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"forge": key, "names": sorted(names)}, f)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def build_card_name_index(runtime):
    """Every Name: line of every card script under res/cardsfolder (a set), or None when the folder is missing. Reads about
    34,000 files: seconds on Karl's PC, and 50 seconds on the first start of an installed copy on his Surface (soak/bug report
    of 4 Oct: x64 emulation, a cold disk and the virus scanner) - so the GUI never waits for this (patch 38)."""
    root = os.path.join(runtime, "res", "cardsfolder")
    if not os.path.isdir(root):
        return None
    names = set()
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".txt"):
                continue
            try:
                with open(os.path.join(dirpath, fn), "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("Name:"):
                            names.add(line.split(":", 1)[1].strip())
            except OSError:
                continue
    return names


def _build_and_keep(runtime):
    names = build_card_name_index(runtime)
    if names:
        save_card_index(names, runtime)
    _NAME_INDEX[runtime] = names


def warm_card_index(runtime=None):
    """Patch 38: have the index ready without ever blocking - from a saved file at once, else built on a background thread
    (started once). Returns True when it is ready now."""
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    _card_name_index(runtime, wait=False)
    return card_index_ready(runtime)


def card_index_ready(runtime=None):
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    return runtime in _NAME_INDEX


def _card_name_index(runtime, wait=True):
    """Every card name Forge's database actually answers to, read straight from the Name: lines inside its card
    scripts (res/cardsfolder/**/*.txt), cached per runtime. A two-faced card's script declares one Name: line per
    face (front, then another after ALTERNATE for the back), so this knows a card like Blightstep Pathway or
    Malakir Rebirth by its front face alone - the way every deck site writes it. Forge's own FILE name for those
    scripts joins both faces together (blightstep_pathway_searstep_pathway.txt), which is not how anyone writes a
    decklist and is why guessing a file name from the pasted text (the old approach here) wrongly warned Karl that
    Forge did not have cards it actually has (confirmed by starting a real game with them: no card lost, no error
    logged - see the round 15 notes). Returns None when the folder itself is missing.
    Patch 38: a saved index for the same Forge build (shipped with an installed copy, or kept in the card cache) is used
    instead of reading every script; a new one is saved after a build. wait=False never blocks: it starts (or leaves
    running) a background build and returns None until it's done."""
    if runtime in _NAME_INDEX:
        return _NAME_INDEX[runtime]
    saved = _load_saved_index(runtime)
    if saved:
        _NAME_INDEX[runtime] = saved
        return saved
    with _INDEX_LOCK:
        t = _INDEX_THREADS.get(runtime)
        if t is None and runtime not in _NAME_INDEX:
            t = threading.Thread(target=_build_and_keep, args=(runtime,), daemon=True, name="card-name-index")
            _INDEX_THREADS[runtime] = t
            t.start()
    if not wait:
        return _NAME_INDEX.get(runtime)
    if t is not None:
        t.join()
    return _NAME_INDEX.get(runtime)


def forge_knows(name, runtime=None, wait=True):
    """True/False: does Forge's card database have this card, under any of its face names? None when the script
    folder is not there at all (then nothing can be said). Used to warn about typos in a pasted deck before Forge
    silently drops the card. Checks the real Name: lines Forge's scripts declare first (see _card_name_index), so a
    two-faced card is recognised by its front face alone; falls back to the old guess-a-file-name check only if a
    script's Name: line could not be read for some reason. Patch 38: wait=False (the deck screen) answers None while
    the index is still being built, instead of freezing the window."""
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    root = os.path.join(runtime, "res", "cardsfolder")
    if not os.path.isdir(root):
        return None
    name = name.strip()
    index = _card_name_index(runtime, wait)
    if index is None and not wait:
        return None
    faces = [f.strip() for f in _FACE_SPLIT.split(name) if f.strip()]
    if index:
        if name in index or any(f in index for f in faces):
            return True
    # fallback: guess the script's file name from the text (catches anything the index missed)
    for slug in {_slug(" ".join(faces)), _slug(faces[0]) if faces else ""}:
        if not slug:
            continue
        # a-z folders hold the cards; 'upcoming' and 'rebalanced' hold newer / Alchemy cards that Forge loads too
        for folder in (slug[:1], "upcoming", "rebalanced"):
            if os.path.isfile(os.path.join(root, folder, slug + ".txt")):
                return True
    return False


def unknown_cards(names, runtime=None, wait=True):
    """The card names (each once, in first-seen order) that Forge's database does not have. wait=False (patch 38): [] while the
    index is still being built."""
    seen, missing = set(), []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        if forge_knows(n, runtime, wait) is False:
            missing.append(n)
    return missing


def write_deck_file(path, commanders, deck, name="Deck", runtime=None, fmt=None):
    """Write a Forge .dck file. `deck` is a list of card names (repeats allowed). Round FMT1: a Brawl deck's [metadata] says
    "Deck Type=Brawl" (formats.dck_lines); a Commander deck's file is exactly what it was before."""
    import formats
    counts = {}
    for card in deck:
        n = forge_card_name(card, runtime)
        counts[n] = counts.get(n, 0) + 1
    lines = ["[metadata]", f"Name={name}"] + formats.dck_lines(fmt) + ["[Commander]"]
    lines += [f"1 {forge_card_name(c, runtime)}" for c in commanders]
    lines.append("[Main]")
    lines += [f"{n} {c}" for c, n in counts.items()]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


DEFAULT_MEMORY_MB = 3072          # Java's heap limit (-Xmx) on a normal PC
SMALL_PC_MEMORY_MB = 2048         # round 28d: on a PC with less than SMALL_PC_RAM_MB of RAM (see default_memory_mb)
SMALL_PC_RAM_MB = 8 * 1024 - 512  # "8 GB" PCs report a little under 8192 MB; below this counts as small


def previous_engine_log(path=None):
    """Where the engine log of the Forge run BEFORE this one is kept (round 28d)."""
    path = path or os.path.join(DATA_DIR, "forge_engine.log")
    root, ext = os.path.splitext(path)
    return root + ".prev" + ext


def keep_previous_engine_log(path):
    """Round 28d: forge_engine.log is rewritten ("w") every time Forge starts, so the log of a game that crashed was gone as
    soon as the next game (or the next start of the program) began - exactly when a crash report is offered. Move it to
    forge_engine.prev.log first. Never raises: a locked or missing file just means nothing is kept."""
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            os.replace(path, previous_engine_log(path))
    except OSError:
        pass


def total_ram_mb():
    """This computer's physical memory in MB, or None when it can't be read. No extra library: Windows asks the system
    (GlobalMemoryStatusEx), Linux reads /proc/meminfo, macOS asks sysctl."""
    try:
        if os.name == "nt":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _Status()
            st.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys // (1024 * 1024))
            return None
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) // (1024 * 1024)
    except Exception:
        return None


def small_pc(ram_mb="ask"):
    """True when this PC has less memory than Manticore is comfortable with (round 28d, ALPHA_FINISH_LINE must-have 14)."""
    ram_mb = total_ram_mb() if ram_mb == "ask" else ram_mb
    return ram_mb is not None and ram_mb < SMALL_PC_RAM_MB


def default_memory_mb(ram_mb="ask"):
    """Java's heap limit for this PC: DEFAULT_MEMORY_MB, or SMALL_PC_MEMORY_MB on a small PC so Forge doesn't push Windows
    into swapping. UNVERIFIED which figure a 4-player game really needs: the bridge now reports peakLiveMb at game over and
    the soak summary shows the largest, so these numbers can be set from real games."""
    return SMALL_PC_MEMORY_MB if small_pc(ram_mb) else DEFAULT_MEMORY_MB


def bridge_stamp(runtime=None):
    """Round 28d: the source stamp inside the bridge jar Forge runs (bridge_source.sha256, written by setup_forge.build_bridge),
    first 12 characters, or None. Part of a saved game's replay key."""
    import zipfile
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
    try:
        with zipfile.ZipFile(os.path.join(runtime, "forge_bridge.jar")) as z:
            return z.read("bridge_source.sha256").decode("ascii", "replace").strip()[:12] or None
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def new_seed():
    """A fresh shuffle seed. Every game gets one (and it is written into bug reports), so a report can be replayed against the same shuffles:
    Forge's shuffles and its AI both draw from the seeded generator (checked: the same seed and the same clicks gave the same 165 log lines twice)."""
    return random.SystemRandom().randrange(1, 2 ** 31 - 1)


# ---------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------
class ForgeSession:
    """One running game. Call poll() from the GUI thread each frame; everything else is thread-safe enough
    for a single GUI thread (the reader thread only appends to a queue)."""

    def __init__(self, deck_path, opponent_paths, name="You", seed=None, runtime=None, record_path=None,
                 java=None, memory_mb=None, command=None, dev=False, faults=(), loop_cap=None, fmt=None,
                 classic_stops=False):
        import formats
        self.deck_path = deck_path
        # Round FMT1: the game's format. None = what the player's .dck says ("Deck Type=Brawl"), so a resumed game, a bug report
        # replay and anything else that starts from saved .dck files plays the format it was started in.
        self.fmt = formats.normal(fmt) if fmt else (formats.from_dck_file(deck_path) if deck_path else formats.DEFAULT)
        self.format_used = None             # round FMT1: the format the bridge's "ready" line reports
        self.opponent_paths = list(opponent_paths)
        self.name = name
        self.seed = seed
        self.runtime = runtime or os.environ.get("FORGE_RUNTIME") or DEFAULT_RUNTIME
        self.record_path = record_path
        self.java = java
        self.dev = dev                      # start the bridge with --dev so setup() works (tests and card_check.py only; the game never does)
        self.command = command              # a full command line to run instead of Java (tests use a stand-in bridge)
        self.memory_mb = memory_mb or default_memory_mb()      # round 28d: less on a PC with under 8 GB of RAM
        self.ai_timeout = None              # round 28d: seconds an AI may think (the bridge's "ready" says)
        self.game_over_info = {}            # round 28d: the bridge's game_over message (peakHeapMb, peakLiveMb, maxHeapMb)
        self.proc = None
        self.inbox = queue.Queue()
        self.state = None                   # latest full snapshot (a dict), or None before the first one
        self.state_version = 0              # increments on every new snapshot
        self.log = []                       # [{"type", "text"}]
        self.sent_log = deque(maxlen=400)   # (time, command) for the newest commands sent to the engine
        self.sent_all = []                  # every command since the game started: with the seed, what a bug report needs to replay the game
        self.log_total = 0                  # entries ever received (self.log keeps only the newest 2000)
        self.requests = deque()             # unanswered questions from the engine, oldest first
        self.infos = deque()                # "here is a list to look at" / messages, for the GUI to show
        self.ready = False
        self.game_over = False
        self.setups_done = 0
        self.fatal = None
        self.exited = False
        self.bad_action_at = 0.0
        self.stderr_path = os.path.join(DATA_DIR, "forge_engine.log")
        self.crash_dir = None               # patch 46: where Java writes its own crash report (the folder forge_engine.log is in)
        self._record = None
        self._err = None
        self._cards = {}                    # id -> latest card dict seen anywhere
        self._t0 = None
        self.on_send = None                 # the table's hook: called with each command sent (engine-reply timing, round 21's journal)
        self.protocol = 1                   # the bridge's "ready" line says 2 when it sends events, prompt kinds and question numbers (round 22)
        self.events = deque(maxlen=20000)   # (event dict, time.monotonic() received), oldest first; the table drains it (round 22)
        self.last_rx = time.monotonic()     # when the engine last sent anything ("engine not responding", round 22)
        self.dropped = []                   # commands the bridge refused because they answered an old question (round 22)
        self.checks = []                    # round 27d: the bridge's own rule checks that failed ({"q", "rule", "detail"}); should stay empty
        self.shapes = {}                    # round 27d: (question, shape) -> how many times Forge asked that kind of question
        self.faults = list(faults)          # round 27d: deliberate bridge mistakes for tests (needs dev=True); see Checks.java
        self.loop_cap = loop_cap            # round 28e: the AI loop guard's limit for tests (needs dev=True; None = the bridge's own 10, 0 = off)
        self.loop_guards = []               # round 28e: {"t": "ai_loop_guard", ...} - the bridge stopped an AI repeating one ability
        self.loop_cap_used = None           # round 28e: the limit the bridge's "ready" line reports
        # Patch 43: the bridge passes priority for me when nothing meaningful is happening (Passing.java). classic_stops=True asks for
        # every stop as before (the card check's scripted boards rely on them); "passing" is what the bridge's "ready" line says.
        self.classic_stops = bool(classic_stops)
        self.passing = None
        self.passed = []                    # patch 43: {"t":"passed","card","player","kind"} - an opponent's trigger/ability passed for me
        self._checks_noted = set()
        # Round MP1 (online play). None/"host"/"guest"; the rest are the network host's own messages (java_bridge NetHost).
        self.online = None
        self.peer_name = None               # the other person's name (the guest's, or the host's)
        self.peer_code = None               # their copy's version code (a different one is only a warning)
        self.hosting = None                 # {"t":"hosting","port","bind"}: NetHost is ready for the guest
        self.upnp = None                    # {"t":"upnp","ok",...}: what the router said (see Upnp.java)
        self.join_refusals = []             # {"t":"join_refused","reason"}: someone tried to join and was turned away
        self.peer_left = None               # {"t":"peer_left","who","why"}: the other table has gone
        self.refused_cmds = []              # {"t":"refused_cmd","c"}: the host's engine refused one of the guest's commands
        self.hosting_cancelled = False
        self.host_failed = None             # {"t":"host_failed","reason","text"}: NetHost couldn't start (the port, the keystore...)
        # Round MP2
        self.peer_dropped = None            # {"t":"peer_dropped","who","name","why","grace"} while the guest's seat waits for it
        self.peer_drops = 0                 # how many times the guest's connection dropped (and peer_backs, how many came back)
        self.peer_backs = 0
        self.spectators = []                # names of the people watching (in the order they came)
        self.spectator = False              # this table is only watching (NetSession(watch=True))
        self.net_state = None               # the guest's own connection: None (not online) / "connected" / "reconnecting" / "lost"
        self.net_lost = None                # {"t":"net_lost","reason","text"}: reconnecting failed for good
        self.net_drops = 0
        self._replied = {}                  # request id -> the value answered (a resent question gets the same answer again)
        # Round MP2c
        self.lobby = None                   # {"t":"lobby","players":[names],"joined","wanted","ai"}: who is at the table so far
        self.guests_joined = []             # the host's view: guests who have joined, in order
        self.players_left = []              # peer_left messages about a player leaving a game of 3 or more (the others play on)
        self.dropped_seats = {}             # seat number -> its peer_dropped message, while that guest is away
        self.peer_back_name = None          # the newest peer_back's name
        self.seat_list = []                 # round MP2b: [{"seat","name","deck"}] from the host's bridge's "seats" message

    # ---- lifecycle ----
    def start(self):
        if self.command:
            cmd, cwd = list(self.command), None
        else:
            problem = runtime_problem(self.runtime)
            if problem:
                raise ForgeUnavailable(problem)
            java = self.java or find_java()
            cp = class_path(self.runtime)
            self.crash_dir = self.crash_folder()
            java_crash.move_strays(self.runtime, self.crash_dir)     # patch 46: crash reports left in forge_runtime/ move out
            cmd = self.java_command(java, cp)
            cwd = self.runtime
        flags = 0x08000000 if os.name == "nt" else 0        # CREATE_NO_WINDOW: no console flashing on Windows
        keep_previous_engine_log(self.stderr_path)
        try:
            err = self._err = open(self.stderr_path, "w", encoding="utf-8", errors="replace")
        except OSError:
            err = subprocess.DEVNULL
        self.proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     creationflags=flags, env=self._child_env())
        if self.record_path:
            self._record = open(self.record_path, "w", encoding="utf-8")
        self._t0 = time.time()
        threading.Thread(target=self._read, daemon=True, name="forge-reader").start()
        return self

    def crash_folder(self):
        """Patch 46: the folder Java's own crash report goes to - the one forge_engine.log is in (the logs folder; a soak game's
        own folder). Created here: if it doesn't exist Java falls back to its working folder, forge_runtime/."""
        folder = os.path.dirname(os.path.abspath(self.stderr_path)) if self.stderr_path else DATA_DIR
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
        return folder

    def java_command(self, java, cp):
        """The bridge's command line. Patch 46: the crash-file options come before -cp (they are Java's, not the bridge's)."""
        return ([java, f"-Xmx{self.memory_mb}m", "-Dfile.encoding=UTF-8", "-Dio.netty.tryReflectionSetAccessible=true"]
                + java_crash.jvm_flags(self.crash_dir or self.crash_folder()) + ["-cp", cp] + self._bridge_args())

    def crash_report(self):
        """Patch 46: Java's own crash report for this session's engine (hs_err_pid<pid>.log), or None. Only once Java has
        died: a running engine has none. Looked for in the crash folder first, then where Java falls back to (forge_runtime/)."""
        pid = getattr(self.proc, "pid", None)
        if not pid or self.command:
            return None
        try:
            if self.proc.poll() is None:
                if not self.exited:
                    return None
                self.proc.wait(timeout=1.0)     # its output has ended: the process is a moment from gone
        except Exception:
            return None
        path = java_crash.report_for_pid(pid, self.crash_dir, self.runtime)
        try:                                     # pids are reused: an older report under the same number isn't this engine's
            if path and self._t0 and os.path.getmtime(path) < self._t0 - 2:
                return None
        except OSError:
            return None
        return path

    def _bridge_args(self):
        """The bridge's main class and its arguments (round MP1: HostSession runs NetHost instead)."""
        args = [BRIDGE_MAIN, "--deck", self.deck_path, "--name", self.name]
        for opp in self.opponent_paths:
            args += ["--opponent", opp]
        if self.seed is None:
            self.seed = new_seed()
        args += ["--seed", str(self.seed)]
        args += ["--parent-pid", str(os.getpid())]        # round 29a: the bridge exits the moment this process is gone (ParentWatch.java)
        if self.fmt != "commander":
            args += ["--format", self.fmt]                # round FMT1 (a Commander game's command line is unchanged)
        if self.classic_stops:
            args.append("--classic-stops")                # patch 43: every priority stop, as before
        if self.dev:
            args.append("--dev")
            for f in self.faults:
                args += ["--fault", f]
            if self.loop_cap is not None:
                args += ["--loop-cap", str(int(self.loop_cap))]
        return args

    def _child_env(self):
        return _clean_env()

    def _read(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.inbox.put(json.loads(line))
                except ValueError:
                    pass
        finally:
            self.inbox.put({"t": "_exit"})

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        if self.proc and self.alive():
            try:
                self.send(c="quit")
                self.proc.wait(timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass
            if self.alive():
                self.proc.kill()
        self._log_shapes()
        for name in ("_record", "_err"):
            f = getattr(self, name)
            if f:
                f.close()
                setattr(self, name, None)
        for stream in (getattr(self.proc, "stdin", None), getattr(self.proc, "stdout", None)):
            try:
                if stream:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _log_shapes(self):
        """Round 28b: when MANTICORE_SHAPES_LOG is set, append this session's shapes as one JSON
        line (tools/soak.py and the scenario tests both close many sessions; tools/coverage_report.py
        reads the file back to say which kinds of question were never exercised)."""
        path = os.environ.get("MANTICORE_SHAPES_LOG")
        if not path or not self.shapes:
            return
        flat = {"%s|%s" % k: n for k, n in self.shapes.items()}
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"shapes": flat}) + "\n")
        except OSError:
            pass

    # ---- messages in ----
    def poll(self, limit=200):
        """Apply queued messages from Forge. Returns how many were handled."""
        n = 0
        while n < limit:
            try:
                m = self.inbox.get_nowait()
            except queue.Empty:
                break
            self.handle(m)
            n += 1
        return n

    def handle(self, m):
        if self._record:
            self._record.write(json.dumps(m) + "\n")
        self.last_rx = time.monotonic()
        t = m.get("t")
        if t == "event":
            self.events.append((m, self.last_rx))
        elif t == "dropped":
            self.dropped.append(m)
        elif t == "check":
            self._note_check(m)
        elif t == "shape":
            key = (m.get("q"), m.get("shape"))
            self.shapes[key] = self.shapes.get(key, 0) + 1
        elif t == "state":
            self._absorb_state(m)
        elif t == "request":
            self._note_request(m)
        elif t == "log":
            self.log.extend(m.get("entries", []))
            self.log_total += len(m.get("entries", []))
            del self.log[:-2000]
        elif t in ("info", "message"):
            self.infos.append(m)
        elif t == "ready":
            self.ready = True
            self.protocol = m.get("protocol", 1)
            self.ai_timeout = m.get("aiTimeout")
            self.loop_cap_used = m.get("loopCap")
            self.format_used = m.get("format", "commander")
            self.passing = m.get("passing")         # patch 43: "smart" or "classic" (None: a bridge from before patch 43)
            if self.format_used != self.fmt and not self.command:
                # Round FMT1: a bridge from before the formats round ignores --format and would quietly play Commander
                self.fatal = (f"The engine started a {self.format_used.title()} game instead of {self.fmt.title()}: its bridge "
                              "(forge_bridge.jar) is older than this program. Run setup_forge.py again, or reinstall.")
        elif t == "game_over":
            self.game_over = True
            self.game_over_info = {k: v for k, v in m.items() if k != "t"}
        elif t in ONLINE_MESSAGES:
            self._note_online(t, m)
        elif t == "setup_done":
            self.setups_done += 1               # round 28c: a dev "setup" has finished applying (see SetupState.java)
        elif t == "ai_loop_guard":
            self._note_loop_guard(m)
        elif t == "passed":
            self.passed.append(m)                   # patch 43: shown in the table's feed
            del self.passed[:-200]
        elif t == "bad_action":
            self.bad_action_at = time.time()
        elif t == "fatal":
            self.fatal = m.get("text", "Forge stopped")
        elif t == "_exit":
            self.exited = True

    def _note_online(self, t, m):
        """Round MP1: the network host's own messages (never sent in an ordinary game)."""
        if t == "hosting":
            self.hosting = m
        elif t == "upnp":
            self.upnp = m
        elif t == "guest_joined":
            if self.peer_name is None:                 # the first guest (MP1's one guest)
                self.peer_name, self.peer_code = m.get("name"), m.get("code")
            self.guests_joined.append(m.get("name") or "Guest")
        elif t == "guest_left_lobby":                  # round MP2c: gone before the game started; the seat is free again
            name = m.get("name") or "Guest"
            if name in self.guests_joined:
                self.guests_joined.remove(name)
            if self.peer_name == name:
                self.peer_name = self.guests_joined[0] if self.guests_joined else None
        elif t == "lobby":
            self.lobby = m
        elif t == "seats":                             # round MP2b: who sits where (the host's save)
            self.seat_list = list(m.get("guests") or [])
        elif t == "join_refused":
            self.join_refusals.append(m)
        elif t == "peer_left":
            if m.get("who") == "guest" and int(m.get("players") or 2) > 2:
                self.players_left.append(m)            # round MP2c: one of three or four left; the others play on
                self.dropped_seats.pop(m.get("seat"), None)
                self.peer_dropped = next(iter(self.dropped_seats.values()), None)
            else:
                self.peer_left = m
        elif t == "refused_cmd":
            self.refused_cmds.append(m)
        elif t == "hosting_cancelled":
            self.hosting_cancelled = True
        elif t == "host_failed":
            self.host_failed = m
        elif t == "peer_dropped":                      # round MP2
            self.dropped_seats[m.get("seat")] = m
            self.peer_dropped = m
            self.peer_drops += 1
        elif t == "peer_back":
            self.dropped_seats.pop(m.get("seat"), None)
            self.peer_dropped = next(iter(self.dropped_seats.values()), None)
            self.peer_backs += 1
            self.peer_back_name = m.get("name") or None
        elif t == "spectator_joined":
            self.spectators.append(m.get("name") or "Someone")
        elif t == "spectator_left":
            name = m.get("name") or "Someone"
            if name in self.spectators:
                self.spectators.remove(name)
        elif t == "net_dropped":
            self.net_state = "reconnecting"
            self.net_drops += 1
        elif t == "net_back":
            self.net_state = "connected"
        elif t == "net_lost":
            self.net_state = "lost"
            self.net_lost = m

    def _note_request(self, m):
        """Round MP2: after an online guest reconnects, the host asks every question still waiting again, with the same id.
        One already on screen is not added twice; one already answered (the answer may have been lost with the old
        connection) gets the same answer again."""
        rid = m.get("id")
        if rid is not None and rid in self._replied:
            self.send(c="reply", id=rid, value=self._replied[rid])
            return
        if rid is not None and any(r.get("id") == rid for r in self.requests):
            return
        self.requests.append(m)

    def _note_loop_guard(self, m):
        """Round 28e: the bridge stopped an AI using one ability over and over (see java_bridge LoopGuard.java). Kept for the
        soak, and the first one per card goes to crash_log.txt so a bug report shows that the guard stepped in."""
        first = not any(g.get("card") == m.get("card") for g in self.loop_guards)
        self.loop_guards.append(m)
        if first:
            try:
                import crashlog
                crashlog.note("AI loop stopped", "%s used %s (%s) %s times in one turn; the AI was made to pass instead" % (
                    m.get("player"), m.get("card"), m.get("ability"), m.get("count")))
            except Exception:
                pass

    def _note_check(self, m):
        """A failed bridge self-check (round 27d): keep it, and write the first of each kind to crash_log.txt so it shows up in
        bug reports even when nobody notices anything odd at the table."""
        self.checks.append(m)
        extra = os.environ.get("MANTICORE_CHECKS_LOG")          # a soak run or test run can collect every failed check in one file
        if extra:
            try:
                with open(extra, "a", encoding="utf-8") as f:
                    f.write(json.dumps(m) + "\n")
            except OSError:
                pass
        key = (m.get("q"), m.get("rule"))
        if key in self._checks_noted:
            return
        self._checks_noted.add(key)
        try:
            import crashlog
            crashlog.note("Bridge self-check failed: %s / %s" % key, m.get("detail", ""))
        except Exception:
            pass

    def _absorb_state(self, m):
        self.state = m
        self.state_version += 1
        for p in m.get("players", []):
            for zone in p.get("zones", {}).values():
                for c in zone:
                    self._cards[c["id"]] = c

    # ---- messages out ----
    def send(self, **cmd):
        if not self.alive():
            return False
        if cmd.get("c") in NUMBERED and "at" not in cmd:
            at = (self.state or {}).get("inputSeq")
            if at is not None:                    # protocol 2: say which question this click answers (see NUMBERED)
                cmd["at"] = at
        try:
            now = time.time()
            self.sent_log.append((now, cmd))
            self.sent_all.append((now, cmd))
            if self._record:                      # round 27d: the record holds both directions, so a recorded game can be checked offline
                self._record.write(json.dumps({"t": "_sent", "time": now, "cmd": cmd}) + "\n")
            self.proc.stdin.write(json.dumps(cmd) + "\n")
            self.proc.stdin.flush()
            if self.on_send is not None:
                try:
                    self.on_send(cmd)
                except Exception:                  # a broken hook must never stop a command reaching the engine
                    pass
            return True
        except (OSError, ValueError):
            return False

    def click_card(self, card_id):
        return self.send(c="card", id=card_id)

    def click_player(self, player_id):
        return self.send(c="player", id=player_id)

    def setup(self, lines):
        """Put a board in place with Forge's own 'Setup Game State' lines, e.g. ["humanhand=Sol Ring", "aibattlefield=Forest;Forest", "humanlife=40"]
        (zones named are replaced, the rest is left alone). Needs a session started with dev=True; a normal game ignores it."""
        return self.send(c="setup", lines=list(lines))

    def ok(self):
        return self.send(c="ok")

    def cancel(self):
        return self.send(c="cancel")

    def use_mana(self, symbol):
        """Spend one floating mana ('W', 'U', 'B', 'R', 'G' or 'C') on the payment Forge is asking for."""
        return self.send(c="mana", color=symbol)

    def undo(self):
        return self.send(c="undo")

    def alpha_strike(self):
        return self.send(c="alpha")

    def concede(self):
        return self.send(c="concede")

    def set_stops(self, mine, phases):
        return self.send(c="stops", mine=bool(mine), phases=list(phases))

    # ---- passing for me (Forge's own "yield" features; the bridge makes the same calls Forge's window does) ----
    def yield_turn(self):
        """Pass priority until the end of this turn (what the End Turn button does)."""
        return self.send(c="yield", mode="turn")

    def yield_stack(self, stop_on_interrupt=True):
        """Pass until the stack is empty. With stop_on_interrupt an opponent's new spell ends the skip."""
        return self.send(c="yield", mode="stack" if stop_on_interrupt else "stackall")

    def yield_until(self, phase="UPKEEP"):
        """Pass until this phase of MY next turn (Forge phase name, e.g. "UPKEEP"); an attack or an opponent's spell ends it early."""
        return self.send(c="yield", mode="until", phase=phase)

    def yield_clear(self):
        """Stop whatever skip is running."""
        return self.send(c="yield", mode="clear")

    def auto_yield(self, key, on=True):
        """Always pass priority when this ability (a stack item's "key") is on top of the stack, or stop doing so."""
        return self.send(c="autoyield", key=key, on=bool(on))

    def trigger_decision(self, key, decision):
        """For one of MY optional ("you may") triggers: "accept" = always use it, "decline" = never, "ask" = ask me each time."""
        return self.send(c="trigger", key=key, decision=decision)

    def auto_pass(self, on):
        """Pass by itself whenever Forge finds nothing I can do; an opponent's spell or attack still stops it."""
        return self.send(c="autopass", on=bool(on))

    def reset_yields(self):
        """Forget every "always pass" and "always yes / no" and stop any skip."""
        return self.send(c="yieldreset")

    # ---- patch 43: only the meaningful stops (java_bridge Passing.java) ----
    def hold_priority(self):
        """Ctrl held while casting: the next time I get priority with my own spell or ability on top, it's a stop."""
        return self.send(c="hold")

    def pass_turn(self):
        """Shift+Enter: pass everything for the rest of this turn (I am still asked to block)."""
        return self.send(c="passturn")

    def full_control(self, mode="on"):
        """"on" / "off" (Ctrl+Shift), or "turn" (Ctrl: every stop until this turn ends)."""
        return self.send(c="fullcontrol", mode=mode)

    def always_stop(self, names, on=True):
        """Card names whose spells, triggers and abilities always stop me (when I can do something)."""
        return self.send(c="alwaysstop", names=list(names), on=bool(on))

    def passing_state(self):
        """{classic, fullControl, turnControl, passTurn, hold, passed, alwaysStop} from the newest snapshot ({} before patch 43)."""
        return ((self.state or {}).get("yield") or {}).get("passing") or {}

    def answer(self, request, value):
        """Reply to a request from the engine and drop it from the queue."""
        try:
            self.requests.remove(request)
        except ValueError:
            pass
        rid = request.get("id")
        if rid is not None:
            self._replied[rid] = value
            while len(self._replied) > REPLIES_KEPT:
                self._replied.pop(next(iter(self._replied)))
        return self.send(c="reply", id=rid, value=value)

    # ---- reading the state ----
    def me(self):
        s = self.state
        if not s:
            return None
        return next((p for p in s["players"] if p["id"] == s.get("me")), None)

    def opponents(self):
        s = self.state
        if not s:
            return []
        return [p for p in s["players"] if p["id"] != s.get("me")]

    def player(self, player_id):
        s = self.state
        return next((p for p in s["players"] if p["id"] == player_id), None) if s else None

    def card(self, card_id):
        return self._cards.get(card_id)

    def prompt_text(self):
        s = self.state
        return (s or {}).get("prompt", {}).get("message", "")

    def is_mulligan_prompt(self):
        return "keep your hand" in self.prompt_text().lower()


# ---------------------------------------------------------------------------
# Round MP1: online play (1v1 with a friend; the engine runs on the host's PC)
# ---------------------------------------------------------------------------
class _SocketProc:
    """Looks like the subprocess a ForgeSession expects (stdin, stdout, poll, wait, kill), over the guest's TLS connection."""

    def __init__(self, sock, rfile):
        import io
        self.sock = sock
        self.stdout = io.TextIOWrapper(rfile, encoding="utf-8", errors="replace", newline="\n")
        self.stdin = sock.makefile("w", encoding="utf-8", newline="\n")
        self.closed = False

    def poll(self):
        return 1 if self.closed else None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.closed = True
        forge_net.close_socket(self.sock)


class NetSession(ForgeSession):
    """The guest's side of an online game: the same messages as a local game, from the host's engine over the network. The
    table treats it like any ForgeSession. start() connects, checks the host's certificate against the invite code's
    fingerprint, sends the hello (name, password, deck) and waits for the answer; it raises forge_net.NetRefused with a
    plain-words message when that fails. It blocks for up to about 25 s, so the table calls it from a worker thread.

    Round MP2:
      * Reconnecting. When the connection drops in the middle of a game, the reader thread connects again by itself (same
        address, the host certificate seen the first time, the rejoin token from the welcome) for up to the host's grace
        period, and the table sees {"t":"net_dropped"} then {"t":"net_back"} (or "net_lost" and the end). The host asks
        every question still waiting again; ForgeSession._note_request keeps that from showing twice or losing an answer.
      * Heartbeats. The host sends {"t":"hb"} every few seconds; the reader answers {"c":"hb"} at once (they never reach
        the inbox or the recording), and SILENCE_SECONDS without any line counts as a dropped connection.
      * Leaving. close() tells the host {"c":"leave"} first, so the host's game ends at once instead of after the grace period.
      * Watching (watch=True): no deck and no seat; the snapshots show every hidden card face down, and the only command sent
        is "flush". A spectator whose connection drops does not reconnect (it would need the password again).
    The token, like the password, is never logged, recorded or put in a report."""

    _connect = staticmethod(forge_net.connect_tls)         # tests put a plain-socket stand-in here (tests/net_fake.py)
    _sleep = staticmethod(time.sleep)
    FINAL_REFUSALS = ("rejoin", "gone", "fingerprint", "version", "password", "full", "watch_full", "not_started", "deck")

    def __init__(self, address, port, name, password, deck_name, dck_text, fingerprint=None, code=None,
                 connect_timeout=None, welcome_timeout=None, record_path=None, watch=False, silence_seconds=None, room=None):
        super().__init__("", [], name=name, record_path=record_path)
        self.room = room                        # round MP2d: the host's room at a relay (address and port are the relay's)
        self.address, self.port = address, int(port)
        self.deck_name, self.dck_text = deck_name, dck_text
        self.fingerprint = fingerprint
        self.code = code
        self.connect_timeout = connect_timeout or forge_net.CONNECT_SECONDS
        self.welcome_timeout = welcome_timeout or forge_net.WELCOME_SECONDS
        self.silence_seconds = silence_seconds or forge_net.SILENCE_SECONDS
        self._password = password              # kept only until the hello is sent; never logged, never in a report
        self.online = "guest"
        self.spectator = bool(watch)
        self.seat = None
        self.watched_guest = None               # a spectator: the guest's name (the host's is peer_name)
        self.grace = 0                          # seconds the host keeps my seat after a drop (its welcome says)
        self.reconnects = 0                     # connections made again after a drop
        self._token = None                      # the rejoin secret from the welcome: never logged
        self._peer_fp = None                    # the host certificate's fingerprint, seen on the first connection
        self._closing = False
        self._ended = False                     # the game is over or the host left: a drop now is not reconnected
        self._wlock = threading.Lock()

    def __repr__(self):
        return "NetSession(%s:%s%s, %r)" % (self.address, self.port, " via relay" if self.room else "", self.name)

    # ---- connecting ----
    def _handshake(self, line):
        """Connect, send one line, read the host's answer. (sock, rfile, welcome) or NetRefused."""
        sock = self._connect(self.address, self.port, self._peer_fp or self.fingerprint, timeout=self.connect_timeout,
                             **({"room": self.room} if self.room else {}))
        rfile = None
        try:
            sock.settimeout(self.welcome_timeout)
            rfile = sock.makefile("rb")
            sock.sendall(line.encode("utf-8"))
            reply_line = rfile.readline(forge_net.REPLY_MAX_BYTES)
        except socket.timeout:
            forge_net.close_socket(sock)
            raise forge_net.NetRefused("no_answer") from None
        except OSError:
            forge_net.close_socket(sock)
            raise forge_net.NetRefused("closed") from None
        if not reply_line:
            forge_net.close_socket(sock)
            raise forge_net.NetRefused("closed")
        try:
            reply = json.loads(reply_line.decode("utf-8", "replace"))
        except ValueError:
            reply = {}
        if reply.get("t") == "refused":
            forge_net.close_socket(sock)
            reason = reply.get("reason") or "unknown"
            if self.spectator and reason == "full":
                reason = "watch_full"
            raise forge_net.NetRefused(reason, reply.get("text", ""))
        if reply.get("t") != "welcome":
            forge_net.close_socket(sock)
            raise forge_net.NetRefused("unknown", "the host sent something this program doesn't understand")
        return sock, rfile, reply

    def start(self):
        password, self._password = self._password, None
        if self.spectator:
            line = forge_net.watch_line(self.name, password, self.code)
        else:
            line = forge_net.hello_line(self.name, password, self.code, self.deck_name, self.dck_text)
        sock, rfile, reply = self._handshake(line)
        self.peer_name, self.peer_code, self.seat = reply.get("host"), reply.get("code"), reply.get("seat")
        self.watched_guest = reply.get("guest")
        self.players = int(reply.get("players") or 2)           # round MP2c: the whole table, AI seats included
        self._token = reply.get("token") or None
        try:
            self.grace = max(0, int(reply.get("grace") or 0))
        except (TypeError, ValueError):
            self.grace = 0
        try:
            der = sock.getpeercert(binary_form=True)
            if der:
                self._peer_fp = forge_net.fingerprint(der)
        except (AttributeError, ValueError, OSError):
            pass                                          # a plain socket (tests): reconnect with the invite's fingerprint
        sock.settimeout(self.silence_seconds)
        self.proc = _SocketProc(sock, rfile)
        self.net_state = "connected"
        if self.record_path:
            self._record = open(self.record_path, "w", encoding="utf-8")
        self._t0 = time.time()
        threading.Thread(target=self._read, daemon=True, name="net-reader").start()
        return self

    # ---- reading, and coming back after a drop ----
    def _read(self):
        try:
            while True:
                why = self._read_connection(self.proc)
                if self._closing or self._ended or self.spectator or not self._token or self.grace <= 0:
                    break
                if not self._reconnect(why):
                    break
        finally:
            self.inbox.put({"t": "_exit"})

    def _read_connection(self, proc):
        """Read one connection until it ends. Returns why it ended, in plain words."""
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                t = m.get("t") if isinstance(m, dict) else None
                if t == "hb":                              # the host checking the link: answer at once, keep it out of the game
                    self._write_line(proc, '{"c":"hb"}')
                    continue
                if t in ("peer_left", "game_over"):
                    self._ended = True
                self.inbox.put(m)
            return "the host closed the connection"
        except TimeoutError:
            return "nothing from the host for %d s" % self.silence_seconds
        except (OSError, ValueError) as e:
            return "the connection failed (%s)" % (e.__class__.__name__,)
        finally:
            proc.kill()

    def _reconnect(self, why):
        """Connect again with the rejoin token until the grace period runs out. True when the game goes on."""
        self.inbox.put({"t": "net_dropped", "why": why, "grace": self.grace})
        deadline = time.monotonic() + self.grace + forge_net.RECONNECT_EXTRA
        attempt = 0
        while not self._closing:
            attempt += 1
            try:
                sock, rfile, reply = self._handshake(forge_net.rejoin_line(self._token))
            except forge_net.NetRefused as e:
                if e.reason in self.FINAL_REFUSALS:
                    self.inbox.put({"t": "net_lost", "reason": e.reason, "text": str(e)})
                    return False
            except Exception:                              # anything else: the network isn't back yet
                pass
            else:
                sock.settimeout(self.silence_seconds)
                with self._wlock:
                    if self._closing:
                        forge_net.close_socket(sock)
                        return False
                    self.proc = _SocketProc(sock, rfile)
                self.reconnects += 1
                self.inbox.put({"t": "net_back", "attempts": attempt})
                return True
            wait = forge_net.reconnect_wait(attempt)
            if time.monotonic() + wait > deadline:
                break
            end = time.monotonic() + wait
            while not self._closing and time.monotonic() < end:
                self._sleep(0.1)
        if not self._closing:
            self.inbox.put({"t": "net_lost", "reason": "lost", "text": forge_net.join_message("lost")})
        return False

    # ---- writing ----
    def _write_line(self, proc, text, timeout=-1):
        """One line to the host, never two threads at once (the GUI's commands, the reader's heartbeat answers)."""
        if not self._wlock.acquire(timeout=timeout):
            return False
        try:
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False
        finally:
            self._wlock.release()

    def send(self, **cmd):
        if self.spectator and cmd.get("c") != "flush":
            return False                                   # a spectator only watches (the host would refuse it anyway)
        with self._wlock:
            return super().send(**cmd)

    def connected(self):
        """Round MP2: the connection is up right now (False while reconnecting)."""
        return self.alive() and self.net_state == "connected"

    def close(self):
        if self._wlock.acquire(timeout=1):
            try:
                self._closing = True
                proc = self.proc
            finally:
                self._wlock.release()
        else:
            self._closing = True
            proc = self.proc
        if proc is not None and not proc.closed:
            self._write_line(proc, json.dumps({"c": "leave"}), timeout=1)        # round MP2: no grace period, I'm going
            proc.kill()
        self._log_shapes()
        if self._record:
            self._record.close()
            self._record = None


class HostSession(ForgeSession):
    """The host's side of an online game: an ordinary ForgeSession whose bridge is NetHost (the host's seat, plus a TLS port
    for the friend's table). The game password and the keystore's password reach Java through its environment, never its
    command line (any program on the PC can read a command line)."""

    def __init__(self, deck_path, name, port, password, cert, guest_deck_path, upnp=True, bind="0.0.0.0", code="",
                 seed=None, runtime=None, java=None, memory_mb=None, record_path=None, dev=False, block_seconds=None,
                 stall_seconds=None, grace_seconds=None, heartbeat_seconds=None, silence_seconds=None, guests=1, ai_paths=(),
                 relay=None, journal_path=None, resume=None, drop_notice_delay_ms=None):
        super().__init__(deck_path, list(ai_paths), name=name, seed=seed, runtime=runtime, record_path=record_path, java=java,
                         memory_mb=memory_mb, dev=dev)
        self.guests_wanted = max(1, min(3, int(guests)))        # round MP2c: people joining over the network
        # round MP2d: (address, port) of a relay: NetHost then listens on 127.0.0.1 only and relay_agent brings the guests in
        self.relay = (relay[0], int(relay[1])) if relay else None
        self.relay_agent = None
        # round MP2b: NetHost writes every command it applies to journal_path; resume = {"replay": an earlier journal,
        # "guests": [(seat, name, deck path)]} plays a saved game back first (the seed and decks are the saved ones)
        self.journal_path = journal_path
        self.resume = resume
        self.resuming = bool(resume and resume.get("replay"))
        self.replay_progress = (0, 0)
        self.replay_result = None           # the bridge's {"t":"replay_done","applied","skipped","total","diverged"?}
        if resume:
            self.guests_wanted = max(1, min(3, len(resume.get("guests") or [])))
        if 1 + self.guests_wanted + len(self.opponent_paths) > 4:
            raise ValueError("an online game has at most 4 players")
        self.port, self.bind, self.upnp_wanted = int(port), bind, bool(upnp)
        if self.relay:
            self.bind, self.upnp_wanted = "127.0.0.1", False
        self._password = password
        self.cert = cert
        self.guest_deck_path = guest_deck_path
        self.code = code
        self.block_seconds, self.stall_seconds = block_seconds, stall_seconds
        # round MP2: how long a dropped guest's seat is kept (None = the bridge's 60 s, 0 = no reconnecting), and the heartbeat
        # timings (tests)
        self.grace_seconds, self.heartbeat_seconds, self.silence_seconds = grace_seconds, heartbeat_seconds, silence_seconds
        self.drop_notice_delay_ms = drop_notice_delay_ms   # tests only (round MP2e)
        self.online = "host"

    def __repr__(self):
        return "HostSession(port %s, %r)" % (self.port, self.name)

    def _bridge_args(self):
        args = [NET_HOST_MAIN, "--deck", self.deck_path, "--name", self.name, "--port", str(self.port), "--bind", self.bind,
                "--keystore", self.cert.path, "--guest-deck", self.guest_deck_path, "--code", self.code or ""]
        if self.seed is None:
            self.seed = new_seed()
        args += ["--seed", str(self.seed), "--parent-pid", str(os.getpid())]
        if self.upnp_wanted:
            args.append("--upnp")
        if self.dev:
            args.append("--dev")
        if self.block_seconds is not None:
            args += ["--block-seconds", str(int(self.block_seconds))]
        if self.stall_seconds is not None:
            args += ["--stall-seconds", str(int(self.stall_seconds))]
        for flag, value in (("--grace-seconds", self.grace_seconds), ("--heartbeat-seconds", self.heartbeat_seconds),
                            ("--silence-seconds", self.silence_seconds),
                            ("--drop-notice-delay-ms", self.drop_notice_delay_ms)):
            if value is not None:
                args += [flag, str(int(value))]
        if self.guests_wanted != 1 and not self.resume:
            args += ["--guests", str(self.guests_wanted)]
        for path in self.opponent_paths:                           # round MP2c: AI seats
            args += ["--opponent", path]
        if self.journal_path:                                      # round MP2b
            args += ["--journal", self.journal_path]
        if self.resume:
            if self.resume.get("replay"):
                args += ["--replay", self.resume["replay"]]
            for seat, name, deck in self.resume.get("guests") or []:
                args += ["--resume-guest", "%d|%s|%s" % (int(seat), name, deck)]
        return args

    def _child_env(self):
        env = _clean_env()
        env["MANTICORE_NET_PASSWORD"] = self._password or ""
        env["MANTICORE_KEYSTORE_PASS"] = self.cert.store_pass
        return env

    def handle(self, m):
        t = m.get("t")
        if self.resuming and t in ("request", "event"):
            return                  # round MP2b: the play-back answers these itself; what is still asked comes again at the end
        super().handle(m)

    def everyone_back(self):
        """Round MP2b: every guest of the resumed game has joined again."""
        names = {g.get("name") for g in self.seat_list} or {n for _s, n, _d in (self.resume or {}).get("guests") or []}
        return names <= set(self.guests_joined)

    def _note_online(self, t, m):
        super()._note_online(t, m)
        if t == "replay":
            self.replay_progress = (m.get("done") or 0, m.get("total") or 0)
        elif t == "replay_done":
            self.resuming = False
            self.replay_result = m
        if t == "hosting" and self.relay and self.relay_agent is None:      # round MP2d: NetHost listens; open the room
            import relay_agent
            self.relay_agent = relay_agent.RelayAgent(self.relay[0], self.relay[1], self.port).start()

    def relay_status(self):
        """Round MP2d: (status, room, error) of the relay connection, or None when not playing through a relay."""
        a = self.relay_agent
        if not self.relay:
            return None
        if a is None:
            return ("connecting", None, None)
        return (a.status, a.room, a.error)

    def close(self):
        if self.relay_agent is not None:
            self.relay_agent.close()
        super().close()

    def cancel_host(self):
        """Stop waiting for the friend (the Cancel button on the Host screen)."""
        return self.send(c="cancel_host")

    def waiting(self):
        """True while the game hasn't started (round MP2c: until every guest the host asked for has joined)."""
        return not self.ready
