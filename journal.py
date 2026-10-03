# SPDX-License-Identifier: GPL-3.0-or-later
"""
journal.py - every game is written down as it is played, so a crash or a closed window never loses it.

saves/current_game.jsonl, one JSON object per line:
    {"t": "start", "v": 1, "seed": 123, "name": "Karl", "decks": {"player.dck": "...", "opponent1.dck": "..."}, "code": "a4c0bcc3", "at": 1790000000.0}
    {"t": "cmd", "i": 0, "dt": 1.25, "c": {"c": "ok"}}                 one per command sent to Forge (clicks, replies, stops, yields ...)
    {"t": "end", "result": "won" | "lost" | "conceded" | "closed", "at": ...}
A journal without an "end" line is an unfinished game: the next start offers to resume it (replay.Replayer sends the same commands to a
new engine with the same seed and decks). Finished journals move to saves/finished/ (the newest KEEP are kept).
The seed + decks + commands are exactly what a bug report's commands.json holds, so a journal can also be replayed with replay.py.
No pygame. The clock is passed in.
"""
import json
import os

KEEP = 10
FSYNC_EVERY = 20                  # commands between fsyncs (a flush happens on every line anyway)
CURRENT = "current_game.jsonl"


class GameJournal:
    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, CURRENT)
        self.f = None
        self.count = 0
        self.t0 = None

    def start(self, seed, name, decks, code, now, replay=None, printings=None, fmt=None):
        """Begin a new journal (an unfinished older one is moved to finished/ as 'abandoned' first, never overwritten)."""
        os.makedirs(self.folder, exist_ok=True)
        self.close()                                       # Windows cannot move a file that is still open
        if os.path.isfile(self.path):
            self._retire("abandoned", now)
        self.f = open(self.path, "w", encoding="utf-8")
        self.count, self.t0 = 0, now
        head = {"t": "start", "v": 1, "seed": seed, "name": name, "decks": decks, "code": code, "at": now}
        if replay:
            head["replay"] = replay          # round 28d: what decides whether this game can be replayed later (see replay_matches)
        if printings and any(printings):
            head["printings"] = printings    # round ALT1: [mine, AI 1's, ...] {name lower: [set, cn]} - the pictures, for Resume
        if fmt and fmt != "commander":
            head["format"] = fmt             # round FMT1: for people and tools; Resume reads it from player.dck's "Deck Type=Brawl"
        self._write(head)
        self._sync()

    def discard(self, now):
        """Round 28d: give up an unfinished game that can't be resumed (it is kept in finished/ as 'not_resumable')."""
        self.close()
        if os.path.isfile(self.path):
            self._retire("not_resumable", now)

    def reopen(self, count, t0):
        """Keep writing to the unfinished journal after a resume: the replayed commands are already in it (count of them)."""
        os.makedirs(self.folder, exist_ok=True)
        self.f = open(self.path, "a", encoding="utf-8")
        self.count, self.t0 = count, t0

    def active(self):
        return self.f is not None

    def close(self):
        """Stop writing but leave the game unfinished (the program is closing: the next start offers to resume it)."""
        if self.f is not None:
            try:
                self.f.close()
            except OSError:
                pass
            self.f = None

    def command(self, cmd, now):
        if self.f is None:
            return
        self._write({"t": "cmd", "i": self.count, "dt": round(now - self.t0, 3), "c": cmd})
        self.count += 1
        if self.count % FSYNC_EVERY == 0:
            self._sync()

    def end(self, result, now):
        if self.f is None:
            return
        self._write({"t": "end", "result": result, "at": now})
        self._sync()
        self.f.close()
        self.f = None
        self._retire(result, now)

    def _write(self, obj):
        self.f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.f.flush()

    def _sync(self):
        try:
            os.fsync(self.f.fileno())
        except OSError:
            pass

    def _retire(self, label, now):
        done = os.path.join(self.folder, "finished")
        os.makedirs(done, exist_ok=True)
        stamp = f"{now:014.3f}"                          # sorts by time; never reuses a name, so nothing is overwritten
        try:
            os.replace(self.path, os.path.join(done, f"{stamp}_{label}.jsonl"))
        except OSError:
            return
        old = sorted(os.listdir(done))
        for name in old[:-KEEP]:
            try:
                os.remove(os.path.join(done, name))
            except OSError:
                pass


def read(path):
    """(start dict, [commands], end dict or None) from a journal file; a torn last line (crash mid-write) is ignored."""
    start, cmds, end = None, [], None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            t = obj.get("t")
            if t == "start":
                start = obj
            elif t == "cmd":
                cmds.append(obj["c"])
            elif t == "end":
                end = obj
    return start, cmds, end


def unfinished(folder):
    """(start, commands) of an unfinished game to offer for resuming, or None."""
    path = os.path.join(folder, CURRENT)
    if not os.path.isfile(path):
        return None
    start, cmds, end = read(path)
    if start is None or end is not None or not cmds:
        return None
    return start, cmds


def replay_matches(start, current):
    """Round 28d: can a saved game be played back on this copy? A replay feeds Forge the same seed and the same clicks and
    expects the same game, which only holds for the same Forge build and the same bridge (its source stamp). The Python
    code ("code") may differ - Karl's own copy changes with every patch and his resumes must keep working. A journal from
    before round 28d has no key: it can't be checked, so it isn't offered."""
    saved = (start or {}).get("replay")
    if not isinstance(saved, dict) or not current:
        return False
    return all(saved.get(k) == current.get(k) for k in ("forge", "bridge"))
