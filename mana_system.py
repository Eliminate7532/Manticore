# SPDX-License-Identifier: GPL-3.0-or-later

"""
mana_system.py - Mana cost parsing, mana pool tracking, and cast legality checking
"""
import re
from collections import defaultdict

MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")

COLOR_SYMBOLS = {"W", "U", "B", "R", "G", "C"}
COLOR_ORDER = ("W", "U", "B", "R", "G", "C")
ALL_COLORS = ("W", "U", "B", "R", "G")


def parse_mana_cost(mana_cost_str):
    """
    Parse a Scryfall-style mana cost string like '{2}{U}{U}' into a dict:
    {"generic": 2, "U": 2}. Hybrid/phyrexian symbols are simplified to their
    first listed color for now (good enough for goldfishing, not tournament-legal edge cases).
    """
    if not mana_cost_str:
        return {"generic": 0}

    cost = defaultdict(int)
    symbols = MANA_SYMBOL_RE.findall(mana_cost_str)

    for sym in symbols:
        if sym.isdigit():
            cost["generic"] += int(sym)
        elif sym == "X":
            cost["X"] = cost.get("X", 0) + 1
        elif sym in COLOR_SYMBOLS:
            cost[sym] += 1
        elif "/" in sym:
            # hybrid or phyrexian mana, e.g. "U/B" or "U/P" - take the first real color symbol
            parts = sym.split("/")
            for p in parts:
                if p in COLOR_SYMBOLS:
                    cost[p] += 1
                    break
        else:
            # unrecognized symbol (snow, etc) - treat as generic
            cost["generic"] += 1

    return dict(cost)


def total_cmc(cost_dict):
    return sum(v for k, v in cost_dict.items() if k != "X")


class ManaPool:
    def __init__(self):
        self.pool = defaultdict(int)

    def add(self, color, amount=1):
        self.pool[color] += amount

    def clear(self):
        self.pool = defaultdict(int)

    def total(self):
        return sum(self.pool.values())

    def can_pay(self, cost_dict):
        """Check if the current pool can pay a parsed mana cost, without mutating the pool."""
        pool_copy = dict(self.pool)
        # pay colored costs first
        for color, amount in cost_dict.items():
            if color in ("generic", "X"):
                continue
            if pool_copy.get(color, 0) < amount:
                return False
            pool_copy[color] -= amount
        # pay generic from whatever's left
        generic_needed = cost_dict.get("generic", 0)
        remaining = sum(pool_copy.values())
        return remaining >= generic_needed

    def pay(self, cost_dict):
        """Actually deduct mana for a cost. Returns True if successful, False (no change) if not enough."""
        if not self.can_pay(cost_dict):
            return False
        for color, amount in cost_dict.items():
            if color in ("generic", "X"):
                continue
            self.pool[color] -= amount
        # Spend generic mana from colorless first, then from whichever colour
        # we hold the most of, so useful coloured mana is kept when possible.
        generic_needed = cost_dict.get("generic", 0)
        order = sorted((c for c in self.pool if self.pool[c] > 0),
                       key=lambda c: (c != "C", -self.pool[c]))
        for color in order:
            take = min(self.pool[color], generic_needed)
            self.pool[color] -= take
            generic_needed -= take
            if generic_needed == 0:
                break
        return True

    def text(self):
        """Human-readable pool, e.g. '{U}{U}{C}'. Empty string if the pool is empty."""
        return "".join(f"{{{c}}}" * self.pool[c] for c in COLOR_ORDER if self.pool.get(c, 0) > 0)


def get_land_mana_color(card_data):
    """
    Best-effort guess at what color(s) of mana a land produces, based on Scryfall
    oracle text. Basic lands are exact; nonbasics are a simple keyword scan.
    """
    type_line = card_data.get("type_line", "")
    name = card_data.get("name", "")
    oracle_text = card_data.get("oracle_text", "") or ""

    basic_map = {
        "Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G",
    }
    for basic_name, color in basic_map.items():
        if basic_name in name:
            return [color]

    produced = []
    for color, symbol in [("W", "W"), ("U", "U"), ("B", "B"), ("R", "R"), ("G", "G")]:
        if f"Add {{{symbol}}}" in oracle_text or f"add {{{symbol}}}" in oracle_text.lower():
            produced.append(color)

    if not produced and "Land" in type_line:
        produced = ["C"]  # colorless fallback for lands we can't parse (utility lands etc)

    return produced


# ---------------------------------------------------------------------------
# Mana abilities: work out what a permanent can produce from its Scryfall text
# ---------------------------------------------------------------------------
from dataclasses import dataclass, replace as _dc_replace


def card_face(info, face=0):
    """Dict with name/type_line/mana_cost/oracle_text for one face of a card.

    Double-faced and split cards keep their text inside info["card_faces"];
    single-faced cards keep it at the top level.
    """
    if not info:
        return {}
    faces = info.get("card_faces")
    if faces:
        merged = {k: info.get(k) for k in ("name", "type_line", "mana_cost", "oracle_text")}
        merged.update({k: v for k, v in faces[min(face, len(faces) - 1)].items() if v is not None})
        return merged
    return info


@dataclass(frozen=True)
class ManaOption:
    """One way to tap a permanent for mana."""
    produces: dict            # e.g. {"G": 1} or {"C": 2}
    life_cost: int = 0        # "Pay N life" as part of the cost
    damage: int = 0           # "deals N damage to you"
    sacrifice: bool = False   # "Sacrifice this <permanent>"
    condition: str = ""       # "metalcraft" when the ability needs 3+ artifacts
    note: str = ""            # restrictions the sim does not enforce
    bonus: str = ""           # extra colour added by a Kinnan-style effect (set at tap time)
    restrict: str = ""        # "Spend this mana only to cast ..." (the auto-payer never uses these)
    tap_other: tuple = ()     # (count, valid expression): "tap an untapped creature you control" as a cost
    tap_target: int = -1      # battlefield index chosen for tap_other (-1 = pick automatically)
    tap_target_name: str = ""
    exile_self: bool = False  # "Exile this card from your hand: Add {G}"

    def label(self):
        prod = dict(self.produces)
        text = "".join(f"{{{c}}}" * prod[c] for c in COLOR_ORDER if c in prod)
        extras = []
        if self.bonus:
            extras.append(f"+{{{self.bonus}}} bonus")
        if self.life_cost:
            extras.append(f"pay {self.life_cost} life")
        if self.damage:
            extras.append(f"{self.damage} damage to you")
        if self.sacrifice:
            extras.append("sacrifice")
        if self.tap_other:
            extras.append(f"tap {self.tap_target_name}" if self.tap_target_name else "tap another permanent")
        if self.exile_self:
            extras.append("exile it from your hand")
        if self.restrict:
            extras.append("restricted")
        return text + (f"  ({', '.join(extras)})" if extras else "")


_TAP_ABILITY_RE = re.compile(r"^(?:[A-Za-z]+ — )?(?P<cost>\{T\}(?:, [^:]+)?):\s*(?P<effect>Add\b.*)$")
_ANY_ADD_RE = re.compile(r"^[^:]*:\s*Add\b")
_OPTIONS_RE = re.compile(r"Add (\{[WUBRGC]\})(?:, (\{[WUBRGC]\}))*,? or (\{[WUBRGC]\})")
_mana_option_cache = {}


def _parse_add(add, identity):
    """Turn the 'Add ...' clause into a list of alternative productions, or None if unsupported."""
    add = add.strip().rstrip(".")
    if add == "Add one mana of any color":
        return [{c: 1} for c in ALL_COLORS]
    if add == "Add one mana of any color in your commander's color identity":
        return [{c: 1} for c in identity]
    if re.fullmatch(r"Add (?:\{[WUBRGC]\})+", add):
        counts = {}
        for sym in re.findall(r"\{([WUBRGC])\}", add):
            counts[sym] = counts.get(sym, 0) + 1
        return [counts]
    if _OPTIONS_RE.fullmatch(add):
        return [{sym: 1} for sym in re.findall(r"\{([WUBRGC])\}", add)]
    return None


def mana_options(info, face=0, identity=()):
    """Work out how a permanent can be tapped for mana.

    Returns (options, manual_lines):
      options       list of ManaOption the sim can automate
      manual_lines  mana abilities found in the text that the sim does NOT
                    automate (they need the manual mana keys)
    Supported: '{T}: Add ...' with optional 'Pay N life' and 'Sacrifice this X'
    costs, fixed / either-or / any-colour / commander-identity mana, damage to
    you, and the Metalcraft condition. Restrictions on how the mana may be
    spent are recorded in .note but not enforced.
    """
    key = (info.get("name") if info else None, face, tuple(identity))
    if key in _mana_option_cache:
        return _mana_option_cache[key]

    text = card_face(info, face).get("oracle_text") or ""
    lines = [l.strip() for l in text.split("\n")]
    lines = [l[1:-1] if l.startswith("(") and l.endswith(")") else l for l in lines]
    trigger_damage = sum(int(n) for n in re.findall(r"becomes tapped, it deals (\d+) damage to you", text))

    options, manual = [], []
    for line in lines:
        if not _ANY_ADD_RE.match(line) or 'have "' in line or 'has "' in line:
            continue
        m = _TAP_ABILITY_RE.match(line)
        if not m:
            manual.append(line)
            continue

        life, sac, supported = 0, False, True
        for part in (p.strip() for p in m.group("cost").split(",")):
            if part == "{T}":
                continue
            life_m = re.fullmatch(r"Pay (\d+) life", part)
            if life_m:
                life += int(life_m.group(1))
            elif re.fullmatch(r"Sacrifice this \w+", part):
                sac = True
            else:
                supported = False
        if not supported:
            manual.append(line)
            continue

        sentences = re.split(r"(?<=\.)\s+", m.group("effect").strip())
        productions = _parse_add(sentences[0], identity)
        if productions is None:
            manual.append(line)
            continue

        rest = " ".join(sentences[1:])
        damage = trigger_damage + sum(int(n) for n in re.findall(r"deals (\d+) damage to you", rest))
        condition = "metalcraft" if "Activate only if you control three or more artifacts" in rest else ""
        note = re.sub(r"[^.]*deals \d+ damage to you\.?", "", rest).strip()
        if condition:
            note = ""
        for prod in productions:
            options.append(ManaOption(prod, life_cost=life, damage=damage, sacrifice=sac,
                                      condition=condition, note=note))

    result = (tuple(options), tuple(manual))
    _mana_option_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# Auto-tap planner: which sources should be tapped to pay a cost?
# ---------------------------------------------------------------------------
def _apply(remaining, produces):
    """Use mana `produces` against the still-unpaid cost vector. Returns (new_remaining, wasted)."""
    rem = list(remaining)
    leftover = 0
    for i, color in enumerate(COLOR_ORDER):
        n = produces.get(color, 0)
        used = min(rem[i], n)
        rem[i] -= used
        leftover += n - used
    used = min(rem[6], leftover)
    rem[6] -= used
    return tuple(rem), leftover - used


def plan_payment(cost, pool, sources):
    """Choose which sources to tap so that pool + tapped mana covers `cost`.

    cost     parsed cost dict, e.g. {"generic": 2, "U": 1}
    pool     dict of mana already floating, e.g. {"U": 1}
    sources  list of (source_id, is_land, [ManaOption, ...]); each source can
             be used at most once, with exactly one of its options.
    Returns (True, [(source_id, option_index), ...]) or (False, None).
    The plan prefers: nothing tapped, then lands over other permanents, then
    fewer taps, and avoids paying life, taking damage or sacrificing.
    """
    need = tuple(cost.get(c, 0) for c in COLOR_ORDER) + (cost.get("generic", 0),)
    need, _ = _apply(need, {c: n for c, n in pool.items() if n > 0})
    if not any(need):
        return True, []

    LAND, OTHER = 10, 13
    n = len(sources)
    memo = {}

    def best(i, rem):
        if not any(rem):
            return 0, ()
        if i == n:
            return None
        key = (i, rem)
        if key in memo:
            return memo[key]
        result = best(i + 1, rem)                       # leave this source alone
        source_id, is_land, options = sources[i]
        for oi, opt in enumerate(options):
            new_rem, wasted = _apply(rem, opt.produces)
            if new_rem == rem:
                continue
            sub = best(i + 1, new_rem)
            if sub is None:
                continue
            cost_here = ((LAND if is_land else OTHER) + wasted
                         + opt.life_cost * 50 + opt.damage * 30 + (500 if opt.sacrifice else 0))
            total = sub[0] + cost_here
            if result is None or total < result[0]:
                result = (total, ((source_id, oi),) + sub[1])
        memo[key] = result
        return result

    result = best(0, need)
    if result is None:
        return False, None
    return True, list(result[1])
