# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's progression, part 4: GEAR AND RELICS (Round PROG4; Karl, 2 Nov 2026: gear from "Everywhere" - common pieces in
town shops for gold, better ones in chests, from bosses and quests; the design is the Project doc
claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.4, under its section 5's rules). No pygame: rpg_screens.py draws; every rule it
shows is here, once.

    read(path)                  rpg/data/gear.json, checked (a bad key is a ValueError naming it)
    state(save)                 save.gear = {"owned": [piece id, ...] (in the order found), "worn": {slot id: piece id}}
    give(save, pid)             a found / bought piece into the save (a piece is owned once: again -> None)
    open_slots(save)            the slots the player can wear something in now (the Pack slot only with the Pack Mule perk)
    wear(save, pid, slot) / take_off(save, slot)
    effects(save)               THE worn gear's effects, each counted once: life, hand, battlefield, command (start effects, through
                                Round PROG3's per-seat start effects), reward_choices, gold_mult, walk_speed
    shop_offers(save, region)   what a town's Gear tab sells: the region's common pieces not owned yet (cheapest first)
    buy(save, pid, region)      one of those, for its price (GearError: not sold here, owned already, not enough gold)
    drop_for_*(...)             the piece a boss, a rare foe, a quest or a chest gives (None: none, or owned already)

The save keeps save.gear; a save from before this round has none: nothing owned, nothing worn (rpg.save). Gear is never lost - a lost
duel takes gold only (rpg.duel), never a piece.

Readings of mine (Karl's to change, each in one place - gear.json or here):
  - A piece is owned once. One found again (a boss beaten a second time, a rare foe again) gives nothing more and says nothing.
  - A rare foe drops its piece the first time it is beaten (the brief: "rare foes may drop one (say which)" - the encounter screen
    says "Carries: <piece>" while you don't own it).
  - Several pieces' effects add up: life and hand are summed, gold_mult and walk_speed multiplied, cards listed in order.
  - The Pack slot (Pack Mule, a perk that was "not offered" until gear existed) holds any piece: the sixth slot of the handover.
  - A duel's start can't go below 1 life or 0 cards in hand (start_effects' ranges), whatever trade-offs are worn.
"""
import json
import math

from rpg import data_path

GEAR_FILE = data_path("gear.json")
RARITIES = ("common", "uncommon", "rare")
EFFECTS = ("life", "hand", "battlefield", "command", "reward_choices", "gold_mult", "walk_speed")
SHOP_RARITIES = ("common",)                 # what a town's Gear tab sells (Karl's answer 4: "common gear in town shops")

# The words the player sees (Karl to edit)
GEAR_TITLE = "Gear"
FOUND_LINE = "New gear: {name} ({slot}). Wear it from the Menu: Gear."
WORN_LINE = "Worn: {names}."
CARRIES_LINE = "Carries: {name} (gear)."
BOUGHT_LINE = "Bought {name} for {price} gold. Wear it from the Menu: Gear."
WORE_LINE = "{name} worn ({slot})."
OFF_LINE = "{name} taken off."
NOTHING_WORN = "Nothing worn."


class GearError(ValueError):
    """Something the player can't do with gear; str() is a sentence for the screen."""


def _whole(v, where, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{where}: a whole number from {lo} to {hi} is needed, not {v!r}")
    return v


def _factor(v, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0.25 <= v <= 4:
        raise ValueError(f"{where}: a factor from 0.25 to 4 is needed, not {v!r}")
    return float(v)


class Slot:
    __slots__ = ("id", "name", "any", "perk")

    def __init__(self, d, i):
        where = f"gear.json slots[{i}]"
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or not d["id"] or not isinstance(d.get("name"), str):
            raise ValueError(f"{where}: an id and a name are needed")
        bad = [k for k in d if k not in ("id", "name", "any", "perk")]
        if bad:
            raise ValueError(f"{where}: {bad[0]!r} isn't a slot key")
        self.id, self.name = d["id"], d["name"]
        self.any = bool(d.get("any", False))
        self.perk = d.get("perk") or None

    def __repr__(self):
        return f"Slot({self.id!r})"


class Piece:
    __slots__ = ("id", "name", "slot", "rarity", "region", "price", "icon", "text", "effect")

    def __init__(self, pid, d, slots):
        where = f"gear.json pieces.{pid}"
        if not isinstance(d, dict):
            raise ValueError(f"{where}: a table is needed")
        bad = [k for k in d if k not in ("name", "slot", "rarity", "region", "price", "icon", "text", "effect")]
        if bad:
            raise ValueError(f"{where}: {bad[0]!r} isn't a piece key")
        self.id = pid
        for k in ("name", "text", "icon"):
            if not isinstance(d.get(k), str) or not d[k].strip():
                raise ValueError(f"{where}: a {k} is needed")
        self.name, self.text, self.icon = d["name"], d["text"], d["icon"]
        if d.get("slot") not in slots or slots[d["slot"]].any:
            raise ValueError(f"{where}.slot: {d.get('slot')!r} isn't a slot a piece belongs to")
        self.slot = d["slot"]
        if d.get("rarity") not in RARITIES:
            raise ValueError(f"{where}.rarity: one of {', '.join(RARITIES)} is needed")
        self.rarity = d["rarity"]
        self.region = _whole(d.get("region", 1), where + ".region", 1, 99)
        self.price = _whole(d.get("price"), where + ".price", 1, 100000)
        eff = d.get("effect")
        if not isinstance(eff, dict) or not eff:
            raise ValueError(f"{where}.effect: a table with at least one effect is needed")
        bad = [k for k in eff if k not in EFFECTS]
        if bad:
            raise ValueError(f"{where}.effect: {bad[0]!r} isn't one of {', '.join(EFFECTS)}")
        out = {}
        for k in ("life", "hand"):
            if k in eff:
                out[k] = _whole(eff[k], f"{where}.effect.{k}", -20, 20)
        if "reward_choices" in eff:
            out["reward_choices"] = _whole(eff["reward_choices"], f"{where}.effect.reward_choices", 1, 5)
        for k in ("gold_mult", "walk_speed"):
            if k in eff:
                out[k] = _factor(eff[k], f"{where}.effect.{k}")
        for k in ("battlefield", "command"):
            if k in eff:
                v = eff[k]
                if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() and ";" not in x for x in v):
                    raise ValueError(f"{where}.effect.{k}: a list of card names is needed")
                out[k] = [x.strip() for x in v]
        self.effect = out

    def __repr__(self):
        return f"Piece({self.id!r})"


class Gear:
    def __init__(self, d):
        if not isinstance(d, dict):
            raise ValueError("gear.json: a table is needed")
        sl = d.get("slots")
        if not isinstance(sl, list) or not sl:
            raise ValueError("gear.json: slots (a list) is needed")
        self.slots = {}
        for i, s in enumerate(sl):
            slot = Slot(s, i)
            if slot.id in self.slots:
                raise ValueError(f"gear.json slots: {slot.id!r} twice")
            self.slots[slot.id] = slot
        pc = d.get("pieces")
        if not isinstance(pc, dict) or not pc:
            raise ValueError("gear.json: pieces (a table) is needed")
        self.pieces = {pid: Piece(pid, v, self.slots) for pid, v in pc.items() if not str(pid).startswith("_")}
        src = d.get("sources") or {}
        if not isinstance(src, dict):
            raise ValueError("gear.json sources: a table is needed")
        bad = [k for k in src if k not in ("bosses", "rares", "quests")]
        if bad:
            raise ValueError(f"gear.json sources: {bad[0]!r} isn't one of bosses, rares, quests")
        self.sources = {}
        for k in ("bosses", "rares", "quests"):
            v = src.get(k) or {}
            if not isinstance(v, dict):
                raise ValueError(f"gear.json sources.{k}: a table is needed")
            for who, pid in v.items():
                if pid not in self.pieces:
                    raise ValueError(f"gear.json sources.{k}.{who}: no piece {pid!r}")
            self.sources[k] = dict(v)

    def piece(self, pid):
        return self.pieces.get(pid)

    def slot_name(self, sid):
        s = self.slots.get(sid)
        return s.name if s else sid


_READ = {}


def read(path=GEAR_FILE):
    if path not in _READ:
        with open(path, encoding="utf-8") as f:
            _READ[path] = Gear(json.load(f))
    return _READ[path]


# ---- the save's part --------------------------------------------------------------------------------------------------------

def blank():
    return {"owned": [], "worn": {}}


def state(save):
    """The save's gear table (made when missing - a save from before this round)."""
    g = getattr(save, "gear", None)
    if not isinstance(g, dict):
        g = blank()
        save.gear = g
    if not isinstance(g.get("owned"), list):
        g["owned"] = []
    if not isinstance(g.get("worn"), dict):
        g["worn"] = {}
    return g


def owns(save, pid):
    return pid in state(save)["owned"]


def owned(save, gr=None):
    """The pieces owned, in the order found (an id this copy doesn't know is skipped)."""
    gr = gr or read()
    return [gr.pieces[p] for p in state(save)["owned"] if p in gr.pieces]


def give(save, pid, gr=None):
    """Piece `pid` into the save. Returns the Piece, or None when it is owned already (or unknown: said once in crash_log)."""
    gr = gr or read()
    p = gr.piece(pid)
    if p is None:
        from rpg import notes
        notes.once(f"gear:unknown:{pid}"[:200], f"gear: no piece {pid!r} in gear.json - nothing given")
        return None
    st = state(save)
    if pid in st["owned"]:
        return None
    st["owned"].append(pid)
    return p


def found_line(piece, gr=None):
    gr = gr or read()
    return FOUND_LINE.format(name=piece.name, slot=gr.slot_name(piece.slot))


def open_slots(save, gr=None):
    """The slots the player can wear something in now, in gear.json's order: a slot with a perk only once that perk is taken."""
    gr = gr or read()
    try:
        from rpg import levels
        taken = set(levels.state(save)["perks"])
    except Exception:
        taken = set()
    return [s for s in gr.slots.values() if not s.perk or s.perk in taken]


def worn(save, gr=None):
    """[(Slot, Piece)] for every open slot with a piece in it, in gear.json's slot order. A piece in a slot that isn't open (the perk
    went - never in play) or an id this copy doesn't know is left out."""
    gr = gr or read()
    st = state(save)
    out = []
    for s in open_slots(save, gr):
        p = gr.piece(st["worn"].get(s.id))
        if p is not None and p.id in st["owned"]:
            out.append((s, p))
    return out


def worn_in(save, pid, gr=None):
    """The slot id piece `pid` is worn in, or None."""
    for s, p in worn(save, gr):
        if p.id == pid:
            return s.id
    return None


def can_wear_in(piece, slot):
    return slot.any or slot.id == piece.slot


def wear(save, pid, slot=None, gr=None):
    """Wear owned piece `pid` in `slot` (default: its own slot). Whatever was in that slot comes off; a piece already worn in another
    slot moves (a piece is worn once). GearError for a piece not owned or a slot it can't go in. Returns the Slot."""
    gr = gr or read()
    p = gr.piece(pid)
    if p is None or not owns(save, pid):
        raise GearError("You don't have that piece.")
    sid = slot or p.slot
    s = next((x for x in open_slots(save, gr) if x.id == sid), None)
    if s is None or not can_wear_in(p, s):
        raise GearError(f"{p.name} can't be worn there.")
    st = state(save)
    for k, v in list(st["worn"].items()):
        if v == pid:
            del st["worn"][k]
    st["worn"][s.id] = pid
    return s


def take_off(save, slot_id):
    """Take off what is worn in `slot_id`; returns its piece id, or None when the slot was empty."""
    return state(save)["worn"].pop(slot_id, None)


def effects(save, gr=None):
    """THE worn gear's effects, each worn piece counted once: {"life": sum, "hand": sum, "battlefield": [...], "command": [...],
    "reward_choices": sum, "gold_mult": product, "walk_speed": product} (only the keys some worn piece has)."""
    gr = gr or read()
    out = {}
    for _s, p in worn(save, gr):
        e = p.effect
        for k in ("life", "hand", "reward_choices"):
            if k in e:
                out[k] = out.get(k, 0) + e[k]
        for k in ("gold_mult", "walk_speed"):
            if k in e:
                out[k] = out.get(k, 1.0) * e[k]
        for k in ("battlefield", "command"):
            if k in e:
                out[k] = out.get(k, []) + list(e[k])
    return out


def walk_speed(save, gr=None):
    """The factor on the walker's speed (1.0: as before). Never stops the walk: an unreadable gear.json gives 1.0."""
    try:
        return float(effects(save, gr).get("walk_speed", 1.0))
    except (OSError, ValueError):
        return 1.0


def worn_line(save, gr=None):
    """"Worn: Copper Charm, Road Boots." or "" with nothing worn."""
    names = [p.name for _s, p in worn(save, gr)]
    return WORN_LINE.format(names=", ".join(names)) if names else ""


# ---- where gear comes from --------------------------------------------------------------------------------------------------

def shop_offers(save, region=1, gr=None):
    """A town's Gear tab: the common pieces of this region or an earlier one that the player doesn't own yet, cheapest first."""
    gr = gr or read()
    return sorted((p for p in gr.pieces.values() if p.rarity in SHOP_RARITIES and p.region <= int(region or 1)
                   and not owns(save, p.id)), key=lambda p: (p.price, p.name))


def buy(save, pid, region=1, gr=None):
    """Buy piece `pid` in a town's Gear tab. GearError when it isn't sold here, is owned already or costs more than you carry."""
    gr = gr or read()
    p = gr.piece(pid)
    if p is None or p.rarity not in SHOP_RARITIES or p.region > int(region or 1):
        raise GearError("That piece isn't sold here.")
    if owns(save, pid):
        raise GearError(f"You already have {p.name}.")
    if save.gold < p.price:
        raise GearError(f"{p.name} costs {p.price} gold; you have {save.gold}.")
    save.gold -= p.price
    give(save, pid, gr)
    return p


def source_piece(kind, who, gr=None):
    """The piece gear.json's sources give for (kind "bosses" / "rares" / "quests", who), or None."""
    gr = gr or read()
    return gr.piece((gr.sources.get(kind) or {}).get(who))


def drop_for(save, kind, who, gr=None):
    """Give the piece of (kind, who) when there is one and it isn't owned yet. Returns the lines that say so ([] for nothing)."""
    try:
        gr = gr or read()
        p = source_piece(kind, who, gr)
    except (OSError, ValueError) as e:
        from rpg import notes
        notes.once("gear:read", f"gear.json couldn't be read, so no gear was given: {e}")
        return []
    if p is None or give(save, p.id, gr) is None:
        return []
    return [found_line(p, gr)]


def carries(save, kind, who, gr=None):
    """The encounter screen's "Carries: <piece> (gear)." for a foe whose piece isn't owned yet, else ""."""
    try:
        gr = gr or read()
        p = source_piece(kind, who, gr)
    except (OSError, ValueError):
        return ""
    return CARRIES_LINE.format(name=p.name) if p is not None and not owns(save, p.id) else ""
