# SPDX-License-Identifier: GPL-3.0-or-later
"""
online_save.py - Round MP2b: the host's saved online games, so one can be continued another day.

An online game can't be journalled by the host's table alone (the guests' clicks never pass through it), so the host's engine
(java_bridge NetHost --journal) writes every command it applies, from every seat, to cmds.jsonl. This module keeps the rest:

    saves/online/<id>/meta.json     who sat where, the seed, the deck files' names, the replay key, the turn reached
    saves/online/<id>/cmds.jsonl    NetHost's journal: {"s": seat, "c": {...}} per line
    saves/online/<id>/*.dck         every deck of that game (host.dck, guest_2.dck ..., ai_1.dck ...)
    saves/online/finished/          games that ended (or were given up), the newest KEEP of them

To continue a game, NetHost starts with the same seed, decks and names and plays cmds.jsonl back (--replay, ResumeFeeder);
the guests join again with the host's new invite code and get their own seats back. Like "Resume last game", it needs the same
Forge build and the same bridge (journal.replay_matches). Nothing here holds the game password, a rejoin token or an address.
"""
import json
import os
import random
import shutil
import time

FOLDER = "online"
KEEP = 5
META = "meta.json"
CMDS = "cmds.jsonl"


def root(saves_dir):
    return os.path.join(saves_dir, FOLDER)


def new_folder(saves_dir, now=None):
    """A fresh folder for a game about to be hosted (nothing is written into it until the game starts)."""
    now = time.time() if now is None else now
    name = time.strftime("%Y%m%d_%H%M%S", time.localtime(now)) + "_%03d" % random.randrange(1000)
    path = os.path.join(root(saves_dir), name)
    os.makedirs(path, exist_ok=True)
    return path


def journal_path(folder):
    return os.path.join(folder, CMDS)


def read_meta(folder):
    try:
        with open(os.path.join(folder, META), "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


def _write_meta(folder, meta):
    path = os.path.join(folder, META)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, path)


def start(folder, meta, decks):
    """The game has started: copy its deck files in and write meta.json. decks = {"host.dck": source path, ...}."""
    os.makedirs(folder, exist_ok=True)
    for name, src in decks.items():
        dst = os.path.join(folder, name)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
    meta = dict(meta, v=1)
    meta.setdefault("started", time.time())
    _write_meta(folder, meta)
    return meta


def note(folder, **fields):
    """Keep meta.json up to date (the turn reached), quietly: a save that can't be updated is still a save."""
    meta = read_meta(folder)
    if meta is None:
        return
    if all(meta.get(k) == v for k, v in fields.items()):
        return
    meta.update(fields)
    try:
        _write_meta(folder, meta)
    except OSError:
        pass


def count_lines(path):
    try:
        with open(path, "rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def open_folders(saves_dir):
    """Started, unfinished games, newest first."""
    base = root(saves_dir)
    try:
        names = sorted((n for n in os.listdir(base) if n != "finished"), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        folder = os.path.join(base, n)
        meta = read_meta(folder)
        if meta is not None and os.path.isdir(folder):
            out.append((folder, meta))
    return out


def latest(saves_dir):
    """(folder, meta) of the newest unfinished online game that has anything to play back, or None."""
    for folder, meta in open_folders(saves_dir):
        if count_lines(replay_source(folder) or "") > 0:
            return folder, meta
    return None


def replay_source(folder):
    """The fullest journal in the folder: cmds.jsonl, or an older copy kept by prepare_resume (a resume that stopped early
    leaves a shorter cmds.jsonl behind)."""
    try:
        files = [os.path.join(folder, n) for n in os.listdir(folder) if n.startswith("cmds") and n.endswith(".jsonl")]
    except OSError:
        return None
    files = [f for f in files if count_lines(f) > 0]
    return max(files, key=count_lines) if files else None


def prepare_resume(folder):
    """Keep the journal to play back under its own name (NetHost writes a fresh cmds.jsonl while it plays it back). Returns its path."""
    src = replay_source(folder)
    if src is None:
        return None
    n = 1
    while os.path.exists(os.path.join(folder, "cmds_%d.jsonl" % n)):
        n += 1
    dst = os.path.join(folder, "cmds_%d.jsonl" % n)
    shutil.copyfile(src, dst)
    return dst


def finish(folder, label, now=None):
    """The game ended (or was given up): move it to finished/, keeping the newest KEEP there."""
    now = time.time() if now is None else now
    if not folder or not os.path.isdir(folder):
        return
    done = os.path.join(os.path.dirname(folder), "finished")
    os.makedirs(done, exist_ok=True)
    try:
        os.replace(folder, os.path.join(done, "%s_%s" % (os.path.basename(folder), label)))
    except OSError:
        return
    for old in sorted(os.listdir(done))[:-KEEP]:
        shutil.rmtree(os.path.join(done, old), ignore_errors=True)


def forget_unstarted(folder):
    """A hosted game that never started (cancelled): its folder holds nothing worth keeping."""
    if folder and os.path.isdir(folder) and read_meta(folder) is None:
        shutil.rmtree(folder, ignore_errors=True)


def give_up_others(saves_dir, keep):
    """A new online game has started: older unfinished ones are kept in finished/ as "abandoned"."""
    for folder, _meta in open_folders(saves_dir):
        if os.path.abspath(folder) != os.path.abspath(keep):
            finish(folder, "abandoned")


def who(meta):
    """'Sam and Ann (and 1 AI)'."""
    names = [g.get("name") or "?" for g in meta.get("guests") or []]
    ai = len(meta.get("ai") or [])
    out = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else "nobody"
    return out + (" (and %d AI)" % ai if ai else "")


def describe(meta):
    """'turn 12, with Sam and Ann (2 Oct 21:40)'."""
    who_ = who(meta)
    when = time.strftime("%d %b %H:%M", time.localtime(meta.get("saved") or meta.get("started") or 0)).lstrip("0")
    turn = meta.get("turn")
    return "%swith %s (%s)" % ("turn %s, " % turn if turn else "", who_, when)
