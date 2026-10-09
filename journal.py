# SPDX-License-Identifier: GPL-3.0-or-later
"""
journal.py - every game is written down as it is played, so a crash or a closed window never loses it.

saves/current_game.jsonl, one JSON object per line:
    {"t": "start", "v": 1, "seed": 123, "name": "Karl", "decks": {"player.dck": "...", "opponent1.dck": "..."}, "code": "a4c0bcc3", "at": 1790000000.0}
        (patch 44: plus "stats": {"id", "format", "deck", "opponent_decks"} - see stats.py; round PRI1: plus "speed": "slow" when
         the game started in Slow - for reading only: the {"c": "speed"} command it sent is what replays it)
    {"t": "cmd", "i": 0, "dt": 1.25, "c": {"c": "ok"}}                 one per command sent to Forge (clicks, replies, stops, yields ...)
    {"t": "end", "result": "won" | "lost" | "conceded" | "closed", "at": ...}
Round UNDO1 adds three kinds of line (older copies of the program skip them; read() still returns only start / commands / end):
    {"t": "track", "i": 120}            from command 120 on, every command the bridge dropped has a "drop" line (see mark_drop)
    {"t": "drop", "i": 131}             command 131 never reached Forge's question (too late, the engine busy, a greyed button)
    {"t": "point", "i": 140, "turn": 6, "seq": 88, "req": null, "log": 512, "phase": "MAIN1", "start": false, "sum": "..."}
                                        a moment of my own turn Undo can go back to (rewind.py): 140 commands were sent before it
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
REWOUND = "rewound_last.jsonl"    # round UNDO1: the journal as it was before the latest rewind (for a bug report)
NUMBERED = ("ok", "cancel", "card", "player", "mana")     # the clicks that carry "at" (forge_client.NUMBERED) and can be dropped
RECENT = 400                      # numbered commands kept in memory to match a dropped message to its command


class GameJournal:
    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, CURRENT)
        self.f = None
        self.count = 0
        self.t0 = None
        self.recent = []                   # round UNDO1: [index, c, at, dropped] of the newest numbered commands

    def start(self, seed, name, decks, code, now, replay=None, printings=None, fmt=None, stats=None, speed=None):
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
        if stats:
            head["stats"] = stats            # patch 44: the game's id and decks for the stats, so a resumed game is the same game
        if speed == "slow":
            head["speed"] = speed            # round PRI1: for people and tools; Resume and Undo's rebuild send the "speed" command
                                             # the game itself sent (a command like any other), so a Slow game replays Slow
        self._write(head)
        self._write({"t": "track", "i": 0})          # round UNDO1: drops are recorded from the first command on
        self.recent = []
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
        self.recent = []
        self._write({"t": "track", "i": count})      # round UNDO1: from here on this program records dropped clicks

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
        if isinstance(cmd, dict) and cmd.get("c") in NUMBERED and cmd.get("at") is not None:
            self.recent.append([self.count, cmd.get("c"), cmd.get("at"), False])
            del self.recent[:-RECENT]
        self.count += 1
        if self.count % FSYNC_EVERY == 0:
            self._sync()

    def mark_drop(self, c, at):
        """Round UNDO1: the bridge said it dropped a click ({"t": "dropped", "c", "at", ...}). Write which command that was, so a
        rebuild can leave it out instead of guessing from timing. The message names the kind and the question, not the card, so it
        goes to the NEWEST command of that kind and question not yet marked: when two clicks hit one question and one is dropped,
        it is the later one (the earlier started a mana ability: "busy"; or was applied and moved the question on: "too late").
        Returns the command's index, or None (a click from before this journal was opened, or nothing matches)."""
        if self.f is None:
            return None
        for entry in reversed(self.recent):
            if not entry[3] and entry[1] == c and entry[2] == at:
                entry[3] = True
                self._write({"t": "drop", "i": entry[0]})
                return entry[0]
        return None

    def point(self, p):
        """Round UNDO1: a moment Undo can go back to (rewind.py makes the dict)."""
        if self.f is None or not isinstance(p, dict):
            return
        self._write(dict(p, t="point"))

    def cut(self, keep, now):
        """Round UNDO1: a rewind went back to the moment before command `keep`. Keep the start line, the first `keep` commands and
        what belongs to them (their drops, the points before that moment, the tracking lines); the rest is the game that was
        undone - it goes to rewound_last.jsonl, whole, for a bug report. The journal then goes on from command `keep`."""
        self.close()
        lines = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            pass
        try:
            with open(os.path.join(self.folder, REWOUND), "w", encoding="utf-8") as f:
                f.writelines(lines)
        except OSError:
            pass
        kept = []
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            t, i = obj.get("t"), obj.get("i")
            if t == "start":
                kept.append(obj)
            elif t in ("cmd", "drop", "point", "track") and isinstance(i, int) and i < keep:
                kept.append(obj)
            elif t == "point" and isinstance(i, int) and i == keep:
                continue                                   # the moment itself: the table records it again when it sees it
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for obj in kept:
                f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        os.replace(tmp, self.path)
        start = next((o for o in kept if o.get("t") == "start"), None)
        self.reopen(keep, (start or {}).get("at") or now)

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


def read_full(path):
    """Round UNDO1: everything in a journal - {"start", "commands", "end", "drops" (set of command indexes), "fast_from" (the first
    command from which drops were recorded without a gap, or None), "points" (the rewind points, in order)}. A torn line is skipped."""
    out = {"start": None, "commands": [], "end": None, "drops": set(), "fast_from": None, "points": []}
    tracked = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            t = obj.get("t")
            if t == "start":
                out["start"] = obj
            elif t == "cmd":
                out["commands"].append(obj["c"])
            elif t == "end":
                out["end"] = obj
            elif t == "drop" and isinstance(obj.get("i"), int):
                out["drops"].add(obj["i"])
            elif t == "point" and isinstance(obj.get("i"), int):
                out["points"].append(obj)
            elif t == "track" and isinstance(obj.get("i"), int):
                tracked.append(obj["i"])
    # Drops are known from a "track" line on. A journal written by an older program (or a game resumed from one) has commands
    # before its first "track": those are replayed the careful old way. After a later "track" (a resume) the record goes on.
    if tracked:
        out["fast_from"] = min(tracked)
    return out


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
