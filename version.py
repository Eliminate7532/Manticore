# SPDX-License-Identifier: GPL-3.0-or-later
"""
version.py - says exactly which copy of the program is running (no pygame here, so it is easy to test).

Four things identify a copy, and they all go into the window title, `--version`, and crash_log.txt:
  VERSION      a number we raise by hand when something worth naming ships. Round 27a: follows the round instead of
               sitting at 0.1.0 forever - 0.<round>.0 for a round's first release, .1/.2/... for a fix on top of it.
  tag          the round's own git tag (e.g. "r27a"), if HEAD is exactly on one - see docs/WORKING_AGREEMENT.md.
  commit       the short id of the newest backup commit (read straight from the .git folder, so Git does not need to be installed)
  code         an 8-character fingerprint of every .py file next to this one. Unlike the commit id it also changes when you have
               edited files that are not backed up yet, and it is the same on every computer for the same code (line endings are
               ignored), so "code 9f8e7d6c" in a crash report tells us precisely what was running.
"""
import hashlib
import json
import os
import re
import sys
import zlib

import paths

VERSION = "0.28.44"                  # 0.27.0 Round 27 (licensing), 0.27.1 Round 27a (reliability), 0.27.2 Round 27b (a deck per AI, search, fetch fix), 0.27.3 Round 27c (bridge fixes), 0.27.4 Round 27d (bridge self-checks), 0.27.5 Round 27e (window fits the screen), 0.27.6 Round 28b (rule checks, soak bot, coverage report), 0.27.7 Round 28ba (soak you can trust), 0.28.0 Round 28 (a per-user data folder for an installed copy), 0.28.1 Round 28bb (soak night 1 fixes: winning froze the game, attach highlight, bot stalls), 0.28.2 Round 28bc (soak night 2 fixes), 0.28.3 Round 28bd (Cancel during a mana ability killed the game), 0.28.4 Round 28c (nightly run: card-check sweep, mid-game boards), 0.28.5 Round 28d (alpha hardening: crash-report offer, replay key, Forge build, unknown cards, Siege player choice, AI time limit, memory), 0.28.6 Round AD1 (Diablo look: palette, fonts, table backgrounds; built as 0.28.5 on round-ad1, renumbered when brought onto 28d), 0.28.7 Round 28e (the AI loop guard: night 9's endless Grim Monolith loop), 0.28.8 Rounds AD2b/AD2c/AD2 (VS, mulligan and Victory/Defeat screens; readability animations; stone frames, splash, title and main menu), 0.28.9 Round 29a (the bridge exits when the program that started it is gone; soak night 10 fixes), 0.28.10 Round 29b (scenario 7 flake; card-check set-ups for planeswalkers, Auras and the deck's commander), 0.28.11 Round UX1 (the first-game tour of the table), 0.28.12 Round AU1 (dark-fantasy sounds from CC0 recordings, ambience per table, music), 0.28.13 Round BAN1 (format legality on the deck screen; only legal commanders and pairs start), 0.28.14 Round MANA1 (mana symbols from the Mana font), 0.28.15 Round SPDX (licence headers), 0.28.16 Round ALT1 (alternate card art: a printing per card per deck, the Card art picker, your own pictures in my_art), 0.28.17 Round 29 (the Windows installer, the Manticore name; built as 0.28.9 on the round-29 branch, brought onto the queue after ALT1), 0.28.18 Round MP1 (play a friend online: Host / Join, 1v1, one Forge engine on the host), 0.28.19 Round 30 (an installed copy updates itself from GitHub Releases), 0.28.20 Round UI2 (the 200% text gaps; my_art backed up), 0.28.21 Round CHK1 (card checker: double-faced and flavor-name scripts, a creature of mine, three lands of every colour), 0.28.22 Round UI3 (Undo at 200% stacked under End Turn; card checker: a mana value 4+ permanent and creature cards in my graveyard), 0.28.23 Round UI4 (the yes/no window fits its words; partners on the VS screen fanned wide), 0.28.24 Round CHK2 (card checker: an enchanted creature of mine; combat-only targets are a SKIP), 0.28.25 Round OS1 (online soak: two false alarms from its first long run), 0.28.26 Round MP1a (two concede races found by the online soak; the online soak tool's concede and leave runs; built as 0.28.23 alongside UI4, renumbered after OS1), 0.28.27 Round SB1 (the soak bot declines a payment question that keeps coming back; a time-cap game is a warning; built as 0.28.24, renumbered), 0.28.28 Round PUB1 (the public source export works on the project and leaves Karl's notes and paths out; built as 0.28.25, renumbered), 0.28.29 Licences on the chain (patch 6: the pygame DLLs, AvQest, the Grok credit, five Java libraries; Forge's own modules count as the Forge component), 0.28.30 Round MP2a (online: a dropped connection reconnects by itself, heartbeats, leaving ends the game at once; up to four people can watch), 0.28.31 Rounds MP2b, MP2c and MP2d (a hosted online game can be continued another day; online games of three or four: up to three friends, AI players for the empty seats; playing through a relay when the host can't take incoming connections), 0.28.32 Round FMT1 (formats: MTG Arena's 100-card Brawl beside Commander - a format switch on the deck screen, Brawl's deck rules and banned list, 25/30 life and no commander damage in the engine, four Brawl sample decks; built on 1-25, rebuilt on MP2's 27), 0.28.33 Round FB1 (Forge updated from 3a74143 to fb4d809, 2 Oct 2026: Reality Fracture's last 16 cards, Forge's rules and AI fixes; the bridge drops three zone methods Forge removed; a forge_runtime older than forge_bundle is noticed and reinstalled; built on 0.28.30 after patch 25, rebuilt as patch 29 after MP2 and FMT1), 0.28.34 Round FB1b (what FB1's checks found: the host never says a friend dropped after saying they were back; the soak seat declares attackers, and uses Alpha Strike when Forge refuses an attack), 0.28.35 Round 31 title fix (the title and studio pictures shown whole at their own proportions, not cropped to the window), 0.28.36 Round 32 (UI polish from Karl's game of 3 Oct: a Menace tag on creatures with menace; Concede and return to the main menu; the 4-player VS screen keeps its frames inside the window; menus, buttons, the phase bar and the prompt headline in AvQest with plain digits), 0.28.37 Round UI5 (the opponents of a 3-4 player game: a small board gets one row of cards about twice as big, the command zone's title reads CMD and its frame stays in its row at 200% text, the panels' piles and commander damage stay inside them; built as 0.28.34 on patch 28, rebuilt as patch 33 after Round 32), 0.28.38 Round FR1 (Suggest a feature: a second tab in the F8 window - one text message to the same Discord, or to an optional ideas_webhook, and an idea_*.txt copy; F8 on the deck screen opens on it; the cog's button is Bug or idea; built as 0.28.35 on 28 + UI5, rebuilt as patch 34 after UI5's 33), 0.28.39 Patch 35 (the public repository github.com/Eliminate7532/Manticore: update_config.json names it for updates, NOTICES.json source_url points the Licenses window and --release builds at it), 0.28.40 Round ALT2 (MPC Autofill pictures in the Card art picker: a second tab with one card's community renders as thumbnails; only the picked picture is downloaded, its print bleed cut off, and kept per deck as a "# art: <card> = mpc:<id>" line), 0.28.41 Patch 37 (the installer allows Windows 11 on Arm, x64compatible; the soak no longer counts the AI loop guard's hand-on frame as bridge code, so a Forge AI StackOverflowError in a finished game is a warning again), 0.28.42 Patch 38 (the Card art window: a card size, - / + / Ctrl+wheel, remembered; picked MPC Autofill pictures kept in mpc_art/ at 1400 px; the alpha decks: Goreclaw in, the Kinnan list out to tests/fixtures/decks), 0.28.43 Patch 39 (the deck screen never waits for the card-name index: shipped with the installer, saved in the cache, built in the background - the Surface froze 50 s at first start; reports describe the program folder and name an emulated run; no crash-log entry while no release exists), 0.28.44 Patch 40 (card pictures imported into one deck from a .zip or a folder - Proxxied's ZIP Archive export: matched by file name, double-faced backs through Scryfall's card data, kept in deck_art/, written into that deck file only as "# art: <card> = image:<id>" lines; Undo and Remove imported)
APP_NAME = "Manticore"             # Round 29 (decision D1): was "Commander Sim"
BASE_DIR = paths.program_dir()            # Round 29: the program folder - for a frozen build, _MEIPASS (a .py's own __file__ is not reliable there)
BUILD_INFO_NAME = "build_info.json"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _head_sha(folder=BASE_DIR):
    """Full 40-character commit id HEAD points to, or None (no backup made yet, or Git files not readable)."""
    git = os.path.join(folder, ".git")
    head = _read(os.path.join(git, "HEAD"))
    if head is None:
        return None
    head = head.strip()
    if _HEX40.match(head):                                  # detached HEAD: the file holds the id itself
        return head
    if not head.startswith("ref:"):
        return None
    ref = head[4:].strip()
    sha = (_read(os.path.join(git, *ref.split("/"))) or "").strip()
    if not _HEX40.match(sha):                               # refs get packed into one file by 'git gc'
        for line in (_read(os.path.join(git, "packed-refs")) or "").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and _HEX40.match(parts[0]):
                sha = parts[0]
                break
    return sha if _HEX40.match(sha) else None


def build_info_path(folder=BASE_DIR):
    return os.path.join(folder, BUILD_INFO_NAME)


def _has_py_files(folder):
    try:
        return any(n.lower().endswith(".py") for n in os.listdir(folder))
    except OSError:
        return False


def uses_build_info(folder=BASE_DIR):
    """Round 29: True for a copy made by the installer build - frozen, or a build_info.json with no .py files beside it. Such a
    copy has no .git folder and no source to fingerprint (the hash of nothing is da39a3ee, which would make every friend's bug
    report look identical), so it reports what the build recorded instead."""
    if getattr(sys, "frozen", False):
        return True
    return os.path.isfile(build_info_path(folder)) and not _has_py_files(folder)


def build_info(folder=BASE_DIR):
    """The dict the build wrote into build_info.json ({version, commit, tag, code, built_at, forge, bridge_stamp}), or None."""
    try:
        with open(build_info_path(folder), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _info_text(folder, key):
    value = (build_info(folder) or {}).get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def commit(folder=BASE_DIR):
    """Short id of the commit the folder is on, or None (no backup made yet, or Git files not readable). An installed build
    reports the commit it was built from (build_info.json)."""
    if uses_build_info(folder):
        found = _info_text(folder, "commit")
        return found[:7] if found else None
    sha = _head_sha(folder)
    return sha[:7] if sha else None


def _loose_object(folder, sha):
    """(kind, body) of a loose git object (not yet packed by 'git gc'), or (None, None) if it isn't one."""
    path = os.path.join(folder, ".git", "objects", sha[:2], sha[2:])
    try:
        with open(path, "rb") as f:
            raw = f.read()
        data = zlib.decompress(raw)
    except (OSError, zlib.error):
        return None, None
    header, _sep, body = data.partition(b"\x00")
    kind = header.split(b" ", 1)[0].decode("ascii", "replace")
    return kind, body


def tag(folder=BASE_DIR):
    """The tag name (e.g. 'r27a') pointing straight at HEAD's commit, or None. Round notes are tagged after Karl's
    Windows test run passes (docs/WORKING_AGREEMENT.md); most of the time HEAD is a few commits past the last tag,
    and this correctly returns None then - it only reports an EXACT match, deliberately not the nearest tag behind
    HEAD (that would claim a round's release for commits it doesn't cover)."""
    if uses_build_info(folder):
        return _info_text(folder, "tag")
    head_sha = _head_sha(folder)
    if not head_sha:
        return None
    git_dir = os.path.join(folder, ".git")
    tags_dir = os.path.join(git_dir, "refs", "tags")
    try:
        names = sorted(os.listdir(tags_dir))
    except OSError:
        names = []
    for name in names:
        ref_sha = (_read(os.path.join(tags_dir, name)) or "").strip()
        if not _HEX40.match(ref_sha):
            continue
        if ref_sha == head_sha:
            return name
        kind, body = _loose_object(folder, ref_sha)                # an annotated tag points AT a commit, not IS one
        if kind == "tag":
            for line in body.split(b"\n"):
                if line.startswith(b"object ") and line[7:].decode("ascii", "replace") == head_sha:
                    return name
    peeled_for = None
    for line in (_read(os.path.join(git_dir, "packed-refs")) or "").splitlines():
        if line.startswith("^"):
            if peeled_for and line[1:].strip() == head_sha:
                return peeled_for
            peeled_for = None
            continue
        parts = line.split()
        peeled_for = None
        if len(parts) == 2 and parts[1].startswith("refs/tags/"):
            if parts[0] == head_sha:
                return parts[1][len("refs/tags/"):]
            peeled_for = parts[1][len("refs/tags/"):]
    return None


def code_fingerprint(folder=BASE_DIR):
    """8 hex characters that change whenever any .py file in the folder changes. An installed build has no .py files: it reports
    the fingerprint of the SOURCE folder it was built from (build_info.json), so it matches Karl's checkout of the same commit;
    with no such file it says "unknown", never the empty-hash code."""
    if uses_build_info(folder):
        return _info_text(folder, "code") or "unknown"
    h = hashlib.sha1()
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".py"))
    except OSError:
        return "unknown"
    if not names:
        return "unknown"
    for name in names:
        try:
            with open(os.path.join(folder, name), "rb") as f:
                data = f.read().replace(b"\r\n", b"\n")     # Windows and Linux copies of the same code must match
        except OSError:
            continue
        h.update(name.encode("utf-8") + b"\0" + data + b"\0")
    return h.hexdigest()[:8]


def describe(folder=BASE_DIR):
    """'Manticore 0.27.1 | commit a1b2c3d | code 9f8e7d6c', or with ' | tag r27a' inserted when HEAD is exactly
    on a round's tag (most of the time it isn't - see tag()). An installed build (Round 29) ends with
    ' | installed build 2026-10-02' (the day it was built; 'unknown' if build_info.json is missing)."""
    c = commit(folder)
    t = tag(folder)
    tag_part = f" | tag {t}" if t else ""
    built_part = ""
    if uses_build_info(folder):
        built_at = _info_text(folder, "built_at")
        built_part = f" | installed build {built_at[:10] if built_at else 'unknown'}"
    return f"{APP_NAME} {VERSION} | commit {c or 'none yet'}{tag_part} | code {code_fingerprint(folder)}{built_part}"


_FORGE_LINE = re.compile(r"^Forge\s+(\S+).*?commit\s+([0-9a-f]{7,40})", re.I)


def forge_build(runtime=None):
    """Round 28d (F1): which Forge this copy runs, from forge_runtime/VERSION.txt (written by setup_forge.py), as
    'Forge 2.0.15-SNAPSHOT | commit 3a74143', or None when it can't be read. A bug report must say which Forge it came from:
    new Forge builds change behaviour (ROADMAP Phase F). FORGE_RUNTIME points elsewhere, as for forge_client."""
    runtime = runtime or os.environ.get("FORGE_RUNTIME") or paths.runtime_dir()
    first = ((_read(os.path.join(runtime, "VERSION.txt")) or "").strip().splitlines() or [""])[0]
    m = _FORGE_LINE.match(first)
    if m:
        return f"Forge {m.group(1)} | commit {m.group(2)[:7]}"
    return first[:80] or None


def short(folder=BASE_DIR):
    """For the window title: '0.27.1 - code 9f8e7d6c'"""
    return f"{VERSION} - code {code_fingerprint(folder)}"
