# SPDX-License-Identifier: GPL-3.0-or-later
"""The Adventure's progression, part 1: what makes growing stronger visible (Round PROG1; Karl, 2 Nov 2026: "My main idea is to add
ways to make progression more tangible and 'fun' JRPG style"; the design is the Project doc claude/HANDOVER_PROGRESSION_2026-11-02.md,
section 3.1). No pygame: rpg_results.py and rpg_screens.py draw; every rule they show is here, once.

    read(path)                     rpg/data/progression.json, checked (a key it can't use is a ValueError naming it)
    card_quality(cards, pool, tiers) THE Card quality formula (the meter, the soak and prog-tune call this one): the mean tier of
                                   the deck's non-land cards, copies counted; None when it has none with a tier. Round PROG1b
                                   (Karl, 3 Nov 2026): it was "deck power" - RPG-SOAK1 showed the mean tier is not how strong a
                                   deck is; deck_power stays as another name for it (tools/rpg_soak.py and older code import it)
    quality_text(v) / change_text  "Card quality 1.4" / "+0.1" (one decimal: progression.json card_quality.decimals);
                                   power_text is another name for quality_text
    PowerMeter                     the meter's memory: the last value and, for meter.change_seconds after it moves, the change
    note_found(save, name)         the Card Book's log: when and where a card first entered the collection (rpg.save calls it)
    is_new(save, name)             THE "NEW!" rule: never owned before (not in the log, and not owned now)
    book_rows(...)                 the Card Book: every card ever owned, with its tier, copies, deck copies and where it was found
    bestiary_rows(...)             the bestiary: each foe kind met, its record and the cards won from it; never met = "???"
    results(out, ...)              what the results screen after a duel shows (banner, gold before and after, the cards)

Readings of mine (Karl's to change, each in one place):
  - Card quality counts copies (four Elves weigh four times) and leaves out lands and the cards no tier table knows (tier None).
  - "Found" is the first time a card enters the collection (won, bought, from a pack, a quest, the starting deck). A save from
    before this round has no log: its cards show "found before the Card Book", and none of them is NEW.
  - A card sold and won again is not NEW (it is in the log); a card owned before the log began and sold before it is (the save
    never wrote it down).
"""
import json
import math

from rpg import cardpool, clock, data_path

PROGRESSION_FILE = data_path("progression.json")
BEFORE = "Found before the Card Book"
UNMET = "???"
QUALITY_NAME = "Card quality"
QUALITY_EXPLAIN = ("The average tier of your cards: how often tournament players use them. Not how strong your deck is - the "
                   "curve and counts matter as much.")
START_PLACE = "your starting deck"


class Progression:
    def __init__(self, d):
        if not isinstance(d, dict):
            raise ValueError("progression.json: a table is needed")
        r = d.get("results")
        m = d.get("card_quality", d.get("meter"))         # Round PROG1b: the key was "meter" (PROG1); a file with it still reads
        if not isinstance(r, dict):
            raise ValueError("progression.json: results is missing")
        if not isinstance(m, dict):
            raise ValueError("progression.json: card_quality is missing")
        self.banner_seconds = _seconds(r.get("banner_seconds"), "results.banner_seconds")
        self.gold_count_seconds = _seconds(r.get("gold_count_seconds"), "results.gold_count_seconds")
        self.card_flip_seconds = _seconds(r.get("card_flip_seconds"), "results.card_flip_seconds")
        self.card_gap_seconds = _seconds(r.get("card_gap_seconds"), "results.card_gap_seconds")
        self.xp_slot_lines = _whole(r.get("xp_slot_lines"), "results.xp_slot_lines", 0, 6)
        self.change_seconds = _seconds(m.get("change_seconds"), "card_quality.change_seconds")
        self.decimals = _whole(m.get("decimals"), "card_quality.decimals", 0, 3)
        explain = m.get("explain", QUALITY_EXPLAIN)                  # Round PROG1b: what Card quality is, in one line
        if not isinstance(explain, str) or not explain.strip():
            raise ValueError(f"progression.json card_quality.explain: a line of text, not {explain!r}")
        self.explain = explain.strip()
        # Round PROG2: visible rewards (a file from PROG1 without these keys reads with the defaults)
        rw, bo, ch = d.get("rewards", {}), d.get("boss", {}), d.get("chests", {})
        if not isinstance(rw, dict) or not isinstance(bo, dict) or not isinstance(ch, dict):
            raise ValueError("progression.json: rewards, boss and chests must be tables")
        self.choices_per_card = _whole(rw.get("choices_per_card", 3), "rewards.choices_per_card", 1, 5)
        sig = bo.get("signature_card", True)
        if not isinstance(sig, bool):
            raise ValueError(f"progression.json boss.signature_card: true or false, not {sig!r}")
        self.signature_card = sig
        self.refill_hour = _whole(ch.get("refill_hour", 7), "chests.refill_hour", 0, 23)
        lst = ch.get("list", {})
        if not isinstance(lst, dict):
            raise ValueError("progression.json chests.list: a table of chest id -> contents is needed")
        self.chests = {cid: _chest(cid, v) for cid, v in lst.items()}

    def results_seconds(self, cards):
        """How long the whole results screen plays when nobody skips it (the banner, the count, then each card in turn)."""
        flips = 0.0 if not cards else self.card_flip_seconds + (cards - 1) * self.card_gap_seconds
        return self.banner_seconds + self.gold_count_seconds + flips


CHEST_KEYS = ("gold", "card", "pack", "gear", "daily")


def _chest(cid, v):
    """Round PROG2: one chest's contents, checked (a key it can't use is a ValueError naming the chest)."""
    where = f"progression.json chests.list.{cid}"
    if not isinstance(cid, str) or not cid or not isinstance(v, dict):
        raise ValueError(f"{where}: a table of contents is needed")
    bad = [k for k in v if k not in CHEST_KEYS]
    if bad:
        raise ValueError(f"{where}: {bad[0]!r} isn't one of {', '.join(CHEST_KEYS)}")
    out = {"gold": _whole(v.get("gold", 0), where + ".gold", 0, 100000),
           "gear": _whole(v.get("gear", 0), where + ".gear", 0, 100000),
           "card": v.get("card"), "pack": v.get("pack"), "daily": v.get("daily", False)}
    if out["card"] not in (None, "band"):
        raise ValueError(f"{where}.card: only \"band\" (a card from the region's band in your colours), not {out['card']!r}")
    if out["pack"] is not None and (not isinstance(out["pack"], str) or not out["pack"]):
        raise ValueError(f"{where}.pack: a set code, not {out['pack']!r}")
    if not isinstance(out["daily"], bool):
        raise ValueError(f"{where}.daily: true or false, not {out['daily']!r}")
    if not (out["gold"] or out["gear"] or out["card"] or out["pack"]):
        raise ValueError(f"{where}: an empty chest (no gold, card, pack or gear)")
    return out


def _seconds(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 30:
        raise ValueError(f"progression.json {name}: seconds from 0 to 30 are needed, not {v!r}")
    return float(v)


def _whole(v, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"progression.json {name}: a whole number from {lo} to {hi} is needed, not {v!r}")
    return v


_READ = {}


def read(path=PROGRESSION_FILE):
    if path not in _READ:
        with open(path, encoding="utf-8") as f:
            _READ[path] = Progression(json.load(f))
    return _READ[path]


# ---- the Card quality meter (Round PROG1: "deck power") ---------------------------------------------------------------------------

def card_quality(cards, pool, tier_table):
    """The mean tier of the non-land cards of `cards` ([(name, copies)]), copies counted - the meter's one formula. None when no
    non-land card has a tier. A land is any card whose type line says Land (a basic, a dual, an MDFC with a land front)."""
    total = n = 0
    for name, k in cards:
        card = pool.get(name) if pool is not None else None
        if card is None or "Land" in (card.type_line or ""):
            continue
        t = tier_table.tier_of(card.name)
        if t is None:
            continue
        k = int(k)
        total += t * k
        n += k
    return total / n if n else None


def _rounded(v, decimals):
    return None if v is None else round(v + 0.0, decimals)


deck_power = card_quality            # Round PROG1's name: tools/rpg_soak.py and older code import it


def quality_text(v, decimals=1):
    return f"{QUALITY_NAME} -" if v is None else f"{QUALITY_NAME} {v:.{decimals}f}"


power_text = quality_text


def change_text(old, new, decimals=1):
    """"+0.1" / "-0.2" when the shown value moved, else None."""
    a, b = _rounded(old, decimals), _rounded(new, decimals)
    if a is None or b is None or a == b:
        return None
    d = b - a
    return f"{'+' if d > 0 else '-'}{abs(d):.{decimals}f}"


class PowerMeter:
    """The meter's memory: see(value, now) -> (text, change or None). A change shows for change_seconds after the value moves;
    the first value seen shows none (opening a window is not a change)."""

    def __init__(self, change_seconds=4.0, decimals=1):
        self.change_seconds, self.decimals = change_seconds, decimals
        self.value = None
        self.seen = False
        self.change, self.until = None, 0.0

    def see(self, value, now):
        if not self.seen:
            self.seen, self.value = True, value
        elif _rounded(value, self.decimals) != _rounded(self.value, self.decimals):
            self.change = change_text(self.value, value, self.decimals)
            self.until = now + self.change_seconds
            self.value = value
        if self.change is not None and now >= self.until:
            self.change = None
        return quality_text(self.value, self.decimals), self.change


# ---- the Card Book's log ---------------------------------------------------------------------------------------------------------

def note_found(save, name):
    """A card entered the collection: written down the first time (the day by the clock, the map and the town you're in)."""
    key = cardpool.normal_name(name)
    if key in save.found:
        return False
    w = save.world
    save.found[key] = {"name": name, "day": clock.day_number(clock.now(w)), "map": w.get("map") if isinstance(w.get("map"), str)
                       else None, "town": w.get("in_town") if isinstance(w.get("in_town"), str) else None}
    return True


def is_new(save, name):
    """The "NEW!" rule (the results screen and a pack's NEW): this card was never owned before - not in the Card Book's log and
    not owned now (a save from before the log owns cards it never wrote down). A free basic land is never new."""
    if save.collection.is_free(name):
        return False
    card = save.collection.pool.get(name) if save.collection.pool is not None else None
    shown = card.name if card is not None else name
    return cardpool.normal_name(shown) not in save.found and (save.collection.count(shown) or 0) == 0


def place_text(entry, place_name=None):
    """"Day 2, The Warrens" for a log entry (place_name(map id, town id) -> a name; the ids when it can't say)."""
    if not entry:
        return BEFORE
    where = None
    if place_name is not None:
        try:
            where = place_name(entry.get("map"), entry.get("town"))
        except Exception:
            where = None
    where = where or entry.get("town") or entry.get("map")
    if not where and not entry.get("map") and not entry.get("town"):
        where = START_PLACE                    # nowhere on the map yet: the new game's starting deck
    day = f"Day {entry['day']}" if entry.get("day") else None
    return ", ".join(x for x in (day, where) if x) or BEFORE


class BookRow:
    __slots__ = ("name", "card", "tier", "owned", "in_deck", "found", "colours")

    def __init__(self, name, card, tier, owned, in_deck, found, colours):
        self.name, self.card, self.tier, self.owned, self.in_deck = name, card, tier, owned, in_deck
        self.found, self.colours = found, colours

    def __repr__(self):
        return f"BookRow({self.name!r}, tier {self.tier}, owned {self.owned}, deck {self.in_deck}, {self.found!r})"


def card_colours(card):
    """The letters a card counts as for the Card Book's filter: its colour identity ("C" for none)."""
    ident = "".join(c for c in (getattr(card, "identity", None) or getattr(card, "colours", "") or "") if c in "WUBRG")
    return ident or "C"


def book_rows(save, pool, tier_table, deck=None, colours=(), tier=None, place_name=None):
    """Every card the player has ever owned (the log, plus what is owned now), best tier first, then by name. colours: letters of
    "WUBRGC" - a card shows when it has any of them (empty: every card); tier: one tier, or None for all."""
    from rpg.collection import deck_counts
    in_deck = {k: v[1] for k, v in deck_counts(deck or {}).items()}
    names = {}
    for key, e in save.found.items():
        names[key] = (e.get("name") or key, e)
    for n in save.collection.names():
        names.setdefault(cardpool.normal_name(n), (n, None))
    out = []
    want = set(colours or ())
    for key, (name, entry) in names.items():
        card = pool.get(name) if pool is not None else None
        shown = card.name if card is not None else name
        t = tier_table.tier_of(shown) if tier_table is not None else None
        cols = card_colours(card)
        if want and not (set(cols) & want):
            continue
        if tier is not None and t != tier:
            continue
        have = save.collection.count(shown)
        out.append(BookRow(shown, card, t, have, in_deck.get(cardpool.normal_name(shown), 0), place_text(entry, place_name),
                           cols))
    out.sort(key=lambda r: (-(r.tier if r.tier is not None else -1), r.name.lower()))
    return out


# ---- the bestiary ----------------------------------------------------------------------------------------------------------------

def note_beaten(save, kind, cards):
    """A duel won against `kind`: the cards it gave go in its page of the bestiary."""
    if not isinstance(kind, str) or not kind:
        return
    page = save.bestiary.setdefault(kind, {"cards": {}})
    for name in cards:
        page["cards"][name] = page["cards"].get(name, 0) + 1


def duel_record(games, slot):
    """{kind: {"won", "lost", "draw"}} from the table's record of the Adventure's duels (rpg_saves/duel_stats, stats.load) for
    one save slot: the start line's deck is "rpg:<slot>:<deck name>" and the foe's "rpg-enemy:<kind>" (forge_table's
    start_adventure_duel). A conceded duel counts as lost; one that didn't finish counts nowhere."""
    out = {}
    mine = f"rpg:{slot}:"
    for g in games or ():
        start, end = g.get("start") or {}, g.get("end") or {}
        if not str(((start.get("deck") or {}).get("id")) or "").startswith(mine):
            continue
        key = {"won": "won", "lost": "lost", "conceded": "lost", "draw": "draw"}.get(end.get("result"))
        if key is None:
            continue
        for o in start.get("opponents") or ():
            oid = str(((o or {}).get("deck") or {}).get("id") or "")
            if oid.startswith("rpg-enemy:"):
                rec = out.setdefault(oid.split(":", 1)[1], {"won": 0, "lost": 0, "draw": 0})
                rec[key] += 1
    return out


class BeastRow:
    __slots__ = ("kind", "name", "met", "home", "won", "lost", "draw", "cards", "battler", "battler_scale")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def __repr__(self):
        return f"BeastRow({self.kind!r}, met {self.met}, {self.won}-{self.lost}-{self.draw})"


def bestiary_rows(save, kinds, games=(), slot=None, home_of=None):
    """One row per foe kind in enemy_kinds.json's order. Met = beaten (the save's record), or fought (the duel record), or a page
    in the bestiary. Wins are the higher of the save's count and the duel record's (a save from before the record began)."""
    rec = duel_record(games, slot if slot is not None else save.slot)
    defeated = save.record.get("defeated") or {}
    out = []
    for k in kinds:
        kid = k.get("id")
        r = rec.get(kid, {"won": 0, "lost": 0, "draw": 0})
        won = max(r["won"], int(defeated.get(kid) or 0))
        page = save.bestiary.get(kid) or {}
        met = bool(won or r["lost"] or r["draw"] or page)
        home = None
        if met and home_of is not None:
            try:
                home = home_of(kid)
            except Exception:
                home = None
        cards = sorted((page.get("cards") or {}).items(), key=lambda e: (-e[1], e[0].lower()))
        out.append(BeastRow(kind=kid, name=(k.get("name") or kid) if met else UNMET, met=met, home=home, won=won,
                            lost=r["lost"], draw=r["draw"], cards=cards, battler=k.get("battler"),
                            battler_scale=k.get("battler_scale")))
    return out


# ---- the results screen ----------------------------------------------------------------------------------------------------------

BANNERS = {"won": "VICTORY", "lost": "DEFEAT", "conceded": "DEFEAT", "draw": "DRAW"}


def results(out, lines, gold_now, tier_table=None, is_new=None, can_cast=None):
    """What the results screen shows for a settled duel (rpg.duel.finish's dict, as rpg.adventure.finish_loaded fills it):
    {"banner", "result", "lines" (the result's words, rpg.adventure.outcome_lines - every fact the old window said), "gold_before",
    "gold_after", "gold_change", "cards": [{"name", "tier", "new"}]}. gold_now: the save's gold after the duel."""
    r = (out or {}).get("result")
    change = int((out or {}).get("gold") or 0)
    new = set((out or {}).get("new") or ())
    cards = []
    for name, src in (out or {}).get("cards") or ():
        t = tier_table.tier_of(name) if tier_table is not None else None
        cards.append({"name": name, "tier": t, "new": name in new, "signature": src == "signature"})
    offers = []                                   # Round PROG2: the reward cards to choose, in groups (rpg.rewards)
    for group in (out or {}).get("offers") or ():
        row = []
        for name in group:
            t = tier_table.tier_of(name) if tier_table is not None else None
            row.append({"name": name, "tier": t, "new": bool(is_new(name)) if is_new else False,
                        "castable": bool(can_cast(name)) if can_cast else True})
        offers.append(row)
    if (out or {}).get("again"):                  # the choice left open when the game closed: no gold counts again
        change = 0
    return {"banner": BANNERS.get(r, ""), "result": r, "lines": list(lines), "gold_before": gold_now - change,
            "gold_after": gold_now, "gold_change": change, "cards": cards, "offers": offers,
            "picks": [g[0]["name"] for g in offers], "xp": xp_info((out or {}).get("xp"))}


def xp_info(g):
    """Round PROG3: the results screen's XP slot for a gain (rpg.levels.gain's dict): {"gained", "before": (level, into, need),
    "after": (level, into, need), "lines": the level-up lines}; None when the duel gave no XP record (an older outcome, a choice
    reopened)."""
    if not isinstance(g, dict):
        return None
    from rpg import levels
    try:
        lv = levels.read()
    except (OSError, ValueError):
        return None
    return {"gained": g.get("gained", 0), "xp_before": g.get("xp_before", 0), "xp_after": g.get("xp_after", 0),
            "before": lv.bar(g.get("xp_before", 0)), "after": lv.bar(g.get("xp_after", 0)),
            "level_before": g.get("level_before", 1), "level_after": g.get("level_after", 1),
            "lines": levels.level_up_lines(g, lv)}
