# SPDX-License-Identifier: GPL-3.0-or-later
"""The RPG's save file (RPG plan decisions 2 and 4: the collection lives in the save; the save remembers the last town).

One file per slot, <rpg_saves>/save_<slot>.json (paths.rpg_saves_dir(): beside saves/ and stats/ in your data folder). Written
safely: the new text goes to save_<slot>.json.tmp, is flushed to the disk, and only then replaces the file; the file it replaces is
first copied to save_<slot>.json.bak, but only when it still reads as a good save (a damaged file never overwrites a good backup).
read() falls back to the .bak when the file itself is damaged, and says so.

The file is versioned ("format": "manticore-rpg-save", "version": 1). A newer version is refused with a plain sentence (this copy
of the game doesn't know its fields); an older one would be brought up to date by MIGRATIONS (none yet). Keys this version doesn't
know are kept and written back unchanged (SaveGame.extra).

Randomness is the save's own: rng(purpose) gives a random.Random seeded from (seed, counter, purpose) by SHA-256 and moves the
counter on, so the same save makes the same pack, reward and deck choice on any computer and any Python, and loading a save never
re-rolls what was already rolled. stable_rng(*parts) doesn't move the counter (a shop's stock is drawn again every time it is
shown and must come out the same).
"""
import hashlib
import json
import os
import random
import shutil
import time

from rpg.collection import Collection

FORMAT = "manticore-rpg-save"
VERSION = 1
MIGRATIONS = {}           # {old version: function(dict) -> dict of the next version}
NAME_MAX = 24
KNOWN = ("format", "version", "slot", "name", "created", "updated", "seed", "rng_counter", "gold", "collection", "decks",
         "active_deck", "start", "world", "shops", "record", "pending_duel", "forge")


class SaveError(Exception):
    """A save that can't be read or written; str() is a sentence for the screen."""


def saves_dir():
    import paths
    return paths.rpg_saves_dir()


def save_path(slot, folder=None):
    return os.path.join(folder or saves_dir(), f"save_{int(slot)}.json")


def check_name(name):
    """The player's name, tidied ("  Karl  " -> "Karl"), or a SaveError saying what's wrong. Only shown, never a file name."""
    if not isinstance(name, str):
        raise SaveError("A name is needed.")
    name = " ".join(name.split())
    if not name:
        raise SaveError("A name is needed.")
    if len(name) > NAME_MAX:
        raise SaveError(f"A name can be at most {NAME_MAX} letters long.")
    if any(ord(c) < 32 or c == "\x7f" for c in name):
        raise SaveError("A name can't hold control characters.")
    return name


def _seed_int(*parts):
    h = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def _need(cond, what):
    if not cond:
        raise SaveError(f"The save file is damaged ({what}).")


def _is_int(v, lo=None):
    return isinstance(v, int) and not isinstance(v, bool) and (lo is None or v >= lo)


def _card_list(v, what):
    _need(isinstance(v, list), what)
    out = []
    for e in v:
        _need(isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and e[0] and _is_int(e[1], 1), what)
        out.append([e[0], e[1]])
    return out


class SaveGame:
    def __init__(self, slot, name, seed, pool=None, basics_free=True):
        self.slot = int(slot)
        self.name = check_name(name)
        self.created = self.updated = time.time()
        self.seed = int(seed)
        self.rng_counter = 0
        self.gold = 0
        self.collection = Collection(pool=pool, basics_free=basics_free)
        self.decks = []                     # [{"name", "main": [[n, k]], "sideboard": [[n, k]]}]
        self.active_deck = None             # index into decks
        self.start = {}                     # {"half_decks": [codes]}
        self.world = {"region": 1, "map": None, "x": None, "y": None, "last_town": None, "towns_visited": []}
        self.shops = {}                     # {town: {"restock": n, "bought": {"slot": n}}}
        self.record = {"duels": 0, "wins": 0, "losses": 0, "draws": 0, "defeated": {}}
        self.pending_duel = None
        self.forge = ""
        self.extra = {}

    # ---- randomness ----------------------------------------------------------------------------------------------------------

    def rng(self, purpose):
        r = random.Random(_seed_int(self.seed, self.rng_counter, purpose))
        self.rng_counter += 1
        return r

    def stable_rng(self, *parts):
        return random.Random(_seed_int(self.seed, "stable", *parts))

    # ---- decks ---------------------------------------------------------------------------------------------------------------

    def deck(self):
        """The deck the player duels with, or None."""
        if self.active_deck is None or not (0 <= self.active_deck < len(self.decks)):
            return None
        return self.decks[self.active_deck]

    # ---- to and from the file ------------------------------------------------------------------------------------------------

    def to_dict(self):
        d = dict(self.extra)
        d.update({"format": FORMAT, "version": VERSION, "slot": self.slot, "name": self.name, "created": self.created,
                  "updated": self.updated, "seed": self.seed, "rng_counter": self.rng_counter, "gold": self.gold,
                  "collection": self.collection.to_dict(), "decks": self.decks, "active_deck": self.active_deck,
                  "start": self.start, "world": self.world, "shops": self.shops, "record": self.record,
                  "pending_duel": self.pending_duel, "forge": self.forge})
        return d

    @classmethod
    def from_dict(cls, d, pool=None, basics_free=True):
        _need(isinstance(d, dict), "not a JSON object")
        _need(d.get("format") == FORMAT, "not a Manticore RPG save")
        v = d.get("version")
        _need(_is_int(v, 1), "no version")
        if v > VERSION:
            raise SaveError(f"This save was made by a newer Manticore (save version {v}; this copy reads up to {VERSION}). "
                            "Update Manticore to load it.")
        while v < VERSION:
            d = MIGRATIONS[v](d)
            v = d["version"]
        _need(_is_int(d.get("slot"), 1), "slot")
        try:
            name = check_name(d.get("name"))
        except SaveError:
            raise SaveError("The save file is damaged (name).")
        _need(_is_int(d.get("seed")), "seed")
        s = cls(d["slot"], name, d["seed"], pool=pool, basics_free=basics_free)
        for k in ("created", "updated"):
            _need(isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool), k)
        s.created, s.updated = float(d["created"]), float(d["updated"])
        _need(_is_int(d.get("rng_counter"), 0), "rng_counter")
        s.rng_counter = d["rng_counter"]
        _need(_is_int(d.get("gold"), 0), "gold")
        s.gold = d["gold"]
        coll = d.get("collection")
        _need(isinstance(coll, dict), "collection")
        for n, k in coll.items():
            _need(isinstance(n, str) and n and _is_int(k, 1), "collection")
            s.collection.add(n, k)
        decks = d.get("decks")
        _need(isinstance(decks, list), "decks")
        for dk in decks:
            _need(isinstance(dk, dict) and isinstance(dk.get("name"), str) and dk["name"].strip(), "decks")
            s.decks.append({"name": dk["name"], "main": _card_list(dk.get("main"), "decks"),
                            "sideboard": _card_list(dk.get("sideboard", []), "decks")})
        ad = d.get("active_deck")
        _need(ad is None or (_is_int(ad, 0) and ad < len(s.decks)), "active_deck")
        s.active_deck = ad
        _need(isinstance(d.get("start"), dict), "start")
        s.start = d["start"]
        w = d.get("world")
        _need(isinstance(w, dict) and _is_int(w.get("region"), 1), "world")
        for k in ("map", "last_town"):
            _need(w.get(k) is None or isinstance(w.get(k), str), "world")
        for k in ("x", "y"):
            _need(w.get(k) is None or _is_int(w.get(k)), "world")
        _need(isinstance(w.get("towns_visited"), list) and all(isinstance(t, str) for t in w["towns_visited"]), "world")
        for k in ("cleared", "beaten", "seen"):    # Round RPG5: encounters won on the map; RPG6: the rooms seen (rpg.explore)
            _need(w.get(k) is None or (isinstance(w[k], list) and all(isinstance(t, str) for t in w[k])), "world")
        s.world = w
        shops = d.get("shops")
        _need(isinstance(shops, dict), "shops")
        for town, st in shops.items():
            _need(isinstance(st, dict) and _is_int(st.get("restock"), 0) and isinstance(st.get("bought"), dict), "shops")
            _need(all(isinstance(k, str) and _is_int(n, 1) for k, n in st["bought"].items()), "shops")
        s.shops = shops
        r = d.get("record")
        _need(isinstance(r, dict) and all(_is_int(r.get(k), 0) for k in ("duels", "wins", "losses", "draws")), "record")
        _need(isinstance(r.get("defeated"), dict) and all(_is_int(n, 0) for n in r["defeated"].values()), "record")
        s.record = r
        pd = d.get("pending_duel")
        _need(pd is None or isinstance(pd, dict), "pending_duel")
        s.pending_duel = pd
        s.forge = d.get("forge") or ""
        s.extra = {k: v for k, v in d.items() if k not in KNOWN}
        return s


def _read_file(path, pool, basics_free):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as e:
        raise SaveError(f"The save file is damaged ({type(e).__name__}).")
    return SaveGame.from_dict(data, pool=pool, basics_free=basics_free)


def write(save, folder=None, now=None):
    """Write the save safely (see the module's docstring). Returns the file's path."""
    path = save_path(save.slot, folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save.updated = time.time() if now is None else now
    text = json.dumps(save.to_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        try:
            _read_file(path, None, save.collection.basics_free)
            shutil.copy2(path, path + ".bak")
        except (SaveError, OSError):
            pass                                    # a damaged file never replaces a good backup
    os.replace(tmp, path)
    return path


def read(slot, folder=None, pool=None, basics_free=True):
    """(SaveGame, note). note is None, or a sentence when the backup had to be used. SaveError when neither can be read;
    FileNotFoundError when the slot is empty."""
    path = save_path(slot, folder)
    try:
        return _read_file(path, pool, basics_free), None
    except FileNotFoundError:
        if os.path.exists(path + ".bak"):
            first = SaveError("The save file is missing.")
        else:
            raise
    except SaveError as e:
        first = e
    if "newer Manticore" in str(first):
        raise first
    try:
        s = _read_file(path + ".bak", pool, basics_free)
    except (FileNotFoundError, SaveError):
        raise first
    when = time.strftime("%d %b %Y %H:%M", time.localtime(s.updated))
    return s, f"{first} The backup from {when} was loaded instead."


def slots(n, folder=None):
    """[{"slot", "used", "name", "gold", "updated", "wins", "problem"}] for slots 1..n - what a load screen shows."""
    out = []
    for slot in range(1, n + 1):
        row = {"slot": slot, "used": False, "name": None, "gold": None, "updated": None, "wins": None, "problem": None}
        try:
            s, note = read(slot, folder)
            row.update(used=True, name=s.name, gold=s.gold, updated=s.updated, wins=s.record["wins"], problem=note)
        except FileNotFoundError:
            pass
        except SaveError as e:
            row.update(used=True, problem=str(e))
        out.append(row)
    return out
