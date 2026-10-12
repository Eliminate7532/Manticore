# SPDX-License-Identifier: GPL-3.0-or-later
"""
last_good.py - Round LAST1: the "last good state" saves.

Karl's call (11 Oct 2026): when an opponent takes minutes, the table must be able to put the game back where it last was fine. The
engine can only write a game safely while its game thread is parked, which is whenever a person has a question open (Main.saveState
answers "busy" otherwise). So the table asks for a save at those moments, a few times a minute at most, and keeps the newest few. At 180 s
of a stall it offers the newest one. No pygame here: forge_table.py does the drawing and the clicking.
"""
import os
import re

SAVE_MIN_GAP = 20.0          # seconds between two save attempts (a save of a big board is not free)
LOST_AFTER = 30.0            # a save the engine has not answered by now is forgotten, and a new one may go out
KEEP = 3                     # the newest saves kept on disk
WATCHDOG_SECONDS = 180.0     # a stall this long on an opponent's turn gets the offer (Karl)
_NAME = re.compile(r"^state_(\d{4})\.state$")


class LastGood:
    def __init__(self, folder, keep=KEEP):
        self.folder = folder
        self.keep = keep
        self.n = 0                       # saves asked for so far; the number in the file name
        self.in_flight = None            # (n, path, signature, time sent)
        self.last_try = None
        self.last_sig = None             # the table's signature at the newest GOOD save
        self.saves = []                  # [{"n", "path", "turn", "at"}] oldest first, only ones the engine confirmed

    def due(self, now, sig):
        """True when a save should go out now: nothing in flight, the table is not what the newest save saw, and the gap has passed."""
        if sig is None:
            return False
        if self.in_flight is not None:
            if now - self.in_flight[3] < LOST_AFTER:
                return False
            self.in_flight = None
        if sig == self.last_sig:
            return False
        return self.last_try is None or now - self.last_try >= SAVE_MIN_GAP

    def next_path(self):
        self.n += 1
        os.makedirs(self.folder, exist_ok=True)
        return self.n, os.path.join(self.folder, "state_%04d.state" % self.n)

    def sent(self, now, n, path, sig):
        self.in_flight = (n, path, sig, now)
        self.last_try = now

    def reply(self, msg, now):
        """The engine's {"t":"state_saved"} answer. Returns True when it is a good save we now hold."""
        fl = self.in_flight
        if fl is None or msg.get("n") != fl[0]:
            return False                                    # an answer to a save we already gave up on
        self.in_flight = None
        if not msg.get("ok") or not os.path.isfile(fl[1]):
            self.last_try = now                             # busy / failed: try again after the gap
            return False
        self.last_sig = fl[2]
        self.saves.append({"n": fl[0], "path": fl[1], "turn": msg.get("turn"), "at": now})
        self.prune()
        return True

    def prune(self):
        while len(self.saves) > self.keep:
            old = self.saves.pop(0)
            try:
                os.remove(old["path"])
            except OSError:
                pass

    def latest(self, players=None):
        """The newest save whose file is still there, or None. Round LAST1b: with `players`, only a save that holds that many
        players (or whose file names none - players_in() can't tell)."""
        for s in reversed(self.saves):
            if os.path.isfile(s["path"]) and (players is None or players_in(s["path"]) in (None, players)):
                return s
        return None


_PLAYER_LINE = re.compile(r"^p(\d+)[a-z]+=")


def players_in(path):
    """Round LAST1b: how many players a saved game holds, or None when its file names none (or can't be read). Forge's GameState
    writes p0..pN lines ("p0life=33", "p0hand=...") only for the players still in the game: in a 4-player game where two are out,
    a save holds 2, and a new 4-player game refuses it ("Non-matching number of players, (4 vs. 2)") - live, 12 Oct 2026."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            ids = {m.group(1) for m in (_PLAYER_LINE.match(line) for line in f) if m}
    except OSError:
        return None
    return len(ids) or None


def clean_old(root, days=3):
    """Remove save folders under root not touched for `days` days (the saves of old games). Best effort; never raises."""
    import shutil
    import time
    try:
        cutoff = time.time() - days * 86400
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
