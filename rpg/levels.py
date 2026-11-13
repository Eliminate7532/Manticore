# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's progression, part 3: XP, levels and perks (Round PROG3; Karl, 2 Nov 2026: "My main idea is to add ways to make
progression more tangible and 'fun' JRPG style"; the design is the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md, section 3.3,
and Karl's answers there - 1: levels may change the duel itself ("Duel too, freely"), 3: a small bonus every level plus a perk choice
every few levels). No pygame: rpg_results.py and rpg_screens.py draw; every rule they show is here, once.

    read(path)                    rpg/data/progression.json's "xp", "levels" and "perks", checked (a bad key is a ValueError naming it)
    xp_for(result, kind)          THE XP rule: how much a duel's result (or "quest") gives
    level_of(xp) / xp_to_reach    THE curve: level 2 at levels.first XP, each next level levels.step more than the last; cap levels.cap
    bar(xp)                       (level, XP into the level, XP the level needs; None at the cap) - the bar on the results screen
    gain(save, amount)            XP into the save; on reaching a perk level (every levels.perk_every), a choice of perk_choices
                                  perks is dealt into the save's perk offers. Returns what to show (before / after / levels / offers)
    offers(save) / choose(...)    the perk choices still waiting, and taking one
    duel_effects(save)            THE start effects of the player's seat in an Adventure duel (start_effects.py: life, hand, cards):
                                  base life + life_per_level a level + the perks'
    reward_choices(save, base)    pick 1 of (base + Keen Eye) reward cards
    win_gold(save, gold)          a win's gold with the perks
    loss_cost(save, numbers, kind)  what a loss of that kind of fight takes with the perks (Round GOLD1: by kind)

The save keeps save.progress = {"xp": int, "perks": [perk id, ...] (in the order taken), "offers": [[perk id, ...], ...] (choices
still waiting, oldest first)}; a save from before this round has none: level 1, 0 XP, no perks (rpg.save).

Readings of mine (Karl's to change, each in one place - progression.json or here):
  - A draw gives 0 XP (the handover lists a loss's 2 but not a draw's).
  - A rare foe's win gives "rare" XP, whatever its encounter kind; a boss's "boss"; a dungeon's "dungeon"; anything else "regular".
  - A conceded duel counts as a loss (2 XP), as it does for gold.
  - XP keeps counting past the cap (level 20), but nothing more comes of it; the bar shows "MAX".
  - A perk the game can't do yet is in the list with "available": false and is never offered (the round's doc names them).
  - Levels never pass the cap in one gain: crossing several levels at once deals one perk choice per perk level crossed.
"""
import json
import math

from rpg import data_path

PROGRESSION_FILE = data_path("progression.json")
PLAIN_LIFE = 20                         # the Adventure's duel without levels (rpg.duel.LIFE, Forge's Constructed)
XP_KINDS = ("regular", "dungeon", "rare", "boss", "quest", "loss", "draw")
PERK_EFFECTS = ("reward_choices", "gold_bonus", "loss_cut", "life", "hand", "battlefield",   # GOLD1: loss_cut, not loss_share
                "gear_slot")      # Round PROG4: Pack Mule - the Pack slot opens (rpg.gear: the slot names the perk)


class Perk:
    __slots__ = ("id", "name", "text", "stackable", "available", "why_not", "effect")

    def __init__(self, pid, d):
        where = f"progression.json perks.{pid}"
        if not isinstance(d, dict):
            raise ValueError(f"{where}: a table is needed")
        bad = [k for k in d if k not in ("name", "text", "stackable", "available", "why_not", "effect")]
        if bad:
            raise ValueError(f"{where}: {bad[0]!r} isn't a perk key")
        self.id = pid
        self.name, self.text = d.get("name"), d.get("text")
        if not isinstance(self.name, str) or not self.name or not isinstance(self.text, str) or not self.text:
            raise ValueError(f"{where}: a name and a text are needed")
        self.stackable = d.get("stackable", False)
        self.available = d.get("available", True)
        if not isinstance(self.stackable, bool) or not isinstance(self.available, bool):
            raise ValueError(f"{where}: stackable and available are true or false")
        self.why_not = d.get("why_not") or ""
        eff = d.get("effect") or {}
        if not isinstance(eff, dict):
            raise ValueError(f"{where}.effect: a table is needed")
        bad = [k for k in eff if k not in PERK_EFFECTS]
        if bad:
            raise ValueError(f"{where}.effect: {bad[0]!r} isn't one of {', '.join(PERK_EFFECTS)}")
        if self.available and not eff:
            raise ValueError(f"{where}: an available perk needs an effect")
        for k in ("reward_choices", "life", "hand"):
            if k in eff:
                _whole(eff[k], f"{where}.effect.{k}", 1, 20)
        if "gear_slot" in eff:                                            # Round PROG4
            _whole(eff["gear_slot"], f"{where}.effect.gear_slot", 1, 1)
        for k in ("gold_bonus",):
            if k in eff and not _fraction(eff[k]):
                raise ValueError(f"{where}.effect.{k}: a share from 0 to 1 is needed, not {eff[k]!r}")
        if "loss_cut" in eff and not _fraction(eff["loss_cut"]):        # Round GOLD1: how much smaller a loss is (0.25 = a quarter)
            raise ValueError(f"{where}.effect.loss_cut: a share from 0 to 1 is needed, not {eff['loss_cut']!r}")
        if "battlefield" in eff:
            b = eff["battlefield"]
            if not isinstance(b, list) or not b or not all(isinstance(x, str) and x for x in b):
                raise ValueError(f"{where}.effect.battlefield: a list of card names is needed")
        self.effect = dict(eff)

    def __repr__(self):
        return f"Perk({self.id!r})"


def _fraction(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1


def _whole(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{name}: a whole number from {lo} to {hi} is needed, not {v!r}")
    return v


class Levels:
    def __init__(self, d):
        if not isinstance(d, dict):
            raise ValueError("progression.json: a table is needed")
        xp, lv, pk = d.get("xp"), d.get("levels"), d.get("perks")
        if not isinstance(xp, dict) or not isinstance(lv, dict) or not isinstance(pk, dict):
            raise ValueError("progression.json: xp, levels and perks are needed (Round PROG3)")
        bad = [k for k in xp if k not in XP_KINDS]
        if bad:
            raise ValueError(f"progression.json xp: {bad[0]!r} isn't one of {', '.join(XP_KINDS)}")
        self.xp = {k: _whole(xp.get(k, 0), f"progression.json xp.{k}", 0, 10000) for k in XP_KINDS}
        self.first = _whole(lv.get("first"), "progression.json levels.first", 1, 100000)
        self.step = _whole(lv.get("step"), "progression.json levels.step", 0, 100000)
        self.cap = _whole(lv.get("cap"), "progression.json levels.cap", 1, 99)
        self.base_life = _whole(lv.get("base_life", 20), "progression.json levels.base_life", 1, 999)
        self.life_per_level = _whole(lv.get("life_per_level"), "progression.json levels.life_per_level", 0, 20)
        self.perk_every = _whole(lv.get("perk_every"), "progression.json levels.perk_every", 1, 99)
        self.perk_choices = _whole(lv.get("perk_choices"), "progression.json levels.perk_choices", 1, 6)
        self.perks = {pid: Perk(pid, v) for pid, v in pk.items() if not str(pid).startswith("_")}

    # ---- the curve ----
    def xp_to_reach(self, level):
        """Total XP that level `level` needs (level 1: 0). Level 2 at first; each next level needs step more than the last."""
        level = max(1, min(int(level), self.cap))
        n = level - 1
        return n * self.first + self.step * n * (n - 1) // 2

    def level_of(self, xp):
        xp = max(0, int(xp or 0))
        lv = 1
        while lv < self.cap and xp >= self.xp_to_reach(lv + 1):
            lv += 1
        return lv

    def bar(self, xp):
        """(level, XP into this level, XP this level needs to the next) - (cap, None, None) at the cap."""
        lv = self.level_of(xp)
        if lv >= self.cap:
            return lv, None, None
        lo = self.xp_to_reach(lv)
        return lv, max(0, int(xp or 0)) - lo, self.xp_to_reach(lv + 1) - lo

    def is_perk_level(self, level):
        return level > 1 and level % self.perk_every == 0

    def offerable(self):
        return [p for p in self.perks.values() if p.available]


_READ = {}


def read(path=PROGRESSION_FILE):
    if path not in _READ:
        with open(path, encoding="utf-8") as f:
            _READ[path] = Levels(json.load(f))
    return _READ[path]


# ---- the save's part --------------------------------------------------------------------------------------------------------

def blank():
    return {"xp": 0, "perks": [], "offers": []}


def state(save):
    """The save's progress table (made when missing - a save from before this round)."""
    p = getattr(save, "progress", None)
    if not isinstance(p, dict):
        p = blank()
        save.progress = p
    p.setdefault("xp", 0)
    p.setdefault("perks", [])
    p.setdefault("offers", [])
    return p


def xp_for(result, kind, lv=None):
    """THE XP rule: a won duel gives its kind's XP ("regular", "dungeon", "rare", "boss"); a lost or conceded one "loss"; a draw
    "draw"; "unfinished" nothing. result "quest": a quest handed in."""
    lv = lv or read()
    if result == "quest":
        return lv.xp["quest"]
    if result == "won":
        return lv.xp.get(kind if kind in ("regular", "dungeon", "rare", "boss") else "regular", 0)
    if result in ("lost", "conceded"):
        return lv.xp["loss"]
    if result == "draw":
        return lv.xp["draw"]
    return 0


def level(save, lv=None):
    lv = lv or read()
    return lv.level_of(state(save)["xp"])


def _deal(save, lv, at_level):
    """One perk choice for reaching `at_level`: perk_choices different perks the save may still take (a non-stackable perk only once,
    and never one already waiting in another choice), drawn with the save's rng. [] when none is left."""
    p = state(save)
    taken = set(p["perks"]) | {x for grp in p["offers"] for x in grp}
    pool = [k.id for k in lv.offerable() if k.stackable or k.id not in taken]
    if not pool:
        return []
    rng = save.rng(f"perk:{at_level}")
    pool = sorted(pool)
    return rng.sample(pool, min(lv.perk_choices, len(pool)))


def gain(save, amount, lv=None):
    """XP into the save. Returns {"gained", "xp_before", "xp_after", "level_before", "level_after", "offers" (the perk choices
    dealt now)}; a perk choice is dealt for every perk level crossed."""
    lv = lv or read()
    p = state(save)
    amount = max(0, int(amount or 0))
    before = int(p["xp"])
    lb = lv.level_of(before)
    p["xp"] = before + amount
    la = lv.level_of(p["xp"])
    dealt = []
    for n in range(lb + 1, la + 1):
        if lv.is_perk_level(n):
            grp = _deal(save, lv, n)
            if grp:
                p["offers"].append(grp)
                dealt.append(grp)
    return {"gained": amount, "xp_before": before, "xp_after": p["xp"], "level_before": lb, "level_after": la, "offers": dealt}


def offers(save):
    """The perk choices still waiting (oldest first): [[perk id, ...], ...]."""
    return [list(g) for g in state(save)["offers"]]


def choose(save, perk_id, lv=None):
    """Take `perk_id` from the oldest waiting choice. ValueError when it isn't offered there."""
    lv = lv or read()
    p = state(save)
    if not p["offers"]:
        raise ValueError("No perk to choose.")
    grp = p["offers"][0]
    if perk_id not in grp:
        raise ValueError(f"{perk_id!r} isn't one of the perks offered.")
    if perk_id not in lv.perks:
        raise ValueError(f"No perk {perk_id!r}.")
    p["offers"].pop(0)
    p["perks"].append(perk_id)
    return lv.perks[perk_id]


def perks_of(save, lv=None):
    lv = lv or read()
    return [lv.perks[k] for k in state(save)["perks"] if k in lv.perks]


def _gear(save):
    """Round PROG4: the worn gear's effects (rpg.gear.effects) - {} when gear.json can't be read (the duel as without it, said once)."""
    try:
        from rpg import gear
        return gear.effects(save)
    except (OSError, ValueError) as e:
        from rpg import notes
        notes.once("gear:read", f"gear.json couldn't be read, so no gear counts: {e}")
        return {}


def duel_effects(save, lv=None):
    """The player's seat's start effects in an Adventure duel (start_effects.py's dict): life = base_life + life_per_level for each
    level above 1 + the perks' life + the worn gear's life (Round PROG4); hand = 7 + the perks' and the gear's hand (only when it
    isn't 7); the perks' and then the gear's battlefield cards; the gear's command-zone relics. Each worn piece counts once
    (rpg.gear.effects). At level 1 with no perks and no gear this is {}: the duel exactly as before (no --start-effects; an older
    bridge still plays it). Life never goes below 1 nor hand below 0 (a trade-off piece can't make the bridge refuse the duel)."""
    lv = lv or read()
    level_now = level(save, lv)
    life = lv.base_life + lv.life_per_level * (level_now - 1)
    hand = 7
    bf = []
    for k in perks_of(save, lv):
        life += int(k.effect.get("life", 0))
        hand += int(k.effect.get("hand", 0))
        bf += list(k.effect.get("battlefield", ()))
    ge = _gear(save)
    life += int(ge.get("life", 0))
    hand += int(ge.get("hand", 0))
    bf += list(ge.get("battlefield", ()))
    cz = list(ge.get("command", ()))
    life = max(1, min(999, life))
    hand = max(0, min(20, hand))
    out = {}
    if life != PLAIN_LIFE:                   # the plain duel's 20 life needs nothing from the bridge (an older one still plays it)
        out["life"] = life
    if hand != 7:
        out["hand"] = hand
    if bf:
        out["battlefield"] = bf
    if cz:
        out["command"] = cz
    return out


def reward_choices(save, base, lv=None):
    """Cards offered per reward pick: the base + the perks' (Keen Eye) + the worn gear's (Round PROG4: Seeker's Monocle)."""
    lv = lv or read()
    return base + sum(int(k.effect.get("reward_choices", 0)) for k in perks_of(save, lv)) + \
        int(_gear(save).get("reward_choices", 0))


def win_gold(save, gold, lv=None):
    """A won duel's gold: the perks' bonus (Bounty Hunter: +10%), then times the worn gear's gold_mult (Round PROG4: Merchant's
    Token), rounded once."""
    lv = lv or read()
    bonus = sum(float(k.effect.get("gold_bonus", 0)) for k in perks_of(save, lv))
    mult = float(_gear(save).get("gold_mult", 1.0))
    return int(round(gold * (1 + bonus) * mult)) if (bonus or mult != 1.0) else gold


def loss_share(save, numbers, kind="regular", lv=None):
    """Round GOLD1: the share of the gold carried a loss of this kind of fight takes - numbers.json's share for the kind, made
    smaller by the perks' loss_cut (Thick Skin: a quarter less), never above economy.MAX_LOSS_SHARE."""
    from rpg import economy
    lv = lv or read()
    share = numbers.loss_share(kind)
    for k in perks_of(save, lv):
        if "loss_cut" in k.effect:
            share *= 1 - min(1.0, max(0.0, float(k.effect["loss_cut"])))
    return economy.clamp_loss_share(share)


def loss_cost(save, numbers, kind="regular", lv=None):
    """What a loss of this kind of fight takes, with the perks (rpg.economy.cost_of: rounded down)."""
    from rpg import economy
    return economy.cost_of(save.gold, loss_share(save, numbers, kind, lv))


# ---- words -------------------------------------------------------------------------------------------------------------------

def bar_text(xp, lv=None):
    """"Level 3  XP 12/60" ("Level 20  XP MAX" at the cap)."""
    lv = lv or read()
    n, into, need = lv.bar(xp)
    return f"Level {n}  XP MAX" if into is None else f"Level {n}  XP {into}/{need}"


def level_up_lines(g, lv=None):
    """The lines for a gain (gain()'s dict): [] when no level was reached; else "Level up! Level 4: you start your duels with 23
    life." and, when a perk choice was dealt, "A perk to choose: Menu." """
    lv = lv or read()
    if not g or g["level_after"] <= g["level_before"]:
        return []
    life = lv.base_life + lv.life_per_level * (g["level_after"] - 1)
    out = [f"Level up! Level {g['level_after']}: your duels start at {life} life (before perks)."]
    if g.get("offers"):
        out.append("A perk to choose: Menu." if len(g["offers"]) == 1 else f"{len(g['offers'])} perks to choose: Menu.")
    return out
