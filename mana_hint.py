# SPDX-License-Identifier: GPL-3.0-or-later
"""
mana_hint.py - "why can't I pay?"  (no pygame in here)

While Forge waits for a mana payment ("Pay Mana Cost: {G}") the table can look at what the player still has untapped and say, in a line,
why the cost cannot be met: "Nothing you have untapped makes green." Two bug reports (Kinnan cast with one land tapped; a Chrome Mox that could
only make blue) were people wondering exactly that.

It reads the mana abilities off each permanent's rules text ("{T}: Add {G}.", "Add one mana of any color", Chrome Mox's imprinted colours ...),
so it is a HEURISTIC and built never to cry wolf: when it meets something it cannot read (an ability that depends on other permanents, a
mana doubler such as Kinnan, a cost with Phyrexian or X symbols) it treats that as possibly making anything, or says nothing at all. It
only speaks when the cost cannot be met even under the most generous reading of the cards it does not understand.
"""
import re

COLOURS = "WUBRG"
NAMES = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green", "C": "colourless"}
ANY_COLOUR = frozenset(COLOURS)
ANY_TYPE = frozenset(COLOURS + "C")

_ADD_LINE = re.compile(r"^(?P<cost>[^:]+?):\s*Add\s+(?P<what>.+?)\.?\s*$", re.I)
_SYMBOL = re.compile(r"\{([WUBRGC])\}")
_DOUBLER = re.compile(r"whenever you tap [^.]*for mana|would produce|additional (?:one )?mana|adds? an additional", re.I)


def cost_units(cost):
    """'{2}{G}{U/B}' -> (generic, [set of colours each coloured symbol accepts]) or None when the cost has a symbol this cannot judge
    (Phyrexian, X, snow, '2/W' ...). Nothing is said for those."""
    generic, coloured = 0, []
    for sym in re.findall(r"\{([^}]*)\}", cost or ""):
        if sym.isdigit():
            generic += int(sym)
        elif sym in tuple(COLOURS) + ("C",):
            coloured.append({sym})
        elif re.fullmatch(r"[WUBRG]/[WUBRG]", sym):
            coloured.append(set(sym.split("/")))
        else:
            return None
    return generic, coloured


class Source:
    """One permanent (or the floating mana) and the mana it can make: `units` is a list of sets (each set = the colours one mana may be)."""
    def __init__(self, name, units, unknown=False, note=""):
        self.name, self.units, self.unknown, self.note = name, units, unknown, note


def _imprint_colours(card):
    seen = {c for i in (card.get("imprinted") or []) for c in (i.get("colors") or [])}
    return frozenset(c for c in COLOURS if c in seen)


def mana_abilities(card):
    """The mana a permanent can make by tapping / sacrificing itself, read from its text.
    Returns (units, unknown, note): units = [set, ...] for one activation; unknown = an ability we could not read; note = a short remark."""
    text = (card.get("text") or "").replace("\r", "")
    units, unknown, note = None, False, ""
    for line in text.split("\n"):
        line = line.strip().strip("()").strip()
        m = _ADD_LINE.match(line)
        if not m:
            if re.search(r"\bAdd\b[^.]*\{[WUBRGC]\}|\bAdd (?:one|an|two|three) ", line) and ":" in line:
                unknown = True
            continue
        cost, what = m.group("cost"), m.group("what")
        if re.search(r"\{[0-9WUBRGCX]", cost.replace("{T}", "")) or "Pay" in cost or "Discard" in cost:      # costs mana / cards: a filter, not a source
            unknown = True
            continue
        low = what.lower()
        got = None
        if "exiled card's colo" in low:
            cols = _imprint_colours(card)
            got = [set(cols)] if cols else []
            if not cols:
                note = "no mana: nothing imprinted"
            else:
                note = "only " + "/".join(NAMES[c] for c in COLOURS if c in cols) + ", from its imprint"
        elif "any type" in low:
            got = [set(ANY_TYPE)]
        elif re.search(r"any (?:one )?color", low):
            got = [set(ANY_COLOUR)] if "opponent" not in low and "could produce" not in low else None
        else:
            syms = _SYMBOL.findall(what)
            if syms and " or " in what and len(syms) == 2 and not re.search(r"\}\{", what):
                got = [set(syms)]                                     # "{G} or {U}": one mana, either colour
            elif syms:
                got = [{s} for s in syms]                             # "{C}{C}" / "{R}{G}": several mana at once
        if got is None:
            unknown = True
            continue
        if units is None or len(got) > len(units):
            units = got
    return units, unknown, note


def sources(me_cards, pool):
    """(usable, blocked, unknown_names, doubler): the sources that can make mana right now, the ones that could but cannot yet
    (summoning-sick creatures), names of permanents with a mana ability we could not read, and whether a mana doubler is in play."""
    usable, blocked, unknown_names, doubler = [], [], [], False
    for colour, n in (pool or {}).items():
        if n and colour in tuple(COLOURS) + ("C",):
            usable.append(Source("floating mana", [{colour}] * n))
    for c in me_cards:
        text = c.get("text") or ""
        if _DOUBLER.search(text):
            doubler = True
        if c.get("tapped"):
            continue
        units, unknown, note = mana_abilities(c)
        if unknown and not units:
            unknown_names.append(c.get("name", "?"))
            continue
        if unknown:
            unknown_names.append(c.get("name", "?"))
        if units is None:
            continue
        src = Source(c.get("name", "?"), units, note=note)
        taps = "{T}" in text
        if c.get("isCreature") and c.get("sick") and taps and "Haste" not in (c.get("keywords") or []):
            blocked.append(src)
        else:
            usable.append(src)
    return usable, blocked, unknown_names, doubler


def _match(wants, units):
    """Maximum matching of coloured symbols (each a set of accepted colours) to mana units (each a set of possible colours).
    Returns the list of symbols that could not be matched."""
    owner = {}

    def try_one(i, seen):
        for j, u in enumerate(units):
            if j in seen or not (wants[i] & u):
                continue
            seen.add(j)
            if j not in owner or try_one(owner[j], seen):
                owner[j] = i
                return True
        return False
    return [wants[i] for i in range(len(wants)) if not try_one(i, set())], len(units) - len(owner)


def can_pay(cost, unit_sets):
    """True when `unit_sets` (list of colour-sets, one per mana) can pay `cost`; None when the cost cannot be judged."""
    parsed = cost_units(cost)
    if parsed is None:
        return None
    generic, wants = parsed
    lacking, spare = _match(wants, unit_sets)
    return not lacking and spare >= generic


def explain(cost, me_cards, pool=None):
    """One or two short sentences saying why `cost` cannot be paid from what is untapped, or '' when it can (or when nothing sure can be said)."""
    parsed = cost_units(cost)
    if parsed is None:
        return ""
    usable, blocked, unknown_names, doubler = sources(me_cards, pool)
    if doubler:
        return ""                                                    # Kinnan and friends make extra mana: never claim "you can't"
    generic, wants = parsed
    if not wants and generic == 0:
        return ""
    units = [u for s in usable for u in s.units]
    wildcard = [set(ANY_TYPE) for _ in unknown_names]                # anything we could not read may make anything
    if can_pay(cost, units + wildcard):
        return ""
    lacking, spare = _match(wants, units)
    parts = []
    if lacking:
        want = sorted({c for w in lacking for c in w}, key=lambda c: "WUBRGC".index(c))
        parts.append(f"Can't pay {cost}: nothing you have untapped makes {' or '.join(NAMES[c] for c in want)} mana.")
    else:
        parts.append(f"Can't pay {cost}: you are {max(0, generic - spare)} mana short.")
    if blocked and can_pay(cost, units + [u for s in blocked for u in s.units]):
        parts.append(", ".join(s.name for s in blocked) + (" is" if len(blocked) == 1 else " are") + " summoning sick and can't tap for mana yet.")
    real = [s for s in usable if s.name != "floating mana"]

    def one(s):
        cols = sorted({c for u in s.units for c in u}, key=lambda c: "WUBRGC".index(c))
        label = s.note or ("any colour" if set(cols) >= ANY_COLOUR else "/".join(NAMES[c] for c in cols) if cols else "no mana")
        return f"{s.name} ({label})"
    if real:
        parts.append("Untapped: " + ", ".join(one(s) for s in real[:4]) + (", ..." if len(real) > 4 else "") + ".")
    return " ".join(parts)
