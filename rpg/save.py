# SPDX-License-Identifier: GPL-3.0-or-later
"""The RPG's save file (RPG plan decisions 2 and 4: the collection lives in the save; the save remembers the last town).

One file per slot, <rpg_saves>/save_<slot>.json (paths.rpg_saves_dir(): beside saves/ and stats/ in your data folder). Written
safely: the new text goes to save_<slot>.json.tmp, is flushed to the disk, and only then replaces the file; the file it replaces is
first copied to save_<slot>.json.bak, but only when it still reads as a good save (a damaged file never overwrites a good backup).
read() falls back to the .bak when the file itself is damaged, and says so.

The file is versioned ("format": "manticore-rpg-save", "version": 1). A newer version is refused with a plain sentence (this copy
of the game doesn't know its fields); an older one would be brought up to date by MIGRATIONS (none yet). Keys this version doesn't
know are kept and written back unchanged (SaveGame.extra).

Round RPG7: "basics_free" says which rule the save was made under (basic lands free and uncounted, or bought and counted - Karl,
13 Oct: bought, 1 gold each). It is save version 2: a version 1 save (made while basic lands were free) is brought up to date with
basics_free true, and an older copy of the game refuses a version 2 save instead of reading it under the wrong rule and dropping
its basic lands. A save is always read under its own rule; the basics_free a caller passes is only for a new SaveGame.

Round PROG1: two new keys, each with a default, so a save from before loads as it was and the version stays 2 (an older copy of
the game keeps them as unknown keys and writes them back): "found" - the Card Book's log, {normal name: {"name", "day", "map",
"town"}} for every card the moment it first enters the collection (rpg.progress.note_found; a save from before has none, and its
cards show as found before the Card Book) - and "bestiary": {foe kind: {"cards": {name: copies}}}, the cards won from each kind.
A damaged value of either is mended to empty (said once in crash_log.txt), never a refused save.

Round PROG2: one more key, the same way (default None, version still 2): "pending_pick" - a won duel's reward cards still to choose
(rpg.rewards: {"groups": [[name, ...], ...], "kind", "enemy"}), so a game closed on the choosing screen offers the same cards again.

Round PROG3: one more key, the same way (version still 2): "progress" - XP, levels and perks (rpg.levels: {"xp": n, "perks": [perk
id, ...], "offers": [[perk id, ...], ...]}). A save from before has none: level 1, 0 XP, no perks. A damaged value is mended (said
once): what can be read is kept.

Round PROG4: one more key, the same way (version still 2): "gear" - the gear owned and worn (rpg.gear: {"owned": [piece id, ...],
"worn": {slot id: piece id}}). A save from before has none: nothing owned, nothing worn. A damaged value is mended (said once): what
can be read is kept; a worn piece that isn't owned comes off.

Round SPAR1: one more key, the same way (version still 2): "sparring" - the sparring yard's results (rpg.spar: [{"deck", "cards",
"day", "duels", "wins", ...}], newest last). A save from before has none: never sparred. A record that isn't one is dropped (said once).

Randomness is the save's own: rng(purpose) gives a random.Random seeded from (seed, counter, purpose) by SHA-256 and moves the
counter on, so the same save makes the same pack, reward and deck choice on any computer and any Python, and loading a save never
re-rolls what was already rolled. stable_rng(*parts) doesn't move the counter (a shop's stock is drawn again every time it is
shown and must come out the same).
"""
import hashlib
import json
import math
import os
import random
import shutil
import time

from rpg import notes
from rpg.collection import Collection

FORMAT = "manticore-rpg-save"
VERSION = 2               # Round RPG7: "basics_free" (version 1 had none: basic lands were free)


def _v1_to_v2(d):
    """Version 1 was made while basic lands were free and uncounted: say so, so the save keeps that rule."""
    d = dict(d)
    d["basics_free"] = True
    d["version"] = 2
    return d


MIGRATIONS = {1: _v1_to_v2}   # {old version: function(dict) -> dict of the next version}
NAME_MAX = 24
HEROES = ("Warrior", "Paladin", "Rogue", "Mage", "Engineer")   # Round RPG11: who you walk the map as (DawnLike's
                                                                   # five class sheets); an older save has none: the first
KNOWN = ("format", "version", "slot", "name", "created", "updated", "seed", "rng_counter", "gold", "collection", "decks",
         "active_deck", "start", "world", "shops", "record", "pending_duel", "forge", "basics_free", "found", "bestiary",
         "pending_pick", "progress", "gear", "sparring")


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


def _finite(v):
    try:
        return math.isfinite(v)
    except OverflowError:                        # a whole number past what a float can hold
        return False


def _repaired(slot, what, text):
    """Round RPG16d: a value an older copy of the game could write, mended as the save is read - said once a run in crash_log.txt
    (the next write keeps the mended value)."""
    notes.once(f"save:{slot}:{what}", f"save slot {slot}: {text}")


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
        self.shops = {}                     # {town: {"restock": n, "bought": {"slot": n}, "themes": [code]}} (themes: Round PACK1)
        self.record = {"duels": 0, "wins": 0, "losses": 0, "draws": 0, "defeated": {}}
        self.pending_duel = None
        self.forge = ""
        self.extra = {}
        self.found = {}                     # Round PROG1: the Card Book's log (rpg.progress)
        self.bestiary = {}                  # Round PROG1: {kind: {"cards": {name: copies}}}
        self.pending_pick = None            # Round PROG2: a won duel's reward cards still to choose (rpg.rewards), else None
        self.progress = {"xp": 0, "perks": [], "offers": []}   # Round PROG3: XP, levels and perks (rpg.levels)
        self.gear = {"owned": [], "worn": {}}                   # Round PROG4: gear owned and worn (rpg.gear)
        self.sparring = []                  # Round SPAR1: the sparring yard's results, newest last (rpg.spar)
        self.collection.on_add = self._noted

    def _noted(self, name):
        """Round PROG1: a card entered the collection - the Card Book writes down when and where, the first time (rpg.progress)."""
        from rpg import progress
        progress.note_found(self, name)

    # ---- randomness ----------------------------------------------------------------------------------------------------------

    def rng(self, purpose):
        r = random.Random(_seed_int(self.seed, self.rng_counter, purpose))
        self.rng_counter += 1
        return r

    def peek_rng(self, purpose):
        """Round RPG13: the generator the next rng(purpose) call will give, without using it up (the encounter screen's "See its
        deck" shows the deck the duel will then play)."""
        return random.Random(_seed_int(self.seed, self.rng_counter, purpose))

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
                  "pending_duel": self.pending_duel, "forge": self.forge, "basics_free": self.collection.basics_free,
                  "found": self.found, "bestiary": self.bestiary, "pending_pick": self.pending_pick,
                  "progress": self.progress, "gear": self.gear, "sparring": self.sparring})
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
        _need(isinstance(d.get("basics_free"), bool), "basics_free")       # Round RPG7: the save's own rule
        s = cls(d["slot"], name, d["seed"], pool=pool, basics_free=d["basics_free"])
        for k in ("created", "updated"):
            _need(isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool), k)
        s.created, s.updated = float(d["created"]), float(d["updated"])
        _need(_is_int(d.get("rng_counter"), 0), "rng_counter")
        s.rng_counter = d["rng_counter"]
        gold = d.get("gold")
        if _is_int(gold) and gold < 0:              # Round RPG16d (review T3): RPG16c gave an event's toll whatever its sign
            _repaired(d["slot"], "gold", f"gold was {gold}; it is 0 now (an event took more than there was)")
            gold = 0
        _need(_is_int(gold, 0), "gold")
        s.gold = gold
        coll = d.get("collection")
        _need(isinstance(coll, dict), "collection")
        s.collection.on_add = None                  # Round PROG1: reading the save finds nothing new
        for n, k in coll.items():
            _need(isinstance(n, str) and n and _is_int(k, 1), "collection")
            s.collection.add(n, k)
        s.collection.on_add = s._noted
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
        _need(w.get("facing") in (None, "down", "left", "right", "up"), "world")     # Round RPG11: which way the walker faces
        _need(w.get("hero") in (None,) + HEROES, "world")                             # Round RPG11: the hero picked
        dg = w.get("danger")                         # Round RPG13: steps to the next random fight (rpg.encounters)
        if isinstance(dg, (int, float)) and not isinstance(dg, bool) and not _finite(dg):    # Round RPG16d (review T10): json's
            # Infinity / NaN, or a whole number no float can hold (the walk subtracts a float from it) - a new gap
            _repaired(d["slot"], "danger", f"the steps to the next random fight were {dg!r}; a new number is drawn")
            w.pop("danger")
            dg = None
        _need(dg is None or (isinstance(dg, (int, float)) and not isinstance(dg, bool) and dg >= 0), "world")
        fl = w.get("flags")                          # Round RPG14: the story's flags (rpg.talk); Round RPG16d (review T2):
        if fl is not None:                           # ... only words - RPG16c let an event's list in, and set() of it raised
            good = [f for f in fl if isinstance(f, str) and f] if isinstance(fl, list) else []
            if not isinstance(fl, list) or len(good) != len(fl):
                bad = [f for f in fl if not (isinstance(f, str) and f)] if isinstance(fl, list) else fl
                _repaired(d["slot"], "flags", f"flags that aren't words were dropped: {bad!r}"[:300])
                w["flags"] = good
        hg = w.get("held_gold")                      # Round GOLD1: {spot: gold} the foes hold since they beat you - absent in a
        if hg is not None:                           # ... save from before (nothing held); a damaged entry is dropped, said once
            good = {k: n for k, n in hg.items() if isinstance(k, str) and k and _is_int(n, 1)} if isinstance(hg, dict) else {}
            if not isinstance(hg, dict) or len(good) != len(hg):
                _repaired(d["slot"], "held_gold", "gold a foe held that isn't a whole number above 0 was dropped")
            if good:
                w["held_gold"] = good
            else:
                w.pop("held_gold")
        s.world = w
        shops = d.get("shops")
        _need(isinstance(shops, dict), "shops")
        for town, st in shops.items():
            _need(isinstance(st, dict) and _is_int(st.get("restock"), 0) and isinstance(st.get("bought"), dict), "shops")
            _need(all(isinstance(k, str) and _is_int(n, 1) for k, n in st["bought"].items()), "shops")
            th = st.get("themes")                    # Round PACK1: the theme packs bought this restock - absent in a save from
            if th is not None:                       # ... before (none bought); a damaged list is mended, said once
                good = [c for c in th if isinstance(c, str) and c] if isinstance(th, list) else []
                if not isinstance(th, list) or len(good) != len(th):
                    _repaired(d["slot"], "shops", f"theme packs bought in {town} that aren't names were dropped")
                    st["themes"] = good
        s.shops = shops
        r = d.get("record")
        _need(isinstance(r, dict) and all(_is_int(r.get(k), 0) for k in ("duels", "wins", "losses", "draws")), "record")
        _need(isinstance(r.get("defeated"), dict) and all(_is_int(n, 0) for n in r["defeated"].values()), "record")
        s.record = r
        pd = d.get("pending_duel")
        _need(pd is None or isinstance(pd, dict), "pending_duel")
        s.pending_duel = pd
        s.forge = d.get("forge") or ""
        s.found = _found_log(d, d["slot"])          # Round PROG1: absent in a save from before - {} (found before the Card Book)
        s.bestiary = _bestiary(d, d["slot"])
        s.pending_pick = _pending_pick(d, d["slot"])  # Round PROG2: absent in a save from before - None (nothing to choose)
        s.progress = _progress(d, d["slot"])          # Round PROG3: absent in a save from before - level 1, 0 XP, no perks
        s.gear = _gear(d, d["slot"])                  # Round PROG4: absent in a save from before - nothing owned or worn
        s.sparring = _sparring(d, d["slot"])          # Round SPAR1: absent in a save from before - never sparred
        s.extra = {k: v for k, v in d.items() if k not in KNOWN}
        return s


def _progress(d, slot):
    """Round PROG3: XP and perks read leniently - a damaged part is mended (said once), never a refused save."""
    v = d.get("progress")
    out = {"xp": 0, "perks": [], "offers": []}
    if v is None:
        return out
    bad = not isinstance(v, dict)
    v = v if isinstance(v, dict) else {}
    xp = v.get("xp", 0)
    if isinstance(xp, int) and not isinstance(xp, bool) and xp >= 0:
        out["xp"] = xp
    else:
        bad = True
    perks = v.get("perks", [])
    if isinstance(perks, list) and all(isinstance(x, str) and x for x in perks):
        out["perks"] = list(perks)
    else:
        bad = True
    offers = v.get("offers", [])
    if isinstance(offers, list) and all(isinstance(g, list) and g and all(isinstance(x, str) and x for x in g) for g in offers):
        out["offers"] = [list(g) for g in offers]
    else:
        bad = True
    if bad:
        _repaired(slot, "progress", "the XP and perks were damaged; what could be read is kept")
    return out


def _sparring(d, slot):
    """Round SPAR1: the sparring yard's records read leniently - a record that isn't one is dropped (said once), never a refused
    save. A record: {"deck": name, "cards": fingerprint, "day", "duels", "wins", ...} with 0 <= wins <= duels, duels >= 1."""
    v = d.get("sparring")
    if v is None:
        return []
    good = []
    for r in v if isinstance(v, list) else []:
        if (isinstance(r, dict) and isinstance(r.get("deck"), str) and _is_int(r.get("day"), 1) and _is_int(r.get("duels"), 1)
                and _is_int(r.get("wins"), 0) and r["wins"] <= r["duels"]):
            good.append(r)
    if not isinstance(v, list) or len(good) != len(v):
        _repaired(slot, "sparring", "sparring records that aren't records were dropped")
    return good


def _gear(d, slot):
    """Round PROG4: the gear read leniently - a damaged part is mended (said once), never a refused save. An id this copy doesn't
    know is kept (a newer gear.json may have it; rpg.gear skips it); a worn piece that isn't owned comes off."""
    v = d.get("gear")
    out = {"owned": [], "worn": {}}
    if v is None:
        return out
    bad = not isinstance(v, dict)
    v = v if isinstance(v, dict) else {}
    owned = v.get("owned", [])
    if isinstance(owned, list):
        for x in owned:
            if isinstance(x, str) and x and x not in out["owned"]:
                out["owned"].append(x)
            else:
                bad = True
    else:
        bad = True
    worn = v.get("worn", {})
    if isinstance(worn, dict):
        for k, x in worn.items():
            if isinstance(k, str) and k and isinstance(x, str) and x in out["owned"] and x not in out["worn"].values():
                out["worn"][k] = x
            else:
                bad = True
    else:
        bad = True
    if bad:
        _repaired(slot, "gear", "the gear owned and worn was damaged; what could be read is kept")
    return out


def _pending_pick(d, slot):
    """Round PROG2: the reward still to choose, read leniently - {"groups": [[name, ...], ...], "kind", "enemy", ...} or None. A
    damaged value is dropped (said once): the cards are lost, the save loads."""
    v = d.get("pending_pick")
    if v is None:
        return None
    groups = v.get("groups") if isinstance(v, dict) else None
    if (isinstance(groups, list) and groups and all(isinstance(g, list) and g and all(isinstance(n, str) and n for n in g)
                                                    for g in groups)):
        return v
    _repaired(slot, "pending_pick", "the reward cards still to choose were damaged and are dropped")
    return None


def _found_log(d, slot):
    """Round PROG1: the "found" key read leniently - a damaged entry is dropped (said once), never a refused save."""
    v = d.get("found")
    if v is None:
        return {}
    if not isinstance(v, dict):
        _repaired(slot, "found", f"the Card Book's log was {type(v).__name__}, not a table; it starts again empty")
        return {}
    out, bad = {}, 0
    for k, e in v.items():
        if (isinstance(k, str) and k and isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"]
                and (e.get("day") is None or _is_int(e.get("day"), 1))
                and all(e.get(x) is None or isinstance(e.get(x), str) for x in ("map", "town"))):
            out[k] = {x: e.get(x) for x in ("name", "day", "map", "town")}
        else:
            bad += 1
    if bad:
        _repaired(slot, "found", f"{bad} damaged line(s) of the Card Book's log were dropped")
    return out


def _bestiary(d, slot):
    v = d.get("bestiary")
    if v is None:
        return {}
    out, bad = {}, 0
    if isinstance(v, dict):
        for kind, e in v.items():
            cards = e.get("cards") if isinstance(e, dict) else None
            if isinstance(kind, str) and kind and isinstance(cards, dict) and all(
                    isinstance(n, str) and n and _is_int(c, 1) for n, c in cards.items()):
                out[kind] = {"cards": dict(cards)}
            else:
                bad += 1
    else:
        bad = 1
    if bad:
        _repaired(slot, "bestiary", "damaged entries of the bestiary were dropped")
    return out


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
